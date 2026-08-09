from __future__ import annotations

"""OneFormer semantic-segmentation engine (library).

Imported by the unified MaskEngine host (``mask_engine_worker``) and run under
the managed AI runtime Python — never inside the Qt GUI process. It therefore
stays free of ``image_triage``/PySide6 imports and depends only on the AI
runtime packages (torch, transformers, PIL, numpy). ``_OneFormerEngine`` keeps
the processor and model resident so consecutive images reuse the loaded weights.
"""

import json
import os
import re
import time
from pathlib import Path


METRIC_PREFIX = "AI_METRIC "
METRIC_ENV_VAR = "IMAGE_TRIAGE_AI_METRICS"

# The application's fixed semantic categories and the normalized ADE20k labels
# that map into them. Kept in sync with image_triage.semantic_masks and the
# validated OneFormer sandbox mapping. Because OneFormer assigns every pixel to
# exactly one ADE class, these merged masks never overlap.
APP_CATEGORY_LABELS: dict[str, frozenset[str]] = {
    "sky": frozenset({"sky"}),
    "trees": frozenset({"tree", "palm", "palm tree"}),
    "foliage": frozenset({"grass", "plant", "flower", "field"}),
    "water": frozenset(
        {
            "water",
            "sea",
            "river",
            "lake",
            "pool",
            "swimming pool",
            "falls",
            "waterfall",
        }
    ),
    "mountains": frozenset({"mountain", "hill"}),
    "animals": frozenset({"animal"}),
    "people": frozenset({"person"}),
    "buildings": frozenset(
        {
            "building",
            "house",
            "skyscraper",
            "hovel",
            "tower",
            "bridge",
            "grandstand",
        }
    ),
}


def _metrics_enabled() -> bool:
    return (os.environ.get(METRIC_ENV_VAR, "") or "").strip().casefold() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _emit_metric(event: str, started: float, **fields: object) -> None:
    if not _metrics_enabled():
        return
    payload = {
        "event": event,
        "duration_ms": round((time.perf_counter() - started) * 1000.0, 3),
        "worker_pid": os.getpid(),
        **fields,
    }
    print(METRIC_PREFIX + json.dumps(payload, default=str, sort_keys=True), flush=True)


def _device_name(torch, requested: str) -> str:
    normalized = (requested or "auto").strip().lower()
    if normalized == "auto":
        return "cuda" if torch.cuda.is_available() else "cpu"
    if normalized.startswith("cuda") and torch.cuda.is_available():
        return normalized
    return "cpu"


def _normalized_terms(label: str) -> frozenset[str]:
    normalized = re.sub(r"[_-]+", " ", str(label).strip().casefold())
    return frozenset(part.strip() for part in normalized.split(",") if part.strip())


def _category_for_raw_label(label: str) -> str | None:
    terms = _normalized_terms(label)
    for category, aliases in APP_CATEGORY_LABELS.items():
        if terms & aliases:
            return category
    return None


def _category_class_ids(
    id2label: dict[object, object],
    categories: tuple[str, ...],
) -> tuple[dict[str, list[int]], dict[str, list[str]]]:
    """Group ADE class ids by application category for the requested set."""
    class_ids: dict[str, list[int]] = {category: [] for category in categories}
    source_labels: dict[str, list[str]] = {category: [] for category in categories}
    for key, label_value in id2label.items():
        try:
            class_id = int(key)
        except (TypeError, ValueError):
            continue
        category = _category_for_raw_label(str(label_value))
        if category in class_ids:
            class_ids[category].append(class_id)
            source_labels[category].append(str(label_value))
    return class_ids, source_labels


class _OneFormerEngine:
    def __init__(self, requested_device: str) -> None:
        self.requested_device = requested_device
        self.device = "unknown"
        self.model_dir: Path | None = None
        self.torch = None
        self.np = None
        self.Image = None
        self.OneFormerProcessor = None
        self.OneFormerForUniversalSegmentation = None
        self.processor = None
        self.model = None
        self.id2label: dict[object, object] = {}

    def warm_imports(self) -> str:
        if self.torch is not None:
            return self.device

        phase_started = time.perf_counter()
        import numpy as np
        import torch
        from PIL import Image

        self.np = np
        self.torch = torch
        self.Image = Image
        self.device = _device_name(torch, self.requested_device)
        _emit_metric(
            "ai.mask.oneformer.worker.dependency_import",
            phase_started,
            torch_version=getattr(torch, "__version__", "unknown"),
            cuda_available=bool(torch.cuda.is_available()),
            cpu_threads=int(torch.get_num_threads()),
        )

        from transformers import (
            OneFormerForUniversalSegmentation,
            OneFormerProcessor,
        )

        self.OneFormerProcessor = OneFormerProcessor
        self.OneFormerForUniversalSegmentation = OneFormerForUniversalSegmentation
        print(f"DEVICE {self.device}", flush=True)
        return self.device

    def load_model(self, model_dir: Path) -> str:
        resolved_model_dir = model_dir.resolve()
        self.warm_imports()
        if self.model is not None and self.model_dir == resolved_model_dir:
            return self.device

        assert self.OneFormerProcessor is not None
        assert self.OneFormerForUniversalSegmentation is not None
        assert self.torch is not None
        print("PROGRESS Loading scene model...", flush=True)
        phase_started = time.perf_counter()
        self.processor = self.OneFormerProcessor.from_pretrained(
            str(resolved_model_dir),
            local_files_only=True,
        )
        model = self.OneFormerForUniversalSegmentation.from_pretrained(
            str(resolved_model_dir),
            local_files_only=True,
        )
        model.to(self.device)
        model.eval()
        if self.device.startswith("cuda"):
            self.torch.cuda.synchronize()
        self.model = model
        self.model_dir = resolved_model_dir
        self.id2label = dict(model.config.id2label)
        _emit_metric(
            "ai.mask.oneformer.worker.model_load",
            phase_started,
            device=self.device,
            precision="fp32",
            classes=len(self.id2label),
        )
        return self.device


def generate_semantic_masks(
    *,
    model_dir: Path,
    input_path: Path,
    output_dir: Path,
    categories: tuple[str, ...],
    minimum_coverage: float,
    requested_device: str,
    engine: _OneFormerEngine | None = None,
    emit_result: bool = True,
) -> dict[str, object]:
    total_started = time.perf_counter()
    active_engine = engine or _OneFormerEngine(requested_device)
    device = active_engine.load_model(model_dir)
    torch = active_engine.torch
    np = active_engine.np
    Image = active_engine.Image
    processor = active_engine.processor
    model = active_engine.model
    assert torch is not None and np is not None and Image is not None
    assert processor is not None and model is not None

    # Absent the request's categories, fall back to the full supported set so
    # the worker always emits the complete fixed mask inventory.
    if not categories:
        categories = tuple(APP_CATEGORY_LABELS)

    print("PROGRESS Preparing image...", flush=True)
    phase_started = time.perf_counter()
    with Image.open(input_path) as loaded:
        image = loaded.convert("RGB")
    width, height = image.size
    inputs = processor(
        images=image,
        task_inputs=["semantic"],
        return_tensors="pt",
    )
    inputs = {
        key: value.to(device) if hasattr(value, "to") else value
        for key, value in inputs.items()
    }
    preprocess_ms = (time.perf_counter() - phase_started) * 1000.0
    _emit_metric(
        "ai.mask.oneformer.worker.preprocess",
        phase_started,
        device=device,
        source_width=width,
        source_height=height,
    )

    print("PROGRESS Finding scene regions...", flush=True)
    if device.startswith("cuda"):
        torch.cuda.synchronize()
        torch.cuda.reset_peak_memory_stats()
    phase_started = time.perf_counter()
    with torch.inference_mode():
        outputs = model(**inputs)
    if device.startswith("cuda"):
        torch.cuda.synchronize()
    inference_ms = (time.perf_counter() - phase_started) * 1000.0
    inference_fields: dict[str, object] = {"device": device}
    if device.startswith("cuda"):
        inference_fields.update(
            cuda_peak_allocated_mb=round(
                torch.cuda.max_memory_allocated() / (1024 * 1024), 3
            ),
            cuda_peak_reserved_mb=round(
                torch.cuda.max_memory_reserved() / (1024 * 1024), 3
            ),
        )
    _emit_metric(
        "ai.mask.oneformer.worker.inference",
        phase_started,
        **inference_fields,
    )

    phase_started = time.perf_counter()
    segmentation = processor.post_process_semantic_segmentation(
        outputs,
        target_sizes=[(height, width)],
    )[0]
    segmentation = segmentation.detach().cpu().numpy().astype(np.int32, copy=False)
    postprocess_ms = (time.perf_counter() - phase_started) * 1000.0
    _emit_metric(
        "ai.mask.oneformer.worker.postprocess",
        phase_started,
        device=device,
        classes=int(segmentation.max(initial=0)) + 1,
    )

    phase_started = time.perf_counter()
    class_ids, source_labels = _category_class_ids(active_engine.id2label, categories)
    present_ids = set(int(value) for value in np.unique(segmentation).tolist())
    minimum = max(0.0, float(minimum_coverage))
    category_stats: dict[str, dict[str, object]] = {}
    hard_masks: dict[str, "np.ndarray"] = {}
    for category in categories:
        ids = class_ids.get(category, [])
        labels = source_labels.get(category, [])
        if ids:
            mask = np.isin(segmentation, ids)
            coverage = float(mask.mean())
            # Only report ADE labels that actually contribute pixels.
            present_labels = [
                label for class_id, label in zip(ids, labels) if class_id in present_ids
            ]
        else:
            mask = np.zeros(segmentation.shape, dtype=bool)
            coverage = 0.0
            present_labels = []
        if coverage < minimum:
            # Suppress sub-threshold specks at the source while still writing a
            # (blank) mask so the cache's fixed file set stays complete.
            hard_masks[category] = np.zeros(segmentation.shape, dtype=np.uint8)
            category_stats[category] = {"coverage": 0.0, "sourceLabels": []}
        else:
            hard_masks[category] = (mask.astype(np.uint8) * 255)
            category_stats[category] = {
                "coverage": coverage,
                "sourceLabels": present_labels,
            }
    merge_ms = (time.perf_counter() - phase_started) * 1000.0

    phase_started = time.perf_counter()
    output_dir.mkdir(parents=True, exist_ok=True)
    for category, mask in hard_masks.items():
        Image.fromarray(mask, mode="L").save(output_dir / f"{category}.png")
    _emit_metric(
        "ai.mask.oneformer.worker.mask_write",
        phase_started,
        categories=len(hard_masks),
    )

    result: dict[str, object] = {
        "device": device,
        "sourceSize": [width, height],
        "categoryStats": category_stats,
        "timingsMs": {
            "preprocess": round(preprocess_ms, 3),
            "inference": round(inference_ms, 3),
            "postprocess": round(postprocess_ms, 3),
            "merge": round(merge_ms, 3),
        },
    }
    _emit_metric(
        "ai.mask.oneformer.worker.total",
        total_started,
        device=device,
        categories=len(hard_masks),
        source_width=width,
        source_height=height,
    )
    if emit_result:
        print("RESULT " + json.dumps(result), flush=True)
    return result


def _parse_categories(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    categories: list[str] = []
    for item in value:
        normalized = str(item).strip().casefold()
        if normalized in APP_CATEGORY_LABELS and normalized not in categories:
            categories.append(normalized)
    return tuple(categories)
