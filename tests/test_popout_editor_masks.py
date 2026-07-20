from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PySide6.QtCore import QPointF
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from image_triage.ui.mask_overlay import build_group_strength, paint_brush_stroke
from image_triage.ui.photo_editor_panel import PhotoEditorPanel
from photo_terminal.masks import refine_color_range, refine_luminance_range


class RangeMaskTests(unittest.TestCase):
    def test_luminance_range_keeps_only_selected_tones(self) -> None:
        source = Image.new("L", (4, 1))
        source.putdata((10, 80, 160, 240))
        result = refine_luminance_range(
            source, Image.new("L", source.size, 255), 70, 170, feather=0
        )
        self.assertEqual([0, 255, 255, 0], list(result.get_flattened_data()))

    def test_color_range_supports_a_hard_zero_feather_edge(self) -> None:
        source = Image.new("RGB", (3, 1))
        source.putdata(((100, 100, 100), (110, 100, 100), (150, 100, 100)))
        result = refine_color_range(
            source,
            Image.new("L", source.size, 255),
            (100, 100, 100),
            tolerance=10,
            feather=0,
        )
        self.assertEqual([255, 255, 0], list(result.get_flattened_data()))


class BrushMaskTests(unittest.TestCase):
    def test_brush_stroke_is_continuous_and_click_subtracts(self) -> None:
        mask = QImage(100, 40, QImage.Format.Format_Grayscale8)
        mask.fill(0)
        paint_brush_stroke(
            mask, QPointF(10, 20), QPointF(90, 20), size=10, flow=100, mode="add"
        )
        self.assertEqual(255, mask.pixelColor(50, 20).value())
        self.assertEqual(0, mask.pixelColor(50, 0).value())

        paint_brush_stroke(
            mask, QPointF(50, 20), QPointF(50, 20), size=10, flow=100, mode="subtract"
        )
        self.assertEqual(0, mask.pixelColor(50, 20).value())

    def test_bitmap_strength_applies_density(self) -> None:
        with tempfile.TemporaryDirectory(prefix="image_triage_bitmap_strength_") as temp_dir:
            path = Path(temp_dir) / "mask.png"
            mask = QImage(12, 8, QImage.Format.Format_Grayscale8)
            mask.fill(255)
            self.assertTrue(mask.save(str(path)))
            strength = build_group_strength(
                [("bitmap", {"assetPath": str(path), "density": 50}, "add")],
                12,
                8,
                (12, 8),
            )
            self.assertIsNotNone(strength)
            self.assertAlmostEqual(128, strength.pixelColor(6, 4).value(), delta=1)


class PhotoEditorPanelMaskTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_range_masks_can_be_tuned_and_color_can_be_resampled(self) -> None:
        with tempfile.TemporaryDirectory(prefix="image_triage_range_panel_") as temp_dir:
            source_path = Path(temp_dir) / "source.png"
            source = Image.new("RGB", (8, 2))
            source.putdata(
                [(20, 20, 20)] * 4 + [(220, 40, 40)] * 4
                + [(20, 20, 20)] * 4 + [(220, 40, 40)] * 4
            )
            source.save(source_path)

            panel = PhotoEditorPanel()
            panel.set_image(source_path)
            panel._set_editor_page(1)

            panel.range_low_spin.setValue(0)
            panel.range_high_spin.setValue(80)
            panel.range_feather_spin.setValue(0)
            panel.add_luminance_range_mask()
            luma_mask = panel._selected_mask_dict()
            self.assertEqual("luminance-range", luma_mask["uiStyle"])

            panel.range_high_spin.setValue(30)
            panel._regenerate_selected_range_mask()
            self.assertEqual(30, panel._selected_mask_dict()["params"]["high"])

            panel.range_tolerance_spin.setValue(10)
            panel.range_feather_spin.setValue(0)
            panel.arm_color_range_mask()
            panel.handle_overlay_source_clicked(1, 0)
            color_mask = panel._selected_mask_dict()
            self.assertEqual("color-range", color_mask["uiStyle"])
            color_asset = panel._bitmap_asset_path(color_mask)
            with Image.open(color_asset) as rendered:
                before = list(rendered.convert("L").get_flattened_data())
            self.assertGreater(before[0], before[7])

            panel.resample_selected_color_range()
            panel.handle_overlay_source_clicked(7, 0)
            color_mask = panel._selected_mask_dict()
            with Image.open(panel._bitmap_asset_path(color_mask)) as rendered:
                after = list(rendered.convert("L").get_flattened_data())
            self.assertLess(after[0], after[7])

            panel.arm_brush_mask("add")
            self.assertEqual("brush", panel._selected_mask_dict()["uiStyle"])
            self.assertEqual("add", panel.mask_overlay_state()["brush_mode"])
            panel.close()

    def test_generated_mask_asset_cannot_become_an_editor_source(self) -> None:
        with tempfile.TemporaryDirectory(prefix="image_triage_nested_mask_") as temp_dir:
            asset_path = Path(temp_dir) / "portrait.edit-assets" / "mask-001.png"
            asset_path.parent.mkdir()
            Image.new("L", (8, 8), 255).save(asset_path)

            panel = PhotoEditorPanel()
            panel.set_image(asset_path)

            self.assertIsNone(panel._source_path)
            self.assertFalse(panel.editor_stack.isEnabled())
            self.assertIn("cannot be edited", panel.status_label.text())
            panel.close()


if __name__ == "__main__":
    unittest.main()
