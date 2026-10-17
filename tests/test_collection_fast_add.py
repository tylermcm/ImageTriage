"""Fast ways into a collection: add selection / all visible, the active
collection and its B key, winners auto-added, drops on the sidebar, and one
undo step per add."""
from __future__ import annotations

import os
import tempfile
import unittest
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QMenu, QStatusBar, QWidget

from image_triage.catalog_controller import CatalogController
from image_triage.folder_session import FolderSession, session_field
from image_triage.library_store import LibraryStore
from image_triage.models import ImageRecord
from image_triage.record_ops_controller import UndoAction
from image_triage.ui import CollectionPickerChoice, CollectionPickerDialog


def _record(path: str, *, folder: bool = False) -> ImageRecord:
    return ImageRecord(path=path, name=os.path.basename(path), size=1, modified_ns=1, is_folder=folder)


def _keys(paths) -> tuple[str, ...]:
    return tuple(path.replace("\\", "/") for path in paths)


class _Grid:
    def __init__(self) -> None:
        self.current = -1
        self.selected: list[int] = []

    def current_index(self) -> int:
        return self.current

    def selected_indexes(self) -> list[int]:
        return list(self.selected)


class _UndoStack:
    """Just enough of RecordOpsController's stack: batches pop together."""

    def __init__(self, host: "_Host") -> None:
        self._host = host
        self.actions: list[UndoAction] = []

    def push_undo(self, action: UndoAction) -> None:
        self.actions.append(action)

    def undo_last(self) -> list[UndoAction]:
        batch = [self.actions.pop()]
        while batch[0].batch_id and self.actions and self.actions[-1].batch_id == batch[0].batch_id:
            batch.append(self.actions.pop())
        for action in batch:
            if action.kind in {"collection_add", "collection_remove"}:
                self._host._catalog.undo_collection_change(action)
        return batch


class _Host(QWidget):
    _collection_mode = session_field("collection_mode")

    def __init__(self, store: LibraryStore, settings: QSettings) -> None:
        super().__init__()
        self._folder_session = FolderSession()
        self._library_store = store
        self._settings = settings
        self._collection_mode = ""
        self._records: list[ImageRecord] = []
        self.grid = _Grid()
        self._record_ops = _UndoStack(self)
        self.menu_refreshes = 0
        self._projects = SimpleNamespace(refresh_collections_menu=self._count_refresh)
        self.actions = None
        self._status = QStatusBar(self)
        self._catalog = CatalogController(self)

    def _count_refresh(self) -> None:
        self.menu_refreshes += 1

    def statusBar(self) -> QStatusBar:
        return self._status

    def _selected_records_for_context(self, index: int) -> list[ImageRecord]:
        indexes = self.grid.selected_indexes()
        if index not in indexes:
            indexes = [index]
        return [self._records[i] for i in indexes if 0 <= i < len(self._records) and not self._records[i].is_folder]

    def _record_at(self, index: int) -> ImageRecord | None:
        return self._records[index] if 0 <= index < len(self._records) else None

    def _selected_records_for_workflow(self) -> list[ImageRecord]:
        if self.grid.current < 0:
            return []
        return self._selected_records_for_context(self.grid.current)


class CollectionFastAddTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="collection_fast_add_")
        self.previous_appdata = os.environ.get("IMAGE_TRIAGE_APPDATA")
        os.environ["IMAGE_TRIAGE_APPDATA"] = self.temp.name
        self.store = LibraryStore()
        self.settings = QSettings(os.path.join(self.temp.name, "settings.ini"), QSettings.Format.IniFormat)
        self.host = _Host(self.store, self.settings)
        self.portfolio = self.store.create_collection(name="Portfolio", item_paths=("C:/a/1.jpg",))
        self.host._records = [
            _record("C:/a/folder", folder=True),
            _record("C:/a/1.jpg"),
            _record("C:/a/2.jpg"),
            _record("D:/b/3.jpg"),
        ]

    def tearDown(self) -> None:
        self.host._catalog = None
        self.host.close()
        self.settings.sync()
        del self.settings
        if self.previous_appdata is None:
            os.environ.pop("IMAGE_TRIAGE_APPDATA", None)
        else:
            os.environ["IMAGE_TRIAGE_APPDATA"] = self.previous_appdata
        self.temp.cleanup()

    def _members(self) -> tuple[str, ...]:
        return _keys(self.store.load_collection(self.portfolio.id).item_paths)

    def test_store_reports_only_new_paths_and_header_skips_items(self) -> None:
        missing = self.store.paths_missing_from_collection(self.portfolio.id, ["C:/a/1.jpg", "C:/a/2.jpg", "C:/a/2.jpg"])
        self.assertEqual(("C:/a/2.jpg",), _keys(missing))
        header = self.store.load_collection_header(self.portfolio.id)
        self.assertEqual(("Portfolio", 1, ()), (header.name, header.item_count, header.item_paths))
        self.assertIsNone(self.store.load_collection_header("nope"))

    def test_add_all_visible_skips_folders_and_existing_members_in_one_undo_step(self) -> None:
        self.host._catalog.add_visible_to_collection(collection_id=self.portfolio.id)

        self.assertEqual(("C:/a/1.jpg", "C:/a/2.jpg", "D:/b/3.jpg"), self._members())
        self.assertEqual(1, len(self.host._record_ops.actions))
        self.assertIn("Added 2 visible image(s) to Portfolio (1 already there)", self.host.statusBar().currentMessage())

        self.host._record_ops.undo_last()
        self.assertEqual(("C:/a/1.jpg",), self._members())

    def test_add_selection_uses_the_selection(self) -> None:
        self.host.grid.current = 2
        self.host.grid.selected = [2, 3]
        self.host._catalog.add_selected_to_collection(collection_id=self.portfolio.id)
        self.assertEqual(("C:/a/1.jpg", "C:/a/2.jpg", "D:/b/3.jpg"), self._members())

    def test_adding_nothing_new_pushes_no_undo(self) -> None:
        self.host.grid.current = 1
        self.host._catalog.add_selected_to_collection(collection_id=self.portfolio.id)
        self.assertEqual([], self.host._record_ops.actions)

    def test_collect_key_adds_to_active_collection(self) -> None:
        self.host._catalog.set_active_collection(self.portfolio.id, show_message=False)
        self.assertEqual(self.portfolio.id, self.host._catalog.active_collection_id())

        self.host._catalog.add_index_to_active_collection(2)
        self.assertEqual(("C:/a/1.jpg", "C:/a/2.jpg"), self._members())
        self.assertIn("Added 2.jpg to Portfolio", self.host.statusBar().currentMessage())

        # The menu command only adds; B toggles.
        self.host._catalog.add_index_to_active_collection(2, toggle=False)
        self.assertIn("already in Portfolio", self.host.statusBar().currentMessage())
        self.assertEqual(1, len(self.host._record_ops.actions))

    def test_collect_key_toggles_and_undo_restores_the_order(self) -> None:
        catalog = self.host._catalog
        self.store.add_paths_to_collection(self.portfolio.id, ["C:/a/2.jpg", "D:/b/3.jpg"])
        catalog.set_active_collection(self.portfolio.id, show_message=False)

        catalog.add_index_to_active_collection(2)  # 2.jpg is the middle member
        self.assertEqual(("C:/a/1.jpg", "D:/b/3.jpg"), self._members())
        self.assertIn("Removed 2.jpg from Portfolio", self.host.statusBar().currentMessage())

        self.host._record_ops.undo_last()
        self.assertEqual(("C:/a/1.jpg", "C:/a/2.jpg", "D:/b/3.jpg"), self._members())

    def test_collect_key_on_a_mixed_selection_adds_the_missing_ones(self) -> None:
        self.host._catalog.set_active_collection(self.portfolio.id, show_message=False)
        self.host.grid.selected = [1, 2]  # 1.jpg is a member, 2.jpg is not
        self.host._catalog.add_index_to_active_collection(2)
        self.assertEqual(("C:/a/1.jpg", "C:/a/2.jpg"), self._members())

    def test_collect_key_takes_the_selection_when_the_cursor_is_in_it(self) -> None:
        self.host._catalog.set_active_collection(self.portfolio.id, show_message=False)
        self.host.grid.selected = [2, 3]
        self.host._catalog.add_index_to_active_collection(3)
        self.assertEqual(("C:/a/1.jpg", "C:/a/2.jpg", "D:/b/3.jpg"), self._members())

    def test_popout_collect_key_adds_only_the_photo_on_screen(self) -> None:
        self.host._catalog.set_active_collection(self.portfolio.id, show_message=False)
        self.host.grid.selected = [2, 3]
        self.host._catalog.add_index_to_active_collection(3, whole_selection=False)
        self.assertEqual(("C:/a/1.jpg", "D:/b/3.jpg"), self._members())

    def test_collect_key_is_inert_in_collection_mode(self) -> None:
        self.host._catalog.set_active_collection(self.portfolio.id, show_message=False)
        self.host._collection_mode = "create"
        self.host._catalog.add_index_to_active_collection(2)
        self.host._catalog.add_visible_to_collection(collection_id=self.portfolio.id)
        self.assertEqual(("C:/a/1.jpg",), self._members())

    def test_winners_also_go_to_the_also_add_to_collection_only_when_one_is_set(self) -> None:
        catalog = self.host._catalog
        self.assertEqual("", catalog.winner_auto_add_batch_id())  # default: none
        self.assertEqual("Also add to: none", catalog.also_add_to_text())
        catalog.set_active_collection(self.portfolio.id, show_message=False)
        self.assertEqual("Also add to: Portfolio", catalog.also_add_to_text())
        batch_id = catalog.winner_auto_add_batch_id()
        self.assertTrue(batch_id)

        # The winner's own undo entry shares the batch id, so one Undo takes back both.
        self.host._record_ops.push_undo(UndoAction(kind="annotation", primary_path="D:/b/3.jpg", batch_id=batch_id))
        self.assertEqual("Portfolio", catalog.auto_add_winner_paths(["D:/b/3.jpg"], batch_id=batch_id))
        self.assertEqual(("C:/a/1.jpg", "D:/b/3.jpg"), self._members())

        undone = self.host._record_ops.undo_last()
        self.assertEqual({"annotation", "collection_add"}, {action.kind for action in undone})
        self.assertEqual(("C:/a/1.jpg",), self._members())

    def test_taking_back_an_auto_added_winner_takes_it_out_again(self) -> None:
        catalog = self.host._catalog
        catalog.set_active_collection(self.portfolio.id, show_message=False)
        catalog.auto_add_winner_paths(["D:/b/3.jpg"], batch_id=catalog.winner_auto_add_batch_id())
        self.assertEqual(("C:/a/1.jpg", "D:/b/3.jpg"), self._members())

        # 1.jpg was added by hand, so un-winnering it leaves it alone.
        self.assertEqual("", catalog.winner_auto_remove_batch_id(["C:/a/1.jpg"]))
        batch_id = catalog.winner_auto_remove_batch_id(["D:/b/3.jpg", "C:/a/1.jpg"])
        self.assertTrue(batch_id)
        self.assertEqual("Portfolio", catalog.auto_remove_unwinner_paths(["D:/b/3.jpg", "C:/a/1.jpg"], batch_id=batch_id))
        self.assertEqual(("C:/a/1.jpg",), self._members())
        self.assertEqual("", catalog.winner_auto_remove_batch_id(["D:/b/3.jpg"]))

    def test_insert_paths_at_restores_positions(self) -> None:
        self.store.add_paths_to_collection(self.portfolio.id, ["C:/a/2.jpg", "D:/b/3.jpg", "D:/b/4.jpg"])
        positions = self.store.collection_positions(self.portfolio.id, ["D:/b/4.jpg", "C:/a/1.jpg"])
        self.assertEqual([("C:/a/1.jpg", 0), ("D:/b/4.jpg", 3)], [(_keys([p])[0], i) for p, i in positions])
        self.store.remove_paths_from_collection(self.portfolio.id, [p for p, _i in positions])
        self.store.insert_paths_at(self.portfolio.id, [p for p, _i in positions], [i for _p, i in positions])
        self.assertEqual(("C:/a/1.jpg", "C:/a/2.jpg", "D:/b/3.jpg", "D:/b/4.jpg"), self._members())

    def test_picker_lists_recent_first_and_filters(self) -> None:
        catalog = self.host._catalog
        prints = self.store.create_collection(name="Prints")
        alpha = self.store.create_collection(name="Alpha")
        catalog.remember_recent_collection(prints.id)
        seen = {}

        def fake_picker(entries, *, title, default_id, recent_ids, allow_new):
            seen.update(entries=[name for _id, name, _count in entries], recent=recent_ids, allow_new=allow_new)
            return CollectionPickerChoice(new_name="Brand New")

        catalog.run_collection_picker = fake_picker
        created = catalog.choose_virtual_collection(title="t", prompt="p", allow_new=True)
        self.assertEqual(["Prints", "Alpha", "Portfolio"], seen["entries"])
        self.assertEqual((prints.id,), seen["recent"])
        self.assertEqual("Brand New", created.name)
        self.assertIsNotNone(alpha)

    def test_picker_dialog_search_names_a_new_collection(self) -> None:
        dialog = CollectionPickerDialog([("a", "Portfolio", 3), ("b", "Prints", 1)], title="t", default_id="b")
        self.assertEqual("b", dialog._current_id())
        dialog.search_field.setText("port")
        labels = [dialog.list_widget.item(row).text() for row in range(dialog.list_widget.count())]
        self.assertEqual(["Portfolio  (3)", 'New collection "port"'], labels)
        dialog.search_field.setText("Moab picks")
        dialog._accept_current()
        self.assertEqual(CollectionPickerChoice(new_name="Moab picks"), dialog.choice())
        no_new = CollectionPickerDialog([("a", "Portfolio", 3)], title="t", allow_new=False)
        self.assertEqual(1, no_new.list_widget.count())

    def test_collections_submenu_shows_membership_and_toggles(self) -> None:
        catalog = self.host._catalog
        prints = self.store.create_collection(name="Prints", item_paths=("C:/a/2.jpg",))
        catalog.set_active_collection(prints.id, show_message=False)

        menu = QMenu()
        catalog.populate_collections_menu(menu, ["C:/a/1.jpg", "C:/a/2.jpg"])
        rows = {action.text(): action for action in menu.actions() if action.text()}
        # Active first; partial membership shows as a count, not a tick.
        self.assertEqual(["Prints  (active)  \u2014 1 of 2 in", "Portfolio  \u2014 1 of 2 in", "New collection..."], list(rows))
        self.assertFalse(rows["Portfolio  \u2014 1 of 2 in"].isChecked())

        rows["Portfolio  \u2014 1 of 2 in"].trigger()
        self.assertEqual(("C:/a/1.jpg", "C:/a/2.jpg"), self._members())

        menu = QMenu()
        catalog.populate_collections_menu(menu, ["C:/a/1.jpg"])
        portfolio_row = next(action for action in menu.actions() if action.text() == "Portfolio")
        self.assertTrue(portfolio_row.isChecked())
        portfolio_row.trigger()  # all in -> removes
        self.assertEqual(("C:/a/2.jpg",), self._members())

    def test_chip_text_lists_collections_from_the_cache(self) -> None:
        catalog = self.host._catalog
        self.store.create_collection(name="Prints", item_paths=("C:/a/1.jpg",))
        self.assertEqual("\u25CF 2 collections", catalog.collection_chip_text("C:/a/1.jpg")[0])
        self.assertIn("Portfolio", catalog.collection_chip_text("C:/a/1.jpg")[1])
        self.assertEqual("+ Collection", catalog.collection_chip_text("D:/b/3.jpg")[0])
        # The cache is per change, not per call: a direct store write is not seen until refreshed.
        self.store.add_paths_to_collection(self.portfolio.id, ["D:/b/3.jpg"])
        self.assertEqual("+ Collection", catalog.collection_chip_text("D:/b/3.jpg")[0])
        catalog.invalidate_membership_cache()
        self.assertEqual("\u25CF Portfolio", catalog.collection_chip_text("D:/b/3.jpg")[0])

    def test_menu_offers_none_then_collections_then_new(self) -> None:
        from PySide6.QtWidgets import QMenu

        catalog = self.host._catalog
        self.store.create_collection(name="Prints")
        menu = QMenu()
        catalog.populate_also_add_to_menu(menu)
        rows = [(action.text(), action.isChecked()) for action in menu.actions() if action.text()]
        self.assertEqual(("None (just mark winners)", True), rows[0])
        self.assertEqual({"Portfolio  (1)", "Prints  (0)"}, {text for text, _checked in rows[1:3]})
        self.assertEqual("New collection...", rows[-1][0])

        next(action for action in menu.actions() if action.text() == "Prints  (0)").trigger()
        self.assertEqual("Also add to: Prints", catalog.also_add_to_text())
        menu = QMenu()
        catalog.populate_also_add_to_menu(menu)
        checked = [action.text() for action in menu.actions() if action.isChecked()]
        self.assertEqual(["Prints  (0)"], checked)

    def test_winner_auto_add_is_a_no_op_without_a_batch_id(self) -> None:
        self.assertEqual("", self.host._catalog.auto_add_winner_paths(["D:/b/3.jpg"], batch_id=""))
        self.assertEqual(("C:/a/1.jpg",), self._members())

    def test_dropped_paths_are_added(self) -> None:
        self.host._catalog.add_dropped_paths_to_collection(self.portfolio.id, ["D:/b/3.jpg", ""])
        self.assertEqual(("C:/a/1.jpg", "D:/b/3.jpg"), self._members())

    def test_deleting_the_active_collection_clears_it(self) -> None:
        catalog = self.host._catalog
        catalog.set_active_collection(self.portfolio.id, show_message=False)
        self.assertEqual("Portfolio", catalog.active_collection().name)
        self.store.delete_collection(self.portfolio.id)
        self.assertIsNone(catalog.active_collection())

