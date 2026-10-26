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
from PySide6.QtGui import QImage

from .. import memtrace
from ..cache import DiskThumbnailCache, ThumbnailKey
from ..imaging import load_image_for_display
from ..perf import perf_logger

_JOBS = memtrace.jobs("qml.thumbnail.decode")
BUCKET_ASPECT = 2 / 3
BUCKETS = (160, 240, 360, 540, 800)   # the sizes PhotoGrid.bucket() asks for
STRIP = 0.30                      # the bottom share of a thumbnail that a card's name, stars and format tag are drawn over


def strip_luma(image: QImage) -> float:
    """The mean brightness (0 black to 1 white, sRGB luma) of the bottom strip of `image`."""
    height = image.height()
    top = min(height - 1, int(height * (1 - STRIP)))
    small = image.copy(0, top, image.width(), height - top).scaled(16, 4, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
    small = small.convertToFormat(QImage.Format.Format_RGB32)
    total = 0.0
    for y in range(small.height()):
        for x in range(small.width()):
            pixel = small.pixel(x, y)
            total += 0.2126 * ((pixel >> 16) & 255) + 0.7152 * ((pixel >> 8) & 255) + 0.0722 * (pixel & 255)
    return total / (small.width() * small.height() * 255.0)


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
    painted = Signal()                     # a card's picture finished loading (the launch splash waits for the first few)

    def __init__(self, disk: DiskThumbnailCache | None = None, workers: int = 6, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.disk = disk or DiskThumbnailCache()
        self.pool = QThreadPool()
        self.pool.setMaxThreadCount(workers)
        self._lock = threading.Lock()
        self._wanted: dict[str, int] = {}      # path -> how many delegates currently want a thumbnail of it
        self._queued: set[ThumbnailKey] = set()
        self._stats: dict[str, tuple[int, int]] = {}      # normalised path -> (modified_ns, size), from the folder listings (see prime_folder)
        self._tones: dict[str, float] = {}      # cache file -> brightness of its bottom strip (the file name changes when the photo does)

    @staticmethod
    def size_for(bucket: int) -> QSize:
        return QSize(bucket, max(1, round(bucket * BUCKET_ASPECT)))

    @staticmethod
    def _norm(path: str) -> str:
        return os.path.normcase(os.path.normpath(path))

    def prime_folder(self, folder: str) -> None:
        """Read every file's modified time and size from one listing of ``folder`` (called on the scan thread). ``_key`` then needs no
        ``os.stat`` of its own, which on a network share cost ~2 ms a photo, on the GUI thread, every time a card was built."""
        from ..scanner import normalize_filesystem_path

        found: dict[str, tuple[int, int]] = {}
        base = normalize_filesystem_path(folder)       # the scanner records photos under this spelling (a mapped drive becomes its share)
        try:
            with os.scandir(folder) as entries:
                for entry in entries:
                    try:
                        if entry.is_file(follow_symlinks=False):
                            info = entry.stat(follow_symlinks=False)
                            found[self._norm(os.path.join(base, entry.name))] = (info.st_mtime_ns, info.st_size)
                    except OSError:
                        continue
        except OSError:
            return
        with self._lock:
            if len(self._stats) > 60000:
                self._stats.clear()
            self._stats.update(found)

    def _key(self, path: str, bucket: int) -> ThumbnailKey | None:
        norm = self._norm(path)
        known = self._stats.get(norm)
        if known is None:
            try:
                info = os.stat(path)
            except OSError:
                return None
            known = (info.st_mtime_ns, info.st_size)
            with self._lock:
                self._stats[norm] = known
        size = self.size_for(bucket)
        return ThumbnailKey(path=path, modified_ns=known[0], file_size=known[1], width=size.width(), height=size.height())

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
            fresh = key not in self._queued
            self._queued.add(key)
        if fresh:
            if _JOBS is not None:
                _JOBS.queued()
            # Newest request first: what is on screen now, not what was scrolled past.
            self.pool.start(_Task(self, key), int(time.monotonic() * 1000) & 0x7FFFFFFF)
        return self._stand_in(path, bucket, key)

    def _stand_in(self, path: str, bucket: int, key: ThumbnailKey) -> str:
        """The nearest size already cached, shown until the one asked for is made: changing the thumbnail size would otherwise blank
        every card until its new size had been decoded (seconds, on a NAS), and the cell redraws sharp when ``ready`` arrives."""
        for other in sorted((b for b in BUCKETS if b != bucket), key=lambda b: abs(b - bucket)):
            size = self.size_for(other)
            candidate = self.disk._file_path(ThumbnailKey(path=path, modified_ns=key.modified_ns, file_size=key.file_size, width=size.width(), height=size.height()))
            if candidate.exists():
                return QUrl.fromLocalFile(str(candidate)).toString()
        return ""

    @Slot(str, int, result=float)
    def tone(self, path: str, bucket: int) -> float:
        """How bright the bottom of the photo's cached thumbnail is, 0 (black) to 1 (white), or -1 while there is none. The card draws its
        name, stars and format tag over that strip and picks light or dark ink by this, so they can be read on a white photo too."""
        key = self._key(path, bucket)
        if key is None:
            return -1.0
        target = self.disk._file_path(key)
        name = str(target)
        known = self._tones.get(name)
        if known is not None:
            return known
        if not target.exists():
            return -1.0
        image = QImage(name)
        if image.isNull() or image.height() < 2:
            return -1.0
        if len(self._tones) > 50000:
            self._tones.clear()
        value = self._tones[name] = strip_luma(image)
        return value

    def cached_file(self, path: str, bucket: int) -> str:
        """The cached thumbnail's file if it exists, else "" (nothing is queued; `url` does that)."""
        key = self._key(path, bucket)
        if key is None:
            return ""
        target = self.disk._file_path(key)
        return str(target) if target.exists() else ""

    @Slot()
    def notePainted(self) -> None:
        self.painted.emit()

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
