from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
import onnx
from onnx import TensorProto, helper, numpy_helper

from aiculler.topiq_onnx import prepare_topiq_model_for_providers


_MALFORMED_CONVS = {
    "node_Conv_1388": (64, 256),
    "node_Conv_1408": (128, 512),
    "node_Conv_1434": (256, 1024),
    "node_Conv_1472": (512, 2048),
}


def _write_topiq_fixture(path: Path, *, include_all_nodes: bool = True) -> None:
    nodes = []
    initializers = []
    outputs = []
    entries = list(_MALFORMED_CONVS.items())
    if not include_all_nodes:
        entries = entries[:-1]
    for index, (name, (output_channels, bias_channels)) in enumerate(entries):
        weight_name = f"weight_{index}"
        bias_name = f"bias_{index}"
        output_name = f"output_{index}"
        weights = np.zeros((output_channels, 3, 1, 1), dtype=np.float32)
        bias = np.arange(bias_channels, dtype=np.float32)
        initializers.extend(
            [
                numpy_helper.from_array(weights, name=weight_name),
                numpy_helper.from_array(bias, name=bias_name),
            ]
        )
        nodes.append(
            helper.make_node(
                "Conv",
                ["pixels", weight_name, bias_name],
                [output_name],
                name=name,
            )
        )
        outputs.append(
            helper.make_tensor_value_info(
                output_name,
                TensorProto.FLOAT,
                [1, output_channels, 4, 4],
            )
        )
    graph = helper.make_graph(
        nodes,
        "topiq_export_fixture",
        [helper.make_tensor_value_info("pixels", TensorProto.FLOAT, [1, 3, 4, 4])],
        outputs,
        initializer=initializers,
    )
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 18)])
    onnx.save(model, str(path))


class AICullerTopiqOnnxTests(unittest.TestCase):
    def test_cpu_provider_uses_original_model_without_writing_derived_graph(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "topiq_nr.onnx"
            _write_topiq_fixture(source)

            result = prepare_topiq_model_for_providers(source, ["CPUExecutionProvider"])

            self.assertEqual(source.resolve(), result.path)
            self.assertFalse(result.optimized)
            self.assertEqual([], list(source.parent.glob("*.image_triage_cuda_*.onnx")))

    def test_cuda_provider_repairs_all_known_biases_and_reuses_result(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "topiq_nr.onnx"
            _write_topiq_fixture(source)

            first = prepare_topiq_model_for_providers(
                source,
                ["CUDAExecutionProvider", "CPUExecutionProvider"],
            )

            self.assertTrue(first.optimized, first.detail)
            self.assertNotEqual(source.resolve(), first.path)
            self.assertTrue(first.path.is_file())
            repaired = onnx.load(str(first.path))
            initializers = {
                initializer.name: numpy_helper.to_array(initializer)
                for initializer in repaired.graph.initializer
            }
            nodes = {node.name: node for node in repaired.graph.node}
            for node_name, (output_channels, _bad_channels) in _MALFORMED_CONVS.items():
                repaired_bias = initializers[nodes[node_name].input[2]]
                self.assertEqual((output_channels,), repaired_bias.shape)

            metadata = json.loads(first.path.with_suffix(".json").read_text(encoding="utf-8"))
            self.assertEqual(1, metadata["repair_version"])
            second = prepare_topiq_model_for_providers(
                source,
                ["CUDAExecutionProvider", "CPUExecutionProvider"],
            )
            self.assertEqual(first.path, second.path)
            self.assertTrue(second.optimized)
            self.assertIn("reused", second.detail)

    def test_unknown_export_is_left_untouched_for_existing_cpu_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "other_topiq.onnx"
            _write_topiq_fixture(source, include_all_nodes=False)

            result = prepare_topiq_model_for_providers(
                source,
                ["CUDAExecutionProvider", "CPUExecutionProvider"],
            )

            self.assertEqual(source.resolve(), result.path)
            self.assertFalse(result.optimized)
            self.assertIn("does not match", result.detail)


if __name__ == "__main__":
    unittest.main()
