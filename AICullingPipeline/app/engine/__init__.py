"""Public reusable engine API for embedding, clustering, labeling, and ranking."""

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
from app.labeling.loaders import load_labeling_dataset
from app.labeling.models import ClusterItem, DatasetBundle, ImageItem, PairCandidate
from app.labeling.session import LabelingSession

__all__ = [
    "ARTIFACT_CONTRACT_VERSION",
    "ArtifactBundleContract",
    "ClusterItem",
    "ClusteringConfig",
    "CullingEngineAdapter",
    "CullingGroupRecord",
    "DatasetBundle",
    "ENGINE_API_VERSION",
    "EngineImageIdentity",
    "EngineImageRecord",
    "EngineRankedResult",
    "EngineScoreRecord",
    "ExtractionConfig",
    "ImageItem",
    "HumanDecisionRecord",
    "LabelingConfig",
    "LabelingSession",
    "PairCandidate",
    "PairwiseRankerTrainer",
    "RankerService",
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


_LAZY_EXPORTS = {
    "build_artifact_contract": ("app.engine.integration", "build_artifact_contract"),
    "build_culling_groups": ("app.engine.integration", "build_culling_groups"),
    "create_host_adapter": ("app.engine.integration", "create_host_adapter"),
    "discover_artifact_bundle": ("app.engine.integration", "discover_artifact_bundle"),
    "extract_embeddings": ("app.engine.integration", "extract_embeddings"),
    "run_embedding_extraction": ("app.engine.extraction", "run_embedding_extraction"),
    "run_similarity_clustering": ("app.engine.clustering", "run_similarity_clustering"),
    "create_labeling_session": ("app.engine.labeling", "create_labeling_session"),
    "CullingEngineAdapter": ("app.engine.integration", "CullingEngineAdapter"),
    "PairwiseRankerTrainer": ("app.engine.ranking", "PairwiseRankerTrainer"),
    "RankerService": ("app.engine.ranking", "RankerService"),
    "build_cluster_report": ("app.engine.ranking", "build_cluster_report"),
    "build_reference_bank": ("app.engine.ranking", "build_reference_bank"),
    "build_reference_feature_matrix": ("app.engine.ranking", "build_reference_feature_matrix"),
    "create_ranking_service": ("app.engine.ranking", "create_ranking_service"),
    "evaluate_ranker": ("app.engine.ranking", "evaluate_ranker"),
    "export_ranked_results": ("app.engine.ranking", "export_ranked_results"),
    "load_ranker": ("app.engine.ranking", "load_ranker"),
    "rank_cluster": ("app.engine.ranking", "rank_cluster"),
    "score_images": ("app.engine.ranking", "score_images"),
    "score_cluster_artifacts": ("app.engine.ranking", "score_cluster_artifacts"),
    "train_ranker": ("app.engine.ranking", "train_ranker"),
}


def __getattr__(name: str) -> Any:
    """Lazily resolve engine exports so heavy optional deps load only when needed."""

    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module 'app.engine' has no attribute {name!r}")

    module_name, attribute_name = target
    module = import_module(module_name)
    value = getattr(module, attribute_name)
    globals()[name] = value
    return value
