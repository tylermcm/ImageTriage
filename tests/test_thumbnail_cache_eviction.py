from __future__ import annotations

import os
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from PySide6.QtGui import QColor, QImage

from image_triage.cache import DiskThumbnailCache, ThumbnailKey


def _make_image(size: int = 32) -> QImage:
    image = QImage(size, size, QImage.Format.Format_RGB32)
    image.fill(QColor("red"))
    return image


def _key(path: str) -> ThumbnailKey:
    return ThumbnailKey(path=path, modified_ns=0, file_size=0, width=32, height=32)


class DiskThumbnailCacheEvictionTests(unittest.TestCase):
    def test_enforce_size_cap_is_a_no_op_under_the_cap(self) -> None:
        with TemporaryDirectory() as temp_dir:
            cache = DiskThumbnailCache(Path(temp_dir) / "thumbs", max_bytes=1024 * 1024 * 1024)
            cache.save(_key("a.jpg"), _make_image())

            freed = cache.enforce_size_cap()

            self.assertEqual(0, freed)
            self.assertEqual(1, sum(1 for _ in cache.root.glob("*/*.jpg")))

    def test_enforce_size_cap_deletes_oldest_files_first(self) -> None:
        with TemporaryDirectory() as temp_dir:
            cache = DiskThumbnailCache(Path(temp_dir) / "thumbs", max_bytes=0)
            cache.save(_key("old.jpg"), _make_image())
            old_path = next(cache.root.glob("*/*.jpg"))
            old_stat = old_path.stat()
            # Force distinct mtimes so eviction order is deterministic
            # regardless of filesystem timestamp resolution.
            os.utime(old_path, (old_stat.st_atime, old_stat.st_mtime - 60))

            cache.save(_key("new.jpg"), _make_image())

            freed = cache.enforce_size_cap(max_bytes=old_stat.st_size)

            remaining = list(cache.root.glob("*/*.jpg"))
            self.assertGreater(freed, 0)
            self.assertEqual(1, len(remaining))
            self.assertIsNone(cache.load(_key("old.jpg")))
            self.assertIsNotNone(cache.load(_key("new.jpg")))

    def test_enforce_size_cap_stops_once_back_under_the_cap(self) -> None:
        with TemporaryDirectory() as temp_dir:
            cache = DiskThumbnailCache(Path(temp_dir) / "thumbs", max_bytes=0)
            for index in range(5):
                cache.save(_key(f"{index}.jpg"), _make_image())

            sizes = [p.stat().st_size for p in cache.root.glob("*/*.jpg")]
            total = sum(sizes)
            # Cap at "all but the smallest file" worth of bytes: exactly one
            # file should be evicted, not all of them.
            cap = total - min(sizes)

            cache.enforce_size_cap(max_bytes=cap)

            remaining = list(cache.root.glob("*/*.jpg"))
            self.assertEqual(4, len(remaining))

    def test_background_eviction_runs_without_blocking_save(self) -> None:
        with TemporaryDirectory() as temp_dir:
            cache = DiskThumbnailCache(Path(temp_dir) / "thumbs", max_bytes=0)
            for index in range(250):
                cache.save(_key(f"{index}.jpg"), _make_image())

            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and cache._eviction_lock.locked():
                time.sleep(0.05)

            self.assertFalse(cache._eviction_lock.locked())


if __name__ == "__main__":
    unittest.main()
