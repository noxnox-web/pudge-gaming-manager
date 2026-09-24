"""Scan orchestration for the GUI.

A full scan takes ~4 seconds, far too long for the UI thread — a frozen
window on a club PC looks like a crashed program. It runs on a ``QThread``
worker and reports back by signal.

Full scan versus refresh
------------------------
Most of those 4 seconds are two PowerShell calls: the hardware inventory
(~2.3 s) and the routing query (~2.9 s, run beside it). Both answer
questions that applying a change cannot have altered. Deleting temporary
files does not change which CPU is installed, and it certainly does not
change the ping to the router.

So there are two modes. :meth:`ScanController.start` is the full scan: the
window opens with one, and the rescan button asks for one. After an action
the dashboard calls :meth:`ScanController.refresh`, which re-measures
everything live — processor load, GPU temperature, free space, the current
display mode — while reusing the hardware inventory and carrying the
previous network reading forward untouched. That is ~70 ms instead of
~4 000 ms, and no value is presented as freshly measured unless it was.

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
        *,
        quick: bool = False,
        previous_network: NetworkSnapshot | None = None,
    ) -> None:
        super().__init__()
        self._scanner = scanner or HardwareScanner()
        self._network_scanner = network_scanner or NetworkScanner()
        self._quick = quick
        self._previous_network = previous_network

    def run(self) -> None:
        try:
            if self._quick:
                self._run_refresh()
            else:
                self._run_full()
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(readable_error(exc))

    def _run_full(self) -> None:
        self.progress.emit("Чтение железа и сети…")
        # Independent and both ~3 s, so they run side by side. The network
        # scan never raises; its failures come back as data.
        with ThreadPoolExecutor(max_workers=1) as pool:
            network_job = pool.submit(self._network_scanner.scan)
            snapshot = self._scanner.scan()
            network = network_job.result()
        self._publish(snapshot, network)

    def _run_refresh(self) -> None:
        """Re-measure what an action can change, and nothing else.

        The network is not re-scanned and not re-labelled: the snapshot
        carried forward is the one the last full scan took, exactly as the
        dashboard has been displaying it since. Claiming a fresh ping here
        would be inventing a measurement nobody made.
        """
        self.progress.emit("Обновление показаний…")
        snapshot = self._scanner.scan(reuse_inventory=True)
        self._publish(snapshot, self._previous_network)

    def _publish(
        self, snapshot: HardwareSnapshot, network: NetworkSnapshot | None
    ) -> None:
        self.progress.emit("Анализ…")
        issues = tuple(detect_issues(snapshot, network))
        score = compute_score(snapshot, network=network)
        self.finished.emit(ScanResult(snapshot, score, issues, network))


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
        # One scanner for the controller's lifetime, not one per worker:
        # it is what holds the cached hardware inventory, and a scanner
        # built per scan would have nothing to reuse.
        self._scanner = HardwareScanner()
        self._last_network: NetworkSnapshot | None = None

    @property
    def busy(self) -> bool:
        return self._thread is not None and self._thread.isRunning()

    @property
    def scanner(self) -> HardwareScanner:
        """The scanner holding the cached inventory.

        Shared with the optimize controller so its after-state measurement
        reuses what this one already read, rather than paying for the
        PowerShell inventory call a second time in the same operation.
        """
        return self._scanner

    def start(self) -> None:
        """Begin a full scan, hardware and network.

        What the window opens with and what the rescan button asks for.
        Ignored when a scan is already running.
        """
        self._begin(quick=False)

    def refresh(self) -> None:
        """Re-measure after an action, without the two PowerShell calls.

        Falls back to a full scan when nothing has been scanned yet: there
        would be no inventory to reuse and no network reading to carry.
        """
        self._begin(quick=self._last_network is not None)

    def _begin(self, *, quick: bool) -> None:
        if self.busy:
            return

        thread = QThread()
        worker = ScanWorker(
            scanner=self._scanner,
            quick=quick,
            previous_network=self._last_network,
        )
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
        if result.network is not None:
            self._last_network = result.network
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
