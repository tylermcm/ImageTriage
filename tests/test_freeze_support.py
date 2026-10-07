from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from freeze_support import (
    FreezeAssetLayout,
    QT_WINDOWS_BINARY_EXCLUDES,
    prepare_ai_build_assets,
    resolve_freeze_asset_layout,
)


class FreezeSupportTests(unittest.TestCase):
    def test_qt_binary_excludes_block_all_host_icu_dependency_resolution(self) -> None:
        self.assertEqual(
            set(QT_WINDOWS_BINARY_EXCLUDES),
            {"icu.dll", "icuin.dll", "icuuc.dll", "icudt78.dll"},
        )

    def test_frozen_layout_ships_managed_runtime_locks(self) -> None:
        root = Path("C:/build-test")
        layout = FreezeAssetLayout(
            ai_site_packages_source=root / "site-packages",
            ai_stdlib_source=root / "stdlib",
            ai_binary_modules_source=root / "lib-dynload",
        )
        destinations = {destination for _source, destination in layout.include_files}

        self.assertIn("packaging/ai_runtime_locks", destinations)

    def test_resolve_freeze_asset_layout_prefers_explicit_environment_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            site_packages = root / "site-packages"
            stdlib = root / "stdlib"
            binary_modules = root / "lib-dynload"
            for path in (site_packages, stdlib, binary_modules):
                path.mkdir(parents=True)

            env = {
                "IMAGE_TRIAGE_AI_SITE_PACKAGES": str(site_packages),
                "IMAGE_TRIAGE_AI_STDLIB": str(stdlib),
                "IMAGE_TRIAGE_AI_DLLS": str(binary_modules),
            }
            with patch.dict(os.environ, env, clear=False):
                layout = resolve_freeze_asset_layout()

            self.assertEqual(layout.ai_site_packages_source, site_packages.resolve())
            self.assertEqual(layout.ai_stdlib_source, stdlib.resolve())
            self.assertEqual(layout.ai_binary_modules_source, binary_modules.resolve())

    def test_frozen_layout_no_longer_ships_the_legacy_engine(self) -> None:
        layout = FreezeAssetLayout(
            ai_site_packages_source=Path("sp"),
            ai_stdlib_source=Path("std"),
            ai_binary_modules_source=Path("dll"),
        )

        targets = [target for _source, target in layout.include_files]

        self.assertNotIn("ai_runtime", targets)
        self.assertFalse(hasattr(layout, "ai_source"))
        self.assertFalse(hasattr(layout, "ai_stage_root"))

    def _layout(self, root: Path, *, site_packages: Path, bundle: bool) -> FreezeAssetLayout:
        stdlib = root / "stdlib"
        stdlib.mkdir(parents=True, exist_ok=True)
        (stdlib / "json.py").write_text("# stub\n", encoding="utf-8")
        binary_modules = root / "binary-modules"
        binary_modules.mkdir(parents=True, exist_ok=True)
        (binary_modules / "_struct.pyd").write_bytes(b"binary")
        return FreezeAssetLayout(
            ai_site_packages_source=site_packages,
            ai_stdlib_source=stdlib,
            ai_binary_modules_source=binary_modules,
            bundle_ai_site_packages=bundle,
            ai_site_packages_stage_root=root / "stage" / "ai_site_packages",
            ai_stdlib_stage_root=root / "stage" / "ai_stdlib",
            ai_binary_modules_stage_root=root / "stage" / "lib",
        )

    def test_prepare_ai_build_assets_stages_runtime_support_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            site_packages = root / "site-packages"
            site_packages.mkdir(parents=True)
            (site_packages / "typing_extensions.py").write_text("# stub\n", encoding="utf-8")
            (site_packages / "requests").mkdir()
            (site_packages / "requests" / "__init__.py").write_text("", encoding="utf-8")
            layout = self._layout(root, site_packages=site_packages, bundle=True)

            with patch("freeze_support.AI_SITE_PACKAGES_ENTRIES", ("typing_extensions.py", "requests")), patch(
                "freeze_support.AI_SITE_PACKAGES_OPTIONAL_ENTRIES",
                (),
            ):
                prepare_ai_build_assets(layout)

            self.assertTrue((layout.ai_site_packages_stage_root / "typing_extensions.py").exists())
            self.assertTrue((layout.ai_site_packages_stage_root / "requests" / "__init__.py").exists())
            self.assertTrue((layout.ai_stdlib_stage_root / "json.py").exists())
            self.assertTrue((layout.ai_binary_modules_stage_root / "_struct.pyd").exists())

    def test_prepare_ai_build_assets_can_skip_bundled_site_packages(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            layout = self._layout(root, site_packages=root / "missing-site-packages", bundle=False)

            prepare_ai_build_assets(layout)

            self.assertFalse(layout.ai_site_packages_stage_root.exists())
            self.assertTrue((layout.ai_stdlib_stage_root / "json.py").exists())

    def test_prepare_ai_build_assets_fails_when_required_site_package_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            site_packages = root / "site-packages"
            site_packages.mkdir(parents=True)
            layout = self._layout(root, site_packages=site_packages, bundle=True)

            with patch("freeze_support.AI_SITE_PACKAGES_ENTRIES", ("onnxruntime",)), patch(
                "freeze_support.AI_SITE_PACKAGES_OPTIONAL_ENTRIES",
                (),
            ):
                with self.assertRaisesRegex(FileNotFoundError, "Required bundled AI dependency"):
                    prepare_ai_build_assets(layout)


if __name__ == "__main__":
    unittest.main()
