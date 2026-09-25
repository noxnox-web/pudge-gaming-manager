"""The system audit, «Настройки Windows», and the change history.

Three tools that share one worker because they share a shape: read on a
worker thread, show a dialog, and — for settings and history — make a
change that the engine backs up and verifies. One job at a time; a second
request while one runs is ignored rather than queued, the same as the other
controllers.
"""

from __future__ import annotations

import threading

from PySide6.QtCore import QObject, Signal

from ...core import services
from ...core.benchmark import load_monitor
from ...core.settings.service import SettingsService
from .background import BackgroundRunner


class SystemController(QObject):
    audit_ready = Signal(object)
    """Emits a :class:`SystemReport`."""

    settings_loaded = Signal(object)
    """Emits a :class:`SettingsView`."""

    settings_planned = Signal(object)
    """Emits the :class:`Plan` for the operator to confirm."""

    settings_applied = Signal(object)
    """Emits ``(RunReport, notes)``."""

    history_ready = Signal(object)
    """Emits ``list[ChangeRecord]``."""

    restored = Signal(object)
    """Emits ``list[RestoreResult]``."""

    monitor_progress = Signal(float)
    """Emits 0..1 while a load recording runs (from the worker thread)."""

    monitor_done = Signal(object)
    """Emits a :class:`LoadReport`."""

    failed = Signal(str)

    def __init__(self, parent: QObject | None = None, service: SettingsService | None = None) -> None:
        super().__init__(parent)
        self._service = service
        self._runner = BackgroundRunner(self)
        self._runner.failed.connect(self._on_failed)
        self._applying = False
        # The load recording runs for minutes while someone plays, so it has
        # its own worker: the audit and settings stay usable meanwhile.
        self._monitor = BackgroundRunner(self)
        self._monitor.failed.connect(self._on_failed)
        self._stop_monitor = threading.Event()

    def _settings(self) -> SettingsService:
        if self._service is None:
            self._service = services.create_settings_service()
        return self._service

    @property
    def busy(self) -> bool:
        return self._runner.busy

    @property
    def applying(self) -> bool:
        """True while a change is being written: closing must wait."""
        return self._applying and self._runner.busy

    def _on_failed(self, message: str) -> None:
        self._applying = False
        self.failed.emit(message)

    # -- audit -------------------------------------------------------------

    def start_audit(self, snapshot: object | None) -> None:
        self._runner.start(lambda: services.system_audit(snapshot), self.audit_ready.emit)

    # -- settings ----------------------------------------------------------

    def load_settings(self) -> None:
        self._runner.start(lambda: self._settings().load(), self.settings_loaded.emit)

    def plan_settings(self, choices: dict[str, str], device_ids: set[str]) -> None:
        self._runner.start(
            lambda: self._settings().preview(choices, device_ids),
            self.settings_planned.emit,
        )

    def plan_profile_fixes(self, fixes: object) -> None:
        self._runner.start(
            lambda: self._settings().preview_profile_fixes(fixes),
            self.settings_planned.emit,
        )

    def apply_settings(self, plan: object) -> None:
        self._applying = True
        self._runner.start(lambda: self._settings().apply(plan), self._on_applied)

    def _on_applied(self, payload: object) -> None:
        self._applying = False
        self.settings_applied.emit(payload)

    # -- load recording ------------------------------------------------------

    @property
    def monitoring(self) -> bool:
        return self._monitor.busy

    def start_monitor(self, duration_s: float) -> None:
        self._stop_monitor.clear()
        self._monitor.start(
            lambda: load_monitor.record(
                duration_s,
                should_stop=self._stop_monitor.is_set,
                progress=self.monitor_progress.emit,
            ),
            self.monitor_done.emit,
        )

    def stop_monitor(self) -> None:
        """Ends the recording at the next sample; the partial report is kept."""
        self._stop_monitor.set()

    # -- history -----------------------------------------------------------

    def load_history(self) -> None:
        self._runner.start(services.change_history, self.history_ready.emit)

    def restore(self, backup_id: str) -> None:
        self._applying = True
        self._runner.start(
            lambda: [services.restore_change(backup_id)], self._on_restored
        )

    def restore_all(self) -> None:
        self._applying = True
        self._runner.start(services.restore_all_changes, self._on_restored)

    def _on_restored(self, payload: object) -> None:
        self._applying = False
        self.restored.emit(payload)

    def shutdown(self) -> None:
        # Stop first: joining a ten-minute recording would hang the close.
        self._stop_monitor.set()
        self._monitor.shutdown()
        self._runner.shutdown()
