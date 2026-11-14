"""A named person's photos from every folder of the library, shown together in the grid."""
import time

import pytest

from image_triage.qml_shell import people_model
from image_triage.qml_shell.people_model import PeopleModel, photos_of_person
from image_triage.qml_shell.photos import PhotoModel
from test_qml_shell import qapp, pump  # noqa: F401

# What each folder's face index holds: person name -> (cluster ids, the photos those clusters are in).
INDEX = {
    "/lib/wedding": {"Ana": ([1, 4], ["/lib/wedding/1.jpg", "/lib/wedding/2.jpg"]), "": ([9], ["/lib/wedding/9.jpg"])},
    "/lib/trip": {"ana": ([2], ["/lib/trip/7.jpg"]), "Ben": ([3], ["/lib/trip/8.jpg"])},
    "/lib/empty": {},
}


@pytest.fixture
def indexes(monkeypatch):
    def read_people(db):
        return [{"id": str(clusters[0]), "name": name, "count": len(paths), "clusters": list(clusters), "ignored": False}
                for name, (clusters, paths) in INDEX[db].items()]

    def paths_of(db, clusters):
        wanted = set(clusters)
        return [path for _name, (ids, paths) in INDEX[db].items() if wanted & set(ids) for path in paths]

    monkeypatch.setattr(people_model, "_read_people", read_people)
    monkeypatch.setattr("image_triage.face_groups.face_group_photo_paths", paths_of)
    return lambda folder: folder if folder in INDEX else None      # a folder's index, or None when it was never analysed


def test_the_person_is_found_by_name_in_every_folder_that_was_analysed(indexes):
    folders = ["/lib/wedding", "/lib/trip", "/lib/empty", "/lib/never-analysed"]
    assert photos_of_person("Ana", folders, indexes) == ["/lib/wedding/1.jpg", "/lib/wedding/2.jpg", "/lib/trip/7.jpg"]
    assert photos_of_person("ben", folders, indexes) == ["/lib/trip/8.jpg"]
    assert photos_of_person("", folders, indexes) == []                  # unnamed people are not one person across folders
    assert photos_of_person("Nobody", folders, indexes) == []


def test_a_folder_whose_index_cannot_be_read_does_not_stop_the_others(indexes, monkeypatch):
    def flaky(folder):
        if folder == "/lib/wedding":
            raise OSError("the share went away")
        return indexes(folder)

    assert photos_of_person("Ana", ["/lib/wedding", "/lib/trip"], flaky) == ["/lib/trip/7.jpg"]


def wait(qapp, predicate):
    end = time.monotonic() + 5
    while not predicate() and time.monotonic() < end:
        pump(qapp, .02)
    assert predicate()


def model(qapp, indexes, monkeypatch, folders):
    photos = PhotoModel(parent=qapp)
    shown, said = [], []
    monkeypatch.setattr(photos, "loadPaths", lambda paths, title, kind="search": shown.append((list(paths), title, kind)))
    people = PeopleModel(photos, parent=qapp)
    people._db_path = indexes
    people._folder = "/lib/wedding"
    people._people = [{"id": "1", "name": "Ana", "count": 2, "clusters": [1, 4]}, {"id": "9", "name": "", "count": 1, "clusters": [9]}]
    people._library_folders = folders
    people.said.connect(lambda kind, title, body: said.append(title))
    return people, shown, said


def test_show_everywhere_opens_the_persons_photos_together_in_the_grid(qapp, indexes, monkeypatch):
    people, shown, said = model(qapp, indexes, monkeypatch, ["/lib/trip", "/lib/empty"])
    people.showEverywhere("1")
    assert shown == []                                   # the folders are read on a thread, not here
    wait(qapp, lambda: shown)
    assert shown == [(["/lib/wedding/1.jpg", "/lib/wedding/2.jpg", "/lib/trip/7.jpg"], "Ana", "person")] and said == []


def test_an_unnamed_person_and_a_library_with_no_other_folders_are_explained(qapp, indexes, monkeypatch):
    people, shown, said = model(qapp, indexes, monkeypatch, [])
    people.showEverywhere("9")
    assert said == ["Name this person first"] and shown == []
    people.showEverywhere("1")
    wait(qapp, lambda: shown)
    assert said[-1] == "Only this folder was searched" and shown[0][0] == ["/lib/wedding/1.jpg", "/lib/wedding/2.jpg"]


def test_a_later_request_replaces_an_earlier_one(qapp, indexes, monkeypatch):
    people, shown, said = model(qapp, indexes, monkeypatch, ["/lib/trip"])
    people._show_everywhere(people._everywhere_token - 1, "Old", ["/x.jpg"], 2)
    assert shown == []


def test_the_library_lists_the_folders_it_has_read(tmp_path):
    import sqlite3

    from image_triage.library_store import LibraryStore

    store = LibraryStore.__new__(LibraryStore)
    store._db_path = tmp_path / "library.sqlite3"
    store._initialize()
    with sqlite3.connect(store._db_path) as connection:
        connection.execute("INSERT INTO catalog_roots (root_path, enabled) VALUES ('/lib', 1), ('/off', 0)")
        connection.executemany("INSERT INTO catalog_folder_state (root_path, folder_path) VALUES (?, ?)",
                               [("/lib", "/lib/trip"), ("/lib", "/lib/wedding"), ("/off", "/off/old")])
    assert store.indexed_folders() == ["/lib/trip", "/lib/wedding"]     # a root that is switched off is not searched
