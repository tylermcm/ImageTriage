"""The inspector's Quality checks: focus, exposure, noise and motion blur measured on the selected photo itself.

The measurements are the Widgets app's own (`review_tools.build_inspection_stats`, the numbers its inspector reads), taken on the same kind of picture it
uses (the photo read for display at up to 1200 px, the embedded preview for a RAW), on one worker thread that skips photos the person has already moved past.
The wording follows `ui.inspector_quality.quality_readout`, and the focus and exposure cut-offs are the ones `ai_why` pins for the grid's tooltip, so the
shell and the Widgets app call the same photo the same thing. `inspection.get(path)` is what QML reads; `ready(path)` says a photo's checks have arrived.

Each check is {"name", "text", "value", "tone"}: `value` is 0..100 with higher better (noise and blur are turned round), `tone` is 0 good, 1 take a look,
2 poor. `reasons` is the same findings as sentences, {"good", "head", "detail"}, which the AI tab lists beside the AI's own reasons.
"""
from __future__ import annotations

from PySide6.QtCore import QObject, QSize

from ..ai_why import ACCEPTABLE_MIN, EXPOSURE_BALANCED_MIN, SHARP_MIN
from .photo_jobs import LatestPhotoJobs

CACHE_SIZE = 96
READ_EDGE = 1200                # the Widgets inspector measures its 1200 px preview
MIN_TEXTURED_TILES = 4          # fewer textured regions than this and focus cannot be judged (as in the Widgets readout)
NOISE_HIGH, NOISE_MODERATE = 65.0, 32.0
BLUR_WEAK, BLUR_POSSIBLE = 40.0, 60.0
HIGHLIGHT_CLIP_PCT, SHADOW_CLIP_PCT = 2.0, 4.0
BRIGHT_MEDIAN, DARK_MEDIAN = 190.0, 55.0


def _clip(value: float) -> int:
    return int(round(max(0.0, min(100.0, value))))


def _balance(stats) -> float:
    """0..100, 100 - clipping - distance from mid grey: the score `review_intelligence` calls exposure balance."""
    clipping = min(38.0, (stats.shadow_clip_pct + stats.highlight_clip_pct) * 1.7)
    return 100.0 - clipping - abs(stats.mean_luminance - 128.0) / 2.4 - abs(stats.median_luminance - 128.0) / 2.8


def _check(name: str, text: str, value: float, tone: int) -> dict:
    return {"name": name, "text": text, "value": _clip(value), "tone": tone}


def _reason(good: bool, head: str, detail: str = "") -> dict:
    return {"good": good, "head": head, "detail": detail}


def measure(stats) -> dict:
    """The checks and reasons for one photo's `InspectionStats`."""
    checks, reasons = [], []

    if stats.detail_valid_tile_count < MIN_TEXTURED_TILES:
        checks.append(_check("Focus", "Inconclusive", 0, 1))
        reasons.append(_reason(False, "Focus can't be judged.", "Too few textured regions to measure."))
    else:
        score = stats.detail_score
        if score >= SHARP_MIN:
            checks.append(_check("Focus", "Sharp", score, 0))
            reasons.append(_reason(True, "Subject is sharp.", f"Detail score {score:.0f} of 100."))
        elif score >= ACCEPTABLE_MIN:
            checks.append(_check("Focus", "Slightly soft", score, 1))
            reasons.append(_reason(False, "Focus is a little soft.", f"Detail score {score:.0f} of 100."))
        else:
            checks.append(_check("Focus", "Soft", score, 2))
            reasons.append(_reason(False, "Focus looks soft.", f"Detail score {score:.0f} of 100; look closer at 100%."))

    balance = _balance(stats)
    if stats.highlight_clip_pct >= HIGHLIGHT_CLIP_PCT:
        checks.append(_check("Exposure", "Highlights clipped", balance, 2 if stats.highlight_clip_pct >= 10 else 1))
        reasons.append(_reason(False, "Highlights are clipped.", f"{stats.highlight_clip_pct:.1f}% of the frame is blown out."))
    elif stats.shadow_clip_pct >= SHADOW_CLIP_PCT:
        checks.append(_check("Exposure", "Shadows clipped", balance, 2 if stats.shadow_clip_pct >= 15 else 1))
        reasons.append(_reason(False, "Shadows are clipped.", f"{stats.shadow_clip_pct:.1f}% of the frame is crushed to black."))
    elif stats.median_luminance >= BRIGHT_MEDIAN:
        checks.append(_check("Exposure", "Bright", balance, 1))
        reasons.append(_reason(False, "Exposure runs bright.", "Highlights are close to clipping."))
    elif stats.median_luminance <= DARK_MEDIAN:
        checks.append(_check("Exposure", "Dark", balance, 1))
        reasons.append(_reason(False, "Exposure runs dark.", "Shadow detail may be hidden."))
    else:
        checks.append(_check("Exposure", "Balanced", balance, 0 if balance >= EXPOSURE_BALANCED_MIN else 1))
        reasons.append(_reason(balance >= EXPOSURE_BALANCED_MIN, "Balanced exposure.", "Highlights and shadows are well protected."))

    noise = stats.noise_score
    if noise >= NOISE_HIGH:
        checks.append(_check("Noise", "High", 100 - noise, 2))
        reasons.append(_reason(False, "Noise is visible.", "Fine-grained variation across flat areas."))
    elif noise >= NOISE_MODERATE:
        checks.append(_check("Noise", "Moderate", 100 - noise, 1))
        reasons.append(_reason(False, "Some noise.", "A little fine-grained variation in flat areas."))
    else:
        checks.append(_check("Noise", "Low", 100 - noise, 0))

    blur = stats.motion_blur_score
    if blur >= BLUR_POSSIBLE:
        checks.append(_check("Motion blur", "Possible blur", 100 - blur, 2))
        reasons.append(_reason(False, "Possible motion blur.", "Directional softness suggests movement."))
    elif blur >= BLUR_WEAK:
        checks.append(_check("Motion blur", "Weak signal", 100 - blur, 1))
    else:
        checks.append(_check("Motion blur", "No strong signal", 100 - blur, 0))

    return {"checks": checks, "reasons": reasons}


def compute(path: str) -> dict | None:
    """The Quality checks of one photo, or None when it cannot be read. Slow (decodes the photo): only ever called from a worker thread."""
    from ..imaging import load_image_for_display
    from ..review_tools import build_inspection_stats, histogram_synopsis

    image, _error = load_image_for_display(path, QSize(READ_EDGE, READ_EDGE), prefer_embedded=True)
    if image.isNull():
        return None
    stats = build_inspection_stats(image)
    if stats.width <= 0:
        return None
    return dict(measure(stats), summary=histogram_synopsis(stats))


class InspectionService(LatestPhotoJobs):
    """`inspection.get(path)` and `request(path)` for QML (see photo_jobs.py); the loader is `compute` unless a test gives another."""

    def __init__(self, loader=None, parent: QObject | None = None) -> None:
        super().__init__(loader or compute, parent, CACHE_SIZE)
