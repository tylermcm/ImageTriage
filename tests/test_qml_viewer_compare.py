"""Compare: photos side by side in the viewer (compare_model.py, CompareView.qml)."""
from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QObject, QPoint, QPointF, Qt
from PySide6.QtQuick import QQuickItem
from PySide6.QtTest import QTest

from image_triage.qml_shell.photos import PhotoModel
from image_triage.qml_shell.viewer_host import ViewerHost
from qml_support import NoThumbs
from test_qml_shell import _load_main, pump, qapp  # noqa: F401
from test_qml_shell_viewer import wait_for

COUNT = 8
SIZES = [(1800, 1200), (1200, 800)]            # every other photo is smaller: the same place must still line up


def make_rows(folder: Path, suffix: str = "jpg") -> list[dict]:
    from PIL import Image

    rows = []
    for index in range(COUNT):
        path = folder / f"DSC_66{index:02d}.{suffix}"
        Image.new("RGB", SIZES[index % 2], (30 * index, 90, 150)).save(path)
        rows.append({"path": str(path), "name": path.stem, "ext": suffix.upper(), "raw": False, "decision": "", "rating": 0, "ai": 60})
    return rows


@pytest.fixture
def photos(qapp, tmp_path):
    model = PhotoModel(parent=qapp)
    model.setRows(make_rows(tmp_path), str(tmp_path / "Kutaisi"))
    return model


@pytest.fixture
def host(qapp, photos):
    viewer = ViewerHost(photos, NoThumbs(parent=qapp), None, memory=True, parent=qapp)
    yield viewer
    viewer.shutdown()
    pump(qapp, 0.1)


def shown(compare):
    return [pane["row"] for pane in compare.panes]


# ---- the model ---------------------------------------------------------------------------------------------------------
def test_with_nothing_picked_the_photo_and_its_neighbour_are_compared(host, photos):
    compare = host.compare
    assert not compare.active and compare.panes == []
    host.open(2)
    compare.openFor(2)
    assert compare.active and shown(compare) == [2, 3] and compare.activeIndex == 0 and host.row == 2
    assert compare.remaining == COUNT - 4                          # the photos after them are what comes next
    pane = compare.panes[0]
    assert pane["name"] == "DSC_6602" and pane["decisionName"] == "Undecided" and pane["rating"] == 0
    assert pane["src"].startswith("file:") and (pane["w"], pane["h"]) == SIZES[0] and not pane["loading"]
    compare.close()
    assert not compare.active and host.isOpen and host.row == 2    # leaving compare leaves the viewer on the active photo
    compare.openFor(COUNT - 1)                                     # the last photo is compared with the one before it
    assert shown(compare) == [COUNT - 2, COUNT - 1] and compare.activeIndex == 1 and host.row == COUNT - 1
    host.close()
    assert not compare.active                                      # closing the viewer ends the compare


def test_photos_picked_in_the_grid_are_compared_up_to_four_at_a_time_and_open_the_viewer(host, photos):
    compare = host.compare
    compare.openRows([6, 1, 3, 5, 7], 3)
    assert host.isOpen and shown(compare) == [1, 3, 5, 6] and compare.activeIndex == 1 and host.row == 3
    assert compare.remaining == 1
    compare.close()
    compare.openRows([4, 2], 0)                                    # the grid's own photo is not one of the picked: the first is active
    assert shown(compare) == [2, 4] and compare.activeIndex == 0 and host.row == 2 and compare.remaining == 0


def test_the_active_pane_is_the_viewers_photo_and_decisions_stay_on_it(host, photos):
    compare = host.compare
    host.open(2)
    compare.openFor(2)
    compare.moveActive(1)
    assert compare.activeIndex == 1 and host.row == 3
    compare.moveActive(1)                                          # no pane further right
    assert compare.activeIndex == 1 and host.row == 3
    host.decide(host.row, "win")                                   # auto advance is on: comparing, the photo stays where it is
    host.rate(host.row, 4)
    assert host.row == 3 and shown(compare) == [2, 3]
    assert [(p["decision"], p["decisionName"], p["rating"]) for p in compare.panes] == [("", "Undecided", 0), ("win", "Winner", 4)]
    compare.setActive(0)
    host.decide(host.row, "rej")
    assert host.row == 2 and photos.row_at(2)["decision"] == "rej" and compare.panes[0]["decisionName"] == "Reject"
    compare.close()
    host.decide(host.row, "keep")                                  # out of compare a decision moves on again
    assert host.row == 3


def test_next_swaps_the_other_panes_so_the_winner_stays_and_back_takes_it_back(host, photos):
    compare = host.compare
    host.open(0)
    compare.openFor(0)
    compare.setActive(1)                                           # row 1 is the better one so far
    compare.next()
    assert shown(compare) == [2, 1] and compare.activeIndex == 1 and host.row == 1
    compare.setActive(0)                                           # now row 2 beats it
    compare.next()
    assert shown(compare) == [2, 3] and host.row == 2
    compare.back()
    assert shown(compare) == [2, 1] and compare.activeIndex == 0
    compare.back()
    assert shown(compare) == [0, 1] and compare.activeIndex == 1 and host.row == 1
    compare.back()                                                 # nothing further back
    assert shown(compare) == [0, 1]
    for _ in range(COUNT):
        compare.next()
    assert shown(compare) == [COUNT - 1, 1] and compare.remaining == 0
    assert host.toast["text"] == "No more photos to compare"


def test_two_three_or_four_panes(host, photos):
    compare = host.compare
    host.open(0)
    compare.openFor(0)
    compare.setCount(4)
    assert shown(compare) == [0, 1, 2, 3]
    compare.setActive(3)
    compare.next()                                                 # three panes move on, the active one stays
    assert shown(compare) == [4, 5, 6, 3]
    compare.setCount(2)                                            # panes go from the right, never the active one
    assert shown(compare) == [4, 3] and compare.activeIndex == 1 and host.row == 3
    compare.setCount(9)
    assert compare.count == 4
    compare.close()
    compare.openRows([0, 1], 0)                                    # a set of two can still be widened: the photos after it fill in
    compare.setCount(3)
    assert shown(compare) == [0, 1, 2]


def test_going_to_another_photo_puts_it_in_the_active_pane(host, photos):
    compare = host.compare
    host.open(0)
    compare.openFor(0)
    host.go(5)                                                     # the filmstrip
    assert shown(compare) == [5, 1] and compare.activeIndex == 0
    host.go(1)                                                     # a photo already in a pane: that pane becomes the active one
    assert shown(compare) == [5, 1] and compare.activeIndex == 1


def test_a_deleted_photo_leaves_its_pane_to_the_next_and_too_few_photos_end_the_compare(host, photos):
    compare = host.compare
    host.open(0)
    compare.openFor(0)
    photos.remove_paths([photos.row_at(1)["path"]])                # the pane beside the active one
    assert compare.active and [p["name"] for p in compare.panes] == ["DSC_6600", "DSC_6602"] and host.row == 0
    photos.remove_paths([photos.row_at(0)["path"]])                # the active one
    assert compare.active and len(compare.panes) == 2 and compare.panes[compare.activeIndex]["row"] == host.row
    assert len({p["path"] for p in compare.panes}) == 2
    photos.remove_paths([photos.row_at(i)["path"] for i in range(1, photos.rowCount())])
    assert not compare.active and host.isOpen                      # one photo left: nothing to compare it with


def test_pictures_that_need_decoding_are_read_by_the_viewers_workers(qapp, tmp_path):
    model = PhotoModel(parent=qapp)
    model.setRows(make_rows(tmp_path, "tif"), str(tmp_path))
    viewer = ViewerHost(model, NoThumbs(parent=qapp), None, memory=True, parent=qapp)
    try:
        viewer.compare.openRows([1, 2, 5], 1)
        assert [p["loading"] for p in viewer.compare.panes] == [True, True, True]      # nothing was decoded on this thread
        assert wait_for(qapp, lambda: all(p["src"] for p in viewer.compare.panes))
        assert all("imagetriage-viewer-" in p["src"] and p["w"] > 0 for p in viewer.compare.panes)
    finally:
        viewer.shutdown()


def test_compare_is_refused_while_a_raw_is_edited_and_with_one_photo(qapp, host, photos):
    host.open(0)
    host.rawEditor.open("sample.NEF")
    host.compare.openFor(0)
    assert not host.compare.active and host.toast["text"] == "Close the RAW editor to compare photos"
    host.rawEditor.cancel()
    photos.remove_paths([photos.row_at(i)["path"] for i in range(1, photos.rowCount())])
    host.compare.openFor(0)
    assert not host.compare.active and host.toast["text"] == "There is nothing to compare this photo with"


# ---- the window --------------------------------------------------------------------------------------------------------
@pytest.fixture
def shell(qapp, tmp_path):
    engine, window, warnings = _load_main(qapp, tmp_path)
    model = engine.rootContext().contextProperty("photos")
    model.setRows(make_rows(tmp_path), str(tmp_path / "Kutaisi"))
    window.setWidth(1440)
    window.setHeight(900)
    window.show()
    pump(qapp, 0.4)
    popout = engine.rootContext().contextProperty("popout")
    try:
        yield engine, window, model, popout, warnings
    finally:
        popout.shutdown()
        window.close()
        engine.deleteLater()
        pump(qapp, 0.1)


def find(window, name):
    """An item by its objectName, looked for down the visual tree (what a Repeater makes has no QObject parent for findChild to follow)."""
    todo = [window.findChild(QQuickItem, "viewer")]
    while todo:
        item = todo.pop()
        if item.objectName() == name:
            return item
        todo.extend(item.childItems())
    return None


def pane(window, index):
    return find(window, f"comparePane{index}")


def place(window, index):
    item = pane(window, index)
    return item.property("shownX"), item.property("shownY")


def test_c_compares_in_the_viewer_and_the_keys_work_on_the_active_pane(shell, qapp):
    engine, window, model, popout, warnings = shell
    popout.open(2)
    pump(qapp, 0.4)
    view = window.findChild(QQuickItem, "compareView")
    single = window.findChild(QQuickItem, "viewerZoomPan")
    assert not view.isVisible() and single.isVisible()
    QTest.keyClick(window, Qt.Key.Key_C)
    pump(qapp, 0.4)
    assert popout.compare.active and view.isVisible() and not single.isVisible()
    assert pane(window, 0).property("isActive") and not pane(window, 1).property("isActive") and pane(window, 2) is None
    assert find(window, "compareName0").property("text") == "DSC_6602"
    assert find(window, "viewerTool_compare").property("on")
    QTest.keyClick(window, Qt.Key.Key_Right)                       # the arrows choose the pane, they do not step
    pump(qapp, 0.2)
    assert popout.row == 3 and pane(window, 1).property("isActive") and [p["row"] for p in popout.compare.panes] == [2, 3]
    QTest.keyClick(window, Qt.Key.Key_W)                           # the usual keys decide and rate the active pane's photo
    QTest.keyClick(window, Qt.Key.Key_4)
    pump(qapp, 0.2)
    assert model.row_at(3)["decision"] == "win" and model.row_at(3)["rating"] == 4 and model.row_at(2)["decision"] == ""
    assert popout.row == 3                                         # and the decision does not move on
    assert find(window, "compareDecision1").property("text") == "Winner"
    assert find(window, "compareDecision0").property("text") == "Undecided"
    assert find(window, "compareRating1").property("rating") == 4
    QTest.keyClick(window, Qt.Key.Key_N)                           # winner stays: the other pane takes the next photo
    pump(qapp, 0.3)
    assert [p["row"] for p in popout.compare.panes] == [4, 3] and popout.row == 3
    assert find(window, "compareName0").property("text") == "DSC_6604"
    QTest.keyClick(window, Qt.Key.Key_P)
    pump(qapp, 0.2)
    assert [p["row"] for p in popout.compare.panes] == [2, 3]
    QTest.keyClick(window, Qt.Key.Key_Left)
    QTest.keyClick(window, Qt.Key.Key_R)
    pump(qapp, 0.2)
    assert popout.row == 2 and model.row_at(2)["decision"] == "rej" and model.row_at(3)["decision"] == "win"
    QTest.keyClick(window, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)      # undo is the viewer's as ever
    pump(qapp, 0.2)
    assert model.row_at(2)["decision"] == ""
    QTest.keyClick(window, Qt.Key.Key_Escape)                      # Esc leaves compare, on the active photo
    pump(qapp, 0.4)
    assert not popout.compare.active and popout.isOpen and popout.row == 2 and single.isVisible() and not view.isVisible()
    button = find(window, "viewerTool_compare")                    # the button in the action bar does what C does
    assert not button.property("on")
    QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, button.mapToScene(QPointF(button.width() / 2, button.height() / 2)).toPoint())
    pump(qapp, 0.4)
    assert popout.compare.active and button.property("on") and [p["row"] for p in popout.compare.panes] == [2, 3]
    QTest.keyClick(window, Qt.Key.Key_C)
    pump(qapp, 0.4)
    assert not popout.compare.active and popout.isOpen
    QTest.keyClick(window, Qt.Key.Key_Escape)                      # and the next Esc is the viewer's own
    pump(qapp, 0.4)
    assert not popout.isOpen
    assert not [w for w in warnings if "Viewer.qml" in w or "CompareView.qml" in w or "Main.qml" in w], warnings


def test_zoom_and_pan_are_linked_across_panes_of_different_sizes(shell, qapp):
    engine, window, model, popout, warnings = shell
    popout.open(0)
    pump(qapp, 0.3)
    QTest.keyClick(window, Qt.Key.Key_C)
    pump(qapp, 0.5)
    view = window.findChild(QQuickItem, "compareView")
    left, right = pane(window, 0), pane(window, 1)
    assert left.property("nw") != right.property("nw")             # 1800 px wide beside 1200 px wide
    assert not view.property("zoomed") and place(window, 0) == place(window, 1) == (0.5, 0.5)
    QTest.keyClick(window, Qt.Key.Key_Z)                           # 100% of the active pane's photo
    pump(qapp, 0.3)
    assert view.property("zoomed") and view.property("activeScale") == pytest.approx(1.0)
    assert window.findChild(QQuickItem, "viewerZoomPercent").property("text") == "100%"
    # both panes show the same share of their photos
    assert left.property("drawW") / left.width() == pytest.approx(right.property("drawW") / right.width(), rel=0.01)
    # drag the right pane: both move to the same place of their photos, and the pane dragged becomes the active one
    start = right.mapToScene(QPointF(right.width() / 2, right.height() / 2 - 20)).toPoint()
    QTest.mousePress(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start)
    QTest.mouseMove(window, start + QPoint(-40, -25), 30)
    QTest.mouseMove(window, start + QPoint(-90, -60), 30)
    QTest.mouseRelease(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start + QPoint(-90, -60))
    pump(qapp, 0.2)
    moved = place(window, 1)
    assert moved[0] > 0.52 and moved[1] > 0.52
    assert place(window, 0) == pytest.approx(moved, abs=0.002)
    assert popout.compare.activeIndex == 1 and popout.row == 1
    # the place survives the other pane taking the next photo (a photo of the first size again)
    QTest.keyClick(window, Qt.Key.Key_N)
    pump(qapp, 0.4)
    assert [p["row"] for p in popout.compare.panes] == [2, 1]
    assert place(window, 0) == pytest.approx(moved, abs=0.002) and view.property("zoomed")
    # the wheel zooms every pane about the pointer
    before = left.property("drawW")
    spot = left.mapToScene(QPointF(left.width() / 2, left.height() / 2 - 20)).toPoint()
    from PySide6.QtGui import QWheelEvent
    from PySide6.QtCore import QCoreApplication

    event = QWheelEvent(QPointF(spot), QPointF(window.mapToGlobal(spot)), QPoint(0, 0), QPoint(0, 120), Qt.MouseButton.NoButton,
                        Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
    QCoreApplication.sendEvent(window, event)
    pump(qapp, 0.2)
    assert left.property("drawW") == pytest.approx(before * 1.2, rel=0.01)
    assert place(window, 0) == pytest.approx(place(window, 1), abs=0.002)
    QTest.keyClick(window, Qt.Key.Key_0)                           # fit again
    pump(qapp, 0.2)
    assert not view.property("zoomed") and place(window, 0) == (0.5, 0.5)
    assert not [w for w in warnings if "Viewer.qml" in w or "CompareView.qml" in w], warnings


def test_the_grid_opens_compare_on_the_photos_picked_with_c_and_from_the_palette(shell, qapp):
    engine, window, model, popout, warnings = shell
    context = engine.rootContext()
    grid = window.findChild(QObject, "photoGrid")
    grid.setProperty("currentIndex", 1)
    grid.forceActiveFocus()
    model.selectRange(1, 3)
    pump(qapp, 0.1)
    QTest.keyClick(window, Qt.Key.Key_C)
    pump(qapp, 0.5)
    assert popout.isOpen and popout.compare.active and [p["row"] for p in popout.compare.panes] == [1, 2, 3]
    assert pane(window, 2) is not None and pane(window, 2).isVisible()
    four = find(window, "comparePanes4")
    QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, four.mapToScene(QPointF(four.width() / 2, four.height() / 2)).toPoint())
    pump(qapp, 0.3)
    assert [p["row"] for p in popout.compare.panes] == [1, 2, 3, 4]
    assert pane(window, 3).y() > pane(window, 0).y() and pane(window, 3).x() == pane(window, 1).x()      # two rows of two
    QTest.keyClick(window, Qt.Key.Key_Space)                       # Space leaves compare before it closes the viewer
    pump(qapp, 0.4)
    assert not popout.compare.active and popout.isOpen
    popout.close()
    pump(qapp, 0.4)
    model.clearSelection()
    grid.setProperty("currentIndex", 5)
    context.contextProperty("cmdPalette").executed.emit("review.compare")
    pump(qapp, 0.4)
    assert popout.compare.active and [p["row"] for p in popout.compare.panes] == [5, 6] and popout.row == 5
    context.contextProperty("cmdPalette").executed.emit("review.compare")      # over the viewer the command leaves compare
    pump(qapp, 0.3)
    assert not popout.compare.active and popout.isOpen
    assert not [w for w in warnings if "Viewer.qml" in w or "CompareView.qml" in w or "Main.qml" in w], warnings


def test_typing_c_in_the_search_field_is_text_not_compare(shell, qapp):
    engine, window, model, popout, warnings = shell
    QTest.keyClick(window, Qt.Key.Key_F, Qt.KeyboardModifier.ControlModifier)
    pump(qapp, 0.2)
    QTest.keyClick(window, Qt.Key.Key_C)
    QTest.keyClick(window, Qt.Key.Key_BracketRight)
    pump(qapp, 0.2)
    assert not popout.isOpen and not popout.compare.active
    assert window.findChild(QObject, "photoSearchInput").property("text") == "c]"
