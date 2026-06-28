from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from aiculler.adapter_training import (
    AdapterTrainer,
    adapter_validation_health,
    fit_preference_model,
    fit_regression_model,
    import_ratings_csv,
    load_rating_records,
    split_holdout,
)
from aiculler.storage import SQLiteFeatureStore


class AICullerAdapterTrainingTests(unittest.TestCase):
    @staticmethod
    def _split_example(image_id: int, *, folder_id: str, primary_category: str):
        return type(
            "Example",
            (),
            {
                "image_id": image_id,
                "folder_id": folder_id,
                "primary_category": primary_category,
            },
        )()

    @staticmethod
    def _rating_example(
        image_id: int,
        numeric_score: float,
        *,
        label: str = "",
        folder_id: str = "folder-a",
        primary_category: str = "landscape",
        cluster_id: int | None = 1,
        weight: float = 1.0,
    ):
        return type(
            "RatingExampleStub",
            (),
            {
                "image_id": image_id,
                "filename": f"{image_id}.jpg",
                "source_path": f"/tmp/{image_id}.jpg",
                "label": label or str(numeric_score),
                "label_type": "bucket",
                "numeric_score": numeric_score,
                "weight": weight,
                "label_origin": "test",
                "primary_category": primary_category,
                "cluster_id": cluster_id,
                "folder_id": folder_id,
                "reason_tags": (),
            },
        )()

    def test_load_rating_records_reads_optional_sample_weight_and_reason_tags(self) -> None:
        with tempfile.TemporaryDirectory(prefix="aiculler_rating_weight_") as temp_dir:
            root = Path(temp_dir)
            store = SQLiteFeatureStore(root / "features.sqlite")
            try:
                image_id = store.upsert_image(root / "one.jpg", status="ready")
                store.save_features(image_id, np.asarray([1.0, 0.0], dtype=np.float32), technical_score=0.8)
                ratings_path = root / "ratings.csv"
                with ratings_path.open("w", encoding="utf-8", newline="") as handle:
                    writer = csv.DictWriter(handle, fieldnames=("source_path", "label", "weight", "review_round", "reason_tags"))
                    writer.writeheader()
                    writer.writerow({
                        "source_path": str(root / "one.jpg"),
                        "label": "hero",
                        "weight": "4",
                        "review_round": "adapter_global_review",
                        "reason_tags": "composition;light_color",
                    })

                records = load_rating_records(store, ratings_path)
                import_ratings_csv(store, ratings_path)
                rows = store.list_ratings()
            finally:
                store.close()

            self.assertEqual(1, len(records))
            self.assertEqual(4.0, records[0].weight)
            self.assertEqual("global", records[0].label_origin)
            self.assertEqual(str(root), records[0].folder_id)
            self.assertEqual(("composition", "light_color"), records[0].reason_tags)
            metadata = json.loads(rows[0]["metadata_json"])
            self.assertEqual(["composition", "light_color"], metadata["reason_tags"])

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

    def test_regression_model_fits_weighted_label_order(self) -> None:
        features = np.asarray(
            [
                [0.0],
                [0.5],
                [1.0],
                [1.5],
            ],
            dtype=np.float32,
        )
        examples = [
            self._rating_example(1, 0.0, label="reject"),
            self._rating_example(2, 0.25, label="weak"),
            self._rating_example(3, 0.8, label="strong"),
            self._rating_example(4, 1.0, label="hero"),
        ]
        model = fit_regression_model(features, examples, {1: 0, 2: 1, 3: 2, 4: 3}, l2=0.01)
        scores = model.score(features)

        self.assertLess(scores[0], scores[1])
        self.assertLess(scores[1], scores[2])
        self.assertLess(scores[2], scores[3])

    def test_adapter_validation_health_flags_inverted_holdout(self) -> None:
        health = adapter_validation_health(
            {
                "holdout_count": 12,
                "holdout": {"rank_lift": -0.02},
                "culling": {
                    "rank_correlation": -0.1,
                    "top_30_recall": 0.31,
                    "keeper_recall": 0.24,
                },
            }
        )

        self.assertEqual("failed", health["status"])
        self.assertIn("Held-out rank lift is not positive.", health["reasons"])
        self.assertIn("Top-30 keeper recall is below 40%.", health["reasons"])

    def test_random_holdout_is_deterministic(self) -> None:
        examples = [
            self._split_example(index, folder_id=f"folder-{index % 2}", primary_category="cat-a")
            for index in range(10)
        ]

        first_train, first_holdout, first_info = split_holdout(
            examples,
            holdout_fraction=0.3,
            seed=42,
            validation_mode="random_holdout",
        )
        second_train, second_holdout, second_info = split_holdout(
            examples,
            holdout_fraction=0.3,
            seed=42,
            validation_mode="random_holdout",
        )

        self.assertEqual("random_holdout", first_info["validation_mode"])
        self.assertEqual([item.image_id for item in first_train], [item.image_id for item in second_train])
        self.assertEqual([item.image_id for item in first_holdout], [item.image_id for item in second_holdout])
        self.assertEqual(7, len(first_train))
        self.assertEqual(3, len(first_holdout))

    def test_category_grouped_holdout_samples_each_category(self) -> None:
        examples = []
        image_id = 0
        for category in ("sports", "portrait"):
            for _ in range(4):
                image_id += 1
                examples.append(self._split_example(image_id, folder_id="folder-a", primary_category=category))

        train, holdout, info = split_holdout(
            examples,
            holdout_fraction=0.25,
            seed=13,
            validation_mode="category_grouped_holdout",
        )

        self.assertEqual("category_grouped_holdout", info["validation_mode"])
        self.assertEqual({"sports", "portrait"}, {item.primary_category for item in holdout})
        self.assertEqual(6, len(train))
        self.assertEqual(2, len(holdout))

    def test_folder_grouped_holdout_falls_back_for_single_folder(self) -> None:
        examples = [
            type(
                "Example",
                (),
                {
                    "folder_id": "folder-a",
                    "primary_category": "cat-a",
                },
            )()
            for _ in range(6)
        ]

        train, holdout, info = split_holdout(
            examples,
            holdout_fraction=0.25,
            seed=13,
            validation_mode="folder_grouped_holdout",
        )

        self.assertEqual("folder_grouped_holdout", info["requested_mode"])
        self.assertEqual("category_grouped_holdout", info["validation_mode"])
        self.assertIsNotNone(info["warning"])
        self.assertTrue(train)
        self.assertTrue(holdout)

    def test_folder_grouped_holdout_keeps_folders_separate(self) -> None:
        examples = []
        for folder_id in ("folder-a", "folder-b", "folder-c"):
            for _ in range(3):
                examples.append(
                    type(
                        "Example",
                        (),
                        {
                            "folder_id": folder_id,
                            "primary_category": "cat-a",
                        },
                    )()
                )

        train, holdout, info = split_holdout(
            examples,
            holdout_fraction=0.33,
            seed=13,
            validation_mode="folder_grouped_holdout",
        )

        train_folders = {example.folder_id for example in train}
        holdout_folders = {example.folder_id for example in holdout}
        self.assertEqual("folder_grouped_holdout", info["validation_mode"])
        self.assertTrue(train_folders)
        self.assertTrue(holdout_folders)
        self.assertFalse(train_folders & holdout_folders)

    def test_folder_grouped_holdout_is_deterministic_when_one_folder_dominates(self) -> None:
        examples = []
        image_id = 0
        for folder_id, count in (("dominant", 8), ("small-a", 1), ("small-b", 1)):
            for _ in range(count):
                image_id += 1
                examples.append(self._split_example(image_id, folder_id=folder_id, primary_category="cat-a"))

        first_train, first_holdout, first_info = split_holdout(
            examples,
            holdout_fraction=0.2,
            seed=7,
            validation_mode="folder_grouped_holdout",
        )
        second_train, second_holdout, second_info = split_holdout(
            examples,
            holdout_fraction=0.2,
            seed=7,
            validation_mode="folder_grouped_holdout",
        )

        self.assertEqual("folder_grouped_holdout", first_info["validation_mode"])
        self.assertEqual([item.image_id for item in first_train], [item.image_id for item in second_train])
        self.assertEqual([item.image_id for item in first_holdout], [item.image_id for item in second_holdout])
        self.assertEqual(first_info["train_folder_count"], second_info["train_folder_count"])
        self.assertEqual(first_info["holdout_folder_count"], second_info["holdout_folder_count"])
        self.assertIsNotNone(first_info["train_folder_count"])
        self.assertIsNotNone(first_info["holdout_folder_count"])

    def test_adapter_training_records_validation_mode_and_culling_metrics(self) -> None:
        with tempfile.TemporaryDirectory(prefix="aiculler_validation_metrics_") as temp_dir:
            root = Path(temp_dir)
            store = SQLiteFeatureStore(root / "features.sqlite")
            try:
                rows = (
                    ("a", "one.jpg", [1.0, 0.0], "hero"),
                    ("a", "two.jpg", [0.8, 0.1], "keep"),
                    ("b", "three.jpg", [0.0, 1.0], "reject"),
                    ("b", "four.jpg", [0.1, 0.8], "weak"),
                )
                ratings_path = root / "ratings.csv"
                with ratings_path.open("w", encoding="utf-8", newline="") as handle:
                    writer = csv.DictWriter(handle, fieldnames=("source_path", "label", "folder_id"))
                    writer.writeheader()
                    for folder_id, filename, embedding, label in rows:
                        image_path = root / folder_id / filename
                        image_id = store.upsert_image(image_path, status="ready")
                        store.save_features(image_id, np.asarray(embedding, dtype=np.float32), technical_score=0.5)
                        writer.writerow({"source_path": str(image_path), "label": label, "folder_id": folder_id})
                import_ratings_csv(store, ratings_path)

                result = AdapterTrainer(
                    store,
                    projected_dim=2,
                    holdout_fraction=0.5,
                    validation_mode="folder_grouped_holdout",
                ).train(model_version="adapter-v1")
            finally:
                store.close()

        self.assertEqual("folder_grouped_holdout", result.metrics["validation"]["validation_mode"])
        self.assertIn("culling", result.metrics)
        self.assertIn("score_fit_percent", result.metrics["culling"])
        self.assertNotIn("pairwise", result.metrics)
        self.assertIn(result.metrics["model_selection"]["selected_global_model"], {"centroid", "regression"})
        self.assertEqual(["centroid", "regression"], result.metrics["model_selection"]["global_model_candidates"])

if __name__ == "__main__":
    unittest.main()
