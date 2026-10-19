#!/usr/bin/env python3
"""Export per-frame features and the reviewed reference decisions for the cascade's golden test (tests/fixtures/cascade_golden.json).

Features are what the app's extractor will supply (hash, centre sharpness, clipped fraction, extremes, blink, MANIQA, aesthetic); the decisions come
from cascade_stack_sheet.py (--rank sequential, pHash duplicate threshold 8), the output the owner reviewed.

  python3 export_cascade_fixture.py --set unrated:unrated_bursts:unrated_sequential:../facet/photo_scores_pro.db:../maniqa/maniqa_results_unrated.json \
      --set marked:burst_picks:marked_sequential:../facet/photo_scores_burst63.db:../maniqa/maniqa_results_burst63.json --out cascade_golden.json
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3

import cv2
import numpy as np


def phash_hex(gray: np.ndarray) -> str:
    small = cv2.resize(gray, (32, 32), interpolation=cv2.INTER_AREA).astype(np.float32)
    low = cv2.dct(small)[:8, :8].flatten()[1:]
    bits = low > np.median(low)
    return format(int("".join("1" if b else "0" for b in bits), 2), "x")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", action="append", required=True, help="name:bursts_dir:decisions_dir:facet_db:maniqa_json")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    out = {"schema": 1, "settings": {"split_jump": 18, "duplicate_distance": 8}, "bursts": []}
    for spec in args.set:
        name, bursts_dir, decisions_dir, db_path, maniqa_path = spec.split(":")
        root = os.path.dirname(os.path.abspath(bursts_dir))
        bursts = json.load(open(os.path.join(bursts_dir, "bursts.json")))
        decisions = {d["id"]: d for d in json.load(open(os.path.join(decisions_dir, "data.json")))}
        maniqa = {r["id"]: r["maniqa"] for r in json.load(open(maniqa_path))}
        con = sqlite3.connect(db_path)
        con.row_factory = sqlite3.Row
        rows_by_name = {os.path.basename(r["path"]).lower(): dict(r) for r in con.execute("select * from photos")}
        for b in bursts:
            ref = decisions[b["id"]]
            frames = []
            for i, (jpg, nef) in enumerate(zip(b["frames"], b["paths"])):
                gray = cv2.cvtColor(cv2.imread(os.path.join(root, jpg)), cv2.COLOR_BGR2GRAY)
                row = rows_by_name[os.path.basename(nef).lower()]
                frames.append({"key": os.path.basename(nef), "phash": phash_hex(gray), "centre_sharpness": round(b["measures"][i]["lap_centre"], 6),
                               "clipped": round(b["measures"][i]["clip"], 9), "extremes": round(float(((gray <= 5) | (gray >= 250)).mean()), 9),
                               "blink": bool(row["is_blink"]), "maniqa": round(float(maniqa[b["id"]][i]), 6), "aesthetic": round(float(row["aesthetic"] or 0), 6)})
            stacks = []
            for s in ref["stacks"]:
                v = s["views"]["8"]
                stacks.append({"indices": s["idx"], "split_jump": s["split_jump"], "no_pick": bool(s["no_winner"]),
                               "flags": {k: v2 for k, v2 in s["flags"].items()}, "final": v["final"], "maniqa_cut": v.get("maniqa_cut", []),
                               "duplicate_of": v["dup_of"], "rejected": [int(k) for k, c in v["cluster"].items() if c is None]})
            out["bursts"].append({"set": name, "id": b["id"], "frames": frames, "stacks": stacks})
    json.dump(out, open(args.out, "w"), separators=(",", ":"))
    print(len(out["bursts"]), "bursts,", sum(len(b["frames"]) for b in out["bursts"]), "frames ->", args.out)


if __name__ == "__main__":
    main()
