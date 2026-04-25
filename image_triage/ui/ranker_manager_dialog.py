from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..ai_training import RankerRunInfo
from ..shell_actions import open_in_file_explorer, open_with_default


@dataclass(slots=True, frozen=True)
class RankerCenterSummary:
    folder_path: str
    hidden_root: str
    pairwise_labels: int
    cluster_labels: int
    general_pairwise_labels: int
    general_cluster_labels: int
    general_source_folders: int
    general_retrain_status: str
    candidates_ready: bool
    prepared_ready: bool
    active_ranker_label: str
    active_profile_label: str
    active_reference_label: str
    saved_rankers: int
    has_active_checkpoint: bool
    can_run_full_pipeline: bool
    can_train: bool
    can_evaluate: bool


class RankerCenterDialog(QDialog):
    def __init__(
        self,
        *,
        summary: RankerCenterSummary,
        runs: tuple[RankerRunInfo, ...],
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._summary = summary
        self._runs = list(runs)
        self._requested_action = ""

        self.setWindowTitle("Ranker Center")
        self.resize(1120, 760)

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(16, 16, 16, 16)
        root_layout.setSpacing(14)

        intro = QLabel(
            "Use Ranker Center to see the current folder's AI-training status, launch the next step, and compare saved ranker versions."
        )
        intro.setObjectName("secondaryText")
        intro.setWordWrap(True)
        root_layout.addWidget(intro)

        summary_card = QFrame(self)
        summary_card.setObjectName("aiTrainingStatsCard")
        summary_layout = QGridLayout(summary_card)
        summary_layout.setContentsMargins(14, 14, 14, 14)
        summary_layout.setHorizontalSpacing(20)
        summary_layout.setVerticalSpacing(8)
        root_layout.addWidget(summary_card)

        summary_rows = (
            ("Folder", summary.folder_path or "No folder selected"),
            ("Hidden AI Workspace", summary.hidden_root or "Not created yet"),
            ("Pairwise Labels", str(summary.pairwise_labels)),
            ("Cluster Labels", str(summary.cluster_labels)),
            ("General Pairwise Labels", str(summary.general_pairwise_labels)),
            ("General Cluster Labels", str(summary.general_cluster_labels)),
            ("General Label Sources", str(summary.general_source_folders)),
            ("General Retrain", summary.general_retrain_status or "No General Use guidance yet"),
            ("Candidate Groups", _yes_no(summary.candidates_ready)),
            ("Prepared Training Data", _yes_no(summary.prepared_ready)),
            ("Active Ranker", summary.active_ranker_label),
            ("Active Profile", summary.active_profile_label),
            ("Reference Bank", summary.active_reference_label),
            ("Saved Rankers", str(summary.saved_rankers)),
        )
        for row_index, (key_text, value_text) in enumerate(summary_rows):
            key_label = QLabel(key_text, summary_card)
            key_label.setObjectName("mutedText")
            value_label = QLabel(value_text, summary_card)
            value_label.setObjectName("secondaryText")
            value_label.setWordWrap(True)
            value_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            summary_layout.addWidget(key_label, row_index, 0)
            summary_layout.addWidget(value_label, row_index, 1)

        actions_card = QFrame(self)
        actions_card.setObjectName("aiTrainingStatsCard")
        actions_layout = QVBoxLayout(actions_card)
        actions_layout.setContentsMargins(14, 14, 14, 14)
        actions_layout.setSpacing(10)
        root_layout.addWidget(actions_card)

        actions_title = QLabel("Quick Actions", actions_card)
        actions_title.setObjectName("secondaryText")
        actions_layout.addWidget(actions_title)

        action_rows = (
            (
                ("Collect Labels...", "collect_labels", True),
                ("Prepare Data", "prepare_data", True),
                ("Run Full Pipeline...", "run_full_pipeline", summary.can_run_full_pipeline),
            ),
            (
                ("Train...", "train", summary.can_train),
                ("Evaluate", "evaluate", summary.can_evaluate),
                ("Score", "score", summary.has_active_checkpoint),
            ),
        )
        for row_buttons in action_rows:
            row_layout = QHBoxLayout()
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(8)
            for label, action_id, enabled in row_buttons:
                button = QPushButton(label, actions_card)
                button.setEnabled(enabled)
                button.clicked.connect(lambda _checked=False, target=action_id: self._trigger_action(target))
                row_layout.addWidget(button)
            row_layout.addStretch(1)
            actions_layout.addLayout(row_layout)

        table_label = QLabel("Saved Rankers", self)
        table_label.setObjectName("secondaryText")
        root_layout.addWidget(table_label)

        self.table = QTableWidget(0, 10, self)
        self.table.setHorizontalHeaderLabels(
            [
                "Active",
                "Run",
                "Profile",
                "Created",
                "Best Epoch",
                "Pairwise Acc",
                "Top-1 Hit",
                "Fit",
                "Labels",
                "Reference",
            ]
        )
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        root_layout.addWidget(self.table, 1)

        details_card = QFrame(self)
        details_card.setObjectName("aiTrainingStatsCard")
        details_layout = QVBoxLayout(details_card)
        details_layout.setContentsMargins(14, 14, 14, 14)
        details_layout.setSpacing(10)
        root_layout.addWidget(details_card)

        details_title = QLabel("Selected Run", details_card)
        details_title.setObjectName("secondaryText")
        details_layout.addWidget(details_title)

        self.details_label = QLabel(details_card)
        self.details_label.setObjectName("mutedText")
        self.details_label.setWordWrap(True)
        self.details_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        details_layout.addWidget(self.details_label)

        button_row = QHBoxLayout()
        button_row.setContentsMargins(0, 0, 0, 0)
        button_row.setSpacing(8)
        details_layout.addLayout(button_row)

        self.open_folder_button = QPushButton("Open Run Folder", details_card)
        self.open_train_log_button = QPushButton("Open Training Log", details_card)
        self.open_eval_log_button = QPushButton("Open Eval Log", details_card)
        self.use_default_button = QPushButton("Use Default Model", details_card)
        self.use_selected_button = QPushButton("Use Selected Ranker", details_card)
        self.close_button = QPushButton("Close", details_card)

        for button in (
            self.open_folder_button,
            self.open_train_log_button,
            self.open_eval_log_button,
            self.use_default_button,
            self.use_selected_button,
            self.close_button,
        ):
            button_row.addWidget(button)
        button_row.addStretch(1)

        self.open_folder_button.clicked.connect(self._handle_open_folder)
        self.open_train_log_button.clicked.connect(self._handle_open_train_log)
        self.open_eval_log_button.clicked.connect(self._handle_open_eval_log)
        self.use_default_button.clicked.connect(lambda: self._trigger_action("use_default"))
        self.use_selected_button.clicked.connect(lambda: self._trigger_action("use_selected"))
        self.close_button.clicked.connect(self.reject)
        self.table.itemSelectionChanged.connect(self._refresh_selection_state)
        self.table.itemDoubleClicked.connect(lambda _item: self._trigger_action("use_selected"))

        self._populate_table()
        self._refresh_selection_state()

    @property
    def requested_action(self) -> str:
        return self._requested_action

    def selected_run(self) -> RankerRunInfo | None:
        row = self.table.currentRow()
        if row < 0 or row >= len(self._runs):
            return None
        return self._runs[row]

    def _trigger_action(self, action_id: str) -> None:
        self._requested_action = action_id
        self.accept()

    def _populate_table(self) -> None:
        self.table.setRowCount(len(self._runs))
        for row, run in enumerate(self._runs):
            values = (
                "Yes" if run.is_active else "",
                run.display_name,
                run.profile_label,
                _format_created_at(run.created_at),
                str(run.best_epoch) if run.best_epoch is not None else "n/a",
                _format_metric(run.best_validation_accuracy),
                _format_metric(run.cluster_top1_hit_rate),
                run.fit_diagnosis.label,
                f"P {run.pairwise_labels} | C {run.cluster_labels}",
                Path(run.reference_bank_path).name if run.reference_bank_path else "Plain ranker",
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 0:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.table.setItem(row, column, item)
        if self._runs:
            active_index = next((index for index, run in enumerate(self._runs) if run.is_active), 0)
            self.table.selectRow(active_index)
            self.table.resizeColumnsToContents()
        else:
            self.details_label.setText("No saved rankers yet. Collect labels, then train a model to start building versions.")

    def _refresh_selection_state(self) -> None:
        selected = self.selected_run()
        self.use_selected_button.setEnabled(selected is not None and selected.checkpoint_path is not None)
        self.open_folder_button.setEnabled(selected is not None and selected.run_dir.exists())
        self.open_train_log_button.setEnabled(selected is not None and selected.train_log_path is not None and selected.train_log_path.exists())
        self.open_eval_log_button.setEnabled(selected is not None and selected.evaluation_log_path is not None and selected.evaluation_log_path.exists())
        if selected is None:
            if self._runs:
                self.details_label.setText("Select a ranker run to inspect its checkpoint, logs, and metrics.")
            return
        details = [
            f"Run: {selected.display_name}",
            f"Profile: {selected.profile_label}",
            f"Created: {_format_created_at(selected.created_at)}",
            f"Checkpoint: {selected.checkpoint_path or 'Not available'}",
            f"Run Folder: {selected.run_dir}",
            f"Training Health: {selected.fit_diagnosis.label}",
            f"Health Summary: {selected.fit_diagnosis.summary}",
            f"Try Next: {selected.fit_diagnosis.remedy}",
        ]
        if selected.metrics_path is not None:
            details.append(f"Training Metrics: {selected.metrics_path}")
        if selected.evaluation_metrics_path is not None:
            details.append(f"Evaluation Metrics: {selected.evaluation_metrics_path}")
        if selected.train_log_path is not None:
            details.append(f"Training Log: {selected.train_log_path}")
        if selected.evaluation_log_path is not None:
            details.append(f"Eval Log: {selected.evaluation_log_path}")
        if selected.reference_bank_path:
            details.append(f"Reference Bank: {selected.reference_bank_path}")
        details.append("Status: Active" if selected.is_active else "Status: Saved version")
        self.details_label.setText("\n".join(details))

    def _handle_open_folder(self) -> None:
        selected = self.selected_run()
        if selected is None:
            return
        open_in_file_explorer(str(selected.run_dir))

    def _handle_open_train_log(self) -> None:
        selected = self.selected_run()
        if selected is None or selected.train_log_path is None or not selected.train_log_path.exists():
            return
        open_with_default(str(selected.train_log_path))

    def _handle_open_eval_log(self) -> None:
        selected = self.selected_run()
        if selected is None:
            return
        log_path = selected.evaluation_log_path
        if log_path is None or not log_path.exists():
            metrics_path = selected.evaluation_metrics_path
            if metrics_path is None or not metrics_path.exists():
                return
            open_with_default(str(metrics_path))
            return
        open_with_default(str(log_path))


RankerManagerDialog = RankerCenterDialog


def _format_metric(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.3f}"


def _format_created_at(value: str) -> str:
    return value.replace("T", " ") if value else "n/a"


def _yes_no(value: bool) -> str:
    return "Ready" if value else "Not Yet"
