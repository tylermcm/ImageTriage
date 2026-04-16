"""Reusable engine entry points for culling-oriented clustering."""

from __future__ import annotations

from pathlib import Path
from typing import Dict

from app.config import ClusteringConfig
from app.pipelines.clustering_pipeline import SimilarityClusteringPipeline


def run_similarity_clustering(config: ClusteringConfig) -> Dict[str, Path]:
    """Run the Week 2 / 2.5 clustering pipeline through the engine surface."""

    return SimilarityClusteringPipeline(config).run()
