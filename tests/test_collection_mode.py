from __future__ import annotations

import os
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QAction, QContextMenuEvent, QKeyEvent, QMouseEvent
from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QStatusBar, QWidget

from image_triage.grid import ThumbnailGridView
from image_triage.library_store import LibraryStore
from image_triage.models import ImageRecord
from image_triage.preview import FullScreenPreview, PreviewEntry
from image_triage.thumbnails import ThumbnailManager
from image_triage.window import MainWindow


def _record(path: str, *, folder: bool = False) -> ImageRecord:
    return ImageRecord(path=path, name=os.path.basename(path), size=1, modified_ns=1, is_folder=folder)


def _mouse(kind: QEvent.Type, point: QPoint) -> QMouseEvent:
    position = QPointF(point)
    return QMouseEvent(kind, position, position, Qt.MouseButton.LeftButton,
                       Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)


class _PreviewStub:
    def __init__(self) -> None:
        self.browse_mode = False

    def set_collection_browse_mode(self, enabled: bool) -> None:
        self.browse_mode = enabled


class _ModeHost(QWidget):
    _begin_collection_mode = MainWindow._begin_collection_mode
    _cancel_collection_mode = MainWindow._cancel_collection_mode
    _save_collection_mode = MainWindow._save_collection_mode
    _refresh_collection_mode_ui = MainWindow._refresh_collection_mode_ui
    _create_virtual_collection_from_selection = MainWindow._create_virtual_collection_from_selection

    def __init__(self, store: LibraryStore) -> None:
        super().__init__()
        self._library_store = store
        self._collection_mode = ""
        self._collection_target_id = ""
        self._collection_previous_view = "grid"
        self._browser_view_mode = "grid"
        self._active_tool_mode = ""
        self._zen_mode_enabled = False
        self.grid = ThumbnailGridView(ThumbnailManager(), self)
        self.grid.resize(760, 420)
        self.grid.set_items([_record("C:/album/a.jpg")], request_thumbnails=False)
        self.grid.collection_selection_changed.connect(self._refresh_collection_mode_ui)
        self.preview = _PreviewStub()
        self.inspector_panel = QWidget(self)
        self.collection_mode_bar = QWidget(self)
        self.collection_mode_bar.hide()
        self.collection_mode_title = QLabel(self)
        self.collection_mode_count = QLabel(self)
        self.collection_mode_save_button = QPushButton(self)
        self._status = QStatusBar(self)
        self.refreshes = 0

    def statusBar(self) -> QStatusBar:
        return self._status

    def _set_browser_view_mode(self, mode: str) -> None:
        self._browser_view_mode = mode

    def _update_action_states(self) -> None:
        pass

    def _cancel_tool_mode(self, *, show_message: bool = True) -> None:
        self._active_tool_mode = ""

    def _refresh_collections_menu(self) -> None:
        self.refreshes += 1

    def _exec_dialog_with_geometry(self, dialog, _key: str):
        dialog.name_field.setText("Cross-folder picks")
        return dialog.DialogCode.Accepted


class CollectionModeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="collection_mode_")
        self.previous_appdata = os.environ.get("IMAGE_TRIAGE_APPDATA")
        os.environ["IMAGE_TRIAGE_APPDATA"] = self.temp.name
        self.store = LibraryStore()
        self.host = _ModeHost(self.store)

    def tearDown(self) -> None:
        self.host.close()
        if self.previous_appdata is None:
            os.environ.pop("IMAGE_TRIAGE_APPDATA", None)
        else:
            os.environ["IMAGE_TRIAGE_APPDATA"] = self.previous_appdata
        self.temp.cleanup()

    def test_create_collects_across_folder_changes_and_saves(self) -> None:
        self.host._create_virtual_collection_from_selection()
        self.assertEqual("create", self.host._collection_mode)
        self.assertTrue(self.host.grid.collection_checkbox_mode())
        self.assertFalse(self.host.collection_mode_save_button.isEnabled())
        self.assertFalse(self.host.inspector_panel.isEnabled())
        self.host.grid.toggle_collection_index(0)
        self.host.grid.set_items([_record("C:/other/b.jpg")], request_thumbnails=False)
        self.host.grid.toggle_collection_index(0)
        self.assertEqual("2 images checked", self.host.collection_mode_count.text())
        self.assertTrue(self.host.collection_mode_save_button.isEnabled())

        self.host._save_collection_mode()

        collections = self.store.list_collections()
        self.assertEqual(1, len(collections))
        saved = self.store.load_collection(collections[0].id)
        self.assertEqual(("C:/album/a.jpg", "C:/other/b.jpg"),
                         tuple(path.replace("\\", "/") for path in saved.item_paths))
        self.assertEqual("", self.host._collection_mode)
        self.assertFalse(self.host.grid.collection_checkbox_mode())
        self.assertTrue(self.host.inspector_panel.isEnabled())

    def test_cancel_discards_unsaved_picks(self) -> None:
        self.host._begin_collection_mode("create")
        self.host.grid.toggle_collection_index(0)
        self.host._cancel_collection_mode()
        self.assertFalse(self.store.list_collections())
        self.assertFalse(self.host.preview.browse_mode)

    def test_canceling_name_dialog_keeps_checked_images(self) -> None:
        self.host._begin_collection_mode("create")
        self.host.grid.toggle_collection_index(0)
        self.host._exec_dialog_with_geometry = lambda dialog, _key: dialog.DialogCode.Rejected
        self.host._save_collection_mode()
        self.assertEqual("create", self.host._collection_mode)
        self.assertEqual(("C:/album/a.jpg",), self.host.grid.collection_paths())
        self.assertFalse(self.store.list_collections())

    def test_edit_starts_with_members_checked_and_can_remove_across_folders(self) -> None:
        collection = self.store.create_collection(
            name="Existing", item_paths=("C:/album/a.jpg", "C:/other/b.jpg")
        )
        self.host._begin_collection_mode("edit", collection=collection)
        self.assertTrue(self.host.grid.collection_path_checked("C:/album/a.jpg"))
        self.host.grid.toggle_collection_index(0)
        self.host.grid.set_items([_record("C:/third/c.jpg")], request_thumbnails=False)
        self.host.grid.toggle_collection_index(0)
        self.host._save_collection_mode()
        saved = self.store.load_collection(collection.id)
        self.assertEqual(("C:/other/b.jpg", "C:/third/c.jpg"),
                         tuple(path.replace("\\", "/") for path in saved.item_paths))

    def test_edit_can_clear_all_members(self) -> None:
        collection = self.store.create_collection(name="Existing", item_paths=("C:/album/a.jpg",))
        self.host._begin_collection_mode("edit", collection=collection)
        self.host.grid.toggle_collection_index(0)
        self.assertTrue(self.host.collection_mode_save_button.isEnabled())
        self.host._save_collection_mode()
        self.assertEqual((), self.store.load_collection(collection.id).item_paths)

    def test_action_gate_leaves_discovery_and_blocks_edits(self) -> None:
        names = (
            "open_folder", "refresh_folder", "open_preview", "show_hidden_folders",
            "grid_view", "browse_catalog", "refresh_catalog", "advanced_filters",
            "clear_filters", "delete_selection", "create_virtual_collection",
            "sort_actions", "filter_actions", "ai_state_actions", "column_actions",
        )

        class ActionBag:
            __dataclass_fields__ = {name: None for name in names}

        actions = ActionBag()
        for name in names:
            setattr(actions, name, {} if name.endswith("_actions") else QAction(name))
        actions.sort_actions["name"] = QAction("Sort by name")
        actions.filter_actions["all"] = QAction("Show all")
        actions.delete_selection.setEnabled(True)
        actions.browse_catalog.setEnabled(False)
        host = type("ActionHost", (), {"actions": actions})()

        MainWindow._limit_actions_for_collection_mode(host)

        self.assertTrue(actions.open_folder.isEnabled())
        self.assertTrue(actions.sort_actions["name"].isEnabled())
        self.assertFalse(actions.browse_catalog.isEnabled())
        self.assertFalse(actions.delete_selection.isEnabled())
        self.assertFalse(actions.create_virtual_collection.isEnabled())


class CollectionCheckboxGridTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.grid = ThumbnailGridView(ThumbnailManager())
        self.grid.resize(760, 420)
        self.grid.set_items([_record("C:/album/a.jpg"), _record("C:/album/b.jpg")], request_thumbnails=False)
        self.grid.set_collection_checkbox_mode(True)

    def tearDown(self) -> None:
        self.grid.deleteLater()

    def test_checkbox_click_is_distinct_from_double_click_preview(self) -> None:
        previews: list[int] = []
        self.grid.preview_requested.connect(previews.append)
        rect = self.grid._item_rect(0)
        checkbox = self.grid._checkbox_rect(rect).center()
        image = self.grid._image_rect(rect).center()
        self.grid.mousePressEvent(_mouse(QEvent.Type.MouseButtonPress, checkbox))
        self.assertTrue(self.grid.collection_path_checked("C:/album/a.jpg"))
        self.grid.mouseDoubleClickEvent(_mouse(QEvent.Type.MouseButtonDblClick, checkbox))
        self.assertEqual([], previews)
        self.grid.mouseDoubleClickEvent(_mouse(QEvent.Type.MouseButtonDblClick, image))
        self.assertEqual([0], previews)

    def test_picks_survive_set_items_and_review_shortcuts_are_suppressed(self) -> None:
        mutations: list[int] = []
        self.grid.winner_requested.connect(mutations.append)
        self.grid.reject_requested.connect(mutations.append)
        self.grid.delete_requested.connect(mutations.append)
        self.grid.toggle_collection_index(0)
        self.grid.set_items([_record("C:/other/c.jpg")], request_thumbnails=False)
        self.assertEqual(("C:/album/a.jpg",), self.grid.collection_paths())
        for key in (Qt.Key.Key_W, Qt.Key.Key_X, Qt.Key.Key_Delete):
            self.grid.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, key, Qt.KeyboardModifier.NoModifier))
        self.assertEqual([], mutations)
        self.grid.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Space, Qt.KeyboardModifier.NoModifier))
        self.assertEqual(2, len(self.grid.collection_paths()))

    def test_review_buttons_and_context_menu_are_inert(self) -> None:
        winners: list[int] = []
        contexts: list[int] = []
        self.grid.winner_requested.connect(winners.append)
        self.grid.context_menu_requested.connect(lambda index, _point: contexts.append(index))
        winner = self.grid._winner_button_hit_rect(self.grid._item_rect(0)).center()
        self.grid.mousePressEvent(_mouse(QEvent.Type.MouseButtonPress, winner))
        self.grid.contextMenuEvent(QContextMenuEvent(
            QContextMenuEvent.Reason.Mouse, QPoint(20, 20), QPoint(120, 120)
        ))
        self.assertEqual([], winners)
        self.assertEqual([], contexts)

    def test_folder_tile_cannot_be_checked(self) -> None:
        self.grid.set_items([_record("C:/other", folder=True)], request_thumbnails=False)
        self.grid.toggle_collection_index(0)
        self.assertEqual((), self.grid.collection_paths())


class CollectionBrowsePreviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_collection_preview_disables_editing_and_review(self) -> None:
        preview = FullScreenPreview()
        preview._entries = [PreviewEntry(_record("C:/album/a.jpg"), "C:/album/a.jpg")]
        winners: list[str] = []
        preview.winner_requested.connect(winners.append)
        preview.set_collection_browse_mode(True)
        self.assertFalse(preview._studio_rail.isVisible())
        self.assertFalse(preview.photo_editor_panel.isEnabled())
        self.assertFalse(preview._mockup_keep.isVisible())
        preview._handle_heart_clicked(0)
        preview.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_W,
                                        Qt.KeyboardModifier.NoModifier))
        self.assertEqual([], winners)
        preview.set_collection_browse_mode(False)
        self.assertTrue(preview.photo_editor_panel.isEnabled())
        self.assertFalse(preview.photoshop_button.isEnabled())
        preview.close()


if __name__ == "__main__":
    unittest.main()
