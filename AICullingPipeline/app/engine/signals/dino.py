"""DINO-derived culling signals."""

from __future__ import annotations

from dataclasses import replace
import time
from typing import Dict

import numpy as np

from app.engine.signals.layers import SignalLayerContext, append_layer_status
from app.engine.signals.models import DinoSignals, ImageSignalRecord, LayerStatus


class DinoSignalLayer:
    """Extract centrality and group context from existing DINO embeddings."""

    layer_id = "dino"
    display_name = "DINO Base Layer"
    required_stack_slot = True

    def __init__(self, *, timing_callback=None) -> None:
        self.timing_callback = timing_callback

    def status(self) -> LayerStatus:
        return LayerStatus(
            layer_id=self.layer_id,
            display_name=self.display_name,
            enabled=True,
            available=True,
            status="ready",
            backend="DINO embeddings",
        )

    def analyze(
        self,
        records: Dict[str, ImageSignalRecord],
        context: SignalLayerContext,
    ) -> Dict[str, ImageSignalRecord]:
        updated = dict(records)
        signals = build_dino_signals(context.ranking_artifacts, timing_callback=self.timing_callback)
        for image_id, dino_signals in signals.items():
            record = updated.get(image_id)
            if record is not None:
                updated[image_id] = replace(record, dino=dino_signals)
        return append_layer_status(updated, self.status())


def build_dino_signals(ranking_artifacts, *, timing_callback=None) -> Dict[str, DinoSignals]:
    """Build DINO centrality/group signals for every ranked artifact image."""

    normalized_embeddings = _l2_normalize(ranking_artifacts.embeddings)
    signals: Dict[str, DinoSignals] = {}

    for cluster_id, members in ranking_artifacts.clusters_by_id.items():
        if not members:
            continue
        cluster_started = time.perf_counter()
        indices = [member.embedding_index for member in members]
        cluster_embeddings = normalized_embeddings[indices]
        if len(members) == 1:
            centrality_scores = np.asarray([1.0], dtype=np.float32)
            neighbor_scores = np.asarray([None], dtype=object)
        else:
            centroid = _l2_normalize(cluster_embeddings.mean(axis=0, keepdims=True))
            centrality_scores = (cluster_embeddings @ centroid.T).reshape(-1).astype(np.float32)
            similarity_matrix = cluster_embeddings @ cluster_embeddings.T
            np.fill_diagonal(similarity_matrix, -1.0)
            neighbor_scores = similarity_matrix.max(axis=1).astype(np.float32)

        ordered_positions = sorted(
            range(len(members)),
            key=lambda index: (
                -float(centrality_scores[index]),
                members[index].cluster_position,
                members[index].file_name.casefold(),
                members[index].image_id,
            ),
        )
        rank_by_position = {position: rank for rank, position in enumerate(ordered_positions, start=1)}

        for position, member in enumerate(members):
            nearest_similarity = neighbor_scores[position]
            nearest_value = (
                None
                if nearest_similarity is None
                else float(nearest_similarity)
            )
            signals[member.image_id] = DinoSignals(
                cluster_id=cluster_id,
                group_size=len(members),
                group_position=member.cluster_position,
                group_rank_by_centrality=rank_by_position[position],
                centrality_score=float(centrality_scores[position]),
                nearest_neighbor_similarity=nearest_value,
                duplicate_risk=_duplicate_risk(nearest_value),
                status="analyzed",
            )
        if timing_callback is not None:
            timing_callback(
                "dino_cluster",
                time.perf_counter() - cluster_started,
                {
                    "cluster_id": cluster_id,
                    "members": len(members),
                    "similarity_comparisons": len(members) * len(members) if len(members) > 1 else 0,
                },
            )

    return signals


def _duplicate_risk(nearest_similarity: float | None) -> str:
    if nearest_similarity is None:
        return "single_image"
    if nearest_similarity >= 0.985:
        return "high"
    if nearest_similarity >= 0.955:
        return "medium"
    return "low"


def _l2_normalize(values: np.ndarray) -> np.ndarray:
    array = np.asarray(values, dtype=np.float32)
    norms = np.linalg.norm(array, axis=1, keepdims=True)
    return array / np.clip(norms, a_min=1e-12, a_max=None)
