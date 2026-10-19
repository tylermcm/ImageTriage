"""Standalone AI upscale worker (ONNX Runtime only). Run as ``python upscale_worker.py --job job.json``.

Runs under the managed AI runtime, where the application package is not importable, so
``upscale_core`` is imported as a sibling module. Protocol on stdout, one line each:

    PROGRESS {"stage": ..., "done": n, "total": m}
    AI_METRIC {...}                  (only when IMAGE_TRIAGE_AI_METRICS is on)
    RESULT {...}                     exactly once on success
    ERROR {"category": ..., "message": ..., "remediation": ...}   exit code 1
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Callable

try:  # frozen / managed runtime: sibling import
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import upscale_core as core
except ImportError:  # pragma: no cover - package import for tests
    from image_triage import upscale_core as core

UPSCALE_PROTOCOL_VERSION = 1
PROGRESS_PREFIX = "PROGRESS "
RESULT_PREFIX = "RESULT "
ERROR_PREFIX = "ERROR "
METRIC_PREFIX = "AI_METRIC "
SELF_CHECK_TILE = 96

Emit = Callable[[str], None]


def _say(emit: Emit, prefix: str, payload: dict[str, Any]) -> None:
    emit(prefix + json.dumps(payload, sort_keys=True, default=str))


class _Session:
    """A loaded model on one provider."""

    def __init__(self, run: Callable[[Any], Any], provider: str, warnings: tuple[str, ...] = ()) -> None:
        self.run = run
        self.provider = provider
        self.warnings = warnings


def _make_ort_session(onnx_path: Path, provider: str):
    import onnxruntime as ort

    options = ort.SessionOptions()
    options.log_severity_level = 3
    session = ort.InferenceSession(str(onnx_path), sess_options=options, providers=[provider])
    input_name = session.get_inputs()[0].name
    # onnxruntime does not raise when a provider cannot load (a missing CUDA DLL, say): it warns and runs on the CPU.
    # Report the provider the session really has, or the self-check below would compare the CPU with itself.
    active = (session.get_providers() or ["CPUExecutionProvider"])[0]

    def run(tensor):
        return session.run(None, {input_name: tensor})[0]

    return run, active


def _preload_gpu_libraries(ort, emit: Emit) -> None:
    """The managed GPU profile carries the CUDA and cuDNN DLLs inside ``torch/lib`` and nothing imports torch in this worker, so
    onnxruntime cannot find them and quietly runs on the CPU (about 17x slower for this model). ``preload_dlls`` locates them."""
    preload = getattr(ort, "preload_dlls", None)
    if preload is None:
        return
    try:
        preload()
    except Exception as exc:  # no GPU libraries at all: the CPU path still works
        _say(emit, METRIC_PREFIX, {"event": "preload_dlls_failed", "warning": str(exc)})


def default_session_factory(onnx_path: Path, device: str, emit: Emit) -> _Session:
    """Try providers in order; a non-CPU provider must reproduce the CPU result on a test tile."""
    import onnxruntime as ort

    if str(device).strip().lower() != "cpu":
        _preload_gpu_libraries(ort, emit)

    providers = core.select_providers(ort.get_available_providers(), device)
    cpu_run, _ = _make_ort_session(onnx_path, "CPUExecutionProvider")
    probe = core.self_check_input(SELF_CHECK_TILE)
    reference = None
    skipped: list[str] = []
    for provider in providers:
        if provider == "CPUExecutionProvider":
            return _Session(cpu_run, provider, tuple(skipped))
        try:
            run, active = _make_ort_session(onnx_path, provider)
            if active != provider:
                warning = f"{provider} could not be loaded (onnxruntime fell back to {active}), so it was skipped."
            else:
                if reference is None:
                    reference = cpu_run(probe)
                if core.providers_agree(run(probe), reference):
                    return _Session(run, provider, tuple(skipped))
                warning = f"{provider} disagreed with the CPU result and was skipped."
        except Exception as exc:  # provider missing a DLL, out of memory, bad kernel...
            warning = f"{provider} failed its self-check ({exc}) and was skipped."
        skipped.append(warning)
        _say(emit, METRIC_PREFIX, {"event": "provider_skipped", "warning": warning})
    return _Session(cpu_run, "CPUExecutionProvider", tuple(skipped))


def _load_source(path: Path, apply_orientation: bool):
    from PIL import Image, ImageOps

    try:
        with Image.open(path) as opened:
            opened.load()
            icc = opened.info.get("icc_profile")
            exif = opened.info.get("exif")
            image = ImageOps.exif_transpose(opened) if apply_orientation else opened.copy()
    except Exception as exc:
        raise core.UpscaleError("bad_image", f"Cannot read the image: {exc}") from exc
    import numpy as np

    has_alpha = "A" in image.getbands() or "transparency" in image.info
    if has_alpha:
        rgba = image.convert("RGBA")
        alpha = np.asarray(rgba.getchannel("A"), dtype=np.uint8)
        rgb = np.asarray(rgba.convert("RGB"), dtype=np.uint8)
    else:
        alpha = None
        rgb = np.asarray(image.convert("RGB"), dtype=np.uint8)
    return rgb, alpha, icc, exif


def run_job(
    job: dict[str, Any],
    session_factory: Callable[[Path, str, Emit], _Session] | None = None,
    emit: Emit = lambda line: print(line, flush=True),
) -> dict[str, Any]:
    started = time.perf_counter()
    if int(job.get("protocolVersion", -1)) != UPSCALE_PROTOCOL_VERSION:
        raise core.UpscaleError(
            "protocol_mismatch",
            f"Upscale worker protocol {job.get('protocolVersion')!r} is not {UPSCALE_PROTOCOL_VERSION}.",
            "Restart Image Triage.",
        )
    spec = core.get_spec(str(job.get("modelKey", "")))
    model_dir = Path(job["modelDir"])
    core.verify_model_file(model_dir, spec, deep=True)  # every job: weights are pinned by hash
    source = Path(job["inputPath"])
    output = Path(job["outputPath"])
    overwrite = bool(job.get("overwrite", False))
    if output.exists() and not overwrite:
        raise core.UpscaleError("exists", f"Output already exists: {output}")

    t = time.perf_counter()
    rgb, alpha, icc, exif = _load_source(source, bool(job.get("applyOrientation", True)))
    load_s = time.perf_counter() - t
    core_px = int(job.get("core", core.DEFAULT_CORE))
    pad = int(job.get("pad", core.DEFAULT_PAD))
    plan = core.plan_upscale(
        rgb.shape[1], rgb.shape[0], spec.key, output.suffix, core=core_px, pad=pad, has_alpha=alpha is not None
    )

    t = time.perf_counter()
    factory = session_factory or default_session_factory
    session = factory(model_dir / spec.filename, str(job.get("device", "auto")), emit)
    session_s = time.perf_counter() - t

    last = {"t": 0.0}

    def progress(done: int, total: int) -> None:
        now = time.perf_counter()
        if done == total or now - last["t"] >= 0.25:
            last["t"] = now
            _say(emit, PROGRESS_PREFIX, {"stage": "upscale", "done": done, "total": total})

    t = time.perf_counter()
    result = core.upscale_array(rgb, session.run, spec.scale, core=plan.core, pad=plan.pad, progress=progress)
    infer_s = time.perf_counter() - t

    _say(emit, PROGRESS_PREFIX, {"stage": "save", "done": 0, "total": 1})
    t = time.perf_counter()
    core.save_output(
        result,
        output,
        spec,
        session.provider,
        alpha=alpha,
        icc_profile=icc,
        exif=exif,
        jpeg_quality=int(job.get("jpegQuality", 95)),
        overwrite=overwrite,
    )
    save_s = time.perf_counter() - t
    _say(emit, PROGRESS_PREFIX, {"stage": "save", "done": 1, "total": 1})
    return {
        "outputPath": str(output),
        "modelKey": spec.key,
        "scale": spec.scale,
        "provider": session.provider,
        "inputSize": [plan.in_width, plan.in_height],
        "outputSize": [plan.out_width, plan.out_height],
        "tiles": plan.tile_count,
        "warnings": [*plan.warnings, *session.warnings],
        "timings": {
            "load": round(load_s, 3),
            "session": round(session_s, 3),
            "inference": round(infer_s, 3),
            "save": round(save_s, 3),
            "total": round(time.perf_counter() - started, 3),
        },
    }


def _error_payload(exc: BaseException) -> dict[str, str]:
    if isinstance(exc, core.UpscaleError):
        return {"category": exc.category, "message": str(exc), "remediation": exc.remediation}
    text = str(exc)
    if isinstance(exc, MemoryError) or "out of memory" in text.lower():
        return {"category": "out_of_memory", "message": text or "Out of memory.", "remediation": "Close other applications or use a smaller image."}
    return {"category": "worker_failed", "message": f"{type(exc).__name__}: {text}", "remediation": ""}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--job", required=True, help="Path to the job JSON file.")
    args = parser.parse_args(argv)
    emit: Emit = lambda line: print(line, flush=True)
    try:
        job = json.loads(Path(args.job).read_text(encoding="utf-8"))
        payload = run_job(job, emit=emit)
    except BaseException as exc:  # the parent only understands one ERROR line and a non-zero exit
        if isinstance(exc, KeyboardInterrupt):
            raise
        _say(emit, ERROR_PREFIX, _error_payload(exc))
        return 1
    _say(emit, RESULT_PREFIX, payload)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
