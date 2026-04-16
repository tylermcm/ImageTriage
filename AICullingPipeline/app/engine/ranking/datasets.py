"""Pairwise ranking datasets and split helpers."""

from __future__ import annotations

from typing import List, Sequence, Tuple

import numpy as np
import torch
from torch.utils.data import Dataset

from app.storage.ranking_artifacts import PairwisePreferenceRecord


class PairwisePreferenceDataset(Dataset):
    """A dataset of preferred-vs-other embedding pairs."""

    def __init__(
        self,
        embeddings: np.ndarray,
        preferences: Sequence[PairwisePreferenceRecord],
        reference_features: np.ndarray | None = None,
    ) -> None:
        self.embeddings = torch.from_numpy(np.asarray(embeddings, dtype=np.float32))
        self.preferences = list(preferences)
        self.reference_features = (
            torch.from_numpy(np.asarray(reference_features, dtype=np.float32))
            if reference_features is not None
            else None
        )

    def __len__(self) -> int:
        """Return the number of labeled preference pairs."""

        return len(self.preferences)

    def __getitem__(self, index: int) -> dict[str, torch.Tensor]:
        """Return one preferred-vs-other training example."""

        pair = self.preferences[index]
        sample = {
            "preferred": self.embeddings[pair.preferred_index],
            "other": self.embeddings[pair.other_index],
        }
        if self.reference_features is not None:
            sample["preferred_reference_features"] = self.reference_features[pair.preferred_index]
            sample["other_reference_features"] = self.reference_features[pair.other_index]
        return sample


def split_pairwise_preferences(
    preferences: Sequence[PairwisePreferenceRecord],
    *,
    validation_fraction: float,
    random_seed: int,
) -> Tuple[List[PairwisePreferenceRecord], List[PairwisePreferenceRecord]]:
    """Split preferences deterministically into train and validation lists."""

    total = len(preferences)
    if total <= 1 or validation_fraction <= 0.0:
        return list(preferences), []

    indices = np.arange(total)
    rng = np.random.default_rng(random_seed)
    rng.shuffle(indices)

    validation_count = int(round(total * validation_fraction))
    validation_count = max(1, min(total - 1, validation_count))
    validation_indices = set(int(value) for value in indices[:validation_count])

    train_preferences: List[PairwisePreferenceRecord] = []
    validation_preferences: List[PairwisePreferenceRecord] = []
    for index, preference in enumerate(preferences):
        if index in validation_indices:
            validation_preferences.append(preference)
        else:
            train_preferences.append(preference)

    return train_preferences, validation_preferences
