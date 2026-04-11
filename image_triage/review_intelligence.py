from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import timedelta
from hashlib import sha1
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QObject, QRunnable, QSize, Qt, Signal
from PySide6.QtGui import QImage

from .imaging import load_image_for_display
from .metadata import CaptureMetadata, EMPTY_METADATA, load_capture_metadata
from .models import ImageRecord
from .review_tools import build_inspection_stats
from .scanner import normalized_path_key

_FINGERPRINT_SIZE = QSize(96, 96)
_FALLBACK_MAX_NEIGHBORS = 10

_GROUP_PRIORITY = {
    "burst": 1,
    "similar": 2,
    "likely_duplicate": 3,
    "exact_duplicate": 4,
}

_GROUP_LABEL = {
    "burst": "Burst",
    "similar": "Similar",
    "likely_duplicate": "Near Dup",
    "exact_duplicate": "Exact Dup",
}


@dataclass(slots=True, frozen=True)
class ReviewInsight:
    path: str
    group_id: str = ""
    group_kind: str = ""
    group_label: str = ""
    group_size: int = 1
    rank_in_group: int = 1
    reasons: tuple[str, ...] = ()
    detail_score: float = 0.0
    exposure_score: float = 0.0

    @property
    def has_group(self) -> bool:
        return self.group_size > 1 and bool(self.group_id)

    @property
    def is_duplicate(self) -> bool:
        return self.group_kind in {"exact_duplicate", "likely_duplicate"}

    @property
    def is_exact_duplicate(self) -> bool:
        return self.group_kind == "exact_duplicate"

    @property
    def is_likely_duplicate(self) -> bool:
        return self.group_kind == "likely_duplicate"

    @property
    def is_similar_group(self) -> bool:
        return self.group_kind in {"similar", "burst"}

    @property
    def rank_text(self) -> str:
        if not self.has_group:
            return ""
        return f"{self.rank_in_group}/{self.group_size}"

    @property
    def summary_text(self) -> str:
        if not self.has_group:
            return ""
        return f"{self.group_label} {self.rank_text}"


@dataclass(slots=True, frozen=True)
class ReviewGroup:
    id: str
    kind: str
    label: str
    member_paths: tuple[str, ...]
    reasons: tuple[str, ...] = ()


@dataclass(slots=True, frozen=True)
class ReviewIntelligenceBundle:
    groups: tuple[ReviewGroup, ...]
    insights_by_path: dict[str, ReviewInsight]

    def insight_for_path(self, path: str) -> ReviewInsight | None:
        direct = self.insights_by_path.get(path)
        if direct is not None:
            return direct
        return self.insights_by_path.get(normalized_path_key(path))


@dataclass(slots=True, frozen=True)
class _RecordFingerprint:
    record: ImageRecord
    source_path: str
    metadata: CaptureMetadata
    dhash: int | None = None
    avg_luma: float = 0.0
    width: int = 0
    height: int = 0
    sha1_digest: str = ""
    detail_score: float = 0.0
    exposure_score: float = 0.0


class ReviewIntelligenceSignals(QObject):
    started = Signal(str, int, int)
    progress = Signal(str, int, int, int)
    finished = Signal(str, int, object)
    failed = Signal(str, int, str)
    cancelled = Signal(str, int)


class BuildReviewIntelligenceTask(QRunnable):
    def __init__(self, *, folder: str, token: int, records: tuple[ImageRecord, ...]) -> None:
        super().__init__()
        self.folder = folder
        self.token = token
        self.records = records
        self.signals = ReviewIntelligenceSignals()
        self._cancelled = False
        self.setAutoDelete(True)

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        total = len(self.records)
        self.signals.started.emit(self.folder, self.token, total)
        try:
            bundle = build_review_intelligence(
                list(self.records),
                should_cancel=lambda: self._cancelled,
                progress_callback=lambda current, total_records: self.signals.progress.emit(
                    self.folder,
                    self.token,
                    current,
                    total_records,
                ),
            )
        except ReviewIntelligenceCancelled:
            self.signals.cancelled.emit(self.folder, self.token)
            return
        except Exception as exc:  # pragma: no cover - desktop/runtime path
            self.signals.failed.emit(self.folder, self.token, str(exc))
            return
        self.signals.finished.emit(self.folder, self.token, bundle)


class ReviewIntelligenceCancelled(RuntimeError):
    pass


def build_review_intelligence(
    records: list[ImageRecord],
    *,
    should_cancel: Callable[[], bool] | None = None,
    progress_callback: Callable[[int, int], None] | None = None,
) -> ReviewIntelligenceBundle:
    if not records:
        return ReviewIntelligenceBundle(groups=(), insights_by_path={})

    total_records = len(records)
    _emit_progress(progress_callback, 0, total_records)
    metadata_cache: dict[str, CaptureMetadata] = {}
    fingerprints: list[_RecordFingerprint] = []
    for index, record in enumerate(records, start=1):
        _raise_if_cancelled(should_cancel)
        fingerprints.append(_build_fingerprint(record, metadata_cache))
        _emit_progress(progress_callback, index, total_records)

    path_to_fingerprint = {fingerprint.record.path: fingerprint for fingerprint in fingerprints}

    union = _UnionFind()
    edge_kinds: dict[tuple[str, str], str] = {}
    edge_reasons: dict[tuple[str, str], tuple[str, ...]] = {}

    _raise_if_cancelled(should_cancel)
    exact_duplicate_groups = _find_exact_duplicate_groups(fingerprints, should_cancel=should_cancel)
    for members in exact_duplicate_groups:
        _raise_if_cancelled(should_cancel)
        anchor = members[0]
        for path in members[1:]:
            _connect(
                union,
                edge_kinds,
                edge_reasons,
                anchor,
                path,
                kind="exact_duplicate",
                reasons=("Binary-identical file content.",),
            )

    ordered = sorted(
        fingerprints,
        key=lambda item: (
            item.metadata.captured_at_value or _modified_sort_key(item.record),
            item.record.modified_ns,
            item.record.name.casefold(),
        ),
    )
    for index, anchor in enumerate(ordered):
        _raise_if_cancelled(should_cancel)
        previous = anchor
        for offset, candidate in enumerate(ordered[index + 1 : index + 1 + _FALLBACK_MAX_NEIGHBORS], start=1):
            _raise_if_cancelled(should_cancel)
            if offset > 4 and _time_gap_too_large(anchor.metadata, candidate.metadata):
                break
            kind, reasons = _classify_pair(anchor, previous, candidate)
            previous = candidate
            if not kind:
                continue
            _connect(
                union,
                edge_kinds,
                edge_reasons,
                anchor.record.path,
                candidate.record.path,
                kind=kind,
                reasons=reasons,
            )

    members_by_root: dict[str, list[str]] = defaultdict(list)
    for record in records:
        _raise_if_cancelled(should_cancel)
        members_by_root[union.find(record.path)].append(record.path)

    groups: list[ReviewGroup] = []
    insights_by_path: dict[str, ReviewInsight] = {}
    for component_index, member_paths in enumerate(
        sorted(
            (sorted(paths, key=lambda path: _record_sort_key(path_to_fingerprint[path].record)) for paths in members_by_root.values()),
            key=lambda members: (_record_sort_key(path_to_fingerprint[members[0]].record), len(members)),
        ),
        start=1,
    ):
        _raise_if_cancelled(should_cancel)
        if len(member_paths) <= 1:
            continue

        kinds_in_group = [
            kind
            for (left, right), kind in edge_kinds.items()
            if left in member_paths and right in member_paths
        ]
        kind = max(kinds_in_group, key=lambda item: _GROUP_PRIORITY.get(item, 0), default="similar")
        label = _GROUP_LABEL.get(kind, "Group")
        reason_set: list[str] = []
        for (left, right), reasons in edge_reasons.items():
            if left not in member_paths or right not in member_paths:
                continue
            for reason in reasons:
                if reason not in reason_set:
                    reason_set.append(reason)
        group_id = f"review-{component_index}"
        groups.append(
            ReviewGroup(
                id=group_id,
                kind=kind,
                label=label,
                member_paths=tuple(member_paths),
                reasons=tuple(reason_set[:3]),
            )
        )
        for rank, path in enumerate(member_paths, start=1):
            _raise_if_cancelled(should_cancel)
            fingerprint = path_to_fingerprint[path]
            insight = ReviewInsight(
                path=path,
                group_id=group_id,
                group_kind=kind,
                group_label=label,
                group_size=len(member_paths),
                rank_in_group=rank,
                reasons=tuple(reason_set[:3]),
                detail_score=fingerprint.detail_score,
                exposure_score=fingerprint.exposure_score,
            )
            insights_by_path[path] = insight
            insights_by_path[normalized_path_key(path)] = insight

    return ReviewIntelligenceBundle(groups=tuple(groups), insights_by_path=insights_by_path)


def _build_fingerprint(record: ImageRecord, metadata_cache: dict[str, CaptureMetadata]) -> _RecordFingerprint:
    source_path = _analysis_source_path(record)
    metadata = metadata_cache.get(record.path)
    if metadata is None:
        metadata = load_capture_metadata(source_path)
        metadata_cache[record.path] = metadata

    image, _error = load_image_for_display(source_path, _FINGERPRINT_SIZE, prefer_embedded=True)
    dhash = None
    avg_luma = 0.0
    width = metadata.width
    height = metadata.height
    detail_score = 0.0
    exposure_score = 0.0
    if not image.isNull():
        dhash = _dhash(image)
        avg_luma = _average_luma(image)
        width = max(width, image.width())
        height = max(height, image.height())
        stats = build_inspection_stats(image)
        detail_score = stats.detail_score
        exposure_score = _exposure_balance(stats)

    return _RecordFingerprint(
        record=record,
        source_path=source_path,
        metadata=metadata,
        dhash=dhash,
        avg_luma=avg_luma,
        width=width,
        height=height,
        detail_score=detail_score,
        exposure_score=exposure_score,
    )


def _analysis_source_path(record: ImageRecord) -> str:
    for path in record.companion_paths:
        suffix = Path(path).suffix.lower()
        if suffix in {".jpg", ".jpeg", ".png", ".webp"}:
            return path
    return record.path


def _find_exact_duplicate_groups(
    fingerprints: list[_RecordFingerprint],
    *,
    should_cancel: Callable[[], bool] | None = None,
) -> list[tuple[str, ...]]:
    by_size: dict[int, list[_RecordFingerprint]] = defaultdict(list)
    for fingerprint in fingerprints:
        _raise_if_cancelled(should_cancel)
        by_size[fingerprint.record.size].append(fingerprint)

    groups: list[tuple[str, ...]] = []
    for candidates in by_size.values():
        _raise_if_cancelled(should_cancel)
        if len(candidates) < 2:
            continue
        by_hash: dict[str, list[str]] = defaultdict(list)
        for fingerprint in candidates:
            _raise_if_cancelled(should_cancel)
            digest = fingerprint.sha1_digest or _sha1_for_path(fingerprint.record.path)
            if not digest:
                continue
            by_hash[digest].append(fingerprint.record.path)
        for member_paths in by_hash.values():
            if len(member_paths) >= 2:
                groups.append(tuple(sorted(member_paths, key=str.casefold)))
    return groups


def _raise_if_cancelled(should_cancel: Callable[[], bool] | None) -> None:
    if should_cancel is not None and should_cancel():
        raise ReviewIntelligenceCancelled("Review intelligence task cancelled")


def _emit_progress(
    callback: Callable[[int, int], None] | None,
    current: int,
    total: int,
) -> None:
    if callback is None:
        return
    if total <= 0:
        callback(current, total)
        return
    if current in {0, 1, total} or current % 40 == 0:
        callback(current, total)


def _classify_pair(
    anchor: _RecordFingerprint,
    previous: _RecordFingerprint,
    candidate: _RecordFingerprint,
) -> tuple[str, tuple[str, ...]]:
    if not _can_be_related(anchor, candidate):
        return "", ()

    if _is_burst_neighbor(anchor.metadata, previous.metadata, candidate.metadata):
        return "burst", ("Captured in a tight burst with matching camera settings.",)

    if anchor.dhash is None or candidate.dhash is None:
        return "", ()

    distance = _hamming_distance(anchor.dhash, candidate.dhash)
    size_similarity = _dimension_similarity(anchor, candidate)
    luma_delta = abs(anchor.avg_luma - candidate.avg_luma)

    if distance <= 3 and size_similarity >= 0.94:
        return "likely_duplicate", ("Very small visual difference from a neighboring frame.",)

    if distance <= 5 and size_similarity >= 0.90 and luma_delta <= 16.0:
        return "likely_duplicate", ("Same composition with only minor framing or exposure drift.",)

    if distance <= 8 and (size_similarity >= 0.84 or _metadata_scene_match(anchor.metadata, candidate.metadata)):
        return "similar", ("Strong visual match inside the same local capture sequence.",)

    if distance <= 10 and _time_close(anchor.metadata, candidate.metadata, seconds=18.0):
        return "similar", ("Looks related and was captured close in time.",)

    return "", ()


def _can_be_related(left: _RecordFingerprint, right: _RecordFingerprint) -> bool:
    if _time_gap_too_large(left.metadata, right.metadata):
        return False
    if left.width > 0 and right.width > 0:
        width_ratio = min(left.width, right.width) / max(left.width, right.width)
        if width_ratio < 0.45:
            return False
    if left.height > 0 and right.height > 0:
        height_ratio = min(left.height, right.height) / max(left.height, right.height)
        if height_ratio < 0.45:
            return False
    return True


def _time_gap_too_large(left: CaptureMetadata, right: CaptureMetadata) -> bool:
    if left is EMPTY_METADATA or right is EMPTY_METADATA:
        return False
    if left.captured_at_value is None or right.captured_at_value is None:
        return False
    return abs(left.captured_at_value - right.captured_at_value) > timedelta(seconds=40.0)


def _time_close(left: CaptureMetadata, right: CaptureMetadata, *, seconds: float) -> bool:
    if left is EMPTY_METADATA or right is EMPTY_METADATA:
        return False
    if left.captured_at_value is None or right.captured_at_value is None:
        return False
    return abs(left.captured_at_value - right.captured_at_value) <= timedelta(seconds=seconds)


def _metadata_scene_match(left: CaptureMetadata, right: CaptureMetadata) -> bool:
    if left is EMPTY_METADATA or right is EMPTY_METADATA:
        return False
    if left.lens and right.lens and left.lens != right.lens:
        return False
    return all(
        (
            _close_numeric(left.focal_length_value, right.focal_length_value, tolerance=4.0),
            _close_numeric(left.aperture_value, right.aperture_value, tolerance=0.8),
            _close_numeric(left.iso_value, right.iso_value, tolerance=80.0),
        )
    )


def _is_burst_neighbor(anchor: CaptureMetadata, previous: CaptureMetadata, candidate: CaptureMetadata) -> bool:
    if (
        anchor is EMPTY_METADATA
        or previous is EMPTY_METADATA
        or candidate is EMPTY_METADATA
        or anchor.captured_at_value is None
        or previous.captured_at_value is None
        or candidate.captured_at_value is None
    ):
        return False

    if abs(candidate.captured_at_value - anchor.captured_at_value) > timedelta(seconds=4.0):
        return False
    if abs(candidate.captured_at_value - previous.captured_at_value) > timedelta(seconds=2.5):
        return False
    if anchor.lens and candidate.lens and anchor.lens != candidate.lens:
        return False
    if not _close_numeric(anchor.focal_length_value, candidate.focal_length_value, tolerance=3.0):
        return False
    if not _close_numeric(anchor.aperture_value, candidate.aperture_value, tolerance=0.7):
        return False
    if not _close_numeric(anchor.iso_value, candidate.iso_value, tolerance=40.0):
        return False
    return True


def _close_numeric(left: float | None, right: float | None, *, tolerance: float) -> bool:
    if left is None or right is None:
        return True
    return abs(left - right) <= tolerance


def _dimension_similarity(left: _RecordFingerprint, right: _RecordFingerprint) -> float:
    if left.width <= 0 or left.height <= 0 or right.width <= 0 or right.height <= 0:
        return 0.0
    width_ratio = min(left.width, right.width) / max(left.width, right.width)
    height_ratio = min(left.height, right.height) / max(left.height, right.height)
    return min(width_ratio, height_ratio)


def _sha1_for_path(path: str) -> str:
    digest = sha1(usedforsecurity=False)
    try:
        with open(path, "rb") as handle:
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
    except OSError:
        return ""
    return digest.hexdigest()


def _modified_sort_key(record: ImageRecord) -> int:
    return int(record.modified_ns or 0)


def _record_sort_key(record: ImageRecord) -> tuple[int, str]:
    return (int(record.modified_ns or 0), record.name.casefold())


def _dhash(image: QImage) -> int:
    scaled = image.convertToFormat(QImage.Format.Format_Grayscale8).scaled(
        9,
        8,
        aspectMode=Qt.AspectRatioMode.IgnoreAspectRatio,
        mode=Qt.TransformationMode.SmoothTransformation,
    )
    if scaled.isNull():
        return 0

    value = 0
    for y in range(8):
        for x in range(8):
            value <<= 1
            left = scaled.pixelColor(x, y).red()
            right = scaled.pixelColor(x + 1, y).red()
            if left > right:
                value |= 1
    return value


def _average_luma(image: QImage) -> float:
    scaled = image.convertToFormat(QImage.Format.Format_Grayscale8).scaled(
        8,
        8,
        aspectMode=Qt.AspectRatioMode.IgnoreAspectRatio,
        mode=Qt.TransformationMode.SmoothTransformation,
    )
    if scaled.isNull():
        return 0.0
    total = 0
    samples = 0
    for y in range(scaled.height()):
        for x in range(scaled.width()):
            total += scaled.pixelColor(x, y).red()
            samples += 1
    if samples <= 0:
        return 0.0
    return float(total) / float(samples)


def _exposure_balance(stats) -> float:
    clipping_penalty = min(38.0, (stats.shadow_clip_pct + stats.highlight_clip_pct) * 1.7)
    mean_penalty = abs(stats.mean_luminance - 128.0) / 2.4
    median_penalty = abs(stats.median_luminance - 128.0) / 2.8
    return max(0.0, min(100.0, 100.0 - clipping_penalty - mean_penalty - median_penalty))


def _hamming_distance(left: int, right: int) -> int:
    return (left ^ right).bit_count()


def _connect(
    union: "_UnionFind",
    edge_kinds: dict[tuple[str, str], str],
    edge_reasons: dict[tuple[str, str], tuple[str, ...]],
    left: str,
    right: str,
    *,
    kind: str,
    reasons: tuple[str, ...],
) -> None:
    union.union(left, right)
    edge_key = tuple(sorted((left, right), key=str.casefold))
    existing_kind = edge_kinds.get(edge_key)
    if existing_kind is None or _GROUP_PRIORITY.get(kind, 0) >= _GROUP_PRIORITY.get(existing_kind, 0):
        edge_kinds[edge_key] = kind
        edge_reasons[edge_key] = reasons


class _UnionFind:
    def __init__(self) -> None:
        self._parent: dict[str, str] = {}

    def find(self, item: str) -> str:
        parent = self._parent.get(item, item)
        if parent != item:
            parent = self.find(parent)
        self._parent[item] = parent
        return parent

    def union(self, left: str, right: str) -> None:
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root == right_root:
            return
        if left_root.casefold() <= right_root.casefold():
            self._parent[right_root] = left_root
        else:
            self._parent[left_root] = right_root
