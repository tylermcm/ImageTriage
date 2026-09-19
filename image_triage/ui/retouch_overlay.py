"""On-canvas heal / clone / red-eye spots.

Spots are parametric, not baked: each one is a dict on the recipe carrying its
own radius, feather and strength, so it stays movable and deletable for the
life of the edit. This overlay draws them and edits their geometry; the panel
owns the list and the render backend applies them.
"""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget

from .canvas_overlay import CanvasOverlay

_HANDLE_HIT = 10.0


class RetouchOverlay(CanvasOverlay):
    """Place, select, move and delete retouch spots."""

    spot_added = Signal(dict)        # full spot dict, source coordinates
    spot_moved = Signal(str, dict)   # id, changed fields
    spot_removed = Signal(str)
    spot_committed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._active = False
        self._spots: list[dict] = []
        self._tool: str | None = None
        self._selected_id: str | None = None
        self._brush = {"size": 40.0, "feather": 50.0, "strength": 80.0}
        self._cursor_pos: QPointF | None = None
        self._drag_id: str | None = None
        self._drag_part: str | None = None
        self._drag_origin: QPointF | None = None
        self._drag_start: dict | None = None

    # -- state ----------------------------------------------------------------

    def set_state(
        self,
        *,
        interactive: bool,
        spots: list[dict],
        tool: str | None,
        selected_id: str | None,
        brush: dict,
        source_size: tuple[int, int] | None,
    ) -> None:
        self._active = bool(interactive and source_size is not None)
        self._spots = list(spots or [])
        self._tool = tool
        self._selected_id = selected_id
        self._brush = dict(brush or self._brush)
        self._source_size = source_size
        self.setVisible(self._active or bool(self._spots))
        # Spots stay drawn when another tool is armed (so you can see what has
        # been removed) but only take the mouse on their own pages.
        self.set_pass_through(not self._active)
        self.update()

    # -- painting -------------------------------------------------------------

    def _radius_display(self, source_radius: float) -> float:
        scales = self._scales()
        if scales is None:
            return 0.0
        # Uniform display scale (the preview preserves aspect), so either axis
        # gives the same answer; averaging avoids a lopsided ring if it does not.
        return source_radius * (scales[0] + scales[1]) / 2.0

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt override
        if self._scales() is None:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        for spot in self._spots:
            self._paint_spot(painter, spot)
        if self._active and self._tool and self._cursor_pos is not None:
            radius = self._radius_display(self._brush.get("size", 40.0))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(255, 255, 255, 150), 1.0, Qt.PenStyle.DashLine))
            painter.drawEllipse(self._cursor_pos, radius, radius)
        painter.end()

    def _paint_spot(self, painter: QPainter, spot: dict) -> None:
        centre = self._to_display(float(spot.get("x", 0.0)), float(spot.get("y", 0.0)))
        radius = self._radius_display(float(spot.get("r", 0.0)))
        selected = str(spot.get("id")) == self._selected_id
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(0, 0, 0, 130), 3.0))
        painter.drawEllipse(centre, radius, radius)
        colour = QColor("#7fc4ff") if selected else QColor(255, 255, 255, 220)
        painter.setPen(QPen(colour, 1.6))
        painter.drawEllipse(centre, radius, radius)
        if spot.get("kind") == "clone" and spot.get("sx") is not None:
            source = self._to_display(float(spot["sx"]), float(spot["sy"]))
            painter.setPen(QPen(colour, 1.2, Qt.PenStyle.DashLine))
            painter.drawEllipse(source, radius, radius)
            painter.drawLine(source, centre)

    # -- interaction ----------------------------------------------------------

    def _spot_at(self, pos: QPointF) -> tuple[str, str] | None:
        """(spot id, part) under ``pos`` — nearest first, so overlapping spots
        pick the one whose edge you actually clicked."""
        best: tuple[float, str, str] | None = None
        for spot in self._spots:
            centre = self._to_display(float(spot.get("x", 0.0)), float(spot.get("y", 0.0)))
            radius = self._radius_display(float(spot.get("r", 0.0)))
            distance = math.hypot(pos.x() - centre.x(), pos.y() - centre.y())
            if abs(distance - radius) <= _HANDLE_HIT:
                part = "resize"
            elif distance <= radius:
                part = "move"
            else:
                if spot.get("kind") == "clone" and spot.get("sx") is not None:
                    source = self._to_display(float(spot["sx"]), float(spot["sy"]))
                    if math.hypot(pos.x() - source.x(), pos.y() - source.y()) <= radius:
                        part = "source"
                    else:
                        continue
                else:
                    continue
            if best is None or distance < best[0]:
                best = (distance, str(spot.get("id")), part)
        return (best[1], best[2]) if best else None

    def _spot_by_id(self, spot_id: str | None) -> dict | None:
        for spot in self._spots:
            if str(spot.get("id")) == spot_id:
                return spot
        return None

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt override
        if not self._active:
            super().mousePressEvent(event)
            return
        pos = event.position()
        if event.button() == Qt.MouseButton.RightButton:
            hit = self._spot_at(pos)
            if hit is not None:
                self.spot_removed.emit(hit[0])
                event.accept()
                return
            super().mousePressEvent(event)
            return
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return

        hit = self._spot_at(pos)
        if hit is not None:
            spot_id, part = hit
            self._drag_id = spot_id
            self._drag_part = part
            self._drag_origin = pos
            start = self._spot_by_id(spot_id)
            self._drag_start = dict(start) if start else None
            self._selected_id = spot_id
            self.update()
            event.accept()
            return

        if self._tool is None:
            super().mousePressEvent(event)
            return
        x, y = self._to_source(pos)
        spot = {
            "kind": self._tool,
            "x": round(float(x), 2),
            "y": round(float(y), 2),
            "r": float(self._brush.get("size", 40.0)),
            "feather": float(self._brush.get("feather", 50.0)),
            "strength": float(self._brush.get("strength", 80.0)) / 100.0,
        }
        if self._tool == "clone":
            # Seed the source above and left; the user drags it where they want.
            offset = float(self._brush.get("size", 40.0)) * 1.6
            spot["sx"] = round(float(x) - offset, 2)
            spot["sy"] = round(float(y) - offset, 2)
        self.spot_added.emit(spot)
        event.accept()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt override
        self._cursor_pos = event.position()
        if not self._active:
            super().mouseMoveEvent(event)
            return
        if self._drag_id is None:
            self.update()
            super().mouseMoveEvent(event)
            return
        self._apply_drag(event.position())
        event.accept()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt override
        if self._drag_id is not None and event.button() == Qt.MouseButton.LeftButton:
            self._drag_id = None
            self._drag_part = None
            self._drag_origin = None
            self._drag_start = None
            self.spot_committed.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802 - Qt override
        self._cursor_pos = None
        self.update()
        super().leaveEvent(event)

    def _apply_drag(self, pos: QPointF) -> None:
        if self._drag_start is None or self._drag_origin is None:
            return
        start_x, start_y = self._to_source(self._drag_origin)
        now_x, now_y = self._to_source(pos)
        dx, dy = now_x - start_x, now_y - start_y
        start = self._drag_start
        if self._drag_part == "move":
            changes = {
                "x": round(float(start.get("x", 0.0)) + dx, 2),
                "y": round(float(start.get("y", 0.0)) + dy, 2),
            }
            # A clone's source travels with its target, as in Photoshop.
            if start.get("kind") == "clone" and start.get("sx") is not None:
                changes["sx"] = round(float(start["sx"]) + dx, 2)
                changes["sy"] = round(float(start["sy"]) + dy, 2)
        elif self._drag_part == "source":
            changes = {
                "sx": round(float(start.get("sx", 0.0)) + dx, 2),
                "sy": round(float(start.get("sy", 0.0)) + dy, 2),
            }
        else:  # resize
            centre = self._to_display(float(start.get("x", 0.0)), float(start.get("y", 0.0)))
            distance = math.hypot(pos.x() - centre.x(), pos.y() - centre.y())
            scales = self._scales()
            scale = (scales[0] + scales[1]) / 2.0 if scales else 1.0
            changes = {"r": max(2.0, round(distance / max(scale, 1e-6), 2))}
        local = self._spot_by_id(self._drag_id)
        if local is not None:
            local.update(changes)
        self.update()
        self.spot_moved.emit(self._drag_id, changes)
