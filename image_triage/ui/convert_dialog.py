from __future__ import annotations

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..image_convert import (
    ConvertOptions,
    ConvertPlan,
    ConvertSourceItem,
    build_convert_plan,
    convert_formats,
    default_convert_format,
    format_for_suffix,
)


class ConvertDialog(QDialog):
    PREVIEW_ROW_LIMIT = 1200

    def __init__(
        self,
        sources: list[ConvertSourceItem],
        *,
        title: str,
        scope_label: str,
        show_preview: bool | None = None,
        raw_note: str = "",
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)

        self._sources = list(sources)
        self._show_preview = len(self._sources) > 1 if show_preview is None else bool(show_preview)
        self._latest_plan = build_convert_plan(self._sources, ConvertOptions())

        self.resize(920 if self._show_preview else 500, 680 if self._show_preview else 360)

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(16, 16, 16, 16)
        root_layout.setSpacing(12)

        intro = QLabel(
            f"Convert {len(self._sources)} image(s) into another format. "
            "By default, converted copies stay in the same folder unless overwrite is enabled."
        )
        intro.setObjectName("secondaryText")
        intro.setWordWrap(True)
        root_layout.addWidget(intro)

        scope = QLabel(scope_label)
        scope.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        scope.setObjectName("mutedText")
        scope.setWordWrap(True)
        root_layout.addWidget(scope)

        output_group = QGroupBox("Output Format")
        output_layout = QVBoxLayout(output_group)
        output_layout.setContentsMargins(12, 14, 12, 12)
        output_layout.setSpacing(10)

        self.format_combo = QComboBox()
        for item in convert_formats():
            self.format_combo.addItem(item.name, item.suffix)
        default_index = self.format_combo.findData(default_convert_format().suffix)
        self.format_combo.setCurrentIndex(default_index if default_index >= 0 else 0)
        output_layout.addWidget(self.format_combo)

        self.format_description_label = QLabel("")
        self.format_description_label.setObjectName("secondaryText")
        self.format_description_label.setWordWrap(True)
        output_layout.addWidget(self.format_description_label)

        root_layout.addWidget(output_group)

        self.overwrite_checkbox = QCheckBox("Overwrite files")
        self.strip_metadata_checkbox = QCheckBox("Remove metadata that doesn't affect rendering")
        root_layout.addWidget(self.overwrite_checkbox)
        root_layout.addWidget(self.strip_metadata_checkbox)

        raw_notice = raw_note or "Convert can't be used on RAW files."
        self.notice_label = QLabel(
            f"{raw_notice} Converted files keep the original dimensions unless you use Resize instead."
        )
        self.notice_label.setObjectName("mutedText")
        self.notice_label.setWordWrap(True)
        root_layout.addWidget(self.notice_label)

        if self._show_preview:
            preview_header = QWidget(self)
            preview_header_layout = QVBoxLayout(preview_header)
            preview_header_layout.setContentsMargins(0, 0, 0, 0)
            preview_header_layout.setSpacing(4)
            preview_title = QLabel("Preview")
            preview_title.setObjectName("sectionLabel")
            preview_header_layout.addWidget(preview_title)
            self.preview_summary_label = QLabel("")
            self.preview_summary_label.setObjectName("secondaryText")
            preview_header_layout.addWidget(self.preview_summary_label)
            root_layout.addWidget(preview_header)

            self.preview_table = QTableWidget(0, 3, self)
            self.preview_table.setHorizontalHeaderLabels(["Current Name", "Output File", "Status"])
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
        else:
            self.preview_summary_label = QLabel("")
            self.preview_table = None
            self.preview_notice_label = QLabel("")

        button_box = QDialogButtonBox(self)
        self.cancel_button = QPushButton("Cancel")
        self.apply_button = QPushButton("Convert")
        button_box.addButton(self.cancel_button, QDialogButtonBox.ButtonRole.RejectRole)
        button_box.addButton(self.apply_button, QDialogButtonBox.ButtonRole.AcceptRole)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        root_layout.addWidget(button_box)

        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(100)
        self._preview_timer.timeout.connect(self._refresh_preview)

        self._wire_inputs()
        self._refresh_format_details()
        self._refresh_preview()

    def accepted_plan(self) -> ConvertPlan:
        return self._latest_plan

    def accepted_options(self) -> ConvertOptions:
        return self._current_options()

    def _wire_inputs(self) -> None:
        self.format_combo.currentIndexChanged.connect(self._handle_format_changed)
        self.overwrite_checkbox.toggled.connect(self._schedule_preview_refresh)
        self.strip_metadata_checkbox.toggled.connect(self._schedule_preview_refresh)

    def _handle_format_changed(self) -> None:
        self._refresh_format_details()
        self._schedule_preview_refresh()

    def _refresh_format_details(self) -> None:
        output_format = format_for_suffix(str(self.format_combo.currentData() or default_convert_format().suffix))
        self.format_description_label.setText(output_format.description)

    def _schedule_preview_refresh(self) -> None:
        self._preview_timer.start()

    def _refresh_preview(self) -> None:
        self._latest_plan = build_convert_plan(self._sources, self._current_options())
        if self._latest_plan.general_error:
            summary = self._latest_plan.general_error
        else:
            summary = f"{self._latest_plan.executable_count} item(s) | {self._latest_plan.copy_mode} | {self._latest_plan.output_label}"
        if self._latest_plan.error_count:
            summary = f"{summary} | {self._latest_plan.error_count} error(s)"
        self.preview_summary_label.setText(summary)
        self.apply_button.setEnabled(self._latest_plan.can_apply)
        if self._show_preview and self.preview_table is not None:
            self._populate_preview_table(self._latest_plan)

    def _populate_preview_table(self, plan: ConvertPlan) -> None:
        items = plan.items[: self.PREVIEW_ROW_LIMIT]
        self.preview_table.setRowCount(len(items))
        self.preview_table.clearContents()
        status_colors = {
            "Copy": QColor(70, 150, 95),
            "Convert": QColor(70, 120, 190),
            "Overwrite": QColor(185, 133, 61),
            "Error": QColor(190, 70, 70),
        }
        for row, item in enumerate(items):
            current_item = QTableWidgetItem(item.source.source_name)
            target_item = QTableWidgetItem(item.target_name or item.source.source_name)
            status_text = item.status if not item.message else f"{item.status}: {item.message}"
            status_item = QTableWidgetItem(status_text)
            color = status_colors.get(item.status)
            if color is not None:
                status_item.setForeground(color)
            for table_item in (current_item, target_item, status_item):
                table_item.setFlags(table_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.preview_table.setItem(row, 0, current_item)
            self.preview_table.setItem(row, 1, target_item)
            self.preview_table.setItem(row, 2, status_item)

        if len(plan.items) > self.PREVIEW_ROW_LIMIT:
            self.preview_notice_label.setText(
                f"Showing the first {self.PREVIEW_ROW_LIMIT} rows out of {len(plan.items)}. "
                "Validation still covers the full batch."
            )
            self.preview_notice_label.show()
        else:
            self.preview_notice_label.hide()
        self.preview_table.resizeRowsToContents()

    def _current_options(self) -> ConvertOptions:
        return ConvertOptions(
            output_suffix=str(self.format_combo.currentData() or default_convert_format().suffix),
            overwrite=self.overwrite_checkbox.isChecked(),
            strip_metadata=self.strip_metadata_checkbox.isChecked(),
        )
