from __future__ import annotations

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..batch_rename import BatchRenamePreview, BatchRenameRules, RenameCaseMode, build_batch_rename_preview
from ..models import ImageRecord


class BatchRenameDialog(QDialog):
    PREVIEW_ROW_LIMIT = 1200

    def __init__(self, records: list[ImageRecord], *, title: str, scope_label: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.resize(920, 720)

        self._records = list(records)
        self._sequence_required = len(self._records) > 1
        self._latest_preview = build_batch_rename_preview([], BatchRenameRules())

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(16, 16, 16, 16)
        root_layout.setSpacing(12)

        intro = QLabel(
            f"Preview filename changes for {len(self._records)} image bundle(s). "
            "Extensions stay intact, bundle companions follow the primary rename, and apply stays disabled until the preview is clean. "
            "Leave New Name blank to keep each current base name and just use the other modifiers."
        )
        if self._sequence_required:
            intro.setText(f"{intro.text()} Sequence is required for multi-image batches so every target name stays unique.")
        intro.setWordWrap(True)
        intro.setObjectName("secondaryText")
        root_layout.addWidget(intro)

        scope = QLabel(scope_label)
        scope.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        scope.setObjectName("mutedText")
        scope.setWordWrap(True)
        root_layout.addWidget(scope)

        controls_group = QGroupBox("Rename Rules")
        controls_layout = QVBoxLayout(controls_group)
        controls_layout.setContentsMargins(12, 14, 12, 12)
        controls_layout.setSpacing(12)

        top_row = QWidget(self)
        top_form = QFormLayout(top_row)
        top_form.setContentsMargins(0, 0, 0, 0)
        top_form.setSpacing(10)

        self.new_name_field = QLineEdit()
        self.new_name_field.setPlaceholderText("Optional base name (without extension)")
        top_form.addRow("New Name", self.new_name_field)

        self.prefix_field = QLineEdit()
        self.prefix_field.setPlaceholderText("Optional prefix")
        top_form.addRow("Prefix", self.prefix_field)

        self.suffix_field = QLineEdit()
        self.suffix_field.setPlaceholderText("Optional suffix")
        top_form.addRow("Suffix", self.suffix_field)

        self.case_combo = QComboBox()
        for mode in RenameCaseMode:
            self.case_combo.addItem(mode.value, mode)
        top_form.addRow("Case", self.case_combo)

        self.collapse_whitespace_checkbox = QCheckBox("Collapse repeated spaces")
        top_form.addRow("", self.collapse_whitespace_checkbox)
        controls_layout.addWidget(top_row)

        sequence_group = QGroupBox("Sequence")
        sequence_layout = QFormLayout(sequence_group)
        sequence_layout.setContentsMargins(12, 14, 12, 12)
        sequence_layout.setSpacing(10)

        self.sequence_checkbox = QCheckBox("Append sequence")
        sequence_layout.addRow("", self.sequence_checkbox)

        sequence_row = QWidget(self)
        sequence_row_layout = QHBoxLayout(sequence_row)
        sequence_row_layout.setContentsMargins(0, 0, 0, 0)
        sequence_row_layout.setSpacing(8)
        self.sequence_start_spin = self._build_spinbox(minimum=0, maximum=999999, value=1)
        self.sequence_step_spin = self._build_spinbox(minimum=1, maximum=999999, value=1)
        self.sequence_padding_spin = self._build_spinbox(minimum=0, maximum=8, value=3)
        self.sequence_separator_field = QLineEdit("_")
        self.sequence_separator_field.setMaxLength(4)
        self.sequence_separator_field.setMaximumWidth(70)
        sequence_row_layout.addWidget(self.sequence_start_spin)
        sequence_row_layout.addWidget(self.sequence_step_spin)
        sequence_row_layout.addWidget(self.sequence_padding_spin)
        sequence_row_layout.addWidget(self.sequence_separator_field)
        sequence_layout.addRow("Start / Step / Pad / Sep", sequence_row)
        controls_layout.addWidget(sequence_group)

        root_layout.addWidget(controls_group)

        preview_header = QWidget(self)
        preview_header_layout = QHBoxLayout(preview_header)
        preview_header_layout.setContentsMargins(0, 0, 0, 0)
        preview_header_layout.setSpacing(8)
        preview_title = QLabel("Preview")
        preview_title.setObjectName("sectionLabel")
        preview_header_layout.addWidget(preview_title)
        preview_header_layout.addStretch(1)
        self.preview_summary_label = QLabel("")
        self.preview_summary_label.setObjectName("secondaryText")
        preview_header_layout.addWidget(self.preview_summary_label)
        root_layout.addWidget(preview_header)

        self.preview_table = QTableWidget(0, 3, self)
        self.preview_table.setHorizontalHeaderLabels(["Current Name", "New Name", "Status"])
        self.preview_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.preview_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.preview_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.preview_table.setAlternatingRowColors(True)
        self.preview_table.verticalHeader().setVisible(False)
        self.preview_table.setShowGrid(False)
        header = self.preview_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        root_layout.addWidget(self.preview_table, 1)

        self.preview_notice_label = QLabel("")
        self.preview_notice_label.setObjectName("mutedText")
        self.preview_notice_label.setWordWrap(True)
        self.preview_notice_label.hide()
        root_layout.addWidget(self.preview_notice_label)

        button_box = QDialogButtonBox(self)
        self.reset_button = QPushButton("Reset")
        self.cancel_button = QPushButton("Cancel")
        self.apply_button = QPushButton("Apply Rename")
        button_box.addButton(self.reset_button, QDialogButtonBox.ButtonRole.ResetRole)
        button_box.addButton(self.cancel_button, QDialogButtonBox.ButtonRole.RejectRole)
        button_box.addButton(self.apply_button, QDialogButtonBox.ButtonRole.AcceptRole)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        self.reset_button.clicked.connect(self._reset_fields)
        root_layout.addWidget(button_box)

        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(100)
        self._preview_timer.timeout.connect(self._refresh_preview)

        self._wire_preview_inputs()
        self._configure_sequence_mode()
        self._refresh_preview()

    def accepted_preview(self) -> BatchRenamePreview:
        return self._latest_preview

    def _wire_preview_inputs(self) -> None:
        for field in (
            self.new_name_field,
            self.prefix_field,
            self.suffix_field,
            self.sequence_separator_field,
        ):
            field.textChanged.connect(self._schedule_preview_refresh)
        for widget in (self.collapse_whitespace_checkbox, self.sequence_checkbox):
            widget.toggled.connect(self._schedule_preview_refresh)
        for widget in (self.sequence_start_spin, self.sequence_step_spin, self.sequence_padding_spin):
            widget.valueChanged.connect(self._schedule_preview_refresh)
        self.case_combo.currentIndexChanged.connect(self._schedule_preview_refresh)
        self.sequence_checkbox.toggled.connect(self._set_sequence_controls_enabled)

    def _schedule_preview_refresh(self) -> None:
        self._preview_timer.start()

    def _refresh_preview(self) -> None:
        self._latest_preview = build_batch_rename_preview(self._records, self._current_rules())
        self._populate_preview_table(self._latest_preview)
        summary = (
            f"{self._latest_preview.renamed_count} rename(s) | "
            f"{self._latest_preview.unchanged_count} unchanged | "
            f"{self._latest_preview.error_count} error(s)"
        )
        if self._latest_preview.general_error:
            summary = f"{summary} | {self._latest_preview.general_error}"
        self.preview_summary_label.setText(summary)
        self.apply_button.setEnabled(self._latest_preview.can_apply)

    def _populate_preview_table(self, preview: BatchRenamePreview) -> None:
        items = preview.items[: self.PREVIEW_ROW_LIMIT]
        self.preview_table.setRowCount(len(items))
        self.preview_table.clearContents()
        status_colors = {
            "Rename": QColor(70, 150, 95),
            "Unchanged": QColor(135, 140, 145),
            "Error": QColor(190, 70, 70),
        }
        for row, item in enumerate(items):
            current_item = QTableWidgetItem(item.source_name)
            new_item = QTableWidgetItem(item.target_name or item.source_name)
            status_text = item.status if not item.message else f"{item.status}: {item.message}"
            status_item = QTableWidgetItem(status_text)
            color = status_colors.get(item.status)
            if color is not None:
                status_item.setForeground(color)
            for table_item in (current_item, new_item, status_item):
                table_item.setFlags(table_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.preview_table.setItem(row, 0, current_item)
            self.preview_table.setItem(row, 1, new_item)
            self.preview_table.setItem(row, 2, status_item)

        if len(preview.items) > self.PREVIEW_ROW_LIMIT:
            self.preview_notice_label.setText(
                f"Showing the first {self.PREVIEW_ROW_LIMIT} rows out of {len(preview.items)}. "
                "Validation still covers the full batch."
            )
            self.preview_notice_label.show()
        else:
            self.preview_notice_label.hide()

        self.preview_table.resizeRowsToContents()

    def _current_rules(self) -> BatchRenameRules:
        case_mode = self.case_combo.currentData()
        if not isinstance(case_mode, RenameCaseMode):
            case_mode = RenameCaseMode.KEEP
        return BatchRenameRules(
            new_name=self.new_name_field.text(),
            prefix=self.prefix_field.text(),
            suffix=self.suffix_field.text(),
            case_mode=case_mode,
            collapse_whitespace=self.collapse_whitespace_checkbox.isChecked(),
            sequence_enabled=self._sequence_required or self.sequence_checkbox.isChecked(),
            sequence_start=self.sequence_start_spin.value(),
            sequence_step=self.sequence_step_spin.value(),
            sequence_padding=self.sequence_padding_spin.value(),
            sequence_separator=self.sequence_separator_field.text(),
        )

    def _reset_fields(self) -> None:
        self.new_name_field.clear()
        self.prefix_field.clear()
        self.suffix_field.clear()
        self.case_combo.setCurrentIndex(0)
        self.collapse_whitespace_checkbox.setChecked(False)
        self.sequence_checkbox.setChecked(self._sequence_required)
        self.sequence_start_spin.setValue(1)
        self.sequence_step_spin.setValue(1)
        self.sequence_padding_spin.setValue(3)
        self.sequence_separator_field.setText("_")
        self._configure_sequence_mode()
        self._refresh_preview()

    def _set_sequence_controls_enabled(self, enabled: bool) -> None:
        for widget in (
            self.sequence_start_spin,
            self.sequence_step_spin,
            self.sequence_padding_spin,
            self.sequence_separator_field,
        ):
            widget.setEnabled(enabled)

    def _configure_sequence_mode(self) -> None:
        if self._sequence_required:
            self.sequence_checkbox.setChecked(True)
            self.sequence_checkbox.setEnabled(False)
            self.sequence_checkbox.setToolTip("Sequence is required when renaming multiple image bundles.")
            self._set_sequence_controls_enabled(True)
            return
        self.sequence_checkbox.setEnabled(True)
        self.sequence_checkbox.setToolTip("")
        self._set_sequence_controls_enabled(self.sequence_checkbox.isChecked())

    @staticmethod
    def _build_spinbox(*, minimum: int, maximum: int, value: int) -> QSpinBox:
        widget = QSpinBox()
        widget.setRange(minimum, maximum)
        widget.setValue(value)
        widget.setGroupSeparatorShown(True)
        return widget
