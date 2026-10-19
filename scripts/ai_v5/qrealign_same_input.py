#!/usr/bin/env python3
"""Q-ReAlign: our worker engine against Facet's own scorer on exactly the same 1280 px JPEGs (so input rendering cannot explain a difference).

Run from the Facet checkout (it imports Facet's models.qrealign_native) with image_triage on PYTHONPATH:
  PYTHONPATH=<dir containing image_triage>:. venv/bin/python qrealign_same_input.py --qrealign-dir <snapshot> --bursts <iqa_synth/burst_picks> --count 40
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import numpy as np


def spearman(a, b) -> float:
    ra, rb = np.argsort(np.argsort(a)), np.argsort(np.argsort(b))
    return float(np.corrcoef(ra, rb)[0, 1])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--qrealign-dir", required=True)
    ap.add_argument("--bursts", required=True)
    ap.add_argument("--count", type=int, default=40)
    args = ap.parse_args()
    root = os.path.dirname(os.path.abspath(args.bursts))
    frames = [os.path.join(root, jpg) for b in json.load(open(os.path.join(args.bursts, "bursts.json"))) for jpg in b["frames"]]
    frames = frames[:: max(1, len(frames) // args.count)][: args.count]

    from PIL import Image

    from image_triage.cascade.rankers.worker import QRealignEngine
    from models.qrealign_native import QReAlignNativeScorer

    engine = QRealignEngine(Path(args.qrealign_dir), "cuda")
    ours = [engine.score(path) for path in frames]
    del engine
    facet = QReAlignNativeScorer(size="mini", device="cuda", dtype="float16").load()
    theirs = [facet.score(Image.open(path).convert("RGB"), "aesthetic") / 10.0 for path in frames]
    diff = np.abs(np.array(ours) - np.array(theirs))
    print(f"{len(frames)} frames: Spearman {spearman(ours, theirs):.3f}, mean abs diff {diff.mean():.4f}, max {diff.max():.4f}")
    print(f"ours {min(ours):.3f}..{max(ours):.3f}   facet/10 {min(theirs):.3f}..{max(theirs):.3f}")
    for i in np.argsort(-diff)[:3]:
        print(f"  worst: {os.path.basename(os.path.dirname(frames[i]))}/{os.path.basename(frames[i])}  ours {ours[i]:.3f}  facet {theirs[i]:.3f}")


if __name__ == "__main__":
    main()
