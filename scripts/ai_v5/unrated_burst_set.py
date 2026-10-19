#!/usr/bin/env python3
"""Sample bursts from an album the owner has not rated, for an unbiased look at the cascade.

Same burst construction as burst_pick_dataset.py (consecutive frames, EXIF gap <= 5 s, pHash distance <= 20, 3 to 10 frames) but with no keeper
requirement: there are none, and pick is -1. Bursts are sampled evenly across the album with a fixed seed. Output matches burst_picks/ so the
Facet scan, MANIQA and cascade_burst_sheet.py can run unchanged.

  python3 unrated_burst_set.py --spike spike1 --album "Engagement Trip" --count 45 --out unrated_bursts
"""
from __future__ import annotations

import argparse
import json
import os

import cv2
import numpy as np

from burst_pick_dataset import load_frame, measures
from spike_common import hamming64, load_features


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--spike", required=True)
    ap.add_argument("--album", required=True)
    ap.add_argument("--count", type=int, default=45)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    f = load_features(os.path.join(args.spike, "features.npz"))
    album = np.array([str(a).split("/")[0] for a in f["folder"]])
    ham = hamming64(f["phash_norm"][:-1], f["phash_norm"][1:])
    dt = f["ts"][1:] - f["ts"][:-1]
    stream = f["stream"] if "stream" in f else f["folder"]  # round 1 features have no stream key
    same = (stream[:-1] == stream[1:]) & (dt <= 5.0) & (ham <= 20.0)
    gid = np.concatenate([[0], np.cumsum(~same)])
    candidates = []
    for g in np.unique(gid):
        idx = np.flatnonzero(gid == g)
        if 3 <= len(idx) <= 10 and str(album[idx[0]]) == args.album:
            candidates.append(idx)
    print(f"{len(candidates)} candidate bursts of 3 to 10 frames in {args.album}", flush=True)
    rng = np.random.default_rng(0)
    chosen = sorted(rng.choice(len(candidates), size=min(args.count, len(candidates)), replace=False))
    os.makedirs(args.out, exist_ok=True)
    bursts = []
    for n, c in enumerate(chosen):
        idx = candidates[c]
        b = {"id": f"u{n}", "album": args.album, "paths": [str(f["path"][i]) for i in idx], "pick": -1, "ts": [float(f["ts"][i]) for i in idx],
             "frames": [], "measures": []}
        os.makedirs(os.path.join(args.out, b["id"]), exist_ok=True)
        ok = True
        for k, path in enumerate(b["paths"]):
            bgr = load_frame(path)
            if bgr is None:
                ok = False
                break
            out_path = os.path.join(args.out, b["id"], f"{k}.jpg")
            cv2.imwrite(out_path, bgr, [cv2.IMWRITE_JPEG_QUALITY, 90])
            b["frames"].append(out_path)
            b["measures"].append(measures(bgr))
        if ok:
            bursts.append(b)
    json.dump(bursts, open(os.path.join(args.out, "bursts.json"), "w"), indent=0)
    print(f"{len(bursts)} bursts, {sum(len(b['frames']) for b in bursts)} frames written to {args.out}")


if __name__ == "__main__":
    main()
