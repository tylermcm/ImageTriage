"""Helpers for reading and writing pipeline artifacts."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

import numpy as np

from app.config import ExtractionConfig
from app.data.image_scanner import ImageRecord


def save_metadata_csv(path: Path, records: list[ImageRecord]) -> None:
    """Save image metadata rows to CSV."""

    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "image_id",
        "file_path",
        "relative_path",
        "file_name",
        "width",
        "height",
        "capture_timestamp",
        "capture_time_source",
        "timestamp_available",
        "status",
        "error",
        "embedding_index",
    ]

    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for record in records:
            writer.writerow(
                {
                    "image_id": record.image_id,
                    "file_path": record.file_path,
                    "relative_path": record.relative_path,
                    "file_name": record.file_name,
                    "width": record.width,
                    "height": record.height,
                    "capture_timestamp": record.capture_timestamp,
                    "capture_time_source": record.capture_time_source,
                    "timestamp_available": record.timestamp_available,
                    "status": record.status,
                    "error": record.error,
                    "embedding_index": record.embedding_index,
                }
            )


def save_numpy_array(path: Path, array: np.ndarray) -> None:
    """Save a NumPy array to disk."""

    path.parent.mkdir(parents=True, exist_ok=True)
    np.save(path, array)


def save_json(path: Path, payload: Any) -> None:
    """Save JSON data to disk with consistent formatting."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def save_resolved_config(
    path: Path,
    config: ExtractionConfig,
    resolved_model_name: str,
) -> None:
    """Persist the effective runtime config for reproducibility."""

    payload = config.to_serializable_dict()
    payload["resolved_model_name"] = resolved_model_name
    save_json(path, payload)
