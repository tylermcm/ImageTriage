from __future__ import annotations

import os
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QLabel,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtTest import QTest


def _panel():
    from image_triage.ui.photo_editor_panel import PhotoEditorPanel

    panel = PhotoEditorPanel()
    panel._source_path = Path("photo.jpg")
    panel.editor_stack.setCurrentIndex(panel.PAGE_MASKS)
    panel._sync_enabled()
    return panel


def _fake_mask(panel, mask_id: str, mask_type: str, **extra) -> None:
    """Put a mask in the session and select it, without touching disk."""
    mask = {"id": mask_id, "type": mask_type, "params": {}, **extra}
    panel._session = {"masks": [mask], "operations": [], "coordinateSpaces": []}
    item = QListWidgetItem(mask_id)
    item.setData(Qt.ItemDataRole.UserRole, mask_id)
    panel.masks_list.addItem(item)
    panel.masks_list.setCurrentItem(item)


class MaskTwoStateNavigationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = QApplication.instance() or QApplication([])

    def test_the_tab_opens_on_the_work_pane(self) -> None:
        panel = _panel()
        self.assertEqual(panel.MASK_PANE_WORK, panel.mask_stack.currentIndex())

    def test_new_mask_opens_create_and_cancel_returns(self) -> None:
        panel = _panel()
        panel.new_mask_button.click()
        self.assertEqual(panel.MASK_PANE_CREATE, panel.mask_stack.currentIndex())
        self.assertEqual("Point to preview.\nClick to mask.", panel._scene_hint.text())
        self.assertTrue(panel._scene_hint.wordWrap())
        panel.mask_create_back.click()
        self.assertEqual(panel.MASK_PANE_WORK, panel.mask_stack.currentIndex())

    def test_add_and_subtract_open_the_mask_type_chooser(self) -> None:
        for combine, title in (
            ("add", "Add to mask"),
            ("subtract", "Subtract from mask"),
        ):
            with self.subTest(combine=combine):
                panel = _panel()
                _fake_mask(panel, "mask-001", "radial")
                panel.add_submask(combine)

                self.assertEqual(
                    panel.MASK_PANE_CREATE,
                    panel.mask_stack.currentIndex(),
                )
                self.assertEqual("mask-001", panel._pending_parent_id)
                self.assertEqual(combine, panel._pending_combine)
                self.assertIsNone(panel._mask_create_mode)
                self.assertEqual(title, panel.mask_create_title.text())
                panel.close()

    def test_subtract_chooser_preserves_context_for_the_selected_tool(self) -> None:
        panel = _panel()
        _fake_mask(panel, "mask-001", "radial")
        panel.add_submask("subtract")
        panel.radial_tool_button.click()

        self.assertEqual("radial", panel._mask_create_mode)
        self.assertEqual("mask-001", panel._pending_parent_id)
        self.assertEqual("subtract", panel.mask_overlay_state()["create_combine"])

        panel.mask_create_back.click()
        self.assertIsNone(panel._pending_parent_id)
        self.assertEqual("add", panel._pending_combine)
        panel.close()

    def test_there_is_no_refine_adjust_toggle(self) -> None:
        # The whole point of the redesign: shape and effect share one scroll.
        panel = _panel()
        self.assertFalse(hasattr(panel, "mask_mode_buttons"))
        self.assertFalse(hasattr(panel, "mask_editor_stack"))

    def test_leaving_create_disarms_the_armed_tool(self) -> None:
        panel = _panel()
        panel._show_mask_pane(panel.MASK_PANE_CREATE)
        panel._arm_base_tool("radial")
        self.assertEqual("radial", panel._mask_create_mode)
        panel._show_mask_pane(panel.MASK_PANE_WORK)
        self.assertIsNone(panel._mask_create_mode)

    def test_creating_a_mask_returns_to_the_work_pane(self) -> None:
        panel = _panel()
        panel._show_mask_pane(panel.MASK_PANE_CREATE)
        _fake_mask(panel, "mask-001", "radial")
        panel._select_mask_in_list("mask-001")
        self.assertEqual(panel.MASK_PANE_WORK, panel.mask_stack.currentIndex())

    def test_the_detail_stack_is_hidden_until_a_mask_is_selected(self) -> None:
        panel = _panel()
        panel._sync_mask_controls(None)
        self.assertFalse(panel._mask_detail.isVisibleTo(panel))
        _fake_mask(panel, "mask-001", "radial")
        self.assertTrue(panel._mask_detail.isVisibleTo(panel))

    def test_new_mask_needs_a_loaded_image(self) -> None:
        from image_triage.ui.photo_editor_panel import PhotoEditorPanel

        panel = PhotoEditorPanel()
        panel.editor_stack.setCurrentIndex(panel.PAGE_MASKS)
        panel._source_path = None
        panel._sync_enabled()
        self.assertFalse(panel.new_mask_button.isEnabled())


class MaskToolRowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = QApplication.instance() or QApplication([])

    def test_tool_rows_carry_a_label_and_optional_shortcut_chip(self) -> None:
        from PySide6.QtWidgets import QLabel

        panel = _panel()
        row = panel.radial_tool_button
        labels = [w.text() for w in row.findChildren(QLabel)]
        self.assertIn("Radial gradient", labels)
        self.assertIn("R", labels)
        self.assertTrue(row.isCheckable())

    def test_arming_a_tool_row_still_reaches_the_handler(self) -> None:
        panel = _panel()
        panel._show_mask_pane(panel.MASK_PANE_CREATE)
        panel.radial_tool_button.click()  # checkable: toggles on, fires clicked
        self.assertEqual("radial", panel._mask_create_mode)

    def test_tool_row_child_labels_pass_clicks_through(self) -> None:
        from PySide6.QtWidgets import QLabel

        panel = _panel()
        transparent = Qt.WidgetAttribute.WA_TransparentForMouseEvents
        for label in panel.brush_tool_button.findChildren(QLabel):
            self.assertTrue(label.testAttribute(transparent))


class MaskListBlockTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = QApplication.instance() or QApplication([])

    def test_add_and_subtract_sit_under_the_list_and_gate_on_selection(self) -> None:
        panel = _panel()
        # No mask: the group actions are present but disabled.
        panel._sync_mask_controls(None)
        self.assertFalse(panel.add_submask_button.isEnabled())
        self.assertFalse(panel.subtract_submask_button.isEnabled())
        _fake_mask(panel, "mask-001", "radial")
        self.assertTrue(panel.add_submask_button.isEnabled())
        self.assertTrue(panel.subtract_submask_button.isEnabled())

    def test_add_and_subtract_are_compact_icon_buttons(self) -> None:
        panel = _panel()
        for button in (
            panel.add_submask_button,
            panel.subtract_submask_button,
        ):
            with self.subTest(label=button.text()):
                self.assertEqual("maskCombineButton", button.objectName())
                self.assertFalse(button.icon().isNull())
                self.assertEqual(22, button.height())
                self.assertEqual(
                    panel.new_mask_button.height(),
                    button.height(),
                )

    def test_masks_live_in_a_small_framed_viewport(self) -> None:
        panel = _panel()
        viewport = panel.mask_list_viewport
        self.assertIsInstance(viewport, QFrame)
        self.assertEqual("maskListViewport", viewport.objectName())
        self.assertIs(panel.masks_list.parent(), viewport)
        self.assertFalse(viewport.isAncestorOf(panel.add_submask_button))
        margins = panel.masks_list.viewportMargins()
        self.assertGreaterEqual(margins.left(), 4)
        self.assertGreaterEqual(margins.right(), 4)

    def test_add_subtract_are_reachable_without_a_selected_mask_in_the_detail(self) -> None:
        # They live in the always-visible list block, not the hidden detail, so
        # they no longer require scrolling to the bottom of the mask controls.
        panel = _panel()
        self.assertFalse(
            panel.add_submask_button.parent() is panel._mask_detail
            or panel._mask_detail.isAncestorOf(panel.add_submask_button)
        )

    def test_overlay_settings_button_opens_the_display_settings_menu(self) -> None:
        panel = _panel()
        self.assertEqual("overlayMenuButton", panel.overlay_menu_button.objectName())
        self.assertEqual(30, panel.overlay_menu_button.width())
        self.assertEqual(22, panel.overlay_menu_button.height())
        self.assertFalse(panel.overlay_menu_button.icon().isNull())
        self.assertEqual(
            [label for _mode, label in panel.OVERLAY_MODE_OPTIONS],
            [action.text() for action in panel.overlay_mode_group.actions()],
        )

    def test_overlay_settings_flow_into_the_canvas_state(self) -> None:
        class MemorySettings:
            def __init__(self) -> None:
                self.values: dict[str, object] = {}

            def setValue(self, key: str, value: object) -> None:  # noqa: N802
                self.values[key] = value

        panel = _panel()
        settings = MemorySettings()
        panel._settings = settings
        color = QColor(40, 180, 255, 96)

        panel._set_overlay_mode("image-black")
        panel._set_overlay_color(color)
        panel._set_overlay_show_tools(False)

        state = panel.mask_overlay_state()
        self.assertEqual("image-black", state["overlay_mode"])
        self.assertEqual(color.rgba(), state["overlay_color"].rgba())
        self.assertFalse(state["show_tools"])
        self.assertEqual("image-black", settings.values[panel.OVERLAY_MODE_KEY])
        self.assertEqual(int(color.rgba()), settings.values[panel.OVERLAY_COLOR_KEY])

    def test_automatic_toggle_controls_slider_overlay_suppression(self) -> None:
        class MemorySettings:
            def setValue(self, _key: str, _value: object) -> None:  # noqa: N802
                pass

        panel = _panel()
        panel._settings = MemorySettings()
        panel._set_overlay_auto_toggle(False)
        panel._begin_mask_slider_drag()
        self.assertFalse(panel._overlay_suppressed_for_drag)

        panel._set_overlay_auto_toggle(True)
        panel._begin_mask_slider_drag()
        self.assertTrue(panel._overlay_suppressed_for_drag)
        panel._end_mask_slider_drag()
        self.assertFalse(panel._overlay_suppressed_for_drag)

    def test_overlay_color_dialog_is_compact_with_only_opacity_numeric_control(self) -> None:
        panel = _panel()
        dialog = panel._build_overlay_color_dialog()
        dialog.show()
        self.app.processEvents()

        pick = next(
            button
            for button in dialog.findChildren(QPushButton)
            if "Pick Screen Color" in button.text()
        )
        custom = next(
            label
            for label in dialog.findChildren(QLabel)
            if "Custom colors" in label.text()
        )
        gap = custom.y() - (pick.y() + pick.height())
        self.assertLessEqual(gap, 12)
        visible_spins = [
            spin for spin in dialog.findChildren(QSpinBox) if spin.isVisibleTo(dialog)
        ]
        self.assertEqual(["overlayOpacitySpin"], [spin.objectName() for spin in visible_spins])
        opacity_slider = dialog.findChild(QSlider, "overlayOpacitySlider")
        self.assertIsNotNone(opacity_slider)
        self.assertEqual((1, 100), (opacity_slider.minimum(), opacity_slider.maximum()))
        self.assertEqual(50, opacity_slider.value())
        opacity_slider.setValue(25)
        self.assertAlmostEqual(64, dialog.currentColor().alpha(), delta=1)
        self.assertLess(dialog.height(), 410)
        dialog.close()

    def test_the_list_is_a_single_row_tall_for_one_mask(self) -> None:
        panel = _panel()
        _fake_mask(panel, "mask-001", "radial")
        one = panel.masks_list.height()
        for extra in range(2, 5):
            _fake_mask_append(panel, f"mask-00{extra}")
        grown = panel.masks_list.height()
        self.assertGreater(grown, one)

    def test_the_list_caps_its_height_and_scrolls(self) -> None:
        panel = _panel()
        _fake_mask(panel, "mask-001", "radial")
        for extra in range(2, 9):
            _fake_mask_append(panel, f"mask-00{extra}")
        row = panel.masks_list.sizeHintForRow(0)
        cap = panel.MASK_LIST_MAX_ROWS * row + 2 * panel.masks_list.frameWidth() + 8
        self.assertLessEqual(panel.masks_list.height(), cap)


def _fake_mask_append(panel, mask_id: str) -> None:
    item = QListWidgetItem(mask_id)
    item.setData(Qt.ItemDataRole.UserRole, mask_id)
    panel.masks_list.addItem(item)


class MaskRowTrashTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = QApplication.instance() or QApplication([])

    def test_each_row_has_a_trash_button_wired_to_its_own_id(self) -> None:
        from PySide6.QtWidgets import QToolButton

        panel = _panel()
        deleted: list[str] = []
        panel.delete_mask = deleted.append  # type: ignore[assignment]
        row = panel._make_mask_row_widget("mask-007", "Water", "", False)
        trash = row.findChild(QToolButton, "maskRowTrash")
        self.assertIsNotNone(trash)
        trash.click()
        self.assertEqual(["mask-007"], deleted)

    def test_generated_mask_row_has_a_touchup_button_wired_to_its_id(self) -> None:
        from PySide6.QtWidgets import QToolButton

        panel = _panel()
        opened: list[str] = []
        panel.open_mask_touchup = opened.append  # type: ignore[assignment]
        row = panel._make_mask_row_widget(
            "mask-007", "Subject", "", False, can_touch_up=True
        )
        touchup = row.findChild(QToolButton, "maskRowTouchup")
        self.assertIsNotNone(touchup)
        self.assertFalse(touchup.icon().isNull())
        touchup.click()
        self.assertEqual(["mask-007"], opened)

    def test_regular_mask_row_has_no_touchup_button(self) -> None:
        from PySide6.QtWidgets import QToolButton

        panel = _panel()
        row = panel._make_mask_row_widget("mask-001", "Radial", "", False)
        self.assertIsNone(row.findChild(QToolButton, "maskRowTouchup"))

    def test_rows_are_single_line_compact(self) -> None:
        panel = _panel()
        row = panel._make_mask_row_widget("mask-001", "Sky", "", False)
        self.assertLessEqual(row.sizeHint().height(), 24)

    def test_root_row_content_clears_the_selection_indicator(self) -> None:
        from PySide6.QtWidgets import QLabel

        panel = _panel()
        row = panel._make_mask_row_widget("mask-001", "Water", "", False)
        self.assertGreaterEqual(row.layout().contentsMargins().left(), 10)
        self.assertGreaterEqual(row.layout().contentsMargins().right(), 10)
        label = row.findChild(QLabel, "maskRowLabel")
        self.assertIn("background-color: transparent", label.styleSheet())

    def test_selection_is_painted_by_the_custom_row(self) -> None:
        panel = _panel()
        item = QListWidgetItem()
        item.setData(Qt.ItemDataRole.UserRole, "mask-001")
        row = panel._make_mask_row_widget("mask-001", "Water", "", False)
        self.assertTrue(
            row.testAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        )
        panel.masks_list.addItem(item)
        panel.masks_list.setItemWidget(item, row)
        panel.masks_list.setCurrentItem(item)
        panel._sync_mask_row_selection()
        self.assertTrue(row.property("selected"))

    def test_a_submask_row_shows_its_combine_marker(self) -> None:
        from PySide6.QtWidgets import QLabel

        panel = _panel()
        row = panel._make_mask_row_widget("mask-002", "Radial Gradient", "−", True)
        labels = [w.text() for w in row.findChildren(QLabel)]
        self.assertIn("−", labels)

    def test_the_row_label_passes_clicks_through_for_selection(self) -> None:
        from PySide6.QtWidgets import QLabel

        panel = _panel()
        row = panel._make_mask_row_widget("mask-001", "Sky", "", False)
        transparent = Qt.WidgetAttribute.WA_TransparentForMouseEvents
        label = row.findChild(QLabel, "maskRowLabel")
        self.assertTrue(label.testAttribute(transparent))

    def test_delete_selected_delegates_to_delete_mask(self) -> None:
        panel = _panel()
        seen: list[object] = []
        panel.delete_mask = seen.append  # type: ignore[assignment]
        _fake_mask(panel, "mask-001", "radial")
        panel.delete_selected_mask()
        self.assertEqual(["mask-001"], seen)

    def test_the_row_container_is_not_transparent_to_mouse(self) -> None:
        # Regression: a transparent container swallowed the trash button's clicks
        # and passed them to the list, so single-click delete silently no-op'd.
        panel = _panel()
        row = panel._make_mask_row_widget("mask-001", "Sky", "", False)
        self.assertFalse(
            row.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        )

    def test_clicking_a_row_body_selects_it(self) -> None:
        panel = _panel()
        selected: list[object] = []
        row = panel._make_mask_row_widget("mask-001", "Sky", "", False)
        row.clicked.connect(lambda: selected.append("clicked"))
        row.clicked.emit()
        self.assertEqual(["clicked"], selected)

    def test_double_clicking_a_row_emits_edit_request(self) -> None:
        panel = _panel()
        requested: list[object] = []
        row = panel._make_mask_row_widget("mask-001", "Sky", "", False)
        row.doubleClicked.connect(lambda: requested.append("edit"))
        row.show()
        QTest.mouseDClick(row, Qt.MouseButton.LeftButton)
        self.assertEqual(["edit"], requested)

    def test_a_second_parent_group_draws_a_divider(self) -> None:
        panel = _panel()
        first = panel._make_mask_row_widget("m1", "Sky", "", False, separated=False)
        second = panel._make_mask_row_widget("m2", "Water", "", False, separated=True)
        self.assertFalse(bool(first.property("separated")))
        self.assertTrue(bool(second.property("separated")))


class MaskContextualSectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = QApplication.instance() or QApplication([])

    def _visible_sections(self, panel) -> set[str]:
        names = {
            "shape": panel._mask_shape_section,
            "range": panel._mask_range_section,
            "brush": panel._mask_brush_section,
        }
        return {name for name, section in names.items() if not section.isHidden()}

    def test_a_radial_shows_shape_but_not_range_or_brush(self) -> None:
        panel = _panel()
        _fake_mask(panel, "mask-001", "radial")
        self.assertEqual({"shape"}, self._visible_sections(panel))

    def test_a_luminance_range_shows_range(self) -> None:
        panel = _panel()
        _fake_mask(panel, "mask-001", "bitmap", uiStyle="luminance-range")
        self.assertEqual({"range"}, self._visible_sections(panel))
        self.assertFalse(panel.luminance_range_controls.isHidden())
        self.assertTrue(panel.color_refine_row.isHidden())

    def test_a_painted_brush_mask_shows_brush_settings(self) -> None:
        panel = _panel()
        _fake_mask(panel, "mask-001", "bitmap", uiStyle="brush")
        self.assertEqual({"brush"}, self._visible_sections(panel))
        for row in (
            panel.brush_size_row,
            panel.brush_feather_row,
            panel.brush_density_row,
            panel.brush_flow_row,
        ):
            self.assertFalse(row.isHidden())

    def test_a_color_range_shows_only_refine(self) -> None:
        panel = _panel()
        _fake_mask(panel, "mask-001", "bitmap", uiStyle="color-range")
        self.assertEqual({"range"}, self._visible_sections(panel))
        self.assertTrue(panel.luminance_range_controls.isHidden())
        self.assertFalse(panel.color_refine_row.isHidden())

    def test_a_semantic_mask_keeps_touchup_out_of_the_adjustment_pane(self) -> None:
        panel = _panel()
        _fake_mask(panel, "mask-001", "subject-select")
        self.assertEqual(set(), self._visible_sections(panel))
        self.assertFalse(hasattr(panel, "edge_detection_radius_spin"))
        self.assertFalse(panel.overlay_menu_button.isHidden())

    def test_touchup_page_replaces_editor_and_ok_restores_it(self) -> None:
        panel = _panel()
        _fake_mask(panel, "mask-001", "subject-select")
        page = panel.open_mask_touchup("mask-001")
        self.assertIs(page, panel._mask_touchup_page)
        self.assertFalse(panel._mask_touchup_page.isHidden())
        for widget in (
            panel._editor_tool_rail,
            panel.editor_stack,
            panel._editor_footer,
        ):
            self.assertTrue(widget.isHidden())

        panel._mask_touchup_spins["edgeDetectionRadius"].setValue(12.5)
        panel._mask_touchup_spins["edgeSmooth"].setValue(18)
        panel._mask_touchup_spins["edgeFeather"].setValue(4.5)
        panel._mask_touchup_spins["edgeContrast"].setValue(22)
        panel._mask_touchup_spins["edgeShift"].setValue(-15)

        params = panel._selected_mask_dict()["params"]
        self.assertEqual(12.5, params["edgeDetectionRadius"])
        self.assertEqual(18, params["edgeSmooth"])
        self.assertEqual(4.5, params["edgeFeather"])
        self.assertEqual(22, params["edgeContrast"])
        self.assertEqual(-15, params["edgeShift"])
        # OK sits below the Refine Edges row and the Clear/Invert row.
        self.assertEqual(
            6,
            panel._mask_touchup_page.layout().indexOf(
                panel.mask_touchup_ok_button.parentWidget()
            ),
        )
        panel.mask_touchup_ok_button.click()
        self.assertIsNone(panel._mask_touchup_mask_id)
        self.assertTrue(panel._mask_touchup_page.isHidden())
        for widget in (
            panel._editor_tool_rail,
            panel.editor_stack,
            panel._editor_footer,
        ):
            self.assertFalse(widget.isHidden())

    def test_cancelling_touchup_restores_original_values(self) -> None:
        panel = _panel()
        _fake_mask(panel, "mask-001", "subject-select")
        panel._selected_mask_dict()["params"] = {"edgeSmooth": 12}
        page = panel.open_mask_touchup("mask-001")
        self.assertIsNotNone(page)
        panel._mask_touchup_spins["edgeSmooth"].setValue(44)
        panel._mask_touchup_spins["edgeShift"].setValue(-20)

        panel.mask_touchup_cancel_button.click()

        self.assertEqual({"edgeSmooth": 12}, panel._selected_mask_dict()["params"])
        self.assertIsNone(panel._mask_touchup_mask_id)
        self.assertEqual("Cancel", panel.mask_touchup_cancel_button.text())
        self.assertEqual(72, panel.mask_touchup_cancel_button.width())

    def test_cancelling_click_select_discards_the_session_mask(self) -> None:
        panel = _panel()
        _fake_mask(panel, "mask-001", "subject-select")
        panel._prompt_session_active = True
        panel._prompt_session_root_id = "mask-001"
        panel._mask_touchup_mask_id = "mask-001"
        panel._prompt_meta = {"mask-001": {"points": [(0.5, 0.5)]}}
        panel._ensure_session = lambda: (Path("session.json"), panel._session)  # type: ignore[method-assign]
        panel._write_session = lambda session, status: None  # type: ignore[method-assign]

        panel.mask_touchup_cancel_button.click()

        self.assertEqual([], panel._session["masks"])
        self.assertEqual({}, panel._prompt_meta)
        self.assertFalse(panel._prompt_session_active)
        self.assertFalse(panel._point_select_active)

    def test_touchup_page_keeps_overlay_controls_available(self) -> None:
        panel = _panel()
        _fake_mask(panel, "mask-001", "subject-select")
        panel.open_mask_touchup("mask-001")

        self.assertFalse(panel.touchup_overlay_check.isHidden())
        self.assertFalse(panel.touchup_overlay_menu_button.isHidden())
        self.assertFalse(panel.touchup_overlay_menu_button.icon().isNull())

    def test_touchup_clear_is_reversible_and_invert_is_persisted(self) -> None:
        panel = _panel()
        _fake_mask(panel, "mask-001", "subject-select")
        panel.open_mask_touchup("mask-001")

        panel.mask_touchup_clear_button.click()
        params = panel._selected_mask_dict()["params"]
        self.assertTrue(params["selectionCleared"])
        self.assertEqual("Restore Selection", panel.mask_touchup_clear_button.text())
        panel.mask_touchup_clear_button.click()
        self.assertFalse(params["selectionCleared"])
        self.assertEqual("Clear Selection", panel.mask_touchup_clear_button.text())

        panel.mask_touchup_invert_button.click()
        self.assertTrue(params["invert"])
        self.assertTrue(panel.mask_touchup_invert_button.isChecked())

    def test_feather_slider_allocates_one_third_to_each_pixel_decade(self) -> None:
        panel = _panel()
        feather_spin = panel._mask_touchup_spins["edgeFeather"]
        slider = panel.mask_touchup_feather_slider

        slider.setValue(1000)
        self.assertAlmostEqual(10.0, feather_spin.value(), places=1)
        slider.setValue(2000)
        self.assertAlmostEqual(100.0, feather_spin.value(), places=1)
        slider.setValue(3000)
        self.assertAlmostEqual(1000.0, feather_spin.value(), places=1)
        feather_spin.setValue(10.0)
        self.assertEqual(1000, slider.value())
        feather_spin.setValue(100.0)
        self.assertEqual(2000, slider.value())

    def test_saved_feather_value_restores_the_slider_position(self) -> None:
        panel = _panel()
        _fake_mask(panel, "mask-001", "subject-select")
        panel._selected_mask_dict()["params"]["edgeFeather"] = 1000.0

        panel.open_mask_touchup("mask-001")

        self.assertEqual(1000.0, panel._mask_touchup_spins["edgeFeather"].value())
        self.assertEqual(3000, panel.mask_touchup_feather_slider.value())

    def test_no_mask_leaves_every_contextual_section_hidden(self) -> None:
        panel = _panel()
        panel._sync_mask_controls(None)
        self.assertEqual(set(), self._visible_sections(panel))


class MaskEditModeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = QApplication.instance() or QApplication([])

    def test_double_clicking_an_existing_brush_arms_painting(self) -> None:
        panel = _panel()
        _fake_mask(panel, "mask-001", "bitmap", uiStyle="brush")
        panel._begin_mask_edit("mask-001")
        state = panel.mask_overlay_state()
        self.assertEqual("mask-001", panel._active_mask_edit_id)
        self.assertEqual("add", state["brush_mode"])
        self.assertFalse(state["scene_pick"])

    def test_double_clicking_a_shape_disables_semantic_scene_picking(self) -> None:
        panel = _panel()
        _fake_mask(panel, "mask-001", "radial")
        panel._begin_mask_edit("mask-001")
        state = panel.mask_overlay_state()
        self.assertEqual("mask-001", panel._active_mask_edit_id)
        self.assertIsNone(state["brush_mode"])
        self.assertFalse(state["scene_pick"])


class MaskAdjustmentGroupingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = QApplication.instance() or QApplication([])

    def test_local_adjustments_keep_every_key_and_exclude_vignette(self) -> None:
        from image_triage.ui.photo_editor_panel import MASK_ADJUSTMENT_KEYS

        panel = _panel()
        self.assertEqual(set(MASK_ADJUSTMENT_KEYS), set(panel._mask_rows))
        self.assertNotIn("vignette", panel._mask_rows)


class MaskPaneFootprintTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = QApplication.instance() or QApplication([])

    def _bodies(self, panel) -> dict[str, object]:
        bodies: dict[str, object] = {}
        for index, name in ((panel.MASK_PANE_WORK, "work"), (panel.MASK_PANE_CREATE, "create")):
            bodies[name] = panel.mask_stack.widget(index).widget()
        assert all(not isinstance(body, QScrollArea) for body in bodies.values())
        return bodies

    def test_no_pane_is_wider_than_the_editor_column(self) -> None:
        # The old flat tab needed 464px in a ~344px pane, so the right-hand
        # controls sat off screen with horizontal scrolling switched off.
        panel = _panel()
        _fake_mask(panel, "mask-001", "radial")
        for name, body in self._bodies(panel).items():
            with self.subTest(pane=name):
                self.assertLessEqual(body.minimumSizeHint().width(), 344)

    def test_the_create_pane_fits_a_short_column(self) -> None:
        panel = _panel()
        create = panel.mask_stack.widget(panel.MASK_PANE_CREATE).widget()
        self.assertLess(create.sizeHint().height(), 640)


class EditorFooterTests(unittest.TestCase):
    """The footer belongs to the page: Adjust saves, a mask finishes, and every
    other page leaves the strip free."""

    def setUp(self) -> None:
        self.app = QApplication.instance() or QApplication([])

    def test_adjust_keeps_save_and_hides_the_mask_actions(self) -> None:
        panel = _panel()
        panel._set_editor_page(panel.PAGE_ADJUST)
        self.assertFalse(panel._editor_footer.isHidden())
        self.assertFalse(panel.save_button.isHidden())
        self.assertTrue(panel.mask_done_button.isHidden())
        self.assertTrue(panel.delete_mask_button.isHidden())

    def test_the_other_tool_pages_have_no_footer(self) -> None:
        panel = _panel()
        # Crop keeps a footer of its own (Reset crop / Apply crop).
        for page in (
            panel.PAGE_REMOVE,
            panel.PAGE_RED_EYE,
            panel.PAGE_BACKGROUND,
            panel.PAGE_LENS_BLUR,
            panel.PAGE_PRESETS,
        ):
            with self.subTest(page=page):
                panel._set_editor_page(page)
                self.assertTrue(panel._editor_footer.isHidden())

    def test_the_mask_overview_has_no_footer_either(self) -> None:
        panel = _panel()
        panel._set_editor_page(panel.PAGE_MASKS)
        self.assertTrue(panel._editor_footer.isHidden())

    def test_inside_a_mask_the_slots_become_reset_done_delete(self) -> None:
        panel = _panel()
        panel._set_editor_page(panel.PAGE_MASKS)
        _fake_mask(panel, "mask-001", "radial")
        self.assertFalse(panel._editor_footer.isHidden())
        for button in (
            panel.reset_mask_adjustments_button,
            panel.mask_done_button,
            panel.delete_mask_button,
        ):
            with self.subTest(label=button.text()):
                self.assertFalse(button.isHidden())
        for button in (panel.reset_button, panel.save_button, panel.save_copy_button):
            with self.subTest(label=button.text()):
                self.assertTrue(button.isHidden())

    def test_done_leaves_the_mask_for_the_overview(self) -> None:
        panel = _panel()
        panel._set_editor_page(panel.PAGE_MASKS)
        _fake_mask(panel, "mask-001", "radial")
        panel.mask_done_button.click()
        self.assertIsNone(panel._selected_mask_id())
        self.assertTrue(panel._mask_overview.isVisibleTo(panel))
        self.assertTrue(panel._editor_footer.isHidden())

    def test_the_relocated_actions_left_the_scrolling_detail(self) -> None:
        panel = _panel()
        for button in (panel.reset_mask_adjustments_button, panel.delete_mask_button):
            with self.subTest(label=button.text()):
                self.assertFalse(panel._mask_detail.isAncestorOf(button))
                self.assertTrue(panel._editor_footer.isAncestorOf(button))


class MaskOverviewTests(unittest.TestCase):
    """With no mask open the Work pane is a layer overview; opening a layer puts
    you inside that mask, and "All masks" steps back out."""

    def setUp(self) -> None:
        self.app = QApplication.instance() or QApplication([])

    def _panel_with_layers(self):
        panel = _panel()
        panel._session = {
            "masks": [
                {"id": "mask-001", "type": "radial", "coordinateSpaceId": "space",
                 "params": {"cx": 50, "cy": 50, "rx": 20, "ry": 20}},
                {"id": "mask-002", "type": "linear-gradient", "coordinateSpaceId": "space",
                 "params": {"x1": 0, "y1": 0, "x2": 100, "y2": 100}},
                {"id": "mask-003", "type": "radial", "coordinateSpaceId": "space",
                 "parentId": "mask-002", "combine": "subtract",
                 "params": {"cx": 20, "cy": 20, "rx": 10, "ry": 10}},
            ],
            "operations": [],
            "coordinateSpaces": [{"id": "space", "sourceWidth": 100, "sourceHeight": 100}],
        }
        for mask_id in ("mask-001", "mask-002", "mask-003"):
            item = QListWidgetItem(mask_id)
            item.setData(Qt.ItemDataRole.UserRole, mask_id)
            panel.masks_list.addItem(item)
        panel._ensure_session = lambda: (Path("session.json"), panel._session)  # type: ignore[method-assign]
        panel._write_session = (  # type: ignore[method-assign]
            lambda session, status: panel._sync_mask_controls(panel._selected_mask_dict())
        )
        panel._sync_mask_controls(None)
        return panel

    @staticmethod
    def _live(panel, name: str) -> list:
        from PySide6.QtWidgets import QWidget

        # Rebuilt cards are hidden and deleteLater'd, so skip the hidden ones.
        return [w for w in panel._mask_overview.findChildren(QWidget, name) if not w.isHidden()]

    def test_opening_a_photo_shows_one_card_per_mask_group(self) -> None:
        panel = self._panel_with_layers()
        self.assertEqual(2, len(self._live(panel, "maskLayerCard")))
        self.assertTrue(panel._mask_overview.isVisibleTo(panel))
        self.assertFalse(panel._mask_detail.isVisibleTo(panel))
        self.assertFalse(panel.mask_list_viewport.isVisibleTo(panel))

    def test_opening_a_layer_goes_inside_that_mask(self) -> None:
        panel = self._panel_with_layers()
        panel._enter_mask("mask-002")
        self.assertEqual("mask-002", panel._selected_mask_id())
        self.assertFalse(panel._mask_overview.isVisibleTo(panel))
        self.assertTrue(panel._mask_detail.isVisibleTo(panel))
        self.assertTrue(panel.mask_back_button.isVisibleTo(panel))

    def test_all_masks_returns_to_the_overview_with_that_layer_open(self) -> None:
        panel = self._panel_with_layers()
        panel._enter_mask("mask-003")
        panel.mask_back_button.click()
        self.assertIsNone(panel._selected_mask_id())
        self.assertTrue(panel._mask_overview.isVisibleTo(panel))
        self.assertEqual("mask-002", panel._expanded_layer_id())
        # The expanded group lists its base and its subtracting component.
        self.assertEqual(2, len(self._live(panel, "maskComponentRow")))

    def test_all_masks_sits_on_the_overlay_row_not_in_the_caption(self) -> None:
        panel = self._panel_with_layers()
        panel._enter_mask("mask-002")
        panel.resize(380, 900)
        panel.show()
        self.app.processEvents()
        back = panel.mask_back_button
        # Same row as Show overlay, left of it; Show overlay beside its settings.
        self.assertIs(back.parentWidget(), panel.overlay_check.parentWidget())
        self.assertLess(back.geometry().x(), panel.overlay_check.geometry().x())
        self.assertLess(
            panel.overlay_check.geometry().x(), panel.overlay_menu_button.geometry().x()
        )
        self.assertEqual(back.geometry().center().y(), panel.overlay_check.geometry().center().y())
        panel.close()

    def test_hiding_a_layer_drops_it_from_the_composite_but_keeps_its_edits(self) -> None:
        from photo_terminal.adjustments import EditRecipe

        from image_triage.ui.photo_editor_panel import recipe_for_mask, replace_mask_operations

        panel = self._panel_with_layers()
        replace_mask_operations(panel._session, "mask-001", EditRecipe(exposure=0.5))
        shown = len(panel.masked_adjustments())
        panel.set_mask_layer_visible("mask-001", False)
        self.assertEqual(shown - 1, len(panel.masked_adjustments()))
        self.assertEqual(0.5, recipe_for_mask(panel._session, "mask-001").exposure)
        panel.set_mask_layer_visible("mask-001", True)
        self.assertEqual(shown, len(panel.masked_adjustments()))
        self.assertNotIn("enabled", panel._session["masks"][0])

    def test_only_components_switch_between_add_and_subtract(self) -> None:
        panel = self._panel_with_layers()
        panel.set_mask_component_combine("mask-003", "add")
        self.assertNotIn("combine", panel._mask_by_id("mask-003"))
        panel.set_mask_component_combine("mask-003", "subtract")
        self.assertEqual("subtract", panel._mask_by_id("mask-003")["combine"])
        panel.set_mask_component_combine("mask-002", "subtract")
        self.assertNotIn("combine", panel._mask_by_id("mask-002"))

    def test_an_expanded_overview_fits_the_editor_column(self) -> None:
        panel = self._panel_with_layers()
        panel._toggle_overview_layer("mask-002")
        body = panel.mask_stack.widget(panel.MASK_PANE_WORK).widget()
        self.assertLessEqual(body.minimumSizeHint().width(), 344)


if __name__ == "__main__":
    unittest.main()
