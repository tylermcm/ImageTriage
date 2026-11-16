"""Auto tone and auto white balance: the editor's slider settings worked out from the picture, with no UI and no model.

The picture is the editor's own preview (sRGB display pixels, with the edits so far applied), so everything here is a *change* to
the current slider values: ask again after an edit and it asks for a smaller change. Nothing is learned; these are rules, kept
cautious on purpose: they should never make a photo worse, and they leave a photo alone that is already right.

Rules, in short:

* **Exposure** moves the median to mid-grey (18 % linear), at most two stops and never so far that the brightest 0.5 % clips.
  A photo that is meant to be dark or bright is only nudged, not flattened to grey.
* **Highlights / shadows** are pulled back when a good share of the picture sits near white / black.
* **Whites / blacks** are set so about 0.2 % of the picture clips at each end.
* **Contrast** follows the spread between the 5th and 95th percentile; **vibrance** lifts a flat colour picture.
* **White balance** looks only at near-neutral, unclipped pixels, and acts only on a clear cast: a lake or a forest is not a cast.
"""
from __future__ import annotations

import math

TONE_KEYS = ("exposure", "contrast", "highlights", "shadows", "whites", "blacks", "vibrance")
WB_KEYS = ("temperature", "tint")
MAX_SIDE = 640                     # the analysis is on a small copy: its answer does not depend on the size
MID_GREY = 0.18                    # linear
MAX_STOPS = 2.0


def _small(rgb):
    import numpy as np
    from PIL import Image

    image = Image.fromarray(np.asarray(rgb, np.uint8))
    scale = MAX_SIDE / max(image.size)
    if scale < 1:
        image = image.resize((max(1, round(image.width * scale)), max(1, round(image.height * scale))), Image.Resampling.BOX)
    return np.asarray(image, np.float32) / 255.0


def _linear(values):
    import numpy as np

    return np.where(values <= 0.04045, values / 12.92, ((values + 0.055) / 1.055) ** 2.4)


def _luma(values):
    return values[..., 0] * 0.2126 + values[..., 1] * 0.7152 + values[..., 2] * 0.0722


def _clip(value, low, high):
    return max(low, min(high, value))


def measure(rgb) -> dict:
    """What the rules look at: percentiles of the display brightness, the median in linear light, colourfulness."""
    import numpy as np

    pic = _small(rgb)
    luma = _luma(pic)
    pct = np.percentile(luma, [0.2, 2, 5, 10, 50, 90, 95, 98, 99.5, 99.8])
    lin = _luma(_linear(pic))
    chroma = pic.max(axis=-1) - pic.min(axis=-1)
    return {
        "p0_2": float(pct[0]), "p2": float(pct[1]), "p5": float(pct[2]), "p10": float(pct[3]), "p50": float(pct[4]),
        "p90": float(pct[5]), "p95": float(pct[6]), "p98": float(pct[7]), "p99_5": float(pct[8]), "p99_8": float(pct[9]),
        "median_linear": float(np.median(lin)), "top_linear": float(np.percentile(lin, 99.5)),
        "saturation": float(np.mean(chroma / np.maximum(pic.max(axis=-1), 1e-3))),
        "clipped_high": float(np.mean(pic.max(axis=-1) >= 0.995)), "clipped_low": float(np.mean(pic.max(axis=-1) <= 0.01)),
    }


def tone(rgb, current: dict | None = None, strength: float = 1.0) -> dict:
    """New values for ``TONE_KEYS``: the current value plus the change the picture asks for, scaled by ``strength`` (0..1).
    Only keys that change are returned."""
    current = current or {}
    m = measure(rgb)
    s = _clip(strength, 0.0, 1.0)
    delta: dict[str, float] = {}

    # exposure: median to mid-grey, damped (a dark scene is allowed to stay dark), never into clipping
    wanted = math.log2(MID_GREY / max(m["median_linear"], 1e-4))
    stops = _clip(wanted * 0.75, -MAX_STOPS, MAX_STOPS)
    if stops > 0:
        headroom = math.log2(0.985 / max(m["top_linear"], 1e-4))        # how far the top can rise before it clips
        stops = _clip(min(stops, max(0.0, headroom)), 0.0, MAX_STOPS)
    if abs(stops) >= 0.05:
        delta["exposure"] = stops

    # the tonal sliders, from where the picture sits after that exposure change (about +0.18 in display value per stop)
    lift = 0.0 if "exposure" not in delta else 0.12 * delta["exposure"]
    p10, p95, p98 = m["p10"] + lift, min(1.0, m["p95"] + lift), min(1.0, m["p98"] + lift)
    highlights = -_clip((p98 - 0.86) / 0.14, 0.0, 1.0) * 60 - _clip(m["clipped_high"] * 400, 0.0, 25)
    shadows = _clip((0.22 - p10) / 0.22, 0.0, 1.0) * 60
    if abs(highlights) >= 2:
        delta["highlights"] = highlights
    if shadows >= 2:
        delta["shadows"] = shadows

    # whites and blacks: end points so that about 0.2 % clips
    top = min(1.0, m["p99_8"] + lift)
    low = max(0.0, m["p0_2"] + lift * 0.5)
    whites = _clip((0.985 - top) * 220, -30.0, 40.0)
    blacks = -_clip((low - 0.025) * 400, 0.0, 40.0)
    if abs(whites) >= 3:
        delta["whites"] = whites
    if blacks <= -3:
        delta["blacks"] = blacks

    # contrast follows the spread of the body of the picture; flat pictures get some, harsh ones less
    spread = p95 - m["p5"]
    contrast = _clip((0.62 - spread) / 0.3, -1.0, 1.0) * 30
    if abs(contrast) >= 3:
        delta["contrast"] = contrast
    vibrance = _clip((0.32 - m["saturation"]) / 0.32, 0.0, 1.0) * 25 + 5
    delta["vibrance"] = vibrance * (1.0 if m["saturation"] < 0.5 else 0.0)

    out = {}
    for key, change in delta.items():
        if not change:
            continue
        low_, high_ = (-5.0, 5.0) if key == "exposure" else (-100.0, 100.0)
        value = _clip(float(current.get(key, 0) or 0) + change * s, low_, high_)
        if abs(value - float(current.get(key, 0) or 0)) >= (0.02 if key == "exposure" else 0.5):
            out[key] = round(value, 2) if key == "exposure" else round(value)
    return out


def _neutral_estimate(rgb):
    """The colour of the near-neutral, unclipped pixels (linear light), or None when there are too few to say anything."""
    import numpy as np

    pic = _small(rgb)
    lin = _linear(pic)
    brightness = pic.max(axis=-1)
    chroma = (pic.max(axis=-1) - pic.min(axis=-1)) / np.maximum(brightness, 1e-3)
    usable = (brightness > 0.12) & (brightness < 0.93) & (chroma < 0.30)
    if usable.mean() < 0.03:
        return None
    pixels = lin[usable]
    # shades of grey (Minkowski norm 6): between "average" and "brightest", the usual compromise
    return np.power(np.mean(np.power(pixels, 6), axis=0), 1 / 6)


def white_balance(rgb, current: dict | None = None, strength: float = 1.0) -> dict:
    """New ``temperature`` and ``tint`` when the picture has a clear colour cast, else ``{}``. The same temperature and tint
    arithmetic the white-balance dropper uses (``raw_editor._white_balance_from_image``), as a change to the current values."""
    current = current or {}
    estimate = _neutral_estimate(rgb)
    if estimate is None:
        return {}
    r, g, b = (float(v) for v in estimate)
    if min(r, g, b) <= 1e-6:
        return {}
    temperature = 100 * (b - r) / (0.35 * (r + b))
    tint = 400 * (1 - (2 * r * b / (r + b)) / g)
    # the dropper's numbers describe a neutral swatch; for a whole picture only a clear cast counts, and only part of it is taken
    out = {}
    s = _clip(strength, 0.0, 1.0)
    for key, value in (("temperature", temperature), ("tint", tint)):
        if abs(value) < 6:
            continue
        step = _clip((abs(value) - 6) * 0.6, 0.0, 30.0) * (1 if value > 0 else -1)
        now = float(current.get(key, 0) or 0)
        new = _clip(now + step * s, -100.0, 100.0)
        if abs(new - now) >= 1:
            out[key] = round(new)
    return out


def auto(rgb, current: dict | None = None, strength: float = 1.0) -> dict:
    """Tone and white balance together."""
    return {**white_balance(rgb, current, strength), **tone(rgb, current, strength)}
