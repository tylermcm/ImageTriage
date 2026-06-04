from __future__ import annotations

import csv
import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np
from PIL import Image

from aiculler.storage import SQLiteFeatureStore


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
                image_id, image_path, path_key, size, mtime_ns, metrics = _compute_metrics_cache_row(item)
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
                    image_id, image_path, path_key, size, mtime_ns, metrics = future.result()
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
    )


def compute_technical_metrics(image_path: str | Path) -> ImageTechnicalMetrics:
    with Image.open(image_path) as opened:
        img = opened.convert("RGB")
    # Larger sample window: 1024-px thumbs lose almost all evidence of
    # camera-shake / motion blur from a 24MP raw. 2048 px preserves enough
    # high-frequency content for Laplacian variance to discriminate sharp
    # from soft frames without making per-image cost unreasonable.
    img.thumbnail((2048, 2048), Image.Resampling.BILINEAR)
    rgb = np.asarray(img, dtype=np.float32) / 255.0
    gray = rgb @ np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)

    # Focus / sharpness: variance of the Laplacian (the canonical sharpness
    # metric in OpenCV / scikit-image). The first-derivative magnitude we used
    # previously gets fooled by high-frequency static texture (Spanish moss,
    # tree foliage), saturating to "sharp" even when the image is clearly
    # motion-blurred. The Laplacian (second derivative) falls off much faster
    # under any kind of blur because blur smooths the local intensity curve.
    laplacian = (
        gray[:-2, 1:-1]
        + gray[2:, 1:-1]
        + gray[1:-1, :-2]
        + gray[1:-1, 2:]
        - 4.0 * gray[1:-1, 1:-1]
    )
    laplacian_variance = float(np.var(laplacian))
    # Multiplier 80 maps a sharp landscape (lap_var ~0.015+) to ~1.0 and a
    # noticeably soft frame (lap_var ~0.003) to ~0.24 (below the default
    # outoffocus threshold of 0.30). Tune via tag_penalties.csv if needed.
    focus_score = float(np.clip(laplacian_variance * 80.0, 0.0, 1.0))

    # First-derivative gradients still drive motion-blur direction. With the
    # new Laplacian-based focus_score being trustworthy now, the original
    # softness * directional_balance formula gives clean signal: it fires
    # when the image is both visibly soft AND has axis-asymmetric edges
    # (the signature of a camera pan or shake along one axis).
    gx = np.diff(gray, axis=1)
    gy = np.diff(gray, axis=0)
    edge_energy = float(np.mean(np.abs(gx)) + np.mean(np.abs(gy)))
    directional_balance = abs(float(np.mean(np.abs(gx))) - float(np.mean(np.abs(gy)))) / (edge_energy + 1e-6)
    motion_blur_score = float(np.clip((1.0 - focus_score) * directional_balance, 0.0, 1.0))

    max_channel = np.max(rgb, axis=2)
    highlight_clip_ratio = float(np.mean(max_channel >= 0.985))
    shadow_clip_ratio = float(np.mean(gray <= 0.025))
    contrast_score = float(np.clip(np.std(gray) * 4.0, 0.0, 1.0))

    blurred = local_mean(gray)
    high_frequency = gray - blurred
    noise_score = float(np.clip(np.std(high_frequency) * 8.0, 0.0, 1.0))

    bright_ratio = float(np.mean(gray >= 0.90))
    p50 = float(np.percentile(gray, 50))
    p99 = float(np.percentile(gray, 99))
    highlight_severity = min(1.0, highlight_clip_ratio * 18.0)
    bright_severity = min(1.0, bright_ratio * 4.0)
    glare_gap = max(0.0, p99 - p50 - 0.30)
    harsh_light_score = float(np.clip(0.55 * highlight_severity + 0.30 * bright_severity + 0.15 * glare_gap * 2.0, 0.0, 1.0))

    return ImageTechnicalMetrics(
        focus_score=focus_score,
        motion_blur_score=motion_blur_score,
        highlight_clip_ratio=highlight_clip_ratio,
        shadow_clip_ratio=shadow_clip_ratio,
        contrast_score=contrast_score,
        noise_score=noise_score,
        harsh_light_score=harsh_light_score,
    )


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
) -> tuple[int, Path, str, int, int, ImageTechnicalMetrics | None]:
    image_id, image_path, path_key, size, mtime_ns = item
    try:
        metrics = compute_technical_metrics(image_path)
    except Exception:
        metrics = None
    return image_id, image_path, path_key, size, mtime_ns, metrics


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
    return (
        padded[:-2, :-2]
        + padded[:-2, 1:-1]
        + padded[:-2, 2:]
        + padded[1:-1, :-2]
        + padded[1:-1, 1:-1]
        + padded[1:-1, 2:]
        + padded[2:, :-2]
        + padded[2:, 1:-1]
        + padded[2:, 2:]
    ) / 9.0


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
