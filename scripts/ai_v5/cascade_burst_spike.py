#!/usr/bin/env python3
"""Full three-pass cascade on the burst-pick set: heuristics, pHash, then MANIQA + Facet aesthetic. Rules fixed before the result.

Per burst (never removes every frame):
  pass 1  drop a frame if Facet flags a blink, OR its centre Laplacian variance < 0.4 x the burst's best,
          OR its clipped-highlight fraction > 2 x the burst's lowest AND > 0.02
  pass 2  among surviving frames whose pHash distance is <= 4, keep only the one with the higher pass-3 score
  pass 3  rank survivors by z(MANIQA) + z(Facet aesthetic), z-scored within the burst, and keep the top K (K = 2, also reported for 1)

Pre-registered rule, on the bursts with >= 3 frames (2-frame bursts are trivially kept): the owner's kept frame is in the output at least
85% of the time with a bootstrap lower bound above 70%, AND the cascade removes at least 40% of frames overall.

  python3 cascade_burst_spike.py --bursts burst_picks --maniqa ../maniqa/maniqa_results.json --facet-db ../facet/photo_scores_pro.db
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3

import cv2
import numpy as np


def phash(path: str) -> int:
    gray = cv2.resize(cv2.imread(path, cv2.IMREAD_GRAYSCALE), (32, 32), interpolation=cv2.INTER_AREA).astype(np.float32)
    low = cv2.dct(gray)[:8, :8].flatten()[1:]
    bits = low > np.median(low)
    return int("".join("1" if x else "0" for x in bits), 2)


def z(v):
    v = np.asarray(v, float)
    return (v - v.mean()) / (v.std() + 1e-9)


def interval(v, n=5000, seed=0):
    rng = np.random.default_rng(seed)
    m = [v[rng.integers(0, len(v), len(v))].mean() for _ in range(n)]
    return float(v.mean()), *[float(x) for x in np.percentile(m, [2.5, 97.5])]


def cascade(b, rows, maniqa, root, k, pass1=True, pass2=True):
    n = len(b["frames"])
    score = z(maniqa) + z([r["aesthetic"] or 0 for r in rows])
    alive = list(range(n))
    if pass1:
        lap = [m["lap_centre"] for m in b["measures"]]
        clip = [m["clip"] for m in b["measures"]]
        keep = [i for i in alive if not (rows[i]["is_blink"] or lap[i] < 0.4 * max(lap) or (clip[i] > 2 * min(clip) and clip[i] > 0.02))]
        alive = keep or alive
    if pass2:
        hashes = {i: phash(os.path.join(root, b["frames"][i])) for i in alive}
        dropped = set()
        for i in alive:
            for j in alive:
                if i < j and i not in dropped and j not in dropped and bin(hashes[i] ^ hashes[j]).count("1") <= 4:
                    dropped.add(i if score[i] < score[j] else j)
        alive = [i for i in alive if i not in dropped]
    return sorted(alive, key=lambda i: -score[i])[:k]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bursts", required=True)
    ap.add_argument("--maniqa", required=True)
    ap.add_argument("--facet-db", required=True)
    args = ap.parse_args()
    root = os.path.dirname(os.path.abspath(args.bursts))
    bursts = [b for b in json.load(open(os.path.join(args.bursts, "bursts.json"))) if len(b["frames"]) >= 3]
    M = {r["id"]: r["maniqa"] for r in json.load(open(args.maniqa))}
    con = sqlite3.connect(args.facet_db)
    con.row_factory = sqlite3.Row
    F = {os.path.basename(r["path"]).lower(): dict(r) for r in con.execute("select * from photos")}
    print(f"{len(bursts)} bursts with 3 or more frames; {sum(len(b['frames']) for b in bursts)} frames")
    configs = [("full cascade, keep 2", dict(k=2)), ("full cascade, keep 1", dict(k=1)), ("pass 3 only, keep 2", dict(k=2, pass1=False, pass2=False)),
               ("pass 1 + 3, keep 2", dict(k=2, pass2=False)), ("pass 1 only (all survivors)", dict(k=99, pass2=False)),
               ("pass 1+2 only (all survivors)", dict(k=99))]
    for name, kw in configs:
        hit, out, total = [], 0, 0
        for b in bursts:
            rows = [F[os.path.basename(p).lower()] for p in b["paths"]]
            chosen = cascade(b, rows, M[b["id"]], root, **kw)
            hit.append(float(b["pick"] in chosen)); out += len(chosen); total += len(b["frames"])
        h = np.array(hit)
        m, lo, hi = interval(h)
        removed = 1 - out / total
        ok = m >= 0.85 and lo > 0.70 and removed >= 0.40
        print(f"{name:30s} kept frame survives {m:.3f} [{lo:.3f}, {hi:.3f}]   frames removed {removed:.0%}" + ("   MEETS THE RULE" if ok and name.startswith("full") else ""))


if __name__ == "__main__":
    main()
