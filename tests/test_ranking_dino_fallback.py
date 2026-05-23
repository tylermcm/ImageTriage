from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np


AICULLING_ROOT = Path(__file__).resolve().parents[1] / "AICullingPipeline"
if str(AICULLING_ROOT) not in sys.path:
    sys.path.insert(0, str(AICULLING_ROOT))

from app.engine.ranking.inference import RankerEmbeddingDimensionError
from app.engine.ranking.models import LinearRanker
from app.engine.ranking.service import RankerService, rank_clusters_by_embedding_centrality
from app.storage.ranking_artifacts import RankedImageArtifact, RankingArtifacts


class RankingDinoFallbackTests(unittest.TestCase):
    def test_ranker_service_rejects_incompatible_embedding_width(self) -> None:
        service = RankerService(
            model=LinearRanker(768),
            device="cpu",
            normalize_embeddings=True,
            checkpoint_metadata={
                "model_config": {
                    "architecture": "linear",
                    "input_dim": 768,
                    "hidden_dim": 0,
                    "dropout": 0.0,
                }
            },
        )

        with self.assertRaises(RankerEmbeddingDimensionError) as context:
            service.score_embeddings(np.zeros((2, 1024), dtype=np.float32))

        self.assertEqual(context.exception.expected_dim, 768)
        self.assertEqual(context.exception.actual_dim, 1024)

    def test_centrality_fallback_ranks_without_fixed_embedding_width(self) -> None:
        artifacts = _ranking_artifacts(
            embeddings=np.asarray(
                [
                    [1.0, 0.0, 0.0, 0.0],
                    [0.9, 0.1, 0.0, 0.0],
                    [-1.0, 0.0, 0.0, 0.0],
                ],
                dtype=np.float32,
            )
        )

        ranked = rank_clusters_by_embedding_centrality(artifacts)

        self.assertEqual([member.image_id for member in ranked["cluster_000"]], ["image_1", "image_0", "image_2"])
        self.assertGreater(ranked["cluster_000"][0].score, ranked["cluster_000"][-1].score)


def _ranking_artifacts(*, embeddings: np.ndarray) -> RankingArtifacts:
    ordered_images = [
        RankedImageArtifact(
            image_id=f"image_{index}",
            embedding_index=index,
            file_path=f"C:/photos/image_{index}.jpg",
            relative_path=f"image_{index}.jpg",
            file_name=f"image_{index}.jpg",
            cluster_id="cluster_000",
            cluster_size=int(embeddings.shape[0]),
            cluster_position=index,
            cluster_reason="test",
            capture_timestamp="",
            capture_time_source="missing",
        )
        for index in range(int(embeddings.shape[0]))
    ]
    return RankingArtifacts(
        embeddings=embeddings,
        ordered_images=ordered_images,
        images_by_id={image.image_id: image for image in ordered_images},
        clusters_by_id={"cluster_000": ordered_images},
    )


if __name__ == "__main__":
    unittest.main()
