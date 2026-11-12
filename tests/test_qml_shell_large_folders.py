"""The photo list and the collections on a large folder: what a keypress, a selection and a folder change may cost."""
from __future__ import annotations

import os
import threading
import time

import pytest
from PySide6.QtCore import QCoreApplication
from PySide6.QtGui import QGuiApplication

from image_triage.models import ImageRecord
from image_triage.qml_shell import photos as photos_module
from image_triage.qml_shell.collections_model import CollectionsModel
from image_triage.qml_shell.photos import DecisionRole, PhotoModel, SelectedRole, member_key


@pytest.fixture(scope="module")
def qapp():
    return QCoreApplication.instance() or QGuiApplication([])


def until(app, done, seconds=5.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end and not done():
        app.processEvents()
        time.sleep(0.005)
    assert done()


def synthetic_rows(folder, count):
    return [{"path": os.path.join(str(folder), f"IMG_{index:05d}.jpg"), "name": f"IMG_{index:05d}.jpg", "ext": "JPG", "raw": False,
             "decision": "", "rating": 0, "ai": -1, "companions": ()} for index in range(count)]


@pytest.fixture
def store_dir(tmp_path, monkeypatch):
    import image_triage.library_store as storage

    monkeypatch.setattr(storage, "app_data_root", lambda: tmp_path / "data")
    return tmp_path


def test_a_decision_in_a_large_folder_with_many_collections_is_instant(qapp, store_dir):
    folder = store_dir / "shoot"
    folder.mkdir()
    photos = PhotoModel(parent=qapp)
    rows = synthetic_rows(folder, 20000)
    photos.setRows(rows, str(folder))
    collections = CollectionsModel(photos, memory=False, parent=qapp)
    store = collections._db()
    for number in range(20):
        cid = store.create_collection(name=f"Set {number:02d}").id
        store.add_paths_to_collection(cid, [row["path"] for row in rows[number * 500:number * 500 + 1000]])
    collections._reload()
    assert [row["here"] for row in collections.rows] == [1000] * 20
    winners = collections.winnersCount
    costs = []
    for row in (5, 9000, 19999):
        started = time.perf_counter()
        photos.setDecision(row, "win")
        costs.append(time.perf_counter() - started)
    assert min(costs) < 0.05, costs                          # the membership is not worked out again for a decision
    assert collections.winnersCount == winners + 3           # ...and what is counted from the decisions still follows them
    assert [row["here"] for row in collections.rows] == [1000] * 20


def test_photos_leaving_the_folder_leave_the_collections_count(qapp, store_dir):
    folder = store_dir / "shoot"
    folder.mkdir()
    photos = PhotoModel(parent=qapp)
    rows = synthetic_rows(folder, 12)
    photos.setRows(rows, str(folder))
    collections = CollectionsModel(photos, memory=False, parent=qapp)
    cid = collections.save("", "Shortlist", "", "", False)
    assert collections.addRows(cid, list(range(6))) == 6
    assert collections.get(cid)["here"] == 6 and collections.get(cid)["count"] == 6
    photos.remove_paths([rows[0]["path"], rows[1]["path"]])
    assert collections.get(cid)["here"] == 4                 # the folder's photos changed: the members here are counted again
    photos.setRows(synthetic_rows(store_dir / "other", 3), str(store_dir / "other"))
    assert collections.get(cid)["here"] == 0 and collections.get(cid)["count"] == 6


def test_adding_many_rows_reads_one_collection_once(qapp, store_dir, monkeypatch):
    folder = store_dir / "shoot"
    folder.mkdir()
    photos = PhotoModel(parent=qapp)
    photos.setRows(synthetic_rows(folder, 300), str(folder))
    collections = CollectionsModel(photos, memory=False, parent=qapp)
    first = collections.save("", "First", "", "", False)
    second = collections.save("", "Second", "", "", False)
    store = collections._db()
    reads = {"all": 0, "one": []}
    list_collections, load_collection = store.list_collections, store.load_collection
    monkeypatch.setattr(store, "list_collections", lambda: reads.__setitem__("all", reads["all"] + 1) or list_collections())
    monkeypatch.setattr(store, "load_collection", lambda cid: reads["one"].append(cid) or load_collection(cid))
    assert collections.addRows(first, list(range(300))) == 300
    assert reads == {"all": 0, "one": [first]}               # one write, and only the collection that changed is read again
    assert collections.get(first)["count"] == 300 and collections.get(first)["here"] == 300 and collections.get(second)["count"] == 0
    assert collections.addSelection(second, 4) == 1 and collections.addSelection(second, 4) == 0
    assert reads["all"] == 0 and set(reads["one"]) == {first, second}
    assert collections.hasRow(second, 4) and not collections.hasRow(second, 5)
    collections.removeRow(second, 4)
    assert reads["all"] == 0 and collections.get(second)["count"] == 0 and not collections.hasRow(second, 4)


def test_a_member_saved_through_a_link_to_the_folder_is_still_a_member(qapp, store_dir):
    folder = store_dir / "shoot"
    folder.mkdir()
    link = store_dir / "link"
    try:
        link.symlink_to(folder, target_is_directory=True)
    except OSError:
        pytest.skip("links cannot be made here")
    assert member_key(str(link / "IMG_00001.JPG")) == member_key(str(folder / "img_00001.jpg"))
    photos = PhotoModel(parent=qapp)
    photos.setRows(synthetic_rows(link, 4), str(link))
    collections = CollectionsModel(photos, memory=False, parent=qapp)
    cid = collections.save("", "Linked", "", "", False)
    collections.addSelection(cid, 1)
    photos.setRows(synthetic_rows(folder, 4), str(folder))
    assert collections.get(cid)["here"] == 1 and collections.hasRow(cid, 1)


def test_loaded_rows_arrive_as_the_list_that_was_built(qapp):
    photos = PhotoModel(parent=qapp)
    rows = synthetic_rows("/photos", 5)
    photos._loaded.emit(rows, "/photos", -1)
    assert photos.all_rows() is rows                         # a `list` signal would hand over a copy of every row


def test_select_all_and_clear_are_one_change_each(qapp):
    photos = PhotoModel(parent=qapp)
    photos.setRows(synthetic_rows("/photos", 400), "/photos")
    changes = []
    photos.dataChanged.connect(lambda top, bottom, roles: changes.append((top.row(), bottom.row(), list(roles))))
    photos.selectAll()
    assert changes == [(0, 399, [SelectedRole])] and photos.picked == 400
    photos.setDecisionMany(photos.selectionRows(0), "keep")
    assert changes[1:] == [(0, 399, [DecisionRole])]
    del changes[:]
    photos.clearSelection()
    assert changes == [(0, 399, [SelectedRole])] and photos.picked == 0
    del changes[:]
    photos.toggleSelected(7, 3)
    assert changes == [(3, 7, [SelectedRole])] and photos.selectionRows(0) == [3, 7]


def test_a_read_that_was_replaced_stops_before_its_decisions_are_read(qapp, tmp_path, monkeypatch):
    first, second = tmp_path / "first", tmp_path / "second"
    release, entered = threading.Event(), threading.Event()

    def scan(folder):
        if folder == str(first):
            entered.set()
            release.wait(5)
        return [ImageRecord(path=os.path.join(folder, "IMG_0001.jpg"), name="IMG_0001.jpg", size=1, modified_ns=1)]

    monkeypatch.setattr(photos_module, "scan_folder", scan)
    monkeypatch.setattr(photos_module, "scan_child_folders", lambda folder: [])
    asked = []
    photos = PhotoModel(parent=qapp)
    photos.set_annotation_loader(lambda records: asked.append(os.path.dirname(records[0].path)) or {})
    photos.loadFolder(str(first))
    assert entered.wait(5)
    photos.loadFolder(str(second))
    until(qapp, lambda: not photos.loading)
    assert photos.folderPath == str(second) and asked == [str(second)]
    workers = [t for t in threading.enumerate() if t.name == "qml-scan"]
    release.set()
    for worker in workers:
        worker.join(5)
    qapp.processEvents()
    assert asked == [str(second)]                            # the first read ended at its next step: the decision store was not read for it
    assert photos.folderPath == str(second) and photos.total == 1
    assert not photos._found_folders and not photos._scan_errors
