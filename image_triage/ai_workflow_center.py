from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
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


def _load_telemetry_health(db_path: str | Path) -> dict[str, object]:
    path = Path(db_path)
    if not path.exists():
        return {
            "override_count": 0,
            "final_usable_override_count": 0,
            "ignored_intermediate_override_count": 0,
            "latest_override_created_at": "",
        }
    connection = sqlite3.connect(path)
    try:
        table_exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'user_overrides'"
        ).fetchone()
        if table_exists is None:
            return {
                "override_count": 0,
                "final_usable_override_count": 0,
                "ignored_intermediate_override_count": 0,
                "latest_override_created_at": "",
            }
        row = connection.execute(
            """
            SELECT
                COUNT(*) AS override_count,
                SUM(CASE WHEN is_final = 1 AND ignored_for_training = 0 THEN 1 ELSE 0 END)
                  AS final_usable_override_count,
                SUM(CASE WHEN is_final = 0 OR ignored_for_training = 1 THEN 1 ELSE 0 END)
                  AS ignored_intermediate_override_count,
                MAX(created_at) AS latest_override_created_at
            FROM user_overrides
            """
        ).fetchone()
    finally:
        connection.close()
    if row is None:
        return {
            "override_count": 0,
            "final_usable_override_count": 0,
            "ignored_intermediate_override_count": 0,
            "latest_override_created_at": "",
        }
    return {
        "override_count": int(row[0] or 0),
        "final_usable_override_count": int(row[1] or 0),
        "ignored_intermediate_override_count": int(row[2] or 0),
        "latest_override_created_at": str(row[3] or ""),
    }


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
    telemetry_override_count: int
    telemetry_final_usable_override_count: int
    telemetry_ignored_intermediate_override_count: int
    telemetry_latest_override_created_at: str
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
        self._hidden_for_adapter_review = False
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

        self._step_keys: list[str] = []
        self._page_widgets: dict[str, _StepPage] = {}
        self._build_pages()
        self.refresh()

    def hide_for_adapter_review(self) -> None:
        if not self.isVisible():
            self._hidden_for_adapter_review = False
            return
        self._hidden_for_adapter_review = True
        self.hide()

    def restore_after_adapter_review(self) -> None:
        if not self._hidden_for_adapter_review:
            return
        self._hidden_for_adapter_review = False
        self.refresh()
        self.show()
        self.raise_()
        self.activateWindow()

    def _show_help(self) -> None:
        show_paged_help(
            self,
            title="AI Workflow Center Help",
            pages=ai_workflow_center_help_pages(),
        )

    def _build_pages(self) -> None:
        for key, label in (
            ("setup", "1. Setup"),
            ("index", "2. Cull & Score"),
            ("review", "3. Review Results"),
            ("apply", "4. Apply Decisions"),
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

    def refresh(self) -> None:
        snapshot = self._capture_snapshot()
        folder_text = snapshot.folder_path or "(no folder open)"
        self._sidebar_subtitle.setText(f"Folder:\n{folder_text}")

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
        clip_model_label = clip_model_variant_info(getattr(self._window, "_ai_clip_model_variant", "fp32")).label
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
        telemetry_override_count = 0
        telemetry_final_usable_override_count = 0
        telemetry_ignored_intermediate_override_count = 0
        telemetry_latest_override_created_at = ""
        adapter_version = ""
        adapter_created_at = ""
        train_mae: float | None = None
        holdout_mae: float | None = None
        train_rank_lift: float | None = None
        scored_count = 0
        adapter_models: tuple[dict[str, object], ...] = ()
        global_adapter_models: tuple[dict[str, object], ...] = ()
        global_adapter_version = ""
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
                telemetry_health = _load_telemetry_health(db_path)
                telemetry_override_count = int(telemetry_health.get("override_count") or 0)
                telemetry_final_usable_override_count = int(telemetry_health.get("final_usable_override_count") or 0)
                telemetry_ignored_intermediate_override_count = int(
                    telemetry_health.get("ignored_intermediate_override_count") or 0
                )
                telemetry_latest_override_created_at = str(telemetry_health.get("latest_override_created_at") or "")
            # In-progress labels saved via the per-card adapter combo. These
            # haven't been imported into the DB yet — that happens at train
            # time — but they DO count toward "you can train now".
            try:
                pending_labels = self._window._load_aiculler_internal_labels(paths)
                pending_label_count = len(pending_labels)
            except Exception:
                pending_label_count = 0
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
            telemetry_override_count=telemetry_override_count,
            telemetry_final_usable_override_count=telemetry_final_usable_override_count,
            telemetry_ignored_intermediate_override_count=telemetry_ignored_intermediate_override_count,
            telemetry_latest_override_created_at=telemetry_latest_override_created_at,
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
            phash_report_exists=phash_report_exists,
            phash_artifact_dir=phash_artifact_dir,
        )

    def _build_steps(self, snap: WorkflowSnapshot) -> dict[str, StepSpec]:
        steps: dict[str, StepSpec] = {}

        steps["setup"] = StepSpec(
            key="setup",
            title="AI setup",
            subtitle="Runtime and the current AI culling model set in one workflow.",
            description=(
                "Choose GPU or CPU once. Image Triage installs the runtime together "
                "with CLIP, TOPIQ, and InsightFace quality models."
            ),
            status=STATUS_DONE if snap.runtime_ready else STATUS_BLOCKED,
            metrics=[
                ("Runtime", "Ready" if snap.runtime_ready else "Unavailable"),
                ("Culler source", snap.runtime_source or "—"),
                ("Model root", snap.model_root or "—"),
                ("CLIP model", snap.clip_model_label),
                ("Culling models", "CLIP · TOPIQ · InsightFace"),
                ("Current folder", snap.folder_path or "(none)"),
            ] + ([("Note", snap.runtime_note)] if snap.runtime_note else []),
            actions=[
                ActionSpec(
                    label="Set Up AI",
                    callback=lambda: self._invoke("_install_ai_runtime"),
                    primary=not snap.runtime_ready,
                    enabled=True,
                ),
                ActionSpec(
                    label="Uninstall AI Runtime & Models",
                    callback=lambda: self._invoke("_uninstall_ai_components"),
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

        index_status = STATUS_BLOCKED
        if not snap.runtime_ready or not snap.folder_open:
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
        steps["index"] = StepSpec(
            key="index",
            title="Cull & Score",
            subtitle="Run the current CLIP, TOPIQ, InsightFace, and duplicate-grouping pipeline.",
            description=(
                "This is the complete culling pass. It groups near duplicates with pHash, "
                "uses CLIP for visual scoring and categories, adds TOPIQ and InsightFace "
                "quality signals, clusters similar work, and produces a diversified ranking."
            ),
            status=index_status,
            metrics=index_metrics,
            actions=[
                ActionSpec(
                    label="Run Cull & Score",
                    callback=lambda: self._invoke("_run_ai_pipeline"),
                    primary=True,
                    enabled=snap.runtime_ready and snap.folder_open,
                ),
                ActionSpec(
                    label="Quick Rerank",
                    callback=lambda: self._invoke("_rerank_ai_pipeline"),
                    enabled=snap.can_rerank,
                    tooltip=(
                        "Reuses the existing ingest, categories, and clusters and recalculates the base ranking."
                        if not snap.can_rerank
                        else ""
                    ),
                ),
            ],
        )

        results_ready = bool(snap.can_rerank and snap.indexed_count)
        steps["review"] = StepSpec(
            key="review",
            title="Review Results",
            subtitle="Inspect the ranked cull in AI Review.",
            description=(
                "Review AI Pick, Keeper, Needs Review, and Reject buckets. The ranking "
                "comes directly from the current base-model pipeline and requires no "
                "training pass."
            ),
            status=STATUS_DONE if results_ready else STATUS_BLOCKED,
            metrics=[
                ("Ranked images", str(snap.indexed_count) if results_ready else "—"),
                ("Categories", "CLIP semantic categories"),
                ("Quality", "TOPIQ · InsightFace"),
                ("Duplicate handling", "pHash grouping and diversity penalties"),
            ],
            actions=[
                ActionSpec(
                    label="Open AI Review",
                    callback=lambda: self._invoke("_open_current_ai_review"),
                    primary=True,
                    enabled=results_ready,
                    tooltip="Run Cull & Score first." if not results_ready else "",
                ),
            ],
        )

        steps["apply"] = StepSpec(
            key="apply",
            title="Apply Decisions",
            subtitle="Use the reviewed ranking to organize the folder.",
            description=(
                "Move AI Picks to the winners folder, send clear rejects to the app recycle "
                "bin, or organize photos by the semantic categories created during the cull."
            ),
            status=STATUS_READY if results_ready else STATUS_BLOCKED,
            metrics=[
                ("Results", "Ready" if results_ready else "Run Cull & Score first"),
                ("Source", "Current base-model ranking"),
            ],
            actions=[
                ActionSpec(
                    label="Apply AI Decisions",
                    callback=lambda: self._invoke("_apply_ai_culling"),
                    primary=True,
                    enabled=results_ready,
                ),
                ActionSpec(
                    label="Sort Into Categories",
                    callback=lambda: self._invoke("_sort_images_into_semantic_folders"),
                    enabled=results_ready,
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
