"""Loading Week 1 artifacts and saving refined culling-oriented clustering outputs."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime
import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

from app.clustering.akaze import AkazeFeatures, compute_akaze_features as compute_akaze_image_features
from app.clustering.hashing import compute_average_hash
from app.data.image_scanner import read_capture_time_from_file
from app.utils.io_utils import save_json


LOGGER = logging.getLogger(__name__)


@dataclass
class EmbeddedImageRecord:
    """Metadata row for one embedded image used during clustering."""

    image_id: str
    file_path: str
    relative_path: str
    file_name: str
    embedding_index: int
    capture_timestamp: str
    capture_time_source: str
    timestamp_available: bool
    timestamp_origin: str
    capture_datetime: Optional[datetime]
    filename_sequence_number: Optional[int]
    perceptual_hash: Optional[np.ndarray] = None
    akaze_features: Optional[AkazeFeatures] = None


@dataclass
class LoadedEmbeddingArtifacts:
    """In-memory representation of the embedding artifacts used for clustering."""

    embeddings: np.ndarray
    records: List[EmbeddedImageRecord]
    total_metadata_rows: int
    skipped_without_embeddings: int
    timestamp_available_count: int
    timestamp_missing_count: int
    timestamp_from_metadata_count: int
    timestamp_from_source_count: int
    perceptual_hash_available_count: int
    akaze_feature_available_count: int


def load_embedding_artifacts(
    artifacts_dir: Path,
    *,
    metadata_filename: str,
    embeddings_filename: str,
    image_ids_filename: str,
    enrich_missing_timestamps: bool = True,
    compute_perceptual_hashes: bool = False,
    hash_size: int = 8,
    compute_akaze_features: bool = False,
    akaze_max_side: int = 1024,
) -> LoadedEmbeddingArtifacts:
    """Load and validate the artifact set produced by Week 1."""

    metadata_path = artifacts_dir / metadata_filename
    embeddings_path = artifacts_dir / embeddings_filename
    image_ids_path = artifacts_dir / image_ids_filename

    if not metadata_path.exists():
        raise FileNotFoundError(f"Metadata file not found: {metadata_path}")

    if not embeddings_path.exists():
        raise FileNotFoundError(f"Embeddings file not found: {embeddings_path}")

    embeddings = np.load(embeddings_path)
    if embeddings.ndim != 2:
        raise ValueError(
            f"Expected embeddings.npy with shape [N, D], received {tuple(embeddings.shape)}."
        )

    rows = _load_csv_rows(metadata_path)
    records = _extract_embedded_records(
        rows,
        enrich_missing_timestamps=enrich_missing_timestamps,
        compute_perceptual_hashes=compute_perceptual_hashes,
        hash_size=hash_size,
        compute_akaze_features=compute_akaze_features,
        akaze_max_side=akaze_max_side,
    )

    expected_indices = list(range(len(records)))
    actual_indices = [record.embedding_index for record in records]
    if actual_indices != expected_indices:
        raise ValueError(
            "Embedded metadata rows must have contiguous embedding_index values "
            "from 0 to N-1."
        )

    if len(records) != embeddings.shape[0]:
        raise ValueError(
            "Mismatch between metadata rows with embeddings and embeddings.npy rows: "
            f"{len(records)} != {embeddings.shape[0]}"
        )

    if image_ids_path.exists():
        image_ids = json.loads(image_ids_path.read_text(encoding="utf-8"))
        expected_image_ids = [record.image_id for record in records]
        if image_ids != expected_image_ids:
            raise ValueError("image_ids.json is not aligned with images.csv and embeddings.npy.")
    else:
        LOGGER.warning("Optional image IDs file is missing: %s", image_ids_path)

    timestamp_available_count = sum(record.timestamp_available for record in records)
    timestamp_from_metadata_count = sum(
        record.timestamp_origin == "metadata" for record in records
    )
    timestamp_from_source_count = sum(
        record.timestamp_origin == "source_read" for record in records
    )
    perceptual_hash_available_count = sum(
        record.perceptual_hash is not None for record in records
    )
    akaze_feature_available_count = sum(
        record.akaze_features is not None for record in records
    )

    return LoadedEmbeddingArtifacts(
        embeddings=embeddings.astype(np.float32, copy=False),
        records=records,
        total_metadata_rows=len(rows),
        skipped_without_embeddings=len(rows) - len(records),
        timestamp_available_count=timestamp_available_count,
        timestamp_missing_count=len(records) - timestamp_available_count,
        timestamp_from_metadata_count=timestamp_from_metadata_count,
        timestamp_from_source_count=timestamp_from_source_count,
        perceptual_hash_available_count=perceptual_hash_available_count,
        akaze_feature_available_count=akaze_feature_available_count,
    )


def save_clusters_csv(path: Path, rows: List[Dict[str, Any]]) -> None:
    """Save per-image cluster assignments to CSV."""

    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "image_id",
        "cluster_id",
        "cluster_size",
        "cluster_position",
        "time_window_id",
        "window_kind",
        "cluster_reason",
        "embedding_index",
        "capture_timestamp",
        "capture_time_source",
        "timestamp_origin",
        "timestamp_available",
        "window_time_span",
        "cluster_time_span_seconds",
        "min_link_similarity",
        "max_link_similarity",
        "max_link_time_gap_seconds",
        "max_link_hash_distance",
        "max_link_akaze_good_matches",
        "max_link_akaze_inlier_count",
        "min_link_akaze_inlier_ratio",
        "max_link_akaze_inlier_ratio",
        "file_path",
        "relative_path",
        "file_name",
    ]

    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def save_cluster_summary(path: Path, summary: Dict[str, Any]) -> None:
    """Save the cluster summary to JSON."""

    save_json(path, summary)


def save_cluster_report(
    path: Path,
    *,
    summary: Dict[str, Any],
    grouped_rows: Dict[str, List[Dict[str, Any]]],
) -> None:
    """Save a plain-text report grouping file paths by cluster."""

    path.parent.mkdir(parents=True, exist_ok=True)

    size_distribution = summary.get("cluster_size_distribution", {})
    size_distribution_text = ", ".join(
        f"{size}:{count}" for size, count in sorted(size_distribution.items(), key=lambda item: int(item[0]))
    ) or "none"

    lines = [
        "Cluster Summary",
        f"method: {summary['clustering_method']}",
        f"time_filter_required: {summary['time_filter_required']}",
        f"max_time_gap_seconds: {summary['max_time_gap_seconds']}",
        f"similarity_threshold: {summary['similarity_threshold']}",
        f"use_perceptual_hash_filter: {summary['use_perceptual_hash_filter']}",
        f"max_hash_distance: {summary['max_hash_distance']}",
        f"use_akaze_verifier: {summary.get('use_akaze_verifier')}",
        f"akaze_max_side: {summary.get('akaze_max_side')}",
        f"akaze_ratio_test_threshold: {summary.get('akaze_ratio_test_threshold')}",
        f"akaze_min_good_matches: {summary.get('akaze_min_good_matches')}",
        f"akaze_min_inliers: {summary.get('akaze_min_inliers')}",
        f"akaze_min_inlier_ratio: {summary.get('akaze_min_inlier_ratio')}",
        f"representative_gate_enabled: {summary.get('representative_gate_enabled')}",
        f"representative_gate_min_cluster_size: {summary.get('representative_gate_min_cluster_size')}",
        f"representative_min_matches: {summary.get('representative_min_matches')}",
        f"cluster_relink_enabled: {summary.get('cluster_relink_enabled')}",
        f"cluster_relink_similarity_threshold: {summary.get('cluster_relink_similarity_threshold')}",
        f"cluster_relink_centroid_threshold: {summary.get('cluster_relink_centroid_threshold')}",
        f"cluster_relink_max_sequence_gap: {summary.get('cluster_relink_max_sequence_gap')}",
        f"cluster_relink_min_matches: {summary.get('cluster_relink_min_matches')}",
        f"timestamp_fallback_mode: {summary['timestamp_fallback_mode']}",
        f"total_metadata_rows: {summary['total_metadata_rows']}",
        f"clustered_images: {summary['clustered_images']}",
        f"skipped_without_embeddings: {summary['skipped_without_embeddings']}",
        f"timestamp_available_images: {summary['timestamp_available_images']}",
        f"timestamp_missing_images: {summary['timestamp_missing_images']}",
        f"timestamps_from_metadata: {summary['timestamps_from_metadata']}",
        f"timestamps_from_source_read: {summary['timestamps_from_source_read']}",
        f"akaze_feature_available_images: {summary.get('akaze_feature_available_images')}",
        f"total_time_windows: {summary['total_time_windows']}",
        f"singleton_clusters: {summary['singleton_clusters']}",
        f"largest_cluster_size: {summary['largest_cluster_size']}",
        f"relinked_clusters: {summary.get('relinked_clusters')}",
        f"cluster_size_distribution: {size_distribution_text}",
        "",
        "Clusters",
    ]

    for cluster_id in sorted(grouped_rows.keys()):
        members = grouped_rows[cluster_id]
        first_member = members[0]
        lines.append(
            f"{cluster_id} | size={len(members)} | window={first_member['time_window_id']} | "
            f"reason={first_member['cluster_reason']} | time_span={first_member['cluster_time_span_seconds']}"
        )
        lines.append(
            f"  window_kind={first_member['window_kind']} | window_span={first_member['window_time_span']} | "
            f"min_link_similarity={first_member['min_link_similarity']} | "
            f"max_link_similarity={first_member['max_link_similarity']} | "
            f"max_link_time_gap_seconds={first_member['max_link_time_gap_seconds']} | "
            f"max_link_hash_distance={first_member['max_link_hash_distance']} | "
            f"max_link_akaze_good_matches={first_member.get('max_link_akaze_good_matches')} | "
            f"max_link_akaze_inlier_count={first_member.get('max_link_akaze_inlier_count')} | "
            f"min_link_akaze_inlier_ratio={first_member.get('min_link_akaze_inlier_ratio')} | "
            f"max_link_akaze_inlier_ratio={first_member.get('max_link_akaze_inlier_ratio')}"
        )
        for row in members:
            lines.append(
                f"  - {row['relative_path']} | timestamp={row['capture_timestamp'] or 'missing'} | "
                f"source={row['capture_time_source']}"
            )
        lines.append("")

    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def _load_csv_rows(path: Path) -> List[Dict[str, str]]:
    """Load CSV rows into dictionaries."""

    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _extract_embedded_records(
    rows: List[Dict[str, str]],
    *,
    enrich_missing_timestamps: bool,
    compute_perceptual_hashes: bool,
    hash_size: int,
    compute_akaze_features: bool,
    akaze_max_side: int,
) -> List[EmbeddedImageRecord]:
    """Extract and enrich metadata rows that map directly to embeddings."""

    records: List[EmbeddedImageRecord] = []
    for row in rows:
        embedding_index_text = (row.get("embedding_index") or "").strip()
        if not embedding_index_text:
            continue

        file_path = row["file_path"]
        capture_metadata = _resolve_capture_metadata(
            row,
            Path(file_path),
            enrich_missing_timestamps=enrich_missing_timestamps,
        )
        perceptual_hash = (
            compute_average_hash(Path(file_path), hash_size=hash_size)
            if compute_perceptual_hashes
            else None
        )
        akaze_features = (
            compute_akaze_image_features(Path(file_path), max_side=akaze_max_side)
            if compute_akaze_features
            else None
        )

        records.append(
            EmbeddedImageRecord(
                image_id=row["image_id"],
                file_path=file_path,
                relative_path=row.get("relative_path", ""),
                file_name=row["file_name"],
                embedding_index=int(embedding_index_text),
                capture_timestamp=capture_metadata["capture_timestamp"],
                capture_time_source=capture_metadata["capture_time_source"],
                timestamp_available=capture_metadata["timestamp_available"],
                timestamp_origin=capture_metadata["timestamp_origin"],
                capture_datetime=capture_metadata["capture_datetime"],
                filename_sequence_number=_extract_filename_sequence_number(row["file_name"]),
                perceptual_hash=perceptual_hash,
                akaze_features=akaze_features,
            )
        )

    return sorted(records, key=lambda record: record.embedding_index)


def _resolve_capture_metadata(
    row: Dict[str, str],
    path: Path,
    *,
    enrich_missing_timestamps: bool,
) -> Dict[str, Any]:
    """Resolve capture time from saved metadata or source-file EXIF."""

    metadata_timestamp = (row.get("capture_timestamp") or "").strip()
    metadata_source = (row.get("capture_time_source") or "").strip() or "metadata"
    metadata_datetime = _parse_timestamp(metadata_timestamp)
    if metadata_datetime is not None:
        return {
            "capture_timestamp": metadata_datetime.strftime("%Y-%m-%d %H:%M:%S"),
            "capture_time_source": metadata_source,
            "timestamp_available": True,
            "timestamp_origin": "metadata",
            "capture_datetime": metadata_datetime,
        }

    if enrich_missing_timestamps:
        source_capture = read_capture_time_from_file(path)
        if source_capture is not None:
            source_datetime = _parse_timestamp(source_capture["timestamp"])
            if source_datetime is not None:
                return {
                    "capture_timestamp": source_datetime.strftime("%Y-%m-%d %H:%M:%S"),
                    "capture_time_source": source_capture["source"],
                    "timestamp_available": True,
                    "timestamp_origin": "source_read",
                    "capture_datetime": source_datetime,
                }

    return {
        "capture_timestamp": "",
        "capture_time_source": "missing",
        "timestamp_available": False,
        "timestamp_origin": "missing",
        "capture_datetime": None,
    }


def _parse_timestamp(value: str) -> Optional[datetime]:
    """Parse normalized timestamp strings used by project metadata."""

    text = value.strip()
    if not text:
        return None

    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y:%m:%d %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue

    LOGGER.debug("Unable to parse metadata timestamp: %s", text)
    return None


def _extract_filename_sequence_number(file_name: str) -> Optional[int]:
    """Extract the trailing numeric token from a filename when available."""

    stem = file_name.rsplit(".", 1)[0]
    matches = re.findall(r"(\d+)", stem)
    if not matches:
        return None
    return int(matches[-1])
