#!/usr/bin/env python3
"""Do the app's ranker workers reproduce the scores we validated? Runs the real engines from image_triage.cascade.rankers.worker on real frames and
compares them with the scores computed earlier by the reference code (MANIQA: the repo's own inference in fp32; Q-ReAlign: Facet's scorer).

  python ranker_parity.py --maniqa-dir maniqa --qrealign-dir <snapshot dir> --bursts iqa_synth/burst_picks --maniqa-ref maniqa_results_burst63.json \
      --facet-db facet/photo_scores_burst63.db --count 40
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import time
from pathlib import Path

import numpy as np


def spearman(a, b) -> float:
    ra, rb = np.argsort(np.argsort(a)), np.argsort(np.argsort(b))
    return float(np.corrcoef(ra, rb)[0, 1])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--maniqa-dir", required=True)
    ap.add_argument("--qrealign-dir", required=True)
    ap.add_argument("--bursts", required=True)
    ap.add_argument("--maniqa-ref", required=True)
    ap.add_argument("--facet-db", required=True)
    ap.add_argument("--count", type=int, default=40)
    ap.add_argument("--device", default="cuda")
    args = ap.parse_args()
    root = os.path.dirname(os.path.abspath(args.bursts))
    bursts = json.load(open(os.path.join(args.bursts, "bursts.json")))
    reference = {r["id"]: r["maniqa"] for r in json.load(open(args.maniqa_ref))}
    con = sqlite3.connect(args.facet_db)
    con.row_factory = sqlite3.Row
    facet = {os.path.basename(r["path"]).lower(): r["aesthetic"] for r in con.execute("select path, aesthetic from photos")}
    items = [(b["id"], i, os.path.join(root, jpg), os.path.basename(nef).lower()) for b in bursts for i, (jpg, nef) in enumerate(zip(b["frames"], b["paths"]))]
    items = items[:: max(1, len(items) // args.count)][: args.count]

    from image_triage.cascade.rankers.worker import ManiqaEngine, QRealignEngine

    started = time.perf_counter()
    maniqa = ManiqaEngine(Path(args.maniqa_dir), args.device)
    print(f"MANIQA engine on {maniqa.device} ({'fp16' if maniqa.half else 'fp32'}), loaded in {time.perf_counter() - started:.1f}s")
    tick = time.perf_counter()
    worker_m = [maniqa.score(path) for _, _, path, _ in items]
    print(f"  {1000 * (time.perf_counter() - tick) / len(items):.0f} ms per frame")
    ref_m = [reference[bid][i] for bid, i, _, _ in items]
    print(f"  vs the validated fp32 scores: Spearman {spearman(worker_m, ref_m):.3f}, mean abs diff {np.mean(np.abs(np.array(worker_m) - np.array(ref_m))):.4f} "
          f"(scores range {min(ref_m):.3f} to {max(ref_m):.3f}), worker range {min(worker_m):.3f} to {max(worker_m):.3f}, NaNs {int(np.isnan(worker_m).sum())}")
    del maniqa

    started = time.perf_counter()
    qr = QRealignEngine(Path(args.qrealign_dir), args.device)
    print(f"Q-ReAlign engine on {qr.device} ({'fp16' if qr.half else 'fp32'}), loaded in {time.perf_counter() - started:.1f}s")
    tick = time.perf_counter()
    worker_q = [qr.score(path) for _, _, path, _ in items]
    print(f"  {1000 * (time.perf_counter() - tick) / len(items):.0f} ms per frame")
    ref_q = [facet[name] for _, _, _, name in items]
    print(f"  vs Facet's aesthetic score: Spearman {spearman(worker_q, ref_q):.3f}; worker range {min(worker_q):.3f} to {max(worker_q):.3f}; Facet range {min(ref_q):.2f} to {max(ref_q):.2f}")
    ratio = np.array(ref_q) / np.maximum(np.array(worker_q), 1e-9)
    print(f"  Facet / worker ratio: median {np.median(ratio):.2f} (a constant factor of 10 means only the scale differs)")


if __name__ == "__main__":
    main()
