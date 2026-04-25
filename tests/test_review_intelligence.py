from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from image_triage.catalog import CatalogRepository
from image_triage.metadata import CaptureMetadata
from image_triage.models import ImageRecord
from image_triage.review_intelligence import (
    BuildReviewIntelligenceTask,
    ReviewIntelligenceCancelled,
    ReviewGroup,
    ReviewInsight,
    ReviewIntelligenceBundle,
    _RecordFingerprint,
    build_review_feature_cache_key,
    build_review_grouping_cache_key,
    build_review_intelligence,
)


class ReviewIntelligenceTests(unittest.TestCase):
    def test_exact_duplicate_records_are_grouped(self) -> None:
        records = [
            ImageRecord(path="C:/shots/a.raw", name="a.raw", size=1024, modified_ns=1),
            ImageRecord(path="C:/shots/b.raw", name="b.raw", size=1024, modified_ns=2),
            ImageRecord(path="C:/shots/c.raw", name="c.raw", size=900, modified_ns=3),
        ]
        fingerprints = {
            records[0].path: _RecordFingerprint(
                record=records[0],
                source_path=records[0].path,
                metadata=CaptureMetadata(path=records[0].path, width=4000, height=3000),
                dhash=None,
                avg_luma=0.0,
                width=4000,
                height=3000,
                sha1_digest="match",
            ),
            records[1].path: _RecordFingerprint(
                record=records[1],
                source_path=records[1].path,
                metadata=CaptureMetadata(path=records[1].path, width=4000, height=3000),
                dhash=None,
                avg_luma=0.0,
                width=4000,
                height=3000,
                sha1_digest="match",
            ),
            records[2].path: _RecordFingerprint(
                record=records[2],
                source_path=records[2].path,
                metadata=CaptureMetadata(path=records[2].path, width=4000, height=3000),
                dhash=None,
                avg_luma=0.0,
                width=4000,
                height=3000,
                sha1_digest="different",
            ),
        }

        with patch(
            "image_triage.review_intelligence._build_fingerprint",
            side_effect=lambda record, _metadata_cache: fingerprints[record.path],
        ):
            bundle = build_review_intelligence(records)

        group = bundle.groups[0]
        insight_a = bundle.insight_for_path(records[0].path)
        insight_b = bundle.insight_for_path(records[1].path)
        insight_c = bundle.insight_for_path(records[2].path)

        self.assertEqual(len(bundle.groups), 1)
        self.assertEqual(group.kind, "exact_duplicate")
        self.assertEqual(group.member_paths, (records[0].path, records[1].path))
        self.assertIsNotNone(insight_a)
        self.assertIsNotNone(insight_b)
        assert insight_a is not None
        assert insight_b is not None
        self.assertTrue(insight_a.is_exact_duplicate)
        self.assertEqual(insight_a.group_label, "Exact Dup")
        self.assertEqual(insight_b.rank_text, "2/2")
        self.assertIsNone(insight_c)

    def test_likely_duplicate_frames_get_near_duplicate_label(self) -> None:
        records = [
            ImageRecord(path="C:/shots/frame_01.jpg", name="frame_01.jpg", size=1500, modified_ns=1),
            ImageRecord(path="C:/shots/frame_02.jpg", name="frame_02.jpg", size=1510, modified_ns=2),
        ]
        fingerprints = {
            records[0].path: _RecordFingerprint(
                record=records[0],
                source_path=records[0].path,
                metadata=CaptureMetadata(path=records[0].path, width=4000, height=3000),
                dhash=0b1111000011110000,
                avg_luma=92.0,
                width=4000,
                height=3000,
                sha1_digest="a",
            ),
            records[1].path: _RecordFingerprint(
                record=records[1],
                source_path=records[1].path,
                metadata=CaptureMetadata(path=records[1].path, width=4000, height=3000),
                dhash=0b1111000011110001,
                avg_luma=95.0,
                width=4000,
                height=3000,
                sha1_digest="b",
            ),
        }

        with patch(
            "image_triage.review_intelligence._build_fingerprint",
            side_effect=lambda record, _metadata_cache: fingerprints[record.path],
        ):
            bundle = build_review_intelligence(records)

        self.assertEqual(len(bundle.groups), 1)
        self.assertEqual(bundle.groups[0].kind, "likely_duplicate")
        self.assertEqual(bundle.groups[0].label, "Near Dup")
        self.assertIn("Very small visual difference", bundle.groups[0].reasons[0])

        insight = bundle.insight_for_path(records[0].path)
        self.assertIsNotNone(insight)
        assert insight is not None
        self.assertTrue(insight.is_likely_duplicate)
        self.assertEqual(insight.summary_text, "Near Dup 1/2")

    def test_fits_records_are_excluded_from_near_duplicate_groups(self) -> None:
        records = [
            ImageRecord(path="C:/astro/frame_01.fits", name="frame_01.fits", size=1500, modified_ns=1),
            ImageRecord(path="C:/astro/frame_02.fits", name="frame_02.fits", size=1510, modified_ns=2),
        ]
        captured_at = datetime(2022, 8, 24, 23, 23, 26)
        fingerprints = {
            records[0].path: _RecordFingerprint(
                record=records[0],
                source_path=records[0].path,
                metadata=CaptureMetadata(path=records[0].path, width=3008, height=3008, captured_at_value=captured_at),
                dhash=0b1111000011110000,
                avg_luma=92.0,
                width=3008,
                height=3008,
                sha1_digest="a",
            ),
            records[1].path: _RecordFingerprint(
                record=records[1],
                source_path=records[1].path,
                metadata=CaptureMetadata(path=records[1].path, width=3008, height=3008, captured_at_value=captured_at),
                dhash=0b1111000011110001,
                avg_luma=95.0,
                width=3008,
                height=3008,
                sha1_digest="b",
            ),
        }

        with patch(
            "image_triage.review_intelligence._build_fingerprint",
            side_effect=lambda record, _metadata_cache: fingerprints[record.path],
        ):
            bundle = build_review_intelligence(records)

        self.assertEqual(bundle.groups, ())
        self.assertIsNone(bundle.insight_for_path(records[0].path))
        self.assertIsNone(bundle.insight_for_path(records[1].path))

    def test_build_review_intelligence_emits_progress_milestones(self) -> None:
        records = [
            ImageRecord(path=f"C:/shots/frame_{index:03d}.jpg", name=f"frame_{index:03d}.jpg", size=1000 + index, modified_ns=index)
            for index in range(1, 86)
        ]
        emitted: list[tuple[int, int]] = []

        def _fingerprint(record: ImageRecord, _metadata_cache: dict[str, CaptureMetadata]) -> _RecordFingerprint:
            metadata = CaptureMetadata(path=record.path, width=4000, height=3000)
            return _RecordFingerprint(
                record=record,
                source_path=record.path,
                metadata=metadata,
                dhash=(record.modified_ns % 16),
                avg_luma=90.0,
                width=4000,
                height=3000,
                sha1_digest=f"sha-{record.modified_ns}",
            )

        with patch("image_triage.review_intelligence._build_fingerprint", side_effect=_fingerprint):
            build_review_intelligence(records, progress_callback=lambda current, total: emitted.append((current, total)))

        self.assertIn((0, 85), emitted)
        self.assertIn((1, 85), emitted)
        self.assertIn((40, 85), emitted)
        self.assertIn((80, 85), emitted)
        self.assertIn((85, 85), emitted)
        self.assertEqual(emitted[-1], (85, 85))

    def test_build_review_intelligence_raises_when_cancelled(self) -> None:
        records = [
            ImageRecord(path=f"C:/shots/frame_{index:03d}.jpg", name=f"frame_{index:03d}.jpg", size=1000 + index, modified_ns=index)
            for index in range(1, 6)
        ]
        checks = {"count": 0}

        def _should_cancel() -> bool:
            checks["count"] += 1
            return checks["count"] >= 2

        with self.assertRaises(ReviewIntelligenceCancelled):
            build_review_intelligence(records, should_cancel=_should_cancel)

    def test_build_review_intelligence_emits_chunk_payloads(self) -> None:
        records = [
            ImageRecord(path="C:/shots/frame_01.jpg", name="frame_01.jpg", size=1500, modified_ns=1),
            ImageRecord(path="C:/shots/frame_02.jpg", name="frame_02.jpg", size=1510, modified_ns=2),
        ]
        fingerprints = {
            records[0].path: _RecordFingerprint(
                record=records[0],
                source_path=records[0].path,
                metadata=CaptureMetadata(path=records[0].path, width=4000, height=3000),
                dhash=0b1111000011110000,
                avg_luma=92.0,
                width=4000,
                height=3000,
                sha1_digest="a",
            ),
            records[1].path: _RecordFingerprint(
                record=records[1],
                source_path=records[1].path,
                metadata=CaptureMetadata(path=records[1].path, width=4000, height=3000),
                dhash=0b1111000011110001,
                avg_luma=95.0,
                width=4000,
                height=3000,
                sha1_digest="b",
            ),
        }
        emitted_chunks: list[tuple[tuple[object, ...], dict[str, object]]] = []
        with patch(
            "image_triage.review_intelligence._build_fingerprint",
            side_effect=lambda record, _metadata_cache: fingerprints[record.path],
        ):
            build_review_intelligence(
                records,
                chunk_callback=lambda groups, insights: emitted_chunks.append((groups, insights)),
            )

        self.assertTrue(emitted_chunks)
        groups, insights = emitted_chunks[-1]
        self.assertTrue(groups)
        self.assertIn(records[0].path, insights)
        self.assertIn(records[1].path, insights)

    def test_build_review_task_emits_cancelled_signal(self) -> None:
        task = BuildReviewIntelligenceTask(
            folder="C:/shots",
            token=17,
            records=(ImageRecord(path="C:/shots/a.jpg", name="a.jpg", size=100, modified_ns=1),),
        )
        cancelled_events: list[tuple[str, int]] = []
        finished_events: list[tuple[str, int]] = []
        task.signals.cancelled.connect(lambda folder, token: cancelled_events.append((folder, token)))
        task.signals.finished.connect(lambda folder, token, _bundle: finished_events.append((folder, token)))

        task.cancel()
        task.run()

        self.assertEqual(cancelled_events, [("C:/shots", 17)])
        self.assertEqual(finished_events, [])

    def test_build_review_task_uses_cached_grouping_when_available(self) -> None:
        with tempfile.TemporaryDirectory(prefix="image_triage_review_cache_") as temp_dir:
            db_path = Path(temp_dir) / "catalog.sqlite3"
            folder = str(Path(temp_dir) / "shots")
            records = (
                ImageRecord(path=f"{folder}/frame_01.jpg", name="frame_01.jpg", size=100, modified_ns=1),
                ImageRecord(path=f"{folder}/frame_02.jpg", name="frame_02.jpg", size=120, modified_ns=2),
            )
            repository = CatalogRepository(db_path)
            repository.save_folder_records(folder, list(records))
            cached_group = ReviewGroup(
                id="review-1",
                kind="similar",
                label="Similar",
                member_paths=(records[0].path, records[1].path),
                reasons=("Cached grouping.",),
            )
            cached_insight = ReviewInsight(
                path=records[0].path,
                group_id=cached_group.id,
                group_kind=cached_group.kind,
                group_label=cached_group.label,
                group_size=2,
                rank_in_group=1,
                reasons=cached_group.reasons,
                detail_score=84.0,
                exposure_score=66.0,
            )
            cached_bundle = ReviewIntelligenceBundle(
                groups=(cached_group,),
                insights_by_path={
                    records[0].path: cached_insight,
                },
            )
            repository.save_review_grouping(
                folder,
                cache_key=build_review_grouping_cache_key(records),
                provider_id="default",
                bundle=cached_bundle,
            )
            task = BuildReviewIntelligenceTask(
                folder=folder,
                token=21,
                records=records,
                folder_path=folder,
                catalog_db_path=db_path,
            )
            finished_payloads: list[ReviewIntelligenceBundle] = []
            cache_status_payloads: list[dict[str, object]] = []
            task.signals.finished.connect(lambda _folder, _token, bundle: finished_payloads.append(bundle))
            task.signals.cache_status.connect(lambda _folder, _token, payload: cache_status_payloads.append(payload))

            with patch(
                "image_triage.review_intelligence.build_review_intelligence",
                side_effect=AssertionError("expected cached grouping"),
            ):
                task.run()

            self.assertEqual(1, len(finished_payloads))
            self.assertEqual(cached_group, finished_payloads[0].groups[0])
            self.assertEqual(cached_insight, finished_payloads[0].insight_for_path(records[0].path))
            self.assertEqual("catalog", cache_status_payloads[0]["grouping_source"])
            self.assertEqual("skipped", cache_status_payloads[0]["feature_source"])

    def test_build_review_task_persists_grouping_after_compute(self) -> None:
        with tempfile.TemporaryDirectory(prefix="image_triage_review_cache_") as temp_dir:
            db_path = Path(temp_dir) / "catalog.sqlite3"
            folder = str(Path(temp_dir) / "shots")
            records = (
                ImageRecord(path=f"{folder}/frame_01.jpg", name="frame_01.jpg", size=100, modified_ns=1),
                ImageRecord(path=f"{folder}/frame_02.jpg", name="frame_02.jpg", size=120, modified_ns=2),
            )
            repository = CatalogRepository(db_path)
            repository.save_folder_records(folder, list(records))
            computed_group = ReviewGroup(
                id="review-2",
                kind="likely_duplicate",
                label="Near Dup",
                member_paths=(records[0].path, records[1].path),
                reasons=("Computed grouping.",),
            )
            computed_insight_a = ReviewInsight(
                path=records[0].path,
                group_id=computed_group.id,
                group_kind=computed_group.kind,
                group_label=computed_group.label,
                group_size=2,
                rank_in_group=1,
                reasons=computed_group.reasons,
                detail_score=91.0,
                exposure_score=74.0,
            )
            computed_insight_b = ReviewInsight(
                path=records[1].path,
                group_id=computed_group.id,
                group_kind=computed_group.kind,
                group_label=computed_group.label,
                group_size=2,
                rank_in_group=2,
                reasons=computed_group.reasons,
                detail_score=79.0,
                exposure_score=63.0,
            )
            computed_bundle = ReviewIntelligenceBundle(
                groups=(computed_group,),
                insights_by_path={
                    records[0].path: computed_insight_a,
                    records[1].path: computed_insight_b,
                },
            )
            task = BuildReviewIntelligenceTask(
                folder=folder,
                token=22,
                records=records,
                folder_path=folder,
                catalog_db_path=db_path,
            )
            cache_status_payloads: list[dict[str, object]] = []
            task.signals.cache_status.connect(lambda _folder, _token, payload: cache_status_payloads.append(payload))

            with patch(
                "image_triage.review_intelligence.build_review_intelligence",
                return_value=computed_bundle,
            ):
                task.run()

            loaded = repository.load_review_grouping(folder, cache_key=build_review_grouping_cache_key(records))

            self.assertIsNotNone(loaded)
            assert loaded is not None
            self.assertEqual(computed_group, loaded.bundle.groups[0])
            self.assertEqual(computed_insight_a, loaded.bundle.insight_for_path(records[0].path))
            self.assertEqual(computed_insight_b, loaded.bundle.insight_for_path(records[1].path))
            self.assertEqual("live", cache_status_payloads[0]["grouping_source"])

    def test_build_review_task_uses_cached_features_before_compute(self) -> None:
        with tempfile.TemporaryDirectory(prefix="image_triage_review_cache_") as temp_dir:
            db_path = Path(temp_dir) / "catalog.sqlite3"
            folder = str(Path(temp_dir) / "shots")
            records = (
                ImageRecord(path=f"{folder}/frame_01.jpg", name="frame_01.jpg", size=100, modified_ns=1),
            )
            repository = CatalogRepository(db_path)
            repository.save_folder_records(folder, list(records))
            cached_fingerprint = _RecordFingerprint(
                record=records[0],
                source_path=records[0].path,
                metadata=CaptureMetadata(path=records[0].path, width=4000, height=3000),
                dhash=0xAAAA,
                avg_luma=90.0,
                width=4000,
                height=3000,
                sha1_digest="cached",
                detail_score=54.0,
                exposure_score=61.0,
            )
            repository.save_review_features(
                folder,
                cache_keys={records[0].path: build_review_feature_cache_key(records[0])},
                fingerprints=[cached_fingerprint],
            )
            task = BuildReviewIntelligenceTask(
                folder=folder,
                token=23,
                records=records,
                folder_path=folder,
                catalog_db_path=db_path,
            )
            finished_payloads: list[ReviewIntelligenceBundle] = []
            cache_status_payloads: list[dict[str, object]] = []
            task.signals.finished.connect(lambda _folder, _token, bundle: finished_payloads.append(bundle))
            task.signals.cache_status.connect(lambda _folder, _token, payload: cache_status_payloads.append(payload))

            with patch(
                "image_triage.review_intelligence._build_fingerprint",
                side_effect=AssertionError("expected cached review features"),
            ):
                task.run()

            self.assertEqual(1, len(finished_payloads))
            self.assertEqual((), finished_payloads[0].groups)
            self.assertEqual("catalog", cache_status_payloads[0]["feature_source"])

    def test_build_review_task_persists_features_after_compute(self) -> None:
        with tempfile.TemporaryDirectory(prefix="image_triage_review_cache_") as temp_dir:
            db_path = Path(temp_dir) / "catalog.sqlite3"
            folder = str(Path(temp_dir) / "shots")
            records = (
                ImageRecord(path=f"{folder}/frame_01.jpg", name="frame_01.jpg", size=100, modified_ns=1),
            )
            repository = CatalogRepository(db_path)
            repository.save_folder_records(folder, list(records))
            computed_fingerprint = _RecordFingerprint(
                record=records[0],
                source_path=records[0].path,
                metadata=CaptureMetadata(path=records[0].path, width=3200, height=2400),
                dhash=0x1234,
                avg_luma=72.0,
                width=3200,
                height=2400,
                sha1_digest="persisted",
                detail_score=48.0,
                exposure_score=53.0,
            )
            task = BuildReviewIntelligenceTask(
                folder=folder,
                token=24,
                records=records,
                folder_path=folder,
                catalog_db_path=db_path,
            )
            cache_status_payloads: list[dict[str, object]] = []
            task.signals.cache_status.connect(lambda _folder, _token, payload: cache_status_payloads.append(payload))

            with patch(
                "image_triage.review_intelligence._build_fingerprint",
                return_value=computed_fingerprint,
            ):
                task.run()

            loaded = repository.load_review_features(
                folder,
                records=records,
                cache_keys={records[0].path: build_review_feature_cache_key(records[0])},
            )

            self.assertEqual(computed_fingerprint, loaded[records[0].path])
            self.assertEqual("live", cache_status_payloads[0]["feature_source"])


if __name__ == "__main__":
    unittest.main()
