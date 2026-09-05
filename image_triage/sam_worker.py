from __future__ import annotations

"""SAM 2.1 promptable-segmentation engine (library).

Imported by the unified MaskEngine host (``mask_engine_worker``) and run under
the managed AI runtime Python — never inside the Qt GUI process. Depends only on
the AI runtime packages (torch, transformers, PIL, numpy).

Unlike BiRefNet/OneFormer (one-shot image -> mask), SAM is *interactive*: an
image is loaded once (``embed``), then each click (``segment``) turns points
into a mask. The engine keeps the current image resident so repeated clicks are
cheap. A "largest-mask" rule resolves SAM's single-click part/whole ambiguity so
a click on a torso returns the whole person.
"""

import json
import os
import time
from pathlib import Path


METRIC_PREFIX = "AI_METRIC "
METRIC_ENV_VAR = "IMAGE_TRIAGE_AI_METRICS"


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


def _choose_mask_index(areas: list[int], frame: int) -> int:
    """Largest-mask rule: pick the biggest of SAM's candidate masks (part/whole
    -> whole) but never the near-whole-frame mask, which is usually background."""
    if not areas:
        raise ValueError("No candidate masks.")
    order = sorted(range(len(areas)), key=lambda i: areas[i], reverse=True)
    for index in order:
        if areas[index] < 0.9 * frame:
            return index
    return order[0]


class _SamEngine:
    def __init__(self, requested_device: str) -> None:
        self.requested_device = requested_device
        self.device = "unknown"
        self.model_dir: Path | None = None
        self.torch = None
        self.np = None
        self.Image = None
        self.Sam2Model = None
        self.Sam2Processor = None
        self.processor = None
        self.model = None
        # Current image state (reset by ``embed``).
        self._image = None
        self._image_key: str | None = None
        self._size: tuple[int, int] = (0, 0)

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
            "ai.mask.sam.worker.dependency_import",
            phase_started,
            torch_version=getattr(torch, "__version__", "unknown"),
            cuda_available=bool(torch.cuda.is_available()),
        )
        from transformers import Sam2Model, Sam2Processor

        self.Sam2Model = Sam2Model
        self.Sam2Processor = Sam2Processor
        print(f"DEVICE {self.device}", flush=True)
        return self.device

    def load_model(self, model_dir: Path) -> str:
        resolved = model_dir.resolve()
        self.warm_imports()
        if self.model is not None and self.model_dir == resolved:
            return self.device
        assert self.Sam2Model is not None and self.Sam2Processor is not None
        print("PROGRESS Loading selection model...", flush=True)
        phase_started = time.perf_counter()
        self.processor = self.Sam2Processor.from_pretrained(str(resolved), local_files_only=True)
        model = self.Sam2Model.from_pretrained(str(resolved), local_files_only=True)
        model.to(self.device)
        model.eval()
        if self.device.startswith("cuda"):
            self.torch.cuda.synchronize()
        self.model = model
        self.model_dir = resolved
        _emit_metric("ai.mask.sam.worker.model_load", phase_started, device=self.device)
        return self.device

    def embed(self, input_path: Path, *, image_key: str | None = None) -> tuple[int, int]:
        """Load the image the following ``segment`` calls will prompt against."""
        assert self.Image is not None
        phase_started = time.perf_counter()
        with self.Image.open(input_path) as loaded:
            image = loaded.convert("RGB")
        self._image = image
        self._image_key = image_key or str(Path(input_path).resolve())
        self._size = (image.width, image.height)
        _emit_metric(
            "ai.mask.sam.worker.embed",
            phase_started,
            device=self.device,
            width=image.width,
            height=image.height,
        )
        return image.width, image.height

    def segment(
        self,
        *,
        points: list[tuple[float, float]],
        labels: list[int],
        output_path: Path,
        minimum_area: float = 0.0,
        image_key: str | None = None,
    ) -> dict[str, object]:
        return self.segment_many(
            point_groups=[points],
            label_groups=[labels],
            output_paths=[output_path],
            minimum_area=minimum_area,
            image_key=image_key,
        )[0]

    def segment_many(
        self,
        *,
        point_groups: list[list[tuple[float, float]]],
        label_groups: list[list[int]],
        output_paths: list[Path],
        minimum_area: float = 0.0,
        image_key: str | None = None,
    ) -> list[dict[str, object]]:
        """Evaluate independent prompt objects in one SAM forward pass."""
        if self._image is None:
            raise RuntimeError("SAM has no embedded image; call embed first.")
        if image_key is not None and self._image_key is not None and image_key != self._image_key:
            raise RuntimeError("SAM embedded image does not match the requested image.")
        torch = self.torch
        np = self.np
        assert torch is not None and np is not None
        assert self.model is not None and self.processor is not None
        if not point_groups or any(not points for points in point_groups):
            raise ValueError("At least one non-empty point group is required.")
        if len(point_groups) != len(label_groups) or len(point_groups) != len(output_paths):
            raise ValueError("Point groups, label groups, and output paths must have equal lengths.")
        if any(len(points) != len(labels) for points, labels in zip(point_groups, label_groups)):
            raise ValueError("Each point group must have the same number of labels.")

        phase_started = time.perf_counter()
        # transformers SAM2 point nesting: [batch, object, point, xy] and
        # [batch, object, point] for labels.
        input_points = [
            [[[float(x), float(y)] for (x, y) in points] for points in point_groups]
        ]
        input_labels = [[[int(v) for v in labels] for labels in label_groups]]
        inputs = self.processor(
            images=self._image,
            input_points=input_points,
            input_labels=input_labels,
            return_tensors="pt",
        ).to(self.device)
        with torch.inference_mode():
            outputs = self.model(**inputs)
        if self.device.startswith("cuda"):
            torch.cuda.synchronize()
        masks = self.processor.post_process_masks(outputs.pred_masks, inputs["original_sizes"])[0]
        object_candidates = np.asarray(masks.detach().cpu().numpy())
        iou_scores = getattr(outputs, "iou_scores", None)
        object_ious = (
            np.asarray(iou_scores.detach().cpu().numpy())[0]
            if iou_scores is not None
            else None
        )
        results: list[dict[str, object]] = []
        for object_index, (candidates, output_path) in enumerate(zip(object_candidates, output_paths)):
            binary = np.asarray(candidates) > 0.5
            height, width = binary.shape[-2:]
            areas = binary.reshape(binary.shape[0], -1).sum(axis=1).astype(np.int64)
            frame = int(height * width)
            chosen = _choose_mask_index([int(a) for a in areas.tolist()], frame)
            chosen_binary = binary[chosen]
            mask = chosen_binary.astype(np.uint8) * 255
            iou = float(object_ious[object_index][chosen]) if object_ious is not None else 0.0
            rows = np.any(chosen_binary, axis=1)
            cols = np.any(chosen_binary, axis=0)
            if rows.any() and cols.any():
                y0, y1 = np.where(rows)[0][[0, -1]]
                x0, x1 = np.where(cols)[0][[0, -1]]
                bounds = [int(x0), int(y0), int(x1) + 1, int(y1) + 1]
            else:
                bounds = [0, 0, 0, 0]
            coverage = float(areas[chosen]) / frame if frame else 0.0
            output_path.parent.mkdir(parents=True, exist_ok=True)
            self.Image.fromarray(mask, mode="L").save(output_path)
            results.append(
                {
                    "device": self.device,
                    "maskPath": str(output_path),
                    "sourceSize": [width, height],
                    "bounds": bounds,
                    "coverage": coverage,
                    "iou": iou,
                    "chosenMask": chosen,
                    "suppressed": coverage < max(0.0, float(minimum_area)),
                }
            )
        _emit_metric(
            "ai.mask.sam.worker.segment",
            phase_started,
            device=self.device,
            objects=len(point_groups),
            points=sum(len(points) for points in point_groups),
        )
        return results
