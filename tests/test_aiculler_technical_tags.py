from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

from aiculler.technical_tags import (
    ImageTechnicalMetrics,
    compute_technical_metrics,
    local_mean,
)


def _legacy_local_mean(gray: np.ndarray) -> np.ndarray:
    padded = np.pad(gray, 1, mode="edge")
    return (
        padded[:-2, :-2]
        + padded[:-2, 1:-1]
        + padded[:-2, 2:]
        + padded[1:-1, :-2]
        + padded[1:-1, 1:-1]
        + padded[1:-1, 2:]
        + padded[2:, :-2]
        + padded[2:, 1:-1]
        + padded[2:, 2:]
    ) / 9.0


def _legacy_metrics(path: Path) -> ImageTechnicalMetrics:
    with Image.open(path) as opened:
        image = opened.convert("RGB")
    image.thumbnail((2048, 2048), Image.Resampling.BILINEAR)
    rgb = np.asarray(image, dtype=np.float32) / 255.0
    gray = rgb @ np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)
    laplacian = (
        gray[:-2, 1:-1]
        + gray[2:, 1:-1]
        + gray[1:-1, :-2]
        + gray[1:-1, 2:]
        - 4.0 * gray[1:-1, 1:-1]
    )
    focus_score = float(np.clip(float(np.var(laplacian)) * 80.0, 0.0, 1.0))
    gx = np.diff(gray, axis=1)
    gy = np.diff(gray, axis=0)
    edge_energy = float(np.mean(np.abs(gx)) + np.mean(np.abs(gy)))
    directional_balance = abs(
        float(np.mean(np.abs(gx))) - float(np.mean(np.abs(gy)))
    ) / (edge_energy + 1e-6)
    motion_blur_score = float(np.clip((1.0 - focus_score) * directional_balance, 0.0, 1.0))
    max_channel = np.max(rgb, axis=2)
    highlight_clip_ratio = float(np.mean(max_channel >= 0.985))
    shadow_clip_ratio = float(np.mean(gray <= 0.025))
    contrast_score = float(np.clip(np.std(gray) * 4.0, 0.0, 1.0))
    noise_score = float(np.clip(np.std(gray - _legacy_local_mean(gray)) * 8.0, 0.0, 1.0))
    bright_ratio = float(np.mean(gray >= 0.90))
    p50 = float(np.percentile(gray, 50))
    p99 = float(np.percentile(gray, 99))
    highlight_severity = min(1.0, highlight_clip_ratio * 18.0)
    bright_severity = min(1.0, bright_ratio * 4.0)
    glare_gap = max(0.0, p99 - p50 - 0.30)
    harsh_light_score = float(
        np.clip(
            0.55 * highlight_severity + 0.30 * bright_severity + 0.15 * glare_gap * 2.0,
            0.0,
            1.0,
        )
    )
    return ImageTechnicalMetrics(
        focus_score=focus_score,
        motion_blur_score=motion_blur_score,
        highlight_clip_ratio=highlight_clip_ratio,
        shadow_clip_ratio=shadow_clip_ratio,
        contrast_score=contrast_score,
        noise_score=noise_score,
        harsh_light_score=harsh_light_score,
    )


class AICullerTechnicalTagTests(unittest.TestCase):
    def setUp(self) -> None:
        self.rng = np.random.default_rng(2468)

    def test_in_place_local_mean_matches_legacy_expression_exactly(self) -> None:
        gray = self.rng.random((47, 63), dtype=np.float32)

        actual = local_mean(gray)
        expected = _legacy_local_mean(gray)

        self.assertTrue(np.array_equal(expected, actual))

    def test_optimized_metrics_match_legacy_metrics_exactly(self) -> None:
        pixels = self.rng.integers(0, 256, (91, 137, 3), dtype=np.uint8)
        with tempfile.TemporaryDirectory(prefix="aiculler_tag_metrics_") as temp_dir:
            path = Path(temp_dir) / "sample.png"
            Image.fromarray(pixels, "RGB").save(path)

            expected = _legacy_metrics(path)
            timings: dict[str, float] = {}
            actual = compute_technical_metrics(path, timings=timings)

        self.assertEqual(expected, actual)
        self.assertIn("clipping_contrast", timings)
        self.assertIn("local_mean", timings)
        self.assertIn("total", timings)


if __name__ == "__main__":
    unittest.main()
