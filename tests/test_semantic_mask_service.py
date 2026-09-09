from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import image_triage.semantic_mask_service as service_module
from image_triage.semantic_mask_service import (
    SemanticMaskWarmTask,
    validate_semantic_runtime,
)


class SemanticRuntimeValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        service_module.reset_runtime_validation_cache()

    def tearDown(self) -> None:
        service_module.reset_runtime_validation_cache()

    def test_missing_runtime_dependency_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            site_packages = Path(temp_dir) / "site-packages"
            # torch present, transformers missing.
            (site_packages / "torch").mkdir(parents=True)
            (site_packages / "PIL").mkdir()
            (site_packages / "numpy").mkdir()
            (site_packages / "safetensors").mkdir()
            original = service_module.resolve_ai_runtime_site_packages
            service_module.resolve_ai_runtime_site_packages = lambda **_kwargs: (site_packages,)
            try:
                with self.assertRaisesRegex(
                    RuntimeError,
                    "transformers.*run Set Up AI again.*editor masking support",
                ):
                    validate_semantic_runtime()
            finally:
                service_module.resolve_ai_runtime_site_packages = original

    def test_absent_runtime_is_reported(self) -> None:
        original = service_module.resolve_ai_runtime_site_packages
        service_module.resolve_ai_runtime_site_packages = lambda **_kwargs: ()
        try:
            with self.assertRaisesRegex(RuntimeError, "AI runtime is unavailable"):
                validate_semantic_runtime()
        finally:
            service_module.resolve_ai_runtime_site_packages = original


class SemanticMaskWarmTaskTests(unittest.TestCase):
    def test_unknown_stage_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            SemanticMaskWarmTask("nonsense")

    def test_warm_skips_quietly_when_model_not_installed(self) -> None:
        import image_triage.mask_engine_service as engine_module

        with tempfile.TemporaryDirectory() as temp_dir:
            original_install = service_module.resolve_segmentation_model_installation
            original_engine = engine_module.default_mask_engine_service
            spawned: list[str] = []

            class _Missing:
                is_installed = False
                install_dir = Path(temp_dir)

            def _guard():
                spawned.append("engine")
                raise AssertionError("must not touch the host when uninstalled")

            service_module.resolve_segmentation_model_installation = lambda: _Missing()
            engine_module.default_mask_engine_service = _guard
            try:
                # Neither stage may raise or reach the host when uninstalled.
                SemanticMaskWarmTask("model").run()
                SemanticMaskWarmTask("imports").run()
            finally:
                service_module.resolve_segmentation_model_installation = original_install
                engine_module.default_mask_engine_service = original_engine
            self.assertEqual([], spawned)


if __name__ == "__main__":
    unittest.main()
