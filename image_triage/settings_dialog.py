from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from .models import DeleteMode, WinnerMode


@dataclass(slots=True, frozen=True)
class WorkflowSettingsResult:
    session_id: str
    winner_mode: WinnerMode
    delete_mode: DeleteMode


class WorkflowSettingsDialog(QDialog):
    def __init__(
        self,
        *,
        sessions: list[str],
        current_session: str,
        winner_mode: WinnerMode,
        delete_mode: DeleteMode,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Workflow Settings")
        self.setModal(True)
        self.resize(460, 260)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(14)

        intro = QLabel(
            "Adjust how decisions are stored, how accepted images are handled, and how deletes behave."
        )
        intro.setWordWrap(True)
        intro.setObjectName("secondaryText")
        layout.addWidget(intro)

        workflow_group = QGroupBox("Workflow")
        workflow_layout = QFormLayout(workflow_group)
        workflow_layout.setContentsMargins(12, 14, 12, 12)
        workflow_layout.setSpacing(12)

        self.session_combo = QComboBox()
        self.session_combo.setEditable(True)
        self.session_combo.addItems(sessions)
        self.session_combo.setCurrentText(current_session)
        self.session_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)

        self.winner_mode_combo = QComboBox()
        for mode in WinnerMode:
            self.winner_mode_combo.addItem(mode.value, mode)
        self.winner_mode_combo.setCurrentIndex(max(0, self.winner_mode_combo.findData(winner_mode)))

        self.delete_mode_combo = QComboBox()
        for mode in DeleteMode:
            self.delete_mode_combo.addItem(mode.value, mode)
        self.delete_mode_combo.setCurrentIndex(max(0, self.delete_mode_combo.findData(delete_mode)))

        workflow_layout.addRow("Session", self.session_combo)
        workflow_layout.addRow("Accepted handling", self.winner_mode_combo)
        workflow_layout.addRow("Delete behavior", self.delete_mode_combo)
        layout.addWidget(workflow_group)

        notes = QWidget()
        notes_layout = QVBoxLayout(notes)
        notes_layout.setContentsMargins(2, 0, 2, 0)
        notes_layout.setSpacing(4)
        for text in (
            "Sessions keep separate accept/reject/rating/tag states for the same files.",
            "Link mode uses hardlinks when possible and falls back to copy on unsupported drives.",
            "Safe Trash moves files into Image Triage recovery storage so Ctrl+Z can restore them.",
        ):
            label = QLabel(text)
            label.setWordWrap(True)
            label.setObjectName("mutedText")
            label.setStyleSheet("font-size: 11px;")
            notes_layout.addWidget(label)
        layout.addWidget(notes)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons, 0, Qt.AlignmentFlag.AlignRight)

    def result_settings(self) -> WorkflowSettingsResult:
        session_id = (self.session_combo.currentText() or "").strip()
        winner_mode = self.winner_mode_combo.currentData()
        delete_mode = self.delete_mode_combo.currentData()
        if not isinstance(winner_mode, WinnerMode):
            winner_mode = WinnerMode.COPY
        if not isinstance(delete_mode, DeleteMode):
            delete_mode = DeleteMode.SAFE_TRASH
        return WorkflowSettingsResult(
            session_id=session_id or "Default",
            winner_mode=winner_mode,
            delete_mode=delete_mode,
        )
