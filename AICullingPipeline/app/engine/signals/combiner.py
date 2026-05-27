"""Transparent scoring combiner for personalized culling."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Dict, Iterable, Mapping

from app.engine.signals.models import FinalDecision, ImageSignalRecord, PersonalPreferenceSignals


FEATURE_NAMES = (
    "dino_centrality",
    "dino_group_rank",
    "technical_detail",
    "technical_sharpness",
    "technical_exposure",
    "technical_contrast",
    "technical_noise_inverse",
    "subject_confidence",
    "subject_size",
    "subject_centering",
    "face_quality",
    "eye_open",
    "aesthetic",
    "composition",
)


@dataclass(frozen=True)
class ScoringProfile:
    """Weights and gates for one culling domain/profile."""

    name: str
    weights: Dict[str, float]
    hard_gates: Dict[str, float] = field(default_factory=dict)
    keep_threshold: float = 0.72
    reject_threshold: float = 0.34
    shortlist_size: int = 3


DEFAULT_PROFILES: Dict[str, ScoringProfile] = {
    "General Use": ScoringProfile(
        name="General Use",
        weights={
            "dino_centrality": 0.25,
            "technical_detail": 0.18,
            "technical_sharpness": 0.18,
            "technical_exposure": 0.12,
            "technical_contrast": 0.06,
            "technical_noise_inverse": 0.06,
            "subject_confidence": 0.07,
            "aesthetic": 0.08,
        },
    ),
    "Wildlife": ScoringProfile(
        name="Wildlife",
        weights={
            "dino_centrality": 0.18,
            "technical_detail": 0.24,
            "technical_sharpness": 0.24,
            "subject_confidence": 0.14,
            "subject_size": 0.10,
            "technical_exposure": 0.06,
            "aesthetic": 0.04,
        },
        hard_gates={"technical_sharpness": 0.25},
    ),
    "Portrait": ScoringProfile(
        name="Portrait",
        weights={
            "face_quality": 0.25,
            "eye_open": 0.22,
            "technical_sharpness": 0.16,
            "technical_exposure": 0.12,
            "subject_centering": 0.10,
            "aesthetic": 0.10,
            "dino_centrality": 0.05,
        },
        hard_gates={"face_quality": 0.25},
    ),
    "Landscape": ScoringProfile(
        name="Landscape",
        weights={
            "technical_detail": 0.22,
            "technical_exposure": 0.20,
            "technical_contrast": 0.12,
            "aesthetic": 0.20,
            "composition": 0.12,
            "dino_centrality": 0.14,
        },
    ),
}


def choose_profile(name: str | None) -> ScoringProfile:
    if name and name in DEFAULT_PROFILES:
        return DEFAULT_PROFILES[name]
    return DEFAULT_PROFILES["General Use"]


def apply_combiner(
    records: Mapping[str, ImageSignalRecord],
    *,
    profile: ScoringProfile,
    learned_weights: Mapping[str, float] | None = None,
) -> Dict[str, ImageSignalRecord]:
    """Apply transparent profile scoring to all records."""

    learned_weights = dict(learned_weights or {})
    updated: Dict[str, ImageSignalRecord] = {}
    raw_scores: Dict[str, float] = {}
    feature_map: Dict[str, Dict[str, float]] = {}

    for image_id, record in records.items():
        features = feature_values(record)
        feature_map[image_id] = features
        raw_scores[image_id] = weighted_score(features, profile.weights, learned_weights)

    group_members: Dict[str, list[str]] = {}
    for image_id, record in records.items():
        group_id = record.dino.cluster_id or image_id
        group_members.setdefault(group_id, []).append(image_id)

    rank_by_image: Dict[str, int] = {}
    for members in group_members.values():
        ordered = sorted(
            members,
            key=lambda image_id: (
                -raw_scores[image_id],
                records[image_id].file_name.casefold(),
                image_id,
            ),
        )
        for rank, image_id in enumerate(ordered, start=1):
            rank_by_image[image_id] = rank

    for image_id, record in records.items():
        features = feature_map[image_id]
        score = raw_scores[image_id]
        warnings = _gate_warnings(features, profile.hard_gates)
        adjusted_score = min(score, profile.reject_threshold) if warnings else score
        updated[image_id] = replace(
            record,
            personal=PersonalPreferenceSignals(
                profile_name=profile.name,
                score=adjusted_score,
                confidence=_confidence_label(record),
                feature_values=features,
                learned_adjustment=_learned_adjustment(features, learned_weights),
                status="scored",
            ),
            final=FinalDecision(
                score=adjusted_score,
                bucket=_bucket(adjusted_score, profile),
                rank_in_group=rank_by_image.get(image_id),
                reasons=_reasons(record, features, adjusted_score),
                warnings=warnings,
                scoring_strategy="transparent_signal_combiner.v1",
            ),
        )
    return updated


def feature_values(record: ImageSignalRecord) -> Dict[str, float]:
    """Convert one signal record into normalized combiner features."""

    return {
        "dino_centrality": _bounded(record.dino.centrality_score),
        "dino_group_rank": _rank_feature(record.dino.group_rank_by_centrality, record.dino.group_size),
        "sequence_position": _rank_feature(
            None if record.dino.group_position is None else record.dino.group_position + 1,
            record.dino.group_size,
        ),
        "technical_detail": _bounded(record.technical.detail_score),
        "technical_sharpness": _bounded(record.technical.sharpness_score),
        "technical_exposure": _bounded(record.technical.exposure_score),
        "technical_contrast": _bounded(record.technical.contrast_score, scale=0.35),
        "technical_noise_inverse": 1.0 - _bounded(record.technical.noise_score),
        "subject_confidence": _bounded(record.subject.subject_confidence),
        "subject_size": _bounded(record.subject.subject_box_area_ratio, scale=0.35),
        "subject_centering": _bounded(record.subject.subject_centering_score),
        "face_quality": _bounded(record.subject.face.face_sharpness_score),
        "eye_open": _bounded(record.subject.face.eye_open_score),
        "aesthetic": _bounded(record.aesthetic.aesthetic_score),
        "composition": _bounded(record.aesthetic.composition_score),
    }


def pairwise_feature_delta(
    preferred: ImageSignalRecord,
    other: ImageSignalRecord,
) -> Dict[str, float]:
    """Build the training row used by a future personal combiner trainer."""

    preferred_features = feature_values(preferred)
    other_features = feature_values(other)
    return {
        feature: preferred_features.get(feature, 0.0) - other_features.get(feature, 0.0)
        for feature in FEATURE_NAMES
    }


def weighted_score(
    features: Mapping[str, float],
    base_weights: Mapping[str, float],
    learned_weights: Mapping[str, float] | None = None,
) -> float:
    weights = dict(base_weights)
    for key, value in (learned_weights or {}).items():
        weights[key] = weights.get(key, 0.0) + float(value)
    total_weight = sum(abs(value) for value in weights.values())
    if total_weight <= 0:
        return 0.0
    score = sum(features.get(key, 0.0) * weight for key, weight in weights.items()) / total_weight
    return _bounded(score)


def _bounded(value: float | None, *, scale: float = 1.0) -> float:
    if value is None:
        return 0.0
    try:
        numeric = float(value) / scale
    except (TypeError, ValueError, ZeroDivisionError):
        return 0.0
    return max(0.0, min(1.0, numeric))


def _rank_feature(rank: int | None, group_size: int | None) -> float:
    if rank is None or not group_size or group_size <= 1:
        return 1.0
    return max(0.0, min(1.0, 1.0 - ((rank - 1) / max(1, group_size - 1))))


def _gate_warnings(features: Mapping[str, float], hard_gates: Mapping[str, float]) -> list[str]:
    warnings: list[str] = []
    for key, threshold in hard_gates.items():
        if features.get(key, 0.0) < float(threshold):
            warnings.append(f"{key} below profile minimum")
    return warnings


def _bucket(score: float, profile: ScoringProfile) -> str:
    if score >= profile.keep_threshold:
        return "keep"
    if score <= profile.reject_threshold:
        return "reject"
    return "needs_review"


def _confidence_label(record: ImageSignalRecord) -> str:
    analyzed = 0
    if record.dino.status == "analyzed":
        analyzed += 1
    if record.technical.status == "analyzed":
        analyzed += 1
    if record.subject.status == "analyzed" or record.subject.face.status == "analyzed":
        analyzed += 1
    if record.aesthetic.status == "analyzed":
        analyzed += 1
    if analyzed >= 4:
        return "high"
    if analyzed >= 2:
        return "medium"
    return "low"


def _learned_adjustment(features: Mapping[str, float], learned_weights: Mapping[str, float]) -> float | None:
    if not learned_weights:
        return None
    return sum(features.get(key, 0.0) * float(value) for key, value in learned_weights.items())


def _reasons(record: ImageSignalRecord, features: Mapping[str, float], score: float) -> list[str]:
    reasons: list[str] = []
    if record.dino.group_rank_by_centrality == 1 and (record.dino.group_size or 1) > 1:
        reasons.append("Most central frame in DINO group")
    if features.get("technical_sharpness", 0.0) >= 0.70:
        reasons.append("Strong detail/sharpness signal")
    if record.technical.exposure_status == "properly_exposed":
        reasons.append("Properly exposed")
    if features.get("face_quality", 0.0) >= 0.70:
        reasons.append("Strong face-quality signal")
    if features.get("aesthetic", 0.0) >= 0.70:
        reasons.append("Strong aesthetic signal")
    if not reasons:
        reasons.append("Mixed or incomplete signals")
    if score <= 0.34:
        reasons.append("Low combined score")
    return reasons


def summarize_feature_deltas(rows: Iterable[Dict[str, float]]) -> Dict[str, float]:
    """Small diagnostic helper for future preference-trainer reports."""

    materialized = list(rows)
    if not materialized:
        return {feature: 0.0 for feature in FEATURE_NAMES}
    return {
        feature: sum(row.get(feature, 0.0) for row in materialized) / len(materialized)
        for feature in FEATURE_NAMES
    }
