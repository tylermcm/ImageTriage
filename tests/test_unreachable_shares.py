"""WI-8.5: an unreachable share must neither freeze the window nor cost the user their saved lists.

A NAS that is asleep (or a VPN that is off) makes ``os.path.isdir`` block for ~20 s, and it answers
"no". Two things used to follow:

* the GUI thread did those checks (saved favorites / recent folders / recent destinations at startup, the
  last folder, menus, typed paths), so the whole window froze until the share gave up; and
* an unreachable path was treated as a *deleted* one: the loaders dropped it from the in-memory list, and
  the next save wrote the shortened list back, so favorites, recent folders and Move-To destinations on the
  NAS were erased from settings just because the share was asleep.

The rule now: a path on a network or removable (or otherwise not-plain-local) drive is never checked on the
GUI thread and is never judged missing, so it is never pruned; opening it goes through the scan worker, which
reports failure. A folder that is *provably* gone on a local fixed drive is still pruned as before.

The share is simulated: ``\\\\nas\\...`` paths are classified as network (type 4) by the app, and here every
filesystem question about them is answered "no" and recorded together with the thread that asked.
"""
from __future__ import annotations

import os
import shutil
import threading
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from image_triage.app_identity import user_settings
from image_triage.library_store import LibraryStore

NAS = "\\\\nas\\photos"
NAS_FOLDER = NAS + "\\2026\\shoot"
NAS_OTHER = NAS + "\\2025\\trip"
NAS_DEST = NAS + "\\_winners"


def _is_nas(path) -> bool:
    return str(path).replace("/", "\\").lower().startswith("\\\\nas\\")


def _as_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return [str(item) for item in value]


class AsleepShare:
    """Answers "no" to every filesystem question about ``\\\\nas\\...`` and records who asked."""

    def __init__(self, monkeypatch) -> None:
        self.gui_checks: list[str] = []
        self.worker_checks: list[str] = []
        real_isdir, real_exists = os.path.isdir, os.path.exists

        self.gui_stacks: list[str] = []

        def record(path) -> None:
            on_gui = threading.current_thread() is threading.main_thread()
            (self.gui_checks if on_gui else self.worker_checks).append(str(path))
            if on_gui:
                import traceback

                frames = [f"{Path(f.filename).name}:{f.lineno} {f.name}" for f in traceback.extract_stack()[:-2] if "image_triage" in f.filename]
                self.gui_stacks.append(f"{path} <- " + " > ".join(frames[-5:]))

        def isdir(path):
            if _is_nas(path):
                record(path)
                return False
            return real_isdir(path)

        def exists(path):
            if _is_nas(path):
                record(path)
                return False
            return real_exists(path)

        monkeypatch.setattr(os.path, "isdir", isdir)
        monkeypatch.setattr(os.path, "exists", exists)
        # Path.resolve() asks the filesystem too (a network round trip on a share); when the host is down it
        # can raise, as it did on the real OS ("The specified network name is no longer available").
        import pathlib

        real_resolve = pathlib.Path.resolve
        self.gui_resolves: list[str] = []

        def resolve(path_self, strict=False):
            if _is_nas(path_self):
                record(path_self)
                if threading.current_thread() is threading.main_thread():
                    self.gui_resolves.append(str(path_self))
                raise OSError(64, "The specified network name is no longer available")
            return real_resolve(path_self, strict)

        monkeypatch.setattr(pathlib.Path, "resolve", resolve)
        # The scan worker is what finally reports the failure; make it fail fast instead of waiting on DNS.
        import image_triage.scanner as scanner

        real_scan_folder, real_children = scanner.scan_folder, scanner.scan_child_folders

        def scan_folder(folder, *args, **kwargs):
            if _is_nas(folder):
                record(folder)
                raise FileNotFoundError(folder)
            return real_scan_folder(folder, *args, **kwargs)

        def scan_child_folders(folder, *args, **kwargs):
            if _is_nas(folder):
                record(folder)
                return []
            return real_children(folder, *args, **kwargs)

        monkeypatch.setattr(scanner, "scan_folder", scan_folder)
        monkeypatch.setattr(scanner, "scan_child_folders", scan_child_folders)


@pytest.fixture
def share(monkeypatch) -> AsleepShare:
    return AsleepShare(monkeypatch)




def _saved(key: str) -> list[str]:
    return _as_list(user_settings().value(key, [], list))


# --------------------------------------------------------------------------- nothing is lost


















# --------------------------------------------------------------------------- nothing blocks the GUI thread








# --------------------------------------------------------------------------- the library index
def _library(tmp_path, monkeypatch):
    monkeypatch.setenv("IMAGE_TRIAGE_APPDATA", str(tmp_path / "appdata"))
    return LibraryStore()








# --------------------------------------------------------------------------- the policy module itself
windows_only = pytest.mark.skipif(os.name != "nt", reason="drive types are read with the Windows API")


@windows_only
def test_policy_treats_only_a_plain_local_drive_as_checkable(tmp_path) -> None:
    from image_triage import path_policy

    assert path_policy.is_plain_local(str(tmp_path)) is True
    assert path_policy.is_plain_local(NAS_FOLDER) is False
    assert path_policy.drive_root(NAS_FOLDER) == NAS + "\\"
    assert path_policy.drive_root(r"C:\x\y") == "C:\\"


@windows_only
def test_policy_never_calls_an_unverifiable_path_missing(tmp_path) -> None:
    from image_triage import path_policy

    assert path_policy.confirmed_missing(NAS_FOLDER) is False  # a share: unverifiable, so never "missing"
    assert path_policy.confirmed_missing(str(tmp_path / "gone")) is True  # provably gone on a fixed drive
    assert path_policy.confirmed_missing(str(tmp_path)) is False
    assert path_policy.confirmed_missing("") is False and path_policy.confirmed_missing(None) is False


@windows_only
def test_policy_does_not_call_a_path_on_an_absent_drive_letter_missing() -> None:
    """An unplugged USB drive or a disconnected NAS letter is not the same as a deleted folder."""
    from image_triage import path_policy

    present = {drive[0].upper() for drive in os.listdrives()}
    absent = next(letter for letter in "WVUTSRQ" if letter not in present)
    assert path_policy.confirmed_missing(f"{absent}:\\Photos\\2026") is False


# --------------------------------------------------------------------------- suggestions, launch target, drag-over










class _ModelIndexCalls:
    """Records every ``QFileSystemModel.index(path)`` made from Python, and from which thread.

    That call blocks the GUI thread for ~20 s when the path is on a share that is asleep."""

    def __init__(self, monkeypatch) -> None:
        from PySide6.QtCore import QModelIndex
        from PySide6.QtWidgets import QFileSystemModel

        self.calls: list[tuple[bool, str]] = []
        real_index = QFileSystemModel.index

        def index(model, *args, **kwargs):
            if args and isinstance(args[0], str):
                self.calls.append((threading.current_thread() is threading.main_thread(), args[0]))
                if _is_nas(args[0]):
                    return QModelIndex()
            return real_index(model, *args, **kwargs)

        monkeypatch.setattr(QFileSystemModel, "index", index)

    def gui_paths(self) -> list[str]:
        return [path for on_gui, path in self.calls if on_gui]
















def _ingested_enabled(window) -> bool:
    from image_triage.filtering import FilterMode

    return window.actions.filter_actions[FilterMode.AI_INGESTED].isEnabled()
