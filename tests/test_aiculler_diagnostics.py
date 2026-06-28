from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from aiculler.diagnostics import adapter_feasibility_report
from aiculler.storage import SQLiteFeatureStore


class AICullerDiagnosticsTests(unittest.TestCase):
    def test_report_generation_includes_correlations_and_base_adapter_delta(self) -> None:
        with tempfile.TemporaryDirectory(prefix="aiculler_diag_") as temp_dir:
            root = Path(temp_dir)
            store = _build_store(root, folder_count=3, rows_per_folder=20, base_good=False, adapter_good=True)
            try:
                report = adapter_feasibility_report(store, "adapter-v1", folder_root=root)
            finally:
                store.close()

        self.assertEqual(1, report["schema_version"])
        self.assertEqual(3, report["feasibility"]["folder_count"])
        self.assertEqual(60, report["feasibility"]["effective_label_count"])
        self.assertEqual(15, report["feasibility"]["keeper_count"])
        self.assertEqual(15, report["feasibility"]["reason_tagged_label_count"])
        self.assertEqual({"composition": 15, "light_color": 15}, report["feasibility"]["reason_tag_counts"])
        self.assertFalse(report["duplicate_reject_inference"]["available"])
        self.assertIsNone(report["duplicate_reject_inference"]["duplicate_reject_fraction"])

        overall = report["base_vs_adapter"]["overall"]
        self.assertEqual("in_sample", report["base_vs_adapter"]["evaluation_scope"])
        self.assertEqual("limited", report["base_vs_adapter"]["generalization_claim"])
        self.assertGreater(overall["delta"]["top_30_recall_delta"], 0.5)
        self.assertLess(overall["delta"]["false_reject_rate_delta"], 0.0)
        features = report["feature_correlations"]["features"]
        self.assertIn("technical_score", features)
        self.assertIn("adapter_score", features)
        self.assertIn("technical_score_folder_percentile", features)
        winner_metrics = report["winner_metrics"]
        self.assertEqual("known_labels_in_scored_pool", winner_metrics["evaluation_scope"])
        self.assertEqual(15, winner_metrics["winner_count"])
        self.assertEqual(0.1, winner_metrics["random"]["winner"]["top_10_percent_recall"])
        self.assertGreater(winner_metrics["adapter"]["winner"]["top_10_percent_recall"], winner_metrics["random"]["winner"]["top_10_percent_recall"])
        self.assertEqual(1.0, winner_metrics["adapter"]["winner"]["top_20_image_hit_rate"])
        self.assertEqual(15, winner_metrics["adapter"]["winner"]["top_20_image_hits"])

    def test_weak_health_when_adapter_is_worse_than_base(self) -> None:
        with tempfile.TemporaryDirectory(prefix="aiculler_diag_weak_") as temp_dir:
            root = Path(temp_dir)
            store = _build_store(root, folder_count=3, rows_per_folder=20, base_good=True, adapter_good=False)
            try:
                report = adapter_feasibility_report(store, "adapter-v1")
            finally:
                store.close()

        self.assertEqual("weak", report["health"]["state"])
        self.assertTrue(any("top-30 recall" in reason for reason in report["health"]["reasons"]))

    def test_insufficient_data_health(self) -> None:
        with tempfile.TemporaryDirectory(prefix="aiculler_diag_small_") as temp_dir:
            root = Path(temp_dir)
            store = _build_store(root, folder_count=1, rows_per_folder=10, base_good=False, adapter_good=True)
            try:
                report = adapter_feasibility_report(store, "adapter-v1")
            finally:
                store.close()

        self.assertEqual("insufficient_data", report["health"]["state"])
        self.assertIn("fewer than 3 labeled folders", report["health"]["reasons"])
        self.assertEqual("unmeasurable", report["base_vs_adapter"]["generalization_claim"])
        self.assertEqual("undetermined", report["feature_correlations"]["features"]["adapter_score"]["stability_state"])
        self.assertEqual("too_few_to_measure", report["winner_metrics"]["sample_warning"]["state"])

    def test_duplicate_reject_inference_uses_phash_cache_when_available(self) -> None:
        with tempfile.TemporaryDirectory(prefix="aiculler_diag_phash_") as temp_dir:
            root = Path(temp_dir)
            store = _build_store(root, folder_count=3, rows_per_folder=20, base_good=False, adapter_good=True)
            try:
                ratings = store.list_ratings()
                keeper = next(row for row in ratings if row["label"] == "hero")
                reject = next(row for row in ratings if row["label"] == "reject")
                artifact_dir = root / ".image_triage_ai" / "phash_prefilter"
                artifact_dir.mkdir(parents=True, exist_ok=True)
                cache_payload = {
                    "schema_version": 1,
                    "entries": {
                        str(Path(keeper["source_path"]).resolve()).casefold(): {"hash": 100, "size": 1, "mtime_ns": 1},
                        str(Path(reject["source_path"]).resolve()).casefold(): {"hash": 101, "size": 1, "mtime_ns": 1},
                    },
                }
                (artifact_dir / "phash_cache.json").write_text(json.dumps(cache_payload), encoding="utf-8")
                (artifact_dir / "phash_prefilter_report.json").write_text(
                    json.dumps({"settings": {"hamming_threshold": 2}}),
                    encoding="utf-8",
                )
                report = adapter_feasibility_report(store, "adapter-v1", folder_root=root)
            finally:
                store.close()

        inference = report["duplicate_reject_inference"]
        self.assertTrue(inference["available"])
        self.assertEqual(1, inference["inferred_duplicate_reject_count"])
        self.assertEqual([int(reject["image_id"])], inference["excluded_image_ids"])

    def test_healthy_requires_high_confidence_and_consistent_improvement(self) -> None:
        with tempfile.TemporaryDirectory(prefix="aiculler_diag_healthy_") as temp_dir:
            root = Path(temp_dir)
            store = _build_store(root, folder_count=5, rows_per_folder=40, base_good=False, adapter_good=True)
            try:
                report = adapter_feasibility_report(store, "adapter-v1")
            finally:
                store.close()

        self.assertEqual("healthy", report["health"]["state"])
        self.assertEqual("high", report["health"]["confidence"])
        self.assertTrue(report["health"]["per_folder_consistency"]["is_consistent"])

    def test_report_is_stable_json_serializable(self) -> None:
        with tempfile.TemporaryDirectory(prefix="aiculler_diag_json_") as temp_dir:
            root = Path(temp_dir)
            store = _build_store(root, folder_count=3, rows_per_folder=20, base_good=False, adapter_good=True)
            try:
                report = adapter_feasibility_report(store, "adapter-v1")
            finally:
                store.close()

        payload = json.loads(json.dumps(report, sort_keys=True))
        self.assertEqual("adapter-v1", payload["model_version"])


def _build_store(
    root: Path,
    *,
    folder_count: int,
    rows_per_folder: int,
    base_good: bool,
    adapter_good: bool,
) -> SQLiteFeatureStore:
    store = SQLiteFeatureStore(root / "aiculler.sqlite")
    store.save_adapter_model(
        "adapter-v1",
        "centroid_style_adapter",
        {"base_weight": 0.0, "adapter_weight": 1.0},
        {},
    )
    ratings = []
    adapter_scores = {}
    keeper_count = max(2, rows_per_folder // 4)
    maybe_count = max(1, rows_per_folder // 4)
    for folder_index in range(folder_count):
        folder_id = f"folder-{folder_index}"
        for row_index in range(rows_per_folder):
            image_path = root / folder_id / f"image-{row_index:03d}.jpg"
            image_id = store.upsert_image(image_path, status="ready", metadata={"folder_id": folder_id})
            if row_index < keeper_count:
                label = "hero"
                numeric_score = 1.0
                reason_tags = ("composition", "light_color")
            elif row_index < keeper_count + maybe_count:
                label = "maybe"
                numeric_score = 0.5
                reason_tags = ()
            else:
                label = "reject"
                numeric_score = 0.0
                reason_tags = ()
            base_score = _score_for_label(numeric_score, good=base_good)
            adapter_score = _score_for_label(numeric_score, good=adapter_good)
            store.save_features(
                image_id,
                np.asarray([float(row_index), float(folder_index)], dtype=np.float32),
                technical_score=base_score,
                prompt_score=numeric_score,
                learned_user_score=adapter_score,
                profile_score=numeric_score,
                tag_base_score=base_score,
                tag_penalty=1.0 - numeric_score,
                final_score=base_score,
                metadata={"folder_id": folder_id},
            )
            ratings.append(
                {
                    "image_id": image_id,
                    "label": label,
                    "label_type": "bucket",
                    "numeric_score": numeric_score,
                    "source": "test",
                    "label_origin": "internal_adapter",
                    "primary_category": "cat",
                    "cluster_id": None,
                    "metadata": {"folder_id": folder_id, "reason_tags": list(reason_tags)},
                }
            )
            adapter_scores[image_id] = {
                "global_score": adapter_score,
                "category_score": None,
                "cluster_score": None,
                "adapter_score": adapter_score,
                "confidence": 1.0,
                "primary_category": "cat",
                "cluster_id": None,
            }
    store.add_ratings(ratings)
    store.save_adapter_scores("adapter-v1", adapter_scores)
    return store


def _score_for_label(numeric_score: float, *, good: bool) -> float:
    return numeric_score if good else 1.0 - numeric_score


if __name__ == "__main__":
    unittest.main()
