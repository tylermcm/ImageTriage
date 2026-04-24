from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from image_triage.catalog import CatalogRepository
from image_triage.models import ImageRecord
from image_triage.window import MainWindow


def _record(path: str, *, name: str, size: int, modified_ns: int) -> ImageRecord:
    return ImageRecord(
        path=path,
        name=name,
        size=size,
        modified_ns=modified_ns,
    )


class _WindowCacheStub:
    def __init__(self, repository: CatalogRepository) -> None:
        self._catalog_repository = repository


class _WindowRebuildStub:
    def __init__(self, folder: str = "") -> None:
        self._scope_kind = "folder" if folder else "collection"
        self._current_folder = folder
        self.status_messages: list[str] = []
        self.load_calls: list[tuple[str, bool, bool]] = []

    def statusBar(self):
        return self

    def showMessage(self, message: str) -> None:
        self.status_messages.append(message)

    def _load_folder(
        self,
        folder: str,
        *,
        force_refresh: bool = False,
        chunked_restore: bool = False,
        bypass_catalog_cache: bool = False,
    ) -> None:
        self.load_calls.append((folder, force_refresh, bypass_catalog_cache))


class WindowCatalogCacheTests(unittest.TestCase):
    def test_load_cached_folder_records_uses_catalog_only(self) -> None:
        with tempfile.TemporaryDirectory(prefix="image_triage_window_cache_") as temp_dir:
            db_path = Path(temp_dir) / "catalog.sqlite3"
            folder = str(Path(temp_dir) / "shots")
            records = [
                _record(
                    f"{folder}/cached_01.jpg",
                    name="cached_01.jpg",
                    size=123,
                    modified_ns=1,
                )
            ]
            repository = CatalogRepository(db_path)
            repository.save_folder_records(folder, records)
            window = _WindowCacheStub(repository)

            loaded_records, source = MainWindow._load_cached_folder_records(window, folder)

            self.assertEqual(records, loaded_records)
            self.assertEqual("catalog", source)

    def test_persist_folder_record_cache_updates_catalog(self) -> None:
        with tempfile.TemporaryDirectory(prefix="image_triage_window_cache_") as temp_dir:
            db_path = Path(temp_dir) / "catalog.sqlite3"
            folder = str(Path(temp_dir) / "shots")
            records = [
                _record(
                    f"{folder}/fresh_01.jpg",
                    name="fresh_01.jpg",
                    size=456,
                    modified_ns=2,
                )
            ]
            window = _WindowCacheStub(CatalogRepository(db_path))

            MainWindow._persist_folder_record_cache(window, folder, records, source="test-save")

            self.assertEqual(records, window._catalog_repository.load_folder_records(folder))

    def test_rebuild_current_folder_catalog_cache_bypasses_cached_reads(self) -> None:
        folder = r"X:\Shots\Set A"
        window = _WindowRebuildStub(folder)

        MainWindow._rebuild_current_folder_catalog_cache(window)

        self.assertEqual([(folder, True, True)], window.load_calls)
        self.assertIn("rebuilding catalog cache", window.status_messages[-1].casefold())

    def test_rebuild_current_folder_catalog_cache_requires_real_folder(self) -> None:
        window = _WindowRebuildStub("")

        MainWindow._rebuild_current_folder_catalog_cache(window)

        self.assertEqual([], window.load_calls)
        self.assertIn("open a real folder", window.status_messages[-1].casefold())


if __name__ == "__main__":
    unittest.main()
