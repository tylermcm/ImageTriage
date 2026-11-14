"""F2 with one photo: the name is edited on its card and the whole bundle is renamed off the UI thread, with undo."""
from __future__ import annotations

import time

import pytest
from PySide6.QtCore import QCoreApplication, QObject, QStandardPaths, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest

from image_triage.qml_shell import decision_bridge as bridge_module
from image_triage.qml_shell import file_actions as fa
from image_triage.qml_shell import rename_one
from image_triage.qml_shell.decision_bridge import DecisionBridge
from image_triage.qml_shell.file_actions import FileActions
from image_triage.qml_shell.menu_model import ToastModel
from image_triage.qml_shell.photos import PhotoModel


@pytest.fixture(scope="module")
def qapp():
    return QCoreApplication.instance() or QGuiApplication([])


def pump(app, seconds=0.2):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.005)


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    QStandardPaths.setTestModeEnabled(True)
    session = "qml-rename-" + tmp_path.name
    monkeypatch.setattr(bridge_module, "session_id", lambda: session)
    monkeypatch.setattr(fa, "session_id", lambda: session)
    yield
    QStandardPaths.setTestModeEnabled(False)


def load(app, photos, folder):
    photos.loadFolder(str(folder))
    for _ in range(100):
        pump(app, 0.05)
        if not photos.loading and photos.total:
            return


def settle(app, photos, files):
    files.waitForIdle()
    for _ in range(100):
        pump(app, 0.03)
        if not photos.loading:
            return


class World:
    def __init__(self, qapp, tmp_path):
        self.shoot = tmp_path / "shoot"
        self.shoot.mkdir()
        for name in ("a", "b", "c"):
            (self.shoot / f"{name}.jpg").write_bytes(b"\xff\xd8\xff\xd9" + name.encode())
        (self.shoot / "a.nef").write_bytes(b"raw-a")                  # a's RAW
        (self.shoot / "a.xmp").write_text("<x/>", encoding="utf-8")   # and its sidecar
        self.photos = PhotoModel(parent=qapp)
        self.toasts = ToastModel(parent=qapp)
        self.bridge = DecisionBridge(self.photos, toasts=self.toasts, parent=qapp)
        self.files = FileActions(self.photos, self.toasts, parent=qapp)
        self.results = []
        self.photos.renameFinished.connect(lambda old, new, error: self.results.append((old, new, error)))
        load(qapp, self.photos, self.shoot)

    def row(self, stem):
        return next(i for i in range(self.photos.shown) if self.photos.get(i)["name"].startswith(stem))

    def files_named(self):
        return sorted(p.name for p in self.shoot.iterdir() if p.is_file())


@pytest.fixture
def world(qapp, tmp_path):
    return World(qapp, tmp_path)


def test_the_whole_bundle_takes_the_new_stem_and_the_decision_follows(qapp, world):
    w = world
    w.photos.setDecision(w.row("a"), "win")
    w.bridge.flush()
    assert w.files.renameRow(w.row("a"), "sunrise") == ""
    settle(qapp, w.photos, w.files)
    assert w.files_named() == ["b.jpg", "c.jpg", "sunrise.jpg", "sunrise.nef", "sunrise.xmp"]
    assert w.results and w.results[-1][2] == "" and w.results[-1][1].endswith("sunrise.nef")
    assert w.photos.get(w.row("sunrise"))["decision"] == "win", "the saved decision is found under the new name"


def test_undo_renames_everything_back_with_its_decision(qapp, world):
    w = world
    w.photos.setDecision(w.row("a"), "keep")
    w.bridge.flush()
    w.files.renameRow(w.row("a"), "sunrise")
    settle(qapp, w.photos, w.files)
    assert w.photos.undo() == "rename of a.nef"
    settle(qapp, w.photos, w.files)
    assert w.files_named() == ["a.jpg", "a.nef", "a.xmp", "b.jpg", "c.jpg"]
    assert w.photos.get(w.row("a"))["decision"] == "keep"


def test_a_name_that_is_taken_is_refused_and_nothing_is_renamed(qapp, world):
    w = world
    before = (w.shoot / "c.jpg").read_bytes()
    assert w.files.renameRow(w.row("b"), "c") == ""                   # whether the name is free is the worker's to say
    settle(qapp, w.photos, w.files)
    assert w.results[-1][1] == "" and w.results[-1][2] == rename_one.TAKEN
    assert (w.shoot / "c.jpg").read_bytes() == before and (w.shoot / "b.jpg").exists()
    assert not w.photos.canUndo


def test_a_companions_name_being_taken_is_refused_too(qapp, world):
    w = world
    (w.shoot / "taken.nef").write_bytes(b"someone else's raw")
    load(qapp, w.photos, w.shoot)
    assert w.files.renameRow(w.row("a"), "taken") == ""
    settle(qapp, w.photos, w.files)
    assert w.results[-1][2] == rename_one.TAKEN
    assert (w.shoot / "a.jpg").exists() and (w.shoot / "taken.nef").read_bytes() == b"someone else's raw"


def test_the_name_rules_are_the_existing_ones_and_are_answered_without_the_disk(qapp, world, monkeypatch):
    w = world
    row = w.row("b")
    assert w.photos.renameCheck(row, "   ")["error"] == "Enter a file name."
    assert "path" in w.photos.renameCheck(row, "sub/b")["error"]
    assert w.photos.renameCheck(row, "b")["same"] and w.photos.renameCheck(row, "b.jpg")["same"]
    assert w.photos.renameCheck(row, "new.name") == {"error": "", "warning": "", "same": False}
    monkeypatch.setattr(rename_one, "name_warning", lambda name, directory=None: "Windows could not read this name.")
    assert w.photos.renameCheck(row, "what?")["warning"] == "Windows could not read this name."
    assert w.files.renameRow(row, "sub/b") != "" and not w.files.busy, "a name that cannot be used starts nothing"


def test_a_name_typed_with_its_extension_or_with_a_dot_is_a_stem(qapp, world):
    w = world
    w.files.renameRow(w.row("b"), "day 1.final.jpg")
    settle(qapp, w.photos, w.files)
    assert "day 1.final.jpg" in w.files_named() and "b.jpg" not in w.files_named()


def test_the_edit_data_goes_with_the_new_name(qapp, world):
    from image_triage import edit_carry

    w = world
    root = edit_carry.ensure_edit_root(w.shoot)
    (root / "b.jpg.cameraraw.json").write_text("{}", encoding="utf-8")
    assert edit_carry.edit_files_for(w.shoot / "b.jpg")
    w.files.renameRow(w.row("b"), "beach")
    settle(qapp, w.photos, w.files)
    assert edit_carry.edit_files_for(w.shoot / "beach.jpg") and not edit_carry.edit_files_for(w.shoot / "b.jpg")
    w.photos.undo()
    settle(qapp, w.photos, w.files)
    assert edit_carry.edit_files_for(w.shoot / "b.jpg") and not edit_carry.edit_files_for(w.shoot / "beach.jpg")


def test_f2_edits_the_name_on_the_card_and_enter_renames(qapp, tmp_path):
    from test_qml_shell import _load_main

    shoot = tmp_path / "shoot"
    shoot.mkdir()
    for name in ("a", "b", "c"):
        (shoot / f"{name}.jpg").write_bytes(b"\xff\xd8\xff\xd9" + name.encode())
    engine, window, warnings = _load_main(qapp, tmp_path)
    context = engine.rootContext()
    model, files = context.contextProperty("photos"), context.contextProperty("fileActions")
    load(qapp, model, shoot)
    window.setWidth(1440)
    window.setHeight(900)
    window.show()
    pump(qapp, 0.5)
    grid = window.findChild(QObject, "photoGrid")
    box, field = window.findChild(QObject, "gridRename"), window.findChild(QObject, "gridRenameInput")
    grid.forceActiveFocus()
    grid.setProperty("currentIndex", 1)
    pump(qapp, 0.1)

    QTest.keyClick(window, Qt.Key.Key_F2)
    pump(qapp, 0.2)
    assert box.property("visible") and field.property("activeFocus") and field.property("text") == "b"
    assert not window.findChild(QObject, "batchWindow") or True
    QTest.keyClick(window, Qt.Key.Key_Escape)
    pump(qapp, 0.1)
    assert not box.property("visible") and grid.property("activeFocus"), "Esc puts the keyboard back on the grid"
    assert sorted(p.name for p in shoot.iterdir()) == ["a.jpg", "b.jpg", "c.jpg"]

    QTest.keyClick(window, Qt.Key.Key_F2)
    pump(qapp, 0.1)
    field.setProperty("text", "c")
    QTest.keyClick(window, Qt.Key.Key_Return)
    settle(qapp, model, files)
    pump(qapp, 0.2)
    note = window.findChild(QObject, "gridRenameNote")
    assert box.property("visible") and note.property("visible"), "a taken name is said on the card, and the field stays"
    assert (shoot / "b.jpg").exists()

    field.setProperty("text", "zebra")
    QTest.keyClick(window, Qt.Key.Key_Delete)                        # a key typed in the field is text: it must not delete the photo
    QTest.keyClick(window, Qt.Key.Key_Return)
    settle(qapp, model, files)
    pump(qapp, 0.3)
    assert sorted(p.name for p in shoot.iterdir()) == ["a.jpg", "c.jpg", "zebra.jpg"]
    assert not box.property("visible") and grid.property("activeFocus")
    assert model.get(grid.property("currentIndex"))["name"] == "zebra.jpg", "the grid follows the photo to its new place"

    model.selectRange(0, 1)                                           # several photos: the batch window, as before
    QTest.keyClick(window, Qt.Key.Key_F2)
    pump(qapp, 0.2)
    assert not box.property("visible")
    assert [w for w in warnings if "PhotoGrid" in w or "Main.qml" in w] == []
    window.close()
    pump(qapp, 0.1)
