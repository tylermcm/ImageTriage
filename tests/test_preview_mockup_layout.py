"""Live popout controls that the approved visual mockup depends on."""

from __future__ import annotations

import os
import unittest
from dataclasses import replace
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings, QSize, Qt
from PySide6.QtWidgets import QApplication, QFrame

from image_triage.models import ImageRecord
from image_triage.preview import FullScreenPreview, PreviewEntry
from image_triage.ui.photo_editor_panel import EditRecipe
from image_triage.ui import popout_layout_ratios as ratios


class PreviewMockupLayoutTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = QApplication.instance() or QApplication([])
        settings = QSettings()
        self._saved_preferences = {
            key: settings.value(key)
            for key in (
                FullScreenPreview.FILMSTRIP_COLLAPSED_KEY,
                FullScreenPreview.FILMSTRIP_THUMB_HEIGHT_KEY,
                FullScreenPreview.FILMSTRIP_THUMB_RATIO_KEY,
                FullScreenPreview.INSPECTOR_VISIBLE_KEY,
            )
        }
        settings.remove(FullScreenPreview.FILMSTRIP_THUMB_HEIGHT_KEY)
        settings.remove(FullScreenPreview.FILMSTRIP_THUMB_RATIO_KEY)
        settings.remove(FullScreenPreview.FILMSTRIP_COLLAPSED_KEY)
        settings.remove(FullScreenPreview.INSPECTOR_VISIBLE_KEY)
        self.preview = FullScreenPreview()
        self.preview.resize(1440, 900)
        self.preview.show()
        self.app.processEvents()

    def tearDown(self) -> None:
        self.preview.close()
        settings = QSettings()
        for key, value in self._saved_preferences.items():
            if value is None:
                settings.remove(key)
            else:
                settings.setValue(key, value)
        self.app.processEvents()

    def test_editor_tools_are_on_the_outer_edge_and_sections_fit(self) -> None:
        panel = self.preview.photo_editor_panel
        self.assertGreater(panel._editor_tool_rail.x(), panel._editor_column.x())
        self.assertEqual(panel._editor_tool_rail.width(), ratios.ratio_px(ratios.TOOL_RAIL_W, 1440, minimum=42))
        self.assertLessEqual(self.preview._studio_rail.width(), 340)
        self.assertFalse(panel._adjust_curve_section.layout().itemAt(1).widget().isVisible())
        self.assertTrue(self.preview._mockup_metadata_bar.isVisible())
        self.assertTrue(self.preview.before_after_button.isVisible())
        self.assertFalse(self.preview.before_after_button.isEnabled())

    def test_editor_rail_and_picker_use_the_supplied_white_icons(self) -> None:
        panel = self.preview.photo_editor_panel
        assets = Path(__file__).resolve().parents[1] / "image_triage" / "ui" / "assets"

        def has_white_artwork(size: QSize, button) -> bool:
            icon = button.icon().pixmap(size).toImage()
            if icon.isNull():
                return False
            colors = (icon.pixelColor(x, y) for y in range(icon.height()) for x in range(icon.width()))
            return any(color.alpha() > 128 and min(color.red(), color.green(), color.blue()) >= 245
                       for color in colors)

        for button, (_page, label, _tooltip, glyph, _group) in zip(panel._mode_buttons, panel.RAIL_TOOLS):
            with self.subTest(tool=label):
                self.assertTrue((assets / panel.POPOUT_RAIL_ICON_FILES[glyph]).is_file())
                self.assertTrue(has_white_artwork(QSize(32, 32), button))
        self.assertTrue((assets / "editor_point_color_picker.png").is_file())
        self.assertTrue(has_white_artwork(QSize(18, 18), panel.point_color_sample_button))

    def test_editor_rail_icon_and_label_are_tight_and_centered(self) -> None:
        rail = self.preview.photo_editor_panel._editor_tool_rail
        self.assertFalse(rail.findChildren(QFrame, "editorToolRailDivider"))
        for size in ((1024, 768), (1440, 900), (2048, 1191)):
            self.preview.resize(*size)
            self.app.processEvents()
            buttons = self.preview.photo_editor_panel._mode_buttons
            self.assertEqual([later.y() - earlier.y() for earlier, later in zip(buttons, buttons[1:])],
                             [buttons[0].height()] * (len(buttons) - 1))
            for button, (_page, label, *_rest) in zip(
                buttons,
                self.preview.photo_editor_panel.RAIL_TOOLS,
            ):
                with self.subTest(size=size, tool=label):
                    self.assertTrue(button.tight_icon_label)
                    icon_rect, label_rect = button._icon_label_rects()
                    self.assertEqual(label_rect.top() - icon_rect.bottom() - 1, 1)
                    self.assertEqual(icon_rect.center().x(), button.rect().center().x())
                    self.assertEqual(label_rect.center().x(), button.rect().center().x())
                    top_space = icon_rect.top()
                    bottom_space = button.height() - 1 - label_rect.bottom()
                    self.assertLessEqual(abs(top_space - bottom_space), 1)

    def test_icon_actions_are_below_image_and_photo_details_are_in_status_bar(self) -> None:
        bar = self.preview._mockup_metadata_bar
        actions = self.preview._studio_actionbar
        self.assertIs(actions.parentWidget(), bar)
        self.assertIs(self.preview._mockup_rating.parentWidget(), bar)
        self.assertEqual(bar.layout().count(), 4)
        self.assertGreaterEqual(bar.mapTo(self.preview, bar.rect().topLeft()).y(),
                                self.preview.content_widget.mapTo(self.preview, self.preview.content_widget.rect().bottomLeft()).y())
        self.assertIs(self.preview._mockup_filename.parentWidget(), self.preview._mockup_status_bar)
        self.assertIs(self.preview._mockup_capture.parentWidget(), self.preview._mockup_status_bar)
        self.assertIs(self.preview._mockup_image_size.parentWidget(), self.preview._mockup_status_bar)
        self.assertIs(self.preview._mockup_status_bar.parentWidget(), self.preview._filmstrip)
        self.assertEqual(self.preview.compare_toggle_button.toolButtonStyle(), Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.assertEqual(self.preview.inspector_toggle.text(), "")
        self.assertTrue(self.preview._mockup_rating.isVisible())

    def test_popout_metrics_follow_window_width_and_height(self) -> None:
        small = (1440, 900)
        large = (2048, 1191)
        small_bar_height = self.preview._studio_toolbar.height()
        small_rail_width = self.preview._studio_rail.width()
        small_row_height = self.preview.photo_editor_panel._rows["exposure"].height()
        small_thumb_height = self.preview._filmstrip.thumb_height()

        self.preview.resize(*large)
        self.app.processEvents()
        self.assertEqual(self.preview._studio_toolbar.height(), ratios.ratio_px(ratios.PATH_BAR_H, large[1]))
        self.assertEqual(self.preview._mockup_metadata_bar.height(),
                         ratios.ratio_px(ratios.ACTION_BAR_H, large[1], minimum=32))
        self.assertEqual(self.preview._studio_rail.width(), ratios.ratio_px(ratios.EDITOR_W, large[0]))
        self.assertEqual(self.preview.photo_editor_panel._editor_tool_rail.width(), ratios.ratio_px(ratios.TOOL_RAIL_W, large[0]))
        self.assertEqual(self.preview.photo_editor_panel._mode_buttons[0].height(),
                         ratios.ratio_px(ratios.TOOL_BUTTON_H, large[1]))
        self.assertEqual(self.preview.photo_editor_panel._rows["exposure"].slider.width(),
                         ratios.ratio_px(ratios.ADJUSTMENT_SLIDER_W, large[0]))
        self.assertEqual(self.preview.photo_editor_panel._rows["exposure"].value_box.width(),
                         ratios.ratio_px(ratios.ADJUSTMENT_VALUE_W, large[0]))
        self.assertEqual(self.preview.photo_editor_panel._rows["exposure"].height(), ratios.ratio_px(ratios.ADJUSTMENT_ROW_H, large[1]))
        self.assertEqual(self.preview._filmstrip.thumb_height(), ratios.ratio_px(ratios.FILMSTRIP_THUMB_H, large[1]))
        self.assertEqual(self.preview._filmstrip._handle.height(), ratios.ratio_px(ratios.FILMSTRIP_HANDLE_H, large[1]))
        self.assertAlmostEqual(self.preview._filmstrip.height() / large[1], 0.082, delta=0.003)
        self.assertEqual(self.preview._content_layout.contentsMargins().left(), 0)
        self.assertEqual(self.preview._content_layout.contentsMargins().top(), 0)
        self.assertEqual(self.preview._mockup_keep.size().width(), ratios.ratio_px(ratios.RATING_ACTION_W, large[0]))
        self.assertEqual(self.preview._mockup_keep.size().height(), ratios.ratio_px(ratios.RATING_ACTION_H, large[1]))
        self.assertIn(f"font-size: {ratios.ratio_px(ratios.PATH_TEXT_H, large[1])}px", self.preview.styleSheet())
        self.assertGreaterEqual(self.preview._studio_toolbar.height(), small_bar_height)
        self.assertGreater(self.preview._studio_rail.width(), small_rail_width)
        self.assertGreater(self.preview.photo_editor_panel._rows["exposure"].height(), small_row_height)
        self.assertGreater(self.preview._filmstrip.thumb_height(), small_thumb_height)

        self.preview.resize(*small)
        self.app.processEvents()
        self.assertEqual(self.preview._studio_toolbar.height(), small_bar_height)
        self.assertEqual(self.preview._studio_rail.width(), small_rail_width)

        self.preview.resize(1024, 768)
        self.app.processEvents()
        self.assertEqual(self.preview._studio_rail.width(), 270)
        self.assertFalse(self.preview._mockup_undo.isVisible())
        self.assertFalse(self.preview._mockup_redo.isVisible())
        adjust = self.preview.photo_editor_panel
        self.assertEqual(adjust._adjust_body.width(), adjust._adjust_scroll.viewport().width())

    def test_user_resized_filmstrip_keeps_its_height_ratio(self) -> None:
        self.preview._filmstrip.drag_resize(72)
        self.assertAlmostEqual(self.preview._mockup_filmstrip_ratio, 72 / 900)
        self.preview.resize(1440, 1200)
        self.app.processEvents()
        self.assertEqual(self.preview._filmstrip.thumb_height(), 96)

    def test_rating_buttons_emit_real_record_path_and_refresh_from_annotation(self) -> None:
        record = ImageRecord(path=r"C:\photos\frame.jpg", name="frame.jpg", size=100, modified_ns=1)
        self.preview._source_entries = [PreviewEntry(record, record.path, rating=3)]
        self.preview._rebuild_entries()
        self.preview._update_info_label()
        emitted: list[tuple[str, int]] = []
        self.preview.rating_requested.connect(lambda path, rating: emitted.append((path, rating)))

        self.preview._mockup_star_buttons[4].click()
        self.assertEqual(emitted, [(record.path, 5)])
        self.preview.set_annotation_state(record.path, winner=False, reject=False, rating=5)
        self.assertEqual(self.preview._entries[0].rating, 5)
        self.assertIn("#f2c858", self.preview._mockup_star_buttons[4].styleSheet())
        self.preview._mockup_star_buttons[4].click()
        self.assertEqual(emitted[-1], (record.path, 0))

    def test_layout_buttons_toggle_filmstrip_and_editor(self) -> None:
        filmstrip = self.preview._filmstrip
        starting_collapsed = filmstrip.is_collapsed()
        menu_actions = {action.text(): action for action in self.preview._mockup_settings_button.menu().actions()}
        menu_actions["Toggle filmstrip"].trigger()
        self.assertNotEqual(filmstrip.is_collapsed(), starting_collapsed)
        self.assertEqual(self.preview._mockup_filmstrip_toggle.isChecked(), not filmstrip.is_collapsed())
        starting_editor = self.preview._studio_rail.isVisible()
        menu_actions["Toggle editor"].trigger()
        self.assertNotEqual(self.preview._studio_rail.isVisible(), starting_editor)
        self.assertEqual(self.preview.inspector_toggle.isChecked(), self.preview._studio_rail.isVisible())

    def test_status_counts_only_actual_recipe_changes(self) -> None:
        record = ImageRecord(path=r"C:\photos\frame.jpg", name="frame.jpg", size=100, modified_ns=1)
        self.preview._source_entries = [PreviewEntry(record, record.path)]
        self.preview._rebuild_entries()
        self.preview._update_mockup_image_info()
        self.assertEqual(self.preview._mockup_edits_count.text(), "")

        self.preview._editor_recipe = replace(EditRecipe(), exposure=0.35)
        self.preview._update_mockup_image_info()
        self.assertEqual(self.preview._mockup_edits_count.text(), "1 adjustment")
        self.assertIn("Edited", self.preview._mockup_edited.text())


if __name__ == "__main__":
    unittest.main()
