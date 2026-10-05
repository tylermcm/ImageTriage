"""The batch tool modes (rename, resize, convert): entering and leaving a mode, the mode bar, and running the active mode on the selection. Extracted from MainWindow (docs/mainwindow_decomposition_plan.md, DC-4.5)."""
from __future__ import annotations

from PySide6.QtCore import QObject

from .image_convert import ConvertSourceItem
from .image_resize import ResizeSourceItem
from .models import ImageRecord
from .ui import BatchRenameDialog

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .window import MainWindow


class ToolModeController(QObject):
    """The batch tool modes (rename, resize, convert): entering and leaving a mode, the mode bar, and running the active mode on the selection. Extracted from MainWindow (docs/mainwindow_decomposition_plan.md, DC-4.5)."""

    def __init__(self, window: "MainWindow") -> None:
        super().__init__(window)
        self._window = window

    def open_batch_rename_dialog(
        self,
        records: list[ImageRecord],
        *,
        title: str,
        scope_label: str,
        folder: str,
    ) -> bool:
        if not records:
            return False
        dialog = BatchRenameDialog(records, title=title, scope_label=scope_label, parent=self._window)
        if self._window._exec_dialog_with_geometry(dialog, "batch_rename") != dialog.DialogCode.Accepted:
            return False
        preview = dialog.accepted_preview()
        if not preview.can_apply:
            return False
        return self._window._batch_rename.apply_preview(preview, folder=folder)

    def start_batch_rename_tool_mode(self) -> None:
        if not self._window._current_folder or not self._window._all_records or self._window._is_recycle_folder() or self._window._is_winners_folder():
            return
        if self._window._active_tool_mode == "batch_rename" and self._window.grid.tool_checkbox_mode():
            self._window.statusBar().showMessage("Batch Rename tool is already active.")
            return
        if self._window._active_tool_mode and self._window._active_tool_mode != "batch_rename":
            self.cancel_tool_mode(show_message=False)
        self._window._active_tool_mode = "batch_rename"
        self._window.grid.set_tool_checkbox_mode(True, clear_selection=True)
        self.refresh_tool_mode_ui()
        self._window.statusBar().showMessage("Batch Rename tool active. Use the top-left checkboxes to choose images, then click Run.")

    def start_batch_resize_tool_mode(self) -> None:
        if not self._window._current_folder or not self._window._all_records or self._window._is_recycle_folder():
            return
        if not any(self._window._export_jobs.record_supports_resize(record) for record in self._window._all_records):
            return
        if self._window._active_tool_mode == "batch_resize" and self._window.grid.tool_checkbox_mode():
            self._window.statusBar().showMessage("Batch Resize tool is already active.")
            return
        if self._window._active_tool_mode and self._window._active_tool_mode != "batch_resize":
            self.cancel_tool_mode(show_message=False)
        self._window._active_tool_mode = "batch_resize"
        self._window.grid.set_tool_checkbox_mode(True, clear_selection=True, toggle_on_image_click=True)
        self.refresh_tool_mode_ui()
        self._window.statusBar().showMessage("Batch Resize tool active. Click thumbnails or checkboxes to choose images, then click Run.")

    def start_batch_convert_tool_mode(self) -> None:
        if not self._window._current_folder or not self._window._all_records or self._window._is_recycle_folder():
            return
        if not any(self._window._export_jobs.record_supports_convert(record) for record in self._window._all_records):
            return
        if self._window._active_tool_mode == "batch_convert" and self._window.grid.tool_checkbox_mode():
            self._window.statusBar().showMessage("Batch Convert tool is already active.")
            return
        if self._window._active_tool_mode and self._window._active_tool_mode != "batch_convert":
            self.cancel_tool_mode(show_message=False)
        self._window._active_tool_mode = "batch_convert"
        self._window.grid.set_tool_checkbox_mode(True, clear_selection=True)
        self.refresh_tool_mode_ui()
        self._window.statusBar().showMessage("Batch Convert tool active. Use the top-left checkboxes to choose images, then click Run.")

    def add_all_for_active_tool_mode(self) -> None:
        if self._window._active_tool_mode != "batch_resize":
            return
        indexes = [
            index
            for index, record in enumerate(self._window._records)
            if self._window._export_jobs.record_supports_resize(record)
        ]
        if not indexes:
            self._window.statusBar().showMessage("No resize-eligible images are available in this folder.")
            return
        current_index = self._window.grid.current_index()
        if current_index not in indexes:
            current_index = indexes[0]
        self._window.grid.set_selected_indexes(indexes, current_index=current_index)
        self.refresh_tool_mode_ui()
        self._window.statusBar().showMessage(f"Added {len(indexes)} resize-eligible image(s).")

    def run_active_tool_mode(self) -> None:
        if self._window._active_tool_mode == "batch_rename":
            records = self.selected_records_for_tool_mode()
            if not records:
                return
            scope_label = f"Tool selection: {len(records)} image bundle(s)"
            applied = self.open_batch_rename_dialog(
                records,
                title="Batch Rename Selection",
                scope_label=scope_label,
                folder=self._window._current_folder,
            )
            if applied and self._window._active_tool_mode:
                self.cancel_tool_mode(show_message=False)
            return
        if self._window._active_tool_mode == "batch_resize":
            selected_records = self.selected_records_for_tool_mode()
            sources = self.selected_resize_sources_for_tool_mode()
            if not sources:
                self._window.statusBar().showMessage("Batch Resize skips RAW files. Select one or more non-RAW images.")
                return
            skipped_raw_count = max(0, len(selected_records) - len(sources))
            scope_label = f"Tool selection: {len(sources)} image(s)"
            raw_note = "Resize can't be used on RAW files."
            if skipped_raw_count:
                scope_label = (
                    f"{scope_label}\n"
                    f"{skipped_raw_count} RAW file(s) were skipped because resize can't be used on RAW files."
                )
            applied = self._window._export_jobs.open_resize_dialog(
                sources,
                title="Batch Resize Selection",
                scope_label=scope_label,
                show_preview=True,
                raw_note=raw_note,
            )
            if applied and self._window._active_tool_mode:
                self.cancel_tool_mode(show_message=False)
            return
        if self._window._active_tool_mode != "batch_convert":
            return
        selected_records = self.selected_records_for_tool_mode()
        sources = self.selected_convert_sources_for_tool_mode()
        if not sources:
            self._window.statusBar().showMessage("Batch Convert skips RAW files. Select one or more non-RAW images.")
            return
        skipped_raw_count = max(0, len(selected_records) - len(sources))
        scope_label = f"Tool selection: {len(sources)} image(s)"
        raw_note = "Convert can't be used on RAW files."
        if skipped_raw_count:
            scope_label = (
                f"{scope_label}\n"
                f"{skipped_raw_count} RAW file(s) were skipped because convert can't be used on RAW files."
            )
        applied = self._window._export_jobs.open_convert_dialog(
            sources,
            title="Batch Convert Selection",
            scope_label=scope_label,
            show_preview=True,
            raw_note=raw_note,
        )
        if applied and self._window._active_tool_mode:
            self.cancel_tool_mode(show_message=False)

    def cancel_tool_mode(self, checked: bool = False, *, show_message: bool = True) -> None:
        del checked
        if not self._window._active_tool_mode and not self._window.grid.tool_checkbox_mode():
            return
        self._window._active_tool_mode = ""
        self._window.grid.set_tool_checkbox_mode(False, clear_selection=True)
        self.refresh_tool_mode_ui()
        if show_message:
            self._window.statusBar().showMessage("Exited tool selection mode")

    def refresh_tool_mode_ui(self) -> None:
        active = bool(self._window._active_tool_mode)
        self._window.tool_mode_bar.setVisible(active)
        if not active:
            return
        selected_count = len(self.selected_records_for_tool_mode())
        if self._window._active_tool_mode == "batch_rename":
            self._window.tool_mode_add_all_button.hide()
            self._window.tool_mode_title.setText("Batch Rename")
            self._window.tool_mode_help.setText("Select images with the checkboxes, then run the rename tool.")
            self._window.tool_mode_run_button.setText("Run Batch Rename")
            self._window.tool_mode_selection.setText(f"{selected_count} selected")
            self._window.tool_mode_run_button.setEnabled(selected_count > 0)
        elif self._window._active_tool_mode == "batch_resize":
            eligible_count = len(self.selected_resize_sources_for_tool_mode())
            skipped_raw_count = max(0, selected_count - eligible_count)
            total_eligible_count = sum(1 for record in self._window._records if self._window._export_jobs.record_supports_resize(record))
            self._window.tool_mode_add_all_button.show()
            self._window.tool_mode_add_all_button.setEnabled(total_eligible_count > 0)
            self._window.tool_mode_title.setText("Batch Resize")
            self._window.tool_mode_help.setText("Click thumbnails or checkboxes to select images, then run the resize tool. RAW files are skipped.")
            self._window.tool_mode_run_button.setText("Run Batch Resize")
            if skipped_raw_count:
                self._window.tool_mode_selection.setText(f"{eligible_count} eligible | {skipped_raw_count} RAW skipped")
            else:
                self._window.tool_mode_selection.setText(f"{eligible_count} eligible")
            self._window.tool_mode_run_button.setEnabled(eligible_count > 0)
        elif self._window._active_tool_mode == "batch_convert":
            self._window.tool_mode_add_all_button.hide()
            eligible_count = len(self.selected_convert_sources_for_tool_mode())
            skipped_raw_count = max(0, selected_count - eligible_count)
            self._window.tool_mode_title.setText("Batch Convert")
            self._window.tool_mode_help.setText("Select images with the checkboxes, then run the convert tool. RAW files are skipped.")
            self._window.tool_mode_run_button.setText("Run Batch Convert")
            if skipped_raw_count:
                self._window.tool_mode_selection.setText(f"{eligible_count} eligible | {skipped_raw_count} RAW skipped")
            else:
                self._window.tool_mode_selection.setText(f"{eligible_count} eligible")
            self._window.tool_mode_run_button.setEnabled(eligible_count > 0)
        else:
            self._window.tool_mode_add_all_button.hide()
            self._window.tool_mode_title.setText("Tool")
            self._window.tool_mode_help.setText("Select images, then run the active tool.")
            self._window.tool_mode_run_button.setText("Run")
            self._window.tool_mode_selection.setText(f"{selected_count} selected")
            self._window.tool_mode_run_button.setEnabled(selected_count > 0)

    def selected_records_for_tool_mode(self) -> list[ImageRecord]:
        return [
            self._window._records[index]
            for index in self._window.grid.selected_indexes()
            if 0 <= index < len(self._window._records) and not self._window._records[index].is_folder
        ]

    def selected_resize_sources_for_tool_mode(self) -> list[ResizeSourceItem]:
        return [
            source
            for index in self._window.grid.selected_indexes()
            if 0 <= index < len(self._window._records)
            for source in [self._window._export_jobs.resize_source_for_index(index)]
            if source is not None
        ]

    def selected_convert_sources_for_tool_mode(self) -> list[ConvertSourceItem]:
        return [
            source
            for index in self._window.grid.selected_indexes()
            if 0 <= index < len(self._window._records)
            for source in [self._window._export_jobs.convert_source_for_index(index)]
            if source is not None
        ]
