"""Dark theme for the administrator interface.

Design constraints (rule #69): dark, modern, compact, strong typography,
clear status indicators, no decorative colour. An administrator glancing at a
club PC should read its state in under a second, so status colour is reserved
exclusively for status — never for branding or emphasis.
"""

from __future__ import annotations

# -- palette ---------------------------------------------------------------

BACKGROUND = "#0e1116"
SURFACE = "#161b22"
SURFACE_RAISED = "#1c2230"
BORDER = "#2a313c"

TEXT = "#e6edf3"
TEXT_MUTED = "#8b949e"
TEXT_FAINT = "#6e7681"

ACCENT = "#3b82f6"
ACCENT_HOVER = "#60a5fa"
ACCENT_PRESSED = "#2563eb"

GOOD = "#3fb950"
WARNING = "#d29922"
CRITICAL = "#f85149"
UNAVAILABLE = "#6e7681"
"""Grey, deliberately. An unreadable value is not a fault in the PC and must
not look like one."""

STATUS_COLOURS: dict[str, str] = {
    "GOOD": GOOD,
    "WARNING": WARNING,
    "CRITICAL": CRITICAL,
    "UNAVAILABLE": UNAVAILABLE,
}

FONT_STACK = '"Segoe UI Variable Display", "Segoe UI", system-ui, sans-serif'
MONO_STACK = '"Cascadia Mono", "Consolas", monospace'


def stylesheet() -> str:
    """Return the application-wide Qt stylesheet."""
    return f"""
    QWidget {{
        background-color: {BACKGROUND};
        color: {TEXT};
        font-family: {FONT_STACK};
        font-size: 14px;
    }}

    /* Labels sit inside cards, which are lighter than the window. Without
       this they inherit the window background and render as dark boxes. */
    QLabel {{
        background: transparent;
    }}

    QLabel#Title {{
        font-size: 22px;
        font-weight: 700;
        letter-spacing: 0.5px;
    }}
    QLabel#Subtitle {{
        color: {TEXT_MUTED};
        font-size: 13px;
    }}
    /* The author credit, set on the title's baseline. Deliberately small
       and faint: it is a signature, not a second heading. */
    QLabel#Byline {{
        color: {TEXT_FAINT};
        font-size: 11px;
        font-style: italic;
        padding-bottom: 3px;
    }}
    QLabel#SectionHeading {{
        color: {TEXT_MUTED};
        font-size: 11px;
        font-weight: 700;
        letter-spacing: 1.4px;
    }}

    QFrame#Card {{
        background-color: {SURFACE};
        border: 1px solid {BORDER};
        border-radius: 10px;
    }}
    QFrame#Divider {{
        background-color: {BORDER};
        max-height: 1px;
        border: none;
    }}

    QLabel#MetricName   {{ color: {TEXT_MUTED}; font-size: 13px; }}
    QLabel#MetricValue  {{ font-size: 15px; font-weight: 600; }}
    QLabel#MetricDetail {{ color: {TEXT_FAINT}; font-size: 12px; }}

    QLabel#ScoreValue {{
        font-size: 56px;
        font-weight: 800;
        letter-spacing: -1px;
    }}
    QLabel#ScoreCaption {{
        color: {TEXT_MUTED};
        font-size: 12px;
        letter-spacing: 1.2px;
        font-weight: 700;
    }}
    QLabel#ScoreNote {{
        color: {TEXT_FAINT};
        font-size: 11px;
    }}

    QPushButton#Primary {{
        background-color: {ACCENT};
        color: #ffffff;
        border: none;
        border-radius: 8px;
        padding: 14px 28px;
        font-size: 15px;
        font-weight: 700;
        letter-spacing: 0.6px;
    }}
    QPushButton#Primary:hover    {{ background-color: {ACCENT_HOVER}; }}
    QPushButton#Primary:pressed  {{ background-color: {ACCENT_PRESSED}; }}
    QPushButton#Primary:disabled {{
        background-color: {SURFACE_RAISED};
        color: {TEXT_FAINT};
    }}

    QPushButton#Secondary {{
        background-color: transparent;
        color: {TEXT};
        border: 1px solid {BORDER};
        border-radius: 8px;
        padding: 10px 18px;
        font-size: 13px;
    }}
    QPushButton#Secondary:hover    {{ border-color: {ACCENT}; color: {ACCENT_HOVER}; }}
    QPushButton#Secondary:disabled {{ color: {TEXT_FAINT}; border-color: {BORDER}; }}

    QListWidget {{
        background-color: transparent;
        border: none;
        outline: none;
    }}
    QListWidget::item {{
        background-color: {SURFACE_RAISED};
        border: 1px solid {BORDER};
        border-radius: 8px;
        padding: 10px 12px;
        margin-bottom: 6px;
    }}
    QListWidget::item:selected {{
        border-color: {ACCENT};
        color: {TEXT};
    }}

    QProgressBar {{
        background-color: {SURFACE_RAISED};
        border: none;
        border-radius: 3px;
        height: 6px;
        text-align: center;
        color: transparent;
    }}
    QProgressBar::chunk {{
        background-color: {ACCENT};
        border-radius: 3px;
    }}

    QScrollBar:vertical {{
        background: transparent;
        width: 10px;
        margin: 0;
    }}
    QScrollBar::handle:vertical {{
        background: {BORDER};
        border-radius: 5px;
        min-height: 30px;
    }}
    QScrollBar::handle:vertical:hover {{ background: {TEXT_FAINT}; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

    QScrollBar:horizontal {{
        background: transparent;
        height: 10px;
        margin: 0;
    }}
    QScrollBar::handle:horizontal {{
        background: {BORDER};
        border-radius: 5px;
        min-width: 30px;
    }}
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}

    QToolTip {{
        background-color: {SURFACE_RAISED};
        color: {TEXT};
        border: 1px solid {BORDER};
        padding: 6px 8px;
        border-radius: 6px;
    }}
    """


def status_colour(status: str) -> str:
    return STATUS_COLOURS.get(status, UNAVAILABLE)


def score_colour(value: float | None) -> str:
    """Colour for the headline score. Grey when there is no score."""
    if value is None:
        return UNAVAILABLE
    if value >= 80:
        return GOOD
    if value >= 55:
        return WARNING
    return CRITICAL
