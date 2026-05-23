"""Preference sampling helpers for ranker training."""

from __future__ import annotations

from typing import Any, Sequence


AI_DISAGREEMENT_SOURCE_MODE = "ai_disagreement"


def oversample_disagreement_preferences(preferences: Sequence[Any], *, factor: int) -> list[Any]:
    """Duplicate AI disagreement pairs inside the training split only."""

    base = list(preferences)
    multiplier = max(1, int(factor))
    if multiplier <= 1:
        return base
    disagreement_pairs = [
        preference for preference in base if getattr(preference, "source_mode", "") == AI_DISAGREEMENT_SOURCE_MODE
    ]
    if not disagreement_pairs:
        return base
    return base + disagreement_pairs * (multiplier - 1)
