from __future__ import annotations

"""Optional library layer for virtual collections and cross-folder cataloging.

The app remains folder-first:

- Review decisions and AI runs still belong to real folders and sessions.
- Virtual collections only store file references; they do not move or duplicate files.
- The catalog stores bundle-aware record snapshots so users can search across many
  folders when they want to, without forcing every workflow into a mandatory DAM.
"""

import json
import os
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from hashlib import sha1
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, Signal

from .formats import IMAGE_SUFFIXES
from .models import ImageRecord, ImageVariant
from .scan_cache import app_data_root
from .scanner import EDIT_DIRECTORIES, JPEG_PAIR_DIRECTORIES, normalize_filesystem_path, normalized_path_key, scan_folder


COLLECTION_KINDS: tuple[str, ...] = (
    "Portfolio Picks",
    "Edit Candidates",
    "Proofing Set",
    "Theme",
    "Custom",
)


@dataclass(slots=True, frozen=True)
class VirtualCollection:
    id: str
    name: str
    description: str = ""
    kind: str = "Custom"
    item_paths: tuple[str, ...] = ()
    item_count: int = 0
    created_at: str = ""
    updated_at: str = ""


@dataclass(slots=True, frozen=True)
class CatalogRoot:
    path: str
    enabled: bool = True
    indexed_folder_count: int = 0
    indexed_record_count: int = 0
    last_indexed_at: str = ""
    last_error: str = ""


@dataclass(slots=True, frozen=True)
class CatalogRefreshSummary:
    root_count: int
    folder_count: int
    record_count: int
    refreshed_roots: tuple[str, ...] = ()
    missing_roots: tuple[str, ...] = ()


class LibraryStore:
    SCHEMA_VERSION = 2

    def __init__(self) -> None:
        root = app_data_root()
        root.mkdir(parents=True, exist_ok=True)
        self._db_path = root / "library.sqlite3"
        self._initialize()

    def list_collections(self) -> list[VirtualCollection]:
        with self._connect() as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                """
                SELECT
                    c.id,
                    c.name,
                    c.description,
                    c.kind,
                    c.created_at,
                    c.updated_at,
                    COUNT(i.item_path) AS item_count
                FROM virtual_collections c
                LEFT JOIN collection_items i
                    ON i.collection_id = c.id
                GROUP BY c.id, c.name, c.description, c.kind, c.created_at, c.updated_at
                ORDER BY c.name COLLATE NOCASE, c.created_at ASC
                """
            ).fetchall()
        return [
            VirtualCollection(
                id=str(row["id"] or ""),
                name=str(row["name"] or ""),
                description=str(row["description"] or ""),
                kind=str(row["kind"] or "Custom"),
                item_count=int(row["item_count"] or 0),
                created_at=str(row["created_at"] or ""),
                updated_at=str(row["updated_at"] or ""),
            )
            for row in rows
            if row["id"] and row["name"]
        ]

    def load_collection(self, collection_id: str) -> VirtualCollection | None:
        normalized_id = (collection_id or "").strip()
        if not normalized_id:
            return None
        with self._connect() as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                """
                SELECT id, name, description, kind, created_at, updated_at
                FROM virtual_collections
                WHERE id = ?
                """,
                (normalized_id,),
            ).fetchone()
            if row is None:
                return None
            item_rows = connection.execute(
                """
                SELECT item_path
                FROM collection_items
                WHERE collection_id = ?
                ORDER BY sort_order ASC, added_at ASC
                """,
                (normalized_id,),
            ).fetchall()
        item_paths = tuple(str(item_row["item_path"] or "") for item_row in item_rows if item_row["item_path"])
        return VirtualCollection(
            id=str(row["id"] or ""),
            name=str(row["name"] or ""),
            description=str(row["description"] or ""),
            kind=str(row["kind"] or "Custom"),
            item_paths=item_paths,
            item_count=len(item_paths),
            created_at=str(row["created_at"] or ""),
            updated_at=str(row["updated_at"] or ""),
        )

    def find_collection_by_name(self, name: str) -> VirtualCollection | None:
        normalized_name = " ".join((name or "").split())
        if not normalized_name:
            return None
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT id
                FROM virtual_collections
                WHERE name = ?
                COLLATE NOCASE
                LIMIT 1
                """,
                (normalized_name,),
            ).fetchone()
        if row is None:
            return None
        return self.load_collection(str(row[0]))

    def create_collection(
        self,
        *,
        name: str,
        description: str = "",
        kind: str = "Custom",
        item_paths: tuple[str, ...] | list[str] = (),
    ) -> VirtualCollection:
        normalized_name = " ".join((name or "").split())
        if not normalized_name:
            raise ValueError("Choose a collection name.")
        normalized_kind = (kind or "Custom").strip() or "Custom"
        normalized_paths = _unique_paths(item_paths)
        with self._connect() as connection:
            collection_id = self._unique_collection_id(connection, normalized_name)
            connection.execute(
                """
                INSERT INTO virtual_collections (id, name, description, kind)
                VALUES (?, ?, ?, ?)
                """,
                (collection_id, normalized_name, description.strip(), normalized_kind),
            )
            self._replace_collection_items(connection, collection_id, normalized_paths)
            connection.commit()
        collection = self.load_collection(collection_id)
        if collection is None:
            raise RuntimeError("The collection could not be loaded after creation.")
        return collection

    def update_collection(self, collection: VirtualCollection) -> VirtualCollection:
        normalized_id = (collection.id or "").strip()
        normalized_name = " ".join((collection.name or "").split())
        if not normalized_id or not normalized_name:
            raise ValueError("Collection id and name are required.")
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE virtual_collections
                SET
                    name = ?,
                    description = ?,
                    kind = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    normalized_name,
                    collection.description.strip(),
                    (collection.kind or "Custom").strip() or "Custom",
                    normalized_id,
                ),
            )
            if connection.total_changes == 0:
                raise ValueError("Collection not found.")
            connection.commit()
        updated = self.load_collection(normalized_id)
        if updated is None:
            raise RuntimeError("Collection disappeared after update.")
        return updated

    def replace_collection_paths(self, collection_id: str, item_paths: tuple[str, ...] | list[str]) -> VirtualCollection | None:
        normalized_id = (collection_id or "").strip()
        if not normalized_id:
            return None
        normalized_paths = _unique_paths(item_paths)
        with self._connect() as connection:
            self._replace_collection_items(connection, normalized_id, normalized_paths)
            connection.execute(
                """
                UPDATE virtual_collections
                SET updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (normalized_id,),
            )
            connection.commit()
        return self.load_collection(normalized_id)

    def add_paths_to_collection(self, collection_id: str, item_paths: tuple[str, ...] | list[str]) -> VirtualCollection | None:
        normalized_id = (collection_id or "").strip()
        normalized_paths = _unique_paths(item_paths)
        if not normalized_id or not normalized_paths:
            return self.load_collection(normalized_id) if normalized_id else None
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT COALESCE(MAX(sort_order), -1)
                FROM collection_items
                WHERE collection_id = ?
                """,
                (normalized_id,),
            ).fetchone()
            start_order = int(row[0] or -1) + 1
            for offset, path in enumerate(normalized_paths):
                connection.execute(
                    """
                    INSERT INTO collection_items (collection_id, item_path, sort_order)
                    VALUES (?, ?, ?)
                    ON CONFLICT(collection_id, item_path) DO NOTHING
                    """,
                    (normalized_id, path, start_order + offset),
                )
            connection.execute(
                """
                UPDATE virtual_collections
                SET updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (normalized_id,),
            )
            connection.commit()
        return self.load_collection(normalized_id)

    def remove_paths_from_collection(self, collection_id: str, item_paths: tuple[str, ...] | list[str]) -> VirtualCollection | None:
        normalized_id = (collection_id or "").strip()
        normalized_paths = _unique_paths(item_paths)
        if not normalized_id or not normalized_paths:
            return self.load_collection(normalized_id) if normalized_id else None
        with self._connect() as connection:
            connection.executemany(
                """
                DELETE FROM collection_items
                WHERE collection_id = ?
                  AND item_path = ?
                """,
                [(normalized_id, path) for path in normalized_paths],
            )
            connection.execute(
                """
                UPDATE virtual_collections
                SET updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (normalized_id,),
            )
            self._resequence_collection_items(connection, normalized_id)
            connection.commit()
        return self.load_collection(normalized_id)

    def delete_collection(self, collection_id: str) -> bool:
        normalized_id = (collection_id or "").strip()
        if not normalized_id:
            return False
        with self._connect() as connection:
            connection.execute("DELETE FROM virtual_collections WHERE id = ?", (normalized_id,))
            deleted = connection.total_changes > 0
            connection.commit()
        return deleted

    def list_catalog_roots(self) -> list[CatalogRoot]:
        with self._connect() as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                """
                SELECT
                    root_path,
                    enabled,
                    indexed_folder_count,
                    indexed_record_count,
                    last_indexed_at,
                    last_error
                FROM catalog_roots
                ORDER BY root_path COLLATE NOCASE
                """
            ).fetchall()
        return [
            CatalogRoot(
                path=str(row["root_path"] or ""),
                enabled=bool(row["enabled"]),
                indexed_folder_count=int(row["indexed_folder_count"] or 0),
                indexed_record_count=int(row["indexed_record_count"] or 0),
                last_indexed_at=str(row["last_indexed_at"] or ""),
                last_error=str(row["last_error"] or ""),
            )
            for row in rows
            if row["root_path"]
        ]

    def is_catalog_root(self, path: str) -> bool:
        normalized = normalize_filesystem_path(path)
        if not normalized:
            return False
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT 1
                FROM catalog_roots
                WHERE root_path = ?
                LIMIT 1
                """,
                (normalized,),
            ).fetchone()
        return row is not None

    def add_catalog_root(self, path: str, *, enabled: bool = True) -> CatalogRoot:
        normalized = normalize_filesystem_path(path)
        if not normalized:
            raise ValueError("Choose a folder to catalog.")
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO catalog_roots (root_path, enabled)
                VALUES (?, ?)
                ON CONFLICT(root_path) DO UPDATE SET
                    enabled = excluded.enabled,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (normalized, int(enabled)),
            )
            connection.commit()
        return next(root for root in self.list_catalog_roots() if normalized_path_key(root.path) == normalized_path_key(normalized))

    def remove_catalog_root(self, path: str) -> bool:
        normalized = normalize_filesystem_path(path)
        if not normalized:
            return False
        with self._connect() as connection:
            connection.execute("DELETE FROM catalog_folder_state WHERE root_path = ?", (normalized,))
            if self._fts_available(connection):
                connection.execute("DELETE FROM catalog_records_fts WHERE root_path = ?", (normalized,))
            connection.execute("DELETE FROM catalog_roots WHERE root_path = ?", (normalized,))
            deleted = connection.total_changes > 0
            connection.commit()
        return deleted

    def refresh_catalog(
        self,
        root_paths: tuple[str, ...] | list[str] | None = None,
        *,
        progress_callback=None,
    ) -> CatalogRefreshSummary:
        requested_roots = _unique_paths(root_paths or [root.path for root in self.list_catalog_roots() if root.enabled])
        if not requested_roots:
            return CatalogRefreshSummary(root_count=0, folder_count=0, record_count=0)

        total_folders = 0
        total_records = 0
        refreshed_roots: list[str] = []
        missing_roots: list[str] = []
        total_roots = len(requested_roots)

        with self._connect() as connection:
            fts_ready = self._fts_available(connection)
            for index, root_path in enumerate(requested_roots, start=1):
                normalized_root = normalize_filesystem_path(root_path)
                if progress_callback is not None:
                    progress_callback(index - 1, total_roots, f"Scanning catalog root: {normalized_root}")
                self._ensure_catalog_root(connection, normalized_root)
                if not normalized_root or not os.path.isdir(normalized_root):
                    connection.execute("DELETE FROM catalog_records WHERE root_path = ?", (normalized_root,))
                    connection.execute("DELETE FROM catalog_folder_state WHERE root_path = ?", (normalized_root,))
                    if fts_ready:
                        connection.execute("DELETE FROM catalog_records_fts WHERE root_path = ?", (normalized_root,))
                    connection.execute(
                        """
                        UPDATE catalog_roots
                        SET
                            indexed_folder_count = 0,
                            indexed_record_count = 0,
                            last_indexed_at = CURRENT_TIMESTAMP,
                            last_error = ?
                        WHERE root_path = ?
                        """,
                        ("Folder not found.", normalized_root),
                    )
                    missing_roots.append(normalized_root)
                    continue

                folder_state_rows = connection.execute(
                    """
                    SELECT folder_path, folder_signature, record_count
                    FROM catalog_folder_state
                    WHERE root_path = ?
                    """,
                    (normalized_root,),
                ).fetchall()
                folder_state_by_path = {
                    str(row[0]): (str(row[1] or ""), int(row[2] or 0))
                    for row in folder_state_rows
                    if row[0]
                }
                seen_folders: set[str] = set()
                indexed_folder_count = 0
                for folder in _iter_catalog_candidate_folders(normalized_root):
                    seen_folders.add(folder)
                    signature = _catalog_folder_signature(folder)
                    previous_signature, previous_count = folder_state_by_path.get(folder, ("", -1))
                    if previous_signature == signature:
                        if previous_count > 0:
                            indexed_folder_count += 1
                        continue

                    try:
                        records = scan_folder(folder)
                    except Exception:
                        continue
                    snapshots: list[tuple[str, str, str, str, int, int, str]] = []
                    for record in records:
                        snapshots.append(
                            (
                                record.path,
                                normalized_root,
                                normalize_filesystem_path(folder),
                                record.name,
                                int(record.modified_ns),
                                int(record.size),
                                json.dumps(_serialize_record(record), separators=(",", ":")),
                            )
                        )

                    connection.execute(
                        "DELETE FROM catalog_records WHERE root_path = ? AND folder_path = ?",
                        (normalized_root, folder),
                    )
                    if snapshots:
                        _insert_catalog_snapshots(connection, snapshots)
                    if fts_ready:
                        connection.execute(
                            "DELETE FROM catalog_records_fts WHERE root_path = ? AND folder_path = ?",
                            (normalized_root, folder),
                        )
                        if snapshots:
                            connection.executemany(
                                """
                                INSERT INTO catalog_records_fts (path, name, root_path, folder_path)
                                VALUES (?, ?, ?, ?)
                                """,
                                [(snapshot[0], snapshot[3], snapshot[1], snapshot[2]) for snapshot in snapshots],
                            )

                    record_count = len(snapshots)
                    connection.execute(
                        """
                        INSERT INTO catalog_folder_state (
                            root_path,
                            folder_path,
                            folder_signature,
                            record_count,
                            indexed_at
                        )
                        VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                        ON CONFLICT(root_path, folder_path) DO UPDATE SET
                            folder_signature = excluded.folder_signature,
                            record_count = excluded.record_count,
                            indexed_at = CURRENT_TIMESTAMP
                        """,
                        (normalized_root, folder, signature, record_count),
                    )
                    if record_count > 0:
                        indexed_folder_count += 1

                stale_folders = [folder for folder in folder_state_by_path.keys() if folder not in seen_folders]
                if stale_folders:
                    connection.executemany(
                        "DELETE FROM catalog_records WHERE root_path = ? AND folder_path = ?",
                        [(normalized_root, folder) for folder in stale_folders],
                    )
                    connection.executemany(
                        "DELETE FROM catalog_folder_state WHERE root_path = ? AND folder_path = ?",
                        [(normalized_root, folder) for folder in stale_folders],
                    )
                    if fts_ready:
                        connection.executemany(
                            "DELETE FROM catalog_records_fts WHERE root_path = ? AND folder_path = ?",
                            [(normalized_root, folder) for folder in stale_folders],
                        )

                total_root_records = int(
                    connection.execute(
                        "SELECT COUNT(*) FROM catalog_records WHERE root_path = ?",
                        (normalized_root,),
                    ).fetchone()[0]
                    or 0
                )
                connection.execute(
                    """
                    UPDATE catalog_roots
                    SET
                        indexed_folder_count = ?,
                        indexed_record_count = ?,
                        last_indexed_at = CURRENT_TIMESTAMP,
                        last_error = ''
                    WHERE root_path = ?
                    """,
                    (indexed_folder_count, total_root_records, normalized_root),
                )
                total_folders += indexed_folder_count
                total_records += total_root_records
                refreshed_roots.append(normalized_root)
                if progress_callback is not None:
                    progress_callback(
                        index,
                        total_roots,
                        f"Indexed {total_root_records} image bundle(s) from {Path(normalized_root).name or normalized_root}",
                    )
            connection.commit()

        return CatalogRefreshSummary(
            root_count=len(requested_roots),
            folder_count=total_folders,
            record_count=total_records,
            refreshed_roots=tuple(refreshed_roots),
            missing_roots=tuple(missing_roots),
        )

    def search_catalog(
        self,
        *,
        search_text: str = "",
        root_path: str = "",
        limit: int = 800,
    ) -> list[ImageRecord]:
        normalized_root = normalize_filesystem_path(root_path)
        tokens = [token.casefold() for token in search_text.split() if token.strip()]
        with self._connect() as connection:
            rows = []
            if tokens and self._fts_available(connection):
                fts_terms: list[str] = []
                for token in tokens:
                    escaped = token.replace('"', '""')
                    fts_terms.append(f'"{escaped}"*')
                fts_query = " AND ".join(fts_terms)
                query = """
                    SELECT c.record_json
                    FROM catalog_records c
                    JOIN catalog_records_fts f
                        ON f.path = c.path
                    WHERE 1 = 1
                """
                parameters: list[object] = []
                if normalized_root:
                    query += " AND f.root_path = ?"
                    parameters.append(normalized_root)
                query += " AND f.catalog_records_fts MATCH ?"
                parameters.append(fts_query)
                query += " ORDER BY c.modified_ns DESC, c.name COLLATE NOCASE LIMIT ?"
                parameters.append(max(1, int(limit)))
                try:
                    rows = connection.execute(query, parameters).fetchall()
                except sqlite3.OperationalError:
                    rows = []

            if not rows:
                query = """
                    SELECT record_json
                    FROM catalog_records
                    WHERE 1 = 1
                """
                parameters = []
                if normalized_root:
                    query += " AND root_path = ?"
                    parameters.append(normalized_root)
                for token in tokens:
                    query += " AND (LOWER(name) LIKE ? OR LOWER(path) LIKE ?)"
                    like = f"%{token}%"
                    parameters.extend([like, like])
                query += " ORDER BY modified_ns DESC, name COLLATE NOCASE LIMIT ?"
                parameters.append(max(1, int(limit)))
                rows = connection.execute(query, parameters).fetchall()
        return [
            record
            for row in rows
            for record in [_deserialize_record_json(row[0])]
            if record is not None
        ]

    def load_catalog_records_for_paths(self, paths: tuple[str, ...] | list[str]) -> dict[str, ImageRecord]:
        normalized_paths = _unique_paths(paths)
        if not normalized_paths:
            return {}
        loaded: dict[str, ImageRecord] = {}
        with self._connect() as connection:
            connection.row_factory = sqlite3.Row
            for chunk in _chunked(normalized_paths, 300):
                placeholders = ",".join("?" for _ in chunk)
                rows = connection.execute(
                    f"""
                    SELECT path, record_json
                    FROM catalog_records
                    WHERE path IN ({placeholders})
                    """,
                    chunk,
                ).fetchall()
                for row in rows:
                    record = _deserialize_record_json(str(row["record_json"] or ""))
                    if record is None:
                        continue
                    loaded[normalized_path_key(str(row["path"] or record.path))] = record
        return loaded

    def _initialize(self) -> None:
        with self._connect() as connection:
            current_version = connection.execute("PRAGMA user_version").fetchone()[0]
            self._create_schema(connection)
            if current_version < 2:
                self._rebuild_catalog_fts(connection)
            if current_version < self.SCHEMA_VERSION:
                connection.execute(f"PRAGMA user_version = {self.SCHEMA_VERSION}")
            connection.commit()

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self._db_path)
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
        finally:
            connection.close()

    def _create_schema(self, connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS virtual_collections (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                kind TEXT NOT NULL DEFAULT 'Custom',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS collection_items (
                collection_id TEXT NOT NULL,
                item_path TEXT NOT NULL,
                sort_order INTEGER NOT NULL DEFAULT 0,
                added_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(collection_id, item_path),
                FOREIGN KEY(collection_id) REFERENCES virtual_collections(id) ON DELETE CASCADE
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_virtual_collections_name
            ON virtual_collections(name COLLATE NOCASE)
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS catalog_roots (
                root_path TEXT PRIMARY KEY,
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                last_indexed_at TEXT NOT NULL DEFAULT '',
                indexed_folder_count INTEGER NOT NULL DEFAULT 0,
                indexed_record_count INTEGER NOT NULL DEFAULT 0,
                last_error TEXT NOT NULL DEFAULT ''
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS catalog_records (
                path TEXT PRIMARY KEY,
                root_path TEXT NOT NULL,
                folder_path TEXT NOT NULL,
                name TEXT NOT NULL,
                modified_ns INTEGER NOT NULL DEFAULT 0,
                file_size INTEGER NOT NULL DEFAULT 0,
                record_json TEXT NOT NULL,
                indexed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(root_path) REFERENCES catalog_roots(root_path) ON DELETE CASCADE
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_catalog_records_root_folder
            ON catalog_records(root_path, folder_path)
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_catalog_records_name
            ON catalog_records(name COLLATE NOCASE)
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS catalog_folder_state (
                root_path TEXT NOT NULL,
                folder_path TEXT NOT NULL,
                folder_signature TEXT NOT NULL DEFAULT '',
                record_count INTEGER NOT NULL DEFAULT 0,
                indexed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(root_path, folder_path),
                FOREIGN KEY(root_path) REFERENCES catalog_roots(root_path) ON DELETE CASCADE
            )
            """
        )
        try:
            connection.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS catalog_records_fts
                USING fts5(path, name, root_path, folder_path, tokenize = 'unicode61')
                """
            )
        except sqlite3.OperationalError:
            pass

    def _unique_collection_id(self, connection: sqlite3.Connection, name: str) -> str:
        base = _key_from_name(name) or "collection"
        candidate = base
        suffix = 2
        while connection.execute(
            "SELECT 1 FROM virtual_collections WHERE id = ? LIMIT 1",
            (candidate,),
        ).fetchone():
            candidate = f"{base}_{suffix}"
            suffix += 1
        return candidate

    def _replace_collection_items(self, connection: sqlite3.Connection, collection_id: str, item_paths: list[str]) -> None:
        connection.execute("DELETE FROM collection_items WHERE collection_id = ?", (collection_id,))
        connection.executemany(
            """
            INSERT INTO collection_items (collection_id, item_path, sort_order)
            VALUES (?, ?, ?)
            """,
            [(collection_id, path, index) for index, path in enumerate(item_paths)],
        )
        connection.execute(
            """
            UPDATE virtual_collections
            SET updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (collection_id,),
        )

    def _resequence_collection_items(self, connection: sqlite3.Connection, collection_id: str) -> None:
        rows = connection.execute(
            """
            SELECT item_path
            FROM collection_items
            WHERE collection_id = ?
            ORDER BY sort_order ASC, added_at ASC
            """,
            (collection_id,),
        ).fetchall()
        for index, row in enumerate(rows):
            connection.execute(
                """
                UPDATE collection_items
                SET sort_order = ?
                WHERE collection_id = ?
                  AND item_path = ?
                """,
                (index, collection_id, row[0]),
            )

    def _ensure_catalog_root(self, connection: sqlite3.Connection, root_path: str) -> None:
        connection.execute(
            """
            INSERT INTO catalog_roots (root_path, enabled)
            VALUES (?, 1)
            ON CONFLICT(root_path) DO UPDATE SET
                updated_at = CURRENT_TIMESTAMP
            """,
            (root_path,),
        )

    def _fts_available(self, connection: sqlite3.Connection) -> bool:
        try:
            connection.execute("SELECT 1 FROM catalog_records_fts LIMIT 1").fetchone()
        except sqlite3.OperationalError:
            return False
        return True

    def _rebuild_catalog_fts(self, connection: sqlite3.Connection) -> None:
        if not self._fts_available(connection):
            return
        connection.execute("DELETE FROM catalog_records_fts")
        rows = connection.execute(
            """
            SELECT path, name, root_path, folder_path
            FROM catalog_records
            """
        ).fetchall()
        if not rows:
            return
        for chunk_start in range(0, len(rows), 600):
            chunk = rows[chunk_start:chunk_start + 600]
            connection.executemany(
                """
                INSERT INTO catalog_records_fts (path, name, root_path, folder_path)
                VALUES (?, ?, ?, ?)
                """,
                chunk,
            )


class CatalogRefreshSignals(QObject):
    started = Signal(int)
    progress = Signal(int, int, str)
    finished = Signal(object)
    failed = Signal(str)


class CatalogRefreshTask(QRunnable):
    def __init__(self, root_paths: tuple[str, ...] | list[str] | None = None) -> None:
        super().__init__()
        self.root_paths = tuple(_unique_paths(root_paths or ()))
        self.signals = CatalogRefreshSignals()
        self.setAutoDelete(True)

    def run(self) -> None:
        try:
            store = LibraryStore()
            target_roots = self.root_paths or tuple(root.path for root in store.list_catalog_roots() if root.enabled)
            self.signals.started.emit(max(1, len(target_roots)))
            summary = store.refresh_catalog(
                target_roots,
                progress_callback=lambda current, total, message: self.signals.progress.emit(current, total, message),
            )
        except Exception as exc:  # pragma: no cover - runtime UI worker path
            self.signals.failed.emit(str(exc))
            return
        self.signals.finished.emit(summary)


def _insert_catalog_snapshots(
    connection: sqlite3.Connection,
    snapshots: list[tuple[str, str, str, str, int, int, str]],
    *,
    chunk_size: int = 400,
) -> None:
    for chunk_start in range(0, len(snapshots), chunk_size):
        chunk = snapshots[chunk_start:chunk_start + chunk_size]
        connection.executemany(
            """
            INSERT INTO catalog_records (
                path,
                root_path,
                folder_path,
                name,
                modified_ns,
                file_size,
                record_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            chunk,
        )


def _catalog_folder_signature(folder_path: str) -> str:
    digest = sha1(usedforsecurity=False)
    try:
        with os.scandir(folder_path) as entries:
            for entry in sorted(entries, key=lambda candidate: candidate.name.casefold()):
                entry_name = entry.name.casefold()
                try:
                    stat_result = entry.stat(follow_symlinks=False)
                except OSError:
                    continue
                modified_ns = int(getattr(stat_result, "st_mtime_ns", int(stat_result.st_mtime * 1_000_000_000)))
                size = int(stat_result.st_size or 0)
                kind = "d" if entry.is_dir(follow_symlinks=False) else "f"
                digest.update(f"{kind}|{entry_name}|{modified_ns}|{size}\n".encode("utf-8"))
    except OSError:
        return ""
    return digest.hexdigest()


def _iter_catalog_candidate_folders(root_path: str):
    normalized_root = normalize_filesystem_path(root_path)
    if not normalized_root or not os.path.isdir(normalized_root):
        return
    for current_dir, dirnames, filenames in os.walk(normalized_root):
        lower_dirs = {name.casefold() for name in dirnames}
        if any(Path(name).suffix.casefold() in IMAGE_SUFFIXES for name in filenames) or lower_dirs.intersection(JPEG_PAIR_DIRECTORIES | EDIT_DIRECTORIES):
            yield normalize_filesystem_path(current_dir)


def _serialize_record(record: ImageRecord) -> dict[str, object]:
    return {
        "path": record.path,
        "name": record.name,
        "size": record.size,
        "modified_ns": record.modified_ns,
        "companion_paths": list(record.companion_paths),
        "edited_paths": list(record.edited_paths),
        "variants": [
            {
                "path": variant.path,
                "name": variant.name,
                "size": variant.size,
                "modified_ns": variant.modified_ns,
            }
            for variant in record.display_variants
        ],
    }


def _deserialize_record_json(raw: str) -> ImageRecord | None:
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    try:
        return ImageRecord(
            path=str(payload["path"]),
            name=str(payload["name"]),
            size=int(payload["size"]),
            modified_ns=int(payload["modified_ns"]),
            companion_paths=tuple(str(path) for path in payload.get("companion_paths", [])),
            edited_paths=tuple(str(path) for path in payload.get("edited_paths", [])),
            variants=tuple(
                ImageVariant(
                    path=str(variant["path"]),
                    name=str(variant["name"]),
                    size=int(variant["size"]),
                    modified_ns=int(variant["modified_ns"]),
                )
                for variant in payload.get("variants", [])
                if isinstance(variant, dict)
            ),
        )
    except (KeyError, TypeError, ValueError):
        return None


def _unique_paths(paths: tuple[str, ...] | list[str]) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()
    for path in paths:
        normalized = normalize_filesystem_path(path)
        key = normalized_path_key(normalized)
        if not normalized or key in seen:
            continue
        seen.add(key)
        ordered.append(normalized)
    return ordered


def _key_from_name(name: str) -> str:
    text = " ".join((name or "").strip().split()).casefold()
    if not text:
        return ""
    cleaned = [character if character.isalnum() else "_" for character in text]
    normalized = "".join(cleaned).strip("_")
    while "__" in normalized:
        normalized = normalized.replace("__", "_")
    return normalized


def _chunked(items: list[str], size: int):
    for index in range(0, len(items), size):
        yield items[index:index + size]
