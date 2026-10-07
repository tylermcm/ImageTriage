"""Folder navigation: the tree, drives, favorites, recent folders, the path bar and its suggestions, parent/child moves and back-forward history. Extracted from MainWindow (docs/mainwindow_decomposition_plan.md, DC-4.4)."""
from __future__ import annotations

import os
import time

from PySide6.QtCore import QDir, QModelIndex, QObject, QSignalBlocker, QThreadPool, Qt
from PySide6.QtGui import QStandardItemModel
from PySide6.QtWidgets import QComboBox, QFileDialog, QFileSystemModel, QListWidgetItem, QMenu
from pathlib import Path

from . import path_policy
from .drive_list_model import DriveListModel
from .file_ops import is_unc_path
from .perf import perf_logger
from .records_view_controller import _memory_path_key
from .scanner import PathReachableTask, scan_child_folders
from .shell_actions import open_in_file_explorer

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .window import MainWindow


class NavigationController(QObject):
    """Folder navigation: the tree, drives, favorites, recent folders, the path bar and its suggestions, parent/child moves and back-forward history. Extracted from MainWindow (docs/mainwindow_decomposition_plan.md, DC-4.4)."""

    def __init__(self, window: "MainWindow") -> None:
        super().__init__(window)
        self._window = window
        self._drive_sync_token = 0
        self._empty_tree_model: QStandardItemModel | None = None
        self.drive_model = DriveListModel(window)  # the Drives list's rows; see drive_list_model.py

    # ------------------------------------------------------------------ the Folders tree's model

    def empty_tree_model(self) -> QStandardItemModel:
        """What the Folders tree shows while no drive is rooted. A ``QFileSystemModel`` with no root would list every
        drive (and so ask each one, dead network drives included, which blocks ~20 s per dead drive)."""
        if self._empty_tree_model is None:
            self._empty_tree_model = QStandardItemModel(self)
        return self._empty_tree_model

    @staticmethod
    def _same_root(a: str, b: str) -> bool:
        return os.path.normcase(os.path.normpath(a)) == os.path.normcase(os.path.normpath(b)) if a and b else a == b

    def attach_tree_model(self, model) -> None:
        """Show ``model`` in the Folders tree. A file-system model is only ever attached after it has been rooted at a
        drive, never before (see :meth:`empty_tree_model`)."""
        tree = self._window.folder_tree
        if tree.model() is model:
            return
        tree.setModel(model)
        for column in range(1, model.columnCount()):
            tree.hideColumn(column)

    def root_tree_at(self, drive_root: str) -> QModelIndex:
        """Root the Folders tree at ``drive_root`` and return its index (invalid if it cannot be shown).

        The model lists only what is under one drive, so one dead drive elsewhere cannot hold it up. Callers must have
        established that asking about ``drive_root`` is safe on the GUI thread (a plain local drive, or a share a worker
        has seen answer)."""
        window = self._window
        model = window.folder_model
        if not self._same_root(model.rootPath(), drive_root):
            model.setRootPath(drive_root)
        self.attach_tree_model(model)
        index = model.index(drive_root)
        if index.isValid() and window.folder_tree.rootIndex() != index:
            window.folder_tree.setRootIndex(index)
        return index

    def sync_drive_roots(self) -> None:
        """Pick up a drive that appeared or went away (a card inserted, a share mapped) since the list was built."""
        self.drive_model.sync_roots()

    def choose_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self._window, "Choose Folder", self._window._current_folder or QDir.homePath())
        if folder:
            self.select_folder(folder)

    def select_folder(
        self,
        folder: str,
        *,
        sync_tree: bool = True,
        chunked_restore: bool = False,
        preferred_record_path: str | None = None,
    ) -> None:
        perf_logger().log(
            "folder.select",
            folder=folder,
            sync_tree=sync_tree,
            chunked_restore=chunked_restore,
            preferred_record_path=preferred_record_path or "",
        )
        slow_source = self._window._is_slow_source_folder(folder)
        if sync_tree and slow_source:
            perf_logger().log("folder.select.tree_sync_skipped", folder=folder, reason="slow_source")
        elif sync_tree:
            sync_start = time.perf_counter()
            if path_policy.is_plain_local(folder):
                self.root_tree_at(path_policy.drive_root(folder))  # the tree shows one drive: the one this folder is on
            index = self._window.folder_model.index(folder)
            if index.isValid():
                self._window.folder_tree.setCurrentIndex(index)
            perf_logger().duration(
                "folder.select.tree_sync",
                (time.perf_counter() - sync_start) * 1000.0,
                folder=folder,
                index_valid=index.isValid(),
            )
        self._window._scan.load_folder(
            folder,
            chunked_restore=chunked_restore,
            preferred_record_path=preferred_record_path,
        )

    def handle_tree_selection(self, index) -> None:
        folder = self._window.folder_model.filePath(index)
        if folder:
            self._window._scan.load_folder(folder)

    def handle_drive_selected(self, index) -> None:
        if not index.isValid():
            return
        drive = self.drive_model.filePath(index)
        if not drive:
            return
        if path_policy.is_plain_local(drive):
            self.root_tree_at(drive)
        # A share, removable or optical drive is rooted by sync_drive_sections once the folder load has started:
        # asking the model about it here could block the window on a drive that does not answer.
        self._window.folder_tree.clearSelection()
        self._window.folder_tree.setCurrentIndex(QModelIndex())
        self._window.drive_list.clearSelection()
        self._window._scan.load_folder(drive)

    def refresh_drive_list(self) -> None:
        # refresh_folder_tree re-reads the drives (a fresh check of every network/removable one) as well as the tree.
        self.refresh_folder_tree()
        self._window.drive_list.viewport().update()

    def sync_drive_sections(self) -> None:
        """Root the Folders tree at the current folder's drive and open the
        path down to it, without changing which row is selected."""
        tree = getattr(self._window, "folder_tree", None)
        if tree is None or not hasattr(self._window, "drive_list"):
            return
        folder = self._window._current_folder if self._window._scope_kind == "folder" else ""
        drive_root = self._window._drive_root_for(folder or "")
        if not drive_root:
            return
        if self._window._is_slow_source_folder(folder) or not path_policy.is_plain_local(drive_root):
            # QFileSystemModel.index() on a share that is asleep blocks the GUI thread for ~20 s, and this
            # runs on every folder open, so a share's drive is re-rooted only after a worker has seen it
            # answer. (select_folder already skips selecting a share's folder in the tree.)
            self.request_drive_sections_sync(folder, drive_root)
            return
        self.apply_drive_sections(folder, drive_root)

    def request_drive_sections_sync(self, folder: str, drive_root: str) -> None:
        self._drive_sync_token += 1
        token = self._drive_sync_token
        task = PathReachableTask(drive_root, token)
        task.signals.checked.connect(self.handle_drive_reachable, Qt.ConnectionType.QueuedConnection)
        self._window._drive_sync_tasks[token] = (task, folder, drive_root)
        QThreadPool.globalInstance().start(task)

    def handle_drive_reachable(self, token: int, _path: str, reachable: bool) -> None:
        pending = self._window._drive_sync_tasks.pop(token, None)
        if pending is None or token != self._drive_sync_token or not reachable:
            return
        _task, folder, drive_root = pending
        if self._window._scope_kind != "folder" or _memory_path_key(self._window._current_folder) != _memory_path_key(folder):
            return  # the user has moved on while the worker was asking
        self.apply_drive_sections(folder, drive_root)

    def apply_drive_sections(self, folder: str, drive_root: str) -> None:
        tree = self._window.folder_tree
        root_index = self.root_tree_at(drive_root)
        if not root_index.isValid():
            return
        target = self._window.folder_model.index(folder)
        ancestors: list[QModelIndex] = []
        parent = target.parent() if target.isValid() else QModelIndex()
        while parent.isValid() and parent != root_index:
            ancestors.append(parent)
            parent = parent.parent()
        for ancestor in reversed(ancestors):
            tree.expand(ancestor)

    def handle_favorite_activated(self, item: QListWidgetItem) -> None:
        folder = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(folder, str) and folder and not self._window._dir_confirmed_missing(folder):
            self.select_folder(folder)

    def show_folder_tree_context_menu(self, point) -> None:
        index = self._window.folder_tree.indexAt(point)
        if not index.isValid():
            return
        folder = self._window.folder_model.filePath(index)
        if not folder or self._window._dir_confirmed_missing(folder):
            return
        self.show_folder_context_menu(folder, self._window.folder_tree.viewport().mapToGlobal(point), is_favorite=folder in self._window._favorites)

    def show_favorites_context_menu(self, point) -> None:
        item = self._window.favorites_list.itemAt(point)
        if item is None:
            return
        folder = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(folder, str) or not folder:
            return
        self.show_folder_context_menu(folder, self._window.favorites_list.viewport().mapToGlobal(point), is_favorite=True)

    def show_folder_context_menu(self, folder: str, global_pos, *, is_favorite: bool) -> None:
        if self._window._collection_mode:
            menu = QMenu(self._window)
            open_action = menu.addAction("Open")
            if menu.exec(global_pos) == open_action:
                self.select_folder(folder)
            return
        menu = QMenu(self._window)
        open_action = menu.addAction("Open")
        explorer_label = "Open In File Explorer" if os.name == "nt" else "Open In File Manager"
        explorer_action = menu.addAction(explorer_label)
        menu.addSeparator()
        new_folder_action = menu.addAction("New Folder...")
        extract_archive_action = menu.addAction("Extract Archive Here...")
        rename_action = menu.addAction("Rename...")
        move_action = menu.addAction("Move Folder...")
        delete_action = menu.addAction("Delete Folder...")
        menu.addSeparator()
        catalog_action = menu.addAction("Remove From Library" if self._window._library_store.is_catalog_root(folder) else "Add To Library")
        favorite_action = menu.addAction("Remove From Favorites" if is_favorite else "Add To Favorites")
        can_modify = not self._window._is_filesystem_root(folder)
        rename_action.setEnabled(can_modify)
        move_action.setEnabled(can_modify)
        delete_action.setEnabled(can_modify)

        chosen = menu.exec(global_pos)
        if chosen is None:
            return
        if chosen == open_action:
            self.select_folder(folder)
            return
        if chosen == explorer_action:
            open_in_file_explorer(folder)
            return
        if chosen == new_folder_action:
            self._window._folder_ops.create_folder_prompt(folder, select_created=True)
            return
        if chosen == extract_archive_action:
            self._window._export_jobs.extract_archive_into_folder_prompt(folder)
            return
        if chosen == rename_action:
            self._window._folder_ops.rename_folder(folder)
            return
        if chosen == move_action:
            self._window._folder_ops.move_folder_prompt(folder)
            return
        if chosen == delete_action:
            self._window._folder_ops.delete_folder_prompt(folder)
            return
        if chosen == catalog_action:
            if self._window._library_store.is_catalog_root(folder):
                self._window._library_store.remove_catalog_root(folder)
                self._window._catalog.refresh_catalog_menu()
                self._window.statusBar().showMessage(f"Removed from library: {Path(folder).name}")
            else:
                self._window._library_store.add_catalog_root(folder)
                self._window._catalog.refresh_catalog_menu()
                self._window._catalog.start_catalog_refresh((folder,), label=f"Indexing {Path(folder).name} for the library...")
            return
        if chosen == favorite_action:
            if is_favorite:
                self.remove_favorite(folder)
            else:
                self.add_favorite(folder)

    def refresh_folder_tree(self) -> None:
        """Re-read the folder tree from disk.

        QFileSystemModel never re-lists a directory it has already populated,
        and its watcher does not fire on network shares, so new folders stayed
        invisible. Swapping in a fresh model is the only reliable re-read; the
        root, expanded branches and current selection are put back afterwards.
        """
        window = self._window
        tree = window.folder_tree
        old_model = window.folder_model
        showing_old = tree.model() is old_model
        root_path = old_model.filePath(tree.rootIndex()) if showing_old and tree.rootIndex().isValid() else ""
        current_path = old_model.filePath(tree.currentIndex()) if showing_old and tree.currentIndex().isValid() else ""
        drive_path = self.drive_model.filePath(window.drive_list.currentIndex()) if window.drive_list.currentIndex().isValid() else ""
        expanded: list[str] = []

        def collect(parent: QModelIndex) -> None:
            for row in range(old_model.rowCount(parent)):
                child = old_model.index(row, 0, parent)
                if tree.isExpanded(child):
                    expanded.append(old_model.filePath(child))
                    collect(child)

        if showing_old:
            collect(tree.rootIndex())

        def restorable(path: str) -> bool:
            # model.index() on a share that is asleep blocks the GUI thread for ~20 s, so only plain local paths are
            # put back here; a share's drive is re-rooted by sync_drive_sections below once a worker has seen it
            # answer (its expanded branches and drive selection are not kept).
            return bool(path) and not window._is_slow_source_folder(path) and path_policy.is_plain_local(path)

        new_model = QFileSystemModel(window)
        new_model.setFilter(self.folder_tree_filter())
        window.folder_model = new_model
        if restorable(root_path):
            # Rooted before it is shown, so the tree never lists "all drives".
            self.root_tree_at(root_path)
        else:
            self.attach_tree_model(self.empty_tree_model())
        old_model.deleteLater()

        if tree.model() is new_model:
            for path in expanded:
                if not restorable(path):
                    continue
                index = new_model.index(path)
                if index.isValid():
                    tree.expand(index)
            if restorable(current_path):
                index = new_model.index(current_path)
                if index.isValid():
                    tree.setCurrentIndex(index)
        self.drive_model.refresh()
        if drive_path:
            drive_index = self.drive_model.index_for_path(drive_path)
            if drive_index.isValid():
                window.drive_list.setCurrentIndex(drive_index)
        self.sync_drive_sections()
        window._drive_list_fit_timer.start()

    def parent_folder_for_navigation(self) -> str:
        folder = self._window._current_folder if self._window._scope_kind == "folder" else ""
        if not folder:
            return ""
        if is_unc_path(folder):
            parts = str(folder).strip("\\").split("\\")
            if len(parts) <= 2:
                return ""
            if len(parts) == 3:
                return f"\\\\{parts[0]}\\{parts[1]}"
            return "\\\\" + "\\".join(parts[:-1])
        try:
            current = Path(folder)
            parent = current.parent
        except (OSError, ValueError):
            return ""
        if not str(parent) or parent == current:
            return ""
        return os.path.normpath(str(parent))

    def only_child_folder_for_navigation(self) -> str:
        if self._window._scope_kind != "folder" or not self._window._current_folder or len(self._window._folder_records) != 1:
            return ""
        child = self._window._folder_records[0]
        if not child.is_folder or not child.name:
            return ""
        return os.path.normpath(os.path.join(self._window._current_folder, child.name))

    def refresh_directory_navigation_buttons(self) -> None:
        parent_folder = self.parent_folder_for_navigation()
        child_folder = self.only_child_folder_for_navigation()
        child_count = len(self._window._folder_records) if self._window._scope_kind == "folder" and self._window._current_folder else 0

        up_tooltip = f"Open parent folder: {parent_folder}" if parent_folder else "Already at the top of this drive"
        if child_folder:
            down_tooltip = f"Open only child folder: {Path(child_folder).name}"
        elif child_count == 0:
            down_tooltip = "No child folders"
        else:
            down_tooltip = f"{child_count} child folders; choose one from the folder list"

        for button in getattr(self._window, "_directory_up_buttons", ()):
            button.setEnabled(bool(parent_folder))
            button.setToolTip(up_tooltip)
        for button in getattr(self._window, "_directory_down_buttons", ()):
            button.setEnabled(bool(child_folder))
            button.setToolTip(down_tooltip)
        topbar_up = getattr(self._window._toolbar, "_topbar_up_button", None)
        if topbar_up is not None:
            topbar_up.setEnabled(bool(parent_folder))
            topbar_up.setToolTip(up_tooltip)

    def navigate_to_parent_folder(self) -> None:
        target = self.parent_folder_for_navigation()
        if target:
            self.select_folder(target)

    def navigate_to_only_child_folder(self) -> None:
        target = self.only_child_folder_for_navigation()
        if target:
            self.select_folder(target)

    def update_nav_history_buttons(self) -> None:
        back_button = getattr(self._window._toolbar, "_topbar_back_button", None)
        if back_button is not None:
            back_button.setEnabled(bool(getattr(self._window._toolbar, "_nav_back", None)))
        forward_button = getattr(self._window._toolbar, "_topbar_forward_button", None)
        if forward_button is not None:
            forward_button.setEnabled(bool(getattr(self._window._toolbar, "_nav_forward", None)))

    def remember_recent_folder(self, folder: str) -> None:
        normalized = os.path.normpath(str(folder).strip())
        if not normalized:
            return
        normalized_key = _memory_path_key(normalized)
        self._window._recent_folders = [
            normalized,
            *[
                item
                for item in self._window._recent_folders
                if _memory_path_key(item) != normalized_key
            ],
        ][:12]
        self._window._settings_ctl.save_recent_folders()
        self.refresh_recent_folder_combos()

    def recent_folder_paths(self, *, exclude_current_folder: bool = False) -> list[str]:
        valid: list[str] = []
        seen: set[str] = set()
        for path in self._window._recent_folders:
            key = _memory_path_key(path)
            if key in seen:
                continue
            seen.add(key)
            valid.append(path)
        if valid != self._window._recent_folders:
            self._window._recent_folders = valid[:12]
            self._window._settings_ctl.save_recent_folders()
        if not exclude_current_folder or not self._window._current_folder:
            return valid
        current_key = _memory_path_key(self._window._current_folder)
        return [path for path in valid if _memory_path_key(path) != current_key]

    def open_recent_folder(self, folder: str) -> None:
        if not self._window._dir_confirmed_missing(folder):
            # A share that does not answer is not "gone": the scan worker opens it, or reports the failure
            # in the grid, and the entry stays in the list.
            self.select_folder(folder)
            return
        missing_key = _memory_path_key(folder)
        self._window._recent_folders = [
            path for path in self._window._recent_folders if _memory_path_key(path) != missing_key
        ]
        self._window._settings_ctl.save_recent_folders()
        self.refresh_recent_folder_combos()
        self._window.statusBar().showMessage("Recent folder no longer exists.")

    def refresh_recent_folder_combos(self) -> None:
        self._window._appearance.refresh_breadcrumb()
        current_text = self._window._projects.scope_display_label()
        current_folder = self._window._current_folder if self._window._scope_kind == "folder" and self._window._current_folder else ""
        for combo in (
            getattr(self._window, "manual_path_combo", None),
            getattr(self._window, "ai_path_combo", None),
            getattr(self._window, "topbar_path_combo", None),
        ):
            if combo is None:
                continue
            with QSignalBlocker(combo):
                combo.clear()
                combo.addItem(current_text, current_folder)
                recent_paths = self.recent_folder_paths(exclude_current_folder=True)
                if recent_paths:
                    combo.insertSeparator(combo.count())
                    for folder in recent_paths:
                        combo.addItem(folder, folder)
                combo.insertSeparator(combo.count())
                combo.addItem("Open Folder...", "__open_folder__")
                combo.setCurrentIndex(0)
                combo.setEditText(current_text)
            combo.setToolTip(current_text)
            line_edit = combo.lineEdit()
            if line_edit is not None:
                line_edit.setToolTip(current_text)
        self.refresh_directory_navigation_buttons()

    def handle_path_combo_activated(self, combo: QComboBox, index: int) -> None:
        value = combo.itemData(index)
        if value == "__open_folder__":
            self.refresh_recent_folder_combos()
            self.choose_folder()
            return
        if isinstance(value, str) and value:
            if self._window._current_folder and _memory_path_key(value) == _memory_path_key(self._window._current_folder):
                self.refresh_recent_folder_combos()
                return
            self.open_recent_folder(value)
            return
        self.refresh_recent_folder_combos()

    def handle_path_suggestion_accepted(self, folder: str) -> None:
        normalized = self._window._normalize_for_gui(folder)
        if not normalized or self._window._dir_confirmed_missing(normalized):
            self.refresh_recent_folder_combos()
            return
        if self._window._current_folder and _memory_path_key(normalized) == _memory_path_key(self._window._current_folder):
            self.refresh_recent_folder_combos()
            return
        self.select_folder(normalized)

    def commit_path_combo_text(self, combo: QComboBox) -> None:
        raw_text = combo.currentText().strip().strip('"')
        folder = self._window._normalize_for_gui(raw_text)
        if not folder:
            self.refresh_recent_folder_combos()
            return
        if self._window._dir_confirmed_missing(folder):
            self._window.statusBar().showMessage(f"Folder not found: {folder}")
            self.refresh_recent_folder_combos()
            return
        if self._window._current_folder and _memory_path_key(folder) == _memory_path_key(self._window._current_folder):
            self.refresh_recent_folder_combos()
            return
        self.select_folder(folder)

    def remember_recent_destination(self, destination_dir: str) -> None:
        normalized = self._window._normalize_for_gui(destination_dir)
        if not normalized or self._window._dir_confirmed_missing(normalized):
            return
        self._window._recent_destinations = [
            normalized,
            *[
                item
                for item in self._window._recent_destinations
                if _memory_path_key(item) != _memory_path_key(normalized)
            ],
        ][:10]
        self._window._settings_ctl.save_recent_destinations()

    def recent_destination_paths(self, *, exclude_current_folder: bool = False) -> list[str]:
        cleaned: list[str] = []
        seen: set[str] = set()
        for path in self._window._recent_destinations:
            if self._window._dir_confirmed_missing(path):
                continue
            normalized = _memory_path_key(path)
            if normalized in seen:
                continue
            seen.add(normalized)
            cleaned.append(path)
        # Only provably-gone folders and duplicates are ever dropped from the saved list. "Hide the folder
        # I am in" is a filter on what the menu shows: it used to be written back too, so merely opening
        # the Move-To menu erased the current folder from the saved destinations.
        if cleaned != self._window._recent_destinations:
            self._window._recent_destinations = cleaned[:10]
            self._window._settings_ctl.save_recent_destinations()
        if not exclude_current_folder or not self._window._current_folder:
            return cleaned
        current_key = _memory_path_key(self._window._current_folder)
        return [path for path in cleaned if _memory_path_key(path) != current_key]

    def refresh_favorites_panel(self) -> None:
        if not hasattr(self._window, "favorites_list"):
            return
        self._window.favorites_list.clear()
        for path in self._window._favorites:
            item = QListWidgetItem(Path(path).name or path)
            item.setToolTip(path)
            item.setData(Qt.ItemDataRole.UserRole, path)
            self._window.favorites_list.addItem(item)
        has_favorites = bool(self._window._favorites)
        self._window.favorites_label.setVisible(has_favorites)
        self._window.favorites_list.setVisible(has_favorites)
        self._window.favorites_divider.setVisible(has_favorites)
        self.update_favorites_height()

    def update_favorites_height(self) -> None:
        if not hasattr(self._window, "favorites_list"):
            return
        count = self._window.favorites_list.count()
        if count <= 0:
            self._window.favorites_list.setFixedHeight(0)
            return
        row_height = self._window.favorites_list.sizeHintForRow(0)
        if row_height <= 0:
            row_height = self._window.favorites_list.fontMetrics().height() + 12
        frame = self._window.favorites_list.frameWidth() * 2
        height = frame + (row_height * count)
        self._window.favorites_list.setFixedHeight(height)

    def add_favorite(self, folder: str) -> None:
        if not folder or self._window._dir_confirmed_missing(folder) or folder in self._window._favorites:
            return
        self._window._favorites.append(folder)
        self._window._settings_ctl.save_favorites()
        self.refresh_favorites_panel()
        self._window.statusBar().showMessage(f"Added to favorites: {folder}")

    def remove_favorite(self, folder: str) -> None:
        if folder not in self._window._favorites:
            return
        self._window._favorites = [path for path in self._window._favorites if path != folder]
        self._window._settings_ctl.save_favorites()
        self.refresh_favorites_panel()
        self._window.statusBar().showMessage(f"Removed from favorites: {folder}")

    def folder_tree_filter(self):
        filters = QDir.Filter.AllDirs | QDir.Filter.NoDotAndDotDot | QDir.Filter.Drives
        if self._window._show_hidden_folders:
            filters |= QDir.Filter.Hidden
        return filters

    def handle_show_hidden_folders_toggled(self, checked: bool) -> None:
        self._window._show_hidden_folders = bool(checked)
        self._window._settings.setValue(self._window.SHOW_HIDDEN_FOLDERS_KEY, self._window._show_hidden_folders)
        self._window.folder_model.setFilter(self.folder_tree_filter())
        if self._window._current_folder and self._window._scope_kind == "folder":
            current_path = self._window._records_view.current_visible_record_path()
            self._window._folder_records = scan_child_folders(
                self._window._current_folder,
                include_hidden=self._window._show_hidden_folders,
            )
            self._window._views.apply_records_view(current_path=current_path)
        self._window._inspector.update_action_states()
        state = "shown" if self._window._show_hidden_folders else "hidden"
        self._window.statusBar().showMessage(f"Hidden folders {state}")
