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
import os
import shutil
import threading
from pathlib import Path

from PySide6.QtCore import Property, QAbstractListModel, QByteArray, QModelIndex, QObject, Qt, QUrl, Signal, Slot

from .. import path_policy
from ..app_identity import user_settings

FAVORITES_KEY = "folders/favorites"
RECENT_KEY = "folders/recent_opened"
SHOW_HIDDEN_KEY = "view/show_hidden_folders"
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
        self._root = ""                                        # the drive (or share) the tree is rooted at: the open folder's
        self._expanded: dict[str, str] = {}                   # the folders the tree has open: key -> path
        self._children: dict[str, list[tuple[str, str]]] = {}  # key -> [(name, path)]
        self._requested: set[str] = set()
        self._counts: dict[str, int] = {}                      # photos in a folder, known once it has been opened
        self._sizes: dict[str, tuple[int, int]] = {}           # drive -> (free, total) bytes, as the last probe saw them
        self._query = ""
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
        if not self._root:
            return []
        query = self._query.casefold()
        open_key = _key(self._open) if self._open else ""

        def walk(parent: str, depth: int) -> list[dict]:
            out: list[dict] = []
            for name, path in self._children.get(_key(parent), ()):
                key = _key(path)
                expanded = key in self._expanded
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

    # -- opening a folder --------------------------------------------------------------------------------------------------
    @Slot(str)
    def openFolder(self, path: str) -> None:
        """Show `path` in the grid, put the tree on its drive with the way down to it opened, and remember it as a recent folder."""
        path = os.path.normpath(path.strip()) if path and path.strip() else ""
        if not path or path.startswith("sample:") or path_policy.confirmed_missing(path):
            return
        self._open = path
        self._root = os.path.normpath(self._drive_root(path) or path)
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

    @Slot(str)
    def savePosition(self, photo: str) -> None:
        """Remember `photo` as where the person is in the open folder. Called as they move (after a pause) and when the window closes."""
        if not photo:
            return
        if self._memory or self._settings is None:
            self.saved = (self._open, photo)
            return
        if not self._open or not self._persist or self._pending_current or self._photos.loading or _key(os.path.dirname(photo)) != _key(self._open):
            return                                             # not before the grid has been put back, and only photos of this folder
        states = self._folder_states()
        key = _key(self._open)
        entry = states.pop(key, {})
        entry = dict(entry) if isinstance(entry, dict) else {}
        entry["current"] = photo                               # the other fields of the entry (the Widgets app's sort and scroll) are kept as they are
        states[key] = entry
        while len(states) > STATE_LIMIT:
            states.pop(next(iter(states)))
        self._settings.setValue(FOLDER_STATE_KEY, json.dumps(states))

    def _row_of(self, photo: str) -> int:
        row = self._photos.row_for_path(photo)
        if row >= 0:
            return row
        wanted = _key(photo)
        for index in range(self._photos.rowCount()):
            if _key(self._photos.row_at(index)["path"]) == wanted:
                return index
        return -1

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
            self._expanded[key] = path
            self._list(path)
        self.changed.emit()

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
            self.changed.emit()

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
    openPath = Property(str, lambda self: self._open, notify=changed)
    recentFolders = Property("QVariantList", lambda self: self._recent_view(), notify=changed)
    folderUrl = Property(str, lambda self: QUrl.fromLocalFile(self._open).toString() if self._open else "", notify=changed)
