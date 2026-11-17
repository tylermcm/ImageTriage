"""Upright for Crop > Transform: Vertical, Horizontal and Straighten solved from lines in the photo.

- **Guided**: the person draws two to four guides on lines that should be vertical or horizontal (each guide is classed by its own
  direction).
- **Auto, Level, Vertical, Full**: the lines are found on the preview (``find_lines``: edge strength and direction, then a Hough
  transform in numpy, near-vertical and near-horizontal only) and given to the same solver.

``solve`` searches the sliders (Vertical, Horizontal, Straighten) that make every vertical line vertical and every horizontal line
horizontal after the warp and the straighten, with a small pull towards zero so a line that says nothing about one slider leaves it
alone. Lines are in the shown frame (after quarter turns and mirrors, before the warp), 0..1 across the picture.
"""
from __future__ import annotations

import math

from . import raw_perspective as rp

MODES = ("off", "auto", "level", "vertical", "full", "guided")
MAX_GUIDES = 4
_FREE = {"level": ("angle",), "vertical": ("angle", "vertical"), "full": ("angle", "vertical", "horizontal"),
         "auto": ("angle", "vertical", "horizontal"), "guided": ("angle", "vertical", "horizontal")}
_LIMIT = {"auto": 60.0}            # Auto keeps corrections moderate, as Lightroom's does


def classify(line) -> str:
    x1, y1, x2, y2 = line
    return "vertical" if abs(y2 - y1) >= abs(x2 - x1) else "horizontal"


def _residual(params: dict, lines, width: float, height: float, weight_pull: float) -> float:
    h = rp._shown_homography({**{k: 0.0 for k in rp.RANGES}, "scale": 100.0, "vertical": params.get("vertical", 0.0),
                              "horizontal": params.get("horizontal", 0.0)}, width, height)
    angle = math.radians(params.get("angle", 0.0))
    cos, sin = math.cos(angle), math.sin(angle)
    total = 0.0
    for line in lines:
        x1, y1, x2, y2 = line
        a = rp.apply(h, x1 * width, y1 * height)
        b = rp.apply(h, x2 * width, y2 * height)
        dx, dy = b[0] - a[0], b[1] - a[1]
        dx, dy = dx * cos - dy * sin, dx * sin + dy * cos            # after the straighten (clockwise for a positive angle)
        length = math.hypot(dx, dy) or 1.0
        off = (dx if classify(line) == "vertical" else dy) / length   # sine of how far it is from upright
        total += off * off
    pull = sum((params.get(k, 0.0) / 100) ** 2 for k in ("vertical", "horizontal")) + (params.get("angle", 0.0) / 45) ** 2
    return total + weight_pull * pull


def solve(lines, width: float, height: float, mode: str = "guided") -> dict:
    """``{"vertical", "horizontal", "angle"}`` for ``lines`` (``[(x1, y1, x2, y2), ...]``, 0..1 in the shown frame)."""
    free = _FREE.get(mode, ())
    result = {"vertical": 0.0, "horizontal": 0.0, "angle": 0.0}
    if not lines or not free:
        return result
    bounds = {"vertical": (-100.0, 100.0), "horizontal": (-100.0, 100.0), "angle": (-45.0, 45.0)}
    limit = _LIMIT.get(mode)
    if limit:
        bounds["vertical"] = bounds["horizontal"] = (-limit, limit)
        bounds["angle"] = (-limit * 0.45, limit * 0.45)
    pull = 0.002 if mode == "guided" else 0.01
    cost = lambda p: _residual(p, lines, width, height, pull)  # noqa: E731
    # coordinate search with shrinking steps: three unknowns at most, smooth cost
    step = {"vertical": 25.0, "horizontal": 25.0, "angle": 8.0}
    best = cost(result)
    for _ in range(60):
        improved = False
        for key in free:
            for direction in (1, -1):
                trial = dict(result)
                low, high = bounds[key]
                trial[key] = max(low, min(high, trial[key] + direction * step[key]))
                value = cost(trial)
                if value < best - 1e-12:
                    result, best, improved = trial, value, True
        if not improved:
            for key in step:
                step[key] /= 2
            if max(step.values()) < 0.02:
                break
    return {k: round(v, 1) for k, v in result.items()}


# ---- finding lines ---------------------------------------------------------------------------------------------------------------
def _gray(image):
    import numpy as np
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QImage

    if max(image.width(), image.height()) > 800:
        image = image.scaled(800, 800, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
    gray = image.convertToFormat(QImage.Format.Format_Grayscale8)
    stride = gray.bytesPerLine()
    data = np.frombuffer(gray.constBits(), dtype=np.uint8, count=stride * gray.height()).reshape(gray.height(), stride)
    return data[:, :gray.width()].astype(np.float32) / 255


def find_lines(image, max_lines: int = 16):
    """Straight lines of the picture (a QImage as shown), near-vertical and near-horizontal only: ``[(x1, y1, x2, y2), ...]`` 0..1,
    the strongest first. Each line runs between the furthest edge points that support it."""
    import numpy as np

    g = _gray(image)
    h, w = g.shape
    gx = np.zeros_like(g)
    gy = np.zeros_like(g)
    gx[:, 1:-1] = g[:, 2:] - g[:, :-2]
    gy[1:-1, :] = g[2:, :] - g[:-2, :]
    magnitude = np.hypot(gx, gy)
    threshold = np.quantile(magnitude, 0.93)
    ys, xs = np.nonzero(magnitude > max(threshold, 0.08))
    if len(xs) < 50:
        return []
    direction = np.arctan2(gy[ys, xs], gx[ys, xs])            # gradient direction: the line runs across it
    lines = []
    diagonal = math.hypot(w, h)
    for family, centre in (("vertical", 0.0), ("horizontal", math.pi / 2)):
        # a vertical line has a horizontal gradient (theta near 0); keep edge points whose gradient fits the family
        thetas = np.radians(np.arange(-35, 35.5, 0.5)) + centre
        keep = np.abs(np.sin(direction - centre)) < math.sin(math.radians(40))
        px, py = xs[keep].astype(np.float32), ys[keep].astype(np.float32)
        if len(px) < 30:
            continue
        rhos = px[:, None] * np.cos(thetas)[None, :] + py[:, None] * np.sin(thetas)[None, :]
        bins = np.round(rhos + diagonal).astype(np.int32)
        size = int(2 * diagonal) + 2
        votes = np.zeros((size, len(thetas)), dtype=np.int32)
        np.add.at(votes, (bins, np.broadcast_to(np.arange(len(thetas)), bins.shape)), 1)
        span = h if family == "vertical" else w
        found = 0
        for _ in range(max_lines * 3):
            r, t = np.unravel_index(int(votes.argmax()), votes.shape)
            if votes[r, t] < max(25, span * 0.12):
                break
            votes[max(0, r - 6):r + 7, max(0, t - 6):t + 7] = 0       # one line, not its neighbours
            on = np.abs(bins[:, t] - r) <= 1
            sx, sy = px[on], py[on]
            if len(sx) < 20:
                continue
            along = (sy if family == "vertical" else sx)
            first, last = along.argmin(), along.argmax()
            if along[last] - along[first] < span * 0.12:
                continue
            lines.append((int(votes.shape[0] and len(sx)), (float(sx[first]) / w, float(sy[first]) / h, float(sx[last]) / w, float(sy[last]) / h)))
            found += 1
            if found >= max_lines:
                break
    lines.sort(key=lambda item: -item[0])
    return [line for _, line in lines[:max_lines * 2]]


def auto_lines(image, mode: str):
    """The lines a mode works from: Level and Vertical lean on the strongest lines, Full and Auto on both families."""
    lines = find_lines(image)
    if mode == "level":
        horizontals = [line for line in lines if classify(line) == "horizontal"]
        return horizontals or lines
    return lines
