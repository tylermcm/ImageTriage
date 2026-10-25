"""The inspector's histograms: the real brightness and red, green and blue distribution of the selected photo.

The numbers are the Widgets app's own (`review_tools.build_histogram_stats`, the same one the popout's inspection card draws, and its
`histogram_synopsis` sentence). A photo is read the way the grid reads it (`imaging.load_image_for_display`, the embedded preview for a RAW, at most
512 px on the long edge for the counting), on one worker thread that skips photos the person has already moved past, so flipping through a folder never
queues work. The 256 bins are folded into 64 and scaled to the tallest bin that is not pressed against an edge, so a clipped spike at 0 or 255 does not
flatten everything else. `histograms.get(path)` is what QML reads; `ready(path)` says a photo's numbers have arrived (photo_jobs.py).
"""
from __future__ import annotations

from PySide6.QtCore import QObject, QSize

from .photo_jobs import LatestPhotoJobs

BINS = 64
CACHE_SIZE = 96


def compute(path: str) -> dict | None:
    """The histogram of one photo as {"luma": [...], "r": [...], "g": [...], "b": [...], "summary": str} with values 0..1, or None when it cannot be read.
    Slow (decodes the photo): only ever called from a worker thread."""
    import numpy as np

    from ..imaging import load_image_for_display
    from ..review_tools import build_histogram_stats, histogram_synopsis

    image, _error = load_image_for_display(path, QSize(512, 512), prefer_embedded=True)
    if image.isNull():
        return None
    stats = build_histogram_stats(image)
    if stats.width <= 0:
        return None

    def fold(values) -> "np.ndarray":
        return np.asarray(values, dtype=np.float64).reshape(BINS, 256 // BINS).sum(axis=1)

    channels = {"luma": fold(stats.histogram_luma), "r": fold(stats.histogram_red), "g": fold(stats.histogram_green), "b": fold(stats.histogram_blue)}
    # the red, green and blue share one scale so their heights can be compared; the brightness has its own
    scales = {"luma": _peak(channels["luma"])}
    colour_peak = max(_peak(channels[key]) for key in ("r", "g", "b"))
    scales.update(r=colour_peak, g=colour_peak, b=colour_peak)
    out: dict = {key: [round(min(1.0, float(v) / scales[key]), 3) for v in channels[key]] for key in channels}
    out["summary"] = histogram_synopsis(stats)
    return out


def _peak(bins) -> float:
    """The tallest bin, not counting the two at the ends (pure black and pure white pile up there and would flatten the rest)."""
    inner = bins[1:-1] if len(bins) > 2 else bins
    return max(float(inner.max()), 1.0)


class HistogramService(LatestPhotoJobs):
    """`histograms.get(path)` and `request(path)` for QML (see photo_jobs.py); the loader is `compute` unless a test gives another."""

    def __init__(self, loader=None, parent: QObject | None = None) -> None:
        super().__init__(loader or compute, parent, CACHE_SIZE)
