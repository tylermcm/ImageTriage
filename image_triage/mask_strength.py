"""Renders a mask group's components into a grayscale strength field.

Extracted from the now-removed built-in manual editor's on-canvas mask overlay
(``ui/mask_overlay.py``) because ``editor_render.CpuEditorRenderBackend``
still needs it to composite masked local adjustments, including for headless
renders (exports/thumbnails/PocketDrop handoff) that have no live overlay.
"""
from __future__ import annotations

from typing import Any

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QImage, QLinearGradient, QPainter, QRadialGradient, QTransform

from .mask_refinement import refine_bitmap_qimage
from .perf import perf_logger


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _component_parts(component: Any) -> tuple[str, dict[str, Any], str]:
    """Accept (type, params) or (type, params, combine); default combine=add."""
    if len(component) >= 3:
        return str(component[0]), dict(component[1]), str(component[2] or "add")
    return str(component[0]), dict(component[1]), "add"


def _paint_component_gray(
    mask_type: str,
    params: dict[str, Any],
    width: int,
    height: int,
    sx: float,
    sy: float,
    level_scale: float,
    guide_image: QImage | None = None,
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
        live_bitmap = params.get("_liveBitmap")
        bitmap = (
            QImage(live_bitmap)
            if isinstance(live_bitmap, QImage) and not live_bitmap.isNull()
            else QImage(
                str(params.get("assetPath") or params.get("path") or "")
            )
        )
        if bitmap.isNull():
            return None
        bitmap = bitmap.convertToFormat(QImage.Format.Format_Grayscale8)
        if bitmap.width() != width or bitmap.height() != height:
            bitmap = bitmap.scaled(width, height, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
        try:
            refined_bitmap = refine_bitmap_qimage(
                bitmap,
                params,
                guide_image=guide_image,
                scale=(sx + sy) / 2.0,
            )
        except Exception as exc:
            logger = perf_logger()
            if logger.enabled:
                logger.log(
                    "mask.refinement.failed",
                    error_type=type(exc).__name__,
                    error=str(exc),
                )
        else:
            bitmap = refined_bitmap
        if bool(params.get("selectionCleared", False)):
            bitmap.fill(Qt.GlobalColor.black)
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


def build_group_strength(
    components: list[Any],
    width: int,
    height: int,
    source_size: tuple[int, int],
    level_scale: float = 1.0,
    guide_image: QImage | None = None,
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
    # The group root (first component) carries the whole mask's invert / clear.
    # These must act on the finished union, not per-component: inverting each
    # add layer and then max-unioning gives 1−min(A,B), never 1−max(A,B), which
    # is why per-component invert of a multi-part selection floods the frame.
    group_invert = False
    group_cleared = False
    for index, component in enumerate(components):
        mask_type, params, combine = _component_parts(component)
        if index == 0:
            group_invert = bool(params.get("invert", False))
            group_cleared = bool(params.get("selectionCleared", False))
            params = {**params, "invert": False, "selectionCleared": False}
        if accum is None:
            if combine == "subtract":
                continue  # nothing to carve from yet
            accum = _paint_component_gray(
                mask_type,
                params,
                width,
                height,
                sx,
                sy,
                level_scale,
                guide_image,
            )
            continue
        if combine == "subtract":
            # Full-strength layer regardless of level_scale so the carve fully
            # removes coverage; invert + Multiply => dst · (1 − strength).
            layer = _paint_component_gray(
                mask_type,
                params,
                width,
                height,
                sx,
                sy,
                1.0,
                guide_image,
            )
            if layer is None:
                continue
            layer.invertPixels()
            mode = QPainter.CompositionMode.CompositionMode_Multiply
        else:
            layer = _paint_component_gray(
                mask_type,
                params,
                width,
                height,
                sx,
                sy,
                level_scale,
                guide_image,
            )
            if layer is None:
                continue
            mode = QPainter.CompositionMode.CompositionMode_Lighten
        painter = QPainter(accum)
        painter.setCompositionMode(mode)
        painter.drawImage(0, 0, layer)
        painter.end()
    if accum is not None:
        # Apply the group-level clear / invert to the finished union, in that
        # order (clearing empties the selection, inverting then floods it) to
        # match the per-component ordering this replaces.
        if group_cleared:
            accum.fill(Qt.GlobalColor.black)
        if group_invert:
            accum.invertPixels()
    return accum


def mask_strength_qimage(
    components: list[tuple[str, dict[str, Any]]],
    width: int,
    height: int,
    source_size: tuple[int, int],
    guide_image: QImage | None = None,
    transform: QTransform | None = None,
    transform_source_size: tuple[int, int] | None = None,
) -> QImage | None:
    """Grayscale8 union strength field for masked adjustments.
    White = full effect, black = none.

    ``transform`` is the source->frame geometry (crop, straighten, flip). When
    it is given the field is rasterized in *source* space and then put through
    the identical affine the pixels took, so a mask cannot drift relative to
    the photo. When it is None the original scale-only path runs untouched.
    """
    if transform is None:
        gray = build_group_strength(
            components,
            width,
            height,
            source_size,
            guide_image=guide_image,
        )
        return gray.convertToFormat(QImage.Format.Format_Grayscale8) if gray else None

    raster_source_size = transform_source_size or source_size
    source_gray = build_group_strength(
        components,
        max(1, int(raster_source_size[0])),
        max(1, int(raster_source_size[1])),
        source_size,
        guide_image=guide_image,
    )
    if source_gray is None:
        return None
    warped = QImage(max(1, int(width)), max(1, int(height)), QImage.Format.Format_RGB32)
    warped.fill(Qt.GlobalColor.black)
    painter = QPainter(warped)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    painter.setWorldTransform(transform)
    painter.drawImage(0, 0, source_gray)
    painter.end()
    return warped.convertToFormat(QImage.Format.Format_Grayscale8)
