"""The Library page of the side panel: favorites, drives and the folder tree, and opening a folder from them.

Everything comes from what the Widgets app uses, so both front ends agree:

* favorites and recent folders are the Widgets settings keys `folders/favorites` and `folders/recent_opened`
  (`navigation_controller.add_favorite`, `remember_recent_folder`);
* drives are `drive_list_model.DriveListModel`, which never asks a drive anything on the GUI thread (one dead network drive froze the
  Widgets window for 20 s before it);
* the folder tree is the folders under the open folder's drive (`scanner.scan_child_folders`, hidden ones per `view/show_hidden_folders`),
  listed on a worker thread one level at a time as they are opened, so a share that is asleep cannot hold the window up.

`memory=True` (`--mock` and the tests) shows the mockup's sample favorites, drives and folders and touches no setting.
"""
from __future__ import annotations

import json
from collections import deque
import os
import shutil
import threading
import time
from pathlib import Path

from PySide6.QtCore import Property, QAbstractListModel, QByteArray, QFileSystemWatcher, QModelIndex, QObject, Qt, QTimer, QUrl, Signal, Slot

from .. import path_policy
from ..app_identity import user_settings

FAVORITES_KEY = "folders/favorites"
RECENT_KEY = "folders/recent_opened"
SHOW_HIDDEN_KEY = "view/show_hidden_folders"
SINGLE_BRANCH_KEY = "view/single_drive_expansion"
WATCH_KEY = "catalog/watch_current_folder"
WATCH_DELAY_MS = 900                 # the Widgets app waits this long after a change, so a copy of many files is read once
RECENT_LIMIT = 12
LAST_FOLDER_KEY = "window/last_folder"                  # the Widgets keys: both front ends reopen the same folder, on the same photo
RESTORE_POSITION_KEY = "view/restore_folder_position"
FOLDER_STATE_KEY = "view/folder_state"                  # JSON, folder key -> {"current": the photo the person was on, ...}; the Widgets app keeps more in it
STATE_LIMIT = 120
INDENT = 18


def _key(path: str) -> str:
    return os.path.normpath(path).casefold()                 # what the Widgets controllers use to compare folders in memory


def _count_text(count: int | None) -> str:
    return "" if count is None else f"{count:,}"


def _size_text(size: float) -> str:
    """The way Windows writes a drive's size: powers of 1024, one decimal only for a part of a terabyte."""
    tb = size / 1024 ** 4
    if tb >= 1:
        text = f"{tb:.1f}".rstrip("0").rstrip(".")
        return f"{text} TB"
    gb = size / 1024 ** 3
    if gb >= 1:
        return f"{round(gb)} GB"
    return f"{max(0, round(size / 1024 ** 2))} MB"


# The mockup's Library page, for --mock and the tests.
_SAMPLE_FAVORITES = (("Wedding 2026", "1,284"), ("Client selects", "212"))
_SAMPLE_DRIVES = (("Photos (D:)", "612 GB free of 2 TB", 0.70), ("Cards (E:)", "41 GB free of 256 GB", 0.84))


def _listing(folder: str):
    """What a refresh of `folder` could change: the names in it, without the files the app writes there itself.

    The watcher fires for any new entry, and the app adds entries of its own while the person works (an XMP sidecar on
    every decision, the hidden AI and edit folders). Reading the folder again for those resets the grid under them
    and used to stop a running analysis. None when the folder cannot be listed.
    """
    try:
        with os.scandir(folder) as entries:
            return frozenset(e.name for e in entries if not e.name.startswith(".") and not e.name.lower().endswith((".xmp", ".tmp")))
    except OSError:
        return None


def _same_folder_spelling(photos, photo: str) -> bool:
    """Do the open folder's photos carry the folder as `photo` spells it? (Then a photo that is not found by its path is not there.)"""
    rows = photos.all_rows()
    return not rows or _key(os.path.dirname(rows[0]["path"])) == _key(os.path.dirname(photo))


class _RowsModel(QAbstractListModel):
    """A list of dicts for a QML Repeater. `set_rows` inserts, removes or updates only the rows that differ, so a listing that arrives
    does not rebuild the rows around it (which would also make the panel's scroll position jump)."""
    countChanged = Signal()

    def __init__(self, roles: dict[str, str], parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._roles = {Qt.ItemDataRole.UserRole + 1 + n: (role, key) for n, (role, key) in enumerate(roles.items())}   # role id -> (QML name, row key)
        self._rows: list[dict] = []

    def roleNames(self) -> dict:
        return {role_id: QByteArray(name.encode()) for role_id, (name, _key_name) in self._roles.items()}

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._rows)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self._rows) or role not in self._roles:
            return None
        return self._rows[index.row()].get(self._roles[role][1])

    count = Property(int, lambda self: len(self._rows), notify=countChanged)

    def set_rows(self, rows: list[dict]) -> None:
        old = self._rows
        if rows == old:
            return
        shared = min(len(old), len(rows))
        head = 0
        while head < shared and old[head] == rows[head]:
            head += 1
        tail = 0
        while tail < shared - head and old[-1 - tail] == rows[-1 - tail]:
            tail += 1
        old_mid, new_mid = old[head:len(old) - tail], rows[head:len(rows) - tail]
        if len(old_mid) == len(new_mid) and all(a["path"] == b["path"] for a, b in zip(old_mid, new_mid)):
            self._rows = rows                                   # the same rows with other values (a count, the highlight)
            self.dataChanged.emit(self.index(head), self.index(head + len(old_mid) - 1))
            return
        if old_mid:
            self.beginRemoveRows(QModelIndex(), head, head + len(old_mid) - 1)
            self._rows = old[:head] + old[len(old) - tail:]
            self.endRemoveRows()
        if new_mid:
            self.beginInsertRows(QModelIndex(), head, head + len(new_mid) - 1)
            self._rows = rows
            self.endInsertRows()
        else:
            self._rows = rows
        if len(rows) != len(old):
            self.countChanged.emit()


class LibraryNavModel(QObject):
    changed = Signal()
    positionRestored = Signal(int)                             # the grid should be on this row: the photo the person was on when they left this folder
    _listed = Signal(str, list)                                # a folder's child folders, from the worker thread
    _searched = Signal(int, object)

    def __init__(self, photos, *, memory: bool = False, persist: bool = True, drives=None, drive_root=None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._photos = photos
        self._memory = memory
        self._persist = persist and not memory                 # headless checks (--grab) and the sample page never write the person's settings
        self._pending_current = ""                             # the saved photo of the folder just opened, until the grid has it
        self.saved: tuple[str, str] | None = None              # (folder, photo) of the last position saved in memory mode, for the tests
        self._drive_root = drive_root or path_policy.drive_root         # the drive (or share) a folder is on; a test hands in its own
        self._settings = None if memory else user_settings(self)
        self._mem_favorites: list[str] = []
        self._open = ""                                        # the folder the grid shows
        self._back: list[str] = []                             # the folders opened before this one, newest last (the header's Back)
        self._forward: list[str] = []                          # the folders Back stepped away from (Forward)
        self._stepping = False                                 # Back, Forward or Up is opening a folder: that is not a new step in the history
        self._root = ""                                        # the drive (or share) the tree is rooted at: the open folder's
        self._expanded: dict[str, str] = {}                   # the folders the tree has open: key -> path
        self._children: dict[str, list[tuple[str, str]]] = {}  # key -> [(name, path)]
        self._requested: set[str] = set()
        self._counts: dict[str, int] = {}                      # photos in a folder, known once it has been opened
        self._sizes: dict[str, tuple[int, int]] = {}           # drive -> (free, total) bytes, as the last probe saw them
        self._query = ""
        self._search_token = 0
        self._search_cancel = threading.Event()
        self._search_expanded: set[str] = set()
        self._direct_matches: list[str] | None = None
        self._search_pending = False
        self._search_limited = False
        self._open_query_when_ready = False
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(200)
        self._search_timer.timeout.connect(self._search_folders)
        self._searched.connect(self._apply_search)
        self._last_photo = ""                                  # the photo the grid was last on, for reading the folder again where the person is
        self._watcher: QFileSystemWatcher | None = None
        self._watch_timer: QTimer | None = None
        if not memory:
            self._watcher = QFileSystemWatcher(self)
            self._watch_seen = None
            self._watch_timer = QTimer(self)
            self._watch_timer.setSingleShot(True)
            self._watch_timer.setInterval(WATCH_DELAY_MS)
            self._watcher.directoryChanged.connect(lambda _path: self._watch_timer.start())
            self._watch_timer.timeout.connect(self._watched_change)
        self._listed.connect(self._apply_listed)
        # what the page's Repeaters show (role names are not PanelRow's own property names: icon, text, count, current)
        self._fav_rows = _RowsModel({"folder": "path", "title": "name", "photos": "count", "here": "current"}, self)
        self._drv_rows = _RowsModel({"folder": "path", "title": "name", "detail": "detail", "ratio": "ratio"}, self)
        self._dir_rows = _RowsModel({"folder": "path", "title": "name", "photos": "count", "depth": "depth", "glyph": "icon", "here": "current", "canOpen": "expandable"}, self)
        self.changed.connect(self._publish)
        photos.loadingChanged.connect(self._note_count)
        if memory:                                              # the sample page names the open folder and its count: keep them current
            photos.countsChanged.connect(self.changed.emit)
            photos.folderChanged.connect(self.changed.emit)
        self._drive_model = drives
        if drives is None and not memory:
            from ..drive_list_model import DriveListModel, storage_numbers

            def probe(path: str):
                answer = storage_numbers(path)
                if answer[0]:
                    try:
                        usage = shutil.disk_usage(path.replace("/", "\\") if os.name == "nt" else path)
                        self._sizes[_key(path)] = (usage.free, usage.total)
                    except OSError:
                        pass
                return answer

            self._drive_model = DriveListModel(self, probe=probe)
        if self._drive_model is not None:
            for signal in (self._drive_model.modelReset, self._drive_model.rowsInserted, self._drive_model.rowsRemoved, self._drive_model.dataChanged):
                signal.connect(lambda *_: self.changed.emit())
            if drives is None:
                self._drive_model.refresh()
        self._publish()

    # -- favorites ---------------------------------------------------------------------------------------------------------
    def _favorite_paths(self) -> list[str]:
        if self._settings is None:
            return list(self._mem_favorites)
        raw = self._settings.value(FAVORITES_KEY, [], list)
        if isinstance(raw, str):
            raw = [raw]
        found: list[str] = []
        for path in raw or []:
            # only a folder provably gone from a local drive is dropped; a share that is asleep stays (as the Widgets list)
            if isinstance(path, str) and path and path not in found and not path_policy.confirmed_missing(path):
                found.append(path)
        return found

    def _save_favorites(self, paths: list[str]) -> None:
        if self._settings is None:
            self._mem_favorites = paths
        else:
            self._settings.setValue(FAVORITES_KEY, paths)
        self.changed.emit()

    @Slot(str)
    def addFavorite(self, path: str) -> None:
        paths = self._favorite_paths()
        if path and path not in paths:
            self._save_favorites(paths + [path])

    @Slot(str)
    def removeFavorite(self, path: str) -> None:
        paths = self._favorite_paths()
        if path in paths:
            self._save_favorites([p for p in paths if p != path])

    @Slot(str, result=bool)
    def isFavorite(self, path: str) -> bool:
        return path in self._favorite_paths()

    def _favorites_view(self) -> list[dict]:
        if self._memory:
            return [{"path": f"sample:{name}", "name": name, "count": count, "current": False} for name, count in _SAMPLE_FAVORITES]
        rows = []
        for path in self._favorite_paths():
            name = Path(path).name or path
            if self._query and self._query.casefold() not in name.casefold():
                continue
            rows.append({"path": path, "name": name, "count": _count_text(self._counts.get(_key(path))), "current": bool(self._open) and _key(path) == _key(self._open)})
        return rows

    # -- drives ------------------------------------------------------------------------------------------------------------
    def _drives_view(self) -> list[dict]:
        if self._memory:
            return [{"path": f"sample:{name}", "name": name, "detail": detail, "ratio": ratio} for name, detail, ratio in _SAMPLE_DRIVES]
        model = self._drive_model
        rows = []
        for row in range(model.rowCount()):
            index = model.index(row)
            path = model.filePath(index)
            sizes = self._sizes.get(_key(path))
            detail = f"{_size_text(sizes[0])} free of {_size_text(sizes[1])}" if sizes else ""
            ratio = model.usage_ratio(path)
            rows.append({"path": path, "name": str(model.data(index)), "detail": detail, "ratio": 0.0 if ratio is None else ratio})
        return rows

    # -- the folder tree ---------------------------------------------------------------------------------------------------
    def _include_hidden(self) -> bool:
        return bool(self._settings is not None and self._settings.value(SHOW_HIDDEN_KEY, False, bool))

    def _list(self, path: str) -> None:
        """Ask for the child folders of `path` once; they arrive later through `_listed`."""
        key = _key(path)
        if key in self._children or key in self._requested:
            return
        self._requested.add(key)
        hidden = self._include_hidden()

        def work() -> None:
            from ..scanner import scan_child_folders

            try:
                found = [(record.name, record.path) for record in scan_child_folders(path, include_hidden=hidden)]
            except Exception:
                found = []
            self._listed.emit(path, found)

        threading.Thread(target=work, name="qml-folders", daemon=True).start()

    def _apply_listed(self, path: str, found: list) -> None:
        key = _key(path)
        self._requested.discard(key)
        self._children[key] = [(name, child) for name, child in found]
        self.changed.emit()

    def _tree_rows(self) -> list[dict]:
        if self._memory:
            leaf = self._photos.leafName or "Kutaisi"
            sample = (("2025", "8,410", 0, "chevR", False), ("2026", "3,902", 0, "chevDown", False), ("January", "640", 1, "chevR", False),
                      ("Georgia trip", "1,284", 1, "chevDown", False), (leaf, str(self._photos.total), 2, "library", True), ("Tbilisi", "402", 2, "library", False))
            return [{"path": f"sample:{name}", "name": name, "count": count, "depth": depth, "icon": icon, "current": current, "expandable": icon != "library"}
                    for name, count, depth, icon, current in sample]
        if self._query and self._direct_matches is not None:
            return [{"path": path, "name": path, "count": _count_text(self._counts.get(_key(path))), "depth": 0,
                     "icon": "chevR", "current": _key(path) == _key(self._open), "expandable": True} for path in self._direct_matches]
        if not self._root:
            return []
        query = self._query.strip().casefold()
        open_key = _key(self._open) if self._open else ""

        def walk(parent: str, depth: int) -> list[dict]:
            out: list[dict] = []
            for name, path in self._children.get(_key(parent), ()):
                key = _key(path)
                expanded = key in self._expanded or bool(query and key in self._search_expanded)
                below = walk(path, depth + 1) if expanded else []
                if query and query not in name.casefold() and not below:
                    continue
                kids = self._children.get(key)
                leaf = kids is not None and not kids
                out.append({
                    "path": path, "name": name, "count": _count_text(self._counts.get(key)), "depth": depth,
                    "icon": "library" if leaf else ("chevDown" if expanded else "chevR"), "current": key == open_key, "expandable": not leaf,
                })
                out.extend(below)
            return out

        return walk(self._root, 0)

    def _tree_status(self) -> str:
        """A quiet line under the Folders heading when there is nothing to list: what to do, or that the drive is being read."""
        if self._memory:
            return ""
        if self._query:
            if self._search_pending:
                return "Searching folders…"
            if self._search_limited:
                return "More folders may match. Enter a full path to go directly."
            if not self._root and self._direct_matches is None:
                return "Choose a drive to search, or enter a full folder path"
            return "" if self._tree_rows() else "No folder matches"
        if not self._root:
            return "Choose a drive or open a folder"
        key = _key(self._root)
        if key not in self._children:
            return "Reading folders…"
        if not self._children[key]:
            return "No folders in this drive"
        if self._query and not self._tree_rows():
            return "No folder matches"
        return ""

    # -- Back, Forward and Up (the header's buttons) -----------------------------------------------------------------------
    def _parent_folder(self) -> str:
        if not self._open:
            return ""
        parent = os.path.dirname(self._open.rstrip("\\/"))
        return parent if parent and _key(parent) != _key(self._open) else ""

    @Slot()
    def goBack(self) -> None:
        while self._back:
            target = self._back.pop()
            if _key(target) == _key(self._open) or path_policy.confirmed_missing(target):
                continue
            self._forward.append(self._open)
            self._step_to(target)
            return

    @Slot()
    def goForward(self) -> None:
        while self._forward:
            target = self._forward.pop()
            if _key(target) == _key(self._open) or path_policy.confirmed_missing(target):
                continue
            self._back.append(self._open)
            self._step_to(target)
            return

    @Slot()
    def goUp(self) -> None:
        parent = self._parent_folder()
        if parent:
            self.openFolder(parent)             # a step like any other: Back returns to where Up left

    def _step_to(self, path: str) -> None:
        self._stepping = True
        try:
            self.openFolder(path)
        finally:
            self._stepping = False

    # -- opening a folder --------------------------------------------------------------------------------------------------
    @Slot(str)
    def openFolder(self, path: str) -> None:
        """Show `path` in the grid, put the tree on its drive with the way down to it opened, and remember it as a recent folder."""
        path = os.path.normpath(path.strip()) if path and path.strip() else ""
        if not path or path.startswith("sample:") or path_policy.confirmed_missing(path):
            return
        if self._query:
            self.setQuery("")
        if self._open and _key(self._open) != _key(path) and not self._stepping:
            self._back.append(self._open)
            del self._back[:-100]
            self._forward.clear()
        same_folder = bool(self._open) and _key(self._open) == _key(path)
        self._open = path
        self._root = os.path.normpath(self._drive_root(path) or path)
        if not same_folder:
            self._expanded = {}
        self._list(self._root)
        parent = self._root
        try:
            parts = Path(path).relative_to(self._root).parts if _key(path) != _key(self._root) else ()
        except ValueError:
            parts = ()
        for part in parts[:-1]:                                # every folder above the open one is opened; the open one is highlighted
            parent = os.path.join(parent, part)
            self._expanded[_key(parent)] = parent
            self._list(parent)
        self._remember_recent(path)
        self._remember_last_folder(path)
        self._pending_current = self._saved_position(path)
        self._photos.loadFolder(path)
        self._watch(path)
        self.changed.emit()

    # -- watching the open folder (the Widgets setting "Refresh the open folder when files change on disk") --------------------
    def _watching(self) -> bool:
        return self._settings is not None and bool(self._settings.value(WATCH_KEY, True, bool))

    def _watch(self, folder: str) -> None:
        """Watch `folder` (a local one: a network or removable drive is not watched, as in the backend) when the setting is on."""
        if self._watcher is None:
            return
        old = self._watcher.directories()
        if old:
            self._watcher.removePaths(old)
        if self._watch_timer is not None:
            self._watch_timer.stop()
        self._watch_seen = None
        if folder and self._watching() and path_policy.is_plain_local(folder) and os.path.isdir(folder):   # the cheap check first: a stat of an offline share stalls the window
            self._watcher.addPath(folder)
            self._watch_seen = _listing(folder)

    def _watched_change(self) -> None:
        if not self._open or not self._watching():
            return
        if self._photos.loading:
            self._watch_timer.start(450)                        # a read is still going: look again once it is done
        elif not path_policy.confirmed_missing(self._open):
            seen = _listing(self._open)
            if seen is not None and seen == self._watch_seen:
                return                                          # only the app's own files changed (a sidecar, its hidden AI and edit folders): nothing to read again
            self._watch_seen = seen
            self.refreshFolder()

    @Slot()
    def refreshFolder(self) -> None:
        """Read the open folder again (F5, and a change on disk), keeping the grid on the photo the person is on."""
        if not self._open or self._memory:
            return
        if self._last_photo and self._in_open_folder(self._last_photo):
            self._pending_current = self._last_photo
        self._photos.loadFolder(self._open)
        self.changed.emit()

    # -- session memory: the last folder, and the photo the person was on --------------------------------------------------
    def _remember_last_folder(self, folder: str) -> None:
        if self._persist and self._settings is not None:
            self._settings.setValue(LAST_FOLDER_KEY, folder)

    @Slot(result=bool)
    def restoreLastFolder(self) -> bool:
        """Open the folder that was open when the app was last closed (what the Widgets app does at start); False when there is none to open."""
        if not self._persist or self._settings is None:
            return False
        last = self._settings.value(LAST_FOLDER_KEY, "", str)
        if not isinstance(last, str) or not last or path_policy.confirmed_missing(last):
            return False                                       # a share that is asleep is not "gone": it is tried, and the grid says if it cannot be read
        self.openFolder(last)
        return bool(self._open)

    def _folder_states(self) -> dict:
        if self._settings is None:
            return {}
        raw = self._settings.value(FOLDER_STATE_KEY, "", str)
        try:
            payload = json.loads(raw) if isinstance(raw, str) and raw else {}
        except ValueError:
            return {}
        return payload if isinstance(payload, dict) else {}

    def _saved_position(self, folder: str) -> str:
        """The photo to put the grid on when `folder` opens: the one saved for it, when "Reopen folders where I left off" is on (the default)."""
        if not self._persist or self._settings is None or not self._settings.value(RESTORE_POSITION_KEY, True, bool):
            return ""
        entry = self._folder_states().get(_key(folder))
        current = entry.get("current") if isinstance(entry, dict) else ""
        return current if isinstance(current, str) else ""

    def _in_open_folder(self, photo: str) -> bool:
        """Is `photo` one of the open folder's? The scanner lists a folder under the path the filesystem resolves it to, so a folder opened
        through a link (or a mapped drive) holds photos whose folder is spelled differently from the one that was opened. That spelling is
        taken from the photos already read: asking the filesystem here would be a check of a share on the interface thread."""
        parent = _key(os.path.dirname(photo))
        if parent == _key(self._open):
            return True
        rows = self._photos.all_rows()
        return bool(rows) and _key(self._photos.folder_dir()) == _key(self._open) and parent == _key(os.path.dirname(rows[0]["path"]))

    def open_file(self, photo: str) -> None:
        """Open the folder `photo` is in and put the grid on it once the folder has been read (a photo opened from the file manager)."""
        photo = os.path.normpath(photo.strip()) if photo and photo.strip() else ""
        if not photo:
            return
        self.openFolder(os.path.dirname(photo))
        if self._open and _key(self._open) == _key(os.path.dirname(photo)):
            self._pending_current = photo                      # instead of the photo saved for the folder: this one was asked for

    @Slot(str)
    def savePosition(self, photo: str) -> None:
        """Remember `photo` as where the person is in the open folder. Called as they move (after a pause) and when the window closes."""
        if not photo:
            return
        self._last_photo = photo
        if self._memory or self._settings is None:
            self.saved = (self._open, photo)
            return
        if not self._open or not self._persist or self._pending_current or self._photos.loading or not self._in_open_folder(photo):
            return                                             # not before the grid has been put back, and only photos of this folder
        states = self._folder_states()
        key = _key(self._open)
        entry = states.pop(key, {})
        entry = dict(entry) if isinstance(entry, dict) else {}
        entry["current"] = photo                               # the other fields of the entry (the shared sort and scroll) are kept as they are
        states[key] = entry
        while len(states) > STATE_LIMIT:
            states.pop(next(iter(states)))
        self._settings.setValue(FOLDER_STATE_KEY, json.dumps(states))

    def _row_of(self, photo: str) -> int:
        row = self._photos.row_for_path(photo)
        if row >= 0:
            return row
        wanted = _key(photo)
        name = os.path.basename(wanted)
        by_name = -1
        for index in range(self._photos.rowCount()):
            item = self._photos.row_at(index)
            if _key(item["path"]) == wanted or any(_key(other) == wanted for other in item.get("companions", ())):
                return index                                   # the photo, or the photo whose JPEG (or RAW) partner this file is
            if by_name < 0 and name in (os.path.basename(_key(p)) for p in (item["path"], *item.get("companions", ()))):
                by_name = index
        # The folder was reached through a link: its photos are listed under the resolved path, and the file asked for is known by its name.
        return by_name if by_name >= 0 and not _same_folder_spelling(self._photos, photo) else -1

    @Slot(str)
    def openUrl(self, url: str) -> None:
        """The folder a dialog returned (a file:/// URL)."""
        local = QUrl(url).toLocalFile()
        self.openFolder(local or url)

    @Slot(str)
    def toggle(self, path: str) -> None:
        """Open or close a folder in the tree."""
        key = _key(path)
        if key in self._expanded:
            del self._expanded[key]
        else:
            if self._one_branch():
                self._close_siblings(path)
            self._expanded[key] = path
            self._list(path)
        self.changed.emit()

    @Slot(str)
    def expand(self, path: str) -> None:
        """Reveal children on a row double-click, including a row already selected."""
        if _key(path) not in self._expanded:
            self.toggle(path)

    def _one_branch(self) -> bool:
        """The Widgets setting "Keep only one branch expanded per level" (`view/single_drive_expansion`, on by default)."""
        return self._settings is not None and bool(self._settings.value(SINGLE_BRANCH_KEY, True, bool))

    def _close_siblings(self, path: str) -> None:
        """Opening a folder closes the opened folders beside it, and what is below them, unless they are on the way to the open folder."""
        parent = _key(os.path.dirname(path))
        way, here = set(), self._open
        while here:
            way.add(_key(here))
            above = os.path.dirname(here)
            here = above if above != here else ""
        for key, opened in list(self._expanded.items()):
            if _key(os.path.dirname(opened)) == parent and key != _key(path) and key not in way:
                for below in [k for k in self._expanded if k == key or k.startswith(key + os.sep)]:
                    del self._expanded[below]

    @Slot(str)
    def reveal(self, path: str) -> None:
        if self._memory or not path or path.startswith("sample:"):
            return
        from ..shell_actions import open_in_file_explorer

        open_in_file_explorer(path)

    @Slot(str)
    def revealFile(self, path: str) -> None:
        """Show a photo selected in File Explorer."""
        if self._memory or not path or path.startswith("sample:"):
            return
        from ..shell_actions import reveal_in_file_explorer

        reveal_in_file_explorer(path)

    @Slot()
    def refresh(self) -> None:
        """Read the drives and every opened folder again."""
        self._children.clear()
        self._requested.clear()
        if self._root:
            self._list(self._root)
            for path in list(self._expanded.values()):
                self._list(path)
        if self._drive_model is not None and not self._memory:
            self._drive_model.refresh()
        self.changed.emit()

    @Slot(str)
    def setQuery(self, text: str) -> None:
        if text != self._query:
            self._query = text
            self._search_token += 1
            self._search_cancel.set()
            self._search_cancel = threading.Event()
            self._search_timer.stop()
            self._search_expanded.clear()
            self._direct_matches = None
            self._search_limited = False
            self._open_query_when_ready = False
            self._search_pending = bool(text.strip()) and not self._memory
            if self._search_pending:
                self._search_timer.start()
            self.changed.emit()

    def _search_folders(self) -> None:
        token, query, cancel = self._search_token, self._query.strip().strip('"'), self._search_cancel
        root, hidden = self._root, self._include_hidden()
        local = QUrl(query).toLocalFile() if query.startswith("file:") else query
        direct = os.path.isabs(local)

        def work() -> None:
            from ..scanner import scan_child_folders

            listings = []
            matches = [] if direct else None
            limited = False
            if direct:
                if os.path.isdir(local):
                    matches.append(os.path.normpath(local))
            elif root:
                pending, seen = deque([(root, 0)]), set()
                deadline = time.monotonic() + 5
                found = 0
                while pending and not cancel.is_set():
                    if len(seen) >= 5000 or time.monotonic() >= deadline or found >= 100:
                        limited = True
                        break
                    parent, depth = pending.popleft()
                    key = _key(parent)
                    if key in seen:
                        continue
                    seen.add(key)
                    try:
                        children = [(r.name, r.path) for r in scan_child_folders(parent, include_hidden=hidden)]
                    except OSError:
                        children = []
                    listings.append((parent, children))
                    found += sum(query.casefold() in name.casefold() for name, _ in children)
                    if depth < 32:
                        pending.extend((path, depth + 1) for _, path in children
                                       if not os.path.islink(path) and not getattr(os.path, "isjunction", lambda _: False)(path))
                    elif children:
                        limited = True
            if not cancel.is_set():
                self._searched.emit(token, (listings, matches, limited))

        threading.Thread(target=work, name="qml-folder-search", daemon=True).start()

    def _apply_search(self, token: int, result) -> None:
        if token != self._search_token:
            return
        listings, self._direct_matches, self._search_limited = result
        for path, children in listings:
            self._children[_key(path)] = children
            self._search_expanded.add(_key(path))
        self._search_pending = False
        self.changed.emit()
        if self._open_query_when_ready:
            self.openQuery()

    @Slot()
    def openQuery(self) -> None:
        """Enter opens a pasted folder path, or the sole matching folder."""
        if self._search_pending:
            self._open_query_when_ready = True
            return
        matches = self._direct_matches
        if matches is None:
            query = self._query.strip().casefold()
            matches = [r["path"] for r in self._tree_rows() if query and query in r["name"].casefold()]
        if len(matches) == 1:
            self.openFolder(matches[0])

    # -- recent folders (the Widgets key, same rules) ----------------------------------------------------------------------
    def _remember_recent(self, folder: str) -> None:
        if self._settings is None or not self._persist:
            return
        raw = self._settings.value(RECENT_KEY, [], list)
        if isinstance(raw, str):
            raw = [raw]
        key = _key(folder)
        recent = [folder] + [p for p in (raw or []) if isinstance(p, str) and p and _key(p) != key]
        self._settings.setValue(RECENT_KEY, recent[:RECENT_LIMIT])

    def _note_count(self) -> None:
        """Once the grid has finished reading a folder its photo count is known, and the rows that show that folder say it."""
        if not self._photos.loading and self._open and not self._memory:
            if _key(self._photos.folderPath) == _key(self._open):
                self._counts[_key(self._open)] = self._photos.total
                pending, self._pending_current = self._pending_current, ""
                if pending:
                    row = self._row_of(pending)
                    if row >= 0:
                        self.positionRestored.emit(row)
            self.changed.emit()

    def _recent_view(self, limit: int = 5) -> list[dict]:
        """The folders opened last, newest first (the Widgets key), for the File menu; one that is provably gone from a local drive is left out."""
        if self._settings is None:
            return []
        raw = self._settings.value(RECENT_KEY, [], list)
        if isinstance(raw, str):
            raw = [raw]
        found: list[dict] = []
        for path in raw or []:
            if isinstance(path, str) and path and not path_policy.confirmed_missing(path):
                found.append({"path": path, "name": Path(path).name or path})
            if len(found) >= limit:
                break
        return found

    # -- what QML shows ----------------------------------------------------------------------------------------------------
    def _publish(self) -> None:
        self._fav_rows.set_rows(self._favorites_view())
        self._drv_rows.set_rows(self._drives_view())
        self._dir_rows.set_rows(self._tree_rows())

    favoritesModel = Property(QObject, lambda self: self._fav_rows, constant=True)
    drivesModel = Property(QObject, lambda self: self._drv_rows, constant=True)
    treeModel = Property(QObject, lambda self: self._dir_rows, constant=True)
    favorites = Property("QVariantList", lambda self: self._favorites_view(), notify=changed)
    drives = Property("QVariantList", lambda self: self._drives_view(), notify=changed)
    tree = Property("QVariantList", lambda self: self._tree_rows(), notify=changed)
    treeStatus = Property(str, lambda self: self._tree_status(), notify=changed)
    query = Property(str, lambda self: self._query, notify=changed)
    openPath = Property(str, lambda self: self._open, notify=changed)
    canBack = Property(bool, lambda self: bool(self._back), notify=changed)
    canForward = Property(bool, lambda self: bool(self._forward), notify=changed)
    canUp = Property(bool, lambda self: bool(self._parent_folder()), notify=changed)
    recentFolders = Property("QVariantList", lambda self: self._recent_view(), notify=changed)
    folderUrl = Property(str, lambda self: QUrl.fromLocalFile(self._open).toString() if self._open else "", notify=changed)
