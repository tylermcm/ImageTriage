"""Runtime selection and the single worker environment builder."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from image_triage import ai_env
from image_triage.ai_env import AIRuntimeUnavailable, RuntimeSelection
from image_triage.ai_runtime_packages import (
    AIRuntimeDirectories,
    AIRuntimeInstallationStatus,
    AIRuntimeProfileStatus,
)


def _status(
    *,
    installed: tuple[str, ...],
    preferred: str = "gpu",
    torch_variants: tuple[str, ...] = (),
    root: Path = Path("C:/managed"),
) -> AIRuntimeInstallationStatus:
    directories = AIRuntimeDirectories(
        root=root,
        metadata_path=root / "runtime_installation.json",
        profiles_root=root / "profiles",
    )
    profiles = {
        variant: AIRuntimeProfileStatus(
            variant=variant,
            site_packages_dir=root / "profiles" / variant / "site-packages",
            missing_modules=() if variant in installed else ("torch",),
        )
        for variant in ("cpu", "gpu")
    }
    return AIRuntimeInstallationStatus(
        directories=directories,
        profiles=profiles,
        installed_variants=installed,
        preferred_variant=preferred,
        dino_installed_variants=torch_variants,
    )


class DeviceResolutionTests(unittest.TestCase):
    def test_gpu_aliases_normalize_to_cuda(self) -> None:
        for value in ("gpu", "GPU", "cuda", "Cuda"):
            with self.subTest(value=value):
                self.assertEqual(ai_env.resolve_device(value), "cuda")

    def test_an_explicit_gpu_index_survives_normalization(self) -> None:
        # AICULLING_DEVICE=cuda:1 is a supported override; collapsing it to
        # "auto" silently moved multi-GPU users onto device 0.
        self.assertEqual(ai_env.resolve_device("cuda:1"), "cuda:1")
        self.assertEqual(ai_env.resolve_device("gpu:2"), "cuda:2")
        self.assertEqual(ai_env.resolve_device("cuda:0"), "cuda:0")

    def test_a_malformed_index_is_not_treated_as_a_device(self) -> None:
        self.assertEqual(ai_env.resolve_device("cuda:x"), "auto")
        self.assertEqual(ai_env.resolve_device("cuda:-1"), "auto")

    def test_device_family_groups_indexed_cuda_devices(self) -> None:
        self.assertEqual(ai_env.device_family("cuda:3"), "cuda")
        self.assertEqual(ai_env.device_family("cuda"), "cuda")
        self.assertEqual(ai_env.device_family("cpu"), "cpu")

    def test_unknown_values_fall_back_to_auto(self) -> None:
        self.assertEqual(ai_env.resolve_device(None), "auto")
        self.assertEqual(ai_env.resolve_device("something-else"), "auto")


class VariantChoiceTests(unittest.TestCase):
    def test_an_explicit_cpu_request_never_selects_the_gpu_profile(self) -> None:
        # "Switch to CPU" used to fall through to the GPU profile, which made
        # the remediation ineffective on a machine with a broken GPU runtime.
        self.assertEqual(ai_env._choose_variant(("gpu",), "gpu", "cpu"), "")
        self.assertEqual(ai_env._choose_variant(("cpu", "gpu"), "gpu", "cpu"), "cpu")

    def test_an_explicit_gpu_request_never_selects_the_cpu_profile(self) -> None:
        self.assertEqual(ai_env._choose_variant(("cpu",), "cpu", "cuda"), "")

    def test_auto_honours_the_recorded_preference(self) -> None:
        self.assertEqual(ai_env._choose_variant(("cpu", "gpu"), "cpu", "auto"), "cpu")
        self.assertEqual(ai_env._choose_variant(("cpu", "gpu"), "gpu", "auto"), "gpu")


class SelectRuntimeTests(unittest.TestCase):
    def test_no_installed_runtime_raises_with_a_setup_action(self) -> None:
        with patch(
            "image_triage.ai_env.load_ai_runtime_installation_status",
            return_value=_status(installed=()),
        ):
            with self.assertRaises(AIRuntimeUnavailable) as caught:
                ai_env.select_runtime("auto")

        self.assertEqual(caught.exception.category, "runtime_missing")
        self.assertIn("Set Up AI", caught.exception.remediation)

    def test_requiring_torch_rejects_a_base_only_install(self) -> None:
        # This is the field failure: Settings reported success after a base
        # install, then editor masking failed with missing torch.
        with patch(
            "image_triage.ai_env.load_ai_runtime_installation_status",
            return_value=_status(installed=("gpu",), torch_variants=()),
        ):
            with self.assertRaises(AIRuntimeUnavailable) as caught:
                ai_env.select_runtime("auto", require_torch=True)

        self.assertEqual(caught.exception.category, "runtime_incomplete")
        self.assertIn("PyTorch", str(caught.exception))

    def test_a_recorded_profile_with_no_files_is_reported_as_corrupt(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            status = _status(installed=("cpu",), preferred="cpu", root=Path(temp_dir))
            with patch(
                "image_triage.ai_env.load_ai_runtime_installation_status", return_value=status
            ):
                with self.assertRaises(AIRuntimeUnavailable) as caught:
                    ai_env.select_runtime("cpu")

        self.assertEqual(caught.exception.category, "runtime_corrupt")

    def test_selection_pins_a_concrete_device_never_auto(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "profiles" / "gpu" / "site-packages").mkdir(parents=True)
            status = _status(installed=("gpu",), preferred="gpu", root=root)
            with patch(
                "image_triage.ai_env.load_ai_runtime_installation_status", return_value=status
            ), patch("image_triage.ai_env._load_ai_runtime_metadata", return_value={}):
                selection = ai_env.select_runtime("auto")

        self.assertEqual(selection.device, "cuda")
        self.assertNotEqual(selection.device, "auto")
        self.assertEqual(selection.variant, "gpu")

    def test_an_indexed_gpu_request_selects_the_gpu_profile_and_keeps_the_index(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "profiles" / "gpu" / "site-packages").mkdir(parents=True)
            status = _status(installed=("gpu",), preferred="gpu", root=root)
            with patch(
                "image_triage.ai_env.load_ai_runtime_installation_status", return_value=status
            ), patch("image_triage.ai_env._load_ai_runtime_metadata", return_value={}):
                selection = ai_env.select_runtime("cuda:1")

        self.assertEqual(selection.variant, "gpu")
        self.assertEqual(selection.device, "cuda:1")

    def test_an_explicit_cpu_request_pins_cpu_even_on_a_gpu_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for variant in ("cpu", "gpu"):
                (root / "profiles" / variant / "site-packages").mkdir(parents=True)
            status = _status(installed=("cpu", "gpu"), preferred="gpu", root=root)
            with patch(
                "image_triage.ai_env.load_ai_runtime_installation_status", return_value=status
            ), patch("image_triage.ai_env._load_ai_runtime_metadata", return_value={}):
                selection = ai_env.select_runtime("cpu")

        self.assertEqual(selection.variant, "cpu")
        self.assertEqual(selection.device, "cpu")


class WorkerEnvironmentTests(unittest.TestCase):
    def _selection(self, site: Path) -> RuntimeSelection:
        return RuntimeSelection(
            variant="gpu",
            device="cuda",
            site_packages=(site,),
            profile_generation="gpu-abc123",
            root=site.parent.parent,
        )

    def test_managed_site_packages_come_first_on_pythonpath(self) -> None:
        selection = self._selection(Path("C:/managed/profiles/gpu-abc123/site-packages"))
        env = ai_env.build_worker_env(
            selection, base_env={"PYTHONPATH": os.pathsep.join(["C:/other", "C:/more"])}
        )

        entries = env["PYTHONPATH"].split(os.pathsep)
        self.assertEqual(entries[0], str(selection.primary_site_packages))
        self.assertEqual(entries[1:], ["C:/other", "C:/more"])

    def test_a_repeated_launch_does_not_stack_the_same_profile(self) -> None:
        selection = self._selection(Path("C:/managed/profiles/gpu-abc123/site-packages"))
        first = ai_env.build_worker_env(selection, base_env={})
        second = ai_env.build_worker_env(selection, base_env=first)

        entries = second["PYTHONPATH"].split(os.pathsep)
        self.assertEqual(entries.count(str(selection.primary_site_packages)), 1)

    def test_the_environment_isolates_the_worker_from_a_user_python(self) -> None:
        selection = self._selection(Path("C:/managed/profiles/gpu-abc123/site-packages"))
        env = ai_env.build_worker_env(selection, base_env={})

        self.assertEqual(env["PYTHONNOUSERSITE"], "1")
        self.assertEqual(env["HF_HUB_OFFLINE"], "1")
        self.assertEqual(env["TRANSFORMERS_OFFLINE"], "1")

    def test_the_selected_profile_and_device_travel_with_the_process(self) -> None:
        selection = self._selection(Path("C:/managed/profiles/gpu-abc123/site-packages"))
        env = ai_env.build_worker_env(selection, base_env={}, protocol_version=7)

        self.assertEqual(env[ai_env.AI_PROFILE_ENV], "gpu-abc123")
        self.assertEqual(env[ai_env.AI_DEVICE_ENV], "cuda")
        self.assertEqual(env[ai_env.AI_PROTOCOL_ENV], "7")

    def test_a_worker_reads_back_what_its_parent_pinned(self) -> None:
        selection = self._selection(Path("C:/managed/profiles/gpu-abc123/site-packages"))
        env = ai_env.build_worker_env(selection, base_env={}, protocol_version=3)
        with patch.dict(os.environ, env, clear=False):
            self.assertEqual(ai_env.worker_reported_profile(), "gpu-abc123")
            self.assertEqual(ai_env.worker_selected_device(), "cuda")
            self.assertEqual(ai_env.worker_protocol_version(), 3)
            ai_env.assert_worker_protocol(3)

    def test_a_protocol_mismatch_fails_loudly(self) -> None:
        with patch.dict(os.environ, {ai_env.AI_PROTOCOL_ENV: "1"}, clear=False):
            with self.assertRaises(AIRuntimeUnavailable) as caught:
                ai_env.assert_worker_protocol(2)

        self.assertEqual(caught.exception.category, "protocol_mismatch")
        self.assertIn("Restart", caught.exception.remediation)


if __name__ == "__main__":
    unittest.main()
