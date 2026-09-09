from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from image_triage.window import AIRuntimeInstallTask, AISetupSelection, MainWindow


class _FakeStdout:
    def __init__(self, lines: list[str]) -> None:
        self._lines = iter(lines)

    def __iter__(self):
        return self

    def __next__(self) -> str:
        return next(self._lines)

    def close(self) -> None:
        return None

    def __enter__(self) -> "_FakeStdout":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False


class _FakeProcess:
    def __init__(self, lines: list[str]) -> None:
        self.stdout = _FakeStdout(lines)

    def wait(self) -> int:
        return 0


class AIRuntimeInstallTaskTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "Windows-specific console hiding")
    def test_runtime_install_task_hides_console_window(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace_root = Path(temp_dir)
            install_root = workspace_root / "runtime"
            command = ["python", "installer.py", "install", "--variant", "gpu"]
            task = AIRuntimeInstallTask(
                command=command,
                cwd=workspace_root,
                install_root=install_root,
                variant_choice="gpu",
            )
            started: list[tuple[str, str]] = []
            progress: list[str] = []
            finished: list[tuple[str, str]] = []
            failed: list[str] = []
            task.signals.started.connect(lambda root, variant: started.append((root, variant)))
            task.signals.progress.connect(progress.append)
            task.signals.finished.connect(lambda root, variant: finished.append((root, variant)))
            task.signals.failed.connect(failed.append)

            captured_kwargs: dict[str, object] = {}

            def fake_popen(*args, **kwargs):
                _ = args
                captured_kwargs.update(kwargs)
                return _FakeProcess(["Installing packages\n"])

            with patch("image_triage.window.subprocess.Popen", side_effect=fake_popen):
                task.run()

        self.assertEqual(started, [(str(install_root), "gpu")])
        self.assertEqual(progress, ["Installing packages"])
        self.assertEqual(finished, [(str(install_root), "gpu")])
        self.assertEqual(failed, [])
        self.assertIn("creationflags", captured_kwargs)
        self.assertIn("startupinfo", captured_kwargs)

    def test_combined_ai_setup_installs_editor_mask_runtime_dependencies(self) -> None:
        calls: list[tuple[tuple, dict]] = []

        class _SetupStub:
            def _start_ai_runtime_install(self, *args, **kwargs) -> None:
                calls.append((args, kwargs))

        selection = AISetupSelection(
            install_runtime=True,
            runtime_variant="gpu",
            include_dino_runtime=True,
            download_aiculler_clip_model=True,
            download_aiculler_topiq_model=True,
            download_aiculler_face_model=True,
            download_dino_model=True,
            download_semantic_model=False,
        )

        started = MainWindow._start_ai_setup_selection(
            _SetupStub(),
            selection,
            force_runtime=False,
        )

        self.assertTrue(started)
        self.assertEqual(1, len(calls))
        _args, kwargs = calls[0]
        self.assertTrue(kwargs["include_dino"])
        self.assertFalse(kwargs["download_dino_model_after"])
        self.assertTrue(kwargs["download_aiculler_clip_after"])
        self.assertTrue(kwargs["download_aiculler_topiq_after"])
        self.assertTrue(kwargs["download_aiculler_face_after"])

    def test_runtime_progress_hides_package_details(self) -> None:
        messages: list[str | None] = []

        class _ProgressStub:
            def _set_ai_setup_busy(self, message: str | None) -> None:
                messages.append(message)

        MainWindow._handle_ai_runtime_install_progress(
            _ProgressStub(),
            "Downloading torch-2.8.0-cp313-win_amd64.whl",
        )

        self.assertEqual(messages, ["Installing AI runtime..."])

    def test_model_progress_hides_file_details(self) -> None:
        messages: list[str | None] = []

        class _ProgressStub:
            def _set_ai_setup_busy(self, message: str | None) -> None:
                messages.append(message)

        MainWindow._handle_ai_model_download_progress(
            _ProgressStub(),
            "model.safetensors",
            50,
            100,
        )

        self.assertEqual(messages, ["Downloading AI culling models..."])


if __name__ == "__main__":
    unittest.main()
