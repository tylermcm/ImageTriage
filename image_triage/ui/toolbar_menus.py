from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import QMenu, QToolButton, QWidget

from ..models import FilterMode, SortMode
from .actions import MainWindowActions
from .menus import add_ai_results_actions


class ToolbarMenuController:
    """Builds the small, self-contained popup menus and buttons shared by the
    topbar and workspace toolbar (Review/View/Filters/Columns/Sort/AI
    Results/Collections/Catalog). Pure construction from `actions` — holds no
    state of its own beyond the widgets it's asked to parent new menus to."""

    def __init__(self, parent: QWidget, actions: MainWindowActions) -> None:
        self._parent = parent
        self._actions = actions

    def build_popup_button(self, text: str, menu: QMenu) -> QToolButton:
        button = QToolButton()
        button.setObjectName("workspacePresetsButton")
        button.setText(text)
        button.setToolTip(text)
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        button.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        button.setMenu(menu)
        return button

    def build_review_toolbar_menu(self) -> QMenu:
        actions = self._actions
        menu = QMenu(self._parent)
        menu.addAction(actions.open_preview)
        menu.addAction(actions.compare_mode)
        menu.addAction(actions.winner_ladder_mode)
        menu.addAction(actions.auto_advance)
        menu.addSeparator()
        menu.addAction(actions.burst_groups)
        menu.addAction(actions.burst_stacks)
        menu.addSeparator()
        menu.addAction(actions.manage_people)
        return menu

    def build_view_toolbar_menu(self) -> QMenu:
        actions = self._actions
        menu = QMenu(self._parent)

        menu.addAction(actions.grid_view)
        menu.addAction(actions.details_view)
        menu.addAction(actions.zen_mode)
        menu.addSeparator()

        quick_filter_menu = menu.addMenu("Quick Filter")
        for mode in FilterMode:
            quick_filter_menu.addAction(actions.filter_actions[mode])

        sort_menu = menu.addMenu("Sort")
        for mode in SortMode:
            sort_menu.addAction(actions.sort_actions[mode])

        columns_menu = menu.addMenu("Columns")
        for count in range(1, 9):
            columns_menu.addAction(actions.column_actions[count])

        menu.addSeparator()
        menu.addAction(actions.show_hidden_folders)

        return menu

    def build_projects_toolbar_menu(self) -> QMenu:
        actions = self._actions
        menu = QMenu("Collections", self._parent)
        menu.addAction(actions.create_virtual_collection)
        menu.addAction(actions.add_selection_to_collection)
        menu.addAction(actions.remove_selection_from_collection)
        menu.addAction(actions.delete_virtual_collection)
        return menu

    def build_catalog_toolbar_menu(self) -> QMenu:
        actions = self._actions
        menu = QMenu("Library", self._parent)
        menu.addAction(actions.browse_catalog)
        menu.addSeparator()
        menu.addAction(actions.add_current_folder_to_catalog)
        menu.addAction(actions.add_folder_to_catalog)
        menu.addAction(actions.remove_catalog_folder)
        menu.addAction(actions.refresh_catalog)
        menu.addAction(actions.rebuild_folder_catalog_cache)
        return menu

    def build_ai_results_menu(self) -> QMenu:
        menu = QMenu("AI Results And Filters", self._parent)
        add_ai_results_actions(menu, self._actions)
        return menu

    def build_columns_toolbar_menu(self) -> QMenu:
        menu = QMenu("Columns", self._parent)
        for count in range(1, 9):
            menu.addAction(self._actions.column_actions[count])
        return menu

    def build_sort_toolbar_menu(self) -> QMenu:
        menu = QMenu("Sort", self._parent)
        for mode in SortMode:
            menu.addAction(self._actions.sort_actions[mode])
        return menu

    def build_quick_filter_toolbar_menu(self) -> QMenu:
        menu = QMenu("Quick Filter", self._parent)
        for mode in FilterMode:
            menu.addAction(self._actions.filter_actions[mode])
        return menu

    @staticmethod
    def menu_text_with_hint(text: str, hint: str = "") -> str:
        return f"{text}\t{hint}" if hint else text

    def menu_text_with_action_shortcut(self, text: str, action: QAction | None) -> str:
        if action is None:
            return text
        shortcut_text = action.shortcut().toString(QKeySequence.SequenceFormat.NativeText)
        return self.menu_text_with_hint(text, shortcut_text)
