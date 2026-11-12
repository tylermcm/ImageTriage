from __future__ import annotations

import struct
import tempfile
import unittest
from pathlib import Path

import pytest

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

# A small uncompressed Bayer DNG exercises LibRaw itself, rather than a stub
# that could accidentally return sensor-order pixels unlike rawpy.postprocess.
def _write_orientation_dng(path, orientation):
    import numpy as np

    width, height = 96, 64
    y, x = np.indices((height, width))
    mosaic = np.where(y < height // 2, np.where(x < width // 2, 8000, 22000),
                      np.where(x < width // 2, 38000, 54000)).astype('<u2')
    data = mosaic.tobytes()
    tags = {}

    def add(tag, kind, values):
        if kind == 2:
            payload = values.encode('ascii') + b'\0'
            count = len(payload)
        elif kind == 1:
            payload, count = bytes(values), len(values)
        elif kind in (3, 4):
            payload = struct.pack('<' + ('H' if kind == 3 else 'I') * len(values), *values)
            count = len(values)
        else:
            payload = b''.join(struct.pack('<ii' if kind == 10 else '<II', a, b) for a, b in values)
            count = len(values)
        tags[tag] = (kind, count, payload)

    for tag, value in [(256, width), (257, height), (278, height), (279, len(data)), (50717, 65535)]:
        add(tag, 4, [value])
    for tag, value in [(258, 16), (259, 1), (262, 32803), (274, orientation), (277, 1), (284, 1), (50778, 21)]:
        add(tag, 3, [value])
    add(33421, 3, [2, 2])
    add(33422, 1, [0, 1, 1, 2])
    for tag in (271, 272, 50708):
        add(tag, 2, 'Synthetic orientation test')
    add(50706, 1, [1, 4, 0, 0])
    add(50707, 1, [1, 3, 0, 0])
    add(50714, 5, [(0, 1)])
    add(50728, 5, [(1, 1)] * 3)
    add(50721, 10, [(1 if i in (0, 4, 8) else 0, 1) for i in range(9)])
    add(273, 4, [0])
    add(50719, 4, [0, 0])
    add(50720, 4, [width, height])
    offset = 8 + 2 + 12 * len(tags) + 4
    extra, entries = bytearray(), []
    for tag, (kind, count, payload) in sorted(tags.items()):
        if tag == 273:
            value = None
        elif len(payload) <= 4:
            value = payload.ljust(4, b'\0')
        else:
            value = struct.pack('<I', offset + len(extra))
            extra.extend(payload)
            if len(extra) % 2:
                extra.append(0)
        entries.append((tag, kind, count, value))
    start = offset + len(extra)
    ifd = b''.join(struct.pack('<HHI', tag, kind, count) +
                   (struct.pack('<I', start) if value is None else value)
                   for tag, kind, count, value in entries)
    path.write_bytes(b'II' + struct.pack('<HIH', 42, 8, len(tags)) + ifd + struct.pack('<I', 0) + extra + data)


@pytest.mark.parametrize('orientation,turns,flip', [(1, 0, 0), (3, 2, 3), (6, -1, 6), (8, 1, 5)])
@pytest.mark.parametrize('quality', ['high', 'balanced', 'fast'])
def test_demosaiced_raw_applies_orientation_once(tmp_path, orientation, turns, flip, quality):
    import numpy as np
    rawpy = pytest.importorskip('rawpy')

    path = tmp_path / 'orientation.dng'
    _write_orientation_dng(path, orientation)
    before = path.read_bytes()
    options = dict(use_camera_wb=True, output_bps=8, auto_bright_thr=.01, user_flip=0)
    if quality in ('balanced', 'fast'):
        options['half_size'] = True
    if quality == 'fast':
        options['demosaic_algorithm'] = rawpy.DemosaicAlgorithm.LINEAR
    with rawpy.imread(str(path)) as raw:
        assert raw.sizes.flip == flip
        sensor_pixels = raw.postprocess(**options)
    expected = imaging._qimage_from_rgb_array(np.ascontiguousarray(np.rot90(sensor_pixels, turns)))
    with rawpy.imread(str(path)) as raw:
        actual = imaging._postprocess_raw(raw, QSize(), quality_mode=quality)
    # Compare all pixels: 180-degree errors retain dimensions, and both portrait
    # directions swap dimensions but put the distinctive corners in opposite places.
    assert actual == expected
    if quality != 'fast':
        from image_triage.image_resize import load_image_for_export

        target = QSize() if quality == 'high' else QSize(16, 16)
        exported = load_image_for_export(str(path), target_size=target,
                                         ignore_orientation=False, strip_metadata=True)
        assert exported.image == imaging._scale_if_needed(expected, target)
    if quality == 'high':
        from types import SimpleNamespace

        # Embedded bitmaps still arrive in sensor order and need the host rotation.
        thumbnail = SimpleNamespace(sizes=SimpleNamespace(flip=flip), extract_thumb=lambda:
                                    SimpleNamespace(format=rawpy.ThumbFormat.BITMAP, data=sensor_pixels))
        assert imaging._load_embedded_thumbnail(thumbnail, QSize()) == expected
    assert path.read_bytes() == before


if __name__ == "__main__":
    unittest.main()
