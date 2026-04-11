from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..ai_training import RankerTrainingOptions


class TrainRankerDialog(QDialog):
    def __init__(
        self,
        *,
        pairwise_count: int,
        cluster_count: int,
        active_reference_bank_path: str = "",
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Train Ranker")
        self.setModal(True)
        self.resize(560, 440)

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(16, 16, 16, 16)
        root_layout.setSpacing(12)

        intro = QLabel(
            "Train a new preference ranker from the current folder's saved pairwise and cluster labels. "
            "Each run is versioned so you can compare, switch back, and keep older rankers around."
        )
        intro.setObjectName("secondaryText")
        intro.setWordWrap(True)
        root_layout.addWidget(intro)

        summary = QLabel(
            f"Pairwise labels: {pairwise_count}\n"
            f"Cluster labels: {cluster_count}\n"
            "Cluster labels are included automatically when the engine can convert them into pairwise supervision."
        )
        summary.setObjectName("mutedText")
        summary.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        summary.setWordWrap(True)
        root_layout.addWidget(summary)

        options_group = QGroupBox("Training Options")
        options_layout = QFormLayout(options_group)
        options_layout.setContentsMargins(12, 14, 12, 12)
        options_layout.setSpacing(10)

        self.run_name_edit = QLineEdit()
        self.run_name_edit.setPlaceholderText("Optional name like indoor-portraits-v3")
        options_layout.addRow("Run Name", self.run_name_edit)

        self.num_epochs_spin = QSpinBox()
        self.num_epochs_spin.setRange(1, 1000)
        self.num_epochs_spin.setValue(30)
        options_layout.addRow("Epochs", self.num_epochs_spin)

        self.batch_size_spin = QSpinBox()
        self.batch_size_spin.setRange(1, 4096)
        self.batch_size_spin.setValue(32)
        options_layout.addRow("Batch Size", self.batch_size_spin)

        self.learning_rate_spin = QDoubleSpinBox()
        self.learning_rate_spin.setDecimals(6)
        self.learning_rate_spin.setRange(0.000001, 1.0)
        self.learning_rate_spin.setSingleStep(0.0001)
        self.learning_rate_spin.setValue(0.001)
        options_layout.addRow("Learning Rate", self.learning_rate_spin)

        self.hidden_dim_spin = QSpinBox()
        self.hidden_dim_spin.setRange(0, 4096)
        self.hidden_dim_spin.setSingleStep(32)
        self.hidden_dim_spin.setValue(0)
        options_layout.addRow("Hidden Layer", self.hidden_dim_spin)

        self.reference_top_k_spin = QSpinBox()
        self.reference_top_k_spin.setRange(1, 16)
        self.reference_top_k_spin.setValue(3)
        options_layout.addRow("Reference Top-K", self.reference_top_k_spin)

        self.device_combo = QComboBox()
        self.device_combo.addItem("Auto", "auto")
        self.device_combo.addItem("CPU", "cpu")
        self.device_combo.addItem("CUDA", "cuda")
        options_layout.addRow("Device", self.device_combo)

        root_layout.addWidget(options_group)

        reference_group = QGroupBox("Reference Bank")
        reference_layout = QVBoxLayout(reference_group)
        reference_layout.setContentsMargins(12, 14, 12, 12)
        reference_layout.setSpacing(10)

        self.use_reference_bank_checkbox = QCheckBox("Use a reference bank during training")
        self.use_reference_bank_checkbox.setChecked(bool(active_reference_bank_path))
        reference_layout.addWidget(self.use_reference_bank_checkbox)

        reference_row = QWidget(self)
        reference_row_layout = QHBoxLayout(reference_row)
        reference_row_layout.setContentsMargins(0, 0, 0, 0)
        reference_row_layout.setSpacing(8)
        self.reference_bank_path_edit = QLineEdit(active_reference_bank_path)
        self.reference_bank_path_edit.setPlaceholderText("Choose reference_bank.npz")
        self.reference_bank_browse_button = QPushButton("Browse...")
        self.reference_bank_browse_button.clicked.connect(self._browse_reference_bank)
        reference_row_layout.addWidget(self.reference_bank_path_edit, 1)
        reference_row_layout.addWidget(self.reference_bank_browse_button)
        reference_layout.addWidget(reference_row)

        reference_note = QLabel(
            "Leave this off to train a plain preference ranker. Turn it on when you want exemplar-conditioned scoring.\n\n"
            "Use Stats For Nerds during training if you want the live epoch and loss view."
        )
        reference_note.setObjectName("mutedText")
        reference_note.setWordWrap(True)
        reference_layout.addWidget(reference_note)
        root_layout.addWidget(reference_group)

        button_box = QDialogButtonBox(self)
        self.cancel_button = QPushButton("Cancel")
        self.train_button = QPushButton("Train")
        button_box.addButton(self.cancel_button, QDialogButtonBox.ButtonRole.RejectRole)
        button_box.addButton(self.train_button, QDialogButtonBox.ButtonRole.AcceptRole)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        root_layout.addWidget(button_box)

        self.use_reference_bank_checkbox.toggled.connect(self._refresh_reference_controls)
        self.reference_bank_path_edit.textChanged.connect(self._refresh_reference_controls)
        self._refresh_reference_controls()

    def accepted_options(self) -> RankerTrainingOptions:
        reference_bank_path = ""
        if self.use_reference_bank_checkbox.isChecked():
            reference_bank_path = self.reference_bank_path_edit.text().strip()
        return RankerTrainingOptions(
            run_name=self.run_name_edit.text().strip(),
            num_epochs=int(self.num_epochs_spin.value()),
            batch_size=int(self.batch_size_spin.value()),
            learning_rate=float(self.learning_rate_spin.value()),
            hidden_dim=int(self.hidden_dim_spin.value()),
            reference_bank_path=reference_bank_path,
            reference_top_k=int(self.reference_top_k_spin.value()),
            device=str(self.device_combo.currentData() or "auto"),
        )

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
        self.reference_bank_path_edit.setEnabled(enabled)
        self.reference_bank_browse_button.setEnabled(enabled)
        self.reference_top_k_spin.setEnabled(enabled)
        valid_reference = bool(self.reference_bank_path_edit.text().strip()) if enabled else True
        self.train_button.setEnabled(valid_reference)
