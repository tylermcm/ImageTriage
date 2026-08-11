from __future__ import annotations

"""Monocular depth engine (Depth Anything V2 Small) — library.

Imported by the unified MaskEngine host (``mask_engine_worker``) and run under
the managed AI runtime Python, never inside the Qt GUI process. One-shot like
BiRefNet/OneFormer: image in, a single-channel relative-depth map out (255 =
nearest, 0 = farthest), written as an 8-bit PNG. That map drives depth-aware
render effects (lens blur, atmosphere) — the first non-mask model output the
host carries.
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


class _DepthEngine:
    def __init__(self, requested_device: str) -> None:
        self.requested_device = requested_device
        self.device = "unknown"
        self.model_dir: Path | None = None
        self.torch = None
        self.np = None
        self.Image = None
        self.AutoImageProcessor = None
        self.AutoModelForDepthEstimation = None
        self.processor = None
        self.model = None

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
            "ai.mask.depth.worker.dependency_import",
            phase_started,
            torch_version=getattr(torch, "__version__", "unknown"),
            cuda_available=bool(torch.cuda.is_available()),
        )
        from transformers import AutoImageProcessor, AutoModelForDepthEstimation

        self.AutoImageProcessor = AutoImageProcessor
        self.AutoModelForDepthEstimation = AutoModelForDepthEstimation
        print(f"DEVICE {self.device}", flush=True)
        return self.device

    def load_model(self, model_dir: Path) -> str:
        resolved = model_dir.resolve()
        self.warm_imports()
        if self.model is not None and self.model_dir == resolved:
            return self.device
        assert self.AutoImageProcessor is not None
        assert self.AutoModelForDepthEstimation is not None
        print("PROGRESS Loading depth model...", flush=True)
        phase_started = time.perf_counter()
        self.processor = self.AutoImageProcessor.from_pretrained(
            str(resolved), local_files_only=True
        )
        model = self.AutoModelForDepthEstimation.from_pretrained(
            str(resolved), local_files_only=True
        )
        model.to(self.device)
        model.eval()
        if self.device.startswith("cuda"):
            self.torch.cuda.synchronize()
        self.model = model
        self.model_dir = resolved
        _emit_metric("ai.mask.depth.worker.model_load", phase_started, device=self.device)
        return self.device


def generate_depth(
    *,
    model_dir: Path,
    input_path: Path,
    output_path: Path,
    requested_device: str,
    engine: _DepthEngine | None = None,
    emit_result: bool = True,
) -> dict[str, object]:
    total_started = time.perf_counter()
    active = engine or _DepthEngine(requested_device)
    device = active.load_model(model_dir)
    torch = active.torch
    np = active.np
    Image = active.Image
    assert torch is not None and np is not None and Image is not None
    assert active.model is not None and active.processor is not None

    print("PROGRESS Estimating depth...", flush=True)
    phase_started = time.perf_counter()
    with Image.open(input_path) as loaded:
        image = loaded.convert("RGB")
    width, height = image.size
    inputs = active.processor(images=image, return_tensors="pt").to(device)
    with torch.inference_mode():
        outputs = active.model(**inputs)
    predicted = outputs.predicted_depth  # (1, h, w), higher = nearer
    depth = torch.nn.functional.interpolate(
        predicted.unsqueeze(1),
        size=(height, width),
        mode="bicubic",
        align_corners=False,
    ).squeeze()
    if device.startswith("cuda"):
        torch.cuda.synchronize()
    depth_np = depth.detach().float().cpu().numpy()
    _emit_metric(
        "ai.mask.depth.worker.inference",
        phase_started,
        device=device,
        width=width,
        height=height,
    )

    phase_started = time.perf_counter()
    d_min = float(depth_np.min())
    d_max = float(depth_np.max())
    if d_max > d_min:
        normalized = (depth_np - d_min) / (d_max - d_min)
    else:
        normalized = np.zeros_like(depth_np)
    pixels = np.clip(normalized * 255.0, 0.0, 255.0).astype(np.uint8)  # 255 = nearest
    output_path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(pixels, mode="L").save(output_path)
    _emit_metric(
        "ai.mask.depth.worker.postprocess",
        phase_started,
        output_bytes=output_path.stat().st_size,
    )
    _emit_metric("ai.mask.depth.worker.total", total_started, device=device)

    result: dict[str, object] = {
        "device": device,
        "width": width,
        "height": height,
        "depthPath": str(output_path),
        "min": d_min,
        "max": d_max,
    }
    if emit_result:
        print("RESULT " + json.dumps(result), flush=True)
    return result
