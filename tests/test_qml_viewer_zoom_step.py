"""Stepping in the viewer keeps the zoom and the part of the photo being looked at, however the next photo is reached."""
from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import Q_ARG, QMetaObject, QObject
from PySide6.QtQuick import QQuickItem

from test_qml_shell import _load_main, pump, qapp  # noqa: F401
from test_qml_shell_viewer import wait_for

SIZES = [(3000, 2000), (2400, 1600), (2000, 3000), (3000, 2000)]


def make_rows(folder: Path, suffix: str) -> list[dict]:
    from PIL import Image

    rows = []
    for index, size in enumerate(SIZES):
        path = folder / f"DSC_55{index:02d}.{suffix}"
        Image.new("RGB", size, (40 * index, 90, 150)).save(path)
        rows.append({"path": str(path), "name": path.stem, "ext": suffix.upper(), "raw": False, "decision": "", "rating": 0, "ai": 60})
    return rows


def open_shell(qapp, tmp_path, suffix):
    engine, window, warnings = _load_main(qapp, tmp_path)
    model = engine.rootContext().contextProperty("photos")
    model.setRows(make_rows(tmp_path, suffix), str(tmp_path / "Kutaisi"))
    window.setWidth(1440)
    window.setHeight(900)
    window.show()
    pump(qapp, 0.4)
    return engine, window, model, engine.rootContext().contextProperty("popout"), warnings


@pytest.fixture(params=["jpg", "tif"])
def shell(request, qapp, tmp_path):
    """jpg is shown from its own file at once; tif is read on a worker, so its size is not known for a moment after the step."""
    engine, window, model, popout, warnings = open_shell(qapp, tmp_path, request.param)
    try:
        yield engine, window, model, popout, warnings
    finally:
        popout.shutdown()
        window.close()
        engine.deleteLater()
        pump(qapp, 0.1)


def centre(pan):
    """The middle of what is shown, as a fraction of the photo."""
    return ((pan.property("contentX") + pan.width() / 2 - pan.property("imageLeft")) / pan.property("drawWidth"),
            (pan.property("contentY") + pan.height() / 2 - pan.property("imageTop")) / pan.property("drawHeight"))


def zoom_to(qapp, window, popout, fx, fy):
    viewer = window.findChild(QQuickItem, "viewer")
    pan = window.findChild(QQuickItem, "viewerZoomPan")
    assert wait_for(qapp, lambda: popout.pixelWidth > 0)
    pump(qapp, 0.2)
    QMetaObject.invokeMethod(viewer, "setZoom", Q_ARG("QVariant", 1), Q_ARG("QVariant", pan.width() / 2), Q_ARG("QVariant", pan.height() / 2))
    pan.setProperty("contentX", pan.property("imageLeft") + fx * pan.property("drawWidth") - pan.width() / 2)
    pan.setProperty("contentY", pan.property("imageTop") + fy * pan.property("drawHeight") - pan.height() / 2)
    pump(qapp, 0.1)
    assert centre(pan) == pytest.approx((fx, fy), abs=0.01)
    return viewer, pan


def settled(qapp, popout, row):
    assert wait_for(qapp, lambda: popout.row == row and popout.pixelWidth > 0)
    pump(qapp, 0.3)


def test_stepping_keeps_the_zoom_and_the_same_place_in_a_photo_of_another_size(shell, qapp):
    engine, window, model, popout, warnings = shell
    popout.open(0)
    viewer, pan = zoom_to(qapp, window, popout, 0.7, 0.6)
    QMetaObject.invokeMethod(viewer, "step", Q_ARG("QVariant", 1))        # 3000 x 2000 -> 2400 x 1600
    settled(qapp, popout, 1)
    assert viewer.property("zoomed") and viewer.property("zoomScale") == 1
    assert centre(pan) == pytest.approx((0.7, 0.6), abs=0.01)
    QMetaObject.invokeMethod(viewer, "step", Q_ARG("QVariant", 1))        # -> a portrait photo
    settled(qapp, popout, 2)
    assert centre(pan) == pytest.approx((0.7, 0.6), abs=0.01)
    QMetaObject.invokeMethod(viewer, "step", Q_ARG("QVariant", -1))
    settled(qapp, popout, 1)
    assert centre(pan) == pytest.approx((0.7, 0.6), abs=0.01)
    assert not [w for w in warnings if "Viewer.qml" in w], warnings


def test_a_decision_that_moves_on_keeps_the_place_too(shell, qapp):
    """Winner, Keep and Reject step from the host (auto advance), not through the viewer's own step."""
    engine, window, model, popout, warnings = shell
    popout.open(0)
    viewer, pan = zoom_to(qapp, window, popout, 0.3, 0.7)
    popout.decide(0, "keep")
    settled(qapp, popout, 1)
    assert viewer.property("zoomed") and viewer.property("zoomScale") == 1
    assert centre(pan) == pytest.approx((0.3, 0.7), abs=0.01)


def test_a_place_near_the_edge_stays_inside_the_next_photo(shell, qapp):
    engine, window, model, popout, warnings = shell
    popout.open(0)
    viewer, pan = zoom_to(qapp, window, popout, 0.8, 0.8)
    pan.setProperty("contentX", pan.property("contentWidth") - pan.width())     # the right edge
    pump(qapp, 0.1)
    QMetaObject.invokeMethod(viewer, "step", Q_ARG("QVariant", 1))
    settled(qapp, popout, 1)
    assert pan.property("contentX") == pytest.approx(pan.property("contentWidth") - pan.width(), abs=1)
    assert 0 <= pan.property("contentY") <= pan.property("contentHeight") - pan.height()


def test_a_photo_being_edited_as_raw_starts_fitted(qapp, tmp_path):
    engine, window, model, popout, warnings = open_shell(qapp, tmp_path, "jpg")
    try:
        popout.open(0)
        viewer, pan = zoom_to(qapp, window, popout, 0.7, 0.6)
        popout.rawEditor.open("sample.NEF")
        pump(qapp, 0.2)
        assert viewer.property("rawEditing")
        viewer.setProperty("zoomed", True)
        QMetaObject.invokeMethod(viewer, "step", Q_ARG("QVariant", 1))
        pump(qapp, 0.3)
        assert not viewer.property("zoomed")
    finally:
        popout.rawEditor.cancel()
        popout.shutdown()
        window.close()
        engine.deleteLater()
        pump(qapp, 0.1)
