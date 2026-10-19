"""Ranker worker subprocess: ``python -m image_triage.cascade.rankers.worker --kind maniqa|qrealign --model-dir DIR --device DEVICE``.

Runs inside the managed AI runtime (torch, timm, transformers live there; the app process never imports them). Speaks the line protocol in
``protocol.py`` on stdin/stdout. Everything heavy is imported inside functions, so importing this module is cheap and torch-free.
Precision: fp16 on CUDA (a Volta card has no bf16 and Q-ReAlign hangs on it), fp32 on CPU.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from pathlib import Path
from typing import Callable

from . import protocol

MANIQA_CROPS = 20
MANIQA_CROP_SIZE = 224
MANIQA_LONG_EDGE = 1280
MANIQA_CROP_SEED = 20
QREALIGN_LONG_EDGE = 1024
QREALIGN_QUESTION = "Can you rate the aesthetics of this picture?"
QREALIGN_ANSWER_STEM = "The aesthetics of the image is"
QREALIGN_LEVELS = ("excellent", "good", "fair", "poor", "bad")
QREALIGN_WEIGHTS = (1.0, 0.75, 0.5, 0.25, 0.0)


class WorkerFatal(RuntimeError):
    def __init__(self, category: str, message: str) -> None:
        super().__init__(message)
        self.category = category


def pick_device(torch, requested: str) -> str:
    """The same rule as ``birefnet_worker._device_name``: honour an explicit CUDA request only when CUDA exists."""
    value = (requested or "auto").strip().lower() or "auto"
    if value == "auto":
        return "cuda" if torch.cuda.is_available() else "cpu"
    if value.startswith("cuda") and torch.cuda.is_available():
        return value
    return "cpu"


def load_image(path: str, long_edge: int):
    """RGB PIL image, EXIF-rotated, bounded to ``long_edge`` on the long side (never enlarged)."""
    from PIL import Image, ImageOps

    with Image.open(path) as handle:
        image = ImageOps.exif_transpose(handle).convert("RGB")
    biggest = max(image.size)
    if biggest > long_edge:
        scale = long_edge / biggest
        image = image.resize((max(1, round(image.width * scale)), max(1, round(image.height * scale))), Image.Resampling.LANCZOS)
    return image


# -- MANIQA ---------------------------------------------------------------------------------------------------------------


class ManiqaEngine:
    def __init__(self, model_dir: Path, device: str) -> None:
        import numpy as np
        import torch

        from ._maniqa_net import MANIQA
        from .bundles import MANIQA_CHECKPOINT

        self.np, self.torch = np, torch
        self.device = pick_device(torch, device)
        self.half = self.device.startswith("cuda")
        checkpoint = model_dir / MANIQA_CHECKPOINT
        if not checkpoint.is_file():
            raise WorkerFatal("weights_missing", f"{MANIQA_CHECKPOINT} not found in {model_dir}")
        net = MANIQA(embed_dim=768, num_outputs=1, dim_mlp=768, patch_size=8, img_size=224, window_size=4, depths=[2, 2],
                     num_heads=[4, 4], num_tab=2, scale=0.8)
        state = torch.load(str(checkpoint), map_location="cpu", weights_only=True)
        result = net.load_state_dict(state, strict=False)
        # Position-index buffers are recomputed at construction; anything else missing means the wrong checkpoint.
        missing = [k for k in result.missing_keys if not k.endswith(("relative_position_index", "attn_mask"))]
        if missing:
            raise WorkerFatal("weights_missing", f"{MANIQA_CHECKPOINT} does not match the MANIQA network ({len(missing)} missing tensors, e.g. {missing[0]})")
        net = net.to(self.device).eval()
        self.net = net.half() if self.half else net

    def score(self, path: str) -> float:
        np, torch = self.np, self.torch
        image = load_image(path, MANIQA_LONG_EDGE)
        if min(image.size) <= MANIQA_CROP_SIZE:  # a crop must fit; tiny frames are enlarged so their short edge clears it
            scale = (MANIQA_CROP_SIZE + 1) / min(image.size)
            from PIL import Image

            image = image.resize((max(MANIQA_CROP_SIZE + 1, round(image.width * scale)), max(MANIQA_CROP_SIZE + 1, round(image.height * scale))), Image.Resampling.LANCZOS)
        pixels = np.asarray(image, dtype=np.float32) / 255.0
        height, width = pixels.shape[:2]
        rng = np.random.default_rng(MANIQA_CROP_SEED)  # same crops procedure for every frame
        crops = []
        for _ in range(MANIQA_CROPS):
            top, left = int(rng.integers(0, height - MANIQA_CROP_SIZE)), int(rng.integers(0, width - MANIQA_CROP_SIZE))
            crops.append(pixels[top:top + MANIQA_CROP_SIZE, left:left + MANIQA_CROP_SIZE])
        batch = torch.from_numpy(np.stack(crops)).permute(0, 3, 1, 2)
        batch = ((batch - 0.5) / 0.5).to(self.device, dtype=torch.float16 if self.half else torch.float32)
        with torch.inference_mode():
            return float(self.net(batch).float().mean())


# -- Q-ReAlign ------------------------------------------------------------------------------------------------------------


class QRealignEngine:
    def __init__(self, model_dir: Path, device: str) -> None:
        import torch
        import transformers

        self.torch = torch
        self.device = pick_device(torch, device)
        self.half = self.device.startswith("cuda")
        dtype = torch.float16 if self.half else torch.float32
        try:
            self.processor = transformers.AutoProcessor.from_pretrained(str(model_dir), local_files_only=True)
            auto = getattr(transformers, "AutoModelForImageTextToText", None)
            model_class = auto or transformers.Qwen3_5ForConditionalGeneration
            model = model_class.from_pretrained(str(model_dir), dtype=dtype, local_files_only=True)
        except (OSError, ValueError, AttributeError) as exc:
            raise WorkerFatal("weights_missing", f"Q-ReAlign could not be loaded from {model_dir}: {exc}") from exc
        self.model = model.to(self.device).eval()
        tokenizer = self.processor.tokenizer
        # The word that follows "...of the image is" starts with a space; its first token carries the choice.
        self.level_ids = [tokenizer(" " + word, add_special_tokens=False)["input_ids"][0] for word in QREALIGN_LEVELS]
        if len(set(self.level_ids)) != len(self.level_ids):
            raise WorkerFatal("worker_failed", "Q-ReAlign level words share a first token; the tokenizer is not the expected one.")
        self.weights = torch.tensor(QREALIGN_WEIGHTS, dtype=torch.float32, device=self.device)

    def score(self, path: str) -> float:
        torch = self.torch
        image = load_image(path, QREALIGN_LONG_EDGE)
        messages = [{"role": "user", "content": [{"type": "image"}, {"type": "text", "text": QREALIGN_QUESTION}]}]
        prompt = self.processor.apply_chat_template(messages, tokenize=False, add_generation_prompt=True, enable_thinking=False)
        inputs = self.processor(text=[prompt + QREALIGN_ANSWER_STEM], images=[image], return_tensors="pt")
        inputs = {k: (v.to(self.device) if hasattr(v, "to") else v) for k, v in inputs.items()}
        if self.half:
            inputs = {k: (v.half() if getattr(v, "is_floating_point", lambda: False)() else v) for k, v in inputs.items()}
        with torch.inference_mode():
            logits = self.model(**inputs).logits[0, -1].float()
        probabilities = torch.softmax(logits[self.level_ids], dim=-1)
        return float((probabilities * self.weights).sum())


ENGINES: dict[str, Callable[[Path, str], object]] = {"maniqa": ManiqaEngine, "qrealign": QRealignEngine}


# -- protocol loop --------------------------------------------------------------------------------------------------------


def serve(engine, stdin, emit: Callable[[dict], None]) -> None:
    """Answer score requests until ``quit`` or EOF. ``engine.score(path) -> float`` is the only thing the loop needs."""
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        request = json.loads(line)
        op = request.get("op")
        if op == "quit":
            return
        if op != "score":
            continue
        request_id = request.get("id")
        for item in request.get("items", []):
            key = item["key"]
            try:
                emit({"event": "score", "id": request_id, "key": key, "value": engine.score(item["path"])})
            except (OSError, ValueError) as exc:  # unreadable or corrupt image: this frame only
                emit({"event": "frame_error", "id": request_id, "key": key, "message": f"{type(exc).__name__}: {exc}"})
        emit({"event": "done", "id": request_id})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--kind", required=True, choices=sorted(ENGINES))
    parser.add_argument("--model-dir", required=True)
    parser.add_argument("--device", default="auto")
    args = parser.parse_args(argv)

    out = sys.stdout
    sys.stdout = sys.stderr  # stray prints from libraries must not corrupt the protocol stream

    def emit(message: dict) -> None:
        out.write(protocol.encode_event(message) + "\n")
        out.flush()

    started = time.perf_counter()
    try:
        engine = ENGINES[args.kind](Path(args.model_dir), args.device)
    except WorkerFatal as exc:
        emit({"event": "error", "category": exc.category, "message": str(exc)})
        return 2
    except ImportError as exc:
        emit({"event": "error", "category": "missing_module", "message": f"{exc.name or exc} is not installed in the AI runtime"})
        return 2
    except Exception as exc:  # noqa: BLE001 - report anything at the boundary instead of dying silently
        traceback.print_exc()
        emit({"event": "error", "category": "worker_failed", "message": f"{type(exc).__name__}: {exc}"})
        return 2
    emit({"event": "ready", "device": engine.device, "precision": "fp16" if engine.half else "fp32",
          "seconds": round(time.perf_counter() - started, 2)})
    try:
        serve(engine, sys.stdin, emit)
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        emit({"event": "error", "category": "worker_failed", "message": f"{type(exc).__name__}: {exc}"})
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
