"""The sidebar's projects, collections and face groups, and the search and path controls above the grid. Extracted from MainWindow (docs/mainwindow_decomposition_plan.md, DC-4.4)."""
from __future__ import annotations

import os
import re

from PySide6.QtCore import QObject, QSize, Qt
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QLineEdit, QListWidgetItem, QMenu, QSizePolicy, QToolButton, QWidget

from .aiculler_workflow import aiculler_db_path
from .records_view_controller import _memory_path_key
from .scanner import normalize_filesystem_path
from .ui import WORKSPACE_METRICS, default_theme
from .ui.directory_suggestions import _DirectorySuggestionController
from .ui.face_groups import face_group_photo_paths, load_face_groups
from .ui.project_rows import _MAX_VISIBLE_PROJECT_ROWS, _PROJECT_EMPTY_ROW_PX, _PROJECT_ROW_PX

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .window import MainWindow


class ProjectsController(QObject):
    """The sidebar's projects, collections and face groups, and the search and path controls above the grid. Extracted from MainWindow (docs/mainwindow_decomposition_plan.md, DC-4.4)."""

    def __init__(self, window: "MainWindow") -> None:
        super().__init__(window)
        self._window = window
        self._scope_label = ""

    def build_section_label(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("sectionLabel")
        return label

    def build_search_field(self) -> QLineEdit:
        field = QLineEdit()
        field.setObjectName("workspaceSearchField")
        field.setClearButtonEnabled(True)
        field.setPlaceholderText("Search by content, person, or filename...")
        field.setToolTip(
            "Search naturally, for example: red car, dog on a beach, or mountains at sunset. "
            "Object and people search use the current folder's AI index."
        )
        field.setMinimumWidth(140)
        field.setMaximumWidth(320)
        field.setSizePolicy(QSizePolicy.Policy.MinimumExpanding, QSizePolicy.Policy.Fixed)
        field.returnPressed.connect(lambda target=field: self.open_directory_from_search(target))
        return field

    def open_directory_from_search(self, field: QLineEdit) -> None:
        """Enter on a folder path in a search box opens that folder."""
        raw = field.text().strip().strip('"')
        if not raw:
            return
        explicit = bool(re.match(r"^(?:[A-Za-z]:|\\|~)", raw))
        if not explicit and "/" not in raw and "\\" not in raw:
            return
        candidate = normalize_filesystem_path(os.path.expanduser(raw))
        if candidate and os.path.isfile(candidate):
            candidate = os.path.dirname(candidate)
        if not candidate or not os.path.isdir(candidate):
            if explicit:
                self._window.statusBar().showMessage(f"Folder not found: {raw}")
            return
        field.clear()
        self._window._navigation.select_folder(candidate)

    def build_path_combo(self, *, mode: str) -> QComboBox:
        combo = QComboBox()
        combo.setObjectName("pathComboBox")
        combo.setEditable(True)
        combo.setCompleter(None)
        combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        combo.setDuplicatesEnabled(False)
        combo.setMinimumWidth(280)
        combo.setMaximumWidth(640)
        combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        combo.setToolTip("Type a folder path or choose a recent folder")
        line_edit = combo.lineEdit()
        if line_edit is not None:
            line_edit.setPlaceholderText("No folder selected")
            line_edit.setClearButtonEnabled(False)
            line_edit.returnPressed.connect(lambda target=combo: self._window._navigation.commit_path_combo_text(target))
        combo.activated.connect(lambda index, target=combo: self._window._navigation.handle_path_combo_activated(target, index))
        combo._directory_suggestion_controller = _DirectorySuggestionController(  # type: ignore[attr-defined]
            combo,
            on_accept_path=self._window._navigation.handle_path_suggestion_accepted,
        )
        return combo

    def build_directory_nav_button(self, text: str, tooltip: str, *, mode: str) -> QToolButton:
        button = QToolButton()
        button.setObjectName("pathNavButton")
        button.setText("")
        button.setToolTip(tooltip)
        button.setStatusTip(tooltip)
        button.setAutoRaise(True)
        button.setCursor(Qt.CursorShape.ArrowCursor)
        button.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        button.setFixedSize(38, 28)
        button.setIconSize(QSize(24, 24))
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        color = (self._window._theme or default_theme()).text_muted.qcolor()
        direction = "up" if text == "\u2191" else "down"
        button.setIcon(self._window._appearance.directory_nav_icon(direction, color))
        return button

    def build_path_control(self, combo: QComboBox, *, mode: str) -> QWidget:
        wrapper = QWidget()
        wrapper.setObjectName("pathControl")
        wrapper.setMinimumWidth(344)
        wrapper.setMaximumWidth(720)
        wrapper.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        layout = QHBoxLayout(wrapper)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(WORKSPACE_METRICS.space_4)

        up_button = self.build_directory_nav_button("\u2191", "Open parent folder", mode=mode)
        down_button = self.build_directory_nav_button("\u2193", "Open only child folder", mode=mode)
        up_button.clicked.connect(self._window._navigation.navigate_to_parent_folder)
        down_button.clicked.connect(self._window._navigation.navigate_to_only_child_folder)
        self._window._directory_up_buttons.append(up_button)
        self._window._directory_down_buttons.append(down_button)

        layout.addWidget(up_button, 0)
        layout.addWidget(down_button, 0)
        layout.addWidget(combo, 1)
        return wrapper

    def build_selection_count_label(self, *, mode: str) -> QLabel:
        label = QLabel("0 selected")
        label.setObjectName("toolbarSelectionCount")
        label.setMinimumWidth(76)
        label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        label.setToolTip("Selected images in the current view")
        return label

    def is_cluster_item(self, item_id: object) -> bool:
        # Items that render in the top-bar cluster (everything the cluster can
        # show). Kept structural — no dependency on self.actions — so it is safe
        # to call at load time before the UI is built.
        return isinstance(item_id, str) and bool(item_id) and item_id not in self._window.TOPBAR_CHROME_ITEMS

    def items_to_slots(self, items) -> list[str | None]:
        n = self._window.TOPBAR_SLOT_COUNT
        usable = n
        result: list[str | None] = [None] * n
        seen: set[str] = set()
        idx = 0
        for item in items:
            if idx >= usable:
                break
            if not self.is_cluster_item(item):
                continue
            if item not in self._window.TOPBAR_REPEATABLE_ITEMS:
                if item in seen:
                    continue
                seen.add(item)
            result[idx] = item
            idx += 1
        return result

    def scope_display_label(self) -> str:
        if self._window._scope_kind == "folder":
            return self._window._current_folder or "No folder selected"
        return self._scope_label or "Virtual Scope"

    def apply_scope_label(self) -> None:
        self._window._navigation.refresh_recent_folder_combos()

    def set_scope_state(self, *, kind: str, scope_id: str = "", label: str = "") -> None:
        self._window._folder_session.scope_kind = kind
        self._window._folder_session.scope_id = scope_id
        self._scope_label = label
        self.apply_scope_label()

    def current_scope_key(self) -> str:
        if self._window._scope_kind == "folder":
            return _memory_path_key(self._window._current_folder)
        return f"{self._window._scope_kind}:{self._window._scope_id or self._scope_label.casefold()}"

    def face_groups_db_path(self):
        paths = self._window._aiculler.aiculler_paths_for_current_folder()
        if paths is None:
            return None
        db_path = aiculler_db_path(paths)
        return db_path if db_path.exists() else None

    def refresh_face_groups(self) -> None:
        """Reload the sidebar's face list for the current folder."""
        panel = getattr(self._window, "face_groups_panel", None)
        if panel is None:
            return
        db_path = self.face_groups_db_path()
        if db_path is None:
            panel.set_groups([], has_index=False)
            return
        panel.set_groups(load_face_groups(db_path), has_index=True)

    def handle_face_group_activated(self, group) -> None:
        db_path = self.face_groups_db_path()
        if db_path is None:
            return
        paths = face_group_photo_paths(db_path, group.cluster_ids)
        if not paths:
            self._window.statusBar().showMessage("No indexed photos for that face yet.")
            return
        self._window._records_view.show_photos_for_person(group.filter_label, paths)

    def refresh_projects_panel(self) -> None:
        """Mirror the collections menu into the sidebar section."""
        panel = getattr(self._window, "projects_list", None)
        if panel is None:
            return
        collections = self._window._library_store.list_collections()
        panel.clear()
        for collection in collections:
            item = QListWidgetItem(f"{collection.name}  ({collection.item_count})")
            item.setData(Qt.ItemDataRole.UserRole, collection.id)
            item.setToolTip(collection.description or collection.name)
            # Sized here for the same reason as the face rows: a stylesheet
            # min-height is only a floor and drifts with font size and DPI.
            item.setSizeHint(QSize(0, _PROJECT_ROW_PX))
            panel.addItem(item)
        if not collections:
            # Keep the empty state close to its section header.  The previous
            # two-line, vertically-centred row left a conspicuous blank band
            # above the only visible text (and QListWidget elided the newline
            # anyway, so it still appeared as a single line).
            empty = QListWidgetItem("No collections yet.")
            empty.setFlags(Qt.ItemFlag.NoItemFlags)
            empty.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
            empty.setSizeHint(QSize(0, _PROJECT_EMPTY_ROW_PX))
            panel.addItem(empty)
        self.update_projects_height()

    def update_projects_height(self) -> None:
        panel = getattr(self._window, "projects_list", None)
        if panel is None:
            return
        heights = [panel.item(row).sizeHint().height() for row in range(panel.count())]
        if not heights:
            heights = [_PROJECT_ROW_PX]
        # Cap the section so a long project list cannot crowd out the pane.
        visible = sum(heights[:_MAX_VISIBLE_PROJECT_ROWS])
        panel.setMinimumHeight(0)
        panel.setMaximumHeight(visible + 2 * panel.frameWidth() + 2)

    def project_id_for_item(self, item) -> str:
        if item is None:
            return ""
        return str(item.data(Qt.ItemDataRole.UserRole) or "")

    def handle_project_activated(self, item) -> None:
        collection_id = self.project_id_for_item(item)
        if collection_id:
            self._window._catalog.open_virtual_collection(collection_id)

    def show_projects_context_menu(self, point) -> None:
        panel = self._window.projects_list
        item = panel.itemAt(point)
        collection_id = self.project_id_for_item(item)
        menu = QMenu(panel)
        menu.addAction(self._window.actions.create_virtual_collection)
        menu.addAction(self._window.actions.add_selection_to_collection)
        if collection_id:
            menu.addSeparator()
            open_action = menu.addAction("Open collection")
            open_action.triggered.connect(
                lambda _checked=False, target=collection_id: self._window._catalog.open_virtual_collection(target)
            )
            menu.addAction(self._window.actions.remove_selection_from_collection)
            menu.addAction(self._window.actions.delete_virtual_collection)
        menu.exec(panel.viewport().mapToGlobal(point))

    def refresh_collections_menu(self) -> None:
        self.refresh_projects_panel()
        if not hasattr(self._window, "collections_menu") or self._window.collections_menu is None:
            return
        self._window.collections_menu.clear()
        self._window.collections_menu.setTitle("Collections")
        self._window.collections_menu.addAction(self._window.actions.create_virtual_collection)
        self._window.collections_menu.addAction(self._window.actions.add_selection_to_collection)
        self._window.collections_menu.addAction(self._window.actions.remove_selection_from_collection)
        self._window.collections_menu.addAction(self._window.actions.delete_virtual_collection)
        self._window.collections_menu.addSeparator()

        collections = self._window._library_store.list_collections()
        if collections:
            header = self._window.collections_menu.addSection("Open Collection")
            header.setEnabled(False)
            for collection in collections:
                action = self._window.collections_menu.addAction(f"{collection.name} ({collection.item_count})")
                action.setToolTip(collection.description or collection.kind)
                action.triggered.connect(lambda _checked=False, target=collection.id: self._window._catalog.open_virtual_collection(target))
        else:
            empty_action = self._window.collections_menu.addAction("No collections yet")
            empty_action.setEnabled(False)
        if self._window.actions is not None:
            self._window._inspector.update_action_states()
