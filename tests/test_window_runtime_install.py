from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from image_triage.window import AIRuntimeInstallTask


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


if __name__ == "__main__":
    unittest.main()
