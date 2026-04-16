from __future__ import annotations

import os
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch

from freeze_support import FreezeAssetLayout, prepare_ai_build_assets, resolve_freeze_asset_layout


class FreezeSupportTests(unittest.TestCase):
    def test_resolve_freeze_asset_layout_prefers_explicit_environment_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            ai_source = root / "engine"
            site_packages = root / "site-packages"
            stdlib = root / "stdlib"
            binary_modules = root / "lib-dynload"
            for path in (ai_source, site_packages, stdlib, binary_modules):
                path.mkdir(parents=True)

            env = {
                "IMAGE_TRIAGE_AI_SOURCE": str(ai_source),
                "IMAGE_TRIAGE_AI_SITE_PACKAGES": str(site_packages),
                "IMAGE_TRIAGE_AI_STDLIB": str(stdlib),
                "IMAGE_TRIAGE_AI_DLLS": str(binary_modules),
            }
            with patch.dict(os.environ, env, clear=False):
                layout = resolve_freeze_asset_layout()

            self.assertEqual(layout.ai_source, ai_source.resolve())
            self.assertEqual(layout.ai_site_packages_source, site_packages.resolve())
            self.assertEqual(layout.ai_stdlib_source, stdlib.resolve())
            self.assertEqual(layout.ai_binary_modules_source, binary_modules.resolve())

    def test_prepare_ai_build_assets_stages_runtime_and_support_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            ai_source = root / "AICullingPipeline"
            scripts_dir = ai_source / "scripts"
            config_dir = ai_source / "configs"
            app_dir = ai_source / "app"
            app_dir.mkdir(parents=True)
            config_dir.mkdir(parents=True)
            scripts_dir.mkdir(parents=True)
            (app_dir / "__init__.py").write_text("", encoding="utf-8")
            (config_dir / "extract_embeddings.json").write_text("{}", encoding="utf-8")
            (config_dir / "cluster_embeddings.json").write_text("{}", encoding="utf-8")
            (config_dir / "export_ranked_report.json").write_text("{}", encoding="utf-8")
            for script_name in (
                "extract_embeddings.py",
                "cluster_embeddings.py",
                "export_ranked_report.py",
            ):
                (scripts_dir / script_name).write_text(
                    textwrap.dedent(
                        """
                        from __future__ import annotations

                        print("hello")
                        """
                    ).lstrip(),
                    encoding="utf-8",
                )

            site_packages = root / "site-packages"
            site_packages.mkdir(parents=True)
            (site_packages / "typing_extensions.py").write_text("# stub\n", encoding="utf-8")
            (site_packages / "requests").mkdir()
            (site_packages / "requests" / "__init__.py").write_text("", encoding="utf-8")

            stdlib = root / "stdlib"
            stdlib.mkdir(parents=True)
            (stdlib / "json.py").write_text("# stub\n", encoding="utf-8")

            binary_modules = root / "binary-modules"
            binary_modules.mkdir(parents=True)
            (binary_modules / "_struct.pyd").write_bytes(b"binary")

            layout = FreezeAssetLayout(
                ai_source=ai_source,
                ai_site_packages_source=site_packages,
                ai_stdlib_source=stdlib,
                ai_binary_modules_source=binary_modules,
                ai_stage_root=root / "stage" / "ai_runtime" / "AICullingPipeline",
                ai_site_packages_stage_root=root / "stage" / "ai_site_packages",
                ai_stdlib_stage_root=root / "stage" / "ai_stdlib",
                ai_binary_modules_stage_root=root / "stage" / "lib",
            )

            with patch("freeze_support.AI_SITE_PACKAGES_ENTRIES", ("typing_extensions.py", "requests")), patch(
                "freeze_support.AI_SITE_PACKAGES_OPTIONAL_ENTRIES",
                (),
            ):
                prepare_ai_build_assets(layout)

            staged_script = layout.ai_stage_root / "scripts" / "extract_embeddings.py"
            self.assertTrue(staged_script.exists())
            staged_text = staged_script.read_text(encoding="utf-8")
            self.assertIn("image_triage-bootstrap", staged_text)
            self.assertTrue((layout.ai_site_packages_stage_root / "typing_extensions.py").exists())
            self.assertTrue((layout.ai_site_packages_stage_root / "requests" / "__init__.py").exists())
            self.assertTrue((layout.ai_stdlib_stage_root / "json.py").exists())
            self.assertTrue((layout.ai_binary_modules_stage_root / "_struct.pyd").exists())


if __name__ == "__main__":
    unittest.main()
