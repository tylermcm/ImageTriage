"""Reusable Week 5 evaluation logic for ranked culling outputs."""

from __future__ import annotations

import csv
import math
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence, Tuple

import numpy as np

from app.config import RankingEvaluationConfig
from app.engine.ranking.exports import (
    RankedExportRow,
    build_ranked_export_rows,
    group_ranked_export_rows,
)
from app.engine.ranking.service import (
    RankedClusterMember,
    load_ranker,
    rank_clusters_by_embedding_centrality,
)
from app.storage.ranking_artifacts import (
    ClusterLabelRecord,
    PairwisePreferenceRecord,
    load_latest_cluster_labels,
    load_preference_labels,
    load_ranking_artifacts,
    save_ranking_summary_json,
)


def evaluate_pairwise_preferences(
    scores: np.ndarray,
    preferences: Sequence[PairwisePreferenceRecord],
) -> Dict[str, Any]:
    """Evaluate pairwise agreement for one set of labeled preferences."""

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
        "mean_correct_margin": (
            float(correct_margins.mean()) if correct_margins.size else None
        ),
        "mean_incorrect_margin": (
            float(incorrect_margins.mean()) if incorrect_margins.size else None
        ),
    }


def evaluate_cluster_rankings(
    rows: Iterable[RankedExportRow],
    *,
    cluster_labels_by_id: Dict[str, ClusterLabelRecord],
    top_k_values: Sequence[int],
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """Evaluate ranked cluster winners against saved human cluster labels."""

    grouped = group_ranked_export_rows(rows)
    breakdown_rows: List[Dict[str, Any]] = []
    top_k_values = tuple(sorted(set(int(value) for value in top_k_values if int(value) > 0)))

    evaluated_clusters = 0
    skipped_without_best = 0
    missing_rankings = 0
    first_best_ranks: List[int] = []
    hit_counts = {value: 0 for value in top_k_values}
    eligible_counts = {value: 0 for value in top_k_values}

    for cluster_id, label_record in sorted(cluster_labels_by_id.items()):
        members = grouped.get(cluster_id)
        if not members:
            missing_rankings += 1
            breakdown_rows.append(
                {
                    "cluster_id": cluster_id,
                    "cluster_size": 0,
                    "evaluation_status": "missing_ranked_output",
                    "model_top1_file_name": "",
                    "model_top1_score": "",
                    "human_best_files": "",
                    "human_acceptable_files": "",
                    "human_reject_files": "",
                }
            )
            continue

        image_name_by_id = {member.image_id: member.file_name for member in members}
        human_best_ids = set(label_record.best_image_ids)
        human_acceptable_ids = set(label_record.acceptable_image_ids)
        human_reject_ids = set(label_record.reject_image_ids)
        top_member = members[0]

        row: Dict[str, Any] = {
            "cluster_id": cluster_id,
            "cluster_size": members[0].cluster_size,
            "evaluation_status": "evaluated" if human_best_ids else "skipped_no_human_best",
            "model_top1_file_name": top_member.file_name,
            "model_top1_score": top_member.score,
            "human_best_files": "; ".join(
                image_name_by_id[image_id] for image_id in label_record.best_image_ids if image_id in image_name_by_id
            ),
            "human_acceptable_files": "; ".join(
                image_name_by_id[image_id]
                for image_id in label_record.acceptable_image_ids
                if image_id in image_name_by_id
            ),
            "human_reject_files": "; ".join(
                image_name_by_id[image_id] for image_id in label_record.reject_image_ids if image_id in image_name_by_id
            ),
            "model_top1_human_label": top_member.human_label or "",
            "model_top1_is_human_non_reject": top_member.model_top1_is_human_non_reject,
        }

        if not human_best_ids:
            skipped_without_best += 1
            for value in top_k_values:
                row[f"top_{value}_hit"] = ""
            row["first_human_best_rank"] = ""
            breakdown_rows.append(row)
            continue

        evaluated_clusters += 1
        first_best_rank = next(
            member.rank_in_cluster for member in members if member.image_id in human_best_ids
        )
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

    summary = {
        "labeled_clusters": len(cluster_labels_by_id),
        "evaluated_clusters": evaluated_clusters,
        "skipped_clusters_without_best": skipped_without_best,
        "missing_ranked_clusters": missing_rankings,
        "top_k_metrics": {
            f"top_{value}": {
                "eligible_clusters": eligible_counts[value],
                "hit_count": hit_counts[value],
                "hit_rate": (
                    hit_counts[value] / eligible_counts[value]
                    if eligible_counts[value]
                    else None
                ),
            }
            for value in top_k_values
        },
        "mean_first_human_best_rank": (
            float(np.mean(first_best_ranks)) if first_best_ranks else None
        ),
        "median_first_human_best_rank": (
            float(np.median(first_best_ranks)) if first_best_ranks else None
        ),
    }
    return summary, breakdown_rows


def evaluate_ranker(config: RankingEvaluationConfig) -> Dict[str, Path]:
    """Run the reusable Week 5 evaluation pipeline and save machine-readable outputs."""

    config.output_dir.mkdir(parents=True, exist_ok=True)
    ranking_artifacts = load_ranking_artifacts(
        config.artifacts_dir,
        metadata_filename=config.metadata_filename,
        embeddings_filename=config.embeddings_filename,
        image_ids_filename=config.image_ids_filename,
        clusters_filename=config.clusters_filename,
    )
    service = load_ranker(
        config.checkpoint_path,
        device=config.device,
        reference_bank_path=config.reference_bank_path,
    )
    scores = service.score_embeddings(
        ranking_artifacts.embeddings,
        batch_size=config.score_batch_size,
    )

    loaded_labels = load_preference_labels(
        labels_dir=config.labels_dir,
        ranking_artifacts=ranking_artifacts,
        pairwise_labels_filename=config.pairwise_labels_filename,
        cluster_labels_filename=config.cluster_labels_filename,
        include_cluster_label_pairs=config.include_cluster_label_pairs,
        skip_ties=True,
    )
    pairwise_only = [
        preference
        for preference in loaded_labels.preferences
        if preference.label_origin == "pairwise_label"
    ]
    cluster_only = [
        preference
        for preference in loaded_labels.preferences
        if preference.label_origin == "cluster_label"
    ]

    pairwise_metrics = _evaluate_pairwise_splits(
        scores,
        all_preferences=loaded_labels.preferences,
        pairwise_only=pairwise_only,
        cluster_only=cluster_only,
    )

    cluster_labels_by_id = load_latest_cluster_labels(
        labels_dir=config.labels_dir,
        cluster_labels_filename=config.cluster_labels_filename,
    )
    ranked_clusters = service.rank_clusters(
        ranking_artifacts,
        batch_size=config.score_batch_size,
    )
    export_rows = build_ranked_export_rows(
        ranked_clusters,
        ranking_artifacts,
        cluster_labels_by_id=cluster_labels_by_id,
    )
    cluster_metrics, cluster_breakdown_rows = evaluate_cluster_rankings(
        export_rows,
        cluster_labels_by_id=cluster_labels_by_id,
        top_k_values=config.top_k_values,
    )
    baseline_comparison = _build_baseline_comparison(
        ranking_artifacts=ranking_artifacts,
        trained_scores=scores,
        trained_ranked_clusters=ranked_clusters,
        loaded_preferences=loaded_labels.preferences,
        pairwise_only=pairwise_only,
        cluster_only=cluster_only,
        cluster_labels_by_id=cluster_labels_by_id,
        top_k_values=config.top_k_values,
    )

    metrics_skipped: List[str] = []
    if pairwise_metrics["pairwise_label"]["evaluated_pairs"] == 0:
        metrics_skipped.append("Pairwise-label accuracy skipped because no usable pairwise labels were available.")
    if pairwise_metrics["cluster_label"]["evaluated_pairs"] == 0:
        metrics_skipped.append("Cluster-derived pairwise accuracy skipped because no usable cluster label pairs were available.")
    if cluster_metrics["evaluated_clusters"] == 0:
        metrics_skipped.append("Cluster winner metrics skipped because no clusters with saved human best labels were available.")

    summary = {
        "checkpoint_path": str(config.checkpoint_path),
        "resolved_device": str(service.device),
        "model_architecture": service.checkpoint_metadata["model_config"]["architecture"],
        "normalize_embeddings": service.normalize_embeddings,
        "reference_conditioning_enabled": service.reference_conditioning_enabled,
        "reference_bank_path": (
            str(config.reference_bank_path)
            if config.reference_bank_path is not None
            else service.checkpoint_metadata.get("reference_conditioning", {}).get("reference_bank_path")
        ),
        "reference_feature_names": list(service.reference_feature_names),
        "dataset_summary": {
            "total_images": len(ranking_artifacts.ordered_images),
            "total_clusters": len(ranking_artifacts.clusters_by_id),
            "singleton_clusters": sum(
                len(members) == 1 for members in ranking_artifacts.clusters_by_id.values()
            ),
            "largest_cluster_size": max(
                (len(members) for members in ranking_artifacts.clusters_by_id.values()),
                default=0,
            ),
        },
        "label_summary": loaded_labels.summary,
        "pairwise_evaluation": pairwise_metrics,
        "cluster_evaluation": cluster_metrics,
        "baseline_comparison": baseline_comparison,
        "metrics_skipped": metrics_skipped,
        "notes": [
            "Pairwise evaluation uses the label files provided in labels_dir. For a true held-out evaluation set, point labels_dir at held-out label artifacts.",
            "Top-k cluster hit rates are only computed on clusters with at least k images and at least one saved human best label.",
            "Baseline comparison evaluates the same labels against trained ranker, DINO embedding centrality, file order, and random expected chance.",
        ],
    }

    metrics_path = config.output_dir / config.metrics_filename
    pairwise_breakdown_path = config.output_dir / config.pairwise_breakdown_filename
    cluster_breakdown_path = config.output_dir / config.cluster_breakdown_filename

    save_ranking_summary_json(metrics_path, summary)
    _save_pairwise_breakdown_csv(pairwise_breakdown_path, pairwise_metrics)
    _save_cluster_breakdown_csv(cluster_breakdown_path, cluster_breakdown_rows, config.top_k_values)

    return {
        "metrics": metrics_path,
        "pairwise_breakdown": pairwise_breakdown_path,
        "cluster_breakdown": cluster_breakdown_path,
    }


def _evaluate_pairwise_splits(
    scores: np.ndarray,
    *,
    all_preferences: Sequence[PairwisePreferenceRecord],
    pairwise_only: Sequence[PairwisePreferenceRecord],
    cluster_only: Sequence[PairwisePreferenceRecord],
) -> Dict[str, Dict[str, Any]]:
    return {
        "all_preferences": evaluate_pairwise_preferences(scores, all_preferences),
        "pairwise_label": evaluate_pairwise_preferences(scores, pairwise_only),
        "cluster_label": evaluate_pairwise_preferences(scores, cluster_only),
    }


def _build_baseline_comparison(
    *,
    ranking_artifacts: Any,
    trained_scores: np.ndarray,
    trained_ranked_clusters: Dict[str, List[RankedClusterMember]],
    loaded_preferences: Sequence[PairwisePreferenceRecord],
    pairwise_only: Sequence[PairwisePreferenceRecord],
    cluster_only: Sequence[PairwisePreferenceRecord],
    cluster_labels_by_id: Dict[str, ClusterLabelRecord],
    top_k_values: Sequence[int],
) -> Dict[str, Any]:
    """Evaluate trained ranking against simple embedding/order baselines."""

    scorers: Dict[str, Dict[str, Any]] = {}
    scorers["trained_ranker"] = _evaluate_scorer(
        name="Trained Ranker",
        scores=trained_scores,
        ranked_clusters=trained_ranked_clusters,
        ranking_artifacts=ranking_artifacts,
        all_preferences=loaded_preferences,
        pairwise_only=pairwise_only,
        cluster_only=cluster_only,
        cluster_labels_by_id=cluster_labels_by_id,
        top_k_values=top_k_values,
    )

    centrality_ranked = rank_clusters_by_embedding_centrality(ranking_artifacts)
    centrality_scores = _scores_from_ranked_clusters(
        centrality_ranked,
        ranking_artifacts=ranking_artifacts,
    )
    scorers["dino_centrality"] = _evaluate_scorer(
        name="DINO Centrality",
        scores=centrality_scores,
        ranked_clusters=centrality_ranked,
        ranking_artifacts=ranking_artifacts,
        all_preferences=loaded_preferences,
        pairwise_only=pairwise_only,
        cluster_only=cluster_only,
        cluster_labels_by_id=cluster_labels_by_id,
        top_k_values=top_k_values,
    )

    file_order_scores = _file_order_scores(ranking_artifacts)
    scorers["file_order"] = _evaluate_scorer(
        name="File Order",
        scores=file_order_scores,
        ranked_clusters=_rank_clusters_from_scores(ranking_artifacts, file_order_scores),
        ranking_artifacts=ranking_artifacts,
        all_preferences=loaded_preferences,
        pairwise_only=pairwise_only,
        cluster_only=cluster_only,
        cluster_labels_by_id=cluster_labels_by_id,
        top_k_values=top_k_values,
    )
    scorers["random_expected"] = _evaluate_random_expected(
        all_preferences=loaded_preferences,
        pairwise_only=pairwise_only,
        cluster_only=cluster_only,
        ranking_artifacts=ranking_artifacts,
        cluster_labels_by_id=cluster_labels_by_id,
        top_k_values=top_k_values,
    )

    trained = scorers["trained_ranker"]
    centrality = scorers["dino_centrality"]
    return {
        "scorers": scorers,
        "deltas_vs_dino_centrality": {
            "pairwise_accuracy": _metric_delta(
                trained,
                centrality,
                "pairwise_evaluation",
                "all_preferences",
                "accuracy",
            ),
            "cluster_top1_hit_rate": _top_k_delta(trained, centrality, "top_1"),
            "cluster_top3_hit_rate": _top_k_delta(trained, centrality, "top_3"),
            "mean_first_human_best_rank": _metric_delta(
                trained,
                centrality,
                "cluster_evaluation",
                "mean_first_human_best_rank",
                lower_is_better=True,
            ),
        },
        "notes": [
            "DINO Centrality ranks each cluster by cosine similarity to the cluster centroid.",
            "File Order ranks by original cluster/file order.",
            "Random Expected is analytical chance, not a sampled random seed.",
        ],
    }


def _evaluate_scorer(
    *,
    name: str,
    scores: np.ndarray,
    ranked_clusters: Dict[str, List[RankedClusterMember]],
    ranking_artifacts: Any,
    all_preferences: Sequence[PairwisePreferenceRecord],
    pairwise_only: Sequence[PairwisePreferenceRecord],
    cluster_only: Sequence[PairwisePreferenceRecord],
    cluster_labels_by_id: Dict[str, ClusterLabelRecord],
    top_k_values: Sequence[int],
) -> Dict[str, Any]:
    export_rows = build_ranked_export_rows(
        ranked_clusters,
        ranking_artifacts,
        cluster_labels_by_id=cluster_labels_by_id,
    )
    cluster_metrics, _cluster_breakdown_rows = evaluate_cluster_rankings(
        export_rows,
        cluster_labels_by_id=cluster_labels_by_id,
        top_k_values=top_k_values,
    )
    return {
        "display_name": name,
        "pairwise_evaluation": _evaluate_pairwise_splits(
            scores,
            all_preferences=all_preferences,
            pairwise_only=pairwise_only,
            cluster_only=cluster_only,
        ),
        "cluster_evaluation": cluster_metrics,
    }


def _scores_from_ranked_clusters(
    ranked_clusters: Dict[str, List[RankedClusterMember]],
    *,
    ranking_artifacts: Any,
) -> np.ndarray:
    scores = np.zeros((len(ranking_artifacts.ordered_images),), dtype=np.float32)
    for members in ranked_clusters.values():
        for member in members:
            image = ranking_artifacts.images_by_id.get(member.image_id)
            if image is not None:
                scores[image.embedding_index] = float(member.score)
    return scores


def _file_order_scores(ranking_artifacts: Any) -> np.ndarray:
    scores = np.zeros((len(ranking_artifacts.ordered_images),), dtype=np.float32)
    for image in ranking_artifacts.ordered_images:
        # Larger is better. The tiny embedding-index term makes deterministic ties without changing order.
        scores[image.embedding_index] = -float(image.cluster_position) - float(image.embedding_index) * 1e-9
    return scores


def _rank_clusters_from_scores(
    ranking_artifacts: Any,
    scores: np.ndarray,
) -> Dict[str, List[RankedClusterMember]]:
    ranked: Dict[str, List[RankedClusterMember]] = {}
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
            RankedClusterMember(
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
                reference_adjustment=0.0,
            )
            for rank_index, member in enumerate(ordered, start=1)
        ]
    return ranked


def _evaluate_random_expected(
    *,
    all_preferences: Sequence[PairwisePreferenceRecord],
    pairwise_only: Sequence[PairwisePreferenceRecord],
    cluster_only: Sequence[PairwisePreferenceRecord],
    ranking_artifacts: Any,
    cluster_labels_by_id: Dict[str, ClusterLabelRecord],
    top_k_values: Sequence[int],
) -> Dict[str, Any]:
    return {
        "display_name": "Random Expected",
        "pairwise_evaluation": {
            "all_preferences": _random_pairwise_metrics(len(all_preferences)),
            "pairwise_label": _random_pairwise_metrics(len(pairwise_only)),
            "cluster_label": _random_pairwise_metrics(len(cluster_only)),
        },
        "cluster_evaluation": _random_cluster_metrics(
            ranking_artifacts=ranking_artifacts,
            cluster_labels_by_id=cluster_labels_by_id,
            top_k_values=top_k_values,
        ),
    }


def _random_pairwise_metrics(evaluated_pairs: int) -> Dict[str, Any]:
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
    ranking_artifacts: Any,
    cluster_labels_by_id: Dict[str, ClusterLabelRecord],
    top_k_values: Sequence[int],
) -> Dict[str, Any]:
    top_k_values = tuple(sorted(set(int(value) for value in top_k_values if int(value) > 0)))
    hit_sums = {value: 0.0 for value in top_k_values}
    eligible_counts = {value: 0 for value in top_k_values}
    first_best_ranks: List[float] = []
    evaluated_clusters = 0
    skipped_without_best = 0
    missing_rankings = 0

    for cluster_id, label_record in sorted(cluster_labels_by_id.items()):
        members = ranking_artifacts.clusters_by_id.get(cluster_id)
        if not members:
            missing_rankings += 1
            continue
        best_count = len(set(label_record.best_image_ids))
        if best_count <= 0:
            skipped_without_best += 1
            continue

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
        "skipped_clusters_without_best": skipped_without_best,
        "missing_ranked_clusters": missing_rankings,
        "top_k_metrics": {
            f"top_{value}": {
                "eligible_clusters": eligible_counts[value],
                "hit_count": hit_sums[value],
                "hit_rate": (
                    hit_sums[value] / eligible_counts[value]
                    if eligible_counts[value]
                    else None
                ),
            }
            for value in top_k_values
        },
        "mean_first_human_best_rank": (
            float(np.mean(first_best_ranks)) if first_best_ranks else None
        ),
        "median_first_human_best_rank": (
            float(np.median(first_best_ranks)) if first_best_ranks else None
        ),
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


def _metric_delta(
    left: Dict[str, Any],
    right: Dict[str, Any],
    *path: str,
    lower_is_better: bool = False,
) -> float | None:
    def read(payload: Dict[str, Any]) -> float | None:
        current: Any = payload
        for key in path:
            if not isinstance(current, dict):
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


def _top_k_delta(left: Dict[str, Any], right: Dict[str, Any], key: str) -> float | None:
    return _metric_delta(
        left,
        right,
        "cluster_evaluation",
        "top_k_metrics",
        key,
        "hit_rate",
    )


def _save_pairwise_breakdown_csv(path: Path, payload: Dict[str, Dict[str, Any]]) -> None:
    """Save pairwise evaluation summaries to CSV."""

    fieldnames = [
        "split_name",
        "evaluated_pairs",
        "accuracy",
        "mean_margin",
        "median_margin",
        "mean_correct_margin",
        "mean_incorrect_margin",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for split_name in ("all_preferences", "pairwise_label", "cluster_label"):
            row = dict(payload[split_name])
            row["split_name"] = split_name
            writer.writerow({field: row.get(field) for field in fieldnames})


def _save_cluster_breakdown_csv(
    path: Path,
    rows: Iterable[Dict[str, Any]],
    top_k_values: Sequence[int],
) -> None:
    """Save per-cluster evaluation rows to CSV."""

    fieldnames = [
        "cluster_id",
        "cluster_size",
        "evaluation_status",
        "model_top1_file_name",
        "model_top1_score",
        "model_top1_human_label",
        "model_top1_is_human_non_reject",
        "first_human_best_rank",
    ]
    fieldnames.extend(f"top_{value}_hit" for value in top_k_values)
    fieldnames.extend(
        [
            "human_best_files",
            "human_acceptable_files",
            "human_reject_files",
        ]
    )

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field) for field in fieldnames})
