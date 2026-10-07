"""Running AI culling and reading its results: the pipeline and rerank runs and their progress and summary, loading and restoring results for a folder, applying the AI's decisions, and the toolbar/action state that follows from them. Extracted from MainWindow (docs/mainwindow_decomposition_plan.md, DC-4.2)."""
from __future__ import annotations

import os
import shutil
import time
import uuid

from PySide6.QtCore import QDir, QObject, QSignalBlocker, QThreadPool, Qt
from PySide6.QtWidgets import QCheckBox, QDialog, QDialogButtonBox, QFileDialog, QLabel, QMessageBox, QPushButton, QVBoxLayout
from collections import Counter
from pathlib import Path
from textwrap import dedent

from .ai_results import AIBundle, AICullBucket, AIConfidenceBucket, ai_cull_bucket_for_result, ai_manual_cull_sort_key, ai_review_tag_definitions, find_ai_result_for_record, inspect_ai_bundle_source, iter_ai_bundle_results, load_ai_bundle, refine_ai_result_with_review_insight
from .ai_runtime_packages import ai_runtime_variant_label
from .ai_workflow import build_ai_workflow_paths, existing_hidden_ai_report_dir, reset_hidden_ai_review_cache
from .aiculler_workflow import AICullerRunTask, aiculler_db_path, aiculler_rerank_readiness, aiculler_runtime_available, build_aiculler_workflow_paths
from .filtering import AIStateFilter
from .models import FilterMode, ImageRecord, SessionAnnotation
from .perf import perf_logger
from .phash_prefilter import build_phash_prefilter_paths
from .preview import PreviewEntry
from .records_view_cache import ViewInvalidationReason
from .records_view_controller import _memory_path_key
from .scanner import normalized_path_key
from .shell_actions import open_with_default
from .tasks.ai_tasks import HiddenAIResultsLoadTask, PostAIRunBundleLoadTask, _AIFolderProbeTask, _compute_ai_folder_probe, _unknown_ai_folder_probe
from .ui import AIReviewProgressDialog, ApplyAIDecisionsDialog, GuidedAICullPreferencesDialog, GuidedCullPreferences
from .ui.ai_review_dialogs import AIReviewCompleteDialog

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .window import MainWindow


class AiRunController(QObject):
    """Running AI culling and reading its results: the pipeline and rerank runs and their progress and summary, loading and restoring results for a folder, applying the AI's decisions, and the toolbar/action state that follows from them. Extracted from MainWindow (docs/mainwindow_decomposition_plan.md, DC-4.2)."""

    def __init__(self, window: "MainWindow") -> None:
        super().__init__(window)
        self._window = window
        self._ai_folder_probe_cache: dict | None = None
        self._active_ai_run_start_perf = 0.0
        self._active_hidden_ai_results_task: HiddenAIResultsLoadTask | None = None
        self._ai_demoted_burst_paths: set[str] = set()
        self._ai_probe_generation = 0
        self._ai_probe_task: _AIFolderProbeTask | None = None
        self._ai_review_progress_dialog: AIReviewProgressDialog | None = None
        self._ai_status_terminal_notice_key = ""
        self._hidden_ai_results_token = 0

    def confirm_cpu_clip_run(self, runtime) -> bool:
        if runtime.device != "cpu":
            return True
        choice = QMessageBox.warning(
            self._window,
            "CPU AI Culling",
            "GPU acceleration is unavailable, so CLIP will run on the CPU.\n\n"
            "Large folders can take tens of minutes. Image Triage will still use FP32 first "
            "and retry FP16 automatically if FP32 cannot initialize.\n\n"
            "Continue with CPU inference?",
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        return choice == QMessageBox.StandardButton.Ok

    def ai_clip_model_variant_label(self) -> str:
        return "Automatic (FP32 to FP16)"

    def open_guided_ai_cull_preferences(self) -> None:
        dialog = getattr(self, "_guided_ai_cull_preferences_dialog", None)
        if dialog is not None and dialog.isVisible():
            dialog.raise_()
            dialog.activateWindow()
            return

        image_count = sum(1 for record in self._window._all_records if not record.is_folder)
        dialog = GuidedAICullPreferencesDialog(
            folder_name=self._window._projects.scope_display_label(),
            image_count=image_count,
            keep_top_percent=self._window._ai_keep_top_percent_setting,
            review_band_percent=self._window._ai_review_band_percent_setting,
            phash_prefilter_settings=self._window._phash_prefilter_settings,
            parent=self._window,
        )
        self._guided_ai_cull_preferences_dialog = dialog
        dialog.workflow_button.clicked.connect(self._window._handoff.open_ai_workflow_center)
        dialog.accepted.connect(lambda d=dialog: self.handle_guided_ai_cull_preferences_accepted(d))
        dialog.finished.connect(lambda _code, d=dialog: self.clear_guided_ai_cull_preferences_dialog(d))
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def clear_guided_ai_cull_preferences_dialog(self, dialog: GuidedAICullPreferencesDialog) -> None:
        if getattr(self, "_guided_ai_cull_preferences_dialog", None) is dialog:
            self._guided_ai_cull_preferences_dialog = None

    def handle_guided_ai_cull_preferences_accepted(self, dialog: GuidedAICullPreferencesDialog) -> None:
        self.apply_guided_ai_cull_preferences(dialog.result_preferences())
        self.run_ai_pipeline()

    def apply_guided_ai_cull_preferences(self, preferences: GuidedCullPreferences) -> None:
        new_keep_top = self._window._normalize_ai_keep_top_percent(preferences.keep_top_percent)
        new_review_band = self._window._normalize_ai_review_band_percent(preferences.review_band_percent)
        if (
            new_keep_top != self._window._ai_keep_top_percent_setting
            or new_review_band != self._window._ai_review_band_percent_setting
        ):
            self._window._ai_keep_top_percent_setting = new_keep_top
            self._window._ai_review_band_percent_setting = new_review_band
            self._window._settings_ctl.apply_cull_thresholds_to_classifier()

        self._window._phash_prefilter_settings = preferences.phash_prefilter_settings.normalized()

        self._window._settings.setValue(self._window.AI_KEEP_TOP_PERCENT_KEY, self._window._ai_keep_top_percent_setting)
        self._window._settings.setValue(self._window.AI_REVIEW_BAND_PERCENT_KEY, self._window._ai_review_band_percent_setting)
        self._window._settings_ctl.save_phash_prefilter_settings(self._window._phash_prefilter_settings)
        self.update_ai_toolbar_state()
        self._window.statusBar().showMessage("Guided AI Cull preferences saved.")

    def refresh_ai_workflow_center(self) -> None:
        dialog = getattr(self._window._handoff, "_ai_workflow_center_dialog", None)
        if dialog is not None and dialog.isVisible():
            dialog.refresh()

    def mark_background_review_work_deferred_for_ai(self, *, reason: str) -> None:
        if not self._window._all_records:
            return
        self._window._ai_deferred_background_work = True
        self._window._ai_deferred_background_scope_key = self._window._projects.current_scope_key()
        logger = perf_logger()
        if logger.enabled:
            logger.log(
                "ai.background_start_deferred",
                reason=reason,
                scope=self._window._ai_deferred_background_scope_key,
                records=len(self._window._all_records),
            )

    def defer_background_review_work_for_ai(self, *, reason: str) -> None:
        if not self._window._all_records:
            return
        logger = perf_logger()
        active_scope = self._window._active_scope_enrichment_task is not None
        active_annotations = self._window._active_annotation_hydration_task is not None
        active_review = self._window._active_review_intelligence_task is not None
        self._window._ai_deferred_background_work = True
        self._window._ai_deferred_background_scope_key = self._window._projects.current_scope_key()
        self._window._scope_enrichment_token += 1
        self._window._scan.cancel_scope_enrichment_task()
        self._window._annotation_hydration_token += 1
        if self._window._active_annotation_hydration_task is not None:
            self._window._active_annotation_hydration_task.cancel()
        self._window._active_annotation_hydration_task = None
        self._window._annotation_hydration_dirty_paths.clear()
        self._window._annotation_hydration_pending_clear_paths.clear()
        self._window._annotation_reapply_timer.stop()
        self._window._review_intelligence_token += 1
        if self._window._active_review_intelligence_task is not None:
            self._window._active_review_intelligence_task.cancel()
            self._window._active_review_intelligence_task = None
        self._window._review_intelligence = None
        self._window._review_chunk_flush_timer.stop()
        self._window._review_chunk_dirty_paths.clear()
        self._window._review_scoring_cache_source = "deferred"
        self._window._review_scoring_cache_detail = "Workflow scoring is deferred while AI review runs."
        self._window._review_grouping_cache_source = "deferred"
        self._window._review_grouping_cache_detail = "Smart groups are deferred while AI review runs."
        self._window._review_feature_cache_source = "deferred"
        self._window._review_feature_cache_detail = "Review feature analysis is deferred while AI review runs."
        self._window._scan.refresh_catalog_status_indicator()
        if logger.enabled:
            logger.log(
                "ai.background_deferred",
                reason=reason,
                scope=self._window._ai_deferred_background_scope_key,
                records=len(self._window._all_records),
                active_scope=active_scope,
                active_annotations=active_annotations,
                active_review=active_review,
            )

    def resume_deferred_background_review_work_after_ai(self, *, reason: str) -> None:
        if not self._window._ai_deferred_background_work:
            return
        deferred_scope_key = self._window._ai_deferred_background_scope_key
        self._window._ai_deferred_background_work = False
        self._window._ai_deferred_background_scope_key = ""
        logger = perf_logger()
        current_scope_key = self._window._projects.current_scope_key()
        if self._window._active_ai_task is not None or deferred_scope_key != current_scope_key or not self._window._all_records:
            self._window._review_scoring_cache_source = "idle"
            self._window._review_scoring_cache_detail = "Ready"
            self._window._review_grouping_cache_source = "idle"
            self._window._review_grouping_cache_detail = "Ready"
            self._window._review_feature_cache_source = "idle"
            self._window._review_feature_cache_detail = "Ready"
            self._window._scan.refresh_catalog_status_indicator()
            if logger.enabled:
                logger.log(
                    "ai.background_resume_skipped",
                    reason=reason,
                    deferred_scope=deferred_scope_key,
                    current_scope=current_scope_key,
                    active_ai=self._window._active_ai_task is not None,
                    records=len(self._window._all_records),
                )
            return
        records = list(self._window._all_records)
        if logger.enabled:
            logger.log(
                "ai.background_resumed",
                reason=reason,
                scope=current_scope_key,
                records=len(records),
            )
        self._window._annotation_ctl.start_annotation_hydration(records)
        self._window._scan.start_review_intelligence_analysis()
        if self._window._active_scope_enrichment_task is None:
            self._window._scan.start_scope_enrichment_task(records)

    @staticmethod
    def category_display_label(category: object) -> str:
        text = str(category or "").strip()
        labels = {
            "landscape": "Landscape",
            "wildlife": "Wildlife",
            "people_portrait": "Portrait",
            "travel_built": "Travel/Built",
            "night_astro": "Night/Astro",
            "macro_detail": "Macro/Detail",
            "abstract_texture": "Abstract/Texture",
            "product_still_life": "Product/Still",
            "street_documentary": "Street/Documentary",
            "architecture": "Architecture",
            "sports_action": "Sports/Action",
            "event_stage": "Event/Stage",
            "vehicle_transport": "Vehicle/Transport",
            "interior_space": "Interior",
            "aerial_drone": "Aerial/Drone",
            "water_coastal": "Water/Coastal",
            "uncategorized": "Uncategorized",
        }
        return labels.get(text, text.replace("_", " ").strip().title() or "Uncategorized")

    def choose_ai_results(self) -> None:
        start_dir = self._window._settings.value(self._window.AI_RESULTS_KEY, "", str) or self._window._current_folder or QDir.homePath()
        folder = QFileDialog.getExistingDirectory(self._window, "Choose AI Results Folder", start_dir)
        if folder:
            self.load_ai_results(folder)

    def saved_ai_results_belong_to_current_folder(self, saved_path: str) -> bool:
        if not self._window._current_folder or not saved_path:
            return True

        def key(path: str) -> str:
            return os.path.normpath(path).casefold()

        def is_same_or_child(path_key: str, parent_key: str) -> bool:
            parent_key = parent_key.rstrip("\\/")
            return path_key == parent_key or path_key.startswith(parent_key + os.sep)

        current_key = key(str(self._window._current_folder))
        saved_key = key(str(saved_path))
        if not current_key or not saved_key:
            return True
        if saved_key == current_key:
            return True

        hidden_root_key = key(os.path.join(str(self._window._current_folder), ".image_triage_ai"))
        return is_same_or_child(saved_key, hidden_root_key)

    def restore_ai_results(self, *, force: bool = False) -> bool:
        logger = perf_logger()
        start = time.perf_counter() if logger.enabled else 0.0
        # Keep AI bundle loading off the normal startup/manual browse path.
        if not force:
            self.refresh_ai_state()
            if logger.enabled:
                logger.duration("ai_results.restore", (time.perf_counter() - start) * 1000.0, force=force, state="skipped_manual_mode")
            return False
        # Fast path: bundle already loaded for this folder. Skip the re-parse
        # — this fires on every tab flip and was the dominant cost there.
        if (
            self._window._ai_bundle is not None
            and self._window._ai_bundle.source_path
            and self.saved_ai_results_belong_to_current_folder(str(self._window._ai_bundle.source_path))
        ):
            if logger.enabled:
                logger.duration("ai_results.restore", (time.perf_counter() - start) * 1000.0, force=force, state="already_loaded")
            return True
        saved_path = self._window._settings.value(self._window.AI_RESULTS_KEY, "", str)
        if not saved_path:
            self.refresh_ai_state()
            if logger.enabled:
                logger.duration("ai_results.restore", (time.perf_counter() - start) * 1000.0, force=force, state="missing_setting")
            return False
        if not self.saved_ai_results_belong_to_current_folder(saved_path):
            had_ai_bundle = self._window._ai_bundle is not None
            if had_ai_bundle:
                self.clear_ai_results_state(preserve_setting=True, refresh=False)
                self.update_ai_toolbar_state()
            if logger.enabled:
                logger.duration(
                    "ai_results.restore",
                    (time.perf_counter() - start) * 1000.0,
                    force=force,
                    state="foreign_folder",
                    folder=self._window._current_folder,
                    path=saved_path,
                )
            return False
        if not Path(saved_path).exists():
            self._window._settings.remove(self._window.AI_RESULTS_KEY)
            self.refresh_ai_state()
            if logger.enabled:
                logger.duration("ai_results.restore", (time.perf_counter() - start) * 1000.0, force=force, state="missing_file", path=saved_path)
            return False
        loaded = self.load_ai_results(saved_path, show_message=False)
        if logger.enabled:
            logger.duration("ai_results.restore", (time.perf_counter() - start) * 1000.0, force=force, state="loaded" if loaded else "failed", path=saved_path)
        return loaded

    def clear_ai_results_state(self, *, preserve_setting: bool = False, refresh: bool = True) -> None:
        self._window._ai_bundle = None
        if self._window._active_ai_task is None:
            self._window._ai_stage_index = 0
            self._window._ai_stage_total = 3
            self._window._ai_stage_message = "Ready to run AI review"
            self._window._ai_progress_current = 0
            self._window._ai_progress_total = 0
            self._window._ai_progress_eta_text = ""
        if not preserve_setting:
            self._window._settings.remove(self._window.AI_RESULTS_KEY)
        if refresh:
            self.refresh_ai_state()

    def hidden_ai_paths_for_current_folder(self):
        if not self._window._current_folder:
            return None
        # Resolving the path asks the filesystem, so for a folder on a share the GUI thread does not.
        return build_ai_workflow_paths(self._window._current_folder, resolve=not self._window._is_slow_source_folder(self._window._current_folder))

    def cancel_hidden_ai_results_load(self) -> None:
        self._window._hidden_ai_results_timer.stop()
        self._hidden_ai_results_token += 1
        if self._active_hidden_ai_results_task is not None:
            self._active_hidden_ai_results_task.cancel()
            self._active_hidden_ai_results_task = None

    def schedule_hidden_ai_results_load(self, *, delay_ms: int | None = None) -> None:
        if not self._window._current_folder or not self._window._all_records:
            return
        scope_key = self._window._projects.current_scope_key()
        if self._window._hidden_ai_results_checked_scope_key == scope_key:
            return
        if self._active_hidden_ai_results_task is not None:
            return
        if delay_ms is None:
            delay_ms = 450
        self._window._hidden_ai_results_timer.start(max(0, int(delay_ms)))

    def start_hidden_ai_results_load(self) -> None:
        if not self._window._current_folder or not self._window._all_records:
            return
        if self._window._scan_in_progress or self._window._records_view.records_view_chunk_active():
            self.schedule_hidden_ai_results_load(delay_ms=350)
            return
        scope_key = self._window._projects.current_scope_key()
        if self._window._hidden_ai_results_checked_scope_key == scope_key:
            return
        if self._active_hidden_ai_results_task is not None:
            return

        self._hidden_ai_results_token += 1
        token = self._hidden_ai_results_token
        task = HiddenAIResultsLoadTask(
            folder=self._window._current_folder,
            token=token,
            catalog_db_path=self._window._catalog_repository.db_path,
        )
        task.signals.finished.connect(self.handle_hidden_ai_results_loaded, Qt.ConnectionType.QueuedConnection)
        task.signals.missing.connect(self.handle_hidden_ai_results_missing, Qt.ConnectionType.QueuedConnection)
        task.signals.failed.connect(self.handle_hidden_ai_results_failed, Qt.ConnectionType.QueuedConnection)
        self._active_hidden_ai_results_task = task
        self._window._hidden_ai_results_checked_scope_key = scope_key
        QThreadPool.globalInstance().start(task, -50)

    def handle_hidden_ai_results_loaded(
        self,
        folder: str,
        token: int,
        bundle_obj: object,
        source_details_obj: object,
        cache_source: str,
    ) -> None:
        if token != self._hidden_ai_results_token:
            return
        self._active_hidden_ai_results_task = None
        if not isinstance(bundle_obj, AIBundle):
            return
        self._window._ai_bundle = bundle_obj
        self.recompute_ai_demoted_burst_paths()
        source_path = getattr(source_details_obj, "source_path", "") or bundle_obj.source_path
        if source_path:
            self._window._settings.setValue(self._window.AI_RESULTS_KEY, str(source_path))
        if self._window._active_ai_task is None:
            self._window._ai_stage_index = 3
            self._window._ai_stage_total = 3
            self._window._ai_stage_message = "Saved AI cache loaded"
            self._window._ai_progress_current = 0
            self._window._ai_progress_total = 0
            self._window._ai_progress_eta_text = ""
        self.refresh_ai_state()
        matched = bundle_obj.count_matches(self._window._all_records)
        source_label = "catalog cache" if cache_source == "catalog" else "saved AI results"
        self._window.statusBar().showMessage(f"Loaded {source_label} ({matched} matched image(s))")

    def handle_hidden_ai_results_missing(self, folder: str, token: int) -> None:
        if token != self._hidden_ai_results_token:
            return
        self._active_hidden_ai_results_task = None
        self.update_ai_toolbar_state()

    def handle_hidden_ai_results_failed(self, folder: str, token: int, message: str) -> None:
        if token != self._hidden_ai_results_token:
            return
        self._active_hidden_ai_results_task = None
        perf_logger().log("hidden_ai.load.failed_ui", folder=folder, message=message)
        self.update_ai_toolbar_state()

    def load_hidden_ai_results_for_current_folder(self, *, show_message: bool = True) -> bool:
        logger = perf_logger()
        start = time.perf_counter() if logger.enabled else 0.0
        if not self._window._current_folder:
            if logger.enabled:
                logger.duration("ai_results.load_hidden_current", (time.perf_counter() - start) * 1000.0, state="no_folder")
            return False
        # Fast path: if a matching bundle is already in memory for this folder
        # there's nothing to reload. Saves a CSV re-parse + catalog round-trip
        # on every Manual<->AI tab flip.
        if (
            self._window._ai_bundle is not None
            and self._window._ai_bundle.source_path
            and self.saved_ai_results_belong_to_current_folder(str(self._window._ai_bundle.source_path))
        ):
            if logger.enabled:
                logger.duration(
                    "ai_results.load_hidden_current",
                    (time.perf_counter() - start) * 1000.0,
                    folder=self._window._current_folder,
                    state="already_loaded",
                )
            return True
        report_dir = existing_hidden_ai_report_dir(self._window._current_folder)
        if report_dir is None:
            if show_message:
                self._window.statusBar().showMessage("No saved hidden AI results were found for this folder")
                self.update_ai_toolbar_state()
            if logger.enabled:
                logger.duration(
                    "ai_results.load_hidden_current",
                    (time.perf_counter() - start) * 1000.0,
                    folder=self._window._current_folder,
                    state="missing",
                    show_message=show_message,
                )
            return False
        loaded = self.load_ai_results(report_dir, show_message=show_message)
        if logger.enabled:
            logger.duration(
                "ai_results.load_hidden_current",
                (time.perf_counter() - start) * 1000.0,
                folder=self._window._current_folder,
                report_dir=str(report_dir),
                state="loaded" if loaded else "failed",
                show_message=show_message,
            )
        return loaded

    def load_ai_results(self, path: str | Path, *, show_message: bool = True) -> bool:
        logger = perf_logger()
        start = time.perf_counter() if logger.enabled else 0.0
        step_start = start
        result_state = "failed"
        result_count = 0
        source_details = None
        cache_source = "file"
        try:
            source_details = inspect_ai_bundle_source(path)
            if logger.enabled:
                now = time.perf_counter()
                logger.duration(
                    "ai_results.load.inspect_source",
                    (now - step_start) * 1000.0,
                    path=str(path),
                    cache_key=source_details.cache_key,
                )
                step_start = now
            bundle = None
            if self._window._current_folder and source_details.cache_key:
                cached_entry = self._window._catalog_repository.load_ai_bundle(
                    self._window._current_folder,
                    cache_key=source_details.cache_key,
                )
                if logger.enabled:
                    now = time.perf_counter()
                    logger.duration(
                        "ai_results.load.catalog_lookup",
                        (now - step_start) * 1000.0,
                        folder=self._window._current_folder,
                        path=str(path),
                        hit=cached_entry is not None,
                    )
                    step_start = now
                if cached_entry is not None:
                    bundle = cached_entry.bundle
                    cache_source = "catalog"
            if bundle is None:
                bundle = load_ai_bundle(path)
                if logger.enabled:
                    now = time.perf_counter()
                    logger.duration(
                        "ai_results.load.file_read",
                        (now - step_start) * 1000.0,
                        path=str(path),
                    )
                    step_start = now
        except (FileNotFoundError, ValueError, OSError) as exc:
            if show_message:
                QMessageBox.warning(self._window, "AI Results", f"Could not load AI results.\n\n{exc}")
                self._window.statusBar().showMessage("AI results load failed")
            if logger.enabled:
                logger.duration(
                    "ai_results.load.total",
                    (time.perf_counter() - start) * 1000.0,
                    path=str(path),
                    state="failed",
                    error=str(exc),
                )
            return False

        self._window._ai_bundle = bundle
        self.recompute_ai_demoted_burst_paths()
        result_count = len(bundle.results_by_path or {})
        self._window._settings.setValue(self._window.AI_RESULTS_KEY, source_details.source_path if source_details is not None else str(path))
        if self._window._active_ai_task is None and self._window._ai_stage_message != "AI review complete":
            self._window._ai_stage_index = 0
            self._window._ai_stage_total = 3
            self._window._ai_stage_message = "Saved AI cache loaded"
            self._window._ai_progress_current = 0
            self._window._ai_progress_total = 0
            self._window._ai_progress_eta_text = ""
        self.refresh_ai_state()
        if logger.enabled:
            now = time.perf_counter()
            logger.duration(
                "ai_results.load.refresh_ai_state",
                (now - step_start) * 1000.0,
                path=str(path),
                source=cache_source,
                results=result_count,
            )
            step_start = now

        matched = bundle.count_matches(self._window._all_records)
        if logger.enabled:
            now = time.perf_counter()
            logger.duration(
                "ai_results.load.match_records",
                (now - step_start) * 1000.0,
                path=str(path),
                matched=matched,
                records=len(self._window._all_records),
            )
            step_start = now
        if (
            cache_source != "catalog"
            and source_details is not None
            and self._window._current_folder
            and matched > 0
        ):
            self._window._catalog_repository.save_ai_bundle(
                self._window._current_folder,
                cache_key=source_details.cache_key,
                bundle=bundle,
            )
            if logger.enabled:
                now = time.perf_counter()
                logger.duration(
                    "ai_results.load.catalog_save",
                    (now - step_start) * 1000.0,
                    folder=self._window._current_folder,
                    path=str(path),
                    results=result_count,
                )
                step_start = now
        if show_message:
            source_name = Path(bundle.export_csv_path).name
            if cache_source == "catalog":
                self._window.statusBar().showMessage(f"Loaded AI results from catalog cache ({matched} matched image(s))")
            else:
                self._window.statusBar().showMessage(f"Loaded AI results from {source_name} ({matched} matched image(s))")
        result_state = "loaded"
        if logger.enabled:
            logger.duration(
                "ai_results.load.total",
                (time.perf_counter() - start) * 1000.0,
                path=str(path),
                source=cache_source,
                state=result_state,
                results=result_count,
                matched=matched,
                show_message=show_message,
            )
        return True

    def clear_ai_results(self) -> None:
        if self._window._ai_bundle is None:
            return
        self.clear_ai_results_state()
        self._window.statusBar().showMessage("Cleared AI results")

    def reset_ai_review_cache(self) -> None:
        if not self._window._current_folder:
            self._window.statusBar().showMessage("Open a folder before resetting AI review cache.")
            return
        if (
            self._window._active_ai_task is not None
            or self._window._active_ai_runtime_task is not None
            or self._window._active_ai_training_task is not None
            or self._window._active_ai_model_task is not None
        ):
            self._window.statusBar().showMessage("Wait for the current AI task to finish before resetting the AI cache.")
            return
        selected = self.prompt_ai_cache_reset_options()
        if not selected:
            return
        labels = {
            "phash": "pHash duplicate artifacts",
            "clip_topiq": "CLIP/TOPIQ scoring artifacts, exports, and report",
        }
        chosen_text = "\n".join(f"- {labels[key]}" for key in selected)
        warning = dedent(
            f"""
            Reset selected AI artifacts for this folder?

            This will delete:
            {chosen_text}

            It does not delete images, adapter labels, global labels, or training label history.
            """
        ).strip()
        choice = QMessageBox.warning(
            self._window,
            "Reset Selected AI Artifacts",
            warning,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if choice != QMessageBox.StandardButton.Yes:
            return
        reset_parts: list[str] = []
        try:
            if "phash" in selected:
                phash_paths = build_phash_prefilter_paths(self._window._current_folder)
                if phash_paths.artifact_dir.exists():
                    shutil.rmtree(phash_paths.artifact_dir, ignore_errors=False)
                reset_parts.append("pHash")
            if "clip_topiq" in selected:
                paths = build_ai_workflow_paths(self._window._current_folder)
                self.clear_ai_results_state()
                cached_summary = getattr(self._window, "_last_ai_review_summary", None)
                if cached_summary and normalized_path_key(str(cached_summary.get("folder", ""))) == normalized_path_key(self._window._current_folder):
                    self._window._last_ai_review_summary = None
                reset_hidden_ai_review_cache(paths)
                self._window._aiculler_ingested_cache_folder_key = ""
                self._window._aiculler_ingested_path_keys = set()
                self._window._aiculler_ingested_sibling_keys = set()
                self._window._catalog_repository.delete_ai_workflow_cache(self._window._current_folder)
                self._window._catalog_repository.delete_ai_bundle_cache(self._window._current_folder)
                reset_parts.append("CLIP/TOPIQ")
        except OSError as exc:
            QMessageBox.warning(
                self._window,
                "Reset AI Review Cache",
                f"Could not reset the AI review cache.\n\n{exc}",
            )
            self._window.statusBar().showMessage("AI review cache reset failed")
            return
        if "phash" in selected:
            self._window._scan.refresh_prefilter_decisions_for_current_folder()
            self._window.grid.set_prefilter_decisions(self._window._prefilter_decisions_by_path)
            self._window._records_view_cache.mark(ViewInvalidationReason.FILTER_CHANGED)
            self._window._views.apply_records_view(current_path=self._window._records_view.current_visible_record_path())
        self.invalidate_ai_folder_probe_cache()
        self.update_ai_toolbar_state()
        self.refresh_ai_workflow_center()
        suffix = ", ".join(reset_parts) if reset_parts else "selected artifacts"
        self._window.statusBar().showMessage(f"Reset {suffix} for {self._window._current_folder}")

    def prompt_ai_cache_reset_options(self) -> tuple[str, ...]:
        dialog = QDialog(self._window)
        dialog.setWindowTitle("Reset AI Artifacts")
        dialog.setModal(True)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)

        intro = QLabel("Choose which AI artifacts to reset for this folder.")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        phash_checkbox = QCheckBox("pHash duplicate artifacts")
        clip_checkbox = QCheckBox("CLIP/TOPIQ scoring artifacts, exports, and report")
        for checkbox in (phash_checkbox, clip_checkbox):
            checkbox.setChecked(False)
            layout.addWidget(checkbox)

        warning_label = QLabel(
            "Adapter labels, global labels, images, and training label history are not deleted by this reset."
        )
        warning_label.setWordWrap(True)
        warning_label.setObjectName("secondaryText")
        layout.addWidget(warning_label)

        button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        reset_button = button_box.button(QDialogButtonBox.StandardButton.Ok)
        if reset_button is not None:
            reset_button.setText("Reset Selected")
            reset_button.setEnabled(False)
        remove_all_button = QPushButton("Remove All")
        button_box.addButton(remove_all_button, QDialogButtonBox.ButtonRole.ActionRole)

        def sync_enabled() -> None:
            if reset_button is not None:
                reset_button.setEnabled(phash_checkbox.isChecked() or clip_checkbox.isChecked())

        for checkbox in (phash_checkbox, clip_checkbox):
            checkbox.toggled.connect(sync_enabled)
        remove_all_button.clicked.connect(lambda _checked=False: [checkbox.setChecked(True) for checkbox in (phash_checkbox, clip_checkbox)])
        button_box.accepted.connect(dialog.accept)
        button_box.rejected.connect(dialog.reject)
        layout.addWidget(button_box)

        if self._window._exec_dialog_with_geometry(dialog, "reset_ai_artifacts") != dialog.DialogCode.Accepted:
            return ()
        selected: list[str] = []
        if phash_checkbox.isChecked():
            selected.append("phash")
        if clip_checkbox.isChecked():
            selected.append("clip_topiq")
        return tuple(selected)

    def open_ai_report(self) -> None:
        if self._window._ai_bundle is None or not self._window._ai_bundle.report_html_path:
            self._window.statusBar().showMessage("No AI HTML report is available")
            return
        report_path = Path(self._window._ai_bundle.report_html_path)
        if not report_path.exists():
            self._window.statusBar().showMessage("AI HTML report could not be found")
            return
        open_with_default(str(report_path))
        self._window.statusBar().showMessage(f"Opened AI report: {report_path.name}")

    def refresh_ai_state(self) -> None:
        logger = perf_logger()
        start = time.perf_counter() if logger.enabled else 0.0
        step_start = start

        def log_step(event: str, step_started: float, **fields: object) -> float:
            if not logger.enabled:
                return step_started
            now = time.perf_counter()
            logger.duration(
                event,
                (now - step_started) * 1000.0,
                mode="manual",
                records=len(self._window._all_records),
                visible_records=len(self._window._records),
                ai_loaded=self._window._ai_bundle is not None,
                **fields,
            )
            return now

        ai_results = self._window._ai_bundle.results_by_path if self._window._ai_bundle and self._window._ai_bundle.results_by_path else {}
        self._window.grid.set_ai_results(ai_results)
        step_start = log_step("ai_state.refresh.grid_results", step_start, results=len(ai_results))
        self._window.details_view.refresh_rows()
        step_start = log_step("ai_state.refresh.details_rows", step_start)
        self._window._scan.start_scope_enrichment_task()
        step_start = log_step("ai_state.refresh.scope_enrichment", step_start)
        current_path = self._window._records_view.current_visible_record_path()
        if self._window._all_records:
            self._window._views.apply_records_view(
                current_path=current_path,
                chunked=self._window._records_view.records_view_chunk_active(),
                post_load_enrichment=self._window._records_view_chunk_post_load_enrichment,
            )
        step_start = log_step("ai_state.refresh.records_view", step_start, current_path=current_path or "")
        self._window._views.refresh_viewport_mode()
        step_start = log_step("ai_state.refresh.viewport", step_start)
        self.refresh_ai_summary_cache()
        step_start = log_step("ai_state.refresh.summary_cache", step_start)
        self.update_ai_summary()
        step_start = log_step("ai_state.refresh.summary_ui", step_start)
        self.update_ai_toolbar_state()
        step_start = log_step("ai_state.refresh.toolbar", step_start)
        self._window._inspector.update_status()
        step_start = log_step("ai_state.refresh.status", step_start)
        self._window._inspector.update_inspector_context()
        step_start = log_step("ai_state.refresh.inspector", step_start)
        if self._window._preview_ctl.preview_is_visible():
            index = self._window.grid.current_index()
            if index >= 0:
                self._window._preview_ctl.open_preview(index)
        step_start = log_step("ai_state.refresh.preview", step_start, preview_visible=self._window._preview_ctl.preview_is_visible())
        if logger.enabled:
            logger.duration(
                "ai_state.refresh.total",
                (time.perf_counter() - start) * 1000.0,
                mode="manual",
                records=len(self._window._all_records),
                visible_records=len(self._window._records),
                results=len(ai_results),
            )

    def ai_folder_probe(self, ai_paths) -> dict:
        folder = self._window._current_folder or ""
        now = time.perf_counter()
        cache = self._ai_folder_probe_cache
        if (
            cache is not None
            and cache.get("folder") == folder
            and (now - cache.get("at", 0.0)) < self._window._AI_FOLDER_PROBE_TTL_S
        ):
            return cache
        if folder and self._window._is_slow_source_folder(folder):
            return self.ai_folder_probe_off_thread(ai_paths, folder, cache)
        probe = _compute_ai_folder_probe(ai_paths, folder)
        probe["at"] = now
        self._ai_folder_probe_cache = probe
        return probe

    def ai_folder_probe_off_thread(self, ai_paths, folder: str, cache: dict | None) -> dict:
        """The probe of a folder on a network / removable drive.

        It opens SQLite files and stats artifacts inside the folder, which on the GUI thread would stall
        every folder open on a NAS (and freeze the window if the share is asleep). So a worker does it;
        until its answer arrives the toolbar shows what was last known for this folder, or "nothing found
        yet", and the arriving answer refreshes the toolbar."""
        if self._ai_probe_task is None:
            task = _AIFolderProbeTask(self._ai_probe_generation, folder, ai_paths)
            task.signals.ready.connect(self.handle_ai_folder_probe_ready, Qt.ConnectionType.QueuedConnection)
            self._ai_probe_task = task
            QThreadPool.globalInstance().start(task)
        if cache is not None and cache.get("folder") == folder:
            return cache
        return _unknown_ai_folder_probe(folder)

    def handle_ai_folder_probe_ready(self, generation: int, folder: str, probe: object) -> None:
        self._ai_probe_task = None
        current = self._window._current_folder or ""
        if not isinstance(probe, dict):
            return
        if folder != current or generation != self._ai_probe_generation:
            # The folder, or its AI data, changed while the worker was looking: this answer is stale, so
            # ask again for what is current (one probe is in flight at a time).
            self.update_ai_toolbar_state()
            return
        probe["at"] = time.perf_counter()
        self._ai_folder_probe_cache = probe
        self.update_ai_toolbar_state()

    def invalidate_ai_folder_probe_cache(self) -> None:
        self._ai_folder_probe_cache = None
        self._ai_probe_generation += 1

    def update_ai_toolbar_state(self) -> None:
        logger = perf_logger()
        start = time.perf_counter() if logger.enabled else 0.0
        step_start = start

        def log_step(event: str, step_started: float, **fields: object) -> float:
            if not logger.enabled:
                return step_started
            now = time.perf_counter()
            logger.duration(
                event,
                (now - step_started) * 1000.0,
                mode="manual",
                records=len(self._window._all_records),
                ai_loaded=self._window._ai_bundle is not None,
                **fields,
            )
            return now

        current_folder = bool(self._window._current_folder)
        ai_loaded = self._window._ai_bundle is not None
        ai_runtime_ready = self._window._ai_setup.ai_runtime_available()
        culler_runtime_ready = aiculler_runtime_available()
        semantic_model_ready = self._window._ai_setup.semantic_model_available()
        step_start = log_step(
            "ai_toolbar_state.readiness",
            step_start,
            runtime_ready=ai_runtime_ready,
            culler_ready=culler_runtime_ready,
            semantic_ready=semantic_model_ready,
        )
        ai_paths = self.hidden_ai_paths_for_current_folder()
        ai_probe = self.ai_folder_probe(ai_paths)
        # Existence of the saved ranked export is still only surfaced in AI mode.
        saved_exists = False  # the saved-ranked-export probe was only surfaced in the retired AI Review mode
        step_start = log_step("ai_toolbar_state.saved_probe", step_start, saved_exists=saved_exists, has_paths=ai_paths is not None)
        current_index = self._window.grid.current_index()
        current_ai = self.ai_result_for_index(current_index)
        can_compare_group = bool(current_ai and current_ai.group_size > 1)
        step_start = log_step("ai_toolbar_state.current_ai", step_start, can_compare_group=can_compare_group)

        if self._window.actions is not None:
            can_use_ai_tools = (
                current_folder
                and culler_runtime_ready
                and self._window._active_ai_task is None
                and self._window._active_ai_runtime_task is None
                and self._window._active_ai_training_task is None
                and self._window._active_ai_model_task is None
            )
            can_apply_ai_cull = (
                current_folder
                and ai_loaded
                and self._window._active_ai_task is None
                and self._window._active_ai_runtime_task is None
                and self._window._active_ai_training_task is None
                and self._window._active_ai_model_task is None
                and not self._window._is_winners_folder()
                and not self._window._is_recycle_folder()
            )
            can_sort_semantic = (
                current_folder
                and self._window._active_ai_task is None
                and self._window._active_ai_runtime_task is None
                and self._window._active_ai_training_task is None
                and self._window._active_ai_model_task is None
                and not self._window._is_winners_folder()
                and not self._window._is_recycle_folder()
                and bool(ai_probe["semantic_ready"] or ai_probe["report_ready"])
            )
            step_start = log_step("ai_toolbar_state.can_flags", step_start)
            rerank_ready = bool(ai_probe["rerank_ready"])
            step_start = log_step("ai_toolbar_state.db_probe", step_start)
            self._window.actions.install_ai_runtime.setEnabled(True)
            self._window.actions.download_ai_model.setEnabled(True)
            self._window.actions.run_ai_culling.setEnabled(can_use_ai_tools)
            self._window.actions.quick_rerank_ai_culling.setEnabled(can_use_ai_tools and rerank_ready)
            self._window.actions.apply_ai_culling.setEnabled(can_apply_ai_cull)
            self._window.actions.sort_ai_semantic_folders.setEnabled(can_sort_semantic)
            self._window.actions.reset_ai_review_cache.setEnabled(
                current_folder
                and self._window._active_ai_task is None
                and self._window._active_ai_runtime_task is None
                and self._window._active_ai_training_task is None
                and self._window._active_ai_model_task is None
            )
            self._window.actions.load_saved_ai.setEnabled(current_folder and saved_exists and self._window._active_ai_task is None)
            self._window.actions.open_ai_report.setEnabled(bool(ai_loaded and self._window._ai_bundle and self._window._ai_bundle.report_html_path))
            self._window.actions.show_ai_review_summary.setEnabled(bool(ai_loaded or getattr(self._window, "_last_ai_review_summary", None)))
            can_open_training_commands = (
                current_folder
                and self._window._active_ai_task is None
                and self._window._active_ai_runtime_task is None
                and self._window._active_ai_training_task is None
                and self._window._active_ai_model_task is None
                and not self._window._is_winners_folder()
                and not self._window._is_recycle_folder()
            )
            self._window.actions.manage_people.setEnabled(can_open_training_commands and bool(ai_probe["adapter_db_exists"]))
            self._window.actions.review_ai_adapter_labels.setEnabled(can_open_training_commands and bool(ai_probe["adapter_db_exists"]))
            self._window.actions.next_ai_pick.setEnabled(ai_loaded)
            self._window.actions.next_unreviewed_ai_pick.setEnabled(ai_loaded)
            self._window.actions.compare_ai_group.setEnabled(ai_loaded and can_compare_group)
            self._window.actions.review_ai_disagreements.setEnabled(ai_loaded)
            self._window.actions.clear_ai_results.setEnabled(ai_loaded)
            if FilterMode.AI_GROUPED in self._window.actions.filter_actions:
                self._window.actions.filter_actions[FilterMode.AI_GROUPED].setEnabled(ai_loaded)
            if FilterMode.AI_TOP_PICKS in self._window.actions.filter_actions:
                self._window.actions.filter_actions[FilterMode.AI_TOP_PICKS].setEnabled(ai_loaded)
            if FilterMode.AI_DISAGREEMENTS in self._window.actions.filter_actions:
                self._window.actions.filter_actions[FilterMode.AI_DISAGREEMENTS].setEnabled(ai_loaded)
            aiculler_available = bool(ai_probe["aiculler_available"])
            if FilterMode.AI_INGESTED in self._window.actions.filter_actions:
                self._window.actions.filter_actions[FilterMode.AI_INGESTED].setEnabled(aiculler_available)
            phash_available = bool(ai_probe["phash_available"])
            if FilterMode.AI_PREFILTER_DUMPED in self._window.actions.filter_actions:
                self._window.actions.filter_actions[FilterMode.AI_PREFILTER_DUMPED].setEnabled(phash_available)
        step_start = log_step("ai_toolbar_state.actions", step_start)
        for mode, action in self._window._ai_state_actions.items():
            action.setEnabled(ai_loaded or mode == AIStateFilter.ALL)
        step_start = log_step("ai_toolbar_state.filter_actions", step_start)
        self._window._aiculler.refresh_adapter_status_indicator()
        self.refresh_ai_workflow_center()
        step_start = log_step("ai_toolbar_state.adapter_status", step_start)

        if self._window._active_ai_task is not None:
            self._window.ai_status_label.setText(self.build_ai_progress_text())
        elif self._window._active_ai_runtime_task is not None:
            self._window.ai_status_label.setText("Installing AI runtime...")
        elif self._window._active_ai_model_task is not None:
            self._window.ai_status_label.setText("Downloading AI model...")
        elif not ai_runtime_ready:
            self._window.ai_status_label.setText("AI runtime not installed")
        elif not culler_runtime_ready:
            self._window.ai_status_label.setText("AI culling models not installed")
        elif ai_loaded and self._window._ai_bundle is not None:
            export_name = Path(self._window._ai_bundle.export_csv_path).name
            self._window.ai_status_label.setText(f"Loaded {export_name}")
        elif saved_exists:
            self._window.ai_status_label.setText("Saved AI cache available")
        elif not current_folder and self._window._scope_kind != "folder":
            self._window.ai_status_label.setText("AI cache stays folder-local in virtual scopes")
        else:
            self._window.ai_status_label.setText("No AI cache for this folder yet")
        step_start = log_step("ai_toolbar_state.status_label", step_start)

        runtime_lines = [
            f"Python: {self._window._ai_runtime.python_executable}",
            f"Runtime installed: {ai_runtime_ready}",
            f"Semantic model: {self._window._ai_runtime.semantic_model_name}",
            f"Semantic model installed: {semantic_model_ready}",
            f"Embedding batch size: {self._window._settings_ctl.ai_embed_batch_size_label()}",
            f"CLI-Culler CLIP model: {self.ai_clip_model_variant_label()}",
            f"TOPIQ model installed: {self._window._ai_setup.aiculler_topiq_model_available()}",
            f"InsightFace models installed: {self._window._ai_setup.aiculler_face_model_available()}",
        ]
        runtime_status = self._window._ai_setup.managed_ai_runtime_status()
        runtime_lines.append(f"Runtime cache: {runtime_status.directories.root}")
        if runtime_status.installed_variants:
            runtime_lines.append(
                "Runtime profiles: " + ", ".join(ai_runtime_variant_label(variant) for variant in runtime_status.installed_variants)
            )
        runtime_lines.append(
            f"CLIP model dir: {self._window._ai_setup.managed_aiculler_clip_model_installation().install_dir}"
        )
        runtime_lines.append(
            f"TOPIQ model dir: {self._window._ai_setup.managed_aiculler_topiq_model_installation().install_dir}"
        )
        runtime_lines.append(
            f"InsightFace model dir: {self._window._ai_setup.managed_aiculler_face_model_installation().install_dir}"
        )
        runtime_lines.append(f"Managed semantic model dir: {self._window._ai_setup.managed_semantic_model_installation().install_dir}")
        if ai_paths is not None:
            runtime_lines.append(f"Hidden cache: {ai_paths.hidden_root}")
        runtime_lines.append("Tag legend:")
        for tag_name, description in ai_review_tag_definitions():
            runtime_lines.append(f"{tag_name}: {description}")
        tooltip_text = "\n".join(runtime_lines)
        self._window.ai_status_label.setToolTip(tooltip_text)
        self._window.ai_status_widget.setToolTip(tooltip_text)
        step_start = log_step("ai_toolbar_state.tooltip", step_start, tooltip_lines=len(runtime_lines))
        active_ai_status = any(
            task is not None
            for task in (
                self._window._active_ai_task,
                self._window._active_ai_runtime_task,
                self._window._active_ai_model_task,
                self._window._active_ai_training_task,
            )
        )
        self.sync_ai_status_visibility(active=active_ai_status, message=self._window._ai_stage_message)
        self.refresh_ai_progress_bar()
        self._window._toolbar.schedule_workspace_toolbar_overflow_update("ai")
        step_start = log_step("ai_toolbar_state.progress_overflow", step_start, active_ai_status=active_ai_status)
        self._window._inspector.update_action_states()
        step_start = log_step("ai_toolbar_state.action_states", step_start)
        if logger.enabled:
            logger.duration(
                "ai_toolbar_state.total",
                (time.perf_counter() - start) * 1000.0,
                mode="manual",
                records=len(self._window._all_records),
                ai_loaded=ai_loaded,
                saved_exists=saved_exists,
            )

    def manual_ai_protected_paths(self) -> tuple[str, ...]:
        protected: list[str] = []
        seen: set[str] = set()
        for record in self._window._all_records:
            if record.is_folder:
                continue
            annotation = self._window._annotations.get(record.path)
            if annotation is None or not annotation.winner:
                continue
            for path in record.stack_paths:
                key = os.path.normcase(os.path.abspath(path))
                if key in seen:
                    continue
                seen.add(key)
                protected.append(path)
        return tuple(protected)

    def run_ai_pipeline(self) -> None:
        logger = perf_logger()
        start = time.perf_counter() if logger.enabled else 0.0

        if not self._window._current_folder:
            self._window.statusBar().showMessage("Choose a folder before running AI review")
            if logger.enabled:
                logger.duration("ai.run_prepare.blocked", (time.perf_counter() - start) * 1000.0, reason="no_folder")
            return
        if not self._window._all_records:
            self._window.statusBar().showMessage("No images are loaded for the current folder yet.")
            if logger.enabled:
                logger.duration("ai.run_prepare.blocked", (time.perf_counter() - start) * 1000.0, folder=self._window._current_folder, reason="no_records")
            return
        if self._window._active_ai_task is not None:
            self._window.statusBar().showMessage("AI review is already running for the current folder")
            self.show_ai_review_progress_dialog(folder=self._window._current_folder)
            if logger.enabled:
                logger.duration("ai.run_prepare.blocked", (time.perf_counter() - start) * 1000.0, folder=self._window._current_folder, reason="already_running")
            return

        try:
            self._window._ai_setup.refresh_ai_runtime_preferences()
            runtime = self._window._settings_ctl.configured_aiculler_runtime(workers=self._window._settings_ctl.configured_ai_embed_batch_size())
            runtime.validate()
            if not self.confirm_cpu_clip_run(runtime):
                if logger.enabled:
                    logger.duration(
                        "ai.run_prepare.blocked",
                        (time.perf_counter() - start) * 1000.0,
                        folder=self._window._current_folder,
                        reason="cpu_warning_cancelled",
                    )
                return
            paths = build_aiculler_workflow_paths(self._window._current_folder)
            task = AICullerRunTask(
                folder=Path(self._window._current_folder),
                runtime=runtime,
                paths=paths,
                records=tuple(record for record in self._window._all_records if not record.is_folder),
                run_phash_prefilter=self._window._phash_prefilter_settings.enabled,
                phash_prefilter_settings=self._window._phash_prefilter_settings,
                protected_paths=self.manual_ai_protected_paths(),
            )
        except Exception as exc:
            if logger.enabled:
                logger.duration(
                    "ai.run_prepare.failed",
                    (time.perf_counter() - start) * 1000.0,
                    folder=self._window._current_folder,
                    records=len(self._window._all_records),
                    error=str(exc),
                )
            QMessageBox.warning(self._window, "AI Review", f"Could not prepare the AI run.\n\n{exc}")
            return

        task.signals.started.connect(self.handle_ai_run_started, Qt.ConnectionType.QueuedConnection)
        task.signals.stage.connect(self.handle_ai_run_stage, Qt.ConnectionType.QueuedConnection)
        task.signals.progress.connect(self.handle_ai_run_progress, Qt.ConnectionType.QueuedConnection)
        task.signals.detail.connect(self.handle_ai_run_detail, Qt.ConnectionType.QueuedConnection)
        task.signals.finished.connect(self.handle_ai_run_finished, Qt.ConnectionType.QueuedConnection)
        task.signals.failed.connect(self.handle_ai_run_failed, Qt.ConnectionType.QueuedConnection)
        task.signals.cancelled.connect(self.handle_ai_run_cancelled, Qt.ConnectionType.QueuedConnection)
        self._window._active_ai_task = task
        self.defer_background_review_work_for_ai(reason="run_ai_review")
        self._active_ai_run_start_perf = start if logger.enabled else 0.0
        self._window._active_ai_embedding_cache_key = ""
        self._window._active_ai_cluster_cache_key = ""
        self._window._active_ai_report_cache_key = ""
        self._window._active_ai_semantic_cache_key = ""
        self._window._ai_stage_index = 0
        self._window._ai_stage_total = 5
        self._window._ai_stage_message = "Queued AI review"
        self._window._ai_progress_current = 0
        self._window._ai_progress_total = 0
        self._window._ai_progress_eta_text = ""
        self.show_ai_review_progress_dialog(folder=self._window._current_folder, reset=True)
        if self._ai_review_progress_dialog is not None:
            self._ai_review_progress_dialog.set_stage(
                stage_index=self._window._ai_stage_index,
                stage_total=self._window._ai_stage_total,
                message=self._window._ai_stage_message,
            )
        self.update_ai_toolbar_state()
        self._window.statusBar().showMessage(f"Queued AI review for {self._window._current_folder}")
        self._window._ai_run_pool.start(task)
        if logger.enabled:
            logger.duration(
                "ai.run_queued",
                (time.perf_counter() - start) * 1000.0,
                folder=self._window._current_folder,
                records=len(self._window._all_records),
                backend="cli-culler",
            )

    def rerank_ai_pipeline(self) -> None:
        if not self._window._current_folder:
            self._window.statusBar().showMessage("Choose a folder before reranking.")
            return
        if self._window._active_ai_task is not None:
            self._window.statusBar().showMessage("AI review is already running for the current folder")
            self.show_ai_review_progress_dialog(folder=self._window._current_folder)
            return
        try:
            paths = build_aiculler_workflow_paths(self._window._current_folder)
        except Exception as exc:
            QMessageBox.warning(self._window, "Quick Rerank", f"Could not resolve AI paths.\n\n{exc}")
            return
        readiness = aiculler_rerank_readiness(aiculler_db_path(paths))
        if not readiness.get("can_rerank"):
            QMessageBox.information(
                self._window,
                "Quick Rerank",
                "Run Cull & Score at least once for this folder before using Quick Rerank.\n\n"
                "Quick Rerank reuses the existing ingest, categories, and clusters.",
            )
            return
        try:
            runtime = self._window._settings_ctl.configured_aiculler_runtime(workers=self._window._settings_ctl.configured_ai_embed_batch_size())
            runtime.validate()
            task = AICullerRunTask(
                folder=Path(self._window._current_folder),
                runtime=runtime,
                paths=paths,
                records=tuple(record for record in self._window._all_records if not record.is_folder),
                stages=("rank",),
                phash_prefilter_settings=self._window._phash_prefilter_settings,
            )
        except Exception as exc:
            QMessageBox.warning(self._window, "Quick Rerank", f"Could not prepare the rerank.\n\n{exc}")
            return
        task.signals.started.connect(self.handle_ai_run_started, Qt.ConnectionType.QueuedConnection)
        task.signals.stage.connect(self.handle_ai_run_stage, Qt.ConnectionType.QueuedConnection)
        task.signals.progress.connect(self.handle_ai_run_progress, Qt.ConnectionType.QueuedConnection)
        task.signals.detail.connect(self.handle_ai_run_detail, Qt.ConnectionType.QueuedConnection)
        task.signals.finished.connect(self.handle_ai_run_finished, Qt.ConnectionType.QueuedConnection)
        task.signals.failed.connect(self.handle_ai_run_failed, Qt.ConnectionType.QueuedConnection)
        task.signals.cancelled.connect(self.handle_ai_run_cancelled, Qt.ConnectionType.QueuedConnection)
        self._window._active_ai_task = task
        self.defer_background_review_work_for_ai(reason="rerank_ai_review")
        self._active_ai_run_start_perf = 0.0
        self._window._active_ai_embedding_cache_key = ""
        self._window._active_ai_cluster_cache_key = ""
        self._window._active_ai_report_cache_key = ""
        self._window._active_ai_semantic_cache_key = ""
        self._window._ai_stage_index = 0
        self._window._ai_stage_total = 2
        self._window._ai_stage_message = "Queued quick rerank"
        self._window._ai_progress_current = 0
        self._window._ai_progress_total = 0
        self._window._ai_progress_eta_text = ""
        self.show_ai_review_progress_dialog(folder=self._window._current_folder, reset=True)
        if self._ai_review_progress_dialog is not None:
            self._ai_review_progress_dialog.set_stage(
                stage_index=self._window._ai_stage_index,
                stage_total=self._window._ai_stage_total,
                message=self._window._ai_stage_message,
            )
        self.update_ai_toolbar_state()
        ready_count = int(readiness.get("ready_image_count") or 0)
        file_records = sum(1 for record in self._window._all_records if not record.is_folder)
        if ready_count and file_records and ready_count != file_records:
            self._window.statusBar().showMessage(
                f"Quick rerank: scoring {ready_count} indexed image(s). "
                f"Folder has {file_records} — run AI Culler to pick up new files."
            )
        else:
            self._window.statusBar().showMessage(f"Queued quick rerank for {self._window._current_folder}")
        self._window._ai_run_pool.start(task)

    def ai_cull_record_groups(self, records: list[ImageRecord] | tuple[ImageRecord, ...] | None = None) -> dict[AICullBucket, list[ImageRecord]]:
        grouped: dict[AICullBucket, list[ImageRecord]] = {bucket: [] for bucket in AICullBucket}
        for record in records or self._window._all_records:
            ai_result = self.ai_result_for_record(record)
            grouped[ai_cull_bucket_for_result(ai_result)].append(record)
        for bucket, bucket_records in grouped.items():
            bucket_records.sort(
                key=lambda record: (
                    ai_manual_cull_sort_key(self.ai_result_for_record(record)),
                    record.name.casefold(),
                )
            )
            grouped[bucket] = bucket_records
        return grouped

    def open_ai_cull_follow_up_review(self, source_paths: tuple[str, ...]) -> None:
        existing_paths = tuple(path for path in source_paths if self._window._record_index_by_path.get(path) is not None)
        if not existing_paths:
            self._window.statusBar().showMessage("No Winner or Needs Review images remain for follow-up review.")
            return
        self._window._toolbar.sync_chrome_to_manual_review()
        current_path = self._window._records_view.current_visible_record_path()
        target_path = next(
            (path for path in existing_paths if normalized_path_key(path) == normalized_path_key(current_path)),
            existing_paths[0],
        )
        index = self._window._record_index_by_path.get(target_path)
        if index is not None:
            self._window.grid.set_current_index(index)
        self._window.statusBar().showMessage(f"{len(existing_paths)} Winner or Needs Review image(s) remain for manual review.")

    def apply_ai_culling(self) -> None:
        if not self._window._current_folder:
            self._window.statusBar().showMessage("Open a source folder before applying AI decisions.")
            return
        if self._window._ai_bundle is None:
            self._window.statusBar().showMessage("Run Cull & Score or load AI results before applying AI decisions.")
            return
        if self._window._is_winners_folder() or self._window._is_recycle_folder():
            self._window.statusBar().showMessage("Apply AI Decisions only from the source folder, not from _winners or the recycle bin.")
            return
        if self._window._active_ai_task is not None:
            self._window.statusBar().showMessage("Wait for the current AI review run to finish first.")
            return

        groups = self.ai_cull_record_groups(tuple(self._window._all_records))
        ai_pick_records = groups[AICullBucket.AI_PICK]
        reject_records = groups[AICullBucket.REJECT]
        keeper_records = groups[AICullBucket.KEEPER]
        review_records = groups[AICullBucket.NEEDS_REVIEW]

        if not ai_pick_records and not reject_records and not keeper_records and not review_records:
            self._window.statusBar().showMessage("No AI-ranked images are available to cull in this folder.")
            return

        dialog = ApplyAIDecisionsDialog(
            ai_pick_records=ai_pick_records,
            reject_records=reject_records,
            keeper_count=len(keeper_records),
            review_count=len(review_records),
            thumbnail_manager=self._window.thumbnail_manager,
            parent=self._window,
        )
        if self._window._exec_dialog_with_geometry(dialog, "apply_ai_decisions") != dialog.DialogCode.Accepted:
            return

        winners_dir = os.path.join(self._window._current_folder, "_winners")
        ai_pick_paths = [record.path for record in ai_pick_records]
        reject_paths = [record.path for record in reject_records]
        follow_up_paths = tuple(record.path for record in (*keeper_records, *review_records))

        batch_id = uuid.uuid4().hex
        moved_winners = 0
        moved_rejects = 0
        removed_reject_paths: list[str] = []
        if ai_pick_paths:
            # Routed through the same progress-dialog-and-cancel transfer the
            # ordinary drag-drop/manual batch move uses (RecordOpsController.
            # move_records_by_paths -> transfer_progress.run_file_transfer),
            # rather than looping _move_record_to_path per file: hundreds of
            # AI Picks used to move with no feedback and no way to cancel.
            # The shared batch_id keeps this half of the action in the same
            # Undo as the Reject -> Recycle half below. A user who cancels
            # partway through only has the already-moved subset removed from
            # the view/undo stack; the rest stays untouched in the source
            # folder (see move_records_by_paths, which only acts on
            # result.moved).
            moved_winners = self._window._record_ops.move_records_by_paths(ai_pick_paths, winners_dir, batch_id=batch_id)
        if reject_paths:
            # Recycling is fast/local enough (same as everywhere else in the
            # app that recycles) that a progress dialog would just be noise,
            # so this half intentionally stays a plain per-file loop.
            for path in reject_paths:
                if self._window._record_ops.move_record_to_ai_recycle_by_path(path, defer_removal=True, batch_id=batch_id):
                    moved_rejects += 1
                    removed_reject_paths.append(path)
        if removed_reject_paths:
            self._window._record_ops.remove_records_by_paths(removed_reject_paths)

        self._window.statusBar().showMessage(
            f"Applied AI decisions: moved {moved_winners} AI Pick image(s) to _winners and {moved_rejects} Reject image(s) to the recycle bin."
        )

        reviewable_paths = tuple(path for path in follow_up_paths if self._window._record_index_by_path.get(path) is not None)
        if not reviewable_paths:
            return

        follow_up = QMessageBox.question(
            self._window,
            "Review Winners And Needs Review?",
            (
                f"{len(reviewable_paths)} image(s) remain in Winner or Needs Review.\n\n"
                "Jump to the first remaining image in Manual Review?"
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if follow_up == QMessageBox.StandardButton.Yes:
            self.open_ai_cull_follow_up_review(reviewable_paths)

    def ensure_ai_review_progress_dialog(self) -> AIReviewProgressDialog:
        dialog = self._ai_review_progress_dialog
        if dialog is None:
            dialog = AIReviewProgressDialog(
                detailed=self._window._ai_review_detail_progress_enabled,
                parent=self._window,
            )
            dialog.stop_requested.connect(self.request_stop_ai_review)
            self._ai_review_progress_dialog = dialog
        return dialog

    def show_ai_review_progress_dialog(self, *, folder: str, reset: bool = False) -> None:
        dialog = self.ensure_ai_review_progress_dialog()
        if reset:
            dialog.start_run(folder=folder, stage_total=self._window._ai_stage_total)
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def close_ai_review_progress_dialog(self) -> None:
        dialog = self._ai_review_progress_dialog
        if dialog is None:
            return
        dialog.finish_and_close("AI Review complete")
        dialog.deleteLater()
        self._ai_review_progress_dialog = None

    def request_stop_ai_review(self) -> None:
        task = self._window._active_ai_task
        if task is None:
            if self._ai_review_progress_dialog is not None:
                self._ai_review_progress_dialog.mark_finished("AI Review is not running")
            return
        perf_logger().log("ai.run_cancel_requested", folder=self._window._current_folder)
        task.cancel()
        self._window._ai_stage_message = "Stopping AI review"
        self._window._ai_progress_eta_text = ""
        self.update_ai_toolbar_state()
        self._window.statusBar().showMessage("Stopping AI review...")

    def ai_run_signal_matches_active_task(self, folder: str) -> bool:
        task = self._window._active_ai_task
        if task is None or not folder:
            return False
        task_folder = str(getattr(task, "folder", "") or "")
        if not task_folder:
            return False
        if os.path.normpath(task_folder).casefold() == os.path.normpath(folder).casefold():
            return True
        return normalized_path_key(task_folder) == normalized_path_key(folder)

    def ai_run_signal_matches_current_folder(self, folder: str) -> bool:
        if not self._window._current_folder or not folder:
            return False
        if os.path.normpath(self._window._current_folder).casefold() == os.path.normpath(folder).casefold():
            return True
        return normalized_path_key(self._window._current_folder) == normalized_path_key(folder)

    def handle_ai_run_started(self, folder: str) -> None:
        perf_logger().log("ai.run_started", folder=folder)
        if not self.ai_run_signal_matches_active_task(folder) or not self.ai_run_signal_matches_current_folder(folder):
            perf_logger().log("ai.run_signal_ignored", signal="started", folder=folder)
            return
        self._last_ai_perf_progress_signature = None
        self.show_ai_review_progress_dialog(folder=folder)
        if self._ai_review_progress_dialog is not None:
            self._ai_review_progress_dialog.set_stage(
                stage_index=0,
                stage_total=max(1, self._window._ai_stage_total),
                message="Preparing AI review",
            )
        self._window._ai_stage_index = 0
        self._window._ai_stage_total = max(1, self._window._ai_stage_total)
        self._window._ai_stage_message = "Preparing AI review"
        self._window._ai_progress_current = 0
        self._window._ai_progress_total = 0
        self._window._ai_progress_eta_text = ""
        self._window.statusBar().showMessage(f"AI review started for {folder}")
        self.update_ai_toolbar_state()

    def handle_ai_run_stage(self, folder: str, stage_index: int, stage_total: int, message: str) -> None:
        perf_logger().log("ai.stage", folder=folder, stage_index=stage_index, stage_total=stage_total, message=message)
        if not self.ai_run_signal_matches_active_task(folder) or not self.ai_run_signal_matches_current_folder(folder):
            perf_logger().log("ai.run_signal_ignored", signal="stage", folder=folder, message=message)
            return
        if self._ai_review_progress_dialog is not None:
            self._ai_review_progress_dialog.set_stage(
                stage_index=max(0, stage_index),
                stage_total=max(1, stage_total),
                message=message,
            )
        self._window._ai_stage_index = max(0, stage_index)
        self._window._ai_stage_total = max(1, stage_total)
        self._window._ai_stage_message = message
        self._window._ai_progress_current = 0
        self._window._ai_progress_total = 0
        self._window._ai_progress_eta_text = ""
        self._window.ai_status_label.setText(self.build_ai_progress_text())
        self.refresh_ai_progress_bar()
        self._window.statusBar().showMessage(message)

    def handle_ai_run_progress(
        self,
        folder: str,
        message: str,
        current: int,
        total: int,
        eta_text: str,
    ) -> None:
        if not self.ai_run_signal_matches_active_task(folder) or not self.ai_run_signal_matches_current_folder(folder):
            perf_logger().log("ai.run_signal_ignored", signal="progress", folder=folder, message=message)
            return
        if self.should_log_ai_progress_perf(message=message, current=current, total=total):
            perf_logger().log("ai.progress", folder=folder, message=message, current=current, total=total, eta=eta_text)
        if self._ai_review_progress_dialog is not None:
            self._ai_review_progress_dialog.set_progress(
                message=message,
                current=max(0, current),
                total=max(0, total),
                eta_text=eta_text.strip(),
            )
        self._window._ai_stage_message = message
        self._window._ai_progress_current = max(0, current)
        self._window._ai_progress_total = max(0, total)
        self._window._ai_progress_eta_text = eta_text.strip()
        self._window.ai_status_label.setText(self.build_ai_progress_text())
        self.refresh_ai_progress_bar()

    def should_log_ai_progress_perf(self, *, message: str, current: int, total: int) -> bool:
        logger = perf_logger()
        if not logger.enabled:
            return False
        normalized_message = " ".join((message or "").split())
        current = max(0, int(current))
        total = max(0, int(total))
        previous = getattr(self, "_last_ai_perf_progress_signature", None)
        if previous is None:
            self._last_ai_perf_progress_signature = (normalized_message, current, total)
            return True
        previous_message, previous_current, previous_total = previous
        if normalized_message != previous_message or total != previous_total:
            self._last_ai_perf_progress_signature = (normalized_message, current, total)
            return True
        if total <= 0:
            return False
        step = max(1, total // 20)
        if current >= total or current - int(previous_current) >= step:
            self._last_ai_perf_progress_signature = (normalized_message, current, total)
            return True
        return False

    def handle_ai_run_detail(self, folder: str, message: str) -> None:
        if not self.ai_run_signal_matches_active_task(folder) or not self.ai_run_signal_matches_current_folder(folder):
            perf_logger().log("ai.run_signal_ignored", signal="detail", folder=folder, message=message)
            return
        if self._ai_review_progress_dialog is not None:
            self._ai_review_progress_dialog.append_detail(message)

    @staticmethod
    def ai_review_bucket_counts(bundle: AIBundle | None) -> Counter[AICullBucket]:
        counts: Counter[AICullBucket] = Counter()
        if bundle is None:
            return counts
        for result in iter_ai_bundle_results(bundle):
            counts[ai_cull_bucket_for_result(result)] += 1
        return counts

    def remember_ai_review_summary(
        self,
        *,
        folder: str,
        report_dir: str,
        html_report_path: str,
        same_folder: bool,
        bundle: AIBundle | None = None,
    ) -> dict[str, object]:
        payload = {
            "folder": folder,
            "report_dir": report_dir,
            "html_report_path": html_report_path,
            "same_folder": same_folder,
            "bundle": bundle,
        }
        self._window._last_ai_review_summary = dict(payload)
        return payload

    def last_ai_review_summary_for_current_state(self) -> dict[str, object] | None:
        cached = getattr(self._window, "_last_ai_review_summary", None)
        if cached:
            return dict(cached)
        bundle = self._window._ai_bundle
        if bundle is None:
            return None

        report_dir = str(Path(bundle.export_csv_path).parent) if bundle.export_csv_path else bundle.source_path
        html_report_path = bundle.report_html_path
        source_path = bundle.source_path or report_dir or bundle.export_csv_path or html_report_path
        if source_path:
            try:
                source_details = inspect_ai_bundle_source(source_path)
            except (FileNotFoundError, ValueError, OSError):
                source_details = None
            if source_details is not None:
                report_dir = source_details.source_path
                if source_details.report_html_path:
                    html_report_path = source_details.report_html_path

        resolved_report_dir = Path(report_dir).expanduser() if report_dir else Path()
        derived_folder = ""
        if self._window._current_folder:
            derived_folder = self._window._current_folder
        elif report_dir:
            if resolved_report_dir.name.casefold() == "ranker_report":
                derived_folder = str(resolved_report_dir.parent.parent)
            elif resolved_report_dir.parent.name.casefold() == ".image_triage_ai":
                derived_folder = str(resolved_report_dir.parent.parent)
            else:
                derived_folder = str(resolved_report_dir.parent)
        if not derived_folder:
            return None

        same_folder = bool(
            self._window._current_folder
            and normalized_path_key(derived_folder) == normalized_path_key(self._window._current_folder)
        )
        return {
            "folder": derived_folder,
            "report_dir": report_dir,
            "html_report_path": html_report_path,
            "same_folder": same_folder,
            "bundle": bundle,
        }

    def show_last_ai_review_summary(self) -> None:
        payload = self.last_ai_review_summary_for_current_state()
        if payload is None:
            self._window.statusBar().showMessage("Run or load AI review results before reopening the summary.")
            return
        self.show_ai_review_complete_dialog(**payload)

    def show_ai_review_complete_dialog(
        self,
        *,
        folder: str,
        report_dir: str,
        html_report_path: str,
        same_folder: bool,
        bundle: AIBundle | None = None,
    ) -> None:
        if self._window._active_ai_task is None:
            self.close_ai_review_progress_dialog()
        # IMPORTANT: never call load_ai_bundle() here. This dialog runs on the
        # UI thread, and load_ai_bundle reads the bundle CSV synchronously —
        # on a UNC/NAS path that freezes the whole app. If the caller didn't
        # supply a pre-loaded bundle, show the dialog with empty bucket counts;
        # the async post-AI loader will populate the AI tab separately.
        resolved_bundle = bundle
        self.remember_ai_review_summary(
            folder=folder,
            report_dir=report_dir,
            html_report_path=html_report_path,
            same_folder=same_folder,
            bundle=resolved_bundle,
        )
        self.update_ai_toolbar_state()
        paths = build_ai_workflow_paths(folder)
        dialog = AIReviewCompleteDialog(
            folder=folder,
            hidden_root=str(paths.hidden_root),
            artifacts_dir=str(paths.artifacts_dir),
            report_dir=report_dir,
            export_csv_path=str(paths.ranked_export_path),
            report_html_path=html_report_path or str(paths.html_report_path),
            bucket_counts=self.ai_review_bucket_counts(resolved_bundle),
            same_folder=same_folder,
            parent=self._window,
        )
        self._window._exec_dialog_with_geometry(dialog, "ai_review_complete")

    def handle_ai_run_finished(self, folder: str, report_dir: str, html_report_path: str) -> None:
        logger = perf_logger()
        start = time.perf_counter() if logger.enabled else 0.0
        step_start = start

        def log_step(event: str, step_started: float, **fields: object) -> float:
            if not logger.enabled:
                return step_started
            now = time.perf_counter()
            logger.duration(
                event,
                (now - step_started) * 1000.0,
                folder=folder,
                report_dir=report_dir,
                same_folder=normalized_path_key(folder) == normalized_path_key(self._window._current_folder),
                **fields,
            )
            return now

        if not self.ai_run_signal_matches_active_task(folder):
            perf_logger().log("ai.run_signal_ignored", signal="finished", folder=folder, report_dir=report_dir)
            return
        if logger.enabled and self._active_ai_run_start_perf:
            logger.duration(
                "ai.run_ui_total",
                (time.perf_counter() - self._active_ai_run_start_perf) * 1000.0,
                folder=folder,
                report_dir=report_dir,
                phase="worker_finished_signal",
            )
        self._window._active_ai_task = None
        self.invalidate_ai_folder_probe_cache()
        embedding_cache_key = self._window._active_ai_embedding_cache_key
        cluster_cache_key = self._window._active_ai_cluster_cache_key
        report_cache_key = self._window._active_ai_report_cache_key
        semantic_cache_key = self._window._active_ai_semantic_cache_key
        if embedding_cache_key and cluster_cache_key and report_cache_key:
            paths = build_ai_workflow_paths(folder)
            self._window._catalog_repository.save_ai_workflow_cache(
                folder,
                embedding_cache_key=embedding_cache_key,
                cluster_cache_key=cluster_cache_key,
                report_cache_key=report_cache_key,
                artifacts_dir=str(paths.artifacts_dir),
                report_dir=str(paths.report_dir),
                semantic_cache_key=semantic_cache_key,
            )
            step_start = log_step(
                "ai.run_finished.catalog_save",
                step_start,
                embedding_key=embedding_cache_key,
                cluster_key=cluster_cache_key,
                report_key=report_cache_key,
                semantic_key=semantic_cache_key,
            )
        else:
            step_start = log_step("ai.run_finished.catalog_save", step_start, skipped=True)
        self._window._active_ai_embedding_cache_key = ""
        self._window._active_ai_cluster_cache_key = ""
        self._window._active_ai_report_cache_key = ""
        self._window._active_ai_semantic_cache_key = ""
        self._window._ai_stage_index = self._window._ai_stage_total
        self._window._ai_stage_message = "AI review complete"
        if self._window._ai_progress_total <= 0:
            self._window._ai_progress_total = 1
        self._window._ai_progress_current = self._window._ai_progress_total
        self._window._ai_progress_eta_text = ""
        self.close_ai_review_progress_dialog()
        same_folder = self.ai_run_signal_matches_current_folder(folder)

        if not same_folder:
            # Different visible folder: clean up the completed worker, but do
            # not mutate the current progress dialog or load foreign results.
            self.update_ai_toolbar_state()
            step_start = log_step("ai.run_finished.toolbar_state", step_start)
            if logger.enabled:
                logger.duration(
                    "ai.run_finished_handler",
                    (time.perf_counter() - start) * 1000.0,
                    folder=folder,
                    report_dir=report_dir,
                    same_folder=False,
                    loaded_results=False,
                )
            self.resume_deferred_background_review_work_after_ai(reason="finished")
            self._active_ai_run_start_perf = 0.0
            return

        # same_folder branch: kick the bundle load onto a worker so a slow
        # UNC/NAS path can't freeze the UI. The continuation handler runs
        # the tab switch + completion dialog once the bundle arrives.
        self._window._scan.refresh_winner_scores_for_current_folder()
        self._window._scan.refresh_face_records_for_current_folder()
        self._window._scan.refresh_image_categories_for_current_folder()
        self._window.statusBar().showMessage(
            f"AI review complete. Loading {Path(html_report_path).name}..."
        )
        if logger.enabled:
            logger.duration(
                "ai.run_finished_handler.async_load_kicked_off",
                (time.perf_counter() - start) * 1000.0,
                folder=folder,
                report_dir=report_dir,
                same_folder=True,
            )
        task = PostAIRunBundleLoadTask(
            folder=folder,
            report_dir=report_dir,
            html_report_path=html_report_path,
            catalog_db_path=self._window._catalog_repository.db_path,
        )
        task.signals.finished.connect(
            self.handle_post_ai_run_bundle_loaded, Qt.ConnectionType.QueuedConnection
        )
        task.signals.failed.connect(
            self.handle_post_ai_run_bundle_failed, Qt.ConnectionType.QueuedConnection
        )
        QThreadPool.globalInstance().start(task, -50)

    def handle_post_ai_run_bundle_loaded(
        self,
        folder: str,
        report_dir: str,
        html_report_path: str,
        bundle_obj: object,
        source_details_obj: object,
    ) -> None:
        logger = perf_logger()
        start = time.perf_counter() if logger.enabled else 0.0
        bundle = bundle_obj if isinstance(bundle_obj, AIBundle) else None
        same_folder = self.ai_run_signal_matches_current_folder(folder)
        if bundle is not None and same_folder:
            self._window._aiculler_ingested_cache_folder_key = ""
            self._window._aiculler_ingested_path_keys = set()
            self._window._aiculler_ingested_sibling_keys = set()
            self._window._ai_bundle = bundle
            self.recompute_ai_demoted_burst_paths()
            source_path = getattr(source_details_obj, "source_path", "") or report_dir
            if source_path:
                self._window._settings.setValue(self._window.AI_RESULTS_KEY, str(source_path))
            self.refresh_ai_state()
        if same_folder:
            self._window.statusBar().showMessage(
                f"AI review complete. Loaded {Path(html_report_path).name}"
            )
        else:
            self.resume_deferred_background_review_work_after_ai(reason="finished")
            self._active_ai_run_start_perf = 0.0
            return
        self.show_ai_review_complete_dialog(
            folder=folder,
            report_dir=report_dir,
            html_report_path=html_report_path,
            same_folder=same_folder,
            bundle=bundle,
        )
        if logger.enabled:
            logger.duration(
                "ai.run_finished.async_continuation",
                (time.perf_counter() - start) * 1000.0,
                folder=folder,
                report_dir=report_dir,
                same_folder=same_folder,
                loaded_results=bundle is not None,
            )
        self.resume_deferred_background_review_work_after_ai(reason="finished")
        self._active_ai_run_start_perf = 0.0

    def handle_post_ai_run_bundle_failed(
        self,
        folder: str,
        report_dir: str,
        html_report_path: str,
        error: str,
    ) -> None:
        same_folder = self.ai_run_signal_matches_current_folder(folder)
        if not same_folder:
            self.resume_deferred_background_review_work_after_ai(reason="finished_with_error")
            self._active_ai_run_start_perf = 0.0
            return
        self._window.statusBar().showMessage(f"AI review complete, but loading results failed: {error}")
        self.show_ai_review_complete_dialog(
            folder=folder,
            report_dir=report_dir,
            html_report_path=html_report_path,
            same_folder=same_folder,
            bundle=None,
        )
        self.resume_deferred_background_review_work_after_ai(reason="finished_with_error")
        self._active_ai_run_start_perf = 0.0

    def handle_ai_run_failed(self, folder: str, message: str) -> None:
        logger = perf_logger()
        if not self.ai_run_signal_matches_active_task(folder):
            logger.log("ai.run_signal_ignored", signal="failed", folder=folder, message=message)
            return
        if logger.enabled and self._active_ai_run_start_perf:
            logger.duration(
                "ai.run_ui_total",
                (time.perf_counter() - self._active_ai_run_start_perf) * 1000.0,
                folder=folder,
                phase="failed_signal",
            )
        logger.log("ai.run_failed", folder=folder, message=message)
        self._window._active_ai_task = None
        self.invalidate_ai_folder_probe_cache()
        self._active_ai_run_start_perf = 0.0
        self._window._active_ai_embedding_cache_key = ""
        self._window._active_ai_cluster_cache_key = ""
        self._window._active_ai_report_cache_key = ""
        self._window._active_ai_semantic_cache_key = ""
        self._window._ai_stage_message = "AI review failed"
        self._window._ai_progress_eta_text = ""
        self.update_ai_toolbar_state()
        same_folder = self.ai_run_signal_matches_current_folder(folder)
        if same_folder and self._ai_review_progress_dialog is not None:
            self._ai_review_progress_dialog.mark_failed("AI Review failed")
        if same_folder:
            QMessageBox.warning(self._window, "AI Review Failed", message)
            self._window.statusBar().showMessage("AI review failed")
        self.resume_deferred_background_review_work_after_ai(reason="failed")

    def handle_ai_run_cancelled(self, folder: str, message: str) -> None:
        logger = perf_logger()
        if not self.ai_run_signal_matches_active_task(folder):
            logger.log("ai.run_signal_ignored", signal="cancelled", folder=folder, message=message)
            return
        if logger.enabled and self._active_ai_run_start_perf:
            logger.duration(
                "ai.run_ui_total",
                (time.perf_counter() - self._active_ai_run_start_perf) * 1000.0,
                folder=folder,
                phase="cancelled_signal",
            )
        logger.log("ai.run_cancelled", folder=folder, message=message)
        self._window._active_ai_task = None
        self.invalidate_ai_folder_probe_cache()
        self._active_ai_run_start_perf = 0.0
        self._window._active_ai_embedding_cache_key = ""
        self._window._active_ai_cluster_cache_key = ""
        self._window._active_ai_report_cache_key = ""
        self._window._active_ai_semantic_cache_key = ""
        self._window._ai_stage_message = "AI review stopped"
        self._window._ai_progress_current = 0
        self._window._ai_progress_total = 1
        self._window._ai_progress_eta_text = ""
        self.update_ai_toolbar_state()
        same_folder = self.ai_run_signal_matches_current_folder(folder)
        if same_folder and self._ai_review_progress_dialog is not None:
            self._ai_review_progress_dialog.mark_finished(message or "AI Review stopped")
        if same_folder:
            self._window.statusBar().showMessage(message or "AI review stopped")
        self.resume_deferred_background_review_work_after_ai(reason="cancelled")

    def set_ai_status_visible(self, visible: bool) -> None:
        visible = bool(visible)
        if self._window._ai_status_visible == visible:
            return
        self._window._ai_status_visible = visible
        self._window.ai_status_widget.setVisible(visible)
        self._window._toolbar.schedule_workspace_toolbar_overflow_update("ai")

    def sync_ai_status_visibility(self, *, active: bool, message: str) -> None:
        terminal_messages = {
            "AI review complete",
            "AI review failed",
            "AI review stopped",
            "Reused cached AI results",
        }
        if active:
            self._ai_status_terminal_notice_key = ""
            self._window._ai_status_hide_timer.stop()
            self.set_ai_status_visible(True)
            return
        if message in terminal_messages:
            if self._ai_status_terminal_notice_key != message:
                self._ai_status_terminal_notice_key = message
                self.set_ai_status_visible(True)
                self._window._ai_status_hide_timer.start()
            return
        self._ai_status_terminal_notice_key = ""
        if not self._window._ai_status_hide_timer.isActive():
            self.set_ai_status_visible(False)

    def refresh_ai_progress_bar(self) -> None:
        if self._window._active_ai_task is not None:
            if self._window._ai_progress_total > 0:
                total = max(1, self._window._ai_progress_total)
                value = min(max(self._window._ai_progress_current, 0), total)
                self._window.ai_progress_bar.setRange(0, total)
                self._window.ai_progress_bar.setValue(value)
                self._window.ai_progress_bar.setFormat(f"{value}/{total}")
            else:
                self._window.ai_progress_bar.setRange(0, 0)
                self._window.ai_progress_bar.setFormat("")
                self._window.ai_progress_bar.setValue(0)
            self._window.ai_progress_bar.setToolTip(self._window._ai_stage_message)
            return

        if self._window._ai_stage_message == "AI review complete":
            total = max(1, self._window._ai_progress_total)
            self._window.ai_progress_bar.setRange(0, total)
            self._window.ai_progress_bar.setValue(total)
            self._window.ai_progress_bar.setFormat("Done")
        elif self._window._ai_stage_message == "AI review stopped":
            self._window.ai_progress_bar.setRange(0, 1)
            self._window.ai_progress_bar.setValue(0)
            self._window.ai_progress_bar.setFormat("Stopped")
        elif self._window._ai_stage_message == "AI review failed":
            self._window.ai_progress_bar.setRange(0, 1)
            self._window.ai_progress_bar.setValue(0)
            self._window.ai_progress_bar.setFormat("Failed")
        else:
            self._window.ai_progress_bar.setRange(0, 1)
            self._window.ai_progress_bar.setValue(0)
            self._window.ai_progress_bar.setFormat("Idle")
        self._window.ai_progress_bar.setToolTip(self._window._ai_stage_message)

    def build_ai_progress_text(self) -> str:
        if self._window._active_ai_task is None:
            return self._window._ai_stage_message

        parts = [self._window._ai_stage_message]
        if self._window._ai_progress_total > 0:
            parts.append(f"{self._window._ai_progress_current}/{self._window._ai_progress_total}")
        if self._window._ai_progress_eta_text:
            parts.append(f"{self._window._ai_progress_eta_text} left")
        return " | ".join(parts)

    def update_ai_summary(self) -> None:
        self._window.summary_ai.setText(self._window._summary_ai_text)
        self._window.summary_ai.setToolTip(self._window._summary_ai_tooltip)

    def refresh_ai_summary_cache(self) -> None:
        if self._window._ai_bundle is None:
            self._window._summary_ai_text = "AI: Off"
            self._window._summary_ai_tooltip = "No AI export is currently loaded."
            return

        total_records = len(self._window._all_records)
        matched = self._window._ai_bundle.count_matches(self._window._all_records)
        source_name = Path(self._window._ai_bundle.export_csv_path).stem
        if total_records:
            self._window._summary_ai_text = f"AI: {matched}/{total_records} matched"
        else:
            self._window._summary_ai_text = f"AI: {source_name}"

        tooltip_lines = [
            f"Source: {self._window._ai_bundle.source_path}",
            f"Export: {self._window._ai_bundle.export_csv_path}",
        ]
        bucket_counts = {
            AIConfidenceBucket.OBVIOUS_WINNER: 0,
            AIConfidenceBucket.LIKELY_KEEPER: 0,
            AIConfidenceBucket.NEEDS_REVIEW: 0,
            AIConfidenceBucket.LIKELY_REJECT: 0,
        }
        if self._window._all_records:
            for record in self._window._all_records:
                result = self.ai_result_for_record(record)
                if result is None:
                    continue
                bucket_counts[result.confidence_bucket] = bucket_counts.get(result.confidence_bucket, 0) + 1
            tooltip_lines.append(
                "Buckets: "
                + ", ".join(
                    [
                        f"winners {bucket_counts[AIConfidenceBucket.OBVIOUS_WINNER]}",
                        f"keepers {bucket_counts[AIConfidenceBucket.LIKELY_KEEPER]}",
                        f"review {bucket_counts[AIConfidenceBucket.NEEDS_REVIEW]}",
                        f"rejects {bucket_counts[AIConfidenceBucket.LIKELY_REJECT]}",
                    ]
                )
            )
        if self._window._ai_bundle.report_html_path:
            tooltip_lines.append(f"Report: {self._window._ai_bundle.report_html_path}")
        self._window._summary_ai_tooltip = "\n".join(tooltip_lines)

    def ai_result_for_record(self, record: ImageRecord | None, *, preferred_path: str | None = None):
        if record is None or self._window._ai_bundle is None:
            return None
        result = self.raw_ai_result_for_record(record, preferred_path=preferred_path)
        return self._window._aiculler.apply_user_label_override(result, record)

    def raw_ai_result_for_record(self, record: ImageRecord | None, *, preferred_path: str | None = None):
        if record is None or self._window._ai_bundle is None:
            return None
        result = find_ai_result_for_record(self._window._ai_bundle, record, preferred_path=preferred_path)
        refined = refine_ai_result_with_review_insight(result, self._window._inspector.review_insight_for_record(record))
        return self.apply_burst_dedup_to_ai_result(refined, record)

    def apply_burst_dedup_to_ai_result(self, result, record):
        """Demote non-best frames in a visually similar burst to LIKELY_REJECT.

        We deliberately removed cluster-context from bucket classification so
        each image is judged on its own folder percentile — but that means
        multiple frames from the same burst can all hit the Keeper threshold.
        This post-pass uses the demote set computed by
        _recompute_ai_demoted_burst_paths() to override the bucket to Reject
        for everything except the best-scoring frame of each burst.
        """

        if result is None or not self._ai_demoted_burst_paths:
            return result
        if _memory_path_key(record.path) not in self._ai_demoted_burst_paths:
            return result
        from .ai_results import AIConfidenceBucket, _combine_confidence_summaries, _replace_confidence
        summary = _combine_confidence_summaries(
            getattr(result, "confidence_summary", ""),
            "Demoted because a stronger frame in the same burst already passes as Winner.",
        )
        return _replace_confidence(result, AIConfidenceBucket.LIKELY_REJECT, summary)

    def recompute_ai_demoted_burst_paths(self) -> None:
        """Rebuild the demote set from the current bundle + review intelligence.

        For each review group with more than one member, pick the highest-
        scoring member (by bundle score). Every OTHER member of the group goes
        into the demote set and will be force-rejected by _ai_result_for_record.
        Called whenever bundle or review_intelligence changes."""

        demoted: set[str] = set()
        bundle = self._window._ai_bundle
        review = self._window._review_intelligence
        if (
            bundle is None
            or bundle.results_by_path is None
            or review is None
            or not review.groups
        ):
            self._ai_demoted_burst_paths = demoted
            return

        def _score_for(path: str) -> float:
            insight_path = bundle.results_by_path.get(path) or bundle.results_by_path.get(normalized_path_key(path))
            if insight_path is None:
                return -1.0
            return float(getattr(insight_path, "score", 0.0) or 0.0)

        for group in review.groups:
            members = [str(p) for p in (group.member_paths or ()) if p]
            if len(members) <= 1:
                continue
            best_path = max(members, key=_score_for)
            for member in members:
                if member == best_path:
                    continue
                demoted.add(_memory_path_key(member))
        self._ai_demoted_burst_paths = demoted

    def ai_result_for_record_memory(self, record: ImageRecord | None, *, preferred_path: str | None = None):
        if record is None or self._window._ai_bundle is None:
            return None
        fast_results = self._window._ai_bundle.results_by_fast_path
        if not fast_results:
            return self.ai_result_for_record(record, preferred_path=preferred_path)

        seen_keys: set[str] = set()
        candidate_paths: list[str] = []
        if preferred_path:
            candidate_paths.append(preferred_path)
        candidate_paths.extend(record.stack_paths)
        for path in candidate_paths:
            if not path:
                continue
            key = _memory_path_key(str(path))
            if key in seen_keys:
                continue
            seen_keys.add(key)
            result = fast_results.get(key)
            if result is not None:
                return refine_ai_result_with_review_insight(result, self._window._inspector.review_insight_for_record(record))
        return None

    def details_ai_text_for_record(self, record: ImageRecord) -> str:
        if record is None or record.is_folder:
            return "-"
        ai_result = self.ai_result_for_record(record, preferred_path=record.path)
        if ai_result is None:
            return "-"
        parts = [ai_result.confidence_bucket_short_label or ai_result.confidence_bucket_label]
        category = str(getattr(ai_result, "primary_category", "") or "").strip()
        if category and category != "uncategorized":
            parts.append(self.category_display_label(category))
        score = ai_result.display_score_text
        if score:
            parts.append(score)
        face_count = len(self._window._inspector.face_records_for_record(record))
        if face_count:
            parts.append(f"{face_count} face{'s' if face_count != 1 else ''}")
        if ai_result.group_size > 1:
            parts.append(ai_result.rank_text)
        return " | ".join(part for part in parts if part) or "-"

    def ai_result_for_index(self, index: int):
        record = self._window._record_at(index)
        if record is None:
            return None
        preferred_path = self._window.grid.displayed_variant_path(index) if record.has_variant_stack else record.path
        return self.ai_result_for_record(record, preferred_path=preferred_path)

    def is_unreviewed_record(self, record: ImageRecord) -> bool:
        annotation = self._window._annotations.get(record.path, SessionAnnotation())
        return not annotation.winner and not annotation.reject

    def find_next_ai_index(self, *, top_pick_only: bool = False, unreviewed_only: bool = False) -> int | None:
        if not self._window._records:
            return None

        start_index = self._window.grid.current_index()
        if start_index < 0:
            start_index = -1

        total = len(self._window._records)
        for offset in range(1, total + 1):
            index = (start_index + offset) % total
            record = self._window._record_at(index)
            ai_result = self.ai_result_for_index(index)
            if record is None or ai_result is None:
                continue
            if top_pick_only and not ai_result.is_top_pick:
                continue
            if unreviewed_only and not self.is_unreviewed_record(record):
                continue
            return index
        return None

    def jump_to_next_ai_top_pick(self, *, unreviewed_only: bool = False) -> None:
        if self._window._ai_bundle is None:
            self._window.statusBar().showMessage("Load AI results first to jump between AI picks")
            return

        index = self.find_next_ai_index(top_pick_only=True, unreviewed_only=unreviewed_only)
        if index is None:
            if unreviewed_only:
                self._window.statusBar().showMessage("No unreviewed AI top picks are visible in the current view")
            else:
                self._window.statusBar().showMessage("No AI top picks are visible in the current view")
            return

        self._window.grid.set_current_index(index)
        record = self._window._record_at(index)
        if record is not None:
            label = "unreviewed AI top pick" if unreviewed_only else "AI top pick"
            self._window.statusBar().showMessage(f"Jumped to {label}: {record.name}")

    def visible_ai_group_rows(self, group_id: str) -> list[tuple[int, ImageRecord, object]]:
        rows: list[tuple[int, ImageRecord, object]] = []
        for index, record in enumerate(self._window._records):
            ai_result = self.ai_result_for_index(index)
            if ai_result is None or ai_result.group_size <= 1 or ai_result.group_id != group_id:
                continue
            rows.append((index, record, ai_result))
        rows.sort(key=lambda item: (item[2].rank_in_group, -item[2].score, item[1].name.casefold()))
        return rows

    def jump_to_ai_top_pick_in_group(self, index: int | None = None) -> None:
        if self._window._ai_bundle is None:
            self._window.statusBar().showMessage("Load AI results first to jump within AI groups")
            return

        if index is None:
            index = self._window.grid.current_index()
        current_ai = self.ai_result_for_index(index)
        if current_ai is None or current_ai.group_size <= 1:
            self._window.statusBar().showMessage("The current image does not belong to a multi-image AI group")
            return

        group_rows = self.visible_ai_group_rows(current_ai.group_id)
        if not group_rows:
            self._window.statusBar().showMessage("The current AI group is not visible in this view")
            return

        top_index = group_rows[0][0]
        self._window.grid.set_current_index(top_index)
        top_record = group_rows[0][1]
        if len(group_rows) < current_ai.group_size:
            self._window.statusBar().showMessage(
                f"Jumped to AI top pick: {top_record.name} ({len(group_rows)}/{current_ai.group_size} group images visible)"
            )
        else:
            self._window.statusBar().showMessage(f"Jumped to AI top pick: {top_record.name}")

    def open_current_ai_group_compare(self, index: int | None = None) -> None:
        if self._window._ai_bundle is None:
            self._window.statusBar().showMessage("Load AI results first to compare AI groups")
            return

        if index is None:
            index = self._window.grid.current_index()
        current_record = self._window._record_at(index)
        current_ai = self.ai_result_for_index(index)
        if current_record is None or current_ai is None or current_ai.group_size <= 1:
            self._window.statusBar().showMessage("The current image does not belong to a multi-image AI group")
            return

        group_rows = self.visible_ai_group_rows(current_ai.group_id)
        if len(group_rows) < 2:
            visible_count = len(group_rows)
            if visible_count == 1 and current_ai.group_size > 1:
                self._window.statusBar().showMessage(
                    f"Only 1/{current_ai.group_size} AI group images are visible. Switch View to All to compare the full group."
                )
            else:
                self._window.statusBar().showMessage("Not enough AI group images are visible to open compare")
            return

        entries: list[PreviewEntry] = []
        focused_slot = 0
        for slot, (item_index, record, ai_result) in enumerate(group_rows):
            annotation = self._window._annotations.get(record.path, SessionAnnotation())
            displayed_path = self._window.grid.displayed_variant_path(item_index) if record.has_variant_stack else self._window._preview_ctl.preview_source_path(record)
            edited_candidates = self._window._preview_ctl.ordered_edited_candidates(record, displayed_path)
            edited_path = edited_candidates[0] if edited_candidates else ""
            label = ai_result.rank_text if ai_result.group_size > 1 else ""
            if record.path == current_record.path:
                focused_slot = slot
            entries.append(
                PreviewEntry(
                    record=record,
                    source_path=displayed_path,
                    winner=annotation.winner,
                    reject=annotation.reject,
                    rating=annotation.rating,
                    edited_path=edited_path,
                    edited_candidates=tuple(edited_candidates),
                    label=f"AI {label}" if label else "AI",
                    ai_result=ai_result,
                    review_summary=self._window._inspector.review_summary_for_record(record),
                    workflow_summary=self._window._inspector.workflow_summary_for_record(record),
                    workflow_details=self._window._inspector.workflow_detail_lines_for_record(record),
                    placeholder_image=self._window._preview_ctl.preview_placeholder_for_index(item_index),
                )
            )

        self._window._compare_enabled = True
        if self._window.actions is not None:
            with QSignalBlocker(self._window.actions.compare_mode):
                self._window.actions.compare_mode.setChecked(True)
            self._window._toolbar.sync_topbar_action_buttons()
        self._window.preview.set_compare_mode(True)
        self._window._compare_count = len(entries)
        self._window._manual_compare_count = len(entries)
        self._window.preview.set_compare_count(len(entries))
        self._window.preview.show_entries(entries)
        self._window.preview._set_focused_slot(focused_slot)

        if len(group_rows) < current_ai.group_size:
            self._window.statusBar().showMessage(
                f"Opened AI group compare ({len(group_rows)}/{current_ai.group_size} visible in current view)"
            )
        else:
            self._window.statusBar().showMessage(f"Opened AI group compare: {current_ai.group_id}")

    def ai_review_tags_markdown(self) -> str:
        lines = [f"- **{label}**: {description}" for label, description in ai_review_tag_definitions()]
        return "\n".join(lines)
