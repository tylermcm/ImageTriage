from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

from image_triage.ai_workflow import AIWorkflowPaths
from image_triage.aiculler_workflow import (
    AICullerRunTask,
    AICullerRuntime,
    SOURCE_AICULLER_ROOT,
    default_aiculler_runtime,
    list_adapter_model_summaries,
    _rows_to_gui_output,
)


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
                )
            }
            os.environ["IMAGE_TRIAGE_AICULLER_MODEL_ROOT"] = str(model_root)
            os.environ["IMAGE_TRIAGE_AICULLER_PYTHON"] = sys.executable
            for name in (
                "IMAGE_TRIAGE_AICULLER_ROOT",
                "IMAGE_TRIAGE_AICULLER_CLI",
                "IMAGE_TRIAGE_AICULLER_TOPIQ",
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
                )
            }
            os.environ["LOCALAPPDATA"] = str(local_appdata)
            os.environ["IMAGE_TRIAGE_AICULLER_PYTHON"] = sys.executable
            for name in (
                "IMAGE_TRIAGE_AICULLER_MODEL_ROOT",
                "IMAGE_TRIAGE_AICULLER_ROOT",
                "IMAGE_TRIAGE_AICULLER_CLI",
                "IMAGE_TRIAGE_AICULLER_TOPIQ",
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
