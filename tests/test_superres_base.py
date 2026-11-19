"""Super Resolution's base companion as the writer lays it out: decoded here the way photocraft_raw::superres::apply reads it."""
import struct
import zlib

import numpy as np

from image_triage import superres_base_encode as encode


def decode(data):
    assert data.startswith(encode.BASE_MAGIC)
    version, scale, w, h, cx, cy, cw, ch, bits, name_len = struct.unpack_from("<10I", data, 8)
    at = 48 + name_len
    top = (1 << bits) - 1
    out = np.zeros((h, w, 3), np.float64)
    for k in range(3):
        sections = []
        for _ in range(2):
            (length,) = struct.unpack_from("<Q", data, at)
            sections.append(zlib.decompress(data[at + 8:at + 8 + length]))
            at += 8 + length
        residual = np.frombuffer(sections[0], np.int8).astype(np.int64).reshape(h, w)
        escapes = iter(np.frombuffer(sections[1], "<i2").astype(int))
        codes = np.zeros((h, w), np.int64)
        for y in range(h):
            for x in range(w):
                left = codes[y, x - 1] if x else 0
                up = codes[y - 1, x] if y else 0
                corner = codes[y - 1, x - 1] if x and y else 0
                hi, lo = max(left, up), min(left, up)
                pred = lo if corner >= hi else hi if corner <= lo else left + up - corner
                r = residual[y, x]
                codes[y, x] = pred + (next(escapes) if r == encode.ESCAPE else r)
        out[..., k] = (codes / top) ** 2
    return data[48:48 + name_len].decode(), (scale, w, h, (cx, cy, cw, ch), bits), out


def test_the_base_companion_round_trips_within_its_precision():
    rng = np.random.default_rng(2)
    yy, xx = np.mgrid[0:40, 0:56]
    channels = np.stack([(0.02 + 0.5 * xx / 56 * yy / 40), 0.3 + 0.0 * xx, (0.6 * xx / 56)], -1)
    channels = (channels + 0.01 * rng.standard_normal(channels.shape)).clip(0, 1).astype(np.float32)
    data = encode.encode_base("../../IMG.NEF", (4, 2, 28, 20), channels)
    original, (scale, w, h, crop, bits), back = decode(data)
    assert (original, scale, w, h, crop, bits) == ("../../IMG.NEF", 2, 56, 40, (4, 2, 28, 20), encode.BITS)
    step = 2 * np.sqrt(channels) / ((1 << encode.BITS) - 1)
    assert np.all(np.abs(back - channels) <= step * 0.51 + 1e-7)
