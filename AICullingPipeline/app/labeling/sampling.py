"""Pair sampling logic for local preference labeling."""

from __future__ import annotations

from itertools import combinations
import random
from typing import Dict, List, Optional, Tuple

from app.labeling.models import ClusterItem, DatasetBundle, ImageItem, PairCandidate
from app.labeling.storage import PairwiseLabelStore


class PairSampler:
    """Sample unlabeled image pairs for pairwise preference collection."""

    def __init__(
        self,
        dataset: DatasetBundle,
        pair_store: PairwiseLabelStore,
        *,
        random_seed: int,
    ) -> None:
        self.dataset = dataset
        self.pair_store = pair_store
        self.rng = random.Random(random_seed)
        self.cluster_pair_candidates = _build_cluster_pair_candidates(
            dataset.multi_image_clusters
        )

    def next_cluster_pair(self) -> Optional[PairCandidate]:
        """Return the next unlabeled pair from multi-image clusters."""

        available_clusters = [
            (cluster_id, pairs)
            for cluster_id, pairs in self.cluster_pair_candidates.items()
            if any(not self.pair_store.has_pair(left.image_id, right.image_id) for left, right in pairs)
        ]
        if not available_clusters:
            return None

        cluster_id, pairs = self.rng.choice(available_clusters)
        remaining_pairs = [
            (left, right)
            for left, right in pairs
            if not self.pair_store.has_pair(left.image_id, right.image_id)
        ]
        left, right = self.rng.choice(remaining_pairs)
        return PairCandidate(
            image_a=left,
            image_b=right,
            source_mode="cluster_pair",
            cluster_id=cluster_id,
        )

    def next_arbitrary_pair(self, *, singletons_only: bool) -> Optional[PairCandidate]:
        """Return an unlabeled arbitrary pair from singletons or the full dataset."""

        pool = (
            self.dataset.singleton_images
            if singletons_only and len(self.dataset.singleton_images) >= 2
            else self.dataset.ordered_images
        )
        if len(pool) < 2:
            return None

        max_attempts = min(500, len(pool) * 20)
        for _ in range(max_attempts):
            left, right = self.rng.sample(pool, 2)
            if self.pair_store.has_pair(left.image_id, right.image_id):
                continue
            return PairCandidate(
                image_a=left,
                image_b=right,
                source_mode="arbitrary_pair",
                cluster_id=None,
            )

        remaining_pairs = _remaining_arbitrary_pairs(pool, self.pair_store)
        if not remaining_pairs:
            return None

        left, right = self.rng.choice(remaining_pairs)
        return PairCandidate(
            image_a=left,
            image_b=right,
            source_mode="arbitrary_pair",
            cluster_id=None,
        )

    def cluster_pair_counts(self) -> Tuple[int, int]:
        """Return labeled and total counts for cluster-based pair candidates."""

        total = 0
        remaining = 0
        for pairs in self.cluster_pair_candidates.values():
            total += len(pairs)
            remaining += sum(
                not self.pair_store.has_pair(left.image_id, right.image_id)
                for left, right in pairs
            )
        labeled = total - remaining
        return labeled, total


def _build_cluster_pair_candidates(
    clusters: List[ClusterItem],
) -> Dict[str, List[Tuple[ImageItem, ImageItem]]]:
    """Precompute all unordered pairs within multi-image clusters."""

    candidates: Dict[str, List[Tuple[ImageItem, ImageItem]]] = {}
    for cluster in clusters:
        candidates[cluster.cluster_id] = list(combinations(cluster.members, 2))
    return candidates


def _remaining_arbitrary_pairs(pool, pair_store: PairwiseLabelStore):
    """Enumerate remaining arbitrary pairs when random sampling gets saturated."""

    remaining = []
    for left, right in combinations(pool, 2):
        if pair_store.has_pair(left.image_id, right.image_id):
            continue
        remaining.append((left, right))
    return remaining
