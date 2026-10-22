#!/usr/bin/env python3
"""Score the stack rules against a person's answers from stack_review.html, and replay other settings on the same lines with no image read.

For every line the person answered (two neighbouring frames, same stack or not) this computes the measures the rules use, the app's answer and the person's, then:
  * reports the false merges (the app joined, the person split) and false splits (the app split, the person joined) with their measures, and
  * sweeps the two thresholds that decide a stack boundary for adjacent frames: the burst time gap and the hash distance (stack_split_jump). Two neighbouring frames are in
    one stack exactly when the gap is within the limit and the hash distance is below the split jump (the burst hash limit, 20, sits above the usual 18, so the jump decides).

  python scripts/ai_v5/stack_eval.py --from-frozen run_a/frozen.json --labels stack_labels.json [--labels more.json] [--extra-frozen run_b/frozen.json]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pass1_audit as audit_script  # noqa: E402
from image_triage.cascade.folder import DEFAULT_BURST_GAP_SECONDS, DEFAULT_BURST_HAMMING  # noqa: E402
from image_triage.cascade.model import CascadeSettings  # noqa: E402


def hamming(a: int, b: int) -> int:
    return bin(int(a) ^ int(b)).count("1")


def load_lines(frozen_paths: list[str], label_paths: list[str]) -> list[dict[str, Any]]:
    features: dict[str, Any] = {}
    captured: dict[str, float | None] = {}
    focal: dict[str, float] = {}
    for path in frozen_paths:
        data = audit_script.dataset_from_snapshot(json.loads(Path(path).read_text(encoding="utf-8")))
        for frames in data.groups.values():
            for f in frames:
                features[f.path] = f.features
                captured[f.path] = f.captured
        focal.update(data.focal)
    lines: dict[tuple[str, str], dict[str, Any]] = {}
    for path in label_paths:
        for row in json.loads(Path(path).read_text(encoding="utf-8")):
            a, b = row["a"], row["b"]
            fa, fb = features.get(a), features.get(b)
            if fa is None or fb is None:
                continue
            ca, cb = captured.get(a), captured.get(b)
            lines[(a, b)] = {
                "a": a, "b": b, "folder": row.get("folder", ""), "asked": bool(row.get("asked")),
                "app": row["app_says"], "human": row["human"],
                "distance": hamming(fa.phash, fb.phash),
                "gap": None if ca is None or cb is None else round(cb - ca, 2),
                "focal_ratio": None if not focal.get(a) or not focal.get(b) else round(max(focal[a], focal[b]) / min(focal[a], focal[b]), 3),
            }
    return list(lines.values())


def predict(line: dict[str, Any], gap_limit: float, jump: int, burst_hamming: int = DEFAULT_BURST_HAMMING) -> str:
    gap = line["gap"]
    joined = gap is not None and 0.0 <= gap <= gap_limit and line["distance"] < min(jump, burst_hamming + 1)
    return "same" if joined else "different"


def confusion(lines: list[dict[str, Any]], gap_limit: float, jump: int) -> tuple[int, int, int]:
    """(false merges, false splits, lines)."""
    merges = sum(1 for l in lines if predict(l, gap_limit, jump) == "same" and l["human"] == "different")
    splits = sum(1 for l in lines if predict(l, gap_limit, jump) == "different" and l["human"] == "same")
    return merges, splits, len(lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--from-frozen", nargs="+", required=True)
    p.add_argument("--labels", nargs="+", required=True)
    args = p.parse_args(argv)
    lines = load_lines(args.from_frozen, args.labels)
    defaults = CascadeSettings()
    print(f"{len(lines)} answered lines; the app joined {sum(1 for l in lines if l['app'] == 'same')} and split {sum(1 for l in lines if l['app'] == 'different')}")
    print(f"Rules today: burst gap {DEFAULT_BURST_GAP_SECONDS} s, burst hash limit {DEFAULT_BURST_HAMMING}, stack split jump {defaults.split_jump}")
    merges = [l for l in lines if l["app"] == "same" and l["human"] == "different"]
    splits = [l for l in lines if l["app"] == "different" and l["human"] == "same"]
    print(f"\nFalse merges (joined, you split): {len(merges)} of {sum(1 for l in lines if l['app'] == 'same')} joined lines")
    print(f"False splits (split, you joined): {len(splits)} of {sum(1 for l in lines if l['app'] == 'different')} split lines")

    def why(l: dict[str, Any]) -> str:
        reasons = []
        if l["gap"] is None:
            reasons.append("no capture time")
        elif l["gap"] > DEFAULT_BURST_GAP_SECONDS:
            reasons.append(f"time gap {l['gap']} s")
        elif l["gap"] < 0:
            reasons.append("out of time order")
        if l["distance"] >= defaults.split_jump:
            reasons.append(f"hash distance {l['distance']}")
        return ", ".join(reasons) or "other"

    print("\nWhy the app split the lines you would join:")
    for reason, n in Counter("time gap only" if all(x.startswith("time") for x in why(l).split(", ")) else "hash only" if all(x.startswith("hash") for x in why(l).split(", ")) else why(l) if not why(l).startswith("time gap") and "hash" not in why(l) else "time and hash" for l in splits).most_common():
        print(f"  {reason}: {n}")
    print("\nFalse splits, by measure:")
    for l in sorted(splits, key=lambda l: (l["gap"] if l["gap"] is not None else 1e9)):
        print(f"  {os.path.basename(l['a'])} / {os.path.basename(l['b'])}: gap {l['gap']} s, hash distance {l['distance']}, focal ratio {l['focal_ratio']}  ({why(l)})")
    print("\nFalse merges, by measure:")
    for l in merges:
        print(f"  {os.path.basename(l['a'])} / {os.path.basename(l['b'])}: gap {l['gap']} s, hash distance {l['distance']}, focal ratio {l['focal_ratio']}")

    print("\nSweep: false merges / false splits (lower is better) for burst gap (rows, seconds) x split jump (columns)")
    jumps = [14, 16, 18, 20, 22, 24, 26, 28]
    gaps = [3, 5, 8, 12, 20, 30, 60]
    print("gap\\jump " + "".join(f"{j:>9}" for j in jumps))
    for g in gaps:
        cells = []
        for j in jumps:
            m, s, _ = confusion(lines, g, j)
            cells.append(f"{m}/{s}".rjust(9))
        print(f"{g:>8} " + "".join(cells))
    m, s, n = confusion(lines, DEFAULT_BURST_GAP_SECONDS, defaults.split_jump)
    print(f"\nToday's rules reproduce the app on these lines: {sum(1 for l in lines if predict(l, DEFAULT_BURST_GAP_SECONDS, defaults.split_jump) == l['app'])} of {n}; against you: {m} false merges, {s} false splits.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
