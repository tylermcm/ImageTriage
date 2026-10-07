"""How the browser shows photos: sort mode, column count and zoom, grid/details/compare view modes, details-row density, details selection, and the view toggles (auto-advance, burst groups, stacks, compare, auto-bracket). Extracted from MainWindow (docs/mainwindow_decomposition_plan.md, DC-4.4)."""
from __future__ import annotations

from PySide6.QtCore import QObject, QSignalBlocker

from .models import ImageRecord, SessionAnnotation, SortMode
from .records_view_cache import ViewInvalidationReason

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .window import MainWindow


class ViewController(QObject):
    """How the browser shows photos: sort mode, column count and zoom, grid/details/compare view modes, details-row density, details selection, and the view toggles (auto-advance, burst groups, stacks, compare, auto-bracket). Extracted from MainWindow (docs/mainwindow_decomposition_plan.md, DC-4.4)."""

    def __init__(self, window: "MainWindow") -> None:
        super().__init__(window)
        self._window = window
        self._syncing_browser_selection = False

    def handle_sort_changed(self) -> None:
        selected = self.selected_sort_mode()
        if selected is None:
            return
        self.set_sort_mode(selected)

    def set_sort_mode(self, mode: SortMode) -> None:
        self._window._sort_mode = mode
        if mode == SortMode.AI_WOW:
            self._window._scan.refresh_winner_scores_for_current_folder()
        self._window._records_view_cache.mark(ViewInvalidationReason.SORT_CHANGED)
        combo_index = self._window.sort_combo.findData(mode)
        if combo_index >= 0 and combo_index != self._window.sort_combo.currentIndex():
            self._window.sort_combo.setCurrentIndex(combo_index)
            return
        self.apply_records_view()
        self.scroll_active_view_to_top()
        self._window._settings_ctl.remember_current_folder_view_state()
        self._window._inspector.update_action_states()

    def columns_to_zoom_slider_value(self, columns: object) -> int:
        # Slider remains left = smaller/more columns, right = larger/fewer columns.
        normalized = self._window._normalize_column_count(columns)
        return int(round(((8 - normalized) / 7) * 100))

    def set_column_count(self, count: int, *, sync_slider: bool = True) -> None:
        columns = self._window._normalize_column_count(count)
        combo_index = self._window.columns_combo.findData(columns)
        if combo_index >= 0:
            with QSignalBlocker(self._window.columns_combo):
                self._window.columns_combo.setCurrentIndex(combo_index)
        if self._window.grid.current_columns() == columns and self._window.grid.zoom_mode() == "column":
            if sync_slider:
                self.sync_zoom_slider_from_grid()
            self._window._inspector.update_action_states()
            return
        self._window.grid.set_column_count(columns)
        self._window._settings.setValue(self._window.VIEW_COLUMNS_KEY, columns)
        # A discrete column choice clears any continuous zoom level.
        self._window._settings.remove(self._window.VIEW_ZOOM_WIDTH_KEY)
        if sync_slider:
            self.sync_zoom_slider_from_grid()
        self._window._settings_ctl.remember_current_folder_view_state()
        self._window._inspector.update_action_states()

    def sync_zoom_slider_from_grid(self) -> None:
        slider = getattr(self._window._toolbar, "topbar_zoom_slider", None)
        if slider is None:
            return
        value = self.columns_to_zoom_slider_value(self._window.grid.current_columns())
        if slider.value() != value:
            with QSignalBlocker(slider):
                slider.setValue(value)

    def set_browser_view_mode(self, mode: str) -> None:
        normalized = self._window._normalize_browser_view_mode(mode)
        if self._window._collection_mode and normalized != "grid":
            return
        if self._window._browser_view_mode == normalized and getattr(self._window, "browser_stack", None) is not None:
            self._window.browser_stack.setCurrentIndex(1 if normalized == "details" else 0)
            self.sync_details_view_from_grid()
            self._window._inspector.update_action_states()
            return
        current_index = self._window.grid.current_index()
        selected_indexes = self._window.grid.selected_indexes()
        self._window._folder_session.browser_view_mode = normalized
        self._window._settings.setValue(self._window.BROWSER_VIEW_MODE_KEY, normalized)
        if getattr(self._window, "browser_stack", None) is not None:
            self._window.browser_stack.setCurrentIndex(1 if normalized == "details" else 0)
        if normalized == "details":
            self._window.details_view.set_selected_indexes(selected_indexes, current_index=current_index)
            self._window.details_view.table.setFocus()
        else:
            self._window.grid.setFocus()
            self._window.grid.schedule_visible_thumbnail_requests()
        self._window._inspector.update_action_states()

    def set_details_row_density(self, density: str) -> None:
        normalized = self._window._normalize_details_row_density(density)
        self._window._details_row_density = normalized
        self._window._settings.setValue(self._window.DETAILS_ROW_DENSITY_KEY, normalized)
        self._window.details_view.set_row_density(normalized)
        label = "compact" if normalized == "compact" else "comfortable"
        self._window.statusBar().showMessage(f"Details row density set to {label}")
        self._window._inspector.update_action_states()

    def sync_details_view_from_grid(self) -> None:
        if getattr(self._window, "details_view", None) is None or self._syncing_browser_selection:
            return
        if self._window._browser_view_mode != "details":
            return
        self._syncing_browser_selection = True
        try:
            self._window.details_view.set_selected_indexes(
                self._window.grid.selected_indexes(),
                current_index=self._window.grid.current_index(),
            )
        finally:
            self._syncing_browser_selection = False

    def handle_details_current_changed(self, index: int) -> None:
        if self._syncing_browser_selection:
            return
        if not 0 <= index < len(self._window._records):
            return
        selected_indexes = self._window.details_view.selected_indexes()
        if index not in selected_indexes:
            selected_indexes = [index]
        self._syncing_browser_selection = True
        try:
            self._window.grid.set_logical_selection(selected_indexes, current_index=index)
        finally:
            self._syncing_browser_selection = False
        self._window._inspector.update_action_states()
        self._window._inspector.update_status(index=index)
        self._window._inspector.update_inspector_context(index)

    def handle_details_selection_changed(self) -> None:
        if self._syncing_browser_selection:
            return
        current_index = self._window.details_view.current_index()
        selected_indexes = self._window.details_view.selected_indexes()
        self._syncing_browser_selection = True
        try:
            self._window.grid.set_logical_selection(selected_indexes, current_index=current_index)
        finally:
            self._syncing_browser_selection = False
        self._window._inspector.update_action_states()
        self._window._inspector.update_status(index=current_index)
        self._window._inspector.update_inspector_context(current_index)

    def jump_details_to_review_state(self, target: str) -> None:
        if not self._window._records:
            return
        start = self._window.grid.current_index()
        total = len(self._window._records)

        def matches(record: ImageRecord) -> bool:
            annotation = self._window._annotations.get(record.path, SessionAnnotation())
            if target == "kept":
                return annotation.winner and not record.is_folder
            if target == "rejected":
                return annotation.reject and not record.is_folder
            return not annotation.winner and not annotation.reject and not record.is_folder

        for offset in range(1, total + 1):
            index = (max(0, start) + offset) % total
            record = self._window._record_at(index)
            if record is not None and matches(record):
                if self._window._browser_view_mode != "details":
                    self.set_browser_view_mode("details")
                self._window.details_view.set_selected_indexes([index], current_index=index)
                self._window.grid.set_logical_selection([index], current_index=index)
                self._window._inspector.update_status(index=index)
                self._window.statusBar().showMessage(f"Details jumped to {record.name}")
                return
        label = {"kept": "kept", "rejected": "rejected"}.get(target, "unreviewed")
        self._window.statusBar().showMessage(f"No {label} image found in Details View")

    def scroll_active_view_to_top(self) -> None:
        if getattr(self._window, "_browser_view_mode", "grid") == "details":
            self._window.details_view.table.scrollToTop()
        else:
            self._window.grid.verticalScrollBar().setValue(0)

    def set_annotation_views(self, changed_paths: list[str] | tuple[str, ...] | set[str] | None = None) -> None:
        if changed_paths:
            if getattr(self._window.grid, "_annotations", None) is not self._window._annotations:
                self._window.grid.set_annotations(self._window._annotations)
                self._window.details_view.set_annotations(self._window._annotations)
            self._window.grid.update_annotations(changed_paths)
            changed_rows = {
                self._window._record_index_by_path[path]
                for path in changed_paths
                if path in self._window._record_index_by_path
            }
            self._window.details_view.refresh_rows(changed_rows)
            if self._window._preview_ctl.preview_is_visible():
                for path in changed_paths:
                    annotation = self._window._annotations.get(path, SessionAnnotation())
                    self._window.preview.set_annotation_state(path, annotation.winner, annotation.reject, annotation.rating)
            return
        self._window.grid.set_annotations(self._window._annotations)
        self._window.details_view.set_annotations(self._window._annotations)

    def refresh_viewport_mode(self) -> None:
        return

    def selected_sort_mode(self) -> SortMode | None:
        selected = self._window.sort_combo.currentData()
        if isinstance(selected, SortMode):
            return selected
        if isinstance(selected, str):
            for mode in SortMode:
                if selected in {mode.name, mode.value}:
                    return mode
                try:
                    if SortMode(selected) == mode:
                        return mode
                except ValueError:
                    continue
        text = self._window.sort_combo.currentText()
        for mode in SortMode:
            if text == mode.value:
                return mode
        return None

    def scroll_current_to_top(self, path: str) -> None:
        index = self._window._record_index_by_path.get(path)
        if index is not None:
            self._window.grid.scroll_index_to_top(index)

    def restore_pending_folder_scroll(self) -> None:
        if self._window._pending_folder_scroll_value is None:
            return
        value = self._window._pending_folder_scroll_value
        self._window._pending_folder_scroll_value = None
        self._window.grid.restore_scroll_value(value)

    def handle_columns_changed(self) -> None:
        columns = self._window._normalize_column_count(self._window.columns_combo.currentData())
        self.set_column_count(columns)

    def handle_auto_advance_toggled(self, checked: bool) -> None:
        self._window._auto_advance_enabled = checked
        self._window._settings.setValue(self._window.AUTO_ADVANCE_KEY, checked)
        preview = self._window._preview_ctl.preview_if_built()
        if preview is not None:
            preview.set_auto_advance_enabled(checked)
        self._window._inspector.update_action_states()
        mode = "on" if checked else "off"
        self._window.statusBar().showMessage(f"Auto-advance {mode}")

    def handle_burst_groups_toggled(self, checked: bool) -> None:
        self._window._burst_groups_enabled = checked
        self._window._settings.setValue(self._window.BURST_GROUPS_KEY, checked)
        self._window._annotation_ctl.refresh_burst_group_view()
        self._window._inspector.update_action_states()
        group_count = len(self._window._visible_burst_groups)
        if checked and group_count:
            self._window.statusBar().showMessage(f"Smart groups on ({group_count} group(s) in the current view)")
            return
        mode = "on" if checked else "off"
        self._window.statusBar().showMessage(f"Smart groups {mode}")

    def handle_burst_stacks_toggled(self, checked: bool) -> None:
        self._window._burst_stacks_enabled = checked
        self._window._settings.setValue(self._window.BURST_STACKS_KEY, checked)
        self._window._annotation_ctl.refresh_burst_group_view()
        self._window._inspector.update_action_states()
        group_count = len(self._window._visible_burst_groups)
        if checked and group_count:
            self._window.statusBar().showMessage(f"Smart stacks on ({group_count} stack(s) in the current view)")
            return
        mode = "on" if checked else "off"
        self._window.statusBar().showMessage(f"Smart stacks {mode}")

    def handle_compare_toggled(self, checked: bool) -> None:
        if not checked and self._window._winner_ladder_state is not None:
            self._window._preview_ctl.finish_winner_ladder(reopen_preview=False, show_message=False)
        self._window._compare_enabled = checked
        preview = self._window._preview_ctl.preview_if_built()
        if preview is not None:
            preview.set_compare_mode(checked)
        self._window._inspector.update_action_states()
        mode = "on" if checked else "off"
        self._window.statusBar().showMessage(f"Compare {mode}")
        if self._window._preview_ctl.preview_is_visible():
            index = self._window.grid.current_index()
            if index >= 0:
                self._window._preview_ctl.open_preview(index)

    def handle_auto_bracket_toggled(self, checked: bool) -> None:
        self._window._auto_bracket_enabled = checked
        self._window._settings.setValue(self._window.AUTO_BRACKET_KEY, checked)
        preview = self._window._preview_ctl.preview_if_built()
        if preview is not None:
            preview.set_auto_bracket_mode(checked)
        mode = "on" if checked else "off"
        self._window.statusBar().showMessage(f"Auto-bracket compare {mode}")
        if self._window._preview_ctl.preview_is_visible() and self._window._compare_enabled:
            index = self._window.grid.current_index()
            if index >= 0:
                self._window._preview_ctl.open_preview(index)

    def apply_records_view(
        self,
        current_path: str | None = None,
        *,
        chunked: bool = False,
        post_load_enrichment: str = "",
    ) -> bool:
        return self._window._records_view.apply_records_view(
            current_path,
            chunked=chunked,
            post_load_enrichment=post_load_enrichment,
        )
