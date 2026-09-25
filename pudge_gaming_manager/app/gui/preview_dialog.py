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
    """Shows planned changes and asks for confirmation.

    MEDIUM and above are not planned by default, so they arrive here as
    skipped rows. When any of them was skipped *only* for its risk class,
    a second button offers to re-plan with that class allowed. Closing with
    :data:`SHOW_MEDIUM` is the dashboard's cue to do so.

    Allowing the class is not the same as choosing the change. After the
    re-plan those rows are applicable but **unticked**, so approving one
    still takes a deliberate click (rule #38).
    """

    #: Result code meaning "re-plan, this time including MEDIUM".
    SHOW_MEDIUM = QDialog.DialogCode.Accepted + 1

    def __init__(
        self, preview: OptimizationPreview, parent=None, snapshot=None
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Проверка изменений")
        self.setMinimumSize(660, 460)
        self.setStyleSheet(theme.stylesheet())
        self._preview = preview

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(14)

        heading = QLabel(
            f"Запланировано изменений: {preview.change_count} — снимите "
            "галочку, чтобы пропустить"
            if preview.has_changes
            else "Изменения не нужны"
        )
        heading.setObjectName("Title")
        heading.setWordWrap(True)
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

        # What the plan was computed for, and what it deliberately leaves
        # alone: the operator should not have to wonder whether HAGS or
        # Defender were touched because they are absent from the list.
        if snapshot is not None:
            detected = QLabel(f"Обнаружено: {hardware_line(snapshot)}")
            detected.setObjectName("ScoreNote")
            detected.setWordWrap(True)
            layout.addWidget(detected)
        untouched = QLabel(
            "Не меняется здесь: HAGS и игровой режим — только вручную в "
            "«Настройки Windows»; Защитник, Центр обновления, HPET, режим MSI "
            "и привязка к ядрам — никогда (причины — в «Аудит системы»)."
        )
        untouched.setObjectName("ScoreNote")
        untouched.setWordWrap(True)
        layout.addWidget(untouched)

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

        # Offered only when something is waiting behind the risk gate, and
        # only while it is still shut: a plan already built with MEDIUM
        # allowed has nothing further to unlock.
        blocked = preview.plan.blocked_by_risk
        if blocked and not preview.allow_risk_above_low:
            show_medium = buttons.addButton(
                f"Показать изменения MEDIUM ({len(blocked)})",
                QDialogButtonBox.ButtonRole.ActionRole,
            )
            show_medium.setObjectName("Secondary")
            show_medium.setAutoDefault(False)
            show_medium.clicked.connect(lambda: self.done(self.SHOW_MEDIUM))
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
        # A plan consisting only of MEDIUM changes opens with nothing
        # ticked, and "Apply" must not be live until something is.
        self._apply.setEnabled(bool(self.selected_indices()))

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
            # SAFE and LOW start ticked; anything above starts unticked.
            # The operator asked to *see* this class, which is not the same
            # as asking to apply a particular change in it.
            item.setCheckState(
                Qt.CheckState.Checked
                if change.tweak.risk.auto_applicable
                else Qt.CheckState.Unchecked
            )
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


def hardware_line(snapshot) -> str:
    """One line naming the machine the plan was built for."""
    parts = []
    if snapshot.cpu.model:
        parts.append(snapshot.cpu.model.strip())
    for gpu in snapshot.gpus:
        driver = f", драйвер {gpu.driver_version}" if gpu.driver_version else ""
        parts.append(f"{gpu.model}{driver}")
    ram = snapshot.ram.total_mb.value
    if ram:
        parts.append(f"{ram / 1024:.0f} ГБ ОЗУ")
    if snapshot.os.caption:
        parts.append(f"{snapshot.os.caption} (сборка {snapshot.os.build})")
    return " · ".join(parts) or "нет данных сканирования"


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
