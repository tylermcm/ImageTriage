"""What the grid and the viewer show about a video clip: its length ("0:34" on its card), size and frame rate.

Known clips answer at once (``video.info_cache``, filled by the thumbnail grab and kept between runs). An unknown one is probed on a
worker, two at a time, and ``revision`` goes up when its answer is in, so the cards showing it look again.
"""
from __future__ import annotations

import os
import threading

from PySide6.QtCore import Property, QObject, QRunnable, QThreadPool, QTimer, Signal, Slot

from ..video import format_duration, info_cache, probe


class _Probe(QRunnable):
    def __init__(self, service: "ClipService", path: str) -> None:
        super().__init__()
        self.service, self.path = service, path

    def run(self) -> None:
        try:
            info = probe(self.path)
        except Exception:
            info = None
        self.service._probed.emit(self.path, info)


class ClipService(QObject):
    revisionChanged = Signal()
    _probed = Signal(str, object)

    def __init__(self, parent: QObject | None = None, memory: bool = False) -> None:
        super().__init__(parent)
        self._memory = memory
        self._revision = 0
        self._asked: set[str] = set()
        self._lock = threading.Lock()
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(2)
        self._probed.connect(self._arrived)
        self._save = QTimer(self)
        self._save.setSingleShot(True)
        self._save.setInterval(2000)
        self._save.timeout.connect(lambda: info_cache().save())

    revision = Property(int, lambda self: self._revision, notify=revisionChanged)

    def _known(self, path: str):
        if not path:
            return None
        info = info_cache().get(path)
        if info is None and not self._memory:
            with self._lock:
                fresh = path not in self._asked
                self._asked.add(path)
            if fresh and os.path.isfile(path):
                self._pool.start(_Probe(self, path))
        return info

    @Slot(str, result=str)
    def duration(self, path: str) -> str:
        """"0:34", or "" until it is known (a probe has then been asked for)."""
        if self._memory:
            return "0:34"
        info = self._known(path)
        return format_duration(info.duration_ms) if info is not None else ""

    @Slot(str, result="QVariantMap")
    def info(self, path: str) -> dict:
        info = self._known(path)
        if info is None:
            return {}
        return {"durationMs": info.duration_ms, "width": info.width, "height": info.height, "fps": info.fps, "audio": info.has_audio,
                "frames": info.frame_count}

    def _arrived(self, path: str, info) -> None:
        if info is not None and not info.error:
            info_cache().put(path, info)
            self._save.start()
        self._revision += 1
        self.revisionChanged.emit()

    @Slot()
    def flush(self) -> None:
        info_cache().save()
