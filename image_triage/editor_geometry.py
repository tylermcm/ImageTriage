"""The one geometric relationship between source pixels and what is on screen.

Before crop existed, that relationship was implicit and duplicated: the mask
overlay computed ``label_width / source_width``, ``build_group_strength``
computed ``raster_width / source_width``, and the render backend rasterized
masks at the adjusted image's size while normalizing against the source size.
All three assumed *scale only* — no offset, no rotation, no flip. A crop breaks
all three at once and silently: nothing raises, masks simply land in the wrong
place.

``ViewTransform`` makes the relationship explicit and single-sourced. The
straighten affine is shared with :func:`apply_view_geometry`; the legacy
quarter-turn remains the renderer's immediately following step and is composed
into the default display transform so renderer and overlay describe the same
final frame.

With no crop, no angle and no flip the transform is the identity and every
caller behaves exactly as it did before. That is deliberate: it is what makes
the migration safe.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any

from PySide6.QtGui import QTransform


@dataclass(frozen=True)
class ViewTransform:
    """Maps ``space-source-full`` pixels to the frame the viewer actually sees.

    ``crop`` is ``(left, top, right, bottom)`` in *straightened* source pixels;
    ``angle`` is the straighten in degrees (positive rotates the image
    counter-clockwise, so the horizon tips clockwise, matching Lightroom).
    ``rotate`` is the legacy post-crop quarter-turn.
    ``bypass_crop`` is set while the Crop tool is armed, when the viewer is
    shown the whole straightened frame with the crop box drawn over it.
    """

    source_size: tuple[int, int]
    crop: tuple[int, int, int, int] | None = None
    angle: float = 0.0
    # Legacy quarter-turn rotation is applied after straighten/crop by the
    # renderer. It still belongs in the display transform so overlays see the
    # same final orientation and frame dimensions as the pixels.
    rotate: float = 0.0
    flip_h: bool = False
    flip_v: bool = False
    bypass_crop: bool = False

    # -- basics ---------------------------------------------------------------

    @property
    def effective_crop(self) -> tuple[int, int, int, int] | None:
        return None if self.bypass_crop else self.crop

    def is_identity(self) -> bool:
        return (
            self.effective_crop is None
            and not self.angle
            and not self.rotate
            and not self.flip_h
            and not self.flip_v
        )

    def _base_frame_size(self) -> tuple[int, int]:
        """Frame before the renderer's final legacy rotation."""
        crop = self.effective_crop
        if crop is not None:
            left, top, right, bottom = crop
            return max(1, int(right) - int(left)), max(1, int(bottom) - int(top))
        if self.angle:
            # Straighten without a crop keeps the whole rotated bounding box, so
            # nothing is thrown away before the user has drawn a box.
            width, height = self.source_size
            radians = math.radians(self.angle)
            cos, sin = abs(math.cos(radians)), abs(math.sin(radians))
            return (
                max(1, int(round(width * cos + height * sin))),
                max(1, int(round(width * sin + height * cos))),
            )
        return (max(1, int(self.source_size[0])), max(1, int(self.source_size[1])))

    def frame_size(self) -> tuple[int, int]:
        """Size of the final rendered frame, including a quarter turn."""
        width, height = self._base_frame_size()
        if not self.rotate:
            return width, height
        radians = math.radians(self.rotate)
        cos, sin = abs(math.cos(radians)), abs(math.sin(radians))
        return (
            max(1, int(round(width * cos + height * sin))),
            max(1, int(round(width * sin + height * cos))),
        )

    # -- mapping --------------------------------------------------------------

    def _parts(self) -> tuple[float, float, float, float, float, float]:
        """Straighten parts before the final legacy rotation."""
        radians = math.radians(self.angle)
        cos, sin = math.cos(radians), math.sin(radians)
        source_w, source_h = self.source_size
        crop = self.effective_crop
        if crop is not None:
            centre_x = (crop[0] + crop[2]) / 2.0
            centre_y = (crop[1] + crop[3]) / 2.0
        else:
            centre_x = source_w / 2.0
            centre_y = source_h / 2.0
        frame_w, frame_h = self._base_frame_size()
        return cos, sin, centre_x, centre_y, frame_w / 2.0, frame_h / 2.0

    def _base_to_frame(self, x: float, y: float) -> tuple[float, float]:
        """Post-rotate a point from the straighten frame into the display."""
        if not self.rotate:
            return float(x), float(y)
        base_w, base_h = self._base_frame_size()
        frame_w, frame_h = self.frame_size()
        radians = math.radians(self.rotate)
        cos, sin = math.cos(radians), math.sin(radians)
        dx, dy = float(x) - base_w / 2.0, float(y) - base_h / 2.0
        return (
            dx * cos + dy * sin + frame_w / 2.0,
            -dx * sin + dy * cos + frame_h / 2.0,
        )

    def _frame_to_base(self, x: float, y: float) -> tuple[float, float]:
        """Undo the renderer's final rotation."""
        if not self.rotate:
            return float(x), float(y)
        base_w, base_h = self._base_frame_size()
        frame_w, frame_h = self.frame_size()
        radians = math.radians(self.rotate)
        cos, sin = math.cos(radians), math.sin(radians)
        rx, ry = float(x) - frame_w / 2.0, float(y) - frame_h / 2.0
        return (
            rx * cos - ry * sin + base_w / 2.0,
            rx * sin + ry * cos + base_h / 2.0,
        )

    def source_to_frame(self, x: float, y: float) -> tuple[float, float]:
        """Source pixel -> frame pixel."""
        cos, sin, cx, cy, fx, fy = self._parts()
        dx, dy = float(x) - cx, float(y) - cy
        # The straighten rotates the *image*, so a source point moves by the
        # inverse rotation into the straightened frame.
        rx = dx * cos + dy * sin
        ry = -dx * sin + dy * cos
        if self.flip_h:
            rx = -rx
        if self.flip_v:
            ry = -ry
        return self._base_to_frame(rx + fx, ry + fy)

    def frame_to_source(self, x: float, y: float) -> tuple[float, float]:
        """Frame pixel -> source pixel (the exact inverse)."""
        cos, sin, cx, cy, fx, fy = self._parts()
        base_x, base_y = self._frame_to_base(x, y)
        rx, ry = base_x - fx, base_y - fy
        if self.flip_h:
            rx = -rx
        if self.flip_v:
            ry = -ry
        dx = rx * cos - ry * sin
        dy = rx * sin + ry * cos
        return dx + cx, dy + cy

    def affine(self) -> tuple[float, float, float, float, float, float]:
        """PIL ``Image.Transform.AFFINE`` coefficients: frame px -> source px.

        PIL's affine is the *inverse* map (for each output pixel it asks which
        input pixel to sample), which is exactly ``frame_to_source``.
        """
        x0, y0 = self.frame_to_source(0.0, 0.0)
        x1, y1 = self.frame_to_source(1.0, 0.0)
        x2, y2 = self.frame_to_source(0.0, 1.0)
        return (x1 - x0, x2 - x0, x0, y1 - y0, y2 - y0, y0)

    def qtransform(self) -> QTransform:
        """Source px -> frame px, for painting masks in frame space."""
        x0, y0 = self.source_to_frame(0.0, 0.0)
        x1, y1 = self.source_to_frame(1.0, 0.0)
        x2, y2 = self.source_to_frame(0.0, 1.0)
        # QTransform's constructor takes (m11, m12, m21, m22, dx, dy) in
        # row-vector convention: x' = m11*x + m21*y + dx.
        return QTransform(
            x1 - x0,
            y1 - y0,
            x2 - x0,
            y2 - y0,
            x0,
            y0,
        )

    def swaps_axes(self) -> bool:
        """Whether the final rotation is an odd quarter turn."""
        quarter = round(float(self.rotate) / 90.0)
        return (
            abs(float(self.rotate) - quarter * 90.0) <= 1e-6
            and int(quarter) % 2 != 0
        )

    def _display_rect_size(self, width: float, height: float) -> tuple[float, float]:
        if self.swaps_axes():
            return float(height), float(width)
        return float(width), float(height)

    def _stored_rect_size(self, width: float, height: float) -> tuple[float, float]:
        # Quarter-turn swapping is its own inverse.
        return self._display_rect_size(width, height)

    # -- crop geometry --------------------------------------------------------
    # These read the transform as the Crop tool uses it: no effective crop, so
    # the frame is the whole straightened bounding box and the source image is
    # a rotated quad inside it.

    def image_quad(self) -> tuple[tuple[float, float], ...]:
        """The source rectangle's corners, in frame pixels.

        With a straighten in play this is a rotated quad inside the frame's
        bounding box. The wedges outside it are blank, not photo, so a crop
        must stay inside *this* — clamping to the frame is what let the box be
        dragged out into the black corners.
        """
        width, height = float(self.source_size[0]), float(self.source_size[1])
        corners = ((0.0, 0.0), (width, 0.0), (width, height), (0.0, height))
        return tuple(self.source_to_frame(x, y) for x, y in corners)

    def frame_rect_for_crop(
        self, crop: tuple[float, float, float, float]
    ) -> tuple[float, float, float, float]:
        """Stored crop -> the axis-aligned rectangle the viewer sees.

        A stored crop carries its centre in source pixels and its size in frame
        pixels (that is what :meth:`_parts` reads back). So the rectangle is
        centred on the mapped centre and keeps the stored size: mapping the two
        opposite corners instead is what made the box change shape — and drift
        off its locked ratio — as the straighten turned.
        """
        left, top, right, bottom = (float(v) for v in crop)
        centre_x, centre_y = self.source_to_frame((left + right) / 2.0, (top + bottom) / 2.0)
        width, height = self._display_rect_size(right - left, bottom - top)
        half_w, half_h = width / 2.0, height / 2.0
        return (centre_x - half_w, centre_y - half_h, centre_x + half_w, centre_y + half_h)

    def crop_for_frame_rect(
        self, rect: tuple[float, float, float, float]
    ) -> tuple[float, float, float, float]:
        """The inverse of :meth:`frame_rect_for_crop`."""
        left, top, right, bottom = (float(v) for v in rect)
        centre_x, centre_y = self.frame_to_source((left + right) / 2.0, (top + bottom) / 2.0)
        width, height = self._stored_rect_size(right - left, bottom - top)
        half_w, half_h = width / 2.0, height / 2.0
        return (centre_x - half_w, centre_y - half_h, centre_x + half_w, centre_y + half_h)

    # -- cache key ------------------------------------------------------------

    def freeze(self) -> tuple:
        """Hashable identity, for render and strength-field cache keys."""
        crop = self.effective_crop
        return (
            tuple(int(v) for v in self.source_size),
            tuple(int(v) for v in crop) if crop is not None else None,
            round(float(self.angle), 4),
            round(float(self.rotate), 4),
            bool(self.flip_h),
            bool(self.flip_v),
        )


def view_transform_for(
    recipe: Any,
    source_size: tuple[int, int],
    *,
    bypass_crop: bool = False,
    include_rotate: bool = True,
) -> ViewTransform:
    """Build the transform a recipe describes."""
    crop = getattr(recipe, "crop", None)
    return ViewTransform(
        source_size=(int(source_size[0]), int(source_size[1])),
        crop=tuple(int(v) for v in crop) if crop else None,
        angle=float(getattr(recipe, "crop_angle", 0.0) or 0.0),
        rotate=(float(getattr(recipe, "rotate", 0.0) or 0.0) if include_rotate else 0.0),
        flip_h=bool(getattr(recipe, "flip_h", False)),
        flip_v=bool(getattr(recipe, "flip_v", False)),
        bypass_crop=bool(bypass_crop),
    )


# -- keeping a crop inside the photo ------------------------------------------
# A straightened photo is a rotated quad, so "inside the image" is not a
# rectangle test. These work for moves, edge drags, corner drags and locked
# ratios alike, which is why the overlay does not hand-roll edge maths.

Quad = tuple[tuple[float, float], ...]
Rect = tuple[float, float, float, float]


def quad_contains(quad: Quad, x: float, y: float, *, tolerance: float = 1e-7) -> bool:
    """Is the point inside the convex quad (a rotated rectangle always is)?"""
    sign = 0.0
    for index in range(4):
        ax, ay = quad[index]
        bx, by = quad[(index + 1) % 4]
        cross = (bx - ax) * (y - ay) - (by - ay) * (x - ax)
        if abs(cross) <= tolerance:
            continue  # on an edge counts as inside
        if sign == 0.0:
            sign = 1.0 if cross > 0 else -1.0
        elif (cross > 0.0) != (sign > 0.0):
            return False
    return True


def rect_in_quad(quad: Quad, rect: Rect) -> bool:
    left, top, right, bottom = (float(v) for v in rect)
    return all(
        quad_contains(quad, x, y)
        for x, y in ((left, top), (right, top), (right, bottom), (left, bottom))
    )


def _lerp_rect(start: Rect, end: Rect, t: float) -> Rect:
    return tuple(s + (e - s) * t for s, e in zip(start, end))  # type: ignore[return-value]


def _quad_centroid(quad: Quad) -> tuple[float, float]:
    return (
        sum(point[0] for point in quad) / 4.0,
        sum(point[1] for point in quad) / 4.0,
    )


def _point_inside(quad: Quad, x: float, y: float, *, steps: int = 24) -> tuple[float, float]:
    """``(x, y)`` if it is in the quad, else the closest point to it along the
    line to the quad's centre."""
    if quad_contains(quad, x, y):
        return x, y
    cx, cy = _quad_centroid(quad)
    low, high = 0.0, 1.0
    for _ in range(steps):
        mid = (low + high) / 2.0
        if quad_contains(quad, cx + (x - cx) * mid, cy + (y - cy) * mid):
            low = mid
        else:
            high = mid
    return cx + (x - cx) * low, cy + (y - cy) * low


def inset_quad(quad: Quad, pixels: float = 1.0) -> Quad:
    """Pull the quad in by about ``pixels``.

    A crop is fitted against the photo and then stored as whole pixels, and the
    fit converges exactly onto the edge — so without a hair of slack the
    rounding can put a corner just outside the photo again.
    """
    centre_x, centre_y = _quad_centroid(quad)
    side_a = math.dist(quad[0], quad[1])
    side_b = math.dist(quad[1], quad[2])
    half = max(1e-6, min(side_a, side_b) / 2.0)
    scale = max(0.0, 1.0 - pixels / half)
    return tuple(
        (centre_x + (x - centre_x) * scale, centre_y + (y - centre_y) * scale)
        for x, y in quad
    )


def fit_rect_in_quad(quad: Quad, rect: Rect, *, steps: int = 24) -> Rect:
    """Shrink ``rect`` about its centre until it fits inside ``quad``.

    This is what a straighten does to a crop that would otherwise hang over the
    rotated photo's edge: the box keeps its centre and its ratio and gives up
    size, the way Lightroom does.
    """
    if rect_in_quad(quad, rect):
        return tuple(float(v) for v in rect)  # type: ignore[return-value]
    left, top, right, bottom = (float(v) for v in rect)
    centre_x, centre_y = _point_inside(quad, (left + right) / 2.0, (top + bottom) / 2.0)
    # Re-centre first, so a box whose centre drifted outside still has an
    # anchor to shrink towards.
    half_w, half_h = (right - left) / 2.0, (bottom - top) / 2.0
    target = (centre_x - half_w, centre_y - half_h, centre_x + half_w, centre_y + half_h)
    degenerate = (centre_x, centre_y, centre_x, centre_y)
    low, high = 0.0, 1.0
    for _ in range(steps):
        mid = (low + high) / 2.0
        if rect_in_quad(quad, _lerp_rect(degenerate, target, mid)):
            low = mid
        else:
            high = mid
    return _lerp_rect(degenerate, target, low)


def limit_rect_to_quad(quad: Quad, start: Rect, desired: Rect, *, steps: int = 24) -> Rect:
    """The furthest rectangle between ``start`` and ``desired`` that still fits.

    Bisection rather than per-edge clamping: validity is monotone along the
    interpolation for a convex quad, so one rule covers every drag mode and
    never lets a ratio-locked box escape while an edge is being pushed.
    """
    if rect_in_quad(quad, desired):
        return tuple(float(v) for v in desired)  # type: ignore[return-value]
    if not rect_in_quad(quad, start):
        return fit_rect_in_quad(quad, desired)
    low, high = 0.0, 1.0
    for _ in range(steps):
        mid = (low + high) / 2.0
        if rect_in_quad(quad, _lerp_rect(start, desired, mid)):
            low = mid
        else:
            high = mid
    return _lerp_rect(start, desired, low)
