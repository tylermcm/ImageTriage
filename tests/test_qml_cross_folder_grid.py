"""Photos of several folders together in the grid: library search results and every photo of a collection, with a way back."""
from __future__ import annotations

import time

import pytest
from PySide6.QtCore import QCoreApplication, QObject, QPointF, QStandardPaths, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest

from image_triage.qml_shell import decision_bridge as bridge_module
from image_triage.qml_shell import file_actions as fa
from image_triage.qml_shell.decision_bridge import DecisionBridge
from image_triage.qml_shell.file_actions import FileActions
from image_triage.qml_shell.library_nav import LibraryNavModel
from image_triage.qml_shell.menu_model import ToastModel
from image_triage.qml_shell.photos import FolderNameRole, GridModel, PhotoModel


@pytest.fixture(scope="module")
def qapp():
    return QCoreApplication.instance() or QGuiApplication([])


def pump(app, seconds=0.2):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.005)


def wait_for(app, done, seconds=8.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        pump(app, 0.03)
        if done():
            return True
    return False


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    QStandardPaths.setTestModeEnabled(True)
    session = "qml-cross-" + tmp_path.name
    monkeypatch.setattr(bridge_module, "session_id", lambda: session)
    monkeypatch.setattr(fa, "session_id", lambda: session)
    monkeypatch.setattr(FileActions, "_delete_mode", lambda self: fa.SAFE_TRASH)
    from image_triage.safe_trash import SafeTrash

    monkeypatch.setattr(SafeTrash, "recycle_root_for_folder", lambda self, folder=None: tmp_path / "trash")
    monkeypatch.setattr(SafeTrash, "is_temporary_storage_folder", lambda self, folder=None: False)
    from PySide6.QtCore import QSettings

    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    monkeypatch.setattr(FileActions, "_settings", lambda self: settings)
    yield
    QStandardPaths.setTestModeEnabled(False)


def photo(folder, name):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_bytes(b"\xff\xd8\xff\xd9" + name.encode())
    return str(folder / name)


class World:
    def __init__(self, qapp, tmp_path):
        self.app = qapp
        self.day1, self.day2, self.other = tmp_path / "2026" / "day 1", tmp_path / "2026" / "day 2", tmp_path / "other"
        self.a = photo(self.day1, "a.jpg")
        photo(self.day1, "a.nef")                                       # a's RAW: one bundle
        self.b = photo(self.day1, "b.jpg")
        self.c = photo(self.day2, "c.jpg")
        self.d = photo(self.day2, "d.jpg")
        self.e = photo(self.other, "e.jpg")
        self.photos = PhotoModel(parent=qapp)
        self.toasts = ToastModel(parent=qapp)
        self.bridge = DecisionBridge(self.photos, toasts=self.toasts, parent=qapp)
        self.files = FileActions(self.photos, self.toasts, parent=qapp)

    def open(self, folder):
        self.photos.loadFolder(str(folder))
        assert wait_for(self.app, lambda: not self.photos.loading and self.photos.folderPath == str(folder))

    def show(self, paths, title="found", kind="search"):
        self.photos.loadPaths(paths, title, kind)
        assert wait_for(self.app, lambda: not self.photos.loading and self.photos.isVirtual)

    def names(self):
        return [self.photos.get(i)["name"] for i in range(self.photos.shown)]

    def row(self, stem):
        return next(i for i in range(self.photos.shown) if self.photos.get(i)["name"].startswith(stem))


@pytest.fixture
def world(qapp, tmp_path):
    return World(qapp, tmp_path)


def test_photos_of_several_folders_are_rows_like_any_other(qapp, world):
    w = world
    w.open(w.day2)
    w.show([w.a, w.c, w.e], "trip")
    assert sorted(w.names()) == ["a.nef", "c.jpg", "e.jpg"], "the bundle a path belongs to is the row, as in its folder"
    assert w.photos.get(w.row("a"))["companions"] and w.photos.row_at(w.row("a"))["record"] is not None
    assert w.photos.isVirtual and w.photos.virtualTitle == "trip" and w.photos.virtualDetail == "3 photos from 3 folders"
    assert w.photos.folderPath == "" and w.photos.folder_dir() == "" and w.photos.subfolders == [], "there is no open folder meanwhile"
    assert w.photos.virtualReturn == str(w.day2)
    folders = {w.photos.data(w.photos.index(i), FolderNameRole) for i in range(w.photos.shown)}
    assert folders == {"day 1", "day 2", "other"}


def test_decisions_are_the_same_ones_as_in_the_folder(qapp, world):
    w = world
    w.open(w.day1)
    w.photos.setDecision(w.row("b"), "win")
    w.bridge.flush()
    w.show([w.b, w.c])
    assert w.photos.get(w.row("b"))["decision"] == "win", "loaded through the same annotation loader"
    w.photos.setDecision(w.row("c"), "rej")
    w.photos.setRating(w.row("c"), 2)
    w.bridge.flush()
    w.open(w.day2)
    assert w.photos.get(w.row("c"))["decision"] == "rej" and w.photos.get(w.row("c"))["rating"] == 2
    assert not w.photos.isVirtual


def test_a_folder_that_is_away_is_left_out_and_said(qapp, world, tmp_path):
    w = world
    w.show([w.a, str(tmp_path / "unplugged" / "x.jpg")])
    assert w.names() == ["a.nef"] and w.photos.loadError == ""
    assert w.photos.virtualDetail == "1 photo from 1 folder · 1 folder could not be read"


def test_delete_and_undo_work_on_a_photo_among_several_folders(qapp, world):
    w = world
    w.show([w.b, w.c, w.e])
    w.files.deleteRow(w.row("c"))
    w.files.waitForIdle()
    pump(qapp, 0.1)
    assert sorted(w.names()) == ["b.jpg", "e.jpg"] and not (w.day2 / "c.jpg").exists()
    assert w.photos.undo() != ""
    w.files.waitForIdle()
    assert wait_for(qapp, lambda: not w.photos.loading)
    assert (w.day2 / "c.jpg").exists()
    assert w.photos.isVirtual and sorted(w.names()) == ["b.jpg", "c.jpg", "e.jpg"], "the view is read again as what it was, not as a folder"


def test_what_works_on_one_folder_is_given_none_and_the_way_back_is_the_folder(qapp, world):
    w = world
    nav = LibraryNavModel(w.photos, memory=False, persist=False, parent=qapp)
    nav.openFolder(str(w.day1))
    assert wait_for(qapp, lambda: not w.photos.loading and w.photos.total == 2)
    seen = []
    w.photos.folderChanged.connect(lambda: seen.append(w.photos.folderPath))
    w.show([w.c, w.e])
    nav._watched_change()                                               # the folder behind changes on disk: the view is not replaced by it
    pump(qapp, 0.2)
    assert w.photos.isVirtual and not w.photos.loading
    nav.savePosition(w.c)
    assert nav._last_photo != w.c, "a photo of another folder is not a place in the open folder"
    nav.refreshFolder()                                                 # F5 reads the same photos again
    assert wait_for(qapp, lambda: not w.photos.loading)
    assert w.photos.isVirtual and sorted(w.names()) == ["c.jpg", "e.jpg"]
    back, forward = nav.canBack, nav.canForward
    nav.leaveVirtual()
    assert wait_for(qapp, lambda: not w.photos.loading and not w.photos.isVirtual)
    assert w.photos.folderPath == str(w.day1) and sorted(w.names()) == ["a.nef", "b.jpg"]
    assert (nav.canBack, nav.canForward) == (back, forward), "going back is not a step in the folder history"


def test_sorting_filters_and_sections_work_across_folders(qapp, world):
    import os

    w = world
    os.utime(w.c, (1_790_000_000, 1_790_000_000))
    os.utime(w.e, (1_790_000_000 + 3 * 86400, 1_790_000_000 + 3 * 86400))
    grid = GridModel(w.photos, parent=qapp)
    grid.setColumns(4)
    w.show([w.c, w.e])
    w.photos.setSort("modified")
    assert w.names() == ["c.jpg", "e.jpg"] and grid.sectioned and len(w.photos.sections()) == 2
    w.photos.setSort("name")
    w.photos.setDecision(0, "win")
    w.photos.setFilter("win")
    assert w.names() == ["c.jpg"] and w.photos.total == 2


def test_every_photo_of_a_collection_and_the_library_results_open_in_the_grid(qapp, world, monkeypatch):
    from image_triage.qml_shell import collections_model as cm
    from image_triage.qml_shell.collections_model import CollectionsModel
    from image_triage.qml_shell.reference_model import ReferenceModel

    w = world
    w.open(w.day1)
    collections = CollectionsModel(w.photos, memory=True, parent=qapp)

    class Store:                                                        # the library store's answer for one collection
        def load_collection(self, cid):
            from types import SimpleNamespace
            return SimpleNamespace(item_paths=(w.a, w.d, w.e)) if cid == "c2" else None

    monkeypatch.setattr(collections, "_memory", False)
    monkeypatch.setattr(collections, "_db", lambda: Store())
    collections.showAll("c2")
    assert wait_for(qapp, lambda: not w.photos.loading and w.photos.isVirtual)
    assert sorted(w.names()) == ["a.nef", "d.jpg", "e.jpg"]
    assert (w.photos.virtualTitle, w.photos.virtualKind, w.photos.virtualReturn) == ("Print order", "collection", str(w.day1))
    assert cm is not None

    reference = ReferenceModel(w.photos, w.toasts, memory=True, parent=qapp)
    reference._lib_results = [{"path": w.b, "name": "b.jpg", "where": str(w.day1), "root": ""}, {"path": w.c, "name": "c.jpg", "where": str(w.day2), "root": ""}]
    reference._lq = "beach"
    reference.showLibInGrid()
    assert wait_for(qapp, lambda: not w.photos.loading and w.photos.virtualKind == "search")
    assert sorted(w.names()) == ["b.jpg", "c.jpg"] and "beach" in w.photos.virtualTitle
    assert w.photos.virtualReturn == str(w.day1), "the way back is still the folder, not the view before"


def walk(item):
    yield item
    for child in item.childItems():
        yield from walk(child)


def test_the_window_names_what_is_shown_and_back_to_folder_returns(qapp, tmp_path):
    from test_qml_shell import _load_main

    day1, day2 = tmp_path / "shoots" / "day 1", tmp_path / "shoots" / "day 2"
    paths = [photo(day1, "a.jpg"), photo(day1, "b.jpg"), photo(day2, "c.jpg")]
    engine, window, warnings = _load_main(qapp, tmp_path)
    context = engine.rootContext()
    photos, nav = context.contextProperty("photos"), context.contextProperty("libraryNav")
    window.setWidth(1440)
    window.setHeight(900)
    window.show()
    nav.openFolder(str(day1))
    assert wait_for(qapp, lambda: not photos.loading and photos.total == 2)
    pump(qapp, 0.3)
    photos.loadPaths([paths[1], paths[2]], "“beach” in the library", "search")
    assert wait_for(qapp, lambda: not photos.loading and photos.isVirtual)
    pump(qapp, 0.4)
    grid = window.findChild(QObject, "photoGrid")
    assert grid.property("count") == 2
    texts = {item.property("text") for item in walk(window.contentItem()) if item.property("visible") and isinstance(item.property("text"), str)}
    assert "“beach” in the library" in texts and "Library search" in texts and "2 photos from 2 folders" in texts
    chips = sorted(item.property("text") for chip in walk(grid) if chip.objectName() == "gridFolderChip" and chip.property("visible")
                   for item in walk(chip) if isinstance(item.property("text"), str) and item.property("text"))
    assert chips == ["day 1", "day 2"], "each card says which folder its photo is in"

    grid.forceActiveFocus()
    grid.setProperty("currentIndex", 1)
    QTest.keyClick(window, Qt.Key.Key_W)                                # a decision works as in a folder
    assert photos.get(1)["decision"] == "win"
    popout = context.contextProperty("popout")
    popout.open(0)                                                      # and so does the viewer
    pump(qapp, 0.3)
    assert popout.isOpen and popout.row == 0
    popout.close()
    pump(qapp, 0.3)

    back = window.findChild(QObject, "backToFolder")
    assert back.property("visible")
    QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, back.mapToScene(QPointF(back.width() / 2, back.height() / 2)).toPoint())
    assert wait_for(qapp, lambda: not photos.loading and not photos.isVirtual)
    pump(qapp, 0.3)
    assert photos.folderPath == str(day1) and grid.property("count") == 2 and not back.property("visible")
    assert not [w for w in warnings if "PhotoGrid.qml" in w or "Main.qml" in w or "SidePanel.qml" in w or "LibraryWindow.qml" in w], warnings
    window.close(); engine.deleteLater(); pump(qapp, 0.2)
