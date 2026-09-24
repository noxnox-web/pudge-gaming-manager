"""Scan orchestration for the GUI.

The scan takes ~3 seconds, which is far too long to run on the UI thread — a
frozen window on a club PC looks like a crashed program. It runs on a
``QThread`` worker and reports back by signal.

This module is the boundary: it calls ``core`` and knows nothing about how
the data is obtained.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from PySide6.QtCore import QObject, QThread, Signal

from ...core.diagnostics.issues import detect_issues
from ...core.scanner.hardware_scan import HardwareScanner
from ...core.scanner.network_scan import NetworkScanner
from ...core.scoring.score import compute_score
from ...core.types import GamingScore, HardwareSnapshot, Issue, NetworkSnapshot
from .errors import readable_error


@dataclass(frozen=True, slots=True)
class ScanResult:
    """Everything one dashboard refresh needs."""

    snapshot: HardwareSnapshot
    score: GamingScore
    issues: tuple[Issue, ...]
    network: NetworkSnapshot | None = None

    @property
    def actionable_issues(self) -> tuple[Issue, ...]:
        return tuple(i for i in self.issues if i.status.is_actionable)


class ScanWorker(QObject):
    """Runs a hardware scan off the UI thread."""

    finished = Signal(object)
    """Emits a :class:`ScanResult`."""

    failed = Signal(str)
    """Emits a user-readable message. Never a raw traceback (rule #36)."""

    progress = Signal(str)

    def __init__(
        self,
        scanner: HardwareScanner | None = None,
        network_scanner: NetworkScanner | None = None,
    ) -> None:
        super().__init__()
        self._scanner = scanner or HardwareScanner()
        self._network_scanner = network_scanner or NetworkScanner()

    def run(self) -> None:
        try:
            self.progress.emit("Чтение железа и сети…")
            # Independent and both ~3 s, so they run side by side. The
            # network scan never raises; its failures come back as data.
            with ThreadPoolExecutor(max_workers=1) as pool:
                network_job = pool.submit(self._network_scanner.scan)
                snapshot = self._scanner.scan()
                network = network_job.result()

            self.progress.emit("Analysing…")
            issues = tuple(detect_issues(snapshot, network))
            score = compute_score(snapshot, network=network)

            self.finished.emit(ScanResult(snapshot, score, issues, network))
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(readable_error(exc))


class ScanController(QObject):
    """Owns the worker thread and its lifetime."""

    started = Signal()
    progress = Signal(str)
    completed = Signal(object)
    failed = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._thread: QThread | None = None
        self._worker: ScanWorker | None = None

    @property
    def busy(self) -> bool:
        return self._thread is not None and self._thread.isRunning()

    def start(self) -> None:
        """Begin a scan. Ignored when one is already running."""
        if self.busy:
            return

        thread = QThread()
        worker = ScanWorker()
        worker.moveToThread(thread)

        thread.started.connect(worker.run)
        worker.progress.connect(self.progress)
        worker.finished.connect(self._on_finished)
        worker.failed.connect(self._on_failed)

        # Keep references: a garbage-collected QThread aborts the scan.
        self._thread, self._worker = thread, worker

        self.started.emit()
        thread.start()

    def _on_finished(self, result: ScanResult) -> None:
        self._teardown()
        self.completed.emit(result)

    def _on_failed(self, message: str) -> None:
        self._teardown()
        self.failed.emit(message)

    def _teardown(self) -> None:
        if self._thread is not None:
            self._thread.quit()
            self._thread.wait(5000)
        self._thread = None
        self._worker = None

    def shutdown(self) -> None:
        """Stop cleanly on window close, so Qt does not warn about a
        running thread being destroyed."""
        self._teardown()
