"""Shared presentation primitives extracted from the UI prototype.

These are the reusable, behaviour-free pieces of the generated prototype that
the real application window adopts during the prototype-to-app migration: the
exact colour tokens the design was tuned around, and the custom-drawn folder
icon. Keeping them in one module avoids duplicating the design between the
standalone prototype (`generated_prototype.py`) and the live `MainWindow`.
"""

from __future__ import annotations

from PySide6.QtCore import QFileInfo, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPalette, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import QFileIconProvider, QTreeView


# --- Prototype colour tokens -------------------------------------------------
# The hex values the prototype layout was approved against. The real window's
# Dark/Midnight palettes are tuned toward these during the migration.
PROTO_RAIL_BG = "#0d0d0d"          # left vertical button rail
PROTO_DIRECTORY_BG = "#161516"     # directory / folder panel
PROTO_FOLDER_CARD_BG = "#151515"   # folder pane card
PROTO_REVIEW_CARD_BG = "#111111"   # review / AI activity card
PROTO_VIEWPORT_BG = "#070707"      # image viewport background
PROTO_RATING_FOOTER_BG = "#141313"  # metadata strip under each thumbnail
PROTO_RIGHT_CARD_BG = "#151515"    # right inspector cards
PROTO_TOPBAR_BG = "#141415"        # top bar
PROTO_BUTTON_BG = "#20201f"        # top-bar button background
PROTO_BUTTON_HOVER = "#313130"     # top-bar button hover
PROTO_RAIL_BUTTON_HOVER = "#181818"  # rail button hover
PROTO_SETTINGS_BAR_BG = "#161615"  # bottom settings bar
PROTO_DIVIDER = "#242527"          # connected-pane definition lines
PROTO_CARD_RADIUS = 10

PROTO_FOLDER_COLOR = "#d3b15b"     # flat folder icon gold
PROTO_DRIVE_COLOR = "#8f9bb0"      # flat drive icon steel
PROTO_DRIVE_LED_COLOR = "#5ad17e"  # drive activity LED accent


def folder_icon_pixmap(size: int = 16, color: str = PROTO_FOLDER_COLOR) -> QPixmap:
    """A plain, flat single-tone folder icon with the classic angled tab.

    Rendered at 2x and tagged with a device pixel ratio so it stays crisp.
    """
    scale = 2
    s = size * scale
    pixmap = QPixmap(s, s)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    folder = QColor(color)
    path = QPainterPath()
    path.moveTo(s * 0.10, s * 0.80)
    path.lineTo(s * 0.10, s * 0.26)
    path.lineTo(s * 0.40, s * 0.26)
    path.lineTo(s * 0.49, s * 0.37)
    path.lineTo(s * 0.90, s * 0.37)
    path.lineTo(s * 0.90, s * 0.80)
    path.closeSubpath()
    pen = QPen(folder, s * 0.085)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    painter.setPen(pen)
    painter.setBrush(folder)
    painter.drawPath(path)
    painter.end()
    pixmap.setDevicePixelRatio(scale)
    return pixmap


class FolderTreeView(QTreeView):
    """Folder tree that draws clean right/down chevrons for expandable rows
    instead of the default branch connector and indentation guide lines.

    Only the branch (disclosure) painting is customized — item backgrounds,
    selection, and icons are still rendered normally via the active stylesheet.
    """

    def drawBranches(self, painter: QPainter, rect, index) -> None:  # type: ignore[override]
        model = self.model()
        if model is None or not model.hasChildren(index):
            # Leaf rows get no branch decoration at all (no guide lines).
            return
        indent = self.indentation() or 16
        size = 3.0
        cx = rect.right() - indent / 2.0 + 0.5
        cy = rect.center().y() + 0.5
        color = QColor("#8a909a")
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pen = QPen(color, 1.5)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        if self.isExpanded(index):
            points = QPolygonF(
                [
                    QPointF(cx - size, cy - size * 0.4),
                    QPointF(cx, cy + size * 0.6),
                    QPointF(cx + size, cy - size * 0.4),
                ]
            )
        else:
            points = QPolygonF(
                [
                    QPointF(cx - size * 0.4, cy - size),
                    QPointF(cx + size * 0.6, cy),
                    QPointF(cx - size * 0.4, cy + size),
                ]
            )
        painter.drawPolyline(points)
        painter.restore()


def drive_icon_pixmap(
    size: int = 16,
    color: str = PROTO_DRIVE_COLOR,
    led_color: str = PROTO_DRIVE_LED_COLOR,
) -> QPixmap:
    """A flat, single-tone external-drive icon with a small activity LED.

    Deliberately a different silhouette and colour from the folder icon so
    drive roots read as distinct from ordinary directories in the tree.
    """
    scale = 2
    s = size * scale
    pixmap = QPixmap(s, s)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    body = QColor(color)
    # Drive body: a landscape rounded rectangle.
    rect = QRectF(s * 0.12, s * 0.34, s * 0.76, s * 0.34)
    path = QPainterPath()
    path.addRoundedRect(rect, s * 0.07, s * 0.07)
    painter.fillPath(path, body)
    # A subtle separator slot near the top, carved darker for depth.
    slot = QColor(0, 0, 0, 60)
    slot_pen = QPen(slot, s * 0.03)
    painter.setPen(slot_pen)
    painter.drawLine(QPointF(s * 0.22, s * 0.43), QPointF(s * 0.78, s * 0.43))
    # Activity LED on the right side.
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(led_color))
    painter.drawEllipse(QPointF(s * 0.74, s * 0.58), s * 0.035, s * 0.035)
    painter.end()
    pixmap.setDevicePixelRatio(scale)
    return pixmap


class PrototypeFileIconProvider(QFileIconProvider):
    """Supplies the flat prototype folder icon for directories in tree views."""

    def __init__(self, size: int = 18) -> None:
        super().__init__()
        self._folder_icon = QIcon(folder_icon_pixmap(size))
        self._drive_icon = QIcon(drive_icon_pixmap(size))

    def icon(self, info) -> QIcon:  # type: ignore[override]
        if isinstance(info, QFileInfo):
            if info.isDir():
                if self._is_drive(info):
                    return self._drive_icon
                return self._folder_icon
            return super().icon(info)
        if info == QFileIconProvider.IconType.Drive:
            return self._drive_icon
        if info == QFileIconProvider.IconType.Folder:
            return self._folder_icon
        return super().icon(info)

    @staticmethod
    def _is_drive(info: QFileInfo) -> bool:
        """True for drive/filesystem roots (e.g. ``C:\\`` or a UNC share root)."""
        if info.isRoot():
            return True
        path = info.absoluteFilePath()
        # Normalise so ``C:`` and ``C:/`` both register as drive roots.
        stripped = path.rstrip("/\\")
        if len(stripped) == 2 and stripped[1] == ":":
            return True
        return False
