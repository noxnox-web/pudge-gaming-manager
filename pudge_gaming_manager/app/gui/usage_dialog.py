"""Where the disk space went, and what starts with Windows.

Two read-first dialogs kept together because they are the same shape: a
list the operator reads, with at most one reversible action on it.

The usage report has no action at all — it deletes nothing and offers no
button that would. It exists to answer "what is using 200 GB" for the
folders the cleaner's allowlist deliberately says nothing about, and the
decision about those folders stays with a person.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
)

from ...core.cleanup import UsageReport
from ...core.startup import StartupEntry
from . import theme

#: Item data role holding a startup row's entry object.
_ENTRY = Qt.ItemDataRole.UserRole + 1


class UsageDialog(QDialog):
    """The largest folders on this PC. Read-only."""

    def __init__(self, report: UsageReport, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Что занимает место")
        self.setMinimumSize(780, 560)
        self.setStyleSheet(theme.stylesheet())

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(12)

        heading = QLabel(
            f"Крупнейшие папки: {report.size_display} в {len(report.folders)} папках"
            if report.folders
            else "Ничего не измерено"
        )
        heading.setObjectName("Title")
        layout.addWidget(heading)

        notes = [
            "Только просмотр — отсюда ничего не удаляется. Это папки, о "
            "которых чистильщик молчит: он работает по белому списку и не трогает "
            "то, что в нём не перечислено.",
        ]
        if not report.complete:
            notes.append(
                "Обход прерван по времени, поэтому список неполный: "
                "какие-то папки не успели попасть в замер."
            )
        if report.unreadable:
            notes.append(
                f"Не удалось прочитать папок: {report.unreadable} — "
                "они в итог не вошли."
            )
        subtitle = QLabel(" ".join(notes))
        subtitle.setObjectName("Subtitle")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        rows = QListWidget()
        rows.setWordWrap(True)
        rows.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        rows.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        for folder in report.folders:
            item = QListWidgetItem(
                f"{folder.size_display:>10}   {folder.path}   "
                f"({folder.file_count} файлов)"
            )
            item.setToolTip(str(folder.path))
            rows.addItem(item)
        if not report.folders:
            rows.addItem(QListWidgetItem("Измеримых папок не найдено."))
        layout.addWidget(rows, stretch=1)

        footer = QLabel(
            f"Замер занял {report.duration_s:.0f} с; корней просмотрено: "
            f"{len(report.roots_scanned)}; глубина: {report.depth}."
        )
        footer.setObjectName("ScoreNote")
        layout.addWidget(footer)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close = buttons.button(QDialogButtonBox.StandardButton.Close)
        close.setObjectName("Secondary")
        close.setText("Закрыть")
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)


class StartupDialog(QDialog):
    """What launches at sign-in, with a switch for each entry."""

    def __init__(self, entries: list[StartupEntry], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Автозагрузка")
        self.setMinimumSize(820, 560)
        self.setStyleSheet(theme.stylesheet())
        self._suppress = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(12)

        active = sum(1 for e in entries if e.enabled)
        heading = QLabel(
            f"Запускается при входе: {active} из {len(entries)}"
            if entries
            else "Записей автозагрузки нет"
        )
        heading.setObjectName("Title")
        layout.addWidget(heading)

        subtitle = QLabel(
            "Снятая галочка отключает запись так же, как это делает "
            "диспетчер задач: сама запись и ярлык остаются на месте, "
            "меняется только флаг разрешения — включить обратно можно в "
            "любой момент. Ничего не удаляется."
        )
        subtitle.setObjectName("Subtitle")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        self._rows = QListWidget()
        self._rows.setWordWrap(True)
        self._rows.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self._rows.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        for entry in entries:
            self._rows.addItem(self._row(entry))
        layout.addWidget(self._rows, stretch=1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close = buttons.button(QDialogButtonBox.StandardButton.Close)
        close.setObjectName("Secondary")
        close.setText("Закрыть")
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)

    # -- rows --------------------------------------------------------------

    @staticmethod
    def _row(entry: StartupEntry) -> QListWidgetItem:
        publisher = entry.publisher or "издатель неизвестен"
        text = f"{entry.name}    —    {publisher}\n{entry.location_label}: {entry.command}"
        if entry.protected_reason:
            text += f"\nНе отключается: {entry.protected_reason}"
        item = QListWidgetItem(text)
        item.setData(_ENTRY, entry)
        item.setToolTip(entry.command)
        item.setForeground(
            QColor(theme.TEXT if entry.enabled else theme.TEXT_FAINT)
        )
        flags = Qt.ItemFlag.ItemIsEnabled
        if not entry.protected_reason:
            flags |= Qt.ItemFlag.ItemIsUserCheckable
        item.setFlags(flags)
        item.setCheckState(
            Qt.CheckState.Checked if entry.enabled else Qt.CheckState.Unchecked
        )
        return item

    def connect_toggle(self, handler) -> None:
        """Call ``handler(entry, enabled)`` when a row is ticked or unticked.

        Wired from the dashboard rather than in ``__init__`` so this dialog
        holds no controller: the registry write is the window's business,
        and a dialog that could write on its own would be a second place
        the rule "the GUI never touches the OS" had to be enforced.
        """
        self._handler = handler
        self._rows.itemChanged.connect(self._on_item_changed)

    def _on_item_changed(self, item: QListWidgetItem) -> None:
        if self._suppress:
            return
        entry = item.data(_ENTRY)
        if entry is None:
            return
        self._handler(entry, item.checkState() == Qt.CheckState.Checked)

    def apply_result(self, entry: StartupEntry) -> None:
        """Re-render the row from the state the registry actually reports.

        A toggle that Windows refused must not leave a tick claiming it
        worked, so the row follows the read-back, not the click.
        """
        for row in range(self._rows.count()):
            item = self._rows.item(row)
            existing = item.data(_ENTRY)
            if existing is None or existing.key != entry.key:
                continue
            self._suppress = True
            try:
                item.setData(_ENTRY, entry)
                item.setCheckState(
                    Qt.CheckState.Checked
                    if entry.enabled
                    else Qt.CheckState.Unchecked
                )
                item.setForeground(
                    QColor(theme.TEXT if entry.enabled else theme.TEXT_FAINT)
                )
            finally:
                self._suppress = False
            return
