from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
)


class AIReviewProgressDialog(QDialog):
    """Modeless progress window for the folder AI Review run."""

    stop_requested = Signal()

    def __init__(self, *, detailed: bool = False, parent=None) -> None:
        super().__init__(parent)
        self._running = True
        self._stop_requested = False
        self._detailed = bool(detailed)

        self.setWindowTitle("AI Review")
        self.setModal(False)
        self.setMinimumWidth(560)
        self.resize(720 if detailed else 560, 520 if detailed else 260)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        title = QLabel("AI Review", self)
        title.setObjectName("dialogTitle")
        layout.addWidget(title)

        self.folder_label = QLabel("-", self)
        self.folder_label.setObjectName("mutedText")
        self.folder_label.setWordWrap(True)
        layout.addWidget(self.folder_label)

        self.stage_label = QLabel("Queued", self)
        self.stage_label.setWordWrap(True)
        layout.addWidget(self.stage_label)

        self.stage_progress = QProgressBar(self)
        self.stage_progress.setRange(0, 1)
        self.stage_progress.setValue(0)
        self.stage_progress.setFormat("Stage")
        layout.addWidget(self.stage_progress)

        self.progress_label = QLabel("Waiting to start", self)
        self.progress_label.setObjectName("secondaryText")
        self.progress_label.setWordWrap(True)
        layout.addWidget(self.progress_label)

        self.item_progress = QProgressBar(self)
        self.item_progress.setRange(0, 0)
        self.item_progress.setValue(0)
        layout.addWidget(self.item_progress)

        self.detail_label = QLabel("Activity", self)
        self.detail_label.setObjectName("sectionLabel")
        layout.addWidget(self.detail_label)

        self.detail_log = QPlainTextEdit(self)
        self.detail_log.setObjectName("aiReviewProgressLog")
        self.detail_log.setReadOnly(True)
        self.detail_log.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.detail_log.document().setMaximumBlockCount(350)
        layout.addWidget(self.detail_log, 1)

        self.detail_label.setVisible(self._detailed)
        self.detail_log.setVisible(self._detailed)

        button_row = QHBoxLayout()
        button_row.setContentsMargins(0, 0, 0, 0)
        button_row.addStretch(1)

        self.hide_button = QPushButton("Hide", self)
        self.hide_button.clicked.connect(self.hide)
        button_row.addWidget(self.hide_button)

        self.stop_button = QPushButton("Stop", self)
        self.stop_button.clicked.connect(self._handle_stop_clicked)
        button_row.addWidget(self.stop_button)

        layout.addLayout(button_row)

    def start_run(self, *, folder: str, stage_total: int) -> None:
        self._running = True
        self._stop_requested = False
        self.folder_label.setText(folder or "-")
        self.stage_progress.setRange(0, max(1, stage_total))
        self.stage_progress.setValue(0)
        self.stage_progress.setFormat(f"Stage 0/{max(1, stage_total)}")
        self.item_progress.setRange(0, 0)
        self.progress_label.setText("Starting AI Review")
        self.stop_button.setEnabled(True)
        self.stop_button.setText("Stop")
        self.hide_button.setEnabled(True)
        self.append_detail("AI Review queued.")

    def set_stage(self, *, stage_index: int, stage_total: int, message: str) -> None:
        total = max(1, int(stage_total))
        current = min(max(0, int(stage_index)), total)
        self.stage_label.setText(message or "Running AI Review")
        self.stage_progress.setRange(0, total)
        self.stage_progress.setValue(current)
        self.stage_progress.setFormat(f"Stage {current}/{total}")
        self.item_progress.setRange(0, 0)
        self.progress_label.setText("Preparing stage")

    def set_progress(self, *, message: str, current: int, total: int, eta_text: str = "") -> None:
        current = max(0, int(current))
        total = max(0, int(total))
        parts = [message or "Running"]
        if total > 0:
            parts.append(f"{min(current, total)}/{total}")
        if eta_text:
            parts.append(f"{eta_text} left")
        self.progress_label.setText(" | ".join(parts))
        if total > 0:
            self.item_progress.setRange(0, max(1, total))
            self.item_progress.setValue(min(current, total))
            self.item_progress.setFormat(f"{min(current, total)}/{total}")
        else:
            self.item_progress.setRange(0, 0)
            self.item_progress.setValue(0)
            self.item_progress.setFormat("")

    def append_detail(self, text: str) -> None:
        text = " ".join((text or "").split())
        if not text:
            return
        self.detail_log.appendPlainText(text)

    def set_stopping(self) -> None:
        self._stop_requested = True
        self.stop_button.setEnabled(False)
        self.stop_button.setText("Stopping...")
        self.stage_label.setText("Stopping AI Review")
        self.progress_label.setText("Waiting for the current AI step to stop")
        self.append_detail("Stop requested.")

    def mark_finished(self, message: str = "AI Review complete") -> None:
        self._running = False
        self.stage_label.setText(message)
        self.progress_label.setText(message)
        self.stage_progress.setValue(self.stage_progress.maximum())
        if self.item_progress.maximum() > 0:
            self.item_progress.setValue(self.item_progress.maximum())
        else:
            self.item_progress.setRange(0, 1)
            self.item_progress.setValue(1)
        self.stop_button.setEnabled(True)
        self.stop_button.setText("Close")
        self.hide_button.setEnabled(False)
        self.append_detail(message)

    def finish_and_close(self, message: str = "AI Review complete") -> None:
        self.mark_finished(message)
        self.accept()

    def mark_failed(self, message: str = "AI Review failed") -> None:
        self._running = False
        self.stage_label.setText(message)
        self.progress_label.setText(message)
        self.item_progress.setRange(0, 1)
        self.item_progress.setValue(0)
        self.stop_button.setEnabled(True)
        self.stop_button.setText("Close")
        self.hide_button.setEnabled(False)
        self.append_detail(message)

    def closeEvent(self, event) -> None:  # type: ignore[override]
        if self._running:
            event.ignore()
            self.hide()
            return
        super().closeEvent(event)

    def _handle_stop_clicked(self) -> None:
        if not self._running:
            self.close()
            return
        if self._stop_requested:
            return
        self.set_stopping()
        self.stop_requested.emit()
