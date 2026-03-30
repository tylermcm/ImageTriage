from __future__ import annotations

import csv
import json
import os
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Iterable

from .models import ImageRecord
from .scanner import normalized_path_key


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

    @property
    def is_top_pick(self) -> bool:
        return self.rank_in_group == 1 and self.group_size > 1

    @property
    def score_text(self) -> str:
        return f"{self.score:.2f}"

    @property
    def normalized_score_text(self) -> str:
        if self.normalized_score is None:
            return ""
        return f"{self.normalized_score:.1f}"

    @property
    def display_score_text(self) -> str:
        return self.normalized_score_text or self.score_text

    @property
    def display_score_with_scale_text(self) -> str:
        if self.normalized_score is None:
            return self.score_text
        return f"{self.normalized_score_text}/100"

    @property
    def rank_text(self) -> str:
        if self.group_size <= 0:
            return ""
        return f"#{self.rank_in_group}/{self.group_size}"


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


def load_ai_bundle(path: str | Path) -> AIBundle:
    source_path = Path(path).expanduser().resolve()
    export_csv_path = _discover_export_csv(source_path)
    summary_path = _discover_neighbor(export_csv_path.parent, SUMMARY_FILENAMES)
    html_path = _discover_neighbor(export_csv_path.parent, HTML_REPORT_FILENAMES)

    results_by_path: dict[str, AIImageResult] = {}
    group_buckets: dict[str, list[AIImageResult]] = {}
    with export_csv_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError(f"AI export file has no header row: {export_csv_path}")
        for row in reader:
            result = _row_to_result(row)
            if result is None:
                continue
            results_by_path[normalized_path_key(result.file_path)] = result
            group_buckets.setdefault(result.group_id, []).append(result)

    grouped_results = {
        group_id: tuple(sorted(results, key=lambda item: (item.rank_in_group, -item.score, item.file_name.casefold())))
        for group_id, results in group_buckets.items()
    }
    normalized_scores_by_path = _build_normalized_score_map(grouped_results)
    enriched_results_by_path: dict[str, AIImageResult] = {}
    enriched_results_by_fast_path: dict[str, AIImageResult] = {}
    results_by_group = {
        group_id: tuple(
            _enrich_result_with_normalized_score(
                result,
                normalized_scores_by_path,
                enriched_results_by_path,
                enriched_results_by_fast_path,
            )
            for result in results
        )
        for group_id, results in grouped_results.items()
    }

    summary = {}
    if summary_path is not None:
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            summary = {}

    return AIBundle(
        source_path=str(source_path),
        export_csv_path=str(export_csv_path),
        summary_json_path=str(summary_path) if summary_path is not None else "",
        report_html_path=str(html_path) if html_path is not None else "",
        results_by_path=enriched_results_by_path,
        results_by_fast_path=enriched_results_by_fast_path,
        results_by_group=results_by_group,
        normalized_scores_by_path=normalized_scores_by_path,
        summary=summary,
    )


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


def _enrich_result_with_normalized_score(
    result: AIImageResult,
    normalized_scores_by_path: dict[str, float],
    enriched_results_by_path: dict[str, AIImageResult],
    enriched_results_by_fast_path: dict[str, AIImageResult],
) -> AIImageResult:
    normalized_key = normalized_path_key(result.file_path)
    normalized_score = normalized_scores_by_path.get(normalized_key)
    enriched = replace(result, normalized_score=normalized_score)
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
        if not results:
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
