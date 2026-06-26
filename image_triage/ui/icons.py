from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPainterPath, QPen, QPixmap, QTransform


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
    pen.setCosmetic(True)
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


def _draw_arrow_pixmap(
    color: QColor,
    *,
    pointing_left: bool,
    pixel_size: int,
    stroke_width: float,
) -> QPixmap:
    pixmap = QPixmap(pixel_size, pixel_size)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    pen = QPen(color, stroke_width)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)

    s = float(pixel_size)
    path = QPainterPath()
    path.moveTo(s * 0.26, s * 0.50)  # shaft
    path.lineTo(s * 0.76, s * 0.50)
    path.moveTo(s * 0.76, s * 0.50)  # upper barb
    path.lineTo(s * 0.57, s * 0.31)
    path.moveTo(s * 0.76, s * 0.50)  # lower barb
    path.lineTo(s * 0.57, s * 0.69)
    if pointing_left:
        flip = QTransform()
        flip.translate(s, 0.0)
        flip.scale(-1.0, 1.0)
        path = flip.map(path)
    painter.drawPath(path)
    painter.end()
    return pixmap


def build_arrow_icon(
    color: QColor,
    *,
    pointing_left: bool = False,
    pixel_size: int = 18,
    stroke_width: float = 1.8,
) -> QIcon:
    return QIcon(
        _draw_arrow_pixmap(
            color,
            pointing_left=pointing_left,
            pixel_size=pixel_size,
            stroke_width=stroke_width,
        )
    )


def _draw_home_pixmap(color: QColor, *, pixel_size: int, stroke_width: float) -> QPixmap:
    pixmap = QPixmap(pixel_size, pixel_size)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    pen = QPen(color, stroke_width)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)

    s = float(pixel_size)
    roof = QPainterPath()
    roof.moveTo(s * 0.16, s * 0.52)
    roof.lineTo(s * 0.50, s * 0.20)
    roof.lineTo(s * 0.84, s * 0.52)
    painter.drawPath(roof)

    body = QPainterPath()
    body.moveTo(s * 0.27, s * 0.47)
    body.lineTo(s * 0.27, s * 0.82)
    body.lineTo(s * 0.73, s * 0.82)
    body.lineTo(s * 0.73, s * 0.47)
    painter.drawPath(body)

    door = QPainterPath()
    door.moveTo(s * 0.44, s * 0.82)
    door.lineTo(s * 0.44, s * 0.63)
    door.lineTo(s * 0.56, s * 0.63)
    door.lineTo(s * 0.56, s * 0.82)
    painter.drawPath(door)

    painter.end()
    return pixmap


def build_home_icon(color: QColor, *, pixel_size: int = 18, stroke_width: float = 1.7) -> QIcon:
    return QIcon(_draw_home_pixmap(color, pixel_size=pixel_size, stroke_width=stroke_width))


def _draw_doc_category_pixmap(
    kind: str,
    color: QColor,
    *,
    pixel_size: int,
    stroke_width: float,
) -> QPixmap:
    pixmap = QPixmap(pixel_size, pixel_size)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    pen = QPen(color, stroke_width)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    s = float(pixel_size)

    def line(*points: tuple[float, float]) -> None:
        path = QPainterPath()
        path.moveTo(points[0][0] * s, points[0][1] * s)
        for px, py in points[1:]:
            path.lineTo(px * s, py * s)
        painter.drawPath(path)

    def poly(*points: tuple[float, float]) -> None:
        path = QPainterPath()
        path.moveTo(points[0][0] * s, points[0][1] * s)
        for px, py in points[1:]:
            path.lineTo(px * s, py * s)
        path.closeSubpath()
        painter.drawPath(path)

    def circle(cx: float, cy: float, r: float, *, fill: bool = False) -> None:
        painter.setBrush(color if fill else Qt.BrushStyle.NoBrush)
        painter.drawEllipse(QPointF(cx * s, cy * s), r * s, r * s)
        painter.setBrush(Qt.BrushStyle.NoBrush)

    if kind == "rocket":  # Getting Started
        path = QPainterPath()
        path.moveTo(0.50 * s, 0.15 * s)
        path.cubicTo(0.64 * s, 0.27 * s, 0.64 * s, 0.46 * s, 0.58 * s, 0.61 * s)
        path.lineTo(0.42 * s, 0.61 * s)
        path.cubicTo(0.36 * s, 0.46 * s, 0.36 * s, 0.27 * s, 0.50 * s, 0.15 * s)
        painter.drawPath(path)
        circle(0.50, 0.37, 0.06)
        line((0.42, 0.53), (0.31, 0.65), (0.42, 0.60))
        line((0.58, 0.53), (0.69, 0.65), (0.58, 0.60))
        line((0.45, 0.63), (0.45, 0.72))
        line((0.50, 0.63), (0.50, 0.79))
        line((0.55, 0.63), (0.55, 0.72))
    elif kind == "image":  # Reviewing & Sorting
        painter.drawRoundedRect(QRectF(0.17 * s, 0.25 * s, 0.66 * s, 0.50 * s), 0.06 * s, 0.06 * s)
        circle(0.33, 0.39, 0.05)
        line((0.20, 0.70), (0.39, 0.50), (0.51, 0.61), (0.63, 0.45), (0.80, 0.66))
    elif kind == "sparkle":  # AI Culling
        poly(
            (0.50, 0.13), (0.57, 0.43), (0.87, 0.50), (0.57, 0.57),
            (0.50, 0.87), (0.43, 0.57), (0.13, 0.50), (0.43, 0.43),
        )
    elif kind == "target":  # Adapter Training
        circle(0.50, 0.50, 0.33)
        circle(0.50, 0.50, 0.18)
        circle(0.50, 0.50, 0.05, fill=True)
    elif kind == "books":  # Library & Organization
        line((0.15, 0.82), (0.85, 0.82))
        painter.drawRect(QRectF(0.24 * s, 0.30 * s, 0.11 * s, 0.52 * s))
        painter.drawRect(QRectF(0.39 * s, 0.24 * s, 0.11 * s, 0.58 * s))
        poly((0.66, 0.82), (0.72, 0.38), (0.81, 0.40), (0.75, 0.82))
    elif kind == "box":  # Export & Handoff
        painter.drawRect(QRectF(0.22 * s, 0.34 * s, 0.56 * s, 0.46 * s))
        line((0.22, 0.47), (0.78, 0.47))
        line((0.50, 0.34), (0.50, 0.47))
    elif kind == "sliders":  # Settings
        for y, knob in ((0.34, 0.40), (0.54, 0.62), (0.74, 0.33)):
            line((0.20, y), (0.80, y))
            circle(knob, y, 0.06, fill=True)
    elif kind == "book":  # Reference
        path = QPainterPath()
        path.moveTo(0.50 * s, 0.30 * s)
        path.cubicTo(0.40 * s, 0.24 * s, 0.28 * s, 0.25 * s, 0.18 * s, 0.32 * s)
        path.lineTo(0.18 * s, 0.72 * s)
        path.cubicTo(0.30 * s, 0.66 * s, 0.42 * s, 0.68 * s, 0.50 * s, 0.74 * s)
        painter.drawPath(path)
        path2 = QPainterPath()
        path2.moveTo(0.50 * s, 0.30 * s)
        path2.cubicTo(0.60 * s, 0.24 * s, 0.72 * s, 0.25 * s, 0.82 * s, 0.32 * s)
        path2.lineTo(0.82 * s, 0.72 * s)
        path2.cubicTo(0.70 * s, 0.66 * s, 0.58 * s, 0.68 * s, 0.50 * s, 0.74 * s)
        painter.drawPath(path2)
        line((0.50, 0.30), (0.50, 0.74))

    painter.end()
    return pixmap


def build_doc_category_icon(
    kind: str,
    color: QColor,
    *,
    pixel_size: int = 18,
    stroke_width: float = 1.5,
) -> QIcon:
    return QIcon(
        _draw_doc_category_pixmap(kind, color, pixel_size=pixel_size, stroke_width=stroke_width)
    )


def _draw_pin_pixmap(
    color: QColor,
    *,
    filled: bool,
    pixel_size: int,
    stroke_width: float,
) -> QPixmap:
    pixmap = QPixmap(pixel_size, pixel_size)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    pen = QPen(color, stroke_width)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(color if filled else Qt.BrushStyle.NoBrush)

    s = float(pixel_size)
    pin = QPainterPath()
    pin.moveTo(-0.22, -0.37)
    pin.lineTo(0.22, -0.37)
    pin.quadTo(0.27, -0.37, 0.27, -0.31)
    pin.lineTo(0.27, -0.26)
    pin.lineTo(0.12, -0.22)
    pin.lineTo(0.09, 0.03)
    pin.lineTo(0.30, 0.16)
    pin.lineTo(0.30, 0.24)
    pin.lineTo(-0.30, 0.24)
    pin.lineTo(-0.30, 0.16)
    pin.lineTo(-0.09, 0.03)
    pin.lineTo(-0.12, -0.22)
    pin.lineTo(-0.27, -0.26)
    pin.lineTo(-0.27, -0.31)
    pin.quadTo(-0.27, -0.37, -0.22, -0.37)
    pin.closeSubpath()
    transform = QTransform()
    transform.translate(s * 0.50, s * 0.50)
    transform.rotate(34)
    transform.scale(-s, s)
    painter.drawPath(transform.map(pin))

    needle = QPainterPath()
    needle.moveTo(0.0, 0.24)
    needle.lineTo(0.0, 0.48)
    painter.drawPath(transform.map(needle))
    painter.end()
    return pixmap


def build_pin_icon(
    outline_color: QColor,
    filled_color: QColor,
    *,
    pixel_size: int = 24,
    stroke_width: float = 1.9,
) -> QIcon:
    icon = QIcon()
    outline = _draw_pin_pixmap(outline_color, filled=False, pixel_size=pixel_size, stroke_width=stroke_width)
    filled = _draw_pin_pixmap(filled_color, filled=True, pixel_size=pixel_size, stroke_width=stroke_width)
    icon.addPixmap(outline, QIcon.Mode.Normal, QIcon.State.Off)
    icon.addPixmap(outline, QIcon.Mode.Active, QIcon.State.Off)
    icon.addPixmap(filled, QIcon.Mode.Normal, QIcon.State.On)
    icon.addPixmap(filled, QIcon.Mode.Active, QIcon.State.On)
    return icon
