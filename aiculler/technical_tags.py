from __future__ import annotations

import csv
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np
from PIL import Image

from aiculler.storage import SQLiteFeatureStore


_LUMA_WEIGHTS = np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)


@dataclass(frozen=True)
class ImageTechnicalMetrics:
    focus_score: float
    motion_blur_score: float
    highlight_clip_ratio: float
    shadow_clip_ratio: float
    contrast_score: float
    noise_score: float
    harsh_light_score: float


@dataclass(frozen=True)
class TagPenaltyConfig:
    tag: str
    metric: str
    direction: str
    threshold: float
    weight: float
    k: float


@dataclass(frozen=True)
class TagPenaltyRecord:
    image_id: int
    filename: str
    source_path: str
    base_score: float
    adjusted_score: float
    tag_penalty: float
    triggered_tags: str
    metrics: ImageTechnicalMetrics


@dataclass(frozen=True)
class TechnicalMetricCacheStats:
    total: int
    cache_hits: int
    cache_misses: int
    failures: int
    workers: int
    phase_total_seconds: dict[str, float] = field(default_factory=dict)
    phase_average_seconds: dict[str, float] = field(default_factory=dict)


class TechnicalTagScorer:
    """Apply measurable technical reject-tag penalties to existing scores."""

    def __init__(
        self,
        store: SQLiteFeatureStore,
        configs: list[TagPenaltyConfig],
        *,
        penalty_weight: float = 0.50,
        base_column: str = "final_score",
    ):
        self.store = store
        self.configs = configs
        self.penalty_weight = float(penalty_weight)
        if base_column not in {"final_score", "technical_score"}:
            raise ValueError("base_column must be final_score or technical_score")
        self.base_column = base_column

    def score(self, tags: list[str]) -> list[TagPenaltyRecord]:
        selected = [config for config in self.configs if config.tag in set(tags)]
        if not selected:
            raise ValueError(f"No matching tag configs for: {', '.join(tags)}")

        rows = self.store.list_images(require_embedding=True)
        metrics_by_id, _stats = compute_technical_metrics_batch(
            self.store,
            [
                (int(row["id"]), Path(row["preview_path"] or row["source_path"]))
                for row in rows
            ],
        )
        records: list[TagPenaltyRecord] = []
        updates: dict[int, tuple[float, float, str, float]] = {}
        for row in rows:
            image_id = int(row["id"])
            metrics = metrics_by_id.get(image_id)
            if metrics is None:
                continue
            tag_penalty, triggered_tags = compute_tag_penalty(metrics, selected)
            base_score = row[self.base_column]
            if self.base_column == "final_score" and row["tag_base_score"] is not None:
                base_score = row["tag_base_score"]
            if base_score is None:
                base_score = row["technical_score"] or 0.0
            base_score = float(base_score)
            adjusted_score = base_score - self.penalty_weight * tag_penalty
            tag_flags = ",".join(triggered_tags)
            updates[image_id] = (base_score, tag_penalty, tag_flags, adjusted_score)
            records.append(
                TagPenaltyRecord(
                    image_id=image_id,
                    filename=Path(row["source_path"]).name,
                    source_path=row["source_path"],
                    base_score=base_score,
                    adjusted_score=adjusted_score,
                    tag_penalty=tag_penalty,
                    triggered_tags=tag_flags,
                    metrics=metrics,
                )
            )

        self.store.update_tag_scores(updates)
        return sorted(records, key=lambda record: record.adjusted_score, reverse=True)


def compute_technical_metrics_batch(
    store: SQLiteFeatureStore,
    items: list[tuple[int, Path]],
    *,
    max_workers: int | None = None,
    progress_callback: Callable[[int, int, str], None] | None = None,
) -> tuple[dict[int, ImageTechnicalMetrics], TechnicalMetricCacheStats]:
    metrics_by_id: dict[int, ImageTechnicalMetrics] = {}
    missing: list[tuple[int, Path, str, int, int]] = []
    cache_hits = 0
    failures = 0
    phase_values: dict[str, list[float]] = {}
    for image_id, image_path in items:
        signature = _path_signature(image_path)
        if signature is None:
            failures += 1
            continue
        path_key, size, mtime_ns = signature
        cached = store.get_technical_metrics_cache(path_key)
        if cached is not None and int(cached["size"]) == size and int(cached["mtime_ns"]) == mtime_ns:
            try:
                metrics_by_id[image_id] = technical_metrics_from_dict(json.loads(cached["metrics_json"]))
                cache_hits += 1
                continue
            except (TypeError, ValueError, json.JSONDecodeError):
                pass
        missing.append((image_id, image_path, path_key, size, mtime_ns))

    total = len(items)
    completed = len(metrics_by_id) + failures
    workers = _technical_worker_count(max_workers=max_workers, item_count=len(missing))
    if missing:
        if workers <= 1:
            for item in missing:
                image_id, image_path, path_key, size, mtime_ns, metrics, timings = _compute_metrics_cache_row(item)
                _append_phase_timings(phase_values, timings)
                if metrics is None:
                    failures += 1
                else:
                    store.set_technical_metrics_cache(
                        path_key=path_key,
                        path=image_path,
                        size=size,
                        mtime_ns=mtime_ns,
                        metrics_json=json.dumps(technical_metrics_to_dict(metrics), sort_keys=True, separators=(",", ":")),
                    )
                    metrics_by_id[image_id] = metrics
                completed += 1
                if progress_callback is not None:
                    progress_callback(completed, total, image_path.name)
        else:
            with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="tag-metrics") as executor:
                futures = [executor.submit(_compute_metrics_cache_row, item) for item in missing]
                for future in as_completed(futures):
                    image_id, image_path, path_key, size, mtime_ns, metrics, timings = future.result()
                    _append_phase_timings(phase_values, timings)
                    if metrics is None:
                        failures += 1
                    else:
                        store.set_technical_metrics_cache(
                            path_key=path_key,
                            path=image_path,
                            size=size,
                            mtime_ns=mtime_ns,
                            metrics_json=json.dumps(technical_metrics_to_dict(metrics), sort_keys=True, separators=(",", ":")),
                        )
                        metrics_by_id[image_id] = metrics
                    completed += 1
                    if progress_callback is not None:
                        progress_callback(completed, total, image_path.name)
    return metrics_by_id, TechnicalMetricCacheStats(
        total=total,
        cache_hits=cache_hits,
        cache_misses=len(missing),
        failures=failures,
        workers=workers,
        phase_total_seconds={
            phase: round(sum(values), 6)
            for phase, values in sorted(phase_values.items())
        },
        phase_average_seconds={
            phase: round(sum(values) / len(values), 6)
            for phase, values in sorted(phase_values.items())
            if values
        },
    )


def compute_technical_metrics(
    image_path: str | Path,
    *,
    timings: dict[str, float] | None = None,
) -> ImageTechnicalMetrics:
    def measured(name: str, operation):
        started_at = time.perf_counter()
        result = operation()
        if timings is not None:
            timings[name] = time.perf_counter() - started_at
        return result

    total_started_at = time.perf_counter()
    decode_started_at = time.perf_counter()
    with Image.open(image_path) as opened:
        img = opened.convert("RGB")
    # Larger sample window: 1024-px thumbs lose almost all evidence of
    # camera-shake / motion blur from a 24MP raw. 2048 px preserves enough
    # high-frequency content for Laplacian variance to discriminate sharp
    # from soft frames without making per-image cost unreasonable.
    img.thumbnail((2048, 2048), Image.Resampling.BILINEAR)
    if timings is not None:
        timings["decode_resize"] = time.perf_counter() - decode_started_at
    rgb_uint8 = measured("rgb_uint8", lambda: np.asarray(img, dtype=np.uint8))
    def rgb_float_values() -> np.ndarray:
        result = rgb_uint8.astype(np.float32)
        np.divide(result, 255.0, out=result)
        return result

    rgb = measured("rgb_float", rgb_float_values)
    gray = measured(
        "grayscale",
        lambda: rgb @ _LUMA_WEIGHTS,
    )

    # Focus / sharpness: variance of the Laplacian (the canonical sharpness
    # metric in OpenCV / scikit-image). The first-derivative magnitude we used
    # previously gets fooled by high-frequency static texture (Spanish moss,
    # tree foliage), saturating to "sharp" even when the image is clearly
    # motion-blurred. The Laplacian (second derivative) falls off much faster
    # under any kind of blur because blur smooths the local intensity curve.
    def laplacian_variance_value() -> float:
        laplacian = gray[:-2, 1:-1] + gray[2:, 1:-1]
        np.add(laplacian, gray[1:-1, :-2], out=laplacian)
        np.add(laplacian, gray[1:-1, 2:], out=laplacian)
        np.subtract(laplacian, 4.0 * gray[1:-1, 1:-1], out=laplacian)
        return float(np.var(laplacian))

    laplacian_variance = measured("laplacian", laplacian_variance_value)
    # Multiplier 80 maps a sharp landscape (lap_var ~0.015+) to ~1.0 and a
    # noticeably soft frame (lap_var ~0.003) to ~0.24 (below the default
    # outoffocus threshold of 0.30). Tune via tag_penalties.csv if needed.
    focus_score = float(np.clip(laplacian_variance * 80.0, 0.0, 1.0))

    # First-derivative gradients still drive motion-blur direction. With the
    # new Laplacian-based focus_score being trustworthy now, the original
    # softness * directional_balance formula gives clean signal: it fires
    # when the image is both visibly soft AND has axis-asymmetric edges
    # (the signature of a camera pan or shake along one axis).
    def gradient_values() -> tuple[float, float]:
        gx = np.diff(gray, axis=1)
        gy = np.diff(gray, axis=0)
        mean_gx = np.mean(np.abs(gx))
        mean_gy = np.mean(np.abs(gy))
        edge_energy_value = float(mean_gx + mean_gy)
        directional_balance_value = (
            abs(float(mean_gx) - float(mean_gy)) / (edge_energy_value + 1e-6)
        )
        return edge_energy_value, directional_balance_value

    edge_energy, directional_balance = measured("gradients", gradient_values)
    motion_blur_score = float(np.clip((1.0 - focus_score) * directional_balance, 0.0, 1.0))

    def clipping_contrast_values() -> tuple[float, float, float]:
        max_channel = np.maximum(rgb_uint8[..., 0], rgb_uint8[..., 1])
        np.maximum(max_channel, rgb_uint8[..., 2], out=max_channel)
        return (
            float(np.mean(max_channel >= 252)),
            float(np.mean(gray <= 0.025)),
            float(np.clip(np.std(gray) * 4.0, 0.0, 1.0)),
        )

    highlight_clip_ratio, shadow_clip_ratio, contrast_score = measured(
        "clipping_contrast",
        clipping_contrast_values,
    )

    blurred = measured("local_mean", lambda: local_mean(gray))
    noise_score = measured(
        "noise",
        lambda: float(np.clip(np.std(gray - blurred) * 8.0, 0.0, 1.0)),
    )

    def harsh_light_values() -> tuple[float, float, float]:
        p50, p99 = np.percentile(gray, [50, 99])
        return (
            float(np.mean(gray >= 0.90)),
            float(p50),
            float(p99),
        )

    bright_ratio, p50, p99 = measured("harsh_light_inputs", harsh_light_values)
    highlight_severity = min(1.0, highlight_clip_ratio * 18.0)
    bright_severity = min(1.0, bright_ratio * 4.0)
    glare_gap = max(0.0, p99 - p50 - 0.30)
    harsh_light_score = float(np.clip(0.55 * highlight_severity + 0.30 * bright_severity + 0.15 * glare_gap * 2.0, 0.0, 1.0))

    result = ImageTechnicalMetrics(
        focus_score=focus_score,
        motion_blur_score=motion_blur_score,
        highlight_clip_ratio=highlight_clip_ratio,
        shadow_clip_ratio=shadow_clip_ratio,
        contrast_score=contrast_score,
        noise_score=noise_score,
        harsh_light_score=harsh_light_score,
    )
    if timings is not None:
        timings["total"] = time.perf_counter() - total_started_at
    return result


def technical_metrics_to_dict(metrics: ImageTechnicalMetrics) -> dict[str, float]:
    return {
        "focus_score": float(metrics.focus_score),
        "motion_blur_score": float(metrics.motion_blur_score),
        "highlight_clip_ratio": float(metrics.highlight_clip_ratio),
        "shadow_clip_ratio": float(metrics.shadow_clip_ratio),
        "contrast_score": float(metrics.contrast_score),
        "noise_score": float(metrics.noise_score),
        "harsh_light_score": float(metrics.harsh_light_score),
    }


def technical_metrics_from_dict(payload: dict[str, object]) -> ImageTechnicalMetrics:
    return ImageTechnicalMetrics(
        focus_score=float(payload["focus_score"]),
        motion_blur_score=float(payload["motion_blur_score"]),
        highlight_clip_ratio=float(payload["highlight_clip_ratio"]),
        shadow_clip_ratio=float(payload["shadow_clip_ratio"]),
        contrast_score=float(payload["contrast_score"]),
        noise_score=float(payload["noise_score"]),
        harsh_light_score=float(payload["harsh_light_score"]),
    )


def _compute_metrics_cache_row(
    item: tuple[int, Path, str, int, int],
) -> tuple[int, Path, str, int, int, ImageTechnicalMetrics | None, dict[str, float]]:
    image_id, image_path, path_key, size, mtime_ns = item
    timings: dict[str, float] = {}
    try:
        metrics = compute_technical_metrics(image_path, timings=timings)
    except Exception:
        metrics = None
    return image_id, image_path, path_key, size, mtime_ns, metrics, timings


def _append_phase_timings(
    phase_values: dict[str, list[float]],
    timings: dict[str, float],
) -> None:
    for phase, duration in timings.items():
        phase_values.setdefault(str(phase), []).append(float(duration))


def _path_signature(path: Path) -> tuple[str, int, int] | None:
    try:
        resolved = path.expanduser().resolve()
        stat = resolved.stat()
    except OSError:
        return None
    return str(resolved).casefold(), int(stat.st_size), int(stat.st_mtime_ns)


def _technical_worker_count(*, max_workers: int | None, item_count: int) -> int:
    if item_count <= 1:
        return item_count
    if max_workers is not None:
        return max(1, min(int(max_workers), item_count))
    return max(1, min(8, os.cpu_count() or 4, item_count))


def local_mean(gray: np.ndarray) -> np.ndarray:
    padded = np.pad(gray, 1, mode="edge")
    result = padded[:-2, :-2] + padded[:-2, 1:-1]
    np.add(result, padded[:-2, 2:], out=result)
    np.add(result, padded[1:-1, :-2], out=result)
    np.add(result, padded[1:-1, 1:-1], out=result)
    np.add(result, padded[1:-1, 2:], out=result)
    np.add(result, padded[2:, :-2], out=result)
    np.add(result, padded[2:, 1:-1], out=result)
    np.add(result, padded[2:, 2:], out=result)
    np.divide(result, 9.0, out=result)
    return result


def compute_tag_penalty(
    metrics: ImageTechnicalMetrics,
    configs: list[TagPenaltyConfig],
) -> tuple[float, list[str]]:
    total = 0.0
    triggered: list[str] = []
    for config in configs:
        metric_value = float(getattr(metrics, config.metric))
        severity = severity_from_metric(metric_value, config)
        total += config.weight * severity
        if severity > 0.0:
            triggered.append(config.tag)
    return total, triggered


def severity_from_metric(metric_value: float, config: TagPenaltyConfig) -> float:
    if config.direction == "higher_is_worse":
        x = metric_value - config.threshold
    elif config.direction == "lower_is_worse":
        x = config.threshold - metric_value
    else:
        raise ValueError(f"Unsupported tag direction: {config.direction}")
    raw = float(1.0 / (1.0 + np.exp(-config.k * x)))
    return float(np.clip((raw - 0.5) * 2.0, 0.0, 1.0))


def load_tag_penalty_configs(path: str | Path) -> list[TagPenaltyConfig]:
    configs: list[TagPenaltyConfig] = []
    with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError("tag penalty CSV must include headers")
        for line_number, row in enumerate(reader, start=2):
            try:
                config = TagPenaltyConfig(
                    tag=(row.get("tag") or "").strip(),
                    metric=(row.get("metric") or "").strip(),
                    direction=(row.get("direction") or "").strip(),
                    threshold=float(row.get("threshold") or 0.0),
                    weight=float(row.get("weight") or 1.0),
                    k=float(row.get("k") or 10.0),
                )
            except ValueError as exc:
                raise ValueError(f"tag penalty row {line_number} has invalid numeric value") from exc
            if not config.tag or not config.metric or not config.direction:
                raise ValueError(f"tag penalty row {line_number} requires tag, metric, and direction")
            configs.append(config)
    return configs
