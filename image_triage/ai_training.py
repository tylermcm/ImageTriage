from __future__ import annotations

import csv
import hashlib
import json
import os
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import re

from PySide6.QtCore import QObject, QRunnable, Signal

from .ai_workflow import (
    AIWorkflowRuntime,
    _parse_tqdm_progress,
    _run_command_with_live_output,
    _should_use_local_staging,
    build_ai_workflow_paths,
    prepare_hidden_ai_workspace,
    rewrite_extraction_artifact_paths,
    stage_supported_images,
)
from .brackets import BracketDetector
from .bursts import burst_candidate_indices
from .metadata import EMPTY_METADATA, CaptureMetadata, load_capture_metadata
from .models import ImageRecord, SortMode, sort_records


LABELS_DIR_NAME = "labels"
LABELING_ARTIFACTS_DIR_NAME = "labeling_artifacts"
TRAINING_DIR_NAME = "training"
TRAINING_RUNS_DIR_NAME = "runs"
EVALUATION_DIR_NAME = "evaluation"
REFERENCE_BANK_DIR_NAME = "reference_bank"
RANKER_RUN_METADATA_FILENAME = "ranker_run.json"
ACTIVE_RANKER_FILENAME = "active_ranker.json"
PAIRWISE_LABELS_FILENAME = "pairwise_labels.jsonl"
CLUSTER_LABELS_FILENAME = "cluster_labels.jsonl"
BEST_CHECKPOINT_FILENAME = "best_ranker.pt"
LAST_CHECKPOINT_FILENAME = "last_ranker.pt"
TRAINING_METRICS_FILENAME = "training_metrics.json"
TRAINING_HISTORY_FILENAME = "training_history.csv"
TRAINING_LOG_FILENAME = "train_ranker.log"
RESOLVED_CONFIG_FILENAME = "resolved_config.json"
EVALUATION_METRICS_FILENAME = "ranker_evaluation.json"
PAIRWISE_BREAKDOWN_FILENAME = "pairwise_evaluation.csv"
CLUSTER_BREAKDOWN_FILENAME = "cluster_evaluation.csv"
EVALUATION_LOG_FILENAME = "evaluate_ranker.log"
REFERENCE_BANK_FILENAME = "reference_bank.npz"
REFERENCE_BANK_SUMMARY_FILENAME = "reference_bank_summary.json"


@dataclass(slots=True, frozen=True)
class AITrainingPaths:
    folder: Path
    hidden_root: Path
    artifacts_dir: Path
    report_dir: Path
    ranked_export_path: Path
    html_report_path: Path
    labeling_artifacts_dir: Path
    labeling_metadata_path: Path
    labeling_image_ids_path: Path
    labeling_clusters_path: Path
    labels_dir: Path
    pairwise_labels_path: Path
    cluster_labels_path: Path
    training_dir: Path
    training_runs_dir: Path
    active_ranker_path: Path
    best_checkpoint_path: Path
    last_checkpoint_path: Path
    training_metrics_path: Path
    training_history_path: Path
    evaluation_dir: Path
    evaluation_metrics_path: Path
    pairwise_breakdown_path: Path
    cluster_breakdown_path: Path
    reference_bank_dir: Path
    reference_bank_path: Path
    reference_bank_summary_path: Path


@dataclass(slots=True)
class RankerTrainingOptions:
    run_name: str = ""
    num_epochs: int = 30
    batch_size: int = 32
    learning_rate: float = 0.001
    hidden_dim: int = 0
    reference_bank_path: str = ""
    reference_top_k: int = 3
    device: str = "auto"


@dataclass(slots=True)
class ReferenceBankBuildOptions:
    reference_dir: str
    output_dir: str
    batch_size: int = 8
    device: str = "auto"


@dataclass(slots=True, frozen=True)
class RankerRunInfo:
    run_id: str
    display_name: str
    run_dir: Path
    checkpoint_path: Path | None
    last_checkpoint_path: Path | None
    metrics_path: Path | None
    history_path: Path | None
    resolved_config_path: Path | None
    evaluation_metrics_path: Path | None
    train_log_path: Path | None
    evaluation_log_path: Path | None
    created_at: str
    pairwise_labels: int
    cluster_labels: int
    num_epochs: int | None
    best_epoch: int | None
    best_validation_accuracy: float | None
    best_validation_loss: float | None
    cluster_top1_hit_rate: float | None
    reference_bank_path: str
    is_active: bool = False
    is_legacy: bool = False


class AITrainingTaskSignals(QObject):
    started = Signal(int)
    stage = Signal(int, int, str)
    progress = Signal(int, int, str)
    log = Signal(str)
    finished = Signal(object)
    failed = Signal(str)


def build_ai_training_paths(folder: str | Path) -> AITrainingPaths:
    workflow_paths = build_ai_workflow_paths(folder)
    hidden_root = workflow_paths.hidden_root
    labeling_artifacts_dir = hidden_root / LABELING_ARTIFACTS_DIR_NAME
    labels_dir = hidden_root / LABELS_DIR_NAME
    training_dir = hidden_root / TRAINING_DIR_NAME
    training_runs_dir = training_dir / TRAINING_RUNS_DIR_NAME
    evaluation_dir = hidden_root / EVALUATION_DIR_NAME
    reference_bank_dir = hidden_root / REFERENCE_BANK_DIR_NAME
    return AITrainingPaths(
        folder=workflow_paths.folder,
        hidden_root=hidden_root,
        artifacts_dir=workflow_paths.artifacts_dir,
        report_dir=workflow_paths.report_dir,
        ranked_export_path=workflow_paths.ranked_export_path,
        html_report_path=workflow_paths.html_report_path,
        labeling_artifacts_dir=labeling_artifacts_dir,
        labeling_metadata_path=labeling_artifacts_dir / "images.csv",
        labeling_image_ids_path=labeling_artifacts_dir / "image_ids.json",
        labeling_clusters_path=labeling_artifacts_dir / "clusters.csv",
        labels_dir=labels_dir,
        pairwise_labels_path=labels_dir / PAIRWISE_LABELS_FILENAME,
        cluster_labels_path=labels_dir / CLUSTER_LABELS_FILENAME,
        training_dir=training_dir,
        training_runs_dir=training_runs_dir,
        active_ranker_path=training_dir / ACTIVE_RANKER_FILENAME,
        best_checkpoint_path=training_dir / BEST_CHECKPOINT_FILENAME,
        last_checkpoint_path=training_dir / LAST_CHECKPOINT_FILENAME,
        training_metrics_path=training_dir / TRAINING_METRICS_FILENAME,
        training_history_path=training_dir / TRAINING_HISTORY_FILENAME,
        evaluation_dir=evaluation_dir,
        evaluation_metrics_path=evaluation_dir / EVALUATION_METRICS_FILENAME,
        pairwise_breakdown_path=evaluation_dir / PAIRWISE_BREAKDOWN_FILENAME,
        cluster_breakdown_path=evaluation_dir / CLUSTER_BREAKDOWN_FILENAME,
        reference_bank_dir=reference_bank_dir,
        reference_bank_path=reference_bank_dir / REFERENCE_BANK_FILENAME,
        reference_bank_summary_path=reference_bank_dir / REFERENCE_BANK_SUMMARY_FILENAME,
    )


def prepare_hidden_ai_training_workspace(folder: str | Path) -> AITrainingPaths:
    prepare_hidden_ai_workspace(folder)
    paths = build_ai_training_paths(folder)
    paths.labeling_artifacts_dir.mkdir(parents=True, exist_ok=True)
    paths.labels_dir.mkdir(parents=True, exist_ok=True)
    paths.training_dir.mkdir(parents=True, exist_ok=True)
    paths.training_runs_dir.mkdir(parents=True, exist_ok=True)
    paths.evaluation_dir.mkdir(parents=True, exist_ok=True)
    paths.reference_bank_dir.mkdir(parents=True, exist_ok=True)
    return paths


def ai_training_artifacts_ready(paths: AITrainingPaths) -> bool:
    required = (
        paths.artifacts_dir / "images.csv",
        paths.artifacts_dir / "embeddings.npy",
        paths.artifacts_dir / "image_ids.json",
        paths.artifacts_dir / "clusters.csv",
    )
    return all(path.exists() for path in required)


def labeling_artifacts_ready(paths: AITrainingPaths) -> bool:
    required = (
        paths.labeling_metadata_path,
        paths.labeling_image_ids_path,
        paths.labeling_clusters_path,
    )
    return all(path.exists() for path in required)


def count_label_records(paths: AITrainingPaths) -> tuple[int, int]:
    return _count_jsonl_lines(paths.pairwise_labels_path), _count_jsonl_lines(paths.cluster_labels_path)


def resolve_trained_checkpoint(paths: AITrainingPaths) -> Path | None:
    active_selection = _read_active_ranker_selection(paths)
    active_checkpoint = _checkpoint_from_active_selection(paths, active_selection)
    if active_checkpoint is not None:
        return active_checkpoint
    for run in list_ranker_runs(paths):
        if run.checkpoint_path is not None:
            return run.checkpoint_path
    return resolve_legacy_trained_checkpoint(paths)


def resolve_legacy_trained_checkpoint(paths: AITrainingPaths) -> Path | None:
    for candidate in (paths.best_checkpoint_path, paths.last_checkpoint_path):
        if candidate.exists():
            return candidate
    return None


def list_ranker_runs(paths: AITrainingPaths) -> tuple[RankerRunInfo, ...]:
    active_selection = _read_active_ranker_selection(paths)
    active_checkpoint = _checkpoint_from_active_selection(paths, active_selection)
    runs: list[RankerRunInfo] = []

    if paths.training_runs_dir.exists():
        for run_dir in sorted(
            (item for item in paths.training_runs_dir.iterdir() if item.is_dir()),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        ):
            run = _load_ranker_run_info(run_dir, active_checkpoint=active_checkpoint)
            if run is not None:
                runs.append(run)

    legacy_run = _load_legacy_ranker_run_info(paths, active_checkpoint=active_checkpoint)
    if legacy_run is not None:
        runs.append(legacy_run)

    runs.sort(
        key=lambda item: (
            item.created_at,
            item.run_dir.stat().st_mtime if item.run_dir.exists() else 0.0,
        ),
        reverse=True,
    )
    return tuple(runs)


def set_active_ranker_selection(
    paths: AITrainingPaths,
    *,
    checkpoint_path: str | Path,
    run_id: str = "",
    display_name: str = "",
) -> None:
    candidate = Path(checkpoint_path).expanduser().resolve()
    payload = {
        "checkpoint_path": str(candidate),
        "run_id": run_id.strip(),
        "display_name": display_name.strip() or candidate.parent.name,
        "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    paths.active_ranker_path.parent.mkdir(parents=True, exist_ok=True)
    paths.active_ranker_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def clear_active_ranker_selection(paths: AITrainingPaths) -> None:
    try:
        if paths.active_ranker_path.exists():
            paths.active_ranker_path.unlink()
    except OSError:
        return


def training_output_dir_for_checkpoint(paths: AITrainingPaths, checkpoint_path: str | Path) -> Path:
    checkpoint = Path(checkpoint_path).expanduser().resolve()
    if checkpoint.parent == paths.training_dir:
        return paths.training_dir
    return checkpoint.parent


def evaluation_output_dir_for_checkpoint(paths: AITrainingPaths, checkpoint_path: str | Path) -> Path:
    training_output_dir = training_output_dir_for_checkpoint(paths, checkpoint_path)
    if training_output_dir == paths.training_dir:
        return paths.evaluation_dir
    return training_output_dir / EVALUATION_DIR_NAME


def create_ranker_run(paths: AITrainingPaths, requested_name: str = "") -> tuple[str, str, Path]:
    timestamp = datetime.now().astimezone()
    base_id = timestamp.strftime("%Y%m%d-%H%M%S")
    slug = _slugify_run_name(requested_name)
    run_id = f"{base_id}-{slug}" if slug else base_id
    run_dir = paths.training_runs_dir / run_id
    suffix = 2
    while run_dir.exists():
        candidate_id = f"{run_id}-{suffix}"
        run_dir = paths.training_runs_dir / candidate_id
        suffix += 1
    display_name = requested_name.strip() or f"Ranker {timestamp.strftime('%Y-%m-%d %H:%M:%S')}"
    return run_dir.name, display_name, run_dir


def build_labeling_command(
    runtime: AIWorkflowRuntime,
    *,
    folder: str | Path,
    annotator_id: str = "",
    artifacts_dir: str | Path | None = None,
) -> tuple[str, ...]:
    paths = prepare_hidden_ai_training_workspace(folder)
    _validate_runtime_paths(
        runtime,
        required=(
            ("engine root", runtime.engine_root),
            ("python executable", runtime.python_executable),
            ("labeling config", runtime.engine_root / "configs" / "labeling_app.json"),
        ),
    )
    command = [
        str(runtime.python_executable),
        "scripts/labeling_app.py",
        "--config",
        str(runtime.engine_root / "configs" / "labeling_app.json"),
        "--artifacts-dir",
        str(Path(artifacts_dir).expanduser().resolve()) if artifacts_dir is not None else str(paths.labeling_artifacts_dir),
        "--output-dir",
        str(paths.labels_dir),
    ]
    if annotator_id.strip():
        command.extend(["--annotator-id", annotator_id.strip()])
    return tuple(command)


def launch_labeling_app(
    runtime: AIWorkflowRuntime,
    *,
    folder: str | Path,
    annotator_id: str = "",
    artifacts_dir: str | Path | None = None,
    appearance_mode: str | None = None,
    parent_pid: int | None = None,
    sync_file_path: str | Path | None = None,
) -> subprocess.Popen[str]:
    command = list(build_labeling_command(runtime, folder=folder, annotator_id=annotator_id, artifacts_dir=artifacts_dir))
    env = dict(os.environ)
    env["PYTHONUNBUFFERED"] = "1"
    env.setdefault("IMAGE_TRIAGE_HOST_ROOT", str(Path(__file__).resolve().parents[1]))
    if appearance_mode:
        env["IMAGE_TRIAGE_APPEARANCE_MODE"] = appearance_mode
    if parent_pid is not None and parent_pid > 0:
        env["IMAGE_TRIAGE_PARENT_PID"] = str(parent_pid)
    if sync_file_path is not None:
        env["IMAGE_TRIAGE_SYNC_FILE"] = str(Path(sync_file_path).expanduser().resolve())
    return subprocess.Popen(
        command,
        cwd=str(runtime.engine_root),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
    )


class PrepareLabelingCandidatesTask(QRunnable):
    def __init__(self, *, folder: Path, records: tuple[ImageRecord, ...]) -> None:
        super().__init__()
        self.folder = folder
        self.records = records
        self.signals = AITrainingTaskSignals()
        self.setAutoDelete(True)

    def run(self) -> None:
        try:
            paths = prepare_hidden_ai_training_workspace(self.folder)
            if not self.records:
                raise ValueError("No images are loaded for the current folder.")
        except Exception as exc:
            self.signals.failed.emit(str(exc))
            return

        self.signals.started.emit(1)
        self.signals.stage.emit(1, 1, "Building label candidates")

        try:
            ordered_records = sort_records(list(self.records), SortMode.NAME)
            metadata_by_path: dict[str, CaptureMetadata] = {}
            total = len(ordered_records)
            for index, record in enumerate(ordered_records, start=1):
                metadata = load_capture_metadata(record.path)
                metadata_by_path[record.path] = metadata
                self.signals.progress.emit(index, total, f"Scanning images {index}/{total}")

            cluster_rows = _build_labeling_cluster_rows(
                folder=self.folder,
                records=ordered_records,
                metadata_by_path=metadata_by_path,
            )
            image_rows = _build_labeling_metadata_rows(
                folder=self.folder,
                records=ordered_records,
                metadata_by_path=metadata_by_path,
            )
            image_ids = [row["image_id"] for row in image_rows]
            _write_csv_rows(paths.labeling_metadata_path, image_rows, fieldnames=list(image_rows[0].keys()) if image_rows else ["image_id", "file_path", "relative_path", "file_name", "capture_timestamp", "capture_time_source"])
            paths.labeling_image_ids_path.write_text(json.dumps(image_ids, indent=2), encoding="utf-8")
            _write_csv_rows(
                paths.labeling_clusters_path,
                cluster_rows,
                fieldnames=list(cluster_rows[0].keys()) if cluster_rows else ["image_id", "cluster_id", "cluster_size", "cluster_position", "time_window_id", "window_kind", "cluster_reason", "capture_timestamp", "capture_time_source", "file_path", "relative_path", "file_name"],
            )
        except Exception as exc:
            self.signals.failed.emit(str(exc))
            return

        multi_image_groups = sum(1 for row in cluster_rows if int(row["cluster_size"]) > 1 and int(row["cluster_position"]) == 0)
        self.signals.finished.emit(
            {
                "artifacts_dir": str(paths.labeling_artifacts_dir),
                "labels_dir": str(paths.labels_dir),
                "total_images": len(image_rows),
                "multi_image_groups": multi_image_groups,
            }
        )


class PrepareTrainingDataTask(QRunnable):
    def __init__(self, *, folder: Path, runtime: AIWorkflowRuntime) -> None:
        super().__init__()
        self.folder = folder
        self.runtime = runtime
        self.signals = AITrainingTaskSignals()
        self.setAutoDelete(True)

    def run(self) -> None:
        try:
            paths = prepare_hidden_ai_training_workspace(self.folder)
            _validate_runtime_paths(
                self.runtime,
                required=(
                    ("engine root", self.runtime.engine_root),
                    ("python executable", self.runtime.python_executable),
                    ("extract config", self.runtime.extraction_config_path),
                    ("cluster config", self.runtime.clustering_config_path),
                ),
            )
        except Exception as exc:
            self.signals.failed.emit(str(exc))
            return

        staged_input_dir: Path | None = None
        use_local_stage = _should_use_local_staging(self.folder, self.runtime)
        total_stages = 2 + (1 if use_local_stage else 0)
        self.signals.started.emit(total_stages)

        try:
            command_start_index = 1
            input_dir = self.folder
            if use_local_stage:
                self.signals.stage.emit(1, total_stages, "Staging images locally")
                staged_input_dir = stage_supported_images(
                    source_folder=self.folder,
                    runtime=self.runtime,
                    progress_callback=lambda current, total, eta_text, message: self.signals.progress.emit(
                        current,
                        total,
                        _merge_eta(message, eta_text),
                    ),
                )
                input_dir = staged_input_dir
                command_start_index = 2

            commands = [
                (
                    "Extracting embeddings",
                    [
                        str(self.runtime.python_executable),
                        "scripts/extract_embeddings.py",
                        "--config",
                        str(self.runtime.extraction_config_path),
                        "--input-dir",
                        str(input_dir),
                        "--output-dir",
                        str(paths.artifacts_dir),
                        "--batch-size",
                        str(self.runtime.batch_size),
                        "--model-name",
                        self.runtime.model_name,
                        "--device",
                        self.runtime.device,
                        "--num-workers",
                        str(self.runtime.num_workers),
                    ],
                ),
                (
                    "Building culling groups",
                    [
                        str(self.runtime.python_executable),
                        "scripts/cluster_embeddings.py",
                        "--config",
                        str(self.runtime.clustering_config_path),
                        "--artifacts-dir",
                        str(paths.artifacts_dir),
                        "--output-dir",
                        str(paths.artifacts_dir),
                    ],
                ),
            ]

            for stage_index, (stage_message, command) in enumerate(commands, start=command_start_index):
                self.signals.stage.emit(stage_index, total_stages, stage_message)
                completed = _run_command_with_live_output(
                    command,
                    cwd=self.runtime.engine_root,
                    progress_callback=lambda line: _emit_command_progress(self.signals, line),
                )
                if completed.returncode != 0:
                    raise RuntimeError(_command_failure_message(stage_message, completed.stdout))
                if staged_input_dir is not None and command[1] == "scripts/extract_embeddings.py":
                    rewrite_extraction_artifact_paths(
                        artifacts_dir=paths.artifacts_dir,
                        source_folder=self.folder,
                    )
        except Exception as exc:
            self.signals.failed.emit(str(exc))
            return

        self.signals.finished.emit(
            {
                "artifacts_dir": str(paths.artifacts_dir),
                "labels_dir": str(paths.labels_dir),
            }
        )


class TrainRankerTask(QRunnable):
    def __init__(self, *, folder: Path, runtime: AIWorkflowRuntime, options: RankerTrainingOptions) -> None:
        super().__init__()
        self.folder = folder
        self.runtime = runtime
        self.options = options
        self.signals = AITrainingTaskSignals()
        self.setAutoDelete(True)
        self.paths = build_ai_training_paths(folder)
        self.run_id, self.display_name, self.run_dir = create_ranker_run(self.paths, options.run_name)
        self.log_path = self.run_dir / TRAINING_LOG_FILENAME

    def run(self) -> None:
        try:
            paths = prepare_hidden_ai_training_workspace(self.folder)
            _validate_runtime_paths(
                self.runtime,
                required=(
                    ("engine root", self.runtime.engine_root),
                    ("python executable", self.runtime.python_executable),
                    ("train config", self.runtime.engine_root / "configs" / "train_ranker.json"),
                ),
            )
            pairwise_count, cluster_count = count_label_records(paths)
            if pairwise_count <= 0 and cluster_count <= 0:
                raise ValueError("No saved pairwise or cluster labels were found. Open Collect Training Labels first.")
            self.run_dir.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            self.signals.failed.emit(str(exc))
            return

        self.signals.started.emit(1)
        self.signals.stage.emit(1, 1, "Training ranker")
        command = [
            str(self.runtime.python_executable),
            "scripts/train_ranker.py",
            "--config",
            str(self.runtime.engine_root / "configs" / "train_ranker.json"),
            "--artifacts-dir",
            str(paths.artifacts_dir),
            "--labels-dir",
            str(paths.labels_dir),
            "--output-dir",
            str(self.run_dir),
            "--reference-top-k",
            str(max(1, self.options.reference_top_k)),
            "--num-epochs",
            str(max(1, self.options.num_epochs)),
            "--batch-size",
            str(max(1, self.options.batch_size)),
            "--learning-rate",
            str(max(0.000001, float(self.options.learning_rate))),
            "--hidden-dim",
            str(max(0, self.options.hidden_dim)),
            "--device",
            self.options.device or self.runtime.device,
        ]
        reference_bank_path = self.options.reference_bank_path.strip()
        if reference_bank_path:
            command.extend(["--reference-bank-path", reference_bank_path])

        try:
            completed = _run_command_with_live_output(
                command,
                cwd=self.runtime.engine_root,
                progress_callback=lambda line: _emit_command_progress(self.signals, line, default_message="Training ranker"),
            )
            if completed.returncode != 0:
                raise RuntimeError(_command_failure_message("Training ranker", completed.stdout))
        except Exception as exc:
            self.signals.failed.emit(str(exc))
            return

        checkpoint_path = _resolve_run_checkpoint(self.run_dir)
        if checkpoint_path is None:
            self.signals.failed.emit("Training finished, but no checkpoint was created.")
            return
        try:
            _write_ranker_run_metadata(
                run_dir=self.run_dir,
                run_id=self.run_id,
                display_name=self.display_name,
                pairwise_count=pairwise_count,
                cluster_count=cluster_count,
                reference_bank_path=reference_bank_path,
            )
        except OSError:
            pass
        self.signals.finished.emit(
            {
                "run_id": self.run_id,
                "display_name": self.display_name,
                "checkpoint_path": str(checkpoint_path),
                "training_dir": str(self.run_dir),
                "metrics_path": str(self.run_dir / TRAINING_METRICS_FILENAME),
                "history_path": str(self.run_dir / TRAINING_HISTORY_FILENAME),
                "resolved_config_path": str(self.run_dir / RESOLVED_CONFIG_FILENAME),
                "log_path": str(self.log_path),
                "reference_bank_path": reference_bank_path,
            }
        )


class EvaluateRankerTask(QRunnable):
    def __init__(
        self,
        *,
        folder: Path,
        runtime: AIWorkflowRuntime,
        checkpoint_path: Path,
        reference_bank_path: str = "",
    ) -> None:
        super().__init__()
        self.folder = folder
        self.runtime = runtime
        self.checkpoint_path = checkpoint_path
        self.reference_bank_path = reference_bank_path.strip()
        self.signals = AITrainingTaskSignals()
        self.setAutoDelete(True)

    def run(self) -> None:
        try:
            paths = prepare_hidden_ai_training_workspace(self.folder)
            evaluation_dir = evaluation_output_dir_for_checkpoint(paths, self.checkpoint_path)
            _validate_runtime_paths(
                self.runtime,
                required=(
                    ("engine root", self.runtime.engine_root),
                    ("python executable", self.runtime.python_executable),
                    ("evaluate config", self.runtime.engine_root / "configs" / "evaluate_ranker.json"),
                    ("checkpoint", self.checkpoint_path),
                ),
            )
            pairwise_count, cluster_count = count_label_records(paths)
            if pairwise_count <= 0 and cluster_count <= 0:
                raise ValueError("No saved pairwise or cluster labels were found for evaluation.")
            evaluation_dir.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            self.signals.failed.emit(str(exc))
            return

        self.signals.started.emit(1)
        self.signals.stage.emit(1, 1, "Evaluating trained ranker")
        command = [
            str(self.runtime.python_executable),
            "scripts/evaluate_ranker.py",
            "--config",
            str(self.runtime.engine_root / "configs" / "evaluate_ranker.json"),
            "--artifacts-dir",
            str(paths.artifacts_dir),
            "--labels-dir",
            str(paths.labels_dir),
            "--checkpoint-path",
            str(self.checkpoint_path),
            "--output-dir",
            str(evaluation_dir),
            "--device",
            self.runtime.device,
        ]
        if self.reference_bank_path:
            command.extend(["--reference-bank-path", self.reference_bank_path])

        try:
            completed = _run_command_with_live_output(
                command,
                cwd=self.runtime.engine_root,
                progress_callback=lambda line: _emit_command_progress(self.signals, line, default_message="Evaluating ranker"),
            )
            if completed.returncode != 0:
                raise RuntimeError(_command_failure_message("Evaluating ranker", completed.stdout))
        except Exception as exc:
            self.signals.failed.emit(str(exc))
            return

        self.signals.finished.emit(
            {
                "metrics_path": str(evaluation_dir / EVALUATION_METRICS_FILENAME),
                "pairwise_breakdown_path": str(evaluation_dir / PAIRWISE_BREAKDOWN_FILENAME),
                "cluster_breakdown_path": str(evaluation_dir / CLUSTER_BREAKDOWN_FILENAME),
                "log_path": str(evaluation_dir / EVALUATION_LOG_FILENAME),
            }
        )


class ScoreCurrentFolderTask(QRunnable):
    def __init__(
        self,
        *,
        folder: Path,
        runtime: AIWorkflowRuntime,
        checkpoint_path: Path,
        reference_bank_path: str = "",
    ) -> None:
        super().__init__()
        self.folder = folder
        self.runtime = runtime
        self.checkpoint_path = checkpoint_path
        self.reference_bank_path = reference_bank_path.strip()
        self.signals = AITrainingTaskSignals()
        self.setAutoDelete(True)

    def run(self) -> None:
        try:
            paths = prepare_hidden_ai_training_workspace(self.folder)
            _validate_runtime_paths(
                self.runtime,
                required=(
                    ("engine root", self.runtime.engine_root),
                    ("python executable", self.runtime.python_executable),
                    ("ranked report config", self.runtime.report_config_path),
                    ("checkpoint", self.checkpoint_path),
                ),
            )
            if not ai_training_artifacts_ready(paths):
                raise ValueError("Training artifacts are not ready yet. Prepare the current folder first.")
        except Exception as exc:
            self.signals.failed.emit(str(exc))
            return

        self.signals.started.emit(1)
        self.signals.stage.emit(1, 1, "Scoring current folder with the trained ranker")
        command = [
            str(self.runtime.python_executable),
            "scripts/export_ranked_report.py",
            "--config",
            str(self.runtime.report_config_path),
            "--artifacts-dir",
            str(paths.artifacts_dir),
            "--checkpoint-path",
            str(self.checkpoint_path),
            "--output-dir",
            str(paths.report_dir),
            "--device",
            self.runtime.device,
        ]
        if paths.labels_dir.exists():
            command.extend(["--labels-dir", str(paths.labels_dir)])
        if self.reference_bank_path:
            command.extend(["--reference-bank-path", self.reference_bank_path])

        try:
            completed = _run_command_with_live_output(
                command,
                cwd=self.runtime.engine_root,
                progress_callback=lambda line: _emit_command_progress(self.signals, line, default_message="Scoring and exporting report"),
            )
            if completed.returncode != 0:
                raise RuntimeError(_command_failure_message("Scoring current folder", completed.stdout))
        except Exception as exc:
            self.signals.failed.emit(str(exc))
            return

        self.signals.finished.emit(
            {
                "report_dir": str(paths.report_dir),
                "html_report_path": str(paths.html_report_path),
                "ranked_export_path": str(paths.ranked_export_path),
            }
        )


class BuildReferenceBankTask(QRunnable):
    def __init__(self, *, runtime: AIWorkflowRuntime, options: ReferenceBankBuildOptions) -> None:
        super().__init__()
        self.runtime = runtime
        self.options = options
        self.signals = AITrainingTaskSignals()
        self.setAutoDelete(True)

    def run(self) -> None:
        try:
            reference_dir = Path(self.options.reference_dir).expanduser().resolve()
            output_dir = Path(self.options.output_dir).expanduser().resolve()
            _validate_runtime_paths(
                self.runtime,
                required=(
                    ("engine root", self.runtime.engine_root),
                    ("python executable", self.runtime.python_executable),
                    ("reference bank config", self.runtime.engine_root / "configs" / "build_reference_bank.json"),
                    ("reference folder", reference_dir),
                ),
            )
            output_dir.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            self.signals.failed.emit(str(exc))
            return

        self.signals.started.emit(1)
        self.signals.stage.emit(1, 1, "Building reference bank")
        command = [
            str(self.runtime.python_executable),
            "scripts/build_reference_bank.py",
            "--config",
            str(self.runtime.engine_root / "configs" / "build_reference_bank.json"),
            "--reference-dir",
            str(reference_dir),
            "--output-dir",
            str(output_dir),
            "--batch-size",
            str(max(1, self.options.batch_size)),
            "--model-name",
            self.runtime.model_name,
            "--device",
            self.options.device or self.runtime.device,
        ]

        try:
            completed = _run_command_with_live_output(
                command,
                cwd=self.runtime.engine_root,
                progress_callback=lambda line: _emit_command_progress(self.signals, line, default_message="Building reference bank"),
            )
            if completed.returncode != 0:
                raise RuntimeError(_command_failure_message("Building reference bank", completed.stdout))
        except Exception as exc:
            self.signals.failed.emit(str(exc))
            return

        self.signals.finished.emit(
            {
                "reference_bank_path": str(output_dir / REFERENCE_BANK_FILENAME),
                "summary_path": str(output_dir / REFERENCE_BANK_SUMMARY_FILENAME),
                "output_dir": str(output_dir),
            }
        )


def _emit_command_progress(signals: AITrainingTaskSignals, line: str, *, default_message: str = "") -> None:
    message = (line or "").strip()
    if not message:
        return
    signals.log.emit(message)
    parsed = _parse_tqdm_progress(message)
    if parsed is not None:
        label, current, total, eta_text = parsed
        signals.progress.emit(current, total, _merge_eta(label, eta_text))
        return
    signals.progress.emit(0, 0, message or default_message)


def _read_active_ranker_selection(paths: AITrainingPaths) -> dict[str, object]:
    if not paths.active_ranker_path.exists():
        return {}
    try:
        data = json.loads(paths.active_ranker_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def _checkpoint_from_active_selection(paths: AITrainingPaths, selection: dict[str, object]) -> Path | None:
    checkpoint_text = str(selection.get("checkpoint_path") or "").strip()
    if checkpoint_text:
        candidate = Path(checkpoint_text).expanduser()
        if candidate.exists():
            return candidate.resolve()
    run_id = str(selection.get("run_id") or "").strip()
    if run_id:
        candidate = _resolve_run_checkpoint(paths.training_runs_dir / run_id)
        if candidate is not None:
            return candidate.resolve()
    return None


def _resolve_run_checkpoint(run_dir: Path) -> Path | None:
    for candidate in (run_dir / BEST_CHECKPOINT_FILENAME, run_dir / LAST_CHECKPOINT_FILENAME):
        if candidate.exists():
            return candidate.resolve()
    return None


def _write_ranker_run_metadata(
    *,
    run_dir: Path,
    run_id: str,
    display_name: str,
    pairwise_count: int,
    cluster_count: int,
    reference_bank_path: str,
) -> None:
    payload = {
        "run_id": run_id,
        "display_name": display_name.strip() or run_id,
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "pairwise_labels": max(0, int(pairwise_count)),
        "cluster_labels": max(0, int(cluster_count)),
        "reference_bank_path": reference_bank_path.strip(),
    }
    (run_dir / RANKER_RUN_METADATA_FILENAME).write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _load_ranker_run_info(run_dir: Path, *, active_checkpoint: Path | None) -> RankerRunInfo | None:
    checkpoint_path = _resolve_run_checkpoint(run_dir)
    if checkpoint_path is None and not (run_dir / TRAINING_METRICS_FILENAME).exists():
        return None

    metadata = _read_json_dict(run_dir / RANKER_RUN_METADATA_FILENAME)
    metrics = _read_json_dict(run_dir / TRAINING_METRICS_FILENAME)
    resolved_config = _read_json_dict(run_dir / RESOLVED_CONFIG_FILENAME)
    evaluation_dir = run_dir / EVALUATION_DIR_NAME
    evaluation_metrics = _read_json_dict(evaluation_dir / EVALUATION_METRICS_FILENAME)

    created_at = str(metadata.get("created_at") or "")
    if not created_at:
        created_at = datetime.fromtimestamp(run_dir.stat().st_mtime).astimezone().isoformat(timespec="seconds")
    reference_bank_path = str(
        metadata.get("reference_bank_path")
        or resolved_config.get("reference_bank_path")
        or ""
    ).strip()

    cluster_top1 = _nested_float(
        evaluation_metrics,
        "cluster_evaluation",
        "top_k_metrics",
        "top_1",
        "hit_rate",
    )
    return RankerRunInfo(
        run_id=str(metadata.get("run_id") or run_dir.name),
        display_name=str(metadata.get("display_name") or run_dir.name),
        run_dir=run_dir,
        checkpoint_path=checkpoint_path,
        last_checkpoint_path=(run_dir / LAST_CHECKPOINT_FILENAME).resolve() if (run_dir / LAST_CHECKPOINT_FILENAME).exists() else None,
        metrics_path=(run_dir / TRAINING_METRICS_FILENAME) if (run_dir / TRAINING_METRICS_FILENAME).exists() else None,
        history_path=(run_dir / TRAINING_HISTORY_FILENAME) if (run_dir / TRAINING_HISTORY_FILENAME).exists() else None,
        resolved_config_path=(run_dir / RESOLVED_CONFIG_FILENAME) if (run_dir / RESOLVED_CONFIG_FILENAME).exists() else None,
        evaluation_metrics_path=(evaluation_dir / EVALUATION_METRICS_FILENAME) if (evaluation_dir / EVALUATION_METRICS_FILENAME).exists() else None,
        train_log_path=(run_dir / TRAINING_LOG_FILENAME) if (run_dir / TRAINING_LOG_FILENAME).exists() else None,
        evaluation_log_path=(evaluation_dir / EVALUATION_LOG_FILENAME) if (evaluation_dir / EVALUATION_LOG_FILENAME).exists() else None,
        created_at=created_at,
        pairwise_labels=int(metadata.get("pairwise_labels") or metrics.get("label_summary", {}).get("pairwise_labels", 0) or 0),
        cluster_labels=int(metadata.get("cluster_labels") or metrics.get("label_summary", {}).get("cluster_labels", 0) or 0),
        num_epochs=_nested_int(resolved_config, "num_epochs"),
        best_epoch=_nested_int(metrics, "best_epoch"),
        best_validation_accuracy=_nested_float(metrics, "best_validation_pairwise_accuracy"),
        best_validation_loss=_nested_float(metrics, "best_validation_loss"),
        cluster_top1_hit_rate=cluster_top1,
        reference_bank_path=reference_bank_path,
        is_active=bool(active_checkpoint and checkpoint_path and _same_path(active_checkpoint, checkpoint_path)),
        is_legacy=False,
    )


def _load_legacy_ranker_run_info(paths: AITrainingPaths, *, active_checkpoint: Path | None) -> RankerRunInfo | None:
    checkpoint_path = resolve_legacy_trained_checkpoint(paths)
    metrics_path = paths.training_metrics_path if paths.training_metrics_path.exists() else None
    history_path = paths.training_history_path if paths.training_history_path.exists() else None
    resolved_config_path = paths.training_dir / RESOLVED_CONFIG_FILENAME
    evaluation_metrics_path = paths.evaluation_metrics_path if paths.evaluation_metrics_path.exists() else None
    if checkpoint_path is None and metrics_path is None and history_path is None:
        return None

    metrics = _read_json_dict(paths.training_metrics_path)
    resolved_config = _read_json_dict(resolved_config_path)
    evaluation_metrics = _read_json_dict(paths.evaluation_metrics_path)
    created_at = datetime.fromtimestamp(paths.training_dir.stat().st_mtime).astimezone().isoformat(timespec="seconds")
    return RankerRunInfo(
        run_id="legacy",
        display_name="Legacy Ranker",
        run_dir=paths.training_dir,
        checkpoint_path=checkpoint_path,
        last_checkpoint_path=paths.last_checkpoint_path.resolve() if paths.last_checkpoint_path.exists() else None,
        metrics_path=metrics_path,
        history_path=history_path,
        resolved_config_path=resolved_config_path if resolved_config_path.exists() else None,
        evaluation_metrics_path=evaluation_metrics_path,
        train_log_path=(paths.training_dir / TRAINING_LOG_FILENAME) if (paths.training_dir / TRAINING_LOG_FILENAME).exists() else None,
        evaluation_log_path=(paths.evaluation_dir / EVALUATION_LOG_FILENAME) if (paths.evaluation_dir / EVALUATION_LOG_FILENAME).exists() else None,
        created_at=created_at,
        pairwise_labels=int(metrics.get("label_summary", {}).get("pairwise_labels", 0) or 0),
        cluster_labels=int(metrics.get("label_summary", {}).get("cluster_labels", 0) or 0),
        num_epochs=_nested_int(resolved_config, "num_epochs"),
        best_epoch=_nested_int(metrics, "best_epoch"),
        best_validation_accuracy=_nested_float(metrics, "best_validation_pairwise_accuracy"),
        best_validation_loss=_nested_float(metrics, "best_validation_loss"),
        cluster_top1_hit_rate=_nested_float(
            evaluation_metrics,
            "cluster_evaluation",
            "top_k_metrics",
            "top_1",
            "hit_rate",
        ),
        reference_bank_path=str(resolved_config.get("reference_bank_path") or "").strip(),
        is_active=bool(active_checkpoint and checkpoint_path and _same_path(active_checkpoint, checkpoint_path)),
        is_legacy=True,
    )


def _read_json_dict(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def _nested_float(payload: dict[str, object], *keys: str) -> float | None:
    current: object = payload
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    try:
        if current is None:
            return None
        return float(current)
    except (TypeError, ValueError):
        return None


def _nested_int(payload: dict[str, object], *keys: str) -> int | None:
    current: object = payload
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    try:
        if current is None:
            return None
        return int(current)
    except (TypeError, ValueError):
        return None


def _slugify_run_name(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.strip().lower())
    return slug.strip("-")[:48]


def _same_path(left: Path, right: Path) -> bool:
    try:
        return left.resolve() == right.resolve()
    except OSError:
        return str(left).casefold() == str(right).casefold()


def _command_failure_message(stage_message: str, stdout: str) -> str:
    text = (stdout or "").strip()
    if not text:
        return f"{stage_message} failed."
    tail = "\n".join(text.splitlines()[-20:])
    return f"{stage_message} failed.\n\n{tail}"


def _count_jsonl_lines(path: Path) -> int:
    if not path.exists():
        return 0
    try:
        with path.open("r", encoding="utf-8") as handle:
            return sum(1 for line in handle if line.strip())
    except OSError:
        return 0


def _merge_eta(message: str, eta_text: str) -> str:
    eta = (eta_text or "").strip()
    if not eta:
        return message
    return f"{message} | ETA {eta}"


def _validate_runtime_paths(runtime: AIWorkflowRuntime, *, required: tuple[tuple[str, Path], ...]) -> None:
    missing: list[str] = []
    for label, path in required:
        if not path.exists():
            missing.append(f"{label}: {path}")
    if missing:
        raise FileNotFoundError("Missing AI workflow paths:\n" + "\n".join(missing))


def _build_labeling_metadata_rows(
    *,
    folder: Path,
    records: list[ImageRecord],
    metadata_by_path: dict[str, CaptureMetadata],
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for record in records:
        relative_path = _relative_path_for_record(folder, record)
        metadata = metadata_by_path.get(record.path, EMPTY_METADATA)
        rows.append(
            {
                "image_id": _stable_image_id(relative_path),
                "file_path": str(Path(record.path).resolve()),
                "relative_path": relative_path,
                "file_name": record.name,
                "capture_timestamp": metadata.captured_at or "",
                "capture_time_source": "metadata" if metadata.captured_at else "missing",
            }
        )
    return rows


def _build_labeling_cluster_rows(
    *,
    folder: Path,
    records: list[ImageRecord],
    metadata_by_path: dict[str, CaptureMetadata],
) -> list[dict[str, str]]:
    groups = _label_candidate_groups(records, metadata_by_path)
    rows: list[dict[str, str]] = []
    for cluster_index, group in enumerate(groups):
        cluster_id = f"label_cluster_{cluster_index:04d}"
        cluster_size = len(group["indices"])
        for position, record_index in enumerate(group["indices"]):
            record = records[record_index]
            relative_path = _relative_path_for_record(folder, record)
            metadata = metadata_by_path.get(record.path, EMPTY_METADATA)
            rows.append(
                {
                    "image_id": _stable_image_id(relative_path),
                    "cluster_id": cluster_id,
                    "cluster_size": str(cluster_size),
                    "cluster_position": str(position),
                    "time_window_id": cluster_id,
                    "window_kind": group["window_kind"],
                    "cluster_reason": group["cluster_reason"],
                    "capture_timestamp": metadata.captured_at or "",
                    "capture_time_source": "metadata" if metadata.captured_at else "missing",
                    "file_path": str(Path(record.path).resolve()),
                    "relative_path": relative_path,
                    "file_name": record.name,
                }
            )
    return rows


def _label_candidate_groups(
    records: list[ImageRecord],
    metadata_by_path: dict[str, CaptureMetadata],
) -> list[dict[str, object]]:
    if not records:
        return []

    groups: list[dict[str, object]] = []
    detector = BracketDetector()
    detector._cache.update(metadata_by_path)
    used: set[int] = set()
    index = 0
    while index < len(records):
        if index in used:
            index += 1
            continue

        bracket_group = detector.group_for(records, index)
        if bracket_group is not None:
            indices = list(range(bracket_group.start_index, bracket_group.end_index))
            if not any(candidate in used for candidate in indices):
                used.update(indices)
                groups.append(
                    {
                        "indices": indices,
                        "cluster_reason": "label_candidates_bracket",
                        "window_kind": "bracket",
                    }
                )
                index = bracket_group.end_index
                continue

        burst_indices = burst_candidate_indices(records, metadata_by_path, start_index=index, used=used)
        if len(burst_indices) >= 2:
            used.update(burst_indices)
            groups.append(
                {
                    "indices": burst_indices,
                    "cluster_reason": "label_candidates_burst",
                    "window_kind": "burst",
                }
            )
            index = burst_indices[-1] + 1
            continue

        used.add(index)
        groups.append(
            {
                "indices": [index],
                "cluster_reason": "label_candidates_singleton",
                "window_kind": "singleton",
            }
        )
        index += 1

    return groups


def _relative_path_for_record(folder: Path, record: ImageRecord) -> str:
    record_path = Path(record.path).expanduser().resolve()
    folder_path = folder.expanduser().resolve()
    try:
        relative_path = record_path.relative_to(folder_path).as_posix()
    except ValueError:
        relative_path = record_path.name
    return relative_path.replace("\\", "/").lstrip("./")


def _stable_image_id(relative_path: str) -> str:
    normalized = relative_path.replace("\\", "/").strip().lstrip("./")
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()


def _write_csv_rows(path: Path, rows: list[dict[str, str]], *, fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        if rows:
            writer.writerows(rows)
