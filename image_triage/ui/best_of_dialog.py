from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QSpinBox,
    QVBoxLayout,
)

from ..production_workflows import BEST_OF_BALANCED, BEST_OF_TOP_N, BEST_OF_TOP_PER_GROUP
from ..review_workflows import (
    REVIEW_ROUND_HERO,
    REVIEW_ROUND_THIRD_PASS,
    review_round_label,
)


@dataclass(slots=True, frozen=True)
class BestOfDialogResult:
    strategy: str
    limit: int
    review_round: str = ""


class BestOfSetDialog(QDialog):
    def __init__(self, *, visible_count: int, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Best-of-Set Auto Assembly")
        self.resize(480, 260)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        intro = QLabel(
            "Build an editable shortlist from the current set. The result is applied as a selection, so you can still review and override it immediately."
        )
        intro.setWordWrap(True)
        intro.setObjectName("secondaryText")
        layout.addWidget(intro)

        form = QFormLayout()
        form.setSpacing(10)
        layout.addLayout(form)

        self.strategy_combo = QComboBox()
        self.strategy_combo.addItem("Balanced Shortlist", BEST_OF_BALANCED)
        self.strategy_combo.addItem("Top N Overall", BEST_OF_TOP_N)
        self.strategy_combo.addItem("Top Per Group", BEST_OF_TOP_PER_GROUP)
        form.addRow("Strategy", self.strategy_combo)

        self.limit_spin = QSpinBox()
        self.limit_spin.setRange(1, max(1, visible_count))
        self.limit_spin.setValue(min(max(6, min(visible_count, 12)), max(1, visible_count)))
        form.addRow("Proposal Size", self.limit_spin)

        self.round_combo = QComboBox()
        self.round_combo.addItem("Just Select Them", "")
        self.round_combo.addItem(review_round_label(REVIEW_ROUND_THIRD_PASS), REVIEW_ROUND_THIRD_PASS)
        self.round_combo.addItem(review_round_label(REVIEW_ROUND_HERO), REVIEW_ROUND_HERO)
        form.addRow("Optional Mark", self.round_combo)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons, 0, Qt.AlignmentFlag.AlignRight)

    def result_data(self) -> BestOfDialogResult:
        return BestOfDialogResult(
            strategy=str(self.strategy_combo.currentData() or BEST_OF_BALANCED),
            limit=self.limit_spin.value(),
            review_round=str(self.round_combo.currentData() or ""),
        )
