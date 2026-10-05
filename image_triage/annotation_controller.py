"""Marking and review state: winners, rejects, tags, batch marks, persistence and winner sync, pairwise feedback, review counts and the burst view. Extracted from MainWindow (docs/mainwindow_decomposition_plan.md, DC-4.4)."""
from __future__ import annotations

import json
import os
import time

from PySide6.QtCore import QObject, Qt
from PySide6.QtWidgets import QInputDialog, QMessageBox
from pathlib import Path

from .ai_training import prepare_hidden_ai_training_workspace
from .annotation_queue import WinnerSyncRequest
from .bursts import find_burst_groups
from .formats import RAW_SUFFIXES
from .grid import BurstVisualInfo
from .models import ImageRecord, SessionAnnotation, WinnerMode
from .perf import perf_logger
from .record_ops_controller import UndoAction
from .records_view_cache import ViewInvalidationReason
from .review_workflows import AI_DISAGREEMENT_SOURCE_MODE, ai_strength, build_pairwise_label_payload, current_timestamp, ai_disagreement_group_leader_path, disagreement_level_for
from .scanner import normalized_path_key
from .shell_actions import open_in_photoshop
from .tasks.annotation_tasks import AnnotationHydrationTask

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .window import MainWindow


class AnnotationController(QObject):
    """Marking and review state: winners, rejects, tags, batch marks, persistence and winner sync, pairwise feedback, review counts and the burst view. Extracted from MainWindow (docs/mainwindow_decomposition_plan.md, DC-4.4)."""

    def __init__(self, window: "MainWindow") -> None:
        super().__init__(window)
        self._window = window

    def save_fast_rating_hint_state(self) -> None:
        self._window._settings.setValue(self._window.FAST_RATING_HINT_DISABLED_KEY, self._window._fast_rating_hint_disabled)
        self._window._settings.setValue(self._window.FAST_RATING_HINT_SESSIONS_KEY, json.dumps(sorted(self._window._fast_rating_hint_sessions)))

    def schedule_scope_enrichment_refresh(self) -> None:
        if not self._window._all_records:
            return
        self._window._scope_enrichment_debounce_timer.start()

    def start_annotation_hydration(self, records: list[ImageRecord]) -> None:
        if not records:
            self._window._annotation_hydration_token += 1
            if self._window._active_annotation_hydration_task is not None:
                self._window._active_annotation_hydration_task.cancel()
            self._window._active_annotation_hydration_task = None
            self._window._annotation_hydration_dirty_paths.clear()
            self._window._annotation_hydration_pending_clear_paths.clear()
            self._window._annotation_reapply_timer.stop()
            return
        if self._window._active_ai_task is not None:
            self._window._ai_run.mark_background_review_work_deferred_for_ai(reason="annotation_hydration")
            return
        self._window._annotation_hydration_token += 1
        token = self._window._annotation_hydration_token
        previous_task = self._window._active_annotation_hydration_task
        if previous_task is not None:
            previous_task.cancel()
        self._window._active_annotation_hydration_task = None
        self._window._annotation_hydration_dirty_paths.clear()
        self._window._annotation_hydration_pending_clear_paths = {record.path for record in records if record.path in self._window._annotations}
        self._window._annotation_reapply_timer.stop()
        scope_key = self._window._projects.current_scope_key()
        task = AnnotationHydrationTask(
            scope_key=scope_key,
            token=token,
            session_id=self._window._session_id,
            records=tuple(records),
            prioritized_paths=tuple(self._window.grid.visible_item_paths(limit=240)),
        )
        task.signals.chunk.connect(self.handle_annotation_hydration_chunk, Qt.ConnectionType.QueuedConnection)
        task.signals.finished.connect(self.handle_annotation_hydration_finished, Qt.ConnectionType.QueuedConnection)
        task.signals.failed.connect(self.handle_annotation_hydration_failed, Qt.ConnectionType.QueuedConnection)
        self._window._active_annotation_hydration_task = task
        self._window._annotation_hydration_pool.start(task)

    def handle_annotation_hydration_chunk(self, scope_key: str, token: int, chunk: dict[str, SessionAnnotation]) -> None:
        if token != self._window._annotation_hydration_token or scope_key != self._window._projects.current_scope_key():
            return
        if not chunk:
            return
        changed_paths: list[str] = []
        for path, annotation in chunk.items():
            self._window._annotation_hydration_pending_clear_paths.discard(path)
            previous = self._window._annotations.get(path)
            if previous == annotation:
                continue
            self._window._annotations[path] = annotation
            changed_paths.append(path)
        if not changed_paths:
            return
        self._window._records_view_cache.mark(ViewInvalidationReason.ANNOTATION_CHANGED, paths=changed_paths)
        self._window._annotation_hydration_dirty_paths.update(changed_paths)
        self._window._annotation_reapply_timer.start()

    def flush_annotation_hydration_updates(self) -> None:
        if not self._window._annotation_hydration_dirty_paths:
            return
        changed_paths = sorted(self._window._annotation_hydration_dirty_paths)
        self._window._annotation_hydration_dirty_paths.clear()
        current_path = self._window._records_view.current_visible_record_path()
        self.apply_annotation_change_effects(changed_paths, current_path=current_path)

    def handle_annotation_hydration_finished(self, scope_key: str, token: int) -> None:
        if token != self._window._annotation_hydration_token or scope_key != self._window._projects.current_scope_key():
            return
        self._window._active_annotation_hydration_task = None
        if self._window._annotation_hydration_pending_clear_paths:
            stale_paths = sorted(self._window._annotation_hydration_pending_clear_paths)
            self._window._annotation_hydration_pending_clear_paths.clear()
            for path in stale_paths:
                self._window._annotations.pop(path, None)
            self._window._annotation_hydration_dirty_paths.update(stale_paths)
        self.flush_annotation_hydration_updates()

    def handle_annotation_hydration_failed(self, scope_key: str, token: int, message: str) -> None:
        if token != self._window._annotation_hydration_token or scope_key != self._window._projects.current_scope_key():
            return
        self._window._active_annotation_hydration_task = None
        self._window._annotation_hydration_dirty_paths.clear()
        self._window._annotation_hydration_pending_clear_paths.clear()
        self._window.statusBar().showMessage(f"Loaded folder, but annotation hydration failed: {message}")

    def recalculate_review_counts(self) -> None:
        accepted = 0
        rejected = 0
        for record in self._window._all_records:
            annotation = self._window._annotations.get(record.path)
            if annotation is None:
                continue
            if annotation.winner:
                accepted += 1
            if annotation.reject:
                rejected += 1
        self._window._accepted_count = accepted
        self._window._rejected_count = rejected
        self._window._unreviewed_count = max(0, len(self._window._all_records) - accepted - rejected)

    def apply_review_count_delta(
        self,
        previous_annotation: SessionAnnotation | None,
        annotation: SessionAnnotation | None,
    ) -> None:
        previous_accepted = 1 if previous_annotation is not None and previous_annotation.winner else 0
        previous_rejected = 1 if previous_annotation is not None and previous_annotation.reject else 0
        next_accepted = 1 if annotation is not None and annotation.winner else 0
        next_rejected = 1 if annotation is not None and annotation.reject else 0
        self._window._accepted_count = max(0, self._window._accepted_count + next_accepted - previous_accepted)
        self._window._rejected_count = max(0, self._window._rejected_count + next_rejected - previous_rejected)
        self._window._unreviewed_count = max(0, len(self._window._all_records) - self._window._accepted_count - self._window._rejected_count)

    def review_insight_for_path(self, path: str):
        if not path or self._window._review_intelligence is None:
            return None
        return self._window._review_intelligence.insight_for_path(path)

    def annotation_prefers_frame(self, annotation: SessionAnnotation | None) -> bool:
        if annotation is None:
            return False
        return annotation.winner

    def comparison_target_for_preference(self, record: ImageRecord, ai_result, burst_recommendation) -> str:
        if burst_recommendation is not None and burst_recommendation.recommended_path:
            if normalized_path_key(burst_recommendation.recommended_path) != normalized_path_key(record.path):
                return burst_recommendation.recommended_path
        if ai_result is not None and self._window._ai_bundle is not None and ai_result.group_size > 1 and not ai_result.is_top_pick:
            group_results = self._window._ai_bundle.group_results(ai_result.group_id)
            if group_results:
                target_path = group_results[0].file_path
                if normalized_path_key(target_path) != normalized_path_key(record.path):
                    return target_path
        return ""

    def build_pairwise_feedback_payload(self, preferred_path: str, other_path: str) -> dict[str, object]:
        preferred_record = self._window._record_for_path(preferred_path)
        other_record = self._window._record_for_path(other_path)
        preferred_ai = self._window._ai_run.ai_result_for_record(preferred_record) if preferred_record is not None else None
        other_ai = self._window._ai_run.ai_result_for_record(other_record) if other_record is not None else None
        preferred_review = self.review_insight_for_path(preferred_path)
        other_review = self.review_insight_for_path(other_path)
        return {
            "preferred_path": preferred_path,
            "other_path": other_path,
            "preferred_detail_score": float(getattr(preferred_review, "detail_score", 0.0) or 0.0),
            "other_detail_score": float(getattr(other_review, "detail_score", 0.0) or 0.0),
            "preferred_ai_strength": ai_strength(preferred_ai),
            "other_ai_strength": ai_strength(other_ai),
            "preferred_ai_bucket": preferred_ai.confidence_bucket.value if preferred_ai is not None else "",
            "other_ai_bucket": other_ai.confidence_bucket.value if other_ai is not None else "",
            "preferred_ai_score": float(preferred_ai.score) if preferred_ai is not None else None,
            "other_ai_score": float(other_ai.score) if other_ai is not None else None,
            "preferred_ai_normalized_score": (
                float(preferred_ai.normalized_score) if preferred_ai is not None and preferred_ai.normalized_score is not None else None
            ),
            "other_ai_normalized_score": (
                float(other_ai.normalized_score) if other_ai is not None and other_ai.normalized_score is not None else None
            ),
            "preferred_ai_rank_in_group": int(preferred_ai.rank_in_group) if preferred_ai is not None else 0,
            "other_ai_rank_in_group": int(other_ai.rank_in_group) if other_ai is not None else 0,
        }

    def append_jsonl_record(self, path: Path, payload: dict[str, object]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=True) + "\n")

    def record_pairwise_preference(
        self,
        *,
        left_path: str,
        right_path: str,
        preferred_path: str,
        source_mode: str,
        group_id: str = "",
        extra_payload: dict[str, object] | None = None,
    ) -> None:
        if not self._window._current_folder or not left_path or not right_path or not preferred_path:
            return
        label_payload = build_pairwise_label_payload(
            folder=self._window._current_folder,
            left_path=left_path,
            right_path=right_path,
            preferred_path=preferred_path,
            source_mode=source_mode,
            cluster_id=group_id,
            annotator_id=self._window._session_id,
        )
        try:
            training_paths = prepare_hidden_ai_training_workspace(self._window._current_folder)
            self.append_jsonl_record(training_paths.pairwise_labels_path, label_payload)
        except OSError:
            return

        other_path = right_path if normalized_path_key(preferred_path) == normalized_path_key(left_path) else left_path
        payload = self.build_pairwise_feedback_payload(preferred_path, other_path)
        if extra_payload:
            payload.update(extra_payload)
        payload.update(
            {
                "label_id": label_payload["label_id"],
                "left_path": left_path,
                "right_path": right_path,
            }
        )
        preferred_record = self._window._record_for_path(preferred_path)
        preferred_annotation = self._window._annotations.get(preferred_path, SessionAnnotation())
        preferred_ai = self._window._ai_run.ai_result_for_record(preferred_record) if preferred_record is not None else None
        self._window._decision_store.record_correction_event(
            self._window._session_id,
            folder_path=self._window._current_folder,
            record_path=preferred_path,
            other_path=other_path,
            image_id=str(label_payload.get("image_a_id") or ""),
            other_image_id=str(label_payload.get("image_b_id") or ""),
            preferred_image_id=str(label_payload.get("preferred_image_id") or ""),
            group_id=group_id,
            event_type="pairwise_preference",
            decision=str(label_payload.get("decision") or ""),
            source_mode=source_mode,
            ai_bucket=preferred_ai.confidence_bucket.value if preferred_ai is not None else "",
            ai_rank_in_group=preferred_ai.rank_in_group if preferred_ai is not None else 0,
            ai_group_size=preferred_ai.group_size if preferred_ai is not None else 0,
            review_round=preferred_annotation.review_round,
            payload=payload,
        )
        self.schedule_scope_enrichment_refresh()

    def record_annotation_feedback_event(
        self,
        record: ImageRecord,
        annotation: SessionAnnotation,
        ai_result,
        *,
        source_mode: str,
        payload: dict[str, object],
    ) -> None:
        if not self._window._current_folder or ai_result is None:
            return
        self._window._decision_store.record_correction_event(
            self._window._session_id,
            folder_path=self._window._current_folder,
            record_path=record.path,
            image_id=ai_result.image_id,
            preferred_image_id=ai_result.image_id,
            group_id=ai_result.group_id,
            event_type="annotation_feedback",
            decision="",
            source_mode=source_mode,
            ai_bucket=ai_result.confidence_bucket.value,
            ai_rank_in_group=ai_result.rank_in_group,
            ai_group_size=ai_result.group_size,
            review_round=annotation.review_round,
            payload=payload,
        )

    def capture_annotation_feedback(
        self,
        record: ImageRecord,
        previous_annotation: SessionAnnotation,
        annotation: SessionAnnotation,
        *,
        source_mode: str,
    ) -> None:
        ai_result = self._window._ai_run.ai_result_for_record(record)
        burst_recommendation = self._window._inspector.burst_recommendation_for_record(record)
        previous_level = disagreement_level_for(previous_annotation, ai_result)
        new_level = disagreement_level_for(annotation, ai_result)
        if ai_result is not None and (
            new_level
            or previous_annotation.rating != annotation.rating
            or previous_annotation.winner != annotation.winner
            or previous_annotation.reject != annotation.reject
        ):
            payload = {
                "timestamp": current_timestamp(),
                "previous_winner": previous_annotation.winner,
                "previous_reject": previous_annotation.reject,
                "previous_rating": previous_annotation.rating,
                "previous_review_round": previous_annotation.review_round,
                "winner": annotation.winner,
                "reject": annotation.reject,
                "rating": annotation.rating,
                "review_round": annotation.review_round,
                "disagreement_level": new_level,
                "previous_disagreement_level": previous_level,
                "manual_source_mode": source_mode,
                "ai_group_id": ai_result.group_id,
                "ai_score": float(ai_result.score),
                "ai_normalized_score": (
                    float(ai_result.normalized_score) if ai_result.normalized_score is not None else None
                ),
                "ai_folder_percentile": (
                    float(ai_result.folder_percentile) if ai_result.folder_percentile is not None else None
                ),
                "ai_score_gap_to_next": (
                    float(ai_result.score_gap_to_next) if ai_result.score_gap_to_next is not None else None
                ),
                "ai_score_gap_to_top": (
                    float(ai_result.score_gap_to_top) if ai_result.score_gap_to_top is not None else None
                ),
                "ai_confidence_bucket": ai_result.confidence_bucket.value,
                "ai_rank_in_group": int(ai_result.rank_in_group),
                "ai_group_size": int(ai_result.group_size),
            }
            self.record_annotation_feedback_event(record, annotation, ai_result, source_mode=source_mode, payload=payload)

        if self.annotation_prefers_frame(previous_annotation) or not self.annotation_prefers_frame(annotation):
            return
        ai_target_path = ""
        if ai_result is not None and self._window._ai_bundle is not None:
            ai_target_path = ai_disagreement_group_leader_path(
                record.path,
                ai_result,
                self._window._ai_bundle.group_results(ai_result.group_id),
            )
        target_path = ai_target_path or self.comparison_target_for_preference(record, ai_result, burst_recommendation)
        if not target_path:
            return
        group_id = ""
        if ai_target_path and ai_result is not None:
            group_id = ai_result.group_id
        elif burst_recommendation is not None:
            group_id = burst_recommendation.group_id
        elif ai_result is not None:
            group_id = ai_result.group_id
        self.record_pairwise_preference(
            left_path=record.path,
            right_path=target_path,
            preferred_path=record.path,
            source_mode=AI_DISAGREEMENT_SOURCE_MODE if ai_target_path else source_mode,
            group_id=group_id,
            extra_payload={
                "record_path": record.path,
                "comparison_target": target_path,
                "manual_source_mode": source_mode,
                "disagreement_level": new_level,
                "ai_disagreement_pair": bool(ai_target_path),
            },
        )

    def record_should_hint_fast_rating(self, record: ImageRecord) -> bool:
        for path in record.stack_paths:
            suffix = Path(path).suffix.lower()
            if suffix in RAW_SUFFIXES:
                return True
            try:
                if os.path.getsize(path) >= self._window.FAST_RATING_HINT_SIZE_BYTES:
                    return True
            except OSError:
                continue
        return record.size >= self._window.FAST_RATING_HINT_SIZE_BYTES

    def maybe_show_fast_rating_hint(self, records: list[ImageRecord]) -> bool:
        if (
            self._window._fast_rating_hint_disabled
            or self._window._winner_mode != WinnerMode.COPY
            or self._window._session_id in self._window._fast_rating_hint_sessions
            or not records
        ):
            return True
        if not any(self.record_should_hint_fast_rating(record) for record in records):
            return True

        message = QMessageBox(self._window)
        message.setIcon(QMessageBox.Icon.Information)
        message.setWindowTitle("Workflow Tip")
        message.setText(
            "Workflow is set to copy winners.\n\n"
            "For quicker winner marking with RAW or large files, consider changing Winner handling to "
            "'Link To _winners'.\n\n"
            "Open Settings to change it."
        )
        continue_button = message.addButton("Continue", QMessageBox.ButtonRole.AcceptRole)
        dont_show_button = message.addButton("Don't Show Again", QMessageBox.ButtonRole.ActionRole)
        message.setDefaultButton(continue_button)
        message.exec()

        self._window._fast_rating_hint_sessions.add(self._window._session_id)
        if message.clickedButton() is dont_show_button:
            self._window._fast_rating_hint_disabled = True
        self.save_fast_rating_hint_state()
        return True

    def delete_record(self, index: int) -> None:
        self._window._record_ops.delete_record(index)

    def keep_record(self, index: int) -> None:
        self._window._record_ops.keep_record(index)

    def tag_record(self, index: int) -> None:
        record = self._window._record_at(index)
        if record is None:
            return

        current = ", ".join(self._window._annotations.get(record.path, SessionAnnotation()).tags)
        value, accepted = QInputDialog.getText(
            self._window,
            "Tag Image",
            "Comma-separated tags",
            text=current,
        )
        if not accepted:
            return

        tags = tuple(tag.strip() for tag in value.split(",") if tag.strip())
        annotation = self._window._annotations.setdefault(record.path, SessionAnnotation())
        previous_annotation = self._window._annotation_snapshot(annotation)
        if previous_annotation.tags == tags:
            return
        annotation.tags = tags
        self.queue_annotation_persist(record, previous_annotation=previous_annotation)
        self.apply_annotation_change_effects([record.path], current_path=record.path)
        if tags:
            self._window.statusBar().showMessage(f"Tagged {record.name}: {', '.join(tags)}")
        else:
            self._window.statusBar().showMessage(f"Cleared tags for {record.name}")

    def queue_annotation_persist(
        self,
        record: ImageRecord,
        *,
        previous_annotation: SessionAnnotation | None = None,
        session_id: str | None = None,
        winner_sync: WinnerSyncRequest | None = None,
    ) -> None:
        self._window._records_view_cache.mark(ViewInvalidationReason.ANNOTATION_CHANGED, paths=[record.path])
        target_session = session_id or self._window._session_id
        annotation = self._window._annotations.get(record.path)
        self._window._annotation_persistence_queue.enqueue(
            record.path,
            annotation,
            record=record,
            session_id=target_session,
            previous_annotation=previous_annotation,
            winner_sync=winner_sync,
        )

    def build_winner_sync_request(
        self, record: ImageRecord, winner_enabled: bool, folder: str
    ) -> WinnerSyncRequest | None:
        """The winner-copy-sync work for one annotation change, queued to run
        on the background persistence worker instead of blocking the UI."""
        if self._window._is_winners_folder(folder):
            return None
        return WinnerSyncRequest(
            winner_enabled=winner_enabled,
            folder=folder,
            winner_mode=self._window._winner_mode,
            source_paths=self._window._record_paths(record),
        )

    def handle_annotation_persist_failed(self, path: str, message: str) -> None:
        rollback = self._window._annotation_persistence_queue.rollback(path)
        if rollback is None:
            self._window.statusBar().showMessage(f"Could not persist annotation for {Path(path).name or path}: {message}")
            return
        if rollback.is_empty:
            self._window._annotations.pop(path, None)
        else:
            self._window._annotations[path] = rollback
        self.apply_annotation_change_effects([path], current_path=path)
        self._window.statusBar().showMessage(f"Rolled back annotation for {Path(path).name or path}: {message}")

    def handle_annotation_persist_warning(self, path: str, message: str) -> None:
        self._window.statusBar().showMessage(f"Saved app state for {Path(path).name or path}, but sidecar sync failed: {message}")

    def handle_winner_sync_failed(self, path: str, message: str) -> None:
        rollback = self._window._annotation_persistence_queue.rollback(path)
        if rollback is None:
            self._window.statusBar().showMessage(f"Could not update winner copy for {Path(path).name or path}: {message}")
            return
        if rollback.is_empty:
            self._window._annotations.pop(path, None)
        else:
            self._window._annotations[path] = rollback
        self.apply_annotation_change_effects([path], current_path=path)
        self._window.statusBar().showMessage(f"Reverted winner state for {Path(path).name or path}: {message}")

    def handle_winner_kept(self, path: str, kept_csv: str) -> None:
        self._window.statusBar().showMessage(
            f"Winner removed: {Path(path).name or path} (left {kept_csv} in _winners: not a copy Image Triage made)"
        )

    def apply_annotation_change_effects(
        self,
        changed_paths: list[str] | tuple[str, ...] | set[str],
        *,
        current_path: str | None = None,
        counts_already_updated: bool = False,
    ) -> None:
        logger = perf_logger()
        start = time.perf_counter() if logger.enabled else 0.0
        paths = [path for path in changed_paths if path]
        if not paths:
            return
        self._window._records_view_cache.mark(ViewInvalidationReason.ANNOTATION_CHANGED, paths=paths)
        if self._window._records_view.annotation_change_affects_active_filter():
            self._window._views.apply_records_view(current_path=current_path)
            if logger.enabled:
                logger.duration("annotation.change_effects", (time.perf_counter() - start) * 1000.0, paths=len(paths), reapply_view=True)
            return
        self._window._scan.refresh_workflow_insights_cache(changed_paths=set(paths))
        self._window._views.set_annotation_views(paths)
        self._window.grid.update_review_workflow_insights(self._window._workflow_insights_by_path, paths)
        if not counts_already_updated:
            self.recalculate_review_counts()
        self._window._records_view.update_filter_summary()
        current_change_emitted = False
        if current_path:
            next_index = self._window._record_index_by_path.get(current_path)
            if next_index is not None:
                self._window.grid.set_current_index(next_index)
                current_change_emitted = True
        if not current_change_emitted:
            self._window._inspector.update_action_states()
            self._window._inspector.update_status()
        if logger.enabled:
            logger.duration("annotation.change_effects", (time.perf_counter() - start) * 1000.0, paths=len(paths), reapply_view=False)

    def toggle_winner(
        self,
        index: int,
        *,
        advance_override: bool | None = None,
        current_path_override: str | None = None,
    ) -> None:
        logger = perf_logger()
        start = time.perf_counter() if logger.enabled else 0.0
        record = self._window._record_at(index)
        if record is None:
            return
        if not self._window._current_folder:
            self._window.statusBar().showMessage("Winner/reject actions stay folder-first. Open the source folder to change those states.")
            return
        if self._window._is_recycle_folder():
            self._window._record_ops.restore_record(index)
            return
        if self._window._is_winners_folder():
            self.delete_record(index)
            self._window.statusBar().showMessage(f"Removed winner copy: {record.name}")
            return

        should_advance = self._window._auto_advance_enabled if advance_override is None else advance_override
        next_path = self._window._next_visible_path(index) if should_advance else record.path
        if current_path_override is not None:
            next_path = current_path_override
        annotation = self._window._annotations.setdefault(record.path, SessionAnnotation())
        if not annotation.winner and not self.maybe_show_fast_rating_hint([record]):
            return
        previous_annotation = self._window._annotation_snapshot(annotation)
        previous_winner = annotation.winner
        previous_reject = annotation.reject
        previous_photoshop = annotation.photoshop
        annotation.winner = not annotation.winner
        if annotation.winner:
            annotation.reject = False

        winner_sync = self.build_winner_sync_request(record, annotation.winner, self._window._current_folder)

        self._window._record_ops.push_undo(
            UndoAction(
                kind="annotation",
                primary_path=record.path,
                original_winner=previous_winner,
                original_reject=previous_reject,
                original_photoshop=previous_photoshop,
                rating=annotation.rating,
                tags=annotation.tags,
                original_review_round=previous_annotation.review_round,
                folder=self._window._current_folder,
                source_paths=self._window._record_paths(record),
                session_id=self._window._session_id,
                winner_mode=self._window._winner_mode.value,
            )
        )
        self.queue_annotation_persist(record, previous_annotation=previous_annotation, winner_sync=winner_sync)
        self._window._aiculler.sync_annotation_to_global_adapter_label(record, annotation)
        self.capture_annotation_feedback(record, previous_annotation, annotation, source_mode="winner_toggle")
        self.apply_review_count_delta(previous_annotation, annotation)
        self.apply_annotation_change_effects([record.path], current_path=next_path, counts_already_updated=True)
        if annotation.winner:
            self._window.statusBar().showMessage(f"Winner added: {record.name}")
        else:
            self._window.statusBar().showMessage(f"Winner removed: {record.name}")
        if logger.enabled:
            logger.duration("annotation.winner_toggle", (time.perf_counter() - start) * 1000.0, path=record.path, winner=annotation.winner, advance=should_advance)

    def toggle_reject(
        self,
        index: int,
        *,
        advance_override: bool | None = None,
        current_path_override: str | None = None,
    ) -> None:
        logger = perf_logger()
        start = time.perf_counter() if logger.enabled else 0.0
        record = self._window._record_at(index)
        if record is None:
            return
        if not self._window._current_folder:
            self._window.statusBar().showMessage("Winner/reject actions stay folder-first. Open the source folder to change those states.")
            return

        should_advance = self._window._auto_advance_enabled if advance_override is None else advance_override
        next_path = self._window._next_visible_path(index) if should_advance else record.path
        if current_path_override is not None:
            next_path = current_path_override

        annotation = self._window._annotations.setdefault(record.path, SessionAnnotation())
        if not annotation.reject and not self.maybe_show_fast_rating_hint([record]):
            return
        previous_annotation = self._window._annotation_snapshot(annotation)
        previous_winner = annotation.winner
        previous_reject = annotation.reject
        previous_photoshop = annotation.photoshop
        annotation.reject = not annotation.reject
        if annotation.reject:
            annotation.winner = False

        winner_sync = (
            self.build_winner_sync_request(record, annotation.winner, self._window._current_folder)
            if previous_winner != annotation.winner
            else None
        )

        self._window._record_ops.push_undo(
            UndoAction(
                kind="annotation",
                primary_path=record.path,
                original_winner=previous_winner,
                original_reject=previous_reject,
                original_photoshop=previous_photoshop,
                rating=annotation.rating,
                tags=annotation.tags,
                original_review_round=previous_annotation.review_round,
                folder=self._window._current_folder,
                source_paths=self._window._record_paths(record),
                session_id=self._window._session_id,
                winner_mode=self._window._winner_mode.value,
            )
        )
        self.queue_annotation_persist(record, previous_annotation=previous_annotation, winner_sync=winner_sync)
        self._window._aiculler.sync_annotation_to_global_adapter_label(record, annotation)
        self.capture_annotation_feedback(record, previous_annotation, annotation, source_mode="reject_toggle")
        self.apply_review_count_delta(previous_annotation, annotation)
        self.apply_annotation_change_effects([record.path], current_path=next_path, counts_already_updated=True)
        if annotation.reject:
            self._window.statusBar().showMessage(f"Rejected: {record.name}")
        else:
            self._window.statusBar().showMessage(f"Reject removed: {record.name}")
        if logger.enabled:
            logger.duration("annotation.reject_toggle", (time.perf_counter() - start) * 1000.0, path=record.path, reject=annotation.reject, advance=should_advance)

    def batch_set_winner(self, records: list[ImageRecord]) -> None:
        if not records:
            return
        if self._window._is_winners_folder():
            self._window._record_ops.batch_delete_records(records)
            return
        candidates = [record for record in records if not self._window._annotations.get(record.path, SessionAnnotation()).winner]
        if not self.maybe_show_fast_rating_hint(candidates):
            return
        changed, failures = self.batch_apply_annotation_state(records, winner=True, reject=False, source_mode="winner_toggle")
        if failures:
            self._window.statusBar().showMessage(f"Marked {changed} winner image(s); {failures} failed to sync winner artifacts")
            return
        self._window.statusBar().showMessage(f"Marked {changed} winner image(s)")

    def batch_set_reject(self, records: list[ImageRecord]) -> None:
        if not records:
            return
        candidates = [record for record in records if not self._window._annotations.get(record.path, SessionAnnotation()).reject]
        if not self.maybe_show_fast_rating_hint(candidates):
            return
        changed, failures = self.batch_apply_annotation_state(records, winner=False, reject=True, source_mode="reject_toggle")
        if failures:
            self._window.statusBar().showMessage(f"Rejected {changed} image(s); {failures} failed to update")
            return
        self._window.statusBar().showMessage(f"Rejected {changed} image(s)")

    def batch_apply_annotation_state(
        self,
        records: list[ImageRecord],
        *,
        winner: bool,
        reject: bool,
        source_mode: str,
    ) -> tuple[int, int]:
        if not records:
            return 0, 0

        changed_paths: list[str] = []
        undo_actions: list[UndoAction] = []
        failures = 0
        current_path = self._window._records_view.current_visible_record_path() or records[0].path

        for record in records:
            annotation = self._window._annotations.setdefault(record.path, SessionAnnotation())
            previous_annotation = self._window._annotation_snapshot(annotation)
            target_winner = bool(winner)
            target_reject = bool(reject)
            if target_winner:
                target_reject = False
            if target_reject:
                target_winner = False
            if previous_annotation.winner == target_winner and previous_annotation.reject == target_reject:
                continue

            annotation.winner = target_winner
            annotation.reject = target_reject
            winner_sync = (
                self.build_winner_sync_request(record, annotation.winner, self._window._current_folder)
                if previous_annotation.winner != annotation.winner
                else None
            )

            undo_actions.append(
                UndoAction(
                    kind="annotation",
                    primary_path=record.path,
                    original_winner=previous_annotation.winner,
                    original_reject=previous_annotation.reject,
                    original_photoshop=previous_annotation.photoshop,
                    rating=previous_annotation.rating,
                    tags=previous_annotation.tags,
                    original_review_round=previous_annotation.review_round,
                    folder=self._window._current_folder,
                    source_paths=self._window._record_paths(record),
                    session_id=self._window._session_id,
                    winner_mode=self._window._winner_mode.value,
                )
            )
            self.queue_annotation_persist(record, previous_annotation=previous_annotation, winner_sync=winner_sync)
            self._window._aiculler.sync_annotation_to_global_adapter_label(record, annotation)
            self.capture_annotation_feedback(record, previous_annotation, annotation, source_mode=source_mode)
            self.apply_review_count_delta(previous_annotation, annotation)
            changed_paths.append(record.path)

        if undo_actions:
            self._window._record_ops.push_undo_actions(undo_actions)
        if changed_paths:
            self.apply_annotation_change_effects(changed_paths, current_path=current_path, counts_already_updated=True)
        return len(changed_paths), failures

    def batch_keep_records(self, records: list[ImageRecord]) -> None:
        if not records:
            return
        moved = sum(1 for record in records if self._window._record_ops.keep_record_by_path(record.path))
        self._window.statusBar().showMessage(f"Moved {moved} image(s) to _keep")

    def batch_restore_records(self, records: list[ImageRecord]) -> None:
        if not records:
            return
        restored = sum(1 for record in records if self._window._record_ops.restore_record_by_path(record.path))
        self._window.statusBar().showMessage(f"Restored {restored} image(s)")

    def batch_open_in_photoshop(self, records: list[ImageRecord]) -> None:
        if not self._window._photoshop_executable or not records:
            return
        for record in records:
            open_in_photoshop(record.path)
        self._window.statusBar().showMessage(f"Opened {len(records)} image(s) in Photoshop")

    def refresh_burst_group_view(self, *, request_thumbnails: bool = True) -> None:
        burst_groups: list[tuple[int, ...]] = []
        burst_group_map: dict[str, BurstVisualInfo] = {}
        if (self._window._burst_groups_enabled or self._window._burst_stacks_enabled) and self._window._records:
            if self._window._review_intelligence is not None:
                visible_groups_by_id: dict[str, list[int]] = {}
                visible_label_by_id: dict[str, tuple[str, str]] = {}
                for record_index, record in enumerate(self._window._records):
                    insight = self._window._inspector.review_insight_for_record(record)
                    if insight is None or not insight.has_group:
                        continue
                    visible_groups_by_id.setdefault(insight.group_id, []).append(record_index)
                    visible_label_by_id.setdefault(insight.group_id, (insight.group_label, insight.group_kind))
                burst_groups = [tuple(indexes) for indexes in visible_groups_by_id.values() if len(indexes) >= 2]
                burst_groups.sort(key=lambda members: members[0])
                for group_number, group in enumerate(burst_groups, start=1):
                    insight = self._window._inspector.review_insight_for_record(self._window._records[group[0]])
                    label, kind = visible_label_by_id.get(
                        insight.group_id if insight is not None else "",
                        ("Group", "similar"),
                    )
                    for index_in_group, record_index in enumerate(group, start=1):
                        if not 0 <= record_index < len(self._window._records):
                            continue
                        burst_group_map[self._window._records[record_index].path] = BurstVisualInfo(
                            group_number=group_number,
                            index_in_group=index_in_group,
                            group_size=len(group),
                            label=label,
                            kind=kind,
                        )
            else:
                burst_groups = find_burst_groups(self._window._records, self._window._filter_metadata_by_path)
                for group_number, group in enumerate(burst_groups, start=1):
                    for index_in_group, record_index in enumerate(group, start=1):
                        if not 0 <= record_index < len(self._window._records):
                            continue
                        burst_group_map[self._window._records[record_index].path] = BurstVisualInfo(
                            group_number=group_number,
                            index_in_group=index_in_group,
                            group_size=len(group),
                            label="Burst",
                            kind="burst",
                        )
        self._window._visible_burst_groups = burst_groups
        self._window.grid.set_burst_groups(burst_group_map, burst_groups, request_thumbnails=request_thumbnails)
        self._window.grid.set_burst_stack_mode(self._window._burst_stacks_enabled, request_thumbnails=request_thumbnails)
        self._window._records_view.update_filter_summary()
