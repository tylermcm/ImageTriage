"""Standalone painter for main-viewport grid cards.

This module intentionally has no app-state dependencies. It receives a pixmap,
metadata, and state flags, then paints one card with QPainter. The prototype and
the eventual grid integration should share this renderer.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import QPoint, QRect, QRectF, QSize, Qt
from PySide6.QtGui import (
    QColor,
    QBrush,
    QFont,
    QFontMetrics,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)

from .prototype_style import folder_icon_pixmap


_ACTION_ICON_FILES: dict[str, Path] = {
    "heart": Path(__file__).resolve().parent / "assets" / "loupe_heart.png",
    "reject": Path(__file__).resolve().parent / "assets" / "loupe_reject.png",
}


@lru_cache(maxsize=16)
def load_action_icon(name: str, rgb: tuple[int, int, int]) -> QImage | None:
    """User-supplied action-button artwork recolored for overlay use.

    The source assets are black artwork on a light background with no alpha
    channel, so the alpha is built from luminance (black -> opaque, light ->
    transparent) and the visible pixels are filled with the requested color.
    Faint values are squashed to zero because the reject asset has a
    checkerboard pattern baked into its background; the remap keeps stroke
    edges antialiased. Callers draw only the central glyph — the thick
    baked-in ring stays outside the sampled crop.
    """
    path = _ACTION_ICON_FILES.get(name)
    if path is None or not path.exists():
        return None
    source = QImage(str(path))
    if source.isNull():
        return None
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


def _paint_action_icon_image(painter: QPainter, rect: QRect, image: QImage) -> None:
    """Draw the central glyph of the artwork; the asset's own thick ring stays
    outside this crop so the painted ellipse defines the circle."""
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    crop_frac = 0.62
    source_inset_x = image.width() * (1 - crop_frac) / 2
    source_inset_y = image.height() * (1 - crop_frac) / 2
    source = QRectF(image.rect()).adjusted(source_inset_x, source_inset_y, -source_inset_x, -source_inset_y)
    target_inset_x = rect.width() * (1 - crop_frac) / 2
    target_inset_y = rect.height() * (1 - crop_frac) / 2
    target = QRectF(rect).adjusted(target_inset_x, target_inset_y, -target_inset_x, -target_inset_y)
    painter.drawImage(target, image, source)


# Past this many grid columns the cards should switch to the barebones compact
# layout: a 3:2 photo with icon-only badge chips in the top corners and the
# heart/reject buttons in the bottom corners.
COMPACT_COLUMN_THRESHOLD = 4
# Past this many columns even the compact overlay goes away — plain photo.
PLAIN_PHOTO_COLUMN_THRESHOLD = 5

# The detailed card is composed at the size of the approved four-column view
# and then uniformly transformed into the live tile. Keeping every internal
# measurement in this one design space makes the card behave like a flattened
# layout in a graphics editor: text, badges, buttons, scrims, and strokes all
# resize together instead of independently hitting pixel-size floors.
DETAILED_CARD_REFERENCE_WIDTH = 356
DETAILED_CARD_REFERENCE_HEIGHT = 285

# The uniform scale is symmetric: it shrinks the whole card gracefully on dense
# grids, but it would also blow the chrome up on wide 1-2 column cards until the
# filename, buttons, and scrim dominate the image. Cap the chrome's growth here:
# below the cap every layer scales together (the flattened-layout win); at and
# above it the chrome freezes at its reference size and the extra tile space
# flows to the photo instead. 1.0 freezes it at the four-column reference look;
# raise it to let the chrome keep growing a little through the 3-column range.
# 1.15 lets the footer strip scale ~15% past the four-column reference on wide
# 1-3 column cards (where the frozen chrome read a touch small against the big
# photo) while 4-column and denser grids stay exactly at the tuned reference.
DETAILED_CARD_MAX_CHROME_SCALE = 1.15

# Default corner rounding in the renderer's native coordinate space. Detailed
# cards transform it with the rest of the composition; compact cards use it as
# a live-pixel value unless the caller supplies a column-aware override.
IMAGE_CORNER_RADIUS = 8.0

# Photo corner rounding scales with the grid column count: wide single-column
# cards get the most rounding, dense eight-column cards the least, linearly
# between. Endpoints are the design values; the per-column step is
# (12 - 6) / (8 - 1) = 6/7 ≈ 0.857.
GRID_CORNER_RADIUS_AT_MIN_COLUMNS = 12.0  # radius at 1 column
GRID_CORNER_RADIUS_AT_MAX_COLUMNS = 6.0   # radius at 8 columns
GRID_CORNER_RADIUS_MIN_COLUMNS = 1
GRID_CORNER_RADIUS_MAX_COLUMNS = 8


def grid_card_corner_radius(columns: int) -> float:
    """Photo corner radius for a card shown at ``columns`` grid columns.

    Linear from 12px at 1 column down to 6px at 8 columns, clamped to that
    range outside 1..8 columns.
    """
    clamped = max(GRID_CORNER_RADIUS_MIN_COLUMNS, min(GRID_CORNER_RADIUS_MAX_COLUMNS, int(columns)))
    span = GRID_CORNER_RADIUS_MAX_COLUMNS - GRID_CORNER_RADIUS_MIN_COLUMNS
    t = (clamped - GRID_CORNER_RADIUS_MIN_COLUMNS) / span
    return GRID_CORNER_RADIUS_AT_MIN_COLUMNS + t * (
        GRID_CORNER_RADIUS_AT_MAX_COLUMNS - GRID_CORNER_RADIUS_AT_MIN_COLUMNS
    )


@dataclass(frozen=True, slots=True)
class GridCardData:
    filename: str = "DSC_7149.NEF"
    exif_text: str = "1/250s  \u00b7  f/5  \u00b7  ISO 200  \u00b7  35mm"
    meta_text: str = "54.4 MB  \u00b7  2025-08-16 11:15  \u00b7  Banff 8-25"
    duplicate_text: str = "Near Duplicate \u00b7 2/3"
    ai_text: str = "AI Pick \u00b7 99"
    position_text: str = "1 / 24"
    status_text: str = "Winner"
    status_kind: str = "keeper"
    duplicate_visible: bool = True
    ai_visible: bool = True
    # AI workflow tags rendered as a rail under the top-left badge:
    # (text, kind) pairs where kind picks the accent color (e.g. "best_frame",
    # "round", "disputed", "needs_review", "ai_miss", "edited").
    tags: tuple[tuple[str, str], ...] = ()
    selected: bool = False
    favorite: bool = False
    rejected: bool = False
    hover_favorite: bool = False
    hover_reject: bool = False
    # Immersive review style: the photo fills the whole cell (no footer strip
    # below it) and the scrim alphas scale to 65% so the photo stays readable
    # underneath the overlay. Photo-fit keeps the text strip under the photo.
    immersive: bool = False
    # Folder tiles: paint the flat folder glyph in the photo pane and hide the
    # heart/reject actions (folders cannot be culled).
    is_folder: bool = False
    show_actions: bool = True


@dataclass(frozen=True, slots=True)
class GridCardHitRects:
    favorite: QRect
    reject: QRect


def render_grid_card_pixmap(
    size: QSize,
    source_pixmap: QPixmap | None,
    data: GridCardData,
    *,
    compact: bool = False,
    compact_actions: str = "corners",
    compact_filename: bool = False,
    compact_badge_text: bool = False,
    compact_overlay: bool = True,
    corner_radius: float | None = None,
) -> QPixmap:
    """Render a single card into a transparent pixmap."""

    output = QPixmap(size)
    output.fill(Qt.GlobalColor.transparent)
    painter = QPainter(output)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    paint_grid_card(
        painter,
        QRect(QPoint(0, 0), size),
        source_pixmap,
        data,
        compact=compact,
        compact_actions=compact_actions,
        compact_filename=compact_filename,
        compact_badge_text=compact_badge_text,
        compact_overlay=compact_overlay,
        corner_radius=corner_radius,
    )
    painter.end()
    return output


def paint_grid_card(
    painter: QPainter,
    rect: QRect,
    source_pixmap: QPixmap | None,
    data: GridCardData,
    *,
    compact: bool = False,
    compact_actions: str = "corners",
    compact_filename: bool = False,
    compact_badge_text: bool = False,
    compact_overlay: bool = True,
    corner_radius: float | None = None,
) -> GridCardHitRects:
    """Paint one card and return action hit rectangles in viewport space."""

    if not compact and not data.immersive:
        return _paint_scaled_detailed_card(
            painter,
            rect,
            source_pixmap,
            data,
            compact_actions=compact_actions,
            compact_filename=compact_filename,
            compact_badge_text=compact_badge_text,
            compact_overlay=compact_overlay,
            corner_radius=corner_radius,
        )
    return _paint_grid_card_native(
        painter,
        rect,
        source_pixmap,
        data,
        compact=compact,
        compact_actions=compact_actions,
        compact_filename=compact_filename,
        compact_badge_text=compact_badge_text,
        compact_overlay=compact_overlay,
        corner_radius=corner_radius,
    )


def _paint_grid_card_native(
    painter: QPainter,
    rect: QRect,
    source_pixmap: QPixmap | None,
    data: GridCardData,
    *,
    compact: bool = False,
    compact_actions: str = "corners",
    compact_filename: bool = False,
    compact_badge_text: bool = False,
    compact_overlay: bool = True,
    corner_radius: float | None = None,
) -> GridCardHitRects:
    """Paint one main viewport card and return action hit rectangles.

    ``corner_radius`` overrides IMAGE_CORNER_RADIUS for the photo clip and
    the matching selection ring; None keeps the module default.

    ``compact_overlay=False`` strips the compact card down to the plain
    photo (plus the selection ring) — no badges, tags, filename, or action
    buttons. Used past the column threshold, where the chrome is too small
    to be useful. Hit rects come back empty in that mode.

    ``compact`` selects the barebones layout used past four grid columns:
    the photo fills the card at 3:2, with badge chips in the top corners and
    the heart/reject buttons along the bottom, all sharing the same inset.
    No status, position, or scrim. ``compact_actions`` places the buttons in
    opposite bottom corners ("corners") or side by side in the bottom-right
    ("right"). With the "right" layout, ``compact_filename`` draws the
    filename left-aligned on the button row. ``compact_badge_text`` renders
    the duplicate/AI badges with their text instead of icon-only chips.
    """

    if rect.width() <= 8 or rect.height() <= 8:
        return GridCardHitRects(QRect(), QRect())

    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

    scale = _scale_for(rect, compact)
    outer = QRectF(rect).adjusted(0.5, 0.5, -0.5, -0.5)

    # Frameless: no card background or border — the content sits directly on
    # the viewport, matching the single-column loupe.
    pad = _content_pad(scale, compact)
    content_rect = QRect(
        rect.left() + pad,
        rect.top() + pad,
        max(1, rect.width() - pad * 2),
        max(1, rect.height() - pad * 2),
    )
    image_radius = IMAGE_CORNER_RADIUS if corner_radius is None else max(0.0, float(corner_radius))
    # Only round the photo pixels when a radius is explicitly requested (the
    # prototype). With no override the app keeps its existing behavior exactly.
    round_photo = corner_radius is not None

    if compact or data.immersive:
        # Barebones/immersive: the photo owns the whole cell (the compact
        # cell itself is sized 3:2 by the caller).
        photo_rect = QRect(content_rect)
    else:
        # Photo-fit uses a full-width 3:2 photo pane. The footer/scrim is
        # responsible for hiding the lower transition rather than shortening
        # the photo and breaking the expected image ratio.
        photo_height = min(round(content_rect.width() * 2 / 3), content_rect.height())
        photo_rect = QRect(content_rect.left(), content_rect.top(), content_rect.width(), photo_height)

    _paint_image(
        painter,
        content_rect,
        image_radius,
        source_pixmap,
        photo_rect=photo_rect,
        round_photo=round_photo,
        folder=data.is_folder,
    )
    if not compact:
        # The footer text block overlaps the photo's lower edge on grid
        # tiles, so both full styles scrim it for guaranteed contrast:
        # detailed with the solid fade, immersive with the lighter (65%)
        # variant. Barebones has no text and needs no scrim.
        _paint_scrim(painter, rect, content_rect, image_radius, scale, light=data.immersive)

    if compact and not compact_overlay:
        favorite_rect = QRect()
        reject_rect = QRect()
    elif compact:
        _paint_compact_badges(painter, rect, content_rect, data, scale, show_text=compact_badge_text)
        favorite_rect, reject_rect = _paint_compact_overlay(
            painter,
            rect,
            content_rect,
            data,
            scale,
            compact_actions,
            show_filename=compact_filename,
            source_pixmap=source_pixmap,
        )
    else:
        _paint_badges(painter, rect, content_rect, data, scale)
        favorite_rect, reject_rect = _paint_bottom_overlay(painter, rect, content_rect, data, scale)

    if data.selected:
        # Selection ring on the content itself (no card frame to carry it);
        # a thin dark contrast line inside the accent keeps it readable over
        # bright photo edges — same treatment as the single-column loupe.
        ring_radius = max(3.0, image_radius - 1.0)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(0, 0, 0, 140), 1.0))
        painter.drawRoundedRect(QRectF(rect).adjusted(2.0, 2.0, -2.0, -2.0), ring_radius, ring_radius)
        painter.setPen(QPen(QColor(80, 140, 255), 1.6))
        painter.drawRoundedRect(outer, image_radius, image_radius)

    painter.restore()
    return GridCardHitRects(favorite_rect, reject_rect)


_DETAILED_REFERENCE_RECT = QRect(
    0, 0, DETAILED_CARD_REFERENCE_WIDTH, DETAILED_CARD_REFERENCE_HEIGHT
)


def _detailed_reference_scale() -> float:
    """The constant per-element ``scale`` the detailed chrome is composed at.

    Fixed to the reference tile so font/spacing floors resolve identically no
    matter the live card size; the uniform transform does the resizing.
    """
    return _scale_for(_DETAILED_REFERENCE_RECT, False)


def _detailed_footer_band(chrome_scale: float) -> int:
    """Height of the black strip below the photo, in live pixels.

    In the reference composition the 3:2 photo leaves this band beneath it for
    the footer text. It scales with the chrome (not the tile), so once the
    chrome is capped the band stops growing and the photo keeps all the extra
    height instead of sitting above a fat black gutter.
    """
    band = DETAILED_CARD_REFERENCE_HEIGHT - round(DETAILED_CARD_REFERENCE_WIDTH * 2 / 3)
    return max(0, round(band * chrome_scale))


def _detailed_chrome_layout(rect: QRect) -> tuple[float, QRect]:
    """Chrome scale and the design canvas it is composed in for ``rect``.

    ``chrome_scale`` is the uniform factor applied to the footer chrome, capped
    by DETAILED_CARD_MAX_CHROME_SCALE. The design canvas is the tile divided by
    that scale, so right/bottom-anchored chrome still reaches the real tile
    edges after the transform while its size stays frozen at the reference.
    """
    raw_scale = rect.width() / DETAILED_CARD_REFERENCE_WIDTH
    chrome_scale = min(raw_scale, DETAILED_CARD_MAX_CHROME_SCALE)
    design_w = max(1, round(rect.width() / chrome_scale))
    design_h = max(1, round(rect.height() / chrome_scale))
    return chrome_scale, QRect(0, 0, design_w, design_h)


def _map_detailed_hit_rect(rect: QRect, scale: float, origin_x: float, origin_y: float) -> QRect:
    if rect.isEmpty():
        return QRect()
    return QRect(
        round(origin_x + rect.left() * scale),
        round(origin_y + rect.top() * scale),
        max(1, round(rect.width() * scale)),
        max(1, round(rect.height() * scale)),
    )


def _paint_scaled_detailed_card(
    painter: QPainter,
    rect: QRect,
    source_pixmap: QPixmap | None,
    data: GridCardData,
    *,
    compact_actions: str,
    compact_filename: bool,
    compact_badge_text: bool,
    compact_overlay: bool,
    corner_radius: float | None,
) -> GridCardHitRects:
    if rect.width() <= 8 or rect.height() <= 8:
        return GridCardHitRects(QRect(), QRect())

    chrome_scale, design_rect = _detailed_chrome_layout(rect)
    ref_scale = _detailed_reference_scale()
    ref_width = DETAILED_CARD_REFERENCE_WIDTH

    image_radius = IMAGE_CORNER_RADIUS if corner_radius is None else max(0.0, float(corner_radius))
    round_photo = corner_radius is not None

    painter.save()
    painter.setClipRect(rect, Qt.ClipOperation.IntersectClip)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

    # Layer 1 (real tile coordinates): the photo fills the whole tile except a
    # footer band whose height is frozen with the chrome, so wide cards spend
    # their extra pixels on the image instead of a taller black gutter.
    footer_band = _detailed_footer_band(chrome_scale)
    photo_height = max(1, rect.height() - footer_band)
    photo_rect = QRect(rect.left(), rect.top(), rect.width(), photo_height)
    _paint_image(
        painter,
        rect,
        image_radius,
        source_pixmap,
        photo_rect=photo_rect,
        round_photo=round_photo,
        folder=data.is_folder,
    )

    # Layers 2+ (scrim, badges, footer text/buttons): composed once at the
    # reference metrics and drawn through a single uniform transform so every
    # layer scales together, but frozen in size once past the cap.
    painter.save()
    painter.translate(rect.left(), rect.top())
    painter.scale(chrome_scale, chrome_scale)
    design_radius = image_radius / chrome_scale if chrome_scale else image_radius
    _paint_scrim(
        painter, design_rect, design_rect, design_radius, ref_scale, chrome_width=ref_width
    )
    _paint_badges(painter, design_rect, design_rect, data, ref_scale, chrome_width=ref_width)
    favorite_design, reject_design = _paint_bottom_overlay(
        painter, design_rect, design_rect, data, ref_scale, chrome_width=ref_width
    )
    painter.restore()

    if data.selected:
        outer = QRectF(rect).adjusted(0.5, 0.5, -0.5, -0.5)
        ring_radius = max(3.0, image_radius - 1.0)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(0, 0, 0, 140), 1.0))
        painter.drawRoundedRect(QRectF(rect).adjusted(2.0, 2.0, -2.0, -2.0), ring_radius, ring_radius)
        painter.setPen(QPen(QColor(80, 140, 255), 1.6))
        painter.drawRoundedRect(outer, image_radius, image_radius)

    painter.restore()

    return GridCardHitRects(
        _map_detailed_hit_rect(favorite_design, chrome_scale, rect.left(), rect.top()),
        _map_detailed_hit_rect(reject_design, chrome_scale, rect.left(), rect.top()),
    )


def _scale_for(rect: QRect, compact: bool = False) -> float:
    if compact:
        # Sub-linear (square-root) response: the chrome keeps shrinking as
        # columns increase so it never dominates the card, but slower than
        # 1:1 so text and icons stay readable at high column counts. The cap
        # lets the chrome keep growing on very wide cards (1-2 columns, the
        # immersive loupe) without ballooning.
        return max(0.42, min(1.75, (rect.width() / 560.0) ** 0.5))
    # Floor lowered from 0.72 so full cards keep shrinking their chrome on small
    # cells (low res / many columns) instead of clipping. Wide cards are
    # unchanged (they sit well above the floor).
    return max(0.5, min(1.25, rect.width() / 560.0))


def _content_pad(scale: float, compact: bool) -> int:
    # Frameless cards: the photo owns the whole cell, so there is no inset.
    # Kept as a function so paint and hit-testing stay on one definition.
    return 0


def _detailed_footer_band_for_width(width: int) -> int:
    """Footer-band height for a full card of the given width (paint-consistent).

    Mirrors the band used at paint time so ``grid_card_height_for_width`` and
    ``_paint_scaled_detailed_card`` always agree on where the 3:2 photo ends.
    """
    chrome_scale = min(width / DETAILED_CARD_REFERENCE_WIDTH, DETAILED_CARD_MAX_CHROME_SCALE)
    return _detailed_footer_band(chrome_scale)


def grid_card_height_for_width(width: int, *, compact: bool = False) -> int:
    """Cell height the card is designed for at a given width.

    Full cards are a true 3:2 photo pane plus a footer band. The band's height
    is frozen with the chrome once the card grows past the reference size, so
    wide 1-2 column cards stay close to 3:2 (a thin footer) instead of a 5:4
    tile with a fat black gutter, while at the reference and denser grids the
    band still scales with the tile (the tuned look is unchanged). Barebones
    compact cards are the 3:2 photo and nothing else.
    """
    if compact:
        return max(1, round(width * 2 / 3))
    return max(1, round(width * 2 / 3) + _detailed_footer_band_for_width(width))


# Gallery style: a clean 3:2 photo, then the filename (left) and the heart/
# reject actions (right) sitting in a transparent strip BELOW the photo,
# directly on the app background — no footer band, no on-photo chrome. The
# strip is frozen with the same cap as the detailed chrome so it never
# balloons on wide 1-2 column cards.
GALLERY_STRIP_REFERENCE_HEIGHT = 40


def _gallery_strip_height(width: int) -> int:
    chrome_scale = min(width / DETAILED_CARD_REFERENCE_WIDTH, DETAILED_CARD_MAX_CHROME_SCALE)
    return max(22, round(GALLERY_STRIP_REFERENCE_HEIGHT * chrome_scale))


def gallery_card_height_for_width(width: int) -> int:
    """Tile height for the gallery style: a 3:2 photo plus the below strip."""
    return max(1, round(width * 2 / 3) + _gallery_strip_height(width))


def _gallery_metrics(rect: QRect) -> tuple[float, int, int, int, int, int]:
    """Shared geometry so gallery paint and hit-testing always agree.

    Returns (chrome_scale, photo_height, strip_top, button, gap, edge)."""
    chrome_scale = min(rect.width() / DETAILED_CARD_REFERENCE_WIDTH, DETAILED_CARD_MAX_CHROME_SCALE)
    photo_height = round(rect.width() * 2 / 3)
    strip_top = rect.top() + photo_height
    button = max(17, round(23 * chrome_scale))
    gap = max(8, round(13 * chrome_scale))
    edge = max(2, round(3 * chrome_scale))
    return chrome_scale, photo_height, strip_top, button, gap, edge


def grid_gallery_action_rects(rect: QRect) -> GridCardHitRects:
    """Heart/reject hit rects for the gallery card: right-aligned in the strip
    beneath the photo. Kept in sync with ``paint_gallery_card``."""
    if rect.width() <= 8 or rect.height() <= 8:
        return GridCardHitRects(QRect(), QRect())
    _, _, strip_top, button, gap, edge = _gallery_metrics(rect)
    strip = _gallery_strip_height(rect.width())
    top = strip_top + max(0, (strip - button) // 2)
    reject = QRect(rect.right() - edge - button, top, button, button)
    favorite = QRect(reject.left() - gap - button, top, button, button)
    return GridCardHitRects(favorite, reject)


def paint_gallery_card(
    painter: QPainter,
    rect: QRect,
    source_pixmap: QPixmap | None,
    data: GridCardData,
    *,
    corner_radius: float | None = None,
    filename_color: QColor | None = None,
    action_fill: QColor | None = None,
    action_hover_fill: QColor | None = None,
    action_border: QColor | None = None,
    action_hover_border: QColor | None = None,
    action_icon_color: QColor | None = None,
    action_hover_icon_color: QColor | None = None,
) -> GridCardHitRects:
    """Paint the gallery card: a rounded 3:2 photo with the filename and the
    heart/reject actions on the transparent strip below it."""
    if rect.width() <= 8 or rect.height() <= 8:
        return GridCardHitRects(QRect(), QRect())

    chrome_scale, photo_height, strip_top, button, gap, edge = _gallery_metrics(rect)
    strip = _gallery_strip_height(rect.width())
    image_radius = IMAGE_CORNER_RADIUS if corner_radius is None else max(0.0, float(corner_radius))
    round_photo = corner_radius is not None

    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

    photo_rect = QRect(rect.left(), rect.top(), rect.width(), max(1, photo_height))
    _paint_image(
        painter,
        photo_rect,
        image_radius,
        source_pixmap,
        photo_rect=photo_rect,
        round_photo=round_photo,
        folder=data.is_folder,
    )
    if data.selected:
        ring_radius = max(3.0, image_radius - 1.0)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(0, 0, 0, 140), 1.0))
        painter.drawRoundedRect(QRectF(photo_rect).adjusted(1.5, 1.5, -1.5, -1.5), ring_radius, ring_radius)
        painter.setPen(QPen(QColor(80, 140, 255), 1.6))
        painter.drawRoundedRect(QRectF(photo_rect).adjusted(0.5, 0.5, -0.5, -0.5), image_radius, image_radius)

    hits = grid_gallery_action_rects(rect)
    favorite_rect = QRect(hits.favorite)
    reject_rect = QRect(hits.reject)

    if data.show_actions:
        _paint_action_button(
            painter, favorite_rect, "heart",
            active=data.favorite, hover=data.hover_favorite, active_color=QColor(245, 95, 118),
            idle_fill=action_fill,
            hover_fill=action_hover_fill,
            idle_border=action_border,
            hover_border=action_hover_border,
            idle_icon_color=action_icon_color,
            hover_icon_color=action_hover_icon_color,
        )
        _paint_action_button(
            painter, reject_rect, "reject",
            active=data.rejected, hover=data.hover_reject, active_color=QColor(255, 107, 107),
            idle_fill=action_fill,
            hover_fill=action_hover_fill,
            idle_border=action_border,
            hover_border=action_hover_border,
            idle_icon_color=action_icon_color,
            hover_icon_color=action_hover_icon_color,
        )
        name_right_limit = favorite_rect.left() - gap
    else:
        favorite_rect = QRect()
        reject_rect = QRect()
        name_right_limit = rect.right() - edge

    name_left = rect.left() + max(1, round(2 * chrome_scale))
    name_font = QFont("Segoe UI", max(11, round(12 * chrome_scale)))
    name_rect = QRect(name_left, strip_top, max(1, name_right_limit - name_left), strip)
    _draw_elided_text(painter, name_rect, data.filename, name_font, filename_color or QColor(154, 160, 166))

    painter.restore()
    return GridCardHitRects(favorite_rect, reject_rect)


def _footer_basis_for_width(width: int) -> int:
    """Footer/badge height basis for a card of the given width."""
    return max(1, round(width * 407 / 560))


def _full_card_basis(card_rect: QRect) -> int:
    """Height basis for the full card's footer/badge geometry.

    The chrome was designed against the original 11:8 (407/560) tile. The
    cell is now taller (see grid_card_height_for_width) so the footer clears
    more of the photo, but the chrome itself must not grow with the cell —
    so every former ``card_rect.height()`` fraction measures against this
    width-derived basis instead.
    """
    return _footer_basis_for_width(card_rect.width())


def _chrome_size_width(card_rect: QRect, chrome_width: int | None) -> int:
    """Effective width the footer chrome sizes itself against.

    Detailed cards pass a frozen reference width once the card grows past the
    design size, so the badges/text/buttons stop scaling with the tile while
    still anchoring to its real left/right/bottom edges. Everything else
    (immersive) passes ``None`` and keeps sizing to the live card width.
    """
    return card_rect.width() if chrome_width is None else chrome_width


def grid_card_action_rects(
    rect: QRect, *, compact: bool = False, compact_actions: str = "corners", compact_overlay: bool = True
) -> GridCardHitRects:
    """Favorite/reject hit rectangles for a card painted by paint_grid_card.

    Kept in sync with _paint_bottom_overlay / _paint_compact_overlay (which
    use the same internal helpers) so grid hit-testing can ask for the
    rectangles without painting. The compact flags must match the ones given
    to ``paint_grid_card``.
    """
    if rect.width() <= 8 or rect.height() <= 8:
        return GridCardHitRects(QRect(), QRect())
    if compact and not compact_overlay:
        return GridCardHitRects(QRect(), QRect())
    if not compact:
        chrome_scale, design_rect = _detailed_chrome_layout(rect)
        ref_scale = _detailed_reference_scale()
        design_hits = _action_button_rects(
            design_rect, design_rect, ref_scale, chrome_width=DETAILED_CARD_REFERENCE_WIDTH
        )
        return GridCardHitRects(
            _map_detailed_hit_rect(design_hits.favorite, chrome_scale, rect.left(), rect.top()),
            _map_detailed_hit_rect(design_hits.reject, chrome_scale, rect.left(), rect.top()),
        )

    scale = _scale_for(rect, compact)
    pad = _content_pad(scale, compact)
    content_rect = QRect(
        rect.left() + pad,
        rect.top() + pad,
        max(1, rect.width() - pad * 2),
        max(1, rect.height() - pad * 2),
    )
    return _compact_action_button_rects(rect, content_rect, scale, compact_actions)


def _action_button_rects(
    card_rect: QRect, image_rect: QRect, scale: float, chrome_width: int | None = None
) -> GridCardHitRects:
    """Button geometry for the bottom overlay: anchored under the right-side
    position/status stack. Independent of card data so hit-testing and
    painting always agree."""
    basis = _footer_basis_for_width(_chrome_size_width(card_rect, chrome_width))
    margin = max(12, round(14 * scale))
    button = max(1, round(basis * 0.092))
    gap = max(8, round(10 * scale))
    reject_rect = QRect(image_rect.right() - margin - button, 0, button, button)
    favorite_rect = QRect(reject_rect.left() - gap - button, 0, button, button)

    _, _, action_top = _right_stack_vertical_positions(card_rect, scale, chrome_width)
    reject_rect.moveTop(action_top)
    favorite_rect.moveTop(action_top)
    return GridCardHitRects(favorite_rect, reject_rect)


def _compact_corner_inset(scale: float) -> int:
    """One inset shared by all four barebones corner elements (badge chips on
    top, action buttons on the bottom) so the padding reads identical in x
    and y around the card."""
    return max(5, round(10 * scale))


def _compact_action_button_rects(
    card_rect: QRect, image_rect: QRect, scale: float, layout: str = "corners"
) -> GridCardHitRects:
    """Barebones button geometry, inset by the badge chips' corner padding.

    ``layout="corners"`` pins the heart to the bottom-left corner and reject
    to the bottom-right; ``layout="right"`` pairs both buttons side by side
    in the bottom-right corner.
    """
    inset = _compact_corner_inset(scale)
    button = max(14, round(30 * scale))
    top = image_rect.bottom() - inset - button + 1
    reject_rect = QRect(image_rect.right() - inset - button + 1, top, button, button)
    if layout == "right":
        gap = max(5, round(6 * scale))
        favorite_rect = QRect(reject_rect.left() - gap - button, top, button, button)
    else:
        favorite_rect = QRect(image_rect.left() + inset, top, button, button)
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
    *,
    photo_rect: QRect | None = None,
    round_photo: bool = False,
    folder: bool = False,
) -> None:
    path = _rounded_path(QRectF(content_rect), radius)
    if photo_rect is None:
        photo_rect = _photo_rect_for(content_rect)
    painter.save()
    painter.setClipPath(path)

    painter.fillRect(content_rect, QColor(8, 9, 11))
    if source_pixmap is None or source_pixmap.isNull():
        if folder:
            _paint_folder_image(painter, photo_rect)
        else:
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
        if round_photo:
            # Intersect (not replace) so the photo keeps the rounded corners
            # of the outer content clip; a plain setClipRect would replace the
            # rounded path and square the photo off (the ring would round but
            # the image would not).
            painter.setClipRect(photo_rect, Qt.ClipOperation.IntersectClip)
        else:
            painter.setClipRect(photo_rect)
        painter.drawPixmap(draw_rect, source_pixmap)
        painter.restore()

    painter.restore()

    painter.setPen(QPen(QColor(255, 255, 255, 18), 1))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawRoundedRect(QRectF(content_rect).adjusted(0.5, 0.5, -0.5, -0.5), radius, radius)


def _paint_folder_image(painter: QPainter, image_rect: QRect) -> None:
    """Folder tile: the flat folder glyph centered on the dark photo pane."""
    painter.fillRect(image_rect, QColor(17, 18, 20))
    icon_size = max(24, round(min(image_rect.width(), image_rect.height()) * 0.55))
    icon = folder_icon_pixmap(icon_size)
    target = QRect(0, 0, icon_size, icon_size)
    target.moveCenter(image_rect.center())
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    painter.drawPixmap(target, icon, icon.rect())


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


def _metadata_text_top(card_rect: QRect, scale: float, chrome_width: int | None = None) -> int:
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

    basis = _footer_basis_for_width(_chrome_size_width(card_rect, chrome_width))
    text_stack_height = name_h + exif_h + meta_h
    text_block_height = max(text_stack_height, round(basis * 0.1475))
    text_block_bottom = card_rect.bottom() - round(basis * 0.08)
    # name_rect top == position_top in the overlay layout.
    return text_block_bottom - text_block_height + 1


def _right_stack_vertical_positions(
    card_rect: QRect, scale: float, chrome_width: int | None = None
) -> tuple[int, int, int]:
    """Position, status, and action tops for the detailed card's right rail.

    The position text shares the filename baseline. The status and action rows
    retain their existing offsets from it, so the whole rail moves as one unit.
    """

    basis = _footer_basis_for_width(_chrome_size_width(card_rect, chrome_width))
    name_font = QFont("Segoe UI", max(11, round(13 * scale)), QFont.Weight.DemiBold)
    meta_font = QFont("Segoe UI", max(8, round(9 * scale)))
    status_font = QFont("Segoe UI", max(8, round(9 * scale)), QFont.Weight.DemiBold)
    name_metrics = QFontMetrics(name_font)
    meta_metrics = QFontMetrics(meta_font)
    status_metrics = QFontMetrics(status_font)

    filename_baseline = _metadata_text_top(card_rect, scale, chrome_width) + name_metrics.ascent()
    position_top = filename_baseline - status_metrics.ascent()
    side_top = position_top + round(basis * 0.009)
    status_top = (
        side_top
        + meta_metrics.height()
        + max(2, round(3 * scale))
        - round(basis * 0.0114)
    )
    right_text_gap = max(0, status_top - (position_top + meta_metrics.height() - 1))
    action_top = (
        status_top
        + status_metrics.height()
        - 1
        + right_text_gap
        + 1
        + round(basis * 0.008)
    )
    return position_top, status_top, action_top


def _paint_scrim(
    painter: QPainter,
    card_rect: QRect,
    image_rect: QRect,
    radius: float,
    scale: float,
    *,
    light: bool = False,
    chrome_width: int | None = None,
) -> None:
    # Anchor the fade to the text block so the gradient reaches uniformly up to
    # the top of the filename line, then ramps to a solid base under the text.
    h = _footer_basis_for_width(_chrome_size_width(card_rect, chrome_width))
    text_top = _metadata_text_top(card_rect, scale, chrome_width)
    # Fully opaque by the first text line (position/filename) so every line
    # keeps contrast even over white photos; the fade-in starts higher to
    # keep the blend soft.
    fade_top = max(image_rect.top(), text_top - round(0.12 * h))
    solid_top = text_top + round(0.02 * h)
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
        if light:
            # Immersive: the fade stays translucent (65%) so the photo reads
            # through, but the solid band behind the text ramps to 80% so the
            # labels keep a legibility floor on bright content.
            alpha_scale = 0.65 + 0.15 * ease
        else:
            alpha_scale = 1.0
        grad.setColorAt(t, QColor(6, 9, 12, round(255 * ease * alpha_scale)))

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


def _paint_badges(
    painter: QPainter,
    card_rect: QRect,
    image_rect: QRect,
    data: GridCardData,
    scale: float,
    chrome_width: int | None = None,
) -> None:
    size_width = _chrome_size_width(card_rect, chrome_width)
    basis = _footer_basis_for_width(size_width)
    edge_inset = max(12, round(14 * scale))
    font_size = max(8, round(9 * scale))
    font = QFont("Segoe UI", font_size, QFont.Weight.DemiBold)
    badge_height = max(1, round(basis * 0.069))
    duplicate_width = max(1, round(size_width * 0.261))
    ai_width = max(1, round(size_width * 0.161))
    vertical_shift = round(basis * 0.0219)
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

    if data.tags:
        tag_font = QFont("Segoe UI", max(8, round(9 * scale)), QFont.Weight.DemiBold)
        tag_gap = max(5, round(6 * scale))
        rail_y = top
        if data.duplicate_visible and data.duplicate_text:
            rail_y += badge_height + tag_gap
        # Stop above the metadata text block so the rail can never collide
        # with the footer; remaining tags are dropped rather than clipped.
        rail_bottom = _metadata_text_top(card_rect, scale, chrome_width) - tag_gap
        pill_height = _tag_pill_height(tag_font, scale)
        for text, kind in data.tags:
            if not text or rail_y + pill_height > rail_bottom:
                break
            _paint_tag_pill(painter, image_rect.left() + edge_inset, rail_y, text, tag_font, _tag_accent(kind), scale)
            rail_y += pill_height + tag_gap


# Accent colors for AI workflow tags, keyed by GridCardData.tags kind.
# Status kinds reuse the card's status text colors; workflow kinds follow the
# AI activity tag palette so the grid and the AI settings stay in step.
# AI Miss is the only tag the rail draws now (see grid._review_workflow_tags);
# every other accent was for a retired tag and has been removed.
_TAG_ACCENTS: dict[str, tuple[int, int, int]] = {
    "ai_miss": (215, 84, 122),
}


def _tag_accent(kind: str) -> QColor:
    return QColor(*_TAG_ACCENTS.get(kind.strip().casefold(), (198, 208, 222)))


def _tag_pill_height(font: QFont, scale: float) -> int:
    return QFontMetrics(font).height() + max(3, round(5 * scale))


def _paint_tag_pill(
    painter: QPainter, x: int, y: int, text: str, font: QFont, accent: QColor, scale: float
) -> QRect:
    """One workflow tag as a pill in the card badge style: dark translucent
    fill, accent text, subtle accent border."""
    metrics = QFontMetrics(font)
    h_pad = max(6, round(8 * scale))
    rect = QRect(x, y, metrics.horizontalAdvance(text) + h_pad * 2, _tag_pill_height(font, scale))
    radius = max(4.0, round(5 * scale))
    border = QColor(accent)
    border.setAlpha(120)
    painter.save()
    painter.setPen(QPen(border, 1))
    painter.setBrush(QColor(20, 23, 28, 218))
    painter.drawRoundedRect(QRectF(rect), radius, radius)
    painter.setPen(QColor(accent).lighter(112))
    painter.setFont(font)
    painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)
    painter.restore()
    return rect


def _paint_tag_dot(painter: QPainter, x: int, y: int, chip: int, accent: QColor) -> QRect:
    """Icon-only form of a workflow tag: a chip-sized square holding a filled
    dot in the tag's accent color. Used past the column threshold, matching
    the icon-only duplicate/AI chips."""
    rect = QRect(x, y, chip, chip)
    radius = max(4.0, chip * 0.3)
    border = QColor(accent)
    border.setAlpha(130)
    painter.save()
    painter.setPen(QPen(border, 1))
    painter.setBrush(QColor(31, 36, 41, 226))
    painter.drawRoundedRect(QRectF(rect), radius, radius)
    diameter = max(4.0, chip * 0.38)
    dot = QRectF(0, 0, diameter, diameter)
    dot.moveCenter(QRectF(rect).center())
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(accent))
    painter.drawEllipse(dot)
    painter.restore()
    return rect


def _badge_natural_width(text: str, font: QFont, scale: float) -> int:
    """Width _paint_badge produces for a text badge without a fixed size.
    Kept in sync with its padding/icon math so overflow checks agree with
    what actually gets painted."""
    metrics = QFontMetrics(font)
    h_pad = max(8, round(11 * scale))
    icon_width = max(10, round(13 * scale))
    icon_gap = max(6, round(7 * scale))
    return metrics.horizontalAdvance(text) + h_pad * 2 + icon_width + icon_gap


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
    # When a fixed badge is too narrow for legible text, collapse to a clean
    # square icon-only chip instead of hard-clipping the label (which is what
    # produced "Near Duplicate" -> "NP" on small cards).
    text_available = width - (h_pad * 2 + icon_width + icon_gap)
    icon_only = fixed_size is not None and text_available < metrics.averageCharWidth() * 4
    if icon_only:
        width = height
    left = anchor.x() - width if align_right else anchor.x()
    rect = QRect(left, anchor.y(), width, height)
    radius = max(5.0, round(6 * scale))

    painter.setPen(QPen(border, 1))
    painter.setBrush(fill)
    painter.drawRoundedRect(QRectF(rect), radius, radius)

    if icon_only:
        icon_dim = min(icon_width, max(9, round(height * 0.5)))
        icon_rect = QRect(
            rect.center().x() - icon_dim // 2 + 1,
            rect.center().y() - icon_dim // 2,
            icon_dim,
            icon_dim,
        )
    else:
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

    if not icon_only:
        text_rect = rect.adjusted(h_pad + icon_width + icon_gap, 0, -h_pad, 0)
        painter.setPen(text_color)
        elided = metrics.elidedText(text, Qt.TextElideMode.ElideRight, max(0, text_rect.width()))
        painter.drawText(
            text_rect,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            elided,
        )
    painter.restore()
    return rect


def _paint_compact_badges(
    painter: QPainter,
    card_rect: QRect,
    image_rect: QRect,
    data: GridCardData,
    scale: float,
    *,
    show_text: bool = False,
) -> None:
    """Badge chips pinned to the top corners of the barebones card, sharing
    the action buttons' corner inset. Icon-only by default; ``show_text``
    renders them as small icon + text pills instead. AI workflow tags (led by
    the AI bucket, since the compact card has no status text) stack below the
    top-left badge as pills or, in icon-only mode, accent dots."""
    inset = _compact_corner_inset(scale)
    chip = max(11, round(22 * scale))
    top = image_rect.top() + inset

    if show_text:
        # Text pills only when both fit between the insets without touching;
        # otherwise fall back to the icon-only chips so nothing ever clips.
        font = QFont("Segoe UI", max(7, round(8 * scale)), QFont.Weight.DemiBold)
        needed = 0
        if data.duplicate_visible and data.duplicate_text:
            needed += _badge_natural_width(data.duplicate_text, font, scale)
        if data.ai_visible and data.ai_text:
            if needed:
                needed += max(8, round(10 * scale))
            needed += _badge_natural_width(data.ai_text, font, scale)
        if needed > image_rect.width() - inset * 2:
            show_text = False

    tag_entries: list[tuple[str, str]] = []
    if data.status_text:
        tag_entries.append((data.status_text, data.status_kind))
    tag_entries.extend(data.tags)

    def _paint_tag_rail(rail_y: int) -> None:
        if not tag_entries:
            return
        tag_gap = max(4, round(5 * scale))
        rail_y += tag_gap
        rail_x = image_rect.left() + inset
        hit_rects = _compact_action_button_rects(card_rect, image_rect, scale)
        rail_bottom = hit_rects.favorite.top() - tag_gap
        tag_font = QFont("Segoe UI", max(7, round(8 * scale)), QFont.Weight.DemiBold)
        pill_height = _tag_pill_height(tag_font, scale)
        entry_height = pill_height if show_text else chip
        for text, kind in tag_entries:
            if not text or rail_y + entry_height > rail_bottom:
                break
            if show_text:
                _paint_tag_pill(painter, rail_x, rail_y, text, tag_font, _tag_accent(kind), scale)
            else:
                _paint_tag_dot(painter, rail_x, rail_y, chip, _tag_accent(kind))
            rail_y += entry_height + tag_gap

    if show_text:
        font = QFont("Segoe UI", max(7, round(8 * scale)), QFont.Weight.DemiBold)
        badge_height = max(round(24 * scale), QFontMetrics(font).height() + max(7, round(8 * scale)))
        if data.duplicate_visible and data.duplicate_text:
            _paint_badge(
                painter,
                QPoint(image_rect.left() + inset, top),
                data.duplicate_text,
                font,
                QColor(31, 36, 41, 226),
                QColor(255, 197, 73),
                QColor(255, 255, 255, 72),
                icon="duplicate",
                align_right=False,
                scale=scale,
            )
        if data.ai_visible and data.ai_text:
            _paint_badge(
                painter,
                QPoint(image_rect.right() - inset + 1, top),
                data.ai_text,
                font,
                QColor(124, 88, 23, 222),
                QColor(255, 218, 92),
                QColor(255, 178, 37, 214),
                icon="spark",
                align_right=True,
                scale=scale,
            )
        rail_top = top
        if data.duplicate_visible and data.duplicate_text:
            rail_top += badge_height
        _paint_tag_rail(rail_top)
        return

    radius = max(4.0, round(5 * scale))
    icon_size = max(9, round(chip * 0.56))

    def _chip(rect: QRect, fill: QColor, border: QColor) -> QRect:
        painter.setPen(QPen(border, 1))
        painter.setBrush(fill)
        painter.drawRoundedRect(QRectF(rect), radius, radius)
        return QRect(
            rect.left() + round((rect.width() - icon_size) / 2),
            rect.top() + round((rect.height() - icon_size) / 2),
            icon_size,
            icon_size,
        )

    painter.save()
    if data.duplicate_visible and data.duplicate_text:
        icon_rect = _chip(
            QRect(image_rect.left() + inset, top, chip, chip),
            QColor(31, 36, 41, 226),
            QColor(255, 255, 255, 72),
        )
        _paint_duplicate_icon(painter, icon_rect, QColor(255, 197, 73), scale)

    if data.ai_visible and data.ai_text:
        icon_rect = _chip(
            QRect(image_rect.right() - inset - chip + 1, top, chip, chip),
            QColor(124, 88, 23, 222),
            QColor(255, 178, 37, 214),
        )
        _paint_spark_icon(painter, icon_rect, QColor(255, 218, 92))
    painter.restore()

    rail_top = top
    if data.duplicate_visible and data.duplicate_text:
        rail_top += chip
    _paint_tag_rail(rail_top)


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
    chrome_width: int | None = None,
) -> tuple[QRect, QRect]:
    basis = _footer_basis_for_width(_chrome_size_width(card_rect, chrome_width))
    margin = max(12, round(14 * scale))

    hit_rects = _action_button_rects(card_rect, image_rect, scale, chrome_width)
    favorite_rect = QRect(hit_rects.favorite)
    reject_rect = QRect(hit_rects.reject)
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
        round(basis * 0.1475),
    )
    text_block_bottom = card_rect.bottom() - round(basis * 0.08)
    position_top = text_block_bottom - text_block_height + 1

    text_stack_height = name_metrics.height() + exif_metrics.height() + meta_metrics.height()
    line_gap = max(max(5, round(6 * scale)), round((text_block_height - text_stack_height) / 2))
    name_rect = QRect(body_left, position_top, body_width, name_metrics.height())
    exif_rect = QRect(body_left, name_rect.bottom() + line_gap, body_width, exif_metrics.height())
    meta_rect = QRect(body_left, exif_rect.bottom() + line_gap, body_width, meta_metrics.height())

    # The detailed footer sits on the card's anchored scrim, which guarantees
    # a dark backdrop under every line regardless of the photo, so the text
    # colors stay fixed (unlike the scrimless compact card, which samples the
    # photo and flips its filename color).
    painter.save()
    _draw_elided_text(painter, name_rect, data.filename, name_font, QColor(255, 255, 255))
    _draw_elided_text(painter, exif_rect, data.exif_text, exif_font, QColor(204, 214, 226))
    _draw_elided_text(painter, meta_rect, data.meta_text, meta_font, QColor(132, 147, 168))

    side_top, status_top, _ = _right_stack_vertical_positions(card_rect, scale, chrome_width)
    side_rect = QRect(
        side_left,
        side_top,
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
        status_rect = QRect(
            side_left,
            status_top,
            side_width,
            status_height,
        )
        _draw_elided_text(
            painter,
            status_rect,
            data.status_text,
            status_font,
            _status_color(data.status_kind),
            align=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
        )

    if data.show_actions:
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
    else:
        favorite_rect = QRect()
        reject_rect = QRect()
    painter.restore()
    return favorite_rect, reject_rect


_LUMINANCE_CACHE: dict[tuple, float | None] = {}
_LUMINANCE_CACHE_LIMIT = 1024


def _photo_region_luminance(source_pixmap: QPixmap, photo_rect: QRect, region: QRect) -> float | None:
    """Average luminance (0-255) of the photo pixels shown under ``region``,
    memoized per pixmap/geometry since paints repeat during scrolling."""
    key = (
        source_pixmap.cacheKey(),
        photo_rect.width(),
        photo_rect.height(),
        region.left() - photo_rect.left(),
        region.top() - photo_rect.top(),
        region.width(),
        region.height(),
    )
    if key in _LUMINANCE_CACHE:
        return _LUMINANCE_CACHE[key]
    if len(_LUMINANCE_CACHE) >= _LUMINANCE_CACHE_LIMIT:
        _LUMINANCE_CACHE.clear()
    value = _compute_photo_region_luminance(source_pixmap, photo_rect, region)
    _LUMINANCE_CACHE[key] = value
    return value


def _compute_photo_region_luminance(source_pixmap: QPixmap, photo_rect: QRect, region: QRect) -> float | None:
    """Mirrors the crop/center math in ``_paint_image`` so the sampled source
    area matches what is actually painted. Returns None when the region
    falls outside the drawn photo (letterboxing) or cannot be sampled.
    """
    if source_pixmap.isNull() or photo_rect.width() <= 0 or photo_rect.height() <= 0:
        return None
    scaled_size = source_pixmap.size()
    aspect_mode = Qt.AspectRatioMode.KeepAspectRatioByExpanding
    if source_pixmap.height() > source_pixmap.width() * 1.16:
        aspect_mode = Qt.AspectRatioMode.KeepAspectRatio
    scaled_size.scale(photo_rect.size(), aspect_mode)
    if scaled_size.width() <= 0 or scaled_size.height() <= 0:
        return None
    draw_rect = QRect(QPoint(0, 0), scaled_size)
    draw_rect.moveCenter(photo_rect.center())

    sx = source_pixmap.width() / scaled_size.width()
    sy = source_pixmap.height() / scaled_size.height()
    source_region = QRect(
        round((region.left() - draw_rect.left()) * sx),
        round((region.top() - draw_rect.top()) * sy),
        max(1, round(region.width() * sx)),
        max(1, round(region.height() * sy)),
    ).intersected(source_pixmap.rect())
    if source_region.isEmpty():
        return None

    sample = (
        source_pixmap.copy(source_region)
        .scaled(8, 4, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
        .toImage()
    )
    if sample.isNull():
        return None
    total = 0.0
    count = 0
    for y in range(sample.height()):
        for x in range(sample.width()):
            color = sample.pixelColor(x, y)
            total += 0.2126 * color.red() + 0.7152 * color.green() + 0.0722 * color.blue()
            count += 1
    if count == 0:
        return None
    return total / count


def _paint_compact_overlay(
    painter: QPainter,
    card_rect: QRect,
    image_rect: QRect,
    data: GridCardData,
    scale: float,
    layout: str = "corners",
    *,
    show_filename: bool = False,
    source_pixmap: QPixmap | None = None,
) -> tuple[QRect, QRect]:
    """Barebones overlay: the heart and reject buttons along the bottom edge
    (opposite corners or paired bottom-right, per ``layout``). With the
    paired layout, ``show_filename`` draws the filename left-aligned on the
    button row at the shared corner inset, picking light or dark text from
    the photo's luminance under it. No status or position text."""
    hit_rects = _compact_action_button_rects(card_rect, image_rect, scale, layout)
    favorite_rect = QRect(hit_rects.favorite)
    reject_rect = QRect(hit_rects.reject)

    painter.save()
    if show_filename and layout == "right" and data.filename:
        inset = _compact_corner_inset(scale)
        name_font = QFont("Segoe UI", max(9, round(13 * scale)), QFont.Weight.DemiBold)
        name_left = image_rect.left() + inset
        name_width = favorite_rect.left() - max(6, round(8 * scale)) - name_left
        # Skip the filename entirely once the row is too tight for even a few
        # characters — an ellipsis crashing into the buttons reads as clipping.
        if name_width >= round(QFontMetrics(name_font).averageCharWidth() * 4.5):
            name_rect = QRect(name_left, favorite_rect.top(), name_width, favorite_rect.height())
            name_color = QColor(255, 255, 255)
            halo = QColor(10, 13, 18, 160)
            if source_pixmap is not None:
                luminance = _photo_region_luminance(source_pixmap, image_rect, name_rect)
                if luminance is not None and luminance > 148:
                    name_color = QColor(26, 30, 36)
                    halo = QColor(255, 255, 255, 140)
            # Soft opposing halo keeps the text readable on busy midtones,
            # where average luminance alone can't guarantee contrast.
            _draw_elided_text(
                painter, name_rect.translated(1, 1), data.filename, name_font, halo
            )
            _draw_elided_text(painter, name_rect, data.filename, name_font, name_color)

    if data.show_actions:
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
    else:
        favorite_rect = QRect()
        reject_rect = QRect()
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
    idle_fill: QColor | None = None,
    hover_fill: QColor | None = None,
    idle_border: QColor | None = None,
    hover_border: QColor | None = None,
    idle_icon_color: QColor | None = None,
    hover_icon_color: QColor | None = None,
) -> None:
    painter.save()
    fill = QColor(idle_fill) if idle_fill is not None else QColor(21, 24, 30, 214)
    border = QColor(idle_border) if idle_border is not None else QColor(255, 255, 255, 54)
    text_color = QColor(idle_icon_color) if idle_icon_color is not None else QColor(242, 246, 250)
    if hover:
        fill = QColor(hover_fill) if hover_fill is not None else QColor(35, 40, 48, 232)
        border = QColor(hover_border) if hover_border is not None else QColor(255, 255, 255, 88)
        if hover_icon_color is not None:
            text_color = QColor(hover_icon_color)
    if active:
        fill = QColor(active_color)
        fill.setAlpha(60)
        border = QColor(active_color)
        border.setAlpha(178)
        text_color = QColor(active_color)

    painter.setPen(QPen(border, 1))
    painter.setBrush(fill)
    painter.drawEllipse(QRectF(rect))

    image = load_action_icon(
        "heart" if icon == "heart" else "reject",
        (text_color.red(), text_color.green(), text_color.blue()),
    )
    if image is not None:
        _paint_action_icon_image(painter, rect, image)
    elif icon == "heart":
        # Fallback: vector icons when the PNG assets are missing.
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
    "COMPACT_COLUMN_THRESHOLD",
    "DETAILED_CARD_REFERENCE_HEIGHT",
    "DETAILED_CARD_REFERENCE_WIDTH",
    "PLAIN_PHOTO_COLUMN_THRESHOLD",
    "IMAGE_CORNER_RADIUS",
    "grid_card_corner_radius",
    "GridCardData",
    "GridCardHitRects",
    "grid_card_action_rects",
    "grid_card_height_for_width",
    "gallery_card_height_for_width",
    "grid_gallery_action_rects",
    "paint_gallery_card",
    "load_action_icon",
    "paint_grid_card",
    "render_grid_card_pixmap",
]
