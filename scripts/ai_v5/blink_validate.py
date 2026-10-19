#!/usr/bin/env python3
"""Run the real blink engine (image_triage.cascade.rankers.worker.BlinkEngine) over the reviewed bursts and report what it flags.

Run from a directory that contains the image_triage package, in an environment with mediapipe:
  python blink_validate.py --model-dir blink --sets iqa_synth/burst_picks iqa_synth/unrated_bursts --out blink_scores.json [--raw 8]
It scores every 1280 px burst frame, optionally a few of the original NEFs (to exercise the RAW path), and prints timing, how many frames contain a
face at all, the score distribution, and the frames at or above the blink threshold.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

import numpy as np

from image_triage.cascade.blink import BLINK_THRESHOLD
from image_triage.cascade.rankers.worker import BlinkEngine


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model-dir", required=True)
    ap.add_argument("--sets", nargs="+", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--raw", type=int, default=0, help="also score this many original NEFs through the worker RAW path")
    args = ap.parse_args()
    engine = BlinkEngine(Path(args.model_dir), "cpu")
    rows = []
    started = time.perf_counter()
    for bursts_dir in args.sets:
        root = os.path.dirname(os.path.abspath(bursts_dir))
        for burst in json.load(open(os.path.join(bursts_dir, "bursts.json"))):
            for index, (jpg, nef) in enumerate(zip(burst["frames"], burst["paths"])):
                path = os.path.join(root, jpg)
                tick = time.perf_counter()
                score = engine.score(path)
                rows.append({"set": os.path.basename(bursts_dir), "burst": burst["id"], "index": index, "jpg": jpg, "nef": nef, "score": score,
                             "ms": 1000 * (time.perf_counter() - tick)})
    elapsed = time.perf_counter() - started
    scores = np.array([r["score"] for r in rows])
    print(f"{len(rows)} frames in {elapsed:.0f}s: mean {1000 * elapsed / len(rows):.0f} ms per frame (median {np.median([r['ms'] for r in rows]):.0f} ms)")
    print(f"frames with a face scoring above 0: {(scores > 0).sum()} ({(scores > 0).mean():.0%}); no face / not prominent: {(scores == 0).sum()}")
    print("score percentiles among frames with a face (50/75/90/95/99):", [round(float(np.percentile(scores[scores > 0], q)), 3) for q in (50, 75, 90, 95, 99)])
    for threshold in (0.4, 0.5, BLINK_THRESHOLD, 0.7, 0.8):
        print(f"  score >= {threshold}: {(scores >= threshold).sum()} frames")
    if args.raw:
        raw = []
        for row in rows[:: max(1, len(rows) // args.raw)][: args.raw]:
            tick = time.perf_counter()
            try:
                value = engine.score(row["nef"])
            except OSError as error:
                value = None
                print("RAW read failed:", row["nef"], error)
            raw.append({"nef": row["nef"], "jpg_score": row["score"], "raw_score": value, "ms": 1000 * (time.perf_counter() - tick)})
        print("RAW path (score from the NEF's embedded preview vs the 1280 px JPEG):")
        for item in raw:
            print(f"  {os.path.basename(item['nef'])}: jpeg {item['jpg_score']:.3f}  raw {item['raw_score'] if item['raw_score'] is None else round(item['raw_score'], 3)}  ({item['ms']:.0f} ms)")
    json.dump(rows, open(args.out, "w"))
    engine.close() if hasattr(engine, "close") else None


if __name__ == "__main__":
    main()
