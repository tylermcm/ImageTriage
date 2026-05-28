from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .aiculler_workflow import (
    aiculler_db_path,
    aiculler_rerank_readiness,
    aiculler_runtime_available,
    build_aiculler_workflow_paths,
    load_adapter_status_summary,
)

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


@dataclass
class WorkflowSnapshot:
    runtime_ready: bool
    folder_open: bool
    db_exists: bool
    indexed_count: int
    cluster_run_id: str
    can_rerank: bool
    label_count: int
    adapter_version: str
    adapter_created_at: str
    train_mae: float | None
    holdout_mae: float | None
    train_rank_lift: float | None
    scored_count: int
    folder_path: str = ""
    file_count: int = 0


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
        outer.addWidget(self._subtitle_label)

        self._description_label = QLabel()
        self._description_label.setWordWrap(True)
        self._description_label.setStyleSheet("color: #d4dbe4;")
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
        self._metrics_layout = QVBoxLayout(self._metrics_frame)
        self._metrics_layout.setContentsMargins(14, 12, 14, 12)
        self._metrics_layout.setSpacing(6)
        outer.addWidget(self._metrics_frame)

        outer.addStretch(1)

        self._action_row = QHBoxLayout()
        self._action_row.setSpacing(8)
        outer.addLayout(self._action_row)

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
            for label, value in step.metrics:
                row = QHBoxLayout()
                row.setSpacing(8)
                key_label = QLabel(label)
                key_label.setStyleSheet("color: #8d99ac;")
                key_label.setMinimumWidth(140)
                value_label = QLabel(value)
                value_label.setStyleSheet("color: #e6ecf4;")
                value_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                value_label.setWordWrap(True)
                row.addWidget(key_label, 0)
                row.addWidget(value_label, 1)
                wrapper = QWidget()
                wrapper.setLayout(row)
                self._metrics_layout.addWidget(wrapper)

        while self._action_row.count():
            item = self._action_row.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.deleteLater()
        self._action_row.addStretch(1)
        for action in step.actions:
            button = QPushButton(action.label)
            button.setEnabled(action.enabled)
            if action.tooltip:
                button.setToolTip(action.tooltip)
            if action.primary:
                button.setDefault(True)
                button.setStyleSheet(
                    "QPushButton {"
                    " background: #2f6fd6; color: white;"
                    " border-radius: 6px; padding: 7px 16px;"
                    " font-weight: 600;"
                    "} QPushButton:disabled { background: #3b4252; color: #7a8295; }"
                )
            else:
                button.setStyleSheet(
                    "QPushButton {"
                    " background: rgba(255, 255, 255, 0.07); color: #d4dbe4;"
                    " border: 1px solid rgba(255, 255, 255, 0.12);"
                    " border-radius: 6px; padding: 7px 14px;"
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
        self.resize(820, 540)
        self.setMinimumSize(720, 460)

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

        header_label = QLabel("AI Workflow")
        header_font = QFont()
        header_font.setPointSize(13)
        header_font.setBold(True)
        header_label.setFont(header_font)
        sidebar_layout.addWidget(header_label)

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
        root.addWidget(self._pages, 1)

        self._step_keys: list[str] = []
        self._page_widgets: dict[str, _StepPage] = {}
        self._build_pages()
        self.refresh()

    def _build_pages(self) -> None:
        for key, label in (
            ("setup", "1. Setup"),
            ("index", "2. Index & Score"),
            ("label", "3. Review Labels"),
            ("train", "4. Train Adapter"),
            ("evaluate", "5. Evaluate"),
            ("apply", "6. Rank & Apply"),
        ):
            self._step_keys.append(key)
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, key)
            self._step_list.addItem(item)
            page = _StepPage()
            self._page_widgets[key] = page
            self._pages.addWidget(page)
        if self._step_list.count():
            self._step_list.setCurrentRow(0)

    def _handle_step_selected(self, row: int) -> None:
        if 0 <= row < self._pages.count():
            self._pages.setCurrentIndex(row)

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
        try:
            runtime_ready = aiculler_runtime_available()
        except Exception:
            runtime_ready = False
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
        adapter_version = ""
        adapter_created_at = ""
        train_mae: float | None = None
        holdout_mae: float | None = None
        train_rank_lift: float | None = None
        scored_count = 0
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
        return WorkflowSnapshot(
            runtime_ready=runtime_ready,
            folder_open=bool(folder_path),
            db_exists=db_exists,
            indexed_count=indexed_count,
            cluster_run_id=cluster_run_id,
            can_rerank=can_rerank,
            label_count=label_count,
            adapter_version=adapter_version,
            adapter_created_at=adapter_created_at,
            train_mae=train_mae,
            holdout_mae=holdout_mae,
            train_rank_lift=train_rank_lift,
            scored_count=scored_count,
            folder_path=folder_path,
            file_count=file_count,
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
                ("Current folder", snap.folder_path or "(none)"),
            ],
            actions=[
                ActionSpec(
                    label="Open CLI-Culler folder",
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
                    label="Run AI Culler",
                    callback=lambda: self._invoke("_run_ai_pipeline"),
                    primary=True,
                    enabled=snap.runtime_ready and snap.folder_open,
                ),
                ActionSpec(
                    label="Quick Rerank",
                    callback=lambda: self._invoke("_rerank_ai_pipeline"),
                    enabled=snap.can_rerank,
                    tooltip=(
                        "Skips ingest/cluster — only available after a full Run AI Culler has populated the index."
                        if not snap.can_rerank
                        else ""
                    ),
                ),
            ],
        )

        label_status = STATUS_BLOCKED
        if index_status == STATUS_DONE:
            label_status = STATUS_DONE if snap.label_count > 0 else STATUS_READY
        steps["label"] = StepSpec(
            key="label",
            title="Review Labels",
            subtitle="Hand-rate a sampled set of candidates to teach the adapter.",
            description=(
                "Opens a filtered grid showing the most useful candidates "
                "(proportional category coverage + technical-disagreement). Use "
                "keys 1–5 (best / strong / maybe / weak / reject) — auto-advance "
                "moves to the next un-labeled candidate automatically."
            ),
            status=label_status,
            metrics=[
                ("Saved labels", str(snap.label_count)),
                ("Cluster run", snap.cluster_run_id or "—"),
            ],
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
        train_metrics: list[tuple[str, str]] = [
            ("Saved labels", str(snap.label_count)),
            ("Current adapter", snap.adapter_version or "untrained"),
        ]
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
                "Trains the adapter on every label saved for this folder. Training "
                "is fast — a few seconds per hundred labels — and produces a new "
                "model version you can evaluate or rank with."
            ),
            status=train_status,
            metrics=train_metrics,
            actions=[
                ActionSpec(
                    label="Train Adapter",
                    callback=lambda: self._invoke("_train_aiculler_adapter"),
                    primary=True,
                    enabled=snap.db_exists and snap.label_count > 0,
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

        apply_status = STATUS_BLOCKED
        if snap.adapter_version:
            apply_status = STATUS_DONE if snap.scored_count > 0 else STATUS_READY
        apply_metrics = [
            ("Adapter version", snap.adapter_version or "untrained"),
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
                "the recycle bin."
            ),
            status=apply_status,
            metrics=apply_metrics,
            actions=[
                ActionSpec(
                    label="Rank with Adapter",
                    callback=lambda: self._invoke("_rank_aiculler_adapter"),
                    primary=True,
                    enabled=bool(snap.adapter_version),
                ),
                ActionSpec(
                    label="Apply AI Culling",
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
