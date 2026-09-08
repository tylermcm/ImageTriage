from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QAction
from PySide6.QtWidgets import QApplication, QMainWindow

from image_triage.details_view import PhotoDetailsView
from image_triage.models import ImageRecord, SortMode, sort_records
from image_triage.window import MainWindow


class _GridState:
    def visible_item_count(self) -> int:
        return 4

    def selected_count(self) -> int:
        return 1

    def select_all(self) -> None:
        pass

    def clear_selection(self, *, keep_current: bool = True) -> None:
        pass


class WorkspaceContextMenuTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    @staticmethod
    def _action(parent, text: str, *, checkable: bool = False) -> QAction:
        action = QAction(text, parent)
        action.setCheckable(checkable)
        return action

    def test_empty_grid_menu_exposes_requested_workspace_actions(self) -> None:
        host = QMainWindow()
        selected_sorts = []
        host.actions = SimpleNamespace(
            clear_filters=self._action(host, "Clear Filters"),
            refresh_folder=self._action(host, "Refresh Folder"),
            grid_view=self._action(host, "Grid View", checkable=True),
            details_view=self._action(host, "Details View", checkable=True),
        )
        host._filter_query = SimpleNamespace(search_text="mountains")
        host._pending_search_text = "mountains"
        host._clear_search_from_workspace_menu = lambda: None
        host.grid = _GridState()
        host._effective_loupe_card_style = "gallery"
        host._browser_view_mode = "grid"
        host._allowed_card_styles = lambda: ("detailed", "gallery", "zen")
        host._set_grid_filenames_visible = lambda _visible: None
        host._sort_mode = SortMode.NAME
        host._ai_bundle = None
        host._winner_scores_by_path = {}
        host._set_sort_mode = selected_sorts.append
        host._current_folder = tempfile.gettempdir()
        host._open_current_folder_in_file_manager = lambda: None

        menu = MainWindow._build_empty_grid_context_menu(host)
        visible_actions = [action.text() for action in menu.actions() if not action.isSeparator()]
        self.assertEqual(
            [
                "Clear Filters",
                "Clear Search",
                "Refresh Folder",
                "Select All",
                "Deselect All",
                "View",
                "Sort By",
                "Open Current Folder In File Explorer",
            ],
            visible_actions,
        )

        submenus = {action.text(): action.menu() for action in menu.actions() if action.menu() is not None}
        view_actions = {action.text(): action for action in submenus["View"].actions() if not action.isSeparator()}
        self.assertTrue(view_actions["Show Filenames"].isChecked())
        self.assertEqual({"Show Filenames", "Grid View", "Details View"}, set(view_actions))

        sort_actions = {action.text(): action for action in submenus["Sort By"].actions()}
        self.assertEqual({mode.value for mode in SortMode}, set(sort_actions))
        self.assertTrue(sort_actions[SortMode.NAME.value].isChecked())
        self.assertFalse(sort_actions[SortMode.AI_RANK.value].isEnabled())
        self.assertFalse(sort_actions[SortMode.AI_WOW.value].isEnabled())
        sort_actions[SortMode.TYPE.value].trigger()
        self.assertEqual([SortMode.TYPE], selected_sorts)

    def test_file_type_sort_uses_extension_then_filename(self) -> None:
        records = [
            ImageRecord(path="C:/photos/z.png", name="z.png", size=1, modified_ns=1),
            ImageRecord(path="C:/photos/b.jpg", name="b.jpg", size=1, modified_ns=1),
            ImageRecord(path="C:/photos/a.jpg", name="a.jpg", size=1, modified_ns=1),
        ]

        ordered = sort_records(records, SortMode.TYPE)

        self.assertEqual(["a.jpg", "b.jpg", "z.png"], [record.name for record in ordered])

    def test_details_view_no_longer_builds_a_duplicate_preview_pane(self) -> None:
        details = PhotoDetailsView(ai_text_provider=lambda _record: "-")

        self.assertFalse(hasattr(details, "preview_pane"))
        self.assertFalse(hasattr(details, "preview_toggle"))
        self.assertFalse(hasattr(details, "splitter"))
        self.assertIs(details.table.parentWidget(), details)


if __name__ == "__main__":
    unittest.main()
