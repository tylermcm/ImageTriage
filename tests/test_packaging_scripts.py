from __future__ import annotations

import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read_tree(path: str) -> ast.Module:
    return ast.parse((ROOT / path).read_text(encoding="utf-8"), filename=path)


def _string_constants(tree: ast.AST) -> set[str]:
    values: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            values.add(node.value)
    return values


class PackagingScriptTests(unittest.TestCase):
    def test_both_packages_freeze_the_same_module_lists(self) -> None:
        # One list for the MSI and the AppImage (freeze_support): the AppImage's own copy fell behind and froze an app that could not start.
        for setup_path in ("setup_msi.py", "setup_linux.py"):
            text = (ROOT / setup_path).read_text(encoding="utf-8")
            self.assertIn('"includes": list(APP_FREEZE_INCLUDES)', text, setup_path)
            self.assertIn('"excludes": [*APP_FREEZE_EXCLUDES, *AI_FREEZE_EXCLUDES]', text, setup_path)

    def test_frozen_apps_include_onnxruntime_for_in_process_masking(self) -> None:
        import freeze_support

        self.assertIn("onnxruntime", freeze_support.APP_FREEZE_INCLUDES)

    def test_the_qml_shell_is_named_explicitly_for_cx_freeze(self) -> None:
        import freeze_support

        for name in ("image_triage.qml_shell", "image_triage.qml_shell.app", "PySide6.QtQml", "PySide6.QtQuick"):
            self.assertIn(name, freeze_support.APP_FREEZE_INCLUDES)
        self.assertIn("image_triage.window", freeze_support.APP_FREEZE_EXCLUDES)

    def test_linux_appimage_packages_current_frozen_helpers(self) -> None:
        constants = _string_constants(_read_tree("setup_linux.py"))

        self.assertIn("ImageTriage", constants)
        self.assertIn("ai_python_runner", constants)
        self.assertIn("ai_runtime_installer", constants)
        self.assertIn("image_triage_cleanup", constants)

    def test_linux_menu_entries_match_the_apps_desktop_file_name(self) -> None:
        # Wayland ties a window to its menu entry by the app's desktop file name: the AppImage's entry, the installer's and the app agree.
        app = (ROOT / "image_triage" / "qml_shell" / "app.py").read_text(encoding="utf-8")
        self.assertIn('QGuiApplication.setDesktopFileName("image-triage")', app)
        appimage = (ROOT / "setup_linux.py").read_text(encoding="utf-8")
        installer = (ROOT / "packaging" / "install_linux.sh").read_text(encoding="utf-8")
        for text in (appimage, installer):
            self.assertIn("StartupWMClass=image-triage", text)
            self.assertIn("Categories=Graphics;Photography;", text)
        self.assertIn('"bdist_appimage": bdist_appimage_with_desktop_entry()', appimage)
        self.assertTrue((ROOT / "build_assets" / "icons" / "image_triage-v5.png").is_file())
        self.assertIn("icons/image_triage-v5.png", installer)

    def test_the_raw_preview_worker_builds_each_platforms_gpu_backend(self) -> None:
        # DX12 only (as it once was) left the Linux worker without an adapter: every preview fell back to the CPU.
        manifest = (ROOT / "native" / "raw_preview" / "Cargo.toml.in").read_text(encoding="utf-8")
        source = (ROOT / "native" / "raw_preview" / "main.rs").read_text(encoding="utf-8")
        for table, feature, backend in (("cfg(windows)", '"dx12"', "Backends::DX12"),
                                        ('cfg(target_os = "macos")', '"metal"', "Backends::METAL"),
                                        ('cfg(not(any(windows, target_os = "macos")))', '"vulkan"', "Backends::VULKAN")):
            section = manifest.split(f"[target.'{table}'.dependencies]", 1)[1].split("\n[", 1)[0]
            self.assertIn(feature, section, table)
            self.assertIn(backend, source)
        self.assertTrue((ROOT / "scripts" / "build_raw_preview.sh").is_file())

    def test_linux_appimage_keeps_pip_for_frozen_runtime_installer(self) -> None:
        import freeze_support

        self.assertIn("pip", freeze_support.APP_FREEZE_INCLUDES)
        self.assertIn("pip._internal", freeze_support.APP_FREEZE_INCLUDES)
        self.assertNotIn("pip", freeze_support.APP_FREEZE_EXCLUDES + freeze_support.AI_FREEZE_EXCLUDES)

    def test_upscale_worker_and_its_core_are_packaged_as_external_ai_scripts(self) -> None:
        # The worker imports upscale_core as a sibling, so both must sit in ai_workers/.
        # (each name in AI_WORKER_SCRIPTS is shipped as ai_workers/<name>.py, from image_triage/<name>.py)
        import freeze_support

        self.assertIn("upscale_worker", freeze_support.AI_WORKER_SCRIPTS)
        self.assertIn("upscale_core", freeze_support.AI_WORKER_SCRIPTS)
        self.assertIn('f"ai_workers/{name}.py") for name in AI_WORKER_SCRIPTS', (ROOT / "freeze_support.py").read_text(encoding="utf-8"))

    def test_upscale_service_modules_are_named_explicitly_for_cx_freeze(self) -> None:
        import freeze_support

        self.assertIn("image_triage.upscale_core", freeze_support.APP_FREEZE_INCLUDES)
        self.assertIn("image_triage.upscale_service", freeze_support.APP_FREEZE_INCLUDES)

    def test_vendored_editor_engine_is_named_explicitly_for_cx_freeze(self) -> None:
        # image_triage.photo_terminal (the vendored former cli_editor package)
        # is reached via a lazy import inside editor_render.py, so it must be
        # named explicitly rather than relying on cx_Freeze's static scan.
        import freeze_support

        includes = freeze_support.APP_FREEZE_INCLUDES
        self.assertIn("image_triage.photo_terminal", includes)
        self.assertIn("image_triage.photo_terminal.adjustments", includes)


if __name__ == "__main__":
    unittest.main()


class PackagingConsistencyTests(unittest.TestCase):
    def test_the_cleanup_tool_removes_every_type_the_app_registers(self) -> None:
        import importlib.util

        from image_triage.file_associations import supported_file_association_suffixes

        spec = importlib.util.spec_from_file_location("image_triage_cleanup_under_test", ROOT / "packaging" / "image_triage_cleanup.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertEqual(set(supported_file_association_suffixes()), set(module.SUPPORTED_SUFFIXES))
        self.assertGreater(len(module.SUPPORTED_SUFFIXES), len(module._FALLBACK_SUFFIXES))

    def test_every_declared_dependency_is_imported_and_the_native_module_is_package_data(self) -> None:
        import tomllib

        project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        names = [dependency.split(">")[0].split("=")[0].strip().lower() for dependency in project["project"]["dependencies"]]
        self.assertNotIn("qrcode", names)      # nothing imports it
        data = project["tool"]["setuptools"]["package-data"]
        self.assertIn("pocketdrop.dll", data["image_triage.pocketdrop"])

    def test_the_test_workflow_runs_the_whole_suite_on_main(self) -> None:
        text = (ROOT / ".github" / "workflows" / "tests.yml").read_text(encoding="utf-8")
        self.assertIn("python -m pytest -q -p no:cacheprovider\n", text)
        self.assertIn("windows-latest", text)
        self.assertIn("ubuntu-latest", text)
        self.assertNotIn("continue-on-error", text)                    # both jobs block: the suite is clean on Linux and Windows
        for package in ("libegl1", "libgl1", "libxkbcommon0", "libfontconfig1", "libdbus-1-3", "libglib2.0-0"):
            self.assertIn(package, text)

    def test_the_release_build_makes_and_bundles_the_raw_preview_worker(self) -> None:
        text = (ROOT / ".github" / "workflows" / "build-windows-msi-release.yml").read_text(encoding="utf-8")
        self.assertIn("IMAGE_TRIAGE_BUNDLE_RAW_PREVIEW_EXE=", text)
        self.assertLess(text.index("Build the RAW preview worker"), text.index("Build MSI"))
        self.assertIn("RENDER_ALGORITHM", text)
