#!/usr/bin/env python3
"""MANIQA (IIGROUP/MANIQA, Apache-2.0 code) as a burst picker, scored with the same pre-registered rule as the other spikes.

Scores each 1280-px burst frame the way the repo's predict_one_image.py does: the mean of 20 random 224-px crops (fixed seed, so every
frame of every burst sees the same procedure), KonIQ-10k checkpoint. The highest score in a burst is the pick.
Rule: promising only if top-1 >= 0.45 AND the paired bootstrap interval of the gain over the least-clipped-highlights baseline is above 0.

  cd maniqa && ../facet/venv/bin/python ../iqa_synth/maniqa_burst_spike.py --bursts ../iqa_synth/burst_picks --ckpt ckpt_koniq10k.pt
"""
from __future__ import annotations

import argparse
import json
import os
import time

import cv2
import numpy as np
import torch

from models.maniqa import MANIQA


def interval(v, n=5000, seed=0):
    rng = np.random.default_rng(seed)
    m = [v[rng.integers(0, len(v), len(v))].mean() for _ in range(n)]
    return float(v.mean()), *[float(x) for x in np.percentile(m, [2.5, 97.5])]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bursts", required=True)
    ap.add_argument("--ckpt", required=True)
    ap.add_argument("--crops", type=int, default=20)
    args = ap.parse_args()
    net = MANIQA(embed_dim=768, num_outputs=1, dim_mlp=768, patch_size=8, img_size=224, window_size=4, depths=[2, 2],
                 num_heads=[4, 4], num_tab=2, scale=0.8)
    print(net.load_state_dict(torch.load(args.ckpt, map_location="cpu"), strict=False))
    net = net.cuda().eval()
    bursts = json.load(open(os.path.join(args.bursts, "bursts.json")))
    started = time.time()
    n_frames = 0
    for b in bursts:
        scores = []
        for path in b["frames"]:
            img = cv2.cvtColor(cv2.imread(os.path.join(os.path.dirname(os.path.abspath(args.bursts)), path), cv2.IMREAD_COLOR), cv2.COLOR_BGR2RGB).astype("float32") / 255
            h, w = img.shape[:2]
            rng = np.random.default_rng(20)
            patches = []
            for _ in range(args.crops):
                top, left = int(rng.integers(0, h - 224)), int(rng.integers(0, w - 224))
                patches.append(img[top:top + 224, left:left + 224])
            x = torch.from_numpy(np.stack(patches)).permute(0, 3, 1, 2)
            x = ((x - 0.5) / 0.5).cuda()
            with torch.no_grad():
                scores.append(float(net(x).mean()))
            n_frames += 1
        b["maniqa"] = scores
    print(f"scored {n_frames} frames in {time.time() - started:.0f}s ({(time.time() - started) / n_frames:.2f}s per frame)")
    pick = np.array([float(int(np.argmax(b["maniqa"])) == b["pick"]) for b in bursts])
    base = np.array([float(int(np.argmin([m["clip"] for m in b["measures"]])) == b["pick"]) for b in bursts])
    rand = np.array([1.0 / len(b["frames"]) for b in bursts])
    pair = np.array([np.mean([b["maniqa"][b["pick"]] > b["maniqa"][j] for j in range(len(b["frames"])) if j != b["pick"]]) for b in bursts])
    m, lo, hi = interval(pick)
    dm, dlo, dhi = interval(pick - base)
    rm, rlo, rhi = interval(pick - rand)
    am, alo, ahi = interval(pair)
    print(f"MANIQA top-1 {m:.3f} [{lo:.3f}, {hi:.3f}]  vs least-clipped {dm:+.3f} [{dlo:+.3f}, {dhi:+.3f}]  vs random {rm:+.3f} [{rlo:+.3f}, {rhi:+.3f}]  "
          f"pair accuracy {am:.3f} [{alo:.3f}, {ahi:.3f}]  -> {'PROMISING' if m >= 0.45 and dlo > 0 and alo > 0.5 else 'does not meet the rule'}")
    json.dump([{"id": b["id"], "pick": b["pick"], "maniqa": b["maniqa"]} for b in bursts], open("maniqa_results.json", "w"))


if __name__ == "__main__":
    main()
