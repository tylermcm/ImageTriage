"""Culling-oriented clustering algorithms for saved image embeddings."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
from sklearn.cluster import DBSCAN

from app.clustering.akaze import AkazeMatchResult, match_akaze_features
from app.clustering.artifacts import EmbeddedImageRecord
from app.clustering.hashing import hamming_distance
from app.clustering.windowing import (
    CandidateWindow,
    build_candidate_windows,
    describe_time_window,
    pair_allowed,
)


@dataclass
class ClusterGroup:
    """A culling-relevant cluster with traceable grouping metadata."""

    member_indices: List[int]
    time_window_id: str
    window_kind: str
    cluster_reason: str
    window_time_span: str
    cluster_time_span_seconds: Optional[float]
    min_link_similarity: Optional[float]
    max_link_similarity: Optional[float]
    max_link_time_gap_seconds: Optional[float]
    max_link_hash_distance: Optional[int]
    max_link_akaze_good_matches: Optional[int]
    max_link_akaze_inlier_count: Optional[int]
    min_link_akaze_inlier_ratio: Optional[float]
    max_link_akaze_inlier_ratio: Optional[float]
    link_metrics: List[Dict[str, Optional[float]]]


@dataclass
class _SecondPassClusterState:
    """Mutable internal state used by the cross-gap relink pass."""

    member_indices: List[int]
    time_window_ids: List[str]
    window_kinds: List[str]
    link_metrics: List[Dict[str, Optional[float]]]
    representative_gate_applied: bool
    cluster_relink_applied: bool = False


def normalize_embeddings(embeddings: np.ndarray) -> np.ndarray:
    """L2-normalize embeddings row-wise for cosine similarity computations."""

    if embeddings.ndim != 2:
        raise ValueError(
            f"Expected embeddings with shape [N, D], received {tuple(embeddings.shape)}."
        )

    norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
    norms = np.clip(norms, a_min=1e-12, a_max=None)
    return embeddings / norms


def compute_cosine_similarity_matrix(normalized_embeddings: np.ndarray) -> np.ndarray:
    """Compute a dense cosine similarity matrix from normalized embeddings."""

    similarity = normalized_embeddings @ normalized_embeddings.T
    return np.clip(similarity, -1.0, 1.0)


def cluster_embeddings(
    normalized_embeddings: np.ndarray,
    records: Sequence[EmbeddedImageRecord],
    *,
    method: str,
    similarity_threshold: float,
    dbscan_eps: float,
    dbscan_min_samples: int,
    minimum_cluster_size: int,
    max_time_gap_seconds: float,
    time_filter_required: bool,
    timestamp_fallback_mode: str,
    filename_order_window: int,
    use_perceptual_hash_filter: bool,
    max_hash_distance: Optional[int],
    representative_gate_enabled: bool,
    representative_gate_min_cluster_size: int,
    representative_min_matches: int,
    use_akaze_verifier: bool,
    akaze_ratio_test_threshold: float,
    akaze_min_good_matches: int,
    akaze_min_inliers: int,
    akaze_min_inlier_ratio: float,
    cluster_relink_enabled: bool,
    cluster_relink_similarity_threshold: float,
    cluster_relink_centroid_threshold: float,
    cluster_relink_max_sequence_gap: int,
    cluster_relink_min_matches: int,
) -> Tuple[List[ClusterGroup], List[CandidateWindow]]:
    """Cluster normalized embeddings into culling-relevant groups."""

    if normalized_embeddings.shape[0] == 0:
        return [], []

    windows = build_candidate_windows(
        records,
        max_time_gap_seconds=max_time_gap_seconds,
        time_filter_required=time_filter_required,
        timestamp_fallback_mode=timestamp_fallback_mode,
        filename_order_window=filename_order_window,
    )

    groups: List[ClusterGroup] = []
    for window in windows:
        groups.extend(
            _cluster_window(
                normalized_embeddings,
                records,
                window,
                method=method,
                similarity_threshold=similarity_threshold,
                dbscan_eps=dbscan_eps,
                dbscan_min_samples=dbscan_min_samples,
                minimum_cluster_size=minimum_cluster_size,
                max_time_gap_seconds=max_time_gap_seconds,
                time_filter_required=time_filter_required,
                timestamp_fallback_mode=timestamp_fallback_mode,
                filename_order_window=filename_order_window,
                use_perceptual_hash_filter=use_perceptual_hash_filter,
                max_hash_distance=max_hash_distance,
                representative_gate_enabled=representative_gate_enabled,
                representative_gate_min_cluster_size=representative_gate_min_cluster_size,
                representative_min_matches=representative_min_matches,
                use_akaze_verifier=use_akaze_verifier,
                akaze_ratio_test_threshold=akaze_ratio_test_threshold,
                akaze_min_good_matches=akaze_min_good_matches,
                akaze_min_inliers=akaze_min_inliers,
                akaze_min_inlier_ratio=akaze_min_inlier_ratio,
            )
        )

    if cluster_relink_enabled:
        groups = _relink_cluster_groups(
            groups,
            normalized_embeddings,
            records,
            use_perceptual_hash_filter=use_perceptual_hash_filter,
            max_hash_distance=max_hash_distance,
            cluster_relink_similarity_threshold=cluster_relink_similarity_threshold,
            cluster_relink_centroid_threshold=cluster_relink_centroid_threshold,
            cluster_relink_max_sequence_gap=cluster_relink_max_sequence_gap,
            cluster_relink_min_matches=cluster_relink_min_matches,
        )

    return sorted(groups, key=lambda group: min(group.member_indices)), windows


def _cluster_window(
    normalized_embeddings: np.ndarray,
    records: Sequence[EmbeddedImageRecord],
    window: CandidateWindow,
    *,
    method: str,
    similarity_threshold: float,
    dbscan_eps: float,
    dbscan_min_samples: int,
    minimum_cluster_size: int,
    max_time_gap_seconds: float,
    time_filter_required: bool,
    timestamp_fallback_mode: str,
    filename_order_window: int,
    use_perceptual_hash_filter: bool,
    max_hash_distance: Optional[int],
    representative_gate_enabled: bool,
    representative_gate_min_cluster_size: int,
    representative_min_matches: int,
    use_akaze_verifier: bool,
    akaze_ratio_test_threshold: float,
    akaze_min_good_matches: int,
    akaze_min_inliers: int,
    akaze_min_inlier_ratio: float,
) -> List[ClusterGroup]:
    """Cluster a single candidate window."""

    local_member_indices = list(window.member_indices)
    if len(local_member_indices) == 1:
        return [
            _build_cluster_group(
                records,
                window,
                local_member_indices,
                link_metrics=[],
                reason_override=_singleton_reason(window.window_kind),
            )
        ]

    local_embeddings = normalized_embeddings[local_member_indices]
    similarity = compute_cosine_similarity_matrix(local_embeddings)
    pair_matrix, metric_map = _build_pair_matrix_and_metrics(
        records,
        local_member_indices,
        similarity,
        similarity_threshold=similarity_threshold,
        max_time_gap_seconds=max_time_gap_seconds,
        time_filter_required=time_filter_required,
        timestamp_fallback_mode=timestamp_fallback_mode,
        filename_order_window=filename_order_window,
        use_perceptual_hash_filter=use_perceptual_hash_filter,
        max_hash_distance=max_hash_distance,
        use_akaze_verifier=use_akaze_verifier,
        akaze_ratio_test_threshold=akaze_ratio_test_threshold,
        akaze_min_good_matches=akaze_min_good_matches,
        akaze_min_inliers=akaze_min_inliers,
        akaze_min_inlier_ratio=akaze_min_inlier_ratio,
    )

    if method == "graph":
        local_clusters = _graph_sequence_clusters(
            pair_matrix,
            records,
            local_member_indices,
            representative_gate_enabled=representative_gate_enabled,
            representative_gate_min_cluster_size=representative_gate_min_cluster_size,
            representative_min_matches=representative_min_matches,
        )
    elif method == "dbscan":
        local_clusters = _dbscan_clusters(similarity, pair_matrix, dbscan_eps, dbscan_min_samples)
    else:
        raise ValueError(f"Unsupported clustering method: {method}")

    local_clusters = _apply_minimum_cluster_size(local_clusters, minimum_cluster_size)

    groups: List[ClusterGroup] = []
    for cluster in local_clusters:
        global_indices = [local_member_indices[index] for index in cluster]
        link_metrics = _collect_link_metrics(cluster, metric_map)
        groups.append(
            _build_cluster_group(
                records,
                window,
                global_indices,
                link_metrics=link_metrics,
                reason_override=None,
                use_perceptual_hash_filter=use_perceptual_hash_filter,
                use_akaze_verifier=use_akaze_verifier,
                representative_gate_applied=(
                    method == "graph"
                    and representative_gate_enabled
                    and len(cluster) > representative_gate_min_cluster_size
                ),
            )
        )

    return groups


def _build_pair_matrix_and_metrics(
    records: Sequence[EmbeddedImageRecord],
    local_member_indices: Sequence[int],
    similarity: np.ndarray,
    *,
    similarity_threshold: float,
    max_time_gap_seconds: float,
    time_filter_required: bool,
    timestamp_fallback_mode: str,
    filename_order_window: int,
    use_perceptual_hash_filter: bool,
    max_hash_distance: Optional[int],
    use_akaze_verifier: bool,
    akaze_ratio_test_threshold: float,
    akaze_min_good_matches: int,
    akaze_min_inliers: int,
    akaze_min_inlier_ratio: float,
) -> Tuple[np.ndarray, Dict[Tuple[int, int], Dict[str, Optional[float]]]]:
    """Build the allowed-pair matrix and record why links were accepted."""

    num_items = len(local_member_indices)
    pair_matrix = np.eye(num_items, dtype=bool)
    metric_map: Dict[Tuple[int, int], Dict[str, Optional[float]]] = {}

    for left_local in range(num_items):
        left_record = records[local_member_indices[left_local]]
        for right_local in range(left_local + 1, num_items):
            right_record = records[local_member_indices[right_local]]

            pair_similarity = float(similarity[left_local, right_local])
            if pair_similarity < similarity_threshold:
                continue

            akaze_match: Optional[AkazeMatchResult] = None
            if use_akaze_verifier:
                akaze_match = match_akaze_features(
                    left_record.akaze_features,
                    right_record.akaze_features,
                    ratio_test_threshold=akaze_ratio_test_threshold,
                    min_good_matches=akaze_min_good_matches,
                    min_inliers=akaze_min_inliers,
                    min_inlier_ratio=akaze_min_inlier_ratio,
                )
                if akaze_match is None or not akaze_match.passed:
                    continue

            if not pair_allowed(
                left_record,
                right_record,
                max_time_gap_seconds=max_time_gap_seconds,
                time_filter_required=time_filter_required,
                timestamp_fallback_mode=timestamp_fallback_mode,
                filename_order_window=filename_order_window,
            ):
                continue

            hash_distance: Optional[int] = None
            if use_perceptual_hash_filter:
                hash_distance = hamming_distance(
                    left_record.perceptual_hash,
                    right_record.perceptual_hash,
                )
                if hash_distance is None:
                    continue
                if max_hash_distance is not None and hash_distance > max_hash_distance:
                    continue

            time_gap_seconds = _compute_time_gap_seconds(left_record, right_record)
            pair_matrix[left_local, right_local] = True
            pair_matrix[right_local, left_local] = True
            metric_map[(left_local, right_local)] = {
                "similarity": pair_similarity,
                "time_gap_seconds": time_gap_seconds,
                "hash_distance": float(hash_distance) if hash_distance is not None else None,
                "akaze_good_matches": float(akaze_match.good_match_count)
                if akaze_match is not None
                else None,
                "akaze_inlier_count": float(akaze_match.inlier_count)
                if akaze_match is not None
                else None,
                "akaze_inlier_ratio": float(akaze_match.inlier_ratio)
                if akaze_match is not None
                else None,
            }

    return pair_matrix, metric_map


def _graph_sequence_clusters(
    adjacency: np.ndarray,
    records: Sequence[EmbeddedImageRecord],
    local_member_indices: Sequence[int],
    *,
    representative_gate_enabled: bool,
    representative_gate_min_cluster_size: int,
    representative_min_matches: int,
) -> List[List[int]]:
    """Build deterministic sequence-aware clusters with representative checks."""

    ordered_local_indices = _ordered_local_indices(records, local_member_indices)
    clusters: List[List[int]] = []

    for candidate_local in ordered_local_indices:
        assigned = False
        for cluster in reversed(clusters):
            if _can_join_sequence_cluster(
                candidate_local,
                cluster,
                adjacency,
                representative_gate_enabled=representative_gate_enabled,
                representative_gate_min_cluster_size=representative_gate_min_cluster_size,
                representative_min_matches=representative_min_matches,
            ):
                cluster.append(candidate_local)
                assigned = True
                break

        if not assigned:
            clusters.append([candidate_local])

    return sorted(
        (sorted(cluster) for cluster in clusters),
        key=lambda members: members[0],
    )


def _ordered_local_indices(
    records: Sequence[EmbeddedImageRecord],
    local_member_indices: Sequence[int],
) -> List[int]:
    """Return local indices sorted in a stable culling-friendly sequence order."""

    enumerated = list(enumerate(local_member_indices))
    ordered = sorted(
        enumerated,
        key=lambda item: _record_sort_key(records[item[1]]),
    )
    return [local_index for local_index, _ in ordered]


def _record_sort_key(record: EmbeddedImageRecord) -> Tuple[int, str, int, str, str]:
    """Return a deterministic ordering key for sequential clustering."""

    if record.capture_datetime is not None:
        return (
            0,
            record.capture_datetime.isoformat(),
            record.filename_sequence_number
            if record.filename_sequence_number is not None
            else record.embedding_index,
            record.file_name.casefold(),
            record.relative_path.casefold(),
        )

    return (
        1,
        "",
        record.filename_sequence_number
        if record.filename_sequence_number is not None
        else record.embedding_index,
        record.file_name.casefold(),
        record.relative_path.casefold(),
    )


def _can_join_sequence_cluster(
    candidate_local: int,
    cluster: Sequence[int],
    adjacency: np.ndarray,
    *,
    representative_gate_enabled: bool,
    representative_gate_min_cluster_size: int,
    representative_min_matches: int,
) -> bool:
    """Decide whether a candidate should join an existing sequence cluster."""

    if not any(bool(adjacency[candidate_local, member]) for member in cluster):
        return False

    if (
        not representative_gate_enabled
        or len(cluster) < representative_gate_min_cluster_size
    ):
        return True

    representative_members = _representative_members(cluster)
    required_matches = min(representative_min_matches, len(representative_members))
    if required_matches <= 0:
        return True

    representative_matches = sum(
        1 for member in representative_members if bool(adjacency[candidate_local, member])
    )
    return representative_matches >= required_matches


def _representative_members(cluster: Sequence[int]) -> List[int]:
    """Pick stable early/middle/late representatives for anti-chaining checks."""

    ordered_members = list(cluster)
    if not ordered_members:
        return []

    candidate_positions = {
        0,
        len(ordered_members) // 2,
        len(ordered_members) - 1,
    }
    return [ordered_members[position] for position in sorted(candidate_positions)]


def _dbscan_clusters(
    similarity: np.ndarray,
    allowed_pairs: np.ndarray,
    eps: float,
    min_samples: int,
) -> List[List[int]]:
    """Build clusters with DBSCAN using precomputed cosine distance within allowed pairs."""

    distance = 1.0 - similarity
    distance = np.clip(distance, 0.0, 2.0)
    distance[~allowed_pairs] = 2.0
    np.fill_diagonal(distance, 0.0)

    labels = DBSCAN(eps=eps, min_samples=min_samples, metric="precomputed").fit_predict(
        distance
    )

    label_to_members: Dict[int, List[int]] = {}
    singleton_members: List[List[int]] = []

    for index, label in enumerate(labels.tolist()):
        if label == -1:
            singleton_members.append([index])
            continue

        label_to_members.setdefault(int(label), []).append(index)

    grouped_clusters = [
        sorted(label_to_members[label]) for label in sorted(label_to_members.keys())
    ]
    grouped_clusters.extend(singleton_members)
    return sorted(grouped_clusters, key=lambda members: members[0])


def _relink_cluster_groups(
    groups: List[ClusterGroup],
    normalized_embeddings: np.ndarray,
    records: Sequence[EmbeddedImageRecord],
    *,
    use_perceptual_hash_filter: bool,
    max_hash_distance: Optional[int],
    cluster_relink_similarity_threshold: float,
    cluster_relink_centroid_threshold: float,
    cluster_relink_max_sequence_gap: int,
    cluster_relink_min_matches: int,
) -> List[ClusterGroup]:
    """Run a second pass that can reconnect split clusters across a short gap."""

    states = [
        _SecondPassClusterState(
            member_indices=_sorted_member_indices(records, group.member_indices),
            time_window_ids=[group.time_window_id],
            window_kinds=[group.window_kind],
            link_metrics=list(group.link_metrics),
            representative_gate_applied="representative_gate" in group.cluster_reason,
        )
        for group in sorted(groups, key=lambda item: _cluster_sort_key(records, item.member_indices))
    ]

    merged_states: List[_SecondPassClusterState] = []
    for candidate_state in states:
        best_state_index: Optional[int] = None
        best_match_score = -1.0
        best_relink_metrics: Optional[List[Dict[str, Optional[float]]]] = None

        for state_index in range(len(merged_states) - 1, -1, -1):
            existing_state = merged_states[state_index]
            sequence_gap = _cluster_sequence_gap(records, existing_state, candidate_state)
            if sequence_gap > cluster_relink_max_sequence_gap:
                break

            relink_metrics = _collect_cluster_relink_metrics(
                normalized_embeddings,
                records,
                existing_state,
                candidate_state,
                similarity_threshold=cluster_relink_similarity_threshold,
                centroid_threshold=cluster_relink_centroid_threshold,
                minimum_matches=cluster_relink_min_matches,
                use_perceptual_hash_filter=use_perceptual_hash_filter,
                max_hash_distance=max_hash_distance,
            )
            if not relink_metrics:
                continue

            match_score = max(
                float(metric["similarity"])
                for metric in relink_metrics
                if metric.get("similarity") is not None
            )
            if match_score > best_match_score:
                best_state_index = state_index
                best_match_score = match_score
                best_relink_metrics = relink_metrics

        if best_state_index is None or best_relink_metrics is None:
            merged_states.append(candidate_state)
            continue

        merged_states[best_state_index] = _merge_second_pass_states(
            merged_states[best_state_index],
            candidate_state,
            best_relink_metrics,
            records,
        )

    return [
        _build_second_pass_cluster_group(
            records,
            state,
            use_perceptual_hash_filter=use_perceptual_hash_filter,
        )
        for state in merged_states
    ]


def _collect_cluster_relink_metrics(
    normalized_embeddings: np.ndarray,
    records: Sequence[EmbeddedImageRecord],
    left_state: _SecondPassClusterState,
    right_state: _SecondPassClusterState,
    *,
    similarity_threshold: float,
    centroid_threshold: float,
    minimum_matches: int,
    use_perceptual_hash_filter: bool,
    max_hash_distance: Optional[int],
) -> Optional[List[Dict[str, Optional[float]]]]:
    """Collect strong tail/head links that justify reconnecting two clusters."""

    centroid_similarity = _cluster_centroid_similarity(
        normalized_embeddings,
        left_state.member_indices,
        right_state.member_indices,
    )
    if centroid_similarity < centroid_threshold:
        return None

    left_tail = _cluster_edge_members(records, left_state.member_indices, count=3, side="tail")
    right_head = _cluster_edge_members(records, right_state.member_indices, count=3, side="head")
    if not left_tail or not right_head:
        return None

    relink_metrics: List[Dict[str, Optional[float]]] = []
    for left_index in left_tail:
        for right_index in right_head:
            pair_similarity = float(normalized_embeddings[left_index] @ normalized_embeddings[right_index])
            if pair_similarity < similarity_threshold:
                continue

            hash_distance: Optional[int] = None
            if use_perceptual_hash_filter:
                hash_distance = hamming_distance(
                    records[left_index].perceptual_hash,
                    records[right_index].perceptual_hash,
                )
                if hash_distance is None:
                    continue
                if max_hash_distance is not None and hash_distance > max_hash_distance:
                    continue

            relink_metrics.append(
                {
                    "similarity": pair_similarity,
                    "time_gap_seconds": _compute_time_gap_seconds(
                        records[left_index],
                        records[right_index],
                    ),
                    "hash_distance": float(hash_distance)
                    if hash_distance is not None
                    else None,
                }
            )

    possible_matches = len(left_tail) * len(right_head)
    required_matches = min(minimum_matches, possible_matches)
    if len(relink_metrics) < required_matches:
        return None

    return relink_metrics


def _cluster_centroid_similarity(
    normalized_embeddings: np.ndarray,
    left_member_indices: Sequence[int],
    right_member_indices: Sequence[int],
) -> float:
    """Compute cosine similarity between two cluster centroids."""

    left_centroid = _cluster_centroid(normalized_embeddings, left_member_indices)
    right_centroid = _cluster_centroid(normalized_embeddings, right_member_indices)
    return float(np.clip(left_centroid @ right_centroid, -1.0, 1.0))


def _cluster_centroid(
    normalized_embeddings: np.ndarray,
    member_indices: Sequence[int],
) -> np.ndarray:
    """Compute a normalized centroid for one cluster."""

    centroid = np.mean(normalized_embeddings[list(member_indices)], axis=0)
    norm = np.linalg.norm(centroid)
    if norm <= 1e-12:
        return centroid.astype(np.float32, copy=False)
    return (centroid / norm).astype(np.float32, copy=False)


def _cluster_edge_members(
    records: Sequence[EmbeddedImageRecord],
    member_indices: Sequence[int],
    *,
    count: int,
    side: str,
) -> List[int]:
    """Return the first or last few members in stable sequence order."""

    ordered_members = _sorted_member_indices(records, member_indices)
    if side == "head":
        return ordered_members[:count]
    return ordered_members[-count:]


def _sorted_member_indices(
    records: Sequence[EmbeddedImageRecord],
    member_indices: Sequence[int],
) -> List[int]:
    """Sort member indices in stable record order."""

    return sorted(member_indices, key=lambda index: _record_sort_key(records[index]))


def _cluster_sort_key(
    records: Sequence[EmbeddedImageRecord],
    member_indices: Sequence[int],
) -> Tuple[int, str, int, str, str]:
    """Sort clusters by their earliest member in stable sequence order."""

    first_member = _sorted_member_indices(records, member_indices)[0]
    return _record_sort_key(records[first_member])


def _cluster_sequence_gap(
    records: Sequence[EmbeddedImageRecord],
    left_state: _SecondPassClusterState,
    right_state: _SecondPassClusterState,
) -> int:
    """Estimate the sequence gap between two clusters using filename order when possible."""

    left_tail = _cluster_edge_members(records, left_state.member_indices, count=1, side="tail")[0]
    right_head = _cluster_edge_members(records, right_state.member_indices, count=1, side="head")[0]
    return abs(
        _record_sequence_position(records[right_head]) - _record_sequence_position(records[left_tail])
    )


def _record_sequence_position(record: EmbeddedImageRecord) -> int:
    """Return the best available monotonic sequence position for one record."""

    if record.filename_sequence_number is not None:
        return record.filename_sequence_number
    return record.embedding_index


def _merge_second_pass_states(
    left_state: _SecondPassClusterState,
    right_state: _SecondPassClusterState,
    relink_metrics: List[Dict[str, Optional[float]]],
    records: Sequence[EmbeddedImageRecord],
) -> _SecondPassClusterState:
    """Merge two second-pass states into one combined cluster state."""

    merged_member_indices = _sorted_member_indices(
        records,
        [*left_state.member_indices, *right_state.member_indices],
    )
    merged_time_window_ids = list(dict.fromkeys([*left_state.time_window_ids, *right_state.time_window_ids]))
    merged_window_kinds = list(dict.fromkeys([*left_state.window_kinds, *right_state.window_kinds]))
    return _SecondPassClusterState(
        member_indices=merged_member_indices,
        time_window_ids=merged_time_window_ids,
        window_kinds=merged_window_kinds,
        link_metrics=[*left_state.link_metrics, *right_state.link_metrics, *relink_metrics],
        representative_gate_applied=(
            left_state.representative_gate_applied
            or right_state.representative_gate_applied
        ),
        cluster_relink_applied=True,
    )


def _build_second_pass_cluster_group(
    records: Sequence[EmbeddedImageRecord],
    state: _SecondPassClusterState,
    *,
    use_perceptual_hash_filter: bool,
    use_akaze_verifier: bool = False,
) -> ClusterGroup:
    """Create a final cluster group from a second-pass merge state."""

    member_indices = _sorted_member_indices(records, state.member_indices)
    time_window_span, _ = describe_time_window(records, member_indices)
    _, cluster_time_span_seconds = describe_time_window(records, member_indices)
    similarity_values = [
        float(metric["similarity"])
        for metric in state.link_metrics
        if metric.get("similarity") is not None
    ]
    time_gap_values = [
        float(metric["time_gap_seconds"])
        for metric in state.link_metrics
        if metric.get("time_gap_seconds") is not None
    ]
    hash_distance_values = [
        int(metric["hash_distance"])
        for metric in state.link_metrics
        if metric.get("hash_distance") is not None
    ]
    akaze_good_match_values = [
        int(metric["akaze_good_matches"])
        for metric in state.link_metrics
        if metric.get("akaze_good_matches") is not None
    ]
    akaze_inlier_count_values = [
        int(metric["akaze_inlier_count"])
        for metric in state.link_metrics
        if metric.get("akaze_inlier_count") is not None
    ]
    akaze_inlier_ratio_values = [
        float(metric["akaze_inlier_ratio"])
        for metric in state.link_metrics
        if metric.get("akaze_inlier_ratio") is not None
    ]

    window_kind = _merged_window_kind(state.window_kinds, state.cluster_relink_applied)
    return ClusterGroup(
        member_indices=member_indices,
        time_window_id=_merged_time_window_id(state.time_window_ids),
        window_kind=window_kind,
        cluster_reason=_compose_cluster_reason(
            window_kind,
            use_perceptual_hash_filter=use_perceptual_hash_filter,
            use_akaze_verifier=use_akaze_verifier,
            representative_gate_applied=state.representative_gate_applied,
            cluster_relink_applied=state.cluster_relink_applied,
        ),
        window_time_span=time_window_span,
        cluster_time_span_seconds=cluster_time_span_seconds,
        min_link_similarity=min(similarity_values) if similarity_values else None,
        max_link_similarity=max(similarity_values) if similarity_values else None,
        max_link_time_gap_seconds=max(time_gap_values) if time_gap_values else None,
        max_link_hash_distance=max(hash_distance_values) if hash_distance_values else None,
        max_link_akaze_good_matches=max(akaze_good_match_values)
        if akaze_good_match_values
        else None,
        max_link_akaze_inlier_count=max(akaze_inlier_count_values)
        if akaze_inlier_count_values
        else None,
        min_link_akaze_inlier_ratio=min(akaze_inlier_ratio_values)
        if akaze_inlier_ratio_values
        else None,
        max_link_akaze_inlier_ratio=max(akaze_inlier_ratio_values)
        if akaze_inlier_ratio_values
        else None,
        link_metrics=list(state.link_metrics),
    )


def _merged_time_window_id(time_window_ids: Sequence[str]) -> str:
    """Create a stable summary identifier for merged window membership."""

    unique_ids = list(dict.fromkeys(time_window_ids))
    if len(unique_ids) == 1:
        return unique_ids[0]
    if len(unique_ids) == 2:
        return "+".join(unique_ids)
    return f"{unique_ids[0]}+...+{unique_ids[-1]}"


def _merged_window_kind(
    window_kinds: Sequence[str],
    cluster_relink_applied: bool,
) -> str:
    """Describe the window kind after optional second-pass relinking."""

    unique_kinds = list(dict.fromkeys(window_kinds))
    if not cluster_relink_applied:
        return unique_kinds[0]
    if len(unique_kinds) == 1:
        return f"{unique_kinds[0]}_relinked"
    return "mixed_relinked"


def _apply_minimum_cluster_size(
    clusters: List[List[int]],
    minimum_cluster_size: int,
) -> List[List[int]]:
    """Split undersized clusters into deterministic singleton clusters."""

    adjusted_clusters: List[List[int]] = []
    for members in clusters:
        if len(members) < minimum_cluster_size:
            adjusted_clusters.extend([[member] for member in members])
            continue

        adjusted_clusters.append(sorted(members))

    return sorted(adjusted_clusters, key=lambda members: members[0])


def _collect_link_metrics(
    cluster: Sequence[int],
    metric_map: Dict[Tuple[int, int], Dict[str, Optional[float]]],
) -> List[Dict[str, Optional[float]]]:
    """Collect accepted link metrics that belong to a cluster."""

    metrics: List[Dict[str, Optional[float]]] = []
    sorted_cluster = sorted(cluster)
    for left_offset, left_local in enumerate(sorted_cluster):
        for right_local in sorted_cluster[left_offset + 1 :]:
            metric = metric_map.get((left_local, right_local))
            if metric is not None:
                metrics.append(metric)
    return metrics


def _build_cluster_group(
    records: Sequence[EmbeddedImageRecord],
    window: CandidateWindow,
    member_indices: List[int],
    *,
    link_metrics: List[Dict[str, Optional[float]]],
    reason_override: Optional[str],
    use_perceptual_hash_filter: bool = False,
    use_akaze_verifier: bool = False,
    representative_gate_applied: bool = False,
    cluster_relink_applied: bool = False,
) -> ClusterGroup:
    """Create a traceable cluster result with report-friendly statistics."""

    member_indices = sorted(member_indices)
    window_time_span, _ = describe_time_window(records, window.member_indices)
    _, cluster_time_span_seconds = describe_time_window(records, member_indices)

    similarity_values = [
        float(metric["similarity"])
        for metric in link_metrics
        if metric.get("similarity") is not None
    ]
    time_gap_values = [
        float(metric["time_gap_seconds"])
        for metric in link_metrics
        if metric.get("time_gap_seconds") is not None
    ]
    hash_distance_values = [
        int(metric["hash_distance"])
        for metric in link_metrics
        if metric.get("hash_distance") is not None
    ]
    akaze_good_match_values = [
        int(metric["akaze_good_matches"])
        for metric in link_metrics
        if metric.get("akaze_good_matches") is not None
    ]
    akaze_inlier_count_values = [
        int(metric["akaze_inlier_count"])
        for metric in link_metrics
        if metric.get("akaze_inlier_count") is not None
    ]
    akaze_inlier_ratio_values = [
        float(metric["akaze_inlier_ratio"])
        for metric in link_metrics
        if metric.get("akaze_inlier_ratio") is not None
    ]

    if reason_override is not None:
        cluster_reason = reason_override
    elif len(member_indices) == 1:
        cluster_reason = _singleton_reason(window.window_kind)
    else:
        cluster_reason = _compose_cluster_reason(
            window.window_kind,
            use_perceptual_hash_filter=use_perceptual_hash_filter,
            use_akaze_verifier=use_akaze_verifier,
            representative_gate_applied=representative_gate_applied,
            cluster_relink_applied=cluster_relink_applied,
        )

    return ClusterGroup(
        member_indices=member_indices,
        time_window_id=window.window_id,
        window_kind=window.window_kind,
        cluster_reason=cluster_reason,
        window_time_span=window_time_span,
        cluster_time_span_seconds=cluster_time_span_seconds,
        min_link_similarity=min(similarity_values) if similarity_values else None,
        max_link_similarity=max(similarity_values) if similarity_values else None,
        max_link_time_gap_seconds=max(time_gap_values) if time_gap_values else None,
        max_link_hash_distance=max(hash_distance_values) if hash_distance_values else None,
        max_link_akaze_good_matches=max(akaze_good_match_values)
        if akaze_good_match_values
        else None,
        max_link_akaze_inlier_count=max(akaze_inlier_count_values)
        if akaze_inlier_count_values
        else None,
        min_link_akaze_inlier_ratio=min(akaze_inlier_ratio_values)
        if akaze_inlier_ratio_values
        else None,
        max_link_akaze_inlier_ratio=max(akaze_inlier_ratio_values)
        if akaze_inlier_ratio_values
        else None,
        link_metrics=list(link_metrics),
    )


def _compose_cluster_reason(
    window_kind: str,
    *,
    use_perceptual_hash_filter: bool,
    use_akaze_verifier: bool,
    representative_gate_applied: bool,
    cluster_relink_applied: bool,
) -> str:
    """Compose a stable cluster reason string from active grouping stages."""

    if window_kind.startswith("filename_order"):
        base = "filename_order+visual_similarity"
    else:
        base = "capture_time+visual_similarity"

    if use_perceptual_hash_filter:
        base += "+perceptual_hash"
    if use_akaze_verifier:
        base += "+akaze"
    if representative_gate_applied:
        base += "+representative_gate"
    if cluster_relink_applied:
        base += "+cluster_relink"
    return base


def _compute_time_gap_seconds(
    left: EmbeddedImageRecord,
    right: EmbeddedImageRecord,
) -> Optional[float]:
    """Compute the absolute time gap between two records when timestamps exist."""

    if left.capture_datetime is None or right.capture_datetime is None:
        return None

    return float(abs((left.capture_datetime - right.capture_datetime).total_seconds()))


def _singleton_reason(window_kind: str) -> str:
    """Return a reason string for singleton windows."""

    if window_kind == "missing_timestamp_singleton":
        return "timestamp_missing_no_fallback"
    if window_kind == "filename_order":
        return "filename_order_no_visual_match"
    return "no_culling_match_in_time_window"
