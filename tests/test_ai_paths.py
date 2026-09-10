"""Canonical managed-root selection, migration and storage preflight."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from image_triage import ai_paths


class ManagedRootTests(unittest.TestCase):
    def test_windows_root_ignores_store_virtualized_local_appdata(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store_cache = Path(temp_dir) / (
                "Packages/PythonSoftwareFoundation.Python.3.13_qbz5n2kfra8p0/LocalCache/Local"
            )
            store_cache.mkdir(parents=True)
            profile = Path(temp_dir) / "profile"
            profile.mkdir()
            env = {"LOCALAPPDATA": str(store_cache), "USERPROFILE": str(profile)}
            with patch.dict(os.environ, env, clear=False), patch.object(os, "name", "nt"):
                root = ai_paths.default_managed_ai_root()
                devirtualized = ai_paths.devirtualized_local_appdata()

        self.assertNotIn("LocalCache", str(root))
        self.assertEqual(root, profile / ".image-triage" / "AI")
        self.assertEqual(devirtualized, profile / "AppData" / "Local")

    def test_devirtualization_leaves_an_ordinary_local_appdata_alone(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env = {"LOCALAPPDATA": temp_dir, "USERPROFILE": temp_dir}
            with patch.dict(os.environ, env, clear=False), patch.object(os, "name", "nt"):
                self.assertEqual(ai_paths.devirtualized_local_appdata(), Path(temp_dir))

    def test_environment_override_wins(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.dict(os.environ, {ai_paths.AI_ROOT_ENV: temp_dir}, clear=False):
                self.assertEqual(ai_paths.managed_ai_root(), Path(temp_dir).resolve())
                self.assertEqual(
                    ai_paths.managed_models_root(), Path(temp_dir).resolve() / "models"
                )

    def test_managed_root_is_short_enough_for_deep_package_paths(self) -> None:
        # The Store-virtualized root that caused the field failure was ~120
        # characters before any package path was appended.
        with tempfile.TemporaryDirectory() as temp_dir:
            profile = Path(temp_dir) / "u"
            profile.mkdir()
            with patch.dict(os.environ, {"USERPROFILE": str(profile)}, clear=False), patch.object(
                os, "name", "nt"
            ):
                root = ai_paths.default_managed_ai_root()
        self.assertLessEqual(len(str(root)) - len(str(profile)), 20)

    def test_resolving_a_model_dir_never_moves_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            legacy_root = Path(temp_dir) / "legacy"
            legacy = legacy_root / "image_triage_ai_cache" / "models" / "Thing"
            legacy.mkdir(parents=True)
            (legacy / "weights.bin").write_bytes(b"x")
            managed = Path(temp_dir) / "managed"
            env = {
                ai_paths.AI_ROOT_ENV: str(managed),
                "LOCALAPPDATA": str(legacy_root),
                "USERPROFILE": str(legacy_root),
            }
            with patch.dict(os.environ, env, clear=False):
                resolved = ai_paths.managed_model_dir("Thing")
                self.assertFalse(resolved.exists())
                self.assertTrue((legacy / "weights.bin").exists())


class MigrationTests(unittest.TestCase):
    def test_explicit_migration_moves_a_legacy_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            legacy_root = Path(temp_dir) / "legacy"
            legacy = legacy_root / "image_triage_ai_cache" / "models" / "Editor" / "Thing"
            legacy.mkdir(parents=True)
            (legacy / "weights.bin").write_bytes(b"payload")
            managed = Path(temp_dir) / "managed"
            env = {
                ai_paths.AI_ROOT_ENV: str(managed),
                "LOCALAPPDATA": str(legacy_root),
                "USERPROFILE": str(legacy_root),
            }
            with patch.dict(os.environ, env, clear=False):
                moved = ai_paths.migrate_managed_assets(model_parts=[("Editor", "Thing")])
                target = ai_paths.managed_model_dir("Editor", "Thing")

                self.assertEqual(len(moved), 1)
                self.assertTrue((target / "weights.bin").exists())
                self.assertEqual((target / "weights.bin").read_bytes(), b"payload")
                self.assertFalse(legacy.exists())

    def test_migration_never_overwrites_an_existing_managed_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            legacy_root = Path(temp_dir) / "legacy"
            legacy = legacy_root / "image_triage_ai_cache" / "models" / "Thing"
            legacy.mkdir(parents=True)
            (legacy / "weights.bin").write_bytes(b"old")
            managed = Path(temp_dir) / "managed"
            target = managed / "models" / "Thing"
            target.mkdir(parents=True)
            (target / "weights.bin").write_bytes(b"new")
            env = {
                ai_paths.AI_ROOT_ENV: str(managed),
                "LOCALAPPDATA": str(legacy_root),
                "USERPROFILE": str(legacy_root),
            }
            with patch.dict(os.environ, env, clear=False):
                moved = ai_paths.migrate_managed_assets(model_parts=[("Thing",)])

                self.assertEqual(moved, ())
                self.assertEqual((target / "weights.bin").read_bytes(), b"new")
                self.assertTrue(legacy.exists())

    def test_migration_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            legacy_root = Path(temp_dir) / "legacy"
            legacy = legacy_root / "image_triage_ai_cache" / "models" / "Thing"
            legacy.mkdir(parents=True)
            (legacy / "a.bin").write_bytes(b"a")
            managed = Path(temp_dir) / "managed"
            env = {
                ai_paths.AI_ROOT_ENV: str(managed),
                "LOCALAPPDATA": str(legacy_root),
                "USERPROFILE": str(legacy_root),
            }
            with patch.dict(os.environ, env, clear=False):
                first = ai_paths.migrate_managed_assets(model_parts=[("Thing",)])
                second = ai_paths.migrate_managed_assets(model_parts=[("Thing",)])

        self.assertEqual(len(first), 1)
        self.assertEqual(second, ())


class PreflightTests(unittest.TestCase):
    def test_preflight_reports_a_writable_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            result = ai_paths.preflight_storage(Path(temp_dir) / "nested")

        self.assertTrue(result.ok)
        self.assertTrue(result.writable)
        self.assertTrue(result.exists)
        self.assertEqual(result.describe(), "")

    def test_preflight_reports_insufficient_free_space(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            result = ai_paths.preflight_storage(
                Path(temp_dir), required_bytes=1 << 62
            )

        self.assertFalse(result.ok)
        self.assertFalse(result.has_free_space)
        self.assertIn("free space", result.describe())

    def test_preflight_reports_an_uncreatable_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            blocker = Path(temp_dir) / "blocker"
            blocker.write_bytes(b"not a directory")
            result = ai_paths.preflight_storage(blocker / "child")

        self.assertFalse(result.ok)
        self.assertFalse(result.writable)
        self.assertTrue(result.describe())


class RedactionTests(unittest.TestCase):
    def test_redact_removes_the_user_profile_and_name(self) -> None:
        env = {"USERPROFILE": r"C:\Users\jsmith", "USERNAME": "jsmith"}
        with patch.dict(os.environ, env, clear=False):
            text = ai_paths.redact(r"loading C:\Users\jsmith\.image-triage\AI for jsmith")

        self.assertNotIn("jsmith", text)
        self.assertIn("<user-home>", text)

    def test_redact_handles_forward_slash_paths(self) -> None:
        env = {"USERPROFILE": r"C:\Users\jsmith", "USERNAME": "jsmith"}
        with patch.dict(os.environ, env, clear=False):
            text = ai_paths.redact("C:/Users/jsmith/model.bin")

        self.assertNotIn("jsmith", text)

    def test_redact_leaves_short_usernames_alone_to_avoid_mangling_text(self) -> None:
        env = {"USERPROFILE": r"C:\Users\ab", "USERNAME": "ab"}
        with patch.dict(os.environ, env, clear=False):
            text = ai_paths.redact("unable to load")

        self.assertEqual(text, "unable to load")


class RuntimeTagTests(unittest.TestCase):
    def test_tag_names_python_system_and_architecture(self) -> None:
        tag = ai_paths.runtime_tag()
        self.assertRegex(tag, r"^py\d{2,3}-[a-z_]+-[a-z0-9_]+$")

    def test_architecture_normalizes_x86_64(self) -> None:
        with patch("image_triage.ai_paths.platform.machine", return_value="x86_64"):
            self.assertEqual(ai_paths.process_architecture(), "amd64")

    def test_architecture_normalizes_aarch64(self) -> None:
        with patch("image_triage.ai_paths.platform.machine", return_value="aarch64"):
            self.assertEqual(ai_paths.process_architecture(), "arm64")


if __name__ == "__main__":
    unittest.main()
