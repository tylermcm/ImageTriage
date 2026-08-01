from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PIL import Image

_CLI_EDITOR = Path(__file__).resolve().parents[1] / "cli_editor"
if str(_CLI_EDITOR) not in sys.path:
    sys.path.insert(0, str(_CLI_EDITOR))

from photo_terminal.adjustments import EditRecipe, _vignette_falloff, apply_vignette


def _flat(value: int, size: tuple[int, int] = (120, 80)) -> Image.Image:
    width, height = size
    return Image.fromarray(np.full((height, width, 3), value, np.uint8), "RGB")


def _corner(image: Image.Image) -> float:
    return float(np.asarray(image)[0, 0, 0])


def _centre(image: Image.Image) -> float:
    array = np.asarray(image)
    return float(array[array.shape[0] // 2, array.shape[1] // 2, 0])


class VignetteAmountTests(unittest.TestCase):
    def test_zero_is_a_no_op(self) -> None:
        base = _flat(128)
        self.assertIs(base, apply_vignette(base, 0.0))

    def test_centre_is_untouched_at_default_midpoint(self) -> None:
        out = apply_vignette(_flat(128), 1.0)
        self.assertEqual(128, _centre(out))

    def test_positive_darkens_and_negative_lightens_the_corners(self) -> None:
        base = _flat(100)
        self.assertLess(_corner(apply_vignette(base, 1.0)), 100)
        self.assertGreater(_corner(apply_vignette(base, -1.0)), 100)

    def test_the_two_directions_are_symmetric_in_stops(self) -> None:
        # The old fixed 0.45/1.35 pair made darkening ~3x stronger in stops,
        # which is why -100 read as "nothing happened".
        base = _flat(40)  # dark enough that +2 EV does not clip
        darker = _corner(apply_vignette(base, 1.0))
        brighter = _corner(apply_vignette(base, -1.0))
        self.assertAlmostEqual(40 / darker, brighter / 40, delta=0.15)

    def test_amount_scales_monotonically(self) -> None:
        base = _flat(160)
        corners = [_corner(apply_vignette(base, a)) for a in (0.25, 0.5, 0.75, 1.0)]
        self.assertTrue(all(a > b for a, b in zip(corners, corners[1:])))

    def test_corners_reach_full_strength(self) -> None:
        # The old ramp only hit maximum at the literal corner pixel, so even
        # +100 fell short of the requested two stops.
        self.assertLess(_corner(apply_vignette(_flat(200), 1.0)), 200 * 0.3)


class VignetteShapeTests(unittest.TestCase):
    def test_midpoint_moves_the_falloff_outward(self) -> None:
        base = _flat(180)
        edge_in = np.asarray(apply_vignette(base, 1.0, midpoint=10))
        edge_out = np.asarray(apply_vignette(base, 1.0, midpoint=90))
        midway = (edge_in.shape[0] // 2, edge_in.shape[1] // 4)
        self.assertLess(edge_in[midway][0], edge_out[midway][0])

    def test_feather_softens_the_transition(self) -> None:
        base = _flat(180)
        hard = np.asarray(apply_vignette(base, 1.0, feather=0)).astype(int)
        soft = np.asarray(apply_vignette(base, 1.0, feather=100)).astype(int)
        row = hard.shape[0] // 2
        # Largest neighbour-to-neighbour step along a scanline.
        hard_step = np.abs(np.diff(hard[row, :, 0])).max()
        soft_step = np.abs(np.diff(soft[row, :, 0])).max()
        self.assertGreater(hard_step, soft_step)

    def _edges(self, roundness: float) -> tuple[int, int]:
        """(top edge midpoint, left edge midpoint) of a 120x80 landscape frame."""
        out = np.asarray(apply_vignette(_flat(180), 1.0, roundness=roundness)).astype(int)
        return int(out[0, out.shape[1] // 2, 0]), int(out[out.shape[0] // 2, 0, 0])

    def test_circular_roundness_bites_the_long_edge_hardest(self) -> None:
        # A circle drawn through the corners of a landscape frame passes much
        # closer to the left/right edges than the top/bottom ones.
        top, side = self._edges(100)
        self.assertGreater(top, side)

    def test_neutral_roundness_tracks_the_frame_aspect(self) -> None:
        top, side = self._edges(0)
        self.assertAlmostEqual(top, side, delta=1)

    def test_negative_roundness_squares_the_vignette_off(self) -> None:
        # Frame-shaped: the whole edge darkens, not just the corners.
        square_top, square_side = self._edges(-100)
        round_top, round_side = self._edges(0)
        self.assertAlmostEqual(square_top, square_side, delta=1)
        self.assertLess(square_top, round_top)
        self.assertLess(square_side, round_side)

    def test_highlights_protects_bright_pixels(self) -> None:
        bright = _flat(250)
        unprotected = _corner(apply_vignette(bright, 1.0))
        protected = _corner(apply_vignette(bright, 1.0, highlights=100))
        self.assertGreater(protected, unprotected)
        # A dark frame is not protected by the same setting.
        dark = _flat(30)
        self.assertAlmostEqual(
            _corner(apply_vignette(dark, 1.0)),
            _corner(apply_vignette(dark, 1.0, highlights=100)),
            delta=2,
        )

    def test_falloff_is_cached_per_geometry_not_per_amount(self) -> None:
        _vignette_falloff.cache_clear()
        base = _flat(64, (40, 30))
        for amount in (0.2, 0.4, 0.6, 0.8):
            apply_vignette(base, amount)
        info = _vignette_falloff.cache_info()
        self.assertEqual(1, info.misses)
        self.assertEqual(3, info.hits)


class VignetteRecipeTests(unittest.TestCase):
    def test_shape_fields_reach_the_renderer(self) -> None:
        base = _flat(180)
        plain = EditRecipe.from_dict({"vignette": 100})
        shaped = EditRecipe.from_dict({"vignette": 100, "vignette_midpoint": 10})
        self.assertFalse(
            np.array_equal(np.asarray(plain.apply(base)), np.asarray(shaped.apply(base)))
        )

    def test_shape_defaults_do_not_apply_without_an_amount(self) -> None:
        base = _flat(180)
        recipe = EditRecipe.from_dict({"vignette_midpoint": 10, "vignette_feather": 0})
        self.assertTrue(np.array_equal(np.asarray(recipe.apply(base)), np.asarray(base)))

    def test_merged_keeps_a_base_shape_under_a_default_override(self) -> None:
        # merged() used to drop anything falsy from the override, which meant a
        # 50-defaulting field always clobbered the base.
        base = EditRecipe.from_dict({"vignette": 60, "vignette_midpoint": 20})
        merged = base.merged(EditRecipe(contrast=10))
        self.assertEqual(20, merged.vignette_midpoint)
        self.assertEqual(10, merged.contrast)


class VignetteSessionTests(unittest.TestCase):
    def test_shape_round_trips_through_session_operations(self) -> None:
        from image_triage.ui.photo_editor_panel import operations_from_recipe, recipe_from_session

        recipe = EditRecipe.from_dict(
            {"vignette": 70, "vignette_roundness": -40, "vignette_highlights": 25}
        )
        ops = operations_from_recipe(recipe)
        vignette_ops = [op for op in ops if op["type"] == "adjust.vignette"]
        self.assertEqual(1, len(vignette_ops))
        self.assertEqual(-40, vignette_ops[0]["params"]["roundness"])

        restored = recipe_from_session({"operations": ops})
        self.assertEqual(70, restored.vignette)
        self.assertEqual(-40, restored.vignette_roundness)
        self.assertEqual(25, restored.vignette_highlights)
        self.assertEqual(50, restored.vignette_midpoint)

    def test_shape_is_not_persisted_without_an_amount(self) -> None:
        from image_triage.ui.photo_editor_panel import operations_from_recipe

        recipe = EditRecipe.from_dict({"vignette_roundness": -40})
        self.assertEqual(
            [], [op for op in operations_from_recipe(recipe) if op["type"] == "adjust.vignette"]
        )


class VignettePanelTests(unittest.TestCase):
    def setUp(self) -> None:
        from PySide6.QtWidgets import QApplication

        self.app = QApplication.instance() or QApplication([])

    def test_options_are_collapsed_until_the_label_is_clicked(self) -> None:
        from image_triage.ui.photo_editor_panel import PhotoEditorPanel

        panel = PhotoEditorPanel()
        self.assertFalse(panel._vignette_options.isVisibleTo(panel))
        panel._rows["vignette"].set_expanded(True)
        self.assertTrue(panel._vignette_options.isVisibleTo(panel))

    def test_a_shaped_recipe_opens_the_options(self) -> None:
        from image_triage.ui.photo_editor_panel import PhotoEditorPanel

        panel = PhotoEditorPanel()
        panel._recipe = EditRecipe.from_dict({"vignette": 50, "vignette_roundness": 80})
        panel._sync_rows_from_recipe()
        self.assertTrue(panel._vignette_options.isVisibleTo(panel))
        self.assertEqual(80, panel._rows["vignette_roundness"].slider.value())

    def test_option_rows_open_on_their_neutral_values(self) -> None:
        from image_triage.ui.photo_editor_panel import PhotoEditorPanel

        panel = PhotoEditorPanel()
        defaults = EditRecipe()
        for key in ("vignette_midpoint", "vignette_roundness", "vignette_feather", "vignette_highlights"):
            with self.subTest(key):
                self.assertEqual(getattr(defaults, key), panel._rows[key].slider.value())
                self.assertEqual(getattr(defaults, key), panel._rows[key].value_box.value())

    def test_option_rows_drive_the_recipe(self) -> None:
        from image_triage.ui.photo_editor_panel import PhotoEditorPanel

        panel = PhotoEditorPanel()
        panel._rows["vignette_feather"].slider.setValue(12)
        self.assertEqual(12, panel._recipe.vignette_feather)

    def test_shape_controls_stay_out_of_the_mask_adjustment_set(self) -> None:
        from image_triage.ui.photo_editor_panel import (
            MASK_ADJUSTMENT_KEYS,
            VIGNETTE_OPTION_KEYS,
        )

        self.assertFalse(VIGNETTE_OPTION_KEYS & set(MASK_ADJUSTMENT_KEYS))


if __name__ == "__main__":
    unittest.main()
