"""Leaving a collection returns to the exact folder browsing position."""
import math

import pytest
from PySide6.QtCore import QObject, QPointF, Qt, QMetaObject, Q_ARG
from PySide6.QtTest import QTest

from test_qml_shell import _load_main, pump, qapp, rows  # noqa: F401


def items(root):
    yield root
    for item in root.childItems():
        yield from items(item)


@pytest.mark.parametrize("exit_by", ["button", "card"])
@pytest.mark.parametrize("selected_row", [0, 90])
def test_collection_exit_restores_folder_photo_and_scroll(qapp, tmp_path, exit_by, selected_row):
    engine, window, warnings = _load_main(qapp, tmp_path, "tab=coll")
    window.setWidth(1440)
    window.setHeight(900)
    QMetaObject.invokeMethod(window, "applyStartup", Q_ARG("QVariant", 1440))
    context = engine.rootContext()
    photos, collections = context.contextProperty("photos"), context.contextProperty("collections")
    photos.setRows(rows(300), "/p")
    cid = collections.save("", "Position picks", "", "", False)
    for row in (85, 90, 110):
        collections.addSelection(cid, row)
    pump(qapp, .8)
    grid = window.findChild(QObject, "photoGrid")
    grid.setProperty("currentIndex", selected_row)
    pump(qapp, .1)
    columns = grid.parentItem().property("cols")
    offset = math.floor(90/columns) * grid.property("cellHeight") - 37
    grid.setProperty("contentY", offset)
    pump(qapp, .1)
    saved = grid.property("contentY")
    original = photos.get(selected_row)["path"]
    for _ in range(2):
        collections.open(cid)
        pump(qapp, .2)
        assert photos.scopeKind == "collection" and photos.shown == 3
        if exit_by == "button":
            target = next(item for item in items(window.contentItem()) if item.property("text") == "Back to folder")
            point = QPointF(target.width()/2, target.height()/2)
        else:
            target = next(item for item in items(window.contentItem()) if item.objectName() == "collectionCard_" + cid)
            point = QPointF(25, 110)
        QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, target.mapToScene(point).toPoint())
        pump(qapp, .25)
        assert photos.scopeKind == ""
        assert photos.get(grid.property("currentIndex"))["path"] == original
        assert grid.property("contentY") == pytest.approx(saved, abs=.5)
    assert not [w for w in warnings if "PhotoGrid.qml" in w], warnings
    window.close(); engine.deleteLater(); pump(qapp, .2)


def test_loading_a_different_folder_clears_scope_bookmarks(qapp, tmp_path):
    engine, window, warnings = _load_main(qapp, tmp_path)
    context = engine.rootContext()
    photos, collections = context.contextProperty("photos"), context.contextProperty("collections")
    photos.setRows(rows(100), "/first")
    pump(qapp, .8)
    grid = window.findChild(QObject, "photoGrid")
    grid.setProperty("currentIndex", 60)
    pump(qapp, .1)
    collections.open("c1")
    pump(qapp, .1)
    photos.setRows(rows(30), "/second")
    pump(qapp, .2)
    assert not grid.parentItem().property("scopePositions").toVariant()
    assert grid.property("currentIndex") == 0
    assert grid.property("contentY") == pytest.approx(grid.property("originY"))
    window.close(); engine.deleteLater(); pump(qapp, .2)
