from __future__ import annotations

import sqlite3
from dataclasses import dataclass, replace
from datetime import datetime, timezone

import numpy as np


@dataclass(frozen=True, slots=True)
class PersonCluster:
    cluster_id: int
    name: str
    face_count: int
    centroid: tuple[float, ...]
    ignored: bool = False


@dataclass(frozen=True, slots=True)
class FaceIdentity:
    image_id: int
    face_index: int
    det_score: float
    embedding: tuple[float, ...]
    cluster_id: int | None = None
    source_path: str = ""


def ensure_people_search_schema(connection: sqlite3.Connection) -> None:
    _ensure_image_face_identity_columns(connection)
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS face_identity_clusters (
            cluster_id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL DEFAULT '',
            face_count INTEGER NOT NULL DEFAULT 0,
            centroid BLOB NOT NULL,
            dim INTEGER NOT NULL,
            dtype TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    columns = {row[1] for row in connection.execute("PRAGMA table_info(face_identity_clusters)")}
    if "ignored" not in columns:
        connection.execute(
            "ALTER TABLE face_identity_clusters ADD COLUMN ignored INTEGER NOT NULL DEFAULT 0"
        )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_face_identity_clusters_name ON face_identity_clusters(name)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_image_faces_cluster ON image_faces(cluster_id)"
    )


def cluster_face_identities(
    connection: sqlite3.Connection,
    *,
    threshold: float = 0.40,
    min_face_confidence: float = 0.0,
    identity_model: str | None = None,
) -> list[PersonCluster]:
    # 0.40 is calibrated for AuraFace (glintr100) embeddings; their same-person
    # cosine similarities run tighter than a typical ArcFace-r50 default (~0.62).
    # Validated on a real library: 0.40 kept the largest person cluster intact
    # while ~0.45+ began over-splitting one person into several clusters.
    #
    # ``identity_model`` scopes clustering to one recognizer's embeddings so
    # vectors written by a different recognizer never mix into the same space.
    ensure_people_search_schema(connection)
    if identity_model is not None:
        # Names given under another recognizer are remembered by where the face is, before anything below can drop their clusters.
        remember_named_faces(connection, identity_model)
    faces = list_face_identities(
        connection,
        min_face_confidence=min_face_confidence,
        identity_model=identity_model,
    )
    if not faces:
        with connection:
            connection.execute("UPDATE image_faces SET cluster_id = NULL")
            connection.execute("DELETE FROM face_identity_clusters")
        return []

    previous = _load_existing_clusters(connection)
    groups: list[list[FaceIdentity]] = []
    centroids: list[np.ndarray] = []
    for face in faces:
        vector = _normalize(np.asarray(face.embedding, dtype=np.float32))
        best_index = -1
        best_score = -1.0
        for index, centroid in enumerate(centroids):
            score = cosine_similarity(vector, centroid)
            if score > best_score:
                best_index = index
                best_score = score
        if best_index >= 0 and best_score >= threshold:
            groups[best_index].append(face)
            centroids[best_index] = _centroid([item.embedding for item in groups[best_index]])
        else:
            groups.append([face])
            centroids.append(vector)

    used_previous: set[int] = set()
    clusters: list[PersonCluster] = []
    stamp = datetime.now(timezone.utc).isoformat()
    with connection:
        connection.execute("UPDATE image_faces SET cluster_id = NULL")
        for group, centroid in zip(groups, centroids):
            cluster_id, name, ignored = _reuse_cluster(
                previous, used_previous, centroid, threshold=threshold
            )
            blob, dim, dtype = _vector_to_db(centroid)
            if cluster_id is None:
                cursor = connection.execute(
                    """
                    INSERT INTO face_identity_clusters(name, face_count, centroid, dim, dtype, updated_at)
                    VALUES('', ?, ?, ?, ?, ?)
                    """,
                    (len(group), blob, dim, dtype, stamp),
                )
                cluster_id = int(cursor.lastrowid)
                name = ""
                ignored = False
            else:
                connection.execute(
                    """
                    UPDATE face_identity_clusters
                    SET face_count = ?, centroid = ?, dim = ?, dtype = ?, updated_at = ?
                    WHERE cluster_id = ?
                    """,
                    (len(group), blob, dim, dtype, stamp, cluster_id),
                )
            connection.executemany(
                """
                UPDATE image_faces
                SET cluster_id = ?
                WHERE image_id = ? AND face_index = ?
                """,
                [(cluster_id, face.image_id, face.face_index) for face in group],
            )
            clusters.append(
                PersonCluster(
                    cluster_id=cluster_id,
                    name=name,
                    face_count=len(group),
                    centroid=tuple(float(value) for value in centroid),
                    ignored=ignored,
                )
            )
        connection.execute(
            """
            DELETE FROM face_identity_clusters
            WHERE cluster_id NOT IN (
                SELECT DISTINCT cluster_id FROM image_faces WHERE cluster_id IS NOT NULL
            )
            """
        )
    if identity_model is not None and apply_remembered_names(connection, identity_model):
        names = {item.cluster_id: item for item in list_person_clusters(connection, include_ignored=True)}
        clusters = [replace(cluster, name=names[cluster.cluster_id].name, ignored=names[cluster.cluster_id].ignored) if cluster.cluster_id in names else cluster for cluster in clusters]
    return clusters


# -- names that survive a change of recognizer -----------------------------------------------------------------------------------------------
# A different recognizer is a different embedding space, so its clusters cannot be matched to the old ones by centroid. What both share is the photo and where the face
# sits in it: before an old face is replaced, its name is remembered by (image, box), and a new cluster takes the name most of its faces carried.


def _ensure_name_carry_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS face_name_carry (
            image_id INTEGER NOT NULL, x1 REAL NOT NULL, y1 REAL NOT NULL, x2 REAL NOT NULL, y2 REAL NOT NULL,
            name TEXT NOT NULL DEFAULT '', ignored INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(image_id, x1, y1, x2, y2)
        )
        """
    )


def remember_named_faces(connection: sqlite3.Connection, identity_model: str) -> int:
    """Remember the name (or ignored mark) of every face that was not made by ``identity_model``. Idempotent; returns how many rows were added."""
    ensure_people_search_schema(connection)
    _ensure_name_carry_table(connection)
    with connection:
        cursor = connection.execute(
            """
            INSERT OR IGNORE INTO face_name_carry(image_id, x1, y1, x2, y2, name, ignored)
            SELECT image_faces.image_id, image_faces.x1, image_faces.y1, image_faces.x2, image_faces.y2,
                   face_identity_clusters.name, face_identity_clusters.ignored
            FROM image_faces
            INNER JOIN face_identity_clusters ON face_identity_clusters.cluster_id = image_faces.cluster_id
            WHERE (image_faces.identity_model IS NULL OR image_faces.identity_model != ?)
              AND (face_identity_clusters.name != '' OR face_identity_clusters.ignored = 1)
            """,
            (identity_model,),
        )
    return int(cursor.rowcount or 0)


def _boxes_match(left: tuple[float, float, float, float], right: tuple[float, float, float, float]) -> bool:
    """The same face: either box's centre lies inside the other (the two recognizers' detectors draw the box a little differently)."""

    def centre_inside(inner: tuple[float, float, float, float], outer: tuple[float, float, float, float]) -> bool:
        cx, cy = (inner[0] + inner[2]) / 2.0, (inner[1] + inner[3]) / 2.0
        return outer[0] <= cx <= outer[2] and outer[1] <= cy <= outer[3]

    return centre_inside(left, right) and centre_inside(right, left)


def apply_remembered_names(connection: sqlite3.Connection, identity_model: str) -> int:
    """Name the unnamed clusters of ``identity_model`` from remembered faces: a cluster takes the name most of its matching faces carried, if no other cluster has it.
    Returns how many clusters were named. Remembered faces that matched are forgotten; the rest wait for their photo to be scanned."""
    ensure_people_search_schema(connection)
    _ensure_name_carry_table(connection)
    previous_factory = connection.row_factory
    connection.row_factory = sqlite3.Row
    try:
        carry = connection.execute("SELECT image_id, x1, y1, x2, y2, name, ignored FROM face_name_carry").fetchall()
        if not carry:
            return 0
        members = connection.execute(
            """
            SELECT image_faces.cluster_id, image_faces.image_id, image_faces.x1, image_faces.y1, image_faces.x2, image_faces.y2
            FROM image_faces
            INNER JOIN face_identity_clusters ON face_identity_clusters.cluster_id = image_faces.cluster_id
            WHERE image_faces.identity_model = ? AND face_identity_clusters.name = '' AND face_identity_clusters.ignored = 0
            """,
            (identity_model,),
        ).fetchall()
        taken = {str(row["name"]).casefold() for row in connection.execute("SELECT name FROM face_identity_clusters WHERE name != ''")}
    finally:
        connection.row_factory = previous_factory
    by_image: dict[int, list[sqlite3.Row]] = {}
    for row in carry:
        by_image.setdefault(int(row["image_id"]), []).append(row)
    votes: dict[int, dict[tuple[str, int], int]] = {}
    matched_keys: list[tuple] = []
    for member in members:
        box = (float(member["x1"]), float(member["y1"]), float(member["x2"]), float(member["y2"]))
        for remembered in by_image.get(int(member["image_id"]), ()):
            if _boxes_match(box, (float(remembered["x1"]), float(remembered["y1"]), float(remembered["x2"]), float(remembered["y2"]))):
                tally = votes.setdefault(int(member["cluster_id"]), {})
                key = (str(remembered["name"]), int(remembered["ignored"]))
                tally[key] = tally.get(key, 0) + 1
                matched_keys.append(tuple(remembered[column] for column in ("image_id", "x1", "y1", "x2", "y2")))
                break
    named = 0
    stamp = datetime.now(timezone.utc).isoformat()
    ranked = sorted(votes.items(), key=lambda item: -max(item[1].values()))
    with connection:
        for cluster_id, tally in ranked:
            (name, ignored), _count = max(tally.items(), key=lambda item: item[1])
            if name and name.casefold() not in taken:
                connection.execute("UPDATE face_identity_clusters SET name = ?, updated_at = ? WHERE cluster_id = ?", (name, stamp, cluster_id))
                taken.add(name.casefold())
                named += 1
            elif not name and ignored:
                connection.execute("UPDATE face_identity_clusters SET ignored = 1, updated_at = ? WHERE cluster_id = ?", (stamp, cluster_id))
        connection.executemany("DELETE FROM face_name_carry WHERE image_id = ? AND x1 = ? AND y1 = ? AND x2 = ? AND y2 = ?", matched_keys)
    return named


def assign_person_name(connection: sqlite3.Connection, cluster_id: int, name: str) -> None:
    assign_person_names(connection, (cluster_id,), name)


def assign_person_names(connection: sqlite3.Connection, cluster_ids, name: str) -> None:
    ensure_people_search_schema(connection)
    clean_name = " ".join(str(name or "").split())
    ids = tuple(int(cluster_id) for cluster_id in cluster_ids)
    if not ids:
        return
    stamp = datetime.now(timezone.utc).isoformat()
    with connection:
        connection.executemany(
            "UPDATE face_identity_clusters SET name = ?, updated_at = ? WHERE cluster_id = ?",
            [(clean_name, stamp, cluster_id) for cluster_id in ids],
        )


def list_person_clusters(
    connection: sqlite3.Connection, *, include_ignored: bool = False
) -> list[PersonCluster]:
    ensure_people_search_schema(connection)
    previous_factory = connection.row_factory
    connection.row_factory = sqlite3.Row
    where = "" if include_ignored else "WHERE ignored = 0"
    try:
        rows = connection.execute(
            f"""
            SELECT cluster_id, name, face_count, centroid, dtype, ignored
            FROM face_identity_clusters
            {where}
            ORDER BY CASE WHEN name = '' THEN 1 ELSE 0 END, name COLLATE NOCASE, face_count DESC
            """
        ).fetchall()
    finally:
        connection.row_factory = previous_factory
    return [
        PersonCluster(
            cluster_id=int(row["cluster_id"]),
            name=str(row["name"] or ""),
            face_count=int(row["face_count"] or 0),
            centroid=_tuple_from_blob(row["centroid"], row["dtype"]),
            ignored=bool(row["ignored"]),
        )
        for row in rows
    ]


def set_clusters_ignored(connection: sqlite3.Connection, cluster_ids, ignored: bool) -> None:
    """Hide (or restore) face clusters that are not a person worth tagging."""
    ids = [int(cluster_id) for cluster_id in cluster_ids]
    if not ids:
        return
    ensure_people_search_schema(connection)
    placeholders = ",".join("?" for _ in ids)
    connection.execute(
        f"UPDATE face_identity_clusters SET ignored = ? WHERE cluster_id IN ({placeholders})",
        (1 if ignored else 0, *ids),
    )


def list_face_identities(
    connection: sqlite3.Connection,
    *,
    min_face_confidence: float = 0.0,
    identity_model: str | None = None,
) -> list[FaceIdentity]:
    """List stored face identity vectors.

    ``identity_model`` scopes the result to a single recognizer's embeddings.
    This is required when the recognizer changes between app versions: different
    recognizers can both produce 512-d vectors, so without this filter stale
    vectors from a prior model would be clustered together with the current
    ones — cosine across two different embedding spaces is meaningless.
    """
    ensure_people_search_schema(connection)
    previous_factory = connection.row_factory
    connection.row_factory = sqlite3.Row
    try:
        query = """
            SELECT image_faces.image_id, image_faces.face_index, image_faces.det_score,
                   image_faces.identity_embedding, image_faces.identity_dtype,
                   image_faces.cluster_id, images.source_path
            FROM image_faces
            LEFT JOIN images ON images.id = image_faces.image_id
            WHERE image_faces.identity_embedding IS NOT NULL
              AND image_faces.det_score >= ?
        """
        params: list[object] = [float(min_face_confidence)]
        if identity_model is not None:
            query += " AND image_faces.identity_model = ?"
            params.append(identity_model)
        query += " ORDER BY image_faces.image_id ASC, image_faces.face_index ASC"
        rows = connection.execute(query, params).fetchall()
    finally:
        connection.row_factory = previous_factory
    identities: list[FaceIdentity] = []
    for row in rows:
        embedding = _tuple_from_blob(row["identity_embedding"], row["identity_dtype"])
        if embedding:
            identities.append(
                FaceIdentity(
                    image_id=int(row["image_id"]),
                    face_index=int(row["face_index"]),
                    det_score=float(row["det_score"]),
                    embedding=embedding,
                    cluster_id=None if row["cluster_id"] is None else int(row["cluster_id"]),
                    source_path=str(row["source_path"] or ""),
                )
            )
    return identities


def named_people_by_image_id(connection: sqlite3.Connection) -> dict[int, set[str]]:
    ensure_people_search_schema(connection)
    previous_factory = connection.row_factory
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            """
            SELECT image_faces.image_id, face_identity_clusters.name
            FROM image_faces
            INNER JOIN face_identity_clusters
              ON face_identity_clusters.cluster_id = image_faces.cluster_id
            WHERE face_identity_clusters.name != '' AND face_identity_clusters.ignored = 0
            """
        ).fetchall()
    finally:
        connection.row_factory = previous_factory
    grouped: dict[int, set[str]] = {}
    for row in rows:
        grouped.setdefault(int(row["image_id"]), set()).add(str(row["name"]))
    return grouped


def image_ids_matching_people(
    connection: sqlite3.Connection,
    names: tuple[str, ...],
    *,
    match_all: bool = True,
) -> set[int]:
    requested = {name.casefold() for name in names if name.strip()}
    if not requested:
        return set()
    grouped = named_people_by_image_id(connection)
    matches: set[int] = set()
    for image_id, present_names in grouped.items():
        present = {name.casefold() for name in present_names}
        matched = requested.issubset(present) if match_all else bool(requested & present)
        if matched:
            matches.add(image_id)
    return matches


def cosine_similarity(left: np.ndarray | tuple[float, ...], right: np.ndarray | tuple[float, ...]) -> float:
    left_arr = _normalize(np.asarray(left, dtype=np.float32).reshape(-1))
    right_arr = _normalize(np.asarray(right, dtype=np.float32).reshape(-1))
    if left_arr.size != right_arr.size:
        raise ValueError(f"Embedding dimensions differ: {left_arr.size} vs {right_arr.size}")
    if left_arr.size == 0:
        return 0.0
    return float(np.dot(left_arr, right_arr))


def _ensure_image_face_identity_columns(connection: sqlite3.Connection) -> None:
    from .quality.store import ensure_faces_table

    ensure_faces_table(connection)


def _load_existing_clusters(connection: sqlite3.Connection) -> list[PersonCluster]:
    previous_factory = connection.row_factory
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            "SELECT cluster_id, name, face_count, centroid, dtype, ignored FROM face_identity_clusters"
        ).fetchall()
    finally:
        connection.row_factory = previous_factory
    return [
        PersonCluster(
            cluster_id=int(row["cluster_id"]),
            name=str(row["name"] or ""),
            face_count=int(row["face_count"] or 0),
            centroid=_tuple_from_blob(row["centroid"], row["dtype"]),
            ignored=bool(row["ignored"]),
        )
        for row in rows
    ]


def _reuse_cluster(
    previous: list[PersonCluster],
    used_previous: set[int],
    centroid: np.ndarray,
    *,
    threshold: float,
) -> tuple[int | None, str, bool]:
    best: PersonCluster | None = None
    best_score = -1.0
    for candidate in previous:
        if candidate.cluster_id in used_previous:
            continue
        score = cosine_similarity(candidate.centroid, centroid)
        if score > best_score:
            best = candidate
            best_score = score
    if best is None or best_score < threshold:
        return None, "", False
    used_previous.add(best.cluster_id)
    return best.cluster_id, best.name, best.ignored


def _centroid(vectors: list[tuple[float, ...]]) -> np.ndarray:
    matrix = np.vstack([_normalize(np.asarray(vector, dtype=np.float32)) for vector in vectors])
    return _normalize(matrix.mean(axis=0))


def _normalize(vector: np.ndarray) -> np.ndarray:
    values = np.asarray(vector, dtype=np.float32).reshape(-1)
    norm = float(np.linalg.norm(values))
    if norm == 0.0:
        return values
    return values / norm


def _vector_to_db(vector: np.ndarray | tuple[float, ...]) -> tuple[bytes, int, str]:
    values = np.asarray(vector, dtype=np.float32).reshape(-1)
    return values.tobytes(), int(values.size), str(values.dtype)


def _tuple_from_blob(blob: bytes | None, dtype: str | None) -> tuple[float, ...]:
    if blob is None:
        return ()
    values = np.frombuffer(blob, dtype=np.dtype(dtype or "float32")).astype(np.float32, copy=True)
    return tuple(float(value) for value in values)
