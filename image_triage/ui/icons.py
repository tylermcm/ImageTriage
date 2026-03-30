from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPainterPath, QPen, QPixmap


def build_symbol_icon(symbol: str, color: QColor, *, pixel_size: int = 18, font_size: int = 13) -> QIcon:
    pixmap = QPixmap(pixel_size, pixel_size)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
    painter.setPen(color)
    font = QFont("Segoe UI Symbol", font_size)
    font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
    painter.setFont(font)
    painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, symbol)
    painter.end()

    return QIcon(pixmap)


def _draw_undo_pixmap(color: QColor, *, pixel_size: int, stroke_width: float) -> QPixmap:
    pixmap = QPixmap(pixel_size, pixel_size)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    pen = QPen(color, stroke_width)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)

    path = QPainterPath()
    path.moveTo(pixel_size * 0.76, pixel_size * 0.34)
    path.cubicTo(
        pixel_size * 0.60,
        pixel_size * 0.18,
        pixel_size * 0.34,
        pixel_size * 0.22,
        pixel_size * 0.24,
        pixel_size * 0.43,
    )
    path.cubicTo(
        pixel_size * 0.18,
        pixel_size * 0.58,
        pixel_size * 0.22,
        pixel_size * 0.75,
        pixel_size * 0.36,
        pixel_size * 0.80,
    )
    painter.drawPath(path)

    arrow = QPainterPath()
    arrow.moveTo(pixel_size * 0.24, pixel_size * 0.43)
    arrow.lineTo(pixel_size * 0.39, pixel_size * 0.30)
    arrow.moveTo(pixel_size * 0.24, pixel_size * 0.43)
    arrow.lineTo(pixel_size * 0.39, pixel_size * 0.56)
    painter.drawPath(arrow)

    painter.end()
    return pixmap


def build_undo_icon(
    color: QColor,
    *,
    disabled_color: QColor | None = None,
    pixel_size: int = 18,
    stroke_width: float = 1.8,
) -> QIcon:
    icon = QIcon(_draw_undo_pixmap(color, pixel_size=pixel_size, stroke_width=stroke_width))
    if disabled_color is not None:
        icon.addPixmap(
            _draw_undo_pixmap(disabled_color, pixel_size=pixel_size, stroke_width=stroke_width),
            QIcon.Mode.Disabled,
        )
    return icon
