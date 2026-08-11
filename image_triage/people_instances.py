from __future__ import annotations

"""SAM-based per-person instance masks for point-at-it people selection.

OneFormer only offers one merged ``people`` region and BiRefNet fuses touching
people into a single matte, so neither can answer "highlight the person I'm
pointing at". This runs the already-resident SAM host over the people region and
splits it into individual people, each its own hover target. Clicking one routes
into the exact same SAM (+ BiRefNet refine) path a manual click-to-select uses.

The work reuses click-to-select's preview cache and image embedding (same cache
key), so the per-image SAM embedding is computed once and shared: instance
discovery here and later manual clicks pay for it together, not twice.
"""

import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np
from PIL import Image, ImageFilter
from PySide6.QtCore import QObject, QRunnable, Signal

from .ai_model import AIModelInstallation, resolve_sam_model_installation, download_sam_model
from .mask_engine_service import default_mask_engine_service
from .perf import perf_logger, write_execution_log
from .prompt_masks import (
    PROMPT_MASK_MINIMUM_AREA,
    PROMPT_MASK_PREVIEW_EDGE,
    _decode_rgb_preview,
    _source_cache_key,
    default_prompt_mask_cache_root,
)
from .semantic_mask_service import validate_semantic_runtime

ProgressCallback = Callable[[str], None]

# Tuning. A person instance must be at least this fraction of the frame (drops
# specks / distant passers-by) and at most this fraction (drops the whole-crowd
# blob SAM sometimes returns when a seed sits on shared background).
INSTANCE_MIN_AREA = 0.004
INSTANCE_MAX_AREA = 0.60
# A candidate is a person only if most of it lands inside the people region.
INSTANCE_MIN_PEOPLE_OVERLAP = 0.55
# Two candidates are the same person when they overlap this much (IoU), or when
# the smaller is largely swallowed by the larger (containment).
INSTANCE_DEDUPE_IOU = 0.55
INSTANCE_DEDUPE_CONTAINMENT = 0.70
# SAM sometimes returns a person in vertical pieces (torso, then shins). Two
# instances are pieces of one body when their horizontal spans line up this
# closely and they abut/overlap vertically — merge them rather than offer two
# half-person hover targets.
INSTANCE_MERGE_X_OVERLAP = 0.70
INSTANCE_MERGE_Y_GAP = 0.03   # fraction of frame height
# Cap the SAM calls so a dense crowd can't stall the first hover.
MAX_INSTANCE_SEEDS = 40


@dataclass(frozen=True)
class PersonInstance:
    """One isolated person: a preview-space mask plus the seed that made it."""

    mask: np.ndarray                      # bool (preview_h, preview_w)
    seed_norm: tuple[float, float]        # normalized point that produced it
    bounds: tuple[int, int, int, int]     # x0, y0, x1, y1 in preview px
    area: int                             # mask pixel count


def _progress(callback: ProgressCallback | None, message: str) -> None:
    if callback is not None:
        callback(message)


def _ms_since(started: float) -> float:
    return (time.perf_counter() - started) * 1000.0


def _interiorness(people: np.ndarray, iterations: int) -> np.ndarray:
    """Cheap distance-to-edge proxy: how many erosions each pixel survives.

    A stack of PIL MinFilter erosions stands in for a distance transform so we
    can seed from person *cores* (torsos) first without scipy/cv2 in the frozen
    build."""
    surviving = Image.fromarray((people * 255).astype(np.uint8), mode="L")
    score = np.zeros(people.shape, dtype=np.int32)
    for _ in range(max(1, iterations)):
        surviving = surviving.filter(ImageFilter.MinFilter(3))
        score += (np.asarray(surviving, dtype=np.uint8) > 0).astype(np.int32)
    return score


def _seed_order(people: np.ndarray) -> list[tuple[int, int]]:
    """Grid seeds inside the people region, most-interior first.

    The grid is coarse on purpose: each real person only needs one seed in their
    core (the interiorness sort front-loads torsos, and a claimed found person
    suppresses the rest), so a fine grid just buys redundant SAM calls."""
    height, width = people.shape
    # 1/24 of the short edge: fine enough that narrow people still get a core
    # seed, coarse enough to avoid ~2x redundant SAM calls. Below ~1/18 touching
    # people start sharing a seed and merge; finer than this just adds calls.
    step = max(24, min(width, height) // 24)
    interior = _interiorness(people, iterations=max(2, step // 4))
    seeds: list[tuple[int, int, int]] = []  # (interiorness, y, x)
    for y in range(step // 2, height, step):
        for x in range(step // 2, width, step):
            if people[y, x] and interior[y, x] > 0:
                seeds.append((int(interior[y, x]), y, x))
    seeds.sort(key=lambda item: -item[0])
    return [(y, x) for _score, y, x in seeds]


def _iou(a: np.ndarray, b: np.ndarray) -> float:
    inter = int(np.logical_and(a, b).sum())
    if inter == 0:
        return 0.0
    union = int(np.logical_or(a, b).sum())
    return inter / union if union else 0.0


def _containment(a: np.ndarray, b: np.ndarray, area_a: int, area_b: int) -> float:
    """Fraction of the smaller mask that sits inside the other."""
    smaller = min(area_a, area_b)
    if smaller == 0:
        return 0.0
    return int(np.logical_and(a, b).sum()) / smaller


def _bounds(mask: np.ndarray) -> tuple[int, int, int, int]:
    rows = np.any(mask, axis=1)
    cols = np.any(mask, axis=0)
    y0, y1 = np.where(rows)[0][[0, -1]]
    x0, x1 = np.where(cols)[0][[0, -1]]
    return int(x0), int(y0), int(x1) + 1, int(y1) + 1


def _same_body(a: PersonInstance, b: PersonInstance, frame_height: int) -> bool:
    """Whether two instances are vertical pieces of one person: their x-spans
    line up and they abut/overlap vertically (a torso above its own shins, not
    two people side by side)."""
    ax0, ay0, ax1, ay1 = a.bounds
    bx0, by0, bx1, by1 = b.bounds
    x_overlap = max(0, min(ax1, bx1) - max(ax0, bx0))
    min_width = max(1, min(ax1 - ax0, bx1 - bx0))
    if x_overlap / min_width < INSTANCE_MERGE_X_OVERLAP:
        return False
    y_gap = max(0, max(ay0, by0) - min(ay1, by1))
    return y_gap <= INSTANCE_MERGE_Y_GAP * frame_height


def _merge_body_parts(
    instances: list[PersonInstance], frame_height: int
) -> list[PersonInstance]:
    """Fuse instances that are pieces of the same body (see ``_same_body``)."""
    merged: list[PersonInstance] = []
    for inst in sorted(instances, key=lambda p: -p.area):
        for index, kept in enumerate(merged):
            if _same_body(kept, inst, frame_height):
                union = np.logical_or(kept.mask, inst.mask)
                merged[index] = PersonInstance(
                    mask=union,
                    seed_norm=kept.seed_norm,   # keep the larger piece's seed
                    bounds=_bounds(union),
                    area=int(union.sum()),
                )
                break
        else:
            merged.append(inst)
    merged.sort(key=lambda p: -p.area)
    return merged


def segment_people_instances(
    source_path: str | Path,
    *,
    people_mask_path: str | Path,
    installation: AIModelInstallation | None = None,
    cache_root: str | Path | None = None,
    progress_callback: ProgressCallback | None = None,
    max_seeds: int = MAX_INSTANCE_SEEDS,
) -> list[PersonInstance]:
    """Split the people region of ``source_path`` into individual person masks.

    ``people_mask_path`` is OneFormer's merged people mask (any resolution; it is
    resized to the SAM preview). Returns instances ordered largest-first.
    """
    logger = perf_logger()
    overall_started = time.perf_counter()
    source = Path(source_path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    people_path = Path(people_mask_path)
    if not people_path.is_file():
        return []

    phase = time.perf_counter()
    model_installation = installation or resolve_sam_model_installation()
    if not model_installation.is_installed:
        _progress(progress_callback, "Downloading selection model...")
        download_sam_model(model_installation)
    validate_semantic_runtime()
    logger.duration("ai.mask.people_instances.setup", _ms_since(phase))

    phase = time.perf_counter()
    stat = source.stat()
    cache_key = _source_cache_key(source, stat.st_size, stat.st_mtime_ns)
    cache_dir = Path(cache_root or default_prompt_mask_cache_root()) / cache_key
    cache_dir.mkdir(parents=True, exist_ok=True)
    preview_path = cache_dir / "preview.png"
    preview_decoded = not preview_path.is_file()
    if preview_decoded:
        rgb = _decode_rgb_preview(source, PROMPT_MASK_PREVIEW_EDGE)
        Image.fromarray(rgb, mode="RGB").save(preview_path)
        preview_width, preview_height = int(rgb.shape[1]), int(rgb.shape[0])
    else:
        with Image.open(preview_path) as loaded:
            preview_width, preview_height = loaded.size
    logger.duration(
        "ai.mask.people_instances.preview",
        _ms_since(phase),
        decoded=preview_decoded,
        width=preview_width,
        height=preview_height,
    )

    phase = time.perf_counter()
    with Image.open(people_path) as handle:
        people_img = handle.convert("L").resize(
            (preview_width, preview_height), Image.Resampling.BILINEAR
        )
    people = np.asarray(people_img, dtype=np.uint8) > 127
    if not people.any():
        return []
    people_coverage = float(people.mean())
    logger.duration(
        "ai.mask.people_instances.people_mask", _ms_since(phase), coverage=people_coverage
    )

    frame_area = float(preview_width * preview_height)
    phase = time.perf_counter()
    seeds = _seed_order(people)
    logger.duration(
        "ai.mask.people_instances.seeds", _ms_since(phase), seed_candidates=len(seeds)
    )
    if not seeds:
        return []

    service = default_mask_engine_service()
    claimed = np.zeros((preview_height, preview_width), dtype=bool)
    instances: list[PersonInstance] = []
    started = time.perf_counter()
    calls = 0
    skipped_claimed = 0
    segment_ms_total = 0.0
    first_call_ms = 0.0

    for (sy, sx) in seeds:
        if claimed[sy, sx]:
            skipped_claimed += 1
            continue  # already inside a discovered person
        if calls >= max_seeds:
            break
        calls += 1
        _progress(progress_callback, f"Finding people... ({len(instances)} so far)")
        output_path = cache_dir / f"instance-{uuid.uuid4().hex[:10]}.png"
        call_started = time.perf_counter()
        try:
            infer_result = service.infer_prompt(
                model_dir=model_installation.install_dir,
                input_path=preview_path,
                output_path=output_path,
                points=[(float(sx), float(sy))],
                labels=[1],
                image_key=cache_key,
                minimum_area=PROMPT_MASK_MINIMUM_AREA,
                progress_callback=None,
            )
            call_ms = _ms_since(call_started)
            segment_ms_total += call_ms
            if calls == 1:
                first_call_ms = call_ms
            logger.duration(
                "ai.mask.people_instances.segment",
                call_ms,
                seed=calls,
                device=str(infer_result.get("device") or "unknown"),
                first=calls == 1,
            )
            mask = np.asarray(Image.open(output_path).convert("L"), dtype=np.uint8) > 127
        except Exception as exc:  # noqa: BLE001
            logger.log(
                "ai.mask.people_instances.seed_failed",
                error=str(exc),
                seed=calls,
                elapsed_ms=round(_ms_since(call_started), 1),
            )
            continue
        finally:
            output_path.unlink(missing_ok=True)

        area = int(mask.sum())
        if area == 0:
            continue
        claimed |= mask  # even rejects suppress their seeds so we don't re-probe
        frac = area / frame_area
        if frac < INSTANCE_MIN_AREA or frac > INSTANCE_MAX_AREA:
            continue
        overlap = int(np.logical_and(mask, people).sum()) / area
        if overlap < INSTANCE_MIN_PEOPLE_OVERLAP:
            continue
        if any(
            _iou(mask, inst.mask) > INSTANCE_DEDUPE_IOU
            or _containment(mask, inst.mask, area, inst.area) > INSTANCE_DEDUPE_CONTAINMENT
            for inst in instances
        ):
            continue
        instances.append(
            PersonInstance(
                mask=mask,
                seed_norm=(
                    (sx + 0.5) / preview_width,
                    (sy + 0.5) / preview_height,
                ),
                bounds=_bounds(mask),
                area=area,
            )
        )

    loop_ms = _ms_since(started)
    instances = _merge_body_parts(instances, preview_height)
    total_ms = _ms_since(overall_started)
    other_seg_ms = segment_ms_total - first_call_ms
    rest_calls = max(0, calls - 1)
    logger.duration(
        "ai.mask.people_instances.total",
        total_ms,
        seeds=calls,
        skipped=skipped_claimed,
        instances=len(instances),
        segment_ms=round(segment_ms_total, 1),
        first_call_ms=round(first_call_ms, 1),
        avg_rest_call_ms=round(other_seg_ms / rest_calls, 1) if rest_calls else 0.0,
        loop_ms=round(loop_ms, 1),
        people_coverage=round(people_coverage, 4),
    )
    # Always-on headline so the slowdown is visible without the gated JSONL.
    write_execution_log(
        "people-instances: "
        f"{len(instances)} people in {total_ms / 1000.0:.1f}s "
        f"({calls} SAM calls, {skipped_claimed} skipped; "
        f"first-call {first_call_ms / 1000.0:.1f}s, "
        f"rest {other_seg_ms / 1000.0:.1f}s over {rest_calls} calls) "
        f"preview={preview_width}x{preview_height}"
    )
    return instances


class PeopleInstanceSignals(QObject):
    ready = Signal(str, object)  # source path, list[PersonInstance] | None


class PeopleInstanceTask(QRunnable):
    """Split the people region into individuals off the UI thread."""

    def __init__(
        self,
        source_path: str | Path,
        people_mask_path: str | Path,
        *,
        cache_root: str | Path | None = None,
    ) -> None:
        super().__init__()
        self.signals = PeopleInstanceSignals()
        self._source_path = Path(source_path)
        self._people_mask_path = Path(people_mask_path)
        self._cache_root = cache_root
        self.setAutoDelete(True)

    def run(self) -> None:
        try:
            instances: list[PersonInstance] | None = segment_people_instances(
                self._source_path,
                people_mask_path=self._people_mask_path,
                cache_root=self._cache_root,
            )
        except Exception as exc:  # noqa: BLE001
            perf_logger().log("ai.mask.people_instances.failed", error=str(exc))
            instances = None
        self.signals.ready.emit(str(self._source_path), instances)


__all__ = [
    "PersonInstance",
    "segment_people_instances",
    "PeopleInstanceTask",
    "PeopleInstanceSignals",
]
