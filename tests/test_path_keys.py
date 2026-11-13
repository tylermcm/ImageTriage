"""One platform-aware identity key for a path (audit D21).

The Windows and macOS rules are exercised through the helper's ``platform`` parameter: nothing here runs on those
systems. The scan, decision, catalog and collection tests need a case-sensitive file system and are skipped elsewhere.
"""
from __future__ import annotations

import ntpath
import sqlite3
from pathlib import Path

import pytest

from image_triage.aiculler_global_store import GlobalAdapterLabelStore
from image_triage.catalog import CatalogRepository
from image_triage.decision_store import DecisionStore
from image_triage.models import ImageRecord, SessionAnnotation
from image_triage.path_keys import folds_case, legacy_path_key, name_key, path_key, pick_stored, same_path
from image_triage.scanner import normalized_path_key, scan_folder


@pytest.mark.parametrize("platform", ["win32", "cygwin", "darwin"])
def test_case_is_folded_where_the_file_system_folds_it(platform):
    assert folds_case(platform)
    assert path_key("/Shoot/IMG_1.jpg", platform=platform) == path_key("/shoot/img_1.JPG", platform=platform)
    assert name_key("IMG_1", platform=platform) == name_key("img_1", platform=platform)
    assert same_path("/Shoot/A.JPG", "/shoot/a.jpg", platform=platform)


@pytest.mark.parametrize("platform", ["linux", "freebsd14"])
def test_case_is_kept_on_a_case_sensitive_platform(platform):
    assert not folds_case(platform)
    assert path_key("/shoot/IMG_1.jpg", platform=platform) == "/shoot/IMG_1.jpg"
    assert path_key("/shoot/IMG_1.jpg", platform=platform) != path_key("/shoot/img_1.JPG", platform=platform)
    assert name_key("IMG_1", platform=platform) != name_key("img_1", platform=platform)
    assert not same_path("/shoot/A.JPG", "/shoot/a.jpg", platform=platform)


def test_separators_and_dot_segments_are_normalised():
    assert path_key("C:/Photos//Shoot/./x/../IMG.JPG", platform="win32") == "c:\\photos\\shoot\\img.jpg"
    assert path_key("C:\\Photos\\Shoot\\IMG.JPG", platform="win32") == path_key("c:/photos/shoot/img.jpg", platform="win32")
    assert path_key("/photos//shoot/./x/../IMG.JPG", platform="linux") == "/photos/shoot/IMG.JPG"
    assert path_key("/photos/odd\\name.jpg", platform="linux") == "/photos/odd\\name.jpg"   # a backslash is a name character
    assert path_key("", platform="linux") == "" and path_key("", platform="win32") == ""


@pytest.mark.parametrize("path", [
    "C:\\Photos\\2026\\IMG_0001.CR3", "c:/photos/2026/../2025/Straße/ÉTÉ.JPG", "\\\\NAS\\Share\\Shoot\\IMG.jpg", "D:\\İstanbul\\A.NEF",
])
def test_a_windows_key_is_the_key_earlier_builds_stored(path):
    # What the catalog, the label store and every other store wrote on Windows: normpath, then casefold. Equal keys mean
    # an existing Windows database needs no migration.
    stored_by_earlier_builds = ntpath.normpath(path).casefold()
    assert path_key(path, platform="win32") == stored_by_earlier_builds == legacy_path_key(path, platform="win32")


def test_an_old_key_is_only_used_when_it_cannot_mean_another_file():
    stored = {"/shoot/img_1.jpg": {"path": "/shoot/IMG_1.jpg", "rating": 5}}
    # the row remembers the path it was written for: only that path gets it
    assert pick_stored("/shoot/IMG_1.jpg", stored, stored_path=lambda row: row["path"], platform="linux") == ("/shoot/img_1.jpg", stored["/shoot/img_1.jpg"])
    assert pick_stored("/shoot/img_1.JPG", stored, stored_path=lambda row: row["path"], platform="linux") is None
    # the row does not say: it is used unless another path being looked up shares the old key
    bare = {"/shoot/img_1.jpg": "row"}
    assert pick_stored("/shoot/IMG_1.jpg", bare, live_paths=["/shoot/IMG_1.jpg", "/shoot/IMG_2.jpg"], platform="linux") == ("/shoot/img_1.jpg", "row")
    assert pick_stored("/shoot/IMG_1.jpg", bare, live_paths=["/shoot/IMG_1.jpg", "/shoot/img_1.JPG"], platform="linux") is None
    # a row under the current key always wins, and Windows never needs the fallback
    both = {"/shoot/IMG_1.jpg": "new", "/shoot/img_1.jpg": "old"}
    assert pick_stored("/shoot/IMG_1.jpg", both, platform="linux") == ("/shoot/IMG_1.jpg", "new")
    assert pick_stored("C:\\Shoot\\IMG_1.jpg", {"c:\\shoot\\img_1.jpg": "row"}, platform="win32") == ("c:\\shoot\\img_1.jpg", "row")


@pytest.fixture
def case_sensitive_folder(tmp_path):
    folder = tmp_path / "shoot"
    folder.mkdir()
    (folder / "probe").write_bytes(b"")
    if (folder / "PROBE").exists() or folds_case():
        pytest.skip("needs a case-sensitive file system")
    (folder / "probe").unlink()
    return folder


def _store(path) -> DecisionStore:
    store = DecisionStore.__new__(DecisionStore)        # the real store opens the user's app data; point this one at the test's file
    store._db_path = path
    store._initialize()
    return store


def test_two_files_differing_only_by_case_are_two_photos(case_sensitive_folder):
    (case_sensitive_folder / "IMG_1.jpg").write_bytes(b"\xff\xd8\xff\xd9 upper")
    (case_sensitive_folder / "img_1.JPG").write_bytes(b"\xff\xd8\xff\xd9 the lower one")
    records = scan_folder(str(case_sensitive_folder))
    assert sorted(record.name for record in records) == ["IMG_1.jpg", "img_1.JPG"]
    assert all(not record.companion_paths and not record.edited_paths and not record.variants for record in records)
    assert len({normalized_path_key(record.path) for record in records}) == 2


def test_their_decisions_are_independent(case_sensitive_folder, tmp_path):
    (case_sensitive_folder / "IMG_1.jpg").write_bytes(b"\xff\xd8\xff\xd9 upper")
    (case_sensitive_folder / "img_1.JPG").write_bytes(b"\xff\xd8\xff\xd9 the lower one")
    upper, lower = sorted(scan_folder(str(case_sensitive_folder)), key=lambda record: record.name)
    store = _store(tmp_path / "decisions.sqlite3")
    store.save_annotation("s", upper, SessionAnnotation(winner=True, rating=5))
    store.save_annotation("s", lower, SessionAnnotation(reject=True))
    loaded = store.load_annotations("s", [upper, lower])
    assert loaded[upper.path].winner and loaded[upper.path].rating == 5 and not loaded[upper.path].reject
    assert loaded[lower.path].reject and not loaded[lower.path].winner
    store.delete_annotation("s", lower.path)
    assert set(store.load_annotations("s", [upper, lower])) == {upper.path}
    # pruning one photo's row does not take the other's, and a photo that is on disk keeps its row
    (case_sensitive_folder / "img_1.JPG").unlink()
    store.save_annotation("s", lower, SessionAnnotation(reject=True))
    assert store.prune_missing("s", str(case_sensitive_folder), [upper.path]) == 1
    assert set(store.load_annotations("s", [upper, lower])) == {upper.path}


def test_a_raw_and_a_jpeg_whose_names_differ_by_case_are_not_bundled(case_sensitive_folder):
    (case_sensitive_folder / "IMG_2.CR2").write_bytes(b"raw")
    (case_sensitive_folder / "img_2.jpg").write_bytes(b"\xff\xd8\xff\xd9")
    (case_sensitive_folder / "IMG_3.CR2").write_bytes(b"raw")
    (case_sensitive_folder / "IMG_3.JPG").write_bytes(b"\xff\xd8\xff\xd9")       # the same name: still one photo
    records = {record.name: record for record in scan_folder(str(case_sensitive_folder))}
    assert set(records) == {"IMG_2.CR2", "img_2.jpg", "IMG_3.CR2"}
    assert [Path(path).name for path in records["IMG_3.CR2"].companion_paths] == ["IMG_3.JPG"]


def test_the_catalog_keeps_two_folders_differing_by_case_apart(case_sensitive_folder, tmp_path):
    other = case_sensitive_folder.parent / "SHOOT"
    other.mkdir()
    repository = CatalogRepository(tmp_path / "catalog.sqlite3")
    one = ImageRecord(path=str(case_sensitive_folder / "a.jpg"), name="a.jpg", size=1, modified_ns=1)
    two = ImageRecord(path=str(other / "b.jpg"), name="b.jpg", size=2, modified_ns=2)
    assert repository.save_folder_records(str(case_sensitive_folder), [one])
    assert repository.save_folder_records(str(other), [two])
    assert [record.name for record in repository.load_folder_records(str(case_sensitive_folder))] == ["a.jpg"]
    assert [record.name for record in repository.load_folder_records(str(other))] == ["b.jpg"]


def test_a_label_saved_under_the_old_key_is_still_found_and_rewritten(case_sensitive_folder, tmp_path):
    photo = str(case_sensitive_folder / "IMG_9.NEF")
    twin = str(case_sensitive_folder / "img_9.nef")
    store = GlobalAdapterLabelStore(tmp_path / "labels.sqlite")
    try:
        store.upsert_label(photo, "hero")
        store.connection.execute("UPDATE adapter_labels SET path_key = ?", (legacy_path_key(photo),))   # what an earlier build wrote
        store.connection.commit()
        found = store.labels_for_paths((photo, twin))
        assert set(found) == {photo} and found[photo].label == "hero"       # the twin does not inherit it
        store.delete_label(twin)                                           # nor can it delete or retag it
        store.update_reason_tags(twin, ("soft",))
        assert store.labels_for_paths((photo,))[photo].reason_tags == ()
        store.upsert_label(photo, "hero")
        keys = [row[0] for row in store.connection.execute("SELECT path_key FROM adapter_labels")]
        assert keys == [path_key(photo)]
    finally:
        store.close()


def test_collections_tell_the_two_photos_apart(case_sensitive_folder, tmp_path, monkeypatch):
    from image_triage import library_store

    upper, lower = str(case_sensitive_folder / "IMG_1.jpg"), str(case_sensitive_folder / "img_1.JPG")
    store = library_store.LibraryStore.__new__(library_store.LibraryStore)
    store._db_path = tmp_path / "library.sqlite3"
    store._initialize()
    collection = store.create_collection(name="Picks", item_paths=(upper,))
    assert store.paths_missing_from_collection(collection.id, (upper, lower)) == [lower]
    assert len(store.add_paths_to_collection(collection.id, (lower,)).item_paths) == 2
    with sqlite3.connect(tmp_path / "library.sqlite3") as connection:
        assert connection.execute("SELECT COUNT(*) FROM collection_items").fetchone()[0] == 2
