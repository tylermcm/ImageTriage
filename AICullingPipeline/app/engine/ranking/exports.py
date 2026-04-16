"""Reusable ranked-export helpers for Week 5 evaluation and reporting."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Iterable, List, Optional

from app.config import RankingReportConfig
from app.storage.ranking_artifacts import (
    ClusterLabelRecord,
    RankingArtifacts,
    load_latest_cluster_labels,
    load_ranking_artifacts,
    save_ranking_summary_json,
)

if TYPE_CHECKING:
    from app.engine.ranking.service import RankedClusterMember, RankerService


@dataclass(frozen=True)
class RankedExportRow:
    """One reusable ranked-output row enriched with optional human labels."""

    cluster_id: str
    cluster_size: int
    cluster_position: int
    cluster_reason: str
    rank_in_cluster: int
    image_id: str
    score: float
    file_path: str
    relative_path: str
    file_name: str
    capture_timestamp: str
    capture_time_source: str
    human_label: Optional[str]
    is_human_best: bool
    is_human_acceptable: bool
    is_human_reject: bool
    cluster_has_human_labels: bool
    cluster_has_human_best: bool
    model_top1_matches_human_best: Optional[bool]
    model_top1_is_human_non_reject: Optional[bool]

    def to_dict(self) -> Dict[str, Any]:
        """Convert the row into a JSON/CSV-friendly dictionary."""

        return {
            "cluster_id": self.cluster_id,
            "cluster_size": self.cluster_size,
            "cluster_position": self.cluster_position,
            "cluster_reason": self.cluster_reason,
            "rank_in_cluster": self.rank_in_cluster,
            "image_id": self.image_id,
            "score": self.score,
            "file_path": self.file_path,
            "relative_path": self.relative_path,
            "file_name": self.file_name,
            "capture_timestamp": self.capture_timestamp,
            "capture_time_source": self.capture_time_source,
            "human_label": self.human_label or "",
            "is_human_best": self.is_human_best,
            "is_human_acceptable": self.is_human_acceptable,
            "is_human_reject": self.is_human_reject,
            "cluster_has_human_labels": self.cluster_has_human_labels,
            "cluster_has_human_best": self.cluster_has_human_best,
            "model_top1_matches_human_best": self.model_top1_matches_human_best,
            "model_top1_is_human_non_reject": self.model_top1_is_human_non_reject,
        }


def build_ranked_export_rows(
    ranked_clusters: Dict[str, List["RankedClusterMember"]],
    ranking_artifacts: RankingArtifacts,
    *,
    cluster_labels_by_id: Optional[Dict[str, ClusterLabelRecord]] = None,
) -> List[RankedExportRow]:
    """Build a stable ranked export from scored clusters plus optional human labels."""

    cluster_labels_by_id = cluster_labels_by_id or {}
    rows: List[RankedExportRow] = []

    for cluster_id in sorted(ranked_clusters.keys()):
        members = ranked_clusters[cluster_id]
        label_record = cluster_labels_by_id.get(cluster_id)
        cluster_has_human_labels = label_record is not None
        cluster_has_human_best = bool(label_record and label_record.best_image_ids)
        top_member = members[0] if members else None
        top_matches_best = (
            top_member.image_id in set(label_record.best_image_ids)
            if top_member is not None and cluster_has_human_best and label_record is not None
            else None
        )
        top_is_non_reject = (
            top_member.image_id not in set(label_record.reject_image_ids)
            if top_member is not None and label_record is not None
            else None
        )

        for member in members:
            artifact = ranking_artifacts.images_by_id[member.image_id]
            human_label = label_record.label_for_image(member.image_id) if label_record else None
            rows.append(
                RankedExportRow(
                    cluster_id=member.cluster_id,
                    cluster_size=member.cluster_size,
                    cluster_position=artifact.cluster_position,
                    cluster_reason=artifact.cluster_reason,
                    rank_in_cluster=member.rank_in_cluster,
                    image_id=member.image_id,
                    score=member.score,
                    file_path=member.file_path,
                    relative_path=member.relative_path,
                    file_name=member.file_name,
                    capture_timestamp=member.capture_timestamp,
                    capture_time_source=member.capture_time_source,
                    human_label=human_label,
                    is_human_best=human_label == "best",
                    is_human_acceptable=human_label == "acceptable",
                    is_human_reject=human_label == "reject",
                    cluster_has_human_labels=cluster_has_human_labels,
                    cluster_has_human_best=cluster_has_human_best,
                    model_top1_matches_human_best=top_matches_best,
                    model_top1_is_human_non_reject=top_is_non_reject,
                )
            )
    return rows


def group_ranked_export_rows(
    rows: Iterable[RankedExportRow],
) -> Dict[str, List[RankedExportRow]]:
    """Group export rows by cluster ID in rank order."""

    grouped: Dict[str, List[RankedExportRow]] = {}
    for row in rows:
        grouped.setdefault(row.cluster_id, []).append(row)
    for members in grouped.values():
        members.sort(key=lambda item: (item.rank_in_cluster, item.cluster_position, item.image_id))
    return grouped


def summarize_ranked_export(
    rows: Iterable[RankedExportRow],
    *,
    checkpoint_path: Path,
    model_architecture: str,
    normalize_embeddings: bool,
) -> Dict[str, Any]:
    """Build a machine-readable summary for ranked export outputs."""

    materialized = list(rows)
    grouped = group_ranked_export_rows(materialized)
    cluster_sizes = [members[0].cluster_size for members in grouped.values() if members]
    labeled_clusters = [members for members in grouped.values() if members and members[0].cluster_has_human_labels]
    clusters_with_human_best = [
        members for members in grouped.values() if members and members[0].cluster_has_human_best
    ]
    top1_best_matches = sum(
        1
        for members in clusters_with_human_best
        if members[0].model_top1_matches_human_best is True
    )
    top1_non_reject_matches = sum(
        1
        for members in labeled_clusters
        if members[0].model_top1_is_human_non_reject is True
    )

    return {
        "checkpoint_path": str(checkpoint_path),
        "total_images": len(materialized),
        "total_clusters": len(grouped),
        "singleton_clusters": sum(size == 1 for size in cluster_sizes),
        "largest_cluster_size": max(cluster_sizes) if cluster_sizes else 0,
        "labeled_clusters": len(labeled_clusters),
        "clusters_with_human_best": len(clusters_with_human_best),
        "model_top1_matches_human_best": top1_best_matches,
        "model_top1_human_best_match_rate": (
            top1_best_matches / len(clusters_with_human_best)
            if clusters_with_human_best
            else None
        ),
        "model_top1_non_reject_count": top1_non_reject_matches,
        "model_top1_non_reject_rate": (
            top1_non_reject_matches / len(labeled_clusters)
            if labeled_clusters
            else None
        ),
        "model_architecture": model_architecture,
        "normalize_embeddings": normalize_embeddings,
    }


def save_ranked_export_csv(path: Path, rows: Iterable[RankedExportRow]) -> None:
    """Save the Week 5 ranked export rows to CSV."""

    materialized = list(rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "cluster_id",
        "cluster_size",
        "cluster_position",
        "cluster_reason",
        "rank_in_cluster",
        "image_id",
        "score",
        "file_path",
        "relative_path",
        "file_name",
        "capture_timestamp",
        "capture_time_source",
        "human_label",
        "is_human_best",
        "is_human_acceptable",
        "is_human_reject",
        "cluster_has_human_labels",
        "cluster_has_human_best",
        "model_top1_matches_human_best",
        "model_top1_is_human_non_reject",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in materialized:
            writer.writerow(row.to_dict())


def export_ranked_results(config: RankingReportConfig) -> Dict[str, Path]:
    """Load artifacts/checkpoint, build ranked export rows, and save report-ready outputs."""

    from app.engine.ranking.reporting import build_cluster_report
    from app.engine.ranking.service import load_ranker

    config.output_dir.mkdir(parents=True, exist_ok=True)
    ranking_artifacts = load_ranking_artifacts(
        config.artifacts_dir,
        metadata_filename=config.metadata_filename,
        embeddings_filename=config.embeddings_filename,
        image_ids_filename=config.image_ids_filename,
        clusters_filename=config.clusters_filename,
    )
    cluster_labels_by_id = (
        load_latest_cluster_labels(
            labels_dir=config.labels_dir,
            cluster_labels_filename=config.cluster_labels_filename,
        )
        if config.labels_dir is not None
        else {}
    )
    service = load_ranker(
        config.checkpoint_path,
        device=config.device,
        reference_bank_path=config.reference_bank_path,
    )
    ranked_clusters = service.rank_clusters(
        ranking_artifacts,
        batch_size=config.score_batch_size,
    )
    rows = build_ranked_export_rows(
        ranked_clusters,
        ranking_artifacts,
        cluster_labels_by_id=cluster_labels_by_id,
    )

    export_path = config.output_dir / config.ranked_export_filename
    summary_path = config.output_dir / config.summary_filename
    html_path = config.output_dir / config.html_report_filename

    save_ranked_export_csv(export_path, rows)
    summary = summarize_ranked_export(
        rows,
        checkpoint_path=config.checkpoint_path,
        model_architecture=service.checkpoint_metadata["model_config"]["architecture"],
        normalize_embeddings=service.normalize_embeddings,
    )
    summary["reference_conditioning_enabled"] = service.reference_conditioning_enabled
    summary["reference_bank_path"] = (
        str(config.reference_bank_path)
        if config.reference_bank_path is not None
        else service.checkpoint_metadata.get("reference_conditioning", {}).get("reference_bank_path")
    )
    summary["reference_feature_names"] = list(service.reference_feature_names)
    save_ranking_summary_json(summary_path, summary)
    build_cluster_report(
        html_path,
        rows,
        summary=summary,
        include_singletons=config.html_include_singletons,
        max_clusters=config.html_max_clusters,
    )

    return {
        "ranked_export": export_path,
        "summary": summary_path,
        "html_report": html_path,
    }
