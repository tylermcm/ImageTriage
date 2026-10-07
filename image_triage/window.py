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
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from queue import Empty, SimpleQueue

import numpy as np
from PySide6.QtCore import (
    QByteArray,
    QDir,
    QEasingCurve,
    QEvent,
    QFileSystemWatcher,
    QMimeData,
    QModelIndex,
    QPoint,
    QPropertyAnimation,
    QRect,
    QSignalBlocker,
    QSize,
    QStandardPaths,
    Qt,
    QThreadPool,
    QTimer,
    QUrl,
    Slot,
)
from PySide6.QtGui import (
    QAction,
    QActionGroup,
    QColor,
    QCloseEvent,
    QFont,
    QGuiApplication,
    QIcon,
    QImage,
    QKeySequence,
    QPainter,
    QPixmap,
    QShortcut,
    QTransform,
)
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QFileDialog,
    QFileSystemModel,
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
    QScrollArea,
    QSizePolicy,
    QSlider,
    QStackedWidget,
    QStatusBar,
    QToolButton,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from aiculler.telemetry import TelemetryEvent, ThreadedTelemetryLogger

from .ai_model import AIModelInstallation, DEFAULT_AICULLER_CLIP_VARIANT
from .ai_runtime_packages import load_ai_runtime_installation_status
from .annotation_queue import AnnotationPersistenceQueue
from .app_identity import migrate_legacy_settings_once, user_settings
from .ai_training import normalize_ranker_profile, suggest_training_profile
from .ai_workflow import ai_report_artifacts_ready, ai_semantic_artifacts_ready, build_ai_workflow_paths, default_ai_workflow_runtime
from .ai_workflow_center import AIWorkflowCenterDialog
from .aiculler_workflow import AICullerRunTask, aiculler_db_path
from .ai_results import AIBundle, AIConfidenceBucket, load_ai_bundle
from .batch_rename_controller import BatchRenameApplyController, BatchRenameExecutionContext
from .catalog_controller import CatalogController, CatalogExecutionContext
from .command_palette_controller import CommandPaletteController
from .folder_ops_controller import FolderOpsController
from .folder_session import FolderSession, session_field
from .handoff_controller import HandoffController
from .projects_controller import ProjectsController
from .view_controller import ViewController
from .display_controller import DisplayController
from .startup_controller import StartupController
from .dragdrop_controller import DragDropController
from .context_menu_controller import ContextMenuController
from .inspector_controller import InspectorController
from .scan_controller import ScanController
from .settings_controller import SettingsController
from .navigation_controller import NavigationController
from .annotation_controller import AnnotationController
from .preview_controller import PreviewController
from .help_update_controller import HelpUpdateController
from .export_jobs_controller import ExportJobsController
from .appearance_controller import AppearanceController
from .toolbar_controller import ToolbarController
from .zen_controller import ZenController
from .tool_mode_controller import ToolModeController
from .ai_run_controller import AiRunController
from .aiculler_controller import AiCullerController
from .ai_setup_controller import AiSetupController
from .record_ops_controller import RecordOpsController, UndoAction
from .records_repository import RecordsRepository
from .records_view_controller import RecordsViewController, _memory_path_key
from .recycle_bin_controller import RecycleBinController
from .catalog import CatalogRepository, catalog_cache_env_override
from .decision_store import DecisionStore
from .details_view import PhotoDetailsView
from .prefilter_common import PrefilterDecision
from .phash_prefilter import PHashPrefilterSettings, build_phash_prefilter_paths, load_phash_prefilter_decisions
from .file_ops import FileMove, record_paths, unique_destination
from .filtering import AIStateFilter, RecordFilterQuery, SavedFilterPreset
from .formats import IMAGE_SUFFIXES, RAW_SUFFIXES, suffix_for_path
from .grid import ThumbnailGridView
from .job_controller import JobController
from .library_store import (
    CatalogRefreshTask,
    LibraryStore,
    VirtualCollection,
)
from .metadata import CaptureMetadata, MetadataManager
from .models import FilterMode, ImageRecord, ImageVariant, JPEG_SUFFIXES, SessionAnnotation, SortMode
from .perf import perf_logger
from .preview import FullScreenPreview
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
from .review_workflows import BurstRecommendation, RecordWorkflowInsight, TasteProfile, build_burst_recommendations
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
from .semantic_index import SemanticFolderIndexTask
from .face_index import FaceFolderIndexTask
from .semantic_sort import load_semantic_classifications, semantic_classification_for_record, semantic_folder_name
from .shell_actions import detect_photoshop_executable, open_in_file_explorer, open_in_photoshop, open_with_default, open_with_dialog, reveal_in_file_explorer
from .thumbnails import ThumbnailManager
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
from .ui.display_metrics import DisplayProfile, normalize_display_profile_preference
from .ui.backdrop import paint_backdrop
from .pocketdrop import PocketDropPanel
from .ui import layout_ratios
from .ui.nav_rail import NavRail
from .ui.sections import SectionHeader
from .ui.face_groups import FaceGroupsPanel, face_group_photo_paths, load_face_groups
from .ui.prototype_style import FolderTreeView, sidebar_people_icon_pixmap, sidebar_projects_icon_pixmap
from .xmp import load_sidecar_annotation
from .tasks.ai_tasks import AIModelDownloadTask, AIRuntimeInstallTask, AISetupSelection, _compute_ai_folder_probe, _unknown_ai_folder_probe
from .tasks.annotation_tasks import AnnotationHydrationTask, InspectorStatsRequest, InspectorStatsTask, ScopeEnrichmentTask
from .tasks.update_tasks import AppUpdateCheckTask, AppUpdateDownloadTask
from .ui.directory_suggestions import _DirectorySuggestionController
from .ui.project_rows import _MAX_VISIBLE_PROJECT_ROWS, _PROJECT_EMPTY_ROW_PX, _PROJECT_ROW_PX


_NAV_SECTION_GAP_PX = 8
_PROJECT_HEADER_BODY_GAP_PX = 0


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
        self._handoff = HandoffController(self)
        self._projects = ProjectsController(self)
        self._views = ViewController(self)
        self._display = DisplayController(self)
        self._startup = StartupController(self)
        self._dragdrop = DragDropController(self)
        self._context_menus = ContextMenuController(self)
        self._inspector = InspectorController(self)
        self._scan = ScanController(self)
        self._settings_ctl = SettingsController(self)
        self._navigation = NavigationController(self)
        self._annotation_ctl = AnnotationController(self)
        self._preview_ctl = PreviewController(self)
        self._help_update = HelpUpdateController(self)
        self._export_jobs = ExportJobsController(self)
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
        self._settings_ctl.load_pane_width_ratios()
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
        self._photoshop_executable = detect_photoshop_executable()
        self.grid = ThumbnailGridView(self.thumbnail_manager)
        self.details_view = PhotoDetailsView(ai_text_provider=self._ai_run.details_ai_text_for_record)
        self._preview_navigation_dirty = False
        self._preview_preload_timer = QTimer(self)
        self._preview_preload_timer.setSingleShot(True)
        self._preview_preload_timer.setInterval(120)
        self._preview_preload_timer.timeout.connect(self._preview_ctl.run_preview_preload)
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
        self._annotation_reapply_timer.timeout.connect(self._annotation_ctl.flush_annotation_hydration_updates)

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
        self._review_chunk_flush_timer.timeout.connect(self._scan.flush_review_chunk_updates)
        self._scope_enrichment_debounce_timer = QTimer(self)
        self._scope_enrichment_debounce_timer.setSingleShot(True)
        self._scope_enrichment_debounce_timer.setInterval(220)
        self._scope_enrichment_debounce_timer.timeout.connect(self._scan.run_scope_enrichment_debounced)
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
        self._resize_progress_dialog: QProgressDialog | None = None
        self._active_catalog_task: CatalogRefreshTask | None = None
        self._catalog_context: CatalogExecutionContext | None = None
        self._catalog_progress_dialog: QProgressDialog | None = None
        self._job_controllers: dict[str, JobController] = {}
        self._archive_job_key = "archive:create"
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
        self._inspection_stats_pool = QThreadPool(self)
        self._inspection_stats_pool.setMaxThreadCount(1)
        self._inspection_stats_drain_timer = QTimer(self)
        self._inspection_stats_drain_timer.setInterval(25)
        self._inspection_stats_drain_timer.timeout.connect(self._inspector.drain_inspector_stats_results)
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
        self._settings_ctl.apply_cull_thresholds_to_classifier()
        self._settings_ctl.apply_base_score_blend_to_workflow()
        self._ai_review_detail_progress_enabled = self._settings.value(self.AI_REVIEW_DETAIL_PROGRESS_KEY, False, bool)
        self._show_ai_tags_in_grid = self._settings.value(self.SHOW_AI_TAGS_IN_GRID_KEY, False, bool)
        self.grid.set_show_ai_annotations(self._show_ai_tags_in_grid)
        self._apply_edits_to_pocketdrop = self._settings.value(self.APPLY_EDITS_TO_POCKETDROP_KEY, False, bool)
        self._phash_prefilter_settings = self._settings_ctl.load_phash_prefilter_settings()
        self._catalog_load_source = "idle"
        self._catalog_load_detail = "Ready"
        self._review_grouping_cache_source = "idle"
        self._review_grouping_cache_detail = "Ready"
        self._review_feature_cache_source = "idle"
        self._review_feature_cache_detail = "Ready"
        self._review_scoring_cache_source = "idle"
        self._review_scoring_cache_detail = "Ready"
        self._folder_watch_refresh_pending = False
        # Network and removable drives get no QFileSystemWatcher; instead the folder's modified
        # time is compared against the one its listing was taken at whenever the user returns to
        # the app (ScanController.check_folder_changed_on_activation).
        self._folder_dir_mtime_ns: int | None = None
        self._folder_check_task: FolderModifiedCheckTask | None = None
        self._folder_check_token = -1
        self._folder_check_last_started = 0.0
        # Re-rooting the Folders tree on a share waits for a worker to see the drive answer.
        self._drive_sync_tasks: dict[int, tuple[PathReachableTask, str, str]] = {}
        # The AI-folder probe of a folder on a share is computed by a worker (see _ai_folder_probe).
        # So are a share folder's saved pHash prefilter decisions (see _refresh_prefilter_decisions_...).
        # Resolution-aware: coerce the effective style + thresholds to what the
        # display can show (warning is deferred until the window is up).
        self._display_class_value = "high"
        self._effective_loupe_card_style = self._loupe_card_style
        self._display.apply_display_style_policy(show_warning=False)
        QTimer.singleShot(0, self._display.post_show_display_setup)
        self.grid.set_free_smooth_scroll_enabled(self._free_smooth_scroll_enabled)
        self._ai_setup.refresh_ai_runtime_preferences()

    def _init_session_and_saved_collections(self) -> None:
        """Session id, saved presets / favorites / recents / commands, collection and tool-mode
        state, undo stack and the annotation-persistence queue wiring."""
        self._folder_session.session_id = self._decision_store.ensure_session(
            self._settings.value(self.SESSION_KEY, DecisionStore.DEFAULT_SESSION, str)
        )
        self._winner_mode = self._settings_ctl.load_winner_mode()
        self._delete_mode = self._settings_ctl.load_delete_mode()
        self._workflow_presets = self._settings_ctl.load_workflow_presets()
        self._fast_rating_hint_disabled = self._settings.value(self.FAST_RATING_HINT_DISABLED_KEY, False, bool)
        self._fast_rating_hint_sessions = self._settings_ctl.load_fast_rating_hint_sessions()
        self._favorites = self._settings_ctl.load_favorites()
        self._recent_folders = self._settings_ctl.load_recent_folders()
        self._recent_destinations = self._settings_ctl.load_recent_destinations()
        self._folder_view_states = self._settings_ctl.load_folder_view_states()
        self._pending_folder_scroll_value: int | None = None
        self._saved_filter_presets = self._settings_ctl.load_saved_filter_presets()
        self._saved_workflow_recipes = self._settings_ctl.load_saved_workflow_recipes()
        self._saved_workspace_presets = self._settings_ctl.load_saved_workspace_presets()
        self._recent_command_ids = self._settings_ctl.load_recent_command_ids()
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
        self._annotation_persistence_queue.failed.connect(self._annotation_ctl.handle_annotation_persist_failed)
        self._annotation_persistence_queue.warning.connect(self._annotation_ctl.handle_annotation_persist_warning)
        self._annotation_persistence_queue.winner_sync_failed.connect(self._annotation_ctl.handle_winner_sync_failed)
        self._annotation_persistence_queue.winner_kept.connect(self._annotation_ctl.handle_winner_kept)

    def _init_filter_metadata_and_folder_watching(self) -> None:
        """Search debounce, the filter-metadata manager and prefetch timers, and the folder watcher."""
        self._search_apply_timer = QTimer(self)
        self._search_apply_timer.setSingleShot(True)
        self._search_apply_timer.setInterval(140)
        self._search_apply_timer.timeout.connect(self._records_view.commit_search_text_filter)
        self._filter_metadata_manager = MetadataManager(max_workers=2, parent=self)
        self._filter_metadata_manager.metadata_ready.connect(self._records_view.handle_filter_metadata_ready)
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
        self._folder_watcher.directoryChanged.connect(self._scan.handle_watched_folder_changed)
        self._folder_watch_refresh_timer = QTimer(self)
        self._folder_watch_refresh_timer.setSingleShot(True)
        self._folder_watch_refresh_timer.setInterval(900)
        self._folder_watch_refresh_timer.timeout.connect(self._scan.run_watched_folder_refresh)
        app = QApplication.instance()
        if app is not None:
            app.applicationStateChanged.connect(self._scan.handle_application_state_changed)

    def _init_folder_tree_and_drive_list(self) -> None:
        """Folder model, the Folders tree and the flat Drives list."""
        # Never rooted at "all drives" (setRootPath("")): one offline network drive then stalls every listing for ~20 s.
        # It is rooted at one drive at a time (NavigationController.root_tree_at); the Drives list has its own model.
        self.folder_model = QFileSystemModel(self)
        self.folder_model.setFilter(self._navigation.folder_tree_filter())

        self.folder_tree = FolderTreeView()
        self.folder_tree.setObjectName("folderTree")
        self.folder_tree.setModel(self._navigation.empty_tree_model())
        self.folder_tree.set_single_drive_expansion_enabled(
            self._single_drive_expansion_enabled
        )
        self.folder_tree.setRootIndex(QModelIndex())
        self.folder_tree.setHeaderHidden(True)
        self.folder_tree.header().hide()
        self.folder_tree.setMouseTracking(True)
        self.folder_tree.clicked.connect(self._navigation.handle_tree_selection)
        self.folder_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.folder_tree.customContextMenuRequested.connect(self._navigation.show_folder_tree_context_menu)
        self.folder_tree.setAcceptDrops(True)
        self.folder_tree.viewport().setAcceptDrops(True)
        self.folder_tree.viewport().installEventFilter(self)

        # Drives sit in their own flat list; the Folders tree below is rooted at
        # the drive holding the current folder (see _sync_drive_sections).
        self.drive_list = FolderTreeView()
        self.drive_list.setObjectName("driveList")
        self.drive_list.setModel(self._navigation.drive_model)
        self.drive_list.setRootIndex(QModelIndex())
        self.drive_list.setHeaderHidden(True)
        self.drive_list.header().hide()
        self.drive_list.setMouseTracking(True)
        self.drive_list.set_drives_only(True)
        self.drive_list.clicked.connect(self._navigation.handle_drive_selected)
        self._drive_list_fit_timer = QTimer(self)
        self._drive_list_fit_timer.setSingleShot(True)
        self._drive_list_fit_timer.setInterval(0)
        self._drive_list_fit_timer.timeout.connect(self.drive_list.fit_height_to_rows)
        self._navigation.drive_model.rowsInserted.connect(lambda *_args: self._drive_list_fit_timer.start())
        self._navigation.drive_model.rowsRemoved.connect(lambda *_args: self._drive_list_fit_timer.start())
        self._navigation.drive_model.modelReset.connect(self._drive_list_fit_timer.start)
        self._navigation.drive_model.layoutChanged.connect(lambda *_args: self._drive_list_fit_timer.start())
        self._navigation.drive_model.refresh()
        self._drive_list_fit_timer.start()

    def _init_left_rail_section_widgets(self) -> None:
        """Left-rail section widgets: Drives / Folders headers, Favorites list, Face Groups and
        Collections sections."""
        self.drives_refresh_button = self._toolbar.build_left_rail_plus_button(tooltip="Refresh drives")
        self.drives_refresh_button.setProperty("fluentGlyph", "E72C")
        self.drives_refresh_button.clicked.connect(self._navigation.refresh_drive_list)
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
        self.favorites_list.customContextMenuRequested.connect(self._navigation.show_favorites_context_menu)
        self.favorites_list.itemActivated.connect(self._navigation.handle_favorite_activated)
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
        self._handoff.apply_pocketdrop_background()

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
        self._navigation.refresh_favorites_panel()

    def _init_path_controls_and_combos(self) -> None:
        """Path combos and controls, selection-count labels, and the sort / filter / columns combos."""
        self._directory_up_buttons: list[QToolButton] = []
        self._directory_down_buttons: list[QToolButton] = []
        self.manual_path_combo = self._projects.build_path_combo(mode="manual")
        self.ai_path_combo = self._projects.build_path_combo(mode="ai")
        self.manual_path_control = self._projects.build_path_control(self.manual_path_combo, mode="manual")
        self.ai_path_control = self._projects.build_path_control(self.ai_path_combo, mode="ai")
        self.manual_selection_count_label = self._projects.build_selection_count_label(mode="manual")
        self.ai_selection_count_label = self._projects.build_selection_count_label(mode="ai")

        self.sort_combo = QComboBox()
        for mode in SortMode:
            self.sort_combo.addItem(mode.value, mode)
        self.sort_combo.currentIndexChanged.connect(self._views.handle_sort_changed)

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
        self.columns_combo.currentIndexChanged.connect(self._views.handle_columns_changed)

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
        self._settings_ctl.apply_shortcut_overrides()
        # zen_mode binding is owned by the QShortcut above, so the QAction itself
        # must clear its sequence (after shortcut overrides apply) to avoid double-fire.
        self.actions.zen_mode.setShortcut(QKeySequence())
        self._records_view.build_record_filter_actions()
        self.projects_add_button.clicked.connect(
            lambda _checked=False: self.actions.create_virtual_collection.trigger()
        )
        self.face_groups_add_button.clicked.connect(
            lambda _checked=False: self.actions.manage_people.trigger()
        )
        self.projects_list.itemActivated.connect(self._projects.handle_project_activated)
        self.projects_list.itemClicked.connect(self._projects.handle_project_activated)
        self.projects_list.customContextMenuRequested.connect(self._projects.show_projects_context_menu)
        self.face_groups_panel.group_activated.connect(self._projects.handle_face_group_activated)
        self.face_groups_panel.browse_all_requested.connect(
            lambda: self.actions.manage_people.trigger()
        )
        self._projects.refresh_face_groups()

    def _init_inspector_menus_and_filter_buttons(self) -> None:
        """Inspector panel, shared popup menus, search fields and the Review / View / Filter buttons."""
        self.inspector_panel = InspectorPanel()
        self.inspector_panel.setMinimumWidth(0)
        self.thumbnail_manager.thumbnail_ready.connect(self._inspector.handle_inspector_thumbnail_ready)
        self.thumbnail_manager.thumbnail_ready.connect(self._preview_ctl.handle_preview_filmstrip_thumbnail_ready)
        self.workspace_preset_menu = QMenu(self)
        self.workflow_recipe_menu = QMenu("Run Recipe", self)
        self.collections_menu = QMenu("Collections", self)
        self.catalog_menu = QMenu("Library", self)

        self.manual_search_field = self._projects.build_search_field()
        self.ai_search_field = self._projects.build_search_field()
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
        ai_status_layout.addWidget(self._projects.build_section_label("AI Status"))
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
        self.collection_mode_save_button.clicked.connect(self._catalog.save_collection_mode)
        self.collection_mode_cancel_button.clicked.connect(self._catalog.cancel_collection_mode)
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
        self.details_view.layout_state_changed.connect(self._settings_ctl.save_details_view_state)
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
        self.workspace_docks.on_user_resized_panels = self._settings_ctl.remember_user_pane_widths
        self.workspace_docks.width_ratios_provider = self._settings_ctl.pane_width_ratios
        self.inspector_panel.popout_requested.connect(lambda: self.workspace_docks.pop_out_panel("inspector"))
        self.inspector_panel.swap_side_requested.connect(self.workspace_docks.swap_sides)
        self.inspector_panel.close_requested.connect(lambda: self.workspace_docks.hide_panel("inspector"))
        self.inspector_panel.face_cycle_requested.connect(self._inspector.cycle_inspector_face_preview)
        self.inspector_panel.previous_requested.connect(lambda: self.grid.step_current(-1))
        self.inspector_panel.next_requested.connect(lambda: self.grid.step_current(1))
        self.inspector_panel.analyze_requested.connect(lambda: self.actions.run_ai_culling.trigger())
        self.inspector_panel.compare_requested.connect(lambda: self.actions.compare_mode.trigger())
        self._settings_ctl.refresh_workspace_preset_menu()
        self._settings_ctl.refresh_workflow_recipe_menu()
        self._projects.refresh_collections_menu()
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
        self.update_download_button = self._help_update.build_update_download_button()
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
        self._settings_ctl.adopt_menu_bar_shortcuts()
        self.menuBar().hide()
        self._help_update.refresh_update_button_state()
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
        self._settings_ctl.apply_default_workspace()
        self._appearance.apply_display_profile()
        QTimer.singleShot(0, self._settings_ctl.restore_details_view_state)

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
        self._scan.refresh_catalog_status_indicator()
        self._aiculler.refresh_adapter_status_indicator()
        self._records_view.refresh_filter_toolbar_menu()
        self._navigation.refresh_recent_folder_combos()

    def _init_view_signal_connections(self) -> None:
        """Connect the thumbnail grid and details-view signals to the window's handlers."""
        self.grid.current_changed.connect(self._inspector.handle_current_changed)
        self.grid.collection_selection_changed.connect(self._catalog.refresh_collection_mode_ui)
        self.grid.collection_cancel_requested.connect(self._catalog.cancel_collection_mode)
        self.grid.preview_requested.connect(self._preview_ctl.open_preview)
        self.grid.delete_requested.connect(self._annotation_ctl.delete_record)
        self.grid.keep_requested.connect(self._annotation_ctl.keep_record)
        self.grid.move_requested.connect(self._record_ops.move_record_prompt)
        self.grid.tag_requested.connect(self._annotation_ctl.tag_record)
        self.grid.winner_requested.connect(self._annotation_ctl.toggle_winner)
        self.grid.reject_requested.connect(self._annotation_ctl.toggle_reject)
        self.grid.adapter_label_requested.connect(self._aiculler.handle_aiculler_adapter_label_requested)
        self.grid.adapter_reasons_requested.connect(self._aiculler.handle_aiculler_adapter_reasons_requested)
        self.grid.adapter_review_mode_cleared.connect(self._aiculler.exit_aiculler_adapter_review_mode)
        self.grid.dispute_label_requested.connect(self._aiculler.handle_dispute_label_requested)
        self.grid.dispute_chord_started.connect(self._aiculler.handle_dispute_chord_started)
        self.grid.dispute_chord_cancelled.connect(self._aiculler.handle_dispute_chord_cancelled)
        self.grid.context_menu_requested.connect(self._context_menus.show_grid_context_menu)
        self.grid.selection_changed.connect(self._inspector.handle_grid_selection_changed)
        self.grid.verticalScrollBar().valueChanged.connect(self._records_view.schedule_metadata_scroll_prefetch)
        self.details_view.current_changed.connect(self._views.handle_details_current_changed)
        self.details_view.selection_changed.connect(self._views.handle_details_selection_changed)
        self.details_view.preview_requested.connect(self._preview_ctl.open_preview)
        self.details_view.context_menu_requested.connect(self._context_menus.show_grid_context_menu)
        self.details_view.delete_requested.connect(self._annotation_ctl.delete_record)
        self.details_view.keep_requested.connect(self._annotation_ctl.keep_record)
        self.details_view.move_requested.connect(self._record_ops.move_record_prompt)
        self.details_view.tag_requested.connect(self._annotation_ctl.tag_record)
        self.details_view.winner_requested.connect(self._annotation_ctl.toggle_winner)
        self.details_view.reject_requested.connect(self._annotation_ctl.toggle_reject)

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
        self._settings_ctl.restore_window_state()
        self._records_view.sync_record_filter_controls()
        self._records_view.update_filter_summary()
        self._toolbar.sync_chrome_to_manual_review()
        self._inspector.update_action_states()
        QTimer.singleShot(0, self._settings_ctl.finish_startup_restore)
        if self._check_updates_on_startup and not self._quick_view_mode:
            QTimer.singleShot(2500, self._help_update.check_for_updates_on_startup)

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
    #     ``_preview_ctl.preview_if_built`` / ``preview_is_visible`` so that never
    #     forces the build; ``configure_new_preview`` replays the current window state
    #     into whatever gets built later.
    #   * Anything the old __init__ wired to the viewer lives in that method.
    @property
    def preview(self) -> FullScreenPreview:
        preview = self._preview
        if preview is None:
            preview = self._preview_ctl.build_preview()
        return preview

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
        timer.timeout.connect(self._preview_ctl.run_deferred_preview_build)
        self._deferred_preview_timer = timer
        timer.start()

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

    def changeEvent(self, event) -> None:  # type: ignore[override]
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange:
            self._display.sync_window_control_glyphs()

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
            if self._display.is_window_drag_point(local):
                return True, 2  # HTCAPTION: drag, snap, double-click maximize
        return super().nativeEvent(event_type, message)

    # -- proportional layout ----------------------------------------------

    PANE_RATIOS_KEY = "ui/pane_width_ratios"
    # Early builds saved transient start-up widths as if they were drags.
    PANE_RATIOS_RESET_KEY = "ui/pane_width_ratios_reset_v3"

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

    @staticmethod
    def _set_widget_font_px(widget: QWidget, size: int) -> None:
        sheet = f"font-size: {size}px;"
        if widget.styleSheet() != sheet:
            widget.setStyleSheet(sheet)

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
        add_button("E72C", "Restart App", self._startup.restart_app_for_development)
        add_button("E946", "Help", self._help_update.show_library_help)
        add_button("E713", "Settings", self._settings_ctl.show_settings)
        return bar

    # -- Top-bar slot model ------------------------------------------------
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

    def _handle_system_color_scheme_changed(self) -> None:
        if self._appearance_mode == AppearanceMode.AUTO:
            self._appearance.apply_appearance()

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

    # ------------------------------------------------------------------
    # Capability readiness, repair and diagnostics
    # ------------------------------------------------------------------

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._zen_mode_enabled:
            self._zen.set_zen_mode(False)
        self._settings_ctl.remember_current_folder_view_state()
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
        self._settings_ctl.save_window_state()
        perf_logger().log("app.close")
        perf_logger().flush()
        super().closeEvent(event)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self._startup_window_state_fixup_applied:
            return
        self._startup_window_state_fixup_applied = True
        self._display.apply_native_frame_styles()
        self._display.sync_window_control_glyphs()
        # Now, not on a timer: the first frame is drawn at the live ratios.
        self._appearance.apply_layout_ratios()
        # Don't auto-focus/select any control on startup (the path bar used to
        # grab focus and highlight its text).
        QTimer.singleShot(0, self._startup.clear_startup_focus)
        # Once the viewport has a real width, snap the zoom slider to the grid's
        # actual tile width so dragging starts from the right place.
        QTimer.singleShot(0, self._views.sync_zoom_slider_from_grid)
        if self._startup_window_state in {"maximized", "fullscreen"}:
            QTimer.singleShot(0, self._startup.apply_startup_window_state_fixup)
        else:
            # The saved/default client rectangle was clamped before show. Run
            # once more now that Windows has reported the real frame/title bar,
            # keeping the complete window above the taskbar.
            QTimer.singleShot(0, lambda: fit_window_to_available_geometry(self))

    def _load_start_folder(self) -> None:
        last_folder = self._settings.value(self.LAST_FOLDER_KEY, "", str)
        if last_folder and not self._dir_confirmed_missing(last_folder):
            # On a network / removable drive nothing asks the share here: the scan worker opens the
            # folder (or the grid says it could not), and the saved last folder is kept either way.
            self._navigation.select_folder(last_folder, sync_tree=False, chunked_restore=True)
            self.folder_tree.clearSelection()
            self.folder_tree.setCurrentIndex(QModelIndex())

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

    def _open_file_associations_dialog(self) -> None:
        dialog = FileAssociationsDialog(self)
        dialog.exec()

    @staticmethod
    def _drive_root_for(folder: str) -> str:
        drive, _rest = os.path.splitdrive(os.path.normpath(folder)) if folder else ("", "")
        if not drive:
            return ""
        return drive if drive.startswith("\\") else drive + os.sep

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
                self._display.schedule_app_bar_alignment()
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
                handled = self._dragdrop.handle_record_drop_event(event, source="folder_tree")
                if handled is not None:
                    return handled
            favorites_viewport = self.favorites_list.viewport() if hasattr(self, "favorites_list") else None
            if watched is favorites_viewport:
                handled = self._dragdrop.handle_record_drop_event(event, source="favorites")
                if handled is not None:
                    return handled
            return super().eventFilter(watched, event)
        except RuntimeError:
            return False

    @staticmethod
    def _is_filesystem_root(folder: str) -> bool:
        return FolderOpsController.is_filesystem_root(folder)

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
            renamed_record = self._record_ops.record_after_moves(item.record, item.planned_moves)
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
            self._views.apply_records_view(current_path=current_path)
            self.statusBar().showMessage(f"Renamed {len(renamed_items)} image bundle(s)")
            return

        self.statusBar().showMessage(
            f"Renamed {len(renamed_items)} image bundle(s) in {Path(context.folder).name or context.folder} (undo is only available for the current folder)"
        )

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

    def _selected_records_for_actions(self) -> list[ImageRecord]:
        current_index = self.grid.current_index()
        if current_index < 0:
            return []
        return self._selected_records_for_context(current_index)

    def _selected_records_for_workflow(self) -> list[ImageRecord]:
        records = self._selected_records_for_actions()
        if records:
            return records
        current_index = self.grid.current_index()
        record = self._record_at(current_index)
        return [record] if record is not None else []

    def _rename_selected_record(self) -> None:
        current_index = self.grid.current_index()
        if current_index >= 0:
            self._record_ops.rename_record_prompt(current_index)

    def _accept_selected_records(self) -> None:
        records = self._selected_records_for_actions()
        if records:
            self._annotation_ctl.batch_set_winner(records)

    def _reject_selected_records(self) -> None:
        records = self._selected_records_for_actions()
        if records:
            self._annotation_ctl.batch_set_reject(records)

    def _keep_selected_records(self) -> None:
        records = self._selected_records_for_actions()
        if records:
            self._annotation_ctl.batch_keep_records(records)

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
            self._annotation_ctl.batch_restore_records(records)

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
        self._annotation_ctl.batch_open_in_photoshop(records)

    def _create_folder_in_current_folder(self) -> None:
        parent = self._current_folder or QDir.homePath()
        self._folder_ops.create_folder_prompt(parent, select_created=False)

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

    @staticmethod
    def _category_profile(category_info: dict[str, object], face_records: tuple[object, ...]) -> str:
        if face_records:
            return "people_portrait"
        category = str(category_info.get("primary_category") or "uncategorized").strip().lower()
        return category or "uncategorized"

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

    # Per-folder AI-data probes (SQLite opens + artifact existence checks) are
    # stable while navigating within a folder, so cache them keyed by folder.
    # Invalidated on folder change (key mismatch), on AI operations that mutate a
    # folder's hidden cache (_invalidate_ai_folder_probe_cache), and by TTL.
    _AI_FOLDER_PROBE_TTL_S = 60.0

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

    _PREFILTER_LOAD_TTL_S = 60.0

    def _record_for_path(self, path: str) -> ImageRecord | None:
        direct = self._all_records_by_path.get(path)
        if direct is not None:
            return direct
        normalized = _memory_path_key(path)
        for record_path, record in self._all_records_by_path.items():
            if _memory_path_key(record_path) == normalized:
                return record
        return None

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

    def _record_paths(self, record: ImageRecord) -> tuple[str, ...]:
        return record_paths(record)

    @staticmethod
    def _annotation_snapshot(annotation: SessionAnnotation) -> SessionAnnotation:
        return replace(annotation)

    PREVIEW_FILMSTRIP_THUMB_SIZE = QSize(192, 128)

    def _selected_records_for_context(self, index: int) -> list[ImageRecord]:
        selected_indexes = self.grid.selected_indexes()
        if index not in selected_indexes:
            selected_indexes = [index]
        return [
            self._records[item_index]
            for item_index in selected_indexes
            if 0 <= item_index < len(self._records) and not self._records[item_index].is_folder
        ]

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

    def _open_current_folder_in_file_manager(self) -> None:
        if self._current_folder and not self._dir_confirmed_missing(self._current_folder):
            open_in_file_explorer(self._current_folder)

    def _unique_destination(self, directory: str, filename: str) -> str:
        return unique_destination(directory, filename)

    def _next_visible_path(self, index: int) -> str | None:
        if not self._records:
            return None
        if index + 1 < len(self._records):
            return self._records[index + 1].path
        if index > 0:
            return self._records[index - 1].path
        return self._records[index].path
