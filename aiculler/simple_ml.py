from __future__ import annotations

import numpy as np


class PrincipalProjector:
    """Small PCA-style projector backed by NumPy SVD."""

    def __init__(self, n_components: int):
        self.n_components = int(n_components)
        self.mean_: np.ndarray | None = None
        self.components_: np.ndarray | None = None

    def fit(self, values: np.ndarray) -> "PrincipalProjector":
        values = np.asarray(values, dtype=np.float32)
        if values.ndim != 2 or values.shape[0] == 0:
            raise ValueError("PrincipalProjector requires a non-empty 2D array")
        n_components = min(self.n_components, values.shape[0], values.shape[1])
        self.mean_ = values.mean(axis=0)
        centered = values - self.mean_
        if values.shape[0] == 1:
            components = np.eye(values.shape[1], dtype=np.float32)[:n_components]
        else:
            _, _, vt = np.linalg.svd(centered, full_matrices=False)
            components = vt[:n_components].astype(np.float32)
        self.components_ = components
        return self

    def partial_fit(self, values: np.ndarray) -> "PrincipalProjector":
        return self.fit(values)

    def transform(self, values: np.ndarray) -> np.ndarray:
        if self.mean_ is None or self.components_ is None:
            raise RuntimeError("PrincipalProjector must be fitted before transform")
        values = np.asarray(values, dtype=np.float32)
        return (values - self.mean_) @ self.components_.T

    def fit_transform(self, values: np.ndarray) -> np.ndarray:
        return self.fit(values).transform(values)


class Standardizer:
    def __init__(self):
        self.mean_: np.ndarray | None = None
        self.scale_: np.ndarray | None = None

    def fit(self, values: np.ndarray) -> "Standardizer":
        values = np.asarray(values, dtype=np.float32)
        self.mean_ = values.mean(axis=0)
        scale = values.std(axis=0)
        self.scale_ = np.where(scale == 0.0, 1.0, scale).astype(np.float32)
        return self

    def transform(self, values: np.ndarray) -> np.ndarray:
        if self.mean_ is None or self.scale_ is None:
            raise RuntimeError("Standardizer must be fitted before transform")
        return (np.asarray(values, dtype=np.float32) - self.mean_) / self.scale_

    def fit_transform(self, values: np.ndarray) -> np.ndarray:
        return self.fit(values).transform(values)


class LinearPreferenceClassifier:
    """Centroid-based binary classifier with an SGD-like public surface."""

    def __init__(self):
        self.samples: list[np.ndarray] = []
        self.labels: list[int] = []
        self.weights: np.ndarray | None = None
        self.bias = 0.0

    def fit(self, values: np.ndarray, labels: np.ndarray) -> "LinearPreferenceClassifier":
        values = np.asarray(values, dtype=np.float32)
        labels = np.asarray(labels, dtype=np.int64).reshape(-1)
        self.samples = [row.copy() for row in values]
        self.labels = [int(label) for label in labels]
        self._recompute()
        return self

    def partial_fit(
        self,
        values: np.ndarray,
        labels: np.ndarray,
        classes: np.ndarray | None = None,
    ) -> "LinearPreferenceClassifier":
        values = np.asarray(values, dtype=np.float32)
        labels = np.asarray(labels, dtype=np.int64).reshape(-1)
        for row, label in zip(values, labels):
            self.samples.append(row.copy())
            self.labels.append(int(label))
        self._recompute()
        return self

    def decision_function(self, values: np.ndarray) -> np.ndarray:
        values = np.asarray(values, dtype=np.float32)
        if self.weights is None:
            return np.zeros(values.shape[0], dtype=np.float32)
        return values @ self.weights + self.bias

    def _recompute(self) -> None:
        if not self.samples:
            self.weights = None
            self.bias = 0.0
            return
        values = np.vstack(self.samples).astype(np.float32)
        labels = np.asarray(self.labels, dtype=np.int64)
        positives = values[labels == 1]
        negatives = values[labels == 0]
        if len(positives) and len(negatives):
            positive_center = positives.mean(axis=0)
            negative_center = negatives.mean(axis=0)
            self.weights = (positive_center - negative_center).astype(np.float32)
            self.bias = -0.5 * float(np.dot(positive_center, positive_center) - np.dot(negative_center, negative_center))
            return
        polarity = 1.0 if labels[-1] == 1 else -1.0
        self.weights = (polarity * values[-1]).astype(np.float32)
        self.bias = 0.0
