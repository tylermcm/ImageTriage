"""Shared face-group queries and representative-face ranking."""
from __future__ import annotations
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from .people_search import ensure_people_search_schema, list_person_clusters
from .quality.store import ensure_faces_table

Bbox = tuple[float,float,float,float]
MAX_FACE_GROUPS = 8
MIN_FACE_COUNT = 2

def rank_faces(faces: list[dict]) -> list[dict]:
    """Order candidate faces best-first for representative selection.

    Uses the signals we actually store — detector confidence, face size, and
    eye sharpness — to avoid tiny / low-confidence / soft crops. (Pose and
    exposure are not persisted, so they cannot factor in yet.)
    """
    if not faces:
        return []
    areas = [max(1.0, (f["bbox"][2] - f["bbox"][0]) * (f["bbox"][3] - f["bbox"][1])) for f in faces]
    sharps = [float(f.get("sharp") or 0.0) for f in faces]
    max_area = max(areas)
    max_sharp = max(sharps) or 1.0
    scored: list[tuple[float, dict]] = []
    for face, area, sharp in zip(faces, areas, sharps):
        score = 0.45 * float(face["det"]) + 0.40 * (area / max_area) + 0.15 * (sharp / max_sharp)
        scored.append((score, face))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [face for _score, face in scored]


@dataclass(frozen=True)
class FaceGroup:
    key: int
    name: str
    cluster_ids: tuple[int, ...]
    face_count: int
    rep_face: tuple[str, Bbox] | None = None

    @property
    def named(self) -> bool:
        return bool(self.name.strip())

    @property
    def label(self) -> str:
        if self.named:
            return self.name.strip()
        word = "photo" if self.face_count == 1 else "photos"
        return f"Unnamed ({self.face_count} {word})"

    @property
    def filter_label(self) -> str:
        """What the grid's active-filter chip should say."""
        if self.named:
            return self.name.strip()
        word = "photo" if self.face_count == 1 else "photos"
        return f"Unnamed face ({self.face_count} {word})"


def _connect(db_path: str | Path) -> sqlite3.Connection:
    connection = sqlite3.connect(str(db_path))
    connection.row_factory = sqlite3.Row
    return connection


def load_face_groups(
    db_path: str | Path,
    *,
    limit: int = MAX_FACE_GROUPS,
    min_face_count: int = MIN_FACE_COUNT,
) -> list[FaceGroup]:
    """The most-photographed people in a folder, named ones first.

    Named people lead because they are the ones the user curates by; the
    remaining slots go to the most recurring unnamed faces, which are the ones
    worth naming next.
    """
    if not Path(str(db_path)).exists():
        return []
    connection = _connect(db_path)
    try:
        ensure_faces_table(connection)
        ensure_people_search_schema(connection)
        clusters = list_person_clusters(connection)
        faces = _faces_by_cluster(connection)
    except sqlite3.DatabaseError:
        return []
    finally:
        connection.close()

    merged: dict[str, list] = {}
    people: list[FaceGroup] = []
    ordered: list[tuple[str, list[int], int]] = []
    for cluster in clusters:
        name = cluster.name.strip()
        if name and name.casefold() in merged:
            entry = merged[name.casefold()]
            entry[1].append(cluster.cluster_id)
            entry[2] += cluster.face_count
            continue
        entry = [name, [cluster.cluster_id], cluster.face_count]
        if name:
            merged[name.casefold()] = entry
        ordered.append(entry)

    for name, cluster_ids, face_count in ordered:
        if face_count < min_face_count:
            continue
        candidates: list[dict] = []
        for cluster_id in cluster_ids:
            candidates.extend(faces.get(cluster_id, []))
        ranked = rank_faces(candidates)
        rep = (ranked[0]["source"], ranked[0]["bbox"]) if ranked else None
        people.append(
            FaceGroup(
                key=cluster_ids[0],
                name=name,
                cluster_ids=tuple(cluster_ids),
                face_count=face_count,
                rep_face=rep,
            )
        )

    people.sort(key=lambda group: (not group.named, -group.face_count))
    return people[: max(0, limit)]


def face_group_photo_paths(db_path: str | Path, cluster_ids) -> list[str]:
    """Every source image the group's face appears in."""
    ids = [int(cluster_id) for cluster_id in cluster_ids]
    if not ids or not Path(str(db_path)).exists():
        return []
    placeholders = ",".join("?" for _ in ids)
    connection = _connect(db_path)
    try:
        rows = connection.execute(
            f"""
            SELECT DISTINCT images.source_path
            FROM image_faces
            JOIN images ON images.id = image_faces.image_id
            WHERE image_faces.cluster_id IN ({placeholders})
            """,
            tuple(ids),
        ).fetchall()
    except sqlite3.DatabaseError:
        return []
    finally:
        connection.close()
    return [str(row[0]) for row in rows if row[0]]


def _faces_by_cluster(connection: sqlite3.Connection) -> dict[int, list[dict]]:
    rows = connection.execute(
        """
        SELECT image_faces.cluster_id AS cid, images.source_path AS sp,
               image_faces.x1, image_faces.y1, image_faces.x2, image_faces.y2,
               image_faces.det_score AS det, image_faces.eye_sharpness AS sharp
        FROM image_faces JOIN images ON images.id = image_faces.image_id
        WHERE image_faces.cluster_id IS NOT NULL
        """
    ).fetchall()
    grouped: dict[int, list[dict]] = {}
    for row in rows:
        grouped.setdefault(int(row["cid"]), []).append(
            {
                "source": str(row["sp"]),
                "bbox": (float(row["x1"]), float(row["y1"]), float(row["x2"]), float(row["y2"])),
                "det": float(row["det"]),
                "sharp": row["sharp"],
            }
        )
    return grouped
