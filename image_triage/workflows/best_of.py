from __future__ import annotations

"""Best-of set planning for review and delivery workflows."""

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from ..ai_results import AIBundle, AIConfidenceBucket, find_ai_result_for_record
from ..models import ImageRecord, SessionAnnotation
from ..review_workflows import (
    REVIEW_ROUND_HERO,
    REVIEW_ROUND_THIRD_PASS,
    BurstRecommendation,
    ai_strength,
    normalize_review_round,
    review_round_label,
)
from ..scanner import normalized_path_key

if TYPE_CHECKING:
    from ..review_intelligence import ReviewIntelligenceBundle


BEST_OF_TOP_N = "top_n_overall"
BEST_OF_TOP_PER_GROUP = "top_per_group"
BEST_OF_BALANCED = "balanced_shortlist"


@dataclass(slots=True, frozen=True)
class BestOfSetCandidate:
    path: str
    score: float
    reason: str
    group_id: str = ""
    ai_bucket: str = ""


@dataclass(slots=True, frozen=True)
class BestOfSetPlan:
    strategy: str
    candidates: tuple[BestOfSetCandidate, ...]
    summary_lines: tuple[str, ...] = ()


def build_best_of_set_plan(
    records: list[ImageRecord],
    *,
    ai_bundle: AIBundle | None,
    review_bundle: "ReviewIntelligenceBundle | None",
    burst_recommendations: dict[str, BurstRecommendation],
    annotations_by_path: dict[str, SessionAnnotation] | None = None,
    limit: int = 12,
    strategy: str = BEST_OF_BALANCED,
) -> BestOfSetPlan:
    if not records:
        return BestOfSetPlan(strategy=strategy, candidates=(), summary_lines=("No images are loaded.",))

    annotations = annotations_by_path or {}
    review_lookup = review_bundle.insights_by_path if review_bundle is not None else {}

    ranked_rows: list[tuple[float, BestOfSetCandidate]] = []
    for record in records:
        ai_result = find_ai_result_for_record(ai_bundle, record) if ai_bundle is not None else None
        burst = burst_recommendations.get(record.path) or burst_recommendations.get(normalized_path_key(record.path))
        review_insight = review_lookup.get(record.path) or review_lookup.get(normalized_path_key(record.path))
        annotation = annotations.get(record.path, SessionAnnotation())
        score = ai_strength(ai_result) * 100.0
        reasons: list[str] = []
        group_id = ""

        if ai_result is not None:
            group_id = ai_result.group_id
            if ai_result.rank_in_group == 1 and ai_result.group_size > 1:
                score += 8.0
                reasons.append("AI group leader")
            if ai_result.confidence_bucket == AIConfidenceBucket.OBVIOUS_WINNER:
                score += 6.0
                reasons.append("Obvious winner")
            elif ai_result.confidence_bucket == AIConfidenceBucket.LIKELY_KEEPER:
                score += 3.0
                reasons.append("Likely keeper")
        if burst is not None and burst.is_recommended:
            score += 9.0
            reasons.append("Best frame in group")
            if not group_id:
                group_id = burst.group_id
        if review_insight is not None and getattr(review_insight, "is_duplicate", False):
            score -= 5.0
            reasons.append("Duplicate penalty")
        round_value = normalize_review_round(annotation.review_round)
        if round_value in {REVIEW_ROUND_THIRD_PASS, REVIEW_ROUND_HERO}:
            score += 4.0
            reasons.append(review_round_label(round_value))
        if annotation.winner:
            score += 5.0
            reasons.append("Already accepted")

        ranked_rows.append(
            (
                score,
                BestOfSetCandidate(
                    path=record.path,
                    score=round(score, 3),
                    reason=", ".join(reasons[:3]) or "Strong overall combined score",
                    group_id=group_id,
                    ai_bucket=ai_result.confidence_bucket.value if ai_result is not None else "",
                ),
            )
        )

    ranked_rows.sort(key=lambda item: (-item[0], Path(item[1].path).name.casefold()))
    chosen: list[BestOfSetCandidate] = []

    if strategy == BEST_OF_TOP_PER_GROUP:
        seen_groups: set[str] = set()
        for _score, candidate in ranked_rows:
            group_key = candidate.group_id or normalized_path_key(candidate.path)
            if group_key in seen_groups:
                continue
            seen_groups.add(group_key)
            chosen.append(candidate)
            if len(chosen) >= limit:
                break
    elif strategy == BEST_OF_TOP_N:
        chosen = [candidate for _score, candidate in ranked_rows[:limit]]
    else:
        seen_groups: set[str] = set()
        for _score, candidate in ranked_rows:
            if "Best frame" not in candidate.reason and "group leader" not in candidate.reason.lower():
                continue
            group_key = candidate.group_id or normalized_path_key(candidate.path)
            if group_key in seen_groups:
                continue
            seen_groups.add(group_key)
            chosen.append(candidate)
            if len(chosen) >= limit:
                break
        if len(chosen) < limit:
            chosen_keys = {normalized_path_key(candidate.path) for candidate in chosen}
            for _score, candidate in ranked_rows:
                if normalized_path_key(candidate.path) in chosen_keys:
                    continue
                chosen.append(candidate)
                chosen_keys.add(normalized_path_key(candidate.path))
                if len(chosen) >= limit:
                    break

    summary_lines = (
        f"Built {len(chosen)} proposed best-of pick(s) using {strategy.replace('_', ' ')}.",
        "Selections remain editable after the shortlist is applied.",
    )
    return BestOfSetPlan(strategy=strategy, candidates=tuple(chosen), summary_lines=summary_lines)
