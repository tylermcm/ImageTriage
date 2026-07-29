"""Fast deterministic technical-quality signal extraction."""

from __future__ import annotations

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, fields as dataclass_fields, replace
from pathlib import Path
from typing import Dict

import numpy as np

from app.engine.signals.layers import SignalLayerContext, append_layer_status
from app.engine.signals.models import ImageSignalRecord, LayerStatus, TechnicalSignals
from app.utils.perf_metrics import emit_metric, metrics_enabled


class TechnicalSignalLayer:
    """Extract histogram/detail/noise signals without a learned model."""

    layer_id = "technical"
    display_name = "Technical Quality Layer"
    required_stack_slot = True

    def status(self) -> LayerStatus:
        return LayerStatus(
            layer_id=self.layer_id,
            display_name=self.display_name,
            enabled=True,
            available=_pillow_available(),
            status="ready" if _pillow_available() else "unavailable",
            backend="Pillow + NumPy",
            reason="" if _pillow_available() else "Pillow is not installed in this runtime.",
        )

    def analyze(
        self,
        records: Dict[str, ImageSignalRecord],
        context: SignalLayerContext,
    ) -> Dict[str, ImageSignalRecord]:
        status = self.status()
        updated = dict(records)
        if not status.available:
            return append_layer_status(updated, status)

        items = list(records.items())
        collect = metrics_enabled()

        # Reuse cached results for files that have not changed (keyed by
        # path + mtime + size), so a rerun over the same folder skips the decode
        # entirely — the dominant cost of this stage.
        cache = _load_technical_cache(context.artifacts_dir)
        hits = 0
        to_compute: list[tuple[str, str, str]] = []
        for image_id, record in items:
            key = _technical_cache_key(record.file_path)
            cached = _signals_from_cache(cache.get(key)) if key else None
            if cached is not None:
                updated[image_id] = replace(records[image_id], technical=cached)
                hits += 1
            else:
                to_compute.append((image_id, record.file_path, key))

        worker_count = _analysis_worker_count(len(to_compute))
        work = (
            (image_id, file_path, context.max_preview_side, collect)
            for image_id, file_path, _key in to_compute
        )
        if not to_compute:
            analyzed: list = []
        elif worker_count <= 1:
            analyzed = [_analyze_technical_record(item) for item in work]
        else:
            with ThreadPoolExecutor(max_workers=worker_count) as executor:
                analyzed = list(executor.map(_analyze_technical_record, work))

        key_by_id = {image_id: key for image_id, _fp, key in to_compute}
        cache_dirty = False
        for image_id, technical_signals, _timings in analyzed:
            updated[image_id] = replace(records[image_id], technical=technical_signals)
            key = key_by_id.get(image_id)
            # Only cache file-specific outcomes; "not_analyzed" is a transient
            # environment state (Pillow missing) that should not be pinned.
            if key and technical_signals.status in ("analyzed", "failed"):
                cache[key] = _signals_to_cache(technical_signals)
                cache_dirty = True
        if cache_dirty:
            _save_technical_cache(context.artifacts_dir, cache)
        if collect:
            _emit_technical_timing(
                [t for _id, _sig, t in analyzed if t is not None], worker_count
            )
            emit_metric(
                "ai.script.signals.technical_cache",
                images=len(items),
                cache_hits=hits,
                computed=len(to_compute),
            )
        return append_layer_status(updated, status)


def _mark(timings: dict | None, key: str, start: float) -> None:
    if timings is not None:
        timings[key] = timings.get(key, 0.0) + (time.perf_counter() - start) * 1000.0


def analyze_technical_quality(
    path: Path, *, max_side: int = 768, timings: dict | None = None
) -> TechnicalSignals:
    """Analyze one image from disk using low-cost deterministic CV heuristics.

    When ``timings`` is provided, per-phase durations (ms) and image metadata are
    recorded into it — used to separate the redundant decode from the actual math.
    """

    try:
        from PIL import Image, ImageOps
    except Exception as exc:  # pragma: no cover - depends on runtime package set
        return TechnicalSignals(status="not_analyzed", reason=f"Pillow unavailable: {exc}")

    try:
        decode_start = time.perf_counter() if timings is not None else 0.0
        with Image.open(path) as image:
            if timings is not None:
                timings["source_megapixels"] = round((image.width * image.height) / 1e6, 2)
                timings["ext"] = path.suffix.lower().lstrip(".")
            image = ImageOps.exif_transpose(image)
            image.thumbnail((max_side, max_side))
            rgb = image.convert("RGB")
            array = np.asarray(rgb, dtype=np.float32) / 255.0
        _mark(timings, "decode_ms", decode_start)
    except Exception as exc:
        return TechnicalSignals(status="failed", reason=str(exc))

    return _technical_from_array(array, timings)


def technical_signals_from_image(
    image, *, max_side: int = 768, apply_exif: bool = True
) -> TechnicalSignals:
    """Compute the same signals from an already-decoded PIL image.

    Used by the extract-time fusion so the file is decoded once. ``apply_exif``
    is False there — the embedding loader intentionally works on un-rotated
    pixels, and the technical stats are (exposure/noise exactly, tile-sharpness
    nearly) rotation-invariant, so the scores stay effectively the same.
    """
    try:
        from PIL import ImageOps

        working = ImageOps.exif_transpose(image) if apply_exif else image.copy()
        working.thumbnail((max_side, max_side))
        rgb = working.convert("RGB")
        array = np.asarray(rgb, dtype=np.float32) / 255.0
    except Exception as exc:
        return TechnicalSignals(status="failed", reason=str(exc))
    return _technical_from_array(array, None)


def _technical_from_array(array: np.ndarray, timings: dict | None) -> TechnicalSignals:
    if array.size == 0:
        return TechnicalSignals(status="failed", reason="Empty image array.")

    gray_start = time.perf_counter() if timings is not None else 0.0
    gray = (
        array[:, :, 0] * 0.2126
        + array[:, :, 1] * 0.7152
        + array[:, :, 2] * 0.0722
    ).astype(np.float32, copy=False)
    _mark(timings, "gray_ms", gray_start)

    exposure_start = time.perf_counter() if timings is not None else 0.0
    shadow_clip = float((gray <= 0.02).mean())
    highlight_clip = float((gray >= 0.98).mean())
    mean_luma = float(gray.mean())
    contrast = float(gray.std())
    exposure_status, exposure_score = _exposure_status(
        mean_luma=mean_luma,
        shadow_clip=shadow_clip,
        highlight_clip=highlight_clip,
    )
    _mark(timings, "exposure_ms", exposure_start)

    sharpness_start = time.perf_counter() if timings is not None else 0.0
    sharpness_raw, detail_raw, valid_tiles = _tile_detail_scores(gray)
    _mark(timings, "sharpness_ms", sharpness_start)

    noise_start = time.perf_counter() if timings is not None else 0.0
    noise_score = _noise_estimate(gray)
    _mark(timings, "noise_ms", noise_start)
    confidence = _confidence_label(valid_tiles)

    return TechnicalSignals(
        detail_score=detail_raw,
        sharpness_score=sharpness_raw,
        focus_score=detail_raw,
        motion_blur_score=None,
        noise_score=noise_score,
        exposure_score=exposure_score,
        exposure_status=exposure_status,
        highlight_clip_ratio=highlight_clip,
        shadow_clip_ratio=shadow_clip,
        contrast_score=contrast,
        confidence=confidence,
        status="analyzed",
    )


def _pillow_available() -> bool:
    try:
        import PIL  # noqa: F401
    except Exception:
        return False
    return True


def _exposure_status(*, mean_luma: float, shadow_clip: float, highlight_clip: float) -> tuple[str, float]:
    if highlight_clip >= 0.08 and mean_luma >= 0.62:
        return "overexposed", max(0.0, 1.0 - highlight_clip * 4.0)
    if shadow_clip >= 0.20 and mean_luma <= 0.38:
        return "underexposed", max(0.0, 1.0 - shadow_clip * 2.0)
    return "properly_exposed", 1.0 - min(0.75, highlight_clip + shadow_clip)


def _tile_detail_scores(gray: np.ndarray, *, tile_count: int = 8) -> tuple[float | None, float | None, int]:
    height, width = gray.shape[:2]
    if height < 24 or width < 24:
        return None, None, 0

    tile_height = max(8, height // tile_count)
    tile_width = max(8, width // tile_count)
    scores: list[float] = []
    for y in range(0, height - tile_height + 1, tile_height):
        for x in range(0, width - tile_width + 1, tile_width):
            tile = gray[y : y + tile_height, x : x + tile_width]
            mean = float(tile.mean())
            contrast = float(tile.std())
            if mean <= 0.04 or mean >= 0.96 or contrast <= 0.015:
                continue
            laplacian = _laplacian(tile)
            tenengrad = _tenengrad(tile)
            scores.append(float(laplacian.var() + tenengrad.mean()))

    if not scores:
        return None, None, 0

    score_array = np.asarray(scores, dtype=np.float32)
    upper_score = float(np.percentile(score_array, 85))
    top_count = max(1, int(np.ceil(score_array.size * 0.20)))
    top_score = float(np.sort(score_array)[-top_count:].mean())
    return _bounded_log_score(top_score), _bounded_log_score(upper_score), int(score_array.size)


def _laplacian(tile: np.ndarray) -> np.ndarray:
    center = tile[1:-1, 1:-1] * 4.0
    neighbors = (
        tile[:-2, 1:-1]
        + tile[2:, 1:-1]
        + tile[1:-1, :-2]
        + tile[1:-1, 2:]
    )
    return center - neighbors


def _tenengrad(tile: np.ndarray) -> np.ndarray:
    gx = tile[1:-1, 2:] - tile[1:-1, :-2]
    gy = tile[2:, 1:-1] - tile[:-2, 1:-1]
    return gx * gx + gy * gy


def _noise_estimate(gray: np.ndarray) -> float | None:
    if gray.shape[0] < 5 or gray.shape[1] < 5:
        return None
    local_mean = (
        gray[:-2, :-2]
        + gray[:-2, 1:-1]
        + gray[:-2, 2:]
        + gray[1:-1, :-2]
        + gray[1:-1, 1:-1]
        + gray[1:-1, 2:]
        + gray[2:, :-2]
        + gray[2:, 1:-1]
        + gray[2:, 2:]
    ) / 9.0
    residual = gray[1:-1, 1:-1] - local_mean
    return float(min(1.0, max(0.0, residual.std() * 12.0)))


def _bounded_log_score(value: float) -> float:
    return float(min(1.0, max(0.0, np.log1p(max(0.0, value) * 150.0) / np.log1p(150.0))))


def _confidence_label(valid_tiles: int) -> str:
    if valid_tiles <= 2:
        return "low"
    if valid_tiles <= 8:
        return "medium"
    return "high"


def _analyze_technical_record(
    item: tuple[str, str, int, bool]
) -> tuple[str, TechnicalSignals, dict | None]:
    image_id, file_path, max_side, collect = item
    timings: dict | None = {} if collect else None
    signals = analyze_technical_quality(Path(file_path), max_side=max_side, timings=timings)
    return image_id, signals, timings


_TECHNICAL_PHASES = ("decode_ms", "gray_ms", "exposure_ms", "sharpness_ms", "noise_ms")


def _emit_technical_timing(samples: list[dict], worker_count: int) -> None:
    """Aggregate the per-image phase timings into one summary metric.

    Reports each phase's total plus p50/p90/p99 so the redundant decode
    (``decode_ms``) can be compared against the sharpness/noise math without
    flooding the log with 586 per-image lines.
    """
    if not samples:
        return
    payload: dict[str, object] = {
        "images": len(samples),
        "workers": worker_count,
    }
    for phase in _TECHNICAL_PHASES:
        values = sorted(float(s.get(phase, 0.0)) for s in samples)
        if not values:
            continue
        payload[f"{phase}_total"] = round(sum(values), 1)
        payload[f"{phase}_p50"] = round(_percentile(values, 50), 2)
        payload[f"{phase}_p90"] = round(_percentile(values, 90), 2)
        payload[f"{phase}_p99"] = round(_percentile(values, 99), 2)
    mps = [float(s["source_megapixels"]) for s in samples if "source_megapixels" in s]
    if mps:
        payload["source_mp_p50"] = round(_percentile(sorted(mps), 50), 2)
        payload["source_mp_p99"] = round(_percentile(sorted(mps), 99), 2)
    exts: dict[str, int] = {}
    for s in samples:
        ext = str(s.get("ext") or "?")
        exts[ext] = exts.get(ext, 0) + 1
    payload["exts"] = exts
    emit_metric("ai.script.signals.technical_breakdown", **payload)


def _percentile(sorted_values: list[float], pct: float) -> float:
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return sorted_values[0]
    rank = (pct / 100.0) * (len(sorted_values) - 1)
    low = int(rank)
    frac = rank - low
    if low + 1 >= len(sorted_values):
        return sorted_values[-1]
    return sorted_values[low] + (sorted_values[low + 1] - sorted_values[low]) * frac


def _analysis_worker_count(item_count: int) -> int:
    if item_count <= 1:
        return 1
    return max(1, min(8, os.cpu_count() or 4, item_count))


_TECHNICAL_CACHE_FILENAME = "technical_signals_cache.json"
_TECHNICAL_CACHE_VERSION = 1
_TECHNICAL_FIELDS = frozenset(f.name for f in dataclass_fields(TechnicalSignals))


def _technical_cache_key(file_path: str) -> str:
    """Identity for the cache: path + mtime + size. Empty string (never cached)
    if the file cannot be stat'd."""
    try:
        stat = os.stat(file_path)
    except OSError:
        return ""
    return f"{file_path}|{stat.st_mtime_ns}|{stat.st_size}"


def _load_technical_cache(artifacts_dir) -> dict:
    if not artifacts_dir:
        return {}
    path = Path(artifacts_dir) / _TECHNICAL_CACHE_FILENAME
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict) or data.get("version") != _TECHNICAL_CACHE_VERSION:
        return {}
    entries = data.get("entries")
    return entries if isinstance(entries, dict) else {}


def _save_technical_cache(artifacts_dir, cache: dict) -> None:
    if not artifacts_dir:
        return
    path = Path(artifacts_dir) / _TECHNICAL_CACHE_FILENAME
    payload = {"version": _TECHNICAL_CACHE_VERSION, "entries": cache}
    try:
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
        tmp.replace(path)  # atomic swap so a crash can't leave a half-written cache
    except OSError:
        pass


def populate_technical_cache(artifacts_dir, entries) -> int:
    """Merge extract-time technical results into the shared cache so the signals
    stage reads them as hits and skips the redundant decode. ``entries`` is an
    iterable of (file_path, TechnicalSignals). Returns how many were written."""
    if not artifacts_dir:
        return 0
    cache = _load_technical_cache(artifacts_dir)
    written = 0
    for file_path, signals in entries:
        if signals is None or signals.status not in ("analyzed", "failed"):
            continue
        key = _technical_cache_key(str(file_path))
        if not key:
            continue
        cache[key] = _signals_to_cache(signals)
        written += 1
    if written:
        _save_technical_cache(artifacts_dir, cache)
    return written


def _signals_to_cache(signals: TechnicalSignals) -> dict:
    return asdict(signals)


def _signals_from_cache(entry) -> "TechnicalSignals | None":
    if not isinstance(entry, dict):
        return None
    try:
        return TechnicalSignals(**{k: v for k, v in entry.items() if k in _TECHNICAL_FIELDS})
    except (TypeError, ValueError):
        return None
