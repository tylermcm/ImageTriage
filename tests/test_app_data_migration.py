from __future__ import annotations

import contextlib
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PySide6.QtCore import QStandardPaths

from image_triage import app_data_migration
from image_triage.app_data_migration import (
    _copy_file_verified,
    _copy_sqlite_verified,
    _current_app_data_location,
    _legacy_app_data_location,
    _legacy_hand_rolled_root,
    migrate_legacy_app_data_once,
)


def _make_sqlite_db(path: Path, *, rows: tuple[str, ...] = ("a", "b")) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(path))
    try:
        connection.execute("CREATE TABLE items (value TEXT)")
        connection.executemany("INSERT INTO items (value) VALUES (?)", [(row,) for row in rows])
        connection.commit()
    finally:
        connection.close()


def _read_rows(db_path: Path) -> list[tuple[str]]:
    connection = sqlite3.connect(str(db_path))
    try:
        return connection.execute("SELECT value FROM items ORDER BY value").fetchall()
    finally:
        connection.close()


class _FakeCorruptedCursor:
    def fetchone(self):
        return ("simulated corruption",)


class _FakeCorruptedConnection:
    def execute(self, _sql, *_args, **_kwargs):
        return _FakeCorruptedCursor()

    def close(self) -> None:
        pass


@contextlib.contextmanager
def _simulate_a_corrupted_integrity_check():
    """Patches sqlite3.connect so the *second* plain (non-URI) connection to
    any ``*.migrating`` path - i.e. the integrity-check connection
    ``_copy_sqlite_verified`` opens right after the backup API finishes,
    never the backup destination connection itself, which is the first -
    reports ``PRAGMA integrity_check`` as corrupted.

    ``sqlite3.Connection`` is an immutable built-in type in this Python
    version, so its methods can't be monkeypatched directly; patching the
    module-level ``connect`` function is the standard workaround.
    """
    real_connect = sqlite3.connect
    call_counts: dict[str, int] = {}

    def _fake_connect(target, *args, **kwargs):
        if isinstance(target, str) and not kwargs.get("uri") and target.endswith(".migrating"):
            call_counts[target] = call_counts.get(target, 0) + 1
            if call_counts[target] == 2:
                return _FakeCorruptedConnection()
        return real_connect(target, *args, **kwargs)

    with mock.patch.object(sqlite3, "connect", side_effect=_fake_connect):
        yield


class AppDataMigrationPathHelperTests(unittest.TestCase):
    """The path reconstructions, in isolation from any real environment."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="image_triage_wi36_paths_")
        tmp_root = Path(self._tmp.name)
        self._appdata_dir = tmp_root / "roaming"
        self._userprofile_dir = tmp_root / "profile"
        self._appdata_dir.mkdir(parents=True, exist_ok=True)
        self._userprofile_dir.mkdir(parents=True, exist_ok=True)
        self._env_patch = mock.patch.dict(
            os.environ,
            {"APPDATA": str(self._appdata_dir), "USERPROFILE": str(self._userprofile_dir)},
        )
        self._env_patch.start()

    def tearDown(self) -> None:
        self._env_patch.stop()
        self._tmp.cleanup()

    def test_legacy_app_data_location_matches_codex_image_triage(self) -> None:
        self.assertEqual(self._appdata_dir / "Codex" / "Image Triage", _legacy_app_data_location())

    def test_legacy_hand_rolled_root_prefers_userprofile_over_appdata(self) -> None:
        self.assertEqual(
            self._userprofile_dir / "AppData" / "Roaming" / "ImageTriage",
            _legacy_hand_rolled_root(),
        )

    def test_legacy_hand_rolled_root_falls_back_to_appdata_without_userprofile(self) -> None:
        with mock.patch.dict(os.environ, {"USERPROFILE": ""}):
            self.assertEqual(self._appdata_dir / "ImageTriage", _legacy_hand_rolled_root())

    # Note: the Windows formula this reconstructs
    # (%APPDATA%\<Organization>\<Application>) was verified directly against
    # a live, unsandboxed QStandardPaths.writableLocation(AppDataLocation)
    # call (both under plain Python and under the Store-Python package used
    # for day-to-day dev work) before writing this module; it isn't
    # re-verified here because tests/conftest.py sandboxes
    # QStandardPaths.writableLocation to ignore organisation/application
    # name entirely, which would make such a round-trip check tautological.


class AppDataMigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="image_triage_wi36_migration_")
        tmp_root = Path(self._tmp.name)
        self._appdata_dir = tmp_root / "roaming"
        self._userprofile_dir = tmp_root / "profile"
        self._new_root = tmp_root / "new_identity"
        for folder in (self._appdata_dir, self._userprofile_dir, self._new_root):
            folder.mkdir(parents=True, exist_ok=True)

        self._env_patch = mock.patch.dict(
            os.environ,
            {"APPDATA": str(self._appdata_dir), "USERPROFILE": str(self._userprofile_dir)},
        )
        self._env_patch.start()

        real_writable_location = QStandardPaths.writableLocation

        def _fake_writable_location(location):
            if location == QStandardPaths.StandardLocation.AppDataLocation:
                return str(self._new_root)
            return real_writable_location(location)

        self._qsp_patch = mock.patch.object(
            QStandardPaths, "writableLocation", staticmethod(_fake_writable_location)
        )
        self._qsp_patch.start()

    def tearDown(self) -> None:
        self._qsp_patch.stop()
        self._env_patch.stop()
        self._tmp.cleanup()

    def test_copies_everything_when_the_new_location_is_empty(self) -> None:
        legacy_app_data = _legacy_app_data_location()
        legacy_hand_rolled = _legacy_hand_rolled_root()

        _make_sqlite_db(legacy_app_data / "decisions.sqlite3", rows=("ann1", "ann2"))
        (legacy_app_data / "ai_training" / "adapter_labels").mkdir(parents=True)
        (legacy_app_data / "ai_training" / "adapter_labels" / "folder.json").write_text("{}", encoding="utf-8")
        (legacy_app_data / "safe-trash").mkdir(parents=True)
        (legacy_app_data / "safe-trash" / "deleted.jpg").write_bytes(b"pretend-photo-bytes")

        _make_sqlite_db(legacy_hand_rolled / "library.sqlite3", rows=("col1",))
        _make_sqlite_db(legacy_hand_rolled / "catalog" / "catalog.sqlite3", rows=("rec1", "rec2"))
        (legacy_hand_rolled / "catalog" / "catalog.sqlite3-wal").write_bytes(b"stale-wal-bytes")
        (legacy_hand_rolled / "catalog" / "catalog.sqlite3-shm").write_bytes(b"stale-shm-bytes")
        (legacy_hand_rolled / "ai_training" / "label_sources").mkdir(parents=True)
        (legacy_hand_rolled / "ai_training" / "label_sources" / "source.json").write_text("{}", encoding="utf-8")
        (legacy_hand_rolled / "scan-cache").mkdir(parents=True)
        (legacy_hand_rolled / "scan-cache" / "abc123.json").write_text("[]", encoding="utf-8")
        (legacy_hand_rolled / "share_packages" / "pkg1").mkdir(parents=True)
        (legacy_hand_rolled / "share_packages" / "pkg1" / "manifest.json").write_text("{}", encoding="utf-8")
        _make_sqlite_db(legacy_hand_rolled / "share_queue.sqlite3", rows=("q1",))

        result = migrate_legacy_app_data_once()

        self.assertFalse(result.already_migrated)
        self.assertFalse(result.any_failed, msg=result.summary_lines())

        new_root = _current_app_data_location()
        self.assertTrue((new_root / "decisions.sqlite3").exists())
        self.assertEqual([("ann1",), ("ann2",)], _read_rows(new_root / "decisions.sqlite3"))
        self.assertTrue((new_root / "ai_training" / "adapter_labels" / "folder.json").exists())
        self.assertTrue((new_root / "safe-trash" / "deleted.jpg").exists())
        self.assertEqual(
            b"pretend-photo-bytes", (new_root / "safe-trash" / "deleted.jpg").read_bytes()
        )
        self.assertTrue((new_root / "library.sqlite3").exists())
        self.assertTrue((new_root / "catalog" / "catalog.sqlite3").exists())
        self.assertEqual([("rec1",), ("rec2",)], _read_rows(new_root / "catalog" / "catalog.sqlite3"))
        # Stale WAL/SHM sidecars are deliberately not carried over - the
        # backup API already folded their committed content into the main
        # file above.
        self.assertFalse((new_root / "catalog" / "catalog.sqlite3-wal").exists())
        self.assertFalse((new_root / "catalog" / "catalog.sqlite3-shm").exists())
        self.assertTrue((new_root / "ai_training" / "label_sources" / "source.json").exists())
        self.assertTrue((new_root / "scan-cache" / "abc123.json").exists())
        self.assertTrue((new_root / "share_packages" / "pkg1" / "manifest.json").exists())
        self.assertTrue((new_root / "share_queue.sqlite3").exists())
        self.assertTrue((new_root / ".wi36_data_migrated").exists())

    def test_missing_legacy_sources_are_not_an_error(self) -> None:
        # Nothing exists at either legacy location at all (e.g. a brand-new
        # install, or the frozen app on a machine that never had the old
        # identity). Migration should complete cleanly with nothing copied.
        result = migrate_legacy_app_data_once()
        self.assertFalse(result.any_failed)
        self.assertTrue(all(item.status == "skipped_missing_source" for item in result.items))
        self.assertTrue((_current_app_data_location() / ".wi36_data_migrated").exists())

    def test_old_locations_are_never_modified(self) -> None:
        legacy_app_data = _legacy_app_data_location()
        legacy_hand_rolled = _legacy_hand_rolled_root()
        db_path = legacy_app_data / "decisions.sqlite3"
        _make_sqlite_db(db_path, rows=("only-row",))
        plain_path = legacy_hand_rolled / "scan-cache" / "abc.json"
        plain_path.parent.mkdir(parents=True, exist_ok=True)
        plain_path.write_text("[1,2,3]", encoding="utf-8")

        before_db_bytes = db_path.read_bytes()
        before_plain_bytes = plain_path.read_bytes()
        before_db_mtime = db_path.stat().st_mtime_ns
        before_plain_mtime = plain_path.stat().st_mtime_ns

        migrate_legacy_app_data_once()

        self.assertEqual(before_db_bytes, db_path.read_bytes())
        self.assertEqual(before_plain_bytes, plain_path.read_bytes())
        self.assertEqual(before_db_mtime, db_path.stat().st_mtime_ns)
        self.assertEqual(before_plain_mtime, plain_path.stat().st_mtime_ns)

    def test_second_call_is_a_no_op_and_ignores_legacy_changes_made_afterward(self) -> None:
        legacy_app_data = _legacy_app_data_location()
        _make_sqlite_db(legacy_app_data / "decisions.sqlite3")

        first = migrate_legacy_app_data_once()
        self.assertFalse(first.already_migrated)

        (legacy_app_data / "safe-trash").mkdir(parents=True)
        (legacy_app_data / "safe-trash" / "later.jpg").write_bytes(b"added-after-migration")

        second = migrate_legacy_app_data_once()
        self.assertTrue(second.already_migrated)
        self.assertEqual((), second.items)

        new_root = _current_app_data_location()
        self.assertFalse((new_root / "safe-trash" / "later.jpg").exists())

    def test_never_overwrites_data_already_present_at_the_new_location(self) -> None:
        legacy_app_data = _legacy_app_data_location()
        _make_sqlite_db(legacy_app_data / "decisions.sqlite3", rows=("legacy-row",))

        new_root = _current_app_data_location()
        new_root.mkdir(parents=True, exist_ok=True)
        _make_sqlite_db(new_root / "decisions.sqlite3", rows=("current-row",))

        result = migrate_legacy_app_data_once()
        self.assertFalse(result.any_failed)

        item = next(item for item in result.items if item.label == "decisions.sqlite3")
        self.assertEqual("skipped_exists", item.status)
        self.assertEqual([("current-row",)], _read_rows(new_root / "decisions.sqlite3"))

    def test_a_failed_verification_blocks_the_marker_so_a_later_launch_retries(self) -> None:
        legacy_app_data = _legacy_app_data_location()
        _make_sqlite_db(legacy_app_data / "decisions.sqlite3", rows=("row",))

        with _simulate_a_corrupted_integrity_check():
            result = migrate_legacy_app_data_once()

        self.assertTrue(result.any_failed)
        new_root = _current_app_data_location()
        self.assertFalse((new_root / ".wi36_data_migrated").exists())
        self.assertFalse((new_root / "decisions.sqlite3").exists())

        # A later launch (integrity check behaving normally again) retries
        # and succeeds.
        retry = migrate_legacy_app_data_once()
        self.assertFalse(retry.any_failed)
        self.assertTrue((new_root / "decisions.sqlite3").exists())
        self.assertTrue((new_root / ".wi36_data_migrated").exists())


class CorruptedCopyDetectionTests(unittest.TestCase):
    """Direct tests of the two low-level verified-copy helpers."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="image_triage_wi36_corrupt_")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_sqlite_copy_with_a_bad_integrity_check_is_rejected(self) -> None:
        temp_dir = Path(self._tmp.name)
        source = temp_dir / "source.sqlite3"
        dest = temp_dir / "dest.sqlite3"
        _make_sqlite_db(source, rows=("row1",))

        with _simulate_a_corrupted_integrity_check():
            status, detail = _copy_sqlite_verified(source, dest)

        self.assertEqual("verify_failed", status)
        self.assertIn("simulated corruption", detail)
        self.assertFalse(dest.exists())
        self.assertFalse(dest.with_name(dest.name + ".migrating").exists())

    def test_plain_file_copy_with_a_hash_mismatch_is_rejected(self) -> None:
        temp_dir = Path(self._tmp.name)
        source = temp_dir / "source.json"
        dest = temp_dir / "dest.json"
        source.write_text('{"ok": true}', encoding="utf-8")

        real_hash_file = app_data_migration._hash_file
        call_count = {"n": 0}

        def _fake_hash_file(path):
            call_count["n"] += 1
            if call_count["n"] == 2:
                return "deliberately-wrong-hash"
            return real_hash_file(path)

        with mock.patch.object(app_data_migration, "_hash_file", _fake_hash_file):
            status, detail = _copy_file_verified(source, dest)

        self.assertEqual("verify_failed", status)
        self.assertIn("mismatch", detail)
        self.assertFalse(dest.exists())
        self.assertFalse(dest.with_name(dest.name + ".migrating").exists())

    def test_plain_file_copy_succeeds_and_matches_byte_for_byte(self) -> None:
        temp_dir = Path(self._tmp.name)
        source = temp_dir / "source.json"
        dest = temp_dir / "dest.json"
        source.write_text('{"ok": true}', encoding="utf-8")

        status, _detail = _copy_file_verified(source, dest)

        self.assertEqual("copied", status)
        self.assertEqual(source.read_bytes(), dest.read_bytes())


if __name__ == "__main__":
    unittest.main()
