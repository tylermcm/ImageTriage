from __future__ import annotations

import os
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch

from image_triage.ai_workflow import (
    AIWorkflowRuntime,
    _build_stage_failure_message,
    _resolve_stage_command,
    _run_command_with_live_output,
    build_ai_workflow_paths,
    default_ai_workflow_runtime,
    load_supported_extensions,
    reset_hidden_ai_review_cache,
)


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

    def test_run_command_can_tee_raw_output_chunks(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            script_path = Path(temp_dir) / "tee_case.py"
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

            chunks: list[str] = []
            completed = _run_command_with_live_output(
                [sys.executable, str(script_path)],
                cwd=Path(temp_dir),
                output_callback=chunks.append,
            )

        self.assertEqual(completed.returncode, 0)
        self.assertEqual("".join(chunks), completed.stdout)

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

    def test_default_runtime_prefers_generic_bundled_checkpoint_location(self) -> None:
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
                "AICULLING_CHECKPOINT": "",
                "AICULLING_CHECKPOINT_URL": "",
                "AICULLING_MODEL_NAME": "mock-model",
            }
            with patch.dict(os.environ, env, clear=False):
                runtime = default_ai_workflow_runtime()

            self.assertEqual(runtime.checkpoint_path, checkpoint_path.resolve())
            self.assertEqual(runtime.device, "auto")

    def test_default_runtime_honors_batch_size_and_worker_environment_overrides(self) -> None:
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
                "AICULLING_BATCH_SIZE": "48",
                "AICULLING_NUM_WORKERS": "7",
            }
            with patch.dict(os.environ, env, clear=False):
                runtime = default_ai_workflow_runtime()

            self.assertEqual(runtime.batch_size, 48)
            self.assertEqual(runtime.num_workers, 7)

    def test_load_supported_extensions_fallback_includes_raw_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "extract.json"
            config_path.write_text("{}", encoding="utf-8")

            extensions = load_supported_extensions(config_path)

        self.assertIn(".nef", extensions)
        self.assertIn(".cr3", extensions)
        self.assertIn(".jpg", extensions)

    def test_repo_extract_config_includes_raw_extensions(self) -> None:
        config_path = (
            Path(__file__).resolve().parents[1]
            / "AICullingPipeline"
            / "configs"
            / "extract_embeddings.json"
        )

        extensions = load_supported_extensions(config_path)

        self.assertIn(".nef", extensions)
        self.assertIn(".dng", extensions)
        self.assertIn(".raf", extensions)

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

    def test_default_runtime_falls_back_to_generic_legacy_checkpoint_location(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            engine_root = Path(temp_dir) / "engine"
            config_dir = engine_root / "configs"
            checkpoint_path = (
                engine_root
                / "outputs"
                / "legacy_default"
                / "ranker_run_mlp_100ep"
                / "best_ranker.pt"
            )
            config_dir.mkdir(parents=True)
            checkpoint_path.parent.mkdir(parents=True)
            (config_dir / "extract_embeddings.json").write_text("{}", encoding="utf-8")
            (config_dir / "cluster_embeddings.json").write_text("{}", encoding="utf-8")
            (config_dir / "export_ranked_report.json").write_text("{}", encoding="utf-8")
            checkpoint_path.write_bytes(b"checkpoint")

            env = {
                "AICULLING_ENGINE_ROOT": str(engine_root),
                "AICULLING_PYTHON": sys.executable,
                "AICULLING_CHECKPOINT": "",
                "AICULLING_CHECKPOINT_URL": "",
                "AICULLING_MODEL_NAME": "mock-model",
            }
            with patch.dict(os.environ, env, clear=False):
                runtime = default_ai_workflow_runtime()

            self.assertEqual(runtime.checkpoint_path, checkpoint_path.resolve())

    def test_resolve_stage_command_uses_repo_runner_script_when_present(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace_root = Path(temp_dir)
            engine_root = workspace_root / "AICullingPipeline"
            scripts_dir = engine_root / "scripts"
            scripts_dir.mkdir(parents=True)
            script_path = scripts_dir / "extract_embeddings.py"
            script_path.write_text("print('ok')\n", encoding="utf-8")
            runner_script = workspace_root / "packaging" / "ai_python_runner.py"
            runner_script.parent.mkdir(parents=True)
            runner_script.write_text("print('runner')\n", encoding="utf-8")
            runtime = AIWorkflowRuntime(
                engine_root=engine_root,
                python_executable=Path(sys.executable).resolve(),
                model_name="model",
                checkpoint_path=workspace_root / "checkpoint.pt",
                extraction_config_path=workspace_root / "extract.json",
                clustering_config_path=workspace_root / "cluster.json",
                report_config_path=workspace_root / "report.json",
            )

            command = _resolve_stage_command(
                runtime,
                script_relative_path="scripts/extract_embeddings.py",
                stage_args=["--batch-size", "8"],
            )

        self.assertEqual(command[0], str(Path(sys.executable).resolve()))
        self.assertEqual(command[1], str(runner_script.resolve()))
        self.assertEqual(command[2], str(script_path.resolve()))
        self.assertEqual(command[3:], ["--batch-size", "8"])

    def test_repo_runner_loads_dependencies_from_build_assets(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace_root = Path(temp_dir)
            runner_script = Path(__file__).resolve().parents[1] / "packaging" / "ai_python_runner.py"
            engine_root = workspace_root / "AICullingPipeline"
            scripts_dir = engine_root / "scripts"
            scripts_dir.mkdir(parents=True)
            script_path = scripts_dir / "import_case.py"
            script_path.write_text(
                "import ai_only_dependency\nprint(ai_only_dependency.VALUE)\n",
                encoding="utf-8",
            )
            staged_site_packages = workspace_root / "build_assets" / "ai_site_packages"
            staged_site_packages.mkdir(parents=True)
            (staged_site_packages / "ai_only_dependency.py").write_text("VALUE = 7\n", encoding="utf-8")

            completed = _run_command_with_live_output(
                [sys.executable, str(runner_script), str(script_path)],
                cwd=engine_root,
            )

        self.assertEqual(completed.returncode, 0)
        self.assertIn("7", completed.stdout)

    def test_build_stage_failure_message_includes_tail_and_log_path(self) -> None:
        runtime = AIWorkflowRuntime(
            engine_root=Path.cwd(),
            python_executable=Path(sys.executable).resolve(),
            model_name="mock-model",
            checkpoint_path=Path.cwd() / "checkpoint.pt",
            extraction_config_path=Path.cwd() / "extract.json",
            clustering_config_path=Path.cwd() / "cluster.json",
            report_config_path=Path.cwd() / "report.json",
        )
        output_text = "\n".join(f"line {index}" for index in range(100))
        message = _build_stage_failure_message(
            runtime=runtime,
            stage_message="Extracting embeddings",
            stderr="",
            stdout=output_text,
            log_path=Path("C:/temp/latest_ai_culling.log"),
        )

        self.assertIn("Showing last 80 output lines:", message)
        self.assertNotIn("line 0", message)
        self.assertIn("line 99", message)
        self.assertIn("AI run log: C:\\temp\\latest_ai_culling.log", message)


if __name__ == "__main__":
    unittest.main()
