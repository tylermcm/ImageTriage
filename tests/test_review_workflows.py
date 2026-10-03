from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from image_triage.ai_results import AIBundle, AIConfidenceBucket, AIImageResult
from image_triage.models import ImageRecord, SessionAnnotation
from image_triage.review_intelligence import ReviewGroup, ReviewInsight, ReviewIntelligenceBundle
from image_triage.review_workflows import (
    BurstRecommendation,
    TasteProfile,
    build_burst_recommendations,
    build_pairwise_label_payload,
    build_record_workflow_insight,
    ai_disagreement_group_leader_path,
    disagreement_level_for,
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
    def test_disagreement_level_flags_user_keep_against_ai_reject(self) -> None:
        annotation = SessionAnnotation(winner=True)
        ai_result = _ai_result(
            "C:/shots/keep.jpg",
            group_id="ai-1",
            group_size=1,
            rank_in_group=1,
            score=0.12,
            normalized_score=10.0,
            bucket=AIConfidenceBucket.LIKELY_REJECT,
        )

        self.assertEqual("strong", disagreement_level_for(annotation, ai_result))

    def test_disagreement_level_flags_user_reject_against_ai_pick(self) -> None:
        annotation = SessionAnnotation(reject=True)
        ai_result = _ai_result(
            "C:/shots/reject.jpg",
            group_id="ai-1",
            group_size=3,
            rank_in_group=1,
            score=0.95,
            normalized_score=98.0,
            bucket=AIConfidenceBucket.OBVIOUS_WINNER,
        )

        self.assertEqual("strong", disagreement_level_for(annotation, ai_result))

    def test_ai_disagreement_group_leader_targets_ai_top_frame_only_for_non_leader(self) -> None:
        user_pick = _ai_result(
            "C:/shots/frame_02.jpg",
            group_id="ai-1",
            group_size=3,
            rank_in_group=2,
            score=0.65,
            normalized_score=62.0,
            bucket=AIConfidenceBucket.NEEDS_REVIEW,
        )
        ai_pick = _ai_result(
            "C:/shots/frame_01.jpg",
            group_id="ai-1",
            group_size=3,
            rank_in_group=1,
            score=0.91,
            normalized_score=94.0,
            bucket=AIConfidenceBucket.OBVIOUS_WINNER,
        )

        self.assertEqual(
            ai_pick.file_path,
            ai_disagreement_group_leader_path(user_pick.file_path, user_pick, (user_pick, ai_pick)),
        )

    def test_ai_disagreement_group_leader_omits_single_image_without_fake_pair(self) -> None:
        single = _ai_result(
            "C:/shots/single.jpg",
            group_id="ai-1",
            group_size=1,
            rank_in_group=1,
            score=0.1,
            normalized_score=8.0,
            bucket=AIConfidenceBucket.LIKELY_REJECT,
        )

        self.assertEqual("", ai_disagreement_group_leader_path(single.file_path, single, (single,)))

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
                "Highest combined score in this burst (a suggestion, not a verdict).",
                "Measurably sharper than the other frames.",
            ),
        )
        annotation = SessionAnnotation(winner=True, review_round="final_hero_selects")
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

        self.assertEqual(insight.review_round_label, "")
        self.assertTrue(insight.best_in_group)
        self.assertEqual(insight.disagreement_level, "strong")
        self.assertEqual(insight.disagreement_badge, "AI Miss")
        self.assertNotIn("Hero", insight.summary_text)
        self.assertIn("Suggested Frame", insight.summary_text)
        self.assertNotIn("Best Frame", insight.summary_text)
        self.assertIn("AI Disagreement", insight.summary_text)
        self.assertTrue(any("Suggested frame: top-scoring of 4" in line for line in insight.detail_lines))
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
        self.assertTrue(any("Measurably sharper" in line for line in leader.reasons))

    # --- what a suggestion is allowed to claim -------------------------------------------------------
    @staticmethod
    def _burst_of(frames: list[tuple[float, float]]):
        """A burst of frames (detail_score, exposure_score) with no AI results; returns (records, recommendations)."""
        records = [_record(f"C:/shots/frame_{index:02d}.jpg", modified_ns=index + 1) for index in range(len(frames))]
        review_bundle = _review_bundle(
            groups=(
                ReviewGroup(id="burst-1", kind="burst", label="Burst", member_paths=tuple(record.path for record in records)),
            ),
            insights=tuple(
                ReviewInsight(
                    path=record.path, group_id="burst-1", group_kind="burst", group_label="Burst",
                    group_size=len(frames), rank_in_group=index + 1, detail_score=detail, exposure_score=exposure,
                )
                for index, (record, (detail, exposure)) in enumerate(zip(records, frames))
            ),
        )
        _taste, recommendations = build_burst_recommendations(
            records, ai_bundle=None, review_bundle=review_bundle, correction_events=[]
        )
        return records, recommendations

    def test_a_suggestion_does_not_claim_sharper_when_the_frames_are_equally_sharp(self) -> None:
        # The old rule fired for any leader with a detail score of 60 or more, which is nearly every frame.
        records, recommendations = self._burst_of([(95.0, 80.0), (96.0, 80.0), (95.5, 80.0)])
        leader = next(rec for rec in recommendations.values() if rec.is_recommended)
        text = " ".join(leader.reasons).lower()
        self.assertNotIn("sharper", text)
        self.assertNotIn("cleaner exposure", text)
        self.assertIn("about the same across these frames", text)
        self.assertIn("not a verdict", text)

    def test_a_suggestion_claims_sharper_only_for_a_real_gap(self) -> None:
        _records, small = self._burst_of([(80.0, 80.0), (80.0 * 1.14, 80.0)])
        _records, real = self._burst_of([(80.0, 80.0), (80.0 * 1.30, 80.0)])
        small_leader = next(rec for rec in small.values() if rec.is_recommended)
        real_leader = next(rec for rec in real.values() if rec.is_recommended)
        self.assertFalse(any("sharper" in line.lower() for line in small_leader.reasons))
        self.assertTrue(any("Measurably sharper" in line for line in real_leader.reasons))

    def test_a_suggestion_claims_cleaner_exposure_only_for_a_real_gap(self) -> None:
        _records, small = self._burst_of([(90.0, 70.0), (90.0, 70.0 + 9.0)])
        _records, real = self._burst_of([(90.0, 70.0), (90.0, 70.0 + 11.0)])
        small_leader = next(rec for rec in small.values() if rec.is_recommended)
        real_leader = next(rec for rec in real.values() if rec.is_recommended)
        self.assertFalse(any("cleaner exposure" in line.lower() for line in small_leader.reasons))
        self.assertTrue(any("Cleaner exposure" in line for line in real_leader.reasons))

    def test_other_frames_are_called_softer_only_when_clearly_softer_than_the_suggestion(self) -> None:
        records, recommendations = self._burst_of([(100.0, 80.0), (90.0, 80.0), (70.0, 80.0)])
        by_path = {rec.path: rec for rec in recommendations.values() if not rec.is_recommended}
        slightly_softer = by_path[records[1].path]
        much_softer = by_path[records[2].path]
        self.assertFalse(any("softer" in line.lower() for line in slightly_softer.reasons))
        self.assertTrue(any("Softer than the suggested frame" in line for line in much_softer.reasons))
        for rec in by_path.values():
            self.assertNotIn("leader looks stronger", " ".join(rec.reasons))

    def test_changing_the_scoring_version_invalidates_cached_recommendations(self) -> None:
        import image_triage.review_workflows as workflows

        records = [_record("C:/shots/frame_01.jpg"), _record("C:/shots/frame_02.jpg", modified_ns=2)]
        args = dict(ai_bundle=None, review_bundle=None, correction_events=[])
        before = workflows.build_review_scoring_cache_key(records, **args)
        original = workflows._REVIEW_SCORING_CACHE_VERSION
        workflows._REVIEW_SCORING_CACHE_VERSION = original + 1
        try:
            after = workflows.build_review_scoring_cache_key(records, **args)
        finally:
            workflows._REVIEW_SCORING_CACHE_VERSION = original
        self.assertNotEqual(before, after)

    def test_a_burst_suggestion_is_not_called_winner_on_the_card(self) -> None:
        from types import SimpleNamespace

        from image_triage.grid import ThumbnailGridView

        label = ThumbnailGridView._review_keeper_label
        self.assertEqual("Suggested", label(None, None, SimpleNamespace(best_in_group=True)))
        self.assertEqual("", label(None, None, SimpleNamespace(best_in_group=False)))
        top_pick = _ai_result(
            "C:/shots/a.jpg", group_id="g", group_size=3, rank_in_group=1, score=0.9, normalized_score=95.0,
            bucket=AIConfidenceBucket.OBVIOUS_WINNER,
        )
        self.assertEqual("Winner", label(None, top_pick, SimpleNamespace(best_in_group=True)))

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
