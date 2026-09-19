"""Scrolling the editor column must not drag the controls it passes over.

The wheel adjusts a slider, spin box or combo only after it has been clicked;
otherwise the event is ignored so the scroll area scrolls instead.
"""
from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication


def _panel():
    from image_triage.ui.photo_editor_panel import PhotoEditorPanel

    return PhotoEditorPanel()


def _wheel() -> QWheelEvent:
    return QWheelEvent(
        QPointF(5.0, 5.0),
        QPointF(5.0, 5.0),
        QPoint(0, 0),
        QPoint(0, -120),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )


class ScrollGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = QApplication.instance() or QApplication([])
        self.panel = _panel()

    def tearDown(self) -> None:
        self.panel.close()

    def test_an_unfocused_slider_lets_the_wheel_through(self) -> None:
        slider = self.panel._mask_rows["exposure"].slider
        slider.setValue(0)
        event = _wheel()
        slider.wheelEvent(event)
        # Ignored, so the scroll area — not the photo — gets the wheel.
        self.assertFalse(event.isAccepted())
        self.assertEqual(0, slider.value())

    def test_a_clicked_slider_still_takes_the_wheel(self) -> None:
        slider = self.panel._mask_rows["exposure"].slider
        slider.setValue(0)
        slider.hasFocus = lambda: True  # type: ignore[method-assign]
        event = _wheel()
        slider.wheelEvent(event)
        self.assertTrue(event.isAccepted())
        self.assertNotEqual(0, slider.value())

    def test_an_unfocused_value_box_lets_the_wheel_through(self) -> None:
        box = self.panel._mask_rows["exposure"].value_box
        box.setValue(0.0)
        event = _wheel()
        box.wheelEvent(event)
        self.assertFalse(event.isAccepted())
        self.assertEqual(0.0, box.value())

    def test_an_unfocused_combo_keeps_its_selection(self) -> None:
        combo = self.panel.crop_aspect_combo
        combo.setCurrentIndex(0)
        event = _wheel()
        combo.wheelEvent(event)
        self.assertFalse(event.isAccepted())
        self.assertEqual(0, combo.currentIndex())

    def test_controls_can_be_focused_by_clicking(self) -> None:
        # A NoFocus control could never opt in, and Qt's WheelFocus default on
        # spin boxes and combos would let a passing tick take focus and edit.
        for widget in (
            self.panel._mask_rows["exposure"].slider,
            self.panel.background_blur_slider,
            self.panel.lensblur_amount_slider,
            self.panel.lensblur_focus_slider,
            self.panel._mask_rows["exposure"].value_box,
            self.panel.crop_aspect_combo,
        ):
            with self.subTest(widget=widget.objectName() or type(widget).__name__):
                policy = widget.focusPolicy()
                self.assertNotEqual(Qt.FocusPolicy.NoFocus, policy)
                self.assertNotEqual(Qt.FocusPolicy.WheelFocus, policy)
                self.assertTrue(bool(policy & Qt.FocusPolicy.ClickFocus))

    def test_the_brush_size_row_slider_is_guarded_too(self) -> None:
        from PySide6.QtWidgets import QSlider

        slider = self.panel.brush_size_row.findChild(QSlider)
        slider.setValue(25)
        event = _wheel()
        slider.wheelEvent(event)
        self.assertFalse(event.isAccepted())
        self.assertEqual(25, slider.value())


if __name__ == "__main__":
    unittest.main()
