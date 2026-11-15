from __future__ import annotations

"""Object-removal fill (LaMa, ONNX) - library for the unified mask engine host.

Imported by ``mask_engine_worker`` and run under the managed AI runtime Python, never inside the Qt GUI process. One-shot like
the other engines: a picture crop and a mask in, the same crop with the masked part filled in out. LaMa works at 512 x 512,
so the crop is fitted into that (keeping its proportions), filled, and scaled back; compositing it into the photo, matching
its grain and storing it is the application's job (``image_triage.erase``).

Model: ``Carve/LaMa-ONNX`` ``lama_fp32.onnx``: input ``image`` float 0..1 (1, 3, 512, 512) and ``mask`` float, 1 = fill
(1, 1, 512, 512); output float 0..255 (1, 3, 512, 512). Apache-2.0 (a port of advimman/lama's big-lama).
"""

import json
import time
from pathlib import Path

MODEL_FILE = "lama_fp32.onnx"
MODEL_SIDE = 512
RESULT_PREFIX = "RESULT "


def _providers(torch, requested: str, available) -> tuple[list[str], str]:
    wants_gpu = (requested or "auto").strip().lower() != "cpu"
    if wants_gpu and "CUDAExecutionProvider" in available and torch is not None and torch.cuda.is_available():
        return ["CUDAExecutionProvider", "CPUExecutionProvider"], "cuda"
    return ["CPUExecutionProvider"], "cpu"


class _InpaintEngine:
    def __init__(self, requested_device: str) -> None:
        self.requested_device = requested_device
        self.device = "unknown"
        self.model_dir: Path | None = None
        self.session = None
        self.np = None
        self.Image = None
        self._ort = None
        self._torch = None

    def warm_imports(self) -> str:
        if self._ort is not None:
            return self.device
        import numpy as np
        from PIL import Image

        try:
            # onnxruntime's CUDA provider finds its libraries only once torch has loaded them
            import torch
        except Exception:
            torch = None
        import onnxruntime as ort

        self.np, self.Image, self._ort, self._torch = np, Image, ort, torch
        self.device = _providers(torch, self.requested_device, ort.get_available_providers())[1]
        print(f"DEVICE {self.device}", flush=True)
        return self.device

    def load_model(self, model_dir: Path) -> str:
        resolved = Path(model_dir).resolve()
        self.warm_imports()
        if self.session is not None and self.model_dir == resolved:
            return self.device
        print("PROGRESS Loading the fill model...", flush=True)
        providers, self.device = _providers(self._torch, self.requested_device, self._ort.get_available_providers())
        options = self._ort.SessionOptions()
        options.log_severity_level = 3
        self.session = self._ort.InferenceSession(str(resolved / MODEL_FILE), options, providers=providers)
        self.model_dir = resolved
        return self.device


def fit_to_model(np, Image, rgb, hole):
    """``(image, mask, (top, left, height, width))``: the crop (uint8 h x w x 3) and its hole (uint8, 255 = fill) scaled so the
    longer side is the model's size, then centred on a square canvas whose margins repeat the picture (so the model sees
    believable surroundings there, and is not asked to fill them). The box is where the picture sits on the canvas."""
    h, w = rgb.shape[:2]
    scale = MODEL_SIDE / max(h, w)
    nh, nw = max(1, round(h * scale)), max(1, round(w * scale))
    resample = Image.Resampling.LANCZOS if scale < 1 else Image.Resampling.BICUBIC
    small = np.asarray(Image.fromarray(rgb).resize((nw, nh), resample))
    hole_small = np.asarray(Image.fromarray(hole).resize((nw, nh), Image.Resampling.BILINEAR)) > 127
    top, left = (MODEL_SIDE - nh) // 2, (MODEL_SIDE - nw) // 2
    pads = ((top, MODEL_SIDE - nh - top), (left, MODEL_SIDE - nw - left))
    mode = "reflect" if max(pads[0] + pads[1]) < min(nh, nw) else "edge"       # reflecting needs less margin than picture
    canvas = np.pad(small, pads + ((0, 0),), mode=mode)
    mask = np.pad(hole_small, pads, mode="constant", constant_values=False)
    return canvas, mask.astype(np.float32), (top, left, nh, nw)


def fill(engine: "_InpaintEngine", rgb, hole):
    """The crop ``rgb`` with its hole filled by the model, same size and type (uint8 h x w x 3)."""
    np, Image = engine.np, engine.Image
    h, w = rgb.shape[:2]
    canvas, mask, (top, left, nh, nw) = fit_to_model(np, Image, rgb, hole)
    image = (canvas.astype(np.float32) / 255).transpose(2, 0, 1)[None]
    out = engine.session.run(None, {"image": image, "mask": mask[None, None]})[0][0].transpose(1, 2, 0)
    out = np.clip(np.rint(out), 0, 255).astype(np.uint8)[top:top + nh, left:left + nw]
    resample = Image.Resampling.BICUBIC if max(h, w) > MODEL_SIDE else Image.Resampling.LANCZOS
    return np.asarray(Image.fromarray(out).resize((w, h), resample))


def generate_inpaint(
    *,
    model_dir: Path,
    input_path: Path,
    mask_path: Path,
    output_path: Path,
    requested_device: str,
    engine: _InpaintEngine | None = None,
    emit_result: bool = True,
) -> dict[str, object]:
    total_started = time.perf_counter()
    active = engine or _InpaintEngine(requested_device)
    device = active.load_model(model_dir)
    load_seconds = time.perf_counter() - total_started
    np, Image = active.np, active.Image
    print("PROGRESS Filling in...", flush=True)
    with Image.open(input_path) as opened:
        rgb = np.asarray(opened.convert("RGB"))
    with Image.open(mask_path) as opened:
        hole = np.asarray(opened.convert("L"))
    if hole.shape != rgb.shape[:2]:
        raise ValueError("The mask does not match the picture")
    started = time.perf_counter()
    filled = fill(active, rgb, hole)
    inference = time.perf_counter() - started
    output_path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(filled).save(output_path)
    result: dict[str, object] = {
        "device": device,
        "width": int(rgb.shape[1]),
        "height": int(rgb.shape[0]),
        "outputPath": str(output_path),
        "modelLoadSeconds": round(load_seconds, 3),
        "inferenceSeconds": round(inference, 3),
    }
    if emit_result:
        print(RESULT_PREFIX + json.dumps(result), flush=True)
    return result
