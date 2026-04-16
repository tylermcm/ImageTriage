"""Non-UI controller logic for the local labeling application."""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

from app.config import LabelingConfig
from app.labeling.loaders import load_labeling_dataset
from app.labeling.models import ClusterItem, PairCandidate
from app.labeling.sampling import PairSampler
from app.labeling.storage import ClusterLabelStore, PairwiseLabelStore


class LabelingSession:
    """High-level controller that coordinates data loading, sampling, and saves."""

    def __init__(self, config: LabelingConfig) -> None:
        self.config = config
        self.dataset = load_labeling_dataset(
            config.artifacts_dir,
            metadata_filename=config.metadata_filename,
            image_ids_filename=config.image_ids_filename,
            clusters_filename=config.clusters_filename,
        )
        self.pair_store = PairwiseLabelStore(config.output_dir / config.pairwise_labels_filename)
        self.cluster_store = ClusterLabelStore(
            config.output_dir / config.cluster_labels_filename
        )
        self.pair_sampler = PairSampler(
            self.dataset,
            self.pair_store,
            random_seed=config.random_seed,
        )

    def next_pair(self, *, source_mode: str, singletons_only: bool) -> Optional[PairCandidate]:
        """Return the next pair candidate for the requested mode."""

        if source_mode == "cluster_pair":
            return self.pair_sampler.next_cluster_pair()

        return self.pair_sampler.next_arbitrary_pair(singletons_only=singletons_only)

    def save_pair_label(
        self,
        candidate: PairCandidate,
        *,
        decision: str,
    ) -> None:
        """Persist one pairwise decision."""

        preferred_image_id: Optional[str]
        if decision == "left_better":
            preferred_image_id = candidate.image_a.image_id
        elif decision == "right_better":
            preferred_image_id = candidate.image_b.image_id
        else:
            preferred_image_id = None

        self.pair_store.append(
            image_a_id=candidate.image_a.image_id,
            image_b_id=candidate.image_b.image_id,
            preferred_image_id=preferred_image_id,
            decision=decision,
            source_mode=candidate.source_mode,
            cluster_id=candidate.cluster_id,
            annotator_id=self.config.annotator_id,
        )

    def cluster_items(self) -> List[ClusterItem]:
        """Return the multi-image clusters available for cluster labeling."""

        return self.dataset.multi_image_clusters

    def cluster_label_assignments(self, cluster_id: str) -> Dict[str, str]:
        """Return the latest saved assignments for a cluster."""

        latest = self.cluster_store.get_latest(cluster_id)
        if latest is None:
            return {}

        assignments: Dict[str, str] = {}
        for image_id in latest.get("best_image_ids", []):
            assignments[str(image_id)] = "best"
        for image_id in latest.get("acceptable_image_ids", []):
            assignments[str(image_id)] = "acceptable"
        for image_id in latest.get("reject_image_ids", []):
            assignments[str(image_id)] = "reject"
        return assignments

    def save_cluster_label(
        self,
        cluster_id: str,
        assignments: Dict[str, str],
    ) -> None:
        """Persist one cluster annotation."""

        best_image_ids = sorted(
            [image_id for image_id, label in assignments.items() if label == "best"]
        )
        acceptable_image_ids = sorted(
            [image_id for image_id, label in assignments.items() if label == "acceptable"]
        )
        reject_image_ids = sorted(
            [image_id for image_id, label in assignments.items() if label == "reject"]
        )
        self.cluster_store.append(
            cluster_id=cluster_id,
            best_image_ids=best_image_ids,
            acceptable_image_ids=acceptable_image_ids,
            reject_image_ids=reject_image_ids,
            annotator_id=self.config.annotator_id,
        )

    def next_unlabeled_cluster_index(self, start_index: int = 0) -> int:
        """Return the next unlabeled cluster index, or the final index if all are labeled."""

        clusters = self.cluster_items()
        if not clusters:
            return 0

        for index in range(max(0, start_index), len(clusters)):
            if not self.cluster_store.has_cluster(clusters[index].cluster_id):
                return index

        return len(clusters) - 1

    def progress_summary(self) -> Dict[str, int]:
        """Return a summary of labeling progress for the UI."""

        labeled_cluster_pairs, total_cluster_pairs = self.pair_sampler.cluster_pair_counts()
        return {
            "total_images": len(self.dataset.ordered_images),
            "total_clusters": len(self.dataset.multi_image_clusters),
            "labeled_clusters": self.cluster_store.count(),
            "labeled_pairs": self.pair_store.count(),
            "labeled_cluster_pairs": labeled_cluster_pairs,
            "total_cluster_pairs": total_cluster_pairs,
            "singleton_images": len(self.dataset.singleton_images),
        }
