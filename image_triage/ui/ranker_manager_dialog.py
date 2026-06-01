from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from ..ai_training import TrainingSourceInfo


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
    """Choose labeled source folders to use as held-out evaluation sets."""

    def __init__(
        self,
        *,
        sources: tuple[TrainingSourceInfo, ...],
        title_text: str = "Evaluation Source",
        summary_text: str = "Choose labeled folders to evaluate against. This does not add those sources to training.",
        default_folders: tuple[str, ...] = (),
        allow_multiple: bool = True,
        show_evaluate_all: bool = True,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._sources = list(sources)
        self._default_keys = {str(Path(folder).expanduser().resolve()).casefold() for folder in default_folders if str(folder).strip()}
        self._allow_multiple = allow_multiple
        self._evaluate_all_ready = False
        self.setWindowTitle(title_text)
        self.setModal(True)
        self.resize(760, 430)
        self.setMinimumSize(660, 340)

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(14, 14, 14, 14)
        root_layout.setSpacing(10)

        title = QLabel(title_text, self)
        title.setObjectName("dialogTitle")
        root_layout.addWidget(title)

        summary = QLabel(summary_text, self)
        summary.setObjectName("mutedText")
        summary.setWordWrap(True)
        root_layout.addWidget(summary)

        self.table = QTableWidget(0, 6, self)
        self.table.setHorizontalHeaderLabels(["Evaluate", "Source", "Pairwise", "Cluster", "AI Disputes", "Prepared"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for column in range(2, 6):
            self.table.horizontalHeader().setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        self.table.itemDoubleClicked.connect(lambda _item: self.accept() if self.evaluate_button.isEnabled() else None)
        root_layout.addWidget(self.table, 1)

        button_box = QDialogButtonBox(self)
        self.evaluate_button = QPushButton("Evaluate Selected", self)
        self.evaluate_all_button: QPushButton | None = QPushButton("Evaluate All Ready", self) if show_evaluate_all else None
        cancel_button = QPushButton("Cancel", self)
        button_box.addButton(cancel_button, QDialogButtonBox.ButtonRole.RejectRole)
        if self.evaluate_all_button is not None:
            button_box.addButton(self.evaluate_all_button, QDialogButtonBox.ButtonRole.ActionRole)
        button_box.addButton(self.evaluate_button, QDialogButtonBox.ButtonRole.AcceptRole)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        if self.evaluate_all_button is not None:
            self.evaluate_all_button.clicked.connect(self._accept_all_ready)
        root_layout.addWidget(button_box)

        self.table.itemChanged.connect(self._handle_item_changed)
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
                if source.prepared_ready and _source_has_eval_labels(source)
            )
        checked = tuple(
            source
            for row, source in enumerate(self._sources)
            if self._is_row_checked(row) and source.prepared_ready and _source_has_eval_labels(source)
        )
        if checked:
            return checked
        selected = self.selected_source()
        return (selected,) if selected is not None else ()

    def _populate_table(self) -> None:
        self.table.setRowCount(len(self._sources))
        for row, source in enumerate(self._sources):
            evaluate_item = QTableWidgetItem("")
            evaluate_item.setFlags(
                Qt.ItemFlag.ItemIsEnabled
                | Qt.ItemFlag.ItemIsUserCheckable
                | Qt.ItemFlag.ItemIsSelectable
            )
            try:
                source_key = str(Path(source.folder).expanduser().resolve()).casefold()
            except OSError:
                source_key = source.folder.casefold()
            evaluate_item.setCheckState(Qt.CheckState.Checked if source_key in self._default_keys else Qt.CheckState.Unchecked)
            self.table.setItem(row, 0, evaluate_item)

            values = (
                _short_path(source.folder, max_chars=88),
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
        if self._sources:
            self.table.selectRow(0)

    def _refresh_button_state(self) -> None:
        checked_ready = any(
            self._is_row_checked(row)
            and source.prepared_ready
            and (source.pairwise_labels > 0 or source.cluster_labels > 0)
            for row, source in enumerate(self._sources)
        )
        selected = self.selected_source()
        selected_ready = (
            selected is not None
            and selected.prepared_ready
            and _source_has_eval_labels(selected)
        )
        self.evaluate_button.setEnabled(checked_ready or selected_ready)
        if self.evaluate_all_button is not None:
            self.evaluate_all_button.setEnabled(
                sum(
                    1
                    for source in self._sources
                    if source.prepared_ready and _source_has_eval_labels(source)
                )
                >= 2
            )

    def _accept_all_ready(self) -> None:
        self._evaluate_all_ready = True
        self.accept()

    def _is_row_checked(self, row: int) -> bool:
        item = self.table.item(row, 0)
        return item is not None and item.checkState() == Qt.CheckState.Checked

    def _handle_item_changed(self, item: QTableWidgetItem) -> None:
        if not self._allow_multiple and item.column() == 0 and item.checkState() == Qt.CheckState.Checked:
            previous_state = self.table.blockSignals(True)
            try:
                for row in range(self.table.rowCount()):
                    if row == item.row():
                        continue
                    other = self.table.item(row, 0)
                    if other is not None:
                        other.setCheckState(Qt.CheckState.Unchecked)
            finally:
                self.table.blockSignals(previous_state)
        self._refresh_button_state()


def _source_has_eval_labels(source: TrainingSourceInfo) -> bool:
    return source.pairwise_labels > 0 or source.cluster_labels > 0 or source.disagreement_pair_labels > 0


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
