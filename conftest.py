"""Ensure the package is importable when running pytest from the repo root."""
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))


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
