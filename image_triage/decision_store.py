from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from PySide6.QtCore import QStandardPaths

from .models import ImageRecord, SessionAnnotation


class DecisionStore:
    DEFAULT_SESSION = "Default"
    SCHEMA_VERSION = 4

    def __init__(self) -> None:
        app_data = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation)
        root = Path(app_data) if app_data else Path.home() / ".image-triage"
        root.mkdir(parents=True, exist_ok=True)
        self._db_path = root / "decisions.sqlite3"
        self._initialize()

    def load_annotations(self, session_id: str, records: list[ImageRecord]) -> dict[str, SessionAnnotation]:
        if not records:
            return {}

        normalized_session = self._normalize_session_id(session_id)
        loaded: dict[str, SessionAnnotation] = {}
        with sqlite3.connect(self._db_path) as connection:
            connection.row_factory = sqlite3.Row
            for chunk in _chunked(records, 400):
                placeholders = ",".join("?" for _ in chunk)
                paths = [record.path for record in chunk]
                rows = connection.execute(
                    f"""
                    SELECT path, modified_ns, file_size, winner, reject, photoshop, rating, tags_json
                    FROM decisions
                    WHERE session_id = ?
                      AND path IN ({placeholders})
                    """,
                    [normalized_session, *paths],
                ).fetchall()
                row_map = {row["path"]: row for row in rows}
                for record in chunk:
                    row = row_map.get(record.path)
                    if row is None:
                        continue
                    if row["modified_ns"] != record.modified_ns or row["file_size"] != record.size:
                        continue
                    annotation = SessionAnnotation(
                        winner=bool(row["winner"]),
                        reject=bool(row["reject"]),
                        photoshop=bool(row["photoshop"]),
                        rating=int(row["rating"] or 0),
                        tags=tuple(json.loads(row["tags_json"] or "[]")),
                    )
                    if annotation.is_empty:
                        continue
                    loaded[record.path] = annotation
        return loaded

    def save_annotation(self, session_id: str, record: ImageRecord, annotation: SessionAnnotation) -> None:
        if annotation.is_empty:
            self.delete_annotation(session_id, record.path)
            return
        normalized_session = self._normalize_session_id(session_id)
        with sqlite3.connect(self._db_path) as connection:
            self._ensure_session_row(connection, normalized_session)
            connection.execute(
                """
                INSERT INTO decisions (session_id, path, modified_ns, file_size, winner, reject, photoshop, rating, tags_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id, path) DO UPDATE SET
                    modified_ns = excluded.modified_ns,
                    file_size = excluded.file_size,
                    winner = excluded.winner,
                    reject = excluded.reject,
                    photoshop = excluded.photoshop,
                    rating = excluded.rating,
                    tags_json = excluded.tags_json
                """,
                (
                    normalized_session,
                    record.path,
                    record.modified_ns,
                    record.size,
                    int(annotation.winner),
                    int(annotation.reject),
                    int(annotation.photoshop),
                    annotation.rating,
                    json.dumps(list(annotation.tags)),
                ),
            )
            connection.commit()

    def move_annotation(self, session_id: str, old_path: str, record: ImageRecord, annotation: SessionAnnotation) -> None:
        if annotation.is_empty:
            self.delete_annotation(session_id, old_path)
            return
        normalized_session = self._normalize_session_id(session_id)
        with sqlite3.connect(self._db_path) as connection:
            self._ensure_session_row(connection, normalized_session)
            connection.execute("DELETE FROM decisions WHERE session_id = ? AND path = ?", (normalized_session, old_path))
            connection.execute(
                """
                INSERT INTO decisions (session_id, path, modified_ns, file_size, winner, reject, photoshop, rating, tags_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id, path) DO UPDATE SET
                    modified_ns = excluded.modified_ns,
                    file_size = excluded.file_size,
                    winner = excluded.winner,
                    reject = excluded.reject,
                    photoshop = excluded.photoshop,
                    rating = excluded.rating,
                    tags_json = excluded.tags_json
                """,
                (
                    normalized_session,
                    record.path,
                    record.modified_ns,
                    record.size,
                    int(annotation.winner),
                    int(annotation.reject),
                    int(annotation.photoshop),
                    annotation.rating,
                    json.dumps(list(annotation.tags)),
                ),
            )
            connection.commit()

    def delete_annotation(self, session_id: str, path: str) -> None:
        normalized_session = self._normalize_session_id(session_id)
        with sqlite3.connect(self._db_path) as connection:
            connection.execute("DELETE FROM decisions WHERE session_id = ? AND path = ?", (normalized_session, path))
            connection.commit()

    def list_sessions(self) -> list[str]:
        with sqlite3.connect(self._db_path) as connection:
            rows = connection.execute(
                """
                SELECT id
                FROM sessions
                ORDER BY
                    CASE WHEN id = ? THEN 0 ELSE 1 END,
                    last_used_at DESC,
                    id COLLATE NOCASE
                """,
                (self.DEFAULT_SESSION,),
            ).fetchall()
        sessions = [row[0] for row in rows]
        if self.DEFAULT_SESSION not in sessions:
            sessions.insert(0, self.DEFAULT_SESSION)
        return sessions

    def ensure_session(self, session_id: str) -> str:
        normalized_session = self._normalize_session_id(session_id)
        with sqlite3.connect(self._db_path) as connection:
            self._ensure_session_row(connection, normalized_session)
            connection.commit()
        return normalized_session

    def touch_session(self, session_id: str) -> str:
        normalized_session = self._normalize_session_id(session_id)
        with sqlite3.connect(self._db_path) as connection:
            self._ensure_session_row(connection, normalized_session)
            connection.execute(
                "UPDATE sessions SET last_used_at = CURRENT_TIMESTAMP WHERE id = ?",
                (normalized_session,),
            )
            connection.commit()
        return normalized_session

    def _initialize(self) -> None:
        with sqlite3.connect(self._db_path) as connection:
            current_version = connection.execute("PRAGMA user_version").fetchone()[0]
            if current_version < 3:
                self._migrate_to_v3(connection)
            if current_version < 4:
                self._migrate_to_v4(connection)
            self._create_schema(connection)
            self._ensure_session_row(connection, self.DEFAULT_SESSION)
            if current_version < self.SCHEMA_VERSION:
                connection.execute(f"PRAGMA user_version = {self.SCHEMA_VERSION}")
            connection.commit()

    def _create_schema(self, connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                last_used_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS decisions (
                session_id TEXT NOT NULL,
                path TEXT NOT NULL,
                modified_ns INTEGER NOT NULL,
                file_size INTEGER NOT NULL,
                winner INTEGER NOT NULL DEFAULT 0,
                reject INTEGER NOT NULL DEFAULT 0,
                photoshop INTEGER NOT NULL DEFAULT 0,
                rating INTEGER NOT NULL DEFAULT 0,
                tags_json TEXT NOT NULL DEFAULT '[]',
                PRIMARY KEY(session_id, path),
                FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_decisions_file_identity
            ON decisions(session_id, modified_ns, file_size)
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_decisions_review_state
            ON decisions(session_id, winner, reject, photoshop, rating)
        """
        )

    def _migrate_to_v3(self, connection: sqlite3.Connection) -> None:
        has_decisions = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'decisions'"
        ).fetchone()
        self._create_schema(connection)
        self._ensure_session_row(connection, self.DEFAULT_SESSION)
        if has_decisions:
            columns = {
                row[1]
                for row in connection.execute("PRAGMA table_info(decisions)").fetchall()
            }
            if "session_id" not in columns:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS decisions_v3 (
                        session_id TEXT NOT NULL,
                        path TEXT NOT NULL,
                        modified_ns INTEGER NOT NULL,
                        file_size INTEGER NOT NULL,
                        winner INTEGER NOT NULL DEFAULT 0,
                        reject INTEGER NOT NULL DEFAULT 0,
                        photoshop INTEGER NOT NULL DEFAULT 0,
                        rating INTEGER NOT NULL DEFAULT 0,
                        tags_json TEXT NOT NULL DEFAULT '[]',
                        PRIMARY KEY(session_id, path)
                    )
                    """
                )
                connection.execute(
                    """
                    INSERT OR REPLACE INTO decisions_v3 (
                        session_id,
                        path,
                        modified_ns,
                        file_size,
                        winner,
                        reject,
                        photoshop,
                        rating,
                        tags_json
                    )
                    SELECT ?, path, modified_ns, file_size, winner, reject, 0, rating, tags_json
                    FROM decisions
                    """,
                    (self.DEFAULT_SESSION,),
                )
                connection.execute("DROP TABLE decisions")
                connection.execute("ALTER TABLE decisions_v3 RENAME TO decisions")

    def _migrate_to_v4(self, connection: sqlite3.Connection) -> None:
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(decisions)").fetchall()
        }
        if "photoshop" not in columns:
            connection.execute(
                """
                ALTER TABLE decisions
                ADD COLUMN photoshop INTEGER NOT NULL DEFAULT 0
                """
            )

    def _ensure_session_row(self, connection: sqlite3.Connection, session_id: str) -> None:
        connection.execute(
            """
            INSERT INTO sessions (id, created_at, last_used_at)
            VALUES (?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            ON CONFLICT(id) DO UPDATE SET last_used_at = CURRENT_TIMESTAMP
            """,
            (session_id,),
        )

    def _normalize_session_id(self, session_id: str) -> str:
        value = (session_id or "").strip()
        return value or self.DEFAULT_SESSION


def _chunked(records: list[ImageRecord], size: int) -> list[list[ImageRecord]]:
    return [records[index : index + size] for index in range(0, len(records), size)]
