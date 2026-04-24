from __future__ import annotations

import sqlite3

from .schema import CATALOG_MIGRATIONS


def apply_catalog_migrations(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    applied_versions = {
        int(row[0])
        for row in connection.execute("SELECT version FROM schema_migrations ORDER BY version").fetchall()
    }
    for version, migration_sql in CATALOG_MIGRATIONS:
        if version in applied_versions:
            continue
        with connection:
            connection.executescript(migration_sql)
            connection.execute("INSERT INTO schema_migrations(version) VALUES (?)", (version,))
