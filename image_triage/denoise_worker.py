from __future__ import annotations

"""AI Denoise (RawNIND Bayer, ONNX) - library for the unified mask engine host.

Imported by ``mask_engine_worker`` and run under the managed AI runtime Python, never inside the Qt GUI process. The input is the
preview worker's ``denoiseInput`` file: the crop's CFA samples as little-endian float32, normalised 0 (black) .. 1 (white); the
output is the denoised samples of the red-aligned part of it as little-endian float16, with their region. The arithmetic around
the model (packing, tiling, unpacking) is ``raw_denoise``.

Model: ``rawnind_bayer.onnx``, input ``input`` float (1, 4, h, w) of white-balanced R, G1, G2, B planes (h, w multiples of 16);
output ``output`` float (1, 3, 2h, 2w), camera RGB at an arbitrary gain. Weights by B. Brummer and C. De Vleeschouwer (RawNIND),
used under CC-BY-4.0.
"""

import json
import time
from pathlib import Path

import raw_denoise

MODEL_FILE = "rawnind_bayer.onnx"
RESULT_PREFIX = "RESULT "


def _providers(torch, requested: str, available) -> tuple[list[str], str]:
    wants_gpu = (requested or "auto").strip().lower() != "cpu"
    if wants_gpu and "CUDAExecutionProvider" in available and torch is not None and torch.cuda.is_available():
        return ["CUDAExecutionProvider", "CPUExecutionProvider"], "cuda"
    return ["CPUExecutionProvider"], "cpu"


class _DenoiseEngine:
    def __init__(self, requested_device: str) -> None:
        self.requested_device = requested_device
        self.device = "unknown"
        self.model_dir: Path | None = None
        self.session = None
        self.np = None
        self._ort = None
        self._torch = None

    def warm_imports(self) -> str:
        if self._ort is not None:
            return self.device
        import numpy as np

        try:
            import torch  # onnxruntime's CUDA provider finds its libraries only once torch has loaded them
        except Exception:
            torch = None
        import onnxruntime as ort

        self.np, self._ort, self._torch = np, ort, torch
        self.device = _providers(torch, self.requested_device, ort.get_available_providers())[1]
        print(f"DEVICE {self.device}", flush=True)
        return self.device

    def load_model(self, model_dir: Path) -> str:
        resolved = Path(model_dir).resolve()
        self.warm_imports()
        if self.session is not None and self.model_dir == resolved:
            return self.device
        print("PROGRESS Loading the denoise model...", flush=True)
        providers, self.device = _providers(self._torch, self.requested_device, self._ort.get_available_providers())
        options = self._ort.SessionOptions()
        options.log_severity_level = 3
        self.session = self._ort.InferenceSession(str(resolved / MODEL_FILE), options, providers=providers)
        self.model_dir = resolved
        return self.device

    def infer(self, packed):
        return self.session.run(None, {"input": packed.astype(self.np.float32)})[0]


def generate_denoise(
    *,
    model_dir: Path,
    input_path: Path,
    output_path: Path,
    region,
    pattern: str,
    multipliers,
    requested_device: str,
    engine: _DenoiseEngine | None = None,
    emit_result: bool = True,
) -> dict[str, object]:
    total_started = time.perf_counter()
    active = engine or _DenoiseEngine(requested_device)
    device = active.load_model(model_dir)
    load_seconds = time.perf_counter() - total_started
    np = active.np
    x, y, w, h = (int(v) for v in region)
    plane = np.fromfile(input_path, dtype="<f4")
    if plane.size != w * h:
        raise ValueError("The sensor samples do not match their region")
    plane = plane.reshape(h, w)

    last = [0.0]

    def progress(done, total):
        now = time.perf_counter()
        if now - last[0] > 0.5 or done == total:
            last[0] = now
            print(f"PROGRESS Denoising... {round(100 * done / total)}%", flush=True)

    started = time.perf_counter()
    out_region, samples = raw_denoise.denoise_plane(plane, (x, y, w, h), pattern, [float(v) for v in multipliers], active.infer,
                                                    progress=progress)
    inference = time.perf_counter() - started
    output_path.parent.mkdir(parents=True, exist_ok=True)
    np.ascontiguousarray(samples, dtype="<f2").tofile(output_path)
    result: dict[str, object] = {
        "device": device,
        "region": list(out_region),
        "outputPath": str(output_path),
        "modelLoadSeconds": round(load_seconds, 3),
        "inferenceSeconds": round(inference, 3),
    }
    if emit_result:
        print(RESULT_PREFIX + json.dumps(result), flush=True)
    return result
