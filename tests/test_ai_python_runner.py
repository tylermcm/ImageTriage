from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
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
    def test_configure_runtime_adds_packaged_aiculler_root_for_cli_script(self) -> None:
        runner = _load_runner_module()
        with tempfile.TemporaryDirectory(prefix="image_triage_runner_aiculler_") as temp_dir:
            root = Path(temp_dir)
            cli_path = root / "aiculler" / "cli.py"
            cli_path.parent.mkdir(parents=True)
            cli_path.write_text("print('cli')\n", encoding="utf-8")
            (cli_path.parent / "__init__.py").write_text("", encoding="utf-8")
            captured_path: list[str] = []

            with (
                patch.object(runner, "_prepend_ai_stdlib"),
                patch.object(runner, "_prepend_ai_binary_modules"),
                patch.object(runner, "_prepend_ai_site_packages"),
                patch.object(runner, "_prepend_engine_root"),
                patch.object(runner.sys, "executable", str(root / "ai_python_runner.exe")),
                patch.object(runner.sys, "path", []),
                patch.object(runner.Path, "cwd", return_value=root),
            ):
                runner._configure_runtime_environment(cli_path)
                captured_path = list(runner.sys.path)

            self.assertEqual(str(root), captured_path[0])

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



class ManagedRuntimeHardFailTests(unittest.TestCase):
    """A managed-runtime failure must abort, not fall through to other packages."""

    def test_a_resolver_failure_raises_instead_of_returning_empty(self) -> None:
        runner = _load_runner_module()
        # The resolver is imported inside the function, so patch it at source.
        with patch(
            "image_triage.ai_runtime_packages.resolve_ai_runtime_site_packages",
            side_effect=RuntimeError("metadata unreadable"),
        ):
            with patch.dict(
                os.environ, {"IMAGE_TRIAGE_AI_PROFILE": "gpu-abc"}, clear=False
            ):
                with self.assertRaises(runner.ManagedRuntimeError):
                    runner._cached_runtime_site_packages(device="cuda")

    def test_an_absent_profile_aborts_when_the_parent_pinned_one(self) -> None:
        runner = _load_runner_module()
        with patch.object(runner, "_managed_runtime_required", return_value=True), patch(
            "image_triage.ai_runtime_packages.resolve_ai_runtime_site_packages",
            return_value=(),
        ):
            with self.assertRaises(runner.ManagedRuntimeError):
                runner._cached_runtime_site_packages(device="cpu")

    def test_a_bare_invocation_stays_permissive(self) -> None:
        # A script that needs no third-party AI package may still run.
        runner = _load_runner_module()
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("IMAGE_TRIAGE_AI_PROFILE", None)
            with patch(
                "image_triage.ai_runtime_packages.resolve_ai_runtime_site_packages",
                return_value=(),
            ):
                self.assertEqual(runner._cached_runtime_site_packages(device="cpu"), ())

    def test_bundled_packages_may_not_shadow_the_managed_runtime(self) -> None:
        runner = _load_runner_module()
        original = list(sys.path)
        try:
            managed = Path("C:/managed/site-packages")
            bundled = Path("C:/app/ai_site_packages")
            # Bundled ahead of managed is the ordering that must be rejected.
            sys.path.insert(0, str(managed))
            sys.path.insert(0, str(bundled))
            with self.assertRaises(RuntimeError):
                runner._assert_runtime_precedence([managed], [bundled])
        finally:
            sys.path[:] = original

    def test_the_parent_pinned_device_wins_over_the_command_line(self) -> None:
        runner = _load_runner_module()
        original_argv = list(sys.argv)
        try:
            sys.argv = ["runner", "script.py", "--device", "cpu"]
            with patch.dict(
                os.environ, {"IMAGE_TRIAGE_AI_SELECTED_DEVICE": "cuda:1"}, clear=False
            ):
                self.assertEqual(runner._requested_device_from_argv(), "cuda:1")
        finally:
            sys.argv = original_argv


if __name__ == "__main__":
    unittest.main()
