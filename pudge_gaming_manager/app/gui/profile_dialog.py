"""The result of comparing this PC against a Golden Profile.

Read-only by design: drift detection reports and never corrects
(ARCHITECTURE.md §10). Restoring a profile is a separate, human action that
is not built yet, and each row says so rather than implying a fix exists.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
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

    def __init__(self, result: ComparisonResult, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Compare with Golden Profile")
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
            f"{result.checked} setting(s) checked. Nothing on this PC has "
            "been changed."
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
            rows.addItem(QListWidgetItem("No differences found."))
        for view in views:
            item = QListWidgetItem(
                f"{view.value}\n{view.detail}" if view.detail else view.value
            )
            item.setForeground(QBrush(QColor(theme.status_colour(view.status))))
            item.setToolTip(view.tooltip)
            rows.addItem(item)
        layout.addWidget(rows, stretch=1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setObjectName(
            "Secondary"
        )
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
