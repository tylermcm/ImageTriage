"""Dragging photos from the grid onto a collection in the side panel adds them, in one step, without disturbing the pick."""
from __future__ import annotations

from PySide6.QtCore import QObject, QPoint, QPointF, Qt
from PySide6.QtTest import QTest

from image_triage.qml_shell.collections_model import CollectionsModel
from image_triage.qml_shell.photos import PhotoModel
from test_qml_shell import _load_main, pump, qapp, rows  # noqa: F401


def items(item):
    yield item
    for child in item.childItems():
        yield from items(child)


def open_window(qapp, tmp_path):
    engine, window, warnings = _load_main(qapp, tmp_path, startup="tab=coll")
    context = engine.rootContext()
    photos, collections = context.contextProperty("photos"), context.contextProperty("collections")
    photos.setRows(rows(40), "/p")
    window.setWidth(1440)
    window.setHeight(900)
    window.show()
    pump(qapp, 0.9)
    return engine, window, warnings, photos, collections


def cell_point(grid, index):
    cols = grid.parentItem().property("cols")
    width, height = grid.property("cellWidth"), grid.property("cellHeight")
    local = QPointF((index % cols + 0.5) * width, (index // cols + 0.5) * height - (grid.property("contentY") - grid.property("originY")))
    return grid.mapToScene(local).toPoint()


def card_point(window, cid):
    card = next(item for item in items(window.contentItem()) if item.objectName() == "collectionCard_" + cid)
    return card.mapToScene(QPointF(card.width() / 2, card.height() / 2)).toPoint()


def drag(qapp, window, start, end, steps=8, release=True):
    QTest.mousePress(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start)
    pump(qapp, 0.05)
    for n in range(1, steps + 1):
        QTest.mouseMove(window, QPoint(start.x() + (end.x() - start.x()) * n // steps, start.y() + (end.y() - start.y()) * n // steps))
        pump(qapp, 0.03)
    if release:
        QTest.mouseRelease(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, end)
        pump(qapp, 0.2)


def members(collections, cid):
    return set(collections._mem[cid]["members"])


def test_a_photo_outside_the_pick_is_dragged_alone_and_the_pick_stays(qapp, tmp_path):
    engine, window, warnings, photos, collections = open_window(qapp, tmp_path)
    grid = window.findChild(QObject, "photoGrid")
    photos.selectRange(0, 2)
    before = members(collections, "c5")
    drag(qapp, window, cell_point(grid, 5), card_point(window, "c5"))
    assert members(collections, "c5") - before == {photos.get(5)["name"]}
    assert photos.selectionRows(0) == [0, 1, 2], "dragging another photo leaves the pick alone"
    assert not [w for w in warnings if "PhotoGrid.qml" in w or "SidePanel.qml" in w], warnings
    window.close(); engine.deleteLater(); pump(qapp, 0.2)


def test_dragging_a_picked_photo_carries_the_whole_pick_with_a_count_and_one_reload(qapp, tmp_path, monkeypatch):
    engine, window, warnings, photos, collections = open_window(qapp, tmp_path)
    grid = window.findChild(QObject, "photoGrid")
    photos.selectRange(1, 4)
    reloads = []
    real = CollectionsModel._reload_one
    monkeypatch.setattr(CollectionsModel, "_reload_one", lambda self, cid, full=None: (reloads.append(cid), real(self, cid, full))[1])
    drag(qapp, window, cell_point(grid, 2), card_point(window, "c5"), release=False)
    badge, count = window.findChild(QObject, "gridDragBadge"), window.findChild(QObject, "gridDragCount")
    assert badge.property("visible") and count.property("count") == 4
    card = next(item for item in items(window.contentItem()) if item.objectName() == "collectionCard_c5")
    assert card.property("dropActive"), "the collection under the pointer is highlighted"
    end = card_point(window, "c5")
    QTest.mouseRelease(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, end)
    pump(qapp, 0.2)
    assert members(collections, "c5") == {photos.get(i)["name"] for i in (1, 2, 3, 4)}
    assert reloads == ["c5"], "four photos are one add and one re-read of the collection"
    card = next(item for item in items(window.contentItem()) if item.objectName() == "collectionCard_c5")      # the cards were rebuilt by the add
    assert not badge.property("visible") and not card.property("dropActive")
    assert photos.selectionRows(0) == [1, 2, 3, 4]
    window.close(); engine.deleteLater(); pump(qapp, 0.2)


def test_a_drop_anywhere_else_or_escape_adds_nothing_and_clicks_still_pick(qapp, tmp_path):
    engine, window, warnings, photos, collections = open_window(qapp, tmp_path)
    grid = window.findChild(QObject, "photoGrid")
    before = {cid: members(collections, cid) for cid in ("c1", "c5")}
    drag(qapp, window, cell_point(grid, 3), cell_point(grid, 9))                      # dropped on the grid itself
    grid.forceActiveFocus()
    drag(qapp, window, cell_point(grid, 3), card_point(window, "c5"), release=False)
    QTest.keyClick(window, Qt.Key.Key_Escape)
    pump(qapp, 0.1)
    assert not window.findChild(QObject, "gridDragBadge").property("visible")
    QTest.mouseRelease(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, card_point(window, "c5"))
    pump(qapp, 0.2)
    assert {cid: members(collections, cid) for cid in ("c1", "c5")} == before
    QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, cell_point(grid, 6))
    pump(qapp, 0.1)
    assert grid.property("currentIndex") == 6
    QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ShiftModifier, cell_point(grid, 8))
    pump(qapp, 0.1)
    assert photos.selectionRows(8) == [6, 7, 8]
    QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ControlModifier, cell_point(grid, 7))
    pump(qapp, 0.1)
    assert photos.selectionRows(8) == [6, 8]
    window.close(); engine.deleteLater(); pump(qapp, 0.2)


def test_add_rows_is_one_write_for_all_the_photos(qapp):
    photos = PhotoModel(parent=qapp)
    photos.setRows(rows(12), "/p")
    collections = CollectionsModel(photos, memory=True, parent=qapp)
    assert collections.addRows("c5", [0, 1, 2]) == 3 and collections.addRows("c5", [1, 2, 3]) == 1
