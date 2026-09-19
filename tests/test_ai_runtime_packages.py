from __future__ import annotations

import os
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from image_triage import ai_runtime_packages
from image_triage.ai_runtime_packages import (
    AI_RUNTIME_CPU_VARIANT,
    AI_RUNTIME_GPU_VARIANT,
    AI_RUNTIME_BASE_REQUIRED_MODULE_NAMES,
    AI_RUNTIME_BASE_REQUIRED_FILES,
    AI_RUNTIME_DINO_REQUIRED_FILES,
    AI_RUNTIME_REQUIRED_MODULE_NAMES,
    default_ai_runtime_install_root,
    build_ai_runtime_pip_install_args,
    directory_size_bytes,
    estimate_ai_runtime_download_size_mb,
    estimate_ai_runtime_installed_size_mb,
    install_ai_runtime,
    load_ai_runtime_installation_status,
    resolve_ai_runtime_site_packages,
    _ai_runtime_install_lock,
    _validate_distribution_records,
    _default_pip_runner,
    _python_runtime_tag,
)


def _materialize_runtime_modules(target_dir: Path) -> None:
    for module_name in AI_RUNTIME_REQUIRED_MODULE_NAMES:
        package_dir = target_dir / module_name
        package_dir.mkdir(parents=True, exist_ok=True)
        (package_dir / "__init__.py").write_text("", encoding="utf-8")
    torch_lib = target_dir / "torch" / "lib"
    torch_lib.mkdir(parents=True, exist_ok=True)
    (torch_lib / "torch_cuda.dll").write_text("", encoding="utf-8")
    (target_dir / "torch" / "version.py").write_text(
        "__version__ = '2.9.0+cu128'\ncuda = '12.8'\n",
        encoding="utf-8",
    )
    torch_dist_info = target_dir / "torch-2.9.0+cu128.dist-info"
    torch_dist_info.mkdir(parents=True, exist_ok=True)
    (torch_dist_info / "METADATA").write_text(
        "Name: torch\nVersion: 2.9.0+cu128\n",
        encoding="utf-8",
    )
    dist_info = target_dir / "transformers-5.5.4.dist-info"
    dist_info.mkdir(parents=True, exist_ok=True)
    (dist_info / "METADATA").write_text(
        "Name: transformers\nVersion: 5.5.4\n",
        encoding="utf-8",
    )
    ort_dist_info = target_dir / "onnxruntime_gpu-1.26.0.dist-info"
    ort_dist_info.mkdir(parents=True, exist_ok=True)
    (ort_dist_info / "METADATA").write_text(
        "Name: onnxruntime-gpu\nVersion: 1.26.0\n",
        encoding="utf-8",
    )
    for relative_path, _label in AI_RUNTIME_BASE_REQUIRED_FILES + AI_RUNTIME_DINO_REQUIRED_FILES:
        path = target_dir / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")


def _materialize_base_runtime_modules(target_dir: Path) -> None:
    for module_name in AI_RUNTIME_BASE_REQUIRED_MODULE_NAMES:
        package_dir = target_dir / module_name
        package_dir.mkdir(parents=True, exist_ok=True)
        (package_dir / "__init__.py").write_text("", encoding="utf-8")
    for relative_path, _label in AI_RUNTIME_BASE_REQUIRED_FILES:
        path = target_dir / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")


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
            self.assertEqual(status.onnx_gpu_installed_variants, (AI_RUNTIME_GPU_VARIANT,))
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
        self.assertIn("--ignore-installed", args)
        self.assertIn("--no-compile", args)
        self.assertNotIn("--upgrade", args)
        self.assertNotIn("--force-reinstall", args)
        self.assertIn("https://download.pytorch.org/whl/cpu", args)
        self.assertIn("--progress-bar", args)
        self.assertIn("raw", args)

    def test_install_uses_the_pinned_lock_when_this_build_ships_one(self) -> None:
        # A lock pins every transitive distribution and its wheel hash, so pip
        # must not resolve anything of its own.
        for variant in (AI_RUNTIME_CPU_VARIANT, AI_RUNTIME_GPU_VARIANT):
            with self.subTest(variant=variant):
                lock = ai_runtime_packages.resolve_lock_file(variant)
                if lock is None:
                    self.skipTest(f"no lock ships for {variant} on this interpreter")
                args = build_ai_runtime_pip_install_args(
                    variant=variant, target_dir=Path("C:/temp/runtime")
                )
                self.assertIn("--require-hashes", args)
                self.assertIn("--no-deps", args)
                self.assertIn(str(lock), args)

    def test_requirements_are_pinned_exactly_when_no_lock_ships(self) -> None:
        # The fallback path must still be reproducible: no open floors.
        with patch("image_triage.ai_runtime_packages.resolve_lock_file", return_value=None):
            cpu = build_ai_runtime_pip_install_args(
                variant=AI_RUNTIME_CPU_VARIANT, target_dir=Path("C:/temp/runtime")
            )
            gpu = build_ai_runtime_pip_install_args(
                variant=AI_RUNTIME_GPU_VARIANT, target_dir=Path("C:/temp/runtime")
            )

        self.assertIn("transformers==5.14.1", cpu)
        self.assertIn("torch==2.9.0", cpu)
        self.assertIn("torchvision==0.24.0", cpu)
        self.assertIn("onnxruntime==1.27.0", cpu)
        self.assertIn("insightface==1.0.1", cpu)
        self.assertNotIn("onnxruntime-gpu==1.26.0", cpu)

        self.assertIn("torch==2.9.0+cu128", gpu)
        self.assertIn("onnxruntime-gpu==1.26.0", gpu)
        # The CPU wheel installs the same package directory as the GPU wheel.
        self.assertNotIn("onnxruntime==1.27.0", gpu)

        floors = [
            requirement
            for requirement in cpu + gpu
            if isinstance(requirement, str) and ">=" in requirement and "://" not in requirement
        ]
        self.assertEqual(floors, [], msg=f"open version floors remain: {floors}")

    def test_missing_lock_can_be_made_a_hard_error_for_release_builds(self) -> None:
        with patch("image_triage.ai_runtime_packages.resolve_lock_file", return_value=None):
            with patch.dict(
                os.environ,
                {ai_runtime_packages.AI_RUNTIME_REQUIRE_LOCK_ENV: "1"},
                clear=False,
            ):
                with self.assertRaisesRegex(RuntimeError, "refresh_ai_runtime_lock"):
                    build_ai_runtime_pip_install_args(
                        variant=AI_RUNTIME_CPU_VARIANT, target_dir=Path("C:/temp/runtime")
                    )

    def test_frozen_windows_build_cannot_install_without_a_lock(self) -> None:
        with (
            patch("image_triage.ai_runtime_packages.resolve_lock_file", return_value=None),
            patch("image_triage.ai_runtime_packages.sys.frozen", True, create=True),
            patch("image_triage.ai_runtime_packages.os.name", "nt"),
        ):
            with self.assertRaisesRegex(RuntimeError, "refresh_ai_runtime_lock"):
                build_ai_runtime_pip_install_args(
                    variant=AI_RUNTIME_CPU_VARIANT, target_dir=Path("C:/temp/runtime")
                )

    def test_default_pip_runner_uses_embedded_pip_when_frozen(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with (
                patch("image_triage.ai_runtime_packages.sys.frozen", True, create=True),
                patch("image_triage.ai_runtime_packages._run_embedded_pip", return_value=7) as embedded_runner,
                patch("image_triage.ai_runtime_packages.subprocess.run") as subprocess_run,
            ):
                result = _default_pip_runner(["install", "example"], Path(temp_dir))

        self.assertEqual(result, 7)
        embedded_runner.assert_called_once()
        subprocess_run.assert_not_called()

    def test_gpu_runtime_pins_torch_pair_compatible_with_dinov3_transformers(self) -> None:
        args = build_ai_runtime_pip_install_args(
            variant=AI_RUNTIME_GPU_VARIANT,
            target_dir=Path("C:/temp/runtime"),
            force=True,
        )

        self.assertIn("https://download.pytorch.org/whl/cu128", args)

    def test_shipped_locks_never_contain_both_onnxruntime_distributions(self) -> None:
        # Both wheels install the same `onnxruntime` package directory, so a
        # profile holding both has an provider set decided by unpack order.
        for include_dino in (True, False):
            lock = ai_runtime_packages.resolve_lock_file(
                AI_RUNTIME_GPU_VARIANT, include_dino=include_dino
            )
            if lock is None:
                continue
            with self.subTest(include_dino=include_dino):
                names = {
                    line.split("==", 1)[0].strip().lower()
                    for line in lock.read_text(encoding="utf-8").splitlines()
                    if "==" in line and not line.startswith("#")
                }
                self.assertIn("onnxruntime-gpu", names)
                self.assertNotIn("onnxruntime", names)

    def test_shipped_locks_never_contain_both_opencv_distributions(self) -> None:
        for variant in (AI_RUNTIME_CPU_VARIANT, AI_RUNTIME_GPU_VARIANT):
            for include_dino in (True, False):
                lock = ai_runtime_packages.resolve_lock_file(
                    variant, include_dino=include_dino
                )
                if lock is None:
                    continue
                with self.subTest(variant=variant, include_dino=include_dino):
                    names = {
                        line.split("==", 1)[0].strip().lower()
                        for line in lock.read_text(encoding="utf-8").splitlines()
                        if "==" in line and not line.startswith("#")
                    }
                    self.assertIn("opencv-python-headless", names)
                    self.assertNotIn("opencv-python", names)

    def test_shipped_locks_hash_every_distribution(self) -> None:
        for variant in (AI_RUNTIME_CPU_VARIANT, AI_RUNTIME_GPU_VARIANT):
            for include_dino in (True, False):
                lock = ai_runtime_packages.resolve_lock_file(
                    variant, include_dino=include_dino
                )
                if lock is None:
                    continue
                with self.subTest(variant=variant, include_dino=include_dino):
                    lines = [
                        line.strip()
                        for line in lock.read_text(encoding="utf-8").splitlines()
                        if line.strip() and not line.strip().startswith("#")
                    ]
                    pins = [line for line in lines if "==" in line]
                    hashes = [line for line in lines if line.startswith("--hash=sha256:")]
                    self.assertTrue(pins, msg=f"{lock.name} pins nothing")
                    self.assertEqual(
                        len(pins),
                        len(hashes),
                        msg=f"{lock.name} has {len(pins)} pins but {len(hashes)} hashes",
                    )

    def test_runtime_install_can_skip_optional_dino_dependencies(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            install_root = Path(temp_dir) / "runtime"
            recorded_calls: list[list[str]] = []

            def fake_pip_runner(args: list[str], cwd: Path) -> int:
                recorded_calls.append(args)
                target_dir = Path(args[args.index("--target") + 1])
                _materialize_base_runtime_modules(target_dir)
                return 0

            status = install_ai_runtime(
                AI_RUNTIME_CPU_VARIANT,
                include_dino=False,
                install_root=install_root,
                pip_runner=fake_pip_runner,
            )

            self.assertEqual(status.installed_variants, (AI_RUNTIME_CPU_VARIANT,))
            self.assertEqual(status.dino_installed_variants, ())
            self.assertTrue(
                any("-base" in str(item) for item in recorded_calls[0]),
                msg=f"compact install did not use the base set: {recorded_calls[0]}",
            )

    def test_full_runtime_install_upgrades_existing_compact_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            install_root = Path(temp_dir) / "runtime"
            recorded_calls: list[list[str]] = []

            def fake_pip_runner(args: list[str], cwd: Path) -> int:
                self.assertEqual(cwd, install_root)
                recorded_calls.append(args)
                target_dir = Path(args[args.index("--target") + 1])
                # The requirement set now reaches pip through a lock file, so
                # infer the profile from the recorded call order instead.
                if len(recorded_calls) > 1:
                    _materialize_runtime_modules(target_dir)
                else:
                    _materialize_base_runtime_modules(target_dir)
                return 0

            compact_status = install_ai_runtime(
                AI_RUNTIME_CPU_VARIANT,
                include_dino=False,
                install_root=install_root,
                pip_runner=fake_pip_runner,
            )
            upgraded_status = install_ai_runtime(
                AI_RUNTIME_CPU_VARIANT,
                include_dino=True,
                install_root=install_root,
                pip_runner=fake_pip_runner,
            )

            self.assertEqual(compact_status.dino_installed_variants, ())
            self.assertEqual(
                upgraded_status.dino_installed_variants,
                (AI_RUNTIME_CPU_VARIANT,),
            )
            self.assertEqual(len(recorded_calls), 2)
            # Each install names the lock (or requirement set) for its own
            # profile shape: compact first, then the PyTorch one.
            self.assertTrue(
                any("-base" in str(item) for item in recorded_calls[0]),
                msg=f"compact install did not use the base set: {recorded_calls[0]}",
            )
            self.assertFalse(any("-base" in str(item) for item in recorded_calls[1]))

    def test_failed_reinstall_preserves_active_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            install_root = Path(temp_dir) / "runtime"

            def successful_runner(args: list[str], _cwd: Path) -> int:
                _materialize_runtime_modules(Path(args[args.index("--target") + 1]))
                return 0

            installed = install_ai_runtime(
                AI_RUNTIME_CPU_VARIANT,
                install_root=install_root,
                pip_runner=successful_runner,
            )
            active_before = installed.profiles[AI_RUNTIME_CPU_VARIANT].site_packages_dir

            def failing_runner(args: list[str], _cwd: Path) -> int:
                target = Path(args[args.index("--target") + 1])
                (target / "partial-package").mkdir()
                return 2

            with self.assertRaisesRegex(RuntimeError, "existing runtime was not changed"):
                install_ai_runtime(
                    AI_RUNTIME_CPU_VARIANT,
                    force=True,
                    install_root=install_root,
                    pip_runner=failing_runner,
                )

            status = load_ai_runtime_installation_status(install_root=install_root)
            self.assertEqual(
                status.profiles[AI_RUNTIME_CPU_VARIANT].site_packages_dir,
                active_before,
            )
            self.assertTrue(status.profiles[AI_RUNTIME_CPU_VARIANT].is_installed)
            generation_dirs = tuple((install_root / "profiles").glob("cpu-*"))
            self.assertEqual(generation_dirs, (active_before.parent,))

    def test_failed_two_profile_install_activates_neither_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            install_root = Path(temp_dir) / "runtime"
            calls = 0

            def partial_runner(args: list[str], _cwd: Path) -> int:
                nonlocal calls
                calls += 1
                target = Path(args[args.index("--target") + 1])
                if calls == 1:
                    _materialize_runtime_modules(target)
                    return 0
                (target / "partial-package").mkdir()
                return 2

            with self.assertRaisesRegex(RuntimeError, "existing runtime was not changed"):
                install_ai_runtime(
                    "both",
                    install_root=install_root,
                    pip_runner=partial_runner,
                )

            status = load_ai_runtime_installation_status(install_root=install_root)
            self.assertEqual(status.installed_variants, ())
            self.assertFalse((install_root / "runtime_installation.json").exists())
            self.assertEqual(tuple((install_root / "profiles").iterdir()), ())

    def test_validation_failure_does_not_activate_staged_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            install_root = Path(temp_dir) / "runtime"

            def fake_runner(args: list[str], _cwd: Path) -> int:
                _materialize_runtime_modules(Path(args[args.index("--target") + 1]))
                return 0

            original = install_ai_runtime(
                AI_RUNTIME_GPU_VARIANT,
                install_root=install_root,
                pip_runner=fake_runner,
            )
            active_before = original.profiles[AI_RUNTIME_GPU_VARIANT].site_packages_dir

            def fail_validation(_path: Path, _variant: str, _include_dino: bool) -> None:
                raise RuntimeError("synthetic import failure")

            with self.assertRaisesRegex(RuntimeError, "synthetic import failure"):
                install_ai_runtime(
                    AI_RUNTIME_GPU_VARIANT,
                    install_root=install_root,
                    pip_runner=fake_runner,
                    profile_validator=fail_validation,
                )

            status = load_ai_runtime_installation_status(install_root=install_root)
            self.assertEqual(
                status.profiles[AI_RUNTIME_GPU_VARIANT].site_packages_dir,
                active_before,
            )

    def test_runtime_metadata_activates_generated_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            install_root = Path(temp_dir) / "runtime"

            def fake_runner(args: list[str], _cwd: Path) -> int:
                _materialize_base_runtime_modules(Path(args[args.index("--target") + 1]))
                return 0

            status = install_ai_runtime(
                AI_RUNTIME_CPU_VARIANT,
                include_dino=False,
                install_root=install_root,
                pip_runner=fake_runner,
            )
            metadata = json.loads((install_root / "runtime_installation.json").read_text("utf-8"))
            generation = metadata["profile_generations"][AI_RUNTIME_CPU_VARIANT]

            self.assertTrue(generation.startswith("cpu-"))
            self.assertEqual(metadata["metadata_version"], 2)
            self.assertEqual(
                status.profiles[AI_RUNTIME_CPU_VARIANT].site_packages_dir,
                install_root / "profiles" / generation / "site-packages",
            )

    def test_old_gpu_torch_runtime_is_reported_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            install_root = Path(temp_dir) / "runtime"
            site_packages = install_root / "profiles" / AI_RUNTIME_GPU_VARIANT / "site-packages"
            _materialize_runtime_modules(site_packages)
            (site_packages / "torch" / "version.py").write_text(
                "__version__ = '2.8.0+cu128'\ncuda = '12.8'\n",
                encoding="utf-8",
            )

            status = load_ai_runtime_installation_status(install_root=install_root)

        self.assertFalse(status.profiles[AI_RUNTIME_GPU_VARIANT].is_installed)
        self.assertIn("torch>=2.9.0+cu128", status.profiles[AI_RUNTIME_GPU_VARIANT].missing_modules)

    def test_legacy_gpu_profile_remains_valid_but_reports_missing_onnx_gpu_capability(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            install_root = Path(temp_dir) / "runtime"
            site_packages = install_root / "profiles" / AI_RUNTIME_GPU_VARIANT / "site-packages"
            _materialize_runtime_modules(site_packages)
            for metadata_dir in site_packages.glob("onnxruntime_gpu-*.dist-info"):
                for child in metadata_dir.iterdir():
                    child.unlink()
                metadata_dir.rmdir()

            status = load_ai_runtime_installation_status(install_root=install_root)

        self.assertIn(AI_RUNTIME_GPU_VARIANT, status.installed_variants)
        self.assertNotIn(AI_RUNTIME_GPU_VARIANT, status.onnx_gpu_installed_variants)

    def test_runtime_size_estimates_are_available_for_setup_copy(self) -> None:
        self.assertGreater(estimate_ai_runtime_download_size_mb(AI_RUNTIME_GPU_VARIANT), 3000)
        self.assertGreater(estimate_ai_runtime_installed_size_mb(AI_RUNTIME_GPU_VARIANT), 5000)
        self.assertLess(
            estimate_ai_runtime_download_size_mb(
                AI_RUNTIME_GPU_VARIANT,
                include_dino=False,
            ),
            estimate_ai_runtime_download_size_mb(AI_RUNTIME_GPU_VARIANT),
        )
        self.assertLess(
            estimate_ai_runtime_installed_size_mb(
                AI_RUNTIME_CPU_VARIANT,
                include_dino=False,
            ),
            estimate_ai_runtime_installed_size_mb(AI_RUNTIME_CPU_VARIANT),
        )

    def test_runtime_install_root_uses_user_profile_without_home_lookup(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env = {
                "IMAGE_TRIAGE_AI_RUNTIME_ROOT": "",
                "LOCALAPPDATA": str(Path(temp_dir) / "AppData/Local"),
                "USERPROFILE": temp_dir,
            }
            with patch.dict(os.environ, env, clear=False):
                with patch("image_triage.ai_runtime_packages.Path.home", side_effect=RuntimeError("no home")):
                    status = load_ai_runtime_installation_status()

        self.assertTrue(str(status.directories.root).startswith(temp_dir))

    @unittest.skipUnless(os.name == "nt", "Windows Store path virtualization")
    def test_runtime_root_escapes_store_python_local_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            user_profile = Path(temp_dir) / "User"
            store_cache = (
                user_profile
                / "AppData/Local/Packages/PythonSoftwareFoundation.Python.3.13_test/LocalCache/Local"
            )
            with patch.dict(
                os.environ,
                {"LOCALAPPDATA": str(store_cache), "USERPROFILE": str(user_profile)},
                clear=False,
            ):
                root = default_ai_runtime_install_root()

        self.assertTrue(str(root).startswith(str(user_profile / ".image-triage/AI/rt")))
        self.assertNotIn("PythonSoftwareFoundation", str(root))

    @unittest.skipUnless(os.name == "nt", "Windows runtime path migration")
    def test_legacy_runtime_is_moved_to_short_user_profile_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            user_profile = Path(temp_dir) / "User"
            local_appdata = user_profile / "AppData/Local"
            legacy_root = (
                local_appdata
                / "image_triage_ai_cache/runtime"
                / _python_runtime_tag()
            )
            legacy_root.mkdir(parents=True)
            (legacy_root / "legacy-marker").write_text("present", encoding="utf-8")
            with patch.dict(
                os.environ,
                {
                    "IMAGE_TRIAGE_AI_RUNTIME_ROOT": "",
                    "LOCALAPPDATA": str(local_appdata),
                    "USERPROFILE": str(user_profile),
                },
                clear=False,
            ):
                migrated_root = default_ai_runtime_install_root()

            self.assertEqual(
                migrated_root,
                user_profile / ".image-triage/AI/rt" / _python_runtime_tag(),
            )
            self.assertTrue((migrated_root / "legacy-marker").is_file())
            self.assertFalse(legacy_root.exists())

    def test_distribution_record_validation_detects_partial_install(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            site_packages = Path(temp_dir) / "site-packages"
            package = site_packages / "transformers"
            package.mkdir(parents=True)
            (package / "__init__.py").write_text("", encoding="utf-8")
            dist_info = site_packages / "transformers-5.17.0.dist-info"
            dist_info.mkdir()
            (dist_info / "RECORD").write_text(
                "transformers/__init__.py,,\n"
                "transformers/models/example/configuration_example.py,,\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(RuntimeError, "missing installed files"):
                _validate_distribution_records(site_packages)

    def test_distribution_validation_rejects_both_opencv_wheels(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            site_packages = Path(temp_dir)
            for name in ("opencv_python", "opencv_python_headless"):
                dist_info = site_packages / f"{name}-5.0.0.93.dist-info"
                dist_info.mkdir()
                (dist_info / "METADATA").write_text(
                    f"Name: {name.replace('_', '-')}\nVersion: 5.0.0.93\n",
                    encoding="utf-8",
                )
                (dist_info / "RECORD").write_text("", encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "both opencv-python"):
                _validate_distribution_records(site_packages)

    def test_runtime_install_lock_rejects_a_second_installer(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_root = Path(temp_dir) / "runtime"
            with _ai_runtime_install_lock(runtime_root):
                with self.assertRaisesRegex(RuntimeError, "already running"):
                    with _ai_runtime_install_lock(runtime_root):
                        self.fail("a second installer acquired the same runtime lock")

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

    def test_old_transformers_runtime_is_reported_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            install_root = Path(temp_dir) / "runtime"
            site_packages = install_root / "profiles" / AI_RUNTIME_CPU_VARIANT / "site-packages"
            _materialize_runtime_modules(site_packages)
            for metadata_dir in site_packages.glob("transformers-*.dist-info"):
                for child in metadata_dir.iterdir():
                    child.unlink()
                metadata_dir.rmdir()
            dist_info = site_packages / "transformers-4.46.0.dist-info"
            dist_info.mkdir(parents=True)
            (dist_info / "METADATA").write_text(
                "Name: transformers\nVersion: 4.46.0\n",
                encoding="utf-8",
            )

            status = load_ai_runtime_installation_status(install_root=install_root)

        self.assertFalse(status.profiles[AI_RUNTIME_CPU_VARIANT].is_installed)
        self.assertIn("transformers>=4.56", status.profiles[AI_RUNTIME_CPU_VARIANT].missing_modules)

    def test_partial_transformers_tree_is_reported_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            install_root = Path(temp_dir) / "runtime"
            site_packages = install_root / "profiles" / AI_RUNTIME_CPU_VARIANT / "site-packages"
            _materialize_runtime_modules(site_packages)
            missing_path = site_packages / AI_RUNTIME_DINO_REQUIRED_FILES[0][0]
            missing_path.unlink()

            status = load_ai_runtime_installation_status(install_root=install_root)

        self.assertFalse(status.profiles[AI_RUNTIME_CPU_VARIANT].is_installed)
        self.assertIn(
            "transformers package files",
            status.profiles[AI_RUNTIME_CPU_VARIANT].missing_modules,
        )



class OnnxRuntimeExclusivityTests(unittest.TestCase):
    """A profile must never hold both ONNX Runtime distributions at once."""

    @staticmethod
    def _distribution(site_packages: Path, name: str, version: str) -> None:
        info = site_packages / f"{name.replace('-', '_')}-{version}.dist-info"
        info.mkdir(parents=True)
        (info / "METADATA").write_text(
            f"Name: {name}\nVersion: {version}\n", encoding="utf-8"
        )
        (info / "RECORD").write_text("", encoding="utf-8")

    def test_both_distributions_present_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            site_packages = Path(temp_dir)
            self._distribution(site_packages, "onnxruntime", "1.26.0")
            self._distribution(site_packages, "onnxruntime-gpu", "1.26.0")

            with self.assertRaisesRegex(RuntimeError, "onnxruntime-gpu"):
                ai_runtime_packages._validate_onnxruntime_exclusivity(site_packages)

    def test_a_single_distribution_is_accepted(self) -> None:
        for name in ("onnxruntime", "onnxruntime-gpu"):
            with self.subTest(distribution=name), tempfile.TemporaryDirectory() as temp_dir:
                site_packages = Path(temp_dir)
                self._distribution(site_packages, name, "1.26.0")
                ai_runtime_packages._validate_onnxruntime_exclusivity(site_packages)

if __name__ == "__main__":
    unittest.main()
