from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

from .ai_results import AIConfidenceBucket
from .formats import FITS_SUFFIXES, JPEG_SUFFIXES, MODEL_SUFFIXES, PSD_SUFFIXES, RAW_SUFFIXES, suffix_for_path
from .models import FilterMode, ImageRecord, SessionAnnotation
from .review_workflows import (
    REVIEW_ROUND_FIRST_PASS,
    REVIEW_ROUND_HERO,
    REVIEW_ROUND_SECOND_PASS,
    REVIEW_ROUND_THIRD_PASS,
    normalize_review_round,
    review_round_label,
)

if TYPE_CHECKING:
    from .ai_results import AIImageResult
    from .metadata import CaptureMetadata
    from .review_intelligence import ReviewInsight
    from .review_workflows import RecordWorkflowInsight


TIFF_SUFFIXES = frozenset({".tif", ".tiff"})


class FileTypeFilter(str, Enum):
    ALL = "Any File Type"
    RAW = "RAW"
    FITS = "FITS"
    JPEG = "JPEG"
    EDITED = "Edited"
    PSD = "PSD / PSB"
    TIFF = "TIFF"
    STL = "STL"
    OTHER = "Other"


class ReviewStateFilter(str, Enum):
    ALL = "Any Review State"
    ACCEPTED = "Accepted"
    REJECTED = "Rejected"
    UNREVIEWED = "Unreviewed"


class AIStateFilter(str, Enum):
    ALL = "Any AI State"
    TOP_PICKS = "Top Picks"
    GROUPED = "Grouped"
    PENDING = "Pending Review"
    OBVIOUS_WINNERS = "Obvious Winners"
    LIKELY_KEEPERS = "Likely Keepers"
    NEEDS_REVIEW = "Needs Review"
    LIKELY_REJECTS = "Likely Rejects"
    DISAGREEMENTS = "AI Disagreements"


class OrientationFilter(str, Enum):
    ALL = "Any Orientation"
    LANDSCAPE = "Landscape"
    PORTRAIT = "Portrait"
    SQUARE = "Square"


@dataclass(slots=True)
class RecordFilterQuery:
    quick_filter: FilterMode = FilterMode.ALL
    search_text: str = ""
    file_type: FileTypeFilter = FileTypeFilter.ALL
    review_state: ReviewStateFilter = ReviewStateFilter.ALL
    ai_state: AIStateFilter = AIStateFilter.ALL
    review_round: str = ""
    camera_text: str = ""
    lens_text: str = ""
    tag_text: str = ""
    min_rating: int = 0
    orientation: OrientationFilter = OrientationFilter.ALL
    captured_after: date | None = None
    captured_before: date | None = None
    iso_min: int = 0
    iso_max: int = 0
    focal_min: float = 0.0
    focal_max: float = 0.0

    @property
    def normalized_search_text(self) -> str:
        return self.search_text.strip().casefold()

    @property
    def has_active_filters(self) -> bool:
        return bool(active_filter_labels(self))

    @property
    def requires_metadata(self) -> bool:
        return any(
            (
                bool(self.camera_text.strip()),
                bool(self.lens_text.strip()),
                self.orientation != OrientationFilter.ALL,
                self.captured_after is not None,
                self.captured_before is not None,
                self.iso_min > 0,
                self.iso_max > 0,
                self.focal_min > 0,
                self.focal_max > 0,
            )
        )


@dataclass(slots=True, frozen=True)
class SavedFilterPreset:
    name: str
    query: RecordFilterQuery


def active_filter_labels(query: RecordFilterQuery) -> list[str]:
    labels: list[str] = []
    if query.quick_filter != FilterMode.ALL:
        labels.append(query.quick_filter.value)
    if query.normalized_search_text:
        labels.append(f'Search "{query.search_text.strip()}"')
    if query.file_type != FileTypeFilter.ALL:
        labels.append(query.file_type.value)
    if query.review_state != ReviewStateFilter.ALL:
        labels.append(query.review_state.value)
    if query.ai_state != AIStateFilter.ALL:
        labels.append(f"AI {query.ai_state.value}")
    round_label = review_round_label(query.review_round)
    if round_label:
        labels.append(round_label)
    if query.camera_text.strip():
        labels.append(f'Camera "{query.camera_text.strip()}"')
    if query.lens_text.strip():
        labels.append(f'Lens "{query.lens_text.strip()}"')
    if query.tag_text.strip():
        labels.append(f'Tag "{query.tag_text.strip()}"')
    if query.min_rating > 0:
        labels.append(f"Rating {query.min_rating}+")
    if query.orientation != OrientationFilter.ALL:
        labels.append(query.orientation.value)
    if query.captured_after is not None:
        labels.append(f"After {query.captured_after.isoformat()}")
    if query.captured_before is not None:
        labels.append(f"Before {query.captured_before.isoformat()}")
    if query.iso_min > 0 or query.iso_max > 0:
        labels.append(_numeric_range_label("ISO", query.iso_min, query.iso_max))
    if query.focal_min > 0 or query.focal_max > 0:
        labels.append(_numeric_range_label("Focal", query.focal_min, query.focal_max, suffix="mm"))
    return labels


def matches_record_query(
    record: ImageRecord,
    query: RecordFilterQuery,
    *,
    annotation: SessionAnnotation | None = None,
    ai_result: "AIImageResult | None" = None,
    metadata: "CaptureMetadata | None" = None,
    review_insight: "ReviewInsight | None" = None,
    workflow_insight: "RecordWorkflowInsight | None" = None,
) -> bool:
    resolved_annotation = annotation if annotation is not None else SessionAnnotation()
    return (
        _matches_quick_filter(record, query.quick_filter, resolved_annotation, ai_result, review_insight, workflow_insight)
        and _matches_search(record, query)
        and _matches_file_type(record, query.file_type)
        and _matches_review_state(query.review_state, resolved_annotation)
        and _matches_ai_state(query.ai_state, resolved_annotation, ai_result, workflow_insight)
        and _matches_review_round(query.review_round, resolved_annotation)
        and _matches_rating(query.min_rating, resolved_annotation)
        and _matches_tags(query.tag_text, resolved_annotation)
        and _matches_metadata_filters(query, metadata)
    )


def _matches_quick_filter(
    record: ImageRecord,
    quick_filter: FilterMode,
    annotation: SessionAnnotation,
    ai_result: "AIImageResult | None",
    review_insight: "ReviewInsight | None",
    workflow_insight: "RecordWorkflowInsight | None",
) -> bool:
    if quick_filter == FilterMode.WINNERS:
        return annotation.winner
    if quick_filter == FilterMode.REJECTS:
        return annotation.reject
    if quick_filter == FilterMode.UNREVIEWED:
        return not annotation.winner and not annotation.reject
    if quick_filter == FilterMode.EDITED:
        return record.has_edits
    if quick_filter == FilterMode.SMART_GROUPS:
        return review_insight is not None and review_insight.has_group
    if quick_filter == FilterMode.DUPLICATES:
        return review_insight is not None and review_insight.is_duplicate
    if quick_filter == FilterMode.AI_TOP_PICKS:
        return ai_result is not None and ai_result.is_top_pick
    if quick_filter == FilterMode.AI_GROUPED:
        return ai_result is not None and ai_result.group_size > 1
    if quick_filter == FilterMode.AI_DISAGREEMENTS:
        return workflow_insight is not None and workflow_insight.has_disagreement
    if quick_filter == FilterMode.REVIEW_ROUNDS:
        return workflow_insight is not None and workflow_insight.has_round
    return True


def _matches_search(record: ImageRecord, query: RecordFilterQuery) -> bool:
    needle = query.normalized_search_text
    if not needle:
        return True
    for path in record.stack_paths:
        if needle in Path(path).name.casefold():
            return True
    return needle in record.name.casefold()


def _matches_file_type(record: ImageRecord, file_type: FileTypeFilter) -> bool:
    if file_type == FileTypeFilter.ALL:
        return True
    if file_type == FileTypeFilter.EDITED:
        return record.has_edits

    categories = _record_file_type_categories(record)
    if file_type == FileTypeFilter.OTHER:
        return file_type in categories
    return file_type in categories


def _record_file_type_categories(record: ImageRecord) -> set[FileTypeFilter]:
    categories: set[FileTypeFilter] = set()
    recognized_suffix = False
    for path in record.stack_paths:
        suffix = suffix_for_path(path)
        if not suffix:
            continue
        if suffix in RAW_SUFFIXES:
            categories.add(FileTypeFilter.RAW)
            recognized_suffix = True
        elif suffix in FITS_SUFFIXES:
            categories.add(FileTypeFilter.FITS)
            recognized_suffix = True
        elif suffix in JPEG_SUFFIXES:
            categories.add(FileTypeFilter.JPEG)
            recognized_suffix = True
        elif suffix in PSD_SUFFIXES:
            categories.add(FileTypeFilter.PSD)
            recognized_suffix = True
        elif suffix in TIFF_SUFFIXES:
            categories.add(FileTypeFilter.TIFF)
            recognized_suffix = True
        elif suffix in MODEL_SUFFIXES:
            categories.add(FileTypeFilter.STL)
            recognized_suffix = True
        else:
            categories.add(FileTypeFilter.OTHER)
    if record.has_edits:
        categories.add(FileTypeFilter.EDITED)
    if not recognized_suffix:
        categories.add(FileTypeFilter.OTHER)
    return categories


def _matches_review_state(review_state: ReviewStateFilter, annotation: SessionAnnotation) -> bool:
    if review_state == ReviewStateFilter.ACCEPTED:
        return annotation.winner
    if review_state == ReviewStateFilter.REJECTED:
        return annotation.reject
    if review_state == ReviewStateFilter.UNREVIEWED:
        return not annotation.winner and not annotation.reject
    return True


def _matches_ai_state(
    ai_state: AIStateFilter,
    annotation: SessionAnnotation,
    ai_result: "AIImageResult | None",
    workflow_insight: "RecordWorkflowInsight | None",
) -> bool:
    if ai_state == AIStateFilter.ALL:
        return True
    if ai_state == AIStateFilter.DISAGREEMENTS:
        return workflow_insight is not None and workflow_insight.has_disagreement
    if ai_result is None:
        return False
    if ai_state == AIStateFilter.TOP_PICKS:
        return ai_result.is_top_pick
    if ai_state == AIStateFilter.GROUPED:
        return ai_result.group_size > 1
    if ai_state == AIStateFilter.PENDING:
        return not annotation.winner and not annotation.reject
    if ai_state == AIStateFilter.OBVIOUS_WINNERS:
        return ai_result.confidence_bucket == AIConfidenceBucket.OBVIOUS_WINNER
    if ai_state == AIStateFilter.LIKELY_KEEPERS:
        return ai_result.confidence_bucket == AIConfidenceBucket.LIKELY_KEEPER
    if ai_state == AIStateFilter.NEEDS_REVIEW:
        return ai_result.confidence_bucket == AIConfidenceBucket.NEEDS_REVIEW
    if ai_state == AIStateFilter.LIKELY_REJECTS:
        return ai_result.confidence_bucket == AIConfidenceBucket.LIKELY_REJECT
    return True


def _matches_review_round(review_round: str, annotation: SessionAnnotation) -> bool:
    normalized = normalize_review_round(review_round)
    if not normalized:
        return True
    return normalize_review_round(annotation.review_round) == normalized


def _matches_rating(min_rating: int, annotation: SessionAnnotation) -> bool:
    if min_rating <= 0:
        return True
    return annotation.rating >= min_rating


def _matches_tags(tag_text: str, annotation: SessionAnnotation) -> bool:
    needle = tag_text.strip().casefold()
    if not needle:
        return True
    return any(needle in tag.casefold() for tag in annotation.tags)


def _matches_metadata_filters(query: RecordFilterQuery, metadata: "CaptureMetadata | None") -> bool:
    if not query.requires_metadata:
        return True
    if metadata is None:
        return False

    if query.camera_text.strip():
        camera_value = getattr(metadata, "camera", "").casefold()
        if query.camera_text.strip().casefold() not in camera_value:
            return False

    if query.lens_text.strip():
        lens_value = getattr(metadata, "lens", "").casefold()
        if query.lens_text.strip().casefold() not in lens_value:
            return False

    if query.orientation != OrientationFilter.ALL:
        orientation_value = getattr(metadata, "orientation", "")
        if orientation_value != query.orientation.value:
            return False

    captured_at_value = getattr(metadata, "captured_at_value", None)
    if query.captured_after is not None:
        if captured_at_value is None or captured_at_value.date() < query.captured_after:
            return False
    if query.captured_before is not None:
        if captured_at_value is None or captured_at_value.date() > query.captured_before:
            return False

    iso_value = getattr(metadata, "iso_value", None)
    if query.iso_min > 0:
        if iso_value is None or iso_value < query.iso_min:
            return False
    if query.iso_max > 0:
        if iso_value is None or iso_value > query.iso_max:
            return False

    focal_value = getattr(metadata, "focal_length_value", None)
    if query.focal_min > 0:
        if focal_value is None or focal_value < query.focal_min:
            return False
    if query.focal_max > 0:
        if focal_value is None or focal_value > query.focal_max:
            return False

    return True


def _numeric_range_label(label: str, minimum: float, maximum: float, *, suffix: str = "") -> str:
    if minimum > 0 and maximum > 0:
        return f"{label} {minimum:g}-{maximum:g}{suffix}"
    if minimum > 0:
        return f"{label} >= {minimum:g}{suffix}"
    return f"{label} <= {maximum:g}{suffix}"


def serialize_filter_query(query: RecordFilterQuery) -> dict[str, object]:
    return {
        "quick_filter": query.quick_filter.value,
        "search_text": query.search_text,
        "file_type": query.file_type.value,
        "review_state": query.review_state.value,
        "ai_state": query.ai_state.value,
        "review_round": query.review_round,
        "camera_text": query.camera_text,
        "lens_text": query.lens_text,
        "tag_text": query.tag_text,
        "min_rating": query.min_rating,
        "orientation": query.orientation.value,
        "captured_after": query.captured_after.isoformat() if query.captured_after is not None else "",
        "captured_before": query.captured_before.isoformat() if query.captured_before is not None else "",
        "iso_min": query.iso_min,
        "iso_max": query.iso_max,
        "focal_min": query.focal_min,
        "focal_max": query.focal_max,
    }


def deserialize_filter_query(payload: dict[str, object] | None) -> RecordFilterQuery:
    if not isinstance(payload, dict):
        return RecordFilterQuery()
    return RecordFilterQuery(
        quick_filter=_enum_from_value(FilterMode, payload.get("quick_filter"), FilterMode.ALL),
        search_text=_string_value(payload.get("search_text")),
        file_type=_enum_from_value(FileTypeFilter, payload.get("file_type"), FileTypeFilter.ALL),
        review_state=_enum_from_value(ReviewStateFilter, payload.get("review_state"), ReviewStateFilter.ALL),
        ai_state=_enum_from_value(AIStateFilter, payload.get("ai_state"), AIStateFilter.ALL),
        review_round=normalize_review_round(_string_value(payload.get("review_round"))),
        camera_text=_string_value(payload.get("camera_text")),
        lens_text=_string_value(payload.get("lens_text")),
        tag_text=_string_value(payload.get("tag_text")),
        min_rating=_int_value(payload.get("min_rating")),
        orientation=_enum_from_value(OrientationFilter, payload.get("orientation"), OrientationFilter.ALL),
        captured_after=_date_value(payload.get("captured_after")),
        captured_before=_date_value(payload.get("captured_before")),
        iso_min=_int_value(payload.get("iso_min")),
        iso_max=_int_value(payload.get("iso_max")),
        focal_min=_float_value(payload.get("focal_min")),
        focal_max=_float_value(payload.get("focal_max")),
    )


def serialize_saved_filter_preset(preset: SavedFilterPreset) -> dict[str, object]:
    return {
        "name": preset.name,
        "query": serialize_filter_query(preset.query),
    }


def deserialize_saved_filter_preset(payload: dict[str, object] | None) -> SavedFilterPreset | None:
    if not isinstance(payload, dict):
        return None
    name = _string_value(payload.get("name")).strip()
    if not name:
        return None
    return SavedFilterPreset(
        name=name,
        query=deserialize_filter_query(payload.get("query") if isinstance(payload.get("query"), dict) else {}),
    )


def builtin_filter_presets() -> tuple[SavedFilterPreset, ...]:
    return (
        SavedFilterPreset(
            name="Unreviewed RAW",
            query=RecordFilterQuery(
                file_type=FileTypeFilter.RAW,
                review_state=ReviewStateFilter.UNREVIEWED,
            ),
        ),
        SavedFilterPreset(
            name="AI Top Picks Pending",
            query=RecordFilterQuery(
                ai_state=AIStateFilter.TOP_PICKS,
                review_state=ReviewStateFilter.UNREVIEWED,
            ),
        ),
        SavedFilterPreset(
            name="Obvious Winners",
            query=RecordFilterQuery(
                ai_state=AIStateFilter.OBVIOUS_WINNERS,
                review_state=ReviewStateFilter.UNREVIEWED,
            ),
        ),
        SavedFilterPreset(
            name="Likely Keepers",
            query=RecordFilterQuery(
                ai_state=AIStateFilter.LIKELY_KEEPERS,
                review_state=ReviewStateFilter.UNREVIEWED,
            ),
        ),
        SavedFilterPreset(
            name="Needs Review",
            query=RecordFilterQuery(
                ai_state=AIStateFilter.NEEDS_REVIEW,
                review_state=ReviewStateFilter.UNREVIEWED,
            ),
        ),
        SavedFilterPreset(
            name="Near Duplicates",
            query=RecordFilterQuery(
                quick_filter=FilterMode.DUPLICATES,
            ),
        ),
        SavedFilterPreset(
            name="AI Disagreements",
            query=RecordFilterQuery(
                quick_filter=FilterMode.AI_DISAGREEMENTS,
            ),
        ),
        SavedFilterPreset(
            name="First Pass Rejects",
            query=RecordFilterQuery(
                quick_filter=FilterMode.REVIEW_ROUNDS,
                review_round=REVIEW_ROUND_FIRST_PASS,
            ),
        ),
        SavedFilterPreset(
            name="Second Pass Keepers",
            query=RecordFilterQuery(
                quick_filter=FilterMode.REVIEW_ROUNDS,
                review_round=REVIEW_ROUND_SECOND_PASS,
            ),
        ),
        SavedFilterPreset(
            name="Third Pass Finalists",
            query=RecordFilterQuery(
                quick_filter=FilterMode.REVIEW_ROUNDS,
                review_round=REVIEW_ROUND_THIRD_PASS,
            ),
        ),
        SavedFilterPreset(
            name="Final Hero Selects",
            query=RecordFilterQuery(
                quick_filter=FilterMode.REVIEW_ROUNDS,
                review_round=REVIEW_ROUND_HERO,
            ),
        ),
        SavedFilterPreset(
            name="Edited Winners",
            query=RecordFilterQuery(
                file_type=FileTypeFilter.EDITED,
                review_state=ReviewStateFilter.ACCEPTED,
            ),
        ),
    )


def _enum_from_value(enum_type, value, default):
    if isinstance(value, enum_type):
        return value
    if isinstance(value, str):
        for member in enum_type:
            if value in {member.name, member.value}:
                return member
        try:
            return enum_type(value)
        except ValueError:
            return default
    return default


def _string_value(value) -> str:
    return value if isinstance(value, str) else ""


def _int_value(value) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _float_value(value) -> float:
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return 0.0


def _date_value(value) -> date | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None
