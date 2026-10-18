#!/usr/bin/env python3
"""Step 3 of the grouping spike: compare the two ideas against the human labels.

Inputs: review/pairs_meta.json, review/sampling.json and labels.json (id -> "y" same moment, "n" different, "u" unsure).
'u' labels are dropped. The comparison is:

  classical  = log time gap, pHash Hamming distance, same camera settings, exposure-bias change
  dino       = log time gap, DINOv2 cosine similarity
  combined   = all of the above

each as a logistic regression scored by repeated 5-fold cross-validation, so no pair is scored by a model that saw it.
Reported: AUROC and average precision, plain and weighted back to the full population (the sample over-represents the
pairs where the two ideas disagree), the paired bootstrap difference between classical and dino with a 95% interval,
single-score AUROCs, and the error rate of each simple rule on the disagreement pairs.

  python3 grouping_spike_analyze.py --review spike1/review --labels labels.json
"""
from __future__ import annotations

import argparse
import json
import os

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, roc_auc_score
from sklearn.model_selection import StratifiedKFold


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--review", required=True)
    parser.add_argument("--labels", required=True)
    parser.add_argument("--repeats", type=int, default=20)
    parser.add_argument("--bootstrap", type=int, default=2000)
    args = parser.parse_args()
    meta = json.load(open(os.path.join(args.review, "pairs_meta.json"), encoding="utf-8"))
    sampling = json.load(open(os.path.join(args.review, "sampling.json"), encoding="utf-8"))
    labels = json.load(open(args.labels, encoding="utf-8"))

    rows = [m for m in meta if labels.get(str(m["id"])) in ("y", "n")]
    y = np.array([1 if labels[str(m["id"])] == "y" else 0 for m in rows])
    group = np.array([m["group"] for m in rows])
    print(f"labelled {len(rows)} of {len(meta)} (unsure and unlabelled dropped); same-moment share {y.mean():.2f}")
    for g in sampling:
        sel = group == g
        if sel.any():
            print(f"  {g:15s} judged {sel.sum():3d}  same {int(y[sel].sum()):3d}  different {int((1 - y[sel]).sum()):3d}")

    weights = np.array([sampling[g]["population"] / max(1, sampling[g]["sampled"]) for g in group], float)
    dt = np.array([m["dt"] for m in rows]); cos = np.array([m["cos"] for m in rows]); ham = np.array([m["ham"] for m in rows], float)
    same_settings = np.array([float(m["same_settings"]) for m in rows])
    bias = np.array([abs((m["bias_a"] or 0.0) - (m["bias_b"] or 0.0)) for m in rows])
    log_dt = np.log(dt + 0.05)
    features = {
        "classical": np.column_stack([log_dt, ham, same_settings, bias]),
        "dino": np.column_stack([log_dt, cos]),
        "combined": np.column_stack([log_dt, ham, same_settings, bias, cos]),
    }

    print("\nSingle scores (AUROC, weighted to population):")
    for name, score in (("short time gap", -dt), ("low pHash distance", -ham), ("high DINOv2 cosine", cos)):
        print(f"  {name:22s} plain {roc_auc_score(y, score):.3f}   weighted {roc_auc_score(y, score, sample_weight=weights):.3f}")

    oof: dict[str, np.ndarray] = {}
    for name, X in features.items():
        mu, sd = X.mean(0), X.std(0) + 1e-9
        Z = (X - mu) / sd
        preds = np.zeros((args.repeats, len(y)))
        for r in range(args.repeats):
            for tr, te in StratifiedKFold(5, shuffle=True, random_state=r).split(Z, y):
                preds[r, te] = LogisticRegression(C=1.0, max_iter=2000).fit(Z[tr], y[tr], sample_weight=weights[tr] / weights[tr].mean()).predict_proba(Z[te])[:, 1]
        oof[name] = preds.mean(0)
    print("\nCross-validated logistic models:")
    for name, p in oof.items():
        print(f"  {name:10s} AUROC {roc_auc_score(y, p):.3f} (weighted {roc_auc_score(y, p, sample_weight=weights):.3f})   "
              f"avg precision {average_precision_score(y, p):.3f}")

    rng = np.random.default_rng(0)
    diffs = {"classical vs dino": ("classical", "dino"), "combined vs classical": ("combined", "classical"), "combined vs dino": ("combined", "dino")}
    print("\nPaired bootstrap of AUROC difference (95% interval over pairs):")
    for label, (a, b) in diffs.items():
        d = []
        for _ in range(args.bootstrap):
            idx = rng.integers(0, len(y), len(y))
            if y[idx].min() == y[idx].max():
                continue
            d.append(roc_auc_score(y[idx], oof[a][idx]) - roc_auc_score(y[idx], oof[b][idx]))
        lo, hi = np.percentile(d, [2.5, 97.5])
        verdict = "no reliable difference" if lo <= 0 <= hi else (f"{a} better" if lo > 0 else f"{b} better")
        print(f"  {label:24s} {np.mean(d):+.3f}  [{lo:+.3f}, {hi:+.3f}]  {verdict}")

    print("\nSimple rules on the disagreement pairs (classical: gap <= 3 s and pHash <= 12; dino: cosine >= 0.93):")
    for g in ("classical_only", "dino_only"):
        sel = group == g
        if sel.any():
            right = y[sel].mean() if g == "classical_only" else y[sel].mean()
            print(f"  {g:15s} n={sel.sum():3d}  judged the same moment: {y[sel].mean():.0%}  "
                  f"({'classical says same, dino says different' if g == 'classical_only' else 'dino says same, classical says different'})")


if __name__ == "__main__":
    main()
