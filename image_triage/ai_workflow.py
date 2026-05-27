from __future__ import annotations

"""External AI culling pipeline orchestration and stage caching.

This module owns the contract between Image Triage and the separate
AICullingPipeline runtime. It resolves runtime paths, stages supported images,
builds deterministic cache keys for each AI stage, and executes the extraction,
grouping, and report commands on worker threads.
"""

import ctypes
import csv
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
import urllib.request
from dataclasses import dataclass
from hashlib import sha1
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from PySide6.QtCore import QObject, QRunnable, Signal

from .ai_model import (
    DEFAULT_SEMANTIC_MODEL_REPO_ID,
    AIModelInstallation,
    resolve_ai_model_installation,
    resolve_semantic_model_installation,
)
from .ai_runtime_packages import load_ai_runtime_installation_status
from .formats import RAW_SUFFIXES
from .perf import perf_logger

if TYPE_CHECKING:
    from .models import ImageRecord


HIDDEN_ROOT_NAME = ".image_triage_ai"
ARTIFACTS_DIR_NAME = "artifacts"
REPORT_DIR_NAME = "ranker_report"
LOGS_DIR_NAME = "logs"
LATEST_AI_RUN_LOG_FILENAME = "latest_ai_culling.log"
STAGE_WORKSPACES_DIR_NAME = "workspaces"
STAGE_INPUT_DIR_NAME = "input"
STAGE_MANIFEST_FILENAME = "stage_manifest.json"
FILE_ATTRIBUTE_HIDDEN = 0x2
DEFAULT_STAGE_EXTENSIONS = tuple(
    sorted(
        {
            ".jpg",
            ".jpeg",
            ".png",
            ".bmp",
            ".tif",
            ".tiff",
            ".webp",
            *RAW_SUFFIXES,
        }
    )
)
DRIVE_UNKNOWN = 0
DRIVE_NO_ROOT_DIR = 1
DRIVE_REMOVABLE = 2
DRIVE_FIXED = 3
DRIVE_REMOTE = 4
TQDM_PROGRESS_PATTERN = re.compile(
    r"^(?P<label>Scanning images|Extracting embeddings|Classifying images):.*?\|\s*(?:[^|]*\|\s*)?(?P<current>\d+)/(?P<total>\d+)\s*\[(?P<timing>[^\]]+)\]"
)
AI_METRIC_PREFIX = "AI_METRIC "
AI_METRICS_ENV_VAR = "IMAGE_TRIAGE_AI_METRICS"
AI_RUNTIME_DIR_NAME = "ai_runtime"
AI_RUNNER_TARGET_NAME = "ai_python_runner.exe" if os.name == "nt" else "ai_python_runner"
AI_RUNNER_SCRIPT_RELATIVE_PATH = Path("packaging") / "ai_python_runner.py"
DEFAULT_RANKER_RUN_DIR_NAME = "ranker_run_mlp_100ep"
DEFAULT_BUNDLED_CHECKPOINT_RELATIVE_PATH = (
    Path("outputs") / DEFAULT_RANKER_RUN_DIR_NAME / "best_ranker.pt"
)
LEGACY_BUNDLED_CHECKPOINT_RELATIVE_PATH = (
    Path("outputs") / "legacy_default" / DEFAULT_RANKER_RUN_DIR_NAME / "best_ranker.pt"
)
REQUIRED_AI_SCRIPT_RELATIVE_PATHS = (
    "scripts/extract_embeddings.py",
    "scripts/cluster_embeddings.py",
    "scripts/export_ranked_report.py",
)
MISSING_MODULE_PATTERN = re.compile(r"ModuleNotFoundError:\s+No module named ['\"](?P<module>[^'\"]+)['\"]")
FAILURE_OUTPUT_TAIL_LINE_COUNT = 80


@dataclass(slots=True, frozen=True)
class AIWorkflowRuntime:
    """Fully resolved runtime configuration for the external AI pipeline."""
    engine_root: Path
    python_executable: Path | None
    model_name: str
    checkpoint_path: Path
    extraction_config_path: Path
    clustering_config_path: Path
    report_config_path: Path
    semantic_config_path: Path = Path()
    model_installation: AIModelInstallation | None = None
    checkpoint_download_url: str | None = None
    device: str = "auto"
    batch_size: int = 16
    num_workers: int = 4
    local_stage_mode: str = "auto"
    local_stage_root: Path | None = None
    semantic_sidecar_enabled: bool = False
    semantic_model_name: str = "openai/clip-vit-base-patch32"
    semantic_batch_size: int = 16

    def validate(self) -> None:
        """Fail fast if the configured runtime cannot actually execute."""
        missing: list[str] = []
        for label, path in (
            ("engine root", self.engine_root),
            ("extract config", self.extraction_config_path),
            ("cluster config", self.clustering_config_path),
            ("report config", self.report_config_path),
        ):
            if not path.exists():
                missing.append(f"{label}: {path}")
        if self.semantic_sidecar_enabled:
            if not self.semantic_config_path.exists():
                missing.append(f"semantic config: {self.semantic_config_path}")
            semantic_script = self.engine_root / "scripts/classify_images.py"
            semantic_executable = semantic_script.with_suffix(".exe")
            if not semantic_executable.exists() and not semantic_script.exists():
                missing.append(f"semantic tool: {semantic_script}")
            if not self.semantic_model_name:
                missing.append("semantic model: (missing)")
        for script_relative_path in REQUIRED_AI_SCRIPT_RELATIVE_PATHS:
            script_path = self.engine_root / script_relative_path
            script_executable = script_path.with_suffix(".exe")
            if script_executable.exists():
                continue
            if not script_path.exists():
                missing.append(f"ai tool: {script_path}")
                continue
            if self.python_executable is None:
                missing.append("python executable: (missing)")
            elif not self.python_executable.exists():
                missing.append(f"python executable: {self.python_executable}")
        if self.model_installation is not None and not self.model_installation.is_installed:
            missing.extend(f"ai model: {path}" for path in self.model_installation.missing_files)
        elif self.model_name:
            model_path = Path(self.model_name).expanduser()
            if (
                model_path.is_absolute()
                or "/" in self.model_name
                or "\\" in self.model_name
                or self.model_name.startswith(".")
            ):
                if not model_path.exists():
                    missing.append(f"ai model: {model_path}")
                elif model_path.is_dir():
                    for filename in ("config.json", "model.safetensors"):
                        candidate = model_path / filename
                        if not candidate.exists():
                            missing.append(f"ai model: {candidate}")
        if not self.checkpoint_path.exists():
            if self.checkpoint_download_url:
                try:
                    _download_asset(self.checkpoint_download_url, self.checkpoint_path)
                except Exception as exc:
                    missing.append(f"checkpoint download failed: {exc}")
            if not self.checkpoint_path.exists():
                if self.checkpoint_download_url:
                    missing.append(
                        f"checkpoint: {self.checkpoint_path} (download from {self.checkpoint_download_url})"
                    )
                else:
                    missing.append(
                        f"checkpoint: {self.checkpoint_path} (missing; set AICULLING_CHECKPOINT or AICULLING_CHECKPOINT_URL)"
                    )
        if self.local_stage_mode not in {"auto", "always", "off"}:
            raise ValueError("local_stage_mode must be 'auto', 'always', or 'off'.")
        if missing:
            raise FileNotFoundError("Missing AI workflow paths:\n" + "\n".join(missing))


@dataclass(slots=True, frozen=True)
class AIWorkflowPaths:
    """Folder-local filesystem layout for AI artifacts and ranked reports."""
    folder: Path
    hidden_root: Path
    artifacts_dir: Path
    report_dir: Path
    ranked_export_path: Path
    html_report_path: Path
    semantic_export_path: Path
    semantic_summary_path: Path


@dataclass(slots=True, frozen=True)
class AIStageCacheKeys:
    """Deterministic signatures for extract, cluster, and report reuse."""
    embedding_cache_key: str
    cluster_cache_key: str
    report_cache_key: str
    semantic_cache_key: str = ""


class AIRunSignals(QObject):
    """Signals emitted while the external AI pipeline is running."""
    started = Signal(str)
    stage = Signal(str, int, int, str)
    progress = Signal(str, str, int, int, str)
    detail = Signal(str, str)
    finished = Signal(str, str, str)
    failed = Signal(str, str)
    cancelled = Signal(str, str)


class AIRunCancelled(RuntimeError):
    """Raised inside the AI worker when the user stops the active run."""


class AIRunTask(QRunnable):
    """Worker that runs the AI pipeline stages for one folder."""
    def __init__(
        self,
        *,
        folder: Path,
        runtime: AIWorkflowRuntime,
        paths: AIWorkflowPaths,
        labels_dir: Path | None = None,
        reference_bank_path: Path | None = None,
        skip_extract: bool = False,
        skip_cluster: bool = False,
        detailed_progress: bool = False,
    ) -> None:
        super().__init__()
        self.folder = folder
        self.runtime = runtime
        self.paths = paths
        self.labels_dir = labels_dir
        self.reference_bank_path = reference_bank_path
        self.skip_cluster = bool(skip_cluster)
        self.skip_extract = bool(skip_extract or self.skip_cluster)
        self.detailed_progress = bool(detailed_progress)
        self.signals = AIRunSignals()
        self.setAutoDelete(True)
        self._cancel_requested = False
        self._current_process: subprocess.Popen[str] | None = None

    def cancel(self) -> None:
        """Request cancellation and terminate the running child process if present."""

        self._cancel_requested = True
        process = self._current_process
        if process is None or process.poll() is not None:
            return
        _terminate_process(process)

    def _is_cancelled(self) -> bool:
        return self._cancel_requested

    def _emit_detail(self, folder_text: str, message: str) -> None:
        message = " ".join((message or "").split())
        if message:
            self.signals.detail.emit(folder_text, message)

    def _set_current_process(self, process: subprocess.Popen[str] | None) -> None:
        self._current_process = process
        if process is not None and self._cancel_requested:
            _terminate_process(process)

    def _raise_if_cancelled(self) -> None:
        if self._cancel_requested:
            raise AIRunCancelled("AI review stopped by user.")

    def run(self) -> None:
        logger = perf_logger()
        task_start = time.perf_counter() if logger.enabled else 0.0
        folder_text = str(self.folder)
        staged_input_dir: Path | None = None
        log_path = ai_run_log_path(self.paths)
        if logger.enabled:
            logger.log(
                "ai.task.started",
                folder=folder_text,
                skip_extract=self.skip_extract,
                skip_cluster=self.skip_cluster,
                semantic_sidecar=self.runtime.semantic_sidecar_enabled,
                batch_size=self.runtime.batch_size,
                num_workers=self.runtime.num_workers,
                device=self.runtime.device,
            )
        self.signals.started.emit(folder_text)
        log_path.parent.mkdir(parents=True, exist_ok=True)

        def _log_line(handle, text: str = "") -> None:
            handle.write(text + "\n")
            handle.flush()

        def _log_chunk(handle, chunk: str) -> None:
            handle.write(chunk)
            handle.flush()

        try:
            with log_path.open("w", encoding="utf-8", newline="") as log_handle:
                _log_line(log_handle, f"AI culling started: {time.strftime('%Y-%m-%d %H:%M:%S')}")
                _log_line(log_handle, f"Folder: {self.folder}")
                _log_line(log_handle, f"Engine root: {self.runtime.engine_root}")
                _log_line(log_handle, f"Python: {self.runtime.python_executable}")
                _log_line(log_handle, f"Checkpoint: {self.runtime.checkpoint_path}")
                _log_line(log_handle, f"Model: {self.runtime.model_name}")
                _log_line(log_handle, f"Local staging: {self.runtime.local_stage_mode}")
                _log_line(log_handle, f"Semantic sidecar: {self.runtime.semantic_sidecar_enabled}")
                if self.runtime.semantic_sidecar_enabled:
                    _log_line(log_handle, f"Semantic model: {self.runtime.semantic_model_name}")
                if self.runtime.local_stage_root is not None:
                    _log_line(log_handle, f"Stage root: {self.runtime.local_stage_root}")
                _log_line(log_handle, f"Artifacts dir: {self.paths.artifacts_dir}")
                _log_line(log_handle, f"Report dir: {self.paths.report_dir}")

                try:
                    self._emit_detail(folder_text, "Validating AI runtime and preparing the folder workspace.")
                    self._raise_if_cancelled()
                    self.runtime.validate()
                    prepare_hidden_ai_workspace(self.folder)
                    self._raise_if_cancelled()
                except Exception as exc:
                    if isinstance(exc, AIRunCancelled):
                        _log_line(log_handle)
                        _log_line(log_handle, "AI culling stopped before validation completed.")
                        self.signals.cancelled.emit(folder_text, "AI review stopped.")
                        return
                    _log_line(log_handle)
                    _log_line(log_handle, "Validation or workspace preparation failed.")
                    _log_line(log_handle, traceback.format_exc().rstrip())
                    if logger.enabled:
                        logger.duration(
                            "ai.task.failed",
                            (time.perf_counter() - task_start) * 1000.0,
                            folder=folder_text,
                            phase="validate",
                            error=str(exc),
                            log_path=str(log_path),
                        )
                    self.signals.failed.emit(folder_text, _append_log_path(str(exc), log_path))
                    return

                commands: list[tuple[str, str, str, list[str]]] = []
                if not self.skip_extract:
                    commands.append(
                        (
                            "extract",
                            "Extracting embeddings",
                            "scripts/extract_embeddings.py",
                            [
                                "--config",
                                str(self.runtime.extraction_config_path),
                                "--input-dir",
                                "",
                                "--output-dir",
                                str(self.paths.artifacts_dir),
                                "--batch-size",
                                str(self.runtime.batch_size),
                                "--model-name",
                                self.runtime.model_name,
                                "--device",
                                self.runtime.device,
                                "--num-workers",
                                str(self.runtime.num_workers),
                            ],
                        )
                    )
                if not self.skip_cluster:
                    commands.append(
                        (
                            "cluster",
                            "Building culling groups",
                            "scripts/cluster_embeddings.py",
                            [
                                "--config",
                                str(self.runtime.clustering_config_path),
                                "--artifacts-dir",
                                str(self.paths.artifacts_dir),
                                "--output-dir",
                                str(self.paths.artifacts_dir),
                            ],
                        )
                    )
                report_args = [
                    "--config",
                    str(self.runtime.report_config_path),
                    "--artifacts-dir",
                    str(self.paths.artifacts_dir),
                    "--checkpoint-path",
                    str(self.runtime.checkpoint_path),
                    "--output-dir",
                    str(self.paths.report_dir),
                    "--device",
                    self.runtime.device,
                ]
                commands.append(
                    (
                        "report",
                        "Scoring groups and building report",
                        "scripts/export_ranked_report.py",
                        report_args,
                    )
                )
                if self.runtime.semantic_sidecar_enabled:
                    commands.append(
                        (
                            "semantic",
                            "Classifying images semantically",
                            "scripts/classify_images.py",
                            [
                                "--config",
                                str(self.runtime.semantic_config_path),
                                "--artifacts-dir",
                                str(self.paths.artifacts_dir),
                                "--output-dir",
                                str(self.paths.report_dir),
                                "--model-name",
                                self.runtime.semantic_model_name,
                                "--batch-size",
                                str(self.runtime.semantic_batch_size),
                                "--device",
                                self.runtime.device,
                            ],
                        )
                    )

                if self.labels_dir is not None and self.labels_dir.exists():
                    report_args.extend(["--labels-dir", str(self.labels_dir)])
                if self.reference_bank_path is not None and self.reference_bank_path.exists():
                    report_args.extend(["--reference-bank-path", str(self.reference_bank_path)])

                use_local_stage = not self.skip_extract and _should_use_local_staging(self.folder, self.runtime)
                total_stages = len(commands) + (1 if use_local_stage else 0)

                if use_local_stage:
                    self.signals.stage.emit(folder_text, 1, total_stages, "Staging images locally")
                    self._emit_detail(folder_text, "Checking whether images should be copied into the local AI staging cache.")
                    stage_start = time.perf_counter() if logger.enabled else 0.0

                    def _stage_progress(current: int, total: int, eta_text: str, message: str) -> None:
                        self.signals.progress.emit(folder_text, message, current, total, eta_text)
                        _log_line(
                            log_handle,
                            f"[staging] {message} | {current}/{total}" + (f" | eta {eta_text}" if eta_text else ""),
                        )

                    staged_input_dir = stage_supported_images(
                        source_folder=self.folder,
                        runtime=self.runtime,
                        progress_callback=_stage_progress,
                        should_cancel=self._is_cancelled,
                    )
                    self._raise_if_cancelled()
                    if logger.enabled:
                        logger.duration(
                            "ai.stage.local_staging",
                            (time.perf_counter() - stage_start) * 1000.0,
                            folder=folder_text,
                            output_dir=str(staged_input_dir),
                        )
                    if commands and commands[0][0] == "extract":
                        commands[0][3][commands[0][3].index("--input-dir") + 1] = str(staged_input_dir)
                elif commands and commands[0][0] == "extract":
                    commands[0][3][commands[0][3].index("--input-dir") + 1] = str(self.folder)

                for stage_index, (stage_name, stage_message, script_relative_path, stage_args) in enumerate(
                    commands,
                    start=(2 if use_local_stage else 1),
                ):
                    self._raise_if_cancelled()
                    self.signals.stage.emit(folder_text, stage_index, total_stages, stage_message)
                    self._emit_detail(folder_text, f"Starting {stage_message}.")
                    command = _resolve_stage_command(
                        self.runtime,
                        script_relative_path=script_relative_path,
                        stage_args=stage_args,
                    )
                    _log_line(log_handle)
                    _log_line(log_handle, f"[stage {stage_index}/{total_stages}] {stage_name}: {stage_message}")
                    _log_line(log_handle, f"cwd: {self.runtime.engine_root}")
                    _log_line(log_handle, f"command: {_format_command_for_log(command)}")
                    if logger.enabled:
                        logger.log(
                            "ai.stage.command_start",
                            folder=folder_text,
                            stage=stage_name,
                            stage_index=stage_index,
                            stage_total=total_stages,
                            script=script_relative_path,
                            command_executable=Path(command[0]).name if command else "",
                        )
                    stage_start = time.perf_counter() if logger.enabled else 0.0
                    completed = _run_command_with_live_output(
                        command,
                        cwd=self.runtime.engine_root,
                        progress_callback=(
                            lambda line, *, _folder_text=folder_text: _emit_tqdm_progress(
                                signals=self.signals,
                                folder_text=_folder_text,
                                line=line,
                            )
                        ),
                        output_callback=lambda chunk: _log_chunk(log_handle, chunk),
                        detail_callback=(
                            (lambda message, *, _folder_text=folder_text: self._emit_detail(_folder_text, message))
                            if self.detailed_progress
                            else None
                        ),
                        process_started_callback=self._set_current_process,
                        process_finished_callback=lambda _process: self._set_current_process(None),
                        log_context={
                            "folder": folder_text,
                            "stage": stage_name,
                            "stage_index": stage_index,
                            "stage_total": total_stages,
                            "script": script_relative_path,
                        },
                    )
                    if self._cancel_requested:
                        _log_line(log_handle)
                        _log_line(log_handle, "AI culling stopped by user.")
                        if logger.enabled:
                            logger.duration(
                                "ai.task.cancelled",
                                (time.perf_counter() - task_start) * 1000.0,
                                folder=folder_text,
                                phase=stage_name,
                                log_path=str(log_path),
                            )
                        self.signals.cancelled.emit(folder_text, "AI review stopped.")
                        return
                    if logger.enabled:
                        logger.duration(
                            "ai.stage.command",
                            (time.perf_counter() - stage_start) * 1000.0,
                            folder=folder_text,
                            stage=stage_name,
                            stage_index=stage_index,
                            stage_total=total_stages,
                            return_code=completed.returncode,
                            stdout_bytes=len(completed.stdout or ""),
                            stderr_bytes=len(completed.stderr or ""),
                        )
                        logger.log(
                            "ai.stage.artifacts",
                            folder=folder_text,
                            stage=stage_name,
                            stage_index=stage_index,
                            stage_total=total_stages,
                            **_artifact_snapshot(self.paths),
                        )
                    _log_line(log_handle)
                    _log_line(log_handle, f"return code: {completed.returncode}")
                    if completed.returncode != 0:
                        stderr = (completed.stderr or "").strip()
                        stdout = (completed.stdout or "").strip()
                        self.signals.failed.emit(
                            folder_text,
                            _build_stage_failure_message(
                                runtime=self.runtime,
                                stage_message=stage_message,
                                stderr=stderr,
                                stdout=stdout,
                                log_path=log_path,
                            ),
                        )
                        if logger.enabled:
                            logger.duration(
                                "ai.task.failed",
                                (time.perf_counter() - task_start) * 1000.0,
                                folder=folder_text,
                                phase=stage_name,
                                return_code=completed.returncode,
                                log_path=str(log_path),
                            )
                        return
                    if staged_input_dir is not None and stage_name == "extract":
                        rewrite_extraction_artifact_paths(
                            artifacts_dir=self.paths.artifacts_dir,
                            source_folder=self.folder,
                        )

                _log_line(log_handle)
                _log_line(log_handle, "AI culling completed successfully.")
                self._emit_detail(folder_text, "AI Review completed successfully.")
                if logger.enabled:
                    logger.duration(
                        "ai.task.finished",
                        (time.perf_counter() - task_start) * 1000.0,
                        folder=folder_text,
                        stages=total_stages,
                        report_dir=str(self.paths.report_dir),
                        log_path=str(log_path),
                    )
                self.signals.finished.emit(
                    folder_text,
                    str(self.paths.report_dir),
                    str(self.paths.html_report_path),
                )
        except AIRunCancelled:
            try:
                with log_path.open("a", encoding="utf-8", newline="") as log_handle:
                    _log_line(log_handle)
                    _log_line(log_handle, "AI culling stopped by user.")
            except OSError:
                pass
            if logger.enabled:
                logger.duration(
                    "ai.task.cancelled",
                    (time.perf_counter() - task_start) * 1000.0,
                    folder=folder_text,
                    phase="cancelled",
                    log_path=str(log_path),
                )
            self.signals.cancelled.emit(folder_text, "AI review stopped.")
        except Exception as exc:
            stack_text = traceback.format_exc().rstrip()
            try:
                with log_path.open("a", encoding="utf-8", newline="") as log_handle:
                    _log_line(log_handle)
                    _log_line(log_handle, "Unexpected AI culling error.")
                    _log_line(log_handle, stack_text)
            except OSError:
                pass
            if logger.enabled:
                logger.duration(
                    "ai.task.failed",
                    (time.perf_counter() - task_start) * 1000.0,
                    folder=folder_text,
                    phase="unexpected",
                    error=str(exc),
                    log_path=str(log_path),
                )
            self.signals.failed.emit(
                folder_text,
                _append_log_path(f"Unexpected AI culling failure.\n\n{exc}", log_path),
            )


def default_ai_workflow_runtime() -> AIWorkflowRuntime:
    """Resolve the default engine, Python, model, and checkpoint runtime paths."""
    workspace_root = Path(__file__).resolve().parents[1]
    runtime_root = _application_runtime_root(workspace_root)
    bundled_engine_root = runtime_root / AI_RUNTIME_DIR_NAME / "AICullingPipeline"
    adjacent_engine_root = runtime_root / "AICullingPipeline"
    engine_root = _first_existing_path(
        [
            os.environ.get("AICULLING_ENGINE_ROOT", ""),
            str(bundled_engine_root),
            str(adjacent_engine_root),
            str(workspace_root / "AICullingPipeline"),
        ]
    )
    python_executable = _first_existing_path(
        [
            os.environ.get("AICULLING_PYTHON", ""),
            str(runtime_root / AI_RUNNER_TARGET_NAME),
            sys.executable,
        ]
    )
    cache_root = _default_user_cache_root() / "image_triage_ai_cache"
    checkpoint_cache_path = cache_root / "checkpoints" / "best_ranker.pt"
    checkpoint_path = _first_existing_path(
        [
            os.environ.get("AICULLING_CHECKPOINT", ""),
            str(Path(engine_root) / DEFAULT_BUNDLED_CHECKPOINT_RELATIVE_PATH),
            str(Path(engine_root) / LEGACY_BUNDLED_CHECKPOINT_RELATIVE_PATH),
            str(checkpoint_cache_path),
        ]
    )
    checkpoint_download_url = (os.environ.get("AICULLING_CHECKPOINT_URL", "") or "").strip() or None
    model_name_override = (os.environ.get("AICULLING_MODEL_NAME", "") or "").strip()
    model_installation = None if model_name_override else resolve_ai_model_installation()
    model_name = model_name_override or (
        model_installation.model_name if model_installation is not None else ""
    )
    batch_size = _positive_int_env("AICULLING_BATCH_SIZE", 16)
    num_workers = _nonnegative_int_env("AICULLING_NUM_WORKERS", _default_ai_dataloader_workers())
    local_stage_mode = (os.environ.get("AICULLING_LOCAL_STAGE_MODE", "auto") or "auto").strip().lower()
    local_stage_root = Path(
        os.environ.get(
            "AICULLING_LOCAL_STAGE_ROOT",
            str(cache_root / "stage"),
        )
    )
    semantic_sidecar_enabled = _bool_env("AICULLING_SEMANTIC_SIDECAR", False)
    semantic_model_override = (os.environ.get("AICULLING_SEMANTIC_MODEL_NAME", "") or "").strip()
    semantic_installation = resolve_semantic_model_installation()
    semantic_model_name = semantic_model_override or (
        semantic_installation.model_name if semantic_installation.is_installed else DEFAULT_SEMANTIC_MODEL_REPO_ID
    )
    semantic_batch_size = _positive_int_env("AICULLING_SEMANTIC_BATCH_SIZE", 16)
    runtime_status = load_ai_runtime_installation_status()
    if "gpu" in runtime_status.installed_variants:
        device = "cuda"
    elif runtime_status.installed_variants == ("cpu",):
        device = "cpu"
    else:
        device = "auto"

    engine_root_path = Path(engine_root).expanduser().resolve()
    python_path = Path(python_executable).expanduser().resolve() if python_executable else None
    return AIWorkflowRuntime(
        engine_root=engine_root_path,
        python_executable=python_path,
        model_name=model_name,
        checkpoint_path=Path(checkpoint_path).expanduser().resolve(),
        extraction_config_path=engine_root_path / "configs" / "extract_embeddings.json",
        clustering_config_path=engine_root_path / "configs" / "cluster_embeddings.json",
        report_config_path=engine_root_path / "configs" / "export_ranked_report.json",
        semantic_config_path=engine_root_path / "configs" / "semantic_classification.json",
        model_installation=model_installation,
        checkpoint_download_url=checkpoint_download_url,
        device=device,
        batch_size=batch_size,
        num_workers=num_workers,
        local_stage_mode=local_stage_mode,
        local_stage_root=local_stage_root.expanduser().resolve(),
        semantic_sidecar_enabled=semantic_sidecar_enabled,
        semantic_model_name=semantic_model_name,
        semantic_batch_size=semantic_batch_size,
    )


def _default_ai_dataloader_workers() -> int:
    if os.name == "nt":
        return 0
    cpu_count = os.cpu_count() or 4
    return max(2, min(8, max(1, cpu_count // 2)))


def _positive_int_env(name: str, default: int) -> int:
    raw_value = (os.environ.get(name, "") or "").strip()
    if not raw_value:
        return default
    try:
        parsed = int(raw_value)
    except ValueError:
        return default
    return parsed if parsed > 0 else default


def _nonnegative_int_env(name: str, default: int) -> int:
    raw_value = (os.environ.get(name, "") or "").strip()
    if not raw_value:
        return default
    try:
        parsed = int(raw_value)
    except ValueError:
        return default
    return parsed if parsed >= 0 else default


def _bool_env(name: str, default: bool) -> bool:
    raw_value = (os.environ.get(name, "") or "").strip().casefold()
    if not raw_value:
        return default
    if raw_value in {"1", "true", "yes", "on"}:
        return True
    if raw_value in {"0", "false", "no", "off"}:
        return False
    return default


def build_ai_workflow_paths(folder: str | Path) -> AIWorkflowPaths:
    """Resolve the hidden AI artifact/report layout for a real folder."""
    folder_path = Path(folder).expanduser().resolve()
    hidden_root = folder_path / HIDDEN_ROOT_NAME
    artifacts_dir = hidden_root / ARTIFACTS_DIR_NAME
    report_dir = hidden_root / REPORT_DIR_NAME
    return AIWorkflowPaths(
        folder=folder_path,
        hidden_root=hidden_root,
        artifacts_dir=artifacts_dir,
        report_dir=report_dir,
        ranked_export_path=report_dir / "ranked_clusters_export.csv",
        html_report_path=report_dir / "ranked_clusters_report.html",
        semantic_export_path=report_dir / "semantic_classifications.csv",
        semantic_summary_path=report_dir / "semantic_classification_summary.json",
    )


def ai_run_log_path(paths: AIWorkflowPaths) -> Path:
    """Return the stable log file path for the latest AI run in a folder."""
    return paths.hidden_root / LOGS_DIR_NAME / LATEST_AI_RUN_LOG_FILENAME


def prepare_hidden_ai_workspace(folder: str | Path) -> AIWorkflowPaths:
    """Create the hidden AI artifact/report directories for a folder if needed."""
    paths = build_ai_workflow_paths(folder)
    paths.hidden_root.mkdir(parents=True, exist_ok=True)
    paths.artifacts_dir.mkdir(parents=True, exist_ok=True)
    paths.report_dir.mkdir(parents=True, exist_ok=True)
    _mark_hidden(paths.hidden_root)
    return paths


def existing_hidden_ai_report_dir(folder: str | Path) -> Path | None:
    """Return the report directory only when a ranked export already exists."""
    paths = build_ai_workflow_paths(folder)
    if paths.ranked_export_path.exists():
        return paths.report_dir
    return None


def reset_hidden_ai_review_cache(
    folder_or_paths: str | Path | AIWorkflowPaths,
    *,
    clear_logs: bool = False,
) -> AIWorkflowPaths:
    """Delete folder-local AI review outputs without touching labels or training data."""

    paths = folder_or_paths if isinstance(folder_or_paths, AIWorkflowPaths) else build_ai_workflow_paths(folder_or_paths)
    _remove_tree_if_present(paths.artifacts_dir)
    _remove_tree_if_present(paths.report_dir)
    if clear_logs:
        log_path = ai_run_log_path(paths)
        _remove_path_if_present(log_path)
        logs_dir = log_path.parent
        if logs_dir.exists() and not any(logs_dir.iterdir()):
            logs_dir.rmdir()
    if paths.hidden_root.exists():
        _mark_hidden(paths.hidden_root)
    return paths


def build_ai_stage_cache_keys(
    records: list[ImageRecord] | tuple[ImageRecord, ...],
    runtime: AIWorkflowRuntime,
    *,
    labels_dir: Path | None = None,
    reference_bank_path: Path | None = None,
) -> AIStageCacheKeys:
    """Build stage-specific cache keys from inputs, configs, labels, and checkpoint."""
    embedding_payload = {
        "records": _records_signature(records),
        "model_name": runtime.model_name,
        "extraction_config": _path_signature(runtime.extraction_config_path),
    }
    embedding_cache_key = _hash_payload(embedding_payload)
    cluster_payload = {
        "embedding_cache_key": embedding_cache_key,
        "clustering_config": _path_signature(runtime.clustering_config_path),
    }
    cluster_cache_key = _hash_payload(cluster_payload)
    report_payload = {
        "cluster_cache_key": cluster_cache_key,
        "report_config": _path_signature(runtime.report_config_path),
        "checkpoint": _path_signature(runtime.checkpoint_path),
        "labels_dir": _path_signature(labels_dir),
        "reference_bank": _path_signature(reference_bank_path),
        "semantic_sidecar_enabled": runtime.semantic_sidecar_enabled,
    }
    if runtime.semantic_sidecar_enabled:
        report_payload["semantic_model_name"] = runtime.semantic_model_name
        report_payload["semantic_config"] = _path_signature(runtime.semantic_config_path)
        report_payload["semantic_batch_size"] = runtime.semantic_batch_size
    report_cache_key = _hash_payload(report_payload)
    semantic_cache_key = ""
    if runtime.semantic_sidecar_enabled:
        semantic_cache_key = _hash_payload(
            {
                "report_cache_key": report_cache_key,
                "semantic_model_name": runtime.semantic_model_name,
                "semantic_config": _path_signature(runtime.semantic_config_path),
            }
        )
    return AIStageCacheKeys(
        embedding_cache_key=embedding_cache_key,
        cluster_cache_key=cluster_cache_key,
        report_cache_key=report_cache_key,
        semantic_cache_key=semantic_cache_key,
    )


def ai_embedding_artifacts_ready(paths: AIWorkflowPaths) -> bool:
    """Return whether embedding-stage artifacts already exist for a folder."""
    return all(
        (
            (paths.artifacts_dir / "images.csv").exists(),
            (paths.artifacts_dir / "embeddings.npy").exists(),
            (paths.artifacts_dir / "image_ids.json").exists(),
        )
    )


def ai_cluster_artifacts_ready(paths: AIWorkflowPaths) -> bool:
    """Return whether clustering outputs exist on top of embedding artifacts."""
    return ai_embedding_artifacts_ready(paths) and (paths.artifacts_dir / "clusters.csv").exists()


def ai_report_artifacts_ready(paths: AIWorkflowPaths) -> bool:
    """Return whether the ranked AI report export exists for a folder."""
    return paths.ranked_export_path.exists()


def ai_semantic_artifacts_ready(paths: AIWorkflowPaths) -> bool:
    """Return whether the semantic sidecar export exists for a folder."""
    return paths.semantic_export_path.exists() and paths.semantic_summary_path.exists()


def _path_size_or_zero(path: Path) -> int:
    try:
        return path.stat().st_size if path.exists() else 0
    except OSError:
        return 0


def _artifact_snapshot(paths: AIWorkflowPaths) -> dict[str, object]:
    embeddings_path = paths.artifacts_dir / "embeddings.npy"
    images_path = paths.artifacts_dir / "images.csv"
    clusters_path = paths.artifacts_dir / "clusters.csv"
    image_ids_path = paths.artifacts_dir / "image_ids.json"
    return {
        "images_csv": images_path.exists(),
        "images_csv_bytes": _path_size_or_zero(images_path),
        "embeddings": embeddings_path.exists(),
        "embeddings_bytes": _path_size_or_zero(embeddings_path),
        "image_ids": image_ids_path.exists(),
        "image_ids_bytes": _path_size_or_zero(image_ids_path),
        "clusters": clusters_path.exists(),
        "clusters_bytes": _path_size_or_zero(clusters_path),
        "ranked_export": paths.ranked_export_path.exists(),
        "ranked_export_bytes": _path_size_or_zero(paths.ranked_export_path),
        "html_report": paths.html_report_path.exists(),
        "html_report_bytes": _path_size_or_zero(paths.html_report_path),
        "semantic_export": paths.semantic_export_path.exists(),
        "semantic_export_bytes": _path_size_or_zero(paths.semantic_export_path),
        "semantic_summary": paths.semantic_summary_path.exists(),
        "semantic_summary_bytes": _path_size_or_zero(paths.semantic_summary_path),
    }


def _remove_tree_if_present(path: Path) -> None:
    if not path.exists():
        return
    if path.is_file() or path.is_symlink():
        path.unlink()
        return
    shutil.rmtree(path)


def _remove_path_if_present(path: Path) -> None:
    if not path.exists():
        return
    if path.is_dir():
        shutil.rmtree(path)
        return
    path.unlink()


def stage_supported_images(
    *,
    source_folder: Path,
    runtime: AIWorkflowRuntime,
    progress_callback: Callable[[int, int, str, str], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> Path:
    """Copy supported images into a local staging workspace for faster AI processing."""
    def raise_if_cancelled() -> None:
        if should_cancel is not None and should_cancel():
            raise AIRunCancelled("AI review stopped while staging images.")

    logger = perf_logger()
    total_start = time.perf_counter() if logger.enabled else 0.0
    step_start = total_start
    stage_root, stage_root_message = _ensure_stage_root(runtime.local_stage_root)
    raise_if_cancelled()
    if stage_root_message and progress_callback is not None:
        progress_callback(0, 0, "", stage_root_message)
    workspace_dir = _stage_workspace_dir(stage_root, source_folder)
    input_dir = workspace_dir / STAGE_INPUT_DIR_NAME
    manifest_path = workspace_dir / STAGE_MANIFEST_FILENAME
    input_dir.mkdir(parents=True, exist_ok=True)
    supported_extensions = load_supported_extensions(runtime.extraction_config_path)
    source_folder = source_folder.resolve()
    previous_entries = _load_stage_manifest(manifest_path, source_folder)
    raise_if_cancelled()
    if logger.enabled:
        logger.duration(
            "ai.staging.setup",
            (time.perf_counter() - step_start) * 1000.0,
            folder=str(source_folder),
            stage_root=str(stage_root),
            workspace_dir=str(workspace_dir),
            previous_entries=len(previous_entries),
            extensions=len(supported_extensions),
        )
        step_start = time.perf_counter()
    candidate_paths: list[Path] = []
    for path in source_folder.rglob("*"):
        raise_if_cancelled()
        if path.is_file() and path.suffix.lower() in supported_extensions:
            candidate_paths.append(path)
    candidate_paths.sort(key=lambda item: item.relative_to(source_folder).as_posix().casefold())
    if logger.enabled:
        logger.duration(
            "ai.staging.scan",
            (time.perf_counter() - step_start) * 1000.0,
            folder=str(source_folder),
            candidates=len(candidate_paths),
        )
        step_start = time.perf_counter()

    total = len(candidate_paths)
    start_time = time.monotonic()
    copied_count = 0
    reused_count = 0
    copied_bytes = 0
    reused_bytes = 0
    current_entries: dict[str, dict[str, int]] = {}
    if progress_callback is not None:
        progress_callback(0, total, "", f"Staging images locally (0/{total})")

    for index, path in enumerate(candidate_paths, start=1):
        raise_if_cancelled()
        relative_path = path.relative_to(source_folder)
        relative_key = relative_path.as_posix()
        stat_result = path.stat()
        signature = {
            "size": int(stat_result.st_size),
            "modified_ns": int(getattr(stat_result, "st_mtime_ns", int(stat_result.st_mtime * 1_000_000_000))),
        }
        current_entries[relative_key] = signature
        destination = input_dir / relative_path
        cached_signature = previous_entries.get(relative_key)
        if destination.exists() and cached_signature == signature:
            reused_count += 1
            reused_bytes += signature["size"]
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            temp_destination = destination.with_name(destination.name + ".partial")
            if temp_destination.exists():
                temp_destination.unlink(missing_ok=True)
            shutil.copy2(path, temp_destination)
            raise_if_cancelled()
            temp_destination.replace(destination)
            copied_count += 1
            copied_bytes += signature["size"]
        if progress_callback is not None and (index == total or index == 1 or index % 100 == 0):
            progress_callback(
                index,
                total,
                _estimate_eta_text(start_time=start_time, completed=index, total=total),
                f"Staging images locally ({index}/{total}, {reused_count} cached)",
            )

    stale_entries = set(previous_entries) - set(current_entries)
    stale_removed = 0
    for relative_key in stale_entries:
        raise_if_cancelled()
        stale_path = input_dir / Path(relative_key)
        if stale_path.exists():
            stale_path.unlink(missing_ok=True)
            stale_removed += 1
    _prune_empty_stage_directories(input_dir)
    _write_stage_manifest(
        manifest_path=manifest_path,
        source_folder=source_folder,
        entries=current_entries,
    )
    if progress_callback is not None:
        progress_callback(
            total,
            total,
            "",
            f"Staging ready ({copied_count} copied, {reused_count} cached)",
        )
    if logger.enabled:
        logger.duration(
            "ai.staging.finished",
            (time.perf_counter() - total_start) * 1000.0,
            folder=str(source_folder),
            input_dir=str(input_dir),
            total=total,
            copied=copied_count,
            reused=reused_count,
            stale_removed=stale_removed,
            copied_bytes=copied_bytes,
            reused_bytes=reused_bytes,
        )
    return input_dir


def rewrite_extraction_artifact_paths(
    *,
    artifacts_dir: Path,
    source_folder: Path,
) -> None:
    metadata_path = artifacts_dir / "images.csv"
    if metadata_path.exists():
        with metadata_path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            fieldnames = list(reader.fieldnames or [])
            rows = list(reader)

        if fieldnames:
            for row in rows:
                relative_path = (row.get("relative_path") or "").strip()
                if not relative_path:
                    continue
                row["file_path"] = str((source_folder / Path(relative_path)).resolve())

            with metadata_path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(rows)

    resolved_config_path = artifacts_dir / "resolved_config.json"
    if resolved_config_path.exists():
        try:
            payload = json.loads(resolved_config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = None
        if isinstance(payload, dict):
            payload["input_dir"] = str(source_folder.resolve())
            payload["local_stage_mode"] = "used"
            resolved_config_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def load_supported_extensions(config_path: Path) -> tuple[str, ...]:
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return DEFAULT_STAGE_EXTENSIONS

    configured = payload.get("supported_extensions")
    if not isinstance(configured, list):
        return DEFAULT_STAGE_EXTENSIONS
    cleaned = tuple(str(ext).strip().lower() for ext in configured if str(ext).strip())
    return cleaned or DEFAULT_STAGE_EXTENSIONS


def _default_stage_root() -> Path:
    local_appdata_value = os.environ.get("LOCALAPPDATA")
    local_appdata = Path(local_appdata_value) if local_appdata_value else Path.home() / "AppData" / "Local"
    return local_appdata / "image_triage_ai_cache" / "stage"


def _ensure_stage_root(preferred_root: Path | None) -> tuple[Path, str]:
    fallback_root = _default_stage_root()
    temp_root = Path(tempfile.gettempdir()) / "image_triage_ai_cache" / "stage"

    candidates: list[Path] = []
    for candidate in (preferred_root, fallback_root, temp_root):
        if candidate is None:
            continue
        if candidate not in candidates:
            candidates.append(candidate)

    errors: list[str] = []
    for index, candidate in enumerate(candidates):
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            if index == 0:
                return candidate, ""
            preferred_text = str(preferred_root) if preferred_root is not None else "configured scratch path"
            return candidate, f"Scratch path unavailable ({preferred_text}); using {candidate} instead"
        except OSError as exc:
            errors.append(f"{candidate}: {exc}")

    raise OSError("Could not create any AI staging cache directory.\n" + "\n".join(errors))


def _stage_workspace_dir(stage_root: Path, source_folder: Path) -> Path:
    workspace_key = sha1(str(source_folder.resolve()).encode("utf-8"), usedforsecurity=False).hexdigest()
    workspace_dir = stage_root / STAGE_WORKSPACES_DIR_NAME / workspace_key
    workspace_dir.mkdir(parents=True, exist_ok=True)
    return workspace_dir


def _load_stage_manifest(manifest_path: Path, source_folder: Path) -> dict[str, dict[str, int]]:
    if not manifest_path.exists():
        return {}
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    if payload.get("source_folder") != str(source_folder):
        return {}
    entries = payload.get("entries")
    if not isinstance(entries, dict):
        return {}
    cleaned: dict[str, dict[str, int]] = {}
    for relative_key, signature in entries.items():
        if not isinstance(relative_key, str) or not isinstance(signature, dict):
            continue
        try:
            cleaned[relative_key] = {
                "size": int(signature.get("size", 0)),
                "modified_ns": int(signature.get("modified_ns", 0)),
            }
        except (TypeError, ValueError):
            continue
    return cleaned


def _write_stage_manifest(
    *,
    manifest_path: Path,
    source_folder: Path,
    entries: dict[str, dict[str, int]],
) -> None:
    payload = {
        "source_folder": str(source_folder),
        "updated_at": int(time.time()),
        "entries": entries,
    }
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _prune_empty_stage_directories(root: Path) -> None:
    for directory in sorted((path for path in root.rglob("*") if path.is_dir()), key=lambda path: len(path.parts), reverse=True):
        try:
            directory.rmdir()
        except OSError:
            continue


def _should_use_local_staging(folder: Path, runtime: AIWorkflowRuntime) -> bool:
    if runtime.local_stage_mode == "off":
        return False
    if runtime.local_stage_mode == "always":
        return True
    if os.name != "nt":
        return True
    drive_type = _get_drive_type(folder)
    return drive_type in {DRIVE_REMOTE, DRIVE_REMOVABLE}


def _first_existing_path(candidates: list[str]) -> str:
    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate).expanduser()
        if path.exists():
            return str(path.resolve())
    return candidates[-1] if candidates else ""


def _application_runtime_root(workspace_root: Path) -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return workspace_root


def _default_user_cache_root() -> Path:
    if os.name == "nt":
        local_appdata = os.environ.get("LOCALAPPDATA")
        return Path(local_appdata) if local_appdata else Path.home() / "AppData" / "Local"
    xdg_cache_home = os.environ.get("XDG_CACHE_HOME")
    return Path(xdg_cache_home) if xdg_cache_home else Path.home() / ".cache"


def _download_asset(source: str, destination: Path) -> None:
    source_text = source.strip()
    if not source_text:
        raise ValueError("download source is empty")

    destination.parent.mkdir(parents=True, exist_ok=True)
    source_path = Path(source_text).expanduser()
    if source_path.exists():
        shutil.copy2(source_path, destination)
        return

    temp_destination = destination.with_suffix(destination.suffix + ".download")
    if temp_destination.exists():
        temp_destination.unlink(missing_ok=True)

    with urllib.request.urlopen(source_text) as response, temp_destination.open("wb") as handle:
        shutil.copyfileobj(response, handle)
    temp_destination.replace(destination)


def _resolve_stage_command(
    runtime: AIWorkflowRuntime,
    *,
    script_relative_path: str,
    stage_args: list[str],
) -> list[str]:
    script_path = (runtime.engine_root / script_relative_path).resolve()
    stage_executable = script_path.with_suffix(".exe")
    if stage_executable.exists():
        return [str(stage_executable), *stage_args]
    runtime_root = _runtime_root_from_engine_root(runtime.engine_root)
    root_stage_executable = runtime_root / stage_executable.name
    if root_stage_executable.exists():
        return [str(root_stage_executable), *stage_args]
    runner_script_path = _runtime_runner_script(runtime_root)
    if runner_script_path is not None:
        if runtime.python_executable is None:
            raise FileNotFoundError(f"No Python runner configured for AI tool: {script_path}")
        if not runtime.python_executable.exists():
            raise FileNotFoundError(f"Python runner does not exist: {runtime.python_executable}")
        return [str(runtime.python_executable), str(runner_script_path), str(script_path), *stage_args]
    if runtime.python_executable is None:
        raise FileNotFoundError(f"No Python runner configured for AI tool: {script_path}")
    if not runtime.python_executable.exists():
        raise FileNotFoundError(f"Python runner does not exist: {runtime.python_executable}")
    return [str(runtime.python_executable), str(script_path), *stage_args]


def _runtime_root_from_engine_root(engine_root: Path) -> Path:
    if engine_root.name == "AICullingPipeline" and engine_root.parent.name == AI_RUNTIME_DIR_NAME:
        return engine_root.parent.parent
    return engine_root.parent


def _runtime_runner_script(runtime_root: Path) -> Path | None:
    candidate = runtime_root / AI_RUNNER_SCRIPT_RELATIVE_PATH
    if candidate.exists():
        return candidate.resolve()
    return None


def _build_stage_failure_message(
    *,
    runtime: AIWorkflowRuntime,
    stage_message: str,
    stderr: str,
    stdout: str,
    log_path: Path | None = None,
) -> str:
    combined_output = "\n".join(part for part in (stderr, stdout) if part).strip()
    missing_module_match = MISSING_MODULE_PATTERN.search(combined_output)
    if missing_module_match is not None:
        module_name = missing_module_match.group("module")
        runtime_root = _runtime_root_from_engine_root(runtime.engine_root)
        runtime_status = load_ai_runtime_installation_status()
        staged_site_packages = [
            path
            for path in (
                *(profile.site_packages_dir for profile in runtime_status.profiles.values()),
                runtime_root / "ai_site_packages",
                runtime_root / "build_assets" / "ai_site_packages",
            )
            if path.exists()
        ]
        message_lines = [
            f"{stage_message} failed.",
            f"The AI runtime could not import the Python module '{module_name}'.",
            f"Python runner: {runtime.python_executable}",
            f"Engine root: {runtime.engine_root}",
        ]
        message_lines.append(f"Runtime cache root: {runtime_status.directories.root}")
        if staged_site_packages:
            message_lines.append("AI package search roots:")
            message_lines.extend(str(path) for path in staged_site_packages)
        else:
            message_lines.append(
                "Expected AI runtime packages either in the user cache or next to the app executable."
            )
        message_lines.append(
            "Install the AI runtime from the app, refresh the staged runtime assets, or set AICULLING_PYTHON to a Python environment that already has the missing dependency installed."
        )
        if log_path is not None:
            message_lines.append(f"AI run log: {log_path}")
        return "\n".join(message_lines)

    error_parts = [stage_message + " failed."]
    output_excerpt = _summarize_failure_output(combined_output)
    if output_excerpt:
        error_parts.append(output_excerpt)
    if log_path is not None:
        error_parts.append(f"AI run log: {log_path}")
    return "\n\n".join(error_parts)


def _append_log_path(message: str, log_path: Path | None) -> str:
    if log_path is None:
        return message
    return f"{message}\n\nAI run log: {log_path}"


def _summarize_failure_output(output_text: str) -> str:
    if not output_text:
        return ""
    lines = output_text.splitlines()
    if len(lines) <= FAILURE_OUTPUT_TAIL_LINE_COUNT:
        return output_text
    excerpt = "\n".join(lines[-FAILURE_OUTPUT_TAIL_LINE_COUNT:])
    return f"Showing last {FAILURE_OUTPUT_TAIL_LINE_COUNT} output lines:\n{excerpt}"


def _format_command_for_log(command: list[str]) -> str:
    if os.name == "nt":
        return subprocess.list2cmdline(command)
    return " ".join(shlex.quote(part) for part in command)


def _mark_hidden(path: Path) -> None:
    if os.name != "nt":
        return
    try:
        attrs = ctypes.windll.kernel32.GetFileAttributesW(str(path))
        if attrs == -1:
            return
        if attrs & FILE_ATTRIBUTE_HIDDEN:
            return
        ctypes.windll.kernel32.SetFileAttributesW(str(path), attrs | FILE_ATTRIBUTE_HIDDEN)
    except Exception:
        return


def _get_drive_type(path: Path) -> int:
    if os.name != "nt":
        return DRIVE_UNKNOWN
    anchor = path.anchor or str(path)
    try:
        return int(ctypes.windll.kernel32.GetDriveTypeW(anchor))
    except Exception:
        return DRIVE_UNKNOWN


def _run_command_with_live_output(
    command: list[str],
    *,
    cwd: Path,
    progress_callback: Callable[[str], None] | None = None,
    output_callback: Callable[[str], None] | None = None,
    detail_callback: Callable[[str], None] | None = None,
    process_started_callback: Callable[[subprocess.Popen[str]], None] | None = None,
    process_finished_callback: Callable[[subprocess.Popen[str]], None] | None = None,
    log_context: dict[str, object] | None = None,
) -> subprocess.CompletedProcess[str]:
    logger = perf_logger()
    total_start = time.perf_counter() if logger.enabled else 0.0
    context = dict(log_context or {})
    if logger.enabled:
        logger.log(
            "ai.command.start",
            cwd=str(cwd),
            executable=Path(command[0]).name if command else "",
            args=len(command),
            **context,
        )
    env = dict(os.environ)
    env["PYTHONUNBUFFERED"] = "1"
    env.setdefault("IMAGE_TRIAGE_HOST_ROOT", str(Path(__file__).resolve().parents[1]))
    env[AI_METRICS_ENV_VAR] = "1" if logger.enabled or detail_callback is not None else "0"
    spawn_start = time.perf_counter() if logger.enabled else 0.0
    try:
        process = subprocess.Popen(
            command,
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=env,
        )
    except Exception as exc:
        if logger.enabled:
            logger.duration(
                "ai.command.spawn_failed",
                (time.perf_counter() - spawn_start) * 1000.0,
                error=str(exc),
                **context,
            )
        raise
    if logger.enabled:
        logger.duration(
            "ai.command.spawn",
            (time.perf_counter() - spawn_start) * 1000.0,
            pid=process.pid,
            **context,
        )
    if process_started_callback is not None:
        process_started_callback(process)
    if detail_callback is not None:
        detail_callback(f"Started Python worker process {process.pid}.")

    chunks: list[str] = []
    buffer = ""
    output_chunks = 0
    progress_lines = 0
    first_output_logged = False
    assert process.stdout is not None
    try:
        for chunk in iter(process.stdout.readline, ""):
            if chunk == "" and process.poll() is not None:
                break
            if not chunk:
                continue
            output_chunks += 1
            if logger.enabled and not first_output_logged:
                first_output_logged = True
                logger.duration(
                    "ai.command.first_output",
                    (time.perf_counter() - total_start) * 1000.0,
                    pid=process.pid,
                    **context,
                )
            if output_callback is not None:
                output_callback(chunk)
            chunks.append(chunk)
            normalized = chunk.replace("\r", "\n")
            segments = normalized.split("\n")
            for index, segment in enumerate(segments):
                is_terminated = index < len(segments) - 1
                if is_terminated:
                    line = f"{buffer}{segment}".strip()
                    if line:
                        metric_payload = _parse_ai_metric_line(line)
                        if metric_payload is not None:
                            _log_ai_metric_payload(metric_payload, context=context)
                            if detail_callback is not None:
                                detail_text = _format_ai_metric_detail(metric_payload, context=context)
                                if detail_text:
                                    detail_callback(detail_text)
                            buffer = ""
                            continue
                        if progress_callback is not None:
                            progress_lines += 1
                            progress_callback(line)
                        if detail_callback is not None:
                            detail_text = _format_command_output_detail(line)
                            if detail_text:
                                detail_callback(detail_text)
                    buffer = ""
                else:
                    buffer += segment
    finally:
        try:
            process.stdout.close()
        except Exception:
            pass

    trailing_line = buffer.strip()
    if trailing_line:
        metric_payload = _parse_ai_metric_line(trailing_line)
        if metric_payload is not None:
            _log_ai_metric_payload(metric_payload, context=context)
            if detail_callback is not None:
                detail_text = _format_ai_metric_detail(metric_payload, context=context)
                if detail_text:
                    detail_callback(detail_text)
        else:
            if progress_callback is not None:
                progress_lines += 1
                progress_callback(trailing_line)
            if detail_callback is not None:
                detail_text = _format_command_output_detail(trailing_line)
                if detail_text:
                    detail_callback(detail_text)

    return_code = process.wait()
    if process_finished_callback is not None:
        process_finished_callback(process)
    stdout = "".join(chunks)
    if logger.enabled:
        logger.duration(
            "ai.command.total",
            (time.perf_counter() - total_start) * 1000.0,
            pid=process.pid,
            return_code=return_code,
            stdout_bytes=len(stdout),
            output_chunks=output_chunks,
            progress_lines=progress_lines,
            first_output=first_output_logged,
            **context,
        )
    return subprocess.CompletedProcess(
        args=command,
        returncode=return_code,
        stdout=stdout,
        stderr="",
    )


def _log_ai_metric_line(line: str, *, context: dict[str, object]) -> bool:
    payload = _parse_ai_metric_line(line)
    if payload is None:
        return False
    _log_ai_metric_payload(payload, context=context)
    return True


def _log_ai_metric_payload(payload: dict[str, object], *, context: dict[str, object]) -> None:
    logger = perf_logger()
    if not logger.enabled:
        return
    payload = dict(payload)
    event = str(payload.pop("event", "ai.script.metric") or "ai.script.metric")
    duration = payload.pop("duration_ms", None)
    fields = dict(context)
    fields.update(payload)
    if isinstance(duration, (int, float)):
        logger.duration(event, float(duration), **fields)
    else:
        logger.log(event, **fields)


def _parse_ai_metric_line(line: str) -> dict[str, object] | None:
    metric_start = line.find(AI_METRIC_PREFIX)
    if metric_start < 0:
        return None
    metric_text = line[metric_start + len(AI_METRIC_PREFIX) :].strip()
    if not metric_text:
        return None
    try:
        payload, _ = json.JSONDecoder().raw_decode(metric_text)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _format_command_output_detail(line: str) -> str:
    text = " ".join((line or "").split())
    if not text:
        return ""
    if len(text) > 220:
        text = f"{text[:217]}..."
    return text


def _format_ai_metric_detail(payload: dict[str, object], *, context: dict[str, object]) -> str:
    event = str(payload.get("event", "") or "")
    stage = str(context.get("stage", "") or "")
    duration_text = _format_metric_duration(payload.get("duration_ms"))

    if event == "ai.script.extract.start":
        return (
            f"Preparing embedding extraction: {payload.get('batch_size', '-')} image batch size, "
            f"{payload.get('num_workers', '-')} loader workers."
        )
    if event == "ai.script.extract.dependencies_start":
        return "Loading embedding extraction libraries."
    if event == "ai.script.extract.dependencies":
        return f"Loaded embedding extraction libraries{duration_text}."
    if event == "ai.script.extract.scan":
        return (
            f"Scanned image inputs: {payload.get('valid_records', 0)} readable of "
            f"{payload.get('total_records', 0)} files{duration_text}."
        )
    if event == "ai.script.extract.model_load_start":
        return f"Loading DINO embedding model on {payload.get('requested_device', 'auto')}."
    if event == "ai.script.extract.model_load":
        return (
            f"Loaded DINO model ({payload.get('backend', 'model')}, {payload.get('device', '-')}, "
            f"{payload.get('feature_dim', '-')} features){duration_text}."
        )
    if event == "ai.script.extract.iterator":
        return f"Prepared embedding data loader{duration_text}."
    if event == "ai.script.extract.first_batch_ready":
        return f"First embedding batch ready{duration_text}."
    if event == "ai.script.extract.batch":
        return (
            f"Embedded batch {payload.get('batch_index', '-')}: "
            f"{payload.get('processed', payload.get('batch_size', '-'))} image(s){duration_text}."
        )
    if event == "ai.script.extract.inference_summary":
        slow_file = str(payload.get("sample_max_file", "") or "")
        suffix = f" Slowest image: {slow_file}." if slow_file else ""
        return f"Embedding pass summary: {payload.get('samples', '-')} image(s){duration_text}.{suffix}"
    if event == "ai.script.extract.save":
        return f"Saved embedding artifacts for {payload.get('embeddings', 0)} image(s){duration_text}."
    if event == "ai.script.extract.total":
        return f"Finished embedding extraction{duration_text}."

    if event == "ai.script.semantic.start":
        return (
            f"Preparing semantic classification with {payload.get('labels', '-')} label(s), "
            f"batch size {payload.get('batch_size', '-')}."
        )
    if event == "ai.script.semantic.host_dependencies_start":
        return "Loading semantic classification host libraries."
    if event == "ai.script.semantic.host_dependencies":
        return f"Loaded semantic classification host libraries{duration_text}."
    if event == "ai.script.semantic.metadata":
        return f"Loaded semantic metadata for {payload.get('rows', 0)} image(s){duration_text}."
    if event == "ai.script.semantic.dependencies_start":
        return "Loading semantic AI libraries."
    if event == "ai.script.semantic.dependencies":
        return f"Loaded semantic AI libraries{duration_text}."
    if event == "ai.script.semantic.model_load_start":
        return f"Loading semantic model on {payload.get('requested_device', 'auto')}."
    if event == "ai.script.semantic.model_load":
        return f"Loaded semantic model ({payload.get('device', '-')}){duration_text}."
    if event == "ai.script.semantic.batch":
        return (
            f"Classified semantic batch {payload.get('batch_index', '-')}: "
            f"{payload.get('opened', 0)} image(s), {payload.get('failed', 0)} failed{duration_text}."
        )
    if event == "ai.script.semantic.write":
        return f"Saved semantic classifications for {payload.get('rows', 0)} image(s){duration_text}."
    if event == "ai.script.semantic.summary":
        return (
            f"Semantic summary: {payload.get('classified', payload.get('classified_images', '-'))} classified, "
            f"{payload.get('failed', payload.get('failed_images', '-'))} failed."
        )
    if event == "ai.script.semantic.total":
        return f"Finished semantic classification for {payload.get('rows', '-')} image(s){duration_text}."

    if duration_text:
        label = event.removeprefix("ai.script.").replace(".", " ").replace("_", " ")
        if label:
            return f"{label.title()}{duration_text}."
    if stage:
        return f"{stage}: {event}" if event else ""
    return event


def _format_metric_duration(value: object) -> str:
    if not isinstance(value, (int, float)):
        return ""
    milliseconds = max(0.0, float(value))
    if milliseconds < 1000.0:
        return f" in {milliseconds:.0f} ms"
    seconds = milliseconds / 1000.0
    if seconds < 60.0:
        return f" in {seconds:.1f}s"
    minutes = int(seconds // 60)
    remainder = seconds - minutes * 60
    return f" in {minutes}m {remainder:.0f}s"


def _terminate_process(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    try:
        process.terminate()
    except Exception:
        try:
            process.kill()
        except Exception:
            pass


def _emit_tqdm_progress(*, signals: AIRunSignals, folder_text: str, line: str) -> None:
    parsed = _parse_tqdm_progress(line)
    if parsed is None:
        return
    stage_message, current, total, eta_text = parsed
    signals.progress.emit(folder_text, stage_message, current, total, eta_text)


def _parse_tqdm_progress(line: str) -> tuple[str, int, int, str] | None:
    match = TQDM_PROGRESS_PATTERN.search(line)
    if match is None:
        return None

    current = int(match.group("current"))
    total = int(match.group("total"))
    timing = match.group("timing")
    eta_text = ""
    eta_match = re.search(r"<([^,\]]+)", timing)
    if eta_match is not None:
        candidate = eta_match.group(1).strip()
        if candidate and "?" not in candidate:
            eta_text = candidate

    return match.group("label"), current, total, eta_text


def _estimate_eta_text(*, start_time: float, completed: int, total: int) -> str:
    if completed <= 0 or total <= completed:
        return "00:00" if total > 0 else ""
    elapsed = max(0.0, time.monotonic() - start_time)
    if elapsed <= 0.0:
        return ""
    rate = completed / elapsed
    if rate <= 0.0:
        return ""
    remaining_seconds = max(0, int(round((total - completed) / rate)))
    return _format_seconds(remaining_seconds)


def _format_seconds(total_seconds: int) -> str:
    hours, remainder = divmod(max(0, total_seconds), 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


def _hash_payload(payload: dict[str, object]) -> str:
    encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return sha1(encoded, usedforsecurity=False).hexdigest()


def _records_signature(records: list[ImageRecord] | tuple[ImageRecord, ...]) -> list[dict[str, object]]:
    signature_rows: list[dict[str, object]] = []
    for record in records:
        signature_rows.append(
            {
                "path": record.path,
                "size": int(record.size),
                "modified_ns": int(record.modified_ns),
                "companions": list(record.companion_paths),
                "edits": list(record.edited_paths),
                "variants": [
                    {
                        "path": variant.path,
                        "size": int(variant.size),
                        "modified_ns": int(variant.modified_ns),
                    }
                    for variant in record.variants
                ],
            }
        )
    signature_rows.sort(key=lambda item: str(item["path"]).casefold())
    return signature_rows


def _path_signature(path: Path | None) -> dict[str, object] | None:
    if path is None:
        return None
    candidate = Path(path).expanduser()
    try:
        candidate = candidate.resolve(strict=False)
    except OSError:
        candidate = candidate.absolute()
    if candidate.is_dir():
        return _directory_signature(candidate)
    try:
        stat_result = candidate.stat()
    except OSError:
        return {"path": str(candidate), "exists": False}
    return {
        "path": str(candidate),
        "exists": True,
        "size": int(stat_result.st_size),
        "modified_ns": int(getattr(stat_result, "st_mtime_ns", int(stat_result.st_mtime * 1_000_000_000))),
    }


def _directory_signature(path: Path) -> dict[str, object]:
    entries: list[dict[str, object]] = []
    if path.exists():
        for child in sorted((candidate for candidate in path.rglob("*") if candidate.is_file()), key=lambda item: item.as_posix().casefold()):
            try:
                stat_result = child.stat()
            except OSError:
                continue
            entries.append(
                {
                    "path": child.relative_to(path).as_posix(),
                    "size": int(stat_result.st_size),
                    "modified_ns": int(getattr(stat_result, "st_mtime_ns", int(stat_result.st_mtime * 1_000_000_000))),
                }
            )
    return {
        "path": str(path),
        "exists": path.exists(),
        "entries": entries,
    }
