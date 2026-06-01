from __future__ import annotations

import os
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path


ALLOWED_ADAPTER_LABELS = frozenset(
    {
        "hero",
        "portfolio",
        "strong",
        "keep",
        "good",
        "maybe",
        "weak",
        "reject",
        "bad",
        "k",
        "r",
        "yes",
        "no",
        "1",
        "0",
    }
)


@dataclass(frozen=True, slots=True)
class GlobalAdapterLabel:
    source_path: str
    label: str
    filename: str
    folder: str
    weight: float = 1.0
    is_dispute: bool = False


@dataclass(frozen=True, slots=True)
class GlobalAdapterLabelStats:
    total_count: int = 0
    dispute_count: int = 0
    weighted_count: float = 0.0


class GlobalAdapterLabelStore:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.db_path)
        self.connection.row_factory = sqlite3.Row
        self._ensure_schema()

    def close(self) -> None:
        self.connection.close()

    def _ensure_schema(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS adapter_labels (
                source_path TEXT PRIMARY KEY,
                path_key TEXT NOT NULL,
                filename TEXT NOT NULL,
                folder TEXT NOT NULL,
                label TEXT NOT NULL,
                weight REAL NOT NULL DEFAULT 1.0,
                is_dispute INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_adapter_labels_filename
              ON adapter_labels(filename);
            CREATE INDEX IF NOT EXISTS idx_adapter_labels_folder
              ON adapter_labels(folder);
            """
        )
        self.connection.commit()

    def upsert_label(
        self,
        source_path: str | Path,
        label: str,
        *,
        folder: str | Path = "",
        weight: float = 1.0,
        is_dispute: bool = False,
    ) -> None:
        normalized_label = label.strip().lower()
        if normalized_label not in ALLOWED_ADAPTER_LABELS:
            return
        source_text = str(source_path)
        filename = Path(source_text).name
        folder_text = str(folder or Path(source_text).parent)
        self.connection.execute(
            """
            INSERT INTO adapter_labels (
                source_path, path_key, filename, folder, label, weight, is_dispute, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_path) DO UPDATE SET
                path_key = excluded.path_key,
                filename = excluded.filename,
                folder = excluded.folder,
                label = excluded.label,
                weight = excluded.weight,
                is_dispute = excluded.is_dispute,
                updated_at = excluded.updated_at
            """,
            (
                source_text,
                _path_key(source_text),
                filename,
                folder_text,
                normalized_label,
                max(0.05, float(weight)),
                1 if is_dispute else 0,
                time.strftime("%Y-%m-%dT%H:%M:%S"),
            ),
        )
        self.connection.commit()

    def delete_label(self, source_path: str | Path) -> None:
        self.connection.execute(
            "DELETE FROM adapter_labels WHERE source_path = ? OR path_key = ?",
            (str(source_path), _path_key(source_path)),
        )
        self.connection.commit()

    def labels_for_paths(self, paths: list[str] | tuple[str, ...]) -> dict[str, GlobalAdapterLabel]:
        if not paths:
            return {}
        by_key = {_path_key(path): str(path) for path in paths}
        placeholders = ",".join("?" for _ in by_key)
        rows = self.connection.execute(
            f"""
            SELECT source_path, filename, folder, label, weight, is_dispute, path_key
            FROM adapter_labels
            WHERE path_key IN ({placeholders})
            """,
            tuple(by_key),
        ).fetchall()
        result: dict[str, GlobalAdapterLabel] = {}
        for row in rows:
            original_path = by_key.get(str(row["path_key"]), str(row["source_path"]))
            result[original_path] = GlobalAdapterLabel(
                source_path=original_path,
                label=str(row["label"]),
                filename=str(row["filename"]),
                folder=str(row["folder"]),
                weight=float(row["weight"] or 1.0),
                is_dispute=bool(row["is_dispute"]),
            )
        return result

    def summary(self) -> GlobalAdapterLabelStats:
        row = self.connection.execute(
            """
            SELECT
                COUNT(*) AS total_count,
                COALESCE(SUM(CASE WHEN is_dispute THEN 1 ELSE 0 END), 0) AS dispute_count,
                COALESCE(SUM(weight), 0) AS weighted_count
            FROM adapter_labels
            """
        ).fetchone()
        if row is None:
            return GlobalAdapterLabelStats()
        return GlobalAdapterLabelStats(
            total_count=int(row["total_count"] or 0),
            dispute_count=int(row["dispute_count"] or 0),
            weighted_count=float(row["weighted_count"] or 0.0),
        )

    def summary_for_paths(self, paths: list[str] | tuple[str, ...]) -> GlobalAdapterLabelStats:
        labels = self.labels_for_paths(paths)
        if not labels:
            return GlobalAdapterLabelStats()
        values = tuple(labels.values())
        return GlobalAdapterLabelStats(
            total_count=len(values),
            dispute_count=sum(1 for label in values if label.is_dispute),
            weighted_count=sum(float(label.weight or 0.0) for label in values),
        )


def default_global_adapter_label_store_path() -> Path:
    return _default_user_data_root() / "ai_training" / "global_adapter_labels.sqlite"


def _default_user_data_root() -> Path:
    if os.name == "nt":
        appdata = os.environ.get("APPDATA", "").strip()
        if appdata:
            return Path(appdata) / "ImageTriage"
        userprofile = os.environ.get("USERPROFILE", "").strip()
        if userprofile:
            return Path(userprofile) / "AppData" / "Roaming" / "ImageTriage"
    try:
        return Path.home() / ".image-triage"
    except RuntimeError:
        return Path.cwd() / ".image-triage"


def _path_key(path: str | Path) -> str:
    return os.path.normpath(str(path)).casefold()
