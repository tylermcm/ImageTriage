from __future__ import annotations

"""AI label collection, ranker training, and evaluation orchestration.

This module bridges Image Triage's folder-first review flow with the external
AI culling pipeline. It is responsible for:

- defining on-disk layouts for training artifacts
- preparing label-collection inputs from current records
- managing reusable General Use training pools
- spawning background tasks for training, evaluation, scoring, and reference banks
- loading enough metadata about past runs for the UI to present and reuse them
"""

import csv
import hashlib
import json
import os
import subprocess
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import re

import numpy as np

from .ai_workflow import (
    AIWorkflowPaths,
    ARTIFACTS_DIR_NAME,
    REPORT_DIR_NAME,
    build_ai_workflow_paths,
)
from .metadata import CaptureMetadata
from .models import ImageRecord
from .ranker_fit import RankerFitDiagnosis, diagnose_ranker_fit
from .ranker_profiles import (
    DEFAULT_RANKER_PROFILE_KEY,
    RankerProfileSuggestion,
    normalize_ranker_profile,
    ranker_profile_options,
    suggest_training_profile,
)
from .scan_cache import app_data_root


LABELS_DIR_NAME = "labels"
LABELING_ARTIFACTS_DIR_NAME = "labeling_artifacts"
TRAINING_DIR_NAME = "training"
TRAINING_RUNS_DIR_NAME = "runs"
EVALUATION_DIR_NAME = "evaluation"
REFERENCE_BANK_DIR_NAME = "reference_bank"
LABEL_SOURCE_ROOT_DIR_NAME = "label_sources"
LABEL_SOURCE_MANIFEST_FILENAME = "source.json"
LABEL_SOURCES_INDEX_FILENAME = "sources.json"
LEGACY_LABEL_MIGRATION_MARKER = ".legacy_labels_migrated"
RANKER_RUN_METADATA_FILENAME = "ranker_run.json"
ACTIVE_RANKER_FILENAME = "active_ranker.json"
PAIRWISE_LABELS_FILENAME = "pairwise_labels.jsonl"
CLUSTER_LABELS_FILENAME = "cluster_labels.jsonl"
AI_DISAGREEMENT_SOURCE_MODE = "ai_disagreement"
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
SIGNALS_DIR_NAME = "signals"
SIGNALS_JSON_FILENAME = "culling_signals.json"
SIGNALS_CSV_FILENAME = "culling_signals.csv"
SIGNAL_EVALUATION_METRICS_FILENAME = "culling_signal_evaluation.json"
SIGNAL_EVALUATION_SUMMARY_FILENAME = "culling_signal_evaluation.csv"
SIGNAL_COMBINER_WEIGHTS_FILENAME = "personal_combiner_weights.json"
SIGNAL_COMBINER_FEATURES_FILENAME = "personal_combiner_training_rows.csv"
GENERAL_TRAINING_ROOT_DIR_NAME = "ai_training"
GENERAL_TRAINING_PROFILE_DIR_NAME = "general_use"
GENERAL_POOL_MANIFEST_FILENAME = "general_pool_manifest.json"
GENERAL_RETRAIN_RECOMMENDATION_MIN_LABELS = 24
LABELING_READY_FILE_ENV = "IMAGE_TRIAGE_LABELING_READY_FILE"
LABELING_READY_WAIT_TIMEOUT_SECONDS = 45.0
LABELING_READY_POLL_INTERVAL_SECONDS = 0.15


@dataclass(slots=True, frozen=True)
class AITrainingPaths:
    """Resolved filesystem layout for one folder's training workspace."""
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
    """User-configurable knobs for a training run."""
    run_name: str = ""
    profile_key: str = DEFAULT_RANKER_PROFILE_KEY
    num_epochs: int = 30
    batch_size: int = 32
    learning_rate: float = 0.001
    hidden_dim: int = 0
    disagreement_oversample_factor: int = 3
    reference_bank_path: str = ""
    reference_top_k: int = 3
    device: str = "auto"



@dataclass(slots=True, frozen=True)
class RankerRunInfo:
    """Summary metadata for one saved ranker run directory."""
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
    profile_key: str
    profile_label: str
    fit_diagnosis: "RankerFitDiagnosis"
    is_active: bool = False
    is_legacy: bool = False
    disagreement_pair_labels: int = 0


@dataclass(slots=True, frozen=True)
class GeneralTrainingPoolStatus:
    """Status snapshot for the shared General Use label pool."""
    paths: AITrainingPaths
    pairwise_labels: int
    cluster_labels: int
    source_folders: int
    labels_added_since_train: int = 0
    needs_retrain: bool = False
    guidance_text: str = ""
    cached: bool = False
    disagreement_pair_labels: int = 0


@dataclass(slots=True, frozen=True)
class TrainingSourceInfo:
    """One registered source folder that can contribute to General Use training."""
    namespace: str
    folder: str
    display_name: str
    enabled: bool
    pairwise_labels: int
    cluster_labels: int
    disagreement_pair_labels: int
    prepared_ready: bool
    labels_dir: str
    artifacts_dir: str



def build_ai_training_paths(folder: str | Path) -> AITrainingPaths:
    """Resolve the central per-source training workspace derived from an image folder."""
    workflow_paths = build_ai_workflow_paths(folder)
    source_root = _central_label_source_root(workflow_paths.folder)
    labeling_artifacts_dir = source_root / LABELING_ARTIFACTS_DIR_NAME
    labels_dir = source_root / LABELS_DIR_NAME
    training_dir = source_root / TRAINING_DIR_NAME
    training_runs_dir = training_dir / TRAINING_RUNS_DIR_NAME
    evaluation_dir = source_root / EVALUATION_DIR_NAME
    reference_bank_dir = source_root / REFERENCE_BANK_DIR_NAME
    report_dir = source_root / REPORT_DIR_NAME
    return AITrainingPaths(
        folder=workflow_paths.folder,
        hidden_root=source_root,
        artifacts_dir=source_root / ARTIFACTS_DIR_NAME,
        report_dir=report_dir,
        ranked_export_path=report_dir / "ranked_clusters_export.csv",
        html_report_path=report_dir / "ranked_clusters_report.html",
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


def list_registered_training_sources(*, enabled_only: bool = False) -> tuple[TrainingSourceInfo, ...]:
    """Return central label sources with label/prepared counts for UI selection."""

    source_root = _central_label_sources_root()
    if not source_root.exists():
        return ()

    sources: list[TrainingSourceInfo] = []
    seen: set[str] = set()
    for manifest_path in sorted(source_root.glob(f"*/{LABEL_SOURCE_MANIFEST_FILENAME}")):
        payload = _read_json_dict(manifest_path)
        folder_text = str(payload.get("folder") or "").strip()
        namespace = str(payload.get("namespace") or manifest_path.parent.name).strip()
        if not folder_text or not namespace:
            continue
        try:
            normalized = str(Path(folder_text).expanduser().resolve())
        except OSError:
            normalized = folder_text
        key = normalized.casefold()
        if key in seen:
            continue
        seen.add(key)
        enabled = bool(payload.get("enabled", True))
        if enabled_only and not enabled:
            continue
        paths = build_ai_training_paths(normalized)
        pairwise_count, cluster_count = count_label_records(paths)
        disagreement_count = count_disagreement_pair_labels(paths)
        prepared_ready = ai_training_artifacts_ready(paths) and not ai_training_source_needs_prepare(paths.folder)
        if pairwise_count <= 0 and cluster_count <= 0 and disagreement_count <= 0 and not prepared_ready:
            continue
        sources.append(
            TrainingSourceInfo(
                namespace=namespace,
                folder=normalized,
                display_name=str(payload.get("display_name") or Path(normalized).name),
                enabled=enabled,
                pairwise_labels=pairwise_count,
                cluster_labels=cluster_count,
                disagreement_pair_labels=disagreement_count,
                prepared_ready=prepared_ready,
                labels_dir=str(paths.labels_dir),
                artifacts_dir=str(paths.artifacts_dir),
            )
        )
    return tuple(sorted(sources, key=lambda source: source.folder.casefold()))


def set_registered_training_source_enabled(namespace: str, enabled: bool) -> None:
    """Persist whether a registered source contributes to the General Use pool."""

    namespace_text = str(namespace or "").strip()
    if not namespace_text:
        return
    manifest_path = _central_label_sources_root() / namespace_text / LABEL_SOURCE_MANIFEST_FILENAME
    payload = _read_json_dict(manifest_path)
    if not payload:
        return
    payload["enabled"] = bool(enabled)
    payload["updated_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    try:
        manifest_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except OSError:
        return
    _write_label_sources_index()


def build_general_ai_training_paths() -> AITrainingPaths:
    """Resolve the shared General Use training workspace under app data."""
    root = app_data_root() / GENERAL_TRAINING_ROOT_DIR_NAME / GENERAL_TRAINING_PROFILE_DIR_NAME
    workflow_paths = AIWorkflowPaths(
        folder=root,
        hidden_root=root,
        artifacts_dir=root / ARTIFACTS_DIR_NAME,
        report_dir=root / REPORT_DIR_NAME,
        ranked_export_path=(root / REPORT_DIR_NAME) / "ranked_clusters_export.csv",
        html_report_path=(root / REPORT_DIR_NAME) / "ranked_clusters_report.html",
        semantic_export_path=(root / REPORT_DIR_NAME) / "semantic_classifications.csv",
        semantic_summary_path=(root / REPORT_DIR_NAME) / "semantic_classification_summary.json",
    )
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
    """Ensure the central per-source training workspace exists and return its paths."""
    paths = build_ai_training_paths(folder)
    paths.hidden_root.mkdir(parents=True, exist_ok=True)
    paths.artifacts_dir.mkdir(parents=True, exist_ok=True)
    paths.report_dir.mkdir(parents=True, exist_ok=True)
    paths.labeling_artifacts_dir.mkdir(parents=True, exist_ok=True)
    paths.labels_dir.mkdir(parents=True, exist_ok=True)
    paths.training_dir.mkdir(parents=True, exist_ok=True)
    paths.training_runs_dir.mkdir(parents=True, exist_ok=True)
    paths.evaluation_dir.mkdir(parents=True, exist_ok=True)
    paths.reference_bank_dir.mkdir(parents=True, exist_ok=True)
    _register_training_label_source(paths)
    _migrate_legacy_folder_labels(paths)
    return paths



def ai_training_artifacts_ready(paths: AITrainingPaths) -> bool:
    """Return whether embeddings and cluster artifacts exist for training/eval."""
    required = (
        paths.artifacts_dir / "images.csv",
        paths.artifacts_dir / "embeddings.npy",
        paths.artifacts_dir / "image_ids.json",
        paths.artifacts_dir / "clusters.csv",
    )
    return all(path.exists() for path in required)




def ai_training_source_needs_prepare(folder: str | Path) -> bool:
    """Return whether a source has labels missing from prepared training artifacts."""

    paths = build_ai_training_paths(folder)
    pairwise_count, cluster_count = count_label_records(paths)
    disagreement_count = count_disagreement_pair_labels(paths)
    if pairwise_count <= 0 and cluster_count <= 0 and disagreement_count <= 0:
        return False
    if not ai_training_artifacts_ready(paths):
        return True
    labeled_image_ids = _collect_labeled_training_image_ids(paths)
    if not labeled_image_ids:
        return False
    artifact_image_ids = set(_read_json_list(paths.artifacts_dir / "image_ids.json"))
    if not labeled_image_ids.issubset(artifact_image_ids):
        return True
    labeled_cluster_ids = _collect_labeled_training_cluster_ids(paths)
    if not labeled_cluster_ids:
        return False
    artifact_cluster_ids = {
        str(row.get("cluster_id") or "").strip()
        for row in _read_csv_rows(paths.artifacts_dir / "clusters.csv")
        if str(row.get("cluster_id") or "").strip()
    }
    return not labeled_cluster_ids.issubset(artifact_cluster_ids)



def count_label_records(paths: AITrainingPaths) -> tuple[int, int]:
    """Count pairwise and cluster labels currently stored in a workspace."""
    pairwise_count = _count_usable_pairwise_label_records(paths.pairwise_labels_path)
    cluster_count = _count_jsonl_lines(paths.cluster_labels_path)
    if _legacy_label_migration_suppressed(paths):
        return pairwise_count, cluster_count
    legacy_labels_dir = build_ai_workflow_paths(paths.folder).hidden_root / LABELS_DIR_NAME
    try:
        is_legacy_same = _same_path(legacy_labels_dir, paths.labels_dir)
    except OSError:
        is_legacy_same = False
    if not is_legacy_same:
        pairwise_count = max(pairwise_count, _count_usable_pairwise_label_records(legacy_labels_dir / PAIRWISE_LABELS_FILENAME))
        cluster_count = max(cluster_count, _count_jsonl_lines(legacy_labels_dir / CLUSTER_LABELS_FILENAME))
    return pairwise_count, cluster_count


def count_disagreement_pair_labels(paths: AITrainingPaths) -> int:
    """Count usable pairwise labels captured from AI/user disagreement events."""
    count = _count_pairwise_labels_by_source(paths.pairwise_labels_path, AI_DISAGREEMENT_SOURCE_MODE)
    if _legacy_label_migration_suppressed(paths):
        return count
    legacy_labels_dir = build_ai_workflow_paths(paths.folder).hidden_root / LABELS_DIR_NAME
    try:
        is_legacy_same = _same_path(legacy_labels_dir, paths.labels_dir)
    except OSError:
        is_legacy_same = False
    if not is_legacy_same:
        count = max(
            count,
            _count_pairwise_labels_by_source(
                legacy_labels_dir / PAIRWISE_LABELS_FILENAME,
                AI_DISAGREEMENT_SOURCE_MODE,
            ),
        )
    return count



def preview_general_training_pool(
    source_folders: list[str] | tuple[str, ...],
    *,
    reference_run: RankerRunInfo | None = None,
) -> GeneralTrainingPoolStatus:
    """Compute shared-pool counts without rebuilding the pooled artifacts."""
    paths = build_general_ai_training_paths()
    sources = _collect_general_training_sources(source_folders)
    pairwise_total = sum(source["pairwise_labels"] for source in sources)
    cluster_total = sum(source["cluster_labels"] for source in sources)
    disagreement_total = sum(source["disagreement_pair_labels"] for source in sources)
    return _general_training_pool_status(
        paths=paths,
        pairwise_total=pairwise_total,
        cluster_total=cluster_total,
        disagreement_total=disagreement_total,
        source_count=len(sources),
        reference_run=reference_run,
        cached=False,
    )



def resolve_trained_checkpoint(paths: AITrainingPaths) -> Path | None:
    """Resolve the preferred checkpoint for scoring or evaluation in this workspace."""
    active_selection = _read_active_ranker_selection(paths)
    active_checkpoint = _checkpoint_from_active_selection(paths, active_selection)
    if active_checkpoint is not None:
        return active_checkpoint
    for run in list_ranker_runs(paths):
        if run.checkpoint_path is not None:
            return run.checkpoint_path
    return resolve_legacy_trained_checkpoint(paths)


def resolve_legacy_trained_checkpoint(paths: AITrainingPaths) -> Path | None:
    """Fallback to the pre-run-directory checkpoint layout used by older builds."""
    for candidate in (paths.best_checkpoint_path, paths.last_checkpoint_path):
        if candidate.exists():
            return candidate
    return None


def list_ranker_runs(paths: AITrainingPaths) -> tuple[RankerRunInfo, ...]:
    """List all saved ranker runs, newest first, including the legacy layout."""
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


def find_ranker_run_by_checkpoint(paths: AITrainingPaths, checkpoint_path: str | Path | None) -> RankerRunInfo | None:
    """Find the recorded run metadata that owns a checkpoint path."""
    if checkpoint_path is None:
        return None
    candidate = Path(checkpoint_path).expanduser()
    for run in list_ranker_runs(paths):
        if run.checkpoint_path is not None and _same_path(candidate, run.checkpoint_path):
            return run
    return None



















def _coerce_float(value: object) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _coerce_int(value: object) -> int:
    try:
        return int(value) if value is not None else 0
    except (TypeError, ValueError):
        return 0










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
    profile_key, profile_label = normalize_ranker_profile(
        metadata.get("profile_key") or metadata.get("profile_label")
    )
    history_path = (run_dir / TRAINING_HISTORY_FILENAME) if (run_dir / TRAINING_HISTORY_FILENAME).exists() else None
    metrics_path = (run_dir / TRAINING_METRICS_FILENAME) if (run_dir / TRAINING_METRICS_FILENAME).exists() else None
    fit_diagnosis = load_ranker_fit_diagnosis(
        metrics_path,
        history_path,
        num_epochs=_nested_int(resolved_config, "num_epochs"),
    )

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
        metrics_path=metrics_path,
        history_path=history_path,
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
        profile_key=profile_key,
        profile_label=profile_label,
        fit_diagnosis=fit_diagnosis,
        is_active=bool(active_checkpoint and checkpoint_path and _same_path(active_checkpoint, checkpoint_path)),
        is_legacy=False,
        disagreement_pair_labels=int(
            metadata.get("disagreement_pair_labels")
            or metrics.get("label_summary", {}).get("source_mode_distribution", {}).get(AI_DISAGREEMENT_SOURCE_MODE, 0)
            or 0
        ),
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
    profile_key, profile_label = normalize_ranker_profile(DEFAULT_RANKER_PROFILE_KEY)
    fit_diagnosis = load_ranker_fit_diagnosis(
        metrics_path,
        history_path,
        num_epochs=_nested_int(resolved_config, "num_epochs"),
    )
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
        profile_key=profile_key,
        profile_label=profile_label,
        fit_diagnosis=fit_diagnosis,
        is_active=bool(active_checkpoint and checkpoint_path and _same_path(active_checkpoint, checkpoint_path)),
        is_legacy=True,
        disagreement_pair_labels=int(
            metrics.get("label_summary", {}).get("source_mode_distribution", {}).get(AI_DISAGREEMENT_SOURCE_MODE, 0)
            or 0
        ),
    )


def _general_training_pool_status(
    *,
    paths: AITrainingPaths,
    pairwise_total: int,
    cluster_total: int,
    disagreement_total: int,
    source_count: int,
    reference_run: RankerRunInfo | None,
    cached: bool,
) -> GeneralTrainingPoolStatus:
    previous_pairwise = reference_run.pairwise_labels if reference_run is not None else 0
    previous_cluster = reference_run.cluster_labels if reference_run is not None else 0
    labels_added = max(0, pairwise_total - previous_pairwise) + max(0, cluster_total - previous_cluster)
    needs_retrain = labels_added >= GENERAL_RETRAIN_RECOMMENDATION_MIN_LABELS
    if source_count <= 0 or (pairwise_total <= 0 and cluster_total <= 0):
        guidance_text = "General Use has no pooled labels yet. Collect labels in one or more folders first."
    elif reference_run is None:
        guidance_text = (
            f"General Use can train from {source_count} labeled folder(s): "
            f"{pairwise_total} pairwise, {cluster_total} cluster, "
            f"and {disagreement_total} AI dispute labels."
        )
    elif labels_added <= 0:
        guidance_text = "General Use is up to date with the pooled labels."
    elif needs_retrain:
        guidance_text = (
            f"General Use has {labels_added} new labels since "
            f"{reference_run.display_name}. Retraining is recommended."
        )
    else:
        guidance_text = (
            f"General Use has {labels_added} new labels since "
            f"{reference_run.display_name}. Wait for a slightly larger batch unless ranking slipped."
        )
    return GeneralTrainingPoolStatus(
        paths=paths,
        pairwise_labels=pairwise_total,
        cluster_labels=cluster_total,
        source_folders=source_count,
        labels_added_since_train=labels_added,
        needs_retrain=needs_retrain,
        guidance_text=guidance_text,
        cached=cached,
        disagreement_pair_labels=disagreement_total,
    )


def _central_label_sources_root() -> Path:
    """Return the app-data root that stores per-folder label streams."""

    return app_data_root() / GENERAL_TRAINING_ROOT_DIR_NAME / LABEL_SOURCE_ROOT_DIR_NAME


def _central_label_source_root(folder: str | Path) -> Path:
    """Return the app-data workspace for labels collected from one image folder."""

    normalized = str(Path(folder).expanduser().resolve())
    return _central_label_sources_root() / _general_source_namespace(normalized)


def _register_training_label_source(paths: AITrainingPaths) -> None:
    """Persist the source-folder mapping for central label discovery."""

    source_root = paths.labels_dir.parent
    source_root.mkdir(parents=True, exist_ok=True)
    manifest_path = source_root / LABEL_SOURCE_MANIFEST_FILENAME
    existing = _read_json_dict(manifest_path)
    enabled = existing.get("enabled")
    if not isinstance(enabled, bool):
        enabled = True
    payload = {
        "folder": str(paths.folder),
        "display_name": paths.folder.name,
        "namespace": _general_source_namespace(str(paths.folder)),
        "enabled": enabled,
        "labels_dir": str(paths.labels_dir),
        "labeling_artifacts_dir": str(paths.labeling_artifacts_dir),
        "artifacts_dir": str(paths.artifacts_dir),
        "training_dir": str(paths.training_dir),
        "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    try:
        manifest_path.write_text(
            json.dumps(payload, indent=2),
            encoding="utf-8",
        )
    except OSError:
        return
    _write_label_sources_index()


def _write_label_sources_index() -> None:
    """Refresh the aggregate source index used by future training-source filters."""

    source_root = _central_label_sources_root()
    sources: list[dict[str, object]] = []
    if source_root.exists():
        for manifest_path in sorted(source_root.glob(f"*/{LABEL_SOURCE_MANIFEST_FILENAME}")):
            payload = _read_json_dict(manifest_path)
            folder_text = str(payload.get("folder") or "").strip()
            namespace = str(payload.get("namespace") or manifest_path.parent.name).strip()
            if not folder_text or not namespace:
                continue
            sources.append(
                {
                    "folder": folder_text,
                    "display_name": str(payload.get("display_name") or Path(folder_text).name),
                    "namespace": namespace,
                    "enabled": bool(payload.get("enabled", True)),
                    "labels_dir": str(payload.get("labels_dir") or ""),
                    "labeling_artifacts_dir": str(payload.get("labeling_artifacts_dir") or ""),
                    "artifacts_dir": str(payload.get("artifacts_dir") or ""),
                    "training_dir": str(payload.get("training_dir") or ""),
                    "updated_at": str(payload.get("updated_at") or ""),
                }
            )
    try:
        source_root.mkdir(parents=True, exist_ok=True)
        (source_root / LABEL_SOURCES_INDEX_FILENAME).write_text(
            json.dumps({"sources": sources}, indent=2),
            encoding="utf-8",
        )
    except OSError:
        return


def _migrate_legacy_folder_labels(paths: AITrainingPaths) -> None:
    """Copy older folder-local labels into the central label-source workspace."""

    if _legacy_label_migration_suppressed(paths):
        return
    legacy_labels_dir = build_ai_workflow_paths(paths.folder).hidden_root / LABELS_DIR_NAME
    try:
        if _same_path(legacy_labels_dir, paths.labels_dir):
            return
    except OSError:
        pass
    if not legacy_labels_dir.exists():
        return

    migrated_any_file = False
    for filename in (PAIRWISE_LABELS_FILENAME, CLUSTER_LABELS_FILENAME):
        source = legacy_labels_dir / filename
        destination = paths.labels_dir / filename
        if source.exists():
            migrated_any_file = True
            _merge_jsonl_file(source, destination)
    if not migrated_any_file:
        return
    try:
        (paths.labels_dir / LEGACY_LABEL_MIGRATION_MARKER).write_text(
            "legacy folder-local labels migrated or intentionally skipped\n",
            encoding="utf-8",
        )
    except OSError:
        return


def _legacy_label_migration_suppressed(paths: AITrainingPaths) -> bool:
    """Return whether old folder-local labels should be ignored after deletion."""

    marker = paths.labels_dir / LEGACY_LABEL_MIGRATION_MARKER
    try:
        text = marker.read_text(encoding="utf-8", errors="ignore").casefold()
    except OSError:
        return False
    return "suppressed after label deletion" in text


def _merge_jsonl_file(source: Path, destination: Path) -> None:
    """Append missing JSONL lines from source to destination without duplicating exact lines."""

    try:
        source_lines = [line.strip() for line in source.read_text(encoding="utf-8").splitlines() if line.strip()]
    except OSError:
        return
    if not source_lines:
        return
    existing: set[str] = set()
    if destination.exists():
        try:
            existing = {
                line.strip()
                for line in destination.read_text(encoding="utf-8").splitlines()
                if line.strip()
            }
        except OSError:
            existing = set()
    missing = [line for line in source_lines if line not in existing]
    if not missing:
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("a", encoding="utf-8") as handle:
        for line in missing:
            handle.write(line)
            handle.write("\n")



def _collect_labeled_training_image_ids(paths: AITrainingPaths) -> set[str]:
    """Return image IDs that have direct pairwise or cluster labels."""

    image_ids: set[str] = set()
    for record in _iter_jsonl_records(paths.pairwise_labels_path):
        if _is_ambiguous_pairwise_record(record):
            continue
        for key in ("image_a_id", "image_b_id", "preferred_image_id"):
            value = str(record.get(key) or "").strip()
            if value:
                image_ids.add(value)
    for record in _iter_jsonl_records(paths.cluster_labels_path):
        for key in ("best_image_ids", "acceptable_image_ids", "reject_image_ids"):
            values = record.get(key)
            if isinstance(values, list):
                image_ids.update(str(value).strip() for value in values if str(value or "").strip())
    return image_ids


def _collect_labeled_training_cluster_ids(paths: AITrainingPaths) -> set[str]:
    """Return cluster IDs referenced by saved cluster labels."""

    cluster_ids: set[str] = set()
    for record in _iter_jsonl_records(paths.cluster_labels_path):
        value = str(record.get("cluster_id") or "").strip()
        if value:
            cluster_ids.add(value)
    return cluster_ids





def _collect_general_training_sources(source_folders: list[str] | tuple[str, ...]) -> list[dict[str, object]]:
    collected: list[dict[str, object]] = []
    seen: set[str] = set()
    for folder in source_folders:
        normalized_folder = _normalize_source_folder(folder)
        if not normalized_folder:
            continue
        folder_key = normalized_folder.casefold()
        if folder_key in seen:
            continue
        seen.add(folder_key)
        paths = build_ai_training_paths(normalized_folder)
        if not ai_training_artifacts_ready(paths):
            continue
        _register_training_label_source(paths)
        _migrate_legacy_folder_labels(paths)
        pairwise_count, cluster_count = count_label_records(paths)
        disagreement_count = count_disagreement_pair_labels(paths)
        if pairwise_count <= 0 and cluster_count <= 0:
            continue
        collected.append(
            {
                "folder": normalized_folder,
                "paths": paths,
                "namespace": _general_source_namespace(normalized_folder),
                "pairwise_labels": pairwise_count,
                "cluster_labels": cluster_count,
                "disagreement_pair_labels": disagreement_count,
                "signatures": tuple(
                    _path_signature(candidate)
                    for candidate in (
                        paths.artifacts_dir / "images.csv",
                        paths.artifacts_dir / "embeddings.npy",
                        paths.artifacts_dir / "image_ids.json",
                        paths.artifacts_dir / "clusters.csv",
                        paths.pairwise_labels_path,
                        paths.cluster_labels_path,
                    )
                ),
            }
        )
    return collected






def _path_signature(path: Path) -> tuple[str, int, int]:
    try:
        stat_result = path.stat()
    except OSError:
        return str(path), -1, -1
    return str(path), int(stat_result.st_size), int(stat_result.st_mtime_ns)


def _general_source_namespace(folder: str) -> str:
    normalized = _normalize_source_folder(folder)
    return hashlib.sha1(normalized.encode("utf-8"), usedforsecurity=False).hexdigest()[:16]



def _normalize_source_folder(folder: str | Path) -> str:
    try:
        candidate = Path(folder).expanduser().resolve()
    except OSError:
        return ""
    return str(candidate) if candidate.exists() else ""




def _read_json_dict(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    try:
        raw = path.read_bytes()
    except OSError:
        return {}
    data = None
    for encoding in ("utf-8", "utf-8-sig", "utf-16"):
        try:
            data = json.loads(raw.decode(encoding))
            break
        except (UnicodeDecodeError, ValueError, TypeError):
            continue
    if data is None:
        return {}
    return data if isinstance(data, dict) else {}


def _read_json_list(path: Path) -> list[str]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return []
    if not isinstance(data, list):
        return []
    return [str(item) for item in data if str(item or "").strip()]


def _read_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            return [{str(key): str(value or "") for key, value in row.items()} for row in csv.DictReader(handle) if row]
    except (OSError, csv.Error):
        return []


def _read_training_history_rows(path: Path | None) -> list[dict[str, object]]:
    if path is None or not path.exists():
        return []
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            return [dict(row) for row in csv.DictReader(handle) if row]
    except (OSError, csv.Error):
        return []


def load_ranker_fit_diagnosis(
    metrics_path: Path | None,
    history_path: Path | None,
    *,
    num_epochs: int | None = None,
) -> RankerFitDiagnosis:
    """Load metrics/history files and translate them into a user-facing fit summary."""
    metrics = _read_json_dict(metrics_path) if metrics_path is not None else {}
    history_rows = _read_training_history_rows(history_path)
    return diagnose_ranker_fit(metrics=metrics, history_rows=history_rows, num_epochs=num_epochs)


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



def _same_path(left: Path, right: Path) -> bool:
    try:
        return left.resolve() == right.resolve()
    except OSError:
        return str(left).casefold() == str(right).casefold()



def _count_jsonl_lines(path: Path) -> int:
    if not path.exists():
        return 0
    try:
        with path.open("r", encoding="utf-8") as handle:
            return sum(1 for line in handle if line.strip())
    except OSError:
        return 0


def _count_usable_pairwise_label_records(path: Path) -> int:
    """Count pairwise labels that produce a preference for training/evaluation."""

    count = 0
    for record in _iter_jsonl_records(path):
        if _is_non_preference_pairwise_record(record):
            continue
        decision = str(record.get("decision") or "").strip().lower()
        preferred_image_id = str(record.get("preferred_image_id") or "").strip()
        image_a_id = str(record.get("image_a_id") or "").strip()
        image_b_id = str(record.get("image_b_id") or "").strip()
        if image_a_id and image_b_id and (preferred_image_id or decision in {"left_better", "right_better"}):
            count += 1
    return count


def _count_pairwise_labels_by_source(path: Path, source_mode: str) -> int:
    if not path.exists():
        return 0
    count = 0
    for record in _iter_jsonl_records(path):
        if _is_non_preference_pairwise_record(record):
            continue
        if str(record.get("source_mode") or "") == source_mode:
            count += 1
    return count


def _is_ambiguous_pairwise_record(record: dict[str, object]) -> bool:
    """Return whether a saved pairwise record is an explicit tie/skip."""

    return str(record.get("decision") or "").strip().lower() in {"tie", "skip"}


def _is_non_preference_pairwise_record(record: dict[str, object]) -> bool:
    """Return whether a pairwise record should not produce preference pairs."""

    return str(record.get("decision") or "").strip().lower() in {"tie", "skip", "both_reject"}











def _iter_jsonl_records(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    records: list[dict[str, object]] = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                text = line.strip()
                if not text:
                    continue
                try:
                    payload = json.loads(text)
                except (TypeError, ValueError):
                    continue
                if isinstance(payload, dict):
                    records.append(payload)
    except OSError:
        return []
    return records

