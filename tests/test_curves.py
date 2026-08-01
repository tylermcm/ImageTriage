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

from photo_terminal.adjustments import (
    EditRecipe,
    apply_point_curves,
    curve_lut,
    is_identity_curve,
    normalize_curve_points,
)


class CurveLutTests(unittest.TestCase):
    def test_identity_is_a_no_op(self) -> None:
        self.assertEqual(list(range(256)), curve_lut([[0, 0], [255, 255]]))
        self.assertTrue(is_identity_curve(None))
        self.assertTrue(is_identity_curve([]))
        self.assertTrue(is_identity_curve([[0, 0], [255, 255]]))

    def test_passes_through_control_points_and_stays_monotone(self) -> None:
        points = [[0, 0], [64, 30], [128, 160], [192, 220], [255, 255]]
        lut = curve_lut(points)
        for x, y in points:
            self.assertLessEqual(abs(lut[x] - y), 1)
        self.assertTrue(all(lut[i] <= lut[i + 1] for i in range(255)))
        self.assertGreaterEqual(min(lut), 0)
        self.assertLessEqual(max(lut), 255)

    def test_hard_s_curve_does_not_overshoot(self) -> None:
        # A plain cubic spline wobbles here; monotone interpolation must not.
        lut = curve_lut([[0, 0], [100, 20], [155, 235], [255, 255]])
        self.assertTrue(all(lut[i] <= lut[i + 1] for i in range(255)))

    def test_clamps_outside_the_control_range(self) -> None:
        lut = curve_lut([[40, 10], [200, 240]])
        self.assertEqual(10, lut[0])
        self.assertEqual(10, lut[39])
        self.assertEqual(240, lut[255])

    def test_normalize_sorts_dedupes_and_clamps(self) -> None:
        self.assertEqual([[10, 50], [255, 0]], normalize_curve_points([[300, -5], [10, 20], [10, 50]]))


class ApplyPointCurvesTests(unittest.TestCase):
    def _flat(self, value: int) -> Image.Image:
        return Image.fromarray(np.full((4, 4, 3), value, np.uint8), "RGB")

    def test_identity_leaves_pixels_untouched(self) -> None:
        img = self._flat(100)
        out = apply_point_curves(img, rgb=[[0, 0], [255, 255]])
        self.assertTrue(np.array_equal(np.asarray(img), np.asarray(out)))

    def test_composite_curve_brightens(self) -> None:
        out = np.asarray(apply_point_curves(self._flat(100), rgb=[[0, 0], [128, 180], [255, 255]]))
        self.assertGreater(int(out[0, 0, 0]), 100)

    def test_per_channel_curve_only_touches_that_channel(self) -> None:
        out = np.asarray(apply_point_curves(self._flat(120), red=[[0, 0], [120, 200], [255, 255]]))
        self.assertGreater(int(out[0, 0, 0]), 150)
        self.assertEqual(120, int(out[0, 0, 1]))
        self.assertEqual(120, int(out[0, 0, 2]))

    def test_composite_stacks_on_top_of_per_channel(self) -> None:
        rgb = [[0, 0], [200, 60], [255, 255]]
        red = [[0, 0], [120, 200], [255, 255]]
        out = np.asarray(apply_point_curves(self._flat(120), rgb=rgb, red=red))
        self.assertEqual(curve_lut(rgb)[curve_lut(red)[120]], int(out[0, 0, 0]))

    def test_recipe_applies_and_identity_recipe_is_a_no_op(self) -> None:
        base = Image.fromarray(
            np.random.default_rng(0).integers(0, 256, (30, 40, 3), dtype=np.uint8), "RGB"
        )
        shaped = EditRecipe.from_dict({"curve_rgb": [[0, 0], [128, 190], [255, 255]]})
        self.assertFalse(np.array_equal(np.asarray(shaped.apply(base)), np.asarray(base)))
        identity = EditRecipe.from_dict({"curve_rgb": [[0, 0], [255, 255]]})
        self.assertTrue(np.array_equal(np.asarray(identity.apply(base)), np.asarray(base)))


class CurvePersistenceTests(unittest.TestCase):
    def test_round_trips_through_session_operations(self) -> None:
        from image_triage.ui.photo_editor_panel import operations_from_recipe, recipe_from_session

        recipe = EditRecipe.from_dict(
            {"curve_rgb": [[0, 0], [128, 190], [255, 255]], "curve_blue": [[0, 0], [64, 40], [255, 255]]}
        )
        ops = operations_from_recipe(recipe)
        curve_ops = [op for op in ops if op["type"] == "adjust.point_curve"]
        self.assertEqual(2, len(curve_ops))
        self.assertEqual({"rgb", "blue"}, {op["params"]["channel"] for op in curve_ops})

        restored = recipe_from_session({"operations": ops})
        self.assertEqual([[0, 0], [128, 190], [255, 255]], restored.curve_rgb)
        self.assertEqual([[0, 0], [64, 40], [255, 255]], restored.curve_blue)
        self.assertIsNone(restored.curve_red)

    def test_identity_curves_are_not_persisted(self) -> None:
        from image_triage.ui.photo_editor_panel import operations_from_recipe

        recipe = EditRecipe.from_dict({"curve_rgb": [[0, 0], [255, 255]]})
        self.assertEqual(
            [], [op for op in operations_from_recipe(recipe) if op["type"] == "adjust.point_curve"]
        )


class CurveEditorWidgetTests(unittest.TestCase):
    def setUp(self) -> None:
        from PySide6.QtWidgets import QApplication

        self.app = QApplication.instance() or QApplication([])

    def test_endpoints_survive_delete_but_interior_points_do_not(self) -> None:
        from image_triage.ui.photo_editor_panel import CurveEditor

        editor = CurveEditor()
        editor.set_points("rgb", [[0, 0], [128, 190], [255, 255]])
        editor._selected = 0
        editor._remove(0)  # black point must not be deletable
        self.assertEqual(3, len(editor.points_for_test("rgb")))
        editor._selected = 1
        editor._remove(1)  # interior point is deletable
        self.assertEqual(2, len(editor.points_for_test("rgb")))
        self.assertIsNone(editor.curve_value("rgb"))

    def test_x_stays_strictly_increasing(self) -> None:
        from image_triage.ui.photo_editor_panel import CurveEditor

        editor = CurveEditor()
        editor.set_points("rgb", [[0, 0], [100, 120], [200, 200], [255, 255]])
        editor._selected = 1
        editor._apply_point(1, 250, 130)  # shoved past its right neighbour
        points = editor.points_for_test("rgb")
        self.assertLess(points[1][0], points[2][0])

    def test_channels_are_independent(self) -> None:
        from image_triage.ui.photo_editor_panel import CurveEditor

        editor = CurveEditor()
        editor.set_points("red", [[0, 0], [128, 60], [255, 255]])
        self.assertIsNotNone(editor.curve_value("red"))
        self.assertIsNone(editor.curve_value("rgb"))


if __name__ == "__main__":
    unittest.main()
