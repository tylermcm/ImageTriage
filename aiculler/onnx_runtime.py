from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

import numpy as np


@dataclass(frozen=True)
class OnnxSessionSelection:
    session: Any
    model_path: Path
    fallback_used: bool


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
    allow_cpu_fallback: bool = True,
) -> Any:
    selected = list(providers) if providers else preferred_onnx_providers(ort)
    try:
        return ort.InferenceSession(str(model_path), options, providers=selected)
    except Exception:
        if not allow_cpu_fallback or "CUDAExecutionProvider" not in selected:
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
    feed_builder: Callable[[Any, str, np.ndarray], dict[str, np.ndarray]] | None = None,
    output_names: Sequence[str] | None = None,
    allow_cpu_fallback: bool = True,
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
    sample = np.zeros(shape, dtype=dtype)
    feeds = feed_builder(session, input_name, sample) if feed_builder is not None else {input_name: sample}
    try:
        session.run(None if output_names is None else list(output_names), feeds)
        return session
    except Exception:
        if not allow_cpu_fallback:
            raise
        return ort.InferenceSession(
            str(model_path),
            options,
            providers=["CPUExecutionProvider"],
        )


def create_preflight_session_with_model_fallback(
    ort: Any,
    primary_model_path: str | Path,
    fallback_model_path: str | Path | None,
    options: Any,
    *,
    providers: Sequence[str] | None,
    select_input_name: Callable[[Any], str],
    input_shape: Callable[[Any], Sequence[int]],
    feed_builder: Callable[[Any, str, np.ndarray], dict[str, np.ndarray]] | None = None,
    select_output_names: Callable[[Any], Sequence[str] | None] | None = None,
) -> OnnxSessionSelection:
    """Try the primary precision before the fallback on each viable provider."""
    selected = list(providers) if providers else preferred_onnx_providers(ort)
    candidates = [Path(primary_model_path)]
    if fallback_model_path is not None:
        fallback = Path(fallback_model_path)
        if fallback != candidates[0]:
            candidates.append(fallback)

    provider_attempts = [selected]
    if "CUDAExecutionProvider" in selected:
        provider_attempts.append(["CPUExecutionProvider"])

    failures: list[str] = []
    for attempt_providers in provider_attempts:
        require_cuda = "CUDAExecutionProvider" in attempt_providers
        for candidate_index, candidate in enumerate(candidates):
            try:
                session = create_onnx_session(
                    ort,
                    candidate,
                    options,
                    providers=attempt_providers,
                    allow_cpu_fallback=False,
                )
                if require_cuda and "CUDAExecutionProvider" not in session.get_providers():
                    raise RuntimeError("CUDA provider was requested but did not initialize")
                name = select_input_name(session)
                session = preflight_onnx_session(
                    ort,
                    session,
                    candidate,
                    options,
                    input_name=name,
                    input_shape=input_shape(session),
                    feed_builder=feed_builder,
                    output_names=(select_output_names(session) if select_output_names is not None else None),
                    allow_cpu_fallback=False,
                )
                return OnnxSessionSelection(
                    session=session,
                    model_path=candidate.expanduser().resolve(),
                    fallback_used=candidate_index > 0,
                )
            except Exception as exc:
                failures.append(f"{candidate.name} on {','.join(attempt_providers)}: {exc}")

    detail = "\n".join(failures)
    raise RuntimeError(f"Could not initialize the primary or fallback ONNX model.\n{detail}")
