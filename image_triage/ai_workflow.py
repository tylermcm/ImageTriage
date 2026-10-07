from __future__ import annotations

"""Shared AI runtime configuration and per-folder AI artifact layout.

Resolves the managed Python, model and device settings used by the editor mask
services and the AI setup flows, and owns the hidden per-folder `.image_triage_ai`
layout (paths, readiness checks, cache reset). The AI Culler run itself lives in
`aiculler_workflow`.
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
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Callable


from .ai_model import (
    DEFAULT_SEMANTIC_MODEL_REPO_ID,
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
AI_RUNNER_TARGET_NAME = "ai_python_runner.exe" if os.name == "nt" else "ai_python_runner"
AI_RUNNER_SCRIPT_RELATIVE_PATH = Path("packaging") / "ai_python_runner.py"

@dataclass(slots=True, frozen=True)
class AIWorkflowRuntime:
    """Resolved runtime configuration shared by the managed AI runtime and the mask services."""
    python_executable: Path | None
    device: str = "auto"
    batch_size: int = 16
    semantic_model_name: str = "openai/clip-vit-base-patch32"


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
    """Resolve the default Python, model and device runtime configuration."""
    workspace_root = Path(__file__).resolve().parents[1]
    runtime_root = _application_runtime_root(workspace_root)
    python_executable = _first_existing_path(
        [
            os.environ.get("AICULLING_PYTHON", ""),
            str(runtime_root / AI_RUNNER_TARGET_NAME),
            sys.executable,
        ]
    )
    batch_size = _positive_int_env("AICULLING_BATCH_SIZE", 16)
    semantic_model_override = (os.environ.get("AICULLING_SEMANTIC_MODEL_NAME", "") or "").strip()
    semantic_installation = resolve_semantic_model_installation()
    semantic_model_name = semantic_model_override or (
        semantic_installation.model_name if semantic_installation.is_installed else DEFAULT_SEMANTIC_MODEL_REPO_ID
    )
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

    python_path = Path(python_executable).expanduser().resolve() if python_executable else None
    return AIWorkflowRuntime(
        python_executable=python_path,
        device=device,
        batch_size=batch_size,
        semantic_model_name=semantic_model_name,
    )





def _positive_int_env(name: str, default: int) -> int:
    raw_value = (os.environ.get(name, "") or "").strip()
    if not raw_value:
        return default
    try:
        parsed = int(raw_value)
    except ValueError:
        return default
    return parsed if parsed > 0 else default




def ai_device_environment_override() -> str | None:
    """Return a valid explicit AI device override, if one is configured."""
    value = (os.environ.get("AICULLING_DEVICE", "") or "").strip().lower()
    if value in {"auto", "cpu", "cuda"}:
        return value
    if value.startswith("cuda:") and value[5:].isdigit():
        return value
    return None


def build_ai_workflow_paths(folder: str | Path, *, resolve: bool = True) -> AIWorkflowPaths:
    """Resolve the hidden AI artifact/report layout for a real folder.

    ``resolve=False`` skips ``Path.resolve()``, which asks the filesystem (a network round trip for a folder
    on a share, ~20 s if the share is asleep). The GUI thread passes it for a share's folder; workers keep
    the default. The hidden directory is the same physical place either way."""
    folder_path = Path(folder).expanduser()
    if resolve:
        folder_path = folder_path.resolve()
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
