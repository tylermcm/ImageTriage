"""Stable integration contracts shared between engine artifacts and host apps."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional


ENGINE_API_VERSION = "week7.engine_api.v1"
ARTIFACT_CONTRACT_VERSION = "week7.artifact_contract.v1"


@dataclass(frozen=True)
class EngineImageIdentity:
    """Stable identity for one image across engine artifacts and host records."""

    image_id: str
    file_path: str
    relative_path: str
    file_name: str
    source_input_dir: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert the identity into a JSON-friendly dictionary."""

        return {
            "image_id": self.image_id,
            "file_path": self.file_path,
            "relative_path": self.relative_path,
            "file_name": self.file_name,
            "source_input_dir": self.source_input_dir,
        }


@dataclass(frozen=True)
class EngineImageRecord:
    """Host-facing metadata for one engine-tracked image."""

    identity: EngineImageIdentity
    embedding_index: Optional[int] = None
    capture_timestamp: str = ""
    capture_time_source: str = "missing"
    cluster_id: Optional[str] = None
    cluster_size: Optional[int] = None
    cluster_position: Optional[int] = None
    cluster_reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """Convert the image record into a flattened JSON-friendly dictionary."""

        payload = self.identity.to_dict()
        payload.update(
            {
                "embedding_index": self.embedding_index,
                "capture_timestamp": self.capture_timestamp,
                "capture_time_source": self.capture_time_source,
                "cluster_id": self.cluster_id,
                "cluster_size": self.cluster_size,
                "cluster_position": self.cluster_position,
                "cluster_reason": self.cluster_reason,
            }
        )
        return payload


@dataclass(frozen=True)
class HumanDecisionRecord:
    """Optional human review state aligned to one image and culling group."""

    image_id: str
    cluster_id: Optional[str] = None
    culling_label: Optional[str] = None
    rating: Optional[int] = None
    accepted: Optional[bool] = None
    rejected: Optional[bool] = None
    override_label: Optional[str] = None
    override_reason: Optional[str] = None
    annotator_id: Optional[str] = None
    timestamp: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert the human decision into a JSON-friendly dictionary."""

        return {
            "image_id": self.image_id,
            "cluster_id": self.cluster_id,
            "culling_label": self.culling_label,
            "rating": self.rating,
            "accepted": self.accepted,
            "rejected": self.rejected,
            "override_label": self.override_label,
            "override_reason": self.override_reason,
            "annotator_id": self.annotator_id,
            "timestamp": self.timestamp,
        }


@dataclass(frozen=True)
class CullingGroupRecord:
    """Stable host-facing contract for one culling group/cluster."""

    group_id: str
    group_type: str
    size: int
    reason: str
    member_image_ids: tuple[str, ...]

    def to_dict(self) -> Dict[str, Any]:
        """Convert the group contract into a JSON-friendly dictionary."""

        return {
            "group_id": self.group_id,
            "group_type": self.group_type,
            "size": self.size,
            "reason": self.reason,
            "member_image_ids": list(self.member_image_ids),
        }


@dataclass(frozen=True)
class ReferenceBankDescriptor:
    """Optional reference-bank association used for personalized scoring."""

    reference_bank_path: str
    bucket_names: tuple[str, ...]
    feature_names: tuple[str, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        """Convert the reference descriptor into a JSON-friendly dictionary."""

        return {
            "reference_bank_path": self.reference_bank_path,
            "bucket_names": list(self.bucket_names),
            "feature_names": list(self.feature_names),
        }


@dataclass(frozen=True)
class EngineScoreRecord:
    """One AI score resolved back onto an image identity."""

    image: EngineImageRecord
    ai_score: float
    group_id: Optional[str] = None
    group_size: Optional[int] = None
    rank_in_group: Optional[int] = None
    base_score: Optional[float] = None
    reference_adjustment: Optional[float] = None
    model_architecture: Optional[str] = None
    checkpoint_path: Optional[str] = None
    reference_bank: Optional[ReferenceBankDescriptor] = None
    human_decision: Optional[HumanDecisionRecord] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert the score record into a flattened JSON-friendly dictionary."""

        payload = self.image.to_dict()
        payload.update(
            {
                "ai_score": self.ai_score,
                "group_id": self.group_id,
                "group_size": self.group_size,
                "rank_in_group": self.rank_in_group,
                "base_score": self.base_score,
                "reference_adjustment": self.reference_adjustment,
                "model_architecture": self.model_architecture,
                "checkpoint_path": self.checkpoint_path,
                "reference_bank": (
                    self.reference_bank.to_dict() if self.reference_bank is not None else None
                ),
                "human_decision": (
                    self.human_decision.to_dict() if self.human_decision is not None else None
                ),
            }
        )
        return payload


@dataclass(frozen=True)
class EngineRankedResult:
    """One ranked image result inside a culling group."""

    image: EngineImageRecord
    group_id: str
    group_size: int
    rank_in_group: int
    ai_score: float
    base_score: Optional[float] = None
    reference_adjustment: Optional[float] = None
    human_decision: Optional[HumanDecisionRecord] = None
    model_top1_matches_human_best: Optional[bool] = None
    model_top1_is_human_non_reject: Optional[bool] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert the ranked result into a flattened JSON-friendly dictionary."""

        payload = self.image.to_dict()
        payload.update(
            {
                "group_id": self.group_id,
                "group_size": self.group_size,
                "rank_in_group": self.rank_in_group,
                "ai_score": self.ai_score,
                "base_score": self.base_score,
                "reference_adjustment": self.reference_adjustment,
                "human_decision": (
                    self.human_decision.to_dict() if self.human_decision is not None else None
                ),
                "model_top1_matches_human_best": self.model_top1_matches_human_best,
                "model_top1_is_human_non_reject": self.model_top1_is_human_non_reject,
            }
        )
        return payload


@dataclass(frozen=True)
class ArtifactBundleContract:
    """Versioned manifest describing one engine artifact bundle."""

    contract_version: str
    artifacts_dir: str
    source_input_dir: Optional[str]
    metadata_path: Optional[str]
    embeddings_path: Optional[str]
    image_ids_path: Optional[str]
    clusters_path: Optional[str]
    pairwise_labels_path: Optional[str] = None
    cluster_labels_path: Optional[str] = None
    scored_clusters_path: Optional[str] = None
    ranked_export_path: Optional[str] = None
    evaluation_metrics_path: Optional[str] = None
    reference_bank_path: Optional[str] = None
    resolved_config_paths: tuple[str, ...] = field(default_factory=tuple)
    available_artifacts: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert the artifact contract into a JSON-friendly dictionary."""

        return {
            "contract_version": self.contract_version,
            "artifacts_dir": self.artifacts_dir,
            "source_input_dir": self.source_input_dir,
            "metadata_path": self.metadata_path,
            "embeddings_path": self.embeddings_path,
            "image_ids_path": self.image_ids_path,
            "clusters_path": self.clusters_path,
            "pairwise_labels_path": self.pairwise_labels_path,
            "cluster_labels_path": self.cluster_labels_path,
            "scored_clusters_path": self.scored_clusters_path,
            "ranked_export_path": self.ranked_export_path,
            "evaluation_metrics_path": self.evaluation_metrics_path,
            "reference_bank_path": self.reference_bank_path,
            "resolved_config_paths": list(self.resolved_config_paths),
            "available_artifacts": dict(self.available_artifacts),
        }
