"""Artifact loading and saving helpers for Week 4 ranking."""

from __future__ import annotations

import csv
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import numpy as np

from app.labeling.storage import pair_key
from app.utils.io_utils import save_json


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class RankedImageArtifact:
    """One embedded image resolved from saved pipeline artifacts."""

    image_id: str
    embedding_index: int
    file_path: str
    relative_path: str
    file_name: str
    cluster_id: str
    cluster_size: int
    cluster_position: int
    cluster_reason: str
    capture_timestamp: str
    capture_time_source: str


@dataclass
class RankingArtifacts:
    """Embeddings plus aligned metadata used by training and inference."""

    embeddings: np.ndarray
    ordered_images: List[RankedImageArtifact]
    images_by_id: Dict[str, RankedImageArtifact]
    clusters_by_id: Dict[str, List[RankedImageArtifact]]

    @property
    def feature_dim(self) -> int:
        """Return the embedding width."""

        return int(self.embeddings.shape[1]) if self.embeddings.ndim == 2 else 0


@dataclass(frozen=True)
class PairwisePreferenceRecord:
    """One usable pairwise preference for ranker training."""

    preferred_image_id: str
    other_image_id: str
    preferred_index: int
    other_index: int
    source_mode: str
    cluster_id: Optional[str]
    label_origin: str


@dataclass
class LoadedPreferenceLabels:
    """Usable preference pairs plus summary counts from label artifacts."""

    preferences: List[PairwisePreferenceRecord]
    summary: Dict[str, Any]


@dataclass(frozen=True)
class ClusterLabelRecord:
    """Latest saved human cluster labels for one cluster."""

    cluster_id: str
    best_image_ids: tuple[str, ...]
    acceptable_image_ids: tuple[str, ...]
    reject_image_ids: tuple[str, ...]
    timestamp: str
    annotator_id: Optional[str]

    def label_for_image(self, image_id: str) -> Optional[str]:
        """Return the saved human label for one image ID when present."""

        if image_id in self.best_image_ids:
            return "best"
        if image_id in self.acceptable_image_ids:
            return "acceptable"
        if image_id in self.reject_image_ids:
            return "reject"
        return None


def load_ranking_artifacts(
    artifacts_dir: Path,
    *,
    metadata_filename: str,
    embeddings_filename: str,
    image_ids_filename: str,
    clusters_filename: str,
) -> RankingArtifacts:
    """Load embeddings, aligned metadata, and cluster assignments for ranking."""

    metadata_path = artifacts_dir / metadata_filename
    embeddings_path = artifacts_dir / embeddings_filename
    image_ids_path = artifacts_dir / image_ids_filename
    clusters_path = artifacts_dir / clusters_filename

    if not metadata_path.exists():
        raise FileNotFoundError(f"Metadata file not found: {metadata_path}")
    if not embeddings_path.exists():
        raise FileNotFoundError(f"Embeddings file not found: {embeddings_path}")
    if not image_ids_path.exists():
        raise FileNotFoundError(f"Image IDs file not found: {image_ids_path}")

    embeddings = np.load(embeddings_path).astype(np.float32, copy=False)
    if embeddings.ndim != 2:
        raise ValueError(
            f"Expected embeddings.npy with shape [N, D], received {tuple(embeddings.shape)}."
        )

    metadata_rows = _load_csv_rows(metadata_path)
    embedded_rows = _extract_embedded_rows(metadata_rows)
    expected_indices = list(range(len(embedded_rows)))
    actual_indices = [int(row["embedding_index"]) for row in embedded_rows]
    if actual_indices != expected_indices:
        raise ValueError(
            "Embedded metadata rows must have contiguous embedding_index values from 0 to N-1."
        )

    image_ids = _load_json_records(image_ids_path)
    if not isinstance(image_ids, list):
        raise ValueError("image_ids.json must contain a JSON list.")

    expected_image_ids = [row["image_id"] for row in embedded_rows]
    if list(image_ids) != expected_image_ids:
        raise ValueError("image_ids.json is not aligned with images.csv and embeddings.npy.")

    if len(embedded_rows) != embeddings.shape[0]:
        raise ValueError(
            "Mismatch between embedded metadata rows and embeddings.npy rows: "
            f"{len(embedded_rows)} != {embeddings.shape[0]}"
        )

    cluster_rows_by_id = _load_cluster_rows_by_id(
        clusters_path=clusters_path,
        embedded_rows=embedded_rows,
    )

    ordered_images: List[RankedImageArtifact] = []
    for row in embedded_rows:
        cluster_row = cluster_rows_by_id[row["image_id"]]
        ordered_images.append(
            RankedImageArtifact(
                image_id=row["image_id"],
                embedding_index=int(row["embedding_index"]),
                file_path=cluster_row["file_path"],
                relative_path=cluster_row["relative_path"],
                file_name=cluster_row["file_name"],
                cluster_id=cluster_row["cluster_id"],
                cluster_size=int(cluster_row["cluster_size"]),
                cluster_position=int(cluster_row["cluster_position"]),
                cluster_reason=cluster_row["cluster_reason"],
                capture_timestamp=cluster_row["capture_timestamp"],
                capture_time_source=cluster_row["capture_time_source"],
            )
        )

    images_by_id = {image.image_id: image for image in ordered_images}
    clusters_by_id: Dict[str, List[RankedImageArtifact]] = {}
    for image in ordered_images:
        clusters_by_id.setdefault(image.cluster_id, []).append(image)

    for cluster_id, members in clusters_by_id.items():
        clusters_by_id[cluster_id] = sorted(
            members,
            key=lambda item: (item.cluster_position, item.embedding_index, item.file_name.casefold()),
        )

    return RankingArtifacts(
        embeddings=embeddings,
        ordered_images=ordered_images,
        images_by_id=images_by_id,
        clusters_by_id=clusters_by_id,
    )


def load_preference_labels(
    *,
    labels_dir: Path,
    ranking_artifacts: RankingArtifacts,
    pairwise_labels_filename: str,
    cluster_labels_filename: str,
    include_cluster_label_pairs: bool,
    skip_ties: bool,
) -> LoadedPreferenceLabels:
    """Load pairwise preferences from label files and convert cluster labels when enabled."""

    pairwise_path = labels_dir / pairwise_labels_filename
    cluster_path = labels_dir / cluster_labels_filename

    preferences: List[PairwisePreferenceRecord] = []
    summary: Dict[str, Any] = {
        "pairwise_label_records": 0,
        "pairwise_preference_pairs": 0,
        "cluster_label_records": 0,
        "cluster_label_preference_pairs": 0,
        "tie_records_skipped": 0,
        "skip_records_skipped": 0,
        "missing_images_skipped": 0,
        "invalid_records_skipped": 0,
        "labels_dir": str(labels_dir),
    }

    if pairwise_path.exists():
        latest_pairwise_by_key: Dict[tuple[str, str], Dict[str, Any]] = {}
        for record in _load_records_file(pairwise_path):
            summary["pairwise_label_records"] += 1
            image_a_id = str(record.get("image_a_id", "")).strip()
            image_b_id = str(record.get("image_b_id", "")).strip()
            if not image_a_id or not image_b_id:
                summary["invalid_records_skipped"] += 1
                continue
            latest_pairwise_by_key[pair_key(image_a_id, image_b_id)] = record

        for record in latest_pairwise_by_key.values():
            preference = _pairwise_record_to_preference(
                record,
                ranking_artifacts=ranking_artifacts,
                skip_ties=skip_ties,
                summary=summary,
            )
            if preference is None:
                continue
            preferences.append(preference)
            summary["pairwise_preference_pairs"] += 1
    else:
        LOGGER.info("Pairwise labels not found at %s; continuing.", pairwise_path)

    if include_cluster_label_pairs and cluster_path.exists():
        latest_cluster_by_id: Dict[str, Dict[str, Any]] = {}
        for record in _load_records_file(cluster_path):
            summary["cluster_label_records"] += 1
            cluster_id = str(record.get("cluster_id", "")).strip()
            if not cluster_id:
                summary["invalid_records_skipped"] += 1
                continue
            latest_cluster_by_id[cluster_id] = record

        for record in latest_cluster_by_id.values():
            generated = _cluster_record_to_preferences(
                record,
                ranking_artifacts=ranking_artifacts,
                summary=summary,
            )
            preferences.extend(generated)
            summary["cluster_label_preference_pairs"] += len(generated)
    elif include_cluster_label_pairs:
        LOGGER.info("Cluster labels not found at %s; continuing without them.", cluster_path)

    summary["total_preference_pairs"] = len(preferences)
    summary["pair_source_distribution"] = _count_by_field(preferences, "label_origin")
    summary["source_mode_distribution"] = _count_by_field(preferences, "source_mode")
    return LoadedPreferenceLabels(preferences=preferences, summary=summary)


def load_latest_cluster_labels(
    *,
    labels_dir: Path,
    cluster_labels_filename: str,
) -> Dict[str, ClusterLabelRecord]:
    """Load the latest saved cluster-label record for each cluster ID."""

    cluster_path = labels_dir / cluster_labels_filename
    if not cluster_path.exists():
        LOGGER.info("Cluster labels not found at %s; returning no label annotations.", cluster_path)
        return {}

    latest_cluster_by_id: Dict[str, Dict[str, Any]] = {}
    for record in _load_records_file(cluster_path):
        cluster_id = str(record.get("cluster_id", "")).strip()
        if not cluster_id:
            continue
        latest_cluster_by_id[cluster_id] = record

    loaded: Dict[str, ClusterLabelRecord] = {}
    for cluster_id, record in latest_cluster_by_id.items():
        loaded[cluster_id] = ClusterLabelRecord(
            cluster_id=cluster_id,
            best_image_ids=tuple(_unique_nonempty_strings(record.get("best_image_ids", []))),
            acceptable_image_ids=tuple(
                _unique_nonempty_strings(record.get("acceptable_image_ids", []))
            ),
            reject_image_ids=tuple(_unique_nonempty_strings(record.get("reject_image_ids", []))),
            timestamp=str(record.get("timestamp", "")).strip(),
            annotator_id=_optional_text(record.get("annotator_id")),
        )
    return loaded


def save_training_history_csv(path: Path, rows: Iterable[Dict[str, Any]]) -> None:
    """Save per-epoch training history rows to CSV."""

    materialized = list(rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "epoch",
        "train_loss",
        "validation_loss",
        "validation_pairwise_accuracy",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in materialized:
            writer.writerow({field: row.get(field) for field in fieldnames})


def save_ranked_clusters_csv(path: Path, rows: Iterable[Dict[str, Any]]) -> None:
    """Save scored cluster rankings to CSV."""

    materialized = list(rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "cluster_id",
        "cluster_size",
        "rank_in_cluster",
        "image_id",
        "score",
        "file_path",
        "relative_path",
        "file_name",
        "capture_timestamp",
        "capture_time_source",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in materialized:
            writer.writerow({field: row.get(field) for field in fieldnames})


def save_ranking_summary_json(path: Path, payload: Dict[str, Any]) -> None:
    """Save a JSON summary for training or scoring outputs."""

    save_json(path, payload)


def _load_cluster_rows_by_id(
    *,
    clusters_path: Path,
    embedded_rows: List[Dict[str, str]],
) -> Dict[str, Dict[str, str]]:
    """Load cluster rows or synthesize singleton clusters when absent."""

    if clusters_path.exists():
        cluster_rows = _load_csv_rows(clusters_path)
        cluster_rows_by_id = {row["image_id"]: row for row in cluster_rows}
        missing = [row["image_id"] for row in embedded_rows if row["image_id"] not in cluster_rows_by_id]
        if missing:
            raise ValueError(
                "clusters.csv is missing image IDs found in the embedded metadata rows. "
                f"Examples: {missing[:5]}"
            )
        return cluster_rows_by_id

    LOGGER.warning("Cluster file not found at %s; synthesizing singleton clusters.", clusters_path)
    synthetic: Dict[str, Dict[str, str]] = {}
    for row in embedded_rows:
        embedding_index = int(row["embedding_index"])
        synthetic[row["image_id"]] = {
            "image_id": row["image_id"],
            "cluster_id": f"cluster_{embedding_index:04d}",
            "cluster_size": "1",
            "cluster_position": "0",
            "cluster_reason": "synthetic_singleton",
            "file_path": row["file_path"],
            "relative_path": row.get("relative_path", ""),
            "file_name": row["file_name"],
            "capture_timestamp": row.get("capture_timestamp", ""),
            "capture_time_source": row.get("capture_time_source", ""),
        }
    return synthetic


def _extract_embedded_rows(rows: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """Return only rows aligned with embeddings, sorted by embedding index."""

    embedded_rows = [
        row for row in rows if str(row.get("embedding_index", "")).strip()
    ]
    return sorted(embedded_rows, key=lambda row: int(row["embedding_index"]))


def _pairwise_record_to_preference(
    record: Dict[str, Any],
    *,
    ranking_artifacts: RankingArtifacts,
    skip_ties: bool,
    summary: Dict[str, Any],
) -> Optional[PairwisePreferenceRecord]:
    """Convert one pairwise label record into a training preference."""

    decision = str(record.get("decision", "")).strip().lower()
    image_a_id = str(record.get("image_a_id", "")).strip()
    image_b_id = str(record.get("image_b_id", "")).strip()
    preferred_image_id = str(record.get("preferred_image_id", "") or "").strip()

    if decision == "tie":
        summary["tie_records_skipped"] += 1
        return None
    if decision == "skip":
        summary["skip_records_skipped"] += 1
        return None
    if decision not in {"left_better", "right_better"} and not preferred_image_id:
        summary["invalid_records_skipped"] += 1
        return None

    if not preferred_image_id:
        preferred_image_id = image_a_id if decision == "left_better" else image_b_id

    if preferred_image_id == image_a_id:
        other_image_id = image_b_id
    elif preferred_image_id == image_b_id:
        other_image_id = image_a_id
    else:
        summary["invalid_records_skipped"] += 1
        return None

    preferred = ranking_artifacts.images_by_id.get(preferred_image_id)
    other = ranking_artifacts.images_by_id.get(other_image_id)
    if preferred is None or other is None:
        summary["missing_images_skipped"] += 1
        return None

    return PairwisePreferenceRecord(
        preferred_image_id=preferred_image_id,
        other_image_id=other_image_id,
        preferred_index=preferred.embedding_index,
        other_index=other.embedding_index,
        source_mode=str(record.get("source_mode", "pairwise_label")),
        cluster_id=_optional_text(record.get("cluster_id")),
        label_origin="pairwise_label",
    )


def _cluster_record_to_preferences(
    record: Dict[str, Any],
    *,
    ranking_artifacts: RankingArtifacts,
    summary: Dict[str, Any],
) -> List[PairwisePreferenceRecord]:
    """Convert one cluster culling record into pairwise training preferences."""

    cluster_id = str(record.get("cluster_id", "")).strip()
    best_ids = _unique_nonempty_strings(record.get("best_image_ids", []))
    acceptable_ids = _unique_nonempty_strings(record.get("acceptable_image_ids", []))
    reject_ids = _unique_nonempty_strings(record.get("reject_image_ids", []))

    preferences: List[PairwisePreferenceRecord] = []
    preferences.extend(
        _preferences_from_groups(
            best_ids,
            acceptable_ids + reject_ids,
            ranking_artifacts=ranking_artifacts,
            cluster_id=cluster_id,
            summary=summary,
        )
    )
    preferences.extend(
        _preferences_from_groups(
            acceptable_ids,
            reject_ids,
            ranking_artifacts=ranking_artifacts,
            cluster_id=cluster_id,
            summary=summary,
        )
    )
    return preferences


def _preferences_from_groups(
    preferred_ids: List[str],
    other_ids: List[str],
    *,
    ranking_artifacts: RankingArtifacts,
    cluster_id: str,
    summary: Dict[str, Any],
) -> List[PairwisePreferenceRecord]:
    """Generate all preferred-over-other pairs between two labeled groups."""

    preferences: List[PairwisePreferenceRecord] = []
    for preferred_image_id in preferred_ids:
        preferred = ranking_artifacts.images_by_id.get(preferred_image_id)
        if preferred is None:
            summary["missing_images_skipped"] += 1
            continue
        for other_image_id in other_ids:
            if other_image_id == preferred_image_id:
                continue
            other = ranking_artifacts.images_by_id.get(other_image_id)
            if other is None:
                summary["missing_images_skipped"] += 1
                continue
            preferences.append(
                PairwisePreferenceRecord(
                    preferred_image_id=preferred_image_id,
                    other_image_id=other_image_id,
                    preferred_index=preferred.embedding_index,
                    other_index=other.embedding_index,
                    source_mode="cluster_label",
                    cluster_id=cluster_id,
                    label_origin="cluster_label",
                )
            )
    return preferences


def _load_csv_rows(path: Path) -> List[Dict[str, str]]:
    """Load CSV rows into dictionaries."""

    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _load_records_file(path: Path) -> List[Dict[str, Any]]:
    """Load a JSON or JSONL records file."""

    payload = _load_json_records(path)
    if path.suffix.lower() == ".jsonl":
        if not isinstance(payload, list):
            raise ValueError(f"Expected JSONL list semantics from {path}.")
        return payload

    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        return [payload]
    raise ValueError(f"Unsupported JSON payload in {path}.")


def _load_json_records(path: Path) -> Any:
    """Load JSON or JSONL content from disk."""

    suffix = path.suffix.lower()
    if suffix == ".jsonl":
        records: List[Dict[str, Any]] = []
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                text = line.strip()
                if not text:
                    continue
                records.append(json.loads(text))
        return records

    return json.loads(path.read_text(encoding="utf-8"))


def _count_by_field(
    preferences: Iterable[PairwisePreferenceRecord],
    field_name: str,
) -> Dict[str, int]:
    """Count preference records by a dataclass field."""

    counts: Dict[str, int] = {}
    for preference in preferences:
        value = str(getattr(preference, field_name))
        counts[value] = counts.get(value, 0) + 1
    return counts


def _optional_text(value: Any) -> Optional[str]:
    """Normalize optional string values."""

    text = str(value or "").strip()
    return text or None


def _unique_nonempty_strings(value: Any) -> List[str]:
    """Normalize a JSON list field into unique non-empty strings."""

    if not isinstance(value, list):
        return []

    ordered: List[str] = []
    seen: set[str] = set()
    for item in value:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        ordered.append(text)
    return ordered
