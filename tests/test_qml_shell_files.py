"""Delete, move and copy in the QML shell: the Widgets app's file rules, with undo."""
from __future__ import annotations

import os
import time

import pytest
from PySide6.QtCore import QCoreApplication, QStandardPaths
from PySide6.QtGui import QGuiApplication

from image_triage.qml_shell import decision_bridge as bridge_module
from image_triage.qml_shell import file_actions as fa
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
    session = "qml-files-" + tmp_path.name
    monkeypatch.setattr(bridge_module, "session_id", lambda: session)
    monkeypatch.setattr(fa, "session_id", lambda: session)
    monkeypatch.setattr(FileActions, "_delete_mode", lambda self: fa.SAFE_TRASH)
    # the safe trash lives under the test's own folder, not the user's app data
    from image_triage.recycle_bin_controller import RecycleBinController

    monkeypatch.setattr(RecycleBinController, "recycle_root_for_folder", lambda self, folder=None: tmp_path / "trash")
    monkeypatch.setattr(RecycleBinController, "is_temporary_storage_folder", lambda self, folder=None: False)
    from PySide6.QtCore import QSettings

    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    monkeypatch.setattr(FileActions, "_settings", lambda self: settings)
    yield
    QStandardPaths.setTestModeEnabled(False)


@pytest.fixture
def world(qapp, tmp_path):
    shoot = tmp_path / "shoot"
    shoot.mkdir()
    for name in ("a", "b", "c"):
        (shoot / f"{name}.jpg").write_bytes(b"\xff\xd8\xff\xd9" + name.encode())
    (shoot / "a.nef").write_bytes(b"raw-a")                       # a's RAW companion
    photos = PhotoModel(parent=qapp)
    toasts = ToastModel(parent=qapp)
    bridge = DecisionBridge(photos, toasts=toasts, parent=qapp)
    files = FileActions(photos, toasts, parent=qapp)
    load(qapp, photos, shoot)
    return SimpleWorld(shoot, photos, toasts, bridge, files, tmp_path)


class SimpleWorld:
    def __init__(self, shoot, photos, toasts, bridge, files, tmp):
        self.shoot, self.photos, self.toasts, self.bridge, self.files, self.tmp = shoot, photos, toasts, bridge, files, tmp


def load(app, photos, folder):
    photos.loadFolder(str(folder))
    for _ in range(100):
        pump(app, 0.05)
        if not photos.loading and photos.total:
            return


def names(photos):
    return sorted(photos.get(i)["name"] for i in range(photos.shown))


def row_of(photos, stem):
    return next(i for i in range(photos.shown) if photos.get(i)["name"].startswith(stem))


def test_delete_moves_the_whole_bundle_to_the_safe_trash_and_undo_brings_it_back(qapp, world):
    w = world
    count = w.photos.shown
    w.photos.setDecision(row_of(w.photos, "a"), "win")
    w.bridge.flush()
    pump(qapp, 0.3)
    w.files.deleteRow(row_of(w.photos, "a"))
    assert w.photos.shown == count - 1 and not (w.shoot / "a.jpg").exists() and not (w.shoot / "a.nef").exists()
    assert {p.name for p in (w.tmp / "trash").iterdir() if not p.name.startswith(".")} >= {"a.jpg", "a.nef", "a.xmp"}      # the sidecar went too
    assert w.toasts.items[-1]["title"] == "Deleted a.nef" and w.toasts.items[-1]["action"] == "Undo"
    assert w.photos.undo() == "delete of a.nef"
    pump(qapp, 0.8)
    assert (w.shoot / "a.jpg").exists() and (w.shoot / "a.nef").exists()
    assert w.photos.shown == count
    assert w.photos.get(row_of(w.photos, "a"))["decision"] == "win"            # the decision came back with the file


def test_the_toasts_undo_button_takes_back_that_step(qapp, world):
    w = world
    w.files.deleteRow(row_of(w.photos, "b"))
    action = w.toasts.items[-1]
    assert action["action"] == "Undo"
    w.toasts.trigger(action["id"])
    pump(qapp, 0.8)
    assert (w.shoot / "b.jpg").exists() and not (w.tmp / "trash" / "b.jpg").exists()


def test_a_deleted_photos_decision_is_dropped_from_the_store(qapp, world):
    from image_triage.decision_store import DecisionStore

    w = world
    w.photos.setDecision(row_of(w.photos, "b"), "win")
    w.bridge.flush()
    pump(qapp, 0.3)
    assert len(DecisionStore().winner_paths(bridge_module.session_id())) == 1
    w.files.deleteRow(row_of(w.photos, "b"))
    assert DecisionStore().winner_paths(bridge_module.session_id()) == []


def test_move_goes_with_its_companion_and_its_decision(qapp, world):
    from image_triage.decision_store import DecisionStore

    w = world
    dest = w.tmp / "delivery"
    w.photos.setRating(row_of(w.photos, "a"), 4)
    w.bridge.flush()
    pump(qapp, 0.3)
    w.files.moveRow(row_of(w.photos, "a"), str(dest))
    assert {p.name for p in dest.iterdir()} >= {"a.jpg", "a.nef"} and not (w.shoot / "a.jpg").exists() and not (w.shoot / "a.nef").exists()
    assert not any(n.startswith("a") for n in names(w.photos))
    other = PhotoModel(parent=qapp)
    DecisionBridge(other, parent=qapp)
    load(qapp, other, dest)
    assert other.get(row_of(other, "a"))["rating"] == 4                         # the saved rating followed the file
    assert w.toasts.items[-1]["title"] == "Moved a.nef"
    assert w.photos.undo() == "move of a.nef"
    pump(qapp, 0.8)
    assert (w.shoot / "a.jpg").exists() and (w.shoot / "a.nef").exists() and not (dest / "a.jpg").exists()


def test_copy_leaves_the_original_and_has_no_undo(qapp, world):
    w = world
    dest = w.tmp / "copies"
    w.files.copyRow(row_of(w.photos, "c"), str(dest))
    assert (dest / "c.jpg").exists() and (w.shoot / "c.jpg").exists()
    assert w.photos.canUndo is False and w.toasts.items[-1]["title"] == "Copied c.jpg"


def test_a_name_taken_in_the_destination_gets_a_new_name_not_overwritten(qapp, world):
    w = world
    dest = w.tmp / "copies"
    dest.mkdir()
    (dest / "c.jpg").write_bytes(b"other")
    w.files.copyRow(row_of(w.photos, "c"), str(dest))
    assert (dest / "c.jpg").read_bytes() == b"other" and (dest / "c_1.jpg").exists()


def test_moving_into_the_same_folder_is_refused(qapp, world):
    w = world
    w.files.moveRow(row_of(w.photos, "c"), str(w.shoot))
    assert w.toasts.items[-1]["kind"] == "warn" and (w.shoot / "c.jpg").exists()


def test_destinations_are_remembered_and_listed_without_the_current_folder(qapp, world):
    w = world
    dest = w.tmp / "delivery" / "album"
    w.files.copyRow(row_of(w.photos, "c"), str(dest))
    assert w.files.recent()[0] == str(dest)
    other = w.tmp / "else"
    other.mkdir()
    w.files.copyRow(row_of(w.photos, "c"), str(other))
    assert w.files.recent()[:2] == [str(other), str(dest)]
    w.files.copyRow(row_of(w.photos, "c"), str(w.shoot / "sub"))
    assert str(w.shoot) not in w.files.recent()


def test_a_failed_move_changes_nothing(qapp, world, monkeypatch):
    w = world
    monkeypatch.setattr(fa, "move_paths", lambda paths, dest: (_ for _ in ()).throw(OSError("disk is full")))
    w.files.moveRow(row_of(w.photos, "c"), str(w.tmp / "x"))
    assert (w.shoot / "c.jpg").exists() and "c.jpg" in names(w.photos)
    assert w.toasts.items[-1]["kind"] == "error" and "disk is full" in w.toasts.items[-1]["body"]


def test_deleting_inside_a_recycle_bin_asks_first_and_is_for_good(qapp, tmp_path):
    bin_dir = tmp_path / "recycle bin"
    bin_dir.mkdir()
    (bin_dir / "x.jpg").write_bytes(b"\xff\xd8\xff\xd9")
    photos, toasts = PhotoModel(parent=qapp), ToastModel(parent=qapp)
    files = FileActions(photos, toasts, parent=qapp)
    load(qapp, photos, bin_dir)
    asked = []
    files.confirmRequested.connect(lambda *a: asked.append(a))
    files.deleteRow(0)
    assert asked and "permanently" in asked[0][1] and (bin_dir / "x.jpg").exists()
    files.answer(asked[0][0], False)
    assert (bin_dir / "x.jpg").exists()
    files.deleteRow(0)
    files.answer(asked[1][0], True)
    assert not (bin_dir / "x.jpg").exists()


def test_system_trash_that_fails_asks_before_deleting(qapp, world, monkeypatch):
    w = world
    monkeypatch.setattr(FileActions, "_delete_mode", lambda self: "System Trash")
    monkeypatch.setattr(fa.QFile, "moveToTrash", lambda self: False, raising=False)
    asked = []
    w.files.confirmRequested.connect(lambda *a: asked.append(a))
    w.files.deleteRow(row_of(w.photos, "c"))
    assert asked and (w.shoot / "c.jpg").exists()
    w.files.answer(asked[0][0], True)
    assert not (w.shoot / "c.jpg").exists()


def test_sample_photos_have_no_file_to_change(qapp):
    photos, toasts = PhotoModel(parent=qapp), ToastModel(parent=qapp)
    photos.setRows([{"path": "x.jpg", "name": "x.jpg", "ext": "JPG", "raw": False, "decision": "", "rating": 0, "ai": 1}], "sample")
    files = FileActions(photos, toasts, memory=True, parent=qapp)
    files.deleteRow(0)
    assert photos.shown == 1 and toasts.items[-1]["title"] == "These are sample photos"


def test_the_menu_asks_for_a_folder_and_lists_recent_ones(qapp, world):
    from image_triage.qml_shell.cull_model import CullModel
    from image_triage.qml_shell.collections_model import CollectionsModel
    from image_triage.qml_shell.menu_model import MenuModel

    w = world
    requests = []
    menu = MenuModel(w.photos, CollectionsModel(w.photos, memory=True, parent=qapp), CullModel(w.photos, memory=True, parent=qapp), w.toasts, parent=qapp, files=w.files)
    menu.uiRequest.connect(lambda what, arg: requests.append((what, arg)))
    dest = w.tmp / "delivery" / "album"
    w.files.copyRow(row_of(w.photos, "c"), str(dest))
    menu.openPhoto(row_of(w.photos, "b"))
    menu.setSub("mv")
    keys = [i.get("key") for i in menu.view["subItems"] if i.get("kind") == "row"]
    assert keys == ["mv:copy", "mv:move", "mv:r0"] and menu.view["subItems"][-1]["label"] == "delivery / album"
    row = row_of(w.photos, "b")
    menu.act("mv:move")
    assert requests == [("chooseDest", f"move:{row}")]
    menu.openPhoto(row)
    menu.setSub("mv")
    menu.act("mv:r0")
    assert (dest / "b.jpg").exists() and not (w.shoot / "b.jpg").exists()
    menu.openPhoto(row_of(w.photos, "c"))
    menu.act("del")
    assert not (w.shoot / "c.jpg").exists()


def test_the_viewers_delete_shows_the_photo_that_took_its_place(qapp, world, monkeypatch):
    from types import SimpleNamespace

    from image_triage.qml_shell.popout import PopoutHost

    from PySide6.QtCore import QObject, Signal

    class Thumbs(QObject):
        ready = Signal(str, int)

    w = world
    popout = PopoutHost(w.photos, Thumbs(parent=qapp), None, prebuild=False, parent=qapp, files=w.files)
    shown, closed = [], []
    popout._show = lambda row: shown.append(row)
    popout._preview = SimpleNamespace(close=lambda: closed.append(True))
    start = w.photos.shown
    first = w.photos.get(0)["path"]
    popout._delete(first)
    assert w.photos.shown == start - 1 and shown == [0] and not os.path.exists(first)
    for _ in range(w.photos.shown):
        popout._delete(w.photos.get(0)["path"])
    assert w.photos.shown == 0 and closed                                       # the last one went: the viewer is put away
    popout._delete("nothing/here.jpg")                                           # a path that is not in the grid does nothing


def test_the_viewers_move_asks_for_a_folder(qapp, world, monkeypatch):
    from types import SimpleNamespace

    from PySide6.QtCore import QObject, Signal
    from PySide6.QtWidgets import QFileDialog

    from image_triage.qml_shell.popout import PopoutHost

    class Thumbs(QObject):
        ready = Signal(str, int)

    w = world
    popout = PopoutHost(w.photos, Thumbs(parent=qapp), None, prebuild=False, parent=qapp, files=w.files)
    popout._show = lambda row: None
    popout._preview = SimpleNamespace(close=lambda: None)
    dest = w.tmp / "from-viewer"
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", staticmethod(lambda *a, **k: str(dest)))
    popout._move(str(w.shoot / "c.jpg"))
    assert (dest / "c.jpg").exists() and not (w.shoot / "c.jpg").exists()
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", staticmethod(lambda *a, **k: ""))
    popout._move(str(w.shoot / "b.jpg"))                                         # the dialog was cancelled
    assert (w.shoot / "b.jpg").exists()
