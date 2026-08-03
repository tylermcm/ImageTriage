from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


TOPIQ_CUDA_REPAIR_VERSION = 1
_MALFORMED_CONV_BIASES = {
    "node_Conv_1388": (64, 256),
    "node_Conv_1408": (128, 512),
    "node_Conv_1434": (256, 1024),
    "node_Conv_1472": (512, 2048),
}


@dataclass(frozen=True)
class TopiqModelPreparation:
    path: Path
    optimized: bool
    detail: str = ""


def prepare_topiq_model_for_providers(
    model_path: str | Path,
    providers: Sequence[str],
) -> TopiqModelPreparation:
    source = Path(model_path).expanduser().resolve()
    if "CUDAExecutionProvider" not in providers:
        return TopiqModelPreparation(source, False, "CUDA was not requested")

    target = source.with_name(f"{source.stem}.image_triage_cuda_v{TOPIQ_CUDA_REPAIR_VERSION}.onnx")
    metadata_path = target.with_suffix(".json")
    try:
        source_stat = source.stat()
        identity = {
            "repair_version": TOPIQ_CUDA_REPAIR_VERSION,
            "source_size": int(source_stat.st_size),
            "source_mtime_ns": int(source_stat.st_mtime_ns),
        }
        if target.is_file() and _metadata_matches(metadata_path, identity):
            return TopiqModelPreparation(target, True, "reused CUDA-compatible derived graph")
        repaired = _write_cuda_compatible_graph(source, target)
        if not repaired:
            return TopiqModelPreparation(source, False, "model does not match the known TOPIQ export issue")
        _write_metadata(metadata_path, identity)
        return TopiqModelPreparation(target, True, "generated CUDA-compatible derived graph")
    except Exception as exc:
        return TopiqModelPreparation(source, False, f"CUDA graph preparation failed: {exc}")


def _write_cuda_compatible_graph(source: Path, target: Path) -> bool:
    import numpy as np
    import onnx
    from onnx import numpy_helper

    model = onnx.load(str(source))
    initializers = {
        initializer.name: numpy_helper.to_array(initializer)
        for initializer in model.graph.initializer
    }
    matching_nodes = []
    for node in model.graph.node:
        expected = _MALFORMED_CONV_BIASES.get(node.name)
        if expected is None:
            continue
        if node.op_type != "Conv" or len(node.input) < 3:
            return False
        weights = initializers.get(node.input[1])
        bias = initializers.get(node.input[2])
        if weights is None or bias is None or weights.ndim < 1 or bias.ndim != 1:
            return False
        output_channels, malformed_bias_channels = expected
        if int(weights.shape[0]) != output_channels or int(bias.shape[0]) != malformed_bias_channels:
            return False
        matching_nodes.append((node, bias, output_channels))
    if len(matching_nodes) != len(_MALFORMED_CONV_BIASES):
        return False

    for node, bias, output_channels in matching_nodes:
        fixed_name = f"{node.name}_image_triage_bias_v{TOPIQ_CUDA_REPAIR_VERSION}"
        fixed_bias = np.ascontiguousarray(bias[:output_channels])
        model.graph.initializer.append(numpy_helper.from_array(fixed_bias, name=fixed_name))
        node.input[2] = fixed_name

    onnx.checker.check_model(model)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    try:
        onnx.save(model, str(temporary))
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return True


def _metadata_matches(path: Path, expected: dict[str, int]) -> bool:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return False
    return all(payload.get(key) == value for key, value in expected.items())


def _write_metadata(path: Path, payload: dict[str, int]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
