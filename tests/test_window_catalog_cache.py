from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication, QComboBox, QFrame, QGridLayout, QListWidgetItem, QMessageBox, QWidget

from image_triage.ai_results import (
    AIConfidenceBucket,
    AIImageResult,
    ai_review_tag_definitions,
    build_ai_bundle_from_results,
    inspect_ai_bundle_source,
)
from image_triage.ai_workflow import default_ai_workflow_runtime
from image_triage.catalog import CatalogRepository
from image_triage.models import ImageRecord
from image_triage.review_workflows import BurstRecommendation, TasteProfile, build_review_scoring_cache_key
from image_triage.window import (
    AIReviewCompleteDialog,
    AITrainingExecutionContext,
    MainWindow,
    ScopeEnrichmentTask,
    _DirectorySuggestionController,
    _build_ai_training_action_availability,
)


def _ensure_app() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def _record(path: str, *, name: str, size: int, modified_ns: int) -> ImageRecord:
    return ImageRecord(
        path=path,
        name=name,
        size=size,
        modified_ns=modified_ns,
    )


class _WindowCacheStub:
    def __init__(self, repository: CatalogRepository) -> None:
        self._catalog_repository = repository


class _WindowRebuildStub:
    def __init__(self, folder: str = "") -> None:
        self._scope_kind = "folder" if folder else "collection"
        self._current_folder = folder
        self.status_messages: list[str] = []
        self.load_calls: list[tuple[str, bool, bool]] = []

    def statusBar(self):
        return self

    def showMessage(self, message: str) -> None:
        self.status_messages.append(message)

    def _load_folder(
        self,
        folder: str,
        *,
        force_refresh: bool = False,
        chunked_restore: bool = False,
        bypass_catalog_cache: bool = False,
    ) -> None:
        self.load_calls.append((folder, force_refresh, bypass_catalog_cache))


class _WindowLaunchStub:
    def __init__(self) -> None:
        self.select_calls: list[tuple[str, bool, bool, str | None]] = []
        self.status_messages: list[str] = []
        self.folder_tree = SimpleNamespace(
            clearSelection=lambda: None,
            setCurrentIndex=lambda _index=None: None,
        )

    def _select_folder(
        self,
        folder: str,
        *,
        sync_tree: bool = True,
        chunked_restore: bool = False,
        preferred_record_path: str | None = None,
    ) -> None:
        self.select_calls.append((folder, sync_tree, chunked_restore, preferred_record_path))

    def statusBar(self):
        return self

    def showMessage(self, message: str) -> None:
        self.status_messages.append(message)


class _ScopeStartStub:
    def __init__(self, repository: CatalogRepository, records: list[ImageRecord]) -> None:
        self._all_records = records
        self._catalog_repository = repository
        self._session_id = "LinkFlow"
        self._current_folder = ""
        self._scope_kind = "catalog"
        self._ai_bundle = None
        self._active_ai_task = None
        self._ai_deferred_background_work = False
        self._ai_deferred_background_scope_key = ""
        self._review_intelligence = None
        self._scope_enrichment_token = 0
        self._active_scope_enrichment_task = None
        self._scope_enrichment_pool = self
        self._review_scoring_cache_source = "idle"
        self._review_scoring_cache_detail = ""
        self._refresh_calls = 0

    def _current_scope_key(self) -> str:
        return "catalog:root"

    def _cancel_scope_enrichment_task(self) -> None:
        self._active_scope_enrichment_task = None

    def _mark_background_review_work_deferred_for_ai(self, *, reason: str) -> None:
        MainWindow._mark_background_review_work_deferred_for_ai(self, reason=reason)

    def _refresh_catalog_status_indicator(self) -> None:
        self._refresh_calls += 1

    def _handle_scope_enrichment_cache_status(self, *args, **kwargs) -> None:
        pass

    def _handle_scope_enrichment_finished(self, *args, **kwargs) -> None:
        pass

    def _handle_scope_enrichment_failed(self, *args, **kwargs) -> None:
        pass

    def start(self, task) -> None:
        self._active_scope_enrichment_task = task


class _WindowStateFixupStub:
    def __init__(self, *, startup_state: str, maximized: bool = False, fullscreen: bool = False) -> None:
        self._startup_window_state = startup_state
        self._maximized = maximized
        self._fullscreen = fullscreen
        self.calls: list[str] = []

    def isMaximized(self) -> bool:
        return self._maximized

    def isFullScreen(self) -> bool:
        return self._fullscreen

    def showNormal(self) -> None:
        self.calls.append("normal")
        self._maximized = False
        self._fullscreen = False

    def showMaximized(self) -> None:
        self.calls.append("maximized")
        self._maximized = True

    def showFullScreen(self) -> None:
        self.calls.append("fullscreen")
        self._fullscreen = True


class _SettingsStub:
    def __init__(self) -> None:
        self.values: dict[str, object] = {}

    def value(self, key: str, default: object = None, value_type: object = None) -> object:
        return self.values.get(key, default)

    def setValue(self, key: str, value: object) -> None:
        self.values[key] = value

    def remove(self, key: str) -> None:
        self.values.pop(key, None)


class _WindowAiLoadStub:
    AI_RESULTS_KEY = MainWindow.AI_RESULTS_KEY

    def __init__(self, repository: CatalogRepository, folder: str, records: list[ImageRecord]) -> None:
        self._catalog_repository = repository
        self._current_folder = folder
        self._all_records = records
        self._ai_bundle = None
        self._active_ai_task = None
        self._ai_stage_message = ""
        self._ai_stage_index = 0
        self._ai_stage_total = 0
        self._ai_progress_current = 0
        self._ai_progress_total = 0
        self._ai_progress_eta_text = ""
        self._settings = _SettingsStub()
        self.refresh_calls = 0
        self.status_messages: list[str] = []

    def _refresh_ai_state(self) -> None:
        self.refresh_calls += 1

    def statusBar(self):
        return self

    def showMessage(self, message: str) -> None:
        self.status_messages.append(message)


class _WindowAiRestoreStub:
    AI_RESULTS_KEY = MainWindow.AI_RESULTS_KEY

    def __init__(self, folder: str, saved_path: str) -> None:
        self._current_folder = folder
        self._ui_mode = "ai"
        self._ai_bundle = None
        self._settings = _SettingsStub()
        self._settings.setValue(self.AI_RESULTS_KEY, saved_path)
        self.load_ai_calls: list[tuple[str, bool]] = []
        self.refresh_calls = 0
        self.toolbar_updates = 0

    def _load_ai_results(self, path: str, *, show_message: bool = True) -> bool:
        self.load_ai_calls.append((path, show_message))
        return True

    def _saved_ai_results_belong_to_current_folder(self, saved_path: str) -> bool:
        return MainWindow._saved_ai_results_belong_to_current_folder(self, saved_path)

    def _clear_ai_results_state(self, *, preserve_setting: bool = False, refresh: bool = True) -> None:
        self._ai_bundle = None
        if refresh:
            self._refresh_ai_state()

    def _refresh_ai_state(self) -> None:
        self.refresh_calls += 1

    def _update_ai_toolbar_state(self) -> None:
        self.toolbar_updates += 1


class _SignalStub:
    def connect(self, *args, **kwargs) -> None:
        return None


class _AIRunSignalsStub:
    def __init__(self) -> None:
        self.started = _SignalStub()
        self.stage = _SignalStub()
        self.progress = _SignalStub()
        self.finished = _SignalStub()
        self.failed = _SignalStub()


class _AIRunTaskCapture:
    instances: list["_AIRunTaskCapture"] = []

    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.signals = _AIRunSignalsStub()
        _AIRunTaskCapture.instances.append(self)


class _AiRunPoolStub:
    def __init__(self) -> None:
        self.started_task = None

    def start(self, task) -> None:
        self.started_task = task


class _ModeTabsStub:
    def __init__(self) -> None:
        self.index = -1

    def setCurrentIndex(self, index: int) -> None:
        self.index = index


class _WindowAiRunStub:
    def __init__(
        self,
        repository: CatalogRepository,
        folder: str,
        records: list[ImageRecord],
        *,
        load_hidden_result: bool = False,
    ) -> None:
        self._catalog_repository = repository
        self._current_folder = folder
        self._all_records = records
        self._ai_runtime = default_ai_workflow_runtime()
        self._ai_semantic_sidecar_enabled = False
        self._active_reference_bank_path = ""
        self._active_ai_task = None
        self._ai_run_pool = _AiRunPoolStub()
        self.mode_tabs = _ModeTabsStub()
        self.status_messages: list[str] = []
        self.toolbar_updates = 0
        self.load_hidden_calls = 0
        self.load_hidden_result = load_hidden_result
        self._ai_stage_index = 0
        self._ai_stage_total = 3
        self._ai_stage_message = ""
        self._ai_progress_current = 0
        self._ai_progress_total = 0
        self._ai_progress_eta_text = ""
        self._active_ai_embedding_cache_key = ""
        self._active_ai_cluster_cache_key = ""
        self._active_ai_report_cache_key = ""
        self._active_ai_semantic_cache_key = ""
        self.defer_background_calls = 0

    def _ensure_ai_model_available(self, *, title: str) -> bool:
        return True

    def _ensure_semantic_model_available(self, *, title: str) -> bool:
        return True

    def _ensure_ai_runtime_available(self, *, title: str) -> bool:
        return True

    def _refresh_ai_runtime_preferences(self) -> None:
        return None

    def _ai_training_paths_for_folder(self, folder: str | None = None):
        return None

    def _current_trained_checkpoint_path(self):
        return None

    def _load_hidden_ai_results_for_current_folder(self, *, show_message: bool = True) -> bool:
        self.load_hidden_calls += 1
        return self.load_hidden_result

    def _update_ai_toolbar_state(self) -> None:
        self.toolbar_updates += 1

    def _defer_background_review_work_for_ai(self, *, reason: str) -> None:
        self.defer_background_calls += 1

    def _handle_ai_run_started(self, *args, **kwargs) -> None:
        pass

    def _handle_ai_run_stage(self, *args, **kwargs) -> None:
        pass

    def _handle_ai_run_progress(self, *args, **kwargs) -> None:
        pass

    def _handle_ai_run_finished(self, *args, **kwargs) -> None:
        pass

    def _handle_ai_run_failed(self, *args, **kwargs) -> None:
        pass

    def statusBar(self):
        return self

    def showMessage(self, message: str) -> None:
        self.status_messages.append(message)


class _WindowAiRunFinishedStub:
    def __init__(self, repository: CatalogRepository, folder: str) -> None:
        self._catalog_repository = repository
        self._current_folder = folder
        self._active_ai_task = object()
        self._active_ai_embedding_cache_key = "embed-finished"
        self._active_ai_cluster_cache_key = "cluster-finished"
        self._active_ai_report_cache_key = "report-finished"
        self._active_ai_semantic_cache_key = ""
        self._ai_semantic_sidecar_enabled = False
        self._ai_stage_index = 0
        self._ai_stage_total = 3
        self._ai_stage_message = ""
        self._ai_progress_current = 0
        self._ai_progress_total = 0
        self._ai_progress_eta_text = ""
        self._ai_semantic_sidecar_enabled = False
        self.mode_tabs = _ModeTabsStub()
        self.load_ai_calls: list[tuple[str, bool]] = []
        self.completion_dialog_calls: list[dict[str, object]] = []
        self.status_messages: list[str] = []
        self.toolbar_updates = 0
        self._ai_bundle = None
        self.resume_background_calls = 0

    def _load_ai_results(self, report_dir: str, *, show_message: bool = True) -> bool:
        self.load_ai_calls.append((report_dir, show_message))
        return True

    def _show_ai_review_complete_dialog(self, **kwargs) -> None:
        self.completion_dialog_calls.append(kwargs)

    def _update_ai_toolbar_state(self) -> None:
        self.toolbar_updates += 1

    def _resume_deferred_background_review_work_after_ai(self, *, reason: str) -> None:
        self.resume_background_calls += 1

    def statusBar(self):
        return self

    def showMessage(self, message: str) -> None:
        self.status_messages.append(message)


class _WindowAiResetStub:
    AI_RESULTS_KEY = MainWindow.AI_RESULTS_KEY

    def __init__(self, repository: CatalogRepository, folder: str) -> None:
        self._catalog_repository = repository
        self._current_folder = folder
        self._active_ai_task = None
        self._active_ai_runtime_task = None
        self._active_ai_training_task = None
        self._active_ai_model_task = None
        self._ai_bundle = object()
        self._ai_stage_index = 1
        self._ai_stage_total = 3
        self._ai_stage_message = "Loaded"
        self._ai_progress_current = 1
        self._ai_progress_total = 1
        self._ai_progress_eta_text = ""
        self._ai_semantic_sidecar_enabled = False
        self._settings = _SettingsStub()
        self._settings.setValue(self.AI_RESULTS_KEY, folder)
        self.refresh_calls = 0
        self.status_messages: list[str] = []

    def _clear_ai_results_state(self, *, preserve_setting: bool = False) -> None:
        MainWindow._clear_ai_results_state(self, preserve_setting=preserve_setting)

    def _refresh_ai_state(self) -> None:
        self.refresh_calls += 1

    def statusBar(self):
        return self

    def showMessage(self, message: str) -> None:
        self.status_messages.append(message)


class _WindowAiSummaryStub:
    def __init__(self) -> None:
        self._last_ai_review_summary: dict[str, object] | None = None
        self._ai_bundle = None
        self._current_folder = ""
        self.summary_calls: list[dict[str, object]] = []
        self.status_messages: list[str] = []

    def _last_ai_review_summary_for_current_state(self):
        return MainWindow._last_ai_review_summary_for_current_state(self)

    def _show_ai_review_complete_dialog(self, **kwargs) -> None:
        self.summary_calls.append(kwargs)

    def statusBar(self):
        return self

    def showMessage(self, message: str) -> None:
        self.status_messages.append(message)


class _LabelLaunchFinishStub:
    def __init__(self, folder: str) -> None:
        self._ai_training_context = AITrainingExecutionContext(
            action="launch_labeling",
            folder=folder,
            title="Collect Training Labels",
        )
        self._active_ai_training_task = object()
        self._current_folder = folder
        self._ai_training_pipeline = None
        self.registered_processes: list[tuple[object, str]] = []
        self.status_messages: list[str] = []
        self.toolbar_updates = 0
        self.closed_progress_dialog = 0

    def _close_ai_training_progress_dialog(self) -> None:
        self.closed_progress_dialog += 1

    def _update_ai_toolbar_state(self) -> None:
        self.toolbar_updates += 1

    def _register_child_process(self, process, *, name: str) -> None:
        self.registered_processes.append((process, name))

    def statusBar(self):
        return self

    def showMessage(self, message: str) -> None:
        self.status_messages.append(message)


class _VerticalScrollBarStub:
    def value(self) -> int:
        return 0


class _GridMetadataStub:
    def verticalScrollBar(self) -> _VerticalScrollBarStub:
        return _VerticalScrollBarStub()


class _TimerStopStub:
    def __init__(self) -> None:
        self.stopped = False

    def stop(self) -> None:
        self.stopped = True


class _FilterMetadataManagerStub:
    def __init__(self) -> None:
        self.calls = 0

    def get_cached(self, record: ImageRecord):
        self.calls += 1
        return None


class _FilterMetadataResetStub:
    FILTER_METADATA_EAGER_CACHE_MAX_RECORDS = MainWindow.FILTER_METADATA_EAGER_CACHE_MAX_RECORDS

    def __init__(self) -> None:
        self.grid = _GridMetadataStub()
        self._filter_metadata_manager = _FilterMetadataManagerStub()
        self._metadata_scroll_prefetch_timer = _TimerStopStub()
        self._metadata_request_timer = _TimerStopStub()
        self.enqueued: list[tuple[list[str], bool]] = []

    def _metadata_prefetch_seed_paths(self) -> list[str]:
        return ["seed-path"]

    def _enqueue_filter_metadata_paths(self, paths, *, front: bool = False) -> None:
        self.enqueued.append((list(paths), front))


class _ChunkDecisionStub:
    CHUNKED_RESTORE_LOAD_MIN_RECORDS = MainWindow.CHUNKED_RESTORE_LOAD_MIN_RECORDS

    def __init__(self) -> None:
        self._chunked_load_scan_tokens: set[int] = set()


class _WorkflowInsightCacheStub:
    def __init__(self, records: list[ImageRecord]) -> None:
        self._all_records = records
        self._annotations = {}
        self._burst_recommendations = {}
        self._workflow_insights_by_path = {}
        self._all_records_by_path = {record.path: record for record in records}
        self._taste_profile = TasteProfile()

    def _ai_result_for_record(self, record: ImageRecord):
        return None

    def _burst_recommendation_for_record(self, record: ImageRecord | None):
        return MainWindow._burst_recommendation_for_record(self, record)


class WindowCatalogCacheTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        _ensure_app()

    def test_ai_training_action_availability_allows_general_training_without_local_labels(self) -> None:
        availability = _build_ai_training_action_availability(
            local_pairwise_labels=0,
            local_cluster_labels=0,
            local_prepared_ready=False,
            general_pairwise_labels=12,
            general_cluster_labels=4,
            trained_checkpoint_available=True,
            active_profile_key="general",
        )

        self.assertFalse(availability.local_has_labels)
        self.assertTrue(availability.general_has_labels)
        self.assertTrue(availability.can_run_full_pipeline)
        self.assertTrue(availability.can_train)
        self.assertTrue(availability.can_evaluate)

    def test_ai_training_action_availability_requires_local_labels_for_specialist_eval(self) -> None:
        availability = _build_ai_training_action_availability(
            local_pairwise_labels=0,
            local_cluster_labels=0,
            local_prepared_ready=True,
            general_pairwise_labels=18,
            general_cluster_labels=6,
            trained_checkpoint_available=True,
            active_profile_key="portrait",
        )

        self.assertTrue(availability.general_has_labels)
        self.assertTrue(availability.can_train)
        self.assertTrue(availability.can_run_full_pipeline)
        self.assertFalse(availability.can_evaluate)

    def test_restore_ai_results_skips_saved_report_from_different_folder(self) -> None:
        window = _WindowAiRestoreStub(
            folder=r"\\192.168.1.200\ColossalBoi\Photography\China '26\Raw Files",
            saved_path=r"K:\Photography\Canada 10-25\.image_triage_ai\ranker_report",
        )

        restored = MainWindow._restore_ai_results(window, force=True)

        self.assertFalse(restored)
        self.assertEqual([], window.load_ai_calls)
        self.assertEqual(0, window.refresh_calls)
        self.assertEqual(0, window.toolbar_updates)
        self.assertIn(window.AI_RESULTS_KEY, window._settings.values)

    def test_saved_ai_results_scope_allows_current_hidden_report(self) -> None:
        window = _WindowAiRestoreStub(
            folder=r"K:\Photography\Canada 10-25",
            saved_path=r"K:\Photography\Canada 10-25\.image_triage_ai\ranker_report",
        )

        self.assertTrue(
            MainWindow._saved_ai_results_belong_to_current_folder(
                window,
                window._settings.value(window.AI_RESULTS_KEY, "", str),
            )
        )

    def test_handle_ai_training_finished_registers_launched_labeling_process(self) -> None:
        folder = "X:/Shots"
        process = SimpleNamespace(pid=3210)
        window = _LabelLaunchFinishStub(folder)

        MainWindow._handle_ai_training_finished(
            window,
            {
                "process": process,
                "pid": 3210,
                "ready_acknowledged": True,
            },
        )

        self.assertIsNone(window._ai_training_context)
        self.assertIsNone(window._active_ai_training_task)
        self.assertEqual(1, window.closed_progress_dialog)
        self.assertEqual(1, window.toolbar_updates)
        self.assertEqual([(process, "AI Label Collection")], window.registered_processes)
        self.assertEqual(["Opened training label collection for the current folder."], window.status_messages)

    def test_load_cached_folder_records_uses_catalog_only(self) -> None:
        with tempfile.TemporaryDirectory(prefix="image_triage_window_cache_") as temp_dir:
            db_path = Path(temp_dir) / "catalog.sqlite3"
            folder = str(Path(temp_dir) / "shots")
            records = [
                _record(
                    f"{folder}/cached_01.jpg",
                    name="cached_01.jpg",
                    size=123,
                    modified_ns=1,
                )
            ]
            repository = CatalogRepository(db_path)
            repository.save_folder_records(folder, records)
            window = _WindowCacheStub(repository)

            loaded_records, source = MainWindow._load_cached_folder_records(window, folder)

            self.assertEqual(records, loaded_records)
            self.assertEqual("catalog", source)

    def test_persist_folder_record_cache_updates_catalog(self) -> None:
        with tempfile.TemporaryDirectory(prefix="image_triage_window_cache_") as temp_dir:
            db_path = Path(temp_dir) / "catalog.sqlite3"
            folder = str(Path(temp_dir) / "shots")
            records = [
                _record(
                    f"{folder}/fresh_01.jpg",
                    name="fresh_01.jpg",
                    size=456,
                    modified_ns=2,
                )
            ]
            window = _WindowCacheStub(CatalogRepository(db_path))

            MainWindow._persist_folder_record_cache(window, folder, records, source="test-save")

            self.assertEqual(records, window._catalog_repository.load_folder_records(folder))

    def test_rebuild_current_folder_catalog_cache_bypasses_cached_reads(self) -> None:
        folder = r"X:\Shots\Set A"
        window = _WindowRebuildStub(folder)

        MainWindow._rebuild_current_folder_catalog_cache(window)

        self.assertEqual([(folder, True, True)], window.load_calls)
        self.assertIn("rebuilding catalog cache", window.status_messages[-1].casefold())

    def test_rebuild_current_folder_catalog_cache_requires_real_folder(self) -> None:
        window = _WindowRebuildStub("")

        MainWindow._rebuild_current_folder_catalog_cache(window)

        self.assertEqual([], window.load_calls)
        self.assertIn("open a real folder", window.status_messages[-1].casefold())

    def test_open_launch_target_opens_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            stub = _WindowLaunchStub()
            opened = MainWindow._open_launch_target(stub, temp_dir, chunked_restore=True)
        self.assertTrue(opened)
        self.assertEqual([(temp_dir, False, True, None)], stub.select_calls)

    def test_open_launch_target_opens_parent_folder_and_focuses_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "frame001.nef"
            image_path.write_text("x", encoding="utf-8")
            stub = _WindowLaunchStub()
            opened = MainWindow._open_launch_target(stub, str(image_path), chunked_restore=True)
        self.assertTrue(opened)
        self.assertEqual([(temp_dir, False, True, str(image_path))], stub.select_calls)

    def test_start_scope_enrichment_task_uses_all_records_when_records_argument_is_omitted(self) -> None:
        with tempfile.TemporaryDirectory(prefix="image_triage_window_cache_") as temp_dir:
            db_path = Path(temp_dir) / "catalog.sqlite3"
            records = [
                _record(
                    str(Path(temp_dir) / "frame_01.jpg"),
                    name="frame_01.jpg",
                    size=123,
                    modified_ns=1,
                ),
                _record(
                    str(Path(temp_dir) / "frame_02.jpg"),
                    name="frame_02.jpg",
                    size=456,
                    modified_ns=2,
                ),
            ]
            window = _ScopeStartStub(CatalogRepository(db_path), records)

            MainWindow._start_scope_enrichment_task(window)

            self.assertIsNotNone(window._active_scope_enrichment_task)
            self.assertEqual("building", window._review_scoring_cache_source)
            self.assertIn("2 image bundle(s)", window._review_scoring_cache_detail)
            self.assertEqual(1, window._refresh_calls)

    def test_start_scope_enrichment_defers_while_ai_review_is_running(self) -> None:
        with tempfile.TemporaryDirectory(prefix="image_triage_scope_cache_") as temp_dir:
            db_path = Path(temp_dir) / "catalog.sqlite3"
            records = [
                _record(
                    str(Path(temp_dir) / "frame_01.jpg"),
                    name="frame_01.jpg",
                    size=123,
                    modified_ns=1,
                )
            ]
            window = _ScopeStartStub(CatalogRepository(db_path), records)
            window._active_ai_task = object()

            MainWindow._start_scope_enrichment_task(window)

            self.assertIsNone(window._active_scope_enrichment_task)
            self.assertTrue(window._ai_deferred_background_work)
            self.assertEqual("catalog:root", window._ai_deferred_background_scope_key)
            self.assertEqual("deferred", window._review_scoring_cache_source)
            self.assertEqual(1, window._refresh_calls)

    def test_should_chunk_loaded_records_for_large_folder_without_restore_token(self) -> None:
        window = _ChunkDecisionStub()
        records = [
            _record(
                f"X:/Shots/frame_{index:04d}.jpg",
                name=f"frame_{index:04d}.jpg",
                size=100 + index,
                modified_ns=index + 1,
            )
            for index in range(MainWindow.CHUNKED_RESTORE_LOAD_MIN_RECORDS)
        ]

        should_chunk = MainWindow._should_chunk_loaded_records(window, records)

        self.assertTrue(should_chunk)

    def test_reset_filter_metadata_index_skips_eager_cache_probe_for_large_loads(self) -> None:
        window = _FilterMetadataResetStub()
        records = [
            _record(
                f"X:/Shots/frame_{index:04d}.jpg",
                name=f"frame_{index:04d}.jpg",
                size=100 + index,
                modified_ns=index + 1,
            )
            for index in range(MainWindow.FILTER_METADATA_EAGER_CACHE_MAX_RECORDS + 1)
        ]

        MainWindow._reset_filter_metadata_index(window, records)

        self.assertEqual(0, window._filter_metadata_manager.calls)
        self.assertEqual([(["seed-path"], True)], window.enqueued)

    def test_reset_filter_metadata_index_still_checks_small_load_cache(self) -> None:
        window = _FilterMetadataResetStub()
        records = [
            _record(
                f"X:/Shots/frame_{index:04d}.jpg",
                name=f"frame_{index:04d}.jpg",
                size=100 + index,
                modified_ns=index + 1,
            )
            for index in range(4)
        ]

        MainWindow._reset_filter_metadata_index(window, records)

        self.assertEqual(len(records), window._filter_metadata_manager.calls)

    def test_refresh_workflow_insights_cache_avoids_expensive_normalizer_on_loaded_records(self) -> None:
        records = [
            _record(
                r"\\192.168.1.200\Photos\Set A\frame_0001.nef",
                name="frame_0001.nef",
                size=123,
                modified_ns=1,
            ),
            _record(
                r"\\192.168.1.200\Photos\Set A\frame_0002.nef",
                name="frame_0002.nef",
                size=456,
                modified_ns=2,
            ),
        ]
        window = _WorkflowInsightCacheStub(records)

        with patch("image_triage.window.normalized_path_key", side_effect=AssertionError("workflow cache should not resolve paths")):
            MainWindow._refresh_workflow_insights_cache(window, force_full=True)

        for record in records:
            self.assertIn(record.path, window._workflow_insights_by_path)

    def test_load_ai_results_uses_catalog_cache_before_reparsing_export(self) -> None:
        with tempfile.TemporaryDirectory(prefix="image_triage_ai_cache_") as temp_dir:
            db_path = Path(temp_dir) / "catalog.sqlite3"
            repository = CatalogRepository(db_path)
            folder = str(Path(temp_dir) / "shots")
            record = _record(
                f"{folder}/frame_01.jpg",
                name="frame_01.jpg",
                size=123,
                modified_ns=1,
            )
            repository.save_folder_records(folder, [record])
            report_dir = Path(temp_dir) / "report"
            report_dir.mkdir(parents=True, exist_ok=True)
            export_csv_path = report_dir / "ranked_clusters_export.csv"
            export_csv_path.write_text(
                "file_path,file_name,cluster_id,cluster_size,rank_in_cluster,score\n"
                f"{record.path},{record.name},group-a,1,1,0.95\n",
                encoding="utf-8",
            )
            source = inspect_ai_bundle_source(report_dir)
            bundle = build_ai_bundle_from_results(
                source_path=source.source_path,
                export_csv_path=source.export_csv_path,
                summary_json_path=source.summary_json_path,
                report_html_path=source.report_html_path,
                results=[
                    AIImageResult(
                        image_id="frame_01",
                        file_path=record.path,
                        file_name=record.name,
                        group_id="group-a",
                        group_size=1,
                        rank_in_group=1,
                        score=0.95,
                        confidence_bucket=AIConfidenceBucket.LIKELY_KEEPER,
                        confidence_summary="High single-image score compared with the rest of the folder.",
                    )
                ],
                summary={"model": "cached"},
            )
            repository.save_ai_bundle(folder, cache_key=source.cache_key, bundle=bundle)
            window = _WindowAiLoadStub(repository, folder, [record])

            with patch("image_triage.window.load_ai_bundle", side_effect=AssertionError("expected catalog AI cache reuse")):
                loaded = MainWindow._load_ai_results(window, report_dir, show_message=False)

            self.assertTrue(loaded)
            self.assertIsNotNone(window._ai_bundle)
            assert window._ai_bundle is not None
            self.assertEqual(bundle.result_for_path(record.path), window._ai_bundle.result_for_path(record.path))
            self.assertEqual(source.source_path, window._settings.values[window.AI_RESULTS_KEY])
            self.assertEqual(1, window.refresh_calls)

    def test_run_ai_pipeline_reuses_cached_hidden_report_when_inputs_match(self) -> None:
        with tempfile.TemporaryDirectory(prefix="image_triage_ai_cache_") as temp_dir:
            db_path = Path(temp_dir) / "catalog.sqlite3"
            repository = CatalogRepository(db_path)
            folder = str(Path(temp_dir) / "shots")
            record = _record(f"{folder}/frame_01.jpg", name="frame_01.jpg", size=123, modified_ns=1)
            repository.save_folder_records(folder, [record])
            repository.save_ai_workflow_cache(
                folder,
                embedding_cache_key="embed-1",
                cluster_cache_key="cluster-1",
                report_cache_key="report-1",
                artifacts_dir=str(Path(folder) / ".image_triage_ai" / "artifacts"),
                report_dir=str(Path(folder) / ".image_triage_ai" / "ranker_report"),
            )
            window = _WindowAiRunStub(repository, folder, [record], load_hidden_result=True)

            with patch(
                "image_triage.window.build_ai_stage_cache_keys",
                return_value=SimpleNamespace(
                    embedding_cache_key="embed-1",
                    cluster_cache_key="cluster-1",
                    report_cache_key="report-1",
                    semantic_cache_key="semantic-1",
                ),
            ), patch("image_triage.window.ai_report_artifacts_ready", return_value=True), patch(
                "image_triage.window.AIRunTask",
                side_effect=AssertionError("expected cached hidden AI report reuse"),
            ):
                MainWindow._run_ai_pipeline(window)

            self.assertEqual(1, window.load_hidden_calls)
            self.assertIsNone(window._ai_run_pool.started_task)
            self.assertEqual(0, window.defer_background_calls)
            self.assertEqual(1, window.mode_tabs.index)
            self.assertIn("reused cached ai review results", window.status_messages[-1].casefold())

    def test_run_ai_pipeline_skips_extract_and_cluster_when_cluster_cache_matches(self) -> None:
        with tempfile.TemporaryDirectory(prefix="image_triage_ai_cache_") as temp_dir:
            db_path = Path(temp_dir) / "catalog.sqlite3"
            repository = CatalogRepository(db_path)
            folder = str(Path(temp_dir) / "shots")
            record = _record(f"{folder}/frame_01.jpg", name="frame_01.jpg", size=123, modified_ns=1)
            repository.save_folder_records(folder, [record])
            repository.save_ai_workflow_cache(
                folder,
                embedding_cache_key="embed-1",
                cluster_cache_key="cluster-1",
                report_cache_key="report-old",
                artifacts_dir=str(Path(folder) / ".image_triage_ai" / "artifacts"),
                report_dir=str(Path(folder) / ".image_triage_ai" / "ranker_report"),
            )
            window = _WindowAiRunStub(repository, folder, [record], load_hidden_result=False)
            _AIRunTaskCapture.instances.clear()

            with patch(
                "image_triage.window.build_ai_stage_cache_keys",
                return_value=SimpleNamespace(
                    embedding_cache_key="embed-1",
                    cluster_cache_key="cluster-1",
                    report_cache_key="report-new",
                    semantic_cache_key="semantic-new",
                ),
            ), patch("image_triage.window.ai_report_artifacts_ready", return_value=False), patch(
                "image_triage.window.ai_cluster_artifacts_ready",
                return_value=True,
            ), patch("image_triage.window.AIRunTask", new=_AIRunTaskCapture):
                MainWindow._run_ai_pipeline(window)

            self.assertEqual(1, len(_AIRunTaskCapture.instances))
            task = _AIRunTaskCapture.instances[0]
            self.assertTrue(task.kwargs["skip_extract"])
            self.assertTrue(task.kwargs["skip_cluster"])
            self.assertIs(task, window._ai_run_pool.started_task)
            self.assertEqual(1, window.defer_background_calls)
            self.assertIn("cached embeddings and clusters", window.status_messages[-1].casefold())

    def test_handle_ai_run_finished_persists_ai_workflow_cache(self) -> None:
        with tempfile.TemporaryDirectory(prefix="image_triage_ai_cache_") as temp_dir:
            db_path = Path(temp_dir) / "catalog.sqlite3"
            repository = CatalogRepository(db_path)
            folder_path = Path(temp_dir) / "shots"
            folder = str(folder_path)
            record = _record(f"{folder}/frame_01.jpg", name="frame_01.jpg", size=123, modified_ns=1)
            repository.save_folder_records(folder, [record])
            window = _WindowAiRunFinishedStub(repository, folder)
            paths = Path(folder) / ".image_triage_ai" / "ranker_report"

            MainWindow._handle_ai_run_finished(window, folder, str(paths), str(paths / "ranked_clusters_report.html"))

            cached = repository.load_ai_workflow_cache(folder)
            self.assertIsNotNone(cached)
            assert cached is not None
            self.assertEqual("embed-finished", cached.embedding_cache_key)
            self.assertEqual("cluster-finished", cached.cluster_cache_key)
            self.assertEqual("report-finished", cached.report_cache_key)
            self.assertEqual([(str(paths), False)], window.load_ai_calls)
            self.assertEqual(1, len(window.completion_dialog_calls))
            self.assertTrue(window.completion_dialog_calls[0]["same_folder"])
            self.assertEqual(str(paths), window.completion_dialog_calls[0]["report_dir"])
            self.assertEqual(1, window.mode_tabs.index)
            self.assertEqual(1, window.resume_background_calls)

    def test_ai_review_complete_dialog_starts_with_collapsed_outputs_and_two_column_legend(self) -> None:
        dialog = AIReviewCompleteDialog(
            folder=r"K:\Photography\Canada 10-25\AiTest",
            hidden_root=r"K:\Photography\Canada 10-25\AiTest\.image_triage_ai",
            artifacts_dir=r"K:\Photography\Canada 10-25\AiTest\.image_triage_ai\artifacts",
            report_dir=r"K:\Photography\Canada 10-25\AiTest\.image_triage_ai\ranker_report",
            export_csv_path=r"K:\Photography\Canada 10-25\AiTest\.image_triage_ai\ranker_report\ranked_clusters_export.csv",
            report_html_path=r"K:\Photography\Canada 10-25\AiTest\.image_triage_ai\ranker_report\ranked_clusters_report.html",
            same_folder=True,
        )

        self.assertFalse(dialog.outputs_toggle.isChecked())
        self.assertFalse(dialog.outputs_body.isVisible())

        output_entries = dialog.outputs_body.findChildren(QFrame, "aiOutputEntry")
        self.assertEqual(5, len(output_entries))

        legend_host = dialog.findChild(QWidget, "aiReviewLegendHost")
        self.assertIsNotNone(legend_host)
        assert legend_host is not None
        legend_layout = legend_host.layout()
        self.assertIsInstance(legend_layout, QGridLayout)
        assert isinstance(legend_layout, QGridLayout)

        legend_entries = legend_host.findChildren(QFrame, "aiLegendEntry")
        self.assertEqual(len(ai_review_tag_definitions()), len(legend_entries))
        columns = {
            legend_layout.getItemPosition(legend_layout.indexOf(entry))[1]
            for entry in legend_entries
            if legend_layout.indexOf(entry) >= 0
        }
        self.assertEqual({0, 1}, columns)

    def test_show_last_ai_review_summary_uses_cached_payload(self) -> None:
        window = _WindowAiSummaryStub()
        window._last_ai_review_summary = {
            "folder": r"K:\Photography\Canada 10-25\AiTest",
            "report_dir": r"K:\Photography\Canada 10-25\AiTest\.image_triage_ai\ranker_report",
            "html_report_path": r"K:\Photography\Canada 10-25\AiTest\.image_triage_ai\ranker_report\ranked_clusters_report.html",
            "same_folder": True,
            "bundle": None,
        }

        MainWindow._show_last_ai_review_summary(window)

        self.assertEqual(1, len(window.summary_calls))
        self.assertEqual(window._last_ai_review_summary["report_dir"], window.summary_calls[0]["report_dir"])

    def test_show_last_ai_review_summary_rebuilds_payload_from_loaded_bundle(self) -> None:
        window = _WindowAiSummaryStub()
        folder = r"K:\Photography\Canada 10-25\AiTest"
        report_dir = rf"{folder}\.image_triage_ai\ranker_report"
        export_csv_path = rf"{report_dir}\ranked_clusters_export.csv"
        report_html_path = rf"{report_dir}\ranked_clusters_report.html"
        window._current_folder = folder
        window._ai_bundle = build_ai_bundle_from_results(
            source_path=report_dir,
            export_csv_path=export_csv_path,
            report_html_path=report_html_path,
            summary_json_path=rf"{report_dir}\ranked_clusters_summary.json",
            results=[],
            summary={"model": "cached"},
        )

        MainWindow._show_last_ai_review_summary(window)

        self.assertEqual(1, len(window.summary_calls))
        self.assertEqual(folder, window.summary_calls[0]["folder"])
        self.assertEqual(report_dir, window.summary_calls[0]["report_dir"])
        self.assertEqual(report_html_path, window.summary_calls[0]["html_report_path"])
        self.assertIs(window._ai_bundle, window.summary_calls[0]["bundle"])

    def test_directory_suggestion_controller_lists_only_child_directories(self) -> None:
        with tempfile.TemporaryDirectory(prefix="image_triage_path_suggest_") as temp_dir:
            root = Path(temp_dir)
            (root / "Alpha").mkdir()
            (root / "Beta").mkdir()
            (root / ".Hidden").mkdir()
            (root / "notes.txt").write_text("x", encoding="utf-8")

            suggestions = _DirectorySuggestionController._list_directory_suggestions(f"{root}{os.sep}")

            self.assertEqual(["Alpha", "Beta"], [name for name, _path in suggestions])

    def test_directory_suggestion_controller_filters_the_last_path_segment(self) -> None:
        with tempfile.TemporaryDirectory(prefix="image_triage_path_filter_") as temp_dir:
            root = Path(temp_dir)
            (root / "Canada 10-25").mkdir()
            (root / "Canada 11-02").mkdir()
            (root / "Japan '23").mkdir()

            suggestions = _DirectorySuggestionController._list_directory_suggestions(
                str(root / "Can")
            )

            self.assertEqual(
                ["Canada 10-25", "Canada 11-02"],
                [name for name, _path in suggestions],
            )

    def test_directory_suggestion_controller_accepts_to_navigation_callback(self) -> None:
        combo = QComboBox()
        combo.setEditable(True)
        accepted_paths: list[str] = []
        controller = _DirectorySuggestionController(combo, on_accept_path=accepted_paths.append)
        item = QListWidgetItem("Canada 10-25")
        item.setData(Qt.ItemDataRole.UserRole, r"K:\Photography\Canada 10-25")

        controller._accept_item(item)

        self.assertEqual([r"K:\Photography\Canada 10-25"], accepted_paths)

    def test_directory_suggestion_controller_does_not_double_advance_on_down_arrow(self) -> None:
        combo = QComboBox()
        combo.setEditable(True)
        controller = _DirectorySuggestionController(combo)
        controller._list.addItem(QListWidgetItem("Alaska"))
        controller._list.addItem(QListWidgetItem("Alaska Aug '21"))
        controller._list.addItem(QListWidgetItem("Astrophotos"))
        controller._list.setCurrentRow(0)
        controller._popup.show()
        line_edit = combo.lineEdit()
        assert line_edit is not None

        shortcut_event = QKeyEvent(QEvent.Type.ShortcutOverride, Qt.Key.Key_Down, Qt.KeyboardModifier.NoModifier)
        self.assertTrue(controller.eventFilter(line_edit, shortcut_event))
        self.assertEqual(0, controller._list.currentRow())

        keypress_event = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Down, Qt.KeyboardModifier.NoModifier)
        self.assertTrue(controller.eventFilter(line_edit, keypress_event))
        self.assertEqual(1, controller._list.currentRow())
        controller.hide_popup()

    def test_reset_ai_review_cache_removes_folder_artifacts_and_catalog_entries(self) -> None:
        with tempfile.TemporaryDirectory(prefix="image_triage_ai_reset_") as temp_dir:
            db_path = Path(temp_dir) / "catalog.sqlite3"
            repository = CatalogRepository(db_path)
            folder_path = Path(temp_dir) / "shots"
            folder_path.mkdir()
            folder = str(folder_path)
            artifacts_dir = folder_path / ".image_triage_ai" / "artifacts"
            report_dir = folder_path / ".image_triage_ai" / "ranker_report"
            artifacts_dir.mkdir(parents=True)
            report_dir.mkdir(parents=True)
            (artifacts_dir / "embeddings.npy").write_bytes(b"embed")
            export_path = report_dir / "ranked_clusters_export.csv"
            export_path.write_text("file_path\n", encoding="utf-8")
            report_html = report_dir / "ranked_clusters_report.html"
            report_html.write_text("<html></html>\n", encoding="utf-8")
            repository.save_ai_workflow_cache(
                folder,
                embedding_cache_key="embed-1",
                cluster_cache_key="cluster-1",
                report_cache_key="report-1",
                artifacts_dir=str(artifacts_dir),
                report_dir=str(report_dir),
            )
            bundle = build_ai_bundle_from_results(
                source_path=str(report_dir),
                export_csv_path=str(export_path),
                summary_json_path=str(report_dir / "ranked_clusters_summary.json"),
                report_html_path=str(report_html),
                results=[
                    AIImageResult(
                        image_id="frame_01",
                        file_path=str(folder_path / "frame_01.jpg"),
                        file_name="frame_01.jpg",
                        group_id="group-a",
                        group_size=1,
                        rank_in_group=1,
                        score=0.9,
                        confidence_bucket=AIConfidenceBucket.LIKELY_KEEPER,
                        confidence_summary="test",
                    )
                ],
                summary={"model": "cached"},
            )
            repository.save_ai_bundle(folder, cache_key="report-1", bundle=bundle)
            window = _WindowAiResetStub(repository, folder)

            with patch("image_triage.window.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes):
                MainWindow._reset_ai_review_cache(window)

            self.assertFalse(artifacts_dir.exists())
            self.assertFalse(report_dir.exists())
            self.assertIsNone(repository.load_ai_workflow_cache(folder))
            self.assertIsNone(repository.load_ai_bundle(folder, cache_key="report-1"))
            self.assertIsNone(window._ai_bundle)
            self.assertNotIn(window.AI_RESULTS_KEY, window._settings.values)
            self.assertEqual(1, window.refresh_calls)
            self.assertIn("reset ai review cache", window.status_messages[-1].casefold())

    def test_scope_enrichment_task_uses_cached_review_scoring(self) -> None:
        with tempfile.TemporaryDirectory(prefix="image_triage_scope_cache_") as temp_dir:
            db_path = Path(temp_dir) / "catalog.sqlite3"
            folder = str(Path(temp_dir) / "shots")
            records = (
                _record(f"{folder}/frame_01.jpg", name="frame_01.jpg", size=100, modified_ns=1),
                _record(f"{folder}/frame_02.jpg", name="frame_02.jpg", size=120, modified_ns=2),
            )
            correction_events = [
                {
                    "record_path": records[0].path,
                    "other_path": records[1].path,
                    "group_id": "burst-1",
                    "event_type": "pairwise_choice",
                    "decision": "left_better",
                    "source_mode": "taste_calibration",
                    "ai_bucket": "",
                    "ai_rank_in_group": 0,
                    "ai_group_size": 0,
                    "review_round": "",
                    "payload": {
                        "preferred_detail_score": 91.0,
                        "other_detail_score": 65.0,
                        "preferred_ai_strength": 0.88,
                        "other_ai_strength": 0.42,
                    },
                }
            ]
            repository = CatalogRepository(db_path)
            repository.save_folder_records(folder, list(records))
            cache_key = build_review_scoring_cache_key(
                records,
                ai_bundle=None,
                review_bundle=None,
                correction_events=correction_events,
            )
            taste_profile = TasteProfile(summary_lines=("Cached scoring.",))
            cached_recommendation = BurstRecommendation(
                path=records[0].path,
                group_id="burst-1",
                group_label="Burst",
                group_size=2,
                recommended_path=records[0].path,
                rank_in_group=1,
                score=95.0,
                recommended_score=95.0,
                is_recommended=True,
            )
            repository.save_review_scoring(
                folder,
                session_id="LinkFlow",
                cache_key=cache_key,
                provider_id="default",
                records=records,
                taste_profile=taste_profile,
                recommendations={records[0].path: cached_recommendation},
            )

            task = ScopeEnrichmentTask(
                scope_key=folder,
                token=5,
                session_id="LinkFlow",
                folder_path=folder,
                catalog_db_path=db_path,
                include_all_scope_events=False,
                records=records,
                ai_bundle=None,
                review_bundle=None,
            )
            finished_payloads: list[tuple[object, object, object]] = []
            cache_status_payloads: list[dict[str, object]] = []
            task.signals.finished.connect(
                lambda _scope_key, _token, corrections, taste, recommendations: finished_payloads.append(
                    (corrections, taste, recommendations)
                )
            )
            task.signals.cache_status.connect(lambda _scope_key, _token, payload: cache_status_payloads.append(payload))

            with patch("image_triage.window.DecisionStore.load_correction_events", return_value=correction_events), patch(
                "image_triage.window.build_burst_recommendations",
                side_effect=AssertionError("expected cached review scoring"),
            ):
                task.run()

            self.assertEqual(1, len(finished_payloads))
            _, loaded_taste_profile, loaded_recommendations = finished_payloads[0]
            self.assertEqual(taste_profile, loaded_taste_profile)
            self.assertEqual(cached_recommendation, loaded_recommendations[records[0].path])
            self.assertEqual("catalog", cache_status_payloads[0]["source"])

    def test_scope_enrichment_task_persists_review_scoring_after_compute(self) -> None:
        with tempfile.TemporaryDirectory(prefix="image_triage_scope_cache_") as temp_dir:
            db_path = Path(temp_dir) / "catalog.sqlite3"
            folder = str(Path(temp_dir) / "shots")
            records = (
                _record(f"{folder}/frame_01.jpg", name="frame_01.jpg", size=100, modified_ns=1),
                _record(f"{folder}/frame_02.jpg", name="frame_02.jpg", size=120, modified_ns=2),
            )
            correction_events = []
            repository = CatalogRepository(db_path)
            repository.save_folder_records(folder, list(records))
            computed_taste_profile = TasteProfile(summary_lines=("Computed scoring.",))
            computed_recommendation = BurstRecommendation(
                path=records[0].path,
                group_id="burst-1",
                group_label="Burst",
                group_size=2,
                recommended_path=records[0].path,
                rank_in_group=1,
                score=93.0,
                recommended_score=93.0,
                is_recommended=True,
            )

            task = ScopeEnrichmentTask(
                scope_key=folder,
                token=6,
                session_id="LinkFlow",
                folder_path=folder,
                catalog_db_path=db_path,
                include_all_scope_events=False,
                records=records,
                ai_bundle=None,
                review_bundle=None,
            )
            cache_status_payloads: list[dict[str, object]] = []
            task.signals.cache_status.connect(lambda _scope_key, _token, payload: cache_status_payloads.append(payload))

            with patch("image_triage.window.DecisionStore.load_correction_events", return_value=correction_events), patch(
                "image_triage.window.build_burst_recommendations",
                return_value=(computed_taste_profile, {records[0].path: computed_recommendation}),
            ):
                task.run()

            cache_key = build_review_scoring_cache_key(
                records,
                ai_bundle=None,
                review_bundle=None,
                correction_events=correction_events,
            )
            loaded = repository.load_review_scoring(folder, session_id="LinkFlow", cache_key=cache_key)

            self.assertIsNotNone(loaded)
            assert loaded is not None
            self.assertEqual(computed_taste_profile, loaded.taste_profile)
            self.assertEqual(computed_recommendation, loaded.recommendations[records[0].path])
            self.assertEqual("live", cache_status_payloads[0]["source"])

    def test_apply_startup_window_state_fixup_forces_real_windows_maximize(self) -> None:
        stub = _WindowStateFixupStub(startup_state="maximized", maximized=True)

        MainWindow._apply_startup_window_state_fixup(stub)

        self.assertEqual(["normal", "maximized"], stub.calls)
        self.assertTrue(stub.isMaximized())

    def test_apply_startup_window_state_fixup_maximizes_when_not_currently_maximized(self) -> None:
        stub = _WindowStateFixupStub(startup_state="maximized", maximized=False)

        MainWindow._apply_startup_window_state_fixup(stub)

        self.assertEqual(["maximized"], stub.calls)
        self.assertTrue(stub.isMaximized())


if __name__ == "__main__":
    unittest.main()
