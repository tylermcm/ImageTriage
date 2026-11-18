"""Convert the RawNIND Bayer denoiser checkpoint to the ONNX file AI Denoise loads (``rawnind_bayer.onnx``).

Run by a developer, once per model update, with PyTorch, onnx and onnxruntime installed and a checkout of the authors' code for the
network definition (it is not shipped):

    git clone https://github.com/trougnouf/rawnind_jddc
    python scripts/export_rawnind_onnx.py --code rawnind_jddc/src --checkpoint iter_4350000.pt --out rawnind_bayer.onnx

The checkpoint is ``DenoiserTrainingBayerToProfiledRGB_4ch_2024-02-21-bayer_ms-ssim_mgout_notrans_valeither_-4/saved_models/
iter_4350000.pt`` from the authors' model folder linked in the rawnind_jddc README (weights dual licensed GPL-3 + CC-BY-4.0; we use
them under CC-BY-4.0). Update ``ai_model.RAWNIND_MODEL_SHA256`` and ``ai_denoise.MODEL_VERSION`` with the printed values.
"""
import argparse
import hashlib
import sys


def load_utnet2(code: str):
    """The network class from ``rawnind/models/raw_denoiser.py`` alone. The ``rawnind`` package imports its whole training stack
    (configargparse, datasets, ...) when it loads; the network file needs only ``rawnind.libs.rawproc``, and UtNet2 never calls it."""
    import importlib.util
    import types
    from pathlib import Path

    for name in ("rawnind", "rawnind.libs", "rawnind.libs.rawproc"):
        sys.modules.setdefault(name, types.ModuleType(name))
    sys.modules["rawnind"].libs = sys.modules["rawnind.libs"]
    sys.modules["rawnind.libs"].rawproc = sys.modules["rawnind.libs.rawproc"]
    spec = importlib.util.spec_from_file_location("raw_denoiser", Path(code) / "rawnind" / "models" / "raw_denoiser.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.UtNet2


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--code", required=True, help="rawnind_jddc/src")
    parser.add_argument("--checkpoint", required=True)
    parser.add_argument("--out", default="rawnind_bayer.onnx")
    args = parser.parse_args()
    import numpy as np
    import onnxruntime as ort
    import torch
    UtNet2 = load_utnet2(args.code)
    net = UtNet2(in_channels=4, funit=32)
    # weights only: a checkpoint is a pickle, and nothing in it is run
    net.load_state_dict(torch.load(args.checkpoint, map_location="cpu", weights_only=True))
    net.eval()
    torch.onnx.export(net, torch.rand(1, 4, 128, 160), args.out, input_names=["input"], output_names=["output"],
                      dynamic_axes={"input": {2: "h", 3: "w"}, "output": {2: "H", 3: "W"}}, opset_version=17, dynamo=False)
    session = ort.InferenceSession(args.out, providers=["CPUExecutionProvider"])
    x = torch.rand(1, 4, 256, 192) * 0.1
    with torch.no_grad():
        want = net(x).numpy()
    got = session.run(None, {"input": x.numpy()})[0]
    print("relative error", float(np.abs(want - got).max() / np.abs(want).max()))
    print("sha256", hashlib.sha256(open(args.out, "rb").read()).hexdigest())


if __name__ == "__main__":
    main()
