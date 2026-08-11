from __future__ import annotations

"""Depth-aware render effects driven by a monocular depth map.

Lens Blur: unlike the Background tool's binary foreground/background cut, blur
here grows with a pixel's distance from a chosen focal plane, so the fall-off is
gradual and reads like a real aperture — the far street dissolves more than the
mid-ground. Built on the Depth Anything map (255 = nearest).
"""

from typing import Literal

import numpy as np
from PIL import Image, ImageFilter

# Slider 0..100 → max circle-of-confusion radius as a fraction of the short edge.
MAX_LENS_BLUR_FRACTION = 0.05
# Discrete blur levels the variable blur interpolates between (0 = sharp).
_BLUR_LEVELS = 4

FocusMode = Literal["near", "far", "custom"]


def lens_blur_radius(amount: float, size: tuple[int, int]) -> float:
    amount = max(0.0, min(100.0, float(amount)))
    if amount <= 0.0:
        return 0.0
    short_edge = max(1, min(int(size[0]), int(size[1])))
    return (amount / 100.0) * short_edge * MAX_LENS_BLUR_FRACTION


def _normalized_depth(depth: np.ndarray) -> np.ndarray:
    d = np.asarray(depth, dtype=np.float32)
    if d.max() > 1.0:
        d = d / 255.0
    return np.clip(d, 0.0, 1.0)


def composite_lens_blur(
    rgb: np.ndarray,
    depth: np.ndarray,
    *,
    amount: float = 60.0,
    focus: float = 1.0,
) -> np.ndarray:
    """Depth-of-field blur. ``depth`` is the map (255 = nearest); ``focus`` is
    the in-focus depth (0 = farthest, 1 = nearest). Pixels at ``focus`` stay
    sharp; blur grows with |depth − focus|.
    """
    max_radius = lens_blur_radius(amount, (rgb.shape[1], rgb.shape[0]))
    if max_radius < 0.5:
        return rgb
    if depth.shape[:2] != rgb.shape[:2]:
        depth = np.asarray(
            Image.fromarray(np.asarray(depth, dtype=np.uint8), mode="L").resize(
                (rgb.shape[1], rgb.shape[0]), Image.Resampling.BILINEAR
            )
        )
    d = _normalized_depth(depth)
    focus = float(max(0.0, min(1.0, focus)))

    # Circle-of-confusion: 0 at the focal plane, 1 at the depth extremes. The
    # denominator keeps the fall-off spanning the whole available depth range on
    # whichever side of the focal plane is deeper.
    span = max(focus, 1.0 - focus, 1e-3)
    coc = np.clip(np.abs(d - focus) / span, 0.0, 1.0)

    source = rgb.astype(np.float32)
    # A small stack of increasingly blurred versions (level 0 = sharp); each
    # pixel samples the level its CoC calls for, interpolating between two.
    radii = [max_radius * (level / (_BLUR_LEVELS - 1)) for level in range(_BLUR_LEVELS)]
    stack = [source]
    for radius in radii[1:]:
        blurred = Image.fromarray(rgb, mode="RGB").filter(ImageFilter.GaussianBlur(radius))
        stack.append(np.asarray(blurred, dtype=np.float32))
    levels = np.stack(stack)  # (L, H, W, 3)

    position = coc * (_BLUR_LEVELS - 1)
    low = np.floor(position).astype(np.intp)
    high = np.minimum(low + 1, _BLUR_LEVELS - 1)
    frac = (position - low).astype(np.float32)[..., None]
    rows, cols = np.indices(coc.shape)
    low_img = levels[low, rows, cols]
    high_img = levels[high, rows, cols]
    out = low_img * (1.0 - frac) + high_img * frac
    return np.clip(out, 0.0, 255.0).astype(np.uint8)


__all__ = ["FocusMode", "lens_blur_radius", "composite_lens_blur"]
