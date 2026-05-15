from __future__ import annotations

import struct
import tempfile
import unittest
from pathlib import Path

from PySide6.QtCore import QByteArray, QBuffer, QIODevice
from PySide6.QtGui import QImage

from image_triage.raw_embedded_jpeg import extract_embedded_jpeg


def _jpeg_bytes(width: int = 128, height: int = 96) -> bytes:
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(0x336699)
    payload = QByteArray()
    buffer = QBuffer(payload)
    assert buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    assert image.save(buffer, "JPEG")
    buffer.close()
    return bytes(payload)


def _fake_tiff_raw(jpeg: bytes, *, prefix: bytes = b"") -> bytes:
    entry_count = 2
    ifd_offset = 8
    jpeg_offset = ifd_offset + 2 + (entry_count * 12) + 4 + len(prefix)
    return b"".join(
        (
            b"II",
            struct.pack("<H", 42),
            struct.pack("<I", ifd_offset),
            struct.pack("<H", entry_count),
            struct.pack("<HHI", 0x0201, 4, 1),
            struct.pack("<I", jpeg_offset),
            struct.pack("<HHI", 0x0202, 4, 1),
            struct.pack("<I", len(jpeg)),
            struct.pack("<I", 0),
            prefix,
            jpeg,
        )
    )


class RawEmbeddedJpegTests(unittest.TestCase):
    def test_extracts_jpeg_from_tiff_preview_tags(self) -> None:
        jpeg = _jpeg_bytes()
        with tempfile.TemporaryDirectory(prefix="image_triage_raw_jpeg_") as temp_dir:
            path = Path(temp_dir) / "frame.nef"
            path.write_bytes(_fake_tiff_raw(jpeg))

            embedded = extract_embedded_jpeg(str(path))

        self.assertIsNotNone(embedded)
        assert embedded is not None
        self.assertEqual("tiff_jpeg_interchange", embedded.source)
        self.assertTrue(embedded.payload.startswith(b"\xff\xd8"))
        self.assertGreaterEqual(embedded.byte_count, 256)

    def test_returns_none_for_non_tiff_container(self) -> None:
        with tempfile.TemporaryDirectory(prefix="image_triage_raw_jpeg_") as temp_dir:
            path = Path(temp_dir) / "frame.cr3"
            path.write_bytes(b"not-a-tiff-container")

            self.assertIsNone(extract_embedded_jpeg(str(path)))

    def test_marker_scan_finds_jpeg_in_non_tiff_container(self) -> None:
        jpeg = _jpeg_bytes()
        with tempfile.TemporaryDirectory(prefix="image_triage_raw_jpeg_") as temp_dir:
            path = Path(temp_dir) / "frame.cr3"
            path.write_bytes(b"not-a-tiff-container" + (b"\0" * 4096) + jpeg + b"tail")

            embedded = extract_embedded_jpeg(str(path))

        self.assertIsNotNone(embedded)
        assert embedded is not None
        self.assertEqual("jpeg_marker_scan", embedded.source)
        self.assertTrue(embedded.payload.startswith(b"\xff\xd8"))


if __name__ == "__main__":
    unittest.main()
