from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QWidget


_BACKGROUND_PATH = Path(__file__).resolve().parent / "assets" / "splash_background-v4.png"
_BACKGROUND_ASPECT = 1633 / 963


class StartupSplash(QWidget):
    """Branded launch surface with runtime-rendered status and version data."""

    def __init__(self, version: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("startupSplash")
        self.setWindowFlags(
            Qt.WindowType.SplashScreen
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(1020, round(1020 / _BACKGROUND_ASPECT))
        self._background = QPixmap(str(_BACKGROUND_PATH))
        self._status = "Starting…"
        self._progress = 8
        self._version = str(version).strip()

    @property
    def progress(self) -> int:
        return self._progress

    @property
    def status(self) -> str:
        return self._status

    @property
    def version_text(self) -> str:
        return f"v{self._version}" if self._version else ""

    def show_centered(self) -> None:
        screen = self.screen()
        if screen is not None:
            area = screen.availableGeometry()
            target_width = min(1100, max(560, int(area.width() * 0.72)))
            target_width = min(target_width, area.width() - 48)
            target_height = round(target_width / _BACKGROUND_ASPECT)
            if target_height > area.height() - 48:
                target_height = area.height() - 48
                target_width = round(target_height * _BACKGROUND_ASPECT)
            self.setFixedSize(target_width, target_height)
            self.move(area.center() - self.rect().center())
        self.show()
        self.raise_()

    def set_status(self, message: str, progress: int | None = None) -> None:
        self._status = str(message)
        if progress is not None:
            self._progress = max(0, min(100, int(progress)))
        self.update()

    def finish(self, window: QWidget) -> None:
        window.show()
        window.raise_()
        window.activateWindow()
        QTimer.singleShot(120, self.close)

    def paintEvent(self, _event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        target = QRectF(0, 0, self.width(), self.height())
        if not self._background.isNull():
            painter.drawPixmap(target, self._background, QRectF(self._background.rect()))
        else:
            painter.fillRect(target, QColor("#0b0e19"))

        scale = self.width() / 1633.0
        left = 115 * scale
        right = self.width() - (115 * scale)
        status_y = 805 * scale
        bar_y = 849 * scale
        bar_height = max(8.0, 25 * scale)

        status_font = QFont("Segoe UI")
        status_font.setPixelSize(max(12, round(25 * scale)))
        painter.setFont(status_font)
        painter.setPen(QColor("#b9c9e2"))
        painter.drawText(
            QRectF(left, status_y, right - left, 34 * scale),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            self._status,
        )

        track = QRectF(left, bar_y, right - left, bar_height)
        radius = bar_height / 2
        painter.setBrush(QColor(20, 25, 42, 224))
        painter.setPen(QPen(QColor("#3e475f"), max(1.0, 1.5 * scale)))
        painter.drawRoundedRect(track, radius, radius)

        inset = max(2.0, 3 * scale)
        inner = track.adjusted(inset, inset, -inset, -inset)
        fill_width = inner.width() * (self._progress / 100.0)
        if fill_width > 0:
            fill = QRectF(inner.left(), inner.top(), max(inner.height(), fill_width), inner.height())
            fill = fill.intersected(inner)
            gradient = QLinearGradient(fill.left(), fill.top(), fill.right(), fill.top())
            gradient.setColorAt(0.0, QColor("#35e7dd"))
            gradient.setColorAt(0.55, QColor("#66dffc"))
            gradient.setColorAt(1.0, QColor("#a06df5"))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(gradient)
            painter.drawRoundedRect(fill, inner.height() / 2, inner.height() / 2)

        if self.version_text:
            version_font = QFont("Segoe UI")
            version_font.setPixelSize(max(11, round(20 * scale)))
            painter.setFont(version_font)
            painter.setPen(QColor("#9caac2"))
            corner_margin = max(14.0, 28 * scale)
            version_height = max(18.0, 30 * scale)
            painter.drawText(
                QRectF(
                    self.width() * 0.72,
                    self.height() - corner_margin - version_height,
                    (self.width() * 0.28) - corner_margin,
                    version_height,
                ),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                self.version_text,
            )
