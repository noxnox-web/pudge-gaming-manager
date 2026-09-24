"""Golden Profile and Steam-reset button handlers for the dashboard.

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
from .profile_dialog import ComparisonDialog
from .steam_dialog import SteamResetDialog, SteamResultDialog

_log = get_logger(__name__)


class ProfileAndGamesActions:
    """Mixin: save/compare a profile, reset Steam games, startup recovery."""

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
            "Interrupted changes",
            "Pudge Gaming Manager was closed or crashed while changing these "
            "settings, so they were never verified. Each may be half-applied. "
            "Check them and restore the original value by hand if needed:\n\n"
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

    def _on_save_profile(self) -> None:
        if self._result is None or self._profiles.busy:  # type: ignore[attr-defined]
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Save this PC as a Golden Profile", PROFILE_FILENAME,
            "Golden Profile (*.json)",
        )
        if not path:
            return
        self._set_profile_actions(False)
        self._status.setText("Saving profile…")  # type: ignore[attr-defined]
        self._profiles.start_save(  # type: ignore[attr-defined]
            self._result.snapshot, path,  # type: ignore[attr-defined]
            presenters.profile_name_from_path(path),
        )

    def _on_compare_profile(self) -> None:
        if self._result is None or self._profiles.busy:  # type: ignore[attr-defined]
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Compare with a Golden Profile", "", "Golden Profile (*.json)"
        )
        if not path:
            return
        self._set_profile_actions(False)
        self._status.setText("Comparing with profile…")  # type: ignore[attr-defined]
        self._profiles.start_compare(self._result.snapshot, path)  # type: ignore[attr-defined]

    def _on_profile_saved(self, path: object) -> None:
        self._status.setText("")  # type: ignore[attr-defined]
        self._set_profile_actions(True)
        QMessageBox.information(self, "Profile saved", f"Saved to {path}")

    def _on_profile_compared(self, result: object) -> None:
        self._status.setText("")  # type: ignore[attr-defined]
        self._set_profile_actions(True)
        ComparisonDialog(result, self).exec()  # type: ignore[arg-type]

    def _on_profile_failed(self, message: str) -> None:
        self._status.setText("")  # type: ignore[attr-defined]
        self._set_profile_actions(True)
        QMessageBox.warning(self, "Golden Profile", message)

    # -- steam reset -------------------------------------------------------

    def _on_reset_steam(self) -> None:
        """Plan a reset from a profile the operator chooses.

        A reset needs a profile so the keep-list is explicit: without one it
        would remove every game, so there is no "reset without a profile"
        path by design.
        """
        if self._steam.busy:  # type: ignore[attr-defined]
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose the club profile (its games are kept)", "",
            "Golden Profile (*.json)",
        )
        if not path:
            return
        self._set_profile_actions(False)
        self._status.setText("Planning Steam reset…")  # type: ignore[attr-defined]
        self._steam.start_plan(path)  # type: ignore[attr-defined]

    def _on_steam_planned(self, plan: object) -> None:
        self._status.setText("")  # type: ignore[attr-defined]
        self._set_profile_actions(True)
        if plan is None:
            QMessageBox.information(
                self, "Steam reset",
                "Steam is not installed on this PC, so there is nothing to reset.",
            )
            return
        dialog = SteamResetDialog(plan, self)  # type: ignore[arg-type]
        if dialog.exec() != SteamResetDialog.DialogCode.Accepted:
            return
        self._set_profile_actions(False)
        self._status.setText("Removing games…")  # type: ignore[attr-defined]
        self._steam.start_wipe(plan)  # type: ignore[attr-defined]

    def _on_steam_wiped(self, result: object) -> None:
        self._status.setText("")  # type: ignore[attr-defined]
        self._set_profile_actions(True)
        SteamResultDialog(result, self).exec()  # type: ignore[arg-type]

    def _on_steam_failed(self, message: str) -> None:
        self._status.setText("")  # type: ignore[attr-defined]
        self._set_profile_actions(True)
        QMessageBox.warning(self, "Steam reset", message)
