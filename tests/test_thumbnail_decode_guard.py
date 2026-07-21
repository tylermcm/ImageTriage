from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from queue import SimpleQueue
from unittest.mock import patch

from PySide6.QtCore import QSize
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from image_triage.cache import DiskThumbnailCache, MemoryThumbnailCache, ThumbnailKey
from image_triage.imaging import _load_with_fallbacks, sanitize_display_error, thumbnail_skip_reason
from image_triage.thumbnails import ThumbnailRequest, ThumbnailTask, _placeholder_thumbnail


def _ensure_app() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


class ThumbnailDecodeGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        _ensure_app()

    def test_large_psd_uses_thumbnail_placeholder_reason(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "large.psd"
            path.write_bytes(b"not really a psd, just big enough")
            with patch("image_triage.imaging.THUMBNAIL_SKIP_PSD_BYTES", 8):
                reason = thumbnail_skip_reason(str(path), QSize(240, 180))

        self.assertEqual(reason, "Large PSD placeholder")

    def test_large_general_file_uses_thumbnail_placeholder_reason(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "large.jpg"
            path.write_bytes(b"not really a jpeg, just big enough")
            with patch("image_triage.imaging.THUMBNAIL_SKIP_GENERAL_BYTES", 8):
                reason = thumbnail_skip_reason(str(path), QSize(240, 180))

        self.assertEqual(reason, "Large file placeholder")

    def test_decode_errors_are_sanitized_for_tiles(self) -> None:
        self.assertEqual(
            sanitize_display_error("cannot identify image file 'very-long-name.jpg'", path="very-long-name.jpg"),
            "Could not decode image.",
        )
        self.assertEqual(
            sanitize_display_error("this is not a PSD or PSB file", path="bad.psd"),
            "Could not decode PSD composite.",
        )
        self.assertEqual(
            sanitize_display_error("b'Unsupported file format or not RAW file'", path="bad.nef"),
            "File is not a valid RAW image.",
        )

    def test_placeholder_thumbnail_is_rendered_image(self) -> None:
        image = _placeholder_thumbnail("example.psd", QSize(240, 180), "Large PSD placeholder")

        self.assertFalse(image.isNull())
        self.assertEqual(image.width(), 240)
        self.assertEqual(image.height(), 180)

    def test_stale_thumbnail_task_skips_decode(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            key = ThumbnailKey(
                path=str(Path(temp_dir) / "slow.jpg"),
                modified_ns=1,
                file_size=2,
                width=240,
                height=180,
            )
            result_queue: SimpleQueue = SimpleQueue()
            request = ThumbnailRequest(
                key=key,
                path=key.path,
                target_size=QSize(key.width, key.height),
                drop_if_not_wanted=True,
            )
            task = ThumbnailTask(
                request,
                MemoryThumbnailCache(),
                DiskThumbnailCache(Path(temp_dir) / "thumbs"),
                result_queue,
                is_key_wanted=lambda _key: False,
            )

            with patch("image_triage.thumbnails.load_image_for_display") as load_image:
                task.run()

            load_image.assert_not_called()
            state, queued_key, stage = result_queue.get_nowait()
            self.assertEqual(state, "stale")
            self.assertEqual(queued_key, key)
            self.assertEqual(stage, "pre_cache")

    def test_tiff_uses_pillow_before_qt_reader_when_available(self) -> None:
        image = QImage(8, 8, QImage.Format.Format_ARGB32)
        image.fill(0)
        with patch("image_triage.imaging.Image", object()), patch(
            "image_triage.imaging._load_pillow_image",
            return_value=(image, None),
        ) as load_pillow, patch("image_triage.imaging._load_standard_image") as load_standard:
            loaded, error = _load_with_fallbacks("C:/temp/float32.tif", QSize(64, 64))

        self.assertFalse(loaded.isNull())
        self.assertIsNone(error)
        load_pillow.assert_called_once()
        load_standard.assert_not_called()


if __name__ == "__main__":
    unittest.main()
