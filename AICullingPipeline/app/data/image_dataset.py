"""Dataset and collation utilities for batched image embedding."""

from __future__ import annotations

from pathlib import Path
import time
from typing import Any

import torch
from torch.utils.data import Dataset

from app.data.image_loading import load_rgb_for_inference
from app.data.image_scanner import ImageRecord


class ImageDataset(Dataset[dict[str, Any]]):
    """Dataset that loads validated image records for inference."""

    def __init__(
        self,
        records: list[ImageRecord],
        transform: Any,
        *,
        collect_timings: bool = False,
        target_short_edge: int = 224,
    ) -> None:
        self.records = records
        self.transform = transform
        self.collect_timings = collect_timings
        self.target_short_edge = max(1, int(target_short_edge or 224))

    def __len__(self) -> int:
        return len(self.records)

    def __getitem__(self, index: int) -> dict[str, Any]:
        record = self.records[index]
        path = Path(record.file_path)

        try:
            total_start = time.perf_counter() if self.collect_timings else 0.0
            load_start = total_start
            rgb_image = load_rgb_for_inference(path, target_short_edge=self.target_short_edge)
            load_ms = (time.perf_counter() - load_start) * 1000.0 if self.collect_timings else 0.0
            try:
                transform_start = time.perf_counter() if self.collect_timings else 0.0
                tensor = self.transform(rgb_image)
                transform_ms = (time.perf_counter() - transform_start) * 1000.0 if self.collect_timings else 0.0
            finally:
                rgb_image.close()
            total_ms = (time.perf_counter() - total_start) * 1000.0 if self.collect_timings else 0.0
            return {
                "pixel_values": tensor,
                "record_index": index,
                "error": None,
                "timing": {
                    "load_ms": load_ms,
                    "transform_ms": transform_ms,
                    "total_ms": total_ms,
                    "file_name": record.file_name,
                },
            }
        except (OSError, ValueError) as exc:
            total_ms = (time.perf_counter() - total_start) * 1000.0 if self.collect_timings else 0.0
            return {
                "pixel_values": None,
                "record_index": index,
                "error": str(exc),
                "timing": {
                    "load_ms": 0.0,
                    "transform_ms": 0.0,
                    "total_ms": total_ms,
                    "file_name": record.file_name,
                },
            }


def collate_image_batch(samples: list[dict[str, Any]]) -> dict[str, Any]:
    """Collate a batch while retaining per-item load failures."""

    pixel_values: list[torch.Tensor] = []
    record_indices: list[int] = []
    failures: list[dict[str, Any]] = []
    timings: list[dict[str, Any]] = []

    for sample in samples:
        timing = sample.get("timing")
        if isinstance(timing, dict):
            timings.append(timing)
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
    total_ms_values = [float(timing.get("total_ms") or 0.0) for timing in timings]
    max_timing = timings[total_ms_values.index(max(total_ms_values))] if total_ms_values else {}
    return {
        "pixel_values": batch,
        "record_indices": record_indices,
        "failures": failures,
        "timings": {
            "count": len(timings),
            "load_ms": sum(float(timing.get("load_ms") or 0.0) for timing in timings),
            "transform_ms": sum(float(timing.get("transform_ms") or 0.0) for timing in timings),
            "total_ms": sum(total_ms_values),
            "max_ms": max(total_ms_values) if total_ms_values else 0.0,
            "max_file": str(max_timing.get("file_name") or ""),
        },
    }
