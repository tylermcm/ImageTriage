"""Standalone prototype of the CURRENT single loupe card in the main viewport.

This recreates, pixel for pixel, what ``ThumbnailGridView`` paints today when
the grid collapses to one column (``_use_loupe_card_style``): the tile frame,
the top review pills, the bottom scrim overlay with filename/EXIF/meta rows,
the position + keeper labels, and the circular heart / reject action buttons.

The painting code below is a faithful copy of the loupe branch of
``image_triage/grid.py`` (``_paint_tile``, ``_paint_review_top_badges``,
``_paint_review_overlay`` and their sizing helpers). Colors come from
``default_theme()`` through the same mapping as ``ThumbnailGridView.apply_theme``
so the prototype tracks the live app. Any tuning done here must be ported back
into grid.py to take effect in the app.

Tuned deviations from grid.py (search for "Tuned:"):
- 11:8 card presets so the bottom overlay covers less of the photo.
- Action buttons sized up slightly (was ``max(42, min(74, w // 21))``).
- Buttons drop below the status row instead of clipping the Keeper text
  (grid.py centers them on the meta row).

Run it next to scripts/grid_card_prototype.py to compare the current card with
the proposed renderer.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from PySide6.QtCore import QPoint, QRect, QRectF, QSize, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from image_triage.ui.theme import default_theme

from grid_card_prototype import load_source_pixmap, make_dummy_landscape


LEFT_ARROW_SYMBOL = "❮"
RIGHT_ARROW_SYMBOL = "❯"


# Tuned: 11:8 aspect throughout so the overlay covers less of the photo.
SIZE_PRESETS: dict[str, QSize] = {
    "11:8 main pane - 1375 x 1000": QSize(1375, 1000),
    "11:8 main pane - 1100 x 800": QSize(1100, 800),
    "11:8 - 880 x 640": QSize(880, 640),
    "11:8 compact - 660 x 480": QSize(660, 480),
    "11:8 grid parity - 560 x 407": QSize(560, 407),
}


@dataclass(frozen=True)
class LoupeCardColors:
    """The grid.py color set after ``apply_theme(default_theme())``.

    Fields that apply_theme does not touch keep the constructor values from
    ThumbnailGridView.__init__ (the review_* constants).
    """

    border_active: QColor
    border_selected: QColor
    border_idle: QColor
    background_active: QColor
    background_selected: QColor
    background_idle: QColor
    winner_frame_bg: QColor
    reject_frame_bg: QColor
    accepted: QColor
    winner: QColor
    reject: QColor
    placeholder: QColor
    placeholder_text: QColor
    failed_text: QColor
    meta: QColor
    burst_accent: QColor
    review_badge_border: QColor
    review_duplicate_badge_fill: QColor
    review_duplicate_badge_text: QColor
    review_ai_badge_fill: QColor
    review_ai_badge_color: QColor
    review_keeper_color: QColor
    review_index_text: QColor
    viewport_bg: QColor


def make_loupe_card_colors() -> LoupeCardColors:
    theme = default_theme()
    return LoupeCardColors(
        border_active=QColor("#6090ff"),
        border_selected=QColor("#78a0fa"),
        border_idle=theme.border.qcolor(),
        background_active=theme.raised_bg.qcolor(),
        background_selected=theme.panel_bg.qcolor(),
        background_idle=theme.panel_alt_bg.qcolor(),
        winner_frame_bg=QColor("#111c16"),
        reject_frame_bg=QColor("#171113"),
        accepted=theme.success.qcolor(),
        winner=theme.success.qcolor(),
        reject=theme.danger.qcolor(),
        placeholder=theme.input_hover_bg.qcolor(),
        placeholder_text=theme.text_secondary.qcolor(),
        failed_text=theme.danger.qcolor(),
        meta=theme.text_muted.qcolor(),
        burst_accent=theme.accent.qcolor(),
        review_badge_border=QColor(255, 212, 112, 80),
        review_duplicate_badge_fill=QColor(23, 25, 27, 190),
        review_duplicate_badge_text=QColor("#ead9a8"),
        review_ai_badge_fill=QColor(124, 84, 20, 168),
        review_ai_badge_color=QColor("#ffd36c"),
        review_keeper_color=QColor("#8ef7a8"),
        review_index_text=QColor("#8fc1ff"),
        viewport_bg=theme.window_bg.qcolor(),
    )


@dataclass
class LoupeCardData:
    filename: str = "DSC_7149.NEF"
    exif_text: str = "1/250s · f/5 · ISO 200 · 35mm"
    meta_text: str = "54.4 MB · 2025-08-16 11:15 · Banff 8-25"
    duplicate_text: str = "Near Duplicate · 2/3"
    ai_pick_text: str = "AI Pick · 99"
    ai_score_text: str = "AI 62"
    position_text: str = "1 / 24"
    duplicate_visible: bool = True
    ai_visible: bool = True
    ai_top_pick: bool = True
    current: bool = True
    selected: bool = False
    winner: bool = False
    rejected: bool = False
    heart_hovered: bool = False
    reject_hovered: bool = False
    variant_arrows: bool = False
    burst_bubbles: bool = False
    loading: bool = False
    failed_text: str = ""
    # Tuned variant for comparison: scrim alphas scaled to 65% so the photo
    # stays readable underneath the overlay.
    light_scrim: bool = False


# --- Sizing helpers: verbatim from grid.py -----------------------------------


def _review_scale(image_rect: QRect) -> float:
    return max(1.0, min(2.6, image_rect.width() / 580.0))


def _review_overlay_margin(image_rect: QRect) -> int:
    return max(18, min(56, int(round(image_rect.width() * 0.035))))


def _review_action_button_size(image_rect: QRect) -> int:
    # Tuned: a smidge larger than grid.py's max(42, min(74, w // 21)).
    return max(46, min(80, image_rect.width() // 19))


def _review_action_button_gap(image_rect: QRect) -> int:
    return max(12, min(24, image_rect.width() // 64))


def _review_font(
    point_size: int,
    scale: float,
    weight: QFont.Weight = QFont.Weight.Normal,
    *,
    family: str = "Segoe UI",
) -> QFont:
    return QFont(family, max(1, int(round(point_size * scale))), weight)


def _image_rect(tile_rect: QRect) -> QRect:
    padding = max(10, min(18, tile_rect.width() // 90))
    return QRect(
        tile_rect.x() + padding,
        tile_rect.y() + padding,
        tile_rect.width() - (padding * 2),
        tile_rect.height() - (padding * 2),
    )


def _image_draw_rect(image_rect: QRect, pixmap: QPixmap) -> QRect:
    # Single-column branch of grid.py _image_draw_rect: scale down to fit,
    # anchor to the top, center horizontally.
    draw_size = pixmap.size()
    if draw_size.width() > image_rect.width() or draw_size.height() > image_rect.height():
        draw_size.scale(image_rect.size(), Qt.AspectRatioMode.KeepAspectRatio)
    draw_rect = QRect(QPoint(0, 0), draw_size)
    draw_rect.moveTop(image_rect.top())
    draw_rect.moveLeft(image_rect.left() + max(0, (image_rect.width() - draw_rect.width()) // 2))
    return draw_rect


def photo_fit_height(pane_width: int, pixmap: QPixmap) -> int:
    """Pane height where the photo (scaled to width) plus the overlay text
    block fit exactly, so the scrim only kisses the photo's bottom edge."""
    padding = max(10, min(18, pane_width // 90))
    image_w = pane_width - 2 * padding
    photo_h = round(image_w * pixmap.height() / max(1, pixmap.width()))
    scale = max(1.0, min(2.6, image_w / 580.0))
    margin = max(18, min(56, int(round(image_w * 0.035))))
    button_size = max(46, min(80, image_w // 19))
    title_height = max(24, int(round(17 * scale)))
    capture_height = max(20, int(round(13 * scale)))
    meta_height = max(18, int(round(11 * scale)))
    line_gap = max(5, int(round(7 * scale)))
    text_block = (
        margin + button_size // 2 + meta_height // 2 + line_gap + capture_height + line_gap + title_height + line_gap
    )
    return padding * 2 + photo_h + text_block


# --- Painters: verbatim from grid.py ------------------------------------------


def _badge_icon_metrics(scale: float) -> tuple[int, int]:
    icon_width = max(10, int(round(13 * scale)))
    icon_gap = max(6, int(round(7 * scale)))
    return icon_width, icon_gap


def _paint_duplicate_icon(painter: QPainter, rect: QRect, color: QColor, scale: float) -> None:
    # Two overlapping rounded frames, borrowed from grid_card_renderer.
    painter.save()
    painter.setPen(QPen(color, max(1.3, 1.1 * scale)))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    radius = max(2.5, 2.2 * scale)
    inset = max(3.5, 3.0 * scale)
    back = QRectF(rect).adjusted(1.0, 1.0, -inset, -inset)
    front = QRectF(rect).adjusted(inset, inset, -1.0, -1.0)
    painter.drawRoundedRect(back, radius, radius)
    painter.drawRoundedRect(front, radius, radius)
    painter.restore()


def _paint_spark_icon(painter: QPainter, rect: QRect, color: QColor) -> None:
    # Four-point sparkle, borrowed from grid_card_renderer.
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


def _paint_review_pill(
    painter: QPainter,
    rect: QRect,
    text: str,
    fill: QColor,
    foreground: QColor,
    *,
    border: QColor | None = None,
    scale: float = 1.0,
    icon: str = "",
) -> None:
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setPen(QPen(border, 1.0) if border is not None else Qt.PenStyle.NoPen)
    painter.setBrush(fill)
    # Tuned: rounded rectangle instead of grid.py's fully rounded (height/2) oval.
    radius = min(rect.height() / 2, max(6.0, 6.0 * scale))
    painter.drawRoundedRect(QRectF(rect), radius, radius)

    inset = max(10, int(round(10 * scale)))
    text_rect = rect.adjusted(inset, 0, -inset, 0)
    if icon:
        icon_width, icon_gap = _badge_icon_metrics(scale)
        icon_rect = QRect(
            rect.left() + inset,
            rect.top() + (rect.height() - icon_width) // 2,
            icon_width,
            icon_width,
        )
        if icon == "duplicate":
            _paint_duplicate_icon(painter, icon_rect, foreground, scale)
        elif icon == "spark":
            _paint_spark_icon(painter, icon_rect, foreground)
        text_rect = rect.adjusted(inset + icon_width + icon_gap, 0, -inset, 0)

    painter.setPen(foreground)
    painter.setFont(_review_font(9, scale, QFont.Weight.DemiBold))
    painter.drawText(text_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, text)
    painter.restore()


def _review_badge_rect(
    painter: QPainter, text: str, x: int, y: int, scale: float = 1.0, *, icon: str = ""
) -> QRect:
    painter.save()
    painter.setFont(_review_font(9, scale, QFont.Weight.DemiBold))
    width = max(int(round(82 * scale)), painter.fontMetrics().horizontalAdvance(text) + int(round(22 * scale)))
    painter.restore()
    if icon:
        icon_width, icon_gap = _badge_icon_metrics(scale)
        width += icon_width + icon_gap
    return QRect(x, y, width, max(26, int(round(26 * scale))))


def _paint_reject_icon(painter: QPainter, rect: QRect, color: QColor) -> None:
    # Vector X, borrowed from grid_card_renderer (inset widened slightly so it
    # optically matches the heart).
    inset = rect.width() * 0.35
    pen = QPen(color, max(1.4, rect.width() * 0.05))
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
    # Vector heart, borrowed from grid_card_renderer.
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


# Tuned: user-supplied button artwork (black-on-white PNGs in the repo root),
# inverted to a soft white at load time. Only the central glyph of each PNG is
# drawn — the thick baked-in ring is cropped away and replaced by a thin
# painted ellipse so the circle weight can be tuned independently.
BUTTON_ICON_FILES: dict[str, Path] = {
    "heart": ROOT / "heartbutton.png",
    "reject": ROOT / "xbutton.png",
}


@lru_cache(maxsize=8)
def _button_icon_image(name: str, rgb: tuple[int, int, int]) -> QImage | None:
    path = BUTTON_ICON_FILES.get(name)
    if path is None or not path.exists():
        return None
    source = QImage(str(path))
    if source.isNull():
        return None
    # The assets are black artwork on a light background with no alpha channel,
    # so build the alpha from luminance (black -> opaque, light -> transparent)
    # and fill the visible pixels with the requested color. Faint values are
    # squashed to zero because xbutton.png has a checkerboard pattern baked
    # into its background; the remap keeps stroke edges antialiased.
    gray = source.convertToFormat(QImage.Format.Format_Grayscale8)
    gray.invertPixels()
    floor = 64
    remap = bytes(
        0 if value < floor else min(255, round((value - floor) * 255 / (255 - floor))) for value in range(256)
    )
    mapped = bytes(gray.constBits()).translate(remap)
    alpha = QImage(
        mapped, gray.width(), gray.height(), gray.bytesPerLine(), QImage.Format.Format_Alpha8
    ).copy()
    icon = QImage(source.size(), QImage.Format.Format_ARGB32_Premultiplied)
    icon.fill(Qt.GlobalColor.transparent)
    painter = QPainter(icon)
    painter.fillRect(icon.rect(), QColor(*rgb))
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
    painter.drawImage(0, 0, alpha)
    painter.end()
    return icon


def _paint_review_action_button(
    painter: QPainter,
    rect: QRect,
    icon: str,
    *,
    active: bool,
    hovered: bool,
    accent: QColor,
) -> None:
    if rect.isEmpty():
        return
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    fill = QColor(0, 0, 0, 128 if hovered or active else 88)
    border = QColor(accent) if active else QColor(255, 255, 255)
    border.setAlpha(190 if active else (105 if hovered else 72))
    # Tuned: muted idle white (grid.py used #f2f5f8) so the buttons sit
    # quieter on the scrim; hover brightens back up.
    color = QColor(accent) if active else (QColor("#e9eef3") if hovered else QColor("#c3ccd6"))
    painter.setPen(QPen(border, 1.1))
    painter.setBrush(fill)
    painter.drawEllipse(rect)

    image = _button_icon_image(icon, (color.red(), color.green(), color.blue()))
    if image is not None:
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        # Draw only the central glyph of the artwork; the PNG's own thick ring
        # stays outside this crop and the thin ellipse above replaces it.
        crop_frac = 0.62
        source_inset_x = image.width() * (1 - crop_frac) / 2
        source_inset_y = image.height() * (1 - crop_frac) / 2
        source = QRectF(image.rect()).adjusted(source_inset_x, source_inset_y, -source_inset_x, -source_inset_y)
        target_inset_x = rect.width() * (1 - crop_frac) / 2
        target_inset_y = rect.height() * (1 - crop_frac) / 2
        target = QRectF(rect).adjusted(target_inset_x, target_inset_y, -target_inset_x, -target_inset_y)
        painter.drawImage(target, image, source)
    elif icon == "heart":
        # Fallback: vector icons when the PNG assets are missing.
        _paint_heart_icon(painter, rect, color, filled=active)
    else:
        _paint_reject_icon(painter, rect, color)
    painter.restore()


def _paint_variant_arrow(painter: QPainter, rect: QRect, symbol: str, hovered: bool) -> None:
    if rect.isEmpty():
        return
    painter.save()
    border = QColor(255, 255, 255, 95 if hovered else 55)
    background = QColor(18, 27, 40, 210 if hovered else 170)
    painter.setPen(QPen(border, 1.0))
    painter.setBrush(background)
    painter.drawRoundedRect(QRectF(rect), 10, 10)
    painter.setPen(QColor("#f2f5f8"))
    painter.setFont(QFont("Segoe UI Symbol", 12))
    painter.drawText(rect.adjusted(0, -1, 0, 0), Qt.AlignmentFlag.AlignCenter, symbol)
    painter.restore()


def _paint_burst_nav_bubble(
    painter: QPainter, rect: QRect, symbol: str, hovered: bool, accent: QColor
) -> None:
    if rect.isEmpty():
        return
    painter.save()
    border = QColor(accent)
    border.setAlpha(215 if hovered else 155)
    background = QColor(12, 18, 28, 225 if hovered else 165)
    painter.setPen(QPen(border, 1.2))
    painter.setBrush(background)
    painter.drawEllipse(rect)
    painter.setPen(QColor("#f5f9ff"))
    painter.setFont(QFont("Segoe UI Symbol", 12))
    painter.drawText(rect.adjusted(0, -1, 0, 0), Qt.AlignmentFlag.AlignCenter, symbol)
    painter.restore()


def _paint_review_top_badges(
    painter: QPainter, image_rect: QRect, data: LoupeCardData, colors: LoupeCardColors
) -> None:
    scale = _review_scale(image_rect)
    margin = _review_overlay_margin(image_rect)
    badge_y = image_rect.top() + margin
    if data.duplicate_visible and data.duplicate_text:
        left_rect = _review_badge_rect(
            painter, data.duplicate_text, image_rect.left() + margin, badge_y, scale, icon="duplicate"
        )
        _paint_review_pill(
            painter,
            left_rect,
            data.duplicate_text,
            colors.review_duplicate_badge_fill,
            colors.review_duplicate_badge_text,
            border=colors.review_badge_border,
            scale=scale,
            icon="duplicate",
        )

    if data.ai_visible:
        right_text = data.ai_pick_text if data.ai_top_pick else data.ai_score_text
        if right_text:
            right_rect = _review_badge_rect(painter, right_text, 0, badge_y, scale, icon="spark")
            right_rect.moveRight(image_rect.right() - margin)
            _paint_review_pill(
                painter,
                right_rect,
                right_text,
                colors.review_ai_badge_fill,
                colors.review_ai_badge_color,
                border=colors.review_badge_border,
                scale=scale,
                icon="spark",
            )


def _paint_review_overlay(
    painter: QPainter,
    image_rect: QRect,
    data: LoupeCardData,
    colors: LoupeCardColors,
    photo_bottom: int | None = None,
) -> None:
    scale = _review_scale(image_rect)
    margin = _review_overlay_margin(image_rect)
    button_size = _review_action_button_size(image_rect)
    button_gap = _review_action_button_gap(image_rect)

    title_height = max(24, int(round(17 * scale)))
    capture_height = max(20, int(round(13 * scale)))
    meta_height = max(18, int(round(11 * scale)))
    line_gap = max(5, int(round(7 * scale)))
    # Text rows keep grid.py's anchor (button centered on the meta row) ...
    anchor_center_y = image_rect.bottom() - margin - button_size // 2
    meta_y = anchor_center_y - meta_height // 2
    capture_y = meta_y - capture_height - line_gap
    title_y = capture_y - title_height - line_gap

    # Tuned: ... but the buttons then drop just far enough that their top edge
    # clears the status row, so the Keeper/Review text never clips into them.
    button_top = anchor_center_y - button_size // 2
    min_button_top = capture_y + capture_height + max(4, int(round(4 * scale)))
    button_top += max(0, min_button_top - button_top)
    reject_rect = QRect(0, 0, button_size, button_size)
    reject_rect.moveRight(image_rect.right() - margin)
    reject_rect.moveTop(button_top)
    winner_rect = QRect(0, 0, button_size, button_size)
    winner_rect.moveRight(reject_rect.left() - button_gap)
    winner_rect.moveTop(reject_rect.top())

    clip = QPainterPath()
    clip.addRoundedRect(QRectF(image_rect), 5, 5)

    painter.save()
    painter.setClipPath(clip)
    # Tuned: the scrim's job is to hide the dead space between the photo's
    # bottom edge and the pane, so it stays solid up to the photo's bottom
    # edge (or the top of the text block, whichever is higher) and fades out
    # shortly above that. grid.py sized it to 34% of the pane regardless of
    # where the photo actually ended.
    text_top = title_y - line_gap
    photo_edge = image_rect.bottom() if photo_bottom is None else min(photo_bottom, image_rect.bottom())
    overlap = max(6, int(round(8 * scale)))
    solid_top = min(text_top, photo_edge - overlap)
    solid_h = max(1, image_rect.bottom() - solid_top + 1)
    feather_h = max(20, int(round(30 * scale)))
    total = max(1, min(solid_h + feather_h, image_rect.height()))
    scrim_top = image_rect.bottom() - total + 1
    feather_frac = feather_h / float(solid_h + feather_h)
    scrim_rect = QRect(
        image_rect.left(),
        scrim_top,
        image_rect.width(),
        image_rect.bottom() - scrim_top + 1,
    )
    # Tuned: scrim color #07090d (grid.py used 6, 9, 12).
    scrim_color = QColor("#07090d")
    gradient = QLinearGradient(scrim_rect.topLeft(), scrim_rect.bottomLeft())

    def scrim_stop(alpha: int) -> QColor:
        color = QColor(scrim_color)
        # Light-scrim variant: 35% lighter so the photo shows through.
        color.setAlpha(round(alpha * 0.65) if data.light_scrim else alpha)
        return color

    gradient.setColorAt(0.0, scrim_stop(0))
    gradient.setColorAt(feather_frac * 0.50, scrim_stop(40))
    gradient.setColorAt(feather_frac * 0.80, scrim_stop(125))
    # Tuned: fully opaque through the solid region (grid.py let it breathe at
    # 244-250) so the photo's bottom edge never telegraphs through the scrim.
    gradient.setColorAt(feather_frac, scrim_stop(235))
    gradient.setColorAt(min(1.0, feather_frac + (1.0 - feather_frac) * 0.12), scrim_stop(255))
    gradient.setColorAt(1.0, scrim_stop(255))
    painter.fillRect(scrim_rect, QBrush(gradient))
    painter.restore()
    right_width = max(96, int(round(64 * scale)))
    left_width = max(80, winner_rect.left() - margin - image_rect.left() - margin - int(round(18 * scale)))

    title_rect = QRect(image_rect.left() + margin, title_y, left_width, title_height)
    capture_rect = QRect(title_rect.left(), capture_y, left_width, capture_height)
    meta_rect = QRect(title_rect.left(), meta_y, left_width, meta_height)
    right_rect = QRect(
        image_rect.right() - margin - right_width,
        title_y,
        right_width,
        capture_height + title_height + line_gap,
    )

    painter.save()
    painter.setPen(QColor("#f4f7fb"))
    painter.setFont(_review_font(14, scale, QFont.Weight.DemiBold))
    title_text = painter.fontMetrics().elidedText(data.filename, Qt.TextElideMode.ElideRight, title_rect.width())
    painter.drawText(title_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, title_text)

    painter.setPen(QColor("#d9e5f2"))
    painter.setFont(_review_font(10, scale, QFont.Weight.DemiBold))
    capture_text = painter.fontMetrics().elidedText(data.exif_text, Qt.TextElideMode.ElideRight, capture_rect.width())
    painter.drawText(capture_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, capture_text)

    painter.setPen(colors.meta)
    painter.setFont(_review_font(9, scale))
    meta_text = painter.fontMetrics().elidedText(data.meta_text, Qt.TextElideMode.ElideRight, meta_rect.width())
    painter.drawText(meta_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, meta_text)

    # Image count: same row as the filename (top), right-aligned.
    # Tuned: 10pt instead of grid.py's 12pt — it read too large.
    painter.setPen(colors.review_index_text)
    painter.setFont(_review_font(10, scale, QFont.Weight.Bold))
    painter.drawText(
        QRect(right_rect.left(), title_rect.top(), right_rect.width(), title_height),
        Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
        data.position_text,
    )

    keeper_text = ""
    if data.ai_visible:
        keeper_text = "Keeper" if data.ai_top_pick else "Review"
    if keeper_text:
        # Status below the count, right-aligned.
        # Tuned: sits just under the count row (grid.py used the EXIF row),
        # splitting the old gap so it clears the action buttons below.
        keeper_y = title_rect.bottom() + max(2, int(round(2 * scale)))
        painter.setPen(colors.review_keeper_color)
        painter.setFont(_review_font(10, scale, QFont.Weight.DemiBold))
        # Tuned: rect sized to the real font height plus TextDontClip, so the
        # descender of "Keeper" never gets cut off by drawText's rect clip.
        keeper_height = max(capture_height, painter.fontMetrics().height())
        painter.drawText(
            QRect(right_rect.left(), keeper_y, right_rect.width(), keeper_height),
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter | Qt.TextFlag.TextDontClip,
            keeper_text,
        )
    painter.restore()

    _paint_review_action_button(
        painter,
        winner_rect,
        "heart",
        active=data.winner,
        hovered=data.heart_hovered,
        accent=colors.winner,
    )
    _paint_review_action_button(
        painter,
        reject_rect,
        "reject",
        active=data.rejected,
        hovered=data.reject_hovered,
        accent=colors.reject,
    )


def paint_loupe_card(
    painter: QPainter,
    tile_rect: QRect,
    pixmap: QPixmap | None,
    data: LoupeCardData,
    colors: LoupeCardColors,
) -> None:
    painter.save()

    if data.rejected:
        border_color = colors.reject
        background_color = colors.reject_frame_bg
    elif data.winner:
        border_color = colors.accepted
        background_color = colors.winner_frame_bg
    elif data.current:
        border_color = colors.border_active
        background_color = colors.background_active
    elif data.selected:
        border_color = colors.border_selected
        background_color = colors.background_selected
    else:
        border_color = colors.border_idle
        background_color = colors.background_idle
    painter.setPen(QPen(border_color, 1.4 if data.current or data.selected else 1.0))
    painter.setBrush(background_color)
    painter.drawRoundedRect(QRectF(tile_rect), 7, 7)

    image_rect = _image_rect(tile_rect)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(colors.placeholder)
    painter.drawRoundedRect(QRectF(image_rect), 5, 5)

    photo_bottom: int | None = None
    if pixmap is not None and not pixmap.isNull() and not data.loading and not data.failed_text:
        draw_rect = _image_draw_rect(image_rect, pixmap)
        photo_bottom = draw_rect.bottom()
        clip_path = QPainterPath()
        clip_path.addRoundedRect(QRectF(image_rect), 5, 5)
        painter.save()
        painter.setClipPath(clip_path)
        painter.drawPixmap(draw_rect, pixmap)
        painter.restore()
    elif data.failed_text:
        painter.setPen(colors.failed_text)
        painter.setFont(QFont("Segoe UI", 11))
        painter.drawText(
            image_rect.adjusted(12, 12, -12, -12),
            Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
            f"Failed\n{data.failed_text}",
        )
    else:
        painter.setPen(colors.placeholder_text)
        painter.setFont(QFont("Segoe UI", 11))
        painter.drawText(image_rect, Qt.AlignmentFlag.AlignCenter, "Loading...")

    # Loupe hairline framing the photo area.
    painter.save()
    painter.setPen(QPen(QColor(255, 255, 255, 24), 1.0))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawRoundedRect(QRectF(image_rect).adjusted(0.5, 0.5, -0.5, -0.5), 5, 5)
    painter.restore()

    _paint_review_top_badges(painter, image_rect, data, colors)
    _paint_review_overlay(painter, image_rect, data, colors, photo_bottom=photo_bottom)

    if data.variant_arrows:
        arrow = QRect(0, 0, 30, 42)
        arrow.moveLeft(image_rect.left() + 10)
        arrow.moveTop(image_rect.center().y() - arrow.height() // 2)
        _paint_variant_arrow(painter, arrow, LEFT_ARROW_SYMBOL, False)
        arrow = QRect(0, 0, 30, 42)
        arrow.moveRight(image_rect.right() - 10)
        arrow.moveTop(image_rect.center().y() - arrow.height() // 2)
        _paint_variant_arrow(painter, arrow, RIGHT_ARROW_SYMBOL, False)

    if data.burst_bubbles:
        bubble = QRect(0, 0, 34, 34)
        bubble.moveLeft(image_rect.left() + 12)
        bubble.moveTop(image_rect.center().y() - bubble.height() // 2)
        _paint_burst_nav_bubble(painter, bubble, LEFT_ARROW_SYMBOL, False, colors.burst_accent)
        bubble = QRect(0, 0, 34, 34)
        bubble.moveRight(image_rect.right() - 12)
        bubble.moveTop(image_rect.center().y() - bubble.height() // 2)
        _paint_burst_nav_bubble(painter, bubble, RIGHT_ARROW_SYMBOL, False, colors.burst_accent)

    painter.restore()


def render_loupe_card_pixmap(
    size: QSize,
    pixmap: QPixmap | None,
    data: LoupeCardData,
    colors: LoupeCardColors,
) -> QPixmap:
    output = QPixmap(size)
    output.fill(colors.viewport_bg)
    painter = QPainter(output)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    paint_loupe_card(painter, QRect(QPoint(0, 0), size).adjusted(1, 1, -2, -2), pixmap, data, colors)
    painter.end()
    return output


# --- Prototype harness ---------------------------------------------------------


class CardCanvas(QWidget):
    def __init__(self, source_pixmap: QPixmap | None, *, using_dummy: bool = False) -> None:
        super().__init__()
        self._source_pixmap = source_pixmap
        self._using_dummy = using_dummy
        self._card_size = QSize(1100, 800)
        self._colors = make_loupe_card_colors()
        self._data = LoupeCardData()
        self._bright = False
        self._dark = False
        self.setMinimumSize(720, 500)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_card_size(self, size: QSize) -> None:
        self._card_size = QSize(size)
        self.updateGeometry()
        self.update()

    def set_state(self, data: LoupeCardData, *, bright: bool, dark: bool) -> None:
        self._data = data
        self._bright = bright
        self._dark = dark
        self.update()

    def sizeHint(self) -> QSize:
        return self._card_size + QSize(110, 110)

    def photo_fit_size(self, pane_width: int) -> QSize:
        return QSize(pane_width, photo_fit_height(pane_width, self._pixmap_for_state()))

    def render_card(self) -> QPixmap:
        return render_loupe_card_pixmap(self._card_size, self._pixmap_for_state(), self._data, self._colors)

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt override
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.fillRect(self.rect(), self._colors.viewport_bg)

        available = self.rect().adjusted(20, 20, -20, -20)
        card_size = QSize(self._card_size)
        if card_size.width() > available.width() or card_size.height() > available.height():
            card_size.scale(available.size(), Qt.AspectRatioMode.KeepAspectRatio)
        rect = QRect(QPoint(0, 0), card_size)
        rect.moveCenter(self.rect().center())
        paint_loupe_card(painter, rect, self._pixmap_for_state(), self._data, self._colors)
        painter.end()

    def _pixmap_for_state(self) -> QPixmap:
        if self._source_pixmap is None or self._source_pixmap.isNull() or self._using_dummy:
            base = make_dummy_landscape(QSize(1800, 1100), bright=self._bright, dark=self._dark)
        else:
            base = self._source_pixmap

        if not self._bright and not self._dark:
            return base

        adjusted = QPixmap(base.size())
        adjusted.fill(Qt.GlobalColor.transparent)
        painter = QPainter(adjusted)
        painter.drawPixmap(0, 0, base)
        if self._bright:
            painter.fillRect(adjusted.rect(), QColor(255, 255, 255, 44))
        if self._dark:
            painter.fillRect(adjusted.rect(), QColor(0, 0, 0, 76))
        painter.end()
        return adjusted


class PrototypeWindow(QMainWindow):
    def __init__(self, source_pixmap: QPixmap | None, load_message: str, *, using_dummy: bool) -> None:
        super().__init__()
        self.setWindowTitle("Loupe Card Prototype (current implementation)")
        self.resize(1180, 720)

        self.canvas = CardCanvas(source_pixmap, using_dummy=using_dummy)

        self.size_combo = QComboBox()
        for label in SIZE_PRESETS:
            self.size_combo.addItem(label)
        self.size_combo.setCurrentIndex(1)

        self.current_check = QCheckBox("Current (active frame)")
        self.current_check.setChecked(True)
        self.selected_check = QCheckBox("Selected")
        self.ai_check = QCheckBox("AI badge")
        self.ai_check.setChecked(True)
        self.ai_pick_check = QCheckBox("AI top pick")
        self.ai_pick_check.setChecked(True)
        self.duplicate_check = QCheckBox("Duplicate badge")
        self.duplicate_check.setChecked(True)
        self.winner_check = QCheckBox("Winner / heart active")
        self.reject_check = QCheckBox("Reject")
        self.heart_hover_check = QCheckBox("Hover heart")
        self.reject_hover_check = QCheckBox("Hover reject")
        self.arrows_check = QCheckBox("Variant arrows")
        self.bubbles_check = QCheckBox("Burst bubbles")
        self.loading_check = QCheckBox("Loading placeholder")
        self.bright_check = QCheckBox("Bright image")
        self.dark_check = QCheckBox("Dark image")
        self.three_two_check = QCheckBox("3:2 pane")
        self.photo_fit_check = QCheckBox("Photo-fit pane (photo + overlay)")
        self.light_scrim_check = QCheckBox("Lighter scrim (65%)")

        save_button = QPushButton("Save PNG")
        copy_button = QPushButton("Copy PNG")
        save_button.clicked.connect(self._save_png)
        copy_button.clicked.connect(self._copy_png)

        self.status_label = QLabel(load_message)
        self.status_label.setWordWrap(True)
        self.status_label.setObjectName("statusLabel")

        controls = QFrame()
        controls.setObjectName("controls")
        controls_layout = QVBoxLayout(controls)
        controls_layout.setContentsMargins(14, 14, 14, 14)
        controls_layout.setSpacing(10)

        title = QLabel("Card States")
        title.setObjectName("panelTitle")
        controls_layout.addWidget(title)
        controls_layout.addWidget(self.size_combo)
        controls_layout.addSpacing(4)
        for checkbox in self._checkboxes():
            controls_layout.addWidget(checkbox)
        controls_layout.addSpacing(8)

        button_row = QHBoxLayout()
        button_row.addWidget(save_button)
        button_row.addWidget(copy_button)
        controls_layout.addLayout(button_row)
        controls_layout.addStretch(1)
        controls_layout.addWidget(self.status_label)

        root = QWidget()
        layout = QGridLayout(root)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setHorizontalSpacing(12)
        layout.addWidget(self.canvas, 0, 0)
        layout.addWidget(controls, 0, 1)
        layout.setColumnStretch(0, 1)
        layout.setColumnMinimumWidth(1, 250)
        self.setCentralWidget(root)

        self.size_combo.currentTextChanged.connect(self._apply_state)
        for checkbox in self._checkboxes():
            checkbox.toggled.connect(self._apply_state)

        self._apply_state()
        self._apply_style()

    def _checkboxes(self) -> tuple[QCheckBox, ...]:
        return (
            self.current_check,
            self.selected_check,
            self.ai_check,
            self.ai_pick_check,
            self.duplicate_check,
            self.winner_check,
            self.reject_check,
            self.heart_hover_check,
            self.reject_hover_check,
            self.arrows_check,
            self.bubbles_check,
            self.loading_check,
            self.bright_check,
            self.dark_check,
            self.three_two_check,
            self.photo_fit_check,
            self.light_scrim_check,
        )

    def _apply_state(self) -> None:
        if self.sender() is self.reject_check and self.reject_check.isChecked():
            self.winner_check.setChecked(False)
        if self.sender() is self.winner_check and self.winner_check.isChecked():
            self.reject_check.setChecked(False)
        if self.sender() is self.bright_check and self.bright_check.isChecked():
            self.dark_check.setChecked(False)
        if self.sender() is self.dark_check and self.dark_check.isChecked():
            self.bright_check.setChecked(False)

        if self.sender() is self.photo_fit_check and self.photo_fit_check.isChecked():
            self.three_two_check.setChecked(False)
        if self.sender() is self.three_two_check and self.three_two_check.isChecked():
            self.photo_fit_check.setChecked(False)

        size = SIZE_PRESETS[self.size_combo.currentText()]
        if self.photo_fit_check.isChecked():
            size = self.canvas.photo_fit_size(size.width())
        elif self.three_two_check.isChecked():
            size = QSize(size.width(), round(size.width() * 2 / 3))
        self.canvas.set_card_size(size)
        data = LoupeCardData(
            duplicate_visible=self.duplicate_check.isChecked(),
            ai_visible=self.ai_check.isChecked(),
            ai_top_pick=self.ai_pick_check.isChecked(),
            current=self.current_check.isChecked(),
            selected=self.selected_check.isChecked(),
            winner=self.winner_check.isChecked(),
            rejected=self.reject_check.isChecked(),
            heart_hovered=self.heart_hover_check.isChecked(),
            reject_hovered=self.reject_hover_check.isChecked(),
            variant_arrows=self.arrows_check.isChecked(),
            burst_bubbles=self.bubbles_check.isChecked(),
            loading=self.loading_check.isChecked(),
            light_scrim=self.light_scrim_check.isChecked(),
        )
        self.canvas.set_state(
            data,
            bright=self.bright_check.isChecked(),
            dark=self.dark_check.isChecked(),
        )

    def _save_png(self) -> None:
        default = str(ROOT / "loupe-card-prototype.png")
        path, _selected_filter = QFileDialog.getSaveFileName(
            self,
            "Save prototype card",
            default,
            "PNG Images (*.png)",
        )
        if not path:
            return
        if not path.lower().endswith(".png"):
            path += ".png"
        self.canvas.render_card().save(path, "PNG")
        self.status_label.setText(f"Saved {path}")

    def _copy_png(self) -> None:
        QApplication.clipboard().setPixmap(self.canvas.render_card())
        self.status_label.setText("Copied rendered card to the clipboard.")

    def _apply_style(self) -> None:
        self.setStyleSheet(
            """
            QMainWindow, QWidget {
                background: #07080a;
                color: #dbe4ef;
                font-family: Segoe UI;
                font-size: 12px;
            }
            QFrame#controls {
                background: #111316;
                border: 1px solid #282c31;
                border-radius: 8px;
            }
            QLabel#panelTitle {
                color: #ffffff;
                font-size: 14px;
                font-weight: 700;
            }
            QLabel#statusLabel {
                color: #8f9bad;
            }
            QComboBox, QPushButton {
                background: #1b1e23;
                color: #f4f7fb;
                border: 1px solid #2f3540;
                border-radius: 6px;
                padding: 7px 9px;
            }
            QComboBox:hover, QPushButton:hover {
                background: #252a31;
                border-color: #465263;
            }
            QCheckBox {
                color: #cbd5e1;
                spacing: 8px;
                padding: 2px 0;
            }
            QCheckBox::indicator {
                width: 15px;
                height: 15px;
                border-radius: 4px;
                border: 1px solid #44505f;
                background: #11161c;
            }
            QCheckBox::indicator:checked {
                background: #3d7cff;
                border-color: #68a2ff;
            }
            """
        )


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prototype the current main-pane loupe card.")
    parser.add_argument("--image", help="Optional image path to draw inside the card.")
    parser.add_argument("--save", help="Render once to this PNG path and exit.")
    parser.add_argument("--width", type=int, default=1100, help="PNG render width when --save is used.")
    parser.add_argument("--height", type=int, default=800, help="PNG render height when --save is used.")
    parser.add_argument("--three-two", action="store_true", help="Use a 3:2 pane (height derived from width).")
    parser.add_argument(
        "--photo-fit", action="store_true", help="Size the pane to photo height plus overlay text block."
    )
    parser.add_argument("--light-scrim", action="store_true", help="Render the 35%% lighter scrim variant.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    app = QApplication(sys.argv[:1])
    source_pixmap, load_message, using_dummy = load_source_pixmap(args.image)

    if args.save:
        data = LoupeCardData(light_scrim=args.light_scrim)
        source = source_pixmap if not using_dummy else make_dummy_landscape(QSize(1800, 1100))
        if args.photo_fit:
            size = QSize(args.width, photo_fit_height(args.width, source))
        elif args.three_two:
            size = QSize(args.width, round(args.width * 2 / 3))
        else:
            size = QSize(args.width, args.height)
        output = render_loupe_card_pixmap(size, source, data, make_loupe_card_colors())
        if not output.save(args.save, "PNG"):
            print(f"Failed to save {args.save}", file=sys.stderr)
            return 2
        print(f"Saved {args.save}")
        return 0

    window = PrototypeWindow(source_pixmap, load_message, using_dummy=using_dummy)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
