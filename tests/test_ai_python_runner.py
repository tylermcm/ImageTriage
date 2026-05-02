from __future__ import annotations

import importlib.util
import os
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch


RUNNER_PATH = Path(__file__).resolve().parents[1] / "packaging" / "ai_python_runner.py"


def _load_runner_module():
    spec = importlib.util.spec_from_file_location("test_ai_python_runner_module", RUNNER_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class AIPythonRunnerTests(unittest.TestCase):
    def test_cached_runtime_site_packages_take_precedence_over_bundled_fallback(self) -> None:
        runner = _load_runner_module()
        fallback_site_packages = Path(r"C:\fallback\ai_site_packages")
        cached_site_packages = Path(r"C:\runtime\site-packages")
        captured_path: list[str] = []

        with (
            patch.object(runner, "_candidate_runtime_roots", return_value=[Path(r"C:\fallback")]),
            patch.object(runner, "_cached_runtime_site_packages", return_value=(cached_site_packages,)),
            patch.object(Path, "exists", autospec=True, side_effect=lambda self: self in {fallback_site_packages, cached_site_packages}),
            patch.object(runner.sys, "path", []),
        ):
            runner._prepend_ai_site_packages()
            captured_path = list(runner.sys.path)

        self.assertEqual(
            captured_path,
            [str(cached_site_packages), str(fallback_site_packages)],
        )

    def test_cached_runtime_binary_paths_take_precedence_over_bundled_fallback(self) -> None:
        runner = _load_runner_module()
        fallback_root = Path(r"C:\fallback")
        fallback_torch_lib = fallback_root / "ai_site_packages" / "torch" / "lib"
        cached_site_packages = Path(r"C:\runtime\site-packages")
        cached_torch_lib = cached_site_packages / "torch" / "lib"
        captured_path_entries: list[str] = []

        def fake_exists(self: Path) -> bool:
            return self in {
                fallback_root / "lib",
                fallback_root / "ai_python_dlls",
                fallback_root / "ai_site_packages",
                fallback_torch_lib,
                cached_site_packages,
                cached_torch_lib,
            }

        with (
            patch.object(runner, "_candidate_runtime_roots", return_value=[fallback_root]),
            patch.object(runner, "_cached_runtime_site_packages", return_value=(cached_site_packages,)),
            patch.object(Path, "exists", autospec=True, side_effect=fake_exists),
            patch.object(Path, "glob", autospec=True, return_value=()),
            patch.dict(os.environ, {"PATH": ""}, clear=True),
        ):
            runner._prepend_ai_binary_modules()
            captured_path_entries = os.environ["PATH"].split(os.pathsep)
        self.assertEqual(captured_path_entries[0], str(cached_torch_lib))
        self.assertIn(str(fallback_torch_lib), captured_path_entries)

    def test_handle_forked_child_process_uses_stdlib_freeze_support(self) -> None:
        runner = _load_runner_module()
        recorded_calls: list[str] = []

        with (
            patch.object(runner, "_configure_runtime_environment", side_effect=lambda script_path=None: recorded_calls.append("configure")),
            patch.object(runner.sys, "argv", ["ai_python_runner.exe", "--multiprocessing-fork", "pipe_handle=123", "parent_pid=456"]),
            patch("multiprocessing.spawn.freeze_support", side_effect=lambda: recorded_calls.append("freeze")),
        ):
            result = runner._handle_forked_child_process()

        self.assertEqual(result, 0)
        self.assertEqual(recorded_calls, ["configure", "freeze"])

    def test_handle_forked_child_process_uses_loky_main_for_numeric_pipe_handle(self) -> None:
        runner = _load_runner_module()
        recorded_calls: list[tuple[int, int | None]] = []
        joblib_module = types.ModuleType("joblib")
        externals_module = types.ModuleType("joblib.externals")
        loky_module = types.ModuleType("joblib.externals.loky")
        backend_module = types.ModuleType("joblib.externals.loky.backend")
        popen_module = types.ModuleType("joblib.externals.loky.backend.popen_loky_win32")

        def fake_main(*, pipe_handle: int, parent_pid: int | None = None) -> None:
            recorded_calls.append((pipe_handle, parent_pid))

        popen_module.main = fake_main  # type: ignore[attr-defined]
        module_map = {
            "joblib": joblib_module,
            "joblib.externals": externals_module,
            "joblib.externals.loky": loky_module,
            "joblib.externals.loky.backend": backend_module,
            "joblib.externals.loky.backend.popen_loky_win32": popen_module,
        }

        with (
            patch.object(runner, "_configure_runtime_environment"),
            patch.object(runner.sys, "argv", ["ai_python_runner.exe", "--multiprocessing-fork", "321", "parent_pid=654"]),
            patch.dict(sys.modules, module_map, clear=False),
        ):
            result = runner._handle_forked_child_process()

        self.assertEqual(result, 0)
        self.assertEqual(recorded_calls, [(321, 654)])


if __name__ == "__main__":
    unittest.main()
