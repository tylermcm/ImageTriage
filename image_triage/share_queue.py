from __future__ import annotations

"""Persistent records for local Share to Phone packages."""

import json
import shutil
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from .scan_cache import app_data_root


SHARE_STATUSES = ("draft", "ready", "transferred", "posted", "archived", "failed")


@dataclass(slots=True, frozen=True)
class ShareQueueEntry:
    id: str
    name: str
    status: str
    target: str
    account: str
    caption: str
    preset_key: str
    source_paths: tuple[str, ...]
    output_paths: tuple[str, ...]
    alt_text: tuple[str, ...]
    package_dir: str
    created_at: str
    updated_at: str
    transferred_at: str = ""
    posted_at: str = ""
    error: str = ""


class ShareQueueStore:
    """SQLite-backed queue kept separate from the folder and collection stores."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        root = app_data_root()
        root.mkdir(parents=True, exist_ok=True)
        self.db_path = Path(db_path) if db_path is not None else root / "share_queue.sqlite3"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def create_ready(
        self,
        *,
        name: str,
        target: str,
        account: str,
        caption: str,
        preset_key: str,
        source_paths: tuple[str, ...],
        output_paths: tuple[str, ...],
        alt_text: tuple[str, ...],
        package_dir: str,
    ) -> ShareQueueEntry:
        entry_id = uuid4().hex
        now = _utc_now()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO share_queue (
                    id, name, status, target, account, caption, preset_key,
                    source_paths_json, output_paths_json, alt_text_json,
                    package_dir, created_at, updated_at
                ) VALUES (?, ?, 'ready', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry_id,
                    _clean_name(name),
                    target.strip(),
                    account.strip(),
                    caption,
                    preset_key.strip(),
                    _dump_strings(source_paths),
                    _dump_strings(output_paths),
                    _dump_strings(alt_text),
                    package_dir,
                    now,
                    now,
                ),
            )
            connection.commit()
        entry = self.get(entry_id)
        if entry is None:  # pragma: no cover - defensive persistence failure
            raise RuntimeError("The phone-share queue entry could not be loaded after creation.")
        return entry

    def get(self, entry_id: str) -> ShareQueueEntry | None:
        with self._connect() as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute("SELECT * FROM share_queue WHERE id = ?", (entry_id,)).fetchone()
        return _entry_from_row(row) if row is not None else None

    def list_entries(self, *, include_archived: bool = False) -> list[ShareQueueEntry]:
        query = "SELECT * FROM share_queue"
        parameters: tuple[object, ...] = ()
        if not include_archived:
            query += " WHERE status != ?"
            parameters = ("archived",)
        query += " ORDER BY created_at DESC, id DESC"
        with self._connect() as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(query, parameters).fetchall()
        return [_entry_from_row(row) for row in rows]

    def mark_transferred(self, entry_id: str) -> ShareQueueEntry | None:
        current = self.get(entry_id)
        if current is None or current.status in {"posted", "archived"}:
            return current
        now = _utc_now()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE share_queue
                SET status = 'transferred', transferred_at = COALESCE(NULLIF(transferred_at, ''), ?), updated_at = ?
                WHERE id = ?
                """,
                (now, now, entry_id),
            )
            connection.commit()
        return self.get(entry_id)

    def set_status(self, entry_id: str, status: str) -> ShareQueueEntry | None:
        normalized = status.strip().lower()
        if normalized not in SHARE_STATUSES:
            raise ValueError(f"Unsupported share status: {status}")
        now = _utc_now()
        posted_at = now if normalized == "posted" else None
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE share_queue
                SET status = ?, posted_at = CASE WHEN ? IS NULL THEN posted_at ELSE ? END, updated_at = ?
                WHERE id = ?
                """,
                (normalized, posted_at, posted_at, now, entry_id),
            )
            connection.commit()
        return self.get(entry_id)

    def delete(self, entry_id: str, *, remove_files: bool = True) -> bool:
        entry = self.get(entry_id)
        if entry is None:
            return False
        with self._connect() as connection:
            connection.execute("DELETE FROM share_queue WHERE id = ?", (entry_id,))
            connection.commit()
        if remove_files and entry.package_dir:
            package_path = Path(entry.package_dir)
            share_root = (app_data_root() / "share_packages").resolve()
            try:
                resolved = package_path.resolve()
                if resolved != share_root and share_root in resolved.parents:
                    shutil.rmtree(resolved, ignore_errors=True)
            except OSError:
                pass
        return True

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.db_path, timeout=10)
        try:
            yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS share_queue (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    target TEXT NOT NULL DEFAULT '',
                    account TEXT NOT NULL DEFAULT '',
                    caption TEXT NOT NULL DEFAULT '',
                    preset_key TEXT NOT NULL DEFAULT '',
                    source_paths_json TEXT NOT NULL DEFAULT '[]',
                    output_paths_json TEXT NOT NULL DEFAULT '[]',
                    alt_text_json TEXT NOT NULL DEFAULT '[]',
                    package_dir TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    transferred_at TEXT NOT NULL DEFAULT '',
                    posted_at TEXT NOT NULL DEFAULT '',
                    error TEXT NOT NULL DEFAULT ''
                )
                """
            )
            connection.execute("CREATE INDEX IF NOT EXISTS idx_share_queue_created ON share_queue(created_at DESC)")
            connection.commit()


def _entry_from_row(row: sqlite3.Row) -> ShareQueueEntry:
    return ShareQueueEntry(
        id=str(row["id"]),
        name=str(row["name"]),
        status=str(row["status"]),
        target=str(row["target"]),
        account=str(row["account"]),
        caption=str(row["caption"]),
        preset_key=str(row["preset_key"]),
        source_paths=_load_strings(row["source_paths_json"]),
        output_paths=_load_strings(row["output_paths_json"]),
        alt_text=_load_strings(row["alt_text_json"]),
        package_dir=str(row["package_dir"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        transferred_at=str(row["transferred_at"]),
        posted_at=str(row["posted_at"]),
        error=str(row["error"]),
    )


def _clean_name(value: str) -> str:
    return " ".join(value.split()) or "Phone Share"


def _dump_strings(values: tuple[str, ...]) -> str:
    return json.dumps([str(value) for value in values], ensure_ascii=False)


def _load_strings(payload: object) -> tuple[str, ...]:
    try:
        values = json.loads(str(payload or "[]"))
    except (TypeError, ValueError):
        return ()
    if not isinstance(values, list):
        return ()
    return tuple(str(value) for value in values if isinstance(value, str))


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
