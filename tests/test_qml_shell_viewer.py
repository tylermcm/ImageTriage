"""The popout in the QML shell: the host (viewer_host.py) reading, stepping and deciding, and Viewer.qml drawing and answering keys."""
from __future__ import annotations

import time
from pathlib import Path

import pytest
from PySide6.QtCore import QObject, Qt
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
    host.decide(1, "keep")                                  # keep stays on the photo
    assert photos.row_at(1)["decision"] == "keep" and host.row == 1
    assert host.toast["text"] == "DSC_4401 kept" and host.toast["kind"] == "keep" and host.toast["undo"]
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
    assert model.row_at(0)["decision"] == "keep" and popout.row == 0
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


def test_the_editor_key_says_it_is_not_connected_yet(shell, qapp):
    engine, window, model, popout, warnings = shell
    popout.open(0)
    pump(qapp, 0.5)
    QTest.keyClick(window, Qt.Key.Key_E)
    assert "editor" in popout.toast["text"] and not popout.editorConnected


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
