"""Deterministic tests for the Phase-1 classical quality dimensions.

Each test asserts the *direction* of a metric on synthetic images (sharp beats
blurry, clipping is penalized, etc.) rather than exact values, so they stay
robust to normalization-constant tuning.
"""

from __future__ import annotations

import unittest

import numpy as np

from image_triage.quality import DimensionScores, analyze_technical
from image_triage.quality.technical import (
    color_harmony_score,
    contrast_score,
    dynamic_range_score,
    exposure_score,
    is_monochrome,
    noise_score,
    sharpness_score,
)


def gradient(h: int = 64, w: int = 64, lo: int = 0, hi: int = 255) -> np.ndarray:
    row = np.linspace(lo, hi, w)
    return np.clip(np.tile(row, (h, 1)), 0, 255).astype(np.uint8)


def checkerboard(h: int = 64, w: int = 64, block: int = 4) -> np.ndarray:
    yy, xx = np.indices((h, w))
    return (((yy // block + xx // block) % 2) * 255).astype(np.uint8)


def flat(h: int = 64, w: int = 64, val: int = 128) -> np.ndarray:
    return np.full((h, w), val, np.uint8)


def halves(h: int = 64, w: int = 64) -> np.ndarray:
    img = np.zeros((h, w), np.uint8)
    img[:, w // 2 :] = 255
    return img


def to_bgr(gray: np.ndarray) -> np.ndarray:
    return np.stack([gray, gray, gray], axis=-1)


class TechnicalDimensionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.rng = np.random.default_rng(1234)

    def test_sharpness_sharp_beats_blurry(self) -> None:
        self.assertGreater(sharpness_score(checkerboard()), sharpness_score(gradient()))

    def test_exposure_penalizes_clipping(self) -> None:
        clean = gradient(lo=30, hi=220)  # no clipping
        clipped = checkerboard()  # pure 0 and 255 everywhere
        self.assertGreater(exposure_score(clean), exposure_score(clipped))
        self.assertGreater(exposure_score(clean), 8.0)

    def test_dynamic_range_full_beats_narrow(self) -> None:
        self.assertGreater(
            dynamic_range_score(gradient(lo=0, hi=255)),
            dynamic_range_score(gradient(lo=100, hi=120)),
        )

    def test_noise_clean_beats_noisy(self) -> None:
        clean = gradient()
        noisy = np.clip(clean.astype(np.float64) + self.rng.normal(0, 40, clean.shape), 0, 255).astype(np.uint8)
        self.assertGreater(noise_score(clean), noise_score(noisy))

    def test_contrast_high_beats_flat(self) -> None:
        self.assertGreater(contrast_score(halves()), contrast_score(flat()))

    def test_color_harmony_varied_beats_single(self) -> None:
        colorful = self.rng.integers(0, 256, (64, 64, 3), dtype=np.uint8)
        single = np.zeros((64, 64, 3), np.uint8)
        single[..., 2] = 255  # solid red (BGR)
        self.assertGreater(color_harmony_score(colorful), color_harmony_score(single))

    def test_monochrome_detection(self) -> None:
        self.assertTrue(is_monochrome(to_bgr(gradient())))  # R==G==B -> no saturation
        saturated = np.zeros((32, 32, 3), np.uint8)
        saturated[..., 2] = 255  # solid red
        self.assertFalse(is_monochrome(saturated))

    def test_analyze_technical_fills_classical_fields(self) -> None:
        scores = analyze_technical(to_bgr(gradient()))
        self.assertIsInstance(scores, DimensionScores)
        for field in ("sharpness", "exposure", "dynamic_range", "noise", "contrast", "color_harmony"):
            value = getattr(scores, field)
            self.assertIsNotNone(value, field)
            self.assertGreaterEqual(value, 0.0, field)
            self.assertLessEqual(value, 10.0, field)
        self.assertIsInstance(scores.monochrome, bool)
        # Phase-2 fields stay unset.
        self.assertIsNone(scores.aesthetic)

    def test_high_iso_boost_does_not_lower_sharpness(self) -> None:
        img = gradient()
        self.assertGreaterEqual(sharpness_score(img, iso=6400), sharpness_score(img, iso=None))


if __name__ == "__main__":
    unittest.main()
