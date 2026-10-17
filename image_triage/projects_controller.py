"""The sidebar's projects, collections and face groups, and the search and path controls above the grid. Extracted from MainWindow (docs/mainwindow_decomposition_plan.md, DC-4.4)."""
from __future__ import annotations

import os
import re

from PySide6.QtCore import QObject, QSize, Qt, QTimer
from PySide6.QtGui import QBrush, QColor
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

    def refresh_winners_row(self) -> None:
        """The pinned Winners row: every photo marked winner, from every shoot."""
        panel = getattr(self._window, "winners_list", None)
        catalog = getattr(self._window, "_catalog", None)
        if panel is None or catalog is None:
            return
        count = catalog.winner_count()
        panel.clear()
        item = QListWidgetItem(f"\u2665 Winners  ({count})")
        item.setToolTip("Every photo you've marked as a winner, from every shoot, grouped by folder")
        item.setSizeHint(QSize(0, _PROJECT_ROW_PX))
        panel.addItem(item)
        panel.setFixedHeight(_PROJECT_ROW_PX + 2 * panel.frameWidth() + 2)

    def schedule_winners_refresh(self) -> None:
        """Recount the Winners row once marking settles (after the annotation queue has written)."""
        timer = getattr(self, "_winners_refresh_timer", None)
        if timer is None:
            timer = self._winners_refresh_timer = QTimer(self)
            timer.setSingleShot(True)
            timer.setInterval(800)
            timer.timeout.connect(self.refresh_winners_row)
        timer.start()

    def show_winners_context_menu(self, point) -> None:
        panel = self._window.winners_list
        catalog = self._window._catalog
        menu = QMenu(panel)
        menu.addAction("Open Winners", catalog.open_winners_view)
        menu.addAction("Export Winners...", catalog.export_winners)
        grouped = menu.addAction("Group by folder")
        grouped.setCheckable(True)
        grouped.setChecked(catalog.winners_grouped())
        grouped.toggled.connect(catalog.set_winners_grouped)
        menu.exec(panel.viewport().mapToGlobal(point))

    def refresh_projects_panel(self) -> None:
        """Mirror the collections menu into the sidebar section."""
        self.refresh_winners_row()
        panel = getattr(self._window, "projects_list", None)
        if panel is None:
            return
        collections = self._window._library_store.list_collections()
        catalog = getattr(self._window, "_catalog", None)
        active_id = catalog.active_collection_id() if catalog is not None else ""
        panel.clear()
        for collection in collections:
            is_active = collection.id == active_id
            # The active collection (where B and auto-added winners go) wears a dot.
            label = f"{collection.name}  ({collection.item_count})"
            item = QListWidgetItem(f"\u25CF {label}" if is_active else label)
            item.setData(Qt.ItemDataRole.UserRole, collection.id)
            tooltip = collection.description or collection.name
            if is_active:
                tooltip += "\nNew winners (W) also go here, and B adds here"
            item.setToolTip(tooltip + "\nDrop images here to add them")
            if is_active:
                font = item.font()
                font.setBold(True)
                item.setFont(font)
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

    def collection_row(self, collection_id: str):
        panel = getattr(self._window, "projects_list", None)
        if panel is None or not collection_id:
            return None
        for row in range(panel.count()):
            item = panel.item(row)
            if item.data(Qt.ItemDataRole.UserRole) == collection_id:
                return item
        return None

    def flash_collection_row(self, collection_id: str) -> None:
        """Briefly tint a collection's sidebar row so you see where an add went."""
        item = self.collection_row(collection_id)
        if item is None:
            return
        theme = getattr(self._window, "_theme", None) or default_theme()
        tint = QColor(theme.accent.qcolor())
        tint.setAlpha(90)
        item.setBackground(QBrush(tint))

        def clear() -> None:
            # The list is rebuilt on every change, so look the row up again rather than keep the item.
            current = self.collection_row(collection_id)
            if current is not None and current is not self._drop_target_item():
                current.setBackground(QBrush())

        QTimer.singleShot(900, clear)

    def _drop_target_item(self):
        return self.collection_row(getattr(self, "_drop_target_id", ""))

    def set_collection_drop_target(self, collection_id: str) -> None:
        """Highlight the row a drag is over (empty clears), so a drop never lands on a guess."""
        previous = getattr(self, "_drop_target_id", "")
        if previous == collection_id:
            return
        if previous:
            item = self.collection_row(previous)
            if item is not None:
                item.setBackground(QBrush())
        self._drop_target_id = collection_id
        item = self.collection_row(collection_id)
        if item is not None:
            theme = getattr(self._window, "_theme", None) or default_theme()
            tint = QColor(theme.accent.qcolor())
            tint.setAlpha(140)
            item.setBackground(QBrush(tint))

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
            catalog = self._window._catalog
            menu.addSeparator()
            open_action = menu.addAction("Open collection")
            open_action.triggered.connect(
                lambda _checked=False, target=collection_id: catalog.open_virtual_collection(target)
            )
            add_selected = menu.addAction("Add selection here")
            add_selected.setEnabled(self._window.grid.current_index() >= 0)
            add_selected.triggered.connect(
                lambda _checked=False, target=collection_id: catalog.add_selected_to_collection(collection_id=target)
            )
            visible_count = sum(1 for record in self._window._records if not record.is_folder)
            add_visible = menu.addAction(f"Add all {visible_count} visible here")
            add_visible.setEnabled(visible_count > 0)
            add_visible.triggered.connect(
                lambda _checked=False, target=collection_id: catalog.add_visible_to_collection(collection_id=target)
            )
            if catalog.active_collection_id() == collection_id:
                active_action = menu.addAction("Stop adding new winners here")
                active_action.triggered.connect(catalog.clear_active_collection)
            else:
                active_action = menu.addAction("Also add new winners here")
                active_action.triggered.connect(
                    lambda _checked=False, target=collection_id: catalog.set_active_collection(target)
                )
            if self._window._collection_mode:
                for action in (add_selected, add_visible, active_action):
                    action.setEnabled(False)
            export_action = menu.addAction("Export...")
            export_action.triggered.connect(lambda _checked=False, target=collection_id: catalog.export_collection(target))
            menu.addAction(self._window.actions.remove_selection_from_collection)
            menu.addAction(self._window.actions.delete_virtual_collection)
        menu.exec(panel.viewport().mapToGlobal(point))

    def refresh_collections_menu(self) -> None:
        self.refresh_projects_panel()
        catalog = getattr(self._window, "_catalog", None)
        if catalog is not None:
            catalog.refresh_collection_markers()
        if not hasattr(self._window, "collections_menu") or self._window.collections_menu is None:
            return
        self._window.collections_menu.clear()
        self._window.collections_menu.setTitle("Collections")
        actions = self._window.actions
        if actions is None:
            return
        self._window._catalog.refresh_also_add_to_button()
        active = self._window._catalog.active_collection()
        actions.add_to_active_collection.setText(f"Add to {active.name}" if active is not None else "Add to Collection...")
        self._window.collections_menu.addAction(actions.create_virtual_collection)
        self._window.collections_menu.addAction(actions.add_selected_to_collection)
        self._window.collections_menu.addAction(actions.add_visible_to_collection)
        self._window.collections_menu.addSeparator()
        self._window.collections_menu.addAction(actions.add_to_active_collection)
        self._window.collections_menu.addAction(actions.set_active_collection)
        self._window.collections_menu.addSeparator()
        self._window.collections_menu.addAction(actions.export_current_set)
        self._window.collections_menu.addAction(actions.add_selection_to_collection)
        self._window.collections_menu.addAction(actions.remove_selection_from_collection)
        self._window.collections_menu.addAction(actions.delete_virtual_collection)
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
