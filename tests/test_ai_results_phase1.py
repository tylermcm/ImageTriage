from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from image_triage.ai_results import AIConfidenceBucket, build_ai_explanation_lines, load_ai_bundle


class AIResultsPhase1Tests(unittest.TestCase):
    def test_load_ai_bundle_assigns_confidence_buckets_and_explanations(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            csv_path = Path(temp_dir) / "ranked_clusters_export.csv"
            rows = [
                {
                    "file_path": str(Path(temp_dir) / "winner.jpg"),
                    "file_name": "winner.jpg",
                    "cluster_id": "group-a",
                    "cluster_size": "3",
                    "rank_in_cluster": "1",
                    "score": "0.95",
                    "cluster_reason": "Best expression and clean framing",
                },
                {
                    "file_path": str(Path(temp_dir) / "middle.jpg"),
                    "file_name": "middle.jpg",
                    "cluster_id": "group-a",
                    "cluster_size": "3",
                    "rank_in_cluster": "2",
                    "score": "0.70",
                    "cluster_reason": "Good frame but slightly weaker subject pose",
                },
                {
                    "file_path": str(Path(temp_dir) / "lower.jpg"),
                    "file_name": "lower.jpg",
                    "cluster_id": "group-a",
                    "cluster_size": "3",
                    "rank_in_cluster": "3",
                    "score": "0.40",
                    "cluster_reason": "Eyes are softer than the top pick",
                },
                {
                    "file_path": str(Path(temp_dir) / "single.jpg"),
                    "file_name": "single.jpg",
                    "cluster_id": "group-b",
                    "cluster_size": "1",
                    "rank_in_cluster": "1",
                    "score": "0.20",
                    "cluster_reason": "Single image in folder",
                },
            ]
            with csv_path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=tuple(rows[0].keys()))
                writer.writeheader()
                writer.writerows(rows)

            bundle = load_ai_bundle(temp_dir)

            top = bundle.result_for_path(rows[0]["file_path"])
            middle = bundle.result_for_path(rows[1]["file_path"])
            single = bundle.result_for_path(rows[3]["file_path"])

            self.assertIsNotNone(top)
            self.assertIsNotNone(middle)
            self.assertIsNotNone(single)

            assert top is not None
            assert middle is not None
            assert single is not None

            self.assertEqual(top.confidence_bucket, AIConfidenceBucket.OBVIOUS_WINNER)
            self.assertAlmostEqual(top.normalized_score or 0.0, 100.0)
            self.assertEqual(middle.confidence_bucket, AIConfidenceBucket.NEEDS_REVIEW)
            self.assertIsNone(single.normalized_score)
            self.assertEqual(single.confidence_bucket, AIConfidenceBucket.LIKELY_REJECT)

            lines = build_ai_explanation_lines(top, review_summary="Near Dup 1/2")
            self.assertTrue(lines)
            self.assertEqual(lines[0], "Confidence bucket: Obvious winner.")
            self.assertTrue(any("led the next frame" in line for line in lines))
            self.assertTrue(any("Local grouping: Near Dup 1/2." == line for line in lines))


if __name__ == "__main__":
    unittest.main()
