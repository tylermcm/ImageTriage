from __future__ import annotations

"""Persistent OneFormer semantic-mask worker service.

Owns a single staged ``oneformer_worker`` subprocess for the whole application
and serializes semantic-mask requests through it, exactly as
``BiRefNetWorkerService`` does for subject masks. Keeping PyTorch and
Transformers out of the Qt process is the whole point: inference happens in the
managed AI runtime interpreter, and this class is only transport + lifecycle.
"""

import atexit
import json
import os
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QRunnable

from .ai_model import resolve_segmentation_model_installation
from .ai_runtime_packages import resolve_ai_runtime_site_packages
from .ai_workflow import (
    AI_METRICS_ENV_VAR,
    AI_METRIC_PREFIX,
    AIWorkflowRuntime,
    default_ai_workflow_runtime,
    resolve_ai_python_script_command,
)
from .perf import perf_logger


ProgressCallback = Callable[[str], None]


@dataclass(frozen=True)
class SemanticWorkerResult:
    device: str
    source_size: tuple[int, int]
    category_stats: dict[str, dict[str, object]]
    timings_ms: dict[str, float]


def _elapsed_ms(started: float) -> float:
    return (time.perf_counter() - started) * 1000.0


def _progress(callback: ProgressCallback | None, message: str) -> None:
    if callback is not None:
        callback(message)


def _record_worker_metric(payload: dict[str, object]) -> None:
    logger = perf_logger()
    if not logger.enabled:
        return
    fields = dict(payload)
    event = str(fields.pop("event", "ai.mask.oneformer.worker.metric"))
    duration = fields.pop("duration_ms", None)
    fields["source"] = "worker"
    if isinstance(duration, (int, float)):
        logger.duration(event, float(duration), **fields)
    else:
        logger.log(event, **fields)


def _parse_worker_metric(line: str) -> dict[str, object] | None:
    if not line.startswith(AI_METRIC_PREFIX):
        return None
    try:
        payload = json.loads(line.removeprefix(AI_METRIC_PREFIX))
    except (TypeError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


class _WorkerTransportError(RuntimeError):
    pass


class OneFormerWorkerService:
    """Own one staged OneFormer worker and serialize all requests through it."""

    def __init__(self, *, idle_timeout_seconds: float = 600.0) -> None:
        self.idle_timeout_seconds = max(1.0, float(idle_timeout_seconds))
        self._lock = threading.RLock()
        self._process: subprocess.Popen[str] | None = None
        self._runtime: AIWorkflowRuntime | None = None
        self._site_packages: tuple[Path, ...] = ()
        self._imports_ready = False
        self._loaded_model_dir: Path | None = None
        self._device = "unknown"
        self._request_id = 0
        self._idle_timer: threading.Timer | None = None

    @property
    def is_running(self) -> bool:
        with self._lock:
            return self._process is not None and self._process.poll() is None

    @property
    def imports_ready(self) -> bool:
        with self._lock:
            return self.is_running and self._imports_ready

    @property
    def model_ready(self) -> bool:
        with self._lock:
            return self.is_running and self._loaded_model_dir is not None

    def warm_imports(self, progress_callback: ProgressCallback | None = None) -> str:
        with self._lock:
            self._cancel_idle_locked()
            self._ensure_process_locked()
            if self._imports_ready:
                self._log_reuse("imports")
            else:
                result = self._command_locked(
                    {"command": "warm-imports"},
                    progress_callback=progress_callback,
                )
                self._device = str(result.get("device") or self._device)
                self._imports_ready = True
            self._schedule_idle_locked()
            return self._device

    def warm_model(
        self,
        model_dir: str | Path,
        progress_callback: ProgressCallback | None = None,
    ) -> str:
        resolved_model_dir = Path(model_dir).resolve()
        with self._lock:
            self._cancel_idle_locked()
            self._ensure_process_locked()
            if not self._imports_ready:
                result = self._command_locked(
                    {"command": "warm-imports"},
                    progress_callback=progress_callback,
                )
                self._device = str(result.get("device") or self._device)
                self._imports_ready = True
            if self._loaded_model_dir == resolved_model_dir:
                self._log_reuse("model")
            else:
                result = self._command_locked(
                    {"command": "load-model", "modelDir": str(resolved_model_dir)},
                    progress_callback=progress_callback,
                )
                self._device = str(result.get("device") or self._device)
                self._loaded_model_dir = resolved_model_dir
            self._schedule_idle_locked()
            return self._device

    def infer(
        self,
        *,
        model_dir: Path,
        input_path: Path,
        output_dir: Path,
        categories: tuple[str, ...],
        minimum_coverage: float,
        progress_callback: ProgressCallback | None,
    ) -> SemanticWorkerResult:
        resolved_model_dir = model_dir.resolve()
        with self._lock:
            self._cancel_idle_locked()
            self.warm_model(resolved_model_dir, progress_callback)
            result = self._command_locked(
                {
                    "command": "infer",
                    "modelDir": str(resolved_model_dir),
                    "inputPath": str(input_path.resolve()),
                    "outputDir": str(output_dir.resolve()),
                    "categories": list(categories),
                    "minimumCoverage": float(minimum_coverage),
                },
                progress_callback=progress_callback,
            )
            self._device = str(result.get("device") or self._device)
            self._schedule_idle_locked()
            raw_size = result.get("sourceSize")
            if isinstance(raw_size, list) and len(raw_size) == 2:
                source_size = (int(raw_size[0]), int(raw_size[1]))
            else:
                source_size = (0, 0)
            raw_stats = result.get("categoryStats")
            category_stats = raw_stats if isinstance(raw_stats, dict) else {}
            raw_timings = result.get("timingsMs")
            timings = {
                str(key): float(value)
                for key, value in (raw_timings or {}).items()
                if isinstance(value, (int, float))
            } if isinstance(raw_timings, dict) else {}
            return SemanticWorkerResult(
                device=self._device,
                source_size=source_size,
                category_stats=category_stats,
                timings_ms=timings,
            )

    def shutdown(self) -> None:
        with self._lock:
            self._cancel_idle_locked()
            process = self._process
            if process is None:
                return
            started = time.perf_counter()
            try:
                if process.poll() is None:
                    try:
                        self._command_locked({"command": "shutdown"})
                    except Exception:
                        process.terminate()
                    try:
                        process.wait(timeout=2.0)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=2.0)
            finally:
                self._clear_process_locked()
                perf_logger().duration(
                    "ai.mask.oneformer.service.shutdown",
                    _elapsed_ms(started),
                )

    def _ensure_process_locked(self) -> None:
        if self._process is not None and self._process.poll() is None:
            if self._runtime is None:
                return
            current_runtime = default_ai_workflow_runtime()
            if self._runtime.device == current_runtime.device:
                return
            self.shutdown()
        self._clear_process_locked()
        logger = perf_logger()
        phase_started = time.perf_counter()
        runtime, site_packages = _resolve_semantic_runtime()
        logger.duration(
            "ai.mask.oneformer.service.runtime_resolve",
            _elapsed_ms(phase_started),
            requested_device=runtime.device,
            site_packages=len(site_packages),
        )
        worker_path = _semantic_worker_path(runtime)
        command = resolve_ai_python_script_command(worker_path, runtime=runtime)
        command.extend(("--server", "--device", runtime.device))
        env = _semantic_worker_environment(site_packages, logger.enabled)
        creationflags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0) or 0)
        phase_started = time.perf_counter()
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            env=env,
            creationflags=creationflags,
        )
        if process.stdin is None or process.stdout is None:
            process.kill()
            raise RuntimeError("OneFormer worker did not expose its command pipes.")
        self._process = process
        self._runtime = runtime
        self._site_packages = site_packages
        self._imports_ready = False
        self._loaded_model_dir = None
        self._device = "unknown"
        logger.duration(
            "ai.mask.oneformer.service.worker_spawn",
            _elapsed_ms(phase_started),
            requested_device=runtime.device,
            worker_pid=process.pid,
        )

    def _command_locked(
        self,
        payload: dict[str, object],
        *,
        progress_callback: ProgressCallback | None = None,
    ) -> dict[str, object]:
        try:
            return self._send_command_locked(payload, progress_callback=progress_callback)
        except _WorkerTransportError:
            # A staged worker can die between requests (idle reaper races, OS
            # pressure). Respawn once and retry before surfacing the failure.
            self._clear_process_locked()
            self._ensure_process_locked()
            self._imports_ready = False
            self._loaded_model_dir = None
            if str(payload.get("command")) == "infer":
                # Re-establish imports/model that the dead worker had loaded.
                model_dir = payload.get("modelDir")
                if model_dir:
                    self._send_command_locked(
                        {"command": "load-model", "modelDir": str(model_dir)},
                        progress_callback=progress_callback,
                    )
                    self._imports_ready = True
                    self._loaded_model_dir = Path(str(model_dir)).resolve()
            return self._send_command_locked(payload, progress_callback=progress_callback)

    def _send_command_locked(
        self,
        payload: dict[str, object],
        *,
        progress_callback: ProgressCallback | None = None,
    ) -> dict[str, object]:
        process = self._process
        if process is None or process.poll() is not None:
            raise _WorkerTransportError("OneFormer worker is not running.")
        assert process.stdin is not None and process.stdout is not None
        self._request_id += 1
        request_id = self._request_id
        request = {"id": request_id, **payload}
        command_name = str(payload.get("command") or "unknown")
        started = time.perf_counter()
        try:
            process.stdin.write(json.dumps(request, default=str) + "\n")
            process.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            self._clear_process_locked()
            raise _WorkerTransportError("OneFormer worker command pipe closed.") from exc

        recent_lines: list[str] = []
        while True:
            raw_line = process.stdout.readline()
            if raw_line == "":
                return_code = process.poll()
                self._clear_process_locked()
                detail = "\n".join(recent_lines[-20:])
                raise _WorkerTransportError(
                    f"OneFormer worker exited unexpectedly ({return_code})."
                    + (f"\n{detail}" if detail else "")
                )
            line = raw_line.strip()
            if not line:
                continue
            metric = _parse_worker_metric(line)
            if metric is not None:
                _record_worker_metric(metric)
                continue
            recent_lines.append(line)
            if line.startswith("PROGRESS "):
                _progress(progress_callback, line.removeprefix("PROGRESS "))
                continue
            if line.startswith("DEVICE "):
                self._device = line.removeprefix("DEVICE ").strip() or self._device
                continue
            if not line.startswith("RESPONSE "):
                continue
            try:
                response = json.loads(line.removeprefix("RESPONSE "))
            except (TypeError, ValueError) as exc:
                raise _WorkerTransportError("OneFormer worker returned invalid JSON.") from exc
            if not isinstance(response, dict) or response.get("id") != request_id:
                raise _WorkerTransportError("OneFormer worker response was out of sequence.")
            perf_logger().duration(
                "ai.mask.oneformer.service.command",
                _elapsed_ms(started),
                command=command_name,
                worker_pid=process.pid,
                ok=bool(response.get("ok")),
            )
            if not response.get("ok"):
                raise RuntimeError(
                    str(response.get("error") or "OneFormer worker command failed.")
                )
            result = response.get("result")
            return result if isinstance(result, dict) else {}

    def _log_reuse(self, stage: str) -> None:
        process = self._process
        perf_logger().log(
            "ai.mask.oneformer.service.reuse",
            stage=stage,
            worker_pid=process.pid if process is not None else 0,
            device=self._device,
        )

    def _schedule_idle_locked(self) -> None:
        self._cancel_idle_locked()
        timer = threading.Timer(self.idle_timeout_seconds, self.shutdown)
        timer.daemon = True
        self._idle_timer = timer
        timer.start()

    def _cancel_idle_locked(self) -> None:
        timer = self._idle_timer
        self._idle_timer = None
        if timer is not None:
            timer.cancel()

    def _clear_process_locked(self) -> None:
        process = self._process
        self._process = None
        self._runtime = None
        self._site_packages = ()
        self._imports_ready = False
        self._loaded_model_dir = None
        self._device = "unknown"
        if process is None:
            return
        for stream in (process.stdin, process.stdout):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass


_WORKER_SERVICE: OneFormerWorkerService | None = None
_WORKER_SERVICE_LOCK = threading.Lock()


def default_oneformer_worker_service() -> OneFormerWorkerService:
    global _WORKER_SERVICE
    with _WORKER_SERVICE_LOCK:
        if _WORKER_SERVICE is None:
            _WORKER_SERVICE = OneFormerWorkerService()
        return _WORKER_SERVICE


def shutdown_oneformer_worker() -> None:
    service = _WORKER_SERVICE
    if service is not None:
        service.shutdown()


atexit.register(shutdown_oneformer_worker)


def _semantic_worker_path(runtime: AIWorkflowRuntime) -> Path:
    worker_path = Path(__file__).with_name("oneformer_worker.py")
    if not worker_path.is_file() and runtime.python_executable is not None:
        worker_path = runtime.python_executable.parent / "ai_workers" / "oneformer_worker.py"
    if not worker_path.is_file():
        raise FileNotFoundError("OneFormer worker script is missing.")
    return worker_path


def _semantic_worker_environment(
    site_packages: tuple[Path, ...],
    metrics_enabled: bool,
) -> dict[str, str]:
    env = os.environ.copy()
    existing_pythonpath = [part for part in env.get("PYTHONPATH", "").split(os.pathsep) if part]
    env["PYTHONPATH"] = os.pathsep.join(
        [*(str(path) for path in site_packages), *existing_pythonpath]
    )
    env["PYTHONUNBUFFERED"] = "1"
    env["HF_HUB_OFFLINE"] = "1"
    env["TRANSFORMERS_OFFLINE"] = "1"
    env[AI_METRICS_ENV_VAR] = "1" if metrics_enabled else "0"
    return env


def _resolve_semantic_runtime() -> tuple[AIWorkflowRuntime, tuple[Path, ...]]:
    runtime = default_ai_workflow_runtime()
    site_packages = resolve_ai_runtime_site_packages(device=runtime.device)
    if not site_packages:
        raise RuntimeError(
            "The AI runtime is unavailable. Install the PyTorch AI runtime first."
        )
    required_modules = (
        "torch",
        "transformers",
        "safetensors",
        "PIL",
        "numpy",
    )
    missing = [
        name
        for name in required_modules
        if not any((site_dir / name).exists() for site_dir in site_packages)
    ]
    if missing:
        raise RuntimeError(
            "The installed AI runtime is missing OneFormer dependencies: "
            + ", ".join(missing)
        )
    return runtime, site_packages


_RUNTIME_VALIDATED = False


def validate_semantic_runtime() -> None:
    # Resolving the runtime (env + filesystem existence checks) costs ~140 ms
    # and is stable within a session, so validate once. The worker spawn path
    # re-resolves independently, so a genuinely broken runtime still surfaces.
    global _RUNTIME_VALIDATED
    if _RUNTIME_VALIDATED:
        return
    _resolve_semantic_runtime()
    _RUNTIME_VALIDATED = True


def reset_runtime_validation_cache() -> None:
    """Force the next ``validate_semantic_runtime`` to re-check (tests, or after
    the AI runtime is (un)installed / the device changes mid-session)."""
    global _RUNTIME_VALIDATED
    _RUNTIME_VALIDATED = False


class SemanticMaskWarmTask(QRunnable):
    """Warm installed OneFormer resources without downloading or blocking the UI.

    A ``QRunnable`` so callers can schedule it on the shared thread pool exactly
    like ``SubjectMaskWarmTask``.
    """

    def __init__(self, stage: str) -> None:
        super().__init__()
        normalized = stage.strip().casefold()
        if normalized not in {"imports", "model"}:
            raise ValueError(f"Unknown OneFormer warm stage: {stage}")
        self.stage = normalized
        self.setAutoDelete(True)

    def run(self) -> None:
        started = time.perf_counter()
        logger = perf_logger()
        try:
            # No scene-mask model, nothing to warm for — don't spin up torch/CUDA
            # just because the editor opened; masking would prompt a download.
            installation = resolve_segmentation_model_installation()
            if not installation.is_installed:
                logger.duration(
                    "ai.mask.oneformer.warm.skipped",
                    _elapsed_ms(started),
                    stage=self.stage,
                    reason="model_not_installed",
                )
                return
            service = default_oneformer_worker_service()
            if self.stage == "imports":
                device = service.warm_imports()
            else:
                device = service.warm_model(installation.install_dir)
            logger.duration(
                "ai.mask.oneformer.warm",
                _elapsed_ms(started),
                stage=self.stage,
                device=device,
            )
        except Exception as exc:
            logger.duration(
                "ai.mask.oneformer.warm.failed",
                _elapsed_ms(started),
                stage=self.stage,
                error=str(exc),
            )


__all__ = [
    "OneFormerWorkerService",
    "SemanticMaskWarmTask",
    "SemanticWorkerResult",
    "default_oneformer_worker_service",
    "shutdown_oneformer_worker",
    "validate_semantic_runtime",
]
