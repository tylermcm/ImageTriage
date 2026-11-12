"""Middle-button autoscroll stays independent of selection and normal wheels."""
import sys

from PySide6.QtCore import QObject, QPoint, QPointF, Qt
from PySide6.QtGui import QGuiApplication, QWheelEvent
from PySide6.QtTest import QTest

from test_qml_shell import _load_main, pump, qapp, rows  # noqa: F401


def test_left_button_drag_cannot_scroll_the_grid(qapp, tmp_path):
    engine, window, warnings = _load_main(qapp, tmp_path)
    window.setWidth(1440)
    engine.rootContext().contextProperty("photos").setRows(rows(200), "/p")
    pump(qapp, .8)
    grid = window.findChild(QObject, "photoGrid")
    grid.setProperty("contentY", 1000)
    pump(qapp, .1)
    origin = grid.mapToScene(QPointF(grid.width()/2, 280)).toPoint()
    before = grid.property("contentY")
    for direction in (-1, 1):
        QTest.mousePress(window, Qt.LeftButton, Qt.NoModifier, origin)
        for distance in (25, 60, 100, 140):
            QTest.mouseMove(window, origin + QPoint(0, direction * distance))
            pump(qapp, .02)
        QTest.mouseRelease(window, Qt.LeftButton, Qt.NoModifier, origin + QPoint(0, direction * 140))
        pump(qapp, .2)
        assert grid.property("contentY") == before
        assert not grid.parentItem().property("autoScrolling")
    QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, origin)
    assert grid.property("currentIndex") > 0
    assert not [w for w in warnings if "PhotoGrid.qml" in w], warnings
    window.close()
    engine.deleteLater()
    pump(qapp, .2)


def test_middle_click_autoscroll_sticky_drag_cancel_and_bounds(qapp, tmp_path):
    engine, window, warnings = _load_main(qapp, tmp_path)
    window.setWidth(1440)
    window.setHeight(900)
    engine.rootContext().contextProperty("photos").setRows(rows(200), "/p")
    pump(qapp, .8)
    grid = window.findChild(QObject, "photoGrid")
    root = grid.parentItem()
    origin = grid.mapToScene(QPointF(grid.width()/2, 200)).toPoint()
    down = origin + QPoint(0, 90)
    selected = grid.property("currentIndex")
    for position in (origin, down, origin):
        QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, position)
        pump(qapp, .03)
        assert not root.property("autoScrolling")
    selected = grid.property("currentIndex")
    assert selected > 0  # Ordinary left clicks still select photos.
    QTest.mouseClick(window, Qt.MiddleButton, Qt.NoModifier, origin)
    pump(qapp, .05)
    assert root.property("autoScrolling")
    anchor = window.findChild(QObject, "gridAutoScrollAnchor")
    # The anchor mark is the reader-mode cursor Windows ships (scroll_cursor.py); elsewhere there is no artwork and it stays hidden.
    assert anchor.isVisible() == (sys.platform == "win32")
    initial = grid.property("contentY")
    pump(qapp, .08)
    assert grid.property("contentY") == initial  # Dead zone at the anchor.
    QTest.mouseMove(window, down)
    pump(qapp, .2)
    assert grid.property("contentY") > initial
    assert grid.property("currentIndex") == selected
    high = max(grid.property("originY"), grid.property("originY") + grid.property("contentHeight") - grid.height() + grid.property("bottomMargin"))
    grid.setProperty("contentY", high - 1)
    root.setProperty("scrollPointerY", 100000)
    pump(qapp, .05)
    assert grid.property("contentY") == high
    grid.setProperty("contentY", grid.property("originY") + 1)
    root.setProperty("scrollPointerY", -100000)
    pump(qapp, .05)
    assert grid.property("contentY") == grid.property("originY")
    QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, down)
    pump(qapp, .05)
    assert not root.property("autoScrolling")
    assert grid.property("currentIndex") == selected  # Cancel click is consumed.
    stopped = grid.property("contentY")
    pump(qapp, .08)
    assert grid.property("contentY") == stopped
    QTest.mouseClick(window, Qt.MiddleButton, Qt.NoModifier, origin)
    QTest.keyClick(window, Qt.Key_Escape)
    assert not root.property("autoScrolling")
    QTest.mousePress(window, Qt.MiddleButton, Qt.NoModifier, origin)
    QTest.mouseMove(window, down)
    pump(qapp, .1)
    QTest.mouseRelease(window, Qt.MiddleButton, Qt.NoModifier, down)
    assert not root.property("autoScrolling")  # Hold-drag ends on release.
    QTest.mouseClick(window, Qt.MiddleButton, Qt.NoModifier, origin)
    assert root.property("autoScrolling")
    root.setEnabled(False)  # Opening a modal must stop background autoscroll.
    assert not root.property("autoScrolling")
    root.setEnabled(True)
    before = grid.property("contentY")
    point = QPointF(origin)
    event = QWheelEvent(point, QPointF(window.mapToGlobal(origin)), QPoint(), QPoint(0, -120),
                        Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False)
    QGuiApplication.sendEvent(window, event)
    pump(qapp, .25)
    assert grid.property("contentY") > before
    assert not [warning for warning in warnings if "PhotoGrid.qml" in warning], warnings
    window.close()
    engine.deleteLater()
    pump(qapp, .2)
