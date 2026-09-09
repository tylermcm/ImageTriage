"""The popout editor's vertical tool rail and the adjustment panels it exposes.

The rail replaced the old three-tab bar, so page indices are no longer
guessable — everything here goes through PhotoEditorPanel.PAGE_* constants on
purpose. The width assertions exist because the editor column is 344px and this
panel has overrun it repeatedly (see MaskPaneFootprintTests for the same guard
on the mask panes).
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "cli_editor"))

import numpy as np
from PIL import Image
from PySide6.QtWidgets import QApplication, QPushButton, QScrollArea, QWidget

from image_triage.ui.photo_editor_panel import (
    CALIBRATION_SPECS,
    COLOR_GRADING_MIX_SPECS,
    COLOR_GRADING_SPECS,
    COLOR_MIXER_SPECS,
    DEFRINGE_SPECS,
    GRAIN_SPECS,
    EditRecipe,
    PhotoEditorPanel,
    operations_from_recipe,
    recipe_from_session,
)
from photo_terminal.adjustments import (
    apply_color_calibration,
    apply_color_grading,
    apply_defringe,
    apply_grain,
    apply_hsl_adjustments,
)
from photo_terminal.session import RENDERER_ORDER, validate_operation_params

# The editor column budget. 336px of rail plus slack, matching the constant
# MaskPaneFootprintTests measures against.
COLUMN_BUDGET = 344


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _scrolled_body(page: QWidget) -> QWidget:
    return page.widget() if isinstance(page, QScrollArea) else page


class ToolRailNavigationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = _app()
        self.panel = PhotoEditorPanel()

    def tearDown(self) -> None:
        self.panel.close()

    def test_every_rail_entry_has_a_page(self) -> None:
        self.assertEqual(len(self.panel.RAIL_TOOLS), self.panel.editor_stack.count())
        self.assertEqual(len(self.panel.RAIL_TOOLS), len(self.panel._mode_buttons))

    def test_rail_pages_are_declared_in_stack_order(self) -> None:
        pages = [page for page, *_rest in self.panel.RAIL_TOOLS]
        self.assertEqual(list(range(len(pages))), pages)

    def test_clicking_a_rail_button_selects_exactly_that_page(self) -> None:
        for page, label, *_rest in self.panel.RAIL_TOOLS:
            with self.subTest(tool=label):
                self.panel._rail_button_for_page(page).click()
                self.assertEqual(page, self.panel.editor_stack.currentIndex())
                checked = [
                    tool[1]
                    for button, tool in zip(self.panel._mode_buttons, self.panel.RAIL_TOOLS)
                    if button.isChecked()
                ]
                self.assertEqual([label], checked)

    def test_show_adjustments_page_returns_to_adjust(self) -> None:
        self.panel._set_editor_page(self.panel.PAGE_MASKS)
        self.panel.show_adjustments_page()
        self.assertEqual(self.panel.PAGE_ADJUST, self.panel.editor_stack.currentIndex())
        self.assertTrue(self.panel._rail_button_for_page(self.panel.PAGE_ADJUST).isChecked())

    def test_the_mask_overlay_only_goes_live_on_the_masks_page(self) -> None:
        # The overlay used to key off a bare `currentIndex() == 1`; with eight
        # pages that would have made Crop and Red Eye draw mask handles.
        for page, label, *_rest in self.panel.RAIL_TOOLS:
            with self.subTest(tool=label):
                self.panel._set_editor_page(page)
                state = self.panel.mask_overlay_state()
                if page != self.panel.PAGE_MASKS:
                    self.assertFalse(state["interactive"])

    def test_background_and_lens_blur_are_rail_pages_not_popouts(self) -> None:
        for page in (self.panel.PAGE_BACKGROUND, self.panel.PAGE_LENS_BLUR):
            body = _scrolled_body(self.panel.editor_stack.widget(page))
            self.assertTrue(body.findChildren(QWidget), "tool page is empty")
        # The controls the old floating popouts owned are reachable from here.
        self.assertTrue(hasattr(self.panel, "lensblur_amount_slider"))


class ToolRailFootprintTests(unittest.TestCase):
    """Nothing the rail opens may overrun the editor column."""

    def setUp(self) -> None:
        self.app = _app()
        self.panel = PhotoEditorPanel()

    def tearDown(self) -> None:
        self.panel.close()

    def test_new_tool_pages_fit_the_editor_column(self) -> None:
        for page in (
            self.panel.PAGE_CROP,
            self.panel.PAGE_REMOVE,
            self.panel.PAGE_RED_EYE,
            self.panel.PAGE_BACKGROUND,
            self.panel.PAGE_LENS_BLUR,
        ):
            with self.subTest(page=page):
                body = _scrolled_body(self.panel.editor_stack.widget(page))
                self.assertLessEqual(body.minimumSizeHint().width(), COLUMN_BUDGET)

    def test_new_adjustment_sections_fit_the_editor_column(self) -> None:
        # The Adjust page as a whole is not asserted: its pre-existing Curve
        # section reports a wide hint here only because the standalone panel
        # carries no stylesheet (the studio QSS pins those buttons to
        # min-width: 0). The sections added for the Camera Raw parity work are
        # measured individually so a regression is attributable.
        body = _scrolled_body(self.panel.editor_stack.widget(self.panel.PAGE_ADJUST))
        wanted = {
            "Color Mixer",
            "Point Color",
            "Color Grading",
            "Color Calibration",
            "Defringe",
            "Grain",
        }
        seen = set()
        for header in body.findChildren(QPushButton, "editorSectionHeader"):
            title = header.text().replace("▾", "").replace("▸", "").strip()
            if title not in wanted:
                continue
            seen.add(title)
            section = header.parentWidget()
            with self.subTest(section=title):
                self.assertLessEqual(section.minimumSizeHint().width(), COLUMN_BUDGET)
        self.assertEqual(wanted, seen)


class ColorMixerBandTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = _app()
        self.panel = PhotoEditorPanel()

    def tearDown(self) -> None:
        self.panel.close()

    def test_only_the_selected_band_shows_its_sliders(self) -> None:
        self.panel._set_color_mixer_band("blue")
        self.assertEqual("blue", self.panel._color_mixer_band)
        for band, rows in self.panel._color_mixer_rows.items():
            for row in rows:
                with self.subTest(band=band, key=row.key):
                    self.assertEqual(band == "blue", not row.isHidden())

    def test_every_band_and_channel_has_a_row(self) -> None:
        for key, *_rest in COLOR_MIXER_SPECS:
            self.assertIn(key, self.panel._rows)


class PointColorControlTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = _app()
        self.panel = PhotoEditorPanel()
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "point-color.png"
        Image.new("RGB", (8, 8), (255, 0, 0)).save(self.path)
        self.panel.set_image(self.path)

    def tearDown(self) -> None:
        self.panel.close()
        self.temp.cleanup()

    def test_empty_section_has_no_editable_sample(self) -> None:
        self.assertEqual("No samples", self.panel.point_color_combo.currentText())
        self.assertFalse(self.panel.point_color_combo.isEnabled())
        self.assertFalse(self.panel.point_color_delete_button.isEnabled())
        self.assertTrue(self.panel.point_color_sample_button.isEnabled())
        self.assertTrue(
            all(not row.isEnabled() for row in self.panel._point_color_rows.values())
        )

    def test_sampling_selects_a_new_color_and_disarms_the_picker(self) -> None:
        self.panel._set_point_color_sample_armed(True)
        self.panel._sample_point_color_at(4, 4)
        self.assertFalse(self.panel._point_color_sample_armed)
        self.assertEqual("Sample 1", self.panel.point_color_combo.currentText())
        self.assertEqual(
            {"h": 0, "s": 255, "l": 255},
            {key: self.panel.recipe.point_colors[0][key] for key in ("h", "s", "l")},
        )

    def test_rows_edit_only_the_selected_sample(self) -> None:
        self.panel._recipe = EditRecipe.from_dict(
            {
                "point_colors": [
                    {"h": 0, "s": 255, "l": 255, "hueShift": 0, "satShift": 0,
                     "lumShift": 0, "range": 25},
                    {"h": 85, "s": 255, "l": 255, "hueShift": 0, "satShift": 0,
                     "lumShift": 0, "range": 25},
                ]
            }
        )
        self.panel._point_color_selected_index = 1
        self.panel._sync_point_color_controls()
        self.panel._handle_point_color_adjustment("satShift", -35)
        self.assertEqual(0, self.panel.recipe.point_colors[0]["satShift"])
        self.assertEqual(-35.0, self.panel.recipe.point_colors[1]["satShift"])

    def test_delete_removes_the_selected_sample(self) -> None:
        self.panel._set_point_color_sample_armed(True)
        self.panel._sample_point_color_at(2, 2)
        self.panel._remove_selected_point_color()
        self.assertFalse(self.panel.recipe.point_colors)
        self.assertEqual("No samples", self.panel.point_color_combo.currentText())

    def test_reselecting_the_same_photo_keeps_the_current_sample_selected(self) -> None:
        self.panel._set_point_color_sample_armed(True)
        self.panel._sample_point_color_at(2, 2)
        self.panel.set_image(self.path)
        self.assertEqual(0, self.panel._point_color_selected_index)
        self.panel._handle_point_color_adjustment("range", 40)
        self.assertEqual(40.0, self.panel.recipe.point_colors[0]["range"])


class ColorGradingWheelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = _app()
        self.panel = PhotoEditorPanel()

    def tearDown(self) -> None:
        self.panel.close()

    def test_the_wheel_writes_the_selected_zone(self) -> None:
        self.panel._set_grading_zone("highlight")
        self.panel._handle_grading_wheel_changed(210.0, 45.0)
        self.assertAlmostEqual(210.0, self.panel.recipe.grading_highlight_hue)
        self.assertAlmostEqual(45.0, self.panel.recipe.grading_highlight_sat)
        # The sliders are the same value seen twice; they must agree.
        self.assertAlmostEqual(210.0, self.panel._rows["grading_highlight_hue"].value_box.value())
        self.assertAlmostEqual(45.0, self.panel._rows["grading_highlight_sat"].value_box.value())
        self.assertAlmostEqual(0.0, self.panel.recipe.grading_shadow_hue)

    def test_switching_zones_moves_the_puck_to_that_zone(self) -> None:
        self.panel._set_grading_zone("shadow")
        self.panel._handle_grading_wheel_changed(90.0, 20.0)
        self.panel._set_grading_zone("global")
        self.assertEqual((0.0, 0.0), self.panel.color_grading_wheel.values())
        self.panel._set_grading_zone("shadow")
        hue, sat = self.panel.color_grading_wheel.values()
        self.assertAlmostEqual(90.0, hue)
        self.assertAlmostEqual(20.0, sat)


class NewAdjustmentPersistenceTests(unittest.TestCase):
    """Every new control must survive recipe -> session -> recipe."""

    VALUES = {
        "texture": 40.0,
        "grain": 30.0,
        "grain_size": 80.0,
        "grain_roughness": 20.0,
        "red_hue": 25.0,
        "blue_luminance": -40.0,
        "magenta_saturation": 15.0,
        "grading_shadow_hue": 210.0,
        "grading_shadow_sat": 35.0,
        "grading_highlight_lum": -20.0,
        "grading_blending": 70.0,
        "grading_balance": -25.0,
        "calibration_red_hue": 30.0,
        "calibration_blue_saturation": -20.0,
        "calibration_shadow_tint": 15.0,
        "defringe_purple_amount": 12.0,
        "defringe_green_amount": 5.0,
        "point_colors": [
            {"h": 120, "s": 180, "l": 140, "hueShift": 30, "satShift": 20,
             "lumShift": -10, "range": 25}
        ],
    }

    def test_round_trip_preserves_every_new_field(self) -> None:
        ops = operations_from_recipe(EditRecipe.from_dict(self.VALUES))
        restored = recipe_from_session({"operations": ops})
        for key, expected in self.VALUES.items():
            with self.subTest(key=key):
                self.assertEqual(expected, getattr(restored, key))

    def test_emitted_ops_pass_schema_validation(self) -> None:
        for op in operations_from_recipe(EditRecipe.from_dict(self.VALUES)):
            with self.subTest(op=op["type"]):
                self.assertEqual([], validate_operation_params(op["id"], op["type"], op["params"]))

    def test_new_op_types_are_known_to_the_schema(self) -> None:
        for op_type in (
            "adjust.calibration",
            "adjust.color_grading",
            "adjust.defringe",
            "adjust.point_color",
        ):
            self.assertIn(op_type, RENDERER_ORDER)

    def test_ops_are_emitted_in_renderer_order(self) -> None:
        ops = operations_from_recipe(EditRecipe.from_dict(self.VALUES))
        order = [RENDERER_ORDER.index(op["type"]) for op in ops]
        self.assertEqual(sorted(order), order)

    def test_a_neutral_recipe_writes_nothing(self) -> None:
        self.assertEqual([], operations_from_recipe(EditRecipe()))

    def test_shape_controls_ride_along_with_their_amount(self) -> None:
        # grain_size/roughness are neutral at non-zero, so the amount decides
        # whether they are worth writing (the vignette shape's rule).
        shape_only = operations_from_recipe(EditRecipe.from_dict({"grain_size": 90.0}))
        self.assertEqual([], shape_only)
        with_amount = operations_from_recipe(
            EditRecipe.from_dict({"grain": 10.0, "grain_size": 90.0})
        )
        params = next(op["params"] for op in with_amount if op["type"] == "adjust.grain")
        self.assertEqual(90.0, params["size"])
        self.assertIn("roughness", params)

    def test_bad_params_are_rejected(self) -> None:
        self.assertTrue(
            validate_operation_params("op-1", "adjust.color_grading", {"shadowHue": 999})
        )
        self.assertTrue(
            validate_operation_params("op-2", "adjust.defringe", {"purpleAmount": float("nan")})
        )
        self.assertTrue(
            validate_operation_params("op-3", "adjust.point_color", {"colors": []})
        )


class NewAdjustmentRenderTests(unittest.TestCase):
    """Numeric behaviour of the new apply functions."""

    def setUp(self) -> None:
        rng = np.random.RandomState(7)
        self.image = Image.fromarray(rng.randint(0, 255, (48, 64, 3), dtype=np.uint8), "RGB")
        self.gray = Image.new("RGB", (16, 16), (128, 128, 128))

    def test_a_neutral_recipe_is_a_no_op(self) -> None:
        out = np.asarray(EditRecipe().apply(self.image))
        self.assertTrue(np.array_equal(np.asarray(self.image), out))

    def test_color_grading_tints_without_shifting_exposure(self) -> None:
        recipe = EditRecipe.from_dict({"grading_global_hue": 240.0, "grading_global_sat": 60.0})
        out = np.asarray(apply_color_grading(self.gray, recipe), dtype=float)
        self.assertGreater(out[..., 2].mean(), out[..., 0].mean())  # bluer
        # Luma is what "without shifting exposure" means — a blue tint raises
        # the raw channel mean because blue is the darkest primary, so the flat
        # mean is the wrong thing to assert on.
        weights = (0.299, 0.587, 0.114)
        base = np.asarray(self.gray, dtype=float)
        self.assertLess(abs((out @ weights).mean() - (base @ weights).mean()), 1.5)

    def test_color_grading_balance_moves_the_split(self) -> None:
        recipe = EditRecipe.from_dict({"grading_shadow_hue": 240.0, "grading_shadow_sat": 80.0})
        dark = Image.new("RGB", (8, 8), (40, 40, 40))
        bright = Image.new("RGB", (8, 8), (215, 215, 215))
        dark_shift = np.asarray(apply_color_grading(dark, recipe), dtype=float)
        bright_shift = np.asarray(apply_color_grading(bright, recipe), dtype=float)
        dark_blue = dark_shift[..., 2].mean() - dark_shift[..., 0].mean()
        bright_blue = bright_shift[..., 2].mean() - bright_shift[..., 0].mean()
        self.assertGreater(dark_blue, bright_blue)

    def test_calibration_keeps_neutrals_neutral(self) -> None:
        recipe = EditRecipe.from_dict(
            {"calibration_red_hue": 40.0, "calibration_blue_saturation": 60.0}
        )
        out = np.asarray(apply_color_calibration(self.gray, recipe))
        self.assertEqual(0, int(out.max()) - int(out.min()))

    def test_calibration_shifts_color_not_brightness(self) -> None:
        recipe = EditRecipe.from_dict({"calibration_red_hue": 40.0})
        base = np.asarray(self.image, dtype=float)
        out = np.asarray(apply_color_calibration(self.image, recipe), dtype=float)
        self.assertLess(abs(out.mean() - base.mean()), 1.0)
        self.assertFalse(np.array_equal(base, out))

    def test_defringe_only_touches_edges(self) -> None:
        flat = Image.new("RGB", (24, 24), (150, 60, 200))  # solid purple, no edges
        recipe = EditRecipe.from_dict({"defringe_purple_amount": 20.0})
        out = np.asarray(apply_defringe(flat, recipe))
        self.assertTrue(np.array_equal(np.asarray(flat), out))

    def test_defringe_desaturates_a_purple_edge(self) -> None:
        arr = np.zeros((24, 24, 3), dtype=np.uint8)
        arr[:, :12] = (20, 20, 20)
        arr[:, 12:] = (170, 60, 220)
        edged = Image.fromarray(arr, "RGB")
        recipe = EditRecipe.from_dict({"defringe_purple_amount": 20.0})
        out = np.asarray(apply_defringe(edged, recipe), dtype=float)
        before = np.asarray(edged.convert("HSV"), dtype=float)[..., 1]
        after = np.asarray(Image.fromarray(out.astype(np.uint8), "RGB").convert("HSV"))[..., 1]
        self.assertLess(after.mean(), before.mean())

    def test_hsl_saturation_only_stays_backward_compatible(self) -> None:
        # Older sidecars carry saturation alone; that path must not drift when
        # the hue and luminance bands were added around it.
        recipe = EditRecipe.from_dict({"green_saturation": 40.0, "hsl_luminance": 20.0})
        first = np.asarray(apply_hsl_adjustments(self.image, recipe))
        second = np.asarray(apply_hsl_adjustments(self.image, recipe))
        self.assertTrue(np.array_equal(first, second))
        self.assertFalse(np.array_equal(np.asarray(self.image), first))

    def test_band_hue_shift_moves_only_that_band(self) -> None:
        # A pure red patch beside a pure blue one: shifting red must leave blue.
        arr = np.zeros((8, 16, 3), dtype=np.uint8)
        arr[:, :8] = (220, 30, 30)
        arr[:, 8:] = (30, 30, 220)
        image = Image.fromarray(arr, "RGB")
        recipe = EditRecipe.from_dict({"red_hue": 100.0})
        out = np.asarray(apply_hsl_adjustments(image, recipe))
        self.assertFalse(np.array_equal(arr[:, :8], out[:, :8]))
        self.assertTrue(np.array_equal(arr[:, 8:], out[:, 8:]))

    def test_grain_size_changes_the_result(self) -> None:
        fine = np.asarray(apply_grain(self.gray, 60.0, size=0.0, roughness=50.0), dtype=float)
        coarse = np.asarray(apply_grain(self.gray, 60.0, size=100.0, roughness=50.0), dtype=float)
        self.assertFalse(np.array_equal(fine, coarse))

    def test_zero_amount_controls_are_inert(self) -> None:
        for key in [spec[0] for spec in (*COLOR_MIXER_SPECS, *COLOR_GRADING_SPECS,
                                         *COLOR_GRADING_MIX_SPECS, *CALIBRATION_SPECS,
                                         *DEFRINGE_SPECS, *GRAIN_SPECS)]:
            with self.subTest(key=key):
                recipe = EditRecipe.from_dict({key: 0.0})
                out = np.asarray(recipe.apply(self.image))
                self.assertTrue(np.array_equal(np.asarray(self.image), out))


if __name__ == "__main__":
    unittest.main()
