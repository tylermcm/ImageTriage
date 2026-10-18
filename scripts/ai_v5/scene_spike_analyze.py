#!/usr/bin/env python3
"""Score the scene-stacking spike against the owner's labels (labels3.json: query id -> candidate ids ticked as same scene).

Per query and system: precision at 4 (how many of its 4 top results are the same scene) and hit at 4 (at least one is).
Systems that rank a photo the owner never saw would be unfair, but every system's top 4 is inside the judged pool by
construction. Paired bootstrap over queries.

Decision rule fixed in advance: DINOv2 is worth adding for scene stacking only if its precision at 4 beats the better of the two
classical systems by at least +0.10 with a 95% interval above zero.

  python3 scene_spike_analyze.py --meta spike2/scene/scene_meta.json --labels spike2/scene/labels3.json
"""
from __future__ import annotations

import argparse
import json

import numpy as np

SYSTEMS = ("dino", "orb", "lowlevel")


def boot(diff: np.ndarray, n: int = 5000, seed: int = 0):
    rng = np.random.default_rng(seed)
    means = [diff[rng.integers(0, len(diff), len(diff))].mean() for _ in range(n)]
    lo, hi = np.percentile(means, [2.5, 97.5])
    return float(diff.mean()), float(lo), float(hi)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--meta", required=True)
    parser.add_argument("--labels", required=True)
    args = parser.parse_args()
    meta = json.load(open(args.meta, encoding="utf-8"))
    labels = json.load(open(args.labels, encoding="utf-8"))
    rows = [m for m in meta if m["id"] in labels]
    print(f"{len(rows)} of {len(meta)} queries judged")
    rel = {m["id"]: set(labels[m["id"]]) for m in rows}
    n_rel = np.array([len(rel[m["id"]]) for m in rows])
    print(f"same-scene photos found in the pools: mean {n_rel.mean():.2f} per query; queries with none: {(n_rel == 0).sum()}")

    prec = {s: np.array([len(set(m["top"][s]) & rel[m["id"]]) / len(m["top"][s]) for m in rows]) for s in SYSTEMS}
    hit = {s: np.array([float(len(set(m["top"][s]) & rel[m["id"]]) > 0) for m in rows]) for s in SYSTEMS}
    print("\nSystem        precision@4   hit@4 (found at least one)")
    for s in SYSTEMS:
        print(f"  {s:10s}  {prec[s].mean():10.3f}   {hit[s].mean():10.3f}")

    # queries where there is something to find
    has = n_rel > 0
    print(f"\nOnly queries with at least one same-scene photo in the pool ({int(has.sum())}):")
    for s in SYSTEMS:
        print(f"  {s:10s}  precision@4 {prec[s][has].mean():.3f}   hit@4 {hit[s][has].mean():.3f}")

    best = max(("orb", "lowlevel"), key=lambda s: prec[s].mean())
    print(f"\nPaired bootstrap over queries (precision@4, 95% interval); the better classical system is '{best}':")
    for label, a, b in (("dino - orb", "dino", "orb"), ("dino - lowlevel", "dino", "lowlevel"), (f"dino - best classical ({best})", "dino", best)):
        d, lo, hi = boot(prec[a] - prec[b])
        print(f"  {label:34s} {d:+.3f} [{lo:+.3f}, {hi:+.3f}]")
    d, lo, hi = boot(prec["dino"] - prec[best])
    print(f"\nDecision rule (DINOv2 adds value for scene stacking only if the gain over the better classical system is >= +0.10 with the interval above zero): "
          f"{'MEETS the rule' if d >= 0.10 and lo > 0 else 'does NOT meet the rule'}  (gain {d:+.3f}, interval [{lo:+.3f}, {hi:+.3f}])")

    print("\nBy album and photographer (precision@4):")
    cells = sorted({(m["album"], m["photographer"]) for m in rows})
    for album, photographer in cells:
        sel = np.array([(m["album"], m["photographer"]) == (album, photographer) for m in rows])
        print(f"  {album:16s} {photographer:7s} n={int(sel.sum()):2d}  " + "  ".join(f"{s} {prec[s][sel].mean():.2f}" for s in SYSTEMS))

    unique_found = {s: 0 for s in SYSTEMS}
    for m in rows:
        others = set().union(*[set(m["top"][t]) for t in SYSTEMS if t != "dino"]) & rel[m["id"]]
        unique_found["dino"] += len((set(m["top"]["dino"]) & rel[m["id"]]) - others)
        for s in ("orb", "lowlevel"):
            rest = set().union(*[set(m["top"][t]) for t in SYSTEMS if t != s]) & rel[m["id"]]
            unique_found[s] += len((set(m["top"][s]) & rel[m["id"]]) - rest)
    print("\nSame-scene photos that only one system found in its top 4 (and the others missed): " + ", ".join(f"{s} {unique_found[s]}" for s in SYSTEMS))


if __name__ == "__main__":
    main()
