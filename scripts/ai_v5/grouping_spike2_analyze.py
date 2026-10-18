#!/usr/bin/env python3
"""Round 2 analysis: a stronger classical feature set against DINOv2 variants, judged by the owner.

Models are logistic regressions (plus a small boosted-tree model for the classical side so it gets a fair nonlinear shot),
scored by leave-one-album-out cross-validation: every pair is predicted by a model that never saw its album.

Decision rule fixed before the labels came in: adopt DINOv2 only if adding it to the strong classical set improves AUROC by
at least +0.02 with a 95% interval above zero on BOTH the hard pairs and the sequence steps.

  python3 grouping_spike2_analyze.py --spike spike2 --labels spike2/labels2.json --round1 spike1
"""
from __future__ import annotations

import argparse
import json
import os

import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import LeaveOneGroupOut

from spike_common import load_features, orb_inlier_ratio, pair_features, thumb_path

CLASSICAL_BASIC = ["log_dt", "ham_pn", "same_settings", "bias_diff"]
CLASSICAL_STRONG = CLASSICAL_BASIC + ["ham_p", "ham_d", "hist_dist", "ssim", "orb", "shutter_stops", "portrait_change"]
DINO_PRIMARY = ["log_dt", "cos_cls"]


def oof_predict(X: np.ndarray, y: np.ndarray, groups: np.ndarray, kind: str = "lr") -> np.ndarray:
    pred = np.zeros(len(y))
    for tr, te in LeaveOneGroupOut().split(X, y, groups):
        mu, sd = X[tr].mean(0), X[tr].std(0) + 1e-9
        Xtr, Xte = (X[tr] - mu) / sd, (X[te] - mu) / sd
        if kind == "gbm":
            model = HistGradientBoostingClassifier(max_depth=3, learning_rate=0.08, max_iter=120, min_samples_leaf=8, l2_regularization=1.0)
        else:
            model = LogisticRegression(C=1.0, max_iter=3000)
        pred[te] = model.fit(Xtr, y[tr]).predict_proba(Xte)[:, 1]
    return pred


def auc(y, p) -> float:
    return float(roc_auc_score(y, p)) if len(set(y)) == 2 else float("nan")


def paired_bootstrap(y, p_a, p_b, n=2000, seed=0):
    rng = np.random.default_rng(seed)
    d = []
    for _ in range(n):
        i = rng.integers(0, len(y), len(y))
        if y[i].min() == y[i].max():
            continue
        d.append(roc_auc_score(y[i], p_a[i]) - roc_auc_score(y[i], p_b[i]))
    lo, hi = np.percentile(d, [2.5, 97.5])
    return float(np.mean(d)), float(lo), float(hi)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--spike", required=True)
    parser.add_argument("--labels", required=True)
    parser.add_argument("--round1", required=True)
    args = parser.parse_args()

    f = load_features(os.path.join(args.spike, "features.npz"))
    meta = json.load(open(os.path.join(args.spike, "review", "items_meta.json"), encoding="utf-8"))
    labels = json.load(open(args.labels, encoding="utf-8"))
    thumbs = os.path.join(args.spike, "thumbs")

    # ---- judge consistency from the 20 repeated round 1 pairs
    reps = [m for m in meta if m["kind"] == "repeat" and labels.get(m["id"]) in ("y", "n")]
    if reps:
        agree = sum(labels[m["id"]] == m["round1_label"] for m in reps)
        print(f"Judge consistency: {agree} of {len(reps)} repeated round 1 pairs got the same answer ({agree / len(reps):.0%})")
        for m in reps:
            if labels[m["id"]] != m["round1_label"]:
                print(f"   changed {m['round1_label']} -> {labels[m['id']]} ({m['id']})")

    rows = [m for m in meta if m["kind"] in ("pair", "step") and labels.get(m["id"]) in ("y", "n")]
    a = np.array([m["a"] for m in rows]); b = np.array([m["b"] for m in rows])
    y = np.array([1 if labels[m["id"]] == "y" else 0 for m in rows])
    part = np.array([m["kind"] for m in rows])
    album = np.array([m["album"] for m in rows])
    photog = np.array([m["photographer"] for m in rows])
    print(f"\nJudged pairs: {len(rows)}  (hard pairs {int((part == 'pair').sum())}, sequence steps {int((part == 'step').sum())})")
    for kind in ("pair", "step"):
        s = part == kind
        print(f"  {kind:5s} same {int(y[s].sum()):3d}  different {int((1 - y[s]).sum()):3d}")
    for name in sorted(set(album)):
        s = album == name
        print(f"  {name:18s} n={int(s.sum()):3d} different {int((1 - y[s]).sum()):3d}")

    feats = pair_features(f, a, b)
    feats["orb"] = np.array([orb_inlier_ratio(thumb_path(thumbs, f["index"][i]), thumb_path(thumbs, f["index"][j])) for i, j in zip(a, b)])
    for key in ("cos_cls", "cos_mean", "cos_cls_hi", "cos_mean_hi", "cos_cat"):
        pass

    def matrix(names):
        return np.column_stack([feats[n] for n in names])

    sets = {
        "classical (basic: time, pHash, settings, bias)": (CLASSICAL_BASIC, "lr"),
        "classical (strong, logistic)": (CLASSICAL_STRONG, "lr"),
        "classical (strong, boosted trees)": (CLASSICAL_STRONG, "gbm"),
        "DINOv2 CLS (primary)": (DINO_PRIMARY, "lr"),
        "DINOv2 patch-mean": (["log_dt", "cos_mean"], "lr"),
        "DINOv2 CLS+mean": (["log_dt", "cos_cat"], "lr"),
        "DINOv2 CLS, high-res": (["log_dt", "cos_cls_hi"], "lr"),
        "DINOv2 patch-mean, high-res": (["log_dt", "cos_mean_hi"], "lr"),
        "strong classical + DINOv2 CLS": (CLASSICAL_STRONG + ["cos_cls"], "lr"),
        "strong classical + all DINOv2": (CLASSICAL_STRONG + ["cos_cls", "cos_mean", "cos_cls_hi", "cos_mean_hi"], "lr"),
    }
    oof = {name: oof_predict(matrix(cols), y, album, kind) for name, (cols, kind) in sets.items()}

    print("\nSingle scores, AUROC on all judged pairs (no model, no training):")
    for label, score in (("short time gap", -feats["dt"]), ("low pHash distance", -feats["ham_pn"]), ("low dHash distance", -feats["ham_d"]),
                         ("high SSIM", feats["ssim"]), ("ORB geometric match", feats["orb"]), ("colour histogram", -feats["hist_dist"]),
                         ("DINOv2 CLS cosine", feats["cos_cls"]), ("DINOv2 patch-mean cosine", feats["cos_mean"])):
        print(f"  {label:26s} {auc(y, score):.3f}   hard pairs {auc(y[part == 'pair'], score[part == 'pair']):.3f}   steps {auc(y[part == 'step'], score[part == 'step']):.3f}")

    print("\nLeave-one-album-out AUROC (pooled / hard pairs / sequence steps / Alaska-Lamont only / owner only):")
    for name, p in oof.items():
        s_pair, s_step = part == "pair", part == "step"
        lam, ty = photog == "lamont", photog == "owner"
        print(f"  {name:48s} {auc(y, p):.3f}  {auc(y[s_pair], p[s_pair]):.3f}  {auc(y[s_step], p[s_step]):.3f}  {auc(y[lam], p[lam]):.3f}  {auc(y[ty], p[ty]):.3f}")

    print("\nPaired bootstrap of AUROC differences (95% interval):")
    comparisons = [
        ("DINOv2 CLS vs strong classical (logistic)", "DINOv2 CLS (primary)", "classical (strong, logistic)"),
        ("DINOv2 CLS vs strong classical (trees)", "DINOv2 CLS (primary)", "classical (strong, boosted trees)"),
        ("strong classical vs basic classical", "classical (strong, logistic)", "classical (basic: time, pHash, settings, bias)"),
        ("strong + DINOv2 vs strong classical", "strong classical + DINOv2 CLS", "classical (strong, logistic)"),
        ("strong + all DINOv2 vs strong classical", "strong classical + all DINOv2", "classical (strong, logistic)"),
    ]
    results = {}
    for label, na, nb in comparisons:
        row = []
        for kind in ("all", "pair", "step"):
            s = np.ones(len(y), bool) if kind == "all" else part == kind
            d, lo, hi = paired_bootstrap(y[s], oof[na][s], oof[nb][s])
            row.append((kind, d, lo, hi))
        results[label] = row
        text = "   ".join(f"{k}: {d:+.3f} [{lo:+.3f}, {hi:+.3f}]" for k, d, lo, hi in row)
        print(f"  {label:44s} {text}")

    gain = results["strong + DINOv2 vs strong classical"]
    pair_ok = gain[1][2] >= 0.0 and gain[1][1] >= 0.02
    step_ok = gain[2][2] >= 0.0 and gain[2][1] >= 0.02
    print("\nDecision rule (adopt DINOv2 only if the gain is >= +0.02 with a 95% interval above zero on BOTH hard pairs and steps):")
    print(f"  hard pairs: gain {gain[1][1]:+.3f}, interval [{gain[1][2]:+.3f}, {gain[1][3]:+.3f}] -> {'meets the rule' if gain[1][2] > 0 and gain[1][1] >= 0.02 else 'does not meet the rule'}")
    print(f"  seq steps:  gain {gain[2][1]:+.3f}, interval [{gain[2][2]:+.3f}, {gain[2][3]:+.3f}] -> {'meets the rule' if gain[2][2] > 0 and gain[2][1] >= 0.02 else 'does not meet the rule'}")

    # ---- gating: run DINOv2 only on pairs the strong classical model is unsure about
    base, both = oof["classical (strong, logistic)"], oof["strong classical + DINOv2 CLS"]
    print("\nGated use of DINOv2 (leave-one-album-out): the combined model only where the classical model is unsure,")
    print("so only those photos would need an embedding. 'share gated' is the share of judged pairs that would need DINOv2.")
    print(f"  {'band for classical p':22s} share gated   AUROC pooled   hard pairs   sequence steps")
    print(f"  {'never (classical only)':22s} {0:11.0%}   {auc(y, base):13.3f}   {auc(y[part == 'pair'], base[part == 'pair']):10.3f}   {auc(y[part == 'step'], base[part == 'step']):14.3f}")
    for lo, hi in ((0.40, 0.60), (0.30, 0.70), (0.20, 0.80), (0.10, 0.90), (0.0, 1.0)):
        gate = (base >= lo) & (base <= hi)
        pred = np.where(gate, both, base)
        label = f"{lo:.2f} to {hi:.2f}" if (lo, hi) != (0.0, 1.0) else "always (combined)"
        print(f"  {label:22s} {gate.mean():11.0%}   {auc(y, pred):13.3f}   {auc(y[part == 'pair'], pred[part == 'pair']):10.3f}   {auc(y[part == 'step'], pred[part == 'step']):14.3f}")
    step_gate = ((base >= 0.2) & (base <= 0.8))[part == "step"].mean()
    print(f"  in ordinary consecutive frames (sequence steps) only {step_gate:.0%} of pairs fall in the 0.2 to 0.8 band")

    # ---- sequence (cluster-level) test: pairwise precision/recall of the grouping inside each strip
    strips = json.load(open(os.path.join(args.spike, "review", "strips.json"), encoding="utf-8"))["strips"]
    step_prob_idx = {(m["strip"], m["step"]): k for k, m in enumerate(rows) if m["kind"] == "step"}
    from scipy.cluster.hierarchy import fcluster, linkage
    from scipy.spatial.distance import squareform

    def strip_partition_from_human(s_id):
        frames = strips[s_id]
        seg, cur = [0], 0
        for step in range(len(frames) - 1):
            k = step_prob_idx.get((s_id, step))
            if k is None:
                return None
            if y[k] == 0:
                cur += 1
            seg.append(cur)
        return np.array(seg)

    models = {
        "classical (strong, logistic)": (CLASSICAL_STRONG, "lr"),
        "DINOv2 CLS (primary)": (DINO_PRIMARY, "lr"),
        "strong classical + DINOv2 CLS": (CLASSICAL_STRONG + ["cos_cls"], "lr"),
    }
    thresholds = np.round(np.arange(0.05, 0.96, 0.05), 2)
    print("\nSequence test: every frame pair inside each 10-frame strip, average-linkage grouping, swept over cut-offs")
    curves = {}
    n_pairs = sum(len(s) * (len(s) - 1) // 2 for s in strips)
    for name, (cols, kind) in models.items():
        counts = {t: [0, 0, 0, 0] for t in thresholds}  # tp, fm, fs, tn
        for s_id, frames in enumerate(strips):
            human = strip_partition_from_human(s_id)
            if human is None:
                continue
            idx = np.array(frames)
            i_idx, j_idx = np.triu_indices(len(idx), 1)
            all_feats = pair_features(f, idx[i_idx], idx[j_idx])
            all_feats["orb"] = np.array([orb_inlier_ratio(thumb_path(thumbs, f["index"][idx[i]]), thumb_path(thumbs, f["index"][idx[j]])) for i, j in zip(i_idx, j_idx)])
            this_album = str(f["folder"][idx[0]])
            tr = album != this_album
            X = matrix(cols)[tr]
            mu, sd = X.mean(0), X.std(0) + 1e-9
            model = LogisticRegression(C=1.0, max_iter=3000).fit((X - mu) / sd, y[tr])
            Xall = np.column_stack([all_feats[c] for c in cols])
            p = model.predict_proba((Xall - mu) / sd)[:, 1]
            dist = np.zeros((len(idx), len(idx)))
            dist[i_idx, j_idx] = dist[j_idx, i_idx] = 1.0 - p
            tree = linkage(squareform(dist, checks=False), "average")
            h_same = human[i_idx] == human[j_idx]
            for t in thresholds:
                assigned = fcluster(tree, t=1.0 - t, criterion="distance")
                m_same = assigned[i_idx] == assigned[j_idx]
                counts[t][0] += int((h_same & m_same).sum()); counts[t][1] += int((~h_same & m_same).sum())
                counts[t][2] += int((h_same & ~m_same).sum()); counts[t][3] += int((~h_same & ~m_same).sum())
        curves[name] = counts
    human_diff = max(c[1] + c[3] for c in next(iter(curves.values())).values())
    print(f"  {n_pairs} frame pairs; {human_diff} of them are different-moment pairs (the owner broke the strip between them)")
    print("  Different-moment pairs wrongly merged (false merges) when the method keeps at least this share of same-moment pairs together:")
    print(f"  {'method':34s} recall>=0.99     recall>=0.95     recall>=0.90     recall>=0.80")
    for name, counts in curves.items():
        cells = []
        for target in (0.99, 0.95, 0.90, 0.80):
            ok = [(c[1], t) for t, c in counts.items() if (c[0] / max(1, c[0] + c[2])) >= target]
            cells.append(f"{min(ok)[0]:4d} (p>={min(ok)[1]:.2f})" if ok else "   n/a        ")
        print(f"  {name:34s} " + "   ".join(cells))
    print(f"  (lower is better; there are {human_diff} different-moment pairs in total)")


if __name__ == "__main__":
    main()
