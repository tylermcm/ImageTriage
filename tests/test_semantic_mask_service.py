from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from unittest.mock import patch

import image_triage.semantic_mask_service as service_module
from image_triage.ai_env import AIRuntimeUnavailable
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
        # Readiness now comes from the central capability health service, so a
        # package problem is reported with the same message Settings shows
        # instead of this module's own directory-existence check.
        failure = AIRuntimeUnavailable(
            "Scene selection (OneFormer) failed its runtime check: transformers "
            "could not be imported from the installed AI runtime.",
            category="package_missing",
            remediation="Use Repair AI in Settings to reinstall the AI runtime packages.",
        )
        with patch.object(service_module, "select_runtime"), patch.object(
            service_module, "require_capability", side_effect=failure
        ):
            with self.assertRaisesRegex(RuntimeError, "transformers"):
                validate_semantic_runtime()

    def test_absent_runtime_is_reported(self) -> None:
        failure = AIRuntimeUnavailable(
            "The AI runtime is not installed yet.", category="runtime_missing"
        )
        with patch.object(service_module, "select_runtime", side_effect=failure):
            with self.assertRaisesRegex(RuntimeError, "not installed"):
                validate_semantic_runtime()

    def test_a_capability_failure_carries_a_remediation(self) -> None:
        failure = AIRuntimeUnavailable(
            "Scene selection (OneFormer) is not ready.",
            category="bundle_missing",
            remediation="Download OneFormer scene segmentation from Settings.",
        )
        with patch.object(service_module, "select_runtime"), patch.object(
            service_module, "require_capability", side_effect=failure
        ):
            with self.assertRaises(AIRuntimeUnavailable) as caught:
                validate_semantic_runtime()

        self.assertIn("Settings", caught.exception.remediation)


class PerCapabilityValidationTests(unittest.TestCase):
    """Each editor mask feature validates its own capability, not OneFormer's."""

    def setUp(self) -> None:
        service_module.reset_runtime_validation_cache()

    def tearDown(self) -> None:
        service_module.reset_runtime_validation_cache()

    def test_each_feature_validates_its_own_capability(self) -> None:
        asked: list[str] = []
        with patch.object(service_module, "select_runtime"), patch.object(
            service_module, "require_capability", side_effect=lambda key, **_: asked.append(key)
        ):
            service_module.validate_mask_runtime("depth")
            service_module.validate_mask_runtime("sam_masks")
            service_module.validate_semantic_runtime()

        self.assertEqual(asked, ["depth", "sam_masks", "scene_masks"])

    def test_a_missing_scene_model_does_not_block_depth(self) -> None:
        # One optional model must never disable an unrelated capability.
        def guard(key, **_kwargs):
            if key == "scene_masks":
                raise AIRuntimeUnavailable("OneFormer is not downloaded.", category="bundle_missing")

        with patch.object(service_module, "select_runtime"), patch.object(
            service_module, "require_capability", side_effect=guard
        ):
            service_module.validate_mask_runtime("depth")
            with self.assertRaises(AIRuntimeUnavailable):
                service_module.validate_semantic_runtime()

    def test_validation_is_cached_per_capability(self) -> None:
        asked: list[str] = []
        with patch.object(service_module, "select_runtime"), patch.object(
            service_module, "require_capability", side_effect=lambda key, **_: asked.append(key)
        ):
            service_module.validate_mask_runtime("depth")
            service_module.validate_mask_runtime("depth")
            service_module.validate_mask_runtime("sam_masks")

        self.assertEqual(asked, ["depth", "sam_masks"])

    def test_resetting_the_cache_forces_revalidation(self) -> None:
        asked: list[str] = []
        with patch.object(service_module, "select_runtime"), patch.object(
            service_module, "require_capability", side_effect=lambda key, **_: asked.append(key)
        ):
            service_module.validate_mask_runtime("depth")
            service_module.reset_runtime_validation_cache()
            service_module.validate_mask_runtime("depth")

        self.assertEqual(asked, ["depth", "depth"])

    def test_the_shared_engine_host_does_not_require_any_model_bundle(self) -> None:
        # The MaskEngine host loads engines lazily; coupling it to a bundle
        # would let a missing OneFormer model break click selection.
        import image_triage.mask_engine_service as engine_module
        import inspect

        source = inspect.getsource(engine_module._resolve_engine_runtime)
        self.assertNotIn("require_capability", source)
        self.assertIn("require_torch=True", source)


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
