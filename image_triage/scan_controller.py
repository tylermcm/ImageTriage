"""Loading a folder and keeping it current: refresh, folder watching, scan results, scope enrichment, review-intelligence analysis, caches and their status indicators. Extracted from MainWindow (docs/mainwindow_decomposition_plan.md, DC-4.4)."""
from __future__ import annotations

import logging
import os
import time

from PySide6.QtCore import QObject, QThreadPool, Qt

from .aiculler_workflow import WINNER_SCORE_FALLBACK_MODEL_VERSION, aiculler_db_path, load_face_records_by_path, load_image_categories_by_path, load_latest_winner_scores
from .catalog import catalog_cache_env_override
from .grid import GridDeltaUpdate
from .models import FilterMode, ImageRecord, SessionAnnotation
from .phash_prefilter import build_phash_prefilter_paths, load_phash_prefilter_decisions
from .records_view_cache import ViewInvalidationReason
from .records_view_controller import _memory_path_key
from .review_intelligence import BuildReviewIntelligenceTask, ReviewIntelligenceBundle
from .review_workflows import RecordWorkflowInsight, TasteProfile, build_record_workflow_insight
from .scanner import FolderModifiedCheckTask, normalized_path_key
from .tasks.ai_tasks import _PrefilterDecisionsTask
from .tasks.annotation_tasks import ScopeEnrichmentTask

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .window import MainWindow

_logger = logging.getLogger(__name__)


class ScanController(QObject):
    """Loading a folder and keeping it current: refresh, folder watching, scan results, scope enrichment, review-intelligence analysis, caches and their status indicators. Extracted from MainWindow (docs/mainwindow_decomposition_plan.md, DC-4.4)."""

    def __init__(self, window: "MainWindow") -> None:
        super().__init__(window)
        self._window = window
        self._prefilter_load_at = 0.0
        self._prefilter_load_folder = ""
        self._prefilter_load_task: _PrefilterDecisionsTask | None = None
        self._prefilter_load_token = 0
        self._watched_folder_path = ""

    def refresh_folder(self) -> None:
        if self._window._current_folder:
            self.load_folder(
                self._window._current_folder,
                force_refresh=True,
                preferred_record_path=self._window._records_view.current_visible_record_path(),
            )

    def rebuild_current_folder_catalog_cache(self) -> None:
        self._window._catalog.rebuild_current_folder_catalog_cache()

    def load_cached_folder_records(self, folder: str) -> tuple[list[ImageRecord] | None, str]:
        return self._window._catalog.load_cached_folder_records(folder)

    def persist_folder_record_cache(self, folder: str, records: list[ImageRecord], *, source: str = "window") -> None:
        self._window._catalog.persist_folder_record_cache(folder, records, source=source)

    def refresh_current_folder_watch(self) -> None:
        target = ""
        if (
            self._window._watch_current_folder_enabled
            and self._window._scope_kind == "folder"
            and self._window._current_folder
            and not self._window._is_slow_source_folder(self._window._current_folder)
        ):
            candidate = self._window._current_folder
            if os.path.isdir(candidate):
                target = candidate

        if self._watched_folder_path == target:
            return

        existing_paths = list(self._window._folder_watcher.directories())
        if existing_paths:
            self._window._folder_watcher.removePaths(existing_paths)
        self._window._folder_watch_refresh_timer.stop()
        self._window._folder_watch_refresh_pending = False
        self._watched_folder_path = ""
        if not target:
            return
        try:
            if self._window._folder_watcher.addPath(target):
                self._watched_folder_path = target
        except RuntimeError:
            self._watched_folder_path = ""

    def queue_watched_folder_refresh(self, delay_ms: int = 900) -> None:
        if not self._window._watch_current_folder_enabled or self._window._scope_kind != "folder" or not self._window._current_folder:
            return
        self._window._folder_watch_refresh_pending = True
        self._window._folder_watch_refresh_timer.start(max(0, delay_ms))

    def handle_watched_folder_changed(self, path: str) -> None:
        if not self._window._watch_current_folder_enabled or self._window._scope_kind != "folder" or not self._window._current_folder:
            return
        if _memory_path_key(path) != _memory_path_key(self._window._current_folder):
            return
        self.queue_watched_folder_refresh()
        if self._window._scan_in_progress:
            self._window.statusBar().showMessage(f"Detected folder changes in {self._window._current_folder}; refresh queued.")
            return
        self._window.statusBar().showMessage(f"Detected folder changes in {self._window._current_folder}; refreshing...")

    def handle_application_state_changed(self, state: Qt.ApplicationState) -> None:
        if state == Qt.ApplicationState.ApplicationActive:
            self.check_folder_changed_on_activation()
            self._window._navigation.sync_drive_roots()  # a card was inserted or a share mapped while away

    def check_folder_changed_on_activation(self) -> None:
        """Network and removable drives are not watched, so look once when the user comes back to
        the app (typically after importing or exporting elsewhere).

        The folder's modified time is read on a worker thread (a stat on an unreachable share can
        block) and compared with the one its on-screen listing was taken at; a difference queues
        the same refresh a local watcher change would."""
        if (
            not self._window._watch_current_folder_enabled
            or self._window._scope_kind != "folder"
            or not self._window._current_folder
            or self._window._scan_in_progress
            or self._window._folder_dir_mtime_ns is None
            or not self._window._is_slow_source_folder(self._window._current_folder)
        ):
            return
        if self._window._folder_check_task is not None and self._window._folder_check_token == self._window._scan_token:
            return
        now = time.monotonic()
        if now - self._window._folder_check_last_started < 5.0:
            return  # focus flaps (dialogs opening and closing) should not hammer the share
        self._window._folder_check_last_started = now
        task = FolderModifiedCheckTask(self._window._current_folder, self._window._scan_token)
        task.signals.checked.connect(self.handle_folder_modified_checked, Qt.ConnectionType.QueuedConnection)
        self._window._folder_check_task = task
        self._window._folder_check_token = self._window._scan_token
        QThreadPool.globalInstance().start(task)

    def handle_folder_modified_checked(self, folder: str, token: int, modified_ns: object) -> None:
        if token == self._window._folder_check_token:
            self._window._folder_check_task = None
        if token != self._window._scan_token or self._window._scan_in_progress or not isinstance(modified_ns, int):
            return
        if _memory_path_key(folder) != _memory_path_key(self._window._current_folder):
            return
        if modified_ns == self._window._folder_dir_mtime_ns:
            return
        self.queue_watched_folder_refresh()
        self._window.statusBar().showMessage(f"Detected folder changes in {self._window._current_folder}; refreshing...")

    def run_watched_folder_refresh(self) -> None:
        if not self._window._folder_watch_refresh_pending:
            return
        if not self._window._watch_current_folder_enabled or self._window._scope_kind != "folder" or not self._window._current_folder:
            self._window._folder_watch_refresh_pending = False
            return
        if self._window._scan_in_progress:
            self._window._folder_watch_refresh_timer.start(450)
            return
        if self._window._dir_confirmed_missing(self._window._current_folder):
            self._window._folder_watch_refresh_pending = False
            self.refresh_current_folder_watch()
            return
        self._window._folder_watch_refresh_pending = False
        self._window.statusBar().showMessage(f"Refreshing changed folder: {self._window._current_folder}")
        self.load_folder(
            self._window._current_folder,
            force_refresh=True,
            preferred_record_path=self._window._records_view.current_visible_record_path(),
        )

    def load_folder(
        self,
        folder: str,
        *,
        force_refresh: bool = False,
        chunked_restore: bool = False,
        bypass_catalog_cache: bool = False,
        preferred_record_path: str | None = None,
    ) -> None:
        self._window._records_view.load_folder(
            folder,
            force_refresh=force_refresh,
            chunked_restore=chunked_restore,
            bypass_catalog_cache=bypass_catalog_cache,
            preferred_record_path=preferred_record_path,
        )

    def run_loaded_records_enrichment(self) -> None:
        self._window._records_view.run_loaded_records_enrichment()

    def cancel_scope_enrichment_task(self) -> None:
        self._window._scope_enrichment_debounce_timer.stop()
        task = self._window._active_scope_enrichment_task
        if task is None:
            return
        task.cancel()
        self._window._active_scope_enrichment_task = None

    def run_scope_enrichment_debounced(self) -> None:
        self.start_scope_enrichment_task()

    def start_scope_enrichment_task(self, records: list[ImageRecord] | None = None) -> None:
        active_records = list(records) if records is not None else list(self._window._all_records)
        if not active_records:
            self.cancel_scope_enrichment_task()
            self._window._correction_events = []
            self._window._taste_profile = TasteProfile()
            self._window._burst_recommendations = {}
            self._window._workflow_insights_by_path = {}
            return
        if self._window._active_ai_task is not None:
            self._window._ai_run.mark_background_review_work_deferred_for_ai(reason="scope_enrichment")
            self._window._review_scoring_cache_source = "deferred"
            self._window._review_scoring_cache_detail = "Workflow scoring is deferred while AI review runs."
            self.refresh_catalog_status_indicator()
            return

        self.cancel_scope_enrichment_task()
        self._window._scope_enrichment_token += 1
        token = self._window._scope_enrichment_token
        scope_key = self._window._projects.current_scope_key()
        task = ScopeEnrichmentTask(
            scope_key=scope_key,
            token=token,
            session_id=self._window._session_id,
            folder_path=self._window._current_folder,
            catalog_db_path=self._window._catalog_repository.db_path,
            include_all_scope_events=(not self._window._current_folder and self._window._scope_kind != "folder"),
            records=tuple(active_records),
            ai_bundle=self._window._ai_bundle,
            review_bundle=self._window._review_intelligence,
        )
        self._window._review_scoring_cache_source = "building"
        self._window._review_scoring_cache_detail = f"Building workflow scoring for {len(active_records)} image bundle(s)..."
        self.refresh_catalog_status_indicator()
        task.signals.cache_status.connect(self.handle_scope_enrichment_cache_status, Qt.ConnectionType.QueuedConnection)
        task.signals.finished.connect(self.handle_scope_enrichment_finished, Qt.ConnectionType.QueuedConnection)
        task.signals.failed.connect(self.handle_scope_enrichment_failed, Qt.ConnectionType.QueuedConnection)
        self._window._active_scope_enrichment_task = task
        self._window._scope_enrichment_pool.start(task)

    def handle_scope_enrichment_cache_status(self, scope_key: str, token: int, payload: object) -> None:
        if token != self._window._scope_enrichment_token or scope_key != self._window._projects.current_scope_key():
            return
        if not isinstance(payload, dict):
            return
        source = str(payload.get("source") or "idle")
        record_count = int(payload.get("record_count") or 0)
        self._window._review_scoring_cache_source = source
        if source == "catalog":
            self._window._review_scoring_cache_detail = "Loaded workflow scoring from the catalog cache."
        elif source == "live":
            self._window._review_scoring_cache_detail = f"Built workflow scoring live for {record_count} image bundle(s)."
        elif source == "failed":
            self._window._review_scoring_cache_detail = "Workflow scoring failed."
        else:
            self._window._review_scoring_cache_detail = "Workflow scoring is idle."
        self.refresh_catalog_status_indicator()

    def handle_scope_enrichment_finished(
        self,
        scope_key: str,
        token: int,
        correction_events: object,
        taste_profile: object,
        recommendations: object,
    ) -> None:
        if token != self._window._scope_enrichment_token or scope_key != self._window._projects.current_scope_key():
            return
        self._window._active_scope_enrichment_task = None
        self._window._correction_events = list(correction_events) if isinstance(correction_events, list) else []
        self._window._taste_profile = taste_profile if isinstance(taste_profile, TasteProfile) else TasteProfile()
        if isinstance(recommendations, dict):
            self._window._burst_recommendations = {str(path): value for path, value in recommendations.items() if isinstance(path, str)}
        else:
            self._window._burst_recommendations = {}
        self.refresh_workflow_insights_cache(force_full=True)
        current_path = self._window._records_view.current_visible_record_path()
        self._window._views.apply_records_view(current_path=current_path)

    def handle_scope_enrichment_failed(self, scope_key: str, token: int, message: str) -> None:
        if token != self._window._scope_enrichment_token or scope_key != self._window._projects.current_scope_key():
            return
        self._window._active_scope_enrichment_task = None
        self._window._review_scoring_cache_source = "failed"
        self._window._review_scoring_cache_detail = message
        self.refresh_catalog_status_indicator()
        self._window.statusBar().showMessage(f"Workflow enrichment fallback active: {message}")

    def start_review_intelligence_analysis(self, *, force: bool = False) -> None:
        if not self._window._all_records:
            self._window._review_intelligence = None
            self._window._review_chunk_flush_timer.stop()
            self._window._review_chunk_dirty_paths.clear()
            self._window._review_grouping_cache_source = "idle"
            self._window._review_grouping_cache_detail = "No records loaded."
            self._window._review_feature_cache_source = "idle"
            self._window._review_feature_cache_detail = "No review features loaded."
            self.refresh_catalog_status_indicator()
            return
        if self._window._active_ai_task is not None:
            self._window._ai_run.mark_background_review_work_deferred_for_ai(reason="review_intelligence")
            self._window._review_grouping_cache_source = "deferred"
            self._window._review_grouping_cache_detail = "Smart groups are deferred while AI review runs."
            self._window._review_feature_cache_source = "deferred"
            self._window._review_feature_cache_detail = "Review feature analysis is deferred while AI review runs."
            self.refresh_catalog_status_indicator()
            return
        if not force and len(self._window._all_records) > self._window.AUTO_REVIEW_INTELLIGENCE_MAX_RECORDS:
            self._window._review_intelligence = None
            self._window._review_chunk_flush_timer.stop()
            self._window._review_chunk_dirty_paths.clear()
            self._window._review_grouping_cache_source = "skipped"
            self._window._review_grouping_cache_detail = "Smart groups are deferred until requested."
            self._window._review_feature_cache_source = "skipped"
            self._window._review_feature_cache_detail = "Review feature analysis is deferred with smart groups."
            self.refresh_catalog_status_indicator()
            self._window.statusBar().showMessage(
                f"Loaded {len(self._window._all_records)} image bundle(s). Smart groups are deferred until requested."
            )
            return
        previous_task = self._window._active_review_intelligence_task
        if previous_task is not None:
            previous_task.cancel()
        self._window._review_chunk_flush_timer.stop()
        self._window._review_chunk_dirty_paths.clear()
        self._window._review_intelligence_token += 1
        token = self._window._review_intelligence_token
        scope_key = self._window._projects.current_scope_key()
        task = BuildReviewIntelligenceTask(
            folder=scope_key,
            token=token,
            records=tuple(self._window._all_records),
            folder_path=self._window._current_folder,
            catalog_db_path=self._window._catalog_repository.db_path,
        )
        self._window._review_grouping_cache_source = "building"
        self._window._review_grouping_cache_detail = f"Building smart groups for {len(self._window._all_records)} image bundle(s)..."
        self._window._review_feature_cache_source = "building"
        self._window._review_feature_cache_detail = "Preparing review feature analysis..."
        self.refresh_catalog_status_indicator()
        task.signals.started.connect(self.handle_review_intelligence_started, Qt.ConnectionType.QueuedConnection)
        task.signals.progress.connect(self.handle_review_intelligence_progress, Qt.ConnectionType.QueuedConnection)
        task.signals.chunk.connect(self.handle_review_intelligence_chunk, Qt.ConnectionType.QueuedConnection)
        task.signals.cache_status.connect(self.handle_review_intelligence_cache_status, Qt.ConnectionType.QueuedConnection)
        task.signals.cancelled.connect(self.handle_review_intelligence_cancelled, Qt.ConnectionType.QueuedConnection)
        task.signals.finished.connect(self.handle_review_intelligence_finished, Qt.ConnectionType.QueuedConnection)
        task.signals.failed.connect(self.handle_review_intelligence_failed, Qt.ConnectionType.QueuedConnection)
        self._window._active_review_intelligence_task = task
        self._window._review_intelligence_pool.start(task)

    def handle_review_intelligence_started(self, folder: str, token: int, total: int) -> None:
        if token != self._window._review_intelligence_token or folder != self._window._projects.current_scope_key():
            return
        if total > 0:
            self._window.statusBar().showMessage(f"Building smart groups for {total} image bundle(s)...")

    def handle_review_intelligence_progress(self, folder: str, token: int, current: int, total: int) -> None:
        if token != self._window._review_intelligence_token or folder != self._window._projects.current_scope_key():
            return
        if total <= 0:
            return
        if current in {0, 1, total} or current % 80 == 0:
            self._window.statusBar().showMessage(f"Building smart groups ({current}/{total})...")

    def handle_review_intelligence_cache_status(self, folder: str, token: int, payload: object) -> None:
        if token != self._window._review_intelligence_token or folder != self._window._projects.current_scope_key():
            return
        if not isinstance(payload, dict):
            return
        grouping_source = str(payload.get("grouping_source") or "idle")
        feature_source = str(payload.get("feature_source") or "idle")
        total_records = int(payload.get("total_records") or 0)
        cached_feature_count = int(payload.get("cached_feature_count") or 0)
        computed_feature_count = int(payload.get("computed_feature_count") or 0)

        self._window._review_grouping_cache_source = grouping_source
        if grouping_source == "catalog":
            self._window._review_grouping_cache_detail = "Loaded smart groups from the catalog cache."
        elif grouping_source == "live":
            self._window._review_grouping_cache_detail = f"Built smart groups live for {total_records} image bundle(s)."
        elif grouping_source == "failed":
            self._window._review_grouping_cache_detail = "Smart grouping failed."
        else:
            self._window._review_grouping_cache_detail = "Smart grouping is idle."

        self._window._review_feature_cache_source = feature_source
        if feature_source == "catalog":
            self._window._review_feature_cache_detail = f"Reused cached review features for all {cached_feature_count} image bundle(s)."
        elif feature_source == "mixed":
            self._window._review_feature_cache_detail = (
                f"Reused cached review features for {cached_feature_count}/{total_records} bundle(s) "
                f"and computed {computed_feature_count} live."
            )
        elif feature_source == "live":
            self._window._review_feature_cache_detail = f"Computed review features live for {computed_feature_count or total_records} image bundle(s)."
        elif feature_source == "skipped":
            self._window._review_feature_cache_detail = "Review feature analysis was skipped because grouped results came from cache."
        elif feature_source == "failed":
            self._window._review_feature_cache_detail = "Review feature analysis failed."
        else:
            self._window._review_feature_cache_detail = "Review feature analysis is idle."
        self.refresh_catalog_status_indicator()

    def handle_review_intelligence_chunk(self, folder: str, token: int, payload: object) -> None:
        if token != self._window._review_intelligence_token or folder != self._window._projects.current_scope_key():
            return
        if not isinstance(payload, dict):
            return
        groups_payload = payload.get("groups")
        insights_payload = payload.get("insights")
        groups = tuple(group for group in groups_payload if hasattr(group, "id")) if isinstance(groups_payload, (list, tuple)) else ()
        if not isinstance(insights_payload, dict):
            return
        if self._window._review_intelligence is None:
            merged_groups: dict[str, object] = {}
            merged_insights: dict[str, object] = {}
        else:
            merged_groups = {group.id: group for group in self._window._review_intelligence.groups}
            merged_insights = dict(self._window._review_intelligence.insights_by_path)
        changed_paths: set[str] = set()
        for group in groups:
            merged_groups[group.id] = group
            changed_paths.update(path for path in getattr(group, "member_paths", ()) if isinstance(path, str) and path)
        for path, insight in insights_payload.items():
            if isinstance(path, str) and path:
                merged_insights[path] = insight
        if not changed_paths:
            changed_paths.update(
                path
                for path in insights_payload
                if isinstance(path, str) and path in self._window._record_index_by_path
            )
        self._window._review_intelligence = ReviewIntelligenceBundle(
            groups=tuple(merged_groups.values()),
            insights_by_path=merged_insights,
        )
        self._window._review_chunk_dirty_paths.update(path for path in changed_paths if path)
        self._window._review_chunk_flush_timer.start()

    def flush_review_chunk_updates(self) -> None:
        if not self._window._review_chunk_dirty_paths:
            return
        changed_paths = sorted(self._window._review_chunk_dirty_paths)
        self._window._review_chunk_dirty_paths.clear()
        current_path = self._window._records_view.current_visible_record_path()
        if self._window._filter_query.quick_filter in {FilterMode.SMART_GROUPS, FilterMode.DUPLICATES}:
            self._window._records_view_cache.mark(ViewInvalidationReason.REVIEW_CHANGED, paths=changed_paths)
            self._window._views.apply_records_view(current_path=current_path)
            return
        changed_visible_paths = tuple(path for path in changed_paths if path in self._window._record_index_by_path)
        self._window.grid.set_review_insights(self._window._review_intelligence.insights_by_path if self._window._review_intelligence is not None else {})
        if changed_visible_paths:
            self._window.grid.update_items(
                GridDeltaUpdate(
                    changed_paths=changed_visible_paths,
                    selection_anchor=self._window.grid.current_index(),
                    preserve_pixmap_cache=True,
                )
            )
        self._window._preview_ctl.rebuild_visible_preview_group_indexes()
        self._window._annotation_ctl.refresh_burst_group_view()
        if current_path:
            index = self._window._record_index_by_path.get(current_path)
            if index is not None:
                if index != self._window.grid.current_index():
                    self._window.grid.set_current_index(index)
        self._window._records_view.update_filter_summary()
        self._window._inspector.update_action_states()
        self._window._inspector.update_status()

    def handle_review_intelligence_cancelled(self, folder: str, token: int) -> None:
        if token != self._window._review_intelligence_token or folder != self._window._projects.current_scope_key():
            return
        self._window._active_review_intelligence_task = None
        self._window._review_chunk_flush_timer.stop()
        self._window._review_chunk_dirty_paths.clear()
        self._window._review_grouping_cache_source = "idle"
        self._window._review_grouping_cache_detail = "Smart grouping cancelled."
        self._window._review_feature_cache_source = "idle"
        self._window._review_feature_cache_detail = "Review feature analysis cancelled."
        self.refresh_catalog_status_indicator()

    def handle_review_intelligence_finished(self, folder: str, token: int, bundle: ReviewIntelligenceBundle) -> None:
        if token != self._window._review_intelligence_token or folder != self._window._projects.current_scope_key():
            return
        self._window._active_review_intelligence_task = None
        self._window._review_chunk_flush_timer.stop()
        self._window._review_chunk_dirty_paths.clear()
        self._window._review_intelligence = bundle
        self._window._ai_run.recompute_ai_demoted_burst_paths()
        current_path = self._window._records_view.current_visible_record_path()
        self._window._records_view_cache.mark(ViewInvalidationReason.REVIEW_CHANGED)
        self._window._views.apply_records_view(current_path=current_path)
        self.start_scope_enrichment_task()
        if self._window._preview_ctl.preview_is_visible():
            index = self._window.grid.current_index()
            if index >= 0:
                self._window._preview_ctl.open_preview(index)

    def handle_review_intelligence_failed(self, folder: str, token: int, message: str) -> None:
        if token != self._window._review_intelligence_token or folder != self._window._projects.current_scope_key():
            return
        self._window._active_review_intelligence_task = None
        self._window._review_chunk_flush_timer.stop()
        self._window._review_chunk_dirty_paths.clear()
        self._window._review_intelligence = None
        self._window._review_grouping_cache_source = "failed"
        self._window._review_grouping_cache_detail = message
        self._window._review_feature_cache_source = "failed"
        self._window._review_feature_cache_detail = message
        self.refresh_catalog_status_indicator()
        self._window.statusBar().showMessage(f"Smart grouping fallback active: {message}")

    def catalog_cache_reads_enabled(self) -> bool:
        override = catalog_cache_env_override()
        return self._window._catalog_cache_enabled if override is None else override

    def catalog_status_badge_text(self) -> str:
        if self._window._catalog_load_source == "live" and self._window._scan_cached_source:
            return f"Load: {self._window._catalog_source_label(self._window._scan_cached_source)} + Live"
        return f"Load: {self._window._catalog_source_label(self._window._catalog_load_source)}"

    def cache_pipeline_badge_text(self) -> str:
        review_label = self._window._cache_source_label(self._window._review_grouping_cache_source)
        scoring_label = self._window._cache_source_label(self._window._review_scoring_cache_source)
        return f"Review: {review_label} | Workflow: {scoring_label}"

    def review_cache_summary_lines(self) -> list[str]:
        lines = [
            f"Review groups: {self._window._cache_source_label(self._window._review_grouping_cache_source)}",
        ]
        if self._window._review_grouping_cache_detail:
            lines.append(self._window._review_grouping_cache_detail)
        lines.append(f"Review features: {self._window._cache_source_label(self._window._review_feature_cache_source)}")
        if self._window._review_feature_cache_detail:
            lines.append(self._window._review_feature_cache_detail)
        lines.append(f"Workflow scoring: {self._window._cache_source_label(self._window._review_scoring_cache_source)}")
        if self._window._review_scoring_cache_detail:
            lines.append(self._window._review_scoring_cache_detail)
        return lines

    def catalog_debug_summary(self, *, include_current: bool = False) -> str:
        stats = self._window._catalog_repository.stats()
        enabled_label = "Enabled" if self.catalog_cache_reads_enabled() else "Disabled"
        override = catalog_cache_env_override()
        lines = [f"Catalog cache reads: {enabled_label}"]
        if override is not None:
            lines[-1] = f"{lines[-1]} (environment override)"
        lines.append(f"Folder watch: {'Enabled' if self._window._watch_current_folder_enabled else 'Disabled'}")
        if include_current:
            lines.append(f"Current load: {self._window._catalog_source_label(self._window._catalog_load_source)}")
            if self._window._catalog_load_detail:
                lines.append(self._window._catalog_load_detail)
            lines.extend(self.review_cache_summary_lines())
        if stats.error_message:
            lines.append(f"Catalog error: {stats.error_message}")
        else:
            lines.append(f"Indexed folders: {stats.folder_count}")
            lines.append(f"Indexed image bundles: {stats.record_count}")
            lines.append(f"Cached review features: {stats.feature_count}")
            lines.append(f"Cached review group results: {stats.grouping_cache_count}")
            lines.append(f"Cached workflow scoring results: {stats.scoring_cache_count}")
            if stats.last_indexed_at:
                lines.append(f"Last indexed: {stats.last_indexed_at}")
        lines.append(f"Database: {stats.db_path}")
        return "\n".join(lines)

    def refresh_catalog_status_indicator(self) -> None:
        if not hasattr(self._window, "catalog_status_label"):
            return
        self._window.catalog_status_label.setText(self.catalog_status_badge_text())
        summary_text = self.catalog_debug_summary(include_current=True)
        self._window.catalog_status_label.setToolTip(summary_text)
        if hasattr(self._window, "cache_pipeline_label"):
            self._window.cache_pipeline_label.setText(self.cache_pipeline_badge_text())
            self._window.cache_pipeline_label.setToolTip(summary_text)

    def refresh_winner_scores_for_current_folder(self) -> bool:
        try:
            paths = self._window._aiculler.aiculler_paths_for_current_folder()
        except Exception:
            _logger.exception("Failed to resolve aiculler paths for winner scores")
            paths = None
        if paths is None:
            self._window._winner_scores_by_path = {}
            return False
        db_path = aiculler_db_path(paths)
        try:
            bundle = load_latest_winner_scores(
                db_path,
                model_version=WINNER_SCORE_FALLBACK_MODEL_VERSION,
            )
        except Exception:
            _logger.exception("Failed to load winner scores from %s", db_path)
            self._window._winner_scores_by_path = {}
            return False
        self._window._winner_scores_by_path = dict(bundle.get("scores_by_path") or {})
        return bool(self._window._winner_scores_by_path)

    def refresh_face_records_for_current_folder(self) -> bool:
        try:
            paths = self._window._aiculler.aiculler_paths_for_current_folder()
        except Exception:
            _logger.exception("Failed to resolve aiculler paths for face records")
            paths = None
        if paths is None:
            self._window._face_records_by_path = {}
            self._window._face_records_db_path = ""
            return False
        db_path = aiculler_db_path(paths)
        try:
            self._window._face_records_by_path = load_face_records_by_path(db_path)
        except Exception:
            _logger.exception("Failed to load face records from %s", db_path)
            self._window._face_records_by_path = {}
        self._window._face_records_db_path = str(db_path)
        return bool(self._window._face_records_by_path)

    def refresh_image_categories_for_current_folder(self) -> bool:
        try:
            paths = self._window._aiculler.aiculler_paths_for_current_folder()
        except Exception:
            _logger.exception("Failed to resolve aiculler paths for image categories")
            paths = None
        if paths is None:
            self._window._image_categories_by_path = {}
            self._window._image_categories_db_path = ""
            return False
        db_path = aiculler_db_path(paths)
        try:
            self._window._image_categories_by_path = load_image_categories_by_path(db_path)
        except Exception:
            _logger.exception("Failed to load image categories from %s", db_path)
            self._window._image_categories_by_path = {}
        self._window._image_categories_db_path = str(db_path)
        return bool(self._window._image_categories_by_path)

    def reset_review_cache_status(self) -> None:
        self._window._review_grouping_cache_source = "idle"
        self._window._review_grouping_cache_detail = "Ready"
        self._window._review_feature_cache_source = "idle"
        self._window._review_feature_cache_detail = "Ready"
        self._window._review_scoring_cache_source = "idle"
        self._window._review_scoring_cache_detail = "Ready"

    def handle_scan_cached(self, folder: str, token: int, records: list[ImageRecord], source: str) -> None:
        self._window._records_view.handle_scan_cached(folder, token, records, source)

    def handle_scan_finished(self, folder: str, token: int, records: list[ImageRecord], source: str) -> None:
        self._window._records_view.handle_scan_finished(folder, token, records, source)

    def handle_scan_children(self, folder: str, token: int, records: object) -> None:
        self._window._records_view.handle_scan_children(folder, token, records)

    def handle_scan_failed(self, folder: str, token: int, message: str) -> None:
        self._window._records_view.handle_scan_failed(folder, token, message)

    def refresh_prefilter_decisions_for_current_folder(self) -> None:
        if not self._window._current_folder:
            self._window._prefilter_decisions_by_path = {}
            return
        if self._window._is_slow_source_folder(self._window._current_folder):
            # Runs every time the records view is finalized, and loading the decisions resolves the hidden
            # folder, stats and reads a file and resolves every decision's path: all over the share. So
            # for a share a worker does it (at most once a minute per folder) and the arriving answer
            # is pushed to the grid.
            self.load_prefilter_decisions_off_thread(self._window._current_folder)
            return
        try:
            decisions = load_phash_prefilter_decisions(build_phash_prefilter_paths(self._window._current_folder))
        except Exception:
            _logger.exception("Failed to load phash prefilter decisions for %s", self._window._current_folder)
            decisions = {}
        self._window._prefilter_decisions_by_path = {
            normalized_path_key(path): decision
            for path, decision in decisions.items()
            if normalized_path_key(path)
        }

    def load_prefilter_decisions_off_thread(self, folder: str) -> None:
        now = time.monotonic()
        if self._prefilter_load_folder == folder and (
            self._prefilter_load_task is not None or now - self._prefilter_load_at < self._window._PREFILTER_LOAD_TTL_S
        ):
            return
        self._prefilter_load_token += 1
        self._prefilter_load_folder = folder
        self._prefilter_load_at = now
        task = _PrefilterDecisionsTask(self._prefilter_load_token, folder)
        task.signals.ready.connect(self.handle_prefilter_decisions_ready, Qt.ConnectionType.QueuedConnection)
        self._prefilter_load_task = task
        QThreadPool.globalInstance().start(task)

    def handle_prefilter_decisions_ready(self, token: int, folder: str, decisions: object) -> None:
        if token == self._prefilter_load_token:
            self._prefilter_load_task = None
        if token != self._prefilter_load_token or folder != self._window._current_folder or not isinstance(decisions, dict):
            return
        self._window._prefilter_decisions_by_path = decisions
        self._window.grid.set_prefilter_decisions(decisions)

    def refresh_workflow_insights_cache(
        self,
        *,
        changed_paths: set[str] | None = None,
        force_full: bool = False,
    ) -> None:
        if force_full:
            insights: dict[str, RecordWorkflowInsight] = {}
            for record in self._window._all_records:
                annotation = self._window._annotations.get(record.path, SessionAnnotation())
                ai_result = self._window._ai_run.ai_result_for_record(record)
                burst_recommendation = self._window._inspector.burst_recommendation_for_record(record)
                workflow = build_record_workflow_insight(
                    annotation,
                    ai_result,
                    burst_recommendation,
                    self._window._taste_profile,
                )
                insights[record.path] = workflow
                lookup_key = _memory_path_key(record.path)
                if lookup_key != record.path:
                    insights[lookup_key] = workflow
            self._window._workflow_insights_by_path = insights
            return

        if not changed_paths:
            return

        if not self._window._workflow_insights_by_path:
            self._window._workflow_insights_by_path = {}

        for path in changed_paths:
            record = self._window._record_for_path(path)
            if record is None:
                self._window._workflow_insights_by_path.pop(path, None)
                self._window._workflow_insights_by_path.pop(_memory_path_key(path), None)
                continue
            annotation = self._window._annotations.get(record.path, SessionAnnotation())
            ai_result = self._window._ai_run.ai_result_for_record(record)
            burst_recommendation = self._window._inspector.burst_recommendation_for_record(record)
            workflow = build_record_workflow_insight(
                annotation,
                ai_result,
                burst_recommendation,
                self._window._taste_profile,
            )
            self._window._workflow_insights_by_path[record.path] = workflow
            lookup_key = _memory_path_key(record.path)
            if lookup_key != record.path:
                self._window._workflow_insights_by_path[lookup_key] = workflow
