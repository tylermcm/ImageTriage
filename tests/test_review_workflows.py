from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from image_triage.ai_results import AIBundle, AIConfidenceBucket, AIImageResult
from image_triage.models import ImageRecord, SessionAnnotation
from image_triage.review_intelligence import ReviewGroup, ReviewInsight, ReviewIntelligenceBundle
from image_triage.review_workflows import (
    REVIEW_ROUND_HERO,
    REVIEW_ROUND_SECOND_PASS,
    BurstRecommendation,
    CalibrationPair,
    TasteProfile,
    build_burst_recommendations,
    build_calibration_pairs,
    build_pairwise_label_payload,
    build_record_workflow_insight,
    stable_image_id_for_path,
)
from image_triage.scanner import normalized_path_key


def _record(path: str, *, modified_ns: int = 1) -> ImageRecord:
    resolved = Path(path)
    return ImageRecord(
        path=str(resolved),
        name=resolved.name,
        size=1024,
        modified_ns=modified_ns,
    )


def _ai_result(
    path: str,
    *,
    group_id: str,
    group_size: int,
    rank_in_group: int,
    score: float,
    normalized_score: float,
    bucket: AIConfidenceBucket,
) -> AIImageResult:
    resolved = Path(path)
    return AIImageResult(
        image_id=resolved.stem,
        file_path=str(resolved),
        file_name=resolved.name,
        group_id=group_id,
        group_size=group_size,
        rank_in_group=rank_in_group,
        score=score,
        normalized_score=normalized_score,
        confidence_bucket=bucket,
        confidence_summary="",
    )


def _ai_bundle(*results: AIImageResult) -> AIBundle:
    results_by_path = {normalized_path_key(result.file_path): result for result in results}
    return AIBundle(
        source_path="C:/shots",
        export_csv_path="C:/shots/ranked_clusters_export.csv",
        results_by_path=results_by_path,
    )


def _review_bundle(
    *,
    groups: tuple[ReviewGroup, ...],
    insights: tuple[ReviewInsight, ...],
) -> ReviewIntelligenceBundle:
    insights_by_path: dict[str, ReviewInsight] = {}
    for insight in insights:
        insights_by_path[insight.path] = insight
        insights_by_path[normalized_path_key(insight.path)] = insight
    return ReviewIntelligenceBundle(groups=groups, insights_by_path=insights_by_path)


class ReviewWorkflowTests(unittest.TestCase):
    def test_build_record_workflow_insight_surfaces_best_frame_and_ai_disagreement(self) -> None:
        recommendation = BurstRecommendation(
            path="C:/shots/hero.jpg",
            group_id="burst-1",
            group_label="Burst",
            group_size=4,
            recommended_path="C:/shots/hero.jpg",
            rank_in_group=1,
            score=96.0,
            recommended_score=96.0,
            is_recommended=True,
            reasons=(
                "Best overall signal inside this burst.",
                "Detail retention is stronger than the nearby frames.",
            ),
        )
        annotation = SessionAnnotation(winner=True, review_round=REVIEW_ROUND_HERO)
        ai_result = _ai_result(
            "C:/shots/hero.jpg",
            group_id="ai-1",
            group_size=4,
            rank_in_group=4,
            score=0.42,
            normalized_score=22.0,
            bucket=AIConfidenceBucket.LIKELY_REJECT,
        )

        insight = build_record_workflow_insight(
            annotation=annotation,
            ai_result=ai_result,
            burst_recommendation=recommendation,
            taste_profile=TasteProfile(summary_lines=("Recent picks lean toward crisper detail.",)),
        )

        self.assertEqual(insight.review_round_label, "Final Hero Selects")
        self.assertTrue(insight.best_in_group)
        self.assertEqual(insight.disagreement_level, "strong")
        self.assertEqual(insight.disagreement_badge, "AI Miss")
        self.assertIn("Hero", insight.summary_text)
        self.assertIn("Best Frame", insight.summary_text)
        self.assertIn("AI Disagreement", insight.summary_text)
        self.assertTrue(any("Burst specialist pick" in line for line in insight.detail_lines))
        self.assertTrue(any("Taste profile:" in line for line in insight.detail_lines))

    def test_build_burst_recommendations_picks_practical_best_frame(self) -> None:
        records = [
            _record("C:/shots/frame_01.jpg", modified_ns=1),
            _record("C:/shots/frame_02.jpg", modified_ns=2),
            _record("C:/shots/frame_03.jpg", modified_ns=3),
        ]
        review_bundle = _review_bundle(
            groups=(
                ReviewGroup(
                    id="burst-1",
                    kind="burst",
                    label="Burst",
                    member_paths=tuple(record.path for record in records),
                ),
            ),
            insights=(
                ReviewInsight(path=records[0].path, group_id="burst-1", group_kind="burst", group_label="Burst", group_size=3, rank_in_group=1, detail_score=92.0, exposure_score=86.0),
                ReviewInsight(path=records[1].path, group_id="burst-1", group_kind="burst", group_label="Burst", group_size=3, rank_in_group=2, detail_score=58.0, exposure_score=70.0),
                ReviewInsight(path=records[2].path, group_id="burst-1", group_kind="burst", group_label="Burst", group_size=3, rank_in_group=3, detail_score=72.0, exposure_score=62.0),
            ),
        )
        ai_bundle = _ai_bundle(
            _ai_result(records[0].path, group_id="ai-1", group_size=3, rank_in_group=1, score=0.93, normalized_score=96.0, bucket=AIConfidenceBucket.OBVIOUS_WINNER),
            _ai_result(records[1].path, group_id="ai-1", group_size=3, rank_in_group=3, score=0.45, normalized_score=31.0, bucket=AIConfidenceBucket.LIKELY_REJECT),
            _ai_result(records[2].path, group_id="ai-1", group_size=3, rank_in_group=2, score=0.71, normalized_score=64.0, bucket=AIConfidenceBucket.LIKELY_KEEPER),
        )
        correction_events = [
            {
                "payload": {
                    "preferred_detail_score": 92.0,
                    "other_detail_score": 58.0,
                    "preferred_ai_strength": 0.96,
                    "other_ai_strength": 0.31,
                }
            }
        ]

        taste_profile, recommendations = build_burst_recommendations(
            records,
            ai_bundle=ai_bundle,
            review_bundle=review_bundle,
            correction_events=correction_events,
        )

        self.assertEqual(taste_profile.event_count, 1)
        self.assertGreater(taste_profile.detail_bias, 0.0)
        self.assertIn("crisper detail", taste_profile.summary_lines[0].casefold())

        leader = recommendations[records[0].path]
        runner_up = recommendations[records[1].path]
        self.assertTrue(leader.is_recommended)
        self.assertEqual(leader.recommended_path, records[0].path)
        self.assertEqual(leader.rank_in_group, 1)
        self.assertGreater(leader.score, runner_up.score)
        self.assertTrue(any("Detail retention" in line for line in leader.reasons))

    def test_build_calibration_pairs_prefers_burst_and_close_ai_comparisons(self) -> None:
        records = [
            _record("C:/shots/burst_01.jpg", modified_ns=1),
            _record("C:/shots/burst_02.jpg", modified_ns=2),
            _record("C:/shots/final_01.jpg", modified_ns=3),
            _record("C:/shots/final_02.jpg", modified_ns=4),
        ]
        review_bundle = _review_bundle(
            groups=(
                ReviewGroup(
                    id="burst-1",
                    kind="burst",
                    label="Burst",
                    member_paths=(records[0].path, records[1].path),
                ),
            ),
            insights=(
                ReviewInsight(path=records[0].path, group_id="burst-1", group_kind="burst", group_label="Burst", group_size=2, rank_in_group=1, detail_score=88.0, exposure_score=80.0),
                ReviewInsight(path=records[1].path, group_id="burst-1", group_kind="burst", group_label="Burst", group_size=2, rank_in_group=2, detail_score=84.0, exposure_score=77.0),
            ),
        )
        burst_recommendations = {
            records[0].path: BurstRecommendation(
                path=records[0].path,
                group_id="burst-1",
                group_label="Burst",
                group_size=2,
                recommended_path=records[0].path,
                rank_in_group=1,
                score=92.0,
                recommended_score=92.0,
                is_recommended=True,
            ),
            records[1].path: BurstRecommendation(
                path=records[1].path,
                group_id="burst-1",
                group_label="Burst",
                group_size=2,
                recommended_path=records[0].path,
                rank_in_group=2,
                score=86.0,
                recommended_score=92.0,
                is_recommended=False,
            ),
        }
        ai_bundle = _ai_bundle(
            _ai_result(records[2].path, group_id="ai-close", group_size=2, rank_in_group=1, score=0.77, normalized_score=71.0, bucket=AIConfidenceBucket.LIKELY_KEEPER),
            _ai_result(records[3].path, group_id="ai-close", group_size=2, rank_in_group=2, score=0.74, normalized_score=67.0, bucket=AIConfidenceBucket.NEEDS_REVIEW),
        )

        pairs = build_calibration_pairs(
            records,
            ai_bundle=ai_bundle,
            review_bundle=review_bundle,
            burst_recommendations=burst_recommendations,
            limit=4,
        )

        self.assertGreaterEqual(len(pairs), 2)
        self.assertEqual(pairs[0].group_id, "burst-1")
        self.assertIn("burst", pairs[0].prompt.casefold())
        self.assertEqual({pairs[0].left_path, pairs[0].right_path}, {records[0].path, records[1].path})

        ai_pair = next((pair for pair in pairs if pair.group_id == "ai-close"), None)
        self.assertIsNotNone(ai_pair)
        assert ai_pair is not None
        self.assertEqual(ai_pair.group_label, "AI Group")
        self.assertIn("ai finalists", ai_pair.prompt.casefold())
        self.assertEqual({ai_pair.left_path, ai_pair.right_path}, {records[2].path, records[3].path})

    def test_build_pairwise_label_payload_uses_stable_relative_ids(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            folder = Path(temp_dir)
            left_path = folder / "set_a" / "left.jpg"
            right_path = folder / "set_a" / "right.jpg"

            payload = build_pairwise_label_payload(
                folder=folder,
                left_path=str(left_path),
                right_path=str(right_path),
                preferred_path=str(left_path),
                source_mode="winner_ladder",
                cluster_id="burst-9",
                annotator_id="session-1",
            )

            self.assertEqual(payload["image_a_id"], stable_image_id_for_path(folder, left_path))
            self.assertEqual(payload["image_b_id"], stable_image_id_for_path(folder, right_path))
            self.assertEqual(payload["preferred_image_id"], stable_image_id_for_path(folder, left_path))
            self.assertEqual(payload["decision"], "left_better")
            self.assertEqual(payload["source_mode"], "winner_ladder")
            self.assertEqual(payload["cluster_id"], "burst-9")
            self.assertEqual(payload["annotator_id"], "session-1")
            self.assertTrue(str(payload["timestamp"]).endswith("+00:00"))


if __name__ == "__main__":
    unittest.main()
