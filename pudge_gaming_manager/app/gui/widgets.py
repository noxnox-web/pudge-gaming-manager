"""Small building blocks shared by the dashboard's cards."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy, QWidget

from . import presenters, theme

_UNAVAILABLE = presenters.UNAVAILABLE


def card() -> QFrame:
    frame = QFrame()
    frame.setObjectName("Card")
    return frame


def label(text: str, object_name: str = "") -> QLabel:
    label = QLabel(text)
    if object_name:
        label.setObjectName(object_name)
    return label


class MetricRow(QWidget):
    """One measured value with a status dot."""

    def __init__(self, name: str) -> None:
        super().__init__()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 6, 0, 6)
        layout.setSpacing(12)

        self._dot = QLabel("●")
        self._dot.setFixedWidth(14)
        self._dot.setStyleSheet(f"color: {theme.UNAVAILABLE};")

        self._name = label(name, "MetricName")
        self._name.setMinimumWidth(110)

        self._value = label("—", "MetricValue")
        self._value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._value.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

        self._detail = label("", "MetricDetail")
        self._detail.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._detail.setMinimumWidth(150)

        layout.addWidget(self._dot)
        layout.addWidget(self._name)
        layout.addWidget(self._value)
        layout.addWidget(self._detail)

    def set(
        self, value: str, status: str = "GOOD", detail: str = "", tooltip: str = ""
    ) -> None:
        colour = theme.status_colour(status)
        self._dot.setStyleSheet(f"color: {colour};")
        self._value.setText(value)
        self._value.setStyleSheet(
            f"color: {theme.UNAVAILABLE};" if value == _UNAVAILABLE else ""
        )
        self._detail.setText(presenters.elide(detail))
        # The full text stays reachable even when the column elides it.
        self.setToolTip(tooltip or detail)
