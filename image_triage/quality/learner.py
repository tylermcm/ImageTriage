"""Preference learners over quality dimensions + scores (Stage B).

One small, heavily-regularized linear learner class serves both roles:

- **Per-folder learner** — fit on the current folder's labels (the primary, where
  "wow" is actually learnable).
- **Global learner** — fit on all accumulated labels (a cross-folder prior).

They are blended by confidence (``blend_local_global``): with few in-folder
labels the global prior dominates; as the folder is labeled, the per-folder
learner takes over. Both consume the same feature vector (dimensions + existing
scores, optionally embeddings).

Pure NumPy. The linear ridge keeps the model low-capacity so per-folder fits
from few labels without memorizing, and the same code generalizes to the global
pool. Evaluation is always cross-validated — never report in-sample fit.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

import numpy as np

# Interpretable default feature set: quality dimensions + the existing scores.
# Raw embeddings are added explicitly by the caller when desired (per-folder only).
DEFAULT_FEATURES: tuple[str, ...] = (
    "sharpness",
    "exposure",
    "dynamic_range",
    "noise",
    "contrast",
    "color_harmony",
    "aesthetic",
    "technical_score",
    "final_score",
)


def feature_matrix(
    rows: Sequence[Mapping[str, object]],
    feature_names: Sequence[str] = DEFAULT_FEATURES,
    *,
    impute: bool = True,
) -> tuple[np.ndarray, tuple[str, ...]]:
    """Build an (n_rows x n_features) matrix from per-image feature dicts.

    Missing/None values are imputed with the column mean (or 0 if a whole
    column is missing) so a partially-scored image still contributes.
    """
    names = tuple(feature_names)
    matrix = np.full((len(rows), len(names)), np.nan, dtype=np.float64)
    for i, row in enumerate(rows):
        for j, name in enumerate(names):
            value = row.get(name)
            if value is None:
                continue
            try:
                matrix[i, j] = float(value)
            except (TypeError, ValueError):
                continue
    if impute and matrix.size:
        col_means = np.nanmean(matrix, axis=0)
        col_means = np.where(np.isnan(col_means), 0.0, col_means)
        nan_rows, nan_cols = np.where(np.isnan(matrix))
        matrix[nan_rows, nan_cols] = np.take(col_means, nan_cols)
    return matrix, names


@dataclass
class RidgePreferenceLearner:
    """Standardized ridge regression from features to label score."""

    alpha: float = 2.0
    mu_: np.ndarray | None = None
    sd_: np.ndarray | None = None
    weights_: np.ndarray | None = None
    bias_: float = 0.0

    def fit(self, X: np.ndarray, y: np.ndarray) -> "RidgePreferenceLearner":
        X = np.asarray(X, dtype=np.float64)
        y = np.asarray(y, dtype=np.float64)
        self.mu_ = X.mean(axis=0)
        self.sd_ = X.std(axis=0) + 1e-9
        standardized = (X - self.mu_) / self.sd_
        self.bias_ = float(y.mean())
        gram = standardized.T @ standardized + self.alpha * np.eye(standardized.shape[1])
        self.weights_ = np.linalg.solve(gram, standardized.T @ (y - self.bias_))
        return self

    def predict(self, X: np.ndarray) -> np.ndarray:
        if self.weights_ is None:
            raise RuntimeError("learner is not fitted")
        standardized = (np.asarray(X, dtype=np.float64) - self.mu_) / self.sd_
        return standardized @ self.weights_ + self.bias_


def cross_val_predict(
    X: np.ndarray, y: np.ndarray, *, folds: int = 5, alpha: float = 2.0, seed: int = 0
) -> np.ndarray:
    """Out-of-fold predictions — the only honest way to score a per-folder fit."""
    X = np.asarray(X, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    n = len(y)
    if n < 2:
        return np.full(n, float(y.mean()) if n else 0.0, dtype=np.float64)
    folds = max(2, min(folds, n))
    order = np.arange(n)
    np.random.default_rng(seed).shuffle(order)
    parts = np.array_split(order, folds)
    predictions = np.zeros(n, dtype=np.float64)
    for k in range(folds):
        test = parts[k]
        train = np.concatenate([parts[j] for j in range(folds) if j != k])
        model = RidgePreferenceLearner(alpha=alpha).fit(X[train], y[train])
        predictions[test] = model.predict(X[test])
    return predictions


def blend_weight(n_local: int, *, ramp: int = 20) -> float:
    """Confidence weight on the per-folder learner: 0 at no labels -> ~1 as they accrue."""
    n = max(0, int(n_local))
    denom = n + max(1, int(ramp))
    return n / denom


def blend_local_global(local: float, global_: float, n_local: int, *, ramp: int = 20) -> float:
    w = blend_weight(n_local, ramp=ramp)
    return w * float(local) + (1.0 - w) * float(global_)
