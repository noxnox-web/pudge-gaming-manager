"""Widget behaviour that a presenter test cannot reach.

Runs on Qt's offscreen platform, so no window appears and no display is
needed.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QFocusEvent
from PySide6.QtWidgets import QApplication, QPushButton

from pudge_gaming_manager.app.gui.widgets import ElidedLabel, KeyboardFocusRing
from pudge_gaming_manager.hardware.models import Reading



def test_detail_is_elided_to_the_width_it_gets(app: QApplication) -> None:
    label = ElidedLabel("MetricDetail")
    label.set_full_text("Ethernet · роутер <1 мс · через DurevVPN")
    label.show()  # a hidden widget gets no resize events
    label.resize(80, 20)
    assert label.text().endswith("…")
    # The start survives: an operator reads a row left to right.
    assert label.text().startswith("Eth")

    label.resize(1000, 20)
    assert label.text() == "Ethernet · роутер <1 мс · через DurevVPN"


@pytest.mark.parametrize(
    ("reason", "ring"),
    [
        (Qt.FocusReason.TabFocusReason, True),
        (Qt.FocusReason.BacktabFocusReason, True),
        (Qt.FocusReason.MouseFocusReason, False),
        (Qt.FocusReason.ActiveWindowFocusReason, False),
    ],
)
def test_focus_ring_only_for_keyboard_focus(
    app: QApplication, reason: Qt.FocusReason, ring: bool
) -> None:
    button = QPushButton("x")
    rings = KeyboardFocusRing()
    rings.eventFilter(button, QFocusEvent(QEvent.Type.FocusIn, reason))
    assert bool(button.property("kbfocus")) is ring

    rings.eventFilter(
        button, QFocusEvent(QEvent.Type.FocusOut, Qt.FocusReason.MouseFocusReason)
    )
    assert not button.property("kbfocus")


def test_units_render_in_the_ui_language() -> None:
    assert Reading(2400, unit=" MHz").display() == "2400 МГц"
    assert Reading(57.4, unit=" GB").display() == "57 ГБ"
    assert Reading(61.0, unit="°C").display() == "61°C"
    assert Reading.unavailable("no sensor", unit=" W").display() == "нет данных"
