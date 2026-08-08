from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import image_triage.semantic_mask_service as service_module
from image_triage.semantic_mask_service import (
    OneFormerWorkerService,
    SemanticMaskWarmTask,
    SemanticWorkerResult,
    _WorkerTransportError,
    validate_semantic_runtime,
)


def _infer_result() -> dict[str, object]:
    return {
        "device": "cuda",
        "sourceSize": [6, 4],
        "categoryStats": {"sky": {"coverage": 0.5, "sourceLabels": ["sky"]}},
        "timingsMs": {"inference": 12.0, "preprocess": 3.0},
    }


class _FakeStdout:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def readline(self) -> str:
        return self.lines.pop(0) if self.lines else ""

    def close(self) -> None:
        pass


class _FakeStdin:
    def __init__(self, process: "_FakeProcess") -> None:
        self.process = process

    def write(self, raw_request: str) -> int:
        request = json.loads(raw_request)
        command = str(request["command"])
        self.process.commands.append(command)
        if self.process.write_error is not None:
            error = self.process.write_error
            self.process.write_error = None
            raise error
        result: dict[str, object] = {"device": "cuda", "stage": command}
        if command == "infer":
            result = _infer_result()
        response_id = request["id"] if not self.process.force_bad_id else request["id"] + 999
        response = {"id": response_id, "ok": True, "result": result, "error": ""}
        self.process.stdout.lines.append("RESPONSE " + json.dumps(response) + "\n")
        return len(raw_request)

    def flush(self) -> None:
        pass

    def close(self) -> None:
        pass


class _FakeProcess:
    def __init__(self, *, pid: int = 4242) -> None:
        self.pid = pid
        self.stdout = _FakeStdout()
        self.stdin = _FakeStdin(self)
        self.stopped = False
        self.commands: list[str] = []
        self.write_error: Exception | None = None
        self.force_bad_id = False

    def poll(self):
        return 0 if self.stopped else None

    def wait(self, timeout=None):
        self.stopped = True
        return 0

    def terminate(self) -> None:
        self.stopped = True

    def kill(self) -> None:
        self.stopped = True


class OneFormerWorkerServiceTests(unittest.TestCase):
    def _service_with_process(self) -> tuple[OneFormerWorkerService, _FakeProcess]:
        service = OneFormerWorkerService(idle_timeout_seconds=3600)
        process = _FakeProcess()
        service._process = process  # type: ignore[assignment]
        return service, process

    def test_persistent_worker_reuses_imports_model_and_process(self) -> None:
        service, process = self._service_with_process()
        model_dir = Path("model").resolve()
        service.warm_imports()
        service.warm_imports()
        service.warm_model(model_dir)
        service.warm_model(model_dir)
        for index in range(2):
            result = service.infer(
                model_dir=model_dir,
                input_path=Path(f"input-{index}.png"),
                output_dir=Path(f"masks-{index}"),
                categories=("sky", "water"),
                minimum_coverage=0.0005,
                progress_callback=None,
            )
            self.assertIsInstance(result, SemanticWorkerResult)
            self.assertEqual("cuda", result.device)
            self.assertEqual((6, 4), result.source_size)
            self.assertEqual({"sky"}, set(result.category_stats))
            self.assertEqual(12.0, result.timings_ms["inference"])
        service.shutdown()

        self.assertEqual(
            ["warm-imports", "load-model", "infer", "infer", "shutdown"],
            process.commands,
        )

    def test_changing_the_model_directory_reloads_the_model(self) -> None:
        service, process = self._service_with_process()
        service.warm_model(Path("model-a").resolve())
        service.warm_model(Path("model-b").resolve())
        self.assertEqual(
            ["warm-imports", "load-model", "load-model"],
            process.commands,
        )

    def test_out_of_sequence_response_is_rejected(self) -> None:
        service, process = self._service_with_process()
        process.force_bad_id = True
        with self.assertRaises(_WorkerTransportError):
            service._send_command_locked({"command": "warm-imports"})

    def test_broken_pipe_retries_once_then_succeeds(self) -> None:
        service = OneFormerWorkerService(idle_timeout_seconds=3600)
        first = _FakeProcess(pid=1)
        first.write_error = BrokenPipeError("pipe closed")
        second = _FakeProcess(pid=2)
        # ``first`` is installed manually below; the reaper only supplies the
        # replacement process on respawn.
        spawned = iter((second,))

        def fake_ensure_locked() -> None:
            if service._process is None or service._process.poll() is not None:
                service._process = next(spawned)  # type: ignore[assignment]

        service._ensure_process_locked = fake_ensure_locked  # type: ignore[assignment]
        service._process = first  # type: ignore[assignment]

        result = service.infer(
            model_dir=Path("model").resolve(),
            input_path=Path("input.png"),
            output_dir=Path("masks"),
            categories=("sky",),
            minimum_coverage=0.0005,
            progress_callback=None,
        )

        # The dead worker took the first write; the respawned worker reloaded
        # the model and served the retry.
        self.assertEqual("cuda", result.device)
        self.assertIn("load-model", second.commands)
        self.assertIn("infer", second.commands)

    def test_worker_error_response_surfaces_as_runtime_error(self) -> None:
        service = OneFormerWorkerService(idle_timeout_seconds=3600)
        process = _FakeProcess()
        service._process = process  # type: ignore[assignment]

        def failing_write(raw_request: str) -> int:
            request = json.loads(raw_request)
            response = {
                "id": request["id"],
                "ok": False,
                "result": {},
                "error": "boom",
            }
            process.stdout.lines.append("RESPONSE " + json.dumps(response) + "\n")
            return len(raw_request)

        process.stdin.write = failing_write  # type: ignore[assignment]
        with self.assertRaisesRegex(RuntimeError, "boom"):
            service._send_command_locked({"command": "warm-imports"})


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
                with self.assertRaisesRegex(RuntimeError, "transformers"):
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
        with tempfile.TemporaryDirectory() as temp_dir:
            original = service_module.resolve_segmentation_model_installation
            spawned: list[str] = []
            original_service = service_module.default_oneformer_worker_service

            class _Missing:
                is_installed = False
                install_dir = Path(temp_dir)

            def _guard_service():
                spawned.append("service")
                return original_service()

            service_module.resolve_segmentation_model_installation = lambda: _Missing()
            service_module.default_oneformer_worker_service = _guard_service
            try:
                # Neither stage may raise or spawn a worker when uninstalled —
                # the render-time "imports" warm must be a no-op with no model.
                SemanticMaskWarmTask("model").run()
                SemanticMaskWarmTask("imports").run()
            finally:
                service_module.resolve_segmentation_model_installation = original
                service_module.default_oneformer_worker_service = original_service
            self.assertEqual([], spawned)


if __name__ == "__main__":
    unittest.main()
