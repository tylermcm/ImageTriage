"""Decisions and collection entries follow photos that were moved or renamed outside the app (audit D16)."""
from __future__ import annotations

import os
import shutil
import sqlite3

import pytest

from image_triage import decision_relink, path_policy
from image_triage.decision_relink import MISSING, PRESENT, UNAVAILABLE, PathProbe, relink_folder
from image_triage.decision_store import DecisionStore
from image_triage.library_store import LibraryStore
from image_triage.models import SessionAnnotation
from image_triage.scanner import scan_folder


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtCore import QCoreApplication
    from PySide6.QtGui import QGuiApplication

    return QCoreApplication.instance() or QGuiApplication([])


def _store(path) -> DecisionStore:
    store = DecisionStore.__new__(DecisionStore)        # the real store opens the user's app data; point this one at the test's file
    store._db_path = path
    store._initialize()
    return store


def _library(path) -> LibraryStore:
    library = LibraryStore.__new__(LibraryStore)
    library._db_path = path
    library._initialize()
    return library


def _photos(folder, count=3, prefix="IMG_"):
    folder.mkdir(parents=True, exist_ok=True)
    for index in range(count):
        photo = folder / f"{prefix}{index:04d}.jpg"
        photo.write_bytes(b"\xff\xd8\xff\xd9" + bytes([index]) * (10 + index))
        os.utime(photo, ns=(1_700_000_000_000_000_000 + index, 1_700_000_000_000_000_000 + index * 1_000))
    return _scan(folder)


def _scan(folder):
    return sorted((r for r in scan_folder(str(folder)) if not r.is_folder), key=lambda record: record.name)


def _rows(db_path):
    with sqlite3.connect(db_path) as connection:
        return sorted(row[0] for row in connection.execute("SELECT path FROM decisions"))


def test_a_folder_renamed_outside_the_app_keeps_its_decisions(tmp_path):
    store = _store(tmp_path / "decisions.sqlite3")
    before = _photos(tmp_path / "Trip", count=40)
    store.save_annotations("s", [(record, SessionAnnotation(winner=index % 2 == 0, reject=index % 2 == 1, rating=index % 6)) for index, record in enumerate(before)])
    store.record_correction_event("s", folder_path=str(tmp_path / "Trip"), record_path=before[0].path, other_path=before[1].path, event_type="pair")
    os.rename(tmp_path / "Trip", tmp_path / "Trip 2026")

    after = _scan(tmp_path / "Trip 2026")
    assert store.load_annotations("s", after) == {}                          # the failure: every decision is orphaned
    report = relink_folder(store, "s", after)

    assert len(report.relinked) == 40 and report.ambiguous == 0 and report.unavailable == 0
    loaded = store.load_annotations("s", after)
    assert len(loaded) == 40
    assert _rows(tmp_path / "decisions.sqlite3") == sorted(record.path for record in after)
    assert loaded[after[0].path].winner and loaded[after[1].path].reject and loaded[after[5].path].rating == 5
    event = store.load_correction_events("s")[0]
    assert (event["folder_path"], event["record_path"], event["other_path"]) == (str(tmp_path / "Trip 2026"), after[0].path, after[1].path)
    log = store.relink_log("s")
    assert len(log) == 40 and {entry["new_path"] for entry in log} == {record.path for record in after}
    assert relink_folder(store, "s", after).relinked == ()                    # nothing left to do the second time


def test_a_single_file_renamed_outside_the_app_keeps_its_decision(tmp_path):
    store = _store(tmp_path / "decisions.sqlite3")
    before = _photos(tmp_path / "shoot")
    store.save_annotation("s", before[1], SessionAnnotation(winner=True, rating=4))
    store.save_annotation("s", before[2], SessionAnnotation(reject=True))
    os.rename(before[1].path, tmp_path / "shoot" / "the good one.jpg")       # a rename keeps the size and the modified time

    after = _scan(tmp_path / "shoot")
    report = relink_folder(store, "s", after)
    renamed = str(tmp_path / "shoot" / "the good one.jpg")
    assert report.relinked == ((before[1].path, renamed),)
    loaded = store.load_annotations("s", after)
    assert loaded[renamed].winner and loaded[renamed].rating == 4 and loaded[before[2].path].reject
    assert len(_rows(tmp_path / "decisions.sqlite3")) == 2


def test_a_file_moved_to_another_folder_keeps_its_decision(tmp_path):
    store = _store(tmp_path / "decisions.sqlite3")
    before = _photos(tmp_path / "shoot")
    store.save_annotation("s", before[0], SessionAnnotation(rating=3))
    (tmp_path / "picks").mkdir()
    shutil.move(before[0].path, tmp_path / "picks" / before[0].name)
    after = _scan(tmp_path / "picks")
    assert relink_folder(store, "s", after).relinked == ((before[0].path, after[0].path),)
    assert store.load_annotations("s", after)[after[0].path].rating == 3


def test_ambiguous_duplicates_are_left_alone(tmp_path):
    store = _store(tmp_path / "decisions.sqlite3")
    one = _photos(tmp_path / "card copy 1", count=1)[0]
    two = _photos(tmp_path / "card copy 2", count=1)[0]                       # the same file name, size and time
    store.save_annotation("s", one, SessionAnnotation(winner=True))
    store.save_annotation("s", two, SessionAnnotation(reject=True))
    shutil.rmtree(tmp_path / "card copy 1")
    shutil.rmtree(tmp_path / "card copy 2")
    here = _photos(tmp_path / "import", count=1)

    report = relink_folder(store, "s", here)
    assert report.relinked == () and report.ambiguous == 1
    assert _rows(tmp_path / "decisions.sqlite3") == sorted([one.path, two.path])   # both rows are exactly where they were
    assert store.load_annotations("s", here) == {}

    # and one orphaned row that two photos here could claim is not handed to either
    store.delete_annotation("s", two.path)
    twins = _photos(tmp_path / "import" / "again", count=1)
    report = relink_folder(store, "s", [*here, *twins])
    assert report.relinked == () and report.ambiguous == 2
    assert _rows(tmp_path / "decisions.sqlite3") == [one.path]


def test_a_copy_does_not_take_the_decision_of_a_photo_that_is_still_there(tmp_path):
    store = _store(tmp_path / "decisions.sqlite3")
    original = _photos(tmp_path / "shoot", count=1)[0]
    store.save_annotation("s", original, SessionAnnotation(winner=True))
    (tmp_path / "backup").mkdir()
    shutil.copy2(original.path, tmp_path / "backup" / original.name)
    assert relink_folder(store, "s", _scan(tmp_path / "backup")).relinked == ()
    assert _rows(tmp_path / "decisions.sqlite3") == [original.path]


def test_a_file_that_is_merely_unavailable_keeps_its_row(tmp_path):
    store = _store(tmp_path / "decisions.sqlite3")
    on_the_nas = _photos(tmp_path / "nas" / "2026", count=2)
    store.save_annotations("s", [(record, SessionAnnotation(winner=True)) for record in on_the_nas])
    shutil.move(str(tmp_path / "nas"), str(tmp_path / "unplugged"))           # the share is gone from where it was ...
    local = _photos(tmp_path / "local copy", count=2)                         # ... and the same photos are opened elsewhere

    class Asleep(PathProbe):
        def _volume_answers(self, folder: str) -> bool:
            return not folder.startswith(str(tmp_path / "nas"))

    report = relink_folder(store, "s", local, probe=Asleep())
    assert report.relinked == () and report.unavailable == 2
    assert _rows(tmp_path / "decisions.sqlite3") == sorted(record.path for record in on_the_nas)
    assert store.relink_log("s") == []


@pytest.mark.skipif(os.name == "nt", reason="mount points are the POSIX case")
def test_the_probe_tells_missing_from_unavailable(tmp_path, monkeypatch):
    table = (
        "22 1 8:1 / / rw - ext4 /dev/sda1 rw\n"
        "30 22 0:25 / /home/sam/nas rw - nfs4 nas.example:/photos rw\n"
        "35 22 0:41 / /net rw - autofs /etc/auto.net rw\n"
    )
    monkeypatch.setattr(path_policy, "_read_mounts", lambda: path_policy.parse_mountinfo(table))
    path_policy.forget_mounts()
    try:
        probe = PathProbe()
        probe._fstab = ("/srv/archive",)
        (tmp_path / "there").mkdir()
        (tmp_path / "there" / "a.jpg").write_bytes(b"x")
        assert probe.state(str(tmp_path / "there" / "a.jpg")) == PRESENT
        assert probe.state(str(tmp_path / "there" / "b.jpg")) == MISSING       # the folder answers and the file is not in it
        assert probe.state(str(tmp_path / "gone" / "a.jpg")) == MISSING        # the whole folder is gone from a volume that answers
        assert probe.state("/media/sam/CARD/DCIM/a.jpg") == UNAVAILABLE        # a card that is not in the reader
        assert probe.state("/mnt/backup/2026/a.jpg") == UNAVAILABLE            # an unmounted volume
        assert probe.state("/net/server/photos/a.jpg") == UNAVAILABLE          # an automount that did not come up
        assert probe.state("/srv/archive/2026/a.jpg") == UNAVAILABLE           # a configured mount point with nothing mounted
        assert probe.state("a.jpg") == UNAVAILABLE
    finally:
        path_policy.forget_mounts()


def test_one_folder_is_listed_once_however_many_rows_point_into_it(tmp_path, monkeypatch):
    listed: list[str] = []
    real = os.scandir
    monkeypatch.setattr(decision_relink.os, "scandir", lambda path: (listed.append(str(path)), real(path))[1])
    probe = PathProbe()
    for index in range(500):
        assert probe.state(str(tmp_path / "old" / f"IMG_{index}.jpg")) == MISSING
    assert listed == [str(tmp_path / "old")]


def test_collections_follow_the_same_moves(tmp_path):
    store = _store(tmp_path / "decisions.sqlite3")
    library = _library(tmp_path / "library.sqlite3")
    before = _photos(tmp_path / "Trip")
    store.save_annotation("s", before[0], SessionAnnotation(winner=True))
    collection = library.create_collection(name="Picks", item_paths=(before[0].path, before[1].path))
    # the library index knows the second photo's file identity; the first one is only known to the decision store
    with sqlite3.connect(tmp_path / "library.sqlite3") as connection:
        connection.execute("INSERT INTO catalog_roots(root_path) VALUES (?)", (str(tmp_path),))
        connection.execute(
            "INSERT INTO catalog_records(path, root_path, folder_path, name, modified_ns, file_size, record_json) VALUES (?, ?, ?, ?, ?, ?, '{}')",
            (before[1].path, str(tmp_path), str(tmp_path / "Trip"), before[1].name, before[1].identity_modified_ns, before[1].identity_size),
        )
    os.rename(tmp_path / "Trip", tmp_path / "Trip 2026")
    after = _scan(tmp_path / "Trip 2026")

    report = relink_folder(store, "s", after, library=library)
    assert report.relinked == ((before[0].path, after[0].path),) and report.collections_changed == 2
    assert list(library.load_collection(collection.id).item_paths) == [after[0].path, after[1].path]


def test_opening_the_folder_in_the_shell_relinks_and_says_so(tmp_path, qapp, monkeypatch):
    from image_triage.qml_shell import decision_bridge
    from image_triage.qml_shell.photos import PhotoModel

    # Modules that bind DecisionStore by name when first imported must be imported before it is replaced, or they keep
    # this test's store for the rest of the run and later tests' decisions are written to it.
    import image_triage.annotation_queue  # noqa: F401

    store = _store(tmp_path / "decisions.sqlite3")
    monkeypatch.setattr("image_triage.decision_store.DecisionStore", lambda: store)
    monkeypatch.setattr("image_triage.library_store.LibraryStore", lambda: _library(tmp_path / "library.sqlite3"))
    monkeypatch.setattr(decision_bridge, "session_id", lambda: "s")
    before = _photos(tmp_path / "Trip")
    store.save_annotation("s", before[2], SessionAnnotation(reject=True, rating=2))
    os.rename(tmp_path / "Trip", tmp_path / "Renamed")

    class Toasts:
        said: list = []

        def push(self, *args, **kwargs):
            self.said.append(args)

    bridge = decision_bridge.DecisionBridge(PhotoModel(parent=qapp), toasts=Toasts(), parent=qapp)
    loaded = bridge.load(_scan(tmp_path / "Renamed"))                        # what the scan thread calls
    moved = str(tmp_path / "Renamed" / before[2].name)
    assert loaded[moved]["decision"] == "rej" and loaded[moved]["rating"] == 2
    assert len(bridge.relinks) == 1 and bridge.relinks[0].relinked == ((before[2].path, moved),)
    qapp.processEvents()
    assert Toasts.said and Toasts.said[0][0] == "info" and "1 saved decision" in Toasts.said[0][2]
