#!/usr/bin/env python3
"""Burst-pick test set from the owner's own culling marks, plus the no-model baselines.

A burst is a run of consecutive frames in one stream that are close in time and similar by pHash (a rough rule; the real
grouping is built later). A burst becomes a test case when exactly one of its frames is a known keeper: a winner copy,
an "Accepted" XMP label, or an edited export. The keeper is the "pick"; the other frames are the alternatives.
This is weak ground truth (an unpicked frame is not necessarily bad), but it is the owner's real decision.

Baselines scored by top-1 pick rate: random, first frame, last frame, sharpest frame (global and centre), least clipped.
Frames are rewritten as 1280-px JPEGs for the vision-language spike.

  python3 burst_pick_dataset.py --spikes spike2 spike3 --out burst_picks --albums "China '26" "China 8-26"
"""
from __future__ import annotations

import argparse
import collections
import io
import json
import os
import re

import cv2
import numpy as np
from PIL import Image, ImageOps

import nef_preview
from spike_common import hamming64, load_features

ROOT = "/media/dixie/ColossalBoi/Photography"
Image.MAX_IMAGE_PIXELS = None


def keeper_stems() -> dict[str, set[str]]:
    keepers: dict[str, set[str]] = collections.defaultdict(set)
    for dp, _, fn in os.walk(ROOT):
        album = os.path.relpath(dp, ROOT).split(os.sep)[0]
        low = dp.lower()
        for f in fn:
            stem, ext = os.path.splitext(f)
            if "_winners" in low and ext.lower() in (".nef", ".jpg", ".jpeg"):
                keepers[album].add(stem.lower())
            elif ext.lower() == ".xmp":
                try:
                    text = open(os.path.join(dp, f), encoding="utf-8", errors="ignore").read(40000)
                except OSError:
                    continue
                if 'xmp:Label="Accepted"' in text:
                    keepers[album].add(stem.lower().split(".")[0])
            elif (re.search(r"(^|[\\/])edited([\\/]|$)", low) or low.endswith("final")) and ext.lower() in (".jpg", ".jpeg", ".tif", ".png"):
                keepers[album].add(re.sub(r"[-_ ]?(edit|edited|final)$", "", stem.lower()))
    return keepers


def measures(bgr: np.ndarray) -> dict[str, float]:
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY).astype(np.float32)
    h, w = gray.shape
    centre = gray[h // 4 : 3 * h // 4, w // 4 : 3 * w // 4]
    gx, gy = cv2.Sobel(gray, cv2.CV_32F, 1, 0), cv2.Sobel(gray, cv2.CV_32F, 0, 1)
    return {
        "lap": float(cv2.Laplacian(gray, cv2.CV_32F).var()),
        "lap_centre": float(cv2.Laplacian(centre, cv2.CV_32F).var()),
        "tenengrad": float((gx * gx + gy * gy).mean()),
        "clip": float(((bgr.max(axis=2) >= 250).mean())),
        "luma": float(gray.mean()),
    }


def load_frame(path: str, long_side: int = 1280) -> np.ndarray | None:
    info = nef_preview.read_nef(path)
    if info is None:
        return None
    im = Image.open(io.BytesIO(info["jpeg"]))
    im.draft("RGB", (long_side * 2, long_side * 2))
    im = ImageOps.exif_transpose(im).convert("RGB")
    scale = long_side / max(im.size)
    im = im.resize((round(im.width * scale), round(im.height * scale)), Image.LANCZOS)
    return cv2.cvtColor(np.asarray(im), cv2.COLOR_RGB2BGR)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--spikes", nargs="+", required=True)
    parser.add_argument("--albums", nargs="+", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--max-gap", type=float, default=5.0)
    parser.add_argument("--max-ham", type=float, default=20.0)
    parser.add_argument("--max-size", type=int, default=10)
    args = parser.parse_args()
    keepers = keeper_stems()
    os.makedirs(args.out, exist_ok=True)

    bursts = []
    for spike in args.spikes:
        f = load_features(os.path.join(spike, "features.npz"))
        album = np.array([str(a).split("/")[0] for a in f["folder"]])
        stems = np.array([os.path.splitext(os.path.basename(str(p)))[0].lower() for p in f["path"]])
        ham = hamming64(f["phash_norm"][:-1], f["phash_norm"][1:])
        dt = f["ts"][1:] - f["ts"][:-1]
        same = (f["stream"][:-1] == f["stream"][1:]) & (dt <= args.max_gap) & (ham <= args.max_ham)
        gid = np.concatenate([[0], np.cumsum(~same)])
        for g in np.unique(gid):
            idx = np.flatnonzero(gid == g)
            if not (2 <= len(idx) <= args.max_size) or str(album[idx[0]]) not in args.albums:
                continue
            flags = [stems[i] in keepers.get(str(album[i]), set()) for i in idx]
            if sum(flags) == 1:
                bursts.append({"album": str(album[idx[0]]), "paths": [str(f["path"][i]) for i in idx], "pick": int(np.argmax(flags)),
                               "ts": [float(f["ts"][i]) for i in idx]})
    print(f"{len(bursts)} bursts with exactly one known keeper; frames per burst: mean {np.mean([len(b['paths']) for b in bursts]):.1f}", flush=True)

    for n, b in enumerate(bursts):
        b["id"] = f"b{n}"
        b["frames"], b["measures"] = [], []
        os.makedirs(os.path.join(args.out, b["id"]), exist_ok=True)
        for k, path in enumerate(b["paths"]):
            bgr = load_frame(path)
            if bgr is None:
                b["frames"].append(None); b["measures"].append(None); continue
            out_path = os.path.join(args.out, b["id"], f"{k}.jpg")
            cv2.imwrite(out_path, bgr, [cv2.IMWRITE_JPEG_QUALITY, 90])
            b["frames"].append(out_path); b["measures"].append(measures(bgr))
    bursts = [b for b in bursts if all(m is not None for m in b["measures"])]
    json.dump(bursts, open(os.path.join(args.out, "bursts.json"), "w"), indent=0)

    rng = np.random.default_rng(0)
    def hit(rule):
        return np.array([float(rule(b) == b["pick"]) for b in bursts])
    results = {
        "random (expected)": np.array([1.0 / len(b["paths"]) for b in bursts]),
        "first frame": hit(lambda b: 0),
        "last frame": hit(lambda b: len(b["paths"]) - 1),
        "sharpest (global Laplacian)": hit(lambda b: int(np.argmax([m["lap"] for m in b["measures"]]))),
        "sharpest (centre Laplacian)": hit(lambda b: int(np.argmax([m["lap_centre"] for m in b["measures"]]))),
        "sharpest (Tenengrad)": hit(lambda b: int(np.argmax([m["tenengrad"] for m in b["measures"]]))),
        "least clipped highlights": hit(lambda b: int(np.argmin([m["clip"] for m in b["measures"]]))),
    }
    print(f"\n{len(bursts)} bursts scored. Top-1 pick rate (does the rule choose the frame you kept?), 95% bootstrap interval:")
    for name, v in results.items():
        means = [v[rng.integers(0, len(v), len(v))].mean() for _ in range(4000)]
        lo, hi = np.percentile(means, [2.5, 97.5])
        print(f"  {name:30s} {v.mean():.3f}  [{lo:.3f}, {hi:.3f}]")
    by_album = collections.Counter(b["album"] for b in bursts)
    print("bursts by album:", dict(by_album), " sizes:", dict(sorted(collections.Counter(len(b['paths']) for b in bursts).items())))


if __name__ == "__main__":
    main()
