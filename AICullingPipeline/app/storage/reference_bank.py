"""Artifact helpers for Week 6 exemplar-conditioned reference banks."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List

import numpy as np

from app.utils.io_utils import save_json


REFERENCE_BUCKETS = ("terrible", "bad", "okay", "good", "great")


@dataclass
class ReferenceImageArtifact:
    """One reference exemplar image aligned with a saved reference-bank embedding row."""

    bucket: str
    image_id: str
    embedding_index: int
    file_path: str
    relative_path: str
    file_name: str
    width: int | None
    height: int | None
    status: str
    error: str


@dataclass
class ReferenceBankArtifacts:
    """Normalized exemplar embeddings grouped by reference bucket."""

    bucket_order: tuple[str, ...]
    bucket_embeddings: Dict[str, np.ndarray]
    bucket_image_ids: Dict[str, tuple[str, ...]]
    bucket_file_paths: Dict[str, tuple[str, ...]]
    ordered_images: List[ReferenceImageArtifact]
    feature_dim: int

    def bucket_counts(self) -> Dict[str, int]:
        """Return one exemplar count per bucket."""

        return {
            bucket: int(self.bucket_embeddings.get(bucket, np.empty((0, self.feature_dim))).shape[0])
            for bucket in self.bucket_order
        }


def save_reference_bank_npz(
    path: Path,
    reference_bank: ReferenceBankArtifacts,
) -> None:
    """Persist a normalized reference bank to a compressed NPZ artifact."""

    path.parent.mkdir(parents=True, exist_ok=True)
    payload: Dict[str, Any] = {
        "bucket_order": np.asarray(reference_bank.bucket_order, dtype=str),
        "feature_dim": np.asarray([reference_bank.feature_dim], dtype=np.int32),
    }
    for bucket in reference_bank.bucket_order:
        safe_bucket = bucket.lower()
        payload[f"embeddings__{safe_bucket}"] = np.asarray(
            reference_bank.bucket_embeddings.get(bucket, np.empty((0, reference_bank.feature_dim))),
            dtype=np.float32,
        )
        payload[f"image_ids__{safe_bucket}"] = np.asarray(
            reference_bank.bucket_image_ids.get(bucket, ()),
            dtype=str,
        )
        payload[f"file_paths__{safe_bucket}"] = np.asarray(
            reference_bank.bucket_file_paths.get(bucket, ()),
            dtype=str,
        )
    np.savez_compressed(path, **payload)


def load_reference_bank(path: Path) -> ReferenceBankArtifacts:
    """Load a saved reference bank artifact from disk."""

    if not path.exists():
        raise FileNotFoundError(f"Reference bank not found: {path}")

    with np.load(path, allow_pickle=False) as payload:
        bucket_order = tuple(str(value) for value in payload["bucket_order"].tolist())
        feature_dim = int(payload["feature_dim"][0]) if "feature_dim" in payload else 0
        bucket_embeddings: Dict[str, np.ndarray] = {}
        bucket_image_ids: Dict[str, tuple[str, ...]] = {}
        bucket_file_paths: Dict[str, tuple[str, ...]] = {}
        for bucket in bucket_order:
            safe_bucket = bucket.lower()
            embeddings_key = f"embeddings__{safe_bucket}"
            image_ids_key = f"image_ids__{safe_bucket}"
            file_paths_key = f"file_paths__{safe_bucket}"
            bucket_embeddings[bucket] = np.asarray(
                payload[embeddings_key] if embeddings_key in payload.files else np.empty((0, feature_dim)),
                dtype=np.float32,
            )
            bucket_image_ids[bucket] = tuple(
                str(value)
                for value in (
                    payload[image_ids_key] if image_ids_key in payload.files else np.asarray([], dtype=str)
                ).tolist()
            )
            bucket_file_paths[bucket] = tuple(
                str(value)
                for value in (
                    payload[file_paths_key]
                    if file_paths_key in payload.files
                    else np.asarray([], dtype=str)
                ).tolist()
            )

    metadata_path = path.with_name("reference_images.csv")
    ordered_images = load_reference_metadata_csv(metadata_path) if metadata_path.exists() else []
    if feature_dim <= 0 and bucket_embeddings:
        first_nonempty = next(
            (embeddings for embeddings in bucket_embeddings.values() if embeddings.size),
            np.empty((0, 0), dtype=np.float32),
        )
        feature_dim = int(first_nonempty.shape[1]) if first_nonempty.ndim == 2 else 0

    return ReferenceBankArtifacts(
        bucket_order=bucket_order,
        bucket_embeddings=bucket_embeddings,
        bucket_image_ids=bucket_image_ids,
        bucket_file_paths=bucket_file_paths,
        ordered_images=ordered_images,
        feature_dim=feature_dim,
    )


def save_reference_metadata_csv(path: Path, records: Iterable[ReferenceImageArtifact]) -> None:
    """Save reference exemplar metadata rows to CSV."""

    materialized = list(records)
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "bucket",
        "image_id",
        "embedding_index",
        "file_path",
        "relative_path",
        "file_name",
        "width",
        "height",
        "status",
        "error",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for record in materialized:
            writer.writerow(
                {
                    "bucket": record.bucket,
                    "image_id": record.image_id,
                    "embedding_index": record.embedding_index,
                    "file_path": record.file_path,
                    "relative_path": record.relative_path,
                    "file_name": record.file_name,
                    "width": record.width,
                    "height": record.height,
                    "status": record.status,
                    "error": record.error,
                }
            )


def load_reference_metadata_csv(path: Path) -> List[ReferenceImageArtifact]:
    """Load saved reference exemplar metadata rows from CSV."""

    with path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    artifacts: List[ReferenceImageArtifact] = []
    for row in rows:
        artifacts.append(
            ReferenceImageArtifact(
                bucket=str(row.get("bucket", "")).strip().lower(),
                image_id=str(row.get("image_id", "")).strip(),
                embedding_index=int(row.get("embedding_index", 0)),
                file_path=str(row.get("file_path", "")).strip(),
                relative_path=str(row.get("relative_path", "")).strip(),
                file_name=str(row.get("file_name", "")).strip(),
                width=_optional_int(row.get("width")),
                height=_optional_int(row.get("height")),
                status=str(row.get("status", "")).strip(),
                error=str(row.get("error", "")).strip(),
            )
        )
    return artifacts


def save_reference_summary_json(path: Path, payload: Dict[str, Any]) -> None:
    """Persist a JSON summary for one reference bank build."""

    save_json(path, payload)


def _optional_int(value: Any) -> int | None:
    """Convert optional CSV integers into Python integers when present."""

    text = str(value or "").strip()
    if not text:
        return None
    return int(text)
