"""The hollow color ring that marks a collection (sidebar rows, menus, the search box chip, swatches)."""
from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QApplication

# Swatches offered in the New / Edit Collection window; More... opens a full color picker for anything else.
COLLECTION_COLORS: tuple[tuple[str, str], ...] = (
    ("Blue", "#4c8dff"),
    ("Green", "#3ccf8e"),
    ("Amber", "#e0b45a"),
    ("Red", "#ff6b7d"),
    ("Purple", "#c58af9"),
    ("Teal", "#38c6d9"),
)


def normalized_color(value: str) -> str:
    """"#rrggbb" for a valid color, "" for none or anything unreadable."""
    color = QColor(str(value or "").strip())
    return color.name() if color.isValid() else ""


def collection_ring_pixmap(color: str, size: int = 14, *, muted: QColor | None = None) -> QPixmap:
    """A hollow ring in ``color``; with no color, a faint dashed ring so names stay aligned."""
    app = QApplication.instance()
    ratio = float(app.devicePixelRatio()) if app is not None and hasattr(app, "devicePixelRatio") else 1.0
    pixmap = QPixmap(int(size * ratio), int(size * ratio))
    pixmap.setDevicePixelRatio(ratio)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    resolved = normalized_color(color)
    width = max(1.6, size * 0.16)
    if resolved:
        pen = QPen(QColor(resolved), width)
    else:
        faint = QColor(muted) if muted is not None else QColor(138, 144, 154)
        faint.setAlpha(140)
        pen = QPen(faint, max(1.0, width * 0.7), Qt.PenStyle.DashLine)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    inset = width / 2 + 1
    painter.drawEllipse(QRectF(inset, inset, size - 2 * inset, size - 2 * inset))
    painter.end()
    return pixmap


def collection_ring_icon(color: str, size: int = 14) -> QIcon:
    return QIcon(collection_ring_pixmap(color, size))
