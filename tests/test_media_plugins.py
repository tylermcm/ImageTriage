from __future__ import annotations

import unittest

from image_triage.imaging import display_provider_id_for_path
from image_triage import metadata as metadata_module
from image_triage import review_intelligence as review_intelligence_module
from image_triage import review_workflows as review_workflows_module
from image_triage.metadata import CaptureMetadata, load_capture_metadata, metadata_provider_id_for_path
from image_triage.ai_results import AIBundle
from image_triage.models import ImageRecord
from image_triage.plugins import (
    MetadataLoadRequest,
    ReviewGroupingRequest,
    ReviewScoringRequest,
    iter_display_providers,
    iter_metadata_providers,
    iter_review_grouping_providers,
    iter_review_scoring_providers,
    register_metadata_provider,
    register_review_grouping_provider,
    register_review_scoring_provider,
)
from image_triage.review_intelligence import ReviewInsight, ReviewIntelligenceBundle, build_review_intelligence, review_grouping_provider_id
from image_triage.review_workflows import BurstRecommendation, TasteProfile, build_burst_recommendations, review_scoring_provider_id


class MediaPluginTests(unittest.TestCase):
    def test_builtin_display_provider_resolution(self) -> None:
        self.assertEqual("fits", display_provider_id_for_path(r"C:\shots\m42.fits"))
        self.assertEqual("raw", display_provider_id_for_path(r"C:\shots\frame.nef"))
        self.assertEqual("psd", display_provider_id_for_path(r"C:\shots\edit.psd"))
        self.assertEqual("model", display_provider_id_for_path(r"C:\shots\mesh.stl"))
        self.assertEqual("default", display_provider_id_for_path(r"C:\shots\preview.jpg"))
        self.assertEqual("default", display_provider_id_for_path(r"C:\shots\unknown.xyz"))

    def test_builtin_display_provider_registration_is_idempotent(self) -> None:
        display_provider_id_for_path(r"C:\shots\preview.jpg")
        first_ids = [provider.provider_id for provider in iter_display_providers()]
        display_provider_id_for_path(r"C:\shots\preview.jpg")
        second_ids = [provider.provider_id for provider in iter_display_providers()]

        self.assertEqual(first_ids, second_ids)
        self.assertEqual(["model", "fits", "raw", "psd", "default"], first_ids)

    def test_builtin_metadata_provider_resolution(self) -> None:
        self.assertEqual("fits", metadata_provider_id_for_path(r"C:\shots\m42.fits"))
        self.assertEqual("exif", metadata_provider_id_for_path(r"C:\shots\preview.jpg"))
        self.assertEqual("exif", metadata_provider_id_for_path(r"C:\shots\unknown.xyz"))

    def test_builtin_metadata_provider_registration_is_idempotent(self) -> None:
        metadata_provider_id_for_path(r"C:\shots\preview.jpg")
        first_ids = [provider.provider_id for provider in iter_metadata_providers()]
        metadata_provider_id_for_path(r"C:\shots\preview.jpg")
        second_ids = [provider.provider_id for provider in iter_metadata_providers()]

        self.assertEqual(first_ids, second_ids)
        self.assertEqual(["fits", "exif"], first_ids)

    def test_metadata_provider_override_is_used(self) -> None:
        metadata_provider_id_for_path(r"C:\shots\preview.jpg")

        class _OverrideExifProvider:
            provider_id = "exif"

            def can_handle_metadata(self, request: MetadataLoadRequest) -> bool:
                return True

            def load_metadata(self, request: MetadataLoadRequest) -> object:
                return CaptureMetadata(path=request.path, camera="Override")

        class _RestoreExifProvider:
            provider_id = "exif"

            def can_handle_metadata(self, request: MetadataLoadRequest) -> bool:
                return True

            def load_metadata(self, request: MetadataLoadRequest) -> object:
                return metadata_module._load_exif_capture_metadata(request.path)

        register_metadata_provider(_OverrideExifProvider())
        try:
            metadata = load_capture_metadata(r"C:\shots\preview.jpg")
            self.assertEqual("Override", metadata.camera)
        finally:
            register_metadata_provider(_RestoreExifProvider())

    def test_builtin_review_grouping_provider_registration_is_idempotent(self) -> None:
        records = (ImageRecord(path="C:/shots/frame_01.jpg", name="frame_01.jpg", size=100, modified_ns=1),)

        self.assertEqual("default", review_grouping_provider_id(records))
        first_ids = [provider.provider_id for provider in iter_review_grouping_providers()]
        self.assertEqual("default", review_grouping_provider_id(records))
        second_ids = [provider.provider_id for provider in iter_review_grouping_providers()]

        self.assertEqual(first_ids, second_ids)
        self.assertEqual(["default"], first_ids)

    def test_review_grouping_provider_override_is_used(self) -> None:
        records = [
            ImageRecord(path="C:/shots/frame_01.jpg", name="frame_01.jpg", size=100, modified_ns=1),
            ImageRecord(path="C:/shots/frame_02.jpg", name="frame_02.jpg", size=120, modified_ns=2),
        ]
        review_grouping_provider_id(records)

        class _OverrideReviewProvider:
            provider_id = "default"

            def can_handle_review_grouping(self, request: ReviewGroupingRequest) -> bool:
                return True

            def build_review_intelligence(self, request: ReviewGroupingRequest) -> object:
                group = review_intelligence_module.ReviewGroup(
                    id="override-group",
                    kind="similar",
                    label="Override",
                    member_paths=tuple(record.path for record in request.records),
                )
                insight = ReviewInsight(
                    path=request.records[0].path,
                    group_id=group.id,
                    group_kind=group.kind,
                    group_label=group.label,
                    group_size=len(request.records),
                    rank_in_group=1,
                )
                return ReviewIntelligenceBundle(
                    groups=(group,),
                    insights_by_path={request.records[0].path: insight},
                )

        class _RestoreReviewProvider:
            provider_id = "default"

            def can_handle_review_grouping(self, request: ReviewGroupingRequest) -> bool:
                return True

            def build_review_intelligence(self, request: ReviewGroupingRequest) -> object:
                return review_intelligence_module._build_review_intelligence_builtin(
                    list(request.records),
                    should_cancel=request.should_cancel,
                    progress_callback=request.progress_callback,
                    chunk_callback=request.chunk_callback,
                    cached_fingerprints=request.cached_fingerprints,
                    computed_fingerprint_callback=request.computed_fingerprint_callback,
                )

        register_review_grouping_provider(_OverrideReviewProvider())
        try:
            bundle = build_review_intelligence(records)
            self.assertEqual("Override", bundle.groups[0].label)
            insight = bundle.insight_for_path(records[0].path)
            self.assertIsNotNone(insight)
            assert insight is not None
            self.assertEqual("Override", insight.group_label)
        finally:
            register_review_grouping_provider(_RestoreReviewProvider())

    def test_builtin_review_scoring_provider_registration_is_idempotent(self) -> None:
        records = (ImageRecord(path="C:/shots/frame_01.jpg", name="frame_01.jpg", size=100, modified_ns=1),)

        self.assertEqual("default", review_scoring_provider_id(records, ai_bundle=None, review_bundle=None, correction_events=()))
        first_ids = [provider.provider_id for provider in iter_review_scoring_providers()]
        self.assertEqual("default", review_scoring_provider_id(records, ai_bundle=None, review_bundle=None, correction_events=()))
        second_ids = [provider.provider_id for provider in iter_review_scoring_providers()]

        self.assertEqual(first_ids, second_ids)
        self.assertEqual(["default"], first_ids)

    def test_review_scoring_provider_override_is_used(self) -> None:
        records = [
            ImageRecord(path="C:/shots/frame_01.jpg", name="frame_01.jpg", size=100, modified_ns=1),
            ImageRecord(path="C:/shots/frame_02.jpg", name="frame_02.jpg", size=120, modified_ns=2),
        ]
        review_scoring_provider_id(records, ai_bundle=None, review_bundle=None, correction_events=())

        class _OverrideScoringProvider:
            provider_id = "default"

            def can_handle_review_scoring(self, request: ReviewScoringRequest) -> bool:
                return True

            def build_burst_recommendations(self, request: ReviewScoringRequest) -> object:
                recommendation = BurstRecommendation(
                    path=request.records[0].path,
                    group_id="override-burst",
                    group_label="Override",
                    group_size=len(request.records),
                    recommended_path=request.records[0].path,
                    rank_in_group=1,
                    score=97.0,
                    recommended_score=97.0,
                    is_recommended=True,
                )
                return TasteProfile(summary_lines=("Override scoring.",)), {
                    request.records[0].path: recommendation,
                }

        class _RestoreScoringProvider:
            provider_id = "default"

            def can_handle_review_scoring(self, request: ReviewScoringRequest) -> bool:
                return True

            def build_burst_recommendations(self, request: ReviewScoringRequest) -> object:
                return review_workflows_module._build_burst_recommendations_builtin(
                    list(request.records),
                    ai_bundle=request.ai_bundle if isinstance(request.ai_bundle, AIBundle) or request.ai_bundle is None else None,
                    review_bundle=request.review_bundle,
                    correction_events=request.correction_events,
                    should_cancel=request.should_cancel,
                )

        register_review_scoring_provider(_OverrideScoringProvider())
        try:
            taste_profile, recommendations = build_burst_recommendations(
                records,
                ai_bundle=None,
                review_bundle=None,
                correction_events=(),
            )
            self.assertEqual(("Override scoring.",), taste_profile.summary_lines)
            recommendation = recommendations[records[0].path]
            self.assertEqual("Override", recommendation.group_label)
            self.assertTrue(recommendation.is_recommended)
        finally:
            register_review_scoring_provider(_RestoreScoringProvider())


if __name__ == "__main__":
    unittest.main()
