from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol, runtime_checkable

from PySide6.QtCore import QSize
from PySide6.QtGui import QImage

from ..models import ImageRecord


@dataclass(slots=True, frozen=True)
class DisplayLoadRequest:
    path: str
    suffix: str
    target_size: QSize
    prefer_embedded: bool
    fits_display_settings: object | None = None


@runtime_checkable
class MediaDisplayProvider(Protocol):
    provider_id: str

    def can_handle_display(self, request: DisplayLoadRequest) -> bool:
        ...

    def load_for_display(self, request: DisplayLoadRequest) -> tuple[QImage, str | None]:
        ...


@dataclass(slots=True, frozen=True)
class MetadataLoadRequest:
    path: str
    suffix: str


@runtime_checkable
class MediaMetadataProvider(Protocol):
    provider_id: str

    def can_handle_metadata(self, request: MetadataLoadRequest) -> bool:
        ...

    def load_metadata(self, request: MetadataLoadRequest) -> object:
        ...


@dataclass(slots=True, frozen=True)
class ReviewGroupingRequest:
    records: tuple[ImageRecord, ...]
    should_cancel: Callable[[], bool] | None = None
    progress_callback: Callable[[int, int], None] | None = None
    chunk_callback: Callable[[tuple[object, ...], dict[str, object]], None] | None = None
    cached_fingerprints: dict[str, object] | None = None
    computed_fingerprint_callback: Callable[[object], None] | None = None


@runtime_checkable
class ReviewGroupingProvider(Protocol):
    provider_id: str

    def can_handle_review_grouping(self, request: ReviewGroupingRequest) -> bool:
        ...

    def build_review_intelligence(self, request: ReviewGroupingRequest) -> object:
        ...


@dataclass(slots=True, frozen=True)
class ReviewScoringRequest:
    records: tuple[ImageRecord, ...]
    ai_bundle: object | None = None
    review_bundle: object | None = None
    correction_events: tuple[dict[str, object], ...] = ()
    should_cancel: Callable[[], bool] | None = None


@runtime_checkable
class ReviewScoringProvider(Protocol):
    provider_id: str

    def can_handle_review_scoring(self, request: ReviewScoringRequest) -> bool:
        ...

    def build_burst_recommendations(self, request: ReviewScoringRequest) -> object:
        ...
