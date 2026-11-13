"""One file operation at a time, off the UI thread: progress, cancel between items, and the result handed back on the UI thread.

Delete, move, copy, their undo and the Safe Trash window all change files, which can take minutes on a card or a share. Each of them
gives its work to `FileJobs.start` as a function that runs on a worker thread; the window keeps painting, a card shows how far it is
(only when it takes longer than a moment, `SHOW_AFTER_MS`), and Cancel stops it between two items: the function looks at
`ctx.cancelled` before it starts the next photo, so the photo it is on is always finished or put back, never left half done.

Only one runs at a time. A second request is refused (`start` returns False) and the caller says so; two operations at once could
both be moving the same bundle, or writing the same trash manifest.

The work function must not touch the models: it returns plain data, and `done(result)` is called with it on the UI thread.
`wait()` processes events until the operation has ended, for the callers that must answer at once (Undo reports whether it worked)
and for tests; `shutdown()` is for quitting: it cancels and waits, however long the item in hand takes.
"""
from __future__ import annotations

import threading
import time

from PySide6.QtCore import Property, QCoreApplication, QEventLoop, QObject, QRunnable, QThreadPool, QTimer, Signal, Slot

SHOW_AFTER_MS = 300          # an operation that ends sooner than this never shows a card
_POLL_MS = 40                # how often the window looks at how far the worker is


class JobContext:
    """What the work function is given: whether to stop, and where to say how far it is."""

    def __init__(self) -> None:
        self._cancel = threading.Event()
        self.latest = (0, "", -1)                  # done, the item in hand, total (-1: as given at the start); read by the UI thread on a timer

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def step(self, done: int, name: str = "", total: int | None = None) -> None:
        """`done` items are finished and `name` is the one being worked on. Call it before each item and once at the end.
        It costs nothing to call for every item: the window reads the latest value when it next looks."""
        self.latest = (int(done), str(name), -1 if total is None else int(total))


class _Signals(QObject):
    finished = Signal(object)                      # the job


class _Job(QRunnable):
    def __init__(self, work, done, failed, signals: _Signals) -> None:
        super().__init__()
        self.setAutoDelete(False)                  # kept by FileJobs until its result has been handed over
        self.work, self.done, self.failed, self.signals = work, done, failed, signals
        self.context = JobContext()
        self.ended = threading.Event()
        self.result = None
        self.error = ""
        self.delivered = False

    def run(self) -> None:
        try:
            self.result = self.work(self.context)
        except Exception as exc:                   # whatever happens, the operation must end: nothing else could start until it did
            self.error = str(exc) or type(exc).__name__
        self.ended.set()
        try:
            self.signals.finished.emit(self)
        except RuntimeError:
            pass                                   # the application is gone; `shutdown` has collected the result itself


class FileJobs(QObject):
    busyChanged = Signal()
    progressChanged = Signal()

    def __init__(self, parent: QObject | None = None, show_after_ms: int = SHOW_AFTER_MS) -> None:
        super().__init__(parent)
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(1)
        self._signals = _Signals(self)
        self._signals.finished.connect(self._deliver)
        self._job: _Job | None = None
        self._view = {"title": "", "route": "", "done": 0, "total": 0, "name": "", "visible": False, "cancelling": False, "canCancel": True, "started": 0.0}
        self._show = QTimer(self)
        self._show.setSingleShot(True)
        self._show.setInterval(max(0, int(show_after_ms)))
        self._show.timeout.connect(self._reveal)
        self._poll = QTimer(self)
        self._poll.setInterval(_POLL_MS)
        self._poll.timeout.connect(self._read_progress)

    # ---------------- starting, stopping ----------------
    def start(self, title: str, work, done, *, route: str = "", total: int = 0, can_cancel: bool = True, failed=None) -> bool:
        """Run `work(ctx)` on the worker and call `done(result)` here when it ends (`failed(message)` instead when the work raised,
        which it should not: it reports what went wrong in its result). False, and nothing started, while another runs."""
        if self._job is not None:
            return False
        job = _Job(work, done, failed, self._signals)
        self._job = job
        self._view = {"title": title, "route": route, "done": 0, "total": max(0, int(total)), "name": "", "visible": False, "cancelling": False,
                      "canCancel": bool(can_cancel), "started": time.monotonic()}
        self._show.start()
        self._poll.start()
        self.busyChanged.emit()
        self.progressChanged.emit()
        self._pool.start(job)
        return True

    @Slot()
    def cancel(self) -> None:
        """Stop after the item in hand. What is already done stays done."""
        if self._job is None or not self._view["canCancel"] or self._view["cancelling"]:
            return
        self._job.context._cancel.set()
        self._view = dict(self._view, cancelling=True)
        self.progressChanged.emit()

    def wait(self, timeout_ms: int = -1) -> bool:
        """Process events until nothing is running. True when idle; False when `timeout_ms` passed first."""
        deadline = None if timeout_ms < 0 else time.monotonic() + timeout_ms / 1000.0
        while self._job is not None:
            job = self._job
            if job.ended.wait(0.005):
                self._deliver(job)                 # now, not when the queued signal gets here: the caller is waiting for the result
                continue
            QCoreApplication.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 10)
            if deadline is not None and time.monotonic() > deadline:
                return self._job is None
        return True

    def shutdown(self) -> None:
        """The application is closing: stop after the item in hand and hand over what was done, without taking new events."""
        while self._job is not None:
            job = self._job
            job.context._cancel.set()
            job.ended.wait()
            self._deliver(job)

    # ---------------- how far it is, and its end ----------------
    def _read_progress(self) -> None:
        if self._job is None:
            return
        done, name, total = self._job.context.latest
        if (done, name) != (self._view["done"], self._view["name"]) or (total >= 0 and total != self._view["total"]):
            self._view = dict(self._view, done=done, name=name, total=self._view["total"] if total < 0 else total)
            self.progressChanged.emit()

    def _reveal(self) -> None:
        if self._job is not None and not self._view["visible"]:
            self._view = dict(self._view, visible=True)
            self.progressChanged.emit()

    def _deliver(self, job) -> None:
        if job is not self._job or job.delivered or not job.ended.is_set():
            return
        job.delivered = True
        self._job = None
        self._show.stop()
        self._poll.stop()
        self._view = dict(self._view, visible=False, cancelling=False, name="")
        self.busyChanged.emit()
        self.progressChanged.emit()
        if job.error:
            if job.failed is not None:
                job.failed(job.error)
            return
        job.done(job.result)

    busy = Property(bool, lambda self: self._job is not None, notify=busyChanged)
    progress = Property("QVariantMap", lambda self: dict(self._view), notify=progressChanged)

    def view(self) -> dict:
        return self._view
