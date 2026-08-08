from __future__ import annotations

import unittest

import numpy as np
from PIL import Image

from cli_editor.photo_terminal.adjustments import EditRecipe


def neutral_ramp() -> Image.Image:
    values = np.arange(256, dtype=np.uint8)
    pixels = np.repeat(values[:, None], 3, axis=1)[None, :, :]
    return Image.fromarray(pixels, mode="RGB")


class ToneControlTests(unittest.TestCase):
    def test_minimum_contrast_preserves_tonal_separation(self) -> None:
        rendered = np.asarray(EditRecipe(contrast=-100).apply(neutral_ramp()))[0, :, 0]

        self.assertGreaterEqual(len(np.unique(rendered)), 190)
        self.assertEqual(20, int(rendered[0]))
        self.assertEqual(235, int(rendered[-1]))
        self.assertTrue(np.all(np.diff(rendered.astype(np.int16)) >= 0))

    def test_maximum_contrast_remains_monotonic(self) -> None:
        rendered = np.asarray(EditRecipe(contrast=100).apply(neutral_ramp()))[0, :, 0]

        self.assertGreaterEqual(len(np.unique(rendered)), 160)
        self.assertTrue(np.all(np.diff(rendered.astype(np.int16)) >= 0))

    def test_minimum_contrast_does_not_desaturate_color(self) -> None:
        source = Image.new("RGB", (1, 1), (160, 80, 40))
        rendered = np.asarray(EditRecipe(contrast=-100).apply(source))[0, 0]

        self.assertGreater(int(rendered[0]), int(rendered[1]))
        self.assertGreater(int(rendered[1]), int(rendered[2]))
        self.assertGreater(int(rendered.max()) - int(rendered.min()), 20)

    def test_tone_control_endpoints_are_monotonic(self) -> None:
        for control in ("highlights", "shadows", "whites", "blacks"):
            for amount in (-100, 100):
                with self.subTest(control=control, amount=amount):
                    rendered = np.asarray(
                        EditRecipe(**{control: amount}).apply(neutral_ramp())
                    )[0, :, 0]
                    self.assertTrue(np.all(np.diff(rendered.astype(np.int16)) >= 0))

    def test_range_controls_target_their_named_tonal_regions(self) -> None:
        source = np.arange(256, dtype=np.int16)
        cases = (
            ("highlights", 100, 32, 224),
            ("shadows", 100, 224, 32),
            ("whites", 100, 32, 224),
            ("blacks", -100, 224, 32),
        )
        for control, amount, quiet_level, target_level in cases:
            with self.subTest(control=control, amount=amount):
                rendered = np.asarray(
                    EditRecipe(**{control: amount}).apply(neutral_ramp())
                )[0, :, 0].astype(np.int16)
                delta = np.abs(rendered - source)
                self.assertGreater(delta[target_level], delta[quiet_level])

    def test_exposure_supports_photoshop_range(self) -> None:
        dark = np.asarray(EditRecipe(exposure=-5).apply(neutral_ramp()))[0, :, 0]
        bright = np.asarray(EditRecipe(exposure=5).apply(neutral_ramp()))[0, :, 0]

        self.assertLess(int(dark[128]), 10)
        self.assertEqual(255, int(bright[128]))


if __name__ == "__main__":
    unittest.main()
