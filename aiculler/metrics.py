from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence


KEEPER_LABELS = {"hero", "portfolio", "strong", "keep", "keeper", "ai pick", "good", "k", "yes", "1"}
REJECT_LABELS = {"weak", "reject", "bad", "r", "no", "0"}
MAYBE_LABELS = {"maybe", "needs review"}

METRIC_KEYS = (
    "score_fit_percent",
    "mae",
    "keeper_recall",
    "false_reject_rate",
    "false_keep_rate",
    "top_10_recall",
    "top_20_recall",
    "top_30_recall",
    "duplicate_winner_agreement",
    "duplicate_winner_top2_agreement",
    "review_reduction_percent",
    "reject_rescue_rate",
    "pick_demotion_rate",
    "rank_correlation",
    "labeled_image_count",
    "folder_count",
    "cluster_count",
    "override_count",
)


@dataclass(frozen=True)
class CullingMetricRecord:
    image_id: int
    label: str
    score: float
    cluster_id: int | None = None
    folder_id: str | None = None


def score_fit_percent(mae: float | None) -> float | None:
    if mae is None:
        return None
    return max(0.0, min(100.0, (1.0 - float(mae)) * 100.0))


def is_keeper_label(label: str | None) -> bool:
    return str(label or "").strip().lower() in KEEPER_LABELS


def is_reject_label(label: str | None) -> bool:
    return str(label or "").strip().lower() in REJECT_LABELS


def ai_bucket_for_percentile(percentile: float) -> str:
    if percentile >= 90.0:
        return "ai pick"
    if percentile <= 80.0:
        return "reject"
    return "needs review"


def compute_culling_metrics(
    records: Sequence[CullingMetricRecord],
    *,
    mae: float | None = None,
    overrides: Iterable[Mapping[str, object]] = (),
) -> dict[str, object]:
    result: dict[str, object] = {key: None for key in METRIC_KEYS}
    result["score_fit_percent"] = score_fit_percent(mae)
    result["mae"] = mae
    result["labeled_image_count"] = len(records)
    folder_ids = {record.folder_id for record in records if record.folder_id}
    result["folder_count"] = len(folder_ids) if folder_ids else None
    cluster_ids = {record.cluster_id for record in records if record.cluster_id is not None}
    result["cluster_count"] = len(cluster_ids) if cluster_ids else None

    override_rows = list(overrides)
    result["override_count"] = len(override_rows)
    _add_override_rates(result, override_rows)

    if not records:
        return result

    ordered = sorted(records, key=lambda record: (-record.score, record.image_id))
    rank_by_id = {record.image_id: index for index, record in enumerate(ordered)}
    bucket_by_id = _bucket_records_by_percentile(ordered)

    keeper_ids = {record.image_id for record in records if is_keeper_label(record.label)}
    reject_ids = {record.image_id for record in records if is_reject_label(record.label)}
    if keeper_ids:
        false_rejects = sum(1 for image_id in keeper_ids if bucket_by_id.get(image_id) == "reject")
        result["false_reject_rate"] = false_rejects / len(keeper_ids)
        result["keeper_recall"] = (len(keeper_ids) - false_rejects) / len(keeper_ids)
        result["top_10_recall"] = _top_k_recall(keeper_ids, rank_by_id, len(ordered), 0.10)
        result["top_20_recall"] = _top_k_recall(keeper_ids, rank_by_id, len(ordered), 0.20)
        result["top_30_recall"] = _top_k_recall(keeper_ids, rank_by_id, len(ordered), 0.30)
    if reject_ids:
        false_keeps = sum(1 for image_id in reject_ids if bucket_by_id.get(image_id) in {"keeper", "ai pick"})
        result["false_keep_rate"] = false_keeps / len(reject_ids)

    review_count = sum(1 for bucket in bucket_by_id.values() if bucket in {"needs review", "keeper"})
    result["review_reduction_percent"] = (1.0 - (review_count / len(ordered))) * 100.0
    result["rank_correlation"] = _rank_correlation(records)
    _add_duplicate_metrics(result, records, rank_by_id)
    return result


def format_metric(value: object, *, percent: bool = False) -> str:
    if value is None:
        return "N/A"
    if isinstance(value, (int, float)):
        if percent:
            return f"{float(value) * 100.0:.1f}%"
        return f"{float(value):.3f}"
    return str(value)


def _bucket_records_by_percentile(ordered: Sequence[CullingMetricRecord]) -> dict[int, str]:
    count = len(ordered)
    buckets: dict[int, str] = {}
    if count == 1:
        buckets[ordered[0].image_id] = "ai pick"
        return buckets
    for index, record in enumerate(ordered):
        percentile = 100.0 * (count - index - 1) / (count - 1)
        buckets[record.image_id] = ai_bucket_for_percentile(percentile)
    return buckets


def _top_k_recall(keeper_ids: set[int], rank_by_id: dict[int, int], total: int, fraction: float) -> float | None:
    if not keeper_ids or total <= 0:
        return None
    cutoff = max(1, int(math.ceil(total * fraction)))
    found = sum(1 for image_id in keeper_ids if rank_by_id.get(image_id, total) < cutoff)
    return found / len(keeper_ids)


def _add_duplicate_metrics(result: dict[str, object], records: Sequence[CullingMetricRecord], rank_by_id: dict[int, int]) -> None:
    grouped: dict[int, list[CullingMetricRecord]] = {}
    for record in records:
        if record.cluster_id is not None:
            grouped.setdefault(record.cluster_id, []).append(record)
    exact_total = 0
    exact_hits = 0
    top2_total = 0
    top2_hits = 0
    for cluster_records in grouped.values():
        if len(cluster_records) < 2:
            continue
        winners = [record for record in cluster_records if str(record.label).strip().lower() in {"hero", "portfolio"}]
        if not winners:
            continue
        user_winner = max(winners, key=lambda record: record.score)
        ai_ordered = sorted(cluster_records, key=lambda record: rank_by_id.get(record.image_id, 10**9))
        exact_total += 1
        top2_total += 1
        if ai_ordered and ai_ordered[0].image_id == user_winner.image_id:
            exact_hits += 1
        if any(record.image_id == user_winner.image_id for record in ai_ordered[:2]):
            top2_hits += 1
    if exact_total:
        result["duplicate_winner_agreement"] = exact_hits / exact_total
    if top2_total:
        result["duplicate_winner_top2_agreement"] = top2_hits / top2_total


def _add_override_rates(result: dict[str, object], overrides: Sequence[Mapping[str, object]]) -> None:
    final_training = [
        row
        for row in overrides
        if int(row.get("is_final") or 0) == 1 and int(row.get("ignored_for_training") or 0) == 0
    ]
    if not final_training:
        return
    counts = Counter(str(row.get("override_type") or "") for row in final_training)
    total = len(final_training)
    result["reject_rescue_rate"] = counts.get("reject_rescue", 0) / total
    result["pick_demotion_rate"] = counts.get("pick_demotion", 0) / total


def _rank_correlation(records: Sequence[CullingMetricRecord]) -> float | None:
    pairs = [(record.score, _label_value(record.label)) for record in records if _label_value(record.label) is not None]
    if len(pairs) < 3:
        return None
    scores = [item[0] for item in pairs]
    labels = [float(item[1]) for item in pairs]
    if len(set(scores)) < 2 or len(set(labels)) < 2:
        return None
    return _pearson(_ranks(scores), _ranks(labels))


def _label_value(label: str | None) -> float | None:
    text = str(label or "").strip().lower()
    values = {
        "hero": 1.0,
        "portfolio": 1.0,
        "strong": 0.8,
        "keep": 0.75,
        "keeper": 0.75,
        "ai pick": 0.75,
        "good": 0.75,
        "k": 0.75,
        "yes": 0.75,
        "1": 0.75,
        "maybe": 0.5,
        "needs review": 0.5,
        "weak": 0.25,
        "reject": 0.0,
        "bad": 0.0,
        "r": 0.0,
        "no": 0.0,
        "0": 0.0,
    }
    return values.get(text)


def _ranks(values: Sequence[float]) -> list[float]:
    indexed = sorted(enumerate(values), key=lambda item: item[1])
    ranks = [0.0] * len(values)
    index = 0
    while index < len(indexed):
        end = index + 1
        while end < len(indexed) and indexed[end][1] == indexed[index][1]:
            end += 1
        rank = (index + end - 1) / 2.0
        for original_index, _value in indexed[index:end]:
            ranks[original_index] = rank
        index = end
    return ranks


def _pearson(left: Sequence[float], right: Sequence[float]) -> float | None:
    if len(left) != len(right) or len(left) < 2:
        return None
    mean_left = sum(left) / len(left)
    mean_right = sum(right) / len(right)
    numerator = sum((a - mean_left) * (b - mean_right) for a, b in zip(left, right))
    left_sq = sum((a - mean_left) ** 2 for a in left)
    right_sq = sum((b - mean_right) ** 2 for b in right)
    denominator = math.sqrt(left_sq * right_sq)
    if denominator == 0.0:
        return None
    return numerator / denominator
