"""People for the QML shell: the faces PocketDrop-style panel and the Tag People window, on the Widgets app's own face index.

The face index of a folder is the Widgets app's `aiculler.sqlite` (one per folder, built by "Rescan Faces" / the AI scan). This reads and
writes it with the Widgets functions (`ui.face_groups.load_face_groups`, `face_group_photo_paths`, `people_search.assign_person_names`,
`set_clusters_ignored`), so a name given here shows up there. Grouping and ordering follow `PeopleSearchDialog._build_people`.

Not connected: **Rescan Faces** (building the index) and the face crops (each person is a coloured circle with an initial for now).
`memory=True` (--mock, tests) uses a small in-memory set shaped like the mockup.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from PySide6.QtCore import Property, QObject, Signal, Slot

from ..scanner import normalized_path_key
from ..ui.face_groups import MAX_FACE_GROUPS, MIN_FACE_COUNT

_DEMO = (
    ("p1", "Anna", 214, 20), ("p2", "Mark", 187, 150), ("p3", "Lena", 142, 260), ("p4", "Jonas", 96, 330), ("p5", "", 61, 200),
    ("p6", "", 44, 80), ("p7", "", 38, 300), ("p8", "", 29, 110), ("p9", "", 22, 40), ("p10", "", 17, 180), ("p11", "", 9, 350),
    ("p12", "", 6, 230), ("p13", "", 1, 60), ("p14", "", 1, 120),
)


def _hue(ident) -> int:
    digits = "".join(ch for ch in str(ident) if ch.isdigit()) or "0"
    return (int(digits) * 47 + 20) % 360


class PeopleModel(QObject):
    revisionChanged = Signal()

    def __init__(self, photos, memory: bool = False, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._photos = photos
        self._memory = memory
        self._people: list[dict] = []       # everyone not ignored: {id, name, count, clusters}
        self._demo = [{"id": i, "name": n, "count": c, "hue": h, "clusters": [i], "ignored": False} for i, n, c, h in _DEMO]
        self._db: Path | None = None
        self._query = ""
        self._revision = 0
        photos.folderChanged.connect(self.reload)
        self.reload()

    # -- the index --------------------------------------------------------------------------------------------------------------------
    def _db_path(self) -> Path | None:
        folder = self._photos.folderPath
        if not folder or self._memory:
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
        self._db = self._db_path()
        if self._memory:
            self._people = [p for p in self._demo if not p["ignored"]]
        elif self._db is None:
            self._people = []
        else:
            from ..people_search import list_person_clusters

            connection = self._connect()
            try:
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
                self._people = people
            except sqlite3.DatabaseError:
                self._people = []
            finally:
                connection.close()
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
            return {"value": 0.86, "text": "1,104 of 1,284 photos scanned", "pct": "86%"}
        n = len(self._people)
        return {"value": 1.0, "text": f"{n} {'person' if n == 1 else 'people'} found in this folder", "pct": ""}

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
            from ..ui.face_groups import face_group_photo_paths

            keys = {normalized_path_key(p) for p in face_group_photo_paths(self._db, person["clusters"])}
            pred = lambda r, k=keys: normalized_path_key(r["path"]) in k
        self._photos.set_scope("person", pid, pred)

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

    def _write(self, action) -> None:
        if self._memory:
            action(None)
        elif self._db is not None:
            connection = self._connect()
            try:
                action(connection)
                connection.commit()
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

        self._write(lambda c: assign_person_names(c, ids, clean))

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
        self._write(lambda c: assign_person_names(c, ids, ""))

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
