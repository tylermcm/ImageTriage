from __future__ import annotations

import struct
import tempfile
import unittest
from pathlib import Path

from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QSize
from PySide6.QtGui import QImage

import image_triage.imaging as imaging
from image_triage.imaging import _load_raw_image


class _RawContext:
    def __init__(self, raw) -> None:
        self._raw = raw

    def __enter__(self):
        return self._raw

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False


class _RawpyStub:
    def __init__(self, raw) -> None:
        self._raw = raw

    def imread(self, _path: str) -> _RawContext:
        return _RawContext(self._raw)


class _FailingRawpyStub:
    def imread(self, _path: str) -> _RawContext:
        raise AssertionError("rawpy should not open when direct embedded JPEG extraction succeeds")


def _jpeg_bytes(width: int = 128, height: int = 96) -> bytes:
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(0x224466)
    payload = QByteArray()
    buffer = QBuffer(payload)
    assert buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    assert image.save(buffer, "JPEG")
    buffer.close()
    return bytes(payload)


def _fake_tiff_raw(jpeg: bytes) -> bytes:
    entry_count = 2
    jpeg_offset = 8 + 2 + (entry_count * 12) + 4
    return b"".join(
        (
            b"II",
            struct.pack("<H", 42),
            struct.pack("<I", 8),
            struct.pack("<H", entry_count),
            struct.pack("<HHI", 0x0201, 4, 1),
            struct.pack("<I", jpeg_offset),
            struct.pack("<HHI", 0x0202, 4, 1),
            struct.pack("<I", len(jpeg)),
            struct.pack("<I", 0),
            jpeg,
        )
    )


class ImagingRawTests(unittest.TestCase):
    def test_raw_preview_uses_direct_embedded_jpeg_before_rawpy(self) -> None:
        jpeg = _jpeg_bytes(180, 120)
        original_rawpy = imaging.rawpy
        imaging.rawpy = _FailingRawpyStub()
        try:
            with tempfile.TemporaryDirectory(prefix="image_triage_raw_preview_") as temp_dir:
                path = Path(temp_dir) / "frame.nef"
                path.write_bytes(_fake_tiff_raw(jpeg))

                image, error = _load_raw_image(
                    str(path),
                    QSize(800, 600),
                    prefer_embedded=True,
                    suffix=".nef",
                )
        finally:
            imaging.rawpy = original_rawpy

        self.assertFalse(image.isNull(), error)
        self.assertIsNone(error)
        self.assertLessEqual(image.width(), 800)
        self.assertLessEqual(image.height(), 600)
        self.assertEqual(round(180 / 120, 2), round(image.width() / image.height(), 2))

    def test_dng_prefers_embedded_preview_when_available(self) -> None:
        embedded = QImage(64, 48, QImage.Format.Format_RGB32)
        embedded.fill(0x112233)
        raw = object()
        original_rawpy = imaging.rawpy
        original_embedded = imaging._load_embedded_thumbnail
        original_postprocess = imaging._postprocess_raw
        imaging.rawpy = _RawpyStub(raw)
        imaging._load_embedded_thumbnail = lambda _raw, _target_size: embedded

        def fail_postprocess(*_args, **_kwargs):
            raise AssertionError("postprocess should not run when an embedded DNG preview is available")

        imaging._postprocess_raw = fail_postprocess
        try:
            image, error = _load_raw_image(
                "C:/temp/sample.dng",
                QSize(800, 600),
                prefer_embedded=True,
                suffix=".dng",
            )
        finally:
            imaging.rawpy = original_rawpy
            imaging._load_embedded_thumbnail = original_embedded
            imaging._postprocess_raw = original_postprocess

        self.assertFalse(image.isNull(), error)
        self.assertIsNone(error)
        self.assertEqual(image.size(), embedded.size())

    def test_embedded_preview_mode_does_not_postprocess_missing_preview(self) -> None:
        raw = object()
        original_rawpy = imaging.rawpy
        original_embedded = imaging._load_embedded_thumbnail
        original_postprocess = imaging._postprocess_raw
        imaging.rawpy = _RawpyStub(raw)
        imaging._load_embedded_thumbnail = lambda _raw, _target_size: None

        def fail_postprocess(*_args, **_kwargs):
            raise AssertionError("preview navigation should not full-postprocess RAW files")

        imaging._postprocess_raw = fail_postprocess
        try:
            image, error = _load_raw_image(
                "C:/temp/sample.nef",
                QSize(1200, 900),
                prefer_embedded=True,
                suffix=".nef",
            )
        finally:
            imaging.rawpy = original_rawpy
            imaging._load_embedded_thumbnail = original_embedded
            imaging._postprocess_raw = original_postprocess

        self.assertTrue(image.isNull())
        self.assertIn("fast embedded RAW preview", error or "")


if __name__ == "__main__":
    unittest.main()
