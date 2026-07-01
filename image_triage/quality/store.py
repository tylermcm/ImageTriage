"""SQLite persistence for per-image quality dimensions.

A dedicated ``image_dimensions`` table keyed on ``images.id`` (dimensions are
intrinsic to the image, independent of any adapter/model version). Functions
take a raw ``sqlite3.Connection`` so they work against either a throwaway test
DB or the live ``SQLiteFeatureStore.connection``.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from .face import FaceRecord
from .model import DimensionScores

_REAL_FIELDS = (
    "sharpness",
    "exposure",
    "dynamic_range",
    "noise",
    "contrast",
    "color_harmony",
    "aesthetic",
    "composition",
    "saliency",
    "face_quality",
    "eye_sharpness",
)
_BOOL_FIELDS = ("monochrome", "blink")
_ALL_FIELDS = (*_REAL_FIELDS, *_BOOL_FIELDS)


def ensure_table(connection: sqlite3.Connection) -> None:
    columns = ",\n            ".join(f"{name} REAL" for name in _REAL_FIELDS)
    bool_columns = ",\n            ".join(f"{name} INTEGER" for name in _BOOL_FIELDS)
    connection.execute(
        f"""
        CREATE TABLE IF NOT EXISTS image_dimensions (
            image_id INTEGER PRIMARY KEY,
            {columns},
            {bool_columns},
            computed_at TEXT NOT NULL
        )
        """
    )


def ensure_faces_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS image_faces (
            image_id INTEGER NOT NULL,
            face_index INTEGER NOT NULL,
            x1 REAL NOT NULL,
            y1 REAL NOT NULL,
            x2 REAL NOT NULL,
            y2 REAL NOT NULL,
            det_score REAL NOT NULL,
            eye_sharpness REAL,
            gender TEXT,
            age INTEGER,
            blink INTEGER,
            computed_at TEXT NOT NULL,
            PRIMARY KEY(image_id, face_index)
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_image_faces_image ON image_faces(image_id)"
    )


def _bool_to_db(value: bool | None) -> int | None:
    return None if value is None else int(bool(value))


def _bool_from_db(value: object) -> bool | None:
    return None if value is None else bool(value)


def upsert_dimensions(
    connection: sqlite3.Connection,
    image_id: int,
    scores: DimensionScores,
    *,
    computed_at: str | None = None,
) -> None:
    ensure_table(connection)
    stamp = computed_at or datetime.now(timezone.utc).isoformat()
    values: dict[str, object] = {"image_id": int(image_id), "computed_at": stamp}
    for name in _REAL_FIELDS:
        raw = getattr(scores, name)
        values[name] = None if raw is None else float(raw)
    for name in _BOOL_FIELDS:
        values[name] = _bool_to_db(getattr(scores, name))

    columns = ", ".join(values.keys())
    placeholders = ", ".join(f":{name}" for name in values)
    updates = ", ".join(f"{name}=excluded.{name}" for name in values if name != "image_id")
    connection.execute(
        f"""
        INSERT INTO image_dimensions ({columns}) VALUES ({placeholders})
        ON CONFLICT(image_id) DO UPDATE SET {updates}
        """,
        values,
    )


def upsert_faces(
    connection: sqlite3.Connection,
    image_id: int,
    faces: list[FaceRecord] | tuple[FaceRecord, ...],
    *,
    computed_at: str | None = None,
) -> None:
    ensure_faces_table(connection)
    stamp = computed_at or datetime.now(timezone.utc).isoformat()
    connection.execute("DELETE FROM image_faces WHERE image_id = ?", (int(image_id),))
    rows = []
    for index, face in enumerate(faces):
        x1, y1, x2, y2 = face.bbox
        rows.append(
            (
                int(image_id),
                int(index),
                float(x1),
                float(y1),
                float(x2),
                float(y2),
                float(face.det_score),
                None if face.eye_sharpness is None else float(face.eye_sharpness),
                face.gender,
                None if face.age is None else int(face.age),
                _bool_to_db(face.blink),
                stamp,
            )
        )
    if rows:
        connection.executemany(
            """
            INSERT INTO image_faces (
                image_id, face_index, x1, y1, x2, y2, det_score, eye_sharpness,
                gender, age, blink, computed_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )


def _row_to_scores(row: sqlite3.Row) -> DimensionScores:
    kwargs: dict[str, object] = {}
    for name in _REAL_FIELDS:
        value = row[name]
        kwargs[name] = None if value is None else float(value)
    for name in _BOOL_FIELDS:
        kwargs[name] = _bool_from_db(row[name])
    return DimensionScores(**kwargs)


def fetch_dimensions(connection: sqlite3.Connection, image_id: int) -> DimensionScores | None:
    ensure_table(connection)
    previous_factory = connection.row_factory
    connection.row_factory = sqlite3.Row
    try:
        row = connection.execute(
            "SELECT * FROM image_dimensions WHERE image_id = ?", (int(image_id),)
        ).fetchone()
    finally:
        connection.row_factory = previous_factory
    return _row_to_scores(row) if row is not None else None


def fetch_all_dimensions(connection: sqlite3.Connection) -> dict[int, DimensionScores]:
    ensure_table(connection)
    previous_factory = connection.row_factory
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute("SELECT * FROM image_dimensions").fetchall()
    finally:
        connection.row_factory = previous_factory
    return {int(row["image_id"]): _row_to_scores(row) for row in rows}


def _row_to_face(row: sqlite3.Row) -> FaceRecord:
    return FaceRecord(
        bbox=(float(row["x1"]), float(row["y1"]), float(row["x2"]), float(row["y2"])),
        det_score=float(row["det_score"]),
        eye_sharpness=None if row["eye_sharpness"] is None else float(row["eye_sharpness"]),
        gender=None if row["gender"] is None else str(row["gender"]),
        age=None if row["age"] is None else int(row["age"]),
        blink=_bool_from_db(row["blink"]),
    )


def fetch_faces(connection: sqlite3.Connection, image_id: int) -> tuple[FaceRecord, ...]:
    ensure_faces_table(connection)
    previous_factory = connection.row_factory
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            "SELECT * FROM image_faces WHERE image_id = ? ORDER BY face_index ASC",
            (int(image_id),),
        ).fetchall()
    finally:
        connection.row_factory = previous_factory
    return tuple(_row_to_face(row) for row in rows)


def fetch_all_faces_by_path(connection: sqlite3.Connection) -> dict[str, tuple[FaceRecord, ...]]:
    ensure_faces_table(connection)
    previous_factory = connection.row_factory
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            """
            SELECT images.source_path, image_faces.*
            FROM image_faces
            INNER JOIN images ON images.id = image_faces.image_id
            ORDER BY images.source_path ASC, image_faces.face_index ASC
            """
        ).fetchall()
    finally:
        connection.row_factory = previous_factory
    grouped: dict[str, list[FaceRecord]] = {}
    for row in rows:
        grouped.setdefault(str(row["source_path"]), []).append(_row_to_face(row))
    return {path: tuple(faces) for path, faces in grouped.items()}
