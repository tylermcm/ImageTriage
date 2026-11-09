"""Real pointer gestures on the popout crop tool, without a desktop capture."""
import pytest
from PySide6.QtCore import QObject, QPointF, Qt
from PySide6.QtGui import QImage, QColor, QGuiApplication
from PySide6.QtTest import QTest

from test_qml_shell_viewer import shell, qapp, pump  # noqa: F401


def prepare(shell, qapp):
    engine, window, photos, popout, warnings = shell
    popout.open(0)
    raw = popout.rawEditor
    raw.open("sample.NEF")
    image = QImage(800, 500, QImage.Format_RGB32)
    image.fill(QColor("#bf843f"))
    frame = ({"histogram": [[0]*256]*3, "fullSize": [8000, 5000]}, image)
    raw._proxy_arrived(raw._revision, (frame, frame, 0, "sample.NEF"))
    dock = window.findChild(QObject, "viewerRawEditorPanel")
    dock.setProperty("tool", "Crop")
    pump(qapp, .7)
    crop = window.findChild(QObject, "rawCropOverlay")
    assert crop.isVisible() and crop.width() > 100
    return window, raw, dock, crop, warnings


def drag(window, crop, start, end, modifiers=Qt.NoModifier):
    a = crop.mapToScene(QPointF(start[0]*crop.width(), start[1]*crop.height())).toPoint()
    b = crop.mapToScene(QPointF(end[0]*crop.width(), end[1]*crop.height())).toPoint()
    QTest.mousePress(window, Qt.LeftButton, modifiers, a)
    pump(QGuiApplication.instance(), .03)
    QTest.mouseMove(window, b, 30)
    pump(QGuiApplication.instance(), .03)
    QTest.mouseRelease(window, Qt.LeftButton, modifiers, b)
    pump(QGuiApplication.instance(), .03)


def test_draw_ratio_ctrl_override_move_resize_and_preview(shell, qapp):
    window, raw, dock, crop, warnings = prepare(shell, qapp)
    raw.setCropRatio("1:1")
    # Start outside the inscribed square to draw a new rectangle.
    drag(window, crop, (.04, .1), (.5, .65))
    r = raw.values["hostCrop"]
    assert r["w"]*crop.width() == pytest.approx(r["h"]*crop.height(), abs=2)
    raw.resetCrop()
    raw.setCropRatio("16:9")
    QTest.keyPress(window, Qt.Key_Control)
    drag(window, crop, (.1, .02), (.6, .7), Qt.ControlModifier)
    QTest.keyRelease(window, Qt.Key_Control)
    r = raw.values["hostCrop"]
    assert r["w"] == pytest.approx(.5, abs=.01)
    assert r["h"] == pytest.approx(.68, abs=.01)
    assert r["ratio"] == "16:9"  # temporary override keeps the chosen preset
    history = len(raw.history)
    drag(window, crop, (.35, .3), (.45, .4))
    moved = raw.values["hostCrop"]
    assert moved["x"] == pytest.approx(r["x"]+.1, abs=.01)
    assert moved["w"] == pytest.approx(r["w"])
    assert len(raw.history) == history+1
    corner = (moved["x"]+moved["w"], moved["y"]+moved["h"])
    drag(window, crop, corner, (.85, .9))
    resized = raw.values["hostCrop"]
    assert resized["w"]*crop.width() / (resized["h"]*crop.height()) == pytest.approx(16/9, abs=.02)
    dock.setProperty("tool", "Adjust")
    pump(qapp, .3)
    photo = window.findChild(QObject, "viewerPhotoImage")
    # An image-provider source must actually clip, not just change model geometry.
    assert photo.property("paintedWidth")/photo.property("paintedHeight") == pytest.approx(16/9, abs=.02)
    assert not [w for w in warnings if "RawCropOverlay.qml" in w], warnings


def test_ctrl_mid_drag_and_escape_cancel(shell, qapp):
    window, raw, dock, crop, warnings = prepare(shell, qapp)
    raw.setCropRatio("1:1")
    a = crop.mapToScene(QPointF(.03*crop.width(), .05*crop.height())).toPoint()
    b = crop.mapToScene(QPointF(.5*crop.width(), .65*crop.height())).toPoint()
    QTest.mousePress(window, Qt.LeftButton, Qt.NoModifier, a)
    pump(QGuiApplication.instance(), .03)
    QTest.mouseMove(window, b, 30)
    pump(QGuiApplication.instance(), .03)
    pump(qapp, .03)
    assert crop.property("cw")*crop.width() == pytest.approx(crop.property("ch")*crop.height())
    QTest.keyPress(window, Qt.Key_Control)
    assert crop.property("cw") == pytest.approx(.47, abs=.01)
    assert crop.property("ch") == pytest.approx(.6, abs=.01)
    QTest.keyRelease(window, Qt.Key_Control)
    pump(qapp, .03)
    assert crop.property("cw")*crop.width() == pytest.approx(crop.property("ch")*crop.height())
    original = raw.values["hostCrop"]
    QTest.keyClick(window, Qt.Key_Escape)
    QTest.mouseRelease(window, Qt.LeftButton, Qt.NoModifier, b)
    assert raw.values["hostCrop"] == original and raw.active


def test_full_crop_handles_overflow_but_drag_stays_inside_photo(shell, qapp):
    window, raw, dock, crop, warnings = prepare(shell, qapp)
    items, pending = [], [crop]
    while pending:
        item = pending.pop()
        items.append(item)
        pending.extend(item.childItems())
    for x, y in ((0, 0), (1, 0), (0, 1), (1, 1)):
        handle = next(i for i in items if i.objectName() == f"rawCropHandle_{x}_{y}")
        point = handle.mapToItem(crop, QPointF())
        assert point.x() == pytest.approx(x*crop.width()-4)
        assert point.y() == pytest.approx(y*crop.height()-4)
    # Grab the part outside the image, then pull past its boundary.
    a = crop.mapToScene(QPointF(-2, -2)).toPoint()
    b = crop.mapToScene(QPointF(-25, -25)).toPoint()
    QTest.mousePress(window, Qt.LeftButton, Qt.NoModifier, a)
    pump(qapp, .03)
    assert crop.property("dragging")
    QTest.mouseMove(window, b, 30)
    QTest.mouseRelease(window, Qt.LeftButton, Qt.NoModifier, b)
    pump(qapp, .03)
    r = raw.values["hostCrop"]
    assert r["x"] >= 0 and r["y"] >= 0
    assert r["x"]+r["w"] <= 1 and r["y"]+r["h"] <= 1
