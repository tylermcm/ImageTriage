from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from image_triage.catalog import CatalogRepository
from image_triage.models import ImageRecord, ImageVariant, SortMode
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
            self.assertEqual(db_path, stats.db_path)

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


if __name__ == "__main__":
    unittest.main()
