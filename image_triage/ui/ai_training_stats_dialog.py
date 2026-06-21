from __future__ import annotations

import re

from PySide6.QtCore import Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QDialog, QGridLayout, QHBoxLayout, QLabel, QPlainTextEdit, QProgressBar, QPushButton, QVBoxLayout, QWidget


EPOCH_LOG_PATTERN = re.compile(
    r"Epoch\s+(?P<epoch>\d+)/(?P<total>\d+)\s+"
    r"train_loss=(?P<train_loss>[0-9.]+)\s+"
    r"validation_loss=(?P<validation_loss>[^ ]+)\s+"
    r"validation_pairwise_accuracy=(?P<validation_accuracy>[^ ]+)"
)
IMPORTED_RATINGS_PATTERN = re.compile(r"Imported\s+(?P<count>\d+)\s+rating\(s\)")
ADAPTER_SCORED_PATTERN = re.compile(r"(?:Trained|Applied)\s+adapter\s+.*:\s+scored\s+(?P<count>\d+)\s+image\(s\)")
ADAPTER_EVALUATED_PATTERN = re.compile(
    r"Evaluated\s+(?P<count>\d+)\s+rating\(s\),\s+mean absolute error=(?P<mae>[0-9]+(?:\.[0-9]+)?)"
)


RANKER_PROFILE_ROWS = (
    ("Stage", "Waiting for output"),
    ("Run", "Not started"),
    ("Latest Epoch", "n/a"),
    ("Train Loss", "n/a"),
    ("Validation Loss", "n/a"),
    ("Validation Pairwise Acc", "n/a"),
    ("Training Health", "Pending"),
    ("Summary", "Run training or evaluation to get a simple health check."),
    ("Try Next", ""),
)

ADAPTER_PROFILE_ROWS = (
    ("Current Step", "Waiting for output"),
    ("Adapter", "Not started"),
    ("Images Scored", "n/a"),
    ("Labels Imported", "n/a"),
    ("Evaluation MAE", "n/a"),
    ("Score Fit", "n/a"),
    ("Adapter Status", "Pending"),
    ("Summary", "Run adapter training or evaluation to get a simple health check."),
    ("Try Next", ""),
)


class AITrainingStatsDialog(QDialog):
    def __init__(self, *, title: str = "AI Training Stats For Nerds", parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(860, 620)
        self._profile = ""

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

        self._row_title_labels: list[QLabel] = []
        rows = (
            self.stage_value_label,
            self.run_value_label,
            self.epoch_value_label,
            self.train_loss_value_label,
            self.validation_loss_value_label,
            self.validation_accuracy_value_label,
            self.fit_health_value_label,
            self.fit_summary_value_label,
            self.fit_remedy_value_label,
        )
        for row_index, value_label in enumerate(rows):
            key_label = QLabel("", summary_card)
            key_label.setObjectName("mutedText")
            self._row_title_labels.append(key_label)
            summary_layout.addWidget(key_label, row_index, 0)
            summary_layout.addWidget(value_label, row_index, 1)

        root_layout.addWidget(summary_card)

        progress_card = QWidget(self)
        progress_card.setObjectName("aiTrainingStatsCard")
        progress_layout = QGridLayout(progress_card)
        progress_layout.setContentsMargins(14, 14, 14, 14)
        progress_layout.setHorizontalSpacing(18)
        progress_layout.setVerticalSpacing(8)

        self.stage_progress_bar = QProgressBar(progress_card)
        self.stage_progress_bar.setRange(0, 1)
        self.stage_progress_bar.setValue(0)
        self.stage_progress_bar.setFormat("Waiting")
        self.stage_progress_bar.setTextVisible(True)

        self.task_progress_bar = QProgressBar(progress_card)
        self.task_progress_bar.setRange(0, 0)
        self.task_progress_bar.setValue(0)
        self.task_progress_bar.setFormat("Waiting")
        self.task_progress_bar.setTextVisible(True)

        for row_index, (title_text, bar) in enumerate(
            (
                ("Stage Progress", self.stage_progress_bar),
                ("Current Work", self.task_progress_bar),
            )
        ):
            key_label = QLabel(title_text, progress_card)
            key_label.setObjectName("mutedText")
            progress_layout.addWidget(key_label, row_index, 0)
            progress_layout.addWidget(bar, row_index, 1)

        root_layout.addWidget(progress_card)

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
        self.set_profile("ranker")

    def set_profile(self, profile: str) -> None:
        normalized = (profile or "ranker").strip().casefold()
        if normalized not in {"adapter", "ranker"}:
            normalized = "ranker"
        if normalized == self._profile and self._row_title_labels:
            return
        self._profile = normalized
        row_config = ADAPTER_PROFILE_ROWS if normalized == "adapter" else RANKER_PROFILE_ROWS
        value_labels = (
            self.stage_value_label,
            self.run_value_label,
            self.epoch_value_label,
            self.train_loss_value_label,
            self.validation_loss_value_label,
            self.validation_accuracy_value_label,
            self.fit_health_value_label,
            self.fit_summary_value_label,
            self.fit_remedy_value_label,
        )
        for row_label, value_label, (title, default_text) in zip(self._row_title_labels, value_labels, row_config):
            row_label.setText(title)
            value_label.setText(default_text)

    def set_stage_text(self, text: str) -> None:
        self.stage_value_label.setText(text or "Waiting for output")

    def set_status_text(self, text: str) -> None:
        self.set_stage_text(text)

    def set_stage_progress(self, current: int, total: int) -> None:
        if total > 0:
            total_value = max(1, int(total))
            current_value = min(max(int(current), 0), total_value)
            self.stage_progress_bar.setRange(0, total_value)
            self.stage_progress_bar.setValue(current_value)
            self.stage_progress_bar.setFormat(f"Stage {current_value}/{total_value}")
            return
        self.stage_progress_bar.setRange(0, 0)
        self.stage_progress_bar.setFormat("Working")

    def set_task_progress(self, current: int, total: int) -> None:
        if total > 0:
            total_value = max(1, int(total))
            current_value = min(max(int(current), 0), total_value)
            self.task_progress_bar.setRange(0, total_value)
            self.task_progress_bar.setValue(current_value)
            self.task_progress_bar.setFormat(f"{current_value}/{total_value}")
            return
        self.task_progress_bar.setRange(0, 0)
        self.task_progress_bar.setFormat("Working")

    def set_progress(self, current: int, total: int) -> None:
        self.set_stage_progress(current, total)

    def set_stats_button_enabled(self, _enabled: bool) -> None:
        return

    def mark_complete(self, text: str = "Done") -> None:
        self.stage_progress_bar.setRange(0, 1)
        self.stage_progress_bar.setValue(1)
        self.stage_progress_bar.setFormat(text or "Done")
        self.task_progress_bar.setRange(0, 1)
        self.task_progress_bar.setValue(1)
        self.task_progress_bar.setFormat(text or "Done")
        if self._profile == "adapter":
            self.fit_health_value_label.setText(text or "Done")

    def mark_failed(self, text: str = "Failed") -> None:
        self.stage_progress_bar.setRange(0, 1)
        self.stage_progress_bar.setValue(1)
        self.stage_progress_bar.setFormat(text or "Failed")
        self.task_progress_bar.setRange(0, 1)
        self.task_progress_bar.setValue(1)
        self.task_progress_bar.setFormat(text or "Failed")
        if self._profile == "adapter":
            self.fit_health_value_label.setText(text or "Failed")

    def set_run_text(self, text: str) -> None:
        self.run_value_label.setText(text or "Not started")

    def clear_log(self) -> None:
        self.log_view.clear()

    def set_fit_diagnosis(self, label: str, summary: str = "", remedy: str = "") -> None:
        self.fit_health_value_label.setText(label or "Pending")
        default_summary = (
            "Run adapter training or evaluation to get a simple health check."
            if self._profile == "adapter"
            else "Run training or evaluation to get a simple health check."
        )
        self.fit_summary_value_label.setText(summary or default_summary)
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
        for line in lines:
            self._update_metrics_from_line(line)

    def _update_metrics_from_line(self, line: str) -> None:
        if self._profile == "adapter":
            self._update_adapter_metrics_from_line(line)
            return
        match = EPOCH_LOG_PATTERN.search(line)
        if match is None:
            return
        self.epoch_value_label.setText(f"{match.group('epoch')}/{match.group('total')}")
        self.train_loss_value_label.setText(match.group("train_loss"))
        self.validation_loss_value_label.setText(match.group("validation_loss"))
        self.validation_accuracy_value_label.setText(match.group("validation_accuracy"))

    def _update_adapter_metrics_from_line(self, line: str) -> None:
        import_match = IMPORTED_RATINGS_PATTERN.search(line)
        if import_match is not None:
            self.train_loss_value_label.setText(import_match.group("count"))
            self.fit_health_value_label.setText("Labels imported")
            return

        scored_match = ADAPTER_SCORED_PATTERN.search(line)
        if scored_match is not None:
            self.epoch_value_label.setText(scored_match.group("count"))
            self.fit_health_value_label.setText("Adapter trained")
            return

        evaluated_match = ADAPTER_EVALUATED_PATTERN.search(line)
        if evaluated_match is None:
            return
        mae_text = evaluated_match.group("mae")
        self.validation_loss_value_label.setText(mae_text)
        try:
            score_fit = max(0.0, min(100.0, (1.0 - float(mae_text)) * 100.0))
            self.validation_accuracy_value_label.setText(f"{score_fit:.1f}%")
        except ValueError:
            self.validation_accuracy_value_label.setText("n/a")
        self.fit_health_value_label.setText(f"Evaluated {evaluated_match.group('count')} label(s)")
