"""Collections for the QML shell: the shared store, plus an icon for each.

Collections are the shared `LibraryStore` (library.sqlite3): the same collections, the same members, the same colours, and "New
winners go here" is the same setting (`collections/also_add_to`). A collection can also carry an icon from a small built-in library
(mountain, rings, ...); the Widgets store has no field for it, so the icons are kept beside it in the settings (`collections/icons`, a JSON
map of collection id to icon name) and the Widgets app does not show them.

Members are file paths. The grid only holds the open folder's photos, so opening a collection shows its members that are in that folder
(a collection can span folders; the rest are not listed). `memory=True` (--mock, tests) uses a small in-memory set shaped like the mockup.
"""
from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import Property, QObject, Signal, Slot

from ..app_identity import user_settings
from ..formats import IMAGE_SUFFIXES
from .photos import member_key

ACTIVE_KEY = "collections/also_add_to"          # catalog_controller.ACTIVE_COLLECTION_KEY
ICONS_KEY = "collections/icons"
RECENT_KEY = "collections/picker_recent"
COLORS = (("", "No color"), ("#4c8dff", "Blue"), ("#3ccf8e", "Green"), ("#e0b45a", "Amber"), ("#ff6b7d", "Red"), ("#c58af9", "Purple"), ("#38c6d9", "Teal"))
# The icon library: key, what it is called, 24 x 24 stroke path.
ICONS = (
    ("mountain", "Landscape", "M3 19l6.5-11 4.5 7.5 2.5-4L21 19z"),
    ("ring", "Wedding", "M12 21a4.5 4.5 0 100-9 4.5 4.5 0 000 9zM9.5 3.5h5L17 6.5l-5 5-5-5zM7 6.5h10M10.5 3.5l-1 3 2.5 5 2.5-5-1-3"),
    ("heart", "Love", "M12 20s-7.5-4.6-7.5-10.2A4.3 4.3 0 0112 7.4a4.3 4.3 0 017.5 2.4C19.5 15.4 12 20 12 20z"),
    ("camera", "Camera", "M4 8h3l1.5-2.5h7L17 8h3v11H4zM12 16.5a3.5 3.5 0 100-7 3.5 3.5 0 000 7z"),
    ("portrait", "Portrait", "M12 11a3.5 3.5 0 100-7 3.5 3.5 0 000 7zM5 20c.5-3.7 3.3-6 7-6s6.5 2.3 7 6"),
    ("family", "Family", "M16 11a3 3 0 100-6 3 3 0 000 6zM8 12a3 3 0 100-6 3 3 0 000 6zM2.5 19c.4-2.6 2.6-4.2 5.5-4.2s5.1 1.6 5.5 4.2M14 15.2c.7-.3 1.4-.4 2-.4 2.5 0 4.5 1.5 5 4"),
    ("travel", "Travel", "M3 12l18-8-5 17-4-6.5zM12 14.5L21 4"),
    ("sun", "Day", "M12 16a4 4 0 100-8 4 4 0 000 8zM12 3v2M12 19v2M3 12h2M19 12h2M5.6 5.6L7 7M17 17l1.4 1.4M5.6 18.4L7 17M17 7l1.4-1.4"),
    ("moon", "Night", "M20 14.5A8 8 0 019.5 4a8 8 0 1010.5 10.5z"),
    ("water", "Water", "M3 9c2.5-2 4.5 2 7 0s4.5 2 7 0 2.5-1 4 0M3 15c2.5-2 4.5 2 7 0s4.5 2 7 0 2.5-1 4 0"),
    ("tree", "Nature", "M5 19c0-8 5-14 14-14 0 9-6 14-14 14zM5 19l7-7"),
    ("building", "Architecture", "M5 20V8l7-4 7 4v12zM9 20v-5h6v5M9 11h.01M12 11h.01M15 11h.01"),
    ("city", "City", "M4 20V10h5v10M9 20V5h6v15M15 20v-8h5v8"),
    ("paw", "Wildlife", "M12 19c-3 0-5-2-4-4s2-3 4-3 3 1 4 3-1 4-4 4zM7 11a1.5 1.5 0 100-3 1.5 1.5 0 000 3zM10.5 8a1.5 1.5 0 100-3 1.5 1.5 0 000 3zM14 8a1.5 1.5 0 100-3 1.5 1.5 0 000 3zM17.5 11a1.5 1.5 0 100-3 1.5 1.5 0 000 3z"),
    ("ball", "Sports", "M8 4h8v5a4 4 0 01-8 0zM8 6H5a3 3 0 003 4M16 6h3a3 3 0 01-3 4M12 13v4M9 20h6M10 17h4"),
    ("food", "Food", "M7 3v8M5 3v5a2 2 0 004 0V3M7 11v10M17 3c-2 2-2.5 5-2.5 8H17v10M17 3v18"),
    ("flower", "Macro", "M12 14a2 2 0 100-4 2 2 0 000 4zM12 10c-1-3 0-6 0-6s1 3 0 6M12 14c1 3 0 6 0 6s-1-3 0-6M10 12c-3 1-6 0-6 0s3-1 6 0M14 12c3-1 6 0 6 0s-3 1-6 0"),
    ("party", "Event", "M5 21l4-12 8 8zM14 5l1-2M17 8l2-1M19 12l2 .5"),
    ("print", "Print", "M7 9V4h10v5M7 17H5V9h14v8h-2M7 14h10v7H7z"),
    ("album", "Album", "M6 4h12v16H6zM6 4v16M10 8h5"),
    ("star", "Favorite", "M12 4l2.4 5 5.4.6-4 3.7 1.1 5.4L12 16l-4.9 2.7 1.1-5.4-4-3.7 5.4-.6z"),
    ("flag", "Flag", "M5 21V4M5 4h11l-2 4 2 4H5"),
    ("film", "Film", "M4 5h16v14H4zM8 5v14M16 5v14M4 9h4M4 15h4M16 9h4M16 15h4"),
    ("pin", "Places", "M12 21s-6.5-6-6.5-11a6.5 6.5 0 0113 0c0 5-6.5 11-6.5 11zM12 12.5a2.5 2.5 0 100-5 2.5 2.5 0 000 5z"),     # the last tile: where the mockup had a "+"
)
_ICON_PATH = {key: path for key, _label, path in ICONS}
# What --mock shows: (id, name, colour, icon, member photo names, also add winners here)
_DEMO = (
    ("c1", "Album selects", "#4c8dff", "", ("DSC_4203", "DSC_4214", "DSC_4222", "DSC_4226", "DSC_4240", "DSC_4244"), True),
    ("c2", "Print order", "#3ccf8e", "print", ("DSC_4203", "DSC_4222", "DSC_4240"), False),
    ("c3", "Landscapes", "#c58af9", "mountain", ("DSC_4201", "DSC_4203", "DSC_4207", "DSC_4222", "DSC_4231", "DSC_4240"), False),
    ("c4", "Wedding", "#ff6b7d", "ring", ("DSC_4210", "DSC_4219"), False),
    ("c5", "Client proofs", "", "", (), False),
)


def icon_path(key: str) -> str:
    return _ICON_PATH.get(key, "")


class CollectionsModel(QObject):
    revisionChanged = Signal()
    exportRequested = Signal()              # open the Deliver / Handoff Builder on the collection that was just opened

    def __init__(self, photos, memory: bool = False, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._photos = photos
        self._memory = memory
        self._store = None
        self._settings = None if memory else user_settings()
        self._icons: dict[str, str] = {}
        self._recent: list[str] = ["c1", "c3"] if memory else []
        self._active = "c1" if memory else ""
        self._mem: dict[str, dict] = {}
        self._rows: list[dict] = []
        self._members: dict[str, set[str]] = {}
        self._previews: dict[str, list[str]] = {}
        self._headers: list[dict] = []
        self._here: dict[str, list[dict]] = {}      # collection id -> the open folder's photos that are in it (see `_match_folder`)
        self._matched_rows: list | None = None      # the photo list `_here` was worked out for, and how long it was
        self._matched_count = -1
        self._revision = 0
        if memory:
            self._mem = {c[0]: {"id": c[0], "name": c[1], "color": c[2], "icon": c[3], "members": list(c[4])} for c in _DEMO}
            self._order = [c[0] for c in _DEMO]
        else:
            self._icons = self._load_json(ICONS_KEY)
            self._recent = list(self._load_json(RECENT_KEY, []))
            self._active = str(self._settings.value(ACTIVE_KEY, "", str) or "")
        photos.countsChanged.connect(self._photos_changed)
        self._reload()

    # -- store -------------------------------------------------------------------------------------------------------------------
    def _load_json(self, key: str, default=None):
        try:
            return json.loads(str(self._settings.value(key, "", str) or "")) if self._settings.value(key, "", str) else ({} if default is None else default)
        except ValueError:
            return {} if default is None else default

    def _db(self):
        if self._store is None:
            from ..library_store import LibraryStore

            self._store = LibraryStore()
        return self._store

    def _key(self, path: str, name: str) -> str:
        return name if self._memory else member_key(path)

    def _row_key(self, row: dict) -> str:
        """A photo's key among the members. A photo read from a folder carries it from the read; any other gets it the first time it is asked for."""
        if self._memory:
            return row["name"]
        key = row.get("key")
        if key is None:
            key = row["key"] = member_key(row["path"])
        return key

    def _read_members(self, store, cid: str, paths) -> None:
        """Take in one collection's stored paths: who is in it, and its three newest pictures."""
        self._members[cid] = {member_key(p) for p in paths}
        seen = set()
        preview = []
        for path in store.recent_collection_paths(cid):
            image = Path(path)
            key = member_key(str(image.with_suffix("")))
            if image.suffix.lower() not in IMAGE_SUFFIXES or key in seen:
                continue
            seen.add(key)
            preview.append(path)
            if len(preview) == 3:
                break
        self._previews[cid] = preview

    def _reload(self) -> None:
        """Re-read the collections (names, colours, who is in them) and rebuild the rows."""
        self._members = {}
        self._previews = {}
        headers: list[dict] = []
        if self._memory:
            for cid in self._order:
                c = self._mem[cid]
                self._members[cid] = set(c["members"])
                headers.append({"id": cid, "name": c["name"], "color": c["color"], "icon": c["icon"], "count": len(c["members"])})
        else:
            store = self._db()
            for c in store.list_collections():
                full = store.load_collection(c.id)
                self._read_members(store, c.id, full.item_paths if full is not None else ())
                headers.append({"id": c.id, "name": c.name, "color": c.color, "icon": self._icons.get(c.id, ""), "count": c.item_count})
        self._headers = headers
        self._members_changed()

    def _reload_one(self, cid: str, full=None) -> None:
        """One collection's members changed (photos were added to it or taken out): read that one again, not every collection."""
        if self._memory:
            self._reload()
            return
        store = self._db()
        if full is None:
            full = store.load_collection(cid)
        if full is None:
            self._reload()                          # it is gone: start from what the store has
            return
        self._read_members(store, cid, full.item_paths)
        for header in self._headers:
            if header["id"] == cid:
                header["count"] = len(full.item_paths)
        self._members_changed()

    def _members_changed(self) -> None:
        self._matched_rows = None
        if self._photos.scopeKind == "collection" and self._photos.scopeId in self._members:
            # The open scope captured the old membership set. Rebind it after
            # edits so removed members disappear immediately from the grid.
            self.open(self._photos.scopeId)
        else:
            self._refresh_rows()

    def _match_folder(self) -> None:
        """Work out which of the open folder's photos are in which collection: one pass over the photos, each looked up by its key.

        This is the costly part of the rows, so it is done when the members or the folder's photos change and not when a decision does."""
        rows = self._photos.all_rows()
        owners: dict[str, list[str]] = {}
        for cid, keys in self._members.items():
            for key in keys:
                owners.setdefault(key, []).append(cid)
        here: dict[str, list[dict]] = {cid: [] for cid in self._members}
        if owners:
            key_of = self._row_key
            for row in rows:
                for cid in owners.get(key_of(row), ()):
                    here[cid].append(row)
        self._here = here
        self._matched_rows, self._matched_count = rows, len(rows)

    def _matched(self) -> bool:
        """Is `_here` still about the photos the model holds? Its list is replaced when the folder is read or photos leave it."""
        rows = self._photos.all_rows()
        return rows is self._matched_rows and len(rows) == self._matched_count

    def _photos_changed(self) -> None:
        """The photo model's counts changed: a decision (most often), a filter, or the folder's photos themselves."""
        if not self._matched():
            self._refresh_rows()
            return
        self._revision += 1                         # nobody joined or left: only what is counted from the decisions (the winners) is stale
        self.revisionChanged.emit()

    def _refresh_rows(self) -> None:
        rows = []
        self._match_folder()
        for h in self._headers:
            mine = self._here.get(h["id"], [])
            thumbs = [r["path"] for r in mine[:3]]
            while thumbs and len(thumbs) < 3:
                thumbs.append(thumbs[len(thumbs) % len(mine[:3])])
            if self._memory:
                by_name = {r["name"]: r["path"] for r in mine}
                preview = [by_name[name] for name in reversed(self._mem[h["id"]]["members"]) if name in by_name][:3]
            else:
                preview = self._previews.get(h["id"], [])
            rows.append(dict(h, active=h["id"] == self._active, here=len(mine), thumbs=thumbs + [""] * (3 - len(thumbs)), previewThumbs=preview, iconPath=icon_path(h["icon"])))
        self._rows = rows
        self._revision += 1
        self.revisionChanged.emit()

    revision = Property(int, lambda self: self._revision, notify=revisionChanged)
    rows = Property("QVariantList", lambda self: self._rows, notify=revisionChanged)
    winnersCount = Property(int, lambda self: self._photos.winCount if self._photos.scopeKind != "winners" else self._photos.shown, notify=revisionChanged)

    @Slot(result="QVariantList")
    def colors(self) -> list:
        return [{"value": v, "label": n} for v, n in COLORS]

    @Slot(result="QVariantList")
    def icons(self) -> list:
        return [{"key": k, "label": label, "path": path} for k, label, path in ICONS]

    @Slot(str, result=str)
    def iconPathOf(self, key: str) -> str:
        return icon_path(key)

    def member_rows(self, cid: str) -> list[dict]:
        """The open folder's photos that are in this collection."""
        if not self._matched():
            self._match_folder()
        return [{"path": r["path"], "name": r["name"], "decision": r["decision"], "companions": tuple(r.get("companions", ()))} for r in self._here.get(cid, ())]

    def _find(self, cid: str) -> dict | None:
        return next((r for r in self._rows if r["id"] == cid), None)

    @Slot(str, result="QVariantMap")
    def get(self, cid: str) -> dict:
        return self._find(cid) or {}

    # -- create, edit, delete ---------------------------------------------------------------------------------------------------
    @Slot(str, str, result=str)
    def nameProblem(self, name: str, except_id: str = "") -> str:
        """Why this name cannot be saved ("" when it can): the Widgets dialog's rules."""
        clean = " ".join(name.split())
        if not clean:
            return "Give it a name."
        if any(r["id"] != except_id and " ".join(r["name"].split()).casefold() == clean.casefold() for r in self._rows):
            return f"You already have a collection called “{clean}”."
        return ""

    @Slot(str, str, str, str, bool, result=str)
    def save(self, cid: str, name: str, color: str, icon: str, winners: bool) -> str:
        """Create (cid empty) or update a collection; returns its id. `winners` = "New winners go here"."""
        clean = " ".join(name.split())
        if self.nameProblem(clean, cid):
            return ""
        if self._memory:
            if cid:
                self._mem[cid].update(name=clean, color=color, icon=icon)
            else:
                cid = f"c{len(self._mem) + 1}"
                while cid in self._mem:
                    cid += "x"
                self._mem[cid] = {"id": cid, "name": clean, "color": color, "icon": icon, "members": []}
                self._order.append(cid)
        else:
            from ..library_store import VirtualCollection

            store = self._db()
            if cid:
                old = store.load_collection_header(cid)
                store.update_collection(VirtualCollection(id=cid, name=clean, description=old.description if old else "", kind=old.kind if old else "Custom", color=color))
            else:
                cid = store.create_collection(name=clean, color=color).id
            icons = dict(self._icons)
            if icon:
                icons[cid] = icon
            else:
                icons.pop(cid, None)
            self._icons = icons
            self._settings.setValue(ICONS_KEY, json.dumps(icons))
        self._set_active(cid, winners)
        self._reload()
        return cid

    @Slot(str)
    def remove(self, cid: str) -> None:
        if self._memory:
            self._mem.pop(cid, None)
            self._order = [c for c in self._order if c != cid]
        else:
            self._db().delete_collection(cid)
            self._icons.pop(cid, None)
            self._settings.setValue(ICONS_KEY, json.dumps(self._icons))
        if self._active == cid:
            self._set_active(cid, False)
        if self._photos.scopeKind == "collection" and self._photos.scopeId == cid:
            self._photos.set_scope()
        self._reload()

    # -- "New winners go here" -----------------------------------------------------------------------------------------------------
    def _set_active(self, cid: str, on: bool) -> None:
        if on:
            self._active = cid
        elif self._active == cid:
            self._active = ""
        if not self._memory:
            self._settings.setValue(ACTIVE_KEY, self._active)

    @Slot(str)
    def toggleActive(self, cid: str) -> None:
        self._set_active(cid, self._active != cid)
        self._refresh_rows()

    activeId = Property(str, lambda self: self._active, notify=revisionChanged)

    # -- members -----------------------------------------------------------------------------------------------------------------------
    def _rows_for(self, selection_row: int | None, visible: bool) -> list[dict]:
        p = self._photos
        if visible:
            return [p.get(i) for i in range(p.shown)]
        return [p.get(selection_row)] if selection_row is not None and 0 <= selection_row < p.shown else []

    def _add(self, cid: str, rows: list[dict]) -> int:
        rows = [r for r in rows if r]
        if not rows or self._find(cid) is None:
            return 0
        new = 0
        full = None
        if self._memory:
            members = self._mem[cid]["members"]
            for r in rows:
                if r["name"] not in members:
                    members.append(r["name"])
                    new += 1
        else:
            paths: list[str] = []
            for r in rows:
                paths.append(r["path"])
                paths.extend(r.get("companions", ()))
            before = len(self._members.get(cid, ()))
            full = self._db().add_paths_to_collection(cid, paths)        # it answers with the collection as it now is
            new = len({member_key(x) for x in (full.item_paths if full else ())}) - before
        recent = [cid] + [x for x in self._recent if x != cid]
        if recent != self._recent:
            self._recent = recent
            if not self._memory:
                self._settings.setValue(RECENT_KEY, json.dumps(self._recent[:8]))
        self._reload_one(cid, full)
        return new

    @Slot(str, int, result=int)
    def addSelection(self, cid: str, row: int) -> int:
        """Adds the photo the grid is on (and a RAW's companions); returns how many were new."""
        return self._add(cid, self._rows_for(row, False))

    @Slot(str, "QVariantList", result=int)
    def addRows(self, cid: str, rows: list) -> int:
        """Adds these rows of the grid in one step (one write, one re-read), where a call per row re-read the collection each time."""
        p = self._photos
        return self._add(cid, [p.get(int(row)) for row in rows if 0 <= int(row) < p.shown])

    @Slot(str, result=int)
    def addVisible(self, cid: str) -> int:
        return self._add(cid, self._rows_for(None, True))

    @Slot(str, int, result=bool)
    def hasRow(self, cid: str, row: int) -> bool:
        """Is the photo on this row of the grid in the collection?"""
        item = self._photos.row_at(row)
        return item is not None and self._row_key(item) in self._members.get(cid, ())

    @Slot(str, int)
    def removeSelection(self, cid: str, row: int) -> None:
        rows = [self._photos.get(index) for index in self._photos.selectionRows(row)]
        self._remove_rows(cid, rows)

    @Slot(str, int)
    def removeRow(self, cid: str, row: int) -> None:
        """Undo a single-photo addition without including a later multi-selection."""
        self._remove_rows(cid, self._rows_for(row, False))

    def _remove_rows(self, cid: str, rows: list[dict]) -> None:
        rows = [item for item in rows if item]
        if not rows or self._find(cid) is None:
            return
        if self._memory:
            self._mem[cid]["members"] = [m for m in self._mem[cid]["members"] if m not in {r["name"] for r in rows}]
        else:
            paths = [r["path"] for r in rows] + [c for r in rows for c in r.get("companions", ())]
            self._reload_one(cid, self._db().remove_paths_from_collection(cid, paths))
            return
        self._reload()

    @Slot(result="QVariantMap")
    def lastUsedCollection(self) -> dict:
        for cid in self._recent:
            collection = self._find(cid)
            if collection is not None:
                return collection
        return {}

    @Slot(result="QVariantList")
    def pickerEntries(self) -> list:
        """(id, name, count, recent) in the order the Widgets picker lists them: recent first, then A to Z."""
        def order(r):
            rank = self._recent.index(r["id"]) if r["id"] in self._recent else None
            return (rank is None, rank if rank is not None else 0, r["name"].casefold())

        return [{"id": r["id"], "name": r["name"], "count": r["count"], "recent": r["id"] in self._recent, "color": r["color"], "icon": r["icon"],
                 "iconPath": r["iconPath"]} for r in sorted(self._rows, key=order)]

    # -- opening one, and the badge on its photos -------------------------------------------------------------------------------------
    @Slot(str)
    def exportCollection(self, cid: str) -> None:
        """The collection menu's Export...: show the collection, then the Deliver / Handoff Builder takes the photos the grid shows."""
        if cid in self._members:
            self.open(cid)
            self.exportRequested.emit()

    @Slot(str)
    def open(self, cid: str) -> None:
        keys = self._members.get(cid)
        if keys is None:
            return
        self._photos.set_scope("collection", cid, lambda r, k=keys, key_of=self._row_key: key_of(r) in k)

    @Slot(str)
    def showAll(self, cid: str) -> None:
        """Show every photo of the collection in the grid, whatever folder it is in (`PhotoModel.loadPaths`), not only the open folder's."""
        header = next((h for h in self._headers if h["id"] == cid), None)
        if header is None:
            return
        if self._memory:
            if not self._matched():
                self._match_folder()
            paths = [r["path"] for r in self._here.get(cid, ())]         # the sample collections hold names: the photos shown that carry them
        else:
            full = self._db().load_collection(cid)
            paths = list(full.item_paths) if full is not None else []
        self._photos.loadPaths(paths, header["name"], "collection")

    @Slot()
    def openWinners(self) -> None:
        self._photos.set_scope("winners", "", lambda r: r["decision"] == "win")

    @Slot()
    def closeScope(self) -> None:
        self._photos.set_scope()

    @Slot(str, str, result="QVariantMap")
    def markFor(self, path: str, name: str) -> dict:
        """The badge a photo wears: the collection that gets new winners, when the photo is in it (only in the plain folder view)."""
        if not self._active or self._photos.scopeKind:
            return {}
        if self._key(path, name) not in self._members.get(self._active, ()):
            return {}
        row = self._find(self._active)
        return {"name": row["name"], "color": row["color"], "iconPath": row["iconPath"]} if row else {}
