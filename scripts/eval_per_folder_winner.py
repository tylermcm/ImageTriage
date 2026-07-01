#!/usr/bin/env python3
"""Reproducible per-folder winner-ranking evaluation.

Loads labeled images + CLIP embeddings + existing scores from a folder's
``aiculler.sqlite`` and reports the cross-validated per-folder learner against
baselines (random floor, existing stored scores). This makes the headline
number reproducible from the repo instead of an ephemeral claim, and doubles as
the seed of the Phase 6 evaluation harness.

Embeddings-only by default (no image decode -> fast, DB-only). Pass
``--with-dimensions`` to also evaluate the classical CV dimensions (requires
Pillow and reading the image files).

Usage:
    python scripts/eval_per_folder_winner.py --db "K:/Photography/Canada 10-25/.image_triage_ai/artifacts/aiculler.sqlite"

Requires the project environment (imports image_triage.quality).
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from image_triage.quality import analyze_technical  # noqa: E402
from image_triage.quality.analysis import spearman  # noqa: E402
from image_triage.quality.learner import (  # noqa: E402
    RidgePreferenceLearner,
    cross_val_predict,
    feature_matrix,
)

_DIM_FEATURES = [
    "sharpness", "exposure", "dynamic_range", "noise", "contrast",
    "color_harmony", "technical_score", "final_score",
]


def load_labeled(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    conn.row_factory = sqlite3.Row
    return conn.execute(
        """
        SELECT r.image_id, r.numeric_score AS label, i.source_path AS path,
               i.technical_score AS technical_score, i.final_score AS final_score,
               em.embedding AS emb, em.dtype AS dt
        FROM ratings r
        JOIN images i ON i.id = r.image_id
        JOIN embeddings em ON em.image_id = r.image_id
        """
    ).fetchall()


def distinct_folders(rows: list[sqlite3.Row]) -> int:
    return len({str(Path(str(r["path"])).parent) for r in rows if r["path"]})


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", required=True, type=Path, help="path to a folder's aiculler.sqlite")
    ap.add_argument("--with-dimensions", action="store_true", help="also score classical CV dims (loads images)")
    ap.add_argument("--alpha", type=float, default=30.0, help="ridge regularization for embeddings")
    ap.add_argument("--folds", type=int, default=5)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    if not args.db.exists():
        print(f"db not found: {args.db}", file=sys.stderr)
        return 2

    conn = sqlite3.connect(str(args.db))
    rows = load_labeled(conn)
    if not rows:
        print("No labeled rows with embeddings found in this DB.", file=sys.stderr)
        return 1

    labels = np.array([r["label"] for r in rows], dtype=np.float64)
    embs = np.vstack([np.frombuffer(r["emb"], dtype=np.dtype(r["dt"])).astype(np.float64) for r in rows])
    n = len(labels)

    print(f"db:                 {args.db}")
    print(f"labeled w/ embed:   {n}")
    print(f"distinct folders:   {distinct_folders(rows)}  (this DB is one folder; cross-folder needs the global eval)")
    print(f"embedding dim:      {embs.shape[1]}")
    print()
    print(f"{'signal':<40}{'spearman vs label':>18}{'n':>6}")

    def report(name: str, preds) -> None:
        rho, m = spearman(preds, labels)
        print(f"{name:<40}{(round(rho, 3) if rho is not None else None)!s:>18}{m:>6}")

    rng = np.random.default_rng(args.seed)
    report("random (floor)", rng.normal(size=n))
    report("existing technical_score (TOPIQ)", [r["technical_score"] for r in rows])
    report("existing final_score", [r["final_score"] for r in rows])

    cv = cross_val_predict(embs, labels, folds=args.folds, alpha=args.alpha, seed=args.seed)
    report(f"per-folder embeddings (CV, {args.folds}-fold)", cv)
    insample = RidgePreferenceLearner(alpha=args.alpha).fit(embs, labels).predict(embs)
    report("per-folder embeddings (IN-SAMPLE, overfit ref)", insample)

    if args.with_dimensions:
        from PIL import Image

        dimrows: list[dict[str, object]] = []
        loaded = 0
        for r in rows:
            entry: dict[str, object] = {"technical_score": r["technical_score"], "final_score": r["final_score"]}
            try:
                im = Image.open(str(r["path"])).convert("RGB")
                im.thumbnail((1400, 1400))
                s = analyze_technical(np.asarray(im)[:, :, ::-1])
                entry.update({
                    "sharpness": s.sharpness, "exposure": s.exposure, "dynamic_range": s.dynamic_range,
                    "noise": s.noise, "contrast": s.contrast, "color_harmony": s.color_harmony,
                })
                loaded += 1
            except Exception:
                pass
            dimrows.append(entry)
        X, _ = feature_matrix(dimrows, _DIM_FEATURES)
        report(f"per-folder dims+scores (CV, {loaded}/{n} decoded)", cross_val_predict(X, labels, folds=args.folds, alpha=2.0, seed=args.seed))

    print()
    print("CV = honest out-of-fold (the number to trust). In-sample shows the overfit gap.")
    print("The per-folder learner need not transfer across folders; the global adapter is the cross-folder prior.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
