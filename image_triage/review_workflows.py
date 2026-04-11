from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha1
from pathlib import Path
from statistics import mean
from typing import TYPE_CHECKING, Iterable
from uuid import uuid4

from .ai_results import AIBundle, AIConfidenceBucket, AIImageResult, find_ai_result_for_record
from .models import ImageRecord, SessionAnnotation
from .scanner import normalized_path_key

if TYPE_CHECKING:
    from .review_intelligence import ReviewInsight, ReviewIntelligenceBundle


REVIEW_ROUND_FIRST_PASS = "first_pass_rejects"
REVIEW_ROUND_SECOND_PASS = "second_pass_keepers"
REVIEW_ROUND_THIRD_PASS = "third_pass_finalists"
REVIEW_ROUND_HERO = "final_hero_selects"

REVIEW_ROUND_PRESETS: tuple[tuple[str, str, str], ...] = (
    (REVIEW_ROUND_FIRST_PASS, "First Pass Rejects", "Pass 1"),
    (REVIEW_ROUND_SECOND_PASS, "Second Pass Keepers", "Pass 2"),
    (REVIEW_ROUND_THIRD_PASS, "Third Pass Finalists", "Finalist"),
    (REVIEW_ROUND_HERO, "Final Hero Selects", "Hero"),
)

_ROUND_LABELS = {identifier: label for identifier, label, _short in REVIEW_ROUND_PRESETS}
_ROUND_SHORT_LABELS = {identifier: short for identifier, _label, short in REVIEW_ROUND_PRESETS}
_AI_BUCKET_RANK = {
    AIConfidenceBucket.LIKELY_REJECT: 0.0,
    AIConfidenceBucket.NEEDS_REVIEW: 0.45,
    AIConfidenceBucket.LIKELY_KEEPER: 0.74,
    AIConfidenceBucket.OBVIOUS_WINNER: 1.0,
}


@dataclass(slots=True, frozen=True)
class TasteProfile:
    event_count: int = 0
    detail_bias: float = 0.0
    ai_alignment_bias: float = 0.0
    summary_lines: tuple[str, ...] = ()


@dataclass(slots=True, frozen=True)
class BurstRecommendation:
    path: str
    group_id: str
    group_label: str
    group_size: int
    recommended_path: str
    rank_in_group: int
    score: float
    recommended_score: float
    is_recommended: bool = False
    reasons: tuple[str, ...] = ()


@dataclass(slots=True, frozen=True)
class RecordWorkflowInsight:
    review_round: str = ""
    review_round_label: str = ""
    best_in_group: bool = False
    burst_group_label: str = ""
    disagreement_level: str = ""
    disagreement_summary: str = ""
    summary_text: str = ""
    detail_lines: tuple[str, ...] = ()

    @property
    def has_round(self) -> bool:
        return bool(self.review_round)

    @property
    def has_disagreement(self) -> bool:
        return bool(self.disagreement_level)

    @property
    def disagreement_badge(self) -> str:
        if self.disagreement_level == "strong":
            return "AI Miss"
        if self.disagreement_level:
            return "AI Review"
        return ""


@dataclass(slots=True, frozen=True)
class CalibrationPair:
    left_path: str
    right_path: str
    prompt: str
    source_mode: str = "taste_calibration"
    group_id: str = ""
    group_label: str = ""
    left_label: str = ""
    right_label: str = ""


def normalize_review_round(value: str | None) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    normalized = text.casefold().replace("-", "_").replace(" ", "_")
    for identifier, label, short_label in REVIEW_ROUND_PRESETS:
        if normalized in {
            identifier,
            label.casefold().replace(" ", "_"),
            short_label.casefold().replace(" ", "_"),
        }:
            return identifier
    return text


def review_round_label(value: str | None) -> str:
    normalized = normalize_review_round(value)
    if not normalized:
        return ""
    return _ROUND_LABELS.get(normalized, normalized)


def review_round_short_label(value: str | None) -> str:
    normalized = normalize_review_round(value)
    if not normalized:
        return ""
    return _ROUND_SHORT_LABELS.get(normalized, normalized)


def build_taste_profile(correction_events: Iterable[dict[str, object]]) -> TasteProfile:
    detail_deltas: list[float] = []
    ai_deltas: list[float] = []
    event_count = 0

    for event in correction_events:
        payload = event.get("payload")
        if not isinstance(payload, dict):
            continue
        preferred_detail = _float_value(payload.get("preferred_detail_score"))
        other_detail = _float_value(payload.get("other_detail_score"))
        if preferred_detail is not None and other_detail is not None:
            detail_deltas.append((preferred_detail - other_detail) / 100.0)
        preferred_ai = _float_value(payload.get("preferred_ai_strength"))
        other_ai = _float_value(payload.get("other_ai_strength"))
        if preferred_ai is not None and other_ai is not None:
            ai_deltas.append(preferred_ai - other_ai)
        if preferred_detail is not None or preferred_ai is not None:
            event_count += 1

    detail_bias = _clamp(mean(detail_deltas) if detail_deltas else 0.0, -1.0, 1.0)
    ai_alignment_bias = _clamp(mean(ai_deltas) if ai_deltas else 0.0, -1.0, 1.0)

    summary_lines: list[str] = []
    if detail_bias >= 0.08:
        summary_lines.append("Recent picks lean toward crisper detail.")
    elif detail_bias <= -0.08:
        summary_lines.append("Recent picks are not strictly chasing the sharpest frame.")
    if ai_alignment_bias >= 0.08:
        summary_lines.append("You usually keep frames that already lead on AI score.")
    elif ai_alignment_bias <= -0.08:
        summary_lines.append("You often override the AI's favorite on close calls.")

    return TasteProfile(
        event_count=event_count,
        detail_bias=detail_bias,
        ai_alignment_bias=ai_alignment_bias,
        summary_lines=tuple(summary_lines[:2]),
    )


def build_burst_recommendations(
    records: list[ImageRecord],
    *,
    ai_bundle: AIBundle | None,
    review_bundle: "ReviewIntelligenceBundle | None",
    correction_events: Iterable[dict[str, object]],
) -> tuple[TasteProfile, dict[str, BurstRecommendation]]:
    taste_profile = build_taste_profile(correction_events)
    if review_bundle is None or not records:
        return taste_profile, {}

    records_by_path = {record.path: record for record in records}
    recommendations: dict[str, BurstRecommendation] = {}

    for group in review_bundle.groups:
        if group.kind not in {"burst", "similar"} or len(group.member_paths) < 2:
            continue

        member_paths = [path for path in group.member_paths if path in records_by_path]
        if len(member_paths) < 2:
            continue

        detail_values: list[float] = []
        exposure_values: list[float] = []
        ai_values: list[float] = []
        center_values: list[float] = []
        insights: list[ReviewInsight | None] = []
        ai_results: list[AIImageResult | None] = []

        center = (len(member_paths) - 1) / 2.0
        for index, path in enumerate(member_paths):
            review_insight = review_bundle.insight_for_path(path)
            ai_result = find_ai_result_for_record(ai_bundle, records_by_path[path]) if ai_bundle is not None else None
            insights.append(review_insight)
            ai_results.append(ai_result)
            detail_values.append(float(getattr(review_insight, "detail_score", 0.0) or 0.0))
            exposure_values.append(float(getattr(review_insight, "exposure_score", 0.0) or 0.0))
            ai_values.append(ai_strength(ai_result))
            center_values.append(_centrality_score(index, center, len(member_paths)))

        detail_norm = _normalize_group_values(detail_values)
        exposure_norm = _normalize_group_values(exposure_values)
        ai_norm = _normalize_group_values(ai_values, fallback=0.55)
        center_norm = _normalize_group_values(center_values, fallback=0.6)

        detail_weight = 0.50 + max(0.0, taste_profile.detail_bias) * 0.18
        ai_weight = 0.24 + max(0.0, taste_profile.ai_alignment_bias) * 0.18
        exposure_weight = 0.16
        center_weight = max(0.08, 1.0 - detail_weight - ai_weight - exposure_weight)
        total_weight = detail_weight + ai_weight + exposure_weight + center_weight
        detail_weight /= total_weight
        ai_weight /= total_weight
        exposure_weight /= total_weight
        center_weight /= total_weight

        scores: list[float] = []
        for index in range(len(member_paths)):
            score = (
                detail_norm[index] * detail_weight
                + ai_norm[index] * ai_weight
                + exposure_norm[index] * exposure_weight
                + center_norm[index] * center_weight
            ) * 100.0
            scores.append(score)

        ranked_indexes = sorted(
            range(len(member_paths)),
            key=lambda idx: (-scores[idx], member_paths[idx].casefold()),
        )
        leader_index = ranked_indexes[0]
        recommended_path = member_paths[leader_index]
        leader_score = scores[leader_index]
        leader_reasons = _build_burst_reason_lines(
            group_label=group.label,
            detail_score=detail_values[leader_index],
            exposure_score=exposure_values[leader_index],
            ai_result=ai_results[leader_index],
            taste_profile=taste_profile,
            detail_norm=detail_norm[leader_index],
            ai_norm=ai_norm[leader_index],
            center_norm=center_norm[leader_index],
        )

        rank_map = {index: rank + 1 for rank, index in enumerate(ranked_indexes)}
        for index, path in enumerate(member_paths):
            reasons = leader_reasons if index == leader_index else _build_trailing_reason_lines(
                group_label=group.label,
                leader_detail=detail_values[leader_index],
                current_detail=detail_values[index],
                leader_ai=ai_values[leader_index],
                current_ai=ai_values[index],
            )
            recommendation = BurstRecommendation(
                path=path,
                group_id=group.id,
                group_label=group.label,
                group_size=len(member_paths),
                recommended_path=recommended_path,
                rank_in_group=rank_map[index],
                score=round(scores[index], 3),
                recommended_score=round(leader_score, 3),
                is_recommended=index == leader_index,
                reasons=reasons,
            )
            recommendations[path] = recommendation
            recommendations[normalized_path_key(path)] = recommendation

    return taste_profile, recommendations


def build_record_workflow_insight(
    annotation: SessionAnnotation | None,
    ai_result: AIImageResult | None,
    burst_recommendation: BurstRecommendation | None,
    taste_profile: TasteProfile | None = None,
) -> RecordWorkflowInsight:
    resolved_annotation = annotation or SessionAnnotation()
    round_value = normalize_review_round(resolved_annotation.review_round)
    round_label = review_round_label(round_value)
    disagreement_level = disagreement_level_for(resolved_annotation, ai_result)
    disagreement_summary = disagreement_summary_for(resolved_annotation, ai_result, disagreement_level)

    summary_parts: list[str] = []
    detail_lines: list[str] = []
    supporting_lines: list[str] = []
    taste_line = ""

    if round_label:
        summary_parts.append(review_round_short_label(round_value))
        detail_lines.append(f"Review round: {round_label}.")

    if burst_recommendation is not None and burst_recommendation.group_size > 1:
        if burst_recommendation.is_recommended:
            summary_parts.append("Best Frame")
            detail_lines.append(
                f"Burst specialist pick: best frame in {burst_recommendation.group_label.lower()} ({burst_recommendation.rank_in_group}/{burst_recommendation.group_size})."
            )
            supporting_lines.extend(burst_recommendation.reasons[:2])
        elif burst_recommendation.group_label:
            detail_lines.append(
                f"Burst specialist currently prefers another frame in this {burst_recommendation.group_label.lower()}."
            )
            supporting_lines.extend(burst_recommendation.reasons[:1])

    if disagreement_summary:
        summary_parts.append("AI Disagreement")
        detail_lines.append(disagreement_summary)

    if taste_profile is not None and taste_profile.summary_lines and burst_recommendation is not None and burst_recommendation.is_recommended:
        taste_line = f"Taste profile: {taste_profile.summary_lines[0]}"
        detail_lines.append(taste_line)

    for line in supporting_lines:
        if len(detail_lines) >= 4:
            break
        if line in detail_lines:
            continue
        detail_lines.append(line)

    if taste_line and taste_line not in detail_lines:
        if len(detail_lines) >= 4:
            detail_lines = detail_lines[:3]
        detail_lines.append(taste_line)

    return RecordWorkflowInsight(
        review_round=round_value,
        review_round_label=round_label,
        best_in_group=bool(burst_recommendation and burst_recommendation.is_recommended),
        burst_group_label=burst_recommendation.group_label if burst_recommendation is not None else "",
        disagreement_level=disagreement_level,
        disagreement_summary=disagreement_summary,
        summary_text=" | ".join(summary_parts[:3]),
        detail_lines=tuple(detail_lines[:4]),
    )


def disagreement_level_for(annotation: SessionAnnotation | None, ai_result: AIImageResult | None) -> str:
    if annotation is None or ai_result is None:
        return ""

    round_value = normalize_review_round(annotation.review_round)
    high_user_pick = annotation.winner or annotation.rating >= 4 or round_value == REVIEW_ROUND_HERO
    high_user_reject = annotation.reject or round_value == REVIEW_ROUND_FIRST_PASS
    medium_user_pick = annotation.rating >= 3 or round_value in {REVIEW_ROUND_SECOND_PASS, REVIEW_ROUND_THIRD_PASS}

    if high_user_pick and ai_result.confidence_bucket == AIConfidenceBucket.LIKELY_REJECT:
        return "strong"
    if high_user_reject and ai_result.confidence_bucket in {
        AIConfidenceBucket.OBVIOUS_WINNER,
        AIConfidenceBucket.LIKELY_KEEPER,
    }:
        return "strong"
    if medium_user_pick and ai_result.confidence_bucket == AIConfidenceBucket.LIKELY_REJECT:
        return "moderate"
    if round_value == REVIEW_ROUND_THIRD_PASS and ai_result.confidence_bucket == AIConfidenceBucket.NEEDS_REVIEW:
        return "moderate"
    if round_value == REVIEW_ROUND_HERO and ai_result.confidence_bucket == AIConfidenceBucket.NEEDS_REVIEW:
        return "moderate"
    if annotation.rating <= 1 and ai_result.confidence_bucket == AIConfidenceBucket.OBVIOUS_WINNER:
        return "moderate"
    return ""


def disagreement_summary_for(
    annotation: SessionAnnotation | None,
    ai_result: AIImageResult | None,
    disagreement_level: str | None = None,
) -> str:
    if annotation is None or ai_result is None:
        return ""

    level = disagreement_level if disagreement_level is not None else disagreement_level_for(annotation, ai_result)
    if not level:
        return ""

    if annotation.winner:
        return f"You kept a frame AI bucketed as {ai_result.confidence_bucket_label.lower()}."
    if annotation.reject:
        return f"You rejected a frame AI bucketed as {ai_result.confidence_bucket_label.lower()}."
    round_value = normalize_review_round(annotation.review_round)
    round_label = review_round_label(round_value)
    if round_label:
        return f"You moved this image into {round_label.lower()} while AI remained {ai_result.confidence_bucket_label.lower()}."
    if annotation.rating >= 4:
        return f"You rated this highly even though AI marked it as {ai_result.confidence_bucket_label.lower()}."
    if annotation.rating <= 1:
        return f"You rated this poorly even though AI marked it as {ai_result.confidence_bucket_label.lower()}."
    return f"Your review state diverges from the AI's {ai_result.confidence_bucket_label.lower()} call."


def build_calibration_pairs(
    records: list[ImageRecord],
    *,
    ai_bundle: AIBundle | None,
    review_bundle: "ReviewIntelligenceBundle | None",
    burst_recommendations: dict[str, BurstRecommendation],
    limit: int = 8,
) -> tuple[CalibrationPair, ...]:
    if not records:
        return ()

    pairs: list[CalibrationPair] = []
    seen: set[tuple[str, str]] = set()
    records_by_path = {record.path: record for record in records}

    def add_pair(
        left_path: str,
        right_path: str,
        *,
        prompt: str,
        source_mode: str,
        group_id: str = "",
        group_label: str = "",
        left_label: str = "",
        right_label: str = "",
    ) -> None:
        if left_path == right_path or len(pairs) >= limit:
            return
        key = tuple(sorted((normalized_path_key(left_path), normalized_path_key(right_path))))
        if key in seen:
            return
        seen.add(key)
        if len(pairs) % 2 == 1:
            left_path, right_path = right_path, left_path
            left_label, right_label = right_label, left_label
        pairs.append(
            CalibrationPair(
                left_path=left_path,
                right_path=right_path,
                prompt=prompt,
                source_mode=source_mode,
                group_id=group_id,
                group_label=group_label,
                left_label=left_label,
                right_label=right_label,
            )
        )

    if review_bundle is not None:
        for group in review_bundle.groups:
            if group.kind not in {"burst", "similar"} or len(group.member_paths) < 2:
                continue
            ranked = sorted(
                [burst_recommendations.get(path) for path in group.member_paths],
                key=lambda item: (-(item.score if item is not None else 0.0), item.path.casefold() if item is not None else ""),
            )
            ranked = [item for item in ranked if item is not None]
            if len(ranked) < 2:
                continue
            add_pair(
                ranked[0].path,
                ranked[1].path,
                prompt=f"Which frame feels strongest for this {group.label.lower()}?",
                source_mode="taste_calibration",
                group_id=group.id,
                group_label=group.label,
                left_label="Best guess",
                right_label="Closest challenger",
            )
            if len(pairs) >= limit:
                return tuple(pairs)

    if ai_bundle is not None:
        grouped_records: dict[str, list[tuple[ImageRecord, AIImageResult]]] = {}
        for record in records:
            result = find_ai_result_for_record(ai_bundle, record)
            if result is None or result.group_size < 2:
                continue
            grouped_records.setdefault(result.group_id, []).append((record, result))
        for group_id, members in grouped_records.items():
            members.sort(key=lambda item: (item[1].rank_in_group, -item[1].score, item[0].name.casefold()))
            if len(members) < 2:
                continue
            top_gap = members[0][1].score_gap_to_next if members[0][1].score_gap_to_next is not None else 0.0
            if top_gap >= 0.12 and members[0][1].confidence_bucket == AIConfidenceBucket.OBVIOUS_WINNER:
                continue
            add_pair(
                members[0][0].path,
                members[1][0].path,
                prompt="Which of these close AI finalists matches your taste better?",
                source_mode="taste_calibration",
                group_id=group_id,
                group_label="AI Group",
                left_label="AI lead",
                right_label="Runner-up",
            )
            if len(pairs) >= limit:
                return tuple(pairs)

    for index in range(len(records) - 1):
        left = records[index]
        right = records[index + 1]
        left_result = find_ai_result_for_record(ai_bundle, left) if ai_bundle is not None else None
        right_result = find_ai_result_for_record(ai_bundle, right) if ai_bundle is not None else None
        if left_result is None and right_result is None:
            continue
        add_pair(
            left.path,
            right.path,
            prompt="Pick the frame that feels more like your keep.",
            source_mode="taste_calibration",
            left_label="Option A",
            right_label="Option B",
        )
        if len(pairs) >= limit:
            break
    return tuple(pairs)


def stable_image_id_for_path(folder: str | Path, path: str | Path) -> str:
    folder_path = Path(folder).expanduser().resolve()
    target_path = Path(path).expanduser().resolve()
    try:
        relative_path = target_path.relative_to(folder_path).as_posix()
    except ValueError:
        relative_path = target_path.name
    normalized = relative_path.replace("\\", "/").strip().lstrip("./")
    return sha1(normalized.encode("utf-8"), usedforsecurity=False).hexdigest()


def build_pairwise_label_payload(
    *,
    folder: str | Path,
    left_path: str,
    right_path: str,
    preferred_path: str,
    source_mode: str,
    cluster_id: str = "",
    annotator_id: str = "",
) -> dict[str, object]:
    left_id = stable_image_id_for_path(folder, left_path)
    right_id = stable_image_id_for_path(folder, right_path)
    preferred_id = stable_image_id_for_path(folder, preferred_path)
    decision = "left_better" if normalized_path_key(preferred_path) == normalized_path_key(left_path) else "right_better"
    return {
        "label_id": str(uuid4()),
        "image_a_id": left_id,
        "image_b_id": right_id,
        "preferred_image_id": preferred_id,
        "decision": decision,
        "source_mode": source_mode,
        "cluster_id": cluster_id or None,
        "timestamp": current_timestamp(),
        "annotator_id": annotator_id or None,
    }


def current_timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def ai_strength(result: AIImageResult | None) -> float:
    if result is None:
        return 0.45
    normalized = result.normalized_score
    if normalized is not None:
        return _clamp(normalized / 100.0, 0.0, 1.0)
    return _AI_BUCKET_RANK.get(result.confidence_bucket, 0.45)


def _build_burst_reason_lines(
    *,
    group_label: str,
    detail_score: float,
    exposure_score: float,
    ai_result: AIImageResult | None,
    taste_profile: TasteProfile,
    detail_norm: float,
    ai_norm: float,
    center_norm: float,
) -> tuple[str, ...]:
    reasons: list[str] = [f"Best overall signal inside this {group_label.lower()}."]
    if detail_score >= 60.0 or detail_norm >= 0.72:
        reasons.append("Detail retention is stronger than the nearby frames.")
    if exposure_score >= 60.0:
        reasons.append("Exposure balance stayed cleaner across highlights and shadows.")
    if ai_result is not None and ai_result.rank_in_group == 1 and ai_norm >= 0.68:
        reasons.append("AI also ranked it at the top of the group.")
    elif center_norm >= 0.75:
        reasons.append("It sits near the steadier middle of the sequence.")
    if taste_profile.detail_bias >= 0.08 and detail_norm >= 0.72:
        reasons.append("That matches your recent preference for sharper frames.")
    elif taste_profile.ai_alignment_bias >= 0.08 and ai_norm >= 0.68:
        reasons.append("That lines up with the AI leads you have tended to keep.")
    return tuple(reasons[:3])


def _build_trailing_reason_lines(
    *,
    group_label: str,
    leader_detail: float,
    current_detail: float,
    leader_ai: float,
    current_ai: float,
) -> tuple[str, ...]:
    reasons = [f"The current {group_label.lower()} leader looks stronger overall."]
    if leader_detail - current_detail >= 8.0:
        reasons.append("This frame looks softer than the current group leader.")
    elif leader_ai - current_ai >= 0.10:
        reasons.append("Its AI score trails the current group leader.")
    return tuple(reasons[:2])


def _normalize_group_values(values: list[float], *, fallback: float = 0.5) -> list[float]:
    if not values:
        return []
    maximum = max(values)
    minimum = min(values)
    span = maximum - minimum
    if span <= 1e-9:
        return [fallback for _ in values]
    return [max(0.0, min(1.0, (value - minimum) / span)) for value in values]


def _centrality_score(index: int, center: float, count: int) -> float:
    if count <= 1:
        return 1.0
    distance = abs(index - center)
    return max(0.0, 1.0 - (distance / max(1.0, center + 0.5)))


def _float_value(value: object) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))
