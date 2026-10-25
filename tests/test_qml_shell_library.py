"""The Library page of the QML shell: favorites, drives and the folder tree, and opening a folder from them."""
from __future__ import annotations

import time

import pytest
from PySide6.QtCore import QSettings

from image_triage import path_policy
from image_triage.app_identity import user_settings
from image_triage.drive_list_model import DriveListModel
from image_triage.qml_shell import library_nav
from image_triage.qml_shell.library_nav import FAVORITES_KEY, RECENT_KEY, LibraryNavModel, _key, _size_text
from image_triage.qml_shell.photos import PhotoModel
from test_qml_shell import pump, qapp  # noqa: F401  (the same application fixture)


@pytest.fixture(autouse=True)
def clean_settings():
    settings = user_settings()
    for key in (FAVORITES_KEY, RECENT_KEY, library_nav.SHOW_HIDDEN_KEY, library_nav.SINGLE_BRANCH_KEY, library_nav.WATCH_KEY, library_nav.LAST_FOLDER_KEY, library_nav.RESTORE_POSITION_KEY, library_nav.FOLDER_STATE_KEY):
        settings.remove(key)
    yield
    for key in (FAVORITES_KEY, RECENT_KEY, library_nav.SHOW_HIDDEN_KEY, library_nav.LAST_FOLDER_KEY, library_nav.RESTORE_POSITION_KEY, library_nav.FOLDER_STATE_KEY):
        settings.remove(key)


@pytest.fixture
def library(tmp_path):
    """A drive with 2025/{Trip/{Day1,Day2},Wedding} and 2026/Georgia, photos in Day1 and Georgia."""
    from PIL import Image

    root = tmp_path / "Photos"
    for folder in ("2025/Trip/Day1", "2025/Trip/Day2", "2025/Wedding", "2026/Georgia"):
        (root / folder).mkdir(parents=True)
    for folder, count in (("2025/Trip/Day1", 3), ("2026/Georgia", 2)):
        for index in range(count):
            Image.new("RGB", (32, 24), (index * 60, 80, 120)).save(root / folder / f"IMG_{index:02d}.jpg")
    return root


@pytest.fixture
def nav(qapp, library):
    photos = PhotoModel(parent=qapp)
    model = LibraryNavModel(photos, drives=DriveListModel(roots_provider=lambda: [], probe=lambda p: (False, "", None)),
                            drive_root=lambda path: str(library), parent=qapp)             # the tree is rooted at the test's "drive"
    model.photos = photos
    return model


def until(qapp, condition, seconds=5.0):
    end = time.monotonic() + seconds
    while not condition() and time.monotonic() < end:
        pump(qapp, 0.02)
    assert condition()


def rows(model):
    return [(r["name"], r["depth"], r["icon"], r["current"]) for r in model.tree]


# -- the mockup's sample, for --mock and the other windows' tests ----------------------------------------------------------------

def test_memory_mode_is_the_mockups_library(qapp):
    photos = PhotoModel(parent=qapp)
    nav = LibraryNavModel(photos, memory=True, parent=qapp)
    assert [(f["name"], f["count"]) for f in nav.favorites] == [("Wedding 2026", "1,284"), ("Client selects", "212")]
    assert [(d["name"], d["detail"], d["ratio"]) for d in nav.drives] == [("Photos (D:)", "612 GB free of 2 TB", 0.70), ("Cards (E:)", "41 GB free of 256 GB", 0.84)]
    assert [(r["name"], r["depth"], r["current"]) for r in nav.tree] == [
        ("2025", 0, False), ("2026", 0, False), ("January", 1, False), ("Georgia trip", 1, False), ("Kutaisi", 2, True), ("Tbilisi", 2, False)]
    nav.openFolder("sample:Tbilisi")                                       # a sample row opens nothing
    assert nav.openPath == "" and not photos.loading
    photos.setRows([{"path": f"/p/IMG_{i}.jpg", "name": f"IMG_{i}", "ext": "JPG", "raw": False, "decision": "", "rating": 0, "ai": 50} for i in range(5)], "/p/Kutaisi")
    assert [(r["name"], r["count"]) for r in nav.tree if r["current"]] == [("Kutaisi", "5")]       # the open folder's row follows the grid


# -- opening a folder ----------------------------------------------------------------------------------------------------------

def test_opening_a_folder_loads_it_and_opens_the_tree_down_to_it(qapp, nav, library):
    assert nav.treeStatus == "Choose a drive or open a folder" and nav.tree == []
    day1 = library / "2025" / "Trip" / "Day1"
    nav.openFolder(str(day1))
    until(qapp, lambda: not nav.photos.loading and any(r[3] for r in rows(nav)))
    assert rows(nav) == [("2025", 0, "chevDown", False), ("Trip", 1, "chevDown", False), ("Day1", 2, "chevR", True), ("Day2", 2, "chevR", False),
                         ("Wedding", 1, "chevR", False), ("2026", 0, "chevR", False)] or rows(nav)[:3] == [
        ("2025", 0, "chevDown", False), ("Trip", 1, "chevDown", False), ("Day1", 2, "chevR", True)]
    assert nav.photos.folderPath == str(day1) and nav.photos.total == 3
    assert nav.openPath == str(day1) and nav.treeStatus == ""
    assert next(r for r in nav.tree if r["name"] == "Day1")["count"] == "3"        # known once the folder has been read


def test_the_rows_of_a_drive_are_its_folders(qapp, nav, library):
    nav.openFolder(str(library))                                          # a drive itself: its folders are the top rows
    until(qapp, lambda: len(nav.tree) == 2)
    assert [(r["name"], r["depth"], r["current"]) for r in nav.tree] == [("2025", 0, False), ("2026", 0, False)]


def test_a_chevron_opens_and_closes_a_folder_and_lists_it_once(qapp, nav, library, monkeypatch):
    from image_triage import scanner

    listed = []
    real = scanner.scan_child_folders
    monkeypatch.setattr(scanner, "scan_child_folders", lambda folder, **kw: (listed.append(folder), real(folder, **kw))[1])
    nav.openFolder(str(library))
    until(qapp, lambda: len(nav.tree) == 2)
    nav.toggle(str(library / "2026"))
    until(qapp, lambda: any(r["name"] == "Georgia" for r in nav.tree))
    assert [r["name"] for r in nav.tree] == ["2025", "2026", "Georgia"] and nav.tree[2]["depth"] == 1
    nav.toggle(str(library / "2026"))
    assert [r["name"] for r in nav.tree] == ["2025", "2026"]
    nav.toggle(str(library / "2026"))                                    # opened again: no second listing
    assert [r["name"] for r in nav.tree] == ["2025", "2026", "Georgia"]
    assert [p for p in listed if p.endswith("2026")] == [str(library / "2026")]


def names(model):
    return [r["name"] for r in model.tree]


def test_opening_a_folder_closes_the_opened_folders_beside_it(qapp, nav, library):
    nav.openFolder(str(library))
    until(qapp, lambda: len(nav.tree) == 2)
    nav.toggle(str(library / "2025"))
    until(qapp, lambda: names(nav) == ["2025", "Trip", "Wedding", "2026"])
    nav.toggle(str(library / "2025" / "Trip"))
    until(qapp, lambda: "Day1" in names(nav))
    nav.toggle(str(library / "2026"))                                    # the Widgets default: one branch per level
    until(qapp, lambda: "Georgia" in names(nav))
    assert names(nav) == ["2025", "2026", "Georgia"]                     # 2025 and what was open below it are closed


def test_with_the_setting_off_both_branches_stay_open(qapp, nav, library):
    user_settings().setValue(library_nav.SINGLE_BRANCH_KEY, False)
    nav.openFolder(str(library))
    until(qapp, lambda: len(nav.tree) == 2)
    nav.toggle(str(library / "2025"))
    nav.toggle(str(library / "2026"))
    until(qapp, lambda: "Georgia" in names(nav) and "Wedding" in names(nav))
    assert names(nav) == ["2025", "Trip", "Wedding", "2026", "Georgia"]


def test_the_way_to_the_open_folder_stays_open(qapp, nav, library):
    nav.openFolder(str(library / "2025" / "Trip" / "Day1"))
    until(qapp, lambda: "Day1" in names(nav))
    nav.toggle(str(library / "2026"))
    until(qapp, lambda: "Georgia" in names(nav))
    assert names(nav) == ["2025", "Trip", "Day1", "Day2", "Wedding", "2026", "Georgia"]    # 2025 and Trip lead to the open folder: they stay


def photos_loaded(qapp, nav, count):
    until(qapp, lambda: not nav.photos.loading and nav.photos.total == count)


def test_a_file_that_appears_in_the_open_folder_is_picked_up(qapp, nav, library, monkeypatch):
    from PIL import Image

    monkeypatch.setattr(library_nav, "WATCH_DELAY_MS", 100)
    nav._watch_timer.setInterval(100)
    day = library / "2025" / "Trip" / "Day1"
    nav.openFolder(str(day))
    photos_loaded(qapp, nav, 3)
    assert nav._watcher.directories() == [str(day)] or [p.replace("\\", "/") for p in nav._watcher.directories()] == [str(day).replace("\\", "/")]
    Image.new("RGB", (32, 24), (200, 10, 10)).save(day / "IMG_99.jpg")             # something else wrote a file
    photos_loaded(qapp, nav, 4)
    (day / "IMG_99.jpg").unlink()
    photos_loaded(qapp, nav, 3)


def test_the_watch_is_off_when_the_setting_is_off_and_moves_with_the_folder(qapp, nav, library):
    user_settings().setValue(library_nav.WATCH_KEY, False)
    nav.openFolder(str(library / "2026" / "Georgia"))
    photos_loaded(qapp, nav, 2)
    assert nav._watcher.directories() == []
    user_settings().setValue(library_nav.WATCH_KEY, True)
    nav.openFolder(str(library / "2025" / "Trip" / "Day1"))
    photos_loaded(qapp, nav, 3)
    assert len(nav._watcher.directories()) == 1 and nav._watcher.directories()[0].replace("\\", "/").endswith("Day1")


def test_reading_the_folder_again_keeps_the_grid_on_the_photo(qapp, nav, library):
    day = library / "2025" / "Trip" / "Day1"
    nav.openFolder(str(day))
    photos_loaded(qapp, nav, 3)
    moved = []
    nav.positionRestored.connect(moved.append)
    nav.savePosition(str(day / "IMG_02.jpg"))                                       # the person is on the last photo
    nav.refreshFolder()
    until(qapp, lambda: moved)
    assert moved == [nav.photos.row_for_path(str(day / "IMG_02.jpg"))] and moved[0] == 2


def test_a_folder_the_person_has_left_is_not_refreshed_into_view(qapp, nav, library):
    nav.openFolder(str(library / "2026" / "Georgia"))
    photos_loaded(qapp, nav, 2)
    nav.savePosition(str(library / "2026" / "Georgia" / "IMG_01.jpg"))
    nav.openFolder(str(library / "2025" / "Trip" / "Day1"))
    photos_loaded(qapp, nav, 3)
    nav.refreshFolder()                                                              # the last photo was in the other folder: not used
    assert nav._pending_current == ""
    photos_loaded(qapp, nav, 3)


def test_a_folder_with_no_folders_in_it_gets_the_folder_icon(qapp, nav, library):
    nav.openFolder(str(library))
    until(qapp, lambda: len(nav.tree) == 2)
    nav.toggle(str(library / "2025"))
    until(qapp, lambda: any(r["name"] == "Wedding" for r in nav.tree))
    nav.toggle(str(library / "2025" / "Wedding"))
    until(qapp, lambda: next(r for r in nav.tree if r["name"] == "Wedding")["icon"] == "library")
    assert next(r for r in nav.tree if r["name"] == "Wedding")["expandable"] is False


def test_hidden_folders_follow_the_widgets_setting(qapp, nav, library):
    (library / ".private").mkdir()
    nav.openFolder(str(library))
    until(qapp, lambda: len(nav.tree) >= 2)
    assert [r["name"] for r in nav.tree] == ["2025", "2026"]
    user_settings().setValue(library_nav.SHOW_HIDDEN_KEY, True)
    nav.refresh()
    until(qapp, lambda: len(nav.tree) == 3)
    assert [r["name"] for r in nav.tree] == ["2025", "2026", ".private"]


def test_a_folder_that_is_gone_opens_nothing(qapp, nav, library):
    nav.openFolder(str(library / "nowhere"))
    assert nav.openPath == "" and not nav.photos.loading


def test_a_click_on_another_folder_while_one_is_loading_wins(qapp, nav, library):
    nav.openFolder(str(library / "2025" / "Trip" / "Day1"))
    nav.openFolder(str(library / "2026" / "Georgia"))
    until(qapp, lambda: not nav.photos.loading)
    pump(qapp, 0.3)
    assert nav.photos.folderPath == str(library / "2026" / "Georgia") and nav.photos.total == 2


def test_the_search_field_narrows_the_loaded_rows(qapp, nav, library):
    nav.openFolder(str(library / "2025" / "Trip" / "Day1"))
    until(qapp, lambda: any(r[3] for r in rows(nav)))
    nav.setQuery("day2")
    assert [r["name"] for r in nav.tree] == ["2025", "Trip", "Day2"]      # the match and the folders it is in
    nav.setQuery("zzz")
    assert nav.tree == [] and nav.treeStatus == "No folder matches"
    nav.setQuery("")
    assert len(nav.tree) >= 3


# -- favorites and recent folders ------------------------------------------------------------------------------------------------

def test_favorites_are_the_widgets_settings_key(qapp, nav, library):
    nav.addFavorite(str(library / "2026"))
    nav.addFavorite(str(library / "2026"))                                # once
    nav.addFavorite(str(library / "2025"))
    assert user_settings().value(FAVORITES_KEY, [], list) == [str(library / "2026"), str(library / "2025")]
    assert [(f["name"], f["current"]) for f in nav.favorites] == [("2026", False), ("2025", False)]
    assert nav.isFavorite(str(library / "2025"))
    nav.removeFavorite(str(library / "2025"))
    assert [f["name"] for f in nav.favorites] == ["2026"] and not nav.isFavorite(str(library / "2025"))
    nav.openFolder(str(library / "2026"))
    assert nav.favorites[0]["current"]


def test_a_favorite_that_is_gone_from_a_local_drive_is_not_listed_but_stays_saved(qapp, nav, library):
    gone = str(library / "deleted")
    user_settings().setValue(FAVORITES_KEY, [str(library / "2026"), gone])
    assert [f["name"] for f in nav.favorites] == ["2026"]


def test_the_search_field_narrows_the_favorites(qapp, nav, library):
    nav.addFavorite(str(library / "2026"))
    nav.addFavorite(str(library / "2025"))
    nav.setQuery("25")
    assert [f["name"] for f in nav.favorites] == ["2025"]


def test_recent_folders_are_written_like_the_widgets_list(qapp, nav, library):
    nav.openFolder(str(library / "2025"))
    nav.openFolder(str(library / "2026"))
    nav.openFolder(str(library / "2025"))                                 # moved to the front, not repeated
    assert user_settings().value(RECENT_KEY, [], list) == [str(library / "2025"), str(library / "2026")]


# -- drives -----------------------------------------------------------------------------------------------------------------------

def test_sizes_are_written_the_way_windows_writes_them():
    assert _size_text(2 * 1024 ** 4) == "2 TB"
    assert _size_text(int(1.8 * 1024 ** 4)) == "1.8 TB"
    assert _size_text(612 * 1024 ** 3) == "612 GB"
    assert _size_text(256 * 1024 ** 3) == "256 GB"
    assert _size_text(40 * 1024 ** 2) == "40 MB"


def test_a_drive_shows_its_label_free_space_and_meter(qapp):
    photos = PhotoModel(parent=qapp)
    drives = DriveListModel(roots_provider=lambda: [("D:/", path_policy.DRIVE_FIXED)], probe=lambda p: (True, "Photos", 0.7))
    nav = LibraryNavModel(photos, drives=drives, parent=qapp)
    drives.refresh()
    until(qapp, lambda: nav.drives and nav.drives[0]["name"] == "Photos (D:)")
    nav._sizes[_key("D:/")] = (612 * 1024 ** 3, 2 * 1024 ** 4)
    entry = nav.drives[0]
    assert (entry["detail"], entry["ratio"], entry["path"]) == ("612 GB free of 2 TB", 0.7, "D:/")


def test_the_library_page_loads_in_the_window_without_a_qml_complaint(qapp, tmp_path):
    from test_qml_shell import _load_main

    engine, window, warnings = _load_main(qapp, tmp_path)
    pump(qapp, 0.4)
    assert not [w for w in warnings if "Required property" in w or "libraryNav" in w or "Cannot" in w or "ReferenceError" in w or "TypeError" in w], warnings
    window.close()
    engine.deleteLater()
    pump(qapp, 0.2)


def test_recent_folders_are_listed_newest_first_for_the_file_menu(qapp, nav, library):
    assert nav.recentFolders == []
    nav.openFolder(str(library / "2025"))
    nav.openFolder(str(library / "2026"))
    nav.openFolder(str(library / "2025"))
    assert [(r["name"], r["path"]) for r in nav.recentFolders] == [("2025", str(library / "2025")), ("2026", str(library / "2026"))]
    user_settings().setValue(RECENT_KEY, [str(library / "2026"), str(library / "deleted")] + [str(library / f"x{i}") for i in range(10)])
    assert [r["name"] for r in nav.recentFolders][:1] == ["2026"] and len(nav.recentFolders) <= 5     # a folder gone from a local drive is left out, five at most
    assert "deleted" not in [r["name"] for r in nav.recentFolders]


# -- session memory: the last folder, and the photo the person was on --------------------------------------------------------------

def photos_of(nav):
    return [nav.photos.row_at(i)["path"] for i in range(nav.photos.rowCount())]


def loaded(qapp, nav):
    until(qapp, lambda: not nav.photos.loading and nav.photos.rowCount() > 0)


def test_the_last_folder_is_remembered_and_reopened_by_the_next_session(qapp, nav, library):
    day1 = str(library / "2025" / "Trip" / "Day1")
    nav.openFolder(day1)
    loaded(qapp, nav)
    assert user_settings().value(library_nav.LAST_FOLDER_KEY, "", str) == day1             # the Widgets key
    again = LibraryNavModel(PhotoModel(parent=qapp), drives=DriveListModel(roots_provider=lambda: [], probe=lambda p: (False, "", None)),
                            drive_root=lambda path: str(library), parent=qapp)
    again.photos = again._photos
    assert again.restoreLastFolder() and again.openPath == day1
    until(qapp, lambda: not again.photos.loading and again.photos.rowCount() == 3)


def test_a_last_folder_that_is_gone_is_not_opened(qapp, nav, library):
    assert not nav.restoreLastFolder()                                                     # nothing remembered
    user_settings().setValue(library_nav.LAST_FOLDER_KEY, str(library / "deleted"))
    assert not nav.restoreLastFolder() and nav.openPath == ""


def test_headless_checks_and_the_sample_page_never_write_the_settings(qapp, library):
    photos = PhotoModel(parent=qapp)
    quiet = LibraryNavModel(photos, persist=False, drives=DriveListModel(roots_provider=lambda: [], probe=lambda p: (False, "", None)),
                            drive_root=lambda path: str(library), parent=qapp)
    quiet.openFolder(str(library / "2026" / "Georgia"))
    until(qapp, lambda: not photos.loading and photos.rowCount() == 2)
    quiet.savePosition(photos.row_at(1)["path"])
    assert user_settings().value(library_nav.LAST_FOLDER_KEY, "", str) == ""
    assert user_settings().value(library_nav.FOLDER_STATE_KEY, "", str) == ""
    assert user_settings().value(RECENT_KEY, [], list) == []
    assert not quiet.restoreLastFolder()


def test_the_photo_the_person_was_on_is_saved_per_folder_and_put_back(qapp, nav, library):
    import json

    day1 = str(library / "2025" / "Trip" / "Day1")
    nav.openFolder(day1)
    loaded(qapp, nav)
    third = photos_of(nav)[2]
    nav.savePosition(third)
    states = json.loads(user_settings().value(library_nav.FOLDER_STATE_KEY, "", str))
    assert states[_key(day1)] == {"current": third}
    nav.openFolder(str(library / "2026" / "Georgia"))                                      # another folder has its own place
    loaded(qapp, nav)
    nav.savePosition(photos_of(nav)[1])
    assert len(json.loads(user_settings().value(library_nav.FOLDER_STATE_KEY, "", str))) == 2
    rows = []
    nav.positionRestored.connect(rows.append)
    nav.openFolder(day1)                                                                   # back: the grid is told which row to be on
    until(qapp, lambda: rows)
    assert rows == [2] and photos_of(nav)[2] == third
    nav.openFolder(str(library / "2025" / "Wedding"))                                      # a folder never visited: nothing to restore
    pump(qapp, 0.4)
    assert rows == [2]


def test_the_other_fields_of_a_saved_state_are_kept_and_old_folders_make_room(qapp, nav, library):
    import json

    day1 = str(library / "2025" / "Trip" / "Day1")
    old = {f"x{i}": {"current": f"/x{i}/a.jpg"} for i in range(library_nav.STATE_LIMIT)}
    old[_key(day1)] = {"sort": "NAME", "scroll": 120, "current": "/old/a.jpg"}
    user_settings().setValue(library_nav.FOLDER_STATE_KEY, json.dumps(old))
    nav.openFolder(day1)
    loaded(qapp, nav)
    nav.savePosition(photos_of(nav)[1])
    states = json.loads(user_settings().value(library_nav.FOLDER_STATE_KEY, "", str))
    assert states[_key(day1)] == {"sort": "NAME", "scroll": 120, "current": photos_of(nav)[1]}      # the Widgets app's sort and scroll are not touched
    assert len(states) == library_nav.STATE_LIMIT and "x0" not in states and list(states)[-1] == _key(day1)


def test_the_setting_can_turn_the_restore_off(qapp, nav, library):
    day1 = str(library / "2025" / "Trip" / "Day1")
    nav.openFolder(day1)
    loaded(qapp, nav)
    nav.savePosition(photos_of(nav)[2])
    user_settings().setValue(library_nav.RESTORE_POSITION_KEY, False)                      # "Reopen folders where I left off"
    rows = []
    nav.positionRestored.connect(rows.append)
    nav.openFolder(day1)
    pump(qapp, 0.6)
    assert rows == []


def test_nothing_is_saved_until_the_grid_has_been_put_back(qapp, nav, library):
    import json

    day1 = str(library / "2025" / "Trip" / "Day1")
    nav.openFolder(day1)
    loaded(qapp, nav)
    nav.savePosition(photos_of(nav)[2])
    nav.openFolder(day1)                                                                   # reopening: the saved photo is waiting to be restored
    nav.savePosition(photos_of(nav)[0])                                                    # the grid still says row 0: that must not overwrite it
    loaded(qapp, nav)
    pump(qapp, 0.3)
    assert json.loads(user_settings().value(library_nav.FOLDER_STATE_KEY, "", str))[_key(day1)]["current"] == photos_of(nav)[2]
    nav.savePosition(str(library / "2026" / "Georgia" / "IMG_00.jpg"))                     # a photo of another folder is not this folder's position
    assert json.loads(user_settings().value(library_nav.FOLDER_STATE_KEY, "", str))[_key(day1)]["current"] == photos_of(nav)[2]


def test_the_window_moves_the_grid_to_the_restored_row_and_saves_where_it_is(qapp, tmp_path):
    from PySide6.QtCore import QObject

    from test_qml_shell import _load_main, rows

    engine, window, warnings = _load_main(qapp, tmp_path)
    context = engine.rootContext()
    context.contextProperty("photos").setRows(rows(12), "/p")
    pump(qapp, 0.5)
    grid = window.findChild(QObject, "photoGrid")
    context.contextProperty("libraryNav").positionRestored.emit(7)
    pump(qapp, 0.3)
    assert grid.property("currentIndex") == 7
    grid.setProperty("currentIndex", 3)                                                    # the person moves on: a moment later it is remembered
    pump(qapp, 1.1)
    assert context.contextProperty("libraryNav").saved[1] == context.contextProperty("photos").row_at(3)["path"]
    window.close()
    engine.deleteLater()
    pump(qapp, 0.2)
