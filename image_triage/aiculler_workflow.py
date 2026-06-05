from __future__ import annotations

import csv
import hashlib
import html
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, Signal

from .ai_runtime_packages import resolve_ai_runtime_site_packages
from .ai_workflow import AIWorkflowPaths, AIWorkflowRuntime, build_ai_workflow_paths
from .aiculler_global_store import (
    GlobalAdapterLabel,
    default_global_adapter_db_path,
    default_global_adapter_workspace_path,
)
from .dino_prefilter import (
    DINOPrefilterMode,
    DINOPrefilterSettings,
    build_dino_prefilter_paths,
    default_dino_prefilter_settings,
    load_dino_prefilter_decisions,
    run_dino_prefilter_from_signal_rows,
)
from .formats import JPEG_SUFFIXES, suffix_for_path
from .models import ImageRecord
from .perceptual_hash import find_perceptual_duplicate_groups_with_stats
from .perf import perf_logger
from .phash_prefilter import (
    PHashExecutionMode,
    PHashPrefilterSettings,
    build_phash_prefilter_paths,
    default_phash_prefilter_settings,
    load_phash_prefilter_decisions,
    run_phash_prefilter_from_signal_rows,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_AICULLER_ROOT = REPO_ROOT
DEFAULT_AICULLER_ROOT = SOURCE_AICULLER_ROOT
DEFAULT_AICULLER_CONFIG_ROOT = REPO_ROOT / "aiculler" / "resources"
INGEST_EVENT_PATTERN = re.compile(r"^\[(?P<status>[^\]]+)\]\s+#(?P<current>\d+)\s+")
CLI_PROGRESS_PATTERN = re.compile(
    r"^\[(?P<label>[^\]]+)\]\s+(?P<current>\d+)/(?P<total>\d+)(?:\s+(?P<message>.*))?$"
)
CAPTURE_SEQUENCE_PATTERN = re.compile(r"^(?P<prefix>.*?)(?P<number>\d{3,})(?P<suffix>[^0-9]*)$")
CAPTURE_DIVERSITY_BUCKET_SIZE = 24


@dataclass(slots=True, frozen=True)
class AICullerClipModelVariant:
    key: str
    label: str
    description: str
    expected_delta: str
    warning: str = ""
    recommended: bool = False


CLIP_MODEL_VARIANT_ENV = "IMAGE_TRIAGE_AICULLER_CLIP_VARIANT"
DEFAULT_CLIP_MODEL_VARIANT = "uint8"
CLIP_MODEL_VARIANTS: tuple[AICullerClipModelVariant, ...] = (
    AICullerClipModelVariant(
        key="uint8",
        label="UInt8 (recommended)",
        description="Default CPU-friendly export. Balanced quality, compatibility, memory use, and speed.",
        expected_delta="Baseline speed for this app.",
        recommended=True,
    ),
    AICullerClipModelVariant(
        key="int8",
        label="Int8",
        description="Alternate 8-bit export. Usually fastest, with a higher risk of embedding drift.",
        expected_delta="Expected: slightly faster than UInt8.",
        warning="May change categories, duplicate grouping, and adapter behavior.",
    ),
    AICullerClipModelVariant(
        key="quantized",
        label="Quantized",
        description="Alternate quantized export with similar size and runtime profile to UInt8.",
        expected_delta="Expected: roughly similar to UInt8.",
        warning="Use only when comparing output drift against the recommended default.",
    ),
    AICullerClipModelVariant(
        key="q4",
        label="Q4",
        description="4-bit export. Smaller vision model, but more aggressive compression.",
        expected_delta="Expected: can be slower than UInt8 on CPU despite the smaller file.",
        warning="Higher risk of category/ranking drift. Validate before using for real culls.",
    ),
    AICullerClipModelVariant(
        key="bnb4",
        label="BNB4",
        description="4-bit BNB export. Smallest vision file, with the most aggressive compression.",
        expected_delta="Expected: may be slower than UInt8 on CPU.",
        warning="Highest risk of output drift. Treat as experimental.",
    ),
    AICullerClipModelVariant(
        key="fp32",
        label="FP32 full precision",
        description="Full precision split export. Largest model and slower on CPU.",
        expected_delta="Expected: slower than UInt8 and uses much more memory.",
        warning="Full precision is not automatically better for this workflow. Validate before switching.",
    ),
)


def clip_model_variant_options() -> tuple[AICullerClipModelVariant, ...]:
    return CLIP_MODEL_VARIANTS


def coerce_clip_model_variant(value: object) -> str:
    key = str(value or "").strip().lower()
    valid = {variant.key for variant in CLIP_MODEL_VARIANTS}
    return key if key in valid else DEFAULT_CLIP_MODEL_VARIANT


def clip_model_variant_info(value: object) -> AICullerClipModelVariant:
    key = coerce_clip_model_variant(value)
    for variant in CLIP_MODEL_VARIANTS:
        if variant.key == key:
            return variant
    return CLIP_MODEL_VARIANTS[0]


@dataclass(slots=True, frozen=True)
class AICullerRuntime:
    root: Path
    python_executable: Path
    cli_entrypoint: Path
    clip_vision_model: Path
    clip_text_model: Path
    tokenizer: Path
    clip_model_variant: str = DEFAULT_CLIP_MODEL_VARIANT
    topiq_model: Path | None = None
    categories_csv: Path | None = None
    tag_penalties_csv: Path | None = None
    avoid_tags: tuple[str, ...] = ("blownout", "harshlight", "outoffocus", "motionblur")
    penalty_weight: float = 0.85
    workers: int = 4

    def validate(self) -> None:
        missing: list[str] = []
        for label, path in (
            ("CLI-Culler root", self.root),
            ("CLI-Culler Python", self.python_executable),
            ("CLI-Culler entrypoint", self.cli_entrypoint),
            ("CLIP vision model", self.clip_vision_model),
            ("CLIP text model", self.clip_text_model),
            ("CLIP tokenizer", self.tokenizer),
        ):
            if not path.exists():
                missing.append(f"{label}: {path}")
        if self.categories_csv is not None and not self.categories_csv.exists():
            missing.append(f"category prompts: {self.categories_csv}")
        if self.topiq_model is not None and not self.topiq_model.exists():
            missing.append(f"TOPIQ model: {self.topiq_model}")
        if self.tag_penalties_csv is not None and not self.tag_penalties_csv.exists():
            missing.append(f"tag penalties: {self.tag_penalties_csv}")
        if missing:
            raise FileNotFoundError("Missing CLI-Culler runtime paths:\n" + "\n".join(missing))


@dataclass(slots=True, frozen=True)
class AICullerRuntimeStatus:
    runtime: AICullerRuntime
    missing_required: tuple[str, ...]
    missing_optional: tuple[str, ...]

    @property
    def is_ready(self) -> bool:
        return not self.missing_required


class AICullerRunSignals(QObject):
    started = Signal(str)
    stage = Signal(str, int, int, str)
    progress = Signal(str, str, int, int, str)
    detail = Signal(str, str)
    finished = Signal(str, str, str)
    failed = Signal(str, str)
    cancelled = Signal(str, str)


class AICullerCommandSignals(QObject):
    started = Signal(int)
    stage = Signal(int, int, str)
    progress = Signal(int, int, str)
    log = Signal(str)
    finished = Signal(object)
    failed = Signal(str)


ALL_AICULLER_STAGES: tuple[str, ...] = (
    "ingest",
    "assign-categories",
    "cluster-categories",
    "rank",
)


class AICullerRunTask(QRunnable):
    def __init__(
        self,
        *,
        folder: Path,
        records: tuple[ImageRecord, ...],
        runtime: AICullerRuntime,
        paths: AIWorkflowPaths,
        run_id: str | None = None,
        stages: tuple[str, ...] = ALL_AICULLER_STAGES,
        run_dino_prefilter: bool = False,
        dino_prefilter_settings: DINOPrefilterSettings | None = None,
        phash_prefilter_settings: PHashPrefilterSettings | None = None,
        dino_runtime: AIWorkflowRuntime | None = None,
    ) -> None:
        super().__init__()
        self.folder = folder
        self.records = records
        self.runtime = runtime
        self.paths = paths
        self.run_id = run_id or time.strftime("%Y%m%dT%H%M%S")
        self.run_dino_prefilter = bool(run_dino_prefilter)
        self.dino_prefilter_settings = (dino_prefilter_settings or default_dino_prefilter_settings()).normalized()
        self.phash_prefilter_settings = (phash_prefilter_settings or default_phash_prefilter_settings()).normalized()
        self.dino_runtime = dino_runtime
        unknown = tuple(stage for stage in stages if stage not in ALL_AICULLER_STAGES)
        if unknown:
            raise ValueError(f"Unknown AI Culler stage(s): {unknown}")
        self.stages = stages or ALL_AICULLER_STAGES
        self.signals = AICullerRunSignals()
        self.setAutoDelete(True)
        self._cancel_requested = False
        self._current_process: subprocess.Popen[str] | None = None
        # Monotonic counter of completed (ready/error) images. Used instead
        # of the per-event image index so the progress bar stays monotonic
        # when the two-stage pipeline retires images out of submission order.
        self._completed_image_count = 0

    def _emit_detail(self, message: str) -> None:
        self.signals.detail.emit(str(self.folder), message)

    def cancel(self) -> None:
        self._cancel_requested = True
        process = self._current_process
        if process is not None and process.poll() is None:
            try:
                process.terminate()
            except OSError:
                pass

    def run(self) -> None:
        folder_text = str(self.folder)
        logger = perf_logger()
        task_start = time.perf_counter() if logger.enabled else 0.0
        self.signals.started.emit(folder_text)
        if logger.enabled:
            logger.log(
                "ai.workflow.started",
                workflow="clip_topiq",
                run_id=self.run_id,
                folder=folder_text,
                records=len(self.records),
                stages=self.stages,
                dino_prepass_requested=self.run_dino_prefilter,
                dino_enabled=self.dino_prefilter_settings.enabled,
                dino_mode=self.dino_prefilter_settings.mode.value,
                phash_enabled=self.phash_prefilter_settings.enabled,
                phash_mode=self.phash_prefilter_settings.mode.value,
                phash_execution_mode=self.phash_prefilter_settings.execution_mode.value,
                clip_model_variant=self.runtime.clip_model_variant,
                clip_vision_model=self.runtime.clip_vision_model.name,
                topiq_enabled=self.runtime.topiq_model is not None,
                workers=self.runtime.workers,
            )
        try:
            self.runtime.validate()
            self.paths.hidden_root.mkdir(parents=True, exist_ok=True)
            self.paths.artifacts_dir.mkdir(parents=True, exist_ok=True)
            self.paths.report_dir.mkdir(parents=True, exist_ok=True)
            db_path = self.paths.artifacts_dir / "aiculler.sqlite"
            cache_dir = self.paths.hidden_root / "aiculler_cache"
            raw_rank_path = self.paths.report_dir / "aiculler_raw_ranking.csv"
            category_path = self.paths.report_dir / "semantic_classifications.csv"
            cluster_path = self.paths.report_dir / "semantic_clusters.csv"
            dino_stage_enabled = self.run_dino_prefilter and self.dino_prefilter_settings.enabled and "ingest" in self.stages
            phash_enabled = self.phash_prefilter_settings.enabled and "ingest" in self.stages
            phash_before_ai = phash_enabled and (
                self.phash_prefilter_settings.execution_mode == PHashExecutionMode.BEFORE_AI
                or (
                    self.phash_prefilter_settings.execution_mode == PHashExecutionMode.PARALLEL_WITH_DINO
                    and not dino_stage_enabled
                    and not self.dino_prefilter_settings.enabled
                )
            )
            phash_parallel_dino = phash_enabled and self.phash_prefilter_settings.execution_mode == PHashExecutionMode.PARALLEL_WITH_DINO and dino_stage_enabled
            phash_parallel_main = phash_enabled and self.phash_prefilter_settings.execution_mode == PHashExecutionMode.PARALLEL_WITH_MAIN
            total = len(self.stages) + 1 + (1 if dino_stage_enabled else 0) + (1 if phash_before_ai and not dino_stage_enabled else 0)
            stage_index = 1
            phash_executor: ThreadPoolExecutor | None = None
            phash_future: Future[dict[str, object]] | None = None
            if dino_stage_enabled:
                self._raise_if_cancelled()
                self.signals.stage.emit(folder_text, stage_index, total, "Running DINO Prefilter")
                dino_start = time.perf_counter() if logger.enabled else 0.0
                if phash_parallel_dino:
                    phash_executor, phash_future = self._start_phash_prefilter_async(context="parallel_with_dino")
                self._run_dino_prefilter()
                if phash_parallel_dino:
                    self._wait_for_phash_prefilter(phash_future, context="parallel_with_dino")
                    if phash_executor is not None:
                        phash_executor.shutdown(wait=False)
                        phash_executor = None
                if logger.enabled:
                    logger.duration(
                        "ai.workflow.stage",
                        (time.perf_counter() - dino_start) * 1000.0,
                        workflow="dino_prefilter",
                        parent_workflow="clip_topiq",
                        run_id=self.run_id,
                        folder=folder_text,
                        stage="dino_prefilter",
                        stage_message="Running DINO Prefilter",
                    )
                stage_index += 1
            if phash_before_ai:
                self._raise_if_cancelled()
                if not dino_stage_enabled:
                    self.signals.stage.emit(folder_text, stage_index, total, "Running pHash Prefilter")
                    stage_index += 1
                self._run_phash_prefilter()
            if phash_parallel_main:
                phash_executor, phash_future = self._start_phash_prefilter_async(context="parallel_with_main")
            include_paths_file = self._write_dino_prefilter_include_file() if "ingest" in self.stages else None

            all_commands: dict[str, tuple[str, list[str]]] = {
                "ingest": (
                    "Ingesting images",
                    self._command(
                        db_path,
                        "ingest",
                        str(self.folder),
                        "--cache",
                        str(cache_dir),
                        "--clip",
                        str(self.runtime.clip_vision_model),
                        "--workers",
                        str(max(1, self.runtime.workers)),
                        *self._include_paths_args(include_paths_file),
                        *self._topiq_args(),
                    ),
                ),
                "assign-categories": (
                    "Assigning semantic categories",
                    self._command(
                        db_path,
                        "assign-categories",
                        "--text-model",
                        str(self.runtime.clip_text_model),
                        "--tokenizer",
                        str(self.runtime.tokenizer),
                        "--out",
                        str(category_path),
                        *self._category_args(),
                    ),
                ),
                "cluster-categories": (
                    "Clustering within categories",
                    self._command(
                        db_path,
                        "cluster-categories",
                        "--cluster-run-id",
                        self.run_id,
                        "--out",
                        str(cluster_path),
                    ),
                ),
                "rank": (
                    "Ranking images",
                    self._command(
                        db_path,
                        "rank",
                        *self._technical_penalty_args(),
                        "--out",
                        str(raw_rank_path),
                    ),
                ),
            }
            commands = [(stage, *all_commands[stage]) for stage in self.stages]

            for index, (stage, message, command) in enumerate(commands, start=stage_index):
                self._raise_if_cancelled()
                self.signals.stage.emit(folder_text, index, total, message)
                stage_start = time.perf_counter() if logger.enabled else 0.0
                self._run_command(command, stage_message=message)
                if logger.enabled:
                    logger.duration(
                        "ai.workflow.stage",
                        (time.perf_counter() - stage_start) * 1000.0,
                        workflow="clip_topiq",
                        run_id=self.run_id,
                        folder=folder_text,
                        stage=stage,
                        stage_message=message,
                        stage_index=index,
                        stage_total=total,
                    )
                if stage == "ingest":
                    prune_start = time.perf_counter() if logger.enabled else 0.0
                    self._prune_aiculler_db_to_include_file(db_path, include_paths_file)
                    if logger.enabled:
                        logger.duration(
                            "ai.workflow.stage",
                            (time.perf_counter() - prune_start) * 1000.0,
                            workflow="clip_topiq",
                            run_id=self.run_id,
                            folder=folder_text,
                            stage="prune_scoped_db",
                            include_paths_file=str(include_paths_file or ""),
                        )

            if phash_parallel_main:
                self._wait_for_phash_prefilter(phash_future, context="parallel_with_main")
                if phash_executor is not None:
                    phash_executor.shutdown(wait=False)
                    phash_executor = None
            self._raise_if_cancelled()
            self.signals.stage.emit(folder_text, total, total, "Preparing GUI results")
            export_start = time.perf_counter() if logger.enabled else 0.0
            self._write_gui_exports(db_path)
            if logger.enabled:
                logger.duration(
                    "ai.workflow.stage",
                    (time.perf_counter() - export_start) * 1000.0,
                    workflow="clip_topiq",
                    run_id=self.run_id,
                    folder=folder_text,
                    stage="gui_exports",
                    report_dir=str(self.paths.report_dir),
                    ranked_export_exists=self.paths.ranked_export_path.exists(),
                    html_report_exists=self.paths.html_report_path.exists(),
                )
                logger.duration(
                    "ai.workflow.finished",
                    (time.perf_counter() - task_start) * 1000.0,
                    workflow="clip_topiq",
                    run_id=self.run_id,
                    folder=folder_text,
                    stages=self.stages,
                    report_dir=str(self.paths.report_dir),
                )
            self.signals.finished.emit(folder_text, str(self.paths.report_dir), str(self.paths.html_report_path))
        except _AICullerCancelled:
            if logger.enabled:
                logger.duration(
                    "ai.workflow.cancelled",
                    (time.perf_counter() - task_start) * 1000.0,
                    workflow="clip_topiq",
                    run_id=self.run_id,
                    folder=folder_text,
                )
            self.signals.cancelled.emit(folder_text, "AI review stopped.")
        except Exception as exc:
            if logger.enabled:
                logger.duration(
                    "ai.workflow.failed",
                    (time.perf_counter() - task_start) * 1000.0,
                    workflow="clip_topiq",
                    run_id=self.run_id,
                    folder=folder_text,
                    error=str(exc),
                )
            self.signals.failed.emit(folder_text, str(exc))
        finally:
            if "phash_executor" in locals() and phash_executor is not None:
                if phash_future is not None and not phash_future.done():
                    phash_future.cancel()
                phash_executor.shutdown(wait=False)
            self._current_process = None

    def _command(self, db_path: Path, command: str, *args: str) -> list[str]:
        return [
            str(self.runtime.python_executable),
            str(self.runtime.cli_entrypoint),
            "--db",
            str(db_path),
            "--log-dir",
            str(self.paths.hidden_root / "logs"),
            "--run-id",
            self.run_id,
            command,
            *args,
        ]

    def _run_dino_prefilter(self) -> None:
        logger = perf_logger()
        workflow_start = time.perf_counter() if logger.enabled else 0.0
        runtime = self.dino_runtime
        if runtime is None:
            raise FileNotFoundError("DINO Prefilter is enabled, but no DINO runtime is configured.")
        if logger.enabled:
            logger.log(
                "ai.workflow.started",
                workflow="dino_prefilter",
                run_id=self.run_id,
                folder=str(self.folder),
                records=len(self.records),
                mode=self.dino_prefilter_settings.mode.value,
                aggressiveness_percent=self.dino_prefilter_settings.aggressiveness_percent,
                model_policy="base_model_only",
                model_name=runtime.model_name,
                device=runtime.device,
                batch_size=runtime.batch_size,
                num_workers=runtime.num_workers,
            )
        self._validate_dino_prefilter_runtime(runtime)
        prefilter_paths = build_dino_prefilter_paths(self.paths)
        prefilter_paths.ensure()
        settings = self.dino_prefilter_settings.normalized()
        artifacts_dir = prefilter_paths.artifact_dir / "artifacts"
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        python_executable = runtime.python_executable or Path(sys.executable)
        extraction_include_file = self._write_dino_extraction_include_file(prefilter_paths)
        extraction_include_count = len(_read_include_paths_file(extraction_include_file)) if extraction_include_file else 0
        extraction_cache_key = self._dino_extraction_cache_key(runtime, extraction_include_file)
        extraction_cache_marker = artifacts_dir / "image_triage_extraction_cache.json"
        signal_args = ["--skip-specialists"]
        if not settings.technical_trash_enabled:
            signal_args.append("--skip-technical")
        commands = (
            (
                "Extracting DINO embeddings",
                [
                    str(python_executable),
                    str(runtime.engine_root / "scripts" / "extract_embeddings.py"),
                    "--config",
                    str(runtime.extraction_config_path),
                    "--input-dir",
                    str(self.folder),
                    "--output-dir",
                    str(artifacts_dir),
                    "--batch-size",
                    str(max(1, int(runtime.batch_size))),
                    "--model-name",
                    runtime.model_name,
                    "--device",
                    runtime.device,
                    "--num-workers",
                    str(max(0, int(runtime.num_workers))),
                    *self._include_paths_args(extraction_include_file),
                ],
            ),
            (
                "Clustering DINO embeddings",
                [
                    str(python_executable),
                    str(runtime.engine_root / "scripts" / "cluster_embeddings.py"),
                    "--config",
                    str(runtime.clustering_config_path),
                    "--artifacts-dir",
                    str(artifacts_dir),
                    "--output-dir",
                    str(artifacts_dir),
                ],
            ),
            (
                "Building DINO prefilter signals",
                [
                    str(python_executable),
                    str(runtime.engine_root / "scripts" / "build_culling_signals.py"),
                    "--artifacts-dir",
                    str(artifacts_dir),
                    "--output-dir",
                    str(prefilter_paths.artifact_dir),
                    *signal_args,
                ],
            ),
        )
        for message, command in commands:
            self._raise_if_cancelled()
            stage_key = _stage_key_from_message(message)
            cache_state: dict[str, object] | None = None
            if stage_key == "extracting_dino_embeddings":
                cache_state = self._dino_extraction_cache_state(
                    artifacts_dir=artifacts_dir,
                    marker_path=extraction_cache_marker,
                    cache_key=extraction_cache_key,
                )
                if logger.enabled:
                    logger.log(
                        "ai.workflow.cache_check",
                        workflow="dino_prefilter",
                        run_id=self.run_id,
                        folder=str(self.folder),
                        stage=stage_key,
                        include_paths=extraction_include_count,
                        **cache_state,
                    )
            if (
                stage_key == "extracting_dino_embeddings"
                and cache_state is not None
                and bool(cache_state.get("cache_hit"))
            ):
                self._emit_detail("Reusing cached DINO embeddings for this image set.")
                if logger.enabled:
                    logger.log(
                        "ai.workflow.stage_skipped",
                        workflow="dino_prefilter",
                        run_id=self.run_id,
                        folder=str(self.folder),
                        stage=stage_key,
                        reason="cache_hit",
                        cache_key=extraction_cache_key,
                        marker_path=str(extraction_cache_marker),
                        include_paths=extraction_include_count,
                    )
                continue
            self._emit_detail(message)
            stage_start = time.perf_counter() if logger.enabled else 0.0
            self._run_dino_command(command, stage_message=message, runtime=runtime)
            if stage_key == "extracting_dino_embeddings":
                self._write_dino_extraction_cache_marker(
                    marker_path=extraction_cache_marker,
                    cache_key=extraction_cache_key,
                )
            if logger.enabled:
                logger.duration(
                    "ai.workflow.stage",
                    (time.perf_counter() - stage_start) * 1000.0,
                    workflow="dino_prefilter",
                    run_id=self.run_id,
                    folder=str(self.folder),
                    stage=stage_key,
                    stage_message=message,
                    artifact_dir=str(prefilter_paths.artifact_dir),
                )
        signals_csv_path = prefilter_paths.artifact_dir / "culling_signals.csv"
        if not signals_csv_path.exists():
            raise FileNotFoundError(f"DINO Prefilter signals were not written: {signals_csv_path}")
        load_start = time.perf_counter() if logger.enabled else 0.0
        with signals_csv_path.open("r", encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        if logger.enabled:
            logger.duration(
                "ai.workflow.stage",
                (time.perf_counter() - load_start) * 1000.0,
                workflow="dino_prefilter",
                run_id=self.run_id,
                folder=str(self.folder),
                stage="load_signal_rows",
                signal_rows=len(rows),
                rows_path=str(signals_csv_path),
            )
        audit_start = time.perf_counter() if logger.enabled else 0.0
        decisions = run_dino_prefilter_from_signal_rows(
            rows,
            settings=self.dino_prefilter_settings,
            paths=prefilter_paths,
        )
        if logger.enabled:
            logger.duration(
                "ai.workflow.stage",
                (time.perf_counter() - audit_start) * 1000.0,
                workflow="dino_prefilter",
                run_id=self.run_id,
                folder=str(self.folder),
                stage="decision_audit",
                signal_rows=len(rows),
                decisions=len(decisions),
                rows_path=str(prefilter_paths.rows_path),
                report_path=str(prefilter_paths.report_path),
            )
        candidates = sum(1 for decision in decisions.values() if decision.is_candidate)
        rescued = sum(1 for decision in decisions.values() if decision.is_rescued)
        self._emit_detail(
            f"DINO Prefilter marked {candidates} candidate(s), rescued {rescued}, scanned {len(decisions)} image(s)."
        )
        if logger.enabled:
            logger.duration(
                "ai.workflow.finished",
                (time.perf_counter() - workflow_start) * 1000.0,
                workflow="dino_prefilter",
                run_id=self.run_id,
                folder=str(self.folder),
                scanned=len(decisions),
                candidates=candidates,
                rescued=rescued,
                artifact_dir=str(prefilter_paths.artifact_dir),
            )

    def _run_phash_prefilter(self) -> dict[str, object]:
        logger = perf_logger()
        workflow_start = time.perf_counter() if logger.enabled else 0.0
        settings = self.phash_prefilter_settings.normalized()
        if not settings.enabled:
            if logger.enabled:
                logger.log(
                    "ai.workflow.stage_skipped",
                    workflow="phash_prefilter",
                    run_id=self.run_id,
                    folder=str(self.folder),
                    stage="phash_prefilter",
                    reason="disabled",
                )
            return {"rows": 0, "decisions": 0}
        if logger.enabled:
            logger.log(
                "ai.workflow.started",
                workflow="phash_prefilter",
                run_id=self.run_id,
                folder=str(self.folder),
                records=len(self.records),
                mode=settings.mode.value,
                execution_mode=settings.execution_mode.value,
                hamming_threshold=settings.hamming_threshold,
                cache_enabled=settings.cache_enabled,
            )
        rows = self._phash_prefilter_signal_rows()
        paths = build_phash_prefilter_paths(self.paths)
        decisions = run_phash_prefilter_from_signal_rows(rows, settings=settings, paths=paths)
        candidates = sum(1 for decision in decisions.values() if decision.is_candidate)
        self._emit_detail(
            f"pHash Prefilter marked {candidates} duplicate candidate(s), scanned {len(decisions)} image(s)."
        )
        if logger.enabled:
            logger.duration(
                "ai.workflow.finished",
                (time.perf_counter() - workflow_start) * 1000.0,
                workflow="phash_prefilter",
                run_id=self.run_id,
                folder=str(self.folder),
                scanned=len(decisions),
                candidates=candidates,
                artifact_dir=str(paths.artifact_dir),
            )
        return {"rows": len(rows), "decisions": len(decisions), "candidates": candidates}

    def _start_phash_prefilter_async(self, *, context: str) -> tuple[ThreadPoolExecutor, Future[dict[str, object]]]:
        logger = perf_logger()
        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="image-triage-phash")
        future = executor.submit(self._run_phash_prefilter)
        self._emit_detail(f"Started pHash Prefilter asynchronously ({context}).")
        if logger.enabled:
            logger.log(
                "ai.workflow.async_started",
                workflow="phash_prefilter",
                run_id=self.run_id,
                folder=str(self.folder),
                context=context,
            )
        return executor, future

    def _wait_for_phash_prefilter(self, future: Future[dict[str, object]] | None, *, context: str) -> dict[str, object]:
        if future is None:
            return {}
        logger = perf_logger()
        wait_start = time.perf_counter() if logger.enabled else 0.0
        self._emit_detail("Waiting for pHash Prefilter to finish.")
        try:
            result = future.result()
        except Exception as exc:
            if logger.enabled:
                logger.duration(
                    "ai.workflow.async_failed",
                    (time.perf_counter() - wait_start) * 1000.0,
                    workflow="phash_prefilter",
                    run_id=self.run_id,
                    folder=str(self.folder),
                    context=context,
                    error=str(exc),
                )
            raise
        if logger.enabled:
            logger.duration(
                "ai.workflow.async_finished",
                (time.perf_counter() - wait_start) * 1000.0,
                workflow="phash_prefilter",
                run_id=self.run_id,
                folder=str(self.folder),
                context=context,
                **result,
            )
        return result

    def _phash_prefilter_signal_rows(self) -> list[dict[str, object]]:
        logger = perf_logger()
        stage_start = time.perf_counter() if logger.enabled else 0.0
        settings = self.phash_prefilter_settings.normalized()
        if not settings.enabled:
            if logger.enabled:
                logger.log(
                    "ai.workflow.stage_skipped",
                    workflow="phash_prefilter",
                    run_id=self.run_id,
                    folder=str(self.folder),
                    stage="phash_duplicates",
                    reason="disabled",
                )
            return []
        representatives = self._collect_jpeg_representatives()
        if len(representatives) < 2:
            if logger.enabled:
                logger.log(
                    "ai.workflow.stage_skipped",
                    workflow="phash_prefilter",
                    run_id=self.run_id,
                    folder=str(self.folder),
                    stage="phash_duplicates",
                    reason="not_enough_images",
                    representatives=len(representatives),
                )
            return []
        self._emit_detail(f"Running pHash duplicate check on {len(representatives)} image(s).")
        phash_paths = build_phash_prefilter_paths(self.paths)
        cache_path = phash_paths.cache_path if settings.cache_enabled else None
        result = find_perceptual_duplicate_groups_with_stats(
            representatives,
            hamming_threshold=settings.hamming_threshold,
            cache_path=cache_path,
        )
        groups = result.groups
        rows: list[dict[str, object]] = []
        for group_index, group in enumerate(groups, start=1):
            group_size = len(group.members)
            for rank, path in enumerate(group.members, start=1):
                rows.append(
                    {
                        "file_path": path,
                        "group_size": str(group_size),
                        "dino_rank": "1",
                        "phash_group": f"phash_{group_index:04d}",
                        "phash_rank": str(rank),
                        "phash_hamming_threshold": str(settings.hamming_threshold),
                        "phash_max_distance": str(group.max_distance),
                        "phash_duplicate_score": "0.0" if rank == 1 else "1.0",
                        "best_representative": "1" if rank == 1 else "0",
                    }
                )
        duplicate_count = sum(max(0, len(group.members) - 1) for group in groups)
        self._emit_detail(f"pHash duplicate check found {duplicate_count} duplicate candidate(s) in {len(groups)} group(s).")
        if logger.enabled:
            logger.duration(
                "ai.workflow.stage",
                (time.perf_counter() - stage_start) * 1000.0,
                workflow="phash_prefilter",
                run_id=self.run_id,
                folder=str(self.folder),
                stage="phash_duplicates",
                representatives=len(representatives),
                groups=len(groups),
                duplicate_candidates=duplicate_count,
                hamming_threshold=settings.hamming_threshold,
                cache_hits=result.stats.cached,
                cache_misses=result.stats.computed,
                cache_failures=result.stats.failed,
                cache_path=result.stats.cache_path,
                hash_count=result.stats.hash_count,
                worker_count=result.stats.worker_count,
                comparison_count=result.stats.comparison_count,
                cache_read_ms=round(result.stats.cache_read_ms, 3),
                signature_lookup_ms=round(result.stats.signature_lookup_ms, 3),
                compute_ms=round(result.stats.compute_ms, 3),
                cache_write_ms=round(result.stats.cache_write_ms, 3),
                pairwise_ms=round(result.stats.pairwise_ms, 3),
                grouping_ms=round(result.stats.grouping_ms, 3),
            )
        return rows

    def _collect_jpeg_representatives(
        self,
        *,
        excluded_keys: set[str] | None = None,
    ) -> list[str]:
        """Return one representative (JPEG-preferred) path per grid record.

        Folder rows are skipped, duplicates are removed, and records whose stack
        intersects ``excluded_keys`` (normalized paths) are dropped. This collapses
        RAW+JPEG stacks to a single decodable image so the engine does not count
        each RAW and its sibling JPEG separately.
        """
        excluded = excluded_keys or set()
        representatives: list[str] = []
        seen: set[str] = set()
        for record in self.records:
            if getattr(record, "is_folder", False):
                continue
            if excluded and any(_norm_path(path) in excluded for path in record.stack_paths):
                continue
            representative = self._jpeg_representative_for_record(record)
            if not representative:
                continue
            key = _norm_path(representative)
            if key in seen:
                continue
            seen.add(key)
            representatives.append(representative)
        return representatives

    def _write_dino_extraction_include_file(self, prefilter_paths: DINOPrefilterPaths) -> Path | None:
        """Scope DINO extraction to one representative (JPEG-preferred) per grid record.

        Without this the engine walks the folder recursively, which pulls in the hidden
        .image_triage_ai cache and counts each RAW and its sibling JPEG separately.
        """
        representatives = self._collect_jpeg_representatives()
        if not representatives:
            return None
        prefilter_paths.ensure()
        include_path = prefilter_paths.artifact_dir / "dino_extraction_paths.txt"
        include_path.write_text("\n".join(representatives) + "\n", encoding="utf-8")
        self._emit_detail(f"Scoped DINO extraction to {len(representatives)} image(s) from the culling pool.")
        return include_path

    @staticmethod
    def _jpeg_representative_for_record(record: ImageRecord) -> str:
        for path in record.stack_paths:
            if suffix_for_path(path) in JPEG_SUFFIXES:
                return path
        return record.path

    def _validate_dino_prefilter_runtime(self, runtime: AIWorkflowRuntime) -> None:
        missing: list[str] = []
        python_executable = runtime.python_executable or Path(sys.executable)
        for label, path in (
            ("DINO Python", python_executable),
            ("DINO engine root", runtime.engine_root),
            ("DINO extract config", runtime.extraction_config_path),
            ("DINO cluster config", runtime.clustering_config_path),
            ("DINO extract script", runtime.engine_root / "scripts" / "extract_embeddings.py"),
            ("DINO cluster script", runtime.engine_root / "scripts" / "cluster_embeddings.py"),
            ("DINO signal script", runtime.engine_root / "scripts" / "build_culling_signals.py"),
        ):
            if not Path(path).exists():
                missing.append(f"{label}: {path}")
        if runtime.model_installation is not None and not runtime.model_installation.is_installed:
            missing.extend(f"DINO model: {path}" for path in runtime.model_installation.missing_files)
        elif runtime.model_name:
            model_path = Path(runtime.model_name).expanduser()
            if model_path.is_absolute() or "/" in runtime.model_name or "\\" in runtime.model_name or runtime.model_name.startswith("."):
                if not model_path.exists():
                    missing.append(f"DINO model: {model_path}")
                elif model_path.is_dir():
                    for filename in ("config.json", "model.safetensors"):
                        candidate = model_path / filename
                        if not candidate.exists():
                            missing.append(f"DINO model: {candidate}")
        if missing:
            raise FileNotFoundError("Missing DINO Prefilter runtime paths:\n" + "\n".join(missing))

    def _dino_extraction_cache_key(self, runtime: AIWorkflowRuntime, include_paths_file: Path | None) -> str:
        payload = {
            "schema_version": 1,
            "model_policy": "base_model_only",
            "model_name": runtime.model_name,
            "input_dir": str(self.folder.resolve()),
            "extraction_config": _file_cache_identity(runtime.extraction_config_path),
            "include_paths": [
                _file_cache_identity(path)
                for path in _read_include_paths_file(include_paths_file)
            ],
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _dino_extraction_cache_valid(*, artifacts_dir: Path, marker_path: Path, cache_key: str) -> bool:
        return bool(
            AICullerRunTask._dino_extraction_cache_state(
                artifacts_dir=artifacts_dir,
                marker_path=marker_path,
                cache_key=cache_key,
            )["cache_hit"]
        )

    @staticmethod
    def _dino_extraction_cache_state(*, artifacts_dir: Path, marker_path: Path, cache_key: str) -> dict[str, object]:
        output_files = ("images.csv", "embeddings.npy", "image_ids.json")
        output_exists = {
            filename: (artifacts_dir / filename).exists()
            for filename in output_files
        }
        state: dict[str, object] = {
            "cache_key": cache_key,
            "cache_hit": False,
            "marker_path": str(marker_path),
            "marker_exists": marker_path.exists(),
            "outputs_present": all(output_exists.values()),
            "missing_outputs": [filename for filename, exists in output_exists.items() if not exists],
            "cache_key_matches": False,
        }
        if not cache_key or not marker_path.exists():
            return state
        try:
            payload = json.loads(marker_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            state["marker_readable"] = False
            return state
        state["marker_readable"] = True
        state["cache_key_matches"] = isinstance(payload, dict) and payload.get("cache_key") == cache_key
        state["cache_hit"] = bool(state["outputs_present"] and state["cache_key_matches"])
        return state

    @staticmethod
    def _write_dino_extraction_cache_marker(*, marker_path: Path, cache_key: str) -> None:
        marker_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "cache_key": cache_key,
                    "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                },
                sort_keys=True,
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )

    def _run_dino_command(self, command: list[str], *, stage_message: str, runtime: AIWorkflowRuntime) -> None:
        logger = perf_logger()
        command_start = time.perf_counter() if logger.enabled else 0.0
        env = dict(os.environ)
        env["PYTHONUNBUFFERED"] = "1"
        existing_pythonpath = env.get("PYTHONPATH", "")
        path_entries = [str(site_dir) for site_dir in resolve_ai_runtime_site_packages(device=runtime.device)]
        path_entries.append(str(runtime.engine_root))
        if existing_pythonpath:
            path_entries.extend(part for part in existing_pythonpath.split(os.pathsep) if part)
        env["PYTHONPATH"] = os.pathsep.join(path_entries)
        process = subprocess.Popen(
            command,
            cwd=str(runtime.engine_root),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=env,
        )
        if logger.enabled:
            logger.log(
                "ai.workflow.subprocess_started",
                workflow="dino_prefilter",
                run_id=self.run_id,
                folder=str(self.folder),
                stage=_stage_key_from_message(stage_message),
                stage_message=stage_message,
                pid=process.pid,
                executable=Path(command[0]).name if command else "",
                cwd=str(runtime.engine_root),
            )
        self._current_process = process
        output_lines: list[str] = []
        assert process.stdout is not None
        self.signals.progress.emit(str(self.folder), stage_message, 0, 0, "")
        for raw_line in iter(process.stdout.readline, ""):
            if self._cancel_requested:
                self.cancel()
                raise _AICullerCancelled()
            line = raw_line.strip()
            if not line:
                continue
            output_lines.append(line)
            self._emit_detail(line)
            parsed = _parse_tqdm_progress(line)
            if parsed is not None:
                message, current, total, eta_text = parsed
                self.signals.progress.emit(str(self.folder), message, current, total, eta_text)
        return_code = process.wait()
        self._current_process = None
        if self._cancel_requested:
            raise _AICullerCancelled()
        if return_code != 0:
            tail = "\n".join(output_lines[-30:])
            if logger.enabled:
                logger.duration(
                    "ai.workflow.subprocess_failed",
                    (time.perf_counter() - command_start) * 1000.0,
                    workflow="dino_prefilter",
                    run_id=self.run_id,
                    folder=str(self.folder),
                    stage=_stage_key_from_message(stage_message),
                    stage_message=stage_message,
                    return_code=return_code,
                    output_lines=len(output_lines),
                )
            raise RuntimeError(f"{stage_message} failed." + (f"\n\n{tail}" if tail else ""))
        if logger.enabled:
            logger.duration(
                "ai.workflow.subprocess_finished",
                (time.perf_counter() - command_start) * 1000.0,
                workflow="dino_prefilter",
                run_id=self.run_id,
                folder=str(self.folder),
                stage=_stage_key_from_message(stage_message),
                stage_message=stage_message,
                return_code=return_code,
                output_lines=len(output_lines),
            )

    def _write_dino_prefilter_include_file(self) -> Path | None:
        """Scope the AI Culler ingest to one JPEG-per-stack representative.

        Always written (when records are known) so CLIP/TOPIQ do not double-count
        RAW+JPEG stacks or walk the hidden cache. When DINO pool removal is active,
        records flagged for removal are additionally dropped from the pool.
        """
        prefilter_paths = build_dino_prefilter_paths(self.paths)
        settings = self.dino_prefilter_settings
        excluded_keys: set[str] = set()
        pool_removal_active = settings.enabled and settings.mode == DINOPrefilterMode.POOL_REMOVAL
        if pool_removal_active:
            decisions = load_dino_prefilter_decisions(prefilter_paths)
            excluded_keys = {
                _norm_path(path)
                for path, decision in decisions.items()
                if decision.action == "remove_from_pool"
            }
            if not excluded_keys:
                self._emit_detail("Pool removal enabled, but no DINO removal rows were found.")
        elif settings.enabled:
            self._emit_detail("Soft quarantine enabled; all images remain in the AI pool.")

        phash_settings = self.phash_prefilter_settings
        phash_pool_removal_active = (
            phash_settings.enabled
            and phash_settings.mode == DINOPrefilterMode.POOL_REMOVAL
            and phash_settings.execution_mode != PHashExecutionMode.PARALLEL_WITH_MAIN
        )
        if phash_pool_removal_active:
            phash_decisions = load_phash_prefilter_decisions(build_phash_prefilter_paths(self.paths))
            phash_excluded = {
                _norm_path(path)
                for path, decision in phash_decisions.items()
                if decision.action == "remove_from_pool"
            }
            excluded_keys.update(phash_excluded)
            if not phash_excluded:
                self._emit_detail("Pool removal enabled, but no pHash removal rows were found.")
        elif phash_settings.enabled and phash_settings.mode == DINOPrefilterMode.POOL_REMOVAL:
            self._emit_detail("Async-with-main pHash runs after ingest starts, so pool removal can only apply on a later run.")

        total_records = sum(1 for record in self.records if not getattr(record, "is_folder", False))
        included = self._collect_jpeg_representatives(excluded_keys=excluded_keys)
        if not included:
            return None
        prefilter_paths.ensure()
        include_path = prefilter_paths.artifact_dir / "aiculler_include_paths.txt"
        include_path.write_text("\n".join(included) + "\n", encoding="utf-8")
        excluded_count = total_records - len(included)
        if (pool_removal_active or phash_pool_removal_active) and excluded_count > 0:
            self._emit_detail(f"Pool removal excluded {excluded_count} image(s); {len(included)} remain for AI Culler.")
        else:
            self._emit_detail(f"Scoped AI Culler ingest to {len(included)} image(s) from the culling pool.")
        return include_path

    @staticmethod
    def _include_paths_args(include_paths_file: Path | None) -> tuple[str, ...]:
        if include_paths_file is None:
            return ()
        return ("--include-paths-file", str(include_paths_file))

    def _topiq_args(self) -> tuple[str, ...]:
        if self.runtime.topiq_model is None:
            return ()
        return ("--topiq", str(self.runtime.topiq_model))

    def _category_args(self) -> tuple[str, ...]:
        if self.runtime.categories_csv is None:
            return ()
        return ("--categories", str(self.runtime.categories_csv))

    def _technical_penalty_args(self) -> tuple[str, ...]:
        if self.runtime.tag_penalties_csv is None or not self.runtime.avoid_tags:
            return ()
        args: list[str] = [
            "--tag-config",
            str(self.runtime.tag_penalties_csv),
            "--penalty-weight",
            f"{self.runtime.penalty_weight:g}",
        ]
        for tag in self.runtime.avoid_tags:
            args.extend(("--avoid", tag))
        return tuple(args)

    def _run_command(self, command: list[str], *, stage_message: str) -> None:
        logger = perf_logger()
        command_start = time.perf_counter() if logger.enabled else 0.0
        env = dict(os.environ)
        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONPATH"] = _aiculler_pythonpath(self.runtime.root, env.get("PYTHONPATH", ""))
        process = subprocess.Popen(
            command,
            cwd=str(self.runtime.root),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=env,
        )
        if logger.enabled:
            logger.log(
                "ai.workflow.subprocess_started",
                workflow="clip_topiq",
                run_id=self.run_id,
                folder=str(self.folder),
                stage=_stage_key_from_message(stage_message),
                stage_message=stage_message,
                pid=process.pid,
                executable=Path(command[0]).name if command else "",
                cli_command=_cli_command_from_args(command),
                cli_log_dir=_cli_arg_value(command, "--log-dir"),
                cli_run_id=_cli_arg_value(command, "--run-id"),
                cwd=str(self.runtime.root),
            )
        self._current_process = process
        output_lines: list[str] = []
        assert process.stdout is not None
        for raw_line in iter(process.stdout.readline, ""):
            if self._cancel_requested:
                self.cancel()
                raise _AICullerCancelled()
            line = raw_line.strip()
            if not line:
                continue
            output_lines.append(line)
            self._emit_progress_for_line(stage_message, line)
        return_code = process.wait()
        self._current_process = None
        if self._cancel_requested:
            raise _AICullerCancelled()
        if return_code != 0:
            tail = "\n".join(output_lines[-30:])
            if logger.enabled:
                logger.duration(
                    "ai.workflow.subprocess_failed",
                    (time.perf_counter() - command_start) * 1000.0,
                    workflow="clip_topiq",
                    run_id=self.run_id,
                    folder=str(self.folder),
                    stage=_stage_key_from_message(stage_message),
                    stage_message=stage_message,
                    return_code=return_code,
                    output_lines=len(output_lines),
                    cli_command=_cli_command_from_args(command),
                    cli_log_dir=_cli_arg_value(command, "--log-dir"),
                    cli_run_id=_cli_arg_value(command, "--run-id"),
                )
            raise RuntimeError(f"{stage_message} failed." + (f"\n\n{tail}" if tail else ""))
        if logger.enabled:
            logger.duration(
                "ai.workflow.subprocess_finished",
                (time.perf_counter() - command_start) * 1000.0,
                workflow="clip_topiq",
                run_id=self.run_id,
                folder=str(self.folder),
                stage=_stage_key_from_message(stage_message),
                stage_message=stage_message,
                return_code=return_code,
                output_lines=len(output_lines),
                cli_command=_cli_command_from_args(command),
                cli_log_dir=_cli_arg_value(command, "--log-dir"),
                cli_run_id=_cli_arg_value(command, "--run-id"),
            )

    def _prune_aiculler_db_to_include_file(self, db_path: Path, include_paths_file: Path | None) -> None:
        logger = perf_logger()
        prune_start = time.perf_counter() if logger.enabled else 0.0
        if include_paths_file is None or not include_paths_file.exists() or not db_path.exists():
            return
        include_keys: set[str] = set()
        for line in include_paths_file.read_text(encoding="utf-8").splitlines():
            text = line.strip()
            if not text or text.startswith("#"):
                continue
            candidate = Path(text)
            if not candidate.is_absolute():
                candidate = include_paths_file.parent / candidate
            include_keys.add(_norm_path(candidate))
        if not include_keys:
            return
        connection = sqlite3.connect(db_path)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys=ON")
            rows = connection.execute("SELECT id, source_path FROM images").fetchall()
            stale_ids = [
                int(row["id"])
                for row in rows
                if _norm_path(str(row["source_path"])) not in include_keys
            ]
            if not stale_ids:
                self._emit_detail(f"AI Culler database already scoped to {len(include_keys)} image(s).")
                if logger.enabled:
                    logger.duration(
                        "ai.workflow.db_prune",
                        (time.perf_counter() - prune_start) * 1000.0,
                        workflow="clip_topiq",
                        run_id=self.run_id,
                        folder=str(self.folder),
                        include_paths=len(include_keys),
                        stale_rows=0,
                    )
                return
            for table in (
                "embeddings",
                "feedback",
                "image_categories",
                "image_cluster_memberships",
                "ratings",
                "adapter_scores",
            ):
                _delete_rows_by_ids(connection, table, "image_id", stale_ids)
            _delete_rows_by_ids(connection, "images", "id", stale_ids)
            connection.commit()
            remaining = connection.execute("SELECT COUNT(*) FROM images").fetchone()
            remaining_count = int(remaining[0]) if remaining else 0
        finally:
            connection.close()
        self._emit_detail(f"Pruned AI Culler database to {remaining_count} scoped image row(s); removed {len(stale_ids)} stale row(s).")
        if logger.enabled:
            logger.duration(
                "ai.workflow.db_prune",
                (time.perf_counter() - prune_start) * 1000.0,
                workflow="clip_topiq",
                run_id=self.run_id,
                folder=str(self.folder),
                include_paths=len(include_keys),
                stale_rows=len(stale_ids),
                remaining_rows=remaining_count,
            )

    def _emit_progress_for_line(self, stage_message: str, line: str) -> None:
        self._emit_detail(line)
        match = INGEST_EVENT_PATTERN.match(line)
        if match is not None and self.records:
            status = (match.group("status") or "").strip().lower()
            # CLI-Culler's two-stage pipeline emits both "previewed" and
            # "ready" events per image, and with workers > 1 they interleave
            # AND retire out of submission order. Driving the bar off the
            # raw image index makes it jitter both ways. Instead: count
            # completed images monotonically (each "ready"/"error" bumps
            # the counter once), ignore "previewed" for progress purposes
            # (it still flows through the detail signal for the log panel).
            if status in {"ready", "error"}:
                self._completed_image_count = min(
                    self._completed_image_count + 1, len(self.records)
                )
                self.signals.progress.emit(
                    str(self.folder),
                    stage_message,
                    self._completed_image_count,
                    len(self.records),
                    "",
            )
            return
        progress_match = CLI_PROGRESS_PATTERN.match(line)
        if progress_match is not None:
            try:
                current = int(progress_match.group("current"))
                total = int(progress_match.group("total"))
            except (TypeError, ValueError):
                current = 0
                total = 0
            label = (progress_match.group("label") or "").replace("-", " ").strip()
            detail = (progress_match.group("message") or "").strip()
            progress_message = f"{stage_message}: {label}" if label else stage_message
            self.signals.progress.emit(str(self.folder), progress_message, current, total, detail)
            return
        self.signals.progress.emit(str(self.folder), stage_message, 0, 0, "")

    def _write_gui_exports(self, db_path: Path) -> None:
        write_gui_exports(db_path, self.paths, run_id=self.run_id)
        write_run_config(
            self.paths,
            runtime=self.runtime,
            mode="run",
            run_id=self.run_id,
            stages=self.stages,
        )

    def _raise_if_cancelled(self) -> None:
        if self._cancel_requested:
            raise _AICullerCancelled()


class _AICullerCancelled(RuntimeError):
    pass


class DINOPrefilterRunTask(AICullerRunTask):
    def __init__(
        self,
        *,
        folder: Path,
        paths: AIWorkflowPaths,
        dino_prefilter_settings: DINOPrefilterSettings,
        dino_runtime: AIWorkflowRuntime,
        phash_prefilter_settings: PHashPrefilterSettings | None = None,
        records: tuple[ImageRecord, ...] = (),
        run_id: str | None = None,
    ) -> None:
        QRunnable.__init__(self)
        self.folder = folder
        self.records = records
        self.paths = paths
        self.run_id = run_id or time.strftime("%Y%m%dT%H%M%S")
        self.run_dino_prefilter = True
        self.dino_prefilter_settings = dino_prefilter_settings.normalized()
        self.phash_prefilter_settings = (phash_prefilter_settings or default_phash_prefilter_settings()).normalized()
        self.dino_runtime = dino_runtime
        self.stages = ()
        self.signals = AICullerRunSignals()
        self.setAutoDelete(True)
        self._cancel_requested = False
        self._current_process: subprocess.Popen[str] | None = None
        self._completed_image_count = 0

    def run(self) -> None:
        folder_text = str(self.folder)
        self.signals.started.emit(folder_text)
        try:
            if not self.dino_prefilter_settings.enabled:
                raise RuntimeError("DINO Prefilter is disabled.")
            self.paths.hidden_root.mkdir(parents=True, exist_ok=True)
            self.paths.artifacts_dir.mkdir(parents=True, exist_ok=True)
            self.paths.report_dir.mkdir(parents=True, exist_ok=True)
            self._raise_if_cancelled()
            self.signals.stage.emit(folder_text, 1, 1, "Running DINO Prefilter")
            phash_executor: ThreadPoolExecutor | None = None
            phash_future: Future[dict[str, object]] | None = None
            if (
                self.phash_prefilter_settings.enabled
                and self.phash_prefilter_settings.execution_mode == PHashExecutionMode.PARALLEL_WITH_DINO
            ):
                phash_executor, phash_future = self._start_phash_prefilter_async(context="standalone_dino")
            self._run_dino_prefilter()
            if phash_future is not None:
                self._wait_for_phash_prefilter(phash_future, context="standalone_dino")
            if phash_executor is not None:
                phash_executor.shutdown(wait=False)
            prefilter_paths = build_dino_prefilter_paths(self.paths)
            self.signals.finished.emit(
                folder_text,
                str(prefilter_paths.artifact_dir),
                str(prefilter_paths.report_path),
            )
        except _AICullerCancelled:
            self.signals.cancelled.emit(folder_text, "DINO Prefilter stopped.")
        except Exception as exc:
            self.signals.failed.emit(folder_text, str(exc))
        finally:
            if "phash_executor" in locals() and phash_executor is not None:
                if phash_future is not None and not phash_future.done():
                    phash_future.cancel()
                phash_executor.shutdown(wait=False)
            self._current_process = None


class AICullerAdapterTask(QRunnable):
    def __init__(
        self,
        *,
        runtime: AICullerRuntime,
        paths: AIWorkflowPaths,
        mode: str,
        ratings_csv: Path | None = None,
        ratings_csv_text: str = "",
        model_version: str = "",
        source_model_db: Path | None = None,
        apply_before_rank: bool = False,
        run_id: str | None = None,
    ) -> None:
        super().__init__()
        self.runtime = runtime
        self.paths = paths
        self.mode = mode
        self.ratings_csv = ratings_csv
        self.ratings_csv_text = ratings_csv_text
        self.model_version = model_version.strip() or time.strftime("%Y%m%dT%H%M%S")
        self.source_model_db = source_model_db
        self.apply_before_rank = bool(apply_before_rank)
        self.run_id = run_id or self.model_version
        self.signals = AICullerCommandSignals()
        self.setAutoDelete(True)

    def run(self) -> None:
        materialized_ratings: Path | None = None
        try:
            self.runtime.validate()
            db_path = aiculler_db_path(self.paths)
            if not db_path.exists():
                raise FileNotFoundError("Run Index & Score in the AI Workflow Center before training or ranking with an adapter.")
            self.paths.report_dir.mkdir(parents=True, exist_ok=True)
            if self.mode == "train" and self.ratings_csv_text:
                self.paths.artifacts_dir.mkdir(parents=True, exist_ok=True)
                materialized_ratings = self.paths.artifacts_dir / f".adapter_ratings_{self.model_version}.csv"
                materialized_ratings.write_text(self.ratings_csv_text, encoding="utf-8")
                self.ratings_csv = materialized_ratings
            if self.source_model_db is not None:
                copied = copy_adapter_model(self.source_model_db, db_path, self.model_version)
                if not copied:
                    raise FileNotFoundError(f"Could not copy adapter model from {self.source_model_db}")
            commands = self._commands(db_path)
            self.signals.started.emit(len(commands))
            for index, (message, command) in enumerate(commands, start=1):
                self.signals.stage.emit(index, len(commands), message)
                self._run_command(command, message)
            if self.mode in {"train", "rank"}:
                write_gui_exports(db_path, self.paths, model_version=self.model_version)
            write_run_config(
                self.paths,
                runtime=self.runtime,
                mode=self.mode,
                run_id=self.run_id,
                model_version=self.model_version,
            )
            self.signals.finished.emit(
                {
                    "mode": self.mode,
                    "model_version": self.model_version,
                    "report_dir": str(self.paths.report_dir),
                    "export_csv_path": str(self.paths.ranked_export_path),
                    "evaluation_csv_path": str(self.paths.report_dir / f"adapter_evaluation_{self.model_version}.csv"),
                }
            )
        except Exception as exc:
            self.signals.failed.emit(str(exc))
        finally:
            if materialized_ratings is not None:
                try:
                    materialized_ratings.unlink()
                except OSError:
                    pass

    def _commands(self, db_path: Path) -> list[tuple[str, list[str]]]:
        if self.mode == "train":
            if self.ratings_csv is None or not self.ratings_csv.exists():
                raise FileNotFoundError("No ratings CSV is available for adapter training.")
            return [
                (
                    "Importing adapter labels",
                    self._command(
                        db_path,
                        "import-ratings",
                        "--ratings",
                        str(self.ratings_csv),
                        "--source",
                        "image_triage",
                        "--skip-missing",
                    ),
                ),
                (
                    "Training local preference adapter",
                    self._command(
                        db_path,
                        "train-adapter",
                        "--model-version",
                        self.model_version,
                        "--base-weight",
                        "0",
                        "--adapter-weight",
                        "1",
                        "--out",
                        str(self.paths.report_dir / f"adapter_scores_{self.model_version}.csv"),
                    ),
                ),
                (
                    "Evaluating local preference adapter",
                    self._command(
                        db_path,
                        "evaluate-adapter",
                        "--model-version",
                        self.model_version,
                        "--out",
                        str(self.paths.report_dir / f"adapter_evaluation_{self.model_version}.csv"),
                    ),
                ),
            ]
        if self.mode == "evaluate":
            return [
                (
                    "Evaluating local preference adapter",
                    self._command(
                        db_path,
                        "evaluate-adapter",
                        "--model-version",
                        self.model_version,
                        "--out",
                        str(self.paths.report_dir / f"adapter_evaluation_{self.model_version}.csv"),
                    ),
                )
            ]
        if self.mode == "rank":
            commands: list[tuple[str, list[str]]] = []
            if self.apply_before_rank:
                commands.append(
                    (
                        "Scoring folder with adapter",
                        self._command(
                            db_path,
                            "apply-adapter",
                            "--model-version",
                            self.model_version,
                            "--out",
                            str(self.paths.report_dir / f"adapter_scores_{self.model_version}.csv"),
                        ),
                    )
                )
            commands.append(
                (
                    "Ranking with adapter",
                    self._command(
                        db_path,
                        "rank-adapter",
                        "--model-version",
                        self.model_version,
                        "--base-weight",
                        "0",
                        "--adapter-weight",
                        "1",
                        "--out",
                        str(self.paths.report_dir / f"adapter_ranking_{self.model_version}.csv"),
                    ),
                )
            )
            return commands
        raise ValueError(f"Unsupported adapter task mode: {self.mode}")

    def _command(self, db_path: Path, command: str, *args: str) -> list[str]:
        return [
            str(self.runtime.python_executable),
            str(self.runtime.cli_entrypoint),
            "--db",
            str(db_path),
            "--log-dir",
            str(self.paths.hidden_root / "logs"),
            "--run-id",
            self.run_id,
            command,
            *args,
        ]

    def _run_command(self, command: list[str], stage_message: str) -> None:
        env = dict(os.environ)
        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONPATH"] = _aiculler_pythonpath(self.runtime.root, env.get("PYTHONPATH", ""))
        process = subprocess.Popen(
            command,
            cwd=str(self.runtime.root),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=env,
        )
        output_lines: list[str] = []
        assert process.stdout is not None
        for raw_line in iter(process.stdout.readline, ""):
            line = raw_line.strip()
            if not line:
                continue
            output_lines.append(line)
            self.signals.log.emit(line)
            self.signals.progress.emit(0, 0, stage_message)
        return_code = process.wait()
        if return_code != 0:
            tail = "\n".join(output_lines[-30:])
            raise RuntimeError(f"{stage_message} failed." + (f"\n\n{tail}" if tail else ""))


class AICullerGlobalAdapterTask(QRunnable):
    def __init__(
        self,
        *,
        runtime: AICullerRuntime,
        labels: tuple[GlobalAdapterLabel, ...],
        model_version: str = "",
        run_id: str | None = None,
    ) -> None:
        super().__init__()
        self.runtime = runtime
        self.labels = labels
        self.paths = global_aiculler_workflow_paths()
        self.model_version = model_version.strip() or f"Global Adapter {time.strftime('%Y-%m-%d %H.%M.%S')}"
        self.run_id = run_id or self.model_version
        self.signals = AICullerCommandSignals()
        self.setAutoDelete(True)

    def run(self) -> None:
        try:
            self.runtime.validate()
            usable_labels = tuple(label for label in self.labels if Path(label.source_path).exists())
            if len(usable_labels) < 2:
                raise ValueError("Global adapter training needs at least two labeled images that still exist on disk.")
            if len({label.label for label in usable_labels}) < 2:
                raise ValueError("Global adapter training needs at least two different label values.")

            self.paths.hidden_root.mkdir(parents=True, exist_ok=True)
            self.paths.artifacts_dir.mkdir(parents=True, exist_ok=True)
            self.paths.report_dir.mkdir(parents=True, exist_ok=True)
            db_path = aiculler_db_path(self.paths)
            cache_dir = self.paths.hidden_root / "aiculler_cache"
            include_path = self.paths.artifacts_dir / "global_adapter_include_paths.txt"
            ratings_path = self.paths.artifacts_dir / f".adapter_ratings_{self.model_version}.csv"
            include_path.write_text(
                "\n".join(label.source_path for label in usable_labels) + "\n",
                encoding="utf-8",
            )
            _write_global_adapter_ratings_csv(ratings_path, usable_labels)

            commands = [
                (
                    "Building global adapter image set",
                    self._command(
                        db_path,
                        "ingest",
                        str(self.paths.folder),
                        "--cache",
                        str(cache_dir),
                        "--clip",
                        str(self.runtime.clip_vision_model),
                        "--workers",
                        str(max(1, self.runtime.workers)),
                        "--include-paths-file",
                        str(include_path),
                        *self._topiq_args(),
                    ),
                ),
                (
                    "Assigning semantic categories",
                    self._command(
                        db_path,
                        "assign-categories",
                        "--text-model",
                        str(self.runtime.clip_text_model),
                        "--tokenizer",
                        str(self.runtime.tokenizer),
                        "--out",
                        str(self.paths.report_dir / "semantic_classifications.csv"),
                        *self._category_args(),
                    ),
                ),
                (
                    "Clustering within categories",
                    self._command(
                        db_path,
                        "cluster-categories",
                        "--cluster-run-id",
                        self.run_id,
                        "--out",
                        str(self.paths.report_dir / "semantic_clusters.csv"),
                    ),
                ),
                (
                    "Importing global adapter labels",
                    self._command(
                        db_path,
                        "import-ratings",
                        "--ratings",
                        str(ratings_path),
                        "--source",
                        "image_triage_global",
                        "--skip-missing",
                    ),
                ),
                (
                    "Training global preference adapter",
                    self._command(
                        db_path,
                        "train-adapter",
                        "--model-version",
                        self.model_version,
                        "--base-weight",
                        "0",
                        "--adapter-weight",
                        "1",
                        "--out",
                        str(self.paths.report_dir / f"adapter_scores_{self.model_version}.csv"),
                    ),
                ),
                (
                    "Evaluating global preference adapter",
                    self._command(
                        db_path,
                        "evaluate-adapter",
                        "--model-version",
                        self.model_version,
                        "--out",
                        str(self.paths.report_dir / f"adapter_evaluation_{self.model_version}.csv"),
                    ),
                ),
            ]
            self.signals.started.emit(len(commands))
            for index, (message, command) in enumerate(commands, start=1):
                self.signals.stage.emit(index, len(commands), message)
                self._run_command(command, message)
            write_run_config(
                self.paths,
                runtime=self.runtime,
                mode="global_adapter_train",
                run_id=self.run_id,
                model_version=self.model_version,
            )
            self.signals.finished.emit(
                {
                    "mode": "global_train",
                    "scope": "global",
                    "model_version": self.model_version,
                    "report_dir": str(self.paths.report_dir),
                    "evaluation_csv_path": str(self.paths.report_dir / f"adapter_evaluation_{self.model_version}.csv"),
                    "label_count": len(usable_labels),
                }
            )
        except Exception as exc:
            self.signals.failed.emit(str(exc))

    def _topiq_args(self) -> tuple[str, ...]:
        if self.runtime.topiq_model is None:
            return ()
        return ("--topiq", str(self.runtime.topiq_model))

    def _category_args(self) -> tuple[str, ...]:
        if self.runtime.categories_csv is None:
            return ()
        return ("--categories", str(self.runtime.categories_csv))

    def _command(self, db_path: Path, command: str, *args: str) -> list[str]:
        return [
            str(self.runtime.python_executable),
            str(self.runtime.cli_entrypoint),
            "--db",
            str(db_path),
            "--log-dir",
            str(self.paths.hidden_root / "logs"),
            "--run-id",
            self.run_id,
            command,
            *args,
        ]

    def _run_command(self, command: list[str], stage_message: str) -> None:
        env = dict(os.environ)
        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONPATH"] = _aiculler_pythonpath(self.runtime.root, env.get("PYTHONPATH", ""))
        process = subprocess.Popen(
            command,
            cwd=str(self.runtime.root),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=env,
        )
        output_lines: list[str] = []
        assert process.stdout is not None
        for raw_line in iter(process.stdout.readline, ""):
            line = raw_line.strip()
            if not line:
                continue
            output_lines.append(line)
            self.signals.log.emit(line)
            self.signals.progress.emit(0, 0, stage_message)
        return_code = process.wait()
        if return_code != 0:
            tail = "\n".join(output_lines[-30:])
            raise RuntimeError(f"{stage_message} failed." + (f"\n\n{tail}" if tail else ""))


def _write_global_adapter_ratings_csv(path: Path, labels: tuple[GlobalAdapterLabel, ...]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=("source_path", "filename", "label", "rating", "winner", "reject", "review_round", "weight"),
        )
        writer.writeheader()
        for label in labels:
            writer.writerow(
                {
                    "source_path": label.source_path,
                    "filename": label.filename or Path(label.source_path).name,
                    "label": label.label,
                    "rating": "",
                    "winner": int(label.label in {"hero", "portfolio", "keep", "good", "k", "yes", "1"}),
                    "reject": int(label.label in {"reject", "bad", "r", "no", "0"}),
                    "review_round": "adapter_global_dispute" if label.is_dispute else "adapter_global_review",
                    "weight": label.weight,
                }
            )


def default_aiculler_runtime(workers: int | None = None, clip_model_variant: str | None = None) -> AICullerRuntime:
    root = Path(os.environ.get("IMAGE_TRIAGE_AICULLER_ROOT", "") or _default_aiculler_root()).expanduser().resolve()
    model_root = _default_aiculler_model_root(root)
    python_executable = Path(
        os.environ.get("IMAGE_TRIAGE_AICULLER_PYTHON", "")
        or _default_aiculler_python(root)
    ).expanduser().resolve()
    cli_entrypoint = Path(
        os.environ.get("IMAGE_TRIAGE_AICULLER_CLI", "")
        or _default_aiculler_cli(root)
    ).expanduser().resolve()
    clip_root = model_root / "Clip" / "clip-vit-large-patch14"
    resolved_clip_variant = coerce_clip_model_variant(
        clip_model_variant
        or os.environ.get(CLIP_MODEL_VARIANT_ENV, "")
        or DEFAULT_CLIP_MODEL_VARIANT
    )
    default_clip_vision, default_clip_text = _clip_model_paths_for_variant(clip_root, resolved_clip_variant)
    configured_topiq = os.environ.get("IMAGE_TRIAGE_AICULLER_TOPIQ", "").strip()
    topiq_path = Path(configured_topiq or model_root / "TOPIQ" / "topiq_nr.onnx")
    categories_path = Path(os.environ.get("IMAGE_TRIAGE_AICULLER_CATEGORIES", "") or _default_aiculler_config_path(root, "categories.csv"))
    tag_penalties_path = Path(os.environ.get("IMAGE_TRIAGE_AICULLER_TAG_PENALTIES", "") or _default_aiculler_config_path(root, "tag_penalties.csv"))
    avoid_tags = tuple(
        tag.strip()
        for tag in os.environ.get(
            "IMAGE_TRIAGE_AICULLER_AVOID_TAGS",
            "blownout,harshlight,outoffocus,motionblur",
        ).split(",")
        if tag.strip()
    )
    return AICullerRuntime(
        root=root,
        python_executable=python_executable,
        cli_entrypoint=cli_entrypoint,
        clip_vision_model=Path(
            os.environ.get("IMAGE_TRIAGE_AICULLER_CLIP_VISION", "")
            or default_clip_vision
        ).expanduser().resolve(),
        clip_text_model=Path(
            os.environ.get("IMAGE_TRIAGE_AICULLER_CLIP_TEXT", "")
            or default_clip_text
        ).expanduser().resolve(),
        tokenizer=Path(
            os.environ.get("IMAGE_TRIAGE_AICULLER_TOKENIZER", "")
            or clip_root / "tokenizer.json"
        ).expanduser().resolve(),
        clip_model_variant=resolved_clip_variant,
        topiq_model=(
            topiq_path.expanduser().resolve()
            if configured_topiq or topiq_path.exists()
            else None
        ),
        categories_csv=categories_path.expanduser().resolve() if categories_path.exists() else None,
        tag_penalties_csv=tag_penalties_path.expanduser().resolve() if tag_penalties_path.exists() else None,
        avoid_tags=avoid_tags,
        penalty_weight=float(os.environ.get("IMAGE_TRIAGE_AICULLER_PENALTY_WEIGHT", "0.85") or "0.85"),
        workers=int(workers) if workers is not None and workers > 0 else int(os.environ.get("IMAGE_TRIAGE_AICULLER_WORKERS", "4") or "4"),
    )


def _clip_model_paths_for_variant(clip_root: Path, variant: str) -> tuple[Path, Path]:
    normalized = coerce_clip_model_variant(variant)
    onnx_root = clip_root / "onnx"
    if normalized == "fp32":
        return onnx_root / "vision_model.onnx", onnx_root / "text_model.onnx"
    return onnx_root / f"vision_model_{normalized}.onnx", onnx_root / f"text_model_{normalized}.onnx"


def _default_aiculler_python(root: Path) -> Path:
    app_dir = Path(sys.executable).resolve().parent
    runner = app_dir / ("ai_python_runner.exe" if os.name == "nt" else "ai_python_runner")
    if runner.exists():
        return runner
    root_venv_python = root / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if root_venv_python.exists():
        return root_venv_python
    legacy_venv_python = _legacy_aiculler_root() / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if legacy_venv_python.exists():
        return legacy_venv_python
    return Path(sys.executable)


def _default_aiculler_cli(root: Path) -> Path:
    package_cli = root / "aiculler" / "cli.py"
    if package_cli.exists():
        return package_cli
    return root / "src" / "aiculler" / "cli.py"


def _default_aiculler_root() -> Path:
    if getattr(sys, "frozen", False):
        app_root = Path(sys.executable).resolve().parent
        if (app_root / "aiculler" / "cli.py").exists():
            return app_root
        legacy_bundled = app_root / "vendor" / "cli-culler"
        if legacy_bundled.exists():
            return legacy_bundled
    return DEFAULT_AICULLER_ROOT


def _default_aiculler_config_path(root: Path, filename: str) -> Path:
    candidates = (
        root / "aiculler" / "resources" / filename,
        DEFAULT_AICULLER_CONFIG_ROOT / filename,
        root / filename,
    )
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


def _aiculler_pythonpath(root: Path, existing_pythonpath: str = "") -> str:
    entries = []
    package_root = root if (root / "aiculler").exists() else root / "src"
    entries.append(str(package_root))
    if existing_pythonpath:
        entries.extend(part for part in existing_pythonpath.split(os.pathsep) if part)
    deduped: list[str] = []
    seen: set[str] = set()
    for entry in entries:
        key = os.path.normcase(os.path.abspath(entry))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(entry)
    return os.pathsep.join(deduped)


def _default_aiculler_model_root(root: Path) -> Path:
    configured = os.environ.get("IMAGE_TRIAGE_AICULLER_MODEL_ROOT", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    candidates = (
        _default_aiculler_cache_model_root(),
        root / "models",
        _legacy_aiculler_root() / "models",
    )
    for candidate in candidates:
        if candidate.exists():
            return candidate.expanduser().resolve()
    return (root / "models").expanduser().resolve()


def _default_aiculler_cache_model_root() -> Path:
    return _default_user_cache_root() / "image_triage_ai_cache" / "models" / "CLI-Culler"


def _default_user_cache_root() -> Path:
    if os.name == "nt":
        local_appdata = os.environ.get("LOCALAPPDATA", "").strip()
        if local_appdata:
            return Path(local_appdata)
        userprofile = os.environ.get("USERPROFILE", "").strip()
        if userprofile:
            return Path(userprofile) / "AppData" / "Local"
        try:
            return Path.home() / "AppData" / "Local"
        except RuntimeError:
            return Path(tempfile.gettempdir())
    xdg_cache_home = os.environ.get("XDG_CACHE_HOME", "").strip()
    if xdg_cache_home:
        return Path(xdg_cache_home)
    try:
        return Path.home() / ".cache"
    except RuntimeError:
        return Path(tempfile.gettempdir())


def _legacy_aiculler_root() -> Path:
    try:
        return Path.home() / "Documents" / "GitHub" / "CLI-Culler"
    except RuntimeError:
        userprofile = os.environ.get("USERPROFILE", "").strip()
        if userprofile:
            return Path(userprofile) / "Documents" / "GitHub" / "CLI-Culler"
        return Path("C:/Users/tylle/Documents/GitHub/CLI-Culler")


def aiculler_runtime_available() -> bool:
    try:
        return aiculler_runtime_status().is_ready
    except Exception:
        return False


def aiculler_runtime_status(workers: int | None = None) -> AICullerRuntimeStatus:
    runtime = default_aiculler_runtime(workers=workers)
    required: list[str] = []
    optional: list[str] = []
    for label, path in (
        ("CLI-Culler root", runtime.root),
        ("CLI-Culler Python", runtime.python_executable),
        ("CLI-Culler entrypoint", runtime.cli_entrypoint),
        ("CLIP vision model", runtime.clip_vision_model),
        ("CLIP text model", runtime.clip_text_model),
        ("CLIP tokenizer", runtime.tokenizer),
    ):
        if not path.exists():
            required.append(f"{label}: {path}")
    if runtime.categories_csv is not None and not runtime.categories_csv.exists():
        required.append(f"category prompts: {runtime.categories_csv}")
    if runtime.tag_penalties_csv is not None and not runtime.tag_penalties_csv.exists():
        required.append(f"tag penalties: {runtime.tag_penalties_csv}")
    if runtime.topiq_model is None:
        optional.append("TOPIQ model: not configured; heuristic technical scoring will be used")
    elif not runtime.topiq_model.exists():
        optional.append(f"TOPIQ model: {runtime.topiq_model}")
    return AICullerRuntimeStatus(
        runtime=runtime,
        missing_required=tuple(required),
        missing_optional=tuple(optional),
    )


def build_aiculler_workflow_paths(folder: str | Path) -> AIWorkflowPaths:
    return build_ai_workflow_paths(folder)


def aiculler_db_path(paths: AIWorkflowPaths) -> Path:
    return paths.artifacts_dir / "aiculler.sqlite"


def global_aiculler_workflow_paths() -> AIWorkflowPaths:
    root = default_global_adapter_workspace_path()
    return AIWorkflowPaths(
        folder=root,
        hidden_root=root,
        artifacts_dir=root / "artifacts",
        report_dir=root / "ranker_report",
        ranked_export_path=root / "ranker_report" / "aiculler_ranked_export.csv",
        html_report_path=root / "ranker_report" / "aiculler_report.html",
        semantic_export_path=root / "ranker_report" / "semantic_classifications.csv",
        semantic_summary_path=root / "ranker_report" / "semantic_summary.json",
    )


def global_aiculler_db_path() -> Path:
    return default_global_adapter_db_path()


def latest_adapter_model_version(db_path: str | Path) -> str:
    path = Path(db_path)
    if not path.exists():
        return ""
    with sqlite3.connect(path) as connection:
        row = connection.execute(
            """
            SELECT model_version
            FROM adapter_models
            ORDER BY created_at DESC, model_version DESC
            LIMIT 1
            """
        ).fetchone()
    return str(row[0]) if row else ""


def list_adapter_model_summaries(db_path: str | Path) -> list[dict[str, object]]:
    path = Path(db_path)
    if not path.exists():
        return []
    summaries: list[dict[str, object]] = []
    connection = sqlite3.connect(path)
    try:
        rows = connection.execute(
            """
            SELECT
                adapter_models.model_version,
                adapter_models.created_at,
                adapter_models.metrics_json,
                adapter_models.training_config_json,
                COUNT(adapter_scores.image_id) AS scored_count
            FROM adapter_models
            LEFT JOIN adapter_scores
                ON adapter_scores.model_version = adapter_models.model_version
            GROUP BY adapter_models.model_version
            ORDER BY adapter_models.created_at DESC, adapter_models.model_version DESC
            """
        ).fetchall()
    finally:
        connection.close()
    for row in rows:
        try:
            metrics = json.loads(row[2] or "{}")
        except (TypeError, ValueError):
            metrics = {}
        try:
            config = json.loads(row[3] or "{}")
        except (TypeError, ValueError):
            config = {}
        summary_config = dict(config) if isinstance(config, dict) else {}
        summary_config.pop("model_data", None)
        train = metrics.get("train") if isinstance(metrics, dict) else None
        holdout = metrics.get("holdout") if isinstance(metrics, dict) else None
        train_mae = _as_optional_float(train.get("mae")) if isinstance(train, dict) else None
        holdout_mae = _as_optional_float(holdout.get("mae")) if isinstance(holdout, dict) else None
        failure_rate = holdout_mae if holdout_mae is not None else train_mae
        accuracy_percent = None if failure_rate is None else max(0.0, min(100.0, (1.0 - failure_rate) * 100.0))
        summaries.append(
            {
                "model_version": str(row[0]),
                "created_at": str(row[1] or ""),
                "scored_count": int(row[4] or 0),
                "train_mae": train_mae,
                "holdout_mae": holdout_mae,
                "accuracy_percent": accuracy_percent,
                "train_count": _as_optional_int(train.get("count")) if isinstance(train, dict) else None,
                "holdout_count": _as_optional_int(holdout.get("count")) if isinstance(holdout, dict) else None,
                "train_rank_lift": _as_optional_float(train.get("rank_lift")) if isinstance(train, dict) else None,
                "holdout_rank_lift": _as_optional_float(holdout.get("rank_lift")) if isinstance(holdout, dict) else None,
                "training_config": summary_config,
                "label_origin_counts": config.get("label_origin_counts", {}) if isinstance(config, dict) else {},
            }
        )
    return summaries


def delete_adapter_model(db_path: str | Path, model_version: str) -> bool:
    version = str(model_version or "").strip()
    path = Path(db_path)
    if not version or not path.exists():
        return False
    connection = sqlite3.connect(path)
    try:
        connection.execute("DELETE FROM adapter_scores WHERE model_version = ?", (version,))
        cursor = connection.execute("DELETE FROM adapter_models WHERE model_version = ?", (version,))
        connection.commit()
        return cursor.rowcount > 0
    finally:
        connection.close()


def copy_adapter_model(source_db_path: str | Path, target_db_path: str | Path, model_version: str) -> bool:
    version = str(model_version or "").strip()
    source_path = Path(source_db_path)
    target_path = Path(target_db_path)
    if not version or not source_path.exists() or not target_path.exists():
        return False
    source = sqlite3.connect(source_path)
    target = sqlite3.connect(target_path)
    try:
        row = source.execute(
            """
            SELECT model_version, model_type, training_config_json, metrics_json, created_at
            FROM adapter_models
            WHERE model_version = ?
            """,
            (version,),
        ).fetchone()
        if row is None:
            return False
        target.execute(
            """
            INSERT INTO adapter_models (
                model_version, model_type, training_config_json, metrics_json, created_at
            )
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(model_version) DO UPDATE SET
                model_type = excluded.model_type,
                training_config_json = excluded.training_config_json,
                metrics_json = excluded.metrics_json,
                created_at = excluded.created_at
            """,
            tuple(row),
        )
        target.commit()
        return True
    finally:
        source.close()
        target.close()


def aiculler_rerank_readiness(db_path: str | Path) -> dict[str, object]:
    info: dict[str, object] = {
        "db_exists": False,
        "image_count": 0,
        "ready_image_count": 0,
        "cluster_run_id": "",
        "can_rerank": False,
    }
    path = Path(db_path)
    if not path.exists():
        return info
    info["db_exists"] = True
    with sqlite3.connect(path) as connection:
        ready_row = connection.execute(
            "SELECT COUNT(*) FROM images WHERE status = 'ready'"
        ).fetchone()
        info["ready_image_count"] = int(ready_row[0]) if ready_row else 0
        total_row = connection.execute("SELECT COUNT(*) FROM images").fetchone()
        info["image_count"] = int(total_row[0]) if total_row else 0
        run_row = connection.execute(
            """
            SELECT run_id
            FROM semantic_clusters
            ORDER BY created_at DESC, run_id DESC
            LIMIT 1
            """
        ).fetchone()
        info["cluster_run_id"] = str(run_row[0]) if run_row else ""
    info["can_rerank"] = bool(info["ready_image_count"] and info["cluster_run_id"])
    return info


def load_adapter_status_summary(db_path: str | Path) -> dict[str, object]:
    summary: dict[str, object] = {
        "db_exists": False,
        "model_version": "",
        "created_at": "",
        "rating_count": 0,
        "scored_count": 0,
        "train_mae": None,
        "holdout_mae": None,
        "train_rank_lift": None,
        "holdout_rank_lift": None,
        "train_count": None,
        "holdout_count": None,
    }
    path = Path(db_path)
    if not path.exists():
        return summary
    summary["db_exists"] = True
    with sqlite3.connect(path) as connection:
        rating_row = connection.execute("SELECT COUNT(*) FROM ratings").fetchone()
        summary["rating_count"] = int(rating_row[0]) if rating_row else 0
        model_row = connection.execute(
            """
            SELECT model_version, created_at, metrics_json
            FROM adapter_models
            ORDER BY created_at DESC, model_version DESC
            LIMIT 1
            """
        ).fetchone()
        if model_row is None:
            return summary
        model_version = str(model_row[0])
        summary["model_version"] = model_version
        summary["created_at"] = str(model_row[1] or "")
        try:
            metrics = json.loads(model_row[2] or "{}")
        except (TypeError, ValueError):
            metrics = {}
        train = metrics.get("train") if isinstance(metrics, dict) else None
        holdout = metrics.get("holdout") if isinstance(metrics, dict) else None
        if isinstance(train, dict):
            summary["train_mae"] = _as_optional_float(train.get("mae"))
            summary["train_rank_lift"] = _as_optional_float(train.get("rank_lift"))
            summary["train_count"] = _as_optional_int(train.get("count"))
        if isinstance(holdout, dict):
            summary["holdout_mae"] = _as_optional_float(holdout.get("mae"))
            summary["holdout_rank_lift"] = _as_optional_float(holdout.get("rank_lift"))
            summary["holdout_count"] = _as_optional_int(holdout.get("count"))
        scored_row = connection.execute(
            "SELECT COUNT(*) FROM adapter_scores WHERE model_version = ?",
            (model_version,),
        ).fetchone()
        summary["scored_count"] = int(scored_row[0]) if scored_row else 0
    return summary


def _as_optional_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_optional_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def write_gui_exports(
    db_path: Path,
    paths: AIWorkflowPaths,
    *,
    run_id: str = "",
    model_version: str = "",
) -> None:
    rows = (
        _load_adapter_gui_rows(db_path, model_version)
        if model_version
        else _load_ranked_gui_rows(db_path, run_id)
    )
    _write_csv(paths.ranked_export_path, rows)
    _write_semantic_classifications(paths.semantic_export_path, rows)
    _write_semantic_summary(paths.semantic_summary_path, rows)
    _write_html_report(paths.html_report_path, rows)
    _write_gui_diagnostics(
        paths.report_dir / "aiculler_diagnostics.json",
        rows,
        mode="adapter" if model_version else "base",
        run_id=run_id,
        model_version=model_version,
    )


def write_run_config(
    paths: AIWorkflowPaths,
    *,
    runtime: AICullerRuntime,
    mode: str,
    run_id: str = "",
    stages: tuple[str, ...] = (),
    model_version: str = "",
) -> Path:
    payload = {
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "mode": mode,
        "run_id": run_id,
        "stages": list(stages),
        "model_version": model_version,
        "source": {
            "root": str(runtime.root),
            "cli_entrypoint": str(runtime.cli_entrypoint),
            "python": str(runtime.python_executable),
        },
        "models": {
            "clip_model_variant": runtime.clip_model_variant,
            "clip_vision": str(runtime.clip_vision_model),
            "clip_text": str(runtime.clip_text_model),
            "tokenizer": str(runtime.tokenizer),
            "topiq": "" if runtime.topiq_model is None else str(runtime.topiq_model),
        },
        "configs": {
            "categories_csv": "" if runtime.categories_csv is None else str(runtime.categories_csv),
            "tag_penalties_csv": "" if runtime.tag_penalties_csv is None else str(runtime.tag_penalties_csv),
            "avoid_tags": list(runtime.avoid_tags),
            "penalty_weight": runtime.penalty_weight,
            "base_score_blend_weight": _BASE_SCORE_BLEND_WEIGHT,
        },
        "ranking": {
            "adapter_export_blend_owner": "image_triage_gui_export",
            "cli_adapter_base_weight": 0.0,
            "cli_adapter_adapter_weight": 1.0,
        },
    }
    target = paths.report_dir / "aiculler_run_config.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return target


def _write_gui_diagnostics(
    path: Path,
    rows: list[dict[str, object]],
    *,
    mode: str,
    run_id: str = "",
    model_version: str = "",
) -> None:
    group_counts: dict[str, int] = {}
    penalized = 0
    max_penalty = 0.0
    for row in rows:
        group_id = str(row.get("group_id") or row.get("cluster_id") or "ungrouped")
        group_counts[group_id] = group_counts.get(group_id, 0) + 1
        penalty = _as_debug_float(row.get("duplicate_diversity_penalty"))
        if penalty > 0:
            penalized += 1
            max_penalty = max(max_penalty, penalty)
    top_rows = []
    for row in rows[:40]:
        top_rows.append(
            {
                "rank": row.get("rank"),
                "file_name": row.get("file_name"),
                "group_id": row.get("group_id"),
                "rank_in_cluster": row.get("rank_in_cluster"),
                "score": row.get("score"),
                "pre_diversity_score": row.get("pre_diversity_score"),
                "duplicate_diversity_penalty": row.get("duplicate_diversity_penalty"),
                "primary_category": row.get("primary_category"),
            }
        )
    payload = {
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "mode": mode,
        "run_id": run_id,
        "model_version": model_version,
        "row_count": len(rows),
        "group_count": len(group_counts),
        "largest_groups": [
            {"group_id": group_id, "count": count}
            for group_id, count in sorted(group_counts.items(), key=lambda item: (-item[1], item[0]))[:20]
        ],
        "diversity": {
            "enabled": True,
            "penalized_rows": penalized,
            "max_penalty": max_penalty,
            "first_duplicate_penalty": _DUPLICATE_DIVERSITY_FIRST_PENALTY,
            "step_penalty": _DUPLICATE_DIVERSITY_STEP_PENALTY,
            "max_configured_penalty": _DUPLICATE_DIVERSITY_MAX_PENALTY,
        },
        "top_rows": top_rows,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _as_debug_float(value: object) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def load_adapter_review_candidates(
    db_path: Path,
    *,
    max_rows: int = 120,
    top_global_quota: int = 10,
    min_per_category: int = 2,
    already_labeled: set[str] | frozenset[str] | None = None,
) -> list[dict[str, object]]:
    """Pick adapter-review candidates with proportional category quotas.

    Each populated category gets a slot budget proportional to how many of the
    folder's images it contains, so a folder with 100 wildlife and 200 landscape
    images surfaces labels in a 1:2 split. Within each category, picks favor
    images where the technical/base score and the final ranked score disagree
    most (the labels that move the model). Paths in ``already_labeled`` are
    de-prioritized but a small revisit slice still appears so the user can
    correct earlier ratings.
    """

    if max_rows <= 0:
        return []
    labeled = {str(path) for path in (already_labeled or ())}
    run_id = _latest_cluster_run_id(db_path)
    rows = _load_ranked_gui_rows(db_path, run_id)
    if not rows:
        raise ValueError("Run Index & Score in the AI Workflow Center before reviewing adapter labels.")

    selected: dict[str, dict[str, object]] = {}
    reasons: dict[str, list[str]] = {}

    def add_row(row: dict[str, object], reason: str) -> bool:
        key = str(row.get("file_path") or "")
        if not key:
            return False
        first_time = key not in selected
        selected.setdefault(key, row)
        reason_list = reasons.setdefault(key, [])
        if reason not in reason_list:
            reason_list.append(reason)
        if key in labeled and "already_labeled" not in reason_list:
            reason_list.append("already_labeled")
        return first_time

    ranked_rows = sorted(rows, key=lambda row: int(row.get("rank") or 0))
    global_budget = max(0, min(top_global_quota, max_rows))
    for row in ranked_rows[:global_budget]:
        add_row(row, "top_global")

    remaining = max(0, max_rows - len(selected))
    if remaining > 0:
        by_category: dict[str, list[dict[str, object]]] = {}
        for row in rows:
            category = str(row.get("primary_category") or "uncategorized") or "uncategorized"
            by_category.setdefault(category, []).append(row)
        total = sum(len(items) for items in by_category.values()) or 1
        # Only revisit labeled items that aren't already in the result set; otherwise
        # the reserved budget gets wasted and fresh categories don't fill it.
        revisit_pool = sum(1 for path in labeled if path not in selected)
        revisit_budget = min(remaining // 4, revisit_pool)
        fresh_budget = remaining - revisit_budget
        if fresh_budget < 0:
            fresh_budget = 0
        category_quotas = _proportional_quotas(
            counts={cat: len(items) for cat, items in by_category.items()},
            budget=fresh_budget,
            total=total,
            min_per_category=max(0, min_per_category),
        )
        for category, items in by_category.items():
            quota = category_quotas.get(category, 0)
            if quota <= 0:
                continue
            ranked_for_category = sorted(
                items,
                key=lambda row: (
                    1 if str(row.get("file_path") or "") in labeled else 0,
                    -_informativeness_score(row),
                    int(row.get("rank") or 0),
                ),
            )
            picked = 0
            for row in ranked_for_category:
                key = str(row.get("file_path") or "")
                if key in selected or key in labeled:
                    continue
                if add_row(row, f"category:{category}"):
                    if float(row.get("tag_penalty") or 0.0) > 0.0:
                        reasons.setdefault(key, []).append("penalized")
                    picked += 1
                    if picked >= quota:
                        break

        if revisit_budget > 0:
            revisit_rows = [row for row in rows if str(row.get("file_path") or "") in labeled]
            revisit_rows.sort(key=lambda row: -_informativeness_score(row))
            revisits_added = 0
            for row in revisit_rows:
                key = str(row.get("file_path") or "")
                if key in selected:
                    continue
                if add_row(row, "revisit"):
                    revisits_added += 1
                    if revisits_added >= revisit_budget:
                        break

    ordered = sorted(
        selected.values(),
        key=lambda row: (
            0 if "top_global" in reasons.get(str(row.get("file_path") or ""), []) else 1,
            str(row.get("primary_category") or "uncategorized").casefold(),
            int(row.get("rank") or 0),
            str(row.get("file_name") or "").casefold(),
        ),
    )[:max_rows]

    candidates: list[dict[str, object]] = []
    for row in ordered:
        key = str(row.get("file_path") or "")
        candidate = dict(row)
        candidate["label"] = ""
        candidate["review_reason"] = ";".join(reasons.get(key, []))
        candidates.append(candidate)
    return candidates


def _informativeness_score(row: dict[str, object]) -> float:
    final_score = float(row.get("final_score") or row.get("score") or 0.0)
    base_score = float(row.get("tag_base_score") or row.get("technical_score") or final_score)
    disagreement = abs(base_score - final_score)
    penalty = float(row.get("tag_penalty") or 0.0)
    ambiguity = 1.0 - abs(final_score - 0.5) * 2.0
    return disagreement * 1.5 + penalty * 1.25 + max(0.0, ambiguity) * 0.5


def _proportional_quotas(
    *,
    counts: dict[str, int],
    budget: int,
    total: int,
    min_per_category: int,
) -> dict[str, int]:
    if budget <= 0 or total <= 0:
        return {category: 0 for category in counts}
    quotas: dict[str, int] = {}
    fractions: dict[str, float] = {}
    for category, count in counts.items():
        if count <= 0:
            quotas[category] = 0
            continue
        share = budget * (count / total)
        base = int(share)
        seeded = min(count, max(base, min_per_category))
        quotas[category] = seeded
        fractions[category] = share - base
    overflow = sum(quotas.values()) - budget
    if overflow > 0:
        candidates = sorted(
            (cat for cat in quotas if quotas[cat] > max(0, min_per_category)),
            key=lambda cat: fractions.get(cat, 0.0),
        )
        for category in candidates:
            if overflow <= 0:
                break
            slack = quotas[category] - max(0, min_per_category)
            give_back = min(slack, overflow)
            quotas[category] -= give_back
            overflow -= give_back
    deficit = budget - sum(quotas.values())
    if deficit > 0:
        candidates = sorted(
            (cat for cat in quotas if quotas[cat] < counts[cat]),
            key=lambda cat: fractions.get(cat, 0.0),
            reverse=True,
        )
        for category in candidates:
            if deficit <= 0:
                break
            room = counts[category] - quotas[category]
            give = min(room, deficit)
            quotas[category] += give
            deficit -= give
    return quotas


def _latest_cluster_run_id(db_path: Path) -> str:
    with sqlite3.connect(db_path) as connection:
        row = connection.execute(
            """
            SELECT run_id
            FROM semantic_clusters
            ORDER BY created_at DESC, run_id DESC
            LIMIT 1
            """
        ).fetchone()
    return str(row[0]) if row else ""


def _load_ranked_gui_rows(db_path: Path, run_id: str) -> list[dict[str, object]]:
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        image_rows = connection.execute(
            """
            SELECT
                images.id,
                images.source_path,
                images.technical_score,
                images.tag_base_score,
                images.tag_penalty,
                images.tag_flags,
                images.final_score,
                COALESCE(image_categories.primary_category, 'uncategorized') AS primary_category,
                image_cluster_memberships.cluster_id,
                semantic_clusters.label AS cluster_label
            FROM images
            LEFT JOIN image_categories ON image_categories.image_id = images.id
            LEFT JOIN image_cluster_memberships
              ON image_cluster_memberships.image_id = images.id
             AND image_cluster_memberships.cluster_id IN (
                SELECT cluster_id FROM semantic_clusters WHERE run_id = ?
             )
            LEFT JOIN semantic_clusters
              ON semantic_clusters.cluster_id = image_cluster_memberships.cluster_id
             AND semantic_clusters.run_id = ?
            WHERE images.status = 'ready'
              AND images.final_score IS NOT NULL
            """,
            (run_id, run_id),
        ).fetchall()

    return _rows_to_gui_output(image_rows)


# How much weight the tag-penalty-aware base score gets vs. the adapter score
# when blending. Range 0.0-1.0 (window pushes the user's slider value here).
# Default 0.65 = base score (with penalties) wins over adapter; 1.0 = adapter
# is completely ignored; 0.0 = adapter only, penalties have no influence.
_BASE_SCORE_BLEND_WEIGHT = 0.65
_DUPLICATE_DIVERSITY_FIRST_PENALTY = 0.14
_DUPLICATE_DIVERSITY_STEP_PENALTY = 0.04
_DUPLICATE_DIVERSITY_MAX_PENALTY = 0.35


def set_base_score_blend_weight(weight: float) -> None:
    """Update the blend weight between the tag-penalty-aware base score and
    the adapter score. Called by MainWindow whenever the user changes the
    'Base score weight' slider in Settings -> AI."""

    global _BASE_SCORE_BLEND_WEIGHT
    _BASE_SCORE_BLEND_WEIGHT = max(0.0, min(1.0, float(weight)))


def _load_adapter_gui_rows(db_path: Path, model_version: str) -> list[dict[str, object]]:
    base_weight = _BASE_SCORE_BLEND_WEIGHT
    adapter_weight = 1.0 - base_weight
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        image_rows = connection.execute(
            f"""
            SELECT
                images.id,
                images.source_path,
                images.technical_score,
                images.tag_base_score,
                images.tag_penalty,
                images.tag_flags,
                (
                    COALESCE(images.final_score, images.technical_score, 0.0) * {base_weight:.6f}
                    + adapter_scores.adapter_score * {adapter_weight:.6f}
                ) AS final_score,
                COALESCE(adapter_scores.primary_category, image_categories.primary_category, 'uncategorized') AS primary_category,
                adapter_scores.cluster_id,
                semantic_clusters.label AS cluster_label
            FROM adapter_scores
            JOIN images ON images.id = adapter_scores.image_id
            LEFT JOIN image_categories ON image_categories.image_id = images.id
            LEFT JOIN semantic_clusters ON semantic_clusters.cluster_id = adapter_scores.cluster_id
            WHERE adapter_scores.model_version = ?
            """,
            (model_version,),
        ).fetchall()
    return _rows_to_gui_output(image_rows)


def _rows_to_gui_output(image_rows: list[sqlite3.Row]) -> list[dict[str, object]]:
    groups: dict[str, list[sqlite3.Row]] = {}
    for row in image_rows:
        group_id = _diversity_group_id(row)
        groups.setdefault(group_id, []).append(row)

    ranked_groups: list[tuple[str, list[sqlite3.Row]]] = []
    for group_id in sorted(groups, key=lambda key: _group_sort_key(key, groups[key])):
        group_rows = sorted(groups[group_id], key=lambda item: (-float(item["final_score"] or 0.0), Path(item["source_path"]).name.casefold()))
        ranked_groups.append((group_id, group_rows))

    output: list[dict[str, object]] = []
    global_rank = 1
    max_group_size = max((len(rows) for _, rows in ranked_groups), default=0)
    for rank_index in range(max_group_size):
        round_rows: list[tuple[str, list[sqlite3.Row], sqlite3.Row]] = []
        for group_id, group_rows in ranked_groups:
            if rank_index < len(group_rows):
                round_rows.append((group_id, group_rows, group_rows[rank_index]))
        round_rows.sort(key=lambda item: (-_diversified_score(item[2], rank_index + 1, len(item[1])), Path(item[2]["source_path"]).name.casefold()))
        for group_id, group_rows, row in round_rows:
            group_size = len(group_rows)
            rank_in_group = rank_index + 1
            source_path = str(row["source_path"])
            original_score = float(row["final_score"] or row["technical_score"] or 0.0)
            penalty = _duplicate_diversity_penalty(rank_in_group, group_size)
            score = max(0.0, original_score - penalty)
            output.append(
                {
                    "rank": global_rank,
                    "image_id": str(row["id"]),
                    "file_path": source_path,
                    "file_name": Path(source_path).name,
                    "cluster_id": group_id,
                    "group_id": group_id,
                    "semantic_group_id": _gui_group_id(row),
                    "cluster_size": group_size,
                    "group_size": group_size,
                    "rank_in_cluster": rank_in_group,
                    "score": score,
                    "technical_score": row["technical_score"],
                    "tag_base_score": _row_value(row, "tag_base_score"),
                    "tag_penalty": _row_value(row, "tag_penalty"),
                    "triggered_tags": _row_value(row, "tag_flags"),
                    "final_score": score,
                    "pre_diversity_score": original_score,
                    "duplicate_diversity_penalty": penalty,
                    "primary_category": row["primary_category"],
                    "cluster_reason": _cluster_reason(row),
                }
            )
            global_rank += 1
    return output


def _diversified_score(row: sqlite3.Row, rank_in_group: int, group_size: int) -> float:
    original_score = float(row["final_score"] or row["technical_score"] or 0.0)
    return max(0.0, original_score - _duplicate_diversity_penalty(rank_in_group, group_size))


def _duplicate_diversity_penalty(rank_in_group: int, group_size: int) -> float:
    if group_size <= 1 or rank_in_group <= 1:
        return 0.0
    return min(
        _DUPLICATE_DIVERSITY_MAX_PENALTY,
        _DUPLICATE_DIVERSITY_FIRST_PENALTY + (rank_in_group - 2) * _DUPLICATE_DIVERSITY_STEP_PENALTY,
    )


def _diversity_group_id(row: sqlite3.Row) -> str:
    source_path = str(row["source_path"])
    path = Path(source_path)
    match = CAPTURE_SEQUENCE_PATTERN.match(path.stem)
    if match is None:
        return _path_group_key(path)
    try:
        number = int(match.group("number"))
    except ValueError:
        return _path_group_key(path)
    bucket_start = (number // CAPTURE_DIVERSITY_BUCKET_SIZE) * CAPTURE_DIVERSITY_BUCKET_SIZE
    bucket_end = bucket_start + CAPTURE_DIVERSITY_BUCKET_SIZE - 1
    prefix = match.group("prefix").casefold()
    suffix = match.group("suffix").casefold()
    return f"{_path_group_key(path)}::{prefix}{bucket_start:04d}-{bucket_end:04d}{suffix}"


def _path_group_key(path: Path) -> str:
    return os.path.normcase(os.path.normpath(str(path.parent)))


def _norm_path(path: str | Path) -> str:
    return os.path.normcase(os.path.abspath(os.fspath(path)))


def _read_include_paths_file(include_paths_file: Path | None) -> list[Path]:
    if include_paths_file is None or not include_paths_file.exists():
        return []
    paths: list[Path] = []
    for line in include_paths_file.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text or text.startswith("#"):
            continue
        candidate = Path(text)
        if not candidate.is_absolute():
            candidate = include_paths_file.parent / candidate
        paths.append(candidate)
    return paths


def _file_cache_identity(path: str | Path) -> dict[str, object]:
    candidate = Path(path)
    try:
        resolved = candidate.expanduser().resolve()
        stat = resolved.stat()
    except OSError:
        return {
            "path": str(candidate),
            "exists": False,
        }
    return {
        "path": _norm_path(resolved),
        "exists": True,
        "size": int(stat.st_size),
        "mtime_ns": int(stat.st_mtime_ns),
    }


def _stage_key_from_message(message: str) -> str:
    text = re.sub(r"[^a-z0-9]+", "_", (message or "").strip().casefold())
    return text.strip("_") or "stage"


def _cli_command_from_args(command: list[str]) -> str:
    known_commands = {
        "ingest",
        "assign-categories",
        "cluster-categories",
        "rank",
        "train-adapter",
        "evaluate-adapter",
        "rank-adapter",
        "import-ratings",
    }
    for arg in command:
        text = str(arg)
        if text in known_commands:
            return text
    return ""


def _cli_arg_value(command: list[str], option: str) -> str:
    try:
        index = command.index(option)
    except ValueError:
        return ""
    value_index = index + 1
    if value_index >= len(command):
        return ""
    return str(command[value_index])


def _delete_rows_by_ids(
    connection: sqlite3.Connection,
    table: str,
    column: str,
    ids: list[int],
    *,
    chunk_size: int = 400,
) -> None:
    table_row = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone()
    if table_row is None or not ids:
        return
    for start in range(0, len(ids), max(1, chunk_size)):
        chunk = ids[start : start + chunk_size]
        placeholders = ", ".join("?" for _ in chunk)
        connection.execute(
            f"DELETE FROM {table} WHERE {column} IN ({placeholders})",
            chunk,
        )


_TQDM_PROGRESS_RE = re.compile(
    r"^(?P<desc>.+?):\s*\d+%\|[^|]*\|\s*(?P<current>\d+)/(?P<total>\d+)"
    r"(?:\s*\[(?P<elapsed>[^<\]]+)<(?P<remaining>[^,\]]+))?"
)


def _parse_tqdm_progress(line: str) -> tuple[str, int, int, str] | None:
    """Extract (message, current, total, eta) from a tqdm progress line.

    Matches lines like ``Extracting embeddings: 7%|6 | 192/2895 [00:17<03:51, ...]``.
    Returns None for non-progress output.
    """
    match = _TQDM_PROGRESS_RE.match(line.strip())
    if match is None:
        return None
    try:
        current = int(match.group("current"))
        total = int(match.group("total"))
    except (TypeError, ValueError):
        return None
    if total <= 0:
        return None
    message = match.group("desc").strip() or "Working"
    remaining = (match.group("remaining") or "").strip()
    eta_text = remaining if remaining and "?" not in remaining else ""
    return message, current, total, eta_text


def _row_value(row: sqlite3.Row, name: str, default: object = "") -> object:
    return row[name] if name in row.keys() else default


def _gui_group_id(row: sqlite3.Row) -> str:
    cluster_id = row["cluster_id"]
    if cluster_id is not None:
        label = str(row["cluster_label"] or "").strip()
        return label or f"semantic_cluster_{int(cluster_id)}"
    category = str(row["primary_category"] or "uncategorized").strip() or "uncategorized"
    return category


def _cluster_reason(row: sqlite3.Row) -> str:
    category = str(row["primary_category"] or "uncategorized").strip() or "uncategorized"
    label = str(row["cluster_label"] or "").strip()
    if label:
        return f"semantic category: {category}; cluster: {label}"
    return f"semantic category: {category}"


def _group_sort_key(group_id: str, rows: list[sqlite3.Row]) -> tuple[float, str]:
    best = max(float(row["final_score"] or 0.0) for row in rows) if rows else 0.0
    return (-best, group_id.casefold())


def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0].keys()) if rows else [
        "rank",
        "image_id",
        "file_path",
        "file_name",
        "cluster_id",
        "group_id",
        "cluster_size",
        "group_size",
        "rank_in_cluster",
        "score",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _write_semantic_summary(path: Path, rows: list[dict[str, object]]) -> None:
    counts: dict[str, int] = {}
    for row in rows:
        category = str(row.get("primary_category") or "uncategorized")
        counts[category] = counts.get(category, 0) + 1
    path.write_text(
        json.dumps({"classified_images": len(rows), "category_counts": counts}, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _write_semantic_classifications(path: Path, rows: list[dict[str, object]]) -> None:
    fieldnames = ["file_path", "primary_label", "primary_score", "status"]
    records = [
        {
            "file_path": row.get("file_path") or "",
            "primary_label": row.get("primary_category") or "uncategorized",
            "primary_score": 1.0,
            "status": "ready",
        }
        for row in rows
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(records)


def _write_html_report(path: Path, rows: list[dict[str, object]]) -> None:
    body_rows = "\n".join(
        "<tr>"
        f"<td>{int(row['rank'])}</td>"
        f"<td>{html.escape(str(row['file_name']))}</td>"
        f"<td>{html.escape(str(row['group_id']))}</td>"
        f"<td>{float(row['score']):.4f}</td>"
        f"<td>{html.escape(str(row.get('primary_category') or ''))}</td>"
        "</tr>"
        for row in rows[:500]
    )
    path.write_text(
        """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>AI Culler Report</title>
<style>
body{font-family:Segoe UI,Arial,sans-serif;margin:24px;color:#1f2933}
table{border-collapse:collapse;width:100%}
th,td{border-bottom:1px solid #d9e2ec;padding:7px 9px;text-align:left}
th{background:#f0f4f8}
</style>
</head>
<body>
<h1>AI Culler Report</h1>
<p>Showing the top 500 ranked images.</p>
<table>
<thead><tr><th>Rank</th><th>File</th><th>Group</th><th>Score</th><th>Category</th></tr></thead>
<tbody>
"""
        + body_rows
        + """
</tbody>
</table>
</body>
</html>
""",
        encoding="utf-8",
    )
