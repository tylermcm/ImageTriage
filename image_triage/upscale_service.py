"""GUI-side AI upscale service: spawns ``upscale_worker`` under the managed runtime. No Qt, no torch.

``run_upscale`` blocks, so call it from a worker thread. Cancel by setting the ``threading.Event``.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

from . import upscale_core as core
from .ai_env import AIRuntimeUnavailable, build_worker_env, select_runtime

UPSCALE_PROTOCOL_VERSION = 1
MODEL_DIR_ENV = "IMAGE_TRIAGE_UPSCALE_MODEL_DIR"
MODEL_BUNDLE_DIRNAME = "Real-ESRGAN"


def _worker_script() -> Path:
    """Source tree: next to this module. Frozen app: ``ai_workers/`` beside the executable
    (the module itself lives in library.zip there, so it has no file to point at)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "ai_workers" / "upscale_worker.py"
    return Path(__file__).with_name("upscale_worker.py")


WORKER_SCRIPT = _worker_script()


class UpscaleServiceError(Exception):
    def __init__(self, category: str, message: str, remediation: str = "") -> None:
        super().__init__(message)
        self.category = category
        self.remediation = remediation


@dataclass(frozen=True)
class UpscaleRequest:
    input_path: Path
    model_key: str = core.DEFAULT_MODEL_KEY
    output_path: Path | None = None
    output_suffix: str = ""
    device: str = "auto"
    apply_orientation: bool = True
    keep_metadata: bool = True
    jpeg_quality: int = 95
    overwrite: bool = False
    model_dir: Path | None = None


@dataclass(frozen=True)
class UpscaleProgress:
    stage: str
    done: int
    total: int

    @property
    def fraction(self) -> float:
        return 0.0 if self.total <= 0 else min(1.0, max(0.0, self.done / self.total))


@dataclass(frozen=True)
class UpscaleResult:
    output_path: Path
    model_key: str
    scale: int
    provider: str
    input_size: tuple[int, int]
    output_size: tuple[int, int]
    warnings: tuple[str, ...] = ()
    timings: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class UpscaleAvailability:
    runtime_ok: bool
    runtime_reason: str
    models: dict[str, str]  # key -> "" when usable, else the reason

    @property
    def available(self) -> bool:
        return self.runtime_ok and any(not reason for reason in self.models.values())


def upscale_model_dir() -> Path:
    override = (os.environ.get(MODEL_DIR_ENV) or "").strip()
    if override:
        return Path(override)
    from .ai_paths import managed_model_dir

    return managed_model_dir(MODEL_BUNDLE_DIRNAME)


def check_availability(
    model_dir: Path | None = None,
    *,
    deep: bool = False,
    runtime_resolver: Callable[..., Any] = select_runtime,
) -> UpscaleAvailability:
    try:
        runtime_resolver("auto", require_torch=False)
        runtime_ok, reason = True, ""
    except AIRuntimeUnavailable as exc:
        runtime_ok, reason = False, f"{exc} {exc.remediation}".strip()
    models = core.model_status(model_dir or upscale_model_dir(), deep=deep)
    return UpscaleAvailability(runtime_ok, reason, models)


def _default_spawn(command: list[str], env: dict[str, str]) -> subprocess.Popen:
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return subprocess.Popen(
        command,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=flags,
    )


def _build_command(job_path: Path) -> list[str]:
    from .ai_workflow import resolve_ai_python_script_command

    return resolve_ai_python_script_command(WORKER_SCRIPT, "--job", str(job_path))


def run_upscale(
    request: UpscaleRequest,
    progress_callback: Callable[[UpscaleProgress], None] | None = None,
    cancel_event: threading.Event | None = None,
    *,
    spawn: Callable[[list[str], dict[str, str]], Any] = _default_spawn,
    runtime_resolver: Callable[..., Any] = select_runtime,
    command_builder: Callable[[Path], list[str]] = _build_command,
) -> UpscaleResult:
    spec = _spec_or_raise(request.model_key)
    source = Path(request.input_path)
    if not source.is_file():
        raise UpscaleServiceError("bad_image", f"Input file not found: {source}")
    output = Path(request.output_path) if request.output_path else core.upscale_output_path(source, spec.scale, request.output_suffix)
    model_dir = Path(request.model_dir) if request.model_dir else upscale_model_dir()
    try:
        selection = runtime_resolver(request.device, require_torch=False)
    except AIRuntimeUnavailable as exc:
        raise UpscaleServiceError(exc.category, str(exc), exc.remediation) from exc
    job = {
        "protocolVersion": UPSCALE_PROTOCOL_VERSION,
        "inputPath": str(source),
        "outputPath": str(output),
        "modelKey": spec.key,
        "modelDir": str(model_dir),
        "device": request.device,
        "applyOrientation": request.apply_orientation,
        "keepMetadata": request.keep_metadata,
        "jpegQuality": request.jpeg_quality,
        "overwrite": request.overwrite,
    }
    fd, job_name = tempfile.mkstemp(prefix="upscale_job_", suffix=".json")
    job_path = Path(job_name)
    partial = output.with_name(output.name + ".partial")
    process = None
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(job, handle)
        try:
            command = command_builder(job_path)
        except FileNotFoundError as exc:
            raise UpscaleServiceError("worker_missing", f"Upscale worker unavailable: {exc}", "Reinstall Image Triage or run Set Up AI.") from exc
        env = build_worker_env(selection, protocol_version=UPSCALE_PROTOCOL_VERSION)
        process = spawn(command, env)
        return _stream(process, progress_callback, cancel_event, output, partial)
    finally:
        if process is not None and process.poll() is None:
            _terminate(process)
        for leftover in (job_path, partial):
            try:
                leftover.unlink()
            except OSError:
                pass


def _spec_or_raise(key: str) -> core.UpscaleModelSpec:
    try:
        return core.get_spec(key)
    except core.UpscaleError as exc:
        raise UpscaleServiceError(exc.category, str(exc)) from exc


def _terminate(process: Any) -> None:
    try:
        process.terminate()
        process.wait(timeout=5)
    except Exception:
        try:
            process.kill()
        except Exception:
            pass


def _stream(process: Any, progress_callback, cancel_event, output: Path, partial: Path) -> UpscaleResult:
    tail: deque[str] = deque(maxlen=12)
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None

    finished = threading.Event()

    def watch() -> None:
        while not finished.is_set():
            if cancel_event.wait(0.05):
                if not finished.is_set() and process.poll() is None:
                    _terminate(process)
                return

    if cancel_event is not None:
        threading.Thread(target=watch, daemon=True).start()
    try:
        for raw in _lines(process):
            line = raw.rstrip("\r\n")
            if cancel_event is not None and cancel_event.is_set():
                break
            if line.startswith("PROGRESS "):
                data = _json(line[len("PROGRESS "):])
                if data is not None and progress_callback is not None:
                    progress_callback(UpscaleProgress(str(data.get("stage", "")), int(data.get("done", 0)), int(data.get("total", 0))))
            elif line.startswith("RESULT "):
                result = _json(line[len("RESULT "):])
            elif line.startswith("ERROR "):
                error = _json(line[len("ERROR "):])
            elif line:
                tail.append(line)
        if cancel_event is not None and cancel_event.is_set():
            _terminate(process)
            raise UpscaleServiceError("cancelled", "Upscaling was cancelled.")
        code = process.wait()
    finally:
        finished.set()
    if error is not None:
        raise UpscaleServiceError(str(error.get("category", "worker_failed")), str(error.get("message", "")), str(error.get("remediation", "")))
    if code != 0 or result is None:
        raise UpscaleServiceError("worker_failed", "The upscale worker exited unexpectedly.\n" + "\n".join(tail), "")
    return UpscaleResult(
        output_path=Path(result["outputPath"]),
        model_key=str(result["modelKey"]),
        scale=int(result["scale"]),
        provider=str(result["provider"]),
        input_size=tuple(result["inputSize"]),  # type: ignore[arg-type]
        output_size=tuple(result["outputSize"]),  # type: ignore[arg-type]
        warnings=tuple(result.get("warnings", ())),
        timings=dict(result.get("timings", {})),
    )


def _lines(process: Any) -> Iterable[str]:
    stream = process.stdout
    return iter(stream.readline, "") if hasattr(stream, "readline") else iter(stream)


def _json(text: str) -> dict[str, Any] | None:
    try:
        value = json.loads(text)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None
