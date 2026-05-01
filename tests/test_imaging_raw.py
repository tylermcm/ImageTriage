from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from PySide6.QtCore import QSize
from PySide6.QtGui import QImage

from image_triage.imaging import _load_raw_image


class _RawContext:
    def __init__(self, raw) -> None:
        self._raw = raw

    def __enter__(self):
        return self._raw

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False


class ImagingRawTests(unittest.TestCase):
    def test_dng_prefers_embedded_preview_when_available(self) -> None:
        embedded = QImage(64, 48, QImage.Format.Format_RGB32)
        embedded.fill(0x112233)
        raw = object()
        rawpy_mock = Mock()
        rawpy_mock.imread.return_value = _RawContext(raw)

        with patch("image_triage.imaging.rawpy", rawpy_mock), patch(
            "image_triage.imaging._load_embedded_thumbnail",
            return_value=embedded,
        ), patch(
            "image_triage.imaging._postprocess_raw",
            side_effect=AssertionError("postprocess should not run when an embedded DNG preview is available"),
        ):
            image, error = _load_raw_image(
                "C:/temp/sample.dng",
                QSize(800, 600),
                prefer_embedded=True,
                suffix=".dng",
            )

        self.assertFalse(image.isNull(), error)
        self.assertIsNone(error)
        self.assertEqual(image.size(), embedded.size())


if __name__ == "__main__":
    unittest.main()
