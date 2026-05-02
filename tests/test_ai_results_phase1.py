from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from image_triage.ai_results import (
    AICullBucket,
    AIConfidenceBucket,
    AIImageResult,
    ai_cull_bucket_for_result,
    ai_manual_cull_sort_key,
    ai_review_badge_label,
    ai_review_tag_definitions,
    build_ai_explanation_lines,
    load_ai_bundle,
    refine_ai_result_with_review_insight,
)
from image_triage.review_intelligence import ReviewInsight


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

    def test_cluster_leader_can_be_rejected_when_the_whole_cluster_is_weak(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            csv_path = Path(temp_dir) / "ranked_clusters_export.csv"
            rows = [
                {
                    "file_path": str(Path(temp_dir) / "hero_a.jpg"),
                    "file_name": "hero_a.jpg",
                    "cluster_id": "group-a",
                    "cluster_size": "2",
                    "rank_in_cluster": "1",
                    "score": "14.0",
                    "cluster_reason": "Strong composition",
                },
                {
                    "file_path": str(Path(temp_dir) / "hero_b.jpg"),
                    "file_name": "hero_b.jpg",
                    "cluster_id": "group-a",
                    "cluster_size": "2",
                    "rank_in_cluster": "2",
                    "score": "13.2",
                    "cluster_reason": "Similar alternate frame",
                },
                {
                    "file_path": str(Path(temp_dir) / "strong_single_1.jpg"),
                    "file_name": "strong_single_1.jpg",
                    "cluster_id": "group-c",
                    "cluster_size": "1",
                    "rank_in_cluster": "1",
                    "score": "12.0",
                    "cluster_reason": "Strong standalone frame",
                },
                {
                    "file_path": str(Path(temp_dir) / "strong_single_2.jpg"),
                    "file_name": "strong_single_2.jpg",
                    "cluster_id": "group-d",
                    "cluster_size": "1",
                    "rank_in_cluster": "1",
                    "score": "11.0",
                    "cluster_reason": "Strong standalone frame",
                },
                {
                    "file_path": str(Path(temp_dir) / "strong_single_3.jpg"),
                    "file_name": "strong_single_3.jpg",
                    "cluster_id": "group-e",
                    "cluster_size": "1",
                    "rank_in_cluster": "1",
                    "score": "10.0",
                    "cluster_reason": "Strong standalone frame",
                },
                {
                    "file_path": str(Path(temp_dir) / "strong_single_4.jpg"),
                    "file_name": "strong_single_4.jpg",
                    "cluster_id": "group-f",
                    "cluster_size": "1",
                    "rank_in_cluster": "1",
                    "score": "9.0",
                    "cluster_reason": "Strong standalone frame",
                },
                {
                    "file_path": str(Path(temp_dir) / "blank_top.jpg"),
                    "file_name": "blank_top.jpg",
                    "cluster_id": "group-b",
                    "cluster_size": "2",
                    "rank_in_cluster": "1",
                    "score": "-4.0",
                    "cluster_reason": "Low-information frame",
                },
                {
                    "file_path": str(Path(temp_dir) / "blank_other.jpg"),
                    "file_name": "blank_other.jpg",
                    "cluster_id": "group-b",
                    "cluster_size": "2",
                    "rank_in_cluster": "2",
                    "score": "-4.8",
                    "cluster_reason": "Low-information alternate frame",
                },
            ]
            with csv_path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=tuple(rows[0].keys()))
                writer.writeheader()
                writer.writerows(rows)

            bundle = load_ai_bundle(temp_dir)
            weak_top = bundle.result_for_path(rows[-2]["file_path"])
            strong_top = bundle.result_for_path(rows[0]["file_path"])

            self.assertIsNotNone(weak_top)
            self.assertIsNotNone(strong_top)
            assert weak_top is not None
            assert strong_top is not None

            self.assertEqual(weak_top.confidence_bucket, AIConfidenceBucket.LIKELY_REJECT)
            self.assertFalse(weak_top.is_top_pick)
            self.assertTrue(weak_top.is_rank_leader)
            self.assertTrue(weak_top.is_weak_cluster_leader)
            self.assertIn("whole cluster", weak_top.confidence_summary)

            self.assertTrue(strong_top.is_top_pick)

    def test_third_place_group_frame_is_rejected_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            csv_path = Path(temp_dir) / "ranked_clusters_export.csv"
            rows = [
                {
                    "file_path": str(Path(temp_dir) / "winner.jpg"),
                    "file_name": "winner.jpg",
                    "cluster_id": "group-a",
                    "cluster_size": "4",
                    "rank_in_cluster": "1",
                    "score": "0.95",
                    "cluster_reason": "Best frame",
                },
                {
                    "file_path": str(Path(temp_dir) / "runner_up.jpg"),
                    "file_name": "runner_up.jpg",
                    "cluster_id": "group-a",
                    "cluster_size": "4",
                    "rank_in_cluster": "2",
                    "score": "0.80",
                    "cluster_reason": "Strong alternate",
                },
                {
                    "file_path": str(Path(temp_dir) / "third.jpg"),
                    "file_name": "third.jpg",
                    "cluster_id": "group-a",
                    "cluster_size": "4",
                    "rank_in_cluster": "3",
                    "score": "0.58",
                    "cluster_reason": "Clearly weaker than the first two",
                },
                {
                    "file_path": str(Path(temp_dir) / "tail.jpg"),
                    "file_name": "tail.jpg",
                    "cluster_id": "group-a",
                    "cluster_size": "4",
                    "rank_in_cluster": "4",
                    "score": "0.31",
                    "cluster_reason": "Weak tail frame",
                },
            ]
            with csv_path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=tuple(rows[0].keys()))
                writer.writeheader()
                writer.writerows(rows)

            bundle = load_ai_bundle(temp_dir)
            third = bundle.result_for_path(rows[2]["file_path"])

            self.assertIsNotNone(third)
            assert third is not None
            self.assertEqual(third.confidence_bucket, AIConfidenceBucket.LIKELY_REJECT)
            self.assertIn("Third-place", third.confidence_summary)

    def test_singleton_in_bottom_quartile_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            csv_path = Path(temp_dir) / "ranked_clusters_export.csv"
            rows = [
                {
                    "file_path": str(Path(temp_dir) / "top.jpg"),
                    "file_name": "top.jpg",
                    "cluster_id": "group-a",
                    "cluster_size": "1",
                    "rank_in_cluster": "1",
                    "score": "0.95",
                    "cluster_reason": "Top singleton",
                },
                {
                    "file_path": str(Path(temp_dir) / "high.jpg"),
                    "file_name": "high.jpg",
                    "cluster_id": "group-b",
                    "cluster_size": "1",
                    "rank_in_cluster": "1",
                    "score": "0.80",
                    "cluster_reason": "High singleton",
                },
                {
                    "file_path": str(Path(temp_dir) / "middle.jpg"),
                    "file_name": "middle.jpg",
                    "cluster_id": "group-c",
                    "cluster_size": "1",
                    "rank_in_cluster": "1",
                    "score": "0.70",
                    "cluster_reason": "Middle singleton",
                },
                {
                    "file_path": str(Path(temp_dir) / "low.jpg"),
                    "file_name": "low.jpg",
                    "cluster_id": "group-d",
                    "cluster_size": "1",
                    "rank_in_cluster": "1",
                    "score": "0.30",
                    "cluster_reason": "Lower-quartile singleton",
                },
                {
                    "file_path": str(Path(temp_dir) / "bottom.jpg"),
                    "file_name": "bottom.jpg",
                    "cluster_id": "group-e",
                    "cluster_size": "1",
                    "rank_in_cluster": "1",
                    "score": "0.20",
                    "cluster_reason": "Bottom singleton",
                },
            ]
            with csv_path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=tuple(rows[0].keys()))
                writer.writeheader()
                writer.writerows(rows)

            bundle = load_ai_bundle(temp_dir)
            low = bundle.result_for_path(rows[3]["file_path"])

            self.assertIsNotNone(low)
            assert low is not None
            self.assertEqual(low.folder_percentile, 25.0)
            self.assertEqual(low.confidence_bucket, AIConfidenceBucket.LIKELY_REJECT)

    def test_extreme_low_detail_cluster_leader_is_demoted_to_reject(self) -> None:
        result = AIImageResult(
            image_id="hero-1",
            file_path="C:/shots/blankish.jpg",
            file_name="blankish.jpg",
            group_id="group-a",
            group_size=3,
            rank_in_group=1,
            score=0.95,
            normalized_score=100.0,
            folder_percentile=98.0,
            score_gap_to_next=0.25,
            confidence_bucket=AIConfidenceBucket.OBVIOUS_WINNER,
            confidence_summary="Clear lead inside its AI group.",
        )
        insight = ReviewInsight(
            path=result.file_path,
            detail_score=5.0,
            exposure_score=78.0,
        )

        refined = refine_ai_result_with_review_insight(result, insight)

        self.assertIsNotNone(refined)
        assert refined is not None
        self.assertEqual(refined.confidence_bucket, AIConfidenceBucket.LIKELY_REJECT)
        self.assertFalse(refined.is_top_pick)
        self.assertIn("low-detail", refined.confidence_summary)
        self.assertIn("Clear lead inside its AI group", refined.confidence_summary)

    def test_extreme_low_detail_singleton_is_softened_to_review_not_auto_reject(self) -> None:
        result = AIImageResult(
            image_id="single-1",
            file_path="C:/shots/minimal.jpg",
            file_name="minimal.jpg",
            group_id="single-a",
            group_size=1,
            rank_in_group=1,
            score=0.82,
            folder_percentile=97.0,
            confidence_bucket=AIConfidenceBucket.LIKELY_KEEPER,
            confidence_summary="High single-image score compared with the rest of the folder.",
        )
        insight = ReviewInsight(
            path=result.file_path,
            detail_score=5.0,
            exposure_score=81.0,
        )

        refined = refine_ai_result_with_review_insight(result, insight)

        self.assertIsNotNone(refined)
        assert refined is not None
        self.assertEqual(refined.confidence_bucket, AIConfidenceBucket.NEEDS_REVIEW)
        self.assertFalse(refined.is_top_pick)
        self.assertIn("checked manually", refined.confidence_summary)

    def test_ai_cull_bucket_and_badge_label_distinguish_top_pick_from_keeper(self) -> None:
        ai_pick = AIImageResult(
            image_id="pick-1",
            file_path="C:/shots/pick.jpg",
            file_name="pick.jpg",
            group_id="group-a",
            group_size=3,
            rank_in_group=1,
            score=0.98,
            confidence_bucket=AIConfidenceBucket.OBVIOUS_WINNER,
            confidence_summary="Clear lead inside its AI group.",
        )
        keeper = AIImageResult(
            image_id="keeper-1",
            file_path="C:/shots/keeper.jpg",
            file_name="keeper.jpg",
            group_id="group-b",
            group_size=1,
            rank_in_group=1,
            score=0.74,
            confidence_bucket=AIConfidenceBucket.LIKELY_KEEPER,
            confidence_summary="High single-image score compared with the rest of the folder.",
        )

        self.assertEqual(ai_cull_bucket_for_result(ai_pick), AICullBucket.AI_PICK)
        self.assertEqual(ai_review_badge_label(ai_pick), "AI Pick")
        self.assertEqual(ai_cull_bucket_for_result(keeper), AICullBucket.KEEPER)
        self.assertEqual(ai_review_badge_label(keeper), "Keeper")

    def test_manual_cull_sort_key_prioritizes_ai_pick_then_reject_then_keeper_then_review(self) -> None:
        ai_pick = AIImageResult(
            image_id="pick-1",
            file_path="C:/shots/pick.jpg",
            file_name="pick.jpg",
            group_id="group-a",
            group_size=3,
            rank_in_group=1,
            score=0.98,
            folder_percentile=99.0,
            confidence_bucket=AIConfidenceBucket.OBVIOUS_WINNER,
            confidence_summary="Clear lead inside its AI group.",
        )
        reject = AIImageResult(
            image_id="reject-1",
            file_path="C:/shots/reject.jpg",
            file_name="reject.jpg",
            group_id="group-b",
            group_size=1,
            rank_in_group=1,
            score=0.12,
            folder_percentile=4.0,
            confidence_bucket=AIConfidenceBucket.LIKELY_REJECT,
            confidence_summary="Single-image score lands near the bottom of the folder.",
        )
        keeper = AIImageResult(
            image_id="keeper-1",
            file_path="C:/shots/keeper.jpg",
            file_name="keeper.jpg",
            group_id="group-c",
            group_size=1,
            rank_in_group=1,
            score=0.72,
            folder_percentile=86.0,
            confidence_bucket=AIConfidenceBucket.LIKELY_KEEPER,
            confidence_summary="High single-image score compared with the rest of the folder.",
        )
        review = AIImageResult(
            image_id="review-1",
            file_path="C:/shots/review.jpg",
            file_name="review.jpg",
            group_id="group-d",
            group_size=2,
            rank_in_group=2,
            score=0.55,
            folder_percentile=51.0,
            confidence_bucket=AIConfidenceBucket.NEEDS_REVIEW,
            confidence_summary="Model signals are mixed enough to warrant a human pass.",
        )

        ordered = sorted(
            (review, keeper, reject, ai_pick),
            key=ai_manual_cull_sort_key,
        )

        self.assertEqual([item.file_name for item in ordered], ["pick.jpg", "reject.jpg", "keeper.jpg", "review.jpg"])

    def test_ai_review_tag_definitions_cover_primary_badges(self) -> None:
        definitions = dict(ai_review_tag_definitions())

        self.assertIn("AI Pick", definitions)
        self.assertIn("Keeper", definitions)
        self.assertIn("Needs Review", definitions)
        self.assertIn("Reject", definitions)
        self.assertIn("Best Frame", definitions)
        self.assertIn("AI Review", definitions)


if __name__ == "__main__":
    unittest.main()
