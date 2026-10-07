from __future__ import annotations

import json
import os
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PySide6.QtCore import QSize
from PySide6.QtGui import QImage

from image_triage.cache import ThumbnailKey, edit_state_for
from image_triage.edit_storage import editor_session_path
from image_triage.imaging import load_image_for_display
from image_triage.photo_terminal.session import SCHEMA_NAME, SCHEMA_VERSION
from image_triage.thumbnails import ThumbnailManager
from image_triage.models import ImageRecord

IMAGE_SIZE = (120, 100)


def _write_source_image(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", IMAGE_SIZE, color=(120, 130, 140)).save(path)


def _write_session(image_path: Path, *, operations: list[dict] | None = None) -> Path:
    session_path = editor_session_path(image_path)
    session_path.parent.mkdir(parents=True, exist_ok=True)
    session_path.write_text(
        json.dumps(
            {
                "version": SCHEMA_VERSION,
                "schema": SCHEMA_NAME,
                "coordinateSpaces": [
                    {
                        "id": "space-source-full",
                        "sourceWidth": IMAGE_SIZE[0],
                        "sourceHeight": IMAGE_SIZE[1],
                        "cropInEffect": None,
                    }
                ],
                "assets": {"dir": "assets", "bitmapMasks": []},
                "operations": operations or [],
                "masks": [],
            }
        )
    )
    return session_path


def _make_record(path: Path) -> ImageRecord:
    stat = path.stat()
    return ImageRecord(path=str(path), name=path.name, modified_ns=stat.st_mtime_ns, size=stat.st_size)


class ThumbnailKeyEditStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory(prefix="thumb_edit_state_")
        self.dir = Path(self._tmp.name)
        self.image_path = self.dir / "IMG_0001.jpg"
        _write_source_image(self.image_path)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_no_sidecar_is_sentinel(self) -> None:
        self.assertEqual((0, 0), edit_state_for(self.image_path))

    def test_key_changes_when_sidecar_mtime_or_size_changes(self) -> None:
        before = edit_state_for(self.image_path)
        session_path = _write_session(self.image_path, operations=[{"id": "op-1", "type": "adjust.exposure", "enabled": True, "params": {"exposure": 1.0}}])
        after_create = edit_state_for(self.image_path)
        self.assertNotEqual(before, after_create)

        # Force a distinct mtime/size so the change is unambiguous regardless
        # of filesystem timestamp resolution.
        time.sleep(0.01)
        session_path.write_text(session_path.read_text() + " ")
        after_edit = edit_state_for(self.image_path)
        self.assertNotEqual(after_create, after_edit)

    def test_thumbnail_manager_key_reflects_edit_state(self) -> None:
        manager = ThumbnailManager()
        record = _make_record(self.image_path)
        key_before = manager.make_key(record, QSize(64, 64))
        self.assertEqual((0, 0), key_before.edit_state)

        _write_session(self.image_path, operations=[{"id": "op-1", "type": "adjust.exposure", "enabled": True, "params": {"exposure": 1.0}}])
        key_after = manager.make_key(record, QSize(64, 64))
        self.assertNotEqual((0, 0), key_after.edit_state)
        self.assertNotEqual(key_before.digest(), key_after.digest())


class ThumbnailTaskEditRenderTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory(prefix="thumb_edit_render_")
        self.dir = Path(self._tmp.name)
        self.image_path = self.dir / "IMG_0002.jpg"
        _write_source_image(self.image_path)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _run_task_sync(self) -> QImage:
        from queue import SimpleQueue

        from image_triage.cache import DiskThumbnailCache, MemoryThumbnailCache
        from image_triage.thumbnails import ThumbnailRequest, ThumbnailTask

        memory_cache = MemoryThumbnailCache()
        disk_cache = DiskThumbnailCache(self.dir / "disk_cache")
        result_queue: SimpleQueue = SimpleQueue()
        target_size = QSize(64, 64)
        record_stat = self.image_path.stat()
        key = ThumbnailKey(
            path=str(self.image_path),
            modified_ns=record_stat.st_mtime_ns,
            file_size=record_stat.st_size,
            width=target_size.width(),
            height=target_size.height(),
            edit_state=edit_state_for(self.image_path),
        )
        request = ThumbnailRequest(key=key, path=str(self.image_path), target_size=target_size)
        task = ThumbnailTask(request, memory_cache, disk_cache, result_queue)
        task.run()
        state, _key, image = result_queue.get_nowait()
        self.assertEqual("ready", state)
        return image

    def test_edited_source_thumbnail_differs_from_plain_decode(self) -> None:
        plain, _error = load_image_for_display(str(self.image_path), QSize(64, 64), prefer_embedded=True)

        _write_session(
            self.image_path,
            operations=[{"id": "op-1", "type": "adjust.exposure", "enabled": True, "params": {"exposure": 1.5}}],
        )

        edited = self._run_task_sync()

        self.assertFalse(edited.isNull())
        plain_pixel = plain.pixelColor(0, 0)
        edited_pixel = edited.pixelColor(0, 0)
        self.assertNotEqual(
            (plain_pixel.red(), plain_pixel.green(), plain_pixel.blue()),
            (edited_pixel.red(), edited_pixel.green(), edited_pixel.blue()),
        )

    def test_unedited_source_thumbnail_matches_plain_decode(self) -> None:
        plain, _error = load_image_for_display(str(self.image_path), QSize(64, 64), prefer_embedded=True)

        result = self._run_task_sync()

        self.assertFalse(result.isNull())
        self.assertEqual(plain.size(), result.size())
        plain_pixel = plain.pixelColor(0, 0)
        result_pixel = result.pixelColor(0, 0)
        self.assertEqual(
            (plain_pixel.red(), plain_pixel.green(), plain_pixel.blue()),
            (result_pixel.red(), result_pixel.green(), result_pixel.blue()),
        )


if __name__ == "__main__":
    unittest.main()
