"""Time-aware candidate window construction for culling-oriented clustering."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import re
from typing import Dict, List, Optional, Sequence, Tuple

from app.clustering.artifacts import EmbeddedImageRecord


@dataclass
class CandidateWindow:
    """A restricted comparison window used for culling-oriented grouping."""

    window_id: str
    member_indices: List[int]
    window_kind: str


def build_candidate_windows(
    records: Sequence[EmbeddedImageRecord],
    *,
    max_time_gap_seconds: float,
    time_filter_required: bool,
    timestamp_fallback_mode: str,
    filename_order_window: int,
) -> List[CandidateWindow]:
    """Build candidate windows based on capture time and optional weak fallbacks."""

    timestamped_indices = [
        index for index, record in enumerate(records) if record.capture_datetime is not None
    ]
    missing_indices = [
        index for index, record in enumerate(records) if record.capture_datetime is None
    ]

    windows: List[CandidateWindow] = []
    windows.extend(
        _build_timestamp_windows(records, timestamped_indices, max_time_gap_seconds)
    )

    if missing_indices:
        if timestamp_fallback_mode == "filename_order" and not time_filter_required:
            windows.extend(
                _build_filename_windows(records, missing_indices, filename_order_window)
            )
        else:
            windows.extend(
                CandidateWindow(
                    window_id=f"missing_{index:04d}",
                    member_indices=[record_index],
                    window_kind="missing_timestamp_singleton",
                )
                for index, record_index in enumerate(sorted(missing_indices))
            )

    return sorted(windows, key=lambda window: _window_sort_key(window, records))


def pair_allowed(
    left: EmbeddedImageRecord,
    right: EmbeddedImageRecord,
    *,
    max_time_gap_seconds: float,
    time_filter_required: bool,
    timestamp_fallback_mode: str,
    filename_order_window: int,
) -> bool:
    """Decide whether a pair of images is eligible for visual comparison."""

    if left.capture_datetime is not None and right.capture_datetime is not None:
        delta = abs((left.capture_datetime - right.capture_datetime).total_seconds())
        return delta <= max_time_gap_seconds

    if time_filter_required:
        return False

    if timestamp_fallback_mode != "filename_order":
        return False

    left_order = left.filename_sequence_number
    right_order = right.filename_sequence_number
    if left_order is not None and right_order is not None:
        return abs(left_order - right_order) <= filename_order_window

    return abs(left.embedding_index - right.embedding_index) <= filename_order_window


def describe_time_window(
    records: Sequence[EmbeddedImageRecord],
    member_indices: Sequence[int],
) -> Tuple[str, Optional[float]]:
    """Describe the time span covered by a set of records."""

    datetimes = [
        records[index].capture_datetime
        for index in member_indices
        if records[index].capture_datetime is not None
    ]
    if not datetimes:
        return "timestamps unavailable", None

    start = min(datetimes)
    end = max(datetimes)
    span_seconds = float((end - start).total_seconds())
    return (
        f"{start.strftime('%Y-%m-%d %H:%M:%S')} -> {end.strftime('%Y-%m-%d %H:%M:%S')}",
        span_seconds,
    )


def _build_timestamp_windows(
    records: Sequence[EmbeddedImageRecord],
    indices: Sequence[int],
    max_time_gap_seconds: float,
) -> List[CandidateWindow]:
    """Create windows from timestamped records using adjacent time proximity."""

    ordered_indices = sorted(
        indices,
        key=lambda index: (
            records[index].capture_datetime or datetime.min,
            records[index].embedding_index,
        ),
    )
    if not ordered_indices:
        return []

    windows: List[CandidateWindow] = []
    current_members: List[int] = [ordered_indices[0]]
    previous_time = records[ordered_indices[0]].capture_datetime

    for record_index in ordered_indices[1:]:
        current_time = records[record_index].capture_datetime
        gap_seconds = abs((current_time - previous_time).total_seconds())
        if gap_seconds <= max_time_gap_seconds:
            current_members.append(record_index)
        else:
            windows.append(
                CandidateWindow(
                    window_id=f"time_{len(windows):04d}",
                    member_indices=sorted(current_members),
                    window_kind="capture_time",
                )
            )
            current_members = [record_index]

        previous_time = current_time

    windows.append(
        CandidateWindow(
            window_id=f"time_{len(windows):04d}",
            member_indices=sorted(current_members),
            window_kind="capture_time",
        )
    )
    return windows


def _build_filename_windows(
    records: Sequence[EmbeddedImageRecord],
    indices: Sequence[int],
    filename_order_window: int,
) -> List[CandidateWindow]:
    """Create weak fallback windows from filename ordering when timestamps are missing."""

    ordered_indices = sorted(
        indices,
        key=lambda index: (
            records[index].filename_sequence_number
            if records[index].filename_sequence_number is not None
            else records[index].embedding_index,
            records[index].file_name.casefold(),
            records[index].embedding_index,
        ),
    )
    if not ordered_indices:
        return []

    windows: List[CandidateWindow] = []
    current_members: List[int] = [ordered_indices[0]]
    previous_record = records[ordered_indices[0]]

    for record_index in ordered_indices[1:]:
        current_record = records[record_index]
        if _filename_gap(previous_record, current_record) <= filename_order_window:
            current_members.append(record_index)
        else:
            windows.append(
                CandidateWindow(
                    window_id=f"filename_{len(windows):04d}",
                    member_indices=sorted(current_members),
                    window_kind="filename_order",
                )
            )
            current_members = [record_index]

        previous_record = current_record

    windows.append(
        CandidateWindow(
            window_id=f"filename_{len(windows):04d}",
            member_indices=sorted(current_members),
            window_kind="filename_order",
        )
    )
    return windows


def _filename_gap(left: EmbeddedImageRecord, right: EmbeddedImageRecord) -> int:
    """Estimate the sequencing gap between two records based on filename order."""

    if (
        left.filename_sequence_number is not None
        and right.filename_sequence_number is not None
    ):
        return abs(left.filename_sequence_number - right.filename_sequence_number)

    return abs(left.embedding_index - right.embedding_index)


def _window_sort_key(
    window: CandidateWindow,
    records: Sequence[EmbeddedImageRecord],
) -> Tuple[int, int, int]:
    """Sort windows deterministically for stable cluster IDs."""

    first_member = min(window.member_indices)
    first_record = records[first_member]
    priority = 0 if window.window_kind == "capture_time" else 1
    return (
        priority,
        first_record.embedding_index,
        first_member,
    )


def extract_filename_sequence_number(file_name: str) -> Optional[int]:
    """Extract a trailing numeric token from a filename when available."""

    stem = file_name.rsplit(".", 1)[0]
    matches = re.findall(r"(\d+)", stem)
    if not matches:
        return None
    return int(matches[-1])
