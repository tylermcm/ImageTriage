"""The on-canvas crop rectangle.

While this overlay is armed the renderer is told to *bypass* the crop, so the
pane shows the whole straightened frame with the box drawn over it and the
outside dimmed. That is the only arrangement in which a handle can be dragged
back outward to recover content — and it keeps the frame size constant during
the drag, which matters because the pane resizes its label to the rendered
pixmap and the overlay tracks the label.
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget

from .canvas_overlay import CanvasOverlay

# Corner handles, then edge handles. Order matters: corners are hit-tested
# first so the shared pixels at a corner grab the corner, not the edge.
_CORNERS = ("tl", "tr", "br", "bl")
_EDGES = ("t", "r", "b", "l")

_HANDLE_HIT = 11.0
_HANDLE_ARM = 16.0
_HANDLE_THICKNESS = 3.0
_MIN_CROP = 16.0  # source pixels, so a crop can never collapse to nothing


class CropOverlay(CanvasOverlay):
    """Drag-to-crop with corner/edge handles and a rule-of-thirds grid."""

    # Live during a drag: {"crop": (l, t, r, b)} in source pixels.
    crop_changed = Signal(dict)
    # The drag ended — owners should persist.
    crop_committed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._active = False
        self._crop: tuple[float, float, float, float] | None = None
        self._aspect: float | None = None
        self._show_grid = True
        self._drag_mode: str | None = None
        self._drag_origin: QPointF | None = None
        self._drag_start_crop: tuple[float, float, float, float] | None = None

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
        if crop is not None:
            self._crop = tuple(float(v) for v in crop)  # type: ignore[assignment]
        elif source_size is not None:
            self._crop = (0.0, 0.0, float(source_size[0]), float(source_size[1]))
        else:
            self._crop = None
        self.setVisible(self._active)
        self.set_pass_through(not self._active)
        self.update()

    def crop_rect(self) -> tuple[int, int, int, int] | None:
        if self._crop is None:
            return None
        return tuple(int(round(v)) for v in self._crop)  # type: ignore[return-value]

    # -- painting -------------------------------------------------------------

    def _display_rect(self) -> QRectF | None:
        if self._crop is None or self._scales() is None:
            return None
        left, top, right, bottom = self._crop
        tl = self._to_display(left, top)
        br = self._to_display(right, bottom)
        return QRectF(tl, br).normalized()

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
        painter.end()

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
        return "move" if rect.contains(pos) else None

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
    }

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt override
        if not self._active or event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        mode = self._hit_test(event.position())
        if mode is None:
            super().mousePressEvent(event)
            return
        self._drag_mode = mode
        self._drag_origin = event.position()
        self._drag_start_crop = self._crop
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
        self._apply_drag(event.position())
        event.accept()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt override
        if self._drag_mode is not None and event.button() == Qt.MouseButton.LeftButton:
            self._drag_mode = None
            self._drag_origin = None
            self._drag_start_crop = None
            self.crop_committed.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _apply_drag(self, pos: QPointF) -> None:
        if self._drag_start_crop is None or self._drag_origin is None or self._source_size is None:
            return
        start_source = self._to_source(self._drag_origin)
        now_source = self._to_source(pos)
        dx = now_source[0] - start_source[0]
        dy = now_source[1] - start_source[1]
        left, top, right, bottom = self._drag_start_crop
        width, height = float(self._source_size[0]), float(self._source_size[1])

        if self._drag_mode == "move":
            dx = max(-left, min(width - right, dx))
            dy = max(-top, min(height - bottom, dy))
            left, right = left + dx, right + dx
            top, bottom = top + dy, bottom + dy
        else:
            mode = self._drag_mode or ""
            if "l" in mode:
                left = min(max(0.0, left + dx), right - _MIN_CROP)
            if "r" in mode:
                right = max(min(width, right + dx), left + _MIN_CROP)
            if "t" in mode:
                top = min(max(0.0, top + dy), bottom - _MIN_CROP)
            if "b" in mode:
                bottom = max(min(height, bottom + dy), top + _MIN_CROP)
            if self._aspect:
                left, top, right, bottom = self._constrain_aspect(
                    (left, top, right, bottom), mode, width, height
                )

        self._crop = (left, top, right, bottom)
        self.update()
        self.crop_changed.emit({"crop": tuple(int(round(v)) for v in self._crop)})

    def _constrain_aspect(
        self,
        crop: tuple[float, float, float, float],
        mode: str,
        width: float,
        height: float,
    ) -> tuple[float, float, float, float]:
        """Force ``crop`` to the locked ratio, growing from the anchored edge."""
        left, top, right, bottom = crop
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
        current_w = min(current_w, width)
        current_h = min(current_h, height)
        # The edge the user is not dragging stays put.
        if "l" in mode:
            left = right - current_w
        else:
            right = left + current_w
        if "t" in mode:
            top = bottom - current_h
        else:
            bottom = top + current_h
        # Nudge back inside the frame rather than clipping the ratio.
        if left < 0:
            right, left = right - left, 0.0
        if top < 0:
            bottom, top = bottom - top, 0.0
        if right > width:
            left, right = left - (right - width), width
        if bottom > height:
            top, bottom = top - (bottom - height), height
        return left, top, right, bottom
