from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from image_triage.ai_results import AIConfidenceBucket, AIImageResult, build_ai_bundle_from_results, inspect_ai_bundle_source
from image_triage.catalog import CatalogRepository
from image_triage.models import ImageRecord
from image_triage.review_workflows import BurstRecommendation, TasteProfile, build_review_scoring_cache_key
from image_triage.window import AITrainingExecutionContext, MainWindow, ScopeEnrichmentTask, _build_ai_training_action_availability


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


class _ScopeStartStub:
    def __init__(self, repository: CatalogRepository, records: list[ImageRecord]) -> None:
        self._all_records = records
        self._catalog_repository = repository
        self._session_id = "LinkFlow"
        self._current_folder = ""
        self._scope_kind = "catalog"
        self._ai_bundle = None
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


class _SettingsStub:
    def __init__(self) -> None:
        self.values: dict[str, object] = {}

    def setValue(self, key: str, value: object) -> None:
        self.values[key] = value


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
        self._ai_runtime = object()
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

    def _ensure_ai_model_available(self, *, title: str) -> bool:
        return True

    def _ai_training_paths_for_folder(self, folder: str | None = None):
        return None

    def _current_trained_checkpoint_path(self):
        return None

    def _load_hidden_ai_results_for_current_folder(self, *, show_message: bool = True) -> bool:
        self.load_hidden_calls += 1
        return self.load_hidden_result

    def _update_ai_toolbar_state(self) -> None:
        self.toolbar_updates += 1

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
        self._ai_stage_index = 0
        self._ai_stage_total = 3
        self._ai_stage_message = ""
        self._ai_progress_current = 0
        self._ai_progress_total = 0
        self._ai_progress_eta_text = ""
        self.mode_tabs = _ModeTabsStub()
        self.load_ai_calls: list[tuple[str, bool]] = []
        self.status_messages: list[str] = []
        self.toolbar_updates = 0

    def _load_ai_results(self, report_dir: str, *, show_message: bool = True) -> bool:
        self.load_ai_calls.append((report_dir, show_message))
        return True

    def _update_ai_toolbar_state(self) -> None:
        self.toolbar_updates += 1

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
                ),
            ), patch("image_triage.window.ai_report_artifacts_ready", return_value=True), patch(
                "image_triage.window.AIRunTask",
                side_effect=AssertionError("expected cached hidden AI report reuse"),
            ):
                MainWindow._run_ai_pipeline(window)

            self.assertEqual(1, window.load_hidden_calls)
            self.assertIsNone(window._ai_run_pool.started_task)
            self.assertEqual(1, window.mode_tabs.index)
            self.assertIn("reused cached ai culling results", window.status_messages[-1].casefold())

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
            self.assertEqual(1, window.mode_tabs.index)

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


if __name__ == "__main__":
    unittest.main()
