from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

import numpy as np


def preferred_onnx_providers(ort: Any) -> list[str]:
    """Prefer managed CUDA inference while retaining an automatic CPU fallback."""
    available = set(ort.get_available_providers())
    if "CUDAExecutionProvider" in available:
        preload = getattr(ort, "preload_dlls", None)
        if callable(preload):
            try:
                preload()
            except Exception:
                pass
    preferred = (
        "CUDAExecutionProvider",
        "CoreMLExecutionProvider",
        "CPUExecutionProvider",
    )
    return [provider for provider in preferred if provider in available] or ["CPUExecutionProvider"]


def create_onnx_session(
    ort: Any,
    model_path: str | Path,
    options: Any,
    *,
    providers: Sequence[str] | None = None,
) -> Any:
    selected = list(providers) if providers else preferred_onnx_providers(ort)
    try:
        return ort.InferenceSession(str(model_path), options, providers=selected)
    except Exception:
        if "CUDAExecutionProvider" not in selected:
            raise
        return ort.InferenceSession(
            str(model_path),
            options,
            providers=["CPUExecutionProvider"],
        )


def preflight_onnx_session(
    ort: Any,
    session: Any,
    model_path: str | Path,
    options: Any,
    *,
    input_name: str,
    input_shape: Sequence[int] | None = None,
) -> Any:
    """Keep CUDA only when the exact model can complete a representative run."""
    if "CUDAExecutionProvider" not in session.get_providers():
        return session
    input_meta = next(meta for meta in session.get_inputs() if meta.name == input_name)
    shape = tuple(input_shape) if input_shape is not None else tuple(
        dimension if isinstance(dimension, int) and dimension > 0 else 1
        for dimension in input_meta.shape
    )
    dtype = {
        "tensor(float)": np.float32,
        "tensor(float16)": np.float16,
        "tensor(double)": np.float64,
        "tensor(int64)": np.int64,
        "tensor(int32)": np.int32,
    }.get(str(input_meta.type), np.float32)
    try:
        session.run(None, {input_name: np.zeros(shape, dtype=dtype)})
        return session
    except Exception:
        return ort.InferenceSession(
            str(model_path),
            options,
            providers=["CPUExecutionProvider"],
        )
