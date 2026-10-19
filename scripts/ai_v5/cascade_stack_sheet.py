#!/usr/bin/env python3
"""Cascade v3 review data: split into stacks first, then pass 1 within each stack, blown-out stacks get no winner.

Provisional rules from the owner's review of the Engagement Trip set (not yet validated beyond it):
  stack split   break a burst wherever the pHash distance between consecutive frames is >= SPLIT (default 18): a different pose or framing
  pass 1        within each stack: blink; soft (centre Laplacian < 0.4 x the stack's best); clipped (clipped fraction > max(2 x the stack's lowest, 0.10))
  blown out     a frame with >= 40% of pixels at maximum brightness; a stack where every frame is blown out gets no winner
  collapse      pHash complete-linkage duplicates at T in {8, 12} within the stack, keeping the most evenly exposed frame
  rank          top 2 of the representatives by z(MANIQA) + z(Facet aesthetic) within the stack

  python3 cascade_stack_sheet.py --bursts unrated_bursts --maniqa ../maniqa/maniqa_results_unrated.json --facet-db ../facet/photo_scores_pro.db --out unrated_sheet3
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess

import cv2
import numpy as np

from cascade_burst_spike2 import complete_linkage, phash, z

BLOWN = 0.40


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bursts", required=True)
    ap.add_argument("--maniqa", required=True)
    ap.add_argument("--facet-db", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--split", type=int, default=18)
    ap.add_argument("--rank", choices=["blend", "sequential"], default="blend", help="blend: top 2 by z(MANIQA)+z(aesthetic); sequential: MANIQA keeps top 3, then Q-ReAlign aesthetic keeps top 2")
    args = ap.parse_args()
    root = os.path.dirname(os.path.abspath(args.bursts))
    os.makedirs(args.out, exist_ok=True)
    bursts = json.load(open(os.path.join(args.bursts, "bursts.json")))
    M = {r["id"]: r["maniqa"] for r in json.load(open(args.maniqa))}
    con = sqlite3.connect(args.facet_db)
    con.row_factory = sqlite3.Row
    F = {os.path.basename(r["path"]).lower(): dict(r) for r in con.execute("select * from photos")}
    all_paths = [p for b in bursts for p in b["paths"]]
    orient = {}
    try:
        res = subprocess.run([os.environ.get("EXIFTOOL", "exiftool"), "-n", "-Orientation", "-T", "-fast2"] + all_paths, capture_output=True, text=True, timeout=300)
        orient = dict(zip(all_paths, [int(x) if x.strip().isdigit() else 1 for x in res.stdout.splitlines()]))
    except (OSError, subprocess.SubprocessError):
        pass
    rotate = {6: cv2.ROTATE_90_CLOCKWISE, 8: cv2.ROTATE_90_COUNTERCLOCKWISE, 3: cv2.ROTATE_180}

    out = []
    for b in bursts:
        imgs = [cv2.imread(os.path.join(root, p)) for p in b["frames"]]
        gs = [cv2.cvtColor(im, cv2.COLOR_BGR2GRAY) for im in imgs]
        hs = [phash(g) for g in gs]
        n = len(gs)
        dist = [[int((hs[i] != hs[j]).sum()) for j in range(n)] for i in range(n)]
        tails = [float(((g <= 5) | (g >= 250)).mean()) for g in gs]
        rows = [F[os.path.basename(p).lower()] for p in b["paths"]]
        score_all = list(M[b["id"]])
        aes = [r["aesthetic"] or 0 for r in rows]
        lap = [m["lap_centre"] for m in b["measures"]]
        clip = [m["clip"] for m in b["measures"]]

        frames = []
        for i, im in enumerate(imgs):
            code = rotate.get(orient.get(b["paths"][i], 1))
            if code is not None:
                im = cv2.rotate(im, code)
            h, w = im.shape[:2]
            s = 560 / max(h, w)
            name = f"{b['id']}_{i}.jpg"
            cv2.imwrite(os.path.join(args.out, name), cv2.resize(im, (round(w * s), round(h * s)), interpolation=cv2.INTER_AREA), [cv2.IMWRITE_JPEG_QUALITY, 78])
            frames.append({"img": name, "file": os.path.basename(b["paths"][i]), "kept": i == b["pick"], "tails": round(tails[i], 3), "clip": round(clip[i], 2),
                           "gap": None if i == 0 else round(b["ts"][i] - b["ts"][i - 1], 1), "jump": None if i == 0 else dist[i - 1][i]})

        # stacks: split wherever the consecutive pHash jump is large
        stacks_idx, cur = [], [0]
        for i in range(1, n):
            if dist[i - 1][i] >= args.split:
                stacks_idx.append(cur)
                cur = []
            cur.append(i)
        stacks_idx.append(cur)

        stacks = []
        for idx in stacks_idx:
            sc = z([score_all[i] for i in idx]) + z([aes[i] for i in idx]) if len(idx) > 1 else np.zeros(1)
            score = {i: float(sc[k]) for k, i in enumerate(idx)}
            mz = {i: float(v) for i, v in zip(idx, z([score_all[i] for i in idx]))} if len(idx) > 1 else {idx[0]: 0.0}
            az = {i: float(v) for i, v in zip(idx, z([aes[i] for i in idx]))} if len(idx) > 1 else {idx[0]: 0.0}
            lap_best, clip_low = max(lap[i] for i in idx), min(clip[i] for i in idx)
            flags = {}
            for i in idx:
                f = []
                if rows[i]["is_blink"]:
                    f.append("blink")
                if len(idx) > 1 and lap[i] < 0.4 * lap_best:
                    f.append("soft")
                if len(idx) > 1 and clip[i] > max(2 * clip_low, 0.10):
                    f.append("clipped")
                if clip[i] >= BLOWN:
                    f.append("blown out")
                flags[str(i)] = f
            blown_all = all(clip[i] >= BLOWN for i in idx)
            alive = [i for i in idx if not (set(flags[str(i)]) - {"blown out"})] or list(idx)
            views = {}
            for T in (8, 12):
                clusters = complete_linkage(alive, dist, T)
                reps = {min(c, key=lambda i: tails[i]) for c in clusters}
                maniqa_cut = []
                if blown_all:
                    final = []
                elif args.rank == "sequential":
                    by_maniqa = sorted(reps, key=lambda i: -mz[i])
                    maniqa_cut = by_maniqa[3:]
                    final = sorted(by_maniqa[:3], key=lambda i: -az[i])[:2]
                else:
                    final = sorted(reps, key=lambda i: -score[i])[:2]
                cl_of = {i: k for k, c in enumerate(clusters) for i in c}
                dup_of = {i: min(c, key=lambda j: tails[j]) for c in clusters for i in c if i not in reps}
                views[str(T)] = {"cluster": {str(i): cl_of.get(i) for i in idx}, "rep": sorted(reps), "final": final, "maniqa_cut": maniqa_cut, "dup_of": {str(k): v for k, v in dup_of.items()}}
            stacks.append({"idx": idx, "flags": flags, "score": {str(i): round(score[i], 2) for i in idx}, "no_winner": blown_all, "views": views,
                           "split_jump": None if idx[0] == 0 else dist[idx[0] - 1][idx[0]]})
        out.append({"id": b["id"], "album": b["album"], "n": n, "frames": frames, "stacks": stacks})
    json.dump(out, open(os.path.join(args.out, "data.json"), "w"))
    if any(b["pick"] >= 0 for b in bursts):
        for T in ("8", "12"):
            hit, eligible, kept, total = [], 0, 0, 0
            for b, o in zip(bursts, out):
                if b["pick"] < 0:
                    continue
                st = next(s for s in o["stacks"] if b["pick"] in s["idx"])
                v = st["views"][T]
                cl = v["cluster"][str(b["pick"])]
                same_cluster = [i for i in st["idx"] if v["cluster"].get(str(i)) is not None and v["cluster"][str(i)] == cl] if cl is not None else []
                survived = any(f in same_cluster or f == b["pick"] for f in v["final"]) if not st["no_winner"] else False
                groups = len({c for c in v["cluster"].values() if c is not None})
                for s2 in o["stacks"]:
                    kept += len(s2["views"][T]["final"])
                total += o["n"]
                if groups >= 3:
                    eligible += 1
                    hit.append(float(survived))
            h = np.array(hit)
            m = float(h.mean()) if len(h) else float("nan")
            rng = np.random.default_rng(0)
            bs = [h[rng.integers(0, len(h), len(h))].mean() for _ in range(4000)] if len(h) else [float("nan")]
            lo, hi = np.percentile(bs, [2.5, 97.5])
            removed = 1 - kept / total
            print(f"T={T}: bursts with >= 3 groups in the pick's stack: {eligible}; pick's cluster survives {m:.3f} [{lo:.3f}, {hi:.3f}]; frames removed overall {removed:.0%}"
                  + ("  MEETS THE RULE" if m >= 0.85 and lo > 0.70 and removed >= 0.40 else "  does not meet the rule"))
    print(len(out), "bursts,", sum(len(x["stacks"]) for x in out), "stacks written")


if __name__ == "__main__":
    main()
