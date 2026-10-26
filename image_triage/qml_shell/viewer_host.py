"""The popout in the QML shell: a full-window viewer drawn by `qml/Viewer.qml` (mockup: scripts/qml_parity/viewer.dc.html).

This is the host behind it, under the context name `popout` (the grid, the rail and the menus open it with `popout.open(row)`).
`popout.PopoutHost` is the Widgets app's own viewer in a separate window; it is kept behind `--widgets-popout`.

What it does for the picture: a plain JPEG, PNG or WebP is shown from its own file (Qt applies the EXIF orientation); anything else
(a RAW, a TIFF, a PSD...) is read the way the Widgets viewer reads it, `imaging.load_image_for_display`, on a worker thread and written
to a temporary JPEG that Qt then shows. Why files and not an `image://` provider: see thumbs.py (Python on Qt's loader thread
deadlocks). The photos around the current one are read ahead, nearest first and biased to the way the person is going.

Focus assist (F) is the Widgets viewer's `review_tools.build_focus_assist_image`, on the photo read at up to 2400 px, with the same
settings keys (`preview/focus_assist_*`), shown in place of the photo.
"""
from __future__ import annotations

import os
import shutil
import tempfile
import threading
from collections import OrderedDict
from pathlib import Path

from PySide6.QtCore import Property, QObject, QRunnable, QSize, Qt, QThreadPool, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QImage, QImageIOHandler, QImageReader

from ..app_identity import user_settings
from ..formats import RAW_SUFFIXES, suffix_for_path
from ..preload_order import candidate_indexes

DIRECT = frozenset({".jpg", ".jpeg", ".jpe", ".png", ".webp", ".bmp", ".gif"})      # shown from the file itself
READ_LIMIT = 8192                  # the long side asked of the reader: the photo's own size for anything a camera makes
ASSIST_LIMIT = 2400                # focus assist works on the photo read this big, as it works on the Widgets viewer's picture
FILES_KEPT = 24                    # temporary pictures kept (read ahead included)
PRELOAD_INTERVAL_MS = 120
TOAST_MS = 3200
STRIP_MIN, STRIP_MAX, STRIP_DEFAULT = 84, 260, 120
STRIP_KEY = "viewer/strip_height"
BAR_HIDDEN_KEY = "viewer/action_bar_hidden"
ASSIST_ON_KEY, ASSIST_COLOR_KEY = "preview/focus_assist_enabled", "preview/focus_assist_color"
PRELOAD_KEY, ADVANCE_KEY = "preview/preload_batch_size", "view/auto_advance"
DECISION_WORDS = {"win": "marked Winner", "keep": "kept", "rej": "rejected"}


def _file_url(path: str) -> str:
    return QUrl.fromLocalFile(path).toString()


class _Task(QRunnable):
    def __init__(self, host: "ViewerHost", kind: str, path: str, token: str) -> None:
        super().__init__()
        self.host, self.kind, self.path, self.token = host, kind, path, token
        self.setAutoDelete(True)

    def run(self) -> None:
        host = self.host
        if not host._still_wanted(self.path, self.kind):
            host._done.emit(self.kind, self.path, self.token, "", 0, 0, "superseded")
            return
        try:
            if self.kind == "assist":
                file, width, height, error = host._make_assist(self.path, self.token)
            else:
                file, width, height, error = host._make_picture(self.path)
        except Exception as exc:                              # a reader that fails must not take the worker down
            file, width, height, error = "", 0, 0, str(exc) or "The photo could not be read."
        host._done.emit(self.kind, self.path, self.token, file, width, height, error)


class ViewerHost(QObject):
    openChanged = Signal()
    rowChanged = Signal()
    imageChanged = Signal()
    assistChanged = Signal()
    toastChanged = Signal()
    stripChanged = Signal()
    barChanged = Signal()
    closed = Signal(int)                       # the viewer was put away on this row of the grid
    _done = Signal(str, str, str, str, int, int, str)

    def __init__(self, photos, thumbs, collections=None, *, files=None, cull=None, toasts=None, memory: bool = False,
                 prebuild: bool = False, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._photos, self._thumbs, self._collections, self._files, self._cull, self._toasts = photos, thumbs, collections, files, cull, toasts
        self._memory = memory
        self._settings = None if memory else user_settings()
        self._open = False
        self._row = -1
        self._path = ""
        self._travel = 0
        self._window = None
        self._dir = ""
        self._pictures: OrderedDict[str, tuple[str, int, int]] = OrderedDict()      # path -> (temporary file, width, height)
        self._failed: dict[str, str] = {}
        self._asked: set[str] = set()
        self._assist: dict[tuple[str, str], tuple[str, int, int]] = {}
        self._assist_asked: set[tuple[str, str]] = set()
        self._wanted: set[tuple[str, str]] = set()
        self._lock = threading.Lock()
        self._pool = QThreadPool()
        self._pool.setMaxThreadCount(2)
        self._strip = self._read_int(STRIP_KEY, STRIP_DEFAULT, STRIP_MIN, STRIP_MAX)
        self._bar_hidden = self._read_bool(BAR_HIDDEN_KEY, False)
        self._assist_on = self._read_bool(ASSIST_ON_KEY, False)
        self._assist_color = self._read_str(ASSIST_COLOR_KEY, "red")
        self._toast: dict = {}
        self._toast_id = 0
        self._toast_timer = QTimer(self)
        self._toast_timer.setSingleShot(True)
        self._toast_timer.setInterval(TOAST_MS)
        self._toast_timer.timeout.connect(self._clear_toast)
        self._preload_timer = QTimer(self)
        self._preload_timer.setSingleShot(True)
        self._preload_timer.setInterval(PRELOAD_INTERVAL_MS)
        self._preload_timer.timeout.connect(self._run_preload)
        self._done.connect(self._arrived, Qt.ConnectionType.QueuedConnection)
        photos.modelReset.connect(self._photos_changed)
        photos.rowsRemoved.connect(self._photos_changed)
        photos.rowsInserted.connect(self._photos_changed)

    # -- settings -----------------------------------------------------------------------------------------------------------
    def _read_int(self, key: str, default: int, low: int, high: int) -> int:
        if self._settings is None:
            return default
        try:
            return max(low, min(high, int(self._settings.value(key, default))))
        except (TypeError, ValueError):
            return default

    def _read_bool(self, key: str, default: bool) -> bool:
        return default if self._settings is None else bool(self._settings.value(key, default, bool))

    def _read_str(self, key: str, default: str) -> str:
        return default if self._settings is None else str(self._settings.value(key, default, str))

    def _write(self, key: str, value) -> None:
        if self._settings is not None:
            self._settings.setValue(key, value)

    # -- what QML sees ------------------------------------------------------------------------------------------------------
    isOpen = Property(bool, lambda self: self._open, notify=openChanged)
    qml = Property(bool, lambda self: True, constant=True)               # the shell draws it (the Widgets host has no such property)
    row = Property(int, lambda self: self._row, notify=rowChanged)
    canEdit = Property(bool, lambda self: True, constant=True)           # PhotoCraft is there to be opened (False would grey the button)
    barHidden = Property(bool, lambda self: self._bar_hidden, notify=barChanged)
    stripHeight = Property(int, lambda self: self._strip, notify=stripChanged)
    toast = Property("QVariantMap", lambda self: self._toast, notify=toastChanged)
    assistOn = Property(bool, lambda self: self._assist_on, notify=assistChanged)

    @Property(str, notify=imageChanged)
    def src(self) -> str:
        """The picture to show (a file URL), or "" while it is being read."""
        picture = self._picture_for(self._path)
        return picture[0] if picture else ""

    @Property(int, notify=imageChanged)
    def pixelWidth(self) -> int:
        picture = self._picture_for(self._path)
        return picture[1] if picture else 0

    @Property(int, notify=imageChanged)
    def pixelHeight(self) -> int:
        picture = self._picture_for(self._path)
        return picture[2] if picture else 0

    @Property(bool, notify=imageChanged)
    def loading(self) -> bool:
        return self._open and self._path != "" and self._picture_for(self._path) is None and self._path not in self._failed

    @Property(str, notify=imageChanged)
    def error(self) -> str:
        return self._failed.get(self._path, "")

    @Property(str, notify=assistChanged)
    def assistSrc(self) -> str:
        """Focus assist's picture of the photo (a file URL) while assist is on and it is made, else ""."""
        if not self._assist_on:
            return ""
        found = self._assist.get((self._path, self._assist_color))
        return _file_url(found[0]) if found else ""

    @Property(bool, notify=assistChanged)
    def assistBusy(self) -> bool:
        return self._assist_on and (self._path, self._assist_color) not in self._assist and self._path not in self._failed

    @Property(str, notify=assistChanged)
    def assistColor(self) -> str:
        return self._assist_color

    @Property(str, notify=rowChanged)
    def crumb(self) -> str:
        """The folder as the top bar shows it: its last two names."""
        if not self._photos.folder_dir():                 # the sample page (--mock): the mockup's own folder names
            return "Georgia trip \u203a Kutaisi"
        folder = self._photos.folder_dir()
        parts = [p for p in Path(folder).parts if p not in ("/", "\\") and not p.endswith((":", ":\\"))] if folder else []
        return " › ".join(parts[-2:]) if parts else "Sample photos"

    @Property("QVariantMap", notify=imageChanged)
    def info(self) -> dict:
        """What the top bar says about the photo: its kind, its size on disk and the size of the picture it shows."""
        item = self._photos.row_at(self._row)
        if item is None:
            return {"kind": "", "ext": "", "size": ""}
        ext = (item.get("ext") or "").upper().lstrip(".")
        if not self._photos.folder_dir():                 # the sample page: the sizes the mockup shows
            raw = bool(item.get("raw"))
            return {"kind": "RAW + JPG" if raw else ext, "ext": ext, "size": "31.4 MB" if raw else "9.8 MB", "dims": "6048 \u00d7 4024", "raw": raw}
        companions = [os.path.splitext(c)[1].lstrip(".").upper() for c in item.get("companions", ())]
        kind = ext + (" + " + companions[0] if companions and item.get("raw") else "")
        try:
            size = os.stat(item["path"]).st_size
        except OSError:
            size = 0
        text = "" if size <= 0 else (f"{size / 1048576:.1f} MB" if size >= 1048576 else f"{max(1, size // 1024)} KB")
        picture = self._picture_for(self._path)
        dims = f"{picture[1]} × {picture[2]}" if picture and picture[1] else ""
        return {"kind": kind, "ext": ext, "size": text, "dims": dims, "raw": bool(item.get("raw"))}

    def setWindow(self, window) -> None:
        self._window = window

    # -- opening, stepping, closing -----------------------------------------------------------------------------------------
    @Slot(int)
    def open(self, row: int) -> None:
        if not 0 <= row < self._photos.rowCount():
            return
        self._travel = 0
        self._show(row)
        if not self._open:
            self._open = True
            self.openChanged.emit()

    @Slot()
    def close(self) -> None:
        if not self._open:
            return
        self._open = False
        self._preload_timer.stop()
        self.openChanged.emit()
        self.imageChanged.emit()
        self.closed.emit(self._row)

    @Slot(int)
    def go(self, row: int) -> None:
        count = self._photos.rowCount()
        if count == 0:
            return
        target = max(0, min(count - 1, row))
        if target == self._row:
            return
        self._travel = 1 if target > self._row else -1
        self._show(target)

    @Slot(int)
    def step(self, delta: int) -> None:
        self.go(self._row + delta)

    def _show(self, row: int) -> None:
        item = self._photos.row_at(row)
        if item is None:
            return
        self._row = row
        self._path = item["path"]
        self._ask("picture", self._path)
        if self._assist_on:
            self._ask("assist", self._path)
        self._preload_timer.start()
        self.rowChanged.emit()
        self.imageChanged.emit()
        self.assistChanged.emit()

    def _photos_changed(self, *args) -> None:
        """Photos went away (deleted, moved) or the folder changed: stay on the same photo, or the one that took its place."""
        if not self._open:
            return
        count = self._photos.rowCount()
        if count == 0:
            self.close()
            return
        found = self._photos.row_for_path(self._path) if self._path else -1
        target = found if found >= 0 else min(self._row, count - 1)
        item = self._photos.row_at(target)
        if item is not None and (target != self._row or item["path"] != self._path):
            self._show(target)
        else:
            self.rowChanged.emit()

    # -- reading the picture ------------------------------------------------------------------------------------------------
    def _picture_for(self, path: str):
        if not path:
            return None
        if suffix_for_path(path) in DIRECT:
            reader = QImageReader(path)
            size = reader.size()
            if reader.transformation() & QImageIOHandler.Transformation.TransformationRotate90:
                size = size.transposed()                   # a portrait photo's size is the way it is shown
            return (_file_url(path), size.width(), size.height())
        found = self._pictures.get(path)
        if found is None:
            return None
        self._pictures.move_to_end(path)
        return (_file_url(found[0]), found[1], found[2])

    def _ensure_dir(self) -> str:
        if not self._dir:
            self._dir = tempfile.mkdtemp(prefix="imagetriage-viewer-")
        return self._dir

    def _still_wanted(self, path: str, kind: str) -> bool:
        with self._lock:
            return (path, kind) in self._wanted

    def _ask(self, kind: str, path: str) -> None:
        """Start reading `path` (a picture, or its focus-assist version) unless it is read, being read, or needs no reading."""
        if kind == "picture":
            if suffix_for_path(path) in DIRECT or path in self._pictures or path in self._asked or path in self._failed:
                return
            self._asked.add(path)
            token = ""
        else:
            token = self._assist_color
            key = (path, token)
            if key in self._assist or key in self._assist_asked or path in self._failed:
                return
            self._assist_asked.add(key)
        with self._lock:
            self._wanted.add((path, kind))
        self._pool.start(_Task(self, kind, path, token))

    def _make_picture(self, path: str):
        from ..imaging import load_image_for_display, sanitize_display_error

        image, error = load_image_for_display(path, QSize(READ_LIMIT, READ_LIMIT), prefer_embedded=True)
        if image.isNull():
            return "", 0, 0, sanitize_display_error(error, path=path) or "The photo could not be read."
        file = os.path.join(self._ensure_dir(), f"{abs(hash(path)):x}.jpg")
        image.convertToFormat(QImage.Format.Format_RGB888).save(file, "JPG", 93)
        width, height = image.width(), image.height()
        try:                                                  # the camera's own pixel size, which a RAW's embedded preview is smaller than
            from ..metadata import load_capture_metadata

            meta = load_capture_metadata(path)
            if meta.width > 0 and meta.height > 0:
                width, height = meta.width, meta.height
        except Exception:
            pass
        return file, width, height, ""

    def _make_assist(self, path: str, color: str):
        from ..imaging import load_image_for_display
        from ..review_tools import build_focus_assist_image

        image, error = load_image_for_display(path, QSize(ASSIST_LIMIT, ASSIST_LIMIT), prefer_embedded=True)
        if image.isNull():
            return "", 0, 0, error or "The photo could not be read."
        assisted = build_focus_assist_image(image, color, dim_background=self._read_bool("preview/focus_assist_dim_background", True))
        file = os.path.join(self._ensure_dir(), f"a{abs(hash((path, color))):x}.jpg")
        assisted.convertToFormat(QImage.Format.Format_RGB888).save(file, "JPG", 90)
        return file, assisted.width(), assisted.height(), ""

    def _arrived(self, kind: str, path: str, token: str, file: str, width: int, height: int, error: str) -> None:
        with self._lock:
            self._wanted.discard((path, kind))
        if kind == "assist":
            self._assist_asked.discard((path, token))
            if file:
                self._assist[(path, token)] = (file, width, height)
                if len(self._assist) > 6:
                    old = next(iter(self._assist))
                    self._remove(self._assist.pop(old)[0])
            elif error != "superseded":
                self._failed[path] = error
            if path == self._path:
                self.assistChanged.emit()
            return
        self._asked.discard(path)
        if file:
            self._pictures[path] = (file, width, height)
            while len(self._pictures) > FILES_KEPT:
                _old, (old_file, _w, _h) = self._pictures.popitem(last=False)
                self._remove(old_file)
        elif error != "superseded":
            self._failed[path] = error
        if path == self._path:
            self.imageChanged.emit()
            self.assistChanged.emit()

    @staticmethod
    def _remove(file: str) -> None:
        try:
            os.remove(file)
        except OSError:
            pass

    def _run_preload(self) -> None:
        if not self._open or self._row < 0:
            return
        limit = self._read_int(PRELOAD_KEY, 4, 0, 24)
        if limit <= 0:
            return
        taken = 0
        for index in candidate_indexes(self._row, self._photos.rowCount(), self._travel):
            if taken >= limit:
                break
            item = self._photos.row_at(index)
            if item is None or index == self._row:
                continue
            taken += 1
            self._ask("picture", item["path"])

    def shutdown(self) -> None:
        self._preload_timer.stop()
        with self._lock:
            self._wanted.clear()
        self._pool.waitForDone(2000)
        if self._dir:
            shutil.rmtree(self._dir, ignore_errors=True)
            self._dir = ""

    # -- focus assist -------------------------------------------------------------------------------------------------------
    @Slot()
    def toggleAssist(self) -> None:
        self._assist_on = not self._assist_on
        self._write(ASSIST_ON_KEY, self._assist_on)
        if self._assist_on and self._path:
            self._ask("assist", self._path)
        self.assistChanged.emit()

    @Slot()
    def cycleAssistColor(self) -> None:
        from ..review_tools import FOCUS_ASSIST_COLORS

        ids = [c.id for c in FOCUS_ASSIST_COLORS]
        at = ids.index(self._assist_color) if self._assist_color in ids else 0
        self._assist_color = ids[(at + 1) % len(ids)]
        self._write(ASSIST_COLOR_KEY, self._assist_color)
        if self._assist_on and self._path:
            self._ask("assist", self._path)
        self.assistChanged.emit()

    # -- the filmstrip's height ---------------------------------------------------------------------------------------------
    @Slot(int)
    def setStripHeight(self, height: int) -> None:
        value = max(STRIP_MIN, min(STRIP_MAX, int(height)))
        if value != self._strip:
            self._strip = value
            self.stripChanged.emit()

    @Slot()
    def saveStripHeight(self) -> None:
        self._write(STRIP_KEY, self._strip)

    # -- the action bar can be put away (Tab, or the arrow at its end); the choice is remembered ---------------------------
    @Slot(bool)
    def setBarHidden(self, hidden: bool) -> None:
        if bool(hidden) != self._bar_hidden:
            self._bar_hidden = bool(hidden)
            self._write(BAR_HIDDEN_KEY, self._bar_hidden)
            self.barChanged.emit()

    # -- deciding, rating, collecting, deleting ------------------------------------------------------------------------------
    @Slot(int, str)
    def decide(self, row: int, decision: str) -> None:
        """Winner, Keep or Reject on a photo (the grid's own toggle). Winner also goes into the collection that takes new winners."""
        item = self._photos.row_at(row)
        if item is None:
            return
        before = item["decision"]
        self._photos.setDecision(row, decision)
        after = self._photos.row_at(row)["decision"]
        if after == "win" and self._collections is not None and self._collections.activeId != "":
            self._collections.addSelection(self._collections.activeId, row)
        if after:
            self._say(f"{item['name']} {DECISION_WORDS[after]}", after, undo=True)
        else:
            self._say(f"{item['name']} is no longer {'a winner' if before == 'win' else 'kept' if before == 'keep' else 'rejected'}", "info", undo=True)
        if after in ("win", "rej") and self._read_bool(ADVANCE_KEY, True) and row == self._row:
            self.step(1)

    @Slot(int, int)
    def rate(self, row: int, value: int) -> None:
        if self._photos.row_at(row) is not None:
            self._photos.setRating(row, max(0, min(5, int(value))))

    @Slot()
    def undo(self) -> None:
        what = self._photos.undo()
        self._say(f"Undid the {what}" if what else "Nothing to undo", "info", undo=False)

    @Slot(int)
    def delete(self, row: int) -> None:
        if self._files is not None:
            self._files.deleteRow(row)                  # asks first when it must (the shell's confirm card); the photo going away moves the viewer on

    @Slot(int, result="QVariantList")
    def collectionRows(self, row: int) -> list:
        """The collections as the popover lists them, each with whether the photo on `row` is in it."""
        if self._collections is None:
            return []
        return [{"id": c["id"], "name": c["name"], "color": c["color"], "on": bool(self._collections.hasRow(c["id"], row))} for c in self._collections.rows]

    @Slot(int, str)
    def toggleCollection(self, row: int, cid: str) -> None:
        if self._collections is None:
            return
        if self._collections.hasRow(cid, row):
            self._collections.removeSelection(cid, row)
        else:
            self._collections.addSelection(cid, row)

    def flag(self, path: str) -> dict:
        return self._cull.flagInfo(path) if self._cull is not None else {}

    @Slot(int, result="QVariantMap")
    def flagFor(self, row: int) -> dict:
        """The Cull analysis's reason for a flagged photo (label, colour), or {}."""
        item = self._photos.row_at(row)
        return self.flag(item["path"]) if item is not None else {}

    @Slot(int)
    def keepFlagged(self, row: int) -> None:
        item = self._photos.row_at(row)
        if item is None:
            return
        if self._cull is not None:
            self._cull.restorePath(item["path"], True)
        if item["decision"] != "keep":
            self._photos.setDecision(row, "keep")
        self._say(f"{item['name']} kept", "keep", undo=True)

    # -- the toast ----------------------------------------------------------------------------------------------------------
    def _say(self, text: str, kind: str, undo: bool) -> None:
        self._toast_id += 1
        self._toast = {"id": self._toast_id, "text": text, "kind": kind, "undo": undo}
        self.toastChanged.emit()
        self._toast_timer.start()

    @Slot(str)
    def notice(self, text: str) -> None:
        self._say(text, "info", undo=False)

    def _clear_toast(self) -> None:
        self._toast = {}
        self.toastChanged.emit()

    @Slot()
    def dismissToast(self) -> None:
        self._toast_timer.stop()
        self._clear_toast()
