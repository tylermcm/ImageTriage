"""Convert human training labels into combiner feature rows."""

from __future__ import annotations

from typing import Any, Dict, Iterable, Mapping

from app.engine.signals.combiner import pairwise_feature_delta
from app.engine.signals.models import ImageSignalRecord
from app.storage.ranking_artifacts import PairwisePreferenceRecord


def build_preference_feature_rows(
    records: Mapping[str, ImageSignalRecord],
    preferences: Iterable[PairwisePreferenceRecord],
) -> list[Dict[str, Any]]:
    """Convert pairwise/cluster-derived labels into feature-delta training rows."""

    rows: list[Dict[str, Any]] = []
    for preference in preferences:
        preferred = records.get(preference.preferred_image_id)
        other = records.get(preference.other_image_id)
        if preferred is None or other is None:
            continue
        row: Dict[str, Any] = {
            "preferred_image_id": preference.preferred_image_id,
            "other_image_id": preference.other_image_id,
            "cluster_id": preference.cluster_id or "",
            "source_mode": preference.source_mode,
            "label_origin": preference.label_origin,
            "target": 1,
        }
        row.update(pairwise_feature_delta(preferred, other))
        rows.append(row)
    return rows
