"""The GUI's background runner must report back on the UI thread.

Regression: completion handlers used to be closures, which PySide6 runs on
the *worker* thread. Tearing the thread down from inside itself destroyed a
running QThread and aborted the process after every preview and save.
"""

from __future__ import annotations

import os
import threading

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QEventLoop, QTimer

from pudge_gaming_manager.app.controllers.background import BackgroundRunner



def _wait(until) -> None:
    """Spin the event loop until ``until()`` holds, so queued slots run."""
    loop = QEventLoop()
    timer = QTimer()
    timer.timeout.connect(lambda: until() and loop.quit())
    timer.start(10)
    QTimer.singleShot(5000, loop.quit)
    loop.exec()
    timer.stop()


def test_result_is_delivered_on_the_ui_thread(app) -> None:
    runner = BackgroundRunner()
    ui_thread = threading.get_ident()
    seen: list = []

    runner.start(lambda: 42, lambda value: seen.append((value, threading.get_ident())))
    _wait(lambda: seen)

    assert seen == [(42, ui_thread)]
    assert not runner.busy


def test_runner_is_reusable_after_a_job(app) -> None:
    runner = BackgroundRunner()
    results: list = []
    for value in (1, 2):
        assert runner.start(lambda v=value: v, results.append)
        _wait(lambda v=value: len(results) == v)
    assert results == [1, 2]


def test_failure_is_reported_as_readable_text(app) -> None:
    runner = BackgroundRunner()
    messages: list = []
    runner.failed.connect(messages.append)

    def boom() -> object:
        raise RuntimeError("disk vanished")

    runner.start(boom, lambda _: None)
    _wait(lambda: messages)
    assert messages == ["disk vanished"]


def test_second_job_is_refused_while_busy(app) -> None:
    runner = BackgroundRunner()
    gate = threading.Event()
    done: list = []
    assert runner.start(lambda: gate.wait(5), done.append)
    assert not runner.start(lambda: None, done.append)
    gate.set()
    _wait(lambda: done)
    assert done == [True]
