"""Reusable ranker inference service and scored-cluster output helpers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

from app.config import RankingScoreConfig
from app.engine.ranking.exports import build_ranked_export_rows
from app.engine.ranking.inference import (
    ScoreBreakdown,
    l2_normalize_embeddings,
    load_ranker_checkpoint,
    score_embedding_batch_with_details,
)
from app.engine.ranking.reference_bank import build_reference_feature_matrix
from app.storage.reference_bank import ReferenceBankArtifacts, load_reference_bank
from app.storage.ranking_artifacts import (
    RankingArtifacts,
    RankedImageArtifact,
    load_ranking_artifacts,
    save_ranked_clusters_csv,
    save_ranking_summary_json,
)


@dataclass(frozen=True)
class RankedClusterMember:
    """One scored image inside a ranked cluster output."""

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
    base_score: float | None = None
    reference_adjustment: float | None = None


class RankerService:
    """Inference service for loading a trained ranker and scoring embeddings."""

    def __init__(
        self,
        *,
        model: Any,
        device: Any,
        normalize_embeddings: bool,
        checkpoint_metadata: Dict[str, Any],
        reference_bank: ReferenceBankArtifacts | None = None,
        reference_top_k: int = 3,
        reference_feature_names: tuple[str, ...] = (),
    ) -> None:
        self.model = model
        self.device = device
        self.normalize_embeddings = normalize_embeddings
        self.checkpoint_metadata = checkpoint_metadata
        self.reference_bank = reference_bank
        self.reference_top_k = reference_top_k
        self.reference_feature_names = reference_feature_names
        self.reference_conditioning_enabled = bool(
            checkpoint_metadata.get("reference_conditioning", {}).get("enabled", False)
        ) or int(checkpoint_metadata.get("model_config", {}).get("reference_feature_dim", 0)) > 0

    @classmethod
    def from_checkpoint(
        cls,
        checkpoint_path: Path,
        *,
        device: str = "auto",
        reference_bank_path: Path | None = None,
    ) -> "RankerService":
        """Load a reusable ranker service from a saved checkpoint."""

        model, checkpoint, resolved_device = load_ranker_checkpoint(
            checkpoint_path,
            device=device,
        )
        normalize_embeddings = bool(checkpoint.get("normalize_embeddings", True))
        reference_conditioning = checkpoint.get("reference_conditioning", {})
        model_config = checkpoint.get("model_config", {})
        reference_feature_dim = int(
            model_config.get(
                "reference_feature_dim",
                reference_conditioning.get("reference_feature_dim", 0),
            )
        )
        reference_top_k = int(reference_conditioning.get("reference_top_k", 3))
        reference_feature_names = tuple(
            str(value) for value in reference_conditioning.get("reference_feature_names", [])
        )

        loaded_reference_bank: ReferenceBankArtifacts | None = None
        if reference_feature_dim > 0:
            resolved_reference_bank_path = _resolve_reference_bank_path(
                checkpoint,
                explicit_reference_bank_path=reference_bank_path,
            )
            loaded_reference_bank = load_reference_bank(resolved_reference_bank_path)

        return cls(
            model=model,
            device=resolved_device,
            normalize_embeddings=normalize_embeddings,
            checkpoint_metadata=checkpoint,
            reference_bank=loaded_reference_bank,
            reference_top_k=reference_top_k,
            reference_feature_names=reference_feature_names,
        )

    def score_embedding(self, embedding: np.ndarray) -> float:
        """Score one embedding and return a scalar preference value."""

        array = np.asarray(embedding, dtype=np.float32)
        if array.ndim != 1:
            raise ValueError(f"Expected one embedding shaped [D], received {tuple(array.shape)}.")
        return float(self.score_embeddings(array[np.newaxis, :])[0])

    def score_embedding_details(self, embedding: np.ndarray) -> ScoreBreakdown:
        """Return base score, reference adjustment, and final score for one embedding."""

        array = np.asarray(embedding, dtype=np.float32)
        if array.ndim != 1:
            raise ValueError(f"Expected one embedding shaped [D], received {tuple(array.shape)}.")
        return self.score_embeddings_with_details(array[np.newaxis, :])

    def score_embeddings(self, embeddings: np.ndarray, *, batch_size: int = 2048) -> np.ndarray:
        """Score many embeddings and return one scalar per row."""

        return self.score_embeddings_with_details(
            embeddings,
            batch_size=batch_size,
        ).final_scores

    def score_embeddings_with_details(
        self,
        embeddings: np.ndarray,
        *,
        batch_size: int = 2048,
    ) -> ScoreBreakdown:
        """Score many embeddings and return score-component details."""

        raw_embeddings = np.asarray(embeddings, dtype=np.float32)
        prepared_embeddings = raw_embeddings
        if self.normalize_embeddings and prepared_embeddings.size:
            prepared_embeddings = l2_normalize_embeddings(prepared_embeddings)

        reference_features, reference_feature_names = self._prepare_reference_features(raw_embeddings)
        return score_embedding_batch_with_details(
            self.model,
            prepared_embeddings,
            reference_features=reference_features,
            reference_feature_names=reference_feature_names,
            device=self.device,
            batch_size=batch_size,
        )

    def score_images(
        self,
        ranking_artifacts: RankingArtifacts,
        *,
        image_ids: Optional[List[str]] = None,
    ) -> Dict[str, float]:
        """Score one or more images by stable image ID."""

        if image_ids is None:
            scores = self.score_embeddings(ranking_artifacts.embeddings)
            return {
                image.image_id: float(scores[image.embedding_index])
                for image in ranking_artifacts.ordered_images
            }

        target_images = [ranking_artifacts.images_by_id[image_id] for image_id in image_ids]
        embeddings = np.stack(
            [ranking_artifacts.embeddings[image.embedding_index] for image in target_images],
            axis=0,
        )
        scores = self.score_embeddings(embeddings)
        return {
            image.image_id: float(score)
            for image, score in zip(target_images, scores)
        }

    def rank_cluster(
        self,
        ranking_artifacts: RankingArtifacts,
        cluster_id: str,
        *,
        batch_size: int = 2048,
    ) -> List[RankedClusterMember]:
        """Rank all images in one cluster from best to worst."""

        members = ranking_artifacts.clusters_by_id.get(cluster_id)
        if members is None:
            raise KeyError(f"Unknown cluster_id: {cluster_id}")
        return self._rank_members(ranking_artifacts, members, batch_size=batch_size)

    def rank_clusters(
        self,
        ranking_artifacts: RankingArtifacts,
        *,
        batch_size: int = 2048,
    ) -> Dict[str, List[RankedClusterMember]]:
        """Rank every cluster in the artifact set."""

        ranked: Dict[str, List[RankedClusterMember]] = {}
        for cluster_id in sorted(ranking_artifacts.clusters_by_id.keys()):
            ranked[cluster_id] = self._rank_members(
                ranking_artifacts,
                ranking_artifacts.clusters_by_id[cluster_id],
                batch_size=batch_size,
            )
        return ranked

    def _rank_members(
        self,
        ranking_artifacts: RankingArtifacts,
        members: List[RankedImageArtifact],
        *,
        batch_size: int = 2048,
    ) -> List[RankedClusterMember]:
        """Score and order one cluster's members."""

        embeddings = np.stack(
            [ranking_artifacts.embeddings[member.embedding_index] for member in members],
            axis=0,
        )
        details = self.score_embeddings_with_details(embeddings, batch_size=batch_size)
        ordered = sorted(
            zip(members, details.final_scores, details.base_scores, details.reference_adjustments),
            key=lambda item: (
                -float(item[1]),
                item[0].cluster_position,
                item[0].file_name.casefold(),
                item[0].image_id,
            ),
        )

        ranked_members: List[RankedClusterMember] = []
        for rank_index, (member, final_score, base_score, reference_adjustment) in enumerate(
            ordered,
            start=1,
        ):
            ranked_members.append(
                RankedClusterMember(
                    cluster_id=member.cluster_id,
                    cluster_size=member.cluster_size,
                    rank_in_cluster=rank_index,
                    image_id=member.image_id,
                    score=float(final_score),
                    file_path=member.file_path,
                    relative_path=member.relative_path,
                    file_name=member.file_name,
                    capture_timestamp=member.capture_timestamp,
                    capture_time_source=member.capture_time_source,
                    base_score=float(base_score),
                    reference_adjustment=float(reference_adjustment),
                )
            )
        return ranked_members

    def _prepare_reference_features(
        self,
        embeddings: np.ndarray,
    ) -> tuple[np.ndarray | None, tuple[str, ...]]:
        """Compute reference-conditioned similarity features when enabled."""

        if not self.reference_conditioning_enabled:
            return None, self.reference_feature_names
        if self.reference_bank is None:
            raise ValueError(
                "This checkpoint expects reference-conditioned features, but no reference bank "
                "was provided when loading the ranker."
            )

        features, generated_feature_names = build_reference_feature_matrix(
            embeddings,
            self.reference_bank,
            top_k=self.reference_top_k,
        )
        if self.reference_feature_names:
            return features, self.reference_feature_names
        return features, generated_feature_names


def create_ranking_service(
    checkpoint_path: Path,
    *,
    device: str = "auto",
    reference_bank_path: Path | None = None,
) -> RankerService:
    """Create a reusable ranking service from a saved checkpoint."""

    return RankerService.from_checkpoint(
        checkpoint_path,
        device=device,
        reference_bank_path=reference_bank_path,
    )


def load_ranker(
    checkpoint_path: Path,
    *,
    device: str = "auto",
    reference_bank_path: Path | None = None,
) -> RankerService:
    """Load a trained ranker through the public engine surface."""

    return create_ranking_service(
        checkpoint_path,
        device=device,
        reference_bank_path=reference_bank_path,
    )


def score_images(
    service: RankerService,
    ranking_artifacts: RankingArtifacts,
    *,
    image_ids: Optional[List[str]] = None,
) -> Dict[str, float]:
    """Score one or more images through a reusable function-style API."""

    return service.score_images(ranking_artifacts, image_ids=image_ids)


def rank_cluster(
    service: RankerService,
    ranking_artifacts: RankingArtifacts,
    cluster_id: str,
    *,
    batch_size: int = 2048,
) -> List[RankedClusterMember]:
    """Rank one cluster through a reusable function-style API."""

    return service.rank_cluster(ranking_artifacts, cluster_id, batch_size=batch_size)


def score_cluster_artifacts(config: RankingScoreConfig) -> Dict[str, Path]:
    """Load a trained ranker, score saved artifacts, and write ranked cluster outputs."""

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
    ranked_clusters = service.rank_clusters(ranking_artifacts)
    export_rows = build_ranked_export_rows(ranked_clusters, ranking_artifacts)
    rows: List[Dict[str, Any]] = [
        {
            "cluster_id": row.cluster_id,
            "cluster_size": row.cluster_size,
            "rank_in_cluster": row.rank_in_cluster,
            "image_id": row.image_id,
            "score": row.score,
            "file_path": row.file_path,
            "relative_path": row.relative_path,
            "file_name": row.file_name,
            "capture_timestamp": row.capture_timestamp,
            "capture_time_source": row.capture_time_source,
        }
        for row in export_rows
    ]

    scored_clusters_path = config.output_dir / config.scored_clusters_filename
    summary_path = config.output_dir / config.summary_filename
    save_ranked_clusters_csv(scored_clusters_path, rows)

    cluster_sizes = [len(members) for members in ranked_clusters.values()]
    reference_conditioning = service.checkpoint_metadata.get("reference_conditioning", {})
    resolved_reference_bank_path = (
        str(config.reference_bank_path)
        if config.reference_bank_path is not None
        else reference_conditioning.get("reference_bank_path")
    )
    save_ranking_summary_json(
        summary_path,
        {
            "checkpoint_path": str(config.checkpoint_path),
            "total_images": len(ranking_artifacts.ordered_images),
            "total_clusters": len(ranked_clusters),
            "singleton_clusters": sum(size == 1 for size in cluster_sizes),
            "largest_cluster_size": max(cluster_sizes) if cluster_sizes else 0,
            "model_architecture": service.checkpoint_metadata["model_config"]["architecture"],
            "normalize_embeddings": service.normalize_embeddings,
            "reference_conditioning_enabled": service.reference_conditioning_enabled,
            "reference_bank_path": resolved_reference_bank_path,
            "reference_feature_names": reference_conditioning.get("reference_feature_names", []),
        },
    )

    return {
        "ranked_clusters": scored_clusters_path,
        "summary": summary_path,
    }


def _resolve_reference_bank_path(
    checkpoint: Dict[str, Any],
    *,
    explicit_reference_bank_path: Path | None,
) -> Path:
    """Resolve the reference bank path for a reference-conditioned checkpoint."""

    if explicit_reference_bank_path is not None:
        return Path(explicit_reference_bank_path).expanduser().resolve()

    reference_conditioning = checkpoint.get("reference_conditioning", {})
    configured_path = reference_conditioning.get("reference_bank_path")
    if not configured_path:
        configured_path = checkpoint.get("training_config", {}).get("reference_bank_path")
    if not configured_path:
        raise ValueError(
            "This checkpoint requires reference conditioning, but no reference_bank_path "
            "was provided and none was recorded in the checkpoint metadata."
        )
    return Path(str(configured_path)).expanduser().resolve()
