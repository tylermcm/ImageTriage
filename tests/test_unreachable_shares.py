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
from image_triage.window import MainWindow
from tests.harness import make_jpegs, pump_until

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


def _open_window(share, tmp_path, *, last_folder: str = "", favorites=None, recents=None, destinations=None):
    from tests.test_preview_lazy_build import _fresh_window

    local = tmp_path / "local"
    local_b = tmp_path / "local_b"
    local.mkdir(exist_ok=True)
    local_b.mkdir(exist_ok=True)

    def seed(settings) -> None:
        settings.setValue(MainWindow.FAVORITES_KEY, favorites if favorites is not None else [str(local), NAS_FOLDER])
        settings.setValue(MainWindow.RECENT_FOLDERS_KEY, recents if recents is not None else [str(local), NAS_FOLDER, NAS_OTHER])
        settings.setValue(MainWindow.RECENT_DESTINATIONS_KEY, destinations if destinations is not None else [str(local_b), NAS_DEST])
        if last_folder:
            settings.setValue(MainWindow.LAST_FOLDER_KEY, last_folder)

    return _fresh_window(seed=seed), local, local_b


def _saved(key: str) -> list[str]:
    return _as_list(user_settings().value(key, [], list))


# --------------------------------------------------------------------------- nothing is lost
def test_saved_lists_keep_unreachable_entries_at_startup(dialogs, share, tmp_path) -> None:
    manager, local, local_b = _open_window(share, tmp_path)
    with manager as window:
        assert window._favorites == [str(local), NAS_FOLDER]
        assert window._recent_folders == [str(local), NAS_FOLDER, NAS_OTHER]
        assert window._recent_destinations == [str(local_b), NAS_DEST]


def test_opening_a_folder_does_not_erase_unreachable_recents_from_settings(dialogs, share, tmp_path) -> None:
    manager, local, local_b = _open_window(share, tmp_path)
    with manager as window:
        window._remember_recent_folder(str(local_b))  # what opening any folder does
        saved = _saved(MainWindow.RECENT_FOLDERS_KEY)
        assert NAS_FOLDER in saved and NAS_OTHER in saved and str(local_b) in saved


def test_adding_a_favorite_does_not_erase_unreachable_favorites(dialogs, share, tmp_path) -> None:
    manager, local, local_b = _open_window(share, tmp_path)
    with manager as window:
        window._add_favorite(str(local_b))
        assert _saved(MainWindow.FAVORITES_KEY) == [str(local), NAS_FOLDER, str(local_b)]


def test_the_move_to_menu_lists_unreachable_destinations_and_keeps_them_saved(dialogs, share, tmp_path) -> None:
    manager, local, local_b = _open_window(share, tmp_path)
    with manager as window:
        listed = window._recent_destination_paths()
        assert NAS_DEST in listed and str(local_b) in listed
        assert NAS_DEST in _saved(MainWindow.RECENT_DESTINATIONS_KEY)


def test_building_the_move_to_menu_in_a_folder_does_not_erase_that_folder_from_the_saved_list(dialogs, share, tmp_path) -> None:
    """Same function, same flaw: filtering the *view* (hide the current folder) was written back to settings."""
    manager, local, local_b = _open_window(share, tmp_path)
    with manager as window:
        window._current_folder = str(local_b)
        shown = window._recent_destination_paths(exclude_current_folder=True)
        assert str(local_b) not in shown, "the current folder is still hidden from the menu"
        assert str(local_b) in _saved(MainWindow.RECENT_DESTINATIONS_KEY), "but it must stay in the saved list"


def test_clicking_an_unreachable_recent_folder_keeps_it_and_reports_the_failure(dialogs, share, tmp_path) -> None:
    manager, local, local_b = _open_window(share, tmp_path)
    with manager as window:
        window._open_recent_folder(NAS_FOLDER)
        assert pump_until(lambda: window._catalog_load_source == "failed", timeout=15), "the scan worker should report it"
        assert NAS_FOLDER in window._recent_folders and NAS_FOLDER in _saved(MainWindow.RECENT_FOLDERS_KEY)
        assert "Could not scan" in window.statusBar().currentMessage()


def test_a_local_folder_that_is_really_gone_is_still_pruned(dialogs, share, tmp_path) -> None:
    gone = str(tmp_path / "deleted_long_ago")
    manager, local, local_b = _open_window(share, tmp_path, favorites=[str(tmp_path / "local"), gone, NAS_FOLDER])
    with manager as window:
        assert window._favorites == [str(local), NAS_FOLDER]


def test_renaming_a_local_folder_leaves_unreachable_entries_alone(dialogs, share, tmp_path) -> None:
    manager, local, local_b = _open_window(share, tmp_path)
    with manager as window:
        renamed = tmp_path / "local_renamed"
        local.rename(renamed)
        window._folder_ops.remap_folder_references(str(local), str(renamed))
        assert _saved(MainWindow.FAVORITES_KEY) == [str(renamed), NAS_FOLDER]
        assert _saved(MainWindow.RECENT_FOLDERS_KEY) == [str(renamed), NAS_FOLDER, NAS_OTHER]
        assert _saved(MainWindow.RECENT_DESTINATIONS_KEY) == [str(local_b), NAS_DEST]


def test_renaming_or_deleting_a_local_folder_does_not_ask_the_share_to_compare_its_saved_entries(dialogs, share, tmp_path) -> None:
    """The favorites / recents / destinations loops compared paths with ``normalized_path_key``, which resolves
    them through the filesystem; for a share that is a network round trip per saved entry. The key is memoized,
    so the memo is emptied first: an entry some earlier step already resolved would hide the ask."""
    from image_triage.scanner import _normalize_filesystem_path_cached

    manager, local, local_b = _open_window(share, tmp_path)
    with manager as window:
        doomed = tmp_path / "doomed"
        doomed.mkdir()
        renamed = tmp_path / "local_renamed"
        local.rename(renamed)
        _normalize_filesystem_path_cached.cache_clear()
        window._folder_ops.remap_folder_references(str(local), str(renamed))
        assert share.gui_checks == [], "rename asked the share:\n" + "\n".join(share.gui_stacks)
        _normalize_filesystem_path_cached.cache_clear()
        window._folder_ops.delete_folder_prompt(str(doomed))
        assert share.gui_checks == [], "delete asked the share:\n" + "\n".join(share.gui_stacks)
        assert not doomed.exists()
        assert NAS_FOLDER in _saved(MainWindow.FAVORITES_KEY) and NAS_DEST in _saved(MainWindow.RECENT_DESTINATIONS_KEY)


# --------------------------------------------------------------------------- nothing blocks the GUI thread
def test_no_network_path_is_checked_on_the_gui_thread(dialogs, share, tmp_path) -> None:
    manager, local, local_b = _open_window(share, tmp_path, last_folder=NAS_FOLDER)
    with manager as window:
        window._load_start_folder()
        window._refresh_recent_folder_combos()
        window._recent_destination_paths(exclude_current_folder=True)
        window._refresh_favorites_panel()
        window._remember_recent_destination(NAS_DEST)
        window._add_favorite(NAS_OTHER)
        window._open_recent_folder(NAS_FOLDER)
        window._handle_path_suggestion_accepted(NAS_OTHER)
        window._commit_path_combo_text(window.topbar_path_combo)
        pump_until(lambda: window._catalog_load_source == "failed", timeout=15)
        assert share.gui_checks == [], "the GUI thread asked the share:\n" + "\n".join(share.gui_stacks)


def test_the_start_folder_on_an_unreachable_share_is_opened_by_the_worker_not_checked_by_the_gui(dialogs, share, tmp_path) -> None:
    manager, local, local_b = _open_window(share, tmp_path, last_folder=NAS_FOLDER)
    with manager as window:
        window._load_start_folder()
        assert pump_until(lambda: window._catalog_load_source == "failed", timeout=15)
        assert share.gui_checks == [], "\n".join(share.gui_stacks)
        assert NAS_FOLDER in share.worker_checks, "the scan worker is what asked the share"
        assert _saved_last_folder() == NAS_FOLDER, "and the saved last folder is kept for the next start"


def _saved_last_folder() -> str:
    return str(user_settings().value(MainWindow.LAST_FOLDER_KEY, "", str))


def test_a_slow_source_start_folder_still_opens_normally_when_it_is_reachable(dialogs, share, tmp_path) -> None:
    folder = tmp_path / "card_dump"
    make_jpegs(folder, [f"IMG_{index}.jpg" for index in range(3)])
    manager, local, local_b = _open_window(share, tmp_path, last_folder=str(folder))
    with manager as window:
        window._is_slow_source_folder = lambda _folder=None: True  # behave as a network / removable drive
        window._load_start_folder()
        assert pump_until(lambda: len(window._records) == 3, timeout=30)
        assert window._current_folder == str(folder)


# --------------------------------------------------------------------------- the library index
def _library(tmp_path, monkeypatch):
    monkeypatch.setenv("IMAGE_TRIAGE_APPDATA", str(tmp_path / "appdata"))
    return LibraryStore()


def _indexed_root(store: LibraryStore, root: Path):
    make_jpegs(root, ["a.jpg", "b.jpg"])
    store.add_catalog_root(str(root))
    summary = store.refresh_catalog((str(root),))
    assert summary.record_count == 2
    return next(item for item in store.list_catalog_roots())


def test_refreshing_the_library_keeps_the_index_of_a_root_whose_drive_is_unreachable(tmp_path, monkeypatch) -> None:
    store = _library(tmp_path, monkeypatch)
    root = tmp_path / "nas_library"
    _indexed_root(store, root)
    real_isdir = os.path.isdir
    anchor = Path(str(root)).anchor
    # The whole drive stops answering: the root and the drive root itself both read as "not a directory".
    monkeypatch.setattr(os.path, "isdir", lambda p: False if str(p) == anchor or str(p).startswith(str(root)) else real_isdir(p))
    summary = store.refresh_catalog((str(root),))
    monkeypatch.setattr(os.path, "isdir", real_isdir)
    kept = next(item for item in store.list_catalog_roots())
    assert kept.indexed_record_count == 2, "the index must survive the share being unreachable"
    assert "reach" in kept.last_error.lower()
    assert summary.missing_roots == () and len(summary.unreachable_roots) == 1


def test_refreshing_the_library_still_clears_a_folder_that_was_really_deleted(tmp_path, monkeypatch) -> None:
    store = _library(tmp_path, monkeypatch)
    root = tmp_path / "old_library"
    _indexed_root(store, root)
    shutil.rmtree(root)
    summary = store.refresh_catalog((str(root),))
    cleared = next(item for item in store.list_catalog_roots())
    assert cleared.indexed_record_count == 0 and cleared.last_error == "Folder not found."
    assert len(summary.missing_roots) == 1 and summary.unreachable_roots == ()


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
def _suggestion_controller():
    from PySide6.QtWidgets import QComboBox

    from image_triage.window import _DirectorySuggestionController

    QApplication.instance() or QApplication([])
    combo = QComboBox()
    combo.setEditable(True)
    return combo, _DirectorySuggestionController(combo)


def test_suggestions_for_a_share_are_listed_off_the_gui_thread(monkeypatch) -> None:
    from image_triage.window import _DirectorySuggestionController

    combo, controller = _suggestion_controller()
    calls: list[tuple[bool, str]] = []

    def fake_list(text):
        calls.append((threading.current_thread() is threading.main_thread(), text))
        return [("2026", NAS + "\\2026")]

    monkeypatch.setattr(_DirectorySuggestionController, "_list_directory_suggestions", staticmethod(fake_list))
    text = NAS + "\\"
    combo.lineEdit().setText(text)
    controller._show_suggestions_for_text(text)
    assert pump_until(lambda: controller._list.count() == 1, timeout=10)
    assert calls == [(False, text)], "the share must be listed by the worker, not the GUI thread"


def test_only_the_newest_share_suggestions_are_shown(monkeypatch) -> None:
    from image_triage.window import _DirectorySuggestionController

    combo, controller = _suggestion_controller()
    gate = threading.Event()
    listed: list[str] = []

    def fake_list(text):
        listed.append(text)
        if text.endswith("old\\"):
            gate.wait(timeout=10)  # the user has typed on by the time this one answers
        return [(text.rstrip("\\").rsplit("\\", 1)[-1], text)]

    monkeypatch.setattr(_DirectorySuggestionController, "_list_directory_suggestions", staticmethod(fake_list))
    old_text, new_text = NAS + "\\old\\", NAS + "\\new\\"
    combo.lineEdit().setText(old_text)
    controller._show_suggestions_for_text(old_text)
    assert pump_until(lambda: listed == [old_text], timeout=10)
    combo.lineEdit().setText(new_text)
    controller._show_suggestions_for_text(new_text)
    gate.set()
    assert pump_until(lambda: controller._list.count() == 1, timeout=10)
    assert controller._list.item(0).text() == "new", "the older answer must be dropped"
    assert pump_until(lambda: not controller._suggestion_tasks, timeout=10), "finished tasks are released"


def test_suggestions_for_a_local_folder_are_still_immediate(tmp_path) -> None:
    (tmp_path / "Alpha").mkdir()
    combo, controller = _suggestion_controller()
    text = str(tmp_path) + os.sep
    combo.lineEdit().setText(text)
    controller._show_suggestions_for_text(text)
    assert controller._list.count() == 1 and controller._list.item(0).text() == "Alpha"  # no pump needed


def test_a_launch_target_on_a_share_is_not_asked_of_the_share_on_the_gui_thread(dialogs, share, tmp_path) -> None:
    manager, local, local_b = _open_window(share, tmp_path)
    with manager as window:
        opened: list[tuple[str, str | None]] = []
        window._select_folder = lambda folder, **kwargs: opened.append((folder, kwargs.get("preferred_record_path")))
        image = NAS_FOLDER + "\\IMG_0001.jpg"
        assert window._open_launch_target(image) is True  # "Open with Image Triage" on a photo on the NAS
        assert window._open_launch_target(NAS_FOLDER) is True
        # (the expectation is built with os.path.normpath: the resolving normalizer would itself ask the share)
        assert opened == [(NAS_FOLDER, os.path.normpath(image)), (os.path.normpath(NAS_FOLDER), None)]
        assert share.gui_checks == [], "\n".join(share.gui_stacks)


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


def test_opening_a_folder_on_a_share_does_not_ask_the_tree_model_about_it_on_the_gui_thread(dialogs, share, tmp_path, monkeypatch) -> None:
    """_sync_drive_sections runs on every folder open; index() on a sleeping share froze the window ~20 s."""
    manager, local, local_b = _open_window(share, tmp_path)
    with manager as window:
        model_calls = _ModelIndexCalls(monkeypatch)
        window._scope_kind = "folder"
        window._current_folder = NAS_FOLDER
        window._sync_drive_sections()
        assert [path for path in model_calls.gui_paths() if _is_nas(path)] == []
        assert pump_until(lambda: NAS in share.worker_checks or NAS + "\\" in share.worker_checks, timeout=10), "a worker asks the share instead"
        pump_until(lambda: False, timeout=0.3)
        assert [path for path in model_calls.gui_paths() if _is_nas(path)] == [], "an unreachable share is never handed to the model"
        assert share.gui_checks == [], "\n".join(share.gui_stacks)


def test_a_reachable_share_is_still_rooted_in_the_tree_once_a_worker_has_seen_it_answer(dialogs, share, tmp_path, monkeypatch) -> None:
    folder = tmp_path / "card_dump"
    folder.mkdir()
    manager, local, local_b = _open_window(share, tmp_path)
    with manager as window:
        model_calls = _ModelIndexCalls(monkeypatch)
        window._is_slow_source_folder = lambda _folder=None: True  # a network drive that does answer
        window._scope_kind = "folder"
        window._current_folder = str(folder)
        window._sync_drive_sections()
        assert model_calls.gui_paths() == [], "nothing is asked of the model before the worker has answered"
        assert pump_until(lambda: str(folder) in model_calls.gui_paths(), timeout=10), "then the tree is synced as before"


def test_only_the_newest_drive_check_is_applied(dialogs, share, tmp_path, monkeypatch) -> None:
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    manager, local, local_b = _open_window(share, tmp_path)
    with manager as window:
        model_calls = _ModelIndexCalls(monkeypatch)
        window._is_slow_source_folder = lambda _folder=None: True
        window._scope_kind = "folder"
        window._current_folder = str(first)
        window._sync_drive_sections()
        window._current_folder = str(second)  # the user opened another folder before the answer came back
        window._sync_drive_sections()
        assert pump_until(lambda: str(second) in model_calls.gui_paths(), timeout=10)
        assert str(first) not in model_calls.gui_paths(), "the older answer must be dropped"
        assert pump_until(lambda: not window._drive_sync_tasks, timeout=10), "finished checks are released"


def test_rebuilding_the_folder_tree_never_hands_a_share_to_the_model_on_the_gui_thread(dialogs, share, tmp_path, monkeypatch) -> None:
    manager, local, local_b = _open_window(share, tmp_path)
    with manager as window:
        model_calls = _ModelIndexCalls(monkeypatch)
        window._scope_kind = "folder"
        window._current_folder = NAS_FOLDER
        window._refresh_folder_tree()  # what every folder create / rename / move / delete does
        pump_until(lambda: False, timeout=0.3)
        assert [path for path in model_calls.gui_paths() if _is_nas(path)] == []
        assert share.gui_checks == [], "\n".join(share.gui_stacks)


def test_the_tree_rebuild_does_not_put_back_a_selection_that_lives_on_a_share(dialogs, share, tmp_path, monkeypatch) -> None:
    """The tree remembers its root / selection / expanded branches across a rebuild. Putting a share's
    paths back means index() on it, so those are skipped; local ones still are put back."""
    manager, local, local_b = _open_window(share, tmp_path)
    with manager as window:
        assert pump_until(lambda: window.folder_model.index(str(tmp_path)).isValid(), timeout=15)
        local_index = window.folder_model.index(str(tmp_path))
        window.folder_tree.setCurrentIndex(local_index)
        window.drive_list.setCurrentIndex(window.folder_model.index(str(tmp_path.anchor)))
        # The old model reports the remembered selection as a share path; the real tree state is untouched.
        monkeypatch.setattr(window.folder_model, "filePath", lambda _index: NAS_FOLDER)
        model_calls = _ModelIndexCalls(monkeypatch)
        window._scope_kind = "folder"
        window._current_folder = str(tmp_path)
        window._refresh_folder_tree()
        assert [path for path in model_calls.gui_paths() if _is_nas(path)] == [], "the share's saved selection must not reach the model"


def test_the_tree_rebuild_still_puts_back_a_local_selection(dialogs, share, tmp_path, monkeypatch) -> None:
    manager, local, local_b = _open_window(share, tmp_path)
    with manager as window:
        assert pump_until(lambda: window.folder_model.index(str(tmp_path)).isValid(), timeout=15)
        window.folder_tree.setCurrentIndex(window.folder_model.index(str(tmp_path)))
        original = window.folder_model.filePath
        monkeypatch.setattr(window.folder_model, "filePath", lambda index: str(tmp_path) if index.isValid() else original(index))
        model_calls = _ModelIndexCalls(monkeypatch)
        window._scope_kind = "folder"
        window._current_folder = str(tmp_path)
        window._refresh_folder_tree()
        assert str(tmp_path) in model_calls.gui_paths(), "a plain local selection is restored as before"


def _fake_ai_probe(monkeypatch, *, gate: threading.Event | None = None):
    """Replace the per-folder AI probe (SQLite opens + artifact stats) and record which thread ran it."""
    import image_triage.window as window_module

    calls: list[tuple[bool, str]] = []

    def fake(ai_paths, folder):
        calls.append((threading.current_thread() is threading.main_thread(), folder))
        if gate is not None and len(calls) == 1:
            gate.wait(timeout=10)
        probe = window_module._unknown_ai_folder_probe(folder)
        probe["aiculler_available"] = True
        return probe

    monkeypatch.setattr(window_module, "_compute_ai_folder_probe", fake)
    return calls


def _ingested_enabled(window) -> bool:
    from image_triage.filtering import FilterMode

    return window.actions.filter_actions[FilterMode.AI_INGESTED].isEnabled()


def test_the_ai_probe_of_a_folder_on_a_share_runs_on_a_worker_and_fills_in_later(dialogs, share, tmp_path, monkeypatch) -> None:
    calls = _fake_ai_probe(monkeypatch)
    manager, local, local_b = _open_window(share, tmp_path)
    with manager as window:
        window._current_folder = NAS_FOLDER
        window._invalidate_ai_folder_probe_cache()
        calls.clear()
        window._update_ai_toolbar_state()
        assert [on_gui for on_gui, _folder in calls] == [] or not any(on_gui for on_gui, _folder in calls), "never on the GUI thread"
        assert _ingested_enabled(window) is False, "until the worker answers the toolbar says nothing was found"
        assert pump_until(lambda: _ingested_enabled(window), timeout=10), "the arriving answer refreshes the toolbar"
        assert calls == [(False, NAS_FOLDER)]


def test_the_ai_probe_of_a_local_folder_is_still_immediate(dialogs, share, tmp_path, monkeypatch) -> None:
    calls = _fake_ai_probe(monkeypatch)
    manager, local, local_b = _open_window(share, tmp_path)
    with manager as window:
        window._current_folder = str(local)
        window._invalidate_ai_folder_probe_cache()
        calls.clear()
        window._update_ai_toolbar_state()
        assert calls == [(True, str(local))] and _ingested_enabled(window) is True


def test_an_ai_probe_answer_that_went_stale_is_dropped_and_asked_again(dialogs, share, tmp_path, monkeypatch) -> None:
    gate = threading.Event()
    calls = _fake_ai_probe(monkeypatch, gate=gate)
    manager, local, local_b = _open_window(share, tmp_path)
    with manager as window:
        window._current_folder = NAS_FOLDER
        window._invalidate_ai_folder_probe_cache()
        calls.clear()
        window._update_ai_toolbar_state()
        assert pump_until(lambda: len(calls) == 1, timeout=10)
        window._invalidate_ai_folder_probe_cache()  # an AI run changed the folder's data while the worker looked
        gate.set()
        assert pump_until(lambda: len(calls) == 2, timeout=10), "the stale answer is dropped and the folder is probed again"
        assert pump_until(lambda: _ingested_enabled(window), timeout=10)


def test_an_ai_probe_started_for_the_previous_folder_does_not_leave_the_new_one_unprobed(dialogs, share, tmp_path, monkeypatch) -> None:
    gate = threading.Event()
    calls = _fake_ai_probe(monkeypatch, gate=gate)
    manager, local, local_b = _open_window(share, tmp_path)
    with manager as window:
        window._current_folder = NAS_FOLDER
        window._invalidate_ai_folder_probe_cache()
        calls.clear()
        window._update_ai_toolbar_state()
        assert pump_until(lambda: len(calls) == 1, timeout=10)
        window._current_folder = NAS_OTHER  # the user opened another folder on the NAS
        window._update_ai_toolbar_state()
        gate.set()
        assert pump_until(lambda: [folder for _gui, folder in calls] == [NAS_FOLDER, NAS_OTHER], timeout=10)
        assert pump_until(lambda: (window._ai_folder_probe_cache or {}).get("folder") == NAS_OTHER, timeout=10)


def test_the_ai_folder_paths_of_a_share_are_built_without_asking_the_share(dialogs, share, tmp_path) -> None:
    """Path.resolve() is a filesystem call: these builders run on every winner / reject mark (telemetry)."""
    manager, local, local_b = _open_window(share, tmp_path)
    with manager as window:
        window._current_folder = NAS_FOLDER
        hidden = window._hidden_ai_paths_for_current_folder()
        culler = window._aiculler_paths_for_current_folder()
        assert str(hidden.folder) == NAS_FOLDER and str(culler.hidden_root).startswith(NAS_FOLDER)
        assert share.gui_resolves == []


def test_the_local_ai_folder_paths_are_still_resolved(dialogs, share, tmp_path) -> None:
    manager, local, local_b = _open_window(share, tmp_path)
    with manager as window:
        window._current_folder = str(local)
        assert window._hidden_ai_paths_for_current_folder().folder == local.resolve()


def test_marking_a_photo_in_a_share_folder_does_not_ask_the_share_for_the_telemetry_log(dialogs, share, tmp_path, monkeypatch) -> None:
    import image_triage.window as window_module

    class _StubLogger:
        def __init__(self, db_path) -> None:
            self.db_path = db_path

        def log_event(self, _event) -> None:
            pass

        def shutdown(self, *args, **kwargs) -> None:
            pass

    monkeypatch.setattr(window_module, "ThreadedTelemetryLogger", _StubLogger)  # no real thread opening a NAS database
    manager, local, local_b = _open_window(share, tmp_path)
    with manager as window:
        window._current_folder = NAS_FOLDER
        assert window._aiculler_telemetry_logger_for_current_folder() is not None
        assert share.gui_resolves == []


def test_the_ai_toolbar_refresh_on_a_share_does_not_resolve_the_folder_on_the_gui_thread(dialogs, share, tmp_path) -> None:
    manager, local, local_b = _open_window(share, tmp_path)
    with manager as window:
        window._current_folder = NAS_FOLDER
        window._invalidate_ai_folder_probe_cache()
        window._update_ai_toolbar_state()  # must not raise: the share is down and resolve() would fail
        pump_until(lambda: False, timeout=0.5)
        assert share.gui_resolves == []


def _fake_prefilter_loader(monkeypatch):
    """Replace the prefilter decision loader and its path builder, recording which thread ran the loader."""
    import image_triage.window as window_module

    calls: list[tuple[bool, str]] = []

    def build_paths(folder):
        return SimpleNamespace(folder=str(folder))

    def load(paths):
        calls.append((threading.current_thread() is threading.main_thread(), paths.folder))
        return {paths.folder + "\\IMG_0001.jpg": SimpleNamespace(action="keep", reason="test")}

    monkeypatch.setattr(window_module, "build_phash_prefilter_paths", build_paths)
    monkeypatch.setattr(window_module, "load_phash_prefilter_decisions", load)
    return calls


def test_a_share_folders_prefilter_decisions_are_loaded_by_a_worker_and_pushed_to_the_grid(dialogs, share, tmp_path, monkeypatch) -> None:
    """Runs every time the records view is finalized, and used to read the rows file over the share."""
    calls = _fake_prefilter_loader(monkeypatch)
    manager, local, local_b = _open_window(share, tmp_path)
    with manager as window:
        window._current_folder = NAS_FOLDER
        window._refresh_prefilter_decisions_for_current_folder()
        assert [on_gui for on_gui, _folder in calls if on_gui] == [], "never on the GUI thread"
        assert pump_until(lambda: bool(window._prefilter_decisions_by_path), timeout=10), "the answer arrives later"
        assert calls == [(False, NAS_FOLDER)]
        assert window.grid._prefilter_decisions_by_path, "and is pushed to the grid"
        assert share.gui_resolves == []


def test_the_prefilter_decisions_of_a_share_are_not_reloaded_on_every_view_refresh(dialogs, share, tmp_path, monkeypatch) -> None:
    calls = _fake_prefilter_loader(monkeypatch)
    manager, local, local_b = _open_window(share, tmp_path)
    with manager as window:
        window._current_folder = NAS_FOLDER
        for _ in range(5):  # five view refreshes in a row
            window._refresh_prefilter_decisions_for_current_folder()
        assert pump_until(lambda: bool(window._prefilter_decisions_by_path), timeout=10)
        window._refresh_prefilter_decisions_for_current_folder()
        assert len(calls) == 1, "one load per folder per minute, not one per refresh"


def test_the_prefilter_decisions_of_a_local_folder_are_still_loaded_immediately(dialogs, share, tmp_path, monkeypatch) -> None:
    calls = _fake_prefilter_loader(monkeypatch)
    manager, local, local_b = _open_window(share, tmp_path)
    with manager as window:
        window._current_folder = str(local)
        window._refresh_prefilter_decisions_for_current_folder()
        assert calls == [(True, str(local))] and window._prefilter_decisions_by_path


def test_the_drag_over_check_never_asks_the_share(dialogs, share, tmp_path) -> None:
    """It runs on every mouse-move while dragging photos over the folder tree."""
    manager, local, local_b = _open_window(share, tmp_path)
    with manager as window:
        window._current_folder = str(local)
        assert window._can_accept_record_drop(NAS_FOLDER) is True
        assert window._can_accept_record_drop(str(tmp_path / "gone")) is False
        assert share.gui_checks == [], "\n".join(share.gui_stacks)
