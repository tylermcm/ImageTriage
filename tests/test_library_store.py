from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from image_triage.library_store import LibraryStore
from image_triage.scanner import normalize_filesystem_path


class LibraryStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self._previous_appdata = os.environ.get("IMAGE_TRIAGE_APPDATA")
        os.environ["IMAGE_TRIAGE_APPDATA"] = self._temp_dir.name

    def tearDown(self) -> None:
        if self._previous_appdata is None:
            os.environ.pop("IMAGE_TRIAGE_APPDATA", None)
        else:
            os.environ["IMAGE_TRIAGE_APPDATA"] = self._previous_appdata
        self._temp_dir.cleanup()

    def test_virtual_collection_crud_and_membership_updates(self) -> None:
        store = LibraryStore()
        paths = (
            "C:/shoots/hero.jpg",
            "C:/shoots/closeup.jpg",
        )

        created = store.create_collection(
            name="Portfolio Picks",
            description="Homepage contenders",
            kind="Portfolio Picks",
            item_paths=paths,
        )
        self.assertEqual(created.name, "Portfolio Picks")
        self.assertEqual(created.item_count, 2)

        updated = store.add_paths_to_collection(created.id, ("C:/shoots/detail.jpg",))
        self.assertIsNotNone(updated)
        assert updated is not None
        self.assertEqual(updated.item_count, 3)

        trimmed = store.remove_paths_from_collection(created.id, ("C:/shoots/closeup.jpg",))
        self.assertIsNotNone(trimmed)
        assert trimmed is not None
        self.assertEqual(
            trimmed.item_paths,
            (
                normalize_filesystem_path("C:/shoots/hero.jpg"),
                normalize_filesystem_path("C:/shoots/detail.jpg"),
            ),
        )

        loaded = store.load_collection(created.id)
        self.assertEqual(loaded, trimmed)
        self.assertTrue(store.delete_collection(created.id))
        self.assertIsNone(store.load_collection(created.id))

    def test_catalog_refresh_and_search_across_subfolders(self) -> None:
        root = Path(self._temp_dir.name) / "library"
        day_one = root / "day_one"
        day_two = root / "day_two"
        day_one.mkdir(parents=True)
        day_two.mkdir(parents=True)
        (day_one / "hero.jpg").write_bytes(b"hero")
        (day_two / "reject.jpg").write_bytes(b"reject")

        store = LibraryStore()
        store.add_catalog_root(str(root))
        summary = store.refresh_catalog((str(root),))

        self.assertEqual(summary.root_count, 1)
        self.assertEqual(summary.folder_count, 2)
        self.assertEqual(summary.record_count, 2)

        roots = store.list_catalog_roots()
        self.assertEqual(len(roots), 1)
        self.assertEqual(roots[0].indexed_record_count, 2)

        search_results = store.search_catalog(search_text="hero")
        self.assertEqual(len(search_results), 1)
        self.assertEqual(search_results[0].name, "hero.jpg")

        scoped_results = store.search_catalog(root_path=str(root), search_text="reject")
        self.assertEqual(len(scoped_results), 1)
        self.assertEqual(scoped_results[0].name, "reject.jpg")

        loaded_by_path = store.load_catalog_records_for_paths((str(day_one / "hero.jpg"),))
        self.assertEqual(len(loaded_by_path), 1)
        self.assertEqual(next(iter(loaded_by_path.values())).name, "hero.jpg")


if __name__ == "__main__":
    unittest.main()
