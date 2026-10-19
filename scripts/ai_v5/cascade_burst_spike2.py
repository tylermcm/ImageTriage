#!/usr/bin/env python3
"""Cascade round 2: pHash clusters, evenly-exposed representative, cluster-level survival. Rules fixed before the run.

Why: in about half of the burst-pick bursts every frame is within pHash distance 8 of the others, so the owner's marked frame is one of
several interchangeable ones. Scoring "is exactly that frame kept" measures noise there. Here near-duplicates are collapsed first.

Per burst (never removes every frame):
  pass 1   drop blink / soft (< 0.4 x best centre Laplacian) / clipped (> 2 x lowest and > 2%) frames, as in round 1
  cluster  complete-linkage clusters of the survivors at pHash distance <= T, T in {8, 12} (both fixed in advance)
  rep      the frame with the fewest pixels at either extreme (<= 5 or >= 250): the owner's own habit, "most even exposure"
  pass 3   rank representatives by z(MANIQA) + z(Facet aesthetic) within the burst, keep the top 2 clusters

Pre-registered rule, on bursts with >= 3 clusters before pass 3: the cluster holding the owner's kept frame survives in >= 85% of bursts
(bootstrap lower bound > 70%) AND >= 40% of frames are removed. Exact-frame survival is reported but is not the rule.

  python3 cascade_burst_spike2.py --bursts burst_picks --maniqa ../maniqa/maniqa_results.json --facet-db ../facet/photo_scores_pro.db
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3

import cv2
import numpy as np


def phash(gray: np.ndarray) -> np.ndarray:
    small = cv2.resize(gray, (32, 32), interpolation=cv2.INTER_AREA).astype(np.float32)
    low = cv2.dct(small)[:8, :8].flatten()[1:]
    return low > np.median(low)


def z(v):
    v = np.asarray(v, float)
    return (v - v.mean()) / (v.std() + 1e-9)


def interval(v, n=5000, seed=0):
    rng = np.random.default_rng(seed)
    m = [v[rng.integers(0, len(v), len(v))].mean() for _ in range(n)]
    return float(v.mean()), *[float(x) for x in np.percentile(m, [2.5, 97.5])]


def complete_linkage(ids, dist, threshold):
    clusters = [[i] for i in ids]
    merged = True
    while merged:
        merged = False
        for a in range(len(clusters)):
            for b in range(a + 1, len(clusters)):
                if max(dist[i][j] for i in clusters[a] for j in clusters[b]) <= threshold:
                    clusters[a] += clusters.pop(b)
                    merged = True
                    break
            if merged:
                break
    return clusters


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bursts", required=True)
    ap.add_argument("--maniqa", required=True)
    ap.add_argument("--facet-db", required=True)
    args = ap.parse_args()
    root = os.path.dirname(os.path.abspath(args.bursts))
    bursts = json.load(open(os.path.join(args.bursts, "bursts.json")))
    M = {r["id"]: r["maniqa"] for r in json.load(open(args.maniqa))}
    con = sqlite3.connect(args.facet_db)
    con.row_factory = sqlite3.Row
    F = {os.path.basename(r["path"]).lower(): dict(r) for r in con.execute("select * from photos")}

    prepared = []
    for b in bursts:
        gs = [cv2.cvtColor(cv2.imread(os.path.join(root, p)), cv2.COLOR_BGR2GRAY) for p in b["frames"]]
        hs = [phash(g) for g in gs]
        n = len(gs)
        dist = [[int((hs[i] != hs[j]).sum()) for j in range(n)] for i in range(n)]
        tails = [float(((g <= 5) | (g >= 250)).mean()) for g in gs]
        rows = [F[os.path.basename(p).lower()] for p in b["paths"]]
        score = z(M[b["id"]]) + z([r["aesthetic"] or 0 for r in rows])
        lap = [m["lap_centre"] for m in b["measures"]]
        clip = [m["clip"] for m in b["measures"]]
        alive = [i for i in range(n) if not (rows[i]["is_blink"] or lap[i] < 0.4 * max(lap) or (clip[i] > 2 * min(clip) and clip[i] > 0.02))] or list(range(n))
        prepared.append((b, n, dist, tails, score, alive))

    for T in (8, 12):
        eligible = [p for p in prepared if len(complete_linkage(list(range(p[1])), p[2], T)) >= 3]
        print(f"\nT = {T}: {len(eligible)} of {len(prepared)} bursts have >= 3 pHash clusters")
        for label, use_pass1, k in (("pass 1 + cluster + pass 3 (keep 2)", True, 2), ("cluster + pass 3 (keep 2)", False, 2),
                                    ("pass 1 + cluster only (all reps)", True, 99), ("cluster only (all reps)", False, 99)):
            cluster_hit, exact_hit, out, total = [], [], 0, 0
            for b, n, dist, tails, score, alive in eligible:
                ids = alive if use_pass1 else list(range(n))
                clusters = complete_linkage(ids, dist, T)
                reps = [min(c, key=lambda i: tails[i]) for c in clusters]
                chosen = sorted(reps, key=lambda i: -score[i])[:k]
                pick = b["pick"]
                pick_cluster = next((c for c in complete_linkage(list(range(n)), dist, T) if pick in c), [pick])
                cluster_hit.append(float(any(i in pick_cluster for i in chosen)))
                exact_hit.append(float(pick in chosen))
                out += len(chosen); total += n
            h = np.array(cluster_hit)
            m, lo, hi = interval(h)
            removed = 1 - out / total
            ok = m >= 0.85 and lo > 0.70 and removed >= 0.40
            print(f"  {label:36s} pick's cluster survives {m:.3f} [{lo:.3f}, {hi:.3f}]  exact frame {np.mean(exact_hit):.3f}  frames removed {removed:.0%}"
                  + ("  MEETS THE RULE" if ok and k == 2 else ""))


if __name__ == "__main__":
    main()
