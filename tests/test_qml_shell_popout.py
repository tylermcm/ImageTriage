"""The popout in the QML shell: the Widgets app's full-screen viewer opened from the grid, stepping, deciding and closing."""
from __future__ import annotations

import time

import pytest
from PySide6.QtCore import QObject, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtTest import QTest

from image_triage.cache import DiskThumbnailCache
from image_triage.qml_shell.collections_model import CollectionsModel
from image_triage.qml_shell.photos import PhotoModel
from image_triage.qml_shell.popout import PopoutHost
from image_triage.qml_shell.thumbs import ThumbService
from qml_support import NoThumbs
from test_qml_shell import _load_main, pump, qapp, rows  # noqa: F401  (the same helpers and application fixture)

COUNT = 6


@pytest.fixture
def photos(qapp, tmp_path):
    from PIL import Image

    model = PhotoModel(parent=qapp)
    items = []
    for index in range(COUNT):
        path = tmp_path / f"DSC_43{index:02d}.jpg"
        Image.new("RGB", (96, 64), (40 * index, 90, 160)).save(path)
        items.append({"path": str(path), "name": f"DSC_43{index:02d}", "ext": "JPG", "raw": False, "decision": "", "rating": 0, "ai": 60})
    model.setRows(items, str(tmp_path))
    return model


@pytest.fixture
def host(qapp, photos):
    popout = PopoutHost(photos, NoThumbs(parent=qapp), None, prebuild=False, parent=qapp)
    yield popout
    popout.shutdown()
    pump(qapp, 0.1)


def path_of(photos, row):
    return photos.row_at(row)["path"]


def test_open_shows_the_photo_and_the_viewer_steps_through_the_grids_photos(qapp, photos, host):
    host.open(2)
    viewer = host.preview()
    assert host.property("isOpen") and viewer.isVisible()
    assert viewer.anchor_path() == path_of(photos, 2)
    viewer.navigation_requested.emit(1)
    assert viewer.anchor_path() == path_of(photos, 3)
    viewer.navigation_requested.emit(-10)                         # clamps to the first photo
    assert viewer.anchor_path() == path_of(photos, 0)
    viewer.navigation_requested.emit(-1)                          # nothing before it: it stays
    assert viewer.anchor_path() == path_of(photos, 0)
    viewer.navigation_requested.emit(COUNT * 2)
    assert viewer.anchor_path() == path_of(photos, COUNT - 1)


def test_a_row_that_does_not_exist_opens_nothing(host):
    host.open(-1)
    host.open(COUNT)
    assert not host.property("isOpen")


def test_decisions_and_ratings_made_in_the_viewer_reach_the_model(qapp, photos, host):
    host.open(1)
    viewer = host.preview()
    path = path_of(photos, 1)
    viewer.winner_requested.emit(path)
    assert photos.row_at(1)["decision"] == "win"
    viewer.winner_requested.emit(path)                            # the same key again clears it, as in the grid
    assert photos.row_at(1)["decision"] == ""
    viewer.reject_requested.emit(path)
    assert photos.row_at(1)["decision"] == "rej"
    viewer.keep_requested.emit(path)
    assert photos.row_at(1)["decision"] == "keep"
    viewer.rating_requested.emit(path, 4)
    assert photos.row_at(1)["rating"] == 4
    viewer.rating_requested.emit(path, 4)                         # asking for what it already is changes nothing (the model alone would toggle)
    assert photos.row_at(1)["rating"] == 4
    viewer.rating_requested.emit(path, 0)
    assert photos.row_at(1)["rating"] == 0
    assert photos.winCount == 0 and photos.keepCount == 1


def test_the_viewer_hears_about_the_new_state(qapp, photos, host):
    host.open(0)
    viewer = host.preview()
    viewer.winner_requested.emit(path_of(photos, 0))
    entry = viewer._source_entries[0]
    assert entry.winner and not entry.reject


def test_a_winner_goes_to_the_collecting_collection(qapp, photos):
    collections = CollectionsModel(photos, memory=True, parent=qapp)
    cid = collections.save("", "Picks", "#ee719e", "", True)       # "New winners go here"
    assert collections.activeId == cid
    popout = PopoutHost(photos, NoThumbs(parent=qapp), collections, prebuild=False, parent=qapp)
    try:
        popout.open(3)
        popout.preview().winner_requested.emit(path_of(photos, 3))
        assert len(collections._mem[cid]["members"]) == 1
        popout.preview().collect_requested.emit(path_of(photos, 4))      # B in the viewer adds the photo it is on
        assert len(collections._mem[cid]["members"]) == 2
    finally:
        popout.shutdown()


def test_closing_puts_the_viewer_away_and_says_where_it_ended(qapp, photos, host):
    seen = []
    host.closed.connect(seen.append)
    host.open(1)
    viewer = host.preview()
    viewer.navigation_requested.emit(2)
    viewer.close()
    assert seen == [3]
    assert not host.property("isOpen") and not viewer.isVisible()
    host.open(0)                                                  # it hides rather than closes: opening again works
    assert viewer.isVisible() and host.preview() is viewer and viewer.anchor_path() == path_of(photos, 0)


def test_the_editor_key_does_nothing_from_here_yet(qapp, photos, host):
    host.open(0)
    viewer = host.preview()
    viewer.toggle_editor()
    assert not viewer.editor_engaged()


def test_compare_shows_the_next_photos_beside_it(qapp, photos, host):
    host.open(1)
    viewer = host.preview()
    viewer.compare_mode_changed.emit(True)
    assert viewer.compare_mode() and len(viewer._source_entries) == 3
    assert [e.record.path for e in viewer._source_entries] == [path_of(photos, i) for i in (1, 2, 3)]
    viewer.compare_mode_changed.emit(False)
    assert len(viewer._source_entries) == 1


def test_the_strip_gets_thumbnails_from_the_cache_and_asks_for_missing_ones(qapp, photos, tmp_path):
    thumbs = ThumbService(DiskThumbnailCache(tmp_path / "cache"), parent=qapp)
    popout = PopoutHost(photos, thumbs, None, prebuild=False, parent=qapp)
    try:
        assert popout._strip_thumb(0) is None                     # not made yet: it is queued, and asked for only once
        assert path_of(photos, 0) in popout._awaiting
        end = time.monotonic() + 10
        pixmap = None
        while pixmap is None and time.monotonic() < end:
            pump(qapp, 0.05)
            pixmap = popout._strip_thumb(0)
        assert isinstance(pixmap, QPixmap) and not pixmap.isNull()
        assert path_of(photos, 0) not in popout._awaiting
        assert popout._strip_thumb(0) is pixmap                   # the same pixmap again: nothing is drawn twice
        assert popout._strip_thumb(COUNT + 5) is None
        assert popout._placeholder(path_of(photos, 0)) is not None
    finally:
        popout.shutdown()


def test_the_strips_status_dots_follow_the_decisions(qapp, photos, host):
    photos.setDecision(0, "win")
    photos.setDecision(1, "rej")
    assert host._strip_tag(0) == "#ee719e" and host._strip_tag(1) == "#ff6b6b" and host._strip_tag(2) is None


def test_the_preload_asks_for_the_neighbours_ahead_first(qapp, photos, host):
    asked = []
    host.open(2)
    viewer = host.preview()
    viewer.preload_paths = lambda paths, **_: asked.append(list(paths))
    host._travel = 1
    host._schedule_preload(2)
    pump(qapp, 0.4)
    assert asked and asked[-1][0] == path_of(photos, 2) and asked[-1][1] == path_of(photos, 3)


def test_the_window_opens_the_viewer_and_takes_the_row_back(qapp, tmp_path):
    engine, window, warnings = _load_main(qapp, tmp_path)
    popout = engine.rootContext().contextProperty("popout")
    photos = engine.rootContext().contextProperty("photos")
    photos.setRows(rows(12), "/p")
    pump(qapp, 0.5)
    grid = window.findChild(QObject, "photoGrid")
    grid.setProperty("currentIndex", 2)
    popout.open(2)                                                # what Enter, Space and a double click send
    assert popout.property("isOpen")
    popout.step(3)
    popout.close()
    pump(qapp, 0.3)
    assert grid.property("currentIndex") == 5                     # the grid is on the photo the viewer ended on
    assert not [w for w in warnings if "ReferenceError" in w or "TypeError" in w or "is not a type" in w], warnings
    popout.shutdown()
    window.close()
    engine.deleteLater()
    pump(qapp, 0.2)
