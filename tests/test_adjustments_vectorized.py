from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

_CLI_EDITOR = Path(__file__).resolve().parents[1] / "cli_editor"
if str(_CLI_EDITOR) not in sys.path:
    sys.path.insert(0, str(_CLI_EDITOR))

from photo_terminal.adjustments import EditRecipe, apply_hsl_adjustments, apply_vignette


class VignetteTests(unittest.TestCase):
    """apply_vignette was a per-pixel Python loop; it is now vectorized. These
    lock the behaviour so a future change can't silently regress the shared
    export pipeline."""

    def test_positive_amount_darkens_corners_but_not_center(self) -> None:
        img = Image.new("RGB", (61, 61), (200, 200, 200))
        out = np.asarray(apply_vignette(img, 0.8)).astype(int)
        center = out[30, 30].mean()
        corner = out[0, 0].mean()
        self.assertLess(corner, center)
        self.assertEqual(int(center), 200)  # center is outside the falloff

    def test_negative_amount_brightens_corners(self) -> None:
        img = Image.new("RGB", (61, 61), (120, 120, 120))
        out = np.asarray(apply_vignette(img, -0.8)).astype(int)
        self.assertGreater(out[0, 0].mean(), out[30, 30].mean())

    def test_deterministic(self) -> None:
        img = Image.fromarray(
            np.random.default_rng(0).integers(0, 256, (40, 50, 3), dtype=np.uint8), "RGB"
        )
        a = np.asarray(apply_vignette(img, 0.5))
        b = np.asarray(apply_vignette(img, 0.5))
        self.assertTrue(np.array_equal(a, b))


class HslTests(unittest.TestCase):
    """apply_hsl_adjustments was also a per-pixel loop; now vectorized."""

    def test_luminance_boost_brightens(self) -> None:
        img = Image.new("RGB", (16, 16), (100, 100, 100))
        recipe = EditRecipe.from_dict({"hsl_luminance": 60})
        out = np.asarray(apply_hsl_adjustments(img, recipe)).astype(int)
        self.assertGreater(out.mean(), 100)

    def test_red_saturation_boost_deepens_reds(self) -> None:
        img = Image.new("RGB", (16, 16), (200, 120, 120))  # a desaturated red
        base_sat = np.asarray(Image.new("RGB", (16, 16), (200, 120, 120)).convert("HSV"))[..., 1].mean()
        recipe = EditRecipe.from_dict({"red_saturation": 80})
        out_sat = np.asarray(apply_hsl_adjustments(img, recipe).convert("HSV"))[..., 1].mean()
        self.assertGreater(out_sat, base_sat)

    def test_deterministic(self) -> None:
        img = Image.fromarray(
            np.random.default_rng(1).integers(0, 256, (20, 24, 3), dtype=np.uint8), "RGB"
        )
        recipe = EditRecipe.from_dict({"green_saturation": 40, "blue_saturation": -30, "hsl_luminance": 20})
        a = np.asarray(apply_hsl_adjustments(img, recipe))
        b = np.asarray(apply_hsl_adjustments(img, recipe))
        self.assertTrue(np.array_equal(a, b))


if __name__ == "__main__":
    unittest.main()
