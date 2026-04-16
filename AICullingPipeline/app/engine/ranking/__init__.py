"""Reusable ranking engine modules."""

from app.engine.ranking.evaluation import evaluate_ranker
from app.engine.ranking.exports import export_ranked_results
from app.engine.ranking.reference_bank import build_reference_bank, build_reference_feature_matrix
from app.engine.ranking.reporting import build_cluster_report
from app.engine.ranking.service import (
    RankerService,
    create_ranking_service,
    load_ranker,
    rank_cluster,
    score_images,
    score_cluster_artifacts,
)
from app.engine.ranking.trainer import PairwiseRankerTrainer, train_ranker

__all__ = [
    "PairwiseRankerTrainer",
    "RankerService",
    "build_cluster_report",
    "build_reference_bank",
    "build_reference_feature_matrix",
    "create_ranking_service",
    "evaluate_ranker",
    "export_ranked_results",
    "load_ranker",
    "rank_cluster",
    "score_images",
    "score_cluster_artifacts",
    "train_ranker",
]
