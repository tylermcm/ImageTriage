"""Standalone painter for main-viewport grid cards.

This module intentionally has no app-state dependencies. It receives a pixmap,
metadata, and state flags, then paints one card with QPainter. The prototype and
the eventual grid integration should share this renderer.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QPoint, QRect, QRectF, QSize, Qt
from PySide6.QtGui import (
    QColor,
    QBrush,
    QFont,
    QFontMetrics,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)


@dataclass(frozen=True, slots=True)
class GridCardData:
    filename: str = "DSC_7149.NEF"
    exif_text: str = "1/250s  \u00b7  f/5  \u00b7  ISO 200  \u00b7  35mm"
    meta_text: str = "54.4 MB  \u00b7  2025-08-16 11:15  \u00b7  Banff 8-25"
    duplicate_text: str = "Near Duplicate \u00b7 2/3"
    ai_text: str = "AI Pick \u00b7 99"
    position_text: str = "1 / 24"
    status_text: str = "Keeper"
    status_kind: str = "keeper"
    duplicate_visible: bool = True
    ai_visible: bool = True
    selected: bool = False
    favorite: bool = False
    rejected: bool = False
    hover_favorite: bool = False
    hover_reject: bool = False


@dataclass(frozen=True, slots=True)
class GridCardHitRects:
    favorite: QRect
    reject: QRect


def render_grid_card_pixmap(
    size: QSize,
    source_pixmap: QPixmap | None,
    data: GridCardData,
) -> QPixmap:
    """Render a single card into a transparent pixmap."""

    output = QPixmap(size)
    output.fill(Qt.GlobalColor.transparent)
    painter = QPainter(output)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    paint_grid_card(painter, QRect(QPoint(0, 0), size), source_pixmap, data)
    painter.end()
    return output


def paint_grid_card(
    painter: QPainter,
    rect: QRect,
    source_pixmap: QPixmap | None,
    data: GridCardData,
) -> GridCardHitRects:
    """Paint one main viewport card and return action hit rectangles."""

    if rect.width() <= 8 or rect.height() <= 8:
        return GridCardHitRects(QRect(), QRect())

    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

    scale = _scale_for(rect)
    radius = max(7.0, 8.0 * scale)
    outer = QRectF(rect).adjusted(0.5, 0.5, -0.5, -0.5)

    selected_color = QColor(80, 140, 255)
    border_color = selected_color if data.selected else QColor(44, 47, 52)
    border_width = 1.35 if data.selected else 1.0

    _fill_round_rect(painter, outer, radius, QColor(16, 17, 19))
    painter.setPen(QPen(border_color, border_width))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawRoundedRect(outer, radius, radius)

    pad = max(6, round(7 * scale))
    content_rect = QRect(
        rect.left() + pad,
        rect.top() + pad,
        max(1, rect.width() - pad * 2),
        max(1, rect.height() - pad * 2),
    )
    image_radius = max(5.0, 6.0 * scale)

    _paint_image(painter, content_rect, image_radius, source_pixmap)
    _paint_scrim(painter, rect, content_rect, image_radius, scale)

    _paint_badges(painter, rect, content_rect, data, scale)
    favorite_rect, reject_rect = _paint_bottom_overlay(painter, rect, content_rect, data, scale)

    painter.restore()
    return GridCardHitRects(favorite_rect, reject_rect)


def _scale_for(rect: QRect) -> float:
    return max(0.72, min(1.25, rect.width() / 560.0))


def grid_card_action_rects(rect: QRect) -> GridCardHitRects:
    """Favorite/reject hit rectangles for a card painted by paint_grid_card.

    Kept in sync with _paint_bottom_overlay (which uses the same internal
    helper) so grid hit-testing can ask for the rectangles without painting.
    """
    if rect.width() <= 8 or rect.height() <= 8:
        return GridCardHitRects(QRect(), QRect())
    scale = _scale_for(rect)
    pad = max(6, round(7 * scale))
    content_rect = QRect(
        rect.left() + pad,
        rect.top() + pad,
        max(1, rect.width() - pad * 2),
        max(1, rect.height() - pad * 2),
    )
    return _action_button_rects(rect, content_rect, scale)


def _action_button_rects(card_rect: QRect, image_rect: QRect, scale: float) -> GridCardHitRects:
    """Button geometry for the bottom overlay: anchored under the right-side
    position/status stack. Independent of card data so hit-testing and
    painting always agree."""
    margin = max(12, round(14 * scale))
    button = max(1, round(card_rect.height() * 0.092))
    gap = max(8, round(10 * scale))
    reject_rect = QRect(image_rect.right() - margin - button, 0, button, button)
    favorite_rect = QRect(reject_rect.left() - gap - button, 0, button, button)

    meta_font = QFont("Segoe UI", max(8, round(9 * scale)))
    status_font = QFont("Segoe UI", max(8, round(9 * scale)), QFont.Weight.DemiBold)
    position_height = QFontMetrics(meta_font).height()
    status_height = QFontMetrics(status_font).height()
    initial_button_bottom = card_rect.bottom() - round(card_rect.height() * 0.0475)
    right_stack_height = round(card_rect.height() * 0.172)
    side_top = initial_button_bottom - right_stack_height + 1
    side_rect_top = side_top - round(card_rect.height() * 0.009)
    status_top = side_top + position_height + max(2, round(3 * scale)) - round(card_rect.height() * 0.0114)
    right_text_gap = max(0, status_top - (side_rect_top + position_height - 1))
    action_top = (status_top + status_height - 1) + right_text_gap + 1 + round(card_rect.height() * 0.008)
    reject_rect.moveTop(action_top)
    favorite_rect.moveTop(action_top)
    return GridCardHitRects(favorite_rect, reject_rect)


def _fill_round_rect(painter: QPainter, rect: QRectF, radius: float, color: QColor) -> None:
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(color)
    painter.drawRoundedRect(rect, radius, radius)


def _rounded_path(rect: QRectF, radius: float) -> QPainterPath:
    path = QPainterPath()
    path.addRoundedRect(rect, radius, radius)
    return path


def _photo_rect_for(content_rect: QRect) -> QRect:
    photo_height = round(content_rect.width() * 2 / 3)
    photo_height = max(1, min(photo_height, content_rect.height()))
    return QRect(content_rect.left(), content_rect.top(), content_rect.width(), photo_height)


def _paint_image(
    painter: QPainter,
    content_rect: QRect,
    radius: float,
    source_pixmap: QPixmap | None,
) -> None:
    path = _rounded_path(QRectF(content_rect), radius)
    photo_rect = _photo_rect_for(content_rect)
    painter.save()
    painter.setClipPath(path)

    painter.fillRect(content_rect, QColor(8, 9, 11))
    if source_pixmap is None or source_pixmap.isNull():
        _paint_empty_image(painter, photo_rect)
    else:
        target_size = photo_rect.size()
        scaled_size = source_pixmap.size()
        aspect_mode = Qt.AspectRatioMode.KeepAspectRatioByExpanding
        if source_pixmap.height() > source_pixmap.width() * 1.16:
            aspect_mode = Qt.AspectRatioMode.KeepAspectRatio
        scaled_size.scale(target_size, aspect_mode)

        draw_rect = QRect(QPoint(0, 0), scaled_size)
        draw_rect.moveCenter(photo_rect.center())

        painter.fillRect(photo_rect, QColor(17, 18, 20))
        painter.save()
        painter.setClipRect(photo_rect)
        painter.drawPixmap(draw_rect, source_pixmap)
        painter.restore()

    painter.restore()

    painter.setPen(QPen(QColor(255, 255, 255, 18), 1))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawRoundedRect(QRectF(content_rect).adjusted(0.5, 0.5, -0.5, -0.5), radius, radius)


def _paint_empty_image(painter: QPainter, image_rect: QRect) -> None:
    top = QColor(57, 73, 88)
    mid = QColor(30, 41, 47)
    bottom = QColor(7, 12, 14)
    grad = QLinearGradient(image_rect.topLeft(), image_rect.bottomLeft())
    grad.setColorAt(0.0, top)
    grad.setColorAt(0.52, mid)
    grad.setColorAt(1.0, bottom)
    painter.fillRect(image_rect, QBrush(grad))

    painter.setPen(QPen(QColor(11, 18, 22), max(2, image_rect.height() // 80)))
    h = image_rect.height()
    w = image_rect.width()
    base = image_rect.top() + round(h * 0.62)
    points = [
        image_rect.left() - round(w * 0.05),
        image_rect.left() + round(w * 0.19),
        image_rect.left() + round(w * 0.31),
        image_rect.left() + round(w * 0.48),
        image_rect.left() + round(w * 0.62),
        image_rect.left() + round(w * 0.83),
        image_rect.right() + round(w * 0.05),
    ]
    heights = [
        round(h * 0.05),
        round(h * 0.29),
        round(h * 0.18),
        round(h * 0.35),
        round(h * 0.21),
        round(h * 0.38),
        round(h * 0.14),
    ]
    mountain = QPainterPath()
    mountain.moveTo(points[0], image_rect.bottom())
    for x, peak in zip(points, heights):
        mountain.lineTo(x, base - peak)
    mountain.lineTo(points[-1], image_rect.bottom())
    mountain.closeSubpath()
    painter.fillPath(mountain, QColor(9, 18, 18, 205))

    lake = QRect(image_rect.left(), base + round(h * 0.04), w, image_rect.bottom() - base)
    painter.fillRect(lake, QColor(16, 60, 66, 130))


def _metadata_text_top(card_rect: QRect, scale: float) -> int:
    """Y coordinate of the top of the text block (the filename line).

    Kept in sync with the text stack built in ``_paint_bottom_overlay`` so the
    scrim can be anchored to where the text actually sits rather than a fixed
    fraction of the card height.
    """
    name_font = QFont("Segoe UI", max(11, round(13 * scale)), QFont.Weight.DemiBold)
    exif_font = QFont("Segoe UI", max(8, round(9 * scale)), QFont.Weight.Normal)
    meta_font = QFont("Segoe UI", max(8, round(9 * scale)))
    name_h = QFontMetrics(name_font).height()
    exif_h = QFontMetrics(exif_font).height()
    meta_h = QFontMetrics(meta_font).height()

    text_stack_height = name_h + exif_h + meta_h
    text_block_height = max(text_stack_height, round(card_rect.height() * 0.1475))
    text_block_bottom = card_rect.bottom() - round(card_rect.height() * 0.08)
    # name_rect top == position_top in the overlay layout.
    return text_block_bottom - text_block_height + 1


def _paint_scrim(
    painter: QPainter,
    card_rect: QRect,
    image_rect: QRect,
    radius: float,
    scale: float,
) -> None:
    # Anchor the fade to the text block so the gradient reaches uniformly up to
    # the top of the filename line, then ramps to a solid base under the text.
    h = card_rect.height()
    text_top = _metadata_text_top(card_rect, scale)
    fade_top = max(image_rect.top(), text_top - round(0.03 * h))  # fully clear just above the text
    solid_top = text_top + round(0.14 * h)  # fully opaque by the meta line
    span = max(1, card_rect.bottom() - fade_top)
    ramp = max(1, solid_top - fade_top)

    grad = QLinearGradient(
        float(image_rect.left()), float(fade_top),
        float(image_rect.left()), float(card_rect.bottom()),
    )
    # Sample a single smoothstep curve at many evenly spaced stops so Qt never
    # has to bridge a large alpha jump (which is what produced the banding).
    steps = 28
    for i in range(steps + 1):
        t = i / steps
        x = min(1.0, (t * span) / ramp)
        ease = x * x * (3.0 - 2.0 * x)  # smoothstep: zero slope at both ends
        grad.setColorAt(t, QColor(6, 9, 12, round(255 * ease)))

    painter.save()
    painter.setClipPath(_rounded_path(QRectF(image_rect), radius))
    painter.fillRect(
        QRect(
            image_rect.left(),
            fade_top,
            image_rect.width(),
            image_rect.bottom() - fade_top + 1,
        ),
        QBrush(grad),
    )
    painter.restore()


def _paint_badges(painter: QPainter, card_rect: QRect, image_rect: QRect, data: GridCardData, scale: float) -> None:
    edge_inset = max(12, round(14 * scale))
    font_size = max(8, round(9 * scale))
    font = QFont("Segoe UI", font_size, QFont.Weight.DemiBold)
    badge_height = max(1, round(card_rect.height() * 0.069))
    duplicate_width = max(1, round(card_rect.width() * 0.261))
    ai_width = max(1, round(card_rect.width() * 0.161))
    vertical_shift = round(card_rect.height() * 0.0219)
    top = image_rect.top() + max(1, max(13, round(16 * scale)) - vertical_shift)

    if data.duplicate_visible and data.duplicate_text:
        _paint_badge(
            painter,
            QPoint(image_rect.left() + edge_inset, top),
            data.duplicate_text,
            font,
            QColor(31, 36, 41, 226),
            QColor(255, 197, 73),
            QColor(255, 255, 255, 72),
            icon="duplicate",
            align_right=False,
            scale=scale,
            fixed_size=QSize(duplicate_width, badge_height),
        )

    if data.ai_visible and data.ai_text:
        _paint_badge(
            painter,
            QPoint(image_rect.right() - edge_inset, top),
            data.ai_text,
            font,
            QColor(124, 88, 23, 222),
            QColor(255, 218, 92),
            QColor(255, 178, 37, 214),
            icon="spark",
            align_right=True,
            scale=scale,
            fixed_size=QSize(ai_width, badge_height),
        )


def _paint_badge(
    painter: QPainter,
    anchor: QPoint,
    text: str,
    font: QFont,
    fill: QColor,
    text_color: QColor,
    border: QColor,
    *,
    icon: str,
    align_right: bool,
    scale: float,
    fixed_size: QSize | None = None,
) -> QRect:
    painter.save()
    painter.setFont(font)
    metrics = QFontMetrics(font)
    h_pad = max(8, round(11 * scale))
    icon_width = max(10, round(13 * scale))
    icon_gap = max(6, round(7 * scale))
    width = metrics.horizontalAdvance(text) + h_pad * 2 + icon_width + icon_gap
    height = max(round(24 * scale), metrics.height() + max(7, round(8 * scale)))
    if fixed_size is not None:
        width = fixed_size.width()
        height = fixed_size.height()
        h_pad = min(h_pad, max(4, round(width * 0.055)))
        icon_width = min(icon_width, max(9, round(height * 0.42)))
        icon_gap = min(icon_gap, max(4, round(width * 0.04)))
    left = anchor.x() - width if align_right else anchor.x()
    rect = QRect(left, anchor.y(), width, height)
    radius = max(5.0, round(6 * scale))

    painter.setPen(QPen(border, 1))
    painter.setBrush(fill)
    painter.drawRoundedRect(QRectF(rect), radius, radius)

    icon_rect = QRect(
        rect.left() + h_pad,
        rect.top() + round((rect.height() - icon_width) / 2),
        icon_width,
        icon_width,
    )
    if icon == "duplicate":
        _paint_duplicate_icon(painter, icon_rect, text_color, scale)
    elif icon == "spark":
        _paint_spark_icon(painter, icon_rect, text_color)

    text_rect = rect.adjusted(h_pad + icon_width + icon_gap, 0, -h_pad, 0)
    painter.setPen(text_color)
    painter.drawText(
        text_rect,
        Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
        text,
    )
    painter.restore()
    return rect


def _paint_duplicate_icon(painter: QPainter, rect: QRect, color: QColor, scale: float) -> None:
    painter.save()
    painter.setPen(QPen(color, max(1.3, 1.6 * scale)))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    radius = max(3.0, 3.5 * scale)
    back = QRectF(rect).adjusted(1.0, 1.0, -5.0 * scale, -5.0 * scale)
    front = QRectF(rect).adjusted(5.0 * scale, 5.0 * scale, -1.0, -1.0)
    painter.drawRoundedRect(back, radius, radius)
    painter.drawRoundedRect(front, radius, radius)
    painter.restore()


def _paint_spark_icon(painter: QPainter, rect: QRect, color: QColor) -> None:
    painter.save()
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(color)
    center = rect.center()
    long_r = rect.height() * 0.43
    short_r = rect.height() * 0.18
    path = QPainterPath()
    path.moveTo(center.x(), center.y() - long_r)
    path.lineTo(center.x() + short_r, center.y() - short_r)
    path.lineTo(center.x() + long_r, center.y())
    path.lineTo(center.x() + short_r, center.y() + short_r)
    path.lineTo(center.x(), center.y() + long_r)
    path.lineTo(center.x() - short_r, center.y() + short_r)
    path.lineTo(center.x() - long_r, center.y())
    path.lineTo(center.x() - short_r, center.y() - short_r)
    path.closeSubpath()
    painter.drawPath(path)
    painter.restore()


def _paint_bottom_overlay(
    painter: QPainter,
    card_rect: QRect,
    image_rect: QRect,
    data: GridCardData,
    scale: float,
) -> tuple[QRect, QRect]:
    margin = max(12, round(14 * scale))

    hit_rects = _action_button_rects(card_rect, image_rect, scale)
    favorite_rect = QRect(hit_rects.favorite)
    reject_rect = QRect(hit_rects.reject)
    initial_button_bottom = card_rect.bottom() - round(card_rect.height() * 0.0475)

    body_left = image_rect.left() + margin

    name_font = QFont("Segoe UI", max(11, round(13 * scale)), QFont.Weight.DemiBold)
    exif_font = QFont("Segoe UI", max(8, round(9 * scale)), QFont.Weight.Normal)
    meta_font = QFont("Segoe UI", max(8, round(9 * scale)))
    status_font = QFont("Segoe UI", max(8, round(9 * scale)), QFont.Weight.DemiBold)

    name_metrics = QFontMetrics(name_font)
    exif_metrics = QFontMetrics(exif_font)
    meta_metrics = QFontMetrics(meta_font)
    status_metrics = QFontMetrics(status_font)

    status_text_width = max(
        QFontMetrics(meta_font).horizontalAdvance(data.position_text),
        status_metrics.horizontalAdvance(data.status_text),
        round(62 * scale),
    )
    side_width = max(round(76 * scale), status_text_width + round(8 * scale))
    side_right = image_rect.right() - margin
    side_left = side_right - side_width
    body_right = min(favorite_rect.left(), side_left) - max(12, round(17 * scale))
    body_width = max(48, body_right - body_left)

    status_height = status_metrics.height()
    position_height = meta_metrics.height()
    text_block_height = max(
        name_metrics.height() + exif_metrics.height() + meta_metrics.height(),
        round(card_rect.height() * 0.1475),
    )
    text_block_bottom = card_rect.bottom() - round(card_rect.height() * 0.08)
    position_top = text_block_bottom - text_block_height + 1

    text_stack_height = name_metrics.height() + exif_metrics.height() + meta_metrics.height()
    line_gap = max(max(5, round(6 * scale)), round((text_block_height - text_stack_height) / 2))
    name_rect = QRect(body_left, position_top, body_width, name_metrics.height())
    exif_rect = QRect(body_left, name_rect.bottom() + line_gap, body_width, exif_metrics.height())
    meta_rect = QRect(body_left, exif_rect.bottom() + line_gap, body_width, meta_metrics.height())

    painter.save()
    _draw_elided_text(
        painter,
        name_rect,
        data.filename,
        name_font,
        QColor(255, 255, 255),
    )
    _draw_elided_text(
        painter,
        exif_rect,
        data.exif_text,
        exif_font,
        QColor(204, 214, 226),
    )
    _draw_elided_text(
        painter,
        meta_rect,
        data.meta_text,
        meta_font,
        QColor(132, 147, 168),
    )

    right_stack_height = round(card_rect.height() * 0.172)
    side_top = initial_button_bottom - right_stack_height + 1
    side_rect = QRect(
        side_left,
        side_top - round(card_rect.height() * 0.009),
        side_width,
        position_height,
    )
    if data.position_text:
        _draw_elided_text(
            painter,
            side_rect,
            data.position_text,
            status_font,
            QColor(137, 207, 255),
            align=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
        )

    if data.status_text:
        status_color = _status_color(data.status_kind)
        status_rect = QRect(
            side_left,
            side_top + position_height + max(2, round(3 * scale)) - round(card_rect.height() * 0.0114),
            side_width,
            status_height,
        )
        _draw_elided_text(
            painter,
            status_rect,
            data.status_text,
            status_font,
            status_color,
            align=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
        )

    _paint_action_button(
        painter,
        favorite_rect,
        "heart",
        active=data.favorite,
        hover=data.hover_favorite,
        active_color=QColor(245, 95, 118),
    )
    _paint_action_button(
        painter,
        reject_rect,
        "reject",
        active=data.rejected,
        hover=data.hover_reject,
        active_color=QColor(255, 107, 107),
    )
    painter.restore()
    return favorite_rect, reject_rect


def _draw_elided_text(
    painter: QPainter,
    rect: QRect,
    text: str,
    font: QFont,
    color: QColor,
    *,
    align: Qt.AlignmentFlag = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
) -> None:
    painter.save()
    painter.setFont(font)
    metrics = QFontMetrics(font)
    elided = metrics.elidedText(text, Qt.TextElideMode.ElideRight, max(1, rect.width()))
    painter.setPen(color)
    painter.drawText(rect, align, elided)
    painter.restore()


def _status_color(status_kind: str) -> QColor:
    normalized = status_kind.strip().lower()
    if normalized in {"keeper", "winner", "accepted"}:
        return QColor(95, 230, 132)
    if normalized in {"reject", "rejected"}:
        return QColor(255, 105, 105)
    if normalized in {"review", "maybe"}:
        return QColor(112, 210, 255)
    return QColor(215, 224, 235)


def _paint_action_button(
    painter: QPainter,
    rect: QRect,
    icon: str,
    *,
    active: bool,
    hover: bool,
    active_color: QColor,
) -> None:
    painter.save()
    fill = QColor(21, 24, 30, 214)
    border = QColor(255, 255, 255, 54)
    text_color = QColor(242, 246, 250)
    if hover:
        fill = QColor(35, 40, 48, 232)
        border = QColor(255, 255, 255, 88)
    if active:
        fill = QColor(active_color)
        fill.setAlpha(60)
        border = QColor(active_color)
        border.setAlpha(178)
        text_color = QColor(active_color)

    painter.setPen(QPen(border, 1))
    painter.setBrush(fill)
    painter.drawEllipse(QRectF(rect))

    if icon == "heart":
        _paint_heart_icon(painter, rect, text_color, filled=active)
    else:
        _paint_reject_icon(painter, rect, text_color)
    painter.restore()


def _paint_reject_icon(painter: QPainter, rect: QRect, color: QColor) -> None:
    inset = rect.width() * 0.365
    pen = QPen(color, max(1.1, rect.width() * 0.031))
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawLine(
        round(rect.left() + inset),
        round(rect.top() + inset),
        round(rect.right() - inset),
        round(rect.bottom() - inset),
    )
    painter.drawLine(
        round(rect.right() - inset),
        round(rect.top() + inset),
        round(rect.left() + inset),
        round(rect.bottom() - inset),
    )


def _paint_heart_icon(painter: QPainter, rect: QRect, color: QColor, *, filled: bool) -> None:
    w = rect.width()
    h = rect.height()
    # Heart path lives in units spanning roughly x[-16, 16], y[-14, 8].
    sx = w * 0.46 / 32.0
    sy = h * 0.46 / 32.0
    cx = rect.center().x()
    # Recentre so the drawn heart sits in the middle of the circle
    # (path midpoint in y is about -3 units).
    cy = rect.center().y() + 3.0 * sy

    def px(x: float) -> float:
        return cx + x * sx

    def py(y: float) -> float:
        return cy + y * sy

    path = QPainterPath()
    path.moveTo(px(0), py(8))
    path.cubicTo(px(-16), py(-1), px(-13), py(-14), px(-5), py(-13))
    path.cubicTo(px(-1), py(-13), px(0), py(-9), px(0), py(-9))
    path.cubicTo(px(0), py(-9), px(1), py(-13), px(5), py(-13))
    path.cubicTo(px(13), py(-14), px(16), py(-1), px(0), py(8))

    pen = QPen(color, max(1.2, w * 0.036))
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(color if filled else Qt.BrushStyle.NoBrush)
    painter.drawPath(path)


__all__ = [
    "GridCardData",
    "GridCardHitRects",
    "grid_card_action_rects",
    "paint_grid_card",
    "render_grid_card_pixmap",
]
