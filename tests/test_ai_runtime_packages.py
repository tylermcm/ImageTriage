from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from image_triage.ai_runtime_packages import (
    AI_RUNTIME_CPU_VARIANT,
    AI_RUNTIME_GPU_VARIANT,
    AI_RUNTIME_REQUIRED_MODULE_NAMES,
    build_ai_runtime_pip_install_args,
    directory_size_bytes,
    estimate_ai_runtime_download_size_mb,
    estimate_ai_runtime_installed_size_mb,
    install_ai_runtime,
    load_ai_runtime_installation_status,
    resolve_ai_runtime_site_packages,
)


def _materialize_runtime_modules(target_dir: Path) -> None:
    for module_name in AI_RUNTIME_REQUIRED_MODULE_NAMES:
        package_dir = target_dir / module_name
        package_dir.mkdir(parents=True, exist_ok=True)
        (package_dir / "__init__.py").write_text("", encoding="utf-8")


class AIRuntimePackageTests(unittest.TestCase):
    def test_install_ai_runtime_can_stage_both_profiles(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            install_root = Path(temp_dir) / "runtime"
            recorded_calls: list[list[str]] = []

            def fake_pip_runner(args: list[str], cwd: Path) -> int:
                self.assertEqual(cwd, install_root)
                recorded_calls.append(args)
                target_dir = Path(args[args.index("--target") + 1])
                _materialize_runtime_modules(target_dir)
                return 0

            status = install_ai_runtime(
                "both",
                install_root=install_root,
                pip_runner=fake_pip_runner,
            )

            self.assertEqual(status.installed_variants, (AI_RUNTIME_CPU_VARIANT, AI_RUNTIME_GPU_VARIANT))
            self.assertEqual(status.preferred_variant, AI_RUNTIME_GPU_VARIANT)
            self.assertEqual(len(recorded_calls), 2)
            self.assertIn("https://download.pytorch.org/whl/cpu", recorded_calls[0])
            self.assertIn("https://download.pytorch.org/whl/cu128", recorded_calls[1])
            self.assertEqual(
                resolve_ai_runtime_site_packages(device="auto", install_root=install_root),
                (status.directories.site_packages_dir(AI_RUNTIME_GPU_VARIANT),),
            )
            self.assertEqual(
                resolve_ai_runtime_site_packages(device="cpu", install_root=install_root),
                (status.directories.site_packages_dir(AI_RUNTIME_CPU_VARIANT),),
            )

    def test_build_ai_runtime_pip_install_args_uses_expected_torch_index(self) -> None:
        args = build_ai_runtime_pip_install_args(
            variant=AI_RUNTIME_CPU_VARIANT,
            target_dir=Path("C:/temp/runtime"),
            force=True,
        )
        self.assertIn("--force-reinstall", args)
        self.assertIn("https://download.pytorch.org/whl/cpu", args)
        self.assertIn("--progress-bar", args)
        self.assertIn("raw", args)

    def test_runtime_size_estimates_are_available_for_setup_copy(self) -> None:
        self.assertGreater(estimate_ai_runtime_download_size_mb(AI_RUNTIME_GPU_VARIANT), 3000)
        self.assertGreater(estimate_ai_runtime_installed_size_mb(AI_RUNTIME_GPU_VARIANT), 5000)

    def test_runtime_install_root_uses_local_appdata_without_home_lookup(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env = {"LOCALAPPDATA": temp_dir}
            with patch.dict(os.environ, env, clear=False):
                with patch("image_triage.ai_runtime_packages.Path.home", side_effect=RuntimeError("no home")):
                    status = load_ai_runtime_installation_status()

        self.assertTrue(str(status.directories.root).startswith(temp_dir))

    def test_directory_size_bytes_sums_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "a").write_bytes(b"123")
            nested = root / "nested"
            nested.mkdir()
            (nested / "b").write_bytes(b"45")

            self.assertEqual(directory_size_bytes(root), 5)

    def test_load_status_without_installation_reports_empty(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            status = load_ai_runtime_installation_status(install_root=Path(temp_dir) / "runtime")
            self.assertFalse(status.is_installed)
            self.assertEqual(status.installed_variants, ())
            self.assertEqual(resolve_ai_runtime_site_packages(install_root=Path(temp_dir) / "runtime"), ())


if __name__ == "__main__":
    unittest.main()
