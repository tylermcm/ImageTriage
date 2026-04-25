from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from image_triage.ai_results import AIBundle, AIConfidenceBucket, AIImageResult, build_ai_bundle_from_results
from image_triage.catalog import CatalogRepository
from image_triage.metadata import CaptureMetadata
from image_triage.models import ImageRecord, ImageVariant, SortMode
from image_triage.review_intelligence import ReviewGroup, ReviewInsight, ReviewIntelligenceBundle, _RecordFingerprint
from image_triage.review_workflows import BurstRecommendation, TasteProfile
from image_triage.scanner import FolderScanTask


def _record(path: str, *, name: str, size: int, modified_ns: int, companion_paths=(), edited_paths=(), variants=()) -> ImageRecord:
    return ImageRecord(
        path=path,
        name=name,
        size=size,
        modified_ns=modified_ns,
        companion_paths=tuple(companion_paths),
        edited_paths=tuple(edited_paths),
        variants=tuple(variants),
    )


class CatalogRepositoryTests(unittest.TestCase):
    def test_save_and_load_folder_records_round_trip(self) -> None:
        with tempfile.TemporaryDirectory(prefix="image_triage_catalog_") as temp_dir:
            db_path = Path(temp_dir) / "catalog.sqlite3"
            repository = CatalogRepository(db_path)
            folder = str(Path(temp_dir) / "shots")
            records = [
                _record(
                    f"{folder}/IMG_0001.CR3",
                    name="IMG_0001.CR3",
                    size=12_345,
                    modified_ns=101,
                    companion_paths=(f"{folder}/IMG_0001.JPG",),
                    edited_paths=(f"{folder}/edit/IMG_0001_1.jpg",),
                    variants=(
                        ImageVariant(path=f"{folder}/IMG_0001.JPG", name="IMG_0001.JPG", size=2_000, modified_ns=100),
                        ImageVariant(path=f"{folder}/edit/IMG_0001_1.jpg", name="IMG_0001_1.jpg", size=2_500, modified_ns=102),
                    ),
                ),
                _record(
                    f"{folder}/m42.fits",
                    name="m42.fits",
                    size=50_000,
                    modified_ns=200,
                ),
            ]

            saved = repository.save_folder_records(folder, records)
            loaded = repository.load_folder_records(folder)

            self.assertTrue(saved)
            self.assertIsNotNone(loaded)
            assert loaded is not None
            self.assertEqual(records, loaded)

    def test_catalog_stats_report_saved_counts(self) -> None:
        with tempfile.TemporaryDirectory(prefix="image_triage_catalog_") as temp_dir:
            db_path = Path(temp_dir) / "catalog.sqlite3"
            repository = CatalogRepository(db_path)
            folder = str(Path(temp_dir) / "shots")
            records = [
                _record(
                    f"{folder}/cached_01.jpg",
                    name="cached_01.jpg",
                    size=123,
                    modified_ns=1,
                ),
                _record(
                    f"{folder}/cached_02.jpg",
                    name="cached_02.jpg",
                    size=456,
                    modified_ns=2,
                ),
            ]

            repository.save_folder_records(folder, records)
            stats = repository.stats()

            self.assertTrue(stats.available)
            self.assertEqual(1, stats.folder_count)
            self.assertEqual(2, stats.record_count)
            self.assertEqual(0, stats.feature_count)
            self.assertEqual(0, stats.ai_cache_count)
            self.assertEqual(0, stats.grouping_cache_count)
            self.assertEqual(0, stats.scoring_cache_count)
            self.assertEqual(db_path, stats.db_path)

    def test_list_folder_paths_returns_indexed_folders(self) -> None:
        with tempfile.TemporaryDirectory(prefix="image_triage_catalog_") as temp_dir:
            db_path = Path(temp_dir) / "catalog.sqlite3"
            repository = CatalogRepository(db_path)
            folder_a = str(Path(temp_dir) / "folder_a")
            folder_b = str(Path(temp_dir) / "folder_b")
            repository.save_folder_records(folder_a, [])
            repository.save_folder_records(folder_b, [])

            folder_paths = repository.list_folder_paths()

            self.assertIn(folder_a, folder_paths)
            self.assertIn(folder_b, folder_paths)

    def test_folder_scan_task_uses_catalog_cache_when_enabled(self) -> None:
        with tempfile.TemporaryDirectory(prefix="image_triage_catalog_") as temp_dir:
            folder_path = Path(temp_dir) / "shots"
            folder_path.mkdir(parents=True, exist_ok=True)
            db_path = Path(temp_dir) / "catalog.sqlite3"
            repository = CatalogRepository(db_path)
            expected_records = [
                _record(
                    str(folder_path / "cached_01.jpg"),
                    name="cached_01.jpg",
                    size=123,
                    modified_ns=1,
                )
            ]
            repository.save_folder_records(str(folder_path), expected_records)
            task = FolderScanTask(str(folder_path), token=7, sort_mode=SortMode.NAME, prefer_cached_only=True)
            finished_payloads: list[tuple[list[ImageRecord], str]] = []
            task.signals.finished.connect(
                lambda _folder, _token, records, source: finished_payloads.append((list(records), source))
            )

            with patch.object(FolderScanTask, "_catalog", repository), patch.dict(
                os.environ,
                {"IMAGE_TRIAGE_USE_CATALOG_CACHE": "1"},
                clear=False,
            ):
                task.run()

            self.assertEqual([(expected_records, "catalog")], finished_payloads)

    def test_folder_scan_task_can_bypass_catalog_cache_reads(self) -> None:
        with tempfile.TemporaryDirectory(prefix="image_triage_catalog_") as temp_dir:
            folder_path = Path(temp_dir) / "shots"
            folder_path.mkdir(parents=True, exist_ok=True)
            db_path = Path(temp_dir) / "catalog.sqlite3"
            repository = CatalogRepository(db_path)
            cached_records = [
                _record(
                    str(folder_path / "cached_01.jpg"),
                    name="cached_01.jpg",
                    size=321,
                    modified_ns=4,
                )
            ]
            live_records = [
                _record(
                    str(folder_path / "live_01.jpg"),
                    name="live_01.jpg",
                    size=654,
                    modified_ns=5,
                )
            ]
            repository.save_folder_records(str(folder_path), cached_records)
            task = FolderScanTask(
                str(folder_path),
                token=9,
                sort_mode=SortMode.NAME,
                prefer_cached_only=True,
                read_cached_records=False,
            )
            finished_payloads: list[tuple[list[ImageRecord], str]] = []
            task.signals.finished.connect(
                lambda _folder, _token, records, source: finished_payloads.append((list(records), source))
            )

            with patch.object(FolderScanTask, "_catalog", repository), patch(
                "image_triage.scanner.scan_folder",
                return_value=live_records,
            ):
                task.run()

            self.assertEqual([(live_records, "live")], finished_payloads)
            self.assertEqual(live_records, repository.load_folder_records(str(folder_path)))

    def test_save_and_load_review_scoring_round_trip(self) -> None:
        with tempfile.TemporaryDirectory(prefix="image_triage_catalog_") as temp_dir:
            db_path = Path(temp_dir) / "catalog.sqlite3"
            repository = CatalogRepository(db_path)
            folder = str(Path(temp_dir) / "shots")
            records = [
                _record(
                    f"{folder}/frame_01.jpg",
                    name="frame_01.jpg",
                    size=12_345,
                    modified_ns=101,
                ),
                _record(
                    f"{folder}/frame_02.jpg",
                    name="frame_02.jpg",
                    size=12_346,
                    modified_ns=102,
                ),
            ]
            repository.save_folder_records(folder, records)
            taste_profile = TasteProfile(
                event_count=2,
                detail_bias=0.18,
                ai_alignment_bias=-0.07,
                summary_lines=("Recent picks lean toward crisper detail.",),
            )
            leader = BurstRecommendation(
                path=records[0].path,
                group_id="burst-1",
                group_label="Burst",
                group_size=2,
                recommended_path=records[0].path,
                rank_in_group=1,
                score=96.0,
                recommended_score=96.0,
                is_recommended=True,
                reasons=("Best overall signal inside this burst.",),
            )
            runner_up = BurstRecommendation(
                path=records[1].path,
                group_id="burst-1",
                group_label="Burst",
                group_size=2,
                recommended_path=records[0].path,
                rank_in_group=2,
                score=82.0,
                recommended_score=96.0,
                is_recommended=False,
                reasons=("The current burst leader looks stronger overall.",),
            )

            saved = repository.save_review_scoring(
                folder,
                session_id="LinkFlow",
                cache_key="cache-123",
                provider_id="default",
                records=records,
                taste_profile=taste_profile,
                recommendations={
                    records[0].path: leader,
                    records[1].path: runner_up,
                },
            )
            loaded = repository.load_review_scoring(folder, session_id="LinkFlow", cache_key="cache-123")

            self.assertTrue(saved)
            self.assertIsNotNone(loaded)
            assert loaded is not None
            self.assertEqual("default", loaded.provider_id)
            self.assertEqual(taste_profile, loaded.taste_profile)
            self.assertEqual(leader, loaded.recommendations[records[0].path])
            self.assertEqual(runner_up, loaded.recommendations[records[1].path])

    def test_load_review_scoring_requires_matching_cache_key(self) -> None:
        with tempfile.TemporaryDirectory(prefix="image_triage_catalog_") as temp_dir:
            db_path = Path(temp_dir) / "catalog.sqlite3"
            repository = CatalogRepository(db_path)
            folder = str(Path(temp_dir) / "shots")
            records = [
                _record(
                    f"{folder}/frame_01.jpg",
                    name="frame_01.jpg",
                    size=500,
                    modified_ns=10,
                )
            ]
            repository.save_folder_records(folder, records)
            repository.save_review_scoring(
                folder,
                session_id="LinkFlow",
                cache_key="cache-a",
                provider_id="default",
                records=records,
                taste_profile=TasteProfile(),
                recommendations={},
            )

            loaded = repository.load_review_scoring(folder, session_id="LinkFlow", cache_key="cache-b")

            self.assertIsNone(loaded)

    def test_save_and_load_review_grouping_round_trip(self) -> None:
        with tempfile.TemporaryDirectory(prefix="image_triage_catalog_") as temp_dir:
            db_path = Path(temp_dir) / "catalog.sqlite3"
            repository = CatalogRepository(db_path)
            folder = str(Path(temp_dir) / "shots")
            records = [
                _record(
                    f"{folder}/frame_01.jpg",
                    name="frame_01.jpg",
                    size=800,
                    modified_ns=21,
                ),
                _record(
                    f"{folder}/frame_02.jpg",
                    name="frame_02.jpg",
                    size=801,
                    modified_ns=22,
                ),
            ]
            repository.save_folder_records(folder, records)
            group = ReviewGroup(
                id="review-1",
                kind="similar",
                label="Similar",
                member_paths=(records[0].path, records[1].path),
                reasons=("Strong visual match inside the same local capture sequence.",),
            )
            insight_a = ReviewInsight(
                path=records[0].path,
                group_id=group.id,
                group_kind=group.kind,
                group_label=group.label,
                group_size=2,
                rank_in_group=1,
                reasons=group.reasons,
                detail_score=88.0,
                exposure_score=72.0,
            )
            insight_b = ReviewInsight(
                path=records[1].path,
                group_id=group.id,
                group_kind=group.kind,
                group_label=group.label,
                group_size=2,
                rank_in_group=2,
                reasons=group.reasons,
                detail_score=67.0,
                exposure_score=65.0,
            )
            bundle = ReviewIntelligenceBundle(
                groups=(group,),
                insights_by_path={
                    records[0].path: insight_a,
                    records[1].path: insight_b,
                },
            )

            saved = repository.save_review_grouping(
                folder,
                cache_key="group-cache-1",
                provider_id="default",
                bundle=bundle,
            )
            loaded = repository.load_review_grouping(folder, cache_key="group-cache-1")

            self.assertTrue(saved)
            self.assertIsNotNone(loaded)
            assert loaded is not None
            self.assertEqual("default", loaded.provider_id)
            self.assertEqual(group, loaded.bundle.groups[0])
            self.assertEqual(insight_a, loaded.bundle.insight_for_path(records[0].path))
            self.assertEqual(insight_b, loaded.bundle.insight_for_path(records[1].path))

    def test_load_review_grouping_requires_matching_cache_key(self) -> None:
        with tempfile.TemporaryDirectory(prefix="image_triage_catalog_") as temp_dir:
            db_path = Path(temp_dir) / "catalog.sqlite3"
            repository = CatalogRepository(db_path)
            folder = str(Path(temp_dir) / "shots")
            records = [
                _record(
                    f"{folder}/frame_01.jpg",
                    name="frame_01.jpg",
                    size=900,
                    modified_ns=31,
                )
            ]
            repository.save_folder_records(folder, records)
            repository.save_review_grouping(
                folder,
                cache_key="group-cache-a",
                provider_id="default",
                bundle=ReviewIntelligenceBundle(groups=(), insights_by_path={}),
            )

            loaded = repository.load_review_grouping(folder, cache_key="group-cache-b")

            self.assertIsNone(loaded)

    def test_save_and_load_review_feature_round_trip(self) -> None:
        with tempfile.TemporaryDirectory(prefix="image_triage_catalog_") as temp_dir:
            db_path = Path(temp_dir) / "catalog.sqlite3"
            repository = CatalogRepository(db_path)
            folder = str(Path(temp_dir) / "shots")
            records = [
                _record(
                    f"{folder}/frame_01.jpg",
                    name="frame_01.jpg",
                    size=1_024,
                    modified_ns=55,
                ),
                _record(
                    f"{folder}/frame_02.jpg",
                    name="frame_02.jpg",
                    size=2_048,
                    modified_ns=56,
                ),
            ]
            repository.save_folder_records(folder, records)
            fingerprint = _RecordFingerprint(
                record=records[0],
                source_path=records[0].path,
                metadata=CaptureMetadata(
                    path=records[0].path,
                    camera="Test Cam",
                    exposure="1/250s",
                    iso="ISO 200",
                    width=6_000,
                    height=4_000,
                ),
                dhash=0xF0F0F0F0F0F0F0F0,
                avg_luma=97.25,
                width=6_000,
                height=4_000,
                sha1_digest="abc123",
                detail_score=88.4,
                exposure_score=72.1,
            )

            saved = repository.save_review_features(
                folder,
                cache_keys={
                    records[0].path: "feature-cache-1",
                    records[1].path: "feature-cache-2",
                },
                fingerprints=[fingerprint],
            )
            loaded = repository.load_review_features(
                folder,
                records=records,
                cache_keys={
                    records[0].path: "feature-cache-1",
                    records[1].path: "feature-cache-2",
                },
            )

            self.assertTrue(saved)
            self.assertEqual(fingerprint, loaded[records[0].path])
            self.assertNotIn(records[1].path, loaded)
            stats = repository.stats()
            self.assertEqual(1, stats.feature_count)

    def test_save_and_load_ai_bundle_round_trip(self) -> None:
        with tempfile.TemporaryDirectory(prefix="image_triage_catalog_") as temp_dir:
            db_path = Path(temp_dir) / "catalog.sqlite3"
            repository = CatalogRepository(db_path)
            folder = str(Path(temp_dir) / "shots")
            records = [
                _record(
                    f"{folder}/frame_01.jpg",
                    name="frame_01.jpg",
                    size=1_024,
                    modified_ns=1,
                ),
                _record(
                    f"{folder}/frame_02.jpg",
                    name="frame_02.jpg",
                    size=2_048,
                    modified_ns=2,
                ),
            ]
            repository.save_folder_records(folder, records)
            results = [
                AIImageResult(
                    image_id="frame_01",
                    file_path=records[0].path,
                    file_name=records[0].name,
                    group_id="group-a",
                    group_size=2,
                    rank_in_group=1,
                    score=0.95,
                    cluster_reason="Best expression",
                    normalized_score=100.0,
                    folder_percentile=100.0,
                    score_gap_to_next=0.25,
                    score_gap_to_top=0.0,
                    confidence_bucket=AIConfidenceBucket.OBVIOUS_WINNER,
                    confidence_summary="Clear lead inside its AI group.",
                ),
                AIImageResult(
                    image_id="frame_02",
                    file_path=records[1].path,
                    file_name=records[1].name,
                    group_id="group-a",
                    group_size=2,
                    rank_in_group=2,
                    score=0.70,
                    cluster_reason="Slightly weaker",
                    normalized_score=0.0,
                    folder_percentile=50.0,
                    score_gap_to_next=None,
                    score_gap_to_top=0.25,
                    confidence_bucket=AIConfidenceBucket.NEEDS_REVIEW,
                    confidence_summary="Model signals are mixed enough to warrant a human pass.",
                ),
            ]
            bundle = build_ai_bundle_from_results(
                source_path=Path(temp_dir) / "report_dir",
                export_csv_path=Path(temp_dir) / "report_dir" / "ranked_clusters_export.csv",
                summary_json_path=Path(temp_dir) / "report_dir" / "ranked_export_summary.json",
                report_html_path=Path(temp_dir) / "report_dir" / "ranked_clusters_report.html",
                results=results,
                summary={"model": "test-ranker"},
            )

            saved = repository.save_ai_bundle(
                folder,
                cache_key="ai-cache-1",
                bundle=bundle,
            )
            loaded = repository.load_ai_bundle(folder, cache_key="ai-cache-1")
            stats = repository.stats()

            self.assertTrue(saved)
            self.assertIsNotNone(loaded)
            assert loaded is not None
            self.assertEqual("ai-cache-1", loaded.cache_key)
            self.assertEqual(bundle.export_csv_path, loaded.bundle.export_csv_path)
            self.assertEqual(bundle.summary, loaded.bundle.summary)
            self.assertEqual(bundle.result_for_path(records[0].path), loaded.bundle.result_for_path(records[0].path))
            self.assertEqual(bundle.result_for_path(records[1].path), loaded.bundle.result_for_path(records[1].path))
            self.assertEqual(1, stats.ai_cache_count)

    def test_load_ai_bundle_requires_matching_cache_key(self) -> None:
        with tempfile.TemporaryDirectory(prefix="image_triage_catalog_") as temp_dir:
            db_path = Path(temp_dir) / "catalog.sqlite3"
            repository = CatalogRepository(db_path)
            folder = str(Path(temp_dir) / "shots")
            record = _record(
                f"{folder}/frame_01.jpg",
                name="frame_01.jpg",
                size=1_024,
                modified_ns=1,
            )
            repository.save_folder_records(folder, [record])
            bundle = AIBundle(
                source_path=str(Path(temp_dir) / "report_dir"),
                export_csv_path=str(Path(temp_dir) / "report_dir" / "ranked_clusters_export.csv"),
                results_by_path={},
                summary={},
            )
            repository.save_ai_bundle(folder, cache_key="ai-cache-a", bundle=bundle)

            loaded = repository.load_ai_bundle(folder, cache_key="ai-cache-b")

            self.assertIsNone(loaded)

    def test_save_and_load_ai_workflow_cache_round_trip(self) -> None:
        with tempfile.TemporaryDirectory(prefix="image_triage_catalog_") as temp_dir:
            db_path = Path(temp_dir) / "catalog.sqlite3"
            repository = CatalogRepository(db_path)
            folder = str(Path(temp_dir) / "shots")
            record = _record(
                f"{folder}/frame_01.jpg",
                name="frame_01.jpg",
                size=1_024,
                modified_ns=1,
            )
            repository.save_folder_records(folder, [record])

            saved = repository.save_ai_workflow_cache(
                folder,
                embedding_cache_key="embed-1",
                cluster_cache_key="cluster-1",
                report_cache_key="report-1",
                artifacts_dir=str(Path(folder) / ".image_triage_ai" / "artifacts"),
                report_dir=str(Path(folder) / ".image_triage_ai" / "ranker_report"),
            )
            loaded = repository.load_ai_workflow_cache(folder)

            self.assertTrue(saved)
            self.assertIsNotNone(loaded)
            assert loaded is not None
            self.assertEqual("embed-1", loaded.embedding_cache_key)
            self.assertEqual("cluster-1", loaded.cluster_cache_key)
            self.assertEqual("report-1", loaded.report_cache_key)


if __name__ == "__main__":
    unittest.main()
