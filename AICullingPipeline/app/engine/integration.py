"""Stable host-facing API aliases and adapter utilities for engine integration."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from app.config import ClusteringConfig, ExtractionConfig
from app.contracts import (
    ENGINE_API_VERSION,
    ArtifactBundleContract,
    CullingGroupRecord,
    EngineImageIdentity,
    EngineImageRecord,
    EngineRankedResult,
    EngineScoreRecord,
    HumanDecisionRecord,
    ReferenceBankDescriptor,
)
from app.engine.clustering import run_similarity_clustering
from app.engine.extraction import run_embedding_extraction
from app.engine.ranking.service import RankerService, load_ranker
from app.storage.artifact_bundle import (
    ArtifactBundlePaths,
    build_artifact_contract,
    discover_artifact_bundle,
)
from app.storage.ranking_artifacts import (
    ClusterLabelRecord,
    RankingArtifacts,
    load_latest_cluster_labels,
    load_ranking_artifacts,
)


def extract_embeddings(config: ExtractionConfig) -> Dict[str, Path]:
    """Stable host-facing alias for the Week 1 embedding pipeline."""

    return run_embedding_extraction(config)


def build_culling_groups(config: ClusteringConfig) -> Dict[str, Path]:
    """Stable host-facing alias for the Week 2 / 2.5 clustering pipeline."""

    return run_similarity_clustering(config)


@dataclass(frozen=True)
class AdapterSummary:
    """Small summary of the current adapter state for diagnostics and host UIs."""

    engine_api_version: str
    total_images: int
    total_groups: int
    labeled_groups: int
    ranker_loaded: bool
    reference_conditioning_enabled: bool


class CullingEngineAdapter:
    """Adapter-oriented facade for host apps that consume the engine artifacts."""

    def __init__(
        self,
        *,
        artifact_bundle: ArtifactBundlePaths,
        ranking_artifacts: RankingArtifacts,
        cluster_labels_by_id: Dict[str, ClusterLabelRecord] | None = None,
        ranker_service: RankerService | None = None,
    ) -> None:
        self.artifact_bundle = artifact_bundle
        self.ranking_artifacts = ranking_artifacts
        self.cluster_labels_by_id = cluster_labels_by_id or {}
        self.ranker_service = ranker_service
        self._image_records = self._build_image_records()
        self._image_by_id = {record.identity.image_id: record for record in self._image_records}
        self._human_decisions_by_image = self._build_human_decisions()
        self._path_to_image_id = {
            record.identity.file_path.casefold(): record.identity.image_id
            for record in self._image_records
        }

    @classmethod
    def from_artifacts(
        cls,
        artifacts_dir: Path,
        *,
        labels_dir: Path | None = None,
        checkpoint_path: Path | None = None,
        reference_bank_path: Path | None = None,
        ranking_output_dir: Path | None = None,
        evaluation_output_dir: Path | None = None,
        device: str = "auto",
        metadata_filename: str = "images.csv",
        embeddings_filename: str = "embeddings.npy",
        image_ids_filename: str = "image_ids.json",
        clusters_filename: str = "clusters.csv",
        cluster_labels_filename: str = "cluster_labels.jsonl",
    ) -> "CullingEngineAdapter":
        """Create a host adapter from the saved engine artifact directories."""

        bundle = discover_artifact_bundle(
            artifacts_dir,
            labels_dir=labels_dir,
            ranking_output_dir=ranking_output_dir,
            evaluation_output_dir=evaluation_output_dir,
            reference_bank_path=reference_bank_path,
            metadata_filename=metadata_filename,
            embeddings_filename=embeddings_filename,
            image_ids_filename=image_ids_filename,
            clusters_filename=clusters_filename,
        )
        resolved_labels_dir = (
            labels_dir.expanduser().resolve()
            if labels_dir is not None
            else (
                bundle.cluster_labels_path.parent
                if bundle.cluster_labels_path is not None
                else (
                    bundle.pairwise_labels_path.parent
                    if bundle.pairwise_labels_path is not None
                    else None
                )
            )
        )
        ranking_artifacts = load_ranking_artifacts(
            bundle.artifacts_dir,
            metadata_filename=metadata_filename,
            embeddings_filename=embeddings_filename,
            image_ids_filename=image_ids_filename,
            clusters_filename=clusters_filename,
        )
        cluster_labels_by_id = (
            load_latest_cluster_labels(
                labels_dir=resolved_labels_dir,
                cluster_labels_filename=cluster_labels_filename,
            )
            if resolved_labels_dir is not None
            else {}
        )
        ranker_service = (
            load_ranker(
                checkpoint_path,
                device=device,
                reference_bank_path=reference_bank_path,
            )
            if checkpoint_path is not None
            else None
        )
        return cls(
            artifact_bundle=bundle,
            ranking_artifacts=ranking_artifacts,
            cluster_labels_by_id=cluster_labels_by_id,
            ranker_service=ranker_service,
        )

    def describe_bundle(self) -> ArtifactBundleContract:
        """Return the stable versioned artifact contract for this adapter."""

        return self.artifact_bundle.to_contract()

    def summarize(self) -> AdapterSummary:
        """Return a short summary for diagnostics or a future host UI."""

        return AdapterSummary(
            engine_api_version=ENGINE_API_VERSION,
            total_images=len(self._image_records),
            total_groups=len(self.ranking_artifacts.clusters_by_id),
            labeled_groups=len(self.cluster_labels_by_id),
            ranker_loaded=self.ranker_service is not None,
            reference_conditioning_enabled=(
                self.ranker_service.reference_conditioning_enabled
                if self.ranker_service is not None
                else False
            ),
        )

    def list_images(self) -> List[EngineImageRecord]:
        """Return every known image in embedding order."""

        return list(self._image_records)

    def get_image(self, image_id: str) -> EngineImageRecord:
        """Return one image by stable image ID."""

        return self._image_by_id[image_id]

    def list_groups(self, *, include_singletons: bool = True) -> List[CullingGroupRecord]:
        """Return the culling groups discovered for this artifact set."""

        groups: List[CullingGroupRecord] = []
        for cluster_id in sorted(self.ranking_artifacts.clusters_by_id.keys()):
            members = self.ranking_artifacts.clusters_by_id[cluster_id]
            if not include_singletons and len(members) <= 1:
                continue
            groups.append(
                CullingGroupRecord(
                    group_id=cluster_id,
                    group_type="culling_cluster",
                    size=len(members),
                    reason=members[0].cluster_reason if members else "",
                    member_image_ids=tuple(member.image_id for member in members),
                )
            )
        return groups

    def get_group(self, group_id: str) -> CullingGroupRecord:
        """Return one culling group by stable cluster/group ID."""

        members = self.ranking_artifacts.clusters_by_id[group_id]
        return CullingGroupRecord(
            group_id=group_id,
            group_type="culling_cluster",
            size=len(members),
            reason=members[0].cluster_reason if members else "",
            member_image_ids=tuple(member.image_id for member in members),
        )

    def get_group_members(self, group_id: str) -> List[EngineImageRecord]:
        """Return the image records that belong to one culling group."""

        return [
            self._image_by_id[member.image_id]
            for member in self.ranking_artifacts.clusters_by_id[group_id]
        ]

    def list_human_decisions(self) -> List[HumanDecisionRecord]:
        """Return the latest saved human decisions aligned by image ID."""

        return list(self._human_decisions_by_image.values())

    def get_human_decision(self, image_id: str) -> HumanDecisionRecord | None:
        """Return one saved human decision when available."""

        return self._human_decisions_by_image.get(image_id)

    def build_path_to_image_id_map(self) -> Dict[str, str]:
        """Return a lookup that maps absolute file paths back to stable image IDs."""

        return dict(self._path_to_image_id)

    def resolve_image_ids_for_paths(self, file_paths: Iterable[str | Path]) -> Dict[str, str]:
        """Map host-app file paths back to engine image IDs when present."""

        resolved: Dict[str, str] = {}
        for path in file_paths:
            absolute = str(Path(path).expanduser().resolve())
            image_id = self._path_to_image_id.get(absolute.casefold())
            if image_id is not None:
                resolved[absolute] = image_id
        return resolved

    def score_images(
        self,
        *,
        image_ids: Optional[List[str]] = None,
        batch_size: int = 2048,
    ) -> List[EngineScoreRecord]:
        """Score one or more images and return host-facing AI score records."""

        service = self._require_ranker()
        target_ids = image_ids or [record.identity.image_id for record in self._image_records]
        embeddings = self._select_embeddings(target_ids)
        details = service.score_embeddings_with_details(embeddings, batch_size=batch_size)

        model_architecture = service.checkpoint_metadata.get("model_config", {}).get("architecture")
        reference_descriptor = self._build_reference_descriptor()

        records: List[EngineScoreRecord] = []
        for row_index, image_id in enumerate(target_ids):
            image_record = self._image_by_id[image_id]
            records.append(
                EngineScoreRecord(
                    image=image_record,
                    ai_score=float(details.final_scores[row_index]),
                    group_id=image_record.cluster_id,
                    group_size=image_record.cluster_size,
                    base_score=float(details.base_scores[row_index]),
                    reference_adjustment=float(details.reference_adjustments[row_index]),
                    model_architecture=model_architecture,
                    checkpoint_path=None,
                    reference_bank=reference_descriptor,
                    human_decision=self._human_decisions_by_image.get(image_id),
                )
            )
        return records

    def rank_group(self, group_id: str, *, batch_size: int = 2048) -> List[EngineRankedResult]:
        """Rank one culling group and return host-facing ranked results."""

        service = self._require_ranker()
        members = service.rank_cluster(
            self.ranking_artifacts,
            group_id,
            batch_size=batch_size,
        )
        label_record = self.cluster_labels_by_id.get(group_id)
        top_matches_best = (
            members[0].image_id in set(label_record.best_image_ids)
            if members and label_record is not None and label_record.best_image_ids
            else None
        )
        top_is_non_reject = (
            members[0].image_id not in set(label_record.reject_image_ids)
            if members and label_record is not None
            else None
        )

        ranked: List[EngineRankedResult] = []
        for member in members:
            ranked.append(
                EngineRankedResult(
                    image=self._image_by_id[member.image_id],
                    group_id=member.cluster_id,
                    group_size=member.cluster_size,
                    rank_in_group=member.rank_in_cluster,
                    ai_score=member.score,
                    base_score=member.base_score,
                    reference_adjustment=member.reference_adjustment,
                    human_decision=self._human_decisions_by_image.get(member.image_id),
                    model_top1_matches_human_best=top_matches_best,
                    model_top1_is_human_non_reject=top_is_non_reject,
                )
            )
        return ranked

    def build_ranked_results(self, *, batch_size: int = 2048) -> List[EngineRankedResult]:
        """Rank every cluster and return a stable host-facing ranked result list."""

        service = self._require_ranker()
        ranked_clusters = service.rank_clusters(
            self.ranking_artifacts,
            batch_size=batch_size,
        )

        results: List[EngineRankedResult] = []
        for group in self.list_groups():
            label_record = self.cluster_labels_by_id.get(group.group_id)
            ranked_members = ranked_clusters[group.group_id]
            top_matches_best = (
                ranked_members[0].image_id in set(label_record.best_image_ids)
                if ranked_members and label_record is not None and label_record.best_image_ids
                else None
            )
            top_is_non_reject = (
                ranked_members[0].image_id not in set(label_record.reject_image_ids)
                if ranked_members and label_record is not None
                else None
            )
            for member in ranked_members:
                results.append(
                    EngineRankedResult(
                        image=self._image_by_id[member.image_id],
                        group_id=member.cluster_id,
                        group_size=member.cluster_size,
                        rank_in_group=member.rank_in_cluster,
                        ai_score=member.score,
                        base_score=member.base_score,
                        reference_adjustment=member.reference_adjustment,
                        human_decision=self._human_decisions_by_image.get(member.image_id),
                        model_top1_matches_human_best=top_matches_best,
                        model_top1_is_human_non_reject=top_is_non_reject,
                    )
                )
        return results

    def _build_image_records(self) -> List[EngineImageRecord]:
        """Build the host-facing image records from saved ranking artifacts."""

        source_input_dir = (
            str(self.artifact_bundle.source_input_dir)
            if self.artifact_bundle.source_input_dir is not None
            else None
        )
        records: List[EngineImageRecord] = []
        for image in self.ranking_artifacts.ordered_images:
            records.append(
                EngineImageRecord(
                    identity=EngineImageIdentity(
                        image_id=image.image_id,
                        file_path=image.file_path,
                        relative_path=image.relative_path,
                        file_name=image.file_name,
                        source_input_dir=source_input_dir,
                    ),
                    embedding_index=image.embedding_index,
                    capture_timestamp=image.capture_timestamp,
                    capture_time_source=image.capture_time_source,
                    cluster_id=image.cluster_id,
                    cluster_size=image.cluster_size,
                    cluster_position=image.cluster_position,
                    cluster_reason=image.cluster_reason,
                )
            )
        return records

    def _build_human_decisions(self) -> Dict[str, HumanDecisionRecord]:
        """Convert cluster-label artifacts into stable image-level human decisions."""

        decisions: Dict[str, HumanDecisionRecord] = {}
        for cluster_id, record in self.cluster_labels_by_id.items():
            for image_id in record.best_image_ids:
                decisions[image_id] = HumanDecisionRecord(
                    image_id=image_id,
                    cluster_id=cluster_id,
                    culling_label="best",
                    accepted=True,
                    rejected=False,
                    annotator_id=record.annotator_id,
                    timestamp=record.timestamp,
                )
            for image_id in record.acceptable_image_ids:
                decisions[image_id] = HumanDecisionRecord(
                    image_id=image_id,
                    cluster_id=cluster_id,
                    culling_label="acceptable",
                    accepted=True,
                    rejected=False,
                    annotator_id=record.annotator_id,
                    timestamp=record.timestamp,
                )
            for image_id in record.reject_image_ids:
                decisions[image_id] = HumanDecisionRecord(
                    image_id=image_id,
                    cluster_id=cluster_id,
                    culling_label="reject",
                    accepted=False,
                    rejected=True,
                    annotator_id=record.annotator_id,
                    timestamp=record.timestamp,
                )
        return decisions

    def _select_embeddings(self, image_ids: List[str]):
        """Select embeddings in the requested image-ID order."""

        return self.ranking_artifacts.embeddings[
            [self.ranking_artifacts.images_by_id[image_id].embedding_index for image_id in image_ids],
            :,
        ]

    def _build_reference_descriptor(self) -> ReferenceBankDescriptor | None:
        """Build the optional reference-bank descriptor from the loaded ranker."""

        if self.ranker_service is None or self.ranker_service.reference_bank is None:
            return None

        bank_path = self.artifact_bundle.to_contract().reference_bank_path
        if bank_path is None:
            return None

        return ReferenceBankDescriptor(
            reference_bank_path=str(bank_path),
            bucket_names=tuple(self.ranker_service.reference_bank.bucket_order),
            feature_names=tuple(self.ranker_service.reference_feature_names),
        )

    def _require_ranker(self) -> RankerService:
        """Return the loaded ranker service or raise a clear integration error."""

        if self.ranker_service is None:
            raise ValueError(
                "This adapter was created without a ranker checkpoint. Provide checkpoint_path "
                "when creating the adapter before requesting scores or ranked results."
            )
        return self.ranker_service


def create_host_adapter(
    artifacts_dir: Path,
    *,
    labels_dir: Path | None = None,
    checkpoint_path: Path | None = None,
    reference_bank_path: Path | None = None,
    ranking_output_dir: Path | None = None,
    evaluation_output_dir: Path | None = None,
    device: str = "auto",
) -> CullingEngineAdapter:
    """Create the stable host-facing adapter over an existing artifact bundle."""

    return CullingEngineAdapter.from_artifacts(
        artifacts_dir,
        labels_dir=labels_dir,
        checkpoint_path=checkpoint_path,
        reference_bank_path=reference_bank_path,
        ranking_output_dir=ranking_output_dir,
        evaluation_output_dir=evaluation_output_dir,
        device=device,
    )


__all__ = [
    "AdapterSummary",
    "ArtifactBundlePaths",
    "CullingEngineAdapter",
    "build_artifact_contract",
    "build_culling_groups",
    "create_host_adapter",
    "discover_artifact_bundle",
    "extract_embeddings",
]
