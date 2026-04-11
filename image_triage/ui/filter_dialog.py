from __future__ import annotations

from datetime import date

from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..filtering import OrientationFilter, RecordFilterQuery


_UNSET_QDATE = QDate(1900, 1, 1)


class AdvancedFilterDialog(QDialog):
    def __init__(self, query: RecordFilterQuery, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Advanced Filters")
        self.setModal(True)
        self.resize(460, 0)
        self._source_query = query

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(16, 16, 16, 16)
        root_layout.setSpacing(12)

        intro = QLabel("Filter by capture metadata and review attributes without adding more top-bar chrome.")
        intro.setObjectName("mutedText")
        intro.setWordWrap(True)
        root_layout.addWidget(intro)

        form_container = QWidget(self)
        form_layout = QFormLayout(form_container)
        form_layout.setContentsMargins(0, 0, 0, 0)
        form_layout.setSpacing(10)
        form_layout.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        self.camera_field = QLineEdit()
        self.camera_field.setPlaceholderText("Sony, Canon, Fujifilm, A7R...")
        form_layout.addRow("Camera", self.camera_field)

        self.lens_field = QLineEdit()
        self.lens_field.setPlaceholderText("24-70, 50mm, GM, RF...")
        form_layout.addRow("Lens", self.lens_field)

        self.tag_field = QLineEdit()
        self.tag_field.setPlaceholderText("portrait, product, wedding...")
        form_layout.addRow("Tags", self.tag_field)

        self.rating_combo = QComboBox()
        self.rating_combo.addItem("Any Rating", 0)
        for rating in range(1, 6):
            self.rating_combo.addItem(f"{rating}+ Stars", rating)
        form_layout.addRow("Min Rating", self.rating_combo)

        self.orientation_combo = QComboBox()
        for mode in OrientationFilter:
            self.orientation_combo.addItem(mode.value, mode)
        form_layout.addRow("Orientation", self.orientation_combo)

        self.after_date = self._build_date_edit()
        self.before_date = self._build_date_edit()
        date_row = QWidget()
        date_layout = QHBoxLayout(date_row)
        date_layout.setContentsMargins(0, 0, 0, 0)
        date_layout.setSpacing(8)
        date_layout.addWidget(self.after_date, 1)
        date_layout.addWidget(self.before_date, 1)
        form_layout.addRow("Captured", date_row)

        self.iso_min = self._build_int_spinbox(0, 640000)
        self.iso_max = self._build_int_spinbox(0, 640000)
        iso_row = QWidget()
        iso_layout = QHBoxLayout(iso_row)
        iso_layout.setContentsMargins(0, 0, 0, 0)
        iso_layout.setSpacing(8)
        iso_layout.addWidget(self.iso_min, 1)
        iso_layout.addWidget(self.iso_max, 1)
        form_layout.addRow("ISO Range", iso_row)

        self.focal_min = self._build_float_spinbox(0.0, 2000.0)
        self.focal_max = self._build_float_spinbox(0.0, 2000.0)
        focal_row = QWidget()
        focal_layout = QHBoxLayout(focal_row)
        focal_layout.setContentsMargins(0, 0, 0, 0)
        focal_layout.setSpacing(8)
        focal_layout.addWidget(self.focal_min, 1)
        focal_layout.addWidget(self.focal_max, 1)
        form_layout.addRow("Focal Range", focal_row)

        root_layout.addWidget(form_container)

        hint = QLabel("Tip: metadata filters populate as the folder metadata index finishes loading.")
        hint.setObjectName("mutedText")
        hint.setWordWrap(True)
        root_layout.addWidget(hint)

        button_box = QDialogButtonBox(self)
        self.apply_button = QPushButton("Apply")
        self.cancel_button = QPushButton("Cancel")
        self.reset_button = QPushButton("Reset")
        button_box.addButton(self.reset_button, QDialogButtonBox.ButtonRole.ResetRole)
        button_box.addButton(self.cancel_button, QDialogButtonBox.ButtonRole.RejectRole)
        button_box.addButton(self.apply_button, QDialogButtonBox.ButtonRole.AcceptRole)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        self.reset_button.clicked.connect(self._reset_fields)
        root_layout.addWidget(button_box)

        self._load_query(query)

    def updated_query(self) -> RecordFilterQuery:
        return RecordFilterQuery(
            quick_filter=self._source_query.quick_filter,
            search_text=self._source_query.search_text,
            file_type=self._source_query.file_type,
            review_state=self._source_query.review_state,
            ai_state=self._source_query.ai_state,
            review_round=self._source_query.review_round,
            camera_text=self.camera_field.text().strip(),
            lens_text=self.lens_field.text().strip(),
            tag_text=self.tag_field.text().strip(),
            min_rating=int(self.rating_combo.currentData() or 0),
            orientation=self._selected_orientation(),
            captured_after=self._date_from_edit(self.after_date),
            captured_before=self._date_from_edit(self.before_date),
            iso_min=self.iso_min.value(),
            iso_max=self.iso_max.value(),
            focal_min=self.focal_min.value(),
            focal_max=self.focal_max.value(),
        )

    def _load_query(self, query: RecordFilterQuery) -> None:
        self.camera_field.setText(query.camera_text)
        self.lens_field.setText(query.lens_text)
        self.tag_field.setText(query.tag_text)

        rating_index = self.rating_combo.findData(query.min_rating)
        if rating_index >= 0:
            self.rating_combo.setCurrentIndex(rating_index)

        orientation_index = self.orientation_combo.findData(query.orientation)
        if orientation_index >= 0:
            self.orientation_combo.setCurrentIndex(orientation_index)

        self._set_date_edit(self.after_date, query.captured_after)
        self._set_date_edit(self.before_date, query.captured_before)
        self.iso_min.setValue(query.iso_min)
        self.iso_max.setValue(query.iso_max)
        self.focal_min.setValue(query.focal_min)
        self.focal_max.setValue(query.focal_max)

    def _reset_fields(self) -> None:
        self.camera_field.clear()
        self.lens_field.clear()
        self.tag_field.clear()
        self.rating_combo.setCurrentIndex(0)
        self.orientation_combo.setCurrentIndex(0)
        self._set_date_edit(self.after_date, None)
        self._set_date_edit(self.before_date, None)
        self.iso_min.setValue(0)
        self.iso_max.setValue(0)
        self.focal_min.setValue(0.0)
        self.focal_max.setValue(0.0)

    @staticmethod
    def _build_date_edit() -> QDateEdit:
        widget = QDateEdit()
        widget.setCalendarPopup(True)
        widget.setDisplayFormat("yyyy-MM-dd")
        widget.setMinimumDate(_UNSET_QDATE)
        widget.setSpecialValueText("Any")
        widget.setDate(_UNSET_QDATE)
        return widget

    @staticmethod
    def _build_int_spinbox(minimum: int, maximum: int) -> QSpinBox:
        widget = QSpinBox()
        widget.setRange(minimum, maximum)
        widget.setSpecialValueText("Any")
        widget.setValue(0)
        widget.setGroupSeparatorShown(True)
        return widget

    @staticmethod
    def _build_float_spinbox(minimum: float, maximum: float) -> QDoubleSpinBox:
        widget = QDoubleSpinBox()
        widget.setRange(minimum, maximum)
        widget.setDecimals(1)
        widget.setSingleStep(5.0)
        widget.setSpecialValueText("Any")
        widget.setValue(0.0)
        widget.setSuffix(" mm")
        return widget

    def _selected_orientation(self) -> OrientationFilter:
        selected = self.orientation_combo.currentData()
        if isinstance(selected, OrientationFilter):
            return selected
        if isinstance(selected, str):
            for mode in OrientationFilter:
                if selected in {mode.name, mode.value}:
                    return mode
        return OrientationFilter.ALL

    @staticmethod
    def _date_from_edit(widget: QDateEdit) -> date | None:
        if widget.date() <= _UNSET_QDATE:
            return None
        selected = widget.date()
        return date(selected.year(), selected.month(), selected.day())

    @staticmethod
    def _set_date_edit(widget: QDateEdit, value: date | None) -> None:
        if value is None:
            widget.setDate(_UNSET_QDATE)
            return
        widget.setDate(QDate(value.year, value.month, value.day))
