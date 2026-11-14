"""Named people that carry across folders.

A folder's faces are clustered into people inside that folder's own database (`people_search`), so a person named in one folder used
to be a stranger in the next. This is a small store of named identities for the whole app: a name and a few representative
embeddings (the centre of that person's cluster in each folder where the person was named). When a folder's faces are clustered, its
unnamed clusters are compared with the store and take the name of the person they match.

Matching is deliberately cautious, because a wrong name is worse than no name:

* a cluster takes a name only when its best match is at or above `MATCH_THRESHOLD` **and** clearly ahead of the next person
  (`MATCH_MARGIN`); an ambiguous match is left unnamed;
* only embeddings of the same recognizer are compared (another recognizer is another space);
* a name already held by a cluster of the folder is never given to a second cluster (that would merge two people the folder keeps
  apart), two stored people are never merged, and of several clusters matching one person only the best takes the name;
* a name given by the store is marked as such in the folder and is not fed back into the store, so a mistake cannot reinforce itself.
  Naming the person by hand (in Tag People) confirms it.

Face embeddings are sensitive. The store lives in the app's data folder on this PC (`people/identities.sqlite3`), never beside the
photos, and nothing here is read by exports, hand-offs or PocketDrop. It holds no folder path (a folder is a hash of its path key).
It is consulted and written only while it is enabled (`enable`, called by the app at start; off in tests and tools), can be switched
off, and can be deleted (`clear`, the AI Center's "Forget everyone").

No Qt apart from finding the app's data folder. The thresholds are not validated on real faces here: no face model exists in the
environment this was written in. They sit above the in-folder clustering threshold (0.35 to 0.40) on purpose.
"""
from __future__ import annotations

import hashlib
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from .path_keys import path_key

MATCH_THRESHOLD = 0.50          # cosine between a cluster's centre and a stored representative, at or above which it may be that person
MATCH_MARGIN = 0.08             # ... and this far ahead of the best match with any other stored person
MAX_REPRESENTATIVES = 8         # per person and recognizer: one per folder, the most recently named folders


@dataclass(frozen=True)
class Match:
    name: str
    score: float
    runner_up: float            # the best score of any other person (-1 when there is none)


def default_path() -> Path:
    from PySide6.QtCore import QStandardPaths

    return Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation)) / "people" / "identities.sqlite3"


def source_key(folder: str) -> str:
    """A folder as the store knows it: a hash of its path key, so the store lists no folder paths."""
    return hashlib.sha256(path_key(str(folder)).encode("utf-8")).hexdigest()[:32] if folder else ""


def _unit(vector) -> np.ndarray:
    values = np.asarray(vector, dtype=np.float32).reshape(-1)
    norm = float(np.linalg.norm(values))
    return values / norm if norm else values


class IdentityStore:
    """The named people of this PC. Every method opens and closes its own connection: it is called from worker threads."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()

    # -- storage ------------------------------------------------------------------------------------------------------------------
    def _connect(self, create: bool) -> sqlite3.Connection | None:
        if not create and not self.path.exists():
            return None                             # reading never creates the file: a PC with nobody named has no store
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(str(self.path), timeout=5)
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute(
            """CREATE TABLE IF NOT EXISTS representatives (
                   name_key TEXT NOT NULL, name TEXT NOT NULL, model TEXT NOT NULL, source TEXT NOT NULL,
                   vector BLOB NOT NULL, dim INTEGER NOT NULL, updated_at TEXT NOT NULL,
                   PRIMARY KEY(name_key, model, source))""")
        return connection

    def sync_source(self, source: str, people: dict[tuple[str, str], object]) -> None:
        """Replace what one folder contributes: `people` is {(name, recognizer): centre of that person's cluster there}. A person the
        folder no longer names loses that folder's representative (and is forgotten when no folder is left)."""
        if not source:
            return
        with self._lock:
            connection = self._connect(create=bool(people))
            if connection is None:
                return
            try:
                stamp = datetime.now(timezone.utc).isoformat()
                with connection:
                    connection.execute("DELETE FROM representatives WHERE source = ?", (source,))
                    for (name, model), vector in people.items():
                        clean = " ".join(str(name).split())
                        values = _unit(vector)
                        if not clean or not values.size or not np.isfinite(values).all():
                            continue
                        connection.execute("INSERT OR REPLACE INTO representatives VALUES (?, ?, ?, ?, ?, ?, ?)",
                                           (clean.casefold(), clean, str(model), source, values.tobytes(), int(values.size), stamp))
                        # keep the newest few per person: a person named in a hundred folders does not grow without end
                        connection.execute(
                            """DELETE FROM representatives WHERE name_key = ? AND model = ? AND source NOT IN (
                                   SELECT source FROM representatives WHERE name_key = ? AND model = ? ORDER BY updated_at DESC LIMIT ?)""",
                            (clean.casefold(), str(model), clean.casefold(), str(model), MAX_REPRESENTATIVES))
            finally:
                connection.close()

    def forget_source(self, source: str) -> None:
        self.sync_source(source, {})

    def _rows(self, model: str | None = None) -> list[tuple[str, str, str, np.ndarray]]:
        with self._lock:
            connection = self._connect(create=False)
            if connection is None:
                return []
            try:
                query, params = "SELECT name_key, name, model, vector FROM representatives", ()
                if model is not None:
                    query, params = query + " WHERE model = ?", (str(model),)
                query += " ORDER BY updated_at ASC, rowid ASC"       # oldest first: a person is shown as they were first spelled
                return [(key, name, row_model, np.frombuffer(blob, dtype=np.float32)) for key, name, row_model, blob in connection.execute(query, params)]
            except sqlite3.DatabaseError:
                return []
            finally:
                connection.close()

    def names(self) -> list[str]:
        """Everyone remembered, A to Z."""
        seen: dict[str, str] = {}
        for key, name, _model, _vector in self._rows():
            seen.setdefault(key, name)
        return sorted(seen.values(), key=str.casefold)

    def clear(self) -> bool:
        """Forget everyone: the store's file is deleted. True when nothing of it is left."""
        with self._lock:
            for path in (self.path, self.path.with_name(self.path.name + "-wal"), self.path.with_name(self.path.name + "-shm"), self.path.with_name(self.path.name + "-journal")):
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
                except OSError:
                    return False
            return not self.path.exists()

    # -- matching -----------------------------------------------------------------------------------------------------------------
    def match(self, vector, model: str, *, threshold: float = MATCH_THRESHOLD, margin: float = MATCH_MARGIN) -> Match | None:
        """The stored person this centre is, or None when nobody is close enough or two people are too close to tell apart."""
        return self._match(_unit(vector), self._rows(model), threshold, margin)

    @staticmethod
    def _match(values: np.ndarray, rows, threshold: float, margin: float) -> Match | None:
        best: dict[str, tuple[float, str]] = {}
        spelled: dict[str, str] = {}
        for key, name, _model, stored in rows:
            spelled.setdefault(key, name)
            if stored.size != values.size or not values.size:
                continue
            score = float(np.dot(stored, values))
            if key not in best or score > best[key][0]:
                best[key] = (score, spelled[key])
        if not best:
            return None
        ranked = sorted(best.values(), key=lambda item: -item[0])
        score, name = ranked[0]
        runner_up = ranked[1][0] if len(ranked) > 1 else -1.0
        if score < threshold or score - runner_up < margin:
            return None
        return Match(name, score, runner_up)


# -- the one store the app uses --------------------------------------------------------------------------------------------------
_active: IdentityStore | None = None


def enable(path: str | Path | None = None) -> IdentityStore:
    """Turn the carrying of names across folders on for this process (the app does, at start). Nothing reads or writes the store before."""
    global _active
    _active = IdentityStore(path or default_path())
    return _active


def disable() -> None:
    global _active
    _active = None


def active() -> IdentityStore | None:
    return _active


# -- a folder's database ---------------------------------------------------------------------------------------------------------
def _ensure_auto_table(connection: sqlite3.Connection) -> None:
    connection.execute("CREATE TABLE IF NOT EXISTS face_identity_auto_names (cluster_id INTEGER PRIMARY KEY, name TEXT NOT NULL)")


def _clusters(connection: sqlite3.Connection) -> list[dict]:
    """The folder's clusters with the recognizer their faces were embedded by."""
    from .people_search import ensure_people_search_schema

    ensure_people_search_schema(connection)
    _ensure_auto_table(connection)
    rows = connection.execute(
        """SELECT c.cluster_id, c.name, c.ignored, c.centroid, c.dtype,
                  (SELECT f.identity_model FROM image_faces f WHERE f.cluster_id = c.cluster_id AND f.identity_model IS NOT NULL
                   GROUP BY f.identity_model ORDER BY COUNT(*) DESC LIMIT 1),
                  a.name
           FROM face_identity_clusters c LEFT JOIN face_identity_auto_names a ON a.cluster_id = c.cluster_id""").fetchall()
    out = []
    for cluster_id, name, ignored, blob, dtype, model, auto in rows:
        vector = np.frombuffer(blob, dtype=np.dtype(dtype or "float32")).astype(np.float32) if blob is not None else np.zeros(0, dtype=np.float32)
        out.append({"id": int(cluster_id), "name": str(name or ""), "ignored": bool(ignored), "vector": vector, "model": str(model or ""),
                    "auto": str(auto or "")})
    return out


def confirm_names(connection: sqlite3.Connection, cluster_ids) -> None:
    """The person named these clusters by hand: whatever the store had suggested for them is now their own choice."""
    _ensure_auto_table(connection)
    connection.executemany("DELETE FROM face_identity_auto_names WHERE cluster_id = ?", [(int(cluster_id),) for cluster_id in cluster_ids])


def remember_folder_names(connection: sqlite3.Connection, folder: str, store: IdentityStore | None = None) -> int:
    """Tell the store who this folder names: every cluster the person named (not ones the store named). Returns how many people."""
    store = store or active()
    if store is None or not folder:
        return 0
    people: dict[tuple[str, str], np.ndarray] = {}
    sums: dict[tuple[str, str], np.ndarray] = {}
    for cluster in _clusters(connection):
        if not cluster["name"] or cluster["ignored"] or not cluster["vector"].size or not cluster["model"]:
            continue
        if cluster["auto"] and cluster["auto"].casefold() == cluster["name"].casefold():
            continue                                # the store's own suggestion: not evidence for itself
        key = (cluster["name"], cluster["model"])
        sums[key] = sums.get(key, 0) + _unit(cluster["vector"])       # clusters sharing a name are one person here: one centre
    for key, total in sums.items():
        people[key] = _unit(total)
    store.sync_source(source_key(folder), people)
    return len(people)


def name_folder_clusters(connection: sqlite3.Connection, store: IdentityStore | None = None) -> list[tuple[int, str]]:
    """Give the folder's unnamed clusters the names of the stored people they match. Returns [(cluster id, name)] for what was named."""
    from .people_search import assign_person_names

    store = store or active()
    if store is None:
        return []
    clusters = _clusters(connection)
    taken = {cluster["name"].casefold() for cluster in clusters if cluster["name"]}
    rows_by_model: dict[str, list] = {}
    candidates: dict[str, tuple[float, int, str]] = {}      # name key -> (score, cluster id, name): the best cluster for each person
    for cluster in clusters:
        if cluster["name"] or cluster["ignored"] or not cluster["vector"].size or not cluster["model"]:
            continue
        rows = rows_by_model.setdefault(cluster["model"], store._rows(cluster["model"]))
        found = store._match(_unit(cluster["vector"]), rows, MATCH_THRESHOLD, MATCH_MARGIN)
        if found is None or found.name.casefold() in taken:
            continue                                # nobody, ambiguous, or a name this folder already gives to someone
        key = found.name.casefold()
        if key not in candidates or found.score > candidates[key][0]:
            candidates[key] = (found.score, cluster["id"], found.name)
    named = [(cluster_id, name) for _score, cluster_id, name in candidates.values()]
    for cluster_id, name in named:
        assign_person_names(connection, (cluster_id,), name)
        connection.execute("INSERT OR REPLACE INTO face_identity_auto_names(cluster_id, name) VALUES (?, ?)", (cluster_id, name))
    if named:
        connection.commit()
    return named


def after_clustering(connection: sqlite3.Connection, folder: str) -> list[tuple[int, str]]:
    """A folder's faces were just clustered: the store learns who the folder names, then names who it recognises. Never raises."""
    if active() is None:
        return []
    try:
        remember_folder_names(connection, folder)
        return name_folder_clusters(connection)
    except Exception:                               # recognising people across folders is extra: the folder's own index stands without it
        return []
