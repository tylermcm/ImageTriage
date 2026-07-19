"""End-to-end per-folder winner ranking (Stage B integration).

Fits the per-folder learner on a folder's labeled embeddings, scores every image
in the folder, and blends with the global prior by confidence. As the folder is
labeled, the per-folder learner takes over (``learner.blend_weight``).

Before there are enough labels to fit anything, ranking falls back to the base
composite score rather than the global prior — leave-one-folder-out over
Banff/Canada/China puts the prior at ~-0.06 rank correlation on folders it has
not seen (no better than random), while the base composite reaches ~+0.22.

Per-folder predictions and global scores live on different scales, so both are
converted to within-folder percentile ranks before blending — the output is a
ranking, which is what the UI consumes.

The core (``rank_folder_winners``) is pure (arrays in, ranked dataclasses out) so
it tests without a DB; ``load_winner_inputs`` is a thin SQLite adapter.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Sequence

import numpy as np

from .learner import RidgePreferenceLearner, blend_weight

# Below this many in-folder labels, don't fit a per-folder model — use the global
# prior alone (cold start).
DEFAULT_MIN_LABELS = 8


@dataclass(slots=True)
class WinnerScore:
    image_id: int
    blended: float  # within-folder ranking key, 0-1
    per_folder: float | None  # raw learner prediction (None when cold-start)
    global_score: float | None
    source: str  # "blend" | "per_folder" | "global"


def _percentile_ranks(values: np.ndarray) -> np.ndarray:
    """Map values to [0, 1] ranks (0 = lowest, 1 = highest)."""
    values = np.asarray(values, dtype=np.float64)
    n = values.size
    if n <= 1:
        return np.zeros(n, dtype=np.float64)
    order = np.argsort(np.argsort(values, kind="mergesort"), kind="mergesort")
    return order.astype(np.float64) / (n - 1)


def _optional_percentiles(
    scores: Sequence[float | None] | None, count: int
) -> tuple[np.ndarray | None, list[float | None]]:
    """Percentile-rank an optional score column; missing values sort to the bottom."""
    raw_out: list[float | None] = [None] * count
    if scores is None or not any(s is not None for s in scores):
        return None, raw_out
    raw = np.array([np.nan if s is None else float(s) for s in scores], dtype=np.float64)
    raw_out = [None if np.isnan(v) else float(v) for v in raw]
    filled = np.nan_to_num(raw, nan=float(np.nanmin(raw)))
    return _percentile_ranks(filled), raw_out


def rank_folder_winners(
    labeled_embeddings: np.ndarray,
    labeled_labels: Sequence[float],
    all_ids: Sequence[int],
    all_embeddings: np.ndarray,
    global_scores: Sequence[float | None] | None = None,
    base_scores: Sequence[float | None] | None = None,
    *,
    alpha: float = 30.0,
    ramp: int = 20,
    min_labels: int = DEFAULT_MIN_LABELS,
) -> list[WinnerScore]:
    all_ids = list(all_ids)
    all_embeddings = np.asarray(all_embeddings, dtype=np.float64)
    n_local = len(labeled_labels)

    global_pct, global_raw = _optional_percentiles(global_scores, len(all_ids))
    base_pct, _ = _optional_percentiles(base_scores, len(all_ids))

    local_pct = None
    local_raw = None
    weight = 0.0
    if n_local >= min_labels:
        learner = RidgePreferenceLearner(alpha=alpha).fit(
            np.asarray(labeled_embeddings, dtype=np.float64),
            np.asarray(labeled_labels, dtype=np.float64),
        )
        local_raw = learner.predict(all_embeddings)
        local_pct = _percentile_ranks(local_raw)
        weight = blend_weight(n_local, ramp=ramp)

    results: list[WinnerScore] = []
    for i, image_id in enumerate(all_ids):
        if local_pct is not None and global_pct is not None:
            blended = weight * local_pct[i] + (1.0 - weight) * global_pct[i]
            source = "blend"
        elif local_pct is not None:
            blended = float(local_pct[i])
            source = "per_folder"
        elif base_pct is not None:
            # Cold start: the global prior measures ~-0.06 rank correlation on folders
            # it has not been trained on (leave-one-folder-out over Banff/Canada/China),
            # i.e. no better than shuffling. The base composite scores ~+0.22 on the one
            # folder with no adapter contamination, so it leads until a per-folder
            # learner exists.
            blended = float(base_pct[i])
            source = "base"
        elif global_pct is not None:
            blended = float(global_pct[i])
            source = "global"
        else:
            blended = 0.5
            source = "global"
        results.append(
            WinnerScore(
                image_id=int(image_id),
                blended=float(blended),
                per_folder=float(local_raw[i]) if local_raw is not None else None,
                global_score=global_raw[i],
                source=source,
            )
        )
    results.sort(key=lambda r: r.blended, reverse=True)
    return results


def load_winner_inputs(
    connection: sqlite3.Connection, *, model_version: str | None = None
) -> tuple[np.ndarray, list[float], list[int], np.ndarray, list[float | None], list[float | None]]:
    """Pull labeled embeddings+labels and all embeddings(+global and base score) from a folder DB."""
    previous = connection.row_factory
    connection.row_factory = sqlite3.Row
    try:
        if model_version is None:
            try:
                row = connection.execute(
                    """
                    SELECT model_version
                    FROM adapter_models
                    ORDER BY created_at DESC, model_version DESC
                    LIMIT 1
                    """
                ).fetchone()
            except sqlite3.Error:
                row = None
            model_version = row["model_version"] if row else None

        all_rows = connection.execute(
            """
            SELECT images.id AS image_id, embeddings.embedding AS emb, embeddings.dtype AS dt,
                   adapter_scores.adapter_score AS global_score,
                   COALESCE(images.final_score, images.technical_score) AS base_score
            FROM images
            JOIN embeddings ON embeddings.image_id = images.id
            LEFT JOIN adapter_scores
              ON adapter_scores.image_id = images.id AND adapter_scores.model_version = ?
            """,
            (str(model_version) if model_version is not None else "",),
        ).fetchall()

        labeled_rows = connection.execute(
            """
            SELECT ratings.image_id AS image_id, ratings.numeric_score AS label,
                   embeddings.embedding AS emb, embeddings.dtype AS dt
            FROM ratings
            JOIN embeddings ON embeddings.image_id = ratings.image_id
            """
        ).fetchall()
    finally:
        connection.row_factory = previous

    def _vec(row: sqlite3.Row) -> np.ndarray:
        return np.frombuffer(row["emb"], dtype=np.dtype(row["dt"])).astype(np.float64)

    all_ids = [int(r["image_id"]) for r in all_rows]
    all_emb = np.vstack([_vec(r) for r in all_rows]) if all_rows else np.empty((0, 0))
    global_scores: list[float | None] = [
        None if r["global_score"] is None else float(r["global_score"]) for r in all_rows
    ]
    base_scores: list[float | None] = [
        None if r["base_score"] is None else float(r["base_score"]) for r in all_rows
    ]
    labeled_emb = np.vstack([_vec(r) for r in labeled_rows]) if labeled_rows else np.empty((0, 0))
    labeled_labels = [float(r["label"]) for r in labeled_rows]
    return labeled_emb, labeled_labels, all_ids, all_emb, global_scores, base_scores
