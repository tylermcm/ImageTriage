"""Perspective correction (Crop tab > Transform), applied to the developed picture before the crop rectangle.

The picture is treated as a plane seen by a camera whose focal length is the picture's long side. Vertical tilts that plane about
the horizontal axis and Horizontal about the vertical one (``MAX_TILT`` degrees at 100); Aspect stretches across, Scale zooms and
the offsets move it. The sliders are as the person sees the picture: the warp is built in the shown frame (after the quarter turns
and mirrors) and carried back to the picture as recorded, so Vertical always works on lines that are vertical on screen.

``homography`` gives the 3x3 matrix (column vectors) from the picture as recorded to the warped picture, both in pixels of the
recorded frame; ``warp`` draws it; ``warped_to_recorded`` maps a point back (for the pickers).
"""
from __future__ import annotations

import math

KEYS = {"vertical": 0.0, "horizontal": 0.0, "aspect": 0.0, "scale": 100.0, "offsetX": 0.0, "offsetY": 0.0, "constrain": True}
RANGES = {"vertical": (-100, 100), "horizontal": (-100, 100), "aspect": (-100, 100), "scale": (50, 150), "offsetX": (-100, 100),
          "offsetY": (-100, 100)}
MAX_TILT = 25.0            # degrees of tilt at 100
FILL = (255, 255, 255)     # what shows where the warped picture leaves the frame (Lightroom shows white)


EXTRA = ("upright", "guides")      # how Upright set the sliders (``raw_upright``); stored beside them


def valid_values(crop: dict) -> bool:
    from .raw_upright import MAX_GUIDES, MODES

    if crop.get("upright", "off") not in MODES:
        return False
    guides = crop.get("guides", [])
    if not isinstance(guides, list) or len(guides) > MAX_GUIDES or not all(
            isinstance(g, list) and len(g) == 4 and all(type(v) in (int, float) and math.isfinite(v) and -0.5 <= v <= 1.5 for v in g)
            for g in guides):
        return False
    for key, (low, high) in RANGES.items():
        value = crop.get(key, KEYS[key])
        if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
            return False
    return type(crop.get("constrain", True)) is bool


def active(crop: dict) -> bool:
    return any(abs(float(crop.get(k, KEYS[k])) - KEYS[k]) > 1e-7 for k in RANGES)


# ---- 3x3 matrices (lists of rows) ------------------------------------------------------------------------------------------------
def _mul(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)] for i in range(3)]


def _inv(m):
    a, b, c = m[0]
    d, e, f = m[1]
    g, h, i = m[2]
    det = a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g)
    if abs(det) < 1e-18:
        raise ValueError("singular transform")
    return [[(e * i - f * h) / det, (c * h - b * i) / det, (b * f - c * e) / det],
            [(f * g - d * i) / det, (a * i - c * g) / det, (c * d - a * f) / det],
            [(d * h - e * g) / det, (b * g - a * h) / det, (a * e - b * d) / det]]


def apply(m, x: float, y: float) -> tuple[float, float]:
    w = m[2][0] * x + m[2][1] * y + m[2][2]
    if abs(w) < 1e-12:
        w = 1e-12
    return (m[0][0] * x + m[0][1] * y + m[0][2]) / w, (m[1][0] * x + m[1][1] * y + m[1][2]) / w


def _translate(tx, ty):
    return [[1, 0, tx], [0, 1, ty], [0, 0, 1]]


def _scale(sx, sy):
    return [[sx, 0, 0], [0, sy, 0], [0, 0, 1]]


def _shown_frame(crop: dict, width: float, height: float):
    """``(M, shown width, shown height)``: M maps recorded pixels to shown pixels (the quarter turn, then the mirrors)."""
    rotate = crop.get("rotate", 0)
    sw, sh = (height, width) if rotate in (90, 270) else (width, height)
    turn = {0: [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
            90: [[0, -1, height], [1, 0, 0], [0, 0, 1]],
            180: [[-1, 0, width], [0, -1, height], [0, 0, 1]],
            270: [[0, 1, 0], [-1, 0, width], [0, 0, 1]]}[rotate]
    flip = [[-1 if crop.get("flipH") else 1, 0, sw if crop.get("flipH") else 0],
            [0, -1 if crop.get("flipV") else 1, sh if crop.get("flipV") else 0], [0, 0, 1]]
    return _mul(flip, turn), sw, sh


def _tilt(vertical: float, horizontal: float, width: float, height: float):
    """The camera-turn homography in shown pixels, keeping the centre in place and its local scale 1."""
    f = max(width, height)
    cx, cy = width / 2, height / 2
    # negative Vertical widens the top (it corrects buildings leaning back), as in Lightroom
    tv, th = math.radians(-vertical / 100 * MAX_TILT), math.radians(horizontal / 100 * MAX_TILT)
    rx = [[1, 0, 0], [0, math.cos(tv), -math.sin(tv)], [0, math.sin(tv), math.cos(tv)]]
    ry = [[math.cos(th), 0, math.sin(th)], [0, 1, 0], [-math.sin(th), 0, math.cos(th)]]
    k = [[f, 0, cx], [0, f, cy], [0, 0, 1]]
    h = _mul(_mul(k, _mul(rx, ry)), _inv(k))
    mx, my = apply(h, cx, cy)
    h = _mul(_translate(cx - mx, cy - my), h)
    # local scale at the centre (Jacobian determinant), so a tilt neither zooms in nor out there
    e = 1.0
    ax, ay = apply(h, cx + e, cy)
    bx, by = apply(h, cx, cy + e)
    det = abs((ax - cx) * (by - cy) - (ay - cy) * (bx - cx))
    s = 1 / math.sqrt(det) if det > 1e-12 else 1.0
    return _mul(_mul(_translate(cx, cy), _mul(_scale(s, s), _translate(-cx, -cy))), h)


def _shown_homography(p: dict, width: float, height: float, zoom: float = 1.0):
    cx, cy = width / 2, height / 2
    stretch = math.exp(p["aspect"] / 100 * 0.4)
    size = p["scale"] / 100 * zoom
    around = lambda m: _mul(_mul(_translate(cx, cy), m), _translate(-cx, -cy))  # noqa: E731
    h = _tilt(p["vertical"], p["horizontal"], width, height)
    h = _mul(around(_scale(stretch * size, size / stretch)), h)
    return _mul(_translate(p["offsetX"] / 100 * width / 2, p["offsetY"] / 100 * height / 2), h)


def _covers(h, width: float, height: float) -> bool:
    """Whether the warped picture fills the whole frame: every corner of the frame comes from inside the picture."""
    inverse = _inv(h)
    for x, y in ((0, 0), (width, 0), (0, height), (width, height)):
        sx, sy = apply(inverse, x, y)
        if not (-1e-6 <= sx <= width + 1e-6 and -1e-6 <= sy <= height + 1e-6):
            return False
    return True


def homography(crop: dict, width: float, height: float):
    """Recorded pixels -> warped pixels for a ``width`` x ``height`` picture."""
    p = {k: float(crop.get(k, KEYS[k])) for k in RANGES}
    m, sw, sh = _shown_frame(crop, width, height)
    h = _shown_homography(p, sw, sh)
    if crop.get("constrain", True) and not _covers(h, sw, sh):
        low, high = 1.0, 1.0
        while not _covers(_shown_homography(p, sw, sh, high), sw, sh) and high < 64:
            high *= 1.5
        for _ in range(40):
            mid = (low + high) / 2
            if _covers(_shown_homography(p, sw, sh, mid), sw, sh):
                high = mid
            else:
                low = mid
        h = _shown_homography(p, sw, sh, high)
    return _mul(_mul(_inv(m), h), m)


def shown_homography(crop: dict, width: float, height: float):
    """``(H, shown width, shown height)``: the warp in the shown frame (after quarter turns and mirrors), for a recorded
    ``width`` x ``height`` picture. Points of the crop tool's view are in this frame (0..1 times the shown size)."""
    m, sw, sh = _shown_frame(crop, width, height)
    return _mul(_mul(m, homography(crop, width, height)), _inv(m)), sw, sh


def warped_to_recorded(u: float, v: float, crop: dict, shape: tuple[float, float]) -> tuple[float, float]:
    """A point of the warped picture (0..1) in the picture as recorded (0..1)."""
    if not active(crop):
        return u, v
    width, height = shape
    x, y = apply(_inv(homography(crop, width, height)), u * width, v * height)
    return x / width, y / height


def warp(image, crop: dict):
    """``image`` (the picture as recorded) warped, same size and pixel format; what leaves the frame shows ``FILL``."""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor, QImage, QPainter, QTransform

    if image.isNull() or not active(crop):
        return image
    h = homography(crop, image.width(), image.height())
    kind = image.format()
    deep = image.depth() > 32
    work = QImage(image.size(), QImage.Format.Format_RGBA64 if deep else QImage.Format.Format_ARGB32)
    work.fill(QColor(*FILL))
    painter = QPainter(work)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setTransform(QTransform(h[0][0], h[1][0], h[2][0], h[0][1], h[1][1], h[2][1], h[0][2], h[1][2], h[2][2]))
    painter.drawImage(0, 0, image)
    painter.end()
    if image.colorSpace().isValid():
        work.setColorSpace(image.colorSpace())
    return work if work.format() == kind else work.convertToFormat(kind)


def query(crop: dict) -> str:
    """The address part that asks the image provider for the warp ("" when there is none)."""
    if not active(crop):
        return ""
    values = [crop.get(k, KEYS[k]) for k in RANGES]
    return "persp=" + ",".join(repr(float(v)) for v in values) + f",{int(bool(crop.get('constrain', True)))},{int(crop.get('rotate', 0))}," \
        f"{int(bool(crop.get('flipH')))},{int(bool(crop.get('flipV')))}"


def from_query(text: str) -> dict | None:
    try:
        parts = text.split(",")
        values = dict(zip(RANGES, (float(v) for v in parts[:6])))
        values.update(constrain=parts[6] == "1", rotate=int(parts[7]), flipH=parts[8] == "1", flipV=parts[9] == "1")
    except (ValueError, IndexError):
        return None
    return values if valid_values(values) and values["rotate"] in (0, 90, 180, 270) else None
