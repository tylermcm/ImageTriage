from __future__ import annotations

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
from PySide6.QtCore import QObject, QRunnable, Signal

from .ai_workflow import (
    AIWorkflowPaths,
    AIWorkflowRuntime,
    ARTIFACTS_DIR_NAME,
    REPORT_DIR_NAME,
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
from .formats import FITS_SUFFIXES, suffix_for_path
from .metadata import EMPTY_METADATA, CaptureMetadata, load_capture_metadata
from .models import ImageRecord, SortMode, sort_records
from .scan_cache import app_data_root


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
GENERAL_TRAINING_ROOT_DIR_NAME = "ai_training"
GENERAL_TRAINING_PROFILE_DIR_NAME = "general_use"
GENERAL_POOL_MANIFEST_FILENAME = "general_pool_manifest.json"
GENERAL_RETRAIN_RECOMMENDATION_MIN_LABELS = 24
LABELING_READY_FILE_ENV = "IMAGE_TRIAGE_LABELING_READY_FILE"
LABELING_READY_WAIT_TIMEOUT_SECONDS = 45.0
LABELING_READY_POLL_INTERVAL_SECONDS = 0.15
DEFAULT_RANKER_PROFILE_KEY = "general"
RANKER_PROFILE_OPTIONS: tuple[tuple[str, str], ...] = (
    (DEFAULT_RANKER_PROFILE_KEY, "General Use"),
    ("portrait", "Portrait"),
    ("landscape", "Landscape"),
    ("wildlife", "Wildlife"),
    ("sports_action", "Sports / Action"),
    ("event_documentary", "Event / Documentary"),
    ("astro", "Astro"),
    ("macro_product", "Macro / Product"),
)
PROFILE_KEYWORD_WEIGHTS: dict[str, dict[str, float]] = {
    "portrait": {
        "portrait": 3.0,
        "portraits": 3.0,
        "headshot": 3.0,
        "headshots": 3.0,
        "model": 2.0,
        "models": 2.0,
        "engagement": 2.0,
        "family": 2.0,
        "senior": 2.0,
        "newborn": 2.0,
        "fashion": 2.0,
    },
    "landscape": {
        "landscape": 3.0,
        "landscapes": 3.0,
        "mountain": 2.0,
        "mountains": 2.0,
        "sunrise": 2.0,
        "sunset": 2.0,
        "waterfall": 2.0,
        "waterfalls": 2.0,
        "travel": 1.5,
        "scenic": 2.0,
        "vista": 2.0,
        "hike": 1.5,
    },
    "wildlife": {
        "wildlife": 3.0,
        "bird": 3.0,
        "birds": 3.0,
        "eagle": 3.0,
        "hawk": 3.0,
        "owl": 3.0,
        "deer": 2.0,
        "elk": 2.0,
        "bear": 2.0,
        "fox": 2.0,
        "duck": 2.0,
        "safari": 2.0,
    },
    "sports_action": {
        "sports": 3.0,
        "sport": 3.0,
        "football": 3.0,
        "soccer": 3.0,
        "basketball": 3.0,
        "baseball": 3.0,
        "hockey": 3.0,
        "race": 2.0,
        "racing": 2.0,
        "action": 2.0,
        "surf": 2.0,
        "skate": 2.0,
    },
    "event_documentary": {
        "event": 3.0,
        "events": 3.0,
        "wedding": 3.0,
        "reception": 2.0,
        "ceremony": 2.0,
        "concert": 2.0,
        "party": 2.0,
        "street": 1.5,
        "documentary": 2.0,
        "festival": 2.0,
        "conference": 2.0,
    },
    "astro": {
        "astro": 4.0,
        "astrophotography": 4.0,
        "nebula": 4.0,
        "galaxy": 4.0,
        "andromeda": 4.0,
        "orion": 4.0,
        "moon": 3.0,
        "milkyway": 4.0,
        "milky": 2.0,
        "eclipse": 3.0,
        "ha": 2.0,
        "oiii": 2.0,
        "sii": 2.0,
        "luminance": 2.0,
    },
    "macro_product": {
        "macro": 4.0,
        "product": 4.0,
        "studio": 2.0,
        "catalog": 2.0,
        "watch": 2.0,
        "jewelry": 2.0,
        "ring": 2.0,
        "detail": 2.0,
        "flower": 1.5,
    },
}


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
    profile_key: str = DEFAULT_RANKER_PROFILE_KEY
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
    profile_key: str
    profile_label: str
    fit_diagnosis: "RankerFitDiagnosis"
    is_active: bool = False
    is_legacy: bool = False


@dataclass(slots=True, frozen=True)
class RankerFitDiagnosis:
    code: str
    label: str
    summary: str
    remedy: str


@dataclass(slots=True, frozen=True)
class RankerProfileSuggestion:
    profile_key: str
    profile_label: str
    reason: str
    confidence: float = 0.0
    confidence_label: str = "Low"
    is_mixed: bool = False


@dataclass(slots=True, frozen=True)
class GeneralTrainingPoolStatus:
    paths: AITrainingPaths
    pairwise_labels: int
    cluster_labels: int
    source_folders: int
    labels_added_since_train: int = 0
    needs_retrain: bool = False
    guidance_text: str = ""
    cached: bool = False


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


def build_general_ai_training_paths() -> AITrainingPaths:
    root = app_data_root() / GENERAL_TRAINING_ROOT_DIR_NAME / GENERAL_TRAINING_PROFILE_DIR_NAME
    workflow_paths = AIWorkflowPaths(
        folder=root,
        hidden_root=root,
        artifacts_dir=root / ARTIFACTS_DIR_NAME,
        report_dir=root / REPORT_DIR_NAME,
        ranked_export_path=(root / REPORT_DIR_NAME) / "ranked_clusters_export.csv",
        html_report_path=(root / REPORT_DIR_NAME) / "ranked_clusters_report.html",
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
    prepare_hidden_ai_workspace(folder)
    paths = build_ai_training_paths(folder)
    paths.labeling_artifacts_dir.mkdir(parents=True, exist_ok=True)
    paths.labels_dir.mkdir(parents=True, exist_ok=True)
    paths.training_dir.mkdir(parents=True, exist_ok=True)
    paths.training_runs_dir.mkdir(parents=True, exist_ok=True)
    paths.evaluation_dir.mkdir(parents=True, exist_ok=True)
    paths.reference_bank_dir.mkdir(parents=True, exist_ok=True)
    return paths


def prepare_general_ai_training_workspace() -> AITrainingPaths:
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


def preview_general_training_pool(
    source_folders: list[str] | tuple[str, ...],
    *,
    reference_run: RankerRunInfo | None = None,
) -> GeneralTrainingPoolStatus:
    paths = build_general_ai_training_paths()
    sources = _collect_general_training_sources(source_folders)
    pairwise_total = sum(source["pairwise_labels"] for source in sources)
    cluster_total = sum(source["cluster_labels"] for source in sources)
    return _general_training_pool_status(
        paths=paths,
        pairwise_total=pairwise_total,
        cluster_total=cluster_total,
        source_count=len(sources),
        reference_run=reference_run,
        cached=False,
    )


def prepare_general_training_pool(
    source_folders: list[str] | tuple[str, ...],
    *,
    reference_run: RankerRunInfo | None = None,
) -> GeneralTrainingPoolStatus:
    paths = prepare_general_ai_training_workspace()
    sources = _collect_general_training_sources(source_folders)
    pairwise_total = sum(source["pairwise_labels"] for source in sources)
    cluster_total = sum(source["cluster_labels"] for source in sources)
    if not sources:
        _write_general_pool_manifest(
            paths,
            {
                "cache_key": "",
                "source_folders": [],
                "pairwise_labels": 0,
                "cluster_labels": 0,
                "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            },
        )
        return _general_training_pool_status(
            paths=paths,
            pairwise_total=0,
            cluster_total=0,
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
            "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        },
    )
    return _general_training_pool_status(
        paths=paths,
        pairwise_total=pairwise_total,
        cluster_total=cluster_total,
        source_count=len(sources),
        reference_run=reference_run,
        cached=False,
    )


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


def find_ranker_run_by_checkpoint(paths: AITrainingPaths, checkpoint_path: str | Path | None) -> RankerRunInfo | None:
    if checkpoint_path is None:
        return None
    candidate = Path(checkpoint_path).expanduser()
    for run in list_ranker_runs(paths):
        if run.checkpoint_path is not None and _same_path(candidate, run.checkpoint_path):
            return run
    return None


def ranker_profile_options() -> tuple[tuple[str, str], ...]:
    return RANKER_PROFILE_OPTIONS


def normalize_ranker_profile(profile_value: object) -> tuple[str, str]:
    token = _profile_token(profile_value)
    for key, label in RANKER_PROFILE_OPTIONS:
        if token in {_profile_token(key), _profile_token(label)}:
            return key, label
    return DEFAULT_RANKER_PROFILE_KEY, dict(RANKER_PROFILE_OPTIONS)[DEFAULT_RANKER_PROFILE_KEY]


def suggest_training_profile(
    records: list[ImageRecord],
    *,
    sample_limit: int = 48,
    metadata_loader: Callable[[str], CaptureMetadata] | None = None,
) -> RankerProfileSuggestion:
    default_key, default_label = normalize_ranker_profile(DEFAULT_RANKER_PROFILE_KEY)
    if not records:
        return RankerProfileSuggestion(default_key, default_label, "No images loaded. Keeping General Use.")

    sampled_records = _sample_profile_records(records, limit=sample_limit)
    total = max(1, len(sampled_records))
    scores = {key: 0.0 for key, _label in RANKER_PROFILE_OPTIONS if key != DEFAULT_RANKER_PROFILE_KEY}
    evidence: dict[str, list[str]] = {key: [] for key in scores}

    fits_count = sum(1 for record in sampled_records if suffix_for_path(record.path) in FITS_SUFFIXES)
    if fits_count / total >= 0.35:
        return RankerProfileSuggestion(
            "astro",
            "Astro",
            "Suggested Astro (high confidence) because this folder is heavily FITS-based.",
            confidence=0.98,
            confidence_label="High",
        )

    loader = metadata_loader or load_capture_metadata
    metadata_rows: list[CaptureMetadata] = []
    for record in sampled_records:
        _apply_keyword_scores(record.path, scores, evidence)
        try:
            metadata = loader(record.path)
        except Exception:
            metadata = EMPTY_METADATA
        if isinstance(metadata, CaptureMetadata):
            metadata_rows.append(metadata)

    if not metadata_rows:
        return _finalize_profile_suggestion(
            scores=scores,
            evidence=evidence,
            default_key=default_key,
            default_label=default_label,
            fallback_reason="No usable metadata found. Keeping General Use.",
        )

    portrait_count = sum(1 for item in metadata_rows if item.height > item.width > 0)
    landscape_count = sum(1 for item in metadata_rows if item.width > item.height > 0)
    wide_count = sum(1 for item in metadata_rows if item.focal_length_value is not None and item.focal_length_value <= 35.0)
    short_tele_count = sum(1 for item in metadata_rows if item.focal_length_value is not None and 50.0 <= item.focal_length_value <= 135.0)
    long_lens_count = sum(1 for item in metadata_rows if item.focal_length_value is not None and item.focal_length_value >= 250.0)
    fast_shutter_count = sum(
        1 for item in metadata_rows if item.exposure_seconds is not None and item.exposure_seconds <= (1.0 / 1000.0)
    )
    long_exposure_count = sum(1 for item in metadata_rows if item.exposure_seconds is not None and item.exposure_seconds >= 1.0)
    wide_aperture_count = sum(1 for item in metadata_rows if item.aperture_value is not None and item.aperture_value <= 4.0)
    high_iso_count = sum(1 for item in metadata_rows if item.iso_value is not None and item.iso_value >= 1600.0)

    usable = max(1, len(metadata_rows))
    portrait_ratio = portrait_count / usable
    landscape_ratio = landscape_count / usable
    wide_ratio = wide_count / usable
    short_tele_ratio = short_tele_count / usable
    long_lens_ratio = long_lens_count / usable
    fast_shutter_ratio = fast_shutter_count / usable
    long_exposure_ratio = long_exposure_count / usable
    wide_aperture_ratio = wide_aperture_count / usable
    high_iso_ratio = high_iso_count / usable

    scores["astro"] += _ratio_points(long_exposure_ratio, start=0.18, full=0.55, weight=8.0)
    scores["astro"] += _ratio_points(high_iso_ratio, start=0.12, full=0.45, weight=4.5)
    scores["portrait"] += _ratio_points(portrait_ratio, start=0.35, full=0.75, weight=5.0)
    scores["portrait"] += _ratio_points(short_tele_ratio, start=0.25, full=0.65, weight=4.0)
    scores["portrait"] += _ratio_points(wide_aperture_ratio, start=0.2, full=0.55, weight=2.5)
    scores["landscape"] += _ratio_points(landscape_ratio, start=0.45, full=0.85, weight=4.5)
    scores["landscape"] += _ratio_points(wide_ratio, start=0.25, full=0.65, weight=4.0)
    scores["wildlife"] += _ratio_points(long_lens_ratio, start=0.25, full=0.7, weight=5.0)
    scores["wildlife"] += _ratio_points(fast_shutter_ratio, start=0.12, full=0.45, weight=3.0)
    scores["sports_action"] += _ratio_points(fast_shutter_ratio, start=0.3, full=0.75, weight=5.0)
    scores["sports_action"] += _ratio_points(high_iso_ratio, start=0.12, full=0.5, weight=2.5)
    scores["sports_action"] += _ratio_points(long_lens_ratio, start=0.1, full=0.4, weight=1.5)
    scores["event_documentary"] += _ratio_points(high_iso_ratio, start=0.2, full=0.6, weight=4.0)
    scores["event_documentary"] += _ratio_points(short_tele_ratio, start=0.1, full=0.45, weight=1.8)
    if 0.2 <= portrait_ratio <= 0.8:
        scores["event_documentary"] += 1.0
    scores["macro_product"] += _ratio_points(wide_aperture_ratio, start=0.2, full=0.6, weight=1.8)
    if portrait_ratio >= 0.5:
        evidence["portrait"].append("portrait-heavy framing")
    if short_tele_ratio >= 0.35:
        evidence["portrait"].append("portrait focal lengths")
    if wide_aperture_ratio >= 0.25:
        evidence["portrait"].append("wide apertures")
    if landscape_ratio >= 0.7:
        evidence["landscape"].append("mostly horizontal framing")
    if wide_ratio >= 0.45:
        evidence["landscape"].append("wide-angle focal lengths")
    if long_lens_ratio >= 0.45:
        evidence["wildlife"].append("long-lens coverage")
    if fast_shutter_ratio >= 0.45:
        evidence["sports_action"].append("very fast shutter speeds")
    if long_exposure_ratio >= 0.3:
        evidence["astro"].append("long exposures")
    if high_iso_ratio >= 0.3:
        evidence["astro"].append("high ISO usage")
        evidence["event_documentary"].append("high ISO usage")

    return _finalize_profile_suggestion(
        scores=scores,
        evidence=evidence,
        default_key=default_key,
        default_label=default_label,
        fallback_reason="No strong class match found. Keeping General Use.",
    )


def set_active_ranker_selection(
    paths: AITrainingPaths,
    *,
    checkpoint_path: str | Path,
    run_id: str = "",
    display_name: str = "",
    profile_key: str = DEFAULT_RANKER_PROFILE_KEY,
    profile_label: str = "",
) -> None:
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
    ready_file_path: str | Path | None = None,
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
    if ready_file_path is not None:
        env[LABELING_READY_FILE_ENV] = str(Path(ready_file_path).expanduser().resolve())
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
    def __init__(
        self,
        *,
        folder: Path,
        runtime: AIWorkflowRuntime,
        annotator_id: str = "",
        artifacts_dir: str | Path | None = None,
        appearance_mode: str | None = None,
        parent_pid: int | None = None,
        sync_file_path: str | Path | None = None,
    ) -> None:
        super().__init__()
        self.folder = folder
        self.runtime = runtime
        self.annotator_id = annotator_id.strip()
        self.artifacts_dir = artifacts_dir
        self.appearance_mode = appearance_mode
        self.parent_pid = parent_pid
        self.sync_file_path = sync_file_path
        self.signals = AITrainingTaskSignals()
        self.setAutoDelete(True)

    def run(self) -> None:
        ready_path = Path(tempfile.gettempdir()) / f"image_triage_labeling_ready_{os.getpid()}_{int(time.time() * 1000)}.flag"
        try:
            ready_path.unlink(missing_ok=True)
        except OSError:
            pass

        self.signals.started.emit(1)
        self.signals.stage.emit(1, 1, "Opening Collect Training Labels")
        self.signals.progress.emit(0, 0, "Starting label collection window...")

        try:
            process = launch_labeling_app(
                self.runtime,
                folder=self.folder,
                annotator_id=self.annotator_id,
                artifacts_dir=self.artifacts_dir,
                ready_file_path=ready_path,
                appearance_mode=self.appearance_mode,
                parent_pid=self.parent_pid,
                sync_file_path=self.sync_file_path,
            )
        except Exception as exc:
            self.signals.failed.emit(str(exc))
            return

        start_time = time.monotonic()
        ready_acknowledged = False
        last_status = ""
        while True:
            if ready_path.exists():
                state, details = _read_labeling_startup_state(ready_path)
                if state == "ready":
                    ready_acknowledged = True
                    break
                if state == "error":
                    self.signals.failed.emit(details or "Collect Training Labels failed while starting.")
                    return
            return_code = process.poll()
            if return_code is not None:
                self.signals.failed.emit(
                    "Collect Training Labels closed before the window finished opening."
                    if return_code == 0
                    else f"Collect Training Labels exited while opening (exit code {return_code})."
                )
                return
            elapsed = time.monotonic() - start_time
            if elapsed >= LABELING_READY_WAIT_TIMEOUT_SECONDS:
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
            else:
                paths = prepare_hidden_ai_training_workspace(self.folder)
                pairwise_count, cluster_count = count_label_records(paths)
            _validate_runtime_paths(
                self.runtime,
                required=(
                    ("engine root", self.runtime.engine_root),
                    ("python executable", self.runtime.python_executable),
                    ("train config", self.runtime.engine_root / "configs" / "train_ranker.json"),
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
    def __init__(
        self,
        *,
        folder: Path,
        runtime: AIWorkflowRuntime,
        checkpoint_path: Path,
        reference_bank_path: str = "",
        use_general_pool: bool = False,
        general_source_folders: tuple[str, ...] = (),
    ) -> None:
        super().__init__()
        self.folder = folder
        self.runtime = runtime
        self.checkpoint_path = checkpoint_path
        self.reference_bank_path = reference_bank_path.strip()
        self.use_general_pool = bool(use_general_pool)
        self.general_source_folders = tuple(str(folder) for folder in general_source_folders if str(folder).strip())
        self.signals = AITrainingTaskSignals()
        self.setAutoDelete(True)

    def run(self) -> None:
        try:
            if self.use_general_pool:
                pool_status = prepare_general_training_pool(self.general_source_folders)
                paths = pool_status.paths
                pairwise_count = pool_status.pairwise_labels
                cluster_count = pool_status.cluster_labels
            else:
                paths = prepare_hidden_ai_training_workspace(self.folder)
                pairwise_count, cluster_count = count_label_records(paths)
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
                "checkpoint_path": str(self.checkpoint_path),
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
    profile_key: str,
) -> None:
    resolved_profile_key, resolved_profile_label = normalize_ranker_profile(profile_key)
    payload = {
        "run_id": run_id,
        "display_name": display_name.strip() or run_id,
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "pairwise_labels": max(0, int(pairwise_count)),
        "cluster_labels": max(0, int(cluster_count)),
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
    )


def _general_training_pool_status(
    *,
    paths: AITrainingPaths,
    pairwise_total: int,
    cluster_total: int,
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
            f"{pairwise_total} pairwise and {cluster_total} cluster labels."
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
    )


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
        pairwise_count, cluster_count = count_label_records(paths)
        if pairwise_count <= 0 and cluster_count <= 0:
            continue
        collected.append(
            {
                "folder": normalized_folder,
                "paths": paths,
                "namespace": _general_source_namespace(normalized_folder),
                "pairwise_labels": pairwise_count,
                "cluster_labels": cluster_count,
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
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
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


def _finalize_profile_suggestion(
    *,
    scores: dict[str, float],
    evidence: dict[str, list[str]],
    default_key: str,
    default_label: str,
    fallback_reason: str,
) -> RankerProfileSuggestion:
    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    if not ranked or ranked[0][1] <= 0.0:
        return RankerProfileSuggestion(default_key, default_label, fallback_reason)

    top_key, top_score = ranked[0]
    second_score = ranked[1][1] if len(ranked) > 1 else 0.0
    total_score = sum(max(0.0, value) for value in scores.values())
    confidence = max(0.0, min(1.0, top_score / max(1.0, total_score)))
    margin_ratio = (top_score - second_score) / max(1.0, top_score)
    strong_categories = sum(1 for _key, score in ranked if score >= top_score * 0.72 and score >= 4.0)
    low_confidence = top_score < 5.0 or confidence < 0.42
    mixed = strong_categories >= 2 or margin_ratio < 0.18
    if low_confidence or mixed:
        competing = ", ".join(
            normalize_ranker_profile(key)[1]
            for key, score in ranked[:3]
            if score >= max(3.0, top_score * 0.6)
        )
        if mixed and competing:
            reason = f"Folder looks mixed between {competing}. Keeping General Use."
        else:
            reason = fallback_reason
        return RankerProfileSuggestion(
            default_key,
            default_label,
            reason,
            confidence=confidence,
            confidence_label=_confidence_label(confidence),
            is_mixed=mixed,
        )

    profile_key, profile_label = normalize_ranker_profile(top_key)
    top_evidence = ", ".join(_dedupe_preserve_order(evidence.get(top_key, ()))[:3])
    reason = f"Suggested {profile_label} ({_confidence_label(confidence).lower()} confidence)"
    if top_evidence:
        reason += f" because of {top_evidence}."
    else:
        reason += "."
    return RankerProfileSuggestion(
        profile_key,
        profile_label,
        reason,
        confidence=confidence,
        confidence_label=_confidence_label(confidence),
        is_mixed=False,
    )


def _apply_keyword_scores(path: str, scores: dict[str, float], evidence: dict[str, list[str]]) -> None:
    lowered = str(path or "").casefold()
    tokens = set(re.findall(r"[a-z0-9]+", lowered))
    for profile_key, weights in PROFILE_KEYWORD_WEIGHTS.items():
        for keyword, weight in weights.items():
            if keyword in tokens or keyword in lowered:
                scores[profile_key] += weight
                evidence[profile_key].append(f"keyword '{keyword}'")


def _ratio_points(value: float, *, start: float, full: float, weight: float) -> float:
    if value <= start:
        return 0.0
    span = max(0.001, full - start)
    scaled = min(1.0, (value - start) / span)
    return scaled * weight


def _confidence_label(confidence: float) -> str:
    if confidence >= 0.68:
        return "High"
    if confidence >= 0.5:
        return "Medium"
    return "Low"


def _dedupe_preserve_order(items: list[str] | tuple[str, ...]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for item in items:
        token = str(item or "").strip()
        if not token or token in seen:
            continue
        seen.add(token)
        ordered.append(token)
    return ordered


def _sample_profile_records(records: list[ImageRecord], *, limit: int) -> list[ImageRecord]:
    if limit <= 0 or len(records) <= limit:
        return list(records)
    sampled: list[ImageRecord] = []
    seen: set[str] = set()
    max_index = len(records) - 1
    for position in range(limit):
        index = round(position * max_index / max(1, limit - 1))
        record = records[index]
        if record.path in seen:
            continue
        seen.add(record.path)
        sampled.append(record)
    return sampled


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
    metrics = _read_json_dict(metrics_path) if metrics_path is not None else {}
    history_rows = _read_training_history_rows(history_path)
    return diagnose_ranker_fit(metrics=metrics, history_rows=history_rows, num_epochs=num_epochs)


def diagnose_ranker_fit(
    *,
    metrics: dict[str, object] | None = None,
    history_rows: list[dict[str, object]] | None = None,
    num_epochs: int | None = None,
) -> RankerFitDiagnosis:
    metrics = metrics or {}
    history = history_rows or []
    best_epoch = _nested_int(metrics, "best_epoch")
    best_validation_accuracy = _nested_float(metrics, "best_validation_pairwise_accuracy")
    best_validation_loss = _nested_float(metrics, "best_validation_loss")
    final_train_loss = _nested_float(metrics, "final_train_loss")
    final_validation_loss = _nested_float(metrics, "final_validation_loss")
    final_validation_accuracy = _nested_float(metrics, "final_validation_pairwise_accuracy")

    if history:
        last_row = history[-1]
        final_train_loss = final_train_loss if final_train_loss is not None else _coerce_float(last_row.get("train_loss"))
        final_validation_loss = final_validation_loss if final_validation_loss is not None else _coerce_float(last_row.get("validation_loss"))
        final_validation_accuracy = (
            final_validation_accuracy
            if final_validation_accuracy is not None
            else _coerce_float(last_row.get("validation_pairwise_accuracy"))
        )
        if num_epochs is None:
            num_epochs = _coerce_int(last_row.get("epoch")) or len(history)
        if best_epoch is None:
            best_epoch = _best_epoch_from_history(history)
        if best_validation_loss is None:
            best_validation_loss = _history_best_float(history, "validation_loss", prefer="min")
        if best_validation_accuracy is None:
            best_validation_accuracy = _history_best_float(history, "validation_pairwise_accuracy", prefer="max")

    total_epochs = max(0, int(num_epochs or best_epoch or len(history) or 0))
    if best_epoch is None or total_epochs <= 0:
        return RankerFitDiagnosis(
            code="unknown",
            label="Too Early To Tell",
            summary="This run does not have enough validation history yet to judge training health.",
            remedy="Finish training and evaluation, then check again.",
        )

    early_best_threshold = max(2, int(round(total_epochs * 0.45)))
    late_best_threshold = max(1, int(round(total_epochs * 0.8)))
    loss_gap = _positive_gap(final_validation_loss, best_validation_loss)
    accuracy_drop = _positive_gap(best_validation_accuracy, final_validation_accuracy)
    train_validation_gap = _positive_gap(final_validation_loss, final_train_loss)

    if (
        best_epoch <= early_best_threshold
        and (
            _gap_exceeds(loss_gap, baseline=best_validation_loss, floor=0.03, ratio=0.12)
            or _gap_exceeds(accuracy_drop, baseline=best_validation_accuracy, floor=0.03, ratio=0.08)
            or _gap_exceeds(train_validation_gap, baseline=final_train_loss, floor=0.05, ratio=0.25)
        )
    ):
        return RankerFitDiagnosis(
            code="overfit",
            label="May Be Overfit",
            summary="The model peaked early and then got worse on held-out examples, so it may be memorizing the training labels.",
            remedy="Try fewer epochs, add more varied labels, or use a broader profile such as General Use for mixed folders.",
        )

    if (
        best_epoch >= late_best_threshold
        and (best_validation_accuracy is None or best_validation_accuracy < 0.78)
        and not _gap_exceeds(loss_gap, baseline=best_validation_loss, floor=0.02, ratio=0.05)
        and not _gap_exceeds(accuracy_drop, baseline=best_validation_accuracy, floor=0.02, ratio=0.05)
    ):
        return RankerFitDiagnosis(
            code="underfit",
            label="May Be Underfit",
            summary="The model was still improving near the end, so it probably has not learned enough yet.",
            remedy="Try more labels, a few more epochs, or a more focused profile. Use General Use when the folder mixes subjects.",
        )

    return RankerFitDiagnosis(
        code="healthy",
        label="Looks Healthy",
        summary="The model improved and settled without a clear late drop. This run looks reasonably balanced.",
        remedy="If the ranking still misses edge cases, label a few more of those cases and train another version.",
    )


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


def _coerce_float(value: object) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _coerce_int(value: object) -> int | None:
    try:
        if value in (None, ""):
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _history_best_float(rows: list[dict[str, object]], key: str, *, prefer: str) -> float | None:
    values = [value for value in (_coerce_float(row.get(key)) for row in rows) if value is not None]
    if not values:
        return None
    return min(values) if prefer == "min" else max(values)


def _best_epoch_from_history(rows: list[dict[str, object]]) -> int | None:
    best_row: dict[str, object] | None = None
    best_loss: float | None = None
    for row in rows:
        candidate_loss = _coerce_float(row.get("validation_loss"))
        if candidate_loss is None:
            continue
        if best_loss is None or candidate_loss < best_loss:
            best_loss = candidate_loss
            best_row = row
    if best_row is not None:
        return _coerce_int(best_row.get("epoch"))
    best_accuracy_row: dict[str, object] | None = None
    best_accuracy: float | None = None
    for row in rows:
        candidate_accuracy = _coerce_float(row.get("validation_pairwise_accuracy"))
        if candidate_accuracy is None:
            continue
        if best_accuracy is None or candidate_accuracy > best_accuracy:
            best_accuracy = candidate_accuracy
            best_accuracy_row = row
    if best_accuracy_row is not None:
        return _coerce_int(best_accuracy_row.get("epoch"))
    return None


def _positive_gap(later_value: float | None, earlier_value: float | None) -> float | None:
    if later_value is None or earlier_value is None:
        return None
    return max(0.0, later_value - earlier_value)


def _gap_exceeds(gap: float | None, *, baseline: float | None, floor: float, ratio: float) -> bool:
    if gap is None:
        return False
    tolerance = max(floor, abs(float(baseline or 0.0)) * ratio)
    return gap > tolerance


def _profile_token(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").strip().casefold())


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
