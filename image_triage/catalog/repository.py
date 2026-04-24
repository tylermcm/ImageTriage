from __future__ import annotations

import os
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from ..models import ImageRecord, ImageVariant
from .db import connect_catalog_db, default_catalog_db_path
from .migrations import apply_catalog_migrations


@dataclass(slots=True, frozen=True)
class CatalogStats:
    db_path: Path
    folder_count: int = 0
    record_count: int = 0
    last_indexed_at: str = ""
    available: bool = False
    error_message: str = ""


def catalog_cache_env_override() -> bool | None:
    value = os.environ.get("IMAGE_TRIAGE_USE_CATALOG_CACHE", "").strip().casefold()
    if not value:
        return None
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    return None


def catalog_cache_enabled(default: bool = True) -> bool:
    override = catalog_cache_env_override()
    return default if override is None else override


class CatalogRepository:
    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_path = Path(db_path) if db_path is not None else default_catalog_db_path()

    def stats(self) -> CatalogStats:
        if not self.db_path.exists():
            return CatalogStats(db_path=self.db_path)
        try:
            with closing(connect_catalog_db(self.db_path)) as connection:
                apply_catalog_migrations(connection)
                row = connection.execute(
                    """
                    SELECT
                        COUNT(*),
                        COALESCE(SUM(record_count), 0),
                        COALESCE(MAX(last_indexed_at), '')
                    FROM catalog_folders
                    """
                ).fetchone()
        except sqlite3.DatabaseError as exc:
            return CatalogStats(
                db_path=self.db_path,
                error_message=str(exc),
            )
        folder_count = int(row[0]) if row is not None else 0
        record_count = int(row[1]) if row is not None else 0
        last_indexed_at = str(row[2] or "") if row is not None else ""
        return CatalogStats(
            db_path=self.db_path,
            folder_count=folder_count,
            record_count=record_count,
            last_indexed_at=last_indexed_at,
            available=True,
        )

    def load_folder_records(self, folder: str) -> list[ImageRecord] | None:
        folder_path = _normalize_filesystem_path(folder)
        folder_key = _normalized_path_key(folder_path)
        try:
            with closing(connect_catalog_db(self.db_path)) as connection:
                apply_catalog_migrations(connection)
                folder_row = connection.execute(
                    "SELECT folder_path FROM catalog_folders WHERE folder_key = ?",
                    (folder_key,),
                ).fetchone()
                if folder_row is None:
                    return None
                record_rows = connection.execute(
                    """
                    SELECT record_path, name, size, modified_ns
                    FROM catalog_records
                    WHERE folder_key = ?
                    ORDER BY position
                    """,
                    (folder_key,),
                ).fetchall()
                member_rows = connection.execute(
                    """
                    SELECT record_path, member_role, member_path, member_name, member_size, member_modified_ns
                    FROM catalog_record_members
                    WHERE folder_key = ?
                    ORDER BY record_path, member_role, position
                    """,
                    (folder_key,),
                ).fetchall()
        except sqlite3.DatabaseError:
            return None

        member_paths: dict[tuple[str, str], list[sqlite3.Row]] = {}
        for row in member_rows:
            member_paths.setdefault((str(row["record_path"]), str(row["member_role"])), []).append(row)

        records: list[ImageRecord] = []
        for row in record_rows:
            record_path = str(row["record_path"])
            companion_rows = member_paths.get((record_path, "companion"), [])
            edit_rows = member_paths.get((record_path, "edit"), [])
            variant_rows = member_paths.get((record_path, "variant"), [])
            records.append(
                ImageRecord(
                    path=record_path,
                    name=str(row["name"]),
                    size=int(row["size"]),
                    modified_ns=int(row["modified_ns"]),
                    companion_paths=tuple(str(member["member_path"]) for member in companion_rows),
                    edited_paths=tuple(str(member["member_path"]) for member in edit_rows),
                    variants=tuple(
                        ImageVariant(
                            path=str(member["member_path"]),
                            name=str(member["member_name"]),
                            size=int(member["member_size"]),
                            modified_ns=int(member["member_modified_ns"]),
                        )
                        for member in variant_rows
                    ),
                )
            )
        return records

    def save_folder_records(
        self,
        folder: str,
        records: list[ImageRecord],
        *,
        source: str = "scan",
    ) -> bool:
        folder_path = _normalize_filesystem_path(folder)
        folder_key = _normalized_path_key(folder_path)
        try:
            with closing(connect_catalog_db(self.db_path)) as connection:
                apply_catalog_migrations(connection)
                with connection:
                    connection.execute(
                        """
                        INSERT INTO catalog_folders(folder_key, folder_path, record_count, source, last_indexed_at)
                        VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                        ON CONFLICT(folder_key) DO UPDATE SET
                            folder_path = excluded.folder_path,
                            record_count = excluded.record_count,
                            source = excluded.source,
                            last_indexed_at = CURRENT_TIMESTAMP
                        """,
                        (folder_key, folder_path, len(records), source),
                    )
                    connection.execute("DELETE FROM catalog_record_members WHERE folder_key = ?", (folder_key,))
                    connection.execute("DELETE FROM catalog_records WHERE folder_key = ?", (folder_key,))
                    for position, record in enumerate(records):
                        connection.execute(
                            """
                            INSERT INTO catalog_records(folder_key, record_path, name, size, modified_ns, position)
                            VALUES (?, ?, ?, ?, ?, ?)
                            """,
                            (
                                folder_key,
                                record.path,
                                record.name,
                                int(record.size),
                                int(record.modified_ns),
                                position,
                            ),
                        )
                        self._insert_path_members(connection, folder_key, record.path, "companion", record.companion_paths)
                        self._insert_path_members(connection, folder_key, record.path, "edit", record.edited_paths)
                        self._insert_variants(connection, folder_key, record.path, record.display_variants if record.variants else ())
        except sqlite3.DatabaseError:
            return False
        return True

    @staticmethod
    def _insert_path_members(
        connection: sqlite3.Connection,
        folder_key: str,
        record_path: str,
        role: str,
        paths: tuple[str, ...],
    ) -> None:
        for position, member_path in enumerate(paths):
            connection.execute(
                """
                INSERT INTO catalog_record_members(
                    folder_key,
                    record_path,
                    member_role,
                    member_path,
                    member_name,
                    member_size,
                    member_modified_ns,
                    position
                )
                VALUES (?, ?, ?, ?, ?, 0, 0, ?)
                """,
                (
                    folder_key,
                    record_path,
                    role,
                    member_path,
                    Path(member_path).name,
                    position,
                ),
            )

    @staticmethod
    def _insert_variants(
        connection: sqlite3.Connection,
        folder_key: str,
        record_path: str,
        variants: tuple[ImageVariant, ...],
    ) -> None:
        for position, variant in enumerate(variants):
            connection.execute(
                """
                INSERT INTO catalog_record_members(
                    folder_key,
                    record_path,
                    member_role,
                    member_path,
                    member_name,
                    member_size,
                    member_modified_ns,
                    position
                )
                VALUES (?, ?, 'variant', ?, ?, ?, ?, ?)
                """,
                (
                    folder_key,
                    record_path,
                    variant.path,
                    variant.name,
                    int(variant.size),
                    int(variant.modified_ns),
                    position,
                ),
            )


def _normalize_filesystem_path(path: str | Path) -> str:
    raw = str(path).strip()
    if not raw:
        return ""
    candidate = Path(raw).expanduser()
    try:
        candidate = candidate.resolve(strict=False)
    except OSError:
        candidate = candidate.absolute()
    return os.path.normpath(str(candidate))


def _normalized_path_key(path: str | Path) -> str:
    return _normalize_filesystem_path(path).casefold()
