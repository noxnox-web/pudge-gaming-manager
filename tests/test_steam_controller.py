"""Tests for the Steam reset controller and the window's wipe guard.

The wiper is faked, so no test plans, deletes or stops anything real. These
cover: a reset reads its keep-list from the chosen profile; ``wiping`` is true
only while games are being deleted and always clears; and the window refuses
to close mid-wipe.
"""

from __future__ import annotations

import json
import os
import pathlib
import threading

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QCoreApplication, QEventLoop, QTimer

from pudge_gaming_manager.app.controllers.steam_controller import SteamController


@pytest.fixture(scope="module")
def app() -> QCoreApplication:
    return QCoreApplication.instance() or QCoreApplication([])


def _wait(until) -> None:
    loop = QEventLoop()
    timer = QTimer()
    timer.timeout.connect(lambda: until() and loop.quit())
    timer.start(10)
    QTimer.singleShot(5000, loop.quit)
    loop.exec()
    timer.stop()


class _FakeWiper:
    def __init__(self) -> None:
        self.release = threading.Event()
        self.scanned_keep: set[int] | None = None

    def scan(self, keep_app_ids: set[int]):
        self.scanned_keep = keep_app_ids
        return f"plan(keep={sorted(keep_app_ids)})"

    def wipe(self, _plan, *, dry_run: bool = False):
        self.release.wait(5)
        return "result"


def _controller(wiper: _FakeWiper) -> SteamController:
    controller = SteamController()
    controller._wiper = wiper  # inject the fake
    return controller


def _profile(tmp_path: pathlib.Path, keep: list[int]) -> str:

    # Build a minimal valid profile file with a games section.
    from pudge_gaming_manager.core.profiles.schema import GamesPolicy, GoldenProfile

    profile = GoldenProfile(games=GamesPolicy(keep_steam_app_ids=tuple(keep)))
    path = tmp_path / "club.json"
    path.write_text(
        json.dumps(profile.model_dump(mode="json")), encoding="utf-8"
    )
    return str(path)


def test_plan_reads_the_keep_list_from_the_profile(app, tmp_path) -> None:
    wiper = _FakeWiper()
    controller = _controller(wiper)
    plans: list = []
    controller.planned.connect(plans.append)

    controller.start_plan(_profile(tmp_path, [730, 570]))
    _wait(lambda: plans)

    assert wiper.scanned_keep == {730, 570}
    assert plans == ["plan(keep=[570, 730])"]


def test_wiping_is_true_during_wipe_and_clears_after(app) -> None:
    wiper = _FakeWiper()
    controller = _controller(wiper)
    done: list = []
    controller.wiped.connect(done.append)

    controller.start_wipe("plan")
    assert controller.wiping is True

    wiper.release.set()
    _wait(lambda: done)
    assert controller.wiping is False
    assert done == ["result"]


def test_wiping_clears_when_the_wipe_fails(app) -> None:
    class _Boom(_FakeWiper):
        def wipe(self, _plan, *, dry_run: bool = False):
            raise RuntimeError("disk detached")

    controller = _controller(_Boom())
    errors: list = []
    controller.failed.connect(errors.append)
    controller.start_wipe("plan")
    _wait(lambda: errors)
    assert controller.wiping is False
    assert errors == ["disk detached"]


def test_close_event_is_refused_while_wiping(app, monkeypatch) -> None:
    from pudge_gaming_manager.app.gui.dashboard import Dashboard

    class _Event:
        def __init__(self) -> None:
            self.ignored = False

        def ignore(self) -> None:
            self.ignored = True

    wiper = _FakeWiper()
    controller = _controller(wiper)
    controller.start_wipe("plan")
    assert controller.wiping

    class _StubOptimizer:
        applying = False

    stub = type("Stub", (), {"_optimizer": _StubOptimizer(), "_steam": controller})()
    monkeypatch.setattr(
        "PySide6.QtWidgets.QMessageBox.warning", lambda *a, **k: None
    )
    event = _Event()
    Dashboard.closeEvent(stub, event)  # type: ignore[arg-type]
    assert event.ignored is True

    wiper.release.set()
    _wait(lambda: not controller.wiping)
    controller.shutdown()
