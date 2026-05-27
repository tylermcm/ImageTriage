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
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import re

import numpy as np
from PySide6.QtCore import QObject, QRunnable, Signal

from .ai_workflow import (
    AIWorkflowPaths,
    AIWorkflowRuntime,
    ARTIFACTS_DIR_NAME,
    REPORT_DIR_NAME,
    _parse_tqdm_progress,
    _resolve_stage_command,
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
from .perf import perf_logger
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


@dataclass(slots=True)
class ReferenceBankBuildOptions:
    """Options used when building the optional nearest-neighbor reference bank."""
    reference_dir: str
    output_dir: str
    batch_size: int = 8
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


class AITrainingTaskSignals(QObject):
    """Common signal contract shared by AI training worker tasks."""
    started = Signal(int)
    stage = Signal(int, int, str)
    progress = Signal(int, int, str)
    log = Signal(str)
    finished = Signal(object)
    failed = Signal(str)


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


def discover_registered_training_source_folders() -> tuple[str, ...]:
    """Return folders that have central label-source manifests."""

    return tuple(source.folder for source in list_registered_training_sources(enabled_only=True))


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


def prepare_general_ai_training_workspace() -> AITrainingPaths:
    """Ensure the shared General Use workspace exists and return its paths."""
    paths = build_general_ai_training_paths()
    paths.hidden_root.mkdir(parents=True, exist_ok=True)
    paths.artifacts_dir.mkdir(parents=True, exist_ok=True)
    paths.report_dir.mkdir(parents=True, exist_ok=True)
    paths.labeling_artifacts_dir.mkdir(parents=True, exist_ok=True)
    paths.labels_dir.mkdir(parents=True, exist_ok=True)
    paths.training_dir.mkdir(parents=True, exist_ok=True)
    paths.training_runs_dir.mkdir(parents=True, exist_ok=True)
    paths.evaluation_dir.mkdir(parents=True, exist_ok=True)
    paths.reference_bank_dir.mkdir(parents=True, exist_ok=True)
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


def ai_training_evaluation_issues(paths: AITrainingPaths) -> tuple[str, ...]:
    """Return blocking issues that would make ranker evaluation misleading."""

    issues: list[str] = []
    pairwise_count, cluster_count = count_label_records(paths)
    disagreement_count = count_disagreement_pair_labels(paths)
    if pairwise_count <= 0 and cluster_count <= 0 and disagreement_count <= 0:
        issues.append("No saved pairwise, cluster, or AI dispute labels were found.")

    required = (
        paths.artifacts_dir / "images.csv",
        paths.artifacts_dir / "embeddings.npy",
        paths.artifacts_dir / "image_ids.json",
        paths.artifacts_dir / "clusters.csv",
    )
    missing = [path.name for path in required if not path.exists()]
    if missing:
        issues.append(
            "Prepared training artifacts are missing: "
            + ", ".join(missing)
            + ". Run Prepare Training Data for this source."
        )
        return tuple(issues)

    labeled_image_ids = _collect_labeled_training_image_ids(paths)
    artifact_image_ids = set(_read_json_list(paths.artifacts_dir / "image_ids.json"))
    missing_image_ids = sorted(labeled_image_ids - artifact_image_ids)
    if missing_image_ids:
        issues.append(
            f"Prepared artifacts are stale: {len(missing_image_ids)} labeled image(s) are missing from image_ids.json. "
            "Run Prepare Training Data for this source."
        )

    labeled_cluster_ids = _collect_labeled_training_cluster_ids(paths)
    if cluster_count > 0 and not labeled_cluster_ids:
        issues.append("Cluster label records exist, but none contain usable cluster IDs.")
    if labeled_cluster_ids:
        artifact_cluster_ids = {
            str(row.get("cluster_id") or "").strip()
            for row in _read_csv_rows(paths.artifacts_dir / "clusters.csv")
            if str(row.get("cluster_id") or "").strip()
        }
        missing_cluster_ids = sorted(labeled_cluster_ids - artifact_cluster_ids)
        if missing_cluster_ids:
            preview = ", ".join(missing_cluster_ids[:5])
            if len(missing_cluster_ids) > 5:
                preview += f", +{len(missing_cluster_ids) - 5} more"
            issues.append(
                f"Prepared cluster artifacts are stale: {len(missing_cluster_ids)} labeled cluster(s) are missing from clusters.csv "
                f"({preview}). Run Prepare Training Data for this source."
            )

    return tuple(issues)


def format_ai_training_evaluation_issues(folder: str | Path, issues: tuple[str, ...]) -> str:
    """Format evaluation blockers for a user-facing dialog or task failure."""

    folder_text = str(folder)
    if not issues:
        return ""
    joined = "\n".join(f"- {issue}" for issue in issues)
    return f"Cannot evaluate this training source yet:\n{folder_text}\n\n{joined}"


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


def labeling_artifacts_ready(paths: AITrainingPaths) -> bool:
    """Return whether the label-collection app can open against prepared artifacts."""
    required = (
        paths.labeling_metadata_path,
        paths.labeling_image_ids_path,
        paths.labeling_clusters_path,
    )
    return all(path.exists() for path in required)


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


def count_ai_disagreement_events(events: list[dict[str, object]] | tuple[dict[str, object], ...]) -> int:
    """Count captured AI disagreement feedback events without requiring a training pair."""
    count = 0
    for event in events:
        payload = event.get("payload")
        payload_dict = payload if isinstance(payload, dict) else {}
        if str(event.get("source_mode") or "") == AI_DISAGREEMENT_SOURCE_MODE:
            count += 1
            continue
        if str(payload_dict.get("disagreement_level") or "").strip():
            count += 1
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


def prepare_general_training_pool(
    source_folders: list[str] | tuple[str, ...],
    *,
    reference_run: RankerRunInfo | None = None,
) -> GeneralTrainingPoolStatus:
    """Build or reuse the shared General Use training pool from source folders."""
    paths = prepare_general_ai_training_workspace()
    sources = _collect_general_training_sources(source_folders)
    pairwise_total = sum(source["pairwise_labels"] for source in sources)
    cluster_total = sum(source["cluster_labels"] for source in sources)
    disagreement_total = sum(source["disagreement_pair_labels"] for source in sources)
    if not sources:
        _write_general_pool_manifest(
            paths,
            {
                "cache_key": "",
                "source_folders": [],
                "pairwise_labels": 0,
                "cluster_labels": 0,
                "disagreement_pair_labels": 0,
                "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            },
        )
        return _general_training_pool_status(
            paths=paths,
            pairwise_total=0,
            cluster_total=0,
            disagreement_total=0,
            source_count=0,
            reference_run=reference_run,
            cached=False,
        )

    cache_key = _general_pool_cache_key(sources)
    manifest = _read_json_dict(_general_pool_manifest_path(paths))
    if (
        str(manifest.get("cache_key") or "") == cache_key
        and ai_training_artifacts_ready(paths)
        and paths.pairwise_labels_path.exists()
        and paths.cluster_labels_path.exists()
    ):
        return _general_training_pool_status(
            paths=paths,
            pairwise_total=pairwise_total,
            cluster_total=cluster_total,
            disagreement_total=disagreement_total,
            source_count=len(sources),
            reference_run=reference_run,
            cached=True,
        )

    _rebuild_general_training_pool(paths, sources)
    _write_general_pool_manifest(
        paths,
        {
            "cache_key": cache_key,
            "source_folders": [str(source["folder"]) for source in sources],
            "pairwise_labels": pairwise_total,
            "cluster_labels": cluster_total,
            "disagreement_pair_labels": disagreement_total,
            "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        },
    )
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


def set_active_ranker_selection(
    paths: AITrainingPaths,
    *,
    checkpoint_path: str | Path,
    run_id: str = "",
    display_name: str = "",
    profile_key: str = DEFAULT_RANKER_PROFILE_KEY,
    profile_label: str = "",
) -> None:
    """Persist which checkpoint should be treated as active for this workspace."""
    candidate = Path(checkpoint_path).expanduser().resolve()
    resolved_profile_key, resolved_profile_label = normalize_ranker_profile(profile_key or profile_label)
    payload = {
        "checkpoint_path": str(candidate),
        "run_id": run_id.strip(),
        "display_name": display_name.strip() or candidate.parent.name,
        "profile_key": resolved_profile_key,
        "profile_label": resolved_profile_label,
        "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    paths.active_ranker_path.parent.mkdir(parents=True, exist_ok=True)
    paths.active_ranker_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def clear_active_ranker_selection(paths: AITrainingPaths) -> None:
    """Remove the explicit active-checkpoint override for a workspace."""
    try:
        if paths.active_ranker_path.exists():
            paths.active_ranker_path.unlink()
    except OSError:
        return


def training_output_dir_for_checkpoint(paths: AITrainingPaths, checkpoint_path: str | Path) -> Path:
    """Resolve the run directory that produced a checkpoint."""
    checkpoint = Path(checkpoint_path).expanduser().resolve()
    if checkpoint.parent == paths.training_dir:
        return paths.training_dir
    return checkpoint.parent


def evaluation_output_dir_for_checkpoint(paths: AITrainingPaths, checkpoint_path: str | Path) -> Path:
    """Resolve where evaluation outputs for a checkpoint should be written."""
    training_output_dir = training_output_dir_for_checkpoint(paths, checkpoint_path)
    if training_output_dir == paths.training_dir:
        return paths.evaluation_dir
    return training_output_dir / EVALUATION_DIR_NAME


def create_ranker_run(paths: AITrainingPaths, requested_name: str = "") -> tuple[str, str, Path]:
    """Allocate a unique run id, display name, and directory for a new training run."""
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
    near_identical_threshold: float | None = None,
) -> tuple[str, ...]:
    """Build the child-process command line for the label-collection UI."""
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
    if near_identical_threshold is not None:
        command.extend(["--near-identical-threshold", f"{float(near_identical_threshold):.3f}"])
    return tuple(command)


def launch_labeling_app(
    runtime: AIWorkflowRuntime,
    *,
    folder: str | Path,
    annotator_id: str = "",
    artifacts_dir: str | Path | None = None,
    near_identical_threshold: float | None = None,
    ready_file_path: str | Path | None = None,
    appearance_mode: str | None = None,
    parent_pid: int | None = None,
    sync_file_path: str | Path | None = None,
) -> subprocess.Popen[str]:
    """Spawn the label-collection UI as a detached child process."""
    logger = perf_logger()
    start = time.perf_counter()
    command = list(
        build_labeling_command(
            runtime,
            folder=folder,
            annotator_id=annotator_id,
            artifacts_dir=artifacts_dir,
            near_identical_threshold=near_identical_threshold,
        )
    )
    if logger.enabled:
        logger.log(
            "labeling.launch.command_ready",
            folder=str(folder),
            artifacts_dir=str(artifacts_dir or ""),
            near_duplicate_threshold=near_identical_threshold if near_identical_threshold is not None else "",
            command=" ".join(command[:4]),
        )
    env = dict(os.environ)
    env["PYTHONUNBUFFERED"] = "1"
    env.setdefault("IMAGE_TRIAGE_HOST_ROOT", str(Path(__file__).resolve().parents[1]))
    if logger.enabled and logger.path is not None:
        env["IMAGE_TRIAGE_PERFORMANCE_LOG_PATH"] = str(logger.path)
    if appearance_mode:
        env["IMAGE_TRIAGE_APPEARANCE_MODE"] = appearance_mode
    if parent_pid is not None and parent_pid > 0:
        env["IMAGE_TRIAGE_PARENT_PID"] = str(parent_pid)
    if ready_file_path is not None:
        env[LABELING_READY_FILE_ENV] = str(Path(ready_file_path).expanduser().resolve())
    if sync_file_path is not None:
        env["IMAGE_TRIAGE_SYNC_FILE"] = str(Path(sync_file_path).expanduser().resolve())
    process = subprocess.Popen(
        command,
        cwd=str(runtime.engine_root),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    logger.duration(
        "labeling.launch.popen",
        (time.perf_counter() - start) * 1000.0,
        folder=str(folder),
        pid=int(getattr(process, "pid", 0) or 0),
    )
    return process


def _read_labeling_startup_state(path: Path) -> tuple[str, str]:
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return "", ""
    if not text:
        return "", ""
    if text == "ready":
        return "ready", ""
    try:
        payload = json.loads(text)
    except (TypeError, ValueError):
        return "", ""
    if not isinstance(payload, dict):
        return "", ""
    state = str(payload.get("state") or "").strip().lower()
    message = str(payload.get("message") or "").strip()
    details = str(payload.get("details") or "").strip()
    combined = details or message
    return state, combined


class LaunchLabelingAppTask(QRunnable):
    """Opens the label-collection UI and waits for a startup handshake."""
    def __init__(
        self,
        *,
        folder: Path,
        runtime: AIWorkflowRuntime,
        annotator_id: str = "",
        artifacts_dir: str | Path | None = None,
        near_identical_threshold: float | None = None,
        appearance_mode: str | None = None,
        parent_pid: int | None = None,
        sync_file_path: str | Path | None = None,
    ) -> None:
        super().__init__()
        self.folder = folder
        self.runtime = runtime
        self.annotator_id = annotator_id.strip()
        self.artifacts_dir = artifacts_dir
        self.near_identical_threshold = near_identical_threshold
        self.appearance_mode = appearance_mode
        self.parent_pid = parent_pid
        self.sync_file_path = sync_file_path
        self.signals = AITrainingTaskSignals()
        self.setAutoDelete(True)

    def run(self) -> None:
        logger = perf_logger()
        task_start = time.perf_counter()
        ready_path = Path(tempfile.gettempdir()) / f"image_triage_labeling_ready_{os.getpid()}_{int(time.time() * 1000)}.flag"
        try:
            ready_path.unlink(missing_ok=True)
        except OSError:
            pass

        self.signals.started.emit(1)
        self.signals.stage.emit(1, 1, "Opening Collect Training Labels")
        self.signals.progress.emit(0, 0, "Starting label collection window...")
        logger.log(
            "labeling.launch.start",
            folder=str(self.folder),
            artifacts_dir=str(self.artifacts_dir or ""),
            near_duplicate_threshold=self.near_identical_threshold if self.near_identical_threshold is not None else "",
            ready_file=str(ready_path),
        )

        try:
            process = launch_labeling_app(
                self.runtime,
                folder=self.folder,
                annotator_id=self.annotator_id,
                artifacts_dir=self.artifacts_dir,
                near_identical_threshold=self.near_identical_threshold,
                ready_file_path=ready_path,
                appearance_mode=self.appearance_mode,
                parent_pid=self.parent_pid,
                sync_file_path=self.sync_file_path,
            )
        except Exception as exc:
            logger.duration(
                "labeling.launch.failed",
                (time.perf_counter() - task_start) * 1000.0,
                folder=str(self.folder),
                error=str(exc),
            )
            self.signals.failed.emit(str(exc))
            return

        start_time = time.monotonic()
        ready_acknowledged = False
        last_status = ""
        last_child_state = ""
        last_child_details = ""
        while True:
            if ready_path.exists():
                state, details = _read_labeling_startup_state(ready_path)
                if state == "ready":
                    ready_acknowledged = True
                    logger.duration(
                        "labeling.launch.ready",
                        (time.perf_counter() - task_start) * 1000.0,
                        folder=str(self.folder),
                        pid=int(getattr(process, "pid", 0) or 0),
                        child_state=state,
                    )
                    break
                if state == "error":
                    logger.duration(
                        "labeling.launch.child_error",
                        (time.perf_counter() - task_start) * 1000.0,
                        folder=str(self.folder),
                        pid=int(getattr(process, "pid", 0) or 0),
                        details=details,
                    )
                    self.signals.failed.emit(details or "Collect Training Labels failed while starting.")
                    return
                if state and (state != last_child_state or details != last_child_details):
                    logger.duration(
                        "labeling.launch.child_state",
                        (time.perf_counter() - task_start) * 1000.0,
                        folder=str(self.folder),
                        pid=int(getattr(process, "pid", 0) or 0),
                        child_state=state,
                        details=details,
                    )
                    self.signals.progress.emit(0, 0, details or f"Label window startup: {state}")
                    last_child_state = state
                    last_child_details = details
            return_code = process.poll()
            if return_code is not None:
                logger.duration(
                    "labeling.launch.exited_early",
                    (time.perf_counter() - task_start) * 1000.0,
                    folder=str(self.folder),
                    pid=int(getattr(process, "pid", 0) or 0),
                    return_code=int(return_code),
                    child_state=last_child_state,
                )
                self.signals.failed.emit(
                    "Collect Training Labels closed before the window finished opening."
                    if return_code == 0
                    else f"Collect Training Labels exited while opening (exit code {return_code})."
                )
                return
            elapsed = time.monotonic() - start_time
            if elapsed >= LABELING_READY_WAIT_TIMEOUT_SECONDS:
                logger.duration(
                    "labeling.launch.ready_timeout",
                    (time.perf_counter() - task_start) * 1000.0,
                    folder=str(self.folder),
                    pid=int(getattr(process, "pid", 0) or 0),
                    child_state=last_child_state,
                    details=last_child_details,
                )
                break
            status_text = (
                "Starting label collection window..."
                if elapsed < 3.0
                else "Loading label collection window..."
                if elapsed < 10.0
                else "Still loading label collection window. Large folders can take a moment."
            )
            if status_text != last_status:
                self.signals.progress.emit(0, 0, status_text)
                last_status = status_text
            time.sleep(LABELING_READY_POLL_INTERVAL_SECONDS)

        try:
            ready_path.unlink(missing_ok=True)
        except OSError:
            pass

        self.signals.finished.emit(
            {
                "process": process,
                "pid": int(getattr(process, "pid", 0) or 0),
                "ready_acknowledged": ready_acknowledged,
            }
        )


class PrepareLabelingCandidatesTask(QRunnable):
    """Builds the CSV/JSON artifacts consumed by the label-collection UI."""
    def __init__(self, *, folder: Path, records: tuple[ImageRecord, ...]) -> None:
        super().__init__()
        self.folder = folder
        self.records = records
        self.signals = AITrainingTaskSignals()
        self.setAutoDelete(True)

    def run(self) -> None:
        logger = perf_logger()
        task_start = time.perf_counter()
        try:
            workspace_start = time.perf_counter()
            paths = prepare_hidden_ai_training_workspace(self.folder)
            logger.duration(
                "labeling.candidates.workspace",
                (time.perf_counter() - workspace_start) * 1000.0,
                folder=str(self.folder),
                artifacts_dir=str(paths.labeling_artifacts_dir),
            )
            if not self.records:
                raise ValueError("No images are loaded for the current folder.")
        except Exception as exc:
            logger.duration(
                "labeling.candidates.failed",
                (time.perf_counter() - task_start) * 1000.0,
                folder=str(self.folder),
                phase="workspace",
                error=str(exc),
            )
            self.signals.failed.emit(str(exc))
            return

        self.signals.started.emit(1)
        self.signals.stage.emit(1, 1, "Building label candidates")
        logger.log("labeling.candidates.start", folder=str(self.folder), record_count=len(self.records))

        try:
            sort_start = time.perf_counter()
            ordered_records = sort_records(list(self.records), SortMode.NAME)
            logger.duration(
                "labeling.candidates.sort",
                (time.perf_counter() - sort_start) * 1000.0,
                folder=str(self.folder),
                record_count=len(ordered_records),
            )
            metadata_by_path: dict[str, CaptureMetadata] = {}
            total = len(ordered_records)
            metadata_start = time.perf_counter()
            slow_metadata_count = 0
            for index, record in enumerate(ordered_records, start=1):
                item_start = time.perf_counter()
                metadata = load_capture_metadata(record.path)
                item_ms = (time.perf_counter() - item_start) * 1000.0
                if item_ms >= 250.0:
                    slow_metadata_count += 1
                    logger.duration(
                        "labeling.candidates.metadata.slow",
                        item_ms,
                        folder=str(self.folder),
                        path=record.path,
                        index=index,
                        total=total,
                    )
                metadata_by_path[record.path] = metadata
                self.signals.progress.emit(index, total, f"Scanning images {index}/{total}")
                if logger.enabled and (index == total or index % 100 == 0):
                    logger.duration(
                        "labeling.candidates.metadata.progress",
                        (time.perf_counter() - metadata_start) * 1000.0,
                        folder=str(self.folder),
                        current=index,
                        total=total,
                        slow_count=slow_metadata_count,
                    )
            logger.duration(
                "labeling.candidates.metadata.total",
                (time.perf_counter() - metadata_start) * 1000.0,
                folder=str(self.folder),
                record_count=total,
                slow_count=slow_metadata_count,
            )

            cluster_start = time.perf_counter()
            cluster_rows = _build_labeling_cluster_rows(
                folder=self.folder,
                records=ordered_records,
                metadata_by_path=metadata_by_path,
            )
            logger.duration(
                "labeling.candidates.cluster_rows",
                (time.perf_counter() - cluster_start) * 1000.0,
                folder=str(self.folder),
                row_count=len(cluster_rows),
            )
            image_rows_start = time.perf_counter()
            image_rows = _build_labeling_metadata_rows(
                folder=self.folder,
                records=ordered_records,
                metadata_by_path=metadata_by_path,
            )
            logger.duration(
                "labeling.candidates.image_rows",
                (time.perf_counter() - image_rows_start) * 1000.0,
                folder=str(self.folder),
                row_count=len(image_rows),
            )
            image_ids = [row["image_id"] for row in image_rows]
            write_start = time.perf_counter()
            _write_csv_rows(paths.labeling_metadata_path, image_rows, fieldnames=list(image_rows[0].keys()) if image_rows else ["image_id", "file_path", "relative_path", "file_name", "capture_timestamp", "capture_time_source"])
            paths.labeling_image_ids_path.write_text(json.dumps(image_ids, indent=2), encoding="utf-8")
            _write_csv_rows(
                paths.labeling_clusters_path,
                cluster_rows,
                fieldnames=list(cluster_rows[0].keys()) if cluster_rows else ["image_id", "cluster_id", "cluster_size", "cluster_position", "time_window_id", "window_kind", "cluster_reason", "capture_timestamp", "capture_time_source", "file_path", "relative_path", "file_name"],
            )
            logger.duration(
                "labeling.candidates.write_artifacts",
                (time.perf_counter() - write_start) * 1000.0,
                folder=str(self.folder),
                image_rows=len(image_rows),
                cluster_rows=len(cluster_rows),
            )
        except Exception as exc:
            logger.duration(
                "labeling.candidates.failed",
                (time.perf_counter() - task_start) * 1000.0,
                folder=str(self.folder),
                phase="build",
                error=str(exc),
            )
            self.signals.failed.emit(str(exc))
            return

        multi_image_groups = sum(1 for row in cluster_rows if int(row["cluster_size"]) > 1 and int(row["cluster_position"]) == 0)
        logger.duration(
            "labeling.candidates.total",
            (time.perf_counter() - task_start) * 1000.0,
            folder=str(self.folder),
            total_images=len(image_rows),
            cluster_rows=len(cluster_rows),
            multi_image_groups=multi_image_groups,
        )
        self.signals.finished.emit(
            {
                "artifacts_dir": str(paths.labeling_artifacts_dir),
                "labels_dir": str(paths.labels_dir),
                "total_images": len(image_rows),
                "multi_image_groups": multi_image_groups,
            }
        )


class PrepareTrainingDataTask(QRunnable):
    """Runs embedding extraction and clustering to produce trainable artifacts."""
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

        include_paths_file, included_image_ids = _write_labeled_training_include_file(paths)
        has_labeled_training_labels = bool(included_image_ids)
        staged_input_dir: Path | None = None
        use_local_stage = include_paths_file is None and _should_use_local_staging(self.folder, self.runtime)
        total_stages = (1 if has_labeled_training_labels else 2) + (1 if use_local_stage else 0)
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
                    "scripts/extract_embeddings.py",
                    [
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
            ]
            if not has_labeled_training_labels:
                commands.append(
                    (
                        "Building culling groups",
                        "scripts/cluster_embeddings.py",
                        [
                            "--config",
                            str(self.runtime.clustering_config_path),
                            "--artifacts-dir",
                            str(paths.artifacts_dir),
                            "--output-dir",
                            str(paths.artifacts_dir),
                        ],
                    )
                )
            if include_paths_file is not None:
                commands[0][2].extend(["--include-paths-file", str(include_paths_file)])
                self.signals.log.emit(f"Preparing training embeddings for {len(included_image_ids)} labeled images.")

            for stage_index, (stage_message, script_relative_path, stage_args) in enumerate(commands, start=command_start_index):
                self.signals.stage.emit(stage_index, total_stages, stage_message)
                command = _resolve_stage_command(
                    self.runtime,
                    script_relative_path=script_relative_path,
                    stage_args=stage_args,
                )
                completed = _run_command_with_live_output(
                    command,
                    cwd=self.runtime.engine_root,
                    progress_callback=lambda line: _emit_command_progress(self.signals, line),
                )
                if completed.returncode != 0:
                    raise RuntimeError(_command_failure_message(stage_message, completed.stdout))
                if staged_input_dir is not None and script_relative_path == "scripts/extract_embeddings.py":
                    rewrite_extraction_artifact_paths(
                        artifacts_dir=paths.artifacts_dir,
                        source_folder=self.folder,
                    )
                if has_labeled_training_labels and script_relative_path == "scripts/extract_embeddings.py":
                    _write_labeled_training_clusters(paths, included_image_ids)
        except Exception as exc:
            self.signals.failed.emit(str(exc))
            return

        self.signals.finished.emit(
            {
                "artifacts_dir": str(paths.artifacts_dir),
                "labels_dir": str(paths.labels_dir),
                "labeled_image_count": len(included_image_ids),
            }
        )


class TrainRankerTask(QRunnable):
    """Runs the external ranker-training command and records its outputs."""
    def __init__(
        self,
        *,
        folder: Path,
        runtime: AIWorkflowRuntime,
        options: RankerTrainingOptions,
        general_source_folders: tuple[str, ...] = (),
    ) -> None:
        super().__init__()
        self.folder = folder
        self.runtime = runtime
        self.options = options
        self.profile_key, self.profile_label = normalize_ranker_profile(options.profile_key)
        self.general_source_folders = tuple(str(folder) for folder in general_source_folders if str(folder).strip())
        self.signals = AITrainingTaskSignals()
        self.setAutoDelete(True)
        self.paths = build_general_ai_training_paths() if self.profile_key == DEFAULT_RANKER_PROFILE_KEY else build_ai_training_paths(folder)
        self.run_id, self.display_name, self.run_dir = create_ranker_run(self.paths, options.run_name)
        self.log_path = self.run_dir / TRAINING_LOG_FILENAME

    def run(self) -> None:
        try:
            if self.profile_key == DEFAULT_RANKER_PROFILE_KEY:
                pool_status = prepare_general_training_pool(self.general_source_folders)
                paths = pool_status.paths
                pairwise_count = pool_status.pairwise_labels
                cluster_count = pool_status.cluster_labels
                disagreement_count = pool_status.disagreement_pair_labels
            else:
                paths = prepare_hidden_ai_training_workspace(self.folder)
                pairwise_count, cluster_count = count_label_records(paths)
                disagreement_count = count_disagreement_pair_labels(paths)
            _validate_runtime_paths(
                self.runtime,
                required=(
                    ("engine root", self.runtime.engine_root),
                    ("python executable", self.runtime.python_executable),
                    ("train config", self.runtime.engine_root / "legacy" / "configs" / "train_ranker.json"),
                ),
            )
            if pairwise_count <= 0 and cluster_count <= 0:
                raise ValueError("No saved pairwise or cluster labels were found. Open Collect Training Labels first.")
            self.run_dir.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            self.signals.failed.emit(str(exc))
            return

        self.signals.started.emit(1)
        self.signals.stage.emit(1, 1, "Training ranker")
        stage_args = [
            "--config",
            str(self.runtime.engine_root / "legacy" / "configs" / "train_ranker.json"),
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
            "--disagreement-oversample-factor",
            str(max(1, min(10, self.options.disagreement_oversample_factor))),
            "--device",
            self.options.device or self.runtime.device,
        ]
        reference_bank_path = self.options.reference_bank_path.strip()
        if reference_bank_path:
            stage_args.extend(["--reference-bank-path", reference_bank_path])
        command = _resolve_stage_command(
            self.runtime,
            script_relative_path="legacy/scripts/train_ranker.py",
            stage_args=stage_args,
        )

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
                disagreement_count=disagreement_count,
                reference_bank_path=reference_bank_path,
                profile_key=self.profile_key,
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
                "profile_key": self.profile_key,
                "profile_label": self.profile_label,
                "training_scope": "shared_general" if self.profile_key == DEFAULT_RANKER_PROFILE_KEY else "folder",
            }
        )


class EvaluateRankerTask(QRunnable):
    """Runs evaluation for a trained checkpoint and stores report artifacts."""
    def __init__(
        self,
        *,
        folder: Path,
        runtime: AIWorkflowRuntime,
        checkpoint_path: Path,
        reference_bank_path: str = "",
        use_general_pool: bool = False,
        general_source_folders: tuple[str, ...] = (),
        evaluation_folder: Path | None = None,
        evaluation_folders: tuple[Path, ...] = (),
        evaluation_output_name: str = "",
    ) -> None:
        super().__init__()
        self.folder = folder
        self.runtime = runtime
        self.checkpoint_path = checkpoint_path
        self.reference_bank_path = reference_bank_path.strip()
        self.use_general_pool = bool(use_general_pool)
        self.general_source_folders = tuple(str(folder) for folder in general_source_folders if str(folder).strip())
        self.evaluation_folder = evaluation_folder
        self.evaluation_folders = tuple(Path(folder) for folder in evaluation_folders)
        self.evaluation_output_name = _slugify_run_name(evaluation_output_name)
        self.signals = AITrainingTaskSignals()
        self.setAutoDelete(True)

    def run(self) -> None:
        try:
            if self.evaluation_folders:
                self._run_source_aware()
                return

            if self.evaluation_folder is not None:
                paths = prepare_hidden_ai_training_workspace(self.evaluation_folder)
                pairwise_count, cluster_count = count_label_records(paths)
            elif self.use_general_pool:
                pool_status = prepare_general_training_pool(self.general_source_folders)
                paths = pool_status.paths
                pairwise_count = pool_status.pairwise_labels
                cluster_count = pool_status.cluster_labels
            else:
                paths = prepare_hidden_ai_training_workspace(self.folder)
                pairwise_count, cluster_count = count_label_records(paths)
            evaluation_dir = evaluation_output_dir_for_checkpoint(paths, self.checkpoint_path)
            if self.evaluation_output_name:
                evaluation_dir = evaluation_dir / self.evaluation_output_name
            _validate_runtime_paths(
                self.runtime,
                required=(
                    ("engine root", self.runtime.engine_root),
                    ("python executable", self.runtime.python_executable),
                    ("evaluate config", self.runtime.engine_root / "legacy" / "configs" / "evaluate_ranker.json"),
                    ("checkpoint", self.checkpoint_path),
                ),
            )
            if pairwise_count <= 0 and cluster_count <= 0:
                raise ValueError("No saved pairwise or cluster labels were found for evaluation.")
            issues = ai_training_evaluation_issues(paths)
            if issues:
                raise ValueError(format_ai_training_evaluation_issues(paths.folder, issues))
            evaluation_dir.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            self.signals.failed.emit(str(exc))
            return

        self.signals.started.emit(1)
        self.signals.stage.emit(1, 1, "Evaluating trained ranker")
        stage_args = [
            "--config",
            str(self.runtime.engine_root / "legacy" / "configs" / "evaluate_ranker.json"),
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
            stage_args.extend(["--reference-bank-path", self.reference_bank_path])
        command = _resolve_stage_command(
            self.runtime,
            script_relative_path="legacy/scripts/evaluate_ranker.py",
            stage_args=stage_args,
        )

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
                "checkpoint_path": str(self.checkpoint_path),
                "metrics_path": str(evaluation_dir / EVALUATION_METRICS_FILENAME),
                "pairwise_breakdown_path": str(evaluation_dir / PAIRWISE_BREAKDOWN_FILENAME),
                "cluster_breakdown_path": str(evaluation_dir / CLUSTER_BREAKDOWN_FILENAME),
                "log_path": str(evaluation_dir / EVALUATION_LOG_FILENAME),
            }
        )

    def _run_source_aware(self) -> None:
        """Evaluate the same checkpoint against multiple prepared source folders."""

        _validate_runtime_paths(
            self.runtime,
            required=(
                ("engine root", self.runtime.engine_root),
                ("python executable", self.runtime.python_executable),
                ("evaluate config", self.runtime.engine_root / "legacy" / "configs" / "evaluate_ranker.json"),
                ("checkpoint", self.checkpoint_path),
            ),
        )

        sources: list[tuple[Path, AITrainingPaths]] = []
        seen: set[str] = set()
        for folder in self.evaluation_folders:
            paths = prepare_hidden_ai_training_workspace(folder)
            key = str(paths.folder).casefold()
            if not key or key in seen:
                continue
            seen.add(key)
            issues = ai_training_evaluation_issues(paths)
            if issues:
                raise ValueError(format_ai_training_evaluation_issues(paths.folder, issues))
            sources.append((paths.folder, paths))

        if not sources:
            raise ValueError("No prepared evaluation sources were selected.")

        base_paths = build_general_ai_training_paths() if self.use_general_pool else sources[0][1]
        summary_dir = evaluation_output_dir_for_checkpoint(base_paths, self.checkpoint_path)
        summary_dir = summary_dir / (self.evaluation_output_name or "source-aware-evaluation")
        summary_dir.mkdir(parents=True, exist_ok=True)

        self.signals.started.emit(len(sources))
        source_summaries: list[dict[str, object]] = []
        warnings: list[str] = []
        for index, (folder, paths) in enumerate(sources, start=1):
            source_name = folder.name or str(folder)
            self.signals.stage.emit(index, len(sources), f"Evaluating {source_name}")
            source_output_dir = summary_dir / f"{_slugify_run_name(source_name) or 'source'}-{_general_source_namespace(str(folder))}"
            source_output_dir.mkdir(parents=True, exist_ok=True)
            self._run_single_evaluation(paths=paths, evaluation_dir=source_output_dir)
            metrics_path = source_output_dir / EVALUATION_METRICS_FILENAME
            source_summary = _summarize_evaluation_metrics(metrics_path)
            source_summary.update(
                {
                    "folder": str(folder),
                    "display_name": source_name,
                    "metrics_path": str(metrics_path),
                    "pairwise_breakdown_path": str(source_output_dir / PAIRWISE_BREAKDOWN_FILENAME),
                    "cluster_breakdown_path": str(source_output_dir / CLUSTER_BREAKDOWN_FILENAME),
                    "log_path": str(source_output_dir / EVALUATION_LOG_FILENAME),
                }
            )
            if int(source_summary.get("cluster_labeled_clusters") or 0) > 0 and int(source_summary.get("cluster_evaluated_clusters") or 0) <= 0:
                warnings.append(f"{source_name}: cluster labels existed, but no clusters were evaluated.")
            source_summaries.append(source_summary)

        summary_payload = _build_source_aware_evaluation_summary(
            checkpoint_path=self.checkpoint_path,
            output_dir=summary_dir,
            source_summaries=source_summaries,
            warnings=warnings,
        )
        metrics_path = summary_dir / "source_aware_evaluation.json"
        metrics_path.write_text(json.dumps(summary_payload, indent=2), encoding="utf-8")
        self.signals.finished.emit(
            {
                "checkpoint_path": str(self.checkpoint_path),
                "metrics_path": str(metrics_path),
                "source_evaluation_paths": [str(summary["metrics_path"]) for summary in source_summaries],
                "source_count": len(source_summaries),
            }
        )

    def _run_single_evaluation(self, *, paths: AITrainingPaths, evaluation_dir: Path) -> None:
        stage_args = [
            "--config",
            str(self.runtime.engine_root / "legacy" / "configs" / "evaluate_ranker.json"),
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
            stage_args.extend(["--reference-bank-path", self.reference_bank_path])
        command = _resolve_stage_command(
            self.runtime,
            script_relative_path="legacy/scripts/evaluate_ranker.py",
            stage_args=stage_args,
        )
        completed = _run_command_with_live_output(
            command,
            cwd=self.runtime.engine_root,
            progress_callback=lambda line: _emit_command_progress(self.signals, line, default_message="Evaluating ranker"),
        )
        if completed.returncode != 0:
            raise RuntimeError(_command_failure_message("Evaluating ranker", completed.stdout))


def _summarize_evaluation_metrics(metrics_path: Path) -> dict[str, object]:
    payload = _read_json_dict(metrics_path)
    pairwise_all = payload.get("pairwise_evaluation", {})
    if isinstance(pairwise_all, dict):
        pairwise_all = pairwise_all.get("all_preferences", {})
    if not isinstance(pairwise_all, dict):
        pairwise_all = {}
    cluster_eval = payload.get("cluster_evaluation", {})
    if not isinstance(cluster_eval, dict):
        cluster_eval = {}
    top_k = cluster_eval.get("top_k_metrics", {})
    top1 = top_k.get("top_1", {}) if isinstance(top_k, dict) else {}
    if not isinstance(top1, dict):
        top1 = {}
    label_summary = payload.get("label_summary", {})
    if not isinstance(label_summary, dict):
        label_summary = {}
    summary = {
        "pairwise_accuracy": _coerce_float(pairwise_all.get("accuracy")),
        "pairwise_evaluated_pairs": _coerce_int(pairwise_all.get("evaluated_pairs")),
        "cluster_top1_hit_rate": _coerce_float(top1.get("hit_rate")),
        "cluster_top1_eligible_clusters": _coerce_int(top1.get("eligible_clusters")),
        "cluster_labeled_clusters": _coerce_int(cluster_eval.get("labeled_clusters")),
        "cluster_evaluated_clusters": _coerce_int(cluster_eval.get("evaluated_clusters")),
        "cluster_missing_ranked_clusters": _coerce_int(cluster_eval.get("missing_ranked_clusters")),
        "label_pairwise_records": _coerce_int(label_summary.get("pairwise_label_records")),
        "label_cluster_records": _coerce_int(label_summary.get("cluster_label_records")),
        "label_total_preference_pairs": _coerce_int(label_summary.get("total_preference_pairs")),
    }
    baseline_summary = _summarize_baseline_comparison(payload)
    if baseline_summary:
        summary["baseline_comparison"] = baseline_summary
    return summary


def _build_source_aware_evaluation_summary(
    *,
    checkpoint_path: Path,
    output_dir: Path,
    source_summaries: list[dict[str, object]],
    warnings: list[str],
) -> dict[str, object]:
    pairwise_values = [
        (float(summary["pairwise_accuracy"]), int(summary.get("pairwise_evaluated_pairs") or 0))
        for summary in source_summaries
        if summary.get("pairwise_accuracy") is not None and int(summary.get("pairwise_evaluated_pairs") or 0) > 0
    ]
    cluster_values = [
        (float(summary["cluster_top1_hit_rate"]), int(summary.get("cluster_top1_eligible_clusters") or 0))
        for summary in source_summaries
        if summary.get("cluster_top1_hit_rate") is not None and int(summary.get("cluster_top1_eligible_clusters") or 0) > 0
    ]
    return {
        "checkpoint_path": str(checkpoint_path),
        "evaluation_mode": "source_aware",
        "output_dir": str(output_dir),
        "source_count": len(source_summaries),
        "macro_pairwise_accuracy": _mean_metric(value for value, _weight in pairwise_values),
        "weighted_pairwise_accuracy": _weighted_mean_metric(pairwise_values),
        "macro_cluster_top1_hit_rate": _mean_metric(value for value, _weight in cluster_values),
        "weighted_cluster_top1_hit_rate": _weighted_mean_metric(cluster_values),
        "total_pairwise_evaluated_pairs": sum(weight for _value, weight in pairwise_values),
        "total_cluster_top1_eligible_clusters": sum(weight for _value, weight in cluster_values),
        "baseline_comparison": _aggregate_source_baseline_comparison(source_summaries),
        "warnings": warnings,
        "sources": source_summaries,
        "notes": [
            "Macro metrics average each source equally, which is the best quick read on generalization.",
            "Weighted metrics are dominated by sources with more labels and can hide weak held-out folders.",
        ],
    }


def _summarize_baseline_comparison(payload: dict[str, object]) -> dict[str, object]:
    comparison = payload.get("baseline_comparison")
    if not isinstance(comparison, dict):
        return {}
    scorers = comparison.get("scorers")
    if not isinstance(scorers, dict):
        return {}
    summarized: dict[str, object] = {}
    for key, scorer_payload in scorers.items():
        if not isinstance(scorer_payload, dict):
            continue
        pairwise_eval = scorer_payload.get("pairwise_evaluation", {})
        if isinstance(pairwise_eval, dict):
            pairwise_all = pairwise_eval.get("all_preferences", {})
        else:
            pairwise_all = {}
        if not isinstance(pairwise_all, dict):
            pairwise_all = {}
        cluster_eval = scorer_payload.get("cluster_evaluation", {})
        if not isinstance(cluster_eval, dict):
            cluster_eval = {}
        top_k = cluster_eval.get("top_k_metrics", {})
        if not isinstance(top_k, dict):
            top_k = {}
        top1 = top_k.get("top_1", {})
        top3 = top_k.get("top_3", {})
        if not isinstance(top1, dict):
            top1 = {}
        if not isinstance(top3, dict):
            top3 = {}
        summarized[str(key)] = {
            "display_name": str(scorer_payload.get("display_name") or key),
            "pairwise_accuracy": _coerce_float(pairwise_all.get("accuracy")),
            "pairwise_evaluated_pairs": _coerce_int(pairwise_all.get("evaluated_pairs")),
            "cluster_top1_hit_rate": _coerce_float(top1.get("hit_rate")),
            "cluster_top1_eligible_clusters": _coerce_int(top1.get("eligible_clusters")),
            "cluster_top3_hit_rate": _coerce_float(top3.get("hit_rate")),
            "cluster_top3_eligible_clusters": _coerce_int(top3.get("eligible_clusters")),
            "mean_first_human_best_rank": _coerce_float(cluster_eval.get("mean_first_human_best_rank")),
            "cluster_evaluated_clusters": _coerce_int(cluster_eval.get("evaluated_clusters")),
        }
    return summarized


def _aggregate_source_baseline_comparison(source_summaries: list[dict[str, object]]) -> dict[str, object]:
    scorer_keys: set[str] = set()
    for summary in source_summaries:
        comparison = summary.get("baseline_comparison")
        if isinstance(comparison, dict):
            scorer_keys.update(str(key) for key in comparison.keys())
    if not scorer_keys:
        return {}

    aggregated: dict[str, object] = {}
    for scorer_key in sorted(scorer_keys):
        pairwise_values: list[tuple[float, int]] = []
        top1_values: list[tuple[float, int]] = []
        top3_values: list[tuple[float, int]] = []
        first_rank_values: list[tuple[float, int]] = []
        display_name = scorer_key
        for source in source_summaries:
            comparison = source.get("baseline_comparison")
            if not isinstance(comparison, dict):
                continue
            scorer = comparison.get(scorer_key)
            if not isinstance(scorer, dict):
                continue
            display_name = str(scorer.get("display_name") or display_name)
            pairwise_acc = _coerce_float(scorer.get("pairwise_accuracy"))
            pairwise_count = _coerce_int(scorer.get("pairwise_evaluated_pairs"))
            if pairwise_acc is not None and pairwise_count > 0:
                pairwise_values.append((pairwise_acc, pairwise_count))
            top1 = _coerce_float(scorer.get("cluster_top1_hit_rate"))
            top1_count = _coerce_int(scorer.get("cluster_top1_eligible_clusters"))
            if top1 is not None and top1_count > 0:
                top1_values.append((top1, top1_count))
            top3 = _coerce_float(scorer.get("cluster_top3_hit_rate"))
            top3_count = _coerce_int(scorer.get("cluster_top3_eligible_clusters"))
            if top3 is not None and top3_count > 0:
                top3_values.append((top3, top3_count))
            first_rank = _coerce_float(scorer.get("mean_first_human_best_rank"))
            evaluated_clusters = _coerce_int(scorer.get("cluster_evaluated_clusters"))
            if first_rank is not None and evaluated_clusters > 0:
                first_rank_values.append((first_rank, evaluated_clusters))
        aggregated[scorer_key] = {
            "display_name": display_name,
            "macro_pairwise_accuracy": _mean_metric(value for value, _weight in pairwise_values),
            "weighted_pairwise_accuracy": _weighted_mean_metric(pairwise_values),
            "macro_cluster_top1_hit_rate": _mean_metric(value for value, _weight in top1_values),
            "weighted_cluster_top1_hit_rate": _weighted_mean_metric(top1_values),
            "macro_cluster_top3_hit_rate": _mean_metric(value for value, _weight in top3_values),
            "weighted_cluster_top3_hit_rate": _weighted_mean_metric(top3_values),
            "macro_mean_first_human_best_rank": _mean_metric(value for value, _weight in first_rank_values),
            "weighted_mean_first_human_best_rank": _weighted_mean_metric(first_rank_values),
        }
    return aggregated


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


def _mean_metric(values: Iterable[float]) -> float | None:
    collected = [float(value) for value in values]
    if not collected:
        return None
    return sum(collected) / len(collected)


def _weighted_mean_metric(values: Iterable[tuple[float, int]]) -> float | None:
    collected = [(float(value), int(weight)) for value, weight in values if int(weight) > 0]
    total_weight = sum(weight for _value, weight in collected)
    if total_weight <= 0:
        return None
    return sum(value * weight for value, weight in collected) / total_weight


class ScoreCurrentFolderTask(QRunnable):
    """Scores the current folder with a trained ranker without retraining it."""
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
        stage_args = [
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
            stage_args.extend(["--labels-dir", str(paths.labels_dir)])
        if self.reference_bank_path:
            stage_args.extend(["--reference-bank-path", self.reference_bank_path])
        command = _resolve_stage_command(
            self.runtime,
            script_relative_path="scripts/export_ranked_report.py",
            stage_args=stage_args,
        )

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


class BuildCullingSignalsTask(QRunnable):
    """Builds modular culling signal artifacts for a prepared folder."""

    def __init__(
        self,
        *,
        folder: Path,
        runtime: AIWorkflowRuntime,
        profile_name: str = "General Use",
        run_technical: bool = True,
        run_specialists: bool = True,
        max_preview_side: int = 768,
        weights_path: Path | None = None,
    ) -> None:
        super().__init__()
        self.folder = folder
        self.runtime = runtime
        self.profile_name = profile_name.strip() or "General Use"
        self.run_technical = bool(run_technical)
        self.run_specialists = bool(run_specialists)
        self.max_preview_side = max(64, int(max_preview_side))
        self.weights_path = weights_path
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
                    ("culling signal script", self.runtime.engine_root / "scripts" / "build_culling_signals.py"),
                ),
            )
            if not ai_training_artifacts_ready(paths):
                raise ValueError("Prepared artifacts are not ready yet. Run Prepare Training Data first.")
            output_dir = paths.hidden_root / SIGNALS_DIR_NAME
            output_dir.mkdir(parents=True, exist_ok=True)
            weights_path = self.weights_path or (output_dir / SIGNAL_COMBINER_WEIGHTS_FILENAME)
            if not weights_path.exists():
                weights_path = None
        except Exception as exc:
            self.signals.failed.emit(str(exc))
            return

        self.signals.started.emit(1)
        self.signals.stage.emit(1, 1, "Building culling signals")
        stage_args = [
            "--artifacts-dir",
            str(paths.artifacts_dir),
            "--output-dir",
            str(output_dir),
            "--profile",
            self.profile_name,
            "--max-preview-side",
            str(self.max_preview_side),
        ]
        if not self.run_technical:
            stage_args.append("--skip-technical")
        if not self.run_specialists:
            stage_args.append("--skip-specialists")
        if weights_path is not None:
            stage_args.extend(["--weights-path", str(weights_path)])
        command = _resolve_stage_command(
            self.runtime,
            script_relative_path="scripts/build_culling_signals.py",
            stage_args=stage_args,
        )

        try:
            completed = _run_command_with_live_output(
                command,
                cwd=self.runtime.engine_root,
                progress_callback=lambda line: _emit_command_progress(
                    self.signals,
                    line,
                    default_message="Building culling signals",
                ),
            )
            if completed.returncode != 0:
                raise RuntimeError(_command_failure_message("Building culling signals", completed.stdout))
        except Exception as exc:
            self.signals.failed.emit(str(exc))
            return

        self.signals.finished.emit(
            {
                "signals_dir": str(output_dir),
                "signals_json_path": str(output_dir / SIGNALS_JSON_FILENAME),
                "signals_csv_path": str(output_dir / SIGNALS_CSV_FILENAME),
                "weights_path": str(weights_path) if weights_path is not None else "",
                "profile_name": self.profile_name,
            }
        )


class EvaluateCullingSignalsTask(QRunnable):
    """Evaluates culling signal scores without requiring a trained checkpoint."""

    def __init__(
        self,
        *,
        folder: Path,
        runtime: AIWorkflowRuntime,
        profile_name: str = "General Use",
        weights_path: Path | None = None,
    ) -> None:
        super().__init__()
        self.folder = folder
        self.runtime = runtime
        self.profile_name = profile_name.strip() or "General Use"
        self.weights_path = weights_path
        self.signals = AITrainingTaskSignals()
        self.setAutoDelete(True)

    def run(self) -> None:
        try:
            paths = prepare_hidden_ai_training_workspace(self.folder)
            signals_dir = paths.hidden_root / SIGNALS_DIR_NAME
            signals_path = signals_dir / SIGNALS_JSON_FILENAME
            output_dir = paths.evaluation_dir / "culling_signals"
            weights_path = self.weights_path
            if weights_path is not None:
                weights_path = Path(weights_path).expanduser()
                if not weights_path.exists():
                    weights_path = None
            _validate_runtime_paths(
                self.runtime,
                required=(
                    ("engine root", self.runtime.engine_root),
                    ("python executable", self.runtime.python_executable),
                    ("culling signal evaluation script", self.runtime.engine_root / "scripts" / "evaluate_culling_signals.py"),
                ),
            )
            if weights_path is not None:
                _validate_runtime_paths(
                    self.runtime,
                    required=(
                        ("culling signal script", self.runtime.engine_root / "scripts" / "build_culling_signals.py"),
                    ),
                )
            if not ai_training_artifacts_ready(paths):
                raise ValueError("Prepared artifacts are not ready yet. Run Prepare Training Data first.")
            pairwise_count, cluster_count = count_label_records(paths)
            disagreement_count = count_disagreement_pair_labels(paths)
            if pairwise_count <= 0 and cluster_count <= 0 and disagreement_count <= 0:
                raise ValueError("No saved labels were found for signal evaluation.")
            issues = ai_training_evaluation_issues(paths)
            if issues:
                raise ValueError(format_ai_training_evaluation_issues(paths.folder, issues))
            if weights_path is None and not signals_path.exists():
                raise ValueError("Culling signals are not built yet. Run Build Culling Signals first.")
            signals_dir.mkdir(parents=True, exist_ok=True)
            output_dir.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            self.signals.failed.emit(str(exc))
            return

        total_steps = 2 if weights_path is not None else 1
        self.signals.started.emit(total_steps)
        if weights_path is not None:
            self.signals.stage.emit(1, total_steps, "Rebuilding culling signals with active weights")
            build_args = [
                "--artifacts-dir",
                str(paths.artifacts_dir),
                "--output-dir",
                str(signals_dir),
                "--profile",
                self.profile_name,
                "--weights-path",
                str(weights_path),
            ]
            build_command = _resolve_stage_command(
                self.runtime,
                script_relative_path="scripts/build_culling_signals.py",
                stage_args=build_args,
            )
            try:
                completed = _run_command_with_live_output(
                    build_command,
                    cwd=self.runtime.engine_root,
                    progress_callback=lambda line: _emit_command_progress(
                        self.signals,
                        line,
                        default_message="Rebuilding culling signals",
                    ),
                )
                if completed.returncode != 0:
                    raise RuntimeError(_command_failure_message("Rebuilding culling signals", completed.stdout))
            except Exception as exc:
                self.signals.failed.emit(str(exc))
                return

        self.signals.stage.emit(total_steps, total_steps, "Evaluating culling signals")
        stage_args = [
            "--artifacts-dir",
            str(paths.artifacts_dir),
            "--labels-dir",
            str(paths.labels_dir),
            "--signals-path",
            str(signals_path),
            "--output-dir",
            str(output_dir),
        ]
        command = _resolve_stage_command(
            self.runtime,
            script_relative_path="scripts/evaluate_culling_signals.py",
            stage_args=stage_args,
        )

        try:
            completed = _run_command_with_live_output(
                command,
                cwd=self.runtime.engine_root,
                progress_callback=lambda line: _emit_command_progress(
                    self.signals,
                    line,
                    default_message="Evaluating culling signals",
                ),
            )
            if completed.returncode != 0:
                raise RuntimeError(_command_failure_message("Evaluating culling signals", completed.stdout))
        except Exception as exc:
            self.signals.failed.emit(str(exc))
            return

        self.signals.finished.emit(
            {
                "metrics_path": str(output_dir / SIGNAL_EVALUATION_METRICS_FILENAME),
                "summary_path": str(output_dir / SIGNAL_EVALUATION_SUMMARY_FILENAME),
                "signals_json_path": str(signals_path),
                "weights_path": str(weights_path) if weights_path is not None else "",
            }
        )


class TuneCullingSignalsTask(QRunnable):
    """Tunes transparent combiner weights and rebuilds signal scores."""

    def __init__(
        self,
        *,
        folder: Path,
        runtime: AIWorkflowRuntime,
        profile_name: str = "General Use",
        source_folders: tuple[str, ...] = (),
    ) -> None:
        super().__init__()
        self.folder = folder
        self.runtime = runtime
        self.profile_name = profile_name.strip() or "General Use"
        self.source_folders = tuple(str(source).strip() for source in source_folders if str(source).strip())
        self.signals = AITrainingTaskSignals()
        self.setAutoDelete(True)

    def run(self) -> None:
        try:
            source_folders = self.source_folders or (str(self.folder),)
            source_paths: list[AITrainingPaths] = []
            source_entries: list[dict[str, str]] = []
            for source_folder in source_folders:
                source_path = prepare_hidden_ai_training_workspace(Path(source_folder))
                if not ai_training_artifacts_ready(source_path):
                    raise ValueError(f"Prepared artifacts are not ready for {source_path.folder}. Run Prepare Training Data first.")
                pairwise_count, cluster_count = count_label_records(source_path)
                disagreement_count = count_disagreement_pair_labels(source_path)
                if pairwise_count <= 0 and cluster_count <= 0 and disagreement_count <= 0:
                    raise ValueError(f"No saved labels were found for {source_path.folder}.")
                issues = ai_training_evaluation_issues(source_path)
                if issues:
                    raise ValueError(format_ai_training_evaluation_issues(source_path.folder, issues))
                source_paths.append(source_path)
                source_signals_dir = source_path.hidden_root / SIGNALS_DIR_NAME
                source_entries.append(
                    {
                        "folder": str(source_path.folder),
                        "source_name": str(source_path.folder),
                        "artifacts_dir": str(source_path.artifacts_dir),
                        "labels_dir": str(source_path.labels_dir),
                        "signals_path": str(source_signals_dir / SIGNALS_JSON_FILENAME),
                    }
                )

            output_paths = prepare_general_ai_training_workspace() if len(source_paths) > 1 else source_paths[0]
            signals_dir = output_paths.hidden_root / SIGNALS_DIR_NAME
            weights_path = signals_dir / SIGNAL_COMBINER_WEIGHTS_FILENAME
            feature_rows_path = signals_dir / SIGNAL_COMBINER_FEATURES_FILENAME
            manifest_path = signals_dir / "culling_combiner_sources.json"
            _validate_runtime_paths(
                self.runtime,
                required=(
                    ("engine root", self.runtime.engine_root),
                    ("python executable", self.runtime.python_executable),
                    ("culling combiner training script", self.runtime.engine_root / "scripts" / "train_culling_combiner.py"),
                    ("culling signal script", self.runtime.engine_root / "scripts" / "build_culling_signals.py"),
                ),
            )
            signals_dir.mkdir(parents=True, exist_ok=True)
            manifest_path.write_text(
                json.dumps({"sources": source_entries}, indent=2),
                encoding="utf-8",
            )
        except Exception as exc:
            self.signals.failed.emit(str(exc))
            return

        total_steps = len(source_paths) + 1
        self.signals.started.emit(total_steps)
        for index, source_path in enumerate(source_paths, start=1):
            self.signals.stage.emit(index, total_steps, f"Refreshing culling signal features for {Path(source_path.folder).name}")
            source_signals_dir = source_path.hidden_root / SIGNALS_DIR_NAME
            source_signals_dir.mkdir(parents=True, exist_ok=True)
            refresh_args = [
                "--artifacts-dir",
                str(source_path.artifacts_dir),
                "--output-dir",
                str(source_signals_dir),
                "--profile",
                self.profile_name,
            ]
            refresh_command = _resolve_stage_command(
                self.runtime,
                script_relative_path="scripts/build_culling_signals.py",
                stage_args=refresh_args,
            )
            try:
                completed = _run_command_with_live_output(
                    refresh_command,
                    cwd=self.runtime.engine_root,
                    progress_callback=lambda line: _emit_command_progress(
                        self.signals,
                        line,
                        default_message="Refreshing culling signal features",
                    ),
                )
                if completed.returncode != 0:
                    raise RuntimeError(_command_failure_message("Refreshing culling signals", completed.stdout))
            except Exception as exc:
                self.signals.failed.emit(str(exc))
                return

        self.signals.stage.emit(total_steps, total_steps, "Tuning culling signal weights")
        train_args = [
            "--source-manifest",
            str(manifest_path),
            "--output-dir",
            str(signals_dir),
            "--profile",
            self.profile_name,
        ]
        train_command = _resolve_stage_command(
            self.runtime,
            script_relative_path="scripts/train_culling_combiner.py",
            stage_args=train_args,
        )

        try:
            completed = _run_command_with_live_output(
                train_command,
                cwd=self.runtime.engine_root,
                progress_callback=lambda line: _emit_command_progress(
                    self.signals,
                    line,
                    default_message="Tuning culling signal weights",
                ),
            )
            if completed.returncode != 0:
                raise RuntimeError(_command_failure_message("Tuning culling signals", completed.stdout))
        except Exception as exc:
            self.signals.failed.emit(str(exc))
            return

        self.signals.finished.emit(
            {
                "signals_dir": str(signals_dir),
                "weights_path": str(weights_path),
                "feature_rows_path": str(feature_rows_path),
                "source_manifest_path": str(manifest_path),
                "source_count": len(source_paths),
                "profile_name": self.profile_name,
            }
        )


class BuildReferenceBankTask(QRunnable):
    """Builds the reusable reference-bank artifact used by later ranker runs."""
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
        stage_args = [
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
        command = _resolve_stage_command(
            self.runtime,
            script_relative_path="scripts/build_reference_bank.py",
            stage_args=stage_args,
        )

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
    disagreement_count: int,
    reference_bank_path: str,
    profile_key: str,
) -> None:
    resolved_profile_key, resolved_profile_label = normalize_ranker_profile(profile_key)
    payload = {
        "run_id": run_id,
        "display_name": display_name.strip() or run_id,
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "pairwise_labels": max(0, int(pairwise_count)),
        "cluster_labels": max(0, int(cluster_count)),
        "disagreement_pair_labels": max(0, int(disagreement_count)),
        "reference_bank_path": reference_bank_path.strip(),
        "profile_key": resolved_profile_key,
        "profile_label": resolved_profile_label,
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


def _write_labeled_training_include_file(paths: AITrainingPaths) -> tuple[Path | None, set[str]]:
    """Write the relative-path include list for images that appear in saved labels."""

    labeled_image_ids = _collect_labeled_training_image_ids(paths)
    if not labeled_image_ids:
        return None, set()

    metadata_rows = _read_csv_rows(paths.labeling_metadata_path)
    if not metadata_rows:
        metadata_rows = _read_csv_rows(paths.artifacts_dir / "images.csv")
    relative_paths_by_id = {
        str(row.get("image_id") or "").strip(): str(row.get("relative_path") or "").strip()
        for row in metadata_rows
        if str(row.get("image_id") or "").strip() and str(row.get("relative_path") or "").strip()
    }
    included_paths = sorted(
        {
            relative_paths_by_id[image_id].replace("\\", "/").lstrip("./")
            for image_id in labeled_image_ids
            if image_id in relative_paths_by_id
        },
        key=str.casefold,
    )
    if not included_paths:
        return None, labeled_image_ids

    include_file = paths.artifacts_dir / "labeled_include_paths.txt"
    include_file.parent.mkdir(parents=True, exist_ok=True)
    include_file.write_text("\n".join(included_paths) + "\n", encoding="utf-8")
    included_image_ids = {
        image_id for image_id, relative_path in relative_paths_by_id.items() if relative_path.replace("\\", "/").lstrip("./") in included_paths
    }
    return include_file, included_image_ids


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


def _write_labeled_training_clusters(paths: AITrainingPaths, included_image_ids: set[str]) -> None:
    """Preserve labeling cluster IDs for the subset of images embedded for training."""

    embedded_rows = _read_csv_rows(paths.artifacts_dir / "images.csv")
    embedded_rows_by_id = {
        str(row.get("image_id") or "").strip(): dict(row)
        for row in embedded_rows
        if str(row.get("image_id") or "").strip()
    }
    embedded_ids = {
        str(row.get("image_id") or "").strip()
        for row in embedded_rows
        if str(row.get("image_id") or "").strip()
    }
    target_ids = embedded_ids & set(included_image_ids)
    if not target_ids:
        target_ids = embedded_ids

    source_rows = [
        dict(row)
        for row in _read_csv_rows(paths.labeling_clusters_path)
        if str(row.get("image_id") or "").strip() in target_ids
    ]
    rows_by_image_id = {str(row.get("image_id") or "").strip(): row for row in source_rows}
    for record in _iter_jsonl_records(paths.cluster_labels_path):
        cluster_id = str(record.get("cluster_id") or "").strip()
        if not cluster_id:
            continue
        labeled_ids: list[str] = []
        seen_labeled_ids: set[str] = set()
        for key in ("best_image_ids", "acceptable_image_ids", "reject_image_ids"):
            values = record.get(key)
            if not isinstance(values, list):
                continue
            for value in values:
                image_id = str(value or "").strip()
                if image_id and image_id not in seen_labeled_ids:
                    seen_labeled_ids.add(image_id)
                    labeled_ids.append(image_id)
        for position, image_id in enumerate(labeled_ids):
            if image_id not in target_ids:
                continue
            metadata_row = embedded_rows_by_id.get(image_id)
            if metadata_row is None:
                continue
            row = _synthetic_cluster_row_from_metadata(metadata_row)
            row["cluster_id"] = cluster_id
            row["cluster_position"] = str(position)
            row["cluster_reason"] = "saved_training_label"
            rows_by_image_id[image_id] = row
    for row in embedded_rows:
        image_id = str(row.get("image_id") or "").strip()
        if not image_id or image_id in rows_by_image_id:
            continue
        rows_by_image_id[image_id] = _synthetic_cluster_row_from_metadata(row)

    grouped: dict[str, list[dict[str, str]]] = {}
    for row in rows_by_image_id.values():
        cluster_id = str(row.get("cluster_id") or "").strip() or f"label_cluster_{len(grouped):04d}"
        row["cluster_id"] = cluster_id
        grouped.setdefault(cluster_id, []).append(row)

    output_rows: list[dict[str, str]] = []
    for cluster_id in sorted(grouped, key=str.casefold):
        rows = sorted(
            grouped[cluster_id],
            key=lambda item: (
                _safe_int(item.get("cluster_position"), default=999999),
                str(item.get("file_name") or "").casefold(),
            ),
        )
        for position, row in enumerate(rows):
            normalized = {str(key): str(value or "") for key, value in row.items()}
            normalized["cluster_id"] = cluster_id
            normalized["cluster_size"] = str(len(rows))
            normalized["cluster_position"] = str(position)
            output_rows.append(normalized)

    fieldnames = [
        "image_id",
        "cluster_id",
        "cluster_size",
        "cluster_position",
        "time_window_id",
        "window_kind",
        "cluster_reason",
        "capture_timestamp",
        "capture_time_source",
        "file_path",
        "relative_path",
        "file_name",
    ]
    for row in output_rows:
        fieldnames = _merge_fieldnames(fieldnames, row)
    _write_csv_rows(paths.artifacts_dir / "clusters.csv", output_rows, fieldnames=fieldnames)


def _synthetic_cluster_row_from_metadata(row: dict[str, str]) -> dict[str, str]:
    image_id = str(row.get("image_id") or "").strip()
    return {
        "image_id": image_id,
        "cluster_id": f"label_single_{image_id[:12]}",
        "cluster_size": "1",
        "cluster_position": "0",
        "time_window_id": f"label_single_{image_id[:12]}",
        "window_kind": "singleton",
        "cluster_reason": "labeled_training_singleton",
        "capture_timestamp": str(row.get("capture_timestamp") or ""),
        "capture_time_source": str(row.get("capture_time_source") or ""),
        "file_path": str(row.get("file_path") or ""),
        "relative_path": str(row.get("relative_path") or ""),
        "file_name": str(row.get("file_name") or ""),
    }


def _safe_int(value: object, *, default: int = 0) -> int:
    try:
        return int(str(value or "").strip())
    except (TypeError, ValueError):
        return default


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


def _general_pool_cache_key(sources: list[dict[str, object]]) -> str:
    payload = [
        {
            "folder": str(source["folder"]),
            "namespace": str(source["namespace"]),
            "pairwise_labels": int(source["pairwise_labels"]),
            "cluster_labels": int(source["cluster_labels"]),
            "disagreement_pair_labels": int(source["disagreement_pair_labels"]),
            "signatures": list(source["signatures"]),
        }
        for source in sources
    ]
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha1(encoded, usedforsecurity=False).hexdigest()


def _rebuild_general_training_pool(paths: AITrainingPaths, sources: list[dict[str, object]]) -> None:
    metadata_rows: list[dict[str, str]] = []
    metadata_fieldnames: list[str] = []
    cluster_rows: list[dict[str, str]] = []
    cluster_fieldnames: list[str] = []
    image_ids: list[str] = []
    embeddings_chunks: list[np.ndarray] = []
    pairwise_records: list[dict[str, object]] = []
    cluster_records: list[dict[str, object]] = []
    embedding_offset = 0

    for source in sources:
        source_paths = source["paths"]
        assert isinstance(source_paths, AITrainingPaths)
        namespace = str(source["namespace"])
        source_metadata_rows = _read_csv_rows(source_paths.artifacts_dir / "images.csv")
        source_cluster_rows = _read_csv_rows(source_paths.artifacts_dir / "clusters.csv")
        source_image_ids = _read_json_list(source_paths.artifacts_dir / "image_ids.json")
        source_embeddings = np.load(source_paths.artifacts_dir / "embeddings.npy").astype(np.float32, copy=False)
        if source_embeddings.ndim != 2:
            raise ValueError(f"Expected 2-D embeddings in {source_paths.artifacts_dir / 'embeddings.npy'}.")
        if len(source_image_ids) != int(source_embeddings.shape[0]):
            raise ValueError(
                f"image_ids.json and embeddings.npy are out of sync for {source['folder']}."
            )
        image_id_map = {
            old_image_id: _namespaced_identifier(namespace, old_image_id)
            for old_image_id in source_image_ids
        }
        cluster_id_map: dict[str, str] = {}
        for row in source_cluster_rows:
            cluster_id = str(row.get("cluster_id") or "").strip()
            if cluster_id and cluster_id not in cluster_id_map:
                cluster_id_map[cluster_id] = _namespaced_identifier(namespace, cluster_id)

        for row in source_metadata_rows:
            merged = {str(key): str(value or "") for key, value in row.items()}
            image_id = str(row.get("image_id") or "").strip()
            if image_id:
                merged["image_id"] = image_id_map.get(image_id, _namespaced_identifier(namespace, image_id))
            embedding_index_text = str(row.get("embedding_index") or "").strip()
            if embedding_index_text:
                merged["embedding_index"] = str(embedding_offset + int(embedding_index_text))
            metadata_fieldnames = _merge_fieldnames(metadata_fieldnames, merged)
            metadata_rows.append(merged)

        for row in source_cluster_rows:
            merged = {str(key): str(value or "") for key, value in row.items()}
            image_id = str(row.get("image_id") or "").strip()
            if image_id:
                merged["image_id"] = image_id_map.get(image_id, _namespaced_identifier(namespace, image_id))
            cluster_id = str(row.get("cluster_id") or "").strip()
            if cluster_id:
                merged["cluster_id"] = cluster_id_map.get(cluster_id, _namespaced_identifier(namespace, cluster_id))
            time_window_id = str(row.get("time_window_id") or "").strip()
            if time_window_id:
                merged["time_window_id"] = _namespaced_identifier(namespace, time_window_id)
            cluster_fieldnames = _merge_fieldnames(cluster_fieldnames, merged)
            cluster_rows.append(merged)

        image_ids.extend(image_id_map.get(old_image_id, _namespaced_identifier(namespace, old_image_id)) for old_image_id in source_image_ids)
        embeddings_chunks.append(source_embeddings)
        pairwise_records.extend(_rewrite_pairwise_label_records(source_paths.pairwise_labels_path, image_id_map, cluster_id_map, namespace))
        cluster_records.extend(_rewrite_cluster_label_records(source_paths.cluster_labels_path, image_id_map, cluster_id_map, namespace))
        embedding_offset += int(source_embeddings.shape[0])

    if not embeddings_chunks:
        raise ValueError("No pooled embeddings were found for the General Use workspace.")

    embeddings = np.concatenate(embeddings_chunks, axis=0)
    np.save(paths.artifacts_dir / "embeddings.npy", embeddings)
    _write_csv_rows(paths.artifacts_dir / "images.csv", metadata_rows, fieldnames=metadata_fieldnames)
    _write_csv_rows(paths.artifacts_dir / "clusters.csv", cluster_rows, fieldnames=cluster_fieldnames)
    (paths.artifacts_dir / "image_ids.json").write_text(json.dumps(image_ids, indent=2), encoding="utf-8")
    _write_jsonl_records(paths.pairwise_labels_path, pairwise_records)
    _write_jsonl_records(paths.cluster_labels_path, cluster_records)


def _rewrite_pairwise_label_records(
    path: Path,
    image_id_map: dict[str, str],
    cluster_id_map: dict[str, str],
    namespace: str,
) -> list[dict[str, object]]:
    rewritten: list[dict[str, object]] = []
    for record in _iter_jsonl_records(path):
        updated = dict(record)
        for key in ("image_a_id", "image_b_id", "preferred_image_id"):
            value = str(record.get(key) or "").strip()
            if value:
                updated[key] = image_id_map.get(value, value)
        cluster_id = str(record.get("cluster_id") or "").strip()
        if cluster_id:
            updated["cluster_id"] = cluster_id_map.get(cluster_id, _namespaced_identifier(namespace, cluster_id))
        rewritten.append(updated)
    return rewritten


def _rewrite_cluster_label_records(
    path: Path,
    image_id_map: dict[str, str],
    cluster_id_map: dict[str, str],
    namespace: str,
) -> list[dict[str, object]]:
    rewritten: list[dict[str, object]] = []
    for record in _iter_jsonl_records(path):
        updated = dict(record)
        cluster_id = str(record.get("cluster_id") or "").strip()
        if cluster_id:
            updated["cluster_id"] = cluster_id_map.get(cluster_id, _namespaced_identifier(namespace, cluster_id))
        for key in ("best_image_ids", "acceptable_image_ids", "reject_image_ids"):
            values = record.get(key) if isinstance(record.get(key), list) else []
            updated[key] = [image_id_map.get(str(value), str(value)) for value in values]
        rewritten.append(updated)
    return rewritten


def _path_signature(path: Path) -> tuple[str, int, int]:
    try:
        stat_result = path.stat()
    except OSError:
        return str(path), -1, -1
    return str(path), int(stat_result.st_size), int(stat_result.st_mtime_ns)


def _general_source_namespace(folder: str) -> str:
    normalized = _normalize_source_folder(folder)
    return hashlib.sha1(normalized.encode("utf-8"), usedforsecurity=False).hexdigest()[:16]


def _namespaced_identifier(namespace: str, value: str) -> str:
    token = f"{namespace}:{str(value or '').strip()}"
    return hashlib.sha1(token.encode("utf-8"), usedforsecurity=False).hexdigest()


def _normalize_source_folder(folder: str | Path) -> str:
    try:
        candidate = Path(folder).expanduser().resolve()
    except OSError:
        return ""
    return str(candidate) if candidate.exists() else ""


def _general_pool_manifest_path(paths: AITrainingPaths) -> Path:
    return paths.hidden_root / GENERAL_POOL_MANIFEST_FILENAME


def _write_general_pool_manifest(paths: AITrainingPaths, payload: dict[str, object]) -> None:
    _general_pool_manifest_path(paths).write_text(json.dumps(payload, indent=2), encoding="utf-8")


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


def _merge_fieldnames(existing: list[str], row: dict[str, str]) -> list[str]:
    merged = list(existing)
    seen = set(existing)
    for key in row.keys():
        if key not in seen:
            seen.add(key)
            merged.append(key)
    return merged


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


def _write_jsonl_records(path: Path, records: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=True) + "\n")
