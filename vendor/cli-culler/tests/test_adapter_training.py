import csv
import tempfile
import unittest
from pathlib import Path

import numpy as np

from aiculler.adapter_training import (
    AdapterTrainer,
    adapter_scores_from_store,
    import_ratings_csv,
    parse_rating_label,
)
from aiculler.storage import SQLiteFeatureStore


class AdapterTrainingTests(unittest.TestCase):
    def test_parse_rating_label_supports_binary_and_buckets(self):
        self.assertEqual(parse_rating_label("keep"), ("binary", 0.75))
        self.assertEqual(parse_rating_label("reject"), ("binary", 0.0))
        self.assertEqual(parse_rating_label("hero"), ("bucket", 1.0))
        self.assertEqual(parse_rating_label("weak"), ("bucket", 0.25))
        with self.assertRaises(ValueError):
            parse_rating_label("unknown")

    def test_import_ratings_and_train_adapter(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            store = SQLiteFeatureStore(tmp_path / "test.sqlite")
            try:
                good = self._add_image(store, tmp_path / "good.jpg", [1.0, 0.0, 0.0])
                bad = self._add_image(store, tmp_path / "bad.jpg", [-1.0, 0.0, 0.0])
                neutral = self._add_image(store, tmp_path / "neutral.jpg", [0.0, 1.0, 0.0])
                self._assign_categories(store, [good, bad, neutral])
                ratings_path = tmp_path / "ratings.csv"
                self._write_ratings(ratings_path, [("good.jpg", "hero"), ("bad.jpg", "reject")])

                imported = import_ratings_csv(store, ratings_path)
                result = AdapterTrainer(
                    store,
                    projected_dim=2,
                    min_category_labels=2,
                    holdout_fraction=0.0,
                ).train(model_version="test_adapter")
                ranked = adapter_scores_from_store(store, "test_adapter", base_weight=0.0, adapter_weight=1.0)

                self.assertEqual(len(imported), 2)
                self.assertEqual(len(result.scores), 3)
                self.assertEqual(ranked[0].image_id, good)
                self.assertGreater(ranked[0].adapter_score, ranked[-1].adapter_score)
            finally:
                store.close()

    @staticmethod
    def _add_image(store: SQLiteFeatureStore, path: Path, embedding: list[float]) -> int:
        image_id = store.upsert_image(path, status="ready")
        store.save_features(image_id, np.asarray(embedding, dtype=np.float32), technical_score=0.5, final_score=0.5)
        return image_id

    @staticmethod
    def _assign_categories(store: SQLiteFeatureStore, image_ids: list[int]) -> None:
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

    @staticmethod
    def _write_ratings(path: Path, rows: list[tuple[str, str]]) -> None:
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=["filename", "label"])
            writer.writeheader()
            for filename, label in rows:
                writer.writerow({"filename": filename, "label": label})


if __name__ == "__main__":
    unittest.main()
