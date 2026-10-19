#!/usr/bin/env python3
"""Data and thumbnails for the cascade review page: per frame, what each pass does, at pHash threshold 8 and 12.

  python3 cascade_burst_sheet.py --bursts burst_picks --maniqa ../maniqa/maniqa_results.json --facet-db ../facet/photo_scores_pro.db --out cascade_sheet
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


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bursts", required=True)
    ap.add_argument("--maniqa", required=True)
    ap.add_argument("--facet-db", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    root = os.path.dirname(os.path.abspath(args.bursts))
    os.makedirs(args.out, exist_ok=True)
    bursts = json.load(open(os.path.join(args.bursts, "bursts.json")))
    M = {r["id"]: r["maniqa"] for r in json.load(open(args.maniqa))}
    con = sqlite3.connect(args.facet_db)
    con.row_factory = sqlite3.Row
    F = {os.path.basename(r["path"]).lower(): dict(r) for r in con.execute("select * from photos")}
    # The embedded NEF preview carries no rotation; read the camera orientation so thumbnails display upright (display only, measures are unaffected).
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
        score = z(M[b["id"]]) + z([r["aesthetic"] or 0 for r in rows])
        lap = [m["lap_centre"] for m in b["measures"]]
        clip = [m["clip"] for m in b["measures"]]
        flags = []
        for i in range(n):
            f = []
            if rows[i]["is_blink"]:
                f.append("blink")
            if lap[i] < 0.4 * max(lap):
                f.append("soft")
            if clip[i] > 2 * min(clip) and clip[i] > 0.02:
                f.append("clipped")
            flags.append(f)
        alive = [i for i in range(n) if not flags[i]] or list(range(n))
        frames = []
        for i, im in enumerate(imgs):
            code = rotate.get(orient.get(b["paths"][i], 1))
            if code is not None:
                im = cv2.rotate(im, code)
            h, w = im.shape[:2]
            s = 560 / max(h, w)
            name = f"{b['id']}_{i}.jpg"
            cv2.imwrite(os.path.join(args.out, name), cv2.resize(im, (round(w * s), round(h * s)), interpolation=cv2.INTER_AREA), [cv2.IMWRITE_JPEG_QUALITY, 78])
            frames.append({"img": name, "file": os.path.basename(b["paths"][i]), "flags": flags[i], "kept": i == b["pick"], "tails": round(tails[i], 3),
                           "score": round(float(score[i]), 2), "maniqa": round(M[b["id"]][i], 3), "aes": round(rows[i]["aesthetic"] or 0, 2)})
        views = {}
        for T in (8, 12):
            clusters = complete_linkage(alive, dist, T)
            reps = {min(c, key=lambda i: tails[i]) for c in clusters}
            ranked = sorted(reps, key=lambda i: -score[i])
            final = ranked[:2]
            cl_of = {i: k for k, c in enumerate(clusters) for i in c}
            dropped_dup_of = {i: min(c, key=lambda j: tails[j]) for c in clusters for i in c if i not in reps}
            if b["pick"] < 0:
                survives = None  # unrated set: no marked frame to compare against
            elif b["pick"] in cl_of:
                survives = any(cl_of.get(b["pick"]) == cl_of.get(f) and cl_of.get(f) is not None for f in final)
            else:
                survives = any(dist[b["pick"]][f] <= T for f in final)
            views[str(T)] = {"cluster": {str(i): cl_of.get(i) for i in range(n)}, "rep": sorted(reps), "final": final, "dup_of": {str(k): v for k, v in dropped_dup_of.items()},
                             "pick_cluster_survives": survives}
        out.append({"id": b["id"], "album": b["album"], "n": n, "frames": frames, "views": views})
    json.dump(out, open(os.path.join(args.out, "data.json"), "w"))
    print(len(out), "bursts written")


if __name__ == "__main__":
    main()
