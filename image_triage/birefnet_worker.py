from __future__ import annotations

import argparse
import importlib
import json
import sys
import types
from pathlib import Path


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


def generate_subject_mask(
    *,
    model_dir: Path,
    input_path: Path,
    output_path: Path,
    components_dir: Path | None,
    requested_device: str,
) -> str:
    import cv2
    import numpy as np
    import torch
    from PIL import Image

    _install_birefnet_import_compatibility()
    from transformers import AutoModelForImageSegmentation

    device = _device_name(torch, requested_device)
    print(f"DEVICE {device}", flush=True)
    print("PROGRESS Loading subject model...", flush=True)
    model = AutoModelForImageSegmentation.from_pretrained(
        str(model_dir),
        trust_remote_code=True,
        local_files_only=True,
    )
    model.eval()
    model.to(device)
    use_half = device.startswith("cuda")
    if use_half:
        model.half()

    print("PROGRESS Preparing image...", flush=True)
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

    print("PROGRESS Finding subject...", flush=True)
    with torch.inference_mode():
        prediction = _prediction_tensor(model(tensor)).sigmoid()
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
    output_path.parent.mkdir(parents=True, exist_ok=True)
    mask.save(output_path)
    components = []
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
    print(
        "RESULT "
        + json.dumps(
            {
                "device": device,
                "width": original_size[0],
                "height": original_size[1],
                "components": components,
            }
        ),
        flush=True,
    )
    return device


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate a BiRefNet subject mask.")
    parser.add_argument("--model-dir", required=True, type=Path)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--components-dir", type=Path)
    parser.add_argument("--device", default="auto")
    args = parser.parse_args(argv)
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
