from __future__ import annotations

from dataclasses import dataclass
from queue import Empty, SimpleQueue

from PySide6.QtCore import QObject, QPoint, QRunnable, QSize, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen, QPolygon

from .cache import DiskThumbnailCache, MemoryThumbnailCache, ThumbnailKey
from .formats import suffix_for_path
from .imaging import load_image_for_display, sanitize_display_error, thumbnail_skip_reason
from .models import ImageRecord


@dataclass(slots=True, frozen=True)
class ThumbnailRequest:
    key: ThumbnailKey
    path: str
    target_size: QSize


class ThumbnailTask(QRunnable):
    def __init__(
        self,
        request: ThumbnailRequest,
        memory_cache: MemoryThumbnailCache,
        disk_cache: DiskThumbnailCache,
        result_queue: SimpleQueue,
    ) -> None:
        super().__init__()
        self.request = request
        self.memory_cache = memory_cache
        self.disk_cache = disk_cache
        self.result_queue = result_queue
        self.setAutoDelete(True)

    def run(self) -> None:
        cached = self.memory_cache.get(self.request.key)
        if cached is not None:
            self.result_queue.put(("ready", self.request.key, cached))
            return

        disk_image = self.disk_cache.load(self.request.key)
        if disk_image is not None:
            self.memory_cache.put(self.request.key, disk_image)
            self.result_queue.put(("ready", self.request.key, disk_image))
            return

        skip_reason = thumbnail_skip_reason(self.request.path, self.request.target_size)
        if skip_reason:
            image = _placeholder_thumbnail(self.request.path, self.request.target_size, skip_reason)
            self.memory_cache.put(self.request.key, image)
            self.disk_cache.save(self.request.key, image)
            self.result_queue.put(("ready", self.request.key, image))
            return

        image, error = load_image_for_display(
            self.request.path,
            self.request.target_size,
            prefer_embedded=True,
        )
        if image.isNull():
            message = sanitize_display_error(error, path=self.request.path)
            image = _placeholder_thumbnail(self.request.path, self.request.target_size, message)
            self.memory_cache.put(self.request.key, image)
            self.disk_cache.save(self.request.key, image)
            self.result_queue.put(("ready", self.request.key, image))
            return

        if image.size().width() > self.request.target_size.width() or image.size().height() > self.request.target_size.height():
            image = image.scaled(
                self.request.target_size,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )

        self.memory_cache.put(self.request.key, image)
        self.disk_cache.save(self.request.key, image)
        self.result_queue.put(("ready", self.request.key, image))


class ThumbnailManager(QObject):
    thumbnail_ready = Signal(object, object)
    thumbnail_failed = Signal(object, str)
    RESULTS_PER_TICK = 8

    def __init__(
        self,
        memory_cache: MemoryThumbnailCache | None = None,
        disk_cache: DiskThumbnailCache | None = None,
        max_workers: int | None = None,
    ) -> None:
        super().__init__()
        self.memory_cache = memory_cache or MemoryThumbnailCache()
        self.disk_cache = disk_cache or DiskThumbnailCache()
        self.pool = QThreadPool(self)
        worker_count = max_workers or 2
        self.pool.setMaxThreadCount(worker_count)
        self._pending: set[ThumbnailKey] = set()
        self._result_queue: SimpleQueue = SimpleQueue()
        self._drain_timer = QTimer(self)
        self._drain_timer.setInterval(12)
        self._drain_timer.timeout.connect(self._drain_results)

    def make_key(self, record: ImageRecord, target_size: QSize) -> ThumbnailKey:
        return ThumbnailKey(
            path=record.path,
            modified_ns=record.modified_ns,
            file_size=record.size,
            width=target_size.width(),
            height=target_size.height(),
        )

    def get_cached(self, record: ImageRecord, target_size: QSize) -> QImage | None:
        return self.memory_cache.get(self.make_key(record, target_size))

    def request_thumbnail(self, record: ImageRecord, target_size: QSize, priority: int = 0) -> ThumbnailKey:
        key = self.make_key(record, target_size)
        cached = self.memory_cache.get(key)
        if cached is not None:
            return key

        if key in self._pending:
            return key

        self._pending.add(key)
        request = ThumbnailRequest(key=key, path=record.path, target_size=target_size)
        task = ThumbnailTask(request, self.memory_cache, self.disk_cache, self._result_queue)
        self.pool.start(task, priority)
        if not self._drain_timer.isActive():
            self._drain_timer.start()
        return key

    def _drain_results(self) -> None:
        processed = 0
        while processed < self.RESULTS_PER_TICK:
            try:
                state, key, payload = self._result_queue.get_nowait()
            except Empty:
                break

            self._pending.discard(key)
            if state == "ready":
                self.thumbnail_ready.emit(key, payload)
            else:
                self.thumbnail_failed.emit(key, payload)
            processed += 1

        if not self._pending and processed == 0:
            self._drain_timer.stop()


def _placeholder_thumbnail(path: str, target_size: QSize, message: str) -> QImage:
    width = max(96, target_size.width() if target_size.isValid() else 256)
    height = max(96, target_size.height() if target_size.isValid() else 192)
    image = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor("#202733"))

    suffix = suffix_for_path(path).lstrip(".").upper() or "FILE"
    headline = suffix[:5]
    detail = (message or "Preview unavailable").strip()

    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    margin = max(10, min(width, height) // 12)
    card = image.rect().adjusted(margin, margin, -margin, -margin)
    painter.setPen(QPen(QColor("#5b697b"), 2))
    painter.setBrush(QColor("#2b3442"))
    painter.drawRoundedRect(card, 8, 8)

    fold = max(18, min(card.width(), card.height()) // 5)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#394657"))
    painter.drawPolygon(
        QPolygon(
            [
                card.topRight(),
                QPoint(card.right() - fold, card.top()),
                QPoint(card.right(), card.top() + fold),
            ]
        )
    )
    painter.setPen(QPen(QColor("#6f7f92"), 1))
    painter.drawLine(card.right() - fold, card.top(), card.right(), card.top() + fold)
    painter.drawLine(card.right() - fold, card.top(), card.right() - fold, card.top() + fold)
    painter.drawLine(card.right() - fold, card.top() + fold, card.right(), card.top() + fold)

    painter.setPen(QColor("#d8e2ef"))
    title_font = QFont("Segoe UI", max(13, min(width, height) // 7), QFont.Weight.Bold)
    painter.setFont(title_font)
    painter.drawText(card.adjusted(8, 10, -8, -card.height() // 2), Qt.AlignmentFlag.AlignCenter, headline)

    painter.setPen(QColor("#aebbd0"))
    detail_font = QFont("Segoe UI", max(8, min(width, height) // 16))
    painter.setFont(detail_font)
    painter.drawText(
        card.adjusted(10, card.height() // 2 - 8, -10, -10),
        Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
        detail,
    )
    painter.end()
    return image
