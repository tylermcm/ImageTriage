"""On-canvas mask creation and editing for the studio preview.

MaskOverlay is a transparent widget stretched over a preview pane's image
label. It paints the selected mask's strength as a translucent red field —
alpha at each pixel is proportional to the mask's real strength there, using
the same linear falloff as photo_terminal.masks (full red inside the core,
fading to transparent through the feathered region) — plus Lightroom-style
edit handles. Dragging on the image creates or reshapes masks; all geometry
is emitted in source-image pixel coordinates, matching the session's
``space-source-full`` coordinate space.
"""
from __future__ import annotations

import math
from typing import Any

from PySide6.QtCore import QEvent, QObject, QPointF, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QImage,
    QPainterPath,
    QLinearGradient,
    QPainter,
    QPen,
    QRadialGradient,
)
from PySide6.QtWidgets import QWidget

OVERLAY_RED = QColor(255, 64, 64)
MAX_ALPHA = 128          # overlay opacity at full mask strength / 100 density
HANDLE_PX = 5.0          # visual half-size of resize handles
HIT_PX = 10.0            # hit-test tolerance in display pixels
MIN_RADIUS_SRC = 4.0     # smallest radius (source px) a drag can produce
ROT_KNOB_GAP = 24.0      # display px from the top handle to the rotation knob
STRENGTH_CACHE_EDGE = 2048


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _paint_component_gray(
    mask_type: str,
    params: dict[str, Any],
    width: int,
    height: int,
    sx: float,
    sy: float,
    level_scale: float,
) -> QImage | None:
    """One component's strength as an opaque gray RGB32 image (white = full
    effect × level_scale, black = none), mirroring photo_terminal's linear
    falloff. RGB32 so union via CompositionMode_Lighten is an exact max."""
    density = _clamp(float(params.get("density", 100.0)) / 100.0, 0.0, 1.0)
    feather = _clamp(float(params.get("feather", 65.0)) / 100.0, 0.0, 1.0)
    invert = bool(params.get("invert", False))
    level = int(round(255 * density * level_scale))
    full = QColor(level, level, level)
    none = QColor(0, 0, 0)
    if invert:
        full, none = none, full

    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(none)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    if mask_type == "radial":
        core = _clamp(1.0 - feather, 0.0, 0.999)
        gradient = QRadialGradient(QPointF(0.0, 0.0), 1.0)
        gradient.setColorAt(0.0, full)
        gradient.setColorAt(core, full)
        gradient.setColorAt(1.0, none)
        # world = Translate(center) · Rotate(angle) · Scale(radii): a unit
        # circle scaled to the ellipse then rotated about its center.
        painter.translate(float(params.get("cx", 0)) * sx, float(params.get("cy", 0)) * sy)
        angle = float(params.get("angle", 0.0))
        if angle:
            painter.rotate(angle)
        painter.scale(max(1.0, float(params.get("rx", 1)) * sx), max(1.0, float(params.get("ry", 1)) * sy))
        painter.setBrush(gradient)
        painter.drawEllipse(QPointF(0.0, 0.0), 1.0, 1.0)
    elif mask_type == "linear-gradient":
        start = QPointF(float(params.get("x1", 0)) * sx, float(params.get("y1", 0)) * sy)
        end = QPointF(float(params.get("x2", 0)) * sx, float(params.get("y2", 0)) * sy)
        if (end - start).manhattanLength() < 1.0:
            painter.fillRect(0, 0, width, height, full)
        else:
            full_until = _clamp(1.0 - max(0.01, feather), 0.0, 0.999)
            gradient = QLinearGradient(start, end)
            gradient.setColorAt(0.0, full)
            gradient.setColorAt(full_until, full)
            gradient.setColorAt(1.0, none)
            painter.fillRect(0, 0, width, height, gradient)
    elif mask_type == "bitmap":
        painter.end()
        path = str(params.get("assetPath") or params.get("path") or "")
        if not path:
            return None
        bitmap = QImage(path)
        if bitmap.isNull():
            return None
        bitmap = bitmap.convertToFormat(QImage.Format.Format_Grayscale8)
        if bitmap.width() != width or bitmap.height() != height:
            bitmap = bitmap.scaled(width, height, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
        if level_scale != 1.0 or density < 1.0 or invert:
            adjusted = QImage(width, height, QImage.Format.Format_Grayscale8)
            for y in range(height):
                src = bitmap.constScanLine(y)
                dst = adjusted.scanLine(y)
                for x in range(width):
                    value = int(src[x]) * density * level_scale
                    if invert:
                        value = 255 * density * level_scale - value
                    dst[x] = max(0, min(255, int(round(value))))
            bitmap = adjusted
        return bitmap.convertToFormat(QImage.Format.Format_RGB32)
    else:
        painter.end()
        return None
    painter.end()
    return image


def _component_parts(component: Any) -> tuple[str, dict[str, Any], str]:
    """Accept (type, params) or (type, params, combine); default combine=add."""
    if len(component) >= 3:
        return str(component[0]), dict(component[1]), str(component[2] or "add")
    return str(component[0]), dict(component[1]), "add"


def build_group_strength(
    components: list[Any],
    width: int,
    height: int,
    source_size: tuple[int, int],
    level_scale: float = 1.0,
) -> QImage | None:
    """Combine the group's component strength fields into one opaque gray
    RGB32 image. ``add`` components union in (per-pixel max, matching
    photo_terminal's 'add'); ``subtract`` components carve out (multiplicative,
    feather-respecting: dst · (1 − strength)). The first component is always an
    add base (the group root)."""
    if width < 1 or height < 1 or source_size[0] < 1 or source_size[1] < 1:
        return None
    sx = width / source_size[0]
    sy = height / source_size[1]
    accum: QImage | None = None
    for component in components:
        mask_type, params, combine = _component_parts(component)
        if accum is None:
            if combine == "subtract":
                continue  # nothing to carve from yet
            accum = _paint_component_gray(mask_type, params, width, height, sx, sy, level_scale)
            continue
        if combine == "subtract":
            # Full-strength layer regardless of level_scale so the carve fully
            # removes coverage; invert + Multiply => dst · (1 − strength).
            layer = _paint_component_gray(mask_type, params, width, height, sx, sy, 1.0)
            if layer is None:
                continue
            layer.invertPixels()
            mode = QPainter.CompositionMode.CompositionMode_Multiply
        else:
            layer = _paint_component_gray(mask_type, params, width, height, sx, sy, level_scale)
            if layer is None:
                continue
            mode = QPainter.CompositionMode.CompositionMode_Lighten
        painter = QPainter(accum)
        painter.setCompositionMode(mode)
        painter.drawImage(0, 0, layer)
        painter.end()
    return accum


def mask_strength_qimage(
    components: list[tuple[str, dict[str, Any]]],
    width: int,
    height: int,
    source_size: tuple[int, int],
) -> QImage | None:
    """Grayscale8 union strength field for live masked-adjustment previews.
    White = full effect, black = none. Painted with Qt gradients, so it is
    fast enough to rebuild per slider tick."""
    gray = build_group_strength(components, width, height, source_size)
    if gray is None:
        return None
    return gray.convertToFormat(QImage.Format.Format_Grayscale8)


class MaskOverlay(QWidget):
    """Interactive overlay for one shape mask (radial or linear-gradient)."""

    # A drag on empty canvas finished while a create tool was armed.
    mask_created = Signal(str, dict)   # mask type, params (source coords)
    # The selected mask's params changed during an edit drag (live, in-memory).
    mask_edited = Signal(dict)
    # A selected bitmap mask was painted on-canvas.
    bitmap_edited = Signal(object)
    # A click on the source image in an armed sampling mode.
    source_clicked = Signal(float, float)
    # An edit drag ended; owners should persist the pending changes.
    edit_committed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setMouseTracking(True)
        self._mask_type: str | None = None
        self._params: dict[str, Any] | None = None
        self._components: list[tuple[str, dict[str, Any], str]] = []
        self._selected_index: int | None = None
        self._source_size: tuple[int, int] | None = None
        self._create_mode: str | None = None
        self._create_combine: str = "add"
        self._brush_mode: str | None = None
        self._brush_size = 25
        self._brush_flow = 100
        self._bitmap_image: QImage | None = None
        self._interactive = False
        self._show_overlay = True
        self._drag: dict[str, Any] | None = None
        self._strength_cache: QImage | None = None
        self._strength_cache_key: tuple | None = None
        self._watched: QWidget | None = None
        self._set_pass_through(True)

    # -- attachment ---------------------------------------------------------
    def attach_to(self, label: QWidget) -> None:
        """Parent the overlay to ``label`` (a pane's image label) and track
        its size so the overlay always covers the displayed pixmap."""
        if self._watched is label:
            return
        if self._watched is not None:
            self._watched.removeEventFilter(self)
        self._watched = label
        self.setParent(label)
        label.installEventFilter(self)
        self.setGeometry(label.rect())
        self.show()
        self.raise_()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if watched is self._watched and event.type() in (QEvent.Type.Resize, QEvent.Type.Show):
            self.setGeometry(self._watched.rect())
        return False

    # -- state --------------------------------------------------------------
    def set_state(
        self,
        *,
        interactive: bool,
        show_overlay: bool,
        create_mode: str | None,
        mask_type: str | None,
        params: dict[str, Any] | None,
        source_size: tuple[int, int] | None,
        components: list[Any] | None = None,
        selected_index: int | None = None,
        create_combine: str = "add",
        brush_mode: str | None = None,
        brush_size: int = 25,
        brush_flow: int = 100,
    ) -> None:
        """``mask_type``/``params`` describe the selected component (handles,
        hit-testing); ``components`` is the whole mask group whose union the
        red overlay shows, with ``selected_index`` marking the selected
        component's slot so live drags replace it. ``create_combine`` is the
        combine mode a drag-in-progress new shape should preview with. Each
        component is (type, params[, combine]); combine defaults to add."""
        self._interactive = bool(interactive)
        self._show_overlay = bool(show_overlay)
        self._create_mode = create_mode
        self._create_combine = create_combine or "add"
        self._brush_mode = brush_mode if brush_mode in ("add", "subtract") else None
        self._brush_size = max(1, int(brush_size))
        self._brush_flow = max(0, min(100, int(brush_flow)))
        self._mask_type = mask_type
        self._params = dict(params) if params else None
        self._bitmap_image = None
        if self._mask_type == "bitmap" and self._params:
            path = str(self._params.get("assetPath") or self._params.get("path") or "")
            if path:
                image = QImage(path)
                if not image.isNull():
                    self._bitmap_image = image.convertToFormat(QImage.Format.Format_Grayscale8)
        if components is not None:
            self._components = [_component_parts(component) for component in components]
            self._selected_index = selected_index
        elif mask_type is not None and params:
            self._components = [(mask_type, dict(params), "add")]
            self._selected_index = 0
        else:
            self._components = []
            self._selected_index = None
        self._source_size = source_size
        accepts_mouse = self._interactive and source_size is not None and (
            self._create_mode is not None
            or self._params is not None
            or (self._mask_type == "bitmap" and self._brush_mode is not None)
        )
        self._set_pass_through(not accepts_mouse)
        if not accepts_mouse:
            self._drag = None
        self.update()

    def _set_pass_through(self, on: bool) -> None:
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, on)
        if on:
            self.unsetCursor()

    # -- coordinate mapping ---------------------------------------------------
    def _scales(self) -> tuple[float, float] | None:
        if self._source_size is None or self.width() < 2 or self.height() < 2:
            return None
        sw, sh = self._source_size
        if sw < 1 or sh < 1:
            return None
        return self.width() / sw, self.height() / sh

    def _to_display(self, x: float, y: float) -> QPointF:
        scales = self._scales()
        if scales is None:
            return QPointF(0, 0)
        return QPointF(x * scales[0], y * scales[1])

    def _to_source(self, pos: QPointF) -> tuple[float, float]:
        scales = self._scales()
        if scales is None:
            return 0.0, 0.0
        return pos.x() / scales[0], pos.y() / scales[1]

    # -- painting -------------------------------------------------------------
    def _effective_components(self) -> list[tuple[str, dict[str, Any]]]:
        """Group components with the selected component's live (possibly
        mid-drag) params substituted in; an in-progress create is appended."""
        components = [(t, dict(p), c) for t, p, c in self._components]
        if self._mask_type == "bitmap" and self._bitmap_image is not None and self._params is not None:
            # During a brush stroke, write a small temp-less in-memory view by
            # leaving the component path as-is for cached draws. The live red
            # overlay catches up after bitmap_edited persists and set_state is
            # called again; painting feedback is shown by the brush cursor.
            pass
        if self._params is not None and self._mask_type is not None:
            if self._drag is not None and self._drag.get("mode") == "create":
                components.append((self._mask_type, dict(self._params), self._create_combine))
            elif self._selected_index is not None and 0 <= self._selected_index < len(components):
                combine = components[self._selected_index][2]
                components[self._selected_index] = (self._mask_type, dict(self._params), combine)
            elif not components:
                components = [(self._mask_type, dict(self._params), "add")]
        return components

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt override
        if self._scales() is None:
            return
        if self._params is None and not self._components:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self._show_overlay:
            image = self._strength_image()
            if image is not None:
                painter.drawImage(self.rect(), image)
        if self._interactive and self._params is not None:
            if self._mask_type == "radial":
                self._paint_radial_handles(painter)
            elif self._mask_type == "linear-gradient":
                self._paint_linear_handles(painter)
            elif self._mask_type == "bitmap" and self._brush_mode is not None:
                self._paint_brush_cursor(painter)
        painter.end()

    def _strength_image(self) -> QImage | None:
        """Cached translucent red union of the group's strength fields at
        capped resolution. Gray union (exact per-pixel max, photo_terminal
        falloff math) becomes the red overlay's alpha channel."""
        components = self._effective_components()
        if not components or self._source_size is None:
            return None

        def freeze(params: dict[str, Any]) -> tuple:
            return tuple(
                sorted(
                    (k, round(float(v), 3) if isinstance(v, (int, float)) else str(v))
                    for k, v in params.items()
                )
            )

        key = (
            tuple((t, freeze(p), c) for t, p, c in components),
            self.width(),
            self.height(),
            self._source_size,
        )
        if key == self._strength_cache_key and self._strength_cache is not None:
            return self._strength_cache

        scale_down = min(1.0, STRENGTH_CACHE_EDGE / max(self.width(), self.height()))
        cw = max(1, int(round(self.width() * scale_down)))
        ch = max(1, int(round(self.height() * scale_down)))
        gray = build_group_strength(
            components, cw, ch, self._source_size, level_scale=MAX_ALPHA / 255.0
        )
        if gray is None:
            return None
        image = QImage(cw, ch, QImage.Format.Format_ARGB32)
        image.fill(OVERLAY_RED)
        image.setAlphaChannel(gray)

        self._strength_cache = image
        self._strength_cache_key = key
        return image

    def _handle_pen(self) -> tuple[QPen, QPen]:
        halo = QPen(QColor(0, 0, 0, 140), 3.0)
        line = QPen(QColor(255, 255, 255, 235), 1.4)
        return halo, line

    def _paint_radial_handles(self, painter: QPainter) -> None:
        parts = self._radial_display_parts()
        if parts is None:
            return
        cx, cy = parts["cx"], parts["cy"]
        rx, ry = parts["rx"], parts["ry"]
        core = parts["core"]
        center = QPointF(cx, cy)
        halo, line = self._handle_pen()
        dashed = QPen(line)
        dashed.setStyle(Qt.PenStyle.DashLine)
        # Draw the ring(s) in a rotated frame; pen width stays constant because
        # we pass the radii to drawEllipse rather than scaling the coordinates.
        painter.save()
        painter.translate(cx, cy)
        painter.rotate(parts["angle_deg"])
        painter.setBrush(Qt.BrushStyle.NoBrush)
        origin = QPointF(0.0, 0.0)
        for pen in (halo, line):
            painter.setPen(pen)
            painter.drawEllipse(origin, rx, ry)
        if 0.02 < core < 0.999:
            painter.setPen(dashed)
            painter.drawEllipse(origin, rx * core, ry * core)
        painter.restore()
        # Line from the top handle to the rotation knob.
        top = parts["handles"][3]
        knob = parts["knob"]
        for pen in (halo, line):
            painter.setPen(pen)
            painter.drawLine(QPointF(*top), QPointF(*knob))
        painter.setPen(halo)
        painter.setBrush(QColor(255, 255, 255, 235))
        painter.drawEllipse(center, 3.5, 3.5)
        painter.drawEllipse(QPointF(*knob), 4.5, 4.5)
        for hx, hy in parts["handles"]:
            painter.drawRect(int(hx - HANDLE_PX), int(hy - HANDLE_PX), int(HANDLE_PX * 2), int(HANDLE_PX * 2))

    def _paint_linear_handles(self, painter: QPainter) -> None:
        params = self._params or {}
        start = self._to_display(float(params.get("x1", 0)), float(params.get("y1", 0)))
        end = self._to_display(float(params.get("x2", 0)), float(params.get("y2", 0)))
        halo, line = self._handle_pen()
        for pen in (halo, line):
            painter.setPen(pen)
            painter.drawLine(start, end)
        direction = end - start
        length = math.hypot(direction.x(), direction.y())
        if length > 1.0:
            # Perpendicular guides at the full-strength edge and the zero edge.
            normal = QPointF(-direction.y() / length, direction.x() / length)
            feather = _clamp(float(params.get("feather", 100.0)) / 100.0, 0.01, 1.0)
            full_until = 1.0 - feather
            guide_half = 4000.0
            for t, style in ((full_until, Qt.PenStyle.SolidLine), (1.0, Qt.PenStyle.DashLine)):
                anchor = QPointF(start.x() + direction.x() * t, start.y() + direction.y() * t)
                pen = QPen(line)
                pen.setStyle(style)
                for p in (halo, pen):
                    painter.setPen(p)
                    painter.drawLine(
                        QPointF(anchor.x() - normal.x() * guide_half, anchor.y() - normal.y() * guide_half),
                        QPointF(anchor.x() + normal.x() * guide_half, anchor.y() + normal.y() * guide_half),
                    )
        painter.setPen(halo)
        painter.setBrush(QColor(255, 255, 255, 235))
        painter.drawEllipse(start, 4.0, 4.0)
        painter.setBrush(QColor(20, 20, 20, 200))
        painter.drawEllipse(end, 4.0, 4.0)

    def _paint_brush_cursor(self, painter: QPainter) -> None:
        if self._drag is None or "pos" not in self._drag:
            return
        pos = self._drag["pos"]
        radius = max(2.0, self._brush_size / 2.0)
        halo, line = self._handle_pen()
        for pen in (halo, line):
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(pos, radius, radius)

    # -- geometry helpers -----------------------------------------------------
    def _radial_display_parts(self) -> dict[str, Any] | None:
        """Display-space geometry for the selected radial mask, rotation baked
        in: center, radii, feather core, the four axis handles (rotated) and
        the rotation knob beyond the top handle."""
        params = self._params or {}
        scales = self._scales()
        if scales is None:
            return None
        sx, sy = scales
        cx = float(params.get("cx", 0)) * sx
        cy = float(params.get("cy", 0)) * sy
        rx = max(1.0, float(params.get("rx", 1)) * sx)
        ry = max(1.0, float(params.get("ry", 1)) * sy)
        angle_deg = float(params.get("angle", 0.0))
        rad = math.radians(angle_deg)
        cos_a, sin_a = math.cos(rad), math.sin(rad)

        def rot(ux: float, uy: float) -> tuple[float, float]:
            return (cx + ux * cos_a - uy * sin_a, cy + ux * sin_a + uy * cos_a)

        handles = (rot(rx, 0.0), rot(-rx, 0.0), rot(0.0, ry), rot(0.0, -ry))
        top = handles[3]
        dxn, dyn = top[0] - cx, top[1] - cy
        length = math.hypot(dxn, dyn) or 1.0
        knob = (top[0] + dxn / length * ROT_KNOB_GAP, top[1] + dyn / length * ROT_KNOB_GAP)
        feather = _clamp(float(params.get("feather", 65.0)) / 100.0, 0.0, 1.0)
        return {
            "cx": cx, "cy": cy, "rx": rx, "ry": ry,
            "cos": cos_a, "sin": sin_a, "angle_deg": angle_deg,
            "handles": handles, "knob": knob, "core": 1.0 - feather,
        }

    # -- hit testing ----------------------------------------------------------
    def _hit_test(self, pos: QPointF) -> str | None:
        if self._params is None:
            return None
        if self._mask_type == "radial":
            return self._hit_test_radial(pos)
        if self._mask_type == "linear-gradient":
            return self._hit_test_linear(pos)
        return None

    def _hit_test_radial(self, pos: QPointF) -> str | None:
        parts = self._radial_display_parts()
        if parts is None:
            return None
        cx, cy = parts["cx"], parts["cy"]
        rx, ry = parts["rx"], parts["ry"]
        cos_a, sin_a, core = parts["cos"], parts["sin"], parts["core"]
        if math.hypot(pos.x() - parts["knob"][0], pos.y() - parts["knob"][1]) <= HIT_PX:
            return "rotate"
        if math.hypot(pos.x() - cx, pos.y() - cy) <= HIT_PX:
            return "move"
        for index, (hx, hy) in enumerate(parts["handles"]):
            if math.hypot(pos.x() - hx, pos.y() - hy) <= HIT_PX:
                return "resize-x" if index < 2 else "resize-y"
        # Distance in the mask's own (unrotated) normalized frame.
        dx, dy = pos.x() - cx, pos.y() - cy
        nx = (dx * cos_a + dy * sin_a) / rx
        ny = (-dx * sin_a + dy * cos_a) / ry
        distance = math.hypot(nx, ny)
        ring_px = min(rx, ry)  # approximate display distance to the ellipse rings
        if abs(distance - 1.0) * ring_px <= HIT_PX:
            return "scale"
        if 0.02 < core < 0.999 and abs(distance - core) * ring_px <= HIT_PX:
            return "feather"
        if distance < 1.0:
            return "move"
        return None

    def _hit_test_linear(self, pos: QPointF) -> str | None:
        params = self._params or {}
        start = self._to_display(float(params.get("x1", 0)), float(params.get("y1", 0)))
        end = self._to_display(float(params.get("x2", 0)), float(params.get("y2", 0)))
        if math.hypot(pos.x() - start.x(), pos.y() - start.y()) <= HIT_PX:
            return "move-start"
        if math.hypot(pos.x() - end.x(), pos.y() - end.y()) <= HIT_PX:
            return "move-end"
        direction = end - start
        length_sq = direction.x() ** 2 + direction.y() ** 2
        if length_sq > 1.0:
            t = _clamp(((pos.x() - start.x()) * direction.x() + (pos.y() - start.y()) * direction.y()) / length_sq, 0.0, 1.0)
            px = start.x() + direction.x() * t
            py = start.y() + direction.y() * t
            if math.hypot(pos.x() - px, pos.y() - py) <= HIT_PX:
                return "move"
        return None

    # -- mouse interaction ----------------------------------------------------
    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt override
        if event.button() != Qt.MouseButton.LeftButton or self._scales() is None:
            event.ignore()
            return
        pos = QPointF(event.position())
        mode = self._hit_test(pos) if self._params is not None else None
        # With a create tool armed, dragging the mask body starts a new mask;
        # only explicit handles (rings, squares, pins) still edit.
        if mode == "move" and self._create_mode is not None:
            mode = None
        if self._mask_type == "bitmap" and self._brush_mode is not None and self._bitmap_image is not None:
            self._drag = {"mode": "brush", "pos": pos}
            self._paint_bitmap_at(pos)
            event.accept()
            return
        if mode is not None:
            self._drag = {
                "mode": mode,
                "start": pos,
                "start_src": self._to_source(pos),
                "params": dict(self._params or {}),
            }
            event.accept()
            return
        if self._create_mode is not None:
            src_x, src_y = self._to_source(pos)
            if self._create_mode == "color-range":
                self.source_clicked.emit(src_x, src_y)
                event.accept()
                return
            self._drag = {"mode": "create", "start": pos, "start_src": (src_x, src_y)}
            if self._create_mode == "radial":
                self._mask_type = "radial"
                self._params = {
                    "cx": src_x, "cy": src_y,
                    "rx": MIN_RADIUS_SRC, "ry": MIN_RADIUS_SRC,
                    "angle": 0.0,
                    "feather": 50.0, "density": 100.0, "invert": False,
                }
            else:
                self._mask_type = "linear-gradient"
                self._params = {
                    "x1": src_x, "y1": src_y, "x2": src_x, "y2": src_y,
                    "feather": 100.0, "density": 100.0, "invert": False,
                }
            self.update()
            event.accept()
            return
        event.ignore()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt override
        pos = QPointF(event.position())
        if self._drag is None:
            if self._interactive:
                self._update_hover_cursor(pos)
            event.ignore()
            return
        self._apply_drag(pos)
        if self._drag is not None:
            self._drag["pos"] = pos
        self.update()
        if self._drag["mode"] != "create" and self._params is not None:
            self.mask_edited.emit(dict(self._params))
        event.accept()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt override
        if event.button() != Qt.MouseButton.LeftButton or self._drag is None:
            event.ignore()
            return
        drag = self._drag
        self._drag = None
        self._apply_drag(QPointF(event.position()))
        self.update()
        if drag["mode"] == "create" and self._params is not None:
            self.mask_created.emit(self._mask_type or "radial", dict(self._params))
        elif drag["mode"] == "brush" and self._bitmap_image is not None:
            self.bitmap_edited.emit(self._bitmap_image)
        elif self._params is not None:
            self.mask_edited.emit(dict(self._params))
            self.edit_committed.emit()
        event.accept()

    def _apply_drag(self, pos: QPointF) -> None:
        if self._drag is None or self._params is None:
            return
        mode = self._drag["mode"]
        if mode == "brush":
            self._paint_bitmap_at(pos)
            return
        src_x, src_y = self._to_source(pos)
        if mode == "create":
            ox, oy = self._drag["start_src"]
            if self._mask_type == "radial":
                self._params["rx"] = max(MIN_RADIUS_SRC, abs(src_x - ox))
                self._params["ry"] = max(MIN_RADIUS_SRC, abs(src_y - oy))
            else:
                self._params["x2"] = src_x
                self._params["y2"] = src_y
            return
        orig = self._drag["params"]
        ox, oy = self._drag["start_src"]
        dx = src_x - ox
        dy = src_y - oy
        if mode == "move":
            if self._mask_type == "radial":
                self._params["cx"] = float(orig.get("cx", 0)) + dx
                self._params["cy"] = float(orig.get("cy", 0)) + dy
            else:
                self._params["x1"] = float(orig.get("x1", 0)) + dx
                self._params["y1"] = float(orig.get("y1", 0)) + dy
                self._params["x2"] = float(orig.get("x2", 0)) + dx
                self._params["y2"] = float(orig.get("y2", 0)) + dy
        elif mode == "move-start":
            self._params["x1"] = float(orig.get("x1", 0)) + dx
            self._params["y1"] = float(orig.get("y1", 0)) + dy
        elif mode == "move-end":
            self._params["x2"] = float(orig.get("x2", 0)) + dx
            self._params["y2"] = float(orig.get("y2", 0)) + dy
        elif mode in ("resize-x", "resize-y", "scale", "feather"):
            cx = float(orig.get("cx", 0))
            cy = float(orig.get("cy", 0))
            rx = max(1e-6, float(orig.get("rx", 1)))
            ry = max(1e-6, float(orig.get("ry", 1)))
            rad = math.radians(float(orig.get("angle", 0.0)))
            cos_a, sin_a = math.cos(rad), math.sin(rad)
            # Project the cursor into the mask's own axes so handles track the
            # rotated ellipse rather than the screen axes.
            ldx = (src_x - cx) * cos_a + (src_y - cy) * sin_a
            ldy = -(src_x - cx) * sin_a + (src_y - cy) * cos_a
            if mode == "resize-x":
                self._params["rx"] = max(MIN_RADIUS_SRC, abs(ldx))
            elif mode == "resize-y":
                self._params["ry"] = max(MIN_RADIUS_SRC, abs(ldy))
            else:
                distance = math.hypot(ldx / rx, ldy / ry)
                if mode == "scale":
                    factor = max(0.05, distance)
                    self._params["rx"] = max(MIN_RADIUS_SRC, rx * factor)
                    self._params["ry"] = max(MIN_RADIUS_SRC, ry * factor)
                else:
                    self._params["feather"] = round(_clamp((1.0 - distance) * 100.0, 0.0, 100.0), 1)
        elif mode == "rotate":
            scales = self._scales()
            if scales is not None:
                sx, sy = scales
                cxd = float(self._params.get("cx", 0)) * sx
                cyd = float(self._params.get("cy", 0)) * sy
                start = self._drag["start"]
                a0 = math.atan2(start.y() - cyd, start.x() - cxd)
                a1 = math.atan2(pos.y() - cyd, pos.x() - cxd)
                self._params["angle"] = float(orig.get("angle", 0.0)) + math.degrees(a1 - a0)

    def _update_hover_cursor(self, pos: QPointF) -> None:
        mode = self._hit_test(pos)
        if mode is None:
            if self._create_mode is not None:
                self.setCursor(Qt.CursorShape.CrossCursor)
            else:
                self.unsetCursor()
            return
        cursors = {
            "move": Qt.CursorShape.SizeAllCursor,
            "move-start": Qt.CursorShape.SizeAllCursor,
            "move-end": Qt.CursorShape.SizeAllCursor,
            "resize-x": Qt.CursorShape.SizeHorCursor,
            "resize-y": Qt.CursorShape.SizeVerCursor,
            "scale": Qt.CursorShape.SizeFDiagCursor,
            "feather": Qt.CursorShape.PointingHandCursor,
            "rotate": Qt.CursorShape.CrossCursor,
        }
        self.setCursor(cursors.get(mode, Qt.CursorShape.ArrowCursor))

    def _paint_bitmap_at(self, pos: QPointF) -> None:
        if self._bitmap_image is None or self._source_size is None:
            return
        sx, sy = self._to_source(pos)
        radius = max(1.0, self._brush_size / 2.0)
        value = int(round(255 * (self._brush_flow / 100.0)))
        if self._brush_mode == "subtract":
            value = -value
        painter = QPainter(self._bitmap_image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        path = QPainterPath()
        path.addEllipse(QPointF(sx, sy), radius, radius)
        if value >= 0:
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
            painter.fillPath(path, QColor(value, value, value))
        else:
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
            painter.fillPath(path, QColor(0, 0, 0, min(255, abs(value))))
        painter.end()
