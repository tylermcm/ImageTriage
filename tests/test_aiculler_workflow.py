from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

from image_triage.ai_workflow import AIWorkflowPaths, AIWorkflowRuntime
from image_triage.aiculler_workflow import (
    AICullerRunTask,
    AICullerRuntime,
    DINOPrefilterRunTask,
    SOURCE_AICULLER_ROOT,
    coerce_clip_model_variant,
    default_aiculler_runtime,
    list_adapter_model_summaries,
    _rows_to_gui_output,
)
from image_triage.dino_prefilter import DINOPrefilterSettings, build_dino_prefilter_paths, write_dino_prefilter_audit
from image_triage.models import ImageRecord


class AICullerWorkflowTests(unittest.TestCase):
    def test_default_runtime_uses_in_repo_cli_culler_source(self) -> None:
        with tempfile.TemporaryDirectory(prefix="image_triage_aiculler_models_") as temp_dir:
            model_root = Path(temp_dir) / "models"
            clip_root = model_root / "Clip" / "clip-vit-large-patch14"
            (clip_root / "onnx").mkdir(parents=True)
            (clip_root / "onnx" / "vision_model_uint8.onnx").write_bytes(b"vision")
            (clip_root / "onnx" / "text_model_uint8.onnx").write_bytes(b"text")
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

    def test_default_model_root_prefers_image_triage_cache(self) -> None:
        with tempfile.TemporaryDirectory(prefix="image_triage_aiculler_cache_") as temp_dir:
            local_appdata = Path(temp_dir) / "local"
            model_root = local_appdata / "image_triage_ai_cache" / "models" / "CLI-Culler"
            clip_root = model_root / "Clip" / "clip-vit-large-patch14"
            (clip_root / "onnx").mkdir(parents=True)
            (clip_root / "onnx" / "vision_model_uint8.onnx").write_bytes(b"vision")
            (clip_root / "onnx" / "text_model_uint8.onnx").write_bytes(b"text")
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

            self.assertEqual((clip_root / "onnx" / "vision_model_uint8.onnx").resolve(), runtime.clip_vision_model)
            self.assertEqual((clip_root / "onnx" / "text_model_uint8.onnx").resolve(), runtime.clip_text_model)
            self.assertEqual((clip_root / "tokenizer.json").resolve(), runtime.tokenizer)

    def test_default_runtime_selects_configured_clip_model_variant(self) -> None:
        with tempfile.TemporaryDirectory(prefix="image_triage_aiculler_variant_") as temp_dir:
            model_root = Path(temp_dir) / "models"
            clip_root = model_root / "Clip" / "clip-vit-large-patch14"
            (clip_root / "onnx").mkdir(parents=True)
            for filename in ("vision_model_q4.onnx", "text_model_q4.onnx"):
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

            self.assertEqual("q4", runtime.clip_model_variant)
            self.assertEqual((clip_root / "onnx" / "vision_model_q4.onnx").resolve(), runtime.clip_vision_model)
            self.assertEqual((clip_root / "onnx" / "text_model_q4.onnx").resolve(), runtime.clip_text_model)
            self.assertEqual("uint8", coerce_clip_model_variant("not-a-model"))

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

    def test_pool_removal_prefilter_writes_include_file_for_aiculler_ingest(self) -> None:
        with tempfile.TemporaryDirectory(prefix="image_triage_aiculler_dino_pool_") as temp_dir:
            root = Path(temp_dir)
            photo_dir = root / "photos"
            good_path = photo_dir / "good.jpg"
            bad_path = photo_dir / "bad.jpg"
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
            dino_paths = build_dino_prefilter_paths(paths)
            write_dino_prefilter_audit(
                dino_paths,
                settings=DINOPrefilterSettings(enabled=True),
                rows=(
                    {
                        "path": str(bad_path),
                        "action": "remove_from_pool",
                        "reason": "technical_trash",
                        "score": 0.99,
                    },
                ),
                scanned_count=2,
                removed_from_pool_count=1,
                reason_counts={"technical_trash": 1},
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
                folder=paths.folder,
                records=(
                    ImageRecord(path=str(good_path), name="good.jpg", size=1, modified_ns=1),
                    ImageRecord(path=str(bad_path), name="bad.jpg", size=1, modified_ns=1),
                ),
                runtime=runtime,
                paths=paths,
                dino_prefilter_settings=DINOPrefilterSettings(enabled=True, mode="pool_removal"),
            )

            include_path = task._write_dino_prefilter_include_file()

            self.assertIsNotNone(include_path)
            assert include_path is not None
            self.assertEqual(str(good_path), include_path.read_text(encoding="utf-8").strip())

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

    def test_dino_prefilter_prepass_runs_base_signal_scripts_and_writes_rows(self) -> None:
        with tempfile.TemporaryDirectory(prefix="image_triage_dino_prepass_") as temp_dir:
            root = Path(temp_dir)
            engine_root = root / "engine"
            scripts_dir = engine_root / "scripts"
            config_dir = engine_root / "configs"
            scripts_dir.mkdir(parents=True)
            config_dir.mkdir(parents=True)
            for filename in ("extract_embeddings.json", "cluster_embeddings.json", "export_ranked_report.json"):
                (config_dir / filename).write_text("{}", encoding="utf-8")
            (scripts_dir / "extract_embeddings.py").write_text(
                "from pathlib import Path\n"
                "import argparse\n"
                "p=argparse.ArgumentParser(); p.add_argument('--output-dir', type=Path); p.add_argument('--config'); p.add_argument('--input-dir'); p.add_argument('--batch-size'); p.add_argument('--model-name'); p.add_argument('--device'); p.add_argument('--num-workers'); a=p.parse_args(); a.output_dir.mkdir(parents=True, exist_ok=True)\n"
                "print('extract ok')\n",
                encoding="utf-8",
            )
            (scripts_dir / "cluster_embeddings.py").write_text(
                "from pathlib import Path\n"
                "import argparse\n"
                "p=argparse.ArgumentParser(); p.add_argument('--output-dir', type=Path); p.add_argument('--config'); p.add_argument('--artifacts-dir'); a=p.parse_args(); a.output_dir.mkdir(parents=True, exist_ok=True)\n"
                "print('cluster ok')\n",
                encoding="utf-8",
            )
            (scripts_dir / "build_culling_signals.py").write_text(
                "from pathlib import Path\n"
                "import argparse, csv\n"
                "p=argparse.ArgumentParser(); p.add_argument('--artifacts-dir'); p.add_argument('--output-dir', type=Path); p.add_argument('--skip-specialists', action='store_true'); a=p.parse_args(); a.output_dir.mkdir(parents=True, exist_ok=True)\n"
                "with (a.output_dir / 'culling_signals.csv').open('w', encoding='utf-8', newline='') as h:\n"
                "    w=csv.DictWriter(h, fieldnames=['file_path','group_size','dino_rank','detail','exposure_status','exposure_score']); w.writeheader(); w.writerow({'file_path': str(a.output_dir / 'tail.jpg'), 'group_size':'4', 'dino_rank':'4', 'detail':'0.9', 'exposure_status':'properly_exposed', 'exposure_score':'1.0'})\n"
                "print('signals ok')\n",
                encoding="utf-8",
            )
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
            dino_runtime = AIWorkflowRuntime(
                engine_root=engine_root,
                python_executable=Path(sys.executable),
                model_name="mock-dino-base",
                checkpoint_path=root / "unused.pt",
                extraction_config_path=config_dir / "extract_embeddings.json",
                clustering_config_path=config_dir / "cluster_embeddings.json",
                report_config_path=config_dir / "export_ranked_report.json",
                batch_size=2,
                num_workers=0,
            )
            task = DINOPrefilterRunTask(
                folder=paths.folder,
                paths=paths,
                dino_prefilter_settings=DINOPrefilterSettings(enabled=True, aggressiveness_percent=85),
                dino_runtime=dino_runtime,
            )
            failures: list[str] = []
            finished: list[tuple[str, str, str]] = []
            task.signals.failed.connect(lambda folder, message: failures.append(message))
            task.signals.finished.connect(lambda folder, artifact_dir, report_path: finished.append((folder, artifact_dir, report_path)))

            task.run()

            dino_paths = build_dino_prefilter_paths(paths)
            self.assertEqual([], failures)
            self.assertEqual(1, len(finished))
            self.assertTrue(dino_paths.report_path.exists())
            self.assertIn("tail.jpg", dino_paths.rows_path.read_text(encoding="utf-8"))

    def test_adapter_model_summaries_include_accuracy_from_failure_rate(self) -> None:
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
        self.assertEqual(85.0, summaries[0]["accuracy_percent"])

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


if __name__ == "__main__":
    unittest.main()
