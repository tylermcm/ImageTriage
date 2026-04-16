"""Reusable engine entry points for embedding extraction."""

from __future__ import annotations

from pathlib import Path
from typing import Dict

from app.config import ExtractionConfig
from app.pipelines.embedding_pipeline import EmbeddingExtractionPipeline


def run_embedding_extraction(config: ExtractionConfig) -> Dict[str, Path]:
    """Run the Week 1 embedding pipeline through the reusable engine surface."""

    return EmbeddingExtractionPipeline(config).run()
