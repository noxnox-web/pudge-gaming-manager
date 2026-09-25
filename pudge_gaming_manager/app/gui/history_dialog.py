"""«История изменений»: every change PGM recorded, and undoing it.

Rows still in force can be selected and rolled back one at a time, or all
at once. Both go through the tweak's own verified rollback; the dialog only
asks. Rolling back is itself a change to the machine, so each action asks
for confirmation first, with No as the default.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QVBoxLayout,
)

from . import theme

_BACKUP_ID = Qt.ItemDataRole.UserRole + 1

_STATE_COLOURS = {
    "APPLIED": theme.TEXT,
    "RESTORED": theme.TEXT_FAINT,
    "FAILED": theme.WARNING,
    "APPLYING": theme.CRITICAL,
}


class HistoryDialog(QDialog):
    restore_requested = Signal(str)
    restore_all_requested = Signal()

    def __init__(self, records, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("История изменений")
        self.setMinimumSize(860, 560)
        self.setStyleSheet(theme.stylesheet())

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(12)
        self._heading = QLabel()
        self._heading.setObjectName("Title")
        layout.addWidget(self._heading)
        note = QLabel(
            "Каждое изменение записывается до того, как сделано: прежнее "
            "значение, его тип и было ли оно вообще. Откат возвращает именно "
            "его и перечитывает, чтобы подтвердить. Если одну настройку меняли "
            "несколько раз, откатывать нужно с последнего изменения — "
            "«Откатить всё» делает это само."
        )
        note.setObjectName("Subtitle")
        note.setWordWrap(True)
        layout.addWidget(note)

        self._list = QListWidget()
        self._list.setWordWrap(True)
        self._list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._list.itemSelectionChanged.connect(self._sync)
        layout.addWidget(self._list, stretch=1)

        buttons = QDialogButtonBox()
        self._restore = buttons.addButton("Откатить выбранное", QDialogButtonBox.ButtonRole.ActionRole)
        self._restore.setObjectName("Secondary")
        self._restore.setAutoDefault(False)
        self._restore.clicked.connect(self._on_restore)
        self._restore_all = buttons.addButton("Откатить всё", QDialogButtonBox.ButtonRole.ActionRole)
        self._restore_all.setObjectName("Secondary")
        self._restore_all.setAutoDefault(False)
        self._restore_all.clicked.connect(self._on_restore_all)
        close = buttons.addButton(QDialogButtonBox.StandardButton.Close)
        close.setText("Закрыть")
        close.setObjectName("Secondary")
        close.setDefault(True)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._busy = False
        self.set_records(records)

    def set_records(self, records) -> None:
        self._list.clear()
        applied = 0
        for record in records:
            item = QListWidgetItem(record.line())
            item.setForeground(QColor(_STATE_COLOURS.get(record.state, theme.TEXT)))
            if record.is_applied and record.scope != "NONE":
                item.setData(_BACKUP_ID, record.backup_id)
                applied += 1
            else:
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            item.setToolTip(f"{record.target}\nтип: {record.old_value_kind or '—'}")
            self._list.addItem(item)
        self._applied = applied
        self._heading.setText(
            f"Изменений в силе: {applied} из {len(records)}" if records else "Изменений ещё не было"
        )
        self._sync()

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        self._sync()

    def _selected_id(self) -> str | None:
        items = self._list.selectedItems()
        return items[0].data(_BACKUP_ID) if items else None

    def _sync(self) -> None:
        self._restore.setEnabled(not self._busy and self._selected_id() is not None)
        self._restore_all.setEnabled(not self._busy and self._applied > 0)

    def _confirm(self, text: str) -> bool:
        answer = QMessageBox.question(
            self, "Откат", text,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return answer == QMessageBox.StandardButton.Yes

    def _on_restore(self) -> None:
        backup_id = self._selected_id()
        if backup_id and self._confirm("Вернуть прежнее значение выбранной настройки?"):
            self.restore_requested.emit(backup_id)

    def _on_restore_all(self) -> None:
        if self._confirm(
            f"Вернуть прежние значения всех {self._applied} изменений, которые "
            "сейчас в силе? Идёт от последнего к первому."
        ):
            self.restore_all_requested.emit()
