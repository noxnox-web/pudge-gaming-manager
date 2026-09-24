"""Frozen-executable entry point for Pudge Cleaner.

PyInstaller bundles this module rather than the package's ``__main__``, so
its relative imports do not have to be resolved from a frozen ``__main__``.

Setting ``PGM_SELFTEST=1`` constructs the dashboard off-screen, pumps the
event loop briefly and exits 0. It is how the build is smoke-tested without a
UAC prompt or a visible window: if a hidden import is missing, Qt fails to
load, or a module was left out of the bundle, this exits non-zero.
"""

from __future__ import annotations

import os
import sys


def _selftest() -> int:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QTimer
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication

    from pudge_gaming_manager.app.gui.dashboard import Dashboard
    from pudge_gaming_manager.app.gui.resources import logo_path

    app = QApplication([])
    window = Dashboard()
    window.show()
    assert not QIcon(logo_path()).isNull(), "bundled logo failed to load"
    QTimer.singleShot(2500, app.quit)
    app.exec()
    window.close()
    print("PGM selftest OK")
    return 0


def main() -> int:
    if os.environ.get("PGM_SELFTEST") == "1":
        return _selftest()
    from pudge_gaming_manager.app.main import main as app_main

    return app_main()


if __name__ == "__main__":
    sys.exit(main())
