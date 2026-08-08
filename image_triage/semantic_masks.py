from __future__ import annotations

import hashlib
import importlib
import json
import os
import sys
import threading
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable

import numpy as np

from PIL import Image
from PySide6.QtCore import QObject, QRunnable, QSize, Signal
from PySide6.QtGui import QImage

from .ai_model import (
    AIModelInstallation,
    DEFAULT_SEGMENTATION_MODEL_REPO_ID,
    DEFAULT_SEGMENTATION_MODEL_REVISION,
    download_segmentation_model,
    resolve_segmentation_model_installation,
)
from .imaging import load_image_for_display
from .perf import perf_logger, write_execution_log
from .semantic_mask_service import (
    default_oneformer_worker_service,
    validate_semantic_runtime,
)


SEMANTIC_MASK_CATEGORIES: tuple[str, ...] = (
    "sky",
    "trees",
    "foliage",
    "water",
    "mountains",
    "animals",
    "people",
    "buildings",
)
SEMANTIC_MASK_MODEL_ID = DEFAULT_SEGMENTATION_MODEL_REPO_ID
SEMANTIC_MASK_MODEL_VERSION = DEFAULT_SEGMENTATION_MODEL_REVISION
# OneFormer runs on a bounded preview; 1600px matches the validated sandbox.
SEMANTIC_MASK_PREVIEW_EDGE = 1600
SEMANTIC_MASK_INVENTORY_REQUEST = "inventory"
# Bump either version to invalidate every cached semantic mask: refinement
# covers the guided-filter/topology post-processing, mapping covers the
# ADE20k -> application-category label table owned by the OneFormer worker.
SEMANTIC_MASK_REFINEMENT_VERSION = "oneformer-ade-guided-1"
SEMANTIC_MASK_MAPPING_VERSION = "ade20k-app-categories-1"
SEMANTIC_MASK_MINIMUM_COVERAGE = 0.0005
SEMANTIC_MASK_EDGE_GAMMA = 1.6
SEMANTIC_SKY_REPAIR_MIN_CONFIDENT_COVERAGE = 0.10
SEMANTIC_SKY_REPAIR_MAX_PROMOTION_COVERAGE = 0.03
SEMANTIC_SKY_REPAIR_MAX_DISTANCE_RATIO = 0.04
SEMANTIC_PRESENCE_SAMPLE_SIZE = 128
SEMANTIC_PRESENCE_RULES: dict[str, tuple[float, int]] = {
    "animals": (0.35, 4),
    "foliage": (0.35, 4),
    "people": (0.50, 2),
}
SEMANTIC_PRESENCE_DEFAULT_RULE = (0.35, 8)

ProgressCallback = Callable[[str], None]


cv2: Any | None = None


@dataclass(frozen=True)
class SemanticCategoryPresence:
    present: bool
    coverage: float
    largest_component_coverage: float
    peak_confidence: float
    mean_confidence: float


@dataclass(frozen=True)
class SemanticMaskResult:
    source_path: Path
    source_size: tuple[int, int]
    mask_paths: dict[str, Path]
    model_id: str
    model_version: str
    weights_hash: str
    cache_hit: bool
    refinement_version: str = SEMANTIC_MASK_REFINEMENT_VERSION
    presence: dict[str, SemanticCategoryPresence] = field(default_factory=dict)

    @property
    def detected_categories(self) -> tuple[str, ...]:
        order = {category: index for index, category in enumerate(SEMANTIC_MASK_CATEGORIES)}
        return tuple(
            category
            for category, stats in sorted(
                self.presence.items(),
                key=lambda item: (
                    -item[1].coverage,
                    order.get(item[0], len(order)),
                ),
            )
            if stats.present
        )


def _candidate_ai_runtime_site_packages() -> tuple[Path, ...]:
    try:
        from .ai_runtime_packages import resolve_ai_runtime_site_packages

        return tuple(resolve_ai_runtime_site_packages(device="cpu"))
    except Exception:
        return ()


def _load_opencv() -> Any | None:
    """OpenCV powers the optional sky/water refinements. It lives in the managed
    AI runtime, so fall back to that site-packages set when it is not importable
    from the GUI process directly."""
    global cv2
    if cv2 is not None:
        return cv2
    try:
        cv2 = importlib.import_module("cv2")
        return cv2
    except Exception:
        pass

    for site_packages in _candidate_ai_runtime_site_packages():
        path_text = str(site_packages)
        if path_text not in sys.path:
            sys.path.append(path_text)
        try:
            cv2 = importlib.import_module("cv2")
            return cv2
        except Exception:
            continue
    return None


def default_semantic_mask_cache_root() -> Path:
    if os.name == "nt":
        local_appdata = os.environ.get("LOCALAPPDATA")
        if local_appdata:
            base = Path(local_appdata)
        else:
            try:
                base = Path.home() / "AppData" / "Local"
            except RuntimeError:
                base = Path.cwd() / ".image-triage-cache"
    else:
        xdg_cache = os.environ.get("XDG_CACHE_HOME")
        if xdg_cache:
            base = Path(xdg_cache)
        else:
            try:
                base = Path.home() / ".cache"
            except RuntimeError:
                base = Path.cwd() / ".cache"
    return base / "image_triage_ai_cache" / "semantic_masks"


def ensure_semantic_masks(
    source_path: str | Path,
    *,
    installation: AIModelInstallation | None = None,
    cache_root: str | Path | None = None,
    progress_callback: ProgressCallback | None = None,
) -> SemanticMaskResult:
    logger = perf_logger()
    total_started = time.perf_counter()
    source = Path(source_path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    logger.log(
        "ai.mask.oneformer.request",
        path=source,
        preview_edge=SEMANTIC_MASK_PREVIEW_EDGE,
    )
    phase_started = time.perf_counter()
    model_installation = installation or resolve_segmentation_model_installation()
    logger.duration(
        "ai.mask.oneformer.model_resolve",
        _elapsed_ms(phase_started),
        installed=model_installation.is_installed,
        model_id=model_installation.repo_id,
        model_revision=model_installation.revision,
    )
    if not model_installation.is_installed:
        _progress(progress_callback, "Downloading masking model...")

        def download_progress(filename: str, current: int, total: int) -> None:
            if total > 0:
                _progress(
                    progress_callback,
                    f"Downloading {Path(filename).name}: {current / (1024 * 1024):.1f} / "
                    f"{total / (1024 * 1024):.1f} MB",
                )
            else:
                _progress(progress_callback, f"Downloading {Path(filename).name}...")

        phase_started = time.perf_counter()
        download_segmentation_model(
            model_installation,
            progress_callback=download_progress,
        )
        logger.duration(
            "ai.mask.oneformer.download",
            _elapsed_ms(phase_started),
            model_id=model_installation.repo_id,
        )

    # OneFormer loads offline from a flat directory of the seven required
    # files; ``pytorch_model.bin`` is the checkpoint whose hash pins the cache.
    model_dir = model_installation.install_dir
    for filename in model_installation.required_filenames:
        required = model_dir / filename
        if not required.is_file():
            raise FileNotFoundError(required)
    weights_path = model_dir / "pytorch_model.bin"

    phase_started = time.perf_counter()
    weights_hash, weights_hash_cache_hit = _cached_sha256_file(weights_path)
    logger.duration(
        "ai.mask.oneformer.weights_hash",
        _elapsed_ms(phase_started),
        bytes=weights_path.stat().st_size,
        cache_hit=weights_hash_cache_hit,
    )
    stat = source.stat()
    cache_key = _source_cache_key(source, stat.st_size, stat.st_mtime_ns, weights_hash)
    cache_dir = Path(cache_root or default_semantic_mask_cache_root()) / cache_key
    metadata_path = cache_dir / "metadata.json"
    expected_paths = {
        category: cache_dir / f"{category}.png" for category in SEMANTIC_MASK_CATEGORIES
    }
    cache_lookup_started = time.perf_counter()
    cached_metadata = _load_json(metadata_path)
    if (
        cached_metadata.get("sourceSizeBytes") == stat.st_size
        and cached_metadata.get("sourceMtimeNs") == stat.st_mtime_ns
        and cached_metadata.get("weightsHash") == weights_hash
        and cached_metadata.get("modelId") == model_installation.repo_id
        and cached_metadata.get("modelVersion") == model_installation.revision
        and cached_metadata.get("mappingVersion") == SEMANTIC_MASK_MAPPING_VERSION
        and cached_metadata.get("refinementVersion") == SEMANTIC_MASK_REFINEMENT_VERSION
        and all(path.is_file() for path in expected_paths.values())
    ):
        source_size = tuple(cached_metadata.get("sourceSize") or ())
        if len(source_size) == 2 and all(int(value) > 0 for value in source_size):
            presence = _presence_from_metadata(cached_metadata.get("categoryStats"))
            if set(presence) != set(SEMANTIC_MASK_CATEGORIES):
                presence = _presence_from_mask_paths(expected_paths)
                cached_metadata["categoryStats"] = _presence_to_metadata(presence)
                try:
                    metadata_path.write_text(
                        json.dumps(cached_metadata, indent=2) + "\n",
                        encoding="utf-8",
                    )
                except OSError:
                    pass
            logger.duration(
                "ai.mask.oneformer.cache_lookup",
                _elapsed_ms(cache_lookup_started),
                cache_hit=True,
            )
            total_ms = _elapsed_ms(total_started)
            logger.duration("ai.mask.oneformer.total", total_ms, cache_hit=True)
            result = SemanticMaskResult(
                source_path=source,
                source_size=(int(source_size[0]), int(source_size[1])),
                mask_paths=expected_paths,
                model_id=model_installation.repo_id,
                model_version=model_installation.revision,
                weights_hash=f"sha256:{weights_hash}",
                cache_hit=True,
                presence=presence,
            )
            _emit_execution_summary(
                logger,
                progress_callback,
                cache_hit=True,
                total_ms=total_ms,
                detected=result.detected_categories,
                label=source.name,
            )
            return result

    logger.duration(
        "ai.mask.oneformer.cache_lookup",
        _elapsed_ms(cache_lookup_started),
        cache_hit=False,
    )
    phase_started = time.perf_counter()
    validate_semantic_runtime()
    logger.duration("ai.mask.oneformer.runtime_validate", _elapsed_ms(phase_started))
    _progress(progress_callback, "Decoding image...")
    phase_started = time.perf_counter()
    rgb = _decode_rgb_preview(source, SEMANTIC_MASK_PREVIEW_EDGE)
    decode_ms = _elapsed_ms(phase_started)
    logger.duration(
        "ai.mask.oneformer.decode_preview",
        decode_ms,
        source_bytes=stat.st_size,
        width=int(rgb.shape[1]),
        height=int(rgb.shape[0]),
    )

    # OneFormer runs in the managed AI runtime worker on a decoded RGB preview,
    # keeping RAW/HEIF decoding and PyTorch out of the Qt process. The worker
    # writes hard 0/255 category masks into a staging directory; refinement and
    # presence measurement happen here.
    cache_dir.mkdir(parents=True, exist_ok=True)
    # BMP is written/read straight through (no compression), so the preview
    # handoff to the worker is a fast memcpy rather than a PNG encode. The file
    # is transient — deleted in the finally below.
    worker_input = cache_dir / "worker-input.bmp"
    worker_masks_dir = cache_dir / "worker-masks"
    phase_started = time.perf_counter()
    Image.fromarray(rgb, mode="RGB").save(worker_input)
    logger.duration(
        "ai.mask.oneformer.worker_input_write",
        _elapsed_ms(phase_started),
        bytes=worker_input.stat().st_size,
    )
    worker_device = "unknown"
    try:
        _progress(progress_callback, "Finding scene regions...")
        phase_started = time.perf_counter()
        worker_result = _run_semantic_worker(
            model_dir=model_dir,
            input_path=worker_input,
            output_dir=worker_masks_dir,
            progress_callback=progress_callback,
        )
        worker_device = worker_result.device
        worker_ms = _elapsed_ms(phase_started)
        worker_infer_ms = float(worker_result.timings_ms.get("inference", 0.0))
        # Wall-clock of the subprocess round-trip (includes a one-time model
        # load on the first request of a worker session); the worker's own
        # preprocess/inference/postprocess splits ride in as fields.
        logger.duration(
            "ai.mask.oneformer.worker_total",
            worker_ms,
            device=worker_result.device,
            **{f"worker_{key}": value for key, value in worker_result.timings_ms.items()},
        )
        phase_started = time.perf_counter()
        masks = _load_worker_masks(worker_masks_dir)
        logger.duration(
            "ai.mask.oneformer.mask_load",
            _elapsed_ms(phase_started),
            categories=len(masks),
        )
    finally:
        worker_input.unlink(missing_ok=True)
        _remove_dir_quietly(worker_masks_dir)
    if set(masks) != set(SEMANTIC_MASK_CATEGORIES):
        missing = ", ".join(sorted(set(SEMANTIC_MASK_CATEGORIES) - set(masks)))
        raise RuntimeError(f"OneFormer worker did not produce masks for: {missing}")

    _progress(progress_callback, "Refining mask edges...")
    refine_started = time.perf_counter()
    # The guide-image statistics are identical for every category, so compute
    # them once for the whole image instead of inside each guided-filter call.
    guide_stats = _guide_stats(rgb)
    refined: dict[str, np.ndarray] = {}
    skipped_empty = 0
    for category, mask in masks.items():
        category_started = time.perf_counter()
        # OneFormer emits confident, non-overlapping hard masks; the guided
        # filter re-attaches edges to the image. No confidence-gamma tightening
        # is applied — that only helped soft per-pixel probability masks. Absent
        # categories are all-zero and refine to all-zero, so skip the filter and
        # the sky/water passes entirely.
        if not np.any(mask):
            refined[category] = mask
            skipped_empty += 1
            continue
        guided = _guided_filter(rgb, mask, stats=guide_stats)
        extra_stage = "none"
        if category == "sky":
            guided = _repair_sky_mask_boundaries(rgb, guided)
            extra_stage = "sky_repair"
        elif category == "water":
            guided = _refine_water_mask_topology(guided)
            extra_stage = "water_topology"
        refined[category] = guided
        # Per-category so we can see whether the cost is the guided filter
        # across all eight, or the sky-repair grabcut / water-topology extras.
        logger.duration(
            "ai.mask.oneformer.refine.category",
            _elapsed_ms(category_started),
            category=category,
            extra_stage=extra_stage,
        )
    refine_ms = _elapsed_ms(refine_started)
    logger.duration(
        "ai.mask.oneformer.refine_total",
        refine_ms,
        categories=len(refined),
        refined=len(refined) - skipped_empty,
        skipped_empty=skipped_empty,
        width=int(rgb.shape[1]),
        height=int(rgb.shape[0]),
    )
    phase_started = time.perf_counter()
    presence = {
        category: _measure_semantic_presence(category, mask)
        for category, mask in refined.items()
    }
    presence = _resolve_semantic_presence_conflicts(refined, presence)
    logger.duration(
        "ai.mask.oneformer.presence",
        _elapsed_ms(phase_started),
        categories=len(presence),
    )
    phase_started = time.perf_counter()
    for category, mask in refined.items():
        _save_mask(expected_paths[category], mask)
    logger.duration(
        "ai.mask.oneformer.mask_write",
        _elapsed_ms(phase_started),
        categories=len(refined),
    )
    metadata = {
        "sourcePath": str(source),
        "sourceSizeBytes": stat.st_size,
        "sourceMtimeNs": stat.st_mtime_ns,
        "sourceSize": [int(rgb.shape[1]), int(rgb.shape[0])],
        "modelId": model_installation.repo_id,
        "modelVersion": model_installation.revision,
        "weightsHash": weights_hash,
        "mappingVersion": SEMANTIC_MASK_MAPPING_VERSION,
        "refinementVersion": SEMANTIC_MASK_REFINEMENT_VERSION,
        "categories": list(SEMANTIC_MASK_CATEGORIES),
        "categoryStats": _presence_to_metadata(presence),
    }
    phase_started = time.perf_counter()
    metadata_path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    logger.duration("ai.mask.oneformer.metadata_write", _elapsed_ms(phase_started))
    total_ms = _elapsed_ms(total_started)
    logger.duration(
        "ai.mask.oneformer.total",
        total_ms,
        cache_hit=False,
        device=worker_device,
        categories=len(refined),
        width=int(rgb.shape[1]),
        height=int(rgb.shape[0]),
    )
    result = SemanticMaskResult(
        source_path=source,
        source_size=(int(rgb.shape[1]), int(rgb.shape[0])),
        mask_paths=expected_paths,
        model_id=model_installation.repo_id,
        model_version=model_installation.revision,
        weights_hash=f"sha256:{weights_hash}",
        cache_hit=False,
        presence=presence,
    )
    _emit_execution_summary(
        logger,
        progress_callback,
        cache_hit=False,
        total_ms=total_ms,
        detected=result.detected_categories,
        device=worker_device,
        decode_ms=decode_ms,
        worker_ms=worker_ms,
        worker_infer_ms=worker_infer_ms,
        refine_ms=refine_ms,
        label=source.name,
    )
    return result


def _run_semantic_worker(
    *,
    model_dir: Path,
    input_path: Path,
    output_dir: Path,
    progress_callback: ProgressCallback | None,
):
    return default_oneformer_worker_service().infer(
        model_dir=model_dir,
        input_path=input_path,
        output_dir=output_dir,
        categories=SEMANTIC_MASK_CATEGORIES,
        minimum_coverage=SEMANTIC_MASK_MINIMUM_COVERAGE,
        progress_callback=progress_callback,
    )


def _remove_dir_quietly(path: Path) -> None:
    if not path.exists():
        return
    for child in path.glob("*"):
        try:
            child.unlink()
        except OSError:
            pass
    try:
        path.rmdir()
    except OSError:
        pass


def _progress(callback: ProgressCallback | None, message: str) -> None:
    if callback is not None:
        callback(message)


def _elapsed_ms(started: float) -> float:
    return (time.perf_counter() - started) * 1000.0


def _format_execution_summary(
    *,
    cache_hit: bool,
    total_ms: float,
    decode_ms: float,
    worker_ms: float,
    worker_infer_ms: float,
    refine_ms: float,
    detected: tuple[str, ...],
    device: str,
) -> str:
    """One human-readable execution line for the program log + UI status."""
    count = len(detected)
    regions = f"{count} region{'' if count == 1 else 's'}"
    # ASCII separators only — the line lands in the perf log and the status bar,
    # and some log viewers/consoles mangle non-ASCII punctuation.
    if cache_hit:
        return f"Scene masks ready from cache in {total_ms:.0f} ms | {regions}"
    return (
        f"Scene masks ready in {total_ms:.0f} ms "
        f"(decode {decode_ms:.0f} / infer {worker_infer_ms:.0f} / refine {refine_ms:.0f} ms) "
        f"| {regions} | {device}"
    )


def _emit_execution_summary(
    logger,
    progress_callback: ProgressCallback | None,
    *,
    cache_hit: bool,
    total_ms: float,
    detected: tuple[str, ...],
    device: str = "cache",
    decode_ms: float = 0.0,
    worker_ms: float = 0.0,
    worker_infer_ms: float = 0.0,
    refine_ms: float = 0.0,
    label: str = "",
) -> None:
    summary = _format_execution_summary(
        cache_hit=cache_hit,
        total_ms=total_ms,
        decode_ms=decode_ms,
        worker_ms=worker_ms,
        worker_infer_ms=worker_infer_ms,
        refine_ms=refine_ms,
        detected=detected,
        device=device,
    )
    # Three sinks: (1) the gated perf JSONL as a structured event, (2) the live
    # editor status bar, (3) the always-on execution.log so timing is captured
    # even when Performance Logging is off.
    logger.log(
        "ai.mask.oneformer.summary",
        message=summary,
        cache_hit=cache_hit,
        total_ms=round(total_ms, 1),
        decode_ms=round(decode_ms, 1),
        worker_ms=round(worker_ms, 1),
        worker_infer_ms=round(worker_infer_ms, 1),
        refine_ms=round(refine_ms, 1),
        detected=list(detected),
        device=device,
    )
    _progress(progress_callback, summary)
    write_execution_log(f"{label}: {summary}" if label else summary)


def _source_cache_key(source: Path, size: int, mtime_ns: int, weights_hash: str) -> str:
    identity = "\0".join(
        (
            os.path.normcase(str(source)),
            str(size),
            str(mtime_ns),
            weights_hash,
            SEMANTIC_MASK_MODEL_ID,
            SEMANTIC_MASK_MODEL_VERSION,
            SEMANTIC_MASK_MAPPING_VERSION,
            SEMANTIC_MASK_REFINEMENT_VERSION,
        )
    )
    return hashlib.sha256(identity.encode("utf-8", errors="surrogatepass")).hexdigest()[:24]


def _load_json(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _presence_from_metadata(value: object) -> dict[str, SemanticCategoryPresence]:
    if not isinstance(value, dict):
        return {}
    presence: dict[str, SemanticCategoryPresence] = {}
    for category, raw_stats in value.items():
        if category not in SEMANTIC_MASK_CATEGORIES or not isinstance(raw_stats, dict):
            continue
        try:
            presence[category] = SemanticCategoryPresence(
                present=bool(raw_stats["present"]),
                coverage=float(raw_stats["coverage"]),
                largest_component_coverage=float(raw_stats["largestComponentCoverage"]),
                peak_confidence=float(raw_stats["peakConfidence"]),
                mean_confidence=float(raw_stats["meanConfidence"]),
            )
        except (KeyError, TypeError, ValueError):
            continue
    return presence


def _presence_to_metadata(
    presence: dict[str, SemanticCategoryPresence],
) -> dict[str, dict[str, float | bool]]:
    return {
        category: {
            "present": stats.present,
            "coverage": stats.coverage,
            "largestComponentCoverage": stats.largest_component_coverage,
            "peakConfidence": stats.peak_confidence,
            "meanConfidence": stats.mean_confidence,
        }
        for category, stats in presence.items()
    }


def _presence_from_mask_paths(
    mask_paths: dict[str, Path],
) -> dict[str, SemanticCategoryPresence]:
    presence: dict[str, SemanticCategoryPresence] = {}
    masks: dict[str, np.ndarray] = {}
    for category, path in mask_paths.items():
        with Image.open(path) as image:
            sampled = np.asarray(
                image.convert("L").resize(
                    (SEMANTIC_PRESENCE_SAMPLE_SIZE, SEMANTIC_PRESENCE_SAMPLE_SIZE),
                    Image.Resampling.BILINEAR,
                ),
                dtype=np.float32,
            ) / 255.0
        masks[category] = sampled
        presence[category] = _measure_semantic_presence(category, sampled)
    return _resolve_semantic_presence_conflicts(masks, presence)


def _largest_component(
    binary: np.ndarray,
) -> tuple[int, tuple[int, int, int, int] | None]:
    values = np.asarray(binary, dtype=bool)
    if values.ndim != 2 or not values.any():
        return 0, None
    height, width = values.shape
    visited = np.zeros_like(values, dtype=bool)
    largest = 0
    largest_bounds: tuple[int, int, int, int] | None = None
    for start_y, start_x in np.argwhere(values):
        y = int(start_y)
        x = int(start_x)
        if visited[y, x]:
            continue
        visited[y, x] = True
        stack = [(y, x)]
        size = 0
        min_x = max_x = x
        min_y = max_y = y
        while stack:
            current_y, current_x = stack.pop()
            size += 1
            min_x = min(min_x, current_x)
            max_x = max(max_x, current_x)
            min_y = min(min_y, current_y)
            max_y = max(max_y, current_y)
            for neighbor_y in range(max(0, current_y - 1), min(height, current_y + 2)):
                for neighbor_x in range(max(0, current_x - 1), min(width, current_x + 2)):
                    if (
                        (neighbor_y != current_y or neighbor_x != current_x)
                        and values[neighbor_y, neighbor_x]
                        and not visited[neighbor_y, neighbor_x]
                    ):
                        visited[neighbor_y, neighbor_x] = True
                        stack.append((neighbor_y, neighbor_x))
        if size > largest:
            largest = size
            largest_bounds = (min_x, min_y, max_x + 1, max_y + 1)
    return largest, largest_bounds


def _sample_semantic_mask(mask: np.ndarray) -> np.ndarray:
    sampled_image = Image.fromarray(
        np.asarray(mask, dtype=np.float32),
        mode="F",
    ).resize(
        (SEMANTIC_PRESENCE_SAMPLE_SIZE, SEMANTIC_PRESENCE_SAMPLE_SIZE),
        Image.Resampling.BILINEAR,
    )
    return np.clip(np.asarray(sampled_image, dtype=np.float32), 0.0, 1.0)


def _semantic_presence_binary(category: str, mask: np.ndarray) -> np.ndarray:
    threshold, _minimum_component_pixels = SEMANTIC_PRESENCE_RULES.get(
        category,
        SEMANTIC_PRESENCE_DEFAULT_RULE,
    )
    return _sample_semantic_mask(mask) >= threshold


def _measure_semantic_presence(
    category: str,
    mask: np.ndarray,
) -> SemanticCategoryPresence:
    sampled = _sample_semantic_mask(mask)
    threshold, minimum_component_pixels = SEMANTIC_PRESENCE_RULES.get(
        category,
        SEMANTIC_PRESENCE_DEFAULT_RULE,
    )
    confident = sampled >= threshold
    confident_count = int(np.count_nonzero(confident))
    largest_component, _bounds = _largest_component(confident)
    total = max(1, sampled.size)
    return SemanticCategoryPresence(
        present=largest_component >= minimum_component_pixels,
        coverage=confident_count / total,
        largest_component_coverage=largest_component / total,
        peak_confidence=float(sampled.max(initial=0.0)),
        mean_confidence=(
            float(sampled[confident].mean())
            if confident_count
            else 0.0
        ),
    )


def _bounds_overlap_fraction(
    first: tuple[int, int, int, int] | None,
    second: tuple[int, int, int, int] | None,
) -> float:
    if first is None or second is None:
        return 0.0
    left = max(first[0], second[0])
    top = max(first[1], second[1])
    right = min(first[2], second[2])
    bottom = min(first[3], second[3])
    intersection = max(0, right - left) * max(0, bottom - top)
    first_area = max(1, (first[2] - first[0]) * (first[3] - first[1]))
    second_area = max(1, (second[2] - second[0]) * (second[3] - second[1]))
    return intersection / min(first_area, second_area)


def _binary_bounds(binary: np.ndarray) -> tuple[int, int, int, int] | None:
    rows, columns = np.where(np.asarray(binary, dtype=bool))
    if not len(rows):
        return None
    return (
        int(columns.min()),
        int(rows.min()),
        int(columns.max()) + 1,
        int(rows.max()) + 1,
    )


def _resolve_semantic_presence_conflicts(
    masks: dict[str, np.ndarray],
    presence: dict[str, SemanticCategoryPresence],
) -> dict[str, SemanticCategoryPresence]:
    animal = presence.get("animals")
    person = presence.get("people")
    if (
        animal is None
        or person is None
        or not animal.present
        or not person.present
        or animal.coverage < person.coverage * 2.0
    ):
        return presence
    animal_bounds = _binary_bounds(
        _semantic_presence_binary("animals", masks["animals"])
    )
    person_bounds = _binary_bounds(
        _semantic_presence_binary("people", masks["people"])
    )
    if _bounds_overlap_fraction(animal_bounds, person_bounds) < 0.70:
        return presence
    resolved = dict(presence)
    resolved["people"] = replace(person, present=False)
    return resolved


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


_WEIGHTS_HASH_CACHE: dict[tuple[str, int, int], str] = {}
_WEIGHTS_HASH_LOCK = threading.Lock()


def _cached_sha256_file(path: Path) -> tuple[str, bool]:
    """SHA-256 the checkpoint once per (path, size, mtime); reuse thereafter.

    Re-hashing the 194 MB OneFormer weights on every image cost ~140 ms; the
    checkpoint is immutable within a session, so key on file identity like the
    BiRefNet path does."""
    resolved = path.resolve()
    stat = resolved.stat()
    key = (os.path.normcase(str(resolved)), stat.st_size, stat.st_mtime_ns)
    with _WEIGHTS_HASH_LOCK:
        cached = _WEIGHTS_HASH_CACHE.get(key)
        if cached is not None:
            return cached, True
        digest = _sha256_file(resolved)
        _WEIGHTS_HASH_CACHE.clear()
        _WEIGHTS_HASH_CACHE[key] = digest
        return digest, False


def _decode_rgb_preview(path: Path, long_edge: int) -> np.ndarray:
    image, error = load_image_for_display(
        str(path),
        QSize(long_edge, long_edge),
        prefer_embedded=True,
    )
    if image.isNull():
        raise RuntimeError(error or "Could not decode image.")
    converted = image.convertToFormat(QImage.Format.Format_RGB888)
    width = converted.width()
    height = converted.height()
    stride = converted.bytesPerLine()
    buffer = np.frombuffer(converted.bits(), dtype=np.uint8, count=height * stride)
    return buffer.reshape(height, stride)[:, : width * 3].reshape(height, width, 3).copy()


def _load_worker_masks(mask_dir: Path) -> dict[str, np.ndarray]:
    """Read the worker's hard 0/255 category masks as float ``0.0/1.0`` arrays.

    A category the worker suppressed (blank PNG) simply loads as all-zero, which
    the refinement and presence stages treat as "absent" without special-casing.
    """
    masks: dict[str, np.ndarray] = {}
    for category in SEMANTIC_MASK_CATEGORIES:
        path = mask_dir / f"{category}.png"
        if not path.is_file():
            continue
        with Image.open(path) as image:
            values = np.asarray(image.convert("L"), dtype=np.float32) / 255.0
        masks[category] = values
    return masks


def _box_mean(values: np.ndarray, radius: int) -> np.ndarray:
    if radius <= 0:
        return values.astype(np.float32, copy=True)
    source = np.ascontiguousarray(values, dtype=np.float32)
    kernel = radius * 2 + 1
    # OpenCV's C++ box filter is ~10x faster than the numpy integral image and
    # is the dominant cost of guided-filter refinement. BORDER_DEFAULT is
    # reflect-101, matching np.pad(mode="reflect"); output is identical to the
    # fallback within float tolerance. cv2 lives in the managed AI runtime.
    opencv = _load_opencv()
    if opencv is not None:
        return opencv.boxFilter(
            source,
            -1,
            (kernel, kernel),
            normalize=True,
            borderType=opencv.BORDER_DEFAULT,
        )
    padded = np.pad(source, ((radius, radius), (radius, radius)), mode="reflect")
    integral = np.pad(padded, ((1, 0), (1, 0)), mode="constant")
    integral = np.cumsum(np.cumsum(integral, axis=0, dtype=np.float32), axis=1, dtype=np.float32)
    sums = (
        integral[kernel:, kernel:]
        - integral[:-kernel, kernel:]
        - integral[kernel:, :-kernel]
        + integral[:-kernel, :-kernel]
    )
    return sums / float(kernel * kernel)


class _GuideStats:
    """RGB-only guided-filter terms, shared across every category of one image.

    ``mean_I`` and ``var_I`` depend only on the guide image, not the mask, so
    computing them once instead of per category removes two of the six box-mean
    passes each category otherwise pays.
    """

    __slots__ = ("gray", "mean_i", "variance_i", "radius", "epsilon")

    def __init__(self, gray, mean_i, variance_i, radius: int, epsilon: float) -> None:
        self.gray = gray
        self.mean_i = mean_i
        self.variance_i = variance_i
        self.radius = radius
        self.epsilon = epsilon


def _guide_stats(guide_rgb: np.ndarray, *, radius: int = 8, epsilon: float = 1e-3) -> _GuideStats:
    rgb = guide_rgb.astype(np.float32) / 255.0
    gray = rgb[..., 0] * 0.299 + rgb[..., 1] * 0.587 + rgb[..., 2] * 0.114
    mean_i = _box_mean(gray, radius)
    variance_i = _box_mean(gray * gray, radius) - mean_i * mean_i
    return _GuideStats(gray, mean_i, variance_i, radius, epsilon)


def _guided_filter(
    guide_rgb: np.ndarray,
    mask: np.ndarray,
    *,
    radius: int = 8,
    epsilon: float = 1e-3,
    stats: _GuideStats | None = None,
) -> np.ndarray:
    # Reuse precomputed guide statistics when refining many masks over one image;
    # otherwise derive them for this single call (keeps the function standalone).
    guide = stats if stats is not None else _guide_stats(guide_rgb, radius=radius, epsilon=epsilon)
    gray = guide.gray
    source = mask.astype(np.float32, copy=False)
    mean_p = _box_mean(source, guide.radius)
    corr_ip = _box_mean(gray * source, guide.radius)
    covariance_ip = corr_ip - guide.mean_i * mean_p
    a = covariance_ip / (guide.variance_i + guide.epsilon)
    b = mean_p - a * guide.mean_i
    return np.clip(_box_mean(a, guide.radius) * gray + _box_mean(b, guide.radius), 0.0, 1.0)


def _tighten_mask_confidence(
    mask: np.ndarray,
    *,
    gamma: float = SEMANTIC_MASK_EDGE_GAMMA,
) -> np.ndarray:
    """Compress uncertain edge tails without moving the 50% boundary."""
    values = np.clip(np.asarray(mask, dtype=np.float32), 0.0, 1.0)
    if gamma <= 1.0:
        return values.copy()
    selected = np.power(values, gamma)
    rejected = np.power(1.0 - values, gamma)
    return np.divide(
        selected,
        selected + rejected,
        out=np.zeros_like(selected),
        where=(selected + rejected) > 0.0,
    )


def _repair_sky_mask_boundaries(
    guide_rgb: np.ndarray,
    mask: np.ndarray,
) -> np.ndarray:
    """Promote small image-supported gaps along a confident sky boundary."""
    values = np.clip(np.asarray(mask, dtype=np.float32), 0.0, 1.0)
    height, width = values.shape
    if height < 16 or width < 16:
        return values.copy()

    definite_background = values < 0.05
    definite_foreground = values >= 0.90
    selected = values >= 0.35
    if (
        np.count_nonzero(definite_background) < 64
        or np.count_nonzero(definite_foreground) < 64
        or float(np.mean(definite_foreground))
        < SEMANTIC_SKY_REPAIR_MIN_CONFIDENT_COVERAGE
        or np.count_nonzero(selected[0]) < max(4, int(round(width * 0.05)))
    ):
        return values.copy()

    opencv = _load_opencv()
    if opencv is None:
        return values.copy()

    labels = np.full(values.shape, opencv.GC_PR_BGD, dtype=np.uint8)
    labels[definite_background] = opencv.GC_BGD
    labels[(values >= 0.38) & (values < 0.90)] = opencv.GC_PR_FGD
    labels[definite_foreground] = opencv.GC_FGD
    background_model = np.zeros((1, 65), dtype=np.float64)
    foreground_model = np.zeros((1, 65), dtype=np.float64)
    try:
        opencv.grabCut(
            opencv.cvtColor(guide_rgb, opencv.COLOR_RGB2BGR),
            labels,
            None,
            background_model,
            foreground_model,
            1,
            opencv.GC_INIT_WITH_MASK,
        )
    except Exception:
        return values.copy()

    grabcut_foreground = np.isin(
        labels,
        (opencv.GC_FGD, opencv.GC_PR_FGD),
    )
    distance = opencv.distanceTransform(
        np.logical_not(selected).astype(np.uint8),
        opencv.DIST_L2,
        3,
    )
    max_distance = max(
        4.0,
        float(max(height, width)) * SEMANTIC_SKY_REPAIR_MAX_DISTANCE_RATIO,
    )
    promotion = grabcut_foreground & np.logical_not(selected) & (distance <= max_distance)
    promotion_coverage = float(np.mean(promotion))
    if (
        promotion_coverage <= 0.0
        or promotion_coverage > SEMANTIC_SKY_REPAIR_MAX_PROMOTION_COVERAGE
    ):
        return values.copy()

    delta = np.where(
        promotion,
        np.maximum(0.72 - values, 0.0),
        0.0,
    ).astype(np.float32)
    delta = opencv.GaussianBlur(delta, (0, 0), 1.1)
    return np.clip(values + delta, 0.0, 1.0)


def _refine_water_mask_topology(mask: np.ndarray) -> np.ndarray:
    """Remove small, disconnected high-confidence regions from water masks."""
    values = np.clip(np.asarray(mask, dtype=np.float32), 0.0, 1.0)
    height, width = values.shape
    if height < 16 or width < 16:
        return values.copy()

    opencv = _load_opencv()
    if opencv is None:
        return values.copy()

    component_count, labels, stats, centroids = opencv.connectedComponentsWithStats(
        (values >= 0.35).astype(np.uint8),
        8,
    )
    if component_count <= 1:
        return values.copy()

    areas = stats[1:, opencv.CC_STAT_AREA]
    largest_label = 1 + int(np.argmax(areas))
    largest_area = int(areas[largest_label - 1])
    if largest_area < max(64, int(round(values.size * 0.005))):
        return values.copy()

    substantial_area = max(64, int(round(largest_area * 0.05)))
    lower_fragment_area = max(64, int(round(largest_area * 0.002)))
    retained_labels: list[int] = []
    for label in range(1, component_count):
        area = int(stats[label, opencv.CC_STAT_AREA])
        center_y = float(centroids[label, 1])
        if (
            label == largest_label
            or area >= substantial_area
            or (center_y >= height * 0.50 and area >= lower_fragment_area)
        ):
            retained_labels.append(label)

    retained_core = np.isin(labels, retained_labels).astype(np.uint8)
    radius = max(3, int(round(max(height, width) * 0.015)))
    kernel = opencv.getStructuringElement(
        opencv.MORPH_ELLIPSE,
        (radius * 2 + 1, radius * 2 + 1),
    )
    support = opencv.dilate(retained_core, kernel)
    return values * support.astype(np.float32)


def _save_mask(path: Path, mask: np.ndarray) -> None:
    pixels = np.clip(mask * 255.0, 0, 255).astype(np.uint8)
    Image.fromarray(pixels, mode="L").save(path)


class SemanticMaskTaskSignals(QObject):
    progress = Signal(str, str)
    finished = Signal(str, str, object)
    failed = Signal(str, str, str)


class SemanticMaskTask(QRunnable):
    def __init__(
        self,
        source_path: str | Path,
        category: str = SEMANTIC_MASK_INVENTORY_REQUEST,
        *,
        installation: AIModelInstallation | None = None,
        cache_root: str | Path | None = None,
    ) -> None:
        super().__init__()
        normalized = category.strip().casefold()
        if (
            normalized not in SEMANTIC_MASK_CATEGORIES
            and normalized != SEMANTIC_MASK_INVENTORY_REQUEST
        ):
            raise ValueError(f"Unknown semantic mask category: {category}")
        self.source_path = Path(source_path).expanduser().resolve()
        self.category = normalized
        self.installation = installation
        self.cache_root = cache_root
        self.signals = SemanticMaskTaskSignals()
        self.setAutoDelete(True)

    def run(self) -> None:
        source_text = str(self.source_path)
        try:
            result = ensure_semantic_masks(
                self.source_path,
                installation=self.installation,
                cache_root=self.cache_root,
                progress_callback=lambda message: self.signals.progress.emit(
                    self.category,
                    message,
                ),
            )
            self.signals.finished.emit(self.category, source_text, result)
        except Exception as exc:
            self.signals.failed.emit(self.category, source_text, str(exc))
