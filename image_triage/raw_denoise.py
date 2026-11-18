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
LEVEL_RADIUS = 8    # half-width (photosites of one colour) of the box that each CFA site's own level is restored over
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
    # The network's full-resolution output carries a small bias that repeats every 2 x 2 pixels (its upsampling): read back at the
    # photosites it differs per site, most visibly between the two greens (G1 and G2 apart by ~15% on a Nikon NEF), which a demosaic
    # turns into a maze pattern. Each site gets back its own local level: the slowly varying part of (original - denoised), which is
    # that bias and not noise, is added to it.
    for sy in (0, 1):
        for sx in (0, 1):
            site = out[sy::2, sx::2]
            site += _box_blur(_box_blur(original[sy::2, sx::2] - site, LEVEL_RADIUS), LEVEL_RADIUS)
    keep = original >= CLIP_KEEP
    out[keep] = original[keep]
    return out


def _box_blur(image, radius):
    """Mean over a (2 radius + 1)-square box, edges repeated (two passes approximate a Gaussian)."""
    import numpy as np

    k = 2 * radius + 1
    padded = np.pad(image.astype(np.float64), radius, mode="edge")
    rows = np.cumsum(np.pad(padded, ((1, 0), (0, 0))), axis=0)
    padded = (rows[k:] - rows[:-k]) / k
    cols = np.cumsum(np.pad(padded, ((0, 0), (1, 0))), axis=1)
    return ((cols[:, k:] - cols[:, :-k]) / k).astype(np.float32)


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
    """Change how much of the denoised samples a file uses, in place (four bytes: near the start of a companion, near the end of
    a denoised raw copy)."""
    value = struct.pack("<f", float(max(0.0, min(100.0, amount))))
    if is_companion(path):
        with open(path, "r+b") as f:
            f.seek(COMPANION_AMOUNT_AT)
            f.write(value)
        return
    if not has_trailer(path):
        raise ValueError("Not a denoised raw file")
    with open(path, "r+b") as f:
        f.seek(-AMOUNT_FROM_END, os.SEEK_END)
        f.write(value)


def read_amount(path: Path) -> float | None:
    if is_companion(path):
        with open(path, "rb") as f:
            f.seek(COMPANION_AMOUNT_AT)
            return struct.unpack("<f", f.read(4))[0]
    if not has_trailer(path):
        return None
    with open(path, "rb") as f:
        f.seek(-AMOUNT_FROM_END, os.SEEK_END)
        return struct.unpack("<f", f.read(4))[0]


# ---- the companion file (photocraft_raw::denoised, "the companion file") -------------------------------------------------------
# Only the denoised samples, kept beside the photo as Camera Raw keeps its AI results in a .acr file: no copy of the raw. A host
# passes the companion where it would pass the raw; PhotoCraft and the preview worker read the original it names and the samples
# together. The samples are on a square-root scale quantised to COMPANION_BITS, split into the four CFA planes, predicted from their
# neighbours (lossless JPEG's median edge detector) and the differences zlib-compressed. At 10 bits the error is about 3% of the
# noise Denoise removes; a 24 MP raw's companion is ~22 MB instead of a 150 MB denoised copy.
COMPANION_MAGIC = b"PCDNCMP1"
COMPANION_AMOUNT_AT = 12
COMPANION_BITS = 10
_ESCAPE = -128


def _median_residuals(codes):
    """``codes`` minus their median-edge prediction from the left, upper and upper-left neighbours (0 outside the plane)."""
    import numpy as np

    p = codes.astype(np.int32)
    left = np.zeros_like(p); left[:, 1:] = p[:, :-1]
    up = np.zeros_like(p); up[1:, :] = p[:-1, :]
    corner = np.zeros_like(p); corner[1:, 1:] = p[:-1, :-1]
    hi, lo = np.maximum(left, up), np.minimum(left, up)
    return p - np.where(corner >= hi, lo, np.where(corner <= lo, hi, left + up - corner))


def companion_bytes(original_name: str, samples, region, sensor, amount: float, bits: int = COMPANION_BITS) -> bytes:
    """A companion for the raw at ``original_name`` (its path relative to the companion's folder, ``/``-separated)."""
    import zlib

    import numpy as np

    x, y, w, h = (int(v) for v in region)
    if w % 2 or h % 2:
        raise ValueError("The denoised region must have even sides")
    top = (1 << bits) - 1
    codes = np.round(np.sqrt(np.clip(np.asarray(samples, np.float32).reshape(h, w), 0.0, 1.0)) * top).astype(np.int32)
    residual = np.concatenate([_median_residuals(codes[sy::2, sx::2]).ravel() for sy in (0, 1) for sx in (0, 1)])
    small = np.abs(residual) <= 127
    stream = np.where(small, residual, _ESCAPE).astype(np.int8).tobytes()
    escapes = residual[~small].astype("<i2").tobytes()
    name = original_name.encode("utf-8")
    head = COMPANION_MAGIC + struct.pack("<If6III", 1, float(amount), x, y, w, h, int(sensor[0]), int(sensor[1]), bits, len(name)) + name
    parts = [zlib.compress(stream, 6), zlib.compress(escapes, 6)]
    return head + b"".join(struct.pack("<Q", len(part)) + part for part in parts)


def write_companion(original: Path, target: Path, samples, region, sensor, amount: float) -> Path:
    """The companion of ``original``'s denoised samples at ``target``, written next to its final name and moved into place."""
    target.parent.mkdir(parents=True, exist_ok=True)
    relative = os.path.relpath(Path(original), Path(target).parent).replace(os.sep, "/")
    partial = target.with_name(target.name + ".partial")
    partial.write_bytes(companion_bytes(relative, samples, region, sensor, amount))
    os.replace(partial, target)
    return target


def read_companion(data: bytes):
    """``(original, amount, region, sensor, samples)`` of a companion (slow: tests and the migration's checks only)."""
    import zlib

    import numpy as np

    if not data.startswith(COMPANION_MAGIC):
        raise ValueError("Not a denoise companion")
    version, amount, x, y, w, h, sw, sh, bits, name_len = struct.unpack_from("<If6III", data, 8)
    at = 48
    original = data[at:at + name_len].decode("utf-8")
    at += name_len
    parts = []
    for _ in range(2):
        (length,) = struct.unpack_from("<Q", data, at)
        parts.append(zlib.decompress(data[at + 8:at + 8 + length]))
        at += 8 + length
    residual = np.frombuffer(parts[0], np.int8).astype(np.int32)
    escapes = iter(np.frombuffer(parts[1], "<i2").astype(int))
    top = (1 << bits) - 1
    out = np.zeros((h, w), np.float32)
    pw, ph = w // 2, h // 2
    k = 0
    for sy in (0, 1):
        for sx in (0, 1):
            codes = np.zeros((ph, pw), np.int64)
            for py in range(ph):
                for px in range(pw):
                    left = codes[py, px - 1] if px else 0
                    up = codes[py - 1, px] if py else 0
                    corner = codes[py - 1, px - 1] if px and py else 0
                    hi, lo = max(left, up), min(left, up)
                    pred = lo if corner >= hi else hi if corner <= lo else left + up - corner
                    r = int(residual[k]); k += 1
                    codes[py, px] = pred + (next(escapes) if r == _ESCAPE else r)
            out[sy::2, sx::2] = (codes / top) ** 2
    return original, amount, (x, y, w, h), (sw, sh), out


def is_companion(path: Path) -> bool:
    try:
        with open(path, "rb") as f:
            return f.read(8) == COMPANION_MAGIC
    except OSError:
        return False


def is_denoised(path: Path) -> bool:
    """A denoised raw: a companion, or (from earlier versions) a raw copy with the samples appended."""
    return is_companion(path) or has_trailer(path)
