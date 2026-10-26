"""Ranker worker subprocess: ``python -m image_triage.cascade.rankers.worker --kind maniqa|qrealign|blink --model-dir DIR --device DEVICE``.

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
FACES_LONG_EDGE = 1280  # the blink check's size: the face finder sees the same pixels
FACES_MAX = 10
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
    """RGB PIL image, upright, bounded to ``long_edge`` on the long side (never enlarged).

    A RAW file is read through its embedded JPEG preview with the app's own extractor and the container's orientation, never decoded (a RAW decode
    is seconds per frame and PIL cannot open most of them). Anything else is opened directly and rotated by its EXIF tag."""
    import io

    from PIL import Image, ImageOps

    from ...formats import RAW_SUFFIXES, suffix_for_path

    if suffix_for_path(path) in RAW_SUFFIXES:
        from ...raw_embedded_jpeg import extract_embedded_jpeg

        embedded = extract_embedded_jpeg(path)
        if embedded is None:
            raise OSError(f"no embedded preview in {path}")
        with Image.open(io.BytesIO(embedded.payload)) as handle:
            handle.draft("RGB", (long_edge, long_edge))  # reduced-size JPEG decode (1/4 scale for a 7000 px preview): never decode more than needed
            image = _transpose(handle.convert("RGB"), embedded.orientation)
    else:
        with Image.open(path) as handle:
            image = ImageOps.exif_transpose(handle).convert("RGB")
    biggest = max(image.size)
    if biggest > long_edge:
        scale = long_edge / biggest
        image = image.resize((max(1, round(image.width * scale)), max(1, round(image.height * scale))), Image.Resampling.LANCZOS)
    return image


def _transpose(image, orientation: int):
    from PIL import Image

    method = {
        2: Image.Transpose.FLIP_LEFT_RIGHT,
        3: Image.Transpose.ROTATE_180,
        4: Image.Transpose.FLIP_TOP_BOTTOM,
        5: Image.Transpose.TRANSPOSE,
        6: Image.Transpose.ROTATE_270,
        7: Image.Transpose.TRANSVERSE,
        8: Image.Transpose.ROTATE_90,
    }.get(int(orientation or 1))
    return image.transpose(method) if method is not None else image


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


# -- Blink (MediaPipe Face Landmarker, CPU) ------------------------------------------------------------------------------


BLINK_LONG_EDGE = 1280
BLINK_MAX_FACES = 8


class BlinkEngine:
    """Scores a frame by how closed the eyes are on its most closed prominent face (0 open .. 1 closed); no face scores 0. CPU only, no torch."""

    def __init__(self, model_dir: Path, device: str) -> None:
        import numpy as np
        import mediapipe as mp
        from mediapipe.tasks import python as mp_python
        from mediapipe.tasks.python import vision

        from .bundles import BLINK_MODEL_FILE

        model = model_dir / BLINK_MODEL_FILE
        if not model.is_file():
            raise WorkerFatal("weights_missing", f"{BLINK_MODEL_FILE} not found in {model_dir}")
        self.np, self.mp = np, mp
        self.device = "cpu"
        self.half = False
        options = vision.FaceLandmarkerOptions(
            base_options=mp_python.BaseOptions(model_asset_path=str(model)),
            running_mode=vision.RunningMode.IMAGE,
            num_faces=BLINK_MAX_FACES,
            output_face_blendshapes=True,
        )
        self.landmarker = vision.FaceLandmarker.create_from_options(options)

    def close(self) -> None:
        """Release the landmarker explicitly; leaving it to interpreter shutdown prints a harmless but noisy MediaPipe traceback."""
        landmarker, self.landmarker = getattr(self, "landmarker", None), None
        if landmarker is not None:
            landmarker.close()

    def score(self, path: str) -> float:
        from ..blink import face_area, frame_blink_score

        image = load_image(path, BLINK_LONG_EDGE)
        pixels = self.np.ascontiguousarray(self.np.asarray(image))
        result = self.landmarker.detect(self.mp.Image(image_format=self.mp.ImageFormat.SRGB, data=pixels))
        faces = []
        for index, landmarks in enumerate(result.face_landmarks or []):
            shapes = {c.category_name: float(c.score) for c in (result.face_blendshapes[index] if result.face_blendshapes else [])}
            faces.append((face_area((p.x, p.y) for p in landmarks), shapes))
        return frame_blink_score(faces)


def _overlap(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    """Intersection over the smaller box, so a face found again at another size still counts as the same one."""
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    smaller = min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1]))
    return (ix * iy) / smaller if smaller > 0 else 0.0


class FacesEngine:
    """Finds the prominent faces in a frame (landmarker) and embeds each (AdaFace). ``score`` is the face count; ``detail`` is what the People index stores.

    ``model_dir`` holds the landmarker, ``aux_dir`` the recognizer. Boxes are fractions of the frame, so the caller can place them on any copy of the image."""

    def __init__(self, model_dir: Path, device: str, aux_dir: Path | None = None) -> None:
        import numpy as np
        import mediapipe as mp
        import onnxruntime
        from mediapipe.tasks import python as mp_python
        from mediapipe.tasks.python import vision

        from .bundles import BLINK_MODEL_FILE, FACES_IDENTITY_FILE

        model = model_dir / BLINK_MODEL_FILE
        if not model.is_file():
            raise WorkerFatal("weights_missing", f"{BLINK_MODEL_FILE} not found in {model_dir}")
        recognizer = (aux_dir or model_dir) / FACES_IDENTITY_FILE
        if not recognizer.is_file():
            raise WorkerFatal("weights_missing", f"{FACES_IDENTITY_FILE} not found in {recognizer.parent}")
        self.np, self.mp = np, mp
        self.device = "cpu"
        self.half = False
        options = vision.FaceLandmarkerOptions(
            base_options=mp_python.BaseOptions(model_asset_path=str(model)),
            running_mode=vision.RunningMode.IMAGE,
            num_faces=FACES_MAX,
            output_face_blendshapes=True,
        )
        self.landmarker = vision.FaceLandmarker.create_from_options(options)
        self.recognizer = onnxruntime.InferenceSession(str(recognizer), providers=["CPUExecutionProvider"])
        self.input_name = self.recognizer.get_inputs()[0].name

    def close(self) -> None:
        landmarker, self.landmarker = getattr(self, "landmarker", None), None
        if landmarker is not None:
            landmarker.close()

    def detail(self, path: str) -> dict:
        from ..blink import eyes_closed, face_area
        from ..faces import PEOPLE_MIN_FACE_AREA, align_crop, five_points, landmark_box, preprocess

        image = load_image(path, FACES_LONG_EDGE)
        width, height = image.size
        # The landmarker's own face detector looks at the whole frame shrunk to a small square, so a face under a few percent of the frame width is
        # invisible to it (a couple photographed from a few metres off). The frame is therefore searched whole, then in grids of overlapping parts of
        # three sizes, each shrunk less than the whole. Only frames the person filter flagged get here, so the extra looks are for photos of people.
        windows = [(0, 0, width, height)]
        if min(width, height) >= 480:
            for size, count in ((0.6, 3), (0.4, 4), (0.27, 5)):
                part_w, part_h = int(width * size), int(height * size)
                for row in range(count):
                    for col in range(count):
                        left = int(col * (width - part_w) / (count - 1))
                        top = int(row * (height - part_h) / (count - 1))
                        windows.append((left, top, left + part_w, top + part_h))
        found = []
        for left, top, right, bottom in windows:
            part = image if (right - left, bottom - top) == (width, height) else image.crop((left, top, right, bottom))
            part_width, part_height = part.size
            pixels = self.np.ascontiguousarray(self.np.asarray(part))
            result = self.landmarker.detect(self.mp.Image(image_format=self.mp.ImageFormat.SRGB, data=pixels))
            for index, landmarks in enumerate(result.face_landmarks or []):
                points = [(p.x, p.y) for p in landmarks]
                area = face_area(points) * (part_width * part_height) / (width * height)    # as a share of the whole frame
                if area < PEOPLE_MIN_FACE_AREA:  # a smaller face is a bystander
                    continue
                shapes = {c.category_name: float(c.score) for c in (result.face_blendshapes[index] if result.face_blendshapes else [])}
                try:
                    crop = align_crop(part, five_points(points, part_width, part_height))
                except ValueError:  # degenerate landmarks: no usable face here
                    continue
                x0, y0, x1, y1 = landmark_box(points)
                box = ((left + x0 * part_width) / width, (top + y0 * part_height) / height, (left + x1 * part_width) / width, (top + y1 * part_height) / height)
                found.append((area, box, crop, shapes))
        # the same face turns up in the whole frame and in two or more parts: keep the largest view of each
        found.sort(key=lambda item: -item[0])
        kept = []
        for area, box, crop, shapes in found:
            if any(_overlap(box, other[1]) > 0.35 for other in kept):
                continue
            kept.append((area, box, crop, shapes))
        faces = []
        for area, box, crop, shapes in kept[:FACES_MAX]:
            vector = self.recognizer.run(None, {self.input_name: preprocess(crop)})[0][0]
            norm = float(self.np.linalg.norm(vector))
            vector = vector / norm if norm > 0 else vector
            closed = eyes_closed(shapes)
            faces.append({
                "box": [round(v, 5) for v in box],
                "area": round(area, 5),
                "embedding": [round(float(v), 6) for v in vector],
                "eyes_closed": None if closed is None else round(closed, 4),
            })
        return {"faces": faces, "width": width, "height": height}

    def score(self, path: str) -> float:
        return float(len(self.detail(path)["faces"]))


ENGINES: dict[str, Callable[..., object]] = {"maniqa": ManiqaEngine, "qrealign": QRealignEngine, "blink": BlinkEngine, "faces": FacesEngine}


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
                if hasattr(engine, "detail"):  # an engine with more to say than a number (the face finder)
                    detail = engine.detail(item["path"])
                    emit({"event": "score", "id": request_id, "key": key, "value": float(len(detail.get("faces", ()))), "detail": detail})
                else:
                    emit({"event": "score", "id": request_id, "key": key, "value": engine.score(item["path"])})
            except (OSError, ValueError) as exc:  # unreadable or corrupt image: this frame only
                emit({"event": "frame_error", "id": request_id, "key": key, "message": f"{type(exc).__name__}: {exc}"})
        emit({"event": "done", "id": request_id})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--kind", required=True, choices=sorted(ENGINES))
    parser.add_argument("--model-dir", required=True)
    parser.add_argument("--device", default="auto")
    parser.add_argument("--aux-dir", default="", help="a second model directory, for engines that need two bundles (the face finder)")
    args = parser.parse_args(argv)

    out = sys.stdout
    sys.stdout = sys.stderr  # stray prints from libraries must not corrupt the protocol stream

    def emit(message: dict) -> None:
        out.write(protocol.encode_event(message) + "\n")
        out.flush()

    started = time.perf_counter()
    try:
        engine = ENGINES[args.kind](Path(args.model_dir), args.device, Path(args.aux_dir)) if args.aux_dir else ENGINES[args.kind](Path(args.model_dir), args.device)
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
    finally:
        close = getattr(engine, "close", None)
        if close is not None:
            close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
