"""Per-photo results worked out off the GUI thread, for the inspector: the histogram and the quality checks.

One worker thread; a photo the person has already moved past is not read (flipping through a folder never queues work); results are kept per photo and
modification time, so an edited photo is counted again. `get(path)` is what QML reads and `ready(path)` says a photo's result has arrived.

The file itself is only ever asked about on the worker thread. `get` answers from what is kept, and `request` hands the photo to the worker, which looks
at its modification time and reads it again only when that has changed: a look at a file on a share that is asleep must not hold the window up, and both
are called on every change of the selection.
"""
from __future__ import annotations

import os
from collections import OrderedDict

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot

class _Task(QRunnable):
    def __init__(self, service: "LatestPhotoJobs", path: str) -> None:
        super().__init__()
        self.service, self.path = service, path
        self.setAutoDelete(True)

    def run(self) -> None:
        service = self.service
        if service._latest != self.path:                           # the person has moved on: nothing is read for a photo nobody is looking at
            service._skipped.emit(self.path)
            return
        try:
            modified = os.stat(self.path).st_mtime_ns              # a photo that was edited in place is counted again
        except OSError:
            service._gone.emit(self.path)                          # no such file (any more): nothing is kept for it
            return
        kept = service._cache.get(self.path)
        if kept is not None and kept[0] == modified:
            service._skipped.emit(self.path)                       # what is kept is still about this file
            return
        try:
            entry = service._loader(self.path)
        except Exception:
            entry = None
        service._done.emit(self.path, modified, entry)


class LatestPhotoJobs(QObject):
    ready = Signal(str)
    _done = Signal(str, object, object)
    _skipped = Signal(str)
    _gone = Signal(str)

    def __init__(self, loader, parent: QObject | None = None, cache_size: int = 96) -> None:
        super().__init__(parent)
        self._loader = loader
        self._cache_size = cache_size
        self._cache: "OrderedDict[str, tuple[int, dict | None]]" = OrderedDict()      # path -> (modified, entry); entry None: it could not be read
        self._latest = ""
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(1)
        self._pending: set[str] = set()
        self._done.connect(self._store)
        self._skipped.connect(self._forget)
        self._gone.connect(self._drop)

    @Slot(str, result="QVariantMap")
    def get(self, path: str) -> dict:
        """The photo's result if it has been worked out ({"ready": true, ...}), else {"ready": false}; "failed" is true for a photo that cannot be read."""
        kept = self._cache.get(path) if path else None
        if kept is None:
            return {"ready": False}
        entry = kept[1]
        if entry is None:
            return {"ready": False, "failed": True}
        self._cache.move_to_end(path)
        return dict(entry, ready=True)

    @Slot(str)
    def request(self, path: str) -> None:
        """Work this photo out (unless it already has been, or is being). A photo asked for and left before its turn is skipped."""
        self._latest = path
        if not path:
            return
        kept = self._cache.get(path)
        if kept is not None and kept[1] is not None:
            self.ready.emit(path)                                  # what is kept is shown at once; the worker says so again if the file has changed since
        if path in self._pending:
            return
        self._pending.add(path)
        self._pool.start(_Task(self, path))

    def _store(self, path: str, modified: int, entry: dict | None) -> None:
        self._pending.discard(path)
        self._cache[path] = (modified, entry)
        self._cache.move_to_end(path)
        while len(self._cache) > self._cache_size:
            self._cache.popitem(last=False)
        if entry is not None:
            self.ready.emit(path)

    def _forget(self, path: str) -> None:
        self._pending.discard(path)

    def _drop(self, path: str) -> None:
        self._pending.discard(path)
        self._cache.pop(path, None)

    def shutdown(self, wait_ms: int = 500) -> None:
        """The app is quitting: what is queued is dropped, and the read in flight gets a moment to end."""
        self._latest = ""
        self._pool.clear()
        self._pool.waitForDone(max(0, int(wait_ms)))
