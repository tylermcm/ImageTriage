"""Classical-CV technical quality dimensions (FACET formula parity, NumPy-only).

These are deterministic, model-free, and folder-independent — the day-one
"reject" signal. Each returns a 0-10 score (higher = better) except
``monochrome`` which is a bool. Inputs are uint8 images in OpenCV BGR channel
order (HxWx3) or grayscale (HxW); arrays decouple this module from image
loading.

Formula references (FACET github.com/ncoevoet/facet, analyzers/technical.py):
- sharpness:   Laplacian variance / 50, capped at 10 (high-ISO log boost)
- exposure:    clipped-pixel fractions (<=5 shadows, >=250 highlights)
- dyn. range:  log2(p98 / p2) in stops
- noise:       Immerkaer 3x3 Laplacian estimator (inverted to a score)
- contrast:    (p95-p5)/255 * 5 + RMS/255 * 20, capped at 10
- color:       Shannon entropy of the HSV (H,S) histogram, max ~15.5 bits
- monochrome:  mean saturation below threshold
"""

from __future__ import annotations

import math

import numpy as np

from .model import DimensionScores

# Tunable normalization constants. Kept here, named, so they can be calibrated
# against FACET / real data later (see plan Phase 3).
_SHARPNESS_DIVISOR = 50.0
_EXPOSURE_CLIP_PENALTY = 10.0
_DR_MAX_STOPS = 8.0
_NOISE_MAX_SIGMA = 12.0
_COLOR_MAX_ENTROPY = 15.5
_MONOCHROME_SAT_THRESHOLD = 0.1

_IMMERKAER_KERNEL = np.array([[1.0, -2.0, 1.0], [-2.0, 4.0, -2.0], [1.0, -2.0, 1.0]])
_LAPLACIAN_KERNEL = np.array([[0.0, 1.0, 0.0], [1.0, -4.0, 1.0], [0.0, 1.0, 0.0]])


def _to_gray(image: np.ndarray) -> np.ndarray:
    """BGR uint8 -> float64 luma (BT.601, the OpenCV default), 0..255."""
    arr = np.asarray(image)
    if arr.ndim == 2:
        return arr.astype(np.float64)
    b = arr[..., 0].astype(np.float64)
    g = arr[..., 1].astype(np.float64)
    r = arr[..., 2].astype(np.float64)
    return 0.114 * b + 0.587 * g + 0.299 * r


def _conv3(a: np.ndarray, kernel: np.ndarray) -> np.ndarray:
    padded = np.pad(a, 1, mode="reflect")
    out = np.zeros_like(a, dtype=np.float64)
    h, w = a.shape
    for di in range(3):
        for dj in range(3):
            out += kernel[di, dj] * padded[di : di + h, dj : dj + w]
    return out


def _bgr_to_hsv(image: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return (H, S, V) in OpenCV ranges: H in [0,180), S and V in [0,255]."""
    arr = np.asarray(image).astype(np.float64) / 255.0
    b, g, r = arr[..., 0], arr[..., 1], arr[..., 2]
    mx = np.max(arr, axis=-1)
    mn = np.min(arr, axis=-1)
    diff = mx - mn

    hue = np.zeros_like(mx)
    safe = diff != 0
    # Red max
    rm = safe & (mx == r)
    hue[rm] = (60.0 * ((g[rm] - b[rm]) / diff[rm]) + 360.0) % 360.0
    # Green max
    gm = safe & (mx == g) & ~rm
    hue[gm] = 60.0 * ((b[gm] - r[gm]) / diff[gm]) + 120.0
    # Blue max
    bm = safe & (mx == b) & ~rm & ~gm
    hue[bm] = 60.0 * ((r[bm] - g[bm]) / diff[bm]) + 240.0

    sat = np.where(mx == 0, 0.0, diff / np.where(mx == 0, 1.0, mx))
    return hue / 2.0, sat * 255.0, mx * 255.0


def sharpness_score(gray: np.ndarray, *, iso: float | None = None) -> float:
    g = _to_gray(gray)
    variance = float(_conv3(g, _LAPLACIAN_KERNEL).var())
    score = variance / _SHARPNESS_DIVISOR
    if iso is not None and iso > 1600:
        # High-ISO images are inherently softer; gently boost so noise-reduced
        # but acceptably sharp shots are not over-penalized.
        score *= 1.0 + 0.35 * math.log2(iso / 1600.0)
    return float(min(10.0, max(0.0, score)))


def exposure_score(gray: np.ndarray) -> float:
    g = _to_gray(gray)
    total = g.size
    shadow_frac = float(np.count_nonzero(g <= 5) / total)
    highlight_frac = float(np.count_nonzero(g >= 250) / total)
    clipped = shadow_frac + highlight_frac
    return float(max(0.0, 10.0 - clipped * _EXPOSURE_CLIP_PENALTY * 10.0))


def dynamic_range_score(gray: np.ndarray) -> float:
    g = _to_gray(gray)
    p2, p98 = np.percentile(g, [2.0, 98.0])
    stops = math.log2((p98 + 1.0) / (p2 + 1.0))
    return float(min(10.0, max(0.0, stops / _DR_MAX_STOPS * 10.0)))


def noise_score(gray: np.ndarray) -> float:
    g = _to_gray(gray)
    h, w = g.shape
    if h < 3 or w < 3:
        return 10.0
    conv = _conv3(g, _IMMERKAER_KERNEL)
    sigma = float(np.sum(np.abs(conv)) * math.sqrt(0.5 * math.pi) / (6.0 * (w - 2) * (h - 2)))
    return float(min(10.0, max(0.0, 10.0 * (1.0 - min(1.0, sigma / _NOISE_MAX_SIGMA)))))


def contrast_score(gray: np.ndarray) -> float:
    g = _to_gray(gray)
    p5, p95 = np.percentile(g, [5.0, 95.0])
    percentile_range = (p95 - p5) / 255.0
    rms = float(np.std(g)) / 255.0
    return float(min(10.0, max(0.0, percentile_range * 5.0 + rms * 20.0)))


def color_harmony_score(image: np.ndarray) -> float:
    arr = np.asarray(image)
    if arr.ndim == 2:
        return 0.0
    hue, sat, _ = _bgr_to_hsv(arr)
    hist, _, _ = np.histogram2d(
        hue.ravel(), sat.ravel(), bins=[180, 256], range=[[0, 180], [0, 256]]
    )
    total = hist.sum()
    if total <= 0:
        return 0.0
    probs = hist[hist > 0] / total
    entropy = float(-np.sum(probs * np.log2(probs)))
    return float(min(10.0, max(0.0, entropy / _COLOR_MAX_ENTROPY * 10.0)))


def is_monochrome(image: np.ndarray) -> bool:
    arr = np.asarray(image)
    if arr.ndim == 2:
        return True
    _, sat, _ = _bgr_to_hsv(arr)
    return bool(float(np.mean(sat)) / 255.0 < _MONOCHROME_SAT_THRESHOLD)


def analyze_technical(image: np.ndarray, *, iso: float | None = None) -> DimensionScores:
    """Compute all Phase-1 classical dimensions for a BGR uint8 image."""
    gray = _to_gray(image)
    return DimensionScores(
        sharpness=sharpness_score(gray, iso=iso),
        exposure=exposure_score(gray),
        dynamic_range=dynamic_range_score(gray),
        noise=noise_score(gray),
        contrast=contrast_score(gray),
        color_harmony=color_harmony_score(image),
        monochrome=is_monochrome(image),
    )
