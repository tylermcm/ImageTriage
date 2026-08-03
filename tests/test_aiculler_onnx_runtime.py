from __future__ import annotations

import unittest

from aiculler.onnx_runtime import (
    create_onnx_session,
    create_preflight_session_with_model_fallback,
    preflight_onnx_session,
    preferred_onnx_providers,
)


class _InputMeta:
    name = "pixels"
    shape = ["batch", 3, 4, 4]
    type = "tensor(float)"


class _Session:
    def __init__(self, providers: list[str], *, fail_run: bool = False) -> None:
        self.providers = providers
        self.fail_run = fail_run

    def get_providers(self) -> list[str]:
        return self.providers

    def get_inputs(self) -> list[_InputMeta]:
        return [_InputMeta()]

    def run(self, _outputs, _inputs):
        if self.fail_run:
            raise RuntimeError("model is not CUDA compatible")
        return [None]


class _FakeOrt:
    def __init__(self, *, fail_cuda: bool = False, fail_cuda_paths: tuple[str, ...] = ()) -> None:
        self.fail_cuda = fail_cuda
        self.fail_cuda_paths = set(fail_cuda_paths)
        self.preloaded = False
        self.calls: list[list[str]] = []

    def get_available_providers(self) -> list[str]:
        return ["CUDAExecutionProvider", "CPUExecutionProvider"]

    def preload_dlls(self) -> None:
        self.preloaded = True

    def InferenceSession(self, path, _options, *, providers):
        selected = list(providers)
        self.calls.append(selected)
        if (
            "CUDAExecutionProvider" in selected
            and (self.fail_cuda or str(path) in self.fail_cuda_paths)
        ):
            raise RuntimeError("CUDA provider failed")
        return _Session(selected)


class AICullerOnnxRuntimeTests(unittest.TestCase):
    def test_preferred_providers_preload_and_prioritize_cuda(self) -> None:
        runtime = _FakeOrt()

        providers = preferred_onnx_providers(runtime)

        self.assertTrue(runtime.preloaded)
        self.assertEqual(["CUDAExecutionProvider", "CPUExecutionProvider"], providers)

    def test_session_creation_falls_back_to_cpu_when_cuda_initialization_fails(self) -> None:
        runtime = _FakeOrt(fail_cuda=True)

        session = create_onnx_session(runtime, "model.onnx", object())

        self.assertEqual(["CPUExecutionProvider"], session.get_providers())
        self.assertEqual(
            [
                ["CUDAExecutionProvider", "CPUExecutionProvider"],
                ["CPUExecutionProvider"],
            ],
            runtime.calls,
        )

    def test_preflight_falls_back_when_exact_model_fails_on_cuda(self) -> None:
        runtime = _FakeOrt()
        session = _Session(["CUDAExecutionProvider", "CPUExecutionProvider"], fail_run=True)

        selected = preflight_onnx_session(
            runtime,
            session,
            "model.onnx",
            object(),
            input_name="pixels",
        )

        self.assertEqual(["CPUExecutionProvider"], selected.get_providers())
        self.assertEqual([["CPUExecutionProvider"]], runtime.calls)

    def test_model_fallback_is_tried_on_cuda_before_cpu(self) -> None:
        runtime = _FakeOrt(fail_cuda_paths=("primary.onnx",))

        selected = create_preflight_session_with_model_fallback(
            runtime,
            "primary.onnx",
            "fallback.onnx",
            object(),
            providers=None,
            select_input_name=lambda _session: "pixels",
            input_shape=lambda _session: (1, 3, 4, 4),
        )

        self.assertTrue(selected.fallback_used)
        self.assertTrue(str(selected.model_path).endswith("fallback.onnx"))
        self.assertEqual(
            [
                ["CUDAExecutionProvider", "CPUExecutionProvider"],
                ["CUDAExecutionProvider", "CPUExecutionProvider"],
            ],
            runtime.calls,
        )


if __name__ == "__main__":
    unittest.main()
