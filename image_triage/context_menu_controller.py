"""The grid's right-click menus. Extracted from MainWindow (docs/mainwindow_decomposition_plan.md, DC-4.4)."""
from __future__ import annotations

import os

from PySide6.QtCore import QDir, QMimeData, QObject, QUrl
from PySide6.QtGui import QAction, QActionGroup, QKeySequence
from PySide6.QtWidgets import QApplication, QFileDialog, QMenu
from pathlib import Path

from .models import ImageRecord, SortMode
from .scanner import normalized_path_key
from .shell_actions import open_in_file_explorer, open_in_photoshop, open_with_default, open_with_dialog, reveal_in_file_explorer

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .window import MainWindow


class ContextMenuController(QObject):
    """The grid's right-click menus. Extracted from MainWindow (docs/mainwindow_decomposition_plan.md, DC-4.4)."""

    def __init__(self, window: "MainWindow") -> None:
        super().__init__(window)
        self._window = window

    def add_recent_destination_actions(self, menu: QMenu, title: str) -> dict[QAction, str]:
        recent_menu = menu.addMenu(title)
        actions: dict[QAction, str] = {}
        for destination in self._window._navigation.recent_destination_paths(exclude_current_folder=True):
            label = Path(destination).name or destination
            action = recent_menu.addAction(f"{label}  [{destination}]")
            actions[action] = destination
        if not actions:
            empty_action = recent_menu.addAction("No recent folders")
            empty_action.setEnabled(False)
        return actions

    def add_send_to_actions(self, menu: QMenu) -> dict[str, object]:
        copy_menu = menu.addMenu("Copy...")
        copy_file_action = copy_menu.addAction("Copy File")
        copy_action = copy_menu.addAction("Copy To Folder...")
        copy_recent_actions = self.add_recent_destination_actions(copy_menu, "Copy To Recent")

        move_menu = menu.addMenu("Move...")
        move_action = move_menu.addAction("Move To Folder...")
        move_new_folder_action = move_menu.addAction("Move To New Folder...")
        move_recent_actions = self.add_recent_destination_actions(move_menu, "Move To Recent")

        archive_menu = menu.addMenu("Archive...")
        zip_action = archive_menu.addAction("ZIP Archive...")
        seven_zip_action = archive_menu.addAction("7-Zip Archive...")
        tar_gz_action = archive_menu.addAction("TAR.GZ Archive...")
        return {
            "copy_file_action": copy_file_action,
            "copy_action": copy_action,
            "copy_recent_actions": copy_recent_actions,
            "move_action": move_action,
            "move_new_folder_action": move_new_folder_action,
            "move_recent_actions": move_recent_actions,
            "zip_action": zip_action,
            "seven_zip_action": seven_zip_action,
            "tar_gz_action": tar_gz_action,
        }

    def copy_records_to_clipboard(self, records: list[ImageRecord], *, display_path: str = "") -> None:
        paths: list[str] = []
        seen: set[str] = set()
        candidates = [display_path] if display_path else [record.path for record in records]
        for path in candidates:
            if not path:
                continue
            normalized = normalized_path_key(path)
            if normalized in seen:
                continue
            seen.add(normalized)
            paths.append(path)
        if not paths:
            return

        mime_data = QMimeData()
        mime_data.setUrls([QUrl.fromLocalFile(path) for path in paths])
        mime_data.setText("\n".join(paths))
        QApplication.clipboard().setMimeData(mime_data)
        count = len(paths)
        self._window.statusBar().showMessage(f"Copied {count} file{'s' if count != 1 else ''} to clipboard")

    def show_grid_context_menu(self, index: int, global_pos) -> None:
        if index < 0:
            self.build_empty_grid_context_menu().exec(global_pos)
            return
        current_record = self._window._record_at(index)
        if current_record is not None and current_record.is_folder:
            menu = QMenu(self._window)
            open_action = menu.addAction(self._window._menu_text_with_hint("Open", "Space / Enter"))
            open_file_manager_label = "Open In File Explorer" if os.name == "nt" else "Open In File Manager"
            open_file_manager_action = menu.addAction(open_file_manager_label)
            reveal_label = "Reveal In File Explorer" if os.name == "nt" else "Reveal In File Manager"
            reveal_action = menu.addAction(reveal_label)
            menu.addSeparator()
            copy_path_action = menu.addAction("Copy Path")
            copy_name_action = menu.addAction("Copy Folder Name")

            chosen = menu.exec(global_pos)
            if chosen is None:
                return
            if chosen == open_action:
                self._window._navigation.select_folder(current_record.path)
                return
            if chosen == open_file_manager_action:
                open_in_file_explorer(current_record.path)
                return
            if chosen == reveal_action:
                reveal_in_file_explorer(current_record.path)
                return
            if chosen == copy_path_action:
                QApplication.clipboard().setText(current_record.path)
                return
            if chosen == copy_name_action:
                QApplication.clipboard().setText(current_record.name)
                return

        records = self._window._selected_records_for_context(index)
        if not records:
            return

        if len(records) > 1:
            menu = QMenu(self._window)
            restore_action = None
            accept_action = None
            reject_action = None
            keep_action = None
            photoshop_action = None
            if self._window._is_recycle_folder():
                restore_action = menu.addAction(f"Restore {len(records)} Images")
                menu.addSeparator()
            else:
                accept_action = menu.addAction(f"Mark {len(records)} Images As Winners")
                reject_action = menu.addAction(f"Reject {len(records)} Images")
                keep_action = menu.addAction(f"Move {len(records)} Images To _keep")
                menu.addSeparator()
            photoshop_action = menu.addAction(f"Open {len(records)} Images In Photoshop")
            photoshop_action.setEnabled(bool(self._window._photoshop_executable))
            if not self._window._photoshop_executable:
                photoshop_action.setText("Open In Photoshop (Not Found)")
            menu.addSeparator()
            send_to_actions = self.add_send_to_actions(menu)
            menu.addSeparator()
            delete_action = menu.addAction(f"Delete {len(records)} Images")

            chosen = menu.exec(global_pos)
            if chosen is None:
                return
            if restore_action is not None and chosen == restore_action:
                self._window._annotation_ctl.batch_restore_records(records)
                return
            if accept_action is not None and chosen == accept_action:
                self._window._annotation_ctl.batch_set_winner(records)
                return
            if reject_action is not None and chosen == reject_action:
                self._window._annotation_ctl.batch_set_reject(records)
                return
            if keep_action is not None and chosen == keep_action:
                self._window._annotation_ctl.batch_keep_records(records)
                return
            if chosen == photoshop_action:
                self._window._annotation_ctl.batch_open_in_photoshop(records)
                return
            if chosen == send_to_actions["copy_file_action"]:
                self.copy_records_to_clipboard(records)
                return
            if chosen == send_to_actions["copy_action"]:
                self._window._record_ops.batch_copy_records(records)
                return
            if chosen in send_to_actions["copy_recent_actions"]:
                self._window._record_ops.copy_selected_records_to_destination(send_to_actions["copy_recent_actions"][chosen])
                return
            if chosen == send_to_actions["move_action"]:
                self._window._record_ops.batch_move_records(records)
                return
            if chosen == send_to_actions["move_new_folder_action"]:
                self._window._record_ops.batch_move_records_to_new_folder(records)
                return
            if chosen in send_to_actions["move_recent_actions"]:
                self._window._record_ops.move_selected_records_to_destination(send_to_actions["move_recent_actions"][chosen])
                return
            if chosen == send_to_actions["zip_action"]:
                self._window._export_jobs.create_archive_for_records(records, "zip")
                return
            if chosen == send_to_actions["seven_zip_action"]:
                self._window._export_jobs.create_archive_for_records(records, "7z")
                return
            if chosen == send_to_actions["tar_gz_action"]:
                self._window._export_jobs.create_archive_for_records(records, "tar_gz")
                return
            if chosen == delete_action:
                self._window._record_ops.batch_delete_records(records)
                return
            return

        record = records[0]
        display_path = self._window.grid.displayed_variant_path(index) or record.path
        display_name = Path(display_path).name

        menu = QMenu(self._window)
        restore_action = None
        open_action = menu.addAction(self._window._menu_text_with_hint("Open", "Space / Enter"))
        open_with_menu = menu.addMenu("Open With")
        default_action = open_with_menu.addAction("Default App")
        open_with_action = open_with_menu.addAction("System Open With...")
        reveal_label = "Reveal In File Explorer" if os.name == "nt" else "Reveal In File Manager"
        reveal_action = menu.addAction(reveal_label)
        photoshop_action = menu.addAction("Open In Photoshop")
        photoshop_action.setEnabled(bool(self._window._photoshop_executable))
        if not self._window._photoshop_executable:
            photoshop_action.setText("Open In Photoshop (Not Found)")
        ai_result = self._window._ai_run.ai_result_for_index(index)
        compare_ai_group_action = None
        jump_ai_pick_action = None
        if ai_result is not None:
            menu.addSeparator()
            if ai_result.group_size > 1:
                compare_ai_group_action = menu.addAction(
                    self._window._toolbar_menus.menu_text_with_action_shortcut("Compare AI Group", self._window.actions.compare_ai_group if self._window.actions else None)
                )
                jump_ai_pick_action = menu.addAction(
                    self._window._toolbar_menus.menu_text_with_action_shortcut("Jump To AI Top Pick", self._window.actions.next_ai_pick if self._window.actions else None)
                )
        if self._window._is_recycle_folder():
            menu.addSeparator()
            restore_action = menu.addAction("Restore")
        else:
            menu.addSeparator()
        rename_action = menu.addAction(
            self._window._toolbar_menus.menu_text_with_action_shortcut("Rename...", self._window.actions.rename_selection if self._window.actions else None)
        )
        rename_action.setEnabled(not self._window._is_recycle_folder() and not self._window._is_winners_folder())
        resize_action = menu.addAction("Resize...")
        resize_action.setEnabled(not self._window._is_recycle_folder() and self._window._export_jobs.record_supports_resize(record))
        convert_action = menu.addAction("Convert...")
        convert_action.setEnabled(not self._window._is_recycle_folder() and self._window._export_jobs.record_supports_convert(record))
        menu.addSeparator()
        send_to_actions = self.add_send_to_actions(menu)
        menu.addSeparator()
        copy_path_action = menu.addAction("Copy Path")
        copy_name_action = menu.addAction("Copy Filename")
        menu.addSeparator()
        delete_action = menu.addAction(self._window._menu_text_with_hint("Delete", "Del"))

        chosen = menu.exec(global_pos)
        if chosen is None:
            return
        if chosen == open_action:
            open_with_default(display_path)
            return
        if compare_ai_group_action is not None and chosen == compare_ai_group_action:
            self._window._ai_run.open_current_ai_group_compare(index)
            return
        if jump_ai_pick_action is not None and chosen == jump_ai_pick_action:
            self._window._ai_run.jump_to_ai_top_pick_in_group(index)
            return
        if restore_action is not None and chosen == restore_action:
            self._window._record_ops.restore_record(index)
            return
        if chosen == rename_action:
            self._window._record_ops.rename_record_prompt(index)
            return
        if chosen == resize_action:
            self._window._export_jobs.resize_record_prompt(index)
            return
        if chosen == convert_action:
            self._window._export_jobs.convert_record_prompt(index)
            return
        if chosen == photoshop_action and self._window._photoshop_executable:
            open_in_photoshop(display_path)
            return
        if chosen == send_to_actions["copy_file_action"]:
            self.copy_records_to_clipboard(records, display_path=display_path)
            return
        if chosen == send_to_actions["copy_action"]:
            destination_dir = QFileDialog.getExistingDirectory(self._window, "Copy Image", self._window._current_folder or QDir.homePath())
            if destination_dir:
                self._window._record_ops.copy_record_to(index, destination_dir)
            return
        if chosen in send_to_actions["copy_recent_actions"]:
            self._window._record_ops.copy_record_to(index, send_to_actions["copy_recent_actions"][chosen])
            return
        if chosen == send_to_actions["move_action"]:
            self._window._record_ops.move_record_prompt(index)
            return
        if chosen == send_to_actions["move_new_folder_action"]:
            self._window._record_ops.batch_move_records_to_new_folder(records)
            return
        if chosen in send_to_actions["move_recent_actions"]:
            self._window._record_ops.move_record_to(index, send_to_actions["move_recent_actions"][chosen])
            return
        if chosen == send_to_actions["zip_action"]:
            self._window._export_jobs.create_archive_for_records(records, "zip")
            return
        if chosen == send_to_actions["seven_zip_action"]:
            self._window._export_jobs.create_archive_for_records(records, "7z")
            return
        if chosen == send_to_actions["tar_gz_action"]:
            self._window._export_jobs.create_archive_for_records(records, "tar_gz")
            return
        if chosen == delete_action:
            self._window._annotation_ctl.delete_record(index)
            return
        if chosen == reveal_action:
            reveal_in_file_explorer(display_path)
            return
        if chosen == copy_path_action:
            QApplication.clipboard().setText(display_path)
            return
        if chosen == copy_name_action:
            QApplication.clipboard().setText(display_name)
            return
        if chosen == default_action:
            open_with_default(display_path)
            return
        if chosen == open_with_action:
            open_with_dialog(display_path)
            return

    def build_empty_grid_context_menu(self) -> QMenu:
        menu = QMenu(self._window)
        menu.setObjectName("emptyGridContextMenu")

        menu.addAction(self._window.actions.clear_filters)
        clear_search = menu.addAction("Clear Search")
        clear_search.setEnabled(
            bool(self._window._filter_query.search_text.strip() or self._window._pending_search_text.strip())
        )
        clear_search.triggered.connect(self._window._records_view.clear_search_from_workspace_menu)

        menu.addSeparator()
        menu.addAction(self._window.actions.refresh_folder)

        menu.addSeparator()
        select_all = menu.addAction("Select All")
        select_all.setShortcut(QKeySequence.StandardKey.SelectAll)
        select_all.setShortcutVisibleInContextMenu(True)
        visible_count = self._window.grid.visible_item_count()
        selected_count = self._window.grid.selected_count()
        select_all.setEnabled(visible_count > 0 and selected_count < visible_count)
        select_all.triggered.connect(self._window.grid.select_all)

        deselect_all = menu.addAction("Deselect All")
        deselect_all.setEnabled(selected_count > 0)
        deselect_all.triggered.connect(lambda: self._window.grid.clear_selection(keep_current=True))

        menu.addSeparator()
        view_menu = QMenu("View", menu)
        menu.addMenu(view_menu)
        show_filenames = view_menu.addAction("Show Filenames")
        show_filenames.setCheckable(True)
        show_filenames.setChecked(self._window._effective_loupe_card_style != "zen")
        show_filenames.setEnabled(
            self._window._browser_view_mode == "grid" and "gallery" in self._window._display.allowed_card_styles()
        )
        show_filenames.toggled.connect(self._window._display.set_grid_filenames_visible)
        view_menu.addSeparator()
        view_menu.addAction(self._window.actions.grid_view)
        view_menu.addAction(self._window.actions.details_view)

        sort_menu = QMenu("Sort By", menu)
        menu.addMenu(sort_menu)
        sort_group = QActionGroup(sort_menu)
        sort_group.setExclusive(True)
        for mode in SortMode:
            sort_action = sort_menu.addAction(mode.value)
            sort_action.setCheckable(True)
            sort_group.addAction(sort_action)
            sort_action.setChecked(self._window._sort_mode == mode)
            if mode == SortMode.AI_RANK:
                sort_action.setEnabled(self._window._ai_bundle is not None)
            elif mode == SortMode.AI_WOW:
                sort_action.setEnabled(bool(self._window._winner_scores_by_path))
            sort_action.triggered.connect(
                lambda _checked=False, selected=mode: self._window._views.set_sort_mode(selected)
            )

        menu.addSeparator()
        open_folder_label = "Open Current Folder In File Explorer" if os.name == "nt" else "Open Current Folder In File Manager"
        open_folder = menu.addAction(open_folder_label)
        open_folder.setEnabled(bool(self._window._current_folder and not self._window._dir_confirmed_missing(self._window._current_folder)))
        open_folder.triggered.connect(self._window._open_current_folder_in_file_manager)
        return menu
