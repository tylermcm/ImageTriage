from __future__ import annotations

from datetime import timedelta

from .formats import FITS_SUFFIXES, suffix_for_path
from .metadata import CaptureMetadata, EMPTY_METADATA
from .models import ImageRecord


def burst_candidate_indices(
    records: list[ImageRecord],
    metadata_by_path: dict[str, CaptureMetadata],
    *,
    start_index: int,
    used: set[int],
    search_limit: int = 8,
) -> list[int]:
    if _record_uses_fits(records[start_index]):
        return [start_index]
    anchor_metadata = metadata_by_path.get(records[start_index].path, EMPTY_METADATA)
    if anchor_metadata is EMPTY_METADATA or anchor_metadata.captured_at_value is None:
        return [start_index]

    indices = [start_index]
    previous_metadata = anchor_metadata
    for candidate_index in range(start_index + 1, min(len(records), start_index + search_limit)):
        if candidate_index in used:
            break
        if _record_uses_fits(records[candidate_index]):
            break
        candidate_metadata = metadata_by_path.get(records[candidate_index].path, EMPTY_METADATA)
        if not is_burst_neighbor(anchor_metadata, previous_metadata, candidate_metadata):
            break
        indices.append(candidate_index)
        previous_metadata = candidate_metadata
    return indices


def find_burst_groups(
    records: list[ImageRecord],
    metadata_by_path: dict[str, CaptureMetadata],
) -> list[tuple[int, ...]]:
    if not records:
        return []

    groups: list[tuple[int, ...]] = []
    used: set[int] = set()
    for index in range(len(records)):
        if index in used:
            continue
        indices = burst_candidate_indices(records, metadata_by_path, start_index=index, used=used)
        used.update(indices)
        if len(indices) >= 2:
            groups.append(tuple(indices))
    return groups


def is_burst_neighbor(
    anchor_metadata: CaptureMetadata,
    previous_metadata: CaptureMetadata,
    candidate_metadata: CaptureMetadata,
) -> bool:
    if (
        anchor_metadata is EMPTY_METADATA
        or previous_metadata is EMPTY_METADATA
        or candidate_metadata is EMPTY_METADATA
        or anchor_metadata.captured_at_value is None
        or previous_metadata.captured_at_value is None
        or candidate_metadata.captured_at_value is None
    ):
        return False

    if abs(candidate_metadata.captured_at_value - anchor_metadata.captured_at_value) > timedelta(seconds=4.0):
        return False
    if abs(candidate_metadata.captured_at_value - previous_metadata.captured_at_value) > timedelta(seconds=2.5):
        return False
    if anchor_metadata.lens and candidate_metadata.lens and anchor_metadata.lens != candidate_metadata.lens:
        return False
    if not _close_numeric(anchor_metadata.focal_length_value, candidate_metadata.focal_length_value, tolerance=3.0):
        return False
    if not _close_numeric(anchor_metadata.aperture_value, candidate_metadata.aperture_value, tolerance=0.7):
        return False
    if not _close_numeric(anchor_metadata.iso_value, candidate_metadata.iso_value, tolerance=40.0):
        return False
    return True


def _close_numeric(left: float | None, right: float | None, *, tolerance: float) -> bool:
    if left is None or right is None:
        return True
    return abs(left - right) <= tolerance


def _record_uses_fits(record: ImageRecord) -> bool:
    return suffix_for_path(record.path) in FITS_SUFFIXES
