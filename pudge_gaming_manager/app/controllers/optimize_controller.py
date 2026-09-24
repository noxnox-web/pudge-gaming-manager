"""Optimization orchestration for the GUI.

Both phases run off the UI thread. The preview alone takes ~15 seconds on a
machine with a large Temp folder, because it counts every candidate file
before promising to remove anything — a preview that guessed would not be a
preview.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from ...core.optimization.pipeline import (
    OptimizationOutcome,
    OptimizationPipeline,
    OptimizationPreview,
)
from ...core.scanner.hardware_scan import HardwareScanner
from ...core.services import create_optimization_pipeline
from ...core.types import HardwareSnapshot, Issue
from .background import BackgroundRunner


class OptimizeController(QObject):
    """Runs preview and apply on worker threads."""

    preview_started = Signal()
    preview_ready = Signal(object)
    apply_started = Signal()
    apply_finished = Signal(object)
    failed = Signal(str)

    def __init__(
        self,
        pipeline: OptimizationPipeline | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        # The GUI asks core for a working pipeline rather than opening a
        # database itself; storage lifetime is not a view concern.
        self._pipeline = pipeline or create_optimization_pipeline()
        self._runner = BackgroundRunner(self)
        self._runner.failed.connect(self._on_failed)
        self._applying = False

    @property
    def busy(self) -> bool:
        return self._runner.busy

    @property
    def applying(self) -> bool:
        """True only while changes are being written to the system.

        Preview and the idle state are both False. The window uses this to
        refuse to close mid-apply, where interrupting between a change and
        its verification could leave a setting half-applied.
        """
        return self._applying

    def start_preview(
        self, snapshot: HardwareSnapshot, issues: tuple[Issue, ...]
    ) -> None:
        if self._runner.start(
            lambda: self._pipeline.preview(snapshot, issues), self.preview_ready.emit
        ):
            self.preview_started.emit()

    def start_apply(self, preview: OptimizationPreview) -> None:
        # The after-state is measured by a real rescan, so the report never
        # attributes an unmeasured change to the optimization.
        if self._runner.start(
            lambda: self._pipeline.apply(preview, rescan=HardwareScanner().scan),
            self._on_applied,
        ):
            self._applying = True
            self.apply_started.emit()

    def _on_applied(self, outcome: object) -> None:
        self._applying = False
        self.apply_finished.emit(outcome)

    def _on_failed(self, message: str) -> None:
        # Clears the apply flag whether the failure came from preview or
        # apply, so a failed run never leaves the window unclosable.
        self._applying = False
        self.failed.emit(message)

    def shutdown(self) -> None:
        self._runner.shutdown()


__all__ = ["OptimizeController", "OptimizationPreview", "OptimizationOutcome"]
