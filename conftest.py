"""Ensure the package is importable when running pytest from the repo root."""
import os
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))


@pytest.fixture(scope="session")
def app():
    """The one Qt application a test process may have.

    Shared because Qt allows a single application object per process and
    never replaces it: a controller test that made a ``QCoreApplication``
    first would leave the widget tests without the ``QApplication`` they
    need, and constructing a widget then kills the interpreter. A
    ``QApplication`` serves both. Offscreen, so no window ever appears.
    """
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _no_real_user_profiles(monkeypatch: pytest.MonkeyPatch) -> None:
    """Never let a test reach the real Windows user profiles.

    The Steam reset signs accounts out by deleting files in *every* user's
    profile. A test that runs a real wipe against a fake Steam tree would
    otherwise still delete this machine's real sign-in tokens (rule #48).
    A test that needs profiles patches in its own temporary ones.
    """
    from pudge_gaming_manager.games.steam import signout

    monkeypatch.setattr(signout, "_profile_dirs", lambda: [])
