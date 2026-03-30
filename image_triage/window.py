from __future__ import annotations

import ctypes
import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QByteArray, QDir, QFile, QModelIndex, QSettings, QSignalBlocker, QStandardPaths, Qt, QThreadPool, QTimer
from PySide6.QtGui import QCloseEvent, QCursor, QFont
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFrame,
    QFileDialog,
    QFileSystemModel,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressBar,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QStatusBar,
    QTabBar,
    QToolButton,
    QToolBar,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from .ai_workflow import AIRunTask, build_ai_workflow_paths, default_ai_workflow_runtime, existing_hidden_ai_report_dir
from .ai_results import AIBundle, find_ai_result_for_record, load_ai_bundle
from .brackets import BracketDetector
from .decision_store import DecisionStore
from .grid import ThumbnailGridView
from .models import DeleteMode, FilterMode, ImageRecord, JPEG_SUFFIXES, SessionAnnotation, SortMode, WinnerMode, sort_records
from .preview import FullScreenPreview, PreviewEntry
from .scan_cache import FolderScanCache
from .scanner import FolderScanTask, discover_edited_paths, normalize_filesystem_path, normalized_path_key, scan_folder
from .settings_dialog import WorkflowSettingsDialog
from .shell_actions import detect_photoshop_executable, open_in_file_explorer, open_in_photoshop, open_with_default, open_with_dialog, reveal_in_file_explorer
from .thumbnails import ThumbnailManager
from .ui import (
    AppearanceMode,
    MainWindowActions,
    build_app_palette,
    build_app_stylesheet,
    build_main_menu_bar,
    build_main_window_actions,
    build_primary_toolbar,
    build_undo_icon,
    parse_appearance_mode,
    resolve_theme,
)


@dataclass(slots=True)
class FileMove:
    source_path: str
    target_path: str


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
    folder: str = ""
    source_paths: tuple[str, ...] = ()
    session_id: str = ""
    winner_mode: str = ""


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
    FAVORITES_KEY = "folders/favorites"

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Image Triage")
        self.resize(1600, 960)
        self._settings = QSettings()
        self._appearance_mode = parse_appearance_mode(self._settings.value(self.APPEARANCE_KEY, AppearanceMode.AUTO.value, str))
        self._theme = None
        self.actions: MainWindowActions | None = None
        self.primary_toolbar: QToolBar | None = None

        self.thumbnail_manager = ThumbnailManager()
        self._decision_store = DecisionStore()
        self._bracket_detector = BracketDetector()
        self._photoshop_executable = detect_photoshop_executable()
        self.grid = ThumbnailGridView(self.thumbnail_manager)
        self.preview = FullScreenPreview(self)
        self.preview.navigation_requested.connect(self._navigate_preview)
        self.preview.set_photoshop_available(bool(self._photoshop_executable))
        self._ai_runtime = default_ai_workflow_runtime()
        self._folder_scan_cache = FolderScanCache()

        self._scan_pool = QThreadPool(self)
        self._scan_pool.setMaxThreadCount(1)
        self._ai_run_pool = QThreadPool(self)
        self._ai_run_pool.setMaxThreadCount(1)
        self._scan_token = 0
        self._scan_showed_cached = False
        self._active_scan_tasks: dict[int, FolderScanTask] = {}
        self._active_ai_task: AIRunTask | None = None
        self._current_folder = ""
        self._scan_in_progress = False
        self._all_records: list[ImageRecord] = []
        self._all_records_by_path: dict[str, ImageRecord] = {}
        self._records: list[ImageRecord] = []
        self._record_index_by_path: dict[str, int] = {}
        self._annotations: dict[str, SessionAnnotation] = {}
        self._ai_bundle: AIBundle | None = None
        self._ui_mode = "manual"
        self._ai_stage_index = 0
        self._ai_stage_total = 3
        self._ai_stage_message = "Ready to run AI culling"
        self._ai_progress_current = 0
        self._ai_progress_total = 0
        self._ai_progress_eta_text = ""
        self._sort_mode = SortMode.NAME
        self._filter_mode = FilterMode.ALL
        self._auto_advance_enabled = True
        self._compare_enabled = False
        self._auto_bracket_enabled = self._settings.value(self.AUTO_BRACKET_KEY, True, bool)
        self._session_id = self._decision_store.ensure_session(
            self._settings.value(self.SESSION_KEY, DecisionStore.DEFAULT_SESSION, str)
        )
        self._winner_mode = self._load_winner_mode()
        self._delete_mode = self._load_delete_mode()
        self._favorites = self._load_favorites()
        self._compare_count = 3
        self._manual_compare_count = 3
        self._undo_stack: list[UndoAction] = []

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

        self.favorites_divider = QFrame()
        self.favorites_divider.setFrameShape(QFrame.Shape.HLine)
        self.favorites_divider.setObjectName("sectionDivider")

        self.library_label = QLabel("Library")
        self.library_label.setObjectName("paneTitle")
        library_font = QFont("Segoe UI Variable Display")
        library_font.setPointSize(max(self.font().pointSize() + 2, 12))
        library_font.setWeight(QFont.Weight.DemiBold)
        self.library_label.setFont(library_font)

        self.left_panel = QWidget()
        self.left_panel.setObjectName("libraryPanel")
        left_layout = QVBoxLayout(self.left_panel)
        left_layout.setContentsMargins(10, 10, 10, 10)
        left_layout.setSpacing(10)
        left_layout.addWidget(self.favorites_label)
        left_layout.addWidget(self.favorites_list)
        left_layout.addWidget(self.favorites_divider)
        left_layout.addWidget(self.library_label)
        left_layout.addWidget(self.folder_tree, 1)
        self._refresh_favorites_panel()

        self.manual_path_label = QLabel("No folder selected")
        self.manual_path_label.setObjectName("pathLabel")
        self.manual_path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.ai_path_label = QLabel("No folder selected")
        self.ai_path_label.setObjectName("pathLabel")
        self.ai_path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        self.sort_combo = QComboBox()
        for mode in SortMode:
            self.sort_combo.addItem(mode.value, mode)
        self.sort_combo.currentIndexChanged.connect(self._handle_sort_changed)

        self.filter_combo = QComboBox()
        for mode in FilterMode:
            self.filter_combo.addItem(mode.value, mode)
        self.filter_combo.currentIndexChanged.connect(self._handle_filter_changed)

        self.columns_combo = QComboBox()
        for count in (2, 3, 4):
            self.columns_combo.addItem(f"{count} Across", count)
        self.columns_combo.setCurrentIndex(1)
        self.columns_combo.currentIndexChanged.connect(self._handle_columns_changed)

        self.actions = build_main_window_actions(self)
        build_main_menu_bar(self, self.actions)
        self.primary_toolbar = build_primary_toolbar(self, self.actions)
        self.primary_toolbar.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        self.manual_toolbar = QWidget()
        self.manual_toolbar.setObjectName("workspaceControls")
        manual_toolbar_layout = QHBoxLayout(self.manual_toolbar)
        manual_toolbar_layout.setContentsMargins(0, 0, 0, 0)
        manual_toolbar_layout.setSpacing(8)
        manual_toolbar_layout.addWidget(self._make_action_button(self.actions.compare_mode))
        manual_toolbar_layout.addWidget(self._make_action_button(self.actions.auto_advance))
        manual_toolbar_layout.addSpacing(4)
        manual_toolbar_layout.addWidget(self._build_section_label("Sort"))
        manual_toolbar_layout.addWidget(self.sort_combo)
        manual_toolbar_layout.addWidget(self._build_section_label("View"))
        manual_toolbar_layout.addWidget(self.filter_combo)
        manual_toolbar_layout.addWidget(self._build_section_label("Columns"))
        manual_toolbar_layout.addWidget(self.columns_combo)
        manual_toolbar_layout.addSpacing(8)
        manual_toolbar_layout.addWidget(self._build_section_label("Folder"))
        manual_toolbar_layout.addWidget(self.manual_path_label, 1)
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

        self.ai_toolbar = QWidget()
        self.ai_toolbar.setObjectName("workspaceControls")
        ai_toolbar_layout = QHBoxLayout(self.ai_toolbar)
        ai_toolbar_layout.setContentsMargins(0, 0, 0, 0)
        ai_toolbar_layout.setSpacing(8)
        ai_toolbar_layout.addWidget(self._build_section_label("AI Status"))
        ai_toolbar_layout.addWidget(self.ai_progress_bar)
        ai_toolbar_layout.addWidget(self.ai_status_label)
        ai_toolbar_layout.addSpacing(8)
        ai_toolbar_layout.addWidget(self._build_section_label("Folder"))
        ai_toolbar_layout.addWidget(self.ai_path_label, 1)

        self.mode_tabs = QTabBar()
        self.mode_tabs.setObjectName("modeTabs")
        self.mode_tabs.addTab("Manual Review")
        self.mode_tabs.addTab("AI Culling")
        self.mode_tabs.setExpanding(False)
        self.mode_tabs.setDrawBase(False)
        self.mode_tabs.currentChanged.connect(self._handle_mode_tab_changed)

        self.toolbar_stack = QStackedWidget()
        self.toolbar_stack.addWidget(self.manual_toolbar)
        self.toolbar_stack.addWidget(self.ai_toolbar)

        self.workspace_bar = QWidget()
        self.workspace_bar.setObjectName("workspaceBar")
        workspace_bar_layout = QHBoxLayout(self.workspace_bar)
        workspace_bar_layout.setContentsMargins(12, 8, 12, 8)
        workspace_bar_layout.setSpacing(12)
        workspace_bar_layout.addWidget(self.mode_tabs, 0, Qt.AlignmentFlag.AlignVCenter)
        workspace_bar_layout.addWidget(self.toolbar_stack, 1)

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

        self.main_splitter = QSplitter()
        self.main_splitter.addWidget(self.left_panel)
        self.main_splitter.addWidget(self.grid)
        self.main_splitter.setStretchFactor(0, 0)
        self.main_splitter.setStretchFactor(1, 1)
        self.main_splitter.setSizes([320, 1180])

        container = QWidget()
        container.setObjectName("centralContainer")
        layout = QVBoxLayout(container)
        layout.setContentsMargins(12, 8, 12, 12)
        layout.setSpacing(8)
        layout.addWidget(self.primary_toolbar)
        layout.addWidget(self.workspace_bar)
        layout.addWidget(self.main_splitter, 1)
        self.setCentralWidget(container)
        self.summary_strip.hide()

        status = QStatusBar()
        status.showMessage("Ready")
        self.setStatusBar(status)

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

        app = QApplication.instance()
        if app is not None:
            style_hints = app.styleHints()
            color_scheme_changed = getattr(style_hints, "colorSchemeChanged", None)
            if color_scheme_changed is not None:
                color_scheme_changed.connect(self._handle_system_color_scheme_changed)
        self._apply_appearance()
        self._restore_window_state()
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
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        return button

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
        self._update_dynamic_action_icons()
        self.grid.apply_theme(self._theme)
        self.preview.apply_theme(self._theme)
        self._update_action_states()

    def _update_dynamic_action_icons(self) -> None:
        if self.actions is None or self._theme is None:
            return
        self.actions.undo.setIcon(
            build_undo_icon(
                self._theme.text_secondary.qcolor(),
                disabled_color=self._theme.text_disabled.qcolor(),
                pixel_size=18,
            )
        )

    def _set_appearance_mode(self, mode: AppearanceMode) -> None:
        normalized = mode if isinstance(mode, AppearanceMode) else parse_appearance_mode(mode)
        if self._appearance_mode == normalized:
            return
        self._appearance_mode = normalized
        self._settings.setValue(self.APPEARANCE_KEY, normalized.value)
        self._apply_appearance()
        self.statusBar().showMessage(f"Appearance set to {normalized.value}")

    def _restore_window_state(self) -> None:
        geometry = self._settings.value(self.GEOMETRY_KEY, QByteArray(), QByteArray)
        state = self._settings.value(self.STATE_KEY, QByteArray(), QByteArray)
        if isinstance(geometry, QByteArray) and not geometry.isEmpty():
            self.restoreGeometry(geometry)
        if isinstance(state, QByteArray) and not state.isEmpty():
            self.restoreState(state)

    def _save_window_state(self) -> None:
        self._settings.setValue(self.GEOMETRY_KEY, self.saveGeometry())
        self._settings.setValue(self.STATE_KEY, self.saveState())

    def _finish_startup_restore(self) -> None:
        self._load_start_folder()
        self._restore_ai_results()

    def closeEvent(self, event: QCloseEvent) -> None:
        self._save_window_state()
        super().closeEvent(event)

    def _load_start_folder(self) -> None:
        last_folder = self._settings.value(self.LAST_FOLDER_KEY, "", str)
        if last_folder and os.path.isdir(last_folder):
            self._select_folder(last_folder, sync_tree=False)
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

    def _select_folder(self, folder: str, *, sync_tree: bool = True) -> None:
        if sync_tree:
            index = self.folder_model.index(folder)
            if index.isValid():
                self.folder_tree.setCurrentIndex(index)
        self._load_folder(folder)

    def _handle_tree_selection(self, index) -> None:
        folder = self.folder_model.filePath(index)
        if folder:
            self._load_folder(folder)

    def _handle_favorite_activated(self, item: QListWidgetItem) -> None:
        folder = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(folder, str) and os.path.isdir(folder):
            self._select_folder(folder)

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
        explorer_action = menu.addAction("Open In File Explorer")
        rename_action = menu.addAction("Rename...")
        menu.addSeparator()
        favorite_action = menu.addAction("Remove From Favorites" if is_favorite else "Add To Favorites")

        chosen = menu.exec(global_pos)
        if chosen is None:
            return
        if chosen == open_action:
            self._select_folder(folder)
            return
        if chosen == explorer_action:
            open_in_file_explorer(folder)
            return
        if chosen == rename_action:
            self._rename_folder(folder)
            return
        if chosen == favorite_action:
            if is_favorite:
                self._remove_favorite(folder)
            else:
                self._add_favorite(folder)

    def _rename_folder(self, folder: str) -> None:
        parent = str(Path(folder).parent)
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
        destination = os.path.join(parent, new_name)
        if os.path.exists(destination):
            QMessageBox.warning(self, "Rename Failed", f"A folder named '{new_name}' already exists.")
            return
        try:
            os.rename(folder, destination)
        except OSError as exc:
            QMessageBox.warning(self, "Rename Failed", f"Could not rename folder.\n\n{exc}")
            return
        if folder in self._favorites:
            self._favorites = [destination if path == folder else path for path in self._favorites]
            self._save_favorites()
            self._refresh_favorites_panel()
        if self._current_folder == folder or self._current_folder.startswith(folder + os.sep):
            suffix = self._current_folder[len(folder):]
            self._current_folder = destination + suffix
        self._refresh_folder_tree()
        self._select_folder(destination)
        self.statusBar().showMessage(f"Renamed folder to {new_name}")

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
        combo_index = self.sort_combo.findData(mode)
        if combo_index >= 0 and combo_index != self.sort_combo.currentIndex():
            self.sort_combo.setCurrentIndex(combo_index)
            return
        self._apply_records_view()
        self._scroll_active_view_to_top()
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
        self._filter_mode = mode
        combo_index = self.filter_combo.findData(mode)
        if combo_index >= 0 and combo_index != self.filter_combo.currentIndex():
            self.filter_combo.setCurrentIndex(combo_index)
            return
        self._apply_records_view()
        self._scroll_active_view_to_top()
        self._update_action_states()

    def _handle_filter_changed(self) -> None:
        selected = self._selected_filter_mode()
        if selected is None:
            return
        self._set_filter_mode(selected)

    def _set_column_count(self, count: int) -> None:
        combo_index = self.columns_combo.findData(count)
        if combo_index >= 0 and combo_index != self.columns_combo.currentIndex():
            self.columns_combo.setCurrentIndex(combo_index)
            return
        self.grid.set_column_count(count)
        self._update_action_states()

    def _scroll_active_view_to_top(self) -> None:
        self.grid.verticalScrollBar().setValue(0)

    def _set_annotation_views(self) -> None:
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
        display_path = ""
        if current_record is not None and current_index >= 0:
            display_path = self.grid.displayed_variant_path(current_index) or current_record.path

        self.actions.undo.setEnabled(bool(self._undo_stack))
        with QSignalBlocker(self.actions.compare_mode):
            self.actions.compare_mode.setChecked(self._compare_enabled)
        with QSignalBlocker(self.actions.auto_advance):
            self.actions.auto_advance.setChecked(self._auto_advance_enabled)
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
                action.setChecked(self._filter_mode == mode)

        current_columns = self.columns_combo.currentData()
        if not isinstance(current_columns, int):
            current_columns = 3
        for count, action in self.actions.column_actions.items():
            with QSignalBlocker(action):
                action.setChecked(current_columns == count)

        self.actions.open_preview.setEnabled(current_record is not None)
        self.actions.accept_selection.setEnabled(has_selection and not in_recycle_folder and not in_winners_folder)
        self.actions.reject_selection.setEnabled(has_selection and not in_recycle_folder and not in_winners_folder)
        self.actions.keep_selection.setEnabled(has_selection and not in_recycle_folder and not in_winners_folder)
        self.actions.move_selection.setEnabled(has_selection)
        self.actions.delete_selection.setEnabled(has_selection)
        self.actions.restore_selection.setEnabled(has_selection and in_recycle_folder)
        self.actions.reveal_in_explorer.setEnabled(bool(display_path))
        self.actions.open_in_photoshop.setEnabled(bool(selected_records and self._photoshop_executable))

    def _selected_records_for_actions(self) -> list[ImageRecord]:
        current_index = self.grid.current_index()
        if current_index < 0:
            return []
        return self._selected_records_for_context(current_index)

    def _open_current_preview(self) -> None:
        current_index = self.grid.current_index()
        if current_index >= 0:
            self._open_preview(current_index)

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

    def _load_favorites(self) -> list[str]:
        raw = self._settings.value(self.FAVORITES_KEY, [], list)
        if isinstance(raw, str):
            raw = [raw]
        favorites: list[str] = []
        for path in raw or []:
            if isinstance(path, str) and path and os.path.isdir(path) and path not in favorites:
                favorites.append(path)
        return favorites

    def _save_favorites(self) -> None:
        self._settings.setValue(self.FAVORITES_KEY, self._favorites)

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
        columns = int(self.columns_combo.currentData())
        self._set_column_count(columns)

    def _handle_auto_advance_toggled(self, checked: bool) -> None:
        self._auto_advance_enabled = checked
        self._update_action_states()
        mode = "on" if checked else "off"
        self.statusBar().showMessage(f"Auto-advance {mode}")

    def _handle_compare_toggled(self, checked: bool) -> None:
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

    def _refresh_folder(self) -> None:
        if self._current_folder:
            self._load_folder(self._current_folder, force_refresh=True)

    def _load_folder(self, folder: str, *, force_refresh: bool = False) -> None:
        if not folder:
            return
        folder_changed = normalized_path_key(folder) != normalized_path_key(self._current_folder)
        self._current_folder = folder
        self._settings.setValue(self.LAST_FOLDER_KEY, folder)
        self.manual_path_label.setText(folder)
        self.ai_path_label.setText(folder)
        self._scan_token += 1
        token = self._scan_token
        self._scan_showed_cached = False
        self._scan_in_progress = True
        self._refresh_recycle_button()
        if folder_changed:
            self._clear_ai_results_state(preserve_setting=True)
        cached_records = self._folder_scan_cache.load(normalize_filesystem_path(folder))
        if cached_records:
            self._scan_showed_cached = True
            self.grid.set_empty_message("Choose a folder to start triaging images.")
            self._apply_loaded_records(cached_records)
            if self._ui_mode == "ai":
                self._load_hidden_ai_results_for_current_folder(show_message=False)
            if self._is_slow_source_folder(folder) and not force_refresh:
                # For removable/network sources, cached results are better than a blocking refresh.
                self._scan_in_progress = False
                self._update_status()
                return
            self.statusBar().showMessage(f"Loaded cached folder index for {self._current_folder}, refreshing from disk...")
        else:
            # Never fall back to a synchronous full scan here; it freezes the UI on large or slow folders.
            self.statusBar().showMessage(f"Scanning {folder}...")
            self._all_records = []
            self._all_records_by_path = {}
            self._records = []
            self._record_index_by_path = {}
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
        )
        self._active_scan_tasks[token] = task
        task.signals.cached.connect(self._handle_scan_cached, Qt.ConnectionType.QueuedConnection)
        task.signals.finished.connect(self._handle_scan_finished, Qt.ConnectionType.QueuedConnection)
        task.signals.failed.connect(self._handle_scan_failed, Qt.ConnectionType.QueuedConnection)
        self._scan_pool.start(task)

    def _apply_loaded_records(self, records: list[ImageRecord]) -> None:
        self._all_records = records
        self._all_records_by_path = {record.path: record for record in records}
        persisted = self._decision_store.load_annotations(self._session_id, records)
        for path, annotation in persisted.items():
            self._annotations[path] = annotation
        self._apply_records_view()

    def _handle_scan_cached(self, folder: str, token: int, records: list[ImageRecord]) -> None:
        if token != self._scan_token or normalized_path_key(folder) != normalized_path_key(self._current_folder) or not records:
            return
        self._scan_showed_cached = True
        self.grid.set_empty_message("Choose a folder to start triaging images.")
        self._apply_loaded_records(records)
        if self._ui_mode == "ai":
            self._load_hidden_ai_results_for_current_folder(show_message=False)
        self.statusBar().showMessage(f"Loaded cached folder index for {self._current_folder}, refreshing from disk...")

    def _handle_scan_finished(self, folder: str, token: int, records: list[ImageRecord]) -> None:
        self._active_scan_tasks.pop(token, None)
        if token != self._scan_token or normalized_path_key(folder) != normalized_path_key(self._current_folder):
            return

        self._scan_in_progress = False
        self.grid.set_empty_message("Choose a folder to start triaging images.")
        self._apply_loaded_records(records)
        if self._ui_mode == "ai":
            self._load_hidden_ai_results_for_current_folder(show_message=False)
        if self._scan_showed_cached:
            self.statusBar().showMessage(f"Refreshed {self._current_folder}")

    def _handle_scan_failed(self, folder: str, token: int, message: str) -> None:
        self._active_scan_tasks.pop(token, None)
        if token != self._scan_token or normalized_path_key(folder) != normalized_path_key(self._current_folder):
            return
        self._scan_in_progress = False
        self._all_records = []
        self._all_records_by_path = {}
        self._records = []
        self._record_index_by_path = {}
        self.grid.set_empty_message("Could not scan this folder.")
        self.grid.set_items([])
        self._refresh_recycle_button()
        self._update_action_states()
        self.statusBar().showMessage(f"Could not scan {self._current_folder}: {message}")

    def _handle_current_changed(self, index: int) -> None:
        self._update_action_states()
        self._update_status(index=index)

    def _handle_grid_selection_changed(self) -> None:
        self._update_action_states()
        self._update_status()

    def _show_ai_menu(self) -> None:
        if self.actions is None:
            return
        menu = QMenu(self)
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
        self._refresh_viewport_mode()
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
        ai_paths = self._hidden_ai_paths_for_current_folder()
        saved_exists = False
        if self._ui_mode == "ai":
            # Hidden-cache existence checks can hit slow shares; only probe them in AI mode.
            saved_exists = bool(ai_paths and ai_paths.ranked_export_path.exists())
        current_ai = self._ai_result_for_index(self.grid.current_index())
        can_compare_group = bool(current_ai and current_ai.group_size > 1)

        if self.actions is not None:
            self.actions.run_ai_culling.setEnabled(current_folder and self._active_ai_task is None)
            self.actions.load_saved_ai.setEnabled(current_folder and saved_exists and self._active_ai_task is None)
            self.actions.open_ai_report.setEnabled(bool(ai_loaded and self._ai_bundle and self._ai_bundle.report_html_path))
            self.actions.next_ai_pick.setEnabled(ai_loaded)
            self.actions.next_unreviewed_ai_pick.setEnabled(ai_loaded)
            self.actions.compare_ai_group.setEnabled(ai_loaded and can_compare_group)
            self.actions.clear_ai_results.setEnabled(ai_loaded)
            if FilterMode.AI_GROUPED in self.actions.filter_actions:
                self.actions.filter_actions[FilterMode.AI_GROUPED].setEnabled(ai_loaded)
            if FilterMode.AI_TOP_PICKS in self.actions.filter_actions:
                self.actions.filter_actions[FilterMode.AI_TOP_PICKS].setEnabled(ai_loaded)

        if self._active_ai_task is not None:
            self.ai_status_label.setText(self._build_ai_progress_text())
        elif ai_loaded and self._ai_bundle is not None:
            export_name = Path(self._ai_bundle.export_csv_path).name
            self.ai_status_label.setText(f"Loaded {export_name}")
        elif saved_exists:
            self.ai_status_label.setText("Saved AI cache available")
        else:
            self.ai_status_label.setText("No AI cache for this folder yet")

        runtime_lines = [
            f"Python: {self._ai_runtime.python_executable}",
            f"Engine: {self._ai_runtime.engine_root}",
            f"Checkpoint: {self._ai_runtime.checkpoint_path}",
            f"Local staging: {self._ai_runtime.local_stage_mode}",
        ]
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
        if self._active_ai_task is not None:
            self.statusBar().showMessage("AI culling is already running for the current folder")
            return

        try:
            paths = build_ai_workflow_paths(self._current_folder)
            task = AIRunTask(
                folder=Path(self._current_folder),
                runtime=self._ai_runtime,
                paths=paths,
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
        if self._ai_bundle is None:
            self.summary_ai.setText("AI: Off")
            self.summary_ai.setToolTip("No AI export is currently loaded.")
            return

        total_records = len(self._all_records)
        matched = self._ai_bundle.count_matches(self._all_records)
        source_name = Path(self._ai_bundle.export_csv_path).stem
        if total_records:
            self.summary_ai.setText(f"AI: {matched}/{total_records} matched")
        else:
            self.summary_ai.setText(f"AI: {source_name}")

        tooltip_lines = [
            f"Source: {self._ai_bundle.source_path}",
            f"Export: {self._ai_bundle.export_csv_path}",
        ]
        if self._ai_bundle.report_html_path:
            tooltip_lines.append(f"Report: {self._ai_bundle.report_html_path}")
        self.summary_ai.setToolTip("\n".join(tooltip_lines))

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
        if self._scan_in_progress and not self._all_records and not self._records:
            self.summary_total.setText("Total: scanning...")
            self.summary_selected.setText("Selected: 0")
            self.summary_accepted.setText("Accepted: 0")
            self.summary_rejected.setText("Rejected: 0")
            self.summary_unreviewed.setText("Unreviewed: ...")
            self._update_ai_summary()
            self.statusBar().showMessage(f"Scanning {self._current_folder}...")
            return

        if index is None:
            index = self.grid.current_index()

        count = len(self._records)
        accepted = sum(1 for record in self._all_records if self._annotations.get(record.path, SessionAnnotation()).winner)
        rejected = sum(1 for record in self._all_records if self._annotations.get(record.path, SessionAnnotation()).reject)
        remaining = sum(
            1
            for record in self._all_records
            if not self._annotations.get(record.path, SessionAnnotation()).winner
            and not self._annotations.get(record.path, SessionAnnotation()).reject
        )
        selected_count = self.grid.selected_count() if count else 0

        self.summary_total.setText(f"Total: {count}")
        self.summary_selected.setText(f"Selected: {selected_count}")
        self.summary_accepted.setText(f"Accepted: {accepted}")
        self.summary_rejected.setText(f"Rejected: {rejected}")
        self.summary_unreviewed.setText(f"Unreviewed: {remaining}")
        self._update_ai_summary()

        if count == 0:
            self.statusBar().showMessage(f"{self._current_folder or 'No folder'} | 0 images | {remaining} unreviewed")
            return

        selected_indexes = self.grid.selected_indexes()
        if len(selected_indexes) > 1:
            focused = max(0, index) + 1
            self.statusBar().showMessage(
                f"{self._current_folder} | {count} images | {len(selected_indexes)} selected | focus {focused}/{count} | {remaining} unreviewed"
            )
            return

        selected = max(0, index) + 1
        message = f"{self._current_folder} | {count} images | {selected}/{count} selected | {remaining} unreviewed"
        record = self._record_at(index)
        preferred_path = self.grid.displayed_variant_path(index) if record and record.has_variant_stack else ""
        ai_result = self._ai_result_for_record(record, preferred_path=preferred_path)
        if ai_result is not None:
            ai_parts = [f"AI {ai_result.display_score_text}"]
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

    def _show_help(self) -> None:
        QMessageBox.information(
            self,
            "Image Triage Help",
            "\n".join(
                [
                    "Grid",
                    "Arrow keys navigate",
                    "Ctrl-click and Shift-click multi-select, Ctrl+A selects all visible images",
                    "Summary strip shows total, selected, accepted, rejected, and unreviewed counts",
                    "Space or Enter opens preview",
                    "W accepts, X rejects",
                    "Ctrl+Z undoes the last change",
                    "K moves to _keep, Delete trashes, M moves to a folder",
                    "0-5 rates, T tags, C toggles compare",
                    "Ctrl+Alt+P jumps to the next AI top pick, Ctrl+Alt+G opens the current AI group in compare",
                    "",
                    "Modes",
                    "Manual Review keeps the original browse-and-cull workflow",
                    "AI Culling runs the AI pipeline for the current folder, can stage remote/removable images to a local SSD scratch area first, stores results in a hidden per-folder cache, and auto-loads the ranked export",
                    "",
                    "Preview",
                    "Wheel or Z zooms, 0 returns to fit, L toggles loupe, Alt+L cycles loupe zoom",
                    "Left-drag pans while zoomed",
                    "Open preview auto-refreshes when the source file changes on disk",
                    "Before/After shows the original beside the latest detected edit",
                    "Open In Photoshop sends the focused preview image to Photoshop",
                    "Left/Right navigates, Tab changes compare focus",
                    "Right-click or Space closes preview",
                    "AI Results loads a ranked export so thumbnails and previews can show AI score, group, and top-pick hints",
                    "Use AI Top Picks / AI Grouped in the View filter to focus on AI-suggested comparisons",
                    "AI Culling mode can reformat the main grid into cluster sections with rank badges and normalized 0-100 per-group scores",
                    "The AI Culling toolbar can run the current model end to end, show live image counts and ETA during extraction, and reopen the saved hidden report automatically",
                    "",
                    "Workflow",
                    "RAW+JPG pairs stay together",
                    "Accepted and rejected are mutually exclusive",
                    "Removable drives use a local 'recycle bin' folder beside the working folder so deletes stay recoverable",
                    "Empty Recycle Bin permanently clears that local recycle bin",
                    "Workflow Settings controls the current session, accepted handling, and delete mode",
                    "Right-click any tile for Explorer and app actions",
                ]
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
        self._settings.remove(self.GEOMETRY_KEY)
        self._settings.remove(self.STATE_KEY)
        if self.primary_toolbar is not None:
            self.removeToolBar(self.primary_toolbar)
            self.addToolBar(Qt.ToolBarArea.TopToolBarArea, self.primary_toolbar)
        self.resize(1600, 960)
        self.main_splitter.setSizes([320, 1180])
        self.statusBar().showMessage("Reset window layout")

    def _show_settings(self) -> None:
        dialog = WorkflowSettingsDialog(
            sessions=self._decision_store.list_sessions(),
            current_session=self._session_id,
            winner_mode=self._winner_mode,
            delete_mode=self._delete_mode,
            parent=self,
        )
        if dialog.exec() != dialog.DialogCode.Accepted:
            return

        result = dialog.result_settings()
        new_session = self._decision_store.ensure_session(result.session_id)
        session_changed = new_session != self._session_id
        winner_changed = result.winner_mode != self._winner_mode
        delete_changed = result.delete_mode != self._delete_mode

        self._session_id = new_session
        self._winner_mode = result.winner_mode
        self._delete_mode = result.delete_mode
        self._settings.setValue(self.SESSION_KEY, self._session_id)
        self._settings.setValue(self.WINNER_MODE_KEY, self._winner_mode.value)
        self._settings.setValue(self.DELETE_MODE_KEY, self._delete_mode.value)
        self._decision_store.touch_session(self._session_id)
        self.summary_session.setText(f"Session: {self._session_id}")

        if session_changed:
            self._undo_stack.clear()
            self._update_action_states()
            self._annotations = self._decision_store.load_annotations(self._session_id, self._all_records)
            self._apply_records_view()

        if winner_changed:
            self.statusBar().showMessage(f"Accepted handling set to {self._winner_mode.value}")
        elif delete_changed:
            self.statusBar().showMessage(f"Delete behavior set to {self._delete_mode.value}")
        elif session_changed:
            self.statusBar().showMessage(f"Switched to session: {self._session_id}")

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

    def _record_paths(self, record: ImageRecord) -> tuple[str, ...]:
        return record.stack_paths

    def _remove_record(self, index: int) -> None:
        if not 0 <= index < len(self._records):
            return
        record = self._records[index]
        self._all_records = [item for item in self._all_records if item.path != record.path]
        self._all_records_by_path.pop(record.path, None)
        next_path = self._next_visible_path(index)
        if next_path == record.path:
            next_path = None
        self._apply_records_view(current_path=next_path)

    def _delete_record(self, index: int) -> None:
        record = self._record_at(index)
        if record is None:
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
                    rating=annotation.rating,
                tags=annotation.tags,
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
        if record is None or not self._current_folder:
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
        self._remove_record(index)
        self.statusBar().showMessage(f"Moved {record.name} to {destination_dir}")

    def _rate_record(self, index: int, rating: int) -> None:
        record = self._record_at(index)
        if record is None:
            return

        annotation = self._annotations.setdefault(record.path, SessionAnnotation())
        annotation.rating = rating
        self._persist_annotation(record)
        self._set_annotation_views()
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
        annotation.tags = tags
        self._persist_annotation(record)
        self._set_annotation_views()
        if tags:
            self.statusBar().showMessage(f"Tagged {record.name}: {', '.join(tags)}")
        else:
            self.statusBar().showMessage(f"Cleared tags for {record.name}")

    def _toggle_winner(
        self,
        index: int,
        *,
        advance_override: bool | None = None,
        current_path_override: str | None = None,
    ) -> None:
        record = self._record_at(index)
        if record is None or not self._current_folder:
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
        previous_winner = annotation.winner
        previous_reject = annotation.reject
        previous_photoshop = annotation.photoshop
        annotation.winner = not annotation.winner
        if annotation.winner:
            annotation.reject = False

        self._set_annotation_views()
        self.grid.viewport().repaint()
        QApplication.processEvents()

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
                folder=self._current_folder,
                source_paths=self._record_paths(record),
                session_id=self._session_id,
                winner_mode=self._winner_mode.value,
            )
        )
        self._persist_annotation(record)
        self._apply_records_view(current_path=next_path)
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
        if record is None or not self._current_folder:
            return

        should_advance = self._auto_advance_enabled if advance_override is None else advance_override
        next_path = self._next_visible_path(index) if should_advance else record.path
        if current_path_override is not None:
            next_path = current_path_override

        annotation = self._annotations.setdefault(record.path, SessionAnnotation())
        previous_winner = annotation.winner
        previous_reject = annotation.reject
        previous_photoshop = annotation.photoshop
        annotation.reject = not annotation.reject
        if annotation.reject:
            annotation.winner = False

        self._set_annotation_views()
        self.grid.viewport().repaint()
        QApplication.processEvents()

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
                folder=self._current_folder,
                source_paths=self._record_paths(record),
                session_id=self._session_id,
                winner_mode=self._winner_mode.value,
            )
        )
        self._persist_annotation(record)
        self._apply_records_view(current_path=next_path)
        if annotation.reject:
            self.statusBar().showMessage(f"Rejected: {record.name}")
        else:
            self.statusBar().showMessage(f"Reject removed: {record.name}")

    def _open_preview(self, index: int) -> None:
        entries, effective_count, anchor_index = self._preview_entries_for(index)
        if not entries:
            return

        if self._compare_enabled:
            self._compare_count = effective_count
            self.preview.set_compare_count(effective_count)
            if anchor_index != index:
                self.grid.set_current_index(anchor_index)
        self.preview.show_entries(entries)

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
                )
            )
        return entries, max(1, len(entries)), start

    def _ordered_edited_candidates(self, record: ImageRecord, displayed_path: str) -> tuple[str, ...]:
        edited_candidates = record.edited_paths or discover_edited_paths(record)
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
        changed = sum(1 for record in records if self._set_winner_by_path(record.path, True))
        self.statusBar().showMessage(f"Accepted {changed} image(s)")

    def _batch_set_reject(self, records: list[ImageRecord]) -> None:
        if not records:
            return
        changed = sum(1 for record in records if self._set_reject_by_path(record.path, True))
        self.statusBar().showMessage(f"Rejected {changed} image(s)")

    def _batch_keep_records(self, records: list[ImageRecord]) -> None:
        if not records:
            return
        moved = sum(1 for record in records if self._keep_record_by_path(record.path))
        self.statusBar().showMessage(f"Moved {moved} image(s) to _keep")

    def _batch_copy_records(self, records: list[ImageRecord]) -> None:
        if not records:
            return
        destination_dir = QFileDialog.getExistingDirectory(self, "Copy Selected Images", self._current_folder or QDir.homePath())
        if not destination_dir:
            return
        copied = sum(1 for record in records if self._copy_record_to_path(record.path, destination_dir))
        self.statusBar().showMessage(f"Copied {copied} image(s) to {destination_dir}")

    def _batch_move_records(self, records: list[ImageRecord]) -> None:
        if not records:
            return
        destination_dir = QFileDialog.getExistingDirectory(self, "Move Selected Images", self._current_folder or QDir.homePath())
        if not destination_dir:
            return
        moved = sum(1 for record in records if self._move_record_to_path(record.path, destination_dir))
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

    def _persist_annotation(self, record: ImageRecord) -> None:
        annotation = self._annotations.get(record.path)
        if annotation is None:
            self._decision_store.delete_annotation(self._session_id, record.path)
            return
        self._decision_store.save_annotation(self._session_id, record, annotation)

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

    def _rekey_annotation_after_move(self, record: ImageRecord, moves: tuple[FileMove, ...]) -> None:
        annotation = self._annotations.pop(record.path, None)
        if annotation is None:
            return
        if annotation.is_empty:
            self._decision_store.delete_annotation(self._session_id, record.path)
            return
        new_primary_path = next((move.target_path for move in moves if move.source_path == record.path), "")
        if not new_primary_path:
            self._annotations[record.path] = annotation
            return
        moved_record = self._record_from_path(new_primary_path)
        if moved_record is None:
            self._annotations[record.path] = annotation
            return
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
            if self._is_recycle_folder():
                restore_action = menu.addAction(f"Restore {len(records)} Images")
            else:
                accept_action = menu.addAction(f"Accept {len(records)} Images")
                reject_action = menu.addAction(f"Reject {len(records)} Images")
                keep_action = menu.addAction(f"Move {len(records)} Images To _keep")
            copy_action = menu.addAction(f"Copy {len(records)} Images...")
            move_action = menu.addAction(f"Cut {len(records)} Images...")
            delete_action = menu.addAction(f"Delete {len(records)} Images")
            photoshop_action = menu.addAction(f"Open {len(records)} Images In Photoshop")
            photoshop_action.setEnabled(bool(self._photoshop_executable))
            if not self._photoshop_executable:
                photoshop_action.setText("Open In Photoshop (Not Found)")

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
            if chosen == copy_action:
                self._batch_copy_records(records)
                return
            if chosen == move_action:
                self._batch_move_records(records)
                return
            if chosen == delete_action:
                self._batch_delete_records(records)
                return
            if chosen == photoshop_action:
                self._batch_open_in_photoshop(records)
                return
            return

        record = records[0]
        display_path = self.grid.displayed_variant_path(index) or record.path
        display_name = Path(display_path).name

        menu = QMenu(self)
        restore_action = None
        open_action = menu.addAction("Open")
        ai_result = self._ai_result_for_index(index)
        compare_ai_group_action = None
        jump_ai_pick_action = None
        if ai_result is not None and ai_result.group_size > 1:
            compare_ai_group_action = menu.addAction("Compare AI Group")
            jump_ai_pick_action = menu.addAction("Jump To AI Top Pick")
            menu.addSeparator()
        if self._is_recycle_folder():
            restore_action = menu.addAction("Restore")
        photoshop_action = menu.addAction("Open In Photoshop")
        photoshop_action.setEnabled(bool(self._photoshop_executable))
        if not self._photoshop_executable:
            photoshop_action.setText("Open In Photoshop (Not Found)")
        copy_action = menu.addAction("Copy...")
        cut_action = menu.addAction("Cut...")
        delete_action = menu.addAction("Delete")
        reveal_action = menu.addAction("Reveal In File Explorer")
        copy_path_action = menu.addAction("Copy Path")
        copy_name_action = menu.addAction("Copy Filename")
        menu.addSeparator()
        open_with_menu = menu.addMenu("Open With")
        default_action = open_with_menu.addAction("Default App")
        open_with_action = open_with_menu.addAction("System Open With...")

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
        if chosen == photoshop_action and self._photoshop_executable:
            open_in_photoshop(display_path)
            return
        if chosen == copy_action:
            destination_dir = QFileDialog.getExistingDirectory(self, "Copy Image", self._current_folder or QDir.homePath())
            if destination_dir:
                self._copy_record_to(index, destination_dir)
            return
        if chosen == cut_action:
            self._move_record_prompt(index)
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
        candidate = Path(directory) / filename
        if not candidate.exists():
            return str(candidate)

        stem = candidate.stem
        suffix = candidate.suffix
        counter = 1
        while True:
            alternative = candidate.with_name(f"{stem}_{counter}{suffix}")
            if not alternative.exists():
                return str(alternative)
            counter += 1

    def _push_undo(self, action: UndoAction) -> None:
        self._undo_stack.append(action)
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
        record = self._all_records_by_path.get(action.primary_path)
        if record is not None:
            self._persist_annotation(record)
        elif annotation.is_empty:
            self._decision_store.delete_annotation(action.session_id or self._session_id, action.primary_path)
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
        if not annotation.is_empty and restored_record is not None:
            self._annotations[action.primary_path] = annotation
            self._decision_store.save_annotation(action.session_id or self._session_id, restored_record, annotation)

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
        )

    def _next_visible_path(self, index: int) -> str | None:
        if not self._records:
            return None
        if index + 1 < len(self._records):
            return self._records[index + 1].path
        if index > 0:
            return self._records[index - 1].path
        return self._records[index].path

    def _apply_records_view(self, current_path: str | None = None) -> None:
        records = sort_records(list(self._all_records), self._sort_mode)
        if self._filter_mode == FilterMode.WINNERS:
            records = [record for record in records if self._annotations.get(record.path, SessionAnnotation()).winner]
        elif self._filter_mode == FilterMode.REJECTS:
            records = [record for record in records if self._annotations.get(record.path, SessionAnnotation()).reject]
        elif self._filter_mode == FilterMode.UNREVIEWED:
            records = [
                record
                for record in records
                if not self._annotations.get(record.path, SessionAnnotation()).winner
                and not self._annotations.get(record.path, SessionAnnotation()).reject
            ]
        elif self._filter_mode == FilterMode.EDITED:
            records = [record for record in records if record.has_edits]
        elif self._filter_mode == FilterMode.AI_TOP_PICKS:
            records = [
                record
                for record in records
                if (ai_result := self._ai_result_for_record(record)) is not None and ai_result.is_top_pick
            ]
        elif self._filter_mode == FilterMode.AI_GROUPED:
            records = [
                record
                for record in records
                if (ai_result := self._ai_result_for_record(record)) is not None and ai_result.group_size > 1
            ]

        self._records = records
        self._record_index_by_path = {record.path: index for index, record in enumerate(records)}
        self.grid.set_items(records)
        self._set_annotation_views()
        self.grid.set_ai_results(self._ai_bundle.results_by_path if self._ai_bundle and self._ai_bundle.results_by_path else {})
        if self._is_recycle_folder():
            self.grid.set_action_mode("recycle_only")
        elif self._is_winners_folder():
            self.grid.set_action_mode("accepted_only")
        elif self._filter_mode == FilterMode.WINNERS:
            self.grid.set_action_mode("accepted_only")
        elif self._filter_mode == FilterMode.REJECTS:
            self.grid.set_action_mode("rejected_only")
        else:
            self.grid.set_action_mode("normal")

        if current_path:
            index = self._record_index_by_path.get(current_path)
            if index is not None:
                self.grid.set_current_index(index)
        self._refresh_viewport_mode()
        self._update_action_states()
        self._update_status()

    def _move_bundle(self, source_paths: tuple[str, ...], destination_dir: str) -> tuple[FileMove, ...]:
        file_moves: list[FileMove] = []
        moved_targets: list[FileMove] = []
        try:
            for source_path in source_paths:
                destination = self._unique_destination(destination_dir, Path(source_path).name)
                shutil.move(source_path, destination)
                file_move = FileMove(source_path=source_path, target_path=destination)
                file_moves.append(file_move)
                moved_targets.append(file_move)
        except OSError as exc:
            for moved in reversed(moved_targets):
                if os.path.exists(moved.target_path):
                    Path(moved.source_path).parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(moved.target_path, moved.source_path)
            raise exc
        return tuple(file_moves)

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
        file_copies: list[FileMove] = []
        created_targets: list[FileMove] = []
        try:
            for source_path in source_paths:
                destination = self._unique_destination(destination_dir, Path(source_path).name)
                shutil.copy2(source_path, destination)
                file_copy = FileMove(source_path=source_path, target_path=destination)
                file_copies.append(file_copy)
                created_targets.append(file_copy)
        except OSError as exc:
            for created in reversed(created_targets):
                if os.path.exists(created.target_path):
                    os.remove(created.target_path)
            raise exc
        return tuple(file_copies)

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
            try:
                os.link(source_path, destination)
                return
            except OSError:
                pass
        shutil.copy2(source_path, destination)
