"""The confirmation step for a disk cleanup, and its result.

Deleting a temporary file cannot be undone, so this dialog is the operator's
decision point (rule #39). Every category the scan found is listed with its
size and risk, ticked or unticked according to what it holds, and nothing is
touched until "Очистить" is pressed. Cancel is the default button.

Each row carries its own tooltip explaining *why* that category is safe to
remove — the same ``rationale`` the category itself declares, so the reason
shown to the operator cannot drift from the reason in the code.
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

from ...core.cleanup import (
    RECYCLE_BIN_ID,
    CleanupRisk,
    DiskCleanupPlan,
    DiskCleanupResult,
)
from ...utilities.formatting import format_size
from . import theme

#: Item data role holding a row's selection id (a category id, or the
#: Recycle Bin's sentinel).
_ENTRY_ID = Qt.ItemDataRole.UserRole + 1

#: Rows are coloured by how much the user could miss what is deleted.
_RISK_COLOUR = {
    CleanupRisk.SAFE: theme.TEXT,
    CleanupRisk.LOW: theme.TEXT,
    CleanupRisk.MEDIUM: theme.WARNING,
}


class CleanupDialog(QDialog):
    """Shows what a cleanup would remove and asks which parts to run."""

    def __init__(self, plan: DiskCleanupPlan, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Очистка диска")
        self.setMinimumSize(720, 540)
        self.setStyleSheet(theme.stylesheet())
        self._plan = plan

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(12)

        heading = QLabel(
            f"Можно освободить {plan.size_display}"
            if plan.has_content
            else "Удалять нечего"
        )
        heading.setObjectName("Title")
        layout.addWidget(heading)

        subtitle = QLabel(
            "Отмеченное будет удалено без возможности восстановления. "
            "Снимите галочку с того, что нужно оставить. Занятые файлы "
            "пропускаются. Пока ничего не изменено."
        )
        subtitle.setObjectName("Subtitle")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        self._rows = QListWidget()
        self._rows.setWordWrap(True)
        self._rows.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._rows.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        self._populate()
        layout.addWidget(self._rows, stretch=1)

        buttons = QDialogButtonBox()
        self._clean = buttons.addButton(
            "Очистить", QDialogButtonBox.ButtonRole.AcceptRole
        )
        self._clean.setObjectName("Primary")
        self._clean.setEnabled(plan.has_content)
        cancel = buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        cancel.setText("Отмена")
        cancel.setObjectName("Secondary")
        # Cancel is the default: a stray Enter must not wipe a disk.
        cancel.setDefault(True)
        cancel.setAutoDefault(True)
        self._clean.setAutoDefault(False)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    # -- result ------------------------------------------------------------

    def selection(self) -> set[str]:
        """The ids the operator left ticked."""
        chosen: set[str] = set()
        for row in range(self._rows.count()):
            item = self._rows.item(row)
            entry_id = item.data(_ENTRY_ID)
            if entry_id and item.checkState() == Qt.CheckState.Checked:
                chosen.add(str(entry_id))
        return chosen

    # -- construction ------------------------------------------------------

    def _populate(self) -> None:
        preselected = self._plan.default_selection()

        if self._plan.bin_state.has_content:
            state = self._plan.bin_state
            self._add_row(
                entry_id=RECYCLE_BIN_ID,
                label="Корзина",
                size=state.size_bytes,
                count=state.item_count,
                checked=RECYCLE_BIN_ID in preselected,
                colour=theme.TEXT,
                tooltip=(
                    "Очищается через системный вызов Windows на всех дисках. "
                    "Всё, что лежит в корзине, пользователь уже удалил."
                ),
            )

        for report in sorted(
            self._plan.scan.categories, key=lambda r: r.total_bytes, reverse=True
        ):
            category = report.category
            if not report.available:
                self._add_unavailable(category.name, report.unavailable_reason)
                continue
            if not report.file_count:
                continue
            self._add_row(
                entry_id=category.id,
                label=category.name,
                size=report.total_bytes,
                count=report.file_count,
                checked=category.id in preselected,
                colour=_RISK_COLOUR.get(category.risk, theme.TEXT),
                tooltip=f"{category.description}\n\n{category.rationale}",
            )

    def _add_row(
        self,
        *,
        entry_id: str,
        label: str,
        size: int,
        count: int,
        checked: bool,
        colour: str,
        tooltip: str,
    ) -> None:
        item = QListWidgetItem(f"{format_size(size):>10}   {label}   ({count})")
        item.setData(_ENTRY_ID, entry_id)
        item.setForeground(QColor(colour))
        item.setToolTip(tooltip)
        item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(
            Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        )
        self._rows.addItem(item)

    def _add_unavailable(self, name: str, reason: str) -> None:
        """A category that cannot be scanned here, shown but not offered.

        Listing it is the point: an operator who does not see "Кэш Opera"
        cannot tell whether it was clean or never looked at.
        """
        item = QListWidgetItem(f"{'—':>10}   {name}   ({reason})")
        item.setForeground(Qt.GlobalColor.gray)
        self._rows.addItem(item)


class CleanupResultDialog(QDialog):
    """What the cleanup actually removed."""

    def __init__(self, result: DiskCleanupResult, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Очистка завершена")
        self.setMinimumSize(600, 380)
        self.setStyleSheet(theme.stylesheet())

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(12)

        heading = QLabel(f"Освобождено {result.size_display}")
        heading.setObjectName("Title")
        layout.addWidget(heading)

        subtitle = QLabel(f"Удалено файлов: {result.files.deleted_files}")
        subtitle.setObjectName("Subtitle")
        layout.addWidget(subtitle)

        detail = QListWidget()
        detail.setWordWrap(True)
        detail.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        detail.setSelectionMode(QListWidget.SelectionMode.NoSelection)

        if result.bin_attempted:
            detail.addItem(QListWidgetItem(result.bin_detail))
        if result.files.failed_files:
            detail.addItem(
                QListWidgetItem(
                    f"Занято другим процессом и оставлено: "
                    f"{result.files.failed_files}"
                )
            )
        if result.files.refused_files:
            detail.addItem(
                QListWidgetItem(
                    f"Изменилось после проверки и оставлено: "
                    f"{result.files.refused_files}"
                )
            )
        for line in result.files.errors:
            detail.addItem(QListWidgetItem(f"ошибка: {line}"))
        if detail.count() == 0:
            detail.addItem(QListWidgetItem("Всё запланированное удалено."))
        layout.addWidget(detail, stretch=1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close_btn = buttons.button(QDialogButtonBox.StandardButton.Close)
        close_btn.setObjectName("Secondary")
        close_btn.setText("Закрыть")
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)
