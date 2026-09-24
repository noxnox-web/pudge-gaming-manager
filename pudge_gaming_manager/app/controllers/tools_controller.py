"""The two read-mostly tools: the usage survey and the startup manager.

Both are small enough that a controller each would be two files of
boilerplate around one worker call. They share one because they share a
shape — survey or enumerate on a worker thread, then hand the result to a
dialog — not because they are related.

The usage survey walks whole directory trees and the startup enumeration
opens registry keys that can block; neither belongs on the UI thread.
Toggling a startup entry writes to the registry, so it is a worker call
too and its result is the re-read state, never an assumption.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from ...core.cleanup import survey
from ...core.startup import StartupEntry, StartupManager
from .background import BackgroundRunner


class ToolsController(QObject):
    """Runs the disk-usage survey and the startup-entry list off the UI thread."""

    usage_ready = Signal(object)
    """Emits a :class:`UsageReport`."""

    startup_ready = Signal(object)
    """Emits a ``list[StartupEntry]``."""

    startup_toggled = Signal(object)
    """Emits the re-read :class:`StartupEntry` after a toggle."""

    failed = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._startup = StartupManager()
        self._runner = BackgroundRunner(self)
        self._runner.failed.connect(self.failed.emit)

    @property
    def busy(self) -> bool:
        return self._runner.busy

    # -- disk usage --------------------------------------------------------

    def start_usage_survey(self) -> None:
        """Measure the largest folders. Reads only; deletes nothing."""
        self._runner.start(survey, self.usage_ready.emit)

    # -- startup -----------------------------------------------------------

    def start_startup_scan(self) -> None:
        self._runner.start(self._startup.entries, self.startup_ready.emit)

    def start_startup_toggle(self, entry: StartupEntry, enabled: bool) -> None:
        self._runner.start(
            lambda: self._startup.set_enabled(entry, enabled),
            self.startup_toggled.emit,
        )

    def shutdown(self) -> None:
        self._runner.shutdown()
