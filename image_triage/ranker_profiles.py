from __future__ import annotations

"""Profile suggestion helpers for AI ranker training."""

from dataclasses import dataclass
import re
from typing import Callable

from .formats import FITS_SUFFIXES, suffix_for_path
from .metadata import EMPTY_METADATA, CaptureMetadata, load_capture_metadata
from .models import ImageRecord


DEFAULT_RANKER_PROFILE_KEY = "general"
RANKER_PROFILE_OPTIONS: tuple[tuple[str, str], ...] = (
    (DEFAULT_RANKER_PROFILE_KEY, "General Use"),
    ("portrait", "Portrait"),
    ("landscape", "Landscape"),
    ("wildlife", "Wildlife"),
    ("sports_action", "Sports / Action"),
    ("event_documentary", "Event / Documentary"),
    ("astro", "Astro"),
    ("macro_product", "Macro / Product"),
)
PROFILE_KEYWORD_WEIGHTS: dict[str, dict[str, float]] = {
    "portrait": {
        "portrait": 3.0,
        "portraits": 3.0,
        "headshot": 3.0,
        "headshots": 3.0,
        "model": 2.0,
        "models": 2.0,
        "engagement": 2.0,
        "family": 2.0,
        "senior": 2.0,
        "newborn": 2.0,
        "fashion": 2.0,
    },
    "landscape": {
        "landscape": 3.0,
        "landscapes": 3.0,
        "mountain": 2.0,
        "mountains": 2.0,
        "sunrise": 2.0,
        "sunset": 2.0,
        "waterfall": 2.0,
        "waterfalls": 2.0,
        "travel": 1.5,
        "scenic": 2.0,
        "vista": 2.0,
        "hike": 1.5,
    },
    "wildlife": {
        "wildlife": 3.0,
        "bird": 3.0,
        "birds": 3.0,
        "eagle": 3.0,
        "hawk": 3.0,
        "owl": 3.0,
        "deer": 2.0,
        "elk": 2.0,
        "bear": 2.0,
        "fox": 2.0,
        "duck": 2.0,
        "safari": 2.0,
    },
    "sports_action": {
        "sports": 3.0,
        "sport": 3.0,
        "football": 3.0,
        "soccer": 3.0,
        "basketball": 3.0,
        "baseball": 3.0,
        "hockey": 3.0,
        "race": 2.0,
        "racing": 2.0,
        "action": 2.0,
        "surf": 2.0,
        "skate": 2.0,
    },
    "event_documentary": {
        "event": 3.0,
        "events": 3.0,
        "wedding": 3.0,
        "reception": 2.0,
        "ceremony": 2.0,
        "concert": 2.0,
        "party": 2.0,
        "street": 1.5,
        "documentary": 2.0,
        "festival": 2.0,
        "conference": 2.0,
    },
    "astro": {
        "astro": 4.0,
        "astrophotography": 4.0,
        "nebula": 4.0,
        "galaxy": 4.0,
        "andromeda": 4.0,
        "orion": 4.0,
        "moon": 3.0,
        "milkyway": 4.0,
        "milky": 2.0,
        "eclipse": 3.0,
        "ha": 2.0,
        "oiii": 2.0,
        "sii": 2.0,
        "luminance": 2.0,
    },
    "macro_product": {
        "macro": 4.0,
        "product": 4.0,
        "studio": 2.0,
        "catalog": 2.0,
        "watch": 2.0,
        "jewelry": 2.0,
        "ring": 2.0,
        "detail": 2.0,
        "flower": 1.5,
    },
}


@dataclass(slots=True, frozen=True)
class RankerProfileSuggestion:
    profile_key: str
    profile_label: str
    reason: str
    confidence: float = 0.0
    confidence_label: str = "Low"
    is_mixed: bool = False


def ranker_profile_options() -> tuple[tuple[str, str], ...]:
    """Return the available training profiles in display order."""
    return RANKER_PROFILE_OPTIONS


def normalize_ranker_profile(profile_value: object) -> tuple[str, str]:
    """Normalize a profile key or label into the canonical key/label pair."""
    token = _profile_token(profile_value)
    for key, label in RANKER_PROFILE_OPTIONS:
        if token in {_profile_token(key), _profile_token(label)}:
            return key, label
    return DEFAULT_RANKER_PROFILE_KEY, dict(RANKER_PROFILE_OPTIONS)[DEFAULT_RANKER_PROFILE_KEY]


def suggest_training_profile(
    records: list[ImageRecord],
    *,
    sample_limit: int = 48,
    metadata_loader: Callable[[str], CaptureMetadata] | None = None,
) -> RankerProfileSuggestion:
    """Suggest a specialist profile only when the folder signal is clear enough."""
    default_key, default_label = normalize_ranker_profile(DEFAULT_RANKER_PROFILE_KEY)
    if not records:
        return RankerProfileSuggestion(default_key, default_label, "No images loaded. Keeping General Use.")

    sampled_records = _sample_profile_records(records, limit=sample_limit)
    total = max(1, len(sampled_records))
    scores = {key: 0.0 for key, _label in RANKER_PROFILE_OPTIONS if key != DEFAULT_RANKER_PROFILE_KEY}
    evidence: dict[str, list[str]] = {key: [] for key in scores}

    fits_count = sum(1 for record in sampled_records if suffix_for_path(record.path) in FITS_SUFFIXES)
    if fits_count / total >= 0.35:
        return RankerProfileSuggestion(
            "astro",
            "Astro",
            "Suggested Astro (high confidence) because this folder is heavily FITS-based.",
            confidence=0.98,
            confidence_label="High",
        )

    loader = metadata_loader or load_capture_metadata
    metadata_rows: list[CaptureMetadata] = []
    for record in sampled_records:
        _apply_keyword_scores(record.path, scores, evidence)
        try:
            metadata = loader(record.path)
        except Exception:
            metadata = EMPTY_METADATA
        if isinstance(metadata, CaptureMetadata):
            metadata_rows.append(metadata)

    if not metadata_rows:
        return _finalize_profile_suggestion(
            scores=scores,
            evidence=evidence,
            default_key=default_key,
            default_label=default_label,
            fallback_reason="No usable metadata found. Keeping General Use.",
        )

    portrait_count = sum(1 for item in metadata_rows if item.height > item.width > 0)
    landscape_count = sum(1 for item in metadata_rows if item.width > item.height > 0)
    wide_count = sum(1 for item in metadata_rows if item.focal_length_value is not None and item.focal_length_value <= 35.0)
    short_tele_count = sum(
        1 for item in metadata_rows if item.focal_length_value is not None and 50.0 <= item.focal_length_value <= 135.0
    )
    long_lens_count = sum(1 for item in metadata_rows if item.focal_length_value is not None and item.focal_length_value >= 250.0)
    fast_shutter_count = sum(
        1 for item in metadata_rows if item.exposure_seconds is not None and item.exposure_seconds <= (1.0 / 1000.0)
    )
    long_exposure_count = sum(1 for item in metadata_rows if item.exposure_seconds is not None and item.exposure_seconds >= 1.0)
    wide_aperture_count = sum(1 for item in metadata_rows if item.aperture_value is not None and item.aperture_value <= 4.0)
    high_iso_count = sum(1 for item in metadata_rows if item.iso_value is not None and item.iso_value >= 1600.0)

    usable = max(1, len(metadata_rows))
    portrait_ratio = portrait_count / usable
    landscape_ratio = landscape_count / usable
    wide_ratio = wide_count / usable
    short_tele_ratio = short_tele_count / usable
    long_lens_ratio = long_lens_count / usable
    fast_shutter_ratio = fast_shutter_count / usable
    long_exposure_ratio = long_exposure_count / usable
    wide_aperture_ratio = wide_aperture_count / usable
    high_iso_ratio = high_iso_count / usable

    scores["astro"] += _ratio_points(long_exposure_ratio, start=0.18, full=0.55, weight=8.0)
    scores["astro"] += _ratio_points(high_iso_ratio, start=0.12, full=0.45, weight=4.5)
    scores["portrait"] += _ratio_points(portrait_ratio, start=0.35, full=0.75, weight=5.0)
    scores["portrait"] += _ratio_points(short_tele_ratio, start=0.25, full=0.65, weight=4.0)
    scores["portrait"] += _ratio_points(wide_aperture_ratio, start=0.2, full=0.55, weight=2.5)
    scores["landscape"] += _ratio_points(landscape_ratio, start=0.45, full=0.85, weight=4.5)
    scores["landscape"] += _ratio_points(wide_ratio, start=0.25, full=0.65, weight=4.0)
    scores["wildlife"] += _ratio_points(long_lens_ratio, start=0.25, full=0.7, weight=5.0)
    scores["wildlife"] += _ratio_points(fast_shutter_ratio, start=0.12, full=0.45, weight=3.0)
    scores["sports_action"] += _ratio_points(fast_shutter_ratio, start=0.3, full=0.75, weight=5.0)
    scores["sports_action"] += _ratio_points(high_iso_ratio, start=0.12, full=0.5, weight=2.5)
    scores["sports_action"] += _ratio_points(long_lens_ratio, start=0.1, full=0.4, weight=1.5)
    scores["event_documentary"] += _ratio_points(high_iso_ratio, start=0.2, full=0.6, weight=4.0)
    scores["event_documentary"] += _ratio_points(short_tele_ratio, start=0.1, full=0.45, weight=1.8)
    if 0.2 <= portrait_ratio <= 0.8:
        scores["event_documentary"] += 1.0
    scores["macro_product"] += _ratio_points(wide_aperture_ratio, start=0.2, full=0.6, weight=1.8)
    if portrait_ratio >= 0.5:
        evidence["portrait"].append("portrait-heavy framing")
    if short_tele_ratio >= 0.35:
        evidence["portrait"].append("portrait focal lengths")
    if wide_aperture_ratio >= 0.25:
        evidence["portrait"].append("wide apertures")
    if landscape_ratio >= 0.7:
        evidence["landscape"].append("mostly horizontal framing")
    if wide_ratio >= 0.45:
        evidence["landscape"].append("wide-angle focal lengths")
    if long_lens_ratio >= 0.45:
        evidence["wildlife"].append("long-lens coverage")
    if fast_shutter_ratio >= 0.45:
        evidence["sports_action"].append("very fast shutter speeds")
    if long_exposure_ratio >= 0.3:
        evidence["astro"].append("long exposures")
    if high_iso_ratio >= 0.3:
        evidence["astro"].append("high ISO usage")
        evidence["event_documentary"].append("high ISO usage")

    return _finalize_profile_suggestion(
        scores=scores,
        evidence=evidence,
        default_key=default_key,
        default_label=default_label,
        fallback_reason="No strong class match found. Keeping General Use.",
    )


def _finalize_profile_suggestion(
    *,
    scores: dict[str, float],
    evidence: dict[str, list[str]],
    default_key: str,
    default_label: str,
    fallback_reason: str,
) -> RankerProfileSuggestion:
    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    if not ranked or ranked[0][1] <= 0.0:
        return RankerProfileSuggestion(default_key, default_label, fallback_reason)

    top_key, top_score = ranked[0]
    second_score = ranked[1][1] if len(ranked) > 1 else 0.0
    total_score = sum(max(0.0, value) for value in scores.values())
    confidence = max(0.0, min(1.0, top_score / max(1.0, total_score)))
    margin_ratio = (top_score - second_score) / max(1.0, top_score)
    strong_categories = sum(1 for _key, score in ranked if score >= top_score * 0.72 and score >= 4.0)
    low_confidence = top_score < 5.0 or confidence < 0.42
    mixed = strong_categories >= 2 or margin_ratio < 0.18
    if low_confidence or mixed:
        competing = ", ".join(
            normalize_ranker_profile(key)[1]
            for key, score in ranked[:3]
            if score >= max(3.0, top_score * 0.6)
        )
        if mixed and competing:
            reason = f"Folder looks mixed between {competing}. Keeping General Use."
        else:
            reason = fallback_reason
        return RankerProfileSuggestion(
            default_key,
            default_label,
            reason,
            confidence=confidence,
            confidence_label=_confidence_label(confidence),
            is_mixed=mixed,
        )

    profile_key, profile_label = normalize_ranker_profile(top_key)
    top_evidence = ", ".join(_dedupe_preserve_order(evidence.get(top_key, ()))[:3])
    reason = f"Suggested {profile_label} ({_confidence_label(confidence).lower()} confidence)"
    if top_evidence:
        reason += f" because of {top_evidence}."
    else:
        reason += "."
    return RankerProfileSuggestion(
        profile_key,
        profile_label,
        reason,
        confidence=confidence,
        confidence_label=_confidence_label(confidence),
        is_mixed=False,
    )


def _apply_keyword_scores(path: str, scores: dict[str, float], evidence: dict[str, list[str]]) -> None:
    lowered = str(path or "").casefold()
    tokens = set(re.findall(r"[a-z0-9]+", lowered))
    for profile_key, weights in PROFILE_KEYWORD_WEIGHTS.items():
        for keyword, weight in weights.items():
            if keyword in tokens or keyword in lowered:
                scores[profile_key] += weight
                evidence[profile_key].append(f"keyword '{keyword}'")


def _ratio_points(value: float, *, start: float, full: float, weight: float) -> float:
    if value <= start:
        return 0.0
    span = max(0.001, full - start)
    scaled = min(1.0, (value - start) / span)
    return scaled * weight


def _confidence_label(confidence: float) -> str:
    if confidence >= 0.68:
        return "High"
    if confidence >= 0.5:
        return "Medium"
    return "Low"


def _dedupe_preserve_order(items: list[str] | tuple[str, ...]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for item in items:
        token = str(item or "").strip()
        if not token or token in seen:
            continue
        seen.add(token)
        ordered.append(token)
    return ordered


def _sample_profile_records(records: list[ImageRecord], *, limit: int) -> list[ImageRecord]:
    if limit <= 0 or len(records) <= limit:
        return list(records)
    sampled: list[ImageRecord] = []
    seen: set[str] = set()
    max_index = len(records) - 1
    for position in range(limit):
        index = round(position * max_index / max(1, limit - 1))
        record = records[index]
        if record.path in seen:
            continue
        seen.add(record.path)
        sampled.append(record)
    return sampled


def _profile_token(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").strip().casefold())


__all__ = [
    "DEFAULT_RANKER_PROFILE_KEY",
    "RankerProfileSuggestion",
    "normalize_ranker_profile",
    "ranker_profile_options",
    "suggest_training_profile",
]
