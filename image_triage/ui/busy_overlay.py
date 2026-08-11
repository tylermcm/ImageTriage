"""Reusable in-place busy indicator for long-running AI setup work."""
from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFontMetricsF, QPainter, QPen
from PySide6.QtWidgets import QWidget


def paint_busy_card(
    painter: QPainter,
    *,
    width: int,
    height: int,
    message: str,
    angle: int,
) -> None:
    """Paint the shared AI spinner card used in the editor and main window."""
    if not message:
        return
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    metrics = QFontMetricsF(painter.font())
    spinner = 26.0
    pad = 18.0
    gap = 12.0
    text_width = metrics.horizontalAdvance(message)
    card_width = max(text_width, spinner) + pad * 2
    card_height = spinner + gap + metrics.height() + pad * 2
    center_x = width / 2.0
    center_y = height / 2.0
    card = QRectF(
        center_x - card_width / 2.0,
        center_y - card_height / 2.0,
        card_width,
        card_height,
    )
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(18, 18, 18, 220))
    painter.drawRoundedRect(card, 10.0, 10.0)

    spinner_y = card.top() + pad + spinner / 2.0
    ring = QRectF(
        center_x - spinner / 2.0,
        spinner_y - spinner / 2.0,
        spinner,
        spinner,
    )
    track = QPen(QColor(255, 255, 255, 60), 3.0)
    track.setCapStyle(Qt.PenCapStyle.RoundCap)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(track)
    painter.drawEllipse(ring)
    arc = QPen(QColor(74, 158, 255, 235), 3.0)
    arc.setCapStyle(Qt.PenCapStyle.RoundCap)
    painter.setPen(arc)
    painter.drawArc(ring, int(-angle * 16), int(-100 * 16))

    text_rect = QRectF(
        card.left(),
        spinner_y + spinner / 2.0 + gap,
        card_width,
        metrics.height(),
    )
    painter.setPen(QPen(QColor(240, 240, 240)))
    painter.drawText(text_rect, Qt.AlignmentFlag.AlignCenter, message)
    painter.restore()


class BusyOverlay(QWidget):
    """Cover a host widget with the same centered spinner used by AI masks."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._host: QWidget | None = None
        self._message = ""
        self._angle = 0
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.hide()

        self._timer = QTimer(self)
        self._timer.setInterval(60)
        self._timer.timeout.connect(self._advance_spinner)

    @property
    def message(self) -> str:
        return self._message

    def attach_to(self, host: QWidget) -> None:
        if self._host is host:
            self.setGeometry(host.rect())
            return
        if self._host is not None:
            self._host.removeEventFilter(self)
        self._host = host
        self.setParent(host)
        host.installEventFilter(self)
        self.setGeometry(host.rect())

    def set_message(self, message: str | None) -> None:
        self._message = str(message or "").strip()
        if not self._message:
            self._timer.stop()
            self.hide()
            return
        if self._host is not None:
            self.setGeometry(self._host.rect())
        self.raise_()
        self.show()
        self._timer.start()
        self.update()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is self._host and event.type() in {
            QEvent.Type.Resize,
            QEvent.Type.Show,
        }:
            self.setGeometry(self._host.rect())
            if self._message:
                self.raise_()
        return super().eventFilter(watched, event)

    def paintEvent(self, event) -> None:
        del event
        painter = QPainter(self)
        paint_busy_card(
            painter,
            width=self.width(),
            height=self.height(),
            message=self._message,
            angle=self._angle,
        )

    def _advance_spinner(self) -> None:
        self._angle = (self._angle + 18) % 360
        self.update()
