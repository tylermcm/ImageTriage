"""The decision store under more than one user: write-ahead logging, waiting for a writer, no write just for opening it, a
guard against a newer database, pruning, and a queue that really has written everything when it says so."""
from __future__ import annotations

import sqlite3
import time

import pytest
from PySide6.QtCore import QCoreApplication, QStandardPaths
from PySide6.QtGui import QGuiApplication

from image_triage import annotation_queue
from image_triage.annotation_queue import AnnotationPersistenceQueue, AnnotationQueueEntry, _AnnotationPersistTask
from image_triage.decision_store import DecisionStore, DecisionStoreTooNewError
from image_triage.models import ImageRecord, SessionAnnotation


@pytest.fixture(scope="module")
def qapp():
    return QCoreApplication.instance() or QGuiApplication([])


def _store(path) -> DecisionStore:
    store = DecisionStore.__new__(DecisionStore)        # the real store opens the user's app data; point this one at the test's file
    store._db_path = path
    store._initialize()
    return store


def _record(path) -> ImageRecord:
    return ImageRecord(path=str(path), name=str(path).replace("\\", "/").rsplit("/", 1)[-1], size=10, modified_ns=4)


def test_the_database_uses_write_ahead_logging_including_one_made_by_an_earlier_build(tmp_path):
    fresh = tmp_path / "fresh.sqlite3"
    _store(fresh)
    with sqlite3.connect(fresh) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"

    earlier = tmp_path / "earlier.sqlite3"
    _store(earlier)
    with sqlite3.connect(earlier) as connection:
        connection.execute("PRAGMA journal_mode = DELETE")
    DecisionStore._wal_tried.discard(str(earlier))        # as a new run of the app would start
    _store(earlier)
    with sqlite3.connect(earlier) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_opening_an_up_to_date_store_writes_nothing(tmp_path):
    path = tmp_path / "decisions.sqlite3"
    _store(path)
    with sqlite3.connect(path) as connection:
        before = connection.execute("SELECT last_used_at FROM sessions").fetchall()
        connection.execute("UPDATE sessions SET last_used_at = 'marker'")
        connection.commit()
        changes_before = connection.execute("PRAGMA data_version").fetchone()[0]
        _store(path)
        _store(path)
        assert connection.execute("PRAGMA data_version").fetchone()[0] == changes_before      # no other connection committed
        assert connection.execute("SELECT last_used_at FROM sessions").fetchall() == [("marker",)] and before


def test_a_database_from_a_newer_version_is_refused_and_left_alone(tmp_path):
    path = tmp_path / "decisions.sqlite3"
    _store(path)
    with sqlite3.connect(path) as connection:
        connection.execute(f"PRAGMA user_version = {DecisionStore.SCHEMA_VERSION + 1}")

    with pytest.raises(DecisionStoreTooNewError, match="newer version"):
        _store(path)

    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == DecisionStore.SCHEMA_VERSION + 1


def test_a_write_waits_for_another_writer_instead_of_failing(tmp_path):
    path = tmp_path / "decisions.sqlite3"
    store = _store(path)
    assert store.BUSY_TIMEOUT_MS >= 5_000
    with store._connect() as connection:
        assert connection.execute("PRAGMA busy_timeout").fetchone()[0] == store.BUSY_TIMEOUT_MS


def test_connections_are_closed_after_each_piece_of_work(tmp_path):
    store = _store(tmp_path / "decisions.sqlite3")
    with store._connect() as connection:
        pass
    with pytest.raises(sqlite3.ProgrammingError):
        connection.execute("SELECT 1")


def test_delete_annotations_drops_many_in_one_go(tmp_path):
    store = _store(tmp_path / "decisions.sqlite3")
    records = [_record(tmp_path / f"{name}.jpg") for name in "abc"]
    store.save_annotations("s", [(record, SessionAnnotation(winner=True)) for record in records])

    store.delete_annotations("s", [records[0].path, records[2].path, ""])

    assert store.winner_paths("s") == [records[1].path]


def test_prune_drops_only_rows_whose_file_is_gone_from_that_folder(tmp_path):
    shoot, other = tmp_path / "shoot", tmp_path / "shoot-2"
    (shoot / "sub").mkdir(parents=True)
    other.mkdir()
    store = _store(tmp_path / "decisions.sqlite3")
    here, gone, grouped, deeper, elsewhere = (
        _record(shoot / "here.jpg"), _record(shoot / "gone.jpg"), _record(shoot / "grouped.jpg"),
        _record(shoot / "sub" / "deep.jpg"), _record(other / "x.jpg"),
    )
    (shoot / "here.jpg").write_bytes(b"1")
    (shoot / "grouped.jpg").write_bytes(b"1")              # on disk, but the listing shows it under another photo
    for record in (here, gone, grouped, deeper, elsewhere):
        store.save_annotation("s", record, SessionAnnotation(winner=True))
    store.save_annotation("another session", gone, SessionAnnotation(winner=True))

    assert store.prune_missing("s", str(shoot), [here.path]) == 1

    assert sorted(store.winner_paths("s")) == sorted([here.path, grouped.path, deeper.path, elsewhere.path])
    assert store.winner_paths("another session") == [gone.path]
    assert store.prune_missing("s", str(shoot), [here.path]) == 0


# ---- the queue ------------------------------------------------------------------------------------------------------------------

def test_flush_blocking_waits_for_the_batch_that_was_still_waiting(qapp, tmp_path, monkeypatch):
    """A change made while an earlier batch is being written is a second batch; closing the app must wait for that one too."""
    written: list[str] = []

    class SlowStore:
        def save_annotations(self, session_id, pairs):
            time.sleep(0.15)
            written.extend(record.path for record, _annotation in pairs)

    monkeypatch.setattr(annotation_queue, "DecisionStore", SlowStore)
    monkeypatch.setattr(annotation_queue, "sync_sidecar_annotation", lambda *a, **k: None)
    queue = AnnotationPersistenceQueue(parent=None)
    first, second = _record(tmp_path / "a.jpg"), _record(tmp_path / "b.jpg")
    queue.enqueue(first.path, SessionAnnotation(winner=True), record=first, session_id="s")
    queue.flush()                                           # the first batch is now being written
    queue.enqueue(second.path, SessionAnnotation(reject=True), record=second, session_id="s")

    queue.flush_blocking()

    assert written == [first.path, second.path]
    assert not queue._active and not queue._pending


def test_a_store_that_cannot_be_opened_fails_the_entries_and_still_ends_the_batch(monkeypatch, tmp_path):
    from queue import SimpleQueue

    def broken():
        raise RuntimeError("the database is from a newer version")

    monkeypatch.setattr(annotation_queue, "DecisionStore", broken)
    record = _record(tmp_path / "a.jpg")
    results = SimpleQueue()

    _AnnotationPersistTask((AnnotationQueueEntry(record.path, record, "s", SessionAnnotation(winner=True)),), results).run()

    events = []
    while not results.empty():
        events.append(results.get_nowait())
    assert events == [("failed", record.path, "the database is from a newer version"), ("done", 1)]


# ---- the shell --------------------------------------------------------------------------------------------------------------------

def test_a_sidecar_that_cannot_be_written_is_said_once_for_the_folder(qapp, tmp_path):
    from image_triage.qml_shell.decision_bridge import DecisionBridge
    from image_triage.qml_shell.menu_model import ToastModel
    from image_triage.qml_shell.photos import PhotoModel

    QStandardPaths.setTestModeEnabled(True)
    try:
        toasts = ToastModel(parent=qapp)
        bridge = DecisionBridge(PhotoModel(parent=qapp), toasts=toasts, parent=qapp)
        for name in ("a.jpg", "b.jpg", "c.jpg"):
            bridge._queue.warning.emit(str(tmp_path / "card" / name), "Permission denied")
        bridge._queue.warning.emit(str(tmp_path / "other" / "z.jpg"), "Permission denied")

        warnings = [t for t in toasts.items if t["title"] == "Could not write the XMP sidecar"]
        assert len(warnings) == 2 and "a.jpg: Permission denied" in warnings[0]["body"] and "z.jpg" in warnings[1]["body"]
    finally:
        QStandardPaths.setTestModeEnabled(False)


def test_opening_a_folder_prunes_the_decisions_of_photos_no_longer_in_it(qapp, tmp_path, monkeypatch):
    from image_triage.qml_shell import decision_bridge as bridge_module
    from image_triage.qml_shell.decision_bridge import DecisionBridge
    from image_triage.scanner import scan_folder

    QStandardPaths.setTestModeEnabled(True)
    try:
        session = "prune-" + tmp_path.name
        monkeypatch.setattr(bridge_module, "session_id", lambda: session)
        shoot = tmp_path / "shoot"
        shoot.mkdir()
        for name in ("a.jpg", "b.jpg"):
            (shoot / name).write_bytes(b"\xff\xd8\xff\xd9" + name.encode())
        records = [r for r in scan_folder(str(shoot)) if not r.is_folder]
        store = DecisionStore()
        store.save_annotations(session, [(record, SessionAnnotation(winner=True)) for record in records])
        (shoot / "b.jpg").unlink()
        left = [r for r in scan_folder(str(shoot)) if not r.is_folder]

        bridge = DecisionBridge.__new__(DecisionBridge)
        loaded = DecisionBridge.load(bridge, left)

        assert list(loaded) == [left[0].path]
        assert store.winner_paths(session) == [left[0].path]
    finally:
        QStandardPaths.setTestModeEnabled(False)
