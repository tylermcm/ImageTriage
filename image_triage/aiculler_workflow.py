from __future__ import annotations

import csv
import html
import json
import os
import re
import sqlite3
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, Signal

from .ai_workflow import AIWorkflowPaths, build_ai_workflow_paths
from .models import ImageRecord


DEFAULT_AICULLER_ROOT = Path(r"C:\Users\tylle\Documents\GitHub\CLI-Culler")
INGEST_EVENT_PATTERN = re.compile(r"^\[(?P<status>[^\]]+)\]\s+#(?P<current>\d+)\s+")


@dataclass(slots=True, frozen=True)
class AICullerRuntime:
    root: Path
    python_executable: Path
    clip_vision_model: Path
    clip_text_model: Path
    tokenizer: Path
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
    ) -> None:
        super().__init__()
        self.folder = folder
        self.records = records
        self.runtime = runtime
        self.paths = paths
        self.run_id = run_id or time.strftime("%Y%m%dT%H%M%S")
        unknown = tuple(stage for stage in stages if stage not in ALL_AICULLER_STAGES)
        if unknown:
            raise ValueError(f"Unknown AI Culler stage(s): {unknown}")
        self.stages = stages or ALL_AICULLER_STAGES
        self.signals = AICullerRunSignals()
        self.setAutoDelete(True)
        self._cancel_requested = False
        self._current_process: subprocess.Popen[str] | None = None

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
        self.signals.started.emit(folder_text)
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
            commands = [all_commands[stage] for stage in self.stages]

            total = len(commands) + 1
            for index, (message, command) in enumerate(commands, start=1):
                self._raise_if_cancelled()
                self.signals.stage.emit(folder_text, index, total, message)
                self._run_command(command, stage_message=message)

            self._raise_if_cancelled()
            self.signals.stage.emit(folder_text, total, total, "Preparing GUI results")
            self._write_gui_exports(db_path)
            self.signals.finished.emit(folder_text, str(self.paths.report_dir), str(self.paths.html_report_path))
        except _AICullerCancelled:
            self.signals.cancelled.emit(folder_text, "AI review stopped.")
        except Exception as exc:
            self.signals.failed.emit(folder_text, str(exc))
        finally:
            self._current_process = None

    def _command(self, db_path: Path, command: str, *args: str) -> list[str]:
        return [
            str(self.runtime.python_executable),
            "-m",
            "aiculler.cli",
            "--db",
            str(db_path),
            "--log-dir",
            str(self.paths.hidden_root / "logs"),
            "--run-id",
            self.run_id,
            command,
            *args,
        ]

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
        env = dict(os.environ)
        env["PYTHONUNBUFFERED"] = "1"
        source_root = str(self.runtime.root / "src")
        existing_pythonpath = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = source_root if not existing_pythonpath else source_root + os.pathsep + existing_pythonpath
        process = subprocess.Popen(
            command,
            cwd=str(self.runtime.root),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=env,
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
            raise RuntimeError(f"{stage_message} failed." + (f"\n\n{tail}" if tail else ""))

    def _emit_progress_for_line(self, stage_message: str, line: str) -> None:
        self.signals.detail.emit(str(self.folder), line)
        match = INGEST_EVENT_PATTERN.match(line)
        if match is not None and self.records:
            current = min(int(match.group("current")), len(self.records))
            self.signals.progress.emit(str(self.folder), stage_message, current, len(self.records), "")
            return
        self.signals.progress.emit(str(self.folder), stage_message, 0, 0, "")

    def _write_gui_exports(self, db_path: Path) -> None:
        write_gui_exports(db_path, self.paths, run_id=self.run_id)

    def _raise_if_cancelled(self) -> None:
        if self._cancel_requested:
            raise _AICullerCancelled()


class _AICullerCancelled(RuntimeError):
    pass


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
        run_id: str | None = None,
    ) -> None:
        super().__init__()
        self.runtime = runtime
        self.paths = paths
        self.mode = mode
        self.ratings_csv = ratings_csv
        self.ratings_csv_text = ratings_csv_text
        self.model_version = model_version.strip() or time.strftime("%Y%m%dT%H%M%S")
        self.run_id = run_id or self.model_version
        self.signals = AICullerCommandSignals()
        self.setAutoDelete(True)

    def run(self) -> None:
        materialized_ratings: Path | None = None
        try:
            self.runtime.validate()
            db_path = aiculler_db_path(self.paths)
            if not db_path.exists():
                raise FileNotFoundError("Run AI Culler before training or ranking with an adapter.")
            self.paths.report_dir.mkdir(parents=True, exist_ok=True)
            if self.mode == "train" and self.ratings_csv_text:
                self.paths.artifacts_dir.mkdir(parents=True, exist_ok=True)
                materialized_ratings = self.paths.artifacts_dir / f".adapter_ratings_{self.model_version}.csv"
                materialized_ratings.write_text(self.ratings_csv_text, encoding="utf-8")
                self.ratings_csv = materialized_ratings
            commands = self._commands(db_path)
            self.signals.started.emit(len(commands))
            for index, (message, command) in enumerate(commands, start=1):
                self.signals.stage.emit(index, len(commands), message)
                self._run_command(command, message)
            if self.mode in {"train", "rank"}:
                write_gui_exports(db_path, self.paths, model_version=self.model_version)
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
                    "Importing ratings",
                    self._command(db_path, "import-ratings", "--ratings", str(self.ratings_csv), "--source", "image_triage"),
                ),
                (
                    "Training adapter",
                    self._command(
                        db_path,
                        "train-adapter",
                        "--model-version",
                        self.model_version,
                        "--out",
                        str(self.paths.report_dir / f"adapter_scores_{self.model_version}.csv"),
                    ),
                ),
                (
                    "Evaluating adapter",
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
                    "Evaluating adapter",
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
            return [
                (
                    "Ranking with adapter",
                    self._command(
                        db_path,
                        "rank-adapter",
                        "--model-version",
                        self.model_version,
                        "--out",
                        str(self.paths.report_dir / f"adapter_ranking_{self.model_version}.csv"),
                    ),
                )
            ]
        raise ValueError(f"Unsupported adapter task mode: {self.mode}")

    def _command(self, db_path: Path, command: str, *args: str) -> list[str]:
        return [
            str(self.runtime.python_executable),
            "-m",
            "aiculler.cli",
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
        source_root = str(self.runtime.root / "src")
        existing_pythonpath = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = source_root if not existing_pythonpath else source_root + os.pathsep + existing_pythonpath
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


def default_aiculler_runtime() -> AICullerRuntime:
    root = Path(os.environ.get("IMAGE_TRIAGE_AICULLER_ROOT", "") or DEFAULT_AICULLER_ROOT).expanduser().resolve()
    python_executable = Path(
        os.environ.get("IMAGE_TRIAGE_AICULLER_PYTHON", "")
        or root / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    ).expanduser().resolve()
    clip_root = root / "models" / "Clip" / "clip-vit-large-patch14"
    topiq_path = Path(os.environ.get("IMAGE_TRIAGE_AICULLER_TOPIQ", "") or root / "models" / "TOPIQ" / "topiq_nr.onnx")
    categories_path = Path(os.environ.get("IMAGE_TRIAGE_AICULLER_CATEGORIES", "") or root / "categories.csv")
    tag_penalties_path = Path(os.environ.get("IMAGE_TRIAGE_AICULLER_TAG_PENALTIES", "") or root / "tag_penalties.csv")
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
        clip_vision_model=Path(
            os.environ.get("IMAGE_TRIAGE_AICULLER_CLIP_VISION", "")
            or clip_root / "onnx" / "vision_model_uint8.onnx"
        ).expanduser().resolve(),
        clip_text_model=Path(
            os.environ.get("IMAGE_TRIAGE_AICULLER_CLIP_TEXT", "")
            or clip_root / "onnx" / "text_model_uint8.onnx"
        ).expanduser().resolve(),
        tokenizer=Path(
            os.environ.get("IMAGE_TRIAGE_AICULLER_TOKENIZER", "")
            or clip_root / "tokenizer.json"
        ).expanduser().resolve(),
        topiq_model=topiq_path.expanduser().resolve() if str(topiq_path).strip() else None,
        categories_csv=categories_path.expanduser().resolve() if categories_path.exists() else None,
        tag_penalties_csv=tag_penalties_path.expanduser().resolve() if tag_penalties_path.exists() else None,
        avoid_tags=avoid_tags,
        penalty_weight=float(os.environ.get("IMAGE_TRIAGE_AICULLER_PENALTY_WEIGHT", "0.85") or "0.85"),
        workers=int(os.environ.get("IMAGE_TRIAGE_AICULLER_WORKERS", "4") or "4"),
    )


def aiculler_runtime_available() -> bool:
    try:
        default_aiculler_runtime().validate()
    except Exception:
        return False
    return True


def build_aiculler_workflow_paths(folder: str | Path) -> AIWorkflowPaths:
    return build_ai_workflow_paths(folder)


def aiculler_db_path(paths: AIWorkflowPaths) -> Path:
    return paths.artifacts_dir / "aiculler.sqlite"


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
        raise ValueError("Run AI Culler before reviewing adapter labels.")

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


def _load_adapter_gui_rows(db_path: Path, model_version: str) -> list[dict[str, object]]:
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
                (
                    COALESCE(images.final_score, images.technical_score, 0.0) * 0.50
                    + adapter_scores.adapter_score * 0.50
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
        group_id = _gui_group_id(row)
        groups.setdefault(group_id, []).append(row)

    output: list[dict[str, object]] = []
    global_rank = 1
    for group_id in sorted(groups, key=lambda key: _group_sort_key(key, groups[key])):
        group_rows = sorted(groups[group_id], key=lambda item: (-float(item["final_score"] or 0.0), Path(item["source_path"]).name.casefold()))
        group_size = len(group_rows)
        for rank_in_group, row in enumerate(group_rows, start=1):
            source_path = str(row["source_path"])
            score = float(row["final_score"] or row["technical_score"] or 0.0)
            output.append(
                {
                    "rank": global_rank,
                    "image_id": str(row["id"]),
                    "file_path": source_path,
                    "file_name": Path(source_path).name,
                    "cluster_id": group_id,
                    "group_id": group_id,
                    "cluster_size": group_size,
                    "group_size": group_size,
                    "rank_in_cluster": rank_in_group,
                    "score": score,
                    "technical_score": row["technical_score"],
                    "tag_base_score": _row_value(row, "tag_base_score"),
                    "tag_penalty": _row_value(row, "tag_penalty"),
                    "triggered_tags": _row_value(row, "tag_flags"),
                    "final_score": row["final_score"],
                    "primary_category": row["primary_category"],
                    "cluster_reason": _cluster_reason(row),
                }
            )
            global_rank += 1
    return output


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
