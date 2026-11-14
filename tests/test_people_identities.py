"""Named people carry across folders: the app-wide store, the cautious matching, and that the face data stays on this PC.

The embeddings here are synthetic unit vectors (no face model exists in this environment), so these tests show the rules, not how
well a real recognizer separates real people."""
from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from PySide6.QtCore import QObject, QSettings

from aiculler.storage import SQLiteFeatureStore
from image_triage import people_identities as identities
from image_triage.aiculler_workflow import aiculler_db_path, build_aiculler_workflow_paths
from image_triage.people_identities import MATCH_MARGIN, MATCH_THRESHOLD, IdentityStore, source_key
from image_triage.people_search import assign_person_name, cluster_face_identities, list_person_clusters
from image_triage.quality.face import FaceRecord
from image_triage.quality.store import upsert_faces
from test_qml_shell import _load_main, pump, qapp  # noqa: F401

MODEL = "adaface-test"
DIM = 16


def person(index: int, tilt: float = 0.0, towards: int | None = None) -> np.ndarray:
    """A unit vector for person `index`, leaning `tilt` towards another axis (cosine with the pure vector is 1/sqrt(1 + tilt^2))."""
    vector = np.zeros(DIM, dtype=np.float32)
    vector[index] = 1.0
    if tilt:
        vector[towards if towards is not None else (index + 8) % DIM] = tilt
    return vector / np.linalg.norm(vector)


@pytest.fixture
def store(tmp_path):
    """The app's store for one test, in a folder of its own; switched off again afterwards so no other test meets it."""
    found = identities.enable(tmp_path / "app-data" / "people" / "identities.sqlite3")
    yield found
    identities.disable()


def folder_db(root: Path, name: str, people: dict[str, tuple[np.ndarray, int]], model: str = MODEL, threshold: float = 0.35) -> tuple[Path, Path]:
    """A folder with a face index: `people` is {label: (embedding, how many photos)}. Returns (the folder, its database)."""
    folder = root / name
    folder.mkdir(parents=True, exist_ok=True)
    db = aiculler_db_path(build_aiculler_workflow_paths(folder))
    db.parent.mkdir(parents=True, exist_ok=True)
    features = SQLiteFeatureStore(db)
    try:
        for label, (vector, count) in people.items():
            for n in range(count):
                image_id = features.upsert_image(folder / f"{label}_{n}.jpg", status="ready")
                upsert_faces(features.connection, image_id, [FaceRecord((4, 4, 40, 50), 0.9, identity_embedding=tuple(float(v) for v in vector), identity_model=model)])
        features.connection.commit()
        cluster_face_identities(features.connection, threshold=threshold, identity_model=model)
        features.connection.commit()
    finally:
        features.close()
    return folder, db


def cluster_of(connection, vector) -> int:
    best = max(list_person_clusters(connection, include_ignored=True), key=lambda c: float(np.dot(np.asarray(c.centroid, dtype=np.float32), vector)))
    return best.cluster_id


def names_in(db: Path) -> dict[int, str]:
    connection = sqlite3.connect(db)
    try:
        return {c.cluster_id: c.name for c in list_person_clusters(connection, include_ignored=True)}
    finally:
        connection.close()


def named_folder(root, store, name="first", who="Anna", vector=None):
    """A folder in which the person has named one cluster by hand, and the store has been told."""
    folder, db = folder_db(root, name, {"a": (person(0) if vector is None else vector, 4), "b": (person(1), 3)})
    connection = sqlite3.connect(db)
    try:
        assign_person_name(connection, cluster_of(connection, person(0) if vector is None else vector), who)
        connection.commit()
        identities.remember_folder_names(connection, str(folder))
    finally:
        connection.close()
    return folder, db


# -- the store ------------------------------------------------------------------------------------------------------------------------

def test_reading_never_creates_the_store_and_clearing_deletes_it(tmp_path):
    found = IdentityStore(tmp_path / "people" / "identities.sqlite3")
    assert found.names() == [] and found.match(person(0), MODEL) is None and not found.path.exists()
    found.sync_source("s1", {})
    assert not found.path.exists()                                          # nobody named: still no file
    found.sync_source("s1", {("Anna", MODEL): person(0)})
    assert found.path.exists() and found.names() == ["Anna"]
    assert found.clear() and not found.path.exists() and found.names() == []
    assert found.clear()                                                    # forgetting twice is not an error


def test_a_match_needs_to_be_close_and_clearly_ahead_of_anyone_else(tmp_path):
    found = IdentityStore(tmp_path / "identities.sqlite3")
    found.sync_source("s1", {("Anna", MODEL): person(0), ("Mark", MODEL): person(1)})
    near = found.match(person(0, tilt=0.5), MODEL)                          # cosine 0.89 with Anna, 0 with Mark
    assert near.name == "Anna" and near.score == pytest.approx(0.894, abs=0.01) and near.runner_up == pytest.approx(0.0, abs=1e-6)
    assert found.match(person(0, tilt=2.5), MODEL) is None                  # cosine 0.37: below the threshold
    assert MATCH_THRESHOLD == 0.50 and MATCH_MARGIN == 0.08
    between = (person(0) + person(1)) / np.linalg.norm(person(0) + person(1))
    assert found.match(between, MODEL) is None                              # 0.71 with both: ambiguous, so nobody
    slightly = person(0) * 1.0 + person(1) * 0.93
    assert found.match(slightly / np.linalg.norm(slightly), MODEL) is None  # ahead, but by less than the margin
    assert found.match(person(0), "another-recognizer") is None             # another embedding space is never compared
    assert found.match(np.zeros(DIM), MODEL) is None and found.match(person(0)[:8], MODEL) is None


def test_two_people_are_never_merged_and_one_person_keeps_a_representative_per_folder(tmp_path):
    found = IdentityStore(tmp_path / "identities.sqlite3")
    found.sync_source("s1", {("Anna", MODEL): person(0), ("Ann", MODEL): person(0, tilt=0.1)})
    assert found.names() == ["Ann", "Anna"]                                 # near-identical faces under two names stay two people
    assert found.match(person(0), MODEL) is None                            # and a face between them is given to neither
    found.sync_source("s1", {("Anna", MODEL): person(0)})
    found.sync_source("s2", {("anna", MODEL): person(3)})                   # the same name in another folder: the same person, a second look
    assert found.names() == ["Anna"] and found.match(person(3), MODEL).name == "Anna" and found.match(person(0), MODEL).name == "Anna"
    found.forget_source("s1")
    assert found.match(person(0), MODEL) is None and found.match(person(3), MODEL) is not None
    for n in range(identities.MAX_REPRESENTATIVES + 3):
        found.sync_source(f"folder-{n}", {("Anna", MODEL): person(4)})
        time.sleep(0.002)
    connection = sqlite3.connect(found.path)
    assert connection.execute("SELECT COUNT(*) FROM representatives WHERE name_key = 'anna'").fetchone()[0] == identities.MAX_REPRESENTATIVES
    connection.close()


def test_the_store_holds_no_folder_paths(tmp_path):
    found = IdentityStore(tmp_path / "identities.sqlite3")
    key = source_key("/photos/2026/family reunion")
    found.sync_source(key, {("Anna", MODEL): person(0)})
    assert "family reunion" not in key and len(key) == 32 and source_key("") == ""
    assert b"family reunion" not in found.path.read_bytes() and b"/photos" not in found.path.read_bytes()


# -- a folder's clusters ---------------------------------------------------------------------------------------------------------------

def test_a_person_named_in_one_folder_is_recognised_in_the_next(tmp_path, store):
    named_folder(tmp_path, store)
    assert store.names() == ["Anna"]
    folder, db = folder_db(tmp_path, "second", {"x": (person(0, tilt=0.4), 5), "y": (person(5), 2)})
    assert set(names_in(db).values()) == {""}                               # clustering alone names nobody
    connection = sqlite3.connect(db)
    try:
        named = identities.after_clustering(connection, str(folder))
        anna = cluster_of(connection, person(0))
        assert named == [(anna, "Anna")]
        assert names_in(db)[anna] == "Anna" and names_in(db)[cluster_of(connection, person(5))] == ""      # the stranger stays unnamed
        assert identities.after_clustering(connection, str(folder)) == []    # nothing more to name the second time
    finally:
        connection.close()


def test_a_name_the_store_gave_is_not_fed_back_until_the_person_confirms_it(tmp_path, store):
    named_folder(tmp_path, store)
    folder, db = folder_db(tmp_path, "second", {"x": (person(0, tilt=0.4), 5)})
    connection = sqlite3.connect(db)
    try:
        identities.after_clustering(connection, str(folder))
        rows = sqlite3.connect(store.path).execute("SELECT COUNT(*) FROM representatives").fetchone()[0]
        assert rows == 1                                                    # the store's own guess is not evidence for itself
        identities.confirm_names(connection, [cluster_of(connection, person(0))])       # named by hand in Tag People
        connection.commit()
        identities.remember_folder_names(connection, str(folder))
        assert sqlite3.connect(store.path).execute("SELECT COUNT(*) FROM representatives").fetchone()[0] == 2
    finally:
        connection.close()


def test_a_name_the_folder_already_uses_is_not_given_to_a_second_cluster(tmp_path, store):
    named_folder(tmp_path, store)
    folder, db = folder_db(tmp_path, "second", {"x": (person(0, tilt=0.3), 5), "other": (person(6), 4)})
    connection = sqlite3.connect(db)
    try:
        assign_person_name(connection, cluster_of(connection, person(6)), "Anna")       # this folder's own Anna: someone else
        connection.commit()
        assert identities.name_folder_clusters(connection) == []
        assert names_in(db)[cluster_of(connection, person(0))] == ""         # left unnamed rather than merged with her
    finally:
        connection.close()


def test_of_several_clusters_matching_one_person_only_the_best_takes_the_name(tmp_path, store):
    named_folder(tmp_path, store)
    # a folder whose own clustering is strict keeps two looks of her apart (cosine 0.63 between them, 0.98 and 0.64 with the stored Anna)
    folder, db = folder_db(tmp_path, "second", {"close": (person(0, tilt=0.2), 3), "far": (person(0, tilt=1.2, towards=9), 3)}, threshold=0.8)
    assert len(names_in(db)) == 2
    connection = sqlite3.connect(db)
    try:
        named = identities.name_folder_clusters(connection)
        assert named == [(cluster_of(connection, person(0, tilt=0.2)), "Anna")]
        assert sorted(names_in(db).values()) == ["", "Anna"]
    finally:
        connection.close()


def test_an_ambiguous_face_is_left_unnamed(tmp_path, store):
    named_folder(tmp_path, store)
    second, db2 = folder_db(tmp_path, "mark", {"m": (person(1), 4)})
    connection = sqlite3.connect(db2)
    assign_person_name(connection, cluster_of(connection, person(1)), "Mark")
    connection.commit()
    identities.remember_folder_names(connection, str(second))
    connection.close()
    between = (person(0) + person(1)) / np.linalg.norm(person(0) + person(1))
    folder, db = folder_db(tmp_path, "third", {"who": (between, 4)})
    connection = sqlite3.connect(db)
    try:
        assert identities.after_clustering(connection, str(folder)) == [] and set(names_in(db).values()) == {""}
    finally:
        connection.close()


def test_removing_a_name_in_its_folder_takes_that_folders_look_out_of_the_store(tmp_path, store):
    folder, db = named_folder(tmp_path, store)
    connection = sqlite3.connect(db)
    try:
        assign_person_name(connection, cluster_of(connection, person(0)), "")
        connection.commit()
        identities.remember_folder_names(connection, str(folder))
    finally:
        connection.close()
    assert store.names() == []


def test_nothing_happens_while_it_is_switched_off(tmp_path):
    assert identities.active() is None                                      # tests and tools: off unless switched on
    folder, db = folder_db(tmp_path, "first", {"a": (person(0), 4)})
    connection = sqlite3.connect(db)
    try:
        assign_person_name(connection, cluster_of(connection, person(0)), "Anna")
        connection.commit()
        assert identities.remember_folder_names(connection, str(folder)) == 0 and identities.after_clustering(connection, str(folder)) == []
    finally:
        connection.close()
    assert not identities.default_path().exists() or "Anna" not in IdentityStore(identities.default_path()).names()


# -- where the app calls it ------------------------------------------------------------------------------------------------------------

def test_indexing_a_folders_faces_names_the_people_it_recognises(tmp_path, store):
    from image_triage.face_index import FaceFolderIndexTask

    named_folder(tmp_path, store)
    folder, db = folder_db(tmp_path, "second", {"x": (person(0, tilt=0.4), 5)})
    features = SQLiteFeatureStore(db)
    try:
        FaceFolderIndexTask._cluster(SimpleNamespace(cluster_threshold=0.35, model_identity=MODEL, folder=str(folder)), features)
    finally:
        features.close()
    assert list(names_in(db).values()) == ["Anna"]


def test_naming_someone_in_tag_people_teaches_the_store(qapp, tmp_path, store):
    from image_triage.qml_shell.people_model import PeopleModel
    from image_triage.qml_shell.photos import PhotoModel

    folder, db = folder_db(tmp_path, "first", {"a": (person(0), 4), "b": (person(1), 3)})
    photos = PhotoModel(parent=qapp)
    photos.setRows([{"path": str(folder / "a_0.jpg"), "name": "a_0", "ext": "JPG", "raw": False, "decision": "", "rating": 0, "ai": -1}], str(folder))
    people = PeopleModel(photos, parent=qapp)
    end = time.monotonic() + 6
    while not people.listPeople("", False, True) and time.monotonic() < end:
        pump(qapp, 0.05)
    cards = people.listPeople("", False, True)
    assert len(cards) == 2 and store.names() == []
    people.name(cards[0]["id"], "Anna")                                      # the person with four photos
    assert store.names() == ["Anna"] and store.match(person(0), MODEL).name == "Anna"
    pump(qapp, 0.3)
    people.clearNames([cards[0]["id"]])
    assert store.names() == []
    assert not list(folder.rglob("identities.sqlite3"))                     # the store is the app's, not the folder's


def test_face_data_is_not_reachable_from_exports_or_pocketdrop():
    root = Path(identities.__file__).resolve().parent
    users = sorted(str(path.relative_to(root)).replace(os.sep, "/") for path in root.rglob("*.py")
                   if "people_identities" in path.read_text(encoding="utf-8") and path.name != "people_identities.py")
    assert users == ["cli_culler_index.py", "face_index.py", "qml_shell/app.py", "qml_shell/cull_model.py", "qml_shell/people_model.py"]
    assert not any(part in user for user in users for part in ("pocketdrop", "handoff", "transfer", "export", "archive", "workflow"))


# -- the AI Center ---------------------------------------------------------------------------------------------------------------------

def test_the_ai_center_shows_switches_and_forgets_the_remembered_people(qapp, tmp_path, store, monkeypatch):
    from image_triage.qml_shell import cull_model as module
    from image_triage.qml_shell.cull_model import PEOPLE_MEMORY_KEY, CullModel
    from image_triage.qml_shell.menu_model import ToastModel
    from image_triage.qml_shell.photos import PhotoModel
    from test_qml_shell_ai_center import FakeOps, until
    from image_triage.qml_shell.ai_center import AiCenterBackend

    monkeypatch.setattr(identities, "default_path", lambda: store.path)
    store.sync_source("s1", {("Anna", MODEL): person(0), ("Mark", MODEL): person(1)})
    cull = CullModel(PhotoModel(parent=qapp), memory=False, ai=AiCenterBackend(FakeOps(), parent=qapp, auto_test=False), toasts=ToastModel(qapp), parent=qapp)
    cull._settings = QSettings(str(tmp_path / "prefs.ini"), QSettings.Format.IniFormat)
    until(qapp, lambda: cull.view["center"]["rtState"] != "Checking…")
    cull.openCenter()
    until(qapp, lambda: cull.view["center"]["peopleMemory"]["count"] == 2)
    row = cull.view["center"]["peopleMemory"]
    assert row["show"] and row["on"] and row["canForget"] and "Anna, Mark" in row["desc"] and "on this PC only" in row["desc"] and "PocketDrop" in row["desc"]
    text = cull.forgetPeopleText()
    assert "2 named people" in text["body"] and "cannot be undone" in text["body"] and store.path.exists()
    cull.setPeopleMemory(False)                                             # off: nothing is read or written, nothing is deleted
    assert identities.active() is None and cull._settings.value(PEOPLE_MEMORY_KEY, True, type=bool) is False and store.path.exists()
    until(qapp, lambda: not cull.view["center"]["peopleMemory"]["on"] and cull.view["center"]["peopleMemory"]["count"] == 2)
    assert cull.view["center"]["peopleMemory"]["canForget"] and cull.view["center"]["peopleMemory"]["desc"].startswith("Off.")
    cull.forgetPeople()                                                     # what is remembered can be deleted while it is off
    until(qapp, lambda: not store.path.exists())
    until(qapp, lambda: cull.view["center"]["peopleMemory"]["count"] == 0)
    assert cull._toasts.items[-1]["title"] == "Remembered people forgotten" and not cull.view["center"]["peopleMemory"]["canForget"]
    cull.setPeopleMemory(True)
    assert identities.active() is not None and "Nobody is remembered yet" in cull.view["center"]["peopleMemory"]["desc"]
    sample = CullModel(PhotoModel(parent=qapp), memory=True, parent=qapp)
    assert not sample.view["center"].get("peopleMemory", {}).get("show")    # the sample page shows no such row


def test_deleting_a_folders_ai_data_takes_its_people_out_of_the_store(qapp, tmp_path, store):
    from image_triage.qml_shell.cull_model import CullModel
    from image_triage.qml_shell.menu_model import ToastModel
    from image_triage.qml_shell.photos import PhotoModel

    folder, db = named_folder(tmp_path, store)
    assert store.names() == ["Anna"]
    photos = PhotoModel(parent=qapp)
    photos.setRows([{"path": str(folder / "a_0.jpg"), "name": "a_0", "ext": "JPG", "raw": False, "decision": "", "rating": 0, "ai": -1}], str(folder))
    cull = CullModel(photos, memory=False, toasts=ToastModel(qapp), parent=qapp)
    cull._ai = None
    cull.deleteFolderData()
    end = time.monotonic() + 6
    while store.names() and time.monotonic() < end:
        pump(qapp, 0.05)
    assert store.names() == [] and not db.exists()


def test_the_ai_center_row_renders(qapp, tmp_path, store, monkeypatch):
    from image_triage.qml_shell.ai_center import AiCenterBackend
    from image_triage.qml_shell.cull_model import CullModel
    from test_qml_shell_ai_center import FakeOps, centre, find, until

    monkeypatch.setattr(identities, "default_path", lambda: store.path)
    store.sync_source("s1", {("Anna", MODEL): person(0), ("Mark", MODEL): person(1), ("Lena", MODEL): person(2)})
    engine, window, warnings = _load_main(qapp, tmp_path / "ui")
    window.setWidth(1440)
    window.setHeight(1000)
    real = CullModel(engine.rootContext().contextProperty("photos"), memory=False, ai=AiCenterBackend(FakeOps(), parent=qapp, auto_test=False), parent=qapp)
    real._settings = QSettings(str(tmp_path / "prefs.ini"), QSettings.Format.IniFormat)
    engine.rootContext().setContextProperty("cull", real)
    until(qapp, lambda: real.view["center"]["rtState"] != "Checking…")
    real.openCenter()
    until(qapp, lambda: real.view["center"]["peopleMemory"]["count"] == 3)
    pump(qapp, 0.8)
    row = find(window, "aiPeopleMemory")
    assert row is not None and row.property("visible") and find(window, "aiForgetPeopleButton").property("visible")
    body = None
    stack = [window.contentItem()]
    while stack:                                                            # scroll the AI Center to the row
        item = stack.pop()
        if item.metaObject().className().startswith("QQuickFlickable") and item.property("contentHeight") and row.mapToItem(item, 0, 0).y() >= 0 and item.isAncestorOf(row):
            body = item
        stack.extend(item.childItems())
    if body is not None:
        body.setProperty("contentY", max(0, min(row.mapToItem(body.property("contentItem"), 0, 0).y() - 60, body.property("contentHeight") - body.height())))
        pump(qapp, 0.3)
    shot = os.environ.get("IT_SHOT_DIR")
    if shot:
        window.grabWindow().save(os.path.join(shot, "ai_center_people.png"))
    assert not [w for w in warnings if "ReferenceError" in w or "TypeError" in w or "Required property" in w or "Unable to assign" in w], warnings
    window.close()
    engine.deleteLater()
    pump(qapp, 0.2)
