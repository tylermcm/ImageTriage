"""AI Denoise on the raw sensor data (RawNIND, Bayer), before PhotoCraft demosaics and develops the photo.

The preview worker hands out the crop's CFA samples, normalised 0 (black) .. 1 (white) (``denoiseInput``). This module packs them
as the model expects (four half-resolution planes R, G1, G2, B, white-balanced for daylight), runs the model in overlapping tiles,
and turns its output (camera RGB at full resolution, at an arbitrary learned gain) back into one sample per photosite of the same
pattern. ``write_denoised`` appends those samples to a copy of the original raw as the trailer PhotoCraft's raw decoder reads
(``photocraft_raw::denoised``); every other reader still sees the original file.

Model: RawNIND UtNet2 Bayer (B. Brummer, C. De Vleeschouwer, "Learning Joint Denoising, Demosaicing, and Compression from the Raw
Natural Image Noise Dataset", 2025), weights used under CC-BY-4.0, converted to ONNX by us. See docs/ai_denoise.md.
"""
from __future__ import annotations

import os
import shutil
import struct
from pathlib import Path

MAGIC = b"PCDENOI1"
TRAILER_LEN = 4 + 4 + 6 * 4 + 8 + 8
AMOUNT_FROM_END = TRAILER_LEN - 4
TILE = 256          # packed pixels per tile side (the model's output is twice that)
OVERLAP = 32        # packed pixels shared by neighbouring tiles, blended
CLIP_KEEP = 0.97    # photosites this close to white keep their own value, so clipped highlights stay clipped
OFFSETS = {"RGGB": (0, 0), "GRBG": (1, 0), "GBRG": (0, 1), "BGGR": (1, 1)}


def aligned(region, pattern):
    """``(x, y, w, h, dx, dy)``: the part of ``region`` (x, y, w, h) that starts on a red photosite and has even sides, and where
    it starts inside the region."""
    if pattern not in OFFSETS:
        raise ValueError(f"Unsupported colour filter pattern {pattern}")
    dx, dy = OFFSETS[pattern]
    x, y, w, h = region
    w2, h2 = (w - dx) // 2 * 2, (h - dy) // 2 * 2
    if w2 < 32 or h2 < 32:
        raise ValueError("The photo is too small to denoise")
    return x + dx, y + dy, w2, h2, dx, dy


def pack(plane, multipliers):
    """RGGB samples (h x w, float) -> 4 x h/2 x w/2, white-balanced (green = 1)."""
    import numpy as np

    r, g1, g2, b = plane[0::2, 0::2], plane[0::2, 1::2], plane[1::2, 0::2], plane[1::2, 1::2]
    return np.stack([r * multipliers[0], g1, g2, b * multipliers[2]]).astype(np.float32)


def _ramp(n, overlap, first, last):
    import numpy as np

    w = np.ones(n, np.float32)
    if overlap:
        edge = (np.arange(overlap, dtype=np.float32) + 0.5) / overlap
        if not first:
            w[:overlap] = edge
        if not last:
            w[-overlap:] = edge[::-1]
    return w


def run_tiles(packed, infer, tile=TILE, overlap=OVERLAP, cancel=None, progress=None):
    """The model's output for the whole ``packed`` picture (4 x H x W), run tile by tile (``infer``: 1x4xhxw -> 1x3x2hx2w),
    feathered across the overlaps. Tile sides are multiples of 16, as the network needs."""
    import numpy as np

    _, H, W = packed.shape
    step = tile - overlap
    ys = list(range(0, max(H - tile, 0) + 1, step)) or [0]
    xs = list(range(0, max(W - tile, 0) + 1, step)) or [0]
    if ys[-1] + tile < H:
        ys.append(max(H - tile, 0))
    if xs[-1] + tile < W:
        xs.append(max(W - tile, 0))
    out = np.zeros((3, 2 * H, 2 * W), np.float32)
    weight = np.zeros((2 * H, 2 * W), np.float32)
    total, done = len(ys) * len(xs), 0
    for y in ys:
        for x in xs:
            if cancel is not None and cancel.is_set():
                raise InterruptedError("cancelled")
            h, w = min(tile, H - y), min(tile, W - x)
            ph, pw = (-h) % 16, (-w) % 16
            piece = packed[:, y:y + h, x:x + w]
            if ph or pw:
                piece = np.pad(piece, ((0, 0), (0, ph), (0, pw)), mode="reflect" if min(h, w) > 16 else "edge")
            result = infer(piece[None])[0][:, :2 * h, :2 * w]
            wy = np.repeat(_ramp(h, min(overlap, h // 2), y == ys[0], y == ys[-1]), 2)
            wx = np.repeat(_ramp(w, min(overlap, w // 2), x == xs[0], x == xs[-1]), 2)
            wt = wy[:, None] * wx[None, :]
            out[:, 2 * y:2 * (y + h), 2 * x:2 * (x + w)] += result * wt
            weight[2 * y:2 * (y + h), 2 * x:2 * (x + w)] += wt
            done += 1
            if progress is not None:
                progress(done, total)
    return out / np.maximum(weight, 1e-6)


def unpack(rgb, packed, multipliers, original):
    """The denoised RGGB samples (h x w): the model's camera RGB at the original's brightness, white balance undone, one channel
    per photosite. Photosites near white in ``original`` keep their value."""
    import numpy as np

    ref = (packed[0].mean() + (packed[1].mean() + packed[2].mean()) / 2 + packed[3].mean()) / 3
    rgb = rgb * (ref / max(float(rgb.mean()), 1e-12))
    out = np.empty(original.shape, np.float32)
    out[0::2, 0::2] = rgb[0, 0::2, 0::2] / multipliers[0]
    out[0::2, 1::2] = rgb[1, 0::2, 1::2]
    out[1::2, 0::2] = rgb[1, 1::2, 0::2]
    out[1::2, 1::2] = rgb[2, 1::2, 1::2] / multipliers[2]
    keep = original >= CLIP_KEEP
    out[keep] = original[keep]
    return out


def denoise_plane(plane, region, pattern, multipliers, infer, cancel=None, progress=None):
    """``((x, y, w, h), samples)``: the denoised samples of the part of ``region`` that starts on red, from the worker's plane
    (``region``'s samples, row-major)."""
    import numpy as np

    x, y, w, h, dx, dy = aligned(region, pattern)
    rggb = np.asarray(plane, np.float32)[dy:dy + h, dx:dx + w]
    packed = pack(rggb, multipliers)
    rgb = run_tiles(packed, infer, cancel=cancel, progress=progress)
    return (x, y, w, h), unpack(rgb, packed, multipliers, rggb)


def trailer(samples, region, sensor, amount: float) -> bytes:
    """The bytes appended to the original raw (see ``photocraft_raw::denoised``)."""
    import numpy as np

    payload = np.ascontiguousarray(samples, dtype="<f2").tobytes()
    x, y, w, h = region
    return payload + struct.pack("<If6IQ", 1, float(amount), x, y, w, h, sensor[0], sensor[1], len(payload)) + MAGIC


def write_denoised(original: Path, target: Path, samples, region, sensor, amount: float) -> Path:
    """A copy of ``original`` with the denoised samples appended, written next to its final name and moved into place."""
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + ".partial")
    with open(original, "rb") as source, open(partial, "wb") as out:
        shutil.copyfileobj(source, out, 1 << 22)
        out.write(trailer(samples, region, sensor, amount))
    os.replace(partial, target)
    return target


def has_trailer(path: Path) -> bool:
    try:
        with open(path, "rb") as f:
            f.seek(-8, os.SEEK_END)
            return f.read(8) == MAGIC
    except OSError:
        return False


def set_amount(path: Path, amount: float) -> None:
    """Change how much of the denoised samples a file uses, in place (four bytes near its end)."""
    if not has_trailer(path):
        raise ValueError("Not a denoised raw file")
    with open(path, "r+b") as f:
        f.seek(-AMOUNT_FROM_END, os.SEEK_END)
        f.write(struct.pack("<f", float(max(0.0, min(100.0, amount)))))


def read_amount(path: Path) -> float | None:
    if not has_trailer(path):
        return None
    with open(path, "rb") as f:
        f.seek(-AMOUNT_FROM_END, os.SEEK_END)
        return struct.unpack("<f", f.read(4))[0]
