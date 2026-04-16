"""Dataset and collation utilities for batched image embedding."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PIL import Image
import torch
from torch.utils.data import Dataset

from app.data.image_scanner import ImageRecord


class ImageDataset(Dataset[dict[str, Any]]):
    """Dataset that loads validated image records for inference."""

    def __init__(self, records: list[ImageRecord], transform: Any) -> None:
        self.records = records
        self.transform = transform

    def __len__(self) -> int:
        return len(self.records)

    def __getitem__(self, index: int) -> dict[str, Any]:
        record = self.records[index]
        path = Path(record.file_path)

        try:
            with Image.open(path) as image:
                rgb_image = image.convert("RGB")
            tensor = self.transform(rgb_image)
            return {"pixel_values": tensor, "record_index": index, "error": None}
        except (OSError, ValueError) as exc:
            return {
                "pixel_values": None,
                "record_index": index,
                "error": str(exc),
            }


def collate_image_batch(samples: list[dict[str, Any]]) -> dict[str, Any]:
    """Collate a batch while retaining per-item load failures."""

    pixel_values: list[torch.Tensor] = []
    record_indices: list[int] = []
    failures: list[dict[str, Any]] = []

    for sample in samples:
        if sample["pixel_values"] is None:
            failures.append(
                {
                    "record_index": sample["record_index"],
                    "error": sample["error"],
                }
            )
            continue

        pixel_values.append(sample["pixel_values"])
        record_indices.append(sample["record_index"])

    batch = torch.stack(pixel_values) if pixel_values else None
    return {
        "pixel_values": batch,
        "record_indices": record_indices,
        "failures": failures,
    }
