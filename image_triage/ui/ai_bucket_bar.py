"""The strip above the grid that counts each AI verdict and filters by it.

It exists only while the open folder has AI results: before a cull the grid looks exactly as it always did.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QWidget

from ..ai_results import AICullBucket

BUCKET_ORDER: tuple[AICullBucket, ...] = (
    AICullBucket.AI_PICK,
    AICullBucket.KEEPER,
    AICullBucket.NEEDS_REVIEW,
    AICullBucket.REJECT,
)
BUCKET_LABELS = {
    AICullBucket.AI_PICK: "Winners",
    AICullBucket.KEEPER: "Keepers",
    AICullBucket.NEEDS_REVIEW: "Needs review",
    AICullBucket.REJECT: "Rejects",
}
# Same colours as the dots painted on the grid cards (ThumbnailGridView._ai_verdict_dot_color).
BUCKET_COLORS = {
    AICullBucket.AI_PICK: QColor("#3fb97a"),
    AICullBucket.KEEPER: QColor("#4b8ff0"),
    AICullBucket.NEEDS_REVIEW: QColor("#e0b04a"),
    AICullBucket.REJECT: QColor("#e5635b"),
}


class _BucketChip(QPushButton):
    def __init__(self, bucket: AICullBucket, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.bucket = bucket
        self.setCheckable(True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet("QPushButton { padding: 3px 12px 3px 24px; }")

    def paintEvent(self, event) -> None:  # type: ignore[override]
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(BUCKET_COLORS[self.bucket])
        radius = 4.5
        painter.drawEllipse(12 - radius, self.height() / 2 - radius, radius * 2, radius * 2)
        painter.end()


class AIBucketBar(QWidget):
    """Counts per AI verdict; clicking a chip asks for that bucket as the filter (clicking it again clears it)."""

    bucket_requested = Signal(object)  # AICullBucket, or None to clear

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("workspaceControls")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 4, 12, 4)
        layout.setSpacing(8)
        self._title = QLabel("AI results")
        self._title.setObjectName("secondaryText")
        layout.addWidget(self._title)
        self._chips: dict[AICullBucket, _BucketChip] = {}
        for bucket in BUCKET_ORDER:
            chip = _BucketChip(bucket, self)
            chip.clicked.connect(lambda _checked=False, b=bucket: self._chip_clicked(b))
            layout.addWidget(chip)
            self._chips[bucket] = chip
        layout.addStretch(1)
        self._active: AICullBucket | None = None
        self.hide()

    def chip(self, bucket: AICullBucket) -> _BucketChip:
        return self._chips[bucket]

    def set_state(self, counts: dict[AICullBucket, int] | None, active: AICullBucket | None) -> None:
        """Show the bar with these counts, or hide it when there are no AI results."""
        total = sum(int(value) for value in (counts or {}).values())
        if not counts or total <= 0:
            self._active = None
            self.hide()
            return
        self._active = active
        for bucket, chip in self._chips.items():
            chip.setText(f"{BUCKET_LABELS[bucket]} {int(counts.get(bucket, 0)):,}")
            chip.setChecked(bucket == active)
        self.show()

    def _chip_clicked(self, bucket: AICullBucket) -> None:
        self.bucket_requested.emit(None if bucket == self._active else bucket)
