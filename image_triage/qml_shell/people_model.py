"""QML people state over the shared face-group and naming stores."""
from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

from PySide6.QtCore import Property, QObject, Signal, Slot

from ..face_groups import MAX_FACE_GROUPS, MIN_FACE_COUNT
from .photos import member_key

_DEMO = (
    ("p1", "Anna", 214, 20), ("p2", "Mark", 187, 150), ("p3", "Lena", 142, 260), ("p4", "Jonas", 96, 330), ("p5", "", 61, 200),
    ("p6", "", 44, 80), ("p7", "", 38, 300), ("p8", "", 29, 110), ("p9", "", 22, 40), ("p10", "", 17, 180), ("p11", "", 9, 350),
    ("p12", "", 6, 230), ("p13", "", 1, 60), ("p14", "", 1, 120),
)


def _hue(ident) -> int:
    digits = "".join(ch for ch in str(ident) if ch.isdigit()) or "0"
    return (int(digits) * 47 + 20) % 360


def photos_of_person(name: str, folders, db_for) -> list[str]:
    """The photos a person called ``name`` appears in, across ``folders``. ``db_for(folder)`` gives a folder's face index or None.
    A folder that has not been analysed, or whose index cannot be read, simply contributes nothing."""
    from ..face_groups import face_group_photo_paths

    wanted, found, seen = name.strip().casefold(), [], set()
    if not wanted:
        return found
    for folder in folders:
        try:
            db = db_for(folder)
            if db is None:
                continue
            clusters = [cluster for person in _read_people(db) if person["name"].strip().casefold() == wanted for cluster in person["clusters"]]
            for path in face_group_photo_paths(db, clusters):
                if path not in seen:
                    seen.add(path)
                    found.append(path)
        except Exception:
            continue
    return found


def _read_people(db: Path) -> list[dict]:
    """Everyone the folder's face index holds who is not ignored, most photographed first; clusters with one name are one person."""
    from ..people_search import list_person_clusters

    connection = sqlite3.connect(str(db), timeout=5)
    try:
        connection.execute("PRAGMA busy_timeout=5000")
        by_name: dict[str, dict] = {}
        people: list[dict] = []
        for cluster in list_person_clusters(connection):
            name = cluster.name.strip()
            if name and name.casefold() in by_name:
                person = by_name[name.casefold()]
                person["clusters"].append(cluster.cluster_id)
                person["count"] += cluster.face_count
                continue
            person = {"id": str(cluster.cluster_id), "name": name, "count": cluster.face_count, "clusters": [cluster.cluster_id], "ignored": False}
            if name:
                by_name[name.casefold()] = person
            people.append(person)
        for person in people:
            person["hue"] = _hue(person["id"])
        people.sort(key=lambda p: p["count"], reverse=True)
        return people
    except sqlite3.DatabaseError:
        return []
    finally:
        connection.close()


class PeopleModel(QObject):
    revisionChanged = Signal()
    _read = Signal(int, str, object, object)        # the index was read off the interface thread: (token, folder, its file or None, the people)
    said = Signal(str, str, str)                    # something to tell the person: kind, title, body (the side panel shows it as a toast)
    _everywhere = Signal(int, str, object, int)     # token, the person's name, their photos in every folder, how many folders were looked in

    def __init__(self, photos, memory: bool = False, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._photos = photos
        self._memory = memory
        self._people: list[dict] = []       # everyone not ignored: {id, name, count, clusters}
        self._demo = [{"id": i, "name": n, "count": c, "hue": h, "clusters": [i], "ignored": False} for i, n, c, h in _DEMO]
        self._db: Path | None = None
        self._query = ""
        self._revision = 0
        self._read_token = 0                # the newest read asked for: an older one that answers later is dropped
        self._folder = ""                   # the folder `_people` belongs to
        self._read.connect(self._apply_read)
        self._everywhere.connect(self._show_everywhere)
        self._everywhere_token = 0
        self._library_folders = None                 # tests name the folders to look in
        photos.folderChanged.connect(self.reload)
        self.reload()

    # -- the index --------------------------------------------------------------------------------------------------------------------
    @staticmethod
    def _db_path(folder: str) -> Path | None:
        if not folder:
            return None
        try:
            from ..aiculler_workflow import aiculler_db_path, build_aiculler_workflow_paths

            path = aiculler_db_path(build_aiculler_workflow_paths(folder, resolve=False))
        except Exception:
            return None
        return path if path.exists() else None

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self._db), timeout=5)
        connection.execute("PRAGMA busy_timeout=5000")
        return connection

    @Slot()
    def reload(self) -> None:
        """Read the open folder's face index again: when the folder changes, after an analysis has written it, after a name is changed.

        Finding the index asks the disk whether a file exists and reading it waits up to five seconds for a writer to let go, so both
        happen on a thread; the people appear when it answers."""
        if self._memory:
            self._people = [p for p in self._demo if not p["ignored"]]
            self._revision += 1
            self.revisionChanged.emit()
            return
        folder = self._photos.folderPath
        self._read_token += 1
        token = self._read_token
        if folder != self._folder:
            # Another folder: the last folder's people are not this one's. (The same folder keeps what it shows until the new read is in.)
            self._folder, self._db, self._people = folder, None, []
            self._revision += 1
            self.revisionChanged.emit()
        if not folder:
            return                          # no folder is open: there is nothing to read

        def work() -> None:
            db = self._db_path(folder)
            try:
                people = _read_people(db) if db is not None else []
            except Exception:
                people = []
            self._read.emit(token, folder, db, people)

        threading.Thread(target=work, name="qml-people", daemon=True).start()

    def _apply_read(self, token: int, folder: str, db, people) -> None:
        if token != self._read_token:
            return
        self._folder, self._db, self._people = folder, db, people
        self._revision += 1
        self.revisionChanged.emit()

    revision = Property(int, lambda self: self._revision, notify=revisionChanged)
    hasIndex = Property(bool, lambda self: self._memory or self._db is not None, notify=revisionChanged)

    @staticmethod
    def _card(p: dict) -> dict:
        return {"id": p["id"], "name": p["name"], "count": p["count"], "hue": p["hue"], "init": p["name"][:1].upper() if p["name"] else "?"}

    # -- the sidebar ----------------------------------------------------------------------------------------------------------------------
    @Slot(str)
    def setQuery(self, text: str) -> None:
        self._query = text.strip().casefold()
        self._revision += 1
        self.revisionChanged.emit()

    @Slot(result="QVariantList")
    def sidebar(self) -> list:
        """The most photographed people, named ones first (the Widgets panel's rule), filtered by the search box."""
        recurring = [p for p in self._people if p["count"] >= MIN_FACE_COUNT]
        recurring.sort(key=lambda p: (not p["name"], -p["count"]))
        shown = [p for p in recurring if not self._query or self._query in (p["name"] or "Unnamed").casefold()]
        return [self._card(p) for p in shown[:MAX_FACE_GROUPS]]

    @Slot(result=str)
    def emptyText(self) -> str:
        if not self.hasIndex:
            return "Face groups unavailable\nFolder not indexed"
        if self._query and any(p["count"] >= MIN_FACE_COUNT for p in self._people):
            return "No matching people\nin this folder"
        return "No recurring faces\nin this folder"

    @Slot(result="QVariantMap")
    def indexInfo(self) -> dict:
        """The Face index block of the Faces page: a meter, a line of text, a percentage (sample data in --mock)."""
        if self._memory:
            return {"meter": True, "value": 0.86, "text": "1,104 of 1,284 photos scanned", "pct": "86%"}
        # The index does not say how much of the folder it has seen, so there is no share to draw: `meter` is false and the value is
        # nothing, not a full bar.
        n = len(self._people)
        return {"meter": False, "value": 0.0, "text": f"{n} {'person' if n == 1 else 'people'} found in this folder", "pct": ""}

    # The same block as properties, so a binding follows the folder and a new analysis without asking again by hand.
    indexShare = Property(float, lambda self: self.indexInfo()["value"] if self.indexInfo()["meter"] else -1.0, notify=revisionChanged)   # -1: not known, draw no meter
    indexText = Property(str, lambda self: self.indexInfo()["text"], notify=revisionChanged)
    indexPct = Property(str, lambda self: self.indexInfo()["pct"], notify=revisionChanged)

    @Slot(str)
    def openPerson(self, pid: str) -> None:
        person = next((p for p in self._people if p["id"] == pid), None)
        if person is None:
            return
        if self._memory:
            rows = self._photos.collect()
            n = min(8, max(2, round(person["count"] / 30)))
            ids = sorted({(k * 5 + int("".join(c for c in pid if c.isdigit()) or 0) * 3) % max(1, len(rows)) for k in range(n)})
            keys = {rows[i]["name"] for i in ids if rows}
            pred = lambda r, k=keys: r["name"] in k
        else:
            from ..face_groups import face_group_photo_paths

            # The same cheap key on both sides (the folder resolved once, not every photo): this runs for every photo each time the grid is filtered.
            keys = {member_key(p) for p in face_group_photo_paths(self._db, person["clusters"])}
            pred = lambda r, k=keys: (r.get("key") or member_key(r["path"])) in k
        self._photos.set_scope("person", pid, pred)

    @Slot(str)
    def showEverywhere(self, pid: str) -> None:
        """Show a named person's photos from every folder of the library together in the grid, not only the open folder's.
        A person is known across folders by name, so this is for people who have one. The folders are read on a thread."""
        person = next((p for p in self._people if p["id"] == pid), None)
        if person is None or self._memory:
            return
        name = person["name"].strip()
        if not name:
            self.said.emit("info", "Name this person first", "People are recognised across folders by their name.")
            return
        self._everywhere_token += 1
        token, here, listed = self._everywhere_token, self._folder, self._library_folders

        def work() -> None:
            try:
                if listed is None:
                    from ..library_store import LibraryStore

                    folders = LibraryStore().indexed_folders()
                else:
                    folders = list(listed)
            except Exception:
                folders = []
            folders = list(dict.fromkeys([here, *folders])) if here else folders
            paths = photos_of_person(name, folders, self._db_path)
            try:
                self._everywhere.emit(token, name, paths, len(folders))
            except RuntimeError:
                pass                                # the model was destroyed meanwhile

        threading.Thread(target=work, name="qml-person-everywhere", daemon=True).start()

    def _show_everywhere(self, token: int, name: str, paths, looked_in: int) -> None:
        if token != self._everywhere_token:
            return
        if not paths:
            self.said.emit("info", f"No photos of {name} were found", "Folders are searched once they have been analysed for faces.")
            return
        if looked_in <= 1:
            self.said.emit("info", "Only this folder was searched", "Add folders in Browse Library to find this person in them too.")
        self._photos.loadPaths(paths, name, "person")

    @Slot(str, result=str)
    def labelOf(self, pid: str) -> str:
        """The grid header's wording for this person (the Widgets filter chip)."""
        p = next((x for x in self._people if x["id"] == pid), None)
        if p is None:
            return ""
        return p["name"] or f"Unnamed face ({p['count']} photo{'' if p['count'] == 1 else 's'})"

    @Slot(str, result="QVariantMap")
    def card(self, pid: str) -> dict:
        p = next((x for x in self._people if x["id"] == pid), None)
        return self._card(p) if p else {}

    # -- the Tag People window --------------------------------------------------------------------------------------------------------------
    @Slot(str, bool, bool, result="QVariantList")
    def listPeople(self, query: str, unnamed_only: bool, include_singles: bool) -> list:
        needle = query.strip().casefold()
        out = []
        for p in self._people:
            if not include_singles and p["count"] < 2:
                continue
            if unnamed_only and p["name"]:
                continue
            if needle and needle not in p["name"].casefold():
                continue
            out.append(self._card(p))
        return out

    @Slot(result="QVariantMap")
    def stats(self) -> dict:
        named = sum(1 for p in self._people if p["name"])
        return {"named": named, "unnamed": len(self._people) - named, "photos": sum(p["count"] for p in self._people)}

    def _write(self, action, named=()) -> None:
        """Change the folder's people. `named` are the clusters the person has just named by hand: the app's store of named people
        (people_identities.py, when it is on) learns who this folder names, so the next folder can recognise them."""
        if self._memory:
            action(None)
        elif self._db is not None:
            connection = self._connect()
            try:
                action(connection)
                connection.commit()
                try:
                    from .. import people_identities

                    if people_identities.active() is not None:
                        people_identities.confirm_names(connection, named)
                        people_identities.remember_folder_names(connection, self._folder)
                        connection.commit()
                except Exception:               # carrying names across folders is extra: the folder's own names are already saved
                    pass
            finally:
                connection.close()
        self.reload()

    def _ids_for(self, pids: list) -> list[int]:
        return [c for p in self._people if p["id"] in set(map(str, pids)) for c in p["clusters"]]

    @Slot(str, str)
    def name(self, pid: str, text: str) -> None:
        clean = " ".join(text.split())
        if not clean:
            return
        self.nameAll([pid], clean)

    @Slot("QVariantList", str)
    def nameAll(self, pids: list, text: str) -> None:
        """Gives these people one name (renaming, and "merge as same person": clusters with one name are one person)."""
        clean = " ".join(text.split())
        ids = self._ids_for(pids)
        if self._memory:
            targets = [p for p in self._demo if p["id"] in set(map(str, pids))]
            total = sum(p["count"] for p in targets)
            for p in targets:
                p["name"] = clean
            if len(targets) > 1 and clean:      # merging: the first stays, carrying everyone's photos
                keep = targets[0]
                keep["count"] = total
                for p in targets[1:]:
                    p["ignored"] = True
            self.reload()
            return
        from ..people_search import assign_person_names

        self._write(lambda c: assign_person_names(c, ids, clean), named=ids)

    @Slot("QVariantList")
    def clearNames(self, pids: list) -> None:
        if self._memory:
            for p in self._demo:
                if p["id"] in set(map(str, pids)):
                    p["name"] = ""
            self.reload()
            return
        from ..people_search import assign_person_names

        ids = self._ids_for(pids)
        self._write(lambda c: assign_person_names(c, ids, ""), named=ids)

    @Slot("QVariantList", result="QVariantList")
    def ignore(self, pids: list) -> list:
        """Hides these people (from this window and from people search); returns what `restore` needs to bring them back."""
        if self._memory:
            for p in self._demo:
                if p["id"] in set(map(str, pids)):
                    p["ignored"] = True
            self.reload()
            return [str(p) for p in pids]
        from ..people_search import set_clusters_ignored

        ids = self._ids_for(pids)
        self._write(lambda c: set_clusters_ignored(c, ids, True))
        return ids

    @Slot("QVariantList")
    def restore(self, hidden: list) -> None:
        if self._memory:
            for p in self._demo:
                if p["id"] in set(map(str, hidden)):
                    p["ignored"] = False
            self.reload()
            return
        from ..people_search import set_clusters_ignored

        ids = [int(h) for h in hidden]
        self._write(lambda c: set_clusters_ignored(c, ids, False))

    @Slot("QVariantList", result=int)
    def photoCount(self, pids: list) -> int:
        return sum(p["count"] for p in self._people if p["id"] in set(map(str, pids)))
