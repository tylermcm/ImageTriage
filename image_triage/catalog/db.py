from __future__ import annotations

import sqlite3
from pathlib import Path

from ..scan_cache import app_data_root

CATALOG_DB_FILENAME = "catalog.sqlite3"


def default_catalog_db_path() -> Path:
    root = app_data_root() / "catalog"
    root.mkdir(parents=True, exist_ok=True)
    return root / CATALOG_DB_FILENAME


def connect_catalog_db(db_path: str | Path | None = None) -> sqlite3.Connection:
    resolved_path = Path(db_path) if db_path is not None else default_catalog_db_path()
    resolved_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(resolved_path))
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA synchronous = NORMAL")
    return connection
