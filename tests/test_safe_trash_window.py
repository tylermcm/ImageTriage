"""The Safe Trash window: what a delete leaves is listed photo by photo, comes back whole with its decision and edit data, is
never restored over another file or into a folder that is gone, can be deleted for good, and is kept under a size limit.
The app's recycle folder stays out of ordinary browsing."""
from __future__ import annotations

import os
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QCoreApplication, QSettings, QStandardPaths, QUrl
from PySide6.QtGui import QGuiApplication

from image_triage import safe_trash_store as store
from image_triage.edit_storage import EDIT_STORAGE_ROOT_NAME
from image_triage.file_ops import FileMove
from image_triage.models import SessionAnnotation
from image_triage.qml_shell import decision_bridge as bridge_module
from image_triage.qml_shell import file_actions as fa
from image_triage.qml_shell.decision_bridge import DecisionBridge
from image_triage.qml_shell.file_actions import FileActions
from image_triage.qml_shell.file_jobs import FileJobs
from image_triage.qml_shell.menu_model import ToastModel
from image_triage.qml_shell.photos import PhotoModel
from image_triage.qml_shell.safe_trash_model import SafeTrashModel
from image_triage.safe_trash import MANIFEST_NAME, SafeTrash


@pytest.fixture(scope="module")
def qapp():
    return QCoreApplication.instance() or QGuiApplication([])


def wait_for(app, condition, seconds=10.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        if condition():
            return True
        time.sleep(0.005)
    return False


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    QStandardPaths.setTestModeEnabled(True)
    monkeypatch.setenv("IMAGE_TRIAGE_APPDATA", str(tmp_path / "appdata"))
    session = "qml-trash-" + tmp_path.name
    monkeypatch.setattr(bridge_module, "session_id", lambda: session)
    monkeypatch.setattr(fa, "session_id", lambda: session)
    monkeypatch.setattr(FileActions, "_delete_mode", lambda self: fa.SAFE_TRASH)
    monkeypatch.setattr(SafeTrash, "recycle_root_for_folder", lambda self, folder=None: tmp_path / "trash")
    monkeypatch.setattr(SafeTrash, "is_temporary_storage_folder", lambda self, folder=None: False)
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    monkeypatch.setattr(FileActions, "_settings", lambda self: settings)
    yield settings
    QStandardPaths.setTestModeEnabled(False)


@pytest.fixture
def world(qapp, tmp_path, isolated):
    shoot = tmp_path / "shoot"
    shoot.mkdir()
    for name in ("a", "b", "c"):
        (shoot / f"{name}.jpg").write_bytes(b"\xff\xd8\xff\xd9" + name.encode() * 100)
    (shoot / "a.nef").write_bytes(b"raw-a")
    photos, toasts = PhotoModel(parent=qapp), ToastModel(parent=qapp)
    bridge = DecisionBridge(photos, toasts=toasts, parent=qapp)
    jobs = FileJobs(parent=qapp)
    files = FileActions(photos, toasts, parent=qapp, jobs=jobs)
    trash = SafeTrashModel(photos, toasts, jobs, parent=qapp, settings=isolated, roots=lambda: [str(tmp_path / "trash")])
    photos.loadFolder(str(shoot))
    assert wait_for(qapp, lambda: not photos.loading and photos.total == 3)
    yield SimpleNamespace(shoot=shoot, photos=photos, toasts=toasts, bridge=bridge, files=files, trash=trash, tmp=tmp_path, root=tmp_path / "trash", settings=isolated)
    files.shutdown()


def row_of(photos, stem):
    return next(i for i in range(photos.shown) if photos.get(i)["name"].startswith(stem))


def delete(w, *stems):
    w.files.deleteRows([row_of(w.photos, stem) for stem in stems])
    assert w.files.waitForIdle()


def listed(w):
    w.trash.show()
    assert w.trash.waitForIdle()
    return w.trash._rows.rows


def pick(w, *names):
    for index, row in enumerate(w.trash._rows.rows):
        if row["name"] in names:
            w.trash.toggle(index)


def reload(qapp, photos):
    assert wait_for(qapp, lambda: not photos.loading)


# ---- the store: photos, not files -------------------------------------------------------------------------------------------------

def _put(root, shoot, name, data=b"x", record=True):
    root.mkdir(exist_ok=True)
    (root / name).write_bytes(data)
    if record:
        SafeTrash(str(shoot)).remember_recycle_origins((FileMove(source_path=str(shoot / name), target_path=str(root / name)),))
    return str(root / name)


def test_files_deleted_by_an_older_build_are_grouped_into_photos_by_where_they_came_from(tmp_path):
    root, shoot = tmp_path / "trash", tmp_path / "shoot"
    shoot.mkdir()
    for name in ("a.nef", "a.jpg", "a.xmp", "b.jpg"):
        _put(root, shoot, name)
    (root / "a.nef.cameraraw.json").write_text("{}")
    SafeTrash(str(shoot)).remember_recycle_origins((FileMove(str(shoot / EDIT_STORAGE_ROOT_NAME / "a.nef.cameraraw.json"), str(root / "a.nef.cameraraw.json")),))
    _put(root, shoot, "stray.jpg", record=False)

    entries = {entry.name: entry for entry in store.list_entries(root)}

    assert sorted(entries) == ["a.nef", "b.jpg", "stray.jpg"]                     # the manifest and the photo record are not photos
    assert sorted(os.path.basename(p) for p in entries["a.nef"].trash_paths) == ["a.jpg", "a.nef", "a.nef.cameraraw.json", "a.xmp"]
    assert entries["a.nef"].folder == str(shoot) and entries["stray.jpg"].folder == "" and not entries["a.nef"].recorded
    assert entries["a.nef"].thumbnail.endswith("a.jpg")                          # the JPEG is cheap to show; the RAW is not asked


def test_a_photo_is_never_restored_over_a_file_or_into_a_folder_that_is_gone(tmp_path):
    root, shoot, gone = tmp_path / "trash", tmp_path / "shoot", tmp_path / "gone"
    shoot.mkdir()
    for name in ("a.nef", "a.jpg"):
        _put(root, shoot, name)
    _put(root, gone, "z.jpg")
    (shoot / "a.jpg").write_bytes(b"a new photo with the old name")

    result = store.restore_entries(store.list_entries(root))

    assert not result["done"] and [entry.name for entry in result["missing"]] == ["z.jpg"]
    why = {entry.name: reason for entry, reason in result["failed"]}
    assert "already in the original folder" in why["a.nef"] and "no longer there" in why["z.jpg"]
    assert (shoot / "a.jpg").read_bytes() == b"a new photo with the old name" and not (shoot / "a.nef").exists()   # the bundle was not split
    assert not gone.exists() and sorted(p.name for p in root.iterdir() if not p.name.startswith(".")) == ["a.jpg", "a.nef", "z.jpg"]


def test_restoring_into_a_chosen_folder_keeps_the_bundle_and_its_edits_together_under_a_free_name(tmp_path):
    root, gone, album = tmp_path / "trash", tmp_path / "gone", tmp_path / "album"
    album.mkdir()
    (album / "a.jpg").write_bytes(b"theirs")
    for name in ("a.nef", "a.jpg"):
        _put(root, gone, name)
    (root / "a.nef.cameraraw.json").write_text("{}")
    SafeTrash(str(gone)).remember_recycle_origins((FileMove(str(gone / EDIT_STORAGE_ROOT_NAME / "a.nef.cameraraw.json"), str(root / "a.nef.cameraraw.json")),))

    result = store.restore_entries(store.list_entries(root), to_folder=str(album))

    assert len(result["done"]) == 1 and not result["failed"]
    assert (album / "a.jpg").read_bytes() == b"theirs" and (album / "a_1.nef").exists() and (album / "a_1.jpg").exists()
    assert (album / EDIT_STORAGE_ROOT_NAME / "a_1.nef.cameraraw.json").exists()
    assert not (root / MANIFEST_NAME).exists() and store.list_entries(root) == []


def test_the_limit_removes_whole_photos_oldest_first_and_never_the_ones_just_deleted(tmp_path):
    root, shoot = tmp_path / "trash", tmp_path / "shoot"
    paths = {}
    for age, name in enumerate(("old.jpg", "mid.jpg", "new.jpg")):
        paths[name] = _put(root, shoot, name, b"x" * 1000)
        store.remember_photos(root, [(name, [paths[name]], None, "")])
    known = store.load_photos(root)
    for item, deleted in zip(known, (100.0, 200.0, 300.0)):
        item["deleted"] = deleted
    store._write_json(root / store.PHOTOS_NAME, known)

    assert [entry.name for entry in store.over_limit(store.list_entries(root), 2500)] == ["old.jpg"]
    assert [entry.name for entry in store.over_limit(store.list_entries(root), 0)] == []           # no limit
    result = store.enforce_limit(root, 1500, protect=[paths["old.jpg"]])

    assert [entry.name for entry in result["done"]] == ["mid.jpg", "new.jpg"]                      # old.jpg is the delete in hand
    assert sorted(p.name for p in root.iterdir() if not p.name.startswith(".")) == ["old.jpg"]
    assert list(store.load_manifest(root)) == [paths["old.jpg"]]


def test_recycle_folders_are_found_on_removable_drives_only(tmp_path):
    card, disk = tmp_path / "card", tmp_path / "disk"
    (card / "DCIM" / "recycle bin").mkdir(parents=True)
    (card / "DCIM" / "100CANON").mkdir()
    (disk / "recycle bin").mkdir(parents=True)
    assert store.removable_trash_roots([str(card), str(disk)], lambda root: root == str(card)) == [str(card / "DCIM" / "recycle bin")]


# ---- the window's model -----------------------------------------------------------------------------------------------------------

def test_a_deleted_photo_is_one_row_with_where_it_was_and_what_it_had(qapp, world):
    w = world
    w.photos.setDecision(row_of(w.photos, "a"), "win")
    w.photos.setRating(row_of(w.photos, "a"), 4)
    w.bridge.flush()
    delete(w, "a", "b")

    rows = listed(w)

    assert sorted(row["name"] for row in rows) == ["a.nef", "b.jpg"] and w.trash.count == 2
    a = next(row for row in rows if row["name"] == "a.nef")
    assert a["folder"] == str(w.shoot) and not a["folderMissing"] and a["files"] >= 3 and a["decision"] == "Winner, 4 stars"
    assert a["deleted"].startswith("Today") and Path(QUrl(a["thumb"]).toLocalFile()) == w.root / "a.jpg"
    assert w.trash.totalText.endswith("bytes") or w.trash.totalText.endswith("KB")
    assert w.trash.rows.rowCount() == 2


def test_restore_brings_the_photo_back_with_its_decision_and_its_edit_data(qapp, world):
    from image_triage.decision_store import DecisionStore

    w = world
    edits = w.shoot / EDIT_STORAGE_ROOT_NAME
    edits.mkdir()
    (edits / "a.nef.cameraraw.json").write_text('{"version": 1}', encoding="utf-8")
    w.photos.setRating(row_of(w.photos, "a"), 5)
    w.bridge.flush()
    delete(w, "a", "c")
    assert DecisionStore().load_annotations_for_paths(fa.session_id(), {}, []) == {} and not (edits / "a.nef.cameraraw.json").exists()
    w.photos._history.clear()                                    # a later session: the delete's own Undo is gone

    listed(w)
    pick(w, "a.nef")
    assert w.trash.selectedCount == 1
    w.trash.restore(False)
    assert w.trash.waitForIdle()

    assert (w.shoot / "a.nef").exists() and (w.shoot / "a.jpg").exists() and (edits / "a.nef.cameraraw.json").exists()
    assert not (w.shoot / "c.jpg").exists() and [row["name"] for row in w.trash._rows.rows] == ["c.jpg"]     # only the chosen one
    assert wait_for(qapp, lambda: not w.photos.loading and w.photos.total == 2)                             # the open folder shows it again
    assert w.photos.get(row_of(w.photos, "a"))["rating"] == 5
    assert w.toasts.items[-1]["title"] == "Restored a.nef"


def test_restore_all_reports_what_it_had_to_leave_and_why(qapp, world):
    w = world
    delete(w, "b", "c")
    (w.shoot / "b.jpg").write_bytes(b"a new photo with the old name")

    listed(w)
    w.trash.restore(True)
    assert w.trash.waitForIdle()

    assert (w.shoot / "c.jpg").exists() and (w.shoot / "b.jpg").read_bytes() == b"a new photo with the old name"
    assert [row["name"] for row in w.trash._rows.rows] == ["b.jpg"]
    assert len(w.trash.report) == 1 and "b.jpg" in w.trash.report[0] and "already in the original folder" in w.trash.report[0]
    assert any(t["title"] == "1 photo could not be restored" for t in w.toasts.items)


def test_a_photo_whose_folder_is_gone_says_so_and_can_be_restored_into_a_chosen_folder(qapp, world):
    import shutil

    from image_triage.library_store import LibraryStore

    w = world
    picks = LibraryStore().create_collection(name="Picks", item_paths=[str(w.shoot / "b.jpg")])
    delete(w, "b")
    w.photos.loadFolder(str(w.tmp))
    shutil.rmtree(w.shoot)

    rows = listed(w)
    assert rows[0]["folderMissing"] and w.trash.missingCount == 1
    pick(w, "b.jpg")
    assert w.trash.selectedNeedFolder == 1
    w.trash.restore(False)
    assert w.trash.waitForIdle()
    assert not w.shoot.exists() and (w.root / "b.jpg").exists()                  # not into a folder made up for it
    assert "no longer there" in w.trash.report[0]

    album = w.tmp / "album"
    album.mkdir()
    w.trash.restoreTo(False, QUrl.fromLocalFile(str(album)).toString())
    assert w.trash.waitForIdle()
    assert (album / "b.jpg").exists() and w.trash.count == 0
    assert LibraryStore().load_collection(picks.id).item_paths == (str(album / "b.jpg"),) or list(LibraryStore().load_collection(picks.id).item_paths) == [str(album / "b.jpg")]


def test_delete_for_good_removes_only_the_chosen_photos_and_empty_removes_everything(qapp, world):
    w = world
    delete(w, "a", "b", "c")
    listed(w)
    pick(w, "b.jpg")
    w.trash.purge(False)
    assert w.trash.waitForIdle()
    left = sorted(p.name for p in w.root.iterdir() if not p.name.startswith("."))
    assert "b.jpg" not in left and "a.nef" in left and "c.jpg" in left and w.trash.count == 2
    assert w.toasts.items[-1]["title"] == "Deleted 1 photo for good"

    w.trash.purge(True)
    assert w.trash.waitForIdle()
    assert w.trash.count == 0 and [p.name for p in w.root.iterdir()] == []        # the records went with the last photo
    assert not (w.shoot / "a.nef").exists()


def test_the_size_limit_is_saved_and_applied_oldest_first_when_it_is_lowered(qapp, world, monkeypatch):
    import image_triage.qml_shell.safe_trash_model as model_module

    w = world
    delete(w, "b")
    delete(w, "c")
    known = store.load_photos(w.root)
    known[0]["deleted"] -= 1000                                  # b.jpg was deleted long before c.jpg
    store._write_json(w.root / store.PHOTOS_NAME, known)
    listed(w)
    assert w.trash.limit == 0 and w.trash.limitWouldRemove(1) == 0 and "deleted for good" in w.trash.limitNote
    assert {"label": "No limit", "value": 0} in w.trash.limitOptions

    monkeypatch.setattr(model_module, "GIGABYTE", 150)           # two 104-byte photos: a "gigabyte" that only one of them fits in
    assert w.trash.limitWouldRemove(1) == 1                      # what the window asks about before it sets the limit
    w.trash.setLimit(1)
    assert w.trash.waitForIdle()

    assert w.settings.value(fa.LIMIT_KEY, 0, int) == 1 and w.trash.limit == 1
    assert [row["name"] for row in w.trash._rows.rows] == ["c.jpg"] and not (w.root / "b.jpg").exists()
    assert w.files.trash_limit_bytes() == 1024 ** 3               # the next delete applies the same limit


def test_a_delete_over_the_limit_evicts_older_photos_but_not_itself(qapp, world, monkeypatch):
    w = world
    delete(w, "b")
    known = store.load_photos(w.root)
    known[0]["deleted"] -= 1000
    store._write_json(w.root / store.PHOTOS_NAME, known)
    monkeypatch.setattr(FileActions, "trash_limit_bytes", lambda self: 150)       # smaller than one photo

    delete(w, "c")

    assert not (w.root / "b.jpg").exists() and (w.root / "c.jpg").exists()        # the delete in hand is still undoable
    assert any("left the safe trash for good" in t["title"] for t in w.toasts.items)
    assert w.photos.undo() == "delete of c.jpg" and (w.shoot / "c.jpg").exists()


def test_the_window_works_while_another_file_operation_runs_by_refusing(qapp, world, monkeypatch):
    import threading

    w = world
    delete(w, "b")
    listed(w)
    gate = threading.Event()
    real = fa.move_paths
    monkeypatch.setattr(fa, "move_paths", lambda paths, dest: (gate.wait(10), real(paths, dest))[1])
    w.files.moveRows([row_of(w.photos, "c")], str(w.tmp / "album"))
    w.trash.restore(True)
    assert w.toasts.items[-1]["title"] == "Another file operation is still running" and (w.root / "b.jpg").exists()
    gate.set()
    assert w.files.waitForIdle()


# ---- out of ordinary browsing (D28) ----------------------------------------------------------------------------------------------

def test_the_apps_recycle_folder_is_not_listed_among_sub_folders_or_seen_by_the_watcher(tmp_path):
    from image_triage.qml_shell.library_nav import _listing
    from image_triage.scanner import scan_child_folders, scan_folder

    card = tmp_path / "DCIM"
    (card / "100CANON").mkdir(parents=True)
    (card / "recycle bin").mkdir()
    (card / "recycle bin" / "deleted.jpg").write_bytes(b"\xff\xd8\xff\xd9")
    assert [record.name for record in scan_child_folders(str(card))] == ["100CANON"]
    assert _listing(str(card)) == frozenset({"100CANON"})
    assert [record.name for record in scan_folder(str(card / "recycle bin"))] == ["deleted.jpg"]      # still opens by its path


# ---- the window itself -----------------------------------------------------------------------------------------------------------

def test_the_window_opens_lists_ticks_and_closes_without_qml_warnings(qapp):
    from PySide6.QtCore import Q_ARG, QMetaObject, QObject
    from PySide6.QtQml import QQmlApplicationEngine

    from image_triage.qml_shell import app as shell_app
    from image_triage.qml_shell.batch_model import BatchModel
    from image_triage.qml_shell.filters_model import FiltersModel
    from image_triage.qml_shell.handoff_model import HandoffModel
    from image_triage.qml_shell.palette_model import PaletteModel
    from image_triage.qml_shell.photos import GridModel
    from image_triage.qml_shell.settings_model import SettingsModel
    from image_triage.qml_shell.theme import Theme
    from image_triage.qml_shell.thumbs import ThumbService
    from image_triage.qml_shell.transfer_model import TransferModel
    from tests.qml_support import collection_models

    photos = PhotoModel(parent=qapp)
    engine = QQmlApplicationEngine()
    warnings: list[str] = []
    engine.warnings.connect(lambda items: warnings.extend(str(i) for i in items))
    context = engine.rootContext()
    models = dict(collection_models(photos, qapp))
    for name, obj in (("theme", Theme(parent=qapp)), ("photos", photos), ("settings", SettingsModel(memory=True, parent=qapp)),
                      ("cmdPalette", PaletteModel(memory=True, parent=qapp)), ("advFilters", FiltersModel(photos, memory=True, parent=qapp)),
                      ("batch", BatchModel(photos, memory=True, parent=qapp)), *models.items(),
                      ("handoff", HandoffModel(photos, memory=True, parent=qapp)), ("transfers", TransferModel(photos, memory=True, parent=qapp)),
                      ("thumbs", ThumbService(parent=qapp)), ("startup", "")):
        context.setContextProperty(name, obj)
    engine.addImportPath(str(shell_app.QML_DIR))
    engine.load(QUrl.fromLocalFile(str(shell_app.QML_DIR / "Main.qml")))
    assert engine.rootObjects(), warnings
    window = engine.rootObjects()[0]
    window.setWidth(1440)
    window.setHeight(900)
    trash = models["safeTrash"]
    card = window.findChild(QObject, "safeTrashWindow")
    assert card is not None and not card.property("visible")

    QMetaObject.invokeMethod(window, "runCommand", Q_ARG("QVariant", "file.safe_trash"))     # the File menu's row and the palette's command
    wait_for(qapp, lambda: False, 0.6)
    assert trash.open and card.property("visible") and card.property("activeFocus")
    listing = window.findChild(QObject, "safeTrashList")
    assert listing.property("count") == 4 and window.property("modalInputBlocked")           # the sample rows; the app behind is not usable
    assert window.findChild(QObject, "safeTrashDelete").property("enabled") is False         # nothing is chosen yet
    trash.toggle(0)
    wait_for(qapp, lambda: False, 0.1)
    assert window.findChild(QObject, "safeTrashDelete").property("enabled") is True
    assert window.findChild(QObject, "safeTrashRestore").property("text") == "Restore 1"

    QMetaObject.invokeMethod(card, "askPurge", Q_ARG("QVariant", False))                     # deleting for good asks first
    wait_for(qapp, lambda: False, 0.4)
    ask = window.findChild(QObject, "safeTrashPurgeCard")
    assert ask.property("opened") and "cannot be undone" in ask.property("body") and trash.count == 4
    ask.close()
    wait_for(qapp, lambda: False, 0.3)
    assert trash.count == 4                                                                   # closing the question deletes nothing

    QMetaObject.invokeMethod(card, "dismissed")                                               # Escape and a click outside both end here
    wait_for(qapp, lambda: False, 0.5)
    assert not trash.open and not card.property("visible")
    assert not [w for w in warnings if any(k in w for k in ("TypeError", "ReferenceError", "is not a type", "Unable to assign", "non-existent", "Binding loop", "Cannot override", "SafeTrashWindow"))], warnings
    window.close()
