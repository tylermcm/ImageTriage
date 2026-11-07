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
