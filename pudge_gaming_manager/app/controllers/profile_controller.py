"""Golden Profile save and compare for the GUI.

Both operations call ``powercfg`` (up to a 20-second timeout) and touch the
file system, which may be a slow USB stick or network share, so they run off
the UI thread like the scan does.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from ...core.profiles import golden, storage
from ...core.types import HardwareSnapshot
from .background import BackgroundRunner


class ProfileController(QObject):
    """Captures this PC to a profile file, or compares it against one."""

    saved = Signal(object)
    """Emits the ``pathlib.Path`` written."""

    compared = Signal(object)
    """Emits a :class:`golden.ComparisonResult`."""

    failed = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._runner = BackgroundRunner(self)
        self._runner.failed.connect(self.failed)

    @property
    def busy(self) -> bool:
        return self._runner.busy

    def start_save(self, snapshot: HardwareSnapshot, path: str, name: str) -> None:
        def work() -> object:
            profile = golden.capture_from_machine(snapshot, name=name)
            return storage.save(profile, path)

        self._runner.start(work, self.saved.emit)

    def start_compare(self, snapshot: HardwareSnapshot, path: str) -> None:
        # Loading validates the untrusted file before any value is used.
        self._runner.start(
            lambda: golden.compare_with_machine(storage.load(path), snapshot),
            self.compared.emit,
        )

    def shutdown(self) -> None:
        self._runner.shutdown()
