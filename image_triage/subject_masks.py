from __future__ import annotations

import atexit
import hashlib
import json
import os
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np
from PIL import Image, ImageOps
from PySide6.QtCore import QObject, QRunnable, QSize, Signal
from PySide6.QtGui import QImage

from .ai_model import (
    AIModelInstallation,
    DEFAULT_BIREFNET_MODEL_REPO_ID,
    DEFAULT_BIREFNET_MODEL_REVISION,
    download_birefnet_model,
    resolve_birefnet_model_installation,
)
from .ai_runtime_packages import resolve_ai_runtime_site_packages
from .ai_workflow import (
    AI_METRICS_ENV_VAR,
    AI_METRIC_PREFIX,
    AIWorkflowRuntime,
    default_ai_workflow_runtime,
    resolve_ai_python_script_command,
)
from .imaging import load_image_for_display
from .perf import perf_logger


SUBJECT_MASK_REQUESTS: tuple[str, ...] = ("subject", "background")
SUBJECT_MASK_SEMANTIC_CATEGORIES: frozenset[str] = frozenset({"animals", "people"})
SUBJECT_MASK_PREVIEW_EDGE = 2048
SUBJECT_MASK_REFINEMENT_VERSION = "birefnet-soft-mask-components-2"

ProgressCallback = Callable[[str], None]


def _elapsed_ms(started: float) -> float:
    return (time.perf_counter() - started) * 1000.0


def _record_worker_metric(payload: dict[str, object]) -> None:
    logger = perf_logger()
    if not logger.enabled:
        return
    fields = dict(payload)
    event = str(fields.pop("event", "ai.mask.birefnet.worker.metric"))
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


@dataclass(frozen=True)
class SubjectMaskComponent:
    component_id: str
    mask_path: Path
    bbox: tuple[int, int, int, int]
    centroid: tuple[float, float]
    area_fraction: float


@dataclass(frozen=True)
class SubjectMaskResult:
    source_path: Path
    source_size: tuple[int, int]
    mask_paths: dict[str, Path]
    model_id: str
    model_version: str
    weights_hash: str
    runtime_device: str
    cache_hit: bool
    components: tuple[SubjectMaskComponent, ...] = ()
    refinement_version: str = SUBJECT_MASK_REFINEMENT_VERSION


class _WorkerTransportError(RuntimeError):
    pass


class BiRefNetWorkerService:
    """Own one staged BiRefNet worker and serialize all requests through it."""

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
        output_path: Path,
        components_dir: Path,
        progress_callback: ProgressCallback | None,
    ) -> tuple[str, list[dict[str, object]]]:
        resolved_model_dir = model_dir.resolve()
        with self._lock:
            self._cancel_idle_locked()
            self.warm_model(resolved_model_dir, progress_callback)
            result = self._command_locked(
                {
                    "command": "infer",
                    "modelDir": str(resolved_model_dir),
                    "input": str(input_path.resolve()),
                    "output": str(output_path.resolve()),
                    "componentsDir": str(components_dir.resolve()),
                },
                progress_callback=progress_callback,
            )
            self._device = str(result.get("device") or self._device)
            raw_components = result.get("components")
            components = [
                item for item in raw_components if isinstance(item, dict)
            ] if isinstance(raw_components, list) else []
            self._schedule_idle_locked()
            return self._device, components

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
                    "ai.mask.birefnet.service.shutdown",
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
        runtime, site_packages = _resolve_subject_runtime()
        logger.duration(
            "ai.mask.birefnet.service.runtime_resolve",
            _elapsed_ms(phase_started),
            requested_device=runtime.device,
            site_packages=len(site_packages),
        )
        worker_path = _subject_worker_path(runtime)
        command = resolve_ai_python_script_command(worker_path, runtime=runtime)
        command.extend(("--server", "--device", runtime.device))
        env = _subject_worker_environment(site_packages, logger.enabled)
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
            raise RuntimeError("BiRefNet worker did not expose its command pipes.")
        self._process = process
        self._runtime = runtime
        self._site_packages = site_packages
        self._imports_ready = False
        self._loaded_model_dir = None
        self._device = "unknown"
        logger.duration(
            "ai.mask.birefnet.service.worker_spawn",
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
        process = self._process
        if process is None or process.poll() is not None:
            raise _WorkerTransportError("BiRefNet worker is not running.")
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
            raise _WorkerTransportError("BiRefNet worker command pipe closed.") from exc

        recent_lines: list[str] = []
        while True:
            raw_line = process.stdout.readline()
            if raw_line == "":
                return_code = process.poll()
                self._clear_process_locked()
                detail = "\n".join(recent_lines[-20:])
                raise _WorkerTransportError(
                    f"BiRefNet worker exited unexpectedly ({return_code})."
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
                raise _WorkerTransportError("BiRefNet worker returned invalid JSON.") from exc
            if not isinstance(response, dict) or response.get("id") != request_id:
                raise _WorkerTransportError("BiRefNet worker response was out of sequence.")
            perf_logger().duration(
                "ai.mask.birefnet.service.command",
                _elapsed_ms(started),
                command=command_name,
                worker_pid=process.pid,
                ok=bool(response.get("ok")),
            )
            if not response.get("ok"):
                raise RuntimeError(
                    str(response.get("error") or "BiRefNet worker command failed.")
                )
            result = response.get("result")
            return result if isinstance(result, dict) else {}

    def _log_reuse(self, stage: str) -> None:
        process = self._process
        perf_logger().log(
            "ai.mask.birefnet.service.reuse",
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


_WORKER_SERVICE: BiRefNetWorkerService | None = None
_WORKER_SERVICE_LOCK = threading.Lock()
_WEIGHTS_HASH_CACHE: dict[tuple[str, int, int], str] = {}
_WEIGHTS_HASH_LOCK = threading.Lock()


def default_birefnet_worker_service() -> BiRefNetWorkerService:
    global _WORKER_SERVICE
    with _WORKER_SERVICE_LOCK:
        if _WORKER_SERVICE is None:
            _WORKER_SERVICE = BiRefNetWorkerService()
        return _WORKER_SERVICE


def shutdown_birefnet_worker() -> None:
    service = _WORKER_SERVICE
    if service is not None:
        service.shutdown()


atexit.register(shutdown_birefnet_worker)


def default_subject_mask_cache_root() -> Path:
    if os.name == "nt":
        local_appdata = os.environ.get("LOCALAPPDATA")
        base = Path(local_appdata) if local_appdata else Path.home() / "AppData" / "Local"
    else:
        xdg_cache = os.environ.get("XDG_CACHE_HOME")
        base = Path(xdg_cache) if xdg_cache else Path.home() / ".cache"
    return base / "image_triage_ai_cache" / "subject_masks"


def ensure_subject_masks(
    source_path: str | Path,
    *,
    installation: AIModelInstallation | None = None,
    cache_root: str | Path | None = None,
    progress_callback: ProgressCallback | None = None,
) -> SubjectMaskResult:
    logger = perf_logger()
    total_started = time.perf_counter()
    source = Path(source_path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    logger.log(
        "ai.mask.birefnet.request",
        path=source,
        preview_edge=SUBJECT_MASK_PREVIEW_EDGE,
    )
    phase_started = time.perf_counter()
    model_installation = installation or resolve_birefnet_model_installation()
    logger.duration(
        "ai.mask.birefnet.model_resolve",
        _elapsed_ms(phase_started),
        installed=model_installation.is_installed,
        model_id=model_installation.repo_id,
        model_revision=model_installation.revision,
    )
    if not model_installation.is_installed:
        phase_started = time.perf_counter()
        _validate_subject_runtime()
        logger.duration(
            "ai.mask.birefnet.runtime_validate",
            _elapsed_ms(phase_started),
            reason="pre_download",
        )
        _progress(progress_callback, "Downloading BiRefNet subject model...")

        def download_progress(filename: str, current: int, total: int) -> None:
            name = Path(filename).name
            if total > 0:
                _progress(
                    progress_callback,
                    f"Downloading {name}: {current / (1024 * 1024):.1f} / "
                    f"{total / (1024 * 1024):.1f} MB",
                )
            else:
                _progress(progress_callback, f"Downloading {name}...")

        phase_started = time.perf_counter()
        download_birefnet_model(
            model_installation,
            progress_callback=download_progress,
        )
        logger.duration(
            "ai.mask.birefnet.download",
            _elapsed_ms(phase_started),
            model_id=model_installation.repo_id,
        )

    model_path = model_installation.install_dir / "model.safetensors"
    if not model_installation.is_installed or not model_path.is_file():
        missing = ", ".join(path.name for path in model_installation.missing_files)
        raise FileNotFoundError(f"BiRefNet installation is incomplete: {missing}")

    phase_started = time.perf_counter()
    weights_hash, weights_hash_cache_hit = _cached_sha256_file(model_path)
    logger.duration(
        "ai.mask.birefnet.weights_hash",
        _elapsed_ms(phase_started),
        bytes=model_path.stat().st_size,
        cache_hit=weights_hash_cache_hit,
    )
    stat = source.stat()
    phase_started = time.perf_counter()
    cache_key = _source_cache_key(source, stat.st_size, stat.st_mtime_ns, weights_hash)
    cache_dir = Path(cache_root or default_subject_mask_cache_root()) / cache_key
    metadata_path = cache_dir / "metadata.json"
    mask_paths = {
        "subject": cache_dir / "subject.png",
        "background": cache_dir / "background.png",
    }
    components_dir = cache_dir / "components"
    cached_metadata = _load_json(metadata_path)
    if (
        cached_metadata.get("sourceSizeBytes") == stat.st_size
        and cached_metadata.get("sourceMtimeNs") == stat.st_mtime_ns
        and cached_metadata.get("weightsHash") == weights_hash
        and cached_metadata.get("refinementVersion") == SUBJECT_MASK_REFINEMENT_VERSION
        and all(path.is_file() for path in mask_paths.values())
    ):
        source_size = tuple(cached_metadata.get("sourceSize") or ())
        if len(source_size) == 2 and all(int(value) > 0 for value in source_size):
            _progress(progress_callback, "Using cached subject masks")
            raw_cached_components = cached_metadata.get("components")
            raw_cached_components = (
                raw_cached_components if isinstance(raw_cached_components, list) else []
            )
            components = _components_from_metadata(cached_metadata, components_dir)
            if len(components) == len(raw_cached_components):
                logger.duration(
                    "ai.mask.birefnet.cache_lookup",
                    _elapsed_ms(phase_started),
                    cache_hit=True,
                    components=len(components),
                )
                logger.duration(
                    "ai.mask.birefnet.total",
                    _elapsed_ms(total_started),
                    cache_hit=True,
                    device=str(cached_metadata.get("runtimeDevice") or "unknown"),
                    components=len(components),
                )
                return SubjectMaskResult(
                    source_path=source,
                    source_size=(int(source_size[0]), int(source_size[1])),
                    mask_paths=mask_paths,
                    model_id=model_installation.repo_id,
                    model_version=model_installation.revision,
                    weights_hash=f"sha256:{weights_hash}",
                    runtime_device=str(cached_metadata.get("runtimeDevice") or "unknown"),
                    cache_hit=True,
                    components=components,
                )

    logger.duration(
        "ai.mask.birefnet.cache_lookup",
        _elapsed_ms(phase_started),
        cache_hit=False,
    )
    phase_started = time.perf_counter()
    _validate_subject_runtime()
    logger.duration(
        "ai.mask.birefnet.runtime_validate",
        _elapsed_ms(phase_started),
        reason="pre_inference",
    )
    _progress(progress_callback, "Decoding image...")
    phase_started = time.perf_counter()
    rgb = _decode_rgb_preview(source, SUBJECT_MASK_PREVIEW_EDGE)
    logger.duration(
        "ai.mask.birefnet.decode_preview",
        _elapsed_ms(phase_started),
        source_bytes=stat.st_size,
        width=int(rgb.shape[1]),
        height=int(rgb.shape[0]),
    )
    cache_dir.mkdir(parents=True, exist_ok=True)
    worker_input = cache_dir / "worker-input.png"
    phase_started = time.perf_counter()
    Image.fromarray(rgb, mode="RGB").save(worker_input)
    logger.duration(
        "ai.mask.birefnet.worker_input_write",
        _elapsed_ms(phase_started),
        bytes=worker_input.stat().st_size,
    )
    try:
        phase_started = time.perf_counter()
        try:
            runtime_device, raw_components = _run_subject_worker(
                model_dir=model_installation.install_dir,
                input_path=worker_input,
                output_path=mask_paths["subject"],
                components_dir=components_dir,
                progress_callback=progress_callback,
            )
        except Exception as exc:
            logger.duration(
                "ai.mask.birefnet.worker_total.failed",
                _elapsed_ms(phase_started),
                error=str(exc),
            )
            logger.duration(
                "ai.mask.birefnet.total.failed",
                _elapsed_ms(total_started),
                cache_hit=False,
                error=str(exc),
            )
            raise
        else:
            logger.duration(
                "ai.mask.birefnet.worker_total",
                _elapsed_ms(phase_started),
                device=runtime_device,
                components=len(raw_components),
            )
    finally:
        worker_input.unlink(missing_ok=True)
    if not mask_paths["subject"].is_file():
        raise RuntimeError("BiRefNet completed without producing a subject mask.")
    phase_started = time.perf_counter()
    with Image.open(mask_paths["subject"]) as foreground:
        subject_mask = foreground.convert("L")
        ImageOps.invert(subject_mask).save(mask_paths["background"])
    logger.duration(
        "ai.mask.birefnet.background_write",
        _elapsed_ms(phase_started),
    )
    phase_started = time.perf_counter()
    components = _components_from_worker(raw_components, components_dir)
    logger.duration(
        "ai.mask.birefnet.component_load",
        _elapsed_ms(phase_started),
        components=len(components),
    )

    metadata = {
        "sourcePath": str(source),
        "sourceSizeBytes": stat.st_size,
        "sourceMtimeNs": stat.st_mtime_ns,
        "sourceSize": [int(rgb.shape[1]), int(rgb.shape[0])],
        "modelId": model_installation.repo_id,
        "modelVersion": model_installation.revision,
        "weightsHash": weights_hash,
        "runtimeDevice": runtime_device,
        "refinementVersion": SUBJECT_MASK_REFINEMENT_VERSION,
        "components": [_component_metadata(component, components_dir) for component in components],
    }
    phase_started = time.perf_counter()
    metadata_path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    logger.duration(
        "ai.mask.birefnet.metadata_write",
        _elapsed_ms(phase_started),
    )
    logger.duration(
        "ai.mask.birefnet.total",
        _elapsed_ms(total_started),
        cache_hit=False,
        device=runtime_device,
        components=len(components),
        width=int(rgb.shape[1]),
        height=int(rgb.shape[0]),
    )
    return SubjectMaskResult(
        source_path=source,
        source_size=(int(rgb.shape[1]), int(rgb.shape[0])),
        mask_paths=mask_paths,
        model_id=model_installation.repo_id,
        model_version=model_installation.revision,
        weights_hash=f"sha256:{weights_hash}",
        runtime_device=runtime_device,
        cache_hit=False,
        components=components,
    )


def _run_subject_worker(
    *,
    model_dir: Path,
    input_path: Path,
    output_path: Path,
    components_dir: Path,
    progress_callback: ProgressCallback | None,
) -> tuple[str, list[dict[str, object]]]:
    return default_birefnet_worker_service().infer(
        model_dir=model_dir,
        input_path=input_path,
        output_path=output_path,
        components_dir=components_dir,
        progress_callback=progress_callback,
    )


def _subject_worker_path(runtime: AIWorkflowRuntime) -> Path:
    worker_path = Path(__file__).with_name("birefnet_worker.py")
    if not worker_path.is_file() and runtime.python_executable is not None:
        worker_path = runtime.python_executable.parent / "ai_workers" / "birefnet_worker.py"
    if not worker_path.is_file():
        raise FileNotFoundError("BiRefNet worker script is missing.")
    return worker_path


def _subject_worker_environment(
    site_packages: tuple[Path, ...],
    metrics_enabled: bool,
) -> dict[str, str]:
    env = os.environ.copy()
    existing_pythonpath = [part for part in env.get("PYTHONPATH", "").split(os.pathsep) if part]
    env["PYTHONPATH"] = os.pathsep.join(
        [*(str(path) for path in site_packages), *existing_pythonpath]
    )
    env["PYTHONUNBUFFERED"] = "1"
    env["HF_HOME"] = str(default_subject_mask_cache_root().parent / "huggingface")
    env["HF_HUB_OFFLINE"] = "1"
    env["TRANSFORMERS_OFFLINE"] = "1"
    env[AI_METRICS_ENV_VAR] = "1" if metrics_enabled else "0"
    return env


def combine_subject_components(
    result: SubjectMaskResult,
    component_ids: tuple[str, ...],
) -> Path:
    selected = [
        component
        for component in result.components
        if component.component_id in set(component_ids)
    ]
    if not selected:
        raise ValueError("Select at least one subject.")
    if len(selected) == 1:
        return selected[0].mask_path
    if len(selected) == len(result.components):
        return result.mask_paths["subject"]
    key = "-".join(component.component_id for component in selected)
    output_path = result.mask_paths["subject"].parent / f"selection-{key}.png"
    merged: np.ndarray | None = None
    for component in selected:
        with Image.open(component.mask_path) as loaded:
            values = np.asarray(loaded.convert("L"), dtype=np.uint8)
        merged = values.copy() if merged is None else np.maximum(merged, values)
    assert merged is not None
    Image.fromarray(merged, mode="L").save(output_path)
    return output_path


def _components_from_worker(
    values: list[dict[str, object]],
    components_dir: Path,
) -> tuple[SubjectMaskComponent, ...]:
    return _parse_components(values, components_dir)


def _components_from_metadata(
    metadata: dict[str, object],
    components_dir: Path,
) -> tuple[SubjectMaskComponent, ...]:
    values = metadata.get("components")
    return _parse_components(values if isinstance(values, list) else [], components_dir)


def _parse_components(
    values: list[object],
    components_dir: Path,
) -> tuple[SubjectMaskComponent, ...]:
    components: list[SubjectMaskComponent] = []
    for value in values:
        if not isinstance(value, dict):
            continue
        component_id = str(value.get("id") or "").strip()
        relative_path = str(value.get("path") or "").strip()
        bbox = value.get("bbox")
        centroid = value.get("centroid")
        path = components_dir / relative_path
        if (
            not component_id
            or not relative_path
            or not path.is_file()
            or not isinstance(bbox, list)
            or len(bbox) != 4
            or not isinstance(centroid, list)
            or len(centroid) != 2
        ):
            continue
        components.append(
            SubjectMaskComponent(
                component_id=component_id,
                mask_path=path,
                bbox=tuple(int(item) for item in bbox),
                centroid=tuple(float(item) for item in centroid),
                area_fraction=float(value.get("areaFraction") or 0.0),
            )
        )
    return tuple(components)


def _component_metadata(
    component: SubjectMaskComponent,
    components_dir: Path,
) -> dict[str, object]:
    return {
        "id": component.component_id,
        "path": component.mask_path.relative_to(components_dir).as_posix(),
        "bbox": list(component.bbox),
        "centroid": list(component.centroid),
        "areaFraction": component.area_fraction,
    }


def _validate_subject_runtime() -> None:
    _resolve_subject_runtime()


def _resolve_subject_runtime() -> tuple[AIWorkflowRuntime, tuple[Path, ...]]:
    runtime = default_ai_workflow_runtime()
    site_packages = resolve_ai_runtime_site_packages(device=runtime.device)
    if not site_packages:
        raise RuntimeError(
            "The AI runtime is unavailable. Install the PyTorch AI runtime first."
        )
    required_modules = (
        "torch",
        "transformers",
        "timm",
        "safetensors",
    )
    missing = [
        name
        for name in required_modules
        if not any((site_dir / name).exists() for site_dir in site_packages)
    ]
    if missing:
        raise RuntimeError(
            "The installed AI runtime is missing BiRefNet dependencies: "
            + ", ".join(missing)
        )
    return runtime, site_packages


def _decode_rgb_preview(path: Path, long_edge: int) -> np.ndarray:
    image, error = load_image_for_display(
        str(path),
        QSize(long_edge, long_edge),
        prefer_embedded=True,
    )
    if image.isNull():
        raise RuntimeError(error or "Could not decode image.")
    converted = image.convertToFormat(QImage.Format.Format_RGB888)
    width = converted.width()
    height = converted.height()
    stride = converted.bytesPerLine()
    buffer = np.frombuffer(converted.bits(), dtype=np.uint8, count=height * stride)
    return buffer.reshape(height, stride)[:, : width * 3].reshape(height, width, 3).copy()


def _progress(callback: ProgressCallback | None, message: str) -> None:
    if callback is not None:
        callback(message)


def _source_cache_key(source: Path, size: int, mtime_ns: int, weights_hash: str) -> str:
    identity = "\0".join(
        (
            os.path.normcase(str(source)),
            str(size),
            str(mtime_ns),
            weights_hash,
            SUBJECT_MASK_REFINEMENT_VERSION,
        )
    )
    return hashlib.sha256(identity.encode("utf-8", errors="surrogatepass")).hexdigest()[:24]


def _load_json(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _cached_sha256_file(path: Path) -> tuple[str, bool]:
    resolved = path.resolve()
    stat = resolved.stat()
    key = (os.path.normcase(str(resolved)), stat.st_size, stat.st_mtime_ns)
    with _WEIGHTS_HASH_LOCK:
        cached = _WEIGHTS_HASH_CACHE.get(key)
        if cached is not None:
            return cached, True
        digest = _sha256_file(resolved)
        _WEIGHTS_HASH_CACHE.clear()
        _WEIGHTS_HASH_CACHE[key] = digest
        return digest, False


class SubjectMaskWarmTask(QRunnable):
    """Warm installed BiRefNet resources without downloading or blocking the UI."""

    def __init__(self, stage: str) -> None:
        super().__init__()
        normalized = stage.strip().casefold()
        if normalized not in {"imports", "model"}:
            raise ValueError(f"Unknown BiRefNet warm stage: {stage}")
        self.stage = normalized
        self.setAutoDelete(True)

    def run(self) -> None:
        started = time.perf_counter()
        logger = perf_logger()
        try:
            service = default_birefnet_worker_service()
            if self.stage == "imports":
                device = service.warm_imports()
            else:
                installation = resolve_birefnet_model_installation()
                if not installation.is_installed:
                    logger.duration(
                        "ai.mask.birefnet.warm.skipped",
                        _elapsed_ms(started),
                        stage=self.stage,
                        reason="model_not_installed",
                    )
                    return
                device = service.warm_model(installation.install_dir)
            logger.duration(
                "ai.mask.birefnet.warm",
                _elapsed_ms(started),
                stage=self.stage,
                device=device,
            )
        except Exception as exc:
            logger.duration(
                "ai.mask.birefnet.warm.failed",
                _elapsed_ms(started),
                stage=self.stage,
                error=str(exc),
            )


class SubjectMaskTaskSignals(QObject):
    progress = Signal(str, str)
    finished = Signal(str, str, object)
    failed = Signal(str, str, str)


class SubjectMaskTask(QRunnable):
    def __init__(
        self,
        source_path: str | Path,
        request: str,
        *,
        installation: AIModelInstallation | None = None,
        cache_root: str | Path | None = None,
    ) -> None:
        super().__init__()
        normalized = request.strip().casefold()
        if normalized not in SUBJECT_MASK_REQUESTS:
            raise ValueError(f"Unknown subject mask request: {request}")
        self.source_path = Path(source_path).expanduser().resolve()
        self.request = normalized
        self.installation = installation
        self.cache_root = cache_root
        self.signals = SubjectMaskTaskSignals()
        self.setAutoDelete(True)

    def run(self) -> None:
        source_text = str(self.source_path)
        try:
            result = ensure_subject_masks(
                self.source_path,
                installation=self.installation,
                cache_root=self.cache_root,
                progress_callback=lambda message: self.signals.progress.emit(
                    self.request,
                    message,
                ),
            )
            self.signals.finished.emit(self.request, source_text, result)
        except Exception as exc:
            self.signals.failed.emit(self.request, source_text, str(exc))


__all__ = [
    "DEFAULT_BIREFNET_MODEL_REPO_ID",
    "DEFAULT_BIREFNET_MODEL_REVISION",
    "SUBJECT_MASK_REQUESTS",
    "SUBJECT_MASK_SEMANTIC_CATEGORIES",
    "BiRefNetWorkerService",
    "SubjectMaskComponent",
    "SubjectMaskResult",
    "SubjectMaskTask",
    "SubjectMaskWarmTask",
    "combine_subject_components",
    "default_birefnet_worker_service",
    "ensure_subject_masks",
    "shutdown_birefnet_worker",
]
