"""The confirmation step between planning and changing anything.

This dialog is the operator's decision point (rule #39). It shows the
complete list of planned changes with their risk levels, and what was
skipped and why. Nothing has been modified by the time it appears, and
nothing is modified unless it is accepted.

The default button is Cancel. A dialog that changes a club PC on a stray
Enter keypress is not a confirmation.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
)

from ...core.optimization.pipeline import OptimizationOutcome, OptimizationPreview
from ...core.optimization.tweak import BackupScope
from . import theme

#: Item data role holding a row's index into ``plan.changes``.
_CHANGE_INDEX = Qt.ItemDataRole.UserRole + 1

_RISK_COLOURS = {
    "SAFE": theme.GOOD,
    "LOW": theme.GOOD,
    "MEDIUM": theme.WARNING,
    "HIGH": theme.CRITICAL,
    "CRITICAL": theme.CRITICAL,
}


class PreviewDialog(QDialog):
    """Shows planned changes and asks for confirmation."""

    def __init__(self, preview: OptimizationPreview, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Проверка изменений")
        self.setMinimumSize(660, 460)
        self.setStyleSheet(theme.stylesheet())
        self._preview = preview

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(14)

        heading = QLabel(
            f"Запланировано изменений: {preview.change_count} — снимите галочку, чтобы пропустить"
            if preview.has_changes
            else "Изменения не нужны"
        )
        heading.setObjectName("Title")
        layout.addWidget(heading)

        irreversible = [
            c
            for c in preview.plan.applicable
            if c.tweak.scope is BackupScope.NONE
        ]
        subtitle = QLabel(
            "Пока ничего не изменено. Каждое обратимое изменение сначала "
            "сохраняется в резерв и откатывается автоматически, если не подтвердится."
            + (
                f"  Необратимых изменений ниже: {len(irreversible)} — "
                "каждое помечено."
                if irreversible
                else ""
            )
        )
        subtitle.setObjectName("Subtitle")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        self._list = QListWidget()
        self._list.setWordWrap(True)
        self._list.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self._list.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        layout.addWidget(self._list, stretch=1)
        self._populate()

        note = QLabel(
            "Применить изменение — значит обновить и проверить настройку. "
            "Это не измеренный прирост производительности."
        )
        note.setObjectName("ScoreNote")
        note.setWordWrap(True)
        layout.addWidget(note)

        buttons = QDialogButtonBox()
        self._apply = buttons.addButton(
            "Применить", QDialogButtonBox.ButtonRole.AcceptRole
        )
        self._apply.setObjectName("Primary")
        self._apply.setEnabled(preview.has_changes)
        cancel = buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        cancel.setText("Отмена")
        cancel.setObjectName("Secondary")
        # Cancel is the default: a stray Enter must not modify a club PC.
        cancel.setDefault(True)
        cancel.setAutoDefault(True)
        self._apply.setAutoDefault(False)

        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._list.itemChanged.connect(self._sync_apply)

    def selected_indices(self) -> set[int]:
        """Indices into ``plan.changes`` the operator left ticked."""
        chosen: set[int] = set()
        for row in range(self._list.count()):
            item = self._list.item(row)
            index = item.data(_CHANGE_INDEX)
            if index is not None and item.checkState() == Qt.CheckState.Checked:
                chosen.add(int(index))
        return chosen

    def selected_preview(self) -> OptimizationPreview:
        """The preview narrowed to what the operator ticked."""
        return self._preview.with_selection(self.selected_indices())

    def _sync_apply(self, _item: QListWidgetItem) -> None:
        self._apply.setEnabled(bool(self.selected_indices()))

    def _populate(self) -> None:
        for index, change in enumerate(self._preview.plan.changes):
            if not change.will_apply:
                continue
            risk = change.tweak.risk.value
            # An irreversible change must say so on its own row. Relying on
            # a general note at the top would let an operator approve a
            # deletion believing it could be undone.
            reversibility = (
                "  ·  НЕОБРАТИМО"
                if change.tweak.scope is BackupScope.NONE
                else ""
            )
            item = QListWidgetItem(
                f"[{risk}]{reversibility}  {change.summary}\n"
                f"{change.tweak.rationale}"
            )
            item.setToolTip(change.tweak.description)
            item.setForeground(Qt.GlobalColor.white)
            # Each change can be left out: fixing the display must not force
            # the operator to accept an irreversible cleanup alongside it.
            item.setFlags(
                Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable
            )
            item.setCheckState(Qt.CheckState.Checked)
            item.setData(_CHANGE_INDEX, index)
            self._list.addItem(item)
            colour = _RISK_COLOURS.get(risk, theme.TEXT)
            item.setData(Qt.ItemDataRole.UserRole, colour)

        for change in self._preview.plan.skipped:
            # Skipped entries are shown, not hidden: "why didn't it fix X?"
            # must be answerable from this screen.
            item = QListWidgetItem(
                f"[пропущено]  {change.summary}\n{change.skip_reason}"
            )
            item.setForeground(Qt.GlobalColor.gray)
            self._list.addItem(item)


class ResultDialog(QDialog):
    """The before/after report (rule #40)."""

    def __init__(self, outcome: OptimizationOutcome, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Оптимизация завершена")
        self.setMinimumSize(640, 460)
        self.setStyleSheet(theme.stylesheet())

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(12)

        heading = QLabel(
            "Оптимизация завершена"
            if outcome.run.failed == 0
            else "Оптимизация завершена с ошибками"
        )
        heading.setObjectName("Title")
        layout.addWidget(heading)

        summary = QHBoxLayout()
        summary.addWidget(
            _stat("Применено", str(outcome.run.applied), theme.GOOD)
        )
        summary.addWidget(
            _stat(
                "Откачено",
                str(outcome.run.rolled_back),
                theme.WARNING if outcome.run.rolled_back else theme.TEXT_MUTED,
            )
        )
        summary.addWidget(
            _stat(
                "Ошибок",
                str(outcome.run.failed),
                theme.CRITICAL if outcome.run.failed else theme.TEXT_MUTED,
            )
        )
        summary.addStretch(1)
        layout.addLayout(summary)

        detail = QListWidget()
        detail.setWordWrap(True)
        detail.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        detail.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        for line in outcome.report_lines():
            if line:
                detail.addItem(QListWidgetItem(line))
        for note in outcome.notes:
            detail.addItem(QListWidgetItem(note))
        layout.addWidget(detail, stretch=1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close_btn = buttons.button(QDialogButtonBox.StandardButton.Close)
        close_btn.setObjectName("Secondary")
        close_btn.setText("Закрыть")
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)


def _stat(label: str, value: str, colour: str) -> QLabel:
    widget = QLabel(f"{value}\n{label}")
    widget.setAlignment(Qt.AlignmentFlag.AlignCenter)
    widget.setStyleSheet(
        f"color: {colour}; font-size: 20px; font-weight: 700; "
        f"padding: 10px 22px; border: 1px solid {theme.BORDER}; "
        "border-radius: 8px;"
    )
    return widget
