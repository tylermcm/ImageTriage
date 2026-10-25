"""Per-photo results worked out off the GUI thread, for the inspector: the histogram and the quality checks.

One worker thread; a photo the person has already moved past is not read (flipping through a folder never queues work); results are kept per photo and
modification time, so an edited photo is counted again. `get(path)` is what QML reads and `ready(path)` says a photo's result has arrived.
"""
from __future__ import annotations

import os
from collections import OrderedDict

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot


class _Task(QRunnable):
    def __init__(self, service: "LatestPhotoJobs", path: str, key: tuple) -> None:
        super().__init__()
        self.service, self.path, self.key = service, path, key
        self.setAutoDelete(True)

    def run(self) -> None:
        service = self.service
        if service._latest != self.path:                           # the person has moved on: nothing is read for a photo nobody is looking at
            service._skipped.emit(self.path)
            return
        try:
            entry = service._loader(self.path)
        except Exception:
            entry = None
        service._done.emit(self.path, self.key, entry)


class LatestPhotoJobs(QObject):
    ready = Signal(str)
    _done = Signal(str, object, object)
    _skipped = Signal(str)

    def __init__(self, loader, parent: QObject | None = None, cache_size: int = 96) -> None:
        super().__init__(parent)
        self._loader = loader
        self._cache_size = cache_size
        self._cache: "OrderedDict[tuple, dict | None]" = OrderedDict()      # (path, modified) -> entry; None: it could not be read
        self._latest = ""
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(1)
        self._pending: set[tuple] = set()
        self._done.connect(self._store)
        self._skipped.connect(self._forget)

    @staticmethod
    def _key(path: str) -> tuple | None:
        try:
            return (path, os.stat(path).st_mtime_ns)               # a photo that was edited in place is counted again
        except OSError:
            return None

    @Slot(str, result="QVariantMap")
    def get(self, path: str) -> dict:
        """The photo's result if it has been worked out ({"ready": true, ...}), else {"ready": false}; "failed" is true for a photo that cannot be read."""
        key = self._key(path) if path else None
        if key is None or key not in self._cache:
            return {"ready": False}
        entry = self._cache[key]
        if entry is None:
            return {"ready": False, "failed": True}
        self._cache.move_to_end(key)
        return dict(entry, ready=True)

    @Slot(str)
    def request(self, path: str) -> None:
        """Work this photo out (unless it already has been, or is being). A photo asked for and left before its turn is skipped."""
        self._latest = path
        key = self._key(path) if path else None
        if key is None or key in self._cache or key in self._pending:
            if key is not None and key in self._cache and self._cache[key] is not None:
                self.ready.emit(path)
            return
        self._pending.add(key)
        self._pool.start(_Task(self, path, key))

    def _store(self, path: str, key: tuple, entry: dict | None) -> None:
        self._pending.discard(key)
        self._cache[key] = entry
        while len(self._cache) > self._cache_size:
            self._cache.popitem(last=False)
        if entry is not None:
            self.ready.emit(path)

    def _forget(self, path: str) -> None:
        self._pending = {k for k in self._pending if k[0] != path}
