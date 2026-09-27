from __future__ import annotations

"""External AI culling pipeline orchestration and stage caching.

This module owns the contract between Image Triage and the separate
AICullingPipeline runtime. It resolves runtime paths, stages supported images,
builds deterministic cache keys for each AI stage, and executes the extraction,
grouping, and report commands on worker threads.
"""

import ctypes
import csv
import logging
import gc
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
import urllib.request
from urllib.parse import urlparse
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Callable


from .ai_model import (
    DEFAULT_SEMANTIC_MODEL_REPO_ID,
    AIModelInstallation,
    resolve_ai_model_installation,
    resolve_semantic_model_installation,
)
from .ai_runtime_packages import load_ai_runtime_installation_status
from .perf import perf_logger

if TYPE_CHECKING:
    from .models import ImageRecord


HIDDEN_ROOT_NAME = ".image_triage_ai"
ARTIFACTS_DIR_NAME = "artifacts"
REPORT_DIR_NAME = "ranker_report"
LOGS_DIR_NAME = "logs"
LATEST_AI_RUN_LOG_FILENAME = "latest_ai_culling.log"
FILE_ATTRIBUTE_HIDDEN = 0x2
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
RECOMMENDED_AI_DATALOADER_WORKERS = 4


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
    worker_capacity = available_ai_dataloader_worker_capacity()
    requested_workers = _nonnegative_int_env(
        "AICULLING_NUM_WORKERS",
        recommended_ai_dataloader_workers(worker_capacity),
    )
    num_workers = min(worker_capacity, requested_workers) if requested_workers > 0 else 0
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
    device_override = ai_device_environment_override()
    if device_override is not None:
        device = device_override
    elif "gpu" in runtime_status.installed_variants:
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



def available_ai_dataloader_worker_capacity() -> int:
    """Return the logical processors available to this process."""

    process_cpu_count = getattr(os, "process_cpu_count", None)
    detected = process_cpu_count() if callable(process_cpu_count) else os.cpu_count()
    return max(1, int(detected or 1))


def recommended_ai_dataloader_workers(capacity: int | None = None) -> int:
    available = available_ai_dataloader_worker_capacity() if capacity is None else max(1, int(capacity))
    return min(RECOMMENDED_AI_DATALOADER_WORKERS, available)



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


def ai_device_environment_override() -> str | None:
    """Return a valid explicit AI device override, if one is configured."""
    value = (os.environ.get("AICULLING_DEVICE", "") or "").strip().lower()
    if value in {"auto", "cpu", "cuda"}:
        return value
    if value.startswith("cuda:") and value[5:].isdigit():
        return value
    return None


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





def ai_report_artifacts_ready(paths: AIWorkflowPaths) -> bool:
    """Return whether the ranked AI report export exists for a folder."""
    return paths.ranked_export_path.exists()


def ai_semantic_artifacts_ready(paths: AIWorkflowPaths) -> bool:
    """Return whether the semantic sidecar export exists for a folder."""
    return paths.semantic_export_path.exists() and paths.semantic_summary_path.exists()




def _remove_tree_if_present(path: Path) -> None:
    if not path.exists():
        return
    if path.is_file() or path.is_symlink():
        _unlink_with_retries(path)
        return
    _rmtree_with_retries(path)


def _remove_path_if_present(path: Path) -> None:
    if not path.exists():
        return
    if path.is_dir():
        _rmtree_with_retries(path)
        return
    _unlink_with_retries(path)


def _rmtree_with_retries(path: Path, *, attempts: int = 8, delay_seconds: float = 0.25) -> None:
    last_error: OSError | None = None
    for attempt in range(attempts):
        try:
            shutil.rmtree(path)
            return
        except OSError as exc:
            last_error = exc
            gc.collect()
            if attempt < attempts - 1:
                time.sleep(delay_seconds * (attempt + 1))
    if last_error is not None:
        raise last_error


def _unlink_with_retries(path: Path, *, attempts: int = 8, delay_seconds: float = 0.25) -> None:
    last_error: OSError | None = None
    for attempt in range(attempts):
        try:
            path.unlink()
            return
        except OSError as exc:
            last_error = exc
            gc.collect()
            if attempt < attempts - 1:
                time.sleep(delay_seconds * (attempt + 1))
    if last_error is not None:
        raise last_error












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

    parsed = urlparse(source_text)
    if parsed.scheme != "https":
        raise ValueError("Checkpoint URL must use https:// or point to an existing local file.")

    temp_destination = destination.with_suffix(destination.suffix + ".download")
    if temp_destination.exists():
        temp_destination.unlink(missing_ok=True)

    with urllib.request.urlopen(source_text) as response, temp_destination.open("wb") as handle:
        shutil.copyfileobj(response, handle)
    temp_destination.replace(destination)



def resolve_ai_python_script_command(
    script_path: str | Path,
    *args: str,
    runtime: AIWorkflowRuntime | None = None,
) -> list[str]:
    """Run an arbitrary AI worker through the same Python boundary as culling."""
    resolved_runtime = runtime or default_ai_workflow_runtime()
    python_executable = resolved_runtime.python_executable
    if python_executable is None or not python_executable.exists():
        raise FileNotFoundError("The managed AI Python runner is unavailable.")
    resolved_script = Path(script_path).expanduser().resolve()
    if not resolved_script.is_file():
        raise FileNotFoundError(resolved_script)
    runtime_root = _application_runtime_root(Path(__file__).resolve().parents[1])
    runner_script = _runtime_runner_script(runtime_root)
    if (
        runner_script is not None
        and python_executable.name.casefold() == AI_RUNNER_TARGET_NAME.casefold()
    ):
        return [
            str(python_executable),
            str(runner_script),
            str(resolved_script),
            *args,
        ]
    return [str(python_executable), str(resolved_script), *args]



def _runtime_runner_script(runtime_root: Path) -> Path | None:
    candidate = runtime_root / AI_RUNNER_SCRIPT_RELATIVE_PATH
    if candidate.exists():
        return candidate.resolve()
    return None






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


# Directories that never contribute to an AI stage's inputs but can each hold
# tens of thousands of files. Walking them turned a cache-key computation into
# a multi-minute stall on a developer checkout.
SIGNATURE_PRUNED_DIRECTORY_NAMES = frozenset(
    {
        ".git",
        ".hg",
        ".idea",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".svn",
        ".vs",
        ".vscode",
        "__pycache__",
        "build",
        "dist",
        "node_modules",
        "site-packages",
    }
)
# Upper bound on files hashed into one directory signature. Beyond this the
# signature records the count and stops, which stays deterministic while
# guaranteeing the walk terminates promptly.
SIGNATURE_MAX_ENTRIES = 4000


def _is_pruned_directory(name: str) -> bool:
    lowered = name.casefold()
    if lowered in SIGNATURE_PRUNED_DIRECTORY_NAMES:
        return True
    # Virtual environments: ".venv", "venv", "linux_build_venv", ...
    return lowered.endswith("venv") or lowered.startswith(".venv")


def _directory_signature(path: Path) -> dict[str, object]:
    """A deterministic, bounded fingerprint of a directory's contents.

    Bounded on purpose: this feeds a cache key that is computed on the UI path,
    and the engine root can be an arbitrary user directory. A single unreadable
    entry (a reparse point, a WSL symlink, a file an antivirus scanner has
    locked) is skipped rather than aborting the whole signature.
    """
    entries: list[dict[str, object]] = []
    exists = path.exists()
    truncated = False
    if exists:
        collected: list[tuple[str, int, int]] = []
        for directory, subdirectories, filenames in os.walk(path, onerror=lambda _exc: None):
            subdirectories[:] = sorted(
                name for name in subdirectories if not _is_pruned_directory(name)
            )
            directory_path = Path(directory)
            for filename in sorted(filenames):
                child = directory_path / filename
                try:
                    stat_result = child.stat()
                except OSError:
                    continue
                collected.append(
                    (
                        child.relative_to(path).as_posix(),
                        int(stat_result.st_size),
                        int(
                            getattr(
                                stat_result,
                                "st_mtime_ns",
                                int(stat_result.st_mtime * 1_000_000_000),
                            )
                        ),
                    )
                )
                if len(collected) >= SIGNATURE_MAX_ENTRIES:
                    truncated = True
                    break
            if truncated:
                break
        collected.sort(key=lambda item: item[0].casefold())
        entries = [
            {"path": relative, "size": size, "modified_ns": modified}
            for relative, size, modified in collected
        ]
    signature: dict[str, object] = {
        "path": str(path),
        "exists": exists,
        "entries": entries,
    }
    if truncated:
        signature["truncated"] = True
    return signature
