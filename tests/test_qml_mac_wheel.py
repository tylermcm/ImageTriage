"""Scrolling with a Mac's mouse, Magic Mouse and trackpad.

Qt's WheelHandler takes the mouse alone unless told otherwise, so a Mac trackpad scrolled nothing; and macOS gives each notch of a
wheel mouse a few pixels of distance, which the grid took as the gesture, so a notch moved a few pixels instead of a row."""
from __future__ import annotations

import pytest
from PySide6.QtCore import QObject, QPoint, QPointF, Qt
from PySide6.QtGui import QInputDevice, QPointingDevice, QWheelEvent
from PySide6.QtQuick import QQuickItem

from test_qml_shell import _load_main, pump, qapp, rows  # noqa: F401  (the same helpers and application fixture)

_TRACKPAD = None


def trackpad():
    global _TRACKPAD
    if _TRACKPAD is None:
        _TRACKPAD = QPointingDevice("test trackpad", 9901, QInputDevice.DeviceType.TouchPad, QPointingDevice.PointerType.Finger,
                                    QInputDevice.Capability.Position | QInputDevice.Capability.Scroll | QInputDevice.Capability.PixelScroll,
                                    2, 0)
    return _TRACKPAD


def send(qapp, window, pos, pixels, angle, phase=Qt.ScrollPhase.NoScrollPhase, device=None):
    point = QPointF(window.mapToGlobal(pos.toPoint()))
    args = (pos, point, QPoint(0, pixels), QPoint(0, angle), Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier, phase, False)
    if device is not None:
        args += (Qt.MouseEventSource.MouseEventNotSynthesized, device)
    qapp.sendEvent(window, QWheelEvent(*args))
    pump(qapp, 0.02)


def gesture(qapp, window, pos, steps, device=None, pixels=-30):
    send(qapp, window, pos, 0, 0, Qt.ScrollPhase.ScrollBegin, device)
    for _ in range(steps):
        send(qapp, window, pos, pixels, pixels * 3, Qt.ScrollPhase.ScrollUpdate, device)
    send(qapp, window, pos, 0, 0, Qt.ScrollPhase.ScrollEnd, device)


@pytest.fixture
def grid(qapp, tmp_path):
    engine, window, warnings = _load_main(qapp, tmp_path)
    engine.rootContext().contextProperty("photos").setRows(rows(400), "/p")
    window.setWidth(1440)
    window.setHeight(900)
    window.show()
    pump(qapp, 0.5)
    view = next(o for o in window.findChildren(QQuickItem) if o.metaObject().className().startswith("QQuickGridView"))
    yield window, view, view.mapToScene(QPointF(view.width() / 2, view.height() / 2))
    window.close()
    pump(qapp, 0.1)


def scrolled(qapp, view, run):
    view.setProperty("contentY", 0)
    pump(qapp, 0.1)
    run()
    pump(qapp, 0.4)
    return view.property("contentY")


def test_a_mac_mouse_notch_moves_a_row_not_a_few_pixels(qapp, grid):
    window, view, pos = grid
    windows_notch = scrolled(qapp, view, lambda: send(qapp, window, pos, 0, -120))
    mac_notch = scrolled(qapp, view, lambda: send(qapp, window, pos, -12, -120))       # macOS adds a few pixels to a notch
    row = view.property("cellHeight")
    assert windows_notch > row * 0.8 and mac_notch > row * 0.8, (windows_notch, mac_notch, row)


def test_a_trackpad_and_a_magic_mouse_scroll_the_grid_by_their_gesture(qapp, grid):
    window, view, pos = grid
    assert scrolled(qapp, view, lambda: gesture(qapp, window, pos, 5, trackpad())) == pytest.approx(150)
    assert scrolled(qapp, view, lambda: gesture(qapp, window, pos, 5)) == pytest.approx(150)        # a Magic Mouse is a mouse with phases
    momentum = scrolled(qapp, view, lambda: [send(qapp, window, pos, -20, -60, Qt.ScrollPhase.ScrollMomentum, trackpad()) for _ in range(4)])
    assert momentum == pytest.approx(80)


def test_a_gestures_begin_and_end_do_not_move_the_grid(qapp, grid):
    window, view, pos = grid
    view.setProperty("contentY", 37)
    pump(qapp, 0.1)
    send(qapp, window, pos, 0, 0, Qt.ScrollPhase.ScrollBegin, trackpad())
    send(qapp, window, pos, 0, 0, Qt.ScrollPhase.ScrollEnd, trackpad())
    pump(qapp, 0.3)
    assert view.property("contentY") == 37, "no snap to a row on a gesture that has not moved"


def test_a_trackpad_scrolls_the_ai_cull_review(qapp, tmp_path):
    engine, window, warnings = _load_main(qapp, tmp_path)
    window.setWidth(1440)
    window.setHeight(1000)
    context = engine.rootContext()
    photos = context.contextProperty("photos")
    photos.setRows([dict(row, decision="") for row in rows(400)], "/p")
    model = context.contextProperty("cull")
    model.scene("cu-review")
    model._flags = {row["path"]: ("soft", "clipped", "blink")[i % 3] for i, row in enumerate(photos.collect())}
    model._rebuild()
    pump(qapp, 0.7)
    listing = window.findChild(QObject, "cullReviewList")
    pos = listing.mapToScene(QPointF(100, 150))
    gesture(qapp, window, pos, 6, trackpad())
    pump(qapp, 0.3)
    assert listing.property("contentY") == pytest.approx(180)
    listing.setProperty("contentY", 0)
    send(qapp, window, pos, -12, -120)                    # a Mac mouse notch
    pump(qapp, 0.3)
    assert listing.property("contentY") == pytest.approx(200)
    window.close()
