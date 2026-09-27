from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path

import numpy as np
from PIL import Image
from PySide6.QtCore import Qt

from image_triage.ai_workflow import AIWorkflowPaths, AIWorkflowRuntime
from image_triage.aiculler_workflow import (
    AICullerRunTask,
    AICullerRuntime,
    SOURCE_AICULLER_ROOT,
    clip_model_variant_options,
    coerce_clip_model_variant,
    compute_and_store_winner_scores,
    default_aiculler_runtime,
    delete_adapter_model,
    list_adapter_model_summaries,
    load_adapter_review_candidates,
    load_latest_winner_scores,
    _aiculler_pythonpath,
    _default_aiculler_python,
    _rows_to_gui_output,
)
from image_triage.models import ImageRecord
from image_triage.phash_prefilter import (
    PHashPrefilterSettings,
    build_phash_prefilter_paths,
    run_phash_prefilter_from_signal_rows,
)


class AICullerWorkflowTests(unittest.TestCase):
    def test_clip_model_catalog_exposes_only_automatic_policy(self) -> None:
        self.assertEqual(
            ("fp32",),
            tuple(option.key for option in clip_model_variant_options()),
        )

    def _build_adapter_review_db(self, folder: Path, *, count: int = 200) -> Path:
        db_path = folder / "aiculler.sqlite"
        connection = sqlite3.connect(db_path)
        try:
            connection.executescript(
                """
                CREATE TABLE images (
                    id INTEGER PRIMARY KEY,
                    source_path TEXT NOT NULL,
                    status TEXT NOT NULL,
                    technical_score REAL,
                    tag_base_score REAL,
                    tag_penalty REAL,
                    tag_flags TEXT,
                    final_score REAL
                );
                CREATE TABLE image_categories (
                    image_id INTEGER PRIMARY KEY,
                    primary_category TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    category_scores_json TEXT NOT NULL,
                    assigned_by TEXT NOT NULL
                );
                CREATE TABLE semantic_clusters (
                    cluster_id INTEGER PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    primary_category TEXT NOT NULL,
                    label TEXT NOT NULL,
                    image_count INTEGER NOT NULL,
                    centroid BLOB NOT NULL,
                    dim INTEGER NOT NULL,
                    dtype TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE image_cluster_memberships (
                    image_id INTEGER NOT NULL,
                    cluster_id INTEGER NOT NULL,
                    distance REAL NOT NULL,
                    rank INTEGER NOT NULL
                );
                """
            )
            for cluster_id in range(1, 21):
                connection.execute(
                    """
                    INSERT INTO semantic_clusters (
                        cluster_id, run_id, primary_category, label, image_count,
                        centroid, dim, dtype, metadata_json
                    )
                    VALUES (?, 'run-1', ?, ?, 10, X'00', 1, 'float32', '{}')
                    """,
                    (cluster_id, "landscape" if cluster_id % 2 else "portrait", f"cluster-{cluster_id}"),
                )
            for index in range(1, count + 1):
                path = folder / f"_DSC{index:04d}.JPG"
                technical = 0.95 - (index % 30) * 0.005
                final = 0.99 - index * 0.002
                category = "landscape" if index % 2 else "portrait"
                cluster_id = ((index - 1) // 10) + 1
                connection.execute(
                    """
                    INSERT INTO images (
                        id, source_path, status, technical_score, tag_base_score,
                        tag_penalty, tag_flags, final_score
                    )
                    VALUES (?, ?, 'ready', ?, ?, 0.0, '', ?)
                    """,
                    (index, str(path), technical, technical - 0.1, final),
                )
                connection.execute(
                    """
                    INSERT INTO image_categories (
                        image_id, primary_category, confidence, category_scores_json, assigned_by
                    )
                    VALUES (?, ?, 1.0, '{}', 'test')
                    """,
                    (index, category),
                )
                connection.execute(
                    """
                    INSERT INTO image_cluster_memberships (image_id, cluster_id, distance, rank)
                    VALUES (?, ?, 0.0, 1)
                    """,
                    (index, cluster_id),
                )
            connection.commit()
        finally:
            connection.close()
        return db_path

    def test_default_runtime_uses_in_repo_cli_culler_source(self) -> None:
        with tempfile.TemporaryDirectory(prefix="image_triage_aiculler_models_") as temp_dir:
            model_root = Path(temp_dir) / "models"
            clip_root = model_root / "Clip" / "TinyCLIP-ViT-8M-16-Text-3M-YFCC15M-ONNX"
            (clip_root / "onnx").mkdir(parents=True)
            (clip_root / "onnx" / "model.onnx").write_bytes(b"model")
            (clip_root / "tokenizer.json").write_text("{}", encoding="utf-8")

            saved_env = {
                name: os.environ.get(name)
                for name in (
                    "IMAGE_TRIAGE_AICULLER_MODEL_ROOT",
                    "IMAGE_TRIAGE_AICULLER_ROOT",
                    "IMAGE_TRIAGE_AICULLER_PYTHON",
                    "IMAGE_TRIAGE_AICULLER_CLI",
                    "IMAGE_TRIAGE_AICULLER_TOPIQ",
                    "IMAGE_TRIAGE_AICULLER_CLIP_VARIANT",
                )
            }
            os.environ["IMAGE_TRIAGE_AICULLER_MODEL_ROOT"] = str(model_root)
            os.environ["IMAGE_TRIAGE_AICULLER_PYTHON"] = sys.executable
            for name in (
                "IMAGE_TRIAGE_AICULLER_ROOT",
                "IMAGE_TRIAGE_AICULLER_CLI",
                "IMAGE_TRIAGE_AICULLER_TOPIQ",
                "IMAGE_TRIAGE_AICULLER_CLIP_VARIANT",
            ):
                os.environ.pop(name, None)
            try:
                runtime = default_aiculler_runtime(workers=3)
            finally:
                for name, value in saved_env.items():
                    if value is None:
                        os.environ.pop(name, None)
                    else:
                        os.environ[name] = value

            self.assertEqual(SOURCE_AICULLER_ROOT.resolve(), runtime.root)
            self.assertEqual((SOURCE_AICULLER_ROOT / "aiculler" / "cli.py").resolve(), runtime.cli_entrypoint)
            self.assertEqual((SOURCE_AICULLER_ROOT / "aiculler" / "resources" / "categories.csv").resolve(), runtime.categories_csv)
            self.assertEqual((SOURCE_AICULLER_ROOT / "aiculler" / "resources" / "tag_penalties.csv").resolve(), runtime.tag_penalties_csv)
            self.assertEqual(Path(sys.executable).resolve(), runtime.python_executable)
            self.assertEqual(clip_root.resolve(), runtime.clip_vision_model.parents[1])
            self.assertIsNone(runtime.topiq_model)
            self.assertEqual(3, runtime.workers)

    def test_default_runtime_and_pythonpath_route_managed_gpu_profile(self) -> None:
        root = Path("C:/app")
        managed = Path("C:/runtime/gpu/site-packages")

        runtime = default_aiculler_runtime(device="cuda")
        pythonpath = _aiculler_pythonpath(
            root,
            "C:/existing",
            runtime_site_packages=(managed,),
        ).split(os.pathsep)

        self.assertEqual("cuda", runtime.device)
        self.assertEqual(str(managed), pythonpath[0])
        self.assertIn("C:/existing", pythonpath)

    def test_default_aiculler_python_does_not_use_project_venv_with_managed_packages(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            venv_python = root / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            venv_python.parent.mkdir(parents=True)
            venv_python.write_bytes(b"")

            selected = _default_aiculler_python(root)

        self.assertEqual(Path(sys.executable), selected)

    def test_default_model_root_prefers_image_triage_cache(self) -> None:
        with tempfile.TemporaryDirectory(prefix="image_triage_aiculler_cache_") as temp_dir:
            local_appdata = Path(temp_dir) / "local"
            model_root = local_appdata / "image_triage_ai_cache" / "models" / "CLI-Culler"
            clip_root = model_root / "Clip" / "TinyCLIP-ViT-8M-16-Text-3M-YFCC15M-ONNX"
            (clip_root / "onnx").mkdir(parents=True)
            (clip_root / "onnx" / "model.onnx").write_bytes(b"model")
            (clip_root / "tokenizer.json").write_text("{}", encoding="utf-8")

            saved_env = {
                name: os.environ.get(name)
                for name in (
                    "LOCALAPPDATA",
                    "IMAGE_TRIAGE_AICULLER_MODEL_ROOT",
                    "IMAGE_TRIAGE_AICULLER_ROOT",
                    "IMAGE_TRIAGE_AICULLER_PYTHON",
                    "IMAGE_TRIAGE_AICULLER_CLI",
                    "IMAGE_TRIAGE_AICULLER_TOPIQ",
                    "IMAGE_TRIAGE_AICULLER_CLIP_VARIANT",
                )
            }
            os.environ["LOCALAPPDATA"] = str(local_appdata)
            os.environ["IMAGE_TRIAGE_AICULLER_PYTHON"] = sys.executable
            for name in (
                "IMAGE_TRIAGE_AICULLER_MODEL_ROOT",
                "IMAGE_TRIAGE_AICULLER_ROOT",
                "IMAGE_TRIAGE_AICULLER_CLI",
                "IMAGE_TRIAGE_AICULLER_TOPIQ",
                "IMAGE_TRIAGE_AICULLER_CLIP_VARIANT",
            ):
                os.environ.pop(name, None)
            try:
                runtime = default_aiculler_runtime()
            finally:
                for name, value in saved_env.items():
                    if value is None:
                        os.environ.pop(name, None)
                    else:
                        os.environ[name] = value

            self.assertEqual((clip_root / "onnx" / "model.onnx").resolve(), runtime.clip_vision_model)
            self.assertEqual((clip_root / "onnx" / "model.onnx").resolve(), runtime.clip_text_model)
            self.assertIsNone(runtime.clip_fallback_vision_model)
            self.assertIsNone(runtime.clip_fallback_text_model)
            self.assertEqual((clip_root / "tokenizer.json").resolve(), runtime.tokenizer)

    def test_default_runtime_ignores_removed_clip_model_variant(self) -> None:
        with tempfile.TemporaryDirectory(prefix="image_triage_aiculler_variant_") as temp_dir:
            model_root = Path(temp_dir) / "models"
            clip_root = model_root / "Clip" / "TinyCLIP-ViT-8M-16-Text-3M-YFCC15M-ONNX"
            (clip_root / "onnx").mkdir(parents=True)
            for filename in ("model.onnx",):
                (clip_root / "onnx" / filename).write_bytes(b"model")
            (clip_root / "tokenizer.json").write_text("{}", encoding="utf-8")

            saved_env = {
                name: os.environ.get(name)
                for name in (
                    "IMAGE_TRIAGE_AICULLER_MODEL_ROOT",
                    "IMAGE_TRIAGE_AICULLER_CLIP_VARIANT",
                    "IMAGE_TRIAGE_AICULLER_CLIP_VISION",
                    "IMAGE_TRIAGE_AICULLER_CLIP_TEXT",
                    "IMAGE_TRIAGE_AICULLER_TOPIQ",
                )
            }
            os.environ["IMAGE_TRIAGE_AICULLER_MODEL_ROOT"] = str(model_root)
            for name in ("IMAGE_TRIAGE_AICULLER_CLIP_VARIANT", "IMAGE_TRIAGE_AICULLER_CLIP_VISION", "IMAGE_TRIAGE_AICULLER_CLIP_TEXT", "IMAGE_TRIAGE_AICULLER_TOPIQ"):
                os.environ.pop(name, None)
            try:
                runtime = default_aiculler_runtime(clip_model_variant="q4")
            finally:
                for name, value in saved_env.items():
                    if value is None:
                        os.environ.pop(name, None)
                    else:
                        os.environ[name] = value

            self.assertEqual("fp32", runtime.clip_model_variant)
            self.assertEqual((clip_root / "onnx" / "model.onnx").resolve(), runtime.clip_vision_model)
            self.assertEqual((clip_root / "onnx" / "model.onnx").resolve(), runtime.clip_text_model)
            self.assertEqual("fp32", coerce_clip_model_variant("not-a-model"))

    def test_default_runtime_keeps_precision_selection_automatic(self) -> None:
        with tempfile.TemporaryDirectory(prefix="image_triage_aiculler_fp16_") as temp_dir:
            model_root = Path(temp_dir) / "models"
            clip_root = model_root / "Clip" / "TinyCLIP-ViT-8M-16-Text-3M-YFCC15M-ONNX"
            (clip_root / "onnx").mkdir(parents=True)
            previous_model_root = os.environ.get("IMAGE_TRIAGE_AICULLER_MODEL_ROOT")
            os.environ["IMAGE_TRIAGE_AICULLER_MODEL_ROOT"] = str(model_root)
            try:
                runtime = default_aiculler_runtime(clip_model_variant="fp16")
            finally:
                if previous_model_root is None:
                    os.environ.pop("IMAGE_TRIAGE_AICULLER_MODEL_ROOT", None)
                else:
                    os.environ["IMAGE_TRIAGE_AICULLER_MODEL_ROOT"] = previous_model_root

        self.assertEqual("fp32", runtime.clip_model_variant)
        self.assertEqual(clip_root / "onnx" / "model.onnx", runtime.clip_vision_model)
        self.assertEqual(clip_root / "onnx" / "model.onnx", runtime.clip_text_model)
        self.assertIsNone(runtime.clip_fallback_vision_model)
        self.assertIsNone(runtime.clip_fallback_text_model)

    def test_command_runs_cli_entrypoint_from_source_tree(self) -> None:
        with tempfile.TemporaryDirectory(prefix="image_triage_aiculler_command_") as temp_dir:
            root = Path(temp_dir)
            cli_path = root / "src" / "aiculler" / "cli.py"
            cli_path.parent.mkdir(parents=True)
            cli_path.write_text("print('cli')\n", encoding="utf-8")
            python_executable = root / "python.exe"
            python_executable.write_text("", encoding="utf-8")
            model_file = root / "model.onnx"
            model_file.write_bytes(b"model")
            tokenizer = root / "tokenizer.json"
            tokenizer.write_text("{}", encoding="utf-8")
            paths = AIWorkflowPaths(
                folder=root / "photos",
                hidden_root=root / ".image_triage_ai",
                artifacts_dir=root / ".image_triage_ai" / "artifacts",
                report_dir=root / ".image_triage_ai" / "ranker_report",
                ranked_export_path=root / ".image_triage_ai" / "ranker_report" / "ranked_clusters_export.csv",
                html_report_path=root / ".image_triage_ai" / "ranker_report" / "ranked_clusters_report.html",
                semantic_export_path=root / ".image_triage_ai" / "ranker_report" / "semantic_classifications.csv",
                semantic_summary_path=root / ".image_triage_ai" / "ranker_report" / "semantic_summary.json",
            )
            runtime = AICullerRuntime(
                root=root,
                python_executable=python_executable,
                cli_entrypoint=cli_path,
                clip_vision_model=model_file,
                clip_text_model=model_file,
                tokenizer=tokenizer,
            )
            task = AICullerRunTask(folder=paths.folder, records=(), runtime=runtime, paths=paths)

            command = task._command(paths.artifacts_dir / "aiculler.sqlite", "rank")

            self.assertEqual(str(python_executable), command[0])
            self.assertEqual(str(cli_path), command[1])
            self.assertNotIn("-m", command)
            self.assertNotIn("aiculler.cli", command)
            self.assertIn("rank", command)

    def test_rank_progress_lines_update_workflow_progress(self) -> None:
        with tempfile.TemporaryDirectory(prefix="image_triage_aiculler_progress_") as temp_dir:
            root = Path(temp_dir)
            paths = AIWorkflowPaths(
                folder=root / "photos",
                hidden_root=root / "photos" / ".image_triage_ai",
                artifacts_dir=root / "photos" / ".image_triage_ai" / "artifacts",
                report_dir=root / "photos" / ".image_triage_ai" / "ranker_report",
                ranked_export_path=root / "photos" / ".image_triage_ai" / "ranker_report" / "ranked_clusters_export.csv",
                html_report_path=root / "photos" / ".image_triage_ai" / "ranker_report" / "ranked_clusters_report.html",
                semantic_export_path=root / "photos" / ".image_triage_ai" / "ranker_report" / "semantic_classifications.csv",
                semantic_summary_path=root / "photos" / ".image_triage_ai" / "ranker_report" / "semantic_summary.json",
            )
            runtime = AICullerRuntime(
                root=root,
                python_executable=root / "python.exe",
                cli_entrypoint=root / "aiculler" / "cli.py",
                clip_vision_model=root / "vision.onnx",
                clip_text_model=root / "text.onnx",
                tokenizer=root / "tokenizer.json",
            )
            task = AICullerRunTask(folder=paths.folder, records=(), runtime=runtime, paths=paths)
            progress: list[tuple[str, str, int, int, str]] = []
            task.signals.progress.connect(lambda folder, message, current, total, eta: progress.append((folder, message, current, total, eta)))

            task._emit_progress_for_line("Ranking images", "[tag-metrics] 25/1404 _DSC2400.JPG")

        self.assertEqual(1, len(progress))
        self.assertEqual("Ranking images: tag metrics", progress[0][1])
        self.assertEqual(25, progress[0][2])
        self.assertEqual(1404, progress[0][3])
        self.assertEqual("_DSC2400.JPG", progress[0][4])

    def test_index_score_can_reuse_prefilter_artifacts_without_rerunning_prefilters(self) -> None:
        with tempfile.TemporaryDirectory(prefix="image_triage_aiculler_reuse_prefilters_") as temp_dir:
            root = Path(temp_dir)
            photo_dir = root / "photos"
            paths = AIWorkflowPaths(
                folder=photo_dir,
                hidden_root=photo_dir / ".image_triage_ai",
                artifacts_dir=photo_dir / ".image_triage_ai" / "artifacts",
                report_dir=photo_dir / ".image_triage_ai" / "ranker_report",
                ranked_export_path=photo_dir / ".image_triage_ai" / "ranker_report" / "ranked_clusters_export.csv",
                html_report_path=photo_dir / ".image_triage_ai" / "ranker_report" / "ranked_clusters_report.html",
                semantic_export_path=photo_dir / ".image_triage_ai" / "ranker_report" / "semantic_classifications.csv",
                semantic_summary_path=photo_dir / ".image_triage_ai" / "ranker_report" / "semantic_summary.json",
            )
            runtime = AICullerRuntime(
                root=root,
                python_executable=root / "python.exe",
                cli_entrypoint=root / "aiculler" / "cli.py",
                clip_vision_model=root / "vision.onnx",
                clip_text_model=root / "text.onnx",
                tokenizer=root / "tokenizer.json",
            )
            task = AICullerRunTask(
                folder=photo_dir,
                records=(),
                runtime=runtime,
                paths=paths,
                run_phash_prefilter=False,
                phash_prefilter_settings=PHashPrefilterSettings(enabled=True),
            )

            self.assertFalse(task._phash_stage_enabled())

    def test_scoped_ingest_prunes_stale_aiculler_database_rows(self) -> None:
        with tempfile.TemporaryDirectory(prefix="image_triage_aiculler_prune_") as temp_dir:
            root = Path(temp_dir)
            photo_dir = root / "photos"
            photo_dir.mkdir()
            keep_path = photo_dir / "keep.jpg"
            stale_path = photo_dir / "stale.jpg"
            include_path = root / "include.txt"
            include_path.write_text(str(keep_path) + "\n", encoding="utf-8")
            db_path = root / "aiculler.sqlite"
            connection = sqlite3.connect(db_path)
            try:
                connection.executescript(
                    """
                    CREATE TABLE images (
                        id INTEGER PRIMARY KEY,
                        source_path TEXT NOT NULL UNIQUE,
                        status TEXT NOT NULL DEFAULT 'ready'
                    );
                    CREATE TABLE embeddings (image_id INTEGER PRIMARY KEY);
                    CREATE TABLE image_categories (image_id INTEGER PRIMARY KEY);
                    CREATE TABLE image_cluster_memberships (image_id INTEGER NOT NULL, cluster_id INTEGER NOT NULL);
                    CREATE TABLE ratings (id INTEGER PRIMARY KEY, image_id INTEGER NOT NULL);
                    CREATE TABLE adapter_scores (model_version TEXT NOT NULL, image_id INTEGER NOT NULL);
                    """
                )
                connection.executemany(
                    "INSERT INTO images (id, source_path, status) VALUES (?, ?, 'ready')",
                    ((1, str(keep_path)), (2, str(stale_path))),
                )
                connection.executemany("INSERT INTO embeddings (image_id) VALUES (?)", ((1,), (2,)))
                connection.executemany("INSERT INTO image_categories (image_id) VALUES (?)", ((1,), (2,)))
                connection.executemany(
                    "INSERT INTO image_cluster_memberships (image_id, cluster_id) VALUES (?, ?)",
                    ((1, 10), (2, 20)),
                )
                connection.executemany("INSERT INTO ratings (image_id) VALUES (?)", ((1,), (2,)))
                connection.executemany(
                    "INSERT INTO adapter_scores (model_version, image_id) VALUES ('adapter-v1', ?)",
                    ((1,), (2,)),
                )
                connection.commit()
            finally:
                connection.close()
            paths = AIWorkflowPaths(
                folder=photo_dir,
                hidden_root=photo_dir / ".image_triage_ai",
                artifacts_dir=photo_dir / ".image_triage_ai" / "artifacts",
                report_dir=photo_dir / ".image_triage_ai" / "ranker_report",
                ranked_export_path=photo_dir / ".image_triage_ai" / "ranker_report" / "ranked_clusters_export.csv",
                html_report_path=photo_dir / ".image_triage_ai" / "ranker_report" / "ranked_clusters_report.html",
                semantic_export_path=photo_dir / ".image_triage_ai" / "ranker_report" / "semantic_classifications.csv",
                semantic_summary_path=photo_dir / ".image_triage_ai" / "ranker_report" / "semantic_summary.json",
            )
            runtime = AICullerRuntime(
                root=root,
                python_executable=root / "python.exe",
                cli_entrypoint=root / "aiculler" / "cli.py",
                clip_vision_model=root / "vision.onnx",
                clip_text_model=root / "text.onnx",
                tokenizer=root / "tokenizer.json",
            )
            task = AICullerRunTask(folder=photo_dir, records=(), runtime=runtime, paths=paths)

            task._prune_aiculler_db_to_include_file(db_path, include_path)

            connection = sqlite3.connect(db_path)
            try:
                for table in (
                    "images",
                    "embeddings",
                    "image_categories",
                    "image_cluster_memberships",
                    "ratings",
                    "adapter_scores",
                ):
                    rows = connection.execute(f"SELECT image_id FROM {table}" if table != "images" else "SELECT id AS image_id FROM images").fetchall()
                    self.assertEqual([(1,)], rows, table)
            finally:
                connection.close()

    def test_adapter_model_summaries_include_score_fit_from_mae(self) -> None:
        with tempfile.TemporaryDirectory(prefix="image_triage_adapter_summary_") as temp_dir:
            db_path = Path(temp_dir) / "aiculler.sqlite"
            connection = sqlite3.connect(db_path)
            try:
                connection.executescript(
                    """
                    CREATE TABLE adapter_models (
                        model_version TEXT PRIMARY KEY,
                        model_type TEXT NOT NULL,
                        training_config_json TEXT NOT NULL,
                        metrics_json TEXT NOT NULL,
                        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                    );
                    CREATE TABLE adapter_scores (
                        model_version TEXT NOT NULL,
                        image_id INTEGER NOT NULL
                    );
                    """
                )
                connection.execute(
                    """
                    INSERT INTO adapter_models (
                        model_version, model_type, training_config_json, metrics_json, created_at
                    )
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        "adapter-v1",
                        "centroid_style_adapter",
                        "{}",
                        '{"train":{"mae":0.20,"count":8},"holdout":{"mae":0.15,"count":2}}',
                        "2026-05-31T12:00:00",
                    ),
                )
                connection.executemany(
                    "INSERT INTO adapter_scores (model_version, image_id) VALUES (?, ?)",
                    (("adapter-v1", 1), ("adapter-v1", 2)),
                )
                connection.commit()
            finally:
                connection.close()

            summaries = list_adapter_model_summaries(db_path)

        self.assertEqual(1, len(summaries))
        self.assertEqual("adapter-v1", summaries[0]["model_version"])
        self.assertEqual(2, summaries[0]["scored_count"])
        self.assertEqual(85.0, summaries[0]["score_fit_percent"])
        self.assertEqual(85.0, summaries[0]["accuracy_percent"])

    def test_delete_adapter_model_removes_model_and_scores(self) -> None:
        with tempfile.TemporaryDirectory(prefix="image_triage_adapter_delete_") as temp_dir:
            db_path = Path(temp_dir) / "aiculler.sqlite"
            connection = sqlite3.connect(db_path)
            try:
                connection.executescript(
                    """
                    CREATE TABLE adapter_models (
                        model_version TEXT PRIMARY KEY,
                        model_type TEXT NOT NULL,
                        training_config_json TEXT NOT NULL,
                        metrics_json TEXT NOT NULL,
                        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                    );
                    CREATE TABLE adapter_scores (
                        model_version TEXT NOT NULL,
                        image_id INTEGER NOT NULL
                    );
                    """
                )
                connection.execute(
                    """
                    INSERT INTO adapter_models (
                        model_version, model_type, training_config_json, metrics_json, created_at
                    )
                    VALUES ('adapter-v1', 'centroid_style_adapter', '{}', '{}', '2026-05-31T12:00:00')
                    """
                )
                connection.executemany(
                    "INSERT INTO adapter_scores (model_version, image_id) VALUES (?, ?)",
                    (("adapter-v1", 1), ("adapter-v1", 2)),
                )
                connection.commit()
            finally:
                connection.close()

            self.assertTrue(delete_adapter_model(db_path, "adapter-v1"))

            connection = sqlite3.connect(db_path)
            try:
                model_count = connection.execute("SELECT COUNT(*) FROM adapter_models").fetchone()[0]
                score_count = connection.execute("SELECT COUNT(*) FROM adapter_scores").fetchone()[0]
            finally:
                connection.close()

        self.assertEqual(0, model_count)
        self.assertEqual(0, score_count)

    def test_compute_and_load_winner_scores_uses_existing_adapter_prior(self) -> None:
        with tempfile.TemporaryDirectory(prefix="image_triage_winner_scores_") as temp_dir:
            db_path = Path(temp_dir) / "aiculler.sqlite"
            connection = sqlite3.connect(db_path)
            try:
                connection.executescript(
                    """
                    CREATE TABLE images (
                        id INTEGER PRIMARY KEY,
                        source_path TEXT NOT NULL,
                        status TEXT NOT NULL DEFAULT 'ready'
                    );
                    CREATE TABLE embeddings (
                        image_id INTEGER PRIMARY KEY,
                        embedding BLOB NOT NULL,
                        dim INTEGER NOT NULL,
                        dtype TEXT NOT NULL
                    );
                    CREATE TABLE ratings (
                        id INTEGER PRIMARY KEY,
                        image_id INTEGER NOT NULL,
                        numeric_score REAL NOT NULL
                    );
                    CREATE TABLE adapter_models (
                        model_version TEXT PRIMARY KEY,
                        model_type TEXT NOT NULL,
                        training_config_json TEXT NOT NULL,
                        metrics_json TEXT NOT NULL,
                        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                    );
                    CREATE TABLE adapter_scores (
                        model_version TEXT NOT NULL,
                        image_id INTEGER NOT NULL,
                        adapter_score REAL NOT NULL
                    );
                    """
                )
                for image_id, adapter_score in ((1, 0.1), (2, 0.9), (3, 0.4)):
                    path = Path(temp_dir) / f"img{image_id}.jpg"
                    vector = np.asarray([float(image_id), 0.0], dtype=np.float32)
                    connection.execute(
                        "INSERT INTO images (id, source_path) VALUES (?, ?)",
                        (image_id, str(path)),
                    )
                    connection.execute(
                        "INSERT INTO embeddings (image_id, embedding, dim, dtype) VALUES (?, ?, ?, ?)",
                        (image_id, vector.tobytes(), int(vector.size), str(vector.dtype)),
                    )
                    connection.execute(
                        "INSERT INTO adapter_scores (model_version, image_id, adapter_score) VALUES ('adapter-v1', ?, ?)",
                        (image_id, adapter_score),
                    )
                connection.execute(
                    """
                    INSERT INTO adapter_models (
                        model_version, model_type, training_config_json, metrics_json, created_at
                    )
                    VALUES ('adapter-v1', 'test', '{}', '{}', '2026-06-01T00:00:00')
                    """
                )
                connection.execute("INSERT INTO ratings (image_id, numeric_score) VALUES (1, 0.0)")
                connection.commit()
            finally:
                connection.close()

            summary = compute_and_store_winner_scores(db_path, model_version="adapter-v1")
            loaded = load_latest_winner_scores(db_path)

        self.assertEqual("adapter-v1", summary["model_version"])
        self.assertEqual(3, summary["scored_count"])
        self.assertEqual({"global": 3}, summary["source_counts"])
        self.assertEqual("adapter-v1", loaded["model_version"])
        self.assertEqual(3, loaded["scored_count"])
        ordered = sorted(
            loaded["scores_by_path"].values(),
            key=lambda row: float(row["blended_score"]),
            reverse=True,
        )
        self.assertEqual([2, 3, 1], [int(row["image_id"]) for row in ordered])

    def test_gui_export_interleaves_groups_and_penalizes_duplicate_frames(self) -> None:
        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        try:
            connection.execute(
                """
                CREATE TABLE rows (
                    id INTEGER,
                    source_path TEXT,
                    technical_score REAL,
                    tag_base_score REAL,
                    tag_penalty REAL,
                    tag_flags TEXT,
                    final_score REAL,
                    primary_category TEXT,
                    cluster_id INTEGER,
                    cluster_label TEXT
                )
                """
            )
            connection.executemany(
                """
                INSERT INTO rows (
                    id, source_path, technical_score, tag_base_score, tag_penalty,
                    tag_flags, final_score, primary_category, cluster_id, cluster_label
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    (1, "C:/shoot/_DSC2236.nef", 0.9, None, None, "", 0.99, "portrait", 10, "same pose"),
                    (2, "C:/shoot/_DSC2237.nef", 0.9, None, None, "", 0.98, "portrait", 10, "same pose"),
                    (3, "C:/shoot/_DSC2101.nef", 0.8, None, None, "", 0.80, "landscape", 20, "road"),
                ),
            )
            rows = connection.execute("SELECT * FROM rows").fetchall()
        finally:
            connection.close()

        output = _rows_to_gui_output(rows)

        self.assertEqual(["_DSC2236.nef", "_DSC2101.nef", "_DSC2237.nef"], [row["file_name"] for row in output])
        self.assertEqual(1, output[0]["rank_in_cluster"])
        self.assertEqual(2, output[2]["rank_in_cluster"])
        self.assertAlmostEqual(0.14, output[2]["duplicate_diversity_penalty"])
        self.assertAlmostEqual(0.84, output[2]["final_score"])

    def test_gui_export_does_not_penalize_whole_semantic_cluster(self) -> None:
        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        try:
            connection.execute(
                """
                CREATE TABLE rows (
                    id INTEGER,
                    source_path TEXT,
                    technical_score REAL,
                    tag_base_score REAL,
                    tag_penalty REAL,
                    tag_flags TEXT,
                    final_score REAL,
                    primary_category TEXT,
                    cluster_id INTEGER,
                    cluster_label TEXT
                )
                """
            )
            connection.executemany(
                """
                INSERT INTO rows (
                    id, source_path, technical_score, tag_base_score, tag_penalty,
                    tag_flags, final_score, primary_category, cluster_id, cluster_label
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    (1, "C:/shoot/_DSC2236.nef", 0.9, None, None, "", 0.99, "portrait", 10, "people_portrait_05"),
                    (2, "C:/shoot/_DSC2536.nef", 0.9, None, None, "", 0.98, "portrait", 10, "people_portrait_05"),
                ),
            )
            rows = connection.execute("SELECT * FROM rows").fetchall()
        finally:
            connection.close()

        output = _rows_to_gui_output(rows)

        self.assertEqual(1, output[0]["rank_in_cluster"])
        self.assertEqual(1, output[1]["rank_in_cluster"])
        self.assertEqual(0.0, output[1]["duplicate_diversity_penalty"])

    def test_adapter_review_selection_collapses_phash_and_refills(self) -> None:
        with tempfile.TemporaryDirectory(prefix="aiculler_review_select_") as temp_dir:
            folder = Path(temp_dir)
            db_path = self._build_adapter_review_db(folder, count=280)
            phash_group_by_path = {
                str(folder / f"_DSC{index:04d}.JPG"): "road-burst"
                for index in range(1, 11)
            }
            phash_group_members = {"road-burst": tuple(phash_group_by_path)}

            result = load_adapter_review_candidates(
                db_path,
                max_rows=12,
                top_global_quota=6,
                phash_group_by_path=phash_group_by_path,
                phash_group_members=phash_group_members,
                return_result=True,
            )

        self.assertEqual(12, len(result.candidates))
        selected_stems = {Path(str(row["file_path"])).stem for row in result.candidates}
        self.assertEqual(1, len(selected_stems.intersection({f"_DSC{index:04d}" for index in range(1, 11)})))
        self.assertIn(str(folder / "_DSC0001.JPG"), result.force_propagate_survivors)
        self.assertGreaterEqual(result.diagnostics.collapsed_sibling_count, 9)
        self.assertGreater(result.diagnostics.cap_skip_count, 0)

    def test_adapter_review_selection_treats_labeled_phash_group_as_covered(self) -> None:
        with tempfile.TemporaryDirectory(prefix="aiculler_review_labeled_") as temp_dir:
            folder = Path(temp_dir)
            db_path = self._build_adapter_review_db(folder, count=120)
            phash_group_by_path = {
                str(folder / f"_DSC{index:04d}.JPG"): "covered-road"
                for index in range(1, 8)
            }
            phash_group_members = {"covered-road": tuple(phash_group_by_path)}

            result = load_adapter_review_candidates(
                db_path,
                max_rows=10,
                top_global_quota=5,
                already_labeled={str(folder / "_DSC0003.NEF")},
                phash_group_by_path=phash_group_by_path,
                phash_group_members=phash_group_members,
                return_result=True,
            )

        selected_stems = {Path(str(row["file_path"])).stem for row in result.candidates}
        self.assertFalse(selected_stems.intersection({f"_DSC{index:04d}" for index in range(1, 8)}))
        self.assertGreaterEqual(result.diagnostics.already_labeled_covered_count, 1)

    def test_adapter_review_labeled_units_do_not_spend_next_batch_caps(self) -> None:
        with tempfile.TemporaryDirectory(prefix="aiculler_review_batch_caps_") as temp_dir:
            folder = Path(temp_dir)
            db_path = self._build_adapter_review_db(folder, count=40)
            first_batch = load_adapter_review_candidates(
                db_path,
                max_rows=2,
                top_global_quota=2,
                return_result=True,
            )

            result = load_adapter_review_candidates(
                db_path,
                max_rows=2,
                top_global_quota=2,
                already_labeled={str(row["file_path"]) for row in first_batch.candidates},
                return_result=True,
            )

        self.assertEqual([3, 4], [int(row["rank"]) for row in result.candidates])
        self.assertEqual(2, result.diagnostics.already_labeled_covered_count)

    def test_adapter_review_caps_nearby_capture_sequence_frames(self) -> None:
        with tempfile.TemporaryDirectory(prefix="aiculler_review_sequence_caps_") as temp_dir:
            folder = Path(temp_dir)
            db_path = self._build_adapter_review_db(folder, count=220)

            result = load_adapter_review_candidates(
                db_path,
                max_rows=20,
                top_global_quota=20,
                return_result=True,
            )

        stems = {Path(str(row["file_path"])).stem for row in result.candidates}
        self.assertFalse({"_DSC0001", "_DSC0002"}.issubset(stems))
        self.assertFalse({"_DSC0024", "_DSC0025"}.issubset(stems))
        self.assertIn("sequence_spread", result.diagnostics.grouping_mode)

if __name__ == "__main__":
    unittest.main()
