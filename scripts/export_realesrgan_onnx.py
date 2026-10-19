"""Export the official Real-ESRGAN ``x2plus`` / ``x4plus`` checkpoints to ONNX (plan milestone B0).

Needs ``torch``, ``onnx`` and ``onnxruntime`` (not part of the application runtime). Usage::

    python scripts/export_realesrgan_onnx.py --weights <dir with the .pth files> --out <dir>

Checks, per model: strict state-dict key match, ONNX-vs-PyTorch agreement on a fixed tile, and
prints size + SHA-256 of the ONNX file so ``upscale_core.MODEL_SPECS`` can be updated if they changed.

The RRDBNet architecture below is re-written from the Real-ESRGAN / BasicSR architecture description.
Real-ESRGAN: Copyright (c) 2021, Xintao Wang, BSD 3-Clause License
(https://github.com/xinntao/Real-ESRGAN/blob/master/LICENSE). Redistribution of this file's
architecture must retain that notice.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

CONFIGS = {
    # key: (checkpoint file, scale)
    "realesrgan-x2plus": ("RealESRGAN_x2plus.pth", 2),
    "realesrgan-x4plus": ("RealESRGAN_x4plus.pth", 4),
}


def build_model(scale: int):
    import torch
    from torch import nn
    from torch.nn import functional as F

    class ResidualDenseBlock(nn.Module):
        def __init__(self, num_feat=64, num_grow_ch=32):
            super().__init__()
            self.conv1 = nn.Conv2d(num_feat, num_grow_ch, 3, 1, 1)
            self.conv2 = nn.Conv2d(num_feat + num_grow_ch, num_grow_ch, 3, 1, 1)
            self.conv3 = nn.Conv2d(num_feat + 2 * num_grow_ch, num_grow_ch, 3, 1, 1)
            self.conv4 = nn.Conv2d(num_feat + 3 * num_grow_ch, num_grow_ch, 3, 1, 1)
            self.conv5 = nn.Conv2d(num_feat + 4 * num_grow_ch, num_feat, 3, 1, 1)
            self.lrelu = nn.LeakyReLU(negative_slope=0.2, inplace=True)

        def forward(self, x):
            x1 = self.lrelu(self.conv1(x))
            x2 = self.lrelu(self.conv2(torch.cat((x, x1), 1)))
            x3 = self.lrelu(self.conv3(torch.cat((x, x1, x2), 1)))
            x4 = self.lrelu(self.conv4(torch.cat((x, x1, x2, x3), 1)))
            x5 = self.conv5(torch.cat((x, x1, x2, x3, x4), 1))
            return x5 * 0.2 + x

    class RRDB(nn.Module):
        def __init__(self, num_feat, num_grow_ch=32):
            super().__init__()
            self.rdb1 = ResidualDenseBlock(num_feat, num_grow_ch)
            self.rdb2 = ResidualDenseBlock(num_feat, num_grow_ch)
            self.rdb3 = ResidualDenseBlock(num_feat, num_grow_ch)

        def forward(self, x):
            return self.rdb3(self.rdb2(self.rdb1(x))) * 0.2 + x

    class RRDBNet(nn.Module):
        def __init__(self, scale, num_in_ch=3, num_out_ch=3, num_feat=64, num_block=23, num_grow_ch=32):
            super().__init__()
            self.scale = scale
            if scale == 2:
                num_in_ch *= 4
            elif scale == 1:
                num_in_ch *= 16
            self.conv_first = nn.Conv2d(num_in_ch, num_feat, 3, 1, 1)
            self.body = nn.Sequential(*[RRDB(num_feat, num_grow_ch) for _ in range(num_block)])
            self.conv_body = nn.Conv2d(num_feat, num_feat, 3, 1, 1)
            self.conv_up1 = nn.Conv2d(num_feat, num_feat, 3, 1, 1)
            self.conv_up2 = nn.Conv2d(num_feat, num_feat, 3, 1, 1)
            self.conv_hr = nn.Conv2d(num_feat, num_feat, 3, 1, 1)
            self.conv_last = nn.Conv2d(num_feat, num_out_ch, 3, 1, 1)
            self.lrelu = nn.LeakyReLU(negative_slope=0.2, inplace=True)

        def forward(self, x):
            if self.scale == 2:
                feat = F.pixel_unshuffle(x, 2)
            elif self.scale == 1:
                feat = F.pixel_unshuffle(x, 4)
            else:
                feat = x
            feat = self.conv_first(feat)
            feat = feat + self.conv_body(self.body(feat))
            feat = self.lrelu(self.conv_up1(F.interpolate(feat, scale_factor=2, mode="nearest")))
            feat = self.lrelu(self.conv_up2(F.interpolate(feat, scale_factor=2, mode="nearest")))
            return self.conv_last(self.lrelu(self.conv_hr(feat)))

    return RRDBNet(scale)


def load_checkpoint(model, path: Path) -> None:
    import torch

    state = torch.load(path, map_location="cpu", weights_only=True)
    for key in ("params_ema", "params"):
        if key in state:
            state = state[key]
            break
    model.load_state_dict(state, strict=True)  # strict: every key must match


def export_one(key: str, weights_dir: Path, out_dir: Path, tol: float) -> bool:
    import numpy as np
    import onnxruntime as ort
    import torch

    filename, scale = CONFIGS[key]
    model = build_model(scale).eval()
    load_checkpoint(model, weights_dir / filename)
    out_path = out_dir / filename.replace(".pth", ".onnx")
    probe = torch.from_numpy(np.random.default_rng(7).random((1, 3, 96, 96), dtype=np.float32))
    torch.onnx.export(
        model,
        probe,
        str(out_path),
        input_names=["input"],
        output_names=["output"],
        dynamic_axes={"input": {2: "h", 3: "w"}, "output": {2: "oh", 3: "ow"}},
        opset_version=17,
        dynamo=False,
    )
    with torch.no_grad():
        expected = model(probe).numpy()
    session = ort.InferenceSession(str(out_path), providers=["CPUExecutionProvider"])
    actual = session.run(None, {"input": probe.numpy()})[0]
    worst = float(np.abs(actual - expected).max())
    digest = hashlib.sha256(out_path.read_bytes()).hexdigest()
    ok = actual.shape == expected.shape and worst <= tol
    print(f"{key}: {out_path.name} size={out_path.stat().st_size} sha256={digest} max|onnx-torch|={worst:.2e} {'OK' if ok else 'MISMATCH'}")
    return ok


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--weights", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--model", choices=sorted(CONFIGS), action="append", help="default: both")
    parser.add_argument("--tolerance", type=float, default=1e-4)
    args = parser.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    results = [export_one(key, args.weights, args.out, args.tolerance) for key in (args.model or sorted(CONFIGS))]
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
