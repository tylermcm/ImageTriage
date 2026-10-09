"""Losslessly unpack Nikon Bayer samples into a sensor DNG for PhotoCraft.

This adapter never calls rawpy.postprocess: white balance, exposure, demosaic,
and colour conversion remain editable in PhotoCraft. The original NEF is passed
separately and embedded alongside this self-contained decode source.
"""
from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path
import struct
import tempfile
import threading
import time

ADAPTER_VERSION = 3
MAX_PIXELS = 100_000_000


def _rationals(values, *, signed=False):
    code = "ii" if signed else "II"
    result = bytearray()
    for value in values:
        value = float(value)
        if not (-2000 <= value <= 2000) or (not signed and value < 0):
            raise ValueError("Invalid RAW calibration value")
        result.extend(struct.pack("<" + code, round(value * 1_000_000), 1_000_000))
    return bytes(result)


def _write_dng(file, raw):
    import numpy as np

    pixels = raw.raw_image_visible
    if pixels.ndim != 2 or pixels.dtype != np.uint16 or not (16 <= pixels.size <= MAX_PIXELS):
        raise ValueError("PhotoCraft requires a bounded 16-bit Bayer sensor plane")
    height, width = pixels.shape
    pattern = raw.raw_pattern
    if pattern is None or pattern.shape != (2, 2) or raw.color_desc != b"RGBG":
        raise ValueError("This RAW sensor layout is not supported by the PhotoCraft adapter")
    # raw_pattern starts at the uncropped sensor origin. Shift by visible margins.
    colors = [int(pattern[(y + raw.sizes.top_margin) % 2, (x + raw.sizes.left_margin) % 2]) for y in range(2) for x in range(2)]
    if sorted(colors) != [0, 1, 2, 3]:
        raise ValueError("Invalid Bayer colour pattern")
    black = [raw.black_level_per_channel[c] for c in colors]
    white = raw.camera_white_level_per_channel
    white = [white[c] for c in (0, 1, 2)] if white is not None else [raw.white_level] * 3
    if any(not (0 < float(v) <= 65535) for v in white) or any(float(v) >= min(white) for v in black):
        raise ValueError("Invalid RAW black/white levels")
    if any(v != white[0] for v in white):
        raise ValueError("Per-colour sensor saturation is not supported by this Bayer DNG adapter")
    wb = [float(v) for v in raw.camera_whitebalance[:3]]
    if any(not (0 < v < 1e6) for v in wb):
        raise ValueError("RAW has no valid as-shot white balance")
    # LibRaw cam_xyz is DNG ColorMatrix (XYZ -> camera), despite the API name.
    # Runtime metadata only; no camera tables or third-party decoder code copied.
    matrix = np.asarray(raw.rgb_xyz_matrix[:3], dtype=np.float64)
    if matrix.shape != (3, 3) or not np.isfinite(matrix).all() or abs(np.linalg.det(matrix)) < 1e-8:
        raise ValueError("RAW has no usable camera colour calibration")
    orientation = {0: 1, 3: 3, 5: 8, 6: 6}.get(raw.sizes.flip)
    if orientation is None:
        raise ValueError("Unsupported RAW orientation")
    crop_width = getattr(raw.sizes, "crop_width", 0)
    crop_height = getattr(raw.sizes, "crop_height", 0)
    crop_x = getattr(raw.sizes, "crop_left_margin", 0) - raw.sizes.left_margin
    crop_y = getattr(raw.sizes, "crop_top_margin", 0) - raw.sizes.top_margin
    if not crop_width or not crop_height:
        crop_x, crop_y, crop_width, crop_height = 0, 0, width, height
    if crop_x < 0 or crop_y < 0 or crop_x + crop_width > width or crop_y + crop_height > height:
        raise ValueError("Invalid RAW default crop")
    entries = []

    def tag(id, type, count, data):
        entries.append((id, type, count, data))

    def shorts(id, values):
        tag(id, 3, len(values), struct.pack("<" + "H" * len(values), *values))

    def longs(id, values):
        tag(id, 4, len(values), struct.pack("<" + "I" * len(values), *values))

    longs(254, [0]); longs(256, [width]); longs(257, [height])
    shorts(258, [16]); shorts(259, [1]); shorts(262, [32803])
    longs(273, [0]); shorts(274, [orientation]); shorts(277, [1])
    longs(278, [height]); longs(279, [pixels.size * 2]); shorts(284, [1])
    shorts(33421, [2, 2]); tag(33422, 1, 4, bytes(1 if c == 3 else c for c in colors))
    tag(50706, 1, 4, bytes([1, 4, 0, 0])); tag(50707, 1, 4, bytes([1, 1, 0, 0]))
    model = b"ImageTriage sensor adapter\0"; tag(50708, 2, len(model), model)
    tag(50710, 1, 3, bytes([0, 1, 2])); shorts(50711, [1]); shorts(50713, [2, 2])
    tag(50714, 5, 4, _rationals(black)); longs(50717, [round(white[0])])
    longs(50719, [crop_x, crop_y]); longs(50720, [crop_width, crop_height])
    tag(50721, 10, 9, _rationals(matrix.ravel(), signed=True))
    tag(50728, 5, 3, _rationals([wb[1] / v for v in wb])); shorts(50778, [21])
    longs(50829, [0, 0, height, width])
    entries.sort()
    offset = 8 + 2 + len(entries) * 12 + 4
    extra = bytearray(); records = []
    for id, type, count, data in entries:
        if len(data) > 4:
            if len(extra) % 2:
                extra.append(0)
            payload = struct.pack("<I", offset + len(extra)); extra.extend(data)
        else:
            payload = data.ljust(4, b"\0")
        records.append((id, type, count, payload))
    if len(extra) % 2:
        extra.append(0)
    sensor_offset = offset + len(extra)
    file.write(b"II*\0" + struct.pack("<I", 8) + struct.pack("<H", len(records)))
    for id, type, count, payload in records:
        file.write(struct.pack("<HHI", id, type, count))
        file.write(struct.pack("<I", sensor_offset) if id == 273 else payload)
    file.write(struct.pack("<I", 0)); file.write(extra)
    # No black subtraction, WB multiplication, gamma, demosaic, or RGB output.
    file.write(np.ascontiguousarray(pixels, dtype="<u2").tobytes())


MAX_CACHED_SENSORS = 4
MAX_CACHED_BYTES = 512 * 1024 * 1024

_state_lock = threading.Lock()
_inflight: dict[str, threading.Lock] = {}
_stats = {"hits": 0, "misses": 0, "joined": 0, "unpack_ms": 0.0}


def cache_stats() -> dict:
    with _state_lock:
        return dict(_stats)


def _count(name: str, amount: float = 1) -> None:
    with _state_lock:
        _stats[name] += amount


def _touch(path: Path) -> None:
    try:
        os.utime(path, None)
    except OSError:
        pass


def materialize_sensor_dng(path: str, cache_dir: Path) -> Path:
    import rawpy

    source = Path(path).resolve()
    before = source.stat()
    key = hashlib.sha256(f"{ADAPTER_VERSION}:{rawpy.__version__}:{source}:{before.st_size}:{before.st_mtime_ns}".encode()).hexdigest()
    cache_dir.mkdir(parents=True, exist_ok=True)
    target = cache_dir / (key + ".sensor.dng")
    if target.is_file():
        _touch(target)
        _count("hits")
        return target
    with _state_lock:
        guard = _inflight.setdefault(key, threading.Lock())
    # A prefetch and the selected photo may ask for the same file; the second waits for
    # the first instead of unpacking it twice.
    with guard:
        if target.is_file():
            _touch(target)
            _count("joined")
            return target
        started = time.perf_counter()
        temporary = None
        try:
            with rawpy.imread(str(source)) as raw, tempfile.NamedTemporaryFile(dir=cache_dir, suffix=".tmp", delete=False) as file:
                temporary = Path(file.name)
                _write_dng(file, raw)
            after = source.stat()
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise ValueError("RAW changed while its sensor data was being unpacked")
            os.replace(temporary, target)
            _count("misses")
            _count("unpack_ms", (time.perf_counter() - started) * 1000)
            return target
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
            with _state_lock:
                _inflight.pop(key, None)


def trim_sensor_cache(cache_dir: Path, *, keep: Path | None = None, max_files: int = MAX_CACHED_SENSORS, max_bytes: int = MAX_CACHED_BYTES) -> None:
    """Keep the most recently used unpacked sensors within a small file and byte budget.

    Only called after a foreground open, never from a prefetch, so a file that was just
    handed to PhotoCraft cannot be evicted underneath it.
    """
    try:
        found = [(p.stat(), p) for p in cache_dir.glob("*.sensor.dng") if p.is_file()]
    except OSError:
        return
    found.sort(key=lambda item: item[0].st_mtime_ns, reverse=True)
    # The file just handed to PhotoCraft is always kept and counts against the budget first.
    kept = sum(1 for _, path in found if path == keep)
    total = sum(stat.st_size for stat, path in found if path == keep)
    for stat, path in found:
        if path == keep:
            continue
        if kept < max_files and total + stat.st_size <= max_bytes:
            kept += 1
            total += stat.st_size
            continue
        try:
            path.unlink(missing_ok=True)
        except OSError:
            logging.getLogger(__name__).warning("Could not evict cached RAW sensor %s", path)
