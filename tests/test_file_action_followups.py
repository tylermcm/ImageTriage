"""What has to follow a photo the app moves, renames, copies or trashes: its place in collections, its edit data, its whole
bundle; and the file actions' cost on the UI thread."""
from __future__ import annotations

import time
from types import SimpleNamespace

import pytest
from PIL import Image
from PySide6.QtCore import QCoreApplication, QSettings, QStandardPaths
from PySide6.QtGui import QGuiApplication

from image_triage.edit_carry import copy_edits
from image_triage.edit_storage import EDIT_STORAGE_ROOT_NAME
from image_triage.library_store import LibraryStore
from image_triage.qml_shell import decision_bridge as bridge_module
from image_triage.qml_shell import file_actions as fa
from image_triage.qml_shell.batch_model import BatchModel
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


def wait_for(app, condition, seconds=10.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        pump(app, 0.05)
        if condition():
            return True
    return False


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    QStandardPaths.setTestModeEnabled(True)
    monkeypatch.setenv("IMAGE_TRIAGE_APPDATA", str(tmp_path / "appdata"))
    session = "qml-follow-" + tmp_path.name
    monkeypatch.setattr(bridge_module, "session_id", lambda: session)
    monkeypatch.setattr(fa, "session_id", lambda: session)
    monkeypatch.setattr(FileActions, "_delete_mode", lambda self: fa.SAFE_TRASH)
    from image_triage.safe_trash import SafeTrash

    monkeypatch.setattr(SafeTrash, "recycle_root_for_folder", lambda self, folder=None: tmp_path / "trash")
    monkeypatch.setattr(SafeTrash, "is_temporary_storage_folder", lambda self, folder=None: False)
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    monkeypatch.setattr(FileActions, "_settings", lambda self: settings)
    yield
    QStandardPaths.setTestModeEnabled(False)


@pytest.fixture
def world(qapp, tmp_path):
    shoot = tmp_path / "shoot"
    shoot.mkdir()
    for index, name in enumerate(("a", "b", "c")):
        Image.new("RGB", (40 + index, 30), (30 * index, 90, 160)).save(shoot / f"{name}.jpg", "JPEG")
    (shoot / "a.nef").write_bytes(b"raw-a")
    photos = PhotoModel(parent=qapp)
    toasts = ToastModel(parent=qapp)
    bridge = DecisionBridge(photos, toasts=toasts, parent=qapp)
    files = FileActions(photos, toasts, parent=qapp)
    photos.loadFolder(str(shoot))
    assert wait_for(qapp, lambda: not photos.loading and photos.total == 3)
    return SimpleNamespace(shoot=shoot, photos=photos, toasts=toasts, bridge=bridge, files=files, tmp=tmp_path)


def row_of(photos, stem):
    return next(i for i in range(photos.shown) if photos.get(i)["name"].startswith(stem))


def path_of(photos, stem):
    return photos.get(row_of(photos, stem))["path"]


def collection_names(store, collection_id):
    return [p.replace("\\", "/").rsplit("/", 2)[-2] + "/" + p.replace("\\", "/").rsplit("/", 1)[-1] for p in store.load_collection(collection_id).item_paths]


# ---- collections --------------------------------------------------------------------------------------------------------------

def test_a_collection_follows_a_moved_photo_and_comes_back_on_undo(qapp, world):
    w = world
    store = LibraryStore()
    picks = store.create_collection(name="Picks", item_paths=[path_of(w.photos, "a"), path_of(w.photos, "b")])
    primary = path_of(w.photos, "a").replace("\\", "/").rsplit("/", 1)[-1]          # a.nef or a.jpg, whichever the bundle is known by
    dest = w.tmp / "album"

    w.files.moveRows([row_of(w.photos, "a")], str(dest))
    assert w.files.waitForIdle()

    assert (dest / primary).exists()
    assert collection_names(store, picks.id) == [f"album/{primary}", "shoot/b.jpg"]
    assert w.photos.undo()
    pump(qapp, 0.3)
    assert collection_names(store, picks.id) == [f"shoot/{primary}", "shoot/b.jpg"]


def test_a_collection_follows_a_batch_rename_and_its_undo(qapp, world):
    w = world
    store = LibraryStore()
    picks = store.create_collection(name="Picks", item_paths=[path_of(w.photos, "b"), path_of(w.photos, "c")])
    batch = BatchModel(w.photos, memory=False, parent=qapp, toasts=w.toasts)
    batch.begin("rename")
    batch.set("prefix", "wed_")
    assert batch.canApply
    batch.apply()
    assert wait_for(qapp, lambda: not batch.busy and any(t["title"].startswith("Renamed") for t in w.toasts.items))

    assert collection_names(store, picks.id) == ["shoot/wed_b_002.jpg", "shoot/wed_c_003.jpg"]
    assert wait_for(qapp, lambda: not w.photos.loading)
    assert w.photos.undo()
    assert wait_for(qapp, lambda: (w.shoot / "b.jpg").exists())
    assert collection_names(store, picks.id) == ["shoot/b.jpg", "shoot/c.jpg"]


def test_rename_item_paths_keeps_the_place_and_drops_a_duplicate(tmp_path, monkeypatch):
    store = LibraryStore()
    a, b, c = (str(tmp_path / name) for name in ("a.jpg", "b.jpg", "c.jpg"))
    one = store.create_collection(name="One", item_paths=[a, b, c])
    two = store.create_collection(name="Two", item_paths=[a, c])

    assert store.rename_item_paths([(a, str(tmp_path / "z.jpg")), (b, c)]) == 3

    assert [p.rsplit("/", 1)[-1].rsplit("\\", 1)[-1] for p in store.load_collection(one.id).item_paths] == ["z.jpg", "c.jpg"]
    assert [p.rsplit("/", 1)[-1].rsplit("\\", 1)[-1] for p in store.load_collection(two.id).item_paths] == ["z.jpg", "c.jpg"]


# ---- copy takes the edit data -------------------------------------------------------------------------------------------------

def test_copying_a_photo_copies_its_edit_data_and_leaves_the_original_its_own(qapp, world):
    w = world
    root = w.shoot / EDIT_STORAGE_ROOT_NAME
    root.mkdir()
    (root / "b.jpg.cameraraw.json").write_text('{"version": 1}', encoding="utf-8")
    dest = w.tmp / "album"

    w.files.copyRows([row_of(w.photos, "b")], str(dest))
    assert w.files.waitForIdle()

    assert (dest / "b.jpg").exists() and (w.shoot / "b.jpg").exists()
    assert (dest / EDIT_STORAGE_ROOT_NAME / "b.jpg.cameraraw.json").read_text(encoding="utf-8") == '{"version": 1}'
    assert (root / "b.jpg.cameraraw.json").exists()


def test_copy_edits_never_replaces_edit_data_already_there(tmp_path):
    src, dst = tmp_path / "src", tmp_path / "dst"
    (src / EDIT_STORAGE_ROOT_NAME).mkdir(parents=True)
    (dst / EDIT_STORAGE_ROOT_NAME).mkdir(parents=True)
    (src / EDIT_STORAGE_ROOT_NAME / "a.nef.cameraraw.json").write_text("new")
    (src / EDIT_STORAGE_ROOT_NAME / "a.nef.pcraft").write_text("project")
    (dst / EDIT_STORAGE_ROOT_NAME / "a.nef.cameraraw.json").write_text("theirs")

    assert copy_edits([(src / "a.nef", dst / "a.nef")]) == ["a.nef.cameraraw.json"]

    assert (dst / EDIT_STORAGE_ROOT_NAME / "a.nef.cameraraw.json").read_text() == "theirs"
    assert (dst / EDIT_STORAGE_ROOT_NAME / "a.nef.pcraft").read_text() == "project"
    assert sorted(p.name for p in (dst / EDIT_STORAGE_ROOT_NAME).iterdir()) == ["a.nef.cameraraw.json", "a.nef.pcraft"]


# ---- system trash keeps the bundle together -----------------------------------------------------------------------------------

class FakeTrash:
    """Stands in for QFile: moves a file into a folder as the system trash would, and refuses the names it is told to."""

    refuse: tuple = ()
    folder = None

    def __init__(self, path):
        self._path = str(path)

    def moveToTrash(self):
        from pathlib import Path
        import shutil

        if Path(self._path).suffix.lower() in self.refuse:
            return False
        FakeTrash.folder.mkdir(parents=True, exist_ok=True)
        target = FakeTrash.folder / Path(self._path).name
        shutil.move(self._path, target)
        self._path = str(target)
        return True

    def fileName(self):
        return self._path


def test_a_system_trash_that_refuses_one_file_brings_the_rest_of_the_bundle_back(qapp, world, monkeypatch):
    w = world
    monkeypatch.setattr(FileActions, "_delete_mode", lambda self: "System Trash")
    primary_suffix = "." + w.photos.get(row_of(w.photos, "a"))["path"].rsplit(".", 1)[-1].lower()
    FakeTrash.folder = w.tmp / "system-trash" / "files"
    FakeTrash.refuse = tuple(s for s in (".jpg", ".nef") if s != primary_suffix)       # the second file of the bundle is refused
    monkeypatch.setattr(fa, "QFile", FakeTrash)
    asked = []
    w.files.confirmRequested.connect(lambda *a: asked.append(a))

    w.files.deleteRow(row_of(w.photos, "a"))
    assert w.files.waitForIdle()

    assert asked and "could not be moved to the trash" in asked[0][2] and "Already in the trash" not in asked[0][2]
    assert (w.shoot / "a.jpg").exists() and (w.shoot / "a.nef").exists()               # together, where they were
    assert not FakeTrash.folder.exists() or not list(FakeTrash.folder.iterdir())


def test_trash_bundle_names_what_it_could_not_bring_back(tmp_path, monkeypatch):
    for name in ("a.nef", "a.jpg"):
        (tmp_path / name).write_bytes(b"x")

    class Silent(FakeTrash):
        def fileName(self):
            return ""                                    # a system that does not say where the file went

    FakeTrash.folder, FakeTrash.refuse = tmp_path / "trash", (".jpg",)
    monkeypatch.setattr(fa, "QFile", Silent)

    assert fa._trash_bundle((str(tmp_path / "a.nef"), str(tmp_path / "a.jpg"))) == (False, ["a.nef"])


# ---- cost on the UI thread ----------------------------------------------------------------------------------------------------

def test_trashing_many_photos_drops_their_decisions_in_one_write(qapp, world, monkeypatch):
    w = world
    from image_triage.decision_store import DecisionStore

    for stem in ("b", "c"):
        w.photos.setDecision(row_of(w.photos, stem), "win")
    w.bridge.flush()
    session = fa.session_id()
    assert len(DecisionStore().winner_paths(session)) == 2
    single = []
    monkeypatch.setattr(DecisionStore, "delete_annotation", lambda self, *a: single.append(a))
    made = []
    real_init = DecisionStore.__init__
    monkeypatch.setattr(DecisionStore, "__init__", lambda self: (made.append(1), real_init(self))[1])

    assert w.files.trashPaths([path_of(w.photos, "b"), path_of(w.photos, "c")], "") > 0

    assert not single and len(made) == 1
    assert DecisionStore().winner_paths(session) == []


def test_recent_destinations_on_a_share_are_listed_without_asking_the_disk(qapp, world, monkeypatch):
    w = world
    from image_triage import path_policy

    share = r"\\nas\photos\delivered"
    gone = str(w.tmp / "no-longer-here")
    here = w.tmp / "album"
    here.mkdir()
    w.files._settings().setValue(fa.RECENT_KEY, [share, gone, str(here)])
    asked = []
    real_isdir = fa.os.path.isdir
    monkeypatch.setattr(path_policy, "is_plain_local", lambda path: not str(path).startswith("\\\\"))
    monkeypatch.setattr(fa.os.path, "isdir", lambda p: (asked.append(str(p)), real_isdir(p))[1])

    assert w.files.recent() == [share, str(here)]
    assert share not in asked
