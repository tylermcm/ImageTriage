from __future__ import annotations

import ctypes
import json
import os
import shutil
import subprocess
import sys
import time
from collections import deque
from dataclasses import dataclass, replace
from pathlib import Path
from textwrap import dedent

from PySide6.QtCore import QByteArray, QDir, QEvent, QFile, QFileSystemWatcher, QMimeData, QModelIndex, QObject, QRunnable, QSize, QSettings, QSignalBlocker, QStandardPaths, Qt, QThreadPool, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QActionGroup, QCloseEvent, QCursor, QGuiApplication, QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
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
    QStackedWidget,
    QStatusBar,
    QTabBar,
    QToolButton,
    QToolBar,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from .ai_model import (
    AIModelInstallation,
    DEFAULT_AI_MODEL_SIZE_MB,
    download_ai_model as download_managed_ai_model,
    resolve_ai_model_installation,
)
from .archive_ops import (
    EXTRACT_ARCHIVE_FILTER,
    CreateArchiveTask,
    ExtractArchiveTask,
    archive_format_for_key,
    ensure_archive_suffix,
)
from .annotation_queue import AnnotationPersistenceQueue
from .ai_training import (
    BuildReferenceBankTask,
    EvaluateRankerTask,
    PrepareLabelingCandidatesTask,
    PrepareTrainingDataTask,
    RankerRunInfo,
    RankerTrainingOptions,
    ReferenceBankBuildOptions,
    ScoreCurrentFolderTask,
    TrainRankerTask,
    ai_training_artifacts_ready,
    build_ai_training_paths,
    clear_active_ranker_selection,
    count_label_records,
    list_ranker_runs,
    labeling_artifacts_ready,
    launch_labeling_app,
    prepare_hidden_ai_training_workspace,
    resolve_trained_checkpoint,
    set_active_ranker_selection,
)
from .ai_workflow import AIRunTask, build_ai_workflow_paths, default_ai_workflow_runtime, existing_hidden_ai_report_dir
from .ai_results import AIBundle, AIConfidenceBucket, build_ai_explanation_lines, find_ai_result_for_record, load_ai_bundle
from .batch_rename import BatchRenameApplyTask, BatchRenamePreview
from .brackets import BracketDetector
from .bursts import find_burst_groups
from .catalog import CatalogRepository, catalog_cache_env_override
from .decision_store import DecisionStore
from .file_ops import FileMove, copy_paths, create_folder, delete_folder, move_folder, move_paths, rename_bundle_paths, rename_folder, unique_destination
from .filtering import (
    AIStateFilter,
    FileTypeFilter,
    OrientationFilter,
    RecordFilterQuery,
    ReviewStateFilter,
    SavedFilterPreset,
    active_filter_labels,
    builtin_filter_presets,
    deserialize_saved_filter_preset,
    matches_record_query,
    serialize_saved_filter_preset,
)
from .formats import FITS_SUFFIXES, MODEL_SUFFIXES, RAW_SUFFIXES, suffix_for_path
from .grid import BurstVisualInfo, GridDeltaUpdate, ThumbnailGridView
from .image_convert import ConvertApplyTask, ConvertOptions, ConvertPlan, ConvertSourceItem
from .image_resize import ResizeApplyTask, ResizeOptions, ResizePlan, ResizeSourceItem
from .job_controller import JobController, JobSpec
from .keyboard_mapping import ShortcutBinding, normalize_shortcut_text, serialize_shortcut_overrides
from .library_store import CatalogRefreshSummary, CatalogRefreshTask, CatalogRoot, LibraryStore, VirtualCollection
from .metadata import EMPTY_METADATA, CaptureMetadata, MetadataManager
from .models import DeleteMode, FilterMode, ImageRecord, JPEG_SUFFIXES, SessionAnnotation, SortMode, WinnerMode, sort_records
from .preview import FullScreenPreview, PreviewEntry
from .production_workflows import (
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
    deserialize_workflow_recipe,
    deserialize_workspace_preset,
    recipe_key_for_name,
    serialize_workflow_recipe,
    serialize_workspace_preset,
)
from .review_tools import FOCUS_ASSIST_COLORS, FOCUS_ASSIST_STRENGTHS
from .records_view_cache import RecordsViewCache, ViewInvalidationReason
from .review_intelligence import BuildReviewIntelligenceTask, ReviewIntelligenceBundle
from .review_workflows import (
    REVIEW_ROUND_FIRST_PASS,
    REVIEW_ROUND_HERO,
    REVIEW_ROUND_SECOND_PASS,
    REVIEW_ROUND_THIRD_PASS,
    BurstRecommendation,
    RecordWorkflowInsight,
    TasteProfile,
    ai_strength,
    build_burst_recommendations,
    build_calibration_pairs,
    build_pairwise_label_payload,
    build_record_workflow_insight,
    current_timestamp,
    disagreement_level_for,
    normalize_review_round,
    review_round_label,
)
from .scanner import FolderScanTask, discover_edited_paths, normalize_filesystem_path, normalized_path_key, scan_folder
from .settings_dialog import WorkflowPreset, WorkflowSettingsDialog
from .shell_actions import detect_photoshop_executable, open_in_file_explorer, open_in_photoshop, open_with_default, open_with_dialog, reveal_in_file_explorer
from .thumbnails import ThumbnailManager
from .ui import (
    AdvancedFilterDialog,
    AITrainingProgressDialog,
    AITrainingStatsDialog,
    AppearanceMode,
    BatchRenameDialog,
    BestOfSetDialog,
    CatalogSearchDialog,
    CollectionEditDialog,
    CommandPaletteDialog,
    ConvertDialog,
    HandoffBuilderDialog,
    HelpMarkdownDialog,
    InspectorPanel,
    KeyboardShortcutDialog,
    MainWindowActions,
    PaletteCommand,
    RankerCenterDialog,
    RankerCenterSummary,
    ResizeDialog,
    TasteCalibrationDialog,
    TrainRankerDialog,
    WorkspaceDocks,
    build_app_palette,
    build_app_stylesheet,
    build_main_menu_bar,
    build_main_window_actions,
    build_primary_toolbar,
    build_workspace_docks,
    clear_window_layout,
    parse_appearance_mode,
    restore_window_layout,
    resolve_theme,
    save_window_layout,
)
from .xmp import load_sidecar_annotation, sidecar_bundle_paths, sync_sidecar_annotation


@dataclass(slots=True)
class UndoAction:
    kind: str
    primary_path: str
    file_moves: tuple[FileMove, ...] = ()
    original_winner: bool = False
    original_reject: bool = False
    original_photoshop: bool = False
    rating: int = 0
    tags: tuple[str, ...] = ()
    original_review_round: str = ""
    folder: str = ""
    source_paths: tuple[str, ...] = ()
    session_id: str = ""
    winner_mode: str = ""


@dataclass(slots=True)
class BatchRenameExecutionContext:
    preview: BatchRenamePreview
    folder: str
    is_current_folder: bool
    loaded_annotations: dict[str, SessionAnnotation]
    current_path_before: str | None = None


@dataclass(slots=True)
class ResizeExecutionContext:
    plan: ResizePlan
    options: ResizeOptions
    refresh_folder: str = ""


@dataclass(slots=True)
class ConvertExecutionContext:
    plan: ConvertPlan
    options: ConvertOptions
    refresh_folder: str = ""


@dataclass(slots=True)
class WorkflowExecutionContext:
    recipe: WorkflowRecipe
    action: str
    destination_root: str = ""
    destination_dir: str = ""
    refresh_folder: str = ""
    archive_after_export: bool = False
    archive_format: str = "zip"


@dataclass(slots=True)
class ArchiveExecutionContext:
    mode: str
    archive_path: str = ""
    destination_dir: str = ""
    archive_label: str = ""
    refresh_folder: str = ""


@dataclass(slots=True)
class AITrainingExecutionContext:
    action: str
    folder: str
    title: str
    launch_labeling_after_prepare: bool = False
    reference_bank_path: str = ""
    run_id: str = ""
    run_label: str = ""
    log_path: str = ""


@dataclass(slots=True)
class AITrainingPipelineState:
    folder: str
    options: RankerTrainingOptions
    step_index: int = 0


@dataclass(slots=True)
class ChildAppProcess:
    name: str
    process: subprocess.Popen[str]


@dataclass(slots=True)
class ShortcutTarget:
    id: str
    label: str
    section: str
    default_shortcut: str
    apply: object


@dataclass(slots=True)
class CatalogExecutionContext:
    root_paths: tuple[str, ...] = ()
    label: str = ""


class ToolbarCustomizerDialog(QDialog):
    MODES = (
        ("primary", "Top Toolbar"),
        ("manual", "Manual Toolbar"),
        ("ai", "AI Toolbar"),
    )

    def __init__(
        self,
        *,
        layouts: dict[str, list[str]],
        allowed_items: dict[str, tuple[str, ...]],
        labels: dict[str, str],
        current_mode: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("toolbarCustomizerDialog")
        self.setWindowTitle("Customize Toolbars")
        self.resize(940, 240)
        self.setMinimumWidth(760)
        self.setSizeGripEnabled(True)
        self._layouts = {mode: list(items) for mode, items in layouts.items()}
        self._allowed_items = allowed_items
        self._labels = labels
        self._mode = current_mode if current_mode in self._layouts else "manual"
        self._selected_index = 0 if self._layouts.get(self._mode) else -1

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(14, 12, 14, 12)
        root_layout.setSpacing(10)

        header_row = QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.setSpacing(8)
        header_row.addWidget(QLabel("Toolbar"))
        self.mode_combo = QComboBox()
        for mode, label in self.MODES:
            self.mode_combo.addItem(label, mode)
        selected_index = self.mode_combo.findData(self._mode)
        self.mode_combo.setCurrentIndex(max(0, selected_index))
        self.mode_combo.currentIndexChanged.connect(self._handle_mode_changed)
        header_row.addWidget(self.mode_combo)
        header_row.addStretch(1)
        root_layout.addLayout(header_row)

        self.preview_scroll = QScrollArea()
        self.preview_scroll.setObjectName("toolbarCustomizerPreviewScroll")
        self.preview_scroll.setWidgetResizable(True)
        self.preview_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.preview_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.preview_scroll.setFixedHeight(92)
        self.preview_frame = QFrame()
        self.preview_frame.setObjectName("toolbarCustomizerPreviewHost")
        self.preview_layout = QHBoxLayout(self.preview_frame)
        self.preview_layout.setContentsMargins(0, 0, 0, 0)
        self.preview_layout.setSpacing(0)
        self.preview_scroll.setWidget(self.preview_frame)
        root_layout.addWidget(self.preview_scroll)

        controls = QHBoxLayout()
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setSpacing(8)
        controls.addWidget(QLabel("Add"))
        self.add_combo = QComboBox()
        self.add_combo.setMinimumWidth(220)
        controls.addWidget(self.add_combo)
        self.add_button = QPushButton("Add")
        self.add_button.clicked.connect(self._add_selected_item)
        controls.addWidget(self.add_button)
        self.remove_button = QPushButton("Remove")
        self.remove_button.clicked.connect(self._remove_selected_item)
        controls.addWidget(self.remove_button)
        self.move_left_button = QPushButton("Move Left")
        self.move_left_button.clicked.connect(lambda: self._move_selected_item(-1))
        controls.addWidget(self.move_left_button)
        self.move_right_button = QPushButton("Move Right")
        self.move_right_button.clicked.connect(lambda: self._move_selected_item(1))
        controls.addWidget(self.move_right_button)
        self.reset_button = QPushButton("Reset")
        self.reset_button.clicked.connect(self._reset_current_toolbar)
        controls.addWidget(self.reset_button)
        controls.addStretch(1)
        root_layout.addLayout(controls)

        self.button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        apply_button = self.button_box.button(QDialogButtonBox.StandardButton.Ok)
        if apply_button is not None:
            apply_button.setText("Apply")
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)
        root_layout.addWidget(self.button_box)

        self._preview_index_by_widget: dict[QWidget, int] = {}
        self._preview_content_width = 0
        self._refresh()

    def _available_dialog_width(self) -> int:
        screen = self.screen() or QGuiApplication.screenAt(self.frameGeometry().center()) or QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        if screen is None:
            return 1400
        return max(760, screen.availableGeometry().width() - 24)

    def _fit_window_to_preview(self) -> None:
        preview_width = max(
            self._preview_content_width,
            self.preview_frame.minimumSizeHint().width(),
            self.preview_frame.sizeHint().width(),
        )
        controls_width = 760
        target_width = min(self._available_dialog_width(), max(760, preview_width + 34, controls_width))
        target_width = max(self.width(), target_width)
        if abs(self.width() - target_width) > 8:
            self.resize(target_width, self.height())

    def toolbar_layouts(self) -> dict[str, list[str]]:
        return {mode: list(items) for mode, items in self._layouts.items()}

    def _current_items(self) -> list[str]:
        return self._layouts.setdefault(self._mode, [])

    def _clear_layout_widgets(self, layout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def eventFilter(self, watched, event) -> bool:
        if event.type() == QEvent.Type.MouseButtonPress and isinstance(watched, QWidget):
            index = self._preview_index_by_widget.get(watched)
            if index is not None:
                self._select_item(index)
                return True
        return super().eventFilter(watched, event)

    def _register_preview_selectable(self, widget: QWidget, index: int) -> None:
        self._preview_index_by_widget[widget] = index
        widget.installEventFilter(self)
        for child in widget.findChildren(QWidget):
            self._preview_index_by_widget[child] = index
            child.installEventFilter(self)

    def _handle_mode_changed(self) -> None:
        mode = self.mode_combo.currentData()
        if isinstance(mode, str):
            self._mode = mode
        self._selected_index = 0 if self._current_items() else -1
        self._refresh()

    def _select_item(self, index: int) -> None:
        if index < 0 or index >= len(self._current_items()):
            self._selected_index = -1
        else:
            self._selected_index = index
        self._refresh()

    def _available_items(self) -> list[str]:
        current = set(self._current_items())
        return [item for item in self._allowed_items.get(self._mode, ()) if item not in current]

    def _add_selected_item(self) -> None:
        item = self.add_combo.currentData()
        if not isinstance(item, str):
            return
        items = self._current_items()
        if item in items:
            return
        items.append(item)
        self._selected_index = len(items) - 1
        self._refresh()

    def _remove_selected_item(self) -> None:
        items = self._current_items()
        if self._selected_index < 0 or self._selected_index >= len(items):
            return
        items.pop(self._selected_index)
        if not items:
            self._selected_index = -1
        else:
            self._selected_index = min(self._selected_index, len(items) - 1)
        self._refresh()

    def _move_selected_item(self, direction: int) -> None:
        items = self._current_items()
        target = self._selected_index + direction
        if self._selected_index < 0 or target < 0 or target >= len(items):
            return
        items[self._selected_index], items[target] = items[target], items[self._selected_index]
        self._selected_index = target
        self._refresh()

    def _reset_current_toolbar(self) -> None:
        default_items = MainWindow.WORKSPACE_TOOLBAR_DEFAULTS.get(self._mode, ())
        self._layouts[self._mode] = list(default_items)
        self._selected_index = 0 if self._layouts[self._mode] else -1
        self._refresh()

    def _preview_button(
        self,
        text: str,
        *,
        parent: QWidget | None = None,
        object_name: str = "workspacePresetsButton",
        min_width: int | None = None,
        selected: bool = False,
    ) -> QToolButton:
        button = QToolButton(parent)
        button.setObjectName(object_name)
        button.setText(text)
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        button.setCheckable(True)
        button.setChecked(selected)
        if min_width is not None:
            button.setMinimumWidth(min_width)
        return button

    def _primary_preview_text(self, item_id: str) -> tuple[str, int]:
        return {
            "open_folder": ("Open", 96),
            "refresh_folder": ("Refresh", 108),
            "undo": ("Undo", 92),
            "run_ai_culling": ("Run AI", 98),
            "ai_results": ("AI Results", 104),
            "command_palette": ("Command", 106),
            "columns": ("Columns", 94),
            "sort": ("Sort", 82),
            "quick_filter": ("Quick Filter", 112),
            "advanced_filters": ("Adv. Filters", 108),
            "clear_filters": ("Clear", 88),
            "batch_rename": ("Rename", 92),
            "batch_resize": ("Resize", 92),
            "batch_convert": ("Convert", 98),
            "handoff_builder": ("Handoff", 98),
            "send_to_editor": ("Editor", 92),
            "best_of_set": ("Best Of", 92),
            "keyboard_shortcuts": ("Shortcuts", 108),
        }.get(item_id, (self._labels.get(item_id, item_id), 92))

    def _build_primary_preview(self, items: list[str]) -> QWidget:
        toolbar = QToolBar("Primary Preview")
        toolbar.setObjectName("primaryToolbar")
        toolbar.setMovable(False)
        toolbar.setFloatable(False)
        toolbar.setIconSize(QSize(14, 14))
        toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        for index, item_id in enumerate(items):
            if item_id == "separator":
                toolbar.addSeparator()
                continue
            text, min_width = self._primary_preview_text(item_id)
            button = self._preview_button(
                text,
                parent=toolbar,
                object_name="primaryToolbarButton",
                min_width=min_width,
                selected=index == self._selected_index,
            )
            button.clicked.connect(lambda _checked=False, selected=index: self._select_item(selected))
            toolbar.addWidget(button)
            self._register_preview_selectable(button, index)
        if toolbar.layout() is not None:
            toolbar.layout().setContentsMargins(2, 2, 2, 2)
            toolbar.layout().setSpacing(2)
        toolbar.setMinimumHeight(36)
        return toolbar

    def _workspace_preview_widget_for_item(self, item_id: str, index: int, parent: QWidget) -> QWidget:
        selected = index == self._selected_index
        if item_id == "search":
            field = QLineEdit(parent)
            field.setObjectName("workspaceSearchField")
            field.setClearButtonEnabled(True)
            field.setPlaceholderText("Search filenames")
            field.setMinimumWidth(124)
            field.setMaximumWidth(220)
            field.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            if selected:
                field.setProperty("toolbarPreviewSelected", True)
            return field
        if item_id == "address":
            label = QComboBox(parent)
            label.setObjectName("pathComboBox")
            label.setEditable(True)
            label.addItem("X:/Photography/China '26/Raw Files")
            label.setMinimumWidth(260)
            label.setMaximumWidth(460)
            label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            if selected:
                label.setProperty("toolbarPreviewSelected", True)
            return label
        if item_id == "selection_count":
            label = QLabel("3 selected", parent)
            label.setObjectName("toolbarSelectionCount")
            label.setMinimumWidth(76)
            if selected:
                label.setProperty("toolbarPreviewSelected", True)
            return label
        if item_id == "filters":
            return self._preview_button("Filters", parent=parent, object_name="workspaceFiltersButton", selected=selected)
        if item_id == "ai_status":
            wrapper = QWidget(parent)
            wrapper.setObjectName("aiStatusToolbarItem")
            layout = QHBoxLayout(wrapper)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(8)
            section = QLabel("AI Status", wrapper)
            section.setObjectName("sectionLabel")
            layout.addWidget(section)
            progress = QProgressBar(wrapper)
            progress.setRange(0, 1)
            progress.setValue(0)
            progress.setFormat("Idle")
            progress.setAlignment(Qt.AlignmentFlag.AlignCenter)
            progress.setTextVisible(True)
            progress.setMinimumWidth(150)
            progress.setMaximumWidth(210)
            progress.setFixedHeight(18)
            layout.addWidget(progress)
            status = QLabel("AI cache not loaded", wrapper)
            status.setObjectName("secondaryText")
            status.setMaximumWidth(260)
            layout.addWidget(status)
            if selected:
                wrapper.setProperty("toolbarPreviewSelected", True)
            return wrapper

        text = {
            "review": "Review",
            "view": "View",
            "columns": "Columns",
            "sort": "Sort",
            "quick_filter": "Quick Filter",
            "run_ai_culling": "Run AI",
            "ai_results": "AI Results",
            "open_folder": "Open",
            "refresh_folder": "Refresh",
            "undo": "Undo",
            "command_palette": "Command",
            "advanced_filters": "Adv. Filters",
            "clear_filters": "Clear",
            "batch_rename": "Rename",
            "batch_resize": "Resize",
            "batch_convert": "Convert",
            "handoff_builder": "Handoff",
            "send_to_editor": "Editor",
            "best_of_set": "Best Of",
            "keyboard_shortcuts": "Shortcuts",
            "compare": "Compare",
            "auto_advance": "Auto",
            "burst_groups": "Groups",
            "burst_stacks": "Stacks",
            "compact_cards": "Compact",
            "selection_count": "3 selected",
            "accept_selection": "Accept",
            "reject_selection": "Reject",
            "keep_selection": "Keep",
            "move_selection": "Move",
            "delete_selection": "Delete",
            "reveal_in_explorer": "Reveal",
            "open_in_photoshop": "Photoshop",
            "load_saved_ai": "Load Saved",
            "load_ai_results": "Load AI",
            "clear_ai_results": "Clear AI",
            "open_ai_report": "Report",
            "next_ai_pick": "Next Pick",
            "next_unreviewed_ai_pick": "Next Unreviewed",
            "compare_ai_group": "AI Compare",
            "review_ai_disagreements": "Disagree",
            "taste_calibration": "Calibrate",
        }.get(item_id, self._labels.get(item_id, item_id))
        return self._preview_button(text, parent=parent, object_name="workspacePresetsButton", selected=selected)

    def _build_workspace_preview(self, items: list[str]) -> QWidget:
        bar = QWidget()
        bar.setObjectName("workspaceBar")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(12)

        tabs = QTabBar(bar)
        tabs.setObjectName("modeTabs")
        tabs.addTab("Manual Review")
        tabs.addTab("AI Culling")
        tabs.setCurrentIndex(1 if self._mode == "ai" else 0)
        tabs.setExpanding(False)
        tabs.setDrawBase(False)
        tabs.setElideMode(Qt.TextElideMode.ElideNone)
        tabs.setUsesScrollButtons(False)
        tabs.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        tabs.ensurePolished()
        tabs.adjustSize()
        target_width = max(tabs.sizeHint().width(), tabs.minimumSizeHint().width()) + 10
        tabs.setMinimumWidth(target_width)
        tabs.setMaximumWidth(target_width)
        layout.addWidget(tabs, 0, Qt.AlignmentFlag.AlignVCenter)

        controls = QWidget(bar)
        controls.setObjectName("workspaceControls")
        controls_layout = QHBoxLayout(controls)
        controls_layout.setContentsMargins(0, 0, 0, 0)
        controls_layout.setSpacing(8)
        for index, item_id in enumerate(items):
            widget = self._workspace_preview_widget_for_item(item_id, index, controls)
            self._register_preview_selectable(widget, index)
            controls_layout.addWidget(widget)
        controls_layout.addStretch(1)
        layout.addWidget(controls, 1)
        return bar

    def _refresh(self) -> None:
        self._preview_index_by_widget.clear()
        self._clear_layout_widgets(self.preview_layout)
        items = self._current_items()
        if self._selected_index >= len(items):
            self._selected_index = len(items) - 1
        if items:
            preview = self._build_primary_preview(items) if self._mode == "primary" else self._build_workspace_preview(items)
            preview.ensurePolished()
            preview.adjustSize()
            self._preview_content_width = preview.sizeHint().width()
            self.preview_frame.setMinimumWidth(self._preview_content_width)
            self.preview_layout.addWidget(preview)
        else:
            empty = QLabel("Empty", self.preview_frame)
            empty.setObjectName("toolbarEditHint")
            self._preview_content_width = empty.sizeHint().width()
            self.preview_frame.setMinimumWidth(0)
            self.preview_layout.addWidget(empty)
        self.preview_layout.addStretch(1)

        self.add_combo.clear()
        available_items = self._available_items()
        for item_id in available_items:
            self.add_combo.addItem(self._labels.get(item_id, item_id), item_id)
        self.add_button.setEnabled(bool(available_items))
        has_selection = 0 <= self._selected_index < len(items)
        self.remove_button.setEnabled(has_selection)
        self.move_left_button.setEnabled(has_selection and self._selected_index > 0)
        self.move_right_button.setEnabled(has_selection and self._selected_index < len(items) - 1)
        self._fit_window_to_preview()


class AnnotationHydrationSignals(QObject):
    chunk = Signal(str, int, object)
    finished = Signal(str, int)
    failed = Signal(str, int, str)


class AnnotationHydrationTask(QRunnable):
    PRIORITY_BATCH_SIZE = 96
    BACKGROUND_BATCH_SIZE = 240

    def __init__(
        self,
        *,
        scope_key: str,
        token: int,
        session_id: str,
        records: tuple[ImageRecord, ...],
        prioritized_paths: tuple[str, ...] = (),
    ) -> None:
        super().__init__()
        self.scope_key = scope_key
        self.token = token
        self.session_id = session_id
        self.records = records
        self.prioritized_paths = prioritized_paths
        self.signals = AnnotationHydrationSignals()
        self._cancelled = False
        self.setAutoDelete(True)

    def cancel(self) -> None:
        self._cancelled = True

    @staticmethod
    def _record_batches(records: list[ImageRecord], batch_size: int) -> list[list[ImageRecord]]:
        return [records[index : index + batch_size] for index in range(0, len(records), batch_size)]

    def _partition_records(self) -> tuple[list[ImageRecord], list[ImageRecord]]:
        if not self.records:
            return [], []
        record_by_key = {normalized_path_key(record.path): record for record in self.records}
        prioritized_records: list[ImageRecord] = []
        seen_keys: set[str] = set()
        for path in self.prioritized_paths:
            if not path:
                continue
            key = normalized_path_key(path)
            if key in seen_keys:
                continue
            record = record_by_key.get(key)
            if record is None:
                continue
            prioritized_records.append(record)
            seen_keys.add(key)
        remaining_records = [record for record in self.records if normalized_path_key(record.path) not in seen_keys]
        return prioritized_records, remaining_records

    def _hydrate_records_batch(
        self,
        store: DecisionStore,
        records: list[ImageRecord],
    ) -> dict[str, SessionAnnotation]:
        if not records or self._cancelled:
            return {}
        records_by_path = {record.path: record for record in records}
        persisted = store.load_annotations_for_paths(
            self.session_id,
            records_by_path,
            list(records_by_path),
        )
        hydrated: dict[str, SessionAnnotation] = {}
        for record in records:
            if self._cancelled:
                return {}
            sidecar = load_sidecar_annotation(record.path)
            if not sidecar.is_empty:
                hydrated[record.path] = sidecar
            persisted_annotation = persisted.get(record.path)
            if persisted_annotation is not None and not persisted_annotation.is_empty:
                hydrated[record.path] = persisted_annotation
        return hydrated

    def run(self) -> None:
        try:
            if self._cancelled:
                return
            store = DecisionStore()
            prioritized_records, remaining_records = self._partition_records()

            for batch in self._record_batches(prioritized_records, self.PRIORITY_BATCH_SIZE):
                if self._cancelled:
                    return
                chunk = self._hydrate_records_batch(store, batch)
                if chunk:
                    self.signals.chunk.emit(self.scope_key, self.token, dict(chunk))

            for batch in self._record_batches(remaining_records, self.BACKGROUND_BATCH_SIZE):
                if self._cancelled:
                    return
                chunk = self._hydrate_records_batch(store, batch)
                if chunk:
                    self.signals.chunk.emit(self.scope_key, self.token, dict(chunk))

            if self._cancelled:
                return
            self.signals.finished.emit(self.scope_key, self.token)
        except Exception as exc:  # pragma: no cover - desktop/runtime path
            self.signals.failed.emit(self.scope_key, self.token, str(exc))


class ScopeEnrichmentSignals(QObject):
    finished = Signal(str, int, object, object, object)
    failed = Signal(str, int, str)


class ScopeEnrichmentTask(QRunnable):
    def __init__(
        self,
        *,
        scope_key: str,
        token: int,
        session_id: str,
        folder_path: str,
        include_all_scope_events: bool,
        records: tuple[ImageRecord, ...],
        ai_bundle: AIBundle | None,
        review_bundle: ReviewIntelligenceBundle | None,
    ) -> None:
        super().__init__()
        self.scope_key = scope_key
        self.token = token
        self.session_id = session_id
        self.folder_path = folder_path
        self.include_all_scope_events = include_all_scope_events
        self.records = records
        self.ai_bundle = ai_bundle
        self.review_bundle = review_bundle
        self.signals = ScopeEnrichmentSignals()
        self._cancelled = False
        self.setAutoDelete(True)

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        try:
            if self._cancelled:
                return
            store = DecisionStore()
            if self.folder_path:
                correction_events = store.load_correction_events(self.session_id, self.folder_path)
            elif self.include_all_scope_events and self.records:
                correction_events = store.load_correction_events(self.session_id)
            else:
                correction_events = []
            if self._cancelled:
                return
            taste_profile, recommendations = build_burst_recommendations(
                list(self.records),
                ai_bundle=self.ai_bundle,
                review_bundle=self.review_bundle,
                correction_events=correction_events,
                should_cancel=lambda: self._cancelled,
            )
            if self._cancelled:
                return
            self.signals.finished.emit(
                self.scope_key,
                self.token,
                correction_events,
                taste_profile,
                recommendations,
            )
        except Exception as exc:  # pragma: no cover - desktop/runtime path
            self.signals.failed.emit(self.scope_key, self.token, str(exc))


class AIModelDownloadSignals(QObject):
    started = Signal(str)
    progress = Signal(str, int, int)
    finished = Signal(str)
    failed = Signal(str)


class AIModelDownloadTask(QRunnable):
    def __init__(
        self,
        *,
        installation: AIModelInstallation,
        force: bool = False,
    ) -> None:
        super().__init__()
        self.installation = installation
        self.force = force
        self.signals = AIModelDownloadSignals()
        self.setAutoDelete(True)

    def run(self) -> None:
        try:
            self.signals.started.emit(str(self.installation.install_dir))
            download_managed_ai_model(
                self.installation,
                force=self.force,
                progress_callback=lambda filename, current, total: self.signals.progress.emit(
                    filename,
                    current,
                    total,
                ),
            )
        except Exception as exc:
            self.signals.failed.emit(str(exc))
            return
        self.signals.finished.emit(str(self.installation.install_dir))


class MainWindow(QMainWindow):
    LAST_FOLDER_KEY = "window/last_folder"
    AI_RESULTS_KEY = "window/ai_results_path"
    AUTO_BRACKET_KEY = "window/auto_bracket_compare"
    APPEARANCE_KEY = "window/appearance"
    GEOMETRY_KEY = "window/geometry"
    STATE_KEY = "window/state"
    SESSION_KEY = "workflow/session"
    WINNER_MODE_KEY = "workflow/winner_mode"
    DELETE_MODE_KEY = "workflow/delete_mode"
    WORKFLOW_PRESETS_KEY = "workflow/presets"
    CATALOG_CACHE_ENABLED_KEY = "catalog/cache_enabled"
    CATALOG_WATCH_CURRENT_FOLDER_KEY = "catalog/watch_current_folder"
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
    WORKSPACE_TOOLBAR_LAYOUT_VERSION = 1
    SHORTCUT_OVERRIDES_KEY = "shortcuts/overrides"
    AI_MODEL_PROMPTED_KEY = "ai/model_prompted"
    AI_CHECKPOINT_OVERRIDE_KEY = "ai/checkpoint_override"
    AI_REFERENCE_BANK_KEY = "ai/reference_bank_path"
    BURST_GROUPS_KEY = "view/burst_groups"
    BURST_STACKS_KEY = "view/burst_stacks"
    VIEW_COLUMNS_KEY = "view/columns"
    COMPACT_CARDS_KEY = "view/compact_cards"
    FOLDER_VIEW_STATE_KEY = "view/folder_state"
    DIALOG_GEOMETRY_KEY_PREFIX = "dialogs/geometry"
    AUTO_REVIEW_INTELLIGENCE_MAX_RECORDS = 2400
    CHUNKED_RESTORE_LOAD_MIN_RECORDS = 600
    CHUNKED_RESTORE_LOAD_BATCH_SIZE = 360
    WORKSPACE_TOOLBAR_DEFAULTS = {
        "primary": ("open_folder", "refresh_folder", "undo", "separator", "run_ai_culling", "ai_results"),
        "manual": ("review", "view", "selection_count", "search", "filters", "address"),
        "ai": ("ai_status", "review", "view", "selection_count", "search", "filters", "address"),
    }
    WORKSPACE_TOOLBAR_ALLOWED_ITEMS = {
        "primary": (
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
        ),
        "manual": (
            "review",
            "view",
            "search",
            "filters",
            "columns",
            "sort",
            "quick_filter",
            "compare",
            "auto_advance",
            "burst_groups",
            "burst_stacks",
            "compact_cards",
            "selection_count",
            "open_folder",
            "refresh_folder",
            "undo",
            "command_palette",
            "accept_selection",
            "reject_selection",
            "keep_selection",
            "move_selection",
            "delete_selection",
            "reveal_in_explorer",
            "open_in_photoshop",
            "batch_rename",
            "batch_resize",
            "batch_convert",
            "handoff_builder",
            "send_to_editor",
            "best_of_set",
            "keyboard_shortcuts",
            "address",
        ),
        "ai": (
            "ai_status",
            "run_ai_culling",
            "ai_results",
            "review",
            "view",
            "search",
            "filters",
            "columns",
            "sort",
            "quick_filter",
            "compare",
            "auto_advance",
            "burst_groups",
            "burst_stacks",
            "compact_cards",
            "selection_count",
            "next_ai_pick",
            "next_unreviewed_ai_pick",
            "compare_ai_group",
            "review_ai_disagreements",
            "taste_calibration",
            "open_folder",
            "refresh_folder",
            "undo",
            "command_palette",
            "load_saved_ai",
            "load_ai_results",
            "clear_ai_results",
            "open_ai_report",
            "accept_selection",
            "reject_selection",
            "keep_selection",
            "move_selection",
            "delete_selection",
            "reveal_in_explorer",
            "open_in_photoshop",
            "address",
        ),
    }
    WORKSPACE_TOOLBAR_ITEM_LABELS = {
        "open_folder": "Open",
        "refresh_folder": "Refresh",
        "undo": "Undo",
        "separator": "Separator",
        "run_ai_culling": "Run AI",
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
        "compact_cards": "Compact Cards",
        "selection_count": "Selected Count",
        "accept_selection": "Accept",
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
        "taste_calibration": "Calibration",
    }

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Image Triage")
        self.resize(1600, 960)
        self._settings = QSettings()
        self._workspace_toolbar_layouts = self._load_workspace_toolbar_layouts()
        self._toolbar_edit_mode = False
        self._toolbar_edit_target_mode = "manual"
        self._toolbar_edit_overlay: QFrame | None = None
        self._workspace_toolbar_item_widgets: dict[str, dict[str, QWidget]] = {}
        self._appearance_mode = parse_appearance_mode(self._settings.value(self.APPEARANCE_KEY, AppearanceMode.AUTO.value, str))
        self._theme = None
        self._child_sync_state_path = self._prepare_child_sync_state_path()
        self._child_processes: dict[int, ChildAppProcess] = {}
        self._child_process_timer = QTimer(self)
        self._child_process_timer.setInterval(1200)
        self._child_process_timer.timeout.connect(self._prune_child_processes)
        self._child_process_timer.start()
        self.actions: MainWindowActions | None = None
        self.primary_toolbar: QToolBar | None = None
        self.workspace_docks: WorkspaceDocks | None = None
        self.inspector_panel: InspectorPanel | None = None
        self._toolbar_context_mode_property = "imageTriageToolbarContextMode"
        self._toolbar_context_installed_property = "imageTriageToolbarContextInstalled"

        self.thumbnail_manager = ThumbnailManager()
        self._decision_store = DecisionStore()
        self._library_store = LibraryStore()
        self._catalog_repository = CatalogRepository()
        self._bracket_detector = BracketDetector()
        self._photoshop_executable = detect_photoshop_executable()
        self.grid = ThumbnailGridView(self.thumbnail_manager)
        self.preview = FullScreenPreview(self)
        self.preview.navigation_requested.connect(self._navigate_preview)
        self.preview.set_photoshop_available(bool(self._photoshop_executable))
        self._ai_model_installation = resolve_ai_model_installation()
        self._ai_runtime = default_ai_workflow_runtime()
        if self._ai_runtime.model_installation is not None:
            self._ai_model_installation = self._ai_runtime.model_installation
        self._default_ai_checkpoint_path = self._ai_runtime.checkpoint_path
        self._active_reference_bank_path = ""
        self._scan_pool = QThreadPool(self)
        self._scan_pool.setMaxThreadCount(1)
        self._ai_run_pool = QThreadPool(self)
        self._ai_run_pool.setMaxThreadCount(1)
        self._ai_training_pool = QThreadPool(self)
        self._ai_training_pool.setMaxThreadCount(1)
        self._ai_model_pool = QThreadPool(self)
        self._ai_model_pool.setMaxThreadCount(1)
        self._batch_rename_pool = QThreadPool(self)
        self._batch_rename_pool.setMaxThreadCount(1)
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
        self._scan_token = 0
        self._scan_showed_cached = False
        self._scan_cached_source = ""
        self._active_scan_tasks: dict[int, FolderScanTask] = {}
        self._active_ai_task: AIRunTask | None = None
        self._active_ai_model_task: AIModelDownloadTask | None = None
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
        self._deferred_enrichment_pending = False
        self._deferred_enrichment_scheduled = False
        self._deferred_enrichment_scope_key = ""
        self._deferred_enrichment_token = 0
        self._review_chunk_dirty_paths: set[str] = set()
        self._review_chunk_flush_timer = QTimer(self)
        self._review_chunk_flush_timer.setSingleShot(True)
        self._review_chunk_flush_timer.setInterval(120)
        self._review_chunk_flush_timer.timeout.connect(self._flush_review_chunk_updates)
        self._scope_enrichment_debounce_timer = QTimer(self)
        self._scope_enrichment_debounce_timer.setSingleShot(True)
        self._scope_enrichment_debounce_timer.setInterval(220)
        self._scope_enrichment_debounce_timer.timeout.connect(self._run_scope_enrichment_debounced)
        self._active_ai_training_task: object | None = None
        self._ai_training_context: AITrainingExecutionContext | None = None
        self._ai_training_pipeline: AITrainingPipelineState | None = None
        self._ai_training_progress_dialog: AITrainingProgressDialog | None = None
        self._ai_training_stats_dialog: AITrainingStatsDialog | None = None
        self._ai_training_log_lines: list[str] = []
        self._ai_training_stage_text = ""
        self._ai_training_run_label = ""
        self._active_batch_rename_task: BatchRenameApplyTask | None = None
        self._batch_rename_context: BatchRenameExecutionContext | None = None
        self._batch_rename_progress_dialog: QProgressDialog | None = None
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
        self._ai_model_job_key = "ai:model"
        self._current_folder = ""
        self._scope_kind = "folder"
        self._scope_id = ""
        self._scope_label = ""
        self._scan_in_progress = False
        self._all_records: list[ImageRecord] = []
        self._all_records_by_path: dict[str, ImageRecord] = {}
        self._records: list[ImageRecord] = []
        self._record_index_by_path: dict[str, int] = {}
        self._edited_candidates_cache: dict[str, tuple[str, ...]] = {}
        self._visible_review_group_rows_by_id: dict[str, list[int]] = {}
        self._visible_ai_group_rows_by_id: dict[str, list[int]] = {}
        self._accepted_count = 0
        self._rejected_count = 0
        self._unreviewed_count = 0
        self._records_have_resizable = False
        self._records_have_convertible = False
        self._training_label_counts_cache_key: tuple[object, ...] = ()
        self._training_label_counts_cache = (0, 0)
        self._summary_ai_text = "AI: Off"
        self._summary_ai_tooltip = "No AI export is currently loaded."
        self._annotations: dict[str, SessionAnnotation] = {}
        self._ai_bundle: AIBundle | None = None
        self._review_intelligence: ReviewIntelligenceBundle | None = None
        self._correction_events: list[dict[str, object]] = []
        self._taste_profile = TasteProfile()
        self._burst_recommendations: dict[str, BurstRecommendation] = {}
        self._workflow_insights_by_path: dict[str, RecordWorkflowInsight] = {}
        self._records_view_cache = RecordsViewCache()
        self._last_view_record_paths: tuple[str, ...] = ()
        self._chunked_load_scan_tokens: set[int] = set()
        self._records_view_chunk_timer = QTimer(self)
        self._records_view_chunk_timer.setSingleShot(True)
        self._records_view_chunk_timer.timeout.connect(self._drain_records_view_chunk)
        self._records_view_chunk_records: list[ImageRecord] = []
        self._records_view_chunk_next_index = 0
        self._records_view_chunk_current_path: str | None = None
        self._records_view_chunk_post_load_enrichment = ""
        self._winner_ladder_state: dict[str, object] | None = None
        self._ui_mode = "manual"
        self._ai_stage_index = 0
        self._ai_stage_total = 3
        self._ai_stage_message = "Ready to run AI culling"
        self._ai_progress_current = 0
        self._ai_progress_total = 0
        self._ai_progress_eta_text = ""
        self._sort_mode = SortMode.NAME
        self._filter_query = RecordFilterQuery()
        self._pending_search_text = ""
        self._auto_advance_enabled = True
        self.preview.set_auto_advance_enabled(self._auto_advance_enabled)
        self._compare_enabled = False
        self._auto_bracket_enabled = self._settings.value(self.AUTO_BRACKET_KEY, True, bool)
        self._burst_groups_enabled = self._settings.value(self.BURST_GROUPS_KEY, False, bool)
        self._burst_stacks_enabled = self._settings.value(self.BURST_STACKS_KEY, False, bool)
        self._compact_cards_enabled = self._settings.value(self.COMPACT_CARDS_KEY, False, bool)
        self._catalog_cache_enabled = self._settings.value(self.CATALOG_CACHE_ENABLED_KEY, True, bool)
        self._watch_current_folder_enabled = self._settings.value(self.CATALOG_WATCH_CURRENT_FOLDER_KEY, True, bool)
        self._catalog_load_source = "idle"
        self._catalog_load_detail = "Ready"
        self._watched_folder_path = ""
        self._folder_watch_refresh_pending = False
        self.grid.set_compact_card_mode(self._compact_cards_enabled)
        self._session_id = self._decision_store.ensure_session(
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
        self._shortcut_overrides = self._load_shortcut_overrides()
        self._shortcut_targets: dict[str, ShortcutTarget] = {}
        self._active_tool_mode = ""
        self._visible_burst_groups: list[tuple[int, ...]] = []
        self._burst_group_map: dict[str, BurstVisualInfo] = {}
        self._apply_saved_ai_training_preferences()
        self._command_palette_open = False
        self._active_command_palette: CommandPaletteDialog | None = None
        self._command_palette_dialogs: dict[str, CommandPaletteDialog] = {}
        self._command_palette_shortcut_main: QShortcut | None = None
        self._command_palette_shortcut_preview: QShortcut | None = None
        self._compare_count = 3
        self._manual_compare_count = 3
        self._undo_stack: list[UndoAction] = []
        self._file_type_actions: dict[FileTypeFilter, QAction] = {}
        self._review_state_actions: dict[ReviewStateFilter, QAction] = {}
        self._ai_state_actions: dict[AIStateFilter, QAction] = {}
        self._annotation_persistence_queue = AnnotationPersistenceQueue(parent=self)
        self._annotation_persistence_queue.failed.connect(self._handle_annotation_persist_failed)
        self._annotation_persistence_queue.warning.connect(self._handle_annotation_persist_warning)

        self._search_apply_timer = QTimer(self)
        self._search_apply_timer.setSingleShot(True)
        self._search_apply_timer.setInterval(140)
        self._search_apply_timer.timeout.connect(self._commit_search_text_filter)
        self._filter_metadata_manager = MetadataManager(max_workers=2, parent=self)
        self._filter_metadata_manager.metadata_ready.connect(self._handle_filter_metadata_ready)
        self._filter_metadata_by_path: dict[str, CaptureMetadata] = {}
        self._filter_metadata_record_paths: set[str] = set()
        self._filter_metadata_loaded_paths: set[str] = set()
        self._filter_metadata_requested_paths: set[str] = set()
        self._filter_metadata_queue: deque[str] = deque()
        self._filter_metadata_queue_limit = 720
        self._metadata_membership_dirty_paths: set[str] = set()
        self._metadata_scroll_last_value = 0
        self._metadata_scroll_direction = 1
        self._metadata_scroll_prefetch_timer = QTimer(self)
        self._metadata_scroll_prefetch_timer.setSingleShot(True)
        self._metadata_scroll_prefetch_timer.setInterval(80)
        self._metadata_scroll_prefetch_timer.timeout.connect(self._run_metadata_scroll_prefetch)
        self._metadata_request_timer = QTimer(self)
        self._metadata_request_timer.setInterval(45)
        self._metadata_request_timer.timeout.connect(self._drain_filter_metadata_requests)
        self._metadata_reapply_timer = QTimer(self)
        self._metadata_reapply_timer.setSingleShot(True)
        self._metadata_reapply_timer.setInterval(180)
        self._metadata_reapply_timer.timeout.connect(self._handle_metadata_filter_batch_update)
        self._folder_watcher = QFileSystemWatcher(self)
        self._folder_watcher.directoryChanged.connect(self._handle_watched_folder_changed)
        self._folder_watch_refresh_timer = QTimer(self)
        self._folder_watch_refresh_timer.setSingleShot(True)
        self._folder_watch_refresh_timer.setInterval(900)
        self._folder_watch_refresh_timer.timeout.connect(self._run_watched_folder_refresh)

        self.folder_model = QFileSystemModel(self)
        self.folder_model.setFilter(QDir.Filter.AllDirs | QDir.Filter.NoDotAndDotDot | QDir.Filter.Drives)
        self.folder_model.setRootPath("")

        self.folder_tree = QTreeView()
        self.folder_tree.setObjectName("folderTree")
        self.folder_tree.setModel(self.folder_model)
        self.folder_tree.setRootIndex(QModelIndex())
        self.folder_tree.setHeaderHidden(True)
        self.folder_tree.header().hide()
        for column in range(1, self.folder_model.columnCount()):
            self.folder_tree.hideColumn(column)
        self.folder_tree.clicked.connect(self._handle_tree_selection)
        self.folder_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.folder_tree.customContextMenuRequested.connect(self._show_folder_tree_context_menu)
        self.folder_tree.setAcceptDrops(True)
        self.folder_tree.viewport().setAcceptDrops(True)
        self.folder_tree.viewport().installEventFilter(self)

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

        self.favorites_divider = QFrame()
        self.favorites_divider.setFrameShape(QFrame.Shape.HLine)
        self.favorites_divider.setObjectName("sectionDivider")

        self.library_label = QLabel("Folders")
        self.library_label.setObjectName("sectionLabel")

        self.left_panel = QWidget()
        self.left_panel.setObjectName("libraryPanelContent")
        self.left_panel.setMinimumWidth(280)
        left_layout = QVBoxLayout(self.left_panel)
        left_layout.setContentsMargins(10, 10, 10, 10)
        left_layout.setSpacing(10)
        left_layout.addWidget(self.favorites_label)
        left_layout.addWidget(self.favorites_list)
        left_layout.addWidget(self.favorites_divider)
        left_layout.addWidget(self.library_label)
        left_layout.addWidget(self.folder_tree, 1)
        self._refresh_favorites_panel()

        self.manual_path_combo = self._build_path_combo(mode="manual")
        self.ai_path_combo = self._build_path_combo(mode="ai")
        self.manual_selection_count_label = self._build_selection_count_label(mode="manual")
        self.ai_selection_count_label = self._build_selection_count_label(mode="ai")

        self.sort_combo = QComboBox()
        for mode in SortMode:
            self.sort_combo.addItem(mode.value, mode)
        self.sort_combo.currentIndexChanged.connect(self._handle_sort_changed)

        self.filter_combo = QComboBox()
        for mode in FilterMode:
            self.filter_combo.addItem(mode.value, mode)
        self.filter_combo.currentIndexChanged.connect(self._handle_filter_changed)

        self.columns_combo = QComboBox()
        for count in range(1, 9):
            self.columns_combo.addItem(f"{count} Across", count)
        saved_columns = self._normalize_column_count(self._settings.value(self.VIEW_COLUMNS_KEY, 3, int))
        default_columns_index = self.columns_combo.findData(saved_columns)
        self.columns_combo.setCurrentIndex(default_columns_index if default_columns_index >= 0 else 0)
        self.grid.set_column_count(saved_columns)
        self.columns_combo.currentIndexChanged.connect(self._handle_columns_changed)

        self.actions = build_main_window_actions(self)
        self._setup_command_palette_shortcuts()
        self._register_shortcut_targets()
        self._apply_shortcut_overrides()
        self._build_record_filter_actions()
        self.primary_toolbar = build_primary_toolbar(self, self.actions)
        self._configure_toolbar_context_target(self.primary_toolbar, "primary")
        self.primary_toolbar.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._rebuild_primary_toolbar()
        self.inspector_panel = InspectorPanel()
        self.inspector_panel.setMinimumWidth(260)
        self.workspace_preset_menu = QMenu(self)
        self.workflow_recipe_menu = QMenu("Run Recipe", self)
        self.collections_menu = QMenu("Collections", self)
        self.catalog_menu = QMenu("Catalog", self)

        self.manual_search_field = self._build_search_field()
        self.ai_search_field = self._build_search_field()
        self.manual_search_field.textChanged.connect(
            lambda text: self._handle_search_text_changed(text, source="manual")
        )
        self.ai_search_field.textChanged.connect(
            lambda text: self._handle_search_text_changed(text, source="ai")
        )
        self.filter_toolbar_menu = QMenu(self)
        self.manual_filter_button = self._build_advanced_filter_button()
        self.ai_filter_button = self._build_advanced_filter_button()
        self.view_toolbar_menu = self._build_view_toolbar_menu()
        self.manual_review_tools_button = self._build_popup_button(
            "Review",
            self._build_review_toolbar_menu(),
        )
        self.ai_review_tools_button = self._build_popup_button(
            "Review",
            self._build_review_toolbar_menu(),
        )
        self.manual_view_tools_button = self._build_popup_button("View", self.view_toolbar_menu)
        self.ai_view_tools_button = self._build_popup_button("View", self.view_toolbar_menu)
        for button in (self.manual_view_tools_button, self.ai_view_tools_button):
            button.setToolTip("Quick filters, sort options, and column layout.")

        self.manual_toolbar = QWidget()
        self.manual_toolbar.setObjectName("workspaceControls")
        self._configure_toolbar_context_target(self.manual_toolbar, "manual")
        self.manual_toolbar_layout = QHBoxLayout(self.manual_toolbar)
        self.manual_toolbar_layout.setContentsMargins(0, 0, 0, 0)
        self.manual_toolbar_layout.setSpacing(8)
        self.ai_progress_bar = QProgressBar()
        self.ai_progress_bar.setRange(0, 1)
        self.ai_progress_bar.setValue(0)
        self.ai_progress_bar.setFormat("Idle")
        self.ai_progress_bar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.ai_progress_bar.setTextVisible(True)
        self.ai_progress_bar.setMinimumWidth(150)
        self.ai_progress_bar.setMaximumWidth(210)
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

        self.ai_toolbar = QWidget()
        self.ai_toolbar.setObjectName("workspaceControls")
        self._configure_toolbar_context_target(self.ai_toolbar, "ai")
        self.ai_toolbar_layout = QHBoxLayout(self.ai_toolbar)
        self.ai_toolbar_layout.setContentsMargins(0, 0, 0, 0)
        self.ai_toolbar_layout.setSpacing(8)
        self._workspace_toolbar_item_widgets = {
            "manual": self._build_workspace_toolbar_widgets("manual"),
            "ai": self._build_workspace_toolbar_widgets("ai"),
        }
        self._rebuild_workspace_toolbar("manual")
        self._rebuild_workspace_toolbar("ai")

        self.mode_tabs = QTabBar()
        self.mode_tabs.setObjectName("modeTabs")
        self.mode_tabs.addTab("Manual Review")
        self.mode_tabs.addTab("AI Culling")
        self.mode_tabs.setExpanding(False)
        self.mode_tabs.setDrawBase(False)
        self.mode_tabs.setElideMode(Qt.TextElideMode.ElideNone)
        self.mode_tabs.setUsesScrollButtons(False)
        self.mode_tabs.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.mode_tabs.currentChanged.connect(self._handle_mode_tab_changed)

        self.toolbar_stack = QStackedWidget()
        self.toolbar_stack.addWidget(self.manual_toolbar)
        self.toolbar_stack.addWidget(self.ai_toolbar)
        self._configure_toolbar_context_target(self.toolbar_stack, "workspace")

        self.workspace_bar = QWidget()
        self.workspace_bar.setObjectName("workspaceBar")
        self._configure_toolbar_context_target(self.workspace_bar, "workspace")
        workspace_bar_layout = QHBoxLayout(self.workspace_bar)
        workspace_bar_layout.setContentsMargins(12, 8, 12, 8)
        workspace_bar_layout.setSpacing(12)
        workspace_bar_layout.addWidget(self.mode_tabs, 0, Qt.AlignmentFlag.AlignVCenter)
        workspace_bar_layout.addWidget(self.toolbar_stack, 1)
        self._refresh_mode_tabs_width()

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
        self.tool_mode_run_button = QPushButton("Run")
        self.tool_mode_cancel_button = QPushButton("Cancel")
        self.tool_mode_run_button.clicked.connect(self._run_active_tool_mode)
        self.tool_mode_cancel_button.clicked.connect(self._cancel_tool_mode)
        tool_mode_layout.addWidget(self.tool_mode_title)
        tool_mode_layout.addWidget(self.tool_mode_help, 1)
        tool_mode_layout.addWidget(self.tool_mode_selection)
        tool_mode_layout.addWidget(self.tool_mode_run_button)
        tool_mode_layout.addWidget(self.tool_mode_cancel_button)
        self.tool_mode_bar.hide()

        center_column = QWidget()
        center_column.setObjectName("workspaceCenterColumn")
        center_layout = QVBoxLayout(center_column)
        center_layout.setContentsMargins(0, 0, 0, 0)
        center_layout.setSpacing(8)
        center_layout.addWidget(self.primary_toolbar)
        center_layout.addWidget(self.workspace_bar)
        center_layout.addWidget(self.tool_mode_bar)
        center_layout.addWidget(self.grid, 1)

        self.workspace_docks = build_workspace_docks(self, self.left_panel, self.inspector_panel, center_column)
        self._refresh_workspace_preset_menu()
        self._refresh_workflow_recipe_menu()
        self._refresh_collections_menu()
        self._refresh_catalog_menu()
        build_main_menu_bar(
            self,
            self.actions,
            self.workspace_docks.toggle_actions,
            workflow_recipe_menu=self.workflow_recipe_menu,
            workspace_preset_menu=self.workspace_preset_menu,
            collections_menu=self.collections_menu,
            catalog_menu=self.catalog_menu,
        )

        self.summary_strip = QWidget()
        self.summary_strip.setObjectName("summaryStrip")
        summary_layout = QHBoxLayout(self.summary_strip)
        summary_layout.setContentsMargins(10, 4, 10, 4)
        summary_layout.setSpacing(6)
        self.summary_total = QLabel("Total: 0")
        self.summary_selected = QLabel("Selected: 0")
        self.summary_accepted = QLabel("Accepted: 0")
        self.summary_rejected = QLabel("Rejected: 0")
        self.summary_unreviewed = QLabel("Unreviewed: 0")
        self.summary_ai = QLabel("AI: Off")
        self.summary_session = QLabel(f"Session: {self._session_id}")
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
        layout.setContentsMargins(12, 8, 12, 12)
        layout.setSpacing(8)
        layout.addWidget(self.workspace_docks.shell, 1)
        self.setCentralWidget(container)
        self.summary_strip.hide()
        self._apply_default_workspace()

        status = QStatusBar()
        status.showMessage("Ready")
        self.setStatusBar(status)
        self.filter_summary_label = QLabel("Filters: All Images")
        self.filter_summary_label.setObjectName("filterSummaryLabel")
        self.filter_summary_label.setMaximumWidth(420)
        self.catalog_status_label = QLabel("")
        self.catalog_status_label.setObjectName("filterSummaryLabel")
        self.catalog_status_label.setMaximumWidth(260)
        self.clear_filters_button = QToolButton()
        self.clear_filters_button.setObjectName("statusFilterClearButton")
        self.clear_filters_button.setAutoRaise(True)
        self.clear_filters_button.setDefaultAction(self.actions.clear_filters)
        status.addPermanentWidget(self.catalog_status_label)
        status.addPermanentWidget(self.filter_summary_label)
        status.addPermanentWidget(self.clear_filters_button)
        self._refresh_catalog_status_indicator()
        self._refresh_filter_toolbar_menu()
        self._refresh_recent_folder_combos()

        self.grid.current_changed.connect(self._handle_current_changed)
        self.grid.preview_requested.connect(self._open_preview)
        self.grid.delete_requested.connect(self._delete_record)
        self.grid.keep_requested.connect(self._keep_record)
        self.grid.move_requested.connect(self._move_record_prompt)
        self.grid.rate_requested.connect(self._rate_record)
        self.grid.tag_requested.connect(self._tag_record)
        self.grid.winner_requested.connect(self._toggle_winner)
        self.grid.reject_requested.connect(self._toggle_reject)
        self.grid.context_menu_requested.connect(self._show_grid_context_menu)
        self.grid.selection_changed.connect(self._handle_grid_selection_changed)
        self.grid.verticalScrollBar().valueChanged.connect(self._schedule_metadata_scroll_prefetch)
        self.preview.compare_mode_changed.connect(self._handle_preview_compare_mode_changed)
        self.preview.auto_bracket_mode_changed.connect(self._handle_preview_auto_bracket_mode_changed)
        self.preview.compare_count_changed.connect(self._handle_preview_compare_count_changed)
        self.preview.photoshop_requested.connect(self._open_preview_image_in_photoshop)
        self.preview.winner_requested.connect(self._handle_preview_winner_requested)
        self.preview.reject_requested.connect(self._handle_preview_reject_requested)
        self.preview.keep_requested.connect(self._handle_preview_keep_requested)
        self.preview.delete_requested.connect(self._handle_preview_delete_requested)
        self.preview.move_requested.connect(self._handle_preview_move_requested)
        self.preview.rate_requested.connect(self._handle_preview_rate_requested)
        self.preview.tag_requested.connect(self._handle_preview_tag_requested)
        self.preview.winner_ladder_choice_requested.connect(self._handle_preview_winner_ladder_choice)
        self.preview.winner_ladder_skip_requested.connect(self._handle_preview_winner_ladder_skip)
        self.preview.closed.connect(self._handle_preview_closed)

        app = QApplication.instance()
        if app is not None:
            style_hints = app.styleHints()
            color_scheme_changed = getattr(style_hints, "colorSchemeChanged", None)
            if color_scheme_changed is not None:
                color_scheme_changed.connect(self._handle_system_color_scheme_changed)
        self._apply_appearance()
        self._restore_window_state()
        self._sync_record_filter_controls()
        self._update_filter_summary()
        self.preview.set_auto_bracket_mode(self._auto_bracket_enabled)
        self._handle_mode_tab_changed(self.mode_tabs.currentIndex())
        self._update_action_states()
        QTimer.singleShot(0, self._finish_startup_restore)

    def _build_section_label(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("sectionLabel")
        return label

    def _make_action_button(self, action) -> QToolButton:
        button = QToolButton()
        button.setDefaultAction(action)
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        return button

    def _build_popup_button(self, text: str, menu: QMenu) -> QToolButton:
        button = QToolButton()
        button.setObjectName("workspacePresetsButton")
        button.setText(text)
        button.setToolTip(text)
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        button.setMenu(menu)
        return button

    def _build_review_toolbar_menu(self) -> QMenu:
        menu = QMenu(self)
        menu.addAction(self.actions.compare_mode)
        menu.addAction(self.actions.auto_advance)
        menu.addSeparator()
        menu.addAction(self.actions.burst_groups)
        menu.addAction(self.actions.burst_stacks)
        return menu

    def _build_view_toolbar_menu(self) -> QMenu:
        menu = QMenu(self)

        quick_filter_menu = menu.addMenu("Quick Filter")
        for mode in FilterMode:
            quick_filter_menu.addAction(self.actions.filter_actions[mode])

        sort_menu = menu.addMenu("Sort")
        for mode in SortMode:
            sort_menu.addAction(self.actions.sort_actions[mode])

        columns_menu = menu.addMenu("Columns")
        for count in range(1, 9):
            columns_menu.addAction(self.actions.column_actions[count])

        menu.addSeparator()
        menu.addAction(self.actions.compact_cards)

        return menu

    def _build_search_field(self) -> QLineEdit:
        field = QLineEdit()
        field.setObjectName("workspaceSearchField")
        field.setClearButtonEnabled(True)
        field.setPlaceholderText("Search filenames")
        field.setMinimumWidth(124)
        field.setMaximumWidth(220)
        field.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        return field

    def _build_path_combo(self, *, mode: str) -> QComboBox:
        combo = QComboBox()
        combo.setObjectName("pathComboBox")
        combo.setEditable(True)
        combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        combo.setDuplicatesEnabled(False)
        combo.setMinimumWidth(260)
        combo.setMaximumWidth(460)
        combo.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        combo.setToolTip("Type a folder path or choose a recent folder")
        line_edit = combo.lineEdit()
        if line_edit is not None:
            line_edit.setPlaceholderText("No folder selected")
            line_edit.setClearButtonEnabled(False)
            line_edit.returnPressed.connect(lambda target=combo: self._commit_path_combo_text(target))
        combo.activated.connect(lambda index, target=combo: self._handle_path_combo_activated(target, index))
        self._configure_toolbar_context_target(combo, mode)
        return combo

    def _build_selection_count_label(self, *, mode: str) -> QLabel:
        label = QLabel("0 selected")
        label.setObjectName("toolbarSelectionCount")
        label.setMinimumWidth(76)
        label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        label.setToolTip("Selected images in the current view")
        self._configure_toolbar_context_target(label, mode)
        return label

    def _build_advanced_filter_button(self) -> QToolButton:
        button = QToolButton()
        button.setObjectName("workspaceFiltersButton")
        button.setText("Filters")
        button.setToolTip("Advanced filters and saved searches")
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        button.setMenu(self.filter_toolbar_menu)
        return button

    def _build_ai_results_menu(self) -> QMenu:
        menu = QMenu("AI Results", self)
        menu.addAction(self.actions.load_saved_ai)
        menu.addAction(self.actions.load_ai_results)
        menu.addAction(self.actions.clear_ai_results)
        menu.addSeparator()
        menu.addAction(self.actions.open_ai_report)
        return menu

    def _build_columns_toolbar_menu(self) -> QMenu:
        menu = QMenu("Columns", self)
        for count in range(1, 9):
            menu.addAction(self.actions.column_actions[count])
        return menu

    def _build_sort_toolbar_menu(self) -> QMenu:
        menu = QMenu("Sort", self)
        for mode in SortMode:
            menu.addAction(self.actions.sort_actions[mode])
        return menu

    def _build_quick_filter_toolbar_menu(self) -> QMenu:
        menu = QMenu("Quick Filter", self)
        for mode in FilterMode:
            menu.addAction(self.actions.filter_actions[mode])
        return menu

    def _build_primary_toolbar_menu_button(self, text: str, menu: QMenu, *, min_width: int = 96) -> QToolButton:
        button = QToolButton(self.primary_toolbar)
        button.setObjectName("primaryToolbarButton")
        button.setText(text)
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        button.setMenu(menu)
        button.setMinimumWidth(min_width)
        button.setToolTip(text)
        self._configure_toolbar_context_target(button, "primary")
        return button

    def _add_primary_toolbar_action(self, action: QAction, text: str, *, min_width: int = 88) -> None:
        action.setIconText(text)
        self.primary_toolbar.addAction(action)
        button = self.primary_toolbar.widgetForAction(action)
        if isinstance(button, QToolButton):
            button.setObjectName("primaryToolbarButton")
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
            button.setMinimumWidth(min_width)
            self._configure_toolbar_context_target(button, "primary")

    def _rebuild_primary_toolbar(self) -> None:
        if self.primary_toolbar is None or self.actions is None:
            return
        self.primary_toolbar.clear()
        action_items = {
            "open_folder": (self.actions.open_folder, "Open", 96),
            "refresh_folder": (self.actions.refresh_folder, "Refresh", 108),
            "undo": (self.actions.undo, "Undo", 92),
            "run_ai_culling": (self.actions.run_ai_culling, "Run AI", 98),
            "command_palette": (self.actions.open_command_palette, "Command", 106),
            "advanced_filters": (self.actions.advanced_filters, "Adv. Filters", 108),
            "clear_filters": (self.actions.clear_filters, "Clear", 88),
            "batch_rename": (self.actions.batch_rename_selection, "Rename", 92),
            "batch_resize": (self.actions.batch_resize_selection, "Resize", 92),
            "batch_convert": (self.actions.batch_convert_selection, "Convert", 98),
            "handoff_builder": (self.actions.handoff_builder, "Handoff", 98),
            "send_to_editor": (self.actions.send_to_editor_pipeline, "Editor", 92),
            "best_of_set": (self.actions.best_of_set_auto_assembly, "Best Of", 92),
            "keyboard_shortcuts": (self.actions.keyboard_shortcuts, "Shortcuts", 108),
        }
        menu_items = {
            "ai_results": ("AI Results", self._build_ai_results_menu, 104),
            "columns": ("Columns", self._build_columns_toolbar_menu, 94),
            "sort": ("Sort", self._build_sort_toolbar_menu, 82),
            "quick_filter": ("Quick Filter", self._build_quick_filter_toolbar_menu, 112),
        }
        for item_id in self._workspace_toolbar_layouts.get("primary", []):
            if item_id == "separator":
                self.primary_toolbar.addSeparator()
                continue
            action_entry = action_items.get(item_id)
            if action_entry is not None:
                action, text, min_width = action_entry
                self._add_primary_toolbar_action(action, text, min_width=min_width)
                continue
            menu_entry = menu_items.get(item_id)
            if menu_entry is not None:
                text, menu_factory, min_width = menu_entry
                self.primary_toolbar.addWidget(
                    self._build_primary_toolbar_menu_button(text, menu_factory(), min_width=min_width)
                )
        if self.primary_toolbar.layout() is not None:
            self.primary_toolbar.layout().setContentsMargins(2, 2, 2, 2)
            self.primary_toolbar.layout().setSpacing(2)

    def _build_workspace_action_button(self, action: QAction, text: str) -> QToolButton:
        button = QToolButton()
        button.setObjectName("workspacePresetsButton")
        button.setDefaultAction(action)
        button.setText(text)
        button.setToolTip(action.toolTip() or text)
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        return button

    def _build_workspace_toolbar_widgets(self, mode: str) -> dict[str, QWidget]:
        if mode == "ai":
            widgets: dict[str, QWidget] = {
                "ai_status": self.ai_status_widget,
                "review": self.ai_review_tools_button,
                "view": self.ai_view_tools_button,
                "search": self.ai_search_field,
                "filters": self.ai_filter_button,
                "address": self.ai_path_combo,
                "selection_count": self.ai_selection_count_label,
            }
        else:
            widgets = {
                "review": self.manual_review_tools_button,
                "view": self.manual_view_tools_button,
                "search": self.manual_search_field,
                "filters": self.manual_filter_button,
                "address": self.manual_path_combo,
                "selection_count": self.manual_selection_count_label,
            }

        menu_factories = {
            "columns": ("Columns", self._build_columns_toolbar_menu),
            "sort": ("Sort", self._build_sort_toolbar_menu),
            "quick_filter": ("Quick Filter", self._build_quick_filter_toolbar_menu),
            "ai_results": ("AI Results", self._build_ai_results_menu),
        }
        for item_id, (text, factory) in menu_factories.items():
            widgets[item_id] = self._build_popup_button(text, factory())

        action_items = {
            "open_folder": (self.actions.open_folder, "Open"),
            "refresh_folder": (self.actions.refresh_folder, "Refresh"),
            "undo": (self.actions.undo, "Undo"),
            "run_ai_culling": (self.actions.run_ai_culling, "Run AI"),
            "command_palette": (self.actions.open_command_palette, "Command"),
            "advanced_filters": (self.actions.advanced_filters, "Adv. Filters"),
            "clear_filters": (self.actions.clear_filters, "Clear"),
            "batch_rename": (self.actions.batch_rename_selection, "Rename"),
            "batch_resize": (self.actions.batch_resize_selection, "Resize"),
            "batch_convert": (self.actions.batch_convert_selection, "Convert"),
            "handoff_builder": (self.actions.handoff_builder, "Handoff"),
            "send_to_editor": (self.actions.send_to_editor_pipeline, "Editor"),
            "best_of_set": (self.actions.best_of_set_auto_assembly, "Best Of"),
            "keyboard_shortcuts": (self.actions.keyboard_shortcuts, "Shortcuts"),
            "compare": (self.actions.compare_mode, "Compare"),
            "auto_advance": (self.actions.auto_advance, "Auto"),
            "burst_groups": (self.actions.burst_groups, "Groups"),
            "burst_stacks": (self.actions.burst_stacks, "Stacks"),
            "compact_cards": (self.actions.compact_cards, "Compact"),
            "accept_selection": (self.actions.accept_selection, "Accept"),
            "reject_selection": (self.actions.reject_selection, "Reject"),
            "keep_selection": (self.actions.keep_selection, "Keep"),
            "move_selection": (self.actions.move_selection, "Move"),
            "delete_selection": (self.actions.delete_selection, "Delete"),
            "reveal_in_explorer": (self.actions.reveal_in_explorer, "Reveal"),
            "open_in_photoshop": (self.actions.open_in_photoshop, "Photoshop"),
            "load_saved_ai": (self.actions.load_saved_ai, "Load Saved"),
            "load_ai_results": (self.actions.load_ai_results, "Load AI"),
            "clear_ai_results": (self.actions.clear_ai_results, "Clear AI"),
            "open_ai_report": (self.actions.open_ai_report, "Report"),
            "next_ai_pick": (self.actions.next_ai_pick, "Next Pick"),
            "next_unreviewed_ai_pick": (self.actions.next_unreviewed_ai_pick, "Next Unreviewed"),
            "compare_ai_group": (self.actions.compare_ai_group, "AI Compare"),
            "review_ai_disagreements": (self.actions.review_ai_disagreements, "Disagree"),
            "taste_calibration": (self.actions.taste_calibration_wizard, "Calibrate"),
        }
        for item_id, (action, text) in action_items.items():
            widgets[item_id] = self._build_workspace_action_button(action, text)
        return widgets

    def _load_workspace_toolbar_layouts(self) -> dict[str, list[str]]:
        layouts = {mode: list(items) for mode, items in self.WORKSPACE_TOOLBAR_DEFAULTS.items()}
        raw_state = self._settings.value(self.WORKSPACE_TOOLBAR_LAYOUT_KEY, "", str)
        if not isinstance(raw_state, str) or not raw_state:
            return layouts
        try:
            payload = json.loads(raw_state)
        except (TypeError, ValueError):
            return layouts
        if not isinstance(payload, dict):
            return layouts
        raw_layouts = payload.get("toolbars", payload)
        if not isinstance(raw_layouts, dict):
            return layouts
        for mode in self.WORKSPACE_TOOLBAR_DEFAULTS:
            raw_items = raw_layouts.get(mode)
            if isinstance(raw_items, list):
                layouts[mode] = self._normalize_workspace_toolbar_items(mode, raw_items)
        return layouts

    def _normalize_workspace_toolbar_items(self, mode: str, raw_items: list[object] | tuple[object, ...]) -> list[str]:
        allowed = set(self.WORKSPACE_TOOLBAR_ALLOWED_ITEMS.get(mode, ()))
        normalized: list[str] = []
        for item in raw_items:
            if not isinstance(item, str) or item not in allowed or item in normalized:
                continue
            normalized.append(item)
        return normalized

    def _save_workspace_toolbar_layouts(self) -> None:
        payload = {
            "version": self.WORKSPACE_TOOLBAR_LAYOUT_VERSION,
            "toolbars": self._workspace_toolbar_layouts,
        }
        self._settings.setValue(self.WORKSPACE_TOOLBAR_LAYOUT_KEY, json.dumps(payload))

    def _clear_layout_items(self, layout, *, delete_widgets: bool = False) -> None:
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is None:
                child_layout = item.layout()
                if child_layout is not None:
                    self._clear_layout_items(child_layout, delete_widgets=delete_widgets)
                continue
            if delete_widgets:
                widget.deleteLater()
            else:
                widget.setParent(None)

    def _set_workspace_toolbar_controls_enabled(self, enabled: bool) -> None:
        for widgets in getattr(self, "_workspace_toolbar_item_widgets", {}).values():
            for widget in widgets.values():
                widget.setEnabled(enabled)

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

    def _rebuild_workspace_toolbar(self, mode: str) -> None:
        if mode == "ai":
            layout = self.ai_toolbar_layout
        else:
            layout = self.manual_toolbar_layout
            mode = "manual"
        self._clear_layout_items(layout)
        widgets = self._workspace_toolbar_item_widgets.get(mode, {})
        for item_id in self._workspace_toolbar_layouts.get(mode, []):
            widget = widgets.get(item_id)
            if widget is None:
                continue
            self._configure_toolbar_context_target(widget, mode)
            layout.addWidget(widget)
        layout.addStretch(1)

    def _enter_toolbar_edit_mode(self) -> None:
        self._show_workspace_toolbar_editor(self._ui_mode)

    def _show_workspace_toolbar_editor(self, mode: str | None = None) -> None:
        target_mode = mode if mode in self.WORKSPACE_TOOLBAR_DEFAULTS else self._ui_mode
        dialog = ToolbarCustomizerDialog(
            layouts=self._workspace_toolbar_layouts,
            allowed_items=self.WORKSPACE_TOOLBAR_ALLOWED_ITEMS,
            labels=self.WORKSPACE_TOOLBAR_ITEM_LABELS,
            current_mode=target_mode,
            parent=self,
        )
        if self._exec_dialog_with_geometry(dialog, "toolbar_customizer") != QDialog.DialogCode.Accepted:
            return
        self._workspace_toolbar_layouts = dialog.toolbar_layouts()
        self._save_workspace_toolbar_layouts()
        self._rebuild_primary_toolbar()
        self._rebuild_workspace_toolbar("manual")
        self._rebuild_workspace_toolbar("ai")
        self._update_ai_toolbar_state()
        self.statusBar().showMessage("Updated toolbar layout.")

    def _hide_workspace_toolbar_editor(self) -> None:
        if not self._toolbar_edit_mode:
            return
        self._toolbar_edit_mode = False
        if self._toolbar_edit_overlay is not None:
            layout = self._toolbar_edit_overlay.layout()
            if layout is not None:
                self._clear_layout_items(layout, delete_widgets=True)
            self._toolbar_edit_overlay.hide()
        self._rebuild_primary_toolbar()
        self._rebuild_workspace_toolbar("manual")
        self._rebuild_workspace_toolbar("ai")
        self._set_workspace_toolbar_controls_enabled(True)
        self._update_ai_toolbar_state()
        self.statusBar().showMessage("Toolbar layout updated.")

    def _position_workspace_toolbar_editor(self) -> None:
        if self._toolbar_edit_overlay is None:
            return
        parent = self._toolbar_edit_overlay.parentWidget()
        if parent is None:
            return
        self._toolbar_edit_overlay.setGeometry(parent.rect())
        self._toolbar_edit_overlay.raise_()

    def _available_toolbar_items_for_mode(self, mode: str) -> list[str]:
        active = set(self._workspace_toolbar_layouts.get(mode, []))
        return [item for item in self.WORKSPACE_TOOLBAR_ALLOWED_ITEMS.get(mode, ()) if item not in active]

    def _select_toolbar_edit_target_mode(self, mode: str) -> None:
        if mode not in self.WORKSPACE_TOOLBAR_DEFAULTS:
            return
        self._toolbar_edit_target_mode = mode
        if mode in {"manual", "ai"}:
            target_index = 1 if mode == "ai" else 0
            if self.mode_tabs.currentIndex() != target_index:
                self.mode_tabs.setCurrentIndex(target_index)
        self._rebuild_workspace_toolbar_editor()

    def _rebuild_toolbar_for_mode(self, mode: str) -> None:
        if mode == "primary":
            self._rebuild_primary_toolbar()
            return
        if mode in {"manual", "ai"}:
            self._rebuild_workspace_toolbar(mode)

    def _add_workspace_toolbar_item(self, mode: str, item_id: str) -> None:
        if item_id not in self.WORKSPACE_TOOLBAR_ALLOWED_ITEMS.get(mode, ()):
            return
        items = self._workspace_toolbar_layouts.setdefault(mode, [])
        if item_id in items:
            return
        items.append(item_id)
        self._save_workspace_toolbar_layouts()
        self._rebuild_toolbar_for_mode(mode)
        self._rebuild_workspace_toolbar_editor()

    def _remove_workspace_toolbar_item(self, mode: str, item_id: str) -> None:
        items = self._workspace_toolbar_layouts.get(mode)
        if not items or item_id not in items:
            return
        items.remove(item_id)
        self._save_workspace_toolbar_layouts()
        self._rebuild_toolbar_for_mode(mode)
        self._rebuild_workspace_toolbar_editor()

    def _move_workspace_toolbar_item(self, mode: str, item_id: str, direction: int) -> None:
        items = self._workspace_toolbar_layouts.get(mode)
        if not items or item_id not in items:
            return
        index = items.index(item_id)
        target = index + direction
        if target < 0 or target >= len(items):
            return
        items[index], items[target] = items[target], items[index]
        self._save_workspace_toolbar_layouts()
        self._rebuild_toolbar_for_mode(mode)
        self._rebuild_workspace_toolbar_editor()

    def _reset_workspace_toolbar_items(self, mode: str) -> None:
        self._workspace_toolbar_layouts[mode] = list(self.WORKSPACE_TOOLBAR_DEFAULTS.get(mode, ()))
        self._save_workspace_toolbar_layouts()
        self._rebuild_toolbar_for_mode(mode)
        self._rebuild_workspace_toolbar_editor()

    def _toolbar_editor_parent_for_mode(self, mode: str) -> QWidget:
        if mode == "primary" and self.primary_toolbar is not None:
            return self.primary_toolbar
        return self.workspace_bar

    def _rebuild_workspace_toolbar_editor(self) -> None:
        mode = self._toolbar_edit_target_mode if self._toolbar_edit_target_mode in self.WORKSPACE_TOOLBAR_DEFAULTS else "manual"
        overlay_parent = self._toolbar_editor_parent_for_mode(mode)
        if self._toolbar_edit_overlay is None:
            self._toolbar_edit_overlay = QFrame(overlay_parent)
            self._toolbar_edit_overlay.setObjectName("toolbarEditOverlay")
            self._toolbar_edit_overlay.setAutoFillBackground(True)
            overlay_layout = QHBoxLayout(self._toolbar_edit_overlay)
        elif self._toolbar_edit_overlay.parentWidget() is not overlay_parent:
            self._toolbar_edit_overlay.hide()
            self._toolbar_edit_overlay.setParent(overlay_parent)
        overlay_layout = self._toolbar_edit_overlay.layout()
        if overlay_layout is None:
            return
        overlay_layout.setContentsMargins(6, 4, 6, 4)
        overlay_layout.setSpacing(6)
        self._clear_layout_items(overlay_layout, delete_widgets=True)

        title_text = {
            "primary": "Top Toolbar",
            "manual": "Manual Toolbar",
            "ai": "AI Toolbar",
        }.get(mode, "Toolbar")
        title = QLabel(title_text, self._toolbar_edit_overlay)
        title.setObjectName("toolbarEditTitle")
        overlay_layout.addWidget(title, 0, Qt.AlignmentFlag.AlignVCenter)

        mode_row = QWidget(self._toolbar_edit_overlay)
        mode_row_layout = QHBoxLayout(mode_row)
        mode_row_layout.setContentsMargins(0, 0, 0, 0)
        mode_row_layout.setSpacing(4)
        for target_mode, label in (("primary", "Top"), ("manual", "Manual"), ("ai", "AI")):
            mode_button = QToolButton(mode_row)
            mode_button.setObjectName("toolbarEditModeButton")
            mode_button.setText(label)
            mode_button.setCheckable(True)
            mode_button.setChecked(mode == target_mode)
            mode_button.clicked.connect(lambda _checked=False, selected=target_mode: self._select_toolbar_edit_target_mode(selected))
            mode_row_layout.addWidget(mode_button)
        overlay_layout.addWidget(mode_row, 0, Qt.AlignmentFlag.AlignVCenter)

        add_button = QToolButton(self._toolbar_edit_overlay)
        add_button.setObjectName("toolbarEditAddButton")
        add_button.setText("+ Add Button")
        add_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        add_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        add_menu = QMenu(add_button)
        available_items = self._available_toolbar_items_for_mode(mode)
        for item_id in available_items:
            action = add_menu.addAction(self.WORKSPACE_TOOLBAR_ITEM_LABELS.get(item_id, item_id))
            action.triggered.connect(lambda _checked=False, selected=item_id: self._add_workspace_toolbar_item(mode, selected))
        if not available_items:
            empty_action = add_menu.addAction("All buttons added")
            empty_action.setEnabled(False)
        add_button.setMenu(add_menu)
        overlay_layout.addWidget(add_button, 0, Qt.AlignmentFlag.AlignVCenter)

        chip_row = QWidget(self._toolbar_edit_overlay)
        chip_row_layout = QHBoxLayout(chip_row)
        chip_row_layout.setContentsMargins(0, 0, 0, 0)
        chip_row_layout.setSpacing(5)
        active_items = self._workspace_toolbar_layouts.get(mode, [])
        for item_id in active_items:
            chip = QFrame(chip_row)
            chip.setObjectName("toolbarEditChip")
            chip_layout = QHBoxLayout(chip)
            chip_layout.setContentsMargins(5, 2, 4, 2)
            chip_layout.setSpacing(3)

            item_label = QLabel(self.WORKSPACE_TOOLBAR_ITEM_LABELS.get(item_id, item_id), chip)
            item_label.setObjectName("toolbarEditHint")
            chip_layout.addWidget(item_label)

            left_button = QToolButton(chip)
            left_button.setObjectName("toolbarEditMoveButton")
            left_button.setText("<")
            left_button.setEnabled(active_items.index(item_id) > 0)
            left_button.clicked.connect(lambda _checked=False, selected=item_id: self._move_workspace_toolbar_item(mode, selected, -1))
            chip_layout.addWidget(left_button)

            right_button = QToolButton(chip)
            right_button.setObjectName("toolbarEditMoveButton")
            right_button.setText(">")
            right_button.setEnabled(active_items.index(item_id) < len(active_items) - 1)
            right_button.clicked.connect(lambda _checked=False, selected=item_id: self._move_workspace_toolbar_item(mode, selected, 1))
            chip_layout.addWidget(right_button)

            remove_button = QToolButton(chip)
            remove_button.setObjectName("toolbarEditRemoveButton")
            remove_button.setText("-")
            remove_button.setToolTip(f"Remove {self.WORKSPACE_TOOLBAR_ITEM_LABELS.get(item_id, item_id)}")
            remove_button.clicked.connect(lambda _checked=False, selected=item_id: self._remove_workspace_toolbar_item(mode, selected))
            chip_layout.addWidget(remove_button)
            chip_row_layout.addWidget(chip)
        if not active_items:
            empty_label = QLabel("Toolbar empty", chip_row)
            empty_label.setObjectName("toolbarEditHint")
            chip_row_layout.addWidget(empty_label)
        chip_row_layout.addStretch(1)
        overlay_layout.addWidget(chip_row, 1, Qt.AlignmentFlag.AlignVCenter)

        reset_button = QPushButton("Reset", self._toolbar_edit_overlay)
        reset_button.setObjectName("toolbarEditResetButton")
        reset_button.clicked.connect(lambda _checked=False: self._reset_workspace_toolbar_items(mode))
        overlay_layout.addWidget(reset_button, 0, Qt.AlignmentFlag.AlignVCenter)

        done_button = QPushButton("Done", self._toolbar_edit_overlay)
        done_button.setObjectName("toolbarEditDoneButton")
        done_button.clicked.connect(self._hide_workspace_toolbar_editor)
        overlay_layout.addWidget(done_button, 0, Qt.AlignmentFlag.AlignVCenter)
        self._toolbar_edit_overlay.show()
        self._position_workspace_toolbar_editor()

    def _build_record_filter_actions(self) -> None:
        file_type_group = QActionGroup(self)
        file_type_group.setExclusive(True)
        for mode in FileTypeFilter:
            action = QAction(mode.value, self)
            action.setCheckable(True)
            action.triggered.connect(lambda _checked=False, selected=mode: self._set_file_type_filter(selected))
            file_type_group.addAction(action)
            self._file_type_actions[mode] = action

        review_group = QActionGroup(self)
        review_group.setExclusive(True)
        for mode in ReviewStateFilter:
            action = QAction(mode.value, self)
            action.setCheckable(True)
            action.triggered.connect(lambda _checked=False, selected=mode: self._set_review_state_filter(selected))
            review_group.addAction(action)
            self._review_state_actions[mode] = action

        ai_group = QActionGroup(self)
        ai_group.setExclusive(True)
        for mode in AIStateFilter:
            action = QAction(mode.value, self)
            action.setCheckable(True)
            action.triggered.connect(lambda _checked=False, selected=mode: self._set_ai_state_filter(selected))
            ai_group.addAction(action)
            self._ai_state_actions[mode] = action

    def _populate_saved_filter_menu(self, menu: QMenu) -> None:
        menu.addAction(self.actions.save_filter_preset)
        menu.addAction(self.actions.delete_filter_preset)
        menu.addSeparator()

        active_label = self._matching_filter_preset_label(self._filter_query)
        builtins = builtin_filter_presets()
        if builtins:
            builtins_header = menu.addSection("Smart Filters")
            builtins_header.setEnabled(False)
            for preset in builtins:
                action = menu.addAction(preset.name)
                action.setCheckable(True)
                action.setChecked(active_label == preset.name)
                action.triggered.connect(lambda _checked=False, target=preset: self._apply_filter_preset(target))
            menu.addSeparator()

        saved_header = menu.addSection("Saved Searches")
        saved_header.setEnabled(False)
        if self._saved_filter_presets:
            for preset in self._saved_filter_presets:
                action = menu.addAction(preset.name)
                action.setCheckable(True)
                action.setChecked(active_label == preset.name)
                action.triggered.connect(lambda _checked=False, target=preset: self._apply_filter_preset(target))
        else:
            empty_action = menu.addAction("No saved searches yet")
            empty_action.setEnabled(False)

    def _refresh_filter_toolbar_menu(self) -> None:
        self.filter_toolbar_menu.clear()
        file_type_menu = self.filter_toolbar_menu.addMenu("File Type")
        for mode in FileTypeFilter:
            file_type_menu.addAction(self._file_type_actions[mode])

        review_menu = self.filter_toolbar_menu.addMenu("Review State")
        for mode in ReviewStateFilter:
            review_menu.addAction(self._review_state_actions[mode])

        ai_menu = self.filter_toolbar_menu.addMenu("AI State")
        for mode in AIStateFilter:
            ai_menu.addAction(self._ai_state_actions[mode])

        self.filter_toolbar_menu.addSeparator()
        self.filter_toolbar_menu.addAction(self.actions.advanced_filters)
        self.filter_toolbar_menu.addAction(self.actions.clear_filters)
        self.filter_toolbar_menu.addSeparator()
        saved_menu = self.filter_toolbar_menu.addMenu("Saved Searches")
        self._populate_saved_filter_menu(saved_menu)

    def _refresh_action_shortcut_hint(self, action: QAction) -> None:
        base_text = action.property("imageTriageBaseText")
        if not isinstance(base_text, str) or not base_text:
            base_text = action.text().replace("&", "")
        shortcut_text = action.shortcut().toString(QKeySequence.SequenceFormat.NativeText)
        hinted_text = f"{base_text} ({shortcut_text})" if shortcut_text else base_text
        action.setToolTip(hinted_text)
        action.setStatusTip(hinted_text)

    def _set_action_shortcut(self, action: QAction, shortcut: str) -> None:
        action.setShortcut(QKeySequence(shortcut))
        self._refresh_action_shortcut_hint(action)

    @staticmethod
    def _menu_text_with_hint(text: str, hint: str = "") -> str:
        return f"{text}\t{hint}" if hint else text

    def _menu_text_with_action_shortcut(self, text: str, action: QAction | None) -> str:
        if action is None:
            return text
        shortcut_text = action.shortcut().toString(QKeySequence.SequenceFormat.NativeText)
        return self._menu_text_with_hint(text, shortcut_text)

    def _register_shortcut_targets(self) -> None:
        if self.actions is None:
            return

        def register_action(binding_id: str, action, *, label: str, section: str) -> None:
            self._shortcut_targets[binding_id] = ShortcutTarget(
                id=binding_id,
                label=label,
                section=section,
                default_shortcut=action.shortcut().toString(QKeySequence.SequenceFormat.PortableText),
                apply=lambda shortcut, target=action: self._set_action_shortcut(target, shortcut),
            )

        register_action("file.open_folder", self.actions.open_folder, label="Open Folder", section="File")
        register_action("file.refresh_folder", self.actions.refresh_folder, label="Refresh Folder", section="File")
        register_action("edit.undo", self.actions.undo, label="Undo", section="Edit")
        register_action("review.open_preview", self.actions.open_preview, label="Open Preview", section="Review")
        register_action("review.compare_mode", self.actions.compare_mode, label="Compare Mode", section="Review")
        register_action("review.accept_selection", self.actions.accept_selection, label="Accept Selection", section="Review")
        register_action("review.reject_selection", self.actions.reject_selection, label="Reject Selection", section="Review")
        register_action("review.keep_selection", self.actions.keep_selection, label="Move Selection To _keep", section="Review")
        register_action("review.move_selection", self.actions.move_selection, label="Move Selection", section="Review")
        register_action("review.delete_selection", self.actions.delete_selection, label="Delete Selection", section="Review")
        register_action("ai.next_top_pick", self.actions.next_ai_pick, label="Next AI Top Pick", section="AI")
        register_action("ai.compare_group", self.actions.compare_ai_group, label="Compare Current AI Group", section="AI")
        register_action("workflow.handoff_builder", self.actions.handoff_builder, label="Deliver / Handoff Builder", section="Workflow")
        register_action("workflow.send_to_editor", self.actions.send_to_editor_pipeline, label="Send To Editor", section="Workflow")
        register_action("workflow.best_of", self.actions.best_of_set_auto_assembly, label="Best-of-Set Auto Assembly", section="Workflow")
        register_action("workflow.save_workspace", self.actions.save_workspace_preset, label="Save Current Workspace Preset", section="Workflow")

        self._shortcut_targets["palette.open"] = ShortcutTarget(
            id="palette.open",
            label="Open Command Palette",
            section="Workspace",
            default_shortcut="Ctrl+K",
            apply=self._apply_command_palette_shortcut,
        )

    def _shortcut_bindings(self) -> list[ShortcutBinding]:
        bindings: list[ShortcutBinding] = []
        for binding_id, target in self._shortcut_targets.items():
            bindings.append(
                ShortcutBinding(
                    id=binding_id,
                    label=target.label,
                    section=target.section,
                    default_shortcut=target.default_shortcut,
                    shortcut=self._shortcut_overrides.get(binding_id, ""),
                )
            )
        bindings.sort(key=lambda item: (item.section.casefold(), item.label.casefold()))
        return bindings

    def _apply_shortcut_overrides(self) -> None:
        for binding_id, target in self._shortcut_targets.items():
            shortcut = self._shortcut_overrides.get(binding_id, "") or target.default_shortcut
            normalized = normalize_shortcut_text(shortcut)
            target.apply(normalized)

    def _apply_command_palette_shortcut(self, shortcut: str) -> None:
        sequence = QKeySequence(shortcut)
        if self._command_palette_shortcut_main is not None:
            self._command_palette_shortcut_main.setKey(sequence)
        if self._command_palette_shortcut_preview is not None:
            self._command_palette_shortcut_preview.setKey(sequence)
        if self.actions is not None:
            self.actions.open_command_palette.setShortcut(sequence)
            self._refresh_action_shortcut_hint(self.actions.open_command_palette)

    def _load_saved_workflow_recipes(self) -> list[WorkflowRecipe]:
        raw = self._settings.value(self.WORKFLOW_RECIPES_KEY, "", str)
        if not raw:
            return []
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError):
            return []
        recipes: list[WorkflowRecipe] = []
        seen: set[str] = set()
        if not isinstance(payload, list):
            return recipes
        for item in payload:
            recipe = deserialize_workflow_recipe(item if isinstance(item, dict) else None)
            if recipe is None or recipe.key in seen:
                continue
            seen.add(recipe.key)
            recipes.append(recipe)
        return recipes

    def _save_saved_workflow_recipes(self) -> None:
        payload = [serialize_workflow_recipe(recipe) for recipe in self._saved_workflow_recipes]
        self._settings.setValue(self.WORKFLOW_RECIPES_KEY, json.dumps(payload))

    def _load_saved_workspace_presets(self) -> list[WorkspacePreset]:
        raw = self._settings.value(self.WORKSPACE_PRESETS_KEY, "", str)
        if not raw:
            return []
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError):
            return []
        presets: list[WorkspacePreset] = []
        seen: set[str] = set()
        if not isinstance(payload, list):
            return presets
        for item in payload:
            preset = deserialize_workspace_preset(item if isinstance(item, dict) else None)
            if preset is None or preset.key in seen:
                continue
            seen.add(preset.key)
            presets.append(preset)
        return presets

    def _save_saved_workspace_presets(self) -> None:
        payload = [serialize_workspace_preset(preset) for preset in self._saved_workspace_presets]
        self._settings.setValue(self.WORKSPACE_PRESETS_KEY, json.dumps(payload))

    def _load_shortcut_overrides(self) -> dict[str, str]:
        raw = self._settings.value(self.SHORTCUT_OVERRIDES_KEY, "", str)
        if not raw:
            return {}
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError):
            return {}
        if not isinstance(payload, dict):
            return {}
        return {
            str(binding_id): normalize_shortcut_text(shortcut if isinstance(shortcut, str) else "")
            for binding_id, shortcut in payload.items()
            if isinstance(binding_id, str)
        }

    def _save_shortcut_overrides(self) -> None:
        payload = serialize_shortcut_overrides(self._shortcut_overrides)
        self._settings.setValue(self.SHORTCUT_OVERRIDES_KEY, json.dumps(payload))

    def _refresh_workflow_recipe_menu(self) -> None:
        if not hasattr(self, "workflow_recipe_menu") or self.workflow_recipe_menu is None:
            return
        self.workflow_recipe_menu.clear()
        self.workflow_recipe_menu.setTitle("Run Recipe")
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
        self._scope_kind = kind
        self._scope_id = scope_id
        self._scope_label = label
        self._apply_scope_label()

    def _current_scope_key(self) -> str:
        if self._scope_kind == "folder":
            return normalized_path_key(self._current_folder)
        return f"{self._scope_kind}:{self._scope_id or self._scope_label.casefold()}"

    def _refresh_collections_menu(self) -> None:
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

    def _refresh_catalog_menu(self) -> None:
        if not hasattr(self, "catalog_menu") or self.catalog_menu is None:
            return
        self.catalog_menu.clear()
        self.catalog_menu.setTitle("Catalog")
        self.catalog_menu.addAction(self.actions.browse_catalog)
        self.catalog_menu.addAction(self.actions.add_current_folder_to_catalog)
        self.catalog_menu.addAction(self.actions.add_folder_to_catalog)
        self.catalog_menu.addAction(self.actions.remove_catalog_folder)
        self.catalog_menu.addAction(self.actions.refresh_catalog)
        self.catalog_menu.addAction(self.actions.rebuild_folder_catalog_cache)
        self.catalog_menu.addSeparator()

        roots = self._library_store.list_catalog_roots()
        if roots:
            header = self.catalog_menu.addSection("Indexed Roots")
            header.setEnabled(False)
            for root in roots:
                label = Path(root.path).name or root.path
                action = self.catalog_menu.addAction(f"{label} ({root.indexed_record_count})")
                tooltip_parts = [root.path]
                if root.last_indexed_at:
                    tooltip_parts.append(f"Indexed: {root.last_indexed_at}")
                if root.last_error:
                    tooltip_parts.append(f"Status: {root.last_error}")
                action.setToolTip("\n".join(tooltip_parts))
                action.triggered.connect(lambda _checked=False, target=root.path: self._browse_catalog(root_path_override=target))
        else:
            empty_action = self.catalog_menu.addAction("No catalog roots yet")
            empty_action.setEnabled(False)
        if self.actions is not None:
            self._update_action_states()

    def _open_command_palette(self, _checked: bool = False, *, context: str | None = None) -> None:
        palette_context = context or ("preview" if self.preview.isVisible() and self.preview.isActiveWindow() else "main")
        if self._active_command_palette is not None and self._active_command_palette.isVisible():
            return
        dialog = self._ensure_command_palette_dialog(palette_context)
        commands = self._build_command_palette_commands(palette_context)
        dialog.configure(
            commands,
            recent_command_ids=tuple(self._recent_command_ids),
            title="Preview Commands" if palette_context == "preview" else "Command Palette",
        )
        self._command_palette_open = True
        self._active_command_palette = dialog
        self._set_command_palette_shortcuts_enabled(False)
        dialog.present()

    def _setup_command_palette_shortcuts(self) -> None:
        self._command_palette_shortcut_main = QShortcut(QKeySequence("Ctrl+K"), self)
        self._command_palette_shortcut_main.setAutoRepeat(False)
        self._command_palette_shortcut_main.activated.connect(lambda: self._open_command_palette(context="main"))
        self._command_palette_shortcut_preview = QShortcut(QKeySequence("Ctrl+K"), self.preview)
        self._command_palette_shortcut_preview.setAutoRepeat(False)
        self._command_palette_shortcut_preview.activated.connect(lambda: self._open_command_palette(context="preview"))

    def _set_command_palette_shortcuts_enabled(self, enabled: bool) -> None:
        if self.actions is not None:
            self.actions.open_command_palette.setEnabled(enabled)
        if self._command_palette_shortcut_main is not None:
            self._command_palette_shortcut_main.setEnabled(enabled)
        if self._command_palette_shortcut_preview is not None:
            self._command_palette_shortcut_preview.setEnabled(enabled)

    def _ensure_command_palette_dialog(self, context: str) -> CommandPaletteDialog:
        existing = self._command_palette_dialogs.get(context)
        if existing is not None:
            return existing
        parent = self.preview if context == "preview" and self.preview.isVisible() else self
        dialog = CommandPaletteDialog([], recent_command_ids=(), parent=parent)
        dialog.finished.connect(self._handle_command_palette_finished)
        self._command_palette_dialogs[context] = dialog
        return dialog

    def _handle_command_palette_finished(self, result: int) -> None:
        dialog = self.sender()
        if not isinstance(dialog, CommandPaletteDialog):
            self._command_palette_open = False
            self._active_command_palette = None
            self._set_command_palette_shortcuts_enabled(True)
            return
        self._command_palette_open = False
        self._active_command_palette = None
        self._set_command_palette_shortcuts_enabled(True)
        if result != dialog.DialogCode.Accepted:
            return
        command = dialog.selected_command
        if command is None:
            return
        self._remember_recent_command(command.id)
        command.callback()

    def _build_command_palette_commands(self, context: str) -> list[PaletteCommand]:
        commands: list[PaletteCommand] = []

        def add_action_command(
            command_id: str,
            action,
            *,
            section: str,
            title: str | None = None,
            subtitle: str = "",
            keywords: tuple[str, ...] = (),
        ) -> None:
            if action is None or not action.isEnabled():
                return
            commands.append(
                PaletteCommand(
                    id=command_id,
                    title=title or self._clean_command_text(action.text()),
                    subtitle=subtitle,
                    section=section,
                    shortcut=action.shortcut().toString(),
                    keywords=keywords,
                    callback=action.trigger,
                )
            )

        if self.actions is not None:
            add_action_command("file.open_folder", self.actions.open_folder, section="File", keywords=("open directory", "browse folder"))
            add_action_command("file.refresh_folder", self.actions.refresh_folder, section="File", keywords=("reload folder", "rescan"))
            add_action_command("file.new_folder", self.actions.new_folder, section="File", keywords=("create folder", "new directory"))
            add_action_command("file.workflow_settings", self.actions.workflow_settings, section="File", keywords=("preferences", "settings"))
            add_action_command("edit.undo", self.actions.undo, section="Edit", keywords=("revert", "undo last action"))
            add_action_command("edit.rename_selection", self.actions.rename_selection, section="Edit", keywords=("rename image", "rename file"))
            add_action_command("tools.batch_rename", self.actions.batch_rename_selection, section="Tools", keywords=("batch rename tool", "rename many"))
            add_action_command("tools.batch_resize", self.actions.batch_resize_selection, section="Tools", keywords=("batch resize tool", "resize many", "convert size"))
            add_action_command("tools.batch_convert", self.actions.batch_convert_selection, section="Tools", keywords=("batch convert tool", "convert format", "png jpg webp"))
            add_action_command("tools.extract_archive", self.actions.extract_archive, section="Tools", keywords=("extract archive", "unzip", "decompress", "7z"))
            add_action_command("workflow.handoff_builder", self.actions.handoff_builder, section="Workflow", keywords=("delivery", "handoff", "export workflow"))
            add_action_command("workflow.send_to_editor", self.actions.send_to_editor_pipeline, section="Workflow", keywords=("retouch", "editor queue", "send to editor"))
            add_action_command("workflow.best_of", self.actions.best_of_set_auto_assembly, section="Workflow", keywords=("best of", "shortlist", "auto assembly"))
            add_action_command("workflow.keyboard_shortcuts", self.actions.keyboard_shortcuts, section="Workflow", keywords=("shortcuts", "keyboard mapping"))
            add_action_command("workflow.save_workspace", self.actions.save_workspace_preset, section="Workflow", keywords=("workspace preset", "save layout"))
            add_action_command("library.create_collection", self.actions.create_virtual_collection, section="Library", keywords=("virtual collection", "portfolio picks", "proofing set"))
            add_action_command("library.add_to_collection", self.actions.add_selection_to_collection, section="Library", keywords=("collection", "save picks"))
            add_action_command("library.remove_from_collection", self.actions.remove_selection_from_collection, section="Library", keywords=("collection", "remove picks"))
            add_action_command("library.delete_collection", self.actions.delete_virtual_collection, section="Library", keywords=("collection", "delete set"))
            add_action_command("library.browse_catalog", self.actions.browse_catalog, section="Library", keywords=("catalog", "cross folder search", "global index"))
            add_action_command("library.add_current_to_catalog", self.actions.add_current_folder_to_catalog, section="Library", keywords=("catalog root", "index current folder"))
            add_action_command("library.add_folder_to_catalog", self.actions.add_folder_to_catalog, section="Library", keywords=("catalog root", "index folder"))
            add_action_command("library.remove_catalog_root", self.actions.remove_catalog_folder, section="Library", keywords=("catalog root", "remove folder"))
            add_action_command("library.refresh_catalog", self.actions.refresh_catalog, section="Library", keywords=("refresh catalog", "reindex library"))
            add_action_command("library.rebuild_open_folder_cache", self.actions.rebuild_folder_catalog_cache, section="Library", keywords=("rebuild cache", "rebuild folder cache", "rescan without cache"))
            add_action_command("review.open_preview", self.actions.open_preview, section="Review", keywords=("viewer", "popout", "fullscreen"))
            add_action_command("review.accept_selection", self.actions.accept_selection, section="Review", keywords=("winner", "approve", "accept"))
            add_action_command("review.reject_selection", self.actions.reject_selection, section="Review", keywords=("reject", "decline"))
            add_action_command("review.keep_selection", self.actions.keep_selection, section="Review", keywords=("keep", "_keep"))
            add_action_command("review.move_selection", self.actions.move_selection, section="Review", keywords=("relocate", "move"))
            add_action_command(
                "review.move_selection_to_new_folder",
                self.actions.move_selection_to_new_folder,
                section="Review",
                keywords=("new folder", "move to new folder", "subfolder"),
            )
            add_action_command("review.delete_selection", self.actions.delete_selection, section="Review", keywords=("trash", "remove", "delete"))
            add_action_command("review.restore_selection", self.actions.restore_selection, section="Review", keywords=("recover", "restore"))
            add_action_command("review.reveal_in_explorer", self.actions.reveal_in_explorer, section="Review", keywords=("show in explorer", "reveal file"))
            add_action_command("review.photoshop", self.actions.open_in_photoshop, section="Review", keywords=("edit in photoshop",))
            add_action_command("review.compare_mode", self.actions.compare_mode, section="Review", subtitle=self._toggle_state_text(self._compare_enabled), keywords=("toggle compare",))
            add_action_command("review.auto_advance", self.actions.auto_advance, section="Review", subtitle=self._toggle_state_text(self._auto_advance_enabled), keywords=("toggle auto advance",))
            add_action_command("view.burst_groups", self.actions.burst_groups, section="View", subtitle=self._toggle_state_text(self._burst_groups_enabled), keywords=("burst grouping", "burst shots", "toggle bursts", "capture sequence"))
            add_action_command("view.burst_stacks", self.actions.burst_stacks, section="View", subtitle=self._toggle_state_text(self._burst_stacks_enabled), keywords=("smart stacks", "cycle group", "stack shots", "duplicate stack"))
            add_action_command("search.advanced_filters", self.actions.advanced_filters, section="Search", keywords=("metadata filters", "search filters"))
            add_action_command("search.save_current", self.actions.save_filter_preset, section="Search", keywords=("save search", "save preset"))
            add_action_command("search.delete_current", self.actions.delete_filter_preset, section="Search", keywords=("delete search", "remove preset"))
            add_action_command("search.clear_filters", self.actions.clear_filters, section="Search", keywords=("reset filters", "clear search"))
            add_action_command("ai.download_model", self.actions.download_ai_model, section="AI", keywords=("download ai model", "install ai model", "enable ai"))
            add_action_command("ai.run_pipeline", self.actions.run_ai_culling, section="AI", keywords=("start ai", "run model"))
            add_action_command("ai.load_saved", self.actions.load_saved_ai, section="AI", keywords=("load cached ai",))
            add_action_command("ai.load_results", self.actions.load_ai_results, section="AI", keywords=("import ai results",))
            add_action_command("ai.clear_results", self.actions.clear_ai_results, section="AI", keywords=("remove ai results",))
            add_action_command("ai.open_report", self.actions.open_ai_report, section="AI", keywords=("html report",))
            add_action_command("ai.data_selection", self.actions.open_ai_data_selection, section="AI", keywords=("collect training labels", "pairwise labels", "cluster labels", "ranking data", "labeling"))
            add_action_command("ai.full_training_pipeline", self.actions.run_full_ai_training_pipeline, section="AI", keywords=("one click training", "full training pipeline", "prepare train evaluate score"))
            add_action_command("ai.prepare_training_data", self.actions.prepare_ai_training_data, section="AI", keywords=("prepare training data", "prepare model data", "extract embeddings", "cluster"))
            add_action_command("ai.build_reference_bank", self.actions.build_ai_reference_bank, section="AI", keywords=("reference bank", "exemplar model", "bucketed references"))
            add_action_command("ai.train_ranker", self.actions.train_ai_ranker, section="AI", keywords=("train model", "train ranker", "preference model"))
            add_action_command("ai.manage_rankers", self.actions.manage_ai_rankers, section="AI", keywords=("ranker center", "choose ranker", "model versions", "ranker versions", "checkpoint manager"))
            add_action_command("ai.evaluate_ranker", self.actions.evaluate_ai_ranker, section="AI", keywords=("evaluate model", "metrics", "validation"))
            add_action_command("ai.score_trained_ranker", self.actions.score_ai_with_trained_ranker, section="AI", keywords=("refresh ai report", "score current folder", "trained checkpoint"))
            add_action_command("ai.clear_trained_model", self.actions.clear_ai_trained_model, section="AI", keywords=("reset ai checkpoint", "clear trained model"))
            add_action_command("ai.next_top_pick", self.actions.next_ai_pick, section="AI", keywords=("next ai pick", "jump ai"))
            add_action_command("ai.next_unreviewed_top_pick", self.actions.next_unreviewed_ai_pick, section="AI", keywords=("unreviewed ai pick",))
            add_action_command("ai.compare_group", self.actions.compare_ai_group, section="AI", keywords=("compare ai cluster", "group compare"))
            add_action_command("window.customize_toolbar", self.actions.customize_workspace_toolbar, section="Workspace", keywords=("customize toolbar", "edit toolbar", "ui edit mode"))
            add_action_command("window.reset_layout", self.actions.reset_layout, section="Workspace", keywords=("restore layout", "default workspace"))
            add_action_command("help.keyboard_help", self.actions.keyboard_help, section="Help", keywords=("quick help", "shortcuts", "help"))
            add_action_command("help.ai_guide", self.actions.ai_guide, section="Help", keywords=("ai guide", "ai training guide", "model guide", "ai help"))
            add_action_command("help.advanced_help", self.actions.advanced_help, section="Help", keywords=("advanced help", "reference", "guide"))
            add_action_command("help.about", self.actions.about, section="Help", keywords=("about", "version"))

            commands.append(
                PaletteCommand(
                    id="mode.manual",
                    title="Switch To Manual Review",
                    subtitle="Current mode" if self._ui_mode == "manual" else "",
                    section="Workspace",
                    keywords=("manual mode", "review mode"),
                    callback=lambda: self._set_ui_mode("manual"),
                )
            )
            commands.append(
                PaletteCommand(
                    id="mode.ai",
                    title="Switch To AI Review",
                    subtitle="Current mode" if self._ui_mode == "ai" else "",
                    section="Workspace",
                    keywords=("ai mode", "ai review"),
                    callback=lambda: self._set_ui_mode("ai"),
                )
            )

            for mode, action in self.actions.appearance_actions.items():
                add_action_command(
                    f"appearance.{mode.value.casefold()}",
                    action,
                    section="Appearance",
                    title=f"Set Theme: {mode.value}",
                    keywords=("theme", "appearance", mode.value.casefold()),
                )
            for mode, action in self.actions.sort_actions.items():
                add_action_command(
                    f"sort.{mode.name.casefold()}",
                    action,
                    section="View",
                    title=f"View: Sort By {mode.value}",
                    keywords=("view", "sort", mode.value.casefold()),
                )
            for mode, action in self.actions.filter_actions.items():
                add_action_command(
                    f"quick_filter.{mode.name.casefold()}",
                    action,
                    section="View",
                    title=f"View: Quick Filter {mode.value}",
                    keywords=("view", "quick filter", "filter", mode.value.casefold()),
                )
            for count, action in self.actions.column_actions.items():
                add_action_command(
                    f"columns.{count}",
                    action,
                    section="View",
                    title=f"View: Columns {count} Across",
                    keywords=("view", "columns", f"{count} across"),
                )

        if self.workspace_docks is not None:
            for key, action in self.workspace_docks.toggle_actions.items():
                panel_title = key.title()
                commands.append(
                    PaletteCommand(
                        id=f"dock.{key}",
                        title=f"{'Hide' if action.isChecked() else 'Show'} {panel_title}",
                        subtitle="Workspace panel",
                        section="Workspace",
                        keywords=(panel_title.casefold(), "panel", "dock", "sidebar"),
                        callback=action.trigger,
                    )
                )

        for preset in builtin_filter_presets():
            commands.append(
                PaletteCommand(
                    id=f"smart_filter.{preset.name.casefold().replace(' ', '_')}",
                    title=f"Apply Smart Filter: {preset.name}",
                    subtitle=self._preset_subtitle(preset),
                    section="Search",
                    keywords=("smart filter", "saved search", preset.name.casefold()),
                    callback=lambda target=preset: self._apply_filter_preset(target),
                )
            )
        for preset in self._saved_filter_presets:
            commands.append(
                PaletteCommand(
                    id=f"saved_filter.{preset.name.casefold()}",
                    title=f"Apply Saved Search: {preset.name}",
                    subtitle=self._preset_subtitle(preset),
                    section="Search",
                    keywords=("saved search", "preset", preset.name.casefold()),
                    callback=lambda target=preset: self._apply_filter_preset(target),
                )
            )

        for recipe in built_in_workflow_recipes():
            commands.append(
                PaletteCommand(
                    id=f"workflow_recipe.{recipe.key}",
                    title=f"Run Workflow Recipe: {recipe.name}",
                    subtitle=recipe.description or "Built-in workflow recipe",
                    section="Workflow",
                    keywords=("workflow recipe", recipe.name.casefold(), recipe.key),
                    callback=lambda target=recipe: self._run_workflow_recipe(target),
                )
            )
        for recipe in self._saved_workflow_recipes:
            commands.append(
                PaletteCommand(
                    id=f"saved_workflow_recipe.{recipe.key}",
                    title=f"Run Saved Recipe: {recipe.name}",
                    subtitle=recipe.description or "Saved workflow recipe",
                    section="Workflow",
                    keywords=("saved recipe", "workflow recipe", recipe.name.casefold()),
                    callback=lambda target=recipe: self._run_workflow_recipe(target),
                )
            )

        for preset in built_in_workspace_presets():
            commands.append(
                PaletteCommand(
                    id=f"workspace_preset.{preset.key}",
                    title=f"Apply Workspace Preset: {preset.name}",
                    subtitle=preset.description,
                    section="Workspace",
                    keywords=("workspace preset", preset.name.casefold(), preset.key),
                    callback=lambda target=preset: self._apply_workspace_preset(target),
                )
            )
        for preset in self._saved_workspace_presets:
            commands.append(
                PaletteCommand(
                    id=f"saved_workspace_preset.{preset.key}",
                    title=f"Apply Saved Workspace: {preset.name}",
                    subtitle=preset.description or "Saved workspace preset",
                    section="Workspace",
                    keywords=("saved workspace", "workspace preset", preset.name.casefold()),
                    callback=lambda target=preset: self._apply_workspace_preset(target),
                )
            )

        for collection in self._library_store.list_collections():
            commands.append(
                PaletteCommand(
                    id=f"collection.{collection.id}",
                    title=f"Open Collection: {collection.name}",
                    subtitle=collection.description or f"{collection.kind} | {collection.item_count} item(s)",
                    section="Library",
                    keywords=("collection", collection.name.casefold(), collection.kind.casefold()),
                    callback=lambda target=collection.id: self._open_virtual_collection(target),
                )
            )

        for root in self._library_store.list_catalog_roots():
            root_label = Path(root.path).name or root.path
            commands.append(
                PaletteCommand(
                    id=f"catalog.{normalized_path_key(root.path)}",
                    title=f"Browse Catalog Root: {root_label}",
                    subtitle=f"{root.indexed_record_count} indexed bundle(s)",
                    section="Library",
                    keywords=("catalog", "library", root_label.casefold()),
                    callback=lambda target=root.path: self._browse_catalog(root_path_override=target),
                )
            )

        for destination in self._recent_destination_paths(exclude_current_folder=True)[:6]:
            label = Path(destination).name or destination
            commands.append(
                PaletteCommand(
                    id=f"recent.move.{normalized_path_key(destination)}",
                    title=f"Move Selection To Recent Folder: {label}",
                    subtitle=destination,
                    section="Review",
                    keywords=("recent folder", "move recent", "destination"),
                    callback=lambda target=destination: self._move_selected_records_to_destination(target),
                )
            )

        if context == "preview" and self.preview.isVisible():
            focused_path = self.preview.focused_path()
            photoshop_path = self.preview.focused_photoshop_path()
            commands.extend(
                [
                    PaletteCommand(
                        id="preview.close",
                        title="Close Preview",
                        subtitle="Close the preview window",
                        section="Preview",
                        shortcut="Esc",
                        keywords=("close viewer", "exit preview"),
                        callback=self.preview.close,
                    ),
                    PaletteCommand(
                        id="preview.previous",
                        title="Previous Image",
                        subtitle="Move to the previous visible image",
                        section="Preview",
                        keywords=("previous", "back", "left"),
                        callback=lambda: self.preview.navigate_relative(-1),
                    ),
                    PaletteCommand(
                        id="preview.next",
                        title="Next Image",
                        subtitle="Move to the next visible image",
                        section="Preview",
                        keywords=("next", "forward", "right"),
                        callback=lambda: self.preview.navigate_relative(1),
                    ),
                    PaletteCommand(
                        id="preview.compare",
                        title="Toggle Compare Mode",
                        subtitle=self._toggle_state_text(self.preview.compare_mode_enabled()),
                        section="Preview",
                        shortcut="C",
                        keywords=("compare", "compare mode"),
                        callback=self.preview.toggle_compare_mode,
                    ),
                    PaletteCommand(
                        id="preview.zoom",
                        title="Toggle Zoom",
                        subtitle="Switch between fit and manual zoom",
                        section="Preview",
                        shortcut="Z",
                        keywords=("zoom", "magnify"),
                        callback=self.preview.toggle_zoom_command,
                    ),
                    PaletteCommand(
                        id="preview.fit",
                        title="Fit To Screen",
                        subtitle="Return the preview to fit mode",
                        section="Preview",
                        shortcut="0",
                        keywords=("fit", "fit screen", "reset zoom"),
                        callback=self.preview.fit_to_screen,
                    ),
                    PaletteCommand(
                        id="preview.loupe",
                        title="Toggle Loupe",
                        subtitle="Enable or disable the loupe overlay",
                        section="Preview",
                        shortcut="L",
                        keywords=("loupe", "magnifier"),
                        callback=self.preview.toggle_loupe_command,
                    ),
                    PaletteCommand(
                        id="preview.focus_assist",
                        title="Toggle Focus Assist",
                        subtitle=(
                            f"{self._toggle_state_text(self.preview.focus_assist_enabled())}"
                            f" | {self.preview.focus_assist_color().label}"
                            f" | {self.preview.focus_assist_strength().label}"
                        ),
                        section="Preview",
                        shortcut="F",
                        keywords=("focus assist", "focus", "inspection", "detail", "sensitivity"),
                        callback=self.preview.toggle_focus_assist_command,
                    ),
                    PaletteCommand(
                        id="preview.focus_assist_background",
                        title="Toggle Focus Assist Background Filter",
                        subtitle="Dimmed background" if self.preview.focus_assist_dim_background() else "Original image background",
                        section="Preview",
                        keywords=("focus assist", "background", "filter", "dim background", "overlay"),
                        callback=self.preview.toggle_focus_assist_background_command,
                    ),
                ]
            )
            for color in FOCUS_ASSIST_COLORS:
                commands.append(
                    PaletteCommand(
                        id=f"preview.focus_assist_color.{color.id}",
                        title=f"Set Focus Assist Color: {color.label}",
                        subtitle=(
                            "Current color"
                            if self.preview.focus_assist_color().id == color.id
                            else "Switch focus peaking color"
                        ),
                        section="Preview",
                        keywords=("focus assist", "focus peaking", "color", color.label.casefold()),
                        callback=lambda color_id=color.id: self.preview.set_focus_assist_color_by_id(color_id),
                    )
                )
            for strength in FOCUS_ASSIST_STRENGTHS:
                commands.append(
                    PaletteCommand(
                        id=f"preview.focus_assist_strength.{strength.id}",
                        title=f"Set Focus Assist Sensitivity: {strength.label}",
                        subtitle=(
                            "Current sensitivity"
                            if self.preview.focus_assist_strength().id == strength.id
                            else "Adjust focus peaking sensitivity"
                        ),
                        section="Preview",
                        keywords=("focus assist", "focus peaking", "sensitivity", strength.label.casefold()),
                        callback=lambda strength_id=strength.id: self.preview.set_focus_assist_strength_by_id(strength_id),
                    )
                )
            if focused_path:
                commands.extend(
                    [
                        PaletteCommand(
                            id="preview.rename",
                            title="Rename Focused Image...",
                            subtitle="Rename the focused image bundle",
                            section="Preview",
                            shortcut="F2",
                            keywords=("rename", "filename"),
                            callback=lambda path=focused_path: self._handle_preview_rename_requested(path),
                        ),
                        PaletteCommand(
                            id="preview.accept",
                            title="Accept Focused Image",
                            subtitle="Mark the focused preview image as accepted",
                            section="Preview",
                            shortcut="W",
                            keywords=("accept", "winner", "approve"),
                            callback=lambda path=focused_path: self._handle_preview_winner_requested(path),
                        ),
                        PaletteCommand(
                            id="preview.reject",
                            title="Reject Focused Image",
                            subtitle="Mark the focused preview image as rejected",
                            section="Preview",
                            shortcut="X",
                            keywords=("reject", "decline"),
                            callback=lambda path=focused_path: self._handle_preview_reject_requested(path),
                        ),
                        PaletteCommand(
                            id="preview.keep",
                            title="Move Focused Image To _keep",
                            subtitle="Send the focused preview image to the keep folder",
                            section="Preview",
                            shortcut="K",
                            keywords=("keep", "_keep"),
                            callback=lambda path=focused_path: self._handle_preview_keep_requested(path),
                        ),
                        PaletteCommand(
                            id="preview.move",
                            title="Move Focused Image...",
                            subtitle="Move the focused preview image to another folder",
                            section="Preview",
                            shortcut="M",
                            keywords=("move", "relocate"),
                            callback=lambda path=focused_path: self._handle_preview_move_requested(path),
                        ),
                        PaletteCommand(
                            id="preview.delete",
                            title="Delete Focused Image",
                            subtitle="Delete the focused preview image",
                            section="Preview",
                            shortcut="Delete",
                            keywords=("delete", "trash", "remove"),
                            callback=lambda path=focused_path: self._handle_preview_delete_requested(path),
                        ),
                        PaletteCommand(
                            id="preview.tag",
                            title="Tag Focused Image",
                            subtitle="Edit tags for the focused preview image",
                            section="Preview",
                            shortcut="T",
                            keywords=("tag", "keywords"),
                            callback=lambda path=focused_path: self._handle_preview_tag_requested(path),
                        ),
                    ]
                )
            if photoshop_path and self._photoshop_executable:
                commands.append(
                    PaletteCommand(
                        id="preview.photoshop",
                        title="Open Focused Image In Photoshop",
                        subtitle="Send the focused preview image to Photoshop",
                        section="Preview",
                        keywords=("photoshop", "edit"),
                        callback=lambda path=photoshop_path: self._open_preview_image_in_photoshop(path),
                    )
                )

        return commands

    def _preset_subtitle(self, preset: SavedFilterPreset) -> str:
        labels = active_filter_labels(preset.query)
        if not labels:
            return "All images"
        return " | ".join(labels[:3])

    @staticmethod
    def _clean_command_text(text: str) -> str:
        return (text or "").replace("&", "").replace("...", "").strip()

    @staticmethod
    def _toggle_state_text(enabled: bool) -> str:
        return "On" if enabled else "Off"

    def _remember_recent_command(self, command_id: str) -> None:
        self._recent_command_ids = [command_id, *[item for item in self._recent_command_ids if item != command_id]][:12]
        self._save_recent_command_ids()

    def _matching_saved_filter_preset(self, query: RecordFilterQuery | None = None) -> SavedFilterPreset | None:
        target = query or self._filter_query
        for preset in self._saved_filter_presets:
            if preset.query == target:
                return preset
        return None

    def _matching_filter_preset_label(self, query: RecordFilterQuery | None = None) -> str:
        target = query or self._filter_query
        saved = self._matching_saved_filter_preset(target)
        if saved is not None:
            return saved.name
        for preset in builtin_filter_presets():
            if preset.query == target:
                return preset.name
        return ""

    def _copy_filter_query(self, query: RecordFilterQuery) -> RecordFilterQuery:
        return RecordFilterQuery(
            quick_filter=query.quick_filter,
            search_text=query.search_text,
            file_type=query.file_type,
            review_state=query.review_state,
            ai_state=query.ai_state,
            review_round=query.review_round,
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

    def _apply_filter_preset(self, preset: SavedFilterPreset) -> None:
        self._filter_query = self._copy_filter_query(preset.query)
        self._pending_search_text = self._filter_query.search_text
        self._apply_filter_query_change()
        self.statusBar().showMessage(f"Applied saved search: {preset.name}")

    def _save_current_filter_preset(self) -> None:
        if not self._filter_query.has_active_filters:
            self.statusBar().showMessage("Set a search or filter before saving a preset")
            return

        existing = self._matching_saved_filter_preset()
        initial_name = existing.name if existing is not None else self._matching_filter_preset_label(self._filter_query)
        name, accepted = QInputDialog.getText(self, "Save Current Search", "Preset name", text=initial_name)
        if not accepted:
            return
        name = (name or "").strip()
        if not name:
            return

        existing_index = next(
            (index for index, preset in enumerate(self._saved_filter_presets) if preset.name.casefold() == name.casefold()),
            None,
        )
        if existing_index is not None:
            overwrite = QMessageBox.question(
                self,
                "Overwrite Saved Search",
                f"A saved search named '{self._saved_filter_presets[existing_index].name}' already exists. Overwrite it?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if overwrite != QMessageBox.StandardButton.Yes:
                return
            self._saved_filter_presets[existing_index] = SavedFilterPreset(name=name, query=self._copy_filter_query(self._filter_query))
        else:
            self._saved_filter_presets.append(SavedFilterPreset(name=name, query=self._copy_filter_query(self._filter_query)))

        self._save_saved_filter_presets()
        self._refresh_filter_toolbar_menu()
        self._update_filter_summary()
        self._update_action_states()
        self.statusBar().showMessage(f"Saved search: {name}")

    def _delete_current_filter_preset(self) -> None:
        preset = self._matching_saved_filter_preset()
        if preset is None:
            self.statusBar().showMessage("The current filter state is not one of your saved searches")
            return

        confirm = QMessageBox.question(
            self,
            "Delete Saved Search",
            f"Delete the saved search '{preset.name}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return

        self._saved_filter_presets = [item for item in self._saved_filter_presets if item.name.casefold() != preset.name.casefold()]
        self._save_saved_filter_presets()
        self._refresh_filter_toolbar_menu()
        self._update_filter_summary()
        self._update_action_states()
        self.statusBar().showMessage(f"Deleted saved search: {preset.name}")

    def _handle_system_color_scheme_changed(self) -> None:
        if self._appearance_mode == AppearanceMode.AUTO:
            self._apply_appearance()

    def _apply_appearance(self) -> None:
        app = QApplication.instance()
        if app is None:
            return
        self._theme = resolve_theme(self._appearance_mode, app)
        app.setPalette(build_app_palette(self._theme))
        app.setStyleSheet(build_app_stylesheet(self._theme))
        self._write_child_sync_state()
        self._update_dynamic_action_icons()
        if self.workspace_docks is not None:
            self.workspace_docks.apply_theme(self._theme)
        self.grid.apply_theme(self._theme)
        self.preview.apply_theme(self._theme)
        self._refresh_mode_tabs_width()
        self._update_action_states()

    def _refresh_mode_tabs_width(self) -> None:
        self.mode_tabs.ensurePolished()
        self.mode_tabs.adjustSize()
        target_width = max(self.mode_tabs.sizeHint().width(), self.mode_tabs.minimumSizeHint().width())
        target_width += 10
        self.mode_tabs.setMinimumWidth(target_width)
        self.mode_tabs.setMaximumWidth(target_width)

    def _update_dynamic_action_icons(self) -> None:
        if self.actions is None or self._theme is None:
            return
        self.actions.undo.setIcon(QIcon())

    def _prepare_child_sync_state_path(self) -> Path:
        base_dir = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation)
        if not base_dir:
            base_dir = str(Path.home() / "AppData" / "Local" / "ImageTriage")
        sync_dir = Path(base_dir) / "child_sync"
        sync_dir.mkdir(parents=True, exist_ok=True)
        return sync_dir / f"host_state_{os.getpid()}.json"

    def _current_child_appearance_mode(self) -> str:
        if self._theme is not None:
            return self._theme.name
        return self._appearance_mode.value

    def _write_child_sync_state(self, *, shutdown_requested: bool = False) -> None:
        payload = {
            "parent_pid": os.getpid(),
            "appearance_mode": self._current_child_appearance_mode(),
            "shutdown_requested": shutdown_requested,
            "updated_at": time.time(),
        }
        temp_path = self._child_sync_state_path.with_suffix(".tmp")
        try:
            temp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            temp_path.replace(self._child_sync_state_path)
        except OSError:
            pass

    def _register_child_process(self, process: subprocess.Popen[str], *, name: str) -> None:
        pid = int(getattr(process, "pid", 0) or 0)
        if pid <= 0:
            return
        self._child_processes[pid] = ChildAppProcess(name=name, process=process)
        self._prune_child_processes()

    def _prune_child_processes(self) -> None:
        finished = [
            pid
            for pid, child in self._child_processes.items()
            if child.process.poll() is not None
        ]
        for pid in finished:
            self._child_processes.pop(pid, None)

    def _shutdown_child_processes(self) -> None:
        self._prune_child_processes()
        if not self._child_processes:
            return

        self._write_child_sync_state(shutdown_requested=True)
        graceful_deadline = time.monotonic() + 1.5
        while self._child_processes and time.monotonic() < graceful_deadline:
            QApplication.processEvents()
            self._prune_child_processes()
            if self._child_processes:
                time.sleep(0.05)

        for child in list(self._child_processes.values()):
            if child.process.poll() is None:
                try:
                    child.process.terminate()
                except OSError:
                    continue

        forced_deadline = time.monotonic() + 1.0
        while self._child_processes and time.monotonic() < forced_deadline:
            QApplication.processEvents()
            self._prune_child_processes()
            if self._child_processes:
                time.sleep(0.05)

        for child in list(self._child_processes.values()):
            if child.process.poll() is None:
                try:
                    child.process.kill()
                except OSError:
                    continue

        self._prune_child_processes()

    def _cleanup_child_sync_state(self) -> None:
        try:
            self._child_process_timer.stop()
        except RuntimeError:
            pass
        try:
            self._child_sync_state_path.unlink(missing_ok=True)
        except OSError:
            pass

    def _apply_default_workspace(self) -> None:
        if self.workspace_docks is None:
            return
        self.workspace_docks.reset_layout()

    def _set_appearance_mode(self, mode: AppearanceMode) -> None:
        normalized = mode if isinstance(mode, AppearanceMode) else parse_appearance_mode(mode)
        if self._appearance_mode == normalized:
            return
        self._appearance_mode = normalized
        self._settings.setValue(self.APPEARANCE_KEY, normalized.value)
        self._apply_appearance()
        self.statusBar().showMessage(f"Appearance set to {normalized.value}")

    def _restore_window_state(self) -> None:
        restored = restore_window_layout(self, self._settings, self.GEOMETRY_KEY, self.STATE_KEY, self.workspace_docks)
        if not restored:
            self._apply_default_workspace()

    def _save_window_state(self) -> None:
        save_window_layout(self, self._settings, self.GEOMETRY_KEY, self.STATE_KEY, self.workspace_docks)

    def _finish_startup_restore(self) -> None:
        self._load_start_folder()
        self._restore_ai_results()
        QTimer.singleShot(0, self._maybe_prompt_for_ai_model_install)

    def _managed_ai_model_installation(self) -> AIModelInstallation:
        runtime_installation = self._ai_runtime.model_installation
        if runtime_installation is not None:
            return runtime_installation
        return self._ai_model_installation

    def _ai_model_available(self) -> bool:
        runtime_installation = self._ai_runtime.model_installation
        if runtime_installation is not None:
            return runtime_installation.is_installed

        model_name = (self._ai_runtime.model_name or "").strip()
        if not model_name:
            return False
        path = Path(model_name).expanduser()
        if path.is_absolute() or "/" in model_name or "\\" in model_name or model_name.startswith("."):
            if not path.exists():
                return False
            if path.is_dir():
                return (path / "config.json").exists() and (path / "model.safetensors").exists()
            return True
        return True

    def _ai_model_explanation_text(self) -> str:
        installation = self._managed_ai_model_installation()
        return (
            "Image Triage uses a local DINOv2 model for AI culling, training-data preparation, "
            "and reference-bank extraction.\n\n"
            "Without that model the AI generation and training tools stay disabled, but you can "
            "still open any AI results that were already generated.\n\n"
            f"Download size: about {DEFAULT_AI_MODEL_SIZE_MB} MB\n"
            f"Install location:\n{installation.install_dir}"
        )

    def _ensure_ai_model_available(self, *, title: str) -> bool:
        if self._ai_model_available():
            return True
        if self._active_ai_model_task is not None:
            self.statusBar().showMessage("AI model download already running.")
            return False
        prompt = QMessageBox.question(
            self,
            title,
            self._ai_model_explanation_text() + "\n\nDownload the AI model now?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if prompt == QMessageBox.StandardButton.Yes:
            self._prompt_for_ai_model_install(automatic=False)
        return False

    def _maybe_prompt_for_ai_model_install(self) -> None:
        if not getattr(sys, "frozen", False):
            return
        if self._ai_runtime.model_installation is None or self._ai_model_available():
            return
        if self._active_ai_model_task is not None:
            return
        if self._settings.value(self.AI_MODEL_PROMPTED_KEY, False, bool):
            return
        self._settings.setValue(self.AI_MODEL_PROMPTED_KEY, True)
        self._prompt_for_ai_model_install(automatic=True)

    def _prompt_for_ai_model_install(self, *, automatic: bool) -> None:
        installation = self._managed_ai_model_installation()
        dialog = QMessageBox(self)
        dialog.setIcon(QMessageBox.Icon.Information)
        dialog.setWindowTitle("Install AI Model")
        dialog.setText("Install the DINOv2 AI model now?")
        dialog.setInformativeText(self._ai_model_explanation_text())
        checkbox = QCheckBox("Download the AI model now (recommended)", dialog)
        checkbox.setChecked(True)
        dialog.setCheckBox(checkbox)
        continue_button = dialog.addButton(
            "Continue" if automatic else "Start Download",
            QMessageBox.ButtonRole.AcceptRole,
        )
        dialog.addButton(
            "Later" if automatic else "Cancel",
            QMessageBox.ButtonRole.RejectRole,
        )
        dialog.exec()
        if dialog.clickedButton() != continue_button:
            self.statusBar().showMessage("AI model download skipped for now.")
            return
        if not checkbox.isChecked():
            self.statusBar().showMessage("AI model download skipped for now.")
            return
        force_download = installation.is_installed
        self._start_ai_model_download(force=force_download)

    def _download_ai_model(self) -> None:
        if self._active_ai_model_task is not None:
            self.statusBar().showMessage("AI model download already running.")
            return
        self._prompt_for_ai_model_install(automatic=False)

    def _start_ai_model_download(self, *, force: bool = False) -> None:
        installation = self._managed_ai_model_installation()
        task = AIModelDownloadTask(installation=installation, force=force)
        task.signals.started.connect(self._handle_ai_model_download_started, Qt.ConnectionType.QueuedConnection)
        task.signals.progress.connect(self._handle_ai_model_download_progress, Qt.ConnectionType.QueuedConnection)
        task.signals.finished.connect(self._handle_ai_model_download_finished, Qt.ConnectionType.QueuedConnection)
        task.signals.failed.connect(self._handle_ai_model_download_failed, Qt.ConnectionType.QueuedConnection)
        self._active_ai_model_task = task
        controller = self._show_job_progress_dialog(
            key=self._ai_model_job_key,
            total_steps=1,
            spec=JobSpec(
                title="Downloading AI Model",
                preparing_label="Preparing AI model download...",
                running_label="Downloading AI model...",
                indeterminate_label="Downloading AI model...",
            ),
        )
        controller.setRange(0, 0)
        controller.setValue(0)
        self._update_action_states()
        self._update_ai_toolbar_state()
        self.statusBar().showMessage("Starting AI model download...")
        self._ai_model_pool.start(task)

    def _handle_ai_model_download_started(self, install_dir: str) -> None:
        controller = self._show_job_progress_dialog(
            key=self._ai_model_job_key,
            total_steps=1,
            spec=JobSpec(
                title="Downloading AI Model",
                preparing_label="Preparing AI model download...",
                running_label="Downloading AI model...",
                indeterminate_label="Downloading AI model...",
            ),
        )
        controller.setRange(0, 0)
        controller.setValue(0)
        controller.setLabelText(f"Downloading AI model to\n{install_dir}")
        self.statusBar().showMessage("Downloading AI model...")

    def _handle_ai_model_download_progress(self, filename: str, current: int, total: int) -> None:
        controller = self._show_job_progress_dialog(
            key=self._ai_model_job_key,
            total_steps=max(1, total if total > 0 else 1),
            spec=JobSpec(
                title="Downloading AI Model",
                preparing_label="Preparing AI model download...",
                running_label="Downloading AI model...",
                indeterminate_label="Downloading AI model...",
            ),
        )
        if total > 0:
            controller.setRange(0, total)
            controller.setValue(min(current, total))
            size_mb = total / (1024 * 1024)
            downloaded_mb = current / (1024 * 1024)
            controller.setLabelText(f"Downloading {filename} ({downloaded_mb:.1f} / {size_mb:.1f} MB)")
        else:
            controller.setRange(0, 0)
            controller.setValue(0)
            controller.setLabelText(f"Downloading {filename}...")

    def _handle_ai_model_download_finished(self, install_dir: str) -> None:
        self._active_ai_model_task = None
        self._close_job_progress_dialog(self._ai_model_job_key)
        self._update_action_states()
        self._update_ai_toolbar_state()
        self.statusBar().showMessage("AI model installed.")
        QMessageBox.information(
            self,
            "AI Model Installed",
            f"The AI model is ready.\n\nInstalled to:\n{install_dir}",
        )

    def _handle_ai_model_download_failed(self, message: str) -> None:
        self._active_ai_model_task = None
        self._close_job_progress_dialog(self._ai_model_job_key)
        self._update_action_states()
        self._update_ai_toolbar_state()
        QMessageBox.warning(self, "AI Model Download", f"Could not download the AI model.\n\n{message}")
        self.statusBar().showMessage("AI model download failed.")

    def closeEvent(self, event: QCloseEvent) -> None:
        self._remember_current_folder_view_state()
        self._folder_watch_refresh_timer.stop()
        if self._folder_watcher.directories():
            self._folder_watcher.removePaths(list(self._folder_watcher.directories()))
        self._annotation_persistence_queue.flush_blocking()
        self._shutdown_child_processes()
        self._cleanup_child_sync_state()
        self._save_window_state()
        super().closeEvent(event)

    def _load_start_folder(self) -> None:
        last_folder = self._settings.value(self.LAST_FOLDER_KEY, "", str)
        if last_folder and os.path.isdir(last_folder):
            self._select_folder(last_folder, sync_tree=False, chunked_restore=True)
            self.folder_tree.clearSelection()
            self.folder_tree.setCurrentIndex(QModelIndex())

    def _folder_drive_root(self, folder: str | None = None) -> str:
        target = folder or self._current_folder
        if not target:
            return ""
        anchor = Path(target).anchor
        return anchor if anchor else str(Path(target).resolve().anchor)

    def _drive_type(self, root: str) -> int:
        if not root:
            return 0
        try:
            return int(ctypes.windll.kernel32.GetDriveTypeW(str(root)))
        except Exception:
            return 0

    def _is_temporary_storage_folder(self, folder: str | None = None) -> bool:
        return self._drive_type(self._folder_drive_root(folder)) == 2

    def _is_slow_source_folder(self, folder: str | None = None) -> bool:
        drive_type = self._drive_type(self._folder_drive_root(folder))
        return drive_type in {2, 4}

    def _recycle_root_for_folder(self, folder: str | None = None) -> Path:
        target_folder = folder or self._current_folder
        if target_folder:
            target_path = Path(target_folder)
            recycle_parts: list[str] = []
            for part in target_path.parts:
                recycle_parts.append(part)
                if part.casefold() == "recycle bin":
                    return Path(*recycle_parts)
        if self._is_temporary_storage_folder(target_folder):
            base_folder = Path(target_folder) if target_folder else Path(self._folder_drive_root())
            parent_folder = base_folder.parent if base_folder.parent != base_folder else base_folder
            return parent_folder / "recycle bin"
        app_data = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation)
        root = Path(app_data) if app_data else Path.home() / ".image-triage"
        return root / "safe-trash"

    def _refresh_recycle_button(self) -> None:
        if self.actions is None:
            return
        if self._is_temporary_storage_folder():
            recycle_root = self._recycle_root_for_folder()
            has_contents = recycle_root.exists() and any(recycle_root.iterdir())
            self.actions.empty_recycle_bin.setEnabled(has_contents)
            self.actions.empty_recycle_bin.setToolTip(
                "Permanently delete everything in this folder's local recycle bin."
            )
            self._update_action_states()
            return
        self.actions.empty_recycle_bin.setEnabled(False)
        self.actions.empty_recycle_bin.setToolTip(
            "Available when browsing a removable drive with items in its Image Triage recycle folder."
        )
        self._update_action_states()

    def _choose_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Choose Folder", self._current_folder or QDir.homePath())
        if folder:
            self._select_folder(folder)

    def _select_folder(self, folder: str, *, sync_tree: bool = True, chunked_restore: bool = False) -> None:
        if sync_tree:
            index = self.folder_model.index(folder)
            if index.isValid():
                self.folder_tree.setCurrentIndex(index)
        self._load_folder(folder, chunked_restore=chunked_restore)

    def _configure_toolbar_context_target(self, widget: QWidget | None, mode: str) -> None:
        if widget is None:
            return
        widget.setProperty(self._toolbar_context_mode_property, mode)
        if widget.property(self._toolbar_context_installed_property):
            return
        widget.installEventFilter(self)
        widget.setProperty(self._toolbar_context_installed_property, True)

    def _toolbar_context_mode_for(self, watched: object) -> str:
        if not isinstance(watched, QObject):
            return ""
        mode = watched.property(self._toolbar_context_mode_property)
        if not isinstance(mode, str):
            return ""
        if mode == "workspace":
            return self._ui_mode
        if mode in self.WORKSPACE_TOOLBAR_DEFAULTS:
            return mode
        return ""

    def _handle_toolbar_context_event(self, mode: str, event) -> bool:
        if event.type() != QEvent.Type.ContextMenu:
            return False
        self._show_toolbar_context_menu(mode, event.globalPos())
        return True

    def _show_toolbar_context_menu(self, mode: str, global_pos) -> None:
        target_mode = mode if mode in self.WORKSPACE_TOOLBAR_DEFAULTS else self._ui_mode
        menu = QMenu(self)
        action = self.actions.customize_workspace_toolbar if self.actions is not None else None
        toolbar_label = {
            "primary": "Top Toolbar",
            "manual": "Manual Toolbar",
            "ai": "AI Toolbar",
        }.get(target_mode, "Toolbar")
        customize_action = menu.addAction(self._menu_text_with_action_shortcut(f"Customize {toolbar_label}...", action))
        chosen = menu.exec(global_pos)
        if chosen == customize_action:
            self._show_workspace_toolbar_editor(target_mode)

    def _handle_tree_selection(self, index) -> None:
        folder = self.folder_model.filePath(index)
        if folder:
            self._load_folder(folder)

    def _handle_favorite_activated(self, item: QListWidgetItem) -> None:
        folder = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(folder, str) and os.path.isdir(folder):
            self._select_folder(folder)

    def eventFilter(self, watched, event) -> bool:
        toolbar_mode = self._toolbar_context_mode_for(watched)
        if toolbar_mode and self._handle_toolbar_context_event(toolbar_mode, event):
            return True
        if hasattr(self, "central_container") and watched is self.central_container:
            if event.type() == QEvent.Type.Resize and self._toolbar_edit_mode:
                self._position_workspace_toolbar_editor()
        if hasattr(self, "primary_toolbar") and watched is self.primary_toolbar:
            if event.type() == QEvent.Type.Resize and self._toolbar_edit_mode:
                self._position_workspace_toolbar_editor()
            if event.type() == QEvent.Type.MouseButtonDblClick:
                self._show_workspace_toolbar_editor("primary")
                return True
        if hasattr(self, "workspace_bar") and watched is self.workspace_bar:
            if event.type() == QEvent.Type.Resize and self._toolbar_edit_mode:
                self._position_workspace_toolbar_editor()
            if event.type() == QEvent.Type.MouseButtonDblClick:
                self._show_workspace_toolbar_editor(self._ui_mode)
                return True
        if hasattr(self, "toolbar_stack") and watched is self.toolbar_stack and event.type() == QEvent.Type.MouseButtonDblClick:
            self._show_workspace_toolbar_editor(self._ui_mode)
            return True
        if hasattr(self, "manual_toolbar") and watched is self.manual_toolbar and event.type() == QEvent.Type.MouseButtonDblClick:
            self._show_workspace_toolbar_editor("manual")
            return True
        if hasattr(self, "ai_toolbar") and watched is self.ai_toolbar and event.type() == QEvent.Type.MouseButtonDblClick:
            self._show_workspace_toolbar_editor("ai")
            return True
        if watched is self.folder_tree.viewport():
            handled = self._handle_record_drop_event(event, source="folder_tree")
            if handled is not None:
                return handled
        if watched is self.favorites_list.viewport():
            handled = self._handle_record_drop_event(event, source="favorites")
            if handled is not None:
                return handled
        return super().eventFilter(watched, event)

    def _handle_record_drop_event(self, event, *, source: str) -> bool | None:
        event_type = event.type()
        if event_type not in {
            QEvent.Type.DragEnter,
            QEvent.Type.DragMove,
            QEvent.Type.DragLeave,
            QEvent.Type.Drop,
        }:
            return None
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
            self._handle_record_drop(paths, destination_folder, copy_requested=copy_requested)
            return True

        event.accept()
        return True

    def _folder_drop_target(self, point) -> str:
        index = self.folder_tree.indexAt(point)
        if not index.isValid():
            return ""
        folder = self.folder_model.filePath(index)
        return folder if folder and os.path.isdir(folder) else ""

    def _favorite_drop_target(self, point) -> str:
        item = self.favorites_list.itemAt(point)
        if item is None:
            return ""
        folder = item.data(Qt.ItemDataRole.UserRole)
        return folder if isinstance(folder, str) and os.path.isdir(folder) else ""

    def _drag_drop_prefers_copy(self, event) -> bool:
        modifiers = QApplication.keyboardModifiers()
        if hasattr(event, "keyboardModifiers"):
            modifiers = event.keyboardModifiers()
        return bool(modifiers & Qt.KeyboardModifier.ControlModifier)

    def _can_accept_record_drop(self, destination_folder: str) -> bool:
        if not destination_folder or not os.path.isdir(destination_folder):
            return False
        if not self._current_folder:
            return False
        return normalized_path_key(destination_folder) != normalized_path_key(self._current_folder)

    def _show_folder_tree_context_menu(self, point) -> None:
        index = self.folder_tree.indexAt(point)
        if not index.isValid():
            return
        folder = self.folder_model.filePath(index)
        if not folder or not os.path.isdir(folder):
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
        catalog_action = menu.addAction("Remove From Catalog" if self._library_store.is_catalog_root(folder) else "Add To Catalog")
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
            self._create_folder_prompt(folder, select_created=True)
            return
        if chosen == extract_archive_action:
            self._extract_archive_into_folder_prompt(folder)
            return
        if chosen == rename_action:
            self._rename_folder(folder)
            return
        if chosen == move_action:
            self._move_folder_prompt(folder)
            return
        if chosen == delete_action:
            self._delete_folder_prompt(folder)
            return
        if chosen == catalog_action:
            if self._library_store.is_catalog_root(folder):
                self._library_store.remove_catalog_root(folder)
                self._refresh_catalog_menu()
                self.statusBar().showMessage(f"Removed catalog root: {Path(folder).name}")
            else:
                self._library_store.add_catalog_root(folder)
                self._refresh_catalog_menu()
                self._start_catalog_refresh((folder,), label=f"Indexing {Path(folder).name} for catalog...")
            return
        if chosen == favorite_action:
            if is_favorite:
                self._remove_favorite(folder)
            else:
                self._add_favorite(folder)

    @staticmethod
    def _is_filesystem_root(folder: str) -> bool:
        path = Path(folder)
        return str(path.parent) == str(path)

    @staticmethod
    def _folder_is_same_or_descendant(path: str, root_folder: str) -> bool:
        try:
            resolved_path = Path(path).resolve(strict=False)
            resolved_root = Path(root_folder).resolve(strict=False)
            resolved_path.relative_to(resolved_root)
            return True
        except ValueError:
            return False

    @classmethod
    def _remap_folder_path(cls, path: str, source_root: str, destination_root: str) -> str:
        if not cls._folder_is_same_or_descendant(path, source_root):
            return path
        resolved_path = Path(path).resolve(strict=False)
        resolved_root = Path(source_root).resolve(strict=False)
        relative = resolved_path.relative_to(resolved_root)
        if not relative.parts:
            return destination_root
        return str(Path(destination_root) / relative)

    def _remap_folder_references(self, source_root: str, destination_root: str) -> str:
        favorites: list[str] = []
        seen_favorites: set[str] = set()
        for path in self._favorites:
            mapped = self._remap_folder_path(path, source_root, destination_root)
            if not os.path.isdir(mapped):
                continue
            key = normalized_path_key(mapped)
            if key in seen_favorites:
                continue
            seen_favorites.add(key)
            favorites.append(mapped)
        self._favorites = favorites
        self._save_favorites()
        self._refresh_favorites_panel()

        recent_destinations: list[str] = []
        seen_destinations: set[str] = set()
        for path in self._recent_destinations:
            mapped = self._remap_folder_path(path, source_root, destination_root)
            if not os.path.isdir(mapped):
                continue
            key = normalized_path_key(mapped)
            if key in seen_destinations:
                continue
            seen_destinations.add(key)
            recent_destinations.append(mapped)
        self._recent_destinations = recent_destinations[:10]
        self._save_recent_destinations()

        recent_folders: list[str] = []
        seen_recent_folders: set[str] = set()
        for path in self._recent_folders:
            mapped = self._remap_folder_path(path, source_root, destination_root)
            if not os.path.isdir(mapped):
                continue
            key = normalized_path_key(mapped)
            if key in seen_recent_folders:
                continue
            seen_recent_folders.add(key)
            recent_folders.append(mapped)
        self._recent_folders = recent_folders[:12]
        self._save_recent_folders()
        self._refresh_recent_folder_combos()

        if self._current_folder and self._folder_is_same_or_descendant(self._current_folder, source_root):
            return self._remap_folder_path(self._current_folder, source_root, destination_root)
        return destination_root

    def _create_folder_prompt(self, parent_folder: str, *, select_created: bool) -> str | None:
        folder_name, accepted = QInputDialog.getText(
            self,
            "New Folder",
            "Folder name",
            text="New Folder",
        )
        if not accepted:
            return None
        folder_name = (folder_name or "").strip()
        if not folder_name:
            return None
        try:
            created = create_folder(parent_folder, folder_name)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Create Folder Failed", f"Could not create folder.\n\n{exc}")
            return None
        self._remember_recent_destination(created)
        self._refresh_folder_tree()
        if select_created:
            self._select_folder(created)
        self.statusBar().showMessage(f"Created folder: {Path(created).name}")
        return created

    def _rename_folder(self, folder: str) -> None:
        current_name = Path(folder).name
        new_name, accepted = QInputDialog.getText(
            self,
            "Rename Folder",
            "Folder name",
            text=current_name,
        )
        if not accepted:
            return
        new_name = (new_name or "").strip()
        if not new_name or new_name == current_name:
            return
        try:
            destination = rename_folder(folder, new_name)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Rename Failed", f"Could not rename folder.\n\n{exc}")
            return
        target_folder = self._remap_folder_references(folder, destination)
        self._refresh_folder_tree()
        self._select_folder(target_folder if os.path.isdir(target_folder) else destination)
        self.statusBar().showMessage(f"Renamed folder to {new_name}")

    def _move_folder_prompt(self, folder: str) -> None:
        destination_parent = QFileDialog.getExistingDirectory(
            self,
            "Move Folder",
            str(Path(folder).parent),
        )
        if not destination_parent:
            return
        try:
            destination = move_folder(folder, destination_parent)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Move Folder Failed", f"Could not move folder.\n\n{exc}")
            return
        target_folder = self._remap_folder_references(folder, destination)
        self._remember_recent_destination(str(Path(destination).parent))
        self._refresh_folder_tree()
        self._select_folder(target_folder if os.path.isdir(target_folder) else destination)
        self.statusBar().showMessage(f"Moved folder to {destination}")

    def _delete_folder_prompt(self, folder: str) -> None:
        if self._is_filesystem_root(folder):
            return
        try:
            has_contents = any(Path(folder).iterdir())
        except OSError as exc:
            QMessageBox.warning(self, "Delete Failed", f"Could not inspect folder.\n\n{exc}")
            return

        message = f"Delete the empty folder '{Path(folder).name}'?"
        if has_contents:
            message = (
                f"Delete the folder '{Path(folder).name}' and everything inside it?\n\n"
                "This will permanently remove all contents."
            )
        confirmation = QMessageBox.question(
            self,
            "Delete Folder",
            message,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirmation != QMessageBox.StandardButton.Yes:
            return
        try:
            delete_folder(folder)
        except OSError as exc:
            QMessageBox.warning(self, "Delete Failed", f"Could not delete folder.\n\n{exc}")
            return

        deleted_key = normalized_path_key(folder)
        self._favorites = [
            path
            for path in self._favorites
            if not (normalized_path_key(path) == deleted_key or normalized_path_key(path).startswith(deleted_key + os.sep))
        ]
        self._save_favorites()
        self._refresh_favorites_panel()
        self._recent_destinations = [
            path
            for path in self._recent_destinations
            if not (normalized_path_key(path) == deleted_key or normalized_path_key(path).startswith(deleted_key + os.sep))
        ]
        self._save_recent_destinations()
        self._recent_folders = [
            path
            for path in self._recent_folders
            if not (normalized_path_key(path) == deleted_key or normalized_path_key(path).startswith(deleted_key + os.sep))
        ]
        self._save_recent_folders()
        self._refresh_recent_folder_combos()

        replacement_folder = str(Path(folder).parent)
        self._refresh_folder_tree()
        if self._current_folder and (
            normalized_path_key(self._current_folder) == deleted_key
            or normalized_path_key(self._current_folder).startswith(deleted_key + os.sep)
        ):
            if os.path.isdir(replacement_folder):
                self._select_folder(replacement_folder)
            else:
                self._current_folder = ""
                self._set_scope_state(kind="folder", scope_id="", label="")
                self._apply_loaded_records([])
        self.statusBar().showMessage(f"Deleted folder: {Path(folder).name}")

    def _open_batch_rename_dialog(
        self,
        records: list[ImageRecord],
        *,
        title: str,
        scope_label: str,
        folder: str,
    ) -> bool:
        if not records:
            return False
        dialog = BatchRenameDialog(records, title=title, scope_label=scope_label, parent=self)
        if self._exec_dialog_with_geometry(dialog, "batch_rename") != dialog.DialogCode.Accepted:
            return False
        preview = dialog.accepted_preview()
        if not preview.can_apply:
            return False
        return self._apply_batch_rename_preview(preview, folder=folder)

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

    def _apply_batch_rename_preview(self, preview: BatchRenamePreview, *, folder: str) -> bool:
        if not preview.planned_moves:
            return False
        if self._active_batch_rename_task is not None:
            QMessageBox.information(self, "Batch Rename Running", "A batch rename is already in progress.")
            return False
        renamed_items = [item for item in preview.items if item.status == "Rename"]
        is_current_folder = normalized_path_key(folder) == normalized_path_key(self._current_folder)
        loaded_annotations: dict[str, SessionAnnotation] = {}
        if not is_current_folder:
            loaded_annotations = self._decision_store.load_annotations(self._session_id, [item.record for item in renamed_items])
        self._batch_rename_context = BatchRenameExecutionContext(
            preview=preview,
            folder=folder,
            is_current_folder=is_current_folder,
            loaded_annotations=loaded_annotations,
            current_path_before=self._current_visible_record_path() if is_current_folder else None,
        )
        task = BatchRenameApplyTask(preview.planned_moves)
        task.signals.started.connect(self._handle_batch_rename_started, Qt.ConnectionType.QueuedConnection)
        task.signals.progress.connect(self._handle_batch_rename_progress, Qt.ConnectionType.QueuedConnection)
        task.signals.finished.connect(self._handle_batch_rename_finished, Qt.ConnectionType.QueuedConnection)
        task.signals.failed.connect(self._handle_batch_rename_failed, Qt.ConnectionType.QueuedConnection)
        self._active_batch_rename_task = task
        self._batch_rename_pool.start(task)
        self.statusBar().showMessage(f"Applying batch rename for {len(renamed_items)} image bundle(s)...")
        return True

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
        QApplication.processEvents()
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
        QApplication.processEvents()
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
        QApplication.processEvents()
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
        self.statusBar().showMessage(f"Running workflow recipe: {plan.recipe.name}")
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
            QApplication.processEvents()

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

    def _start_catalog_refresh(self, root_paths: tuple[str, ...] | list[str], *, label: str) -> bool:
        roots = tuple(normalize_filesystem_path(path) for path in root_paths if normalize_filesystem_path(path))
        if not roots:
            return False
        if self._active_catalog_task is not None:
            QMessageBox.information(self, "Catalog Refresh Running", "A catalog refresh is already in progress.")
            return False
        dialog = self._show_catalog_progress_dialog(max(1, len(roots)))
        dialog.setLabelText(label)
        QApplication.processEvents()
        task = CatalogRefreshTask(roots)
        task.signals.started.connect(self._handle_catalog_refresh_started, Qt.ConnectionType.QueuedConnection)
        task.signals.progress.connect(self._handle_catalog_refresh_progress, Qt.ConnectionType.QueuedConnection)
        task.signals.finished.connect(self._handle_catalog_refresh_finished, Qt.ConnectionType.QueuedConnection)
        task.signals.failed.connect(self._handle_catalog_refresh_failed, Qt.ConnectionType.QueuedConnection)
        self._active_catalog_task = task
        self._catalog_context = CatalogExecutionContext(root_paths=roots, label=label)
        self._catalog_pool.start(task)
        self.statusBar().showMessage(label)
        return True

    def _handle_catalog_refresh_started(self, total_roots: int) -> None:
        dialog = self._show_catalog_progress_dialog(total_roots)
        context = self._catalog_context
        dialog.setLabelText(context.label if context is not None else "Refreshing global catalog...")

    def _handle_catalog_refresh_progress(self, current: int, total: int, message: str) -> None:
        dialog = self._show_catalog_progress_dialog(total)
        self._update_progress_dialog(
            dialog,
            current=current,
            total=total,
            message=message,
            default_label="Refreshing global catalog...",
        )

    def _handle_catalog_refresh_finished(self, result: object) -> None:
        summary = result if isinstance(result, CatalogRefreshSummary) else None
        self._active_catalog_task = None
        self._catalog_context = None
        self._close_catalog_progress_dialog()
        self._refresh_catalog_menu()
        if summary is None:
            self.statusBar().showMessage("Catalog refresh complete")
            return
        message = f"Catalog refreshed: {summary.record_count} image bundle(s) across {summary.folder_count} folder(s)"
        if summary.missing_roots:
            message = f"{message} | Missing roots: {len(summary.missing_roots)}"
        self.statusBar().showMessage(message)

    def _handle_catalog_refresh_failed(self, message: str) -> None:
        self._active_catalog_task = None
        self._catalog_context = None
        self._close_catalog_progress_dialog()
        QMessageBox.warning(self, "Catalog Refresh Failed", f"Could not refresh the global catalog.\n\n{message}")

    def _handle_batch_rename_started(self, total_steps: int) -> None:
        dialog = self._show_batch_rename_progress_dialog(total_steps)
        dialog.setLabelText("Preparing batch rename...")

    def _handle_batch_rename_progress(self, current: int, total: int, message: str) -> None:
        dialog = self._show_batch_rename_progress_dialog(total)
        self._update_progress_dialog(
            dialog,
            current=current,
            total=total,
            message=message,
            default_label="Applying batch rename...",
        )

    def _handle_batch_rename_finished(self, _applied_moves: object) -> None:
        context = self._batch_rename_context
        dialog = self._batch_rename_progress_dialog
        if dialog is not None:
            dialog.setRange(0, 0)
            dialog.setValue(0)
            dialog.setLabelText("Updating library...")
            QApplication.processEvents()

        try:
            if context is not None:
                self._finalize_batch_rename(context)
        except Exception as exc:
            QMessageBox.warning(self, "Batch Rename Finalize Failed", f"The files were renamed, but the library refresh failed.\n\n{exc}")
        finally:
            self._active_batch_rename_task = None
            self._batch_rename_context = None
            self._close_batch_rename_progress_dialog()

    def _handle_batch_rename_failed(self, message: str) -> None:
        self._active_batch_rename_task = None
        self._batch_rename_context = None
        self._close_batch_rename_progress_dialog()
        QMessageBox.warning(self, "Batch Rename Failed", f"Could not apply the batch rename.\n\n{message}")

    def _show_batch_rename_progress_dialog(self, total_steps: int) -> QProgressDialog:
        dialog = self._show_job_progress_dialog(
            key="batch_rename",
            total_steps=total_steps,
            spec=JobSpec(
                title="Batch Rename",
                preparing_label="Preparing batch rename...",
                running_label="Applying batch rename...",
                indeterminate_label="Updating library...",
                window_modality=Qt.WindowModality.WindowModal,
                stays_on_top=False,
            ),
        )
        self._batch_rename_progress_dialog = dialog
        return dialog

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
            QApplication.processEvents()

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
            QApplication.processEvents()

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
            QApplication.processEvents()

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

    def _handle_ai_training_started(self, total_steps: int) -> None:
        context = self._ai_training_context
        dialog = self._show_ai_training_progress_dialog(
            total_steps,
            title=context.title if context is not None else "AI Training",
        )
        self._ai_training_stage_text = "Preparing AI training task..."
        dialog.set_status_text(self._ai_training_stage_text)
        self.statusBar().showMessage("Starting AI training task...")
        self._update_ai_toolbar_state()

    def _handle_ai_training_stage(self, stage_index: int, stage_total: int, message: str) -> None:
        dialog = self._show_ai_training_progress_dialog(
            max(1, stage_total),
            title=self._ai_training_context.title if self._ai_training_context is not None else "AI Training",
        )
        prefix = f"[{max(1, stage_index)}/{max(1, stage_total)}] "
        self._ai_training_stage_text = prefix + (message or "Running AI training task...")
        dialog.set_progress(0, 0)
        dialog.set_status_text(self._ai_training_stage_text)
        if self._ai_training_stats_dialog is not None:
            self._ai_training_stats_dialog.set_stage_text(self._ai_training_stage_text)
        self.statusBar().showMessage(message or "Running AI training task...")

    def _handle_ai_training_progress(self, current: int, total: int, message: str) -> None:
        dialog = self._show_ai_training_progress_dialog(
            max(1, total) if total > 0 else 1,
            title=self._ai_training_context.title if self._ai_training_context is not None else "AI Training",
        )
        self._ai_training_stage_text = message or self._ai_training_stage_text or "Running AI training task..."
        dialog.set_progress(current, total)
        dialog.set_status_text(self._ai_training_stage_text)
        if self._ai_training_stats_dialog is not None:
            self._ai_training_stats_dialog.set_stage_text(self._ai_training_stage_text)

    def _handle_ai_training_log(self, line: str) -> None:
        message = (line or "").strip()
        if not message:
            return
        self._ai_training_log_lines.append(message)
        if len(self._ai_training_log_lines) > 1200:
            self._ai_training_log_lines = self._ai_training_log_lines[-1200:]
        if self._ai_training_stats_dialog is not None:
            self._ai_training_stats_dialog.append_log_line(message)

    def _handle_ai_training_finished(self, result: object) -> None:
        context = self._ai_training_context
        self._active_ai_training_task = None
        self._ai_training_context = None
        self._close_ai_training_progress_dialog()

        if context is None:
            self._update_ai_toolbar_state()
            self.statusBar().showMessage("AI training task complete")
            return

        folder_key = normalized_path_key(context.folder)
        current_key = normalized_path_key(self._current_folder) if self._current_folder else ""
        normalized_action = context.action[9:] if context.action.startswith("pipeline_") else context.action
        pipeline_step_completed = False

        if normalized_action == "label_candidates":
            payload = result if isinstance(result, dict) else {}
            group_count = int(payload.get("multi_image_groups") or 0)
            self._update_ai_toolbar_state()
            if context.launch_labeling_after_prepare and folder_key == current_key:
                self.statusBar().showMessage(
                    f"Built {group_count} multi-image label group(s). Opening Collect Training Labels..."
                )
                self._launch_ai_labeling_app()
                return
            self.statusBar().showMessage(f"Built {group_count} multi-image label group(s)")
            return

        if normalized_action == "prepare":
            self._update_ai_toolbar_state()
            pipeline_step_completed = True
            if self._ai_training_pipeline is None:
                self.statusBar().showMessage("Training data is ready for the current folder")
                return

        if normalized_action == "train":
            payload = result if isinstance(result, dict) else {}
            checkpoint_path = str(payload.get("checkpoint_path") or "")
            training_paths = self._ai_training_paths_for_folder(context.folder)
            try:
                if checkpoint_path:
                    if training_paths is not None:
                        set_active_ranker_selection(
                            training_paths,
                            checkpoint_path=checkpoint_path,
                            run_id=str(payload.get("run_id") or context.run_id or ""),
                            display_name=str(payload.get("display_name") or context.run_label or Path(checkpoint_path).parent.name),
                        )
                    self._set_active_ai_checkpoint(checkpoint_path)
            except FileNotFoundError:
                pass
            reference_bank_path = str(payload.get("reference_bank_path") or "")
            if reference_bank_path:
                try:
                    self._set_active_reference_bank_path(reference_bank_path)
                except FileNotFoundError:
                    pass
            else:
                self._clear_active_reference_bank_path()
            self._update_ai_toolbar_state()
            pipeline_step_completed = True
            if self._ai_training_pipeline is None:
                if checkpoint_path:
                    self.statusBar().showMessage(f"Training complete. Active checkpoint updated to {Path(checkpoint_path).name}")
                else:
                    self.statusBar().showMessage("Training complete")
                return

        if normalized_action == "evaluate":
            payload = result if isinstance(result, dict) else {}
            metrics_path = str(payload.get("metrics_path") or "")
            self._update_ai_toolbar_state()
            pipeline_step_completed = True
            if metrics_path and os.path.exists(metrics_path):
                summary_text = "Evaluation complete"
                try:
                    payload_json = json.loads(Path(metrics_path).read_text(encoding="utf-8"))
                    pairwise_accuracy = payload_json.get("pairwise_evaluation", {}).get("all_preferences", {}).get("accuracy")
                    top1 = payload_json.get("cluster_evaluation", {}).get("top_k_metrics", {}).get("top_1", {}).get("hit_rate")
                    summary_parts = []
                    if pairwise_accuracy is not None:
                        summary_parts.append(f"pairwise acc {float(pairwise_accuracy):.3f}")
                    if top1 is not None:
                        summary_parts.append(f"top-1 hit {float(top1):.3f}")
                    if summary_parts:
                        summary_text = "Evaluation complete: " + ", ".join(summary_parts)
                except (OSError, ValueError, TypeError):
                    pass
                if self._ai_training_pipeline is None:
                    self.statusBar().showMessage(summary_text)
                    return
            if self._ai_training_pipeline is None:
                self.statusBar().showMessage("Evaluation complete")
                return

        if normalized_action == "score":
            payload = result if isinstance(result, dict) else {}
            report_dir = str(payload.get("report_dir") or "")
            self._update_ai_toolbar_state()
            pipeline_step_completed = True
            if report_dir and folder_key == current_key:
                self._load_ai_results(report_dir, show_message=False)
                self.mode_tabs.setCurrentIndex(1)
                if self._ai_training_pipeline is None:
                    self.statusBar().showMessage("Refreshed AI results with the trained ranker")
                    return
            if self._ai_training_pipeline is None:
                self.statusBar().showMessage("Scored the current folder with the trained ranker")
                return

        if normalized_action == "reference_bank":
            payload = result if isinstance(result, dict) else {}
            reference_bank_path = str(payload.get("reference_bank_path") or "")
            if reference_bank_path:
                try:
                    self._set_active_reference_bank_path(reference_bank_path)
                except FileNotFoundError:
                    pass
            self._update_ai_toolbar_state()
            self.statusBar().showMessage("Reference bank build complete")
            return

        if pipeline_step_completed and self._ai_training_pipeline is not None and folder_key == normalized_path_key(self._ai_training_pipeline.folder):
            self._ai_training_pipeline.step_index += 1
            if self._ai_training_pipeline.step_index >= 4:
                self._ai_training_pipeline = None
                self._update_ai_toolbar_state()
                self.statusBar().showMessage("Full AI training pipeline complete. Active ranker evaluated and scored.")
                return
            QTimer.singleShot(0, self._start_next_ai_training_pipeline_step)
            return

        self._update_ai_toolbar_state()
        self.statusBar().showMessage("AI training task complete")

    def _handle_ai_training_failed(self, message: str) -> None:
        context = self._ai_training_context
        title = context.title if context is not None else "AI Training"
        self._active_ai_training_task = None
        self._ai_training_context = None
        self._ai_training_pipeline = None
        self._close_ai_training_progress_dialog()
        self._update_ai_toolbar_state()
        QMessageBox.warning(self, title, message)
        self.statusBar().showMessage(f"{title} failed")

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

    def _show_catalog_progress_dialog(self, total_steps: int) -> QProgressDialog:
        dialog = self._show_job_progress_dialog(
            key="catalog",
            total_steps=total_steps,
            spec=JobSpec(
                title="Global Catalog",
                preparing_label="Refreshing global catalog...",
                running_label="Refreshing global catalog...",
                window_modality=Qt.WindowModality.NonModal,
                stays_on_top=True,
            ),
        )
        self._catalog_progress_dialog = dialog
        return dialog

    def _close_catalog_progress_dialog(self) -> None:
        self._close_job_progress_dialog("catalog")
        self._catalog_progress_dialog = None

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

    def _show_ai_training_progress_dialog(self, total_steps: int, *, title: str) -> AITrainingProgressDialog:
        dialog = self._ai_training_progress_dialog
        if dialog is None:
            dialog = AITrainingProgressDialog(title=title, parent=self)
            dialog.stats_requested.connect(self._open_ai_training_stats_dialog)
            self._ai_training_progress_dialog = dialog
        dialog.setWindowTitle(title)
        dialog.set_progress(0, max(1, total_steps))
        dialog.show()
        self._center_window_dialog(dialog)
        dialog.raise_()
        return dialog

    def _close_ai_training_progress_dialog(self) -> None:
        dialog = self._ai_training_progress_dialog
        if dialog is None:
            return
        dialog.hide()
        dialog.deleteLater()
        self._ai_training_progress_dialog = None

    def _open_ai_training_stats_dialog(self) -> None:
        dialog = self._ai_training_stats_dialog
        if dialog is None:
            dialog = AITrainingStatsDialog(parent=self)
            self._ai_training_stats_dialog = dialog
        dialog.set_stage_text(self._ai_training_stage_text or "Waiting for output")
        dialog.set_run_text(self._ai_training_run_label or "Not started")
        dialog.load_lines(self._ai_training_log_lines)
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _close_batch_rename_progress_dialog(self) -> None:
        self._close_job_progress_dialog("batch_rename")
        self._batch_rename_progress_dialog = None

    def _center_window_dialog(self, dialog) -> None:
        if dialog is None:
            return
        dialog.adjustSize()
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
            self._replace_records_after_moves(renamed_records_by_old_path)
            self._rekey_filter_metadata_after_moves(renamed_records_by_old_path)
            self._push_undo_actions(undo_actions)
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
        current_root = self.folder_model.rootPath()
        self.folder_model.setRootPath("")
        if current_root:
            self.folder_model.setRootPath(current_root)

    def _handle_sort_changed(self) -> None:
        selected = self._selected_sort_mode()
        if selected is None:
            return
        self._set_sort_mode(selected)

    def _set_sort_mode(self, mode: SortMode) -> None:
        self._sort_mode = mode
        self._records_view_cache.mark(ViewInvalidationReason.SORT_CHANGED)
        combo_index = self.sort_combo.findData(mode)
        if combo_index >= 0 and combo_index != self.sort_combo.currentIndex():
            self.sort_combo.setCurrentIndex(combo_index)
            return
        self._apply_records_view()
        self._scroll_active_view_to_top()
        self._remember_current_folder_view_state()
        self._update_action_states()

    def _set_ui_mode(self, mode: str) -> None:
        target_index = 1 if mode == "ai" else 0
        if self.mode_tabs.currentIndex() != target_index:
            self.mode_tabs.setCurrentIndex(target_index)
            return
        self._handle_mode_tab_changed(target_index)

    def _handle_mode_tab_changed(self, index: int) -> None:
        self._ui_mode = "ai" if index == 1 else "manual"
        self.toolbar_stack.setCurrentIndex(index)
        self._refresh_viewport_mode()
        self._update_ai_toolbar_state()
        if self._ui_mode == "ai":
            loaded_hidden = self._load_hidden_ai_results_for_current_folder(show_message=False)
            if not loaded_hidden:
                self._restore_ai_results(force=True)
        self._update_action_states()
        self._update_status()

    def _set_filter_mode(self, mode: FilterMode) -> None:
        self._filter_query.quick_filter = mode
        combo_index = self.filter_combo.findData(mode)
        if combo_index >= 0 and combo_index != self.filter_combo.currentIndex():
            self.filter_combo.setCurrentIndex(combo_index)
            return
        self._apply_filter_query_change()

    def _handle_filter_changed(self) -> None:
        selected = self._selected_filter_mode()
        if selected is None:
            return
        self._set_filter_mode(selected)

    def _handle_search_text_changed(self, text: str, *, source: str) -> None:
        self._pending_search_text = text
        other = self.ai_search_field if source == "manual" else self.manual_search_field
        if other.text() != text:
            with QSignalBlocker(other):
                other.setText(text)
        self._search_apply_timer.start()

    def _commit_search_text_filter(self) -> None:
        self._set_search_text(self._pending_search_text)

    def _set_search_text(self, text: str) -> None:
        if self._filter_query.search_text == text:
            return
        self._filter_query.search_text = text
        self._apply_filter_query_change()

    def _set_file_type_filter(self, mode: FileTypeFilter) -> None:
        if self._filter_query.file_type == mode:
            return
        self._filter_query.file_type = mode
        self._apply_filter_query_change()

    def _set_review_state_filter(self, mode: ReviewStateFilter) -> None:
        if self._filter_query.review_state == mode:
            return
        self._filter_query.review_state = mode
        self._apply_filter_query_change()

    def _set_ai_state_filter(self, mode: AIStateFilter) -> None:
        if self._filter_query.ai_state == mode:
            return
        self._filter_query.ai_state = mode
        self._apply_filter_query_change()

    def _open_advanced_filters_dialog(self) -> None:
        dialog = AdvancedFilterDialog(self._filter_query, self)
        if self._exec_dialog_with_geometry(dialog, "advanced_filters") != dialog.DialogCode.Accepted:
            return
        updated_query = dialog.updated_query()
        if updated_query == self._filter_query:
            return
        self._filter_query = updated_query
        self._apply_filter_query_change()
        self.statusBar().showMessage("Updated advanced filters")

    def _clear_record_filters(self) -> None:
        if not self._filter_query.has_active_filters:
            return
        self._filter_query = RecordFilterQuery()
        self._pending_search_text = ""
        self._sync_record_filter_controls()
        self._apply_filter_query_change()
        self.statusBar().showMessage("Cleared filters")

    def _apply_filter_query_change(self) -> None:
        current_path = self._current_visible_record_path()
        self._sync_record_filter_controls()
        self._ensure_filter_metadata_index()
        self._refresh_filter_toolbar_menu()
        if (
            self._all_records
            and self._review_intelligence is None
            and self._active_review_intelligence_task is None
            and self._filter_query.quick_filter in {FilterMode.SMART_GROUPS, FilterMode.DUPLICATES}
        ):
            self._start_review_intelligence_analysis(force=True)
        self._records_view_cache.mark(ViewInvalidationReason.FILTER_CHANGED)
        self._apply_records_view(current_path=current_path)
        if not self._records:
            return
        if current_path and current_path in self._record_index_by_path:
            return
        self._scroll_active_view_to_top()

    def _sync_record_filter_controls(self) -> None:
        search_text = self._filter_query.search_text
        for field in (self.manual_search_field, self.ai_search_field):
            if field.text() != search_text:
                with QSignalBlocker(field):
                    field.setText(search_text)

        combo_index = self.filter_combo.findData(self._filter_query.quick_filter)
        if combo_index >= 0 and combo_index != self.filter_combo.currentIndex():
            with QSignalBlocker(self.filter_combo):
                self.filter_combo.setCurrentIndex(combo_index)

        for mode, action in self._file_type_actions.items():
            with QSignalBlocker(action):
                action.setChecked(self._filter_query.file_type == mode)
        for mode, action in self._review_state_actions.items():
            with QSignalBlocker(action):
                action.setChecked(self._filter_query.review_state == mode)
        for mode, action in self._ai_state_actions.items():
            with QSignalBlocker(action):
                action.setChecked(self._filter_query.ai_state == mode)

    def _current_visible_record_path(self) -> str | None:
        current_record = self._record_at(self.grid.current_index())
        if current_record is None:
            return None
        return current_record.path

    def _ensure_filter_metadata_index(self) -> None:
        if not self._all_records:
            return
        if self._filter_metadata_record_paths:
            return
        self._reset_filter_metadata_index(self._all_records)

    @staticmethod
    def _normalize_column_count(value: object, *, default: int = 3) -> int:
        try:
            columns = int(value)
        except (TypeError, ValueError):
            columns = default
        return max(1, min(8, columns))

    def _set_column_count(self, count: int) -> None:
        columns = self._normalize_column_count(count)
        combo_index = self.columns_combo.findData(columns)
        if combo_index >= 0 and combo_index != self.columns_combo.currentIndex():
            self.columns_combo.setCurrentIndex(combo_index)
            return
        self.grid.set_column_count(columns)
        self._settings.setValue(self.VIEW_COLUMNS_KEY, columns)
        self._remember_current_folder_view_state()
        self._update_action_states()

    def _scroll_active_view_to_top(self) -> None:
        self.grid.verticalScrollBar().setValue(0)

    def _set_annotation_views(self, changed_paths: list[str] | tuple[str, ...] | set[str] | None = None) -> None:
        if changed_paths:
            if getattr(self.grid, "_annotations", None) is not self._annotations:
                self.grid.set_annotations(self._annotations)
            self.grid.update_annotations(changed_paths)
            if self.preview.isVisible():
                for path in changed_paths:
                    annotation = self._annotations.get(path, SessionAnnotation())
                    self.preview.set_annotation_state(path, annotation.winner, annotation.reject)
            return
        self.grid.set_annotations(self._annotations)

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

    def _selected_filter_mode(self) -> FilterMode | None:
        selected = self.filter_combo.currentData()
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
        text = self.filter_combo.currentText()
        for mode in FilterMode:
            if text == mode.value:
                return mode
        return None

    def _update_action_states(self) -> None:
        if self.actions is None:
            return

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
        current_ai = self._ai_result_for_record(current_record, preferred_path=display_path) if current_record is not None else None
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
        with QSignalBlocker(self.actions.compact_cards):
            self.actions.compact_cards.setChecked(self._compact_cards_enabled)
        with QSignalBlocker(self.actions.mode_actions["manual"]):
            self.actions.mode_actions["manual"].setChecked(self._ui_mode == "manual")
        with QSignalBlocker(self.actions.mode_actions["ai"]):
            self.actions.mode_actions["ai"].setChecked(self._ui_mode == "ai")

        for mode, action in self.actions.appearance_actions.items():
            with QSignalBlocker(action):
                action.setChecked(self._appearance_mode == mode)
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
        self.actions.compact_cards.setEnabled(True)
        self.actions.rename_selection.setEnabled(current_record is not None and has_physical_folder and not in_recycle_folder and not in_winners_folder)
        self.actions.batch_rename_selection.setEnabled(bool(self._current_folder and self._all_records) and not in_recycle_folder and not in_winners_folder)
        has_resizeable_records = self._records_have_resizable
        self.actions.batch_resize_selection.setEnabled(bool(self._current_folder and has_resizeable_records) and not in_recycle_folder)
        has_convertible_records = self._records_have_convertible
        self.actions.batch_convert_selection.setEnabled(bool(self._current_folder and has_convertible_records) and not in_recycle_folder)
        self.actions.extract_archive.setEnabled(bool(self._current_folder))
        training_paths = self._ai_training_paths_for_folder()
        ai_model_ready = self._ai_model_available()
        training_busy = (
            self._active_ai_training_task is not None
            or self._active_ai_task is not None
            or self._active_ai_model_task is not None
        )
        training_allowed = (
            bool(self._current_folder)
            and not in_recycle_folder
            and not in_winners_folder
            and not training_busy
            and ai_model_ready
        )
        has_training_artifacts = bool(training_paths and ai_training_artifacts_ready(training_paths))
        pairwise_labels, cluster_labels = self._cached_training_label_counts(training_paths)
        has_labels = pairwise_labels > 0 or cluster_labels > 0
        trained_checkpoint = self._current_trained_checkpoint_path()
        self.actions.download_ai_model.setEnabled(self._active_ai_model_task is None)
        self.actions.run_full_ai_training_pipeline.setEnabled(training_allowed and has_labels)
        self.actions.prepare_ai_training_data.setEnabled(training_allowed)
        self.actions.open_ai_data_selection.setEnabled(training_allowed)
        self.actions.manage_ai_rankers.setEnabled(training_allowed)
        self.actions.train_ai_ranker.setEnabled(training_allowed and has_training_artifacts)
        self.actions.evaluate_ai_ranker.setEnabled(training_allowed and has_training_artifacts and has_labels and trained_checkpoint is not None)
        self.actions.score_ai_with_trained_ranker.setEnabled(training_allowed and trained_checkpoint is not None)
        self.actions.build_ai_reference_bank.setEnabled(ai_model_ready and not training_busy)
        self.actions.clear_ai_trained_model.setEnabled(ai_model_ready and not training_busy and trained_checkpoint is not None)
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
        self.actions.taste_calibration_wizard.setEnabled(bool(self._current_folder and len(self._all_records) >= 2))
        self.actions.assign_review_round_first_pass.setEnabled(has_selection)
        self.actions.assign_review_round_second_pass.setEnabled(has_selection)
        self.actions.assign_review_round_third_pass.setEnabled(has_selection)
        self.actions.assign_review_round_hero.setEnabled(has_selection)
        self.actions.clear_review_round.setEnabled(has_selection and bool(current_workflow and current_workflow.has_round))
        self.actions.create_virtual_collection.setEnabled(current_record is not None)
        self.actions.add_selection_to_collection.setEnabled(current_record is not None and bool(collections))
        self.actions.remove_selection_from_collection.setEnabled(current_record is not None and bool(collections))
        self.actions.delete_virtual_collection.setEnabled(bool(collections))
        self.actions.browse_catalog.setEnabled(bool(catalog_roots))
        self.actions.add_current_folder_to_catalog.setEnabled(has_physical_folder)
        self.actions.add_folder_to_catalog.setEnabled(True)
        self.actions.remove_catalog_folder.setEnabled(bool(catalog_roots))
        self.actions.refresh_catalog.setEnabled(bool(catalog_roots) and self._active_catalog_task is None)
        self.actions.rebuild_folder_catalog_cache.setEnabled(has_physical_folder and not self._scan_in_progress)
        self.actions.handoff_builder.setEnabled(has_selection and has_physical_folder and not in_recycle_folder)
        self.actions.send_to_editor_pipeline.setEnabled(has_selection and has_physical_folder and not in_recycle_folder and not in_winners_folder)
        self.actions.best_of_set_auto_assembly.setEnabled(bool(self._records) and (self._ai_bundle is not None or self._review_intelligence is not None))
        self.actions.keyboard_shortcuts.setEnabled(True)
        self.actions.save_workspace_preset.setEnabled(self.workspace_docks is not None)
        self.actions.customize_workspace_toolbar.setEnabled(not self._toolbar_edit_mode)
        self.actions.new_folder.setEnabled(bool(self._current_folder))
        self.actions.save_filter_preset.setEnabled(self._filter_query.has_active_filters)
        self.actions.delete_filter_preset.setEnabled(self._matching_saved_filter_preset() is not None)
        self.actions.clear_filters.setEnabled(self._filter_query.has_active_filters)
        self._refresh_tool_mode_ui()
        if self._toolbar_edit_mode:
            self._set_workspace_toolbar_controls_enabled(False)

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
        self._set_ui_mode("ai")
        self._filter_query.quick_filter = FilterMode.AI_DISAGREEMENTS
        self._apply_filter_query_change()
        self.statusBar().showMessage("Showing AI disagreement cases for targeted review.")

    def _open_taste_calibration_wizard(self) -> None:
        if not self._current_folder or len(self._all_records) < 2:
            self.statusBar().showMessage("Load at least two images before running taste calibration.")
            return
        pairs = build_calibration_pairs(
            self._all_records,
            ai_bundle=self._ai_bundle,
            review_bundle=self._review_intelligence,
            burst_recommendations=self._burst_recommendations,
            limit=8,
        )
        if not pairs:
            self.statusBar().showMessage("Not enough useful comparisons are available for calibration in this folder yet.")
            return
        dialog = TasteCalibrationDialog(pairs, self)
        if self._exec_dialog_with_geometry(dialog, "taste_calibration") != dialog.DialogCode.Accepted:
            self.statusBar().showMessage("Taste calibration cancelled.")
            return
        current_path = self._current_visible_record_path()
        recorded = 0
        for response in dialog.responses():
            if response.choice not in {"left", "right"}:
                continue
            self._record_pairwise_preference(
                left_path=response.pair.left_path,
                right_path=response.pair.right_path,
                preferred_path=response.preferred_path,
                source_mode=response.pair.source_mode,
                group_id=response.pair.group_id,
                extra_payload={
                    "prompt": response.pair.prompt,
                    "group_label": response.pair.group_label,
                    "left_label": response.pair.left_label,
                    "right_label": response.pair.right_label,
                },
            )
            recorded += 1
        if recorded:
            self._apply_records_view(current_path=current_path)
            self.statusBar().showMessage(f"Saved {recorded} calibration preference(s) for this folder.")
            return
        self.statusBar().showMessage("Calibration finished with no recorded picks.")

    def _assign_review_round_to_selection(self, review_round: str) -> None:
        records = self._selected_records_for_actions()
        if not records:
            return
        normalized_round = normalize_review_round(review_round)
        current_path = self._current_visible_record_path() or records[0].path
        changed_paths: list[str] = []
        undo_actions: list[UndoAction] = []
        for record in records:
            annotation = self._annotations.setdefault(record.path, SessionAnnotation())
            previous_annotation = self._annotation_snapshot(annotation)
            if normalize_review_round(annotation.review_round) == normalized_round:
                continue
            undo_actions.append(
                UndoAction(
                    kind="annotation",
                    primary_path=record.path,
                    original_winner=annotation.winner,
                    original_reject=annotation.reject,
                    original_photoshop=annotation.photoshop,
                    rating=annotation.rating,
                    tags=annotation.tags,
                    original_review_round=annotation.review_round,
                    folder=self._current_folder,
                    source_paths=self._record_paths(record),
                    session_id=self._session_id,
                    winner_mode=self._winner_mode.value,
                )
            )
            annotation.review_round = normalized_round
            self._queue_annotation_persist(record, previous_annotation=previous_annotation)
            self._capture_annotation_feedback(record, previous_annotation, annotation, source_mode="review_round")
            changed_paths.append(record.path)
        if not changed_paths:
            return
        self._push_undo_actions(undo_actions)
        self._apply_annotation_change_effects(changed_paths, current_path=current_path)
        label = review_round_label(normalized_round)
        if label:
            self.statusBar().showMessage(f"Assigned {label} to {len(changed_paths)} image(s).")
        else:
            self.statusBar().showMessage(f"Cleared review round on {len(changed_paths)} image(s).")

    def _selected_records_for_workflow(self) -> list[ImageRecord]:
        records = self._selected_records_for_actions()
        if records:
            return records
        current_index = self.grid.current_index()
        record = self._record_at(current_index)
        return [record] if record is not None else []

    def _selected_record_paths_for_library(self) -> tuple[str, ...]:
        return tuple(record.path for record in self._selected_records_for_workflow())

    def _choose_virtual_collection(
        self,
        *,
        title: str,
        prompt: str,
    ) -> VirtualCollection | None:
        collections = self._library_store.list_collections()
        if not collections:
            self.statusBar().showMessage("Create a virtual collection first.")
            return None
        labels: list[str] = []
        label_to_id: dict[str, str] = {}
        for collection in collections:
            label = f"{collection.name} ({collection.item_count})"
            if label in label_to_id:
                label = f"{label} [{collection.id}]"
            labels.append(label)
            label_to_id[label] = collection.id
        default_label = labels[0]
        if self._scope_kind == "collection" and self._scope_id:
            current_collection = self._library_store.load_collection(self._scope_id)
            if current_collection is not None:
                for label, collection_id in label_to_id.items():
                    if collection_id == current_collection.id:
                        default_label = label
                        break
        choice, accepted = QInputDialog.getItem(self, title, prompt, labels, labels.index(default_label), False)
        if not accepted or not choice:
            return None
        return self._library_store.load_collection(label_to_id[str(choice)])

    def _resolve_records_for_paths(self, paths: tuple[str, ...] | list[str]) -> tuple[list[ImageRecord], int]:
        ordered_paths: list[str] = []
        seen: set[str] = set()
        for path in paths:
            normalized = normalize_filesystem_path(path)
            key = normalized_path_key(normalized)
            if not normalized or key in seen:
                continue
            seen.add(key)
            ordered_paths.append(normalized)

        if not ordered_paths:
            return [], 0

        catalog_records = self._library_store.load_catalog_records_for_paths(ordered_paths)
        folder_record_maps: dict[str, dict[str, ImageRecord]] = {}
        for path in ordered_paths:
            folder = normalize_filesystem_path(str(Path(path).parent))
            folder_key = normalized_path_key(folder)
            if folder_key in folder_record_maps:
                continue
            records, _source = self._load_cached_folder_records(folder)
            if records is None:
                try:
                    records = scan_folder(folder)
                except Exception:
                    records = []
                else:
                    self._persist_folder_record_cache(folder, records, source="collection-resolve")
            folder_record_maps[folder_key] = {
                normalized_path_key(record.path): record
                for record in records
            }

        resolved: list[ImageRecord] = []
        missing = 0
        added: set[str] = set()
        for path in ordered_paths:
            key = normalized_path_key(path)
            folder_key = normalized_path_key(str(Path(path).parent))
            record = folder_record_maps.get(folder_key, {}).get(key) or catalog_records.get(key)
            if record is None or not os.path.exists(record.path):
                missing += 1
                continue
            record_key = normalized_path_key(record.path)
            if record_key in added:
                continue
            added.add(record_key)
            resolved.append(record)
        return resolved, missing

    def _create_virtual_collection_from_selection(self) -> None:
        paths = self._selected_record_paths_for_library()
        if not paths:
            self.statusBar().showMessage("Select one or more images before creating a collection.")
            return
        dialog = CollectionEditDialog(selection_count=len(paths), parent=self)
        if self._exec_dialog_with_geometry(dialog, "collection_edit") != dialog.DialogCode.Accepted:
            return
        result = dialog.result_data()
        existing = self._library_store.find_collection_by_name(result.name)
        if existing is not None:
            overwrite = QMessageBox.question(
                self,
                "Replace Collection?",
                f"{existing.name} already exists.\n\nReplace its items with the current selection?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if overwrite != QMessageBox.StandardButton.Yes:
                return
            self._library_store.update_collection(
                VirtualCollection(
                    id=existing.id,
                    name=result.name,
                    description=result.description,
                    kind=result.kind,
                    item_paths=existing.item_paths,
                    item_count=existing.item_count,
                    created_at=existing.created_at,
                    updated_at=existing.updated_at,
                )
            )
            collection = self._library_store.replace_collection_paths(existing.id, paths)
        else:
            collection = self._library_store.create_collection(
                name=result.name,
                description=result.description,
                kind=result.kind,
                item_paths=paths,
            )
        self._refresh_collections_menu()
        if collection is not None:
            self.statusBar().showMessage(f"Saved collection: {collection.name} ({collection.item_count} items)")

    def _open_virtual_collection(self, collection_id: str) -> None:
        collection = self._library_store.load_collection(collection_id)
        if collection is None:
            self._refresh_collections_menu()
            self.statusBar().showMessage("That collection is no longer available.")
            return
        records, missing = self._resolve_records_for_paths(collection.item_paths)
        if not records:
            self.statusBar().showMessage(f"{collection.name} has no available files to open.")
            return
        self._load_virtual_scope_records(
            records,
            scope_kind="collection",
            scope_id=collection.id,
            scope_label=f"Collection: {collection.name}",
        )
        if missing:
            self.statusBar().showMessage(f"Loaded collection {collection.name} ({len(records)} available, {missing} missing)")

    def _add_selection_to_virtual_collection(self) -> None:
        paths = self._selected_record_paths_for_library()
        if not paths:
            self.statusBar().showMessage("Select one or more images before adding them to a collection.")
            return
        collection = self._choose_virtual_collection(title="Add To Collection", prompt="Collection")
        if collection is None:
            return
        updated = self._library_store.add_paths_to_collection(collection.id, paths)
        self._refresh_collections_menu()
        if updated is not None:
            self.statusBar().showMessage(f"Added {len(paths)} item(s) to {updated.name}")

    def _remove_selection_from_virtual_collection(self) -> None:
        paths = self._selected_record_paths_for_library()
        if not paths:
            self.statusBar().showMessage("Select one or more images before removing them from a collection.")
            return
        collection = None
        if self._scope_kind == "collection" and self._scope_id:
            collection = self._library_store.load_collection(self._scope_id)
        if collection is None:
            collection = self._choose_virtual_collection(title="Remove From Collection", prompt="Collection")
        if collection is None:
            return
        updated = self._library_store.remove_paths_from_collection(collection.id, paths)
        self._refresh_collections_menu()
        if updated is None:
            return
        if self._scope_kind == "collection" and self._scope_id == updated.id:
            records, missing = self._resolve_records_for_paths(updated.item_paths)
            self._load_virtual_scope_records(
                records,
                scope_kind="collection",
                scope_id=updated.id,
                scope_label=f"Collection: {updated.name}",
            )
            if missing:
                self.statusBar().showMessage(f"Updated {updated.name} ({len(records)} available, {missing} missing)")
            return
        self.statusBar().showMessage(f"Removed selected items from {updated.name}")

    def _delete_virtual_collection(self) -> None:
        collection = None
        if self._scope_kind == "collection" and self._scope_id:
            collection = self._library_store.load_collection(self._scope_id)
        if collection is None:
            collection = self._choose_virtual_collection(title="Delete Collection", prompt="Collection")
        if collection is None:
            return
        confirmation = QMessageBox.question(
            self,
            "Delete Collection?",
            f"Delete the virtual collection \"{collection.name}\"?\n\nThis does not delete any files.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirmation != QMessageBox.StandardButton.Yes:
            return
        deleted = self._library_store.delete_collection(collection.id)
        self._refresh_collections_menu()
        if deleted and self._scope_kind == "collection" and self._scope_id == collection.id:
            last_folder = self._settings.value(self.LAST_FOLDER_KEY, "", str)
            if last_folder and os.path.isdir(last_folder):
                self._select_folder(last_folder)
            else:
                self._current_folder = ""
                self._set_scope_state(kind="folder", scope_id="", label="")
                self._apply_loaded_records([])
        if deleted:
            self.statusBar().showMessage(f"Deleted collection: {collection.name}")

    def _browse_catalog(self, _checked: bool = False, *, root_path_override: str = "") -> None:
        roots = tuple(self._library_store.list_catalog_roots())
        if not roots:
            self.statusBar().showMessage("Add one or more folders to the catalog first.")
            return
        search_text = ""
        root_path = normalize_filesystem_path(root_path_override)
        if not root_path_override:
            dialog = CatalogSearchDialog(roots, parent=self)
            if self._exec_dialog_with_geometry(dialog, "catalog_search") != dialog.DialogCode.Accepted:
                return
            result = dialog.result_data()
            search_text = result.search_text
            root_path = normalize_filesystem_path(result.root_path)
        records = self._library_store.search_catalog(search_text=search_text, root_path=root_path)
        if not records:
            self.statusBar().showMessage("No catalog matches were found.")
            return
        if root_path:
            root_label = Path(root_path).name or root_path
            scope_label = f"Catalog: {root_label}"
        else:
            scope_label = "Catalog: All Indexed Folders"
        if search_text:
            scope_label = f'{scope_label} | Search "{search_text}"'
        scope_id = f"{normalized_path_key(root_path)}|{search_text.casefold()}"
        self._load_virtual_scope_records(records, scope_kind="catalog", scope_id=scope_id, scope_label=scope_label)

    def _add_current_folder_to_catalog(self) -> None:
        if not self._current_folder:
            self.statusBar().showMessage("Open a real folder before adding it to the catalog.")
            return
        self._library_store.add_catalog_root(self._current_folder)
        self._refresh_catalog_menu()
        self._start_catalog_refresh((self._current_folder,), label="Indexing current folder for catalog...")

    def _add_folder_to_catalog_prompt(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Add Folder To Catalog", self._current_folder or QDir.homePath())
        if not folder:
            return
        self._library_store.add_catalog_root(folder)
        self._refresh_catalog_menu()
        self._start_catalog_refresh((folder,), label=f"Indexing {Path(folder).name} for catalog...")

    def _remove_catalog_root_prompt(self) -> None:
        roots = self._library_store.list_catalog_roots()
        if not roots:
            self.statusBar().showMessage("No catalog roots are configured.")
            return
        labels = [f"{Path(root.path).name or root.path} ({root.indexed_record_count})" for root in roots]
        label_to_path = {label: root.path for label, root in zip(labels, roots)}
        choice, accepted = QInputDialog.getItem(self, "Remove Catalog Root", "Catalog root", labels, 0, False)
        if not accepted or not choice:
            return
        root_path = label_to_path[str(choice)]
        confirmation = QMessageBox.question(
            self,
            "Remove Catalog Root?",
            f"Remove {root_path} from the optional catalog index?\n\nThis does not move or delete files.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirmation != QMessageBox.StandardButton.Yes:
            return
        if self._library_store.remove_catalog_root(root_path):
            self._refresh_catalog_menu()
            self.statusBar().showMessage(f"Removed catalog root: {Path(root_path).name or root_path}")

    def _refresh_catalog_index(self) -> None:
        roots = self._library_store.list_catalog_roots()
        if not roots:
            self.statusBar().showMessage("Add one or more folders to the catalog first.")
            return
        self._start_catalog_refresh(tuple(root.path for root in roots), label="Refreshing global catalog...")

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
        if result.review_round:
            self._assign_review_round_to_selection(result.review_round)
        summary = plan.summary_lines[0] if plan.summary_lines else f"Selected {len(selected_indexes)} best-of pick(s)."
        self.statusBar().showMessage(summary)

    def _open_keyboard_shortcuts_dialog(self) -> None:
        dialog = KeyboardShortcutDialog(self._shortcut_bindings(), self)
        if self._exec_dialog_with_geometry(dialog, "keyboard_shortcuts") != dialog.DialogCode.Accepted:
            return
        overrides: dict[str, str] = {}
        for binding in dialog.bindings():
            normalized = normalize_shortcut_text(binding.shortcut)
            if normalized:
                overrides[binding.id] = normalized
        self._shortcut_overrides = overrides
        self._save_shortcut_overrides()
        self._apply_shortcut_overrides()
        self.statusBar().showMessage("Updated keyboard shortcuts")

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
            ui_mode=self._ui_mode,
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
        self._set_ui_mode(preset.ui_mode)
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
            self._rename_record_prompt(current_index)

    def _resize_selected_record(self) -> None:
        current_index = self.grid.current_index()
        if current_index >= 0:
            self._resize_record_prompt(current_index)

    def _record_supports_resize(self, record: ImageRecord | None) -> bool:
        if record is None:
            return False
        suffix = suffix_for_path(record.path)
        return suffix not in RAW_SUFFIXES and suffix not in FITS_SUFFIXES and suffix not in MODEL_SUFFIXES

    def _record_supports_convert(self, record: ImageRecord | None) -> bool:
        if record is None:
            return False
        suffix = suffix_for_path(record.path)
        return suffix not in RAW_SUFFIXES and suffix not in FITS_SUFFIXES and suffix not in MODEL_SUFFIXES

    def _refresh_record_capability_cache(self, records: list[ImageRecord] | None = None) -> None:
        source_records = self._all_records if records is None else records
        self._records_have_resizable = any(self._record_supports_resize(record) for record in source_records)
        self._records_have_convertible = any(self._record_supports_convert(record) for record in source_records)

    def _path_state_cache_token(self, path: Path) -> tuple[str, int, int]:
        try:
            stat_result = path.stat()
        except OSError:
            return str(path), -1, -1
        return str(path), int(stat_result.st_size), int(stat_result.st_mtime_ns)

    def _cached_training_label_counts(self, training_paths) -> tuple[int, int]:
        if training_paths is None:
            return 0, 0
        cache_key = (
            self._path_state_cache_token(training_paths.pairwise_labels_path),
            self._path_state_cache_token(training_paths.cluster_labels_path),
        )
        if cache_key != self._training_label_counts_cache_key:
            self._training_label_counts_cache = count_label_records(training_paths)
            self._training_label_counts_cache_key = cache_key
        return self._training_label_counts_cache

    def _invalidate_training_label_counts_cache(self) -> None:
        self._training_label_counts_cache_key = ()
        self._training_label_counts_cache = (0, 0)

    def _start_batch_rename_tool_mode(self) -> None:
        if not self._current_folder or not self._all_records or self._is_recycle_folder() or self._is_winners_folder():
            return
        if self._active_tool_mode == "batch_rename" and self.grid.tool_checkbox_mode():
            self.statusBar().showMessage("Batch Rename tool is already active.")
            return
        if self._active_tool_mode and self._active_tool_mode != "batch_rename":
            self._cancel_tool_mode(show_message=False)
        self._active_tool_mode = "batch_rename"
        self.grid.set_tool_checkbox_mode(True, clear_selection=True)
        self._refresh_tool_mode_ui()
        self.statusBar().showMessage("Batch Rename tool active. Use the top-left checkboxes to choose images, then click Run.")

    def _start_batch_resize_tool_mode(self) -> None:
        if not self._current_folder or not self._all_records or self._is_recycle_folder():
            return
        if not any(self._record_supports_resize(record) for record in self._all_records):
            return
        if self._active_tool_mode == "batch_resize" and self.grid.tool_checkbox_mode():
            self.statusBar().showMessage("Batch Resize tool is already active.")
            return
        if self._active_tool_mode and self._active_tool_mode != "batch_resize":
            self._cancel_tool_mode(show_message=False)
        self._active_tool_mode = "batch_resize"
        self.grid.set_tool_checkbox_mode(True, clear_selection=True)
        self._refresh_tool_mode_ui()
        self.statusBar().showMessage("Batch Resize tool active. Use the top-left checkboxes to choose images, then click Run.")

    def _start_batch_convert_tool_mode(self) -> None:
        if not self._current_folder or not self._all_records or self._is_recycle_folder():
            return
        if not any(self._record_supports_convert(record) for record in self._all_records):
            return
        if self._active_tool_mode == "batch_convert" and self.grid.tool_checkbox_mode():
            self.statusBar().showMessage("Batch Convert tool is already active.")
            return
        if self._active_tool_mode and self._active_tool_mode != "batch_convert":
            self._cancel_tool_mode(show_message=False)
        self._active_tool_mode = "batch_convert"
        self.grid.set_tool_checkbox_mode(True, clear_selection=True)
        self._refresh_tool_mode_ui()
        self.statusBar().showMessage("Batch Convert tool active. Use the top-left checkboxes to choose images, then click Run.")

    def _run_active_tool_mode(self) -> None:
        if self._active_tool_mode == "batch_rename":
            records = self._selected_records_for_tool_mode()
            if not records:
                return
            scope_label = f"Tool selection: {len(records)} image bundle(s)"
            applied = self._open_batch_rename_dialog(
                records,
                title="Batch Rename Selection",
                scope_label=scope_label,
                folder=self._current_folder,
            )
            if applied and self._active_tool_mode:
                self._cancel_tool_mode(show_message=False)
            return
        if self._active_tool_mode == "batch_resize":
            selected_records = self._selected_records_for_tool_mode()
            sources = self._selected_resize_sources_for_tool_mode()
            if not sources:
                self.statusBar().showMessage("Batch Resize skips RAW files. Select one or more non-RAW images.")
                return
            skipped_raw_count = max(0, len(selected_records) - len(sources))
            scope_label = f"Tool selection: {len(sources)} image(s)"
            raw_note = "Resize can't be used on RAW files."
            if skipped_raw_count:
                scope_label = (
                    f"{scope_label}\n"
                    f"{skipped_raw_count} RAW file(s) were skipped because resize can't be used on RAW files."
                )
            applied = self._open_resize_dialog(
                sources,
                title="Batch Resize Selection",
                scope_label=scope_label,
                show_preview=True,
                raw_note=raw_note,
            )
            if applied and self._active_tool_mode:
                self._cancel_tool_mode(show_message=False)
            return
        if self._active_tool_mode != "batch_convert":
            return
        selected_records = self._selected_records_for_tool_mode()
        sources = self._selected_convert_sources_for_tool_mode()
        if not sources:
            self.statusBar().showMessage("Batch Convert skips RAW files. Select one or more non-RAW images.")
            return
        skipped_raw_count = max(0, len(selected_records) - len(sources))
        scope_label = f"Tool selection: {len(sources)} image(s)"
        raw_note = "Convert can't be used on RAW files."
        if skipped_raw_count:
            scope_label = (
                f"{scope_label}\n"
                f"{skipped_raw_count} RAW file(s) were skipped because convert can't be used on RAW files."
            )
        applied = self._open_convert_dialog(
            sources,
            title="Batch Convert Selection",
            scope_label=scope_label,
            show_preview=True,
            raw_note=raw_note,
        )
        if applied and self._active_tool_mode:
            self._cancel_tool_mode(show_message=False)

    def _cancel_tool_mode(self, checked: bool = False, *, show_message: bool = True) -> None:
        del checked
        if not self._active_tool_mode and not self.grid.tool_checkbox_mode():
            return
        self._active_tool_mode = ""
        self.grid.set_tool_checkbox_mode(False, clear_selection=True)
        self._refresh_tool_mode_ui()
        if show_message:
            self.statusBar().showMessage("Exited tool selection mode")

    def _refresh_tool_mode_ui(self) -> None:
        active = bool(self._active_tool_mode)
        self.tool_mode_bar.setVisible(active)
        if not active:
            return
        selected_count = len(self._selected_records_for_tool_mode())
        if self._active_tool_mode == "batch_rename":
            self.tool_mode_title.setText("Batch Rename")
            self.tool_mode_help.setText("Select images with the checkboxes, then run the rename tool.")
            self.tool_mode_run_button.setText("Run Batch Rename")
            self.tool_mode_selection.setText(f"{selected_count} selected")
            self.tool_mode_run_button.setEnabled(selected_count > 0)
        elif self._active_tool_mode == "batch_resize":
            eligible_count = len(self._selected_resize_sources_for_tool_mode())
            skipped_raw_count = max(0, selected_count - eligible_count)
            self.tool_mode_title.setText("Batch Resize")
            self.tool_mode_help.setText("Select images with the checkboxes, then run the resize tool. RAW files are skipped.")
            self.tool_mode_run_button.setText("Run Batch Resize")
            if skipped_raw_count:
                self.tool_mode_selection.setText(f"{eligible_count} eligible | {skipped_raw_count} RAW skipped")
            else:
                self.tool_mode_selection.setText(f"{eligible_count} eligible")
            self.tool_mode_run_button.setEnabled(eligible_count > 0)
        elif self._active_tool_mode == "batch_convert":
            eligible_count = len(self._selected_convert_sources_for_tool_mode())
            skipped_raw_count = max(0, selected_count - eligible_count)
            self.tool_mode_title.setText("Batch Convert")
            self.tool_mode_help.setText("Select images with the checkboxes, then run the convert tool. RAW files are skipped.")
            self.tool_mode_run_button.setText("Run Batch Convert")
            if skipped_raw_count:
                self.tool_mode_selection.setText(f"{eligible_count} eligible | {skipped_raw_count} RAW skipped")
            else:
                self.tool_mode_selection.setText(f"{eligible_count} eligible")
            self.tool_mode_run_button.setEnabled(eligible_count > 0)
        else:
            self.tool_mode_title.setText("Tool")
            self.tool_mode_help.setText("Select images, then run the active tool.")
            self.tool_mode_run_button.setText("Run")
            self.tool_mode_selection.setText(f"{selected_count} selected")
            self.tool_mode_run_button.setEnabled(selected_count > 0)

    def _selected_records_for_tool_mode(self) -> list[ImageRecord]:
        return [
            self._records[index]
            for index in self.grid.selected_indexes()
            if 0 <= index < len(self._records)
        ]

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

    def _selected_resize_sources_for_tool_mode(self) -> list[ResizeSourceItem]:
        return [
            source
            for index in self.grid.selected_indexes()
            if 0 <= index < len(self._records)
            for source in [self._resize_source_for_index(index)]
            if source is not None
        ]

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

    def _selected_convert_sources_for_tool_mode(self) -> list[ConvertSourceItem]:
        return [
            source
            for index in self.grid.selected_indexes()
            if 0 <= index < len(self._records)
            for source in [self._convert_source_for_index(index)]
            if source is not None
        ]

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
        root = normalize_filesystem_path(destination_root or self._current_folder or "")
        if not root:
            return ""
        if recipe.destination_subfolder:
            return normalize_filesystem_path(str(Path(root) / recipe.destination_subfolder))
        return root

    def _workflow_archive_path(self, recipe: WorkflowRecipe, destination_root: str | None = None) -> str:
        root = normalize_filesystem_path(destination_root or self._current_folder or "")
        if not root:
            return ""
        archive_format = archive_format_for_key(recipe.archive_format)
        base_name = recipe.destination_subfolder.strip() or recipe.name or "handoff"
        return str(Path(root) / f"{base_name}{archive_format.suffix}")

    def _workflow_record_folder_name(self, record: ImageRecord) -> str:
        return Path(record.name).stem or "record"

    def _run_workflow_recipe(
        self,
        recipe: WorkflowRecipe,
        *,
        destination_root: str | None = None,
        records: list[ImageRecord] | None = None,
    ) -> None:
        selected_records = records if records is not None else self._selected_records_for_workflow()
        if not selected_records:
            self.statusBar().showMessage("Select one or more images before running a workflow recipe.")
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
                    QMessageBox.warning(self, "Workflow Recipe", plan.general_error)
                else:
                    self.statusBar().showMessage("The workflow export plan could not be built.")
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
            self.statusBar().showMessage("Choose a destination folder for this workflow recipe.")
            return

        destructive = recipe.transfer_mode == RECIPE_TRANSFER_MOVE
        if destructive:
            confirmation = QMessageBox.question(
                self,
                "Run Workflow Recipe?",
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
                if self._move_record_to_path(record.path, target_dir):
                    processed += 1
            else:
                if self._copy_record_to_path(record.path, target_dir):
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

    def _start_ai_training_task(
        self,
        task: object,
        *,
        action: str,
        title: str,
        folder: str | None = None,
        launch_labeling_after_prepare: bool = False,
        reference_bank_path: str = "",
        run_id: str = "",
        run_label: str = "",
        log_path: str = "",
    ) -> bool:
        if self._active_ai_training_task is not None or self._active_ai_task is not None:
            self.statusBar().showMessage("An AI task is already running.")
            return False

        target_folder = folder or self._current_folder
        if not target_folder:
            self.statusBar().showMessage("Choose a folder first.")
            return False

        signals = getattr(task, "signals", None)
        if signals is None:
            return False

        signals.started.connect(self._handle_ai_training_started, Qt.ConnectionType.QueuedConnection)
        if hasattr(signals, "stage"):
            signals.stage.connect(self._handle_ai_training_stage, Qt.ConnectionType.QueuedConnection)
        signals.progress.connect(self._handle_ai_training_progress, Qt.ConnectionType.QueuedConnection)
        if hasattr(signals, "log"):
            signals.log.connect(self._handle_ai_training_log, Qt.ConnectionType.QueuedConnection)
        signals.finished.connect(self._handle_ai_training_finished, Qt.ConnectionType.QueuedConnection)
        signals.failed.connect(self._handle_ai_training_failed, Qt.ConnectionType.QueuedConnection)

        self._ai_training_log_lines = []
        self._ai_training_stage_text = "Preparing AI training task..."
        self._ai_training_run_label = run_label.strip()
        if self._ai_training_stats_dialog is not None:
            self._ai_training_stats_dialog.set_stage_text(self._ai_training_stage_text)
            self._ai_training_stats_dialog.set_run_text(self._ai_training_run_label or "Not started")
            self._ai_training_stats_dialog.clear_log()

        self._active_ai_training_task = task
        self._ai_training_context = AITrainingExecutionContext(
            action=action,
            folder=target_folder,
            title=title,
            launch_labeling_after_prepare=launch_labeling_after_prepare,
            reference_bank_path=reference_bank_path,
            run_id=run_id.strip(),
            run_label=run_label.strip(),
            log_path=log_path.strip(),
        )
        dialog = self._show_ai_training_progress_dialog(1, title=title)
        dialog.set_status_text("Preparing AI training task...")
        dialog.set_stats_button_enabled(True)
        QApplication.processEvents()
        self._update_ai_toolbar_state()
        self._ai_training_pool.start(task)
        return True

    def _prepare_ai_training_data(
        self,
        _checked: bool = False,
        *,
        launch_labeling_after_prepare: bool = False,
        dialog_title: str | None = None,
        status_message: str | None = None,
    ) -> None:
        if not self._current_folder:
            self.statusBar().showMessage("Choose a folder before preparing training data.")
            return
        if not self._ensure_ai_model_available(title="Prepare Training Data"):
            return
        task = PrepareTrainingDataTask(folder=Path(self._current_folder), runtime=self._ai_runtime)
        resolved_title = dialog_title or "Prepare Training Data"
        started = self._start_ai_training_task(
            task,
            action="prepare",
            title=resolved_title,
            launch_labeling_after_prepare=launch_labeling_after_prepare,
        )
        if started:
            self.statusBar().showMessage(
                status_message
                or (
                    "Preparing model data for the current folder..."
                )
            )

    def _launch_ai_labeling_app(self, *, folder: str | None = None) -> None:
        target_folder = folder or self._current_folder
        if not target_folder:
            self.statusBar().showMessage("Choose a folder before collecting training labels.")
            return
        try:
            training_paths = self._ai_training_paths_for_folder(target_folder)
            artifacts_dir = training_paths.labeling_artifacts_dir if training_paths is not None else None
            process = launch_labeling_app(
                self._ai_runtime,
                folder=target_folder,
                annotator_id=self._session_id,
                artifacts_dir=artifacts_dir,
                appearance_mode=self._current_child_appearance_mode(),
                parent_pid=os.getpid(),
                sync_file_path=self._child_sync_state_path,
            )
            self._register_child_process(process, name="AI Label Collection")
        except Exception as exc:
            QMessageBox.warning(
                self,
                "Collect Training Labels",
                f"Could not open the labeling app.\n\n{exc}",
            )
            return
        self.statusBar().showMessage("Opened training label collection for the current folder.")

    def _open_ai_data_selection(self) -> None:
        if not self._current_folder:
            self.statusBar().showMessage("Choose a folder before collecting training labels.")
            return
        if not self._ensure_ai_model_available(title="Collect Training Labels"):
            return
        training_paths = self._ai_training_paths_for_folder(self._current_folder)
        if training_paths is not None and labeling_artifacts_ready(training_paths):
            self._launch_ai_labeling_app()
            return
        if not self._all_records:
            QMessageBox.information(
                self,
                "Collect Training Labels",
                "No images are loaded for the current folder yet.",
            )
            return
        task = PrepareLabelingCandidatesTask(
            folder=Path(self._current_folder),
            records=tuple(self._all_records),
        )
        started = self._start_ai_training_task(
            task,
            action="label_candidates",
            title="Collect Training Labels",
            launch_labeling_after_prepare=True,
        )
        if started:
            self.statusBar().showMessage("Opening label collection and building local candidate groups...")

    def _train_ai_ranker(self) -> None:
        if not self._current_folder:
            self.statusBar().showMessage("Choose a folder before training a ranker.")
            return
        if not self._ensure_ai_model_available(title="Train Ranker"):
            return
        training_paths = self._ai_training_paths_for_folder(self._current_folder)
        if training_paths is None or not ai_training_artifacts_ready(training_paths):
            QMessageBox.information(
                self,
                "Train Ranker",
                "Prepare training data for this folder before training a ranker.",
            )
            return
        pairwise_count, cluster_count = count_label_records(training_paths)
        if pairwise_count <= 0 and cluster_count <= 0:
            QMessageBox.information(
                self,
                "Train Ranker",
                "No saved labels were found yet.\n\nUse Collect Training Labels first.",
            )
            return
        options = self._prompt_train_ranker_options(
            pairwise_count=pairwise_count,
            cluster_count=cluster_count,
            title="Train Ranker",
        )
        if options is None:
            return
        task = TrainRankerTask(
            folder=Path(self._current_folder),
            runtime=self._ai_runtime,
            options=options,
        )
        started = self._start_ai_training_task(
            task,
            action="train",
            title="Train Ranker",
            reference_bank_path=options.reference_bank_path,
            run_id=task.run_id,
            run_label=task.display_name,
            log_path=str(task.log_path),
        )
        if started:
            self.statusBar().showMessage("Training a new AI preference ranker...")

    def _run_full_ai_training_pipeline(self) -> None:
        if not self._current_folder:
            self.statusBar().showMessage("Choose a folder before running the full training pipeline.")
            return
        if not self._ensure_ai_model_available(title="Run Full Training Pipeline"):
            return
        training_paths = self._ai_training_paths_for_folder(self._current_folder)
        if training_paths is None:
            self.statusBar().showMessage("Open a folder first.")
            return
        pairwise_count, cluster_count = count_label_records(training_paths)
        if pairwise_count <= 0 and cluster_count <= 0:
            QMessageBox.information(
                self,
                "Run Full Training Pipeline",
                "The one-click pipeline still needs your human labels first.\n\nUse Collect Training Labels before running the full pipeline.",
            )
            return
        options = self._prompt_train_ranker_options(
            pairwise_count=pairwise_count,
            cluster_count=cluster_count,
            title="Run Full Training Pipeline",
        )
        if options is None:
            return
        self._ai_training_pipeline = AITrainingPipelineState(folder=self._current_folder, options=options, step_index=0)
        self._start_next_ai_training_pipeline_step()

    def _start_next_ai_training_pipeline_step(self) -> None:
        pipeline = self._ai_training_pipeline
        if pipeline is None:
            return
        if normalized_path_key(pipeline.folder) != normalized_path_key(self._current_folder):
            self._select_folder(pipeline.folder)
        steps = ("prepare", "train", "evaluate", "score")
        if pipeline.step_index >= len(steps):
            self._ai_training_pipeline = None
            return
        step = steps[pipeline.step_index]
        if step == "prepare":
            task = PrepareTrainingDataTask(folder=Path(pipeline.folder), runtime=self._ai_runtime)
            started = self._start_ai_training_task(
                task,
                action="pipeline_prepare",
                title="AI Training Pipeline",
                folder=pipeline.folder,
            )
            if started:
                self.statusBar().showMessage("Pipeline 1/4: preparing training data...")
            return
        if step == "train":
            task = TrainRankerTask(folder=Path(pipeline.folder), runtime=self._ai_runtime, options=pipeline.options)
            started = self._start_ai_training_task(
                task,
                action="pipeline_train",
                title="AI Training Pipeline",
                folder=pipeline.folder,
                reference_bank_path=pipeline.options.reference_bank_path,
                run_id=task.run_id,
                run_label=task.display_name,
                log_path=str(task.log_path),
            )
            if started:
                self.statusBar().showMessage("Pipeline 2/4: training the new ranker...")
            return
        if step == "evaluate":
            checkpoint_path = self._current_trained_checkpoint_path()
            if checkpoint_path is None:
                self._ai_training_pipeline = None
                QMessageBox.warning(self, "AI Training Pipeline", "Training finished, but no checkpoint is available for evaluation.")
                return
            task = EvaluateRankerTask(
                folder=Path(pipeline.folder),
                runtime=self._ai_runtime,
                checkpoint_path=checkpoint_path,
                reference_bank_path=pipeline.options.reference_bank_path,
            )
            started = self._start_ai_training_task(
                task,
                action="pipeline_evaluate",
                title="AI Training Pipeline",
                folder=pipeline.folder,
                reference_bank_path=pipeline.options.reference_bank_path,
            )
            if started:
                self.statusBar().showMessage("Pipeline 3/4: evaluating the active ranker...")
            return
        checkpoint_path = self._current_trained_checkpoint_path()
        if checkpoint_path is None:
            self._ai_training_pipeline = None
            QMessageBox.warning(self, "AI Training Pipeline", "No active checkpoint is available for scoring.")
            return
        task = ScoreCurrentFolderTask(
            folder=Path(pipeline.folder),
            runtime=self._ai_runtime,
            checkpoint_path=checkpoint_path,
            reference_bank_path=pipeline.options.reference_bank_path,
        )
        started = self._start_ai_training_task(
            task,
            action="pipeline_score",
            title="AI Training Pipeline",
            folder=pipeline.folder,
            reference_bank_path=pipeline.options.reference_bank_path,
        )
        if started:
            self.statusBar().showMessage("Pipeline 4/4: scoring the current folder with the active ranker...")

    def _prompt_train_ranker_options(
        self,
        *,
        pairwise_count: int,
        cluster_count: int,
        title: str,
    ) -> RankerTrainingOptions | None:
        dialog = TrainRankerDialog(
            pairwise_count=pairwise_count,
            cluster_count=cluster_count,
            active_reference_bank_path=self._active_reference_bank_path,
            parent=self,
        )
        dialog.setWindowTitle(title)
        if self._exec_dialog_with_geometry(dialog, "train_ranker") != dialog.DialogCode.Accepted:
            return None
        return dialog.accepted_options()

    def _evaluate_ai_ranker(self) -> None:
        if not self._current_folder:
            self.statusBar().showMessage("Choose a folder before evaluating a ranker.")
            return
        if not self._ensure_ai_model_available(title="Evaluate Ranker"):
            return
        training_paths = self._ai_training_paths_for_folder(self._current_folder)
        if training_paths is None or not ai_training_artifacts_ready(training_paths):
            QMessageBox.information(
                self,
                "Evaluate Ranker",
                "Prepare training data for this folder before evaluating a ranker.",
            )
            return
        pairwise_count, cluster_count = count_label_records(training_paths)
        if pairwise_count <= 0 and cluster_count <= 0:
            QMessageBox.information(
                self,
                "Evaluate Ranker",
                "No saved labels were found yet.\n\nUse Collect Training Labels first.",
            )
            return
        checkpoint_path = self._current_trained_checkpoint_path()
        if checkpoint_path is None:
            QMessageBox.information(
                self,
                "Evaluate Ranker",
                "No trained checkpoint is available yet.",
            )
            return
        reference_bank_path = self._active_reference_bank_path if self._active_reference_bank_path else ""
        task = EvaluateRankerTask(
            folder=Path(self._current_folder),
            runtime=self._ai_runtime,
            checkpoint_path=checkpoint_path,
            reference_bank_path=reference_bank_path,
        )
        started = self._start_ai_training_task(
            task,
            action="evaluate",
            title="Evaluate Trained Ranker",
            reference_bank_path=reference_bank_path,
        )
        if started:
            self.statusBar().showMessage("Evaluating the trained ranker...")

    def _score_current_folder_with_trained_ranker(self) -> None:
        if not self._current_folder:
            self.statusBar().showMessage("Choose a folder before scoring it with a trained ranker.")
            return
        if not self._ensure_ai_model_available(title="Score With Trained Ranker"):
            return
        checkpoint_path = self._current_trained_checkpoint_path()
        if checkpoint_path is None:
            QMessageBox.information(
                self,
                "Score With Trained Ranker",
                "No trained checkpoint is available yet.",
            )
            return
        try:
            self._set_active_ai_checkpoint(str(checkpoint_path))
        except FileNotFoundError:
            pass
        training_paths = self._ai_training_paths_for_folder(self._current_folder)
        if training_paths is None or not ai_training_artifacts_ready(training_paths):
            self.statusBar().showMessage("No prepared artifacts found. Running the full AI pipeline with the active trained ranker...")
            self._run_ai_pipeline()
            return
        reference_bank_path = self._active_reference_bank_path if self._active_reference_bank_path else ""
        task = ScoreCurrentFolderTask(
            folder=Path(self._current_folder),
            runtime=self._ai_runtime,
            checkpoint_path=checkpoint_path,
            reference_bank_path=reference_bank_path,
        )
        started = self._start_ai_training_task(
            task,
            action="score",
            title="Score With Trained Ranker",
            reference_bank_path=reference_bank_path,
        )
        if started:
            self.statusBar().showMessage("Scoring the current folder with the trained ranker...")

    def _build_ai_reference_bank(self) -> None:
        if not self._ensure_ai_model_available(title="Build Reference Bank"):
            return
        initial_dir = self._current_folder or QDir.homePath()
        reference_dir = QFileDialog.getExistingDirectory(
            self,
            "Choose Reference Images Folder",
            initial_dir,
        )
        if not reference_dir:
            return
        output_root = self._reference_bank_output_dir()
        output_name = Path(reference_dir).name or "reference_bank"
        output_dir = output_root / output_name
        task = BuildReferenceBankTask(
            runtime=self._ai_runtime,
            options=ReferenceBankBuildOptions(
                reference_dir=reference_dir,
                output_dir=str(output_dir),
                batch_size=8,
                device="auto",
            ),
        )
        started = self._start_ai_training_task(
            task,
            action="reference_bank",
            title="Build Reference Bank",
            folder=self._current_folder or reference_dir,
            reference_bank_path=str(output_dir / "reference_bank.npz"),
        )
        if started:
            self.statusBar().showMessage("Building a reference bank from the selected folder...")

    def _manage_ai_rankers(self) -> None:
        if not self._current_folder:
            self.statusBar().showMessage("Choose a folder before opening Ranker Center.")
            return
        if not self._ensure_ai_model_available(title="Ranker Center"):
            return
        training_paths = self._ai_training_paths_for_folder(self._current_folder)
        if training_paths is None:
            self.statusBar().showMessage("Choose a folder before opening Ranker Center.")
            return
        runs = list_ranker_runs(training_paths)
        pairwise_count, cluster_count = count_label_records(training_paths)
        active_checkpoint = self._current_trained_checkpoint_path()
        active_run = next((run for run in runs if run.is_active), None)
        active_ranker_label = active_run.display_name if active_run is not None else "Default built-in checkpoint"
        active_reference_label = (
            Path(active_run.reference_bank_path).name
            if active_run is not None and active_run.reference_bank_path
            else (Path(self._active_reference_bank_path).name if self._active_reference_bank_path else "None")
        )
        summary = RankerCenterSummary(
            folder_path=self._current_folder,
            hidden_root=str(training_paths.hidden_root),
            pairwise_labels=pairwise_count,
            cluster_labels=cluster_count,
            candidates_ready=labeling_artifacts_ready(training_paths),
            prepared_ready=ai_training_artifacts_ready(training_paths),
            active_ranker_label=active_ranker_label,
            active_reference_label=active_reference_label,
            saved_rankers=len(runs),
            has_active_checkpoint=active_checkpoint is not None,
        )
        dialog = RankerCenterDialog(summary=summary, runs=runs, parent=self)
        if self._exec_dialog_with_geometry(dialog, "ranker_center") != dialog.DialogCode.Accepted:
            return
        requested_action = dialog.requested_action
        if requested_action == "collect_labels":
            self._open_ai_data_selection()
            return
        if requested_action == "prepare_data":
            self._prepare_ai_training_data()
            return
        if requested_action == "run_full_pipeline":
            self._run_full_ai_training_pipeline()
            return
        if requested_action == "train":
            self._train_ai_ranker()
            return
        if requested_action == "evaluate":
            self._evaluate_ai_ranker()
            return
        if requested_action == "score":
            self._score_current_folder_with_trained_ranker()
            return
        if requested_action == "use_default":
            clear_active_ranker_selection(training_paths)
            self._clear_active_ai_checkpoint_override()
            self._clear_active_reference_bank_path()
            self._update_ai_toolbar_state()
            self.statusBar().showMessage("Switched back to the default built-in ranker.")
            return
        selected_run = dialog.selected_run()
        if requested_action != "use_selected" or selected_run is None or selected_run.checkpoint_path is None:
            return
        set_active_ranker_selection(
            training_paths,
            checkpoint_path=selected_run.checkpoint_path,
            run_id=selected_run.run_id,
            display_name=selected_run.display_name,
        )
        try:
            self._set_active_ai_checkpoint(str(selected_run.checkpoint_path))
        except FileNotFoundError as exc:
            QMessageBox.warning(self, "Ranker Center", f"Could not activate the selected ranker.\n\n{exc}")
            return
        if selected_run.reference_bank_path:
            try:
                self._set_active_reference_bank_path(selected_run.reference_bank_path)
            except FileNotFoundError:
                pass
        else:
            self._clear_active_reference_bank_path()
        self._update_ai_toolbar_state()
        self.statusBar().showMessage(f"Active ranker set to {selected_run.display_name}.")

    def _clear_ai_trained_model(self) -> None:
        if self._active_ai_training_task is not None or self._active_ai_task is not None:
            self.statusBar().showMessage("Wait for the current AI task to finish first.")
            return

        training_paths = self._ai_training_paths_for_folder(self._current_folder)
        current_folder_checkpoint = resolve_trained_checkpoint(training_paths) if training_paths is not None else None
        active_checkpoint = self._current_trained_checkpoint_path()

        if current_folder_checkpoint is None and active_checkpoint is None:
            self.statusBar().showMessage("No trained model is available to clear.")
            return

        message = (
            "Reset the active ranker and switch back to the default checkpoint?\n\n"
            "Saved ranker versions will stay on disk so you can switch back later from Ranker Center."
        )
        title = "Reset Active Ranker"

        confirmation = QMessageBox.question(
            self,
            title,
            message,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirmation != QMessageBox.StandardButton.Yes:
            return

        if training_paths is not None:
            clear_active_ranker_selection(training_paths)
        self._clear_active_ai_checkpoint_override()
        self._update_ai_toolbar_state()
        self.statusBar().showMessage("Reset the active model to the default checkpoint.")

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
            self._batch_move_records(records)

    def _move_selected_records_to_new_folder(self) -> None:
        records = self._selected_records_for_actions()
        if records:
            self._batch_move_records_to_new_folder(records)

    def _delete_selected_records(self) -> None:
        records = self._selected_records_for_actions()
        if records:
            self._batch_delete_records(records)

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
        self._create_folder_prompt(parent, select_created=False)

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
            if isinstance(path, str) and path and os.path.isdir(path) and path not in favorites:
                favorites.append(path)
        return favorites

    def _load_recent_folders(self) -> list[str]:
        raw = self._settings.value(self.RECENT_FOLDERS_KEY, [], list)
        if isinstance(raw, str):
            raw = [raw]
        folders: list[str] = []
        seen: set[str] = set()
        for path in raw or []:
            if not isinstance(path, str) or not path or not os.path.isdir(path):
                continue
            normalized = normalized_path_key(path)
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
            if not isinstance(path, str) or not path or not os.path.isdir(path):
                continue
            normalized = normalized_path_key(path)
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

    def _apply_saved_ai_training_preferences(self) -> None:
        checkpoint_override = self._settings.value(self.AI_CHECKPOINT_OVERRIDE_KEY, "", str)
        if isinstance(checkpoint_override, str) and checkpoint_override:
            candidate = Path(checkpoint_override).expanduser()
            if candidate.exists():
                self._ai_runtime = replace(self._ai_runtime, checkpoint_path=candidate.resolve())
            else:
                self._settings.remove(self.AI_CHECKPOINT_OVERRIDE_KEY)

        reference_bank_path = self._settings.value(self.AI_REFERENCE_BANK_KEY, "", str)
        if isinstance(reference_bank_path, str) and reference_bank_path:
            candidate = Path(reference_bank_path).expanduser()
            if candidate.exists():
                self._active_reference_bank_path = str(candidate.resolve())
            else:
                self._settings.remove(self.AI_REFERENCE_BANK_KEY)
                self._active_reference_bank_path = ""

    def _set_active_ai_checkpoint(self, checkpoint_path: str) -> None:
        candidate = Path(checkpoint_path).expanduser()
        if not candidate.exists():
            raise FileNotFoundError(f"Checkpoint not found: {candidate}")
        resolved = candidate.resolve()
        self._ai_runtime = replace(self._ai_runtime, checkpoint_path=resolved)
        if normalized_path_key(str(resolved)) == normalized_path_key(str(self._default_ai_checkpoint_path)):
            self._settings.remove(self.AI_CHECKPOINT_OVERRIDE_KEY)
        else:
            self._settings.setValue(self.AI_CHECKPOINT_OVERRIDE_KEY, str(resolved))

    def _clear_active_ai_checkpoint_override(self) -> None:
        self._ai_runtime = replace(self._ai_runtime, checkpoint_path=self._default_ai_checkpoint_path)
        self._settings.remove(self.AI_CHECKPOINT_OVERRIDE_KEY)

    def _set_active_reference_bank_path(self, reference_bank_path: str) -> None:
        candidate = Path(reference_bank_path).expanduser()
        if not candidate.exists():
            raise FileNotFoundError(f"Reference bank not found: {candidate}")
        resolved = str(candidate.resolve())
        self._active_reference_bank_path = resolved
        self._settings.setValue(self.AI_REFERENCE_BANK_KEY, resolved)

    def _clear_active_reference_bank_path(self) -> None:
        self._active_reference_bank_path = ""
        self._settings.remove(self.AI_REFERENCE_BANK_KEY)

    def _ai_training_paths_for_folder(self, folder: str | None = None):
        target_folder = folder or self._current_folder
        if not target_folder:
            return None
        try:
            return build_ai_training_paths(target_folder)
        except Exception:
            return None

    def _current_trained_checkpoint_path(self) -> Path | None:
        paths = self._ai_training_paths_for_folder()
        if paths is not None:
            checkpoint = resolve_trained_checkpoint(paths)
            if checkpoint is not None:
                return checkpoint
        current_checkpoint = self._ai_runtime.checkpoint_path
        if (
            current_checkpoint.exists()
            and normalized_path_key(str(current_checkpoint))
            != normalized_path_key(str(self._default_ai_checkpoint_path))
        ):
            return current_checkpoint
        return None

    def _reference_bank_output_dir(self) -> Path:
        app_data = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation)
        root = Path(app_data) if app_data else (Path.home() / "AppData" / "Local" / "ImageTriage")
        return root / "ai_training" / "reference_bank"

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
        return {
            "columns": self._normalize_column_count(self.columns_combo.currentData()),
            "sort": self._sort_mode.value,
            "scroll": max(0, int(self.grid.current_scroll_value())),
        }

    def _remember_current_folder_view_state(self) -> None:
        if not getattr(self, "_current_folder", ""):
            return
        key = normalized_path_key(self._current_folder)
        if not key:
            return
        self._folder_view_states[key] = self._current_folder_view_state()
        if len(self._folder_view_states) > 120:
            self._folder_view_states = dict(list(self._folder_view_states.items())[-120:])
        self._save_folder_view_states()

    def _apply_folder_view_state(self, folder: str) -> None:
        state = self._folder_view_states.get(normalized_path_key(folder))
        if not state:
            self._pending_folder_scroll_value = None
            return
        columns = self._normalize_column_count(state.get("columns"))
        combo_index = self.columns_combo.findData(columns)
        if combo_index >= 0:
            with QSignalBlocker(self.columns_combo):
                self.columns_combo.setCurrentIndex(combo_index)
        self.grid.set_column_count(columns)

        sort_mode = self._sort_mode_from_state(state.get("sort"))
        if sort_mode is not None:
            self._sort_mode = sort_mode
            combo_index = self.sort_combo.findData(sort_mode)
            if combo_index >= 0:
                with QSignalBlocker(self.sort_combo):
                    self.sort_combo.setCurrentIndex(combo_index)
        try:
            self._pending_folder_scroll_value = max(0, int(state.get("scroll", 0)))
        except (TypeError, ValueError):
            self._pending_folder_scroll_value = None

    def _restore_pending_folder_scroll(self) -> None:
        if self._pending_folder_scroll_value is None:
            return
        value = self._pending_folder_scroll_value
        self._pending_folder_scroll_value = None
        self.grid.restore_scroll_value(value)

    def _remember_recent_folder(self, folder: str) -> None:
        normalized = normalize_filesystem_path(folder)
        if not normalized or not os.path.isdir(normalized):
            return
        normalized_key = normalized_path_key(normalized)
        self._recent_folders = [
            normalized,
            *[
                item
                for item in self._recent_folders
                if normalized_path_key(item) != normalized_key
            ],
        ][:12]
        self._save_recent_folders()
        self._refresh_recent_folder_combos()

    def _recent_folder_paths(self, *, exclude_current_folder: bool = False) -> list[str]:
        valid: list[str] = []
        seen: set[str] = set()
        for path in self._recent_folders:
            if not os.path.isdir(path):
                continue
            key = normalized_path_key(path)
            if key in seen:
                continue
            seen.add(key)
            valid.append(path)
        if valid != self._recent_folders:
            self._recent_folders = valid[:12]
            self._save_recent_folders()
        if not exclude_current_folder or not self._current_folder:
            return valid
        current_key = normalized_path_key(self._current_folder)
        return [path for path in valid if normalized_path_key(path) != current_key]

    def _open_recent_folder(self, folder: str) -> None:
        if os.path.isdir(folder):
            self._select_folder(folder)
            return
        missing_key = normalized_path_key(folder)
        self._recent_folders = [
            path for path in self._recent_folders if normalized_path_key(path) != missing_key
        ]
        self._save_recent_folders()
        self._refresh_recent_folder_combos()
        self.statusBar().showMessage("Recent folder no longer exists.")

    def _refresh_recent_folder_combos(self) -> None:
        current_text = self._scope_display_label()
        current_folder = self._current_folder if self._scope_kind == "folder" and self._current_folder else ""
        for combo in (
            getattr(self, "manual_path_combo", None),
            getattr(self, "ai_path_combo", None),
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

    def _handle_path_combo_activated(self, combo: QComboBox, index: int) -> None:
        value = combo.itemData(index)
        if value == "__open_folder__":
            self._refresh_recent_folder_combos()
            self._choose_folder()
            return
        if isinstance(value, str) and value:
            if self._current_folder and normalized_path_key(value) == normalized_path_key(self._current_folder):
                self._refresh_recent_folder_combos()
                return
            self._open_recent_folder(value)
            return
        self._refresh_recent_folder_combos()

    def _commit_path_combo_text(self, combo: QComboBox) -> None:
        raw_text = combo.currentText().strip().strip('"')
        folder = normalize_filesystem_path(raw_text)
        if not folder:
            self._refresh_recent_folder_combos()
            return
        if not os.path.isdir(folder):
            self.statusBar().showMessage(f"Folder not found: {folder}")
            self._refresh_recent_folder_combos()
            return
        if self._current_folder and normalized_path_key(folder) == normalized_path_key(self._current_folder):
            self._refresh_recent_folder_combos()
            return
        self._select_folder(folder)

    def _remember_recent_destination(self, destination_dir: str) -> None:
        normalized = normalize_filesystem_path(destination_dir)
        if not normalized or not os.path.isdir(normalized):
            return
        self._recent_destinations = [
            normalized,
            *[
                item
                for item in self._recent_destinations
                if normalized_path_key(item) != normalized_path_key(normalized)
            ],
        ][:10]
        self._save_recent_destinations()

    def _recent_destination_paths(self, *, exclude_current_folder: bool = False) -> list[str]:
        current_key = normalized_path_key(self._current_folder) if exclude_current_folder and self._current_folder else ""
        cleaned: list[str] = []
        seen: set[str] = set()
        for path in self._recent_destinations:
            if not os.path.isdir(path):
                continue
            normalized = normalized_path_key(path)
            if normalized in seen or (current_key and normalized == current_key):
                continue
            seen.add(normalized)
            cleaned.append(path)
        if cleaned != self._recent_destinations:
            self._recent_destinations = cleaned[:10]
            self._save_recent_destinations()
        return cleaned

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
        if not folder or not os.path.isdir(folder) or folder in self._favorites:
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

    def _handle_columns_changed(self) -> None:
        columns = self._normalize_column_count(self.columns_combo.currentData())
        self._set_column_count(columns)

    def _handle_auto_advance_toggled(self, checked: bool) -> None:
        self._auto_advance_enabled = checked
        self.preview.set_auto_advance_enabled(checked)
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

    def _handle_compact_cards_toggled(self, checked: bool) -> None:
        self._compact_cards_enabled = bool(checked)
        self._settings.setValue(self.COMPACT_CARDS_KEY, self._compact_cards_enabled)
        self.grid.set_compact_card_mode(self._compact_cards_enabled)
        self._remember_current_folder_view_state()
        self._update_action_states()
        mode = "on" if checked else "off"
        self.statusBar().showMessage(f"Compact cards {mode}")

    def _handle_compare_toggled(self, checked: bool) -> None:
        if not checked and self._winner_ladder_state is not None:
            self._finish_winner_ladder(reopen_preview=False, show_message=False)
        self._compare_enabled = checked
        self.preview.set_compare_mode(checked)
        self._update_action_states()
        mode = "on" if checked else "off"
        self.statusBar().showMessage(f"Compare {mode}")
        if self.preview.isVisible():
            index = self.grid.current_index()
            if index >= 0:
                self._open_preview(index)

    def _toggle_compare_shortcut(self) -> None:
        if self.actions is not None:
            self.actions.compare_mode.trigger()

    def _handle_auto_bracket_toggled(self, checked: bool) -> None:
        self._auto_bracket_enabled = checked
        self._settings.setValue(self.AUTO_BRACKET_KEY, checked)
        self.preview.set_auto_bracket_mode(checked)
        mode = "on" if checked else "off"
        self.statusBar().showMessage(f"Auto-bracket compare {mode}")
        if self.preview.isVisible() and self._compare_enabled:
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
        index = self._record_index_for_path(path)
        if index is None:
            return
        anchor_path = self.preview.anchor_path() or path
        self._toggle_winner(index, advance_override=False, current_path_override=anchor_path)
        annotation = self._annotations.get(path, SessionAnnotation())
        self.preview.set_annotation_state(path, annotation.winner, annotation.reject)
        anchor_index = self._record_index_for_path(anchor_path)
        if anchor_index is not None:
            self.grid.set_current_index(anchor_index)

    def _handle_preview_reject_requested(self, path: str) -> None:
        index = self._record_index_for_path(path)
        if index is None:
            return
        anchor_path = self.preview.anchor_path() or path
        self._toggle_reject(index, advance_override=False, current_path_override=anchor_path)
        annotation = self._annotations.get(path, SessionAnnotation())
        self.preview.set_annotation_state(path, annotation.winner, annotation.reject)
        anchor_index = self._record_index_for_path(anchor_path)
        if anchor_index is not None:
            self.grid.set_current_index(anchor_index)

    def _handle_preview_keep_requested(self, path: str) -> None:
        self._dispatch_preview_action(path, self._keep_record)

    def _handle_preview_rename_requested(self, path: str) -> None:
        index = self._record_index_for_path(path)
        if index is None:
            return
        renamed_path = self._rename_record_prompt(index)
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

    def _handle_preview_rate_requested(self, path: str, rating: int) -> None:
        index = self._record_index_for_path(path)
        if index is None:
            return
        self._rate_record(index, rating)
        self.grid.set_current_index(index)

    def _handle_preview_tag_requested(self, path: str) -> None:
        self._dispatch_preview_action(path, self._tag_record, preserve_anchor=True)

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
        if self._winner_ladder_state is not None:
            self._finish_winner_ladder(reopen_preview=False, show_message=False)

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
        current_ai = self._ai_result_for_index(index)
        if current_ai is not None and current_ai.group_size > 1:
            rows = [(row_index, record) for row_index, record, _result in self._visible_ai_group_rows(current_ai.group_id)]
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
        displayed_path = self.grid.displayed_variant_path(index) if record.has_variant_stack else self._preview_source_path(record)
        edited_candidates = self._ordered_edited_candidates(record, displayed_path)
        edited_path = edited_candidates[0] if edited_candidates else ""
        return PreviewEntry(
            record=record,
            source_path=displayed_path,
            winner=annotation.winner,
            reject=annotation.reject,
            edited_path=edited_path,
            edited_candidates=tuple(edited_candidates),
            label=label,
            ai_result=self._ai_result_for_record(record, preferred_path=displayed_path),
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
        current_ai = self._ai_result_for_index(index)
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
            self._load_folder(self._current_folder, force_refresh=True)

    def _rebuild_current_folder_catalog_cache(self) -> None:
        if self._scope_kind != "folder" or not self._current_folder:
            self.statusBar().showMessage("Open a real folder before rebuilding its catalog cache.")
            return
        self.statusBar().showMessage(f"Rebuilding catalog cache for {self._current_folder}...")
        self._load_folder(self._current_folder, force_refresh=True, bypass_catalog_cache=True)

    def _load_cached_folder_records(self, folder: str) -> tuple[list[ImageRecord] | None, str]:
        normalized_folder = normalize_filesystem_path(folder)
        records = self._catalog_repository.load_folder_records(normalized_folder)
        if records is not None:
            return records, "catalog"
        return None, ""

    def _persist_folder_record_cache(self, folder: str, records: list[ImageRecord], *, source: str = "window") -> None:
        normalized_folder = normalize_filesystem_path(folder)
        if not normalized_folder:
            return
        self._catalog_repository.save_folder_records(normalized_folder, records, source=source)

    def _refresh_current_folder_watch(self) -> None:
        target = ""
        if self._watch_current_folder_enabled and self._scope_kind == "folder" and self._current_folder:
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
        if normalized_path_key(path) != normalized_path_key(self._current_folder):
            return
        self._queue_watched_folder_refresh()
        if self._scan_in_progress:
            self.statusBar().showMessage(f"Detected folder changes in {self._current_folder}; refresh queued.")
            return
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
        if not os.path.isdir(self._current_folder):
            self._folder_watch_refresh_pending = False
            self._refresh_current_folder_watch()
            return
        self._folder_watch_refresh_pending = False
        self.statusBar().showMessage(f"Refreshing changed folder: {self._current_folder}")
        self._load_folder(self._current_folder, force_refresh=True)

    def _load_folder(
        self,
        folder: str,
        *,
        force_refresh: bool = False,
        chunked_restore: bool = False,
        bypass_catalog_cache: bool = False,
    ) -> None:
        if not folder:
            return
        if self._active_tool_mode or self.grid.tool_checkbox_mode():
            self._cancel_tool_mode(show_message=False)
        self._cancel_records_view_chunk()
        folder_changed = normalized_path_key(folder) != normalized_path_key(self._current_folder)
        if folder_changed:
            self._remember_current_folder_view_state()
        self._current_folder = folder
        self._set_scope_state(kind="folder", scope_id=normalized_path_key(folder), label=folder)
        self._refresh_current_folder_watch()
        self._settings.setValue(self.LAST_FOLDER_KEY, folder)
        self._remember_recent_folder(folder)
        self._apply_folder_view_state(folder)
        self._scan_token += 1
        token = self._scan_token
        if chunked_restore:
            self._chunked_load_scan_tokens.add(token)
        self._chunked_load_scan_tokens = {existing for existing in self._chunked_load_scan_tokens if existing >= token}
        self._scan_showed_cached = False
        self._scan_cached_source = ""
        self._scan_in_progress = True
        self._catalog_load_source = "scanning"
        self._catalog_load_detail = f"Scanning {folder}..."
        self._refresh_catalog_status_indicator()
        self._cancel_scope_enrichment_task()
        self._annotation_hydration_token += 1
        if self._active_annotation_hydration_task is not None:
            self._active_annotation_hydration_task.cancel()
        self._active_annotation_hydration_task = None
        self._annotation_hydration_dirty_paths.clear()
        self._annotation_hydration_pending_clear_paths.clear()
        self._annotation_reapply_timer.stop()
        self._review_intelligence_token += 1
        self._review_chunk_flush_timer.stop()
        self._review_chunk_dirty_paths.clear()
        if self._active_review_intelligence_task is not None:
            self._active_review_intelligence_task.cancel()
            self._active_review_intelligence_task = None
        self._refresh_recycle_button()
        if folder_changed:
            self._clear_ai_results_state(preserve_setting=True)
        # Cache reads can be large enough to make Windows mark startup as hung. Let the
        # scanner worker emit cached records instead of loading the cache on the UI thread.
        self.statusBar().showMessage(f"Scanning {folder}...")
        self._all_records = []
        self._all_records_by_path = {}
        self._records = []
        self._last_view_record_paths = ()
        self._record_index_by_path = {}
        self._edited_candidates_cache = {}
        self._visible_review_group_rows_by_id = {}
        self._visible_ai_group_rows_by_id = {}
        self._accepted_count = 0
        self._rejected_count = 0
        self._unreviewed_count = 0
        self._records_have_resizable = False
        self._records_have_convertible = False
        self._invalidate_training_label_counts_cache()
        self._summary_ai_text = "AI: Off" if self._ai_bundle is None else self._summary_ai_text
        self._summary_ai_tooltip = "No AI export is currently loaded." if self._ai_bundle is None else self._summary_ai_tooltip
        self._filter_metadata_by_path = {}
        self._filter_metadata_record_paths = set()
        self._filter_metadata_loaded_paths = set()
        self._filter_metadata_requested_paths = set()
        self._filter_metadata_queue = deque()
        self._metadata_membership_dirty_paths = set()
        self._metadata_scroll_prefetch_timer.stop()
        self._metadata_request_timer.stop()
        self.grid.set_empty_message(f"Scanning {Path(folder).name}...")
        self.grid.set_items([])
        self._set_annotation_views()
        self._refresh_viewport_mode()
        self._update_ai_toolbar_state()

        task = FolderScanTask(
            folder,
            token,
            self._sort_mode,
            prefer_cached_only=(not force_refresh and self._is_slow_source_folder(folder)),
            use_catalog_cache=self._catalog_cache_reads_enabled(),
            read_cached_records=not bypass_catalog_cache,
        )
        self._active_scan_tasks[token] = task
        task.signals.cached.connect(self._handle_scan_cached, Qt.ConnectionType.QueuedConnection)
        task.signals.finished.connect(self._handle_scan_finished, Qt.ConnectionType.QueuedConnection)
        task.signals.failed.connect(self._handle_scan_failed, Qt.ConnectionType.QueuedConnection)
        self._scan_pool.start(task)

    def _load_virtual_scope_records(
        self,
        records: list[ImageRecord],
        *,
        scope_kind: str,
        scope_id: str,
        scope_label: str,
    ) -> None:
        if self._active_tool_mode or self.grid.tool_checkbox_mode():
            self._cancel_tool_mode(show_message=False)
        self._remember_current_folder_view_state()
        self._pending_folder_scroll_value = None
        self._scan_in_progress = False
        self._scan_token += 1
        self._cancel_scope_enrichment_task()
        self._annotation_hydration_token += 1
        if self._active_annotation_hydration_task is not None:
            self._active_annotation_hydration_task.cancel()
        self._active_annotation_hydration_task = None
        self._annotation_hydration_dirty_paths.clear()
        self._annotation_hydration_pending_clear_paths.clear()
        self._annotation_reapply_timer.stop()
        self._review_intelligence_token += 1
        self._review_chunk_flush_timer.stop()
        self._review_chunk_dirty_paths.clear()
        if self._active_review_intelligence_task is not None:
            self._active_review_intelligence_task.cancel()
            self._active_review_intelligence_task = None
        self._current_folder = ""
        self._set_scope_state(kind=scope_kind, scope_id=scope_id, label=scope_label)
        self._refresh_current_folder_watch()
        self._scan_showed_cached = False
        self._scan_cached_source = ""
        self._catalog_load_source = "idle"
        self._catalog_load_detail = "Virtual scopes are loaded from app state, not folder cache."
        self._refresh_catalog_status_indicator()
        self._clear_ai_results_state(preserve_setting=True)
        self._refresh_recycle_button()
        self.grid.set_empty_message("Choose a folder to start triaging images.")
        self._apply_loaded_records(records)
        self.statusBar().showMessage(f"Loaded {scope_label} ({len(records)} image bundle(s))")

    def _cancel_records_view_chunk(self) -> None:
        self._records_view_chunk_timer.stop()
        self._records_view_chunk_records = []
        self._records_view_chunk_next_index = 0
        self._records_view_chunk_current_path = None
        self._records_view_chunk_post_load_enrichment = ""

    def _records_view_chunk_active(self) -> bool:
        return bool(self._records_view_chunk_records)

    def _finish_loaded_records_enrichment(self, records: list[ImageRecord], *, defer_enrichment: bool) -> None:
        if defer_enrichment:
            self._deferred_enrichment_pending = True
            self._deferred_enrichment_scope_key = self._current_scope_key()
            self._deferred_enrichment_token = self._scan_token
            if not self._scan_in_progress and not self._records_view_chunk_active():
                self._schedule_loaded_records_enrichment()
            return
        self._start_scope_enrichment_task(records)
        self._start_annotation_hydration(records)
        self._start_review_intelligence_analysis()

    def _apply_loaded_records(
        self,
        records: list[ImageRecord],
        *,
        defer_enrichment: bool = False,
        chunked_view: bool = False,
    ) -> None:
        self._all_records = records
        self._all_records_by_path = {record.path: record for record in records}
        self._refresh_record_capability_cache(records)
        self._invalidate_training_label_counts_cache()
        self._edited_candidates_cache = {}
        self._review_intelligence = None
        self._deferred_enrichment_pending = False
        self._deferred_enrichment_scheduled = False
        self._deferred_enrichment_scope_key = ""
        self._deferred_enrichment_token = 0
        self._records_view_cache.mark(ViewInvalidationReason.LOAD_CHANGED)
        self._reset_filter_metadata_index(records)
        current_paths = {record.path for record in records}
        self._annotations = {
            path: annotation
            for path, annotation in self._annotations.items()
            if path in current_paths
        }
        self._correction_events = []
        self._taste_profile = TasteProfile()
        self._burst_recommendations = {}
        self._workflow_insights_by_path = {}
        view_complete = self._apply_records_view(
            chunked=chunked_view,
            post_load_enrichment="defer" if defer_enrichment else "start",
        )
        if view_complete:
            self._finish_loaded_records_enrichment(records, defer_enrichment=defer_enrichment)

    def _schedule_loaded_records_enrichment(self) -> None:
        if not self._deferred_enrichment_pending or self._deferred_enrichment_scheduled:
            return
        if self._records_view_chunk_active():
            return
        self._deferred_enrichment_scheduled = True
        QTimer.singleShot(0, self._run_loaded_records_enrichment)

    def _run_loaded_records_enrichment(self) -> None:
        self._deferred_enrichment_scheduled = False
        if not self._deferred_enrichment_pending:
            return
        if (
            self._deferred_enrichment_token != self._scan_token
            or self._deferred_enrichment_scope_key != self._current_scope_key()
        ):
            self._deferred_enrichment_pending = False
            self._deferred_enrichment_scope_key = ""
            self._deferred_enrichment_token = 0
            return
        self._deferred_enrichment_pending = False
        self._deferred_enrichment_scope_key = ""
        self._deferred_enrichment_token = 0
        records = list(self._all_records)
        self._start_scope_enrichment_task(records)
        self._start_annotation_hydration(records)
        self._start_review_intelligence_analysis()

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

        self._cancel_scope_enrichment_task()
        self._scope_enrichment_token += 1
        token = self._scope_enrichment_token
        scope_key = self._current_scope_key()
        task = ScopeEnrichmentTask(
            scope_key=scope_key,
            token=token,
            session_id=self._session_id,
            folder_path=self._current_folder,
            include_all_scope_events=(not self._current_folder and self._scope_kind != "folder"),
            records=tuple(active_records),
            ai_bundle=self._ai_bundle,
            review_bundle=self._review_intelligence,
        )
        task.signals.finished.connect(self._handle_scope_enrichment_finished, Qt.ConnectionType.QueuedConnection)
        task.signals.failed.connect(self._handle_scope_enrichment_failed, Qt.ConnectionType.QueuedConnection)
        self._active_scope_enrichment_task = task
        self._scope_enrichment_pool.start(task)

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
        current_path = self._current_visible_record_path()
        self._apply_records_view(current_path=current_path)

    def _handle_scope_enrichment_failed(self, scope_key: str, token: int, message: str) -> None:
        if token != self._scope_enrichment_token or scope_key != self._current_scope_key():
            return
        self._active_scope_enrichment_task = None
        self.statusBar().showMessage(f"Workflow enrichment fallback active: {message}")

    def _start_annotation_hydration(self, records: list[ImageRecord]) -> None:
        self._annotation_hydration_token += 1
        token = self._annotation_hydration_token
        previous_task = self._active_annotation_hydration_task
        if previous_task is not None:
            previous_task.cancel()
        self._active_annotation_hydration_task = None
        self._annotation_hydration_dirty_paths.clear()
        self._annotation_hydration_pending_clear_paths = {record.path for record in records if record.path in self._annotations}
        self._annotation_reapply_timer.stop()
        if not records:
            return
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
        current_path = self._current_visible_record_path()
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
            return
        if not force and len(self._all_records) > self.AUTO_REVIEW_INTELLIGENCE_MAX_RECORDS:
            self._review_intelligence = None
            self._review_chunk_flush_timer.stop()
            self._review_chunk_dirty_paths.clear()
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
        )
        task.signals.started.connect(self._handle_review_intelligence_started, Qt.ConnectionType.QueuedConnection)
        task.signals.progress.connect(self._handle_review_intelligence_progress, Qt.ConnectionType.QueuedConnection)
        task.signals.chunk.connect(self._handle_review_intelligence_chunk, Qt.ConnectionType.QueuedConnection)
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
        current_path = self._current_visible_record_path()
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
                self.grid.set_current_index(index)
        self._update_filter_summary()
        self._update_action_states()
        self._update_status()

    def _handle_review_intelligence_cancelled(self, folder: str, token: int) -> None:
        if token != self._review_intelligence_token or folder != self._current_scope_key():
            return
        self._active_review_intelligence_task = None
        self._review_chunk_flush_timer.stop()
        self._review_chunk_dirty_paths.clear()

    def _handle_review_intelligence_finished(self, folder: str, token: int, bundle: ReviewIntelligenceBundle) -> None:
        if token != self._review_intelligence_token or folder != self._current_scope_key():
            return
        self._active_review_intelligence_task = None
        self._review_chunk_flush_timer.stop()
        self._review_chunk_dirty_paths.clear()
        self._review_intelligence = bundle
        current_path = self._current_visible_record_path()
        self._records_view_cache.mark(ViewInvalidationReason.REVIEW_CHANGED)
        self._apply_records_view(current_path=current_path)
        self._start_scope_enrichment_task()
        if self.preview.isVisible():
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
        self.statusBar().showMessage(f"Smart grouping fallback active: {message}")

    def _reset_filter_metadata_index(self, records: list[ImageRecord]) -> None:
        self._filter_metadata_by_path = {}
        self._filter_metadata_loaded_paths = set()
        self._filter_metadata_requested_paths = set()
        self._filter_metadata_queue = deque()
        self._metadata_membership_dirty_paths = set()
        self._metadata_scroll_last_value = self.grid.verticalScrollBar().value()
        self._metadata_scroll_direction = 1
        self._metadata_scroll_prefetch_timer.stop()
        self._metadata_request_timer.stop()
        self._filter_metadata_record_paths = {record.path for record in records}
        for record in records:
            cached = self._filter_metadata_manager.get_cached(record)
            if cached is not None:
                self._filter_metadata_by_path[record.path] = cached
                self._filter_metadata_loaded_paths.add(record.path)
        if records:
            self._enqueue_filter_metadata_paths(self._metadata_prefetch_seed_paths(), front=True)

    def _handle_filter_metadata_ready(self, key, metadata) -> None:
        record = self._all_records_by_path.get(key.path)
        if record is None or record.path not in self._filter_metadata_record_paths:
            return
        self._filter_metadata_requested_paths.discard(record.path)
        self._filter_metadata_by_path[record.path] = metadata
        self._filter_metadata_loaded_paths.add(record.path)
        if self._filter_query.requires_metadata:
            if self._metadata_changes_filter_membership(record, metadata):
                self._metadata_membership_dirty_paths.add(record.path)
                self._metadata_reapply_timer.start()
            else:
                self._update_filter_summary()
        else:
            self._update_filter_summary()
        if self._filter_metadata_queue and not self._metadata_request_timer.isActive():
            self._metadata_request_timer.start()

    def _handle_metadata_filter_batch_update(self) -> None:
        current_path = self._current_visible_record_path()
        if self._filter_query.requires_metadata:
            if not self._metadata_membership_dirty_paths:
                return
            self._metadata_membership_dirty_paths.clear()
            self._apply_records_view(current_path=current_path)
            return
        if self._burst_groups_enabled or self._burst_stacks_enabled:
            self._refresh_burst_group_view()

    def _metadata_prefetch_seed_paths(self, *, lookahead: int = 120) -> list[str]:
        visible_paths = self.grid.visible_item_paths(limit=220)
        if not visible_paths:
            return [record.path for record in self._all_records[: max(80, lookahead)]]

        ordered: list[str] = []
        seen: set[str] = set()
        for path in visible_paths:
            if path and path not in seen:
                ordered.append(path)
                seen.add(path)

        visible_indexes = [self._record_index_by_path[path] for path in visible_paths if path in self._record_index_by_path]
        if not visible_indexes:
            return ordered
        min_visible = min(visible_indexes)
        max_visible = max(visible_indexes)
        direction = 1 if self._metadata_scroll_direction >= 0 else -1
        if direction >= 0:
            start = max_visible + 1
            end = min(len(self._records), start + max(0, lookahead))
            candidate_paths = [self._records[index].path for index in range(start, end)]
        else:
            end = min_visible
            start = max(0, end - max(0, lookahead))
            candidate_paths = [self._records[index].path for index in range(end - 1, start - 1, -1)]

        for path in candidate_paths:
            if path and path not in seen:
                ordered.append(path)
                seen.add(path)
        return ordered

    def _enqueue_filter_metadata_paths(
        self,
        paths: list[str] | tuple[str, ...] | set[str],
        *,
        front: bool = False,
    ) -> None:
        if not paths:
            return
        pending_keys = {normalized_path_key(path) for path in self._filter_metadata_queue}
        additions: list[str] = []
        for path in paths:
            if not path:
                continue
            if path not in self._filter_metadata_record_paths:
                continue
            if path in self._filter_metadata_loaded_paths or path in self._filter_metadata_requested_paths:
                continue
            key = normalized_path_key(path)
            if key in pending_keys:
                continue
            pending_keys.add(key)
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
                self._filter_metadata_queue.pop()
        if not self._metadata_request_timer.isActive():
            self._metadata_request_timer.start()

    def _schedule_metadata_scroll_prefetch(self, value: int) -> None:
        if not self._records:
            return
        if value != self._metadata_scroll_last_value:
            self._metadata_scroll_direction = 1 if value > self._metadata_scroll_last_value else -1
            self._metadata_scroll_last_value = value
        self._metadata_scroll_prefetch_timer.start()

    def _run_metadata_scroll_prefetch(self) -> None:
        if not self._records:
            return
        self._enqueue_filter_metadata_paths(self._metadata_prefetch_seed_paths(), front=True)

    def _metadata_changes_filter_membership(self, record: ImageRecord, metadata: CaptureMetadata) -> bool:
        if not self._filter_query.requires_metadata:
            return False
        annotation = self._annotations.get(record.path, SessionAnnotation())
        needs_ai = (
            self._filter_query.quick_filter in {FilterMode.AI_TOP_PICKS, FilterMode.AI_GROUPED, FilterMode.AI_DISAGREEMENTS}
            or self._filter_query.ai_state != AIStateFilter.ALL
        )
        needs_review = self._filter_query.quick_filter in {FilterMode.SMART_GROUPS, FilterMode.DUPLICATES}
        needs_workflow = (
            self._filter_query.quick_filter in {FilterMode.AI_DISAGREEMENTS, FilterMode.REVIEW_ROUNDS}
            or self._filter_query.ai_state == AIStateFilter.DISAGREEMENTS
            or bool(normalize_review_round(self._filter_query.review_round))
        )
        ai_result = self._ai_result_for_record(record) if needs_ai else None
        review_insight = self._review_insight_for_record(record) if needs_review else None
        workflow_insight = self._workflow_insight_for_record(record) if needs_workflow else None
        old_match = matches_record_query(
            record,
            self._filter_query,
            annotation=annotation,
            ai_result=ai_result,
            metadata=EMPTY_METADATA,
            review_insight=review_insight,
            workflow_insight=workflow_insight,
        )
        new_match = matches_record_query(
            record,
            self._filter_query,
            annotation=annotation,
            ai_result=ai_result,
            metadata=metadata,
            review_insight=review_insight,
            workflow_insight=workflow_insight,
        )
        return old_match != new_match

    def _drain_filter_metadata_requests(self) -> None:
        if not self._filter_metadata_queue:
            self._metadata_request_timer.stop()
            return
        requested = 0
        while self._filter_metadata_queue and requested < 20:
            path = self._filter_metadata_queue.popleft()
            if path in self._filter_metadata_loaded_paths or path in self._filter_metadata_requested_paths:
                continue
            record = self._all_records_by_path.get(path)
            if record is None:
                continue
            self._filter_metadata_requested_paths.add(path)
            priority = max(1, 12_000 - requested * 200)
            self._filter_metadata_manager.request_metadata(record, priority=priority)
            requested += 1
        if not self._filter_metadata_queue:
            self._metadata_request_timer.stop()

    def _catalog_cache_reads_enabled(self) -> bool:
        override = catalog_cache_env_override()
        return self._catalog_cache_enabled if override is None else override

    @staticmethod
    def _catalog_source_label(source: str) -> str:
        if source == "catalog":
            return "Catalog Cache"
        if source == "live":
            return "Live Scan"
        if source == "scanning":
            return "Scanning"
        if source == "failed":
            return "Failed"
        return "Idle"

    def _catalog_status_badge_text(self) -> str:
        if self._catalog_load_source == "live" and self._scan_cached_source:
            return f"Load: {self._catalog_source_label(self._scan_cached_source)} + Live"
        return f"Load: {self._catalog_source_label(self._catalog_load_source)}"

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
        if stats.error_message:
            lines.append(f"Catalog error: {stats.error_message}")
        else:
            lines.append(f"Indexed folders: {stats.folder_count}")
            lines.append(f"Indexed image bundles: {stats.record_count}")
            if stats.last_indexed_at:
                lines.append(f"Last indexed: {stats.last_indexed_at}")
        lines.append(f"Database: {stats.db_path}")
        return "\n".join(lines)

    def _refresh_catalog_status_indicator(self) -> None:
        if not hasattr(self, "catalog_status_label"):
            return
        self.catalog_status_label.setText(self._catalog_status_badge_text())
        self.catalog_status_label.setToolTip(self._catalog_debug_summary(include_current=True))

    def _handle_scan_cached(self, folder: str, token: int, records: list[ImageRecord], source: str) -> None:
        if token != self._scan_token or normalized_path_key(folder) != normalized_path_key(self._current_folder) or not records:
            return
        self._scan_showed_cached = True
        self._scan_cached_source = source
        self._catalog_load_source = source or "idle"
        self._catalog_load_detail = f"Loaded from {self._catalog_source_label(source)}; live refresh still running."
        self._refresh_catalog_status_indicator()
        self.grid.set_empty_message("Choose a folder to start triaging images.")
        self._apply_loaded_records(
            records,
            defer_enrichment=True,
            chunked_view=token in self._chunked_load_scan_tokens,
        )
        if self._ui_mode == "ai":
            self._load_hidden_ai_results_for_current_folder(show_message=False)
        cache_label = self._catalog_source_label(source)
        self.statusBar().showMessage(f"Loaded {cache_label.lower()} for {self._current_folder}, refreshing from disk...")

    def _handle_scan_finished(self, folder: str, token: int, records: list[ImageRecord], source: str) -> None:
        self._active_scan_tasks.pop(token, None)
        if token != self._scan_token or normalized_path_key(folder) != normalized_path_key(self._current_folder):
            self._chunked_load_scan_tokens.discard(token)
            return

        self._scan_in_progress = False
        self.grid.set_empty_message("Choose a folder to start triaging images.")
        chunked_view = token in self._chunked_load_scan_tokens
        self._chunked_load_scan_tokens.discard(token)
        if self._scan_showed_cached and self._records_match_for_refresh(self._all_records, records):
            self._catalog_load_source = source or "live"
            if self._scan_cached_source:
                self._catalog_load_detail = f"Opened from {self._catalog_source_label(self._scan_cached_source)} and confirmed by live scan."
            else:
                self._catalog_load_detail = "Live scan confirmed the current folder contents."
            self._refresh_catalog_status_indicator()
            self._schedule_loaded_records_enrichment()
            if self._ui_mode == "ai":
                self._load_hidden_ai_results_for_current_folder(show_message=False)
            self.statusBar().showMessage(f"Refreshed {self._current_folder}")
            if self._folder_watch_refresh_pending:
                self._folder_watch_refresh_timer.start(250)
            return
        self._apply_loaded_records(records, chunked_view=chunked_view)
        self._catalog_load_source = source or "live"
        if self._scan_showed_cached and self._scan_cached_source:
            self._catalog_load_detail = f"Opened from {self._catalog_source_label(self._scan_cached_source)} and refreshed from disk."
        else:
            self._catalog_load_detail = "Loaded directly from a live folder scan."
        self._refresh_catalog_status_indicator()
        if self._ui_mode == "ai":
            self._load_hidden_ai_results_for_current_folder(show_message=False)
        if self._scan_showed_cached:
            self.statusBar().showMessage(f"Refreshed {self._current_folder}")
        if self._folder_watch_refresh_pending:
            self._folder_watch_refresh_timer.start(250)

    def _handle_scan_failed(self, folder: str, token: int, message: str) -> None:
        self._active_scan_tasks.pop(token, None)
        if token != self._scan_token or normalized_path_key(folder) != normalized_path_key(self._current_folder):
            self._chunked_load_scan_tokens.discard(token)
            return
        self._chunked_load_scan_tokens.discard(token)
        self._cancel_records_view_chunk()
        self._pending_folder_scroll_value = None
        self._scan_in_progress = False
        self._cancel_scope_enrichment_task()
        self._annotation_hydration_token += 1
        self._active_annotation_hydration_task = None
        self._annotation_hydration_dirty_paths.clear()
        self._annotation_hydration_pending_clear_paths.clear()
        self._annotation_reapply_timer.stop()
        self._deferred_enrichment_pending = False
        self._deferred_enrichment_scheduled = False
        self._deferred_enrichment_scope_key = ""
        self._deferred_enrichment_token = 0
        self._review_chunk_flush_timer.stop()
        self._review_chunk_dirty_paths.clear()
        self._all_records = []
        self._all_records_by_path = {}
        self._records = []
        self._last_view_record_paths = ()
        self._record_index_by_path = {}
        self._edited_candidates_cache = {}
        self._visible_review_group_rows_by_id = {}
        self._visible_ai_group_rows_by_id = {}
        self._accepted_count = 0
        self._rejected_count = 0
        self._unreviewed_count = 0
        self._records_have_resizable = False
        self._records_have_convertible = False
        self._invalidate_training_label_counts_cache()
        self._correction_events = []
        self._taste_profile = TasteProfile()
        self._burst_recommendations = {}
        self._workflow_insights_by_path = {}
        self._summary_ai_text = "AI: Off" if self._ai_bundle is None else self._summary_ai_text
        self._summary_ai_tooltip = "No AI export is currently loaded." if self._ai_bundle is None else self._summary_ai_tooltip
        self._filter_metadata_by_path = {}
        self._filter_metadata_record_paths = set()
        self._filter_metadata_loaded_paths = set()
        self._filter_metadata_requested_paths = set()
        self._filter_metadata_queue = deque()
        self._metadata_membership_dirty_paths = set()
        self._metadata_scroll_prefetch_timer.stop()
        self.grid.set_empty_message("Could not scan this folder.")
        self.grid.set_items([])
        self._refresh_recycle_button()
        self._update_action_states()
        self._catalog_load_source = "failed"
        self._catalog_load_detail = message
        self._refresh_catalog_status_indicator()
        self.statusBar().showMessage(f"Could not scan {self._current_folder}: {message}")
        if self._folder_watch_refresh_pending:
            self._folder_watch_refresh_timer.start(450)

    def _handle_current_changed(self, index: int) -> None:
        self._enqueue_filter_metadata_paths(self.grid.visible_item_paths(limit=200), front=True)
        self._update_action_states()
        self._update_status(index=index)
        if not self.preview.isVisible():
            self._schedule_preview_preload(index)

    def _handle_grid_selection_changed(self) -> None:
        self._update_action_states()
        self._update_status()
        self._enqueue_filter_metadata_paths(self._metadata_prefetch_seed_paths(lookahead=100), front=True)

    def _show_ai_menu(self) -> None:
        if self.actions is None:
            return
        menu = QMenu(self)
        menu.addAction(self.actions.download_ai_model)
        menu.addSeparator()
        run_action = menu.addAction(self.actions.run_ai_culling)
        load_hidden_action = menu.addAction(self.actions.load_saved_ai)
        load_action = menu.addAction(self.actions.load_ai_results)
        clear_action = menu.addAction(self.actions.clear_ai_results)
        report_action = menu.addAction(self.actions.open_ai_report)
        menu.addSeparator()
        next_pick_action = menu.addAction(self.actions.next_ai_pick)
        next_unreviewed_pick_action = menu.addAction(self.actions.next_unreviewed_ai_pick)
        compare_group_action = menu.addAction(self.actions.compare_ai_group)
        jump_group_top_action = menu.addAction("Jump To AI Top Pick In Group")
        current_index = self.grid.current_index()
        current_ai_result = self._ai_result_for_index(current_index)
        jump_group_top_action.setEnabled(bool(current_ai_result and current_ai_result.group_size > 1))
        chosen = menu.exec(QCursor.pos())
        if chosen == jump_group_top_action:
            self._jump_to_ai_top_pick_in_group()

    def _choose_ai_results(self) -> None:
        start_dir = self._settings.value(self.AI_RESULTS_KEY, "", str) or self._current_folder or QDir.homePath()
        folder = QFileDialog.getExistingDirectory(self, "Choose AI Results Folder", start_dir)
        if folder:
            self._load_ai_results(folder)

    def _restore_ai_results(self, *, force: bool = False) -> bool:
        # Keep AI bundle loading off the normal startup/manual browse path.
        if not force and self._ui_mode != "ai":
            self._refresh_ai_state()
            return False
        saved_path = self._settings.value(self.AI_RESULTS_KEY, "", str)
        if not saved_path:
            self._refresh_ai_state()
            return False
        if not Path(saved_path).exists():
            self._settings.remove(self.AI_RESULTS_KEY)
            self._refresh_ai_state()
            return False
        return self._load_ai_results(saved_path, show_message=False)

    def _clear_ai_results_state(self, *, preserve_setting: bool = False) -> None:
        self._ai_bundle = None
        if self._active_ai_task is None:
            self._ai_stage_index = 0
            self._ai_stage_total = 3
            self._ai_stage_message = "Ready to run AI culling"
            self._ai_progress_current = 0
            self._ai_progress_total = 0
            self._ai_progress_eta_text = ""
        if not preserve_setting:
            self._settings.remove(self.AI_RESULTS_KEY)
        self._refresh_ai_state()

    def _hidden_ai_paths_for_current_folder(self):
        if not self._current_folder:
            return None
        return build_ai_workflow_paths(self._current_folder)

    def _load_hidden_ai_results_for_current_folder(self, *, show_message: bool = True) -> bool:
        if not self._current_folder:
            return False
        report_dir = existing_hidden_ai_report_dir(self._current_folder)
        if report_dir is None:
            if show_message:
                self.statusBar().showMessage("No saved hidden AI results were found for this folder")
            self._update_ai_toolbar_state()
            return False
        return self._load_ai_results(report_dir, show_message=show_message)

    @staticmethod
    def _records_match_for_refresh(existing: list[ImageRecord], incoming: list[ImageRecord]) -> bool:
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

    def _load_ai_results(self, path: str | Path, *, show_message: bool = True) -> bool:
        try:
            bundle = load_ai_bundle(path)
        except (FileNotFoundError, ValueError, OSError) as exc:
            if show_message:
                QMessageBox.warning(self, "AI Results", f"Could not load AI results.\n\n{exc}")
                self.statusBar().showMessage("AI results load failed")
            return False

        self._ai_bundle = bundle
        self._settings.setValue(self.AI_RESULTS_KEY, str(path))
        if self._active_ai_task is None and self._ai_stage_message != "AI culling complete":
            self._ai_stage_index = 0
            self._ai_stage_total = 3
            self._ai_stage_message = "Saved AI cache loaded"
            self._ai_progress_current = 0
            self._ai_progress_total = 0
            self._ai_progress_eta_text = ""
        self._refresh_ai_state()

        matched = bundle.count_matches(self._all_records)
        if show_message:
            source_name = Path(bundle.export_csv_path).name
            self.statusBar().showMessage(f"Loaded AI results from {source_name} ({matched} matched image(s))")
        return True

    def _clear_ai_results(self) -> None:
        if self._ai_bundle is None:
            return
        self._clear_ai_results_state()
        self.statusBar().showMessage("Cleared AI results")

    def _open_ai_report(self) -> None:
        if self._ai_bundle is None or not self._ai_bundle.report_html_path:
            self.statusBar().showMessage("No AI HTML report is available")
            return
        report_path = Path(self._ai_bundle.report_html_path)
        if not report_path.exists():
            self.statusBar().showMessage("AI HTML report could not be found")
            return
        open_with_default(str(report_path))
        self.statusBar().showMessage(f"Opened AI report: {report_path.name}")

    def _refresh_ai_state(self) -> None:
        ai_results = self._ai_bundle.results_by_path if self._ai_bundle and self._ai_bundle.results_by_path else {}
        self.grid.set_ai_results(ai_results)
        self._start_scope_enrichment_task()
        current_path = self._current_visible_record_path()
        if self._all_records:
            self._apply_records_view(
                current_path=current_path,
                chunked=self._records_view_chunk_active(),
                post_load_enrichment=self._records_view_chunk_post_load_enrichment,
            )
        self._refresh_viewport_mode()
        self._refresh_ai_summary_cache()
        self._update_ai_summary()
        self._update_ai_toolbar_state()
        self._update_status()
        if self.preview.isVisible():
            index = self.grid.current_index()
            if index >= 0:
                self._open_preview(index)

    def _update_ai_toolbar_state(self) -> None:
        current_folder = bool(self._current_folder)
        ai_loaded = self._ai_bundle is not None
        ai_model_ready = self._ai_model_available()
        ai_paths = self._hidden_ai_paths_for_current_folder()
        saved_exists = False
        if self._ui_mode == "ai":
            # Hidden-cache existence checks can hit slow shares; only probe them in AI mode.
            saved_exists = bool(ai_paths and ai_paths.ranked_export_path.exists())
        current_ai = self._ai_result_for_index(self.grid.current_index())
        can_compare_group = bool(current_ai and current_ai.group_size > 1)

        if self.actions is not None:
            can_use_ai_tools = (
                current_folder
                and ai_model_ready
                and self._active_ai_task is None
                and self._active_ai_training_task is None
                and self._active_ai_model_task is None
            )
            self.actions.download_ai_model.setEnabled(self._active_ai_model_task is None)
            self.actions.run_ai_culling.setEnabled(can_use_ai_tools)
            self.actions.load_saved_ai.setEnabled(current_folder and saved_exists and self._active_ai_task is None)
            self.actions.open_ai_report.setEnabled(bool(ai_loaded and self._ai_bundle and self._ai_bundle.report_html_path))
            can_run_training = can_use_ai_tools
            self.actions.open_ai_data_selection.setEnabled(can_run_training)
            self.actions.run_full_ai_training_pipeline.setEnabled(can_run_training)
            self.actions.prepare_ai_training_data.setEnabled(can_run_training)
            self.actions.train_ai_ranker.setEnabled(can_run_training)
            self.actions.manage_ai_rankers.setEnabled(can_run_training)
            self.actions.evaluate_ai_ranker.setEnabled(can_run_training)
            self.actions.score_ai_with_trained_ranker.setEnabled(can_run_training)
            self.actions.build_ai_reference_bank.setEnabled(can_run_training)
            self.actions.clear_ai_trained_model.setEnabled(can_run_training)
            self.actions.next_ai_pick.setEnabled(ai_loaded)
            self.actions.next_unreviewed_ai_pick.setEnabled(ai_loaded)
            self.actions.compare_ai_group.setEnabled(ai_loaded and can_compare_group)
            self.actions.review_ai_disagreements.setEnabled(ai_loaded)
            self.actions.taste_calibration_wizard.setEnabled(current_folder and len(self._all_records) >= 2)
            self.actions.clear_ai_results.setEnabled(ai_loaded)
            if FilterMode.AI_GROUPED in self.actions.filter_actions:
                self.actions.filter_actions[FilterMode.AI_GROUPED].setEnabled(ai_loaded)
            if FilterMode.AI_TOP_PICKS in self.actions.filter_actions:
                self.actions.filter_actions[FilterMode.AI_TOP_PICKS].setEnabled(ai_loaded)
            if FilterMode.AI_DISAGREEMENTS in self.actions.filter_actions:
                self.actions.filter_actions[FilterMode.AI_DISAGREEMENTS].setEnabled(ai_loaded)
        for mode, action in self._ai_state_actions.items():
            action.setEnabled(ai_loaded or mode == AIStateFilter.ALL)

        if self._active_ai_task is not None:
            self.ai_status_label.setText(self._build_ai_progress_text())
        elif self._active_ai_model_task is not None:
            self.ai_status_label.setText("Downloading AI model...")
        elif not ai_model_ready:
            self.ai_status_label.setText("AI model not installed")
        elif ai_loaded and self._ai_bundle is not None:
            export_name = Path(self._ai_bundle.export_csv_path).name
            self.ai_status_label.setText(f"Loaded {export_name}")
        elif saved_exists:
            self.ai_status_label.setText("Saved AI cache available")
        elif not current_folder and self._scope_kind != "folder":
            self.ai_status_label.setText("AI cache stays folder-local in virtual scopes")
        else:
            self.ai_status_label.setText("No AI cache for this folder yet")

        active_checkpoint = self._current_trained_checkpoint_path()
        runtime_lines = [
            f"Python: {self._ai_runtime.python_executable}",
            f"Engine: {self._ai_runtime.engine_root}",
            f"Model: {self._ai_runtime.model_name}",
            f"Model installed: {ai_model_ready}",
            f"Checkpoint: {active_checkpoint or self._ai_runtime.checkpoint_path}",
            f"Local staging: {self._ai_runtime.local_stage_mode}",
        ]
        managed_installation = self._ai_runtime.model_installation
        if managed_installation is not None:
            runtime_lines.append(f"Managed model dir: {managed_installation.install_dir}")
        if self._active_reference_bank_path:
            runtime_lines.append(f"Reference bank: {self._active_reference_bank_path}")
        if self._ai_runtime.local_stage_root is not None:
            runtime_lines.append(f"Stage root: {self._ai_runtime.local_stage_root}")
        if ai_paths is not None:
            runtime_lines.append(f"Hidden cache: {ai_paths.hidden_root}")
        self.ai_status_label.setToolTip("\n".join(runtime_lines))
        self._refresh_ai_progress_bar()
        self._update_action_states()

    def _run_ai_pipeline(self) -> None:
        if not self._current_folder:
            self.statusBar().showMessage("Choose a folder before running AI culling")
            return
        if not self._ensure_ai_model_available(title="Run AI Culling"):
            return
        if self._active_ai_task is not None:
            self.statusBar().showMessage("AI culling is already running for the current folder")
            return

        try:
            paths = build_ai_workflow_paths(self._current_folder)
            training_paths = self._ai_training_paths_for_folder(self._current_folder)
            labels_dir = training_paths.labels_dir if training_paths is not None and training_paths.labels_dir.exists() else None
            reference_bank_path = Path(self._active_reference_bank_path) if self._active_reference_bank_path else None
            runtime = self._ai_runtime
            active_checkpoint = self._current_trained_checkpoint_path()
            if active_checkpoint is not None:
                runtime = replace(runtime, checkpoint_path=active_checkpoint)
            task = AIRunTask(
                folder=Path(self._current_folder),
                runtime=runtime,
                paths=paths,
                labels_dir=labels_dir,
                reference_bank_path=reference_bank_path,
            )
        except Exception as exc:
            QMessageBox.warning(self, "AI Culling", f"Could not prepare the AI run.\n\n{exc}")
            return

        task.signals.started.connect(self._handle_ai_run_started, Qt.ConnectionType.QueuedConnection)
        task.signals.stage.connect(self._handle_ai_run_stage, Qt.ConnectionType.QueuedConnection)
        task.signals.progress.connect(self._handle_ai_run_progress, Qt.ConnectionType.QueuedConnection)
        task.signals.finished.connect(self._handle_ai_run_finished, Qt.ConnectionType.QueuedConnection)
        task.signals.failed.connect(self._handle_ai_run_failed, Qt.ConnectionType.QueuedConnection)
        self._active_ai_task = task
        self._ai_stage_index = 0
        self._ai_stage_total = 3
        self._ai_stage_message = "Queued AI pipeline"
        self._ai_progress_current = 0
        self._ai_progress_total = 0
        self._ai_progress_eta_text = ""
        self._update_ai_toolbar_state()
        self.statusBar().showMessage(f"Queued AI culling for {self._current_folder}")
        self._ai_run_pool.start(task)

    def _handle_ai_run_started(self, folder: str) -> None:
        if normalized_path_key(folder) != normalized_path_key(self._current_folder):
            return
        self._ai_stage_index = 0
        self._ai_stage_total = 3
        self._ai_stage_message = "Preparing AI pipeline"
        self._ai_progress_current = 0
        self._ai_progress_total = 0
        self._ai_progress_eta_text = ""
        self.statusBar().showMessage(f"AI culling started for {folder}")
        self._update_ai_toolbar_state()

    def _handle_ai_run_stage(self, folder: str, stage_index: int, stage_total: int, message: str) -> None:
        if normalized_path_key(folder) != normalized_path_key(self._current_folder):
            return
        self._ai_stage_index = max(0, stage_index)
        self._ai_stage_total = max(1, stage_total)
        self._ai_stage_message = message
        self._ai_progress_current = 0
        self._ai_progress_total = 0
        self._ai_progress_eta_text = ""
        self.ai_status_label.setText(self._build_ai_progress_text())
        self._refresh_ai_progress_bar()
        self.statusBar().showMessage(message)

    def _handle_ai_run_progress(
        self,
        folder: str,
        message: str,
        current: int,
        total: int,
        eta_text: str,
    ) -> None:
        if normalized_path_key(folder) != normalized_path_key(self._current_folder):
            return
        self._ai_stage_message = message
        self._ai_progress_current = max(0, current)
        self._ai_progress_total = max(0, total)
        self._ai_progress_eta_text = eta_text.strip()
        self.ai_status_label.setText(self._build_ai_progress_text())
        self._refresh_ai_progress_bar()

    def _handle_ai_run_finished(self, folder: str, report_dir: str, html_report_path: str) -> None:
        self._active_ai_task = None
        self._ai_stage_index = self._ai_stage_total
        self._ai_stage_message = "AI culling complete"
        if self._ai_progress_total <= 0:
            self._ai_progress_total = 1
        self._ai_progress_current = self._ai_progress_total
        self._ai_progress_eta_text = ""
        same_folder = normalized_path_key(folder) == normalized_path_key(self._current_folder)
        if same_folder:
            self._load_ai_results(report_dir, show_message=False)
            self.mode_tabs.setCurrentIndex(1)
            self.statusBar().showMessage(f"AI culling complete. Loaded {Path(html_report_path).name}")
        else:
            self._update_ai_toolbar_state()
            self.statusBar().showMessage(f"AI culling complete for {folder}")

    def _handle_ai_run_failed(self, folder: str, message: str) -> None:
        self._active_ai_task = None
        self._ai_stage_message = "AI culling failed"
        self._ai_progress_eta_text = ""
        self._update_ai_toolbar_state()
        if normalized_path_key(folder) == normalized_path_key(self._current_folder):
            QMessageBox.warning(self, "AI Culling Failed", message)
            self.statusBar().showMessage("AI culling failed")

    def _refresh_ai_progress_bar(self) -> None:
        if self._active_ai_task is not None:
            if self._ai_progress_total > 0:
                total = max(1, self._ai_progress_total)
                value = min(max(self._ai_progress_current, 0), total)
                self.ai_progress_bar.setRange(0, total)
                self.ai_progress_bar.setValue(value)
                self.ai_progress_bar.setFormat(f"{value}/{total}")
            else:
                self.ai_progress_bar.setRange(0, 0)
                self.ai_progress_bar.setFormat("")
                self.ai_progress_bar.setValue(0)
            self.ai_progress_bar.setToolTip(self._ai_stage_message)
            return

        if self._ai_stage_message == "AI culling complete":
            total = max(1, self._ai_progress_total)
            self.ai_progress_bar.setRange(0, total)
            self.ai_progress_bar.setValue(total)
            self.ai_progress_bar.setFormat("Done")
        elif self._ai_stage_message == "AI culling failed":
            self.ai_progress_bar.setRange(0, 1)
            self.ai_progress_bar.setValue(0)
            self.ai_progress_bar.setFormat("Failed")
        else:
            self.ai_progress_bar.setRange(0, 1)
            self.ai_progress_bar.setValue(0)
            self.ai_progress_bar.setFormat("Idle")
        self.ai_progress_bar.setToolTip(self._ai_stage_message)

    def _build_ai_progress_text(self) -> str:
        if self._active_ai_task is None:
            return self._ai_stage_message

        parts = [self._ai_stage_message]
        if self._ai_progress_total > 0:
            parts.append(f"{self._ai_progress_current}/{self._ai_progress_total}")
        if self._ai_progress_eta_text:
            parts.append(f"{self._ai_progress_eta_text} left")
        return " | ".join(parts)

    def _update_ai_summary(self) -> None:
        self.summary_ai.setText(self._summary_ai_text)
        self.summary_ai.setToolTip(self._summary_ai_tooltip)

    def _refresh_ai_summary_cache(self) -> None:
        if self._ai_bundle is None:
            self._summary_ai_text = "AI: Off"
            self._summary_ai_tooltip = "No AI export is currently loaded."
            return

        total_records = len(self._all_records)
        matched = self._ai_bundle.count_matches(self._all_records)
        source_name = Path(self._ai_bundle.export_csv_path).stem
        if total_records:
            self._summary_ai_text = f"AI: {matched}/{total_records} matched"
        else:
            self._summary_ai_text = f"AI: {source_name}"

        tooltip_lines = [
            f"Source: {self._ai_bundle.source_path}",
            f"Export: {self._ai_bundle.export_csv_path}",
        ]
        bucket_counts = {
            AIConfidenceBucket.OBVIOUS_WINNER: 0,
            AIConfidenceBucket.LIKELY_KEEPER: 0,
            AIConfidenceBucket.NEEDS_REVIEW: 0,
            AIConfidenceBucket.LIKELY_REJECT: 0,
        }
        if self._ai_bundle.results_by_path:
            for result in self._ai_bundle.results_by_path.values():
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
        if self._ai_bundle.report_html_path:
            tooltip_lines.append(f"Report: {self._ai_bundle.report_html_path}")
        self._summary_ai_tooltip = "\n".join(tooltip_lines)

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

    def _update_filter_summary(self) -> None:
        labels = active_filter_labels(self._filter_query)
        preset_label = self._matching_filter_preset_label(self._filter_query)
        metadata_progress = ""
        if self._filter_query.requires_metadata and self._filter_metadata_record_paths:
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

        if self._burst_groups_enabled or self._burst_stacks_enabled:
            burst_group_count = len(self._visible_burst_groups)
            burst_image_count = sum(len(group) for group in self._visible_burst_groups)
            burst_mode_labels: list[str] = []
            if self._burst_groups_enabled:
                burst_mode_labels.append("tags")
            if self._burst_stacks_enabled:
                burst_mode_labels.append("stacks")
            burst_mode_text = ", ".join(burst_mode_labels) if burst_mode_labels else "on"
            group_label = "Smart groups" if self._review_intelligence is not None else "Bursts"
            if burst_group_count:
                burst_summary = f"{group_label} {burst_group_count}"
                tooltip_lines.append(f"{group_label} {burst_mode_text}: {burst_group_count} group(s), {burst_image_count} image(s)")
            else:
                burst_summary = f"{group_label} On"
                tooltip_lines.append(f"{group_label} {burst_mode_text} is on. No related groups are currently visible.")
            summary_text = f"{summary_text} | {burst_summary}"
            visible_total = len(self._records)
            if visible_total and self._review_intelligence is None:
                visible_loaded = sum(1 for record in self._records if record.path in self._filter_metadata_loaded_paths)
                if visible_loaded < visible_total:
                    tooltip_lines.append(f"Burst detection metadata: {visible_loaded}/{visible_total}")
        tooltip_text = "\n".join(tooltip_lines)

        self.filter_summary_label.setText(summary_text)
        self.filter_summary_label.setToolTip(tooltip_text)
        self.clear_filters_button.setVisible(bool(labels))

        advanced_count = self._advanced_filter_count()
        button_text = f"Filters ({advanced_count})" if advanced_count else "Filters"
        button_tooltip = tooltip_text if labels else "Filter by file type, review state, or AI state."
        if self._saved_filter_presets:
            button_tooltip = f"{button_tooltip}\nSaved searches: {len(self._saved_filter_presets)}"
        if preset_label:
            button_tooltip = f"{button_tooltip}\nActive preset: {preset_label}"
        for button in (self.manual_filter_button, self.ai_filter_button):
            button.setText(button_text)
            button.setToolTip(button_tooltip)

    def _advanced_filter_count(self) -> int:
        count = int(self._filter_query.file_type != FileTypeFilter.ALL)
        count += int(self._filter_query.review_state != ReviewStateFilter.ALL)
        count += int(self._filter_query.ai_state != AIStateFilter.ALL)
        count += int(bool(normalize_review_round(self._filter_query.review_round)))
        count += int(bool(self._filter_query.camera_text.strip()))
        count += int(bool(self._filter_query.lens_text.strip()))
        count += int(bool(self._filter_query.tag_text.strip()))
        count += int(self._filter_query.min_rating > 0)
        count += int(self._filter_query.orientation != OrientationFilter.ALL)
        count += int(self._filter_query.captured_after is not None)
        count += int(self._filter_query.captured_before is not None)
        count += int(self._filter_query.iso_min > 0)
        count += int(self._filter_query.iso_max > 0)
        count += int(self._filter_query.focal_min > 0)
        count += int(self._filter_query.focal_max > 0)
        return count

    def _ai_result_for_record(self, record: ImageRecord | None, *, preferred_path: str | None = None):
        if record is None or self._ai_bundle is None:
            return None
        return find_ai_result_for_record(self._ai_bundle, record, preferred_path=preferred_path)

    def _ai_result_for_index(self, index: int):
        record = self._record_at(index)
        if record is None:
            return None
        preferred_path = self.grid.displayed_variant_path(index) if record.has_variant_stack else record.path
        return self._ai_result_for_record(record, preferred_path=preferred_path)

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
        return self._burst_recommendations.get(record.path) or self._burst_recommendations.get(normalized_path_key(record.path))

    def _workflow_insight_for_record(self, record: ImageRecord | None):
        if record is None:
            return None
        return self._workflow_insights_by_path.get(record.path) or self._workflow_insights_by_path.get(normalized_path_key(record.path))

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

    def _load_correction_events_for_current_folder(self) -> None:
        if not self._current_folder:
            if self._scope_kind != "folder" and self._all_records:
                self._correction_events = self._decision_store.load_correction_events(self._session_id)
            else:
                self._correction_events = []
            return
        self._correction_events = self._decision_store.load_correction_events(self._session_id, self._current_folder)

    def _refresh_taste_and_burst_recommendations(self) -> None:
        if not self._all_records:
            self._taste_profile = TasteProfile()
            self._burst_recommendations = {}
            self._workflow_insights_by_path = {}
            return
        taste_profile, recommendations = build_burst_recommendations(
            self._all_records,
            ai_bundle=self._ai_bundle,
            review_bundle=self._review_intelligence,
            correction_events=self._correction_events,
        )
        self._taste_profile = taste_profile
        self._burst_recommendations = recommendations
        self._refresh_workflow_insights_cache(force_full=True)

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
                ai_result = self._ai_result_for_record(record)
                burst_recommendation = self._burst_recommendation_for_record(record)
                workflow = build_record_workflow_insight(
                    annotation,
                    ai_result,
                    burst_recommendation,
                    self._taste_profile,
                )
                insights[record.path] = workflow
                insights[normalized_path_key(record.path)] = workflow
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
                self._workflow_insights_by_path.pop(normalized_path_key(path), None)
                continue
            annotation = self._annotations.get(record.path, SessionAnnotation())
            ai_result = self._ai_result_for_record(record)
            burst_recommendation = self._burst_recommendation_for_record(record)
            workflow = build_record_workflow_insight(
                annotation,
                ai_result,
                burst_recommendation,
                self._taste_profile,
            )
            self._workflow_insights_by_path[record.path] = workflow
            self._workflow_insights_by_path[normalized_path_key(record.path)] = workflow

    def _record_for_path(self, path: str) -> ImageRecord | None:
        direct = self._all_records_by_path.get(path)
        if direct is not None:
            return direct
        normalized = normalized_path_key(path)
        for record_path, record in self._all_records_by_path.items():
            if normalized_path_key(record_path) == normalized:
                return record
        return None

    def _annotation_prefers_frame(self, annotation: SessionAnnotation | None) -> bool:
        if annotation is None:
            return False
        round_value = normalize_review_round(annotation.review_round)
        return annotation.winner or annotation.rating >= 4 or round_value in {REVIEW_ROUND_THIRD_PASS, REVIEW_ROUND_HERO}

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
        preferred_ai = self._ai_result_for_record(preferred_record) if preferred_record is not None else None
        other_ai = self._ai_result_for_record(other_record) if other_record is not None else None
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
            self._invalidate_training_label_counts_cache()
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
        preferred_ai = self._ai_result_for_record(preferred_record) if preferred_record is not None else None
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
        ai_result = self._ai_result_for_record(record)
        burst_recommendation = self._burst_recommendation_for_record(record)
        previous_level = disagreement_level_for(previous_annotation, ai_result)
        new_level = disagreement_level_for(annotation, ai_result)
        if ai_result is not None and (
            new_level
            or previous_annotation.rating != annotation.rating
            or previous_annotation.winner != annotation.winner
            or previous_annotation.reject != annotation.reject
            or normalize_review_round(previous_annotation.review_round) != normalize_review_round(annotation.review_round)
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
            }
            self._record_annotation_feedback_event(record, annotation, ai_result, source_mode=source_mode, payload=payload)

        if self._annotation_prefers_frame(previous_annotation) or not self._annotation_prefers_frame(annotation):
            return
        target_path = self._comparison_target_for_preference(record, ai_result, burst_recommendation)
        if not target_path:
            return
        group_id = ""
        if burst_recommendation is not None:
            group_id = burst_recommendation.group_id
        elif ai_result is not None:
            group_id = ai_result.group_id
        self._record_pairwise_preference(
            left_path=record.path,
            right_path=target_path,
            preferred_path=record.path,
            source_mode=source_mode,
            group_id=group_id,
            extra_payload={"record_path": record.path, "comparison_target": target_path},
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
            ai_result = self._ai_result_for_record(record, preferred_path=preferred_path)
            if ai_result is not None and ai_result.group_size > 1:
                ai_rows_by_id.setdefault(ai_result.group_id, []).append(row_index)
        self._visible_review_group_rows_by_id = review_rows_by_id
        self._visible_ai_group_rows_by_id = ai_rows_by_id

    def _schedule_preview_preload(self, index: int | None = None) -> None:
        if index is None:
            index = self.grid.current_index()
        if index < 0:
            return
        self.preview.preload_paths(self._likely_preview_preload_paths(index))

    def _likely_preview_preload_paths(self, index: int) -> list[str]:
        ordered: list[str] = []
        seen: set[str] = set()

        def add(candidate_index: int) -> None:
            if not 0 <= candidate_index < len(self._records):
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
        for delta in (1, -1, 2, -2, 3, -3):
            add(index + delta)

        current_record = self._record_at(index)
        current_insight = self._review_insight_for_record(current_record)
        if current_insight is not None and current_insight.has_group:
            for row_index in self._visible_review_group_rows_by_id.get(current_insight.group_id, ()):
                add(row_index)

        current_ai = self._ai_result_for_index(index)
        if current_ai is not None and current_ai.group_size > 1:
            for row_index in self._visible_ai_group_rows_by_id.get(current_ai.group_id, ()):
                add(row_index)

        return ordered[:10]

    def _update_inspector_context(self, index: int | None = None) -> None:
        if self.inspector_panel is None:
            return
        if index is None:
            index = self.grid.current_index()

        current_record = self._record_at(index)
        display_path = ""
        annotation = None
        ai_result = None
        review_summary = ""
        workflow_summary = ""
        workflow_details: tuple[str, ...] = ()
        if current_record is not None and index >= 0:
            display_path = self.grid.displayed_variant_path(index) or current_record.path
            annotation = self._annotations.get(current_record.path, SessionAnnotation())
            ai_result = self._ai_result_for_record(current_record, preferred_path=display_path)
            review_summary = self._review_summary_for_record(current_record)
            workflow_summary = self._workflow_summary_for_record(current_record)
            workflow_details = self._workflow_detail_lines_for_record(current_record)

        self.inspector_panel.set_context(
            folder=self._scope_display_label(),
            mode_label="AI Culling" if self._ui_mode == "ai" else "Manual Review",
            selected_count=self.grid.selected_count() if self._records else 0,
            current_record=current_record,
            display_path=display_path,
            annotation=annotation,
            ai_result=ai_result,
            review_summary=review_summary,
            workflow_summary=workflow_summary,
            workflow_details=workflow_details,
        )

    def _is_unreviewed_record(self, record: ImageRecord) -> bool:
        annotation = self._annotations.get(record.path, SessionAnnotation())
        return not annotation.winner and not annotation.reject

    def _find_next_ai_index(self, *, top_pick_only: bool = False, unreviewed_only: bool = False) -> int | None:
        if not self._records:
            return None

        start_index = self.grid.current_index()
        if start_index < 0:
            start_index = -1

        total = len(self._records)
        for offset in range(1, total + 1):
            index = (start_index + offset) % total
            record = self._record_at(index)
            ai_result = self._ai_result_for_index(index)
            if record is None or ai_result is None:
                continue
            if top_pick_only and not ai_result.is_top_pick:
                continue
            if unreviewed_only and not self._is_unreviewed_record(record):
                continue
            return index
        return None

    def _jump_to_next_ai_top_pick(self, *, unreviewed_only: bool = False) -> None:
        if self._ai_bundle is None:
            self.statusBar().showMessage("Load AI results first to jump between AI picks")
            return

        index = self._find_next_ai_index(top_pick_only=True, unreviewed_only=unreviewed_only)
        if index is None:
            if unreviewed_only:
                self.statusBar().showMessage("No unreviewed AI top picks are visible in the current view")
            else:
                self.statusBar().showMessage("No AI top picks are visible in the current view")
            return

        self.grid.set_current_index(index)
        record = self._record_at(index)
        if record is not None:
            label = "unreviewed AI top pick" if unreviewed_only else "AI top pick"
            self.statusBar().showMessage(f"Jumped to {label}: {record.name}")

    def _visible_ai_group_rows(self, group_id: str) -> list[tuple[int, ImageRecord, object]]:
        rows: list[tuple[int, ImageRecord, object]] = []
        for index, record in enumerate(self._records):
            ai_result = self._ai_result_for_index(index)
            if ai_result is None or ai_result.group_size <= 1 or ai_result.group_id != group_id:
                continue
            rows.append((index, record, ai_result))
        rows.sort(key=lambda item: (item[2].rank_in_group, -item[2].score, item[1].name.casefold()))
        return rows

    def _jump_to_ai_top_pick_in_group(self, index: int | None = None) -> None:
        if self._ai_bundle is None:
            self.statusBar().showMessage("Load AI results first to jump within AI groups")
            return

        if index is None:
            index = self.grid.current_index()
        current_ai = self._ai_result_for_index(index)
        if current_ai is None or current_ai.group_size <= 1:
            self.statusBar().showMessage("The current image does not belong to a multi-image AI group")
            return

        group_rows = self._visible_ai_group_rows(current_ai.group_id)
        if not group_rows:
            self.statusBar().showMessage("The current AI group is not visible in this view")
            return

        top_index = group_rows[0][0]
        self.grid.set_current_index(top_index)
        top_record = group_rows[0][1]
        if len(group_rows) < current_ai.group_size:
            self.statusBar().showMessage(
                f"Jumped to AI top pick: {top_record.name} ({len(group_rows)}/{current_ai.group_size} group images visible)"
            )
        else:
            self.statusBar().showMessage(f"Jumped to AI top pick: {top_record.name}")

    def _open_current_ai_group_compare(self, index: int | None = None) -> None:
        if self._ai_bundle is None:
            self.statusBar().showMessage("Load AI results first to compare AI groups")
            return

        if index is None:
            index = self.grid.current_index()
        current_record = self._record_at(index)
        current_ai = self._ai_result_for_index(index)
        if current_record is None or current_ai is None or current_ai.group_size <= 1:
            self.statusBar().showMessage("The current image does not belong to a multi-image AI group")
            return

        group_rows = self._visible_ai_group_rows(current_ai.group_id)
        if len(group_rows) < 2:
            visible_count = len(group_rows)
            if visible_count == 1 and current_ai.group_size > 1:
                self.statusBar().showMessage(
                    f"Only 1/{current_ai.group_size} AI group images are visible. Switch View to All to compare the full group."
                )
            else:
                self.statusBar().showMessage("Not enough AI group images are visible to open compare")
            return

        entries: list[PreviewEntry] = []
        focused_slot = 0
        for slot, (item_index, record, ai_result) in enumerate(group_rows):
            annotation = self._annotations.get(record.path, SessionAnnotation())
            displayed_path = self.grid.displayed_variant_path(item_index) if record.has_variant_stack else self._preview_source_path(record)
            edited_candidates = self._ordered_edited_candidates(record, displayed_path)
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
                    edited_path=edited_path,
                    edited_candidates=tuple(edited_candidates),
                    label=f"AI {label}" if label else "AI",
                    ai_result=ai_result,
                    review_summary=self._review_summary_for_record(record),
                    workflow_summary=self._workflow_summary_for_record(record),
                    workflow_details=self._workflow_detail_lines_for_record(record),
                    placeholder_image=self._preview_placeholder_for_index(item_index),
                )
            )

        self._compare_enabled = True
        if self.actions is not None:
            with QSignalBlocker(self.actions.compare_mode):
                self.actions.compare_mode.setChecked(True)
        self.preview.set_compare_mode(True)
        self._compare_count = len(entries)
        self._manual_compare_count = len(entries)
        self.preview.set_compare_count(len(entries))
        self.preview.show_entries(entries)
        self.preview._set_focused_slot(focused_slot)

        if len(group_rows) < current_ai.group_size:
            self.statusBar().showMessage(
                f"Opened AI group compare ({len(group_rows)}/{current_ai.group_size} visible in current view)"
            )
        else:
            self.statusBar().showMessage(f"Opened AI group compare: {current_ai.group_id}")

    def _update_status(self, index: int | None = None) -> None:
        if index is None:
            index = self.grid.current_index()
        self._update_inspector_context(index)
        self._update_filter_summary()
        scope_label = self._scope_display_label()
        self._update_selection_count_labels()

        if self._records_view_chunk_active():
            total = len(self._records_view_chunk_records)
            loaded = min(len(self._records), total)
            selected_count = self.grid.selected_count() if loaded else 0
            self.summary_total.setText(f"Total: Loading {loaded} / {total}")
            self.summary_selected.setText(f"Selected: {selected_count}")
            self.summary_accepted.setText(f"Accepted: {self._accepted_count}")
            self.summary_rejected.setText(f"Rejected: {self._rejected_count}")
            self.summary_unreviewed.setText(f"Unreviewed: {self._unreviewed_count}")
            self._update_ai_summary()
            self.statusBar().showMessage(f"Loading {loaded} / {total} images from {scope_label}...")
            return

        if self._scan_in_progress and not self._all_records and not self._records:
            self.summary_total.setText("Total: scanning...")
            self.summary_selected.setText("Selected: 0")
            self.summary_accepted.setText("Accepted: 0")
            self.summary_rejected.setText("Rejected: 0")
            self.summary_unreviewed.setText("Unreviewed: ...")
            self._update_ai_summary()
            self.statusBar().showMessage(f"Scanning {scope_label}...")
            return

        count = len(self._records)
        accepted = self._accepted_count
        rejected = self._rejected_count
        remaining = self._unreviewed_count
        selected_count = self.grid.selected_count() if count else 0

        self.summary_total.setText(f"Total: {count}")
        self.summary_selected.setText(f"Selected: {selected_count}")
        self.summary_accepted.setText(f"Accepted: {accepted}")
        self.summary_rejected.setText(f"Rejected: {rejected}")
        self.summary_unreviewed.setText(f"Unreviewed: {remaining}")
        self._update_ai_summary()

        if count == 0:
            self.statusBar().showMessage(f"{scope_label} | 0 images | {remaining} unreviewed")
            return

        selected_indexes = self.grid.selected_indexes()
        if len(selected_indexes) > 1:
            focused = max(0, index) + 1
            self.statusBar().showMessage(
                f"{scope_label} | {count} images | {len(selected_indexes)} selected | focus {focused}/{count} | {remaining} unreviewed"
            )
            return

        selected = max(0, index) + 1
        message = f"{scope_label} | {count} images | {selected}/{count} selected | {remaining} unreviewed"
        record = self._record_at(index)
        preferred_path = self.grid.displayed_variant_path(index) if record and record.has_variant_stack else ""
        ai_result = self._ai_result_for_record(record, preferred_path=preferred_path)
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

    def _show_help_menu(self) -> None:
        if self.actions is None:
            return
        menu = QMenu(self)
        help_action = menu.addAction(self.actions.keyboard_help)
        settings_action = menu.addAction(self.actions.workflow_settings)
        load_ai_action = menu.addAction(self.actions.load_ai_results)
        clear_ai_action = menu.addAction(self.actions.clear_ai_results)
        open_ai_report_action = menu.addAction(self.actions.open_ai_report)
        next_ai_pick_action = menu.addAction(self.actions.next_ai_pick)
        compare_ai_group_action = menu.addAction(self.actions.compare_ai_group)
        current_ai_result = self._ai_result_for_index(self.grid.current_index())
        compare_ai_group_action.setEnabled(bool(current_ai_result and current_ai_result.group_size > 1))
        menu.exec(QCursor.pos())

    def _show_markdown_help_dialog(self, *, title: str, markdown: str) -> None:
        dialog = HelpMarkdownDialog(title=title, markdown=markdown, parent=self)
        self._exec_dialog_with_geometry(dialog, f"help_{title}")

    def _show_help(self) -> None:
        self._show_markdown_help_dialog(
            title="Image Triage Quick Start",
            markdown=dedent(
                """
                # Quick Start

                Use this when you just want to get moving.

                1. **Open a folder** with `File > Open Folder...`.
                2. **Select images** with click, `Ctrl`-click, `Shift`-click, or drag-select.
                3. **Sort fast** with `W` accept, `X` reject, `K` move to `_keep`, `M` move, and `Delete` trash.
                4. **Preview** with `Space` or `Enter`.
                5. **Use right-click or `Tools`** for rename, resize, convert, archive, and batch actions.
                6. **Organize by drag-and-drop** onto folders or favorites. Hold `Ctrl` to copy instead of move.
                7. **Toggle `View > Burst Groups`** to tag likely burst sequences, or **`View > Burst Stacks`** for a stack-style burst navigator in the main viewer.
                8. **Open `Help > AI Guide`** for AI culling and training.

                ## Need More?

                - Open **`Help > AI Guide`** for the full AI workflow.
                - Open **`Help > Advanced Help`** for broader controls and shortcuts.
                """
            ),
        )

    def _show_ai_guide(self) -> None:
        self._show_markdown_help_dialog(
            title="Image Triage AI Guide",
            markdown=dedent(
                """
                # AI Guide

                AI is a core part of Image Triage. The app supports both:

                - **AI culling** for grouping and ranking a folder
                - **AI training** for teaching the model from your own preferences

                The key principle is simple: **AI suggests, you stay in control**.

                ## Model Download

                The installer now opens a first-launch setup step for the local DINOv2 model.

                - Leave the download checkbox on if you want AI culling and AI training available immediately.
                - Turn it off if you only want the core browser for now.
                - If you skip it, use **`AI > Download AI Model...`** later to install the model automatically.

                ## What AI Adds To Review

                When AI results are loaded, the app can show:

                - ranked groups
                - per-image AI scores
                - top-pick hints
                - compare groups inside preview
                - a saved HTML report for the folder

                ## AI Culling Workflow

                Use this when you want the app to score a folder and help you review it faster.

                1. Open the folder you want to review.
                2. Choose **`AI > Run AI Culling`**.
                3. Wait for extraction, grouping, scoring, and report export to finish.
                4. The app will automatically load the new results and switch into **AI Review**.
                5. Use **`Ctrl+Alt+P`** to jump to the next AI top pick.
                6. Use **`Ctrl+Alt+G`** to compare the current AI group.
                7. Later, use **`AI > Load Saved AI For Folder`** if you want to reopen cached results without rerunning the model.

                ## AI Training Workflow

                Use this when you want the model to learn your own culling preferences.

                1. Open the folder you want to train from.
                2. Choose **`AI > Training > Collect Training Labels...`**.
                3. The app opens the labeling workflow and, if needed, quickly builds local label groups from the current folder without running embedding extraction first.
                4. In the labeling app, mark **Best**, **Acceptable**, and **Reject** images in clusters, and pick favorites in pairwise comparisons.
                5. After you have labels, either:
                   - choose **`AI > Training > Run Full Training Pipeline...`** to automatically prepare, train, evaluate, and rescore in one go
                   - or step through the menu manually if you want tighter control
                6. Optional: choose **`AI > Training > Build Reference Bank...`** if you want exemplar-driven scoring from a separate reference folder.
                7. Choose **`AI > Training > Train Ranker...`** when you want a manual training run and give it a run name if you want easier version tracking.
                8. Use **`Stats For Nerds`** during training to watch the live epoch, loss, and validation output.
                9. Choose **`AI > Ranker Center...`** to switch between saved ranker versions and check folder training status.
                10. Choose **`AI > Training > Evaluate Trained Ranker`** to check how well the active checkpoint matches your labels.
                11. Choose **`AI > Training > Score Current Folder With Active Ranker`** to rebuild rankings for the current folder with the selected model.
                12. Review the refreshed result in **AI Review**.
                13. Add more labels and retrain whenever you want to refine the model further.

                ## Training Menu Order

                The training actions are intentionally arranged in usage order:

                1. **Collect Training Labels...**
                2. **Run Full Training Pipeline...**
                3. **Prepare Training Data**
                4. **Build Reference Bank...** (optional)
                5. **Train Ranker...**
                6. **Ranker Center...** (main AI menu item)
                7. **Evaluate Trained Ranker**
                8. **Score Current Folder With Active Ranker**
                9. **Clear Trained Model...**

                ## Where AI Files Live

                Every AI-enabled folder gets a hidden workspace beside the images:

                - **`.image_triage_ai/artifacts`**: embeddings, IDs, and clusters
                - **`.image_triage_ai/labels`**: pairwise and cluster labels
                - **`.image_triage_ai/training`**: versioned ranker runs and the active-ranker pointer
                - **`.image_triage_ai/evaluation`**: evaluation summaries and breakdowns
                - **`.image_triage_ai/ranker_report`**: scored exports and HTML report
                - **`.image_triage_ai/reference_bank`**: optional exemplar reference features

                ## Best Practices

                - Start with folders that match the kind of work you care about most.
                - Label clear winners and clear rejects first.
                - Use the cluster labeling view to sort groups into Best, Acceptable, and Reject before spending time on pairwise edge cases.
                - Treat **Collect Training Labels** as the human-labeling step and **Prepare Training Data** as the later model-prep step.
                - Use run names so it is obvious which checkpoint came from which labeling pass.
                - Keep older rankers around and compare them instead of assuming the newest one is always best.
                - Retrain after a meaningful batch of labels, not after every tiny change.
                - Evaluate a new checkpoint before trusting it broadly.
                - Use a reference bank only when you want the model biased toward a specific style, subject, or look.

                ## Troubleshooting

                - If rankings look stale, run **Score Current Folder With Trained Ranker** again.
                - If the folder changed heavily, collect a fresh round of labels and then rerun **Prepare Training Data** before rescoring.
                - If AI actions are disabled, install the local model from **`AI > Download AI Model...`**.
                - If you only want to review AI results, you do **not** need the training steps.
                - Use **Clear Trained Model...** to reset the current folder back toward the default checkpoint behavior.
                """
            ),
        )

    def _show_advanced_help(self) -> None:
        self._show_markdown_help_dialog(
            title="Image Triage Advanced Help",
            markdown=dedent(
                """
                # Advanced Help

                This is the broader reference for the rest of the app.

                ## Selection

                - `Ctrl`-click adds or removes images
                - `Shift`-click selects a range
                - `Ctrl+A` selects all visible images
                - drag on empty space marquee-selects like File Explorer
                - drag selected thumbnails onto folders or favorites to move them
                - hold `Ctrl` while dragging to copy instead of move
                - **`View > Burst Groups`** highlights likely capture bursts in the main grid as a toggle, not a permanent regrouping
                - **`View > Burst Stacks`** adds stacked burst visuals plus burst cycling in the main viewer with `[` and `]`

                ## Core Review

                - `Space` or `Enter` opens Preview
                - `W` accepts
                - `X` rejects
                - `K` moves to `_keep`
                - `M` moves to a folder
                - `Delete` trashes
                - `Ctrl+Z` undoes the last change
                - `0-5` rates
                - `T` tags
                - `C` toggles compare

                ## Tools

                - Use the **Tools** menu for **Batch Rename**, **Batch Resize**, **Batch Convert**, and archive actions
                - Batch tools use the checkbox mode in the grid
                - Resize and Convert are also available from the image right-click menu
                - RAW files are skipped for Resize and Convert
                - **`Tools > AI Training`** mirrors the step-by-step training actions shown in **`Help > AI Guide`**
                - **`Run Full Training Pipeline...`** is the one-click retrain option after labels already exist
                - **`Ranker Center...`** is a direct item in both **AI** and **Tools** and lets you switch between saved ranker versions and see training status at a glance
                - long AI tasks use centered progress dialogs while scripts are running, and **Stats For Nerds** opens the live training log

                ## Preview

                - mouse wheel or `Z` zooms
                - `0` returns to fit
                - `L` toggles loupe
                - `C` toggles compare
                - `Tab` changes preview focus
                - Left and Right navigate
                - Before/After compares the original with the latest detected edit
                - Open In Photoshop sends the current preview image to Photoshop

                ## Folders And AI

                - right-click folders or favorites to create, rename, move, delete, or favorite folders
                - recent destinations appear in copy and move menus for faster sorting
                - **AI Review** lets you run AI culling or load saved AI results for the current folder
                - **`Help > AI Guide`** is the dedicated walkthrough for the AI side of the app
                - `Ctrl+Alt+P` jumps to the next AI top pick
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
                    "",
                    "A desktop photo triage tool focused on speed, keyboard flow, and AI-assisted review.",
                    "This UI pass adds a command-driven shell foundation with a real menu bar, toolbar, and theme support.",
                ]
            ),
        )

    def _reset_window_layout(self) -> None:
        clear_window_layout(self._settings, self.GEOMETRY_KEY, self.STATE_KEY)
        self.resize(1600, 960)
        self._apply_default_workspace()
        self.statusBar().showMessage("Reset window layout")

    def _show_settings(self) -> None:
        def persist_workflow_presets(presets: tuple[WorkflowPreset, ...]) -> None:
            self._workflow_presets = list(presets)
            self._save_workflow_presets()

        dialog = WorkflowSettingsDialog(
            sessions=self._decision_store.list_sessions(),
            current_session=self._session_id,
            winner_mode=self._winner_mode,
            delete_mode=self._delete_mode,
            catalog_cache_enabled=self._catalog_cache_enabled,
            watch_current_folder=self._watch_current_folder_enabled,
            catalog_summary_text=self._catalog_debug_summary(include_current=True),
            presets=self._workflow_presets,
            preset_save_callback=persist_workflow_presets,
            parent=self,
        )
        if self._exec_dialog_with_geometry(dialog, "workflow_settings") != dialog.DialogCode.Accepted:
            return

        result = dialog.result_settings()
        self._workflow_presets = list(result.presets)
        self._save_workflow_presets()
        new_session = self._decision_store.ensure_session(result.session_id)
        session_changed = new_session != self._session_id
        winner_changed = result.winner_mode != self._winner_mode
        delete_changed = result.delete_mode != self._delete_mode
        catalog_changed = result.catalog_cache_enabled != self._catalog_cache_enabled
        watch_changed = result.watch_current_folder != self._watch_current_folder_enabled

        self._session_id = new_session
        self._winner_mode = result.winner_mode
        self._delete_mode = result.delete_mode
        self._catalog_cache_enabled = result.catalog_cache_enabled
        self._watch_current_folder_enabled = result.watch_current_folder
        self._settings.setValue(self.SESSION_KEY, self._session_id)
        self._settings.setValue(self.WINNER_MODE_KEY, self._winner_mode.value)
        self._settings.setValue(self.DELETE_MODE_KEY, self._delete_mode.value)
        self._settings.setValue(self.CATALOG_CACHE_ENABLED_KEY, self._catalog_cache_enabled)
        self._settings.setValue(self.CATALOG_WATCH_CURRENT_FOLDER_KEY, self._watch_current_folder_enabled)
        self._decision_store.touch_session(self._session_id)
        self.summary_session.setText(f"Session: {self._session_id}")
        self._refresh_current_folder_watch()
        self._refresh_catalog_status_indicator()

        if session_changed:
            self._undo_stack.clear()
            self._update_action_states()
            self._annotations = self._decision_store.load_annotations(self._session_id, self._all_records)
            self._apply_records_view()
            self._start_scope_enrichment_task()

        if winner_changed:
            self.statusBar().showMessage(f"Accepted handling set to {self._winner_mode.value}")
        elif delete_changed:
            self.statusBar().showMessage(f"Delete behavior set to {self._delete_mode.value}")
        elif session_changed:
            self.statusBar().showMessage(f"Switched to session: {self._session_id}")
        elif catalog_changed:
            state = "enabled" if self._catalog_cache_enabled else "disabled"
            self.statusBar().showMessage(f"Catalog cache reads {state}")
        elif watch_changed:
            state = "enabled" if self._watch_current_folder_enabled else "disabled"
            self.statusBar().showMessage(f"Current-folder watch {state}")

    def _empty_recycle_bin(self) -> None:
        recycle_root = self._recycle_root_for_folder()
        if not self._is_temporary_storage_folder():
            self.statusBar().showMessage("Open a removable-drive folder to empty its recycle bin")
            return
        if not recycle_root.exists() or not any(recycle_root.iterdir()):
            self._refresh_recycle_button()
            self.statusBar().showMessage("Recycle bin is already empty")
            return

        confirmation = QMessageBox.warning(
            self,
            "Empty Recycle Bin?",
            (
                "This will permanently delete everything currently stored in this drive's "
                "local recycle bin.\n\nThis action cannot be undone."
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirmation != QMessageBox.StandardButton.Yes:
            return

        shutil.rmtree(recycle_root, ignore_errors=False)
        self._refresh_recycle_button()
        self.statusBar().showMessage(f"Emptied recycle bin for {self._current_folder}")

    def _open_preview_image_in_photoshop(self, path: str) -> None:
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
            "For quicker rating with RAW or large files, consider changing Accepted handling to "
            "'Link To _winners'.\n\n"
            "Navigate to File > Workflow Settings to change it."
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
        ordered: list[str] = []
        seen: set[str] = set()
        for path in (*record.stack_paths, *sidecar_bundle_paths(record)):
            normalized = os.path.normpath(path).casefold()
            if normalized in seen:
                continue
            seen.add(normalized)
            ordered.append(path)
        return tuple(ordered)

    def _remove_record(self, index: int) -> None:
        if not 0 <= index < len(self._records):
            return
        record = self._records[index]
        self._all_records = [item for item in self._all_records if item.path != record.path]
        self._all_records_by_path.pop(record.path, None)
        next_path = self._next_visible_path(index)
        if next_path == record.path:
            next_path = None
        if self._current_folder:
            self._persist_folder_record_cache(self._current_folder, self._all_records, source="window-remove")
        self._apply_records_view(current_path=next_path)

    def _delete_record(self, index: int) -> None:
        record = self._record_at(index)
        if record is None:
            return
        if not self._current_folder:
            self.statusBar().showMessage("Open a real folder to delete files. Virtual scopes are non-destructive views.")
            return

        bundle_paths = self._record_paths(record)
        annotation = self._annotations.get(record.path, SessionAnnotation())
        if self._is_recycle_folder():
            confirmation = QMessageBox.question(
                self,
                "Delete Permanently?",
                f"Permanently delete {record.name} from the recycle bin?\n\nThis cannot be undone.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if confirmation != QMessageBox.StandardButton.Yes:
                return
            try:
                self._delete_paths_permanently(bundle_paths)
            except OSError as exc:
                QMessageBox.warning(self, "Delete Failed", f"Could not permanently delete {record.name}.\n\n{exc}")
                return
            self._forget_recycle_origins(bundle_paths)
            self._decision_store.delete_annotation(self._session_id, record.path)
            self._annotations.pop(record.path, None)
            self._remove_record(index)
            self._refresh_recycle_button()
            self.statusBar().showMessage(f"Permanently deleted {record.name}")
            return

        try:
            trash_moves: tuple[FileMove, ...] = ()
            use_safe_trash = self._delete_mode == DeleteMode.SAFE_TRASH or self._is_temporary_storage_folder()
            if use_safe_trash:
                trash_moves = self._move_bundle_to_recycle(bundle_paths)
                self._remember_recycle_origins(trash_moves)
            else:
                moved_all = self._trash_or_delete_paths(bundle_paths)
                if not moved_all:
                    confirmation = QMessageBox.question(
                        self,
                        "Delete Permanently?",
                        f"Could not move this file set to the trash.\n\nDelete permanently?\n\n{record.name}",
                        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                        QMessageBox.StandardButton.No,
                    )
                    if confirmation != QMessageBox.StandardButton.Yes:
                        return
                    self._delete_paths_permanently(bundle_paths)
        except OSError as exc:
            QMessageBox.warning(self, "Delete Failed", f"Could not delete {record.name}.\n\n{exc}")
            return

        if use_safe_trash:
            self._push_undo(
                UndoAction(
                    kind="delete",
                    primary_path=record.path,
                    file_moves=trash_moves,
                    original_winner=annotation.winner,
                    original_reject=annotation.reject,
                    original_photoshop=annotation.photoshop,
                    rating=annotation.rating,
                    tags=annotation.tags,
                    original_review_round=annotation.review_round,
                    folder=self._current_folder,
                    source_paths=bundle_paths,
                    session_id=self._session_id,
                    winner_mode=self._winner_mode.value,
                )
            )

        self._decision_store.delete_annotation(self._session_id, record.path)
        self._annotations.pop(record.path, None)
        self._remove_record(index)
        self._refresh_recycle_button()
        if use_safe_trash:
            if self._is_temporary_storage_folder():
                self.statusBar().showMessage(f"Moved {record.name} to this drive's recycle bin")
            else:
                self.statusBar().showMessage(f"Safely removed {record.name}")
        else:
            self.statusBar().showMessage(f"Removed {record.name}")

    def _keep_record(self, index: int) -> None:
        record = self._record_at(index)
        if record is None:
            return
        if not self._current_folder:
            self.statusBar().showMessage("Open a real folder to move files. Collections and catalog views do not move originals.")
            return

        keep_dir = os.path.join(self._current_folder, "_keep")
        os.makedirs(keep_dir, exist_ok=True)
        try:
            moves = self._move_bundle(self._record_paths(record), keep_dir)
        except OSError as exc:
            QMessageBox.warning(self, "Move Failed", f"Could not move {record.name}.\n\n{exc}")
            return
        self._rekey_annotation_after_move(record, moves)
        self._push_undo(
            UndoAction(
                kind="move",
                primary_path=record.path,
                file_moves=moves,
                folder=self._current_folder,
                session_id=self._session_id,
            )
        )
        self._remove_record(index)
        self.statusBar().showMessage(f"Moved {record.name} to _keep")

    def _move_record_prompt(self, index: int) -> None:
        record = self._record_at(index)
        if record is None:
            return
        if not self._current_folder:
            self.statusBar().showMessage("Open a real folder to move files. Virtual scopes are browse-only for file moves.")
            return

        destination_dir = QFileDialog.getExistingDirectory(self, "Move Selected Image", self._current_folder or QDir.homePath())
        if not destination_dir:
            return

        try:
            moves = self._move_bundle(self._record_paths(record), destination_dir)
        except OSError as exc:
            QMessageBox.warning(self, "Move Failed", f"Could not move {record.name}.\n\n{exc}")
            return
        self._rekey_annotation_after_move(record, moves)
        self._push_undo(
            UndoAction(
                kind="move",
                primary_path=record.path,
                file_moves=moves,
                folder=self._current_folder,
                session_id=self._session_id,
            )
        )
        self._remember_recent_destination(destination_dir)
        self._remove_record(index)
        self.statusBar().showMessage(f"Moved {record.name} to {destination_dir}")

    def _rate_record(self, index: int, rating: int) -> None:
        record = self._record_at(index)
        if record is None:
            return

        annotation = self._annotations.setdefault(record.path, SessionAnnotation())
        previous_annotation = self._annotation_snapshot(annotation)
        if previous_annotation.rating == rating:
            return
        self._push_undo(
            UndoAction(
                kind="annotation",
                primary_path=record.path,
                original_winner=annotation.winner,
                original_reject=annotation.reject,
                original_photoshop=annotation.photoshop,
                rating=annotation.rating,
                tags=annotation.tags,
                original_review_round=annotation.review_round,
                folder=self._current_folder,
                source_paths=self._record_paths(record),
                session_id=self._session_id,
                winner_mode=self._winner_mode.value,
            )
        )
        annotation.rating = rating
        self._queue_annotation_persist(record, previous_annotation=previous_annotation)
        self._capture_annotation_feedback(record, previous_annotation, annotation, source_mode="rating")
        self._apply_annotation_change_effects([record.path], current_path=record.path)
        self.statusBar().showMessage(f"Rated {record.name}: {rating}/5")

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

    def _annotation_change_affects_active_filter(self) -> bool:
        if bool((self._filter_query.search_text or "").strip()):
            return True
        if self._filter_query.review_state != ReviewStateFilter.ALL:
            return True
        if self._filter_query.quick_filter in {
            FilterMode.WINNERS,
            FilterMode.REJECTS,
            FilterMode.UNREVIEWED,
            FilterMode.AI_DISAGREEMENTS,
            FilterMode.REVIEW_ROUNDS,
        }:
            return True
        if self._filter_query.ai_state == AIStateFilter.DISAGREEMENTS:
            return True
        if bool(normalize_review_round(self._filter_query.review_round)):
            return True
        return False

    def _apply_annotation_change_effects(
        self,
        changed_paths: list[str] | tuple[str, ...] | set[str],
        *,
        current_path: str | None = None,
        counts_already_updated: bool = False,
    ) -> None:
        paths = [path for path in changed_paths if path]
        if not paths:
            return
        self._records_view_cache.mark(ViewInvalidationReason.ANNOTATION_CHANGED, paths=paths)
        if self._annotation_change_affects_active_filter():
            self._apply_records_view(current_path=current_path)
            return
        self._refresh_workflow_insights_cache(changed_paths=set(paths))
        self._set_annotation_views(paths)
        self.grid.update_review_workflow_insights(self._workflow_insights_by_path, paths)
        if not counts_already_updated:
            self._recalculate_review_counts()
        self._update_filter_summary()
        current_change_emitted = False
        if current_path:
            next_index = self._record_index_by_path.get(current_path)
            if next_index is not None:
                self.grid.set_current_index(next_index)
                current_change_emitted = True
        if not current_change_emitted:
            self._update_action_states()
            self._update_status()

    def _toggle_winner(
        self,
        index: int,
        *,
        advance_override: bool | None = None,
        current_path_override: str | None = None,
    ) -> None:
        record = self._record_at(index)
        if record is None:
            return
        if not self._current_folder:
            self.statusBar().showMessage("Winner/reject actions stay folder-first. Open the source folder to change those states.")
            return
        if self._is_recycle_folder():
            self._restore_record(index)
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

        try:
            self._sync_winner_copy(record, annotation.winner, self._current_folder)
        except OSError as exc:
            annotation.winner = previous_winner
            annotation.reject = previous_reject
            self._set_annotation_views()
            QMessageBox.warning(self, "Winner Sync Failed", f"Could not update winner copy for {record.name}.\n\n{exc}")
            return

        self._push_undo(
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
        self._queue_annotation_persist(record, previous_annotation=previous_annotation)
        self._capture_annotation_feedback(record, previous_annotation, annotation, source_mode="winner_toggle")
        self._apply_review_count_delta(previous_annotation, annotation)
        self._apply_annotation_change_effects([record.path], current_path=next_path, counts_already_updated=True)
        if annotation.winner:
            self.statusBar().showMessage(f"Winner added: {record.name}")
        else:
            self.statusBar().showMessage(f"Winner removed: {record.name}")

    def _toggle_reject(
        self,
        index: int,
        *,
        advance_override: bool | None = None,
        current_path_override: str | None = None,
    ) -> None:
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

        try:
            if previous_winner != annotation.winner:
                self._sync_winner_copy(record, annotation.winner, self._current_folder)
        except OSError as exc:
            annotation.winner = previous_winner
            annotation.reject = previous_reject
            self._set_annotation_views()
            QMessageBox.warning(self, "Reject Update Failed", f"Could not update reject state for {record.name}.\n\n{exc}")
            return

        self._push_undo(
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
        self._queue_annotation_persist(record, previous_annotation=previous_annotation)
        self._capture_annotation_feedback(record, previous_annotation, annotation, source_mode="reject_toggle")
        self._apply_review_count_delta(previous_annotation, annotation)
        self._apply_annotation_change_effects([record.path], current_path=next_path, counts_already_updated=True)
        if annotation.reject:
            self.statusBar().showMessage(f"Rejected: {record.name}")
        else:
            self.statusBar().showMessage(f"Reject removed: {record.name}")

    def _open_preview(self, index: int) -> None:
        if self._winner_ladder_state is not None:
            self._finish_winner_ladder(reopen_preview=False, show_message=False)
        entries, effective_count, anchor_index = self._preview_entries_for(index)
        if not entries:
            return

        if self._compare_enabled:
            self._compare_count = effective_count
            self.preview.set_compare_count(effective_count)
            if anchor_index != index:
                self.grid.set_current_index(anchor_index)
        self.preview.set_winner_ladder_mode(False)
        self.preview.show_entries(entries)
        self._schedule_preview_preload(anchor_index if anchor_index >= 0 else index)

    def _navigate_preview(self, delta: int) -> None:
        if not self._records:
            return

        current = self.grid.current_index()
        if current < 0:
            current = 0
        next_index = max(0, min(len(self._records) - 1, current + delta))
        self.grid.set_current_index(next_index)
        self._open_preview(next_index)

    def _preview_source_path(self, record: ImageRecord) -> str:
        for path in record.companion_paths:
            if Path(path).suffix.lower() in JPEG_SUFFIXES:
                return path
        return record.path

    def _preview_entries_for(self, index: int) -> tuple[list[PreviewEntry], int, int]:
        record = self._record_at(index)
        if record is None:
            return [], self._compare_count, index
        annotation = self._annotations.get(record.path, SessionAnnotation())
        displayed_path = self.grid.displayed_variant_path(index) if record.has_variant_stack else self._preview_source_path(record)
        edited_candidates = self._ordered_edited_candidates(record, displayed_path)
        edited_path = edited_candidates[0] if edited_candidates else ""
        if not self._compare_enabled:
            return ([
                PreviewEntry(
                    record=record,
                    source_path=displayed_path,
                    winner=annotation.winner,
                    reject=annotation.reject,
                    edited_path=edited_path,
                    edited_candidates=tuple(edited_candidates),
                    ai_result=self._ai_result_for_record(record, preferred_path=displayed_path),
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
            displayed_path = self.grid.displayed_variant_path(item_index) if record.has_variant_stack else self._preview_source_path(record)
            edited_candidates = self._ordered_edited_candidates(record, displayed_path)
            edited_path = edited_candidates[0] if edited_candidates else ""
            entries.append(
                PreviewEntry(
                    record=record,
                    source_path=displayed_path,
                    winner=annotation.winner,
                    reject=annotation.reject,
                    edited_path=edited_path,
                    edited_candidates=tuple(edited_candidates),
                    ai_result=self._ai_result_for_record(record, preferred_path=displayed_path),
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
            edited_candidates = self._edited_candidates_cache.get(record.path)
            if edited_candidates is None:
                edited_candidates = tuple(discover_edited_paths(record))
                self._edited_candidates_cache[record.path] = edited_candidates
        if displayed_path and displayed_path in edited_candidates:
            return (displayed_path, *[path for path in edited_candidates if path != displayed_path])
        return tuple(edited_candidates)

    def _record_index_for_path(self, path: str) -> int | None:
        return self._record_index_by_path.get(path)

    def _selected_records_for_context(self, index: int) -> list[ImageRecord]:
        selected_indexes = self.grid.selected_indexes()
        if index not in selected_indexes:
            selected_indexes = [index]
        return [self._records[item_index] for item_index in selected_indexes if 0 <= item_index < len(self._records)]

    def _delete_record_by_path(self, path: str) -> bool:
        index = self._record_index_for_path(path)
        if index is None:
            return False
        self._delete_record(index)
        return self._record_index_for_path(path) is None

    def _copy_record_to_path(self, path: str, destination_dir: str) -> bool:
        index = self._record_index_for_path(path)
        if index is None:
            return False
        return self._copy_record_to(index, destination_dir)

    def _keep_record_by_path(self, path: str) -> bool:
        index = self._record_index_for_path(path)
        if index is None:
            return False
        self._keep_record(index)
        return self._record_index_for_path(path) is None

    def _restore_record_by_path(self, path: str) -> bool:
        index = self._record_index_for_path(path)
        if index is None:
            return False
        self._restore_record(index)
        return self._record_index_for_path(path) is None

    def _copy_record_to(self, index: int, destination_dir: str) -> bool:
        record = self._record_at(index)
        if record is None:
            return False
        try:
            self._copy_bundle(self._record_paths(record), destination_dir)
        except OSError as exc:
            QMessageBox.warning(self, "Copy Failed", f"Could not copy {record.name}.\n\n{exc}")
            return False
        self._remember_recent_destination(destination_dir)
        self.statusBar().showMessage(f"Copied {record.name} to {destination_dir}")
        return True

    def _move_record_to(self, index: int, destination_dir: str) -> None:
        record = self._record_at(index)
        if record is None:
            return

        try:
            moves = self._move_bundle(self._record_paths(record), destination_dir)
        except OSError as exc:
            QMessageBox.warning(self, "Move Failed", f"Could not move {record.name}.\n\n{exc}")
            return
        self._rekey_annotation_after_move(record, moves)
        self._push_undo(
            UndoAction(
                kind="move",
                primary_path=record.path,
                file_moves=moves,
                folder=self._current_folder,
                session_id=self._session_id,
            )
        )
        self._remember_recent_destination(destination_dir)
        self._remove_record(index)

    def _restore_record(self, index: int) -> None:
        record = self._record_at(index)
        if record is None:
            return
        if not self._is_recycle_folder():
            return

        try:
            restores = self._restore_bundle(self._record_paths(record))
        except OSError as exc:
            QMessageBox.warning(self, "Restore Failed", f"Could not restore {record.name}.\n\n{exc}")
            return
        if not restores:
            QMessageBox.warning(self, "Restore Failed", f"Could not restore {record.name}.")
            return
        self._remove_record(index)
        self._refresh_recycle_button()
        self.statusBar().showMessage(f"Restored {record.name}")

    def _move_record_to_path(self, path: str, destination_dir: str) -> bool:
        index = self._record_index_for_path(path)
        if index is None:
            return False
        self._move_record_to(index, destination_dir)
        return self._record_index_for_path(path) is None

    def _set_winner_by_path(self, path: str, enabled: bool) -> bool:
        index = self._record_index_for_path(path)
        if index is None:
            return False
        record = self._record_at(index)
        if record is None:
            return False
        current = self._annotations.get(record.path, SessionAnnotation()).winner
        if current == enabled:
            return False
        self._toggle_winner(index, advance_override=False, current_path_override=record.path)
        return True

    def _set_reject_by_path(self, path: str, enabled: bool) -> bool:
        index = self._record_index_for_path(path)
        if index is None:
            return False
        record = self._record_at(index)
        if record is None:
            return False
        current = self._annotations.get(record.path, SessionAnnotation()).reject
        if current == enabled:
            return False
        self._toggle_reject(index, advance_override=False, current_path_override=record.path)
        return True

    def _batch_set_winner(self, records: list[ImageRecord]) -> None:
        if not records:
            return
        if self._is_winners_folder():
            self._batch_delete_records(records)
            return
        candidates = [record for record in records if not self._annotations.get(record.path, SessionAnnotation()).winner]
        if not self._maybe_show_fast_rating_hint(candidates):
            return
        changed, failures = self._batch_apply_annotation_state(records, winner=True, reject=False, source_mode="winner_toggle")
        if failures:
            self.statusBar().showMessage(f"Accepted {changed} image(s); {failures} failed to sync winner artifacts")
            return
        self.statusBar().showMessage(f"Accepted {changed} image(s)")

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
        current_path = self._current_visible_record_path() or records[0].path

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
            try:
                if previous_annotation.winner != annotation.winner:
                    self._sync_winner_copy(record, annotation.winner, self._current_folder)
            except OSError:
                annotation.winner = previous_annotation.winner
                annotation.reject = previous_annotation.reject
                failures += 1
                continue

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
            self._queue_annotation_persist(record, previous_annotation=previous_annotation)
            self._capture_annotation_feedback(record, previous_annotation, annotation, source_mode=source_mode)
            self._apply_review_count_delta(previous_annotation, annotation)
            changed_paths.append(record.path)

        if undo_actions:
            self._push_undo_actions(undo_actions)
        if changed_paths:
            self._apply_annotation_change_effects(changed_paths, current_path=current_path, counts_already_updated=True)
        return len(changed_paths), failures

    def _batch_keep_records(self, records: list[ImageRecord]) -> None:
        if not records:
            return
        moved = sum(1 for record in records if self._keep_record_by_path(record.path))
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
        copied = 0
        for path in primary_paths:
            if self._copy_record_to_path(path, destination_dir):
                copied += 1
        if copied:
            self._remember_recent_destination(destination_dir)
        return copied

    def _move_records_by_paths(self, primary_paths: list[str], destination_dir: str) -> int:
        moved = 0
        for path in primary_paths:
            if self._move_record_to_path(path, destination_dir):
                moved += 1
        if moved:
            self._remember_recent_destination(destination_dir)
        return moved

    def _handle_record_drop(self, primary_paths: list[str], destination_dir: str, *, copy_requested: bool) -> None:
        normalized_destination = normalize_filesystem_path(destination_dir)
        if not normalized_destination or not os.path.isdir(normalized_destination):
            return
        if not self._current_folder or normalized_path_key(normalized_destination) == normalized_path_key(self._current_folder):
            self.statusBar().showMessage("Choose a different folder to drop these images into.")
            return

        unique_paths: list[str] = []
        seen: set[str] = set()
        for path in primary_paths:
            key = normalized_path_key(path)
            if key in seen or self._record_index_for_path(path) is None:
                continue
            seen.add(key)
            unique_paths.append(path)
        if not unique_paths:
            return

        action_label = "Copied" if copy_requested else "Moved"
        if copy_requested:
            count = self._copy_records_by_paths(unique_paths, normalized_destination)
        else:
            count = self._move_records_by_paths(unique_paths, normalized_destination)
        self.statusBar().showMessage(f"{action_label} {count} image(s) to {normalized_destination}")

    def _batch_copy_records(self, records: list[ImageRecord]) -> None:
        if not records:
            return
        destination_dir = QFileDialog.getExistingDirectory(self, "Copy Selected Images", self._current_folder or QDir.homePath())
        if not destination_dir:
            return
        copied = self._copy_records_by_paths(self._primary_paths_for_records(records), destination_dir)
        self.statusBar().showMessage(f"Copied {copied} image(s) to {destination_dir}")

    def _batch_move_records(self, records: list[ImageRecord]) -> None:
        if not records:
            return
        destination_dir = QFileDialog.getExistingDirectory(self, "Move Selected Images", self._current_folder or QDir.homePath())
        if not destination_dir:
            return
        moved = self._move_records_by_paths(self._primary_paths_for_records(records), destination_dir)
        self.statusBar().showMessage(f"Moved {moved} image(s) to {destination_dir}")

    def _batch_delete_records(self, records: list[ImageRecord]) -> None:
        if not records:
            return
        deleted = sum(1 for record in records if self._delete_record_by_path(record.path))
        self.statusBar().showMessage(f"Removed {deleted} image(s)")

    def _batch_restore_records(self, records: list[ImageRecord]) -> None:
        if not records:
            return
        restored = sum(1 for record in records if self._restore_record_by_path(record.path))
        self.statusBar().showMessage(f"Restored {restored} image(s)")

    def _batch_open_in_photoshop(self, records: list[ImageRecord]) -> None:
        if not self._photoshop_executable or not records:
            return
        for record in records:
            open_in_photoshop(record.path)
        self.statusBar().showMessage(f"Opened {len(records)} image(s) in Photoshop")

    def _move_selected_records_to_destination(self, destination_dir: str) -> None:
        records = self._selected_records_for_actions()
        if not records:
            return
        moved = self._move_records_by_paths(self._primary_paths_for_records(records), destination_dir)
        self.statusBar().showMessage(f"Moved {moved} image(s) to {destination_dir}")

    def _copy_selected_records_to_destination(self, destination_dir: str) -> None:
        records = self._selected_records_for_actions()
        if not records:
            return
        copied = self._copy_records_by_paths(self._primary_paths_for_records(records), destination_dir)
        self.statusBar().showMessage(f"Copied {copied} image(s) to {destination_dir}")

    def _batch_move_records_to_new_folder(self, records: list[ImageRecord]) -> None:
        if not records or not self._current_folder:
            return
        destination_dir = self._create_folder_prompt(self._current_folder, select_created=False)
        if not destination_dir:
            return
        moved = self._move_records_by_paths(self._primary_paths_for_records(records), destination_dir)
        self.statusBar().showMessage(f"Moved {moved} image(s) to {destination_dir}")

    def _dispatch_preview_action(self, path: str, handler, *, preserve_anchor: bool = True) -> None:
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

    def _persist_annotation(self, record: ImageRecord, *, session_id: str | None = None) -> None:
        self._records_view_cache.mark(ViewInvalidationReason.ANNOTATION_CHANGED, paths=[record.path])
        target_session_id = session_id or self._session_id
        annotation = self._annotations.get(record.path)
        if annotation is None:
            self._decision_store.delete_annotation(target_session_id, record.path)
        else:
            self._decision_store.save_annotation(target_session_id, record, annotation)
        try:
            sync_sidecar_annotation(record, annotation)
        except OSError as exc:
            self.statusBar().showMessage(f"Saved app state, but could not sync XMP for {record.name}: {exc}")

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

    def _rename_record_prompt(self, index: int) -> str | None:
        record = self._record_at(index)
        if record is None or self._is_recycle_folder() or self._is_winners_folder():
            return None

        requested_name, accepted = QInputDialog.getText(
            self,
            "Rename Image",
            "File name",
            text=record.name,
        )
        if not accepted:
            return None
        requested_name = (requested_name or "").strip()
        if not requested_name:
            return None
        return self._rename_record(index, requested_name)

    def _rename_record(self, index: int, requested_name: str) -> str | None:
        record = self._record_at(index)
        if record is None:
            return None

        try:
            moves = rename_bundle_paths(self._record_paths(record), record.path, requested_name)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Rename Failed", f"Could not rename {record.name}.\n\n{exc}")
            return None
        if not moves:
            return record.path

        self._rekey_annotation_after_move(record, moves)
        self._push_undo(
            UndoAction(
                kind="move",
                primary_path=record.path,
                file_moves=moves,
                folder=self._current_folder,
                session_id=self._session_id,
            )
        )
        renamed_record = self._record_after_moves(record, moves)
        self._replace_record(record.path, renamed_record)
        self._reset_filter_metadata_index(self._all_records)
        self._apply_records_view(current_path=renamed_record.path)
        self.statusBar().showMessage(f"Renamed {record.name} to {renamed_record.name}")
        return renamed_record.path

    def _record_after_moves(self, record: ImageRecord, moves: tuple[FileMove, ...]) -> ImageRecord:
        moved_paths = {move.source_path: move.target_path for move in moves}
        return ImageRecord(
            path=moved_paths.get(record.path, record.path),
            name=Path(moved_paths.get(record.path, record.path)).name,
            size=record.size,
            modified_ns=record.modified_ns,
            companion_paths=tuple(moved_paths.get(path, path) for path in record.companion_paths),
            edited_paths=tuple(moved_paths.get(path, path) for path in record.edited_paths),
            variants=tuple(
                type(variant)(
                    path=moved_paths.get(variant.path, variant.path),
                    name=Path(moved_paths.get(variant.path, variant.path)).name,
                    size=variant.size,
                    modified_ns=variant.modified_ns,
                )
                for variant in record.variants
            ),
        )

    def _replace_record(self, original_path: str, record: ImageRecord) -> None:
        self._all_records = [
            record if existing.path == original_path else existing
            for existing in self._all_records
        ]
        self._all_records_by_path.pop(original_path, None)
        self._all_records_by_path[record.path] = record
        if self._current_folder:
            self._persist_folder_record_cache(self._current_folder, self._all_records, source="window-replace")

    def _replace_records_after_moves(self, records_by_old_path: dict[str, ImageRecord]) -> None:
        if not records_by_old_path:
            return
        self._all_records = [
            records_by_old_path.get(existing.path, existing)
            for existing in self._all_records
        ]
        self._all_records_by_path = {record.path: record for record in self._all_records}
        if self._current_folder:
            self._persist_folder_record_cache(self._current_folder, self._all_records, source="window-move")

    def _rekey_filter_metadata_after_moves(self, records_by_old_path: dict[str, ImageRecord]) -> None:
        if not records_by_old_path:
            return

        updated_metadata: dict[str, CaptureMetadata] = {}
        for old_path, renamed_record in records_by_old_path.items():
            metadata = self._filter_metadata_by_path.pop(old_path, None)
            if metadata is not None:
                updated_metadata[renamed_record.path] = replace(metadata, path=renamed_record.path)
            if old_path in self._filter_metadata_loaded_paths:
                self._filter_metadata_loaded_paths.discard(old_path)
                self._filter_metadata_loaded_paths.add(renamed_record.path)
            if old_path in self._filter_metadata_record_paths:
                self._filter_metadata_record_paths.discard(old_path)
                self._filter_metadata_record_paths.add(renamed_record.path)

        self._filter_metadata_by_path.update(updated_metadata)

    def _rekey_annotation_after_move(
        self,
        record: ImageRecord,
        moves: tuple[FileMove, ...],
        *,
        annotation_override: SessionAnnotation | None = None,
        update_live_cache: bool = True,
    ) -> None:
        annotation = annotation_override
        if annotation is None:
            annotation = self._annotations.pop(record.path, None)
        elif update_live_cache:
            self._annotations.pop(record.path, None)
        if annotation is None:
            return
        if annotation.is_empty:
            self._decision_store.delete_annotation(self._session_id, record.path)
            return
        new_primary_path = next((move.target_path for move in moves if move.source_path == record.path), "")
        if not new_primary_path:
            if update_live_cache:
                self._annotations[record.path] = annotation
            return
        moved_record = self._record_from_path(new_primary_path)
        if moved_record is None:
            if update_live_cache:
                self._annotations[record.path] = annotation
            return
        if update_live_cache:
            self._annotations[new_primary_path] = annotation
        self._decision_store.move_annotation(self._session_id, record.path, moved_record, annotation)

    def _show_grid_context_menu(self, index: int, global_pos) -> None:
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
                accept_action = menu.addAction(f"Accept {len(records)} Images")
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
                self._batch_copy_records(records)
                return
            if chosen in send_to_actions["copy_recent_actions"]:
                self._copy_selected_records_to_destination(send_to_actions["copy_recent_actions"][chosen])
                return
            if chosen == send_to_actions["move_action"]:
                self._batch_move_records(records)
                return
            if chosen == send_to_actions["move_new_folder_action"]:
                self._batch_move_records_to_new_folder(records)
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
                self._batch_delete_records(records)
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
        ai_result = self._ai_result_for_index(index)
        compare_ai_group_action = None
        jump_ai_pick_action = None
        if ai_result is not None and ai_result.group_size > 1:
            menu.addSeparator()
            compare_ai_group_action = menu.addAction(
                self._menu_text_with_action_shortcut("Compare AI Group", self.actions.compare_ai_group if self.actions else None)
            )
            jump_ai_pick_action = menu.addAction(
                self._menu_text_with_action_shortcut("Jump To AI Top Pick", self.actions.next_ai_pick if self.actions else None)
            )
        if self._is_recycle_folder():
            menu.addSeparator()
            restore_action = menu.addAction("Restore")
        else:
            menu.addSeparator()
        rename_action = menu.addAction(
            self._menu_text_with_action_shortcut("Rename...", self.actions.rename_selection if self.actions else None)
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
            self._open_current_ai_group_compare(index)
            return
        if jump_ai_pick_action is not None and chosen == jump_ai_pick_action:
            self._jump_to_ai_top_pick_in_group(index)
            return
        if restore_action is not None and chosen == restore_action:
            self._restore_record(index)
            return
        if chosen == rename_action:
            self._rename_record_prompt(index)
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
                self._copy_record_to(index, destination_dir)
            return
        if chosen in send_to_actions["copy_recent_actions"]:
            self._copy_record_to(index, send_to_actions["copy_recent_actions"][chosen])
            return
        if chosen == send_to_actions["move_action"]:
            self._move_record_prompt(index)
            return
        if chosen == send_to_actions["move_new_folder_action"]:
            self._batch_move_records_to_new_folder(records)
            return
        if chosen in send_to_actions["move_recent_actions"]:
            self._move_record_to(index, send_to_actions["move_recent_actions"][chosen])
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

    def _unique_destination(self, directory: str, filename: str) -> str:
        return unique_destination(directory, filename)

    def _push_undo(self, action: UndoAction) -> None:
        self._undo_stack.append(action)
        self._update_action_states()

    def _push_undo_actions(self, actions: list[UndoAction]) -> None:
        if not actions:
            return
        self._undo_stack.extend(actions)
        self._update_action_states()

    def _undo_last_action(self) -> None:
        if not self._undo_stack:
            return

        action = self._undo_stack.pop()
        if not self._undo_stack:
            self._update_action_states()

        try:
            if action.kind == "annotation":
                self._undo_annotation(action)
            elif action.kind == "move":
                self._undo_move(action)
            elif action.kind == "delete":
                self._undo_delete(action)
        except OSError as exc:
            self._undo_stack.append(action)
            self._update_action_states()
            QMessageBox.warning(self, "Undo Failed", f"Could not undo the last action.\n\n{exc}")
            return

    def _undo_annotation(self, action: UndoAction) -> None:
        annotation = self._annotation_from_action(action)
        if annotation.is_empty:
            self._annotations.pop(action.primary_path, None)
        else:
            self._annotations[action.primary_path] = annotation
        mode_override = None
        for mode in WinnerMode:
            if action.winner_mode in {mode.name, mode.value}:
                mode_override = mode
                break
        self._sync_winner_copy_for_paths(action.source_paths, action.original_winner, action.folder, mode_override=mode_override)
        record = self._all_records_by_path.get(action.primary_path) or self._record_from_path(action.primary_path)
        if record is not None:
            self._queue_annotation_persist(record, session_id=action.session_id or self._session_id)
        self._set_annotation_views()
        self._apply_records_view(current_path=action.primary_path)
        self.statusBar().showMessage(f"Undid annotation change: {Path(action.primary_path).name}")

    def _undo_move(self, action: UndoAction) -> None:
        for file_move in action.file_moves:
            target = Path(file_move.target_path)
            original = Path(file_move.source_path)
            if not target.exists():
                raise OSError(f"Moved file no longer exists: {target}")
            original.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(target), str(original))
        target_primary = next((move.target_path for move in action.file_moves if move.source_path == action.primary_path), "")
        annotation = self._annotations.pop(target_primary, None) if target_primary else None
        if annotation is not None:
            restored_record = self._record_from_path(action.primary_path)
            if restored_record is not None:
                self._annotations[action.primary_path] = annotation
                self._decision_store.move_annotation(action.session_id or self._session_id, target_primary, restored_record, annotation)
        destination_dirs = {str(Path(file_move.target_path).parent) for file_move in action.file_moves}
        if self._current_folder == action.folder or self._current_folder in destination_dirs:
            self._load_folder(self._current_folder)
        self.statusBar().showMessage(f"Undid move: {Path(action.primary_path).name}")

    def _undo_delete(self, action: UndoAction) -> None:
        for file_move in action.file_moves:
            target = Path(file_move.target_path)
            original = Path(file_move.source_path)
            if not target.exists():
                raise OSError(f"Deleted file no longer exists in safe trash: {target}")
            original.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(target), str(original))

        self._forget_recycle_origins(tuple(file_move.target_path for file_move in action.file_moves))
        annotation = self._annotation_from_action(action)
        restored_record = self._record_from_path(action.primary_path)
        if restored_record is not None:
            if annotation.is_empty:
                self._annotations.pop(action.primary_path, None)
            else:
                self._annotations[action.primary_path] = annotation
            self._queue_annotation_persist(restored_record, session_id=action.session_id or self._session_id)

        if self._current_folder == action.folder:
            self._load_folder(self._current_folder)
        else:
            self._set_annotation_views()
            self._update_status()
            self._refresh_recycle_button()
        self.statusBar().showMessage(f"Restored {Path(action.primary_path).name} from safe trash")

    def _annotation_from_action(self, action: UndoAction) -> SessionAnnotation:
        return SessionAnnotation(
            winner=action.original_winner,
            reject=action.original_reject,
            photoshop=action.original_photoshop,
            rating=action.rating,
            tags=action.tags,
            review_round=action.original_review_round,
        )

    def _next_visible_path(self, index: int) -> str | None:
        if not self._records:
            return None
        if index + 1 < len(self._records):
            return self._records[index + 1].path
        if index > 0:
            return self._records[index - 1].path
        return self._records[index].path

    def _apply_records_view_action_mode(self) -> None:
        if self._is_recycle_folder():
            self.grid.set_action_mode("recycle_only")
        elif self._is_winners_folder():
            self.grid.set_action_mode("accepted_only")
        elif self._filter_query.quick_filter == FilterMode.WINNERS:
            self.grid.set_action_mode("accepted_only")
        elif self._filter_query.quick_filter == FilterMode.REJECTS:
            self.grid.set_action_mode("rejected_only")
        else:
            self.grid.set_action_mode("normal")

    def _finalize_records_view_display(
        self,
        *,
        records: list[ImageRecord],
        next_record_paths: tuple[str, ...],
        structural_changed: bool,
        current_path: str | None,
    ) -> None:
        self.grid.set_ai_results(self._ai_bundle.results_by_path if self._ai_bundle and self._ai_bundle.results_by_path else {})
        self.grid.set_review_insights(self._review_intelligence.insights_by_path if self._review_intelligence is not None else {})
        self.grid.set_review_workflow_insights(self._workflow_insights_by_path)
        self._rebuild_visible_preview_group_indexes()
        self._refresh_burst_group_view()
        self._apply_records_view_action_mode()

        restored_current = False
        if current_path:
            index = self._record_index_by_path.get(current_path)
            if index is not None:
                self.grid.set_current_index(index)
                restored_current = True
        if records and not restored_current and structural_changed:
            self.grid.set_current_index(0)
        self._last_view_record_paths = next_record_paths
        self._enqueue_filter_metadata_paths(self._metadata_prefetch_seed_paths(), front=True)
        self._refresh_viewport_mode()
        self._update_action_states()
        self._update_status()
        if self._pending_folder_scroll_value is not None:
            QTimer.singleShot(0, self._restore_pending_folder_scroll)

    def _start_records_view_chunk(
        self,
        *,
        records: list[ImageRecord],
        current_path: str | None,
        post_load_enrichment: str,
    ) -> None:
        self._records_view_chunk_timer.stop()
        self._records_view_chunk_records = records
        self._records_view_chunk_next_index = 0
        self._records_view_chunk_current_path = current_path
        self._records_view_chunk_post_load_enrichment = post_load_enrichment
        self._records = []
        self._record_index_by_path = {}
        self._visible_review_group_rows_by_id = {}
        self._visible_ai_group_rows_by_id = {}
        self._last_view_record_paths = ()
        self.grid.set_items([])
        self._set_annotation_views()
        self.grid.set_ai_results(self._ai_bundle.results_by_path if self._ai_bundle and self._ai_bundle.results_by_path else {})
        self.grid.set_review_insights(self._review_intelligence.insights_by_path if self._review_intelligence is not None else {})
        self.grid.set_review_workflow_insights(self._workflow_insights_by_path)
        self._apply_records_view_action_mode()
        self._update_status()
        self._records_view_chunk_timer.start(0)

    def _drain_records_view_chunk(self) -> None:
        records = self._records_view_chunk_records
        if not records:
            return
        start = self._records_view_chunk_next_index
        batch_size = max(1, self.CHUNKED_RESTORE_LOAD_BATCH_SIZE)
        end = min(len(records), start + batch_size)
        batch = records[start:end]
        if start == 0:
            self._records = list(batch)
            self._record_index_by_path = {record.path: index for index, record in enumerate(self._records)}
            self.grid.set_items(list(self._records))
            self._set_annotation_views()
        else:
            offset = len(self._records)
            self._records.extend(batch)
            for index, record in enumerate(batch, start=offset):
                self._record_index_by_path[record.path] = index
            self.grid.append_items(list(batch))
        self._records_view_chunk_next_index = end
        if end < len(records):
            self._update_status()
            self._records_view_chunk_timer.start(0)
            return

        current_path = self._records_view_chunk_current_path
        post_load_enrichment = self._records_view_chunk_post_load_enrichment
        next_record_paths = tuple(record.path for record in records)
        self._records_view_chunk_records = []
        self._records_view_chunk_next_index = 0
        self._records_view_chunk_current_path = None
        self._records_view_chunk_post_load_enrichment = ""
        self._finalize_records_view_display(
            records=records,
            next_record_paths=next_record_paths,
            structural_changed=True,
            current_path=current_path,
        )
        if post_load_enrichment == "defer":
            self._finish_loaded_records_enrichment(list(self._all_records), defer_enrichment=True)
        elif post_load_enrichment == "start":
            self._finish_loaded_records_enrichment(list(self._all_records), defer_enrichment=False)

    def _apply_records_view(
        self,
        current_path: str | None = None,
        *,
        chunked: bool = False,
        post_load_enrichment: str = "",
    ) -> bool:
        if self._records_view_chunk_active() or not chunked:
            self._cancel_records_view_chunk()
        reasons, dirty_paths = self._records_view_cache.consume()
        force_workflow_rebuild = (
            ViewInvalidationReason.AI_CHANGED in reasons
            and bool(self._all_records)
        )
        if force_workflow_rebuild or dirty_paths:
            self._refresh_workflow_insights_cache(
                changed_paths=set(dirty_paths) if dirty_paths else None,
                force_full=force_workflow_rebuild,
            )

        sorted_records = sort_records(list(self._all_records), self._sort_mode)
        needs_ai = self._filter_query.quick_filter in {FilterMode.AI_TOP_PICKS, FilterMode.AI_GROUPED, FilterMode.AI_DISAGREEMENTS}
        needs_ai = needs_ai or self._filter_query.ai_state != AIStateFilter.ALL
        needs_review = self._filter_query.quick_filter in {FilterMode.SMART_GROUPS, FilterMode.DUPLICATES}
        needs_workflow = self._filter_query.quick_filter in {FilterMode.AI_DISAGREEMENTS, FilterMode.REVIEW_ROUNDS}
        needs_workflow = needs_workflow or self._filter_query.ai_state == AIStateFilter.DISAGREEMENTS
        needs_workflow = needs_workflow or bool(normalize_review_round(self._filter_query.review_round))
        needs_metadata = self._filter_query.requires_metadata
        records: list[ImageRecord] = []
        for record in sorted_records:
            annotation = self._annotations.get(record.path, SessionAnnotation())
            ai_result = self._ai_result_for_record(record) if needs_ai else None
            review_insight = self._review_insight_for_record(record) if needs_review else None
            workflow_insight = self._workflow_insight_for_record(record) if needs_workflow else None
            metadata = self._filter_metadata_by_path.get(record.path, EMPTY_METADATA) if needs_metadata else None
            if matches_record_query(
                record,
                self._filter_query,
                annotation=annotation,
                ai_result=ai_result,
                metadata=metadata,
                review_insight=review_insight,
                workflow_insight=workflow_insight,
            ):
                records.append(record)

        previous_record_paths = self._last_view_record_paths
        next_record_paths = tuple(record.path for record in records)
        structural_changed = previous_record_paths != next_record_paths

        self._records = records
        self._record_index_by_path = {record.path: index for index, record in enumerate(records)}
        self._recalculate_review_counts()
        if reasons.intersection({ViewInvalidationReason.LOAD_CHANGED, ViewInvalidationReason.AI_CHANGED}):
            self._refresh_ai_summary_cache()
        if structural_changed:
            should_chunk = chunked and len(records) >= self.CHUNKED_RESTORE_LOAD_MIN_RECORDS
            if should_chunk:
                self._start_records_view_chunk(
                    records=records,
                    current_path=current_path,
                    post_load_enrichment=post_load_enrichment,
                )
                return False
            self.grid.set_items(records)
            self._set_annotation_views()
        else:
            changed_visible_paths = tuple(path for path in dirty_paths if path in self._record_index_by_path)
            self.grid.update_items(
                GridDeltaUpdate(
                    changed_paths=changed_visible_paths,
                    selection_anchor=self.grid.current_index(),
                    preserve_pixmap_cache=True,
                )
            )
            if changed_visible_paths:
                self._set_annotation_views(changed_visible_paths)
        self._finalize_records_view_display(
            records=records,
            next_record_paths=next_record_paths,
            structural_changed=structural_changed,
            current_path=current_path,
        )
        return True

    def _refresh_burst_group_view(self) -> None:
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
        self._burst_group_map = burst_group_map
        self.grid.set_burst_groups(burst_group_map, burst_groups)
        self.grid.set_burst_stack_mode(self._burst_stacks_enabled)
        self._update_filter_summary()

    def _move_bundle(self, source_paths: tuple[str, ...], destination_dir: str) -> tuple[FileMove, ...]:
        return move_paths(source_paths, destination_dir)

    def _move_bundle_to_recycle(self, source_paths: tuple[str, ...]) -> tuple[FileMove, ...]:
        recycle_root = self._recycle_root_for_folder()
        recycle_root.mkdir(parents=True, exist_ok=True)
        file_moves: list[FileMove] = []
        moved_targets: list[FileMove] = []
        try:
            for source_path in source_paths:
                source = Path(source_path)
                destination = Path(self._unique_destination(str(recycle_root), source.name))
                shutil.move(str(source), str(destination))
                file_move = FileMove(source_path=str(source), target_path=str(destination))
                file_moves.append(file_move)
                moved_targets.append(file_move)
        except OSError as exc:
            for moved in reversed(moved_targets):
                if os.path.exists(moved.target_path):
                    Path(moved.source_path).parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(moved.target_path, moved.source_path)
            raise exc
        return tuple(file_moves)

    def _copy_bundle(self, source_paths: tuple[str, ...], destination_dir: str) -> tuple[FileMove, ...]:
        return copy_paths(source_paths, destination_dir)

    def _safe_trash_directory(self) -> str:
        recycle_root = self._recycle_root_for_folder()
        recycle_root.mkdir(parents=True, exist_ok=True)
        return str(recycle_root)

    def _recycle_manifest_path(self) -> Path:
        return self._recycle_root_for_folder() / ".image-triage-restore.json"

    def _load_recycle_manifest(self) -> dict[str, str]:
        manifest_path = self._recycle_manifest_path()
        if not manifest_path.exists():
            return {}
        try:
            return json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def _save_recycle_manifest(self, data: dict[str, str]) -> None:
        manifest_path = self._recycle_manifest_path()
        if not data:
            if manifest_path.exists():
                manifest_path.unlink()
            return
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")

    def _remember_recycle_origins(self, moves: tuple[FileMove, ...]) -> None:
        if not self._is_recycle_folder() and not self._is_temporary_storage_folder():
            return
        manifest = self._load_recycle_manifest()
        for move in moves:
            manifest[move.target_path] = move.source_path
        self._save_recycle_manifest(manifest)

    def _forget_recycle_origins(self, paths: tuple[str, ...]) -> None:
        manifest = self._load_recycle_manifest()
        changed = False
        for path in paths:
            if path in manifest:
                manifest.pop(path, None)
                changed = True
        if changed:
            self._save_recycle_manifest(manifest)

    def _restore_bundle(self, recycle_paths: tuple[str, ...]) -> tuple[FileMove, ...]:
        manifest = self._load_recycle_manifest()
        restores: list[FileMove] = []
        restored_targets: list[FileMove] = []
        destination_dir: str | None = None
        recycle_root = self._recycle_root_for_folder()
        restore_root = recycle_root.parent
        try:
            for recycle_path in recycle_paths:
                original_path = manifest.get(recycle_path)
                if not original_path:
                    normalized_recycle = os.path.normcase(os.path.normpath(recycle_path))
                    for stored_path, stored_original in manifest.items():
                        if os.path.normcase(os.path.normpath(stored_path)) == normalized_recycle:
                            original_path = stored_original
                            break
                if not original_path:
                    recycle_file = Path(recycle_path)
                    try:
                        relative_path = recycle_file.relative_to(recycle_root)
                        if len(relative_path.parts) > 1:
                            inferred_original = restore_root / relative_path
                            destination = self._unique_destination(str(inferred_original.parent), inferred_original.name)
                        else:
                            if destination_dir is None:
                                destination_dir = QFileDialog.getExistingDirectory(
                                    self,
                                    "Choose Restore Folder",
                                    str(restore_root),
                                )
                                if not destination_dir:
                                    raise OSError("Restore was cancelled.")
                            destination = self._unique_destination(destination_dir, recycle_file.name)
                    except ValueError:
                        if destination_dir is None:
                            destination_dir = QFileDialog.getExistingDirectory(
                                self,
                                "Choose Restore Folder",
                                str(restore_root),
                            )
                            if not destination_dir:
                                raise OSError("Restore was cancelled.")
                        destination = self._unique_destination(destination_dir, Path(recycle_path).name)
                else:
                    destination_dir = str(Path(original_path).parent)
                    destination = self._unique_destination(destination_dir, Path(original_path).name)
                Path(destination).parent.mkdir(parents=True, exist_ok=True)
                shutil.move(recycle_path, destination)
                file_move = FileMove(source_path=destination, target_path=recycle_path)
                restores.append(file_move)
                restored_targets.append(file_move)
        except OSError as exc:
            for restored in reversed(restored_targets):
                if os.path.exists(restored.source_path):
                    Path(restored.target_path).parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(restored.source_path, restored.target_path)
            raise exc
        self._forget_recycle_origins(recycle_paths)
        return tuple(restores)

    def _trash_or_delete_paths(self, source_paths: tuple[str, ...]) -> bool:
        moved_all = True
        for source_path in source_paths:
            if not os.path.exists(source_path):
                continue
            file = QFile(source_path)
            moved = file.moveToTrash() if hasattr(file, "moveToTrash") else False
            moved_all = moved_all and moved
        return moved_all

    def _delete_paths_permanently(self, source_paths: tuple[str, ...]) -> None:
        for source_path in source_paths:
            if os.path.exists(source_path):
                os.remove(source_path)

    def _sync_winner_copy(self, record: ImageRecord, winner_enabled: bool, folder: str) -> None:
        self._sync_winner_copy_for_paths(self._record_paths(record), winner_enabled, folder)

    def _sync_winner_copy_for_paths(
        self,
        source_paths: tuple[str, ...],
        winner_enabled: bool,
        folder: str,
        *,
        mode_override: WinnerMode | None = None,
    ) -> None:
        if self._is_winners_folder(folder):
            return
        winner_mode = mode_override or self._winner_mode
        if winner_mode == WinnerMode.LOGICAL:
            return
        destination_dir = os.path.join(folder, "_winners")
        if winner_enabled:
            os.makedirs(destination_dir, exist_ok=True)
            copied_paths: list[str] = []
            try:
                for source_path in source_paths:
                    destination = os.path.join(destination_dir, Path(source_path).name)
                    if os.path.exists(source_path) and not os.path.exists(destination):
                        self._create_winner_artifact(source_path, destination, winner_mode)
                        copied_paths.append(destination)
            except OSError as exc:
                for copied_path in copied_paths:
                    if os.path.exists(copied_path):
                        os.remove(copied_path)
                raise exc
            return

        for source_path in source_paths:
            destination = os.path.join(destination_dir, Path(source_path).name)
            if os.path.exists(destination):
                os.remove(destination)

    def _create_winner_artifact(self, source_path: str, destination: str, winner_mode: WinnerMode) -> None:
        if winner_mode == WinnerMode.HARDLINK:
            link_error: OSError | None = None
            try:
                os.link(source_path, destination)
                return
            except OSError as exc:
                link_error = exc
            try:
                os.symlink(source_path, destination)
                return
            except OSError as exc:
                raise OSError(
                    f"Could not create a filesystem link for {Path(source_path).name}. "
                    "Use Copy To _winners if this drive does not support links."
                ) from link_error or exc
        shutil.copy2(source_path, destination)
