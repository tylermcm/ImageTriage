from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

if TYPE_CHECKING:
    from .actions import MainWindowActions


@dataclass(slots=True, frozen=True)
class ReviewControlsContext:
    selection_count: int = 0
    records_available: bool = False
    decision_label: str = "No image selected"
    decision_state: str = "empty"
    decision_meta: str = ""
    group_summary: str = ""


class ReviewControlsPanel(QFrame):
    next_unreviewed_requested = Signal()
    next_disagreement_requested = Signal()

    def __init__(self, actions: MainWindowActions, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("reviewWorkflowPanel")
        self._actions = actions
        self._context = ReviewControlsContext()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 9, 10, 2)
        layout.setSpacing(8)

        decision_header = QHBoxLayout()
        decision_header.setContentsMargins(0, 0, 0, 0)
        decision_header.setSpacing(6)
        decision_header.addWidget(self._section_title("Decision"), 1)
        self.selection_count_label = QLabel("No selection")
        self.selection_count_label.setObjectName("reviewSelectionCount")
        self.selection_count_label.setMinimumWidth(0)
        decision_header.addWidget(self.selection_count_label, 0)
        layout.addLayout(decision_header)

        decision_row = QHBoxLayout()
        decision_row.setContentsMargins(0, 0, 0, 0)
        decision_row.setSpacing(8)
        self.decision_marker = QFrame()
        self.decision_marker.setObjectName("reviewDecisionMarker")
        self.decision_marker.setFixedSize(8, 8)
        decision_row.addWidget(self.decision_marker, 0, Qt.AlignmentFlag.AlignVCenter)
        decision_text = QVBoxLayout()
        decision_text.setContentsMargins(0, 0, 0, 0)
        decision_text.setSpacing(1)
        self.decision_label = QLabel(self._context.decision_label)
        self.decision_label.setObjectName("reviewDecisionLabel")
        self.decision_label.setMinimumWidth(0)
        self.decision_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.decision_meta_label = QLabel()
        self.decision_meta_label.setObjectName("reviewDecisionMeta")
        self.decision_meta_label.setMinimumWidth(0)
        self.decision_meta_label.setWordWrap(True)
        decision_text.addWidget(self.decision_label)
        decision_text.addWidget(self.decision_meta_label)
        decision_row.addLayout(decision_text, 1)
        layout.addLayout(decision_row)

        decision_actions = QHBoxLayout()
        decision_actions.setContentsMargins(0, 0, 0, 0)
        decision_actions.setSpacing(6)
        winner_button = self._command_button("Winner")
        winner_button.setObjectName("reviewPrimaryDecisionButton")
        self._bind_action(winner_button, actions.accept_selection)
        decision_actions.addWidget(winner_button, 1)
        reject_button = self._command_button("Reject")
        reject_button.setObjectName("reviewPrimaryDecisionButton")
        self._bind_action(reject_button, actions.reject_selection)
        decision_actions.addWidget(reject_button, 1)
        layout.addLayout(decision_actions)

        layout.addWidget(self._divider())
        layout.addWidget(self._section_title("Up Next"))

        queue_grid = QGridLayout()
        queue_grid.setContentsMargins(0, 0, 0, 0)
        queue_grid.setHorizontalSpacing(6)
        queue_grid.setVerticalSpacing(6)
        self.next_unreviewed_button = self._command_button("Unreviewed")
        self.next_unreviewed_button.setToolTip("Jump to the next unreviewed image")
        self.next_unreviewed_button.clicked.connect(self.next_unreviewed_requested.emit)
        queue_grid.addWidget(self.next_unreviewed_button, 0, 0)

        next_ai_button = self._command_button("AI Pick")
        self._bind_action(next_ai_button, actions.next_ai_pick)
        queue_grid.addWidget(next_ai_button, 0, 1)

        self.next_disagreement_button = self._command_button("Disagree")
        self.next_disagreement_button.setToolTip("Jump to the next AI disagreement")
        self.next_disagreement_button.clicked.connect(self.next_disagreement_requested.emit)
        self._follow_action_enabled(
            self.next_disagreement_button,
            actions.review_ai_disagreements,
        )
        queue_grid.addWidget(self.next_disagreement_button, 0, 2)
        queue_grid.setColumnStretch(0, 1)
        queue_grid.setColumnStretch(1, 1)
        queue_grid.setColumnStretch(2, 1)
        layout.addLayout(queue_grid)

        self.group_section = QWidget(self)
        self.group_section.setObjectName("reviewGroupSection")
        group_layout = QVBoxLayout(self.group_section)
        group_layout.setContentsMargins(0, 0, 0, 0)
        group_layout.setSpacing(6)
        group_layout.addWidget(self._divider())
        group_header = QHBoxLayout()
        group_header.setContentsMargins(0, 0, 0, 0)
        group_header.setSpacing(6)
        group_header.addWidget(self._section_title("Similar Group"), 1)
        self.group_summary_label = QLabel()
        self.group_summary_label.setObjectName("reviewGroupSummary")
        self.group_summary_label.setMinimumWidth(0)
        group_header.addWidget(self.group_summary_label, 0)
        group_layout.addLayout(group_header)

        group_actions = QHBoxLayout()
        group_actions.setContentsMargins(0, 0, 0, 0)
        group_actions.setSpacing(6)
        compare_button = self._command_button("Compare")
        self._bind_action(compare_button, actions.compare_ai_group)
        group_actions.addWidget(compare_button, 1)
        ladder_button = self._command_button("Ladder")
        self._bind_action(ladder_button, actions.winner_ladder_mode)
        group_actions.addWidget(ladder_button, 1)
        group_layout.addLayout(group_actions)
        layout.addWidget(self.group_section)
        self.group_section.hide()

    @staticmethod
    def _section_title(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("reviewSectionTitle")
        label.setMinimumWidth(0)
        label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        return label

    @staticmethod
    def _divider() -> QFrame:
        divider = QFrame()
        divider.setObjectName("reviewSectionDivider")
        divider.setFrameShape(QFrame.Shape.HLine)
        return divider

    def _command_button(self, text: str) -> QPushButton:
        button = QPushButton(text, self)
        button.setObjectName("reviewCommandButton")
        button.setMinimumWidth(0)
        button.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        return button

    @staticmethod
    def _bind_action(button: QToolButton | QPushButton, action: QAction) -> None:
        if not button.toolTip():
            button.setToolTip(action.toolTip() or action.text())
        button.clicked.connect(lambda _checked=False, target=action: target.trigger())

        ReviewControlsPanel._follow_action_enabled(button, action)

    @staticmethod
    def _follow_action_enabled(button: QToolButton | QPushButton, action: QAction) -> None:
        def sync() -> None:
            try:
                button.setEnabled(action.isEnabled())
            except RuntimeError:
                return

        action.changed.connect(sync)
        sync()

    def set_context(self, context: ReviewControlsContext) -> None:
        self._context = context
        if context.selection_count <= 0:
            selection_text = "No selection"
        elif context.selection_count == 1:
            selection_text = "1 selected"
        else:
            selection_text = f"{context.selection_count} selected"
        self.selection_count_label.setText(selection_text)
        self.decision_label.setText(context.decision_label)
        self.decision_meta_label.setText(context.decision_meta)
        self.decision_meta_label.setVisible(bool(context.decision_meta))

        if self.decision_marker.property("decisionState") != context.decision_state:
            self.decision_marker.setProperty("decisionState", context.decision_state)
            self.decision_marker.style().unpolish(self.decision_marker)
            self.decision_marker.style().polish(self.decision_marker)

        self.next_unreviewed_button.setEnabled(context.records_available)
        self.group_summary_label.setText(context.group_summary)
        self.group_section.setVisible(bool(context.group_summary))
