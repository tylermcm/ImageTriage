from __future__ import annotations

import csv
import json
import os
from hashlib import sha1
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Iterable

from .models import ImageRecord
from .scanner import normalized_path_key

if TYPE_CHECKING:
    from .review_intelligence import ReviewInsight


PREFERRED_EXPORT_FILENAMES = (
    "ranked_clusters_export.csv",
    "scored_clusters.csv",
    "ranked_clusters.csv",
)

SUMMARY_FILENAMES = (
    "ranked_export_summary.json",
    "cluster_summary.json",
    "ranked_clusters_summary.json",
)

HTML_REPORT_FILENAMES = (
    "ranked_clusters_report.html",
    "clusters_report.html",
)


def _fast_path_key(path: str | Path) -> str:
    return os.path.normpath(str(path)).casefold()


class AIConfidenceBucket(str, Enum):
    OBVIOUS_WINNER = "obvious_winner"
    LIKELY_KEEPER = "likely_keeper"
    NEEDS_REVIEW = "needs_review"
    LIKELY_REJECT = "likely_reject"


class AICullBucket(str, Enum):
    AI_PICK = "ai_pick"
    REJECT = "reject"
    KEEPER = "keeper"
    NEEDS_REVIEW = "needs_review"
    UNRATED = "unrated"


_EXTREME_LOW_DETAIL_THRESHOLD = 8.0
_LOW_DETAIL_REVIEW_THRESHOLD = 14.0
_LOW_DETAIL_REJECT_SAFE_PERCENTILE = 92.0
_WEAK_CLUSTER_LEADER_REJECT_PERCENTILE = 15.0
_OBVIOUS_WINNER_NORMALIZED_THRESHOLD = 78.0
_OBVIOUS_WINNER_GAP_THRESHOLD = 0.12
_LIKELY_KEEPER_GROUP_NORMALIZED_THRESHOLD = 58.0
_LIKELY_KEEPER_GROUP_PERCENTILE_THRESHOLD = 70.0
_SECOND_PLACE_REVIEW_GAP_THRESHOLD = 0.25
_SECOND_PLACE_REVIEW_NORMALIZED_THRESHOLD = 74.0
_SECOND_PLACE_REVIEW_PERCENTILE_THRESHOLD = 60.0
_THIRD_PLACE_REVIEW_GAP_THRESHOLD = 0.18
_THIRD_PLACE_REVIEW_PERCENTILE_THRESHOLD = 80.0
# AI Review classifies by folder percentile (0-100, evenly distributed).
# Defaults are tuned for high-volume photo culling: only the top ~10% pass as
# Keepers, the bottom ~80% become Rejects, and a thin ~10% band sits at the
# boundary as Needs Review. The window updates these at runtime from the
# user's Settings -> AI sliders via set_cull_thresholds().
_SINGLETON_KEEPER_PERCENTILE_THRESHOLD = 90.0
_SINGLETON_REJECT_PERCENTILE_THRESHOLD = 80.0


def set_cull_thresholds(*, keeper_percentile: float, reject_percentile: float) -> None:
    """Update the bucket classifier's percentile thresholds at runtime.

    Called by MainWindow at startup and whenever the user changes the
    "Keep top X%" / "Review band Y%" sliders in Settings -> AI. The next
    bundle load (or refresh) picks up the new thresholds.
    """

    global _SINGLETON_KEEPER_PERCENTILE_THRESHOLD, _SINGLETON_REJECT_PERCENTILE_THRESHOLD
    keeper = max(0.0, min(100.0, float(keeper_percentile)))
    reject = max(0.0, min(100.0, float(reject_percentile)))
    # Defensive: reject threshold must not exceed keeper threshold, otherwise
    # the classifier produces no Review band.
    if reject > keeper:
        reject = keeper
    _SINGLETON_KEEPER_PERCENTILE_THRESHOLD = keeper
    _SINGLETON_REJECT_PERCENTILE_THRESHOLD = reject


def current_cull_thresholds() -> tuple[float, float]:
    return (_SINGLETON_KEEPER_PERCENTILE_THRESHOLD, _SINGLETON_REJECT_PERCENTILE_THRESHOLD)

AI_REVIEW_TAG_DEFINITIONS: tuple[tuple[str, str], ...] = (
    (
        "AI Pick",
        "The strongest automatic keep. Apply AI Culling moves these frames into _winners without another prompt.",
    ),
    (
        "Keeper",
        "A strong frame, but not decisive enough to file automatically. Review these manually before committing them.",
    ),
    (
        "Needs Review",
        "The model saw mixed signals here. This is a deliberate human-review bucket, not a silent failure.",
    ),
    (
        "Reject",
        "A low-confidence frame that Apply AI Culling can move into the program recycle bin.",
    ),
    (
        "Best Frame",
        "The burst or similarity workflow thinks this is the strongest frame inside a local capture group.",
    ),
    (
        "AI Review",
        "Your manual state and the AI bucket disagree enough that the image deserves a second look.",
    ),
    (
        "AI Miss",
        "A strong disagreement between your review and the AI call, usually a keep versus reject conflict.",
    ),
)


@dataclass(slots=True, frozen=True)
class AIImageResult:
    image_id: str
    file_path: str
    file_name: str
    group_id: str
    group_size: int
    rank_in_group: int
    score: float
    cluster_reason: str = ""
    capture_timestamp: str = ""
    normalized_score: float | None = None
    folder_percentile: float | None = None
    score_gap_to_next: float | None = None
    score_gap_to_top: float | None = None
    confidence_bucket: AIConfidenceBucket = AIConfidenceBucket.NEEDS_REVIEW
    confidence_summary: str = ""

    @property
    def is_rank_leader(self) -> bool:
        return self.rank_in_group == 1 and self.group_size > 1

    @property
    def is_top_pick(self) -> bool:
        # Cluster rank doesn't drive classification anymore — any card whose
        # bucket is keeper / obvious winner qualifies for the "AI Pick" badge.
        return self.confidence_bucket in {
            AIConfidenceBucket.OBVIOUS_WINNER,
            AIConfidenceBucket.LIKELY_KEEPER,
        }

    @property
    def is_weak_cluster_leader(self) -> bool:
        return self.is_rank_leader and self.confidence_bucket == AIConfidenceBucket.LIKELY_REJECT

    @property
    def score_text(self) -> str:
        return f"{self.score:.2f}"

    @property
    def normalized_score_text(self) -> str:
        if self.normalized_score is None:
            return ""
        return f"{self.normalized_score:.1f}"

    @property
    def folder_percentile_text(self) -> str:
        if self.folder_percentile is None:
            return ""
        return f"{self.folder_percentile:.0f}"

    @property
    def display_score_text(self) -> str:
        # Show the folder percentile (0-100, how this image ranks within the
        # entire folder) when available. The earlier within-group normalized
        # score was misleading: a card could show "AI 94" because it led its
        # 3-image burst, while the burst itself sat in the folder's bottom
        # 15% and was correctly classified as Reject. Folder percentile is
        # the metric the keeper/reject buckets actually key off of, so the
        # badge number now matches the bucket badge.
        if self.folder_percentile is not None:
            return self.folder_percentile_text
        return self.normalized_score_text or self.score_text

    @property
    def display_score_with_scale_text(self) -> str:
        if self.folder_percentile is not None:
            return f"{self.folder_percentile_text}p"
        if self.normalized_score is None:
            return self.score_text
        return f"{self.normalized_score_text}/100"

    @property
    def rank_text(self) -> str:
        if self.group_size <= 0:
            return ""
        return f"#{self.rank_in_group}/{self.group_size}"

    @property
    def confidence_bucket_label(self) -> str:
        return confidence_bucket_label(self.confidence_bucket)

    @property
    def confidence_bucket_short_label(self) -> str:
        return confidence_bucket_short_label(self.confidence_bucket)


@dataclass(slots=True, frozen=True)
class AIBundleSource:
    source_path: str
    export_csv_path: str
    summary_json_path: str = ""
    report_html_path: str = ""
    cache_key: str = ""


@dataclass(slots=True, frozen=True)
class AIBundle:
    source_path: str
    export_csv_path: str
    summary_json_path: str = ""
    report_html_path: str = ""
    results_by_path: dict[str, AIImageResult] | None = None
    results_by_fast_path: dict[str, AIImageResult] | None = None
    results_by_group: dict[str, tuple[AIImageResult, ...]] | None = None
    normalized_scores_by_path: dict[str, float] | None = None
    summary: dict | None = None

    def result_for_path(self, path: str | Path) -> AIImageResult | None:
        if self.results_by_fast_path:
            fast = self.results_by_fast_path.get(_fast_path_key(path))
            if fast is not None:
                return fast
        if not self.results_by_path:
            return None
        return self.results_by_path.get(normalized_path_key(path))

    def group_results(self, group_id: str) -> tuple[AIImageResult, ...]:
        if not self.results_by_group:
            return ()
        return self.results_by_group.get(group_id, ())

    def normalized_score_for_result(self, result: AIImageResult | None) -> float | None:
        if result is None or not self.normalized_scores_by_path:
            return None
        return self.normalized_scores_by_path.get(normalized_path_key(result.file_path))

    def normalized_score_for_path(self, path: str | Path) -> float | None:
        if not self.normalized_scores_by_path:
            return None
        fast_result = self.result_for_path(path)
        if fast_result is not None:
            return fast_result.normalized_score
        return self.normalized_scores_by_path.get(normalized_path_key(path))

    def count_matches(self, records: Iterable[ImageRecord]) -> int:
        return sum(1 for record in records if find_ai_result_for_record(self, record) is not None)


def refine_ai_result_with_review_insight(
    result: AIImageResult | None,
    review_insight: "ReviewInsight | None",
) -> AIImageResult | None:
    if result is None or review_insight is None:
        return result

    detail_score = float(getattr(review_insight, "detail_score", 0.0) or 0.0)
    if detail_score <= 0.0:
        return result

    if detail_score <= _EXTREME_LOW_DETAIL_THRESHOLD:
        if result.group_size > 1 or (result.folder_percentile or 0.0) < _LOW_DETAIL_REJECT_SAFE_PERCENTILE:
            summary = _combine_confidence_summaries(
                result.confidence_summary,
                "Technical review flags this as an extremely low-detail frame, which usually means blur, obstruction, or a low-information miss.",
            )
            return _replace_confidence(result, AIConfidenceBucket.LIKELY_REJECT, summary)
        if result.confidence_bucket in {
            AIConfidenceBucket.OBVIOUS_WINNER,
            AIConfidenceBucket.LIKELY_KEEPER,
        }:
            summary = _combine_confidence_summaries(
                result.confidence_summary,
                "Technical review flags this as an extremely low-detail frame, so the rank should be checked manually.",
            )
            return _replace_confidence(result, AIConfidenceBucket.NEEDS_REVIEW, summary)

    if (
        detail_score <= _LOW_DETAIL_REVIEW_THRESHOLD
        and result.confidence_bucket in {
            AIConfidenceBucket.OBVIOUS_WINNER,
            AIConfidenceBucket.LIKELY_KEEPER,
        }
    ):
        summary = _combine_confidence_summaries(
            result.confidence_summary,
            "Technical review sees very limited detail here, which undercuts an automatic keep call.",
        )
        return _replace_confidence(result, AIConfidenceBucket.NEEDS_REVIEW, summary)
    return result


def load_ai_bundle(path: str | Path) -> AIBundle:
    source = inspect_ai_bundle_source(path)
    results: list[AIImageResult] = []
    with Path(source.export_csv_path).open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError(f"AI export file has no header row: {source.export_csv_path}")
        for row in reader:
            result = _row_to_result(row)
            if result is None:
                continue
            results.append(result)

    summary = {}
    if source.summary_json_path:
        try:
            summary = json.loads(Path(source.summary_json_path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            summary = {}

    return build_ai_bundle_from_results(
        source_path=source.source_path,
        export_csv_path=source.export_csv_path,
        summary_json_path=source.summary_json_path,
        report_html_path=source.report_html_path,
        results=results,
        summary=summary,
    )


def inspect_ai_bundle_source(path: str | Path) -> AIBundleSource:
    source_path = Path(path).expanduser().resolve()
    export_csv_path = _discover_export_csv(source_path)
    summary_path = _discover_neighbor(export_csv_path.parent, SUMMARY_FILENAMES)
    html_path = _discover_neighbor(export_csv_path.parent, HTML_REPORT_FILENAMES)
    cache_key = _build_ai_bundle_cache_key(
        source_path=source_path,
        export_csv_path=export_csv_path,
        summary_json_path=summary_path,
        report_html_path=html_path,
    )
    return AIBundleSource(
        source_path=str(source_path),
        export_csv_path=str(export_csv_path),
        summary_json_path=str(summary_path) if summary_path is not None else "",
        report_html_path=str(html_path) if html_path is not None else "",
        cache_key=cache_key,
    )


def build_ai_bundle_from_results(
    *,
    source_path: str | Path,
    export_csv_path: str | Path,
    results: Iterable[AIImageResult],
    summary_json_path: str | Path = "",
    report_html_path: str | Path = "",
    summary: dict | None = None,
) -> AIBundle:
    group_buckets: dict[str, list[AIImageResult]] = {}
    for result in results:
        group_buckets.setdefault(result.group_id, []).append(result)

    grouped_results = {
        group_id: tuple(sorted(group_results, key=lambda item: (item.rank_in_group, -item.score, item.file_name.casefold())))
        for group_id, group_results in group_buckets.items()
    }
    normalized_scores_by_path = _build_normalized_score_map(grouped_results)
    folder_percentiles_by_path = _build_folder_percentile_map(grouped_results)
    enriched_results_by_path: dict[str, AIImageResult] = {}
    enriched_results_by_fast_path: dict[str, AIImageResult] = {}
    results_by_group = {
        group_id: tuple(
            _enrich_result_with_context(
                result,
                grouped_results[group_id],
                normalized_scores_by_path,
                folder_percentiles_by_path,
                enriched_results_by_path,
                enriched_results_by_fast_path,
            )
            for result in group_results
        )
        for group_id, group_results in grouped_results.items()
    }

    return AIBundle(
        source_path=str(source_path),
        export_csv_path=str(export_csv_path),
        summary_json_path=str(summary_json_path),
        report_html_path=str(report_html_path),
        results_by_path=enriched_results_by_path,
        results_by_fast_path=enriched_results_by_fast_path,
        results_by_group=results_by_group,
        normalized_scores_by_path=normalized_scores_by_path,
        summary=dict(summary) if isinstance(summary, dict) else {},
    )


def iter_ai_bundle_results(bundle: AIBundle) -> tuple[AIImageResult, ...]:
    if bundle.results_by_group:
        seen: set[str] = set()
        ordered: list[AIImageResult] = []
        for group_id in sorted(bundle.results_by_group, key=str.casefold):
            for result in bundle.results_by_group[group_id]:
                key = normalized_path_key(result.file_path)
                if not key or key in seen:
                    continue
                seen.add(key)
                ordered.append(result)
        return tuple(ordered)
    if not bundle.results_by_path:
        return ()
    seen = set()
    ordered = []
    for result in bundle.results_by_path.values():
        key = normalized_path_key(result.file_path)
        if not key or key in seen:
            continue
        seen.add(key)
        ordered.append(result)
    return tuple(sorted(ordered, key=lambda item: (item.group_id.casefold(), item.rank_in_group, item.file_name.casefold())))


def find_ai_result_for_record(
    bundle: AIBundle | None,
    record: ImageRecord,
    *,
    preferred_path: str | Path | None = None,
) -> AIImageResult | None:
    if bundle is None or bundle.results_by_path is None:
        return None

    candidate_paths: list[str | Path] = []
    if preferred_path:
        candidate_paths.append(preferred_path)
    candidate_paths.extend(record.stack_paths)

    seen_fast: set[str] = set()
    seen_normalized: set[str] = set()
    for path in candidate_paths:
        fast_key = _fast_path_key(path)
        if fast_key and fast_key not in seen_fast and bundle.results_by_fast_path is not None:
            seen_fast.add(fast_key)
            result = bundle.results_by_fast_path.get(fast_key)
            if result is not None:
                return result
        key = normalized_path_key(path)
        if not key or key in seen_normalized:
            continue
        seen_normalized.add(key)
        result = bundle.results_by_path.get(key)
        if result is not None:
            return result
    return None


def _enrich_result_with_context(
    result: AIImageResult,
    group_results: tuple[AIImageResult, ...],
    normalized_scores_by_path: dict[str, float],
    folder_percentiles_by_path: dict[str, float],
    enriched_results_by_path: dict[str, AIImageResult],
    enriched_results_by_fast_path: dict[str, AIImageResult],
) -> AIImageResult:
    normalized_key = normalized_path_key(result.file_path)
    normalized_score = normalized_scores_by_path.get(normalized_key)
    folder_percentile = folder_percentiles_by_path.get(normalized_key)
    score_gap_to_next = _score_gap_to_next(result, group_results)
    score_gap_to_top = _score_gap_to_top(result, group_results)
    confidence_bucket, confidence_summary = _confidence_context_for_result(
        result,
        group_results,
        normalized_score=normalized_score,
        folder_percentile=folder_percentile,
        score_gap_to_next=score_gap_to_next,
        score_gap_to_top=score_gap_to_top,
    )
    enriched = replace(
        result,
        normalized_score=normalized_score,
        folder_percentile=folder_percentile,
        score_gap_to_next=score_gap_to_next,
        score_gap_to_top=score_gap_to_top,
        confidence_bucket=confidence_bucket,
        confidence_summary=confidence_summary,
    )
    enriched_results_by_path[normalized_key] = enriched
    enriched_results_by_fast_path[_fast_path_key(result.file_path)] = enriched
    return enriched


def _discover_export_csv(path: Path) -> Path:
    if path.is_file():
        if path.suffix.lower() != ".csv":
            raise FileNotFoundError(f"AI results file must be a CSV export: {path}")
        return path

    if not path.exists():
        raise FileNotFoundError(f"AI results path does not exist: {path}")

    direct = _discover_neighbor(path, PREFERRED_EXPORT_FILENAMES)
    if direct is not None:
        return direct

    for child in sorted(path.iterdir(), key=lambda item: item.name.casefold()):
        if not child.is_dir():
            continue
        candidate = _discover_neighbor(child, PREFERRED_EXPORT_FILENAMES)
        if candidate is not None:
            return candidate

    raise FileNotFoundError(
        f"Could not find an AI ranked export under {path}. Expected one of: "
        + ", ".join(PREFERRED_EXPORT_FILENAMES)
    )


def _discover_neighbor(folder: Path, names: tuple[str, ...]) -> Path | None:
    for name in names:
        candidate = folder / name
        if candidate.exists():
            return candidate
    return None


def _build_ai_bundle_cache_key(
    *,
    source_path: Path,
    export_csv_path: Path,
    summary_json_path: Path | None,
    report_html_path: Path | None,
) -> str:
    payload = {
        "source_path": str(source_path),
        "export_csv": _path_signature(export_csv_path),
        "summary_json": _path_signature(summary_json_path),
        "report_html": _path_signature(report_html_path),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return sha1(encoded).hexdigest()


def _path_signature(path: Path | None) -> dict[str, object]:
    if path is None:
        return {"path": "", "size": -1, "modified_ns": -1}
    try:
        stat_result = path.stat()
    except OSError:
        return {"path": str(path), "size": -1, "modified_ns": -1}
    return {
        "path": str(path),
        "size": int(stat_result.st_size),
        "modified_ns": int(getattr(stat_result, "st_mtime_ns", int(stat_result.st_mtime * 1_000_000_000))),
    }


def _row_to_result(row: dict[str, str]) -> AIImageResult | None:
    file_path = (row.get("file_path") or "").strip()
    if not file_path:
        return None

    group_id = (row.get("cluster_id") or row.get("group_id") or "").strip()
    if not group_id:
        group_id = "unassigned"

    image_id = (row.get("image_id") or "").strip()
    file_name = (row.get("file_name") or Path(file_path).name).strip()

    try:
        group_size = int(str(row.get("cluster_size") or row.get("group_size") or "1"))
    except ValueError:
        group_size = 1

    try:
        rank_in_group = int(str(row.get("rank_in_cluster") or row.get("rank") or "1"))
    except ValueError:
        rank_in_group = 1

    try:
        score = float(str(row.get("score") or row.get("ai_score") or "0"))
    except ValueError:
        score = 0.0

    return AIImageResult(
        image_id=image_id,
        file_path=file_path,
        file_name=file_name,
        group_id=group_id,
        group_size=group_size,
        rank_in_group=rank_in_group,
        score=score,
        cluster_reason=(row.get("cluster_reason") or row.get("group_reason") or "").strip(),
        capture_timestamp=(row.get("capture_timestamp") or row.get("timestamp") or "").strip(),
    )


def _build_normalized_score_map(results_by_group: dict[str, tuple[AIImageResult, ...]]) -> dict[str, float]:
    normalized_scores: dict[str, float] = {}
    for results in results_by_group.values():
        if not results or len(results) <= 1:
            continue
        scores = [result.score for result in results]
        max_score = max(scores)
        min_score = min(scores)
        span = max_score - min_score
        for result in results:
            if span <= 1e-9:
                normalized = 100.0
            else:
                normalized = ((result.score - min_score) / span) * 100.0
            normalized_scores[normalized_path_key(result.file_path)] = normalized
    return normalized_scores


def _build_folder_percentile_map(results_by_group: dict[str, tuple[AIImageResult, ...]]) -> dict[str, float]:
    ordered = sorted(
        (result for results in results_by_group.values() for result in results),
        key=lambda item: (-item.score, item.file_name.casefold()),
    )
    total = len(ordered)
    if total <= 0:
        return {}
    if total == 1:
        return {normalized_path_key(ordered[0].file_path): 100.0}

    percentiles: dict[str, float] = {}
    denominator = max(1, total - 1)
    for index, result in enumerate(ordered):
        percentile = 100.0 - ((index / denominator) * 100.0)
        percentiles[normalized_path_key(result.file_path)] = percentile
    return percentiles


def _score_gap_to_next(result: AIImageResult, group_results: tuple[AIImageResult, ...]) -> float | None:
    for index, candidate in enumerate(group_results):
        if normalized_path_key(candidate.file_path) != normalized_path_key(result.file_path):
            continue
        if index + 1 >= len(group_results):
            return None
        return candidate.score - group_results[index + 1].score
    return None


def _score_gap_to_top(result: AIImageResult, group_results: tuple[AIImageResult, ...]) -> float | None:
    if not group_results:
        return None
    top = group_results[0]
    return top.score - result.score


def _replace_confidence(
    result: AIImageResult,
    confidence_bucket: AIConfidenceBucket,
    confidence_summary: str,
) -> AIImageResult:
    if (
        result.confidence_bucket == confidence_bucket
        and result.confidence_summary.strip() == confidence_summary.strip()
    ):
        return result
    return replace(
        result,
        confidence_bucket=confidence_bucket,
        confidence_summary=confidence_summary,
    )


def _combine_confidence_summaries(*parts: str) -> str:
    merged: list[str] = []
    seen: set[str] = set()
    for part in parts:
        normalized = (part or "").strip().rstrip(".")
        if not normalized:
            continue
        key = normalized.casefold()
        if key in seen:
            continue
        seen.add(key)
        merged.append(normalized)
    if not merged:
        return ""
    return ". ".join(merged) + "."


def _confidence_context_for_result(
    result: AIImageResult,
    group_results: tuple[AIImageResult, ...],
    *,
    normalized_score: float | None,
    folder_percentile: float | None,
    score_gap_to_next: float | None,
    score_gap_to_top: float | None,
) -> tuple[AIConfidenceBucket, str]:
    """Classify each image by its FOLDER percentile only.

    The earlier cluster-aware path produced confusing edge cases — a card
    leading a small burst could be marked Reject because the whole burst
    landed in the folder's bottom 15%, or a perfectly fine standalone image
    could be marked Reject because it happened to be rank-2 in some loose
    cluster. The badge then said something like "AI 94" (within-group score)
    on a Reject card, which read as a contradiction.

    Now every image is judged independently against folder percentile, using
    the singleton thresholds. Burst/cluster grouping is still detected and
    surfaced for navigation, but it no longer drives the keeper/reject
    decision. This matches the user's intent of "don't rank like-images
    bundled as one."
    """

    percentile = folder_percentile if folder_percentile is not None else 50.0
    if percentile >= _SINGLETON_KEEPER_PERCENTILE_THRESHOLD:
        return AIConfidenceBucket.LIKELY_KEEPER, "High score compared with the rest of the folder."
    if percentile <= _SINGLETON_REJECT_PERCENTILE_THRESHOLD:
        return AIConfidenceBucket.LIKELY_REJECT, "Score lands near the bottom of the folder."
    return AIConfidenceBucket.NEEDS_REVIEW, "Mid-folder score — needs a human pass."


def confidence_bucket_label(bucket: AIConfidenceBucket | str) -> str:
    resolved = AIConfidenceBucket(bucket) if isinstance(bucket, str) else bucket
    if resolved == AIConfidenceBucket.OBVIOUS_WINNER:
        return "Obvious winner"
    if resolved == AIConfidenceBucket.LIKELY_KEEPER:
        return "Likely keeper"
    if resolved == AIConfidenceBucket.LIKELY_REJECT:
        return "Likely reject"
    return "Needs review"


def confidence_bucket_short_label(bucket: AIConfidenceBucket | str) -> str:
    resolved = AIConfidenceBucket(bucket) if isinstance(bucket, str) else bucket
    if resolved == AIConfidenceBucket.OBVIOUS_WINNER:
        return "Winner"
    if resolved == AIConfidenceBucket.LIKELY_KEEPER:
        return "Keeper"
    if resolved == AIConfidenceBucket.LIKELY_REJECT:
        return "Reject"
    return "Review"


def ai_cull_bucket_for_result(result: AIImageResult | None) -> AICullBucket:
    if result is None:
        return AICullBucket.UNRATED
    if result.is_top_pick:
        return AICullBucket.AI_PICK
    if result.confidence_bucket == AIConfidenceBucket.LIKELY_REJECT:
        return AICullBucket.REJECT
    if result.confidence_bucket == AIConfidenceBucket.LIKELY_KEEPER:
        return AICullBucket.KEEPER
    return AICullBucket.NEEDS_REVIEW


def ai_review_badge_label(result: AIImageResult | None) -> str:
    bucket = ai_cull_bucket_for_result(result)
    if bucket == AICullBucket.AI_PICK:
        return "AI Pick"
    if bucket == AICullBucket.REJECT:
        return "Reject"
    if bucket == AICullBucket.KEEPER:
        return "Keeper"
    if bucket == AICullBucket.NEEDS_REVIEW:
        return "Needs Review"
    return "Unrated"


def ai_manual_cull_sort_key(result: AIImageResult | None) -> tuple[float, ...]:
    bucket = ai_cull_bucket_for_result(result)
    priority = {
        AICullBucket.AI_PICK: 0.0,
        AICullBucket.REJECT: 1.0,
        AICullBucket.KEEPER: 2.0,
        AICullBucket.NEEDS_REVIEW: 3.0,
        AICullBucket.UNRATED: 4.0,
    }[bucket]
    if result is None:
        return (priority, 0.0, 0.0, 0.0)

    folder_percentile = float(result.folder_percentile or 0.0)
    score = float(result.score)
    rank = float(max(1, result.rank_in_group))
    if bucket == AICullBucket.REJECT:
        return (priority, folder_percentile, score, rank)
    return (priority, -folder_percentile, -score, rank)


def ai_review_tag_definitions() -> tuple[tuple[str, str], ...]:
    return AI_REVIEW_TAG_DEFINITIONS


def build_ai_explanation_lines(
    result: AIImageResult | None,
    *,
    review_summary: str = "",
    detail_score: float | None = None,
) -> tuple[str, ...]:
    if result is None:
        return ()

    lines: list[str] = [f"Confidence bucket: {result.confidence_bucket_label}."]
    if result.group_size > 1:
        lines.append(f"Ranked {result.rank_text} inside a {result.group_size}-image AI group.")
        if result.rank_in_group == 1 and result.score_gap_to_next is not None:
            if result.score_gap_to_next >= 0.12:
                lines.append(f"It led the next frame by {result.score_gap_to_next:.2f} model points.")
            else:
                lines.append("Its lead over the next frame is small, so this is not a runaway pick.")
    elif result.folder_percentile is not None:
        lines.append(f"Global folder percentile: {result.folder_percentile:.0f}.")

    if result.cluster_reason:
        lines.append(result.cluster_reason.rstrip(".") + ".")
    if review_summary:
        lines.append(f"Local grouping: {review_summary}.")
    if detail_score is not None:
        if detail_score >= 72.0:
            lines.append("Inspection sees strong detail retention in the focused frame.")
        elif detail_score <= 38.0:
            lines.append("Inspection sees softer detail, which may explain a weaker rank.")
    if result.confidence_summary:
        lines.append(result.confidence_summary)
    return tuple(lines[:5])
