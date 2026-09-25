"""A read-only list of report lines, shared by the audit and the results.

Each row is text plus a colour; long notes wrap. The optional copy button
puts the whole report on the clipboard as plain text, which is how an
operator gets it into a chat with whoever maintains the club's PCs.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QGuiApplication
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
)

from . import theme


class ReportDialog(QDialog):
    def __init__(
        self,
        title: str,
        heading: str,
        rows: list[tuple[str, str]],
        *,
        subtitle: str = "",
        copy_text: str = "",
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumSize(820, 600)
        self.setStyleSheet(theme.stylesheet())

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(12)

        head = QLabel(heading)
        head.setObjectName("Title")
        head.setWordWrap(True)
        layout.addWidget(head)
        if subtitle:
            sub = QLabel(subtitle)
            sub.setObjectName("Subtitle")
            sub.setWordWrap(True)
            layout.addWidget(sub)

        listing = QListWidget()
        listing.setWordWrap(True)
        listing.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        listing.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        for text, colour in rows:
            item = QListWidgetItem(text)
            item.setForeground(QColor(colour or theme.TEXT))
            listing.addItem(item)
        layout.addWidget(listing, stretch=1)

        buttons = QDialogButtonBox()
        if copy_text:
            copy = buttons.addButton("Копировать отчёт", QDialogButtonBox.ButtonRole.ActionRole)
            copy.setObjectName("Secondary")
            copy.setAutoDefault(False)
            copy.clicked.connect(lambda: QGuiApplication.clipboard().setText(copy_text))
        close = buttons.addButton(QDialogButtonBox.StandardButton.Close)
        close.setText("Закрыть")
        close.setObjectName("Secondary")
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
