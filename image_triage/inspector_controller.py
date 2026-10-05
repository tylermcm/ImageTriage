"""The inspector and status line: current-photo context, thumbnails and inspection stats, face and category previews, the status bar and the per-record insight lookups. Extracted from MainWindow (docs/mainwindow_decomposition_plan.md, DC-4.4)."""
from __future__ import annotations

import logging
import os
import time

from PySide6.QtCore import QObject, QRect, QSignalBlocker
from PySide6.QtGui import QAction, QImage
from queue import Empty, SimpleQueue

from .aiculler_workflow import aiculler_db_path
from .models import ImageRecord, SessionAnnotation
from .perf import perf_logger
from .prefilter_common import PrefilterDecision
from .records_view_controller import _memory_path_key
from .review_tools import InspectionStats
from .scanner import normalized_path_key
from .tasks.annotation_tasks import InspectorStatsRequest, InspectorStatsTask

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .window import MainWindow

_logger = logging.getLogger(__name__)


class InspectorController(QObject):
    """The inspector and status line: current-photo context, thumbnails and inspection stats, face and category previews, the status bar and the per-record insight lookups. Extracted from MainWindow (docs/mainwindow_decomposition_plan.md, DC-4.4)."""

    def __init__(self, window: "MainWindow") -> None:
        super().__init__(window)
        self._window = window
        self._face_cycle_index_by_path: dict[str, int] = {}
        self._inspection_stats_pending_keys: set[tuple[str, int, int, int, int]] = set()
        self._inspection_stats_result_queue: SimpleQueue = SimpleQueue()

    def update_selection_count_labels(self) -> None:
        count = self._window.grid.selected_count() if self._window._records else 0
        text = f"{count} selected"
        tooltip = f"{count} selected image{'s' if count != 1 else ''}"
        for label in (
            getattr(self._window, "manual_selection_count_label", None),
            getattr(self._window, "ai_selection_count_label", None),
        ):
            if label is None:
                continue
            label.setText(text)
            label.setToolTip(tooltip)
        self._window._toolbar.schedule_workspace_toolbar_overflow_update("manual")
        self._window._toolbar.schedule_workspace_toolbar_overflow_update("ai")

    def update_action_states(self, *, probe_folder_ai: bool | None = None) -> None:
        logger = perf_logger()
        start = time.perf_counter() if logger.enabled else 0.0
        if self._window.actions is None:
            return
        if probe_folder_ai is None:
            probe_folder_ai = not self._window._scan_in_progress

        current_index = self._window.grid.current_index()
        selected_records = self._window._selected_records_for_context(current_index) if current_index >= 0 else []
        has_selection = bool(selected_records)
        current_record = self._window._record_at(current_index)
        in_recycle_folder = self._window._is_recycle_folder()
        in_winners_folder = self._window._is_winners_folder()
        has_physical_folder = bool(self._window._current_folder)
        collections = self._window._library_store.list_collections()
        catalog_roots = self._window._library_store.list_catalog_roots()
        display_path = ""
        if current_record is not None and current_index >= 0:
            display_path = self._window.grid.displayed_variant_path(current_index) or current_record.path
        current_workflow = self.workflow_insight_for_record(current_record)
        can_open_winner_ladder = self._window._preview_ctl.winner_ladder_candidate_count(current_index) >= 2

        self._window.actions.undo.setEnabled(bool(self._window._undo_stack))
        with QSignalBlocker(self._window.actions.compare_mode):
            self._window.actions.compare_mode.setChecked(self._window._compare_enabled)
        with QSignalBlocker(self._window.actions.auto_advance):
            self._window.actions.auto_advance.setChecked(self._window._auto_advance_enabled)
        with QSignalBlocker(self._window.actions.burst_groups):
            self._window.actions.burst_groups.setChecked(self._window._burst_groups_enabled)
        with QSignalBlocker(self._window.actions.burst_stacks):
            self._window.actions.burst_stacks.setChecked(self._window._burst_stacks_enabled)
        with QSignalBlocker(self._window.actions.show_hidden_folders):
            self._window.actions.show_hidden_folders.setChecked(self._window._show_hidden_folders)
        with QSignalBlocker(self._window.actions.grid_view):
            self._window.actions.grid_view.setChecked(self._window._browser_view_mode == "grid")
        with QSignalBlocker(self._window.actions.details_view):
            self._window.actions.details_view.setChecked(self._window._browser_view_mode == "details")
        with QSignalBlocker(self._window.actions.details_density_compact):
            self._window.actions.details_density_compact.setChecked(self._window._details_row_density == "compact")
        with QSignalBlocker(self._window.actions.details_density_comfortable):
            self._window.actions.details_density_comfortable.setChecked(self._window._details_row_density == "comfortable")
        with QSignalBlocker(self._window.actions.zen_mode):
            self._window.actions.zen_mode.setChecked(self._window._zen_mode_enabled)
        with QSignalBlocker(self._window.actions.performance_logging):
            self._window.actions.performance_logging.setChecked(self._window._performance_logging_enabled)
        if self._window._performance_logging_enabled and not perf_logger().is_writing:
            perf_logger().set_enabled(True, reason="action_state_resync")

        for mode, action in self._window.actions.appearance_actions.items():
            with QSignalBlocker(action):
                action.setChecked(self._window._appearance_mode == mode)
        for placement, action in self._window.actions.toolbar_placement_actions.items():
            with QSignalBlocker(action):
                action.setChecked(self._window._toolbar_placement == placement)
        for mode, action in self._window.actions.sort_actions.items():
            with QSignalBlocker(action):
                action.setChecked(self._window._sort_mode == mode)
        for mode, action in self._window.actions.filter_actions.items():
            with QSignalBlocker(action):
                action.setChecked(self._window._filter_query.quick_filter == mode)

        current_columns = self._window._normalize_column_count(self._window.columns_combo.currentData())
        for count, action in self._window.actions.column_actions.items():
            with QSignalBlocker(action):
                action.setChecked(current_columns == count)

        self._window.actions.open_preview.setEnabled(current_record is not None)
        self._window.actions.winner_ladder_mode.setEnabled(can_open_winner_ladder)
        self._window.actions.burst_groups.setEnabled(bool(self._window._current_folder and self._window._all_records))
        self._window.actions.burst_stacks.setEnabled(bool(self._window._current_folder and self._window._all_records))
        self._window.actions.show_hidden_folders.setEnabled(True)
        self._window.actions.grid_view.setEnabled(True)
        self._window.actions.details_view.setEnabled(True)
        self._window.actions.details_density_compact.setEnabled(True)
        self._window.actions.details_density_comfortable.setEnabled(True)
        self._window.actions.details_next_unreviewed.setEnabled(bool(self._window._records))
        self._window.actions.details_next_kept.setEnabled(bool(self._window._records))
        self._window.actions.details_next_rejected.setEnabled(bool(self._window._records))
        self._window.actions.zen_mode.setEnabled(True)
        self._window.actions.performance_logging.setEnabled(True)
        self._window.actions.open_performance_log_folder.setEnabled(True)
        self._window.actions.rename_selection.setEnabled(current_record is not None and has_physical_folder and not in_recycle_folder and not in_winners_folder)
        self._window.actions.batch_rename_selection.setEnabled(bool(self._window._current_folder and self._window._all_records) and not in_recycle_folder and not in_winners_folder)
        has_resizeable_records = self._window._records_have_resizable
        self._window.actions.batch_resize_selection.setEnabled(bool(self._window._current_folder and has_resizeable_records) and not in_recycle_folder)
        has_convertible_records = self._window._records_have_convertible
        self._window.actions.batch_convert_selection.setEnabled(bool(self._window._current_folder and has_convertible_records) and not in_recycle_folder)
        self._window.actions.extract_archive.setEnabled(bool(self._window._current_folder))
        self._window.actions.install_ai_runtime.setEnabled(True)
        self._window.actions.download_ai_model.setEnabled(True)
        self._window.actions.accept_selection.setEnabled(has_selection and has_physical_folder and not in_recycle_folder and not in_winners_folder)
        self._window.actions.reject_selection.setEnabled(has_selection and has_physical_folder and not in_recycle_folder and not in_winners_folder)
        self._window.actions.keep_selection.setEnabled(has_selection and has_physical_folder and not in_recycle_folder and not in_winners_folder)
        self._window.actions.move_selection.setEnabled(has_selection and has_physical_folder)
        self._window.actions.move_selection_to_new_folder.setEnabled(has_selection and has_physical_folder)
        self._window.actions.delete_selection.setEnabled(has_selection and has_physical_folder)
        self._window.actions.restore_selection.setEnabled(has_selection and has_physical_folder and in_recycle_folder)
        self._window.actions.reveal_in_explorer.setEnabled(bool(display_path))
        self._window.actions.open_in_photoshop.setEnabled(bool(selected_records and self._window._photoshop_executable))
        self._window.actions.review_ai_disagreements.setEnabled(self._window._ai_bundle is not None)
        self._window.actions.create_virtual_collection.setEnabled(True)
        self._window.actions.add_selection_to_collection.setEnabled(bool(collections))
        self._window.actions.remove_selection_from_collection.setEnabled(current_record is not None and bool(collections))
        self._window.actions.delete_virtual_collection.setEnabled(bool(collections))
        self._window.actions.browse_catalog.setEnabled(bool(catalog_roots))
        self._window.actions.add_current_folder_to_catalog.setEnabled(has_physical_folder)
        self._window.actions.add_folder_to_catalog.setEnabled(True)
        self._window.actions.remove_catalog_folder.setEnabled(bool(catalog_roots))
        self._window.actions.refresh_catalog.setEnabled(bool(catalog_roots) and self._window._active_catalog_task is None)
        self._window.actions.rebuild_folder_catalog_cache.setEnabled(has_physical_folder and not self._window._scan_in_progress)
        self._window.actions.share_to_phone.setEnabled(has_selection and not in_recycle_folder)
        self._window.actions.handoff_builder.setEnabled(has_selection and has_physical_folder and not in_recycle_folder)
        self._window.actions.send_to_editor_pipeline.setEnabled(has_selection and has_physical_folder and not in_recycle_folder and not in_winners_folder)
        self._window.actions.best_of_set_auto_assembly.setEnabled(bool(self._window._records) and (self._window._ai_bundle is not None or self._window._review_intelligence is not None))
        self._window.actions.keyboard_shortcuts.setEnabled(True)
        self._window.actions.save_workspace_preset.setEnabled(self._window.workspace_docks is not None)
        self._window.actions.new_folder.setEnabled(bool(self._window._current_folder))
        self._window.actions.save_filter_preset.setEnabled(self._window._filter_query.has_active_filters)
        self._window.actions.delete_filter_preset.setEnabled(self._window._records_view.matching_saved_filter_preset() is not None)
        self._window.actions.clear_filters.setEnabled(self._window._filter_query.has_active_filters)
        self._window.actions.check_for_updates.setEnabled(
            self._window._active_update_check_task is None
            and self._window._active_update_download_task is None
            and not self._window._update_installing
        )
        self._window._help_update.refresh_update_button_state()
        self._window._tool_mode.refresh_tool_mode_ui()
        self._window._navigation.refresh_directory_navigation_buttons()
        if self._window._collection_mode:
            self.limit_actions_for_collection_mode()
        # The checked states pushed above sit under QSignalBlocker, which also
        # swallows the changed() the top-bar buttons listen to.
        self._window._toolbar.sync_topbar_action_buttons()
        if logger.enabled:
            logger.duration(
                "window.update_action_states",
                (time.perf_counter() - start) * 1000.0,
                selected=len(selected_records),
                view=self._window._browser_view_mode,
                records=len(self._window._records),
            )

    def limit_actions_for_collection_mode(self) -> None:
        """Leave image-finding controls available while blocking review/edit actions."""
        allowed = (
            self._window.actions.open_folder,
            self._window.actions.refresh_folder,
            self._window.actions.open_preview,
            self._window.actions.show_hidden_folders,
            self._window.actions.grid_view,
            self._window.actions.browse_catalog,
            self._window.actions.refresh_catalog,
            self._window.actions.advanced_filters,
            self._window.actions.clear_filters,
            *self._window.actions.sort_actions.values(),
            *self._window.actions.filter_actions.values(),
            *self._window.actions.ai_state_actions.values(),
            *self._window.actions.column_actions.values(),
        )
        for field_name in self._window.actions.__dataclass_fields__:
            value = getattr(self._window.actions, field_name)
            if isinstance(value, QAction):
                if value not in allowed:
                    value.setEnabled(False)
            elif isinstance(value, dict):
                for action in value.values():
                    if isinstance(action, QAction) and action not in allowed:
                        action.setEnabled(False)

    def winner_score_for_record(self, record: ImageRecord) -> dict[str, object] | None:
        if not self._window._winner_scores_by_path:
            return None
        for path in record.stack_paths:
            key = os.path.normcase(os.path.normpath(str(path)))
            score = self._window._winner_scores_by_path.get(key)
            if score is not None:
                return score
        return None

    def face_bundle_for_record(self, record: ImageRecord | None) -> dict[str, object]:
        if record is None:
            return {}
        try:
            paths = self._window._aiculler.aiculler_paths_for_current_folder()
            db_path = str(aiculler_db_path(paths))
        except Exception:
            _logger.exception("Failed to resolve aiculler db path for face bundle lookup")
            db_path = ""
        if db_path and db_path != self._window._face_records_db_path:
            self._window._scan.refresh_face_records_for_current_folder()
        if not self._window._face_records_by_path:
            return {}
        for path in record.stack_paths:
            key = os.path.normcase(os.path.normpath(str(path)))
            bundle = self._window._face_records_by_path.get(key)
            if bundle:
                return bundle
        return {}

    def face_records_for_record(self, record: ImageRecord | None) -> tuple[object, ...]:
        bundle = self.face_bundle_for_record(record)
        return tuple(bundle.get("faces") or ())

    def cycle_inspector_face_preview(self) -> None:
        index = self._window.grid.current_index()
        record = self._window._record_at(index)
        faces = self.face_records_for_record(record)
        if record is None or len(faces) <= 1:
            return
        key = normalized_path_key(record.path)
        current = int(self._face_cycle_index_by_path.get(key, 0) or 0)
        self._face_cycle_index_by_path[key] = (current + 1) % len(faces)
        self.update_inspector_context(index)

    def category_info_for_record(self, record: ImageRecord | None) -> dict[str, object]:
        if record is None:
            return {}
        try:
            paths = self._window._aiculler.aiculler_paths_for_current_folder()
            db_path = str(aiculler_db_path(paths))
        except Exception:
            _logger.exception("Failed to resolve aiculler db path for category info lookup")
            db_path = ""
        if db_path and db_path != self._window._image_categories_db_path:
            self._window._scan.refresh_image_categories_for_current_folder()
        for path in record.stack_paths:
            key = os.path.normcase(os.path.normpath(str(path)))
            info = self._window._image_categories_by_path.get(key)
            if info:
                return info
        ai_result = self._window._ai_run.ai_result_for_record_memory(record, preferred_path=record.path)
        category = str(getattr(ai_result, "primary_category", "") or "")
        if category:
            return {"primary_category": category, "confidence": 0.0}
        return {}

    def face_preview_for_record(self, record: ImageRecord | None) -> QImage | None:
        bundle = self.face_bundle_for_record(record)
        faces = tuple(bundle.get("faces") or ())
        preview_path = str(bundle.get("preview_path") or "")
        if not faces or not preview_path:
            return None
        image = QImage(preview_path)
        if image.isNull():
            return None
        ordered_faces = sorted(faces, key=lambda item: float(getattr(item, "det_score", 0.0) or 0.0), reverse=True)
        cycle_key = normalized_path_key(record.path) if record is not None else ""
        face_index = int(self._face_cycle_index_by_path.get(cycle_key, 0) or 0) % max(1, len(ordered_faces))
        face = ordered_faces[face_index]
        try:
            x1, y1, x2, y2 = (float(value) for value in getattr(face, "bbox"))
        except (TypeError, ValueError):
            return None
        width = max(1.0, x2 - x1)
        height = max(1.0, y2 - y1)
        image_width = int(image.width())
        image_height = int(image.height())
        if image_width <= 0 or image_height <= 0:
            return None
        center_x = (x1 + x2) / 2.0
        center_y = (y1 + y2) / 2.0
        side = int(round(max(width, height) * 1.65))
        side = max(1, min(side, image_width, image_height))
        left = int(round(center_x - side / 2.0))
        top = int(round(center_y - side / 2.0))
        left = max(0, min(left, image_width - side))
        top = max(0, min(top, image_height - side))
        crop = image.copy(QRect(left, top, side, side))
        source_path = str(bundle.get("source_path") or "")
        if image_width > image_height and source_path:
            crop = self._window._rotate_face_crop_to_source_orientation(crop, source_path)
        return crop

    def handle_current_changed(self, index: int) -> None:
        logger = perf_logger()
        start = time.perf_counter() if logger.enabled else 0.0
        step_start = start
        self._window._views.sync_details_view_from_grid()
        if logger.enabled:
            now = time.perf_counter()
            logger.duration("window.current_changed.sync_details", (now - step_start) * 1000.0, index=index, view=self._window._browser_view_mode)
            step_start = now
        self._window._records_view.enqueue_filter_metadata_paths(self._window.grid.visible_item_paths(limit=200), front=True)
        if logger.enabled:
            now = time.perf_counter()
            logger.duration("window.current_changed.enqueue_metadata", (now - step_start) * 1000.0, index=index, view=self._window._browser_view_mode)
            step_start = now
        if self._window._aiculler.adapter_review_mode_active():
            self._window._aiculler.schedule_adapter_review_action_state_update()
        else:
            self.update_action_states()
        self.update_inspector_context(index)
        if logger.enabled:
            now = time.perf_counter()
            logger.duration(
                "window.current_changed.action_states",
                (now - step_start) * 1000.0,
                index=index,
                view=self._window._browser_view_mode,
                deferred=self._window._aiculler.adapter_review_mode_active(),
            )
            step_start = now
        self.update_status(index=index)
        if logger.enabled:
            now = time.perf_counter()
            logger.duration("window.current_changed.status", (now - step_start) * 1000.0, index=index, view=self._window._browser_view_mode)
            step_start = now
        if not self._window._preview_ctl.preview_is_visible():
            self._window._preview_ctl.schedule_preview_preload(index)
        if logger.enabled:
            now = time.perf_counter()
            logger.duration("window.current_changed.preview_preload", (now - step_start) * 1000.0, index=index, view=self._window._browser_view_mode)
        if logger.enabled:
            logger.duration("window.current_changed", (time.perf_counter() - start) * 1000.0, index=index, view=self._window._browser_view_mode)

    def handle_inspector_thumbnail_ready(self, key, _image) -> None:
        current_record = self._window._record_at(self._window.grid.current_index())
        if current_record is None or current_record.is_folder:
            return
        displayed_path = self._window.grid.displayed_variant_path(self._window.grid.current_index()) or current_record.path
        if normalized_path_key(getattr(key, "path", "")) != normalized_path_key(displayed_path):
            return
        self.update_inspector_context()

    def handle_grid_selection_changed(self) -> None:
        logger = perf_logger()
        start = time.perf_counter() if logger.enabled else 0.0
        self._window._views.sync_details_view_from_grid()
        deferred_action_state = self._window._aiculler.adapter_review_mode_active()
        if deferred_action_state:
            self._window._aiculler.schedule_adapter_review_action_state_update()
        else:
            self.update_action_states()
        self.update_status()
        self._window._records_view.enqueue_filter_metadata_paths(self._window._records_view.metadata_prefetch_seed_paths(lookahead=100), front=True)
        if logger.enabled:
            logger.duration(
                "window.selection_changed",
                (time.perf_counter() - start) * 1000.0,
                selected=self._window.grid.selected_count(),
                view=self._window._browser_view_mode,
                deferred_action_state=deferred_action_state,
            )

    def review_insight_for_record(self, record: ImageRecord | None):
        if record is None or self._window._review_intelligence is None:
            return None
        return self._window._review_intelligence.insight_for_path(record.path)

    def burst_recommendation_for_record(self, record: ImageRecord | None):
        if record is None:
            return None
        return self._window._burst_recommendations.get(record.path) or self._window._burst_recommendations.get(_memory_path_key(record.path))

    def workflow_insight_for_record(self, record: ImageRecord | None):
        if record is None:
            return None
        return self._window._workflow_insights_by_path.get(record.path) or self._window._workflow_insights_by_path.get(_memory_path_key(record.path))

    def prefilter_decision_for_record(self, record: ImageRecord | None) -> PrefilterDecision | None:
        if record is None:
            return None
        for path in record.stack_paths:
            decision = self._window._prefilter_decisions_by_path.get(normalized_path_key(path))
            if decision is not None:
                return decision
        return None

    def workflow_summary_for_record(self, record: ImageRecord | None) -> str:
        insight = self.workflow_insight_for_record(record)
        if insight is None:
            return ""
        return insight.summary_text

    def workflow_detail_lines_for_record(self, record: ImageRecord | None) -> tuple[str, ...]:
        insight = self.workflow_insight_for_record(record)
        if insight is None:
            return ()
        return insight.detail_lines

    def review_summary_for_record(self, record: ImageRecord | None) -> str:
        parts: list[str] = []
        insight = self.review_insight_for_record(record)
        workflow = self.workflow_insight_for_record(record)
        for text in (
            insight.summary_text if insight is not None else "",
            workflow.summary_text if workflow is not None else "",
        ):
            if text and text not in parts:
                parts.append(text)
        return " | ".join(parts)

    def update_inspector_context(self, index: int | None = None) -> None:
        logger = perf_logger()
        start = time.perf_counter() if logger.enabled else 0.0
        if self._window.inspector_panel is None:
            return
        if index is None:
            index = self._window.grid.current_index()

        current_record = self._window._record_at(index)
        display_path = ""
        annotation = None
        ai_result = None
        metadata = None
        inspection_stats = None
        review_insight = None
        workflow_insight = None
        review_summary = ""
        workflow_summary = ""
        workflow_details: tuple[str, ...] = ()
        thumbnail = None
        if current_record is not None and index >= 0:
            display_path = self._window.grid.displayed_variant_path(index) or current_record.path
            annotation = self._window._annotations.get(current_record.path, SessionAnnotation())
            ai_result = self._window._ai_run.ai_result_for_record(current_record, preferred_path=display_path)
            review_insight = self.review_insight_for_record(current_record)
            workflow_insight = self.workflow_insight_for_record(current_record)
            thumbnail = self.inspector_thumbnail_for(current_record, index, display_path)
            if thumbnail is not None and not thumbnail.isNull() and not current_record.is_folder:
                inspection_stats = self.cached_inspection_stats_for_thumbnail(current_record, display_path, thumbnail)
                if inspection_stats is None:
                    self.schedule_inspection_stats_for_thumbnail(current_record, display_path, thumbnail)
            if not current_record.is_folder:
                metadata = self._window._filter_metadata_manager.get_cached(current_record)
                if metadata is None:
                    self._window._records_view.enqueue_filter_metadata_paths((current_record.path,), front=True)
            review_summary = self.review_summary_for_record(current_record)
            workflow_summary = self.workflow_summary_for_record(current_record)
            workflow_details = self.workflow_detail_lines_for_record(current_record)
            face_records = self.face_records_for_record(current_record)
            face_preview = self.face_preview_for_record(current_record)
            category_info = self.category_info_for_record(current_record)
            category_profile = self._window._category_profile(category_info, face_records)
        else:
            face_records = ()
            face_preview = None
            category_info = {}
            category_profile = "uncategorized"

        self._window.inspector_panel.set_context(
            folder=self._window._projects.scope_display_label(),
            mode_label="Manual Review",
            selected_count=self._window.grid.selected_count() if self._window._records else 0,
            current_record=current_record,
            display_path=display_path,
            annotation=annotation,
            ai_result=ai_result,
            metadata=metadata,
            inspection_stats=inspection_stats,
            review_insight=review_insight,
            workflow_insight=workflow_insight,
            review_summary=review_summary,
            workflow_summary=workflow_summary,
            workflow_details=workflow_details,
            thumbnail=thumbnail,
            face_records=face_records,
            face_preview=face_preview,
            category_info=category_info,
            category_profile=category_profile,
        )
        if current_record is not None and index is not None and index >= 0:
            self._window.inspector_panel.set_position(*self._window.grid.visible_position(index))
        if logger.enabled:
            logger.duration(
                "window.update_inspector_context",
                (time.perf_counter() - start) * 1000.0,
                index=index,
                has_record=current_record is not None,
                has_metadata=metadata is not None,
                has_ai=ai_result is not None,
                has_review=review_insight is not None,
            )

    def inspection_stats_cache_key(self, record: ImageRecord, display_path: str, thumbnail) -> tuple[str, int, int, int, int]:
        cache_path = display_path or record.path
        return (
            normalized_path_key(cache_path),
            int(record.modified_ns or 0),
            int(record.size or 0),
            int(thumbnail.width()),
            int(thumbnail.height()),
        )

    def inspector_thumbnail_for(self, record: ImageRecord, index: int, display_path: str) -> QImage | None:
        if record.is_folder:
            return None

        fallback = self._window.grid.thumbnail_for(index)
        variant = self._window._inspector_thumbnail_variant(record, display_path)
        target = self._window.INSPECTOR_PREVIEW_TARGET_SIZE
        image = self._window.thumbnail_manager.get_cached(variant, target)
        if image is not None and not image.isNull():
            return image

        self._window.thumbnail_manager.request_thumbnail(
            variant,
            target,
            priority=30_000,
            drop_if_not_wanted=False,
        )
        return fallback

    def cached_inspection_stats_for_thumbnail(self, record: ImageRecord, display_path: str, thumbnail) -> InspectionStats | None:
        cache_key = self.inspection_stats_cache_key(record, display_path, thumbnail)
        cached = self._window._inspection_stats_cache.get(cache_key)
        if cached is not None:
            return cached
        return None

    def schedule_inspection_stats_for_thumbnail(self, record: ImageRecord, display_path: str, thumbnail) -> None:
        cache_key = self.inspection_stats_cache_key(record, display_path, thumbnail)
        if cache_key in self._window._inspection_stats_cache or cache_key in self._inspection_stats_pending_keys:
            return
        self._inspection_stats_pending_keys.add(cache_key)
        request = InspectorStatsRequest(cache_key=cache_key, image=thumbnail.copy())
        self._window._inspection_stats_pool.start(InspectorStatsTask(request, self._inspection_stats_result_queue), 0)
        if not self._window._inspection_stats_drain_timer.isActive():
            self._window._inspection_stats_drain_timer.start()

    def drain_inspector_stats_results(self) -> None:
        processed = 0
        changed = False
        while processed < 8:
            try:
                state, cache_key, payload = self._inspection_stats_result_queue.get_nowait()
            except Empty:
                break
            self._inspection_stats_pending_keys.discard(cache_key)
            if state == "ready":
                if len(self._window._inspection_stats_cache) >= 2048:
                    self._window._inspection_stats_cache.clear()
                self._window._inspection_stats_cache[cache_key] = payload
                changed = True
            processed += 1

        if changed:
            self.update_inspector_context()
        if processed == 0 and not self._inspection_stats_pending_keys:
            self._window._inspection_stats_drain_timer.stop()

    def update_status(self, index: int | None = None) -> None:
        if index is None:
            index = self._window.grid.current_index()
        self.update_inspector_context(index)
        self._window._records_view.update_filter_summary()
        scope_label = self._window._projects.scope_display_label()
        self.update_selection_count_labels()

        if self._window._records_view.records_view_chunk_active():
            total = len(self._window._records_view_chunk_records)
            loaded = min(len(self._window._records), total)
            selected_count = self._window.grid.selected_count() if loaded else 0
            self._window.summary_total.setText(f"Total: Loading {loaded} / {total}")
            self._window.summary_selected.setText(f"Selected: {selected_count}")
            self._window.summary_accepted.setText(f"Winners: {self._window._accepted_count}")
            self._window.summary_rejected.setText(f"Rejected: {self._window._rejected_count}")
            self._window.summary_unreviewed.setText(f"Unreviewed: {self._window._unreviewed_count}")
            self._window._ai_run.update_ai_summary()
            self._window.statusBar().showMessage(f"Loading {loaded} / {total} images from {scope_label}...")
            return

        if self._window._scan_in_progress and not self._window._all_records and not self._window._records:
            self._window.summary_total.setText("Total: scanning...")
            self._window.summary_selected.setText("Selected: 0")
            self._window.summary_accepted.setText("Winners: 0")
            self._window.summary_rejected.setText("Rejected: 0")
            self._window.summary_unreviewed.setText("Unreviewed: ...")
            self._window._ai_run.update_ai_summary()
            self._window.statusBar().showMessage(f"Scanning {scope_label}...")
            return

        count = len(self._window._records)
        accepted = self._window._accepted_count
        rejected = self._window._rejected_count
        remaining = self._window._unreviewed_count
        selected_count = self._window.grid.selected_count() if count else 0

        self._window.summary_total.setText(f"Total: {count}")
        self._window.summary_selected.setText(f"Selected: {selected_count}")
        self._window.summary_accepted.setText(f"Winners: {accepted}")
        self._window.summary_rejected.setText(f"Rejected: {rejected}")
        self._window.summary_unreviewed.setText(f"Unreviewed: {remaining}")
        self._window._ai_run.update_ai_summary()

        # The breadcrumb names the folder, so the status line only counts.
        gap = " "
        reviewed = max(0, count - remaining)
        tally = f"{count:,} photos{gap}{reviewed:,} reviewed · {accepted:,} winners · {rejected:,} rejected"
        if count == 0:
            self._window.statusBar().showMessage("0 photos")
            return

        selected_indexes = self._window.grid.selected_indexes()
        if len(selected_indexes) > 1:
            self._window.statusBar().showMessage(f"{tally}{gap}{len(selected_indexes):,} selected")
            return

        message = tally
        record = self._window._record_at(index)
        preferred_path = self._window.grid.displayed_variant_path(index) if record and record.has_variant_stack else ""
        ai_result = self._window._ai_run.ai_result_for_record(record, preferred_path=preferred_path)
        if ai_result is not None:
            ai_parts = [f"AI {ai_result.display_score_text}", ai_result.confidence_bucket_label]
            if ai_result.group_id:
                ai_parts.append(ai_result.group_id)
            if ai_result.group_size > 1:
                ai_parts.append(ai_result.rank_text)
                if ai_result.is_top_pick:
                    ai_parts.append("top pick")
            message = f"{message} | {' | '.join(ai_parts)}"
        self._window.statusBar().showMessage(message)
