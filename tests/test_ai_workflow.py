from __future__ import annotations

import os
import sys
import tempfile
import textwrap
import unittest

import pytest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from image_triage.ai_workflow import (
    AIWorkflowRuntime,
    _parse_ai_metric_line,
    _parse_tqdm_progress,
    ai_semantic_artifacts_ready,
    build_ai_workflow_paths,
    default_ai_workflow_runtime,
    reset_hidden_ai_review_cache,
)
from image_triage.models import ImageRecord


class AIWorkflowStreamingTests(unittest.TestCase):
    def test_parse_tqdm_progress_accepts_progress_bar_segment(self) -> None:
        parsed = _parse_tqdm_progress("Extracting embeddings:   6%|6         | 32/513 [00:57<14:23,  1.80s/image]")

        self.assertEqual(("Extracting embeddings", 32, 513, "14:23"), parsed)

    def test_parse_ai_metric_line_accepts_structured_stdout_metric(self) -> None:
        parsed = _parse_ai_metric_line('AI_METRIC {"event":"ai.script.extract.batch","duration_ms":12.5,"batch_index":2}')

        self.assertEqual(
            {"event": "ai.script.extract.batch", "duration_ms": 12.5, "batch_index": 2},
            parsed,
        )

    def test_parse_ai_metric_line_accepts_tqdm_prefixed_metric(self) -> None:
        parsed = _parse_ai_metric_line(
            'Extracting embeddings:  10%|# | 32/320 [00:01<00:09]AI_METRIC {"event":"ai.script.extract.batch","batch_index":1}'
        )

        self.assertEqual({"event": "ai.script.extract.batch", "batch_index": 1}, parsed)

    def test_default_runtime_uses_current_interpreter_without_python_override(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            engine_root = Path(temp_dir) / "engine"
            config_dir = engine_root / "configs"
            checkpoint_path = engine_root / "outputs" / "ranker" / "best_ranker.pt"
            config_dir.mkdir(parents=True)
            checkpoint_path.parent.mkdir(parents=True)
            (config_dir / "extract_embeddings.json").write_text("{}", encoding="utf-8")
            (config_dir / "cluster_embeddings.json").write_text("{}", encoding="utf-8")
            (config_dir / "export_ranked_report.json").write_text("{}", encoding="utf-8")
            checkpoint_path.write_bytes(b"checkpoint")

            env = {
                "AICULLING_ENGINE_ROOT": str(engine_root),
                "AICULLING_PYTHON": "",
                "AICULLING_CHECKPOINT": str(checkpoint_path),
            }
            with patch.dict(os.environ, env, clear=False):
                runtime = default_ai_workflow_runtime()

            self.assertEqual(runtime.python_executable, Path(sys.executable).resolve())

    def test_default_runtime_honors_device_environment_override(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            engine_root = Path(temp_dir) / "engine"
            config_dir = engine_root / "configs"
            checkpoint_path = engine_root / "outputs" / "ranker_run_mlp_100ep" / "best_ranker.pt"
            config_dir.mkdir(parents=True)
            checkpoint_path.parent.mkdir(parents=True)
            (config_dir / "extract_embeddings.json").write_text("{}", encoding="utf-8")
            (config_dir / "cluster_embeddings.json").write_text("{}", encoding="utf-8")
            (config_dir / "export_ranked_report.json").write_text("{}", encoding="utf-8")
            checkpoint_path.write_bytes(b"checkpoint")

            env = {
                "AICULLING_ENGINE_ROOT": str(engine_root),
                "AICULLING_PYTHON": sys.executable,
                "AICULLING_CHECKPOINT": str(checkpoint_path),
                "AICULLING_MODEL_NAME": "mock-model",
                "AICULLING_DEVICE": "CPU",
            }
            with patch.dict(os.environ, env, clear=False):
                runtime = default_ai_workflow_runtime()

            self.assertEqual(runtime.device, "cpu")

    def test_reset_hidden_ai_review_cache_removes_artifacts_and_report_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            folder = Path(temp_dir) / "shots"
            folder.mkdir()
            paths = build_ai_workflow_paths(folder)
            paths.artifacts_dir.mkdir(parents=True)
            paths.report_dir.mkdir(parents=True)
            labels_dir = paths.hidden_root / "labels"
            labels_dir.mkdir(parents=True)
            training_dir = paths.hidden_root / "training"
            training_dir.mkdir(parents=True)
            (paths.artifacts_dir / "embeddings.npy").write_bytes(b"embed")
            (paths.report_dir / "ranked_clusters_export.csv").write_text("id\n", encoding="utf-8")
            (labels_dir / "pairwise.csv").write_text("a,b\n", encoding="utf-8")
            (training_dir / "active_ranker.txt").write_text("run-1\n", encoding="utf-8")

            reset_hidden_ai_review_cache(paths)

            self.assertFalse(paths.artifacts_dir.exists())
            self.assertFalse(paths.report_dir.exists())
            self.assertTrue(labels_dir.exists())
            self.assertTrue(training_dir.exists())

    def test_semantic_artifact_check_requires_export_and_summary(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = build_ai_workflow_paths(Path(temp_dir) / "shots")
            paths.report_dir.mkdir(parents=True)

            self.assertFalse(ai_semantic_artifacts_ready(paths))
            paths.semantic_export_path.write_text("file_path,primary_label\n", encoding="utf-8")
            self.assertFalse(ai_semantic_artifacts_ready(paths))
            paths.semantic_summary_path.write_text("{}", encoding="utf-8")

            self.assertTrue(ai_semantic_artifacts_ready(paths))

    def test_directory_signature_skips_heavy_non_input_directories(self) -> None:
        from image_triage.ai_workflow import _directory_signature

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "configs").mkdir()
            (root / "configs" / "extract.json").write_text("{}", encoding="utf-8")
            for noisy in ("node_modules", "__pycache__", ".git", "build", ".linux_build_venv"):
                (root / noisy).mkdir()
                (root / noisy / "junk.bin").write_bytes(b"x")

            signature = _directory_signature(root)

        paths = {entry["path"] for entry in signature["entries"]}
        self.assertEqual(paths, {"configs/extract.json"})

    def test_directory_signature_is_bounded_and_marks_truncation(self) -> None:
        from image_triage import ai_workflow

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for index in range(12):
                (root / f"file{index:03d}.bin").write_bytes(b"x")
            with patch.object(ai_workflow, "SIGNATURE_MAX_ENTRIES", 5):
                signature = ai_workflow._directory_signature(root)

        self.assertTrue(signature.get("truncated"))
        self.assertEqual(len(signature["entries"]), 5)

    def test_directory_signature_survives_an_unreadable_entry(self) -> None:
        from image_triage.ai_workflow import _directory_signature

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "good.json").write_text("{}", encoding="utf-8")
            original_stat = Path.stat

            def flaky(self, *args, **kwargs):
                if self.name == "locked.bin":
                    raise OSError(1920, "The file cannot be accessed by the system")
                return original_stat(self, *args, **kwargs)

            (root / "locked.bin").write_bytes(b"x")
            with patch.object(Path, "stat", flaky):
                signature = _directory_signature(root)

        paths = {entry["path"] for entry in signature["entries"]}
        self.assertEqual(paths, {"good.json"})


if __name__ == "__main__":
    unittest.main()
