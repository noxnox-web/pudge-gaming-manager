"""Application entry point."""

from __future__ import annotations

import sys

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from ..utilities.logging_setup import setup_logging
from .gui.dashboard import Dashboard
from .gui.resources import logo_path


def main(argv: list[str] | None = None) -> int:
    """Start the administrator GUI."""
    setup_logging()

    app = QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName("Pudge Gaming Manager")
    app.setApplicationVersion("1.0.0-dev")
    app.setWindowIcon(QIcon(logo_path()))

    window = Dashboard()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
