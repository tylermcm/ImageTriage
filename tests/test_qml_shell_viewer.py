"""The popout in the QML shell: the host (viewer_host.py) reading, stepping and deciding, and Viewer.qml drawing and answering keys."""
from __future__ import annotations

import time
from pathlib import Path

import pytest
from PySide6.QtCore import QObject, QPoint, QPointF, Qt
from PySide6.QtGui import QWindow
from PySide6.QtTest import QTest

from image_triage.qml_shell.collections_model import CollectionsModel
from image_triage.qml_shell.photos import PhotoModel
from image_triage.qml_shell.viewer_host import STRIP_MAX, STRIP_MIN, ViewerHost
from qml_support import NoThumbs
from test_qml_shell import _load_main, pump, qapp  # noqa: F401  (the same helpers and application fixture)

COUNT = 7


def wait_for(app, condition, seconds=8.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        if condition():
            return True
        time.sleep(0.01)
    return condition()


def make_rows(folder: Path, count: int = COUNT, suffix: str = "jpg") -> list[dict]:
    from PIL import Image, ImageDraw

    rows = []
    for index in range(count):
        path = folder / f"DSC_44{index:02d}.{suffix}"
        image = Image.new("RGB", (160, 100), (30 * index % 255, 80, 140))
        ImageDraw.Draw(image).rectangle((30, 20, 120, 80), outline=(255, 255, 255), width=3)
        image.save(path)
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


def test_opening_shows_a_photo_and_stepping_clamps_at_both_ends(qapp, host, photos):
    seen = []
    host.closed.connect(seen.append)
    host.open(2)
    assert host.isOpen and host.row == 2
    host.step(1)
    assert host.row == 3
    host.go(-5)
    assert host.row == 0
    host.step(-1)                                           # nothing before the first photo
    assert host.row == 0
    host.go(COUNT * 3)
    assert host.row == COUNT - 1
    host.close()
    assert not host.isOpen and seen == [COUNT - 1]


def test_a_row_that_does_not_exist_opens_nothing(host):
    host.open(-1)
    host.open(COUNT)
    assert not host.isOpen


def test_a_jpeg_is_shown_from_its_own_file_and_says_its_size(qapp, host, photos):
    host.open(1)
    path = photos.row_at(1)["path"]
    assert host.src.startswith("file:") and host.src.endswith("DSC_4401.jpg")
    assert not host.loading and host.error == ""
    assert (host.pixelWidth, host.pixelHeight) == (160, 100)
    assert host.info["ext"] == "JPG" and host.info["size"].endswith("KB") and host.info["dims"] == "160 × 100"
    assert host.crumb == "Kutaisi" or host.crumb.endswith("Kutaisi")


def test_anything_else_is_read_on_a_worker_and_shown_from_a_temporary_jpeg(qapp, tmp_path):
    folder = tmp_path / "tiffs"
    folder.mkdir()
    model = PhotoModel(parent=qapp)
    model.setRows(make_rows(folder, 3, "tif"), str(folder))
    viewer = ViewerHost(model, NoThumbs(parent=qapp), None, memory=True, parent=qapp)
    try:
        viewer.open(1)
        assert viewer.loading and viewer.src == ""
        assert wait_for(qapp, lambda: viewer.src != "")
        assert viewer.src.startswith("file:") and viewer.src.endswith(".jpg") and "imagetriage-viewer-" in viewer.src
        assert not viewer.loading and viewer.pixelWidth > 0
        assert wait_for(qapp, lambda: len(viewer._pictures) >= 2)        # the neighbours are read ahead
    finally:
        viewer.shutdown()
    assert not viewer._dir                                              # the temporary pictures are gone


def test_a_file_that_cannot_be_read_says_so(qapp, tmp_path):
    folder = tmp_path / "bad"
    folder.mkdir()
    bad = folder / "broken.tif"
    bad.write_bytes(b"not a picture")
    model = PhotoModel(parent=qapp)
    model.setRows([{"path": str(bad), "name": "broken", "ext": "TIF", "raw": False, "decision": "", "rating": 0, "ai": 0}], str(folder))
    viewer = ViewerHost(model, NoThumbs(parent=qapp), None, memory=True, parent=qapp)
    try:
        viewer.open(0)
        assert wait_for(qapp, lambda: viewer.error != "")
        assert not viewer.loading
    finally:
        viewer.shutdown()


def test_decisions_toggle_toast_and_undo_and_a_winner_or_reject_moves_on(qapp, host, photos):
    host.open(1)
    host.decide(1, "keep")
    assert photos.row_at(1)["decision"] == "keep" and host.row == 2
    assert host.toast["text"] == "DSC_4401 kept" and host.toast["kind"] == "keep" and host.toast["undo"]
    host.go(1)
    host.decide(1, "win")                                   # a winner goes on to the next photo
    assert photos.row_at(1)["decision"] == "win" and host.row == 2
    assert host.toast["text"] == "DSC_4401 marked Winner"
    host.undo()
    assert photos.row_at(1)["decision"] == "keep"
    host.decide(2, "rej")
    assert photos.row_at(2)["decision"] == "rej" and host.row == 3
    host.go(2)
    host.decide(2, "rej")                                   # the same key again clears it
    assert photos.row_at(2)["decision"] == "" and "no longer" in host.toast["text"]
    host.rate(2, 4)
    assert photos.row_at(2)["rating"] == 4
    host.rate(2, 4)                                         # the model's own toggle, as the grid's keys
    assert photos.row_at(2)["rating"] == 0


def test_the_toast_goes_away_by_itself(qapp, host):
    host.notice("Hello")
    assert host.toast["text"] == "Hello" and not host.toast["undo"]
    host._clear_toast()
    assert host.toast == {}


def test_the_filmstrip_height_is_kept_between_its_limits(host):
    assert STRIP_MIN <= host.stripHeight <= STRIP_MAX
    host.setStripHeight(10)
    assert host.stripHeight == STRIP_MIN
    host.setStripHeight(9999)
    assert host.stripHeight == STRIP_MAX
    host.setStripHeight(150)
    assert host.stripHeight == 150


def test_photos_going_away_move_the_viewer_on_and_the_last_one_closes_it(qapp, host, photos):
    host.open(2)
    path = photos.row_at(2)["path"]
    photos.remove_paths([path])
    pump(qapp, 0.05)
    assert host.isOpen and host.row == 2 and photos.row_at(2)["path"] != path        # the photo that took its place
    for _ in range(photos.rowCount()):
        photos.remove_paths([photos.row_at(0)["path"]])
    pump(qapp, 0.05)
    assert not host.isOpen


def test_focus_assist_makes_its_picture_and_remembers_the_colour(qapp, host):
    host.open(1)
    assert not host.assistOn and host.assistSrc == ""
    host.toggleAssist()
    assert host.assistOn and host.assistBusy
    assert wait_for(qapp, lambda: host.assistSrc != "")
    assert host.assistSrc.startswith("file:")
    before = host.assistColor
    host.cycleAssistColor()
    assert host.assistColor != before
    assert wait_for(qapp, lambda: host.assistSrc != "")
    host.toggleAssist()
    assert host.assistSrc == ""


def test_collections_list_with_their_members_and_toggle(qapp, photos):
    collections = CollectionsModel(photos, memory=True, parent=qapp)
    cid = collections.save("", "Picks", "#ee719e", "", False)
    viewer = ViewerHost(photos, NoThumbs(parent=qapp), collections, memory=True, parent=qapp)
    try:
        rows = viewer.collectionRows(1)
        assert [(r["name"], r["on"]) for r in rows if r["id"] == cid] == [("Picks", False)]
        viewer.toggleCollection(1, cid)
        assert [r["on"] for r in viewer.collectionRows(1) if r["id"] == cid] == [True]
        assert [r["on"] for r in viewer.collectionRows(2) if r["id"] == cid] == [False]
        viewer.toggleCollection(1, cid)
        assert [r["on"] for r in viewer.collectionRows(1) if r["id"] == cid] == [False]
    finally:
        viewer.shutdown()


def test_a_winner_goes_into_the_collection_that_takes_new_winners(qapp, photos):
    collections = CollectionsModel(photos, memory=True, parent=qapp)
    cid = collections.save("", "Winners", "#ee719e", "", True)
    viewer = ViewerHost(photos, NoThumbs(parent=qapp), collections, memory=True, parent=qapp)
    try:
        viewer.open(3)
        viewer.decide(3, "win")
        assert collections.hasRow(cid, 3)
    finally:
        viewer.shutdown()


def test_cull_flags_show_and_keep_it_restores_the_frame(qapp, photos):
    class Cull(QObject):
        def __init__(self):
            super().__init__()
            self.restored = []

        def flagInfo(self, path):
            return {"label": "Blurry", "color": "#e0b04a"} if path.endswith("DSC_4402.jpg") and path not in self.restored else {}

        def restorePath(self, path, on):
            self.restored.append(path)

    cull = Cull()
    viewer = ViewerHost(photos, NoThumbs(parent=qapp), None, cull=cull, memory=True, parent=qapp)
    try:
        assert viewer.flagFor(2)["label"] == "Blurry" and viewer.flagFor(1) == {}
        viewer.keepFlagged(2)
        assert photos.row_at(2)["decision"] == "keep" and cull.restored == [photos.row_at(2)["path"]]
        assert viewer.flagFor(2) == {}
    finally:
        viewer.shutdown()


# ---- Viewer.qml ----------------------------------------------------------------------------------------------------------
@pytest.fixture
def shell(qapp, tmp_path):
    engine, window, warnings = _load_main(qapp, tmp_path)
    model = engine.rootContext().contextProperty("photos")
    model.setRows(make_rows(tmp_path), str(tmp_path / "Kutaisi"))
    window.setWidth(1440)
    window.setHeight(900)
    window.show()
    pump(qapp, 0.4)
    yield engine, window, model, engine.rootContext().contextProperty("popout"), warnings
    window.close()
    pump(qapp, 0.1)


def test_the_viewer_is_hidden_until_a_photo_is_opened_and_shows_the_controls(shell, qapp):
    engine, window, model, popout, warnings = shell
    viewer = window.findChild(QObject, "viewer")
    assert viewer is not None and not viewer.property("visible")
    popout.open(2)
    pump(qapp, 0.6)
    assert viewer.property("visible") and viewer.property("shown")
    assert viewer.property("row") == 2 and viewer.property("curPath").endswith("DSC_4402.jpg")
    assert not [w for w in warnings if "Viewer.qml" in w and "null" not in w], warnings


def test_the_keys_step_decide_rate_and_close(shell, qapp):
    engine, window, model, popout, warnings = shell
    popout.open(1)
    pump(qapp, 0.5)
    QTest.keyClick(window, Qt.Key.Key_Right)
    assert popout.row == 2
    QTest.keyClick(window, Qt.Key.Key_Left)
    QTest.keyClick(window, Qt.Key.Key_Left)
    assert popout.row == 0
    QTest.keyClick(window, Qt.Key.Key_K)
    assert model.row_at(0)["decision"] == "keep" and popout.row == 1
    popout.go(0)
    QTest.keyClick(window, Qt.Key.Key_W)                       # a winner moves on
    assert model.row_at(0)["decision"] == "win" and popout.row == 1
    QTest.keyClick(window, Qt.Key.Key_4)
    assert model.row_at(1)["rating"] == 4
    QTest.keyClick(window, Qt.Key.Key_Z)
    assert window.findChild(QObject, "viewer").property("zoomed")
    QTest.keyClick(window, Qt.Key.Key_0)
    assert not window.findChild(QObject, "viewer").property("zoomed")
    QTest.keyClick(window, Qt.Key.Key_H)
    assert window.findChild(QObject, "viewer").property("zen")
    QTest.keyClick(window, Qt.Key.Key_H)
    QTest.keyClick(window, Qt.Key.Key_Escape)
    pump(qapp, 0.4)
    assert not popout.isOpen


def test_zoom_keeps_transparent_navigator_and_pans_only_when_dragged(shell, qapp):
    from PySide6.QtQuick import QQuickItem

    engine, window, model, popout, warnings = shell
    popout.open(0)
    popout.rawEditor.open("sample.NEF")
    popout.rawEditor._full_size = [8000, 6000]
    popout.rawEditor.changed.emit()
    pump(qapp, .3)
    viewer = window.findChild(QQuickItem, "viewer")
    navigator = window.findChild(QQuickItem, "viewerZoomNavigator")
    viewport = window.findChild(QQuickItem, "viewerZoomViewport")
    pan = window.findChild(QQuickItem, "viewerZoomPan")
    assert not navigator.isVisible()
    QTest.keyClick(window, Qt.Key_Z)
    pump(qapp, .2)
    assert viewer.property("zoomed") and navigator.isVisible() and pan.property("interactive")
    assert viewport.opacity() == pytest.approx(.35)
    assert viewport.property("color").alpha() == 0
    x, y = pan.property("contentX"), pan.property("contentY")
    point = pan.mapToScene(QPointF(pan.width()/2, pan.height()/2)).toPoint()
    QTest.mouseMove(window, point)
    pump(qapp, .1)
    assert pan.property("contentX") == x and pan.property("contentY") == y
    old_square = viewport.x()
    QTest.mousePress(window, Qt.LeftButton, Qt.NoModifier, point)
    QTest.mouseMove(window, point + QPoint(50, 30), 60)
    QTest.mouseMove(window, point + QPoint(100, 60), 60)
    QTest.mouseRelease(window, Qt.LeftButton, Qt.NoModifier, point + QPoint(100, 60))
    pump(qapp, .2)
    assert pan.property("contentX") != x and viewport.x() != old_square
    QTest.keyClick(window, Qt.Key_L)
    assert viewer.property("zoomed")  # The retired loupe shortcut cannot change the mode.
    QTest.keyClick(window, Qt.Key_0)
    assert not viewer.property("zoomed") and not navigator.isVisible()
    assert not [w for w in warnings if "Viewer.qml" in w], warnings


def test_wheel_zoom_is_variable_pointer_anchored_and_scoped_to_the_photo(shell, qapp):
    from PySide6.QtGui import QGuiApplication, QWheelEvent
    from PySide6.QtQuick import QQuickItem

    engine, window, model, popout, warnings = shell
    popout.open(0)
    popout.rawEditor.open("sample.NEF")
    popout.rawEditor._full_size = [8000, 5000]
    popout.rawEditor.changed.emit()
    pump(qapp, .4)
    viewer = window.findChild(QQuickItem, "viewer")
    pan = window.findChild(QQuickItem, "viewerZoomPan")
    photo = window.findChild(QQuickItem, "viewerPhotoImage")
    viewport = window.findChild(QQuickItem, "viewerZoomViewport")
    navigator = window.findChild(QQuickItem, "viewerZoomNavigator")
    point = QPointF(pan.width()*.65, pan.height()/2)

    def wheel(delta, local=None, pixels=0, target=pan, horizontal=0):
        if local is None:
            local = point
        scene = target.mapToScene(local)
        event = QWheelEvent(scene, QPointF(window.mapToGlobal(scene.toPoint())), QPoint(0,pixels), QPoint(horizontal,delta),
                            Qt.NoButton, Qt.NoModifier, Qt.ScrollUpdate, False)
        QGuiApplication.sendEvent(window, event)
        pump(qapp, .05)

    def anchor():
        return ((pan.property("contentX")+point.x()-pan.property("imageLeft"))/pan.property("drawWidth"),
                (pan.property("contentY")+point.y()-pan.property("imageTop"))/pan.property("drawHeight"))

    fit = pan.property("effectiveScale")
    initial = anchor()
    wheel(120)
    assert viewer.property("zoomed") and navigator.isVisible()
    assert viewer.property("zoomScale") == pytest.approx(fit*1.2)
    assert anchor() == pytest.approx(initial, abs=.001)
    first_square = viewport.width()
    wheel(120)
    assert viewer.property("zoomScale") == pytest.approx(fit*1.2**2)
    assert viewport.width() < first_square
    assert anchor() == pytest.approx(initial, abs=.001)
    wheel(-120)
    assert viewer.property("zoomScale") == pytest.approx(fit*1.2)
    wheel(0,pixels=30)
    assert viewer.property("zoomScale") == pytest.approx(fit*1.2**1.5)
    before = viewer.property("zoomScale")
    wheel(120,QPointF(20,20),target=window.findChild(QQuickItem,"viewerRawEditorPanel"))
    assert viewer.property("zoomScale") == before
    QTest.keyClick(window, Qt.Key_Z)
    assert viewer.property("zoomScale") == 1
    point = QPointF(pan.width()*.65,pan.height()*.45)
    full_anchor = anchor()
    wheel(120)
    assert viewer.property("zoomScale") == pytest.approx(1.2)
    assert anchor() == pytest.approx(full_anchor,abs=.001)
    # Wheels must never trigger Flickable's independent scrolling, including
    # horizontal wheel input which has no vertical zoom delta.
    position = (pan.property("contentX"), pan.property("contentY"))
    wheel(0, horizontal=120)
    pump(qapp, .25)
    assert (pan.property("contentX"), pan.property("contentY")) == pytest.approx(position)
    assert not pan.property("moving")
    assert pan.property("interactive")  # Mouse dragging remains available.
    wheel(-12000, QPointF(pan.width()/2,pan.height()/2))
    assert viewer.property("zoomScale") == pytest.approx(.05)
    assert photo.width() < pan.width()  # Below-fit zoom is not stretched back up.
    assert viewport.width() == pytest.approx(viewport.parentItem().width())
    QTest.keyClick(window, Qt.Key_0)
    assert not viewer.property("zoomed") and not navigator.isVisible()
    assert popout.row == 0
    assert not [w for w in warnings if "Viewer.qml" in w],warnings


def test_the_editor_key_starts_the_opening_screen(shell, qapp):
    engine, window, model, popout, warnings = shell
    popout.open(0)
    pump(qapp, 0.5)
    QTest.keyClick(window, Qt.Key.Key_E)
    pump(qapp, 0.3)
    assert window.findChild(QObject, "viewer").property("opening")          # the sample page's editor only plays the opening screen


def test_the_filmstrip_keeps_the_photo_you_are_on_in_the_middle(shell, qapp):
    engine, window, model, popout, warnings = shell
    popout.open(1)
    pump(qapp, 0.9)
    viewer = window.findChild(QObject, "viewer")
    for target in (1, 5, 3):
        popout.go(target)
        pump(qapp, 1.0)
        item = viewer.property("filmCenterRow")
        assert item == target


def test_the_viewer_follows_the_active_theme(shell, qapp):
    engine, window, model, popout, warnings = shell
    theme = engine.rootContext().contextProperty("theme")
    viewer = window.findChild(QObject, "viewer")
    for mode, tone in (("slate", "soft"), ("slate", "dim"), ("slate", "dark"), ("light", "soft")):
        theme.applyMode(mode, -1, 90, True, tone)
        pump(qapp, 0.2)
        assert viewer.property("cStage") == theme.base
        assert viewer.property("cCard") == theme.s1
        assert viewer.property("cGlass") == theme.s1
        assert viewer.property("cFg") == theme.fg and viewer.property("cLine") == theme.line


def test_tab_puts_the_action_bar_away_and_brings_it_back_and_the_photo_gets_the_room(shell, qapp):
    engine, window, model, popout, warnings = shell
    viewer = window.findChild(QObject, "viewer")
    popout.open(2)
    pump(qapp, 0.6)
    assert not popout.barHidden and viewer.property("bh") == 64
    QTest.keyClick(window, Qt.Key_Tab)
    pump(qapp, 0.5)
    assert popout.barHidden and viewer.property("bh") == 0
    assert window.findChild(QObject, "barTab").property("visible")           # the tab that brings it back
    restore = window.findChild(QObject, "barTab")
    pos = restore.mapToItem(viewer, QPointF(0, 0))
    zoom = window.findChild(QObject, "viewerZoomControls")
    assert pos.y() == zoom.y()
    assert pos.x() >= zoom.x() + zoom.width()
    stage = window.findChild(QObject, "viewerPhotoStage")
    assert pos.y() >= stage.y() + stage.height()                           # never cover the photo
    assert pos.y() + restore.height() <= viewer.height()
    QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, restore.mapToScene(QPointF(restore.width() / 2, restore.height() / 2)).toPoint())
    pump(qapp, 0.5)
    assert not popout.barHidden
    QTest.keyClick(window, Qt.Key_Tab)
    pump(qapp, 0.5)
    assert popout.barHidden
    QTest.keyClick(window, Qt.Key_Tab)
    pump(qapp, 0.5)
    assert not popout.barHidden and not window.findChild(QObject, "barTab").property("visible")


def test_space_closes_the_viewer_and_a_held_space_does_not_reopen_it(shell, qapp):
    engine, window, model, popout, warnings = shell
    popout.open(2)
    pump(qapp, 0.6)
    QTest.keyClick(window, Qt.Key_Space)
    pump(qapp, 0.4)
    assert not popout.isOpen


def test_filmstrip_curve_is_correct_on_first_open_navigation_and_resize(shell, qapp):
    engine, window, model, popout, warnings = shell
    viewer = window.findChild(QObject, "viewer")

    def thumb(index):
        pending = list(viewer.childItems())
        while pending:
            child = pending.pop()
            if child.objectName() == f"filmstripThumb_{index}":
                return child
            pending.extend(child.childItems())
        raise AssertionError(f"Missing thumbnail {index}")

    def check_curve(row):
        for neighbor in (row - 1, row + 1):
            if 0 <= neighbor < model.rowCount():
                assert thumb(row).y() < thumb(neighbor).y()
        for distant in (row - 2, row + 2):
            if 0 <= distant < model.rowCount():
                assert thumb(row).y() < thumb(distant).y()

    popout.open(0)
    pump(qapp, 0.8)
    check_curve(0)
    for row in (4, 6, 2):
        popout.go(row)
        pump(qapp, 0.8)
        check_curve(row)
    window.setWidth(1200)
    popout.setStripHeight(180)
    pump(qapp, 0.8)
    check_curve(2)
    popout.close()
    pump(qapp, 0.3)
    popout.open(4)
    pump(qapp, 0.8)
    check_curve(4)


@pytest.mark.parametrize("maximized", [False, True])
def test_preview_and_its_hide_ui_mode_cover_screen_then_restore_window(shell, qapp, maximized):
    engine, window, model, popout, warnings = shell
    if maximized:
        window.showMaximized()
        pump(qapp, 0.3)
    original_mode = window.visibility()
    original_geometry = window.geometry()
    popout.open(1)
    pump(qapp, 0.5)
    assert window.visibility() == QWindow.FullScreen
    viewer = window.findChild(QObject, "viewer")
    QTest.keyClick(window, Qt.Key_H)
    assert viewer.property("zen")
    assert window.visibility() == QWindow.FullScreen
    QTest.keyClick(window, Qt.Key_H)
    assert window.visibility() == QWindow.FullScreen
    popout.close()
    pump(qapp, 0.5)
    assert window.visibility() == original_mode
    if not maximized:
        assert window.geometry() == original_geometry


def test_raw_dock_meets_filmstrip_and_closes_to_full_width(shell, qapp):
    from PySide6.QtQuick import QQuickItem

    engine, window, model, popout, warnings = shell
    popout.open(0)
    popout.rawEditor.open("sample.NEF")
    pump(qapp, 0.4)
    dock = window.findChild(QQuickItem, "viewerRawEditorPanel")
    strip = window.findChild(QQuickItem, "viewerFilmstrip")
    origin = QPointF()
    assert dock.isVisible()
    assert dock.mapToScene(origin).y() + dock.height() == pytest.approx(window.height())
    assert strip.mapToScene(origin).x() + strip.width() == pytest.approx(dock.x())
    for tool in ("Crop", "Mask", "Remove", "Red Eye", "Presets", "History", "Adjust"):
        dock.setProperty("tool", tool)
        pump(qapp, 0.02)
    popout.rawEditor.cancel()
    pump(qapp, 0.2)
    assert strip.width() == pytest.approx(window.width())
    assert not [w for w in warnings if "RawEditorPanel.qml" in w], warnings


def test_raw_reset_and_history_preserve_unexposed_recipe_settings(qapp):
    from image_triage.qml_shell.raw_editor import RawEditor

    raw = RawEditor(memory=True)
    raw.open("sample.NEF")
    original = {"exposure": 1.0, "contrast": 15, "profile": "Embedded", "masks": [{"name": "Subject"}]}
    raw._original = dict(original)
    raw._draft = dict(original)
    raw.adjust("saturation", -20)
    raw.adjust("contrast", 30)
    raw.reset()
    assert raw.values["exposure"] == 0
    assert raw.values["contrast"] == 0
    assert raw._draft["masks"] == original["masks"]
    raw.undo()
    assert raw.values["contrast"] == 30
    raw.restoreHistory(1)
    assert raw._draft == original
    assert not raw.dirty
    raw.redo()
    assert raw.values["saturation"] == -20
    raw.cancel()


def test_raw_photo_navigation_restores_each_draft_and_undo_history(qapp):
    from image_triage.qml_shell.raw_editor import RawEditor

    raw = RawEditor(memory=True)
    raw.open("first.NEF")
    raw.adjust("exposure", 1.25)
    raw.open("second.NEF")
    assert raw.active and raw.loaded
    assert raw.values["exposure"] == 0
    raw.adjust("contrast", 35)
    raw.open("first.NEF")
    assert raw.values["exposure"] == 1.25
    assert raw.values["contrast"] == 0
    raw.undo()
    assert not raw.dirty
    raw.cancel()
    raw.open("second.NEF")
    assert raw.values["contrast"] == 35
    raw.shutdown()


def test_late_raw_load_cannot_replace_the_photo_switched_to(qapp, monkeypatch):
    from image_triage.qml_shell.raw_editor import RawEditor

    raw = RawEditor()
    monkeypatch.setattr(raw, "_start_queued", lambda: None)
    raw.open("first.NEF")
    first_revision = raw._revision
    raw._running = True
    raw.open("second.NEF")
    second_revision = raw._revision
    raw._arrived(first_revision, "load", ({"exposure": 4}, ""), "")
    assert raw.active and not raw.loaded
    raw._arrived(second_revision, "load", ({"contrast": 15}, ""), "")
    assert raw.loaded and raw.values["exposure"] == 0
    assert raw.values["contrast"] == 15
    raw.shutdown()


def test_filmstrip_wheel_and_keys_work_across_raw_editing(shell, qapp):
    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QGuiApplication, QWheelEvent
    from PySide6.QtQuick import QQuickItem

    engine, window, photos, popout, warnings = shell
    popout.open(1)
    pump(qapp, .3)
    zoom = window.findChild(QQuickItem, "viewerZoomControls")
    initial_zoom_y = zoom.y()
    popout.rawEditor.open("first.NEF")
    pump(qapp, .3)
    assert zoom.y() == initial_zoom_y
    reset = window.findChild(QQuickItem, "rawReset")
    point = reset.mapToScene(QPointF(reset.width() / 2, reset.height() / 2))
    QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, point.toPoint())
    strip = window.findChild(QQuickItem, "viewerFilmstrip")

    def wheel():
        local = strip.mapToScene(QPointF(strip.width() / 2, strip.height() / 2))
        global_pos = window.mapToGlobal(local.toPoint())
        event = QWheelEvent(local, QPointF(global_pos), QPoint(), QPoint(0, -120),
                            Qt.NoButton, Qt.NoModifier, Qt.ScrollUpdate, False)
        QGuiApplication.sendEvent(window, event)
        pump(qapp, .15)

    wheel()
    assert popout.row == 2
    assert popout.rawEditor.active
    QTest.keyClick(window, Qt.Key_Right)
    assert popout.row == 3
    popout.rawEditor.cancel()
    pump(qapp, .2)
    assert zoom.y() == initial_zoom_y
    wheel()
    assert popout.row == 4
    QTest.keyClick(window, Qt.Key_Left)
    assert popout.row == 3
    assert not [w for w in warnings if "Viewer.qml" in w or "RawEditorPanel.qml" in w], warnings


def test_raw_color_tools_validate_and_keep_structured_history(qapp):
    from copy import deepcopy
    from image_triage.qml_shell.raw_editor import RawEditor

    raw = RawEditor(memory=True)
    raw.open("sample.NEF")
    assert raw.values["gradeBlending"] == 50
    points = [[0, 0], [120, 145], [255, 255]]
    raw.setCurve("pointCurveRed", points)
    points[1][1] = 0
    assert raw.values["pointCurveRed"][1][1] == 145
    raw.setMixer(3, "hslSat", 25)
    raw.setGrading("gradeMidtones", 210, 20, -5)
    saved_draft = deepcopy(raw._draft)
    raw.setCurve("pointCurveRed", [[0, 0], [0, 100]])
    raw.setCurve("pointCurveRed", [[0, 0], [255, float("nan")]])
    raw.setMixer(9, "hslSat", 30)
    raw.setGrading("gradeMidtones", float("inf"), 20, 0)
    assert raw._draft == saved_draft
    raw.undo()
    assert raw.values["gradeMidtones"]["sat"] == 0
    raw.redo()
    assert raw.values["gradeMidtones"]["sat"] == 20
    raw.open("second.NEF")
    raw.open("sample.NEF")
    assert raw._draft == saved_draft
    raw.reset()
    assert raw.values["pointCurveRed"] == []
    assert raw.values["hslSat"] == [0] * 8
    assert raw.values["gradeMidtones"] == {"hue": 0, "sat": 0, "lum": 0}
    assert raw.values["gradeBlending"] == 50
    raw.undo()
    assert raw._draft == saved_draft
    raw.shutdown()


def test_raw_color_controls_edit_through_qml(shell, qapp):
    from PySide6.QtQuick import QQuickItem

    engine, window, photos, popout, warnings = shell
    popout.open(0)
    popout.rawEditor.open("sample.NEF")
    pump(qapp, .3)
    panel = window.findChild(QQuickItem, "viewerRawEditorPanel")

    def walk(item):
        for child in item.childItems():
            yield child
            yield from walk(child)

    for child in walk(panel):
        title = child.property("title")
        if title in ("Light", "Color"):
            child.setProperty("expanded", False)
        elif title in ("Curve", "Color Mixer", "Color Grading"):
            child.setProperty("expanded", True)
    pump(qapp, .3)
    scroll = window.findChild(QQuickItem, "rawEditorScroll").property("contentItem")

    def show_section(title):
        section = next(child for child in walk(panel) if child.property("title") == title)
        point = section.mapToItem(scroll, QPointF())
        scroll.setProperty("contentY", point.y() + scroll.property("contentY"))
        pump(qapp, .1)

    def click(name, x, y):
        item = next(child for child in walk(panel) if child.objectName() == name)
        point = item.mapToScene(QPointF(item.width() * x, item.height() * y))
        QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, point.toPoint())
        pump(qapp, .1)

    show_section("Curve")
    click("rawCurveGraph", .5, .35)
    assert len(popout.rawEditor.values["pointCurve"]) == 3
    show_section("Color Mixer")
    click("rawMixer_hslHue_slider", .75, .5)
    assert popout.rawEditor.values["hslHue"][0] > 0
    show_section("Color Grading")
    click("rawWheel_gradeMidtones", .8, .4)
    assert popout.rawEditor.values["gradeMidtones"]["sat"] > 20
    popout.rawEditor.undo()
    assert popout.rawEditor.values["gradeMidtones"]["sat"] == 0
    assert not [w for w in warnings if any(name in w for name in ("RawCurve", "RawColor", "RawGrading"))], warnings


def test_raw_effects_and_as_shot_white_balance_keep_atomic_history(qapp):
    from image_triage.qml_shell.raw_editor import RawEditor

    raw = RawEditor(memory=True)
    raw.open("sample.NEF")
    raw.adjust("temperature", 25)
    raw.adjust("tint", -10)
    raw.asShotWhiteBalance()
    assert raw.values["temperature"] == raw.values["tint"] == 0
    raw.undo()
    assert raw.values["temperature"] == 25 and raw.values["tint"] == -10
    raw.adjust("texture", 30)
    raw.adjust("vignetteAmount", -35)
    raw.adjust("grainAmount", 15)
    raw.adjust("grainSize", 40)
    raw.setGlow("amount", 20)
    raw.setGlow("spread", 75)
    raw.setEffectStyle("glow", "halation")
    raw.setEffectStyle("vignette", "colorPriority")
    assert raw.values["glow"] == {"amount": 20, "style": "halation", "range": 50, "spread": 75, "warmth": 0}
    raw.setGlow("spread", float("nan"))
    raw.setEffectStyle("glow", "invalid")
    assert raw.values["glow"]["spread"] == 75
    raw.reset()
    assert raw.values["grainSize"] == 25
    assert raw.values["grainRoughness"] == 50
    assert raw.values["vignetteStyle"] == "highlightPriority"
    assert raw.values["glow"]["amount"] == 0
    raw.undo()
    assert raw.values["glow"]["amount"] == 20
    raw.shutdown()


def test_raw_detail_and_calibration_preserve_defaults_and_history(qapp):
    from image_triage.qml_shell.raw_editor import RawEditor

    raw = RawEditor(memory=True)
    raw.open("sample.NEF")
    raw.adjust("sharpenAmount", 250)
    raw.adjust("sharpenRadius", .1)
    assert raw.values["sharpenAmount"] == 150
    assert raw.values["sharpenRadius"] == .5
    raw.setCalibration(0, "calibHue", 35)
    raw.setCalibration(2, "calibSat", -20)
    raw.setCalibration(3, "calibHue", 90)
    assert raw.values["calibHue"] == [35, 0, 0]
    assert raw.values["calibSat"] == [0, 0, -20]
    raw.setSuperResolution(True)
    raw.undo()
    assert not raw.values["superResolution"]
    raw.redo()
    assert raw.values["superResolution"]
    raw.open("other.NEF")
    raw.open("sample.NEF")
    assert raw.values["calibHue"] == [35, 0, 0]
    raw.reset()
    assert raw.values["sharpenRadius"] == 1
    assert raw.values["sharpenDetail"] == 25
    assert raw.values["noiseLuminanceDetail"] == 50
    assert raw.values["calibHue"] == raw.values["calibSat"] == [0, 0, 0]
    assert not raw.values["superResolution"]
    raw.shutdown()


def test_raw_technical_controls_edit_through_qml(shell, qapp):
    from PySide6.QtQuick import QQuickItem

    engine, window, photos, popout, warnings = shell
    popout.open(0)
    popout.rawEditor.open("sample.NEF")
    pump(qapp, .3)
    panel = window.findChild(QQuickItem, "viewerRawEditorPanel")

    def walk(item):
        for child in item.childItems():
            yield child
            yield from walk(child)

    for child in walk(panel):
        title = child.property("title")
        if title in ("Light", "Color"):
            child.setProperty("expanded", False)
        elif title in ("Detail", "Calibration"):
            child.setProperty("expanded", True)
    pump(qapp, .3)
    scroll = window.findChild(QQuickItem, "rawEditorScroll").property("contentItem")

    def show_section(title):
        section = next(child for child in walk(panel) if child.property("title") == title)
        point = section.mapToItem(scroll, QPointF())
        scroll.setProperty("contentY", point.y() + scroll.property("contentY"))
        pump(qapp, .1)

    def click(name, x=.5, y=.5):
        item = next(child for child in walk(panel) if child.objectName() == name)
        point = item.mapToScene(QPointF(item.width() * x, item.height() * y))
        QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, point.toPoint())
        pump(qapp, .1)

    show_section("Detail")
    click("rawDetailSharpen_slider", .6)
    assert popout.rawEditor.values["sharpenAmount"] > 0
    click("rawSuperResolution", .25)
    assert popout.rawEditor.values["superResolution"]
    popout.rawEditor.undo()
    assert not popout.rawEditor.values["superResolution"]
    click("rawManualNoise")
    click("rawTechnical_noiseLuminance_slider", .4)
    assert popout.rawEditor.values["noiseLuminance"] > 0
    show_section("Calibration")
    click("rawCalibrationHue_0_slider", .75)
    assert popout.rawEditor.values["calibHue"][0] > 0
    assert not [w for w in warnings if "RawTechnicalAdjustments.qml" in w], warnings


def test_white_balance_sampling_uses_neutral_rgb_and_rejects_clipping(tmp_path):
    from PySide6.QtGui import QColor, QImage
    from image_triage.qml_shell.raw_editor import _white_balance_sample

    path = tmp_path / "sample.png"
    image = QImage(20, 10, QImage.Format_RGBA8888)
    image.fill(QColor(140, 140, 140))
    assert image.save(str(path))
    values = _white_balance_sample(path, .5, .5)
    assert values["temperature"] == pytest.approx(0, abs=.01)
    assert values["tint"] == pytest.approx(0, abs=.01)
    image.fill(QColor(180, 150, 130))
    assert image.save(str(path))
    assert _white_balance_sample(path, .5, .5)["temperature"] < 0
    image.fill(QColor("white"))
    assert image.save(str(path))
    with pytest.raises(ValueError, match="clipped"):
        _white_balance_sample(path, .5, .5)


def test_late_white_balance_sample_does_not_edit_another_photo(qapp, monkeypatch):
    import threading
    from image_triage.qml_shell.raw_editor import RawEditor

    started = threading.Event()
    release = threading.Event()
    raw = RawEditor(memory=True)

    def work(action, path, original, draft, directory, sample):
        assert action == "whiteBalance" and sample == (.4, .6)
        started.set()
        assert release.wait(5)
        return {"temperature": -20, "tint": 5}

    monkeypatch.setattr(raw, "_work", work)
    raw.open("first.NEF")
    raw.pickWhiteBalance(.4, .6)
    assert started.wait(2)
    raw.open("second.NEF")
    release.set()
    assert wait_for(qapp, lambda: not raw.busy)
    assert raw.values["temperature"] == raw.values["tint"] == 0
    assert not raw.dirty
    raw.shutdown()


def test_raw_numeric_entry_and_white_balance_picker_work_through_qml(shell, qapp, monkeypatch):
    from PySide6.QtQuick import QQuickItem

    engine, window, photos, popout, warnings = shell
    popout.open(0)
    popout.rawEditor.open("sample.NEF")
    pump(qapp, .4)
    panel = window.findChild(QQuickItem, "viewerRawEditorPanel")

    def walk(item):
        for child in item.childItems():
            yield child
            yield from walk(child)

    def click_item(item):
        point = item.mapToScene(QPointF(item.width() / 2, item.height() / 2))
        QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, point.toPoint())
        pump(qapp, .1)

    value = next(child for child in walk(panel) if child.objectName() == "rawBasic_exposure_value")
    click_item(value)
    QTest.keyClick(window, Qt.Key_A, Qt.ControlModifier)
    for character in "1.25":
        QTest.keyClick(window, Qt.Key(ord(character)))
    QTest.keyClick(window, Qt.Key_Return)
    pump(qapp, .1)
    assert popout.rawEditor.values["exposure"] == 1.25
    click_item(value)
    QTest.keyClick(window, Qt.Key_A, Qt.ControlModifier)
    for character in "2.50":
        QTest.keyClick(window, Qt.Key(ord(character)))
    QTest.keyClick(window, Qt.Key_Escape)
    assert popout.rawEditor.values["exposure"] == 1.25
    assert popout.rawEditor.active

    samples = []

    def work(action, path, original, draft, directory, sample):
        assert action == "whiteBalance"
        samples.append(sample)
        return {"temperature": -15, "tint": 8}

    monkeypatch.setattr(popout.rawEditor, "_work", work)
    picker = next(child for child in walk(panel) if child.objectName() == "rawWhiteBalancePicker" and child.isVisible())
    # The Color section can extend below the viewport when the offscreen screen is short.
    scroll = window.findChild(QQuickItem, "rawEditorScroll").property("contentItem")
    section = next(child for child in walk(panel) if child.property("title") == "Color")
    point = section.mapToItem(scroll, QPointF())
    scroll.setProperty("contentY", point.y() + scroll.property("contentY"))
    pump(qapp, .1)
    click_item(picker)
    assert panel.property("whiteBalancePicking"), warnings
    sample_area = window.findChild(QQuickItem, "rawWhiteBalanceSample")
    assert wait_for(qapp, lambda: sample_area.isVisible() and sample_area.width() > 0)
    QTest.keyClick(window, Qt.Key_Escape)
    assert not panel.property("whiteBalancePicking") and popout.rawEditor.active
    click_item(picker)
    # Letterbox padding must not become a sample.
    top = sample_area.mapToScene(QPointF(sample_area.width() / 2, 0))
    if sample_area.y() > 12:
        QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, (top - QPointF(0, 8)).toPoint())
        assert panel.property("whiteBalancePicking") and not samples
    click_item(sample_area)
    assert wait_for(qapp, lambda: not popout.rawEditor.busy)
    assert samples[0] == pytest.approx((.5, .5), abs=.01)
    assert popout.rawEditor.values["temperature"] == -15
    assert popout.rawEditor.values["tint"] == 8
    assert not panel.property("whiteBalancePicking")
    popout.rawEditor.undo()
    assert popout.rawEditor.values["temperature"] == popout.rawEditor.values["tint"] == 0
    assert not [w for w in warnings if "RawColorSlider.qml" in w or "RawBasicAdjustments.qml" in w], warnings


def test_raw_display_preview_decodes_when_native_16_bit_render_exceeds_qt_limit(tmp_path):
    from PySide6.QtGui import QColor, QColorSpace, QImage, QImageReader
    from image_triage.qml_shell.raw_editor import _display_preview

    native = tmp_path / "native.png"
    image = QImage(512, 384, QImage.Format_RGBA64)
    image.fill(QColor(35, 120, 220))
    image.setColorSpace(QColorSpace(QColorSpace.SRgb))
    assert image.save(str(native))
    limit = QImageReader.allocationLimit()
    try:
        QImageReader.setAllocationLimit(1)
        assert QImageReader(str(native)).read().isNull()
        display = _display_preview(native)
        decoded = QImageReader(display).read()
        assert not decoded.isNull()
        assert decoded.size() == image.size()  # Full-resolution zoom is preserved.
        assert decoded.pixelColor(20, 20).getRgb() == image.pixelColor(20, 20).getRgb()
        assert decoded.colorSpace() == image.colorSpace()
        assert QImageReader.allocationLimit() == 1
        assert native.exists()  # The converter does not modify the native render.
    finally:
        QImageReader.setAllocationLimit(limit)


def test_raw_completed_preview_replaces_the_displayed_photo(shell, qapp, tmp_path):
    from PySide6.QtGui import QColor, QImage
    from PySide6.QtQuick import QQuickItem

    engine, window, photos, popout, warnings = shell
    popout.open(0)
    popout.rawEditor.open("sample.NEF")
    pump(qapp, .4)
    preview = tmp_path / "edited.png"
    image = QImage(120, 80, QImage.Format_RGB32)
    image.fill(QColor("blue"))
    assert image.save(str(preview))
    popout.rawEditor._preview = str(preview)
    popout.rawEditor.changed.emit()
    pump(qapp, .4)
    photo = window.findChild(QQuickItem, "viewerPhotoImage")
    panel = window.findChild(QQuickItem, "viewerRawEditorPanel")
    assert photo.property("source").toString() == popout.rawEditor.preview, (photo.property("source"), panel.property("displaySource"), warnings)
    assert photo.opacity() == pytest.approx(1), warnings
    point = photo.mapToScene(QPointF(photo.width() / 2, photo.height() / 2)).toPoint()
    rendered = window.grabWindow()
    assert not rendered.isNull()
    color = rendered.pixelColor(point)
    assert color.blue() > 200 and color.red() < 30, (color.getRgb(), warnings)
