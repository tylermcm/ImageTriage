from __future__ import annotations

import os
import threading
import time
from collections import deque
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, QRunnable, QSignalBlocker, QTimer, Qt, Signal
from PySide6.QtGui import QAction, QActionGroup
from PySide6.QtWidgets import QInputDialog, QMenu, QMessageBox, QToolButton

from .ai_results import find_ai_result_for_record
from .aiculler_workflow import aiculler_db_path
from .face_index import FaceFolderIndexTask
from .filtering import (
    AIStateFilter,
    FileTypeFilter,
    OrientationFilter,
    RecordFilterQuery,
    ReviewStateFilter,
    SavedFilterPreset,
    active_filter_labels,
    builtin_filter_presets,
    matches_record_query,
)
from .grid import GridDeltaUpdate
from .metadata import EMPTY_METADATA, CaptureMetadata
from .models import FilterMode, ImageRecord, SessionAnnotation, SortMode, sort_records
from .perf import perf_logger, write_execution_log
from .records_view_cache import ViewInvalidationReason
from .review_workflows import TasteProfile
from .scanner import FolderScanTask, normalize_filesystem_path, normalized_path_key
from .semantic_index import (
    SEMANTIC_EMBEDDING_DIM,
    SemanticFolderIndexTask,
    compute_semantic_model_identity,
    ensure_semantic_onnx_runtime,
)
from .semantic_search import SearchFilters
from .ui import AdvancedFilterDialog, PeopleSearchDialog
from .folder_session import FolderSession

if TYPE_CHECKING:
    from .window import MainWindow


def _memory_path_key(path: str) -> str:
    """Create a cheap case-insensitive in-memory lookup key for loaded paths."""
    return os.path.normpath(path).casefold()


def _search_match_path_key(path: str | Path) -> str:
    text = str(path or "").strip()
    if not text:
        return ""
    return os.path.normpath(os.path.abspath(text)).casefold()


class UnifiedSearchSignals(QObject):
    finished = Signal(str, int, object)
    failed = Signal(str, int, str)


class UnifiedSearchTask(QRunnable):
    """Runs CLIP/person search off the UI thread for the current search box."""

    _text_encoder_cache: dict[tuple[str, str, str], object] = {}
    _text_encoder_cache_lock = threading.Lock()

    def __init__(
        self,
        *,
        folder: str,
        token: int,
        db_path: Path,
        runtime: object,
        query_text: str,
        min_confidence: float,
        limit: int = 500,
    ) -> None:
        super().__init__()
        self.folder = folder
        self.token = token
        self.db_path = Path(db_path)
        self.runtime = runtime
        self.query_text = query_text
        self.min_confidence = float(min_confidence)
        self.limit = int(limit)
        self.signals = UnifiedSearchSignals()
        self._cancelled = False
        self.setAutoDelete(True)

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        logger = perf_logger()
        start = time.perf_counter() if logger.enabled else 0.0
        if self._cancelled:
            return
        try:
            query_text = self.query_text.strip()
            if not query_text or not self.db_path.exists():
                self.signals.finished.emit(self.folder, self.token, self._empty_result(query_text))
                return

            from aiculler.storage import SQLiteFeatureStore
            from .people_search import list_person_clusters
            from .semantic_search import (
                SEARCH_QUERY_TEMPLATES,
                FeatureStoreSemanticSearch,
                parse_search_query,
            )

            store = SQLiteFeatureStore(self.db_path)
            try:
                known_people = tuple(
                    cluster.name
                    for cluster in list_person_clusters(store.connection)
                    if cluster.name.strip()
                )
                if self._cancelled:
                    return
                parsed = parse_search_query(query_text, known_people=known_people)
                text_encoder = None
                if parsed.semantic_text:
                    text_model = Path(getattr(self.runtime, "clip_text_model", ""))
                    tokenizer = Path(getattr(self.runtime, "tokenizer", ""))
                    fallback_text_model = getattr(self.runtime, "clip_fallback_text_model", None)
                    for label, path in (("CLIP text model", text_model), ("CLIP tokenizer", tokenizer)):
                        if not path.exists():
                            raise FileNotFoundError(f"{label} is missing: {path}")
                    text_encoder = self._cached_text_encoder(
                        text_model,
                        tokenizer,
                        fallback_text_model,
                        device=str(getattr(self.runtime, "device", "auto")),
                    )
                service = FeatureStoreSemanticSearch(
                    store,
                    text_encoder,
                    query_templates=SEARCH_QUERY_TEMPLATES,
                )
                hits = service.search(
                    query_text,
                    known_people=known_people,
                    filters=SearchFilters(min_confidence=self.min_confidence),
                    limit=self.limit,
                )
            finally:
                store.close()

            if self._cancelled:
                return
            path_keys: list[str] = []
            rank_by_path: dict[str, float] = {}
            for hit in hits:
                key = _search_match_path_key(hit.source_path)
                if not key:
                    continue
                path_keys.append(key)
                rank_by_path[key] = max(rank_by_path.get(key, 0.0), float(hit.confidence))
            result = {
                "query": query_text,
                "path_keys": tuple(path_keys),
                "rank_by_path": rank_by_path,
                "hit_count": len(path_keys),
                "known_people": known_people,
            }
            if logger.enabled:
                logger.duration(
                    "unified_search",
                    (time.perf_counter() - start) * 1000.0,
                    folder=self.folder,
                    hits=len(path_keys),
                    people=len(known_people),
                )
            self.signals.finished.emit(self.folder, self.token, result)
        except Exception as exc:
            if logger.enabled:
                logger.duration(
                    "unified_search.failed",
                    (time.perf_counter() - start) * 1000.0,
                    folder=self.folder,
                    error=str(exc),
                )
            self.signals.failed.emit(self.folder, self.token, str(exc))

    @classmethod
    def _cached_text_encoder(
        cls,
        text_model: Path,
        tokenizer: Path,
        fallback_text_model: object,
        *,
        device: str = "auto",
    ):
        cls._ensure_text_search_runtime(device=device)
        from aiculler.text_scoring import CLIPTextEncoder

        fallback_path = Path(fallback_text_model) if fallback_text_model else None
        key = (
            str(text_model.resolve()),
            str(tokenizer.resolve()),
            str(fallback_path.resolve()) if fallback_path is not None else "",
        )
        with cls._text_encoder_cache_lock:
            cached = cls._text_encoder_cache.get(key)
            if cached is not None:
                return cached
            encoder = CLIPTextEncoder(
                text_model,
                tokenizer,
                fallback_text_onnx_path=fallback_path,
            )
            cls._text_encoder_cache.clear()
            cls._text_encoder_cache[key] = encoder
            return encoder

    @staticmethod
    def _ensure_text_search_runtime(*, device: str) -> None:
        ensure_semantic_onnx_runtime(device=device)

    @staticmethod
    def _empty_result(query_text: str) -> dict[str, object]:
        return {
            "query": query_text,
            "path_keys": (),
            "rank_by_path": {},
            "hit_count": 0,
            "known_people": (),
        }


class RecordsViewController:
    """Folder scanning, the records-view rebuild pipeline, filter/search state,
    saved filter presets, and unified/semantic/face search orchestration.

    State that only this controller uses (the scan, search and index tokens, the filter-metadata prefetch queue,
    the chunked-render and deferred-enrichment bookkeeping) lives here, set in ``__init__`` (DC-3.5). State shared
    with the rest of the window (`_filter_query`, `_records`, `_folder_records`, `_records_view_cache`,
    `_saved_filter_presets`, ...) still lives on ``MainWindow`` and is reached through the back-reference, as with
    every other WI-4.4 controller. This is the largest and most interconnected slice of the decomposition, so
    internal calls between these methods still route back through the window's own delegate methods rather than
    calling each other directly on this class; that costs one extra hop per call and keeps every reference exactly
    as the original code meant it.
    """

    @property
    def _session(self) -> FolderSession:
        return self._window._folder_session

    def __init__(self, window: "MainWindow") -> None:
        self._window = window
        # State that only this controller uses. It lived on MainWindow until DC-3.5 (docs/mainwindow_decomposition_plan.md).
        self._semantic_index_token = 0
        self._semantic_index_scope_key = ""
        self._semantic_index_signature: tuple[object, ...] = ()
        self._semantic_index_completed = 0
        self._semantic_index_total = 0
        self._face_index_token = 0
        self._face_index_scope_key = ""
        self._face_index_signature: tuple[object, ...] = ()
        self._face_index_people_count = 0
        self._face_index_completed = 0
        self._face_index_total = 0
        self._scan_showed_cached = False
        self._active_scan_tasks: dict[int, FolderScanTask] = {}
        self._active_unified_search_task: UnifiedSearchTask | None = None
        self._unified_search_token = 0
        self._unified_search_signature: tuple[object, ...] = ()
        self._unified_search_completed_signature: tuple[object, ...] = ()
        self._unified_search_path_keys: frozenset[str] = frozenset()
        self._person_filter_paths: frozenset[str] = frozenset()
        self._unified_search_rank_by_path: dict[str, float] = {}
        self._deferred_enrichment_pending = False
        self._deferred_enrichment_scheduled = False
        self._deferred_enrichment_scope_key = ""
        self._deferred_enrichment_token = 0
        self._last_view_record_paths: tuple[str, ...] = ()
        self._chunked_load_scan_tokens: set[int] = set()
        self._records_view_chunk_next_index = 0
        self._records_view_chunk_current_path: str | None = None
        self._file_type_actions: dict[FileTypeFilter, QAction] = {}
        self._review_state_actions: dict[ReviewStateFilter, QAction] = {}
        self._filter_metadata_record_paths: set[str] = set()
        self._filter_metadata_loaded_paths: set[str] = set()
        self._filter_metadata_requested_paths: set[str] = set()
        self._filter_metadata_queue: deque[str] = deque()
        self._filter_metadata_queue_keys: set[str] = set()
        self._filter_metadata_queue_limit = 720
        self._metadata_membership_dirty_paths: set[str] = set()
        self._metadata_scroll_last_value = 0
        self._metadata_scroll_direction = 1

    # -- Folder loading / scanning -----------------------------------------

    def load_folder(
        self,
        folder: str,
        *,
        force_refresh: bool = False,
        chunked_restore: bool = False,
        bypass_catalog_cache: bool = False,
        preferred_record_path: str | None = None,
    ) -> None:
        window = self._window
        if not folder:
            return
        logger = perf_logger()
        pre_scan_start = time.perf_counter() if logger.enabled else 0.0
        slow_source = window._is_slow_source_folder(folder)
        if window._active_tool_mode or window.grid.tool_checkbox_mode():
            window._tool_mode.cancel_tool_mode(show_message=False)
        window._records_view.cancel_records_view_chunk()
        folder_changed = _memory_path_key(folder) != _memory_path_key(self._session.folder)
        if folder_changed:
            if not getattr(window._toolbar, "_nav_suppress_history", False) and self._session.folder:
                nav_back = getattr(window._toolbar, "_nav_back", None)
                if nav_back is not None:
                    nav_back.append(self._session.folder)
                    if len(nav_back) > 100:
                        del nav_back[0]
                    if getattr(window._toolbar, "_nav_forward", None) is not None:
                        window._toolbar._nav_forward.clear()
            window._settings_ctl.remember_current_folder_view_state()
            window._ai_run.cancel_hidden_ai_results_load()
            window._hidden_ai_results_checked_scope_key = ""
        normalized_focus_path = normalize_filesystem_path(preferred_record_path) if preferred_record_path else ""
        if normalized_focus_path and _memory_path_key(str(Path(normalized_focus_path).parent)) == _memory_path_key(folder):
            window._pending_folder_focus_path = normalized_focus_path
        elif folder_changed:
            window._pending_folder_focus_path = ""
        self._session.folder = folder
        window._navigation.update_nav_history_buttons()
        window._folder_records = []
        window._projects.set_scope_state(kind="folder", scope_id=_memory_path_key(folder), label=folder)
        window._scan.refresh_current_folder_watch()
        window._settings.setValue(window.LAST_FOLDER_KEY, folder)
        window._navigation.remember_recent_folder(folder)
        window._settings_ctl.apply_folder_view_state(folder)
        window._scan_token += 1
        token = window._scan_token
        if chunked_restore:
            self._chunked_load_scan_tokens.add(token)
        self._chunked_load_scan_tokens = {existing for existing in self._chunked_load_scan_tokens if existing >= token}
        self._scan_showed_cached = False
        window._scan_cached_source = ""
        window._folder_dir_mtime_ns = None
        window._scan_in_progress = True
        window._ai_deferred_background_work = False
        window._ai_deferred_background_scope_key = ""
        window._catalog_load_source = "scanning"
        window._catalog_load_detail = f"Scanning {folder}..."
        window._scan.reset_review_cache_status()
        window._scan.refresh_catalog_status_indicator()
        window._scan.cancel_scope_enrichment_task()
        window._annotation_hydration_token += 1
        if window._active_annotation_hydration_task is not None:
            window._active_annotation_hydration_task.cancel()
        window._active_annotation_hydration_task = None
        window._annotation_hydration_dirty_paths.clear()
        window._annotation_hydration_pending_clear_paths.clear()
        window._annotation_reapply_timer.stop()
        window._review_intelligence_token += 1
        window._review_chunk_flush_timer.stop()
        window._review_chunk_dirty_paths.clear()
        self.reset_unified_search_state()
        self.reset_semantic_index_state()
        if window._active_review_intelligence_task is not None:
            window._active_review_intelligence_task.cancel()
            window._active_review_intelligence_task = None
        window._recycle_bin.refresh_recycle_button()
        if folder_changed:
            window._ai_run.clear_ai_results_state(preserve_setting=True, refresh=False)
        if logger.enabled:
            logger.duration(
                "folder.load.pre_scan_setup",
                (time.perf_counter() - pre_scan_start) * 1000.0,
                folder=folder,
                folder_changed=folder_changed,
                slow_source=slow_source,
            )
        # Cache reads can be large enough to make Windows mark startup as hung. Let the
        # scanner worker emit cached records instead of loading the cache on the UI thread.
        window.statusBar().showMessage(f"Scanning {folder}...")
        window._records_repo.clear()
        window._folder_records = []
        window._navigation.refresh_directory_navigation_buttons()
        window._records = []
        self._last_view_record_paths = ()
        window._record_index_by_path = {}
        window._edited_candidates_cache = {}
        window._visible_review_group_rows_by_id = {}
        window._visible_ai_group_rows_by_id = {}
        window._accepted_count = 0
        window._rejected_count = 0
        window._unreviewed_count = 0
        window._records_have_resizable = False
        window._records_have_convertible = False
        window._summary_ai_text = "AI: Off" if window._ai_bundle is None else window._summary_ai_text
        window._summary_ai_tooltip = "No AI export is currently loaded." if window._ai_bundle is None else window._summary_ai_tooltip
        window._filter_metadata_by_path = {}
        self._filter_metadata_record_paths = set()
        self._filter_metadata_loaded_paths = set()
        self._filter_metadata_requested_paths = set()
        self._filter_metadata_queue = deque()
        self._filter_metadata_queue_keys = set()
        self._metadata_membership_dirty_paths = set()
        window._metadata_scroll_prefetch_timer.stop()
        window._metadata_request_timer.stop()
        window.grid.set_empty_message(f"Scanning {Path(folder).name}...")
        window.grid.set_items([], emit_state_signals=False, request_thumbnails=False)
        window.details_view.set_records([])
        window._views.set_annotation_views()
        window._views.refresh_viewport_mode()
        window._ai_run.update_ai_toolbar_state()

        task = FolderScanTask(
            folder,
            token,
            window._sort_mode,
            prefer_cached_only=(not force_refresh and window._is_slow_source_folder(folder)),
            use_catalog_cache=window._scan.catalog_cache_reads_enabled(),
            read_cached_records=not bypass_catalog_cache,
            include_hidden_folders=window._show_hidden_folders,
        )
        self._active_scan_tasks[token] = task
        task.signals.children.connect(window._scan.handle_scan_children, Qt.ConnectionType.QueuedConnection)
        task.signals.cached.connect(window._scan.handle_scan_cached, Qt.ConnectionType.QueuedConnection)
        task.signals.finished.connect(window._scan.handle_scan_finished, Qt.ConnectionType.QueuedConnection)
        task.signals.failed.connect(window._scan.handle_scan_failed, Qt.ConnectionType.QueuedConnection)
        window._scan_pool.start(task)

    def load_virtual_scope_records(
        self,
        records: list[ImageRecord],
        *,
        scope_kind: str,
        scope_id: str,
        scope_label: str,
    ) -> None:
        window = self._window
        if window._active_tool_mode or window.grid.tool_checkbox_mode():
            window._tool_mode.cancel_tool_mode(show_message=False)
        window._settings_ctl.remember_current_folder_view_state()
        window._pending_folder_scroll_value = None
        window._scan_in_progress = False
        window._scan_token += 1
        window._ai_deferred_background_work = False
        window._ai_deferred_background_scope_key = ""
        window._scan.cancel_scope_enrichment_task()
        window._annotation_hydration_token += 1
        if window._active_annotation_hydration_task is not None:
            window._active_annotation_hydration_task.cancel()
        window._active_annotation_hydration_task = None
        window._annotation_hydration_dirty_paths.clear()
        window._annotation_hydration_pending_clear_paths.clear()
        window._annotation_reapply_timer.stop()
        window._review_intelligence_token += 1
        window._review_chunk_flush_timer.stop()
        window._review_chunk_dirty_paths.clear()
        self.reset_unified_search_state()
        self.reset_semantic_index_state()
        if window._active_review_intelligence_task is not None:
            window._active_review_intelligence_task.cancel()
            window._active_review_intelligence_task = None
        window._pending_folder_focus_path = ""
        self._session.folder = ""
        window._folder_records = []
        window._projects.set_scope_state(kind=scope_kind, scope_id=scope_id, label=scope_label)
        window._navigation.refresh_directory_navigation_buttons()
        window._scan.refresh_current_folder_watch()
        self._scan_showed_cached = False
        window._scan_cached_source = ""
        window._catalog_load_source = "idle"
        window._catalog_load_detail = "Virtual scopes are loaded from app state, not folder cache."
        window._scan.reset_review_cache_status()
        window._scan.refresh_catalog_status_indicator()
        window._ai_run.clear_ai_results_state(preserve_setting=True)
        window._recycle_bin.refresh_recycle_button()
        window.grid.set_empty_message("Choose a folder to start triaging images.")
        self.apply_loaded_records(
            records,
            chunked_view=self.should_chunk_loaded_records(records),
        )
        window.statusBar().showMessage(f"Loaded {scope_label} ({len(records)} image bundle(s))")

    def cancel_records_view_chunk(self) -> None:
        window = self._window
        window._records_view_chunk_timer.stop()
        window._records_view_chunk_records = []
        self._records_view_chunk_next_index = 0
        self._records_view_chunk_current_path = None
        window._records_view_chunk_post_load_enrichment = ""

    def records_view_chunk_active(self) -> bool:
        return bool(self._window._records_view_chunk_records)

    def should_chunk_loaded_records(
        self,
        records: list[ImageRecord] | tuple[ImageRecord, ...] | None,
        *,
        token: int | None = None,
        requested: bool = False,
    ) -> bool:
        window = self._window
        if requested:
            return True
        if token is not None and token in self._chunked_load_scan_tokens:
            return True
        return bool(records) and len(records) >= window.CHUNKED_RESTORE_LOAD_MIN_RECORDS

    def finish_loaded_records_enrichment(self, records: list[ImageRecord], *, defer_enrichment: bool) -> None:
        window = self._window
        if defer_enrichment:
            self._deferred_enrichment_pending = True
            self._deferred_enrichment_scope_key = window._projects.current_scope_key()
            self._deferred_enrichment_token = window._scan_token
            if not window._scan_in_progress and not self.records_view_chunk_active():
                self.schedule_loaded_records_enrichment()
            return
        window._scan.start_scope_enrichment_task(records)
        window._annotation_ctl.start_annotation_hydration(records)
        window._scan.start_review_intelligence_analysis()

    def apply_loaded_records(
        self,
        records: list[ImageRecord],
        *,
        defer_enrichment: bool = False,
        chunked_view: bool = False,
        current_path: str | None = None,
    ) -> None:
        window = self._window
        window._records_repo.reload(records)
        window._export_jobs.refresh_record_capability_cache(records)
        window._edited_candidates_cache = {}
        window._review_intelligence = None
        self._deferred_enrichment_pending = False
        self._deferred_enrichment_scheduled = False
        self._deferred_enrichment_scope_key = ""
        self._deferred_enrichment_token = 0
        window._records_view_cache.mark(ViewInvalidationReason.LOAD_CHANGED)
        self.reset_filter_metadata_index(records)
        current_paths = {record.path for record in records}
        window._annotations = {
            path: annotation
            for path, annotation in window._annotations.items()
            if path in current_paths
        }
        window._correction_events = []
        window._taste_profile = TasteProfile()
        window._burst_recommendations = {}
        window._workflow_insights_by_path = {}
        view_complete = self.apply_records_view(
            current_path=current_path,
            chunked=chunked_view,
            post_load_enrichment="defer" if defer_enrichment else "start",
        )
        if view_complete:
            self.finish_loaded_records_enrichment(records, defer_enrichment=defer_enrichment)

    def schedule_loaded_records_enrichment(self) -> None:
        window = self._window
        if not self._deferred_enrichment_pending or self._deferred_enrichment_scheduled:
            return
        if self.records_view_chunk_active():
            return
        self._deferred_enrichment_scheduled = True
        QTimer.singleShot(0, window._scan.run_loaded_records_enrichment)

    def run_loaded_records_enrichment(self) -> None:
        window = self._window
        self._deferred_enrichment_scheduled = False
        if not self._deferred_enrichment_pending:
            return
        if (
            self._deferred_enrichment_token != window._scan_token
            or self._deferred_enrichment_scope_key != window._projects.current_scope_key()
        ):
            self._deferred_enrichment_pending = False
            self._deferred_enrichment_scope_key = ""
            self._deferred_enrichment_token = 0
            return
        self._deferred_enrichment_pending = False
        self._deferred_enrichment_scope_key = ""
        self._deferred_enrichment_token = 0
        records = list(window._all_records)
        window._scan.start_scope_enrichment_task(records)
        window._annotation_ctl.start_annotation_hydration(records)
        window._scan.start_review_intelligence_analysis()

    @staticmethod
    def records_match_for_refresh(existing: list[ImageRecord], incoming: list[ImageRecord]) -> bool:
        if len(existing) != len(incoming):
            return False
        for left_record, right_record in zip(existing, incoming):
            if (
                left_record.path != right_record.path
                or left_record.size != right_record.size
                or left_record.modified_ns != right_record.modified_ns
                or left_record.companion_paths != right_record.companion_paths
                or left_record.edited_paths != right_record.edited_paths
                or len(left_record.variants) != len(right_record.variants)
            ):
                return False
            for left_variant, right_variant in zip(left_record.variants, right_record.variants):
                if (
                    left_variant.path != right_variant.path
                    or left_variant.size != right_variant.size
                    or left_variant.modified_ns != right_variant.modified_ns
                ):
                    return False
        return True

    def handle_scan_cached(self, folder: str, token: int, records: list[ImageRecord], source: str) -> None:
        window = self._window
        logger = perf_logger()
        start = time.perf_counter() if logger.enabled else 0.0
        if token != window._scan_token or not records:
            return
        self._scan_showed_cached = True
        window._scan_cached_source = source
        window._catalog_load_source = source or "idle"
        window._catalog_load_detail = f"Loaded from {window._catalog_source_label(source)}; live refresh still running."
        window._scan.refresh_catalog_status_indicator()
        window.grid.set_empty_message("Choose a folder to start triaging images.")
        chunked_view = self.should_chunk_loaded_records(records, token=token)
        self.apply_loaded_records(
            records,
            defer_enrichment=True,
            chunked_view=chunked_view,
            current_path=window._pending_folder_focus_path or None,
        )
        window._ai_run.schedule_hidden_ai_results_load()
        cache_label = window._catalog_source_label(source)
        window.statusBar().showMessage(f"Loaded {cache_label.lower()} for {self._session.folder}, refreshing from disk...")
        if logger.enabled:
            logger.duration("scan.cached_applied", (time.perf_counter() - start) * 1000.0, folder=folder, source=source, records=len(records), chunked=chunked_view)

    def handle_scan_finished(self, folder: str, token: int, records: list[ImageRecord], source: str) -> None:
        window = self._window
        logger = perf_logger()
        start = time.perf_counter() if logger.enabled else 0.0
        finished_task = self._active_scan_tasks.pop(token, None)
        if token != window._scan_token:
            self._chunked_load_scan_tokens.discard(token)
            return

        window._scan_in_progress = False
        # What is now on screen was taken (or vouched for) at this folder modified time; the
        # return-to-app check on network drives compares the folder against it.
        window._folder_dir_mtime_ns = getattr(finished_task, "dir_mtime_ns", None)
        window.grid.set_empty_message("Choose a folder to start triaging images.")
        chunked_view = self.should_chunk_loaded_records(records, token=token)
        self._chunked_load_scan_tokens.discard(token)
        if self._scan_showed_cached and self.records_match_for_refresh(window._all_records, records):
            window._catalog_load_source = source or "live"
            if window._scan_cached_source:
                window._catalog_load_detail = f"Opened from {window._catalog_source_label(window._scan_cached_source)} and confirmed by live scan."
            else:
                window._catalog_load_detail = "Live scan confirmed the current folder contents."
            window._scan.refresh_catalog_status_indicator()
            self.schedule_loaded_records_enrichment()
            window._ai_run.schedule_hidden_ai_results_load()
            window._recycle_bin.refresh_recycle_button()
            window.statusBar().showMessage(f"Refreshed {self._session.folder}")
            if window._folder_watch_refresh_pending:
                window._folder_watch_refresh_timer.start(250)
            window._startup.maybe_open_startup_quick_view()
            window._startup.finish_quick_view_attempt_if_ready()
            window._pending_folder_focus_path = ""
            self.maybe_start_semantic_index(records)
            if logger.enabled:
                logger.duration("scan.finished_confirmed_cache", (time.perf_counter() - start) * 1000.0, folder=folder, source=source, records=len(records))
            return
        self.apply_loaded_records(
            records,
            chunked_view=chunked_view,
            current_path=window._pending_folder_focus_path or None,
        )
        window._catalog_load_source = source or "live"
        if self._scan_showed_cached and window._scan_cached_source:
            window._catalog_load_detail = f"Opened from {window._catalog_source_label(window._scan_cached_source)} and refreshed from disk."
        else:
            window._catalog_load_detail = "Loaded directly from a live folder scan."
        window._scan.refresh_catalog_status_indicator()
        window._ai_run.schedule_hidden_ai_results_load()
        window._recycle_bin.refresh_recycle_button()
        if self._scan_showed_cached:
            window.statusBar().showMessage(f"Refreshed {self._session.folder}")
        if window._folder_watch_refresh_pending:
            window._folder_watch_refresh_timer.start(250)
        window._startup.finish_quick_view_attempt_if_ready()
        window._pending_folder_focus_path = ""
        self.maybe_start_semantic_index(records)
        if logger.enabled:
            logger.duration("scan.finished_applied", (time.perf_counter() - start) * 1000.0, folder=folder, source=source, records=len(records), chunked=chunked_view)

    def handle_scan_children(self, folder: str, token: int, records: object) -> None:
        window = self._window
        if token != window._scan_token or normalized_path_key(folder) != normalized_path_key(self._session.folder):
            return
        if not isinstance(records, list):
            return
        window._folder_records = [record for record in records if isinstance(record, ImageRecord)]
        window._navigation.refresh_directory_navigation_buttons()
        window._records_view_cache.mark(ViewInvalidationReason.LOAD_CHANGED)
        self.apply_records_view(current_path=window._pending_folder_focus_path or None)

    def handle_scan_failed(self, folder: str, token: int, message: str) -> None:
        window = self._window
        perf_logger().log("scan.failed", folder=folder, token=token, message=message)
        self._active_scan_tasks.pop(token, None)
        if token != window._scan_token:
            self._chunked_load_scan_tokens.discard(token)
            return
        self._chunked_load_scan_tokens.discard(token)
        self.cancel_records_view_chunk()
        window._pending_folder_scroll_value = None
        window._scan_in_progress = False
        window._scan.cancel_scope_enrichment_task()
        window._annotation_hydration_token += 1
        window._active_annotation_hydration_task = None
        window._annotation_hydration_dirty_paths.clear()
        window._annotation_hydration_pending_clear_paths.clear()
        window._annotation_reapply_timer.stop()
        self._deferred_enrichment_pending = False
        self._deferred_enrichment_scheduled = False
        self._deferred_enrichment_scope_key = ""
        self._deferred_enrichment_token = 0
        window._pending_folder_focus_path = ""
        window._review_chunk_flush_timer.stop()
        window._review_chunk_dirty_paths.clear()
        self.reset_unified_search_state()
        self.reset_semantic_index_state()
        window._records_repo.clear()
        window._folder_records = []
        window._navigation.refresh_directory_navigation_buttons()
        window._records = []
        self._last_view_record_paths = ()
        window._record_index_by_path = {}
        window._edited_candidates_cache = {}
        window._visible_review_group_rows_by_id = {}
        window._visible_ai_group_rows_by_id = {}
        window._accepted_count = 0
        window._rejected_count = 0
        window._unreviewed_count = 0
        window._records_have_resizable = False
        window._records_have_convertible = False
        window._correction_events = []
        window._taste_profile = TasteProfile()
        window._burst_recommendations = {}
        window._workflow_insights_by_path = {}
        window._summary_ai_text = "AI: Off" if window._ai_bundle is None else window._summary_ai_text
        window._summary_ai_tooltip = "No AI export is currently loaded." if window._ai_bundle is None else window._summary_ai_tooltip
        window._filter_metadata_by_path = {}
        self._filter_metadata_record_paths = set()
        self._filter_metadata_loaded_paths = set()
        self._filter_metadata_requested_paths = set()
        self._filter_metadata_queue = deque()
        self._filter_metadata_queue_keys = set()
        self._metadata_membership_dirty_paths = set()
        window._metadata_scroll_prefetch_timer.stop()
        window.grid.set_empty_message(f"Could not scan this folder.\n\n{message}")
        window.grid.set_items([], emit_state_signals=False, request_thumbnails=False)
        window.details_view.set_records([])
        window.actions.empty_recycle_bin.setEnabled(False)
        window.actions.empty_recycle_bin.setToolTip("Unavailable because this folder could not be scanned.")
        window._inspector.update_action_states(probe_folder_ai=False)
        window._catalog_load_source = "failed"
        window._catalog_load_detail = message
        window._scan.refresh_catalog_status_indicator()
        window.statusBar().showMessage(f"Could not scan {self._session.folder}: {message}")
        window._startup.show_main_window_after_quick_view_failure()
        if window._folder_watch_refresh_pending:
            window._folder_watch_refresh_timer.start(450)

    # -- Records view / filtering pipeline ----------------------------------

    def apply_records_view_action_mode(self) -> None:
        window = self._window
        if window._is_recycle_folder():
            window.grid.set_action_mode("recycle_only")
        elif window._is_winners_folder():
            window.grid.set_action_mode("accepted_only")
        elif window._filter_query.quick_filter == FilterMode.WINNERS:
            window.grid.set_action_mode("accepted_only")
        elif window._filter_query.quick_filter == FilterMode.REJECTS:
            window.grid.set_action_mode("rejected_only")
        else:
            window.grid.set_action_mode("normal")

    def finalize_records_view_display(
        self,
        *,
        records: list[ImageRecord],
        next_record_paths: tuple[str, ...],
        structural_changed: bool,
        current_path: str | None,
    ) -> None:
        window = self._window
        logger = perf_logger()
        start = time.perf_counter() if logger.enabled else 0.0
        step_start = start

        def log_step(event: str, previous: float, **fields) -> float:
            if not logger.enabled:
                return 0.0
            now = time.perf_counter()
            logger.duration(
                event,
                (now - previous) * 1000.0,
                records=len(records),
                structural_changed=structural_changed,
                **fields,
            )
            return now

        window.grid.set_ai_results(window._ai_bundle.results_by_path if window._ai_bundle and window._ai_bundle.results_by_path else {})
        step_start = log_step("records_view.finalize.ai_results", step_start)
        if window._phash_prefilter_settings.enabled or window._prefilter_decisions_by_path:
            window._scan.refresh_prefilter_decisions_for_current_folder()
        window.grid.set_prefilter_decisions(window._prefilter_decisions_by_path)
        step_start = log_step("records_view.finalize.prefilter", step_start)
        if not structural_changed:
            window.details_view.refresh_rows()
        step_start = log_step("records_view.finalize.details_refresh", step_start)
        window.grid.set_review_insights(window._review_intelligence.insights_by_path if window._review_intelligence is not None else {})
        window.grid.set_review_workflow_insights(window._workflow_insights_by_path)
        step_start = log_step("records_view.finalize.review_insights", step_start)
        window._preview_ctl.rebuild_visible_preview_group_indexes()
        step_start = log_step("records_view.finalize.group_indexes", step_start)
        window._annotation_ctl.refresh_burst_group_view(request_thumbnails=False)
        step_start = log_step("records_view.finalize.burst_groups", step_start)
        self.apply_records_view_action_mode()
        step_start = log_step("records_view.finalize.action_mode", step_start)

        restored_current = False
        if current_path:
            index = window._record_index_by_path.get(current_path)
            if index is not None:
                if index != window.grid.current_index():
                    window.grid.set_current_index(index)
                restored_current = True
        if restored_current and window._pending_focus_scroll_top:
            window._pending_focus_scroll_top = False
            QTimer.singleShot(0, lambda path=current_path: window._views.scroll_current_to_top(path))
        if records and not restored_current and structural_changed:
            window.grid.set_current_index(0)
        step_start = log_step("records_view.finalize.current", step_start, restored_current=restored_current)
        self._last_view_record_paths = next_record_paths
        self.enqueue_filter_metadata_paths(self.metadata_prefetch_seed_paths(), front=True)
        step_start = log_step("records_view.finalize.enqueue_metadata", step_start)
        window._views.refresh_viewport_mode()
        window._views.sync_details_view_from_grid()
        step_start = log_step("records_view.finalize.viewport_sync", step_start, view=self._session.browser_view_mode)
        window._inspector.update_action_states()
        step_start = log_step("records_view.finalize.action_states", step_start)
        window._inspector.update_status()
        step_start = log_step("records_view.finalize.status", step_start)
        if structural_changed and self._session.browser_view_mode == "grid":
            window.grid.schedule_visible_thumbnail_requests()
        step_start = log_step("records_view.finalize.thumbnail_schedule", step_start, view=self._session.browser_view_mode)
        if window._pending_folder_scroll_value is not None:
            QTimer.singleShot(0, window._views.restore_pending_folder_scroll)
        step_start = log_step("records_view.finalize.pending_scroll", step_start, has_pending_scroll=window._pending_folder_scroll_value is not None)
        window._startup.maybe_open_startup_quick_view()
        if logger.enabled:
            logger.duration(
                "records_view.finalize",
                (time.perf_counter() - start) * 1000.0,
                records=len(records),
                structural_changed=structural_changed,
                current_path=current_path or "",
            )

    def start_records_view_chunk(
        self,
        *,
        records: list[ImageRecord],
        current_path: str | None,
        post_load_enrichment: str,
    ) -> None:
        window = self._window
        perf_logger().log("records_view.chunk_start", records=len(records), current_path=current_path or "", post_load_enrichment=post_load_enrichment)
        window._records_view_chunk_timer.stop()
        window._records_view_chunk_records = records
        self._records_view_chunk_next_index = 0
        self._records_view_chunk_current_path = current_path
        window._records_view_chunk_post_load_enrichment = post_load_enrichment
        window._records = []
        window._record_index_by_path = {}
        window._visible_review_group_rows_by_id = {}
        window._visible_ai_group_rows_by_id = {}
        self._last_view_record_paths = ()
        window.grid.set_items([], emit_state_signals=False, request_thumbnails=False)
        window.details_view.set_records([])
        window._views.set_annotation_views()
        window.grid.set_ai_results(window._ai_bundle.results_by_path if window._ai_bundle and window._ai_bundle.results_by_path else {})
        if window._phash_prefilter_settings.enabled or window._prefilter_decisions_by_path:
            window._scan.refresh_prefilter_decisions_for_current_folder()
        window.grid.set_prefilter_decisions(window._prefilter_decisions_by_path)
        window.details_view.refresh_rows()
        window.grid.set_review_insights(window._review_intelligence.insights_by_path if window._review_intelligence is not None else {})
        window.grid.set_review_workflow_insights(window._workflow_insights_by_path)
        self.apply_records_view_action_mode()
        window._inspector.update_status()
        window._records_view_chunk_timer.start(0)

    def drain_records_view_chunk(self) -> None:
        window = self._window
        logger = perf_logger()
        start_time = time.perf_counter() if logger.enabled else 0.0
        records = window._records_view_chunk_records
        if not records:
            return
        start = self._records_view_chunk_next_index
        batch_size = max(1, window.CHUNKED_RESTORE_LOAD_BATCH_SIZE)
        end = min(len(records), start + batch_size)
        batch = records[start:end]
        if start == 0:
            window._records = list(batch)
            window._record_index_by_path = {record.path: index for index, record in enumerate(window._records)}
            window.grid.set_items(list(window._records), emit_state_signals=False, request_thumbnails=False)
            window.details_view.set_records(list(window._records))
            window._views.set_annotation_views()
        else:
            offset = len(window._records)
            window._records.extend(batch)
            for index, record in enumerate(batch, start=offset):
                window._record_index_by_path[record.path] = index
            window.grid.append_items(list(batch), request_thumbnails=False)
            window.details_view.append_records(list(batch))
        self._records_view_chunk_next_index = end
        if end < len(records):
            window._inspector.update_status()
            window._records_view_chunk_timer.start(0)
            if logger.enabled:
                logger.duration("records_view.chunk_batch", (time.perf_counter() - start_time) * 1000.0, start=start, end=end, total=len(records), done=False)
            return

        current_path = self._records_view_chunk_current_path
        post_load_enrichment = window._records_view_chunk_post_load_enrichment
        next_record_paths = tuple(record.path for record in records)
        window._records_view_chunk_records = []
        self._records_view_chunk_next_index = 0
        self._records_view_chunk_current_path = None
        window._records_view_chunk_post_load_enrichment = ""
        self.finalize_records_view_display(
            records=records,
            next_record_paths=next_record_paths,
            structural_changed=True,
            current_path=current_path,
        )
        if post_load_enrichment == "defer":
            self.finish_loaded_records_enrichment(list(window._all_records), defer_enrichment=True)
        elif post_load_enrichment == "start":
            self.finish_loaded_records_enrichment(list(window._all_records), defer_enrichment=False)
        window._startup.finish_quick_view_attempt_if_ready()
        if logger.enabled:
            logger.duration("records_view.chunk_batch", (time.perf_counter() - start_time) * 1000.0, start=start, end=end, total=len(records), done=True)

    def sort_records_for_active_context(self, records: list[ImageRecord]) -> list[ImageRecord]:
        window = self._window
        if window._sort_mode == SortMode.AI_WOW:
            if not window._winner_scores_by_path:
                window._scan.refresh_winner_scores_for_current_folder()

            def wow_key(record: ImageRecord) -> tuple[object, ...]:
                if record.is_folder:
                    return (0, record.name.casefold())
                score = window._inspector.winner_score_for_record(record)
                if score is None:
                    return (2, record.name.casefold())
                return (
                    1,
                    -float(score.get("blended_score") or 0.0),
                    record.name.casefold(),
                )

            return sorted(records, key=wow_key)

        if window._sort_mode != SortMode.AI_RANK or window._ai_bundle is None:
            return sort_records(records, window._sort_mode)

        def key(record: ImageRecord) -> tuple[object, ...]:
            if record.is_folder:
                return (0, record.name.casefold())
            result = find_ai_result_for_record(window._ai_bundle, record)
            if result is None:
                return (2, record.name.casefold())
            percentile = float(result.folder_percentile if result.folder_percentile is not None else -1.0)
            return (
                1,
                -float(result.score),
                -percentile,
                int(max(1, result.rank_in_group)),
                record.name.casefold(),
            )

        return sorted(records, key=key)

    def rank_records_for_unified_search(self, records: list[ImageRecord]) -> list[ImageRecord]:
        window = self._window
        if not window._filter_query.search_text.strip() or not self._unified_search_rank_by_path:
            return records

        def record_score(record: ImageRecord) -> float | None:
            scores = [
                self._unified_search_rank_by_path[key]
                for key in (_search_match_path_key(path) for path in record.stack_paths)
                if key in self._unified_search_rank_by_path
            ]
            return max(scores) if scores else None

        def key(item: tuple[int, ImageRecord]) -> tuple[object, ...]:
            index, record = item
            score = record_score(record)
            if score is None:
                return (1, index)
            return (0, -score, index)

        return [record for _, record in sorted(enumerate(records), key=key)]

    def apply_records_view(
        self,
        current_path: str | None = None,
        *,
        chunked: bool = False,
        post_load_enrichment: str = "",
    ) -> bool:
        window = self._window
        logger = perf_logger()
        start_time = time.perf_counter() if logger.enabled else 0.0
        if self.records_view_chunk_active() or not chunked:
            self.cancel_records_view_chunk()
        reasons, dirty_paths = window._records_view_cache.consume()
        force_workflow_rebuild = (
            ViewInvalidationReason.AI_CHANGED in reasons
            and bool(window._all_records)
        )
        if force_workflow_rebuild or dirty_paths:
            window._scan.refresh_workflow_insights_cache(
                changed_paths=set(dirty_paths) if dirty_paths else None,
                force_full=force_workflow_rebuild,
            )

        sorted_records = self.sort_records_for_active_context(list(window._all_records))
        sorted_records = self.rank_records_for_unified_search(sorted_records)
        visible_folder_records = (
            self.sort_records_for_active_context(list(window._folder_records))
            if self._session.scope_kind == "folder" and not window._filter_query.has_active_filters
            else []
        )
        needs_ai = window._filter_query.quick_filter in {FilterMode.AI_TOP_PICKS, FilterMode.AI_GROUPED, FilterMode.AI_DISAGREEMENTS}
        needs_ai = needs_ai or window._filter_query.ai_state != AIStateFilter.ALL
        needs_ai = needs_ai or window._filter_query.ai_cull_bucket is not None
        needs_aiculler_ingested = window._filter_query.quick_filter == FilterMode.AI_INGESTED
        needs_prefilter = window._filter_query.quick_filter == FilterMode.AI_PREFILTER_DUMPED
        needs_review = window._filter_query.quick_filter in {FilterMode.SMART_GROUPS, FilterMode.DUPLICATES}
        needs_workflow = window._filter_query.quick_filter == FilterMode.AI_DISAGREEMENTS
        needs_workflow = needs_workflow or window._filter_query.ai_state == AIStateFilter.DISAGREEMENTS
        needs_workflow = needs_workflow or bool(window._filter_query.ai_workflow_tag.strip())
        needs_metadata = window._filter_query.requires_metadata
        if needs_prefilter:
            window._scan.refresh_prefilter_decisions_for_current_folder()
        if needs_aiculler_ingested:
            window._aiculler.refresh_aiculler_ingested_paths_for_current_folder()
        if not window._filter_query.has_active_filters:
            records = [*visible_folder_records, *sorted_records]
        else:
            records = list(visible_folder_records)
            needs_dispute = window._filter_query.quick_filter == FilterMode.AI_DISAGREEMENTS
            for record in sorted_records:
                annotation = window._annotations.get(record.path, SessionAnnotation())
                ai_result = window._ai_run.ai_result_for_record(record) if needs_ai else None
                review_insight = window._inspector.review_insight_for_record(record) if needs_review else None
                workflow_insight = window._inspector.workflow_insight_for_record(record) if needs_workflow else None
                metadata = window._filter_metadata_by_path.get(record.path, EMPTY_METADATA) if needs_metadata else None
                is_disputed = window._aiculler.is_record_disputed(record) if needs_dispute else False
                prefilter_decision = window._inspector.prefilter_decision_for_record(record) if needs_prefilter else None
                ai_ingested = window._aiculler.record_was_aiculler_ingested(record) if needs_aiculler_ingested else False
                if matches_record_query(
                    record,
                    window._filter_query,
                    annotation=annotation,
                    ai_result=ai_result,
                    metadata=metadata,
                    review_insight=review_insight,
                    workflow_insight=workflow_insight,
                    is_disputed=is_disputed,
                    prefilter_decision=prefilter_decision,
                    ai_ingested=ai_ingested,
                    search_match_paths=self._unified_search_path_keys,
                    person_match_paths=self._person_filter_paths,
                ):
                    records.append(record)

        previous_record_paths = self._last_view_record_paths
        next_record_paths = tuple(record.path for record in records)
        structural_changed = previous_record_paths != next_record_paths

        window._records = records
        window._record_index_by_path = {record.path: index for index, record in enumerate(records)}
        window._annotation_ctl.recalculate_review_counts()
        if reasons.intersection(
            {
                ViewInvalidationReason.LOAD_CHANGED,
                ViewInvalidationReason.AI_CHANGED,
                ViewInvalidationReason.REVIEW_CHANGED,
            }
        ):
            window._ai_run.refresh_ai_summary_cache()
        if structural_changed:
            should_chunk = chunked and len(records) >= window.CHUNKED_RESTORE_LOAD_MIN_RECORDS
            if should_chunk:
                self.start_records_view_chunk(
                    records=records,
                    current_path=current_path,
                    post_load_enrichment=post_load_enrichment,
                )
                if logger.enabled:
                    logger.duration(
                        "records_view.apply",
                        (time.perf_counter() - start_time) * 1000.0,
                        records=len(records),
                        structural_changed=structural_changed,
                        chunked=True,
                        reasons=[reason.name for reason in reasons],
                    )
                return False
            window.grid.set_items(records, emit_state_signals=False, request_thumbnails=False)
            window.details_view.set_records(records)
            window._views.set_annotation_views()
        else:
            changed_visible_paths = tuple(path for path in dirty_paths if path in window._record_index_by_path)
            window.grid.update_items(
                GridDeltaUpdate(
                    changed_paths=changed_visible_paths,
                    selection_anchor=window.grid.current_index(),
                    preserve_pixmap_cache=True,
                )
            )
            if changed_visible_paths:
                window.details_view.refresh_rows(
                    {
                        window._record_index_by_path[path]
                        for path in changed_visible_paths
                        if path in window._record_index_by_path
                    }
                )
            if changed_visible_paths:
                window._views.set_annotation_views(changed_visible_paths)
        self.finalize_records_view_display(
            records=records,
            next_record_paths=next_record_paths,
            structural_changed=structural_changed,
            current_path=current_path,
        )
        if logger.enabled:
            logger.duration(
                "records_view.apply",
                (time.perf_counter() - start_time) * 1000.0,
                records=len(records),
                structural_changed=structural_changed,
                chunked=False,
                reasons=[reason.name for reason in reasons],
                dirty_paths=len(dirty_paths),
            )
        return True

    # -- Filter query & search-bar wiring -----------------------------------

    def set_filter_mode(self, mode: FilterMode) -> None:
        window = self._window
        window._filter_query.quick_filter = mode
        combo_index = window.filter_combo.findData(mode)
        if combo_index >= 0 and combo_index != window.filter_combo.currentIndex():
            window.filter_combo.setCurrentIndex(combo_index)
            return
        self.apply_filter_query_change()

    def handle_filter_changed(self) -> None:
        selected = self.selected_filter_mode()
        if selected is None:
            return
        self.set_filter_mode(selected)

    def selected_filter_mode(self) -> FilterMode | None:
        window = self._window
        selected = window.filter_combo.currentData()
        if isinstance(selected, FilterMode):
            return selected
        if isinstance(selected, str):
            for mode in FilterMode:
                if selected in {mode.name, mode.value}:
                    return mode
                try:
                    if FilterMode(selected) == mode:
                        return mode
                except ValueError:
                    continue
        text = window.filter_combo.currentText()
        for mode in FilterMode:
            if text == mode.value:
                return mode
        return None

    def handle_search_text_changed(self, text: str, *, source: str) -> None:
        window = self._window
        window._pending_search_text = text
        for field_name in ("manual_search_field", "ai_search_field", "topbar_search_field"):
            if field_name.startswith(source):
                continue
            field = getattr(window, field_name, None)
            if field is not None and field.text() != text:
                with QSignalBlocker(field):
                    field.setText(text)
        window._search_apply_timer.start()

    def commit_search_text_filter(self) -> None:
        window = self._window
        self.set_search_text(window._pending_search_text)

    def set_search_text(self, text: str) -> None:
        window = self._window
        if window._filter_query.search_text == text:
            return
        window._filter_query.search_text = text
        self.apply_filter_query_change()

    def set_file_type_filter(self, mode: FileTypeFilter) -> None:
        window = self._window
        if window._filter_query.file_type == mode:
            return
        window._filter_query.file_type = mode
        self.apply_filter_query_change()

    def set_review_state_filter(self, mode: ReviewStateFilter) -> None:
        window = self._window
        if window._filter_query.review_state == mode:
            return
        window._filter_query.review_state = mode
        self.apply_filter_query_change()

    def set_ai_state_filter(self, mode: AIStateFilter) -> None:
        window = self._window
        if window._filter_query.ai_state == mode:
            return
        window._filter_query.ai_state = mode
        self.apply_filter_query_change()

    def open_advanced_filters_dialog(self) -> None:
        window = self._window
        dialog = AdvancedFilterDialog(window._filter_query, window)
        if window._exec_dialog_with_geometry(dialog, "advanced_filters") != dialog.DialogCode.Accepted:
            return
        updated_query = dialog.updated_query()
        if updated_query == window._filter_query:
            return
        window._filter_query = updated_query
        self.apply_filter_query_change()
        window.statusBar().showMessage("Updated advanced filters")

    def clear_record_filters(self) -> None:
        window = self._window
        if not window._filter_query.has_active_filters:
            return
        window._filter_query = RecordFilterQuery()
        window._pending_search_text = ""
        self._person_filter_paths = frozenset()
        self.reset_unified_search_state()
        self.sync_record_filter_controls()
        self.apply_filter_query_change()
        window.statusBar().showMessage("Cleared filters")

    def apply_filter_query_change(self) -> None:
        window = self._window
        current_path = self.current_visible_record_path()
        self.sync_record_filter_controls()
        search_notice = self.schedule_unified_search_for_current_filter()
        self.ensure_filter_metadata_index()
        self.refresh_filter_toolbar_menu()
        if (
            window._all_records
            and window._review_intelligence is None
            and window._active_review_intelligence_task is None
            and window._filter_query.quick_filter in {FilterMode.SMART_GROUPS, FilterMode.DUPLICATES}
        ):
            window._scan.start_review_intelligence_analysis(force=True)
        window._records_view_cache.mark(ViewInvalidationReason.FILTER_CHANGED)
        self.apply_records_view(current_path=current_path)
        if search_notice:
            window.statusBar().showMessage(search_notice)
        if not window._records:
            return
        if current_path and current_path in window._record_index_by_path:
            return
        window._views.scroll_active_view_to_top()

    def sync_record_filter_controls(self) -> None:
        window = self._window
        search_text = window._filter_query.search_text
        for field in (window.manual_search_field, window.ai_search_field, getattr(window._toolbar, "topbar_search_field", None)):
            if field is None:
                continue
            if field.text() != search_text:
                with QSignalBlocker(field):
                    field.setText(search_text)

        combo_index = window.filter_combo.findData(window._filter_query.quick_filter)
        if combo_index >= 0 and combo_index != window.filter_combo.currentIndex():
            with QSignalBlocker(window.filter_combo):
                window.filter_combo.setCurrentIndex(combo_index)

        for mode, action in self._file_type_actions.items():
            with QSignalBlocker(action):
                action.setChecked(window._filter_query.file_type == mode)
        for mode, action in self._review_state_actions.items():
            with QSignalBlocker(action):
                action.setChecked(window._filter_query.review_state == mode)
        for mode, action in window._ai_state_actions.items():
            with QSignalBlocker(action):
                action.setChecked(window._filter_query.ai_state == mode)

    def current_visible_record_path(self) -> str | None:
        window = self._window
        current_record = window._record_at(window.grid.current_index())
        if current_record is None:
            return None
        return current_record.path

    def clear_search_from_workspace_menu(self) -> None:
        window = self._window
        window._records_view.handle_search_text_changed("", source="context_menu")
        window._search_apply_timer.stop()
        window._records_view.commit_search_text_filter()
        window.statusBar().showMessage("Cleared search")

    def annotation_change_affects_active_filter(self) -> bool:
        window = self._window
        if bool((window._filter_query.search_text or "").strip()):
            return True
        if window._filter_query.review_state != ReviewStateFilter.ALL:
            return True
        if window._filter_query.quick_filter in {
            FilterMode.WINNERS,
            FilterMode.REJECTS,
            FilterMode.UNREVIEWED,
            FilterMode.AI_DISAGREEMENTS,
        }:
            return True
        if window._filter_query.ai_state == AIStateFilter.DISAGREEMENTS:
            return True
        return False

    # -- Filter-metadata background index ------------------------------------

    def ensure_filter_metadata_index(self) -> None:
        window = self._window
        if not window._all_records:
            return
        if self._filter_metadata_record_paths:
            return
        self.reset_filter_metadata_index(window._all_records)

    def reset_filter_metadata_index(self, records: list[ImageRecord]) -> None:
        window = self._window
        window._filter_metadata_by_path = {}
        self._filter_metadata_loaded_paths = set()
        self._filter_metadata_requested_paths = set()
        self._filter_metadata_queue = deque()
        self._filter_metadata_queue_keys = set()
        self._metadata_membership_dirty_paths = set()
        self._metadata_scroll_last_value = window.grid.verticalScrollBar().value()
        self._metadata_scroll_direction = 1
        window._metadata_scroll_prefetch_timer.stop()
        window._metadata_request_timer.stop()
        self._filter_metadata_record_paths = {record.path for record in records}
        if len(records) <= window.FILTER_METADATA_EAGER_CACHE_MAX_RECORDS:
            for record in records:
                cached = window._filter_metadata_manager.get_cached(record)
                if cached is not None:
                    window._filter_metadata_by_path[record.path] = cached
                    self._filter_metadata_loaded_paths.add(record.path)
        if records:
            self.enqueue_filter_metadata_paths(self.metadata_prefetch_seed_paths(), front=True)

    def handle_filter_metadata_ready(self, key, metadata) -> None:
        window = self._window
        record = window._all_records_by_path.get(key.path)
        if record is None or record.path not in self._filter_metadata_record_paths:
            return
        self._filter_metadata_requested_paths.discard(record.path)
        window._filter_metadata_by_path[record.path] = metadata
        self._filter_metadata_loaded_paths.add(record.path)
        if window._filter_query.requires_metadata:
            if self.metadata_changes_filter_membership(record, metadata):
                self._metadata_membership_dirty_paths.add(record.path)
                window._metadata_reapply_timer.start()
            else:
                self.update_filter_summary()
        else:
            self.update_filter_summary()
        current_record = window._record_at(window.grid.current_index())
        if current_record is not None and current_record.path == record.path:
            window._inspector.update_inspector_context()
        if self._filter_metadata_queue and not window._metadata_request_timer.isActive():
            window._metadata_request_timer.start()

    def handle_metadata_filter_batch_update(self) -> None:
        window = self._window
        current_path = self.current_visible_record_path()
        if window._filter_query.requires_metadata:
            if not self._metadata_membership_dirty_paths:
                return
            self._metadata_membership_dirty_paths.clear()
            self.apply_records_view(current_path=current_path)
            return
        if window._burst_groups_enabled or window._burst_stacks_enabled:
            window._annotation_ctl.refresh_burst_group_view()

    def metadata_prefetch_seed_paths(self, *, lookahead: int = 120) -> list[str]:
        window = self._window
        visible_paths = window.grid.visible_item_paths(limit=220)
        if not visible_paths:
            return [record.path for record in window._all_records[: max(80, lookahead)]]

        ordered: list[str] = []
        seen: set[str] = set()
        for path in visible_paths:
            if path and path not in seen:
                ordered.append(path)
                seen.add(path)

        visible_indexes = [window._record_index_by_path[path] for path in visible_paths if path in window._record_index_by_path]
        if not visible_indexes:
            return ordered
        min_visible = min(visible_indexes)
        max_visible = max(visible_indexes)
        direction = 1 if self._metadata_scroll_direction >= 0 else -1
        if direction >= 0:
            start = max_visible + 1
            end = min(len(window._records), start + max(0, lookahead))
            candidate_paths = [window._records[index].path for index in range(start, end)]
        else:
            end = min_visible
            start = max(0, end - max(0, lookahead))
            candidate_paths = [window._records[index].path for index in range(end - 1, start - 1, -1)]

        for path in candidate_paths:
            if path and path not in seen:
                ordered.append(path)
                seen.add(path)
        return ordered

    @staticmethod
    def metadata_queue_key(path: str) -> str:
        return os.path.normpath(path).casefold()

    def enqueue_filter_metadata_paths(
        self,
        paths: list[str] | tuple[str, ...] | set[str],
        *,
        front: bool = False,
    ) -> None:
        window = self._window
        if not paths:
            return
        additions: list[str] = []
        for path in paths:
            if not path:
                continue
            if path not in self._filter_metadata_record_paths:
                continue
            if path in self._filter_metadata_loaded_paths or path in self._filter_metadata_requested_paths:
                continue
            key = self.metadata_queue_key(path)
            if key in self._filter_metadata_queue_keys:
                continue
            self._filter_metadata_queue_keys.add(key)
            additions.append(path)
        if not additions:
            return
        if front:
            for path in reversed(additions):
                self._filter_metadata_queue.appendleft(path)
        else:
            self._filter_metadata_queue.extend(additions)
        if len(self._filter_metadata_queue) > self._filter_metadata_queue_limit:
            while len(self._filter_metadata_queue) > self._filter_metadata_queue_limit:
                removed = self._filter_metadata_queue.pop()
                self._filter_metadata_queue_keys.discard(self.metadata_queue_key(removed))
        if not window._metadata_request_timer.isActive():
            window._metadata_request_timer.start()

    def schedule_metadata_scroll_prefetch(self, value: int) -> None:
        window = self._window
        if not window._records:
            return
        if value != self._metadata_scroll_last_value:
            self._metadata_scroll_direction = 1 if value > self._metadata_scroll_last_value else -1
            self._metadata_scroll_last_value = value
        window._metadata_scroll_prefetch_timer.start()

    def run_metadata_scroll_prefetch(self) -> None:
        window = self._window
        if not window._records:
            return
        self.enqueue_filter_metadata_paths(self.metadata_prefetch_seed_paths(), front=True)

    def metadata_changes_filter_membership(self, record: ImageRecord, metadata: CaptureMetadata) -> bool:
        window = self._window
        if not window._filter_query.requires_metadata:
            return False
        annotation = window._annotations.get(record.path, SessionAnnotation())
        needs_ai = (
            window._filter_query.quick_filter in {FilterMode.AI_TOP_PICKS, FilterMode.AI_GROUPED, FilterMode.AI_DISAGREEMENTS}
            or window._filter_query.ai_state != AIStateFilter.ALL
        )
        needs_review = window._filter_query.quick_filter in {FilterMode.SMART_GROUPS, FilterMode.DUPLICATES}
        needs_workflow = (
            window._filter_query.quick_filter == FilterMode.AI_DISAGREEMENTS
            or window._filter_query.ai_state == AIStateFilter.DISAGREEMENTS
        )
        ai_result = window._ai_run.ai_result_for_record(record) if needs_ai else None
        review_insight = window._inspector.review_insight_for_record(record) if needs_review else None
        workflow_insight = window._inspector.workflow_insight_for_record(record) if needs_workflow else None
        is_disputed = window._aiculler.is_record_disputed(record)
        old_match = matches_record_query(
            record,
            window._filter_query,
            annotation=annotation,
            ai_result=ai_result,
            metadata=EMPTY_METADATA,
            review_insight=review_insight,
            workflow_insight=workflow_insight,
            is_disputed=is_disputed,
            search_match_paths=self._unified_search_path_keys,
            person_match_paths=self._person_filter_paths,
        )
        new_match = matches_record_query(
            record,
            window._filter_query,
            annotation=annotation,
            ai_result=ai_result,
            metadata=metadata,
            review_insight=review_insight,
            workflow_insight=workflow_insight,
            is_disputed=is_disputed,
            search_match_paths=self._unified_search_path_keys,
            person_match_paths=self._person_filter_paths,
        )
        return old_match != new_match

    def drain_filter_metadata_requests(self) -> None:
        window = self._window
        if not self._filter_metadata_queue:
            window._metadata_request_timer.stop()
            return
        requested = 0
        while self._filter_metadata_queue and requested < 20:
            path = self._filter_metadata_queue.popleft()
            self._filter_metadata_queue_keys.discard(self.metadata_queue_key(path))
            if path in self._filter_metadata_loaded_paths or path in self._filter_metadata_requested_paths:
                continue
            record = window._all_records_by_path.get(path)
            if record is None:
                continue
            self._filter_metadata_requested_paths.add(path)
            priority = max(1, 12_000 - requested * 200)
            window._filter_metadata_manager.request_metadata(record, priority=priority)
            requested += 1
        if not self._filter_metadata_queue:
            window._metadata_request_timer.stop()

    def rekey_filter_metadata_after_moves(self, records_by_old_path: dict[str, ImageRecord]) -> None:
        window = self._window
        if not records_by_old_path:
            return

        updated_metadata: dict[str, CaptureMetadata] = {}
        for old_path, renamed_record in records_by_old_path.items():
            metadata = window._filter_metadata_by_path.pop(old_path, None)
            if metadata is not None:
                updated_metadata[renamed_record.path] = replace(metadata, path=renamed_record.path)
            if old_path in self._filter_metadata_loaded_paths:
                self._filter_metadata_loaded_paths.discard(old_path)
                self._filter_metadata_loaded_paths.add(renamed_record.path)
            if old_path in self._filter_metadata_record_paths:
                self._filter_metadata_record_paths.discard(old_path)
                self._filter_metadata_record_paths.add(renamed_record.path)

        window._filter_metadata_by_path.update(updated_metadata)

    # -- Saved filter presets ------------------------------------------------

    def build_advanced_filter_button(self) -> QToolButton:
        window = self._window
        button = QToolButton()
        button.setObjectName("workspaceFiltersButton")
        button.setText("Filters")
        button.setToolTip("Advanced filters and saved searches")
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        button.setMenu(window.filter_toolbar_menu)
        return button

    def build_record_filter_actions(self) -> None:
        window = self._window
        file_type_group = QActionGroup(window)
        file_type_group.setExclusive(True)
        for mode in FileTypeFilter:
            action = QAction(mode.value, window)
            action.setCheckable(True)
            action.triggered.connect(lambda _checked=False, selected=mode: self.set_file_type_filter(selected))
            file_type_group.addAction(action)
            self._file_type_actions[mode] = action

        review_group = QActionGroup(window)
        review_group.setExclusive(True)
        for mode in ReviewStateFilter:
            action = QAction(mode.value, window)
            action.setCheckable(True)
            action.triggered.connect(lambda _checked=False, selected=mode: self.set_review_state_filter(selected))
            review_group.addAction(action)
            self._review_state_actions[mode] = action

        ai_group = QActionGroup(window)
        ai_group.setExclusive(True)
        for mode in AIStateFilter:
            action = QAction(mode.value, window)
            action.setCheckable(True)
            action.triggered.connect(lambda _checked=False, selected=mode: self.set_ai_state_filter(selected))
            ai_group.addAction(action)
            window._ai_state_actions[mode] = action
            window.actions.ai_state_actions[mode] = action

    def populate_saved_filter_menu(self, menu: QMenu) -> None:
        window = self._window
        menu.addAction(window.actions.save_filter_preset)
        menu.addAction(window.actions.delete_filter_preset)
        menu.addSeparator()

        active_label = self.matching_filter_preset_label(window._filter_query)
        builtins = builtin_filter_presets()
        if builtins:
            builtins_header = menu.addSection("Smart Filters")
            builtins_header.setEnabled(False)
            for preset in builtins:
                action = menu.addAction(preset.name)
                action.setCheckable(True)
                action.setChecked(active_label == preset.name)
                action.triggered.connect(lambda _checked=False, target=preset: self.apply_filter_preset(target))
            menu.addSeparator()

        saved_header = menu.addSection("Saved Searches")
        saved_header.setEnabled(False)
        if window._saved_filter_presets:
            for preset in window._saved_filter_presets:
                action = menu.addAction(preset.name)
                action.setCheckable(True)
                action.setChecked(active_label == preset.name)
                action.triggered.connect(lambda _checked=False, target=preset: self.apply_filter_preset(target))
        else:
            empty_action = menu.addAction("No saved searches yet")
            empty_action.setEnabled(False)

    def refresh_filter_toolbar_menu(self) -> None:
        window = self._window
        window.filter_toolbar_menu.clear()
        file_type_menu = window.filter_toolbar_menu.addMenu("File Type")
        for mode in FileTypeFilter:
            file_type_menu.addAction(self._file_type_actions[mode])

        review_menu = window.filter_toolbar_menu.addMenu("Review State")
        for mode in ReviewStateFilter:
            review_menu.addAction(self._review_state_actions[mode])

        ai_menu = window.filter_toolbar_menu.addMenu("AI State")
        for mode in AIStateFilter:
            ai_menu.addAction(window._ai_state_actions[mode])

        window.filter_toolbar_menu.addSeparator()
        window.filter_toolbar_menu.addAction(window.actions.advanced_filters)
        window.filter_toolbar_menu.addAction(window.actions.clear_filters)
        window.filter_toolbar_menu.addSeparator()
        saved_menu = window.filter_toolbar_menu.addMenu("Saved Searches")
        self.populate_saved_filter_menu(saved_menu)

    def matching_saved_filter_preset(self, query: RecordFilterQuery | None = None) -> SavedFilterPreset | None:
        window = self._window
        target = query or window._filter_query
        for preset in window._saved_filter_presets:
            if preset.query == target:
                return preset
        return None

    def matching_filter_preset_label(self, query: RecordFilterQuery | None = None) -> str:
        window = self._window
        target = query or window._filter_query
        saved = self.matching_saved_filter_preset(target)
        if saved is not None:
            return saved.name
        for preset in builtin_filter_presets():
            if preset.query == target:
                return preset.name
        return ""

    def copy_filter_query(self, query: RecordFilterQuery) -> RecordFilterQuery:
        return RecordFilterQuery(
            quick_filter=query.quick_filter,
            search_text=query.search_text,
            min_search_confidence=query.min_search_confidence,
            file_type=query.file_type,
            review_state=query.review_state,
            ai_state=query.ai_state,
            ai_cull_bucket=query.ai_cull_bucket,
            ai_workflow_tag=query.ai_workflow_tag,
            folder_text=query.folder_text,
            camera_text=query.camera_text,
            lens_text=query.lens_text,
            tag_text=query.tag_text,
            min_rating=query.min_rating,
            orientation=query.orientation,
            captured_after=query.captured_after,
            captured_before=query.captured_before,
            iso_min=query.iso_min,
            iso_max=query.iso_max,
            focal_min=query.focal_min,
            focal_max=query.focal_max,
        )

    def apply_filter_preset(self, preset: SavedFilterPreset) -> None:
        window = self._window
        window._filter_query = self.copy_filter_query(preset.query)
        window._pending_search_text = window._filter_query.search_text
        self.apply_filter_query_change()
        window.statusBar().showMessage(f"Applied saved search: {preset.name}")

    def save_current_filter_preset(self) -> None:
        window = self._window
        if not window._filter_query.has_active_filters:
            window.statusBar().showMessage("Set a search or filter before saving a preset")
            return

        existing = self.matching_saved_filter_preset()
        initial_name = existing.name if existing is not None else self.matching_filter_preset_label(window._filter_query)
        name, accepted = QInputDialog.getText(window, "Save Current Search", "Preset name", text=initial_name)
        if not accepted:
            return
        name = (name or "").strip()
        if not name:
            return

        existing_index = next(
            (index for index, preset in enumerate(window._saved_filter_presets) if preset.name.casefold() == name.casefold()),
            None,
        )
        if existing_index is not None:
            overwrite = QMessageBox.question(
                window,
                "Overwrite Saved Search",
                f"A saved search named '{window._saved_filter_presets[existing_index].name}' already exists. Overwrite it?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if overwrite != QMessageBox.StandardButton.Yes:
                return
            window._saved_filter_presets[existing_index] = SavedFilterPreset(name=name, query=self.copy_filter_query(window._filter_query))
        else:
            window._saved_filter_presets.append(SavedFilterPreset(name=name, query=self.copy_filter_query(window._filter_query)))

        window._settings_ctl.save_saved_filter_presets()
        self.refresh_filter_toolbar_menu()
        self.update_filter_summary()
        window._inspector.update_action_states()
        window.statusBar().showMessage(f"Saved search: {name}")

    def delete_current_filter_preset(self) -> None:
        window = self._window
        preset = self.matching_saved_filter_preset()
        if preset is None:
            window.statusBar().showMessage("The current filter state is not one of your saved searches")
            return

        confirm = QMessageBox.question(
            window,
            "Delete Saved Search",
            f"Delete the saved search '{preset.name}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return

        window._saved_filter_presets = [item for item in window._saved_filter_presets if item.name.casefold() != preset.name.casefold()]
        window._settings_ctl.save_saved_filter_presets()
        self.refresh_filter_toolbar_menu()
        self.update_filter_summary()
        window._inspector.update_action_states()
        window.statusBar().showMessage(f"Deleted saved search: {preset.name}")

    # -- Filter summary chip --------------------------------------------------

    def update_filter_summary(self) -> None:
        window = self._window
        labels = active_filter_labels(window._filter_query)
        preset_label = self.matching_filter_preset_label(window._filter_query)
        metadata_progress = ""
        if window._filter_query.requires_metadata and self._filter_metadata_record_paths:
            loaded = len(self._filter_metadata_loaded_paths)
            total = len(self._filter_metadata_record_paths)
            if loaded < total:
                metadata_progress = f"Metadata {loaded}/{total}"
        if labels:
            summary_text = "Filters: " + " | ".join(labels)
            tooltip_lines = list(labels)
        else:
            summary_text = "Filters: All Images"
            tooltip_lines = ["No search or filters are active."]

        if preset_label:
            summary_text = f"Preset: {preset_label} | {summary_text}"
            tooltip_lines.insert(0, f"Preset: {preset_label}")
        if metadata_progress:
            summary_text = f"{summary_text} | {metadata_progress}"
            tooltip_lines.append(metadata_progress)

        if window._burst_groups_enabled or window._burst_stacks_enabled:
            burst_group_count = len(window._visible_burst_groups)
            burst_image_count = sum(len(group) for group in window._visible_burst_groups)
            burst_mode_labels: list[str] = []
            if window._burst_groups_enabled:
                burst_mode_labels.append("tags")
            if window._burst_stacks_enabled:
                burst_mode_labels.append("stacks")
            burst_mode_text = ", ".join(burst_mode_labels) if burst_mode_labels else "on"
            group_label = "Smart groups" if window._review_intelligence is not None else "Bursts"
            if burst_group_count:
                burst_summary = f"{group_label} {burst_group_count}"
                tooltip_lines.append(f"{group_label} {burst_mode_text}: {burst_group_count} group(s), {burst_image_count} image(s)")
            else:
                burst_summary = f"{group_label} On"
                tooltip_lines.append(f"{group_label} {burst_mode_text} is on. No related groups are currently visible.")
            summary_text = f"{summary_text} | {burst_summary}"
            visible_total = len(window._records)
            if visible_total and window._review_intelligence is None:
                visible_loaded = sum(1 for record in window._records if record.path in self._filter_metadata_loaded_paths)
                if visible_loaded < visible_total:
                    tooltip_lines.append(f"Burst detection metadata: {visible_loaded}/{visible_total}")
        tooltip_text = "\n".join(tooltip_lines)

        window.filter_summary_label.setText(summary_text)
        window.filter_summary_label.setToolTip(tooltip_text)
        window.clear_filters_button.setVisible(bool(labels))

        advanced_count = self.advanced_filter_count()
        button_text = f"Filters ({advanced_count})" if advanced_count else "Filters"
        button_tooltip = tooltip_text if labels else "Filter by file type, review state, or AI state."
        if window._saved_filter_presets:
            button_tooltip = f"{button_tooltip}\nSaved searches: {len(window._saved_filter_presets)}"
        if preset_label:
            button_tooltip = f"{button_tooltip}\nActive preset: {preset_label}"
        for button in (window.manual_filter_button, window.ai_filter_button):
            button.setText(button_text)
            button.setToolTip(button_tooltip)

    def advanced_filter_count(self) -> int:
        window = self._window
        count = int(window._filter_query.file_type != FileTypeFilter.ALL)
        count += int(window._filter_query.review_state != ReviewStateFilter.ALL)
        count += int(window._filter_query.ai_state != AIStateFilter.ALL)
        count += int(window._filter_query.ai_cull_bucket is not None)
        count += int(bool(window._filter_query.ai_workflow_tag.strip()))
        count += int(bool(window._filter_query.folder_text.strip()))
        count += int(window._filter_query.min_search_confidence > 0.0)
        count += int(bool(window._filter_query.camera_text.strip()))
        count += int(bool(window._filter_query.lens_text.strip()))
        count += int(bool(window._filter_query.tag_text.strip()))
        count += int(window._filter_query.min_rating > 0)
        count += int(window._filter_query.orientation != OrientationFilter.ALL)
        count += int(window._filter_query.captured_after is not None)
        count += int(window._filter_query.captured_before is not None)
        count += int(window._filter_query.iso_min > 0)
        count += int(window._filter_query.iso_max > 0)
        count += int(window._filter_query.focal_min > 0)
        count += int(window._filter_query.focal_max > 0)
        return count

    # -- Unified / semantic / face search ------------------------------------

    def unified_search_current_signature(self) -> tuple[object, ...]:
        window = self._window
        query = window._filter_query
        return (
            normalized_path_key(self._session.folder) if self._session.folder else "",
            query.search_text.strip(),
            round(float(query.min_search_confidence or 0.0), 4),
        )

    def reset_unified_search_state(self) -> None:
        window = self._window
        self._unified_search_token += 1
        if self._active_unified_search_task is not None:
            self._active_unified_search_task.cancel()
            self._active_unified_search_task = None
        self._unified_search_signature = ()
        self._unified_search_completed_signature = ()
        self._unified_search_path_keys = frozenset()
        self._unified_search_rank_by_path = {}

    def reset_semantic_index_state(self) -> None:
        window = self._window
        self._semantic_index_token += 1
        if window._active_semantic_index_task is not None:
            window._active_semantic_index_task.cancel()
            window._active_semantic_index_task = None
        self._semantic_index_scope_key = ""
        self._semantic_index_signature = ()
        self._semantic_index_completed = 0
        self._semantic_index_total = 0
        window._semantic_index_active = False
        self.reset_face_index_state()

    def reset_face_index_state(self) -> None:
        window = self._window
        self._face_index_token += 1
        if window._active_face_index_task is not None:
            window._active_face_index_task.cancel()
            window._active_face_index_task = None
        self._face_index_scope_key = ""
        self._face_index_signature = ()
        window._face_index_active = False
        self._face_index_people_count = 0

    def schedule_unified_search_for_current_filter(self) -> str:
        window = self._window
        signature = self.unified_search_current_signature()
        if signature != self._unified_search_signature:
            self._unified_search_signature = signature
            self._unified_search_completed_signature = ()
            self._unified_search_path_keys = frozenset()
            self._unified_search_rank_by_path = {}
            if self._active_unified_search_task is not None:
                self._active_unified_search_task.cancel()
                self._active_unified_search_task = None

        query_text = window._filter_query.search_text.strip()
        if not query_text:
            return ""
        if self._session.scope_kind != "folder" or not self._session.folder:
            return "Object and people search requires an opened folder."
        if self._active_unified_search_task is not None or self._unified_search_completed_signature == signature:
            return ""
        # Hold the search until background indexing for this folder finishes, so
        # results arrive as one complete set instead of trickling in as each
        # batch of embeddings lands. The search is re-triggered on completion.
        if (
            window._semantic_index_active
            and normalized_path_key(self._session.folder) == self._semantic_index_scope_key
        ):
            return self.semantic_index_status_text()
        paths = window._aiculler.aiculler_paths_for_current_folder()
        if paths is None:
            return "Indexed search is unavailable for this folder."
        db_path = aiculler_db_path(paths)
        if not db_path.exists():
            return (
                "Filename search only. Object and people search becomes available "
                "once the folder has been indexed."
            )
        try:
            runtime = window._settings_ctl.configured_aiculler_runtime(workers=1)
        except Exception as exc:
            return f"Indexed search unavailable: {exc}"

        self._unified_search_token += 1
        token = self._unified_search_token
        task = UnifiedSearchTask(
            folder=self._session.folder,
            token=token,
            db_path=db_path,
            runtime=runtime,
            query_text=query_text,
            min_confidence=float(window._filter_query.min_search_confidence),
        )
        task.signals.finished.connect(window._records_view.handle_unified_search_finished, Qt.ConnectionType.QueuedConnection)
        task.signals.failed.connect(window._records_view.handle_unified_search_failed, Qt.ConnectionType.QueuedConnection)
        self._active_unified_search_task = task
        window._unified_search_pool.start(task)
        return f'Searching indexed photos for "{query_text}"...'

    def handle_unified_search_finished(self, folder: str, token: int, result: object) -> None:
        window = self._window
        if token != self._unified_search_token or normalized_path_key(folder) != normalized_path_key(self._session.folder):
            return
        self._active_unified_search_task = None
        if not isinstance(result, dict):
            return
        query_text = str(result.get("query", ""))
        if query_text != window._filter_query.search_text.strip():
            return
        path_keys = frozenset(str(path) for path in result.get("path_keys", ()) if str(path))
        rank_by_path = result.get("rank_by_path", {})
        self._unified_search_completed_signature = self.unified_search_current_signature()
        self._unified_search_path_keys = path_keys
        self._unified_search_rank_by_path = (
            {str(path): float(score) for path, score in rank_by_path.items()}
            if isinstance(rank_by_path, dict)
            else {}
        )
        current_path = self.current_visible_record_path()
        window._records_view_cache.mark(ViewInvalidationReason.FILTER_CHANGED)
        self.apply_records_view(current_path=current_path)
        hit_count = int(result.get("hit_count") or 0)
        if query_text:
            if hit_count:
                window.statusBar().showMessage(f"Indexed search found {hit_count} image(s)")
            else:
                window.statusBar().showMessage(
                    "No matches. Try different words, or lower the Min Confidence filter."
                )

    def handle_unified_search_failed(self, folder: str, token: int, message: str) -> None:
        window = self._window
        if token != self._unified_search_token or normalized_path_key(folder) != normalized_path_key(self._session.folder):
            return
        self._active_unified_search_task = None
        self._unified_search_completed_signature = self.unified_search_current_signature()
        self._unified_search_path_keys = frozenset()
        self._unified_search_rank_by_path = {}
        if window._filter_query.search_text.strip():
            window.statusBar().showMessage(f"Indexed search unavailable: {message}")

    def semantic_index_status_text(self) -> str:
        window = self._window
        total = self._semantic_index_total
        if total <= 0:
            return "Preparing semantic search..."
        completed = min(self._semantic_index_completed, total)
        return f"Preparing semantic search: {completed:,} / {total:,}"

    def maybe_start_semantic_index(self, records: list[ImageRecord]) -> None:
        """Kick off background semantic-only embedding for the current folder.

        This is decoupled from Index & Score: it builds TinyCLIP image
        embeddings so natural-language search becomes available without running
        technical scoring, TOPIQ, face detection, clustering, or ranking. It is
        best-effort — if the AI runtime or CLIP model is not installed we stay
        quiet and leave filename search as the fallback.
        """
        window = self._window
        if self._session.scope_kind != "folder" or not self._session.folder:
            return
        if not records:
            return
        # Remember the records so background indexing can be restarted after the
        # editor closes (see resume_background_indexing).
        window._background_index_records = list(records)
        if window._background_indexing_suspended:
            return  # editor owns the GPU; resume kicks this off again on close
        if not (window._ai_setup.ai_runtime_available() and window._ai_setup.aiculler_clip_model_available()):
            return
        paths = window._aiculler.aiculler_paths_for_current_folder()
        if paths is None:
            return
        try:
            runtime = window._settings_ctl.configured_aiculler_runtime(workers=1)
        except Exception:
            return
        clip_vision_model = getattr(runtime, "clip_vision_model", None)
        if clip_vision_model is None or not Path(clip_vision_model).exists():
            return
        clip_fallback = getattr(runtime, "clip_fallback_vision_model", None)
        try:
            model_identity = compute_semantic_model_identity(
                clip_vision_model,
                embedding_dim=SEMANTIC_EMBEDDING_DIM,
                fallback_model=clip_fallback,
            )
        except Exception:
            return

        scope_key = normalized_path_key(self._session.folder)
        signature = (scope_key, model_identity, len(records))
        if (
            window._active_semantic_index_task is not None
            and self._semantic_index_scope_key == scope_key
            and self._semantic_index_signature == signature
        ):
            return
        if window._active_semantic_index_task is not None:
            window._active_semantic_index_task.cancel()
            window._active_semantic_index_task = None

        self._semantic_index_token += 1
        token = self._semantic_index_token
        self._semantic_index_scope_key = scope_key
        self._semantic_index_signature = signature
        self._semantic_index_completed = 0
        self._semantic_index_total = 0
        window._semantic_index_active = True
        task = SemanticFolderIndexTask(
            folder=self._session.folder,
            token=token,
            records=tuple(records),
            db_path=aiculler_db_path(paths),
            clip_vision_model=clip_vision_model,
            clip_fallback_model=clip_fallback,
            model_identity=model_identity,
            device=str(getattr(runtime, "device", "auto")),
            expected_dim=SEMANTIC_EMBEDDING_DIM,
        )
        task.signals.progress.connect(window._records_view.handle_semantic_index_progress, Qt.ConnectionType.QueuedConnection)
        task.signals.finished.connect(window._records_view.handle_semantic_index_finished, Qt.ConnectionType.QueuedConnection)
        task.signals.failed.connect(window._records_view.handle_semantic_index_failed, Qt.ConnectionType.QueuedConnection)
        window._active_semantic_index_task = task
        window._semantic_index_pool.start(task)

    def semantic_index_event_is_current(self, folder: str, token: int) -> bool:
        window = self._window
        return (
            token == self._semantic_index_token
            and normalized_path_key(folder) == normalized_path_key(self._session.folder)
        )

    def handle_semantic_index_progress(self, folder: str, token: int, completed: int, total: int) -> None:
        window = self._window
        if not self.semantic_index_event_is_current(folder, token):
            return
        self._semantic_index_completed = int(completed)
        self._semantic_index_total = int(total)
        # Restrained: only surface progress while the user is waiting on a query.
        if window._filter_query.search_text.strip():
            window.statusBar().showMessage(self.semantic_index_status_text())

    def handle_semantic_index_finished(self, folder: str, token: int, indexed: int, ready_total: int) -> None:
        window = self._window
        if not self.semantic_index_event_is_current(folder, token):
            write_execution_log(
                f"face-index: semantic finished but STALE (token {token} != {self._semantic_index_token}); "
                f"not chaining face pass"
            )
            return
        window._active_semantic_index_task = None
        window._semantic_index_active = False
        write_execution_log(
            f"face-index: semantic index finished (indexed={indexed}, ready_total={ready_total}) "
            f"-> chaining face pass"
        )
        # Now that the whole folder is indexed, run any query the user was
        # waiting on once, over the complete set.
        if window._filter_query.search_text.strip():
            window.statusBar().showMessage("Semantic search ready")
            self.run_deferred_unified_search()
        # The face pass reuses the embeddings just produced, so start it now.
        self.maybe_start_face_index()

    def handle_semantic_index_failed(self, folder: str, token: int, message: str) -> None:
        window = self._window
        if not self.semantic_index_event_is_current(folder, token):
            return
        window._active_semantic_index_task = None
        window._semantic_index_active = False
        if window._filter_query.search_text.strip():
            window.statusBar().showMessage(f"Semantic search indexing failed: {message}")
            # Fall back to whatever was indexed (plus filename matches) so the
            # search box is not left hanging on the deferred state.
            self.run_deferred_unified_search()

    def run_deferred_unified_search(self) -> None:
        """Run the query held back during indexing, as a single complete pass."""
        window = self._window
        if not window._filter_query.search_text.strip():
            return
        if self._session.scope_kind != "folder" or not self._session.folder:
            return
        self._unified_search_completed_signature = ()
        if self._active_unified_search_task is not None:
            self._active_unified_search_task.cancel()
            self._active_unified_search_task = None
        notice = self.schedule_unified_search_for_current_filter()
        if notice:
            window.statusBar().showMessage(notice)

    def maybe_start_face_index(self) -> None:
        """Kick off the person-filtered face pass for the current folder.

        Best-effort and layered on the semantic index: it reuses the stored
        TinyCLIP embeddings for a cheap person pre-filter and runs AuraFace only
        on the flagged subset. Requires the AI runtime and the AuraFace face
        model to be installed; otherwise it stays quiet (people tagging simply
        does not populate until the face model is installed).
        """
        window = self._window
        if self._session.scope_kind != "folder" or not self._session.folder:
            write_execution_log("face-index: skip (not a folder scope)")
            return
        if window._background_indexing_suspended:
            write_execution_log("face-index: skip (background indexing suspended — editor open)")
            return  # editor owns the GPU; resume kicks this off again on close
        if not (window._ai_setup.ai_runtime_available() and window._ai_setup.aiculler_face_model_available()):
            write_execution_log(
                f"face-index: skip (runtime_available={window._ai_setup.ai_runtime_available()}, "
                f"face_model_available={window._ai_setup.aiculler_face_model_available()})"
            )
            return
        paths = window._aiculler.aiculler_paths_for_current_folder()
        if paths is None:
            write_execution_log("face-index: skip (no aiculler paths)")
            return
        db_path = aiculler_db_path(paths)
        if not db_path.exists():
            write_execution_log(f"face-index: skip (db missing: {db_path})")
            return
        try:
            runtime = window._settings_ctl.configured_aiculler_runtime(workers=1)
        except Exception as exc:
            write_execution_log(f"face-index: skip (runtime config failed: {exc})")
            return
        text_model = getattr(runtime, "clip_text_model", None)
        tokenizer = getattr(runtime, "tokenizer", None)
        if text_model is None or tokenizer is None or not Path(text_model).exists() or not Path(tokenizer).exists():
            write_execution_log(
                f"face-index: skip (clip text model/tokenizer missing: text={text_model}, tok={tokenizer})"
            )
            return

        scope_key = normalized_path_key(self._session.folder)
        signature = (scope_key, str(text_model))
        if (
            window._active_face_index_task is not None
            and self._face_index_scope_key == scope_key
            and self._face_index_signature == signature
        ):
            write_execution_log("face-index: skip (already running for this folder)")
            return
        if window._active_face_index_task is not None:
            window._active_face_index_task.cancel()
            window._active_face_index_task = None
        write_execution_log(f"face-index: STARTING pass for {self._session.folder} (device={getattr(runtime,'device','?')})")

        self._face_index_token += 1
        token = self._face_index_token
        self._face_index_scope_key = scope_key
        self._face_index_signature = signature
        window._face_index_active = True
        task = FaceFolderIndexTask(
            folder=self._session.folder,
            token=token,
            db_path=db_path,
            clip_text_model=text_model,
            clip_tokenizer=tokenizer,
            clip_fallback_text_model=getattr(runtime, "clip_fallback_text_model", None),
            device=str(getattr(runtime, "device", "auto")),
        )
        task.signals.progress.connect(window._records_view.handle_face_index_progress, Qt.ConnectionType.QueuedConnection)
        task.signals.finished.connect(window._records_view.handle_face_index_finished, Qt.ConnectionType.QueuedConnection)
        task.signals.failed.connect(window._records_view.handle_face_index_failed, Qt.ConnectionType.QueuedConnection)
        window._active_face_index_task = task
        window._face_index_pool.start(task)

    def suspend_background_indexing(self) -> None:
        """Hand the GPU to the interactive editor: cancel the background semantic
        and face index passes so they stop submitting GPU work, freeing the device
        for the mask engine (SAM / OneFormer / BiRefNet). Cancellation is checked
        between images, so any in-flight single inference finishes (<~1s) and then
        the task returns, releasing its onnxruntime session. The passes are
        incremental, so no progress is lost — resume picks up where they stopped."""
        window = self._window
        if window._background_indexing_suspended:
            write_execution_log("gpu-arbitration: suspend requested but already suspended")
            return
        sem_active = window._active_semantic_index_task is not None
        face_active = window._active_face_index_task is not None
        write_execution_log(
            f"gpu-arbitration: editor opened -> suspend indexing "
            f"(semantic_task={sem_active}, face_task={face_active})"
        )
        window._background_indexing_suspended = True
        if window._active_semantic_index_task is not None:
            window._active_semantic_index_task.cancel()
            window._active_semantic_index_task = None
            window._semantic_index_active = False
        if window._active_face_index_task is not None:
            window._active_face_index_task.cancel()
            window._active_face_index_task = None
            window._face_index_active = False

    def resume_background_indexing(self) -> None:
        """Editor closed: resume background indexing on the GPU where it left off.
        The semantic pass is incremental and chains into the face pass on finish,
        so restarting it is enough to continue both."""
        window = self._window
        if not window._background_indexing_suspended:
            return
        write_execution_log(
            f"gpu-arbitration: editor closed -> resume indexing "
            f"(records={len(window._background_index_records)})"
        )
        window._background_indexing_suspended = False
        if window._background_index_records:
            self.maybe_start_semantic_index(window._background_index_records)

    def face_index_event_is_current(self, folder: str, token: int) -> bool:
        window = self._window
        return (
            token == self._face_index_token
            and normalized_path_key(folder) == normalized_path_key(self._session.folder)
        )

    def handle_face_index_progress(self, folder: str, token: int, completed: int, total: int) -> None:
        window = self._window
        if not self.face_index_event_is_current(folder, token):
            return
        # Restrained: no chrome yet (People panel lands in Phase 3). Keep it quiet
        # unless diagnostics want it; progress is tracked for that panel to read.
        self._face_index_completed = int(completed)
        self._face_index_total = int(total)

    def handle_face_index_finished(self, folder: str, token: int, faces_indexed: int, people_count: int) -> None:
        window = self._window
        window._projects.refresh_face_groups()
        if not self.face_index_event_is_current(folder, token):
            return
        window._active_face_index_task = None
        window._face_index_active = False
        self._face_index_people_count = int(people_count)

    def handle_face_index_failed(self, folder: str, token: int, message: str) -> None:
        window = self._window
        if not self.face_index_event_is_current(folder, token):
            return
        window._active_face_index_task = None
        window._face_index_active = False
        perf_logger().log("face_index.failed", folder=folder, message=message)
        window.statusBar().showMessage(f"People indexing failed: {message}")

    def open_people_search_dialog(self) -> None:
        window = self._window
        paths = window._aiculler.aiculler_paths_for_current_folder()
        if paths is None:
            window.statusBar().showMessage("Choose a folder before managing people.")
            return
        db_path = aiculler_db_path(paths)
        if not db_path.exists():
            window.statusBar().showMessage("People are found automatically once this folder finishes indexing.")
            return
        # Retry a previously failed or interrupted face pass and let the modal
        # dialog observe its progressive commits through its refresh timer.
        if not window._semantic_index_active:
            self.maybe_start_face_index()
        dialog = PeopleSearchDialog(db_path, window)
        # Always open at the dialog's own 3x3 default (do not restore a saved
        # geometry, which could reopen at a cramped 2-column size), centered on
        # the main window.
        frame = dialog.frameGeometry()
        frame.moveCenter(window.frameGeometry().center())
        dialog.move(frame.topLeft())
        if dialog.exec() == dialog.DialogCode.Accepted:
            self._unified_search_completed_signature = ()
            self._unified_search_path_keys = frozenset()
            self._unified_search_rank_by_path = {}
            self.schedule_unified_search_for_current_filter()
            window._records_view_cache.mark(ViewInvalidationReason.FILTER_CHANGED)
            self.apply_records_view(current_path=self.current_visible_record_path())
            label = getattr(dialog, "requested_person_label", "")
            paths = getattr(dialog, "requested_person_paths", ())
            if label:
                self.show_photos_for_person(label, paths)
            else:
                window.statusBar().showMessage("Updated people names")

    def show_photos_for_person(self, label: str, paths) -> None:
        """Filter the grid to the photos one face appears in.

        Driven by the cluster's own image paths rather than by the person's
        name, so it works for faces nobody has named yet. It reads as an
        ordinary filter: it shows up in the active-filter chips and the usual
        Clear filters action removes it.
        """
        window = self._window
        # Must be the same key the matcher builds, so use the search helper
        # rather than normalized_path_key (which also resolves symlinks).
        keys = frozenset(
            key for key in (_search_match_path_key(path) for path in paths) if key
        )
        self._person_filter_paths = keys
        window._filter_query.person_label = label
        self.apply_filter_query_change()
        if not keys:
            window.statusBar().showMessage(f"No indexed photos found for {label}")
            return
        count = len(keys)
        window.statusBar().showMessage(
            f"Showing {count} photo(s) of {label} - use Clear filters to go back"
        )
