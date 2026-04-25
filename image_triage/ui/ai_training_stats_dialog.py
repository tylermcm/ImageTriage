from __future__ import annotations

import re

from PySide6.QtCore import Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QDialog, QGridLayout, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget


EPOCH_LOG_PATTERN = re.compile(
    r"Epoch\s+(?P<epoch>\d+)/(?P<total>\d+)\s+"
    r"train_loss=(?P<train_loss>[0-9.]+)\s+"
    r"validation_loss=(?P<validation_loss>[^ ]+)\s+"
    r"validation_pairwise_accuracy=(?P<validation_accuracy>[^ ]+)"
)


class AITrainingStatsDialog(QDialog):
    def __init__(self, *, title: str = "AI Training Stats For Nerds", parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(860, 620)

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(16, 16, 16, 16)
        root_layout.setSpacing(12)

        summary_card = QWidget(self)
        summary_card.setObjectName("aiTrainingStatsCard")
        summary_layout = QGridLayout(summary_card)
        summary_layout.setContentsMargins(14, 14, 14, 14)
        summary_layout.setHorizontalSpacing(18)
        summary_layout.setVerticalSpacing(8)

        self.stage_value_label = QLabel("Waiting for output", summary_card)
        self.run_value_label = QLabel("Not started", summary_card)
        self.epoch_value_label = QLabel("n/a", summary_card)
        self.train_loss_value_label = QLabel("n/a", summary_card)
        self.validation_loss_value_label = QLabel("n/a", summary_card)
        self.validation_accuracy_value_label = QLabel("n/a", summary_card)
        self.fit_health_value_label = QLabel("Pending", summary_card)
        self.fit_summary_value_label = QLabel("Run training or evaluation to get a simple health check.", summary_card)
        self.fit_remedy_value_label = QLabel("", summary_card)
        for label in (
            self.stage_value_label,
            self.run_value_label,
            self.epoch_value_label,
            self.train_loss_value_label,
            self.validation_loss_value_label,
            self.validation_accuracy_value_label,
            self.fit_health_value_label,
            self.fit_summary_value_label,
            self.fit_remedy_value_label,
        ):
            label.setObjectName("secondaryText")
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            label.setWordWrap(True)

        rows = (
            ("Stage", self.stage_value_label),
            ("Run", self.run_value_label),
            ("Latest Epoch", self.epoch_value_label),
            ("Train Loss", self.train_loss_value_label),
            ("Validation Loss", self.validation_loss_value_label),
            ("Validation Pairwise Acc", self.validation_accuracy_value_label),
            ("Training Health", self.fit_health_value_label),
            ("Summary", self.fit_summary_value_label),
            ("Try Next", self.fit_remedy_value_label),
        )
        for row_index, (title_text, value_label) in enumerate(rows):
            key_label = QLabel(title_text, summary_card)
            key_label.setObjectName("mutedText")
            summary_layout.addWidget(key_label, row_index, 0)
            summary_layout.addWidget(value_label, row_index, 1)

        root_layout.addWidget(summary_card)

        self.log_view = QPlainTextEdit(self)
        self.log_view.setObjectName("aiTrainingLogView")
        self.log_view.setReadOnly(True)
        root_layout.addWidget(self.log_view, 1)

        button_row = QHBoxLayout()
        button_row.setContentsMargins(0, 0, 0, 0)
        button_row.addStretch(1)

        self.clear_button = QPushButton("Clear", self)
        self.clear_button.clicked.connect(self.clear_log)
        button_row.addWidget(self.clear_button)

        self.close_button = QPushButton("Close", self)
        self.close_button.clicked.connect(self.close)
        button_row.addWidget(self.close_button)

        root_layout.addLayout(button_row)

    def set_stage_text(self, text: str) -> None:
        self.stage_value_label.setText(text or "Waiting for output")

    def set_run_text(self, text: str) -> None:
        self.run_value_label.setText(text or "Not started")

    def clear_log(self) -> None:
        self.log_view.clear()

    def set_fit_diagnosis(self, label: str, summary: str = "", remedy: str = "") -> None:
        self.fit_health_value_label.setText(label or "Pending")
        self.fit_summary_value_label.setText(summary or "Run training or evaluation to get a simple health check.")
        self.fit_remedy_value_label.setText(remedy or "")

    def append_log_line(self, line: str) -> None:
        message = (line or "").strip()
        if not message:
            return
        self.log_view.appendPlainText(message)
        cursor = self.log_view.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self.log_view.setTextCursor(cursor)
        self._update_metrics_from_line(message)

    def load_lines(self, lines: list[str]) -> None:
        self.log_view.setPlainText("\n".join(lines))
        if lines:
            self._update_metrics_from_line(lines[-1])

    def _update_metrics_from_line(self, line: str) -> None:
        match = EPOCH_LOG_PATTERN.search(line)
        if match is None:
            return
        self.epoch_value_label.setText(f"{match.group('epoch')}/{match.group('total')}")
        self.train_loss_value_label.setText(match.group("train_loss"))
        self.validation_loss_value_label.setText(match.group("validation_loss"))
        self.validation_accuracy_value_label.setText(match.group("validation_accuracy"))
