from __future__ import annotations

import unittest

from aiculler.onnx_runtime import create_onnx_session, preflight_onnx_session, preferred_onnx_providers


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
    def __init__(self, *, fail_cuda: bool = False) -> None:
        self.fail_cuda = fail_cuda
        self.preloaded = False
        self.calls: list[list[str]] = []

    def get_available_providers(self) -> list[str]:
        return ["CUDAExecutionProvider", "CPUExecutionProvider"]

    def preload_dlls(self) -> None:
        self.preloaded = True

    def InferenceSession(self, _path, _options, *, providers):
        selected = list(providers)
        self.calls.append(selected)
        if self.fail_cuda and "CUDAExecutionProvider" in selected:
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


if __name__ == "__main__":
    unittest.main()
