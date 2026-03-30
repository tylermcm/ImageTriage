from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from hashlib import sha1
from pathlib import Path
from threading import Lock

from PySide6.QtCore import QStandardPaths
from PySide6.QtGui import QImage, QImageReader


@dataclass(slots=True, frozen=True)
class ThumbnailKey:
    path: str
    modified_ns: int
    file_size: int
    width: int
    height: int

    def digest(self) -> str:
        payload = f"{self.path}|{self.modified_ns}|{self.file_size}|{self.width}|{self.height}"
        return sha1(payload.encode("utf-8"), usedforsecurity=False).hexdigest()


class MemoryThumbnailCache:
    def __init__(self, max_bytes: int = 256 * 1024 * 1024) -> None:
        self._max_bytes = max_bytes
        self._current_bytes = 0
        self._entries: OrderedDict[ThumbnailKey, tuple[QImage, int]] = OrderedDict()
        self._lock = Lock()

    def get(self, key: ThumbnailKey) -> QImage | None:
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            self._entries.move_to_end(key)
            return entry[0]

    def put(self, key: ThumbnailKey, image: QImage) -> None:
        if image.isNull():
            return

        cost = max(1, image.sizeInBytes())
        with self._lock:
            existing = self._entries.pop(key, None)
            if existing is not None:
                self._current_bytes -= existing[1]

            if cost > self._max_bytes:
                self._entries.clear()
                self._current_bytes = 0
                return

            self._entries[key] = (image, cost)
            self._current_bytes += cost
            self._entries.move_to_end(key)
            self._trim()

    def _trim(self) -> None:
        while self._current_bytes > self._max_bytes and self._entries:
            _, (_, cost) = self._entries.popitem(last=False)
            self._current_bytes -= cost


class DiskThumbnailCache:
    def __init__(self, root: str | Path | None = None) -> None:
        cache_root = root
        if cache_root is None:
            base = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.CacheLocation)
            cache_root = Path(base) / "thumbs"
        self.root = Path(cache_root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _file_path(self, key: ThumbnailKey) -> Path:
        digest = key.digest()
        shard = digest[:2]
        return self.root / shard / f"{digest}.jpg"

    def load(self, key: ThumbnailKey) -> QImage | None:
        target = self._file_path(key)
        if not target.exists():
            return None

        reader = QImageReader(str(target))
        image = reader.read()
        if image.isNull():
            return None
        return image

    def save(self, key: ThumbnailKey, image: QImage) -> None:
        if image.isNull():
            return

        target = self._file_path(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        image.save(str(target), "JPEG", quality=88)
