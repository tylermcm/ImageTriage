import tempfile
import unittest
from pathlib import Path

import numpy as np

from aiculler.semantic import CategoryClusterer, PrimaryCategoryAssigner, load_category_prompts
from aiculler.storage import SQLiteFeatureStore


class FakeTextEncoder:
    def encode(self, prompt: str) -> np.ndarray:
        if "portrait" in prompt:
            return np.array([0.0, 1.0], dtype=np.float32)
        return np.array([1.0, 0.0], dtype=np.float32)


class SemanticWorkflowTests(unittest.TestCase):
    def test_assigns_one_primary_category_per_image(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            store = SQLiteFeatureStore(tmp_path / "test.sqlite")
            try:
                landscape = self._add_image(store, tmp_path / "landscape.jpg", [1.0, 0.0])
                portrait = self._add_image(store, tmp_path / "portrait.jpg", [0.0, 1.0])
                assigner = PrimaryCategoryAssigner(
                    store,
                    FakeTextEncoder(),
                    category_prompts={
                        "landscape": ["landscape"],
                        "portrait": ["portrait"],
                    },
                )

                assignments = assigner.assign()
                by_id = {record.image_id: record for record in assignments}

                self.assertEqual(by_id[landscape].primary_category, "landscape")
                self.assertEqual(by_id[portrait].primary_category, "portrait")
                stored = {int(row["image_id"]): row["primary_category"] for row in store.list_categories()}
                self.assertEqual(stored[landscape], "landscape")
                self.assertEqual(stored[portrait], "portrait")
            finally:
                store.close()

    def test_low_confidence_assignment_becomes_uncategorized(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            store = SQLiteFeatureStore(tmp_path / "test.sqlite")
            try:
                image_id = self._add_image(store, tmp_path / "ambiguous.jpg", [1.0, 1.0])
                assigner = PrimaryCategoryAssigner(
                    store,
                    FakeTextEncoder(),
                    category_prompts={
                        "landscape": ["landscape"],
                        "portrait": ["portrait"],
                    },
                    min_confidence=0.99,
                )

                assignments = assigner.assign()

                self.assertEqual(assignments[0].image_id, image_id)
                self.assertEqual(assignments[0].primary_category, "uncategorized")
            finally:
                store.close()

    def test_clusters_within_primary_category(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            store = SQLiteFeatureStore(tmp_path / "test.sqlite")
            try:
                image_ids = []
                for idx, embedding in enumerate(
                    [
                        [1.0, 0.0],
                        [0.98, 0.05],
                        [0.96, 0.08],
                        [0.94, 0.10],
                        [0.0, 1.0],
                        [0.05, 0.98],
                        [0.08, 0.96],
                        [0.10, 0.94],
                    ]
                ):
                    image_ids.append(self._add_image(store, tmp_path / f"{idx}.jpg", embedding))
                store.update_image_categories(
                    {
                        image_id: {
                            "primary_category": "landscape",
                            "confidence": 0.9,
                            "category_scores": {"landscape": 0.9},
                            "assigned_by": "test",
                        }
                        for image_id in image_ids
                    }
                )

                clusterer = CategoryClusterer(store, run_id="test_clusters", min_cluster_size=2)
                clusters, memberships = clusterer.cluster()

                self.assertEqual(len(clusters), 2)
                self.assertEqual(sum(record.image_count for record in clusters), 8)
                self.assertEqual(len(memberships), 8)
                self.assertEqual(len(store.list_semantic_clusters("test_clusters")), 2)
            finally:
                store.close()

    def test_load_category_prompts_skips_disabled_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "categories.csv"
            path.write_text(
                "category,prompt,enabled\n"
                "landscape,wide mountain scene,1\n"
                "portrait,person face,0\n",
                encoding="utf-8",
            )

            prompts = load_category_prompts(path)

            self.assertEqual(prompts, {"landscape": ["wide mountain scene"]})

    @staticmethod
    def _add_image(store: SQLiteFeatureStore, path: Path, embedding: list[float]) -> int:
        image_id = store.upsert_image(path, status="ready")
        store.save_features(image_id, np.asarray(embedding, dtype=np.float32), technical_score=0.5)
        return image_id


if __name__ == "__main__":
    unittest.main()
