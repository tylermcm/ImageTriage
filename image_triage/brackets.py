from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from .metadata import CaptureMetadata, EMPTY_METADATA, load_capture_metadata
from .models import ImageRecord


SUPPORTED_BRACKET_SIZES = (2, 3, 5, 7, 9)


@dataclass(slots=True, frozen=True)
class BracketGroup:
    start_index: int
    size: int

    @property
    def end_index(self) -> int:
        return self.start_index + self.size


class BracketDetector:
    def __init__(self) -> None:
        self._cache: dict[str, CaptureMetadata] = {}

    def group_for(self, records: list[ImageRecord], index: int) -> BracketGroup | None:
        if not 0 <= index < len(records):
            return None
        anchor_metadata = self._metadata_for(records[index])
        if anchor_metadata is EMPTY_METADATA:
            return None

        start = index
        while start > 0 and self._compatible(records[start - 1], records[index]):
            start -= 1
            if index - start >= 8:
                break

        end = index
        while end + 1 < len(records) and self._compatible(records[end + 1], records[index]):
            end += 1
            if end - start >= 8:
                break

        candidate_size = end - start + 1
        if candidate_size < 2:
            return None
        if not self._looks_like_bracket(records[start : end + 1]):
            return None

        size = self._supported_size(candidate_size)
        if size is None:
            return None
        if candidate_size != size:
            start = max(start, index - (size - 1))
            if start + size > len(records):
                start = len(records) - size
            end = start + size - 1
            if not self._looks_like_bracket(records[start : end + 1]):
                return None
        return BracketGroup(start_index=start, size=size)

    def _compatible(self, candidate: ImageRecord, anchor: ImageRecord) -> bool:
        candidate_metadata = self._metadata_for(candidate)
        anchor_metadata = self._metadata_for(anchor)
        if candidate_metadata is EMPTY_METADATA or anchor_metadata is EMPTY_METADATA:
            return False

        if candidate_metadata.captured_at_value is None or anchor_metadata.captured_at_value is None:
            return False
        delta = abs(candidate_metadata.captured_at_value - anchor_metadata.captured_at_value)
        if delta > timedelta(seconds=2.0):
            return False

        if candidate_metadata.lens and anchor_metadata.lens and candidate_metadata.lens != anchor_metadata.lens:
            return False
        if not _close(candidate_metadata.focal_length_value, anchor_metadata.focal_length_value, tolerance=1.5):
            return False
        if not _close(candidate_metadata.aperture_value, anchor_metadata.aperture_value, tolerance=0.3):
            return False
        if not _close(candidate_metadata.iso_value, anchor_metadata.iso_value, tolerance=5.0):
            return False
        return True

    def _looks_like_bracket(self, records: list[ImageRecord]) -> bool:
        if len(records) < 2:
            return False
        metadata_items = [self._metadata_for(record) for record in records]
        exposures = [
            round(metadata.exposure_seconds, 6)
            for metadata in metadata_items
            if metadata.exposure_seconds is not None
        ]
        if len(set(exposures)) < 2:
            return False

        timestamps = [metadata.captured_at_value for metadata in metadata_items if metadata.captured_at_value is not None]
        if len(timestamps) < len(records):
            return False
        if max(timestamps) - min(timestamps) > timedelta(seconds=2.0):
            return False
        return True

    def _supported_size(self, candidate_size: int) -> int | None:
        supported = [size for size in SUPPORTED_BRACKET_SIZES if size <= candidate_size]
        if candidate_size in SUPPORTED_BRACKET_SIZES:
            return candidate_size
        if candidate_size >= 9:
            return 9
        return supported[-1] if supported and supported[-1] >= 2 else None

    def _metadata_for(self, record: ImageRecord) -> CaptureMetadata:
        cached = self._cache.get(record.path)
        if cached is not None:
            return cached
        metadata = load_capture_metadata(record.path)
        self._cache[record.path] = metadata
        return metadata


def _close(left: float | None, right: float | None, *, tolerance: float) -> bool:
    if left is None or right is None:
        return True
    return abs(left - right) <= tolerance
