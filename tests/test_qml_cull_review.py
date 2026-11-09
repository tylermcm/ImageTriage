"""Cull review stays in place, consumes its gestures and applies selected reasons."""
import pytest
from PySide6.QtCore import QObject, QPoint, QPointF, Qt
from PySide6.QtGui import QGuiApplication, QWheelEvent
from PySide6.QtTest import QTest, QSignalSpy

from image_triage.qml_shell.cull_model import CullModel
from image_triage.qml_shell.cull_real import Outcome, reason_of
from image_triage.qml_shell.photos import PhotoModel
from test_qml_shell import _load_main, pump, qapp, rows  # noqa: F401


@pytest.mark.parametrize("text,key", [
    ("rejected: below the quality floor (MANIQA 0.1)", "native_quality"),
    ("rejected: one region blown to white covers 50%", "native_exposure"),
    ("rejected: black frame", "native_exposure"),
    ("rejected: featureless frame (misfire or blank)", "native_blank"),
    ("rejected: 2 better frames in burst U0001 (keeping 2); higher quality", "native_burst"),
    ("rejected: burst U0001 lost in its scene: 3 better units", "native_scene"),
    ("rejected: not the best-exposed frame of bracket B01 (pick_one)", "native_bracket"),
    ("a future reason we do not recognize", "native_reject"),
])
def test_native_reasons_have_actionable_categories(text, key):
    outcome = Outcome("native_reject", ("native_reject",), text, "one", 1, 1, False)
    assert reason_of(outcome) == key
    assert reason_of(Outcome("native_review", (), text, "one", 1, 1, False)) == "native_review"


def test_categories_can_be_filtered_sorted_and_applied_independently(qapp):
    photos = PhotoModel(parent=qapp)
    source = [dict(row, decision="") for row in rows(6)]
    photos.setRows(source, "/p")
    model = CullModel(photos, memory=True, parent=qapp)
    kinds = ["native_scene", "native_burst", "native_quality", "native_review", "native_reject", "native_quality"]
    summaries = ["lost in its scene", "2 better frames in burst", "below the quality floor", "review", "future reason", "below the quality floor"]
    outcomes = {r["path"]: Outcome("native_review" if k == "native_review" else "native_reject", (), text, str(i), 1, 1, False)
                for i, (r, k, text) in enumerate(zip(source, kinds, summaries))}
    model._apply_outcomes(outcomes)
    model._set(done=True, page="rev")
    updates = QSignalSpy(model.reviewRowsChanged)
    model.pick(source[2]["path"])
    assert updates.count() == 0  # Picking only updates the detail, never the list model.
    assert model.view["det"]["reason"] == "native_quality"
    model.setFilter("native_quality")
    assert len(model.reviewRows) == 2
    model.setFilter("all")
    model.setReviewSort("reason")
    labels = [st["frames"][0]["badge"] for st in model.reviewRows]
    assert labels == sorted(labels, key=str.lower)
    for key in ("native_scene", "native_burst", "native_reject"):
        model.toggleInc(key)
    model.restorePath(source[5]["path"], True)
    assert model.view["applyCount"] == 1
    model.apply()
    pump(qapp, .2)
    assert [r["path"] for r in photos.collect() if r["decision"] == "rej"] == [source[2]["path"]]
    model.undo()
    assert not [r for r in photos.collect() if r["decision"] == "rej"]


def visual_items(root):
    yield root
    for child in root.childItems():
        yield from visual_items(child)


def test_large_reviews_do_not_silently_stop_after_300_stacks(qapp):
    photos = PhotoModel(parent=qapp)
    source = [dict(row, decision="") for row in rows(1000)]
    photos.setRows(source, "/p")
    model = CullModel(photos, memory=True, parent=qapp)
    model._stacks = [[row] for row in photos.collect()]
    model._flags = {row["path"]: "soft" for row in source}
    model._set(done=True, page="rev")
    assert len(model.reviewRows) == 1000


def test_review_mouse_gestures_selection_context_menu_and_scroll(qapp, tmp_path):
    engine, window, warnings = _load_main(qapp, tmp_path)
    window.setWidth(1440); window.setHeight(1000)
    context = engine.rootContext()
    photos = context.contextProperty("photos")
    photos.setRows([dict(row, decision="") for row in rows(400)], "/p")
    model = context.contextProperty("cull")
    model.scene("cu-review")
    model._flags = {row["path"]: ("soft", "clipped", "blink")[i % 3] for i, row in enumerate(photos.collect())}
    model._rebuild()
    pump(qapp, .7)
    dialog = window.findChild(QObject, "cullWindow")
    listing = window.findChild(QObject, "cullReviewList")
    scrolling = window.findChild(QObject, "cullReviewScroll")
    assert listing.property("count") > 100
    for button in (Qt.RightButton, Qt.MiddleButton):
        QTest.mouseClick(window, button, Qt.NoModifier, dialog.mapToScene(QPointF(300, 70)).toPoint())
        assert model.view["window"] == "cull"
    center = listing.mapToScene(QPointF(100, 150))
    wheel = QWheelEvent(center, QPointF(window.mapToGlobal(center.toPoint())), QPoint(), QPoint(0, -240), Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False)
    QGuiApplication.sendEvent(window, wheel)
    pump(qapp, .3)
    assert listing.property("contentY") >= 390
    def visible_frame():
        for item in visual_items(listing):
            if not item.objectName().startswith("cullFrame-"):
                continue
            p = item.mapToItem(listing, QPointF(56, 37))
            if 40 < p.y() < listing.height() - 50:
                return item
        raise AssertionError("No visible frame")
    frame = visible_frame()
    before = listing.property("contentY")
    pos = frame.mapToScene(QPointF(56, 37)).toPoint()
    QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, pos)
    pump(qapp, .3)
    assert abs(listing.property("contentY") - before) < 1
    selected = model.view["det"]["path"]
    assert selected == frame.objectName().removeprefix("cullFrame-")
    QTest.mouseClick(window, Qt.RightButton, Qt.NoModifier, pos)
    pump(qapp, .2)
    menu = window.findChild(QObject, "cullFrameMenu")
    assert menu.property("opened") and model.view["window"] == "cull"
    restore = window.findChild(QObject, "cullMenuRestore")
    QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, restore.mapToScene(QPointF(70, 15)).toPoint())
    pump(qapp, .3)
    assert model.view["det"]["restoreLabel"] == "Flag again"
    assert abs(listing.property("contentY") - before) < 1
    QTest.mouseClick(window, Qt.MiddleButton, Qt.NoModifier, center.toPoint())
    assert scrolling.property("active")
    QTest.mouseMove(window, (center + QPointF(0, 100)).toPoint())
    pump(qapp, .3)
    assert listing.property("contentY") > before + 20
    QTest.keyClick(window, Qt.Key_Escape)
    assert not scrolling.property("active") and model.view["window"] == "cull"
    assert model.view["det"]["path"] == selected
    frame = visible_frame()
    QTest.mouseClick(window, Qt.RightButton, Qt.NoModifier, frame.mapToScene(QPointF(56, 37)).toPoint())
    pump(qapp, .2)
    grid_action = window.findChild(QObject, "cullMenuShowGrid")
    QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, grid_action.mapToScene(QPointF(70, 15)).toPoint())
    pump(qapp, .3)
    assert model.view["window"] == ""
    grid = window.findChild(QObject, "photoGrid")
    assert photos.get(grid.parentItem().property("row"))["path"] == model.view["det"]["path"]
    assert not [w for w in warnings if any(s in w for s in ("CullWindow.qml", "MiddleScroll.qml"))], warnings
    window.close(); engine.deleteLater(); pump(qapp, .2)
