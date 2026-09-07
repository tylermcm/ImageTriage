"""The Projects sidebar section over the library store's virtual collections.

The collections backend already existed; these cover the sidebar surfacing of
it, which is the part that was missing.
"""
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QListWidget, QListWidgetItem

from image_triage.library_store import LibraryStore
from image_triage.window import _MAX_VISIBLE_PROJECT_ROWS, _PROJECT_ROW_PX, MainWindow


class _PanelHost:
    """Only the pieces of MainWindow the Projects section touches."""

    def __init__(self, store: LibraryStore) -> None:
        self._library_store = store
        self.projects_list = QListWidget()

    _refresh_projects_panel = MainWindow._refresh_projects_panel
    _update_projects_height = MainWindow._update_projects_height
    _project_id_for_item = MainWindow._project_id_for_item


class ProjectsPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory(prefix="projects_panel_")
        # LibraryStore resolves its path through app_data_root() at construction,
        # which honours IMAGE_TRIAGE_APPDATA first. Without this the tests write
        # into the real user library.
        self._previous_appdata = os.environ.get("IMAGE_TRIAGE_APPDATA")
        os.environ["IMAGE_TRIAGE_APPDATA"] = self._temp.name
        self.store = LibraryStore()
        self.assertTrue(
            str(self.store._db_path).startswith(self._temp.name),
            "the store must be isolated from the real library",
        )
        self.host = _PanelHost(self.store)

    def tearDown(self) -> None:
        if self._previous_appdata is None:
            os.environ.pop("IMAGE_TRIAGE_APPDATA", None)
        else:
            os.environ["IMAGE_TRIAGE_APPDATA"] = self._previous_appdata
        try:
            self._temp.cleanup()
        except OSError:
            pass

    def test_empty_state_is_shown_and_not_selectable(self) -> None:
        self.host._refresh_projects_panel()
        self.assertEqual(1, self.host.projects_list.count())
        item = self.host.projects_list.item(0)
        self.assertEqual("No projects yet", item.text())
        self.assertEqual(Qt.ItemFlag.NoItemFlags, item.flags())
        self.assertEqual("", self.host._project_id_for_item(item))

    def test_lists_collections_with_their_counts(self) -> None:
        created = self.store.create_collection(name="Client Job", item_paths=("a.jpg", "b.jpg"))
        self.host._refresh_projects_panel()
        self.assertEqual(1, self.host.projects_list.count())
        item = self.host.projects_list.item(0)
        self.assertIn("Client Job", item.text())
        self.assertIn("2", item.text(), "the item count belongs in the label")
        self.assertEqual(created.id, self.host._project_id_for_item(item))

    def test_reflects_later_additions(self) -> None:
        collection = self.store.create_collection(name="Portfolio", item_paths=("a.jpg",))
        self.host._refresh_projects_panel()
        self.store.add_paths_to_collection(collection.id, ("b.jpg", "c.jpg"))
        self.host._refresh_projects_panel()
        self.assertIn("3", self.host.projects_list.item(0).text())

    def test_height_is_capped_so_it_cannot_crowd_the_pane(self) -> None:
        overflow = _MAX_VISIBLE_PROJECT_ROWS + 4
        for index in range(overflow):
            self.store.create_collection(name=f"Set {index}", item_paths=(f"{index}.jpg",))
        self.host._refresh_projects_panel()
        panel = self.host.projects_list
        self.assertEqual(overflow, panel.count())

        rows = [panel.item(i).sizeHint().height() for i in range(panel.count())]
        capped = sum(rows[:_MAX_VISIBLE_PROJECT_ROWS]) + 2 * panel.frameWidth() + 2
        self.assertEqual(capped, panel.maximumHeight())
        self.assertLess(
            panel.maximumHeight(), sum(rows), "a long list must be capped, not shown whole"
        )

    def test_short_list_is_never_taller_than_its_rows(self) -> None:
        self.store.create_collection(name="Only one", item_paths=("a.jpg",))
        self.host._refresh_projects_panel()
        panel = self.host.projects_list
        rows = [panel.item(i).sizeHint().height() for i in range(panel.count())]
        # It may shrink to share the pane, but it never claims more than its
        # content, so a short list leaves the room to the expanded section.
        self.assertEqual(sum(rows) + 2 * panel.frameWidth() + 2, panel.maximumHeight())
        self.assertEqual(0, panel.minimumHeight())

    def test_rows_carry_an_explicit_height(self) -> None:
        # QSize(-1, h) is invalid and Qt silently drops the hint, so the width
        # must be non-negative for the height to stick at all.
        self.store.create_collection(name="Only one", item_paths=("a.jpg",))
        self.host._refresh_projects_panel()
        hint = self.host.projects_list.item(0).sizeHint()
        self.assertTrue(hint.isValid(), "an invalid hint is discarded by Qt")
        self.assertEqual(_PROJECT_ROW_PX, hint.height())

    def test_id_lookup_tolerates_a_missing_item(self) -> None:
        self.assertEqual("", self.host._project_id_for_item(None))
        self.assertEqual("", self.host._project_id_for_item(QListWidgetItem("stray")))


if __name__ == "__main__":
    unittest.main()
