from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QProgressBar, QVBoxLayout


class AITrainingProgressDialog(QDialog):
    stats_requested = Signal()

    def __init__(self, *, title: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(False)
        self.setMinimumWidth(460)
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        self.status_label = QLabel("Preparing AI training task...", self)
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        self.progress_bar = QProgressBar(self)
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(0)
        layout.addWidget(self.progress_bar)

        button_row = QHBoxLayout()
        button_row.setContentsMargins(0, 0, 0, 0)
        button_row.addStretch(1)

        self.stats_button = QPushButton("Stats For Nerds", self)
        self.stats_button.clicked.connect(self.stats_requested.emit)
        button_row.addWidget(self.stats_button)

        self.hide_button = QPushButton("Hide", self)
        self.hide_button.clicked.connect(self.hide)
        button_row.addWidget(self.hide_button)

        layout.addLayout(button_row)

    def set_status_text(self, text: str) -> None:
        self.status_label.setText(text or "Running AI training task...")

    def set_progress(self, current: int, total: int) -> None:
        if total > 0:
            total_value = max(1, total)
            self.progress_bar.setRange(0, total_value)
            self.progress_bar.setValue(min(max(current, 0), total_value))
            return
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setValue(0)

    def set_stats_button_enabled(self, enabled: bool) -> None:
        self.stats_button.setEnabled(enabled)
