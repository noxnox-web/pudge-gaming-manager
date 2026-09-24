"""Steam reset planning and execution for the GUI.

Both steps read or delete large amounts of data, so they run off the UI
thread like the scan does. The keep-list comes from a Golden Profile the
operator loaded: a reset never removes a game unless a profile said which
games to keep, so a stray click cannot wipe an unconfigured PC clean.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from ...core.profiles import storage
from ...core.games import SteamWiper, WipePlan
from .background import BackgroundRunner


class SteamController(QObject):
    """Plans a Steam reset from a profile, and performs an approved plan."""

    planned = Signal(object)
    """Emits a :class:`WipePlan`, or ``None`` when Steam is not installed."""

    wiped = Signal(object)
    """Emits a :class:`WipeResult`."""

    failed = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._wiper = SteamWiper()
        self._runner = BackgroundRunner(self)
        self._runner.failed.connect(self._on_failed)
        self._wiping = False

    @property
    def busy(self) -> bool:
        return self._runner.busy

    @property
    def wiping(self) -> bool:
        """True only while games are being deleted, for the close guard."""
        return self._wiping

    def start_plan(self, profile_path: str) -> None:
        def work() -> object:
            # Loading validates the untrusted profile before any value is used.
            profile = storage.load(profile_path)
            keep = set(profile.games.keep_steam_app_ids)
            return self._wiper.scan(keep)

        self._runner.start(work, self.planned.emit)

    def start_wipe(self, plan: WipePlan) -> None:
        if self._runner.start(lambda: self._wiper.wipe(plan), self._on_wiped):
            self._wiping = True

    def _on_wiped(self, result: object) -> None:
        self._wiping = False
        self.wiped.emit(result)

    def _on_failed(self, message: str) -> None:
        self._wiping = False
        self.failed.emit(message)

    def shutdown(self) -> None:
        self._runner.shutdown()
