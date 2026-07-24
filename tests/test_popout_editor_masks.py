from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QImage, QMouseEvent
from PySide6.QtWidgets import QApplication, QWidget

import image_triage.ui.mask_overlay as mask_overlay_module
import image_triage.ui.photo_editor_panel as photo_editor_panel_module
from image_triage.ui.mask_overlay import (
    MaskOverlay,
    build_group_strength,
    compose_mask_overlay,
    paint_brush_stroke,
)
from image_triage.ui.photo_editor_panel import PhotoEditorPanel
from photo_terminal.masks import refine_color_range, refine_luminance_range


class _RecordingPerfLogger:
    enabled = True

    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, object]]] = []
        self.flush_count = 0

    def log(self, event: str, **fields: object) -> None:
        self.events.append((event, fields))

    def duration(self, event: str, duration_ms: float, **fields: object) -> None:
        self.events.append((event, {"duration_ms": duration_ms, **fields}))

    def flush(self) -> None:
        self.flush_count += 1


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

    def test_feathered_brush_has_a_soft_edge(self) -> None:
        mask = QImage(60, 60, QImage.Format.Format_Grayscale8)
        mask.fill(0)
        paint_brush_stroke(
            mask,
            QPointF(30, 30),
            QPointF(30, 30),
            size=30,
            feather=70,
            flow=100,
            mode="add",
        )
        center = mask.pixelColor(30, 30).value()
        hard_core = mask.pixelColor(37, 30).value()
        soft_edge = mask.pixelColor(42, 30).value()
        outside = mask.pixelColor(48, 30).value()
        self.assertAlmostEqual(center, hard_core, delta=1)
        self.assertGreater(center, soft_edge)
        self.assertGreater(soft_edge, outside)
        self.assertEqual(0, outside)

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


class BrushPerformanceLoggingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_stroke_logs_aggregated_raster_and_commit_timings(self) -> None:
        with tempfile.TemporaryDirectory(prefix="image_triage_brush_perf_") as temp_dir:
            path = Path(temp_dir) / "mask.png"
            image = QImage(100, 100, QImage.Format.Format_Grayscale8)
            image.fill(0)
            self.assertTrue(image.save(str(path)))

            logger = _RecordingPerfLogger()
            original_perf_logger = mask_overlay_module.perf_logger
            mask_overlay_module.perf_logger = lambda: logger
            try:
                host = QWidget()
                host.resize(100, 100)
                overlay = MaskOverlay(host)
                overlay.setGeometry(0, 0, 100, 100)
                overlay.set_state(
                    interactive=True,
                    show_overlay=True,
                    create_mode=None,
                    mask_type="bitmap",
                    params={
                        "assetPath": str(path),
                        "brushSize": 24,
                        "brushFeather": 70,
                    },
                    source_size=(100, 100),
                    brush_mode="add",
                    brush_size=24,
                    brush_feather=70,
                    brush_flow=100,
                )
                press = QMouseEvent(
                    QMouseEvent.Type.MouseButtonPress,
                    QPointF(50, 50),
                    Qt.MouseButton.LeftButton,
                    Qt.MouseButton.LeftButton,
                    Qt.KeyboardModifier.NoModifier,
                )
                release = QMouseEvent(
                    QMouseEvent.Type.MouseButtonRelease,
                    QPointF(50, 50),
                    Qt.MouseButton.LeftButton,
                    Qt.MouseButton.NoButton,
                    Qt.KeyboardModifier.NoModifier,
                )
                overlay.mousePressEvent(press)
                live_overlay = overlay._strength_image()
                self.assertIsNotNone(live_overlay)
                self.assertGreater(live_overlay.pixelColor(50, 50).alpha(), 0)
                persisted = QImage(str(path)).convertToFormat(
                    QImage.Format.Format_Grayscale8
                )
                self.assertEqual(0, persisted.pixelColor(50, 50).value())
                overlay.mouseReleaseEvent(release)
            finally:
                mask_overlay_module.perf_logger = original_perf_logger

            events = {event: fields for event, fields in logger.events}
            self.assertIn("brush.state.bitmap_load", events)
            self.assertIn("brush.stroke_start", events)
            self.assertIn("brush.stroke_complete", events)
            self.assertGreaterEqual(events["brush.stroke_complete"]["segments"], 1)
            self.assertIn("raster_total_ms", events["brush.stroke_complete"])
            self.assertGreaterEqual(
                events["brush.stroke_complete"]["live_overlay_frames"], 1
            )
            self.assertIn(
                "live_overlay_average_ms", events["brush.stroke_complete"]
            )
            self.assertIn("commit_signal_ms", events["brush.stroke_complete"])
            self.assertEqual(1, logger.flush_count)


class BrushLiveOverlayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_subtract_brush_carves_parent_overlay_before_release(self) -> None:
        with tempfile.TemporaryDirectory(prefix="image_triage_brush_live_") as temp_dir:
            directory = Path(temp_dir)
            parent_path = directory / "parent.png"
            brush_path = directory / "brush.png"
            parent = QImage(100, 100, QImage.Format.Format_Grayscale8)
            parent.fill(255)
            brush = QImage(100, 100, QImage.Format.Format_Grayscale8)
            brush.fill(0)
            self.assertTrue(parent.save(str(parent_path)))
            self.assertTrue(brush.save(str(brush_path)))

            host = QWidget()
            host.resize(100, 100)
            overlay = MaskOverlay(host)
            overlay.setGeometry(0, 0, 100, 100)
            overlay.set_state(
                interactive=True,
                show_overlay=True,
                create_mode=None,
                mask_type="bitmap",
                params={"assetPath": str(brush_path), "brushSize": 24},
                source_size=(100, 100),
                components=[
                    ("bitmap", {"assetPath": str(parent_path)}, "add"),
                    ("bitmap", {"assetPath": str(brush_path)}, "subtract"),
                ],
                selected_index=1,
                brush_mode="add",
                brush_size=24,
                brush_feather=0,
                brush_flow=100,
            )
            press = QMouseEvent(
                QMouseEvent.Type.MouseButtonPress,
                QPointF(50, 50),
                Qt.MouseButton.LeftButton,
                Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier,
            )
            overlay.mousePressEvent(press)
            live = overlay._strength_image()
            self.assertIsNotNone(live)
            self.assertEqual(0, live.pixelColor(50, 50).alpha())
            self.assertGreater(live.pixelColor(5, 5).alpha(), 0)


class MaskOverlayDisplayModeTests(unittest.TestCase):
    @staticmethod
    def _images() -> tuple[QImage, QImage]:
        strength = QImage(2, 1, QImage.Format.Format_Grayscale8)
        strength.setPixelColor(0, 0, QColor(255, 255, 255))
        strength.setPixelColor(1, 0, QColor(0, 0, 0))
        base = QImage(2, 1, QImage.Format.Format_RGB32)
        base.setPixelColor(0, 0, QColor(220, 20, 20))
        base.setPixelColor(1, 0, QColor(20, 40, 220))
        return strength, base

    def test_color_overlay_uses_mask_strength_and_selected_opacity(self) -> None:
        strength, base = self._images()
        overlay = compose_mask_overlay(
            strength, "color", QColor(20, 200, 80, 96), base
        )
        self.assertAlmostEqual(96, overlay.pixelColor(0, 0).alpha(), delta=1)
        self.assertEqual(0, overlay.pixelColor(1, 0).alpha())

    def test_image_on_black_and_white_preserve_the_selected_image(self) -> None:
        strength, base = self._images()
        black = compose_mask_overlay(strength, "image-black", QColor("red"), base)
        white = compose_mask_overlay(strength, "image-white", QColor("red"), base)
        self.assertEqual(0, black.pixelColor(0, 0).alpha())
        self.assertEqual(QColor("black").rgb(), black.pixelColor(1, 0).rgb())
        self.assertEqual(0, white.pixelColor(0, 0).alpha())
        self.assertEqual(QColor("white").rgb(), white.pixelColor(1, 0).rgb())

    def test_white_on_black_renders_the_mask_as_grayscale(self) -> None:
        strength, base = self._images()
        overlay = compose_mask_overlay(
            strength, "white-black", QColor("red"), base
        )
        self.assertEqual(QColor("white").rgb(), overlay.pixelColor(0, 0).rgb())
        self.assertEqual(QColor("black").rgb(), overlay.pixelColor(1, 0).rgb())

    def test_image_on_bw_keeps_only_the_selection_in_color(self) -> None:
        strength, base = self._images()
        overlay = compose_mask_overlay(strength, "image-bw", QColor("red"), base)
        selected = overlay.pixelColor(0, 0)
        outside = overlay.pixelColor(1, 0)
        self.assertGreater(selected.red(), selected.blue())
        self.assertEqual(outside.red(), outside.green())
        self.assertEqual(outside.green(), outside.blue())

    def test_color_overlay_on_bw_tints_only_the_selection(self) -> None:
        strength, base = self._images()
        overlay = compose_mask_overlay(
            strength, "color-bw", QColor(20, 220, 80, 128), base
        )
        selected = overlay.pixelColor(0, 0)
        outside = overlay.pixelColor(1, 0)
        self.assertGreater(selected.green(), selected.blue())
        self.assertEqual(outside.red(), outside.green())
        self.assertEqual(outside.green(), outside.blue())


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
            panel.editor_stack.setCurrentIndex(1)

            panel.luminance_range_slider.setValues(0, 80)
            panel.add_luminance_range_mask()
            luma_mask = panel._selected_mask_dict()
            self.assertEqual("luminance-range", luma_mask["uiStyle"])

            panel.luminance_range_slider.setValues(0, 30)
            panel._regenerate_selected_range_mask()
            self.assertEqual(30, panel._selected_mask_dict()["params"]["high"])
            panel.show_luminance_map_check.setChecked(True)
            state = panel.mask_overlay_state()
            self.assertTrue(state["show_overlay"])
            self.assertEqual("white-black", state["overlay_mode"])

            panel.color_refine_spin.setValue(3)
            panel.arm_color_range_mask()
            panel.handle_overlay_source_clicked(1, 0)
            color_mask = panel._selected_mask_dict()
            self.assertEqual("color-range", color_mask["uiStyle"])
            self.assertEqual(3, color_mask["params"]["refine"])
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
            panel.brush_feather_spin.setValue(72)
            panel.brush_density_spin.setValue(64)
            brush_params = panel._selected_mask_dict()["params"]
            self.assertEqual(72, brush_params["brushFeather"])
            self.assertEqual(64, brush_params["density"])
            panel.close()

    def test_mask_chooser_preserves_add_subtract_for_shapes_and_ranges(self) -> None:
        with tempfile.TemporaryDirectory(prefix="image_triage_mask_group_") as temp_dir:
            source_path = Path(temp_dir) / "source.png"
            Image.new("RGB", (80, 60), (60, 110, 160)).save(source_path)

            panel = PhotoEditorPanel()
            panel.set_image(source_path)
            panel.editor_stack.setCurrentIndex(1)
            panel._open_mask_create_pane()
            panel._arm_base_tool("radial")
            panel.handle_overlay_mask_created(
                "radial",
                {
                    "cx": 40,
                    "cy": 30,
                    "rx": 20,
                    "ry": 15,
                    "feather": 50,
                    "density": 100,
                    "invert": False,
                },
            )
            root_id = panel._selected_mask_id()

            panel.add_submask("subtract")
            self.assertEqual(panel.MASK_PANE_CREATE, panel.mask_stack.currentIndex())
            self.assertIsNone(panel._mask_create_mode)
            panel._arm_base_tool("linear-gradient")
            panel.handle_overlay_mask_created(
                "linear-gradient",
                {
                    "x1": 10,
                    "y1": 10,
                    "x2": 70,
                    "y2": 50,
                    "feather": 50,
                    "density": 100,
                    "invert": False,
                },
            )
            subtracted = panel._selected_mask_dict()
            self.assertEqual(root_id, subtracted["parentId"])
            self.assertEqual("subtract", subtracted["combine"])

            panel.add_submask("add")
            panel.luminance_range_slider.setValues(0, 255)
            panel.add_luminance_range_mask()
            added = panel._selected_mask_dict()
            self.assertEqual(root_id, added["parentId"])
            self.assertNotIn("combine", added)
            self.assertEqual("luminance-range", added["uiStyle"])
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
            self.assertIn("cannot be edited", panel.status_message)
            panel.close()

    def test_brush_commit_logs_asset_and_notification_phases(self) -> None:
        with tempfile.TemporaryDirectory(prefix="image_triage_brush_commit_") as temp_dir:
            source_path = Path(temp_dir) / "source.png"
            Image.new("RGB", (20, 20), "gray").save(source_path)
            panel = PhotoEditorPanel()
            panel.set_image(source_path)
            panel.editor_stack.setCurrentIndex(1)
            panel.arm_brush_mask("add")

            logger = _RecordingPerfLogger()
            original_perf_logger = photo_editor_panel_module.perf_logger
            photo_editor_panel_module.perf_logger = lambda: logger
            try:
                edited = QImage(20, 20, QImage.Format.Format_Grayscale8)
                edited.fill(255)
                panel.handle_overlay_bitmap_edited(edited)
            finally:
                photo_editor_panel_module.perf_logger = original_perf_logger
                panel.close()

            event_names = [event for event, _fields in logger.events]
            self.assertIn("brush.commit.asset_write", event_names)
            self.assertIn("brush.commit.overlay_notify", event_names)
            self.assertIn("brush.commit.total", event_names)


if __name__ == "__main__":
    unittest.main()
