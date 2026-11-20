"""The viewer (and the RAW editor in it) in a window of its own, as Camera Raw beside Photoshop: the button in its top bar moves it out
into a movable, resizable window and back; while it is out the library stays usable and the main window is not made full screen;
closing the viewer hides its window; whether it is out, and where the window was, are kept."""
from PySide6.QtCore import QObject, QPointF, Qt
from PySide6.QtTest import QTest

from test_qml_shell_viewer import shell, qapp, pump  # noqa: F401


def press(window, item, qapp):
    point = item.mapToScene(QPointF(item.width() / 2, item.height() / 2)).toPoint()
    QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, point)
    pump(qapp, .3)


def test_the_viewer_moves_into_its_own_window_and_back(shell, qapp):
    engine, window, photos, popout, warnings = shell
    memory = engine.rootContext().contextProperty("uiMemory")
    viewer = window.findChild(QObject, "viewer")
    detached_window = window.findChild(QObject, "viewerWindow")
    popout.open(1); pump(qapp, .4)
    assert viewer.property("shown") and not detached_window.property("visible")
    assert window.property("wantsFullscreen")                                      # docked: over the library, full screen
    press(window, window.findChild(QObject, "viewerDetachButton"), qapp)
    assert detached_window.property("visible") and viewer.parentItem() == detached_window.contentItem()
    assert not window.property("wantsFullscreen") and not window.property("viewerCovers")
    assert memory.get("viewerDetached", False) is True
    detached_window.setWidth(900); detached_window.setHeight(700); detached_window.setX(40); detached_window.setY(60)
    pump(qapp, .8)
    kept = memory.get("viewerWindow", None)
    assert kept and (kept["w"], kept["h"]) == (900, 700)
    popout.close(); pump(qapp, .3)                                                  # closing the viewer hides its window
    assert not detached_window.property("visible")
    popout.open(2); pump(qapp, .4)                                                  # and opening a photo shows it again, out
    assert detached_window.property("visible") and viewer.property("shown")
    press(detached_window, window.findChild(QObject, "viewerDetachButton"), qapp)  # back into the main window
    assert not detached_window.property("visible") and viewer.parentItem() == window.contentItem()
    assert window.property("wantsFullscreen") and memory.get("viewerDetached", True) is False
    popout.close(); pump(qapp, .2)
    assert not [w for w in warnings if "Viewer" in w], warnings
