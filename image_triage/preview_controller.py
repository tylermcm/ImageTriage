"""The full-screen preview and the winner ladder: building and opening the preview, navigation and filmstrip, requests coming back from it, preloading, compare and winner-ladder state. Extracted from MainWindow (docs/mainwindow_decomposition_plan.md, DC-4.4)."""
from __future__ import annotations

import time
from bisect import bisect_left

from PySide6.QtCore import QObject, QSignalBlocker
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication
from pathlib import Path

from .brackets import BracketDetector
from .models import ImageRecord, SessionAnnotation
from .perf import perf_logger
from .preview import FullScreenPreview, PreviewEntry
from .record_ops_controller import UndoAction
from .scanner import normalized_path_key
from .shell_actions import open_in_photoshop
from .ui import load_shortcut_overrides
from .ui import preview_studio

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .window import MainWindow


class PreviewController(QObject):
    """The full-screen preview and the winner ladder: building and opening the preview, navigation and filmstrip, requests coming back from it, preloading, compare and winner-ladder state. Extracted from MainWindow (docs/mainwindow_decomposition_plan.md, DC-4.4)."""

    def __init__(self, window: "MainWindow") -> None:
        super().__init__(window)
        self._window = window
        self._bracket_detector = BracketDetector()
        self._preview_preload_index: int | None = None

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
        preview.winner_requested.connect(self.handle_preview_winner_requested)
        preview.reject_requested.connect(self.handle_preview_reject_requested)
        preview.keep_requested.connect(self.handle_preview_keep_requested)
        preview.delete_requested.connect(self.handle_preview_delete_requested)
        preview.move_requested.connect(self.handle_preview_move_requested)
        preview.tag_requested.connect(self.handle_preview_tag_requested)
        preview.rating_requested.connect(self.handle_preview_rating_requested)
        preview.winner_ladder_choice_requested.connect(self.handle_preview_winner_ladder_choice)
        preview.winner_ladder_skip_requested.connect(self.handle_preview_winner_ladder_skip)
        preview.closed.connect(self.handle_preview_closed)

        preview.set_photoshop_available(bool(self._window._photoshop_executable))
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
        if show_message and winner_path:
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

        add(index)
        delta = 1
        while len(ordered) < limit and delta < len(self._window._records):
            add(index + delta)
            if len(ordered) >= limit:
                break
            add(index - delta)
            delta += 1

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
        # The full-screen editor needs the GPU for masking (SAM / OneFormer /
        # BiRefNet); pause background indexing so it isn't fighting for CUDA.
        self._window._records_view.suspend_background_indexing()
        self._window.preview.show_entries(entries)
        self.sync_preview_browse_context(anchor_index if anchor_index >= 0 else index)
        self.schedule_preview_preload(anchor_index if anchor_index >= 0 else index)
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
        )
        if logger.enabled:
            logger.duration(
                "preview.browse_context",
                (time.perf_counter() - start) * 1000.0,
                current_index=current_index,
                total=len(browsable),
            )

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
        image = self._window.thumbnail_manager.get_cached(record, self._window.PREVIEW_FILMSTRIP_THUMB_SIZE)
        if image is not None and not image.isNull():
            pixmap = QPixmap.fromImage(image)
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
        )
        fallback = self._window.grid.thumbnail_for(index)
        if fallback is not None and not fallback.isNull():
            pixmap = QPixmap.fromImage(fallback)
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
