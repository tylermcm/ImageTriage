"""Catalog migration 11: give every derived cache the parent rows its foreign keys need (audit D19).

Every cache table refers to ``catalog_folders``, and the per-photo feature cache referred to ``catalog_records``. Only
saving a folder listing wrote those two tables, and the shell never saves listings, so its cache writes failed the
foreign key. From this version on a cache write makes its own folder row (``repository._ensure_folder_row``). This
migration brings an existing database to the same shape, in one transaction, without dropping a row:

1. The feature cache is rebuilt to hang off the folder instead of the listing's record. (It also stops being wiped,
   through the cascade, each time a listing is saved again.)
2. Rows written while foreign keys were not enforced get the parents they lack: a folder row marked ``derived`` (its
   path recovered from the rows where it can be), and an empty cache header for recommendations, groups and AI results
   that have none. An empty header never matches a real cache key, so such rows are replaced by the next real write.
3. A row that still has no parent (a bundle member whose photo is not in the listing) is moved to ``catalog_orphans``
   as JSON, so the database is consistent and nothing is lost.
4. On a case-sensitive platform, folder keys that an earlier build folded to lower case are rewritten as the current
   key (``path_keys``, audit D21). The folder row remembers the real path, so this cannot attach a cache to another
   folder; a key that is already taken is left alone.
"""
from __future__ import annotations

import json
import os
import sqlite3

from ..path_keys import legacy_path_key, path_key

DERIVED_FOLDER_SOURCE = "derived"

_FEATURE_COLUMNS = (
    "folder_key", "record_path", "cache_key", "source_path", "metadata_provider_id", "camera_make", "camera_model", "camera",
    "exposure", "aperture", "iso", "focal_length", "lens", "captured_at", "captured_at_iso", "orientation", "exposure_seconds",
    "aperture_value", "iso_value", "focal_length_value", "width", "height", "dhash_hex", "avg_luma", "detail_score",
    "exposure_score", "sha1_digest", "updated_at",
)

_FEATURE_TABLE = """
    CREATE TABLE catalog_review_feature_cache_v11 (
        folder_key TEXT NOT NULL,
        record_path TEXT NOT NULL,
        cache_key TEXT NOT NULL,
        source_path TEXT NOT NULL DEFAULT '',
        metadata_provider_id TEXT NOT NULL DEFAULT '',
        camera_make TEXT NOT NULL DEFAULT '',
        camera_model TEXT NOT NULL DEFAULT '',
        camera TEXT NOT NULL DEFAULT '',
        exposure TEXT NOT NULL DEFAULT '',
        aperture TEXT NOT NULL DEFAULT '',
        iso TEXT NOT NULL DEFAULT '',
        focal_length TEXT NOT NULL DEFAULT '',
        lens TEXT NOT NULL DEFAULT '',
        captured_at TEXT NOT NULL DEFAULT '',
        captured_at_iso TEXT NOT NULL DEFAULT '',
        orientation TEXT NOT NULL DEFAULT '',
        exposure_seconds REAL,
        aperture_value REAL,
        iso_value REAL,
        focal_length_value REAL,
        width INTEGER NOT NULL DEFAULT 0,
        height INTEGER NOT NULL DEFAULT 0,
        dhash_hex TEXT NOT NULL DEFAULT '',
        avg_luma REAL NOT NULL DEFAULT 0.0,
        detail_score REAL NOT NULL DEFAULT 0.0,
        exposure_score REAL NOT NULL DEFAULT 0.0,
        sha1_digest TEXT NOT NULL DEFAULT '',
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (folder_key, record_path),
        FOREIGN KEY (folder_key) REFERENCES catalog_folders(folder_key) ON DELETE CASCADE
    )
"""

# Tables whose folder_key refers straight to catalog_folders.
_FOLDER_CHILDREN = (
    "catalog_records", "catalog_review_scoring_cache", "catalog_review_grouping_cache", "catalog_review_feature_cache",
    "catalog_ai_bundle_cache", "catalog_ai_workflow_cache",
)
# Where a folder's real path can be read back from: (table, column holding a path of a file in that folder).
_PATH_SOURCES = (
    ("catalog_records", "record_path"), ("catalog_review_feature_cache", "record_path"), ("catalog_ai_results", "file_path"),
    ("catalog_review_group_members", "record_path"), ("catalog_review_scoring_recommendations", "record_path"),
)


def repair_derived_caches(connection: sqlite3.Connection) -> None:
    """Run inside the migration's transaction. Parents and children are rewritten in whatever order is convenient; the
    constraints are checked when the transaction commits."""
    connection.execute("PRAGMA defer_foreign_keys = ON")
    _rebuild_feature_cache(connection)
    _add_missing_cache_headers(connection)
    _add_missing_folder_rows(connection)
    _set_aside_rows_without_a_parent(connection)
    _rewrite_folded_folder_keys(connection)


def _rebuild_feature_cache(connection: sqlite3.Connection) -> None:
    columns = ", ".join(_FEATURE_COLUMNS)
    connection.execute("DROP TABLE IF EXISTS catalog_review_feature_cache_v11")
    connection.execute(_FEATURE_TABLE)
    connection.execute(f"INSERT INTO catalog_review_feature_cache_v11({columns}) SELECT {columns} FROM catalog_review_feature_cache")
    connection.execute("DROP TABLE catalog_review_feature_cache")
    connection.execute("ALTER TABLE catalog_review_feature_cache_v11 RENAME TO catalog_review_feature_cache")
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_catalog_review_feature_cache_lookup ON catalog_review_feature_cache(folder_key, cache_key)"
    )


def _add_missing_cache_headers(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        INSERT OR IGNORE INTO catalog_review_scoring_cache(folder_key, session_id, cache_key)
        SELECT DISTINCT folder_key, session_id, '' FROM catalog_review_scoring_recommendations
        """
    )
    connection.execute(
        "INSERT OR IGNORE INTO catalog_review_groups(folder_key, group_id) SELECT DISTINCT folder_key, group_id FROM catalog_review_group_members"
    )
    connection.execute(
        "INSERT OR IGNORE INTO catalog_review_grouping_cache(folder_key, cache_key) SELECT DISTINCT folder_key, '' FROM catalog_review_groups"
    )
    connection.execute(
        "INSERT OR IGNORE INTO catalog_ai_bundle_cache(folder_key, cache_key) SELECT DISTINCT folder_key, '' FROM catalog_ai_results"
    )


def _add_missing_folder_rows(connection: sqlite3.Connection) -> None:
    missing: set[str] = set()
    for table in _FOLDER_CHILDREN:
        rows = connection.execute(
            f"SELECT DISTINCT t.folder_key FROM {table} AS t "
            "WHERE NOT EXISTS (SELECT 1 FROM catalog_folders AS f WHERE f.folder_key = t.folder_key)"
        ).fetchall()
        missing.update(str(row[0]) for row in rows)
    for folder_key in sorted(missing):
        connection.execute(
            "INSERT OR IGNORE INTO catalog_folders(folder_key, folder_path, record_count, source) VALUES (?, ?, 0, ?)",
            (folder_key, _recovered_folder_path(connection, folder_key), DERIVED_FOLDER_SOURCE),
        )


def _recovered_folder_path(connection: sqlite3.Connection, folder_key: str) -> str:
    """The folder's path as it is really spelled, from a file the rows name in it; the key itself when none says."""
    for table, column in _PATH_SOURCES:
        for row in connection.execute(f"SELECT {column} FROM {table} WHERE folder_key = ? LIMIT 20", (folder_key,)):
            folder = os.path.dirname(str(row[0] or ""))
            if folder and folder_key in (path_key(folder), legacy_path_key(folder)):
                return folder
    return folder_key


def _set_aside_rows_without_a_parent(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS catalog_orphans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            table_name TEXT NOT NULL,
            row_json TEXT NOT NULL,
            set_aside_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    for _ in range(8):                               # setting a row aside can only remove violations; this ends at once
        violations = connection.execute("PRAGMA foreign_key_check").fetchall()
        if not violations:
            return
        for table, rowid in {(str(row[0]), row[1]) for row in violations if row[1] is not None}:
            cursor = connection.execute(f'SELECT * FROM "{table}" WHERE rowid = ?', (rowid,))
            row = cursor.fetchone()
            if row is None:
                continue
            names = [description[0] for description in cursor.description]
            connection.execute(
                "INSERT INTO catalog_orphans(table_name, row_json) VALUES (?, ?)",
                (table, json.dumps(dict(zip(names, tuple(row))), ensure_ascii=True, default=str)),
            )
            connection.execute(f'DELETE FROM "{table}" WHERE rowid = ?', (rowid,))


def _rewrite_folded_folder_keys(connection: sqlite3.Connection) -> None:
    rows = connection.execute("SELECT folder_key, folder_path FROM catalog_folders").fetchall()
    taken = {str(row[0]) for row in rows}
    renames: list[tuple[str, str]] = []
    for row in rows:
        old, folder_path = str(row[0]), str(row[1] or "")
        new = path_key(folder_path)
        if not folder_path or new == old or new in taken or legacy_path_key(folder_path) != old:
            continue
        taken.add(new)
        renames.append((new, old))
    if not renames:
        return
    tables = [
        str(row[0]) for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name LIKE 'catalog_%'")
        if any(str(column[1]) == "folder_key" for column in connection.execute(f'PRAGMA table_info("{row[0]}")'))
    ]
    for table in tables:
        connection.executemany(f'UPDATE "{table}" SET folder_key = ? WHERE folder_key = ?', renames)
