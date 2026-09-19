"""The window backdrop: a flat base with two soft elliptical glows.

Painted by the main window behind its see-through chrome, and by views that
paint their own background (the photo grid) so they show the same glow at the
same place instead of a flat colour.
"""
from __future__ import annotations

from PySide6.QtCore import QPoint, QPointF, QRect, QSize, Qt
from PySide6.QtGui import QColor, QPainter, QRadialGradient

from .theme import ThemePalette


def theme_has_backdrop(theme: ThemePalette | None) -> bool:
    return theme is not None and theme.backdrop_glow_primary is not None


def paint_backdrop(painter: QPainter, theme: ThemePalette, window_size: QSize, origin: QPoint, rect: QRect) -> None:
    """Fill ``rect`` (in the painter's coordinates) with the backdrop as seen
    from a widget whose top-left sits at ``origin`` in window coordinates."""
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setClipRect(rect)
    painter.fillRect(rect, theme.window_bg.qcolor())
    width, height = window_size.width(), window_size.height()
    # Violet off the top left, teal off the bottom right; each fades out at
    # 60% of its radii.
    glows = (
        (theme.backdrop_glow_primary, 0.20 * width, -0.10 * height, 0.70 * width, 0.70 * height),
        (theme.backdrop_glow_secondary, 1.10 * width, 1.10 * height, 0.52 * width, 0.60 * height),
    )
    painter.setPen(Qt.PenStyle.NoPen)
    for color, cx, cy, rx, ry in glows:
        if color is None or rx <= 0 or ry <= 0:
            continue
        painter.save()
        painter.translate(cx - origin.x(), cy - origin.y())
        painter.scale(rx, ry)
        gradient = QRadialGradient(QPointF(0.0, 0.0), 1.0)
        inner = color.qcolor()
        outer = QColor(inner)
        outer.setAlpha(0)
        gradient.setColorAt(0.0, inner)
        gradient.setColorAt(0.6, outer)
        gradient.setColorAt(1.0, outer)
        painter.setBrush(gradient)
        painter.drawEllipse(QPointF(0.0, 0.0), 1.0, 1.0)
        painter.restore()
    painter.restore()
