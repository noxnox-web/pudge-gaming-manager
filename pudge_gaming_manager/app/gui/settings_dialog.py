"""«Настройки Windows»: every catalogue setting, its state, and a choice.

Nothing is pre-selected. Each row opens on «не менять», shows what is set
now, what is recommended (or that nothing is, and why), the risk, when the
change takes effect, which builds and hardware it applies to, and how it is
undone. The operator picks a value only for the rows they mean to change.

Choosing is not applying: «Проверить изменения…» plans the selection and
shows it in :class:`PlanConfirmDialog`, whose default button is Cancel.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ...core.optimization.tweak import BackupScope
from ...core.settings.catalog import Tier
from ...core.settings.service import SettingsView
from . import theme

_KEEP = "__keep__"

_TIER_NOTES = {
    Tier.RECOMMENDED: (
        "С этими значениями согласны все источники. ОПТИМИЗИРОВАТЬ ПК "
        "предлагает их сам; здесь их можно выставить и вернуть вручную."
    ),
    Tier.ADVANCED: (
        "Реальные настройки, правильное значение которых зависит от ПК или "
        "по которым источники спорят. Ничего не выбрано за вас."
    ),
    Tier.EXPERIMENTAL: (
        "Документированный механизм, спорный или зависящий от железа эффект. "
        "Не входят ни в один пресет. Меняйте, только если готовы сравнить "
        "замером до и после."
    ),
}


def _small(text: str, colour: str = theme.TEXT_MUTED) -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    label.setStyleSheet(f"color: {colour}; font-size: 12px;")
    return label


class SettingsDialog(QDialog):
    """Shows every setting; emits the operator's choices for planning."""

    plan_requested = Signal(object, object)
    """``(choices: dict[str, str], device_ids: set[str])``."""

    def __init__(self, view: SettingsView, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Настройки Windows")
        self.setMinimumSize(900, 680)
        self.setStyleSheet(theme.stylesheet())
        self._combos: dict[str, QComboBox] = {}
        self._devices: dict[str, QCheckBox] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(12)
        title = QLabel("Настройки Windows")
        title.setObjectName("Title")
        layout.addWidget(title)
        subtitle = QLabel(
            "Ничего не выбрано заранее. Выберите значение только там, где "
            "хотите изменить. Перед применением будет показан план; старое "
            "значение каждой настройки сохраняется, и её можно вернуть из "
            "«Истории изменений»."
        )
        subtitle.setObjectName("Subtitle")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        body = QWidget()
        column = QVBoxLayout(body)
        column.setSpacing(10)
        for tier in Tier:
            states = [s for s in view.states if s.spec.tier is tier]
            if not states:
                continue
            column.addWidget(self._heading(tier.label, _TIER_NOTES[tier]))
            for state in states:
                column.addWidget(self._setting_card(state))
        if view.device_changes:
            column.addWidget(
                self._heading(
                    "Устройства и сеть",
                    "Действуют на несколько устройств сразу. Для каждого "
                    "сохраняется прежнее состояние, и откат возвращает именно его.",
                )
            )
            for change in view.device_changes:
                column.addWidget(self._device_card(change))
        column.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(body)
        layout.addWidget(scroll, stretch=1)

        buttons = QDialogButtonBox()
        self._plan = buttons.addButton(
            "Проверить изменения…", QDialogButtonBox.ButtonRole.ActionRole
        )
        self._plan.setObjectName("Primary")
        self._plan.setAutoDefault(False)
        self._plan.clicked.connect(self._on_plan)
        close = buttons.addButton(QDialogButtonBox.StandardButton.Close)
        close.setText("Закрыть")
        close.setObjectName("Secondary")
        close.setDefault(True)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._sync()

    # -- construction ------------------------------------------------------

    @staticmethod
    def _heading(title: str, note: str) -> QWidget:
        box = QWidget()
        col = QVBoxLayout(box)
        col.setContentsMargins(0, 10, 0, 0)
        head = QLabel(title.upper())
        head.setObjectName("SectionHeading")
        col.addWidget(head)
        col.addWidget(_small(note))
        return box

    def _setting_card(self, state) -> QFrame:
        spec = state.spec
        card = QFrame()
        card.setObjectName("Card")
        col = QVBoxLayout(card)
        col.setContentsMargins(14, 10, 14, 10)

        row = QHBoxLayout()
        name = QLabel(spec.name)
        name.setStyleSheet("font-weight: 600;")
        row.addWidget(name, stretch=1)
        combo = QComboBox()
        combo.addItem(f"не менять (сейчас: {state.current_label})", _KEEP)
        for option in spec.options:
            label = option.label
            if option.key == spec.recommended:
                label += " — рекомендуется"
            if option.key == state.current_key and not state.absent:
                label += " (текущее)"
            combo.addItem(label, option.key)
        combo.setEnabled(state.available)
        combo.currentIndexChanged.connect(self._sync)
        self._combos[spec.id] = combo
        row.addWidget(combo)
        col.addLayout(row)

        col.addWidget(_small(spec.what, theme.TEXT))
        col.addWidget(_small(spec.why))
        if spec.conflict:
            col.addWidget(_small(spec.conflict, theme.WARNING))
        facts = [
            f"Рекомендуется: {state.recommended_label}",
            f"Риск: {spec.risk.value}",
            f"Действует: {spec.activation.label}",
            f"Windows: {'сборка ' + str(spec.min_build) + '+' if spec.min_build else 'любая'}",
            f"Железо: {spec.hardware}",
            "Откат: старое значение сохраняется",
        ]
        col.addWidget(_small(" · ".join(facts), theme.TEXT_FAINT))
        if not state.available:
            col.addWidget(_small(f"Недоступно: {state.unavailable_reason}", theme.WARNING))
        if state.read_error:
            col.addWidget(_small(f"Не прочитано: {state.read_error}", theme.WARNING))
        return card

    def _device_card(self, change) -> QFrame:
        tweak = change.tweak
        card = QFrame()
        card.setObjectName("Card")
        col = QVBoxLayout(card)
        col.setContentsMargins(14, 10, 14, 10)
        box = QCheckBox(tweak.name)
        box.setStyleSheet("font-weight: 600;")
        box.setEnabled(change.will_apply)
        box.toggled.connect(self._sync)
        self._devices[tweak.id] = box
        col.addWidget(box)
        col.addWidget(_small(change.summary, theme.TEXT))
        col.addWidget(_small(tweak.rationale))
        reversible = "старое состояние сохраняется" if tweak.scope is not BackupScope.NONE else "НЕОБРАТИМО"
        col.addWidget(
            _small(f"Риск: {tweak.risk.value} · Нужны права администратора · Откат: {reversible}", theme.TEXT_FAINT)
        )
        if not change.will_apply and change.skip_reason:
            col.addWidget(_small(f"Сейчас не применяется: {change.skip_reason}", theme.TEXT_FAINT))
        return card

    # -- behaviour ---------------------------------------------------------

    def choices(self) -> dict[str, str]:
        return {
            spec_id: combo.currentData()
            for spec_id, combo in self._combos.items()
            if combo.isEnabled() and combo.currentData() not in (None, _KEEP)
        }

    def device_ids(self) -> set[str]:
        return {tid for tid, box in self._devices.items() if box.isChecked()}

    def _sync(self, *_args) -> None:
        self._plan.setEnabled(bool(self.choices() or self.device_ids()))

    def _on_plan(self) -> None:
        self.plan_requested.emit(self.choices(), self.device_ids())

    def set_busy(self, busy: bool) -> None:
        self._plan.setEnabled(not busy and bool(self.choices() or self.device_ids()))


class PlanConfirmDialog(QDialog):
    """The exact changes about to be made. Cancel is the default."""

    def __init__(self, plan, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Проверка изменений")
        self.setMinimumSize(700, 460)
        self.setStyleSheet(theme.stylesheet())
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        applicable = plan.applicable
        head = QLabel(
            f"Будет изменено: {len(applicable)}" if applicable else "Изменять нечего"
        )
        head.setObjectName("Title")
        layout.addWidget(head)
        sub = QLabel(
            "Перед применением создаётся точка восстановления Windows (если "
            "Windows это разрешит), а старое значение каждой настройки "
            "сохраняется. После записи значение перечитывается; если оно не "
            "подтвердилось — изменение откатывается автоматически."
        )
        sub.setObjectName("Subtitle")
        sub.setWordWrap(True)
        layout.addWidget(sub)

        listing = QListWidget()
        listing.setWordWrap(True)
        listing.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        for change in applicable:
            restart = " · после перезагрузки" if getattr(change.tweak, "restart_required", False) else ""
            item = QListWidgetItem(f"[{change.tweak.risk.value}]{restart}  {change.summary}")
            listing.addItem(item)
        for change in plan.skipped:
            item = QListWidgetItem(f"[пропущено]  {change.summary}\n{change.skip_reason}")
            item.setForeground(Qt.GlobalColor.gray)
            listing.addItem(item)
        layout.addWidget(listing, stretch=1)

        buttons = QDialogButtonBox()
        apply = buttons.addButton("Применить", QDialogButtonBox.ButtonRole.AcceptRole)
        apply.setObjectName("Primary")
        apply.setAutoDefault(False)
        apply.setEnabled(bool(applicable))
        cancel = buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        cancel.setText("Отмена")
        cancel.setObjectName("Secondary")
        cancel.setDefault(True)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
