"""Artifact loading for the local labeling tool."""

from __future__ import annotations

import csv
import json
import logging
from pathlib import Path
from typing import Dict, List, Optional

from app.labeling.models import ClusterItem, DatasetBundle, ImageItem


LOGGER = logging.getLogger(__name__)


def load_labeling_dataset(
    artifacts_dir: Path,
    *,
    metadata_filename: str,
    image_ids_filename: str,
    clusters_filename: str,
) -> DatasetBundle:
    """Load the artifact bundle used for local labeling."""

    metadata_path = artifacts_dir / metadata_filename
    image_ids_path = artifacts_dir / image_ids_filename
    clusters_path = artifacts_dir / clusters_filename

    if not metadata_path.exists():
        raise FileNotFoundError(f"Metadata file not found: {metadata_path}")
    if not image_ids_path.exists():
        raise FileNotFoundError(f"Image ID file not found: {image_ids_path}")
    if not clusters_path.exists():
        raise FileNotFoundError(f"Cluster file not found: {clusters_path}")

    metadata_rows = _load_csv_rows(metadata_path)
    cluster_rows = _load_csv_rows(clusters_path)
    image_ids = json.loads(image_ids_path.read_text(encoding="utf-8"))

    metadata_by_id = {row["image_id"]: row for row in metadata_rows}
    cluster_rows_by_id = {row["image_id"]: row for row in cluster_rows}

    missing_cluster_ids = [
        image_id for image_id in image_ids if image_id not in cluster_rows_by_id
    ]
    if missing_cluster_ids:
        raise ValueError(
            "clusters.csv is missing image IDs found in image_ids.json. "
            f"Examples: {missing_cluster_ids[:5]}"
        )

    ordered_images: List[ImageItem] = []
    for image_id in image_ids:
        metadata_row = metadata_by_id.get(image_id)
        cluster_row = cluster_rows_by_id[image_id]
        if metadata_row is None:
            raise ValueError(f"images.csv is missing image_id {image_id}.")

        file_path = Path(cluster_row["file_path"])
        ordered_images.append(
            ImageItem(
                image_id=image_id,
                file_path=file_path,
                relative_path=cluster_row.get("relative_path", metadata_row.get("relative_path", "")),
                file_name=cluster_row.get("file_name", metadata_row["file_name"]),
                cluster_id=cluster_row["cluster_id"],
                cluster_size=int(cluster_row["cluster_size"]),
                embedding_index=_parse_optional_int(cluster_row.get("embedding_index")),
                capture_timestamp=cluster_row.get(
                    "capture_timestamp", metadata_row.get("capture_timestamp", "")
                ),
                capture_time_source=cluster_row.get(
                    "capture_time_source",
                    metadata_row.get("capture_time_source", "missing"),
                ),
                timestamp_available=_parse_bool(
                    cluster_row.get(
                        "timestamp_available",
                        metadata_row.get("timestamp_available", "False"),
                    )
                ),
                file_exists=file_path.exists(),
            )
        )

    images_by_id = {image.image_id: image for image in ordered_images}
    clusters_by_id = _build_clusters(cluster_rows, images_by_id)
    multi_image_clusters = [
        cluster
        for cluster in sorted(clusters_by_id.values(), key=lambda item: item.cluster_id)
        if len(cluster.members) >= 2
    ]
    singleton_images = [image for image in ordered_images if image.cluster_size <= 1]

    missing_files = [image.file_name for image in ordered_images if not image.file_exists]
    if missing_files:
        LOGGER.warning(
            "Found %s missing image files while loading labeling data. "
            "They will display as missing in the UI.",
            len(missing_files),
        )

    return DatasetBundle(
        images_by_id=images_by_id,
        ordered_images=ordered_images,
        clusters_by_id=clusters_by_id,
        multi_image_clusters=multi_image_clusters,
        singleton_images=singleton_images,
    )


def _build_clusters(
    cluster_rows: List[Dict[str, str]],
    images_by_id: Dict[str, ImageItem],
) -> Dict[str, ClusterItem]:
    """Build ClusterItem objects from clustering output rows."""

    grouped_rows: Dict[str, List[Dict[str, str]]] = {}
    for row in cluster_rows:
        grouped_rows.setdefault(row["cluster_id"], []).append(row)

    clusters: Dict[str, ClusterItem] = {}
    for cluster_id, rows in grouped_rows.items():
        ordered_rows = sorted(
            rows,
            key=lambda row: (
                _parse_optional_int(row.get("cluster_position")) or 0,
                _parse_optional_int(row.get("embedding_index")) or 0,
            ),
        )
        members = [images_by_id[row["image_id"]] for row in ordered_rows]
        first_row = ordered_rows[0]
        clusters[cluster_id] = ClusterItem(
            cluster_id=cluster_id,
            members=members,
            cluster_reason=first_row.get("cluster_reason", ""),
            window_kind=first_row.get("window_kind", ""),
            time_window_id=first_row.get("time_window_id", ""),
        )

    return clusters


def _load_csv_rows(path: Path) -> List[Dict[str, str]]:
    """Load a CSV file into row dictionaries."""

    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _parse_bool(value: object) -> bool:
    """Parse bool-like CSV values."""

    return str(value).strip().lower() in {"1", "true", "yes"}


def _parse_optional_int(value: Optional[str]) -> Optional[int]:
    """Parse an optional integer from a CSV field."""

    text = (value or "").strip()
    if not text:
        return None
    return int(text)
