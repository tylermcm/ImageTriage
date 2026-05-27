"""Checkpoint-free evaluation for the modular culling signal stack."""

from __future__ import annotations

import csv
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import numpy as np

from app.engine.signals.combiner import FEATURE_NAMES
from app.engine.ranking.exports import build_ranked_export_rows
from app.storage.ranking_artifacts import (
    ClusterLabelRecord,
    PairwisePreferenceRecord,
    RankingArtifacts,
    load_latest_cluster_labels,
    load_preference_labels,
    load_ranking_artifacts,
    save_ranking_summary_json,
)


SIGNAL_EVALUATION_METRICS_FILENAME = "culling_signal_evaluation.json"
SIGNAL_EVALUATION_SUMMARY_FILENAME = "culling_signal_evaluation.csv"


@dataclass(frozen=True)
class SignalRankedClusterMember:
    """Small ranked-member shape compatible with ranked export helpers."""

    cluster_id: str
    cluster_size: int
    rank_in_cluster: int
    image_id: str
    score: float
    file_path: str
    relative_path: str
    file_name: str
    capture_timestamp: str
    capture_time_source: str
    base_score: float
    reference_adjustment: float = 0.0


def evaluate_culling_signals(
    *,
    artifacts_dir: Path,
    labels_dir: Path,
    signals_path: Path,
    output_dir: Path,
    metadata_filename: str = "images.csv",
    embeddings_filename: str = "embeddings.npy",
    image_ids_filename: str = "image_ids.json",
    clusters_filename: str = "clusters.csv",
    pairwise_labels_filename: str = "pairwise_labels.jsonl",
    cluster_labels_filename: str = "cluster_labels.jsonl",
    include_cluster_label_pairs: bool = True,
    top_k_values: Sequence[int] = (1, 3),
    near_identical_similarity_threshold: float = 0.965,
) -> dict[str, Path]:
    """Evaluate DINO/order/signal-combiner scorers against saved labels."""

    artifacts_dir = Path(artifacts_dir).expanduser().resolve()
    labels_dir = Path(labels_dir).expanduser().resolve()
    signals_path = Path(signals_path).expanduser().resolve()
    output_dir = Path(output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    ranking_artifacts = load_ranking_artifacts(
        artifacts_dir,
        metadata_filename=metadata_filename,
        embeddings_filename=embeddings_filename,
        image_ids_filename=image_ids_filename,
        clusters_filename=clusters_filename,
    )
    loaded_labels = load_preference_labels(
        labels_dir=labels_dir,
        ranking_artifacts=ranking_artifacts,
        pairwise_labels_filename=pairwise_labels_filename,
        cluster_labels_filename=cluster_labels_filename,
        include_cluster_label_pairs=include_cluster_label_pairs,
        skip_ties=True,
    )
    pairwise_only = [
        preference for preference in loaded_labels.preferences if preference.label_origin == "pairwise_label"
    ]
    cluster_only = [
        preference for preference in loaded_labels.preferences if preference.label_origin == "cluster_label"
    ]
    near_identical = _near_identical_preference_filter(
        loaded_labels.preferences,
        ranking_artifacts=ranking_artifacts,
        threshold=near_identical_similarity_threshold,
    )
    distinct_preferences = _exclude_preferences(loaded_labels.preferences, near_identical["keys"])
    distinct_pairwise_only = _exclude_preferences(pairwise_only, near_identical["keys"])
    distinct_cluster_only = _exclude_preferences(cluster_only, near_identical["keys"])
    cluster_labels_by_id = load_latest_cluster_labels(
        labels_dir=labels_dir,
        cluster_labels_filename=cluster_labels_filename,
    )

    scorers: dict[str, dict[str, Any]] = {}
    scorers["random_expected"] = _evaluate_random_expected(
        all_preferences=loaded_labels.preferences,
        pairwise_only=pairwise_only,
        cluster_only=cluster_only,
        distinct_preferences=distinct_preferences,
        distinct_pairwise_only=distinct_pairwise_only,
        distinct_cluster_only=distinct_cluster_only,
        ranking_artifacts=ranking_artifacts,
        cluster_labels_by_id=cluster_labels_by_id,
        top_k_values=top_k_values,
    )

    file_order_scores = _file_order_scores(ranking_artifacts)
    scorers["file_order"] = _evaluate_scorer(
        name="File Order",
        scores=file_order_scores,
        ranking_artifacts=ranking_artifacts,
        all_preferences=loaded_labels.preferences,
        pairwise_only=pairwise_only,
        cluster_only=cluster_only,
        distinct_preferences=distinct_preferences,
        distinct_pairwise_only=distinct_pairwise_only,
        distinct_cluster_only=distinct_cluster_only,
        cluster_labels_by_id=cluster_labels_by_id,
        top_k_values=top_k_values,
    )

    centrality_scores = _dino_centrality_scores(ranking_artifacts)
    scorers["dino_centrality"] = _evaluate_scorer(
        name="DINO Centrality",
        scores=centrality_scores,
        ranking_artifacts=ranking_artifacts,
        all_preferences=loaded_labels.preferences,
        pairwise_only=pairwise_only,
        cluster_only=cluster_only,
        distinct_preferences=distinct_preferences,
        distinct_pairwise_only=distinct_pairwise_only,
        distinct_cluster_only=distinct_cluster_only,
        cluster_labels_by_id=cluster_labels_by_id,
        top_k_values=top_k_values,
    )

    signal_scores = _load_signal_scores(signals_path=signals_path, ranking_artifacts=ranking_artifacts)
    if signal_scores is None:
        raise ValueError(f"No usable Transparent Combiner scores were found in {signals_path}")
    feature_scores = _load_signal_feature_scores(signals_path=signals_path, ranking_artifacts=ranking_artifacts)
    scorers["transparent_combiner"] = _evaluate_scorer(
        name="Transparent Combiner",
        scores=signal_scores["scores"],
        ranking_artifacts=ranking_artifacts,
        all_preferences=loaded_labels.preferences,
        pairwise_only=pairwise_only,
        cluster_only=cluster_only,
        distinct_preferences=distinct_preferences,
        distinct_pairwise_only=distinct_pairwise_only,
        distinct_cluster_only=distinct_cluster_only,
        cluster_labels_by_id=cluster_labels_by_id,
        top_k_values=top_k_values,
    )
    scorers["transparent_combiner"]["signal_source_path"] = str(signals_path)
    scorers["transparent_combiner"]["missing_signal_scores"] = signal_scores["missing_count"]
    feature_scorer_keys: list[str] = []
    for feature_name in FEATURE_NAMES:
        payload = feature_scores.get(feature_name)
        if not payload:
            continue
        scores = payload["scores"]
        if _score_is_constant(scores):
            continue
        scorer_key = f"feature:{feature_name}"
        feature_scorer_keys.append(scorer_key)
        scorers[scorer_key] = _evaluate_scorer(
            name=f"Feature: {feature_name}",
            scores=scores,
            ranking_artifacts=ranking_artifacts,
            all_preferences=loaded_labels.preferences,
            pairwise_only=pairwise_only,
            cluster_only=cluster_only,
            distinct_preferences=distinct_preferences,
            distinct_pairwise_only=distinct_pairwise_only,
            distinct_cluster_only=distinct_cluster_only,
            cluster_labels_by_id=cluster_labels_by_id,
            top_k_values=top_k_values,
        )
        scorers[scorer_key]["feature_name"] = feature_name
        scorers[scorer_key]["missing_feature_scores"] = payload["missing_count"]

    metrics_skipped: list[str] = []
    primary = scorers["transparent_combiner"]
    if primary["pairwise_evaluation"]["pairwise_label"]["evaluated_pairs"] == 0:
        metrics_skipped.append("Pairwise-label accuracy skipped because no usable pairwise labels were available.")
    if primary["pairwise_evaluation"]["cluster_label"]["evaluated_pairs"] == 0:
        metrics_skipped.append("Cluster-derived pairwise accuracy skipped because no usable cluster label pairs were available.")
    if primary["cluster_evaluation"]["evaluated_clusters"] == 0:
        metrics_skipped.append("Cluster winner metrics skipped because no clusters with saved human best labels were available.")

    summary = {
        "evaluation_mode": "culling_signals",
        "primary_scorer": "transparent_combiner",
        "artifacts_dir": str(artifacts_dir),
        "labels_dir": str(labels_dir),
        "signals_path": str(signals_path),
        "dataset_summary": {
            "total_images": len(ranking_artifacts.ordered_images),
            "total_clusters": len(ranking_artifacts.clusters_by_id),
            "singleton_clusters": sum(len(members) == 1 for members in ranking_artifacts.clusters_by_id.values()),
            "largest_cluster_size": max((len(members) for members in ranking_artifacts.clusters_by_id.values()), default=0),
        },
        "label_summary": loaded_labels.summary,
        "near_identical_pair_filter": {
            key: value for key, value in near_identical.items() if key != "keys"
        },
        "pairwise_evaluation": primary["pairwise_evaluation"],
        "cluster_evaluation": primary["cluster_evaluation"],
        "baseline_comparison": {
            "scorers": scorers,
            "feature_scorer_keys": feature_scorer_keys,
            "deltas_transparent_combiner_vs_dino_centrality": {
                "pairwise_accuracy": _metric_delta(
                    scorers["transparent_combiner"],
                    scorers["dino_centrality"],
                    "pairwise_evaluation",
                    "all_preferences",
                    "accuracy",
                ),
                "cluster_top1_hit_rate": _top_k_delta(scorers["transparent_combiner"], scorers["dino_centrality"], "top_1"),
                "cluster_top3_hit_rate": _top_k_delta(scorers["transparent_combiner"], scorers["dino_centrality"], "top_3"),
                "mean_first_human_best_rank": _metric_delta(
                    scorers["transparent_combiner"],
                    scorers["dino_centrality"],
                    "cluster_evaluation",
                    "mean_first_human_best_rank",
                    lower_is_better=True,
                ),
            },
            "notes": [
                "DINO Centrality ranks each cluster by cosine similarity to the cluster centroid.",
                "File Order ranks by original cluster/file order.",
                "Random Expected is analytical chance, not a sampled random seed.",
                "Transparent Combiner reads final.score from culling_signals.json and does not require a trained checkpoint.",
            ],
        },
        "metrics_skipped": metrics_skipped,
        "notes": [
            "This evaluation does not load a trained ranker checkpoint.",
            "Use held-out label sources to measure generalization; evaluating a source used to tune weights is an in-sample check.",
        ],
    }

    metrics_path = output_dir / SIGNAL_EVALUATION_METRICS_FILENAME
    summary_path = output_dir / SIGNAL_EVALUATION_SUMMARY_FILENAME
    save_ranking_summary_json(metrics_path, summary)
    _save_signal_evaluation_summary_csv(summary_path, scorers)
    return {"metrics": metrics_path, "summary": summary_path}


def _evaluate_scorer(
    *,
    name: str,
    scores: np.ndarray,
    ranking_artifacts: RankingArtifacts,
    all_preferences: Sequence[PairwisePreferenceRecord],
    pairwise_only: Sequence[PairwisePreferenceRecord],
    cluster_only: Sequence[PairwisePreferenceRecord],
    distinct_preferences: Sequence[PairwisePreferenceRecord],
    distinct_pairwise_only: Sequence[PairwisePreferenceRecord],
    distinct_cluster_only: Sequence[PairwisePreferenceRecord],
    cluster_labels_by_id: Mapping[str, ClusterLabelRecord],
    top_k_values: Sequence[int],
) -> dict[str, Any]:
    ranked_clusters = _rank_clusters_from_scores(ranking_artifacts, scores)
    export_rows = build_ranked_export_rows(
        ranked_clusters,
        ranking_artifacts,
        cluster_labels_by_id=dict(cluster_labels_by_id),
    )
    cluster_metrics, _cluster_rows = _evaluate_cluster_rankings(
        export_rows,
        cluster_labels_by_id=dict(cluster_labels_by_id),
        top_k_values=top_k_values,
    )
    return {
        "display_name": name,
        "pairwise_evaluation": {
            "all_preferences": _evaluate_pairwise_preferences(scores, all_preferences),
            "pairwise_label": _evaluate_pairwise_preferences(scores, pairwise_only),
            "cluster_label": _evaluate_pairwise_preferences(scores, cluster_only),
            "all_preferences_distinct": _evaluate_pairwise_preferences(scores, distinct_preferences),
            "pairwise_label_distinct": _evaluate_pairwise_preferences(scores, distinct_pairwise_only),
            "cluster_label_distinct": _evaluate_pairwise_preferences(scores, distinct_cluster_only),
        },
        "cluster_evaluation": cluster_metrics,
    }


def _evaluate_pairwise_preferences(
    scores: np.ndarray,
    preferences: Sequence[PairwisePreferenceRecord],
) -> dict[str, Any]:
    if not preferences:
        return {
            "evaluated_pairs": 0,
            "accuracy": None,
            "mean_margin": None,
            "median_margin": None,
            "mean_correct_margin": None,
            "mean_incorrect_margin": None,
        }

    margins = np.asarray(
        [
            float(scores[preference.preferred_index] - scores[preference.other_index])
            for preference in preferences
        ],
        dtype=np.float32,
    )
    correct_mask = margins > 0.0
    correct_margins = margins[correct_mask]
    incorrect_margins = margins[~correct_mask]
    return {
        "evaluated_pairs": int(margins.shape[0]),
        "accuracy": float(correct_mask.mean()),
        "mean_margin": float(margins.mean()),
        "median_margin": float(np.median(margins)),
        "mean_correct_margin": float(correct_margins.mean()) if correct_margins.size else None,
        "mean_incorrect_margin": float(incorrect_margins.mean()) if incorrect_margins.size else None,
    }


def _evaluate_cluster_rankings(
    rows: Iterable[Any],
    *,
    cluster_labels_by_id: Mapping[str, ClusterLabelRecord],
    top_k_values: Sequence[int],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    grouped: dict[str, list[Any]] = {}
    for row in rows:
        grouped.setdefault(row.cluster_id, []).append(row)
    for members in grouped.values():
        members.sort(key=lambda item: (item.rank_in_cluster, item.cluster_position, item.image_id))

    top_k_values = tuple(sorted(set(int(value) for value in top_k_values if int(value) > 0)))
    breakdown_rows: list[dict[str, Any]] = []
    evaluated_clusters = 0
    skipped_without_best = 0
    missing_rankings = 0
    multi_best_clusters = 0
    first_best_ranks: list[int] = []
    hit_counts = {value: 0 for value in top_k_values}
    eligible_counts = {value: 0 for value in top_k_values}

    for cluster_id, label_record in sorted(cluster_labels_by_id.items()):
        members = grouped.get(cluster_id)
        if not members:
            missing_rankings += 1
            breakdown_rows.append({"cluster_id": cluster_id, "evaluation_status": "missing_ranked_output"})
            continue

        human_best_ids = set(label_record.best_image_ids)
        if len(human_best_ids) > 1:
            multi_best_clusters += 1
        top_member = members[0]
        row: dict[str, Any] = {
            "cluster_id": cluster_id,
            "cluster_size": members[0].cluster_size,
            "human_best_count": len(human_best_ids),
            "evaluation_status": "evaluated" if human_best_ids else "skipped_no_human_best",
            "model_top1_file_name": top_member.file_name,
            "model_top1_score": top_member.score,
            "model_top1_human_label": top_member.human_label or "",
            "model_top1_is_human_non_reject": top_member.model_top1_is_human_non_reject,
        }
        if not human_best_ids:
            skipped_without_best += 1
            breakdown_rows.append(row)
            continue

        evaluated_clusters += 1
        first_best_rank = next(member.rank_in_cluster for member in members if member.image_id in human_best_ids)
        first_best_ranks.append(first_best_rank)
        row["first_human_best_rank"] = first_best_rank

        for value in top_k_values:
            if members[0].cluster_size >= value:
                eligible_counts[value] += 1
                hit = any(member.image_id in human_best_ids for member in members[:value])
                row[f"top_{value}_hit"] = hit
                if hit:
                    hit_counts[value] += 1
            else:
                row[f"top_{value}_hit"] = ""
        breakdown_rows.append(row)

    return (
        {
            "labeled_clusters": len(cluster_labels_by_id),
            "evaluated_clusters": evaluated_clusters,
            "multi_best_clusters": multi_best_clusters,
            "skipped_clusters_without_best": skipped_without_best,
            "missing_ranked_clusters": missing_rankings,
            "top_k_metrics": {
                f"top_{value}": {
                    "eligible_clusters": eligible_counts[value],
                    "hit_count": hit_counts[value],
                    "hit_rate": hit_counts[value] / eligible_counts[value] if eligible_counts[value] else None,
                }
                for value in top_k_values
            },
            "mean_first_human_best_rank": float(np.mean(first_best_ranks)) if first_best_ranks else None,
            "median_first_human_best_rank": float(np.median(first_best_ranks)) if first_best_ranks else None,
        },
        breakdown_rows,
    )


def _evaluate_random_expected(
    *,
    all_preferences: Sequence[PairwisePreferenceRecord],
    pairwise_only: Sequence[PairwisePreferenceRecord],
    cluster_only: Sequence[PairwisePreferenceRecord],
    distinct_preferences: Sequence[PairwisePreferenceRecord],
    distinct_pairwise_only: Sequence[PairwisePreferenceRecord],
    distinct_cluster_only: Sequence[PairwisePreferenceRecord],
    ranking_artifacts: RankingArtifacts,
    cluster_labels_by_id: Mapping[str, ClusterLabelRecord],
    top_k_values: Sequence[int],
) -> dict[str, Any]:
    return {
        "display_name": "Random Expected",
        "pairwise_evaluation": {
            "all_preferences": _random_pairwise_metrics(len(all_preferences)),
            "pairwise_label": _random_pairwise_metrics(len(pairwise_only)),
            "cluster_label": _random_pairwise_metrics(len(cluster_only)),
            "all_preferences_distinct": _random_pairwise_metrics(len(distinct_preferences)),
            "pairwise_label_distinct": _random_pairwise_metrics(len(distinct_pairwise_only)),
            "cluster_label_distinct": _random_pairwise_metrics(len(distinct_cluster_only)),
        },
        "cluster_evaluation": _random_cluster_metrics(
            ranking_artifacts=ranking_artifacts,
            cluster_labels_by_id=cluster_labels_by_id,
            top_k_values=top_k_values,
        ),
    }


def _random_pairwise_metrics(evaluated_pairs: int) -> dict[str, Any]:
    return {
        "evaluated_pairs": int(evaluated_pairs),
        "accuracy": 0.5 if evaluated_pairs else None,
        "mean_margin": None,
        "median_margin": None,
        "mean_correct_margin": None,
        "mean_incorrect_margin": None,
    }


def _random_cluster_metrics(
    *,
    ranking_artifacts: RankingArtifacts,
    cluster_labels_by_id: Mapping[str, ClusterLabelRecord],
    top_k_values: Sequence[int],
) -> dict[str, Any]:
    top_k_values = tuple(sorted(set(int(value) for value in top_k_values if int(value) > 0)))
    hit_sums = {value: 0.0 for value in top_k_values}
    eligible_counts = {value: 0 for value in top_k_values}
    first_best_ranks: list[float] = []
    evaluated_clusters = 0
    skipped_without_best = 0
    missing_rankings = 0
    multi_best_clusters = 0

    for cluster_id, label_record in sorted(cluster_labels_by_id.items()):
        members = ranking_artifacts.clusters_by_id.get(cluster_id)
        if not members:
            missing_rankings += 1
            continue
        best_count = len(set(label_record.best_image_ids))
        if best_count <= 0:
            skipped_without_best += 1
            continue
        if best_count > 1:
            multi_best_clusters += 1
        evaluated_clusters += 1
        cluster_size = len(members)
        best_count = min(best_count, cluster_size)
        first_best_ranks.append((cluster_size + 1.0) / (best_count + 1.0))
        for value in top_k_values:
            if cluster_size < value:
                continue
            eligible_counts[value] += 1
            hit_sums[value] += _random_top_k_hit_probability(
                cluster_size=cluster_size,
                best_count=best_count,
                top_k=value,
            )

    return {
        "labeled_clusters": len(cluster_labels_by_id),
        "evaluated_clusters": evaluated_clusters,
        "multi_best_clusters": multi_best_clusters,
        "skipped_clusters_without_best": skipped_without_best,
        "missing_ranked_clusters": missing_rankings,
        "top_k_metrics": {
            f"top_{value}": {
                "eligible_clusters": eligible_counts[value],
                "hit_count": hit_sums[value],
                "hit_rate": hit_sums[value] / eligible_counts[value] if eligible_counts[value] else None,
            }
            for value in top_k_values
        },
        "mean_first_human_best_rank": float(np.mean(first_best_ranks)) if first_best_ranks else None,
        "median_first_human_best_rank": float(np.median(first_best_ranks)) if first_best_ranks else None,
    }


def _random_top_k_hit_probability(*, cluster_size: int, best_count: int, top_k: int) -> float:
    if best_count <= 0 or top_k <= 0:
        return 0.0
    if top_k >= cluster_size:
        return 1.0
    miss_count = max(0, cluster_size - best_count)
    if miss_count < top_k:
        return 1.0
    return 1.0 - (math.comb(miss_count, top_k) / math.comb(cluster_size, top_k))


def _file_order_scores(ranking_artifacts: RankingArtifacts) -> np.ndarray:
    scores = np.zeros((len(ranking_artifacts.ordered_images),), dtype=np.float32)
    for image in ranking_artifacts.ordered_images:
        scores[image.embedding_index] = -float(image.cluster_position) - float(image.embedding_index) * 1e-9
    return scores


def _dino_centrality_scores(ranking_artifacts: RankingArtifacts) -> np.ndarray:
    scores = np.zeros((len(ranking_artifacts.ordered_images),), dtype=np.float32)
    embeddings = _l2_normalize(ranking_artifacts.embeddings.astype(np.float32, copy=False))
    for members in ranking_artifacts.clusters_by_id.values():
        if not members:
            continue
        indices = np.asarray([member.embedding_index for member in members], dtype=np.int64)
        cluster_embeddings = embeddings[indices]
        centroid = _l2_normalize(cluster_embeddings.mean(axis=0, keepdims=True))[0]
        centrality = cluster_embeddings @ centroid
        for member, score in zip(members, centrality):
            scores[member.embedding_index] = float(score)
    return scores


def _near_identical_preference_filter(
    preferences: Sequence[PairwisePreferenceRecord],
    *,
    ranking_artifacts: RankingArtifacts,
    threshold: float,
) -> dict[str, Any]:
    threshold = max(-1.0, min(1.0, float(threshold)))
    embeddings = _l2_normalize(ranking_artifacts.embeddings.astype(np.float32, copy=False))
    keys: set[tuple[str, str, str]] = set()
    similarities: list[float] = []
    flagged_similarities: list[float] = []
    by_origin: dict[str, int] = {}
    by_source_mode: dict[str, int] = {}

    for preference in preferences:
        similarity = float(embeddings[preference.preferred_index] @ embeddings[preference.other_index])
        similarities.append(similarity)
        if similarity < threshold:
            continue
        keys.add(_preference_identity(preference))
        flagged_similarities.append(similarity)
        by_origin[preference.label_origin] = by_origin.get(preference.label_origin, 0) + 1
        by_source_mode[preference.source_mode] = by_source_mode.get(preference.source_mode, 0) + 1

    return {
        "enabled": True,
        "method": "dino_embedding_cosine",
        "threshold": threshold,
        "total_pairs": len(preferences),
        "flagged_pairs": len(flagged_similarities),
        "kept_pairs": len(preferences) - len(flagged_similarities),
        "flagged_fraction": (len(flagged_similarities) / len(preferences)) if preferences else 0.0,
        "mean_similarity": float(np.mean(similarities)) if similarities else None,
        "median_similarity": float(np.median(similarities)) if similarities else None,
        "min_flagged_similarity": float(min(flagged_similarities)) if flagged_similarities else None,
        "max_flagged_similarity": float(max(flagged_similarities)) if flagged_similarities else None,
        "by_label_origin": by_origin,
        "by_source_mode": by_source_mode,
        "keys": keys,
    }


def _exclude_preferences(
    preferences: Sequence[PairwisePreferenceRecord],
    excluded_keys: set[tuple[str, str, str]],
) -> list[PairwisePreferenceRecord]:
    if not excluded_keys:
        return list(preferences)
    return [
        preference
        for preference in preferences
        if _preference_identity(preference) not in excluded_keys
    ]


def _preference_identity(preference: PairwisePreferenceRecord) -> tuple[str, str, str]:
    left, right = sorted((preference.preferred_image_id, preference.other_image_id))
    return (left, right, preference.label_origin)


def _l2_normalize(matrix: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms = np.where(norms <= 1e-12, 1.0, norms)
    return matrix / norms


def _rank_clusters_from_scores(
    ranking_artifacts: RankingArtifacts,
    scores: np.ndarray,
) -> dict[str, list[SignalRankedClusterMember]]:
    ranked: dict[str, list[SignalRankedClusterMember]] = {}
    for cluster_id, members in sorted(ranking_artifacts.clusters_by_id.items()):
        ordered = sorted(
            members,
            key=lambda member: (
                -float(scores[member.embedding_index]),
                member.cluster_position,
                member.file_name.casefold(),
                member.image_id,
            ),
        )
        ranked[cluster_id] = [
            SignalRankedClusterMember(
                cluster_id=member.cluster_id,
                cluster_size=member.cluster_size,
                rank_in_cluster=rank_index,
                image_id=member.image_id,
                score=float(scores[member.embedding_index]),
                file_path=member.file_path,
                relative_path=member.relative_path,
                file_name=member.file_name,
                capture_timestamp=member.capture_timestamp,
                capture_time_source=member.capture_time_source,
                base_score=float(scores[member.embedding_index]),
            )
            for rank_index, member in enumerate(ordered, start=1)
        ]
    return ranked


def _load_signal_scores(
    *,
    signals_path: Path,
    ranking_artifacts: RankingArtifacts,
) -> dict[str, Any] | None:
    try:
        payload = json.loads(signals_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if isinstance(payload, dict):
        payload = payload.get("records", [])
    if not isinstance(payload, list):
        return None

    score_by_id: dict[str, float] = {}
    for item in payload:
        if not isinstance(item, dict):
            continue
        image_id = str(item.get("image_id") or "").strip()
        if not image_id:
            continue
        final = item.get("final")
        personal = item.get("personal")
        score = final.get("score") if isinstance(final, dict) else None
        if score is None and isinstance(personal, dict):
            score = personal.get("score")
        try:
            score_by_id[image_id] = float(score)
        except (TypeError, ValueError):
            continue

    if not score_by_id:
        return None

    scores = np.zeros((len(ranking_artifacts.ordered_images),), dtype=np.float32)
    missing_count = 0
    for image in ranking_artifacts.ordered_images:
        score = score_by_id.get(image.image_id)
        if score is None:
            missing_count += 1
            score = 0.0
        scores[image.embedding_index] = float(score)
    return {"scores": scores, "missing_count": missing_count}


def _load_signal_feature_scores(
    *,
    signals_path: Path,
    ranking_artifacts: RankingArtifacts,
) -> dict[str, dict[str, Any]]:
    try:
        payload = json.loads(signals_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if isinstance(payload, dict):
        payload = payload.get("records", [])
    if not isinstance(payload, list):
        return {}

    feature_by_id: dict[str, dict[str, float]] = {}
    for item in payload:
        if not isinstance(item, dict):
            continue
        image_id = str(item.get("image_id") or "").strip()
        if not image_id:
            continue
        personal = item.get("personal")
        feature_values = personal.get("feature_values") if isinstance(personal, dict) else None
        if not isinstance(feature_values, dict):
            continue
        normalized_features: dict[str, float] = {}
        for feature_name in FEATURE_NAMES:
            try:
                normalized_features[feature_name] = float(feature_values.get(feature_name))
            except (TypeError, ValueError):
                continue
        if normalized_features:
            feature_by_id[image_id] = normalized_features

    outputs: dict[str, dict[str, Any]] = {}
    for feature_name in FEATURE_NAMES:
        scores = np.zeros((len(ranking_artifacts.ordered_images),), dtype=np.float32)
        missing_count = 0
        found = False
        for image in ranking_artifacts.ordered_images:
            value = feature_by_id.get(image.image_id, {}).get(feature_name)
            if value is None:
                missing_count += 1
                value = 0.0
            else:
                found = True
            scores[image.embedding_index] = float(value)
        if found:
            outputs[feature_name] = {"scores": scores, "missing_count": missing_count}
    return outputs


def _score_is_constant(scores: np.ndarray) -> bool:
    if scores.size <= 1:
        return True
    return float(np.nanmax(scores) - np.nanmin(scores)) <= 1e-8


def _metric_delta(
    left: Mapping[str, Any],
    right: Mapping[str, Any],
    *path: str,
    lower_is_better: bool = False,
) -> float | None:
    def read(payload: Mapping[str, Any]) -> float | None:
        current: Any = payload
        for key in path:
            if not isinstance(current, Mapping):
                return None
            current = current.get(key)
        if current is None:
            return None
        try:
            return float(current)
        except (TypeError, ValueError):
            return None

    left_value = read(left)
    right_value = read(right)
    if left_value is None or right_value is None:
        return None
    delta = left_value - right_value
    return -delta if lower_is_better else delta


def _top_k_delta(left: Mapping[str, Any], right: Mapping[str, Any], key: str) -> float | None:
    return _metric_delta(left, right, "cluster_evaluation", "top_k_metrics", key, "hit_rate")


def _save_signal_evaluation_summary_csv(path: Path, scorers: Mapping[str, Mapping[str, Any]]) -> None:
    fieldnames = [
        "scorer",
        "display_name",
        "pairwise_accuracy",
        "pairwise_accuracy_distinct",
        "pairwise_evaluated_pairs",
        "pairwise_evaluated_pairs_distinct",
        "cluster_top1_hit_rate",
        "cluster_top3_hit_rate",
        "mean_first_human_best_rank",
        "evaluated_clusters",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        preferred_order = ("random_expected", "file_order", "dino_centrality", "transparent_combiner")
        ordered_keys = [
            *preferred_order,
            *sorted(key for key in scorers.keys() if key not in preferred_order),
        ]
        for key in ordered_keys:
            scorer = scorers.get(key)
            if not isinstance(scorer, Mapping):
                continue
            pairwise = scorer.get("pairwise_evaluation", {})
            pairwise_all = pairwise.get("all_preferences", {}) if isinstance(pairwise, Mapping) else {}
            pairwise_distinct = pairwise.get("all_preferences_distinct", {}) if isinstance(pairwise, Mapping) else {}
            cluster = scorer.get("cluster_evaluation", {})
            top_k = cluster.get("top_k_metrics", {}) if isinstance(cluster, Mapping) else {}
            top1 = top_k.get("top_1", {}) if isinstance(top_k, Mapping) else {}
            top3 = top_k.get("top_3", {}) if isinstance(top_k, Mapping) else {}
            writer.writerow(
                {
                    "scorer": key,
                    "display_name": scorer.get("display_name", key),
                    "pairwise_accuracy": pairwise_all.get("accuracy") if isinstance(pairwise_all, Mapping) else None,
                    "pairwise_accuracy_distinct": pairwise_distinct.get("accuracy") if isinstance(pairwise_distinct, Mapping) else None,
                    "pairwise_evaluated_pairs": pairwise_all.get("evaluated_pairs") if isinstance(pairwise_all, Mapping) else None,
                    "pairwise_evaluated_pairs_distinct": pairwise_distinct.get("evaluated_pairs") if isinstance(pairwise_distinct, Mapping) else None,
                    "cluster_top1_hit_rate": top1.get("hit_rate") if isinstance(top1, Mapping) else None,
                    "cluster_top3_hit_rate": top3.get("hit_rate") if isinstance(top3, Mapping) else None,
                    "mean_first_human_best_rank": cluster.get("mean_first_human_best_rank") if isinstance(cluster, Mapping) else None,
                    "evaluated_clusters": cluster.get("evaluated_clusters") if isinstance(cluster, Mapping) else None,
                }
            )
