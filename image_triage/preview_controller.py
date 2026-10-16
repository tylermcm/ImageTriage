"""The full-screen preview and the winner ladder: building and opening the preview, navigation and filmstrip, requests coming back from it, preloading, compare and winner-ladder state. Extracted from MainWindow (docs/mainwindow_decomposition_plan.md, DC-4.4)."""
from __future__ import annotations

import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait as wait_for_futures
from bisect import bisect_left

from PySide6.QtCore import QEventLoop, QObject, QSignalBlocker, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QWidget
from pathlib import Path

from . import edit_storage, full_render, photocraft_bridge, photocraft_preview
from .brackets import BracketDetector
from .formats import RAW_SUFFIXES
from .models import ImageRecord, SessionAnnotation
from .app_identity import user_settings
from .nav_timing import NavigationTimer
from .preload_order import candidate_indexes
from .perf import perf_logger
from .preview import FullScreenPreview, PreviewEntry
from .record_ops_controller import UndoAction
from .scanner import normalized_path_key
from .shell_actions import detect_photocraft_executable, open_in_photoshop
from .ui import load_shortcut_overrides
from .ui import preview_studio
from .ui.native_image_layer import CAMERA_RAW_FIRST_DEFAULT, CAMERA_RAW_FIRST_KEY, EDITOR_PRESTART_KEY

_log = logging.getLogger(__name__)

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .window import MainWindow


class PreviewController(QObject):
    """The full-screen preview and the winner ladder: building and opening the preview, navigation and filmstrip, requests coming back from it, preloading, compare and winner-ladder state. Extracted from MainWindow (docs/mainwindow_decomposition_plan.md, DC-4.4)."""

    # How long a selection rests before its RAW loads behind the preview.
    PHOTOCRAFT_RAW_DWELL_MS = 500
    # After a resize: how long it must be quiet before the editor is asked where its image area is, and how long to wait
    # for it to have drawn at the new size.
    RESIZE_QUIET_MS = 60
    RESIZE_SETTLE_S = 1.0
    # With the browsing picture in front, PhotoCraft can take longer to catch up without the user
    # seeing it, but loading a RAW for every photo flicked past is wasted work: start the load once the
    # selection has rested this long (or at once when something in the editor is touched).
    PHOTOCRAFT_NATIVE_DWELL_MS = 300
    # How long a photo must be on screen in the popout, with the editor up but not in use, before the editor develops it ahead
    # of the editor key (`_prefetch_rested_photo`): long enough that flipping through photos never starts one.
    REST_PREFETCH_MS = 600

    _photocraft_ready = Signal(int, str, object)
    _photocraft_preview_shown = Signal(int, str, object)
    _photocraft_window_ready = Signal(int, object)
    _photocraft_close_save_failed = Signal(str, str)
    _photocraft_saved = Signal(str)
    _photocraft_save_failed = Signal(str)
    _photocraft_theme_changed = Signal(object)
    _photocraft_viewport_ready = Signal(int, object)
    _photocraft_camera_raw_up = Signal(int, str)
    _photocraft_document_closed = Signal(str)

    def __init__(self, window: "MainWindow") -> None:
        super().__init__(window)
        self._window = window
        self._bracket_detector = BracketDetector()
        self._preview_preload_index: int | None = None
        self._photocraft_executable = detect_photocraft_executable()
        self._photocraft: photocraft_bridge.PhotoCraftProcess | None = None
        self._photocraft_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="photocraft")
        # Where the editor's window lives while no popout shows it (see `_park_photocraft`), and that window's handle.
        # Set by the UI thread once it has dealt with a request to put the editor in the popout (see `_open_in_photocraft`).
        self._photocraft_window_prepared = threading.Event()
        self._photocraft_park: QWidget | None = None
        self._photocraft_park_id: int | None = None
        self._photocraft_generation = 0
        self._photocraft_future = None
        # The background start of the editor (run_deferred_editor_start), kept so shutting down can wait for it or cancel it,
        # and set once shutdown has begun: from then on no editor that finishes starting is taken into use.
        self._photocraft_prestart = None
        # Full-size renders of photos' edits for exports and hand-offs (full_render), through the editor this controller runs.
        self._full_render = full_render.FullRenderService(
            lambda: self._photocraft,
            save_open_photo=self._save_open_photo_for_render,
            launch=self._launch_editor_for_render,
            source_key=normalized_path_key,
        )
        full_render.register(self._full_render)
        self._photocraft_closing = False
        # Previews use their own worker and PhotoCraft connection so they never queue
        # behind a RAW load.
        self._photocraft_preview_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="photocraft-preview")
        self._photocraft_dwell_request: tuple[int, str] | None = None
        # Filmstrip pixmaps by the cache key of the image they were made from: the strip asks for every visible thumb on each
        # step, and making a pixmap from the same image again each time was most of the cost (and a new pixmap repaints).
        self._filmstrip_pixmaps: dict[int, QPixmap] = {}
        self._photocraft_raw_inflight = False
        self._photocraft_needs_preview_refresh = False
        self._photocraft_current_path = ""
        self._photocraft_open_generation: int | None = None
        # True while the current request is browsed by the popout's own picture (see
        # ui/native_image_layer.py) rather than by a preview document opened in PhotoCraft.
        self._photocraft_native = False
        self.nav_timer = NavigationTimer()
        # +1 / -1 after stepping forward / back, 0 until the user has moved: biases what is preloaded.
        self._preview_travel = 0
        self._nav_input_at: float | None = None
        self._photocraft_dwell_timer = QTimer(self)
        self._photocraft_dwell_timer.setSingleShot(True)
        self._photocraft_dwell_timer.setInterval(self.PHOTOCRAFT_RAW_DWELL_MS)
        self._photocraft_dwell_timer.timeout.connect(self._photocraft_dwell_elapsed)
        self._photocraft_rest_path: str | None = None
        self._photocraft_rest_timer = QTimer(self)
        self._photocraft_rest_timer.setSingleShot(True)
        self._photocraft_rest_timer.setInterval(self.REST_PREFETCH_MS)
        self._photocraft_rest_timer.timeout.connect(self._prefetch_rested_photo)
        # The editor was resized (the filmstrip shown or hidden, the popout resized): once it has stopped, ask it again
        # where its image area is, so a picture laid over it matches the new layout.
        self._photocraft_resize_size: tuple[int, int] | None = None
        self._photocraft_resize_timer = QTimer(self)
        self._photocraft_resize_timer.setSingleShot(True)
        self._photocraft_resize_timer.setInterval(self.RESIZE_QUIET_MS)
        self._photocraft_resize_timer.timeout.connect(self._photocraft_resize_elapsed)
        self._photocraft_ready.connect(self._finish_photocraft_open)
        self._photocraft_preview_shown.connect(self._finish_photocraft_preview)
        self._photocraft_window_ready.connect(self._prepare_photocraft_window)
        self._photocraft_close_save_failed.connect(self._report_close_save_failure)
        self._photocraft_saved.connect(self._refresh_photocraft_save)
        self._photocraft_save_failed.connect(lambda error: self._window.statusBar().showMessage(error, 15000))
        self._photocraft_theme_changed.connect(self._apply_photocraft_theme)
        self._photocraft_viewport_ready.connect(self._apply_photocraft_viewport)
        self._photocraft_camera_raw_up.connect(self._handle_camera_raw_up)
        self._photocraft_document_closed.connect(self._reopen_closed_document)
        self._photocraft_save_timer = QTimer(self)
        self._photocraft_save_timer.setInterval(1500)
        self._photocraft_save_timer.timeout.connect(self._queue_photocraft_save)
        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self.shutdown_photocraft)

    def preview_if_built(self) -> FullScreenPreview | None:
        """The popout viewer if it exists yet; never builds it."""
        return self._window._preview

    def preview_is_visible(self) -> bool:
        """Whether the popout is on screen; an unbuilt viewer is not."""
        preview = self._window._preview
        return preview is not None and preview.isVisible()

    def build_preview(self) -> FullScreenPreview:
        logger = perf_logger()
        start = time.perf_counter() if logger.enabled else 0.0
        preview = FullScreenPreview(self._window)
        self._window.thumbnail_manager.edit_state_changed.connect(self.handle_edit_state_changed)
        # Published before it is configured, so a re-entrant ``self.preview``
        # while configuring gets this instance instead of recursing into a
        # second build.
        self._window._preview = preview
        try:
            self.configure_new_preview(preview)
        except BaseException:
            self._window._preview = None
            preview.deleteLater()
            raise
        if logger.enabled:
            logger.duration("preview.build", (time.perf_counter() - start) * 1000.0)
        return preview

    def configure_new_preview(self, preview: FullScreenPreview) -> None:
        """Leave a freshly built popout in the state an eagerly built one had
        after ``__init__``, plus every window-level setting changed since.

        Order matches the old startup: signals, simple setters, shortcuts, then
        the display profile and the theme (the viewer restyles itself in its own
        constructor, so the theme step is a no-op when it already matches).
        """
        preview.navigation_requested.connect(self.navigate_preview)
        preview.compare_mode_changed.connect(self.handle_preview_compare_mode_changed)
        preview.auto_bracket_mode_changed.connect(self.handle_preview_auto_bracket_mode_changed)
        preview.compare_count_changed.connect(self.handle_preview_compare_count_changed)
        preview.command_palette_requested.connect(lambda: self._window._command_palette.open(context="preview"))
        preview.photoshop_requested.connect(self.open_preview_image_in_photoshop)
        preview.photocraft_edit_requested.connect(self.handle_photocraft_edit_requested)
        preview.photocraft_host_resized.connect(self.resize_photocraft)
        preview.photocraft_close_requested.connect(self.leave_photocraft_editor)
        preview.photocraft_prioritize_requested.connect(self.prioritize_photocraft_open)
        preview.native_layer_painted.connect(self._handle_native_layer_painted)
        preview.photocraft_close_guard = self.prepare_photocraft_preview_close
        preview.winner_requested.connect(self.handle_preview_winner_requested)
        preview.reject_requested.connect(self.handle_preview_reject_requested)
        preview.keep_requested.connect(self.handle_preview_keep_requested)
        preview.delete_requested.connect(self.handle_preview_delete_requested)
        preview.move_requested.connect(self.handle_preview_move_requested)
        preview.tag_requested.connect(self.handle_preview_tag_requested)
        preview.collect_requested.connect(self.handle_preview_collect_requested)
        preview.collections_menu_requested.connect(self.handle_preview_collections_menu)
        preview.set_collection_chip_provider(self._window._catalog.collection_chip_text)
        # A popout built after the active collection was set still needs its members.
        QTimer.singleShot(0, self._window._catalog.refresh_collection_markers)
        preview.rating_requested.connect(self.handle_preview_rating_requested)
        preview.winner_ladder_choice_requested.connect(self.handle_preview_winner_ladder_choice)
        preview.winner_ladder_skip_requested.connect(self.handle_preview_winner_ladder_skip)
        preview.closed.connect(self.handle_preview_closed)

        preview.set_photoshop_available(bool(self._window._photoshop_executable))
        preview.set_photocraft_available(bool(self._photocraft_executable))
        preview.set_auto_advance_enabled(self._window._auto_advance_enabled)
        preview.set_preload_batch_size(self._window._preview_preload_batch_size)
        preview.set_auto_bracket_mode(self._window._auto_bracket_enabled)
        if self._window.actions is not None:
            self._window._settings_ctl.push_review_shortcuts((preview,), load_shortcut_overrides(settings=self._window._settings))
        self._window._command_palette.attach_preview_shortcut(preview)
        if self._window._display_profile is not None:
            preview.apply_display_profile(self._window._display_profile)
        if self._window._theme is not None:
            preview.apply_theme(self._window._theme)
        # Modes the window can enter while there is no viewer to tell.
        preview.set_compare_mode(self._window._compare_enabled)
        if self._window._collection_mode:
            preview.set_collection_browse_mode(True)

    def run_deferred_preview_build(self) -> None:
        timer, self._window._deferred_preview_timer = self._window._deferred_preview_timer, None
        if timer is not None:
            timer.deleteLater()
        # A window that was closed or hidden in the meantime (quick view, app
        # shutting down) is not worth a build; the first real use still builds.
        if self._window._preview is None and self._window.isVisible():
            self.build_preview()

    def open_current_preview(self) -> None:
        current_index = self._window.grid.current_index()
        if current_index >= 0:
            self.open_preview(current_index)

    def open_winner_ladder(self) -> None:
        current_index = self._window.grid.current_index()
        if current_index < 0:
            return
        self.start_winner_ladder(current_index)

    def handle_preview_auto_bracket_mode_changed(self, enabled: bool) -> None:
        if self._window._auto_bracket_enabled != enabled:
            self._window._views.handle_auto_bracket_toggled(enabled)

    def handle_preview_compare_mode_changed(self, enabled: bool) -> None:
        if not enabled and self._window._winner_ladder_state is not None:
            self.finish_winner_ladder(reopen_preview=False, show_message=False)
        if self._window._compare_enabled != enabled:
            if self._window.actions is not None:
                self._window.actions.compare_mode.setChecked(enabled)

    def handle_preview_compare_count_changed(self, count: int) -> None:
        self._window._compare_count = count
        self._window._manual_compare_count = count
        if self._window.preview.isVisible():
            index = self._window.grid.current_index()
            if index >= 0:
                self.open_preview(index)

    def handle_preview_winner_requested(self, path: str) -> None:
        if self._window._collection_mode:
            return
        index = self._window._record_index_by_path.get(path)
        if index is None:
            return
        anchor_path = self._window.preview.anchor_path() or path
        self._window._annotation_ctl.toggle_winner(index, advance_override=False, current_path_override=anchor_path)
        annotation = self._window._annotations.get(path, SessionAnnotation())
        self._window.preview.set_annotation_state(path, annotation.winner, annotation.reject, annotation.rating)
        anchor_index = self._window._record_index_by_path.get(anchor_path)
        if anchor_index is not None:
            self._window.grid.set_current_index(anchor_index)

    def handle_preview_reject_requested(self, path: str) -> None:
        if self._window._collection_mode:
            return
        index = self._window._record_index_by_path.get(path)
        if index is None:
            return
        anchor_path = self._window.preview.anchor_path() or path
        self._window._annotation_ctl.toggle_reject(index, advance_override=False, current_path_override=anchor_path)
        annotation = self._window._annotations.get(path, SessionAnnotation())
        self._window.preview.set_annotation_state(path, annotation.winner, annotation.reject, annotation.rating)
        anchor_index = self._window._record_index_by_path.get(anchor_path)
        if anchor_index is not None:
            self._window.grid.set_current_index(anchor_index)

    def handle_preview_keep_requested(self, path: str) -> None:
        self.dispatch_preview_action(path, self._window._annotation_ctl.keep_record)

    def handle_preview_rename_requested(self, path: str) -> None:
        index = self._window._record_index_by_path.get(path)
        if index is None:
            return
        renamed_path = self._window._record_ops.rename_record_prompt(index)
        if not renamed_path:
            return
        renamed_index = self._window._record_index_by_path.get(renamed_path)
        if renamed_index is not None:
            self._window.grid.set_current_index(renamed_index)
            if self._window.preview.isVisible():
                self.open_preview(renamed_index)

    def handle_preview_delete_requested(self, path: str) -> None:
        self.dispatch_preview_action(path, self._window._annotation_ctl.delete_record)

    def handle_preview_move_requested(self, path: str) -> None:
        self.dispatch_preview_action(path, self._window._record_ops.move_record_prompt)

    def handle_preview_tag_requested(self, path: str) -> None:
        self.dispatch_preview_action(path, self._window._annotation_ctl.tag_record, preserve_anchor=True)

    def handle_preview_collections_menu(self, path: str, global_pos) -> None:
        if self._window._collection_mode:
            return
        menu = QMenu(self._window.preview)
        self._window._catalog.populate_collections_menu(menu, [path])
        menu.exec(global_pos)

    def preview_filmstrip_marked(self, index: int) -> bool:
        record = self._window._record_at(index) if index >= 0 else None
        return record is not None and self._window.grid.in_active_collection(record.path)

    def handle_preview_collect_requested(self, path: str) -> None:
        self.dispatch_preview_action(
            path,
            lambda index: self._window._catalog.add_index_to_active_collection(index, whole_selection=False),
            preserve_anchor=True,
        )

    def handle_preview_rating_requested(self, path: str, rating: int) -> None:
        if self._window._collection_mode:
            return
        index = self._window._record_index_by_path.get(path)
        record = self._window._record_at(index) if index is not None else None
        if record is None:
            return
        annotation = self._window._annotations.setdefault(record.path, SessionAnnotation())
        previous = self._window._annotation_snapshot(annotation)
        next_rating = max(0, min(5, int(rating)))
        if previous.rating == next_rating:
            return
        annotation.rating = next_rating
        self._window._record_ops.push_undo(
            UndoAction(
                kind="annotation",
                primary_path=record.path,
                original_winner=previous.winner,
                original_reject=previous.reject,
                original_photoshop=previous.photoshop,
                rating=previous.rating,
                tags=previous.tags,
                original_review_round=previous.review_round,
                folder=self._window._current_folder,
                source_paths=self._window._record_paths(record),
                session_id=self._window._session_id,
                winner_mode=self._window._winner_mode.value,
            )
        )
        self._window._annotation_ctl.queue_annotation_persist(record, previous_annotation=previous)
        self._window._aiculler.sync_annotation_to_global_adapter_label(record, annotation)
        self._window._annotation_ctl.capture_annotation_feedback(record, previous, annotation, source_mode="rating")
        self._window._annotation_ctl.apply_review_count_delta(previous, annotation)
        self._window._annotation_ctl.apply_annotation_change_effects(
            [record.path], current_path=self._window.preview.anchor_path() or path,
            counts_already_updated=True,
        )
        self._window.preview.set_annotation_state(path, annotation.winner, annotation.reject, annotation.rating)

    def handle_preview_winner_ladder_choice(self, path: str) -> None:
        state = self._window._winner_ladder_state
        if state is None:
            return
        challengers = list(state.get("challenger_paths", ()))
        if not challengers:
            self.finish_winner_ladder(reopen_preview=True)
            return
        winner_path = str(state.get("winner_path") or "")
        challenger_path = challengers[0]
        preferred_path = challenger_path if normalized_path_key(path) == normalized_path_key(challenger_path) else winner_path
        self._window._annotation_ctl.record_pairwise_preference(
            left_path=winner_path,
            right_path=challenger_path,
            preferred_path=preferred_path,
            source_mode="winner_ladder",
            group_id=str(state.get("group_id") or ""),
            extra_payload={"winner_path": winner_path, "challenger_path": challenger_path},
        )
        if normalized_path_key(preferred_path) == normalized_path_key(challenger_path):
            state["winner_path"] = challenger_path
        state["challenger_paths"] = challengers[1:]
        if not state["challenger_paths"]:
            self.finish_winner_ladder(reopen_preview=True)
            return
        self.show_winner_ladder_state()

    def handle_preview_winner_ladder_skip(self) -> None:
        state = self._window._winner_ladder_state
        if state is None:
            return
        challengers = list(state.get("challenger_paths", ()))
        if not challengers:
            self.finish_winner_ladder(reopen_preview=True)
            return
        state["challenger_paths"] = challengers[1:]
        if not state["challenger_paths"]:
            self.finish_winner_ladder(reopen_preview=True)
            return
        self.show_winner_ladder_state()

    def handle_preview_closed(self) -> None:
        # Editor closed — resume background GPU indexing where it left off.
        self._window._records_view.resume_background_indexing()
        self._photocraft_generation += 1
        self._photocraft_save_timer.stop()
        self._rest_on_photo(None)
        # The editor stays up (parked by the close guard) for the next time; what the close did not wait for is finished now.
        self._photocraft_future = self._photocraft_executor.submit(self._settle_closed_photocraft)
        if self._window._winner_ladder_state is not None:
            self.finish_winner_ladder(reopen_preview=False, show_message=False)
        if self._window._quick_view_mode:
            self._window._quick_view_mode = False
            self._window.close()
            app = QApplication.instance()
            if app is not None:
                app.quit()
            return
        if not self._window._preview_navigation_dirty:
            return
        self._window._preview_navigation_dirty = False
        current_index = self._window.grid.current_index()
        if 0 <= current_index < len(self._window._records):
            self._window.grid.set_current_index(current_index)

    def winner_ladder_candidate_rows(self, index: int) -> tuple[list[tuple[int, ImageRecord]], str, str]:
        record = self._window._record_at(index)
        if record is None:
            return [], "", ""
        burst_recommendation = self._window._inspector.burst_recommendation_for_record(record)
        if burst_recommendation is not None and burst_recommendation.group_size > 1:
            rows = [
                (row_index, self._window._records[row_index])
                for row_index in self._window._visible_review_group_rows_by_id.get(burst_recommendation.group_id, ())
                if 0 <= row_index < len(self._window._records)
            ]
            rows.sort(
                key=lambda item: (
                    self._window._inspector.burst_recommendation_for_record(item[1]).rank_in_group
                    if self._window._inspector.burst_recommendation_for_record(item[1]) is not None
                    else 99,
                    item[1].name.casefold(),
                )
            )
            if len(rows) >= 2:
                return rows, "burst", burst_recommendation.group_id
        current_ai = self._window._ai_run.ai_result_for_index(index)
        if current_ai is not None and current_ai.group_size > 1:
            rows = [(row_index, record) for row_index, record, _result in self._window._ai_run.visible_ai_group_rows(current_ai.group_id)]
            if len(rows) >= 2:
                return rows, "ai", current_ai.group_id
        selected_indexes = [item_index for item_index in self._window.grid.selected_indexes() if 0 <= item_index < len(self._window._records)]
        if len(selected_indexes) >= 2:
            rows = [(item_index, self._window._records[item_index]) for item_index in selected_indexes]
            rows.sort(key=lambda item: (item[0] != index, item[0]))
            return rows, "selection", ""
        return [], "", ""

    def winner_ladder_candidate_count(self, index: int) -> int:
        rows, _source_mode, _group_id = self.winner_ladder_candidate_rows(index)
        return len(rows)

    def preview_entry_for_visible_path(self, path: str, *, label: str = "") -> PreviewEntry | None:
        index = self._window._record_index_by_path.get(path)
        if index is None:
            return None
        record = self._window._record_at(index)
        if record is None:
            return None
        annotation = self._window._annotations.get(record.path, SessionAnnotation())
        displayed_path = self.displayed_preview_source_path(index, record)
        edited_candidates = self.ordered_edited_candidates(record, displayed_path)
        edited_path = edited_candidates[0] if edited_candidates else ""
        return PreviewEntry(
            record=record,
            source_path=displayed_path,
            winner=annotation.winner,
            reject=annotation.reject,
            rating=annotation.rating,
            edited_path=edited_path,
            edited_candidates=tuple(edited_candidates),
            label=label,
            ai_result=self._window._ai_run.ai_result_for_record(record, preferred_path=displayed_path),
            review_summary=self._window._inspector.review_summary_for_record(record),
            workflow_summary=self._window._inspector.workflow_summary_for_record(record),
            workflow_details=self._window._inspector.workflow_detail_lines_for_record(record),
            placeholder_image=self.preview_placeholder_for_index(index),
        )

    def start_winner_ladder(self, index: int) -> None:
        rows, source_mode, group_id = self.winner_ladder_candidate_rows(index)
        if len(rows) < 2:
            self._window.statusBar().showMessage("Winner Ladder needs a visible burst, AI group, or multi-selection.")
            return
        current_record = self._window._record_at(index)
        burst_recommendation = self._window._inspector.burst_recommendation_for_record(current_record)
        current_ai = self._window._ai_run.ai_result_for_index(index)
        winner_path = current_record.path if current_record is not None else rows[0][1].path
        if source_mode == "burst" and burst_recommendation is not None and burst_recommendation.recommended_path:
            winner_path = burst_recommendation.recommended_path
        elif source_mode == "ai" and current_ai is not None and self._window._ai_bundle is not None:
            group_results = self._window._ai_bundle.group_results(current_ai.group_id)
            if group_results:
                winner_path = group_results[0].file_path
        ordered_paths = [record.path for _row_index, record in rows]
        challengers = [path for path in ordered_paths if normalized_path_key(path) != normalized_path_key(winner_path)]
        if not challengers:
            self._window.statusBar().showMessage("Winner Ladder could not find a challenger for the current winner.")
            return
        self._window._winner_ladder_state = {
            "winner_path": winner_path,
            "challenger_paths": challengers,
            "group_id": group_id,
            "source_mode": source_mode,
            "previous_compare_enabled": self._window._compare_enabled,
        }
        self.show_winner_ladder_state()

    def show_winner_ladder_state(self) -> None:
        state = self._window._winner_ladder_state
        if state is None:
            return
        challengers = list(state.get("challenger_paths", ()))
        if not challengers:
            self.finish_winner_ladder(reopen_preview=True)
            return
        winner_path = str(state.get("winner_path") or "")
        challenger_path = challengers[0]
        winner_entry = self.preview_entry_for_visible_path(winner_path, label="Current Winner")
        challenger_entry = self.preview_entry_for_visible_path(challenger_path, label="Challenger")
        if winner_entry is None or challenger_entry is None:
            self.finish_winner_ladder(reopen_preview=False)
            return
        self._window._compare_enabled = True
        if self._window.actions is not None:
            with QSignalBlocker(self._window.actions.compare_mode):
                self._window.actions.compare_mode.setChecked(True)
            self._window._toolbar.sync_topbar_action_buttons()
        challenger_index = self._window._record_index_by_path.get(challenger_path)
        if challenger_index is not None:
            self._window.grid.set_current_index(challenger_index)
        self._window.preview.set_compare_mode(True)
        self._window.preview.set_winner_ladder_mode(True)
        self._window.preview.set_compare_count(2)
        self._window.preview.show_entries([winner_entry, challenger_entry])
        self._window.preview._set_focused_slot(1)
        self._window.statusBar().showMessage(
            f"Winner Ladder: {Path(winner_path).name} vs {Path(challenger_path).name}"
        )

    def finish_winner_ladder(self, *, reopen_preview: bool, show_message: bool = True) -> None:
        state = self._window._winner_ladder_state
        if state is None:
            return
        winner_path = str(state.get("winner_path") or "")
        previous_compare_enabled = bool(state.get("previous_compare_enabled"))
        self._window._winner_ladder_state = None
        self._window.preview.set_winner_ladder_mode(False)
        self._window._compare_enabled = previous_compare_enabled
        if self._window.actions is not None:
            with QSignalBlocker(self._window.actions.compare_mode):
                self._window.actions.compare_mode.setChecked(previous_compare_enabled)
            self._window._toolbar.sync_topbar_action_buttons()
        self._window.preview.set_compare_mode(previous_compare_enabled)
        winner_index = self._window._record_index_by_path.get(winner_path)
        if winner_index is not None:
            self._window.grid.set_current_index(winner_index)
            if reopen_preview and self._window.preview.isVisible():
                self.open_preview(winner_index)
        collection_name = ""
        if winner_path and show_message:
            collection_name = self._window._catalog.auto_add_winner_paths(
                [winner_path], batch_id=self._window._catalog.winner_auto_add_batch_id()
            )
        if show_message and winner_path and collection_name:
            self._window._catalog.show_collection_feedback(
                f"Winner Ladder complete: {Path(winner_path).name} (added to {collection_name})",
                self._window._catalog.active_collection_id(),
            )
        elif show_message and winner_path:
            self._window.statusBar().showMessage(f"Winner Ladder complete: {Path(winner_path).name}")

    def preview_path_for_index(self, index: int) -> str:
        record = self._window._record_at(index)
        if record is None:
            return ""
        displayed = self._window.grid.displayed_variant_path(index)
        return displayed or self.preview_source_path(record)

    def preview_placeholder_for_index(self, index: int):
        if index < 0:
            return None
        return self._window.grid.thumbnail_for(index)

    def rebuild_visible_preview_group_indexes(self) -> None:
        review_rows_by_id: dict[str, list[int]] = {}
        ai_rows_by_id: dict[str, list[int]] = {}
        for row_index, record in enumerate(self._window._records):
            review_insight = self._window._inspector.review_insight_for_record(record)
            if review_insight is not None and review_insight.has_group:
                review_rows_by_id.setdefault(review_insight.group_id, []).append(row_index)
            preferred_path = self._window.grid.displayed_variant_path(row_index) if record.has_variant_stack else record.path
            ai_result = self._window._ai_run.ai_result_for_record_memory(record, preferred_path=preferred_path)
            if ai_result is not None and ai_result.group_size > 1:
                ai_rows_by_id.setdefault(ai_result.group_id, []).append(row_index)
        self._window._visible_review_group_rows_by_id = review_rows_by_id
        self._window._visible_ai_group_rows_by_id = ai_rows_by_id

    def schedule_preview_preload(self, index: int | None = None) -> None:
        if index is None:
            index = self._window.grid.current_index()
        if index < 0:
            return
        self._preview_preload_index = index
        # Throttle, not debounce: stepping faster than the timer's interval would otherwise keep
        # restarting it and nothing would be decoded ahead until the user stopped. The run reads the
        # latest index, so a pending run always serves the photo the user is on by then.
        if not self._window._preview_preload_timer.isActive():
            self._window._preview_preload_timer.start()

    def run_preview_preload(self) -> None:
        index = self._preview_preload_index
        self._preview_preload_index = None
        if index is None or index < 0 or not self.preview_is_visible():
            return
        paths = self.likely_preview_preload_paths(index)
        self._window.preview.preload_paths(paths)

    def likely_preview_preload_paths(self, index: int) -> list[str]:
        limit = self._window._normalize_preview_preload_batch_size(
            getattr(self._window, "_preview_preload_batch_size", self._window.PREVIEW_PRELOAD_BATCH_SIZE_DEFAULT)
        )
        if limit <= 0 or not self._window._records:
            return []
        ordered: list[str] = []
        seen: set[str] = set()

        def add(candidate_index: int) -> None:
            if not 0 <= candidate_index < len(self._window._records):
                return
            record = self._window._record_at(candidate_index)
            if record is None or record.is_folder:
                return
            path = self.preview_path_for_index(candidate_index)
            if not path:
                return
            normalized = normalized_path_key(path)
            if normalized in seen:
                return
            seen.add(normalized)
            ordered.append(path)

        for candidate_index in candidate_indexes(index, len(self._window._records), self._preview_travel):
            if len(ordered) >= limit:
                break
            add(candidate_index)

        current_record = self._window._record_at(index)
        current_insight = self._window._inspector.review_insight_for_record(current_record)
        if current_insight is not None and current_insight.has_group:
            for row_index in self._window._visible_review_group_rows_by_id.get(current_insight.group_id, ()):
                add(row_index)
                if len(ordered) >= limit:
                    break

        current_ai = self._window._ai_run.ai_result_for_index(index)
        if current_ai is not None and current_ai.group_size > 1:
            for row_index in self._window._visible_ai_group_rows_by_id.get(current_ai.group_id, ()):
                add(row_index)
                if len(ordered) >= limit:
                    break

        return ordered[:limit]

    def open_preview_image_in_photoshop(self, path: str) -> None:
        if self._window._collection_mode:
            return
        if not path or not self._window._photoshop_executable:
            return
        open_in_photoshop(path)

    def handle_photocraft_edit_requested(self, path: str) -> None:
        """Show the photo in the popout's embedded PhotoCraft.

        PhotoCraft first opens a cached screen-sized preview (tens of milliseconds, a few MB
        of I/O). The photo's RAW follows once the selection has rested for a moment, and
        replaces the preview in place. Moving on first cancels the RAW load.
        """
        preview = self.preview_if_built()
        if preview is None or not path:
            return
        self._photocraft_generation += 1
        generation = self._photocraft_generation
        self._photocraft_current_path = path
        self._photocraft_dwell_timer.stop()
        # Disable the local native parent, which blocks input to its child
        # without a synchronous cross-process WM_ENABLE call on the UI thread.
        preview.set_photocraft_loading(True)
        self._photocraft_save_timer.start()
        self._photocraft_native = preview.native_first_active()
        if self._photocraft_native:
            self._request_photocraft_behind_picture(generation, path)
        elif self._photocraft_fast_lane_ready(path):
            self._photocraft_dwell_request = (generation, path)
            self._photocraft_preview_executor.submit(self._show_photocraft_preview, generation, path)
            self._photocraft_dwell_timer.setInterval(self.PHOTOCRAFT_RAW_DWELL_MS)
            self._photocraft_dwell_timer.start()
        else:
            self._start_photocraft_raw_stage(generation, path)

    def _request_photocraft_behind_picture(self, generation: int, path: str) -> None:
        """The popout is already showing this photo's own picture. PhotoCraft has nothing to show
        until it can show the editable document, so no preview document is opened in it: moving on
        costs one timer restart (and, if a load is already running, one cancel), never a round trip.
        """
        proc = self._photocraft
        if proc is None or not proc.is_running():
            # The editor has to start first, which takes as long as it takes; do not also wait on a dwell.
            self._start_photocraft_raw_stage(generation, path)
            return
        if self._photocraft_raw_inflight:
            # An obsolete open must not finish into this photo's window.
            self._photocraft_preview_executor.submit(self._cancel_photocraft_opens)
        self._photocraft_dwell_request = (generation, path)
        if proc.camera_raw_first and proc.prefetch_ready is not None and proc.prefetch_ready == normalized_path_key(path):
            # Developed ahead of time: swapping to it costs the editor next to nothing, so there is no load to hold back
            # for a selection that may move on.
            proc.prefetch_ready = None
            self._photocraft_dwell_elapsed()
            return
        self._photocraft_dwell_timer.setInterval(self.PHOTOCRAFT_NATIVE_DWELL_MS)
        self._photocraft_dwell_timer.start()

    def _cancel_photocraft_opens(self) -> None:
        proc = self._photocraft
        if proc is None or not proc.is_running():
            return
        try:
            proc.cancel_open_jobs()
        except Exception:
            _log.warning("Could not cancel the obsolete PhotoCraft open", exc_info=True)

    def prioritize_photocraft_open(self) -> None:
        """Something in the editor was touched before the selection rested: load the photo now."""
        if self._photocraft_dwell_timer.isActive():
            self._photocraft_dwell_timer.stop()
            self._photocraft_dwell_elapsed()

    def _photocraft_fast_lane_ready(self, path: str) -> bool:
        """Whether a preview can go in front of the RAW: PhotoCraft is already running for
        this folder and the photo has no saved edits to restore."""
        proc = self._photocraft
        if proc is None or not proc.is_running() or proc.fast_unavailable:
            return False
        if Path(path).suffix.lower() not in RAW_SUFFIXES:
            return False  # JPEGs and other flat files already open in tens of milliseconds
        sidecar = photocraft_bridge.sidecar_pcraft_path(path)
        if sidecar.is_file() or str(sidecar) in proc.pending_stashes or str(sidecar) in proc.stash_errors:
            return False
        folder = str(Path(path).parent)
        return proc.path_within_root(path) is not None and proc.path_within_write_root(str(edit_storage.edit_root_for(folder) / "x")) is not None

    def _start_photocraft_raw_stage(self, generation: int, path: str) -> None:
        if generation != self._photocraft_generation:
            return
        self._photocraft_future = self._photocraft_executor.submit(self._load_photocraft, generation, path)

    def _photocraft_dwell_elapsed(self) -> None:
        request = self._photocraft_dwell_request
        self._photocraft_dwell_request = None
        if request is not None:
            self._start_photocraft_raw_stage(*request)

    def _show_photocraft_preview(self, generation: int, path: str) -> None:
        """First stage, on its own worker and connection so it never waits behind a RAW load."""
        error = None
        proc = None
        try:
            if generation != self._photocraft_generation:
                return
            proc = self._photocraft
            if proc is None or not proc.is_running():
                return
            fast = proc.fast_lane()
            # An obsolete RAW open must not finish into this photo's window.
            if self._photocraft_raw_inflight:
                proc.cancel_open_jobs()
            edit_root = edit_storage.ensure_edit_root(Path(path).parent)
            image = photocraft_preview.ensure_preview(path, edit_root)
            if generation != self._photocraft_generation:
                return
            relative = proc.path_within_root(str(image))
            if relative is None:
                raise photocraft_bridge.PhotoCraftError("The preview is outside this PhotoCraft process's read root")
            # Edits on the photo being left are saved first: opening replaces its document.
            self._save_photocraft_sidecar_if_dirty(proc, control=fast)
            if generation != self._photocraft_generation:
                return
            fast.app_open(relative.replace("\\", "/"), replace=True)
            proc.current_source = None
            proc.current_sidecar = None
            proc.opened_revision = 0
            proc.preview_source = normalized_path_key(path)
        except Exception as caught:
            error = caught
            _log.warning("PhotoCraft preview failed for %s: %s", path, caught)
            if proc is not None and proc.fast is None:
                proc.fast_unavailable = True
        self._photocraft_preview_shown.emit(generation, path, error)

    def _finish_photocraft_preview(self, generation: int, path: str, error) -> None:
        preview = self.preview_if_built()
        if generation != self._photocraft_generation or preview is None or not preview.isVisible():
            return
        if preview._compare_mode or preview._collection_browse_mode or preview._before_after_enabled:
            return
        proc = self._photocraft
        if error is not None or proc is None or not proc.is_running():
            # No preview this time: go straight to the RAW; the editor is still the viewer.
            self._photocraft_dwell_timer.stop()
            self._photocraft_dwell_request = None
            self._start_photocraft_raw_stage(generation, path)
            return
        self._attach_photocraft(preview, proc)
        preview.show_photocraft_host()
        self._mark_navigation(path, "first_pixel", "preview")

    def _load_photocraft(self, generation: int, path: str) -> None:
        # Superseded requests never decode or open a document. The running
        # handoff finishes safely; only the latest selection may become visible.
        if generation != self._photocraft_generation:
            return
        error = None
        self._photocraft_raw_inflight = True
        self._photocraft_open_generation = generation
        try:
            self._open_in_photocraft(None, path)
        except Exception as caught:
            error = caught
            if generation == self._photocraft_generation:
                proc = self._photocraft
                _log.exception("PhotoCraft handoff failed for %s (%s)", path, proc.exit_note() if proc is not None else "no editor was running")
        finally:
            self._photocraft_raw_inflight = False
            self._photocraft_open_generation = None
        if self._photocraft_needs_preview_refresh:
            self._photocraft_needs_preview_refresh = False
            current = self._photocraft_current_path
            if current and generation != self._photocraft_generation and not self._photocraft_native and self._photocraft_fast_lane_ready(current):
                self._photocraft_preview_executor.submit(self._show_photocraft_preview, self._photocraft_generation, current)
        self._photocraft_ready.emit(generation, path, error)

    def _prepare_photocraft_window(self, generation: int, proc) -> None:
        try:
            preview = self.preview_if_built()
            if generation != self._photocraft_generation or preview is None or not preview.isVisible():
                return
            if not preview._uses_photocraft_editor() or not proc.is_running():
                return
            # Attach while the native shell is empty, before the costly first photo
            # render. Keep the Qt host hidden and the loading cover in front.
            preview._photocraft_host.setGeometry(preview.panes_widget.rect())
            parent = preview.photocraft_host_hwnd()
            photocraft_bridge.embed_in_widget(proc.hwnd, parent)
            proc.attached_parent = parent
            photocraft_bridge.resize_embedded(proc.hwnd, *preview.photocraft_host_size())
        finally:
            self._photocraft_window_prepared.set()

    def _finish_photocraft_open(self, generation: int, path: str, error) -> None:
        preview = self.preview_if_built()
        if generation != self._photocraft_generation or preview is None or not preview.isVisible():
            return
        if preview._compare_mode or preview._collection_browse_mode or preview._before_after_enabled:
            return
        if error is not None:
            proc = self._photocraft
            if proc is not None and proc.is_running() and proc.current_source:
                # A failed stash leaves the previous document open. Keep it
                # accessible so the user can save/recover it instead of hiding it.
                self._attach_photocraft(preview, proc)
                preview.show_photocraft_host()
                message = f"Could not switch to {Path(path).name}; PhotoCraft is still showing {Path(proc.source_path).name}: {error}"
            else:
                preview.hide_photocraft_host()
                preview.set_photocraft_loading(False)
                preview.show_photocraft_error(str(error))
                self._photocraft_save_timer.stop()
                message = f"PhotoCraft: {error}"
            self._window.statusBar().showMessage(message, 15000)
            return
        proc = self._photocraft
        if proc is not None and proc.is_running():
            first_attach = not preview.photocraft_edit_active()
            self._attach_photocraft(preview, proc)
            preview.show_photocraft_host()
            self._mark_navigation(path, "first_pixel", "preview", "refined")
            if not proc.camera_raw_first:
                preview.photocraft_document_ready(path)
            self._photocraft_future = self._photocraft_executor.submit(self._refresh_photocraft_viewport, proc, generation, path)
            if first_attach:
                # Initial decoding happens in a small offscreen viewport.
                # Fit once after the host has given it its actual pane size.
                self._photocraft_future = self._photocraft_executor.submit(self._fit_photocraft, proc, generation)

    def _photocraft_launch_args(self) -> tuple[str, ...]:
        """Extra command-line flags for the editor: the Camera Raw workspace when that setting is on."""
        return ("--raw-workspace",) if user_settings().value(CAMERA_RAW_FIRST_KEY, CAMERA_RAW_FIRST_DEFAULT, bool) else ()

    # How long to wait for Camera Raw to appear after its document opened, and how often to look.
    CAMERA_RAW_WAIT_S = 1.5
    CAMERA_RAW_POLL_S = 0.03
    # Camera Raw's own dialog was seen up with its view laid out: the picture over it need only outlast the frame that draws it.
    CAMERA_RAW_SETTLE_MS = 40

    def _refresh_photocraft_viewport(self, proc, generation: int, path: str = "") -> None:
        """Tell the popout where PhotoCraft draws its image, so the browsing picture can cover just that.

        In the Camera Raw workspace the picture stays until Camera Raw has laid out its view (or a short
        wait runs out), so the swap never shows the editor between the document and its dialog.
        """
        if generation != self._photocraft_generation or not proc.is_running():
            return
        try:
            viewport = proc.control.call("ui.viewport")
            if proc.camera_raw_first:
                deadline = time.monotonic() + self.CAMERA_RAW_WAIT_S
                while not self._dialog_view_ready(viewport) and time.monotonic() < deadline:
                    if generation != self._photocraft_generation:
                        return
                    time.sleep(self.CAMERA_RAW_POLL_S)
                    viewport = proc.control.call("ui.viewport")
        except photocraft_bridge.PhotoCraftError:
            viewport = None  # an older PhotoCraft build: the picture covers the whole window instead
        if viewport is not None:
            self._photocraft_viewport_ready.emit(generation, viewport)
        if proc.camera_raw_first and path:
            self._photocraft_camera_raw_up.emit(generation, path)

    @staticmethod
    def _dialog_view_ready(viewport: dict) -> bool:
        """Camera Raw's own dialog has laid out its view. The editor's loading frame, which stands in for the dialog
        while the photo develops, reports the same rectangle with ``loading`` set, and so does the previous photo's
        dialog while it hands over to the next (``busy``): the picture must stay until the next photo's own dialog is
        up, or the swap would show the old photo's render or an empty frame."""
        camera_raw = viewport.get("cameraRaw") or {}
        return bool(camera_raw.get("view")) and not camera_raw.get("loading") and not camera_raw.get("busy")

    def _handle_camera_raw_up(self, generation: int, path: str) -> None:
        preview = self.preview_if_built()
        if preview is not None and generation == self._photocraft_generation:
            preview.photocraft_document_ready(path, settle_ms=self.CAMERA_RAW_SETTLE_MS)
            # The photo is up and being looked at: this is the time to get the next one ready.
            self._prefetch_next_raw(path)

    @staticmethod
    def _note_prefetch(proc, state: dict) -> None:
        """Remember which photo the editor says it has developed ahead of time (compared by the key sent with the request)."""
        sent = proc.prefetch_sent
        proc.prefetch_ready = sent[1] if sent is not None and state.get("prefetched") == sent[0] else None

    def _prefetch_candidate(self, path: str) -> str | None:
        """The raw most likely to be opened after ``path``: the photo next to it in the direction of travel,
        and only if that one is a raw (a flat file opens fast anyway, and nothing further along is worth guessing)."""
        records = getattr(self._window, "_records", None)
        if not records:
            return None
        key = normalized_path_key(path)
        index = next((i for i, record in enumerate(records) if normalized_path_key(record.path) == key), None)
        if index is None:
            return None
        for candidate in candidate_indexes(index, len(records), self._preview_travel):
            if candidate == index:
                continue
            record = records[candidate]
            if getattr(record, "is_folder", False) or Path(record.path).suffix.lower() not in RAW_SUFFIXES:
                return None
            return record.path
        return None

    def _prefetch_next_raw(self, path: str) -> None:
        proc = self._photocraft
        if proc is None or not proc.is_running() or not self._camera_raw_mode(path):
            return
        if not getattr(proc.control, "camera_raw_prefetch_supported", False):
            return
        candidate = self._prefetch_candidate(path)
        relative = proc.path_within_root(candidate) if candidate else None
        signature = photocraft_bridge.file_signature(candidate) if candidate else None
        if relative is None or signature is None:
            return
        # On its own connection and worker: a request must never wait behind, or delay, anything the user is doing.
        self._photocraft_preview_executor.submit(self._send_prefetch, proc, relative, signature, normalized_path_key(candidate))

    def _rest_on_photo(self, path: str | None) -> None:
        """The popout is showing ``path`` (or, with ``None``, nothing worth preparing). If it stays, the editor develops it ahead
        of the editor key."""
        if path and path == self._photocraft_rest_path and self._photocraft_rest_timer.isActive():
            return  # the same photo shown again (a refresh, a re-sync): the wait it started still stands
        self._photocraft_rest_path = path
        if path:
            self._photocraft_rest_timer.start()
        else:
            self._photocraft_rest_timer.stop()

    def _prefetch_rested_photo(self) -> None:
        """The photo has been on screen for a moment and the editor is up (started in the background, or parked) but not in
        use: have it develop this raw now, on its own connection, so that pressing the editor key finds it ready (the open then
        costs ~16 ms instead of ~0.7 s on a fast machine, more on a slow one). One photo at a time, the editor's one slot."""
        path = self._photocraft_rest_path
        proc = self._photocraft
        preview = self.preview_if_built()
        if not path or proc is None or not proc.is_running() or preview is None or not preview.isVisible():
            return
        if preview.photocraft_edit_active() or not self._camera_raw_mode(path):
            return  # the editor is on a photo already (it prepares the next one itself), or this is not a raw it would develop
        if not getattr(proc.control, "camera_raw_prefetch_supported", False):
            return
        key = normalized_path_key(path)
        if proc.prefetch_sent is not None and proc.prefetch_sent[1] == key:
            return
        relative = proc.path_within_root(path)
        signature = photocraft_bridge.file_signature(path)
        if relative is None or signature is None:
            return
        self._photocraft_preview_executor.submit(self._send_prefetch, proc, relative, signature, key)

    def _send_prefetch(self, proc, relative: str, signature: str, key: str = "") -> None:
        try:
            proc.fast_lane().app_prefetch(relative, signature)
            proc.prefetch_sent = (relative, key)
        except photocraft_bridge.PhotoCraftError as error:
            _log.debug("Camera Raw prefetch for %s was not accepted: %s", relative, error)

    def _reopen_closed_document(self, path: str) -> None:
        """The document was closed in the full editor: show the photo in Camera Raw again."""
        preview = self.preview_if_built()
        if preview is None or not preview.isVisible():
            return
        if normalized_path_key(path) != normalized_path_key(self._photocraft_current_path or ""):
            return  # the user has already moved to another photo
        preview.show_browsing_picture()
        self.handle_photocraft_edit_requested(path)
        self.prioritize_photocraft_open()

    def _apply_photocraft_viewport(self, generation: int, viewport) -> None:
        preview = self.preview_if_built()
        if preview is not None and generation == self._photocraft_generation:
            preview.set_photocraft_viewport(viewport)

    def _fit_photocraft(self, proc, generation: int) -> None:
        if generation == self._photocraft_generation and proc.is_running():
            try:
                # Not ui.menu.invoke: the editor refuses every menu command while Camera Raw is open, which in the
                # Camera Raw workspace is nearly always. engine.execute is not a menu click.
                proc.control.call("engine.execute", {"command": "view.fitOnScreen"})
            except photocraft_bridge.PhotoCraftError:
                _log.exception("Could not fit the hosted PhotoCraft canvas")

    def _attach_photocraft(self, preview, proc) -> None:
        parent = preview.photocraft_host_hwnd()
        if proc.attached_parent != parent:
            photocraft_bridge.embed_in_widget(proc.hwnd, parent)
            proc.attached_parent = parent
        preview.set_photocraft_loading(False)

    def _queue_photocraft_save(self) -> None:
        if self._photocraft_future is not None and not self._photocraft_future.done():
            return
        self._photocraft_future = self._photocraft_executor.submit(self._autosave_photocraft)

    # How long the open photo must be left alone before its full-size render is written (it is the heaviest encode of a save, and the editor
    # is slower to answer while it runs): about a pause in editing, not a moment between two edits.
    RENDER_IDLE_S = 10.0

    def _autosave_photocraft(self) -> None:
        proc = self._photocraft
        if proc is not None and proc.is_running():
            try:
                self._sync_photocraft_theme(proc)
                self._poll_photocraft_stashes(proc)
                self._save_photocraft_sidecar_if_dirty(proc, commit_raw_draft=False, lean=True)
                key = str(proc.current_sidecar)
                if (
                    proc.current_sidecar is not None
                    and key in proc.preview_missing
                    and key not in proc.pending_stashes
                    and time.monotonic() - proc.last_edit_at >= self.RENDER_IDLE_S
                ):
                    # The photo has been left alone for a while: this is the time to write the render the autosaves left out.
                    self._save_photocraft_sidecar_if_dirty(proc, commit_raw_draft=False)
            except Exception:
                _log.exception("PhotoCraft autosave failed; retaining the open document")

    def _apply_photocraft_theme(self, colors) -> None:
        preview = self.preview_if_built()
        if preview is not None:
            preview.set_photocraft_palette(colors)

    def _sync_photocraft_theme(self, proc) -> None:
        try:
            colors = proc.control.call("ui.theme")
        except photocraft_bridge.PhotoCraftError as error:
            # Older hosted builds still open; new builds expose actual colors.
            if "unknown method" in str(error):
                return
            raise
        proc.theme_synced = True
        self._photocraft_theme_changed.emit(colors)

    def _refresh_photocraft_save(self, path: str) -> None:
        from .grid import GridDeltaUpdate

        self._filmstrip_edit_state_stale(path)
        self._window.grid.update_items(GridDeltaUpdate(changed_paths=(path,), preserve_pixmap_cache=False))
        preview = self.preview_if_built()
        if preview is not None:
            preview._preview_cache.clear()
            preview._request_preview_loads()

    def _resolve_photocraft_target(self, path: str) -> tuple[str, str]:
        """(path_to_open, sidecar_path) for the real source ``path``.

        If a previous edit session for this photo exists (a ``.pcraft``
        sidecar in the hidden per-folder edit root, same convention as
        ``edit_storage``), that is what gets opened, so edits carry over
        across navigations and app restarts. Otherwise the original is
        opened directly (or, for a format PhotoCraft can't read natively, a
        decoded copy for a non-RAW format). Fresh RAWs retain sensor data and
        their original camera bytes; Nikon uses an undeveloped sensor adapter.
        The sidecar is always addressable under the photo's own parent
        folder, so one automation root covers both.
        """
        sidecar = photocraft_bridge.sidecar_pcraft_path(path)
        proc = self._photocraft
        if self._camera_raw_mode(path):
            # A raw opens in Camera Raw with its recipe, as Photoshop opens the raw file and not the document
            # made from it; the saved full-editor document is what "Open" continues (see _camera_raw_spec).
            return path, str(sidecar)
        in_memory = proc is not None and (str(sidecar) in proc.pending_stashes or str(sidecar) in proc.stash_errors)
        if sidecar.is_file() or in_memory:
            return str(sidecar), str(sidecar)
        legacy_project = sidecar.parent / (Path(path).stem + ".pcraft")
        if legacy_project.is_file():
            return str(legacy_project), str(sidecar)
        # Keep the appearance of existing built-in sessions on first handoff.
        # PhotoCraft starts from their full-resolution render; the JSON bundle
        # remains available and the original is never changed.
        if edit_storage.builtin_session_has_edits(path):
            from .edit_render_headless import render_edited_image

            rendered = render_edited_image(path)
            if rendered is None or rendered.isNull():
                raise photocraft_bridge.PhotoCraftError(f"Could not restore existing edits for {path}")
            root = edit_storage.ensure_edit_root(Path(path).parent)
            imported = root / (Path(path).name + "__photocraft_legacy.tiff")
            if not rendered.save(str(imported), "TIFF"):
                raise photocraft_bridge.PhotoCraftError(f"Could not transfer existing edits for {path}")
            return str(imported), str(sidecar)
        if not photocraft_bridge.path_needs_conversion(path):
            return path, str(sidecar)
        cache_dir = edit_storage.ensure_edit_root(Path(path).parent)
        return photocraft_bridge.materialize_for_photocraft(path, cache_dir), str(sidecar)

    def _camera_raw_mode(self, path: str) -> bool:
        """Whether ``path`` opens in the editor's Camera Raw workspace: a raw, with the workspace on."""
        if Path(path).suffix.lower() not in RAW_SUFFIXES:
            return False
        proc = self._photocraft
        if proc is not None and proc.is_running():
            return bool(proc.camera_raw_first and getattr(proc.control, "camera_raw_supported", False))
        return bool(self._photocraft_launch_args())

    def _camera_raw_spec(self, path: str, proc: photocraft_bridge.PhotoCraftProcess | None) -> dict:
        """What the editor needs to open ``path`` in Camera Raw: its stored recipe, where to keep the recipe
        and its render, and the saved document "Open" continues. Paths are relative to the editor's roots."""
        spec: dict = {
            "recipePath": photocraft_bridge.recipe_path(path).name,
            "displayPath": photocraft_bridge.display_render_path(path).name,
            # Dropped by ``app_open`` for an editor that keeps no render manifest.
            "manifestPath": photocraft_bridge.render_manifest_path(path).name,
        }
        recipe = photocraft_bridge.load_recipe(path)
        if recipe is not None:
            spec["recipe"] = recipe
        signature = photocraft_bridge.file_signature(path)
        if signature is not None:
            # Lets the editor use a copy of this raw it developed ahead of time (see _prefetch_next_raw).
            spec["signature"] = signature
        sidecar = photocraft_bridge.sidecar_pcraft_path(path)
        if proc is not None and (str(sidecar) in proc.pending_stashes or str(sidecar) in proc.stash_errors):
            # A save still in flight is restored from the editor's memory, keyed by its write-root path.
            relative = proc.path_within_write_root(str(sidecar))
            if relative is not None:
                spec["continueFrom"] = relative
        elif sidecar.is_file():
            spec["continueFrom"] = sidecar.relative_to(Path(path).parent).as_posix()
        return spec

    def _open_in_photocraft(self, preview: FullScreenPreview | None, path: str) -> None:
        # The request generation this open belongs to (set by _load_photocraft on the worker).
        generation = self._photocraft_open_generation if self._photocraft_open_generation is not None else self._photocraft_generation
        proc = self._photocraft
        if proc is not None and not proc.is_running():
            proc.shutdown()
            self._photocraft = proc = None
        if proc is not None and proc.current_source == normalized_path_key(path):
            if proc.left and self._camera_raw_mode(path):
                # The photo was left and is wanted again. Leaving it closed its Camera Raw dialog (its settings are kept as the
                # recipe, or it was saved), so what the editor still shows is the bare document: without this the photo came
                # back in the full editor. It is opened again like any photo come back to.
                pass
            elif proc.control.document_revision() is not None:
                return
            else:
                proc.current_source = None
                proc.current_sidecar = None
                proc.opened_revision = 0
        open_path, sidecar_path = self._resolve_photocraft_target(path)
        if proc is not None and proc.is_running() and (
            proc.path_within_root(open_path) is None or proc.path_within_write_root(sidecar_path) is None
        ):
            # A new folder: start a fresh process rooted at this file instead
            # of trying to widen an already-running one's automation root.
            self._save_photocraft_sidecar_if_dirty(proc)
            self._wait_photocraft_stashes(proc)
            proc.shutdown()
            self._photocraft = None
            proc = None
        launched = proc is None
        if proc is not None and proc.idle_priority:
            photocraft_bridge.raise_priority(proc)  # it was started in the background; now it is what the user is using
        if proc is not None and (proc.attached_parent is None or proc.attached_parent == self._photocraft_park_id):
            # Started without a window to go in, or parked when the popout last closed: put it in the popout now, before the
            # photo renders. The open is not sent until that is done: moving a window waits for the thread that owns it, and
            # an editor already busy with the photo's first frame kept the popout's thread waiting half a second (measured,
            # once a prepared photo made the open instant).
            self._photocraft_window_prepared.clear()
            self._photocraft_window_ready.emit(generation, proc)
            self._photocraft_window_prepared.wait(1.0)
        camera_raw = self._camera_raw_spec(path, proc) if self._camera_raw_mode(path) else None
        if launched:
            directory = str(Path(path).parent)
            write_root = edit_storage.ensure_edit_root(directory)
            proc = photocraft_bridge.launch_photocraft(
                open_path, read_root=directory, write_root=str(write_root), executable=self._photocraft_executable,
                extra_args=self._photocraft_launch_args(), camera_raw=camera_raw,
                on_window=lambda launched: self._photocraft_window_ready.emit(generation, launched),
            )
            self._photocraft = proc
            if proc.executable:
                self._photocraft_executable = proc.executable
            proc.current_sidecar = Path(sidecar_path)
            # Hosted imports/restores use Session.add_document -> DocState.new,
            # whose initial revision and saved_revision are both one.
            proc.opened_revision = 1
            proc.document_generation += 1
        elif not self._switch_photocraft_document(proc, open_path, sidecar_path, self._photocraft_open_generation, camera_raw=camera_raw):
            # The selection moved on while this RAW was opening: its result belongs to a photo
            # that is no longer shown, so it must not be bound or recorded.
            return
        relative_sidecar = proc.path_within_write_root(sidecar_path)
        relative_render = proc.path_within_write_root(str(photocraft_bridge.rendered_preview_path(path)))
        bind = {"path": relative_sidecar, "preview": relative_render}
        # The editor's own Save (Ctrl+S) then writes what the host's saves write: the screen-sized picture too, and the manifest.
        bind.update(self._render_manifest_params(proc, path))
        try:
            proc.control.call("app.bind", bind)
        except Exception:
            if launched:
                proc.shutdown()
                self._photocraft = None
            raise
        proc.current_source = normalized_path_key(path)
        proc.source_path = path
        proc.left = False
        proc.source_by_sidecar[relative_sidecar] = path
        if not proc.theme_synced:
            # Once per editor (a background start has usually done it already): each request here is ~120 ms the user waits
            # for the editor. While the editor is up the autosave tick asks again every 1.5 s, which is what follows a theme change.
            self._sync_photocraft_theme(proc)

    def resize_photocraft(self, width: int, height: int) -> None:
        proc = self._photocraft
        if proc is not None and proc.is_running():
            photocraft_bridge.resize_embedded(proc.hwnd, width, height)
            self._photocraft_resize_size = (width, height)
            self._photocraft_resize_timer.start()

    def _photocraft_resize_elapsed(self) -> None:
        proc, size = self._photocraft, self._photocraft_resize_size
        if proc is None or size is None or not proc.is_running():
            return
        # Its own connection and worker: nothing the user is waiting on queues behind this, and this never waits behind a load.
        self._photocraft_preview_executor.submit(self._refresh_viewport_after_resize, proc, self._photocraft_generation, size)

    def _refresh_viewport_after_resize(self, proc, generation: int, size: tuple[int, int]) -> None:
        """Report where the editor draws its image once it has laid itself out at ``size`` (physical pixels)."""
        deadline = time.monotonic() + self.RESIZE_SETTLE_S
        previous = None
        while generation == self._photocraft_generation and proc.is_running():
            try:
                viewport = proc.fast_lane().call("ui.viewport")
            except photocraft_bridge.PhotoCraftError:
                return
            window_size = viewport.get("window") or {}
            drawn = abs(int(window_size.get("width", -1)) - size[0]) <= 2 and abs(int(window_size.get("height", -1)) - size[1]) <= 2
            layout = (viewport.get("canvas"), (viewport.get("cameraRaw") or {}).get("view"))
            # At the new size, and the same layout twice running: it has finished laying out.
            if drawn and layout == previous:
                self._photocraft_viewport_ready.emit(generation, viewport)
                return
            previous = layout if drawn else None
            if time.monotonic() >= deadline:
                return  # never drew at that size (or the size changed again): the next resize asks again
            time.sleep(self.CAMERA_RAW_POLL_S)

    def _switch_photocraft_document(
        self, proc: photocraft_bridge.PhotoCraftProcess, open_path: str, sidecar_path: str, generation: int | None = None,
        camera_raw: dict | None = None,
    ) -> bool:
        """Save the currently open document to its sidecar if it changed,
        and replace it with the next. Keeps exactly one
        PhotoCraft document open at a time instead of accumulating a tab per
        visited photo, and never touches the original photo file."""
        relative = proc.path_within_root(open_path)
        if relative is None:
            raise photocraft_bridge.PhotoCraftError(f"{open_path!r} is outside this PhotoCraft process's automation root")
        if proc.path_within_write_root(sidecar_path) is None:
            raise photocraft_bridge.PhotoCraftError("The next photo's sidecar is outside the automation write root")
        # A raw going into Camera Raw while Camera Raw shows another: the editor keeps the dialog up and the next photo
        # takes it over (it keeps what was adjusted as the recipe by itself), so the dialog is never closed here, which
        # would show it going away and coming back and cost a round trip.
        in_place = camera_raw is not None and proc.camera_raw_first and getattr(proc.control, "camera_raw_switch_supported", False) is True
        self._save_photocraft_sidecar_if_dirty(proc, commit_raw_draft=not in_place)
        if open_path == sidecar_path and (sidecar_path in proc.pending_stashes or sidecar_path in proc.stash_errors):
            # PhotoCraft keys its in-memory snapshots by the stash path, which
            # is relative to the write root. The disk import path is relative
            # to the read root instead (usually .image_triage_edits/... ).
            # A pending/failed save must restore the snapshot, not read a file
            # which may not yet exist or may contain the previous revision.
            relative = proc.path_within_write_root(sidecar_path)
        # Decode with the old document still visible. PhotoCraft commits the
        # replacement atomically on success, retaining the old one on failure.
        extra = {"camera_raw": camera_raw} if camera_raw is not None else {}
        if generation is None:
            opened = proc.control.app_open(relative, replace=True, **extra)
        else:
            opened = proc.control.app_open(relative, replace=True, should_continue=lambda: generation == self._photocraft_generation, **extra)
        proc.camera_raw_rebaseline = bool(isinstance(opened, dict) and (opened.get("cameraRaw") or {}).get("dialog"))
        if generation is not None and generation != self._photocraft_generation:
            # This RAW finished after the selection moved on and is now the open document.
            # Put the current photo's preview back in front of it.
            self._photocraft_needs_preview_refresh = True
            return False
        proc.preview_source = None
        proc.current_sidecar = Path(sidecar_path)
        proc.opened_revision = 1
        proc.document_generation += 1
        if sidecar_path in proc.stash_errors:
            proc.opened_revision = -1
        return True

    def _save_photocraft_sidecar_if_dirty(
        self, proc: photocraft_bridge.PhotoCraftProcess, *, commit_raw_draft: bool = True, control=None, lean: bool = False
    ) -> None:
        """``control`` selects the connection to use; the fast lane while a RAW open is
        still outstanding on the main one. A ``lean`` save leaves the full-size render out when the editor can save without it: the
        autosave does (the photo stays open), and so do the saves the UI thread waits for (closing the popout, quitting). Any other save
        writes it, even if nothing changed since, when an earlier save left it behind (``_render_behind``)."""
        if proc.current_sidecar is None:
            return
        control = control if control is not None else proc.control
        if proc.camera_raw_first:
            # Leaving the photo ("Done"): a raw's Camera Raw settings are kept as its recipe by the editor and
            # nothing is developed or saved; another kind of dialog applies as one step so the revision below,
            # and the save, include it. Autosave never closes a dialog the user is working in.
            try:
                state = control.call("ui.cameraRaw", {"done": True}) if commit_raw_draft else control.call("ui.cameraRaw")
                self._note_prefetch(proc, state)
            except photocraft_bridge.PhotoCraftError as error:
                if "unknown method" not in str(error):
                    raise
                proc.camera_raw_first = False  # a build without the Camera Raw workspace
            else:
                if state.get("open"):
                    return
                if proc.camera_raw_rebaseline:
                    # The raw's dialog is over (we left the photo, or the user pressed Open or Cancel). Once any develop in
                    # progress has landed, a document the editor holds as unchanged is where editing starts from. One it holds
                    # as changed was edited after the develop, before this poll came round (or the editor does not say): that
                    # edit must be saved, here and now, not taken for the baseline.
                    if any(job.get("state") == "running" and job.get("command") != "app.stash"
                           for job in control.call("jobs.list").get("jobs", [])):
                        return
                    state = control.document_state()
                    proc.camera_raw_rebaseline = False
                    if state is None:
                        return
                    baseline, dirty = state
                    if dirty is False:
                        proc.opened_revision = baseline
                        return
                    proc.save_required[str(proc.current_sidecar)] = (proc.document_generation, baseline)
        revision = control.document_revision()
        if revision is None:
            # Closing a tab in PhotoCraft leaves its process/window alive.
            # Drop only the active binding; pending saves still need polling.
            closed_source = proc.source_path
            proc.current_source = None
            proc.current_sidecar = None
            proc.opened_revision = 0
            if proc.camera_raw_first and closed_source:
                # Closing the full editor's document goes back to the photo in Camera Raw.
                self._photocraft_document_closed.emit(closed_source)
            return
        required = str(proc.current_sidecar) in proc.save_required
        if revision == proc.opened_revision and not required and (lean or not self._render_behind(proc)):
            return
        if revision != proc.opened_revision:
            proc.last_edit_at = time.monotonic()
        relative = proc.path_within_write_root(str(proc.current_sidecar))
        if relative is None:
            raise photocraft_bridge.PhotoCraftError("PhotoCraft sidecar is outside the automation write root")
        proc.current_sidecar.parent.mkdir(parents=True, exist_ok=True)
        render_path = photocraft_bridge.rendered_preview_path(proc.source_path)
        render_relative = proc.path_within_write_root(str(render_path))
        if render_relative is None:
            raise photocraft_bridge.PhotoCraftError("PhotoCraft render is outside the automation write root")
        key = str(proc.current_sidecar)
        if key in proc.pending_stashes:
            self._wait_photocraft_stashes(proc, only=key)
        params = {"path": relative, "wait": False}
        # Also ask for the screen-sized JPEG the viewer shows when this photo is browsed again; an
        # older PhotoCraft build ignores the parameter, and a write root that cannot hold it just
        # leaves the full-size PNG as the fallback.
        display_relative = proc.path_within_write_root(str(photocraft_bridge.display_render_path(proc.source_path)))
        if display_relative is not None:
            params["display"] = display_relative
        params.update({key: value for key, value in self._render_manifest_params(proc, proc.source_path).items() if key != "display"})
        # The full-size PNG is the heaviest encode of a save (211 MB for a 45 MP photo): an autosave, with the photo still open, leaves it
        # out when it can (the screen-sized JPEG is what browsing shows) and leaving the photo writes it.
        lean = lean and display_relative is not None and getattr(proc.control, "stash_without_preview_supported", False) is True
        if lean:
            proc.preview_missing.add(key)
        else:
            params["preview"] = render_relative
            proc.preview_missing.discard(key)
        result = control.call("app.stash", params)
        proc.stash_errors.pop(key, None)
        if result.get("pending"):
            proc.pending_stashes[key] = (int(result["job"]), proc.source_path)
            proc.stash_meta[int(result["job"])] = (key, proc.document_generation, revision)
        else:
            self._clear_save_requirement(proc, key, proc.document_generation, revision)
            self._photocraft_saved.emit(proc.source_path)
        proc.opened_revision = revision

    @staticmethod
    def _render_manifest_params(proc, path: str) -> dict:
        """What a save of ``path`` needs to keep a render manifest (an editor that can): where the manifest goes, the original's
        signature it is made against, and the screen-sized picture's name. Empty for an editor that keeps none."""
        if getattr(proc.control, "render_manifest_supported", False) is not True:
            return {}
        manifest = proc.path_within_write_root(str(photocraft_bridge.render_manifest_path(path)))
        display = proc.path_within_write_root(str(photocraft_bridge.display_render_path(path)))
        source = photocraft_bridge.file_signature(path)
        if manifest is None or display is None or source is None:
            return {}
        return {"manifest": manifest, "display": display, "source": source}

    @staticmethod
    def _clear_save_requirement(proc, key: str, generation: int, revision: int) -> None:
        """A save of ``key`` at ``revision`` of document ``generation`` succeeded: it settles a requirement for that document
        at that revision or earlier, and nothing else."""
        required = proc.save_required.get(key)
        if required is not None and required[0] == generation and revision >= required[1]:
            del proc.save_required[key]

    def _render_behind(self, proc: photocraft_bridge.PhotoCraftProcess) -> bool:
        """Whether the open photo's full-size render is behind its project: an earlier save of this session left it out, or one of an
        earlier session did (the files say so)."""
        return str(proc.current_sidecar) in proc.preview_missing or photocraft_bridge.render_is_behind(proc.source_path)

    def _poll_photocraft_stashes(self, proc) -> None:
        jobs = {int(job["id"]): job for job in proc.control.call("jobs.list").get("jobs", [])}
        for job_id, job in jobs.items():
            if job.get("command") == "app.stash" and job.get("state") == "done" and job_id not in proc.observed_stashes:
                proc.observed_stashes.add(job_id)
                source = proc.source_by_sidecar.get(job.get("result", {}).get("path"))
                if source:
                    if source == proc.source_path and str(proc.current_sidecar) not in proc.pending_stashes:
                        proc.opened_revision = int(job.get("result", {}).get("revision", proc.opened_revision))
                    self._photocraft_saved.emit(source)
        for key, (job_id, source) in list(proc.pending_stashes.items()):
            job = jobs.get(job_id)
            if job is None or job.get("state") == "running":
                continue
            del proc.pending_stashes[key]
            sent = proc.stash_meta.pop(job_id, None)
            if job.get("state") == "done":
                if sent is not None:
                    self._clear_save_requirement(proc, *sent)
                photocraft_bridge.prune_render_generations(source)
                if job_id not in proc.observed_stashes:
                    proc.observed_stashes.add(job_id)
                    self._photocraft_saved.emit(source)
            else:
                error = f"Could not save edits for {source}: {job.get('error', job.get('state'))}. Return to this photo to retry."
                proc.stash_errors[key] = error
                if proc.current_sidecar is not None and str(proc.current_sidecar) == key:
                    proc.opened_revision = -1
                self._photocraft_save_failed.emit(error)

    def _wait_photocraft_stashes(self, proc, *, only: str | None = None) -> None:
        deadline = time.monotonic() + 120.0
        while proc.pending_stashes and (only is None or only in proc.pending_stashes):
            self._poll_photocraft_stashes(proc)
            if time.monotonic() >= deadline:
                raise photocraft_bridge.PhotoCraftError("Timed out waiting for PhotoCraft edits to save")
            if proc.pending_stashes:
                time.sleep(0.05)
        errors = proc.stash_errors if only is None else {key: error for key, error in proc.stash_errors.items() if key == only}
        if errors:
            raise photocraft_bridge.PhotoCraftError(next(iter(errors.values())))

    def prepare_photocraft_close(self) -> bool:
        if self._photocraft is None and (self._photocraft_future is None or self._photocraft_future.done()):
            return True
        try:
            self._pump_until_done(self._photocraft_executor.submit(self._save_active_photocraft))
        except Exception as error:
            choice = QMessageBox.warning(
                self.preview_if_built(), "PhotoCraft edits could not be saved",
                f"{error}\n\nCancel keeps the editor open so you can recover your work. "
                "Discard closes it and loses any edits that have not been saved.",
                QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Discard,
                QMessageBox.StandardButton.Cancel,
            )
            if choice == QMessageBox.StandardButton.Discard:
                self._photocraft_generation += 1
                self._photocraft_save_timer.stop()
                self._pump_until_done(self._photocraft_executor.submit(self._discard_photocraft))
                return True
            return False
        return True

    def _discard_photocraft(self) -> None:
        proc = self._photocraft
        if proc is not None:
            proc.shutdown()
            self._photocraft = None

    def prepare_photocraft_preview_close(self) -> bool:
        """The popout is about to close. Nothing is waited for: the editor is kept, so what changed is saved after the close
        (`_settle_closed_photocraft`) instead of holding the close up for it (2 s for a developed 45 MP raw on a fast machine, far
        longer on a slow one). The popout hides and keeps its native window, so the editor stays where it is. Only if the popout
        is set to close for real (`hide_on_close` off) is the editor moved out first: Qt destroys a closed top-level's native
        window and a child window goes with its parent, whatever process it belongs to; an editor left inside lost its window and
        never answered again (measured: nothing in 84 s), which is why it used to be saved and quit here."""
        preview = self.preview_if_built()
        if preview is None or not getattr(preview, "hide_on_close", False):
            self._park_photocraft()
        return True

    def _park_window_id(self) -> int:
        park = self._photocraft_park
        if park is None:
            # A window of its own, never shown: not a child of the main window, whose native window Qt re-creates (and its
            # children with it) when its flags change. Hidden and frameless, it has no taskbar entry and nothing to look at.
            park = QWidget(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint)
            park.setAttribute(Qt.WidgetAttribute.WA_NativeWindow)
            park.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
            park.resize(1280, 800)
            self._photocraft_park = park
            self._photocraft_park_id = int(park.winId())
        return self._photocraft_park_id

    def _park_photocraft(self) -> None:
        """Moves the editor's window out of the popout into a hidden window that lasts as long as the app. Parked, the editor
        keeps running and answering (about 110 ms a request, against 12 ms on screen), so it can finish a save after the popout
        has gone and is there, warm, the next time it is wanted. On the UI thread."""
        proc = self._photocraft
        if proc is None or not proc.is_running() or proc.attached_parent is None:
            return  # nothing running, or it has not been put in a window yet
        park = self._park_window_id()
        if proc.attached_parent != park:
            photocraft_bridge.embed_in_widget(proc.hwnd, park)
            proc.attached_parent = park

    def _pump_until_done(self, future):
        """Waits, on the UI thread, for work on the editor's worker without stopping this thread's message queue. The editor's
        window is a child of one of ours, and a child that closes (or is re-parented) waits for its parent's thread to answer:
        with this thread in a plain blocking wait the editor could not exit at all (measured: not in 10 s; 0.6 s with the queue
        running), so every quit ran into its timeout and the process was killed. User input stays queued, so nothing else in the
        app runs meanwhile. Returns the work's result, or raises what it raised."""
        app = QApplication.instance()
        if app is None or QThread.currentThread() is not app.thread():
            return future.result()
        while not future.done():
            app.processEvents(QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents)
            wait_for_futures([future], timeout=0.01)
        return future.result()

    def _settle_closed_photocraft(self) -> None:
        """After the popout has closed, on the editor's worker: save what changed, with the full-size render, in one save
        (nobody is waiting, and saving the project first and then again with the render wrote its 300 MB twice). The editor
        is parked and still has the photo open, so a save that fails loses nothing: the user is told, and the photo is as
        they left it when the editor is next brought up."""
        proc = self._photocraft
        if proc is None or not proc.is_running():
            return
        proc.left = True
        try:
            self._save_photocraft_sidecar_if_dirty(proc)
            self._wait_photocraft_stashes(proc)
        except (photocraft_bridge.PhotoCraftError, OSError) as error:
            _log.exception("Could not save the photo the editor had open when the popout closed")
            self._photocraft_close_save_failed.emit(proc.source_path, str(error))

    def _report_close_save_failure(self, path: str, error: str) -> None:
        name = Path(path).name if path else "the photo"
        self._window.statusBar().showMessage(
            f"PhotoCraft could not save the edits to {name}: {error}. They are still open in the editor: open the photo and press the editor key to try again.",
            30000,
        )

    def _save_active_photocraft(self) -> None:
        proc = self._photocraft
        if proc is not None and proc.is_running():
            # The UI thread waits for this (closing the popout, quitting): not for the full-size render too.
            self._save_photocraft_sidecar_if_dirty(proc, lean=True)
            self._wait_photocraft_stashes(proc)

    def run_deferred_editor_start(self) -> None:
        """After boot, once the window is up and idle: start the editor quietly, rooted at the folder being browsed and with no
        photo open, so the first press of the editor key finds it running instead of starting it (~6 s). It runs at below-normal
        priority (never slowing browsing) and idles at about 0% CPU and a couple of hundred MB. Not done when the setting is off,
        no editor is installed, or there is no folder yet; a different folder later means a fresh start then, as before."""
        if os.environ.get("IMAGE_TRIAGE_NO_EDITOR_PRESTART") or not user_settings().value(EDITOR_PRESTART_KEY, True, bool):
            return
        if not self._photocraft_executable or self._photocraft is not None:
            return
        directory = str(getattr(self._window, "_current_folder", "") or "")
        if not directory or self._photocraft_closing:
            return
        self._photocraft_prestart = self._photocraft_executor.submit(self._prestart_photocraft, directory)

    def _prestart_photocraft(self, directory: str) -> None:
        # On the editor's own worker, which also serialises it with a photo being opened: whichever comes first starts the process,
        # and the other uses it. The file-system calls (a folder on an unplugged card can take many seconds) stay off the UI thread.
        if self._photocraft is not None or self._photocraft_closing:
            return
        try:
            if not os.path.isdir(directory):
                return
            write_root = edit_storage.ensure_edit_root(directory)
            proc = photocraft_bridge.launch_photocraft(
                None, read_root=directory, write_root=str(write_root), executable=self._photocraft_executable,
                extra_args=self._photocraft_launch_args(), idle=True,
            )
        except Exception:
            _log.warning("Could not start the editor in the background", exc_info=True)
            return
        if self._photocraft_closing:
            # Image Triage began shutting down while this editor was starting: it is never taken into use, and it is ended here
            # rather than left for the process's end.
            proc.shutdown()
            return
        self._photocraft = proc
        if proc.executable:
            self._photocraft_executable = proc.executable
        try:
            self._sync_photocraft_theme(proc)  # now, while nobody is waiting, instead of at the first photo
        except photocraft_bridge.PhotoCraftError:
            _log.debug("The background editor did not report its theme", exc_info=True)

    def leave_photocraft_editor(self) -> None:
        """The editor key was pressed again: the popout is browsing on its own again. What was adjusted on the photo the editor
        had open is kept (the recipe, or a saved document), off the UI thread; the editor itself stays up, idle, for the next
        press, and nothing about browsing waits on it."""
        self._photocraft_generation += 1  # a load still on its way for that photo must not bring the editor back
        self._photocraft_dwell_timer.stop()
        self._photocraft_dwell_request = None
        self._photocraft_save_timer.stop()
        self._filmstrip_edit_state_stale()  # the photo that was being edited has changed on disk, or soon will
        proc = self._photocraft
        if proc is None or not proc.is_running():
            return
        self._photocraft_executor.submit(self._settle_photocraft_after_leaving, proc)

    def _settle_photocraft_after_leaving(self, proc: photocraft_bridge.PhotoCraftProcess) -> None:
        proc.left = True
        try:
            self._save_photocraft_sidecar_if_dirty(proc)
        except photocraft_bridge.PhotoCraftError:
            _log.exception("Could not settle the photo the editor had open")

    def sync_photocraft_edit_target(self) -> None:
        """Keep an active PhotoCraft edit session pointed at whatever photo the
        popout just navigated to, instead of reopening a static preview."""
        preview = self.preview_if_built()
        if preview is None or not preview.photocraft_edit_active():
            return
        proc = self._photocraft
        if proc is None or not proc.is_running():
            return
        path = preview._source_entries[0].record.path if preview._source_entries else ""
        if not path:
            return
        self.handle_photocraft_edit_requested(path)

    def _save_open_photo_for_render(self, proc, image_path: str) -> None:
        """Saves the editor's open photo, with its full-size render, if it has changes the full-size render must include (for
        ``full_render``, from a worker thread); waits for the save."""

        def save() -> None:
            if proc.current_source != normalized_path_key(image_path):
                raise photocraft_bridge.PhotoCraftError("the editor switched photos during export; retry the export")
            self._save_photocraft_sidecar_if_dirty(proc, commit_raw_draft=False, lean=True)

        self._photocraft_executor.submit(save).result(timeout=300)

    def _launch_editor_for_render(self, folder: str):
        """A hidden editor rooted at ``folder``, for rendering a photo there that no running editor can reach (``full_render``)."""
        write_root = edit_storage.ensure_edit_root(folder)
        return photocraft_bridge.launch_photocraft(
            None, read_root=folder, write_root=str(write_root), executable=self._photocraft_executable, idle=True,
        )

    def shutdown_photocraft(self) -> None:
        self._photocraft_generation += 1
        self._photocraft_save_timer.stop()
        self._photocraft_closing = True
        # Keep Qt pumping while an active render cancels and its hidden editor quits.
        with ThreadPoolExecutor(max_workers=1) as render_shutdown:
            self._pump_until_done(render_shutdown.submit(self._full_render.shutdown))
        prestart = self._photocraft_prestart
        if prestart is not None and not prestart.cancel() and not prestart.done():
            # A background start is under way: wait for it (it ends what it started, see _prestart_photocraft).
            self._pump_until_done(prestart)
        if self._photocraft is None and (self._photocraft_future is None or self._photocraft_future.done()):
            return
        self._pump_until_done(self._photocraft_executor.submit(self._shutdown_photocraft))

    def _shutdown_photocraft(self) -> None:
        """Saves the open photo and quits the editor (quitting the app, a development restart). The UI thread waits for this, its
        message queue running (`_pump_until_done`): the save leaves the full-size render out."""
        proc = self._photocraft
        if proc is None:
            return
        try:
            self._save_photocraft_sidecar_if_dirty(proc, lean=True)
            if proc.is_running():
                self._wait_photocraft_stashes(proc)
        except (photocraft_bridge.PhotoCraftError, OSError) as error:
            _log.error("PhotoCraft shutdown could not save edits: %s", error)
        finally:
            proc.shutdown()
            self._photocraft = None

    def open_preview(self, index: int, *, lightweight_grid_sync: bool = False) -> None:
        logger = perf_logger()
        start = time.perf_counter() if logger.enabled else 0.0
        if not self.preview_is_visible():
            self._window._preview_navigation_dirty = False
        if self._window._winner_ladder_state is not None:
            self.finish_winner_ladder(reopen_preview=False, show_message=False)
        record = self._window._record_at(index)
        if record is not None and record.is_folder:
            # The popout browses photos only; entering a subfolder from inside it would pull the user out of the
            # folder they are reviewing. (A subfolder opened from the grid, with no popout showing, still opens.)
            if not self.preview_is_visible():
                self._window._navigation.select_folder(record.path)
            return
        entries_start = time.perf_counter() if logger.enabled else 0.0
        entries, effective_count, anchor_index = self.preview_entries_for(index)
        if not entries:
            return
        self._begin_navigation_timing(entries[0].source_path)
        if logger.enabled:
            logger.duration(
                "window.open_preview.entries",
                (time.perf_counter() - entries_start) * 1000.0,
                index=index,
                entry_count=len(entries),
                effective_count=effective_count,
                anchor_index=anchor_index,
            )

        controls_start = time.perf_counter() if logger.enabled else 0.0
        if self._window._compare_enabled:
            self._window._compare_count = effective_count
            self._window.preview.set_compare_count(effective_count)
            if anchor_index != index:
                if lightweight_grid_sync:
                    self._window.grid.set_logical_selection([anchor_index], current_index=anchor_index)
                else:
                    self._window.grid.set_current_index(anchor_index)
        self._window.preview.set_winner_ladder_mode(False)
        if logger.enabled:
            logger.duration(
                "window.open_preview.controls",
                (time.perf_counter() - controls_start) * 1000.0,
                compare=self._window._compare_enabled,
            )
        # Reserve GPU capacity for the embedded PhotoCraft editor.
        self._window._records_view.suspend_background_indexing()
        self._window.preview.show_entries(entries)
        self.sync_preview_browse_context(anchor_index if anchor_index >= 0 else index)
        self.schedule_preview_preload(anchor_index if anchor_index >= 0 else index)
        self._rest_on_photo(entries[0].record.path if len(entries) == 1 else None)
        if logger.enabled:
            logger.duration(
                "window.open_preview",
                (time.perf_counter() - start) * 1000.0,
                index=index,
                entry_count=len(entries),
                effective_count=effective_count,
                anchor_index=anchor_index,
                compare=self._window._compare_enabled,
            )

    # A key press this recent is the input that opened the photo being requested; anything older is a
    # different gesture (a click, or a request made by the app itself).
    _NAV_INPUT_WINDOW_S = 0.1

    def _begin_navigation_timing(self, path: str) -> None:
        started = self._nav_input_at
        self._nav_input_at = None
        if started is not None and self.nav_timer.now() - started > self._NAV_INPUT_WINDOW_S:
            started = None
        self.nav_timer.begin(normalized_path_key(path), started)

    def _mark_navigation(self, path: str, *stages: str) -> None:
        key = normalized_path_key(path)
        for stage in stages:
            self.nav_timer.mark(key, stage)

    def _handle_native_layer_painted(self, path: str, placeholder: bool) -> None:
        self._mark_navigation(path, "first_pixel") if placeholder else self._mark_navigation(path, "first_pixel", "preview")

    def browsable_indexes(self) -> list[int]:
        """Indexes into the record list of the photos the popout can browse, in order.

        The record list also holds the folder's subfolders as entries (sorted first). They are cards in the grid, but
        the popout's filmstrip and next/previous walk this list, so they must not be part of it: stepping onto one
        would enter that folder.
        """
        return [index for index, record in enumerate(self._window._records) if not getattr(record, "is_folder", False)]

    @staticmethod
    def _browse_position(browsable: list[int], record_index: int) -> int:
        """Position of ``record_index`` among the browsable photos (the nearest one if it is not itself a photo)."""
        if not browsable:
            return 0
        return min(bisect_left(browsable, record_index), len(browsable) - 1)

    def navigate_preview(self, delta: int) -> None:
        """Step ``delta`` photos (not records) from the grid's current photo; the filmstrip speaks in the same units."""
        self._nav_input_at = self.nav_timer.now()
        logger = perf_logger()
        start = time.perf_counter() if logger.enabled else 0.0
        browsable = self.browsable_indexes()
        if not browsable:
            return

        current = self._window.grid.current_index()
        if current < 0:
            current = browsable[0]
        position = self._browse_position(browsable, current)
        next_index = browsable[max(0, min(len(browsable) - 1, position + delta))]
        if delta:
            self._preview_travel = 1 if delta > 0 else -1
        if next_index != current:
            self._window.grid.set_logical_selection([next_index], current_index=next_index)
            self._window._preview_navigation_dirty = True
        self.open_preview(next_index, lightweight_grid_sync=True)
        if logger.enabled:
            record = self._window._record_at(next_index)
            logger.duration(
                "preview.navigation",
                (time.perf_counter() - start) * 1000.0,
                delta=delta,
                previous_index=current,
                current_index=next_index,
                clamped=next_index == current,
                path=record.path if record is not None else "",
            )

    def sync_preview_browse_context(self, current_index: int) -> None:
        """Feed the popout's filmstrip/nav pill its position in the record list."""
        logger = perf_logger()
        start = time.perf_counter() if logger.enabled else 0.0
        browsable = self.browsable_indexes()

        def record_index(position: int) -> int:
            return browsable[position] if 0 <= position < len(browsable) else -1

        self._window.preview.set_browse_context(
            len(browsable),
            self._browse_position(browsable, current_index),
            lambda position: self.preview_filmstrip_thumb(record_index(position)),
            lambda position: self.preview_filmstrip_tag(record_index(position)),
            lambda position: self.preview_filmstrip_marked(record_index(position)),
        )
        if logger.enabled:
            logger.duration(
                "preview.browse_context",
                (time.perf_counter() - start) * 1000.0,
                current_index=current_index,
                total=len(browsable),
            )

    def _filmstrip_edit_state(self, path: str) -> tuple[int, int]:
        """The photo's edit state without looking at the disk on this thread (``EditStateMemo.peek``): what is remembered, else "no
        edits" until it has been found, which is right for nearly every photo. ``handle_edit_state_changed`` draws the strip again if
        the answer turns out different."""
        return self._window.thumbnail_manager.edit_states.peek(path)

    def _filmstrip_edit_state_stale(self, path: str | None = None) -> None:
        """The edits of ``path`` (of every photo, without it) can have changed: they are looked at again the next time anyone asks."""
        self._window.thumbnail_manager.edit_states.stale(path)

    def handle_edit_state_changed(self, _path: str) -> None:
        """A photo's edits turned out different from what the strip drew it with: draw the strip again."""
        if self.preview_is_visible():
            self._window.preview.refresh_filmstrip()

    def _filmstrip_pixmap(self, image) -> QPixmap:
        """The pixmap for ``image``: the same one every time for the same image, so a thumb that is already showing it need not repaint."""
        key = int(image.cacheKey())
        pixmap = self._filmstrip_pixmaps.get(key)
        if pixmap is None:
            pixmap = QPixmap.fromImage(image)
            if len(self._filmstrip_pixmaps) >= 512:
                self._filmstrip_pixmaps.pop(next(iter(self._filmstrip_pixmaps)))
            self._filmstrip_pixmaps[key] = pixmap
        return pixmap

    def preview_filmstrip_thumb(self, index: int) -> QPixmap | None:
        logger = perf_logger()
        start = time.perf_counter() if logger.enabled else 0.0
        record = self._window._record_at(index)
        if record is None or record.is_folder:
            if logger.enabled:
                logger.duration(
                    "preview.filmstrip_thumbnail",
                    (time.perf_counter() - start) * 1000.0,
                    index=index,
                    state="invalid_record",
                )
            return None
        edit_state = self._filmstrip_edit_state(record.path)
        image = self._window.thumbnail_manager.get_cached(record, self._window.PREVIEW_FILMSTRIP_THUMB_SIZE, edit_state)
        if image is not None and not image.isNull():
            pixmap = self._filmstrip_pixmap(image)
            if logger.enabled:
                logger.duration(
                    "preview.filmstrip_thumbnail",
                    (time.perf_counter() - start) * 1000.0,
                    index=index,
                    path=record.path,
                    state="memory_hit",
                    width=image.width(),
                    height=image.height(),
                )
            return pixmap
        # Kick off an async load and refresh the strip when it lands; fall
        # back to the grid's thumbnail so the strip is rarely empty meanwhile.
        self._window.thumbnail_manager.request_thumbnail(
            record,
            self._window.PREVIEW_FILMSTRIP_THUMB_SIZE,
            priority=25_000,
            drop_if_not_wanted=False,
            edit_state=edit_state,
        )
        fallback = self._window.grid.thumbnail_for(index, edit_state)
        if fallback is not None and not fallback.isNull():
            pixmap = self._filmstrip_pixmap(fallback)
            if logger.enabled:
                logger.duration(
                    "preview.filmstrip_thumbnail",
                    (time.perf_counter() - start) * 1000.0,
                    index=index,
                    path=record.path,
                    state="grid_fallback",
                    width=fallback.width(),
                    height=fallback.height(),
                )
            return pixmap
        if logger.enabled:
            logger.duration(
                "preview.filmstrip_thumbnail",
                (time.perf_counter() - start) * 1000.0,
                index=index,
                path=record.path,
                state="queued_no_fallback",
            )
        return None

    def preview_filmstrip_tag(self, index: int) -> str | None:
        """Status dot for a filmstrip thumb: manual review tags first
        (reject red, winner green), then blue for an untouched AI top pick."""
        record = self._window._record_at(index)
        if record is None:
            return None
        annotation = self._window._annotations.get(record.path)
        if annotation is not None:
            if annotation.reject:
                return preview_studio.REJECT
            if annotation.winner:
                return "#ee719e"
        result = self._window._ai_run.ai_result_for_record(record)
        if result is not None and result.is_top_pick:
            return preview_studio.INFO
        return None

    def handle_preview_filmstrip_thumbnail_ready(self, *_args) -> None:
        if self.preview_is_visible():
            logger = perf_logger()
            if logger.enabled:
                logger.log("preview.filmstrip_refresh_requested")
            self._window.preview.refresh_filmstrip()

    def preview_source_path(self, record: ImageRecord) -> str:
        return record.path

    def displayed_preview_source_path(self, index: int, record: ImageRecord) -> str:
        override = self._window._quick_view_source_overrides.get(record.path, "")
        if override:
            return override
        if record.has_variant_stack:
            return self._window.grid.displayed_variant_path(index)
        return self.preview_source_path(record)

    def preview_entries_for(self, index: int) -> tuple[list[PreviewEntry], int, int]:
        record = self._window._record_at(index)
        if record is None:
            return [], self._window._compare_count, index
        annotation = self._window._annotations.get(record.path, SessionAnnotation())
        displayed_path = self.displayed_preview_source_path(index, record)
        edited_candidates = self.ordered_edited_candidates(record, displayed_path)
        edited_path = edited_candidates[0] if edited_candidates else ""
        if not self._window._compare_enabled:
            return ([
                PreviewEntry(
                    record=record,
                    source_path=displayed_path,
                    winner=annotation.winner,
                    reject=annotation.reject,
                    rating=annotation.rating,
                    edited_path=edited_path,
                    edited_candidates=tuple(edited_candidates),
                    ai_result=self._window._ai_run.ai_result_for_record(record, preferred_path=displayed_path),
                    review_summary=self._window._inspector.review_summary_for_record(record),
                    workflow_summary=self._window._inspector.workflow_summary_for_record(record),
                    workflow_details=self._window._inspector.workflow_detail_lines_for_record(record),
                    placeholder_image=self.preview_placeholder_for_index(index),
                )
            ], 1, index)

        group = self._bracket_detector.group_for(self._window._records, index) if self._window._auto_bracket_enabled else None
        effective_count = self._window._manual_compare_count
        start = index
        if group is not None and group.size >= 2:
            start = group.start_index
            effective_count = group.size

        end = min(len(self._window._records), start + max(1, effective_count))
        entries: list[PreviewEntry] = []
        for item_index, record in enumerate(self._window._records[start:end], start=start):
            annotation = self._window._annotations.get(record.path, SessionAnnotation())
            displayed_path = self.displayed_preview_source_path(item_index, record)
            edited_candidates = self.ordered_edited_candidates(record, displayed_path)
            edited_path = edited_candidates[0] if edited_candidates else ""
            entries.append(
                PreviewEntry(
                    record=record,
                    source_path=displayed_path,
                    winner=annotation.winner,
                    reject=annotation.reject,
                    rating=annotation.rating,
                    edited_path=edited_path,
                    edited_candidates=tuple(edited_candidates),
                    ai_result=self._window._ai_run.ai_result_for_record(record, preferred_path=displayed_path),
                    review_summary=self._window._inspector.review_summary_for_record(record),
                    workflow_summary=self._window._inspector.workflow_summary_for_record(record),
                    workflow_details=self._window._inspector.workflow_detail_lines_for_record(record),
                    placeholder_image=self.preview_placeholder_for_index(item_index),
                )
            )
        return entries, max(1, len(entries)), start

    def ordered_edited_candidates(self, record: ImageRecord, displayed_path: str) -> tuple[str, ...]:
        # The UI thread: the project is matched by size here, never read through.
        saved_render = photocraft_bridge.full_render_path(record.path, verify_project=False) or photocraft_bridge.saved_render_path(record.path)
        if saved_render is not None and saved_render.suffix.lower() == ".png":
            return (str(saved_render), *[path for path in record.edited_paths if path != str(saved_render)])
        if record.edited_paths:
            edited_candidates = tuple(record.edited_paths)
            self._window._edited_candidates_cache[record.path] = edited_candidates
        else:
            edited_candidates = self._window._edited_candidates_cache.get(record.path, ())
        if displayed_path and displayed_path in edited_candidates:
            return (displayed_path, *[path for path in edited_candidates if path != displayed_path])
        return tuple(edited_candidates)

    def dispatch_preview_action(self, path: str, handler, *, preserve_anchor: bool = True) -> None:
        if self._window._collection_mode:
            return
        index = self._window._record_index_by_path.get(path)
        if index is None:
            return
        anchor_path = self._window.preview.anchor_path() if preserve_anchor else ""
        handler(index)
        if not self._window.preview.isVisible():
            return
        reopen_index = None
        if anchor_path:
            reopen_index = self._window._record_index_by_path.get(anchor_path)
        if reopen_index is None:
            next_index = self._window.grid.current_index()
            if 0 <= next_index < len(self._window._records):
                reopen_index = next_index
        if reopen_index is not None:
            self.open_preview(reopen_index)
            return
        self._window.preview.close()
