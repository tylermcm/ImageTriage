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
        compute_technical: bool = False,
        technical_max_side: int = 768,
    ) -> None:
        self.records = records
        self.transform = transform
        self.collect_timings = collect_timings
        self.target_short_edge = max(1, int(target_short_edge or 224))
        # Fusion: derive the deterministic technical signals from the same decode
        # instead of re-reading every file in the signals stage.
        self.compute_technical = bool(compute_technical)
        self.technical_max_side = max(64, int(technical_max_side or 768))

    def __len__(self) -> int:
        return len(self.records)

    def __getitem__(self, index: int) -> dict[str, Any]:
        record = self.records[index]
        path = Path(record.file_path)

        try:
            total_start = time.perf_counter() if self.collect_timings else 0.0
            total_cpu_start = time.thread_time() if self.collect_timings else 0.0
            load_start = total_start
            io_profile: dict[str, Any] | None = {} if self.collect_timings else None
            rgb_image = load_rgb_for_inference(
                path,
                target_short_edge=self.target_short_edge,
                profile=io_profile,
            )
            load_ms = (time.perf_counter() - load_start) * 1000.0 if self.collect_timings else 0.0
            technical = None
            technical_ms = 0.0
            technical_cpu_ms = 0.0
            try:
                transform_start = time.perf_counter() if self.collect_timings else 0.0
                transform_cpu_start = time.thread_time() if self.collect_timings else 0.0
                tensor = self.transform(rgb_image)
                transform_ms = (time.perf_counter() - transform_start) * 1000.0 if self.collect_timings else 0.0
                transform_cpu_ms = (
                    (time.thread_time() - transform_cpu_start) * 1000.0
                    if self.collect_timings
                    else 0.0
                )
                # Derive technical signals from this same decode. Failure here must
                # never break extraction — fall through with technical=None and the
                # signals stage will compute it the old way.
                if self.compute_technical:
                    try:
                        from app.engine.signals.technical import technical_signals_from_image

                        technical_start = time.perf_counter() if self.collect_timings else 0.0
                        technical_cpu_start = time.thread_time() if self.collect_timings else 0.0
                        technical = technical_signals_from_image(
                            rgb_image, max_side=self.technical_max_side, apply_exif=False
                        )
                        technical_ms = (
                            (time.perf_counter() - technical_start) * 1000.0
                            if self.collect_timings
                            else 0.0
                        )
                        technical_cpu_ms = (
                            (time.thread_time() - technical_cpu_start) * 1000.0
                            if self.collect_timings
                            else 0.0
                        )
                    except Exception:
                        technical = None
            finally:
                rgb_image.close()
            total_ms = (time.perf_counter() - total_start) * 1000.0 if self.collect_timings else 0.0
            total_cpu_ms = (
                (time.thread_time() - total_cpu_start) * 1000.0
                if self.collect_timings
                else 0.0
            )
            return {
                "pixel_values": tensor,
                "record_index": index,
                "error": None,
                "technical": (record.file_path, technical) if technical is not None else None,
                "timing": {
                    "load_ms": load_ms,
                    "transform_ms": transform_ms,
                    "transform_cpu_ms": transform_cpu_ms,
                    "technical_ms": technical_ms,
                    "technical_cpu_ms": technical_cpu_ms,
                    "total_ms": total_ms,
                    "total_cpu_ms": total_cpu_ms,
                    "file_name": record.file_name,
                    "io": io_profile or {},
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
                    "transform_cpu_ms": 0.0,
                    "technical_ms": 0.0,
                    "technical_cpu_ms": 0.0,
                    "total_ms": total_ms,
                    "total_cpu_ms": 0.0,
                    "file_name": record.file_name,
                    "io": locals().get("io_profile") or {},
                },
            }


def collate_image_batch(samples: list[dict[str, Any]]) -> dict[str, Any]:
    """Collate a batch while retaining per-item load failures."""

    pixel_values: list[torch.Tensor] = []
    record_indices: list[int] = []
    failures: list[dict[str, Any]] = []
    timings: list[dict[str, Any]] = []
    technical: list[Any] = []

    for sample in samples:
        timing = sample.get("timing")
        if isinstance(timing, dict):
            timings.append(timing)
        tech = sample.get("technical")
        if tech is not None:
            technical.append(tech)
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
    io_summary = _collate_io_profiles(
        [timing.get("io") for timing in timings if isinstance(timing.get("io"), dict)]
    )
    return {
        "pixel_values": batch,
        "record_indices": record_indices,
        "failures": failures,
        "technical": technical,
        "timings": {
            "count": len(timings),
            "load_ms": sum(float(timing.get("load_ms") or 0.0) for timing in timings),
            "transform_ms": sum(float(timing.get("transform_ms") or 0.0) for timing in timings),
            "transform_cpu_ms": sum(float(timing.get("transform_cpu_ms") or 0.0) for timing in timings),
            "technical_ms": sum(float(timing.get("technical_ms") or 0.0) for timing in timings),
            "technical_cpu_ms": sum(float(timing.get("technical_cpu_ms") or 0.0) for timing in timings),
            "total_ms": sum(total_ms_values),
            "total_cpu_ms": sum(float(timing.get("total_cpu_ms") or 0.0) for timing in timings),
            "max_ms": max(total_ms_values) if total_ms_values else 0.0,
            "max_file": str(max_timing.get("file_name") or ""),
            "io": io_summary,
        },
    }


def _collate_io_profiles(profiles: list[dict[str, Any]]) -> dict[str, Any]:
    profiles = [profile for profile in profiles if profile]
    if not profiles:
        return {}
    totals: dict[str, float] = {}
    formats: dict[str, int] = {}
    frame_counts: dict[str, int] = {}
    worker_pids: set[int] = set()
    for profile in profiles:
        image_format = str(profile.get("image_format") or "unknown")
        formats[image_format] = formats.get(image_format, 0) + 1
        frame_count = str(int(profile.get("frame_count") or 1))
        frame_counts[frame_count] = frame_counts.get(frame_count, 0) + 1
        worker_pid = int(profile.get("worker_pid") or 0)
        if worker_pid:
            worker_pids.add(worker_pid)
        for key, value in profile.items():
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                continue
            if key in {
                "worker_pid",
                "thread_id",
                "frame_count",
                "source_width",
                "source_height",
                "draft_width",
                "draft_height",
                "output_width",
                "output_height",
                "decoder_read_block_bytes",
            }:
                continue
            totals[key] = totals.get(key, 0.0) + float(value)
    return {
        "samples": len(profiles),
        "worker_pids": sorted(worker_pids),
        "formats": formats,
        "frame_counts": frame_counts,
        **totals,
    }
