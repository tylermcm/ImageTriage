"""Structured signal records for the next-generation culling stack."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields, is_dataclass
from typing import Any, Dict, List, Mapping, Optional


SIGNAL_SCHEMA_VERSION = "culling_signals.v1"


@dataclass(frozen=True)
class LayerStatus:
    """Availability/status for one signal layer."""

    layer_id: str
    display_name: str
    enabled: bool
    available: bool
    status: str = "not_analyzed"
    backend: str = ""
    reason: str = ""


@dataclass(frozen=True)
class DinoSignals:
    """DINO-derived grouping and similarity signals."""

    cluster_id: Optional[str] = None
    group_size: int = 1
    group_position: Optional[int] = None
    group_rank_by_centrality: Optional[int] = None
    centrality_score: Optional[float] = None
    nearest_neighbor_similarity: Optional[float] = None
    duplicate_risk: str = "not_analyzed"
    status: str = "not_analyzed"


@dataclass(frozen=True)
class TechnicalSignals:
    """Deterministic technical quality signals."""

    detail_score: Optional[float] = None
    sharpness_score: Optional[float] = None
    focus_score: Optional[float] = None
    motion_blur_score: Optional[float] = None
    noise_score: Optional[float] = None
    exposure_score: Optional[float] = None
    exposure_status: str = "not_analyzed"
    highlight_clip_ratio: Optional[float] = None
    shadow_clip_ratio: Optional[float] = None
    contrast_score: Optional[float] = None
    confidence: str = "not_analyzed"
    status: str = "not_analyzed"
    reason: str = ""


@dataclass(frozen=True)
class FaceSignals:
    """Face and eye quality signals from a dedicated specialist model."""

    face_count: int = 0
    primary_face_confidence: Optional[float] = None
    face_sharpness_score: Optional[float] = None
    eye_open_score: Optional[float] = None
    blink_detected: Optional[bool] = None
    status: str = "not_analyzed"
    backend: str = ""
    reason: str = ""


@dataclass(frozen=True)
class SubjectSignals:
    """Subject/object signals from a detection specialist."""

    primary_subject_label: Optional[str] = None
    subject_confidence: Optional[float] = None
    subject_box_area_ratio: Optional[float] = None
    subject_centering_score: Optional[float] = None
    detected_labels: List[str] = field(default_factory=list)
    face: FaceSignals = field(default_factory=FaceSignals)
    status: str = "not_analyzed"
    backend: str = ""
    reason: str = ""


@dataclass(frozen=True)
class AestheticSignals:
    """Aesthetic/composition signals from a specialist model or heuristic."""

    aesthetic_score: Optional[float] = None
    composition_score: Optional[float] = None
    clutter_score: Optional[float] = None
    status: str = "not_analyzed"
    backend: str = ""
    reason: str = ""


@dataclass(frozen=True)
class SemanticSignals:
    """Scene/profile routing signals."""

    scene_type: Optional[str] = None
    domain_profile: Optional[str] = None
    tags: List[str] = field(default_factory=list)
    status: str = "not_analyzed"
    backend: str = ""
    reason: str = ""


@dataclass(frozen=True)
class PersonalPreferenceSignals:
    """Output of the personalized preference combiner."""

    profile_name: str = "General Use"
    score: Optional[float] = None
    confidence: str = "not_analyzed"
    feature_values: Dict[str, float] = field(default_factory=dict)
    learned_adjustment: Optional[float] = None
    status: str = "not_analyzed"


@dataclass(frozen=True)
class FinalDecision:
    """Final production culling decision."""

    score: Optional[float] = None
    bucket: str = "needs_review"
    rank_in_group: Optional[int] = None
    reasons: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    scoring_strategy: str = "not_scored"


@dataclass(frozen=True)
class ImageSignalRecord:
    """All known machine-readable signals for one image."""

    image_id: str
    file_path: str
    relative_path: str = ""
    file_name: str = ""
    schema_version: str = SIGNAL_SCHEMA_VERSION
    dino: DinoSignals = field(default_factory=DinoSignals)
    technical: TechnicalSignals = field(default_factory=TechnicalSignals)
    subject: SubjectSignals = field(default_factory=SubjectSignals)
    aesthetic: AestheticSignals = field(default_factory=AestheticSignals)
    semantic: SemanticSignals = field(default_factory=SemanticSignals)
    personal: PersonalPreferenceSignals = field(default_factory=PersonalPreferenceSignals)
    final: FinalDecision = field(default_factory=FinalDecision)
    layer_statuses: List[LayerStatus] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return _to_jsonable(asdict(self))


def record_to_dict(record: ImageSignalRecord) -> Dict[str, Any]:
    return record.to_dict()


def record_from_dict(payload: Mapping[str, Any]) -> ImageSignalRecord:
    """Build a signal record from saved JSON, ignoring unknown future fields."""

    def build(cls, value: object):
        source = value if isinstance(value, dict) else {}
        allowed = {item.name for item in fields(cls)}
        kwargs = {key: source[key] for key in allowed if key in source}
        return cls(**kwargs)

    subject_payload = payload.get("subject") if isinstance(payload.get("subject"), dict) else {}
    face_payload = subject_payload.get("face") if isinstance(subject_payload.get("face"), dict) else {}
    subject = build(SubjectSignals, subject_payload)
    subject = SubjectSignals(
        primary_subject_label=subject.primary_subject_label,
        subject_confidence=subject.subject_confidence,
        subject_box_area_ratio=subject.subject_box_area_ratio,
        subject_centering_score=subject.subject_centering_score,
        detected_labels=list(subject.detected_labels or []),
        face=build(FaceSignals, face_payload),
        status=subject.status,
        backend=subject.backend,
        reason=subject.reason,
    )
    return ImageSignalRecord(
        image_id=str(payload.get("image_id") or ""),
        file_path=str(payload.get("file_path") or ""),
        relative_path=str(payload.get("relative_path") or ""),
        file_name=str(payload.get("file_name") or ""),
        schema_version=str(payload.get("schema_version") or SIGNAL_SCHEMA_VERSION),
        dino=build(DinoSignals, payload.get("dino")),
        technical=build(TechnicalSignals, payload.get("technical")),
        subject=subject,
        aesthetic=build(AestheticSignals, payload.get("aesthetic")),
        semantic=build(SemanticSignals, payload.get("semantic")),
        personal=build(PersonalPreferenceSignals, payload.get("personal")),
        final=build(FinalDecision, payload.get("final")),
        layer_statuses=[
            build(LayerStatus, item)
            for item in payload.get("layer_statuses", [])
            if isinstance(item, dict)
        ],
    )


def _to_jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return _to_jsonable(asdict(value))
    if isinstance(value, dict):
        return {str(key): _to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_jsonable(item) for item in value]
    return value
