from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication, QWidget

from image_triage.preview import FullScreenPreview


def _right_event(event_type: QMouseEvent.Type, position) -> QMouseEvent:
    return QMouseEvent(
        event_type,
        QPointF(position),
        Qt.MouseButton.RightButton,
        Qt.MouseButton.RightButton,
        Qt.KeyboardModifier.NoModifier,
    )


def _center_in_dialog(widget: QWidget, dialog: QWidget):
    return widget.mapTo(dialog, widget.rect().center())


class PreviewRightClickCloseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = QApplication.instance() or QApplication([])
        self.preview = FullScreenPreview()
        self.preview.resize(1280, 800)
        self.preview.show()
        self.app.processEvents()

    def tearDown(self) -> None:
        self.preview.close()
        self.app.processEvents()

    def test_studio_ui_surfaces_are_not_close_targets(self) -> None:
        for surface in (
            self.preview._studio_toolbar,
            self.preview._studio_rail,
            self.preview._filmstrip,
        ):
            with self.subTest(surface=surface.objectName()):
                point = _center_in_dialog(surface, self.preview)
                self.assertFalse(self.preview._right_click_closes_at(point))

    def test_passive_stage_remains_a_close_target(self) -> None:
        point = _center_in_dialog(self.preview.content_widget, self.preview)
        self.assertTrue(self.preview._right_click_closes_at(point))

    def test_right_clicking_toolbar_does_not_arm_or_close_dialog(self) -> None:
        point = _center_in_dialog(self.preview._studio_toolbar, self.preview)
        self.preview.mousePressEvent(
            _right_event(QMouseEvent.Type.MouseButtonPress, point)
        )
        self.assertFalse(self.preview._pending_right_close)
        self.preview.mouseReleaseEvent(
            _right_event(QMouseEvent.Type.MouseButtonRelease, point)
        )
        self.assertTrue(self.preview.isVisible())

    def test_passive_background_right_click_still_closes(self) -> None:
        point = _center_in_dialog(self.preview.content_widget, self.preview)
        self.preview.mousePressEvent(
            _right_event(QMouseEvent.Type.MouseButtonPress, point)
        )
        self.assertTrue(self.preview._pending_right_close)
        self.preview.mouseReleaseEvent(
            _right_event(QMouseEvent.Type.MouseButtonRelease, point)
        )
        self.assertFalse(self.preview.isVisible())


if __name__ == "__main__":
    unittest.main()
