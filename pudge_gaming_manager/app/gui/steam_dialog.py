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

#: Item data role holding a remove-row's Steam app id.
_APP_ID = Qt.ItemDataRole.UserRole + 1


class SteamResetDialog(QDialog):
    """Shows the reset plan and asks for confirmation."""

    def __init__(self, plan: WipePlan, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Очистка Стима")
        self.setMinimumSize(680, 500)
        self.setStyleSheet(theme.stylesheet())
        self._plan = plan

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(12)

        if not plan.has_changes:
            title = "Удалять нечего"
        elif plan.remove:
            title = f"Удалить игр: {len(plan.remove)}, освободить {plan.size_display}"
        else:
            title = f"Очистить кэш и выйти из аккаунтов, освободить {plan.size_display}"
        heading = QLabel(title)
        heading.setObjectName("Title")
        layout.addWidget(heading)

        signout_note = ""
        if plan.signout:
            remembered = (
                f" (на ПК запомнено: {plan.accounts})" if plan.accounts else ""
            )
            signout_note = (
                f" Из всех аккаунтов Steam выполнится выход{remembered} — "
                "игрокам придётся снова ввести пароль."
            )
        subtitle = QLabel(
            f"Сохранится игр: {len(plan.keep)}. Удаление игры стирает её с "
            "диска — это необратимо, Steam скачает её заново при переустановке. "
            "Контент Мастерской очищается для всех игр."
            f"{signout_note} Пока ничего не изменено."
        )
        subtitle.setObjectName("Subtitle")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        self._rows = QListWidget()
        self._rows.setWordWrap(True)
        self._rows.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._rows.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        self._populate(self._rows)
        layout.addWidget(self._rows, stretch=1)

        buttons = QDialogButtonBox()
        self._remove = buttons.addButton(
            "Очистить", QDialogButtonBox.ButtonRole.AcceptRole
        )
        self._remove.setObjectName("Primary")
        self._remove.setEnabled(plan.has_changes)
        cancel = buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        cancel.setText("Отмена")
        cancel.setObjectName("Secondary")
        # Cancel is the default: a stray Enter must not delete games.
        cancel.setDefault(True)
        cancel.setAutoDefault(True)
        self._remove.setAutoDefault(False)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def kept_back_ids(self) -> set[int]:
        """App IDs the operator unticked — games to keep after all."""
        kept: set[int] = set()
        for row in range(self._rows.count()):
            item = self._rows.item(row)
            app_id = item.data(_APP_ID)
            if app_id is not None and item.checkState() == Qt.CheckState.Unchecked:
                kept.add(int(app_id))
        return kept

    def confirmed_plan(self) -> WipePlan:
        """The plan to run: unticked games moved back to keep."""
        return self._plan.with_kept_back(self.kept_back_ids())

    def _populate(self, rows: QListWidget) -> None:
        for game in sorted(
            self._plan.remove, key=lambda g: g.size_bytes or 0, reverse=True
        ):
            item = QListWidgetItem(f"{game.size_display:>10}   {game.name}")
            item.setForeground(Qt.GlobalColor.white)
            item.setData(Qt.ItemDataRole.UserRole, theme.CRITICAL)
            item.setData(_APP_ID, game.app_id)
            item.setToolTip(f"App {game.app_id} — {game.install_path}")
            # Ticked = this game will be removed. Untick to keep it, even if
            # it is not in the built-in keep-list.
            item.setFlags(
                Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable
            )
            item.setCheckState(Qt.CheckState.Checked)
            rows.addItem(item)
        for game in self._plan.keep:
            item = QListWidgetItem(f"оставить {game.size_display:>10}   {game.name}")
            item.setForeground(Qt.GlobalColor.gray)
            rows.addItem(item)
        if self._plan.caches:
            total = format_size(self._plan.cache_bytes)
            item = QListWidgetItem(
                f"кэш      {total:>10}   Кэш Steam "
                f"(папок: {len(self._plan.caches)})"
            )
            item.setForeground(Qt.GlobalColor.gray)
            item.setToolTip(
                "\n".join(f"{c.label}: {c.size_display}" for c in self._plan.caches)
            )
            rows.addItem(item)
        if self._plan.signout:
            total = format_size(self._plan.signout_bytes)
            item = QListWidgetItem(
                f"выход    {total:>10}   Все аккаунты Steam "
                "(список аккаунтов, токены, куки)"
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
        self.setWindowTitle("Очистка Стима завершена")
        self.setMinimumSize(600, 380)
        self.setStyleSheet(theme.stylesheet())

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(12)

        heading = QLabel(
            f"Удалено игр: {result.removed_games}, освобождено {result.size_display}"
        )
        heading.setObjectName("Title")
        layout.addWidget(heading)

        if result.signed_out:
            done = QLabel("Из всех аккаунтов Steam выполнен выход.")
            done.setObjectName("ScoreNote")
            layout.addWidget(done)

        if not result.steam_stopped:
            note = QLabel(
                "Steam не был запущен или его не удалось остановить. Игры, "
                "которые были заняты, могли остаться."
            )
            note.setObjectName("ScoreNote")
            note.setWordWrap(True)
            layout.addWidget(note)

        detail = QListWidget()
        detail.setWordWrap(True)
        detail.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        detail.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        for line in result.refused:
            detail.addItem(QListWidgetItem(f"оставлено (защита): {line}"))
        for line in result.failed:
            detail.addItem(QListWidgetItem(f"ошибка: {line}"))
        if not result.refused and not result.failed:
            detail.addItem(QListWidgetItem("Все запланированные игры удалены."))
        layout.addWidget(detail, stretch=1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close_btn = buttons.button(QDialogButtonBox.StandardButton.Close)
        close_btn.setObjectName("Secondary")
        close_btn.setText("Закрыть")
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)
