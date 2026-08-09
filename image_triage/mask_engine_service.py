from __future__ import annotations

"""GUI-side service for the unified MaskEngine host.

Owns one ``mask_engine_worker`` subprocess for the whole app and routes mask
requests to the right engine (``subject`` -> BiRefNet, ``semantic`` -> OneFormer)
over one JSON-lines pipe — so both models share a single torch import and CUDA
context. This is the only inference path for editor masks; adding a model means
adding an engine to the worker, not a new subprocess/service. See
``docs/editor_inference_host_decision.md``.
"""

import atexit
import os
import json
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable

from .ai_runtime_packages import resolve_ai_runtime_site_packages
from .ai_workflow import (
    AIWorkflowRuntime,
    default_ai_workflow_runtime,
    resolve_ai_python_script_command,
)
from .perf import perf_logger
from .semantic_mask_service import (
    SemanticWorkerResult,
    _parse_worker_metric,
    _record_worker_metric,
    _WorkerTransportError,
)


ProgressCallback = Callable[[str], None]

ENGINE_SUBJECT = "subject"
ENGINE_SEMANTIC = "semantic"
ENGINE_PROMPT = "prompt"


def _elapsed_ms(started: float) -> float:
    return (time.perf_counter() - started) * 1000.0


def _progress(callback: ProgressCallback | None, message: str) -> None:
    if callback is not None:
        callback(message)


class MaskEngineService:
    """Own one staged MaskEngine host and serialize all requests through it."""

    def __init__(self, *, idle_timeout_seconds: float = 600.0) -> None:
        self.idle_timeout_seconds = max(1.0, float(idle_timeout_seconds))
        self._lock = threading.RLock()
        self._process: subprocess.Popen[str] | None = None
        self._runtime: AIWorkflowRuntime | None = None
        self._site_packages: tuple[Path, ...] = ()
        self._imports_ready = False
        self._loaded_models: dict[str, Path] = {}
        self._embedded_image_key: str | None = None
        self._device = "unknown"
        self._request_id = 0
        self._idle_timer: threading.Timer | None = None

    @property
    def is_running(self) -> bool:
        with self._lock:
            return self._process is not None and self._process.poll() is None

    # -- warm / load / infer -------------------------------------------------
    def warm_imports(self, engine: str | None = None, progress_callback: ProgressCallback | None = None) -> str:
        with self._lock:
            self._cancel_idle_locked()
            self._ensure_process_locked()
            if self._imports_ready:
                self._log_reuse("imports")
            else:
                payload: dict[str, object] = {"command": "warm-imports"}
                if engine:
                    payload["engine"] = engine
                result = self._command_locked(payload, progress_callback=progress_callback)
                self._device = str(result.get("device") or self._device)
                self._imports_ready = True
            self._schedule_idle_locked()
            return self._device

    def warm_model(self, engine: str, model_dir: str | Path, progress_callback: ProgressCallback | None = None) -> str:
        resolved = Path(model_dir).resolve()
        with self._lock:
            self._cancel_idle_locked()
            self._ensure_process_locked()
            if not self._imports_ready:
                result = self._command_locked({"command": "warm-imports"}, progress_callback=progress_callback)
                self._device = str(result.get("device") or self._device)
                self._imports_ready = True
            if self._loaded_models.get(engine) == resolved:
                self._log_reuse("model")
            else:
                result = self._command_locked(
                    {"command": "load-model", "engine": engine, "modelDir": str(resolved)},
                    progress_callback=progress_callback,
                )
                self._device = str(result.get("device") or self._device)
                self._loaded_models[engine] = resolved
            self._schedule_idle_locked()
            return self._device

    def infer(self, engine: str, model_dir: Path, payload: dict[str, object], progress_callback: ProgressCallback | None) -> dict[str, object]:
        resolved = model_dir.resolve()
        with self._lock:
            self._cancel_idle_locked()
            self.warm_model(engine, resolved, progress_callback)
            request = {"command": "infer", "engine": engine, "modelDir": str(resolved), **payload}
            result = self._command_locked(request, progress_callback=progress_callback)
            self._device = str(result.get("device") or self._device)
            self._schedule_idle_locked()
            return result

    def infer_semantic(
        self,
        *,
        model_dir: Path,
        input_path: Path,
        output_dir: Path,
        categories: tuple[str, ...],
        minimum_coverage: float,
        progress_callback: ProgressCallback | None,
    ) -> SemanticWorkerResult:
        result = self.infer(
            ENGINE_SEMANTIC,
            model_dir,
            {
                "inputPath": str(input_path.resolve()),
                "outputDir": str(output_dir.resolve()),
                "categories": list(categories),
                "minimumCoverage": float(minimum_coverage),
            },
            progress_callback,
        )
        raw_size = result.get("sourceSize")
        source_size = (
            (int(raw_size[0]), int(raw_size[1]))
            if isinstance(raw_size, list) and len(raw_size) == 2
            else (0, 0)
        )
        raw_stats = result.get("categoryStats")
        raw_timings = result.get("timingsMs")
        timings = {
            str(k): float(v)
            for k, v in (raw_timings or {}).items()
            if isinstance(v, (int, float))
        } if isinstance(raw_timings, dict) else {}
        return SemanticWorkerResult(
            device=str(result.get("device") or self._device),
            source_size=source_size,
            category_stats=raw_stats if isinstance(raw_stats, dict) else {},
            timings_ms=timings,
        )

    def infer_subject(
        self,
        *,
        model_dir: Path,
        input_path: Path,
        output_path: Path,
        components_dir: Path,
        progress_callback: ProgressCallback | None,
    ) -> tuple[str, list[dict[str, object]]]:
        result = self.infer(
            ENGINE_SUBJECT,
            model_dir,
            {
                "input": str(input_path.resolve()),
                "output": str(output_path.resolve()),
                "componentsDir": str(components_dir.resolve()),
            },
            progress_callback,
        )
        raw_components = result.get("components")
        components = [c for c in raw_components if isinstance(c, dict)] if isinstance(raw_components, list) else []
        return str(result.get("device") or self._device), components

    def infer_prompt(
        self,
        *,
        model_dir: Path,
        input_path: Path,
        output_path: Path,
        points: list[tuple[float, float]],
        labels: list[int],
        image_key: str,
        minimum_area: float = 0.0,
        progress_callback: ProgressCallback | None = None,
    ) -> dict[str, object]:
        """Promptable click-to-select: embed the image once, segment per click.

        Re-embeds only when the source image changes; on a host respawn the
        embedding is lost, so it re-warms + re-embeds once before failing.
        """
        resolved_model_dir = model_dir.resolve()
        with self._lock:
            self._cancel_idle_locked()
            last_error: Exception | None = None
            for attempt in range(2):
                try:
                    self.warm_model(ENGINE_PROMPT, resolved_model_dir, progress_callback)
                    if self._embedded_image_key != image_key:
                        self._send_command_locked(
                            {
                                "command": "embed",
                                "engine": ENGINE_PROMPT,
                                "inputPath": str(input_path.resolve()),
                                "imageKey": image_key,
                            },
                            progress_callback=progress_callback,
                        )
                        self._embedded_image_key = image_key
                    result = self._send_command_locked(
                        {
                            "command": "segment",
                            "engine": ENGINE_PROMPT,
                            "points": [[float(x), float(y)] for (x, y) in points],
                            "labels": [int(v) for v in labels],
                            "outputPath": str(output_path.resolve()),
                            "minimumArea": float(minimum_area),
                            "imageKey": image_key,
                        },
                        progress_callback=progress_callback,
                    )
                    self._device = str(result.get("device") or self._device)
                    self._schedule_idle_locked()
                    return result
                except _WorkerTransportError as exc:
                    last_error = exc
                    self._clear_process_locked()
                    self._ensure_process_locked()
            self._schedule_idle_locked()
            raise last_error or RuntimeError("MaskEngine prompt failed.")

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
                perf_logger().duration("ai.mask.engine.service.shutdown", _elapsed_ms(started))

    # -- process management --------------------------------------------------
    def _ensure_process_locked(self) -> None:
        if self._process is not None and self._process.poll() is None:
            if self._runtime is None:
                return
            if self._runtime.device == default_ai_workflow_runtime().device:
                return
            self.shutdown()
        self._clear_process_locked()
        logger = perf_logger()
        phase_started = time.perf_counter()
        runtime, site_packages = _resolve_engine_runtime()
        logger.duration(
            "ai.mask.engine.service.runtime_resolve",
            _elapsed_ms(phase_started),
            requested_device=runtime.device,
            site_packages=len(site_packages),
        )
        worker_path = _mask_engine_worker_path(runtime)
        command = resolve_ai_python_script_command(worker_path, runtime=runtime)
        command.extend(("--server", "--device", runtime.device))
        env = _engine_worker_environment(site_packages, logger.enabled)
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
            raise RuntimeError("MaskEngine host did not expose its command pipes.")
        self._process = process
        self._runtime = runtime
        self._site_packages = site_packages
        self._imports_ready = False
        self._loaded_models = {}
        self._device = "unknown"
        logger.duration(
            "ai.mask.engine.service.worker_spawn",
            _elapsed_ms(phase_started),
            requested_device=runtime.device,
            worker_pid=process.pid,
        )

    def _command_locked(self, payload: dict[str, object], *, progress_callback: ProgressCallback | None = None) -> dict[str, object]:
        try:
            return self._send_command_locked(payload, progress_callback=progress_callback)
        except _WorkerTransportError:
            # Respawn once and, for an infer, re-establish the engine's imports +
            # model that the dead host had loaded, then retry.
            self._clear_process_locked()
            self._ensure_process_locked()
            self._imports_ready = False
            self._loaded_models = {}
            if str(payload.get("command")) == "infer":
                engine = str(payload.get("engine") or "")
                model_dir = payload.get("modelDir")
                if engine and model_dir:
                    self._send_command_locked({"command": "warm-imports"}, progress_callback=progress_callback)
                    self._imports_ready = True
                    self._send_command_locked(
                        {"command": "load-model", "engine": engine, "modelDir": str(model_dir)},
                        progress_callback=progress_callback,
                    )
                    self._loaded_models[engine] = Path(str(model_dir)).resolve()
            return self._send_command_locked(payload, progress_callback=progress_callback)

    def _send_command_locked(self, payload: dict[str, object], *, progress_callback: ProgressCallback | None = None) -> dict[str, object]:
        process = self._process
        if process is None or process.poll() is not None:
            raise _WorkerTransportError("MaskEngine host is not running.")
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
            raise _WorkerTransportError("MaskEngine host command pipe closed.") from exc

        recent_lines: list[str] = []
        while True:
            raw_line = process.stdout.readline()
            if raw_line == "":
                return_code = process.poll()
                self._clear_process_locked()
                detail = "\n".join(recent_lines[-20:])
                raise _WorkerTransportError(
                    f"MaskEngine host exited unexpectedly ({return_code})."
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
                raise _WorkerTransportError("MaskEngine host returned invalid JSON.") from exc
            if not isinstance(response, dict) or response.get("id") != request_id:
                raise _WorkerTransportError("MaskEngine host response was out of sequence.")
            perf_logger().duration(
                "ai.mask.engine.service.command",
                _elapsed_ms(started),
                command=command_name,
                engine=str(payload.get("engine") or ""),
                worker_pid=process.pid,
                ok=bool(response.get("ok")),
            )
            if not response.get("ok"):
                raise RuntimeError(str(response.get("error") or "MaskEngine host command failed."))
            result = response.get("result")
            return result if isinstance(result, dict) else {}

    def _log_reuse(self, stage: str) -> None:
        process = self._process
        perf_logger().log(
            "ai.mask.engine.service.reuse",
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
        self._loaded_models = {}
        self._embedded_image_key = None
        self._device = "unknown"
        if process is None:
            return
        for stream in (process.stdin, process.stdout):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass


_ENGINE_SERVICE: MaskEngineService | None = None
_ENGINE_SERVICE_LOCK = threading.Lock()


def default_mask_engine_service() -> MaskEngineService:
    global _ENGINE_SERVICE
    with _ENGINE_SERVICE_LOCK:
        if _ENGINE_SERVICE is None:
            _ENGINE_SERVICE = MaskEngineService()
        return _ENGINE_SERVICE


def shutdown_mask_engine() -> None:
    service = _ENGINE_SERVICE
    if service is not None:
        service.shutdown()


atexit.register(shutdown_mask_engine)


def _mask_engine_worker_path(runtime: AIWorkflowRuntime) -> Path:
    worker_path = Path(__file__).with_name("mask_engine_worker.py")
    if not worker_path.is_file() and runtime.python_executable is not None:
        worker_path = runtime.python_executable.parent / "ai_workers" / "mask_engine_worker.py"
    if not worker_path.is_file():
        raise FileNotFoundError("MaskEngine host script is missing.")
    return worker_path


def _engine_worker_environment(site_packages: tuple[Path, ...], metrics_enabled: bool) -> dict[str, str]:
    from .ai_workflow import AI_METRICS_ENV_VAR

    env = os.environ.copy()
    existing = [part for part in env.get("PYTHONPATH", "").split(os.pathsep) if part]
    env["PYTHONPATH"] = os.pathsep.join([*(str(p) for p in site_packages), *existing])
    env["PYTHONUNBUFFERED"] = "1"
    env["HF_HUB_OFFLINE"] = "1"
    env["TRANSFORMERS_OFFLINE"] = "1"
    env[AI_METRICS_ENV_VAR] = "1" if metrics_enabled else "0"
    return env


def _resolve_engine_runtime() -> tuple[AIWorkflowRuntime, tuple[Path, ...]]:
    runtime = default_ai_workflow_runtime()
    site_packages = resolve_ai_runtime_site_packages(device=runtime.device)
    if not site_packages:
        raise RuntimeError("The AI runtime is unavailable. Install the PyTorch AI runtime first.")
    # The host can serve any editor engine; require the union of their deps so a
    # spawned host can warm either without a mid-session surprise.
    required = ("torch", "transformers", "safetensors", "timm", "PIL", "numpy")
    missing = [name for name in required if not any((d / name).exists() for d in site_packages)]
    if missing:
        raise RuntimeError("The installed AI runtime is missing MaskEngine dependencies: " + ", ".join(missing))
    return runtime, site_packages


__all__ = [
    "MaskEngineService",
    "ENGINE_SUBJECT",
    "ENGINE_SEMANTIC",
    "SemanticWorkerResult",
    "default_mask_engine_service",
    "shutdown_mask_engine",
]
