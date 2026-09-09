"""The one geometric relationship between source pixels and what is on screen.

Before crop existed, that relationship was implicit and duplicated: the mask
overlay computed ``label_width / source_width``, ``build_group_strength``
computed ``raster_width / source_width``, and the render backend rasterized
masks at the adjusted image's size while normalizing against the source size.
All three assumed *scale only* — no offset, no rotation, no flip. A crop breaks
all three at once and silently: nothing raises, masks simply land in the wrong
place.

``ViewTransform`` makes the relationship explicit and single-sourced. The
invariant that keeps it honest is that :meth:`ViewTransform.affine` returns the
exact coefficients :func:`apply_view_geometry` hands to ``Image.transform`` —
renderer and overlay cannot disagree, because they read one object.

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
    ``bypass_crop`` is set while the Crop tool is armed, when the viewer is
    shown the whole straightened frame with the crop box drawn over it.
    """

    source_size: tuple[int, int]
    crop: tuple[int, int, int, int] | None = None
    angle: float = 0.0
    flip_h: bool = False
    flip_v: bool = False
    bypass_crop: bool = False

    # -- basics ---------------------------------------------------------------

    @property
    def effective_crop(self) -> tuple[int, int, int, int] | None:
        return None if self.bypass_crop else self.crop

    def is_identity(self) -> bool:
        return self.effective_crop is None and not self.angle and not self.flip_h and not self.flip_v

    def frame_size(self) -> tuple[int, int]:
        """Size of the rendered frame, in pixels."""
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

    # -- mapping --------------------------------------------------------------

    def _parts(self) -> tuple[float, float, float, float, float, float]:
        """(cos, sin, source centre x/y, frame centre x/y) for the mapping."""
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
        frame_w, frame_h = self.frame_size()
        return cos, sin, centre_x, centre_y, frame_w / 2.0, frame_h / 2.0

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
        return rx + fx, ry + fy

    def frame_to_source(self, x: float, y: float) -> tuple[float, float]:
        """Frame pixel -> source pixel (the exact inverse)."""
        cos, sin, cx, cy, fx, fy = self._parts()
        rx, ry = float(x) - fx, float(y) - fy
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
        cos, sin, cx, cy, fx, fy = self._parts()
        sx = -1.0 if self.flip_h else 1.0
        sy = -1.0 if self.flip_v else 1.0
        a = cos * sx
        b = -sin * sy
        c = cx - a * fx - b * fy
        d = sin * sx
        e = cos * sy
        f = cy - d * fx - e * fy
        return (a, b, c, d, e, f)

    def qtransform(self) -> QTransform:
        """Source px -> frame px, for painting masks in frame space."""
        cos, sin, cx, cy, fx, fy = self._parts()
        sx = -1.0 if self.flip_h else 1.0
        sy = -1.0 if self.flip_v else 1.0
        # QTransform's constructor takes (m11, m12, m21, m22, dx, dy) in
        # row-vector convention: x' = m11*x + m21*y + dx.
        m11 = cos * sx
        m21 = sin * sx
        m12 = -sin * sy
        m22 = cos * sy
        dx = fx - m11 * cx - m21 * cy
        dy = fy - m12 * cx - m22 * cy
        return QTransform(m11, m12, m21, m22, dx, dy)

    # -- cache key ------------------------------------------------------------

    def freeze(self) -> tuple:
        """Hashable identity, for render and strength-field cache keys."""
        crop = self.effective_crop
        return (
            tuple(int(v) for v in self.source_size),
            tuple(int(v) for v in crop) if crop is not None else None,
            round(float(self.angle), 4),
            bool(self.flip_h),
            bool(self.flip_v),
        )


def view_transform_for(
    recipe: Any, source_size: tuple[int, int], *, bypass_crop: bool = False
) -> ViewTransform:
    """Build the transform a recipe describes."""
    crop = getattr(recipe, "crop", None)
    return ViewTransform(
        source_size=(int(source_size[0]), int(source_size[1])),
        crop=tuple(int(v) for v in crop) if crop else None,
        angle=float(getattr(recipe, "crop_angle", 0.0) or 0.0),
        flip_h=bool(getattr(recipe, "flip_h", False)),
        flip_v=bool(getattr(recipe, "flip_v", False)),
        bypass_crop=bool(bypass_crop),
    )
