"""The photo and folder context menus and the toasts (mockup: scripts/qml_parity/menu.dc.html).

Real: the decision (Winner, Keep, Reject), Collections (the library store, see collections_model), Copy path / file name / folder path
(the clipboard), Show in File Explorer, Refresh folder, Sort, Share to phone, Rename / Resize / Convert (open the batch window), and
Restore / Why for a frame the cull flagged. Not connected yet (they say so in a toast): Open, Open in editor, Open with, Copy or move,
Delete. See docs/qml_unwired.md, section "Context menus and toasts".
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Property, QObject, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices, QGuiApplication

DECISIONS = (("win", "Winner", "win", "#3ccf8e"), ("keep", "Keep", "keep", "#6aa6ff"), ("rej", "Reject", "rej", "#ef6856"))
LABEL = {"win": "Winner", "keep": "Keep", "rej": "Reject"}
SEP = {"kind": "sep"}


class ToastModel(QObject):
    changed = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._items: list[dict] = []
        self._callbacks: dict[int, object] = {}
        self._timers: dict[int, QTimer] = {}
        self._next = 1
        self._still = False

    items = Property("QVariantList", lambda self: self._items, notify=changed)
    still = Property(bool, lambda self: self._still, notify=changed)

    def push(self, kind: str, title: str, body: str = "", action: str = "", callback=None, seconds: float = 6.0) -> int:
        """kind: success, info, warn or error. `callback` runs when the action button is pressed. At most three toasts show; the oldest leaves."""
        ident = self._next
        self._next += 1
        items = self._items + [{"id": ident, "kind": kind, "title": title, "body": body, "action": action}]
        for gone in items[:-3]:                                 # the oldest leaves, and its action with it
            self._callbacks.pop(gone["id"], None)
            timer = self._timers.pop(gone["id"], None)
            if timer is not None:
                timer.stop()
        self._items = items[-3:]
        if callback is not None:
            self._callbacks[ident] = callback
        if not self._still and seconds > 0:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(lambda i=ident: self.close(i))
            timer.start(int(seconds * 1000))
            self._timers[ident] = timer
        self.changed.emit()
        return ident

    @Slot(str, str, str, result=int)
    def show(self, kind: str, title: str, body: str = "") -> int:
        return self.push(kind, title, body)

    @Slot(int)
    def close(self, ident: int) -> None:
        timer = self._timers.pop(ident, None)
        if timer is not None:
            timer.stop()
        self._callbacks.pop(ident, None)
        before = len(self._items)
        self._items = [t for t in self._items if t["id"] != ident]
        if len(self._items) != before:
            self.changed.emit()

    @Slot(int)
    def trigger(self, ident: int) -> None:
        callback = self._callbacks.get(ident)
        self.close(ident)
        if callback is not None:
            callback()

    @Slot(bool)
    def setStill(self, still: bool) -> None:
        """Test hook: toasts stay (the mockup's stills)."""
        self._still = still
        self.changed.emit()


class MenuModel(QObject):
    changed = Signal()
    uiRequest = Signal(str, "QVariant")                 # what only the window can do: "share", "batch", "newCollection", "review", "open", "edit", "clearFilters", "view", "sort"

    def __init__(self, photos, collections, cull, toasts: ToastModel, memory: bool = False, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._photos, self._collections, self._cull, self._toasts, self._memory = photos, collections, cull, toasts, memory
        self._kind = ""
        self._row = -1
        self._sub = ""
        self._state: dict = {}
        self._view: dict = {"open": False, "kind": "", "sub": "", "items": [], "subItems": []}

    view = Property("QVariantMap", lambda self: self._view, notify=changed)

    # -- helpers ------------------------------------------------------------------------------------------------------------------
    def _detail(self) -> dict:
        return self._photos.get(self._row) if self._kind == "photo" else {}

    def _row_of(self, path: str) -> int:
        for i in range(self._photos.shown):
            if self._photos.get(i).get("path") == path:
                return i
        return -1

    def _put_decision(self, path: str, target: str) -> None:
        row = self._row_of(path)
        if row < 0:
            return
        current = self._photos.get(row).get("decision", "")
        if current == target:
            return
        if current:
            self._photos.setDecision(row, current)          # setDecision toggles: this clears it
        if target:
            self._photos.setDecision(row, target)

    def _in_collection(self, cid: str, path: str) -> bool:
        return any(r["path"] == path for r in self._collections.member_rows(cid))

    def _row_item(self, key: str, label: str, icon: str, **kw) -> dict:
        return dict({"kind": "row", "key": key, "label": label, "icon": icon, "kbd": "", "arrow": False, "danger": False, "off": False}, **kw)

    def _arrow(self, key: str, label: str, icon: str) -> dict:
        return self._row_item(key, label, icon, arrow=True, open=self._sub == key)

    def _build(self) -> None:
        items: list[dict] = []
        sub: list[dict] = []
        if self._kind == "photo":
            d = self._detail()
            path = d.get("path", "")
            flag = self._cull.flagInfo(path)
            if flag:
                items.append({"kind": "flag", "label": flag["label"], "color": flag["color"]})
            items.append({"kind": "dec", "decs": [{"key": k, "label": lab, "icon": ic, "color": col, "on": d.get("decision") == k} for k, lab, ic, col in DECISIONS]})
            items += [SEP, self._row_item("open", "Open", "open", kbd="Space"), self._row_item("edit", "Open in editor", "editor", kbd="E"), self._arrow("with", "Open with", "with"), SEP,
                      self._row_item("share", "Share to phone", "phone"), self._arrow("coll", "Collections", "coll"), self._arrow("mv", "Copy or move", "move"), SEP,
                      self._row_item("rename", "Rename…", "rename", kbd="F2"), self._row_item("resize", "Resize…", "resize"), self._row_item("convert", "Convert…", "convert"), SEP,
                      self._row_item("cpath", "Copy path", "link"), self._row_item("cname", "Copy file name", "text"), self._row_item("reveal", "Show in File Explorer", "folder"), SEP,
                      self._row_item("del", "Delete", "trash", kbd="Del", danger=True)]
            if self._sub == "with":
                sub = [self._row_item("with:default", "Default app", "open"), self._row_item("with:other", "Choose another app…", "with"), SEP, self._row_item("with:ps", "Photoshop (not found)", "editor", off=True)]
            elif self._sub == "coll":
                for c in self._collections.rows:
                    sub.append({"kind": "row", "key": "coll:" + c["id"], "label": c["name"], "tick": self._in_collection(c["id"], path), "color": c["color"], "iconPath": c["iconPath"], "mark": True})
                sub += [SEP, self._row_item("coll:new", "New collection…", "plus")]
            elif self._sub == "mv":
                sub = [self._row_item("mv:copy", "Copy to folder…", "move"), self._row_item("mv:move", "Move to folder…", "folder"), SEP, {"kind": "head", "label": "Recent"},
                       self._row_item("mv:r1", "Delivery / Album", "folder"), self._row_item("mv:r2", "Georgia trip / Picks", "folder")]
        elif self._kind == "folder":
            st = self._state
            items = [self._row_item("refresh", "Refresh folder", "refresh", kbd="F5"), self._row_item("clear", "Clear filters", "funnel", off=not st.get("filtered")), SEP,
                     self._arrow("view", "View", "view"), self._arrow("sort", "Sort by", "sort"), SEP,
                     self._row_item("freveal", "Show folder in File Explorer", "folder"), self._row_item("fpath", "Copy folder path", "link")]
            if self._sub == "view":
                sub = [{"kind": "row", "key": "view:names", "label": "Show file names", "tick": bool(st.get("names"))}, {"kind": "row", "key": "view:badges", "label": "Show badges", "tick": bool(st.get("badges"))}, SEP]
                sub += [{"kind": "row", "key": "dens:" + k, "label": lab, "tick": st.get("dens") == k} for k, lab in (("s", "Small thumbnails"), ("m", "Medium thumbnails"), ("l", "Large thumbnails"))]
            elif self._sub == "sort":
                sub = [{"kind": "row", "key": "sort:" + k, "label": lab, "tick": st.get("sort") == k} for k, lab in (("name", "Name"), ("ai", "AI score"), ("rating", "Rating"), ("decision", "Decision"))]
        self._view = {"open": bool(self._kind), "kind": self._kind, "sub": self._sub, "items": items, "subItems": sub}
        self.changed.emit()

    # -- opening and closing -----------------------------------------------------------------------------------------------------
    @Slot(int)
    def openPhoto(self, row: int) -> None:
        self._kind, self._row, self._sub = "photo", row, ""
        self._build()

    @Slot(bool, bool, str, str, bool)
    def openFolder(self, names: bool, badges: bool, dens: str, sort: str, filtered: bool) -> None:
        self._kind, self._row, self._sub = "folder", -1, ""
        self._state = {"names": names, "badges": badges, "dens": dens, "sort": sort, "filtered": filtered}
        self._build()

    @Slot()
    def close(self) -> None:
        if self._kind:
            self._kind, self._sub = "", ""
            self._build()

    @Slot(str)
    def setSub(self, key: str) -> None:
        if key != self._sub and self._kind:
            self._sub = key
            self._build()

    # -- doing things ------------------------------------------------------------------------------------------------------------
    def _clipboard(self, text: str) -> None:
        if not self._memory:
            QGuiApplication.clipboard().setText(text)

    def _not_connected(self, what: str) -> None:
        self._toasts.push("warn", f"{what} isn’t connected yet", "Nothing was changed.")

    @Slot(str)
    def act(self, key: str) -> None:
        row = self._row
        d = self._detail()
        path, name = d.get("path", ""), d.get("name", "")
        if key in ("with", "coll", "mv", "view", "sort"):
            self.setSub(key if key != self._sub else "")
            return
        self.close()
        t = self._toasts
        if key in ("win", "keep", "rej"):
            prev = d.get("decision", "")
            target = "" if prev == key else key
            self._put_decision(path, target)
            title = f"Cleared the decision on {name}" if not target else f"Marked {name} as {LABEL[target]}"
            t.push("success", title, "", "Undo", lambda: self._put_decision(path, prev))
        elif key == "share":
            self.uiRequest.emit("share", row)
            t.push("info", f"Added {name} to PocketDrop", "Scan the code on your phone to download it.", "Show", lambda: self.uiRequest.emit("showDrop", 0))
        elif key == "open":
            self.uiRequest.emit("open", row)
            self._not_connected("Opening a photo")
        elif key == "edit":
            self.uiRequest.emit("edit", row)
            self._not_connected("The editor")
        elif key.startswith("with:"):
            self._not_connected("Open with")
        elif key.startswith("mv:"):
            self._not_connected("Copy or move")
        elif key in ("rename", "resize", "convert"):
            self.uiRequest.emit("batch", key)
        elif key == "coll:new":
            self.uiRequest.emit("newCollection", 0)
        elif key.startswith("coll:"):
            cid = key[5:]
            title = next((c["name"] for c in self._collections.rows if c["id"] == cid), "the collection")
            if self._in_collection(cid, path):
                self._collections.removeSelection(cid, row)
                t.push("success", f"Removed from {title}", "", "Undo", lambda: self._collections.addSelection(cid, self._row_of(path)))
            else:
                self._collections.addSelection(cid, row)
                t.push("success", f"Added to {title}", "", "Undo", lambda: self._collections.removeSelection(cid, self._row_of(path)))
        elif key == "cpath":
            self._clipboard(path)
            t.push("success", "Path copied")
        elif key == "cname":
            self._clipboard(Path(path).name)
            t.push("success", "File name copied", Path(path).name)
        elif key == "reveal":
            if not self._memory:
                from ..shell_actions import reveal_in_file_explorer

                reveal_in_file_explorer(path)
        elif key == "del":
            t.push("warn", "Deleting isn’t connected yet", "Nothing was changed. The file is where it was.")
        elif key == "flagwhy":
            self.uiRequest.emit("review", path)
        elif key == "flagrestore":
            self._cull.restorePath(path, True)
            t.push("success", f"Restored {name}", "It is no longer flagged.", "Undo", lambda: self._cull.restorePath(path, False))
        elif key == "refresh":
            if self._photos.folderPath:
                self._photos.loadFolder(self._photos.folderPath)
            else:
                t.push("info", "Open a folder first")
        elif key == "clear":
            self.uiRequest.emit("clearFilters", 0)
        elif key == "freveal":
            if not self._memory and self._photos.folderPath:
                QDesktopServices.openUrl(QUrl.fromLocalFile(self._photos.folderPath))
        elif key == "fpath":
            self._clipboard(self._photos.folderPath)
            t.push("success", "Folder path copied")
        elif key.startswith("view:") or key.startswith("dens:"):
            self.uiRequest.emit("view", key)
        elif key.startswith("sort:"):
            self._photos.setSort(key[5:])
            self.uiRequest.emit("sort", key[5:])

    # -- test hook: the mockup's scenes -------------------------------------------------------------------------------------------
    @Slot(str, result="QVariantMap")
    def scene(self, name: str) -> dict:
        """Opens the menu or toasts of a mockup scene; returns where the window should put it ({kind, row, x, y, sub})."""
        t = self._toasts
        t.setStill(True)
        by_name = {self._photos.get(i).get("name"): i for i in range(self._photos.shown)}
        if name == "cx-flag":
            self._cull.scene("cu-grid")
        spec = {"cx-photo": ("photo", "DSC_4203", 700, 110, ""), "cx-coll": ("photo", "DSC_4203", 700, 110, "coll"), "cx-with": ("photo", "DSC_4203", 700, 110, "with"),
                "cx-move": ("photo", "DSC_4203", 700, 110, "mv"), "cx-flag": ("photo", "DSC_4210", 960, 250, ""), "cx-bg": ("folder", "", 560, 760, ""), "cx-sort": ("folder", "", 560, 600, "sort")}.get(name)
        if name == "tt-undo":
            t.push("success", "Marked DSC_4210 as Reject", "", "Undo")
        elif name == "tt-stack":
            t.push("success", "Added to Album selects", "", "Undo")
            t.push("info", "Added DSC_4203 to PocketDrop", "Scan the code on your phone to download it.", "Show")
            t.push("error", "Couldn’t open in Photoshop", "Photoshop wasn’t found. Set its location in Settings.", "Open settings")
        elif name == "tt-warn":
            t.push("warn", "This folder is read-only", "Decisions are kept for this session only.", "Learn more")
            t.push("success", "Path copied")
        if spec is None:
            return {"kind": ""}
        kind, photo, x, y, sub = spec
        return {"kind": kind, "row": by_name.get(photo, 0), "x": x, "y": y, "sub": sub}
