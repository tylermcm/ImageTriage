"""Reason-sliced correlation diagnostics for quality dimensions (Phase 3 gate).

Answers the question the whole investigation hinges on: do the explicit
dimensions actually predict the user's labels, and — crucially — the *specific*
reasons they reject images for? The aggregate label correlation can look weak
(the blended label mixes technical, duplicate, boring, composition) while a
dimension correlates strongly with its matching reason tag.

Pure NumPy, no scipy. Spearman = Pearson on average-tie ranks. Every coefficient
is reported with its ``n`` so small-sample (noisy) results can be flagged, not
trusted.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Mapping, Sequence

import numpy as np

# Below this many points a correlation is too noisy to trust.
_MIN_N = 8


def _rankdata(values: np.ndarray) -> np.ndarray:
    """Average-tie ranks (scipy.stats.rankdata 'average' equivalent)."""
    sorter = np.argsort(values, kind="mergesort")
    inv = np.empty(len(values), dtype=np.intp)
    inv[sorter] = np.arange(len(values))
    sorted_values = values[sorter]
    obs = np.r_[True, sorted_values[1:] != sorted_values[:-1]]
    dense = obs.cumsum()[inv]
    counts = np.r_[np.nonzero(obs)[0], len(values)]
    return 0.5 * (counts[dense] + counts[dense - 1] + 1)


def spearman(x: Sequence[float], y: Sequence[float]) -> tuple[float | None, int]:
    """Return (rho, n) over finite paired points; rho is None if not computable."""
    xa = np.asarray(x, dtype=np.float64)
    ya = np.asarray(y, dtype=np.float64)
    mask = np.isfinite(xa) & np.isfinite(ya)
    xa, ya = xa[mask], ya[mask]
    n = int(xa.size)
    if n < 3:
        return None, n
    rx, ry = _rankdata(xa), _rankdata(ya)
    if rx.std() == 0 or ry.std() == 0:
        return None, n
    return float(np.corrcoef(rx, ry)[0, 1]), n


def _grouped_spearman(rows: Sequence[Mapping[str, object]], dim: str, target: str) -> dict[str, object]:
    px, py = [], []
    for row in rows:
        xv, yv = row.get(dim), row.get(target)
        px.append(float(xv) if xv is not None else np.nan)
        py.append(float(yv) if yv is not None else np.nan)
    rho, n = spearman(px, py)

    per_folder: list[dict[str, object]] = []
    by_folder: dict[str, list[Mapping[str, object]]] = defaultdict(list)
    for row in rows:
        by_folder[str(row.get("folder_id") or "")].append(row)
    signs: list[float] = []
    for folder_id, folder_rows in sorted(by_folder.items()):
        fx = [float(r[dim]) if r.get(dim) is not None else np.nan for r in folder_rows]
        fy = [float(r[target]) if r.get(target) is not None else np.nan for r in folder_rows]
        frho, fn = spearman(fx, fy)
        per_folder.append({"folder_id": folder_id, "rho": frho, "n": fn, "trusted": fn >= _MIN_N})
        if frho is not None and fn >= _MIN_N:
            signs.append(frho)
    sign_flips = sum(1 for s in signs if (s > 0) != (signs[0] > 0)) if signs else 0
    return {
        "rho": rho,
        "n": n,
        "trusted": n >= _MIN_N,
        "per_folder": per_folder,
        "sign_flips": sign_flips,
    }


def dimension_label_correlations(
    rows: Sequence[Mapping[str, object]],
    *,
    dimensions: Sequence[str],
    reasons: Sequence[str] | None = None,
    label_key: str = "numeric_score",
) -> dict[str, object]:
    """Correlate each dimension against the overall label and each reason tag.

    Each row needs the dimension values, ``label_key`` (ordinal label),
    ``reason_tags`` (tuple/list), and ``folder_id``. Returns global + per-folder
    Spearman (with n and sign-flip count) for the label and for a binary
    indicator of each reason tag.
    """
    rows = list(rows)
    # Discover reasons if not given; keep only those with enough support.
    tag_counts: dict[str, int] = defaultdict(int)
    for row in rows:
        for tag in row.get("reason_tags") or ():
            tag_counts[str(tag)] += 1
    if reasons is None:
        reasons = [tag for tag, count in tag_counts.items() if count >= _MIN_N]
    reasons = sorted(reasons)

    # Materialize per-row reason indicators.
    enriched: list[dict[str, object]] = []
    for row in rows:
        tags = {str(t) for t in (row.get("reason_tags") or ())}
        new = dict(row)
        new[label_key] = float(row[label_key]) if row.get(label_key) is not None else None
        for reason in reasons:
            new[f"reason::{reason}"] = 1.0 if reason in tags else 0.0
        enriched.append(new)

    features: dict[str, object] = {}
    for dim in dimensions:
        entry: dict[str, object] = {"vs_label": _grouped_spearman(enriched, dim, label_key)}
        for reason in reasons:
            entry[f"vs_reason:{reason}"] = _grouped_spearman(enriched, dim, f"reason::{reason}")
        features[dim] = entry

    return {
        "row_count": len(rows),
        "reason_counts": dict(sorted(tag_counts.items())),
        "reasons_analyzed": reasons,
        "min_n": _MIN_N,
        "features": features,
    }
