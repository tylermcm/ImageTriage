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
    QGridLayout,
    QGroupBox,
    QHeaderView,
    QLabel,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..image_resize import (
    ResizeOptions,
    ResizePlan,
    ResizeSourceItem,
    build_resize_plan,
    default_resize_preset,
    preset_for_key,
    resize_presets,
)


class ResizeDialog(QDialog):
    PREVIEW_ROW_LIMIT = 1200

    def __init__(
        self,
        sources: list[ResizeSourceItem],
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
        self._latest_plan = build_resize_plan(self._sources, ResizeOptions())

        self.resize(920 if self._show_preview else 500, 720 if self._show_preview else 410)

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(16, 16, 16, 16)
        root_layout.setSpacing(12)

        intro = QLabel(
            f"Resize {len(self._sources)} image(s) with PowerToys-style presets. "
            "By default, resized copies stay in the same folder unless overwrite is enabled."
        )
        intro.setObjectName("secondaryText")
        intro.setWordWrap(True)
        root_layout.addWidget(intro)

        scope = QLabel(scope_label)
        scope.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        scope.setObjectName("mutedText")
        scope.setWordWrap(True)
        root_layout.addWidget(scope)

        size_group = QGroupBox("Image Size")
        size_layout = QVBoxLayout(size_group)
        size_layout.setContentsMargins(12, 14, 12, 12)
        size_layout.setSpacing(10)

        self.preset_combo = QComboBox()
        for preset in resize_presets():
            self.preset_combo.addItem(preset.name, preset.key)
        default_index = self.preset_combo.findData(default_resize_preset().key)
        self.preset_combo.setCurrentIndex(default_index if default_index >= 0 else 0)
        size_layout.addWidget(self.preset_combo)

        self.preset_description_label = QLabel("")
        self.preset_description_label.setObjectName("secondaryText")
        size_layout.addWidget(self.preset_description_label)

        self.custom_size_row = QWidget(self)
        custom_layout = QGridLayout(self.custom_size_row)
        custom_layout.setContentsMargins(0, 0, 0, 0)
        custom_layout.setHorizontalSpacing(8)
        custom_layout.setVerticalSpacing(6)
        self.custom_width_spin = self._build_spinbox(minimum=1, maximum=50000, value=1920)
        self.custom_height_spin = self._build_spinbox(minimum=1, maximum=50000, value=1080)
        custom_layout.addWidget(QLabel("Width"), 0, 0)
        custom_layout.addWidget(self.custom_width_spin, 0, 1)
        custom_layout.addWidget(QLabel("Height"), 0, 2)
        custom_layout.addWidget(self.custom_height_spin, 0, 3)
        size_layout.addWidget(self.custom_size_row)

        root_layout.addWidget(size_group)

        self.shrink_only_checkbox = QCheckBox("Make pictures smaller but not larger")
        self.ignore_orientation_checkbox = QCheckBox("Ignore the orientation of pictures")
        self.overwrite_checkbox = QCheckBox("Overwrite files")
        self.strip_metadata_checkbox = QCheckBox("Remove metadata that doesn't affect rendering")
        for widget in (
            self.shrink_only_checkbox,
            self.ignore_orientation_checkbox,
            self.overwrite_checkbox,
            self.strip_metadata_checkbox,
        ):
            root_layout.addWidget(widget)

        raw_notice = raw_note or "Resize can't be used on RAW files."
        self.notice_label = QLabel(
            f"{raw_notice} Formats that cannot be written directly will export as JPG copies when overwrite is off."
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
        self.reset_button = QPushButton("Reset")
        self.cancel_button = QPushButton("Cancel")
        self.apply_button = QPushButton("Resize")
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

        self._wire_inputs()
        self._refresh_preset_details()
        self._refresh_preview()

    def accepted_plan(self) -> ResizePlan:
        return self._latest_plan

    def accepted_options(self) -> ResizeOptions:
        return self._current_options()

    def _wire_inputs(self) -> None:
        self.preset_combo.currentIndexChanged.connect(self._handle_preset_changed)
        self.custom_width_spin.valueChanged.connect(self._schedule_preview_refresh)
        self.custom_height_spin.valueChanged.connect(self._schedule_preview_refresh)
        for widget in (
            self.shrink_only_checkbox,
            self.ignore_orientation_checkbox,
            self.overwrite_checkbox,
            self.strip_metadata_checkbox,
        ):
            widget.toggled.connect(self._schedule_preview_refresh)

    def _handle_preset_changed(self) -> None:
        self._refresh_preset_details()
        self._schedule_preview_refresh()

    def _refresh_preset_details(self) -> None:
        preset = preset_for_key(str(self.preset_combo.currentData() or default_resize_preset().key))
        if preset.key == "custom":
            self.preset_description_label.setText("fits within a custom size")
            self.custom_size_row.show()
        else:
            self.preset_description_label.setText(preset.description)
            self.custom_size_row.hide()

    def _schedule_preview_refresh(self) -> None:
        self._preview_timer.start()

    def _refresh_preview(self) -> None:
        self._latest_plan = build_resize_plan(self._sources, self._current_options())
        summary = (
            f"{self._latest_plan.executable_count} item(s) | "
            f"{self._latest_plan.copy_mode} | "
            f"fits within {self._latest_plan.target_width} x {self._latest_plan.target_height}"
            if not self._latest_plan.general_error
            else self._latest_plan.general_error
        )
        if self._latest_plan.error_count:
            summary = f"{summary} | {self._latest_plan.error_count} error(s)"
        self.preview_summary_label.setText(summary)
        self.apply_button.setEnabled(self._latest_plan.can_apply)
        if self._show_preview and self.preview_table is not None:
            self._populate_preview_table(self._latest_plan)

    def _populate_preview_table(self, plan: ResizePlan) -> None:
        items = plan.items[: self.PREVIEW_ROW_LIMIT]
        self.preview_table.setRowCount(len(items))
        self.preview_table.clearContents()
        status_colors = {
            "Copy": QColor(70, 150, 95),
            "Overwrite": QColor(70, 120, 190),
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

    def _current_options(self) -> ResizeOptions:
        return ResizeOptions(
            preset_key=str(self.preset_combo.currentData() or default_resize_preset().key),
            custom_width=self.custom_width_spin.value(),
            custom_height=self.custom_height_spin.value(),
            shrink_only=self.shrink_only_checkbox.isChecked(),
            ignore_orientation=self.ignore_orientation_checkbox.isChecked(),
            overwrite=self.overwrite_checkbox.isChecked(),
            strip_metadata=self.strip_metadata_checkbox.isChecked(),
        )

    def _reset_fields(self) -> None:
        default_index = self.preset_combo.findData(default_resize_preset().key)
        self.preset_combo.setCurrentIndex(default_index if default_index >= 0 else 0)
        self.custom_width_spin.setValue(1920)
        self.custom_height_spin.setValue(1080)
        self.shrink_only_checkbox.setChecked(False)
        self.ignore_orientation_checkbox.setChecked(False)
        self.overwrite_checkbox.setChecked(False)
        self.strip_metadata_checkbox.setChecked(False)
        self._refresh_preset_details()
        self._refresh_preview()

    @staticmethod
    def _build_spinbox(*, minimum: int, maximum: int, value: int) -> QSpinBox:
        widget = QSpinBox()
        widget.setRange(minimum, maximum)
        widget.setValue(value)
        widget.setGroupSeparatorShown(True)
        return widget
