"""A dead network drive must not be able to freeze the window or empty the Drives list and Folders tree.

Regression for a mapped drive (``P:`` -> a machine that was switched off): Windows takes ~20 s to give up on each query to
it. The Drives list and the Folders tree shared one ``QFileSystemModel`` rooted at "all drives", which asks about every
drive on a single worker thread, so the Drives list stayed empty and every folder listing waited for the dead drive; the
drive list's disk-usage bar also asked ``QStorageInfo`` on the GUI thread. The Drives list now has its own model
(``image_triage/drive_list_model.py``) and the tree is rooted at one drive at a time.
"""
from __future__ import annotations

import threading
import time

import pytest
from PySide6.QtCore import QModelIndex, Qt
from PySide6.QtWidgets import QApplication

from image_triage import drive_list_model, path_policy
from image_triage.drive_list_model import CHECKING, OFFLINE, READY, DriveListModel
from tests.harness import make_jpegs, open_folder, pump_until

@pytest.fixture
def qapp():
    return QApplication.instance() or QApplication([])


FIXED = path_policy.DRIVE_FIXED
REMOTE = path_policy.DRIVE_REMOTE
REMOVABLE = path_policy.DRIVE_REMOVABLE


def _model(roots, probe):
    return DriveListModel(roots_provider=lambda: list(roots), probe=probe)


def _row(model: DriveListModel, path: str) -> int:
    index = model.index_for_path(path)
    assert index.isValid(), path
    return index.row()


def _state(model: DriveListModel, path: str) -> str:
    return model.state_of(path)


def test_a_dead_network_drive_does_not_hold_up_the_list(monkeypatch, qapp) -> None:
    release = threading.Event()
    asked_from: list[bool] = []

    def probe(path):
        asked_from.append(threading.current_thread() is threading.main_thread())
        release.wait(timeout=30)  # the machine that is switched off (and a local disk that is slow to spin up)
        return path == "C:/", "Windows" if path == "C:/" else "", None

    model = _model([("C:/", FIXED), ("P:/", REMOTE), ("X:/", REMOTE)], probe)
    started = time.perf_counter()
    model.refresh()
    assert time.perf_counter() - started < 1.0, "building the list must not wait for any drive"

    assert model.rowCount() == 1  # only the local drive is listed while the others are still being asked
    assert _state(model, "C:/") == READY  # a local drive is listed as ready without waiting to be asked
    assert _state(model, "P:/") == CHECKING and _state(model, "X:/") == CHECKING
    assert not model.index_for_path("P:/").isValid()
    assert model.usage_ratio("P:/") is None  # nothing is measured on the GUI thread for a network drive
    release.set()
    assert pump_until(lambda: _state(model, "P:/") == OFFLINE, timeout=10)
    assert asked_from and not any(asked_from), "drives are only ever asked about off the GUI thread"


def test_a_drive_that_does_not_answer_is_not_listed(monkeypatch, qapp) -> None:
    """An offline share or an empty card reader only added clutter, so it is simply left out."""
    model = _model([("C:/", FIXED), ("P:/", REMOTE), ("D:/", REMOVABLE)], lambda path: (path == "C:/", "", None))
    model.refresh()
    assert pump_until(lambda: _state(model, "P:/") == OFFLINE and _state(model, "D:/") == OFFLINE, timeout=10)

    assert model.rowCount() == 1 and model.filePath(model.index(0)) == "C:/"
    assert not model.index_for_path("P:/").isValid() and not model.index_for_path("D:/").isValid()
    assert not model.is_available("P:/")
    assert model.usage_ratio("P:/") is None
    assert model.flags(model.index(0)) & Qt.ItemFlag.ItemIsSelectable  # the listed drive stays usable


def test_a_drive_that_comes_online_later_appears_by_itself(monkeypatch, qapp) -> None:
    answers = {"X:/": (False, "", None)}
    model = _model([("C:/", FIXED), ("X:/", REMOTE)], lambda path: answers.get(path, (True, "Windows", 0.1)))
    model.refresh()
    assert pump_until(lambda: _state(model, "X:/") == OFFLINE, timeout=10)
    assert model.rowCount() == 1

    answers["X:/"] = (True, "ColossalBoi", 0.4)  # the machine was switched on; the user hits Refresh
    model.refresh()
    assert pump_until(lambda: model.rowCount() == 2, timeout=10)
    assert [model.filePath(model.index(row)) for row in range(2)] == ["C:/", "X:/"]  # in drive-letter order


def test_a_listed_network_drive_stays_listed_while_it_is_asked_again(monkeypatch, qapp) -> None:
    release = threading.Event()
    calls = {"count": 0}

    def probe(_path):
        calls["count"] += 1
        if calls["count"] > 1:
            release.wait(timeout=30)  # the second check is slow
        return True, "ColossalBoi", 0.4

    model = _model([("X:/", REMOTE)], probe)
    model.refresh()
    assert pump_until(lambda: model.rowCount() == 1, timeout=10)
    model.refresh()
    assert model.rowCount() == 1, "a drive that was ready does not blink out of the list while it is re-checked"
    release.set()


def test_a_network_drive_that_answers_gets_its_label_and_usage(monkeypatch, qapp) -> None:
    model = _model([("X:/", REMOTE)], lambda _path: (True, "ColossalBoi", 0.4))
    model.refresh()
    assert model.rowCount() == 0, "not listed until a check has found it ready"
    assert pump_until(lambda: model.rowCount() == 1, timeout=10)

    text = model.data(model.index(0), Qt.ItemDataRole.DisplayRole)
    assert "ColossalBoi" in text and "X:" in text
    assert model.usage_ratio("X:/") == 0.4
    assert model.is_available("X:/")


def test_a_late_answer_to_an_older_refresh_is_ignored(monkeypatch, qapp) -> None:
    first_release, second_release = threading.Event(), threading.Event()
    calls = {"count": 0}

    def probe(_path):
        calls["count"] += 1
        mine = calls["count"]
        (first_release if mine == 1 else second_release).wait(timeout=30)
        return mine == 1, "old" if mine == 1 else "new", 0.1  # the first refresh would say "available"

    model = _model([("X:/", REMOTE)], probe)
    model.refresh()
    model.refresh()  # the user hit Refresh again before the first answer came back
    first_release.set()
    pump_until(lambda: False, timeout=0.5)
    assert _state(model, "X:/") == CHECKING, "the first refresh's answer must not be applied to the second"
    second_release.set()
    assert pump_until(lambda: _state(model, "X:/") == OFFLINE, timeout=10)


def test_the_rows_are_views_of_the_file_system_model_surface(monkeypatch, qapp) -> None:
    model = _model([("C:/", FIXED)], lambda _path: (True, "", None))
    model.refresh()
    index = model.index(0)
    assert model.filePath(index) == "C:/"
    assert model.is_drive(index)  # what the list's delegate uses to know a row is a drive (no file-system query)
    assert model.data(index, Qt.ItemDataRole.ToolTipRole) is None  # no hover tooltip on a drive row
    assert not model.is_drive(QModelIndex())
    assert model.index_for_path("C:\\").isValid() and model.index_for_path("c:/").isValid()
    assert not model.index_for_path("Q:/").isValid()


def test_a_drive_that_appears_is_picked_up_without_asking_the_others_again(monkeypatch, qapp) -> None:
    roots = [("C:/", FIXED)]
    probes: list[str] = []
    model = _model(roots, lambda path: probes.append(path) or (True, "card", 0.2))
    model.refresh()
    assert model.sync_roots() is False  # nothing changed: nothing re-read, nothing re-asked

    roots.append(("D:/", REMOVABLE))
    assert model.sync_roots() is True
    assert pump_until(lambda: _state(model, "D:/") == READY, timeout=10)
    assert probes[1:] == ["D:/"]  # after the first build, only the new drive was asked about


def test_stale_usage_numbers_are_remeasured_in_the_background(monkeypatch, qapp) -> None:
    """The usage bar is painted often; measuring a spun-down disk inside the paint would freeze the window."""
    asked_from: list[bool] = []

    def probe(_path):
        asked_from.append(threading.current_thread() is threading.main_thread())
        return True, "Disk", 0.3

    model = _model([("C:/", FIXED)], probe)
    model.refresh()
    assert pump_until(lambda: model.usage_ratio("C:/") == 0.3, timeout=10)
    model._entries[0].measured_at = time.monotonic() - 3600  # the numbers have gone stale

    assert model.usage_ratio("C:/") == 0.3, "the old number is shown while the new one is fetched"
    assert pump_until(lambda: len(asked_from) >= 2, timeout=10), "and a fresh measurement is started"
    assert not any(asked_from), "off the GUI thread"


def test_the_drive_probe_never_uses_qstorageinfo() -> None:
    """A thread sitting in ``QStorageInfo`` for an offline network drive holds Python's interpreter lock for ~20 s, which
    froze every thread, the main one included: the app never got past its splash screen. The probe uses calls that let
    go of the lock while they wait (``GetVolumeInformationW`` through ctypes, ``shutil.disk_usage``)."""
    import inspect

    source = inspect.getsource(drive_list_model)
    code = "\n".join(line for line in source.splitlines() if not line.lstrip().startswith(("#", '"', "``")))
    assert "QStorageInfo(" not in code
    assert "GetVolumeInformationW" in code and "disk_usage" in code


def test_storage_numbers_describes_a_real_drive(tmp_path) -> None:
    available, label, ratio = drive_list_model.storage_numbers(tmp_path.anchor)
    assert available is True
    assert isinstance(label, str)
    assert ratio is not None and 0.0 <= ratio <= 1.0


def test_storage_numbers_of_a_drive_that_is_not_there_is_unavailable() -> None:
    import os
    import string

    if os.name != "nt":
        return
    present = {path[0].upper() for path, _kind in path_policy.drive_roots()}
    free_letter = next(letter for letter in reversed(string.ascii_uppercase) if letter not in present)
    assert drive_list_model.storage_numbers(f"{free_letter}:/") == (False, "", None)


# ---------------------------------------------------------------------------- on the real window


def test_the_window_lists_drives_without_waiting_for_a_dead_one(main_window, dialogs, monkeypatch) -> None:
    window = main_window
    release = threading.Event()
    monkeypatch.setattr(window._navigation.drive_model, "_roots_provider", lambda: [("C:/", FIXED), ("P:/", REMOTE)])

    def dead_drive(_path):
        release.wait(timeout=30)
        return False, "", None

    monkeypatch.setattr(window._navigation.drive_model, "_probe", dead_drive)

    started = time.perf_counter()
    window._navigation.refresh_drive_list()  # what the Drives "refresh" button does
    elapsed = time.perf_counter() - started

    assert elapsed < 3.0, f"refreshing the drives waited {elapsed:.1f}s for a drive that does not answer"
    assert window._navigation.drive_model.rowCount() == 1  # the local drive; the one being asked about is not listed yet
    assert window.drive_list.model() is window._navigation.drive_model
    release.set()
    pump_until(lambda: False, timeout=0.3)


def test_painting_the_drive_list_never_asks_a_drive_anything(monkeypatch, qapp) -> None:
    """Sizing and painting a row used to call ``QFileInfo.isRoot`` (it queries the file system) and ``QStorageInfo`` on the
    GUI thread, so an offline network drive froze the window for ~20 s and the app never got past its splash screen.

    Uses its own view and model, not the shared test window: resizing and refreshing that one leaked into later tests."""
    from PySide6.QtCore import QFileInfo

    from image_triage.ui import prototype_style
    from image_triage.ui.prototype_style import FolderTreeView

    model = DriveListModel(
        roots_provider=lambda: [("C:/", FIXED), ("P:/", REMOTE), ("X:/", REMOTE)],
        probe=lambda path: (path != "P:/", "Disk", 0.5) if path != "P:/" else (False, "", None),
    )
    model.refresh()
    assert pump_until(lambda: _state(model, "P:/") == OFFLINE and model.rowCount() == 2, timeout=10)
    view = FolderTreeView()
    view.setModel(model)
    view.set_drives_only(True)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("a drive was asked about on the GUI thread")

    for name in ("isRoot", "exists", "isDir", "size"):
        monkeypatch.setattr(QFileInfo, name, forbidden)
    monkeypatch.setattr(prototype_style, "QStorageInfo", forbidden)

    # Qt swallows an exception raised inside a paint callback, so ask the questions the delegate asks directly as well.
    for row in range(model.rowCount()):
        assert prototype_style._index_is_drive(model.index(row))
        assert model.usage_ratio(model.filePath(model.index(row))) in (None, 0.5)

    view.resize(300, 300)
    started = time.perf_counter()
    pixmap = view.grab()  # lays out and paints every row: size hints, icons, names, usage bars
    view.sizeHintForRow(0)
    view.fit_height_to_rows()

    assert not pixmap.isNull()
    assert time.perf_counter() - started < 3.0
    assert not hasattr(model, "fileInfo"), "no file-system-backed query is offered to a view"


def test_the_folder_model_is_never_rooted_at_all_drives(main_window, dialogs, tmp_path, monkeypatch) -> None:
    """``setRootPath("")`` makes the model list, and so ask, every drive on its single worker thread."""
    from PySide6.QtWidgets import QFileSystemModel

    roots: list[str] = []
    real = QFileSystemModel.setRootPath

    def recording(model, path):
        roots.append(path)
        return real(model, path)

    monkeypatch.setattr(QFileSystemModel, "setRootPath", recording)
    window = main_window
    folder = tmp_path / "shoot"
    make_jpegs(folder, ["a.jpg", "b.jpg"])
    window._navigation.select_folder(str(folder))
    assert pump_until(lambda: len(window._records) == 2, timeout=15)
    window._navigation.refresh_folder_tree()  # what every folder create / rename / move / delete does
    window._navigation.refresh_drive_list()  # the Drives "refresh" button
    window._navigation.handle_drive_selected(window._navigation.drive_model.index_for_path(str(tmp_path.anchor)))
    pump_until(lambda: False, timeout=0.3)

    assert roots, "the tree was rooted at the folder's drive"
    assert all(path for path in roots), f"a model was rooted at 'all drives': {roots}"
    assert window.folder_tree.model() is window.folder_model
    assert window.folder_model.filePath(window.folder_tree.rootIndex()).rstrip("/\\").casefold() == str(
        tmp_path.anchor
    ).rstrip("/\\").casefold()


def test_the_tree_shows_nothing_until_a_drive_is_rooted(main_window, dialogs) -> None:
    window = main_window
    window._navigation.refresh_folder_tree()  # no folder open on a plain local drive: nothing to root at yet
    window._current_folder = ""
    window._navigation.attach_tree_model(window._navigation.empty_tree_model())

    assert window.folder_tree.model() is window._navigation.empty_tree_model()
    assert window.folder_tree.model().rowCount() == 0
