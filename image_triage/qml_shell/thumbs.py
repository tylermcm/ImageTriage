"""Thumbnails for the QML grid, served as ``file://`` URLs from the application's disk thumbnail cache.

Why not an ``image://`` provider: Qt Quick decodes images on its own reader thread, and a provider written in Python makes that
thread wait for the GIL while it holds the pixmap-store lock. The GUI thread, running any Python code, can then block on that
same lock while holding the GIL, and the two deadlock (reproduced with a 100,000-photo fling; ``py-spy`` showed the main thread
in ``QQuickPixmap::load`` and the reader thread in the Python callback). So no Python runs on Qt's loader threads here:

* ``ThumbService.url(path, bucket)`` (called by QML on the GUI thread) returns the cached JPEG's ``file://`` URL when it exists,
  and Qt decodes it natively, scaled to ``sourceSize``;
* when it does not exist, the thumbnail is queued for a small Python pool (newest request first, dropped if its delegate has gone
  away), written to the same cache the Widgets grid uses, and ``ready(path)`` tells the delegate to look again.

Memory: Qt keeps the decoded pixmap only while a delegate shows it (its unreferenced-pixmap allowance is a few MB), so the CPU copy
that the Widgets grid keeps in ``MemoryThumbnailCache`` does not exist here; the disk cache is the store.
"""
from __future__ import annotations

import os
import threading
import time

from PySide6.QtCore import QObject, QRunnable, QSize, Qt, QThreadPool, QUrl, Signal, Slot

from .. import memtrace
from ..cache import DiskThumbnailCache, ThumbnailKey
from ..imaging import load_image_for_display
from ..perf import perf_logger

_JOBS = memtrace.jobs("qml.thumbnail.decode")
BUCKET_ASPECT = 2 / 3


class _Task(QRunnable):
    def __init__(self, service: "ThumbService", key: ThumbnailKey) -> None:
        super().__init__()
        self.service = service
        self.key = key
        self.setAutoDelete(True)

    def run(self) -> None:
        self.service._work(self.key)


class ThumbService(QObject):
    ready = Signal(str)

    def __init__(self, disk: DiskThumbnailCache | None = None, workers: int = 3, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.disk = disk or DiskThumbnailCache()
        self.pool = QThreadPool()
        self.pool.setMaxThreadCount(workers)
        self._lock = threading.Lock()
        self._wanted: dict[str, int] = {}      # path -> how many delegates currently want a thumbnail of it
        self._queued: set[ThumbnailKey] = set()

    @staticmethod
    def size_for(bucket: int) -> QSize:
        return QSize(bucket, max(1, round(bucket * BUCKET_ASPECT)))

    def _key(self, path: str, bucket: int) -> ThumbnailKey | None:
        try:
            stat = os.stat(path)
        except OSError:
            return None
        size = self.size_for(bucket)
        return ThumbnailKey(path=path, modified_ns=stat.st_mtime_ns, file_size=stat.st_size, width=size.width(), height=size.height())

    @Slot(str, int, result=str)
    def url(self, path: str, bucket: int) -> str:
        """The thumbnail's URL if it is cached, else an empty string (and the thumbnail is made in the background)."""
        key = self._key(path, bucket)
        if key is None:
            return ""
        with self._lock:
            self._wanted[path] = self._wanted.get(path, 0) + 1
        target = self.disk._file_path(key)
        if target.exists():
            return QUrl.fromLocalFile(str(target)).toString()
        with self._lock:
            if key in self._queued:
                return ""
            self._queued.add(key)
        if _JOBS is not None:
            _JOBS.queued()
        # Newest request first: what is on screen now, not what was scrolled past.
        self.pool.start(_Task(self, key), int(time.monotonic() * 1000) & 0x7FFFFFFF)
        return ""

    @Slot(str)
    def unwant(self, path: str) -> None:
        """A delegate stopped showing ``path`` (recycled or destroyed): its queued thumbnails are skipped unless another wants them."""
        with self._lock:
            count = self._wanted.get(path, 0) - 1
            if count > 0:
                self._wanted[path] = count
            else:
                self._wanted.pop(path, None)

    # -- worker thread ------------------------------------------------------------------------------------------------------
    def _work(self, key: ThumbnailKey) -> None:
        estimate = key.width * key.height * 4
        if _JOBS is not None:
            _JOBS.started(estimate)
        started = time.perf_counter()
        made = False
        try:
            with self._lock:
                wanted = key.path in self._wanted
            if wanted and not self.disk._file_path(key).exists():
                image, _error = load_image_for_display(key.path, QSize(key.width, key.height), prefer_embedded=True)
                if not image.isNull():
                    if image.width() != key.width or image.height() != key.height:
                        # Fill the cell exactly (the grid crops to its 3:2 cell anyway), so every cached file is the bucket's size.
                        image = image.scaled(key.width, key.height, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
                        left = max(0, (image.width() - key.width) // 2)
                        top = max(0, (image.height() - key.height) // 2)
                        image = image.copy(left, top, key.width, key.height)
                    if _JOBS is not None:
                        _JOBS.result(memtrace.image_bytes(image))
                    self.disk.save(key, image)
                    made = True
        except Exception:
            pass  # the cell keeps its placeholder
        finally:
            with self._lock:
                self._queued.discard(key)
            if _JOBS is not None:
                _JOBS.finished(estimate)
            logger = perf_logger()
            if logger.enabled:
                logger.duration("qml.thumbnail.task", (time.perf_counter() - started) * 1000.0, path=key.path, made=made,
                                width=key.width, height=key.height)
        if made:
            self.ready.emit(key.path)
