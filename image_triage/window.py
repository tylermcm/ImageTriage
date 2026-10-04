from __future__ import annotations

"""Main desktop window and task orchestration for Image Triage.

This module is intentionally the highest-level coordinator in the application.
It wires together folder loading, persistent settings, review state, AI tasks,
workflow execution, dock layout, and command routing. The file is large because
it owns the user-facing control flow, but the surrounding modules are expected
to hold the reusable backend logic whenever a behavior can be isolated cleanly.
"""

import ctypes
import ctypes.wintypes
import csv
import json
import logging
import os
import re
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
import uuid
from collections import Counter, deque
from collections.abc import Callable
from dataclasses import replace
from hashlib import sha1
from pathlib import Path
from queue import Empty, SimpleQueue
from textwrap import dedent

import numpy as np
from PySide6.QtCore import (
    QByteArray,
    QDir,
    QEasingCurve,
    QEvent,
    QEventLoop,
    QFileSystemWatcher,
    QMimeData,
    QModelIndex,
    QPoint,
    QPropertyAnimation,
    QRect,
    QRunnable,
    QSignalBlocker,
    QSize,
    QStandardPaths,
    Qt,
    QThreadPool,
    QTimer,
    QUrl,
    Slot,
)
from PySide6.QtGui import QAction, QActionGroup, QColor, QCloseEvent, QCursor, QFont, QGuiApplication, QIcon, QImage, QKeySequence, QPainter, QPen, QPixmap, QShortcut, QTransform
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QFileDialog,
    QFileSystemModel,
    QGraphicsDropShadowEffect,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QProgressBar,
    QProgressDialog,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSpacerItem,
    QStackedWidget,
    QStatusBar,
    QToolButton,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from aiculler.telemetry import TelemetryEvent, ThreadedTelemetryLogger, classify_override, normalize_bucket

from .ai_model import (
    AIModelInstallation,
    DEFAULT_AICULLER_CLIP_VARIANT,
    DEFAULT_AICULLER_CLIP_SIZE_MB,
    DEFAULT_AICULLER_FACE_SIZE_MB,
    DEFAULT_AICULLER_TOPIQ_SIZE_MB,
    resolve_aiculler_clip_model_installation,
    resolve_aiculler_face_model_installation,
    resolve_aiculler_topiq_model_installation,
    resolve_semantic_model_installation,
)
from .ai_runtime_packages import (
    AI_RUNTIME_CPU_VARIANT,
    AI_RUNTIME_GPU_VARIANT,
    AIRuntimeInstallationStatus,
    ai_runtime_variant_label,
    directory_size_bytes,
    estimate_ai_runtime_download_size_mb,
    estimate_ai_runtime_installed_size_mb,
    load_ai_runtime_installation_status,
)
from .archive_ops import (
    EXTRACT_ARCHIVE_FILTER,
    CreateArchiveTask,
    ExtractArchiveTask,
    archive_format_for_key,
    ensure_archive_suffix,
)
from .annotation_queue import AnnotationPersistenceQueue, WinnerSyncRequest
from .app_identity import migrate_legacy_settings_once, user_settings
from .ai_training import (
    normalize_ranker_profile,
    prepare_hidden_ai_training_workspace,
    suggest_training_profile,
)
from .ai_workflow import (
    ai_device_environment_override,
    ai_report_artifacts_ready,
    ai_semantic_artifacts_ready,
    build_ai_workflow_paths,
    default_ai_workflow_runtime,
    existing_hidden_ai_report_dir,
    reset_hidden_ai_review_cache,
)
from .ai_workflow_center import AIWorkflowCenterDialog
from .aiculler_workflow import (
    AICullerRunTask,
    WINNER_SCORE_FALLBACK_MODEL_VERSION,
    aiculler_db_path,
    aiculler_rerank_readiness,
    aiculler_runtime_available,
    build_aiculler_workflow_paths,
    default_aiculler_runtime,
    latest_adapter_model_version,
    load_adapter_review_candidates,
    load_adapter_status_summary,
    load_face_records_by_path,
    load_image_categories_by_path,
    load_latest_winner_scores,
)
from .aiculler_global_store import GlobalAdapterLabelStore, default_global_adapter_label_store_path
from .ai_results import (
    AIBundle,
    AICullBucket,
    AIConfidenceBucket,
    ai_cull_bucket_for_result,
    ai_manual_cull_sort_key,
    ai_review_tag_definitions,
    find_ai_result_for_record,
    inspect_ai_bundle_source,
    iter_ai_bundle_results,
    load_ai_bundle,
    refine_ai_result_with_review_insight,
    set_cull_thresholds,
)
from .batch_rename import BatchRenamePreview
from .batch_rename_controller import BatchRenameApplyController, BatchRenameExecutionContext
from .catalog_controller import CatalogController, CatalogExecutionContext
from .command_palette_controller import CommandPaletteController
from .folder_ops_controller import FolderOpsController
from .folder_session import FolderSession, session_field
from .appearance_controller import AppearanceController
from .toolbar_controller import ToolbarController
from .zen_controller import ZenController
from .tool_mode_controller import ToolModeController
from .ai_run_controller import AiRunController
from .aiculler_controller import AiCullerController
from .ai_setup_controller import AiSetupController
from .record_ops_controller import RecordOpsController, UndoAction
from .records_repository import RecordsRepository
from .records_view_controller import RecordsViewController, UnifiedSearchTask, _memory_path_key
from .recycle_bin_controller import RecycleBinController
from .brackets import BracketDetector
from .bursts import find_burst_groups
from .catalog import CatalogRepository, catalog_cache_env_override
from .decision_store import DecisionStore
from .details_view import PhotoDetailsView
from .prefilter_common import PrefilterDecision
from .phash_prefilter import (
    PHashPrefilterSettings,
    build_phash_prefilter_paths,
    default_phash_prefilter_settings,
    load_phash_prefilter_decisions,
)
from .file_ops import (
    FileMove,
    is_unc_path,
    record_paths,
    unique_destination,
)
from .filtering import (
    AIStateFilter,
    FileTypeFilter,
    RecordFilterQuery,
    ReviewStateFilter,
    SavedFilterPreset,
    deserialize_saved_filter_preset,
    serialize_saved_filter_preset,
)
from .formats import FITS_SUFFIXES, IMAGE_SUFFIXES, MODEL_SUFFIXES, RAW_SUFFIXES, suffix_for_path
from .grid import BurstVisualInfo, GridDeltaUpdate, ThumbnailGridView
from .image_convert import ConvertApplyTask, ConvertOptions, ConvertPlan, ConvertSourceItem
from .image_resize import ResizeApplyTask, ResizeOptions, ResizePlan, ResizeSourceItem
from .job_controller import JobController, JobSpec
from .library_store import (
    CatalogRefreshTask,
    LibraryStore,
    VirtualCollection,
)
from .metadata import CaptureMetadata, MetadataManager
from .models import DeleteMode, FilterMode, ImageRecord, ImageVariant, JPEG_SUFFIXES, SessionAnnotation, SortMode, WinnerMode
from .perceptual_hash import hamming_distance_int
from .perf import perf_logger, performance_log_dir
from .preview import FullScreenPreview, PreviewEntry
from .ui import preview_studio
from .workflows import (
    BEST_OF_BALANCED,
    BEST_OF_TOP_N,
    RECIPE_CONTENT_BUNDLE,
    RECIPE_TRANSFER_ARCHIVE,
    RECIPE_TRANSFER_MOVE,
    BestOfSetPlan,
    WorkflowExportPlan,
    WorkflowExportTask,
    WorkflowRecipe,
    WorkspacePreset,
    build_best_of_set_plan,
    build_workflow_export_plan,
    built_in_workflow_recipes,
    built_in_workspace_presets,
    dump_saved_workflow_recipes,
    dump_saved_workspace_presets,
    load_saved_workflow_recipes,
    load_saved_workspace_presets,
    recipe_key_for_name,
    workflow_archive_path,
    workflow_destination_dir,
    workflow_record_folder_name,
)
from .review_tools import InspectionStats
from .records_view_cache import RecordsViewCache, ViewInvalidationReason
from .review_intelligence import BuildReviewIntelligenceTask, ReviewIntelligenceBundle
from .review_workflows import (
    AI_DISAGREEMENT_SOURCE_MODE,
    BurstRecommendation,
    RecordWorkflowInsight,
    TasteProfile,
    ai_strength,
    build_burst_recommendations,
    build_pairwise_label_payload,
    build_record_workflow_insight,
    current_timestamp,
    ai_disagreement_group_leader_path,
    disagreement_level_for,
)
from . import path_policy
from .scanner import (
    FolderModifiedCheckTask,
    FolderScanTask,
    PathReachableTask,
    normalize_filesystem_path,
    normalized_path_key,
    scan_child_folders,
    scan_folder,
)
from .settings_dialog import WorkflowPreset, WorkflowSettingsDialog
from .semantic_index import SemanticFolderIndexTask
from .face_index import FaceFolderIndexTask
from .semantic_sort import load_semantic_classifications, semantic_classification_for_record, semantic_folder_name
from .shell_actions import detect_photoshop_executable, open_in_file_explorer, open_in_photoshop, open_with_default, open_with_dialog, reveal_in_file_explorer
from .thumbnails import ThumbnailManager
from .updater import UpdateCheckResult, UpdateInfo, current_app_version, launch_update_installer_and_restart
from .ui import (
    AdvancedFilterDialog,
    AIReviewProgressDialog,
    AppearanceMode,
    ApplyAIDecisionsDialog,
    BatchRenameDialog,
    BestOfSetDialog,
    CatalogSearchDialog,
    CollectionEditDialog,
    CommandPaletteDialog,
    ConvertDialog,
    FileAssociationsDialog,
    GuidedAICullPreferencesDialog,
    GuidedCullPreferences,
    HandoffBuilderDialog,
    HelpMarkdownDialog,
    InspectorPanel,
    MainWindowActions,
    PeopleSearchDialog,
    ResizeDialog,
    SHORTCUT_REGISTRY,
    ToolbarMenuController,
    WORKSPACE_METRICS,
    WorkspaceDocks,
    apply_gamma,
    apply_shortcut_overrides,
    appearance_mode_label,
    effective_shortcuts,
    load_shortcut_overrides,
    normalize_ui_gamma,
    save_shortcut_overrides,
    build_app_palette,
    build_app_stylesheet,
    build_main_menu_bar,
    build_main_window_actions,
    build_pin_icon,
    build_workspace_docks,
    clear_window_layout,
    default_theme,
    format_action_tooltip,
    fit_window_to_available_geometry,
    parse_appearance_mode,
    restore_window_layout,
    resolve_theme,
    save_window_layout,
    show_paged_help,
)
from .ui.busy_overlay import BusyOverlay
from .ui.display_metrics import (
    DisplayProfile,
    STANDARD_DISPLAY,
    display_profile_for_preference,
    normalize_display_profile_preference,
)
from .ui.backdrop import paint_backdrop, theme_has_backdrop
from .ui.breadcrumb import BreadcrumbBar
from .pocketdrop import PocketDropPanel
from .ui import layout_ratios
from .ui.nav_rail import ICON_PX as NAV_RAIL_ICON_PX, NavRail
from .ui.sections import SectionHeader
from .ui.face_groups import FaceGroupsPanel, face_group_photo_paths, load_face_groups
from .ui.help_topics import library_help_pages
from .ui.prototype_style import (
    NAV_ICON_ASSETS,
    FolderTreeView,
    folder_icon_pixmap,
    library_icon_pixmap,
    nav_icon_pixmap,
    pocketdrop_icon_pixmap,
    sidebar_people_icon_pixmap,
    sidebar_projects_icon_pixmap,
    rail_tool_pixmap,
    tool_icon_mark,
    trim_to_alpha,
)
from .xmp import load_sidecar_annotation
from .tasks.ai_tasks import AIModelDownloadRequest, AIModelDownloadTask, AIRuntimeInstallTask, AISetupSelection, AIUninstallTask, HiddenAIResultsLoadTask, PostAIRunBundleLoadTask, _AIFolderProbeTask, _PrefilterDecisionsTask, _compute_ai_folder_probe, _unknown_ai_folder_probe
from .tasks.annotation_tasks import AnnotationHydrationTask, InspectorStatsRequest, InspectorStatsTask, ScopeEnrichmentTask
from .tasks.job_contexts import ArchiveExecutionContext, ConvertExecutionContext, ResizeExecutionContext, WorkflowExecutionContext
from .tasks.update_tasks import AppUpdateCheckTask, AppUpdateDownloadTask
from .ui.ai_review_dialogs import AIReviewCompleteDialog
from .ui.directory_suggestions import _DirectorySuggestionController
from .ui.topbar_sync import _TopbarActionSync


# TEMPORARY: the window is translucent so it can be laid over the design
# reference while the layout ratios are tuned. Set this back to 1.0 when done;
# IMAGE_TRIAGE_OPACITY overrides it without editing (e.g. 1 for opaque).
WINDOW_OPACITY = 1


def _window_opacity() -> float:
    raw = os.environ.get("IMAGE_TRIAGE_OPACITY")
    try:
        value = float(raw) if raw else WINDOW_OPACITY
    except ValueError:
        value = WINDOW_OPACITY
    return min(1.0, max(0.1, value))


# Beyond this the Collections section scrolls rather than growing, so it can
# never crowd the folder tree out of the sidebar.
_MAX_VISIBLE_PROJECT_ROWS = 6
_PROJECT_ROW_PX = 34
# The empty row's stylesheet has a 32px minimum plus 3px vertical padding on
# each side. Its viewport must include that full 38px box or Qt clips glyphs.
_PROJECT_EMPTY_ROW_PX = 38
_NAV_SECTION_GAP_PX = 8
_PROJECT_HEADER_BODY_GAP_PX = 0





_POCKETDROP_EDITED_EXPORT_MAX_AGE_SECONDS = 24 * 60 * 60


def _pocketdrop_edited_export_dir() -> Path:
    """Scratch folder for edited-render temp files handed to PocketDrop.

    Sits next to PocketDrop's existing pasted-image scratch folder under the
    app's own AppData root (see pocketdrop.panel._pasted_images_dir), not a
    bare system temp dir, so it is easy to find and sweep.
    """

    from .scan_cache import app_data_root

    folder = app_data_root() / "PocketDrop" / "EditedExports"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _cleanup_pocketdrop_edited_exports() -> None:
    """Best-effort sweep of stale rendered-for-PocketDrop temp files.

    WI-5.3 (D3): PocketDrop's native send (``pd_add_paths``) is fire-and-forget
    -- the host gets no completion callback for an individual transfer (see
    ``pocketdrop/panel.py``), so a temp file written for an edited send cannot
    be deleted right after the call without risking deleting it before the
    transfer has actually read it. Rather than guess at a completion signal
    that does not exist, temp files live in their own scratch subfolder and
    are swept here for anything older than a day -- comfortably longer than
    any local transfer should take -- called opportunistically (startup and
    each PocketDrop send), never blocking on it.
    """

    try:
        folder = _pocketdrop_edited_export_dir()
        cutoff = time.time() - _POCKETDROP_EDITED_EXPORT_MAX_AGE_SECONDS
        for path in folder.iterdir():
            try:
                if path.is_file() and path.stat().st_mtime < cutoff:
                    path.unlink()
            except OSError:
                continue
    except OSError:
        pass


_logger = logging.getLogger(__name__)


class MainWindow(QMainWindow):
    """Top-level application window.

    The main window coordinates three kinds of work:

    - synchronous UI state such as menus, docks, toolbar layout, and selection
    - asynchronous backend tasks such as scans, thumbnailing, AI runs, and archives
    - persistence surfaces such as settings, catalog cache, review state, and
      workflow/toolbar customization

    Most feature modules report into this class, so the class docstring is the
    fast way to understand where user actions eventually land.
    """
    ADAPTER_REASON_TAGS: tuple[tuple[str, str], ...] = (
        ("composition", "Composition"),
        ("light_color", "Light / Color"),
        ("subject_expression", "Subject / Expression"),
        ("story_emotion", "Story / Emotion"),
        ("unique_scene", "Unique Scene"),
        ("technical_failure", "Technical Failure"),
        ("boring_repetitive", "Boring / Repetitive"),
        ("duplicate", "Duplicate"),
    )
    ADAPTER_REASON_TARGET_LABELS: frozenset[str] = frozenset({"hero", "portfolio", "reject", "bad", "r", "no", "0"})

    LAST_FOLDER_KEY = "window/last_folder"
    AI_RESULTS_KEY = "window/ai_results_path"
    AUTO_BRACKET_KEY = "window/auto_bracket_compare"
    APPEARANCE_KEY = "window/appearance"
    # One-shot switch of existing installs onto the Slate default.
    APPEARANCE_SLATE_MIGRATION_KEY = "window/appearance_slate_default"
    TOOLBAR_PLACEMENT_KEY = "ui/toolbar_placement"
    TOOLBAR_PLACEMENTS = ("floating", "docked")
    # The floating bar's bottom margin, inner padding, row gap and fade are
    # FLOATING_TOOLBAR_*_H in layout_ratios.py. This one stays: it only caps the
    # bar's width against a narrow grid and never moves it (the bar is centred).
    FLOATING_TOOLBAR_SIDE_MARGIN = 75
    UI_GAMMA_KEY = "view/ui_gamma"
    INTERFACE_SIZE_KEY = "view/interface_size"
    GEOMETRY_KEY = "window/geometry"
    STATE_KEY = "window/state"
    SESSION_KEY = "workflow/session"
    WINNER_MODE_KEY = "workflow/winner_mode"
    DELETE_MODE_KEY = "workflow/delete_mode"
    WORKFLOW_PRESETS_KEY = "workflow/presets"
    CATALOG_CACHE_ENABLED_KEY = "catalog/cache_enabled"
    CATALOG_WATCH_CURRENT_FOLDER_KEY = "catalog/watch_current_folder"
    RESTORE_FOLDER_POSITION_KEY = "view/restore_folder_position"
    AI_EMBED_BATCH_SIZE_KEY = "ai/embed_batch_size"
    AI_REVIEW_DETAIL_PROGRESS_KEY = "ai/review_detail_progress"
    AI_DISPUTE_WEIGHT_KEY = "ai/dispute_weight"
    AI_DISPUTE_WEIGHT_DEFAULT = 3
    AI_DISPUTE_WEIGHT_MIN = 2
    AI_DISPUTE_WEIGHT_MAX = 5
    AI_KEEP_TOP_PERCENT_KEY = "ai/keep_top_percent"
    AI_KEEP_TOP_PERCENT_DEFAULT = 10
    AI_KEEP_TOP_PERCENT_MIN = 1
    AI_KEEP_TOP_PERCENT_MAX = 50
    AI_REVIEW_BAND_PERCENT_KEY = "ai/review_band_percent"
    AI_REVIEW_BAND_PERCENT_DEFAULT = 10
    AI_REVIEW_BAND_PERCENT_MIN = 0
    AI_REVIEW_BAND_PERCENT_MAX = 30
    AI_BASE_SCORE_WEIGHT_PERCENT_KEY = "ai/base_score_weight_percent"
    AI_BASE_SCORE_WEIGHT_PERCENT_DEFAULT = 65
    AI_BASE_SCORE_WEIGHT_PERCENT_MIN = 0
    AI_BASE_SCORE_WEIGHT_PERCENT_MAX = 100
    PHASH_PREFILTER_ENABLED_KEY = "ai/phash_prefilter/enabled"
    PHASH_PREFILTER_HAMMING_THRESHOLD_KEY = "ai/phash_prefilter/hamming_threshold"
    PHASH_PREFILTER_CACHE_ENABLED_KEY = "ai/phash_prefilter/cache_enabled"
    PHASH_PREFILTER_DIAGNOSTICS_KEY = "ai/phash_prefilter/diagnostics"
    FAST_RATING_HINT_DISABLED_KEY = "workflow/fast_rating_hint_disabled"
    FAST_RATING_HINT_SESSIONS_KEY = "workflow/fast_rating_hint_sessions"
    FAST_RATING_HINT_SIZE_BYTES = 20 * 1024 * 1024
    FAVORITES_KEY = "folders/favorites"
    RECENT_FOLDERS_KEY = "folders/recent_opened"
    RECENT_DESTINATIONS_KEY = "folders/recent_destinations"
    SAVED_FILTERS_KEY = "filters/saved_queries"
    RECENT_COMMANDS_KEY = "commands/recent"
    WORKFLOW_RECIPES_KEY = "workflow/recipes"
    WORKSPACE_PRESETS_KEY = "workspace/presets"
    WORKSPACE_TOOLBAR_LAYOUT_KEY = "workspace/toolbar_items"
    WORKSPACE_TOOLBAR_LAYOUT_VERSION = 3
    WORKSPACE_BAR_STATE_KEY = "workspace/bar_state"
    WORKSPACE_BAR_POSITION_KEY = "workspace/bar_position"
    LEGACY_PRIMARY_TOOLBAR_ITEMS = (
        "open_folder",
        "refresh_folder",
        "undo",
        "separator",
        "run_ai_culling",
        "ai_results",
        "command_palette",
        "columns",
        "sort",
        "quick_filter",
        "advanced_filters",
        "clear_filters",
        "batch_rename",
        "batch_resize",
        "batch_convert",
        "handoff_builder",
        "send_to_editor",
        "best_of_set",
        "keyboard_shortcuts",
    )
    AI_SETUP_PROMPTED_KEY = "ai/setup_prompted"
    BURST_GROUPS_KEY = "view/burst_groups"
    BURST_STACKS_KEY = "view/burst_stacks"
    AUTO_ADVANCE_KEY = "view/auto_advance"
    VIEW_COLUMNS_KEY = "view/columns"
    VIEW_ZOOM_WIDTH_KEY = "view/zoom_width"
    # Retired "Legacy cards" toggle: kept only to migrate stored values into
    # the "classic" card style, then the key is removed.
    COMPACT_CARDS_KEY = "view/legacy_cards"
    LOUPE_CARD_STYLE_KEY = "view/loupe_card_style"
    SMALL_DISPLAY_WARNED_KEY = "view/small_display_warned"
    # Resolution-aware card-style policy. Keyed by display class (from the
    # logical height the app actually gets, so a scaled 1080p screen counts as
    # 720p-class). Smaller displays restrict the selectable styles, pick a
    # photo-first default, and collapse detailed->barebones at fewer columns.
    _DISPLAY_STYLE_POLICY = {
        "low": {"styles": ("zen",), "default": "zen", "compact_threshold": 4, "plain_threshold": 5},
        "medium": {"styles": ("detailed", "zen", "gallery"), "default": "gallery", "compact_threshold": 3, "plain_threshold": 4},
        "high": {"styles": ("detailed", "zen", "gallery"), "default": "gallery", "compact_threshold": 4, "plain_threshold": 5},
    }
    FREE_SMOOTH_SCROLL_KEY = "view/free_smooth_scroll"
    SHOW_HIDDEN_FOLDERS_KEY = "view/show_hidden_folders"
    SINGLE_DRIVE_EXPANSION_KEY = "view/single_drive_expansion"
    BROWSER_VIEW_MODE_KEY = "view/browser_mode"
    DETAILS_ROW_DENSITY_KEY = "view/details_row_density"
    UNIFIED_SEARCH_MIN_CONFIDENCE = 0.0
    DETAILS_HEADER_STATE_KEY = "view/details_header_state"
    DETAILS_SORT_COLUMN_KEY = "view/details_sort_column"
    DETAILS_SORT_ORDER_KEY = "view/details_sort_order"
    PREVIEW_PRELOAD_BATCH_SIZE_KEY = "preview/preload_batch_size"
    PERFORMANCE_LOGGING_KEY = "diagnostics/performance_logging"
    # D1 (tentative, flagged for revisit): opt-in, independent of ui_mode
    # (which is permanently forced to "manual" since the 2026-09-19 AI Review
    # mode retirement, see docs/ai_mode_retirement.md).
    SHOW_AI_TAGS_IN_GRID_KEY = "workflow/show_ai_tags_in_grid"
    # WI-5.3 (D3): opt-in, off by default -- render a photo's built-in editor
    # session before handing it to PocketDrop, instead of always sending the
    # unedited original.
    APPLY_EDITS_TO_POCKETDROP_KEY = "workflow/apply_edits_to_pocketdrop"
    # WI-4.7: remembers the last choice made on the Move/Copy confirmation
    # dialog's "Include paired RAW/JPEG and sidecar files" checkbox. Defaults
    # to True so the first run preserves today's always-bundled behavior.
    TRANSFER_INCLUDE_COMPANIONS_KEY = "workflow/transfer_include_companions"
    # Keep diagnostics focused on the active UI investigations so the JSONL log
    # remains readable while still capturing the popout's full loading path.
    # editslider.* stays available for slider-latency profiling under perf logging.
    # ai. captures the AI-workflow stage/step timing (ai.workflow.*, ai.script.*,
    # ai.stage.*, ai.task.*) — without it the focus filter silently drops every
    # AI metric. (Note the trailing dot: it excludes the noisy ai_toolbar_state.*
    # / ai_state.* UI events, which use an underscore.)
    PERF_FOCUS_PREFIXES = (
        "toolbar.",
        "preview.",
        "window.open_preview",
        "perf.",
        "editslider.",
        "brush.",
        "ai.",
    )
    CHECK_UPDATES_ON_STARTUP_KEY = "updates/check_on_startup"
    ZEN_MENU_PINNED_KEY = "view/zen_menu_pinned"
    LEGACY_TOOLBAR_STYLE_KEY = "view/toolbar_style"
    FOLDER_VIEW_STATE_KEY = "view/folder_state"
    DIALOG_GEOMETRY_KEY_PREFIX = "dialogs/geometry"
    AUTO_REVIEW_INTELLIGENCE_MAX_RECORDS = 2400
    CHUNKED_RESTORE_LOAD_MIN_RECORDS = 600
    CHUNKED_RESTORE_LOAD_BATCH_SIZE = 120
    FILTER_METADATA_EAGER_CACHE_MAX_RECORDS = 400
    PREVIEW_PRELOAD_BATCH_SIZE_DEFAULT = FullScreenPreview.DEFAULT_PRELOAD_BATCH_SIZE
    PREVIEW_PRELOAD_BATCH_SIZE_MAX = FullScreenPreview.MAX_PRELOAD_BATCH_SIZE
    INSPECTOR_PREVIEW_TARGET_SIZE = QSize(1200, 1200)
    AI_EMBED_BATCH_SIZE_AUTO = 0
    # NOTE: Despite the historical name, this setting now controls the
    # *worker concurrency* of CLI-Culler's ingest pipeline (preview pool +
    # feature pool both get this many threads). Sensible defaults below
    # were tuned for the two-stage pipeline: enough to overlap IO and
    # compute without oversubscribing ONNX's intra-op thread pool.
    AI_EMBED_BATCH_SIZE_GPU_AUTO = 8
    AI_EMBED_BATCH_SIZE_CPU_AUTO = 4
    LEFT_NAV_PAGE_KEY = "ui/left_nav_page"
    PINNED_TOOLS_KEY = "ui/pinned_tools"
    DEFAULT_PINNED_TOOLS = ("command_palette", "open_in_photoshop", "compare", "keyboard_shortcuts")
    # Share of its box each rail painter actually inks, measured off the drawn
    # pixmaps. The design's glyphs fill theirs, so the rail divides by these to
    # land every mark at the same visual size.
    NAV_ICON_INK_FILL = {
        "folder": 0.63,
        "library": 0.91,
        "faces": 0.71,
        "people": 0.71,
        "collections": 0.75,
        "pocketdrop": 0.9,
    }
    # A Fluent glyph inks about half the 64px pixmap it is centred in, so the
    # pinned tools ask for a correspondingly larger box to reach the same mark.
    FLUENT_ICON_INK_FILL = 0.53
    # key, label, icon id (see _left_nav_icon), tooltip. Order is the rail's top-to-bottom order.
    LEFT_NAV_DESTINATIONS = (
        ("folders", "Library", "library", "Drives and folders"),
        ("faces", "Faces", "faces", "Face groups"),
        ("collections", "Collections", "collections", "Collections"),
        ("pocketdrop", "PocketDrop", "pocketdrop", "Send files to and from your phone"),
    )
    WORKSPACE_TOOLBAR_DEFAULTS = {
        "manual": ("open_folder", "undo", "review", "view", "filters", "accept_selection", "reject_selection", "selection_count", "search", "address"),
        "ai": (
            "ai_status",
            "apply_ai_culling",
            "sort_ai_semantic_folders",
            "reset_ai_review_cache",
            "ai_results",
            "review",
            "view",
            "selection_count",
            "search",
            "filters",
            "address",
        ),
    }
    # Items the top bar renders as fixed chrome (nav glyphs, path combo, search)
    # rather than in the customizable centre action cluster, so they are skipped
    # when mirroring the editable layout into the top bar.
    TOPBAR_CHROME_ITEMS = frozenset(
        {"open_folder", "refresh_folder", "undo", "search", "address", "selection_count", "ai_status", "separator"}
    )
    TOPBAR_COMPACT_LABELS = {
        "run_ai_culling": "Review",
        "apply_ai_culling": "Apply",
        "sort_ai_semantic_folders": "AI Sort",
        "reset_ai_review_cache": "Reset",
        "advanced_filters": "Filters",
        "keyboard_shortcuts": "Keys",
        "open_in_photoshop": "PS",
        "load_saved_ai": "Saved",
        "load_ai_results": "Load AI",
        "clear_ai_results": "Clear AI",
        "next_ai_pick": "Next",
        "next_unreviewed_ai_pick": "Unseen",
        "compare_ai_group": "Compare",
        "review_ai_disagreements": "Review",
        "quick_rerank_ai_culling": "Rerank",
        "manage_people": "People",
        "show_ai_review_summary": "Summary",
        "winner_ladder_mode": "Ladder",
        "open_preview": "Preview",
        "rename_selection": "Rename",
        "move_selection_to_new_folder": "Move New",
        "restore_selection": "Restore",
        "new_folder": "New Folder",
        "zen_mode": "Zen",
        "save_filter_preset": "Save Search",
        "projects": "Collections",
        "catalog": "Library",
        "performance_logging": "Perf",
        "open_performance_logs": "Logs",
        "columns": "Cols",
        "quick_filter": "Quick",
        "ai_results": "Results",
    }
    TOPBAR_NAV_FLUENT_ICONS = {
        "menu": ("E700", None),
        "open": ("F89A", None),
        "back": ("E72B", None),
        "forward": ("E72A", None),
        "up": ("E74A", None),
        "refresh": ("E8F7", None),
        "undo": ("E7A7", None),
    }
    # Keep the established icon-only footprint even with the compact caption.
    TOPBAR_BUTTON_HEIGHT = 34
    TOPBAR_HOVER_MARGIN = 2
    # Maximum saved logical slots. The rendered slot count is recalculated from
    # the live top-bar width; anything beyond that count goes into More.
    TOPBAR_SLOT_COUNT = 35
    TOPBAR_SLOT_CELL_MIN = 36
    TOPBAR_SLOT_BUTTON_WIDTH = 34
    TOPBAR_SLOT_SPACING = WORKSPACE_METRICS.space_4
    TOPBAR_INITIAL_VISIBLE_SLOTS = 8
    # Items that may appear more than once and are exempt from de-duplication
    # (a visual divider is inert and you can drop as many as you like).
    TOPBAR_REPEATABLE_ITEMS = frozenset({"divider"})
    # Filled chrome glyphs that should render as a clean solid silhouette
    # (no stroke carve-out) because their key feature is an open appendage:
    # E721 = Search (magnifier handle), E9D2 = AI/Activity (picture).
    FILLED_ICON_SKIP_CARVE = frozenset({"E721", "E9D2"})
    WORKSPACE_TOOLBAR_ALLOWED_ITEMS = {
        "manual": (
            "review",
            "view",
            "search",
            "filters",
            "columns",
            "sort",
            "quick_filter",
            "advanced_filters",
            "clear_filters",
            "compare",
            "open_preview",
            "winner_ladder_mode",
            "auto_advance",
            "burst_groups",
            "burst_stacks",
            "show_hidden_folders",
            "zen_mode",
            "selection_count",
            "new_folder",
            "open_folder",
            "refresh_folder",
            "undo",
            "command_palette",
            "accept_selection",
            "reject_selection",
            "rename_selection",
            "keep_selection",
            "move_selection",
            "move_selection_to_new_folder",
            "delete_selection",
            "restore_selection",
            "reveal_in_explorer",
            "open_in_photoshop",
            "batch_rename",
            "batch_resize",
            "batch_convert",
            "share_to_phone",
            "handoff_builder",
            "send_to_editor",
            "best_of_set",
            "projects",
            "catalog",
            "save_filter_preset",
            "keyboard_shortcuts",
            "address",
        ),
        "ai": (
            "ai_status",
            "run_ai_culling",
            "apply_ai_culling",
            "sort_ai_semantic_folders",
            "reset_ai_review_cache",
            "ai_results",
            "review",
            "view",
            "search",
            "filters",
            "columns",
            "sort",
            "quick_filter",
            "advanced_filters",
            "clear_filters",
            "compare",
            "open_preview",
            "winner_ladder_mode",
            "auto_advance",
            "burst_groups",
            "burst_stacks",
            "show_hidden_folders",
            "zen_mode",
            "selection_count",
            "new_folder",
            "quick_rerank_ai_culling",
            "manage_people",
            "show_ai_review_summary",
            "next_ai_pick",
            "next_unreviewed_ai_pick",
            "compare_ai_group",
            "review_ai_disagreements",
            "open_folder",
            "refresh_folder",
            "undo",
            "command_palette",
            "batch_rename",
            "batch_resize",
            "batch_convert",
            "share_to_phone",
            "handoff_builder",
            "send_to_editor",
            "best_of_set",
            "keyboard_shortcuts",
            "load_saved_ai",
            "load_ai_results",
            "clear_ai_results",
            "open_ai_report",
            "accept_selection",
            "reject_selection",
            "rename_selection",
            "keep_selection",
            "move_selection",
            "move_selection_to_new_folder",
            "delete_selection",
            "restore_selection",
            "reveal_in_explorer",
            "open_in_photoshop",
            "projects",
            "catalog",
            "save_filter_preset",
            "address",
        ),
    }
    WORKSPACE_TOOLBAR_ITEM_LABELS = {
        "open_folder": "Open",
        "refresh_folder": "Refresh",
        "undo": "Undo",
        "separator": "Separator",
        "divider": "Divider",
        "run_ai_culling": "AI Workflow",
        "apply_ai_culling": "Apply AI Decisions",
        "sort_ai_semantic_folders": "Semantic Sort",
        "reset_ai_review_cache": "Reset AI Cache",
        "ai_results": "AI Results",
        "command_palette": "Command Palette",
        "columns": "Columns",
        "sort": "Sort",
        "quick_filter": "Quick Filter",
        "advanced_filters": "Advanced Filters",
        "clear_filters": "Clear Filters",
        "batch_rename": "Batch Rename",
        "batch_resize": "Batch Resize",
        "batch_convert": "Batch Convert",
        "share_to_phone": "PocketDrop",
        "handoff_builder": "Handoff",
        "send_to_editor": "Send To Editor",
        "best_of_set": "Best Of",
        "keyboard_shortcuts": "Shortcuts",
        "review": "Review",
        "view": "View",
        "search": "Search",
        "filters": "Filters",
        "compare": "Compare",
        "auto_advance": "Auto-Advance",
        "burst_groups": "Smart Groups",
        "burst_stacks": "Smart Stacks",
        "show_hidden_folders": "Show Hidden Folders",
        "selection_count": "Selected Count",
        "accept_selection": "Winner",
        "reject_selection": "Reject",
        "keep_selection": "Keep",
        "move_selection": "Move",
        "delete_selection": "Delete",
        "reveal_in_explorer": "Reveal",
        "open_in_photoshop": "Photoshop",
        "address": "Address Bar",
        "ai_status": "AI Status",
        "load_saved_ai": "Load Saved AI",
        "load_ai_results": "Load AI Results",
        "clear_ai_results": "Clear AI",
        "open_ai_report": "AI Report",
        "next_ai_pick": "Next AI Pick",
        "next_unreviewed_ai_pick": "Next Unreviewed",
        "compare_ai_group": "Compare AI Group",
        "review_ai_disagreements": "AI Disagreements",
        "quick_rerank_ai_culling": "Quick Rerank",
        "manage_people": "People",
        "show_ai_review_summary": "AI Summary",
        "winner_ladder_mode": "Winner Ladder",
        "open_preview": "Open Preview",
        "rename_selection": "Rename",
        "move_selection_to_new_folder": "Move To New Folder",
        "restore_selection": "Restore",
        "new_folder": "New Folder",
        "zen_mode": "Zen Mode",
        "save_filter_preset": "Save Search",
        "projects": "Collections",
        "catalog": "Library",
        "performance_logging": "Performance Logging",
        "open_performance_logs": "Performance Logs",
    }
    WORKSPACE_TOOLBAR_FLUENT_ICONS = {
        "open_folder": ("F89A", None),
        "refresh_folder": ("E8F7", None),
        "undo": ("E7A7", None),
        "run_ai_culling": ("F5B0", "E99A"),
        "apply_ai_culling": ("F13E", "E99A"),
        "sort_ai_semantic_folders": ("F207", "F1D5"),
        "reset_ai_review_cache": ("EA99", "E99A"),
        "ai_results": ("E8BC", "E99A"),
        "command_palette": ("E756", None),
        "columns": ("F246", None),
        "sort": ("E8CB", None),
        "quick_filter": ("E71C", None),
        "advanced_filters": ("E9E9", None),
        "clear_filters": ("E8E6", "E71C"),
        "batch_rename": ("E8AC", None),
        "batch_resize": ("E799", None),
        "batch_convert": ("EE71", None),
        "share_to_phone": ("E8EA", None),
        "handoff_builder": ("E7B8", None),
        "send_to_editor": ("E7AC", None),
        "best_of_set": ("E735", None),
        "keyboard_shortcuts": ("EDA7", None),
        "review": ("E8FF", None),
        "view": ("E890", None),
        "search": ("E721", None),
        "filters": ("E71C", None),
        "compare": ("E89A", None),
        "auto_advance": ("E72A", "EDB5"),
        "burst_groups": ("E902", None),
        "burst_stacks": ("E7AA", None),
        "show_hidden_folders": ("F78D", "E8B7"),
        "selection_count": ("E762", None),
        "accept_selection": ("E8FB", None),
        "reject_selection": ("E711", None),
        "keep_selection": ("E8E1", None),
        "move_selection": ("E8DE", None),
        "delete_selection": ("E74D", None),
        "reveal_in_explorer": ("E8DA", None),
        "open_in_photoshop": ("PS", None),
        "address": ("E71B", None),
        "ai_status": ("F13F", "E99A"),
        "load_saved_ai": ("E896", "E99A"),
        "load_ai_results": ("E8B5", "E99A"),
        "clear_ai_results": ("E894", "E99A"),
        "open_ai_report": ("E9F9", "E99A"),
        "next_ai_pick": ("E893", "E99A"),
        "next_unreviewed_ai_pick": ("F142", "E99A"),
        "compare_ai_group": ("E89A", "E99A"),
        "review_ai_disagreements": ("E8DF", "E7BA"),
        "quick_rerank_ai_culling": ("E8CB", "E99A"),
        "manage_people": ("E716", None),
        "show_ai_review_summary": ("E9D2", "E99A"),
        "winner_ladder_mode": ("E735", "E89A"),
        "open_preview": ("E8A7", None),
        "rename_selection": ("E8AC", None),
        "move_selection_to_new_folder": ("E8DE", "E8F4"),
        "restore_selection": ("E777", None),
        "new_folder": ("E8F4", None),
        "zen_mode": ("E740", None),
        "save_filter_preset": ("E74E", "E71C"),
        "projects": ("E8B7", None),
        "catalog": ("E8F1", None),
        "performance_logging": ("E9D9", None),
        "open_performance_logs": ("E8A7", None),
        "more": ("E712", None),
    }

    # Transitional shims: this state lives on ``self._folder_session``; the old names stay readable here until the
    # window code that reads them moves (controllers already use the session).
    _current_folder = session_field("folder")
    _scope_kind = session_field("scope_kind")
    _scope_id = session_field("scope_id")
    _collection_mode = session_field("collection_mode")
    _session_id = session_field("session_id")
    _browser_view_mode = session_field("browser_view_mode")

    def __init__(self, launch_target: str | None = None, *, quick_view: bool = False) -> None:
        super().__init__()
        self._init_window_frame_and_launch_state(launch_target, quick_view)
        self._init_settings_and_appearance_prefs()
        self._init_core_services()
        self._init_thread_pools_and_controllers()
        self._init_background_indexing_state()
        self._init_scan_task_and_search_state()
        self._init_enrichment_and_ai_review_label_state()
        self._init_job_contexts_and_scope_state()
        self._init_records_controllers_and_state()
        self._init_view_state_and_preferences()
        self._init_cull_thresholds_and_display_policy()
        self._init_session_and_saved_collections()
        self._init_filter_metadata_and_folder_watching()
        self._init_folder_tree_and_drive_list()
        self._init_left_rail_section_widgets()
        self._init_left_rail_pages_and_nav_rail()
        self._init_left_panel_layout()
        self._init_path_controls_and_combos()
        self._init_actions_and_shortcuts()
        self._init_inspector_menus_and_filter_buttons()
        self._init_workspace_toolbars()
        self._init_workspace_bar_and_mode_bars()
        self._init_center_column_and_docks()
        self._init_menu_bar_and_zen_menu()
        self._init_central_container_and_top_bar()
        self._init_status_bar()
        self._init_view_signal_connections()
        self._init_appearance_restore_and_startup_timers()

    def _init_window_frame_and_launch_state(self, launch_target: str | None, quick_view: bool) -> None:
        """Frame flags, startup launch-target / quick-view flags, window title and size."""
        # The popout viewer is the most expensive widget tree in the app and is
        # not on screen at startup, so it is built on first use (the ``preview``
        # property) instead of here. Must exist before anything can reach it.
        self._preview: FullScreenPreview | None = None
        self._deferred_preview_timer: QTimer | None = None
        # The open folder / scope / session state and the extracted controllers. Created before the rest of
        # construction because the shim attributes below (``_current_folder`` ...) read and write the session.
        self._folder_session = FolderSession(self)
        self._appearance = AppearanceController(self)
        self._toolbar = ToolbarController(self)
        self._zen = ZenController(self)
        self._tool_mode = ToolModeController(self)
        self._ai_run = AiRunController(self)
        self._aiculler = AiCullerController(self)
        self._ai_setup = AiSetupController(self)
        # Windows: our app bar is the title bar (see nativeEvent), so drop the
        # system caption but keep a resizable, snappable frame.
        self._custom_frame = os.name == "nt"
        if self._custom_frame:
            self.setWindowFlags(self.windowFlags() | Qt.WindowType.FramelessWindowHint)
        self._startup_launch_target = normalize_filesystem_path(launch_target) if launch_target else ""
        self._quick_view_mode = bool(quick_view and self._startup_launch_target)
        self._pending_quick_view_path = self._startup_launch_target if self._quick_view_mode else ""
        self._quick_view_source_overrides: dict[str, str] = {}
        self._pending_folder_focus_path = ""
        self._pending_focus_scroll_top = False
        self.setWindowTitle("Image Triage")
        self.resize(1600, 960)

    def _init_settings_and_appearance_prefs(self) -> None:
        """Open the settings store (after the one-time legacy migration) and load the pane, toolbar
        and appearance preferences that later phases read."""
        migrate_legacy_settings_once()
        self._settings = user_settings()
        self._load_pane_width_ratios()
        self._startup_window_state = "normal"
        self._startup_window_state_fixup_applied = False
        self._workspace_toolbar_layouts = self._toolbar.load_workspace_toolbar_layouts()
        # Slot grid backing the top-bar cluster: per mode, a fixed-length list
        # where each entry is an item id or None (a blank cell). Source of truth
        # for the cluster's spatial layout; the flat _workspace_toolbar_layouts
        # list stays derived from it for the rest of the toolbar plumbing.
        self._topbar_slots: dict[str, list[str | None]] = self._toolbar.load_topbar_slots()
        # Prototype migration: the workspace bar is retired in favour of the
        # top bar (nav/search/path) and the left mode tabs, so it starts hidden.
        # The View > Show Workspace Toolbar toggle still restores it per session.
        self._workspace_bar_state = self._normalize_workspace_bar_state("hidden")
        self._workspace_bar_position = self._normalize_workspace_bar_position(
            self._settings.value(self.WORKSPACE_BAR_POSITION_KEY, "top", str)
        )
        self._workspace_toolbar_item_widgets: dict[str, dict[str, QWidget]] = {}
        self._workspace_toolbar_overflow_buttons: dict[str, QToolButton] = {}
        if not self._settings.value(self.APPEARANCE_SLATE_MIGRATION_KEY, False, bool):
            self._settings.setValue(self.APPEARANCE_KEY, AppearanceMode.SLATE.value)
            self._settings.setValue(self.APPEARANCE_SLATE_MIGRATION_KEY, True)
        self._appearance_mode = parse_appearance_mode(
            self._settings.value(self.APPEARANCE_KEY, AppearanceMode.SLATE.value, str)
        )
        self._toolbar_placement = self._toolbar.normalize_toolbar_placement(
            self._settings.value(self.TOOLBAR_PLACEMENT_KEY, "floating", str)
        )
        self._ui_gamma = normalize_ui_gamma(self._settings.value(self.UI_GAMMA_KEY, 1.0, float))
        self._interface_size = normalize_display_profile_preference(
            self._settings.value(self.INTERFACE_SIZE_KEY, "automatic", str)
        )
        self._theme = None
        self._display_profile: DisplayProfile | None = None
        self._display_profile_update_pending = False

    def _init_core_services(self) -> None:
        """The stores, managers, views and AI-model lookups the controllers below are handed."""
        self.actions: MainWindowActions | None = None
        self.workspace_docks: WorkspaceDocks | None = None
        self.inspector_panel: InspectorPanel | None = None

        self.thumbnail_manager = ThumbnailManager()
        self._decision_store = DecisionStore()
        self._library_store = LibraryStore()
        self._catalog_repository = CatalogRepository()
        self._bracket_detector = BracketDetector()
        self._photoshop_executable = detect_photoshop_executable()
        self.grid = ThumbnailGridView(self.thumbnail_manager)
        self.details_view = PhotoDetailsView(ai_text_provider=self._ai_run.details_ai_text_for_record)
        self._preview_navigation_dirty = False
        self._preview_preload_index: int | None = None
        self._preview_preload_timer = QTimer(self)
        self._preview_preload_timer.setSingleShot(True)
        self._preview_preload_timer.setInterval(120)
        self._preview_preload_timer.timeout.connect(self._run_preview_preload)
        self._ai_runtime = default_ai_workflow_runtime()

    def _init_thread_pools_and_controllers(self) -> None:
        """Worker pools, with the batch-rename / folder-ops / catalog controllers built between them."""
        self._scan_pool = QThreadPool(self)
        self._scan_pool.setMaxThreadCount(1)
        self._ai_run_pool = QThreadPool(self)
        self._ai_run_pool.setMaxThreadCount(1)
        self._ai_model_pool = QThreadPool(self)
        self._ai_model_pool.setMaxThreadCount(1)
        self._app_update_pool = QThreadPool(self)
        self._app_update_pool.setMaxThreadCount(1)
        self._batch_rename_pool = QThreadPool(self)
        self._batch_rename_pool.setMaxThreadCount(1)
        self._batch_rename = BatchRenameApplyController(self)
        self._folder_ops = FolderOpsController(self)
        self._catalog = CatalogController(self)
        self._resize_pool = QThreadPool(self)
        self._resize_pool.setMaxThreadCount(1)
        self._convert_pool = QThreadPool(self)
        self._convert_pool.setMaxThreadCount(1)
        self._workflow_export_pool = QThreadPool(self)
        self._workflow_export_pool.setMaxThreadCount(1)
        self._archive_pool = QThreadPool(self)
        self._archive_pool.setMaxThreadCount(1)
        self._catalog_pool = QThreadPool(self)
        self._catalog_pool.setMaxThreadCount(1)
        self._review_intelligence_pool = QThreadPool(self)
        self._review_intelligence_pool.setMaxThreadCount(1)
        self._scope_enrichment_pool = QThreadPool(self)
        self._scope_enrichment_pool.setMaxThreadCount(1)
        self._annotation_hydration_pool = QThreadPool(self)
        self._annotation_hydration_pool.setMaxThreadCount(1)
        self._unified_search_pool = QThreadPool(self)
        self._unified_search_pool.setMaxThreadCount(1)

    def _init_background_indexing_state(self) -> None:
        """Semantic and face auto-indexing pools and state, and the background-indexing suspend flags."""
        # Semantic auto-indexing runs at low concurrency so opening a large
        # folder keeps browsing responsive while embeddings build up.
        self._semantic_index_pool = QThreadPool(self)
        self._semantic_index_pool.setMaxThreadCount(1)
        self._active_semantic_index_task: SemanticFolderIndexTask | None = None
        self._semantic_index_active = False
        # Person-filtered face pass (people tagging), layered on the semantic index.
        self._face_index_pool = QThreadPool(self)
        self._face_index_pool.setMaxThreadCount(1)
        self._active_face_index_task: FaceFolderIndexTask | None = None
        self._face_index_active = False
        # Background GPU indexing yields the GPU to the interactive editor: it is
        # suspended (tasks cancelled) while the full-screen preview/editor is open
        # and resumed on close. _background_index_records holds the last folder's
        # records so the incremental passes can be restarted on resume.
        self._background_indexing_suspended = False
        self._background_index_records: list[ImageRecord] = []

    def _init_scan_task_and_search_state(self) -> None:
        """Recycle-bin controller plus scan, AI-task, update-check, review-intelligence,
        annotation-hydration and unified-search task state."""
        self._recycle_bin = RecycleBinController(self)
        self._scan_token = 0
        self._scan_cached_source = ""
        self._active_ai_task: AICullerRunTask | None = None
        self._active_ai_runtime_task: AIRuntimeInstallTask | None = None
        self._active_ai_model_task: AIModelDownloadTask | None = None
        self._active_update_check_task: AppUpdateCheckTask | None = None
        self._active_update_download_task: AppUpdateDownloadTask | None = None
        self._pending_update_result: UpdateCheckResult | None = None
        self._update_check_silent = False
        self._update_installing = False
        self._active_review_intelligence_task: BuildReviewIntelligenceTask | None = None
        self._active_scope_enrichment_task: ScopeEnrichmentTask | None = None
        self._scope_enrichment_token = 0
        self._review_intelligence_token = 0
        self._active_annotation_hydration_task: AnnotationHydrationTask | None = None
        self._annotation_hydration_token = 0
        self._annotation_hydration_dirty_paths: set[str] = set()
        self._annotation_hydration_pending_clear_paths: set[str] = set()
        self._annotation_reapply_timer = QTimer(self)
        self._annotation_reapply_timer.setSingleShot(True)
        self._annotation_reapply_timer.setInterval(90)
        self._annotation_reapply_timer.timeout.connect(self._flush_annotation_hydration_updates)

    def _init_enrichment_and_ai_review_label_state(self) -> None:
        """Person-filter and deferred-enrichment state, flush and label-save timers, the winner-score /
        face / category caches, and the AI-review and telemetry state."""
        # Photos containing the face picked in Tag People; paired with
        # _filter_query.person_label, which is what activates the filter.
        self._ai_deferred_background_work = False
        self._ai_deferred_background_scope_key = ""
        self._review_chunk_dirty_paths: set[str] = set()
        self._review_chunk_flush_timer = QTimer(self)
        self._review_chunk_flush_timer.setSingleShot(True)
        self._review_chunk_flush_timer.setInterval(120)
        self._review_chunk_flush_timer.timeout.connect(self._flush_review_chunk_updates)
        self._scope_enrichment_debounce_timer = QTimer(self)
        self._scope_enrichment_debounce_timer.setSingleShot(True)
        self._scope_enrichment_debounce_timer.setInterval(220)
        self._scope_enrichment_debounce_timer.timeout.connect(self._run_scope_enrichment_debounced)
        self._adapter_review_action_state_timer = QTimer(self)
        self._adapter_review_action_state_timer.setSingleShot(True)
        self._adapter_review_action_state_timer.setInterval(180)
        self._adapter_review_action_state_timer.timeout.connect(self._aiculler.flush_adapter_review_action_state_update)
        self._aiculler_internal_label_save_timer = QTimer(self)
        self._aiculler_internal_label_save_timer.setSingleShot(True)
        self._aiculler_internal_label_save_timer.setInterval(450)
        self._aiculler_internal_label_save_timer.timeout.connect(self._aiculler.flush_aiculler_internal_label_cache)
        self._aiculler_global_label_save_timer = QTimer(self)
        self._aiculler_global_label_save_timer.setSingleShot(True)
        self._aiculler_global_label_save_timer.setInterval(550)
        self._aiculler_global_label_save_timer.timeout.connect(self._aiculler.flush_aiculler_global_label_queue)
        self._winner_scores_by_path: dict[str, dict[str, object]] = {}
        self._face_records_by_path: dict[str, dict[str, object]] = {}
        self._face_records_db_path = ""
        self._face_cycle_index_by_path: dict[str, int] = {}
        self._image_categories_by_path: dict[str, dict[str, object]] = {}
        self._image_categories_db_path = ""
        self._active_ai_training_task: object | None = None
        # Paths the AI Review post-pass has demoted from Keeper/Review to
        # Reject because they're non-best frames in a visually similar burst.
        # Recomputed whenever the bundle OR review_intelligence changes.
        # Map of fast-path-key -> AIConfidenceBucket name for paths the user
        # has labeled / disputed. Overrides the AI's bucket immediately so the
        # user doesn't have to wait for the next adapter retrain to see their
        # decision reflected in AI Review.
        # Cached fast-path-keys for paths the user has explicitly disputed.
        # Drives the dispute -> AI Disagreements filter inclusion and is
        # refreshed alongside the bucket overrides above.
        self._aiculler_pending_telemetry_events: dict[str, tuple[QTimer, TelemetryEvent]] = {}
        # AI Review forces Smart Groups/Stacks off too (the cluster context
        # was producing misleading "weak cluster leader" rejects). We snapshot
        # the toggles the same way as adapter review so they can be restored
        # when the user switches back to Manual.

    def _init_job_contexts_and_scope_state(self) -> None:
        """Per-job (resize, convert, export, archive, catalog) task / context / dialog slots, job
        controllers, pending model-download flags and the current scope fields."""
        self._active_resize_task: ResizeApplyTask | None = None
        self._resize_context: ResizeExecutionContext | None = None
        self._resize_progress_dialog: QProgressDialog | None = None
        self._active_convert_task: ConvertApplyTask | None = None
        self._convert_context: ConvertExecutionContext | None = None
        self._convert_progress_dialog: QProgressDialog | None = None
        self._active_workflow_export_task: WorkflowExportTask | None = None
        self._workflow_context: WorkflowExecutionContext | None = None
        self._workflow_progress_dialog: QProgressDialog | None = None
        self._active_archive_task: CreateArchiveTask | ExtractArchiveTask | None = None
        self._archive_context: ArchiveExecutionContext | None = None
        self._archive_progress_dialog: QProgressDialog | None = None
        self._active_catalog_task: CatalogRefreshTask | None = None
        self._catalog_context: CatalogExecutionContext | None = None
        self._catalog_progress_dialog: QProgressDialog | None = None
        self._job_controllers: dict[str, JobController] = {}
        self._archive_job_key = "archive:create"
        self._scope_label = ""
        self._scan_in_progress = False

    def _init_records_controllers_and_state(self) -> None:
        """Records repository and the record-ops / command-palette / records-view controllers, then
        the records and annotation state they operate on."""
        self._records_repo = RecordsRepository()
        self._record_ops = RecordOpsController(self)
        self._command_palette = CommandPaletteController(self)
        self._records_view = RecordsViewController(self)
        self._folder_records: list[ImageRecord] = []
        self._records: list[ImageRecord] = []
        self._record_index_by_path: dict[str, int] = {}
        self._edited_candidates_cache: dict[str, tuple[str, ...]] = {}
        self._inspection_stats_cache: dict[tuple[str, int, int, int, int], InspectionStats] = {}
        self._inspection_stats_pending_keys: set[tuple[str, int, int, int, int]] = set()
        self._inspection_stats_result_queue: SimpleQueue = SimpleQueue()
        self._inspection_stats_pool = QThreadPool(self)
        self._inspection_stats_pool.setMaxThreadCount(1)
        self._inspection_stats_drain_timer = QTimer(self)
        self._inspection_stats_drain_timer.setInterval(25)
        self._inspection_stats_drain_timer.timeout.connect(self._drain_inspector_stats_results)
        self._visible_review_group_rows_by_id: dict[str, list[int]] = {}
        self._visible_ai_group_rows_by_id: dict[str, list[int]] = {}
        self._accepted_count = 0
        self._rejected_count = 0
        self._unreviewed_count = 0
        self._records_have_resizable = False
        self._records_have_convertible = False
        self._summary_ai_text = "AI: Off"
        self._summary_ai_tooltip = "No AI export is currently loaded."
        self._annotations: dict[str, SessionAnnotation] = {}
        self._ai_bundle: AIBundle | None = None
        self._last_ai_review_summary: dict[str, object] | None = None
        self._hidden_ai_results_checked_scope_key = ""
        self._review_intelligence: ReviewIntelligenceBundle | None = None
        self._correction_events: list[dict[str, object]] = []
        self._taste_profile = TasteProfile()
        self._burst_recommendations: dict[str, BurstRecommendation] = {}
        self._workflow_insights_by_path: dict[str, RecordWorkflowInsight] = {}
        self._prefilter_decisions_by_path: dict[str, PrefilterDecision] = {}
        self._aiculler_ingested_path_keys: set[str] = set()
        self._aiculler_ingested_sibling_keys: set[str] = set()
        self._aiculler_ingested_cache_folder_key = ""
        self._records_view_cache = RecordsViewCache()
        self._records_view_chunk_timer = QTimer(self)
        self._records_view_chunk_timer.setSingleShot(True)
        self._records_view_chunk_timer.timeout.connect(self._records_view.drain_records_view_chunk)
        self._records_view_chunk_records: list[ImageRecord] = []
        self._records_view_chunk_post_load_enrichment = ""
        self._winner_ladder_state: dict[str, object] | None = None

    def _init_view_state_and_preferences(self) -> None:
        """View and workflow preferences read from settings, perf-logger focus, Zen-menu and
        AI-progress state, sort and filter defaults."""
        self._folder_session.browser_view_mode = self._normalize_browser_view_mode(self._settings.value(self.BROWSER_VIEW_MODE_KEY, "grid", str))
        self._details_row_density = self._normalize_details_row_density(
            self._settings.value(self.DETAILS_ROW_DENSITY_KEY, "comfortable", str)
        )
        self._performance_logging_enabled = self._settings.value(self.PERFORMANCE_LOGGING_KEY, False, bool)
        self._check_updates_on_startup = self._settings.value(self.CHECK_UPDATES_ON_STARTUP_KEY, True, bool)
        # Focus perf logging on the toolbar button-movement events only, muting
        # the ~160 app-wide instrumentation points (kept in place, just silenced)
        # so the log isn't flooded. To profile the whole app again, set this to
        # an empty tuple: perf_logger().set_focus(()).
        perf_logger().set_focus(self.PERF_FOCUS_PREFIXES)
        perf_logger().set_enabled(self._performance_logging_enabled, reason="startup")
        self._syncing_browser_selection = False
        self._zen_mode_enabled = False
        self._zen_menu_pinned = self._settings.value(self.ZEN_MENU_PINNED_KEY, False, bool)
        self._zen_menu_reveal_timer = QTimer(self)
        self._zen_menu_reveal_timer.setInterval(80)
        self._zen_menu_reveal_timer.timeout.connect(self._zen.refresh_zen_menu_visibility)
        self._hidden_ai_results_timer = QTimer(self)
        self._hidden_ai_results_timer.setSingleShot(True)
        self._hidden_ai_results_timer.setInterval(450)
        self._hidden_ai_results_timer.timeout.connect(self._ai_run.start_hidden_ai_results_load)
        self._ai_stage_index = 0
        self._ai_stage_total = 3
        self._ai_stage_message = "Ready to run AI review"
        self._ai_progress_current = 0
        self._ai_progress_total = 0
        self._ai_progress_eta_text = ""
        self._ai_status_visible = False
        self._active_ai_embedding_cache_key = ""
        self._active_ai_cluster_cache_key = ""
        self._active_ai_report_cache_key = ""
        self._active_ai_semantic_cache_key = ""
        self._sort_mode = SortMode.NAME
        self._filter_query = RecordFilterQuery()
        self._pending_search_text = ""
        self._auto_advance_enabled = self._settings.value(self.AUTO_ADVANCE_KEY, True, bool)
        self._compare_enabled = False
        self._auto_bracket_enabled = self._settings.value(self.AUTO_BRACKET_KEY, True, bool)
        self._burst_groups_enabled = self._settings.value(self.BURST_GROUPS_KEY, False, bool)
        self._burst_stacks_enabled = self._settings.value(self.BURST_STACKS_KEY, False, bool)
        self._loupe_card_style = self._normalize_loupe_card_style(
            self._settings.value(self.LOUPE_CARD_STYLE_KEY, "gallery", str)
        )
        if self._settings.value(self.COMPACT_CARDS_KEY, False, bool):
            # One-time migration: the removed "Legacy cards" toggle overrode the
            # card style. Its "classic" successor has since been retired too, so
            # fall back to the default detailed card.
            self._loupe_card_style = "detailed"
            self._settings.setValue(self.LOUPE_CARD_STYLE_KEY, "detailed")
        self._settings.remove(self.COMPACT_CARDS_KEY)
        self._free_smooth_scroll_enabled = self._settings.value(self.FREE_SMOOTH_SCROLL_KEY, False, bool)
        self._preview_preload_batch_size = self._normalize_preview_preload_batch_size(
            self._settings.value(
                self.PREVIEW_PRELOAD_BATCH_SIZE_KEY,
                self.PREVIEW_PRELOAD_BATCH_SIZE_DEFAULT,
                int,
            )
        )
        self._show_hidden_folders = self._settings.value(self.SHOW_HIDDEN_FOLDERS_KEY, False, bool)
        self._single_drive_expansion_enabled = self._settings.value(
            self.SINGLE_DRIVE_EXPANSION_KEY, True, bool
        )
        self._toolbar_style = self._normalize_toolbar_style(None)
        self._settings.remove(self.LEGACY_TOOLBAR_STYLE_KEY)
        self._catalog_cache_enabled = self._settings.value(self.CATALOG_CACHE_ENABLED_KEY, True, bool)
        self._watch_current_folder_enabled = self._settings.value(self.CATALOG_WATCH_CURRENT_FOLDER_KEY, True, bool)
        self._restore_folder_position_enabled = self._settings.value(self.RESTORE_FOLDER_POSITION_KEY, True, bool)
        self._ai_embed_batch_size_setting = self._normalize_ai_embed_batch_size(
            self._settings.value(self.AI_EMBED_BATCH_SIZE_KEY, self.AI_EMBED_BATCH_SIZE_AUTO, int)
        )
        self._ai_clip_model_variant = DEFAULT_AICULLER_CLIP_VARIANT
        self._ai_dispute_weight_setting = self._normalize_ai_dispute_weight(
            self._settings.value(self.AI_DISPUTE_WEIGHT_KEY, self.AI_DISPUTE_WEIGHT_DEFAULT, int)
        )
        self._ai_keep_top_percent_setting = self._normalize_ai_keep_top_percent(
            self._settings.value(self.AI_KEEP_TOP_PERCENT_KEY, self.AI_KEEP_TOP_PERCENT_DEFAULT, int)
        )
        self._ai_review_band_percent_setting = self._normalize_ai_review_band_percent(
            self._settings.value(self.AI_REVIEW_BAND_PERCENT_KEY, self.AI_REVIEW_BAND_PERCENT_DEFAULT, int)
        )
        self._ai_base_score_weight_percent_setting = self._normalize_ai_base_score_weight_percent(
            self._settings.value(self.AI_BASE_SCORE_WEIGHT_PERCENT_KEY, self.AI_BASE_SCORE_WEIGHT_PERCENT_DEFAULT, int)
        )

    def _init_cull_thresholds_and_display_policy(self) -> None:
        """Push the loaded cull thresholds into the classifier, read the remaining AI / catalog
        toggles, apply the display-class style policy and register the first post-show callback."""
        # Push the loaded cull thresholds into the bucket classifier so the
        # very first bundle load uses them.
        self._apply_cull_thresholds_to_classifier()
        self._apply_base_score_blend_to_workflow()
        self._ai_review_detail_progress_enabled = self._settings.value(self.AI_REVIEW_DETAIL_PROGRESS_KEY, False, bool)
        self._show_ai_tags_in_grid = self._settings.value(self.SHOW_AI_TAGS_IN_GRID_KEY, False, bool)
        self.grid.set_show_ai_annotations(self._show_ai_tags_in_grid)
        self._apply_edits_to_pocketdrop = self._settings.value(self.APPLY_EDITS_TO_POCKETDROP_KEY, False, bool)
        self._phash_prefilter_settings = self._load_phash_prefilter_settings()
        self._catalog_load_source = "idle"
        self._catalog_load_detail = "Ready"
        self._review_grouping_cache_source = "idle"
        self._review_grouping_cache_detail = "Ready"
        self._review_feature_cache_source = "idle"
        self._review_feature_cache_detail = "Ready"
        self._review_scoring_cache_source = "idle"
        self._review_scoring_cache_detail = "Ready"
        self._watched_folder_path = ""
        self._folder_watch_refresh_pending = False
        # Network and removable drives get no QFileSystemWatcher; instead the folder's modified
        # time is compared against the one its listing was taken at whenever the user returns to
        # the app (_check_folder_changed_on_activation).
        self._folder_dir_mtime_ns: int | None = None
        self._folder_check_task: FolderModifiedCheckTask | None = None
        self._folder_check_token = -1
        self._folder_check_last_started = 0.0
        # Re-rooting the Folders tree on a share waits for a worker to see the drive answer.
        self._drive_sync_token = 0
        self._drive_sync_tasks: dict[int, tuple[PathReachableTask, str, str]] = {}
        # The AI-folder probe of a folder on a share is computed by a worker (see _ai_folder_probe).
        # So are a share folder's saved pHash prefilter decisions (see _refresh_prefilter_decisions_...).
        self._prefilter_load_task: _PrefilterDecisionsTask | None = None
        self._prefilter_load_token = 0
        self._prefilter_load_folder = ""
        self._prefilter_load_at = 0.0
        # Resolution-aware: coerce the effective style + thresholds to what the
        # display can show (warning is deferred until the window is up).
        self._display_class_value = "high"
        self._effective_loupe_card_style = self._loupe_card_style
        self._apply_display_style_policy(show_warning=False)
        QTimer.singleShot(0, self._post_show_display_setup)
        self.grid.set_free_smooth_scroll_enabled(self._free_smooth_scroll_enabled)
        self._ai_setup.refresh_ai_runtime_preferences()

    def _init_session_and_saved_collections(self) -> None:
        """Session id, saved presets / favorites / recents / commands, collection and tool-mode
        state, undo stack and the annotation-persistence queue wiring."""
        self._folder_session.session_id = self._decision_store.ensure_session(
            self._settings.value(self.SESSION_KEY, DecisionStore.DEFAULT_SESSION, str)
        )
        self._winner_mode = self._load_winner_mode()
        self._delete_mode = self._load_delete_mode()
        self._workflow_presets = self._load_workflow_presets()
        self._fast_rating_hint_disabled = self._settings.value(self.FAST_RATING_HINT_DISABLED_KEY, False, bool)
        self._fast_rating_hint_sessions = self._load_fast_rating_hint_sessions()
        self._favorites = self._load_favorites()
        self._recent_folders = self._load_recent_folders()
        self._recent_destinations = self._load_recent_destinations()
        self._folder_view_states = self._load_folder_view_states()
        self._pending_folder_scroll_value: int | None = None
        self._saved_filter_presets = self._load_saved_filter_presets()
        self._saved_workflow_recipes = self._load_saved_workflow_recipes()
        self._saved_workspace_presets = self._load_saved_workspace_presets()
        self._recent_command_ids = self._load_recent_command_ids()
        self._active_tool_mode = ""
        self._collection_target_id = ""
        self._collection_previous_view = "grid"
        self._collection_previous_inspector_enabled = True
        self._visible_burst_groups: list[tuple[int, ...]] = []
        self._command_palette_open = False
        self._active_command_palette: CommandPaletteDialog | None = None
        self._command_palette_dialogs: dict[str, CommandPaletteDialog] = {}
        self._command_palette_shortcut_main: QShortcut | None = None
        self._command_palette_shortcut_preview: QShortcut | None = None
        self._compare_count = 3
        self._manual_compare_count = 3
        self._undo_stack: list[UndoAction] = []
        self._ai_state_actions: dict[AIStateFilter, QAction] = {}
        self._annotation_persistence_queue = AnnotationPersistenceQueue(parent=self)
        self._annotation_persistence_queue.failed.connect(self._handle_annotation_persist_failed)
        self._annotation_persistence_queue.warning.connect(self._handle_annotation_persist_warning)
        self._annotation_persistence_queue.winner_sync_failed.connect(self._handle_winner_sync_failed)
        self._annotation_persistence_queue.winner_kept.connect(self._handle_winner_kept)

    def _init_filter_metadata_and_folder_watching(self) -> None:
        """Search debounce, the filter-metadata manager and prefetch timers, and the folder watcher."""
        self._search_apply_timer = QTimer(self)
        self._search_apply_timer.setSingleShot(True)
        self._search_apply_timer.setInterval(140)
        self._search_apply_timer.timeout.connect(self._records_view.commit_search_text_filter)
        self._filter_metadata_manager = MetadataManager(max_workers=2, parent=self)
        self._filter_metadata_manager.metadata_ready.connect(self._handle_filter_metadata_ready)
        self._filter_metadata_by_path: dict[str, CaptureMetadata] = {}
        self._metadata_scroll_prefetch_timer = QTimer(self)
        self._metadata_scroll_prefetch_timer.setSingleShot(True)
        self._metadata_scroll_prefetch_timer.setInterval(80)
        self._metadata_scroll_prefetch_timer.timeout.connect(self._records_view.run_metadata_scroll_prefetch)
        self._metadata_request_timer = QTimer(self)
        self._metadata_request_timer.setInterval(45)
        self._metadata_request_timer.timeout.connect(self._records_view.drain_filter_metadata_requests)
        self._metadata_reapply_timer = QTimer(self)
        self._metadata_reapply_timer.setSingleShot(True)
        self._metadata_reapply_timer.setInterval(180)
        self._metadata_reapply_timer.timeout.connect(self._records_view.handle_metadata_filter_batch_update)
        self._folder_watcher = QFileSystemWatcher(self)
        self._folder_watcher.directoryChanged.connect(self._handle_watched_folder_changed)
        self._folder_watch_refresh_timer = QTimer(self)
        self._folder_watch_refresh_timer.setSingleShot(True)
        self._folder_watch_refresh_timer.setInterval(900)
        self._folder_watch_refresh_timer.timeout.connect(self._run_watched_folder_refresh)
        app = QApplication.instance()
        if app is not None:
            app.applicationStateChanged.connect(self._handle_application_state_changed)

    def _init_folder_tree_and_drive_list(self) -> None:
        """Folder model, the Folders tree and the flat Drives list."""
        self.folder_model = QFileSystemModel(self)
        self.folder_model.setFilter(self._folder_tree_filter())
        self.folder_model.setRootPath("")

        self.folder_tree = FolderTreeView()
        self.folder_tree.setObjectName("folderTree")
        self.folder_tree.setModel(self.folder_model)
        self.folder_tree.set_single_drive_expansion_enabled(
            self._single_drive_expansion_enabled
        )
        self.folder_tree.setRootIndex(QModelIndex())
        self.folder_tree.setHeaderHidden(True)
        self.folder_tree.header().hide()
        self.folder_tree.setMouseTracking(True)
        for column in range(1, self.folder_model.columnCount()):
            self.folder_tree.hideColumn(column)
        self.folder_tree.clicked.connect(self._handle_tree_selection)
        self.folder_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.folder_tree.customContextMenuRequested.connect(self._show_folder_tree_context_menu)
        self.folder_tree.setAcceptDrops(True)
        self.folder_tree.viewport().setAcceptDrops(True)
        self.folder_tree.viewport().installEventFilter(self)

        # Drives sit in their own flat list; the Folders tree below is rooted at
        # the drive holding the current folder (see _sync_drive_sections).
        self.drive_list = FolderTreeView()
        self.drive_list.setObjectName("driveList")
        self.drive_list.setModel(self.folder_model)
        self.drive_list.setRootIndex(QModelIndex())
        self.drive_list.setHeaderHidden(True)
        self.drive_list.header().hide()
        self.drive_list.setMouseTracking(True)
        for column in range(1, self.folder_model.columnCount()):
            self.drive_list.hideColumn(column)
        self.drive_list.set_drives_only(True)
        self.drive_list.clicked.connect(self._handle_drive_selected)
        self._drive_list_fit_timer = QTimer(self)
        self._drive_list_fit_timer.setSingleShot(True)
        self._drive_list_fit_timer.setInterval(0)
        self._drive_list_fit_timer.timeout.connect(self.drive_list.fit_height_to_rows)
        self.folder_model.rowsInserted.connect(lambda *_args: self._drive_list_fit_timer.start())
        self.folder_model.rowsRemoved.connect(lambda *_args: self._drive_list_fit_timer.start())
        self.folder_model.layoutChanged.connect(lambda *_args: self._drive_list_fit_timer.start())
        self._drive_list_fit_timer.start()

    def _init_left_rail_section_widgets(self) -> None:
        """Left-rail section widgets: Drives / Folders headers, Favorites list, Face Groups and
        Collections sections."""
        self.drives_refresh_button = self._toolbar.build_left_rail_plus_button(tooltip="Refresh drives")
        self.drives_refresh_button.setProperty("fluentGlyph", "E72C")
        self.drives_refresh_button.clicked.connect(self._refresh_drive_list)
        self.drives_refresh_button.setIconSize(QSize(14, 14))
        self.drives_refresh_button.setFixedSize(24, 24)
        self.drives_header = SectionHeader("Drives", trailing=self.drives_refresh_button)
        self.drives_header.setProperty("sectionRole", "drives")
        self.drives_header.toggled.connect(self.drive_list.setVisible)
        self.folders_add_button = self._toolbar.build_left_rail_plus_button(tooltip="New folder in the current folder")
        self.folders_add_button.clicked.connect(lambda _checked=False: self.actions.new_folder.trigger())
        self.folders_add_button.setIconSize(QSize(14, 14))
        self.folders_add_button.setFixedSize(24, 24)
        self.folders_header = SectionHeader("Folders", trailing=self.folders_add_button)
        self.folders_header.setProperty("sectionRole", "folders")
        self.folders_header.toggled.connect(self.folder_tree.setVisible)

        self.favorites_label = QLabel("Favorites")
        self.favorites_label.setObjectName("sectionLabel")

        self.favorites_list = QListWidget()
        self.favorites_list.setObjectName("favoritesList")
        self.favorites_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.favorites_list.customContextMenuRequested.connect(self._show_favorites_context_menu)
        self.favorites_list.itemActivated.connect(self._handle_favorite_activated)
        self.favorites_list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.favorites_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.favorites_list.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.favorites_list.setAcceptDrops(True)
        self.favorites_list.viewport().setAcceptDrops(True)
        self.favorites_list.viewport().installEventFilter(self)

        initial_sidebar_theme = getattr(self, "_theme", None) or default_theme()
        sidebar_accent = initial_sidebar_theme.accent.qcolor()
        sidebar_muted = initial_sidebar_theme.text_muted.qcolor()
        self.face_groups_panel = FaceGroupsPanel()
        self.face_groups_add_button = self._toolbar.build_left_rail_plus_button(
            tooltip="Open people and face naming"
        )
        self.face_groups_header = SectionHeader(
            "Face Groups",
            icon=QIcon(sidebar_people_icon_pixmap(21, sidebar_accent.name())),
            trailing=self.face_groups_add_button,
            collapsible=False,
            icon_size=21,
        )
        self.face_groups_header.setProperty("sectionRole", "faces")

        self.face_groups_search = QLineEdit()
        self.face_groups_search.setObjectName("faceGroupsSearch")
        self.face_groups_search.setPlaceholderText("Search people...")
        self.face_groups_search.setClearButtonEnabled(True)
        self._face_groups_search_action = self.face_groups_search.addAction(
            self._toolbar.fluent_toolbar_icon("E721", color=QColor("#91a0b3")),
            QLineEdit.ActionPosition.LeadingPosition,
        )
        self.face_groups_search.textChanged.connect(self.face_groups_panel.set_search_text)

        self.face_groups_body = QWidget()
        self.face_groups_body.setObjectName("faceGroupsBody")
        self.face_groups_body.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum
        )
        face_groups_layout = QVBoxLayout(self.face_groups_body)
        face_groups_layout.setContentsMargins(8, 0, 8, 0)
        face_groups_layout.setSpacing(4)
        face_groups_layout.addWidget(self.face_groups_search)
        face_groups_layout.addWidget(self.face_groups_panel)

        # Collections are saved, cross-folder sets of image-bundle references.
        # They sit beside Favorites because they are a way to navigate the
        # library, not a command or a duplicate copy of the source files.
        self.projects_add_button = self._toolbar.build_left_rail_plus_button(
            tooltip="New collection from the current selection"
        )
        self.projects_header = SectionHeader(
            "Collections",
            icon=QIcon(sidebar_projects_icon_pixmap(21, sidebar_accent.name())),
            trailing=self.projects_add_button,
            collapsible=False,
            icon_size=21,
        )
        self.projects_header.setProperty("sectionRole", "projects")

        self.projects_list = QListWidget()
        self.projects_list.setObjectName("projectsList")
        self.projects_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.projects_list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.projects_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.projects_list.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)

    def _init_left_rail_pages_and_nav_rail(self) -> None:
        """Assemble the rail pages (Folders, Faces, Collections, PocketDrop) and the nav rail, then show
        the saved page."""
        self.favorites_divider = QFrame()
        self.favorites_divider.setFrameShape(QFrame.Shape.HLine)
        self.favorites_divider.setObjectName("sectionDivider")

        self.library_label = QLabel("Folders")
        self.library_label.setObjectName("sectionLabel")
        library_header = QWidget()
        library_header_layout = QHBoxLayout(library_header)
        library_header_layout.setContentsMargins(0, 0, 0, 0)
        library_header_layout.setSpacing(8)
        library_header_layout.addWidget(self.library_label, 1)

        self.left_settings_bar = self._build_generated_left_settings_bar()

        # Each rail destination owns the whole pane beside the rail rather than
        # sharing its height with the others.
        folders_page = self._appearance.build_left_nav_page()
        folders_layout = folders_page.layout()
        folders_layout.addWidget(self.favorites_label)
        folders_layout.addWidget(self.favorites_list)
        folders_layout.addWidget(self.favorites_divider)
        library_header.hide()
        folders_layout.addWidget(self.drives_header)
        folders_layout.addWidget(self.drive_list, 0)
        folders_layout.addSpacing(6)
        folders_layout.addWidget(self.folders_header)
        folders_layout.addWidget(self.folder_tree, 1)
        folders_layout.addStretch(0)
        folders_spacer_index = folders_layout.count() - 1

        def sync_folders_stretch(_visible: bool = True) -> None:
            # Whichever list is open absorbs the free height; when both are
            # collapsed the spacer does, so headers stack at the top.
            tree_open = self.folder_tree.isVisibleTo(folders_page)
            folders_layout.setStretchFactor(self.folder_tree, 1 if tree_open else 0)
            folders_layout.setStretch(folders_spacer_index, 0 if tree_open else 1)

        self.drives_header.toggled.connect(sync_folders_stretch)
        self.folders_header.toggled.connect(sync_folders_stretch)

        faces_page = self._appearance.build_left_nav_page()
        faces_layout = faces_page.layout()
        faces_layout.addWidget(self.face_groups_header)
        faces_layout.addSpacing(_NAV_SECTION_GAP_PX)
        faces_layout.addWidget(self.face_groups_body, 1)
        faces_layout.addStretch(0)

        collections_page = self._appearance.build_left_nav_page()
        collections_layout = collections_page.layout()
        collections_layout.addWidget(self.projects_header)
        collections_layout.addWidget(self.projects_list, 1)
        collections_layout.addStretch(0)

        # PocketDrop fills its page edge to edge and lays itself out as in the
        # standalone app, but its background is drawn in the pane colour so the
        # page matches Library and Faces (_apply_pocketdrop_background).
        self.pocketdrop_panel = PocketDropPanel()
        self._apply_pocketdrop_background()

        self.left_nav_pages = QStackedWidget()
        self.left_nav_pages.setObjectName("leftNavPages")
        self._left_nav_page_widgets = {
            "folders": folders_page,
            "faces": faces_page,
            "collections": collections_page,
            "pocketdrop": self.pocketdrop_panel,
        }
        for page in self._left_nav_page_widgets.values():
            self.left_nav_pages.addWidget(page)

        self.left_nav_rail = NavRail()
        for key, label, glyph, tooltip in self.LEFT_NAV_DESTINATIONS:
            self.left_nav_rail.add_destination(key, label, glyph, tooltip=tooltip)
        self.left_nav_rail.set_icon_factory(self._appearance.left_nav_icon)
        self._appearance.apply_left_rail_label_colors()
        self.left_nav_rail.current_changed.connect(self._appearance.show_left_nav_page)
        saved_page = str(self._settings.value(self.LEFT_NAV_PAGE_KEY, "folders") or "folders")
        self._appearance.show_left_nav_page(saved_page if saved_page in self._left_nav_page_widgets else "folders")

    def _init_left_panel_layout(self) -> None:
        """Place the page stack and the settings bar in the right column, build ``left_panel`` and refresh
        the Favorites panel."""
        # The settings bar is flush to the bottom edge of the panel card (no gap),
        # so it sits in its own column below the swapped page.
        right_column = QWidget()
        right_column.setObjectName("libraryRightColumn")
        right_column_layout = QVBoxLayout(right_column)
        right_column_layout.setContentsMargins(0, 0, 0, 0)
        right_column_layout.setSpacing(0)
        right_column_layout.addWidget(self.left_nav_pages, 1)
        right_column_layout.addWidget(self.left_settings_bar, 0)

        self.left_panel = QWidget()
        self.left_panel.setObjectName("libraryPanelContent")
        self.left_panel.setMinimumWidth(0)
        left_layout = QHBoxLayout(self.left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(0)
        left_layout.addWidget(self.left_nav_rail, 0)
        left_layout.addWidget(right_column, 1)
        self._refresh_favorites_panel()

    def _init_path_controls_and_combos(self) -> None:
        """Path combos and controls, selection-count labels, and the sort / filter / columns combos."""
        self._directory_up_buttons: list[QToolButton] = []
        self._directory_down_buttons: list[QToolButton] = []
        self.manual_path_combo = self._build_path_combo(mode="manual")
        self.ai_path_combo = self._build_path_combo(mode="ai")
        self.manual_path_control = self._build_path_control(self.manual_path_combo, mode="manual")
        self.ai_path_control = self._build_path_control(self.ai_path_combo, mode="ai")
        self.manual_selection_count_label = self._build_selection_count_label(mode="manual")
        self.ai_selection_count_label = self._build_selection_count_label(mode="ai")

        self.sort_combo = QComboBox()
        for mode in SortMode:
            self.sort_combo.addItem(mode.value, mode)
        self.sort_combo.currentIndexChanged.connect(self._handle_sort_changed)

        self.filter_combo = QComboBox()
        for mode in FilterMode:
            self.filter_combo.addItem(mode.value, mode)
        self.filter_combo.currentIndexChanged.connect(self._records_view.handle_filter_changed)

        self.columns_combo = QComboBox()
        for count in range(1, 9):
            self.columns_combo.addItem(f"{count} Across", count)
        saved_columns = self._normalize_column_count(self._settings.value(self.VIEW_COLUMNS_KEY, 3, int))
        default_columns_index = self.columns_combo.findData(saved_columns)
        self.columns_combo.setCurrentIndex(default_columns_index if default_columns_index >= 0 else 0)
        self.grid.set_column_count(saved_columns)
        self._settings.remove(self.VIEW_ZOOM_WIDTH_KEY)
        self.columns_combo.currentIndexChanged.connect(self._handle_columns_changed)

    def _init_actions_and_shortcuts(self) -> None:
        """Build the main actions, shortcut overrides, toolbar menus and global shortcuts, create the
        record-filter actions, and wire the Collections / Face Groups lists.

        ``_build_record_filter_actions`` fills ``actions.ai_state_actions``, which
        ``build_main_menu_bar`` reads, so this phase must run before ``_init_menu_bar_and_zen_menu``."""
        self.actions = build_main_window_actions(self)
        apply_shortcut_overrides(self.actions)
        self._toolbar_menus = ToolbarMenuController(self, self.actions)
        self._toolbar.build_left_rail_pinned_tools()
        self._command_palette.setup_shortcuts()
        self._zen_toggle_shortcut = QShortcut(QKeySequence("F11"), self)
        self._zen_toggle_shortcut.setContext(Qt.ShortcutContext.ApplicationShortcut)
        self._zen_toggle_shortcut.setAutoRepeat(False)
        self._zen_toggle_shortcut.activated.connect(self._zen.handle_zen_toggle_shortcut)
        self._zen_escape_shortcut = QShortcut(QKeySequence("Esc"), self)
        self._zen_escape_shortcut.setContext(Qt.ShortcutContext.ApplicationShortcut)
        self._zen_escape_shortcut.setAutoRepeat(False)
        self._zen_escape_shortcut.setEnabled(False)
        self._zen_escape_shortcut.activated.connect(self._zen.handle_zen_escape_shortcut)
        self._apply_shortcut_overrides()
        # zen_mode binding is owned by the QShortcut above, so the QAction itself
        # must clear its sequence (after shortcut overrides apply) to avoid double-fire.
        self.actions.zen_mode.setShortcut(QKeySequence())
        self._build_record_filter_actions()
        self.projects_add_button.clicked.connect(
            lambda _checked=False: self.actions.create_virtual_collection.trigger()
        )
        self.face_groups_add_button.clicked.connect(
            lambda _checked=False: self.actions.manage_people.trigger()
        )
        self.projects_list.itemActivated.connect(self._handle_project_activated)
        self.projects_list.itemClicked.connect(self._handle_project_activated)
        self.projects_list.customContextMenuRequested.connect(self._show_projects_context_menu)
        self.face_groups_panel.group_activated.connect(self._handle_face_group_activated)
        self.face_groups_panel.browse_all_requested.connect(
            lambda: self.actions.manage_people.trigger()
        )
        self._refresh_face_groups()

    def _init_inspector_menus_and_filter_buttons(self) -> None:
        """Inspector panel, shared popup menus, search fields and the Review / View / Filter buttons."""
        self.inspector_panel = InspectorPanel()
        self.inspector_panel.setMinimumWidth(0)
        self.thumbnail_manager.thumbnail_ready.connect(self._handle_inspector_thumbnail_ready)
        self.thumbnail_manager.thumbnail_ready.connect(self._handle_preview_filmstrip_thumbnail_ready)
        self.workspace_preset_menu = QMenu(self)
        self.workflow_recipe_menu = QMenu("Run Recipe", self)
        self.collections_menu = QMenu("Collections", self)
        self.catalog_menu = QMenu("Library", self)

        self.manual_search_field = self._build_search_field()
        self.ai_search_field = self._build_search_field()
        self.manual_search_field.textChanged.connect(
            lambda text: self._records_view.handle_search_text_changed(text, source="manual")
        )
        self.ai_search_field.textChanged.connect(
            lambda text: self._records_view.handle_search_text_changed(text, source="ai")
        )
        self.filter_toolbar_menu = QMenu(self)
        self.manual_filter_button = self._records_view.build_advanced_filter_button()
        self.ai_filter_button = self._records_view.build_advanced_filter_button()
        self.view_toolbar_menu = self._toolbar_menus.build_view_toolbar_menu()
        self.manual_review_tools_button = self._toolbar_menus.build_popup_button(
            "Review",
            self._toolbar_menus.build_review_toolbar_menu(),
        )
        self.ai_review_tools_button = self._toolbar_menus.build_popup_button(
            "Review",
            self._toolbar_menus.build_review_toolbar_menu(),
        )
        self.manual_view_tools_button = self._toolbar_menus.build_popup_button("View", self.view_toolbar_menu)
        self.ai_view_tools_button = self._toolbar_menus.build_popup_button("View", self.view_toolbar_menu)
        for button in (self.manual_view_tools_button, self.ai_view_tools_button):
            button.setToolTip("Quick filters, sort options, and column layout.")

    def _init_workspace_toolbars(self) -> None:
        """Manual and AI workspace toolbars, the AI status strip and the toolbar stack."""
        self.manual_toolbar = QWidget()
        self.manual_toolbar.setObjectName("workspaceControls")
        self.manual_toolbar_layout = QHBoxLayout(self.manual_toolbar)
        self.manual_toolbar_layout.setContentsMargins(0, 0, 0, 0)
        self.manual_toolbar_layout.setSpacing(8)
        self.ai_progress_bar = QProgressBar()
        self.ai_progress_bar.setRange(0, 1)
        self.ai_progress_bar.setValue(0)
        self.ai_progress_bar.setFormat("Idle")
        self.ai_progress_bar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.ai_progress_bar.setTextVisible(True)
        self.ai_progress_bar.setMinimumWidth(124)
        self.ai_progress_bar.setMaximumWidth(180)
        self.ai_progress_bar.setFixedHeight(18)
        self.ai_status_label = QLabel("AI cache not loaded")
        self.ai_status_label.setObjectName("secondaryText")
        self.ai_status_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.ai_status_label.setMaximumWidth(260)
        self.ai_status_label.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self.ai_status_widget = QWidget()
        self.ai_status_widget.setObjectName("aiStatusToolbarItem")
        ai_status_layout = QHBoxLayout(self.ai_status_widget)
        ai_status_layout.setContentsMargins(0, 0, 0, 0)
        ai_status_layout.setSpacing(8)
        ai_status_layout.addWidget(self._build_section_label("AI Status"))
        ai_status_layout.addWidget(self.ai_progress_bar)
        ai_status_layout.addWidget(self.ai_status_label)
        self.ai_status_widget.hide()
        self._ai_status_hide_timer = QTimer(self)
        self._ai_status_hide_timer.setSingleShot(True)
        self._ai_status_hide_timer.setInterval(8000)
        self._ai_status_hide_timer.timeout.connect(lambda: self._ai_run.set_ai_status_visible(False))

        self.ai_toolbar = QWidget()
        self.ai_toolbar.setObjectName("workspaceControls")
        self.ai_toolbar_layout = QHBoxLayout(self.ai_toolbar)
        self.ai_toolbar_layout.setContentsMargins(0, 0, 0, 0)
        self.ai_toolbar_layout.setSpacing(8)
        self._workspace_toolbar_item_widgets = {
            "manual": self._toolbar.build_workspace_toolbar_widgets("manual"),
            "ai": self._toolbar.build_workspace_toolbar_widgets("ai"),
        }
        self._workspace_toolbar_overflow_buttons = {
            "manual": self._toolbar.build_workspace_toolbar_overflow_button("manual"),
            "ai": self._toolbar.build_workspace_toolbar_overflow_button("ai"),
        }
        self._toolbar.rebuild_workspace_toolbar("manual")
        self._toolbar.rebuild_workspace_toolbar("ai")

        self.toolbar_stack = QStackedWidget()
        self.toolbar_stack.addWidget(self.manual_toolbar)
        self.toolbar_stack.addWidget(self.ai_toolbar)

    def _init_workspace_bar_and_mode_bars(self) -> None:
        """Floating workspace bar (drag handle, chrome buttons) and the Tool / Collection mode bars."""
        self.workspace_bar_toggle_button = self._toolbar.build_workspace_bar_button(
            "\u2212",
            "Minimize workspace toolbar",
            object_name="workspacePanelButton",
        )
        self.workspace_bar_toggle_button.clicked.connect(self._toolbar.toggle_workspace_bar_collapsed)
        self.workspace_bar_close_button = self._toolbar.build_workspace_bar_button(
            "\u2715",
            "Hide workspace toolbar",
            object_name="workspacePanelCloseButton",
        )
        self.workspace_bar_close_button.clicked.connect(
            lambda _checked=False: self._toolbar.set_workspace_bar_state("hidden")
        )
        self.workspace_bar_chrome = QWidget()
        self.workspace_bar_chrome.setObjectName("workspaceBarChrome")
        workspace_bar_chrome_layout = QHBoxLayout(self.workspace_bar_chrome)
        workspace_bar_chrome_layout.setContentsMargins(0, 0, 0, 0)
        workspace_bar_chrome_layout.setSpacing(4)
        workspace_bar_chrome_layout.addWidget(self.workspace_bar_toggle_button)
        workspace_bar_chrome_layout.addWidget(self.workspace_bar_close_button)

        self.workspace_bar = QWidget()
        self.workspace_bar.setObjectName("workspaceBar")
        workspace_bar_layout = QHBoxLayout(self.workspace_bar)
        workspace_bar_layout.setContentsMargins(12, 8, 12, 8)
        workspace_bar_layout.setSpacing(10)
        self.workspace_bar_drag_handle = QLabel("\u22EE\u22EE")
        self.workspace_bar_drag_handle.setObjectName("workspaceBarDragHandle")
        self.workspace_bar_drag_handle.setToolTip("Drag toolbar to snap it to the top or bottom")
        self.workspace_bar_drag_handle.setCursor(Qt.CursorShape.SizeAllCursor)
        self.workspace_bar_drag_handle.installEventFilter(self)
        workspace_bar_layout.addWidget(self.workspace_bar_drag_handle, 0, Qt.AlignmentFlag.AlignVCenter)
        workspace_bar_layout.addWidget(self.toolbar_stack, 1)
        workspace_bar_layout.addWidget(self.workspace_bar_chrome, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._toolbar.apply_workspace_bar_state()

        self.tool_mode_bar = QWidget()
        self.tool_mode_bar.setObjectName("workspaceControls")
        tool_mode_layout = QHBoxLayout(self.tool_mode_bar)
        tool_mode_layout.setContentsMargins(12, 8, 12, 8)
        tool_mode_layout.setSpacing(10)
        self.tool_mode_title = QLabel("Tool")
        self.tool_mode_title.setObjectName("sectionLabel")
        self.tool_mode_help = QLabel("")
        self.tool_mode_help.setObjectName("secondaryText")
        self.tool_mode_help.setWordWrap(True)
        self.tool_mode_selection = QLabel("0 selected")
        self.tool_mode_selection.setObjectName("secondaryText")
        self.tool_mode_add_all_button = QPushButton("Add All")
        self.tool_mode_run_button = QPushButton("Run")
        self.tool_mode_cancel_button = QPushButton("Cancel")
        self.tool_mode_add_all_button.clicked.connect(self._tool_mode.add_all_for_active_tool_mode)
        self.tool_mode_run_button.clicked.connect(self._tool_mode.run_active_tool_mode)
        self.tool_mode_cancel_button.clicked.connect(self._tool_mode.cancel_tool_mode)
        tool_mode_layout.addWidget(self.tool_mode_title)
        tool_mode_layout.addWidget(self.tool_mode_help, 1)
        tool_mode_layout.addWidget(self.tool_mode_selection)
        tool_mode_layout.addWidget(self.tool_mode_add_all_button)
        tool_mode_layout.addWidget(self.tool_mode_run_button)
        tool_mode_layout.addWidget(self.tool_mode_cancel_button)
        self.tool_mode_bar.hide()

        self.collection_mode_bar = QWidget()
        self.collection_mode_bar.setObjectName("workspaceControls")
        collection_mode_layout = QHBoxLayout(self.collection_mode_bar)
        collection_mode_layout.setContentsMargins(12, 8, 12, 8)
        collection_mode_layout.setSpacing(10)
        self.collection_mode_title = QLabel("Collection Mode")
        self.collection_mode_title.setObjectName("sectionLabel")
        self.collection_mode_help = QLabel("Check images as you search, filter, and browse folders.")
        self.collection_mode_help.setObjectName("secondaryText")
        self.collection_mode_count = QLabel("0 images checked")
        self.collection_mode_count.setObjectName("secondaryText")
        self.collection_mode_save_button = QPushButton("Save Collection")
        self.collection_mode_cancel_button = QPushButton("Cancel")
        self.collection_mode_save_button.clicked.connect(self._save_collection_mode)
        self.collection_mode_cancel_button.clicked.connect(self._cancel_collection_mode)
        collection_mode_layout.addWidget(self.collection_mode_title)
        collection_mode_layout.addWidget(self.collection_mode_help, 1)
        collection_mode_layout.addWidget(self.collection_mode_count)
        collection_mode_layout.addWidget(self.collection_mode_save_button)
        collection_mode_layout.addWidget(self.collection_mode_cancel_button)
        self.collection_mode_bar.hide()

    def _init_center_column_and_docks(self) -> None:
        """Centre column, the docks shell around it, the inspector signal wiring and the menu
        refreshes that feed the menu bar."""
        center_column = QWidget()
        center_column.setObjectName("workspaceCenterColumn")
        center_layout = QVBoxLayout(center_column)
        self.workspace_center_layout = center_layout
        center_layout.setContentsMargins(0, 0, 0, 0)
        center_layout.setSpacing(8)
        self.browser_stack = QStackedWidget()
        self.browser_stack.addWidget(self.grid)
        self.browser_stack.addWidget(self.details_view)
        self.details_view.set_row_density(self._details_row_density)
        self.details_view.layout_state_changed.connect(self._save_details_view_state)
        self.browser_stack.setCurrentIndex(1 if self._browser_view_mode == "details" else 0)
        self.adapter_review_banner = self._aiculler.build_adapter_review_banner()
        self.adapter_review_banner.hide()
        center_layout.addWidget(self.workspace_bar)
        center_layout.addWidget(self.tool_mode_bar)
        center_layout.addWidget(self.collection_mode_bar)
        center_layout.addWidget(self.adapter_review_banner)
        center_layout.addWidget(self.browser_stack, 1)
        self._toolbar.apply_workspace_bar_position()

        self.workspace_docks = build_workspace_docks(self, self.left_panel, self.inspector_panel, center_column)
        self.workspace_docks.on_user_resized_panels = self._remember_user_pane_widths
        self.workspace_docks.width_ratios_provider = self._pane_width_ratios
        self.inspector_panel.popout_requested.connect(lambda: self.workspace_docks.pop_out_panel("inspector"))
        self.inspector_panel.swap_side_requested.connect(self.workspace_docks.swap_sides)
        self.inspector_panel.close_requested.connect(lambda: self.workspace_docks.hide_panel("inspector"))
        self.inspector_panel.face_cycle_requested.connect(self._cycle_inspector_face_preview)
        self.inspector_panel.previous_requested.connect(lambda: self.grid.step_current(-1))
        self.inspector_panel.next_requested.connect(lambda: self.grid.step_current(1))
        self.inspector_panel.analyze_requested.connect(lambda: self.actions.run_ai_culling.trigger())
        self.inspector_panel.compare_requested.connect(lambda: self.actions.compare_mode.trigger())
        self._refresh_workspace_preset_menu()
        self._refresh_workflow_recipe_menu()
        self._refresh_collections_menu()
        self._catalog.refresh_catalog_menu()

    def _init_menu_bar_and_zen_menu(self) -> None:
        """Main menu bar (needs ``actions.ai_state_actions`` filled), Zen-menu pin and corner widget,
        menu-shortcut adoption and the menu-bar animation."""
        build_main_menu_bar(
            self,
            self.actions,
            self.workspace_docks.toggle_actions,
            workflow_recipe_menu=self.workflow_recipe_menu,
            workspace_preset_menu=self.workspace_preset_menu,
            collections_menu=self.collections_menu,
            catalog_menu=self.catalog_menu,
        )
        self.zen_menu_pin_button = QToolButton()
        self.zen_menu_pin_button.setObjectName("zenMenuPinButton")
        self.zen_menu_pin_button.setIcon(build_pin_icon(QColor(178, 188, 202), QColor(245, 247, 252), pixel_size=20))
        self.zen_menu_pin_button.setIconSize(QSize(20, 20))
        self.zen_menu_pin_button.setText("")
        self.zen_menu_pin_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.zen_menu_pin_button.setToolTip("Keep the menu visible in Zen Mode")
        self.zen_menu_pin_button.setCheckable(True)
        self.zen_menu_pin_button.setChecked(self._zen_menu_pinned)
        self.zen_menu_pin_button.toggled.connect(self._zen.handle_zen_menu_pin_toggled)
        self.zen_menu_pin_button.hide()
        self.update_download_button = self._build_update_download_button()
        self.menu_corner_widget = QWidget()
        self.menu_corner_widget.setObjectName("menuCornerWidget")
        menu_corner_layout = QHBoxLayout(self.menu_corner_widget)
        menu_corner_layout.setContentsMargins(4, 0, 8, 0)
        menu_corner_layout.setSpacing(2)
        menu_corner_layout.addWidget(self.zen_menu_pin_button)
        self.menuBar().setCornerWidget(self.menu_corner_widget, Qt.Corner.TopRightCorner)
        # The app bar's Menu button replaces the classic menu bar. Hidden menus
        # lose their shortcuts, so every menu action is also registered on the
        # window itself.
        self._adopt_menu_bar_shortcuts()
        self.menuBar().hide()
        self._refresh_update_button_state()
        self._zen_menu_animation = QPropertyAnimation(self.menuBar(), b"maximumHeight", self)
        self._zen_menu_animation.setDuration(145)
        self._zen_menu_animation.setEasingCurve(QEasingCurve.Type.OutCubic)

    def _init_central_container_and_top_bar(self) -> None:
        """Summary strip, central container, prototype top bar, overlays, default workspace and
        display profile."""
        self.summary_strip = QWidget()
        self.summary_strip.setObjectName("summaryStrip")
        summary_layout = QHBoxLayout(self.summary_strip)
        summary_layout.setContentsMargins(10, 4, 10, 4)
        summary_layout.setSpacing(6)
        self.summary_total = QLabel("Total: 0")
        self.summary_selected = QLabel("Selected: 0")
        self.summary_accepted = QLabel("Winners: 0")
        self.summary_rejected = QLabel("Rejected: 0")
        self.summary_unreviewed = QLabel("Unreviewed: 0")
        self.summary_ai = QLabel("AI: Off")
        self.summary_session = QLabel(f"Profile: {self._session_id}")
        for label in (
            self.summary_total,
            self.summary_selected,
            self.summary_accepted,
            self.summary_rejected,
            self.summary_unreviewed,
            self.summary_ai,
            self.summary_session,
        ):
            summary_layout.addWidget(label)
        summary_layout.addStretch(1)

        container = QWidget()
        container.setObjectName("centralContainer")
        self.central_container = container
        self.central_container.installEventFilter(self)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.app_top_bar = self._toolbar.build_prototype_top_bar()
        self._appearance.apply_chrome_icon_scale()
        layout.addWidget(self.app_top_bar, 0)
        layout.addWidget(self.workspace_docks.shell, 1)
        self.setCentralWidget(container)
        self.browser_stack.installEventFilter(self)
        self.browser_stack.currentChanged.connect(lambda _index: self._toolbar.position_floating_toolbar())
        self._ai_setup_overlay = BusyOverlay(container)
        self._ai_setup_overlay.attach_to(container)
        self.zen_hint_overlay = QLabel("Zen Mode  |  F11 or Esc to exit", container)
        self.zen_hint_overlay.setObjectName("zenHintOverlay")
        self.zen_hint_overlay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.zen_hint_overlay.hide()
        self.zen_hint_hide_timer = QTimer(self)
        self.zen_hint_hide_timer.setSingleShot(True)
        self.zen_hint_hide_timer.timeout.connect(self.zen_hint_overlay.hide)
        self.summary_strip.hide()
        self._apply_default_workspace()
        self._appearance.apply_display_profile()
        QTimer.singleShot(0, self._restore_details_view_state)

    def _init_status_bar(self) -> None:
        """Status bar and its permanent widgets, then toolbar placement and the status indicators."""
        status = QStatusBar()
        status.showMessage("Ready")
        self.setStatusBar(status)
        self.filter_summary_label = QLabel("Filters: All Images")
        self.filter_summary_label.setObjectName("filterSummaryLabel")
        self.filter_summary_label.setMaximumWidth(420)
        self.catalog_status_label = QLabel("")
        self.catalog_status_label.setObjectName("filterSummaryLabel")
        self.catalog_status_label.setMaximumWidth(260)
        self.cache_pipeline_label = QLabel("")
        self.cache_pipeline_label.setObjectName("filterSummaryLabel")
        self.cache_pipeline_label.setMaximumWidth(340)
        self.clear_filters_button = QToolButton()
        self.clear_filters_button.setObjectName("statusFilterClearButton")
        self.clear_filters_button.setAutoRaise(True)
        self.clear_filters_button.setDefaultAction(self.actions.clear_filters)
        status.addPermanentWidget(self.catalog_status_label)
        status.addPermanentWidget(self.cache_pipeline_label)
        status.addPermanentWidget(self.filter_summary_label)
        status.addPermanentWidget(self.clear_filters_button)
        self._toolbar.apply_toolbar_placement()
        self._refresh_catalog_status_indicator()
        self._aiculler.refresh_adapter_status_indicator()
        self._records_view.refresh_filter_toolbar_menu()
        self._refresh_recent_folder_combos()

    def _init_view_signal_connections(self) -> None:
        """Connect the thumbnail grid and details-view signals to the window's handlers."""
        self.grid.current_changed.connect(self._handle_current_changed)
        self.grid.collection_selection_changed.connect(self._refresh_collection_mode_ui)
        self.grid.collection_cancel_requested.connect(self._cancel_collection_mode)
        self.grid.preview_requested.connect(self._open_preview)
        self.grid.delete_requested.connect(self._delete_record)
        self.grid.keep_requested.connect(self._keep_record)
        self.grid.move_requested.connect(self._move_record_prompt)
        self.grid.tag_requested.connect(self._tag_record)
        self.grid.winner_requested.connect(self._toggle_winner)
        self.grid.reject_requested.connect(self._toggle_reject)
        self.grid.adapter_label_requested.connect(self._aiculler.handle_aiculler_adapter_label_requested)
        self.grid.adapter_reasons_requested.connect(self._aiculler.handle_aiculler_adapter_reasons_requested)
        self.grid.adapter_review_mode_cleared.connect(self._aiculler.exit_aiculler_adapter_review_mode)
        self.grid.dispute_label_requested.connect(self._aiculler.handle_dispute_label_requested)
        self.grid.dispute_chord_started.connect(self._aiculler.handle_dispute_chord_started)
        self.grid.dispute_chord_cancelled.connect(self._aiculler.handle_dispute_chord_cancelled)
        self.grid.context_menu_requested.connect(self._show_grid_context_menu)
        self.grid.selection_changed.connect(self._handle_grid_selection_changed)
        self.grid.verticalScrollBar().valueChanged.connect(self._records_view.schedule_metadata_scroll_prefetch)
        self.details_view.current_changed.connect(self._handle_details_current_changed)
        self.details_view.selection_changed.connect(self._handle_details_selection_changed)
        self.details_view.preview_requested.connect(self._open_preview)
        self.details_view.context_menu_requested.connect(self._show_grid_context_menu)
        self.details_view.delete_requested.connect(self._delete_record)
        self.details_view.keep_requested.connect(self._keep_record)
        self.details_view.move_requested.connect(self._move_record_prompt)
        self.details_view.tag_requested.connect(self._tag_record)
        self.details_view.winner_requested.connect(self._toggle_winner)
        self.details_view.reject_requested.connect(self._toggle_reject)

    def _init_appearance_restore_and_startup_timers(self) -> None:
        """System color-scheme hook, appearance, window-state restore, initial control sync and the
        startup timers."""
        app = QApplication.instance()
        if app is not None:
            style_hints = app.styleHints()
            color_scheme_changed = getattr(style_hints, "colorSchemeChanged", None)
            if color_scheme_changed is not None:
                color_scheme_changed.connect(self._handle_system_color_scheme_changed)
        self._appearance.apply_appearance()
        self._restore_window_state()
        self._records_view.sync_record_filter_controls()
        self._records_view.update_filter_summary()
        self._toolbar.sync_chrome_to_manual_review()
        self._update_action_states()
        QTimer.singleShot(0, self._finish_startup_restore)
        if self._check_updates_on_startup and not self._quick_view_mode:
            QTimer.singleShot(2500, self._check_for_updates_on_startup)

    @property
    def _all_records(self) -> list[ImageRecord]:
        return self._records_repo.all_records

    @property
    def _all_records_by_path(self) -> dict[str, ImageRecord]:
        return self._records_repo.all_records_by_path

    # -- Popout viewer, built on first use (WI-8.1) ------------------------
    # Constructing FullScreenPreview costs ~0.6 s (about 1,100 widgets, 85
    # stylesheet applications) and is ~70% of what MainWindow.__init__ used to
    # take, yet nothing needs it until the user opens the popout. Three rules
    # keep that deferral honest:
    #   * ``self.preview`` is for code that genuinely uses the viewer (open it,
    #     navigate it, read its state); first access builds it.
    #   * Pushing window state *into* the viewer (theme, density, shortcuts,
    #     settings...) or asking whether it is open must go through
    #     ``_preview_if_built`` / ``_preview_is_visible`` so that never forces
    #     the build; ``_configure_new_preview`` replays the current window state
    #     into whatever gets built later.
    #   * Anything the old __init__ wired to the viewer lives in that method.
    @property
    def preview(self) -> FullScreenPreview:
        preview = self._preview
        if preview is None:
            preview = self._build_preview()
        return preview

    def _preview_if_built(self) -> FullScreenPreview | None:
        """The popout viewer if it exists yet; never builds it."""
        return self._preview

    def _preview_is_visible(self) -> bool:
        """Whether the popout is on screen; an unbuilt viewer is not."""
        preview = self._preview
        return preview is not None and preview.isVisible()

    def _build_preview(self) -> FullScreenPreview:
        logger = perf_logger()
        start = time.perf_counter() if logger.enabled else 0.0
        preview = FullScreenPreview(self)
        # Published before it is configured, so a re-entrant ``self.preview``
        # while configuring gets this instance instead of recursing into a
        # second build.
        self._preview = preview
        try:
            self._configure_new_preview(preview)
        except BaseException:
            self._preview = None
            preview.deleteLater()
            raise
        if logger.enabled:
            logger.duration("preview.build", (time.perf_counter() - start) * 1000.0)
        return preview

    def _configure_new_preview(self, preview: FullScreenPreview) -> None:
        """Leave a freshly built popout in the state an eagerly built one had
        after ``__init__``, plus every window-level setting changed since.

        Order matches the old startup: signals, simple setters, shortcuts, then
        the display profile and the theme (the viewer restyles itself in its own
        constructor, so the theme step is a no-op when it already matches).
        """
        preview.navigation_requested.connect(self._navigate_preview)
        preview.compare_mode_changed.connect(self._handle_preview_compare_mode_changed)
        preview.auto_bracket_mode_changed.connect(self._handle_preview_auto_bracket_mode_changed)
        preview.compare_count_changed.connect(self._handle_preview_compare_count_changed)
        preview.command_palette_requested.connect(lambda: self._open_command_palette(context="preview"))
        preview.photoshop_requested.connect(self._open_preview_image_in_photoshop)
        preview.winner_requested.connect(self._handle_preview_winner_requested)
        preview.reject_requested.connect(self._handle_preview_reject_requested)
        preview.keep_requested.connect(self._handle_preview_keep_requested)
        preview.delete_requested.connect(self._handle_preview_delete_requested)
        preview.move_requested.connect(self._handle_preview_move_requested)
        preview.tag_requested.connect(self._handle_preview_tag_requested)
        preview.rating_requested.connect(self._handle_preview_rating_requested)
        preview.winner_ladder_choice_requested.connect(self._handle_preview_winner_ladder_choice)
        preview.winner_ladder_skip_requested.connect(self._handle_preview_winner_ladder_skip)
        preview.closed.connect(self._handle_preview_closed)

        preview.set_photoshop_available(bool(self._photoshop_executable))
        preview.set_auto_advance_enabled(self._auto_advance_enabled)
        preview.set_preload_batch_size(self._preview_preload_batch_size)
        preview.set_auto_bracket_mode(self._auto_bracket_enabled)
        if self.actions is not None:
            self._push_review_shortcuts((preview,), load_shortcut_overrides(settings=self._settings))
        self._command_palette.attach_preview_shortcut(preview)
        if self._display_profile is not None:
            preview.apply_display_profile(self._display_profile)
        if self._theme is not None:
            preview.apply_theme(self._theme)
        # Modes the window can enter while there is no viewer to tell.
        preview.set_compare_mode(self._compare_enabled)
        if self._collection_mode:
            preview.set_collection_browse_mode(True)

    def schedule_deferred_preview_build(self, delay_ms: int = 800) -> None:
        """Build the popout once the window is up and the event loop is idle, so
        the ~0.6 s cost lands in idle time instead of as a hitch the first time
        the user presses Space. Opt-in: only the app entry point calls this
        (tests that show windows never get a surprise build). A no-op when the
        viewer already exists or a build is already scheduled."""
        if self._preview is not None or self._deferred_preview_timer is not None:
            return
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.setInterval(max(0, int(delay_ms)))
        timer.timeout.connect(self._run_deferred_preview_build)
        self._deferred_preview_timer = timer
        timer.start()

    def _run_deferred_preview_build(self) -> None:
        timer, self._deferred_preview_timer = self._deferred_preview_timer, None
        if timer is not None:
            timer.deleteLater()
        # A window that was closed or hidden in the meantime (quick view, app
        # shutting down) is not worth a build; the first real use still builds.
        if self._preview is None and self.isVisible():
            self._build_preview()

    def _build_section_label(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("sectionLabel")
        return label

    def _build_update_download_button(self) -> QToolButton:
        button = QToolButton()
        button.setObjectName("updateDownloadButton")
        button.setText("")
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        button.setIconSize(QSize(28, 28))
        button.setFixedSize(48, 28)
        button.setAutoRaise(True)
        button.setCursor(Qt.CursorShape.ArrowCursor)
        button.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        button.clicked.connect(self._handle_update_button_clicked)
        return button

    def _refresh_update_button_state(self) -> None:
        button = getattr(self, "update_download_button", None)
        if button is None:
            return
        checking = self._active_update_check_task is not None
        downloading = self._active_update_download_task is not None
        installing = bool(getattr(self, "_update_installing", False))
        update_available = bool(
            self._pending_update_result is not None and self._pending_update_result.update_available
        )
        theme = self._theme
        color = QColor(88, 196, 132) if update_available else QColor(129, 135, 146)
        if theme is not None:
            color = theme.success.qcolor() if update_available else theme.text_muted.qcolor()
        if checking:
            tooltip = "Checking for updates..."
        elif downloading:
            tooltip = "Downloading update..."
        elif installing:
            tooltip = "Installing update..."
        elif update_available and self._pending_update_result is not None:
            tooltip = f"Image Triage {self._pending_update_result.latest.version} is available"
        else:
            tooltip = "Check for updates"
        button.setEnabled(not checking and not downloading and not installing)
        button.setToolTip(tooltip)
        button.setStatusTip(tooltip)
        button.setProperty("updateAvailable", update_available)
        button.setIcon(self._appearance.update_download_icon(color))
        button.style().unpolish(button)
        button.style().polish(button)

    def _handle_update_button_clicked(self) -> None:
        result = self._pending_update_result
        if result is not None and result.update_available:
            self._prompt_for_update_download(result)
            return
        self._check_for_updates(silent=False)

    def _build_search_field(self) -> QLineEdit:
        field = QLineEdit()
        field.setObjectName("workspaceSearchField")
        field.setClearButtonEnabled(True)
        field.setPlaceholderText("Search by content, person, or filename...")
        field.setToolTip(
            "Search naturally, for example: red car, dog on a beach, or mountains at sunset. "
            "Object and people search use the current folder's AI index."
        )
        field.setMinimumWidth(140)
        field.setMaximumWidth(320)
        field.setSizePolicy(QSizePolicy.Policy.MinimumExpanding, QSizePolicy.Policy.Fixed)
        field.returnPressed.connect(lambda target=field: self._open_directory_from_search(target))
        return field

    def _open_directory_from_search(self, field: QLineEdit) -> None:
        """Enter on a folder path in a search box opens that folder."""
        raw = field.text().strip().strip('"')
        if not raw:
            return
        explicit = bool(re.match(r"^(?:[A-Za-z]:|\\|~)", raw))
        if not explicit and "/" not in raw and "\\" not in raw:
            return
        candidate = normalize_filesystem_path(os.path.expanduser(raw))
        if candidate and os.path.isfile(candidate):
            candidate = os.path.dirname(candidate)
        if not candidate or not os.path.isdir(candidate):
            if explicit:
                self.statusBar().showMessage(f"Folder not found: {raw}")
            return
        field.clear()
        self._select_folder(candidate)

    def _build_path_combo(self, *, mode: str) -> QComboBox:
        combo = QComboBox()
        combo.setObjectName("pathComboBox")
        combo.setEditable(True)
        combo.setCompleter(None)
        combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        combo.setDuplicatesEnabled(False)
        combo.setMinimumWidth(280)
        combo.setMaximumWidth(640)
        combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        combo.setToolTip("Type a folder path or choose a recent folder")
        line_edit = combo.lineEdit()
        if line_edit is not None:
            line_edit.setPlaceholderText("No folder selected")
            line_edit.setClearButtonEnabled(False)
            line_edit.returnPressed.connect(lambda target=combo: self._commit_path_combo_text(target))
        combo.activated.connect(lambda index, target=combo: self._handle_path_combo_activated(target, index))
        combo._directory_suggestion_controller = _DirectorySuggestionController(  # type: ignore[attr-defined]
            combo,
            on_accept_path=self._handle_path_suggestion_accepted,
        )
        return combo

    def _build_directory_nav_button(self, text: str, tooltip: str, *, mode: str) -> QToolButton:
        button = QToolButton()
        button.setObjectName("pathNavButton")
        button.setText("")
        button.setToolTip(tooltip)
        button.setStatusTip(tooltip)
        button.setAutoRaise(True)
        button.setCursor(Qt.CursorShape.ArrowCursor)
        button.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        button.setFixedSize(38, 28)
        button.setIconSize(QSize(24, 24))
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        color = (self._theme or default_theme()).text_muted.qcolor()
        direction = "up" if text == "\u2191" else "down"
        button.setIcon(self._appearance.directory_nav_icon(direction, color))
        return button

    def _build_path_control(self, combo: QComboBox, *, mode: str) -> QWidget:
        wrapper = QWidget()
        wrapper.setObjectName("pathControl")
        wrapper.setMinimumWidth(344)
        wrapper.setMaximumWidth(720)
        wrapper.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        layout = QHBoxLayout(wrapper)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(WORKSPACE_METRICS.space_4)

        up_button = self._build_directory_nav_button("\u2191", "Open parent folder", mode=mode)
        down_button = self._build_directory_nav_button("\u2193", "Open only child folder", mode=mode)
        up_button.clicked.connect(self._navigate_to_parent_folder)
        down_button.clicked.connect(self._navigate_to_only_child_folder)
        self._directory_up_buttons.append(up_button)
        self._directory_down_buttons.append(down_button)

        layout.addWidget(up_button, 0)
        layout.addWidget(down_button, 0)
        layout.addWidget(combo, 1)
        return wrapper

    def _build_selection_count_label(self, *, mode: str) -> QLabel:
        label = QLabel("0 selected")
        label.setObjectName("toolbarSelectionCount")
        label.setMinimumWidth(76)
        label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        label.setToolTip("Selected images in the current view")
        return label

    APP_SEARCH_MAX_WIDTH = 430
    # Breadcrumb offset from the library pane's edge so the first segment's
    # text (after its button padding) lines up with the pane's section labels.
    APP_BREADCRUMB_INSET = -4

    # -- window frame (Windows) -------------------------------------------

    WINDOW_RESIZE_BORDER = 6

    def showMaximized(self) -> None:  # type: ignore[override]
        # Once the frameless window is on screen, Qt "maximizes" it by stretching
        # it over the work area, which Windows still treats as a restored window:
        # rounded corners, a light 1px border, and corners that click through to
        # whatever is behind. Have Windows maximize it for real instead.
        if getattr(self, "_custom_frame", False) and self.isVisible():
            try:
                ctypes.windll.user32.ShowWindow(int(self.winId()), 3)  # type: ignore[attr-defined]  # SW_MAXIMIZE
                return
            except (AttributeError, OSError):
                _logger.warning("Native ShowWindow(SW_MAXIMIZE) failed; falling back to Qt showMaximized", exc_info=True)
        super().showMaximized()

    def showNormal(self) -> None:  # type: ignore[override]
        # The other half of showMaximized: Qt would only resize the window,
        # leaving Windows still treating it as maximized.
        if getattr(self, "_custom_frame", False) and self.isVisible():
            try:
                user32 = ctypes.windll.user32  # type: ignore[attr-defined]
                hwnd = int(self.winId())
                if user32.IsZoomed(hwnd):
                    user32.ShowWindow(hwnd, 9)  # SW_RESTORE
                    return
            except (AttributeError, OSError):
                _logger.warning("Native ShowWindow(SW_RESTORE) failed; falling back to Qt showNormal", exc_info=True)
        super().showNormal()

    def _sync_window_control_glyphs(self) -> None:
        control = getattr(self, "_window_control_buttons", {}).get("maximize")
        if control is None:
            return
        maximized = self.isMaximized()
        control.setText("\uE923" if maximized else "\uE922")
        control.setToolTip("Restore" if maximized else "Maximize")

    def changeEvent(self, event) -> None:  # type: ignore[override]
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange:
            self._sync_window_control_glyphs()

    def _apply_native_frame_styles(self) -> None:
        """Give the frameless window back a thick frame and caption style so
        Windows still resizes, snaps and animates it; nativeEvent then hides the
        caption area and routes the app bar as the drag region."""
        if not getattr(self, "_custom_frame", False):
            return
        try:
            user32 = ctypes.windll.user32  # type: ignore[attr-defined]
            hwnd = int(self.winId())
            gwl_style = -16
            ws_thickframe, ws_caption = 0x00040000, 0x00C00000
            ws_minimizebox, ws_maximizebox, ws_sysmenu = 0x00020000, 0x00010000, 0x00080000
            get_style = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
            set_style = getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)
            style = get_style(hwnd, gwl_style)
            set_style(hwnd, gwl_style, (style | ws_thickframe | ws_minimizebox | ws_maximizebox | ws_sysmenu) & ~ws_caption)
            # SWP_FRAMECHANGED | SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE
            user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, 0x0020 | 0x0002 | 0x0001 | 0x0004 | 0x0010)
        except (AttributeError, OSError, ValueError):
            _logger.warning("Failed to install the custom window frame; disabling it", exc_info=True)
            self._custom_frame = False

    @staticmethod
    def _monitor_work_area(hwnd: int):
        """The RECT of the monitor's work area (screen minus the taskbar)."""

        class MonitorInfo(ctypes.Structure):
            _fields_ = [
                ("cbSize", ctypes.wintypes.DWORD),
                ("rcMonitor", ctypes.wintypes.RECT),
                ("rcWork", ctypes.wintypes.RECT),
                ("dwFlags", ctypes.wintypes.DWORD),
            ]

        try:
            user32 = ctypes.windll.user32  # type: ignore[attr-defined]
            monitor = user32.MonitorFromWindow(hwnd, 2)  # MONITOR_DEFAULTTONEAREST
            info = MonitorInfo()
            info.cbSize = ctypes.sizeof(MonitorInfo)
            if not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
                return None
            return info.rcWork
        except (AttributeError, OSError, ValueError):
            return None

    @staticmethod
    def _fit_maximized_to_work_area(hwnd: int, minmaxinfo_address: int) -> None:
        """Set a WM_GETMINMAXINFO reply's maximized size and position to the
        work area of the window's monitor."""

        class MinMaxInfo(ctypes.Structure):
            _fields_ = [
                ("ptReserved", ctypes.wintypes.POINT),
                ("ptMaxSize", ctypes.wintypes.POINT),
                ("ptMaxPosition", ctypes.wintypes.POINT),
                ("ptMinTrackSize", ctypes.wintypes.POINT),
                ("ptMaxTrackSize", ctypes.wintypes.POINT),
            ]

        class MonitorInfo(ctypes.Structure):
            _fields_ = [
                ("cbSize", ctypes.wintypes.DWORD),
                ("rcMonitor", ctypes.wintypes.RECT),
                ("rcWork", ctypes.wintypes.RECT),
                ("dwFlags", ctypes.wintypes.DWORD),
            ]

        try:
            user32 = ctypes.windll.user32  # type: ignore[attr-defined]
            monitor = user32.MonitorFromWindow(hwnd, 2)  # MONITOR_DEFAULTTONEAREST
            info = MonitorInfo()
            info.cbSize = ctypes.sizeof(MonitorInfo)
            if not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
                return
            work, bounds = info.rcWork, info.rcMonitor
            mmi = MinMaxInfo.from_address(minmaxinfo_address)
            # Position is relative to the monitor, size is the work area's.
            mmi.ptMaxPosition.x = work.left - bounds.left
            mmi.ptMaxPosition.y = work.top - bounds.top
            mmi.ptMaxSize.x = work.right - work.left
            mmi.ptMaxSize.y = work.bottom - work.top
        except (AttributeError, OSError, ValueError):
            return

    def _is_window_drag_point(self, local: QPoint) -> bool:
        bar = getattr(self, "app_top_bar", None)
        if bar is None or not bar.isVisible():
            return False
        if not bar.geometry().contains(bar.parentWidget().mapFrom(self, local)):
            return False
        child = self.childAt(local)
        return child in (
            bar,
            getattr(self, "app_menu_slot", None),
            getattr(self, "app_crumb_stack", None),
            getattr(self, "app_breadcrumb", None),
            getattr(self, "central_container", None),
        )

    def nativeEvent(self, event_type, message):  # type: ignore[override]
        if not getattr(self, "_custom_frame", False) or bytes(event_type) != b"windows_generic_MSG":
            return super().nativeEvent(event_type, message)
        try:
            msg = ctypes.wintypes.MSG.from_address(int(message))
        except (TypeError, ValueError):
            return super().nativeEvent(event_type, message)
        wm_nccalcsize, wm_nchittest, wm_getminmaxinfo = 0x0083, 0x0084, 0x0024
        wm_entersizemove, wm_exitsizemove = 0x0231, 0x0232
        if msg.message == wm_entersizemove:
            # The user has started dragging the window's edge or title bar:
            # resizes now come in a stream, so relayout is coalesced until done.
            self._in_size_move = True
            return super().nativeEvent(event_type, message)
        if msg.message == wm_exitsizemove:
            self._in_size_move = False
            self._appearance.schedule_display_profile_update()
            self._appearance.schedule_layout_ratio_update()
            return super().nativeEvent(event_type, message)
        if msg.message == wm_getminmaxinfo:
            # Maximize onto the monitor's work area exactly. Left alone, Windows
            # overhangs it by the frame thickness, and Qt then misplaces the
            # client area when it works out the frame margins.
            self._fit_maximized_to_work_area(msg.hWnd, msg.lParam)
            return super().nativeEvent(event_type, message)
        if msg.message == wm_nccalcsize and msg.wParam:
            user32 = ctypes.windll.user32  # type: ignore[attr-defined]
            # Ask Windows, not Qt: Qt's window state lags this message. A
            # maximized frameless window can overhang the screen by the frame
            # thickness, so keep the client area inside the monitor's work area
            # instead of trimming a rect that already fits.
            if user32.IsZoomed(msg.hWnd):
                work_area = self._monitor_work_area(msg.hWnd)
                if work_area is not None:
                    rect = ctypes.wintypes.RECT.from_address(msg.lParam)
                    rect.left = max(rect.left, work_area.left)
                    rect.top = max(rect.top, work_area.top)
                    rect.right = min(rect.right, work_area.right)
                    rect.bottom = min(rect.bottom, work_area.bottom)
            return True, 0
        if msg.message == wm_nchittest:
            x = ctypes.c_short(msg.lParam & 0xFFFF).value
            y = ctypes.c_short((msg.lParam >> 16) & 0xFFFF).value
            ratio = max(1.0, self.devicePixelRatioF())
            local = self.mapFromGlobal(QPoint(round(x / ratio), round(y / ratio)))
            if not (self.isMaximized() or self.isFullScreen()):
                border = self.WINDOW_RESIZE_BORDER
                left = local.x() < border
                right = local.x() >= self.width() - border
                top = local.y() < border
                bottom = local.y() >= self.height() - border
                if top and left:
                    return True, 13
                if top and right:
                    return True, 14
                if bottom and left:
                    return True, 16
                if bottom and right:
                    return True, 17
                if left:
                    return True, 10
                if right:
                    return True, 11
                if top:
                    return True, 12
                if bottom:
                    return True, 15
            if self._is_window_drag_point(local):
                return True, 2  # HTCAPTION: drag, snap, double-click maximize
        return super().nativeEvent(event_type, message)

    # -- proportional layout ----------------------------------------------

    PANE_RATIOS_KEY = "ui/pane_width_ratios"
    # Early builds saved transient start-up widths as if they were drags.
    PANE_RATIOS_RESET_KEY = "ui/pane_width_ratios_reset_v3"

    def _load_pane_width_ratios(self) -> None:
        """Make the widths the user last dragged to the live layout_ratios
        values. Runs before the shell is built, so nothing sizes from the
        defaults first and then jumps."""
        if not self._settings.value(self.PANE_RATIOS_RESET_KEY, False, bool):
            self._settings.setValue(self.PANE_RATIOS_RESET_KEY, True)
            self._settings.remove(self.PANE_RATIOS_KEY)
            return
        raw = self._settings.value(self.PANE_RATIOS_KEY, "", str)
        try:
            stored = json.loads(raw) if raw else {}
        except (TypeError, ValueError):
            stored = {}
        if not isinstance(stored, dict):
            return

        def usable(value) -> float | None:
            ok = isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0
            return float(value) if ok else None

        # Out-of-range values are clamped, never discarded: discarding one put
        # the pane back to its default on every launch.
        layout_ratios.set_pane_widths(usable(stored.get("library")), usable(stored.get("inspector")))

    def _pane_width_ratios(self) -> dict[str, float]:
        return {"library": layout_ratios.LIBRARY_PANEL_W, "inspector": layout_ratios.INSPECTOR_W}

    def _remember_user_pane_widths(self, left: int, right: int) -> None:
        # The splitter reports moves when it is merely re-laid out too, so only
        # record a width the user is actually dragging.
        if QApplication.mouseButtons() == Qt.MouseButton.NoButton:
            return
        width = max(1, self.width())
        layout_ratios.set_pane_widths(
            left / width if left > 0 else None,
            right / width if right > 0 else None,
        )
        ratios = {key: round(value, 4) for key, value in self._pane_width_ratios().items()}
        self._settings.setValue(self.PANE_RATIOS_KEY, json.dumps(ratios))
        # The inspector's label column is a share of the pane, so it has to
        # follow a drag as well as a window resize.
        self._apply_inspector_text_ratios(width, max(1, self.height()))

    def _align_app_bar_to_library(self) -> None:
        """Centre Menu over the rail and start the breadcrumb at the library
        pane's content edge; the spacer is corrected from the measured position
        after each layout pass."""
        self._app_bar_align_pending = False
        bar = getattr(self, "app_top_bar", None)
        rail = getattr(self, "left_nav_rail", None)
        pages = getattr(self, "left_nav_pages", None)
        if bar is None or rail is None or pages is None or not bar.isVisible():
            return
        layout = bar.layout()
        origin = bar.mapTo(self, QPoint(0, 0)).x()
        menu_width = self._toolbar.app_menu_button.sizeHint().width()
        if rail.isVisible() and rail.width() > 0:
            slot_x = rail.mapTo(self, QPoint(0, 0)).x() - origin
            slot_width = max(menu_width, rail.width())
            crumb_target = pages.mapTo(self, QPoint(0, 0)).x() - origin + self.APP_BREADCRUMB_INSET
        else:
            slot_x = layout.contentsMargins().left()
            slot_width = menu_width
            crumb_target = slot_x + menu_width + 12
        self.app_menu_slot.setGeometry(slot_x, 0, slot_width, bar.height())
        self.app_menu_slot.raise_()
        current = self._toolbar._app_bar_crumb_spacer.sizeHint().width()
        gap = max(0, current + crumb_target - self.app_crumb_stack.x())
        if gap != current:
            self._toolbar._app_bar_crumb_spacer.changeSize(gap, 0, QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Minimum)
            layout.invalidate()
            layout.activate()
            self._schedule_app_bar_alignment()

    def _schedule_app_bar_alignment(self) -> None:
        if getattr(self, "_app_bar_align_pending", False):
            return
        self._app_bar_align_pending = True
        QTimer.singleShot(0, self._align_app_bar_to_library)

    @staticmethod
    def _resize_topbar_button(button: QToolButton, profile: DisplayProfile) -> None:
        """Resize an existing composite top-bar button without rebuilding it."""

        hover_width = profile.topbar_slot_button_width + 2 * profile.topbar_hover_margin
        hover_height = profile.topbar_button_height + 2 * profile.topbar_hover_margin
        button.setFixedSize(hover_width, hover_height)
        content = button.findChild(QWidget, "appTopBarButtonContent")
        if content is not None:
            content.setGeometry(
                profile.topbar_hover_margin,
                profile.topbar_hover_margin,
                profile.topbar_slot_button_width,
                profile.topbar_button_height,
            )
        glyph = button.findChild(QToolButton, "appTopBarGlyph")
        if glyph is not None:
            glyph.setIconSize(QSize(profile.topbar_glyph_size, profile.topbar_glyph_size))
            glyph.setFixedSize(profile.topbar_slot_button_width, profile.topbar_glyph_size)
        caption = button.findChild(QLabel, "appTopBarButtonCaption")
        if caption is not None:
            caption.setFixedSize(profile.topbar_slot_button_width, profile.topbar_caption_height)

    @staticmethod
    def _trim_icon_transparency(icon: QIcon, *, padding: int = 3) -> QIcon:
        """Remove excess canvas around a glyph so it fills its existing slot."""
        if icon.isNull():
            return icon
        source = icon.pixmap(QSize(64, 64))
        image = source.toImage()
        if image.isNull():
            return icon
        # Bounding box of every pixel with alpha > 0. Done on the alpha plane in
        # numpy: a per-pixel pixelColor() loop here cost ~2 ms per icon, which
        # dominated every top-bar rebuild (18 buttons -> ~37 ms).
        if image.hasAlphaChannel():
            alpha = image.convertToFormat(QImage.Format.Format_Alpha8)
            height, width = alpha.height(), alpha.width()
            stride = alpha.bytesPerLine()
            plane = np.frombuffer(alpha.constBits(), dtype=np.uint8, count=stride * height)
            rows, columns = np.nonzero(plane.reshape(height, stride)[:, :width])
            if rows.size == 0:
                return icon
            left, right = int(columns.min()), int(columns.max())
            top, bottom = int(rows.min()), int(rows.max())
        else:
            # No alpha channel: every pixel is opaque, so the ink is the whole image.
            left, top, right, bottom = 0, 0, image.width() - 1, image.height() - 1
        inset = max(0, int(padding))
        bounds = QRect(left, top, right - left + 1, bottom - top + 1)
        bounds = bounds.adjusted(-inset, -inset, inset, inset).intersected(image.rect())
        return QIcon(source.copy(bounds))

    @staticmethod
    def _keep_topbar_overflow_menu_source(menu: QMenu, source_menu: QMenu) -> None:
        source_menu.setParent(menu)
        sources = getattr(menu, "_topbar_flattened_source_menus", None)
        if sources is None:
            sources = []
            setattr(menu, "_topbar_flattened_source_menus", sources)
        sources.append(source_menu)

    @staticmethod
    def _normalize_toolbar_style(value: object) -> str:
        # Toolbar presentation is intentionally fixed. Keeping this normalizer
        # lets older settings and internal callers migrate without branching.
        return "icon_subtext"

    @staticmethod
    def _normalize_loupe_card_style(value: object) -> str:
        normalized = str(value or "detailed").strip().casefold().replace("-", "_").replace(" ", "_")
        if normalized in {"zen", "gallery"}:
            return normalized
        # Detailed, Zen, and Gallery are the only styles. Retired ones
        # (immersive, classic, legacy, photo_fit) and anything unknown fall
        # back to the default detailed card.
        return "detailed"

    # -- Resolution-aware card-style policy --------------------------------
    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        if (
            getattr(self, "_in_size_move", False)
            or not self.isVisible()
            or getattr(self, "_laying_out_on_resize", False)
        ):
            # A live drag sends many resizes a frame; coalesce them. (Before the
            # first show the size is provisional, and showEvent lays out.)
            self._appearance.schedule_display_profile_update()
            self._appearance.schedule_layout_ratio_update()
            return
        # A one-off resize -- the startup maximize, a maximize or restore, a
        # snap -- is laid out now, before Qt paints it. Deferring it drew one
        # frame with every size worked out for the previous window size, which
        # is the jump seen on every launch.
        # Guarded: a layout pass that nudges the window's minimum size can
        # resize it from inside the pass; that nested resize is coalesced.
        self._laying_out_on_resize = True
        try:
            self._display_profile_update_pending = True
            self._appearance.apply_display_profile()
            self._layout_ratio_update_pending = True
            self._appearance.apply_layout_ratios()
        finally:
            self._laying_out_on_resize = False

    def paintEvent(self, event) -> None:  # type: ignore[override]
        theme = self._theme
        primary = getattr(theme, "backdrop_glow_primary", None)
        if theme is None or primary is None:
            super().paintEvent(event)
            return
        painter = QPainter(self)
        paint_backdrop(painter, theme, self.size(), QPoint(0, 0), self.rect())
        painter.end()

    def _display_class(self) -> str:
        # Card detail depends chiefly on usable height. A narrow window should
        # condense its chrome without unexpectedly forcing photo-only cards.
        screen = self.screen() or QGuiApplication.primaryScreen()
        if screen is None:
            return "high"
        height = screen.availableGeometry().height()
        if height <= 800:
            return "low"
        if height <= 1200:
            return "medium"
        return "high"

    def _allowed_card_styles(self, display_class: str | None = None) -> tuple[str, ...]:
        cls = display_class or self._display_class()
        return tuple(self._DISPLAY_STYLE_POLICY[cls]["styles"])

    def _effective_card_style(self, saved: str, display_class: str) -> str:
        policy = self._DISPLAY_STYLE_POLICY[display_class]
        return saved if saved in policy["styles"] else policy["default"]

    def _apply_display_style_policy(self, *, show_warning: bool) -> None:
        """Coerce the effective card style + column thresholds to what the
        current display can show. The user's saved preference is left untouched,
        so it comes back when they move to a larger display."""
        display_class = self._display_class()
        policy = self._DISPLAY_STYLE_POLICY[display_class]
        effective = self._effective_card_style(self._loupe_card_style, display_class)
        self._display_class_value = display_class
        self._effective_loupe_card_style = effective
        if getattr(self, "grid", None) is not None:
            self.grid.set_loupe_card_style(effective)
            self.grid.set_column_style_thresholds(policy["compact_threshold"], policy["plain_threshold"])
        if show_warning and display_class == "low":
            self._maybe_warn_small_display()

    def _set_grid_filenames_visible(self, visible: bool) -> None:
        target_style = "gallery" if visible else "zen"
        if self._loupe_card_style == target_style and self._effective_loupe_card_style == target_style:
            return
        self._loupe_card_style = target_style
        self._settings.setValue(self.LOUPE_CARD_STYLE_KEY, target_style)
        self._apply_display_style_policy(show_warning=False)
        self.statusBar().showMessage("Filenames shown" if visible else "Filenames hidden")

    def _post_show_display_setup(self) -> None:
        # Runs once the window is up: warn if on a small display, and re-apply the
        # policy live when moved to another screen or the resolution changes.
        self._appearance.apply_display_profile()
        self._apply_display_style_policy(show_warning=True)
        handle = self.windowHandle()
        if handle is not None:
            handle.screenChanged.connect(self._handle_display_change)
        self._connect_screen_geometry_signal()

    def _connect_screen_geometry_signal(self) -> None:
        screen = self.screen()
        if screen is None:
            return
        try:
            screen.geometryChanged.connect(
                self._handle_display_change, Qt.ConnectionType.UniqueConnection
            )
            screen.availableGeometryChanged.connect(
                self._handle_display_change, Qt.ConnectionType.UniqueConnection
            )
            screen.logicalDotsPerInchChanged.connect(
                self._handle_display_change, Qt.ConnectionType.UniqueConnection
            )
        except (TypeError, RuntimeError):
            pass

    def _handle_display_change(self, _arg=None) -> None:
        self._connect_screen_geometry_signal()
        self._appearance.schedule_display_profile_update()
        QTimer.singleShot(0, lambda: fit_window_to_available_geometry(self))
        self._apply_display_style_policy(show_warning=True)

    def _maybe_warn_small_display(self) -> None:
        if self._settings.value(self.SMALL_DISPLAY_WARNED_KEY, False, bool):
            return
        self._settings.setValue(self.SMALL_DISPLAY_WARNED_KEY, True)
        screen = self.screen() or QGuiApplication.primaryScreen()
        size = screen.size() if screen is not None else None
        dims = f"{size.width()}×{size.height()}" if size is not None else "small"
        QMessageBox.information(
            self,
            "Small display detected",
            f"Your screen ({dims}) is small, so the card style is locked to Zen "
            "(photo-only) to keep the layout usable. Other card styles become "
            "available again on a larger display.",
        )

    @staticmethod
    def _render_fluent_glyphs(
        primary: str,
        secondary: str | None,
        primary_color: QColor,
        secondary_color: QColor,
        *,
        primary_size: int = 31,
        scale: int = 1,
    ) -> QPixmap:
        """A toolbar glyph (and its corner badge) on a 64px canvas, or ``scale``
        times that for callers that trim and resample it."""
        extent = 64 * scale
        pixmap = QPixmap(extent, extent)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

        def draw_glyph(glyph: str, *, x: int, y: int, size: int, selected_color: QColor) -> None:
            font_family = "Segoe MDL2 Assets" if len(glyph) > 2 else "Segoe UI"
            font = QFont(font_family, size * scale, QFont.Weight.DemiBold if len(glyph) <= 2 else QFont.Weight.Normal)
            font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
            painter.setFont(font)
            painter.setPen(selected_color)
            if len(glyph) > 2:
                text = chr(int(glyph, 16))
            else:
                text = glyph
            x, y = x * scale, y * scale
            painter.drawText(QRect(x, y, extent - x, extent - y), Qt.AlignmentFlag.AlignCenter, text)

        draw_glyph(
            primary,
            x=0,
            y=0,
            size=primary_size if len(primary) > 2 else 24,
            selected_color=primary_color,
        )
        if secondary:
            draw_glyph(secondary, x=30, y=30, size=19, selected_color=secondary_color)
        painter.end()
        return pixmap

    def _apply_inspector_text_ratios(self, width: int, height: int) -> None:
        """Size the inspector's own text and its label column.

        One sheet on the panel root, which every row inherits, so rows built
        later for a new photo pick the sizes up without being visited.
        """
        panel = getattr(self, "inspector_panel", None)
        if panel is None:
            return
        px = layout_ratios.ratio_px
        floor = layout_ratios.MIN_TEXT_PX
        title = px(layout_ratios.INSPECTOR_TITLE_H, height, minimum=floor)
        aside = px(layout_ratios.INSPECTOR_ASIDE_H, height, minimum=floor)
        key = px(layout_ratios.INSPECTOR_KEY_H, height, minimum=floor)
        value = px(layout_ratios.INSPECTOR_VALUE_H, height, minimum=floor)
        preview = px(layout_ratios.INSPECTOR_PREVIEW_H, height, minimum=floor)
        # The label column is a share of the pane, so it keeps its proportion
        # as the pane is resized rather than eating a narrow inspector.
        pane = panel.width() or px(layout_ratios.INSPECTOR_W, width, minimum=200)
        key_width = max(48, round(layout_ratios.INSPECTOR_KEY_W * pane))
        chip = px(layout_ratios.INSPECTOR_CHIP_TEXT_H, height, minimum=floor)
        nav = px(layout_ratios.INSPECTOR_NAV_H, height, minimum=layout_ratios.MIN_GLYPH_PX)
        analyze = px(layout_ratios.ANALYZE_BUTTON_H, height, minimum=20)
        sheet = (
            f"QLabel#inspectorAiChip {{ font-size: {chip}px; }}"
            f"QToolButton#inspectorNavButton {{ font-size: {nav}px; }}"
            f"QPushButton#inspectorAnalyzeButton {{ min-height: {analyze}px; }}"
            f"QLabel#inspectorSectionTitle {{ font-size: {title}px; }}"
            f"QLabel#inspectorSectionAside {{ font-size: {aside}px; }}"
            f"QLabel#inspectorKey {{ font-size: {key}px;"
            f" min-width: {key_width}px; max-width: {key_width}px; }}"
            f"QLabel#inspectorValue {{ font-size: {value}px; }}"
            f"QLabel#inspectorPreviewName, QLabel#inspectorPreviewPosition"
            f" {{ font-size: {preview}px; }}"
        )
        if panel.styleSheet() != sheet:
            panel.setStyleSheet(sheet)

    @staticmethod
    def _set_widget_font_px(widget: QWidget, size: int) -> None:
        sheet = f"font-size: {size}px;"
        if widget.styleSheet() != sheet:
            widget.setStyleSheet(sheet)

    def _apply_pocketdrop_background(self) -> None:
        """Paint the PocketDrop page in the same colour as the Library and
        Faces pages beside it."""
        panel = getattr(self, "pocketdrop_panel", None)
        if panel is None:
            return
        theme = getattr(self, "_theme", None) or default_theme()
        panel.set_background(theme.panel_bg.qcolor())

    def _show_pocketdrop_page(self) -> None:
        docks = getattr(self, "workspace_docks", None)
        if docks is not None and docks.library.mode != "expanded":
            docks.expand_panel("library")
        self._appearance.show_left_nav_page("pocketdrop")

    def _build_generated_left_settings_bar(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("leftSettingsBar")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(14)
        self._left_settings_buttons: list[tuple[QToolButton, int]] = []

        def add_button(glyph: str, tooltip: str, handler) -> None:
            button = QToolButton(bar)
            button.setObjectName("leftSettingsBarButton")
            button.setProperty("fluentGlyph", glyph)
            button.setIcon(self._appearance.fluent_filled_icon(glyph, self._appearance.chrome_icon_color()))
            button.setIconSize(QSize(20, 20))
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
            button.setToolTip(tooltip)
            button.setAutoRaise(True)
            button.setFocusPolicy(Qt.FocusPolicy.TabFocus)
            button.setFixedSize(34, 34)
            self._left_settings_buttons.append((button, 20))
            if handler is not None:
                button.clicked.connect(lambda _checked=False, target=handler: target())
            layout.addWidget(button, 0)

        add_button("E8EF", "Collections", None)
        add_button("E8EC", "Tags", None)
        add_button("E9D2", "Activity", None)
        layout.addStretch(1)
        add_button("E72C", "Restart App", self._restart_app_for_development)
        add_button("E946", "Help", self._show_library_help)
        add_button("E713", "Settings", self._show_settings)
        return bar

    # -- Top-bar slot model ------------------------------------------------
    def _is_cluster_item(self, item_id: object) -> bool:
        # Items that render in the top-bar cluster (everything the cluster can
        # show). Kept structural — no dependency on self.actions — so it is safe
        # to call at load time before the UI is built.
        return isinstance(item_id, str) and bool(item_id) and item_id not in self.TOPBAR_CHROME_ITEMS

    def _items_to_slots(self, items) -> list[str | None]:
        n = self.TOPBAR_SLOT_COUNT
        usable = n
        result: list[str | None] = [None] * n
        seen: set[str] = set()
        idx = 0
        for item in items:
            if idx >= usable:
                break
            if not self._is_cluster_item(item):
                continue
            if item not in self.TOPBAR_REPEATABLE_ITEMS:
                if item in seen:
                    continue
                seen.add(item)
            result[idx] = item
            idx += 1
        return result

    def _update_selection_count_labels(self) -> None:
        count = self.grid.selected_count() if self._records else 0
        text = f"{count} selected"
        tooltip = f"{count} selected image{'s' if count != 1 else ''}"
        for label in (
            getattr(self, "manual_selection_count_label", None),
            getattr(self, "ai_selection_count_label", None),
        ):
            if label is None:
                continue
            label.setText(text)
            label.setToolTip(tooltip)
        self._toolbar.schedule_workspace_toolbar_overflow_update("manual")
        self._toolbar.schedule_workspace_toolbar_overflow_update("ai")

    @staticmethod
    def _normalize_workspace_bar_state(value: object) -> str:
        if isinstance(value, str) and value in {"expanded", "minimized", "hidden"}:
            return value
        return "expanded"

    @staticmethod
    def _normalize_workspace_bar_position(value: object) -> str:
        if isinstance(value, str) and value in {"top", "bottom"}:
            return value
        return "top"

    def _build_record_filter_actions(self) -> None:
        self._records_view.build_record_filter_actions()

    def _refresh_action_shortcut_hint(self, action: QAction) -> None:
        base_text = action.property("imageTriageBaseText")
        if not isinstance(base_text, str) or not base_text:
            base_text = action.text().replace("&", "")
        shortcut_text = action.shortcut().toString(QKeySequence.SequenceFormat.NativeText)
        hinted_text = format_action_tooltip(base_text, shortcut_text)
        action.setToolTip(hinted_text)
        action.setStatusTip(hinted_text)

    @staticmethod
    def _menu_text_with_hint(text: str, hint: str = "") -> str:
        return ToolbarMenuController.menu_text_with_hint(text, hint)

    _REVIEW_KEY_BINDING_IDS = (
        "cycle_burst_previous",
        "cycle_burst_next",
        "keep_at_cursor",
        "move_at_cursor",
        "tag_at_cursor",
        "adapter_label_hero",
        "adapter_label_strong",
        "adapter_label_maybe",
        "adapter_label_weak",
        "adapter_label_reject",
    )

    def _apply_shortcut_overrides(self) -> None:
        """Push the single shortcut registry (ui/shortcuts.py) onto every
        surface that reads a keyboard shortcut: QActions, and the raw
        per-key review commands in grid/details/preview (WI-3.2)."""
        if self.actions is None:
            return
        overrides = load_shortcut_overrides(settings=self._settings)
        apply_shortcut_overrides(self.actions, overrides)
        for attr_name, _category, _default, _display in SHORTCUT_REGISTRY:
            action = getattr(self.actions, attr_name, None)
            if action is not None:
                self._refresh_action_shortcut_hint(action)

        surfaces = [self.grid, self.details_view.table]
        preview = self._preview_if_built()
        if preview is not None:
            # An unbuilt viewer picks the same values up in _configure_new_preview.
            surfaces.append(preview)
        self._push_review_shortcuts(surfaces, overrides)

        self._command_palette.apply_shortcut(
            overrides.get(
                "open_command_palette",
                self.actions.open_command_palette.shortcut().toString(QKeySequence.SequenceFormat.PortableText),
            )
        )

    def _push_review_shortcuts(self, surfaces, overrides) -> None:
        review_keys = effective_shortcuts(self._REVIEW_KEY_BINDING_IDS, overrides)
        winner_shortcut = self.actions.accept_selection.shortcut()
        reject_shortcut = self.actions.reject_selection.shortcut()
        for surface in surfaces:
            surface.set_review_action_shortcuts(winner_shortcut, reject_shortcut)
            surface.set_review_key_shortcuts(review_keys)

    def _load_saved_workflow_recipes(self) -> list[WorkflowRecipe]:
        raw = self._settings.value(self.WORKFLOW_RECIPES_KEY, "", str)
        return load_saved_workflow_recipes(raw)

    def _save_saved_workflow_recipes(self) -> None:
        self._settings.setValue(
            self.WORKFLOW_RECIPES_KEY,
            dump_saved_workflow_recipes(self._saved_workflow_recipes),
        )

    def _load_saved_workspace_presets(self) -> list[WorkspacePreset]:
        raw = self._settings.value(self.WORKSPACE_PRESETS_KEY, "", str)
        return load_saved_workspace_presets(raw)

    def _save_saved_workspace_presets(self) -> None:
        self._settings.setValue(
            self.WORKSPACE_PRESETS_KEY,
            dump_saved_workspace_presets(self._saved_workspace_presets),
        )

    def _refresh_workflow_recipe_menu(self) -> None:
        if not hasattr(self, "workflow_recipe_menu") or self.workflow_recipe_menu is None:
            return
        self.workflow_recipe_menu.clear()
        self.workflow_recipe_menu.setTitle("Run Recipe")
        self.workflow_recipe_menu.addAction(self.actions.share_to_phone)
        self.workflow_recipe_menu.addSeparator()
        self.workflow_recipe_menu.addAction(self.actions.handoff_builder)
        self.workflow_recipe_menu.addAction(self.actions.send_to_editor_pipeline)
        self.workflow_recipe_menu.addSeparator()

        builtins = built_in_workflow_recipes()
        if builtins:
            header = self.workflow_recipe_menu.addSection("Built-In Recipes")
            header.setEnabled(False)
            for recipe in builtins:
                action = self.workflow_recipe_menu.addAction(recipe.name)
                action.triggered.connect(lambda _checked=False, target=recipe: self._run_workflow_recipe(target))
            self.workflow_recipe_menu.addSeparator()

        saved_header = self.workflow_recipe_menu.addSection("Saved Recipes")
        saved_header.setEnabled(False)
        if self._saved_workflow_recipes:
            for recipe in self._saved_workflow_recipes:
                action = self.workflow_recipe_menu.addAction(recipe.name)
                action.triggered.connect(lambda _checked=False, target=recipe: self._run_workflow_recipe(target))
        else:
            empty_action = self.workflow_recipe_menu.addAction("No saved recipes yet")
            empty_action.setEnabled(False)

    def _refresh_workspace_preset_menu(self) -> None:
        if not hasattr(self, "workspace_preset_menu") or self.workspace_preset_menu is None:
            return
        self.workspace_preset_menu.clear()
        self.workspace_preset_menu.setTitle("Workspace Presets")
        builtins = built_in_workspace_presets()
        if builtins:
            builtins_header = self.workspace_preset_menu.addSection("Built-In Presets")
            builtins_header.setEnabled(False)
            for preset in builtins:
                action = self.workspace_preset_menu.addAction(preset.name)
                action.setToolTip(preset.description)
                action.triggered.connect(lambda _checked=False, target=preset: self._apply_workspace_preset(target))
            self.workspace_preset_menu.addSeparator()
        saved_header = self.workspace_preset_menu.addSection("Saved Presets")
        saved_header.setEnabled(False)
        if self._saved_workspace_presets:
            for preset in self._saved_workspace_presets:
                action = self.workspace_preset_menu.addAction(preset.name)
                action.setToolTip(preset.description)
                action.triggered.connect(lambda _checked=False, target=preset: self._apply_workspace_preset(target))
        else:
            empty_action = self.workspace_preset_menu.addAction("No saved workspace presets yet")
            empty_action.setEnabled(False)

    def _scope_display_label(self) -> str:
        if self._scope_kind == "folder":
            return self._current_folder or "No folder selected"
        return self._scope_label or "Virtual Scope"

    def _apply_scope_label(self) -> None:
        self._refresh_recent_folder_combos()

    def _set_scope_state(self, *, kind: str, scope_id: str = "", label: str = "") -> None:
        self._folder_session.scope_kind = kind
        self._folder_session.scope_id = scope_id
        self._scope_label = label
        self._apply_scope_label()

    def _current_scope_key(self) -> str:
        if self._scope_kind == "folder":
            return _memory_path_key(self._current_folder)
        return f"{self._scope_kind}:{self._scope_id or self._scope_label.casefold()}"

    def _face_groups_db_path(self):
        paths = self._aiculler.aiculler_paths_for_current_folder()
        if paths is None:
            return None
        db_path = aiculler_db_path(paths)
        return db_path if db_path.exists() else None

    def _refresh_face_groups(self) -> None:
        """Reload the sidebar's face list for the current folder."""
        panel = getattr(self, "face_groups_panel", None)
        if panel is None:
            return
        db_path = self._face_groups_db_path()
        if db_path is None:
            panel.set_groups([], has_index=False)
            return
        panel.set_groups(load_face_groups(db_path), has_index=True)

    def _handle_face_group_activated(self, group) -> None:
        db_path = self._face_groups_db_path()
        if db_path is None:
            return
        paths = face_group_photo_paths(db_path, group.cluster_ids)
        if not paths:
            self.statusBar().showMessage("No indexed photos for that face yet.")
            return
        self._records_view.show_photos_for_person(group.filter_label, paths)

    def _refresh_projects_panel(self) -> None:
        """Mirror the collections menu into the sidebar section."""
        panel = getattr(self, "projects_list", None)
        if panel is None:
            return
        collections = self._library_store.list_collections()
        panel.clear()
        for collection in collections:
            item = QListWidgetItem(f"{collection.name}  ({collection.item_count})")
            item.setData(Qt.ItemDataRole.UserRole, collection.id)
            item.setToolTip(collection.description or collection.name)
            # Sized here for the same reason as the face rows: a stylesheet
            # min-height is only a floor and drifts with font size and DPI.
            item.setSizeHint(QSize(0, _PROJECT_ROW_PX))
            panel.addItem(item)
        if not collections:
            # Keep the empty state close to its section header.  The previous
            # two-line, vertically-centred row left a conspicuous blank band
            # above the only visible text (and QListWidget elided the newline
            # anyway, so it still appeared as a single line).
            empty = QListWidgetItem("No collections yet.")
            empty.setFlags(Qt.ItemFlag.NoItemFlags)
            empty.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
            empty.setSizeHint(QSize(0, _PROJECT_EMPTY_ROW_PX))
            panel.addItem(empty)
        self._update_projects_height()

    def _update_projects_height(self) -> None:
        panel = getattr(self, "projects_list", None)
        if panel is None:
            return
        heights = [panel.item(row).sizeHint().height() for row in range(panel.count())]
        if not heights:
            heights = [_PROJECT_ROW_PX]
        # Cap the section so a long project list cannot crowd out the pane.
        visible = sum(heights[:_MAX_VISIBLE_PROJECT_ROWS])
        panel.setMinimumHeight(0)
        panel.setMaximumHeight(visible + 2 * panel.frameWidth() + 2)

    def _project_id_for_item(self, item) -> str:
        if item is None:
            return ""
        return str(item.data(Qt.ItemDataRole.UserRole) or "")

    def _handle_project_activated(self, item) -> None:
        collection_id = self._project_id_for_item(item)
        if collection_id:
            self._open_virtual_collection(collection_id)

    def _show_projects_context_menu(self, point) -> None:
        panel = self.projects_list
        item = panel.itemAt(point)
        collection_id = self._project_id_for_item(item)
        menu = QMenu(panel)
        menu.addAction(self.actions.create_virtual_collection)
        menu.addAction(self.actions.add_selection_to_collection)
        if collection_id:
            menu.addSeparator()
            open_action = menu.addAction("Open collection")
            open_action.triggered.connect(
                lambda _checked=False, target=collection_id: self._open_virtual_collection(target)
            )
            menu.addAction(self.actions.remove_selection_from_collection)
            menu.addAction(self.actions.delete_virtual_collection)
        menu.exec(panel.viewport().mapToGlobal(point))

    def _refresh_collections_menu(self) -> None:
        self._refresh_projects_panel()
        if not hasattr(self, "collections_menu") or self.collections_menu is None:
            return
        self.collections_menu.clear()
        self.collections_menu.setTitle("Collections")
        self.collections_menu.addAction(self.actions.create_virtual_collection)
        self.collections_menu.addAction(self.actions.add_selection_to_collection)
        self.collections_menu.addAction(self.actions.remove_selection_from_collection)
        self.collections_menu.addAction(self.actions.delete_virtual_collection)
        self.collections_menu.addSeparator()

        collections = self._library_store.list_collections()
        if collections:
            header = self.collections_menu.addSection("Open Collection")
            header.setEnabled(False)
            for collection in collections:
                action = self.collections_menu.addAction(f"{collection.name} ({collection.item_count})")
                action.setToolTip(collection.description or collection.kind)
                action.triggered.connect(lambda _checked=False, target=collection.id: self._open_virtual_collection(target))
        else:
            empty_action = self.collections_menu.addAction("No collections yet")
            empty_action.setEnabled(False)
        if self.actions is not None:
            self._update_action_states()

    def _open_command_palette(self, _checked: bool = False, *, context: str | None = None) -> None:
        self._command_palette.open(_checked, context=context)

    def _handle_command_palette_finished(self, result: int) -> None:
        self._command_palette.handle_finished(result)

    def _apply_filter_preset(self, preset: SavedFilterPreset) -> None:
        self._records_view.apply_filter_preset(preset)

    def _save_current_filter_preset(self) -> None:
        self._records_view.save_current_filter_preset()

    def _delete_current_filter_preset(self) -> None:
        self._records_view.delete_current_filter_preset()

    def _handle_system_color_scheme_changed(self) -> None:
        if self._appearance_mode == AppearanceMode.AUTO:
            self._appearance.apply_appearance()

    def _apply_default_workspace(self) -> None:
        if self.workspace_docks is None:
            return
        self.workspace_docks.reset_layout()

    def _restore_window_state(self) -> None:
        restored, window_state = restore_window_layout(
            self,
            self._settings,
            self.GEOMETRY_KEY,
            self.STATE_KEY,
            self.workspace_docks,
        )
        # The layout is proportioned to a maximized window, so that is how it
        # opens (a saved fullscreen session still reopens fullscreen).
        self._startup_window_state = window_state if window_state == "fullscreen" else "maximized"
        if self._startup_window_state == "maximized":
            # Maximized before the first show, so the window appears at its
            # final size and the panes are laid out once, not at the restored
            # size first and again after a maximize.
            self.setWindowState(self.windowState() | Qt.WindowState.WindowMaximized)
        # TEMPORARY: translucent so the window can be laid over the design
        # reference while the ratios are tuned. Set WINDOW_OPACITY back to 1.0
        # (or run with IMAGE_TRIAGE_OPACITY=1) when that is done.
        self.setWindowOpacity(_window_opacity())
        if not restored:
            self._apply_default_workspace()

    def _save_window_state(self) -> None:
        self._save_details_view_state()
        save_window_layout(self, self._settings, self.GEOMETRY_KEY, self.STATE_KEY, self.workspace_docks)

    def _restart_app_for_development(self) -> None:
        self.statusBar().showMessage("Restarting Image Triage...")
        self._remember_current_folder_view_state()
        self._save_window_state()
        self._settings.sync()

        # Free everything holding the GPU BEFORE the replacement launches, or the
        # two instances fight over CUDA (the replacement's mask/index work then
        # queues for tens of seconds). Cancelling the index tasks also unblocks
        # shutdown: their QThreadPools waitForDone() on teardown, so a running
        # AuraFace/TinyCLIP pass would otherwise keep this process (and its GPU
        # session) alive for minutes — the "reload leaves a zombie" bug.
        try:
            self._records_view.suspend_background_indexing()
        except Exception:
            _logger.exception("Failed to suspend background indexing before dev restart")
        try:
            # Kill the mask_engine_worker child directly rather than via
            # service.shutdown(): shutdown() takes the service lock, which would
            # deadlock if a mask op is currently wedged holding it. We are exiting
            # anyway, so a lock-free kill is correct and can't hang.
            from .mask_engine_service import default_mask_engine_service

            worker = getattr(default_mask_engine_service(), "_process", None)
            if worker is not None and worker.poll() is None:
                worker.kill()
        except Exception:
            _logger.exception("Failed to kill mask_engine_worker before dev restart")

        args = [sys.executable, "-m", "image_triage", *sys.argv[1:]]
        cwd = Path(__file__).resolve().parent.parent
        creation_flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        try:
            subprocess.Popen(
                args,
                cwd=str(cwd),
                close_fds=True,
                creationflags=creation_flags,
            )
        except OSError as exc:
            QMessageBox.warning(self, "Restart Failed", f"Could not restart Image Triage.\n\n{exc}")
            self.statusBar().showMessage("Restart failed.")
            return
        # Guarantee this instance actually exits. close() alone is unreliable
        # here — a running background task, the mask worker, or a closeEvent
        # prompt can keep the event loop (and the GPU) alive. State is already
        # saved and the worker shut down, so a hard exit is safe for a dev reload.
        self.close()
        os._exit(0)

    def _restore_details_view_state(self) -> None:
        header_state = self._settings.value(self.DETAILS_HEADER_STATE_KEY, QByteArray())
        if isinstance(header_state, QByteArray):
            self.details_view.restore_header_state(header_state)
        try:
            sort_column = int(self._settings.value(self.DETAILS_SORT_COLUMN_KEY, 0, int))
        except (TypeError, ValueError):
            sort_column = 0
        sort_order_raw = str(self._settings.value(self.DETAILS_SORT_ORDER_KEY, "asc", str) or "asc")
        sort_order = Qt.SortOrder.DescendingOrder if sort_order_raw == "desc" else Qt.SortOrder.AscendingOrder
        self.details_view.set_sort_state(sort_column, sort_order)

    def _save_details_view_state(self) -> None:
        if getattr(self, "details_view", None) is None:
            return
        self._settings.setValue(self.DETAILS_HEADER_STATE_KEY, self.details_view.save_header_state())
        sort_column, sort_order = self.details_view.sort_state()
        self._settings.setValue(self.DETAILS_SORT_COLUMN_KEY, sort_column)
        self._settings.setValue(
            self.DETAILS_SORT_ORDER_KEY,
            "desc" if sort_order == Qt.SortOrder.DescendingOrder else "asc",
        )

    def _finish_startup_restore(self) -> None:
        if self._startup_launch_target and self._open_launch_target(self._startup_launch_target, chunked_restore=True):
            self._startup_launch_target = ""
        else:
            self._startup_launch_target = ""
            if self._quick_view_mode:
                self._show_main_window_after_quick_view_failure()
            self._load_start_folder()
            self._ai_run.restore_ai_results()
        if not self._quick_view_mode:
            QTimer.singleShot(0, self._ai_setup.maybe_prompt_for_ai_setup)

    # The runtime install status is a filesystem scan (~120ms) that only changes
    # when the user installs the runtime or downloads a model. It was previously
    # re-scanned on every call — and _update_ai_toolbar_state calls it twice —
    # so cache it with a short TTL and invalidate on install/download.
    _AI_RUNTIME_STATUS_TTL_S = 60.0

    @classmethod
    def _normalize_preview_preload_batch_size(cls, value: object) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return cls.PREVIEW_PRELOAD_BATCH_SIZE_DEFAULT
        return max(0, min(cls.PREVIEW_PRELOAD_BATCH_SIZE_MAX, parsed))

    @classmethod
    def _normalize_ai_embed_batch_size(cls, value: object) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return cls.AI_EMBED_BATCH_SIZE_AUTO
        if parsed <= 0:
            return cls.AI_EMBED_BATCH_SIZE_AUTO
        return min(256, parsed)

    @classmethod
    def _normalize_ai_dispute_weight(cls, value: object) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return cls.AI_DISPUTE_WEIGHT_DEFAULT
        return max(cls.AI_DISPUTE_WEIGHT_MIN, min(cls.AI_DISPUTE_WEIGHT_MAX, parsed))

    @classmethod
    def _normalize_ai_keep_top_percent(cls, value: object) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return cls.AI_KEEP_TOP_PERCENT_DEFAULT
        return max(cls.AI_KEEP_TOP_PERCENT_MIN, min(cls.AI_KEEP_TOP_PERCENT_MAX, parsed))

    @classmethod
    def _normalize_ai_review_band_percent(cls, value: object) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return cls.AI_REVIEW_BAND_PERCENT_DEFAULT
        return max(cls.AI_REVIEW_BAND_PERCENT_MIN, min(cls.AI_REVIEW_BAND_PERCENT_MAX, parsed))

    @classmethod
    def _normalize_ai_base_score_weight_percent(cls, value: object) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return cls.AI_BASE_SCORE_WEIGHT_PERCENT_DEFAULT
        return max(cls.AI_BASE_SCORE_WEIGHT_PERCENT_MIN, min(cls.AI_BASE_SCORE_WEIGHT_PERCENT_MAX, parsed))

    def _apply_base_score_blend_to_workflow(self) -> None:
        """Push the user's Base score weight slider into the aiculler_workflow
        module so the next bundle build / adapter export uses it."""

        from .aiculler_workflow import set_base_score_blend_weight
        set_base_score_blend_weight(self._ai_base_score_weight_percent_setting / 100.0)

    def _apply_cull_thresholds_to_classifier(self) -> None:
        """Push the user's Keep/Review sliders into the ai_results module so
        the next bundle load uses them. Keep top X% becomes a >= (100 - X)
        keeper threshold; the review band sits just below that."""

        keep_top = float(self._ai_keep_top_percent_setting)
        review_band = float(self._ai_review_band_percent_setting)
        keeper_threshold = max(0.0, 100.0 - keep_top)
        reject_threshold = max(0.0, keeper_threshold - review_band)
        set_cull_thresholds(
            keeper_percentile=keeper_threshold,
            reject_percentile=reject_threshold,
        )

    def _load_phash_prefilter_settings(self) -> PHashPrefilterSettings:
        defaults = default_phash_prefilter_settings()
        return PHashPrefilterSettings(
            enabled=self._settings.value(
                self.PHASH_PREFILTER_ENABLED_KEY,
                defaults.enabled,
                bool,
            ),
            hamming_threshold=max(
                0,
                min(
                    64,
                    int(
                        self._settings.value(
                            self.PHASH_PREFILTER_HAMMING_THRESHOLD_KEY,
                            defaults.hamming_threshold,
                            int,
                        )
                    ),
                ),
            ),
            cache_enabled=self._settings.value(
                self.PHASH_PREFILTER_CACHE_ENABLED_KEY,
                defaults.cache_enabled,
                bool,
            ),
            diagnostics_enabled=self._settings.value(
                self.PHASH_PREFILTER_DIAGNOSTICS_KEY,
                defaults.diagnostics_enabled,
                bool,
            ),
        ).normalized()

    def _save_phash_prefilter_settings(self, settings: PHashPrefilterSettings) -> None:
        normalized = settings.normalized()
        self._settings.setValue(self.PHASH_PREFILTER_ENABLED_KEY, normalized.enabled)
        self._settings.setValue(self.PHASH_PREFILTER_HAMMING_THRESHOLD_KEY, normalized.hamming_threshold)
        self._settings.setValue(self.PHASH_PREFILTER_CACHE_ENABLED_KEY, normalized.cache_enabled)
        self._settings.setValue(self.PHASH_PREFILTER_DIAGNOSTICS_KEY, normalized.diagnostics_enabled)

    def _default_ai_embed_batch_size(self) -> int:
        runtime_status = self._ai_setup.managed_ai_runtime_status()
        device = (self._ai_runtime.device or "auto").strip().lower()
        if device == "cpu":
            return self.AI_EMBED_BATCH_SIZE_CPU_AUTO
        if device.startswith("cuda"):
            return self.AI_EMBED_BATCH_SIZE_GPU_AUTO
        if (
            runtime_status.preferred_variant == AI_RUNTIME_CPU_VARIANT
            and AI_RUNTIME_GPU_VARIANT not in runtime_status.installed_variants
        ):
            return self.AI_EMBED_BATCH_SIZE_CPU_AUTO
        return self.AI_EMBED_BATCH_SIZE_GPU_AUTO

    def _configured_ai_embed_batch_size(self) -> int:
        if self._ai_embed_batch_size_setting > 0:
            return self._ai_embed_batch_size_setting
        return self._default_ai_embed_batch_size()

    def _ai_embed_batch_size_label(self) -> str:
        if self._ai_embed_batch_size_setting > 0:
            return str(self._ai_embed_batch_size_setting)
        return f"Auto ({self._configured_ai_embed_batch_size()})"

    def _configured_aiculler_runtime(self, *, workers: int | None = None):
        return default_aiculler_runtime(
            workers=workers,
            device=self._ai_runtime.device,
        )

    # ------------------------------------------------------------------
    # Capability readiness, repair and diagnostics
    # ------------------------------------------------------------------

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._zen_mode_enabled:
            self._zen.set_zen_mode(False)
        self._remember_current_folder_view_state()
        if self.grid.zoom_mode() == "column":
            self._settings.setValue(self.VIEW_COLUMNS_KEY, self.grid.current_columns())
        self._folder_watch_refresh_timer.stop()
        if self._folder_watcher.directories():
            self._folder_watcher.removePaths(list(self._folder_watcher.directories()))
        self._annotation_persistence_queue.flush_blocking()
        if self._active_semantic_index_task is not None:
            self._active_semantic_index_task.cancel()
        if self._active_face_index_task is not None:
            self._active_face_index_task.cancel()
        self._aiculler.flush_aiculler_internal_label_cache()
        self._aiculler.flush_aiculler_global_label_queue()
        self._aiculler.shutdown_aiculler_telemetry_logger()
        self._save_window_state()
        perf_logger().log("app.close")
        perf_logger().flush()
        super().closeEvent(event)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self._startup_window_state_fixup_applied:
            return
        self._startup_window_state_fixup_applied = True
        self._apply_native_frame_styles()
        self._sync_window_control_glyphs()
        # Now, not on a timer: the first frame is drawn at the live ratios.
        self._appearance.apply_layout_ratios()
        # Don't auto-focus/select any control on startup (the path bar used to
        # grab focus and highlight its text).
        QTimer.singleShot(0, self._clear_startup_focus)
        # Once the viewport has a real width, snap the zoom slider to the grid's
        # actual tile width so dragging starts from the right place.
        QTimer.singleShot(0, self._sync_zoom_slider_from_grid)
        if self._startup_window_state in {"maximized", "fullscreen"}:
            QTimer.singleShot(0, self._apply_startup_window_state_fixup)
        else:
            # The saved/default client rectangle was clamped before show. Run
            # once more now that Windows has reported the real frame/title bar,
            # keeping the complete window above the taskbar.
            QTimer.singleShot(0, lambda: fit_window_to_available_geometry(self))

    def _clear_startup_focus(self) -> None:
        focused = self.focusWidget()
        if focused is not None and focused is not self:
            focused.clearFocus()
        combo = getattr(self, "topbar_path_combo", None)
        line_edit = combo.lineEdit() if combo is not None and hasattr(combo, "lineEdit") else None
        if line_edit is not None:
            line_edit.deselect()
        # The content grid claims focus a tick after show; re-run once to clear it
        # so nothing is focused/highlighted on boot.
        if not getattr(self, "_startup_focus_recheck_done", False):
            self._startup_focus_recheck_done = True
            QTimer.singleShot(0, self._clear_startup_focus)

    def _apply_startup_window_state_fixup(self) -> None:
        if self._startup_window_state == "fullscreen":
            if self.isMaximized():
                self.showNormal()
            if not self.isFullScreen():
                self.showFullScreen()
                return
            if os.name == "nt":
                self.showNormal()
                self.showFullScreen()
            return

        if self._startup_window_state != "maximized":
            return

        if self.isFullScreen():
            self.showNormal()
        # Ask Windows rather than Qt, whose state can say maximized while the
        # frameless window is only stretched. No restore-then-maximize toggle:
        # showMaximized goes through Windows now, and a toggle just redraws the
        # shell at the restored size for a frame.
        if getattr(self, "_custom_frame", False):
            try:
                zoomed = bool(ctypes.windll.user32.IsZoomed(int(self.winId())))  # type: ignore[attr-defined]
            except (AttributeError, OSError):
                zoomed = self.isMaximized()
        else:
            zoomed = self.isMaximized()
        if not zoomed:
            self.showMaximized()

    def _load_start_folder(self) -> None:
        last_folder = self._settings.value(self.LAST_FOLDER_KEY, "", str)
        if last_folder and not self._dir_confirmed_missing(last_folder):
            # On a network / removable drive nothing asks the share here: the scan worker opens the
            # folder (or the grid says it could not), and the saved last folder is kept either way.
            self._select_folder(last_folder, sync_tree=False, chunked_restore=True)
            self.folder_tree.clearSelection()
            self.folder_tree.setCurrentIndex(QModelIndex())

    def _open_launch_target(self, target: str, *, chunked_restore: bool = False) -> bool:
        normalized = self._normalize_for_gui(target)
        if not normalized:
            return False
        if self._is_slow_source_folder(normalized) or not path_policy.is_plain_local(normalized):
            # The share cannot be asked on the GUI thread, so tell a file from a folder by its name and
            # let the scan worker report a path that is not there.
            is_file = suffix_for_path(normalized) in IMAGE_SUFFIXES
            folder = os.path.normpath(str(Path(normalized).parent)) if is_file else normalized
            self._select_folder(
                folder,
                sync_tree=False,
                chunked_restore=chunked_restore,
                preferred_record_path=normalized if is_file else None,
            )
            self.folder_tree.clearSelection()
            self.folder_tree.setCurrentIndex(QModelIndex())
            return True
        if os.path.isdir(normalized):
            self._select_folder(normalized, sync_tree=False, chunked_restore=chunked_restore)
            self.folder_tree.clearSelection()
            self.folder_tree.setCurrentIndex(QModelIndex())
            return True
        if os.path.isfile(normalized):
            folder = normalize_filesystem_path(str(Path(normalized).parent))
            if folder and os.path.isdir(folder):
                self._select_folder(
                    folder,
                    sync_tree=False,
                    chunked_restore=chunked_restore,
                    preferred_record_path=normalized,
                )
                self.folder_tree.clearSelection()
                self.folder_tree.setCurrentIndex(QModelIndex())
                return True
        self.statusBar().showMessage(f"Launch target not found: {normalized}")
        return False

    def _record_and_index_for_loaded_path(self, path: str) -> tuple[int, ImageRecord] | None:
        target_key = _memory_path_key(path)
        if not target_key:
            return None
        for index, record in enumerate(self._records):
            if record.is_folder:
                continue
            if any(_memory_path_key(candidate) == target_key for candidate in record.stack_paths):
                return index, record
        return None

    def _maybe_open_startup_quick_view(self) -> bool:
        target = self._pending_quick_view_path
        if not self._quick_view_mode or not target or self.preview.isVisible():
            return False
        match = self._record_and_index_for_loaded_path(target)
        if match is None:
            return False
        index, record = match
        self._quick_view_source_overrides[record.path] = target
        self.grid.set_current_index(index)
        self._pending_quick_view_path = ""
        self._open_preview(index)
        return True

    def _show_main_window_after_quick_view_failure(self) -> None:
        if not self._quick_view_mode:
            return
        target = self._pending_quick_view_path
        self._quick_view_mode = False
        self._pending_quick_view_path = ""
        self.show()
        self.raise_()
        self.activateWindow()
        if target:
            self.statusBar().showMessage(f"Could not open {Path(target).name} in the quick viewer.")

    def _finish_quick_view_attempt_if_ready(self) -> None:
        if (
            self._quick_view_mode
            and self._pending_quick_view_path
            and not self._scan_in_progress
            and not self._records_view.records_view_chunk_active()
        ):
            self._show_main_window_after_quick_view_failure()

    def _is_slow_source_folder(self, folder: str | None = None) -> bool:
        return self._recycle_bin.is_slow_source_folder(folder)

    def _normalize_for_gui(self, path: str | None) -> str:
        """``normalize_filesystem_path`` for use on the GUI thread.

        That function resolves the path through the filesystem (memoized, but the first call for each path
        is a network round trip for a share, ~20 s if the share is asleep). So a share's path is only tidied
        here (``os.path.normpath``), not resolved; the scan worker resolves it exactly as before, and a
        local path is resolved as before."""
        raw = str(path or "").strip()
        if raw and (self._is_slow_source_folder(raw) or not path_policy.is_plain_local(raw)):
            return os.path.normpath(raw)
        return normalize_filesystem_path(raw)

    def _dir_confirmed_missing(self, path: str | None) -> bool:
        """True only when ``path`` is provably not a folder and finding out cannot block the GUI thread.

        A network or removable path is never touched here and is never reported missing: a share that is
        asleep makes ``os.path.isdir`` block for ~20 s and answer "no", and callers used to treat that as
        "deleted", dropping the entry from a saved list. Opening such a path is left to the scan worker.
        See ``path_policy``."""
        if not path or self._is_slow_source_folder(path):
            return False
        return path_policy.confirmed_missing(path)

    def _recycle_root_for_folder(self, folder: str | None = None) -> Path:
        return self._recycle_bin.recycle_root_for_folder(folder)

    def _choose_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Choose Folder", self._current_folder or QDir.homePath())
        if folder:
            self._select_folder(folder)

    def _select_folder(
        self,
        folder: str,
        *,
        sync_tree: bool = True,
        chunked_restore: bool = False,
        preferred_record_path: str | None = None,
    ) -> None:
        perf_logger().log(
            "folder.select",
            folder=folder,
            sync_tree=sync_tree,
            chunked_restore=chunked_restore,
            preferred_record_path=preferred_record_path or "",
        )
        slow_source = self._is_slow_source_folder(folder)
        if sync_tree and slow_source:
            perf_logger().log("folder.select.tree_sync_skipped", folder=folder, reason="slow_source")
        elif sync_tree:
            sync_start = time.perf_counter()
            index = self.folder_model.index(folder)
            if index.isValid():
                self.folder_tree.setCurrentIndex(index)
            perf_logger().duration(
                "folder.select.tree_sync",
                (time.perf_counter() - sync_start) * 1000.0,
                folder=folder,
                index_valid=index.isValid(),
            )
        self._load_folder(
            folder,
            chunked_restore=chunked_restore,
            preferred_record_path=preferred_record_path,
        )

    def _open_file_associations_dialog(self) -> None:
        dialog = FileAssociationsDialog(self)
        dialog.exec()

    def _handle_tree_selection(self, index) -> None:
        folder = self.folder_model.filePath(index)
        if folder:
            self._load_folder(folder)

    def _handle_drive_selected(self, index) -> None:
        if not index.isValid():
            return
        self.folder_tree.setRootIndex(index)
        self.folder_tree.clearSelection()
        self.folder_tree.setCurrentIndex(QModelIndex())
        self.drive_list.clearSelection()
        self._handle_tree_selection(index)

    def _refresh_drive_list(self) -> None:
        delegate = self.drive_list.itemDelegate()
        cache = getattr(delegate, "_usage_cache", None)
        if isinstance(cache, dict):
            cache.clear()
        self.drive_list.viewport().update()
        self._refresh_folder_tree()
        self._drive_list_fit_timer.start()

    @staticmethod
    def _drive_root_for(folder: str) -> str:
        drive, _rest = os.path.splitdrive(os.path.normpath(folder)) if folder else ("", "")
        if not drive:
            return ""
        return drive if drive.startswith("\\") else drive + os.sep

    def _sync_drive_sections(self) -> None:
        """Root the Folders tree at the current folder's drive and open the
        path down to it, without changing which row is selected."""
        tree = getattr(self, "folder_tree", None)
        if tree is None or not hasattr(self, "drive_list"):
            return
        folder = self._current_folder if self._scope_kind == "folder" else ""
        drive_root = self._drive_root_for(folder or "")
        if not drive_root:
            return
        if self._is_slow_source_folder(folder) or not path_policy.is_plain_local(drive_root):
            # QFileSystemModel.index() on a share that is asleep blocks the GUI thread for ~20 s, and this
            # runs on every folder open, so a share's drive is re-rooted only after a worker has seen it
            # answer. (_select_folder already skips selecting a share's folder in the tree.)
            self._request_drive_sections_sync(folder, drive_root)
            return
        self._apply_drive_sections(folder, drive_root)

    def _request_drive_sections_sync(self, folder: str, drive_root: str) -> None:
        self._drive_sync_token += 1
        token = self._drive_sync_token
        task = PathReachableTask(drive_root, token)
        task.signals.checked.connect(self._handle_drive_reachable, Qt.ConnectionType.QueuedConnection)
        self._drive_sync_tasks[token] = (task, folder, drive_root)
        QThreadPool.globalInstance().start(task)

    def _handle_drive_reachable(self, token: int, _path: str, reachable: bool) -> None:
        pending = self._drive_sync_tasks.pop(token, None)
        if pending is None or token != self._drive_sync_token or not reachable:
            return
        _task, folder, drive_root = pending
        if self._scope_kind != "folder" or _memory_path_key(self._current_folder) != _memory_path_key(folder):
            return  # the user has moved on while the worker was asking
        self._apply_drive_sections(folder, drive_root)

    def _apply_drive_sections(self, folder: str, drive_root: str) -> None:
        tree = self.folder_tree
        root_index = self.folder_model.index(drive_root)
        if not root_index.isValid():
            return
        if tree.rootIndex() != root_index:
            tree.setRootIndex(root_index)
        target = self.folder_model.index(folder)
        ancestors: list[QModelIndex] = []
        parent = target.parent() if target.isValid() else QModelIndex()
        while parent.isValid() and parent != root_index:
            ancestors.append(parent)
            parent = parent.parent()
        for ancestor in reversed(ancestors):
            tree.expand(ancestor)

    def _handle_favorite_activated(self, item: QListWidgetItem) -> None:
        folder = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(folder, str) and folder and not self._dir_confirmed_missing(folder):
            self._select_folder(folder)

    def eventFilter(self, watched, event) -> bool:
        try:
            if hasattr(self, "central_container") and watched is self.central_container:
                if event.type() == QEvent.Type.Resize and hasattr(self, "zen_hint_overlay"):
                    self._zen.position_zen_hint_overlay()
            if watched is getattr(self, "browser_stack", None) and event.type() in (QEvent.Type.Resize, QEvent.Type.Move):
                self._toolbar.position_floating_toolbar()
            if watched in (
                getattr(self, "left_nav_rail", None),
                getattr(self, "left_nav_pages", None),
                getattr(self, "app_top_bar", None),
            ) and event.type() in (QEvent.Type.Resize, QEvent.Type.Move, QEvent.Type.Show, QEvent.Type.Hide):
                self._schedule_app_bar_alignment()
            path_combo = getattr(self, "topbar_path_combo", None)
            if path_combo is not None and watched is path_combo.lineEdit():
                if event.type() == QEvent.Type.FocusOut:
                    QTimer.singleShot(120, self._appearance.maybe_end_breadcrumb_path_edit)
                elif event.type() == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Escape:
                    self._appearance.end_breadcrumb_path_edit()
                    return True
            if hasattr(self, "topbar_action_stack") and watched is self.topbar_action_stack:
                if event.type() == QEvent.Type.Resize:
                    self._toolbar.update_topbar_overflow("manual")
            if hasattr(self, "workspace_bar") and watched is self.workspace_bar:
                if self._toolbar.handle_workspace_bar_drag_event(event):
                    return True
            if hasattr(self, "workspace_bar_drag_handle") and watched is self.workspace_bar_drag_handle:
                if self._toolbar.handle_workspace_bar_drag_event(event):
                    return True
            if hasattr(self, "toolbar_stack") and watched is self.toolbar_stack:
                if self._toolbar.handle_workspace_bar_drag_event(event):
                    return True
            if hasattr(self, "workspace_bar") and event.type() == QEvent.Type.Resize:
                if watched is self.workspace_bar or watched is self.toolbar_stack:
                    self._toolbar.schedule_workspace_toolbar_overflow_update("manual")
                    self._toolbar.schedule_workspace_toolbar_overflow_update("ai")
                elif watched is self.manual_toolbar:
                    self._toolbar.schedule_workspace_toolbar_overflow_update("manual")
                elif watched is self.ai_toolbar:
                    self._toolbar.schedule_workspace_toolbar_overflow_update("ai")
            folder_viewport = self.folder_tree.viewport() if hasattr(self, "folder_tree") else None
            if watched is folder_viewport:
                handled = self._handle_record_drop_event(event, source="folder_tree")
                if handled is not None:
                    return handled
            favorites_viewport = self.favorites_list.viewport() if hasattr(self, "favorites_list") else None
            if watched is favorites_viewport:
                handled = self._handle_record_drop_event(event, source="favorites")
                if handled is not None:
                    return handled
            return super().eventFilter(watched, event)
        except RuntimeError:
            return False

    def _handle_record_drop_event(self, event, *, source: str) -> bool | None:
        event_type = event.type()
        if event_type not in {
            QEvent.Type.DragEnter,
            QEvent.Type.DragMove,
            QEvent.Type.DragLeave,
            QEvent.Type.Drop,
        }:
            return None
        if self._collection_mode:
            event.ignore()
            return True
        if event_type == QEvent.Type.DragLeave:
            return False

        paths = ThumbnailGridView.dragged_record_paths_from_mime(event.mimeData())
        if not paths:
            return None

        point = event.position().toPoint()
        destination_folder = (
            self._folder_drop_target(point)
            if source == "folder_tree"
            else self._favorite_drop_target(point)
        )
        if not destination_folder or not self._can_accept_record_drop(destination_folder):
            event.ignore()
            return True

        copy_requested = self._drag_drop_prefers_copy(event)
        event.setDropAction(Qt.DropAction.CopyAction if copy_requested else Qt.DropAction.MoveAction)
        if event_type == QEvent.Type.Drop:
            event.accept()
            self._record_ops.handle_record_drop(paths, destination_folder, copy_requested=copy_requested)
            return True

        event.accept()
        return True

    def _folder_drop_target(self, point) -> str:
        index = self.folder_tree.indexAt(point)
        if not index.isValid():
            return ""
        folder = self.folder_model.filePath(index)
        # Runs on every drag-move over the tree, so it must never ask a share.
        return folder if folder and not self._dir_confirmed_missing(folder) else ""

    def _favorite_drop_target(self, point) -> str:
        item = self.favorites_list.itemAt(point)
        if item is None:
            return ""
        folder = item.data(Qt.ItemDataRole.UserRole)
        return folder if isinstance(folder, str) and folder and not self._dir_confirmed_missing(folder) else ""

    def _drag_drop_prefers_copy(self, event) -> bool:
        modifiers = QApplication.keyboardModifiers()
        if hasattr(event, "keyboardModifiers"):
            modifiers = event.keyboardModifiers()
        return bool(modifiers & Qt.KeyboardModifier.ControlModifier)

    def _can_accept_record_drop(self, destination_folder: str) -> bool:
        if not destination_folder or self._dir_confirmed_missing(destination_folder):
            return False
        if not self._current_folder:
            return False
        return normalized_path_key(destination_folder) != normalized_path_key(self._current_folder)

    def _show_folder_tree_context_menu(self, point) -> None:
        index = self.folder_tree.indexAt(point)
        if not index.isValid():
            return
        folder = self.folder_model.filePath(index)
        if not folder or self._dir_confirmed_missing(folder):
            return
        self._show_folder_context_menu(folder, self.folder_tree.viewport().mapToGlobal(point), is_favorite=folder in self._favorites)

    def _show_favorites_context_menu(self, point) -> None:
        item = self.favorites_list.itemAt(point)
        if item is None:
            return
        folder = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(folder, str) or not folder:
            return
        self._show_folder_context_menu(folder, self.favorites_list.viewport().mapToGlobal(point), is_favorite=True)

    def _show_folder_context_menu(self, folder: str, global_pos, *, is_favorite: bool) -> None:
        if self._collection_mode:
            menu = QMenu(self)
            open_action = menu.addAction("Open")
            if menu.exec(global_pos) == open_action:
                self._select_folder(folder)
            return
        menu = QMenu(self)
        open_action = menu.addAction("Open")
        explorer_label = "Open In File Explorer" if os.name == "nt" else "Open In File Manager"
        explorer_action = menu.addAction(explorer_label)
        menu.addSeparator()
        new_folder_action = menu.addAction("New Folder...")
        extract_archive_action = menu.addAction("Extract Archive Here...")
        rename_action = menu.addAction("Rename...")
        move_action = menu.addAction("Move Folder...")
        delete_action = menu.addAction("Delete Folder...")
        menu.addSeparator()
        catalog_action = menu.addAction("Remove From Library" if self._library_store.is_catalog_root(folder) else "Add To Library")
        favorite_action = menu.addAction("Remove From Favorites" if is_favorite else "Add To Favorites")
        can_modify = not self._is_filesystem_root(folder)
        rename_action.setEnabled(can_modify)
        move_action.setEnabled(can_modify)
        delete_action.setEnabled(can_modify)

        chosen = menu.exec(global_pos)
        if chosen is None:
            return
        if chosen == open_action:
            self._select_folder(folder)
            return
        if chosen == explorer_action:
            open_in_file_explorer(folder)
            return
        if chosen == new_folder_action:
            self._folder_ops.create_folder_prompt(folder, select_created=True)
            return
        if chosen == extract_archive_action:
            self._extract_archive_into_folder_prompt(folder)
            return
        if chosen == rename_action:
            self._folder_ops.rename_folder(folder)
            return
        if chosen == move_action:
            self._folder_ops.move_folder_prompt(folder)
            return
        if chosen == delete_action:
            self._folder_ops.delete_folder_prompt(folder)
            return
        if chosen == catalog_action:
            if self._library_store.is_catalog_root(folder):
                self._library_store.remove_catalog_root(folder)
                self._catalog.refresh_catalog_menu()
                self.statusBar().showMessage(f"Removed from library: {Path(folder).name}")
            else:
                self._library_store.add_catalog_root(folder)
                self._catalog.refresh_catalog_menu()
                self._catalog.start_catalog_refresh((folder,), label=f"Indexing {Path(folder).name} for the library...")
            return
        if chosen == favorite_action:
            if is_favorite:
                self._remove_favorite(folder)
            else:
                self._add_favorite(folder)

    @staticmethod
    def _is_filesystem_root(folder: str) -> bool:
        return FolderOpsController.is_filesystem_root(folder)

    def _open_resize_dialog(
        self,
        sources: list[ResizeSourceItem],
        *,
        title: str,
        scope_label: str,
        show_preview: bool | None = None,
        raw_note: str = "",
    ) -> bool:
        if not sources:
            return False
        dialog = ResizeDialog(
            sources,
            title=title,
            scope_label=scope_label,
            show_preview=show_preview,
            raw_note=raw_note,
            parent=self,
        )
        if self._exec_dialog_with_geometry(dialog, "resize") != dialog.DialogCode.Accepted:
            return False
        plan = dialog.accepted_plan()
        if not plan.can_apply:
            return False
        options = dialog.accepted_options()
        return self._apply_resize_plan(
            plan,
            options,
            refresh_folder=self._resize_refresh_folder(plan),
        )

    def _open_convert_dialog(
        self,
        sources: list[ConvertSourceItem],
        *,
        title: str,
        scope_label: str,
        show_preview: bool | None = None,
        raw_note: str = "",
    ) -> bool:
        if not sources:
            return False
        dialog = ConvertDialog(
            sources,
            title=title,
            scope_label=scope_label,
            show_preview=show_preview,
            raw_note=raw_note,
            parent=self,
        )
        if self._exec_dialog_with_geometry(dialog, "convert") != dialog.DialogCode.Accepted:
            return False
        plan = dialog.accepted_plan()
        if not plan.can_apply:
            return False
        options = dialog.accepted_options()
        return self._apply_convert_plan(
            plan,
            options,
            refresh_folder=self._resize_refresh_folder(plan),
        )

    def _resize_refresh_folder(self, plan: ResizePlan | ConvertPlan) -> str:
        if not self._current_folder:
            return ""
        current_folder_key = normalized_path_key(self._current_folder)
        for item in plan.executable_items:
            source_folder = str(Path(item.source.source_path).parent)
            target_folder = str(Path(item.target_path).parent)
            if normalized_path_key(source_folder) == current_folder_key:
                return self._current_folder
            if normalized_path_key(target_folder) == current_folder_key:
                return self._current_folder
        return ""

    def _apply_resize_plan(
        self,
        plan: ResizePlan,
        options: ResizeOptions,
        *,
        refresh_folder: str = "",
    ) -> bool:
        if not plan.executable_items:
            return False
        if self._active_resize_task is not None:
            QMessageBox.information(self, "Resize Running", "A resize task is already in progress.")
            return False
        dialog = self._show_resize_progress_dialog(max(1, len(plan.executable_items)))
        dialog.setLabelText("Preparing resize...")
        self._resize_context = ResizeExecutionContext(
            plan=plan,
            options=options,
            refresh_folder=refresh_folder,
        )
        task = ResizeApplyTask(plan, options)
        task.signals.started.connect(self._handle_resize_started, Qt.ConnectionType.QueuedConnection)
        task.signals.progress.connect(self._handle_resize_progress, Qt.ConnectionType.QueuedConnection)
        task.signals.finished.connect(self._handle_resize_finished, Qt.ConnectionType.QueuedConnection)
        task.signals.failed.connect(self._handle_resize_failed, Qt.ConnectionType.QueuedConnection)
        self._active_resize_task = task
        self._resize_pool.start(task)
        self.statusBar().showMessage(f"Applying resize for {len(plan.executable_items)} image(s)...")
        return True

    def _apply_convert_plan(
        self,
        plan: ConvertPlan,
        options: ConvertOptions,
        *,
        refresh_folder: str = "",
    ) -> bool:
        if not plan.executable_items:
            return False
        if self._active_convert_task is not None:
            QMessageBox.information(self, "Convert Running", "A convert task is already in progress.")
            return False
        dialog = self._show_convert_progress_dialog(max(1, len(plan.executable_items)))
        dialog.setLabelText("Preparing conversion...")
        self._convert_context = ConvertExecutionContext(
            plan=plan,
            options=options,
            refresh_folder=refresh_folder,
        )
        task = ConvertApplyTask(plan, options)
        task.signals.started.connect(self._handle_convert_started, Qt.ConnectionType.QueuedConnection)
        task.signals.progress.connect(self._handle_convert_progress, Qt.ConnectionType.QueuedConnection)
        task.signals.finished.connect(self._handle_convert_finished, Qt.ConnectionType.QueuedConnection)
        task.signals.failed.connect(self._handle_convert_failed, Qt.ConnectionType.QueuedConnection)
        self._active_convert_task = task
        self._convert_pool.start(task)
        self.statusBar().showMessage(f"Applying convert for {len(plan.executable_items)} image(s)...")
        return True

    def _workflow_refresh_folder(self, plan: WorkflowExportPlan) -> str:
        if not self._current_folder:
            return ""
        current_folder_key = normalized_path_key(self._current_folder)
        for item in plan.executable_items:
            target_folder = str(Path(item.target_path).parent)
            if normalized_path_key(target_folder) == current_folder_key:
                return self._current_folder
        return ""

    def _start_workflow_export_task(self, plan: WorkflowExportPlan) -> bool:
        if not plan.executable_items:
            return False
        if self._active_workflow_export_task is not None:
            QMessageBox.information(self, "Workflow Running", "A deliver / handoff export is already in progress.")
            return False
        dialog = self._show_workflow_progress_dialog(max(1, len(plan.executable_items)))
        dialog.setLabelText("Preparing workflow export...")
        destination_root = plan.destination_dir
        if plan.recipe.destination_subfolder:
            destination_root = str(Path(plan.destination_dir).parent)
        self._workflow_context = WorkflowExecutionContext(
            recipe=plan.recipe,
            action="export",
            destination_root=destination_root,
            destination_dir=plan.destination_dir,
            refresh_folder=self._workflow_refresh_folder(plan),
            archive_after_export=plan.recipe.archive_after_export,
            archive_format=plan.recipe.archive_format,
        )
        task = WorkflowExportTask(plan)
        task.signals.started.connect(self._handle_workflow_export_started, Qt.ConnectionType.QueuedConnection)
        task.signals.progress.connect(self._handle_workflow_export_progress, Qt.ConnectionType.QueuedConnection)
        task.signals.finished.connect(self._handle_workflow_export_finished, Qt.ConnectionType.QueuedConnection)
        task.signals.failed.connect(self._handle_workflow_export_failed, Qt.ConnectionType.QueuedConnection)
        self._active_workflow_export_task = task
        self._workflow_export_pool.start(task)
        self.statusBar().showMessage(f"Running export recipe: {plan.recipe.name}")
        return True

    def _handle_workflow_export_started(self, total_steps: int) -> None:
        dialog = self._show_workflow_progress_dialog(total_steps)
        dialog.setLabelText("Preparing workflow export...")

    def _handle_workflow_export_progress(self, current: int, total: int, message: str) -> None:
        dialog = self._show_workflow_progress_dialog(total)
        self._update_progress_dialog(
            dialog,
            current=current,
            total=total,
            message=message,
            default_label="Saving workflow outputs...",
        )

    def _handle_workflow_export_finished(self, written_paths: object) -> None:
        context = self._workflow_context
        written = tuple(path for path in written_paths if isinstance(path, str)) if isinstance(written_paths, (list, tuple)) else ()
        dialog = self._workflow_progress_dialog
        if dialog is not None and context is not None and (context.refresh_folder or context.archive_after_export):
            dialog.setRange(0, 0)
            dialog.setValue(0)
            if context.archive_after_export:
                dialog.setLabelText("Packaging archive...")
            else:
                dialog.setLabelText("Refreshing library...")

        self._active_workflow_export_task = None
        self._workflow_context = None
        self._close_workflow_progress_dialog()

        if context is None:
            self.statusBar().showMessage(f"Exported {len(written)} image(s)")
            return

        if context.destination_dir:
            self._remember_recent_destination(context.destination_dir)
        elif context.destination_root:
            self._remember_recent_destination(context.destination_root)

        if context.archive_after_export and written:
            archive_path = self._workflow_archive_path(context.recipe, context.destination_root or context.destination_dir)
            if archive_path:
                self._start_archive_create_task(
                    written,
                    archive_path,
                    archive_key=context.archive_format,
                    root_dir=context.destination_dir or None,
                    refresh_folder=context.refresh_folder,
                    archive_label=f"workflow archive for {context.recipe.name}",
                )
                self.statusBar().showMessage(f"Exported {len(written)} image(s), packaging archive...")
                return

        if context.refresh_folder:
            self.statusBar().showMessage(f"Exported {len(written)} image(s), refreshing folder...")
            self._load_folder(context.refresh_folder, force_refresh=True)
            return

        self.statusBar().showMessage(f"Exported {len(written)} image(s) with recipe: {context.recipe.name}")

    def _handle_workflow_export_failed(self, message: str) -> None:
        self._active_workflow_export_task = None
        self._workflow_context = None
        self._close_workflow_progress_dialog()
        QMessageBox.warning(self, "Workflow Export Failed", f"Could not apply the workflow export.\n\n{message}")


    def _handle_resize_started(self, total_steps: int) -> None:
        dialog = self._show_resize_progress_dialog(total_steps)
        dialog.setLabelText("Preparing resize...")

    def _handle_resize_progress(self, current: int, total: int, message: str) -> None:
        dialog = self._show_resize_progress_dialog(total)
        self._update_progress_dialog(
            dialog,
            current=current,
            total=total,
            message=message,
            default_label="Saving resized images...",
        )

    def _handle_resize_finished(self, written_paths: object) -> None:
        context = self._resize_context
        written = tuple(path for path in written_paths if isinstance(path, str)) if isinstance(written_paths, (list, tuple)) else ()
        dialog = self._resize_progress_dialog
        if dialog is not None and context is not None and context.refresh_folder:
            dialog.setRange(0, 0)
            dialog.setValue(0)
            dialog.setLabelText("Refreshing library...")

        self._active_resize_task = None
        self._resize_context = None
        self._close_resize_progress_dialog()

        if context is not None and context.refresh_folder:
            self.statusBar().showMessage(f"Resized {len(written)} image(s), refreshing folder...")
            self._load_folder(context.refresh_folder, force_refresh=True)
            return

        self.statusBar().showMessage(f"Resized {len(written)} image(s)")

    def _handle_resize_failed(self, message: str) -> None:
        self._active_resize_task = None
        self._resize_context = None
        self._close_resize_progress_dialog()
        QMessageBox.warning(self, "Resize Failed", f"Could not resize the selected image(s).\n\n{message}")

    def _handle_convert_started(self, total_steps: int) -> None:
        dialog = self._show_convert_progress_dialog(total_steps)
        dialog.setLabelText("Preparing conversion...")

    def _handle_convert_progress(self, current: int, total: int, message: str) -> None:
        dialog = self._show_convert_progress_dialog(total)
        self._update_progress_dialog(
            dialog,
            current=current,
            total=total,
            message=message,
            default_label="Saving converted images...",
        )

    def _handle_convert_finished(self, written_paths: object) -> None:
        context = self._convert_context
        written = tuple(path for path in written_paths if isinstance(path, str)) if isinstance(written_paths, (list, tuple)) else ()
        dialog = self._convert_progress_dialog
        if dialog is not None and context is not None and context.refresh_folder:
            dialog.setRange(0, 0)
            dialog.setValue(0)
            dialog.setLabelText("Refreshing library...")

        self._active_convert_task = None
        self._convert_context = None
        self._close_convert_progress_dialog()

        if context is not None and context.refresh_folder:
            self.statusBar().showMessage(f"Converted {len(written)} image(s), refreshing folder...")
            self._load_folder(context.refresh_folder, force_refresh=True)
            return

        self.statusBar().showMessage(f"Converted {len(written)} image(s)")

    def _handle_convert_failed(self, message: str) -> None:
        self._active_convert_task = None
        self._convert_context = None
        self._close_convert_progress_dialog()
        QMessageBox.warning(self, "Convert Failed", f"Could not convert the selected image(s).\n\n{message}")

    def _handle_archive_started(self, total_steps: int) -> None:
        context = self._archive_context
        dialog = self._show_archive_progress_dialog(total_steps, title="Extract Archive" if context and context.mode == "extract" else "Create Archive")
        dialog.setLabelText("Preparing archive..." if context and context.mode == "create" else "Preparing extraction...")

    def _handle_archive_progress(self, current: int, total: int, message: str) -> None:
        context = self._archive_context
        dialog = self._show_archive_progress_dialog(total, title="Extract Archive" if context and context.mode == "extract" else "Create Archive")
        if context is not None and context.mode == "extract":
            self._update_progress_dialog(
                dialog,
                current=current,
                total=total,
                message=message,
                default_label="Extracting archive...",
            )
        else:
            self._update_progress_dialog(
                dialog,
                current=current,
                total=total,
                message=message,
                default_label="Creating archive...",
            )

    def _handle_archive_finished(self, result: object) -> None:
        context = self._archive_context
        extracted = tuple(path for path in result if isinstance(path, str)) if isinstance(result, (list, tuple)) else ()
        created_path = result if isinstance(result, str) else ""
        dialog = self._archive_progress_dialog
        if dialog is not None and context is not None and context.refresh_folder:
            dialog.setRange(0, 0)
            dialog.setValue(0)
            dialog.setLabelText("Refreshing library...")

        self._active_archive_task = None
        self._archive_context = None
        self._close_archive_progress_dialog()

        if context is not None and context.mode == "extract":
            if context.destination_dir:
                self._remember_recent_destination(context.destination_dir)
            if context.refresh_folder:
                self.statusBar().showMessage(f"Extracted {len(extracted)} item(s), refreshing folder...")
                self._load_folder(context.refresh_folder, force_refresh=True)
                return
            self.statusBar().showMessage(f"Extracted {len(extracted)} item(s) to {context.destination_dir}")
            return

        if created_path:
            self._remember_recent_destination(str(Path(created_path).parent))
            label = context.archive_label if context is not None and context.archive_label else "archive"
            if context is not None and context.refresh_folder:
                self.statusBar().showMessage(f"Created {label} {Path(created_path).name}, refreshing folder...")
                self._load_folder(context.refresh_folder, force_refresh=True)
                return
            self.statusBar().showMessage(f"Created {label} {Path(created_path).name}")
            return

        self.statusBar().showMessage("Archive complete")

    def _handle_archive_failed(self, message: str) -> None:
        context = self._archive_context
        mode = context.mode if context is not None else "create"
        archive_label = context.archive_label if context is not None else "archive"
        self._active_archive_task = None
        self._archive_context = None
        self._close_archive_progress_dialog()
        if mode == "extract":
            QMessageBox.warning(self, "Extract Archive Failed", f"Could not extract the archive.\n\n{message}")
            return
        extra_note = ""
        if archive_label.startswith("workflow archive") and context is not None and context.destination_dir:
            extra_note = f"\n\nThe exported files were kept in:\n{context.destination_dir}"
        QMessageBox.warning(self, "Create Archive Failed", f"Could not create the archive.\n\n{message}{extra_note}")

    def _show_job_progress_dialog(self, *, key: str, total_steps: int, spec: JobSpec) -> QProgressDialog:
        controller = self._job_controllers.get(key)
        if controller is None:
            controller = JobController(self, spec)
            self._job_controllers[key] = controller
        dialog = controller.start(total_steps)
        return dialog

    def _close_job_progress_dialog(self, key: str) -> None:
        controller = self._job_controllers.pop(key, None)
        if controller is None:
            return
        controller.close()

    def _update_progress_dialog(
        self,
        dialog: QProgressDialog,
        *,
        current: int,
        total: int,
        message: str,
        default_label: str,
    ) -> None:
        upper = max(1, int(total))
        dialog.setRange(0, upper)
        dialog.setValue(min(max(int(current), 0), upper))
        dialog.setLabelText(message or default_label)

    def _show_resize_progress_dialog(self, total_steps: int) -> QProgressDialog:
        dialog = self._show_job_progress_dialog(
            key="resize",
            total_steps=total_steps,
            spec=JobSpec(
                title="Resize Images",
                preparing_label="Preparing resize...",
                running_label="Saving resized images...",
                indeterminate_label="Refreshing library...",
                window_modality=Qt.WindowModality.NonModal,
                stays_on_top=True,
            ),
        )
        self._resize_progress_dialog = dialog
        return dialog

    def _show_convert_progress_dialog(self, total_steps: int) -> QProgressDialog:
        dialog = self._show_job_progress_dialog(
            key="convert",
            total_steps=total_steps,
            spec=JobSpec(
                title="Convert Images",
                preparing_label="Preparing conversion...",
                running_label="Converting images...",
                indeterminate_label="Refreshing library...",
                window_modality=Qt.WindowModality.NonModal,
                stays_on_top=True,
            ),
        )
        self._convert_progress_dialog = dialog
        return dialog

    def _close_resize_progress_dialog(self) -> None:
        self._close_job_progress_dialog("resize")
        self._resize_progress_dialog = None

    def _close_convert_progress_dialog(self) -> None:
        self._close_job_progress_dialog("convert")
        self._convert_progress_dialog = None

    def _show_workflow_progress_dialog(self, total_steps: int) -> QProgressDialog:
        dialog = self._show_job_progress_dialog(
            key="workflow",
            total_steps=total_steps,
            spec=JobSpec(
                title="Deliver / Handoff",
                preparing_label="Preparing workflow export...",
                running_label="Saving workflow outputs...",
                indeterminate_label="Refreshing library...",
                window_modality=Qt.WindowModality.NonModal,
                stays_on_top=True,
            ),
        )
        self._workflow_progress_dialog = dialog
        return dialog

    def _close_workflow_progress_dialog(self) -> None:
        self._close_job_progress_dialog("workflow")
        self._workflow_progress_dialog = None

    def _show_archive_progress_dialog(self, total_steps: int, *, title: str) -> QProgressDialog:
        key = f"archive:{title.casefold()}"
        if self._archive_job_key != key:
            self._close_job_progress_dialog(self._archive_job_key)
        self._archive_job_key = key
        dialog = self._show_job_progress_dialog(
            key=key,
            total_steps=total_steps,
            spec=JobSpec(
                title=title,
                preparing_label="Preparing archive...",
                running_label="Processing archive...",
                indeterminate_label="Refreshing library...",
                window_modality=Qt.WindowModality.NonModal,
                stays_on_top=True,
            ),
        )
        self._archive_progress_dialog = dialog
        return dialog

    def _close_archive_progress_dialog(self) -> None:
        self._close_job_progress_dialog(self._archive_job_key)
        self._archive_progress_dialog = None

    def _center_window_dialog(self, dialog) -> None:
        if dialog is None:
            return
        frame = dialog.frameGeometry()
        frame.moveCenter(self.frameGeometry().center())
        dialog.move(frame.topLeft())

    def _dialog_geometry_key(self, dialog_id: str) -> str:
        normalized = "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in dialog_id.strip())
        return f"{self.DIALOG_GEOMETRY_KEY_PREFIX}/{normalized or 'dialog'}"

    def _restore_dialog_geometry(self, dialog: QDialog, dialog_id: str) -> bool:
        geometry = self._settings.value(self._dialog_geometry_key(dialog_id), QByteArray(), QByteArray)
        if isinstance(geometry, QByteArray) and not geometry.isEmpty():
            return dialog.restoreGeometry(geometry)
        return False

    def _save_dialog_geometry(self, dialog: QDialog, dialog_id: str) -> None:
        self._settings.setValue(self._dialog_geometry_key(dialog_id), dialog.saveGeometry())

    def _exec_dialog_with_geometry(self, dialog: QDialog, dialog_id: str):
        restored = self._restore_dialog_geometry(dialog, dialog_id)
        if not restored:
            frame = dialog.frameGeometry()
            frame.moveCenter(self.frameGeometry().center())
            dialog.move(frame.topLeft())
        result = dialog.exec()
        self._save_dialog_geometry(dialog, dialog_id)
        return result

    def _finalize_batch_rename(self, context: BatchRenameExecutionContext) -> None:
        renamed_items = [item for item in context.preview.items if item.status == "Rename"]
        if not renamed_items:
            return

        annotation_updates: list[tuple[str, ImageRecord, SessionAnnotation]] = []
        renamed_records_by_old_path: dict[str, ImageRecord] = {}
        undo_actions: list[UndoAction] = []

        for item in renamed_items:
            renamed_record = self._record_after_moves(item.record, item.planned_moves)
            renamed_records_by_old_path[item.record.path] = renamed_record

            annotation = context.loaded_annotations.get(item.record.path)
            if annotation is None and context.is_current_folder:
                annotation = self._annotations.pop(item.record.path, None)
            elif context.is_current_folder:
                self._annotations.pop(item.record.path, None)
            if annotation is not None:
                if context.is_current_folder and not annotation.is_empty:
                    self._annotations[renamed_record.path] = annotation
                annotation_updates.append((item.record.path, renamed_record, annotation))

            if context.is_current_folder:
                undo_actions.append(
                    UndoAction(
                        kind="move",
                        primary_path=item.record.path,
                        file_moves=item.planned_moves,
                        folder=context.folder,
                        session_id=self._session_id,
                    )
                )

        if annotation_updates:
            self._decision_store.move_annotations(self._session_id, annotation_updates)

        if context.is_current_folder:
            self._record_ops.replace_records_after_moves(renamed_records_by_old_path)
            self._record_ops.rekey_filter_metadata_after_moves(renamed_records_by_old_path)
            self._record_ops.push_undo_actions(undo_actions)
            current_path = context.current_path_before
            if current_path in renamed_records_by_old_path:
                current_path = renamed_records_by_old_path[current_path].path
            self._apply_records_view(current_path=current_path)
            self.statusBar().showMessage(f"Renamed {len(renamed_items)} image bundle(s)")
            return

        self.statusBar().showMessage(
            f"Renamed {len(renamed_items)} image bundle(s) in {Path(context.folder).name or context.folder} (undo is only available for the current folder)"
        )

    def _refresh_folder_tree(self) -> None:
        """Re-read the folder tree from disk.

        QFileSystemModel never re-lists a directory it has already populated,
        and its watcher does not fire on network shares, so new folders stayed
        invisible. Swapping in a fresh model is the only reliable re-read; the
        root, expanded branches and current selection are put back afterwards.
        """
        tree = self.folder_tree
        old_model = self.folder_model
        root_path = old_model.filePath(tree.rootIndex()) if tree.rootIndex().isValid() else ""
        current_path = old_model.filePath(tree.currentIndex()) if tree.currentIndex().isValid() else ""
        drive_path = (
            old_model.filePath(self.drive_list.currentIndex())
            if self.drive_list.currentIndex().isValid()
            else ""
        )
        expanded: list[str] = []

        def collect(parent: QModelIndex) -> None:
            for row in range(old_model.rowCount(parent)):
                child = old_model.index(row, 0, parent)
                if tree.isExpanded(child):
                    expanded.append(old_model.filePath(child))
                    collect(child)

        collect(tree.rootIndex())

        new_model = QFileSystemModel(self)
        new_model.setFilter(self._folder_tree_filter())
        new_model.setRootPath("")
        self.folder_model = new_model
        tree.setModel(new_model)
        self.drive_list.setModel(new_model)
        self.drive_list.setRootIndex(QModelIndex())
        for column in range(1, new_model.columnCount()):
            tree.hideColumn(column)
            self.drive_list.hideColumn(column)
        new_model.rowsInserted.connect(lambda *_args: self._drive_list_fit_timer.start())
        new_model.rowsRemoved.connect(lambda *_args: self._drive_list_fit_timer.start())
        new_model.layoutChanged.connect(lambda *_args: self._drive_list_fit_timer.start())
        old_model.deleteLater()

        def restorable(path: str) -> bool:
            # new_model.index() on a share that is asleep blocks the GUI thread for ~20 s, so only plain
            # local paths are put back here; a share's drive is re-rooted by _sync_drive_sections below
            # once a worker has seen it answer (its expanded branches and drive selection are not kept).
            return bool(path) and not self._is_slow_source_folder(path) and path_policy.is_plain_local(path)

        if restorable(root_path):
            root_index = new_model.index(root_path)
            if root_index.isValid():
                tree.setRootIndex(root_index)
        for path in expanded:
            if not restorable(path):
                continue
            index = new_model.index(path)
            if index.isValid():
                tree.expand(index)
        if restorable(current_path):
            index = new_model.index(current_path)
            if index.isValid():
                tree.setCurrentIndex(index)
        if restorable(drive_path):
            drive_index = new_model.index(drive_path)
            if drive_index.isValid():
                self.drive_list.setCurrentIndex(drive_index)
        self._sync_drive_sections()
        self._drive_list_fit_timer.start()

    def _handle_sort_changed(self) -> None:
        selected = self._selected_sort_mode()
        if selected is None:
            return
        self._set_sort_mode(selected)

    def _set_sort_mode(self, mode: SortMode) -> None:
        self._sort_mode = mode
        if mode == SortMode.AI_WOW:
            self._refresh_winner_scores_for_current_folder()
        self._records_view_cache.mark(ViewInvalidationReason.SORT_CHANGED)
        combo_index = self.sort_combo.findData(mode)
        if combo_index >= 0 and combo_index != self.sort_combo.currentIndex():
            self.sort_combo.setCurrentIndex(combo_index)
            return
        self._apply_records_view()
        self._scroll_active_view_to_top()
        self._remember_current_folder_view_state()
        self._update_action_states()

    def _handle_unified_search_finished(self, folder: str, token: int, result: object) -> None:
        self._records_view.handle_unified_search_finished(folder, token, result)

    def _handle_unified_search_failed(self, folder: str, token: int, message: str) -> None:
        self._records_view.handle_unified_search_failed(folder, token, message)

    def _handle_semantic_index_progress(self, folder: str, token: int, completed: int, total: int) -> None:
        self._records_view.handle_semantic_index_progress(folder, token, completed, total)

    def _handle_semantic_index_finished(self, folder: str, token: int, indexed: int, ready_total: int) -> None:
        self._records_view.handle_semantic_index_finished(folder, token, indexed, ready_total)

    def _handle_semantic_index_failed(self, folder: str, token: int, message: str) -> None:
        self._records_view.handle_semantic_index_failed(folder, token, message)

    def _handle_face_index_progress(self, folder: str, token: int, completed: int, total: int) -> None:
        self._records_view.handle_face_index_progress(folder, token, completed, total)

    def _handle_face_index_finished(self, folder: str, token: int, faces_indexed: int, people_count: int) -> None:
        self._records_view.handle_face_index_finished(folder, token, faces_indexed, people_count)

    def _handle_face_index_failed(self, folder: str, token: int, message: str) -> None:
        self._records_view.handle_face_index_failed(folder, token, message)

    def _open_advanced_filters_dialog(self) -> None:
        self._records_view.open_advanced_filters_dialog()

    def _clear_record_filters(self) -> None:
        self._records_view.clear_record_filters()

    @staticmethod
    def _normalize_column_count(value: object, *, default: int = 3) -> int:
        try:
            columns = int(value)
        except (TypeError, ValueError):
            columns = default
        return max(1, min(8, columns))

    @staticmethod
    def _normalize_browser_view_mode(value: object) -> str:
        text = str(value or "").strip().casefold()
        return "details" if text == "details" else "grid"

    @staticmethod
    def _normalize_details_row_density(value: object) -> str:
        text = str(value or "").strip().casefold()
        return text if text in {"compact", "comfortable"} else "comfortable"

    def _columns_to_zoom_slider_value(self, columns: object) -> int:
        # Slider remains left = smaller/more columns, right = larger/fewer columns.
        normalized = self._normalize_column_count(columns)
        return int(round(((8 - normalized) / 7) * 100))

    def _set_column_count(self, count: int, *, sync_slider: bool = True) -> None:
        columns = self._normalize_column_count(count)
        combo_index = self.columns_combo.findData(columns)
        if combo_index >= 0:
            with QSignalBlocker(self.columns_combo):
                self.columns_combo.setCurrentIndex(combo_index)
        if self.grid.current_columns() == columns and self.grid.zoom_mode() == "column":
            if sync_slider:
                self._sync_zoom_slider_from_grid()
            self._update_action_states()
            return
        self.grid.set_column_count(columns)
        self._settings.setValue(self.VIEW_COLUMNS_KEY, columns)
        # A discrete column choice clears any continuous zoom level.
        self._settings.remove(self.VIEW_ZOOM_WIDTH_KEY)
        if sync_slider:
            self._sync_zoom_slider_from_grid()
        self._remember_current_folder_view_state()
        self._update_action_states()

    def _sync_zoom_slider_from_grid(self) -> None:
        slider = getattr(self, "topbar_zoom_slider", None)
        if slider is None:
            return
        value = self._columns_to_zoom_slider_value(self.grid.current_columns())
        if slider.value() != value:
            with QSignalBlocker(slider):
                slider.setValue(value)

    def _set_browser_view_mode(self, mode: str) -> None:
        normalized = self._normalize_browser_view_mode(mode)
        if self._collection_mode and normalized != "grid":
            return
        if self._browser_view_mode == normalized and getattr(self, "browser_stack", None) is not None:
            self.browser_stack.setCurrentIndex(1 if normalized == "details" else 0)
            self._sync_details_view_from_grid()
            self._update_action_states()
            return
        current_index = self.grid.current_index()
        selected_indexes = self.grid.selected_indexes()
        self._folder_session.browser_view_mode = normalized
        self._settings.setValue(self.BROWSER_VIEW_MODE_KEY, normalized)
        if getattr(self, "browser_stack", None) is not None:
            self.browser_stack.setCurrentIndex(1 if normalized == "details" else 0)
        if normalized == "details":
            self.details_view.set_selected_indexes(selected_indexes, current_index=current_index)
            self.details_view.table.setFocus()
        else:
            self.grid.setFocus()
            self.grid.schedule_visible_thumbnail_requests()
        self._update_action_states()

    def _set_details_row_density(self, density: str) -> None:
        normalized = self._normalize_details_row_density(density)
        self._details_row_density = normalized
        self._settings.setValue(self.DETAILS_ROW_DENSITY_KEY, normalized)
        self.details_view.set_row_density(normalized)
        label = "compact" if normalized == "compact" else "comfortable"
        self.statusBar().showMessage(f"Details row density set to {label}")
        self._update_action_states()

    def _handle_performance_logging_toggled(self, checked: bool) -> None:
        self._performance_logging_enabled = bool(checked)
        self._settings.setValue(self.PERFORMANCE_LOGGING_KEY, self._performance_logging_enabled)
        perf_logger().set_enabled(self._performance_logging_enabled, reason="menu_toggle")
        if self._performance_logging_enabled:
            perf_logger().log("perf.menu_toggle_confirmed", log_dir=str(performance_log_dir()))
            perf_logger().flush()
            path = perf_logger().path
            self.statusBar().showMessage(f"Performance logging enabled: {path}")
        else:
            self.statusBar().showMessage("Performance logging disabled")
        self._update_action_states()

    def _open_performance_log_folder(self) -> None:
        if self._performance_logging_enabled and not perf_logger().is_writing:
            perf_logger().set_enabled(True, reason="open_log_folder_resync")
        path = perf_logger().path
        target = path.parent if path is not None else performance_log_dir()
        target.mkdir(parents=True, exist_ok=True)
        open_in_file_explorer(str(target))

    def _sync_details_view_from_grid(self) -> None:
        if getattr(self, "details_view", None) is None or self._syncing_browser_selection:
            return
        if self._browser_view_mode != "details":
            return
        self._syncing_browser_selection = True
        try:
            self.details_view.set_selected_indexes(
                self.grid.selected_indexes(),
                current_index=self.grid.current_index(),
            )
        finally:
            self._syncing_browser_selection = False

    def _handle_details_current_changed(self, index: int) -> None:
        if self._syncing_browser_selection:
            return
        if not 0 <= index < len(self._records):
            return
        selected_indexes = self.details_view.selected_indexes()
        if index not in selected_indexes:
            selected_indexes = [index]
        self._syncing_browser_selection = True
        try:
            self.grid.set_logical_selection(selected_indexes, current_index=index)
        finally:
            self._syncing_browser_selection = False
        self._update_action_states()
        self._update_status(index=index)
        self._update_inspector_context(index)

    def _handle_details_selection_changed(self) -> None:
        if self._syncing_browser_selection:
            return
        current_index = self.details_view.current_index()
        selected_indexes = self.details_view.selected_indexes()
        self._syncing_browser_selection = True
        try:
            self.grid.set_logical_selection(selected_indexes, current_index=current_index)
        finally:
            self._syncing_browser_selection = False
        self._update_action_states()
        self._update_status(index=current_index)
        self._update_inspector_context(current_index)

    def _jump_details_to_review_state(self, target: str) -> None:
        if not self._records:
            return
        start = self.grid.current_index()
        total = len(self._records)

        def matches(record: ImageRecord) -> bool:
            annotation = self._annotations.get(record.path, SessionAnnotation())
            if target == "kept":
                return annotation.winner and not record.is_folder
            if target == "rejected":
                return annotation.reject and not record.is_folder
            return not annotation.winner and not annotation.reject and not record.is_folder

        for offset in range(1, total + 1):
            index = (max(0, start) + offset) % total
            record = self._record_at(index)
            if record is not None and matches(record):
                if self._browser_view_mode != "details":
                    self._set_browser_view_mode("details")
                self.details_view.set_selected_indexes([index], current_index=index)
                self.grid.set_logical_selection([index], current_index=index)
                self._update_status(index=index)
                self.statusBar().showMessage(f"Details jumped to {record.name}")
                return
        label = {"kept": "kept", "rejected": "rejected"}.get(target, "unreviewed")
        self.statusBar().showMessage(f"No {label} image found in Details View")

    def _scroll_active_view_to_top(self) -> None:
        if getattr(self, "_browser_view_mode", "grid") == "details":
            self.details_view.table.scrollToTop()
        else:
            self.grid.verticalScrollBar().setValue(0)

    def _set_annotation_views(self, changed_paths: list[str] | tuple[str, ...] | set[str] | None = None) -> None:
        if changed_paths:
            if getattr(self.grid, "_annotations", None) is not self._annotations:
                self.grid.set_annotations(self._annotations)
                self.details_view.set_annotations(self._annotations)
            self.grid.update_annotations(changed_paths)
            changed_rows = {
                self._record_index_by_path[path]
                for path in changed_paths
                if path in self._record_index_by_path
            }
            self.details_view.refresh_rows(changed_rows)
            if self._preview_is_visible():
                for path in changed_paths:
                    annotation = self._annotations.get(path, SessionAnnotation())
                    self.preview.set_annotation_state(path, annotation.winner, annotation.reject, annotation.rating)
            return
        self.grid.set_annotations(self._annotations)
        self.details_view.set_annotations(self._annotations)

    def _refresh_viewport_mode(self) -> None:
        return

    def _selected_sort_mode(self) -> SortMode | None:
        selected = self.sort_combo.currentData()
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
        text = self.sort_combo.currentText()
        for mode in SortMode:
            if text == mode.value:
                return mode
        return None

    def _update_action_states(self, *, probe_folder_ai: bool | None = None) -> None:
        logger = perf_logger()
        start = time.perf_counter() if logger.enabled else 0.0
        if self.actions is None:
            return
        if probe_folder_ai is None:
            probe_folder_ai = not self._scan_in_progress

        current_index = self.grid.current_index()
        selected_records = self._selected_records_for_context(current_index) if current_index >= 0 else []
        has_selection = bool(selected_records)
        current_record = self._record_at(current_index)
        in_recycle_folder = self._is_recycle_folder()
        in_winners_folder = self._is_winners_folder()
        has_physical_folder = bool(self._current_folder)
        collections = self._library_store.list_collections()
        catalog_roots = self._library_store.list_catalog_roots()
        display_path = ""
        if current_record is not None and current_index >= 0:
            display_path = self.grid.displayed_variant_path(current_index) or current_record.path
        current_workflow = self._workflow_insight_for_record(current_record)
        can_open_winner_ladder = self._winner_ladder_candidate_count(current_index) >= 2

        self.actions.undo.setEnabled(bool(self._undo_stack))
        with QSignalBlocker(self.actions.compare_mode):
            self.actions.compare_mode.setChecked(self._compare_enabled)
        with QSignalBlocker(self.actions.auto_advance):
            self.actions.auto_advance.setChecked(self._auto_advance_enabled)
        with QSignalBlocker(self.actions.burst_groups):
            self.actions.burst_groups.setChecked(self._burst_groups_enabled)
        with QSignalBlocker(self.actions.burst_stacks):
            self.actions.burst_stacks.setChecked(self._burst_stacks_enabled)
        with QSignalBlocker(self.actions.show_hidden_folders):
            self.actions.show_hidden_folders.setChecked(self._show_hidden_folders)
        with QSignalBlocker(self.actions.grid_view):
            self.actions.grid_view.setChecked(self._browser_view_mode == "grid")
        with QSignalBlocker(self.actions.details_view):
            self.actions.details_view.setChecked(self._browser_view_mode == "details")
        with QSignalBlocker(self.actions.details_density_compact):
            self.actions.details_density_compact.setChecked(self._details_row_density == "compact")
        with QSignalBlocker(self.actions.details_density_comfortable):
            self.actions.details_density_comfortable.setChecked(self._details_row_density == "comfortable")
        with QSignalBlocker(self.actions.zen_mode):
            self.actions.zen_mode.setChecked(self._zen_mode_enabled)
        with QSignalBlocker(self.actions.performance_logging):
            self.actions.performance_logging.setChecked(self._performance_logging_enabled)
        if self._performance_logging_enabled and not perf_logger().is_writing:
            perf_logger().set_enabled(True, reason="action_state_resync")

        for mode, action in self.actions.appearance_actions.items():
            with QSignalBlocker(action):
                action.setChecked(self._appearance_mode == mode)
        for placement, action in self.actions.toolbar_placement_actions.items():
            with QSignalBlocker(action):
                action.setChecked(self._toolbar_placement == placement)
        for mode, action in self.actions.sort_actions.items():
            with QSignalBlocker(action):
                action.setChecked(self._sort_mode == mode)
        for mode, action in self.actions.filter_actions.items():
            with QSignalBlocker(action):
                action.setChecked(self._filter_query.quick_filter == mode)

        current_columns = self._normalize_column_count(self.columns_combo.currentData())
        for count, action in self.actions.column_actions.items():
            with QSignalBlocker(action):
                action.setChecked(current_columns == count)

        self.actions.open_preview.setEnabled(current_record is not None)
        self.actions.winner_ladder_mode.setEnabled(can_open_winner_ladder)
        self.actions.burst_groups.setEnabled(bool(self._current_folder and self._all_records))
        self.actions.burst_stacks.setEnabled(bool(self._current_folder and self._all_records))
        self.actions.show_hidden_folders.setEnabled(True)
        self.actions.grid_view.setEnabled(True)
        self.actions.details_view.setEnabled(True)
        self.actions.details_density_compact.setEnabled(True)
        self.actions.details_density_comfortable.setEnabled(True)
        self.actions.details_next_unreviewed.setEnabled(bool(self._records))
        self.actions.details_next_kept.setEnabled(bool(self._records))
        self.actions.details_next_rejected.setEnabled(bool(self._records))
        self.actions.zen_mode.setEnabled(True)
        self.actions.performance_logging.setEnabled(True)
        self.actions.open_performance_log_folder.setEnabled(True)
        self.actions.rename_selection.setEnabled(current_record is not None and has_physical_folder and not in_recycle_folder and not in_winners_folder)
        self.actions.batch_rename_selection.setEnabled(bool(self._current_folder and self._all_records) and not in_recycle_folder and not in_winners_folder)
        has_resizeable_records = self._records_have_resizable
        self.actions.batch_resize_selection.setEnabled(bool(self._current_folder and has_resizeable_records) and not in_recycle_folder)
        has_convertible_records = self._records_have_convertible
        self.actions.batch_convert_selection.setEnabled(bool(self._current_folder and has_convertible_records) and not in_recycle_folder)
        self.actions.extract_archive.setEnabled(bool(self._current_folder))
        self.actions.install_ai_runtime.setEnabled(True)
        self.actions.download_ai_model.setEnabled(True)
        self.actions.accept_selection.setEnabled(has_selection and has_physical_folder and not in_recycle_folder and not in_winners_folder)
        self.actions.reject_selection.setEnabled(has_selection and has_physical_folder and not in_recycle_folder and not in_winners_folder)
        self.actions.keep_selection.setEnabled(has_selection and has_physical_folder and not in_recycle_folder and not in_winners_folder)
        self.actions.move_selection.setEnabled(has_selection and has_physical_folder)
        self.actions.move_selection_to_new_folder.setEnabled(has_selection and has_physical_folder)
        self.actions.delete_selection.setEnabled(has_selection and has_physical_folder)
        self.actions.restore_selection.setEnabled(has_selection and has_physical_folder and in_recycle_folder)
        self.actions.reveal_in_explorer.setEnabled(bool(display_path))
        self.actions.open_in_photoshop.setEnabled(bool(selected_records and self._photoshop_executable))
        self.actions.review_ai_disagreements.setEnabled(self._ai_bundle is not None)
        self.actions.create_virtual_collection.setEnabled(True)
        self.actions.add_selection_to_collection.setEnabled(bool(collections))
        self.actions.remove_selection_from_collection.setEnabled(current_record is not None and bool(collections))
        self.actions.delete_virtual_collection.setEnabled(bool(collections))
        self.actions.browse_catalog.setEnabled(bool(catalog_roots))
        self.actions.add_current_folder_to_catalog.setEnabled(has_physical_folder)
        self.actions.add_folder_to_catalog.setEnabled(True)
        self.actions.remove_catalog_folder.setEnabled(bool(catalog_roots))
        self.actions.refresh_catalog.setEnabled(bool(catalog_roots) and self._active_catalog_task is None)
        self.actions.rebuild_folder_catalog_cache.setEnabled(has_physical_folder and not self._scan_in_progress)
        self.actions.share_to_phone.setEnabled(has_selection and not in_recycle_folder)
        self.actions.handoff_builder.setEnabled(has_selection and has_physical_folder and not in_recycle_folder)
        self.actions.send_to_editor_pipeline.setEnabled(has_selection and has_physical_folder and not in_recycle_folder and not in_winners_folder)
        self.actions.best_of_set_auto_assembly.setEnabled(bool(self._records) and (self._ai_bundle is not None or self._review_intelligence is not None))
        self.actions.keyboard_shortcuts.setEnabled(True)
        self.actions.save_workspace_preset.setEnabled(self.workspace_docks is not None)
        self.actions.new_folder.setEnabled(bool(self._current_folder))
        self.actions.save_filter_preset.setEnabled(self._filter_query.has_active_filters)
        self.actions.delete_filter_preset.setEnabled(self._records_view.matching_saved_filter_preset() is not None)
        self.actions.clear_filters.setEnabled(self._filter_query.has_active_filters)
        self.actions.check_for_updates.setEnabled(
            self._active_update_check_task is None
            and self._active_update_download_task is None
            and not self._update_installing
        )
        self._refresh_update_button_state()
        self._tool_mode.refresh_tool_mode_ui()
        self._refresh_directory_navigation_buttons()
        if self._collection_mode:
            self._limit_actions_for_collection_mode()
        # The checked states pushed above sit under QSignalBlocker, which also
        # swallows the changed() the top-bar buttons listen to.
        self._toolbar.sync_topbar_action_buttons()
        if logger.enabled:
            logger.duration(
                "window.update_action_states",
                (time.perf_counter() - start) * 1000.0,
                selected=len(selected_records),
                view=self._browser_view_mode,
                records=len(self._records),
            )

    def _limit_actions_for_collection_mode(self) -> None:
        """Leave image-finding controls available while blocking review/edit actions."""
        allowed = (
            self.actions.open_folder,
            self.actions.refresh_folder,
            self.actions.open_preview,
            self.actions.show_hidden_folders,
            self.actions.grid_view,
            self.actions.browse_catalog,
            self.actions.refresh_catalog,
            self.actions.advanced_filters,
            self.actions.clear_filters,
            *self.actions.sort_actions.values(),
            *self.actions.filter_actions.values(),
            *self.actions.ai_state_actions.values(),
            *self.actions.column_actions.values(),
        )
        for field_name in self.actions.__dataclass_fields__:
            value = getattr(self.actions, field_name)
            if isinstance(value, QAction):
                if value not in allowed:
                    value.setEnabled(False)
            elif isinstance(value, dict):
                for action in value.values():
                    if isinstance(action, QAction) and action not in allowed:
                        action.setEnabled(False)

    def _selected_records_for_actions(self) -> list[ImageRecord]:
        current_index = self.grid.current_index()
        if current_index < 0:
            return []
        return self._selected_records_for_context(current_index)

    def _open_current_preview(self) -> None:
        current_index = self.grid.current_index()
        if current_index >= 0:
            self._open_preview(current_index)

    def _open_winner_ladder(self) -> None:
        current_index = self.grid.current_index()
        if current_index < 0:
            return
        self._start_winner_ladder(current_index)

    def _review_ai_disagreements(self) -> None:
        if self._ai_bundle is None:
            self.statusBar().showMessage("Load AI results first to review disagreement cases.")
            return
        self._toolbar.sync_chrome_to_manual_review()
        self._filter_query.quick_filter = FilterMode.AI_DISAGREEMENTS
        self._records_view.apply_filter_query_change()
        self.statusBar().showMessage("Showing AI disagreement cases for targeted review.")

    def _selected_records_for_workflow(self) -> list[ImageRecord]:
        records = self._selected_records_for_actions()
        if records:
            return records
        current_index = self.grid.current_index()
        record = self._record_at(current_index)
        return [record] if record is not None else []

    def _create_virtual_collection_from_selection(self) -> None:
        self._catalog.create_virtual_collection_from_selection()

    def _begin_collection_mode(self, mode: str, *, collection: VirtualCollection | None = None) -> None:
        self._catalog.begin_collection_mode(mode, collection=collection)

    def _refresh_collection_mode_ui(self) -> None:
        self._catalog.refresh_collection_mode_ui()

    def _cancel_collection_mode(self, checked: bool = False, *, show_message: bool = True) -> None:
        self._catalog.cancel_collection_mode(checked, show_message=show_message)

    def _save_collection_mode(self, checked: bool = False) -> None:
        self._catalog.save_collection_mode(checked)

    def _open_virtual_collection(self, collection_id: str) -> None:
        self._catalog.open_virtual_collection(collection_id)

    def _add_selection_to_virtual_collection(self) -> None:
        self._catalog.add_selection_to_virtual_collection()

    def _remove_selection_from_virtual_collection(self) -> None:
        self._catalog.remove_selection_from_virtual_collection()

    def _delete_virtual_collection(self) -> None:
        self._catalog.delete_virtual_collection()

    def _browse_catalog(self, _checked: bool = False, *, root_path_override: str = "") -> None:
        self._catalog.browse_catalog(_checked, root_path_override=root_path_override)

    def _add_current_folder_to_catalog(self) -> None:
        self._catalog.add_current_folder_to_catalog()

    def _add_folder_to_catalog_prompt(self) -> None:
        self._catalog.add_folder_to_catalog_prompt()

    def _remove_catalog_root_prompt(self) -> None:
        self._catalog.remove_catalog_root_prompt()

    def _refresh_catalog_index(self) -> None:
        self._catalog.refresh_catalog_index()

    def _open_handoff_builder(self, _checked: bool = False, *, initial_recipe: WorkflowRecipe | None = None) -> None:
        records = self._selected_records_for_workflow()
        if not records or not self._current_folder:
            self.statusBar().showMessage("Select one or more images before building a handoff workflow.")
            return
        dialog = HandoffBuilderDialog(
            built_in_recipes=built_in_workflow_recipes(),
            saved_recipes=tuple(self._saved_workflow_recipes),
            default_destination_root=self._current_folder,
            selection_count=len(records),
            initial_recipe=initial_recipe,
            parent=self,
        )
        if self._exec_dialog_with_geometry(dialog, "handoff_builder") != dialog.DialogCode.Accepted:
            return
        updated_recipes = list(dialog.saved_recipes())
        if updated_recipes != self._saved_workflow_recipes:
            self._saved_workflow_recipes = updated_recipes
            self._save_saved_workflow_recipes()
            self._refresh_workflow_recipe_menu()
        result = dialog.result_data()
        self._run_workflow_recipe(result.recipe, destination_root=result.destination_root, records=records)

    def _pocketdrop_send_path_for(self, record: ImageRecord) -> str:
        """The path to hand PocketDrop for ``record``: the original file,
        unless the opt-in "apply edits" setting is on and this record has a
        real built-in editor session that renders successfully.

        Falls back to the original path whenever the setting is off, there is
        no sidecar, or the render fails -- sending to PocketDrop must never
        fail or silently drop a file just because a sidecar was unreadable.
        """

        if not self._apply_edits_to_pocketdrop:
            return record.path
        from .edit_storage import session_has_edits

        if not session_has_edits(record.path):
            return record.path
        try:
            from .edit_render_headless import render_edited_image

            rendered = render_edited_image(record.path)
            if rendered is None or rendered.isNull():
                return record.path
            folder = _pocketdrop_edited_export_dir()
            fd, temp_name = tempfile.mkstemp(prefix="pocketdrop_edit_", suffix=".jpg", dir=str(folder))
            os.close(fd)
            # JPEG: PocketDrop and the receiving end need a universally
            # supported format, and a RAW/PSD/etc. source can't be "rendered
            # with edits applied" back into its original format anyway.
            if not rendered.save(temp_name, "JPEG", quality=92):
                try:
                    os.unlink(temp_name)
                except OSError:
                    pass
                return record.path
            return temp_name
        except Exception:
            return record.path

    def _send_selection_to_pocketdrop(self, _checked: bool = False) -> None:
        """Add the selected files to PocketDrop and bring its page forward."""
        records = [record for record in self._selected_records_for_workflow() if record.path]
        if not records:
            self.statusBar().showMessage("Select one or more files to send with PocketDrop.")
            return
        panel = getattr(self, "pocketdrop_panel", None)
        if panel is None or not panel.available:
            detail = panel.error if panel is not None else ""
            self.statusBar().showMessage(f"PocketDrop isn't available. {detail}".strip())
            return
        _cleanup_pocketdrop_edited_exports()
        paths = [self._pocketdrop_send_path_for(record) for record in records]
        self._show_pocketdrop_page()
        panel.add_paths(paths)
        noun = "file" if len(paths) == 1 else "files"
        self.statusBar().showMessage(f"Added {len(paths)} {noun} to PocketDrop")

    def _open_send_to_editor_pipeline(self) -> None:
        recipe = next((item for item in built_in_workflow_recipes() if item.key == "send_to_editor"), None)
        self._open_handoff_builder(initial_recipe=recipe)

    def _open_best_of_set_builder(self) -> None:
        if not self._records:
            self.statusBar().showMessage("Load a folder before assembling a best-of set.")
            return
        dialog = BestOfSetDialog(visible_count=len(self._records), parent=self)
        if self._exec_dialog_with_geometry(dialog, "best_of_set") != dialog.DialogCode.Accepted:
            return
        result = dialog.result_data()
        plan = build_best_of_set_plan(
            self._records,
            ai_bundle=self._ai_bundle,
            review_bundle=self._review_intelligence,
            burst_recommendations=self._burst_recommendations,
            annotations_by_path=self._annotations,
            limit=result.limit,
            strategy=result.strategy,
        )
        if not plan.candidates:
            self.statusBar().showMessage("No best-of candidates were available for the current view.")
            return
        selected_indexes = [
            index
            for candidate in plan.candidates
            for index in [self._record_index_by_path.get(candidate.path)]
            if index is not None
        ]
        if not selected_indexes:
            self.statusBar().showMessage("The proposed best-of picks are no longer visible in the current view.")
            return
        self.grid.set_selected_indexes(selected_indexes, current_index=selected_indexes[0])
        summary = plan.summary_lines[0] if plan.summary_lines else f"Selected {len(selected_indexes)} best-of pick(s)."
        self.statusBar().showMessage(summary)

    def _open_keyboard_shortcuts_dialog(self) -> None:
        # Settings > Shortcuts is now the one editor for every rebindable key
        # (WI-3.2); this used to open a second, separate dialog with its own
        # store.
        self._show_settings(initial_section="Shortcuts")

    def _save_current_workspace_preset(self) -> None:
        if self.workspace_docks is None:
            return
        name, accepted = QInputDialog.getText(
            self,
            "Save Workspace Preset",
            "Preset name",
            text="Current Workspace",
        )
        if not accepted:
            return
        name = " ".join((name or "").split())
        if not name:
            return
        key = recipe_key_for_name(name) or "workspace_preset"
        preset = WorkspacePreset(
            key=key,
            name=name,
            description="Saved from the current workspace.",
            ui_mode="manual",
            columns=int(self.columns_combo.currentData() or 3),
            compare_enabled=self._compare_enabled,
            auto_advance=self._auto_advance_enabled,
            burst_groups=self._burst_groups_enabled,
            burst_stacks=self._burst_stacks_enabled,
            library_panel_mode=self.workspace_docks.library.mode,
            inspector_panel_mode=self.workspace_docks.inspector.mode,
            workspace_state=self.workspace_docks.save_state(),
        )
        existing_index = next((index for index, item in enumerate(self._saved_workspace_presets) if item.key == key), None)
        if existing_index is None:
            existing_index = next((index for index, item in enumerate(self._saved_workspace_presets) if item.name.casefold() == name.casefold()), None)
        if existing_index is not None:
            self._saved_workspace_presets[existing_index] = preset
        else:
            self._saved_workspace_presets.append(preset)
        self._save_saved_workspace_presets()
        self._refresh_workspace_preset_menu()
        self.statusBar().showMessage(f"Saved workspace preset: {preset.name}")

    def _apply_workspace_preset(self, preset: WorkspacePreset) -> None:
        if self.workspace_docks is None:
            return
        if preset.workspace_state:
            self.workspace_docks.restore_state(preset.workspace_state)
        else:
            self.workspace_docks.reset_layout()
            if preset.library_panel_mode == "collapsed":
                self.workspace_docks.collapse_panel("library")
            elif preset.library_panel_mode == "hidden":
                self.workspace_docks.hide_panel("library")
            if preset.inspector_panel_mode == "collapsed":
                self.workspace_docks.collapse_panel("inspector")
            elif preset.inspector_panel_mode == "hidden":
                self.workspace_docks.hide_panel("inspector")
        self._toolbar.sync_chrome_to_manual_review()
        if self._compare_enabled != preset.compare_enabled:
            self._handle_compare_toggled(preset.compare_enabled)
        if self._auto_advance_enabled != preset.auto_advance:
            self._handle_auto_advance_toggled(preset.auto_advance)
        if self._burst_groups_enabled != preset.burst_groups:
            self._handle_burst_groups_toggled(preset.burst_groups)
        if self._burst_stacks_enabled != preset.burst_stacks:
            self._handle_burst_stacks_toggled(preset.burst_stacks)
        self._set_column_count(preset.columns)
        self.statusBar().showMessage(f"Applied workspace preset: {preset.name}")

    def _rename_selected_record(self) -> None:
        current_index = self.grid.current_index()
        if current_index >= 0:
            self._record_ops.rename_record_prompt(current_index)

    def _record_supports_resize(self, record: ImageRecord | None) -> bool:
        if record is None or record.is_folder:
            return False
        suffix = suffix_for_path(record.path)
        return suffix not in RAW_SUFFIXES and suffix not in FITS_SUFFIXES and suffix not in MODEL_SUFFIXES

    def _record_supports_convert(self, record: ImageRecord | None) -> bool:
        if record is None or record.is_folder:
            return False
        suffix = suffix_for_path(record.path)
        return suffix not in RAW_SUFFIXES and suffix not in FITS_SUFFIXES and suffix not in MODEL_SUFFIXES

    def _refresh_record_capability_cache(self, records: list[ImageRecord] | None = None) -> None:
        source_records = self._all_records if records is None else records
        self._records_have_resizable = any(self._record_supports_resize(record) for record in source_records)
        self._records_have_convertible = any(self._record_supports_convert(record) for record in source_records)

    def _resize_source_for_index(self, index: int) -> ResizeSourceItem | None:
        record = self._record_at(index)
        if record is None or not self._record_supports_resize(record):
            return None

        displayed_path = self.grid.displayed_variant_path(index)
        candidates: list[str] = []
        if displayed_path and displayed_path in record.edited_paths:
            candidates.append(displayed_path)
        candidates.append(record.path)
        preview_source = self._preview_source_path(record)
        if preview_source:
            candidates.append(preview_source)
        candidates.extend(record.companion_paths)
        candidates.extend(record.edited_paths)
        if displayed_path:
            candidates.append(displayed_path)

        source_path = next((path for path in candidates if path and os.path.exists(path)), "")
        if not source_path:
            source_path = next((path for path in candidates if path), "")
        if not source_path:
            return None

        return ResizeSourceItem(
            source_path=source_path,
            source_name=Path(source_path).name,
        )

    def _convert_source_for_index(self, index: int) -> ConvertSourceItem | None:
        record = self._record_at(index)
        if record is None or not self._record_supports_convert(record):
            return None

        displayed_path = self.grid.displayed_variant_path(index)
        candidates: list[str] = []
        if displayed_path and displayed_path in record.edited_paths:
            candidates.append(displayed_path)
        candidates.append(record.path)
        preview_source = self._preview_source_path(record)
        if preview_source:
            candidates.append(preview_source)
        candidates.extend(record.companion_paths)
        candidates.extend(record.edited_paths)
        if displayed_path:
            candidates.append(displayed_path)

        source_path = next((path for path in candidates if path and os.path.exists(path)), "")
        if not source_path:
            source_path = next((path for path in candidates if path), "")
        if not source_path:
            return None

        return ConvertSourceItem(
            source_path=source_path,
            source_name=Path(source_path).name,
        )

    def _workflow_export_source_for_record(self, record: ImageRecord) -> ResizeSourceItem | None:
        preferred = record.preferred_edit_path or ""
        candidates: list[str] = []
        if preferred:
            candidates.append(preferred)
        preview_source = self._preview_source_path(record)
        if preview_source:
            candidates.append(preview_source)
        candidates.append(record.path)
        candidates.extend(record.companion_paths)
        candidates.extend(record.edited_paths)

        source_path = next((path for path in candidates if path and os.path.exists(path)), "")
        if not source_path:
            source_path = next((path for path in candidates if path), "")
        if not source_path:
            return None
        return ResizeSourceItem(source_path=source_path, source_name=Path(source_path).name)

    def _workflow_export_sources_for_records(self, records: list[ImageRecord]) -> list[ResizeSourceItem]:
        sources: list[ResizeSourceItem] = []
        seen: set[str] = set()
        for record in records:
            source = self._workflow_export_source_for_record(record)
            if source is None:
                continue
            key = normalized_path_key(source.source_path)
            if key in seen:
                continue
            seen.add(key)
            sources.append(source)
        return sources

    def _workflow_destination_dir(self, recipe: WorkflowRecipe, destination_root: str | None = None) -> str:
        return workflow_destination_dir(recipe, destination_root or self._current_folder or "")

    def _workflow_archive_path(self, recipe: WorkflowRecipe, destination_root: str | None = None) -> str:
        return workflow_archive_path(recipe, destination_root or self._current_folder or "")

    def _workflow_record_folder_name(self, record: ImageRecord) -> str:
        return workflow_record_folder_name(record.name)

    def _run_workflow_recipe(
        self,
        recipe: WorkflowRecipe,
        *,
        destination_root: str | None = None,
        records: list[ImageRecord] | None = None,
    ) -> None:
        selected_records = records if records is not None else self._selected_records_for_workflow()
        if not selected_records:
            self.statusBar().showMessage("Select one or more images before running an export recipe.")
            return

        destination_dir = self._workflow_destination_dir(recipe, destination_root)
        if recipe.uses_transform_export:
            if not destination_dir:
                self.statusBar().showMessage("Choose a destination folder for this handoff recipe.")
                return
            sources = self._workflow_export_sources_for_records(selected_records)
            if not sources:
                self.statusBar().showMessage("No exportable sources were available for the selected records.")
                return
            plan = build_workflow_export_plan(sources, recipe, destination_dir=destination_dir)
            if not plan.can_apply:
                if plan.general_error:
                    QMessageBox.warning(self, "Export Recipe", plan.general_error)
                else:
                    self.statusBar().showMessage("The export plan could not be built.")
                return
            self._start_workflow_export_task(plan)
            return

        if recipe.transfer_mode == RECIPE_TRANSFER_ARCHIVE:
            archive_path = self._workflow_archive_path(recipe, destination_root)
            source_paths = self._archive_source_paths_for_records(selected_records)
            if not archive_path or not source_paths:
                self.statusBar().showMessage("No bundle files were available to archive for this recipe.")
                return
            self._start_archive_create_task(
                source_paths,
                archive_path,
                archive_key=recipe.archive_format,
                root_dir=self._current_folder or None,
            )
            return

        if not destination_dir:
            self.statusBar().showMessage("Choose a destination folder for this export recipe.")
            return

        destructive = recipe.transfer_mode == RECIPE_TRANSFER_MOVE
        if destructive:
            confirmation = QMessageBox.question(
                self,
                "Run Export Recipe?",
                f"This recipe moves the selected bundles into:\n\n{destination_dir}\n\nContinue?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if confirmation != QMessageBox.StandardButton.Yes:
                return

        processed = 0
        for record in list(selected_records):
            target_dir = destination_dir
            if recipe.group_by_record_folder:
                target_dir = normalize_filesystem_path(str(Path(destination_dir) / self._workflow_record_folder_name(record)))
            if recipe.transfer_mode == RECIPE_TRANSFER_MOVE:
                if self._record_ops.move_record_to_path(record.path, target_dir):
                    processed += 1
            else:
                if self._record_ops.copy_record_to_path(record.path, target_dir):
                    processed += 1
        if processed:
            self._remember_recent_destination(destination_dir)
        action_label = "Moved" if recipe.transfer_mode == RECIPE_TRANSFER_MOVE else "Copied"
        self.statusBar().showMessage(f"{action_label} {processed} bundle(s) with recipe: {recipe.name}")

    def _resize_record_prompt(self, index: int) -> bool:
        if self._is_recycle_folder():
            return False
        source = self._resize_source_for_index(index)
        if source is None:
            self.statusBar().showMessage("Resize can't be used on RAW files.")
            return False
        return self._open_resize_dialog(
            [source],
            title="Resize Image",
            scope_label=f"Selected image: {source.source_name}",
            show_preview=False,
            raw_note="Resize can't be used on RAW files.",
        )

    def _convert_record_prompt(self, index: int) -> bool:
        if self._is_recycle_folder():
            return False
        source = self._convert_source_for_index(index)
        if source is None:
            self.statusBar().showMessage("Convert can't be used on RAW files.")
            return False
        return self._open_convert_dialog(
            [source],
            title="Convert Image",
            scope_label=f"Selected image: {source.source_name}",
            show_preview=False,
            raw_note="Convert can't be used on RAW files.",
        )

    def _archive_source_paths_for_records(self, records: list[ImageRecord]) -> tuple[str, ...]:
        ordered: list[str] = []
        seen: set[str] = set()
        for record in records:
            for path in self._record_paths(record):
                key = normalized_path_key(path)
                if key in seen or not os.path.exists(path):
                    continue
                seen.add(key)
                ordered.append(path)
        return tuple(ordered)

    def _default_archive_base_name(self, records: list[ImageRecord]) -> str:
        if len(records) == 1:
            return Path(records[0].name).stem or "archive"
        folder_name = Path(self._current_folder).name if self._current_folder else "selection"
        return f"{folder_name} selection".strip()

    def _archive_output_path_for_records(self, records: list[ImageRecord], archive_key: str) -> str:
        archive_format = archive_format_for_key(archive_key)
        initial_directory = self._current_folder or QDir.homePath()
        initial_path = str(Path(initial_directory) / f"{self._default_archive_base_name(records)}{archive_format.suffix}")
        chosen_path, _selected_filter = QFileDialog.getSaveFileName(
            self,
            f"Create {archive_format.label} Archive",
            initial_path,
            archive_format.save_filter,
        )
        if not chosen_path:
            return ""
        try:
            archive_path = ensure_archive_suffix(chosen_path, archive_format)
        except ValueError as exc:
            QMessageBox.warning(self, "Archive Path", str(exc))
            return ""
        if os.path.exists(archive_path):
            replace = QMessageBox.question(
                self,
                "Replace Archive?",
                f"{Path(archive_path).name} already exists.\n\nDo you want to replace it?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if replace != QMessageBox.StandardButton.Yes:
                return ""
        return archive_path

    def _create_archive_for_records(self, records: list[ImageRecord], archive_key: str) -> None:
        if not records:
            return
        archive_path = self._archive_output_path_for_records(records, archive_key)
        if not archive_path:
            return
        source_paths = self._archive_source_paths_for_records(records)
        if not source_paths:
            self.statusBar().showMessage("No files were available to archive.")
            return
        self._start_archive_create_task(
            source_paths,
            archive_path,
            archive_key=archive_key,
            root_dir=self._current_folder or None,
        )

    def _extract_archive_prompt(self) -> None:
        initial_directory = self._current_folder or QDir.homePath()
        archive_path, _selected_filter = QFileDialog.getOpenFileName(
            self,
            "Extract Archive",
            initial_directory,
            EXTRACT_ARCHIVE_FILTER,
        )
        if not archive_path:
            return
        default_destination = self._current_folder or str(Path(archive_path).parent)
        destination_dir = QFileDialog.getExistingDirectory(self, "Extract Archive To", default_destination)
        if not destination_dir:
            return
        self._start_archive_extract_task(archive_path, destination_dir)

    def _extract_archive_into_folder_prompt(self, destination_dir: str) -> None:
        if not destination_dir:
            return
        archive_path, _selected_filter = QFileDialog.getOpenFileName(
            self,
            "Extract Archive Here",
            destination_dir,
            EXTRACT_ARCHIVE_FILTER,
        )
        if not archive_path:
            return
        self._start_archive_extract_task(archive_path, destination_dir)

    def _start_archive_create_task(
        self,
        source_paths: tuple[str, ...],
        archive_path: str,
        *,
        archive_key: str,
        root_dir: str | None,
        refresh_folder: str = "",
        archive_label: str = "",
    ) -> None:
        if self._active_archive_task is not None:
            self.statusBar().showMessage("An archive task is already running.")
            return
        archive_format = archive_format_for_key(archive_key)
        task = CreateArchiveTask(source_paths, archive_path, archive_key=archive_key, root_dir=root_dir)
        task.signals.started.connect(self._handle_archive_started, Qt.ConnectionType.QueuedConnection)
        task.signals.progress.connect(self._handle_archive_progress, Qt.ConnectionType.QueuedConnection)
        task.signals.finished.connect(self._handle_archive_finished, Qt.ConnectionType.QueuedConnection)
        task.signals.failed.connect(self._handle_archive_failed, Qt.ConnectionType.QueuedConnection)
        self._active_archive_task = task
        self._archive_context = ArchiveExecutionContext(
            mode="create",
            archive_path=archive_path,
            destination_dir=str(Path(archive_path).parent),
            archive_label=archive_label or f"{archive_format.label} archive",
            refresh_folder=refresh_folder,
        )
        self._archive_pool.start(task)

    def _start_archive_extract_task(self, archive_path: str, destination_dir: str) -> None:
        if self._active_archive_task is not None:
            self.statusBar().showMessage("An archive task is already running.")
            return
        normalized_destination = normalize_filesystem_path(destination_dir)
        if not normalized_destination:
            return
        task = ExtractArchiveTask(archive_path, normalized_destination)
        task.signals.started.connect(self._handle_archive_started, Qt.ConnectionType.QueuedConnection)
        task.signals.progress.connect(self._handle_archive_progress, Qt.ConnectionType.QueuedConnection)
        task.signals.finished.connect(self._handle_archive_finished, Qt.ConnectionType.QueuedConnection)
        task.signals.failed.connect(self._handle_archive_failed, Qt.ConnectionType.QueuedConnection)
        self._active_archive_task = task
        self._archive_context = ArchiveExecutionContext(
            mode="extract",
            archive_path=archive_path,
            destination_dir=normalized_destination,
            refresh_folder=normalized_destination if normalized_path_key(normalized_destination) == normalized_path_key(self._current_folder) else "",
        )
        self._archive_pool.start(task)

    def _open_ai_workflow_center(self) -> None:
        dialog = getattr(self, "_ai_workflow_center_dialog", None)
        if dialog is None:
            dialog = AIWorkflowCenterDialog(self)
            self._ai_workflow_center_dialog = dialog
        else:
            dialog.refresh()
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _open_people_search_dialog(self) -> None:
        self._records_view.open_people_search_dialog()

    def _open_current_ai_review(self) -> None:
        if self._ai_bundle is None and not self._ai_run.load_hidden_ai_results_for_current_folder(show_message=True):
            self.statusBar().showMessage("Run Cull & Score first.")
            return
        self._toolbar.sync_chrome_to_manual_review()

    def _accept_selected_records(self) -> None:
        records = self._selected_records_for_actions()
        if records:
            self._batch_set_winner(records)

    def _reject_selected_records(self) -> None:
        records = self._selected_records_for_actions()
        if records:
            self._batch_set_reject(records)

    def _keep_selected_records(self) -> None:
        records = self._selected_records_for_actions()
        if records:
            self._batch_keep_records(records)

    def _move_selected_records(self) -> None:
        records = self._selected_records_for_actions()
        if records:
            self._record_ops.batch_move_records(records)

    def _move_selected_records_to_new_folder(self) -> None:
        records = self._selected_records_for_actions()
        if records:
            self._record_ops.batch_move_records_to_new_folder(records)

    def _delete_selected_records(self) -> None:
        records = self._selected_records_for_actions()
        if records:
            self._record_ops.batch_delete_records(records)

    def _restore_selected_records(self) -> None:
        records = self._selected_records_for_actions()
        if records:
            self._batch_restore_records(records)

    def _reveal_current_selection(self) -> None:
        current_index = self.grid.current_index()
        if current_index < 0:
            return
        record = self._record_at(current_index)
        if record is None:
            return
        reveal_in_file_explorer(self.grid.displayed_variant_path(current_index) or record.path)

    def _open_selected_in_photoshop(self) -> None:
        records = self._selected_records_for_actions()
        if not records:
            return
        if len(records) == 1:
            current_index = self.grid.current_index()
            record = records[0]
            display_path = self.grid.displayed_variant_path(current_index) or record.path
            if self._photoshop_executable:
                open_in_photoshop(display_path)
            return
        self._batch_open_in_photoshop(records)

    def _create_folder_in_current_folder(self) -> None:
        parent = self._current_folder or QDir.homePath()
        self._folder_ops.create_folder_prompt(parent, select_created=False)

    def _load_winner_mode(self) -> WinnerMode:
        raw = self._settings.value(self.WINNER_MODE_KEY, WinnerMode.COPY.value, str)
        for mode in WinnerMode:
            if raw in {mode.name, mode.value}:
                return mode
        return WinnerMode.COPY

    def _load_delete_mode(self) -> DeleteMode:
        raw = self._settings.value(self.DELETE_MODE_KEY, DeleteMode.SAFE_TRASH.value, str)
        for mode in DeleteMode:
            if raw in {mode.name, mode.value}:
                return mode
        return DeleteMode.SAFE_TRASH

    def _load_workflow_presets(self) -> list[WorkflowPreset]:
        raw = self._settings.value(self.WORKFLOW_PRESETS_KEY, "", str)
        if not isinstance(raw, str) or not raw:
            return []
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError):
            return []
        if not isinstance(payload, list):
            return []
        presets: list[WorkflowPreset] = []
        seen: set[str] = set()
        for item in payload:
            if not isinstance(item, dict):
                continue
            name = " ".join(str(item.get("name") or item.get("session_id") or "").split())
            session_id = " ".join(str(item.get("session_id") or name).split())
            if not name or name.casefold() in seen:
                continue
            winner_raw = str(item.get("winner_mode") or WinnerMode.COPY.value)
            delete_raw = str(item.get("delete_mode") or DeleteMode.SAFE_TRASH.value)
            winner_mode = next((mode for mode in WinnerMode if winner_raw in {mode.name, mode.value}), WinnerMode.COPY)
            delete_mode = next((mode for mode in DeleteMode if delete_raw in {mode.name, mode.value}), DeleteMode.SAFE_TRASH)
            presets.append(
                WorkflowPreset(
                    name=name,
                    session_id=session_id or name,
                    winner_mode=winner_mode,
                    delete_mode=delete_mode,
                )
            )
            seen.add(name.casefold())
        return presets

    def _save_workflow_presets(self) -> None:
        payload = [
            {
                "name": preset.name,
                "session_id": preset.session_id,
                "winner_mode": preset.winner_mode.value,
                "delete_mode": preset.delete_mode.value,
            }
            for preset in self._workflow_presets
        ]
        self._settings.setValue(self.WORKFLOW_PRESETS_KEY, json.dumps(payload))

    def _load_fast_rating_hint_sessions(self) -> set[str]:
        raw = self._settings.value(self.FAST_RATING_HINT_SESSIONS_KEY, "", str)
        if not isinstance(raw, str) or not raw:
            return set()
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError):
            return set()
        if not isinstance(payload, list):
            return set()
        return {str(item) for item in payload if isinstance(item, str) and item}

    def _save_fast_rating_hint_state(self) -> None:
        self._settings.setValue(self.FAST_RATING_HINT_DISABLED_KEY, self._fast_rating_hint_disabled)
        self._settings.setValue(self.FAST_RATING_HINT_SESSIONS_KEY, json.dumps(sorted(self._fast_rating_hint_sessions)))

    def _load_favorites(self) -> list[str]:
        raw = self._settings.value(self.FAVORITES_KEY, [], list)
        if isinstance(raw, str):
            raw = [raw]
        favorites: list[str] = []
        for path in raw or []:
            # Only a folder provably gone from a local drive is dropped; a share that is asleep stays.
            if isinstance(path, str) and path and not self._dir_confirmed_missing(path) and path not in favorites:
                favorites.append(path)
        return favorites

    def _load_recent_folders(self) -> list[str]:
        raw = self._settings.value(self.RECENT_FOLDERS_KEY, [], list)
        if isinstance(raw, str):
            raw = [raw]
        folders: list[str] = []
        seen: set[str] = set()
        for path in raw or []:
            if not isinstance(path, str) or not path or self._dir_confirmed_missing(path):
                continue
            # _memory_path_key, not normalized_path_key: that one resolves the path through the filesystem,
            # a network round trip per share path, and this is only for in-memory de-duplication.
            normalized = _memory_path_key(path)
            if normalized in seen:
                continue
            seen.add(normalized)
            folders.append(path)
        return folders[:12]

    def _load_folder_view_states(self) -> dict[str, dict[str, object]]:
        raw = self._settings.value(self.FOLDER_VIEW_STATE_KEY, "", str)
        if not isinstance(raw, str) or not raw:
            return {}
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError):
            return {}
        if not isinstance(payload, dict):
            return {}
        states: dict[str, dict[str, object]] = {}
        for key, value in payload.items():
            if not isinstance(key, str) or not isinstance(value, dict):
                continue
            state: dict[str, object] = {}
            if "columns" in value:
                state["columns"] = self._normalize_column_count(value.get("columns"))
            sort_value = value.get("sort")
            if isinstance(sort_value, str) and any(sort_value in {mode.name, mode.value} for mode in SortMode):
                state["sort"] = sort_value
            scroll_value = value.get("scroll")
            try:
                state["scroll"] = max(0, int(scroll_value))
            except (TypeError, ValueError):
                pass
            if state:
                states[key] = state
        return states

    def _load_recent_destinations(self) -> list[str]:
        raw = self._settings.value(self.RECENT_DESTINATIONS_KEY, [], list)
        if isinstance(raw, str):
            raw = [raw]
        destinations: list[str] = []
        seen: set[str] = set()
        for path in raw or []:
            if not isinstance(path, str) or not path or self._dir_confirmed_missing(path):
                continue
            normalized = _memory_path_key(path)  # pure; normalized_path_key would resolve a share path
            if normalized in seen:
                continue
            seen.add(normalized)
            destinations.append(path)
        return destinations[:10]

    def _load_saved_filter_presets(self) -> list[SavedFilterPreset]:
        raw = self._settings.value(self.SAVED_FILTERS_KEY, "", str)
        if not isinstance(raw, str) or not raw:
            return []
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError):
            return []
        if not isinstance(payload, list):
            return []
        presets: list[SavedFilterPreset] = []
        seen_names: set[str] = set()
        for item in payload:
            preset = deserialize_saved_filter_preset(item if isinstance(item, dict) else None)
            if preset is None:
                continue
            normalized_name = preset.name.casefold()
            if normalized_name in seen_names:
                continue
            seen_names.add(normalized_name)
            presets.append(preset)
        return presets

    def _load_recent_command_ids(self) -> list[str]:
        raw = self._settings.value(self.RECENT_COMMANDS_KEY, [], list)
        if isinstance(raw, str):
            raw = [raw]
        command_ids: list[str] = []
        for value in raw or []:
            if isinstance(value, str) and value and value not in command_ids:
                command_ids.append(value)
        return command_ids[:12]

    def _save_favorites(self) -> None:
        self._settings.setValue(self.FAVORITES_KEY, self._favorites)

    def _save_recent_folders(self) -> None:
        self._settings.setValue(self.RECENT_FOLDERS_KEY, self._recent_folders[:12])

    def _save_folder_view_states(self) -> None:
        self._settings.setValue(self.FOLDER_VIEW_STATE_KEY, json.dumps(self._folder_view_states))

    def _save_recent_destinations(self) -> None:
        self._settings.setValue(self.RECENT_DESTINATIONS_KEY, self._recent_destinations[:10])

    def _save_saved_filter_presets(self) -> None:
        payload = [serialize_saved_filter_preset(preset) for preset in self._saved_filter_presets]
        self._settings.setValue(self.SAVED_FILTERS_KEY, json.dumps(payload))

    def _save_recent_command_ids(self) -> None:
        self._settings.setValue(self.RECENT_COMMANDS_KEY, self._recent_command_ids[:12])

    def _sort_mode_from_state(self, value: object) -> SortMode | None:
        if isinstance(value, SortMode):
            return value
        if not isinstance(value, str):
            return None
        for mode in SortMode:
            if value in {mode.name, mode.value}:
                return mode
        return None

    def _current_folder_view_state(self) -> dict[str, object]:
        state: dict[str, object] = {
            "sort": self._sort_mode.value,
            "scroll": max(0, int(self.grid.current_scroll_value())),
            "current": self._pending_folder_focus_path or self._records_view.current_visible_record_path() or "",
        }
        return state

    def _remember_current_folder_view_state(self) -> None:
        if not getattr(self, "_current_folder", ""):
            return
        key = _memory_path_key(self._current_folder)
        if not key:
            return
        self._folder_view_states[key] = self._current_folder_view_state()
        if len(self._folder_view_states) > 120:
            self._folder_view_states = dict(list(self._folder_view_states.items())[-120:])
        self._save_folder_view_states()

    def _apply_folder_view_state(self, folder: str) -> None:
        state = self._folder_view_states.get(_memory_path_key(folder))
        self._pending_focus_scroll_top = False
        if not state:
            self._pending_folder_scroll_value = None
            return
        # Column count / zoom is program-wide, not per folder.
        sort_mode = self._sort_mode_from_state(state.get("sort"))
        if sort_mode is not None:
            self._sort_mode = sort_mode
            combo_index = self.sort_combo.findData(sort_mode)
            if combo_index >= 0:
                with QSignalBlocker(self.sort_combo):
                    self.sort_combo.setCurrentIndex(combo_index)
        if not self._restore_folder_position_enabled:
            self._pending_folder_scroll_value = None
            return
        saved_current = str(state.get("current") or "")
        if saved_current and not self._pending_folder_focus_path:
            self._pending_folder_focus_path = normalize_filesystem_path(saved_current)
            self._pending_focus_scroll_top = True
            self._pending_folder_scroll_value = None
            return
        try:
            self._pending_folder_scroll_value = max(0, int(state.get("scroll", 0)))
        except (TypeError, ValueError):
            self._pending_folder_scroll_value = None

    def _scroll_current_to_top(self, path: str) -> None:
        index = self._record_index_by_path.get(path)
        if index is not None:
            self.grid.scroll_index_to_top(index)

    def _restore_pending_folder_scroll(self) -> None:
        if self._pending_folder_scroll_value is None:
            return
        value = self._pending_folder_scroll_value
        self._pending_folder_scroll_value = None
        self.grid.restore_scroll_value(value)

    def _parent_folder_for_navigation(self) -> str:
        folder = self._current_folder if self._scope_kind == "folder" else ""
        if not folder:
            return ""
        if is_unc_path(folder):
            parts = str(folder).strip("\\").split("\\")
            if len(parts) <= 2:
                return ""
            if len(parts) == 3:
                return f"\\\\{parts[0]}\\{parts[1]}"
            return "\\\\" + "\\".join(parts[:-1])
        try:
            current = Path(folder)
            parent = current.parent
        except (OSError, ValueError):
            return ""
        if not str(parent) or parent == current:
            return ""
        return os.path.normpath(str(parent))

    def _only_child_folder_for_navigation(self) -> str:
        if self._scope_kind != "folder" or not self._current_folder or len(self._folder_records) != 1:
            return ""
        child = self._folder_records[0]
        if not child.is_folder or not child.name:
            return ""
        return os.path.normpath(os.path.join(self._current_folder, child.name))

    def _refresh_directory_navigation_buttons(self) -> None:
        parent_folder = self._parent_folder_for_navigation()
        child_folder = self._only_child_folder_for_navigation()
        child_count = len(self._folder_records) if self._scope_kind == "folder" and self._current_folder else 0

        up_tooltip = f"Open parent folder: {parent_folder}" if parent_folder else "Already at the top of this drive"
        if child_folder:
            down_tooltip = f"Open only child folder: {Path(child_folder).name}"
        elif child_count == 0:
            down_tooltip = "No child folders"
        else:
            down_tooltip = f"{child_count} child folders; choose one from the folder list"

        for button in getattr(self, "_directory_up_buttons", ()):
            button.setEnabled(bool(parent_folder))
            button.setToolTip(up_tooltip)
        for button in getattr(self, "_directory_down_buttons", ()):
            button.setEnabled(bool(child_folder))
            button.setToolTip(down_tooltip)
        topbar_up = getattr(self, "_topbar_up_button", None)
        if topbar_up is not None:
            topbar_up.setEnabled(bool(parent_folder))
            topbar_up.setToolTip(up_tooltip)

    def _adopt_menu_bar_shortcuts(self) -> None:
        pending = list(self.menuBar().actions())
        # Keyed by id() but holding the wrapper itself: QMenu.actions() returns temporary PySide
        # wrappers, and once one is dropped Python hands its address to another action's wrapper,
        # which a bare set of ids then skipped (random shortcuts were never adopted).
        seen: dict[int, object] = {}
        # The palette key already has the window's own QShortcut (CommandPaletteController, which
        # also follows remaps). The same key on an adopted action is ambiguous: Qt logs "Ambiguous
        # shortcut overload" and fires neither, so Ctrl+K would stop opening the palette.
        palette_action = getattr(getattr(self, "actions", None), "open_command_palette", None)
        while pending:
            action = pending.pop()
            if id(action) in seen:
                continue
            seen[id(action)] = action
            try:
                submenu = action.menu()
                if submenu is not None:
                    pending.extend(submenu.actions())
                elif (
                    not action.isSeparator()
                    and action.shortcuts()
                    and action is not palette_action
                    and action not in QWidget.actions(self)
                ):
                    self.addAction(action)
            except RuntimeError:
                # Rebuilt-on-open menus (recent folders, presets) can already
                # have dropped their entries; none of those carry shortcuts.
                continue

    def _navigate_to_parent_folder(self) -> None:
        target = self._parent_folder_for_navigation()
        if target:
            self._select_folder(target)

    def _navigate_to_only_child_folder(self) -> None:
        target = self._only_child_folder_for_navigation()
        if target:
            self._select_folder(target)

    def _update_nav_history_buttons(self) -> None:
        back_button = getattr(self, "_topbar_back_button", None)
        if back_button is not None:
            back_button.setEnabled(bool(getattr(self, "_nav_back", None)))
        forward_button = getattr(self, "_topbar_forward_button", None)
        if forward_button is not None:
            forward_button.setEnabled(bool(getattr(self, "_nav_forward", None)))

    def _remember_recent_folder(self, folder: str) -> None:
        normalized = os.path.normpath(str(folder).strip())
        if not normalized:
            return
        normalized_key = _memory_path_key(normalized)
        self._recent_folders = [
            normalized,
            *[
                item
                for item in self._recent_folders
                if _memory_path_key(item) != normalized_key
            ],
        ][:12]
        self._save_recent_folders()
        self._refresh_recent_folder_combos()

    def _recent_folder_paths(self, *, exclude_current_folder: bool = False) -> list[str]:
        valid: list[str] = []
        seen: set[str] = set()
        for path in self._recent_folders:
            key = _memory_path_key(path)
            if key in seen:
                continue
            seen.add(key)
            valid.append(path)
        if valid != self._recent_folders:
            self._recent_folders = valid[:12]
            self._save_recent_folders()
        if not exclude_current_folder or not self._current_folder:
            return valid
        current_key = _memory_path_key(self._current_folder)
        return [path for path in valid if _memory_path_key(path) != current_key]

    def _open_recent_folder(self, folder: str) -> None:
        if not self._dir_confirmed_missing(folder):
            # A share that does not answer is not "gone": the scan worker opens it, or reports the failure
            # in the grid, and the entry stays in the list.
            self._select_folder(folder)
            return
        missing_key = _memory_path_key(folder)
        self._recent_folders = [
            path for path in self._recent_folders if _memory_path_key(path) != missing_key
        ]
        self._save_recent_folders()
        self._refresh_recent_folder_combos()
        self.statusBar().showMessage("Recent folder no longer exists.")

    def _refresh_recent_folder_combos(self) -> None:
        self._appearance.refresh_breadcrumb()
        current_text = self._scope_display_label()
        current_folder = self._current_folder if self._scope_kind == "folder" and self._current_folder else ""
        for combo in (
            getattr(self, "manual_path_combo", None),
            getattr(self, "ai_path_combo", None),
            getattr(self, "topbar_path_combo", None),
        ):
            if combo is None:
                continue
            with QSignalBlocker(combo):
                combo.clear()
                combo.addItem(current_text, current_folder)
                recent_paths = self._recent_folder_paths(exclude_current_folder=True)
                if recent_paths:
                    combo.insertSeparator(combo.count())
                    for folder in recent_paths:
                        combo.addItem(folder, folder)
                combo.insertSeparator(combo.count())
                combo.addItem("Open Folder...", "__open_folder__")
                combo.setCurrentIndex(0)
                combo.setEditText(current_text)
            combo.setToolTip(current_text)
            line_edit = combo.lineEdit()
            if line_edit is not None:
                line_edit.setToolTip(current_text)
        self._refresh_directory_navigation_buttons()

    def _handle_path_combo_activated(self, combo: QComboBox, index: int) -> None:
        value = combo.itemData(index)
        if value == "__open_folder__":
            self._refresh_recent_folder_combos()
            self._choose_folder()
            return
        if isinstance(value, str) and value:
            if self._current_folder and _memory_path_key(value) == _memory_path_key(self._current_folder):
                self._refresh_recent_folder_combos()
                return
            self._open_recent_folder(value)
            return
        self._refresh_recent_folder_combos()

    def _handle_path_suggestion_accepted(self, folder: str) -> None:
        normalized = self._normalize_for_gui(folder)
        if not normalized or self._dir_confirmed_missing(normalized):
            self._refresh_recent_folder_combos()
            return
        if self._current_folder and _memory_path_key(normalized) == _memory_path_key(self._current_folder):
            self._refresh_recent_folder_combos()
            return
        self._select_folder(normalized)

    def _commit_path_combo_text(self, combo: QComboBox) -> None:
        raw_text = combo.currentText().strip().strip('"')
        folder = self._normalize_for_gui(raw_text)
        if not folder:
            self._refresh_recent_folder_combos()
            return
        if self._dir_confirmed_missing(folder):
            self.statusBar().showMessage(f"Folder not found: {folder}")
            self._refresh_recent_folder_combos()
            return
        if self._current_folder and _memory_path_key(folder) == _memory_path_key(self._current_folder):
            self._refresh_recent_folder_combos()
            return
        self._select_folder(folder)

    def _remember_recent_destination(self, destination_dir: str) -> None:
        normalized = self._normalize_for_gui(destination_dir)
        if not normalized or self._dir_confirmed_missing(normalized):
            return
        self._recent_destinations = [
            normalized,
            *[
                item
                for item in self._recent_destinations
                if _memory_path_key(item) != _memory_path_key(normalized)
            ],
        ][:10]
        self._save_recent_destinations()

    def _recent_destination_paths(self, *, exclude_current_folder: bool = False) -> list[str]:
        cleaned: list[str] = []
        seen: set[str] = set()
        for path in self._recent_destinations:
            if self._dir_confirmed_missing(path):
                continue
            normalized = _memory_path_key(path)
            if normalized in seen:
                continue
            seen.add(normalized)
            cleaned.append(path)
        # Only provably-gone folders and duplicates are ever dropped from the saved list. "Hide the folder
        # I am in" is a filter on what the menu shows: it used to be written back too, so merely opening
        # the Move-To menu erased the current folder from the saved destinations.
        if cleaned != self._recent_destinations:
            self._recent_destinations = cleaned[:10]
            self._save_recent_destinations()
        if not exclude_current_folder or not self._current_folder:
            return cleaned
        current_key = _memory_path_key(self._current_folder)
        return [path for path in cleaned if _memory_path_key(path) != current_key]

    def _add_recent_destination_actions(self, menu: QMenu, title: str) -> dict[QAction, str]:
        recent_menu = menu.addMenu(title)
        actions: dict[QAction, str] = {}
        for destination in self._recent_destination_paths(exclude_current_folder=True):
            label = Path(destination).name or destination
            action = recent_menu.addAction(f"{label}  [{destination}]")
            actions[action] = destination
        if not actions:
            empty_action = recent_menu.addAction("No recent folders")
            empty_action.setEnabled(False)
        return actions

    def _add_send_to_actions(self, menu: QMenu) -> dict[str, object]:
        copy_menu = menu.addMenu("Copy...")
        copy_file_action = copy_menu.addAction("Copy File")
        copy_action = copy_menu.addAction("Copy To Folder...")
        copy_recent_actions = self._add_recent_destination_actions(copy_menu, "Copy To Recent")

        move_menu = menu.addMenu("Move...")
        move_action = move_menu.addAction("Move To Folder...")
        move_new_folder_action = move_menu.addAction("Move To New Folder...")
        move_recent_actions = self._add_recent_destination_actions(move_menu, "Move To Recent")

        archive_menu = menu.addMenu("Archive...")
        zip_action = archive_menu.addAction("ZIP Archive...")
        seven_zip_action = archive_menu.addAction("7-Zip Archive...")
        tar_gz_action = archive_menu.addAction("TAR.GZ Archive...")
        return {
            "copy_file_action": copy_file_action,
            "copy_action": copy_action,
            "copy_recent_actions": copy_recent_actions,
            "move_action": move_action,
            "move_new_folder_action": move_new_folder_action,
            "move_recent_actions": move_recent_actions,
            "zip_action": zip_action,
            "seven_zip_action": seven_zip_action,
            "tar_gz_action": tar_gz_action,
        }

    def _copy_records_to_clipboard(self, records: list[ImageRecord], *, display_path: str = "") -> None:
        paths: list[str] = []
        seen: set[str] = set()
        candidates = [display_path] if display_path else [record.path for record in records]
        for path in candidates:
            if not path:
                continue
            normalized = normalized_path_key(path)
            if normalized in seen:
                continue
            seen.add(normalized)
            paths.append(path)
        if not paths:
            return

        mime_data = QMimeData()
        mime_data.setUrls([QUrl.fromLocalFile(path) for path in paths])
        mime_data.setText("\n".join(paths))
        QApplication.clipboard().setMimeData(mime_data)
        count = len(paths)
        self.statusBar().showMessage(f"Copied {count} file{'s' if count != 1 else ''} to clipboard")

    def _refresh_favorites_panel(self) -> None:
        if not hasattr(self, "favorites_list"):
            return
        self.favorites_list.clear()
        for path in self._favorites:
            item = QListWidgetItem(Path(path).name or path)
            item.setToolTip(path)
            item.setData(Qt.ItemDataRole.UserRole, path)
            self.favorites_list.addItem(item)
        has_favorites = bool(self._favorites)
        self.favorites_label.setVisible(has_favorites)
        self.favorites_list.setVisible(has_favorites)
        self.favorites_divider.setVisible(has_favorites)
        self._update_favorites_height()

    def _update_favorites_height(self) -> None:
        if not hasattr(self, "favorites_list"):
            return
        count = self.favorites_list.count()
        if count <= 0:
            self.favorites_list.setFixedHeight(0)
            return
        row_height = self.favorites_list.sizeHintForRow(0)
        if row_height <= 0:
            row_height = self.favorites_list.fontMetrics().height() + 12
        frame = self.favorites_list.frameWidth() * 2
        height = frame + (row_height * count)
        self.favorites_list.setFixedHeight(height)

    def _add_favorite(self, folder: str) -> None:
        if not folder or self._dir_confirmed_missing(folder) or folder in self._favorites:
            return
        self._favorites.append(folder)
        self._save_favorites()
        self._refresh_favorites_panel()
        self.statusBar().showMessage(f"Added to favorites: {folder}")

    def _remove_favorite(self, folder: str) -> None:
        if folder not in self._favorites:
            return
        self._favorites = [path for path in self._favorites if path != folder]
        self._save_favorites()
        self._refresh_favorites_panel()
        self.statusBar().showMessage(f"Removed from favorites: {folder}")

    def _folder_tree_filter(self):
        filters = QDir.Filter.AllDirs | QDir.Filter.NoDotAndDotDot | QDir.Filter.Drives
        if self._show_hidden_folders:
            filters |= QDir.Filter.Hidden
        return filters

    def _handle_show_hidden_folders_toggled(self, checked: bool) -> None:
        self._show_hidden_folders = bool(checked)
        self._settings.setValue(self.SHOW_HIDDEN_FOLDERS_KEY, self._show_hidden_folders)
        self.folder_model.setFilter(self._folder_tree_filter())
        if self._current_folder and self._scope_kind == "folder":
            current_path = self._records_view.current_visible_record_path()
            self._folder_records = scan_child_folders(
                self._current_folder,
                include_hidden=self._show_hidden_folders,
            )
            self._apply_records_view(current_path=current_path)
        self._update_action_states()
        state = "shown" if self._show_hidden_folders else "hidden"
        self.statusBar().showMessage(f"Hidden folders {state}")

    def _handle_columns_changed(self) -> None:
        columns = self._normalize_column_count(self.columns_combo.currentData())
        self._set_column_count(columns)

    def _handle_auto_advance_toggled(self, checked: bool) -> None:
        self._auto_advance_enabled = checked
        self._settings.setValue(self.AUTO_ADVANCE_KEY, checked)
        preview = self._preview_if_built()
        if preview is not None:
            preview.set_auto_advance_enabled(checked)
        self._update_action_states()
        mode = "on" if checked else "off"
        self.statusBar().showMessage(f"Auto-advance {mode}")

    def _handle_burst_groups_toggled(self, checked: bool) -> None:
        self._burst_groups_enabled = checked
        self._settings.setValue(self.BURST_GROUPS_KEY, checked)
        self._refresh_burst_group_view()
        self._update_action_states()
        group_count = len(self._visible_burst_groups)
        if checked and group_count:
            self.statusBar().showMessage(f"Smart groups on ({group_count} group(s) in the current view)")
            return
        mode = "on" if checked else "off"
        self.statusBar().showMessage(f"Smart groups {mode}")

    def _handle_burst_stacks_toggled(self, checked: bool) -> None:
        self._burst_stacks_enabled = checked
        self._settings.setValue(self.BURST_STACKS_KEY, checked)
        self._refresh_burst_group_view()
        self._update_action_states()
        group_count = len(self._visible_burst_groups)
        if checked and group_count:
            self.statusBar().showMessage(f"Smart stacks on ({group_count} stack(s) in the current view)")
            return
        mode = "on" if checked else "off"
        self.statusBar().showMessage(f"Smart stacks {mode}")

    def _handle_compare_toggled(self, checked: bool) -> None:
        if not checked and self._winner_ladder_state is not None:
            self._finish_winner_ladder(reopen_preview=False, show_message=False)
        self._compare_enabled = checked
        preview = self._preview_if_built()
        if preview is not None:
            preview.set_compare_mode(checked)
        self._update_action_states()
        mode = "on" if checked else "off"
        self.statusBar().showMessage(f"Compare {mode}")
        if self._preview_is_visible():
            index = self.grid.current_index()
            if index >= 0:
                self._open_preview(index)

    def _handle_auto_bracket_toggled(self, checked: bool) -> None:
        self._auto_bracket_enabled = checked
        self._settings.setValue(self.AUTO_BRACKET_KEY, checked)
        preview = self._preview_if_built()
        if preview is not None:
            preview.set_auto_bracket_mode(checked)
        mode = "on" if checked else "off"
        self.statusBar().showMessage(f"Auto-bracket compare {mode}")
        if self._preview_is_visible() and self._compare_enabled:
            index = self.grid.current_index()
            if index >= 0:
                self._open_preview(index)

    def _handle_preview_auto_bracket_mode_changed(self, enabled: bool) -> None:
        if self._auto_bracket_enabled != enabled:
            self._handle_auto_bracket_toggled(enabled)

    def _handle_preview_compare_mode_changed(self, enabled: bool) -> None:
        if not enabled and self._winner_ladder_state is not None:
            self._finish_winner_ladder(reopen_preview=False, show_message=False)
        if self._compare_enabled != enabled:
            if self.actions is not None:
                self.actions.compare_mode.setChecked(enabled)

    def _handle_preview_compare_count_changed(self, count: int) -> None:
        self._compare_count = count
        self._manual_compare_count = count
        if self.preview.isVisible():
            index = self.grid.current_index()
            if index >= 0:
                self._open_preview(index)

    def _handle_preview_winner_requested(self, path: str) -> None:
        if self._collection_mode:
            return
        index = self._record_index_for_path(path)
        if index is None:
            return
        anchor_path = self.preview.anchor_path() or path
        self._toggle_winner(index, advance_override=False, current_path_override=anchor_path)
        annotation = self._annotations.get(path, SessionAnnotation())
        self.preview.set_annotation_state(path, annotation.winner, annotation.reject, annotation.rating)
        anchor_index = self._record_index_for_path(anchor_path)
        if anchor_index is not None:
            self.grid.set_current_index(anchor_index)

    def _handle_preview_reject_requested(self, path: str) -> None:
        if self._collection_mode:
            return
        index = self._record_index_for_path(path)
        if index is None:
            return
        anchor_path = self.preview.anchor_path() or path
        self._toggle_reject(index, advance_override=False, current_path_override=anchor_path)
        annotation = self._annotations.get(path, SessionAnnotation())
        self.preview.set_annotation_state(path, annotation.winner, annotation.reject, annotation.rating)
        anchor_index = self._record_index_for_path(anchor_path)
        if anchor_index is not None:
            self.grid.set_current_index(anchor_index)

    def _handle_preview_keep_requested(self, path: str) -> None:
        self._dispatch_preview_action(path, self._keep_record)

    def _handle_preview_rename_requested(self, path: str) -> None:
        index = self._record_index_for_path(path)
        if index is None:
            return
        renamed_path = self._record_ops.rename_record_prompt(index)
        if not renamed_path:
            return
        renamed_index = self._record_index_for_path(renamed_path)
        if renamed_index is not None:
            self.grid.set_current_index(renamed_index)
            if self.preview.isVisible():
                self._open_preview(renamed_index)

    def _handle_preview_delete_requested(self, path: str) -> None:
        self._dispatch_preview_action(path, self._delete_record)

    def _handle_preview_move_requested(self, path: str) -> None:
        self._dispatch_preview_action(path, self._move_record_prompt)

    def _handle_preview_tag_requested(self, path: str) -> None:
        self._dispatch_preview_action(path, self._tag_record, preserve_anchor=True)

    def _handle_preview_rating_requested(self, path: str, rating: int) -> None:
        if self._collection_mode:
            return
        index = self._record_index_for_path(path)
        record = self._record_at(index) if index is not None else None
        if record is None:
            return
        annotation = self._annotations.setdefault(record.path, SessionAnnotation())
        previous = self._annotation_snapshot(annotation)
        next_rating = max(0, min(5, int(rating)))
        if previous.rating == next_rating:
            return
        annotation.rating = next_rating
        self._record_ops.push_undo(
            UndoAction(
                kind="annotation",
                primary_path=record.path,
                original_winner=previous.winner,
                original_reject=previous.reject,
                original_photoshop=previous.photoshop,
                rating=previous.rating,
                tags=previous.tags,
                original_review_round=previous.review_round,
                folder=self._current_folder,
                source_paths=self._record_paths(record),
                session_id=self._session_id,
                winner_mode=self._winner_mode.value,
            )
        )
        self._queue_annotation_persist(record, previous_annotation=previous)
        self._aiculler.sync_annotation_to_global_adapter_label(record, annotation)
        self._capture_annotation_feedback(record, previous, annotation, source_mode="rating")
        self._apply_review_count_delta(previous, annotation)
        self._apply_annotation_change_effects(
            [record.path], current_path=self.preview.anchor_path() or path,
            counts_already_updated=True,
        )
        self.preview.set_annotation_state(path, annotation.winner, annotation.reject, annotation.rating)

    def _handle_preview_winner_ladder_choice(self, path: str) -> None:
        state = self._winner_ladder_state
        if state is None:
            return
        challengers = list(state.get("challenger_paths", ()))
        if not challengers:
            self._finish_winner_ladder(reopen_preview=True)
            return
        winner_path = str(state.get("winner_path") or "")
        challenger_path = challengers[0]
        preferred_path = challenger_path if normalized_path_key(path) == normalized_path_key(challenger_path) else winner_path
        self._record_pairwise_preference(
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
            self._finish_winner_ladder(reopen_preview=True)
            return
        self._show_winner_ladder_state()

    def _handle_preview_winner_ladder_skip(self) -> None:
        state = self._winner_ladder_state
        if state is None:
            return
        challengers = list(state.get("challenger_paths", ()))
        if not challengers:
            self._finish_winner_ladder(reopen_preview=True)
            return
        state["challenger_paths"] = challengers[1:]
        if not state["challenger_paths"]:
            self._finish_winner_ladder(reopen_preview=True)
            return
        self._show_winner_ladder_state()

    def _handle_preview_closed(self) -> None:
        # Editor closed — resume background GPU indexing where it left off.
        self._records_view.resume_background_indexing()
        if self._winner_ladder_state is not None:
            self._finish_winner_ladder(reopen_preview=False, show_message=False)
        if self._quick_view_mode:
            self._quick_view_mode = False
            self.close()
            app = QApplication.instance()
            if app is not None:
                app.quit()
            return
        if not self._preview_navigation_dirty:
            return
        self._preview_navigation_dirty = False
        current_index = self.grid.current_index()
        if 0 <= current_index < len(self._records):
            self.grid.set_current_index(current_index)

    def _winner_ladder_candidate_rows(self, index: int) -> tuple[list[tuple[int, ImageRecord]], str, str]:
        record = self._record_at(index)
        if record is None:
            return [], "", ""
        burst_recommendation = self._burst_recommendation_for_record(record)
        if burst_recommendation is not None and burst_recommendation.group_size > 1:
            rows = [
                (row_index, self._records[row_index])
                for row_index in self._visible_review_group_rows_by_id.get(burst_recommendation.group_id, ())
                if 0 <= row_index < len(self._records)
            ]
            rows.sort(
                key=lambda item: (
                    self._burst_recommendation_for_record(item[1]).rank_in_group
                    if self._burst_recommendation_for_record(item[1]) is not None
                    else 99,
                    item[1].name.casefold(),
                )
            )
            if len(rows) >= 2:
                return rows, "burst", burst_recommendation.group_id
        current_ai = self._ai_run.ai_result_for_index(index)
        if current_ai is not None and current_ai.group_size > 1:
            rows = [(row_index, record) for row_index, record, _result in self._ai_run.visible_ai_group_rows(current_ai.group_id)]
            if len(rows) >= 2:
                return rows, "ai", current_ai.group_id
        selected_indexes = [item_index for item_index in self.grid.selected_indexes() if 0 <= item_index < len(self._records)]
        if len(selected_indexes) >= 2:
            rows = [(item_index, self._records[item_index]) for item_index in selected_indexes]
            rows.sort(key=lambda item: (item[0] != index, item[0]))
            return rows, "selection", ""
        return [], "", ""

    def _winner_ladder_candidate_count(self, index: int) -> int:
        rows, _source_mode, _group_id = self._winner_ladder_candidate_rows(index)
        return len(rows)

    def _preview_entry_for_visible_path(self, path: str, *, label: str = "") -> PreviewEntry | None:
        index = self._record_index_for_path(path)
        if index is None:
            return None
        record = self._record_at(index)
        if record is None:
            return None
        annotation = self._annotations.get(record.path, SessionAnnotation())
        displayed_path = self._displayed_preview_source_path(index, record)
        edited_candidates = self._ordered_edited_candidates(record, displayed_path)
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
            ai_result=self._ai_run.ai_result_for_record(record, preferred_path=displayed_path),
            review_summary=self._review_summary_for_record(record),
            workflow_summary=self._workflow_summary_for_record(record),
            workflow_details=self._workflow_detail_lines_for_record(record),
            placeholder_image=self._preview_placeholder_for_index(index),
        )

    def _start_winner_ladder(self, index: int) -> None:
        rows, source_mode, group_id = self._winner_ladder_candidate_rows(index)
        if len(rows) < 2:
            self.statusBar().showMessage("Winner Ladder needs a visible burst, AI group, or multi-selection.")
            return
        current_record = self._record_at(index)
        burst_recommendation = self._burst_recommendation_for_record(current_record)
        current_ai = self._ai_run.ai_result_for_index(index)
        winner_path = current_record.path if current_record is not None else rows[0][1].path
        if source_mode == "burst" and burst_recommendation is not None and burst_recommendation.recommended_path:
            winner_path = burst_recommendation.recommended_path
        elif source_mode == "ai" and current_ai is not None and self._ai_bundle is not None:
            group_results = self._ai_bundle.group_results(current_ai.group_id)
            if group_results:
                winner_path = group_results[0].file_path
        ordered_paths = [record.path for _row_index, record in rows]
        challengers = [path for path in ordered_paths if normalized_path_key(path) != normalized_path_key(winner_path)]
        if not challengers:
            self.statusBar().showMessage("Winner Ladder could not find a challenger for the current winner.")
            return
        self._winner_ladder_state = {
            "winner_path": winner_path,
            "challenger_paths": challengers,
            "group_id": group_id,
            "source_mode": source_mode,
            "previous_compare_enabled": self._compare_enabled,
        }
        self._show_winner_ladder_state()

    def _show_winner_ladder_state(self) -> None:
        state = self._winner_ladder_state
        if state is None:
            return
        challengers = list(state.get("challenger_paths", ()))
        if not challengers:
            self._finish_winner_ladder(reopen_preview=True)
            return
        winner_path = str(state.get("winner_path") or "")
        challenger_path = challengers[0]
        winner_entry = self._preview_entry_for_visible_path(winner_path, label="Current Winner")
        challenger_entry = self._preview_entry_for_visible_path(challenger_path, label="Challenger")
        if winner_entry is None or challenger_entry is None:
            self._finish_winner_ladder(reopen_preview=False)
            return
        self._compare_enabled = True
        if self.actions is not None:
            with QSignalBlocker(self.actions.compare_mode):
                self.actions.compare_mode.setChecked(True)
            self._toolbar.sync_topbar_action_buttons()
        challenger_index = self._record_index_for_path(challenger_path)
        if challenger_index is not None:
            self.grid.set_current_index(challenger_index)
        self.preview.set_compare_mode(True)
        self.preview.set_winner_ladder_mode(True)
        self.preview.set_compare_count(2)
        self.preview.show_entries([winner_entry, challenger_entry])
        self.preview._set_focused_slot(1)
        self.statusBar().showMessage(
            f"Winner Ladder: {Path(winner_path).name} vs {Path(challenger_path).name}"
        )

    def _finish_winner_ladder(self, *, reopen_preview: bool, show_message: bool = True) -> None:
        state = self._winner_ladder_state
        if state is None:
            return
        winner_path = str(state.get("winner_path") or "")
        previous_compare_enabled = bool(state.get("previous_compare_enabled"))
        self._winner_ladder_state = None
        self.preview.set_winner_ladder_mode(False)
        self._compare_enabled = previous_compare_enabled
        if self.actions is not None:
            with QSignalBlocker(self.actions.compare_mode):
                self.actions.compare_mode.setChecked(previous_compare_enabled)
            self._toolbar.sync_topbar_action_buttons()
        self.preview.set_compare_mode(previous_compare_enabled)
        winner_index = self._record_index_for_path(winner_path)
        if winner_index is not None:
            self.grid.set_current_index(winner_index)
            if reopen_preview and self.preview.isVisible():
                self._open_preview(winner_index)
        if show_message and winner_path:
            self.statusBar().showMessage(f"Winner Ladder complete: {Path(winner_path).name}")

    def _refresh_folder(self) -> None:
        if self._current_folder:
            self._load_folder(
                self._current_folder,
                force_refresh=True,
                preferred_record_path=self._records_view.current_visible_record_path(),
            )

    def _rebuild_current_folder_catalog_cache(self) -> None:
        self._catalog.rebuild_current_folder_catalog_cache()

    def _load_cached_folder_records(self, folder: str) -> tuple[list[ImageRecord] | None, str]:
        return self._catalog.load_cached_folder_records(folder)

    def _persist_folder_record_cache(self, folder: str, records: list[ImageRecord], *, source: str = "window") -> None:
        self._catalog.persist_folder_record_cache(folder, records, source=source)

    def _refresh_current_folder_watch(self) -> None:
        target = ""
        if (
            self._watch_current_folder_enabled
            and self._scope_kind == "folder"
            and self._current_folder
            and not self._is_slow_source_folder(self._current_folder)
        ):
            candidate = self._current_folder
            if os.path.isdir(candidate):
                target = candidate

        if self._watched_folder_path == target:
            return

        existing_paths = list(self._folder_watcher.directories())
        if existing_paths:
            self._folder_watcher.removePaths(existing_paths)
        self._folder_watch_refresh_timer.stop()
        self._folder_watch_refresh_pending = False
        self._watched_folder_path = ""
        if not target:
            return
        try:
            if self._folder_watcher.addPath(target):
                self._watched_folder_path = target
        except RuntimeError:
            self._watched_folder_path = ""

    def _queue_watched_folder_refresh(self, delay_ms: int = 900) -> None:
        if not self._watch_current_folder_enabled or self._scope_kind != "folder" or not self._current_folder:
            return
        self._folder_watch_refresh_pending = True
        self._folder_watch_refresh_timer.start(max(0, delay_ms))

    def _handle_watched_folder_changed(self, path: str) -> None:
        if not self._watch_current_folder_enabled or self._scope_kind != "folder" or not self._current_folder:
            return
        if _memory_path_key(path) != _memory_path_key(self._current_folder):
            return
        self._queue_watched_folder_refresh()
        if self._scan_in_progress:
            self.statusBar().showMessage(f"Detected folder changes in {self._current_folder}; refresh queued.")
            return
        self.statusBar().showMessage(f"Detected folder changes in {self._current_folder}; refreshing...")

    def _handle_application_state_changed(self, state: Qt.ApplicationState) -> None:
        if state == Qt.ApplicationState.ApplicationActive:
            self._check_folder_changed_on_activation()

    def _check_folder_changed_on_activation(self) -> None:
        """Network and removable drives are not watched, so look once when the user comes back to
        the app (typically after importing or exporting elsewhere).

        The folder's modified time is read on a worker thread (a stat on an unreachable share can
        block) and compared with the one its on-screen listing was taken at; a difference queues
        the same refresh a local watcher change would."""
        if (
            not self._watch_current_folder_enabled
            or self._scope_kind != "folder"
            or not self._current_folder
            or self._scan_in_progress
            or self._folder_dir_mtime_ns is None
            or not self._is_slow_source_folder(self._current_folder)
        ):
            return
        if self._folder_check_task is not None and self._folder_check_token == self._scan_token:
            return
        now = time.monotonic()
        if now - self._folder_check_last_started < 5.0:
            return  # focus flaps (dialogs opening and closing) should not hammer the share
        self._folder_check_last_started = now
        task = FolderModifiedCheckTask(self._current_folder, self._scan_token)
        task.signals.checked.connect(self._handle_folder_modified_checked, Qt.ConnectionType.QueuedConnection)
        self._folder_check_task = task
        self._folder_check_token = self._scan_token
        QThreadPool.globalInstance().start(task)

    def _handle_folder_modified_checked(self, folder: str, token: int, modified_ns: object) -> None:
        if token == self._folder_check_token:
            self._folder_check_task = None
        if token != self._scan_token or self._scan_in_progress or not isinstance(modified_ns, int):
            return
        if _memory_path_key(folder) != _memory_path_key(self._current_folder):
            return
        if modified_ns == self._folder_dir_mtime_ns:
            return
        self._queue_watched_folder_refresh()
        self.statusBar().showMessage(f"Detected folder changes in {self._current_folder}; refreshing...")

    def _run_watched_folder_refresh(self) -> None:
        if not self._folder_watch_refresh_pending:
            return
        if not self._watch_current_folder_enabled or self._scope_kind != "folder" or not self._current_folder:
            self._folder_watch_refresh_pending = False
            return
        if self._scan_in_progress:
            self._folder_watch_refresh_timer.start(450)
            return
        if self._dir_confirmed_missing(self._current_folder):
            self._folder_watch_refresh_pending = False
            self._refresh_current_folder_watch()
            return
        self._folder_watch_refresh_pending = False
        self.statusBar().showMessage(f"Refreshing changed folder: {self._current_folder}")
        self._load_folder(
            self._current_folder,
            force_refresh=True,
            preferred_record_path=self._records_view.current_visible_record_path(),
        )

    def _load_folder(
        self,
        folder: str,
        *,
        force_refresh: bool = False,
        chunked_restore: bool = False,
        bypass_catalog_cache: bool = False,
        preferred_record_path: str | None = None,
    ) -> None:
        self._records_view.load_folder(
            folder,
            force_refresh=force_refresh,
            chunked_restore=chunked_restore,
            bypass_catalog_cache=bypass_catalog_cache,
            preferred_record_path=preferred_record_path,
        )

    def _run_loaded_records_enrichment(self) -> None:
        self._records_view.run_loaded_records_enrichment()

    def _cancel_scope_enrichment_task(self) -> None:
        self._scope_enrichment_debounce_timer.stop()
        task = self._active_scope_enrichment_task
        if task is None:
            return
        task.cancel()
        self._active_scope_enrichment_task = None

    def _schedule_scope_enrichment_refresh(self) -> None:
        if not self._all_records:
            return
        self._scope_enrichment_debounce_timer.start()

    def _run_scope_enrichment_debounced(self) -> None:
        self._start_scope_enrichment_task()

    def _start_scope_enrichment_task(self, records: list[ImageRecord] | None = None) -> None:
        active_records = list(records) if records is not None else list(self._all_records)
        if not active_records:
            self._cancel_scope_enrichment_task()
            self._correction_events = []
            self._taste_profile = TasteProfile()
            self._burst_recommendations = {}
            self._workflow_insights_by_path = {}
            return
        if self._active_ai_task is not None:
            self._ai_run.mark_background_review_work_deferred_for_ai(reason="scope_enrichment")
            self._review_scoring_cache_source = "deferred"
            self._review_scoring_cache_detail = "Workflow scoring is deferred while AI review runs."
            self._refresh_catalog_status_indicator()
            return

        self._cancel_scope_enrichment_task()
        self._scope_enrichment_token += 1
        token = self._scope_enrichment_token
        scope_key = self._current_scope_key()
        task = ScopeEnrichmentTask(
            scope_key=scope_key,
            token=token,
            session_id=self._session_id,
            folder_path=self._current_folder,
            catalog_db_path=self._catalog_repository.db_path,
            include_all_scope_events=(not self._current_folder and self._scope_kind != "folder"),
            records=tuple(active_records),
            ai_bundle=self._ai_bundle,
            review_bundle=self._review_intelligence,
        )
        self._review_scoring_cache_source = "building"
        self._review_scoring_cache_detail = f"Building workflow scoring for {len(active_records)} image bundle(s)..."
        self._refresh_catalog_status_indicator()
        task.signals.cache_status.connect(self._handle_scope_enrichment_cache_status, Qt.ConnectionType.QueuedConnection)
        task.signals.finished.connect(self._handle_scope_enrichment_finished, Qt.ConnectionType.QueuedConnection)
        task.signals.failed.connect(self._handle_scope_enrichment_failed, Qt.ConnectionType.QueuedConnection)
        self._active_scope_enrichment_task = task
        self._scope_enrichment_pool.start(task)

    def _handle_scope_enrichment_cache_status(self, scope_key: str, token: int, payload: object) -> None:
        if token != self._scope_enrichment_token or scope_key != self._current_scope_key():
            return
        if not isinstance(payload, dict):
            return
        source = str(payload.get("source") or "idle")
        record_count = int(payload.get("record_count") or 0)
        self._review_scoring_cache_source = source
        if source == "catalog":
            self._review_scoring_cache_detail = "Loaded workflow scoring from the catalog cache."
        elif source == "live":
            self._review_scoring_cache_detail = f"Built workflow scoring live for {record_count} image bundle(s)."
        elif source == "failed":
            self._review_scoring_cache_detail = "Workflow scoring failed."
        else:
            self._review_scoring_cache_detail = "Workflow scoring is idle."
        self._refresh_catalog_status_indicator()

    def _handle_scope_enrichment_finished(
        self,
        scope_key: str,
        token: int,
        correction_events: object,
        taste_profile: object,
        recommendations: object,
    ) -> None:
        if token != self._scope_enrichment_token or scope_key != self._current_scope_key():
            return
        self._active_scope_enrichment_task = None
        self._correction_events = list(correction_events) if isinstance(correction_events, list) else []
        self._taste_profile = taste_profile if isinstance(taste_profile, TasteProfile) else TasteProfile()
        if isinstance(recommendations, dict):
            self._burst_recommendations = {str(path): value for path, value in recommendations.items() if isinstance(path, str)}
        else:
            self._burst_recommendations = {}
        self._refresh_workflow_insights_cache(force_full=True)
        current_path = self._records_view.current_visible_record_path()
        self._apply_records_view(current_path=current_path)

    def _handle_scope_enrichment_failed(self, scope_key: str, token: int, message: str) -> None:
        if token != self._scope_enrichment_token or scope_key != self._current_scope_key():
            return
        self._active_scope_enrichment_task = None
        self._review_scoring_cache_source = "failed"
        self._review_scoring_cache_detail = message
        self._refresh_catalog_status_indicator()
        self.statusBar().showMessage(f"Workflow enrichment fallback active: {message}")

    def _start_annotation_hydration(self, records: list[ImageRecord]) -> None:
        if not records:
            self._annotation_hydration_token += 1
            if self._active_annotation_hydration_task is not None:
                self._active_annotation_hydration_task.cancel()
            self._active_annotation_hydration_task = None
            self._annotation_hydration_dirty_paths.clear()
            self._annotation_hydration_pending_clear_paths.clear()
            self._annotation_reapply_timer.stop()
            return
        if self._active_ai_task is not None:
            self._ai_run.mark_background_review_work_deferred_for_ai(reason="annotation_hydration")
            return
        self._annotation_hydration_token += 1
        token = self._annotation_hydration_token
        previous_task = self._active_annotation_hydration_task
        if previous_task is not None:
            previous_task.cancel()
        self._active_annotation_hydration_task = None
        self._annotation_hydration_dirty_paths.clear()
        self._annotation_hydration_pending_clear_paths = {record.path for record in records if record.path in self._annotations}
        self._annotation_reapply_timer.stop()
        scope_key = self._current_scope_key()
        task = AnnotationHydrationTask(
            scope_key=scope_key,
            token=token,
            session_id=self._session_id,
            records=tuple(records),
            prioritized_paths=tuple(self.grid.visible_item_paths(limit=240)),
        )
        task.signals.chunk.connect(self._handle_annotation_hydration_chunk, Qt.ConnectionType.QueuedConnection)
        task.signals.finished.connect(self._handle_annotation_hydration_finished, Qt.ConnectionType.QueuedConnection)
        task.signals.failed.connect(self._handle_annotation_hydration_failed, Qt.ConnectionType.QueuedConnection)
        self._active_annotation_hydration_task = task
        self._annotation_hydration_pool.start(task)

    def _handle_annotation_hydration_chunk(self, scope_key: str, token: int, chunk: dict[str, SessionAnnotation]) -> None:
        if token != self._annotation_hydration_token or scope_key != self._current_scope_key():
            return
        if not chunk:
            return
        changed_paths: list[str] = []
        for path, annotation in chunk.items():
            self._annotation_hydration_pending_clear_paths.discard(path)
            previous = self._annotations.get(path)
            if previous == annotation:
                continue
            self._annotations[path] = annotation
            changed_paths.append(path)
        if not changed_paths:
            return
        self._records_view_cache.mark(ViewInvalidationReason.ANNOTATION_CHANGED, paths=changed_paths)
        self._annotation_hydration_dirty_paths.update(changed_paths)
        self._annotation_reapply_timer.start()

    def _flush_annotation_hydration_updates(self) -> None:
        if not self._annotation_hydration_dirty_paths:
            return
        changed_paths = sorted(self._annotation_hydration_dirty_paths)
        self._annotation_hydration_dirty_paths.clear()
        current_path = self._records_view.current_visible_record_path()
        self._apply_annotation_change_effects(changed_paths, current_path=current_path)

    def _handle_annotation_hydration_finished(self, scope_key: str, token: int) -> None:
        if token != self._annotation_hydration_token or scope_key != self._current_scope_key():
            return
        self._active_annotation_hydration_task = None
        if self._annotation_hydration_pending_clear_paths:
            stale_paths = sorted(self._annotation_hydration_pending_clear_paths)
            self._annotation_hydration_pending_clear_paths.clear()
            for path in stale_paths:
                self._annotations.pop(path, None)
            self._annotation_hydration_dirty_paths.update(stale_paths)
        self._flush_annotation_hydration_updates()

    def _handle_annotation_hydration_failed(self, scope_key: str, token: int, message: str) -> None:
        if token != self._annotation_hydration_token or scope_key != self._current_scope_key():
            return
        self._active_annotation_hydration_task = None
        self._annotation_hydration_dirty_paths.clear()
        self._annotation_hydration_pending_clear_paths.clear()
        self.statusBar().showMessage(f"Loaded folder, but annotation hydration failed: {message}")

    def _start_review_intelligence_analysis(self, *, force: bool = False) -> None:
        if not self._all_records:
            self._review_intelligence = None
            self._review_chunk_flush_timer.stop()
            self._review_chunk_dirty_paths.clear()
            self._review_grouping_cache_source = "idle"
            self._review_grouping_cache_detail = "No records loaded."
            self._review_feature_cache_source = "idle"
            self._review_feature_cache_detail = "No review features loaded."
            self._refresh_catalog_status_indicator()
            return
        if self._active_ai_task is not None:
            self._ai_run.mark_background_review_work_deferred_for_ai(reason="review_intelligence")
            self._review_grouping_cache_source = "deferred"
            self._review_grouping_cache_detail = "Smart groups are deferred while AI review runs."
            self._review_feature_cache_source = "deferred"
            self._review_feature_cache_detail = "Review feature analysis is deferred while AI review runs."
            self._refresh_catalog_status_indicator()
            return
        if not force and len(self._all_records) > self.AUTO_REVIEW_INTELLIGENCE_MAX_RECORDS:
            self._review_intelligence = None
            self._review_chunk_flush_timer.stop()
            self._review_chunk_dirty_paths.clear()
            self._review_grouping_cache_source = "skipped"
            self._review_grouping_cache_detail = "Smart groups are deferred until requested."
            self._review_feature_cache_source = "skipped"
            self._review_feature_cache_detail = "Review feature analysis is deferred with smart groups."
            self._refresh_catalog_status_indicator()
            self.statusBar().showMessage(
                f"Loaded {len(self._all_records)} image bundle(s). Smart groups are deferred until requested."
            )
            return
        previous_task = self._active_review_intelligence_task
        if previous_task is not None:
            previous_task.cancel()
        self._review_chunk_flush_timer.stop()
        self._review_chunk_dirty_paths.clear()
        self._review_intelligence_token += 1
        token = self._review_intelligence_token
        scope_key = self._current_scope_key()
        task = BuildReviewIntelligenceTask(
            folder=scope_key,
            token=token,
            records=tuple(self._all_records),
            folder_path=self._current_folder,
            catalog_db_path=self._catalog_repository.db_path,
        )
        self._review_grouping_cache_source = "building"
        self._review_grouping_cache_detail = f"Building smart groups for {len(self._all_records)} image bundle(s)..."
        self._review_feature_cache_source = "building"
        self._review_feature_cache_detail = "Preparing review feature analysis..."
        self._refresh_catalog_status_indicator()
        task.signals.started.connect(self._handle_review_intelligence_started, Qt.ConnectionType.QueuedConnection)
        task.signals.progress.connect(self._handle_review_intelligence_progress, Qt.ConnectionType.QueuedConnection)
        task.signals.chunk.connect(self._handle_review_intelligence_chunk, Qt.ConnectionType.QueuedConnection)
        task.signals.cache_status.connect(self._handle_review_intelligence_cache_status, Qt.ConnectionType.QueuedConnection)
        task.signals.cancelled.connect(self._handle_review_intelligence_cancelled, Qt.ConnectionType.QueuedConnection)
        task.signals.finished.connect(self._handle_review_intelligence_finished, Qt.ConnectionType.QueuedConnection)
        task.signals.failed.connect(self._handle_review_intelligence_failed, Qt.ConnectionType.QueuedConnection)
        self._active_review_intelligence_task = task
        self._review_intelligence_pool.start(task)

    def _handle_review_intelligence_started(self, folder: str, token: int, total: int) -> None:
        if token != self._review_intelligence_token or folder != self._current_scope_key():
            return
        if total > 0:
            self.statusBar().showMessage(f"Building smart groups for {total} image bundle(s)...")

    def _handle_review_intelligence_progress(self, folder: str, token: int, current: int, total: int) -> None:
        if token != self._review_intelligence_token or folder != self._current_scope_key():
            return
        if total <= 0:
            return
        if current in {0, 1, total} or current % 80 == 0:
            self.statusBar().showMessage(f"Building smart groups ({current}/{total})...")

    def _handle_review_intelligence_cache_status(self, folder: str, token: int, payload: object) -> None:
        if token != self._review_intelligence_token or folder != self._current_scope_key():
            return
        if not isinstance(payload, dict):
            return
        grouping_source = str(payload.get("grouping_source") or "idle")
        feature_source = str(payload.get("feature_source") or "idle")
        total_records = int(payload.get("total_records") or 0)
        cached_feature_count = int(payload.get("cached_feature_count") or 0)
        computed_feature_count = int(payload.get("computed_feature_count") or 0)

        self._review_grouping_cache_source = grouping_source
        if grouping_source == "catalog":
            self._review_grouping_cache_detail = "Loaded smart groups from the catalog cache."
        elif grouping_source == "live":
            self._review_grouping_cache_detail = f"Built smart groups live for {total_records} image bundle(s)."
        elif grouping_source == "failed":
            self._review_grouping_cache_detail = "Smart grouping failed."
        else:
            self._review_grouping_cache_detail = "Smart grouping is idle."

        self._review_feature_cache_source = feature_source
        if feature_source == "catalog":
            self._review_feature_cache_detail = f"Reused cached review features for all {cached_feature_count} image bundle(s)."
        elif feature_source == "mixed":
            self._review_feature_cache_detail = (
                f"Reused cached review features for {cached_feature_count}/{total_records} bundle(s) "
                f"and computed {computed_feature_count} live."
            )
        elif feature_source == "live":
            self._review_feature_cache_detail = f"Computed review features live for {computed_feature_count or total_records} image bundle(s)."
        elif feature_source == "skipped":
            self._review_feature_cache_detail = "Review feature analysis was skipped because grouped results came from cache."
        elif feature_source == "failed":
            self._review_feature_cache_detail = "Review feature analysis failed."
        else:
            self._review_feature_cache_detail = "Review feature analysis is idle."
        self._refresh_catalog_status_indicator()

    def _handle_review_intelligence_chunk(self, folder: str, token: int, payload: object) -> None:
        if token != self._review_intelligence_token or folder != self._current_scope_key():
            return
        if not isinstance(payload, dict):
            return
        groups_payload = payload.get("groups")
        insights_payload = payload.get("insights")
        groups = tuple(group for group in groups_payload if hasattr(group, "id")) if isinstance(groups_payload, (list, tuple)) else ()
        if not isinstance(insights_payload, dict):
            return
        if self._review_intelligence is None:
            merged_groups: dict[str, object] = {}
            merged_insights: dict[str, object] = {}
        else:
            merged_groups = {group.id: group for group in self._review_intelligence.groups}
            merged_insights = dict(self._review_intelligence.insights_by_path)
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
                if isinstance(path, str) and path in self._record_index_by_path
            )
        self._review_intelligence = ReviewIntelligenceBundle(
            groups=tuple(merged_groups.values()),
            insights_by_path=merged_insights,
        )
        self._review_chunk_dirty_paths.update(path for path in changed_paths if path)
        self._review_chunk_flush_timer.start()

    def _flush_review_chunk_updates(self) -> None:
        if not self._review_chunk_dirty_paths:
            return
        changed_paths = sorted(self._review_chunk_dirty_paths)
        self._review_chunk_dirty_paths.clear()
        current_path = self._records_view.current_visible_record_path()
        if self._filter_query.quick_filter in {FilterMode.SMART_GROUPS, FilterMode.DUPLICATES}:
            self._records_view_cache.mark(ViewInvalidationReason.REVIEW_CHANGED, paths=changed_paths)
            self._apply_records_view(current_path=current_path)
            return
        changed_visible_paths = tuple(path for path in changed_paths if path in self._record_index_by_path)
        self.grid.set_review_insights(self._review_intelligence.insights_by_path if self._review_intelligence is not None else {})
        if changed_visible_paths:
            self.grid.update_items(
                GridDeltaUpdate(
                    changed_paths=changed_visible_paths,
                    selection_anchor=self.grid.current_index(),
                    preserve_pixmap_cache=True,
                )
            )
        self._rebuild_visible_preview_group_indexes()
        self._refresh_burst_group_view()
        if current_path:
            index = self._record_index_by_path.get(current_path)
            if index is not None:
                if index != self.grid.current_index():
                    self.grid.set_current_index(index)
        self._records_view.update_filter_summary()
        self._update_action_states()
        self._update_status()

    def _handle_review_intelligence_cancelled(self, folder: str, token: int) -> None:
        if token != self._review_intelligence_token or folder != self._current_scope_key():
            return
        self._active_review_intelligence_task = None
        self._review_chunk_flush_timer.stop()
        self._review_chunk_dirty_paths.clear()
        self._review_grouping_cache_source = "idle"
        self._review_grouping_cache_detail = "Smart grouping cancelled."
        self._review_feature_cache_source = "idle"
        self._review_feature_cache_detail = "Review feature analysis cancelled."
        self._refresh_catalog_status_indicator()

    def _handle_review_intelligence_finished(self, folder: str, token: int, bundle: ReviewIntelligenceBundle) -> None:
        if token != self._review_intelligence_token or folder != self._current_scope_key():
            return
        self._active_review_intelligence_task = None
        self._review_chunk_flush_timer.stop()
        self._review_chunk_dirty_paths.clear()
        self._review_intelligence = bundle
        self._ai_run.recompute_ai_demoted_burst_paths()
        current_path = self._records_view.current_visible_record_path()
        self._records_view_cache.mark(ViewInvalidationReason.REVIEW_CHANGED)
        self._apply_records_view(current_path=current_path)
        self._start_scope_enrichment_task()
        if self._preview_is_visible():
            index = self.grid.current_index()
            if index >= 0:
                self._open_preview(index)

    def _handle_review_intelligence_failed(self, folder: str, token: int, message: str) -> None:
        if token != self._review_intelligence_token or folder != self._current_scope_key():
            return
        self._active_review_intelligence_task = None
        self._review_chunk_flush_timer.stop()
        self._review_chunk_dirty_paths.clear()
        self._review_intelligence = None
        self._review_grouping_cache_source = "failed"
        self._review_grouping_cache_detail = message
        self._review_feature_cache_source = "failed"
        self._review_feature_cache_detail = message
        self._refresh_catalog_status_indicator()
        self.statusBar().showMessage(f"Smart grouping fallback active: {message}")

    def _handle_filter_metadata_ready(self, key, metadata) -> None:
        self._records_view.handle_filter_metadata_ready(key, metadata)

    def _metadata_prefetch_seed_paths(self, *, lookahead: int = 120) -> list[str]:
        return self._records_view.metadata_prefetch_seed_paths(lookahead=lookahead)

    def _enqueue_filter_metadata_paths(
        self,
        paths: list[str] | tuple[str, ...] | set[str],
        *,
        front: bool = False,
    ) -> None:
        self._records_view.enqueue_filter_metadata_paths(paths, front=front)

    def _catalog_cache_reads_enabled(self) -> bool:
        override = catalog_cache_env_override()
        return self._catalog_cache_enabled if override is None else override

    @staticmethod
    def _cache_source_label(source: str, *, live_label: str = "Live") -> str:
        if source == "catalog":
            return "Catalog Cache"
        if source == "live":
            return live_label
        if source == "mixed":
            return "Mixed"
        if source == "building":
            return "Building"
        if source == "deferred":
            return "Deferred"
        if source == "skipped":
            return "Skipped"
        if source == "scanning":
            return "Scanning"
        if source == "failed":
            return "Failed"
        return "Idle"

    @staticmethod
    def _catalog_source_label(source: str) -> str:
        return MainWindow._cache_source_label(source, live_label="Live Scan")

    def _catalog_status_badge_text(self) -> str:
        if self._catalog_load_source == "live" and self._scan_cached_source:
            return f"Load: {self._catalog_source_label(self._scan_cached_source)} + Live"
        return f"Load: {self._catalog_source_label(self._catalog_load_source)}"

    def _cache_pipeline_badge_text(self) -> str:
        review_label = self._cache_source_label(self._review_grouping_cache_source)
        scoring_label = self._cache_source_label(self._review_scoring_cache_source)
        return f"Review: {review_label} | Workflow: {scoring_label}"

    def _review_cache_summary_lines(self) -> list[str]:
        lines = [
            f"Review groups: {self._cache_source_label(self._review_grouping_cache_source)}",
        ]
        if self._review_grouping_cache_detail:
            lines.append(self._review_grouping_cache_detail)
        lines.append(f"Review features: {self._cache_source_label(self._review_feature_cache_source)}")
        if self._review_feature_cache_detail:
            lines.append(self._review_feature_cache_detail)
        lines.append(f"Workflow scoring: {self._cache_source_label(self._review_scoring_cache_source)}")
        if self._review_scoring_cache_detail:
            lines.append(self._review_scoring_cache_detail)
        return lines

    def _catalog_debug_summary(self, *, include_current: bool = False) -> str:
        stats = self._catalog_repository.stats()
        enabled_label = "Enabled" if self._catalog_cache_reads_enabled() else "Disabled"
        override = catalog_cache_env_override()
        lines = [f"Catalog cache reads: {enabled_label}"]
        if override is not None:
            lines[-1] = f"{lines[-1]} (environment override)"
        lines.append(f"Folder watch: {'Enabled' if self._watch_current_folder_enabled else 'Disabled'}")
        if include_current:
            lines.append(f"Current load: {self._catalog_source_label(self._catalog_load_source)}")
            if self._catalog_load_detail:
                lines.append(self._catalog_load_detail)
            lines.extend(self._review_cache_summary_lines())
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

    def _refresh_catalog_status_indicator(self) -> None:
        if not hasattr(self, "catalog_status_label"):
            return
        self.catalog_status_label.setText(self._catalog_status_badge_text())
        summary_text = self._catalog_debug_summary(include_current=True)
        self.catalog_status_label.setToolTip(summary_text)
        if hasattr(self, "cache_pipeline_label"):
            self.cache_pipeline_label.setText(self._cache_pipeline_badge_text())
            self.cache_pipeline_label.setToolTip(summary_text)

    def _refresh_winner_scores_for_current_folder(self) -> bool:
        try:
            paths = self._aiculler.aiculler_paths_for_current_folder()
        except Exception:
            _logger.exception("Failed to resolve aiculler paths for winner scores")
            paths = None
        if paths is None:
            self._winner_scores_by_path = {}
            return False
        db_path = aiculler_db_path(paths)
        try:
            bundle = load_latest_winner_scores(
                db_path,
                model_version=WINNER_SCORE_FALLBACK_MODEL_VERSION,
            )
        except Exception:
            _logger.exception("Failed to load winner scores from %s", db_path)
            self._winner_scores_by_path = {}
            return False
        self._winner_scores_by_path = dict(bundle.get("scores_by_path") or {})
        return bool(self._winner_scores_by_path)

    def _winner_score_for_record(self, record: ImageRecord) -> dict[str, object] | None:
        if not self._winner_scores_by_path:
            return None
        for path in record.stack_paths:
            key = os.path.normcase(os.path.normpath(str(path)))
            score = self._winner_scores_by_path.get(key)
            if score is not None:
                return score
        return None

    def _refresh_face_records_for_current_folder(self) -> bool:
        try:
            paths = self._aiculler.aiculler_paths_for_current_folder()
        except Exception:
            _logger.exception("Failed to resolve aiculler paths for face records")
            paths = None
        if paths is None:
            self._face_records_by_path = {}
            self._face_records_db_path = ""
            return False
        db_path = aiculler_db_path(paths)
        try:
            self._face_records_by_path = load_face_records_by_path(db_path)
        except Exception:
            _logger.exception("Failed to load face records from %s", db_path)
            self._face_records_by_path = {}
        self._face_records_db_path = str(db_path)
        return bool(self._face_records_by_path)

    def _face_bundle_for_record(self, record: ImageRecord | None) -> dict[str, object]:
        if record is None:
            return {}
        try:
            paths = self._aiculler.aiculler_paths_for_current_folder()
            db_path = str(aiculler_db_path(paths))
        except Exception:
            _logger.exception("Failed to resolve aiculler db path for face bundle lookup")
            db_path = ""
        if db_path and db_path != self._face_records_db_path:
            self._refresh_face_records_for_current_folder()
        if not self._face_records_by_path:
            return {}
        for path in record.stack_paths:
            key = os.path.normcase(os.path.normpath(str(path)))
            bundle = self._face_records_by_path.get(key)
            if bundle:
                return bundle
        return {}

    def _face_records_for_record(self, record: ImageRecord | None) -> tuple[object, ...]:
        bundle = self._face_bundle_for_record(record)
        return tuple(bundle.get("faces") or ())

    def _cycle_inspector_face_preview(self) -> None:
        index = self.grid.current_index()
        record = self._record_at(index)
        faces = self._face_records_for_record(record)
        if record is None or len(faces) <= 1:
            return
        key = normalized_path_key(record.path)
        current = int(self._face_cycle_index_by_path.get(key, 0) or 0)
        self._face_cycle_index_by_path[key] = (current + 1) % len(faces)
        self._update_inspector_context(index)

    def _refresh_image_categories_for_current_folder(self) -> bool:
        try:
            paths = self._aiculler.aiculler_paths_for_current_folder()
        except Exception:
            _logger.exception("Failed to resolve aiculler paths for image categories")
            paths = None
        if paths is None:
            self._image_categories_by_path = {}
            self._image_categories_db_path = ""
            return False
        db_path = aiculler_db_path(paths)
        try:
            self._image_categories_by_path = load_image_categories_by_path(db_path)
        except Exception:
            _logger.exception("Failed to load image categories from %s", db_path)
            self._image_categories_by_path = {}
        self._image_categories_db_path = str(db_path)
        return bool(self._image_categories_by_path)

    def _category_info_for_record(self, record: ImageRecord | None) -> dict[str, object]:
        if record is None:
            return {}
        try:
            paths = self._aiculler.aiculler_paths_for_current_folder()
            db_path = str(aiculler_db_path(paths))
        except Exception:
            _logger.exception("Failed to resolve aiculler db path for category info lookup")
            db_path = ""
        if db_path and db_path != self._image_categories_db_path:
            self._refresh_image_categories_for_current_folder()
        for path in record.stack_paths:
            key = os.path.normcase(os.path.normpath(str(path)))
            info = self._image_categories_by_path.get(key)
            if info:
                return info
        ai_result = self._ai_run.ai_result_for_record_memory(record, preferred_path=record.path)
        category = str(getattr(ai_result, "primary_category", "") or "")
        if category:
            return {"primary_category": category, "confidence": 0.0}
        return {}

    @staticmethod
    def _category_profile(category_info: dict[str, object], face_records: tuple[object, ...]) -> str:
        if face_records:
            return "people_portrait"
        category = str(category_info.get("primary_category") or "uncategorized").strip().lower()
        return category or "uncategorized"

    def _face_preview_for_record(self, record: ImageRecord | None) -> QImage | None:
        bundle = self._face_bundle_for_record(record)
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
            crop = self._rotate_face_crop_to_source_orientation(crop, source_path)
        return crop

    @staticmethod
    def _rotate_face_crop_to_source_orientation(crop: QImage, source_path: str) -> QImage:
        orientation = MainWindow._source_orientation_for_face_preview(source_path)
        if crop.isNull() or orientation not in (5, 6, 7, 8):
            return crop
        transform = QTransform()
        if orientation == 5:
            transform.rotate(90)
            transform.scale(-1.0, 1.0)
        elif orientation == 6:
            transform.rotate(90)
        elif orientation == 7:
            transform.rotate(-90)
            transform.scale(-1.0, 1.0)
        elif orientation == 8:
            transform.rotate(-90)
        rotated = crop.transformed(transform, Qt.TransformationMode.SmoothTransformation)
        return rotated if not rotated.isNull() else crop

    @staticmethod
    def _source_orientation_for_face_preview(source_path: str) -> int:
        suffix = suffix_for_path(source_path)
        try:
            if suffix in RAW_SUFFIXES:
                from .raw_embedded_jpeg import extract_embedded_jpeg

                embedded = extract_embedded_jpeg(source_path)
                return int(embedded.orientation) if embedded is not None else 1
            if suffix in JPEG_SUFFIXES:
                from PIL import Image

                with Image.open(source_path) as img:
                    return int(img.getexif().get(274, 1) or 1)
        except Exception:
            return 1
        return 1

    def _reset_review_cache_status(self) -> None:
        self._review_grouping_cache_source = "idle"
        self._review_grouping_cache_detail = "Ready"
        self._review_feature_cache_source = "idle"
        self._review_feature_cache_detail = "Ready"
        self._review_scoring_cache_source = "idle"
        self._review_scoring_cache_detail = "Ready"

    def _handle_scan_cached(self, folder: str, token: int, records: list[ImageRecord], source: str) -> None:
        self._records_view.handle_scan_cached(folder, token, records, source)

    def _handle_scan_finished(self, folder: str, token: int, records: list[ImageRecord], source: str) -> None:
        self._records_view.handle_scan_finished(folder, token, records, source)

    def _handle_scan_children(self, folder: str, token: int, records: object) -> None:
        self._records_view.handle_scan_children(folder, token, records)

    def _handle_scan_failed(self, folder: str, token: int, message: str) -> None:
        self._records_view.handle_scan_failed(folder, token, message)

    def _handle_current_changed(self, index: int) -> None:
        logger = perf_logger()
        start = time.perf_counter() if logger.enabled else 0.0
        step_start = start
        self._sync_details_view_from_grid()
        if logger.enabled:
            now = time.perf_counter()
            logger.duration("window.current_changed.sync_details", (now - step_start) * 1000.0, index=index, view=self._browser_view_mode)
            step_start = now
        self._enqueue_filter_metadata_paths(self.grid.visible_item_paths(limit=200), front=True)
        if logger.enabled:
            now = time.perf_counter()
            logger.duration("window.current_changed.enqueue_metadata", (now - step_start) * 1000.0, index=index, view=self._browser_view_mode)
            step_start = now
        if self._aiculler.adapter_review_mode_active():
            self._aiculler.schedule_adapter_review_action_state_update()
        else:
            self._update_action_states()
        self._update_inspector_context(index)
        if logger.enabled:
            now = time.perf_counter()
            logger.duration(
                "window.current_changed.action_states",
                (now - step_start) * 1000.0,
                index=index,
                view=self._browser_view_mode,
                deferred=self._aiculler.adapter_review_mode_active(),
            )
            step_start = now
        self._update_status(index=index)
        if logger.enabled:
            now = time.perf_counter()
            logger.duration("window.current_changed.status", (now - step_start) * 1000.0, index=index, view=self._browser_view_mode)
            step_start = now
        if not self._preview_is_visible():
            self._schedule_preview_preload(index)
        if logger.enabled:
            now = time.perf_counter()
            logger.duration("window.current_changed.preview_preload", (now - step_start) * 1000.0, index=index, view=self._browser_view_mode)
        if logger.enabled:
            logger.duration("window.current_changed", (time.perf_counter() - start) * 1000.0, index=index, view=self._browser_view_mode)

    def _handle_inspector_thumbnail_ready(self, key, _image) -> None:
        current_record = self._record_at(self.grid.current_index())
        if current_record is None or current_record.is_folder:
            return
        displayed_path = self.grid.displayed_variant_path(self.grid.current_index()) or current_record.path
        if normalized_path_key(getattr(key, "path", "")) != normalized_path_key(displayed_path):
            return
        self._update_inspector_context()

    def _handle_grid_selection_changed(self) -> None:
        logger = perf_logger()
        start = time.perf_counter() if logger.enabled else 0.0
        self._sync_details_view_from_grid()
        deferred_action_state = self._aiculler.adapter_review_mode_active()
        if deferred_action_state:
            self._aiculler.schedule_adapter_review_action_state_update()
        else:
            self._update_action_states()
        self._update_status()
        self._enqueue_filter_metadata_paths(self._metadata_prefetch_seed_paths(lookahead=100), front=True)
        if logger.enabled:
            logger.duration(
                "window.selection_changed",
                (time.perf_counter() - start) * 1000.0,
                selected=self.grid.selected_count(),
                view=self._browser_view_mode,
                deferred_action_state=deferred_action_state,
            )

    # Per-folder AI-data probes (SQLite opens + artifact existence checks) are
    # stable while navigating within a folder, so cache them keyed by folder.
    # Invalidated on folder change (key mismatch), on AI operations that mutate a
    # folder's hidden cache (_invalidate_ai_folder_probe_cache), and by TTL.
    _AI_FOLDER_PROBE_TTL_S = 60.0

    def _sort_images_into_semantic_folders(self) -> None:
        if not self._current_folder:
            self.statusBar().showMessage("Open a source folder before sorting semantic classifications.")
            return
        if self._is_winners_folder() or self._is_recycle_folder():
            self.statusBar().showMessage("Semantic folder sorting runs from the source folder.")
            return
        if self._active_ai_task is not None:
            self.statusBar().showMessage("Wait for the current AI review run to finish before sorting.")
            return
        paths = build_ai_workflow_paths(self._current_folder)
        if not ai_semantic_artifacts_ready(paths):
            if ai_report_artifacts_ready(paths):
                rerun = QMessageBox.question(
                    self,
                    "Semantic Sort",
                    (
                        "Semantic classifications are missing or incomplete for this folder.\n\n"
                        "Run Cull & Score again now to generate the semantic classifications?"
                    ),
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.Yes,
                )
                if rerun == QMessageBox.StandardButton.Yes:
                    self._ai_run.run_ai_pipeline()
                return
            self.statusBar().showMessage("Run Cull & Score before sorting into semantic folders.")
            return
        try:
            classifications = load_semantic_classifications(paths.semantic_export_path)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Semantic Folder Sort", f"Could not load semantic classifications.\n\n{exc}")
            return

        grouped: dict[str, list[ImageRecord]] = {}
        for record in self._all_records:
            if record.is_folder:
                continue
            classification = semantic_classification_for_record(record, classifications)
            if classification is None:
                continue
            folder_name = semantic_folder_name(classification.primary_label)
            grouped.setdefault(folder_name, []).append(record)

        total = sum(len(records) for records in grouped.values())
        if not total:
            self.statusBar().showMessage("No semantic classifications matched the current folder.")
            return

        destination_root = Path(self._current_folder) / "_semantic"
        confirmation = QMessageBox.question(
            self,
            "Sort Into Semantic Folders",
            (
                f"Move {total} image bundle(s) into {len(grouped)} semantic subfolder(s) under:\n"
                f"{destination_root}\n\n"
                "This uses the semantic classification CSV from the last AI Review and can be undone with Undo."
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirmation != QMessageBox.StandardButton.Yes:
            return

        moved = 0
        for folder_name, records in sorted(grouped.items(), key=lambda item: item[0].casefold()):
            destination_dir = str(destination_root / folder_name)
            os.makedirs(destination_dir, exist_ok=True)
            for record in records:
                if self._record_ops.move_record_to_path(record.path, destination_dir):
                    moved += 1
        if moved:
            self._remember_recent_destination(str(destination_root))
            self._recycle_bin.refresh_recycle_button()
            self.statusBar().showMessage(f"Moved {moved} image bundle(s) into semantic folders")
            return
        self.statusBar().showMessage("No images were moved into semantic folders.")

    def _recalculate_review_counts(self) -> None:
        accepted = 0
        rejected = 0
        for record in self._all_records:
            annotation = self._annotations.get(record.path)
            if annotation is None:
                continue
            if annotation.winner:
                accepted += 1
            if annotation.reject:
                rejected += 1
        self._accepted_count = accepted
        self._rejected_count = rejected
        self._unreviewed_count = max(0, len(self._all_records) - accepted - rejected)

    def _apply_review_count_delta(
        self,
        previous_annotation: SessionAnnotation | None,
        annotation: SessionAnnotation | None,
    ) -> None:
        previous_accepted = 1 if previous_annotation is not None and previous_annotation.winner else 0
        previous_rejected = 1 if previous_annotation is not None and previous_annotation.reject else 0
        next_accepted = 1 if annotation is not None and annotation.winner else 0
        next_rejected = 1 if annotation is not None and annotation.reject else 0
        self._accepted_count = max(0, self._accepted_count + next_accepted - previous_accepted)
        self._rejected_count = max(0, self._rejected_count + next_rejected - previous_rejected)
        self._unreviewed_count = max(0, len(self._all_records) - self._accepted_count - self._rejected_count)

    # Map adapter 1-5 labels to confidence buckets. Used by the
    # user-label override so a disputed/labeled card flips bucket immediately
    # without waiting for the next training pass.
    _USER_LABEL_TO_BUCKET = {
        "hero": "OBVIOUS_WINNER",
        "portfolio": "OBVIOUS_WINNER",
        "strong": "LIKELY_KEEPER",
        "keep": "LIKELY_KEEPER",
        "good": "LIKELY_KEEPER",
        "k": "LIKELY_KEEPER",
        "yes": "LIKELY_KEEPER",
        "1": "LIKELY_KEEPER",
        "maybe": "NEEDS_REVIEW",
        "weak": "LIKELY_REJECT",
        "reject": "LIKELY_REJECT",
        "bad": "LIKELY_REJECT",
        "r": "LIKELY_REJECT",
        "no": "LIKELY_REJECT",
        "0": "LIKELY_REJECT",
    }

    def _review_insight_for_record(self, record: ImageRecord | None):
        if record is None or self._review_intelligence is None:
            return None
        return self._review_intelligence.insight_for_path(record.path)

    def _review_insight_for_path(self, path: str):
        if not path or self._review_intelligence is None:
            return None
        return self._review_intelligence.insight_for_path(path)

    def _burst_recommendation_for_record(self, record: ImageRecord | None):
        if record is None:
            return None
        return self._burst_recommendations.get(record.path) or self._burst_recommendations.get(_memory_path_key(record.path))

    def _workflow_insight_for_record(self, record: ImageRecord | None):
        if record is None:
            return None
        return self._workflow_insights_by_path.get(record.path) or self._workflow_insights_by_path.get(_memory_path_key(record.path))

    def _prefilter_decision_for_record(self, record: ImageRecord | None) -> PrefilterDecision | None:
        if record is None:
            return None
        for path in record.stack_paths:
            decision = self._prefilter_decisions_by_path.get(normalized_path_key(path))
            if decision is not None:
                return decision
        return None

    _PREFILTER_LOAD_TTL_S = 60.0

    def _refresh_prefilter_decisions_for_current_folder(self) -> None:
        if not self._current_folder:
            self._prefilter_decisions_by_path = {}
            return
        if self._is_slow_source_folder(self._current_folder):
            # Runs every time the records view is finalized, and loading the decisions resolves the hidden
            # folder, stats and reads a file and resolves every decision's path: all over the share. So
            # for a share a worker does it (at most once a minute per folder) and the arriving answer
            # is pushed to the grid.
            self._load_prefilter_decisions_off_thread(self._current_folder)
            return
        try:
            decisions = load_phash_prefilter_decisions(build_phash_prefilter_paths(self._current_folder))
        except Exception:
            _logger.exception("Failed to load phash prefilter decisions for %s", self._current_folder)
            decisions = {}
        self._prefilter_decisions_by_path = {
            normalized_path_key(path): decision
            for path, decision in decisions.items()
            if normalized_path_key(path)
        }

    def _load_prefilter_decisions_off_thread(self, folder: str) -> None:
        now = time.monotonic()
        if self._prefilter_load_folder == folder and (
            self._prefilter_load_task is not None or now - self._prefilter_load_at < self._PREFILTER_LOAD_TTL_S
        ):
            return
        self._prefilter_load_token += 1
        self._prefilter_load_folder = folder
        self._prefilter_load_at = now
        task = _PrefilterDecisionsTask(self._prefilter_load_token, folder)
        task.signals.ready.connect(self._handle_prefilter_decisions_ready, Qt.ConnectionType.QueuedConnection)
        self._prefilter_load_task = task
        QThreadPool.globalInstance().start(task)

    def _handle_prefilter_decisions_ready(self, token: int, folder: str, decisions: object) -> None:
        if token == self._prefilter_load_token:
            self._prefilter_load_task = None
        if token != self._prefilter_load_token or folder != self._current_folder or not isinstance(decisions, dict):
            return
        self._prefilter_decisions_by_path = decisions
        self.grid.set_prefilter_decisions(decisions)

    def _workflow_summary_for_record(self, record: ImageRecord | None) -> str:
        insight = self._workflow_insight_for_record(record)
        if insight is None:
            return ""
        return insight.summary_text

    def _workflow_detail_lines_for_record(self, record: ImageRecord | None) -> tuple[str, ...]:
        insight = self._workflow_insight_for_record(record)
        if insight is None:
            return ()
        return insight.detail_lines

    def _review_summary_for_record(self, record: ImageRecord | None) -> str:
        parts: list[str] = []
        insight = self._review_insight_for_record(record)
        workflow = self._workflow_insight_for_record(record)
        for text in (
            insight.summary_text if insight is not None else "",
            workflow.summary_text if workflow is not None else "",
        ):
            if text and text not in parts:
                parts.append(text)
        return " | ".join(parts)

    def _refresh_workflow_insights_cache(
        self,
        *,
        changed_paths: set[str] | None = None,
        force_full: bool = False,
    ) -> None:
        if force_full:
            insights: dict[str, RecordWorkflowInsight] = {}
            for record in self._all_records:
                annotation = self._annotations.get(record.path, SessionAnnotation())
                ai_result = self._ai_run.ai_result_for_record(record)
                burst_recommendation = self._burst_recommendation_for_record(record)
                workflow = build_record_workflow_insight(
                    annotation,
                    ai_result,
                    burst_recommendation,
                    self._taste_profile,
                )
                insights[record.path] = workflow
                lookup_key = _memory_path_key(record.path)
                if lookup_key != record.path:
                    insights[lookup_key] = workflow
            self._workflow_insights_by_path = insights
            return

        if not changed_paths:
            return

        if not self._workflow_insights_by_path:
            self._workflow_insights_by_path = {}

        for path in changed_paths:
            record = self._record_for_path(path)
            if record is None:
                self._workflow_insights_by_path.pop(path, None)
                self._workflow_insights_by_path.pop(_memory_path_key(path), None)
                continue
            annotation = self._annotations.get(record.path, SessionAnnotation())
            ai_result = self._ai_run.ai_result_for_record(record)
            burst_recommendation = self._burst_recommendation_for_record(record)
            workflow = build_record_workflow_insight(
                annotation,
                ai_result,
                burst_recommendation,
                self._taste_profile,
            )
            self._workflow_insights_by_path[record.path] = workflow
            lookup_key = _memory_path_key(record.path)
            if lookup_key != record.path:
                self._workflow_insights_by_path[lookup_key] = workflow

    def _record_for_path(self, path: str) -> ImageRecord | None:
        direct = self._all_records_by_path.get(path)
        if direct is not None:
            return direct
        normalized = _memory_path_key(path)
        for record_path, record in self._all_records_by_path.items():
            if _memory_path_key(record_path) == normalized:
                return record
        return None

    def _annotation_prefers_frame(self, annotation: SessionAnnotation | None) -> bool:
        if annotation is None:
            return False
        return annotation.winner

    def _comparison_target_for_preference(self, record: ImageRecord, ai_result, burst_recommendation) -> str:
        if burst_recommendation is not None and burst_recommendation.recommended_path:
            if normalized_path_key(burst_recommendation.recommended_path) != normalized_path_key(record.path):
                return burst_recommendation.recommended_path
        if ai_result is not None and self._ai_bundle is not None and ai_result.group_size > 1 and not ai_result.is_top_pick:
            group_results = self._ai_bundle.group_results(ai_result.group_id)
            if group_results:
                target_path = group_results[0].file_path
                if normalized_path_key(target_path) != normalized_path_key(record.path):
                    return target_path
        return ""

    def _build_pairwise_feedback_payload(self, preferred_path: str, other_path: str) -> dict[str, object]:
        preferred_record = self._record_for_path(preferred_path)
        other_record = self._record_for_path(other_path)
        preferred_ai = self._ai_run.ai_result_for_record(preferred_record) if preferred_record is not None else None
        other_ai = self._ai_run.ai_result_for_record(other_record) if other_record is not None else None
        preferred_review = self._review_insight_for_path(preferred_path)
        other_review = self._review_insight_for_path(other_path)
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

    def _append_jsonl_record(self, path: Path, payload: dict[str, object]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=True) + "\n")

    def _record_pairwise_preference(
        self,
        *,
        left_path: str,
        right_path: str,
        preferred_path: str,
        source_mode: str,
        group_id: str = "",
        extra_payload: dict[str, object] | None = None,
    ) -> None:
        if not self._current_folder or not left_path or not right_path or not preferred_path:
            return
        label_payload = build_pairwise_label_payload(
            folder=self._current_folder,
            left_path=left_path,
            right_path=right_path,
            preferred_path=preferred_path,
            source_mode=source_mode,
            cluster_id=group_id,
            annotator_id=self._session_id,
        )
        try:
            training_paths = prepare_hidden_ai_training_workspace(self._current_folder)
            self._append_jsonl_record(training_paths.pairwise_labels_path, label_payload)
        except OSError:
            return

        other_path = right_path if normalized_path_key(preferred_path) == normalized_path_key(left_path) else left_path
        payload = self._build_pairwise_feedback_payload(preferred_path, other_path)
        if extra_payload:
            payload.update(extra_payload)
        payload.update(
            {
                "label_id": label_payload["label_id"],
                "left_path": left_path,
                "right_path": right_path,
            }
        )
        preferred_record = self._record_for_path(preferred_path)
        preferred_annotation = self._annotations.get(preferred_path, SessionAnnotation())
        preferred_ai = self._ai_run.ai_result_for_record(preferred_record) if preferred_record is not None else None
        self._decision_store.record_correction_event(
            self._session_id,
            folder_path=self._current_folder,
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
        self._schedule_scope_enrichment_refresh()

    def _record_annotation_feedback_event(
        self,
        record: ImageRecord,
        annotation: SessionAnnotation,
        ai_result,
        *,
        source_mode: str,
        payload: dict[str, object],
    ) -> None:
        if not self._current_folder or ai_result is None:
            return
        self._decision_store.record_correction_event(
            self._session_id,
            folder_path=self._current_folder,
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

    def _capture_annotation_feedback(
        self,
        record: ImageRecord,
        previous_annotation: SessionAnnotation,
        annotation: SessionAnnotation,
        *,
        source_mode: str,
    ) -> None:
        ai_result = self._ai_run.ai_result_for_record(record)
        burst_recommendation = self._burst_recommendation_for_record(record)
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
            self._record_annotation_feedback_event(record, annotation, ai_result, source_mode=source_mode, payload=payload)

        if self._annotation_prefers_frame(previous_annotation) or not self._annotation_prefers_frame(annotation):
            return
        ai_target_path = ""
        if ai_result is not None and self._ai_bundle is not None:
            ai_target_path = ai_disagreement_group_leader_path(
                record.path,
                ai_result,
                self._ai_bundle.group_results(ai_result.group_id),
            )
        target_path = ai_target_path or self._comparison_target_for_preference(record, ai_result, burst_recommendation)
        if not target_path:
            return
        group_id = ""
        if ai_target_path and ai_result is not None:
            group_id = ai_result.group_id
        elif burst_recommendation is not None:
            group_id = burst_recommendation.group_id
        elif ai_result is not None:
            group_id = ai_result.group_id
        self._record_pairwise_preference(
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

    def _preview_path_for_index(self, index: int) -> str:
        record = self._record_at(index)
        if record is None:
            return ""
        displayed = self.grid.displayed_variant_path(index)
        return displayed or self._preview_source_path(record)

    def _preview_placeholder_for_index(self, index: int):
        if index < 0:
            return None
        return self.grid.thumbnail_for(index)

    def _rebuild_visible_preview_group_indexes(self) -> None:
        review_rows_by_id: dict[str, list[int]] = {}
        ai_rows_by_id: dict[str, list[int]] = {}
        for row_index, record in enumerate(self._records):
            review_insight = self._review_insight_for_record(record)
            if review_insight is not None and review_insight.has_group:
                review_rows_by_id.setdefault(review_insight.group_id, []).append(row_index)
            preferred_path = self.grid.displayed_variant_path(row_index) if record.has_variant_stack else record.path
            ai_result = self._ai_run.ai_result_for_record_memory(record, preferred_path=preferred_path)
            if ai_result is not None and ai_result.group_size > 1:
                ai_rows_by_id.setdefault(ai_result.group_id, []).append(row_index)
        self._visible_review_group_rows_by_id = review_rows_by_id
        self._visible_ai_group_rows_by_id = ai_rows_by_id

    def _schedule_preview_preload(self, index: int | None = None) -> None:
        if index is None:
            index = self.grid.current_index()
        if index < 0:
            return
        self._preview_preload_index = index
        self._preview_preload_timer.start()

    def _run_preview_preload(self) -> None:
        index = self._preview_preload_index
        self._preview_preload_index = None
        if index is None or index < 0 or not self._preview_is_visible():
            return
        paths = self._likely_preview_preload_paths(index)
        self.preview.preload_paths(paths)

    def _likely_preview_preload_paths(self, index: int) -> list[str]:
        limit = self._normalize_preview_preload_batch_size(
            getattr(self, "_preview_preload_batch_size", self.PREVIEW_PRELOAD_BATCH_SIZE_DEFAULT)
        )
        if limit <= 0 or not self._records:
            return []
        ordered: list[str] = []
        seen: set[str] = set()

        def add(candidate_index: int) -> None:
            if not 0 <= candidate_index < len(self._records):
                return
            record = self._record_at(candidate_index)
            if record is None or record.is_folder:
                return
            path = self._preview_path_for_index(candidate_index)
            if not path:
                return
            normalized = normalized_path_key(path)
            if normalized in seen:
                return
            seen.add(normalized)
            ordered.append(path)

        add(index)
        delta = 1
        while len(ordered) < limit and delta < len(self._records):
            add(index + delta)
            if len(ordered) >= limit:
                break
            add(index - delta)
            delta += 1

        current_record = self._record_at(index)
        current_insight = self._review_insight_for_record(current_record)
        if current_insight is not None and current_insight.has_group:
            for row_index in self._visible_review_group_rows_by_id.get(current_insight.group_id, ()):
                add(row_index)
                if len(ordered) >= limit:
                    break

        current_ai = self._ai_run.ai_result_for_index(index)
        if current_ai is not None and current_ai.group_size > 1:
            for row_index in self._visible_ai_group_rows_by_id.get(current_ai.group_id, ()):
                add(row_index)
                if len(ordered) >= limit:
                    break

        return ordered[:limit]

    def _update_inspector_context(self, index: int | None = None) -> None:
        logger = perf_logger()
        start = time.perf_counter() if logger.enabled else 0.0
        if self.inspector_panel is None:
            return
        if index is None:
            index = self.grid.current_index()

        current_record = self._record_at(index)
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
            display_path = self.grid.displayed_variant_path(index) or current_record.path
            annotation = self._annotations.get(current_record.path, SessionAnnotation())
            ai_result = self._ai_run.ai_result_for_record(current_record, preferred_path=display_path)
            review_insight = self._review_insight_for_record(current_record)
            workflow_insight = self._workflow_insight_for_record(current_record)
            thumbnail = self._inspector_thumbnail_for(current_record, index, display_path)
            if thumbnail is not None and not thumbnail.isNull() and not current_record.is_folder:
                inspection_stats = self._cached_inspection_stats_for_thumbnail(current_record, display_path, thumbnail)
                if inspection_stats is None:
                    self._schedule_inspection_stats_for_thumbnail(current_record, display_path, thumbnail)
            if not current_record.is_folder:
                metadata = self._filter_metadata_manager.get_cached(current_record)
                if metadata is None:
                    self._enqueue_filter_metadata_paths((current_record.path,), front=True)
            review_summary = self._review_summary_for_record(current_record)
            workflow_summary = self._workflow_summary_for_record(current_record)
            workflow_details = self._workflow_detail_lines_for_record(current_record)
            face_records = self._face_records_for_record(current_record)
            face_preview = self._face_preview_for_record(current_record)
            category_info = self._category_info_for_record(current_record)
            category_profile = self._category_profile(category_info, face_records)
        else:
            face_records = ()
            face_preview = None
            category_info = {}
            category_profile = "uncategorized"

        self.inspector_panel.set_context(
            folder=self._scope_display_label(),
            mode_label="Manual Review",
            selected_count=self.grid.selected_count() if self._records else 0,
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
            self.inspector_panel.set_position(*self.grid.visible_position(index))
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

    def _inspection_stats_cache_key(self, record: ImageRecord, display_path: str, thumbnail) -> tuple[str, int, int, int, int]:
        cache_path = display_path or record.path
        return (
            normalized_path_key(cache_path),
            int(record.modified_ns or 0),
            int(record.size or 0),
            int(thumbnail.width()),
            int(thumbnail.height()),
        )

    def _inspector_thumbnail_for(self, record: ImageRecord, index: int, display_path: str) -> QImage | None:
        if record.is_folder:
            return None

        fallback = self.grid.thumbnail_for(index)
        variant = self._inspector_thumbnail_variant(record, display_path)
        target = self.INSPECTOR_PREVIEW_TARGET_SIZE
        image = self.thumbnail_manager.get_cached(variant, target)
        if image is not None and not image.isNull():
            return image

        self.thumbnail_manager.request_thumbnail(
            variant,
            target,
            priority=30_000,
            drop_if_not_wanted=False,
        )
        return fallback

    @staticmethod
    def _inspector_thumbnail_variant(record: ImageRecord, display_path: str) -> ImageRecord | ImageVariant:
        path = display_path or record.path
        if normalized_path_key(path) == normalized_path_key(record.path):
            return record
        path_key = normalized_path_key(path)
        for variant in record.display_variants:
            if normalized_path_key(variant.path) == path_key:
                return variant
        try:
            stat_result = os.stat(path)
            size = int(stat_result.st_size)
            modified_ns = int(stat_result.st_mtime_ns)
        except OSError:
            size = int(record.size or 0)
            modified_ns = int(record.modified_ns or 0)
        return ImageVariant(
            path=path,
            name=Path(path).name,
            size=size,
            modified_ns=modified_ns,
        )

    def _cached_inspection_stats_for_thumbnail(self, record: ImageRecord, display_path: str, thumbnail) -> InspectionStats | None:
        cache_key = self._inspection_stats_cache_key(record, display_path, thumbnail)
        cached = self._inspection_stats_cache.get(cache_key)
        if cached is not None:
            return cached
        return None

    def _schedule_inspection_stats_for_thumbnail(self, record: ImageRecord, display_path: str, thumbnail) -> None:
        cache_key = self._inspection_stats_cache_key(record, display_path, thumbnail)
        if cache_key in self._inspection_stats_cache or cache_key in self._inspection_stats_pending_keys:
            return
        self._inspection_stats_pending_keys.add(cache_key)
        request = InspectorStatsRequest(cache_key=cache_key, image=thumbnail.copy())
        self._inspection_stats_pool.start(InspectorStatsTask(request, self._inspection_stats_result_queue), 0)
        if not self._inspection_stats_drain_timer.isActive():
            self._inspection_stats_drain_timer.start()

    def _drain_inspector_stats_results(self) -> None:
        processed = 0
        changed = False
        while processed < 8:
            try:
                state, cache_key, payload = self._inspection_stats_result_queue.get_nowait()
            except Empty:
                break
            self._inspection_stats_pending_keys.discard(cache_key)
            if state == "ready":
                if len(self._inspection_stats_cache) >= 2048:
                    self._inspection_stats_cache.clear()
                self._inspection_stats_cache[cache_key] = payload
                changed = True
            processed += 1

        if changed:
            self._update_inspector_context()
        if processed == 0 and not self._inspection_stats_pending_keys:
            self._inspection_stats_drain_timer.stop()

    def _update_status(self, index: int | None = None) -> None:
        if index is None:
            index = self.grid.current_index()
        self._update_inspector_context(index)
        self._records_view.update_filter_summary()
        scope_label = self._scope_display_label()
        self._update_selection_count_labels()

        if self._records_view.records_view_chunk_active():
            total = len(self._records_view_chunk_records)
            loaded = min(len(self._records), total)
            selected_count = self.grid.selected_count() if loaded else 0
            self.summary_total.setText(f"Total: Loading {loaded} / {total}")
            self.summary_selected.setText(f"Selected: {selected_count}")
            self.summary_accepted.setText(f"Winners: {self._accepted_count}")
            self.summary_rejected.setText(f"Rejected: {self._rejected_count}")
            self.summary_unreviewed.setText(f"Unreviewed: {self._unreviewed_count}")
            self._ai_run.update_ai_summary()
            self.statusBar().showMessage(f"Loading {loaded} / {total} images from {scope_label}...")
            return

        if self._scan_in_progress and not self._all_records and not self._records:
            self.summary_total.setText("Total: scanning...")
            self.summary_selected.setText("Selected: 0")
            self.summary_accepted.setText("Winners: 0")
            self.summary_rejected.setText("Rejected: 0")
            self.summary_unreviewed.setText("Unreviewed: ...")
            self._ai_run.update_ai_summary()
            self.statusBar().showMessage(f"Scanning {scope_label}...")
            return

        count = len(self._records)
        accepted = self._accepted_count
        rejected = self._rejected_count
        remaining = self._unreviewed_count
        selected_count = self.grid.selected_count() if count else 0

        self.summary_total.setText(f"Total: {count}")
        self.summary_selected.setText(f"Selected: {selected_count}")
        self.summary_accepted.setText(f"Winners: {accepted}")
        self.summary_rejected.setText(f"Rejected: {rejected}")
        self.summary_unreviewed.setText(f"Unreviewed: {remaining}")
        self._ai_run.update_ai_summary()

        # The breadcrumb names the folder, so the status line only counts.
        gap = " "
        reviewed = max(0, count - remaining)
        tally = f"{count:,} photos{gap}{reviewed:,} reviewed · {accepted:,} winners · {rejected:,} rejected"
        if count == 0:
            self.statusBar().showMessage("0 photos")
            return

        selected_indexes = self.grid.selected_indexes()
        if len(selected_indexes) > 1:
            self.statusBar().showMessage(f"{tally}{gap}{len(selected_indexes):,} selected")
            return

        message = tally
        record = self._record_at(index)
        preferred_path = self.grid.displayed_variant_path(index) if record and record.has_variant_stack else ""
        ai_result = self._ai_run.ai_result_for_record(record, preferred_path=preferred_path)
        if ai_result is not None:
            ai_parts = [f"AI {ai_result.display_score_text}", ai_result.confidence_bucket_label]
            if ai_result.group_id:
                ai_parts.append(ai_result.group_id)
            if ai_result.group_size > 1:
                ai_parts.append(ai_result.rank_text)
                if ai_result.is_top_pick:
                    ai_parts.append("top pick")
            message = f"{message} | {' | '.join(ai_parts)}"
        self.statusBar().showMessage(message)

    def _show_markdown_help_dialog(self, *, title: str, markdown: str) -> None:
        dialog = HelpMarkdownDialog(title=title, markdown=markdown, parent=self)
        self._exec_dialog_with_geometry(dialog, f"help_{title}")

    def _show_paged_help_dialog(self, *, title: str, pages: tuple[object, ...]) -> None:
        show_paged_help(self, title=title, pages=pages)

    def _show_documentation(self) -> None:
        from .ui.docs import open_documentation

        open_documentation(self)

    def _show_library_help(self) -> None:
        self._show_paged_help_dialog(
            title="Library Help",
            pages=library_help_pages(),
        )

    def _show_help(self) -> None:
        self._show_markdown_help_dialog(
            title="Image Triage Quick Start",
            markdown=dedent(
                """
                # Quick start

                The fastest path from opening a folder to a sorted set.

                1. **Open a folder** — `File > Open Folder...`.
                2. **Select images** — click, `Ctrl`-click, `Shift`-click, or drag to marquee-select.
                3. **Sort quickly** — `W` accept, `X` reject, `K` move to `_keep`, `M` move, `Delete` trash.
                4. **Preview** — `Space` or `Enter`.
                5. **Run batch actions** — right-click or the **Tools** menu for rename, resize, convert, and archive.
                6. **Organize by drag and drop** — drop onto folders or favorites; hold `Ctrl` to copy instead of move.
                7. **Toggle burst views** — **`View > Review View > Smart Groups`** marks likely burst sequences, while **Smart Stacks** collapses similar frames behind one representative.
                8. **Explore AI** — open **`Help > AI Guide`** for scoring, review, and applying clear decisions.

                ## Need more?

                - **`Help > AI Guide`** — the full AI workflow.
                - **`Help > Advanced Help`** — broader controls and shortcuts.
                - Help or **`?`** buttons in the AI Workflow Center, Settings, Catalog, Collections, and Workflow dialogs — focused, step-by-step help.
                """
            ),
        )

    def _show_ai_review_tag_legend(self) -> None:
        self._show_markdown_help_dialog(
            title="AI Review Tag Legend",
            markdown=dedent(
                f"""
                # AI Review tag legend

                A quick reference for the AI badges Image Triage can show.

                {self._ai_run.ai_review_tags_markdown()}
                """
            ),
        )

    def _show_ai_guide(self) -> None:
        self._show_markdown_help_dialog(
            title="Image Triage AI Guide",
            markdown=dedent(
                f"""
                # AI Guide

                AI is a core part of Image Triage. The current culling workflow groups, scores, ranks, and reviews the images in a folder.

                The guiding principle is simple: **AI suggests, you stay in control.**

                ## AI setup

                The installer opens a first-launch setup step for the optional AI runtime and local model files.

                - Choose the GPU or CPU runtime profile.
                - Setup installs the ONNX runtime and the current CLI-Culler model set: CLIP, TOPIQ, and InsightFace quality models.
                - If you skip it, install later from **`AI > AI Setup And Cache > Set Up AI...`**.

                ## What AI adds to review

                Once AI results are loaded, the app can show:

                - ranked groups
                - per-image AI scores
                - top-pick hints
                - compare groups inside preview
                - a saved HTML report for the folder

                ## AI review workflow

                Use this when you want the app to score a folder and help you review it faster. Open **`AI > AI Workflow Center...`** and use its **`?`** button for the detailed, stage-by-stage guide.

                1. Open the folder you want to review.
                2. Open **`AI > AI Workflow Center...`** and run **Cull & Score**.
                3. Wait for extraction, grouping, scoring, and report export to finish.
                4. The app loads the new results and switches into **AI Review** automatically.
                5. Press **`Ctrl+Alt+N`** to jump to the next AI top pick.
                6. Press **`Ctrl+Alt+G`** to compare the current AI group.
                7. Choose **`AI > Run And Apply > Apply AI Decisions`** to auto-file only the clearest winners and rejects.
                8. Later, use **Load Saved** on the AI task rail, or find **Load Saved AI For Folder** in the Command Palette, to reopen cached results without rerunning the models.

                ## AI review tags

                {self._ai_run.ai_review_tags_markdown()}

                ## How the cull is scored

                - **pHash** groups near-duplicate frames.
                - **CLIP** scores visual content and assigns semantic categories.
                - **TOPIQ** adds technical quality signals.
                - **InsightFace** adds face and eye quality when faces are present.
                - Similar-image clustering and diversity penalties keep bursts from dominating the top results.

                ## Where AI files live

                Every AI-enabled folder gets a hidden workspace beside the images:

                - **`.image_triage_ai/artifacts`** — CLI-Culler database and intermediate artifacts.
                - **`.image_triage_ai/ranker_report`** — scored exports and the HTML report.

                ## Best practices

                - Start with folders that match the kind of work you care about most.
                - Use the Guided AI Cull when you want a quick keeper percentage and review band.
                - Review the uncertain middle manually before applying file moves.

                ## Troubleshooting

                - If rankings look stale and the folder is unchanged, use **Quick Rerank**.
                - If images were added or removed, rerun **Cull & Score**.
                - If AI actions are disabled, open **`AI > AI Setup And Cache > Set Up AI...`** and check the setup state.
                """
            ),
        )

    def _show_advanced_help(self) -> None:
        self._show_markdown_help_dialog(
            title="Image Triage Advanced Help",
            markdown=dedent(
                """
                # Advanced Help

                A broader reference for the rest of the app.

                ## Selection

                - `Ctrl`-click adds or removes an image
                - `Shift`-click selects a range
                - `Ctrl+A` selects all visible images
                - Drag on empty space to marquee-select, like File Explorer
                - Drag selected thumbnails onto folders or favorites to move them
                - Hold `Ctrl` while dragging to copy instead of move
                - **`View > Review View > Smart Groups`** highlights likely capture bursts in the grid as a toggle, not a permanent regrouping
                - **`View > Review View > Smart Stacks`** adds stacked burst visuals plus burst cycling in the main viewer with `[` and `]`

                ## Core review

                - `Space` or `Enter` opens Preview
                - `W` accepts
                - `X` rejects
                - `K` moves to `_keep`
                - `M` moves to a folder
                - `Delete` trashes
                - `Ctrl+Z` undoes the last change
                - `T` tags
                - `C` toggles compare

                ## Tools

                - Use the **Tools** menu for **Batch Rename**, **Batch Resize**, **Batch Convert**, and archive actions
                - Batch tools use the checkbox mode in the grid
                - Resize and Convert are also available from the image right-click menu
                - RAW files are skipped for Resize and Convert
                - The **AI Workflow Center** shows setup, Cull & Score, result review, and applying decisions in order
                - Long AI tasks show progress and a detailed activity log when that option is enabled in Settings

                ## Preview

                - Mouse wheel or `Z` zooms
                - `0` returns to fit
                - `L` toggles the loupe
                - `C` toggles compare
                - `Tab` changes preview focus
                - Left and Right navigate
                - Before/After compares the original with the latest detected edit
                - Open In Photoshop sends the current preview image to Photoshop

                ## Folders and AI

                - Right-click folders or favorites to create, rename, move, delete, or favorite them
                - Recent destinations appear in the copy and move menus for faster sorting
                - The Library panel's bottom **Help** button explains favorites, collections, and catalog search
                - Workflow dialogs include their own **`?`** help for recipes, content mode, transfer mode, and saved recipes
                - Settings includes a **Settings Guide** button for General, Interface, folders, AI Culling, Duplicates, and Shortcuts
                - **AI Review** lets you inspect results, apply clear decisions, or load saved results for the current folder
                - **`Help > AI Guide`** is the dedicated walkthrough for the AI side of the app
                - `Ctrl+Alt+N` jumps to the next AI top pick
                - `Ctrl+Alt+G` compares the current AI group
                """
            ),
        )

    def _show_about_dialog(self) -> None:
        QMessageBox.information(
            self,
            "About Image Triage",
            "\n".join(
                [
                    "Image Triage",
                    f"Version {current_app_version()}",
                    "",
                    "A desktop photo triage tool built for speed, keyboard-driven flow, and AI-assisted review.",
                    "Sort, rate, and cull large shoots quickly, then hand off or export the keepers.",
                ]
            ),
        )

    def _check_for_updates_on_startup(self) -> None:
        if not self._check_updates_on_startup:
            return
        self._check_for_updates(silent=True)

    def _check_for_updates(self, checked: bool = False, *, silent: bool = False) -> None:
        if self._active_update_check_task is not None or self._active_update_download_task is not None:
            return
        self._update_check_silent = bool(silent)
        if self.actions is not None:
            self.actions.check_for_updates.setEnabled(False)
        if not silent:
            self.statusBar().showMessage("Checking for updates...")
        task = AppUpdateCheckTask(current_version=current_app_version())
        self._active_update_check_task = task
        self._refresh_update_button_state()
        task.signals.finished.connect(self._handle_update_check_finished)
        task.signals.failed.connect(self._handle_update_check_failed)
        self._app_update_pool.start(task)

    def _handle_update_check_finished(self, raw_result: object) -> None:
        silent = self._update_check_silent
        self._update_check_silent = False
        self._active_update_check_task = None
        self._update_action_states()
        if not isinstance(raw_result, UpdateCheckResult):
            if not silent:
                QMessageBox.warning(self, "Check For Updates", "The update check returned an unexpected result.")
            self.statusBar().showMessage("Update check failed")
            return
        result = raw_result
        latest = result.latest
        if not result.update_available:
            self._pending_update_result = None
            self._refresh_update_button_state()
            if not silent:
                QMessageBox.information(
                    self,
                    "Check For Updates",
                    f"Image Triage is up to date.\n\nInstalled version: {result.current_version}",
                )
                self.statusBar().showMessage("Image Triage is up to date")
            return

        self._pending_update_result = result
        self._refresh_update_button_state()
        if silent:
            self.statusBar().showMessage(f"Update available: Image Triage {latest.version}")
            return

        self._prompt_for_update_download(result)

    def _prompt_for_update_download(self, result: UpdateCheckResult) -> None:
        latest = result.latest

        if not latest.is_verifiable:
            QMessageBox.warning(
                self,
                "Update Cannot Be Verified",
                f"Image Triage {latest.version} is available, but this release does not publish a "
                "checksum, so the installer cannot be verified and will not be downloaded."
                + (f"{chr(10)}{chr(10)}Release page: {latest.release_notes_url}" if latest.release_notes_url else ""),
            )
            self.statusBar().showMessage("Update skipped: no checksum published")
            return

        details = [
            f"Image Triage {latest.version} is available.",
            "",
            f"Installed version: {result.current_version}",
        ]
        if latest.release_notes_url:
            details.extend(["", f"Release notes: {latest.release_notes_url}"])
        details.extend(
            [
                "",
                "Download and install this update now?",
                "Image Triage will close and restart after the silent MSI install finishes.",
            ]
        )
        choice = QMessageBox.question(
            self,
            "Update Available",
            "\n".join(details),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if choice != QMessageBox.StandardButton.Yes:
            self.statusBar().showMessage("Update download skipped")
            return
        self._download_update_installer(latest)

    def _handle_update_check_failed(self, message: str) -> None:
        silent = self._update_check_silent
        self._update_check_silent = False
        self._active_update_check_task = None
        self._update_action_states()
        if not silent:
            QMessageBox.warning(self, "Check For Updates", message)
            self.statusBar().showMessage("Update check failed")

    def _download_update_installer(self, update: UpdateInfo) -> None:
        if self._active_update_download_task is not None:
            return
        if self.actions is not None:
            self.actions.check_for_updates.setEnabled(False)
        task = AppUpdateDownloadTask(update=update)
        self._active_update_download_task = task
        self._refresh_update_button_state()
        task.signals.started.connect(self._handle_update_download_started)
        task.signals.progress.connect(self._handle_update_download_progress)
        task.signals.finished.connect(self._handle_update_download_finished)
        task.signals.failed.connect(self._handle_update_download_failed)
        self._app_update_pool.start(task)

    def _handle_update_download_started(self, filename: str) -> None:
        dialog = self._show_update_progress_dialog()
        dialog.setRange(0, 0)
        dialog.setValue(0)
        dialog.setLabelText(f"Downloading {filename}...")
        dialog.show()
        self.statusBar().showMessage("Downloading update...")

    def _handle_update_download_progress(self, current: int, total: int, filename: str) -> None:
        dialog = self._show_update_progress_dialog()
        if total > 0:
            unit = 1024 * 1024
            total_units = max(1, (int(total) + unit - 1) // unit)
            current_units = min(total_units, (int(current) + unit - 1) // unit)
            dialog.setRange(0, total_units)
            dialog.setValue(current_units)
            dialog.setLabelText(f"Downloading {filename} ({current_units}/{total_units} MB)...")
        else:
            dialog.setRange(0, 0)
            dialog.setLabelText(f"Downloading {filename}...")

    def _handle_update_download_finished(self, installer_path: str) -> None:
        self._active_update_download_task = None
        self._close_update_progress_dialog()
        self._update_action_states()
        try:
            self._update_installing = True
            self._refresh_update_button_state()
            launch_update_installer_and_restart(installer_path)
        except Exception as exc:
            self._update_installing = False
            self._refresh_update_button_state()
            QMessageBox.warning(self, "Install Update", str(exc))
            self.statusBar().showMessage("Could not launch update installer")
            return
        self.statusBar().showMessage("Installing update; Image Triage will restart when finished")
        app = QApplication.instance()
        if app is not None:
            QTimer.singleShot(300, app.quit)

    def _handle_update_download_failed(self, message: str) -> None:
        self._active_update_download_task = None
        self._close_update_progress_dialog()
        self._update_action_states()
        QMessageBox.warning(self, "Download Update", message)
        self.statusBar().showMessage("Update download failed")

    def _show_update_progress_dialog(self) -> QProgressDialog:
        return self._show_job_progress_dialog(
            key="app_update",
            total_steps=1,
            spec=JobSpec(
                title="Image Triage Update",
                preparing_label="Downloading update...",
                running_label="Downloading update...",
                window_modality=Qt.WindowModality.ApplicationModal,
            ),
        )

    def _close_update_progress_dialog(self) -> None:
        self._close_job_progress_dialog("app_update")

    def _reset_window_layout(self) -> None:
        clear_window_layout(self._settings, self.GEOMETRY_KEY, self.STATE_KEY)
        self._settings.remove(self.WORKSPACE_BAR_STATE_KEY)
        self._settings.remove(self.WORKSPACE_BAR_POSITION_KEY)
        self._toolbar.set_workspace_bar_state("expanded")
        self._toolbar.set_workspace_bar_position("top")
        self.resize(1600, 960)
        self._apply_default_workspace()
        self.statusBar().showMessage("Reset window layout")

    def _show_settings(self, initial_section: str | None = None) -> None:
        def persist_workflow_presets(presets: tuple[WorkflowPreset, ...]) -> None:
            self._workflow_presets = list(presets)
            self._save_workflow_presets()

        dialog = WorkflowSettingsDialog(
            sessions=self._decision_store.list_sessions(),
            current_session=self._session_id,
            winner_mode=self._winner_mode,
            delete_mode=self._delete_mode,
            loupe_card_style=self._effective_loupe_card_style,
            allowed_card_styles=self._allowed_card_styles(),
            ui_gamma=self._ui_gamma,
            interface_size=self._interface_size,
            free_smooth_scroll_enabled=self._free_smooth_scroll_enabled,
            preview_preload_batch_size=self._preview_preload_batch_size,
            show_hidden_folders=self._show_hidden_folders,
            single_drive_expansion_enabled=self._single_drive_expansion_enabled,
            auto_advance_enabled=self._auto_advance_enabled,
            burst_groups_enabled=self._burst_groups_enabled,
            burst_stacks_enabled=self._burst_stacks_enabled,
            catalog_cache_enabled=self._catalog_cache_enabled,
            watch_current_folder=self._watch_current_folder_enabled,
            restore_folder_position=self._restore_folder_position_enabled,
            check_updates_on_startup=self._check_updates_on_startup,
            theme=self._appearance_mode.value,
            performance_logging_enabled=self._performance_logging_enabled,
            show_ai_tags_in_grid=self._show_ai_tags_in_grid,
            apply_edits_to_pocketdrop=self._apply_edits_to_pocketdrop,
            ai_embed_batch_size=self._ai_embed_batch_size_setting,
            ai_review_detail_progress_enabled=self._ai_review_detail_progress_enabled,
            ai_dispute_weight=self._ai_dispute_weight_setting,
            ai_keep_top_percent=self._ai_keep_top_percent_setting,
            ai_review_band_percent=self._ai_review_band_percent_setting,
            ai_base_score_weight_percent=self._ai_base_score_weight_percent_setting,
            phash_prefilter_settings=self._phash_prefilter_settings,
            catalog_summary_text=self._catalog_debug_summary(include_current=True),
            presets=self._workflow_presets,
            preset_save_callback=persist_workflow_presets,
            shortcut_overrides=load_shortcut_overrides(),
            initial_section=initial_section,
            display_profile=self._display_profile or STANDARD_DISPLAY,
            parent=self,
        )
        if self._exec_dialog_with_geometry(dialog, "settings_compact") != dialog.DialogCode.Accepted:
            return

        result = dialog.result_settings()
        self._workflow_presets = list(result.presets)
        self._save_workflow_presets()
        save_shortcut_overrides(dict(result.shortcut_overrides))
        self._apply_shortcut_overrides()
        new_session = self._decision_store.ensure_session(result.session_id)
        session_changed = new_session != self._session_id
        winner_changed = result.winner_mode != self._winner_mode
        delete_changed = result.delete_mode != self._delete_mode
        # Compare against the EFFECTIVE (possibly coerced) style so that opening
        # Settings on a restricted display and clicking OK without touching the
        # card style doesn't overwrite the saved preference (which is what lets
        # it come back on a larger display).
        card_style_changed = self._normalize_loupe_card_style(result.loupe_card_style) != self._effective_loupe_card_style
        new_ui_gamma = normalize_ui_gamma(result.ui_gamma)
        ui_gamma_changed = abs(new_ui_gamma - self._ui_gamma) > 1e-3
        new_interface_size = normalize_display_profile_preference(result.interface_size)
        interface_size_changed = new_interface_size != self._interface_size
        free_scroll_changed = result.free_smooth_scroll_enabled != self._free_smooth_scroll_enabled
        new_preview_preload_batch_size = self._normalize_preview_preload_batch_size(result.preview_preload_batch_size)
        preview_preload_changed = new_preview_preload_batch_size != self._preview_preload_batch_size
        hidden_changed = result.show_hidden_folders != self._show_hidden_folders
        single_drive_changed = (
            result.single_drive_expansion_enabled
            != self._single_drive_expansion_enabled
        )
        auto_advance_changed = result.auto_advance_enabled != self._auto_advance_enabled
        burst_groups_changed = result.burst_groups_enabled != self._burst_groups_enabled
        burst_stacks_changed = result.burst_stacks_enabled != self._burst_stacks_enabled
        catalog_changed = result.catalog_cache_enabled != self._catalog_cache_enabled
        watch_changed = result.watch_current_folder != self._watch_current_folder_enabled
        update_check_changed = result.check_updates_on_startup != self._check_updates_on_startup
        ai_batch_changed = result.ai_embed_batch_size != self._ai_embed_batch_size_setting
        ai_progress_detail_changed = result.ai_review_detail_progress_enabled != self._ai_review_detail_progress_enabled
        phash_prefilter_changed = result.phash_prefilter_settings.normalized() != self._phash_prefilter_settings

        self._folder_session.session_id = new_session
        self._winner_mode = result.winner_mode
        self._delete_mode = result.delete_mode
        # Only overwrite the saved preference when the user actively changed it,
        # so a coerced style on a small display never clobbers the real choice.
        if card_style_changed:
            self._loupe_card_style = self._normalize_loupe_card_style(result.loupe_card_style)
        self._ui_gamma = new_ui_gamma
        self._interface_size = new_interface_size
        self._free_smooth_scroll_enabled = result.free_smooth_scroll_enabled
        self._preview_preload_batch_size = new_preview_preload_batch_size
        self._show_hidden_folders = result.show_hidden_folders
        self._single_drive_expansion_enabled = result.single_drive_expansion_enabled
        self._auto_advance_enabled = result.auto_advance_enabled
        self._burst_groups_enabled = result.burst_groups_enabled
        self._burst_stacks_enabled = result.burst_stacks_enabled
        self._catalog_cache_enabled = result.catalog_cache_enabled
        self._watch_current_folder_enabled = result.watch_current_folder
        self._restore_folder_position_enabled = result.restore_folder_position
        self._check_updates_on_startup = result.check_updates_on_startup
        new_theme = parse_appearance_mode(result.theme)
        if new_theme != self._appearance_mode:
            self._appearance.set_appearance_mode(new_theme)
        if result.performance_logging_enabled != self._performance_logging_enabled:
            self._handle_performance_logging_toggled(result.performance_logging_enabled)
        if result.show_ai_tags_in_grid != self._show_ai_tags_in_grid:
            self._show_ai_tags_in_grid = result.show_ai_tags_in_grid
            self._settings.setValue(self.SHOW_AI_TAGS_IN_GRID_KEY, self._show_ai_tags_in_grid)
            self.grid.set_show_ai_annotations(self._show_ai_tags_in_grid)
        if result.apply_edits_to_pocketdrop != self._apply_edits_to_pocketdrop:
            self._apply_edits_to_pocketdrop = result.apply_edits_to_pocketdrop
            self._settings.setValue(self.APPLY_EDITS_TO_POCKETDROP_KEY, self._apply_edits_to_pocketdrop)
        self._ai_embed_batch_size_setting = self._normalize_ai_embed_batch_size(result.ai_embed_batch_size)
        self._ai_dispute_weight_setting = self._normalize_ai_dispute_weight(result.ai_dispute_weight)
        self._phash_prefilter_settings = result.phash_prefilter_settings.normalized()
        new_keep_top = self._normalize_ai_keep_top_percent(result.ai_keep_top_percent)
        new_review_band = self._normalize_ai_review_band_percent(result.ai_review_band_percent)
        cull_thresholds_changed = (
            new_keep_top != self._ai_keep_top_percent_setting
            or new_review_band != self._ai_review_band_percent_setting
        )
        self._ai_keep_top_percent_setting = new_keep_top
        self._ai_review_band_percent_setting = new_review_band
        if cull_thresholds_changed:
            self._apply_cull_thresholds_to_classifier()
        new_base_weight = self._normalize_ai_base_score_weight_percent(result.ai_base_score_weight_percent)
        if new_base_weight != self._ai_base_score_weight_percent_setting:
            self._ai_base_score_weight_percent_setting = new_base_weight
            self._apply_base_score_blend_to_workflow()
        self._ai_review_detail_progress_enabled = result.ai_review_detail_progress_enabled
        self._ai_setup.refresh_ai_runtime_preferences()
        self._settings.setValue(self.SESSION_KEY, self._session_id)
        self._settings.setValue(self.WINNER_MODE_KEY, self._winner_mode.value)
        self._settings.setValue(self.DELETE_MODE_KEY, self._delete_mode.value)
        self._settings.setValue(self.LOUPE_CARD_STYLE_KEY, self._loupe_card_style)
        self._settings.setValue(self.UI_GAMMA_KEY, self._ui_gamma)
        self._settings.setValue(self.INTERFACE_SIZE_KEY, self._interface_size)
        self._settings.setValue(self.FREE_SMOOTH_SCROLL_KEY, self._free_smooth_scroll_enabled)
        self._settings.setValue(self.PREVIEW_PRELOAD_BATCH_SIZE_KEY, self._preview_preload_batch_size)
        self._settings.setValue(self.SHOW_HIDDEN_FOLDERS_KEY, self._show_hidden_folders)
        self._settings.setValue(
            self.SINGLE_DRIVE_EXPANSION_KEY,
            self._single_drive_expansion_enabled,
        )
        self._settings.setValue(self.AUTO_ADVANCE_KEY, self._auto_advance_enabled)
        self._settings.setValue(self.BURST_GROUPS_KEY, self._burst_groups_enabled)
        self._settings.setValue(self.BURST_STACKS_KEY, self._burst_stacks_enabled)
        self._settings.setValue(self.CATALOG_CACHE_ENABLED_KEY, self._catalog_cache_enabled)
        self._settings.setValue(self.CATALOG_WATCH_CURRENT_FOLDER_KEY, self._watch_current_folder_enabled)
        self._settings.setValue(self.RESTORE_FOLDER_POSITION_KEY, self._restore_folder_position_enabled)
        self._settings.setValue(self.CHECK_UPDATES_ON_STARTUP_KEY, self._check_updates_on_startup)
        self._settings.setValue(self.AI_EMBED_BATCH_SIZE_KEY, self._ai_embed_batch_size_setting)
        self._settings.setValue(self.AI_DISPUTE_WEIGHT_KEY, self._ai_dispute_weight_setting)
        self._settings.setValue(self.AI_KEEP_TOP_PERCENT_KEY, self._ai_keep_top_percent_setting)
        self._settings.setValue(self.AI_REVIEW_BAND_PERCENT_KEY, self._ai_review_band_percent_setting)
        self._settings.setValue(self.AI_BASE_SCORE_WEIGHT_PERCENT_KEY, self._ai_base_score_weight_percent_setting)
        self._save_phash_prefilter_settings(self._phash_prefilter_settings)
        self._settings.setValue(self.AI_REVIEW_DETAIL_PROGRESS_KEY, self._ai_review_detail_progress_enabled)
        self._decision_store.touch_session(self._session_id)
        self.summary_session.setText(f"Profile: {self._session_id}")
        preview = self._preview_if_built()
        if preview is not None:
            preview.set_auto_advance_enabled(self._auto_advance_enabled)
            preview.set_preload_batch_size(self._preview_preload_batch_size)
        # Apply through the resolution policy so the effective (coerced) style
        # and column thresholds land on the grid.
        self._apply_display_style_policy(show_warning=False)
        self.grid.set_free_smooth_scroll_enabled(self._free_smooth_scroll_enabled)
        if ui_gamma_changed:
            self._appearance.apply_appearance()
        if interface_size_changed:
            self._display_profile = None
            self._appearance.apply_display_profile()
        self.folder_model.setFilter(self._folder_tree_filter())
        self.folder_tree.set_single_drive_expansion_enabled(
            self._single_drive_expansion_enabled
        )
        if hidden_changed and self._current_folder and self._scope_kind == "folder":
            current_path = self._records_view.current_visible_record_path()
            self._folder_records = scan_child_folders(self._current_folder, include_hidden=self._show_hidden_folders)
            self._refresh_directory_navigation_buttons()
            self._apply_records_view(current_path=current_path)
        if card_style_changed:
            self._remember_current_folder_view_state()
        if burst_groups_changed or burst_stacks_changed:
            self._refresh_burst_group_view()
        self._refresh_current_folder_watch()
        self._refresh_catalog_status_indicator()
        self._ai_run.update_ai_toolbar_state()

        if session_changed:
            self._undo_stack.clear()
            self._update_action_states()
            self._annotations = self._decision_store.load_annotations(self._session_id, self._all_records)
            self._apply_records_view()
            self._start_scope_enrichment_task()

        if winner_changed:
            self.statusBar().showMessage(f"Winner handling set to {self._winner_mode.value}")
        elif delete_changed:
            self.statusBar().showMessage(f"Delete behavior set to {self._delete_mode.value}")
        elif session_changed:
            self.statusBar().showMessage(f"Switched to session: {self._session_id}")
        elif card_style_changed:
            label = {
                "detailed": "Detailed",
                "zen": "Zen",
                "gallery": "Gallery",
            }.get(self._loupe_card_style, self._loupe_card_style)
            self.statusBar().showMessage(f"Card style set to {label}")
        elif interface_size_changed:
            label = {
                "automatic": "Automatic",
                "compact": "Compact",
                "standard": "Comfortable",
                "spacious": "Large",
            }.get(self._interface_size, self._interface_size)
            self.statusBar().showMessage(f"Interface size set to {label}")
        elif free_scroll_changed:
            state = "enabled" if self._free_smooth_scroll_enabled else "disabled"
            self.statusBar().showMessage(f"Free smooth scrolling {state}")
        elif preview_preload_changed:
            if self._preview_preload_batch_size <= 0:
                self.statusBar().showMessage("Preview preloading disabled")
            else:
                self.statusBar().showMessage(f"Preview preload batch set to {self._preview_preload_batch_size} images")
        elif hidden_changed:
            state = "shown" if self._show_hidden_folders else "hidden"
            self.statusBar().showMessage(f"Hidden folders {state}")
        elif single_drive_changed:
            state = "enabled" if self._single_drive_expansion_enabled else "disabled"
            self.statusBar().showMessage(f"Single-branch expansion {state}")
        elif auto_advance_changed:
            state = "enabled" if self._auto_advance_enabled else "disabled"
            self.statusBar().showMessage(f"Auto-advance {state}")
        elif burst_groups_changed:
            state = "enabled" if self._burst_groups_enabled else "disabled"
            self.statusBar().showMessage(f"Smart groups {state}")
        elif burst_stacks_changed:
            state = "enabled" if self._burst_stacks_enabled else "disabled"
            self.statusBar().showMessage(f"Smart stacks {state}")
        elif catalog_changed:
            state = "enabled" if self._catalog_cache_enabled else "disabled"
            self.statusBar().showMessage(f"Catalog cache reads {state}")
        elif watch_changed:
            state = "enabled" if self._watch_current_folder_enabled else "disabled"
            self.statusBar().showMessage(f"Current-folder watch {state}")
        elif update_check_changed:
            state = "enabled" if self._check_updates_on_startup else "disabled"
            self.statusBar().showMessage(f"Startup update checks {state}")
        elif ai_batch_changed:
            self.statusBar().showMessage(f"AI embedding batch size set to {self._ai_embed_batch_size_label()}")
        elif ai_progress_detail_changed:
            state = "enabled" if self._ai_review_detail_progress_enabled else "disabled"
            self.statusBar().showMessage(f"Detailed AI Review progress {state}")
        elif phash_prefilter_changed:
            state = "enabled" if self._phash_prefilter_settings.enabled else "disabled"
            self.statusBar().showMessage(f"pHash Prefilter {state}")

    def _empty_recycle_bin(self) -> None:
        self._recycle_bin.empty_recycle_bin()

    def _open_preview_image_in_photoshop(self, path: str) -> None:
        if self._collection_mode:
            return
        if not path or not self._photoshop_executable:
            return
        open_in_photoshop(path)

    def _record_at(self, index: int) -> ImageRecord | None:
        if 0 <= index < len(self._records):
            return self._records[index]
        return None

    def _is_winners_folder(self, folder: str | None = None) -> bool:
        target = folder or self._current_folder
        return bool(target) and Path(target).name.lower() == "_winners"

    def _is_recycle_folder(self, folder: str | None = None) -> bool:
        target = folder or self._current_folder
        if not target:
            return False
        path = Path(target)
        return any(part.casefold() == "recycle bin" for part in path.parts)

    def _record_should_hint_fast_rating(self, record: ImageRecord) -> bool:
        for path in record.stack_paths:
            suffix = Path(path).suffix.lower()
            if suffix in RAW_SUFFIXES:
                return True
            try:
                if os.path.getsize(path) >= self.FAST_RATING_HINT_SIZE_BYTES:
                    return True
            except OSError:
                continue
        return record.size >= self.FAST_RATING_HINT_SIZE_BYTES

    def _maybe_show_fast_rating_hint(self, records: list[ImageRecord]) -> bool:
        if (
            self._fast_rating_hint_disabled
            or self._winner_mode != WinnerMode.COPY
            or self._session_id in self._fast_rating_hint_sessions
            or not records
        ):
            return True
        if not any(self._record_should_hint_fast_rating(record) for record in records):
            return True

        message = QMessageBox(self)
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

        self._fast_rating_hint_sessions.add(self._session_id)
        if message.clickedButton() is dont_show_button:
            self._fast_rating_hint_disabled = True
        self._save_fast_rating_hint_state()
        return True

    def _record_paths(self, record: ImageRecord) -> tuple[str, ...]:
        return record_paths(record)

    def _delete_record(self, index: int) -> None:
        self._record_ops.delete_record(index)

    def _keep_record(self, index: int) -> None:
        self._record_ops.keep_record(index)

    def _move_record_prompt(self, index: int) -> None:
        self._record_ops.move_record_prompt(index)

    def _tag_record(self, index: int) -> None:
        record = self._record_at(index)
        if record is None:
            return

        current = ", ".join(self._annotations.get(record.path, SessionAnnotation()).tags)
        value, accepted = QInputDialog.getText(
            self,
            "Tag Image",
            "Comma-separated tags",
            text=current,
        )
        if not accepted:
            return

        tags = tuple(tag.strip() for tag in value.split(",") if tag.strip())
        annotation = self._annotations.setdefault(record.path, SessionAnnotation())
        previous_annotation = self._annotation_snapshot(annotation)
        if previous_annotation.tags == tags:
            return
        annotation.tags = tags
        self._queue_annotation_persist(record, previous_annotation=previous_annotation)
        self._apply_annotation_change_effects([record.path], current_path=record.path)
        if tags:
            self.statusBar().showMessage(f"Tagged {record.name}: {', '.join(tags)}")
        else:
            self.statusBar().showMessage(f"Cleared tags for {record.name}")

    @staticmethod
    def _annotation_snapshot(annotation: SessionAnnotation) -> SessionAnnotation:
        return replace(annotation)

    def _queue_annotation_persist(
        self,
        record: ImageRecord,
        *,
        previous_annotation: SessionAnnotation | None = None,
        session_id: str | None = None,
        winner_sync: WinnerSyncRequest | None = None,
    ) -> None:
        self._records_view_cache.mark(ViewInvalidationReason.ANNOTATION_CHANGED, paths=[record.path])
        target_session = session_id or self._session_id
        annotation = self._annotations.get(record.path)
        self._annotation_persistence_queue.enqueue(
            record.path,
            annotation,
            record=record,
            session_id=target_session,
            previous_annotation=previous_annotation,
            winner_sync=winner_sync,
        )

    def _build_winner_sync_request(
        self, record: ImageRecord, winner_enabled: bool, folder: str
    ) -> WinnerSyncRequest | None:
        """The winner-copy-sync work for one annotation change, queued to run
        on the background persistence worker instead of blocking the UI."""
        if self._is_winners_folder(folder):
            return None
        return WinnerSyncRequest(
            winner_enabled=winner_enabled,
            folder=folder,
            winner_mode=self._winner_mode,
            source_paths=self._record_paths(record),
        )

    def _handle_annotation_persist_failed(self, path: str, message: str) -> None:
        rollback = self._annotation_persistence_queue.rollback(path)
        if rollback is None:
            self.statusBar().showMessage(f"Could not persist annotation for {Path(path).name or path}: {message}")
            return
        if rollback.is_empty:
            self._annotations.pop(path, None)
        else:
            self._annotations[path] = rollback
        self._apply_annotation_change_effects([path], current_path=path)
        self.statusBar().showMessage(f"Rolled back annotation for {Path(path).name or path}: {message}")

    def _handle_annotation_persist_warning(self, path: str, message: str) -> None:
        self.statusBar().showMessage(f"Saved app state for {Path(path).name or path}, but sidecar sync failed: {message}")

    def _handle_winner_sync_failed(self, path: str, message: str) -> None:
        rollback = self._annotation_persistence_queue.rollback(path)
        if rollback is None:
            self.statusBar().showMessage(f"Could not update winner copy for {Path(path).name or path}: {message}")
            return
        if rollback.is_empty:
            self._annotations.pop(path, None)
        else:
            self._annotations[path] = rollback
        self._apply_annotation_change_effects([path], current_path=path)
        self.statusBar().showMessage(f"Reverted winner state for {Path(path).name or path}: {message}")

    def _handle_winner_kept(self, path: str, kept_csv: str) -> None:
        self.statusBar().showMessage(
            f"Winner removed: {Path(path).name or path} (left {kept_csv} in _winners: not a copy Image Triage made)"
        )

    def _apply_annotation_change_effects(
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
        self._records_view_cache.mark(ViewInvalidationReason.ANNOTATION_CHANGED, paths=paths)
        if self._records_view.annotation_change_affects_active_filter():
            self._apply_records_view(current_path=current_path)
            if logger.enabled:
                logger.duration("annotation.change_effects", (time.perf_counter() - start) * 1000.0, paths=len(paths), reapply_view=True)
            return
        self._refresh_workflow_insights_cache(changed_paths=set(paths))
        self._set_annotation_views(paths)
        self.grid.update_review_workflow_insights(self._workflow_insights_by_path, paths)
        if not counts_already_updated:
            self._recalculate_review_counts()
        self._records_view.update_filter_summary()
        current_change_emitted = False
        if current_path:
            next_index = self._record_index_by_path.get(current_path)
            if next_index is not None:
                self.grid.set_current_index(next_index)
                current_change_emitted = True
        if not current_change_emitted:
            self._update_action_states()
            self._update_status()
        if logger.enabled:
            logger.duration("annotation.change_effects", (time.perf_counter() - start) * 1000.0, paths=len(paths), reapply_view=False)

    def _toggle_winner(
        self,
        index: int,
        *,
        advance_override: bool | None = None,
        current_path_override: str | None = None,
    ) -> None:
        logger = perf_logger()
        start = time.perf_counter() if logger.enabled else 0.0
        record = self._record_at(index)
        if record is None:
            return
        if not self._current_folder:
            self.statusBar().showMessage("Winner/reject actions stay folder-first. Open the source folder to change those states.")
            return
        if self._is_recycle_folder():
            self._record_ops.restore_record(index)
            return
        if self._is_winners_folder():
            self._delete_record(index)
            self.statusBar().showMessage(f"Removed winner copy: {record.name}")
            return

        should_advance = self._auto_advance_enabled if advance_override is None else advance_override
        next_path = self._next_visible_path(index) if should_advance else record.path
        if current_path_override is not None:
            next_path = current_path_override
        annotation = self._annotations.setdefault(record.path, SessionAnnotation())
        if not annotation.winner and not self._maybe_show_fast_rating_hint([record]):
            return
        previous_annotation = self._annotation_snapshot(annotation)
        previous_winner = annotation.winner
        previous_reject = annotation.reject
        previous_photoshop = annotation.photoshop
        annotation.winner = not annotation.winner
        if annotation.winner:
            annotation.reject = False

        winner_sync = self._build_winner_sync_request(record, annotation.winner, self._current_folder)

        self._record_ops.push_undo(
            UndoAction(
                kind="annotation",
                primary_path=record.path,
                original_winner=previous_winner,
                original_reject=previous_reject,
                original_photoshop=previous_photoshop,
                rating=annotation.rating,
                tags=annotation.tags,
                original_review_round=previous_annotation.review_round,
                folder=self._current_folder,
                source_paths=self._record_paths(record),
                session_id=self._session_id,
                winner_mode=self._winner_mode.value,
            )
        )
        self._queue_annotation_persist(record, previous_annotation=previous_annotation, winner_sync=winner_sync)
        self._aiculler.sync_annotation_to_global_adapter_label(record, annotation)
        self._capture_annotation_feedback(record, previous_annotation, annotation, source_mode="winner_toggle")
        self._apply_review_count_delta(previous_annotation, annotation)
        self._apply_annotation_change_effects([record.path], current_path=next_path, counts_already_updated=True)
        if annotation.winner:
            self.statusBar().showMessage(f"Winner added: {record.name}")
        else:
            self.statusBar().showMessage(f"Winner removed: {record.name}")
        if logger.enabled:
            logger.duration("annotation.winner_toggle", (time.perf_counter() - start) * 1000.0, path=record.path, winner=annotation.winner, advance=should_advance)

    def _toggle_reject(
        self,
        index: int,
        *,
        advance_override: bool | None = None,
        current_path_override: str | None = None,
    ) -> None:
        logger = perf_logger()
        start = time.perf_counter() if logger.enabled else 0.0
        record = self._record_at(index)
        if record is None:
            return
        if not self._current_folder:
            self.statusBar().showMessage("Winner/reject actions stay folder-first. Open the source folder to change those states.")
            return

        should_advance = self._auto_advance_enabled if advance_override is None else advance_override
        next_path = self._next_visible_path(index) if should_advance else record.path
        if current_path_override is not None:
            next_path = current_path_override

        annotation = self._annotations.setdefault(record.path, SessionAnnotation())
        if not annotation.reject and not self._maybe_show_fast_rating_hint([record]):
            return
        previous_annotation = self._annotation_snapshot(annotation)
        previous_winner = annotation.winner
        previous_reject = annotation.reject
        previous_photoshop = annotation.photoshop
        annotation.reject = not annotation.reject
        if annotation.reject:
            annotation.winner = False

        winner_sync = (
            self._build_winner_sync_request(record, annotation.winner, self._current_folder)
            if previous_winner != annotation.winner
            else None
        )

        self._record_ops.push_undo(
            UndoAction(
                kind="annotation",
                primary_path=record.path,
                original_winner=previous_winner,
                original_reject=previous_reject,
                original_photoshop=previous_photoshop,
                rating=annotation.rating,
                tags=annotation.tags,
                original_review_round=previous_annotation.review_round,
                folder=self._current_folder,
                source_paths=self._record_paths(record),
                session_id=self._session_id,
                winner_mode=self._winner_mode.value,
            )
        )
        self._queue_annotation_persist(record, previous_annotation=previous_annotation, winner_sync=winner_sync)
        self._aiculler.sync_annotation_to_global_adapter_label(record, annotation)
        self._capture_annotation_feedback(record, previous_annotation, annotation, source_mode="reject_toggle")
        self._apply_review_count_delta(previous_annotation, annotation)
        self._apply_annotation_change_effects([record.path], current_path=next_path, counts_already_updated=True)
        if annotation.reject:
            self.statusBar().showMessage(f"Rejected: {record.name}")
        else:
            self.statusBar().showMessage(f"Reject removed: {record.name}")
        if logger.enabled:
            logger.duration("annotation.reject_toggle", (time.perf_counter() - start) * 1000.0, path=record.path, reject=annotation.reject, advance=should_advance)

    def _open_preview(self, index: int, *, lightweight_grid_sync: bool = False) -> None:
        logger = perf_logger()
        start = time.perf_counter() if logger.enabled else 0.0
        if not self._preview_is_visible():
            self._preview_navigation_dirty = False
        if self._winner_ladder_state is not None:
            self._finish_winner_ladder(reopen_preview=False, show_message=False)
        record = self._record_at(index)
        if record is not None and record.is_folder:
            self._select_folder(record.path)
            return
        entries_start = time.perf_counter() if logger.enabled else 0.0
        entries, effective_count, anchor_index = self._preview_entries_for(index)
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
        if self._compare_enabled:
            self._compare_count = effective_count
            self.preview.set_compare_count(effective_count)
            if anchor_index != index:
                if lightweight_grid_sync:
                    self.grid.set_logical_selection([anchor_index], current_index=anchor_index)
                else:
                    self.grid.set_current_index(anchor_index)
        self.preview.set_winner_ladder_mode(False)
        if logger.enabled:
            logger.duration(
                "window.open_preview.controls",
                (time.perf_counter() - controls_start) * 1000.0,
                compare=self._compare_enabled,
            )
        # The full-screen editor needs the GPU for masking (SAM / OneFormer /
        # BiRefNet); pause background indexing so it isn't fighting for CUDA.
        self._records_view.suspend_background_indexing()
        self.preview.show_entries(entries)
        self._sync_preview_browse_context(anchor_index if anchor_index >= 0 else index)
        self._schedule_preview_preload(anchor_index if anchor_index >= 0 else index)
        if logger.enabled:
            logger.duration(
                "window.open_preview",
                (time.perf_counter() - start) * 1000.0,
                index=index,
                entry_count=len(entries),
                effective_count=effective_count,
                anchor_index=anchor_index,
                compare=self._compare_enabled,
            )

    def _navigate_preview(self, delta: int) -> None:
        logger = perf_logger()
        start = time.perf_counter() if logger.enabled else 0.0
        if not self._records:
            return

        current = self.grid.current_index()
        if current < 0:
            current = 0
        next_index = max(0, min(len(self._records) - 1, current + delta))
        if next_index != current:
            self.grid.set_logical_selection([next_index], current_index=next_index)
            self._preview_navigation_dirty = True
        self._open_preview(next_index, lightweight_grid_sync=True)
        if logger.enabled:
            record = self._record_at(next_index)
            logger.duration(
                "preview.navigation",
                (time.perf_counter() - start) * 1000.0,
                delta=delta,
                previous_index=current,
                current_index=next_index,
                clamped=next_index == current,
                path=record.path if record is not None else "",
            )

    PREVIEW_FILMSTRIP_THUMB_SIZE = QSize(192, 128)

    def _sync_preview_browse_context(self, current_index: int) -> None:
        """Feed the popout's filmstrip/nav pill its position in the record list."""
        logger = perf_logger()
        start = time.perf_counter() if logger.enabled else 0.0
        self.preview.set_browse_context(
            len(self._records),
            current_index,
            self._preview_filmstrip_thumb,
            self._preview_filmstrip_tag,
        )
        if logger.enabled:
            logger.duration(
                "preview.browse_context",
                (time.perf_counter() - start) * 1000.0,
                current_index=current_index,
                total=len(self._records),
            )

    def _preview_filmstrip_thumb(self, index: int) -> QPixmap | None:
        logger = perf_logger()
        start = time.perf_counter() if logger.enabled else 0.0
        record = self._record_at(index)
        if record is None or record.is_folder:
            if logger.enabled:
                logger.duration(
                    "preview.filmstrip_thumbnail",
                    (time.perf_counter() - start) * 1000.0,
                    index=index,
                    state="invalid_record",
                )
            return None
        image = self.thumbnail_manager.get_cached(record, self.PREVIEW_FILMSTRIP_THUMB_SIZE)
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
        self.thumbnail_manager.request_thumbnail(
            record,
            self.PREVIEW_FILMSTRIP_THUMB_SIZE,
            priority=25_000,
            drop_if_not_wanted=False,
        )
        fallback = self.grid.thumbnail_for(index)
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

    def _preview_filmstrip_tag(self, index: int) -> str | None:
        """Status dot for a filmstrip thumb: manual review tags first
        (reject red, winner green), then blue for an untouched AI top pick."""
        record = self._record_at(index)
        if record is None:
            return None
        annotation = self._annotations.get(record.path)
        if annotation is not None:
            if annotation.reject:
                return preview_studio.REJECT
            if annotation.winner:
                return "#ee719e"
        result = self._ai_run.ai_result_for_record(record)
        if result is not None and result.is_top_pick:
            return preview_studio.INFO
        return None

    def _handle_preview_filmstrip_thumbnail_ready(self, *_args) -> None:
        if self._preview_is_visible():
            logger = perf_logger()
            if logger.enabled:
                logger.log("preview.filmstrip_refresh_requested")
            self.preview.refresh_filmstrip()

    def _preview_source_path(self, record: ImageRecord) -> str:
        return record.path

    def _displayed_preview_source_path(self, index: int, record: ImageRecord) -> str:
        override = self._quick_view_source_overrides.get(record.path, "")
        if override:
            return override
        if record.has_variant_stack:
            return self.grid.displayed_variant_path(index)
        return self._preview_source_path(record)

    def _preview_entries_for(self, index: int) -> tuple[list[PreviewEntry], int, int]:
        record = self._record_at(index)
        if record is None:
            return [], self._compare_count, index
        annotation = self._annotations.get(record.path, SessionAnnotation())
        displayed_path = self._displayed_preview_source_path(index, record)
        edited_candidates = self._ordered_edited_candidates(record, displayed_path)
        edited_path = edited_candidates[0] if edited_candidates else ""
        if not self._compare_enabled:
            return ([
                PreviewEntry(
                    record=record,
                    source_path=displayed_path,
                    winner=annotation.winner,
                    reject=annotation.reject,
                    rating=annotation.rating,
                    edited_path=edited_path,
                    edited_candidates=tuple(edited_candidates),
                    ai_result=self._ai_run.ai_result_for_record(record, preferred_path=displayed_path),
                    review_summary=self._review_summary_for_record(record),
                    workflow_summary=self._workflow_summary_for_record(record),
                    workflow_details=self._workflow_detail_lines_for_record(record),
                    placeholder_image=self._preview_placeholder_for_index(index),
                )
            ], 1, index)

        group = self._bracket_detector.group_for(self._records, index) if self._auto_bracket_enabled else None
        effective_count = self._manual_compare_count
        start = index
        if group is not None and group.size >= 2:
            start = group.start_index
            effective_count = group.size

        end = min(len(self._records), start + max(1, effective_count))
        entries: list[PreviewEntry] = []
        for item_index, record in enumerate(self._records[start:end], start=start):
            annotation = self._annotations.get(record.path, SessionAnnotation())
            displayed_path = self._displayed_preview_source_path(item_index, record)
            edited_candidates = self._ordered_edited_candidates(record, displayed_path)
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
                    ai_result=self._ai_run.ai_result_for_record(record, preferred_path=displayed_path),
                    review_summary=self._review_summary_for_record(record),
                    workflow_summary=self._workflow_summary_for_record(record),
                    workflow_details=self._workflow_detail_lines_for_record(record),
                    placeholder_image=self._preview_placeholder_for_index(item_index),
                )
            )
        return entries, max(1, len(entries)), start

    def _ordered_edited_candidates(self, record: ImageRecord, displayed_path: str) -> tuple[str, ...]:
        if record.edited_paths:
            edited_candidates = tuple(record.edited_paths)
            self._edited_candidates_cache[record.path] = edited_candidates
        else:
            edited_candidates = self._edited_candidates_cache.get(record.path, ())
        if displayed_path and displayed_path in edited_candidates:
            return (displayed_path, *[path for path in edited_candidates if path != displayed_path])
        return tuple(edited_candidates)

    def _record_index_for_path(self, path: str) -> int | None:
        return self._record_index_by_path.get(path)

    def _selected_records_for_context(self, index: int) -> list[ImageRecord]:
        selected_indexes = self.grid.selected_indexes()
        if index not in selected_indexes:
            selected_indexes = [index]
        return [
            self._records[item_index]
            for item_index in selected_indexes
            if 0 <= item_index < len(self._records) and not self._records[item_index].is_folder
        ]

    def _batch_set_winner(self, records: list[ImageRecord]) -> None:
        if not records:
            return
        if self._is_winners_folder():
            self._record_ops.batch_delete_records(records)
            return
        candidates = [record for record in records if not self._annotations.get(record.path, SessionAnnotation()).winner]
        if not self._maybe_show_fast_rating_hint(candidates):
            return
        changed, failures = self._batch_apply_annotation_state(records, winner=True, reject=False, source_mode="winner_toggle")
        if failures:
            self.statusBar().showMessage(f"Marked {changed} winner image(s); {failures} failed to sync winner artifacts")
            return
        self.statusBar().showMessage(f"Marked {changed} winner image(s)")

    def _batch_set_reject(self, records: list[ImageRecord]) -> None:
        if not records:
            return
        candidates = [record for record in records if not self._annotations.get(record.path, SessionAnnotation()).reject]
        if not self._maybe_show_fast_rating_hint(candidates):
            return
        changed, failures = self._batch_apply_annotation_state(records, winner=False, reject=True, source_mode="reject_toggle")
        if failures:
            self.statusBar().showMessage(f"Rejected {changed} image(s); {failures} failed to update")
            return
        self.statusBar().showMessage(f"Rejected {changed} image(s)")

    def _batch_apply_annotation_state(
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
        current_path = self._records_view.current_visible_record_path() or records[0].path

        for record in records:
            annotation = self._annotations.setdefault(record.path, SessionAnnotation())
            previous_annotation = self._annotation_snapshot(annotation)
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
                self._build_winner_sync_request(record, annotation.winner, self._current_folder)
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
                    folder=self._current_folder,
                    source_paths=self._record_paths(record),
                    session_id=self._session_id,
                    winner_mode=self._winner_mode.value,
                )
            )
            self._queue_annotation_persist(record, previous_annotation=previous_annotation, winner_sync=winner_sync)
            self._aiculler.sync_annotation_to_global_adapter_label(record, annotation)
            self._capture_annotation_feedback(record, previous_annotation, annotation, source_mode=source_mode)
            self._apply_review_count_delta(previous_annotation, annotation)
            changed_paths.append(record.path)

        if undo_actions:
            self._record_ops.push_undo_actions(undo_actions)
        if changed_paths:
            self._apply_annotation_change_effects(changed_paths, current_path=current_path, counts_already_updated=True)
        return len(changed_paths), failures

    def _batch_keep_records(self, records: list[ImageRecord]) -> None:
        if not records:
            return
        moved = sum(1 for record in records if self._record_ops.keep_record_by_path(record.path))
        self.statusBar().showMessage(f"Moved {moved} image(s) to _keep")

    @staticmethod
    def _primary_paths_for_records(records: list[ImageRecord]) -> list[str]:
        seen: set[str] = set()
        ordered: list[str] = []
        for record in records:
            key = normalized_path_key(record.path)
            if key in seen:
                continue
            seen.add(key)
            ordered.append(record.path)
        return ordered

    def _copy_records_by_paths(self, primary_paths: list[str], destination_dir: str) -> int:
        return self._record_ops.copy_records_by_paths(primary_paths, destination_dir)

    def _move_records_by_paths(self, primary_paths: list[str], destination_dir: str, *, batch_id: str = "") -> int:
        return self._record_ops.move_records_by_paths(primary_paths, destination_dir, batch_id=batch_id)

    def _batch_restore_records(self, records: list[ImageRecord]) -> None:
        if not records:
            return
        restored = sum(1 for record in records if self._record_ops.restore_record_by_path(record.path))
        self.statusBar().showMessage(f"Restored {restored} image(s)")

    def _batch_open_in_photoshop(self, records: list[ImageRecord]) -> None:
        if not self._photoshop_executable or not records:
            return
        for record in records:
            open_in_photoshop(record.path)
        self.statusBar().showMessage(f"Opened {len(records)} image(s) in Photoshop")

    def _move_selected_records_to_destination(self, destination_dir: str) -> None:
        self._record_ops.move_selected_records_to_destination(destination_dir)

    def _dispatch_preview_action(self, path: str, handler, *, preserve_anchor: bool = True) -> None:
        if self._collection_mode:
            return
        index = self._record_index_for_path(path)
        if index is None:
            return
        anchor_path = self.preview.anchor_path() if preserve_anchor else ""
        handler(index)
        if not self.preview.isVisible():
            return
        reopen_index = None
        if anchor_path:
            reopen_index = self._record_index_for_path(anchor_path)
        if reopen_index is None:
            next_index = self.grid.current_index()
            if 0 <= next_index < len(self._records):
                reopen_index = next_index
        if reopen_index is not None:
            self._open_preview(reopen_index)
            return
        self.preview.close()

    def _record_from_path(self, path: str) -> ImageRecord | None:
        existing = self._all_records_by_path.get(path)
        if existing is not None:
            return existing
        if not os.path.exists(path):
            return None
        stat_result = os.stat(path)
        return ImageRecord(
            path=path,
            name=Path(path).name,
            size=stat_result.st_size,
            modified_ns=getattr(stat_result, "st_mtime_ns", int(stat_result.st_mtime * 1_000_000_000)),
        )

    def _record_after_moves(self, record: ImageRecord, moves: tuple[FileMove, ...]) -> ImageRecord:
        return self._record_ops.record_after_moves(record, moves)

    def _show_grid_context_menu(self, index: int, global_pos) -> None:
        if index < 0:
            self._build_empty_grid_context_menu().exec(global_pos)
            return
        current_record = self._record_at(index)
        if current_record is not None and current_record.is_folder:
            menu = QMenu(self)
            open_action = menu.addAction(self._menu_text_with_hint("Open", "Space / Enter"))
            open_file_manager_label = "Open In File Explorer" if os.name == "nt" else "Open In File Manager"
            open_file_manager_action = menu.addAction(open_file_manager_label)
            reveal_label = "Reveal In File Explorer" if os.name == "nt" else "Reveal In File Manager"
            reveal_action = menu.addAction(reveal_label)
            menu.addSeparator()
            copy_path_action = menu.addAction("Copy Path")
            copy_name_action = menu.addAction("Copy Folder Name")

            chosen = menu.exec(global_pos)
            if chosen is None:
                return
            if chosen == open_action:
                self._select_folder(current_record.path)
                return
            if chosen == open_file_manager_action:
                open_in_file_explorer(current_record.path)
                return
            if chosen == reveal_action:
                reveal_in_file_explorer(current_record.path)
                return
            if chosen == copy_path_action:
                QApplication.clipboard().setText(current_record.path)
                return
            if chosen == copy_name_action:
                QApplication.clipboard().setText(current_record.name)
                return

        records = self._selected_records_for_context(index)
        if not records:
            return

        if len(records) > 1:
            menu = QMenu(self)
            restore_action = None
            accept_action = None
            reject_action = None
            keep_action = None
            photoshop_action = None
            if self._is_recycle_folder():
                restore_action = menu.addAction(f"Restore {len(records)} Images")
                menu.addSeparator()
            else:
                accept_action = menu.addAction(f"Mark {len(records)} Images As Winners")
                reject_action = menu.addAction(f"Reject {len(records)} Images")
                keep_action = menu.addAction(f"Move {len(records)} Images To _keep")
                menu.addSeparator()
            photoshop_action = menu.addAction(f"Open {len(records)} Images In Photoshop")
            photoshop_action.setEnabled(bool(self._photoshop_executable))
            if not self._photoshop_executable:
                photoshop_action.setText("Open In Photoshop (Not Found)")
            menu.addSeparator()
            send_to_actions = self._add_send_to_actions(menu)
            menu.addSeparator()
            delete_action = menu.addAction(f"Delete {len(records)} Images")

            chosen = menu.exec(global_pos)
            if chosen is None:
                return
            if restore_action is not None and chosen == restore_action:
                self._batch_restore_records(records)
                return
            if accept_action is not None and chosen == accept_action:
                self._batch_set_winner(records)
                return
            if reject_action is not None and chosen == reject_action:
                self._batch_set_reject(records)
                return
            if keep_action is not None and chosen == keep_action:
                self._batch_keep_records(records)
                return
            if chosen == photoshop_action:
                self._batch_open_in_photoshop(records)
                return
            if chosen == send_to_actions["copy_file_action"]:
                self._copy_records_to_clipboard(records)
                return
            if chosen == send_to_actions["copy_action"]:
                self._record_ops.batch_copy_records(records)
                return
            if chosen in send_to_actions["copy_recent_actions"]:
                self._record_ops.copy_selected_records_to_destination(send_to_actions["copy_recent_actions"][chosen])
                return
            if chosen == send_to_actions["move_action"]:
                self._record_ops.batch_move_records(records)
                return
            if chosen == send_to_actions["move_new_folder_action"]:
                self._record_ops.batch_move_records_to_new_folder(records)
                return
            if chosen in send_to_actions["move_recent_actions"]:
                self._move_selected_records_to_destination(send_to_actions["move_recent_actions"][chosen])
                return
            if chosen == send_to_actions["zip_action"]:
                self._create_archive_for_records(records, "zip")
                return
            if chosen == send_to_actions["seven_zip_action"]:
                self._create_archive_for_records(records, "7z")
                return
            if chosen == send_to_actions["tar_gz_action"]:
                self._create_archive_for_records(records, "tar_gz")
                return
            if chosen == delete_action:
                self._record_ops.batch_delete_records(records)
                return
            return

        record = records[0]
        display_path = self.grid.displayed_variant_path(index) or record.path
        display_name = Path(display_path).name

        menu = QMenu(self)
        restore_action = None
        open_action = menu.addAction(self._menu_text_with_hint("Open", "Space / Enter"))
        open_with_menu = menu.addMenu("Open With")
        default_action = open_with_menu.addAction("Default App")
        open_with_action = open_with_menu.addAction("System Open With...")
        reveal_label = "Reveal In File Explorer" if os.name == "nt" else "Reveal In File Manager"
        reveal_action = menu.addAction(reveal_label)
        photoshop_action = menu.addAction("Open In Photoshop")
        photoshop_action.setEnabled(bool(self._photoshop_executable))
        if not self._photoshop_executable:
            photoshop_action.setText("Open In Photoshop (Not Found)")
        ai_result = self._ai_run.ai_result_for_index(index)
        compare_ai_group_action = None
        jump_ai_pick_action = None
        if ai_result is not None:
            menu.addSeparator()
            if ai_result.group_size > 1:
                compare_ai_group_action = menu.addAction(
                    self._toolbar_menus.menu_text_with_action_shortcut("Compare AI Group", self.actions.compare_ai_group if self.actions else None)
                )
                jump_ai_pick_action = menu.addAction(
                    self._toolbar_menus.menu_text_with_action_shortcut("Jump To AI Top Pick", self.actions.next_ai_pick if self.actions else None)
                )
        if self._is_recycle_folder():
            menu.addSeparator()
            restore_action = menu.addAction("Restore")
        else:
            menu.addSeparator()
        rename_action = menu.addAction(
            self._toolbar_menus.menu_text_with_action_shortcut("Rename...", self.actions.rename_selection if self.actions else None)
        )
        rename_action.setEnabled(not self._is_recycle_folder() and not self._is_winners_folder())
        resize_action = menu.addAction("Resize...")
        resize_action.setEnabled(not self._is_recycle_folder() and self._record_supports_resize(record))
        convert_action = menu.addAction("Convert...")
        convert_action.setEnabled(not self._is_recycle_folder() and self._record_supports_convert(record))
        menu.addSeparator()
        send_to_actions = self._add_send_to_actions(menu)
        menu.addSeparator()
        copy_path_action = menu.addAction("Copy Path")
        copy_name_action = menu.addAction("Copy Filename")
        menu.addSeparator()
        delete_action = menu.addAction(self._menu_text_with_hint("Delete", "Del"))

        chosen = menu.exec(global_pos)
        if chosen is None:
            return
        if chosen == open_action:
            open_with_default(display_path)
            return
        if compare_ai_group_action is not None and chosen == compare_ai_group_action:
            self._ai_run.open_current_ai_group_compare(index)
            return
        if jump_ai_pick_action is not None and chosen == jump_ai_pick_action:
            self._ai_run.jump_to_ai_top_pick_in_group(index)
            return
        if restore_action is not None and chosen == restore_action:
            self._record_ops.restore_record(index)
            return
        if chosen == rename_action:
            self._record_ops.rename_record_prompt(index)
            return
        if chosen == resize_action:
            self._resize_record_prompt(index)
            return
        if chosen == convert_action:
            self._convert_record_prompt(index)
            return
        if chosen == photoshop_action and self._photoshop_executable:
            open_in_photoshop(display_path)
            return
        if chosen == send_to_actions["copy_file_action"]:
            self._copy_records_to_clipboard(records, display_path=display_path)
            return
        if chosen == send_to_actions["copy_action"]:
            destination_dir = QFileDialog.getExistingDirectory(self, "Copy Image", self._current_folder or QDir.homePath())
            if destination_dir:
                self._record_ops.copy_record_to(index, destination_dir)
            return
        if chosen in send_to_actions["copy_recent_actions"]:
            self._record_ops.copy_record_to(index, send_to_actions["copy_recent_actions"][chosen])
            return
        if chosen == send_to_actions["move_action"]:
            self._move_record_prompt(index)
            return
        if chosen == send_to_actions["move_new_folder_action"]:
            self._record_ops.batch_move_records_to_new_folder(records)
            return
        if chosen in send_to_actions["move_recent_actions"]:
            self._record_ops.move_record_to(index, send_to_actions["move_recent_actions"][chosen])
            return
        if chosen == send_to_actions["zip_action"]:
            self._create_archive_for_records(records, "zip")
            return
        if chosen == send_to_actions["seven_zip_action"]:
            self._create_archive_for_records(records, "7z")
            return
        if chosen == send_to_actions["tar_gz_action"]:
            self._create_archive_for_records(records, "tar_gz")
            return
        if chosen == delete_action:
            self._delete_record(index)
            return
        if chosen == reveal_action:
            reveal_in_file_explorer(display_path)
            return
        if chosen == copy_path_action:
            QApplication.clipboard().setText(display_path)
            return
        if chosen == copy_name_action:
            QApplication.clipboard().setText(display_name)
            return
        if chosen == default_action:
            open_with_default(display_path)
            return
        if chosen == open_with_action:
            open_with_dialog(display_path)
            return

    def _build_empty_grid_context_menu(self) -> QMenu:
        menu = QMenu(self)
        menu.setObjectName("emptyGridContextMenu")

        menu.addAction(self.actions.clear_filters)
        clear_search = menu.addAction("Clear Search")
        clear_search.setEnabled(
            bool(self._filter_query.search_text.strip() or self._pending_search_text.strip())
        )
        clear_search.triggered.connect(self._clear_search_from_workspace_menu)

        menu.addSeparator()
        menu.addAction(self.actions.refresh_folder)

        menu.addSeparator()
        select_all = menu.addAction("Select All")
        select_all.setShortcut(QKeySequence.StandardKey.SelectAll)
        select_all.setShortcutVisibleInContextMenu(True)
        visible_count = self.grid.visible_item_count()
        selected_count = self.grid.selected_count()
        select_all.setEnabled(visible_count > 0 and selected_count < visible_count)
        select_all.triggered.connect(self.grid.select_all)

        deselect_all = menu.addAction("Deselect All")
        deselect_all.setEnabled(selected_count > 0)
        deselect_all.triggered.connect(lambda: self.grid.clear_selection(keep_current=True))

        menu.addSeparator()
        view_menu = QMenu("View", menu)
        menu.addMenu(view_menu)
        show_filenames = view_menu.addAction("Show Filenames")
        show_filenames.setCheckable(True)
        show_filenames.setChecked(self._effective_loupe_card_style != "zen")
        show_filenames.setEnabled(
            self._browser_view_mode == "grid" and "gallery" in self._allowed_card_styles()
        )
        show_filenames.toggled.connect(self._set_grid_filenames_visible)
        view_menu.addSeparator()
        view_menu.addAction(self.actions.grid_view)
        view_menu.addAction(self.actions.details_view)

        sort_menu = QMenu("Sort By", menu)
        menu.addMenu(sort_menu)
        sort_group = QActionGroup(sort_menu)
        sort_group.setExclusive(True)
        for mode in SortMode:
            sort_action = sort_menu.addAction(mode.value)
            sort_action.setCheckable(True)
            sort_group.addAction(sort_action)
            sort_action.setChecked(self._sort_mode == mode)
            if mode == SortMode.AI_RANK:
                sort_action.setEnabled(self._ai_bundle is not None)
            elif mode == SortMode.AI_WOW:
                sort_action.setEnabled(bool(self._winner_scores_by_path))
            sort_action.triggered.connect(
                lambda _checked=False, selected=mode: self._set_sort_mode(selected)
            )

        menu.addSeparator()
        open_folder_label = "Open Current Folder In File Explorer" if os.name == "nt" else "Open Current Folder In File Manager"
        open_folder = menu.addAction(open_folder_label)
        open_folder.setEnabled(bool(self._current_folder and not self._dir_confirmed_missing(self._current_folder)))
        open_folder.triggered.connect(self._open_current_folder_in_file_manager)
        return menu

    def _clear_search_from_workspace_menu(self) -> None:
        self._records_view.clear_search_from_workspace_menu()

    def _open_current_folder_in_file_manager(self) -> None:
        if self._current_folder and not self._dir_confirmed_missing(self._current_folder):
            open_in_file_explorer(self._current_folder)

    def _unique_destination(self, directory: str, filename: str) -> str:
        return unique_destination(directory, filename)

    def _undo_last_action(self) -> None:
        self._record_ops.undo_last_action()

    def _next_visible_path(self, index: int) -> str | None:
        if not self._records:
            return None
        if index + 1 < len(self._records):
            return self._records[index + 1].path
        if index > 0:
            return self._records[index - 1].path
        return self._records[index].path

    def _apply_records_view(
        self,
        current_path: str | None = None,
        *,
        chunked: bool = False,
        post_load_enrichment: str = "",
    ) -> bool:
        return self._records_view.apply_records_view(
            current_path,
            chunked=chunked,
            post_load_enrichment=post_load_enrichment,
        )

    def _refresh_burst_group_view(self, *, request_thumbnails: bool = True) -> None:
        burst_groups: list[tuple[int, ...]] = []
        burst_group_map: dict[str, BurstVisualInfo] = {}
        if (self._burst_groups_enabled or self._burst_stacks_enabled) and self._records:
            if self._review_intelligence is not None:
                visible_groups_by_id: dict[str, list[int]] = {}
                visible_label_by_id: dict[str, tuple[str, str]] = {}
                for record_index, record in enumerate(self._records):
                    insight = self._review_insight_for_record(record)
                    if insight is None or not insight.has_group:
                        continue
                    visible_groups_by_id.setdefault(insight.group_id, []).append(record_index)
                    visible_label_by_id.setdefault(insight.group_id, (insight.group_label, insight.group_kind))
                burst_groups = [tuple(indexes) for indexes in visible_groups_by_id.values() if len(indexes) >= 2]
                burst_groups.sort(key=lambda members: members[0])
                for group_number, group in enumerate(burst_groups, start=1):
                    insight = self._review_insight_for_record(self._records[group[0]])
                    label, kind = visible_label_by_id.get(
                        insight.group_id if insight is not None else "",
                        ("Group", "similar"),
                    )
                    for index_in_group, record_index in enumerate(group, start=1):
                        if not 0 <= record_index < len(self._records):
                            continue
                        burst_group_map[self._records[record_index].path] = BurstVisualInfo(
                            group_number=group_number,
                            index_in_group=index_in_group,
                            group_size=len(group),
                            label=label,
                            kind=kind,
                        )
            else:
                burst_groups = find_burst_groups(self._records, self._filter_metadata_by_path)
                for group_number, group in enumerate(burst_groups, start=1):
                    for index_in_group, record_index in enumerate(group, start=1):
                        if not 0 <= record_index < len(self._records):
                            continue
                        burst_group_map[self._records[record_index].path] = BurstVisualInfo(
                            group_number=group_number,
                            index_in_group=index_in_group,
                            group_size=len(group),
                            label="Burst",
                            kind="burst",
                        )
        self._visible_burst_groups = burst_groups
        self.grid.set_burst_groups(burst_group_map, burst_groups, request_thumbnails=request_thumbnails)
        self.grid.set_burst_stack_mode(self._burst_stacks_enabled, request_thumbnails=request_thumbnails)
        self._records_view.update_filter_summary()

    def _trash_or_delete_paths(self, source_paths: tuple[str, ...]) -> bool:
        return self._record_ops.trash_or_delete_paths(source_paths)

