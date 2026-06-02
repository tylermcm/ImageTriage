"""Fast deterministic technical-quality signal extraction."""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from typing import Dict

import numpy as np

from app.engine.signals.layers import SignalLayerContext, append_layer_status
from app.engine.signals.models import ImageSignalRecord, LayerStatus, TechnicalSignals


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
        worker_count = _analysis_worker_count(len(items))
        if worker_count <= 1:
            analyzed = (
                (image_id, analyze_technical_quality(Path(record.file_path), max_side=context.max_preview_side))
                for image_id, record in items
            )
        else:
            with ThreadPoolExecutor(max_workers=worker_count) as executor:
                analyzed = list(
                    executor.map(
                        _analyze_technical_record,
                        ((image_id, record.file_path, context.max_preview_side) for image_id, record in items),
                    )
                )

        for image_id, technical_signals in analyzed:
            record = records[image_id]
            updated[image_id] = replace(
                record,
                technical=technical_signals,
            )
        return append_layer_status(updated, status)


def analyze_technical_quality(path: Path, *, max_side: int = 768) -> TechnicalSignals:
    """Analyze one image using low-cost deterministic CV heuristics."""

    try:
        from PIL import Image, ImageOps
    except Exception as exc:  # pragma: no cover - depends on runtime package set
        return TechnicalSignals(status="not_analyzed", reason=f"Pillow unavailable: {exc}")

    try:
        with Image.open(path) as image:
            image = ImageOps.exif_transpose(image)
            image.thumbnail((max_side, max_side))
            rgb = image.convert("RGB")
            array = np.asarray(rgb, dtype=np.float32) / 255.0
    except Exception as exc:
        return TechnicalSignals(status="failed", reason=str(exc))

    if array.size == 0:
        return TechnicalSignals(status="failed", reason="Empty image array.")

    gray = (
        array[:, :, 0] * 0.2126
        + array[:, :, 1] * 0.7152
        + array[:, :, 2] * 0.0722
    ).astype(np.float32, copy=False)

    shadow_clip = float((gray <= 0.02).mean())
    highlight_clip = float((gray >= 0.98).mean())
    mean_luma = float(gray.mean())
    contrast = float(gray.std())
    exposure_status, exposure_score = _exposure_status(
        mean_luma=mean_luma,
        shadow_clip=shadow_clip,
        highlight_clip=highlight_clip,
    )
    sharpness_raw, detail_raw, valid_tiles = _tile_detail_scores(gray)
    noise_score = _noise_estimate(gray)
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


def _analyze_technical_record(item: tuple[str, str, int]) -> tuple[str, TechnicalSignals]:
    image_id, file_path, max_side = item
    return image_id, analyze_technical_quality(Path(file_path), max_side=max_side)


def _analysis_worker_count(item_count: int) -> int:
    if item_count <= 1:
        return 1
    return max(1, min(8, os.cpu_count() or 4, item_count))
