"""Running one blocking job off the UI thread.

The completion handlers are bound methods of a ``QObject`` that lives on the
UI thread, and that is load-bearing: PySide6 runs a plain function or
closure connected to a worker's signal *on the worker thread*. A closure
that then stopped the thread would wait on itself and destroy a running
``QThread``, which aborts the process. A bound slot is delivered queued, on
the thread that owns this object.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QObject, QThread, Signal, Slot

from .errors import readable_error


class _Job(QObject):
    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, work: Callable[[], object]) -> None:
        super().__init__()
        self._work = work

    def run(self) -> None:
        try:
            self.finished.emit(self._work())
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(readable_error(exc))


class BackgroundRunner(QObject):
    """Runs one job at a time and reports back on the UI thread."""

    failed = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._thread: QThread | None = None
        self._job: _Job | None = None
        self._on_done: Callable[[object], None] | None = None

    @property
    def busy(self) -> bool:
        return self._thread is not None and self._thread.isRunning()

    def start(
        self, work: Callable[[], object], on_done: Callable[[object], None]
    ) -> bool:
        """Run ``work`` on a worker thread; call ``on_done`` on this thread.

        Returns False, and does nothing, if a job is already running.
        """
        if self.busy:
            return False
        # Parented to self so Qt owns the QThread's lifetime. Nothing here
        # ever deletes a thread while its worker is still running — doing so
        # aborts the process, which is exactly the bug this guards against.
        thread = QThread(self)
        job = _Job(work)
        job.moveToThread(thread)
        thread.started.connect(job.run)
        job.finished.connect(self._finished)
        job.failed.connect(self._failed)

        # Held so neither is garbage-collected mid-run.
        self._thread, self._job, self._on_done = thread, job, on_done
        thread.start()
        return True

    @Slot(object)
    def _finished(self, payload: object) -> None:
        on_done = self._on_done
        # Reached after run() returned, so the worker loop is idle: quit()
        # and wait() below join it at once. shutdown() may have released the
        # thread already (window closing), in which case there is nothing to
        # join and on_done is stale — guard on that.
        if self._thread is None:
            return
        self._teardown()
        if on_done is not None:
            on_done(payload)

    @Slot(str)
    def _failed(self, message: str) -> None:
        if self._thread is None:
            return
        self._teardown()
        self.failed.emit(message)

    def _teardown(self) -> None:
        """Join the worker thread and drop it. Never called mid-run().

        Both completion slots reach here only after ``run()`` has returned,
        and ``shutdown`` blocks on ``wait()`` first, so ``quit`` is delivered
        to an idle worker loop and ``wait`` joins a finishing thread — the
        object is never destroyed while its worker is still running.
        """
        thread = self._thread
        self._thread = self._job = self._on_done = None
        if thread is not None:
            thread.quit()
            thread.wait()
            thread.deleteLater()

    def shutdown(self) -> None:
        """Block until the current job finishes, then release the thread.

        Never forces a running worker to stop: the jobs are read-mostly or
        already-committed writes, and each has its own internal timeout, so
        joining is bounded. ``quit`` is queued to the worker loop and acted
        on the instant ``run()`` returns; ``wait`` joins it. Abandoning a
        live thread instead would abort the process — the bug this avoids.
        """
        self._teardown()
