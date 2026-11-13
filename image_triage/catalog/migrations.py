from __future__ import annotations

import sqlite3

from .schema import CATALOG_MIGRATIONS, LATEST_CATALOG_SCHEMA_VERSION


class CatalogTooNewError(sqlite3.DatabaseError):
    """The catalog was written by a later version of the app than this one.

    A ``sqlite3.DatabaseError``, so the repository treats such a catalog the way it treats one it cannot open: nothing
    is read from it and nothing is written into it in the old shape."""


def _too_new(version: int) -> CatalogTooNewError:
    return CatalogTooNewError(
        f"The catalog was written by a newer version of Image Triage (format {version}; "
        f"this version reads up to {LATEST_CATALOG_SCHEMA_VERSION}). Update the app to use it."
    )


def apply_catalog_migrations(connection: sqlite3.Connection) -> None:
    """Bring the catalog to this build's schema. ``user_version`` holds the schema version once every migration has
    run, so an up-to-date catalog costs one read here and no write; a newer one is refused (``CatalogTooNewError``)."""
    current_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
    if current_version == LATEST_CATALOG_SCHEMA_VERSION:
        return
    if current_version > LATEST_CATALOG_SCHEMA_VERSION:
        raise _too_new(current_version)
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
    if applied_versions and max(applied_versions) > LATEST_CATALOG_SCHEMA_VERSION:
        raise _too_new(max(applied_versions))
    for version, migration in CATALOG_MIGRATIONS:
        if version in applied_versions:
            continue
        if callable(migration):
            _apply_in_one_transaction(connection, version, migration)
            continue
        with connection:
            connection.executescript(migration)
            connection.execute("INSERT INTO schema_migrations(version) VALUES (?)", (version,))
    connection.execute(f"PRAGMA user_version = {LATEST_CATALOG_SCHEMA_VERSION}")
    connection.commit()


def _apply_in_one_transaction(connection: sqlite3.Connection, version: int, migration) -> None:
    """Run a migration function so that it is applied whole or not at all, and only once when two connections arrive at
    the same time (the second waits for the write lock and then finds the version recorded)."""
    connection.commit()
    connection.execute("BEGIN IMMEDIATE")
    try:
        if connection.execute("SELECT 1 FROM schema_migrations WHERE version = ?", (version,)).fetchone() is None:
            migration(connection)
            connection.execute("INSERT INTO schema_migrations(version) VALUES (?)", (version,))
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
