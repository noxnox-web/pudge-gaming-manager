"""The result of comparing this PC against a Golden Profile.

Drift detection reports and never corrects by itself (ARCHITECTURE.md
§10). For the «Настройки Windows» section a separate, explicit button asks
for the correction; it goes through the same plan and confirmation as any
other change, and nothing is written from this dialog directly.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
)

from ...core.profiles.golden import ComparisonResult
from . import presenters, theme


class ComparisonDialog(QDialog):
    """Lists how this PC differs from the chosen profile."""

    fix_settings_requested = Signal(object)
    """Emits ``dict[str, str]``: setting id -> the profile's option."""

    def __init__(self, result: ComparisonResult, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Сравнение с профилем")
        self.setMinimumSize(640, 420)
        self.setStyleSheet(theme.stylesheet())

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(12)

        heading = QLabel(result.summary())
        heading.setObjectName("Title")
        heading.setWordWrap(True)
        heading.setStyleSheet(
            f"color: {theme.WARNING if result.drifted else theme.GOOD};"
        )
        layout.addWidget(heading)

        subtitle = QLabel(
            f"Проверено настроек: {result.checked}. На этом ПК ничего "
            "не изменено."
        )
        subtitle.setObjectName("Subtitle")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        rows = QListWidget()
        rows.setWordWrap(True)
        rows.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        rows.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        views = presenters.difference_views(result)
        if not views:
            rows.addItem(QListWidgetItem("Различий не найдено."))
        for view in views:
            item = QListWidgetItem(
                f"{view.value}\n{view.detail}" if view.detail else view.value
            )
            item.setForeground(QBrush(QColor(theme.status_colour(view.status))))
            item.setToolTip(view.tooltip)
            rows.addItem(item)
        layout.addWidget(rows, stretch=1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        if result.settings_fixes:
            fixes = dict(result.settings_fixes)
            fix = buttons.addButton(
                f"Привести настройки к профилю… ({len(fixes)})",
                QDialogButtonBox.ButtonRole.ActionRole,
            )
            fix.setObjectName("Primary")
            fix.setAutoDefault(False)
            fix.clicked.connect(lambda: (self.fix_settings_requested.emit(fixes), self.accept()))
        close_btn = buttons.button(QDialogButtonBox.StandardButton.Close)
        close_btn.setObjectName("Secondary")
        close_btn.setText("Закрыть")
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
