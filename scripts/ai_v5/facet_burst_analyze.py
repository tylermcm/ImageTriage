#!/usr/bin/env python3
"""Score Facet's burst-pick logic against the owner's keeper marks, using the same pre-registered rule as the VLM spike.

Facet (MIT, github.com/ncoevoet/facet) ranks the frames of a burst with processing/burst_score.compute_burst_score, a fixed blend of
its per-photo scores: 0.4 aggregate + 0.25 aesthetic + 0.2 tech_sharpness + 0.15 no-blink. The per-photo scores come from Facet's own
scan (commercial licensing mode: Q-ReAlign Mini aesthetic, MediaPipe/landmark eyes and blink, permissive face models, BiRefNet).

Rule: promising only if top-1 >= 0.45 AND the paired bootstrap interval of the gain over the best simple baseline is above zero.

  python3 facet_burst_analyze.py --bursts iqa_synth/burst_picks --db facet/photo_scores_pro.db
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3

import numpy as np

W_DEFAULT = (0.4, 0.25, 0.2, 0.15, 0.0, 0.0)  # aggregate, aesthetic, sharpness, blink, eyes, expression (Facet defaults)


def blend(p: dict, w) -> float:
    s = (p["aggregate"] or 0) * w[0] + (p["aesthetic"] or 0) * w[1] + (p["tech_sharpness"] or 0) * w[2] + (0 if p["is_blink"] else 10) * w[3]
    if (p["face_count"] or 0) > 0:
        if p["eyes_open_score"] is not None:
            s += p["eyes_open_score"] * w[4]
        if p["expression_score"] is not None:
            s += p["expression_score"] * w[5]
    return s


def interval(v: np.ndarray, n: int = 5000, seed: int = 0):
    rng = np.random.default_rng(seed)
    m = [v[rng.integers(0, len(v), len(v))].mean() for _ in range(n)]
    return float(v.mean()), *[float(x) for x in np.percentile(m, [2.5, 97.5])]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bursts", required=True)
    ap.add_argument("--db", required=True)
    args = ap.parse_args()
    con = sqlite3.connect(args.db)
    con.row_factory = sqlite3.Row
    rows = {os.path.basename(r["path"]).lower(): dict(r) for r in con.execute("select * from photos")}
    bursts = json.load(open(os.path.join(args.bursts, "bursts.json")))
    usable = []
    for b in bursts:
        names = [os.path.basename(p).lower() for p in b["paths"]]
        if all(n in rows for n in names):
            usable.append((b, [rows[n] for n in names]))
    print(f"{len(usable)} of {len(bursts)} bursts fully scored by Facet")
    if not usable:
        return

    def top1(rule):
        return np.array([float(int(np.argmax([rule(p, b) for p in ps])) == b["pick"]) for b, ps in usable])

    cands = {
        "random (expected)": np.array([1.0 / len(b["paths"]) for b, _ in usable]),
        "least clipped highlights": np.array([float(int(np.argmin([m["clip"] for m in b["measures"]])) == b["pick"]) for b, _ in usable]),
        "Facet blend (defaults)": top1(lambda p, b: blend(p, W_DEFAULT)),
        "Facet blend + eyes/expression": top1(lambda p, b: blend(p, (0.4, 0.25, 0.2, 0.15, 0.1, 0.1))),
        "aggregate only": top1(lambda p, b: p["aggregate"] or 0),
        "aesthetic only": top1(lambda p, b: p["aesthetic"] or 0),
        "tech_sharpness only": top1(lambda p, b: p["tech_sharpness"] or 0),
        "eyes open + no blink": top1(lambda p, b: (p["eyes_open_score"] if p["eyes_open_score"] is not None else 5) - 10 * (p["is_blink"] or 0)),
    }
    base = cands["least clipped highlights"]
    print(f"{'method':32s} top-1 [95% interval]            gain over least-clipped baseline [95% interval]")
    for name, v in cands.items():
        m, lo, hi = interval(v)
        dm, dlo, dhi = interval(v - base)
        verdict = ""
        if name.startswith("Facet"):
            verdict = "  PROMISING" if m >= 0.45 and dlo > 0 else "  does not meet the rule"
        print(f"{name:32s} {m:.3f} [{lo:.3f}, {hi:.3f}]     {dm:+.3f} [{dlo:+.3f}, {dhi:+.3f}]{verdict}")
    sizes = {}
    for (b, _), h in zip(usable, cands["Facet blend (defaults)"]):
        sizes.setdefault(len(b["paths"]), []).append(h)
    print("Facet blend top-1 by burst size:", {k: f"{np.mean(v):.2f} (n={len(v)})" for k, v in sorted(sizes.items())})


if __name__ == "__main__":
    main()
