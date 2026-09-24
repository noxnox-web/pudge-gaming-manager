"""The confirmation step for a Steam reset, and its result.

Removing a game is irreversible, so this dialog is the operator's decision
point (rule #39). It lists every game that will be removed with its size,
every game that will be kept, and the caches to be cleared — before anything
is touched. Cancel is the default button: a stray Enter must not wipe a club
PC's games.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
)

from ...core.games import WipePlan, WipeResult
from ...utilities.formatting import format_size
from . import theme


class SteamResetDialog(QDialog):
    """Shows the reset plan and asks for confirmation."""

    def __init__(self, plan: WipePlan, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Reset Steam games")
        self.setMinimumSize(680, 500)
        self.setStyleSheet(theme.stylesheet())
        self._plan = plan

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(12)

        if not plan.has_changes:
            title = "Nothing to remove"
        elif plan.remove:
            title = f"Remove {len(plan.remove)} game(s), free {plan.size_display}"
        else:
            title = f"Clear Steam caches and sign out, free {plan.size_display}"
        heading = QLabel(title)
        heading.setObjectName("Title")
        layout.addWidget(heading)

        signout_note = ""
        if plan.signout:
            remembered = (
                f" ({plan.accounts} remembered on this PC)" if plan.accounts else ""
            )
            signout_note = (
                f" Every Steam account will be signed out{remembered} — "
                "players must enter their password again."
            )
        subtitle = QLabel(
            f"{len(plan.keep)} game(s) will be kept. Removing a game deletes "
            "it from disk — this cannot be undone, and Steam must re-download "
            "it to reinstall. Workshop content is cleared for every game."
            f"{signout_note} Nothing has been changed yet."
        )
        subtitle.setObjectName("Subtitle")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        rows = QListWidget()
        rows.setWordWrap(True)
        rows.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        rows.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        self._populate(rows)
        layout.addWidget(rows, stretch=1)

        buttons = QDialogButtonBox()
        self._remove = buttons.addButton(
            "Reset Steam", QDialogButtonBox.ButtonRole.AcceptRole
        )
        self._remove.setObjectName("Primary")
        self._remove.setEnabled(plan.has_changes)
        cancel = buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        cancel.setObjectName("Secondary")
        # Cancel is the default: a stray Enter must not delete games.
        cancel.setDefault(True)
        cancel.setAutoDefault(True)
        self._remove.setAutoDefault(False)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _populate(self, rows: QListWidget) -> None:
        for game in sorted(
            self._plan.remove, key=lambda g: g.size_bytes or 0, reverse=True
        ):
            item = QListWidgetItem(f"REMOVE   {game.size_display:>10}   {game.name}")
            item.setForeground(Qt.GlobalColor.white)
            item.setData(Qt.ItemDataRole.UserRole, theme.CRITICAL)
            item.setToolTip(f"App {game.app_id} — {game.install_path}")
            rows.addItem(item)
        for game in self._plan.keep:
            item = QListWidgetItem(f"keep     {game.size_display:>10}   {game.name}")
            item.setForeground(Qt.GlobalColor.gray)
            rows.addItem(item)
        if self._plan.caches:
            total = format_size(self._plan.cache_bytes)
            item = QListWidgetItem(
                f"cache    {total:>10}   Steam caches "
                f"({len(self._plan.caches)} folder(s))"
            )
            item.setForeground(Qt.GlobalColor.gray)
            item.setToolTip(
                "\n".join(f"{c.label}: {c.size_display}" for c in self._plan.caches)
            )
            rows.addItem(item)
        if self._plan.signout:
            total = format_size(self._plan.signout_bytes)
            item = QListWidgetItem(
                f"sign out {total:>10}   All Steam accounts "
                "(account list, saved tokens, web cookies)"
            )
            item.setForeground(Qt.GlobalColor.white)
            item.setToolTip(
                "\n".join(f"{t.label}: {t.path}" for t in self._plan.signout)
            )
            rows.addItem(item)


class SteamResultDialog(QDialog):
    """What the reset actually removed."""

    def __init__(self, result: WipeResult, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Steam reset complete")
        self.setMinimumSize(600, 380)
        self.setStyleSheet(theme.stylesheet())

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(12)

        heading = QLabel(
            f"Removed {result.removed_games} game(s), freed {result.size_display}"
        )
        heading.setObjectName("Title")
        layout.addWidget(heading)

        if result.signed_out:
            done = QLabel("All Steam accounts were signed out.")
            done.setObjectName("ScoreNote")
            layout.addWidget(done)

        if not result.steam_stopped:
            note = QLabel(
                "Steam was not running, or could not be stopped. Games that "
                "were in use may have been kept."
            )
            note.setObjectName("ScoreNote")
            note.setWordWrap(True)
            layout.addWidget(note)

        detail = QListWidget()
        detail.setWordWrap(True)
        detail.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        detail.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        for line in result.refused:
            detail.addItem(QListWidgetItem(f"kept (safety): {line}"))
        for line in result.failed:
            detail.addItem(QListWidgetItem(f"failed: {line}"))
        if not result.refused and not result.failed:
            detail.addItem(QListWidgetItem("Every planned game was removed."))
        layout.addWidget(detail, stretch=1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setObjectName(
            "Secondary"
        )
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)
