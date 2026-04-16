from __future__ import annotations

import os
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch

from image_triage.ai_workflow import _run_command_with_live_output, default_ai_workflow_runtime


class AIWorkflowStreamingTests(unittest.TestCase):
    def test_run_command_streams_lines_and_flushes_trailing_partial(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            script_path = Path(temp_dir) / "stream_case.py"
            script_path.write_text(
                textwrap.dedent(
                    """
                    import sys

                    sys.stdout.write("first line\\n")
                    sys.stdout.write("second line\\n")
                    sys.stdout.write("final tail")
                    sys.stdout.flush()
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            emitted: list[str] = []
            completed = _run_command_with_live_output(
                [sys.executable, str(script_path)],
                cwd=Path(temp_dir),
                progress_callback=emitted.append,
            )

        self.assertEqual(completed.returncode, 0)
        self.assertEqual(emitted, ["first line", "second line", "final tail"])
        self.assertIn("final tail", completed.stdout)

    def test_run_command_merges_partial_line_chunks_before_emitting(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            script_path = Path(temp_dir) / "partial_case.py"
            script_path.write_text(
                textwrap.dedent(
                    """
                    import sys

                    sys.stdout.write("par")
                    sys.stdout.flush()
                    sys.stdout.write("tial\\n")
                    sys.stdout.flush()
                    sys.stdout.write("tail")
                    sys.stdout.flush()
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            emitted: list[str] = []
            completed = _run_command_with_live_output(
                [sys.executable, str(script_path)],
                cwd=Path(temp_dir),
                progress_callback=emitted.append,
            )

        self.assertEqual(completed.returncode, 0)
        self.assertEqual(emitted, ["partial", "tail"])
        self.assertTrue(completed.stdout.endswith("tail"))

    def test_default_runtime_prefers_explicit_environment_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            engine_root = Path(temp_dir) / "engine"
            config_dir = engine_root / "configs"
            checkpoint_path = engine_root / "outputs" / "ranker" / "best_ranker.pt"
            model_dir = Path(temp_dir) / "model"
            config_dir.mkdir(parents=True)
            checkpoint_path.parent.mkdir(parents=True)
            model_dir.mkdir(parents=True)
            (config_dir / "extract_embeddings.json").write_text("{}", encoding="utf-8")
            (config_dir / "cluster_embeddings.json").write_text("{}", encoding="utf-8")
            (config_dir / "export_ranked_report.json").write_text("{}", encoding="utf-8")
            checkpoint_path.write_bytes(b"checkpoint")
            (model_dir / "config.json").write_text('{"model_type":"dinov2"}', encoding="utf-8")
            (model_dir / "model.safetensors").write_bytes(b"weights")

            env = {
                "AICULLING_ENGINE_ROOT": str(engine_root),
                "AICULLING_PYTHON": sys.executable,
                "AICULLING_CHECKPOINT": str(checkpoint_path),
                "AICULLING_MODEL_DIR": str(model_dir),
                "AICULLING_LOCAL_STAGE_MODE": "always",
                "AICULLING_LOCAL_STAGE_ROOT": str(Path(temp_dir) / "scratch"),
            }
            with patch.dict(os.environ, env, clear=False):
                runtime = default_ai_workflow_runtime()

            self.assertEqual(runtime.engine_root, engine_root.resolve())
            self.assertEqual(runtime.python_executable, Path(sys.executable).resolve())
            self.assertEqual(runtime.model_name, str(model_dir.resolve()))
            self.assertIsNotNone(runtime.model_installation)
            self.assertTrue(runtime.model_installation.is_installed)
            self.assertEqual(runtime.checkpoint_path, checkpoint_path.resolve())
            self.assertEqual(runtime.local_stage_mode, "always")

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


if __name__ == "__main__":
    unittest.main()
