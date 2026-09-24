"""Profile, disk-cleanup and Steam-reset button handlers for the dashboard.

Kept out of ``dashboard.py`` as a mixin so that file stays focused on layout
and the scan/optimize lifecycle, and under the module-size limit. Every
method here runs on the UI thread and delegates the slow, blocking or
destructive work to a controller's worker thread.

The type: ignore comments are because the mixin reaches attributes the
``Dashboard`` defines (``_result``, ``_profiles``, ``_steam``, ``_status``);
they exist at runtime on every real instance.
"""

from __future__ import annotations

from PySide6.QtWidgets import QFileDialog, QMessageBox

from ...core import services
from ...core.profiles.storage import PROFILE_FILENAME
from ...utilities.exceptions import PgmError
from ...utilities.logging_setup import get_logger
from . import presenters
from .cleanup_dialog import CleanupDialog, CleanupResultDialog
from .profile_dialog import ComparisonDialog
from .steam_dialog import SteamResetDialog, SteamResultDialog

_log = get_logger(__name__)


class ProfileAndGamesActions:
    """Mixin: profile, disk cleanup, Steam reset, startup recovery."""

    # -- startup -----------------------------------------------------------

    def _report_interrupted_changes(self) -> None:
        """Tell the operator about changes a previous run never finished.

        Shown once: acknowledging marks them reported. Nothing is rolled back
        automatically — an unattended change nobody previewed is exactly what
        the safety model forbids, and the operator may already have fixed it.
        """
        try:
            changes = services.interrupted_changes()
        except PgmError as exc:
            _log.warning("could not check for interrupted changes: %s", exc.what)
            return
        if not changes:
            return
        body = "\n".join(f"• {c.line()}" for c in changes)
        QMessageBox.warning(
            self,
            "Прерванные изменения",
            "Программа была закрыта или аварийно завершилась во время "
            "изменения этих настроек, поэтому они не были подтверждены. "
            "Возможно, применены частично. Проверьте и при необходимости "
            "верните исходное значение вручную:\n\n"
            f"{body}",
        )
        try:
            services.acknowledge_interrupted(changes)
        except PgmError as exc:
            _log.warning("could not record interrupted changes: %s", exc.what)

    # -- golden profile ----------------------------------------------------

    def _set_profile_actions(self, enabled: bool) -> None:
        self._save_profile.setEnabled(enabled)  # type: ignore[attr-defined]
        self._compare_profile.setEnabled(enabled)  # type: ignore[attr-defined]
        self._reset_steam.setEnabled(enabled)  # type: ignore[attr-defined]
        self._clean_disk.setEnabled(enabled)  # type: ignore[attr-defined]

    def _on_save_profile(self) -> None:
        if self._result is None or self._profiles.busy:  # type: ignore[attr-defined]
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Сохранить этот ПК как профиль", PROFILE_FILENAME,
            "Профиль (*.json)",
        )
        if not path:
            return
        self._set_profile_actions(False)
        self._status.setText("Сохранение профиля…")  # type: ignore[attr-defined]
        self._profiles.start_save(  # type: ignore[attr-defined]
            self._result.snapshot, path,  # type: ignore[attr-defined]
            presenters.profile_name_from_path(path),
        )

    def _on_compare_profile(self) -> None:
        if self._result is None or self._profiles.busy:  # type: ignore[attr-defined]
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Сравнить с профилем", "", "Профиль (*.json)"
        )
        if not path:
            return
        self._set_profile_actions(False)
        self._status.setText("Сравнение с профилем…")  # type: ignore[attr-defined]
        self._profiles.start_compare(self._result.snapshot, path)  # type: ignore[attr-defined]

    def _on_profile_saved(self, path: object) -> None:
        self._status.setText("")  # type: ignore[attr-defined]
        self._set_profile_actions(True)
        QMessageBox.information(self, "Профиль сохранён", f"Сохранено в {path}")

    def _on_profile_compared(self, result: object) -> None:
        self._status.setText("")  # type: ignore[attr-defined]
        self._set_profile_actions(True)
        ComparisonDialog(result, self).exec()  # type: ignore[arg-type]

    def _on_profile_failed(self, message: str) -> None:
        self._status.setText("")  # type: ignore[attr-defined]
        self._set_profile_actions(True)
        QMessageBox.warning(self, "Профиль", message)

    # -- disk cleanup ------------------------------------------------------

    def _on_clean_disk(self) -> None:
        """Inventory every cleanup category, then show what was found.

        The scan changes nothing; it is the preview that asks permission.
        Unlike the cleanup inside OPTIMIZE, this offers every category the
        program knows about, including the ones that are off by default.
        """
        if self._cleanup.busy:  # type: ignore[attr-defined]
            return
        self._set_profile_actions(False)
        self._status.setText("Поиск мусора на диске…")  # type: ignore[attr-defined]
        self._cleanup.start_plan()  # type: ignore[attr-defined]

    def _on_cleanup_planned(self, plan: object) -> None:
        self._status.setText("")  # type: ignore[attr-defined]
        self._set_profile_actions(True)
        if not plan.has_content:  # type: ignore[union-attr]
            QMessageBox.information(
                self, "Очистка диска", "Удалять нечего — на диске чисто."
            )
            return

        dialog = CleanupDialog(plan, self)  # type: ignore[arg-type]
        if dialog.exec() != CleanupDialog.DialogCode.Accepted:
            return
        selected = dialog.selection()
        if not selected:
            return

        self._set_profile_actions(False)
        self._status.setText("Очистка диска…")  # type: ignore[attr-defined]
        self._cleanup.start_clean(plan, selected)  # type: ignore[attr-defined]

    def _on_cleanup_finished(self, result: object) -> None:
        self._status.setText("")  # type: ignore[attr-defined]
        self._set_profile_actions(True)
        CleanupResultDialog(result, self).exec()  # type: ignore[arg-type]
        # Free space changed, so the dashboard's disk figure and score are
        # now stale. Re-measure rather than leaving a number nobody took.
        self._controller.start()  # type: ignore[attr-defined]

    def _on_cleanup_failed(self, message: str) -> None:
        self._status.setText("")  # type: ignore[attr-defined]
        self._set_profile_actions(True)
        QMessageBox.warning(self, "Очистка диска", message)

    # -- steam reset -------------------------------------------------------

    def _on_reset_steam(self) -> None:
        """Plan a reset against the built-in keep-list.

        No profile or config file: the keep-list is baked into the program
        (``games.steam.default_keep``), and the preview then lets the
        operator untick any game before anything is removed.
        """
        if self._steam.busy:  # type: ignore[attr-defined]
            return
        self._set_profile_actions(False)
        self._status.setText("Подготовка очистки Стима…")  # type: ignore[attr-defined]
        self._steam.start_plan()  # type: ignore[attr-defined]

    def _on_steam_planned(self, plan: object) -> None:
        self._status.setText("")  # type: ignore[attr-defined]
        self._set_profile_actions(True)
        if plan is None:
            QMessageBox.information(
                self, "Очистка Стима",
                "Steam на этом ПК не установлен — очищать нечего.",
            )
            return
        dialog = SteamResetDialog(plan, self)  # type: ignore[arg-type]
        if dialog.exec() != SteamResetDialog.DialogCode.Accepted:
            return
        # Games the operator unticked are moved back to keep.
        confirmed = dialog.confirmed_plan()
        self._set_profile_actions(False)
        self._status.setText("Удаление игр…")  # type: ignore[attr-defined]
        self._steam.start_wipe(confirmed)  # type: ignore[attr-defined]

    def _on_steam_wiped(self, result: object) -> None:
        self._status.setText("")  # type: ignore[attr-defined]
        self._set_profile_actions(True)
        SteamResultDialog(result, self).exec()  # type: ignore[arg-type]

    def _on_steam_failed(self, message: str) -> None:
        self._status.setText("")  # type: ignore[attr-defined]
        self._set_profile_actions(True)
        QMessageBox.warning(self, "Очистка Стима", message)
