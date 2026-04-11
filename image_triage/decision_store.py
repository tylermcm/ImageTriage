from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from PySide6.QtCore import QStandardPaths

from .models import ImageRecord, SessionAnnotation


class DecisionStore:
    DEFAULT_SESSION = "Default"
    SCHEMA_VERSION = 6

    def __init__(self) -> None:
        app_data = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation)
        root = Path(app_data) if app_data else Path.home() / ".image-triage"
        root.mkdir(parents=True, exist_ok=True)
        self._db_path = root / "decisions.sqlite3"
        self._initialize()

    def load_annotations(self, session_id: str, records: list[ImageRecord]) -> dict[str, SessionAnnotation]:
        if not records:
            return {}
        records_by_path = {record.path: record for record in records if record.path}
        return self.load_annotations_for_paths(session_id, records_by_path, list(records_by_path))

    def load_annotations_for_paths(
        self,
        session_id: str,
        records_by_path: dict[str, ImageRecord],
        paths: list[str] | tuple[str, ...] | set[str],
    ) -> dict[str, SessionAnnotation]:
        if not records_by_path or not paths:
            return {}

        normalized_session = self._normalize_session_id(session_id)
        loaded: dict[str, SessionAnnotation] = {}
        ordered_paths: list[str] = []
        seen_paths: set[str] = set()
        for path in paths:
            if not path or path in seen_paths:
                continue
            if path not in records_by_path:
                continue
            seen_paths.add(path)
            ordered_paths.append(path)
        if not ordered_paths:
            return loaded
        with sqlite3.connect(self._db_path) as connection:
            connection.row_factory = sqlite3.Row
            for chunk_paths in _chunked_values(ordered_paths, 400):
                placeholders = ",".join("?" for _ in chunk_paths)
                rows = connection.execute(
                    f"""
                    SELECT path, modified_ns, file_size, winner, reject, photoshop, rating, tags_json, review_round
                    FROM decisions
                    WHERE session_id = ?
                      AND path IN ({placeholders})
                    """,
                    [normalized_session, *chunk_paths],
                ).fetchall()
                row_map = {row["path"]: row for row in rows}
                for path in chunk_paths:
                    record = records_by_path.get(path)
                    if record is None:
                        continue
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
                        review_round=str(row["review_round"] or ""),
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
                INSERT INTO decisions (session_id, path, modified_ns, file_size, winner, reject, photoshop, rating, tags_json, review_round)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id, path) DO UPDATE SET
                    modified_ns = excluded.modified_ns,
                    file_size = excluded.file_size,
                    winner = excluded.winner,
                    reject = excluded.reject,
                    photoshop = excluded.photoshop,
                    rating = excluded.rating,
                    tags_json = excluded.tags_json,
                    review_round = excluded.review_round
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
                    annotation.review_round,
                ),
            )
            connection.commit()

    def save_annotations(
        self,
        session_id: str,
        entries: list[tuple[ImageRecord, SessionAnnotation | None]],
    ) -> None:
        if not entries:
            return
        normalized_session = self._normalize_session_id(session_id)
        with sqlite3.connect(self._db_path) as connection:
            self._ensure_session_row(connection, normalized_session)
            for record, annotation in entries:
                if annotation is None or annotation.is_empty:
                    connection.execute(
                        "DELETE FROM decisions WHERE session_id = ? AND path = ?",
                        (normalized_session, record.path),
                    )
                    continue
                connection.execute(
                    """
                    INSERT INTO decisions (session_id, path, modified_ns, file_size, winner, reject, photoshop, rating, tags_json, review_round)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(session_id, path) DO UPDATE SET
                        modified_ns = excluded.modified_ns,
                        file_size = excluded.file_size,
                        winner = excluded.winner,
                        reject = excluded.reject,
                        photoshop = excluded.photoshop,
                        rating = excluded.rating,
                        tags_json = excluded.tags_json,
                        review_round = excluded.review_round
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
                        annotation.review_round,
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
                INSERT INTO decisions (session_id, path, modified_ns, file_size, winner, reject, photoshop, rating, tags_json, review_round)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id, path) DO UPDATE SET
                    modified_ns = excluded.modified_ns,
                    file_size = excluded.file_size,
                    winner = excluded.winner,
                    reject = excluded.reject,
                    photoshop = excluded.photoshop,
                    rating = excluded.rating,
                    tags_json = excluded.tags_json,
                    review_round = excluded.review_round
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
                    annotation.review_round,
                ),
            )
            connection.commit()

    def move_annotations(
        self,
        session_id: str,
        entries: list[tuple[str, ImageRecord, SessionAnnotation]],
    ) -> None:
        if not entries:
            return
        normalized_session = self._normalize_session_id(session_id)
        with sqlite3.connect(self._db_path) as connection:
            self._ensure_session_row(connection, normalized_session)
            for old_path, record, annotation in entries:
                connection.execute(
                    "DELETE FROM decisions WHERE session_id = ? AND path = ?",
                    (normalized_session, old_path),
                )
                if annotation.is_empty:
                    continue
                connection.execute(
                    """
                    INSERT INTO decisions (session_id, path, modified_ns, file_size, winner, reject, photoshop, rating, tags_json, review_round)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(session_id, path) DO UPDATE SET
                        modified_ns = excluded.modified_ns,
                        file_size = excluded.file_size,
                        winner = excluded.winner,
                        reject = excluded.reject,
                        photoshop = excluded.photoshop,
                        rating = excluded.rating,
                        tags_json = excluded.tags_json,
                        review_round = excluded.review_round
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
                        annotation.review_round,
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

    def load_correction_events(self, session_id: str, folder_path: str = "") -> list[dict[str, object]]:
        normalized_session = self._normalize_session_id(session_id)
        with sqlite3.connect(self._db_path) as connection:
            connection.row_factory = sqlite3.Row
            parameters: list[object] = [normalized_session]
            query = """
                SELECT
                    folder_path,
                    record_path,
                    other_path,
                    image_id,
                    other_image_id,
                    preferred_image_id,
                    group_id,
                    event_type,
                    decision,
                    source_mode,
                    ai_bucket,
                    ai_rank_in_group,
                    ai_group_size,
                    review_round,
                    payload_json,
                    created_at
                FROM correction_events
                WHERE session_id = ?
            """
            if folder_path:
                query += " AND folder_path = ?"
                parameters.append(folder_path)
            query += " ORDER BY id ASC"
            rows = connection.execute(query, parameters).fetchall()

        loaded: list[dict[str, object]] = []
        for row in rows:
            payload_json = str(row["payload_json"] or "{}")
            try:
                payload = json.loads(payload_json)
            except json.JSONDecodeError:
                payload = {}
            loaded.append(
                {
                    "folder_path": str(row["folder_path"] or ""),
                    "record_path": str(row["record_path"] or ""),
                    "other_path": str(row["other_path"] or ""),
                    "image_id": str(row["image_id"] or ""),
                    "other_image_id": str(row["other_image_id"] or ""),
                    "preferred_image_id": str(row["preferred_image_id"] or ""),
                    "group_id": str(row["group_id"] or ""),
                    "event_type": str(row["event_type"] or ""),
                    "decision": str(row["decision"] or ""),
                    "source_mode": str(row["source_mode"] or ""),
                    "ai_bucket": str(row["ai_bucket"] or ""),
                    "ai_rank_in_group": int(row["ai_rank_in_group"] or 0),
                    "ai_group_size": int(row["ai_group_size"] or 0),
                    "review_round": str(row["review_round"] or ""),
                    "payload": payload if isinstance(payload, dict) else {},
                    "created_at": str(row["created_at"] or ""),
                }
            )
        return loaded

    def record_correction_event(
        self,
        session_id: str,
        *,
        folder_path: str,
        record_path: str = "",
        other_path: str = "",
        image_id: str = "",
        other_image_id: str = "",
        preferred_image_id: str = "",
        group_id: str = "",
        event_type: str,
        decision: str = "",
        source_mode: str = "",
        ai_bucket: str = "",
        ai_rank_in_group: int = 0,
        ai_group_size: int = 0,
        review_round: str = "",
        payload: dict[str, object] | None = None,
    ) -> None:
        normalized_session = self._normalize_session_id(session_id)
        with sqlite3.connect(self._db_path) as connection:
            self._ensure_session_row(connection, normalized_session)
            connection.execute(
                """
                INSERT INTO correction_events (
                    session_id,
                    folder_path,
                    record_path,
                    other_path,
                    image_id,
                    other_image_id,
                    preferred_image_id,
                    group_id,
                    event_type,
                    decision,
                    source_mode,
                    ai_bucket,
                    ai_rank_in_group,
                    ai_group_size,
                    review_round,
                    payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    normalized_session,
                    folder_path,
                    record_path,
                    other_path,
                    image_id,
                    other_image_id,
                    preferred_image_id,
                    group_id,
                    event_type,
                    decision,
                    source_mode,
                    ai_bucket,
                    int(ai_rank_in_group),
                    int(ai_group_size),
                    review_round,
                    json.dumps(payload or {}),
                ),
            )
            connection.commit()

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
            if current_version < 5:
                self._migrate_to_v5(connection)
            if current_version < 6:
                self._migrate_to_v6(connection)
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
                review_round TEXT NOT NULL DEFAULT '',
                PRIMARY KEY(session_id, path),
                FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS correction_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                folder_path TEXT NOT NULL,
                record_path TEXT NOT NULL DEFAULT '',
                other_path TEXT NOT NULL DEFAULT '',
                image_id TEXT NOT NULL DEFAULT '',
                other_image_id TEXT NOT NULL DEFAULT '',
                preferred_image_id TEXT NOT NULL DEFAULT '',
                group_id TEXT NOT NULL DEFAULT '',
                event_type TEXT NOT NULL,
                decision TEXT NOT NULL DEFAULT '',
                source_mode TEXT NOT NULL DEFAULT '',
                ai_bucket TEXT NOT NULL DEFAULT '',
                ai_rank_in_group INTEGER NOT NULL DEFAULT 0,
                ai_group_size INTEGER NOT NULL DEFAULT 0,
                review_round TEXT NOT NULL DEFAULT '',
                payload_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
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
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_correction_events_session_folder
            ON correction_events(session_id, folder_path, created_at)
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_correction_events_record
            ON correction_events(session_id, record_path, event_type)
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

    def _migrate_to_v5(self, connection: sqlite3.Connection) -> None:
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(decisions)").fetchall()
        }
        if "review_round" not in columns:
            connection.execute(
                """
                ALTER TABLE decisions
                ADD COLUMN review_round TEXT NOT NULL DEFAULT ''
                """
            )

    def _migrate_to_v6(self, connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS correction_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                folder_path TEXT NOT NULL,
                record_path TEXT NOT NULL DEFAULT '',
                other_path TEXT NOT NULL DEFAULT '',
                image_id TEXT NOT NULL DEFAULT '',
                other_image_id TEXT NOT NULL DEFAULT '',
                preferred_image_id TEXT NOT NULL DEFAULT '',
                group_id TEXT NOT NULL DEFAULT '',
                event_type TEXT NOT NULL,
                decision TEXT NOT NULL DEFAULT '',
                source_mode TEXT NOT NULL DEFAULT '',
                ai_bucket TEXT NOT NULL DEFAULT '',
                ai_rank_in_group INTEGER NOT NULL DEFAULT 0,
                ai_group_size INTEGER NOT NULL DEFAULT 0,
                review_round TEXT NOT NULL DEFAULT '',
                payload_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
            )
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


def _chunked_values(values: list[str], size: int) -> list[list[str]]:
    return [values[index : index + size] for index in range(0, len(values), size)]
