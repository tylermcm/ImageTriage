from __future__ import annotations

"""Background compositing driven by a BiRefNet subject matte.

The first editor tool whose AI output is a *rendered edit*, not a selection:
given the foreground matte, blur / replace / remove what's behind the subject.

The blur is alpha-weighted — the background is blurred with the foreground
excluded and renormalized — so subject colours don't bleed into the bokeh the
way a naive whole-image blur produces a bright halo around the cut-out edge.
"""

from typing import Literal

import numpy as np
from PIL import Image, ImageFilter

BackgroundMode = Literal["off", "blur", "color", "remove"]

# The PNG-style transparency checkerboard shown where the background is removed.
CHECKER_LIGHT = (200, 200, 200)
CHECKER_DARK = (150, 150, 150)

# A slider of 0..100 maps to a Gaussian radius up to this fraction of the
# image's short edge, so the effect reads the same on any resolution.
MAX_BLUR_FRACTION = 0.045
MIN_BLUR_RADIUS = 0.6


def blur_radius_for(amount: float, size: tuple[int, int]) -> float:
    """Slider amount (0..100) → Gaussian radius in pixels for ``size``."""
    amount = max(0.0, min(100.0, float(amount)))
    if amount <= 0.0:
        return 0.0
    short_edge = max(1, min(int(size[0]), int(size[1])))
    return MIN_BLUR_RADIUS + (amount / 100.0) * short_edge * MAX_BLUR_FRACTION


def _alpha_weighted_blur(rgb: np.ndarray, fg_alpha: np.ndarray, radius: float) -> np.ndarray:
    """Blur the background with the foreground excluded, then renormalize.

    ``fg_alpha`` is the foreground coverage in 0..1. Zeroing the foreground
    before the blur and dividing by the blurred background-weight keeps subject
    colour out of the bokeh (no bright halo at the matte edge)."""
    bg_weight = np.clip(1.0 - fg_alpha, 0.0, 1.0)
    height, width = bg_weight.shape
    premult = rgb.astype(np.float32) * bg_weight[..., None]

    def _blur(channel: np.ndarray) -> np.ndarray:
        img = Image.fromarray(
            np.clip(channel, 0.0, 255.0).astype(np.uint8), mode="L"
        ).filter(ImageFilter.GaussianBlur(radius))
        return np.asarray(img, dtype=np.float32)

    blurred = np.stack([_blur(premult[..., c]) for c in range(3)], axis=-1)
    weight = np.asarray(
        Image.fromarray((bg_weight * 255.0).astype(np.uint8), mode="L").filter(
            ImageFilter.GaussianBlur(radius)
        ),
        dtype=np.float32,
    ) / 255.0
    safe = np.maximum(weight, 1e-3)[..., None]
    background = blurred / safe
    # Where almost no background weight exists (deep inside the subject), the
    # renormalized value is meaningless — fall back to a plain blur of the
    # original so those pixels stay sane before compositing.
    plain = np.stack(
        [
            np.asarray(
                Image.fromarray(rgb[..., c], mode="L").filter(
                    ImageFilter.GaussianBlur(radius)
                ),
                dtype=np.float32,
            )
            for c in range(3)
        ],
        axis=-1,
    )
    thin = (weight < 0.05)[..., None]
    return np.where(thin, plain, background)


def _checkerboard(height: int, width: int) -> np.ndarray:
    """The classic transparency checker, sized so squares stay visible when the
    image is fit to the screen (proportional to the short edge)."""
    square = max(8, min(height, width) // 40)
    ys = (np.arange(height) // square)[:, None]
    xs = (np.arange(width) // square)[None, :]
    dark = ((ys + xs) % 2).astype(bool)
    board = np.empty((height, width, 3), dtype=np.float32)
    board[:] = np.asarray(CHECKER_LIGHT, dtype=np.float32)
    board[dark] = np.asarray(CHECKER_DARK, dtype=np.float32)
    return board


def _parse_color(color: str | tuple[int, int, int]) -> np.ndarray:
    if isinstance(color, (tuple, list)) and len(color) >= 3:
        return np.asarray([int(color[0]), int(color[1]), int(color[2])], dtype=np.float32)
    text = str(color).strip().lstrip("#")
    if len(text) == 3:
        text = "".join(ch * 2 for ch in text)
    if len(text) != 6:
        return np.asarray([0.0, 0.0, 0.0], dtype=np.float32)
    return np.asarray([int(text[i : i + 2], 16) for i in (0, 2, 4)], dtype=np.float32)


def composite_background(
    rgb: np.ndarray,
    matte: np.ndarray,
    *,
    mode: BackgroundMode = "blur",
    amount: float = 60.0,
    color: str | tuple[int, int, int] = "#000000",
) -> np.ndarray:
    """Composite the foreground (via ``matte``) over a blurred/replaced background.

    ``rgb`` is HxWx3 uint8, ``matte`` is HxW foreground coverage (uint8 or
    float, any range that normalizes to 0..1). Returns HxWx3 uint8.
    """
    if mode == "off":
        return rgb
    fg = np.asarray(matte, dtype=np.float32)
    if fg.max() > 1.0:
        fg = fg / 255.0
    fg = np.clip(fg, 0.0, 1.0)
    source = rgb.astype(np.float32)

    if mode == "blur":
        radius = blur_radius_for(amount, (rgb.shape[1], rgb.shape[0]))
        if radius <= 0.0:
            return rgb
        background = _alpha_weighted_blur(rgb, fg, radius)
    elif mode == "color":
        background = np.empty_like(source)
        background[:] = _parse_color(color)[None, None, :]
    elif mode == "remove":
        background = _checkerboard(rgb.shape[0], rgb.shape[1])
    else:
        return rgb

    a = fg[..., None]
    out = source * a + background * (1.0 - a)
    return np.clip(out, 0.0, 255.0).astype(np.uint8)


__all__ = [
    "BackgroundMode",
    "blur_radius_for",
    "composite_background",
]
