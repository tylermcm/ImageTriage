"""Non-UI controller logic for the Speed Cull labeling application."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from app.config import LabelingConfig
from app.labeling.decision_bridge import DecisionBridge
from app.labeling.loaders import load_labeling_dataset
from app.labeling.models import ClusterItem, ImageItem
from app.labeling.storage import ClusterLabelStore


def _decision_entry_to_label(entry: Dict[str, Any]) -> str:
    """Map a DecisionStore annotation snapshot to a card-assignment label.

    Speed Cull is binary: reject takes precedence over winner. The host's
    star rating field is independent of accept/reject and is not consulted
    here — Speed Cull writes only winner/reject and never touches rating.
    """

    if entry.get("reject"):
        return "reject"
    if entry.get("winner"):
        return "accept"
    return "unlabeled"


LEGACY_LABEL_MIGRATION_MARKER = ".legacy_labels_migrated"


class LabelingSession:
    """High-level controller that coordinates data loading and decision saves."""

    def __init__(
        self,
        config: LabelingConfig,
        *,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
    ) -> None:
        self.config = config
        self.dataset = load_labeling_dataset(
            config.artifacts_dir,
            metadata_filename=config.metadata_filename,
            image_ids_filename=config.image_ids_filename,
            clusters_filename=config.clusters_filename,
            collapse_near_identical=config.collapse_near_identical_for_labeling,
            near_identical_similarity_threshold=config.near_identical_similarity_threshold,
            near_identical_outlier_deviation=config.near_identical_outlier_deviation,
            filter_unusable=config.filter_unusable_for_labeling,
            unusable_shadow_clip_threshold=config.unusable_shadow_clip_threshold,
            unusable_highlight_clip_threshold=config.unusable_highlight_clip_threshold,
            unusable_contrast_threshold=config.unusable_contrast_threshold,
            unusable_sharpness_threshold=config.unusable_sharpness_threshold,
            filter_semantic_outliers=config.filter_semantic_outliers_for_labeling,
            semantic_outlier_similarity_threshold=config.semantic_outlier_similarity_threshold,
            max_labeling_cluster_images=config.max_labeling_cluster_images,
            group_cluster_near_duplicates=config.group_cluster_near_duplicates,
            cluster_near_duplicate_hamming_threshold=config.cluster_near_duplicate_hamming_threshold,
            progress_callback=progress_callback,
        )
        # JSONL store is the offline fallback when the host DecisionStore bridge
        # is unavailable (standalone runs, tests). The host path is preferred.
        self.cluster_store = ClusterLabelStore(
            config.output_dir / config.cluster_labels_filename
        )
        self.decision_bridge = DecisionBridge()
        self._cluster_decision_status_cache: Dict[str, bool] = {}
        self._cluster_decision_statuses_loaded = False

    def cluster_items(self) -> List[ClusterItem]:
        """Return the multi-image clusters available for cull review."""

        return self.dataset.multi_image_clusters

    def cluster_member_groups(self, cluster_id: str) -> List[List["ImageItem"]]:
        """Return the per-cluster near-duplicate groupings consumed by the cull UI.

        Each inner list is a near-duplicate group; a singleton list means the
        member has no near-duplicates within its cluster. When grouping is
        disabled (or no grouping was computed), every member is returned as its
        own group so the UI can render flat or stacked uniformly.
        """

        cluster = self.dataset.clusters_by_id.get(cluster_id)
        if cluster is None:
            return []
        groups = self.dataset.cluster_near_duplicate_groups.get(cluster_id)
        if groups:
            return [list(group) for group in groups]
        return [[member] for member in cluster.members]

    def cluster_label_assignments(self, cluster_id: str) -> Dict[str, str]:
        """Return the current per-image assignments for a cluster.

        Reads from the host DecisionStore when the bridge is available, falling
        back to the legacy cluster_store for standalone runs and tests.
        DecisionStore state is mapped: winner→best, reject→reject, rating>0→acceptable.
        """

        cluster = self.dataset.clusters_by_id.get(cluster_id)
        if cluster is None or not cluster.members:
            return self._cluster_store_assignments(cluster_id)

        if not self.decision_bridge.available:
            return self._cluster_store_assignments(cluster_id)

        path_to_image_id = {str(member.file_path): member.image_id for member in cluster.members}
        decisions = self.decision_bridge.load_decisions(
            [Path(p) for p in path_to_image_id]
        )
        assignments: Dict[str, str] = {}
        for path_str, image_id in path_to_image_id.items():
            entry = decisions.get(path_str)
            if entry is None:
                continue
            assignments[image_id] = _decision_entry_to_label(entry)
        return assignments

    def _cluster_store_assignments(self, cluster_id: str) -> Dict[str, str]:
        """Legacy assignment lookup via the JSONL cluster store."""

        latest = self.cluster_store.get_latest(cluster_id)
        if latest is None:
            return {}

        assignments: Dict[str, str] = {}
        for image_id in latest.get("best_image_ids", []):
            assignments[str(image_id)] = "accept"
        for image_id in latest.get("acceptable_image_ids", []):
            # Legacy "acceptable" entries map to accept under the new binary scheme.
            assignments[str(image_id)] = "accept"
        for image_id in latest.get("reject_image_ids", []):
            assignments[str(image_id)] = "reject"
        return assignments

    def save_card_decisions(
        self,
        cluster_id: str,
        card_decisions: List[Tuple[List[ImageItem], str]],
    ) -> int:
        """Persist speed-cull decisions with stack semantics.

        For each (members, decision) tuple:
          * "accept" → winner=True on the top (first) member only; others unchanged.
          * "reject" → reject=True on ALL members of the stack.
          * "unlabeled" → no-op (preserves any prior state).

        Returns the number of per-image writes performed.
        """

        if self.decision_bridge.available:
            writes: List[Dict[str, Any]] = []
            for members, decision in card_decisions:
                if not members:
                    continue
                if decision == "accept":
                    writes.append({"path": str(members[0].file_path), "winner": True})
                elif decision == "reject":
                    for member in members:
                        writes.append({"path": str(member.file_path), "reject": True})
            written = self.decision_bridge.save_decisions(writes)
            if written:
                self._cluster_decision_status_cache[cluster_id] = True
            return written

        # Fallback path: mirror the per-image assignments to the legacy JSONL store.
        assignments: Dict[str, str] = {}
        for members, decision in card_decisions:
            if not members or decision == "unlabeled":
                continue
            if decision == "reject":
                for member in members:
                    assignments[member.image_id] = "reject"
            elif decision == "accept":
                assignments[members[0].image_id] = "accept"
        if not assignments:
            return 0
        self._legacy_save_assignments(cluster_id, assignments)
        self._cluster_decision_status_cache[cluster_id] = True
        return len(assignments)

    def clear_cluster_decisions(
        self,
        cluster_id: str,
    ) -> int:
        """Clear DecisionStore entries for every member of the given cluster."""

        cluster = self.dataset.clusters_by_id.get(cluster_id)
        if cluster is None or not cluster.members:
            return 0
        if not self.decision_bridge.available:
            return 0
        writes = [
            {"path": str(member.file_path)}  # empty annotation → DecisionStore deletes
            for member in cluster.members
        ]
        written = self.decision_bridge.save_decisions(writes)
        if written:
            self._cluster_decision_status_cache[cluster_id] = False
        return written

    def _legacy_save_assignments(
        self,
        cluster_id: str,
        assignments: Dict[str, str],
    ) -> None:
        """Persist assignments to the legacy JSONL store (offline fallback only).

        The JSONL schema retains best/acceptable/reject buckets for backward
        compatibility; Speed Cull writes Accept into best_image_ids and never
        emits acceptable rows.
        """

        accept_image_ids = sorted(
            [image_id for image_id, label in assignments.items() if label == "accept"]
        )
        reject_image_ids = sorted(
            [image_id for image_id, label in assignments.items() if label == "reject"]
        )
        self.cluster_store.append(
            cluster_id=cluster_id,
            best_image_ids=accept_image_ids,
            acceptable_image_ids=[],
            reject_image_ids=reject_image_ids,
            annotator_id=self.config.annotator_id,
        )

    def delete_all_labels(self) -> Dict[str, int]:
        """Delete all saved labels for this labeling workspace and reset state."""

        deleted = {"clusters": self.cluster_store.count()}
        self._cluster_decision_status_cache.clear()
        self._cluster_decision_statuses_loaded = False
        self.cluster_store.clear()
        try:
            (self.config.output_dir / LEGACY_LABEL_MIGRATION_MARKER).write_text(
                "legacy migration intentionally suppressed after label deletion\n",
                encoding="utf-8",
            )
        except OSError:
            pass
        return deleted

    def next_unlabeled_cluster_index(self, start_index: int = 0) -> int:
        """Return the next unlabeled cluster index, or the final index if all are labeled."""

        clusters = self.cluster_items()
        if not clusters:
            return 0

        for index in range(max(0, start_index), len(clusters)):
            if not self.cluster_has_any_decision(clusters[index].cluster_id):
                return index

        return len(clusters) - 1

    def cluster_has_any_decision(self, cluster_id: str) -> bool:
        """Return True when the cluster has any saved speed-cull decision on a member."""

        cached = self._cluster_decision_status_cache.get(cluster_id)
        if cached is not None:
            return cached

        if self.decision_bridge.available:
            cluster = self.dataset.clusters_by_id.get(cluster_id)
            if cluster is None or not cluster.members:
                self._cluster_decision_status_cache[cluster_id] = False
                return False
            decisions = self.decision_bridge.load_decisions(
                [member.file_path for member in cluster.members]
            )
            for entry in decisions.values():
                if entry.get("winner") or entry.get("reject") or int(entry.get("rating") or 0) > 0:
                    self._cluster_decision_status_cache[cluster_id] = True
                    return True
            self._cluster_decision_status_cache[cluster_id] = False
            return False
        result = self.cluster_store.has_cluster(cluster_id)
        self._cluster_decision_status_cache[cluster_id] = result
        return result

    def cluster_decision_statuses(self) -> Dict[str, bool]:
        """Return cached label status for every cluster with one batched host read."""

        clusters = self.cluster_items()
        if self._cluster_decision_statuses_loaded and len(self._cluster_decision_status_cache) >= len(clusters):
            return dict(self._cluster_decision_status_cache)

        if not self.decision_bridge.available:
            statuses = {cluster.cluster_id: self.cluster_store.has_cluster(cluster.cluster_id) for cluster in clusters}
            self._cluster_decision_status_cache.update(statuses)
            self._cluster_decision_statuses_loaded = True
            return statuses

        path_to_cluster_id: Dict[str, str] = {}
        for cluster in clusters:
            for member in cluster.members:
                path_to_cluster_id[str(member.file_path)] = cluster.cluster_id

        decisions = self.decision_bridge.load_decisions([Path(path) for path in path_to_cluster_id])
        statuses = {cluster.cluster_id: False for cluster in clusters}
        for path_str, entry in decisions.items():
            cluster_id = path_to_cluster_id.get(path_str)
            if cluster_id is None:
                continue
            if entry.get("winner") or entry.get("reject") or int(entry.get("rating") or 0) > 0:
                statuses[cluster_id] = True

        self._cluster_decision_status_cache.update(statuses)
        self._cluster_decision_statuses_loaded = True
        return statuses

    def labeled_cluster_count(self) -> int:
        """Count clusters that have at least one saved decision (under current backend)."""

        return sum(1 for labeled in self.cluster_decision_statuses().values() if labeled)

    def progress_summary(self) -> Dict[str, int]:
        """Return a summary of cull progress for the UI."""

        return {
            "total_images": len(self.dataset.ordered_images),
            "total_clusters": len(self.dataset.multi_image_clusters),
            "labeled_clusters": self.labeled_cluster_count(),
        }
