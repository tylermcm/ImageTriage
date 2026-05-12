from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QSize

from image_triage.imaging import sanitize_display_error, thumbnail_skip_reason
from image_triage.thumbnails import _placeholder_thumbnail


class ThumbnailDecodeGuardTests(unittest.TestCase):
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

    def test_placeholder_thumbnail_is_rendered_image(self) -> None:
        image = _placeholder_thumbnail("example.psd", QSize(240, 180), "Large PSD placeholder")

        self.assertFalse(image.isNull())
        self.assertEqual(image.width(), 240)
        self.assertEqual(image.height(), 180)


if __name__ == "__main__":
    unittest.main()
