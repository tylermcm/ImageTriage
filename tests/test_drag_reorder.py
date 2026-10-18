"""DragReorder: lifting a button, the neighbours sliding aside, the drop reported once, and plain clicks left alone."""
from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMenu, QToolButton, QWidget

from image_triage.ui.drag_reorder import DragReorder


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _mouse(widget: QWidget, kind: QEvent.Type, global_at: QPoint, *, held: bool) -> None:
    """Send a mouse event at a fixed screen position (the widget itself moves while it is dragged)."""
    event = QMouseEvent(
        kind,
        QPointF(widget.mapFromGlobal(global_at)),
        QPointF(global_at),
        Qt.MouseButton.LeftButton if kind != QEvent.Type.MouseMove else Qt.MouseButton.NoButton,
        Qt.MouseButton.LeftButton if held else Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
    )
    QApplication.sendEvent(widget, event)


def _drag(button: QToolButton, dx: int, dy: int = 0) -> None:
    origin = button.mapToGlobal(button.rect().center())
    _mouse(button, QEvent.Type.MouseButtonPress, origin, held=True)
    steps = 6
    for step in range(1, steps + 1):
        _mouse(button, QEvent.Type.MouseMove, origin + QPoint(dx * step // steps, dy * step // steps), held=True)
    _mouse(button, QEvent.Type.MouseButtonRelease, origin + QPoint(dx, dy), held=False)


class _Row(QWidget):
    """Four 40x30 buttons at x = 0, 50, 100, 150 (a 10px gap), or the same stacked vertically."""

    def __init__(self, orientation: Qt.Orientation) -> None:
        super().__init__()
        self.resize(200, 200)
        self.buttons: dict[str, QToolButton] = {}
        for index, key in enumerate("abcd"):
            button = QToolButton(self)
            button.setText(key)
            button.setFixedSize(40, 30)
            button.move(index * 50, 10) if orientation == Qt.Orientation.Horizontal else button.move(10, index * 40)
            self.buttons[key] = button
        self.clicks: list[str] = []
        for key, button in self.buttons.items():
            button.clicked.connect(lambda _c=False, k=key: self.clicks.append(k))
        self.reorder = DragReorder(orientation, self)
        self.reorder.set_items(list(self.buttons.items()))
        self.committed: list[list[str]] = []
        self.reorder.order_committed.connect(self.committed.append)
        self.show()


class DragReorderTests(unittest.TestCase):
    def setUp(self) -> None:
        _app()

    def test_dropping_on_a_later_slot_reports_the_new_order_and_slides_the_neighbours(self) -> None:
        row = _Row(Qt.Orientation.Horizontal)
        _drag(row.buttons["a"], 110)
        QTest.qWait(400)
        self.assertEqual([["b", "c", "a", "d"]], row.committed)
        self.assertEqual(0, row.buttons["b"].x(), "b slid into a's old slot")
        self.assertEqual(50, row.buttons["c"].x(), "c slid into b's old slot")
        self.assertEqual(100, row.buttons["a"].x(), "the dragged button settled in its new slot")
        self.assertEqual(150, row.buttons["d"].x(), "d was never in the way")
        self.assertEqual([], row.clicks, "a drag is not a click")

    def test_dropping_on_an_earlier_slot(self) -> None:
        row = _Row(Qt.Orientation.Horizontal)
        _drag(row.buttons["d"], -60)
        QTest.qWait(400)
        self.assertEqual([["a", "b", "d", "c"]], row.committed)

    def test_a_short_press_is_still_a_click(self) -> None:
        row = _Row(Qt.Orientation.Horizontal)
        QTest.mouseClick(row.buttons["b"], Qt.MouseButton.LeftButton)
        self.assertEqual(["b"], row.clicks)
        self.assertEqual([], row.committed)

    def test_dragging_and_dropping_back_where_it_was_changes_nothing(self) -> None:
        row = _Row(Qt.Orientation.Horizontal)
        _drag(row.buttons["b"], 12)
        QTest.qWait(300)
        self.assertEqual([], row.committed)
        self.assertEqual([50, 0, 100, 150], [row.buttons["b"].x(), row.buttons["a"].x(), row.buttons["c"].x(), row.buttons["d"].x()])

    def test_the_dragged_button_stays_inside_the_row(self) -> None:
        row = _Row(Qt.Orientation.Horizontal)
        a = row.buttons["a"]
        origin = a.mapToGlobal(a.rect().center())
        _mouse(a, QEvent.Type.MouseButtonPress, origin, held=True)
        _mouse(a, QEvent.Type.MouseMove, origin + QPoint(900, 0), held=True)
        self.assertEqual(150, a.x(), "clamped to the last slot")
        _mouse(a, QEvent.Type.MouseMove, origin + QPoint(-900, 0), held=True)
        self.assertEqual(0, a.x(), "clamped to the first slot")
        _mouse(a, QEvent.Type.MouseButtonRelease, origin + QPoint(-900, 0), held=False)
        QTest.qWait(300)

    def test_a_vertical_column_reorders_along_y(self) -> None:
        row = _Row(Qt.Orientation.Vertical)
        _drag(row.buttons["a"], 0, 85)
        QTest.qWait(400)
        self.assertEqual([["b", "c", "a", "d"]], row.committed)
        self.assertEqual([0, 40, 80, 120], [row.buttons[key].y() for key in "bcad"])
        self.assertTrue(all(button.x() == 10 for button in row.buttons.values()), "the cross axis never moves")

    def test_a_disabled_reorder_leaves_presses_alone(self) -> None:
        row = _Row(Qt.Orientation.Horizontal)
        row.reorder._enabled = lambda: False
        _drag(row.buttons["a"], 110)
        QTest.qWait(300)
        self.assertEqual([], row.committed)
        self.assertEqual(0, row.buttons["a"].x())

    def test_a_menu_button_still_opens_its_menu_on_a_plain_click_and_can_be_dragged(self) -> None:
        row = _Row(Qt.Orientation.Horizontal)
        button = row.buttons["b"]
        menu = QMenu(button)
        menu.addAction("one")
        button.setMenu(menu)
        button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        opened: list[bool] = []
        # showMenu() runs a nested event loop until the menu closes, so stand in for it.
        button.showMenu = lambda: opened.append(True)
        origin = button.mapToGlobal(button.rect().center())
        _mouse(button, QEvent.Type.MouseButtonPress, origin, held=True)
        self.assertEqual([], opened, "the menu waits for the release, so a drag can start instead")
        _mouse(button, QEvent.Type.MouseButtonRelease, origin, held=False)
        QTest.qWait(50)
        self.assertEqual([True], opened)
        _drag(button, 60)
        QTest.qWait(400)
        self.assertEqual([True], opened, "a drag opens no menu")
        self.assertEqual([["a", "c", "b", "d"]], row.committed)

    def test_a_disabled_button_can_be_dragged_even_though_it_gets_no_moves(self) -> None:
        row = _Row(Qt.Orientation.Horizontal)
        button = row.buttons["a"]
        button.setEnabled(False)
        origin = button.mapToGlobal(button.rect().center())
        _mouse(button, QEvent.Type.MouseButtonPress, origin, held=True)
        for step in range(1, 7):
            # The moves land on the row behind it, as they do in the real application.
            _mouse(row, QEvent.Type.MouseMove, origin + QPoint(110 * step // 6, 0), held=True)
        _mouse(row, QEvent.Type.MouseButtonRelease, origin + QPoint(110, 0), held=False)
        QTest.qWait(400)
        self.assertEqual([["b", "c", "a", "d"]], row.committed)

    def test_new_items_replace_the_old_ones(self) -> None:
        row = _Row(Qt.Orientation.Horizontal)
        row.reorder.set_items([("b", row.buttons["b"]), ("c", row.buttons["c"])])
        _drag(row.buttons["a"], 110)  # a is no longer draggable
        QTest.qWait(300)
        self.assertEqual([], row.committed)
        self.assertEqual(["b", "c"], row.reorder.keys())


if __name__ == "__main__":
    unittest.main()
