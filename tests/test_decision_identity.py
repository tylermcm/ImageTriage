"""A saved decision belongs to the photo, not to the files that happen to sit beside it."""
from __future__ import annotations

import sqlite3

from image_triage.decision_store import DecisionStore
from image_triage.models import ImageRecord, SessionAnnotation
from image_triage.scanner import scan_folder


def _store(path) -> DecisionStore:
    store = DecisionStore.__new__(DecisionStore)        # the real store opens the user's app data; point this one at the test's file
    store._db_path = path
    store._initialize()
    return store


def _only(folder):
    records = [r for r in scan_folder(str(folder)) if not r.is_folder]
    assert len(records) == 1
    return records[0]


def test_a_new_companion_or_edit_does_not_drop_the_decision(tmp_path):
    shoot = tmp_path / "shoot"
    shoot.mkdir()
    (shoot / "IMG_5.CR2").write_bytes(b"raw-bytes!")
    store = _store(tmp_path / "decisions.sqlite3")
    before = _only(shoot)
    store.save_annotation("s", before, SessionAnnotation(winner=True, rating=5))

    (shoot / "IMG_5.JPG").write_bytes(b"\xff\xd8\xff\xd9 a jpeg written later")
    after = _only(shoot)
    assert after.size != before.size                                   # the bundle total moved ...
    assert (after.identity_size, after.identity_modified_ns) == (before.identity_size, before.identity_modified_ns)
    loaded = store.load_annotations("s", [after])                      # ... and the decision is still there
    assert loaded[after.path].winner and loaded[after.path].rating == 5


def test_a_different_file_at_the_same_path_does_not_inherit_the_decision(tmp_path):
    shoot = tmp_path / "shoot"
    shoot.mkdir()
    photo = shoot / "a.jpg"
    photo.write_bytes(b"\xff\xd8\xff\xd9 one")
    store = _store(tmp_path / "decisions.sqlite3")
    store.save_annotation("s", _only(shoot), SessionAnnotation(reject=True))
    photo.write_bytes(b"\xff\xd8\xff\xd9 a different, longer photo")
    assert store.load_annotations("s", [_only(shoot)]) == {}


def test_rows_saved_with_bundle_totals_by_an_earlier_build_still_load(tmp_path):
    record = ImageRecord(path="/p/a.cr2", name="a.cr2", size=30, modified_ns=9, primary_size=10, primary_modified_ns=4)
    store = _store(tmp_path / "decisions.sqlite3")
    store.save_annotation("s", record, SessionAnnotation(winner=True))
    with sqlite3.connect(tmp_path / "decisions.sqlite3") as connection:  # what the earlier build wrote
        connection.execute("UPDATE decisions SET modified_ns = 9, file_size = 30")
    assert store.load_annotations("s", [record])[record.path].winner


def test_a_database_from_before_sessions_is_upgraded_with_its_decisions(tmp_path):
    path = tmp_path / "decisions.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE decisions (path TEXT PRIMARY KEY, modified_ns INTEGER NOT NULL, file_size INTEGER NOT NULL, "
            "winner INTEGER NOT NULL DEFAULT 0, reject INTEGER NOT NULL DEFAULT 0, rating INTEGER NOT NULL DEFAULT 0, tags_json TEXT NOT NULL DEFAULT '[]')"
        )
        connection.execute("INSERT INTO decisions VALUES ('/p/a.jpg', 4, 10, 1, 0, 3, '[]')")
        connection.execute("PRAGMA user_version = 2")
    store = _store(path)
    record = ImageRecord(path="/p/a.jpg", name="a.jpg", size=10, modified_ns=4)
    loaded = store.load_annotations(DecisionStore.DEFAULT_SESSION, [record])
    assert loaded[record.path].winner and loaded[record.path].rating == 3
