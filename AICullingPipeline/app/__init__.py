"""Reusable AI engine modules for the culling pipeline and future app integration."""

from __future__ import annotations

from importlib import import_module
from typing import Any

from app.contracts import (
    ARTIFACT_CONTRACT_VERSION,
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
from app.config import (
    ClusteringConfig,
    ExtractionConfig,
    LabelingConfig,
    ReferenceBankBuildConfig,
    RankingEvaluationConfig,
    RankingReportConfig,
    RankingScoreConfig,
    RankingTrainConfig,
)

__all__ = [
    "ARTIFACT_CONTRACT_VERSION",
    "ArtifactBundleContract",
    "ClusteringConfig",
    "CullingEngineAdapter",
    "CullingGroupRecord",
    "ENGINE_API_VERSION",
    "EngineImageIdentity",
    "EngineImageRecord",
    "EngineRankedResult",
    "EngineScoreRecord",
    "ExtractionConfig",
    "HumanDecisionRecord",
    "LabelingConfig",
    "ReferenceBankBuildConfig",
    "ReferenceBankDescriptor",
    "RankingEvaluationConfig",
    "RankingReportConfig",
    "RankingScoreConfig",
    "RankingTrainConfig",
    "build_artifact_contract",
    "build_cluster_report",
    "build_culling_groups",
    "build_reference_bank",
    "build_reference_feature_matrix",
    "create_host_adapter",
    "create_labeling_session",
    "create_ranking_service",
    "discover_artifact_bundle",
    "evaluate_ranker",
    "extract_embeddings",
    "export_ranked_results",
    "load_labeling_dataset",
    "load_ranker",
    "rank_cluster",
    "run_embedding_extraction",
    "run_similarity_clustering",
    "score_images",
    "score_cluster_artifacts",
    "train_ranker",
]


def __getattr__(name: str) -> Any:
    """Lazily proxy public exports to the engine package."""

    if name in {
        "build_artifact_contract",
        "create_labeling_session",
        "CullingEngineAdapter",
        "create_host_adapter",
        "create_ranking_service",
        "build_cluster_report",
        "build_culling_groups",
        "build_reference_bank",
        "build_reference_feature_matrix",
        "discover_artifact_bundle",
        "evaluate_ranker",
        "extract_embeddings",
        "export_ranked_results",
        "load_labeling_dataset",
        "load_ranker",
        "rank_cluster",
        "run_embedding_extraction",
        "run_similarity_clustering",
        "score_images",
        "score_cluster_artifacts",
        "train_ranker",
    }:
        engine_module = import_module("app.engine")
        value = getattr(engine_module, name)
        globals()[name] = value
        return value

    raise AttributeError(f"module 'app' has no attribute {name!r}")
