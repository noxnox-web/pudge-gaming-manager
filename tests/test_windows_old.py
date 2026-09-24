"""Tests for the Windows 11 windows.old removal.

No test deletes a real windows.old: the folder path and Windows-11 detection
are patched, and the takeown/icacls/rmdir steps go through a fake runner that
performs a plain recursive delete against a throwaway tree.
"""

from __future__ import annotations

import os
import pathlib
import subprocess

import pytest

from pudge_gaming_manager.core.optimization.tweak import Outcome, TweakContext
from pudge_gaming_manager.core.optimization.tweaks.windows_old import (
    RemoveWindowsOldTweak,
)
from pudge_gaming_manager.windows.cleanup import windows_old

windows_only = pytest.mark.skipif(os.name != "nt", reason="Windows-only behaviour")


class _FakeRunner:
    """Stands in for CommandRunner; the rmdir step really deletes the tree."""

    def __init__(self, do_delete: bool = True) -> None:
        self.calls: list[list[str]] = []
        self._do_delete = do_delete

    def try_run(self, args, *, timeout_s=None, **_kw):
        self.calls.append(list(args))
        if self._do_delete and args[:3] == ["cmd", "/c", "rmdir"]:
            import shutil

            shutil.rmtree(args[-1], ignore_errors=True)
        return None


def _fake_old(tmp_path: pathlib.Path, monkeypatch, present: bool = True) -> pathlib.Path:
    old = tmp_path / "Windows.old"
    if present:
        (old / "Windows" / "System32").mkdir(parents=True)
        (old / "Windows" / "System32" / "x.dll").write_bytes(b"x" * 2048)
    monkeypatch.setattr(windows_old, "windows_old_path", lambda: old)
    return old


# -- detection ---------------------------------------------------------------


def test_not_offered_off_windows_11(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(windows_old, "is_windows_11", lambda: False)
    _fake_old(tmp_path, monkeypatch)
    state = RemoveWindowsOldTweak().scan(TweakContext())
    assert state.needs_change is False
    assert "Windows 11" in state.summary


def test_not_offered_without_the_folder(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(windows_old, "is_windows_11", lambda: True)
    _fake_old(tmp_path, monkeypatch, present=False)
    state = RemoveWindowsOldTweak().scan(TweakContext())
    assert state.needs_change is False


def test_offered_on_windows_11_with_the_folder(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(windows_old, "is_windows_11", lambda: True)
    _fake_old(tmp_path, monkeypatch)
    state = RemoveWindowsOldTweak().scan(TweakContext())
    assert state.needs_change is True
    assert "windows.old" in state.summary


# -- removal -----------------------------------------------------------------


def test_apply_takes_ownership_then_removes(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(windows_old, "is_windows_11", lambda: True)
    old = _fake_old(tmp_path, monkeypatch)
    runner = _FakeRunner()
    tweak = RemoveWindowsOldTweak()

    result = tweak.apply(TweakContext(runner=runner), tweak.scan(TweakContext()))
    assert result.outcome is Outcome.SUCCESS
    assert not old.exists()
    # ownership is taken and rights granted before the delete
    steps = [c[0] for c in runner.calls]
    assert steps == ["takeown", "icacls", "cmd"]
    assert tweak.verify(TweakContext(), tweak.scan(TweakContext())).confirmed


def test_dry_run_deletes_nothing(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(windows_old, "is_windows_11", lambda: True)
    old = _fake_old(tmp_path, monkeypatch)
    runner = _FakeRunner()
    tweak = RemoveWindowsOldTweak()
    result = tweak.apply(TweakContext(runner=runner, dry_run=True), tweak.scan(TweakContext()))
    assert result.outcome is Outcome.SUCCESS
    assert old.exists() and runner.calls == []


def test_apply_reports_failure_if_folder_survives(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(windows_old, "is_windows_11", lambda: True)
    _fake_old(tmp_path, monkeypatch)
    runner = _FakeRunner(do_delete=False)  # commands run but nothing is removed
    tweak = RemoveWindowsOldTweak()
    result = tweak.apply(TweakContext(runner=runner), tweak.scan(TweakContext()))
    assert result.outcome is Outcome.FAILED


@windows_only
def test_a_junction_in_place_of_windows_old_is_refused(tmp_path, monkeypatch) -> None:
    """windows.old swapped for a junction must not be followed."""
    victim = tmp_path / "victim"
    victim.mkdir()
    (victim / "keep.dll").write_bytes(b"keep")
    old = tmp_path / "Windows.old"
    subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(old), str(victim)],
        check=True, capture_output=True,
    )
    monkeypatch.setattr(windows_old, "windows_old_path", lambda: old)
    assert windows_old.is_present() is False  # a reparse point is not "present"
    runner = _FakeRunner()
    assert windows_old.remove(runner=runner) is False
    assert runner.calls == []  # never even attempted
    assert (victim / "keep.dll").exists()
