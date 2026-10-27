"""Pixel-level similarity for the repeat passes: SSIM (numpy only) and an ORB geometric match (needs OpenCV, otherwise unavailable).

Both work on small greyscale arrays. They are meant for two frames of what may be the same scene, shot minutes apart on a tripod or handheld with a
small shift, where pHash (a 64-bit fingerprint of the coarse layout) is too brittle: it moves a lot on smooth blue-hour gradients and cloud movement.
"""

from __future__ import annotations

import numpy as np

SSIM_EDGE = 192  # long edge the frames are compared at
SSIM_MAX_SHIFT = 0.06  # the largest framing shift (share of the frame) the alignment will undo


def normalise(gray: np.ndarray) -> np.ndarray:
    """Zero mean and unit spread, so two exposures of one scene are compared by their structure and not their brightness."""
    flat = gray.astype(np.float32)
    spread = float(flat.std())
    return (flat - float(flat.mean())) / (spread if spread > 1e-6 else 1.0)


def align_shift(left: np.ndarray, right: np.ndarray, limit: float = SSIM_MAX_SHIFT) -> tuple[int, int]:
    """The (dy, dx) that moves ``right`` onto ``left``, by phase correlation, within ``limit`` of the frame (0, 0) when nothing clear stands out."""
    window = np.outer(np.hanning(left.shape[0]), np.hanning(left.shape[1])).astype(np.float32)
    spectrum = np.fft.rfft2(left * window) * np.conj(np.fft.rfft2(right * window))
    spectrum /= np.abs(spectrum) + 1e-9
    surface = np.fft.irfft2(spectrum, s=left.shape)
    height, width = left.shape
    reach_y, reach_x = max(1, int(height * limit)), max(1, int(width * limit))
    best, shift = -1.0, (0, 0)
    for dy in range(-reach_y, reach_y + 1):
        row = surface[dy % height]
        for dx in range(-reach_x, reach_x + 1):
            value = float(row[dx % width])
            if value > best:
                best, shift = value, (dy, dx)
    return shift


def _box(values: np.ndarray, size: int) -> np.ndarray:
    padded = np.pad(values, ((1, 0), (1, 0)))
    total = padded.cumsum(0).cumsum(1)
    return (total[size:, size:] - total[:-size, size:] - total[size:, :-size] + total[:-size, :-size]) / (size * size)


def ssim(left: np.ndarray, right: np.ndarray, window: int = 7) -> float:
    """Mean structural similarity of two equally sized arrays (0 to 1, higher is more alike), uniform window, constants for unit-spread data."""
    c1, c2 = 0.01 ** 2 * 16, 0.03 ** 2 * 16
    mean_l, mean_r = _box(left, window), _box(right, window)
    var_l = _box(left * left, window) - mean_l * mean_l
    var_r = _box(right * right, window) - mean_r * mean_r
    cov = _box(left * right, window) - mean_l * mean_r
    score = ((2 * mean_l * mean_r + c1) * (2 * cov + c2)) / ((mean_l ** 2 + mean_r ** 2 + c1) * (var_l + var_r + c2))
    return float(score.mean())


def aligned_ssim(left: np.ndarray, right: np.ndarray) -> float:
    """SSIM after normalising both and undoing a small framing shift; frames of different shape never match."""
    if left.shape != right.shape:
        return 0.0
    a, b = normalise(left), normalise(right)
    dy, dx = align_shift(a, b)
    height, width = a.shape
    a = a[max(0, dy): height + min(0, dy), max(0, dx): width + min(0, dx)]
    b = b[max(0, -dy): height + min(0, -dy), max(0, -dx): width + min(0, -dx)]
    if min(a.shape) < 16:
        return 0.0
    return ssim(a, b)


def orb_match(left: np.ndarray, right: np.ndarray) -> tuple[int, float]:
    """(inliers, inlier share) of the ORB keypoint matches that agree on one geometric transform between two 8-bit greyscale frames, or (0, 0.0)
    when OpenCV is not installed or there is nothing to match."""
    try:
        import cv2
    except ImportError:
        return 0, 0.0
    orb = cv2.ORB_create(nfeatures=1500)
    keys_l, desc_l = orb.detectAndCompute(left, None)
    keys_r, desc_r = orb.detectAndCompute(right, None)
    if desc_l is None or desc_r is None or len(keys_l) < 12 or len(keys_r) < 12:
        return 0, 0.0
    pairs = cv2.BFMatcher(cv2.NORM_HAMMING).knnMatch(desc_l, desc_r, k=2)
    good = [m for m, n in (p for p in pairs if len(p) == 2) if m.distance < 0.75 * n.distance]
    if len(good) < 12:
        return 0, 0.0
    src = np.float32([keys_l[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
    dst = np.float32([keys_r[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)
    matrix, mask = cv2.findHomography(src, dst, cv2.RANSAC, 4.0)
    if matrix is None or mask is None:
        return 0, 0.0
    # The same place seen from a bit further left, or zoomed, matches too: that is a different shot, not a repeat. A repeat is the same framing, so the
    # transform must be close to the identity: scale within 8%, a turn under 3 degrees and a shift under 8% of the frame.
    scale = float(abs(np.linalg.det(matrix[:2, :2]))) ** 0.5
    turn = abs(float(np.degrees(np.arctan2(matrix[1, 0], matrix[0, 0]))))
    shift = max(abs(float(matrix[0, 2])) / left.shape[1], abs(float(matrix[1, 2])) / left.shape[0])
    if not (0.92 <= scale <= 1.08 and turn <= 3.0 and shift <= 0.08):
        return 0, 0.0
    inliers = int(mask.sum())
    return inliers, inliers / len(good)
