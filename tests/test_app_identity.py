from __future__ import annotations

import unittest

from PySide6.QtCore import QSettings

from image_triage.app_identity import (
    legacy_settings_sources,
    migrate_legacy_settings_once,
    user_settings,
)


class AppIdentityTests(unittest.TestCase):
    def setUp(self) -> None:
        user_settings().clear()
        for legacy in legacy_settings_sources():
            legacy.clear()

    def tearDown(self) -> None:
        user_settings().clear()
        for legacy in legacy_settings_sources():
            legacy.clear()

    def test_user_settings_is_hermetic_ini_not_the_real_registry(self) -> None:
        settings = user_settings()
        self.assertEqual(QSettings.Format.IniFormat, settings.format())

    def test_user_settings_round_trips_and_is_stable_across_calls(self) -> None:
        first = user_settings()
        first.setValue("probe/key", "value")
        first.sync()

        second = user_settings()
        self.assertEqual("value", second.value("probe/key"))

    def test_migration_copies_legacy_keys_without_a_marker(self) -> None:
        legacy = legacy_settings_sources()[0]
        legacy.setValue("legacy_only_key", "legacy_value")
        legacy.sync()

        migrate_legacy_settings_once()

        target = user_settings()
        self.assertEqual("legacy_value", target.value("legacy_only_key"))
        self.assertTrue(target.value("_settings_migrated_from_legacy", False, bool))

    def test_migration_never_overwrites_a_value_already_set_in_the_current_store(self) -> None:
        target = user_settings()
        target.setValue("shared_key", "current_value")
        target.sync()

        legacy = legacy_settings_sources()[0]
        legacy.setValue("shared_key", "legacy_value")
        legacy.sync()

        migrate_legacy_settings_once()

        self.assertEqual("current_value", user_settings().value("shared_key"))

    def test_migration_is_a_no_op_after_the_first_run(self) -> None:
        migrate_legacy_settings_once()
        target = user_settings()
        target.setValue("_settings_migrated_from_legacy", True)

        legacy = legacy_settings_sources()[0]
        legacy.setValue("added_after_migration", "should_not_appear")
        legacy.sync()

        migrate_legacy_settings_once()

        self.assertIsNone(user_settings().value("added_after_migration"))


if __name__ == "__main__":
    unittest.main()
