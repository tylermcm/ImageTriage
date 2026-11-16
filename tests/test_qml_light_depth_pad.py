import numpy as np
import pytest
from PySide6.QtCore import QObject, QPointF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QImage
from PySide6.QtQuick import QQuickItem
from PySide6.QtTest import QTest

from image_triage.qml_shell.raw_light_depth import RawLightDepth
from test_qml_shell_viewer import shell, qapp, pump  # noqa: F401


def ground(*_args):
    return np.repeat(np.linspace(0, 255, 60).astype(np.uint8)[:, None], 80, axis=1)


def walk(item):
    yield item
    for child in item.childItems():
        yield from walk(child)


def open_section(shell, qapp, monkeypatch):
    monkeypatch.setattr("image_triage.editor_tools.installed_models", lambda: frozenset(("depth",)))
    monkeypatch.setattr(RawLightDepth, "_installed", lambda self, key: key == "small")
    engine, window, photos, popout, warnings = shell
    popout.open(0)
    raw = popout.rawEditor
    raw.open("sample.NEF")
    image = QImage(800, 500, QImage.Format_RGB32)
    image.fill(QColor("#bf843f"))
    frame = ({"histogram": [[0] * 256] * 3, "fullSize": [8000, 5000]}, image)
    raw._proxy_arrived(raw._revision, (frame, frame, 0, "sample.NEF"))
    raw.lightDepthEditor._runner = ground
    dock = window.findChild(QObject, "viewerRawEditorPanel")
    dock.setProperty("tool", "Creative")                           # Light Depth lives in the Creative tab, below Sky Replacement
    page = next(i for i in walk(window.contentItem()) if i.objectName() == "rawLightDepthPage")
    page.setProperty("expanded", True)                            # the Creative tools are collapsible sections, closed to begin with
    pump(qapp, 1.2)
    pad = next(i for i in walk(window.contentItem()) if i.objectName() == "lightDepthPadDrag")
    flick = pad
    while flick is not None and flick.property("contentY") is None:
        flick = flick.parentItem()
    flick.setProperty("contentY", float(flick.property("contentY")) + pad.mapToScene(QPointF(0, 0)).y() - 300)     # scroll the scale into view
    pump(qapp, .3)
    return window, raw, pad


def drag(window, pad, x, fractions):
    top, height = pad.mapToScene(QPointF(0, 0)).y(), pad.height()
    QTest.mousePress(window, Qt.LeftButton, Qt.NoModifier, QPointF(x, top + height * fractions[0]).toPoint())
    pump(QGuiApplication.instance(), .05)
    seen = []
    for frac in fractions[1:]:
        QTest.mouseMove(window, QPointF(x, top + height * frac).toPoint(), 20)
        pump(QGuiApplication.instance(), .12)
        seen.append(bool(pad.property("pressed")))
    QTest.mouseRelease(window, Qt.LeftButton, Qt.NoModifier, QPointF(x, top + height * fractions[-1]).toPoint())
    pump(QGuiApplication.instance(), .1)
    return seen


def test_dragging_the_depth_handle_keeps_hold_of_the_pointer_instead_of_scrolling_the_panel(shell, qapp, monkeypatch):
    window, raw, pad = open_section(shell, qapp, monkeypatch)
    light = raw.lightDepthEditor
    top = pad.mapToScene(QPointF(0, 0)).y()
    x = pad.mapToScene(QPointF(pad.width() - 8, 0)).x()
    seen = drag(window, pad, x, (0.32, 0.34, 0.37, 0.40, 0.45, 0.5, 0.6, 0.7))
    assert all(seen)                                              # the panel's own scrolling never took the drag away
    assert light.settings["depth"] == pytest.approx(0.7, abs=0.03)
    assert pad.mapToScene(QPointF(0, 0)).y() == pytest.approx(top)    # and the panel did not scroll under the pointer


def test_the_depth_scale_is_a_real_depth_scale_the_handle_and_the_histogram_agree(shell, qapp, monkeypatch):
    window, raw, pad = open_section(shell, qapp, monkeypatch)
    light = raw.lightDepthEditor
    bars = light.histogram
    assert len(bars) == 48 and max(bars) == pytest.approx(1.0)
    assert min(bars) > 0.7                                         # the test photo's depth is an even ramp: every depth is there
    light.adjust("depth", 0.25)
    light.flush()
    pump(qapp, .1)
    handle = next(i for i in walk(window.contentItem()) if i.objectName() == "lightDepthPadHandle")
    scale = next(i for i in walk(window.contentItem()) if i.objectName() == "lightDepthPad")
    assert handle.property("y") + handle.property("height") / 2 == pytest.approx(0.25 * scale.property("height"))
    # a depth map that is nearly all far away: the bars say so, near the top of the scale
    light._maps.clear()
    light._runner = lambda *a: np.full((60, 80), 40, np.uint8)
    light.prepare()
    for _ in range(100):
        pump(qapp, .05)
        if light.ready:
            break
    bars = light.histogram
    assert bars.index(max(bars)) == int(40 / 255 * 48)


def test_the_feather_band_and_markers_stay_on_the_scale_when_the_depth_is_at_either_end(shell, qapp, monkeypatch):
    window, raw, pad = open_section(shell, qapp, monkeypatch)
    light = raw.lightDepthEditor
    scale = next(i for i in walk(window.contentItem()) if i.objectName() == "lightDepthPad")
    band = next(i for i in walk(window.contentItem()) if i.objectName() == "lightDepthFeatherBand")
    far = next(i for i in walk(window.contentItem()) if i.objectName() == "lightDepthFeatherFar")
    near = next(i for i in walk(window.contentItem()) if i.objectName() == "lightDepthFeatherNear")
    height = scale.property("height")
    for depth in (0.0, 0.03, 0.5, 0.97, 1.0):
        light.adjust("depth", depth)
        light.flush()
        pump(qapp, .1)
        assert band.property("y") >= 0 and band.property("y") + band.property("height") <= height + 0.5 and band.property("height") > 0
        for marker in (far, near):
            assert 0 <= marker.property("y") and marker.property("y") + marker.property("height") <= height + 0.5
    light.adjust("depth", 0.0)
    light.flush()
    pump(qapp, .1)
    assert far.property("opacity") < 1 and near.property("opacity") == 1          # the far marker is off the scale: shown dimmed at its end
