"""Capability probes and the central health/repair/diagnostics service."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from image_triage import ai_health, ai_manifest, ai_paths, ai_probe
from image_triage.ai_env import AIRuntimeUnavailable, RuntimeSelection
from image_triage.ai_model_store import BundleStatus, STATE_MISSING, STATE_READY
from image_triage.ai_probe import ProbeFailure, ProbeResult


def _selection(root: Path) -> RuntimeSelection:
    site = root / "profiles" / "gpu-abc123" / "site-packages"
    site.mkdir(parents=True, exist_ok=True)
    return RuntimeSelection(
        variant="gpu",
        device="cuda",
        site_packages=(site,),
        profile_generation="gpu-abc123",
        root=root,
    )


def _ready_bundle(key: str) -> BundleStatus:
    return BundleStatus(
        key=key,
        name=ai_manifest.MODEL_BUNDLES[key].name,
        install_dir=Path("C:/managed/models") / key,
        state=STATE_READY,
    )


def _missing_bundle(key: str) -> BundleStatus:
    return BundleStatus(
        key=key,
        name=ai_manifest.MODEL_BUNDLES[key].name,
        install_dir=Path("C:/managed/models") / key,
        state=STATE_MISSING,
        missing_files=ai_manifest.MODEL_BUNDLES[key].filenames,
    )


class ProbePathTests(unittest.TestCase):
    def test_a_missing_profile_directory_is_reported_not_swallowed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaises(ProbeFailure) as caught:
                ai_probe.configure_runtime_path(Path(temp_dir) / "absent")

        self.assertEqual(caught.exception.category, "runtime_missing")
        self.assertEqual(caught.exception.stage, ai_probe.STAGE_PATH)

    def test_the_profile_is_placed_first_on_sys_path(self) -> None:
        original = list(sys.path)
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                target = Path(temp_dir).resolve()
                ai_probe.configure_runtime_path(target)
                self.assertEqual(sys.path[0], str(target))
        finally:
            sys.path[:] = original

    def test_configuring_twice_does_not_duplicate_the_entry(self) -> None:
        original = list(sys.path)
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                target = Path(temp_dir).resolve()
                ai_probe.configure_runtime_path(target)
                ai_probe.configure_runtime_path(target)
                self.assertEqual(sys.path.count(str(target)), 1)
        finally:
            sys.path[:] = original


class ProbeImportTests(unittest.TestCase):
    def test_a_module_outside_the_profile_is_reported_as_shadowed(self) -> None:
        # An import that succeeds from the wrong location is worse than one
        # that fails, because the results look plausible.
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaises(ProbeFailure) as caught:
                ai_probe.import_modules(("json",), Path(temp_dir))

        self.assertEqual(caught.exception.category, "package_shadowed")

    def test_a_missing_module_is_reported_as_a_missing_package(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaises(ProbeFailure) as caught:
                ai_probe.import_modules(("image_triage_absent_module",), Path(temp_dir))

        self.assertEqual(caught.exception.category, "package_missing")
        self.assertEqual(caught.exception.stage, ai_probe.STAGE_IMPORT)

    def test_run_probe_never_raises_and_reports_the_stage(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            result = ai_probe.run_probe(
                capability_key="culling",
                site_packages=Path(temp_dir) / "absent",
                modules=("numpy",),
                probe_kind="import",
            )

        self.assertFalse(result.ok)
        self.assertEqual(result.stage, ai_probe.STAGE_PATH)
        self.assertEqual(result.category, "runtime_missing")
        self.assertGreaterEqual(result.duration_ms, 0)

    def test_a_probe_result_round_trips_through_json(self) -> None:
        result = ProbeResult(capability="culling", ok=True, selected_device="cpu")
        payload = json.loads(result.to_json())
        self.assertEqual(payload["capability"], "culling")
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["protocol_version"], ai_probe.PROBE_PROTOCOL_VERSION)


class ProbeProviderTests(unittest.TestCase):
    class _FakeOnnx:
        def __init__(self, providers: list[str]) -> None:
            self._providers = providers

        def get_available_providers(self) -> list[str]:
            return list(self._providers)

    def test_a_gpu_request_without_a_cuda_provider_fails_clearly(self) -> None:
        result = ProbeResult(capability="culling")
        fake = self._FakeOnnx(["CPUExecutionProvider"])
        with patch.dict(sys.modules, {"onnxruntime": fake}):
            with self.assertRaises(ProbeFailure) as caught:
                ai_probe.probe_onnx_providers(result, "cuda")

        self.assertEqual(caught.exception.category, "provider_unavailable")
        self.assertIn("NVIDIA driver", caught.exception.message)

    def test_a_cpu_request_succeeds_without_cuda(self) -> None:
        result = ProbeResult(capability="culling")
        fake = self._FakeOnnx(["CPUExecutionProvider"])
        with patch.dict(sys.modules, {"onnxruntime": fake}):
            ai_probe.probe_onnx_providers(result, "cpu")

        self.assertEqual(result.selected_device, "cpu")
        self.assertEqual(result.providers_available, ["CPUExecutionProvider"])

    def test_no_providers_at_all_is_a_failure(self) -> None:
        result = ProbeResult(capability="culling")
        with patch.dict(sys.modules, {"onnxruntime": self._FakeOnnx([])}):
            with self.assertRaises(ProbeFailure):
                ai_probe.probe_onnx_providers(result, "cpu")

    def test_a_transformers_build_missing_a_required_class_is_named(self) -> None:
        class _Fake:
            __version__ = "5.0.0"

        with patch.dict(sys.modules, {"transformers": _Fake()}):
            with self.assertRaises(ProbeFailure) as caught:
                ai_probe.probe_transformers_classes(("Sam2Model",))

        self.assertEqual(caught.exception.category, "package_incompatible")
        self.assertIn("Sam2Model", caught.exception.message)


class ProbeModelTests(unittest.TestCase):
    def test_full_subject_probe_runs_the_production_worker(self) -> None:
        result = ProbeResult(capability="subject_masks", selected_device="cpu")

        def fake_generate(**kwargs):
            kwargs["output_path"].write_bytes(b"png")
            return {"device": kwargs["requested_device"]}

        with patch(
            "image_triage.birefnet_worker.generate_subject_mask",
            side_effect=fake_generate,
        ) as generate:
            ai_probe._probe_production_torch_worker(
                result, "subject_masks", Path("C:/models/subject"), "cpu"
            )

        self.assertTrue(result.inference_ran)
        self.assertEqual(generate.call_args.kwargs["requested_device"], "cpu")
        self.assertFalse(generate.call_args.kwargs["emit_result"])

    def test_production_probe_rejects_a_worker_that_writes_no_output(self) -> None:
        result = ProbeResult(capability="depth", selected_device="cpu")
        with patch(
            "image_triage.depth_worker.generate_depth",
            return_value={"device": "cpu"},
        ):
            with self.assertRaises(ProbeFailure) as caught:
                ai_probe._probe_production_torch_worker(
                    result, "depth", Path("C:/models/depth"), "cpu"
                )

        self.assertEqual(caught.exception.stage, ai_probe.STAGE_INFERENCE)
        self.assertFalse(result.inference_ran)

    def test_face_probe_executes_every_model_in_the_pack(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            model_dir = Path(temp_dir)
            for name in ("det.onnx", "genderage.onnx", "glintr100.onnx"):
                (model_dir / name).write_bytes(b"model")
            result = ProbeResult(capability="faces")
            with patch.dict(sys.modules, {"insightface": object()}), patch.object(
                ai_probe, "probe_onnx_model"
            ) as probe_model:
                ai_probe.probe_insightface(result, model_dir, "cpu")

        self.assertEqual(probe_model.call_count, 3)
        recognition_call = next(
            call for call in probe_model.call_args_list if call.args[1].name == "glintr100.onnx"
        )
        self.assertEqual(recognition_call.kwargs["expected_embedding_dim"], 512)


class HealthServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = ai_health.AIHealthService()
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name)
        self.addCleanup(self._temp.cleanup)

    def test_a_missing_runtime_reports_the_runtime_stage_and_setup_action(self) -> None:
        with patch(
            "image_triage.ai_health.select_runtime",
            side_effect=AIRuntimeUnavailable("not installed", category="runtime_missing"),
        ):
            health = self.service.check("culling", use_cache=False)

        self.assertFalse(health.ready)
        self.assertEqual(health.stage, ai_health.STAGE_RUNTIME)
        self.assertEqual(health.action, "setup")
        self.assertNotIn("Traceback", health.headline())

    def test_a_missing_model_bundle_reports_the_model_stage(self) -> None:
        with patch("image_triage.ai_health.select_runtime", return_value=_selection(self.root)), patch(
            "image_triage.ai_health.bundle_status", side_effect=lambda key, deep=False: _missing_bundle(key)
        ):
            health = self.service.check("quality_topiq", use_cache=False)

        self.assertFalse(health.ready)
        self.assertEqual(health.stage, ai_health.STAGE_MODELS)
        self.assertEqual(health.action, "download")
        self.assertIn("TOPIQ", health.message)

    def test_a_ready_capability_reports_the_device_it_actually_got(self) -> None:
        payload = {
            "ok": True,
            "selected_device": "cpu",
            "providers_available": ["CPUExecutionProvider"],
            "providers_active": ["CPUExecutionProvider"],
            "modules": {"numpy": "2.1.0"},
            "duration_ms": 42,
        }
        with patch("image_triage.ai_health.select_runtime", return_value=_selection(self.root)), patch(
            "image_triage.ai_health.bundle_status", side_effect=lambda key, deep=False: _ready_bundle(key)
        ), patch.object(self.service, "_run_probe_subprocess", return_value=payload):
            health = self.service.check("culling", device="cpu", use_cache=False)

        self.assertTrue(health.ready)
        self.assertEqual(health.stage, ai_health.STAGE_READY)
        self.assertEqual(health.selected_device, "cpu")
        self.assertEqual(health.profile_id, "gpu-abc123")
        self.assertIn("CPU", health.headline())

    def test_a_provider_failure_offers_the_switch_to_cpu_action(self) -> None:
        payload = {
            "ok": False,
            "category": "provider_unavailable",
            "message": "no CUDA provider",
            "providers_available": ["CPUExecutionProvider"],
        }
        with patch("image_triage.ai_health.select_runtime", return_value=_selection(self.root)), patch(
            "image_triage.ai_health.bundle_status", side_effect=lambda key, deep=False: _ready_bundle(key)
        ), patch.object(self.service, "_run_probe_subprocess", return_value=payload):
            health = self.service.check("culling", device="cuda", use_cache=False)

        self.assertFalse(health.ready)
        self.assertEqual(health.action, "cpu")
        self.assertIn("CPU", health.remediation)

    def test_one_failing_optional_capability_does_not_fail_the_others(self) -> None:
        def probe(capability, selection, requested_device, *, level="quick"):
            if capability.key == "depth":
                return {"ok": False, "category": "model_missing", "message": "no depth model"}
            return {"ok": True, "selected_device": "cpu"}

        with patch("image_triage.ai_health.select_runtime", return_value=_selection(self.root)), patch(
            "image_triage.ai_health.bundle_status", side_effect=lambda key, deep=False: _ready_bundle(key)
        ), patch.object(self.service, "_run_probe_subprocess", side_effect=probe):
            results = self.service.check_all(("culling", "depth", "faces"), use_cache=False)

        self.assertTrue(results["culling"].ready)
        self.assertTrue(results["faces"].ready)
        self.assertFalse(results["depth"].ready)

    def test_probe_results_are_cached_per_capability_and_profile(self) -> None:
        calls: list[str] = []

        def probe(capability, selection, requested_device, *, level="quick"):
            calls.append(capability.key)
            return {"ok": True, "selected_device": "cpu"}

        with patch("image_triage.ai_health.select_runtime", return_value=_selection(self.root)), patch(
            "image_triage.ai_health.bundle_status", side_effect=lambda key, deep=False: _ready_bundle(key)
        ), patch.object(self.service, "_run_probe_subprocess", side_effect=probe):
            self.service.check("culling", use_cache=True)
            self.service.check("culling", use_cache=True)
            self.assertEqual(len(calls), 1)

            self.service.invalidate("culling")
            self.service.check("culling", use_cache=True)
            self.assertEqual(len(calls), 2)

    def test_a_probe_timeout_is_reported_as_a_retryable_failure(self) -> None:
        with patch("image_triage.ai_health.select_runtime", return_value=_selection(self.root)), patch(
            "image_triage.ai_health.bundle_status", side_effect=lambda key, deep=False: _ready_bundle(key)
        ), patch(
            "image_triage.ai_health.subprocess.run",
            side_effect=subprocess.TimeoutExpired("probe", 1),
        ):
            health = self.service.check("culling", use_cache=False)

        self.assertFalse(health.ready)
        self.assertEqual(health.category, "probe_timeout")
        self.assertEqual(health.action, "retry")

    def test_a_probe_that_prints_nothing_usable_is_reported_not_crashed(self) -> None:
        completed = subprocess.CompletedProcess([], 1, stdout="loading...\n", stderr="boom")
        with patch("image_triage.ai_health.select_runtime", return_value=_selection(self.root)), patch(
            "image_triage.ai_health.bundle_status", side_effect=lambda key, deep=False: _ready_bundle(key)
        ), patch("image_triage.ai_health.subprocess.run", return_value=completed):
            health = self.service.check("culling", use_cache=False)

        self.assertFalse(health.ready)
        self.assertEqual(health.category, "probe_error")

    def test_probe_output_is_parsed_even_with_library_chatter_around_it(self) -> None:
        payload = ProbeResult(capability="culling", ok=True).to_json()
        stdout = f"warning: something\n{payload}\n"
        parsed = ai_health._parse_probe_output(stdout)

        self.assertIsNotNone(parsed)
        self.assertTrue(parsed["ok"])

    def test_the_probe_command_targets_the_frozen_helper_when_frozen(self) -> None:
        capability = ai_manifest.CAPABILITIES["culling"]
        selection = _selection(self.root)
        with patch.object(sys, "frozen", True, create=True), patch.object(
            sys, "executable", str(self.root / "ImageTriage.exe")
        ):
            command = ai_health._probe_command(capability, selection, "auto")

        self.assertIn("ai_runtime_installer", command[0])
        self.assertIn("probe", command)
        self.assertIn("--capability", command)


class ProbeIdentityTests(unittest.TestCase):
    """A probe result must prove it came from this invocation."""

    def setUp(self) -> None:
        self.service = ai_health.AIHealthService()
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name)
        self.selection = _selection(self.root)
        self.capability = ai_manifest.CAPABILITIES["culling"]
        self.addCleanup(self._temp.cleanup)

    def _payload(self, **overrides) -> dict:
        payload = {
            "capability": "culling",
            "ok": True,
            "protocol_version": ai_probe.PROBE_PROTOCOL_VERSION,
            "profile": self.selection.profile_id,
            "site_packages": str(self.selection.primary_site_packages),
            "requested_device": self.selection.device,
        }
        payload.update(overrides)
        return payload

    def _problem(self, payload: dict, *, returncode: int = 0) -> str | None:
        return ai_health._probe_identity_problem(
            payload,
            capability=self.capability,
            selection=self.selection,
            requested_device=self.selection.device,
            returncode=returncode,
        )

    def test_a_matching_result_is_accepted(self) -> None:
        self.assertIsNone(self._problem(self._payload()))

    def test_a_stale_protocol_version_is_rejected(self) -> None:
        problem = self._problem(self._payload(protocol_version=0))
        self.assertIn("protocol", problem or "")

    def test_a_result_for_another_capability_is_rejected(self) -> None:
        problem = self._problem(self._payload(capability="depth"))
        self.assertIn("depth", problem or "")

    def test_a_result_from_another_profile_is_rejected(self) -> None:
        problem = self._problem(self._payload(profile="gpu-other"))
        self.assertIn("profile", problem or "")

    def test_a_result_from_another_runtime_directory_is_rejected(self) -> None:
        problem = self._problem(self._payload(site_packages="C:/elsewhere/site-packages"))
        self.assertIn("managed runtime", problem or "")

    def test_a_result_for_another_device_is_rejected(self) -> None:
        problem = self._problem(self._payload(requested_device="cpu"))
        self.assertIn("cpu", problem or "")

    def test_success_with_a_non_zero_exit_is_rejected(self) -> None:
        problem = self._problem(self._payload(), returncode=1)
        self.assertIn("exited", problem or "")

    def test_an_unusable_result_becomes_a_probe_error(self) -> None:
        completed = subprocess.CompletedProcess(
            [], 0, stdout=json.dumps(self._payload(capability="depth")), stderr=""
        )
        with patch("image_triage.ai_health.subprocess.run", return_value=completed):
            payload = self.service._run_probe_subprocess(
                self.capability, self.selection, self.selection.device
            )

        self.assertFalse(payload["ok"])
        self.assertEqual(payload["category"], "probe_error")


class ProbeDeviceTests(unittest.TestCase):
    """Demo Ready must approve the device the workers are actually pinned to."""

    def setUp(self) -> None:
        self.service = ai_health.AIHealthService()
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name)
        self.addCleanup(self._temp.cleanup)

    def test_an_auto_request_probes_the_resolved_device_not_auto(self) -> None:
        seen: list[str] = []

        def probe(capability, selection, requested_device, *, level="quick"):
            seen.append(requested_device)
            return {"ok": True, "selected_device": requested_device}

        selection = _selection(self.root)  # resolves to cuda
        with patch("image_triage.ai_health.select_runtime", return_value=selection), patch(
            "image_triage.ai_health.bundle_status", side_effect=lambda key, deep=False: _ready_bundle(key)
        ), patch.object(self.service, "_run_probe_subprocess", side_effect=probe):
            self.service.check("culling", device="auto", use_cache=False)

        self.assertEqual(seen, ["cuda"])
        self.assertNotIn("auto", seen)

    def test_the_probe_command_carries_the_level(self) -> None:
        capability = ai_manifest.CAPABILITIES["scene_masks"]
        selection = _selection(self.root)
        command = ai_health._probe_command(capability, selection, "cuda", level="full")

        self.assertIn("--level", command)
        self.assertEqual(command[command.index("--level") + 1], "full")


class GateTests(unittest.TestCase):
    """A feature gate must not pay for a subprocess probe."""

    def setUp(self) -> None:
        self.service = ai_health.AIHealthService()
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name)
        self.addCleanup(self._temp.cleanup)

    def test_probe_false_answers_without_spawning_anything(self) -> None:
        def explode(*args, **kwargs):
            raise AssertionError("the gate must not run a probe")

        with patch("image_triage.ai_health.select_runtime", return_value=_selection(self.root)), patch(
            "image_triage.ai_health.bundle_status", side_effect=lambda key, deep=False: _ready_bundle(key)
        ), patch.object(self.service, "_run_probe_subprocess", side_effect=explode):
            health = self.service.check("scene_masks", probe=False)

        self.assertTrue(health.ready)
        self.assertEqual(health.probe_level, "none")

    def test_the_gate_still_fails_on_a_missing_model(self) -> None:
        with patch("image_triage.ai_health.select_runtime", return_value=_selection(self.root)), patch(
            "image_triage.ai_health.bundle_status", side_effect=lambda key, deep=False: _missing_bundle(key)
        ):
            health = self.service.check("scene_masks", probe=False)

        self.assertFalse(health.ready)
        self.assertEqual(health.stage, ai_health.STAGE_MODELS)


class RepairRoutingTests(unittest.TestCase):
    """Repair must not tell users to run the thing that cannot fix them."""

    def setUp(self) -> None:
        self.service = ai_health.AIHealthService()
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name)
        self.addCleanup(self._temp.cleanup)

    def test_package_failures_direct_users_to_setup_not_repair(self) -> None:
        for category in ("package_missing", "package_broken", "package_shadowed"):
            with self.subTest(category=category):
                self.assertEqual(ai_health._ACTIONS[category], "setup")

    def test_repair_flags_a_runtime_reinstall_when_packages_are_broken(self) -> None:
        broken = {"ok": False, "category": "package_broken", "message": "torch is unusable"}
        with patch("image_triage.ai_health.select_runtime", return_value=_selection(self.root)), patch(
            "image_triage.ai_health.bundle_status", side_effect=lambda key, deep=False: _ready_bundle(key)
        ), patch.object(self.service, "_run_probe_subprocess", return_value=broken):
            health = self.service.repair("scene_masks")

        self.assertFalse(health.ready)
        self.assertTrue(health.runtime_repair_required)
        self.assertEqual(health.action, "setup")
        self.assertIn("Set Up AI", health.remediation)

    def test_repair_all_reports_that_a_runtime_reinstall_is_needed(self) -> None:
        broken = {"ok": False, "category": "package_missing", "message": "torch is missing"}
        with patch("image_triage.ai_health.select_runtime", return_value=_selection(self.root)), patch(
            "image_triage.ai_health.bundle_status", side_effect=lambda key, deep=False: _ready_bundle(key)
        ), patch.object(self.service, "_run_probe_subprocess", return_value=broken):
            _results, runtime_required = self.service.repair_all(("scene_masks",))

        self.assertTrue(runtime_required)

    def test_a_model_only_failure_does_not_demand_a_runtime_reinstall(self) -> None:
        self.assertFalse(ai_health._needs_runtime_reinstall("bundle_missing"))
        self.assertFalse(ai_health._needs_runtime_reinstall("provider_unavailable"))


class SetupCapabilityTests(unittest.TestCase):
    """Setup must install exactly the set it goes on to verify."""

    def test_the_verified_set_matches_the_installed_set(self) -> None:
        from image_triage.ai_manifest import (
            BASE_CAPABILITIES,
            OPT_IN_CAPABILITIES,
            TORCH_CAPABILITIES,
            setup_capabilities,
        )

        base = setup_capabilities(include_torch=False)
        full = setup_capabilities(include_torch=True)

        self.assertEqual(base, BASE_CAPABILITIES)
        self.assertEqual(full, BASE_CAPABILITIES + TORCH_CAPABILITIES)
        for key in OPT_IN_CAPABILITIES:
            self.assertNotIn(key, full, msg=f"{key} is opt-in and must not be verified")

    def test_every_setup_capability_has_an_installable_bundle_path(self) -> None:
        from image_triage.ai_manifest import CAPABILITIES, MODEL_BUNDLES, setup_capabilities

        for key in setup_capabilities(include_torch=True):
            for bundle_key in CAPABILITIES[key].model_bundles:
                with self.subTest(capability=key):
                    self.assertIn(bundle_key, MODEL_BUNDLES)


class DiagnosticsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = ai_health.AIHealthService()
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name)
        self.addCleanup(self._temp.cleanup)

    def _results(self) -> dict[str, ai_health.CapabilityHealth]:
        with patch(
            "image_triage.ai_health.select_runtime",
            side_effect=AIRuntimeUnavailable("not installed", category="runtime_missing"),
        ):
            return self.service.check_all(("culling", "depth"), use_cache=False)

    def test_diagnostics_text_is_actionable_and_carries_no_traceback(self) -> None:
        text = self.service.diagnostics_text(self._results())

        self.assertIn("AI culling", text)
        self.assertIn("action:", text)
        self.assertNotIn("Traceback (most recent call last)", text)

    def test_diagnostics_redact_the_user_identity(self) -> None:
        env = {"USERPROFILE": r"C:\Users\jsmith", "USERNAME": "jsmith"}
        with patch.dict(os.environ, env, clear=False):
            payload = self.service.diagnostics(self._results())

        self.assertNotIn("jsmith", json.dumps(payload))

    def test_diagnostics_are_written_to_the_managed_log_directory(self) -> None:
        with patch.dict(os.environ, {ai_paths.AI_ROOT_ENV: str(self.root)}, clear=False):
            path = self.service.write_diagnostics(self._results())

            self.assertTrue(path.is_file())
            self.assertEqual(path.parent, ai_paths.managed_logs_root())
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(len(payload["capabilities"]), 2)

    def test_require_capability_raises_with_the_remediation(self) -> None:
        with patch(
            "image_triage.ai_health.select_runtime",
            side_effect=AIRuntimeUnavailable("not installed", category="runtime_missing"),
        ):
            with self.assertRaises(AIRuntimeUnavailable) as caught:
                ai_health.require_capability("scene_masks")

        self.assertTrue(caught.exception.remediation)


class ReadinessSummaryTests(unittest.TestCase):
    def _health(self, key: str, ready: bool) -> ai_health.CapabilityHealth:
        capability = ai_manifest.CAPABILITIES[key]
        return ai_health.CapabilityHealth(
            key=key,
            name=capability.name,
            summary=capability.summary,
            ready=ready,
            stage=ai_health.STAGE_READY if ready else ai_health.STAGE_MODELS,
            selected_device="cpu",
            optional=capability.optional,
            message="" if ready else "missing model",
            remediation="" if ready else "Download it.",
        )

    def test_summary_is_honest_when_anything_is_missing(self) -> None:
        from image_triage.ui.ai_readiness import summarize

        results = {
            "culling": self._health("culling", True),
            "depth": self._health("depth", False),
        }
        text = summarize(results)

        self.assertIn("1 of 2", text)
        self.assertIn("need attention", text)

    def test_summary_reports_success_only_when_everything_is_ready(self) -> None:
        from image_triage.ui.ai_readiness import summarize

        results = {"culling": self._health("culling", True)}
        self.assertIn("All 1 AI features are ready", summarize(results))

    def test_failure_message_leads_with_the_capability_not_a_traceback(self) -> None:
        from image_triage.ui.ai_readiness import failure_message

        text = failure_message(self._health("depth", False))

        self.assertTrue(text.startswith("missing model"))
        self.assertIn("Stage:", text)
        self.assertIn("Download it.", text)


if __name__ == "__main__":
    unittest.main()
