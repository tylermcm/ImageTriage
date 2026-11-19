"""Super Resolution's base companion (``photocraft_raw::superres``, "the base companion"): the photo's demosaiced linear camera RGB,
upscaled once by the model before any adjustment, kept beside the photo so every development after it starts from the upscaled base
(as Camera Raw does) and an edit never upscales again.

``write_base`` writes the companion from the upscaled channels (a float16 memory map, ``H x W x 3``) row by row, channel by channel:
square-root scale at ``bits``, lossless JPEG's median-edge prediction, zlib; the same layout the raw crate reads (``apply``), and never
the whole base in memory as integers. ``encode_base`` is the same for an array in memory (tests).

Kept free of Qt and of the app: the upscale worker (the AI runtime's Python) imports it.
"""
from __future__ import annotations

import os
import struct
import tempfile
import zlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

BASE_MAGIC = b"PCSRBAS1"
BITS = 10
ESCAPE = -128


def _median_residual_block(codes, previous):
    """Differences from the median-edge prediction for a block of rows (``n x width``); ``previous`` is the row above the block
    (None at the top). Each sample is predicted from its left, upper and upper-left neighbours (0 outside the picture)."""
    import numpy as np

    up = np.empty_like(codes)
    up[1:] = codes[:-1]
    up[0] = 0 if previous is None else previous
    left = np.zeros_like(codes); left[:, 1:] = codes[:, :-1]
    corner = np.zeros_like(codes); corner[:, 1:] = up[:, :-1]
    hi, lo = np.maximum(left, up), np.minimum(left, up)
    return codes - np.where(corner >= hi, lo, np.where(corner <= lo, hi, left + up - corner))


BLOCK = 256       # rows encoded at once
LEVEL = 3         # zlib: 3.4x faster than 6 for 0.3% more bytes on a base


def write_base(target: Path, original: str, crop, rows, height: int, width: int, bits: int = BITS, progress=None,
               cancelled=lambda: False) -> Path:
    """Write the base companion at ``target``. ``rows(y0, y1)`` gives rows ``y0 … y1-1`` of the base as ``n x width x 3`` float32,
    linear camera RGB 0 … 1. ``crop`` is the original sensor's crop it was made from (x, y, w, h); the base is ``width / crop w``
    times its size. Encoded ``BLOCK`` rows at a time."""
    import numpy as np

    scale = width // int(crop[2])
    top = (1 << bits) - 1
    name = original.encode("utf-8")
    head = BASE_MAGIC + struct.pack("<10I", 1, scale, width, height, *(int(v) for v in crop), bits, len(name)) + name
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="image-triage-srbase-", dir=target.parent) as scratch:
        parts = []
        streams = []
        for k in range(3):
            r_path, e_path = Path(scratch) / f"r{k}", Path(scratch) / f"e{k}"
            streams.append((open(r_path, "wb"), zlib.compressobj(LEVEL), open(e_path, "wb"), zlib.compressobj(LEVEL)))
            parts.append((r_path, e_path))
        previous = [None, None, None]
        pool = ThreadPoolExecutor(3)
        try:
            for y0 in range(0, height, BLOCK):
                if cancelled():
                    raise InterruptedError("cancelled")
                y1 = min(height, y0 + BLOCK)
                block = rows(y0, y1)

                def channel(k):
                    codes = np.round(np.sqrt(np.clip(block[:, :, k], 0.0, 1.0)) * top).astype(np.int32)
                    residual = _median_residual_block(codes, previous[k])
                    previous[k] = codes[-1].copy()
                    small = np.abs(residual) <= 127
                    r_file, r_zip, e_file, e_zip = streams[k]
                    r_file.write(r_zip.compress(np.where(small, residual, ESCAPE).astype(np.int8).tobytes()))
                    if not small.all():
                        e_file.write(e_zip.compress(residual[~small].astype("<i2").tobytes()))

                # the three channels at once: numpy and zlib release the interpreter lock, and each channel has its own streams
                list(pool.map(channel, range(3)))
                if progress is not None:
                    progress(y1, height)
            for r_file, r_zip, e_file, e_zip in streams:
                r_file.write(r_zip.flush()); e_file.write(e_zip.flush())
        finally:
            pool.shutdown()
            for r_file, _r, e_file, _e in streams:
                r_file.close(); e_file.close()
        partial = target.with_name(target.name + ".partial")
        with open(partial, "wb") as out:
            out.write(head)
            for r_path, e_path in parts:
                for path in (r_path, e_path):
                    out.write(struct.pack("<Q", path.stat().st_size))
                    with open(path, "rb") as source:
                        while chunk := source.read(1 << 22):
                            out.write(chunk)
        os.replace(partial, target)
    return target


def encode_base(original: str, crop, channels, bits: int = BITS) -> bytes:
    """The companion of ``channels`` (``H x W x 3`` float, linear camera RGB 0 … 1) as bytes (tests)."""
    with tempfile.TemporaryDirectory() as scratch:
        target = Path(scratch) / "base.superres"
        h, w = channels.shape[:2]
        write_base(target, original, crop, lambda y0, y1: channels[y0:y1], h, w, BITS if bits is None else bits)
        return target.read_bytes()
