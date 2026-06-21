from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .ui.help_dialog import build_help_button, show_paged_help
from .ui.help_topics import ai_workflow_center_help_pages
from .aiculler_workflow import (
    aiculler_db_path,
    aiculler_rerank_readiness,
    aiculler_runtime_status,
    build_aiculler_workflow_paths,
    clip_model_variant_info,
    global_aiculler_db_path,
    list_adapter_model_summaries,
    load_adapter_status_summary,
)
from .aiculler_global_store import GlobalAdapterLabelStore, default_global_adapter_label_store_path
from .dino_prefilter import (
    build_dino_prefilter_paths,
    default_dino_prefilter_settings,
    dino_prefilter_mode_label,
)
from .phash_prefilter import build_phash_prefilter_paths

if TYPE_CHECKING:
    from .window import MainWindow


STATUS_DONE = "done"
STATUS_READY = "ready"
STATUS_BLOCKED = "blocked"

_STATUS_LABELS = {
    STATUS_DONE: "Done",
    STATUS_READY: "Ready",
    STATUS_BLOCKED: "Blocked",
}

_STATUS_COLORS = {
    STATUS_DONE: ("#1f6f3a", "#bff1c9"),
    STATUS_READY: ("#214f7e", "#bcd6f4"),
    STATUS_BLOCKED: ("#5a2a2a", "#f1c4c4"),
}


def _display_adapter_version(model_version: str, *, compact: bool = False) -> str:
    text = str(model_version or "").strip()
    if not text:
        return "unknown"
    prefix = ""
    timestamp = text
    for candidate in ("Global Adapter ", "Adapter "):
        if text.startswith(candidate):
            prefix = candidate.strip()
            timestamp = text[len(candidate):]
            break
    parsed = _parse_adapter_timestamp(timestamp)
    if parsed:
        date_text, time_text = parsed
        if compact:
            return f"{prefix + ' ' if prefix else ''}{date_text} {time_text[:5]}"
        return f"{prefix + ' ' if prefix else ''}{date_text} {time_text}"
    return text


def _parse_adapter_timestamp(value: str) -> tuple[str, str] | None:
    text = value.strip()
    if len(text) == 15 and text[8] == "T" and text[:8].isdigit() and text[9:].isdigit():
        return f"{text[0:4]}-{text[4:6]}-{text[6:8]}", f"{text[9:11]}:{text[11:13]}:{text[13:15]}"
    if len(text) == 19 and text[4] == "-" and text[7] == "-" and text[10] == " ":
        return text[:10], text[11:].replace(".", ":")
    return None


def _int_value(value: object, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _sorted_count_pairs(value: object) -> tuple[tuple[str, int], ...]:
    if not isinstance(value, dict):
        return ()
    pairs: list[tuple[str, int]] = []
    for key, count in value.items():
        label = str(key or "").strip()
        if not label:
            continue
        pairs.append((label, _int_value(count)))
    return tuple(sorted(pairs, key=lambda item: (-item[1], item[0])))


def _format_count_pairs(pairs: tuple[tuple[str, int], ...]) -> str:
    if not pairs:
        return "—"
    return ", ".join(f"{label.replace('_', ' ')}: {count}" for label, count in pairs)


def _compact_metric_value(label: str, value: str) -> str:
    text = str(value or "")
    if label not in {"Culler source", "Model root", "Current folder"}:
        return text
    normalized = text.replace("\\", "/")
    if len(normalized) <= 44:
        return normalized
    parts = [part for part in normalized.split("/") if part]
    if len(parts) >= 3:
        prefix = normalized[:2] if len(normalized) >= 2 and normalized[1] == ":" else ""
        suffix = "/".join(parts[-3:])
        compact = f"{prefix}/.../{suffix}" if prefix else f".../{suffix}"
        return compact if len(compact) <= 52 else f".../{suffix[-48:]}"
    return f"...{normalized[-48:]}"


@dataclass
class WorkflowSnapshot:
    runtime_ready: bool
    runtime_source: str
    model_root: str
    runtime_note: str
    clip_model_label: str
    folder_open: bool
    db_exists: bool
    indexed_count: int
    cluster_run_id: str
    can_rerank: bool
    label_count: int           # ratings already imported into the CLI-Culler DB
    pending_label_count: int   # saved folder labels available for adapter training
    global_label_count: int
    global_label_values: int
    global_matching_label_count: int
    global_matching_label_values: int
    global_matching_dispute_count: int
    adapter_version: str
    adapter_created_at: str
    train_mae: float | None
    holdout_mae: float | None
    train_rank_lift: float | None
    scored_count: int
    adapter_models: tuple[dict[str, object], ...] = ()
    global_adapter_models: tuple[dict[str, object], ...] = ()
    global_adapter_version: str = ""
    folder_path: str = ""
    file_count: int = 0
    dino_enabled: bool = False
    dino_mode_label: str = "Soft Quarantine"
    dino_aggressiveness_percent: int = 85
    dino_phash_duplicate_enabled: bool = True
    dino_phash_hamming_threshold: int = 6
    dino_diagnostics_enabled: bool = True
    dino_report_exists: bool = False
    dino_rows_exists: bool = False
    dino_report_created_at: str = ""
    dino_model_policy: str = "base_model_only"
    dino_scanned_count: int = 0
    dino_quarantined_count: int = 0
    dino_removed_from_pool_count: int = 0
    dino_rescued_count: int = 0
    dino_cache_hit: bool = False
    dino_reason_counts: tuple[tuple[str, int], ...] = ()
    dino_rescue_counts: tuple[tuple[str, int], ...] = ()
    dino_artifact_dir: str = ""
    phash_report_exists: bool = False
    phash_artifact_dir: str = ""

    @property
    def total_label_count(self) -> int:
        return self.label_count + self.pending_label_count

    @property
    def can_train_from_global_labels(self) -> bool:
        return self.global_matching_label_count >= 2 and self.global_matching_label_values >= 2

    @property
    def can_train_global_adapter(self) -> bool:
        return self.global_label_count >= 2 and self.global_label_values >= 2

    @property
    def has_trainable_labels(self) -> bool:
        return self.total_label_count > 0 or self.can_train_from_global_labels


@dataclass
class ActionSpec:
    label: str
    callback: Callable[[], None]
    primary: bool = False
    enabled: bool = True
    tooltip: str = ""


@dataclass
class StepSpec:
    key: str
    title: str
    subtitle: str
    description: str
    status: str
    metrics: list[tuple[str, str]] = field(default_factory=list)
    actions: list[ActionSpec] = field(default_factory=list)


class _StepPage(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 24, 28, 24)
        outer.setSpacing(14)

        header = QHBoxLayout()
        header.setSpacing(10)
        self._title_label = QLabel()
        title_font = QFont()
        title_font.setPointSize(16)
        title_font.setBold(True)
        self._title_label.setFont(title_font)
        self._title_label.setWordWrap(True)
        self._title_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        header.addWidget(self._title_label, 1)
        self._status_pill = QLabel()
        self._status_pill.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._status_pill.setFixedHeight(22)
        self._status_pill.setMinimumWidth(72)
        header.addWidget(self._status_pill, 0)
        outer.addLayout(header)

        self._subtitle_label = QLabel()
        self._subtitle_label.setStyleSheet("color: #9aa7b8;")
        self._subtitle_label.setWordWrap(True)
        self._subtitle_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self._subtitle_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        outer.addWidget(self._subtitle_label)

        self._description_label = QLabel()
        self._description_label.setWordWrap(True)
        self._description_label.setStyleSheet("color: #d4dbe4;")
        self._description_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self._description_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        outer.addWidget(self._description_label)

        self._metrics_frame = QFrame()
        self._metrics_frame.setObjectName("workflowMetricsFrame")
        self._metrics_frame.setStyleSheet(
            "QFrame#workflowMetricsFrame {"
            " background: rgba(255, 255, 255, 0.04);"
            " border: 1px solid rgba(255, 255, 255, 0.06);"
            " border-radius: 6px;"
            "}"
        )
        self._metrics_frame.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._metrics_layout = QGridLayout(self._metrics_frame)
        self._metrics_layout.setContentsMargins(14, 12, 14, 12)
        self._metrics_layout.setHorizontalSpacing(10)
        self._metrics_layout.setVerticalSpacing(10)
        self._metrics_layout.setColumnStretch(0, 0)
        self._metrics_layout.setColumnStretch(1, 1)
        outer.addWidget(self._metrics_frame)

        self._action_row = QVBoxLayout()
        self._action_row.setSpacing(8)
        outer.addLayout(self._action_row)
        outer.addStretch(1)

    def apply(self, step: StepSpec) -> None:
        self._title_label.setText(step.title)
        self._subtitle_label.setText(step.subtitle)
        self._description_label.setText(step.description)
        bg, fg = _STATUS_COLORS.get(step.status, _STATUS_COLORS[STATUS_BLOCKED])
        self._status_pill.setText(_STATUS_LABELS.get(step.status, step.status.title()))
        self._status_pill.setStyleSheet(
            f"background: {bg}; color: {fg};"
            " border-radius: 11px; padding: 0px 10px;"
            " font-size: 11px; font-weight: 600;"
        )

        while self._metrics_layout.count():
            item = self._metrics_layout.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.deleteLater()
        if not step.metrics:
            self._metrics_frame.hide()
        else:
            self._metrics_frame.show()
            for row_index, (label, value) in enumerate(step.metrics):
                key_label = QLabel(label)
                key_label.setStyleSheet("color: #8d99ac;")
                key_label.setMinimumWidth(122)
                key_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
                display_value = _compact_metric_value(label, value)
                value_label = QLabel(display_value)
                value_label.setStyleSheet("color: #e6ecf4;")
                value_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                value_label.setWordWrap(True)
                if display_value != value:
                    value_label.setToolTip(value)
                value_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
                value_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
                self._metrics_layout.addWidget(key_label, row_index, 0)
                self._metrics_layout.addWidget(value_label, row_index, 1)

        while self._action_row.count():
            item = self._action_row.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.deleteLater()
        for action in step.actions:
            button = QPushButton(action.label)
            button.setMinimumHeight(32)
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            button.setEnabled(action.enabled)
            if action.tooltip:
                button.setToolTip(action.tooltip)
            if action.primary:
                button.setDefault(True)
                button.setStyleSheet(
                    "QPushButton {"
                    " background: #2f6fd6; color: white;"
                    " border-radius: 6px; padding: 7px 10px;"
                    " font-weight: 600;"
                    "} QPushButton:disabled { background: #3b4252; color: #7a8295; }"
                )
            else:
                button.setStyleSheet(
                    "QPushButton {"
                    " background: rgba(255, 255, 255, 0.07); color: #d4dbe4;"
                    " border: 1px solid rgba(255, 255, 255, 0.12);"
                    " border-radius: 6px; padding: 7px 10px;"
                    "} QPushButton:disabled { color: #6c7488; border-color: rgba(255,255,255,0.05); }"
                )
            button.clicked.connect(action.callback)
            self._action_row.addWidget(button, 0)


class AIWorkflowCenterDialog(QDialog):
    def __init__(self, window: "MainWindow") -> None:
        super().__init__(window)
        self._window = window
        self.setWindowTitle("AI Workflow Center")
        self.setObjectName("aiWorkflowCenter")
        self.setModal(False)
        self.resize(980, 650)
        self.setMinimumSize(900, 560)

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        sidebar = QFrame()
        sidebar.setObjectName("workflowSidebar")
        sidebar.setStyleSheet(
            "QFrame#workflowSidebar {"
            " background: #161c25;"
            " border-right: 1px solid rgba(255, 255, 255, 0.05);"
            "}"
        )
        sidebar.setFixedWidth(230)
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(16, 18, 16, 18)
        sidebar_layout.setSpacing(10)

        header_row = QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.setSpacing(8)
        header_label = QLabel("AI Workflow")
        header_font = QFont()
        header_font.setPointSize(13)
        header_font.setBold(True)
        header_label.setFont(header_font)
        header_row.addWidget(header_label, 1)
        help_button = build_help_button(self, tooltip="Open AI Workflow Center help")
        help_button.clicked.connect(self._show_help)
        header_row.addWidget(help_button, 0)
        sidebar_layout.addLayout(header_row)

        self._sidebar_subtitle = QLabel("")
        self._sidebar_subtitle.setStyleSheet("color: #8d99ac; font-size: 11px;")
        self._sidebar_subtitle.setWordWrap(True)
        sidebar_layout.addWidget(self._sidebar_subtitle)

        self._step_list = QListWidget()
        self._step_list.setObjectName("workflowStepList")
        self._step_list.setStyleSheet(
            "QListWidget#workflowStepList {"
            " background: transparent; border: none;"
            " font-size: 12px;"
            "}"
            "QListWidget#workflowStepList::item {"
            " padding: 10px 12px; margin: 2px 0; border-radius: 6px;"
            " color: #c4cbd6;"
            "}"
            "QListWidget#workflowStepList::item:selected {"
            " background: rgba(47, 111, 214, 0.25); color: white;"
            "}"
            "QListWidget#workflowStepList::item:hover {"
            " background: rgba(255, 255, 255, 0.04);"
            "}"
        )
        self._step_list.currentRowChanged.connect(self._handle_step_selected)
        sidebar_layout.addWidget(self._step_list, 1)

        refresh_button = QPushButton("Refresh")
        refresh_button.setStyleSheet(
            "QPushButton {"
            " background: rgba(255,255,255,0.06); color: #d4dbe4;"
            " border: 1px solid rgba(255,255,255,0.1);"
            " border-radius: 5px; padding: 6px 10px;"
            "}"
        )
        refresh_button.clicked.connect(self.refresh)
        sidebar_layout.addWidget(refresh_button)

        root.addWidget(sidebar, 0)

        self._pages = QStackedWidget()
        self._pages.setMinimumWidth(390)
        root.addWidget(self._pages, 1)

        adapter_panel = QFrame()
        adapter_panel.setObjectName("adapterHistoryPanel")
        adapter_panel.setStyleSheet(
            "QFrame#adapterHistoryPanel {"
            " background: #121821;"
            " border-left: 1px solid rgba(255, 255, 255, 0.05);"
            "}"
        )
        adapter_panel.setFixedWidth(250)
        adapter_layout = QVBoxLayout(adapter_panel)
        adapter_layout.setContentsMargins(14, 18, 14, 18)
        adapter_layout.setSpacing(10)
        adapter_title = QLabel("Adapters")
        adapter_title_font = QFont()
        adapter_title_font.setPointSize(12)
        adapter_title_font.setBold(True)
        adapter_title.setFont(adapter_title_font)
        adapter_layout.addWidget(adapter_title)
        self._adapter_tabs = QTabWidget()
        self._adapter_tabs.setObjectName("adapterScopeTabs")
        self._adapter_tabs.setStyleSheet(
            "QTabWidget#adapterScopeTabs::pane { border: none; }"
            "QTabBar::tab {"
            " background: rgba(255,255,255,0.04); color: #aab6c7;"
            " border: 1px solid rgba(255,255,255,0.08);"
            " padding: 5px 12px; margin-right: 4px; border-radius: 5px;"
            "}"
            "QTabBar::tab:selected { background: #203b64; color: white; }"
        )
        self._local_adapter_summary_label = QLabel("")
        self._local_adapter_summary_label.setStyleSheet("color: #8d99ac; font-size: 11px;")
        self._local_adapter_summary_label.setWordWrap(True)
        self._global_adapter_summary_label = QLabel("")
        self._global_adapter_summary_label.setStyleSheet("color: #8d99ac; font-size: 11px;")
        self._global_adapter_summary_label.setWordWrap(True)
        self._local_adapter_list = self._build_adapter_list()
        self._global_adapter_list = self._build_adapter_list()
        local_tab = QWidget()
        local_layout = QVBoxLayout(local_tab)
        local_layout.setContentsMargins(0, 8, 0, 0)
        local_layout.setSpacing(8)
        local_layout.addWidget(self._local_adapter_summary_label)
        local_layout.addWidget(self._local_adapter_list, 0)
        global_tab = QWidget()
        global_layout = QVBoxLayout(global_tab)
        global_layout.setContentsMargins(0, 8, 0, 0)
        global_layout.setSpacing(8)
        global_layout.addWidget(self._global_adapter_summary_label)
        global_layout.addWidget(self._global_adapter_list, 0)
        self._adapter_tabs.addTab(local_tab, "Local")
        self._adapter_tabs.addTab(global_tab, "Global")
        self._adapter_tabs.currentChanged.connect(lambda _index: self._handle_adapter_selected(self._current_adapter_row()))
        adapter_layout.addWidget(self._adapter_tabs, 0)
        self._local_adapter_list.currentRowChanged.connect(self._handle_adapter_selected)
        self._global_adapter_list.currentRowChanged.connect(self._handle_adapter_selected)
        self._adapter_list_style = (
            "QListWidget#adapterHistoryList {"
            " background: transparent; border: none;"
            "} QListWidget#adapterHistoryList::item {"
            " padding: 8px 8px; margin: 1px 0; border-radius: 5px;"
            " color: #c4cbd6;"
            "} QListWidget#adapterHistoryList::item:selected {"
            " background: rgba(47, 111, 214, 0.25); color: white;"
            "}"
        )
        self._local_adapter_list.setStyleSheet(self._adapter_list_style)
        self._global_adapter_list.setStyleSheet(self._adapter_list_style)
        self._delete_adapter_button = QPushButton("Delete Adapter")
        self._delete_adapter_button.setEnabled(False)
        self._delete_adapter_button.setStyleSheet(
            "QPushButton {"
            " background: rgba(126, 49, 43, 0.72); color: #f3d2ce;"
            " border: 1px solid rgba(255, 255, 255, 0.12);"
            " border-radius: 6px; padding: 7px 10px;"
            "} QPushButton:disabled { background: rgba(255,255,255,0.04); color: #6c7488; }"
        )
        self._delete_adapter_button.clicked.connect(self._delete_selected_adapter)
        adapter_layout.addWidget(self._delete_adapter_button, 0)
        self._adapter_detail_label = QLabel("")
        self._adapter_detail_label.setStyleSheet("color: #d4dbe4; font-size: 11px;")
        self._adapter_detail_label.setWordWrap(True)
        self._adapter_detail_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        detail_scroll = QScrollArea()
        detail_scroll.setWidgetResizable(True)
        detail_scroll.setFrameShape(QFrame.Shape.NoFrame)
        detail_scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        detail_container = QWidget()
        detail_layout = QVBoxLayout(detail_container)
        detail_layout.setContentsMargins(0, 0, 0, 0)
        detail_layout.addWidget(self._adapter_detail_label)
        detail_layout.addStretch(1)
        detail_scroll.setWidget(detail_container)
        adapter_layout.addWidget(detail_scroll, 1)
        root.addWidget(adapter_panel, 0)

        self._step_keys: list[str] = []
        self._page_widgets: dict[str, _StepPage] = {}
        self._adapter_models: tuple[dict[str, object], ...] = ()
        self._global_adapter_models: tuple[dict[str, object], ...] = ()
        self._build_pages()
        self.refresh()

    def _build_adapter_list(self) -> QListWidget:
        adapter_list = QListWidget()
        adapter_list.setObjectName("adapterHistoryList")
        adapter_list.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        adapter_list.setMinimumHeight(58)
        adapter_list.setMaximumHeight(190)
        return adapter_list

    def _show_help(self) -> None:
        show_paged_help(
            self,
            title="AI Workflow Center Help",
            pages=ai_workflow_center_help_pages(),
        )

    def _build_pages(self) -> None:
        for key, label in (
            ("setup", "1. Setup"),
            ("dino", "2. DINO Prefilter"),
            ("index", "3. Index & Score"),
            ("label", "4. Review Labels"),
            ("train", "5. Train Adapter"),
            ("evaluate", "6. Evaluate"),
            ("apply", "7. Rank & Apply"),
        ):
            self._step_keys.append(key)
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, key)
            self._step_list.addItem(item)
            page = _StepPage()
            page.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.Shape.NoFrame)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            scroll.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
            scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
            scroll.setWidget(page)
            self._page_widgets[key] = page
            self._pages.addWidget(scroll)
        if self._step_list.count():
            self._step_list.setCurrentRow(0)

    def _handle_step_selected(self, row: int) -> None:
        if 0 <= row < self._pages.count():
            self._pages.setCurrentIndex(row)
            widget = self._pages.widget(row)
            if isinstance(widget, QScrollArea):
                widget.verticalScrollBar().setValue(0)
                widget.horizontalScrollBar().setValue(0)

    def _handle_adapter_selected(self, row: int) -> None:
        self._update_adapter_detail(row)
        self._delete_adapter_button.setEnabled(0 <= row < len(self._active_adapter_models()))

    def _delete_selected_adapter(self) -> None:
        row = self._current_adapter_row()
        models = self._active_adapter_models()
        if row < 0 or row >= len(models):
            return
        version = str(models[row].get("model_version") or "").strip()
        if not version:
            return
        handler = getattr(self._window, "_delete_aiculler_adapter", None)
        if callable(handler):
            handler(version, scope=self._active_adapter_scope())

    def refresh(self) -> None:
        snapshot = self._capture_snapshot()
        folder_text = snapshot.folder_path or "(no folder open)"
        self._sidebar_subtitle.setText(f"Folder:\n{folder_text}")
        self._populate_adapter_history(snapshot.adapter_models, snapshot.global_adapter_models)

        steps = self._build_steps(snapshot)
        for key, step in steps.items():
            page = self._page_widgets.get(key)
            if page is None:
                continue
            page.apply(step)
            item = self._find_item(key)
            if item is not None:
                marker = {
                    STATUS_DONE: "✓ ",
                    STATUS_READY: "• ",
                    STATUS_BLOCKED: "· ",
                }.get(step.status, "")
                # Preserve numeric prefix while annotating status
                base = step.title.split(": ", 1)[-1] if ": " in step.title else step.title
                idx = self._step_keys.index(key) + 1
                item.setText(f"{marker}{idx}. {base}")
                colors = _STATUS_COLORS.get(step.status, _STATUS_COLORS[STATUS_BLOCKED])
                item.setForeground(Qt.GlobalColor.lightGray if step.status == STATUS_BLOCKED else Qt.GlobalColor.white)
                item.setToolTip(_STATUS_LABELS.get(step.status, ""))

    def _populate_adapter_history(
        self,
        adapter_models: tuple[dict[str, object], ...],
        global_adapter_models: tuple[dict[str, object], ...],
    ) -> None:
        selected_versions = {"local": "", "global": ""}
        current_item = self._local_adapter_list.currentItem()
        if current_item is not None:
            selected_versions["local"] = str(current_item.data(Qt.ItemDataRole.UserRole) or "")
        current_item = self._global_adapter_list.currentItem()
        if current_item is not None:
            selected_versions["global"] = str(current_item.data(Qt.ItemDataRole.UserRole) or "")
        self._adapter_models = adapter_models
        self._global_adapter_models = global_adapter_models
        self._populate_adapter_list(
            self._local_adapter_list,
            adapter_models,
            selected_versions["local"],
            empty_text="No local adapters for this folder yet.",
        )
        self._populate_adapter_list(
            self._global_adapter_list,
            global_adapter_models,
            selected_versions["global"],
            empty_text="No global adapters trained yet.",
        )
        self._local_adapter_summary_label.setText(
            f"{len(adapter_models)} local adapter{'s' if len(adapter_models) != 1 else ''}"
            if adapter_models
            else "No local adapters for this folder yet."
        )
        self._global_adapter_summary_label.setText(
            f"{len(global_adapter_models)} global adapter{'s' if len(global_adapter_models) != 1 else ''}"
            if global_adapter_models
            else "No global adapters trained yet."
        )
        self._update_adapter_detail(self._current_adapter_row())
        self._delete_adapter_button.setEnabled(0 <= self._current_adapter_row() < len(self._active_adapter_models()))

    def _populate_adapter_list(
        self,
        adapter_list: QListWidget,
        adapter_models: tuple[dict[str, object], ...],
        selected_version: str,
        *,
        empty_text: str,
    ) -> None:
        adapter_list.clear()
        selected_row = 0
        for index, model in enumerate(adapter_models):
            version = str(model.get("model_version") or "")
            score_fit = model.get("score_fit_percent", model.get("accuracy_percent"))
            score_fit_text = f"{float(score_fit):.1f}%" if isinstance(score_fit, (int, float)) else "n/a"
            item = QListWidgetItem(f"{_display_adapter_version(version, compact=True)}\nScore Fit {score_fit_text}")
            item.setData(Qt.ItemDataRole.UserRole, version)
            item.setToolTip(_display_adapter_version(version))
            adapter_list.addItem(item)
            if version == selected_version:
                selected_row = index
        list_height = 58 if not adapter_models else max(58, min(190, len(adapter_models) * 62 + 12))
        adapter_list.setMinimumHeight(list_height)
        adapter_list.setMaximumHeight(list_height)
        if adapter_models:
            adapter_list.setCurrentRow(selected_row)

    def _active_adapter_scope(self) -> str:
        return "global" if self._adapter_tabs.currentIndex() == 1 else "local"

    def _active_adapter_models(self) -> tuple[dict[str, object], ...]:
        return self._global_adapter_models if self._active_adapter_scope() == "global" else self._adapter_models

    def _active_adapter_list(self) -> QListWidget:
        return self._global_adapter_list if self._active_adapter_scope() == "global" else self._local_adapter_list

    def _current_adapter_row(self) -> int:
        return self._active_adapter_list().currentRow()

    def _update_adapter_detail(self, row: int) -> None:
        models = self._active_adapter_models()
        if row < 0 or row >= len(models):
            scope_label = "global" if self._active_adapter_scope() == "global" else "local"
            self._adapter_detail_label.setText(f"Train a {scope_label} adapter to see score fit, MAE, and scored image counts here.")
            return
        model = models[row]
        score_fit = model.get("score_fit_percent", model.get("accuracy_percent"))
        holdout_mae = model.get("holdout_mae")
        train_mae = model.get("train_mae")
        failure_rate = holdout_mae if isinstance(holdout_mae, (int, float)) else train_mae
        score_fit_text = f"{float(score_fit):.1f}%" if isinstance(score_fit, (int, float)) else "n/a"
        failure_text = f"{float(failure_rate) * 100.0:.1f}%" if isinstance(failure_rate, (int, float)) else "n/a"
        holdout_text = f"{float(holdout_mae):.4f}" if isinstance(holdout_mae, (int, float)) else "n/a"
        train_text = f"{float(train_mae):.4f}" if isinstance(train_mae, (int, float)) else "n/a"
        details = [
            f"Version: {_display_adapter_version(str(model.get('model_version') or 'unknown'))}",
            f"Score Fit: {score_fit_text}",
            f"MAE as percent: {failure_text}",
            f"Holdout MAE: {holdout_text}",
            f"Train MAE: {train_text}",
            f"Scored images: {int(model.get('scored_count') or 0)}",
        ]
        origin_counts = model.get("label_origin_counts")
        if isinstance(origin_counts, dict) and origin_counts:
            origin_text = ", ".join(f"{key}: {value}" for key, value in sorted(origin_counts.items()))
            details.append(f"Labels: {origin_text}")
        created_at = str(model.get("created_at") or "")
        if created_at:
            details.append(f"Trained: {created_at}")
        self._adapter_detail_label.setText("\n".join(details))

    def _find_item(self, key: str) -> QListWidgetItem | None:
        for row in range(self._step_list.count()):
            item = self._step_list.item(row)
            if item is not None and item.data(Qt.ItemDataRole.UserRole) == key:
                return item
        return None

    def _capture_snapshot(self) -> WorkflowSnapshot:
        runtime_ready = False
        runtime_source = ""
        model_root = ""
        runtime_note = ""
        clip_model_label = clip_model_variant_info(getattr(self._window, "_ai_clip_model_variant", "uint8")).label
        try:
            status = aiculler_runtime_status()
            runtime_ready = status.is_ready
            runtime_source = str(status.runtime.cli_entrypoint)
            parents = status.runtime.clip_vision_model.parents
            model_root = str(parents[3] if len(parents) > 3 else status.runtime.clip_vision_model.parent)
            if status.missing_required:
                runtime_note = status.missing_required[0]
            elif status.missing_optional:
                runtime_note = status.missing_optional[0]
        except Exception as exc:
            runtime_ready = False
            runtime_note = str(exc)
        folder_path = self._window._current_folder or ""
        file_count = sum(1 for record in self._window._all_records if not record.is_folder)
        paths = None
        if folder_path:
            try:
                paths = build_aiculler_workflow_paths(folder_path)
            except Exception:
                paths = None
        db_exists = False
        indexed_count = 0
        cluster_run_id = ""
        can_rerank = False
        label_count = 0
        pending_label_count = 0
        global_label_count = 0
        global_label_values = 0
        global_matching_label_count = 0
        global_matching_label_values = 0
        global_matching_dispute_count = 0
        adapter_version = ""
        adapter_created_at = ""
        train_mae: float | None = None
        holdout_mae: float | None = None
        train_rank_lift: float | None = None
        scored_count = 0
        adapter_models: tuple[dict[str, object], ...] = ()
        global_adapter_models: tuple[dict[str, object], ...] = ()
        global_adapter_version = ""
        dino_settings = getattr(self._window, "_dino_prefilter_settings", default_dino_prefilter_settings())
        dino_settings = dino_settings.normalized()
        dino_report_exists = False
        dino_rows_exists = False
        dino_report_created_at = ""
        dino_model_policy = "base_model_only"
        dino_scanned_count = 0
        dino_quarantined_count = 0
        dino_removed_from_pool_count = 0
        dino_rescued_count = 0
        dino_cache_hit = False
        dino_reason_counts: tuple[tuple[str, int], ...] = ()
        dino_rescue_counts: tuple[tuple[str, int], ...] = ()
        dino_artifact_dir = ""
        phash_report_exists = False
        phash_artifact_dir = ""
        if paths is not None:
            db_path = aiculler_db_path(paths)
            readiness = aiculler_rerank_readiness(db_path)
            db_exists = bool(readiness.get("db_exists"))
            indexed_count = int(readiness.get("ready_image_count") or 0)
            cluster_run_id = str(readiness.get("cluster_run_id") or "")
            can_rerank = bool(readiness.get("can_rerank"))
            if db_exists:
                summary = load_adapter_status_summary(db_path)
                label_count = int(summary.get("rating_count") or 0)
                adapter_version = str(summary.get("model_version") or "")
                adapter_created_at = str(summary.get("created_at") or "")
                train_mae = summary.get("train_mae") if isinstance(summary.get("train_mae"), (int, float)) else None
                holdout_mae = summary.get("holdout_mae") if isinstance(summary.get("holdout_mae"), (int, float)) else None
                train_rank_lift = (
                    summary.get("train_rank_lift")
                    if isinstance(summary.get("train_rank_lift"), (int, float))
                    else None
                )
                scored_count = int(summary.get("scored_count") or 0)
                adapter_models = tuple(list_adapter_model_summaries(db_path))
            # In-progress labels saved via the per-card adapter combo. These
            # haven't been imported into the DB yet — that happens at train
            # time — but they DO count toward "you can train now".
            try:
                pending_labels = self._window._load_aiculler_internal_labels(paths)
                pending_label_count = len(pending_labels)
            except Exception:
                pending_label_count = 0
            try:
                dino_paths = build_dino_prefilter_paths(paths)
                dino_artifact_dir = str(dino_paths.artifact_dir)
                dino_report_exists = dino_paths.report_path.exists()
                dino_rows_exists = dino_paths.rows_path.exists()
                if dino_report_exists:
                    report = json.loads(dino_paths.report_path.read_text(encoding="utf-8"))
                    counts = report.get("counts") if isinstance(report, dict) else {}
                    if not isinstance(counts, dict):
                        counts = {}
                    dino_report_created_at = str(report.get("created_at") or "")
                    dino_model_policy = str(report.get("model_policy") or dino_model_policy)
                    dino_scanned_count = _int_value(counts.get("scanned"))
                    dino_quarantined_count = _int_value(counts.get("quarantined"))
                    dino_removed_from_pool_count = _int_value(counts.get("removed_from_pool"))
                    dino_rescued_count = _int_value(counts.get("rescued"))
                    dino_cache_hit = bool(report.get("cache_hit"))
                    dino_reason_counts = _sorted_count_pairs(report.get("reason_counts"))
                    dino_rescue_counts = _sorted_count_pairs(report.get("rescue_counts"))
            except Exception:
                dino_report_exists = False
                dino_rows_exists = False
            try:
                phash_paths = build_phash_prefilter_paths(paths)
                phash_artifact_dir = str(phash_paths.artifact_dir)
                phash_report_exists = phash_paths.report_path.exists() or phash_paths.cache_path.exists()
            except Exception:
                phash_report_exists = False
        current_file_paths = tuple(record.path for record in self._window._all_records if not record.is_folder)
        try:
            global_db_path = global_aiculler_db_path()
            global_adapter_models = tuple(list_adapter_model_summaries(global_db_path))
            global_adapter_version = str(global_adapter_models[0].get("model_version") or "") if global_adapter_models else ""
        except Exception:
            global_adapter_models = ()
            global_adapter_version = ""
        try:
            store = GlobalAdapterLabelStore(default_global_adapter_label_store_path())
            try:
                all_global_labels = store.all_labels()
                global_label_count = len(all_global_labels)
                global_label_values = len({label.label for label in all_global_labels})
                matching_labels = store.labels_for_paths(current_file_paths)
                global_matching_label_count = len(matching_labels)
                global_matching_label_values = len({label.label for label in matching_labels.values()})
                global_matching_dispute_count = sum(1 for label in matching_labels.values() if label.is_dispute)
            finally:
                store.close()
        except Exception:
            global_label_count = 0
            global_label_values = 0
            global_matching_label_count = 0
            global_matching_label_values = 0
            global_matching_dispute_count = 0
        return WorkflowSnapshot(
            runtime_ready=runtime_ready,
            runtime_source=runtime_source,
            model_root=model_root,
            runtime_note=runtime_note,
            clip_model_label=clip_model_label,
            folder_open=bool(folder_path),
            db_exists=db_exists,
            indexed_count=indexed_count,
            cluster_run_id=cluster_run_id,
            can_rerank=can_rerank,
            label_count=label_count,
            pending_label_count=pending_label_count,
            global_label_count=global_label_count,
            global_label_values=global_label_values,
            global_matching_label_count=global_matching_label_count,
            global_matching_label_values=global_matching_label_values,
            global_matching_dispute_count=global_matching_dispute_count,
            adapter_version=adapter_version,
            adapter_created_at=adapter_created_at,
            train_mae=train_mae,
            holdout_mae=holdout_mae,
            train_rank_lift=train_rank_lift,
            scored_count=scored_count,
            adapter_models=adapter_models,
            global_adapter_models=global_adapter_models,
            global_adapter_version=global_adapter_version,
            folder_path=folder_path,
            file_count=file_count,
            dino_enabled=dino_settings.enabled,
            dino_mode_label=dino_prefilter_mode_label(dino_settings.mode),
            dino_aggressiveness_percent=dino_settings.aggressiveness_percent,
            dino_diagnostics_enabled=dino_settings.diagnostics_enabled,
            dino_report_exists=dino_report_exists,
            dino_rows_exists=dino_rows_exists,
            dino_report_created_at=dino_report_created_at,
            dino_model_policy=dino_model_policy,
            dino_scanned_count=dino_scanned_count,
            dino_quarantined_count=dino_quarantined_count,
            dino_removed_from_pool_count=dino_removed_from_pool_count,
            dino_rescued_count=dino_rescued_count,
            dino_cache_hit=dino_cache_hit,
            dino_reason_counts=dino_reason_counts,
            dino_rescue_counts=dino_rescue_counts,
            dino_artifact_dir=dino_artifact_dir,
            phash_report_exists=phash_report_exists,
            phash_artifact_dir=phash_artifact_dir,
        )

    def _build_steps(self, snap: WorkflowSnapshot) -> dict[str, StepSpec]:
        steps: dict[str, StepSpec] = {}

        steps["setup"] = StepSpec(
            key="setup",
            title="Setup",
            subtitle="Confirm the CLI-Culler runtime, models, and category prompts.",
            description=(
                "Image Triage drives an external CLI-Culler runtime. This step lets "
                "you verify the runtime is reachable and tweak the semantic category "
                "prompts that classify your images."
            ),
            status=STATUS_DONE if snap.runtime_ready else STATUS_BLOCKED,
            metrics=[
                ("Runtime", "Ready" if snap.runtime_ready else "Unavailable"),
                ("Culler source", snap.runtime_source or "—"),
                ("Model root", snap.model_root or "—"),
                ("CLIP model", snap.clip_model_label),
                ("Current folder", snap.folder_path or "(none)"),
            ] + ([("Note", snap.runtime_note)] if snap.runtime_note else []),
            actions=[
                ActionSpec(
                    label="Install AI Runtime",
                    callback=lambda: self._invoke("_install_ai_runtime"),
                    primary=not snap.runtime_ready,
                    enabled=True,
                ),
                ActionSpec(
                    label="Download AI Models",
                    callback=lambda: self._invoke("_download_ai_model"),
                    enabled=True,
                ),
                ActionSpec(
                    label="Open AI Culler source",
                    callback=lambda: self._invoke("_open_aiculler_root"),
                    enabled=True,
                ),
                ActionSpec(
                    label="Edit category prompts",
                    callback=lambda: self._invoke("_open_aiculler_categories"),
                    enabled=True,
                ),
            ],
        )

        if not snap.dino_enabled:
            dino_status = STATUS_DONE
        elif not snap.runtime_ready or not snap.folder_open:
            dino_status = STATUS_BLOCKED
        elif snap.dino_report_exists and snap.dino_rows_exists and snap.dino_scanned_count > 0:
            dino_status = STATUS_DONE
        else:
            dino_status = STATUS_READY
        dino_metrics: list[tuple[str, str]] = [
            ("Configured", "Enabled" if snap.dino_enabled else "Disabled"),
            ("Mode", snap.dino_mode_label),
            ("Confidence threshold", f"{snap.dino_aggressiveness_percent}%"),
            ("Model policy", snap.dino_model_policy or "base_model_only"),
            ("Diagnostics", "On" if snap.dino_diagnostics_enabled else "Off"),
        ]
        if snap.dino_report_exists:
            dino_metrics.extend(
                [
                    ("Last run", snap.dino_report_created_at or "—"),
                    ("Scanned", str(snap.dino_scanned_count)),
                    ("Quarantined", str(snap.dino_quarantined_count)),
                    ("Removed from pool", str(snap.dino_removed_from_pool_count)),
                    ("Rescued", str(snap.dino_rescued_count)),
                    ("Cache", "Hit" if snap.dino_cache_hit else "Fresh run"),
                ]
            )
            if snap.dino_reason_counts:
                dino_metrics.append(("Trash reasons", _format_count_pairs(snap.dino_reason_counts)))
            if snap.dino_rescue_counts:
                dino_metrics.append(("Rescue rules", _format_count_pairs(snap.dino_rescue_counts)))
        else:
            dino_metrics.append(("Last run", "No DINO report for this folder."))
        steps["dino"] = StepSpec(
            key="dino",
            title="DINO Prefilter",
            subtitle="Optional base-model first pass before the AI culler judges the folder.",
            description=(
                "Run DINO Prefilter as its own first pass, then review the marks before moving "
                "to Index & Score. Soft Quarantine marks likely trash while keeping the folder "
                "in the downstream AI pool; Pool Removal excludes those candidates before the "
                "current AI and adapter score the remaining images."
            ),
            status=dino_status,
            metrics=dino_metrics,
            actions=[
                ActionSpec(
                    label="Open DINO Settings",
                    callback=lambda: self._invoke("_open_dino_prefilter_settings"),
                    primary=not snap.dino_enabled,
                    enabled=True,
                ),
                ActionSpec(
                    label="Run DINO Prefilter",
                    callback=lambda: self._invoke("_run_dino_prefilter"),
                    primary=snap.dino_enabled,
                    enabled=snap.dino_enabled and snap.runtime_ready and snap.folder_open,
                    tooltip=(
                        "Enable DINO Prefilter in settings before running this step."
                        if not snap.dino_enabled
                        else ""
                    ),
                ),
                ActionSpec(
                    label="Delete DINO Artifacts",
                    callback=lambda: self._invoke("_delete_dino_prefilter_artifacts"),
                    enabled=bool(snap.dino_artifact_dir and snap.dino_report_exists),
                    tooltip=(
                        "Runs are written after DINO Prefilter has executed for this folder."
                        if not snap.dino_report_exists
                        else ""
                    ),
                ),
                ActionSpec(
                    label="Delete pHash Artifacts",
                    callback=lambda: self._invoke("_delete_phash_prefilter_artifacts"),
                    enabled=bool(snap.phash_artifact_dir and snap.phash_report_exists),
                    tooltip=(
                        "pHash artifacts are written after pHash Prefilter has run for this folder."
                        if not snap.phash_report_exists
                        else ""
                    ),
                ),
            ],
        )

        index_status = STATUS_BLOCKED
        dino_required = snap.dino_enabled
        dino_ready_for_index = (not dino_required) or (
            snap.dino_report_exists and snap.dino_rows_exists and snap.dino_scanned_count > 0
        )
        if not snap.runtime_ready or not snap.folder_open:
            index_status = STATUS_BLOCKED
        elif not dino_ready_for_index:
            index_status = STATUS_BLOCKED
        elif snap.db_exists and snap.indexed_count > 0 and snap.cluster_run_id:
            index_status = STATUS_DONE
        else:
            index_status = STATUS_READY
        index_metrics: list[tuple[str, str]] = [
            ("Indexed images", str(snap.indexed_count) if snap.db_exists else "—"),
            ("Folder files", str(snap.file_count) if snap.folder_open else "—"),
            ("Latest cluster run", snap.cluster_run_id or "—"),
        ]
        if snap.db_exists and snap.indexed_count != snap.file_count and snap.file_count:
            index_metrics.append(("Note", "Folder file count differs from indexed count — re-run AI Culler to catch up."))
        if not dino_ready_for_index:
            index_metrics.append(("Waiting on", "Run and review DINO Prefilter first."))
        steps["index"] = StepSpec(
            key="index",
            title="Index & Score",
            subtitle="Ingest the folder, assign categories, cluster, and rank.",
            description=(
                "This is the full pipeline: ingest each image, assign semantic "
                "categories, cluster within each category, and produce the base "
                "ranking with technical penalties applied."
            ),
            status=index_status,
            metrics=index_metrics,
            actions=[
                ActionSpec(
                    label="Run Index & Score",
                    callback=lambda: self._invoke("_run_ai_pipeline"),
                    primary=True,
                    enabled=snap.runtime_ready and snap.folder_open and dino_ready_for_index,
                    tooltip=(
                        "Run DINO Prefilter first, then review its marks before indexing and scoring."
                        if not dino_ready_for_index
                        else ""
                    ),
                ),
                ActionSpec(
                    label="Quick Rerank",
                    callback=lambda: self._invoke("_rerank_ai_pipeline"),
                    enabled=snap.can_rerank,
                    tooltip=(
                        "Skips ingest and clustering. Available after Index & Score has populated this folder."
                        if not snap.can_rerank
                        else ""
                    ),
                ),
            ],
        )

        # Label step is "done" if any usable folder/global training labels exist.
        # The DB ratings table is only populated after successful training, so
        # saved labels outside that table need to count here too.
        label_status = STATUS_BLOCKED
        if index_status == STATUS_DONE:
            label_status = STATUS_DONE if snap.has_trainable_labels else STATUS_READY
        label_metrics = []
        if snap.pending_label_count:
            label_metrics.append(("Training Labels Available", str(snap.pending_label_count)))
        label_metrics.append(("Trained-on labels", str(snap.label_count)))
        label_metrics.append(("Global labels", str(snap.global_label_count)))
        label_metrics.append(("Matching global labels", str(snap.global_matching_label_count)))
        if snap.global_matching_dispute_count:
            label_metrics.append(("Matching disputes", str(snap.global_matching_dispute_count)))
        label_metrics.append(("Cluster run", snap.cluster_run_id or "—"))
        steps["label"] = StepSpec(
            key="label",
            title="Review Labels",
            subtitle="Hand-rate a sampled set of candidates to teach the adapter.",
            description=(
                "Opens a filtered grid showing the most useful candidates "
                "(proportional category coverage + technical-disagreement). Use "
                "keys 1–5 (best / strong / maybe / weak / reject) — auto-advance "
                "moves to the next un-labeled candidate automatically. You don't "
                "need to label every candidate; a couple of dozen across at least "
                "two rating values is enough to train."
            ),
            status=label_status,
            metrics=label_metrics,
            actions=[
                ActionSpec(
                    label="Open Review Labels",
                    callback=lambda: self._invoke("_review_aiculler_adapter_labels"),
                    primary=True,
                    enabled=snap.db_exists and snap.indexed_count > 0,
                ),
                ActionSpec(
                    label="Prepare Ratings CSV",
                    callback=lambda: self._invoke("_export_aiculler_ratings"),
                    enabled=snap.db_exists,
                ),
            ],
        )

        train_status = STATUS_BLOCKED
        if label_status == STATUS_DONE:
            train_status = STATUS_DONE if snap.adapter_version else STATUS_READY
        train_metrics: list[tuple[str, str]] = []
        if snap.pending_label_count:
            train_metrics.append(("Training Labels Available", str(snap.pending_label_count)))
        train_metrics.append(("Trained-on labels", str(snap.label_count)))
        train_metrics.append(("Global labels", str(snap.global_label_count)))
        train_metrics.append(("Local adapter", _display_adapter_version(snap.adapter_version) if snap.adapter_version else "untrained"))
        train_metrics.append(("Global adapter", _display_adapter_version(snap.global_adapter_version) if snap.global_adapter_version else "untrained"))
        if snap.adapter_created_at:
            train_metrics.append(("Trained at", snap.adapter_created_at))
        if snap.train_mae is not None:
            train_metrics.append(("Train MAE", f"{snap.train_mae:.4f}"))
        if snap.train_rank_lift is not None:
            train_metrics.append(("Train rank lift", f"{snap.train_rank_lift:+.3f}"))
        steps["train"] = StepSpec(
            key="train",
            title="Train Adapter",
            subtitle="Fit a personal model from your saved labels.",
            description=(
                "Trains the adapter on every label saved for this folder — "
                "both the available folder training labels and any "
                "previously-trained labels in the DB. Training is fast "
                "(a few seconds per hundred labels) and produces a new model "
                "version you can evaluate or rank with."
            ),
            status=train_status,
            metrics=train_metrics,
            actions=[
                ActionSpec(
                    label="Train Adapter",
                    callback=lambda: self._invoke("_train_aiculler_adapter"),
                    primary=True,
                    enabled=snap.db_exists and snap.total_label_count > 0,
                ),
                ActionSpec(
                    label="Train Global Adapter",
                    callback=lambda: self._invoke("_train_aiculler_adapter_from_global_labels"),
                    enabled=snap.can_train_global_adapter,
                    tooltip=(
                        "Needs at least two global labels with two different rating values."
                        if not snap.can_train_global_adapter
                        else ""
                    ),
                ),
            ],
        )

        evaluate_status = STATUS_BLOCKED
        if train_status == STATUS_DONE:
            evaluate_status = STATUS_DONE if snap.holdout_mae is not None else STATUS_READY
        eval_metrics: list[tuple[str, str]] = []
        if snap.holdout_mae is not None:
            eval_metrics.append(("Holdout MAE", f"{snap.holdout_mae:.4f}"))
        if snap.train_mae is not None and snap.holdout_mae is not None:
            eval_metrics.append((
                "Generalization gap",
                f"{(snap.holdout_mae - snap.train_mae):+.4f}",
            ))
        if not eval_metrics:
            eval_metrics.append(("Evaluation", "Not run yet."))
        steps["evaluate"] = StepSpec(
            key="evaluate",
            title="Evaluate",
            subtitle="Check the adapter on stored labels and a held-out slice.",
            description=(
                "Computes mean absolute error and rank lift across train and "
                "holdout folds. Aim for a small gap between train and holdout — a "
                "blowout usually means too few or too inconsistent labels."
            ),
            status=evaluate_status,
            metrics=eval_metrics,
            actions=[
                ActionSpec(
                    label="Evaluate Adapter",
                    callback=lambda: self._invoke("_evaluate_aiculler_adapter"),
                    primary=True,
                    enabled=bool(snap.adapter_version),
                ),
            ],
        )

        active_adapter_version = snap.global_adapter_version if self._active_adapter_scope() == "global" else snap.adapter_version
        active_adapter_count = len(snap.global_adapter_models) if self._active_adapter_scope() == "global" else len(snap.adapter_models)
        apply_status = STATUS_BLOCKED
        if active_adapter_version:
            apply_status = STATUS_DONE if snap.scored_count > 0 else STATUS_READY
        apply_metrics = [
            ("Adapter scope", self._active_adapter_scope().title()),
            ("Adapter version", active_adapter_version or "untrained"),
            ("Adapter-scored images", str(snap.scored_count) if snap.scored_count else "—"),
        ]
        steps["apply"] = StepSpec(
            key="apply",
            title="Rank & Apply",
            subtitle="Score the folder with your adapter, then act on the results.",
            description=(
                "Ranks the current folder with the trained adapter and writes "
                "fresh GUI exports. From there you can move the top picks into "
                "your winners folder, sort by AI category, or push rejects to "
                "the recycle bin. If the AI is wrong on a card, use Dispute AI "
                "in AI Review to save your correction as a stronger adapter "
                "training label."
            ),
            status=apply_status,
            metrics=apply_metrics,
            actions=[
                ActionSpec(
                    label="Rank with Adapter",
                    callback=self._rank_selected_adapter,
                    primary=True,
                    enabled=bool(active_adapter_version and active_adapter_count),
                ),
                ActionSpec(
                    label="Apply AI Decisions",
                    callback=lambda: self._invoke("_apply_ai_culling"),
                    enabled=snap.scored_count > 0,
                ),
                ActionSpec(
                    label="Sort Into Categories",
                    callback=lambda: self._invoke("_sort_images_into_semantic_folders"),
                    enabled=snap.scored_count > 0,
                ),
            ],
        )
        return steps

    def _invoke(self, slot_name: str) -> None:
        slot = getattr(self._window, slot_name, None)
        if not callable(slot):
            return
        slot()
        self.refresh()

    def _rank_selected_adapter(self) -> None:
        slot = getattr(self._window, "_rank_aiculler_adapter", None)
        if not callable(slot):
            return
        slot(scope=self._active_adapter_scope())
        self.refresh()
