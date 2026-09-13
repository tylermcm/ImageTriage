"""The on-canvas crop rectangle.

While this overlay is armed the renderer is told to *bypass* the crop, so the
pane shows the whole straightened frame with the box drawn over it and the
outside dimmed. That is the only arrangement in which a handle can be dragged
back outward to recover content — and it keeps the frame size constant during
the drag, which matters because the pane resizes its label to the rendered
pixmap and the overlay tracks the label.

The box is axis-aligned in *frame* space — what the viewer sees — not in source
space. Under a straighten those differ: the source rectangle becomes a rotated
quad inside the frame, with blank wedges in the corners. Working in frame space
keeps the box the shape the user drew (its two opposite corners used to be
mapped separately, which stretched it as the angle turned) and lets "inside the
photo" mean that quad rather than the frame.
"""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QWidget

from ..editor_geometry import fit_rect_in_quad, inset_quad, limit_rect_to_quad
from .canvas_overlay import CanvasOverlay

# Corner handles, then edge handles. Order matters: corners are hit-tested
# first so the shared pixels at a corner grab the corner, not the edge.
_CORNERS = ("tl", "tr", "br", "bl")
_EDGES = ("t", "r", "b", "l")

_HANDLE_HIT = 11.0
_HANDLE_ARM = 16.0
_HANDLE_THICKNESS = 3.0
_MIN_CROP = 16.0  # frame pixels, so a crop can never collapse to nothing
_MAX_STRAIGHTEN = 45.0  # past this, the quarter-turn buttons are the tool


class CropOverlay(CanvasOverlay):
    """Drag-to-crop with corner/edge handles, a rule-of-thirds grid, and
    drag-outside-the-box to rotate freely."""

    # Live during a drag: {"crop": (l, t, r, b)} in stored (source) pixels.
    crop_changed = Signal(dict)
    # The drag ended — owners should persist.
    crop_committed = Signal()
    # Free rotation: the new straighten angle, in degrees.
    angle_changed = Signal(float)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._active = False
        # The box in frame pixels; the stored (source) form is derived on the
        # way out, so nothing downstream has to know about this distinction.
        self._rect: tuple[float, float, float, float] | None = None
        self._aspect: float | None = None
        self._show_grid = True
        self._drag_mode: str | None = None
        self._drag_origin: QPointF | None = None
        self._drag_start_rect: tuple[float, float, float, float] | None = None
        self._drag_start_angle = 0.0
        self._drag_start_bearing = 0.0
        self._live_angle: float | None = None

    # -- state ----------------------------------------------------------------

    def set_state(
        self,
        *,
        interactive: bool,
        crop: tuple[int, int, int, int] | None,
        source_size: tuple[int, int] | None,
        aspect: float | None = None,
        show_grid: bool = True,
    ) -> None:
        self._active = bool(interactive and source_size is not None)
        self._source_size = source_size
        self._aspect = aspect
        self._show_grid = show_grid
        # A sync mid-drag (the panel re-emits as the recipe changes) must not
        # yank the box out from under the pointer.
        if self._drag_mode is None:
            self._rect = self._frame_rect_for(crop)
        self.setVisible(self._active)
        self.set_pass_through(not self._active)
        self.update()

    def _frame_rect_for(
        self, crop: tuple[int, int, int, int] | None
    ) -> tuple[float, float, float, float] | None:
        view = self._effective_view()
        if view is None:
            return None
        if crop is None:
            frame_w, frame_h = view.frame_size()
            # No crop drawn yet: the whole photo, pulled in off the blank
            # corners a straighten leaves behind.
            quad = self._quad() or view.image_quad()
            return fit_rect_in_quad(quad, (0.0, 0.0, float(frame_w), float(frame_h)))
        return view.frame_rect_for_crop(tuple(float(v) for v in crop))

    def crop_rect(self) -> tuple[int, int, int, int] | None:
        stored = self._stored_crop()
        if stored is None:
            return None
        return tuple(int(round(v)) for v in stored)  # type: ignore[return-value]

    def _stored_crop(self) -> tuple[float, float, float, float] | None:
        view = self._effective_view()
        if view is None or self._rect is None:
            return None
        return view.crop_for_frame_rect(self._rect)

    def _quad(self):
        view = self._effective_view()
        if view is None:
            return None
        quad = view.image_quad()
        # A hair of slack once straightened: the box is reported as whole
        # pixels, and rounding outward from an exact edge fit would put it
        # back off the photo. Square-on there is nothing to avoid.
        return inset_quad(quad) if view.angle else quad

    # -- painting -------------------------------------------------------------

    def _display_rect(self) -> QRectF | None:
        scales = self._scales()
        if self._rect is None or scales is None:
            return None
        left, top, right, bottom = self._rect
        return QRectF(
            QPointF(left * scales[0], top * scales[1]),
            QPointF(right * scales[0], bottom * scales[1]),
        ).normalized()

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt override
        rect = self._display_rect()
        if not self._active or rect is None:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Dim everything outside the box so the crop reads at a glance.
        shade = QColor(0, 0, 0, 132)
        full = QRectF(self.rect())
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(shade)
        painter.drawRect(QRectF(full.left(), full.top(), full.width(), rect.top() - full.top()))
        painter.drawRect(QRectF(full.left(), rect.bottom(), full.width(), full.bottom() - rect.bottom()))
        painter.drawRect(QRectF(full.left(), rect.top(), rect.left() - full.left(), rect.height()))
        painter.drawRect(QRectF(rect.right(), rect.top(), full.right() - rect.right(), rect.height()))

        painter.setBrush(Qt.BrushStyle.NoBrush)
        if self._show_grid:
            painter.setPen(QPen(QColor(255, 255, 255, 90), 1.0))
            for step in (1, 2):
                x = rect.left() + rect.width() * step / 3.0
                y = rect.top() + rect.height() * step / 3.0
                painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
                painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))

        painter.setPen(QPen(QColor(255, 255, 255, 210), 1.2))
        painter.drawRect(rect)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(255, 255, 255, 235))
        arm = _HANDLE_ARM
        thick = _HANDLE_THICKNESS
        for name in _CORNERS:
            point = self._handle_point(rect, name)
            hx = -arm if name in ("tr", "br") else 0.0
            hy = -thick if name in ("bl", "br") else 0.0
            painter.drawRect(QRectF(point.x() + hx, point.y() + hy, arm, thick))
            vx = -thick if name in ("tr", "br") else 0.0
            vy = -arm if name in ("bl", "br") else 0.0
            painter.drawRect(QRectF(point.x() + vx, point.y() + vy, thick, arm))
        for name in _EDGES:
            point = self._handle_point(rect, name)
            if name in ("t", "b"):
                painter.drawRect(QRectF(point.x() - arm / 2.0, point.y() - thick / 2.0, arm, thick))
            else:
                painter.drawRect(QRectF(point.x() - thick / 2.0, point.y() - arm / 2.0, thick, arm))

        if self._live_angle is not None:
            self._paint_angle_readout(painter, rect, self._live_angle)
        painter.end()

    @staticmethod
    def _paint_angle_readout(painter: QPainter, rect: QRectF, angle: float) -> None:
        """While free-rotating, say what the angle is: the pointer is out on the
        canvas, nowhere near the Straighten slider."""
        text = f"{angle:+.1f}°"
        font = QFont(painter.font())
        font.setPixelSize(13)
        font.setBold(True)
        painter.setFont(font)
        metrics = painter.fontMetrics()
        width = metrics.horizontalAdvance(text) + 14
        height = metrics.height() + 8
        box = QRectF(
            rect.center().x() - width / 2.0, rect.center().y() - height / 2.0, width, height
        )
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 165))
        painter.drawRoundedRect(box, 4.0, 4.0)
        painter.setPen(QPen(QColor(255, 255, 255, 235)))
        painter.drawText(box, Qt.AlignmentFlag.AlignCenter, text)

    @staticmethod
    def _handle_point(rect: QRectF, name: str) -> QPointF:
        return {
            "tl": rect.topLeft(),
            "tr": rect.topRight(),
            "br": rect.bottomRight(),
            "bl": rect.bottomLeft(),
            "t": QPointF(rect.center().x(), rect.top()),
            "r": QPointF(rect.right(), rect.center().y()),
            "b": QPointF(rect.center().x(), rect.bottom()),
            "l": QPointF(rect.left(), rect.center().y()),
        }[name]

    # -- interaction ----------------------------------------------------------

    def _hit_test(self, pos: QPointF) -> str | None:
        rect = self._display_rect()
        if rect is None:
            return None
        for name in (*_CORNERS, *_EDGES):
            point = self._handle_point(rect, name)
            if (pos - point).manhattanLength() <= _HANDLE_HIT * 1.6:
                return name
        if rect.contains(pos):
            return "move"
        # Outside the box: free rotation, the way Photoshop turns a crop.
        return "rotate"

    _CURSORS = {
        "tl": Qt.CursorShape.SizeFDiagCursor,
        "br": Qt.CursorShape.SizeFDiagCursor,
        "tr": Qt.CursorShape.SizeBDiagCursor,
        "bl": Qt.CursorShape.SizeBDiagCursor,
        "t": Qt.CursorShape.SizeVerCursor,
        "b": Qt.CursorShape.SizeVerCursor,
        "l": Qt.CursorShape.SizeHorCursor,
        "r": Qt.CursorShape.SizeHorCursor,
        "move": Qt.CursorShape.SizeAllCursor,
        "rotate": Qt.CursorShape.CrossCursor,
    }

    def _bearing(self, pos: QPointF) -> float:
        """Pointer angle about the box centre, in degrees, clockwise on screen."""
        rect = self._display_rect()
        if rect is None:
            return 0.0
        return math.degrees(
            math.atan2(pos.y() - rect.center().y(), pos.x() - rect.center().x())
        )

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt override
        if not self._active or event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        mode = self._hit_test(event.position())
        view = self._effective_view()
        if mode is None or view is None:
            super().mousePressEvent(event)
            return
        self._drag_mode = mode
        self._drag_origin = event.position()
        self._drag_start_rect = self._rect
        self._drag_start_angle = float(view.angle)
        self._drag_start_bearing = self._bearing(event.position())
        if mode == "rotate":
            self._live_angle = self._drag_start_angle
        event.accept()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt override
        if not self._active:
            super().mouseMoveEvent(event)
            return
        if self._drag_mode is None:
            mode = self._hit_test(event.position())
            if mode is None:
                self.unsetCursor()
            else:
                self.setCursor(self._CURSORS[mode])
            super().mouseMoveEvent(event)
            return
        if self._drag_mode == "rotate":
            self._apply_rotate(event.position())
        else:
            self._apply_drag(event.position())
        event.accept()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt override
        if self._drag_mode is not None and event.button() == Qt.MouseButton.LeftButton:
            self._drag_mode = None
            self._drag_origin = None
            self._drag_start_rect = None
            self._live_angle = None
            self.crop_committed.emit()
            self.update()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _apply_rotate(self, pos: QPointF) -> None:
        """Turn the photo under a fixed box, following the pointer."""
        delta = self._bearing(pos) - self._drag_start_bearing
        # Shortest way round, so crossing the seam at ±180° does not spin.
        delta = (delta + 180.0) % 360.0 - 180.0
        angle = max(-_MAX_STRAIGHTEN, min(_MAX_STRAIGHTEN, self._drag_start_angle + delta))
        self._live_angle = angle
        self.update()
        self.angle_changed.emit(angle)

    def _apply_drag(self, pos: QPointF) -> None:
        scales = self._scales()
        quad = self._quad()
        if self._drag_start_rect is None or self._drag_origin is None or scales is None:
            return
        if quad is None:
            return
        dx = (pos.x() - self._drag_origin.x()) / scales[0]
        dy = (pos.y() - self._drag_origin.y()) / scales[1]
        left, top, right, bottom = self._drag_start_rect

        if self._drag_mode == "move":
            desired = (left + dx, top + dy, right + dx, bottom + dy)
        else:
            mode = self._drag_mode or ""
            if "l" in mode:
                left = min(left + dx, right - _MIN_CROP)
            if "r" in mode:
                right = max(right + dx, left + _MIN_CROP)
            if "t" in mode:
                top = min(top + dy, bottom - _MIN_CROP)
            if "b" in mode:
                bottom = max(bottom + dy, top + _MIN_CROP)
            desired = (left, top, right, bottom)
            if self._aspect:
                desired = self._constrain_aspect(desired, mode)

        # One containment rule for every mode: the box stays on the photo, not
        # merely inside the frame. The frame's corners are blank once the photo
        # is straightened, and dragging out into them cropped in black.
        self._rect = limit_rect_to_quad(quad, self._drag_start_rect, desired)
        self.update()
        stored = self._stored_crop()
        if stored is not None:
            self.crop_changed.emit({"crop": tuple(int(round(v)) for v in stored)})

    def _constrain_aspect(
        self, rect: tuple[float, float, float, float], mode: str
    ) -> tuple[float, float, float, float]:
        """Force ``rect`` to the locked ratio, growing from the anchored edge."""
        left, top, right, bottom = rect
        aspect = self._aspect or 1.0
        current_w = right - left
        current_h = bottom - top
        if mode in ("t", "b"):
            current_w = current_h * aspect
        elif mode in ("l", "r"):
            current_h = current_w / aspect
        elif current_w / max(current_h, 1e-6) > aspect:
            current_h = current_w / aspect
        else:
            current_w = current_h * aspect
        # The edge the user is not dragging stays put.
        if "l" in mode:
            left = right - current_w
        else:
            right = left + current_w
        if "t" in mode:
            top = bottom - current_h
        else:
            bottom = top + current_h
        return left, top, right, bottom
