"""End-to-end pipeline for similarity grouping on top of saved embeddings."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List

from app.clustering.algorithms import ClusterGroup, cluster_embeddings, normalize_embeddings
from app.clustering.artifacts import (
    LoadedEmbeddingArtifacts,
    load_embedding_artifacts,
    save_cluster_report,
    save_cluster_summary,
    save_clusters_csv,
)
from app.clustering.windowing import CandidateWindow
from app.config import ClusteringConfig


LOGGER = logging.getLogger(__name__)


class SimilarityClusteringPipeline:
    """Pipeline that groups saved embeddings into visually similar clusters."""

    def __init__(self, config: ClusteringConfig) -> None:
        self.config = config

    def run(self) -> Dict[str, Path]:
        """Execute the full clustering workflow and return output artifact paths."""

        self.config.output_dir.mkdir(parents=True, exist_ok=True)

        artifacts = load_embedding_artifacts(
            self.config.artifacts_dir,
            metadata_filename=self.config.metadata_filename,
            embeddings_filename=self.config.embeddings_filename,
            image_ids_filename=self.config.image_ids_filename,
            enrich_missing_timestamps=self.config.enrich_missing_timestamps,
            compute_perceptual_hashes=self.config.use_perceptual_hash_filter,
            hash_size=self.config.hash_size,
            compute_akaze_features=self.config.use_akaze_verifier,
            akaze_max_side=self.config.akaze_max_side,
        )
        LOGGER.info(
            "Loaded %s embedded images from %s (%s timestamps available, %s missing).",
            len(artifacts.records),
            self.config.artifacts_dir,
            artifacts.timestamp_available_count,
            artifacts.timestamp_missing_count,
        )

        normalized_embeddings = normalize_embeddings(artifacts.embeddings)
        LOGGER.info(
            "Clustering with method=%s similarity_threshold=%.3f max_time_gap_seconds=%.1f time_filter_required=%s.",
            self.config.clustering_method,
            self.config.similarity_threshold,
            self.config.max_time_gap_seconds,
            self.config.time_filter_required,
        )
        cluster_groups, windows = cluster_embeddings(
            normalized_embeddings,
            artifacts.records,
            method=self.config.clustering_method,
            similarity_threshold=self.config.similarity_threshold,
            dbscan_eps=self.config.dbscan_eps,
            dbscan_min_samples=self.config.dbscan_min_samples,
            minimum_cluster_size=self.config.minimum_cluster_size,
            max_time_gap_seconds=self.config.max_time_gap_seconds,
            time_filter_required=self.config.time_filter_required,
            timestamp_fallback_mode=self.config.timestamp_fallback_mode,
            filename_order_window=self.config.filename_order_window,
            use_perceptual_hash_filter=self.config.use_perceptual_hash_filter,
            max_hash_distance=self.config.max_hash_distance,
            representative_gate_enabled=self.config.representative_gate_enabled,
            representative_gate_min_cluster_size=self.config.representative_gate_min_cluster_size,
            representative_min_matches=self.config.representative_min_matches,
            use_akaze_verifier=self.config.use_akaze_verifier,
            akaze_ratio_test_threshold=self.config.akaze_ratio_test_threshold,
            akaze_min_good_matches=self.config.akaze_min_good_matches,
            akaze_min_inliers=self.config.akaze_min_inliers,
            akaze_min_inlier_ratio=self.config.akaze_min_inlier_ratio,
            cluster_relink_enabled=self.config.cluster_relink_enabled,
            cluster_relink_similarity_threshold=self.config.cluster_relink_similarity_threshold,
            cluster_relink_centroid_threshold=self.config.cluster_relink_centroid_threshold,
            cluster_relink_max_sequence_gap=self.config.cluster_relink_max_sequence_gap,
            cluster_relink_min_matches=self.config.cluster_relink_min_matches,
        )

        cluster_rows = _build_cluster_rows(artifacts, cluster_groups)
        grouped_rows = _group_rows_by_cluster(cluster_rows)
        summary = _build_summary(self.config, artifacts, cluster_groups, windows)

        clusters_path = self.config.output_dir / self.config.clusters_filename
        summary_path = self.config.output_dir / self.config.summary_filename
        report_path = self.config.output_dir / self.config.report_filename

        save_clusters_csv(clusters_path, cluster_rows)
        save_cluster_summary(summary_path, summary)
        save_cluster_report(report_path, summary=summary, grouped_rows=grouped_rows)

        LOGGER.info(
            "Saved %s cluster assignments across %s clusters to %s.",
            len(cluster_rows),
            summary["total_clusters"],
            clusters_path,
        )

        return {
            "clusters": clusters_path,
            "summary": summary_path,
            "report": report_path,
        }


def _build_cluster_rows(
    artifacts: LoadedEmbeddingArtifacts,
    cluster_groups: List[ClusterGroup],
) -> List[Dict[str, Any]]:
    """Create per-image cluster assignment rows for CSV output."""

    rows: List[Dict[str, Any]] = []

    for cluster_number, cluster_group in enumerate(cluster_groups):
        cluster_id = f"cluster_{cluster_number:04d}"
        member_records = [artifacts.records[index] for index in cluster_group.member_indices]
        member_records = sorted(
            member_records,
            key=lambda record: (
                0 if record.capture_datetime is not None else 1,
                record.capture_datetime.isoformat()
                if record.capture_datetime is not None
                else "",
                record.file_name.casefold(),
                record.relative_path.casefold(),
                record.embedding_index,
            ),
        )

        for cluster_position, record in enumerate(member_records):
            rows.append(
                {
                    "image_id": record.image_id,
                    "cluster_id": cluster_id,
                    "cluster_size": len(member_records),
                    "cluster_position": cluster_position,
                    "time_window_id": cluster_group.time_window_id,
                    "window_kind": cluster_group.window_kind,
                    "cluster_reason": cluster_group.cluster_reason,
                    "embedding_index": record.embedding_index,
                    "capture_timestamp": record.capture_timestamp,
                    "capture_time_source": record.capture_time_source,
                    "timestamp_origin": record.timestamp_origin,
                    "timestamp_available": record.timestamp_available,
                    "window_time_span": cluster_group.window_time_span,
                    "cluster_time_span_seconds": cluster_group.cluster_time_span_seconds,
                    "min_link_similarity": cluster_group.min_link_similarity,
                    "max_link_similarity": cluster_group.max_link_similarity,
                    "max_link_time_gap_seconds": cluster_group.max_link_time_gap_seconds,
                    "max_link_hash_distance": cluster_group.max_link_hash_distance,
                    "max_link_akaze_good_matches": cluster_group.max_link_akaze_good_matches,
                    "max_link_akaze_inlier_count": cluster_group.max_link_akaze_inlier_count,
                    "min_link_akaze_inlier_ratio": cluster_group.min_link_akaze_inlier_ratio,
                    "max_link_akaze_inlier_ratio": cluster_group.max_link_akaze_inlier_ratio,
                    "file_path": record.file_path,
                    "relative_path": record.relative_path,
                    "file_name": record.file_name,
                }
            )

    return sorted(
        rows,
        key=lambda row: (
            row["cluster_id"],
            int(row["cluster_position"]),
            int(row["embedding_index"]),
        ),
    )


def _group_rows_by_cluster(
    rows: List[Dict[str, Any]],
) -> Dict[str, List[Dict[str, Any]]]:
    """Group CSV rows by cluster ID for report generation."""

    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(str(row["cluster_id"]), []).append(row)
    return grouped


def _build_summary(
    config: ClusteringConfig,
    artifacts: LoadedEmbeddingArtifacts,
    cluster_groups: List[ClusterGroup],
    windows: List[CandidateWindow],
) -> Dict[str, Any]:
    """Build a summary dictionary for console output and JSON serialization."""

    cluster_sizes = [len(group.member_indices) for group in cluster_groups]
    size_distribution: Dict[str, int] = {}
    for size in cluster_sizes:
        size_distribution[str(size)] = size_distribution.get(str(size), 0) + 1

    window_kind_distribution: Dict[str, int] = {}
    for window in windows:
        window_kind_distribution[window.window_kind] = (
            window_kind_distribution.get(window.window_kind, 0) + 1
        )

    return {
        "clustering_method": config.clustering_method,
        "total_metadata_rows": artifacts.total_metadata_rows,
        "clustered_images": len(artifacts.records),
        "skipped_without_embeddings": artifacts.skipped_without_embeddings,
        "timestamp_available_images": artifacts.timestamp_available_count,
        "timestamp_missing_images": artifacts.timestamp_missing_count,
        "timestamps_from_metadata": artifacts.timestamp_from_metadata_count,
        "timestamps_from_source_read": artifacts.timestamp_from_source_count,
        "perceptual_hash_available_images": artifacts.perceptual_hash_available_count,
        "akaze_feature_available_images": artifacts.akaze_feature_available_count,
        "embedding_dimension": int(artifacts.embeddings.shape[1])
        if artifacts.embeddings.ndim == 2
        else 0,
        "total_clusters": len(cluster_groups),
        "singleton_clusters": sum(size == 1 for size in cluster_sizes),
        "non_singleton_clusters": sum(size > 1 for size in cluster_sizes),
        "largest_cluster_size": max(cluster_sizes) if cluster_sizes else 0,
        "cluster_size_distribution": size_distribution,
        "total_time_windows": len(windows),
        "time_window_distribution": window_kind_distribution,
        "minimum_cluster_size": config.minimum_cluster_size,
        "max_time_gap_seconds": config.max_time_gap_seconds,
        "time_filter_required": config.time_filter_required,
        "timestamp_fallback_mode": config.timestamp_fallback_mode,
        "filename_order_window": config.filename_order_window,
        "enrich_missing_timestamps": config.enrich_missing_timestamps,
        "similarity_threshold": config.similarity_threshold,
        "dbscan_eps": config.dbscan_eps,
        "dbscan_min_samples": config.dbscan_min_samples,
        "use_perceptual_hash_filter": config.use_perceptual_hash_filter,
        "max_hash_distance": config.max_hash_distance,
        "hash_size": config.hash_size,
        "use_akaze_verifier": config.use_akaze_verifier,
        "akaze_max_side": config.akaze_max_side,
        "akaze_ratio_test_threshold": config.akaze_ratio_test_threshold,
        "akaze_min_good_matches": config.akaze_min_good_matches,
        "akaze_min_inliers": config.akaze_min_inliers,
        "akaze_min_inlier_ratio": config.akaze_min_inlier_ratio,
        "representative_gate_enabled": config.representative_gate_enabled,
        "representative_gate_min_cluster_size": config.representative_gate_min_cluster_size,
        "representative_min_matches": config.representative_min_matches,
        "cluster_relink_enabled": config.cluster_relink_enabled,
        "cluster_relink_similarity_threshold": config.cluster_relink_similarity_threshold,
        "cluster_relink_centroid_threshold": config.cluster_relink_centroid_threshold,
        "cluster_relink_max_sequence_gap": config.cluster_relink_max_sequence_gap,
        "cluster_relink_min_matches": config.cluster_relink_min_matches,
        "relinked_clusters": sum(
            "cluster_relink" in group.cluster_reason for group in cluster_groups
        ),
    }
