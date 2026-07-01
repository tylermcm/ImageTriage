"""Focus-based point-of-interest (PoI) for the "Smart Focus" preview.

Finds where to zoom for detail inspection by locating the in-focus region — the
subject. Photographers focus on the subject, so in shallow depth-of-field the
sharp area *is* the subject, while the soft background scores low. In deep DoF
(landscape, everything sharp) the focus map is flat and we return "full frame"
rather than inventing a crop.

Pure NumPy (reuses the Laplacian from technical.py), ~milliseconds on a thumbnail,
no model and no new dependency. Output is a normalized bbox the UI maps to any
size; the recommendation is a *non-destructive* highlight (click to zoom), not an
auto-crop — so context is preserved for landscape/architecture.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .technical import _LAPLACIAN_KERNEL, _conv3, _to_gray


@dataclass(slots=True)
class PoiResult:
    bbox: tuple[float, float, float, float] | None  # normalized (x0,y0,x1,y1) or None
    confidence: float  # 0-1: how concentrated the focus is
    is_full_frame: bool  # True == no clear subject (deep DoF / flat), use whole frame


_SUBJECT_FOCUS_PROFILES = frozenset(
    {
        "event_stage",
        "macro_detail",
        "product_still_life",
        "sports_action",
        "street_documentary",
        "vehicle_transport",
        "wildlife",
    }
)

_FULL_FRAME_PROFILES = frozenset(
    {
        "aerial_drone",
        "abstract_texture",
        "architecture",
        "interior_space",
        "landscape",
        "night_astro",
        "travel_built",
        "water_coastal",
    }
)


def _pool_grid(arr: np.ndarray, grid: int) -> np.ndarray:
    """Average ``arr`` into a grid x grid map (robust to any input size)."""
    h, w = arr.shape
    y_edges = np.linspace(0, h, grid + 1).astype(int)
    x_edges = np.linspace(0, w, grid + 1).astype(int)
    out = np.zeros((grid, grid), dtype=np.float64)
    for i in range(grid):
        for j in range(grid):
            tile = arr[y_edges[i]: y_edges[i + 1], x_edges[j]: x_edges[j + 1]]
            if tile.size:
                out[i, j] = float(tile.mean())
    return out


def _center_gaussian(grid: int, sigma_frac: float = 0.45) -> np.ndarray:
    axis = np.linspace(-1.0, 1.0, grid)
    xx, yy = np.meshgrid(axis, axis)
    return np.exp(-(xx ** 2 + yy ** 2) / (2.0 * sigma_frac ** 2))


def focus_poi(
    image_bgr: np.ndarray,
    *,
    grid: int = 24,
    center_bias: float = 0.25,
    peak_fraction: float = 0.55,
    spread_limit: float = 0.40,
) -> PoiResult:
    """Locate the in-focus subject; return its normalized bbox or full-frame.

    - ``center_bias``: gentle Gaussian prior toward the frame center (tie-breaker,
      keeps the box off irrelevant corners).
    - ``peak_fraction``: a tile is "in-focus" if its weighted focus is within this
      fraction of the peak.
    - ``spread_limit``: if more than this fraction of tiles are in-focus, focus is
      spread (deep DoF) -> full frame.
    """
    gray = _to_gray(image_bgr)
    if gray.size == 0 or gray.shape[0] < 3 or gray.shape[1] < 3:
        return PoiResult(None, 0.0, True)

    laplacian = _conv3(gray, _LAPLACIAN_KERNEL)
    energy = _pool_grid(laplacian * laplacian, grid)
    peak = float(energy.max())
    if peak <= 0:
        return PoiResult(None, 0.0, True)  # uniformly flat / no detail

    normalized = energy / peak
    weighted = normalized * (1.0 - center_bias + center_bias * _center_gaussian(grid))
    weighted /= float(weighted.max()) + 1e-9

    mask = weighted >= peak_fraction
    in_focus_fraction = float(mask.mean())
    if in_focus_fraction == 0.0 or in_focus_fraction >= spread_limit:
        # Spread focus (deep DoF) or nothing concentrated -> use the whole frame.
        confidence = max(0.0, 1.0 - in_focus_fraction / spread_limit)
        return PoiResult(None, confidence, True)

    ys, xs = np.where(mask)
    x0 = max(0, int(xs.min()) - 1) / grid
    x1 = min(grid, int(xs.max()) + 2) / grid
    y0 = max(0, int(ys.min()) - 1) / grid
    y1 = min(grid, int(ys.max()) + 2) / grid
    confidence = float(1.0 - in_focus_fraction / spread_limit)
    return PoiResult((float(x0), float(y0), float(x1), float(y1)), confidence, False)


def should_use_smart_focus_crop(category_profile: str, result: PoiResult) -> bool:
    """Decide whether a PoI result should replace the full-frame preview.

    Focus PoI is useful for subject-forward images, but scenic images often have
    sharp foreground texture that is not the composition's subject. Keep those
    full-frame by default. For uncategorized images, require the crop to look
    like a centered subject rather than an edge or foreground texture grab.
    """
    if result.is_full_frame or result.bbox is None:
        return False

    profile = str(category_profile or "").strip().lower()
    if profile in _FULL_FRAME_PROFILES:
        return False
    if profile in _SUBJECT_FOCUS_PROFILES:
        return True

    x0, y0, x1, y1 = result.bbox
    width = max(0.0, float(x1) - float(x0))
    height = max(0.0, float(y1) - float(y0))
    center_x = (float(x0) + float(x1)) / 2.0
    center_y = (float(y0) + float(y1)) / 2.0
    if result.confidence < 0.45:
        return False
    if max(width, height) > 0.55:
        return False
    if not (0.20 <= center_x <= 0.80):
        return False
    if not (0.12 <= center_y <= 0.68):
        return False
    if y1 > 0.92 and center_y > 0.55:
        return False
    return True
