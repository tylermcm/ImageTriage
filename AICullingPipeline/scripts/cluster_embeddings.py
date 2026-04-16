"""CLI entry point for similarity clustering on saved image embeddings."""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
import sys
from typing import Optional


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.engine import ClusteringConfig, run_similarity_clustering
from app.utils.logging_utils import setup_logging


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""

    parser = argparse.ArgumentParser(
        description="Group images into culling-relevant clusters from saved embeddings."
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("configs/cluster_embeddings.json"),
        help="Path to the JSON clustering config file.",
    )
    parser.add_argument(
        "--artifacts-dir",
        type=Path,
        help="Override the folder containing images.csv, embeddings.npy, and image_ids.json.",
    )
    parser.add_argument("--output-dir", type=Path, help="Override the output folder.")
    parser.add_argument(
        "--clustering-method",
        choices=("graph", "dbscan"),
        help="Clustering method to use.",
    )
    parser.add_argument(
        "--similarity-threshold",
        type=float,
        help="Cosine similarity threshold used after time-window filtering.",
    )
    parser.add_argument(
        "--max-time-gap-seconds",
        type=float,
        help="Maximum allowed capture-time gap for pair comparisons.",
    )
    parser.add_argument(
        "--time-filter-required",
        type=str,
        choices=("true", "false"),
        help="Whether images without timestamps must stay out of grouped comparisons.",
    )
    parser.add_argument(
        "--timestamp-fallback-mode",
        choices=("none", "filename_order"),
        help="Weak fallback for missing timestamps.",
    )
    parser.add_argument(
        "--filename-order-window",
        type=int,
        help="Maximum filename-order gap used by the weak fallback mode.",
    )
    parser.add_argument(
        "--dbscan-eps",
        type=float,
        help="Cosine distance epsilon for DBSCAN.",
    )
    parser.add_argument(
        "--dbscan-min-samples",
        type=int,
        help="Minimum neighborhood size for DBSCAN.",
    )
    parser.add_argument(
        "--minimum-cluster-size",
        type=int,
        help="Clusters smaller than this are split into singletons.",
    )
    parser.add_argument(
        "--use-perceptual-hash-filter",
        type=str,
        choices=("true", "false"),
        help="Require perceptual hash agreement in addition to embedding similarity.",
    )
    parser.add_argument(
        "--max-hash-distance",
        type=int,
        help="Maximum allowed perceptual hash Hamming distance.",
    )
    parser.add_argument(
        "--use-akaze-verifier",
        type=str,
        choices=("true", "false"),
        help="Require AKAZE local feature agreement in addition to DINO similarity.",
    )
    parser.add_argument(
        "--akaze-max-side",
        type=int,
        help="Maximum image side length used when extracting AKAZE features.",
    )
    parser.add_argument(
        "--akaze-ratio-test-threshold",
        type=float,
        help="Lowe ratio-test threshold used for AKAZE matching.",
    )
    parser.add_argument(
        "--akaze-min-good-matches",
        type=int,
        help="Minimum AKAZE good matches required for a verified pair.",
    )
    parser.add_argument(
        "--akaze-min-inliers",
        type=int,
        help="Minimum AKAZE RANSAC inliers required for a verified pair.",
    )
    parser.add_argument(
        "--akaze-min-inlier-ratio",
        type=float,
        help="Minimum AKAZE inlier ratio required for a verified pair.",
    )
    parser.add_argument(
        "--representative-gate-enabled",
        type=str,
        choices=("true", "false"),
        help="Require large graph clusters to agree with multiple deterministic representatives.",
    )
    parser.add_argument(
        "--representative-gate-min-cluster-size",
        type=int,
        help="Cluster size at which the representative gate starts applying to new members.",
    )
    parser.add_argument(
        "--representative-min-matches",
        type=int,
        help="Minimum number of representative matches required once the gate is active.",
    )
    parser.add_argument(
        "--cluster-relink-enabled",
        type=str,
        choices=("true", "false"),
        help="Run a second pass that can reconnect split clusters across a short sequence gap.",
    )
    parser.add_argument(
        "--cluster-relink-similarity-threshold",
        type=float,
        help="Tail-to-head cosine similarity threshold used by the second-pass cluster relink.",
    )
    parser.add_argument(
        "--cluster-relink-centroid-threshold",
        type=float,
        help="Cluster-centroid cosine similarity threshold used by the second-pass relink.",
    )
    parser.add_argument(
        "--cluster-relink-max-sequence-gap",
        type=int,
        help="Maximum filename/sequence gap considered by the second-pass relink.",
    )
    parser.add_argument(
        "--cluster-relink-min-matches",
        type=int,
        help="Minimum number of strong tail/head matches required by the second-pass relink.",
    )
    return parser.parse_args()


def main() -> None:
    """Load config, run clustering, and print output locations."""

    args = parse_args()
    config = ClusteringConfig.from_file(args.config).apply_overrides(
        artifacts_dir=args.artifacts_dir,
        output_dir=args.output_dir,
        clustering_method=args.clustering_method,
        similarity_threshold=args.similarity_threshold,
        max_time_gap_seconds=args.max_time_gap_seconds,
        time_filter_required=_parse_optional_bool(args.time_filter_required),
        timestamp_fallback_mode=args.timestamp_fallback_mode,
        filename_order_window=args.filename_order_window,
        dbscan_eps=args.dbscan_eps,
        dbscan_min_samples=args.dbscan_min_samples,
        minimum_cluster_size=args.minimum_cluster_size,
        use_perceptual_hash_filter=_parse_optional_bool(args.use_perceptual_hash_filter),
        max_hash_distance=args.max_hash_distance,
        use_akaze_verifier=_parse_optional_bool(args.use_akaze_verifier),
        akaze_max_side=args.akaze_max_side,
        akaze_ratio_test_threshold=args.akaze_ratio_test_threshold,
        akaze_min_good_matches=args.akaze_min_good_matches,
        akaze_min_inliers=args.akaze_min_inliers,
        akaze_min_inlier_ratio=args.akaze_min_inlier_ratio,
        representative_gate_enabled=_parse_optional_bool(args.representative_gate_enabled),
        representative_gate_min_cluster_size=args.representative_gate_min_cluster_size,
        representative_min_matches=args.representative_min_matches,
        cluster_relink_enabled=_parse_optional_bool(args.cluster_relink_enabled),
        cluster_relink_similarity_threshold=args.cluster_relink_similarity_threshold,
        cluster_relink_centroid_threshold=args.cluster_relink_centroid_threshold,
        cluster_relink_max_sequence_gap=args.cluster_relink_max_sequence_gap,
        cluster_relink_min_matches=args.cluster_relink_min_matches,
    )

    setup_logging(
        config.log_level,
        log_file=config.output_dir / "cluster_embeddings.log",
    )

    try:
        outputs = run_similarity_clustering(config)

        summary = json.loads(
            Path(outputs["summary"]).read_text(encoding="utf-8")
        )

        print("Clustering complete.")
        for name, path in outputs.items():
            print(f"{name}: {path}")

        print("")
        print("Summary:")
        print(f"total_metadata_rows: {summary['total_metadata_rows']}")
        print(f"clustered_images: {summary['clustered_images']}")
        print(f"skipped_without_embeddings: {summary['skipped_without_embeddings']}")
        print(f"timestamp_available_images: {summary['timestamp_available_images']}")
        print(f"timestamp_missing_images: {summary['timestamp_missing_images']}")
        print(f"total_time_windows: {summary['total_time_windows']}")
        print(f"total_clusters: {summary['total_clusters']}")
        print(f"singleton_clusters: {summary['singleton_clusters']}")
        print(f"largest_cluster_size: {summary['largest_cluster_size']}")
    finally:
        logging.shutdown()


def _parse_optional_bool(value: Optional[str]) -> Optional[bool]:
    """Parse optional boolean CLI inputs."""

    if value is None:
        return None

    return value.lower() == "true"


if __name__ == "__main__":
    main()
