from __future__ import annotations

CATALOG_MIGRATIONS: tuple[tuple[int, str], ...] = (
    (
        1,
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS catalog_folders (
            folder_key TEXT PRIMARY KEY,
            folder_path TEXT NOT NULL,
            record_count INTEGER NOT NULL DEFAULT 0,
            source TEXT NOT NULL DEFAULT 'scan',
            last_indexed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS catalog_records (
            folder_key TEXT NOT NULL,
            record_path TEXT NOT NULL,
            name TEXT NOT NULL,
            size INTEGER NOT NULL,
            modified_ns INTEGER NOT NULL,
            position INTEGER NOT NULL,
            PRIMARY KEY (folder_key, record_path),
            FOREIGN KEY (folder_key) REFERENCES catalog_folders(folder_key) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS catalog_record_members (
            folder_key TEXT NOT NULL,
            record_path TEXT NOT NULL,
            member_role TEXT NOT NULL,
            member_path TEXT NOT NULL,
            member_name TEXT NOT NULL DEFAULT '',
            member_size INTEGER NOT NULL DEFAULT 0,
            member_modified_ns INTEGER NOT NULL DEFAULT 0,
            position INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (folder_key, record_path, member_role, member_path),
            FOREIGN KEY (folder_key, record_path) REFERENCES catalog_records(folder_key, record_path) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_catalog_records_folder_position
        ON catalog_records(folder_key, position);

        CREATE INDEX IF NOT EXISTS idx_catalog_record_members_folder_record_position
        ON catalog_record_members(folder_key, record_path, member_role, position);
        """,
    ),
)

LATEST_CATALOG_SCHEMA_VERSION = CATALOG_MIGRATIONS[-1][0]
