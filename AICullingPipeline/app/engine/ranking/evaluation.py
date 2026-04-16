"""Reusable Week 5 evaluation logic for ranked culling outputs."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence, Tuple

import numpy as np

from app.config import RankingEvaluationConfig
from app.engine.ranking.exports import (
    RankedExportRow,
    build_ranked_export_rows,
    group_ranked_export_rows,
)
from app.engine.ranking.service import load_ranker
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

    pairwise_metrics = {
        "all_preferences": evaluate_pairwise_preferences(scores, loaded_labels.preferences),
        "pairwise_label": evaluate_pairwise_preferences(scores, pairwise_only),
        "cluster_label": evaluate_pairwise_preferences(scores, cluster_only),
    }

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
        "metrics_skipped": metrics_skipped,
        "notes": [
            "Pairwise evaluation uses the label files provided in labels_dir. For a true held-out evaluation set, point labels_dir at held-out label artifacts.",
            "Top-k cluster hit rates are only computed on clusters with at least k images and at least one saved human best label.",
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
