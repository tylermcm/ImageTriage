from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QApplication

from image_triage.grid import ThumbnailGridView


def _ensure_app() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


class GridAutoscrollTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        _ensure_app()

    def _make_view(self) -> ThumbnailGridView:
        view = ThumbnailGridView(MagicMock())
        view.resize(400, 400)
        view.verticalScrollBar().setRange(0, 5000)
        view.verticalScrollBar().setValue(1000)
        return view

    def test_middle_click_starts_and_click_stops(self) -> None:
        view = self._make_view()
        self.assertFalse(view._autoscroll_active)
        view._start_autoscroll(QPoint(200, 200))
        self.assertTrue(view._autoscroll_active)
        self.assertTrue(view._autoscroll_timer.isActive())
        view._stop_autoscroll()
        self.assertFalse(view._autoscroll_active)
        self.assertFalse(view._autoscroll_timer.isActive())
        view.deleteLater()

    def test_deadzone_does_not_scroll(self) -> None:
        view = self._make_view()
        view._start_autoscroll(QPoint(200, 200))
        view._autoscroll_pointer_y = 200 + view._AUTOSCROLL_DEADZONE - 1
        before = view.verticalScrollBar().value()
        view._autoscroll_tick()
        self.assertEqual(before, view.verticalScrollBar().value())
        view.deleteLater()

    def test_direction_and_farther_is_faster(self) -> None:
        view = self._make_view()
        view._start_autoscroll(QPoint(200, 200))
        sb = view.verticalScrollBar()

        # Small downward push scrolls down (positive delta).
        sb.setValue(1000)
        view._autoscroll_pointer_y = 400
        view._autoscroll_tick()
        near_delta = sb.value() - 1000
        self.assertGreater(near_delta, 0)

        # A farther downward push scrolls faster.
        sb.setValue(1000)
        view._autoscroll_pointer_y = 200 + 1000
        view._autoscroll_tick()
        far_delta = sb.value() - 1000
        self.assertGreater(far_delta, near_delta)

        # Upward push scrolls up (negative delta).
        sb.setValue(3000)
        view._autoscroll_pointer_y = 50
        view._autoscroll_tick()
        self.assertLess(sb.value() - 3000, 0)
        view.deleteLater()

    def test_speed_is_capped(self) -> None:
        view = self._make_view()
        view._start_autoscroll(QPoint(200, 200))
        sb = view.verticalScrollBar()
        sb.setValue(0)
        view._autoscroll_pointer_y = 200 + 100000  # absurd distance
        view._autoscroll_tick()
        self.assertLessEqual(sb.value(), view._AUTOSCROLL_MAX_STEP)
        view.deleteLater()


if __name__ == "__main__":
    unittest.main()
