from __future__ import annotations

import ctypes
import csv
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from hashlib import sha1
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QObject, QRunnable, Signal


HIDDEN_ROOT_NAME = ".image_triage_ai"
ARTIFACTS_DIR_NAME = "artifacts"
REPORT_DIR_NAME = "ranker_report"
STAGE_WORKSPACES_DIR_NAME = "workspaces"
STAGE_INPUT_DIR_NAME = "input"
STAGE_MANIFEST_FILENAME = "stage_manifest.json"
FILE_ATTRIBUTE_HIDDEN = 0x2
DEFAULT_STAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp")
DRIVE_UNKNOWN = 0
DRIVE_NO_ROOT_DIR = 1
DRIVE_REMOVABLE = 2
DRIVE_FIXED = 3
DRIVE_REMOTE = 4
TQDM_PROGRESS_PATTERN = re.compile(
    r"^(?P<label>Scanning images|Extracting embeddings):.*?\|\s*(?P<current>\d+)/(?P<total>\d+)\s*\[(?P<timing>[^\]]+)\]"
)
AI_RUNTIME_DIR_NAME = "ai_runtime"
AI_RUNNER_TARGET_NAME = "ai_python_runner.exe" if os.name == "nt" else "ai_python_runner"
REQUIRED_AI_SCRIPT_RELATIVE_PATHS = (
    "scripts/extract_embeddings.py",
    "scripts/cluster_embeddings.py",
    "scripts/export_ranked_report.py",
)


@dataclass(slots=True, frozen=True)
class AIWorkflowRuntime:
    engine_root: Path
    python_executable: Path | None
    checkpoint_path: Path
    extraction_config_path: Path
    clustering_config_path: Path
    report_config_path: Path
    device: str = "cuda"
    batch_size: int = 16
    num_workers: int = 4
    local_stage_mode: str = "auto"
    local_stage_root: Path | None = None

    def validate(self) -> None:
        missing: list[str] = []
        for label, path in (
            ("engine root", self.engine_root),
            ("checkpoint", self.checkpoint_path),
            ("extract config", self.extraction_config_path),
            ("cluster config", self.clustering_config_path),
            ("report config", self.report_config_path),
        ):
            if not path.exists():
                missing.append(f"{label}: {path}")
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
        if missing:
            raise FileNotFoundError("Missing AI workflow paths:\n" + "\n".join(missing))
        if self.local_stage_mode not in {"auto", "always", "off"}:
            raise ValueError("local_stage_mode must be 'auto', 'always', or 'off'.")


@dataclass(slots=True, frozen=True)
class AIWorkflowPaths:
    folder: Path
    hidden_root: Path
    artifacts_dir: Path
    report_dir: Path
    ranked_export_path: Path
    html_report_path: Path


class AIRunSignals(QObject):
    started = Signal(str)
    stage = Signal(str, int, int, str)
    progress = Signal(str, str, int, int, str)
    finished = Signal(str, str, str)
    failed = Signal(str, str)


class AIRunTask(QRunnable):
    def __init__(
        self,
        *,
        folder: Path,
        runtime: AIWorkflowRuntime,
        paths: AIWorkflowPaths,
        labels_dir: Path | None = None,
        reference_bank_path: Path | None = None,
    ) -> None:
        super().__init__()
        self.folder = folder
        self.runtime = runtime
        self.paths = paths
        self.labels_dir = labels_dir
        self.reference_bank_path = reference_bank_path
        self.signals = AIRunSignals()
        self.setAutoDelete(True)

    def run(self) -> None:
        folder_text = str(self.folder)
        staged_input_dir: Path | None = None
        try:
            self.runtime.validate()
            prepare_hidden_ai_workspace(self.folder)
        except Exception as exc:
            self.signals.failed.emit(folder_text, str(exc))
            return

        self.signals.started.emit(folder_text)

        commands = [
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
                    "--device",
                    self.runtime.device,
                    "--num-workers",
                    str(self.runtime.num_workers),
                ],
            ),
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
            ),
            (
                "report",
                "Scoring groups and building report",
                "scripts/export_ranked_report.py",
                [
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
                ],
            ),
        ]

        if self.labels_dir is not None and self.labels_dir.exists():
            commands[-1][3].extend(["--labels-dir", str(self.labels_dir)])
        if self.reference_bank_path is not None and self.reference_bank_path.exists():
            commands[-1][3].extend(["--reference-bank-path", str(self.reference_bank_path)])

        use_local_stage = _should_use_local_staging(self.folder, self.runtime)
        total_stages = len(commands) + (1 if use_local_stage else 0)
        command_start_index = 1

        if use_local_stage:
            self.signals.stage.emit(folder_text, 1, total_stages, "Staging images locally")
            staged_input_dir = stage_supported_images(
                source_folder=self.folder,
                runtime=self.runtime,
                progress_callback=lambda current, total, eta_text, message: self.signals.progress.emit(
                    folder_text,
                    message,
                    current,
                    total,
                    eta_text,
                ),
            )
            commands[0][3][commands[0][3].index("--input-dir") + 1] = str(staged_input_dir)
            command_start_index = 2
        else:
            commands[0][3][commands[0][3].index("--input-dir") + 1] = str(self.folder)

        for stage_index, (stage_name, stage_message, script_relative_path, stage_args) in enumerate(
            commands,
            start=command_start_index,
        ):
            self.signals.stage.emit(folder_text, stage_index, total_stages, stage_message)
            command = _resolve_stage_command(
                self.runtime,
                script_relative_path=script_relative_path,
                stage_args=stage_args,
            )
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
            )
            if completed.returncode != 0:
                error_parts = [stage_message + " failed."]
                stderr = (completed.stderr or "").strip()
                stdout = (completed.stdout or "").strip()
                if stderr:
                    error_parts.append(stderr)
                elif stdout:
                    error_parts.append(stdout)
                self.signals.failed.emit(folder_text, "\n\n".join(error_parts))
                return
            if staged_input_dir is not None and stage_name == "extract":
                rewrite_extraction_artifact_paths(
                    artifacts_dir=self.paths.artifacts_dir,
                    source_folder=self.folder,
                )

        self.signals.finished.emit(
            folder_text,
            str(self.paths.report_dir),
            str(self.paths.html_report_path),
        )


def default_ai_workflow_runtime() -> AIWorkflowRuntime:
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
    checkpoint_path = _first_existing_path(
        [
            os.environ.get("AICULLING_CHECKPOINT", ""),
            str(Path(engine_root) / "outputs" / "china26_full" / "ranker_run_mlp_100ep" / "best_ranker.pt"),
        ]
    )
    local_stage_mode = (os.environ.get("AICULLING_LOCAL_STAGE_MODE", "auto") or "auto").strip().lower()
    local_appdata = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
    local_stage_root = Path(
        os.environ.get(
            "AICULLING_LOCAL_STAGE_ROOT",
            str(local_appdata / "image_triage_ai_cache" / "stage"),
        )
    )

    engine_root_path = Path(engine_root).expanduser().resolve()
    python_path = Path(python_executable).expanduser().resolve() if python_executable else None
    return AIWorkflowRuntime(
        engine_root=engine_root_path,
        python_executable=python_path,
        checkpoint_path=Path(checkpoint_path).expanduser().resolve(),
        extraction_config_path=engine_root_path / "configs" / "extract_embeddings.json",
        clustering_config_path=engine_root_path / "configs" / "cluster_embeddings.json",
        report_config_path=engine_root_path / "configs" / "export_ranked_report.json",
        local_stage_mode=local_stage_mode,
        local_stage_root=local_stage_root.expanduser().resolve(),
    )


def build_ai_workflow_paths(folder: str | Path) -> AIWorkflowPaths:
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
    )


def prepare_hidden_ai_workspace(folder: str | Path) -> AIWorkflowPaths:
    paths = build_ai_workflow_paths(folder)
    paths.hidden_root.mkdir(parents=True, exist_ok=True)
    paths.artifacts_dir.mkdir(parents=True, exist_ok=True)
    paths.report_dir.mkdir(parents=True, exist_ok=True)
    _mark_hidden(paths.hidden_root)
    return paths


def existing_hidden_ai_report_dir(folder: str | Path) -> Path | None:
    paths = build_ai_workflow_paths(folder)
    if paths.ranked_export_path.exists():
        return paths.report_dir
    return None


def stage_supported_images(
    *,
    source_folder: Path,
    runtime: AIWorkflowRuntime,
    progress_callback: Callable[[int, int, str, str], None] | None = None,
) -> Path:
    stage_root, stage_root_message = _ensure_stage_root(runtime.local_stage_root)
    if stage_root_message and progress_callback is not None:
        progress_callback(0, 0, "", stage_root_message)
    workspace_dir = _stage_workspace_dir(stage_root, source_folder)
    input_dir = workspace_dir / STAGE_INPUT_DIR_NAME
    manifest_path = workspace_dir / STAGE_MANIFEST_FILENAME
    input_dir.mkdir(parents=True, exist_ok=True)
    supported_extensions = load_supported_extensions(runtime.extraction_config_path)
    source_folder = source_folder.resolve()
    previous_entries = _load_stage_manifest(manifest_path, source_folder)
    candidate_paths = sorted(
        (
            path
            for path in source_folder.rglob("*")
            if path.is_file() and path.suffix.lower() in supported_extensions
        ),
        key=lambda item: item.relative_to(source_folder).as_posix().casefold(),
    )

    total = len(candidate_paths)
    start_time = time.monotonic()
    copied_count = 0
    reused_count = 0
    current_entries: dict[str, dict[str, int]] = {}
    if progress_callback is not None:
        progress_callback(0, total, "", f"Staging images locally (0/{total})")

    for index, path in enumerate(candidate_paths, start=1):
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
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            temp_destination = destination.with_name(destination.name + ".partial")
            if temp_destination.exists():
                temp_destination.unlink(missing_ok=True)
            shutil.copy2(path, temp_destination)
            temp_destination.replace(destination)
            copied_count += 1
        if progress_callback is not None and (index == total or index == 1 or index % 100 == 0):
            progress_callback(
                index,
                total,
                _estimate_eta_text(start_time=start_time, completed=index, total=total),
                f"Staging images locally ({index}/{total}, {reused_count} cached)",
            )

    stale_entries = set(previous_entries) - set(current_entries)
    for relative_key in stale_entries:
        stale_path = input_dir / Path(relative_key)
        if stale_path.exists():
            stale_path.unlink(missing_ok=True)
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
    local_appdata = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
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
    if runtime.python_executable is None:
        raise FileNotFoundError(f"No Python runner configured for AI tool: {script_path}")
    if not runtime.python_executable.exists():
        raise FileNotFoundError(f"Python runner does not exist: {runtime.python_executable}")
    return [str(runtime.python_executable), str(script_path), *stage_args]


def _runtime_root_from_engine_root(engine_root: Path) -> Path:
    if engine_root.name == "AICullingPipeline" and engine_root.parent.name == AI_RUNTIME_DIR_NAME:
        return engine_root.parent.parent
    return engine_root.parent


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
) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["PYTHONUNBUFFERED"] = "1"
    process = subprocess.Popen(
        command,
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        env=env,
    )

    chunks: list[str] = []
    buffer = ""
    assert process.stdout is not None
    try:
        for chunk in iter(process.stdout.readline, ""):
            if chunk == "" and process.poll() is not None:
                break
            if not chunk:
                continue
            chunks.append(chunk)
            normalized = chunk.replace("\r", "\n")
            segments = normalized.split("\n")
            for index, segment in enumerate(segments):
                is_terminated = index < len(segments) - 1
                if is_terminated:
                    line = f"{buffer}{segment}".strip()
                    if line and progress_callback is not None:
                        progress_callback(line)
                    buffer = ""
                else:
                    buffer += segment
    finally:
        try:
            process.stdout.close()
        except Exception:
            pass

    if buffer.strip() and progress_callback is not None:
        progress_callback(buffer.strip())

    return subprocess.CompletedProcess(
        args=command,
        returncode=process.wait(),
        stdout="".join(chunks),
        stderr="",
    )


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
