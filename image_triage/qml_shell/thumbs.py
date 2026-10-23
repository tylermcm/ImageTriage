"""``image://thumbs/<path>``: thumbnails for the QML grid, from the application's own caches.

The provider reuses ``MemoryThumbnailCache`` and ``DiskThumbnailCache`` (so a thumbnail made by the Widgets grid is found here and
the reverse) and decodes with ``load_image_for_display`` like every other surface. Requests run on a small pool, newest first, and
a request whose delegate has gone away (``cancel()``) is dropped before it decodes. The images are handed to the scene graph as
texture factories and not kept by Qt (the QML ``Image`` uses ``cache: false``), so the only CPU copy is the one in the memory
cache, which is accounted by memtrace.
"""
from __future__ import annotations

import os
import threading
import time
from urllib.parse import unquote

from PySide6.QtCore import QRunnable, QSize, Qt, QThreadPool
from PySide6.QtGui import QImage
from PySide6.QtQuick import QQuickAsyncImageProvider, QQuickImageResponse, QQuickTextureFactory

from .. import memtrace
from ..cache import DiskThumbnailCache, MemoryThumbnailCache, ThumbnailKey
from ..imaging import load_image_for_display
from ..perf import perf_logger

_JOBS = memtrace.jobs("qml.thumbnail.decode")


class _Task(QRunnable):
    def __init__(self, response: "ThumbResponse") -> None:
        super().__init__()
        self.response = response
        self.setAutoDelete(True)

    def run(self) -> None:
        self.response.work()


class ThumbResponse(QQuickImageResponse):
    def __init__(self, provider: "ThumbProvider", path: str, size: QSize) -> None:
        super().__init__()
        self._provider = provider
        self._path = path
        self._size = size
        self._image = QImage()
        self._cancelled = False
        self._error = ""
        self._est = max(1, size.width()) * max(1, size.height()) * 4
        if _JOBS is not None:
            _JOBS.queued()

    # called on a pool thread
    def work(self) -> None:
        if _JOBS is not None:
            _JOBS.started(self._est)
        started = time.perf_counter()
        try:
            if not self._cancelled:
                self._image = self._provider.fetch(self._path, self._size)
                if _JOBS is not None and not self._image.isNull():
                    _JOBS.result(memtrace.image_bytes(self._image))
        except Exception as error:  # a decode failure shows the placeholder, not a crash
            self._error = str(error)
        finally:
            if _JOBS is not None:
                _JOBS.finished(self._est)
            logger = perf_logger()
            if logger.enabled:
                logger.duration("qml.thumbnail.task", (time.perf_counter() - started) * 1000.0, path=self._path,
                                cancelled=self._cancelled, width=self._size.width(), height=self._size.height())
            self.finished.emit()

    # QQuickImageResponse API
    def textureFactory(self) -> QQuickTextureFactory:
        return QQuickTextureFactory.textureFactoryForImage(self._image)

    def errorString(self) -> str:
        return self._error

    def cancel(self) -> None:
        self._cancelled = True


class ThumbProvider(QQuickAsyncImageProvider):
    def __init__(self, memory: MemoryThumbnailCache | None = None, disk: DiskThumbnailCache | None = None, workers: int = 3) -> None:
        super().__init__()
        self.memory = memory or MemoryThumbnailCache()
        self.disk = disk or DiskThumbnailCache()
        self.pool = QThreadPool()
        self.pool.setMaxThreadCount(workers)
        self._lock = threading.Lock()

    def requestImageResponse(self, image_id: str, requested_size: QSize) -> ThumbResponse:
        path = unquote(image_id)
        size = requested_size if requested_size.isValid() and requested_size.width() > 0 else QSize(256, 171)
        response = ThumbResponse(self, path, size)
        # Newest request first: the thumbnails the user is looking at now, not the ones they scrolled past.
        self.pool.start(_Task(response), int(time.monotonic() * 1000) & 0x7FFFFFFF)
        return response

    def fetch(self, path: str, size: QSize) -> QImage:
        try:
            stat = os.stat(path)
        except OSError:
            return QImage()
        key = ThumbnailKey(path=path, modified_ns=stat.st_mtime_ns, file_size=stat.st_size, width=size.width(), height=size.height())
        cached = self.memory.get(key)
        if cached is not None:
            return cached
        loaded = self.disk.load(key)
        if loaded is not None:
            self.memory.put(key, loaded)
            return loaded
        image, _error = load_image_for_display(path, size, prefer_embedded=True)
        if image.isNull():
            return QImage()
        if image.width() > size.width() or image.height() > size.height():
            image = image.scaled(size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        self.memory.put(key, image)
        self.disk.save(key, image)
        return image
