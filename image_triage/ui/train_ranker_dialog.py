from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..ai_training import RankerTrainingOptions, ranker_profile_options


_DIALOG_WIDTH = 540
_COMPACT_HEIGHT = 330
_EXPANDED_HEIGHT = 532
_REFERENCE_ROW_HEIGHT = 42
_SUGGESTION_HEIGHT = 42
_SOURCE_ROW_HEIGHT = 42


class TrainRankerDialog(QDialog):
    """Compact training setup dialog with advanced knobs hidden by default."""

    configure_sources_requested = Signal()

    def __init__(
        self,
        *,
        pairwise_count: int,
        cluster_count: int,
        disagreement_count: int = 0,
        general_pairwise_count: int = 0,
        general_cluster_count: int = 0,
        general_disagreement_count: int = 0,
        general_source_count: int = 0,
        active_reference_bank_path: str = "",
        suggested_profile_key: str = "",
        suggestion_reason: str = "",
        initial_options: RankerTrainingOptions | None = None,
        show_advanced_options: bool = False,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._shortcuts: list[QShortcut] = []
        self._has_suggestion = bool(suggestion_reason)
        self._initial_options = initial_options
        self._initial_show_advanced = bool(show_advanced_options)
        self._general_source_row_visible = False
        self.setWindowTitle("Train Ranker")
        self.setModal(True)
        self.setFixedWidth(_DIALOG_WIDTH)

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(16, 14, 16, 14)
        root_layout.setSpacing(10)
        root_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        header_row = QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.setSpacing(12)

        header = QLabel("Train Ranker", self)
        header.setObjectName("trainRankerTitle")
        header_row.addWidget(header)
        header_row.addStretch(1)

        label_summary = QLabel(
            f"{pairwise_count} pairwise | {cluster_count} cluster | {disagreement_count} AI disputes",
            self,
        )
        label_summary.setObjectName("trainRankerSummary")
        header_row.addWidget(label_summary)
        root_layout.addLayout(header_row)

        if suggestion_reason:
            suggestion_label = QLabel(suggestion_reason, self)
            suggestion_label.setObjectName("mutedText")
            suggestion_label.setWordWrap(True)
            root_layout.addWidget(suggestion_label)

        basics = QFrame(self)
        basics.setObjectName("aiTrainingStatsCard")
        basics.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        basics_layout = QGridLayout(basics)
        basics_layout.setContentsMargins(12, 10, 12, 10)
        basics_layout.setHorizontalSpacing(12)
        basics_layout.setVerticalSpacing(8)
        basics_layout.setColumnStretch(1, 1)
        root_layout.addWidget(basics)

        self.run_name_edit = QLineEdit(basics)
        self.run_name_edit.setPlaceholderText("Optional version name")
        if initial_options is not None:
            self.run_name_edit.setText(initial_options.run_name)
        _add_row(basics_layout, 0, "Name", self.run_name_edit)

        self.profile_combo = QComboBox(basics)
        for profile_key, profile_label in ranker_profile_options():
            self.profile_combo.addItem(profile_label, profile_key)
        profile_key = initial_options.profile_key if initial_options is not None else suggested_profile_key
        if profile_key:
            profile_index = self.profile_combo.findData(profile_key)
            if profile_index >= 0:
                self.profile_combo.setCurrentIndex(profile_index)
        _add_row(basics_layout, 1, "Profile", self.profile_combo)

        source_row = QWidget(basics)
        source_row_layout = QHBoxLayout(source_row)
        source_row_layout.setContentsMargins(0, 0, 0, 0)
        source_row_layout.setSpacing(8)
        self.general_source_summary_label = QLabel(source_row)
        self.general_source_summary_label.setObjectName("mutedText")
        self.general_source_summary_label.setWordWrap(False)
        self.general_source_button = QPushButton("Choose Sources...", source_row)
        self.general_source_button.clicked.connect(self.configure_sources_requested.emit)
        source_row_layout.addWidget(self.general_source_summary_label, 1)
        source_row_layout.addWidget(self.general_source_button)
        self.general_source_row_label = _add_row(basics_layout, 2, "Sources", source_row)
        self.set_general_source_summary(
            pairwise_count=general_pairwise_count,
            cluster_count=general_cluster_count,
            disagreement_count=general_disagreement_count,
            source_count=general_source_count,
        )

        self.device_combo = QComboBox(basics)
        self.device_combo.addItem("Auto", "auto")
        self.device_combo.addItem("CUDA", "cuda")
        self.device_combo.addItem("CPU", "cpu")
        if initial_options is not None:
            device_index = self.device_combo.findData(initial_options.device)
            if device_index >= 0:
                self.device_combo.setCurrentIndex(device_index)
        _add_row(basics_layout, 3, "Device", self.device_combo)

        initial_reference_path = active_reference_bank_path
        if initial_options is not None:
            initial_reference_path = initial_options.reference_bank_path
        self.use_reference_bank_checkbox = QCheckBox("Use reference bank", basics)
        self.use_reference_bank_checkbox.setChecked(bool(initial_reference_path))
        _add_row(basics_layout, 4, "Reference", self.use_reference_bank_checkbox)

        reference_row = QWidget(basics)
        reference_row_layout = QHBoxLayout(reference_row)
        reference_row_layout.setContentsMargins(0, 0, 0, 0)
        reference_row_layout.setSpacing(8)
        self.reference_bank_path_edit = QLineEdit(initial_reference_path, reference_row)
        self.reference_bank_path_edit.setPlaceholderText("reference_bank.npz")
        self.reference_bank_browse_button = QPushButton("Browse", reference_row)
        self.reference_bank_browse_button.clicked.connect(self._browse_reference_bank)
        reference_row_layout.addWidget(self.reference_bank_path_edit, 1)
        reference_row_layout.addWidget(self.reference_bank_browse_button)
        self.reference_bank_path_label = _add_row(basics_layout, 5, "Bank file", reference_row)

        self.advanced_checkbox = QCheckBox("Show advanced training options", self)
        root_layout.addWidget(self.advanced_checkbox)

        self.advanced_panel = QFrame(self)
        self.advanced_panel.setObjectName("aiTrainingStatsCard")
        self.advanced_panel.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        advanced_layout = QGridLayout(self.advanced_panel)
        advanced_layout.setContentsMargins(12, 10, 12, 10)
        advanced_layout.setHorizontalSpacing(12)
        advanced_layout.setVerticalSpacing(8)
        advanced_layout.setColumnStretch(1, 1)
        root_layout.addWidget(self.advanced_panel)

        self.num_epochs_spin = QSpinBox(self.advanced_panel)
        self.num_epochs_spin.setRange(1, 1000)
        self.num_epochs_spin.setValue(initial_options.num_epochs if initial_options is not None else 30)
        self.num_epochs_spin.setFixedWidth(160)
        _add_row(advanced_layout, 0, "Epochs", self.num_epochs_spin)

        self.batch_size_spin = QSpinBox(self.advanced_panel)
        self.batch_size_spin.setRange(1, 4096)
        self.batch_size_spin.setValue(initial_options.batch_size if initial_options is not None else 32)
        self.batch_size_spin.setFixedWidth(160)
        _add_row(advanced_layout, 1, "Batch size", self.batch_size_spin)

        self.learning_rate_spin = QDoubleSpinBox(self.advanced_panel)
        self.learning_rate_spin.setDecimals(6)
        self.learning_rate_spin.setRange(0.000001, 1.0)
        self.learning_rate_spin.setSingleStep(0.0001)
        self.learning_rate_spin.setValue(initial_options.learning_rate if initial_options is not None else 0.001)
        self.learning_rate_spin.setFixedWidth(160)
        _add_row(advanced_layout, 2, "Learning rate", self.learning_rate_spin)

        self.hidden_dim_spin = QSpinBox(self.advanced_panel)
        self.hidden_dim_spin.setRange(0, 4096)
        self.hidden_dim_spin.setSingleStep(32)
        self.hidden_dim_spin.setSpecialValueText("Off")
        self.hidden_dim_spin.setValue(initial_options.hidden_dim if initial_options is not None else 0)
        self.hidden_dim_spin.setFixedWidth(160)
        _add_row(advanced_layout, 3, "Hidden layer", self.hidden_dim_spin)

        self.reference_top_k_spin = QSpinBox(self.advanced_panel)
        self.reference_top_k_spin.setRange(1, 16)
        self.reference_top_k_spin.setValue(initial_options.reference_top_k if initial_options is not None else 3)
        self.reference_top_k_spin.setFixedWidth(160)
        _add_row(advanced_layout, 4, "Reference top-k", self.reference_top_k_spin)

        self.disagreement_oversample_spin = QSpinBox(self.advanced_panel)
        self.disagreement_oversample_spin.setRange(1, 10)
        self.disagreement_oversample_spin.setValue(initial_options.disagreement_oversample_factor if initial_options is not None else 3)
        self.disagreement_oversample_spin.setFixedWidth(160)
        _add_row(advanced_layout, 5, "AI dispute weight", self.disagreement_oversample_spin)

        button_box = QDialogButtonBox(self)
        self.cancel_button = QPushButton("Cancel", self)
        self.train_button = QPushButton("Train", self)
        self.train_button.setDefault(True)
        button_box.addButton(self.cancel_button, QDialogButtonBox.ButtonRole.RejectRole)
        button_box.addButton(self.train_button, QDialogButtonBox.ButtonRole.AcceptRole)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        root_layout.addWidget(button_box)

        self.profile_combo.currentIndexChanged.connect(self._refresh_profile_controls)
        self.use_reference_bank_checkbox.toggled.connect(self._refresh_reference_controls)
        self.reference_bank_path_edit.textChanged.connect(self._refresh_reference_controls)
        self.advanced_checkbox.toggled.connect(self._handle_advanced_toggled)
        self.advanced_checkbox.setChecked(self._initial_show_advanced)
        self.advanced_panel.setVisible(self._initial_show_advanced)
        for sequence in ("Ctrl+Return", "Ctrl+Enter"):
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.activated.connect(self.accept)
            self._shortcuts.append(shortcut)
        self._refresh_profile_controls()
        self._refresh_reference_controls()

    @property
    def advanced_options_visible(self) -> bool:
        return self.advanced_checkbox.isChecked()

    def accepted_options(self) -> RankerTrainingOptions:
        reference_bank_path = ""
        if self.use_reference_bank_checkbox.isChecked():
            reference_bank_path = self.reference_bank_path_edit.text().strip()
        return RankerTrainingOptions(
            run_name=self.run_name_edit.text().strip(),
            profile_key=str(self.profile_combo.currentData() or "general"),
            num_epochs=int(self.num_epochs_spin.value()),
            batch_size=int(self.batch_size_spin.value()),
            learning_rate=float(self.learning_rate_spin.value()),
            hidden_dim=int(self.hidden_dim_spin.value()),
            disagreement_oversample_factor=int(self.disagreement_oversample_spin.value()),
            reference_bank_path=reference_bank_path,
            reference_top_k=int(self.reference_top_k_spin.value()),
            device=str(self.device_combo.currentData() or "auto"),
        )

    def set_general_source_summary(
        self,
        *,
        pairwise_count: int,
        cluster_count: int,
        disagreement_count: int,
        source_count: int,
    ) -> None:
        self.general_source_summary_label.setText(
            f"{source_count} source(s) | {pairwise_count} pairwise | {cluster_count} cluster | {disagreement_count} AI disputes"
        )

    def _refresh_profile_controls(self) -> None:
        is_general = str(self.profile_combo.currentData() or "") == "general"
        self._general_source_row_visible = is_general
        self.general_source_row_label.setVisible(is_general)
        self.general_source_summary_label.parentWidget().setVisible(is_general)
        self._fit_to_content()

    def _browse_reference_bank(self) -> None:
        initial_dir = self.reference_bank_path_edit.text().strip()
        if initial_dir:
            initial_dir = str(Path(initial_dir).expanduser().resolve().parent)
        selected_path, _selected_filter = QFileDialog.getOpenFileName(
            self,
            "Choose Reference Bank",
            initial_dir,
            "Reference Bank (*.npz);;All Files (*)",
        )
        if selected_path:
            self.reference_bank_path_edit.setText(selected_path)
            self.use_reference_bank_checkbox.setChecked(True)

    def _refresh_reference_controls(self) -> None:
        enabled = self.use_reference_bank_checkbox.isChecked()
        self.reference_bank_path_label.setVisible(enabled)
        self.reference_bank_path_edit.parentWidget().setVisible(enabled)
        self.reference_bank_path_edit.setEnabled(enabled)
        self.reference_bank_browse_button.setEnabled(enabled)
        self.reference_top_k_spin.setEnabled(enabled)
        valid_reference = bool(self.reference_bank_path_edit.text().strip()) if enabled else True
        self.train_button.setEnabled(valid_reference)
        self._fit_to_content()

    def _handle_advanced_toggled(self, checked: bool) -> None:
        self.advanced_panel.setVisible(checked)
        self._fit_to_content()

    def _fit_to_content(self) -> None:
        height = _EXPANDED_HEIGHT if self.advanced_panel.isVisible() else _COMPACT_HEIGHT
        if self.use_reference_bank_checkbox.isChecked():
            height += _REFERENCE_ROW_HEIGHT
        if self._has_suggestion:
            height += _SUGGESTION_HEIGHT
        if self._general_source_row_visible:
            height += _SOURCE_ROW_HEIGHT
        self.setMinimumHeight(height)
        self.setMaximumHeight(height)
        self.resize(_DIALOG_WIDTH, height)


def _add_row(layout: QGridLayout, row: int, label_text: str, field: QWidget) -> QLabel:
    label = QLabel(label_text)
    label.setObjectName("sectionLabel")
    label.setFixedWidth(96)
    layout.addWidget(label, row, 0, Qt.AlignmentFlag.AlignVCenter)
    layout.addWidget(field, row, 1)
    return label
