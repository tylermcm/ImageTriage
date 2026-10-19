#!/usr/bin/env python3
"""Score the vision-language burst-pick spike against the owner's keeper marks, using the rule fixed before the results.

Rule: a method is promising only if its top-1 pick rate is at least 0.45 AND the paired bootstrap interval of its difference from
the best simple baseline lies above zero. The pairwise accuracy (keeper preferred over each non-keeper in the same burst) must
also have a bootstrap interval above 0.5.

  python3 vlm_burst_analyze.py --bursts burst_picks --results vlm_4b/results.json [--results vlm_2b/results.json ...]
"""
from __future__ import annotations

import argparse
import json

import numpy as np


def interval(values: np.ndarray, n: int = 5000, seed: int = 0):
    rng = np.random.default_rng(seed)
    means = [values[rng.integers(0, len(values), len(values))].mean() for _ in range(n)]
    return float(values.mean()), *[float(x) for x in np.percentile(means, [2.5, 97.5])]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bursts", required=True)
    parser.add_argument("--results", nargs="+", required=True)
    args = parser.parse_args()
    bursts = {b["id"]: b for b in json.load(open(f"{args.bursts}/bursts.json"))}

    def baseline(rule):
        return {i: float(rule(b) == b["pick"]) for i, b in bursts.items()}

    baselines = {
        "random (expected)": {i: 1.0 / len(b["frames"]) for i, b in bursts.items()},
        "sharpest (global Laplacian)": baseline(lambda b: int(np.argmax([m["lap"] for m in b["measures"]]))),
        "least clipped highlights": baseline(lambda b: int(np.argmin([m["clip"] for m in b["measures"]]))),
        "last frame": baseline(lambda b: len(b["frames"]) - 1),
    }
    best_name = max((k for k in baselines if not k.startswith("random")), key=lambda k: np.mean(list(baselines[k].values())))
    for path in args.results:
        rows = json.load(open(path))
        ids = [r["id"] for r in rows]
        print(f"\n=== {path}: {len(rows)} bursts (best simple baseline: {best_name}, {np.mean([baselines[best_name][i] for i in ids]):.3f}) ===")
        for task in ("listwise", "pairwise", "rating"):
            if task not in rows[0]:
                continue
            hit = np.array([float(int(np.argmax(r[task])) == r["pick"]) for r in rows])
            base = np.array([baselines[best_name][r["id"]] for r in rows])
            rand = np.array([baselines["random (expected)"][r["id"]] for r in rows])
            m, lo, hi = interval(hit)
            dm, dlo, dhi = interval(hit - base)
            rm, rlo, rhi = interval(hit - rand)
            # pairwise accuracy: keeper's score against each non-keeper's score
            acc = np.array([np.mean([r[task][r["pick"]] > r[task][j] for j in range(r["n"]) if j != r["pick"]]) for r in rows])
            am, alo, ahi = interval(acc)
            promising = m >= 0.45 and dlo > 0 and alo > 0.5
            print(f"  {task:9s} top-1 {m:.3f} [{lo:.3f}, {hi:.3f}]  vs best baseline {dm:+.3f} [{dlo:+.3f}, {dhi:+.3f}]  vs random {rm:+.3f} [{rlo:+.3f}, {rhi:+.3f}]  "
                  f"pair accuracy {am:.3f} [{alo:.3f}, {ahi:.3f}]  -> {'PROMISING' if promising else 'does not meet the rule'}")
        # combine the three tasks by averaging per-burst rank-normalised scores
        def z(v):
            v = np.asarray(v, float)
            return (v - v.mean()) / (v.std() + 1e-9)
        if all(t in rows[0] for t in ("listwise", "pairwise", "rating")):
            hit = np.array([float(int(np.argmax(z(r["listwise"]) + z(r["pairwise"]) + z(r["rating"]))) == r["pick"]) for r in rows])
            m, lo, hi = interval(hit)
            print(f"  {'all three':9s} top-1 {m:.3f} [{lo:.3f}, {hi:.3f}]  (exploratory combination, not part of the rule)")
        by_n = {}
        for r in rows:
            by_n.setdefault(r["n"], []).append(float(int(np.argmax(r.get("pairwise", r.get("listwise")))) == r["pick"]))
        print("  pairwise top-1 by burst size:", {k: f"{np.mean(v):.2f} (n={len(v)})" for k, v in sorted(by_n.items())})


if __name__ == "__main__":
    main()
