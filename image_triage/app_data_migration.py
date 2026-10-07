"""One-time migration of persistent app data off the pre-rename path
identities (WI-3.6; the ``QStandardPaths`` half of D10 - WI-3.1 already did
the ``QSettings``/registry half in :mod:`image_triage.app_identity`).

Before this work item, Image Triage's on-disk user data lived under three
different identities:

1. ``QStandardPaths`` with ``QCoreApplication``'s organisation name set to
   "Codex" (``decisions.sqlite3``, per-folder adapter-label JSON/CSV caches
   under ``ai_training``, and the "safe trash" holding area for files the app
   deleted but hasn't purged yet).
2. A hand-rolled ``%APPDATA%\\ImageTriage`` folder, built independently by
   :func:`image_triage.aiculler_global_store._default_user_data_root` and
   :func:`image_triage.scan_cache.app_data_root` (``library.sqlite3``, the
   catalog cache, the global adapter-label store and training workspace, plus
   some older ``scan-cache``/``share_packages``/``share_queue.sqlite3`` data).
3. The *new*, current identity: ``QStandardPaths`` with the organisation name
   set to "Image Triage" (see ``main.py``), which both of the above now also
   resolve through (``scan_cache.app_data_root`` was fixed alongside this
   module; see its docstring).

:func:`migrate_legacy_app_data_once` copies the irreplaceable and
expensive-to-rebuild items from (1) and (2) into (3), the first time it's
called after the rename ships. It is deliberately conservative:

- **Copy only.** Nothing is ever deleted or modified at the old locations -
  they are left in place indefinitely, serving as an implicit backup.
- **Never overwrites.** Every file is skipped if something already exists at
  its destination path, so a second call (or a call that resumes after a
  partial failure) is always safe to repeat.
- **Verified, not just attempted.** Every SQLite database is copied with
  SQLite's own online-backup API (reading the source read-only) and then
  checked with ``PRAGMA integrity_check``; every other file is copied and
  then compared to its source with a SHA-256 hash. A copy is never left
  visible at its destination path unless it already passed that check (it is
  written to a sibling ``*.migrating`` path first, then renamed into place).
- **A marker gates repeat work**, the same shape as
  ``app_identity.migrate_legacy_settings_once``: once a pass completes with
  no verification failures, a small JSON marker file is written at the new
  root, and every later call returns immediately without touching the old
  locations at all. A pass that hits a verification failure does *not* write
  the marker, so the next launch retries - cheaply, since every
  already-copied file is skipped on the retry.

The thumbnail cache (``QStandardPaths.CacheLocation``) is deliberately never
touched here: it is classified cache-safe-to-lose (WI-3.5) and can be several
gigabytes, so it is cheaper and safer to let it rebuild fresh under the new
identity than to copy it.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import sqlite3
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QStandardPaths

_logger = logging.getLogger(__name__)

_MIGRATION_MARKER_NAME = ".wi36_data_migrated"

# SQLite journal/WAL/shared-memory sidecars. Their committed content is
# already captured into the main database file by the backup API below, so
# copying them verbatim would be meaningless (or, if stale, actively
# misleading) at the destination - they are deliberately skipped.
_SQLITE_SIDECAR_SUFFIXES = ("-wal", "-shm", "-journal")
_SQLITE_FILE_SUFFIXES = {".sqlite", ".sqlite3", ".db"}


@dataclass(frozen=True, slots=True)
class MigratedItem:
    """The outcome of migrating one file."""

    label: str
    source: Path
    destination: Path
    status: str  # "copied", "skipped_exists", "skipped_missing_source", "verify_failed"
    detail: str = ""


@dataclass(frozen=True, slots=True)
class MigrationResult:
    items: tuple[MigratedItem, ...] = field(default_factory=tuple)
    already_migrated: bool = False

    @property
    def any_failed(self) -> bool:
        return any(item.status == "verify_failed" for item in self.items)

    def summary_lines(self) -> list[str]:
        if self.already_migrated:
            return ["WI-3.6 data migration: already done (marker present); nothing touched."]
        lines = [f"WI-3.6 data migration: {len(self.items)} file(s) considered."]
        for item in self.items:
            lines.append(f"  [{item.status}] {item.label}: {item.source} -> {item.destination} ({item.detail})")
        return lines


def _current_app_data_location() -> Path:
    """The current (post-rename) ``QStandardPaths.AppDataLocation``."""
    base = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation)
    return Path(base) if base else Path.home() / ".image-triage"


def _legacy_app_data_location() -> Path:
    """What ``QStandardPaths.AppDataLocation`` resolved to under the
    pre-rename organisation name ("Codex"), reconstructed from the
    environment rather than by flipping ``QCoreApplication``'s global
    organisation/application name and back.

    Mutating that global state here would be fragile: other code
    (``decision_store.py``, ``cache.py``, ``recycle_bin_controller.py``) reads
    it too, and a migration function is exactly the kind of place a slip
    (an exception between the temporary set and the restore) would leave the
    whole rest of the app's lifetime pointed at the wrong identity. Qt's own
    Windows formula for ``AppDataLocation`` is simple and documented:
    ``%APPDATA%\\<Organization>\\<Application>``. Reconstructing it directly
    from ``APPDATA`` is both simpler and safer than the flip, and is exactly
    the "or by explicitly constructing the old identity's paths the same way
    QStandardPaths would" alternative.
    """
    appdata = os.environ.get("APPDATA", "").strip()
    base = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
    return base / "Codex" / "Image Triage"


def _legacy_hand_rolled_root() -> Path:
    """Where ``aiculler_global_store._default_user_data_root`` /
    ``scan_cache.app_data_root`` used to hand-roll their shared data root,
    before this work item switched both to ``QStandardPaths`` (see
    ``scan_cache.py``). Mirrors the old ``_default_user_data_root`` exactly
    (``USERPROFILE`` first, then ``APPDATA``, then ``~/.image-triage``) so
    the migration finds real legacy data wherever it actually is.
    """
    userprofile = os.environ.get("USERPROFILE", "").strip()
    if userprofile:
        return Path(userprofile) / "AppData" / "Roaming" / "ImageTriage"
    appdata = os.environ.get("APPDATA", "").strip()
    if appdata:
        return Path(appdata) / "ImageTriage"
    return Path.home() / ".image-triage"


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _cleanup(path: Path) -> None:
    try:
        if path.exists():
            path.unlink()
    except OSError:
        pass


def _copy_sqlite_verified(source: Path, dest: Path) -> tuple[str, str]:
    """Copy one SQLite database with the online-backup API (read-only on the
    source) and verify the copy with ``PRAGMA integrity_check`` before it
    becomes visible at ``dest``."""
    if dest.exists():
        return "skipped_exists", f"destination already exists: {dest}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp_dest = dest.with_name(dest.name + ".migrating")
    _cleanup(tmp_dest)
    try:
        source_conn = sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True)
        try:
            dest_conn = sqlite3.connect(str(tmp_dest))
            try:
                source_conn.backup(dest_conn)
            finally:
                dest_conn.close()
        finally:
            source_conn.close()
    except Exception as exc:  # noqa: BLE001 - surfaced via status/detail, not raised
        _cleanup(tmp_dest)
        return "verify_failed", f"backup raised {exc!r}"

    try:
        check_conn = sqlite3.connect(str(tmp_dest))
        try:
            (result,) = check_conn.execute("PRAGMA integrity_check").fetchone()
        finally:
            check_conn.close()
    except Exception as exc:  # noqa: BLE001
        _cleanup(tmp_dest)
        return "verify_failed", f"integrity_check raised {exc!r}"

    if str(result).strip().lower() != "ok":
        _cleanup(tmp_dest)
        return "verify_failed", f"integrity_check returned {result!r}"

    tmp_dest.rename(dest)
    return "copied", "integrity_check=ok"


def _copy_file_verified(source: Path, dest: Path) -> tuple[str, str]:
    """Copy one non-database file and verify it with a SHA-256 comparison
    before it becomes visible at ``dest``."""
    if dest.exists():
        return "skipped_exists", f"destination already exists: {dest}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp_dest = dest.with_name(dest.name + ".migrating")
    _cleanup(tmp_dest)
    try:
        shutil.copy2(source, tmp_dest)
    except OSError as exc:
        _cleanup(tmp_dest)
        return "verify_failed", f"copy raised {exc!r}"

    try:
        source_hash = _hash_file(source)
        dest_hash = _hash_file(tmp_dest)
    except OSError as exc:
        _cleanup(tmp_dest)
        return "verify_failed", f"hashing raised {exc!r}"

    if source_hash != dest_hash:
        _cleanup(tmp_dest)
        return "verify_failed", f"sha256 mismatch: source={source_hash} dest={dest_hash}"

    tmp_dest.rename(dest)
    return "copied", f"sha256={dest_hash}"


def _copy_tree_verified(source_dir: Path, dest_dir: Path, label: str, results: list[MigratedItem]) -> None:
    for dirpath, _dirnames, filenames in os.walk(source_dir):
        rel_dir = Path(dirpath).relative_to(source_dir)
        for filename in filenames:
            if filename.endswith(_SQLITE_SIDECAR_SUFFIXES):
                continue
            source_file = Path(dirpath) / filename
            rel_file = filename if str(rel_dir) == "." else str(rel_dir / filename)
            dest_file = dest_dir / rel_file
            item_label = f"{label}/{rel_file}"
            if Path(filename).suffix.lower() in _SQLITE_FILE_SUFFIXES:
                status, detail = _copy_sqlite_verified(source_file, dest_file)
            else:
                status, detail = _copy_file_verified(source_file, dest_file)
            results.append(MigratedItem(item_label, source_file, dest_file, status, detail))


def _migration_plan(new_root: Path) -> list[tuple[str, Path, Path, str]]:
    legacy_app_data = _legacy_app_data_location()
    legacy_hand_rolled = _legacy_hand_rolled_root()
    return [
        ("decisions.sqlite3", legacy_app_data / "decisions.sqlite3", new_root / "decisions.sqlite3", "sqlite"),
        (
            "ai_training (per-folder adapter labels)",
            legacy_app_data / "ai_training",
            new_root / "ai_training",
            "tree",
        ),
        ("safe-trash (pending restores)", legacy_app_data / "safe-trash", new_root / "safe-trash", "tree"),
        ("library.sqlite3", legacy_hand_rolled / "library.sqlite3", new_root / "library.sqlite3", "sqlite"),
        ("catalog", legacy_hand_rolled / "catalog", new_root / "catalog", "tree"),
        (
            "ai_training (global adapter workspace)",
            legacy_hand_rolled / "ai_training",
            new_root / "ai_training",
            "tree",
        ),
        ("scan-cache", legacy_hand_rolled / "scan-cache", new_root / "scan-cache", "tree"),
        ("share_packages", legacy_hand_rolled / "share_packages", new_root / "share_packages", "tree"),
        ("share_queue.sqlite3", legacy_hand_rolled / "share_queue.sqlite3", new_root / "share_queue.sqlite3", "sqlite"),
    ]


def migrate_legacy_app_data_once() -> MigrationResult:
    """Copy legacy "Codex"/hand-rolled app data into the current identity.

    Safe to call on every startup: a marker file under the new root makes it
    a no-op after the first successful pass, and nothing already present at a
    destination path is ever overwritten. The old locations are never
    written to or deleted. Windows-only (every legacy identity this migrates
    away from is itself Windows-specific); a no-op elsewhere.
    """
    if sys.platform != "win32":
        return MigrationResult(items=(), already_migrated=False)

    new_root = _current_app_data_location()
    marker = new_root / _MIGRATION_MARKER_NAME
    if marker.exists():
        return MigrationResult(items=(), already_migrated=True)

    results: list[MigratedItem] = []
    for label, source, dest, kind in _migration_plan(new_root):
        if not source.exists():
            results.append(MigratedItem(label, source, dest, "skipped_missing_source", "legacy source not present"))
            continue
        if kind == "sqlite":
            status, detail = _copy_sqlite_verified(source, dest)
            results.append(MigratedItem(label, source, dest, status, detail))
            continue
        if not source.is_dir():
            results.append(
                MigratedItem(label, source, dest, "verify_failed", f"expected a directory, found: {source}")
            )
            continue
        if dest.exists() and not dest.is_dir():
            results.append(
                MigratedItem(label, source, dest, "verify_failed", f"destination exists and is not a directory: {dest}")
            )
            continue
        dest.mkdir(parents=True, exist_ok=True)
        _copy_tree_verified(source, dest, label, results)

    result = MigrationResult(items=tuple(results))
    if result.any_failed:
        for item in result.items:
            if item.status == "verify_failed":
                _logger.error(
                    "WI-3.6 data migration: failed to migrate %s (%s -> %s): %s",
                    item.label,
                    item.source,
                    item.destination,
                    item.detail,
                )
        return result

    new_root.mkdir(parents=True, exist_ok=True)
    marker.write_text(
        json.dumps({"migrated_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "item_count": len(results)}),
        encoding="utf-8",
    )
    return result
