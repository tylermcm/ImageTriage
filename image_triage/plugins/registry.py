from __future__ import annotations

from .contracts import (
    DisplayLoadRequest,
    MediaDisplayProvider,
    MediaMetadataProvider,
    MetadataLoadRequest,
    ReviewGroupingProvider,
    ReviewGroupingRequest,
    ReviewScoringProvider,
    ReviewScoringRequest,
)


_DISPLAY_PROVIDERS: list[MediaDisplayProvider] = []
_METADATA_PROVIDERS: list[MediaMetadataProvider] = []
_REVIEW_GROUPING_PROVIDERS: list[ReviewGroupingProvider] = []
_REVIEW_SCORING_PROVIDERS: list[ReviewScoringProvider] = []


def register_display_provider(provider: MediaDisplayProvider) -> None:
    existing_index = next(
        (index for index, current in enumerate(_DISPLAY_PROVIDERS) if current.provider_id == provider.provider_id),
        None,
    )
    if existing_index is None:
        _DISPLAY_PROVIDERS.append(provider)
        return
    _DISPLAY_PROVIDERS[existing_index] = provider


def iter_display_providers() -> tuple[MediaDisplayProvider, ...]:
    return tuple(_DISPLAY_PROVIDERS)


def resolve_display_provider(request: DisplayLoadRequest) -> MediaDisplayProvider | None:
    for provider in _DISPLAY_PROVIDERS:
        if provider.can_handle_display(request):
            return provider
    return None


def register_metadata_provider(provider: MediaMetadataProvider) -> None:
    existing_index = next(
        (index for index, current in enumerate(_METADATA_PROVIDERS) if current.provider_id == provider.provider_id),
        None,
    )
    if existing_index is None:
        _METADATA_PROVIDERS.append(provider)
        return
    _METADATA_PROVIDERS[existing_index] = provider


def iter_metadata_providers() -> tuple[MediaMetadataProvider, ...]:
    return tuple(_METADATA_PROVIDERS)


def resolve_metadata_provider(request: MetadataLoadRequest) -> MediaMetadataProvider | None:
    for provider in _METADATA_PROVIDERS:
        if provider.can_handle_metadata(request):
            return provider
    return None


def register_review_grouping_provider(provider: ReviewGroupingProvider) -> None:
    existing_index = next(
        (index for index, current in enumerate(_REVIEW_GROUPING_PROVIDERS) if current.provider_id == provider.provider_id),
        None,
    )
    if existing_index is None:
        _REVIEW_GROUPING_PROVIDERS.append(provider)
        return
    _REVIEW_GROUPING_PROVIDERS[existing_index] = provider


def iter_review_grouping_providers() -> tuple[ReviewGroupingProvider, ...]:
    return tuple(_REVIEW_GROUPING_PROVIDERS)


def resolve_review_grouping_provider(request: ReviewGroupingRequest) -> ReviewGroupingProvider | None:
    for provider in _REVIEW_GROUPING_PROVIDERS:
        if provider.can_handle_review_grouping(request):
            return provider
    return None


def register_review_scoring_provider(provider: ReviewScoringProvider) -> None:
    existing_index = next(
        (index for index, current in enumerate(_REVIEW_SCORING_PROVIDERS) if current.provider_id == provider.provider_id),
        None,
    )
    if existing_index is None:
        _REVIEW_SCORING_PROVIDERS.append(provider)
        return
    _REVIEW_SCORING_PROVIDERS[existing_index] = provider


def iter_review_scoring_providers() -> tuple[ReviewScoringProvider, ...]:
    return tuple(_REVIEW_SCORING_PROVIDERS)


def resolve_review_scoring_provider(request: ReviewScoringRequest) -> ReviewScoringProvider | None:
    for provider in _REVIEW_SCORING_PROVIDERS:
        if provider.can_handle_review_scoring(request):
            return provider
    return None
