import io
import struct
from types import SimpleNamespace

import numpy as np
import pytest

from image_triage.photocraft_raw_source import _write_dng


def sensor():
    return SimpleNamespace(
        raw_image_visible=np.arange(48, dtype=np.uint16).reshape(6, 8) + 100,
        raw_pattern=np.array([[0, 1], [3, 2]], dtype=np.uint8), color_desc=b"RGBG",
        sizes=SimpleNamespace(top_margin=1, left_margin=0, flip=6, crop_width=0, crop_height=0),
        black_level_per_channel=[11, 12, 13, 14], white_level=16383,
        camera_white_level_per_channel=None, camera_whitebalance=[2, 1, 1.5, 1],
        rgb_xyz_matrix=np.array([[.7, -.2, -.1], [-.4, 1.2, .2], [.1, .2, .5], [0, 0, 0]]),
    )


def tags(data):
    count = struct.unpack_from("<H", data, 8)[0]
    sizes = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 10: 8}
    result = {}
    for i in range(count):
        offset = 10 + 12 * i
        tag, kind, n = struct.unpack_from("<HHI", data, offset)
        start = offset + 8 if sizes[kind] * n <= 4 else struct.unpack_from("<I", data, offset + 8)[0]
        result[tag] = data[start:start + sizes[kind] * n]
    return result


def test_adapter_preserves_sensor_values_cfa_levels_wb_and_orientation():
    raw = sensor()
    output = io.BytesIO()
    _write_dng(output, raw)
    data = output.getvalue()
    metadata = tags(data)
    offset = struct.unpack("<I", metadata[273])[0]
    assert np.array_equal(np.frombuffer(data, dtype="<u2", offset=offset).reshape(6, 8), raw.raw_image_visible)
    assert metadata[33422] == bytes([1, 2, 0, 1])
    assert struct.unpack("<H", metadata[274])[0] == 6
    black = struct.unpack("<8I", metadata[50714])
    assert [black[i] / black[i+1] for i in range(0, 8, 2)] == [14, 13, 11, 12]
    wb = struct.unpack("<6I", metadata[50728])
    assert [wb[i] / wb[i+1] for i in range(0, 6, 2)] == pytest.approx([.5, 1, 2/3], abs=1e-6)


@pytest.mark.parametrize("field,value", [("color_desc", b"CMYG"), ("camera_whitebalance", [0,1,1,1]), ("rgb_xyz_matrix", np.zeros((4,3)))])
def test_adapter_rejects_unusable_sensor_metadata(field, value):
    raw = sensor()
    setattr(raw, field, value)
    with pytest.raises(ValueError):
        _write_dng(io.BytesIO(), raw)


def test_camera_default_crop_is_retained_without_discarding_sensor_samples():
    raw = sensor()
    raw.sizes.top_margin = 0
    raw.sizes.crop_left_margin, raw.sizes.crop_top_margin = 2, 1
    raw.sizes.crop_width, raw.sizes.crop_height = 4, 4
    output = io.BytesIO()
    _write_dng(output, raw)
    metadata = tags(output.getvalue())
    assert struct.unpack("<2I", metadata[50719]) == (2, 1)
    assert struct.unpack("<2I", metadata[50720]) == (4, 4)
    assert struct.unpack("<I", metadata[279])[0] == raw.raw_image_visible.nbytes
