"""Cheap-pass feature extraction for the stack-first cascade (docs/ai_v5_implementation.md, work package 2).

Turns image paths into ``FrameFeatures`` from the embedded JPEG preview, never from a RAW decode: ``raw_embedded_jpeg.extract_embedded_jpeg``
supplies the preview bytes and the container's EXIF orientation, Pillow decodes them at reduced size (JPEG draft mode, then a box resize to about
1280 px on the long edge), and everything is measured on that one reduced image. Plain JPEG/PNG/TIFF files are opened directly. No Qt, no torch,
no OpenCV.

Features, per ``model.FrameFeatures``:

* ``phash`` is the cascade's hash: 32x32 box-averaged luma, orthonormal 2-D DCT, the 8x8 low-frequency block with DC dropped, bits above the median
  of the remaining 63. It is the same integer layout as ``perceptual_hash.compute_phash`` (whose DC slot is a zero bit) and the same as the scratch
  hash the thresholds in ``CascadeSettings`` were calibrated on, but it is resampled from the reduced preview with a box filter (equal to OpenCV's
  INTER_AREA) rather than Lanczos, because Lanczos from a small draft shifts distances by a few bits.
* ``centre_sharpness`` is the variance of the 4-neighbour Laplacian over the central half (rows and columns from 1/4 to 3/4) of the luma image.
  Its scale depends on the preview size, so compare it only within a burst, which is all the cascade does.
* ``clipped`` is the fraction of pixels with any RGB channel >= 250; ``extremes`` the fraction of luma pixels <= 5 or >= 250.
* ``blink`` comes from an optional ``blink_lookup(path) -> bool | None``. The face index does not produce blink yet (``quality/face.py`` returns
  ``None`` until the EAR calibration), so with no lookup, or a lookup that returns ``None``, blink is ``False``. It is never cached here.

Results are cached by file signature (size and mtime) in memory and, if a path is given, in a small JSON file. A frame that cannot be read
comes back as ``None`` in its slot; the list always lines up with the input paths.
"""

from __future__ import annotations

import io
import json
import os
import threading
import time
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

from ..formats import RAW_SUFFIXES, suffix_for_path
from ..perceptual_hash import _dct_2d
from ..raw_embedded_jpeg import extract_embedded_jpeg
from .model import FrameFeatures

PREVIEW_LONG_EDGE = 1280
_HASH_SIZE = 8
_SAMPLE_SIZE = 32
_CLIP_LEVEL = 250
_DARK_LEVEL = 5
_CACHE_SCHEMA = 1
# Bump when the measurements change so stale cache rows are ignored.
_ALGORITHM_VERSION = 1

ProgressCallback = Callable[[int, int], None]
CancelCheck = Callable[[], bool]
BlinkLookup = Callable[[str], "bool | None"]
# Optional hook to supply preview bytes from the app's preview caches: path -> (jpeg_bytes, exif_orientation) or None to fall through.
PreviewSource = Callable[[str], "tuple[bytes, int] | None"]


@dataclass(slots=True, frozen=True)
class ExtractStats:
    total: int
    cached: int
    computed: int
    failed: int
    cancelled: bool
    elapsed_ms: float
    compute_ms: float  # summed across workers, so it can exceed elapsed_ms


@dataclass(slots=True, frozen=True)
class ExtractResult:
    features: list[FrameFeatures | None]  # same order as the input paths; None where the frame was unreadable or skipped by cancellation
    stats: ExtractStats


@dataclass(slots=True, frozen=True)
class _Measures:
    phash: int
    centre_sharpness: float
    clipped: float
    extremes: float


class FeatureCache:
    """File-signature cache of the measures (not blink). Thread-safe; ``save`` writes the JSON file if one was given."""

    def __init__(self, path: str | Path | None = None) -> None:
        self._path = Path(path).expanduser() if path is not None else None
        self._lock = threading.Lock()
        self._entries: dict[str, dict[str, object]] = {}
        self._dirty = False
        if self._path is not None:
            self._load()

    def get(self, path: str) -> _Measures | None:
        signature = _file_signature(path)
        if signature is None:
            return None
        with self._lock:
            entry = self._entries.get(_cache_key(path))
        if not entry or entry.get("size") != signature[0] or entry.get("mtime_ns") != signature[1] or entry.get("v") != _ALGORITHM_VERSION:
            return None
        try:
            return _Measures(
                phash=int(entry["phash"]),  # type: ignore[arg-type]
                centre_sharpness=float(entry["sharp"]),  # type: ignore[arg-type]
                clipped=float(entry["clipped"]),  # type: ignore[arg-type]
                extremes=float(entry["extremes"]),  # type: ignore[arg-type]
            )
        except (KeyError, TypeError, ValueError):
            return None

    def put(self, path: str, signature: tuple[int, int], measures: _Measures) -> None:
        entry = {
            "size": signature[0],
            "mtime_ns": signature[1],
            "v": _ALGORITHM_VERSION,
            "phash": measures.phash,
            "sharp": measures.centre_sharpness,
            "clipped": measures.clipped,
            "extremes": measures.extremes,
        }
        with self._lock:
            self._entries[_cache_key(path)] = entry
            self._dirty = True

    def save(self) -> None:
        if self._path is None:
            return
        with self._lock:
            if not self._dirty:
                return
            payload = {"schema_version": _CACHE_SCHEMA, "entries": dict(self._entries)}
            self._dirty = False
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self._path.with_suffix(self._path.suffix + ".tmp")
            temporary.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
            temporary.replace(self._path)
        except OSError:
            pass

    def _load(self) -> None:
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))  # type: ignore[union-attr]
        except (OSError, ValueError):
            return
        if isinstance(payload, dict) and payload.get("schema_version") == _CACHE_SCHEMA and isinstance(payload.get("entries"), dict):
            self._entries = payload["entries"]


def extract_features(
    paths: Sequence[str],
    *,
    cache: FeatureCache | None = None,
    blink_lookup: BlinkLookup | None = None,
    preview_source: PreviewSource | None = None,
    progress: ProgressCallback | None = None,
    cancelled: CancelCheck | None = None,
    max_workers: int | None = None,
) -> ExtractResult:
    """Extract ``FrameFeatures`` for ``paths`` (capture order), keyed by the path string as given.

    ``progress(done, total)`` is called from the calling thread. ``cancelled()`` is polled before each frame starts; frames not yet started come
    back ``None`` and ``stats.cancelled`` is set. Workers default to ``min(4, cpu_count)``: decode is mostly in Pillow's C code, which releases
    the GIL, but the files are often on one disk.
    """
    started = time.perf_counter()
    total = len(paths)
    cache = cache if cache is not None else FeatureCache()
    measures: list[_Measures | None] = [None] * total
    done = 0
    cached_count = 0
    failed = 0
    computed = 0
    compute_ms = 0.0
    was_cancelled = False

    def report() -> None:
        if progress is not None:
            progress(done, total)

    todo: list[int] = []
    for index, path in enumerate(paths):
        hit = cache.get(path)
        if hit is not None:
            measures[index] = hit
            cached_count += 1
            done += 1
            report()
        else:
            todo.append(index)

    def work(index: int) -> tuple[int, _Measures | None, float, bool]:
        if cancelled is not None and cancelled():
            return index, None, 0.0, True
        begin = time.perf_counter()
        signature = _file_signature(paths[index])
        result = _measure_path(paths[index], preview_source) if signature is not None else None
        if result is not None and signature is not None:
            cache.put(paths[index], signature, result)
        return index, result, (time.perf_counter() - begin) * 1000.0, False

    if todo:
        workers = max(1, min(max_workers if max_workers is not None else min(4, os.cpu_count() or 2), len(todo)))
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="cascade-extract") as pool:
            futures = [pool.submit(work, index) for index in todo]
            for future in as_completed(futures):
                index, result, elapsed, skipped = future.result()
                compute_ms += elapsed
                if skipped:
                    was_cancelled = True
                    continue
                measures[index] = result
                if result is None:
                    failed += 1
                else:
                    computed += 1
                done += 1
                report()
    cache.save()

    features: list[FrameFeatures | None] = []
    for path, item in zip(paths, measures):
        if item is None:
            features.append(None)
            continue
        blink = False
        if blink_lookup is not None:
            try:
                blink = bool(blink_lookup(path))
            except Exception:  # a broken face index must not stop the cheap pass
                blink = False
        features.append(
            FrameFeatures(
                key=path,
                phash=item.phash,
                centre_sharpness=item.centre_sharpness,
                clipped=item.clipped,
                extremes=item.extremes,
                blink=blink,
            )
        )
    stats = ExtractStats(
        total=total,
        cached=cached_count,
        computed=computed,
        failed=failed,
        cancelled=was_cancelled,
        elapsed_ms=(time.perf_counter() - started) * 1000.0,
        compute_ms=compute_ms,
    )
    return ExtractResult(features=features, stats=stats)


def measure_image(image: Image.Image) -> _Measures:
    """Measure an already decoded, already oriented RGB image (exposed for tests and for callers that hold pixels)."""
    rgb = _reduce(image.convert("RGB"))
    rgb_array = np.asarray(rgb)
    gray = np.asarray(rgb.convert("L"))
    return _Measures(
        phash=_phash_from_image(rgb.convert("L")),
        centre_sharpness=_centre_laplacian_variance(gray),
        clipped=float((rgb_array >= _CLIP_LEVEL).any(axis=2).mean()),
        extremes=float(((gray <= _DARK_LEVEL) | (gray >= _CLIP_LEVEL)).mean()),
    )


def _measure_path(path: str, preview_source: PreviewSource | None) -> _Measures | None:
    try:
        image = _load_preview(path, preview_source)
        if image is None:
            return None
        with image:
            return measure_image(image)
    except (OSError, ValueError, SyntaxError, Image.DecompressionBombError):
        return None


def _load_preview(path: str, preview_source: PreviewSource | None) -> Image.Image | None:
    """Decode the embedded or plain preview at reduced size and apply EXIF orientation. Never touches a RAW decoder."""
    if preview_source is not None:
        supplied = preview_source(path)
        if supplied is not None:
            payload, orientation = supplied
            return _orient(_decode(io.BytesIO(payload)), orientation)
    if suffix_for_path(path) in RAW_SUFFIXES:
        embedded = extract_embedded_jpeg(path)
        if embedded is None:
            return None
        # As in imaging._load_raw_image, the container's orientation is authoritative; the embedded JPEG's own tag is ignored.
        return _orient(_decode(io.BytesIO(embedded.payload)), embedded.orientation)
    with Image.open(path) as probe:
        orientation = int(probe.getexif().get(0x0112, 1) or 1)
    return _orient(_decode(path), orientation)


def _decode(source: str | io.BytesIO) -> Image.Image:
    image = Image.open(source)
    # No-op for non-JPEG; a JPEG decodes at 1/2, 1/4 or 1/8 scale while both sides stay >= the request, so ask for the fitted size.
    scale = min(1.0, PREVIEW_LONG_EDGE / max(image.size))
    image.draft("RGB", (max(1, round(image.width * scale)), max(1, round(image.height * scale))))
    image.load()
    return image


def _orient(image: Image.Image, orientation: int) -> Image.Image:
    transpose = {
        2: Image.Transpose.FLIP_LEFT_RIGHT,
        3: Image.Transpose.ROTATE_180,
        4: Image.Transpose.FLIP_TOP_BOTTOM,
        5: Image.Transpose.TRANSPOSE,
        6: Image.Transpose.ROTATE_270,
        7: Image.Transpose.TRANSVERSE,
        8: Image.Transpose.ROTATE_90,
    }.get(orientation)
    if transpose is None:
        return image
    rotated = image.transpose(transpose)
    image.close()
    return rotated


def _reduce(image: Image.Image) -> Image.Image:
    longest = max(image.size)
    if longest <= PREVIEW_LONG_EDGE:
        return image
    scale = PREVIEW_LONG_EDGE / longest
    size = (max(1, round(image.width * scale)), max(1, round(image.height * scale)))
    return image.resize(size, Image.Resampling.BOX)


def _phash_from_image(gray: Image.Image) -> int:
    sample = gray.resize((_SAMPLE_SIZE, _SAMPLE_SIZE), Image.Resampling.BOX)
    return phash_from_sample(np.asarray(sample, dtype=np.float32))


def phash_from_sample(sample: np.ndarray) -> int:
    """Hash a 32x32 luma sample: low-frequency DCT block, DC dropped, median threshold. Same integer layout as compute_phash."""
    low = _dct_2d(sample)[:_HASH_SIZE, :_HASH_SIZE].copy()
    comparable = low.flatten()[1:]
    median = float(np.median(comparable))
    low[0, 0] = median
    value = 0
    for bit in low.flatten() > median:
        value = (value << 1) | int(bit)
    return value


def _centre_laplacian_variance(gray: np.ndarray) -> float:
    height, width = gray.shape
    if height < 3 or width < 3:
        return 0.0
    g = gray.astype(np.float32)
    laplacian = g[:-2, 1:-1] + g[2:, 1:-1] + g[1:-1, :-2] + g[1:-1, 2:] - 4.0 * g[1:-1, 1:-1]  # laplacian[y, x] is at gray[y + 1, x + 1]
    top, bottom = height // 4, (3 * height) // 4
    left, right = width // 4, (3 * width) // 4
    centre = laplacian[max(0, top - 1) : max(1, bottom - 1), max(0, left - 1) : max(1, right - 1)]
    return float(centre.var(dtype=np.float64)) if centre.size else 0.0


def _file_signature(path: str) -> tuple[int, int] | None:
    try:
        stat = os.stat(path)
    except OSError:
        return None
    return int(stat.st_size), int(stat.st_mtime_ns)


def _cache_key(path: str) -> str:
    try:
        return str(Path(path).expanduser().resolve()).casefold()
    except OSError:
        return str(path).casefold()
