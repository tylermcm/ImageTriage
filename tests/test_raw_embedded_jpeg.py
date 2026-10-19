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


class MarkerScanPageSizeTests(unittest.TestCase):
    """mmap.find reads past the end of a mapping whose size is an exact multiple of the page size and kills the process on Windows (an
    access violation, not an exception). Files of 4096, 8192 or 102400 bytes crashed the scan; the scan now reads those normally."""

    def test_a_file_that_is_an_exact_multiple_of_the_page_size_does_not_crash_the_process(self) -> None:
        import subprocess
        import sys

        script = (
            "import os, sys, tempfile\n"
            "from image_triage.raw_embedded_jpeg import _scan_jpeg_markers\n"
            "root = tempfile.mkdtemp()\n"
            "for size in (4096, 8192, 102400):\n"
            "    for i in range(300):\n"
            "        path = os.path.join(root, f'{size}_{i}.nef')\n"
            "        with open(path, 'wb') as handle:\n"
            "            handle.write(os.urandom(size))\n"
            "        _scan_jpeg_markers(path, size)\n"
            "print('survived')\n"
        )
        repo_root = str(Path(__file__).resolve().parents[1])
        done = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, cwd=repo_root, timeout=120)
        self.assertEqual(0, done.returncode, done.stderr[-400:])
        self.assertIn("survived", done.stdout)

    def test_a_page_multiple_file_still_finds_the_embedded_jpeg(self) -> None:
        from image_triage.raw_embedded_jpeg import _scan_jpeg_markers

        jpeg = _jpeg_bytes()
        for total in (4096 * 2, 4096 * 5):
            with tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / "padded.nef"
                lead = 300
                path.write_bytes(b"\x00" * lead + jpeg + b"\x00" * (total - lead - len(jpeg)))
                self.assertEqual(total, path.stat().st_size)
                found = _scan_jpeg_markers(str(path), total)
                self.assertIsNotNone(found)
                self.assertEqual((lead, len(jpeg)), (found.offset, found.byte_count))

    def test_an_ordinary_size_file_still_finds_the_embedded_jpeg(self) -> None:
        from image_triage.raw_embedded_jpeg import _scan_jpeg_markers

        jpeg = _jpeg_bytes()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "odd.nef"
            path.write_bytes(b"\x00" * 301 + jpeg + b"\x00" * 77)
            found = _scan_jpeg_markers(str(path), path.stat().st_size)
            self.assertEqual((301, len(jpeg)), (found.offset, found.byte_count))
