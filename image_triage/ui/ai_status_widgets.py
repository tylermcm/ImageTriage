"""Widgets of the AI status: the chip in the top bar (a slow conic gradient ring around it), the quiet "AI ready" text in the status bar, and the card that
hangs from either with an arrow. They only paint and report clicks; ``ai_status_controller`` decides what they say.
"""
from __future__ import annotations

from typing import Callable, Sequence

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QVariantAnimation
from PySide6.QtGui import QColor, QConicalGradient, QFontMetrics, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QAbstractButton,
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..ai_status import AIState, AIStatus

RING_COLORS = ("#ff9a5a", "#ff5e8a", "#b25cff", "#5a8cff", "#ff9a5a")
DOT_COLORS = {
    AIState.READY: "#1f9d63",
    AIState.NEEDS_SETUP: "#c27c0e",
    AIState.INSTALLING: "#b25cff",
    AIState.UPDATE: "#5a8cff",
    AIState.REPAIR: "#d1423f",
}
RING_PERIOD_MS = 7000  # one slow turn


def _ring_gradient(center: QPointF, angle: float) -> QConicalGradient:
    gradient = QConicalGradient(center, angle)
    for index, color in enumerate(RING_COLORS):
        gradient.setColorAt(index / (len(RING_COLORS) - 1), QColor(color))
    return gradient


class AIStatusChip(QAbstractButton):
    """The pill in the top bar for every status that needs the person. A conic gradient ring turns slowly around it."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("aiStatusChip")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self._status = AIStatus(AIState.NEEDS_SETUP, "Set up AI")
        self._angle = 0.0
        self._animation = QVariantAnimation(self)
        self._animation.setStartValue(0.0)
        self._animation.setEndValue(360.0)
        self._animation.setDuration(RING_PERIOD_MS)
        self._animation.setLoopCount(-1)
        self._animation.valueChanged.connect(self._turn)
        self.setFixedHeight(28)

    def status(self) -> AIStatus:
        return self._status

    def set_status(self, status: AIStatus) -> None:
        self._status = status
        self.setAccessibleName(f"{status.label} {status.small}".strip())
        self.setToolTip(status.detail)
        self.updateGeometry()
        self.update()

    def _turn(self, value: object) -> None:
        self._angle = float(value)  # type: ignore[arg-type]
        self.update()

    def ring_running(self) -> bool:
        return self._animation.state() == QVariantAnimation.State.Running

    def showEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().showEvent(event)
        self._animation.start()

    def hideEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().hideEvent(event)
        self._animation.stop()

    def _small_font(self):
        font = self.font()
        font.setPointSizeF(max(7.0, font.pointSizeF() * 0.92))
        return font

    def sizeHint(self) -> QSize:
        metrics = QFontMetrics(self.font())
        width = 14 + 8 + 7 + metrics.horizontalAdvance(self._status.label) + 14
        if self._status.small:
            width += 7 + QFontMetrics(self._small_font()).horizontalAdvance(self._status.small)
        return QSize(width, 28)

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt override
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        outer = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        path = QPainterPath()
        path.addRoundedRect(outer, outer.height() / 2, outer.height() / 2)
        painter.fillPath(path, self.palette().base())
        pen = QPen(_ring_gradient(outer.center(), -self._angle), 1.6)
        painter.setPen(pen)
        painter.drawPath(path)
        if self.hasFocus():
            painter.setPen(QPen(self.palette().highlight().color(), 1.0))
            painter.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1), 14, 14)
        # the status dot
        dot = QRectF(14, self.height() / 2 - 4, 8, 8)
        painter.setPen(Qt.PenStyle.NoPen)
        if self._status.state is AIState.INSTALLING:
            painter.setBrush(_ring_gradient(dot.center(), self._angle * 4))
        else:
            painter.setBrush(QColor(DOT_COLORS[self._status.state]))
        painter.drawEllipse(dot)
        painter.setPen(self.palette().text().color())
        metrics = QFontMetrics(self.font())
        x = int(dot.right()) + 7
        baseline = int(self.height() / 2 + (metrics.ascent() - metrics.descent()) / 2)
        painter.setFont(self.font())
        painter.drawText(x, baseline, self._status.label)
        if self._status.small:
            x += metrics.horizontalAdvance(self._status.label) + 7
            painter.setFont(self._small_font())
            painter.setPen(self.palette().mid().color())
            painter.drawText(x, baseline, self._status.small)


class AIStatusText(QAbstractButton):
    """"AI ready" in the status bar: a small green dot and plain text, the same size as the text around it. The text is the button."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("aiStatusText")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setText("AI ready")
        self.setAccessibleName("AI ready")
        self._hover = False

    def sizeHint(self) -> QSize:
        return QSize(QFontMetrics(self.font()).horizontalAdvance(self.text()) + 18, QFontMetrics(self.font()).height() + 4)

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def enterEvent(self, event) -> None:  # noqa: N802 - Qt override
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802 - Qt override
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt override
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(DOT_COLORS[AIState.READY]))
        painter.drawEllipse(QRectF(1, self.height() / 2 - 3, 6, 6))
        font = self.font()
        font.setUnderline(self._hover or self.hasFocus())
        painter.setFont(font)
        painter.setPen(self.palette().windowText().color())
        metrics = QFontMetrics(font)
        painter.drawText(14, int(self.height() / 2 + (metrics.ascent() - metrics.descent()) / 2), self.text())


class AIStatusPopover(QFrame):
    """A rounded card with an arrow pointing at the widget it hangs from (below it for the top bar, above it for the status bar)."""

    ARROW = 8

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint | Qt.WindowType.NoDropShadowWindowHint)
        self.setObjectName("aiStatusPopover")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._below = True
        self._arrow_x = 40
        self._body: QWidget | None = None
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)

    def set_body(self, body: QWidget) -> None:
        if self._body is not None:
            self._layout.removeWidget(self._body)
            self._body.deleteLater()
        self._body = body
        self._layout.addWidget(body)
        self._apply_margins()
        self.adjustSize()

    def _apply_margins(self) -> None:
        top = self.ARROW + 1 if self._below else 1
        bottom = 1 if self._below else self.ARROW + 1
        self._layout.setContentsMargins(1, top, 1, bottom)

    def popup_at(self, anchor: QWidget, *, below: bool) -> None:
        self._below = below
        self._apply_margins()
        self.adjustSize()
        origin = anchor.mapToGlobal(anchor.rect().bottomLeft() if below else anchor.rect().topLeft())
        center_x = anchor.mapToGlobal(anchor.rect().center()).x()
        screen = QApplication.screenAt(origin) or QApplication.primaryScreen()
        area = screen.availableGeometry()
        width, height = self.width(), self.height()
        x = max(area.left() + 6, min(center_x - 36, area.right() - width - 6))
        y = origin.y() + 2 if below else origin.y() - height - 2
        self._arrow_x = max(20, min(center_x - x, width - 20))
        self.move(x, y)
        self.show()

    def arrow_x(self) -> int:
        return self._arrow_x

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt override
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        arrow = self.ARROW
        top = arrow if self._below else 0
        card = QRectF(0.5, top + 0.5, self.width() - 1, self.height() - arrow - 1)
        path = QPainterPath()
        path.addRoundedRect(card, 12, 12)
        x = float(self._arrow_x)
        tip = QPainterPath()
        if self._below:
            tip.moveTo(x - arrow, card.top())
            tip.lineTo(x, card.top() - arrow)
            tip.lineTo(x + arrow, card.top())
        else:
            tip.moveTo(x - arrow, card.bottom())
            tip.lineTo(x, card.bottom() + arrow)
            tip.lineTo(x + arrow, card.bottom())
        tip.closeSubpath()
        shape = path.united(tip)
        painter.fillPath(shape, self.palette().base())
        painter.setPen(QPen(self.palette().midlight().color(), 1.0))
        painter.drawPath(shape)


def build_card(
    title: str,
    text: str = "",
    *,
    rows: Sequence[tuple[str, str]] = (),
    buttons: Sequence[tuple[str, bool, Callable[[], None]]] = (),
    progress: int | None = None,
    width: int = 340,
) -> tuple[QWidget, dict[str, QPushButton]]:
    """The content of a popover: a title, a short text, label/value rows, an optional progress bar and the buttons (label, primary, callback)."""
    card = QWidget()
    card.setObjectName("aiStatusCard")
    card.setFixedWidth(width)
    layout = QVBoxLayout(card)
    layout.setContentsMargins(16, 14, 16, 14)
    layout.setSpacing(8)
    heading = QLabel(title, card)
    heading.setObjectName("aiStatusCardTitle")
    heading.setStyleSheet("font-size: 15px; font-weight: 650;")
    layout.addWidget(heading)
    if text:
        body = QLabel(text, card)
        body.setWordWrap(True)
        body.setObjectName("aiStatusCardText")
        body.setStyleSheet("color: palette(mid);")
        layout.addWidget(body)
    if progress is not None:
        bar = QProgressBar(card)
        bar.setRange(0, 100)
        bar.setValue(max(0, min(100, progress)))
        bar.setTextVisible(False)
        bar.setFixedHeight(6)
        layout.addWidget(bar)
    for label, value in rows:
        row = QFrame(card)
        row.setStyleSheet("QFrame { background: palette(alternate-base); border-radius: 8px; }")
        line = QHBoxLayout(row)
        line.setContentsMargins(9, 6, 9, 6)
        left = QLabel(label, row)
        right = QLabel(value, row)
        right.setStyleSheet("font-weight: 600;")
        line.addWidget(left, 1)
        line.addWidget(right, 0)
        layout.addWidget(row)
    made: dict[str, QPushButton] = {}
    if buttons:
        line = QHBoxLayout()
        line.setSpacing(8)
        for label, primary, callback in buttons:
            button = QPushButton(label, card)
            if primary:
                button.setObjectName("editorPrimaryButton")
                button.setDefault(True)
            button.clicked.connect(lambda _checked=False, cb=callback: cb())
            line.addWidget(button)
            made[label] = button
        line.addStretch(1)
        layout.addLayout(line)
    return card, made
