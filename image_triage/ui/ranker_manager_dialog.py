from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..ai_training import RankerRunInfo, TrainingSourceInfo
from ..shell_actions import open_in_file_explorer, open_with_default


@dataclass(slots=True, frozen=True)
class RankerCenterSummary:
    folder_path: str
    hidden_root: str
    pairwise_labels: int
    cluster_labels: int
    disagreement_events: int
    disagreement_pair_labels: int
    general_pairwise_labels: int
    general_cluster_labels: int
    general_disagreement_pair_labels: int
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
    """Keyboard-first command center for ranker labeling, training, and versions."""

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
        self._action_buttons: dict[str, QPushButton] = {}
        self._action_enabled: dict[str, bool] = {}
        self._shortcuts: list[QShortcut] = []

        self.setWindowTitle("Ranker Workflow")
        self.setMinimumSize(860, 560)
        self.resize(1040, 660)

        root_layout = QHBoxLayout(self)
        root_layout.setContentsMargins(12, 12, 12, 12)
        root_layout.setSpacing(12)

        sidebar = QFrame(self)
        sidebar.setObjectName("aiTrainingStatsCard")
        sidebar.setFixedWidth(230)
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(14, 14, 14, 14)
        sidebar_layout.setSpacing(10)
        root_layout.addWidget(sidebar)

        title = QLabel("Ranker Workflow", sidebar)
        title.setObjectName("dialogTitle")
        sidebar_layout.addWidget(title)

        folder_label = QLabel(_short_path(summary.folder_path) or "No folder selected", sidebar)
        folder_label.setObjectName("mutedText")
        folder_label.setWordWrap(True)
        folder_label.setToolTip(summary.folder_path)
        sidebar_layout.addWidget(folder_label)

        next_step = QLabel(_next_step_text(summary), sidebar)
        next_step.setObjectName("secondaryText")
        next_step.setWordWrap(True)
        sidebar_layout.addWidget(next_step)

        self._add_action_button(sidebar_layout, "1", "Collect Labels", "collect_labels", True)
        self._add_action_button(sidebar_layout, "2", "Prepare Data", "prepare_data", True)
        self._add_action_button(sidebar_layout, "3", "Train Ranker", "train", summary.can_train)
        self._add_action_button(sidebar_layout, "4", "Evaluate", "evaluate", summary.can_evaluate)
        self._add_action_button(sidebar_layout, "5", "Score Folder", "score", summary.has_active_checkpoint)
        self._add_action_button(sidebar_layout, "F", "Full Pipeline", "run_full_pipeline", summary.can_run_full_pipeline)
        self._add_action_button(sidebar_layout, "S", "Sources", "sources", True)
        sidebar_layout.addStretch(1)
        self._add_action_button(sidebar_layout, "D", "Use Default", "use_default", True)

        main = QWidget(self)
        main_layout = QVBoxLayout(main)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(12)
        root_layout.addWidget(main, 1)

        status_grid = QGridLayout()
        status_grid.setContentsMargins(0, 0, 0, 0)
        status_grid.setHorizontalSpacing(10)
        status_grid.setVerticalSpacing(10)
        main_layout.addLayout(status_grid)

        status_grid.addWidget(
            _metric_card(
                self,
                "Active Ranker",
                summary.active_ranker_label,
                f"{summary.active_profile_label} | Reference: {summary.active_reference_label}",
            ),
            0,
            0,
        )
        status_grid.addWidget(
            _metric_card(
                self,
                "Current Folder Labels",
                f"{summary.pairwise_labels} pairwise / {summary.cluster_labels} cluster",
                f"{summary.disagreement_pair_labels} AI dispute pairs | {summary.disagreement_events} captured",
            ),
            0,
            1,
        )
        status_grid.addWidget(
            _metric_card(
                self,
                "General Use Pool",
                f"{summary.general_pairwise_labels} pairwise / {summary.general_cluster_labels} cluster",
                f"{summary.general_disagreement_pair_labels} AI dispute pairs | {summary.general_source_folders} source folder(s)",
            ),
            0,
            2,
        )

        if summary.general_retrain_status:
            guidance = QLabel(summary.general_retrain_status, self)
            guidance.setObjectName("mutedText")
            guidance.setWordWrap(True)
            main_layout.addWidget(guidance)

        table_header = QHBoxLayout()
        table_header.setContentsMargins(0, 0, 0, 0)
        table_title = QLabel("Saved Rankers", self)
        table_title.setObjectName("sectionLabel")
        table_header.addWidget(table_title)
        table_header.addStretch(1)
        hint = QLabel("Enter uses selected | 1-5 runs workflow steps", self)
        hint.setObjectName("mutedText")
        table_header.addWidget(hint)
        main_layout.addLayout(table_header)

        self.table = QTableWidget(0, 7, self)
        self.table.setHorizontalHeaderLabels(["Active", "Run", "Profile", "Fit", "Labels", "Top-1", "Created"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(False)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(6, QHeaderView.ResizeMode.ResizeToContents)
        main_layout.addWidget(self.table, 1)

        details_card = QFrame(self)
        details_card.setObjectName("aiTrainingStatsCard")
        details_layout = QVBoxLayout(details_card)
        details_layout.setContentsMargins(12, 12, 12, 12)
        details_layout.setSpacing(8)
        main_layout.addWidget(details_card)

        details_title = QLabel("Selected Run", details_card)
        details_title.setObjectName("sectionLabel")
        details_layout.addWidget(details_title)

        self.details_label = QLabel(details_card)
        self.details_label.setObjectName("secondaryText")
        self.details_label.setWordWrap(True)
        self.details_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        details_layout.addWidget(self.details_label)

        button_row = QHBoxLayout()
        button_row.setContentsMargins(0, 0, 0, 0)
        button_row.setSpacing(8)
        details_layout.addLayout(button_row)

        self.use_selected_button = QPushButton("Use Selected", details_card)
        self.open_folder_button = QPushButton("Open Folder", details_card)
        self.open_train_log_button = QPushButton("Training Log", details_card)
        self.open_eval_log_button = QPushButton("Eval Log", details_card)
        self.close_button = QPushButton("Close", details_card)

        for button in (
            self.use_selected_button,
            self.open_folder_button,
            self.open_train_log_button,
            self.open_eval_log_button,
        ):
            button_row.addWidget(button)
        button_row.addStretch(1)
        button_row.addWidget(self.close_button)

        self.use_selected_button.clicked.connect(lambda: self._trigger_action("use_selected"))
        self.open_folder_button.clicked.connect(self._handle_open_folder)
        self.open_train_log_button.clicked.connect(self._handle_open_train_log)
        self.open_eval_log_button.clicked.connect(self._handle_open_eval_log)
        self.close_button.clicked.connect(self.reject)
        self.table.itemSelectionChanged.connect(self._refresh_selection_state)
        self.table.itemDoubleClicked.connect(lambda _item: self._trigger_action("use_selected"))

        self._install_shortcuts()
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

    def _add_action_button(
        self,
        layout: QVBoxLayout,
        shortcut: str,
        label: str,
        action_id: str,
        enabled: bool,
    ) -> None:
        button = QPushButton(f"{shortcut}  {label}", self)
        button.setEnabled(enabled)
        button.setMinimumHeight(34)
        button.clicked.connect(lambda _checked=False, target=action_id: self._trigger_action(target))
        layout.addWidget(button)
        self._action_buttons[action_id] = button
        self._action_enabled[action_id] = enabled

    def _install_shortcuts(self) -> None:
        shortcuts = {
            "1": "collect_labels",
            "2": "prepare_data",
            "3": "train",
            "4": "evaluate",
            "5": "score",
            "F": "run_full_pipeline",
            "S": "sources",
            "D": "use_default",
            "Return": "use_selected",
            "Enter": "use_selected",
        }
        for key, action_id in shortcuts.items():
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.activated.connect(lambda target=action_id: self._trigger_action(target))
            self._shortcuts.append(shortcut)

    def _trigger_action(self, action_id: str) -> None:
        if action_id not in {"use_selected", "use_default"} and not self._action_enabled.get(action_id, True):
            return
        if action_id == "use_selected":
            selected = self.selected_run()
            if selected is None or selected.checkpoint_path is None:
                return
        self._requested_action = action_id
        self.accept()

    def _populate_table(self) -> None:
        self.table.setRowCount(len(self._runs))
        for row, run in enumerate(self._runs):
            values = (
                "Active" if run.is_active else "",
                run.display_name,
                run.profile_label,
                run.fit_diagnosis.label,
                f"{run.pairwise_labels} / {run.cluster_labels} / {run.disagreement_pair_labels}",
                _format_metric(run.cluster_top1_hit_rate),
                _format_created_at(run.created_at),
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column in {0, 4, 5}:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                item.setToolTip(self._tooltip_for_run(run))
                self.table.setItem(row, column, item)
        if self._runs:
            active_index = next((index for index, run in enumerate(self._runs) if run.is_active), 0)
            self.table.selectRow(active_index)
        else:
            self.details_label.setText("No saved rankers yet. Collect labels, then train a version.")

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
            f"{selected.display_name} | {selected.profile_label}",
            f"Fit: {selected.fit_diagnosis.label}. {selected.fit_diagnosis.summary}",
            f"Best epoch: {selected.best_epoch if selected.best_epoch is not None else '-'} | Pairwise accuracy: {_format_metric(selected.best_validation_accuracy)} | Top-1 hit: {_format_metric(selected.cluster_top1_hit_rate)}",
            f"Labels: {selected.pairwise_labels} pairwise / {selected.cluster_labels} cluster / {selected.disagreement_pair_labels} AI dispute pairs",
            f"Reference: {Path(selected.reference_bank_path).name if selected.reference_bank_path else 'None'}",
            f"Checkpoint: {_short_path(str(selected.checkpoint_path)) if selected.checkpoint_path else 'Not available'}",
        ]
        if selected.fit_diagnosis.remedy:
            details.append(f"Next: {selected.fit_diagnosis.remedy}")
        self.details_label.setText("\n".join(details))

    def _tooltip_for_run(self, run: RankerRunInfo) -> str:
        return "\n".join(
            (
                run.display_name,
                f"Profile: {run.profile_label}",
                f"Checkpoint: {run.checkpoint_path or 'Not available'}",
                f"Run folder: {run.run_dir}",
            )
        )

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


class TrainingSourcesDialog(QDialog):
    """Select which registered label sources feed the General Use training pool."""

    def __init__(self, *, sources: tuple[TrainingSourceInfo, ...], parent=None) -> None:
        super().__init__(parent)
        self._sources = list(sources)
        self.setWindowTitle("Training Sources")
        self.setModal(True)
        self.resize(760, 460)
        self.setMinimumSize(660, 360)

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(14, 14, 14, 14)
        root_layout.setSpacing(10)

        title = QLabel("Training Sources", self)
        title.setObjectName("dialogTitle")
        root_layout.addWidget(title)

        summary = QLabel("Choose which labeled folders contribute to the General Use ranker.", self)
        summary.setObjectName("mutedText")
        summary.setWordWrap(True)
        root_layout.addWidget(summary)

        self.table = QTableWidget(0, 6, self)
        self.table.setHorizontalHeaderLabels(["Use", "Source", "Pairwise", "Cluster", "AI Disputes", "Prepared"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for column in range(2, 6):
            self.table.horizontalHeader().setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        root_layout.addWidget(self.table, 1)

        button_box = QDialogButtonBox(self)
        self.select_all_button = QPushButton("Select All", self)
        self.select_none_button = QPushButton("Select None", self)
        save_button = QPushButton("Save", self)
        cancel_button = QPushButton("Cancel", self)
        button_box.addButton(self.select_all_button, QDialogButtonBox.ButtonRole.ActionRole)
        button_box.addButton(self.select_none_button, QDialogButtonBox.ButtonRole.ActionRole)
        button_box.addButton(cancel_button, QDialogButtonBox.ButtonRole.RejectRole)
        button_box.addButton(save_button, QDialogButtonBox.ButtonRole.AcceptRole)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        self.select_all_button.clicked.connect(lambda: self._set_all_checked(True))
        self.select_none_button.clicked.connect(lambda: self._set_all_checked(False))
        root_layout.addWidget(button_box)

        self._populate_table()

    def selected_enabled_by_namespace(self) -> dict[str, bool]:
        states: dict[str, bool] = {}
        for row, source in enumerate(self._sources):
            item = self.table.item(row, 0)
            states[source.namespace] = item.checkState() == Qt.CheckState.Checked if item is not None else source.enabled
        return states

    def _populate_table(self) -> None:
        self.table.setRowCount(len(self._sources))
        for row, source in enumerate(self._sources):
            use_item = QTableWidgetItem("")
            use_item.setFlags(
                Qt.ItemFlag.ItemIsEnabled
                | Qt.ItemFlag.ItemIsUserCheckable
                | Qt.ItemFlag.ItemIsSelectable
            )
            use_item.setCheckState(Qt.CheckState.Checked if source.enabled else Qt.CheckState.Unchecked)
            self.table.setItem(row, 0, use_item)

            values = (
                _short_path(source.folder, max_chars=78),
                str(source.pairwise_labels),
                str(source.cluster_labels),
                str(source.disagreement_pair_labels),
                _ready_text(source.prepared_ready),
            )
            for column, value in enumerate(values, start=1):
                item = QTableWidgetItem(value)
                item.setToolTip(source.folder if column == 1 else "")
                if column >= 2:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.table.setItem(row, column, item)

    def _set_all_checked(self, checked: bool) -> None:
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item is not None:
                item.setCheckState(state)


class PrepareTrainingSourcesDialog(QDialog):
    """Choose which source folders should have training artifacts prepared."""

    def __init__(
        self,
        *,
        sources: tuple[TrainingSourceInfo, ...],
        default_folders: tuple[str, ...] = (),
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._sources = list(sources)
        self._default_keys = {str(Path(folder).expanduser().resolve()).casefold() for folder in default_folders if str(folder).strip()}
        self._browse_folder = ""
        self.setWindowTitle("Prepare Training Data")
        self.setModal(True)
        self.resize(780, 430)
        self.setMinimumSize(660, 340)

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(14, 14, 14, 14)
        root_layout.setSpacing(10)

        title = QLabel("Prepare Training Data", self)
        title.setObjectName("dialogTitle")
        root_layout.addWidget(title)

        summary = QLabel(
            "Choose the folder sources to prepare. This builds embeddings/artifacts for the selected source labels.",
            self,
        )
        summary.setObjectName("mutedText")
        summary.setWordWrap(True)
        root_layout.addWidget(summary)

        self.table = QTableWidget(0, 6, self)
        self.table.setHorizontalHeaderLabels(["Prepare", "Source", "Pairwise", "Cluster", "AI Disputes", "Status"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for column in range(2, 6):
            self.table.horizontalHeader().setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        root_layout.addWidget(self.table, 1)

        button_box = QDialogButtonBox(self)
        self.prepare_button = QPushButton("Prepare Selected", self)
        browse_button = QPushButton("Browse Folder...", self)
        cancel_button = QPushButton("Cancel", self)
        button_box.addButton(browse_button, QDialogButtonBox.ButtonRole.ActionRole)
        button_box.addButton(cancel_button, QDialogButtonBox.ButtonRole.RejectRole)
        button_box.addButton(self.prepare_button, QDialogButtonBox.ButtonRole.AcceptRole)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        browse_button.clicked.connect(self._browse_for_folder)
        root_layout.addWidget(button_box)

        self.table.itemChanged.connect(lambda _item: self._refresh_button_state())
        self._populate_table()
        self._refresh_button_state()

    def selected_folders(self) -> tuple[str, ...]:
        if self._browse_folder:
            return (self._browse_folder,)
        folders: list[str] = []
        for row, source in enumerate(self._sources):
            item = self.table.item(row, 0)
            if item is not None and item.checkState() == Qt.CheckState.Checked:
                folders.append(source.folder)
        return tuple(folders)

    def _populate_table(self) -> None:
        self.table.setRowCount(len(self._sources))
        for row, source in enumerate(self._sources):
            prepare_item = QTableWidgetItem("")
            prepare_item.setFlags(
                Qt.ItemFlag.ItemIsEnabled
                | Qt.ItemFlag.ItemIsUserCheckable
                | Qt.ItemFlag.ItemIsSelectable
            )
            try:
                source_key = str(Path(source.folder).expanduser().resolve()).casefold()
            except OSError:
                source_key = source.folder.casefold()
            prepare_item.setCheckState(Qt.CheckState.Checked if source_key in self._default_keys else Qt.CheckState.Unchecked)
            self.table.setItem(row, 0, prepare_item)

            status = _prepare_status_text(source)
            values = (
                _short_path(source.folder, max_chars=88),
                str(source.pairwise_labels),
                str(source.cluster_labels),
                str(source.disagreement_pair_labels),
                status,
            )
            for column, value in enumerate(values, start=1):
                item = QTableWidgetItem(value)
                item.setToolTip(source.folder if column == 1 else "")
                if column >= 2:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.table.setItem(row, column, item)

    def _refresh_button_state(self) -> None:
        self.prepare_button.setEnabled(bool(self.selected_folders()))

    def _browse_for_folder(self) -> None:
        start_folder = self._sources[0].folder if self._sources else str(Path.home())
        selected = QFileDialog.getExistingDirectory(self, "Choose Folder To Prepare", start_folder)
        if not selected:
            return
        self._browse_folder = selected
        self.accept()


class EvaluationSourceDialog(QDialog):
    """Choose one labeled source folder to use as a held-out evaluation set."""

    def __init__(self, *, sources: tuple[TrainingSourceInfo, ...], parent=None) -> None:
        super().__init__(parent)
        self._sources = list(sources)
        self._evaluate_all_ready = False
        self.setWindowTitle("Evaluation Source")
        self.setModal(True)
        self.resize(760, 430)
        self.setMinimumSize(660, 340)

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(14, 14, 14, 14)
        root_layout.setSpacing(10)

        title = QLabel("Evaluation Source", self)
        title.setObjectName("dialogTitle")
        root_layout.addWidget(title)

        summary = QLabel("Choose the labeled folder to evaluate the active ranker against. This does not add that source to training.", self)
        summary.setObjectName("mutedText")
        summary.setWordWrap(True)
        root_layout.addWidget(summary)

        self.table = QTableWidget(0, 5, self)
        self.table.setHorizontalHeaderLabels(["Source", "Pairwise", "Cluster", "AI Disputes", "Prepared"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in range(1, 5):
            self.table.horizontalHeader().setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        self.table.itemDoubleClicked.connect(lambda _item: self.accept())
        root_layout.addWidget(self.table, 1)

        button_box = QDialogButtonBox(self)
        self.evaluate_button = QPushButton("Evaluate", self)
        self.evaluate_all_button = QPushButton("Evaluate All Ready", self)
        cancel_button = QPushButton("Cancel", self)
        button_box.addButton(cancel_button, QDialogButtonBox.ButtonRole.RejectRole)
        button_box.addButton(self.evaluate_all_button, QDialogButtonBox.ButtonRole.ActionRole)
        button_box.addButton(self.evaluate_button, QDialogButtonBox.ButtonRole.AcceptRole)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        self.evaluate_all_button.clicked.connect(self._accept_all_ready)
        root_layout.addWidget(button_box)

        self.table.itemSelectionChanged.connect(self._refresh_button_state)
        self._populate_table()
        self._refresh_button_state()

    def selected_source(self) -> TrainingSourceInfo | None:
        row = self.table.currentRow()
        if row < 0 or row >= len(self._sources):
            return None
        return self._sources[row]

    def selected_sources(self) -> tuple[TrainingSourceInfo, ...]:
        if self._evaluate_all_ready:
            return tuple(
                source
                for source in self._sources
                if source.prepared_ready and (source.pairwise_labels > 0 or source.cluster_labels > 0)
            )
        selected = self.selected_source()
        return (selected,) if selected is not None else ()

    def _populate_table(self) -> None:
        self.table.setRowCount(len(self._sources))
        for row, source in enumerate(self._sources):
            values = (
                _short_path(source.folder, max_chars=88),
                str(source.pairwise_labels),
                str(source.cluster_labels),
                str(source.disagreement_pair_labels),
                _ready_text(source.prepared_ready),
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(source.folder if column == 0 else "")
                if column >= 1:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.table.setItem(row, column, item)
        if self._sources:
            self.table.selectRow(0)

    def _refresh_button_state(self) -> None:
        selected = self.selected_source()
        self.evaluate_button.setEnabled(
            selected is not None
            and selected.prepared_ready
            and (selected.pairwise_labels > 0 or selected.cluster_labels > 0)
        )
        self.evaluate_all_button.setEnabled(
            sum(
                1
                for source in self._sources
                if source.prepared_ready and (source.pairwise_labels > 0 or source.cluster_labels > 0)
            )
            >= 2
        )

    def _accept_all_ready(self) -> None:
        self._evaluate_all_ready = True
        self.accept()


def _metric_card(parent: QWidget, title: str, value: str, detail: str) -> QFrame:
    frame = QFrame(parent)
    frame.setObjectName("aiTrainingStatsCard")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(12, 12, 12, 12)
    layout.setSpacing(5)
    title_label = QLabel(title, frame)
    title_label.setObjectName("mutedText")
    value_label = QLabel(value or "-", frame)
    value_label.setObjectName("secondaryText")
    value_label.setWordWrap(True)
    detail_label = QLabel(detail or "-", frame)
    detail_label.setObjectName("mutedText")
    detail_label.setWordWrap(True)
    layout.addWidget(title_label)
    layout.addWidget(value_label)
    layout.addWidget(detail_label)
    return frame


def _next_step_text(summary: RankerCenterSummary) -> str:
    if summary.can_train and not summary.has_active_checkpoint:
        return "Next: train a ranker from your saved labels."
    if summary.has_active_checkpoint and summary.can_evaluate:
        return "Next: evaluate or score the current folder."
    if summary.candidates_ready:
        return "Next: collect more labels or train."
    return "Next: collect labels for this folder."


def _format_metric(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value:.3f}"


def _format_created_at(value: str) -> str:
    if not value:
        return "-"
    return value.replace("T", " ")[:19]


def _ready_text(value: bool) -> str:
    return "Ready" if value else "Not ready"


def _prepare_status_text(source: TrainingSourceInfo) -> str:
    if source.prepared_ready:
        return "Ready"
    if source.pairwise_labels > 0 or source.cluster_labels > 0 or source.disagreement_pair_labels > 0:
        return "Needs prepare"
    return "No labels"


def _short_path(value: str, *, max_chars: int = 56) -> str:
    text = str(value or "")
    if len(text) <= max_chars:
        return text
    return f"...{text[-max_chars + 3:]}"
