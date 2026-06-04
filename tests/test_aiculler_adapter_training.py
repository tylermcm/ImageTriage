from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

import numpy as np

from aiculler.adapter_training import fit_preference_model, import_ratings_csv, load_rating_records
from aiculler.storage import SQLiteFeatureStore


class AICullerAdapterTrainingTests(unittest.TestCase):
    def test_load_rating_records_reads_optional_sample_weight(self) -> None:
        with tempfile.TemporaryDirectory(prefix="aiculler_rating_weight_") as temp_dir:
            root = Path(temp_dir)
            store = SQLiteFeatureStore(root / "features.sqlite")
            try:
                image_id = store.upsert_image(root / "one.jpg", status="ready")
                store.save_features(image_id, np.asarray([1.0, 0.0], dtype=np.float32), technical_score=0.8)
                ratings_path = root / "ratings.csv"
                with ratings_path.open("w", encoding="utf-8", newline="") as handle:
                    writer = csv.DictWriter(handle, fieldnames=("source_path", "label", "weight", "review_round"))
                    writer.writeheader()
                    writer.writerow({
                        "source_path": str(root / "one.jpg"),
                        "label": "hero",
                        "weight": "4",
                        "review_round": "adapter_global_review",
                    })

                records = load_rating_records(store, ratings_path)
            finally:
                store.close()

            self.assertEqual(1, len(records))
            self.assertEqual(4.0, records[0].weight)
            self.assertEqual("global", records[0].label_origin)

    def test_import_ratings_replaces_same_image_source_and_origin(self) -> None:
        with tempfile.TemporaryDirectory(prefix="aiculler_rating_dedupe_") as temp_dir:
            root = Path(temp_dir)
            store = SQLiteFeatureStore(root / "features.sqlite")
            try:
                image_path = root / "one.jpg"
                stale_image_path = root / "stale.jpg"
                image_id = store.upsert_image(image_path, status="ready")
                stale_image_id = store.upsert_image(stale_image_path, status="ready")
                store.save_features(image_id, np.asarray([1.0, 0.0], dtype=np.float32), technical_score=0.8)
                store.save_features(stale_image_id, np.asarray([0.0, 1.0], dtype=np.float32), technical_score=0.7)
                ratings_path = root / "ratings.csv"
                with ratings_path.open("w", encoding="utf-8", newline="") as handle:
                    writer = csv.DictWriter(handle, fieldnames=("source_path", "label", "review_round"))
                    writer.writeheader()
                    writer.writerow({"source_path": str(image_path), "label": "hero", "review_round": "adapter_global_review"})
                    writer.writerow({"source_path": str(stale_image_path), "label": "hero", "review_round": "adapter_global_review"})
                import_ratings_csv(store, ratings_path, source="image_triage")

                with ratings_path.open("w", encoding="utf-8", newline="") as handle:
                    writer = csv.DictWriter(handle, fieldnames=("source_path", "label", "review_round"))
                    writer.writeheader()
                    writer.writerow({"source_path": str(image_path), "label": "reject", "review_round": "adapter_global_review"})
                import_ratings_csv(store, ratings_path, source="image_triage")
                rows = store.list_ratings()
            finally:
                store.close()

            self.assertEqual(1, len(rows))
            self.assertEqual("reject", rows[0]["label"])
            self.assertEqual("global", rows[0]["label_origin"])

    def test_load_rating_records_is_strict_by_default_for_missing_images(self) -> None:
        with tempfile.TemporaryDirectory(prefix="aiculler_rating_missing_strict_") as temp_dir:
            root = Path(temp_dir)
            store = SQLiteFeatureStore(root / "features.sqlite")
            try:
                ratings_path = root / "ratings.csv"
                with ratings_path.open("w", encoding="utf-8", newline="") as handle:
                    writer = csv.DictWriter(handle, fieldnames=("source_path", "label"))
                    writer.writeheader()
                    writer.writerow({"source_path": str(root / "missing.jpg"), "label": "hero"})

                with self.assertRaisesRegex(ValueError, "ratings row 2 did not match any image"):
                    load_rating_records(store, ratings_path)
            finally:
                store.close()

    def test_import_ratings_can_skip_missing_images_from_scoped_pool(self) -> None:
        with tempfile.TemporaryDirectory(prefix="aiculler_rating_missing_skip_") as temp_dir:
            root = Path(temp_dir)
            store = SQLiteFeatureStore(root / "features.sqlite")
            try:
                image_path = root / "one.jpg"
                image_id = store.upsert_image(image_path, status="ready")
                store.save_features(image_id, np.asarray([1.0, 0.0], dtype=np.float32), technical_score=0.8)
                ratings_path = root / "ratings.csv"
                with ratings_path.open("w", encoding="utf-8", newline="") as handle:
                    writer = csv.DictWriter(handle, fieldnames=("source_path", "label", "review_round"))
                    writer.writeheader()
                    writer.writerow({"source_path": str(root / "missing.jpg"), "label": "reject", "review_round": "current"})
                    writer.writerow({"source_path": str(image_path), "label": "hero", "review_round": "current"})

                records = import_ratings_csv(store, ratings_path, source="image_triage", skip_unmatched=True)
                rows = store.list_ratings()
            finally:
                store.close()

            self.assertEqual(1, len(records))
            self.assertEqual(1, len(rows))
            self.assertEqual("hero", rows[0]["label"])

    def test_rating_source_path_matches_jpeg_representative_by_unique_stem(self) -> None:
        with tempfile.TemporaryDirectory(prefix="aiculler_rating_stem_match_") as temp_dir:
            root = Path(temp_dir)
            store = SQLiteFeatureStore(root / "features.sqlite")
            try:
                jpeg_path = root / "_DSC1375.JPG"
                raw_path = root / "_DSC1375.NEF"
                image_id = store.upsert_image(jpeg_path, status="ready")
                store.save_features(image_id, np.asarray([1.0, 0.0], dtype=np.float32), technical_score=0.8)
                ratings_path = root / "ratings.csv"
                with ratings_path.open("w", encoding="utf-8", newline="") as handle:
                    writer = csv.DictWriter(handle, fieldnames=("source_path", "filename", "label", "review_round"))
                    writer.writeheader()
                    writer.writerow(
                        {
                            "source_path": str(raw_path),
                            "filename": raw_path.name,
                            "label": "hero",
                            "review_round": "current",
                        }
                    )

                records = load_rating_records(store, ratings_path)
            finally:
                store.close()

            self.assertEqual(1, len(records))
            self.assertEqual(jpeg_path.name, records[0].filename)
            self.assertEqual(str(jpeg_path), records[0].source_path)

    def test_preference_model_uses_sample_weight(self) -> None:
        features = np.asarray(
            [
                [1.0, 0.0],
                [0.0, 1.0],
                [-1.0, 0.0],
            ],
            dtype=np.float32,
        )
        examples = []
        for image_id, numeric_score, weight in (
            (1, 1.0, 8.0),
            (2, 0.0, 1.0),
            (3, 0.0, 1.0),
        ):
            examples.append(
                type(
                    "Example",
                    (),
                    {
                        "image_id": image_id,
                        "numeric_score": numeric_score,
                        "weight": weight,
                    },
                )()
            )
        model = fit_preference_model(features, examples, {1: 0, 2: 1, 3: 2})
        scores = model.score(features)

        self.assertGreater(scores[0], scores[1])
        self.assertGreater(scores[0], scores[2])


if __name__ == "__main__":
    unittest.main()
