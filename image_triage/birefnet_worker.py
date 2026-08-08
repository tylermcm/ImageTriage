from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
import time
import traceback
import types
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
    normalized = requested.strip().lower() or "auto"
    if normalized == "auto":
        return "cuda" if torch.cuda.is_available() else "cpu"
    if normalized.startswith("cuda") and torch.cuda.is_available():
        return normalized
    return "cpu"


def _prediction_tensor(output):
    value = output
    while isinstance(value, (tuple, list)):
        value = value[-1]
    logits = getattr(value, "logits", None)
    return logits if logits is not None else value


def _birefnet_rearrange(tensor, pattern: str, **axes):
    """The pinned BiRefNet checkpoint uses only these three einops patterns."""
    normalized = " ".join(pattern.split())
    grid_h = int(axes["hg"])
    grid_w = int(axes["wg"])
    if normalized in {
        "b c (hg h) (wg w) -> (b hg wg) c h w",
        "b c (hg h) (wg w) -> b (c hg wg) h w",
    }:
        batch, channels, total_h, total_w = tensor.shape
        if total_h % grid_h or total_w % grid_w:
            raise ValueError("BiRefNet patch grid does not divide the input tensor.")
        height = total_h // grid_h
        width = total_w // grid_w
        patches = tensor.reshape(batch, channels, grid_h, height, grid_w, width)
        if normalized.startswith("b c") and "-> (b hg wg)" in normalized:
            return patches.permute(0, 2, 4, 1, 3, 5).reshape(
                batch * grid_h * grid_w,
                channels,
                height,
                width,
            )
        return patches.permute(0, 1, 2, 4, 3, 5).reshape(
            batch,
            channels * grid_h * grid_w,
            height,
            width,
        )
    if normalized == "(b hg wg) c h w -> b c (hg h) (wg w)":
        patch_count, channels, height, width = tensor.shape
        grid_size = grid_h * grid_w
        if patch_count % grid_size:
            raise ValueError("BiRefNet patch count does not match its grid.")
        batch = patch_count // grid_size
        return tensor.reshape(
            batch,
            grid_h,
            grid_w,
            channels,
            height,
            width,
        ).permute(0, 3, 1, 4, 2, 5).reshape(
            batch,
            channels,
            grid_h * height,
            grid_w * width,
        )
    raise ValueError(f"Unsupported BiRefNet rearrange pattern: {pattern}")


def _install_birefnet_import_compatibility() -> None:
    try:
        importlib.import_module("einops")
    except ImportError:
        einops_module = types.ModuleType("einops")
        einops_module.rearrange = _birefnet_rearrange
        sys.modules["einops"] = einops_module

    try:
        importlib.import_module("kornia")
    except ImportError:
        kornia_module = types.ModuleType("kornia")
        filters_module = types.ModuleType("kornia.filters")

        def training_only_laplacian(*_args, **_kwargs):
            raise RuntimeError("BiRefNet's training-only Laplacian is unavailable during inference.")

        filters_module.laplacian = training_only_laplacian
        kornia_module.filters = filters_module
        sys.modules["kornia"] = kornia_module
        sys.modules["kornia.filters"] = filters_module


class _BiRefNetEngine:
    def __init__(self, requested_device: str) -> None:
        self.requested_device = requested_device
        self.device = "unknown"
        self.use_half = False
        self.model_dir: Path | None = None
        self.model = None
        self.cv2 = None
        self.np = None
        self.torch = None
        self.Image = None
        self.AutoModelForImageSegmentation = None

    def warm_imports(self) -> str:
        if self.torch is not None:
            return self.device

        phase_started = time.perf_counter()
        import cv2
        import numpy as np
        import torch
        from PIL import Image

        self.cv2 = cv2
        self.np = np
        self.torch = torch
        self.Image = Image
        self.device = _device_name(torch, self.requested_device)
        _emit_metric(
            "ai.mask.birefnet.worker.dependency_import",
            phase_started,
            torch_version=getattr(torch, "__version__", "unknown"),
            cuda_available=bool(torch.cuda.is_available()),
            cpu_threads=int(torch.get_num_threads()),
        )

        phase_started = time.perf_counter()
        _install_birefnet_import_compatibility()
        from transformers import AutoModelForImageSegmentation

        self.AutoModelForImageSegmentation = AutoModelForImageSegmentation
        _emit_metric("ai.mask.birefnet.worker.transformers_import", phase_started)
        print(f"DEVICE {self.device}", flush=True)
        return self.device

    def load_model(self, model_dir: Path) -> str:
        resolved_model_dir = model_dir.resolve()
        self.warm_imports()
        if self.model is not None and self.model_dir == resolved_model_dir:
            return self.device

        assert self.AutoModelForImageSegmentation is not None
        assert self.torch is not None
        print("PROGRESS Loading subject model...", flush=True)
        phase_started = time.perf_counter()
        model = self.AutoModelForImageSegmentation.from_pretrained(
            str(resolved_model_dir),
            trust_remote_code=True,
            local_files_only=True,
        )
        model.eval()
        model.to(self.device)
        self.use_half = self.device.startswith("cuda")
        if self.use_half:
            model.half()
        if self.device.startswith("cuda"):
            self.torch.cuda.synchronize()
        self.model = model
        self.model_dir = resolved_model_dir
        _emit_metric(
            "ai.mask.birefnet.worker.model_load",
            phase_started,
            device=self.device,
            precision="fp16" if self.use_half else "fp32",
        )
        return self.device


def generate_subject_mask(
    *,
    model_dir: Path,
    input_path: Path,
    output_path: Path,
    components_dir: Path | None,
    requested_device: str,
    engine: _BiRefNetEngine | None = None,
    emit_result: bool = True,
) -> dict[str, object]:
    total_started = time.perf_counter()
    active_engine = engine or _BiRefNetEngine(requested_device)
    device = active_engine.load_model(model_dir)
    cv2 = active_engine.cv2
    np = active_engine.np
    torch = active_engine.torch
    Image = active_engine.Image
    model = active_engine.model
    use_half = active_engine.use_half
    assert cv2 is not None and np is not None and torch is not None and Image is not None
    assert model is not None

    print("PROGRESS Preparing image...", flush=True)
    phase_started = time.perf_counter()
    with Image.open(input_path) as loaded:
        image = loaded.convert("RGB")
    original_size = image.size
    resized = np.asarray(
        image.resize((1024, 1024), Image.Resampling.BILINEAR),
        dtype=np.float32,
    ) / 255.0
    mean = np.asarray([0.485, 0.456, 0.406], dtype=np.float32)
    std = np.asarray([0.229, 0.224, 0.225], dtype=np.float32)
    normalized = (resized - mean) / std
    tensor = torch.from_numpy(normalized.transpose(2, 0, 1)).unsqueeze(0)
    tensor = tensor.to(device=device, dtype=torch.float16 if use_half else torch.float32)
    if device.startswith("cuda"):
        torch.cuda.synchronize()
        torch.cuda.reset_peak_memory_stats()
    _emit_metric(
        "ai.mask.birefnet.worker.preprocess",
        phase_started,
        device=device,
        source_width=original_size[0],
        source_height=original_size[1],
        inference_width=1024,
        inference_height=1024,
    )

    print("PROGRESS Finding subject...", flush=True)
    phase_started = time.perf_counter()
    with torch.inference_mode():
        prediction = _prediction_tensor(model(tensor)).sigmoid()
    if device.startswith("cuda"):
        torch.cuda.synchronize()
    inference_fields: dict[str, object] = {"device": device}
    if device.startswith("cuda"):
        inference_fields.update(
            cuda_allocated_mb=round(torch.cuda.memory_allocated() / (1024 * 1024), 3),
            cuda_peak_allocated_mb=round(
                torch.cuda.max_memory_allocated() / (1024 * 1024), 3
            ),
            cuda_peak_reserved_mb=round(
                torch.cuda.max_memory_reserved() / (1024 * 1024), 3
            ),
        )
    _emit_metric(
        "ai.mask.birefnet.worker.inference",
        phase_started,
        **inference_fields,
    )
    phase_started = time.perf_counter()
    while prediction.ndim > 2:
        prediction = prediction[0]
    pixels = (
        prediction.detach().float().cpu().clamp(0.0, 1.0).numpy() * 255.0
    ).astype(np.uint8)
    mask = Image.fromarray(pixels, mode="L").resize(
        original_size,
        Image.Resampling.BILINEAR,
    )
    mask_pixels = np.asarray(mask, dtype=np.uint8)
    _emit_metric(
        "ai.mask.birefnet.worker.postprocess",
        phase_started,
        output_width=original_size[0],
        output_height=original_size[1],
    )
    phase_started = time.perf_counter()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    mask.save(output_path)
    _emit_metric(
        "ai.mask.birefnet.worker.mask_write",
        phase_started,
        output_bytes=output_path.stat().st_size,
    )
    components = []
    phase_started = time.perf_counter()
    if components_dir is not None:
        components_dir.mkdir(parents=True, exist_ok=True)
        for stale_path in components_dir.glob("subject-*.png"):
            stale_path.unlink(missing_ok=True)
        # BiRefNet returns one soft foreground map. Connected foreground islands
        # give us a useful, cheap instance split for separated people while SAM
        # remains the future path for touching or overlapping subjects.
        support = (mask_pixels >= 24).astype(np.uint8)
        count, labels, stats, centroids = cv2.connectedComponentsWithStats(
            support,
            connectivity=8,
        )
        image_area = max(1, int(mask_pixels.shape[0] * mask_pixels.shape[1]))
        minimum_confident_area = max(64, int(round(image_area * 0.0005)))
        candidates = []
        for component_index in range(1, count):
            region = labels == component_index
            confident_area = int(np.count_nonzero(region & (mask_pixels >= 128)))
            if confident_area < minimum_confident_area:
                continue
            x, y, width, height, support_area = (
                int(value) for value in stats[component_index]
            )
            center_x, center_y = (float(value) for value in centroids[component_index])
            candidates.append(
                (
                    center_x,
                    component_index,
                    region,
                    x,
                    y,
                    width,
                    height,
                    int(support_area),
                    confident_area,
                    center_y,
                )
            )
        candidates.sort(key=lambda item: item[0])
        for display_index, candidate in enumerate(candidates, start=1):
            (
                center_x,
                component_index,
                region,
                x,
                y,
                width,
                height,
                support_area,
                confident_area,
                center_y,
            ) = candidate
            component_id = f"subject-{display_index:02d}"
            component_path = components_dir / f"{component_id}.png"
            component_pixels = np.where(region, mask_pixels, 0).astype(np.uint8)
            Image.fromarray(component_pixels, mode="L").save(component_path)
            components.append(
                {
                    "id": component_id,
                    "path": component_path.name,
                    "bbox": [x, y, width, height],
                    "centroid": [center_x, center_y],
                    "areaFraction": support_area / image_area,
                    "confidentAreaFraction": confident_area / image_area,
                    "labelIndex": component_index,
                }
            )
    _emit_metric(
        "ai.mask.birefnet.worker.component_split",
        phase_started,
        components=len(components),
    )
    _emit_metric(
        "ai.mask.birefnet.worker.total",
        total_started,
        device=device,
        components=len(components),
        source_width=original_size[0],
        source_height=original_size[1],
    )
    result: dict[str, object] = {
        "device": device,
        "width": original_size[0],
        "height": original_size[1],
        "components": components,
    }
    if emit_result:
        print("RESULT " + json.dumps(result), flush=True)
    return result


def _server_response(
    request_id: object,
    *,
    result: dict[str, object] | None = None,
    error: str = "",
) -> None:
    print(
        "RESPONSE "
        + json.dumps(
            {
                "id": request_id,
                "ok": not error,
                "result": result or {},
                "error": error,
            },
            default=str,
        ),
        flush=True,
    )


def run_server(requested_device: str) -> int:
    engine = _BiRefNetEngine(requested_device)
    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue
        request_id: object = None
        try:
            request = json.loads(line)
            if not isinstance(request, dict):
                raise ValueError("Worker request must be a JSON object.")
            request_id = request.get("id")
            command = str(request.get("command") or "").strip().casefold()
            if command == "warm-imports":
                device = engine.warm_imports()
                _server_response(request_id, result={"device": device, "stage": "imports"})
            elif command == "load-model":
                model_dir = Path(str(request.get("modelDir") or "")).resolve()
                device = engine.load_model(model_dir)
                _server_response(request_id, result={"device": device, "stage": "model"})
            elif command == "infer":
                result = generate_subject_mask(
                    model_dir=Path(str(request.get("modelDir") or "")).resolve(),
                    input_path=Path(str(request.get("input") or "")).resolve(),
                    output_path=Path(str(request.get("output") or "")).resolve(),
                    components_dir=(
                        Path(str(request["componentsDir"])).resolve()
                        if request.get("componentsDir")
                        else None
                    ),
                    requested_device=requested_device,
                    engine=engine,
                    emit_result=False,
                )
                _server_response(request_id, result=result)
            elif command == "shutdown":
                _server_response(request_id, result={"stage": "shutdown"})
                return 0
            else:
                raise ValueError(f"Unknown worker command: {command or '<empty>'}")
        except Exception as exc:
            detail = "".join(traceback.format_exception_only(type(exc), exc)).strip()
            _server_response(request_id, error=detail or str(exc))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate a BiRefNet subject mask.")
    parser.add_argument("--server", action="store_true")
    parser.add_argument("--model-dir", type=Path)
    parser.add_argument("--input", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--components-dir", type=Path)
    parser.add_argument("--device", default="auto")
    args = parser.parse_args(argv)
    if args.server:
        return run_server(args.device)
    if args.model_dir is None or args.input is None or args.output is None:
        parser.error("--model-dir, --input, and --output are required outside server mode")
    generate_subject_mask(
        model_dir=args.model_dir.resolve(),
        input_path=args.input.resolve(),
        output_path=args.output.resolve(),
        components_dir=(
            args.components_dir.resolve() if args.components_dir is not None else None
        ),
        requested_device=args.device,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
