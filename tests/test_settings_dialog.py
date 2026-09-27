from __future__ import annotations

import unittest

from PySide6.QtWidgets import QApplication, QFrame, QLabel

from image_triage.models import DeleteMode, WinnerMode
from image_triage.phash_prefilter import PHashPrefilterSettings
from image_triage.settings_dialog import WorkflowSettingsDialog, _settings_tooltip
from image_triage.ui.display_metrics import COMPACT_DISPLAY


def _ensure_app() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


class WorkflowSettingsDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        _ensure_app()

    def test_result_settings_preserves_auto_ai_embedding_batch_size(self) -> None:
        dialog = WorkflowSettingsDialog(
            sessions=["Default"],
            current_session="Default",
            winner_mode=WinnerMode.COPY,
            delete_mode=DeleteMode.SAFE_TRASH,
            ai_embed_batch_size=0,
        )

        result = dialog.result_settings()

        self.assertEqual(0, result.ai_embed_batch_size)
        dialog.deleteLater()

    def test_result_settings_defaults_startup_update_checks_on(self) -> None:
        dialog = WorkflowSettingsDialog(
            sessions=["Default"],
            current_session="Default",
            winner_mode=WinnerMode.COPY,
            delete_mode=DeleteMode.SAFE_TRASH,
        )

        result = dialog.result_settings()

        self.assertTrue(result.check_updates_on_startup)
        dialog.deleteLater()

    def test_result_settings_returns_startup_update_check_choice(self) -> None:
        dialog = WorkflowSettingsDialog(
            sessions=["Default"],
            current_session="Default",
            winner_mode=WinnerMode.COPY,
            delete_mode=DeleteMode.SAFE_TRASH,
            check_updates_on_startup=False,
        )
        dialog.check_updates_on_startup_checkbox.setChecked(True)

        result = dialog.result_settings()

        self.assertTrue(result.check_updates_on_startup)
        dialog.deleteLater()

    def test_single_drive_expansion_defaults_on_and_returns_the_choice(self) -> None:
        dialog = WorkflowSettingsDialog(
            sessions=["Default"],
            current_session="Default",
            winner_mode=WinnerMode.COPY,
            delete_mode=DeleteMode.SAFE_TRASH,
        )

        self.assertEqual(
            dialog.single_drive_expansion_checkbox.text(),
            "Keep only one branch expanded per level",
        )
        self.assertTrue(dialog.result_settings().single_drive_expansion_enabled)
        dialog.single_drive_expansion_checkbox.setChecked(False)
        self.assertFalse(dialog.result_settings().single_drive_expansion_enabled)
        dialog.deleteLater()

    def test_toolbar_presentation_is_not_exposed_in_settings(self) -> None:
        dialog = WorkflowSettingsDialog(
            sessions=["Default"],
            current_session="Default",
            winner_mode=WinnerMode.COPY,
            delete_mode=DeleteMode.SAFE_TRASH,
        )

        labels = {label.text() for label in dialog.findChildren(QLabel)}

        self.assertFalse(hasattr(dialog, "toolbar_style_combo"))
        self.assertNotIn("Toolbar", labels)
        self.assertFalse(hasattr(dialog.result_settings(), "toolbar_style"))
        dialog.deleteLater()

    def test_settings_tooltip_wraps_long_lines(self) -> None:
        tooltip = _settings_tooltip(
            "Weight of the tag-penalty-aware base score vs. the trained adapter when blending the final ranking.",
            width=38,
        )

        self.assertIn("\n", tooltip)
        self.assertLessEqual(max(len(line) for line in tooltip.splitlines()), 38)

    def test_result_settings_returns_custom_ai_embedding_batch_size(self) -> None:
        dialog = WorkflowSettingsDialog(
            sessions=["Default"],
            current_session="Default",
            winner_mode=WinnerMode.COPY,
            delete_mode=DeleteMode.SAFE_TRASH,
            ai_embed_batch_size=32,
        )
        dialog.ai_embed_batch_size_spin.setValue(64)

        result = dialog.result_settings()

        self.assertEqual(64, result.ai_embed_batch_size)
        dialog.deleteLater()

    def test_clip_model_precision_is_automatic(self) -> None:
        dialog = WorkflowSettingsDialog(
            sessions=["Default"],
            current_session="Default",
            winner_mode=WinnerMode.COPY,
            delete_mode=DeleteMode.SAFE_TRASH,
            ai_clip_model_variant="fp16",
        )

        result = dialog.result_settings()

        self.assertEqual("fp32", result.ai_clip_model_variant)
        self.assertFalse(hasattr(dialog, "ai_clip_model_combo"))
        dialog.deleteLater()

    def test_dispute_and_base_score_weight_are_visible_and_round_trip(self) -> None:
        dialog = WorkflowSettingsDialog(
            sessions=["Default"],
            current_session="Default",
            winner_mode=WinnerMode.COPY,
            delete_mode=DeleteMode.SAFE_TRASH,
            ai_dispute_weight=3,
            ai_base_score_weight_percent=65,
        )

        labels = {label.text() for label in dialog.findChildren(QLabel)}
        self.assertIn("Dispute weight", labels)
        self.assertIn("Base score weight", labels)

        dialog.ai_dispute_weight_spin.setValue(5)
        dialog.ai_base_score_weight_spin.setValue(20)
        result = dialog.result_settings()

        self.assertEqual(5, result.ai_dispute_weight)
        self.assertEqual(20, result.ai_base_score_weight_percent)
        dialog.deleteLater()

    def test_interface_size_choice_round_trips_and_sizes_dialog_chrome(self) -> None:
        dialog = WorkflowSettingsDialog(
            sessions=["Default"],
            current_session="Default",
            winner_mode=WinnerMode.COPY,
            delete_mode=DeleteMode.SAFE_TRASH,
            interface_size="large",
            display_profile=COMPACT_DISPLAY,
        )

        self.assertEqual("spacious", dialog.result_settings().interface_size)
        self.assertEqual(COMPACT_DISPLAY.settings_nav_width, dialog.findChild(QFrame, "settingsSidebar").width())
        screen_cap = int(dialog.screen().availableGeometry().width() * 0.94)
        self.assertEqual(min(COMPACT_DISPLAY.settings_min_width, screen_cap), dialog.minimumWidth())
        dialog.deleteLater()

    def test_settings_refresh_uses_descriptions_and_labeled_help(self) -> None:
        dialog = WorkflowSettingsDialog(
            sessions=["Default"],
            current_session="Default",
            winner_mode=WinnerMode.COPY,
            delete_mode=DeleteMode.SAFE_TRASH,
        )

        labels = {label.text() for label in dialog.findChildren(QLabel)}

        self.assertGreaterEqual(dialog.minimumWidth(), 700)
        self.assertIn("Review behavior", labels)
        self.assertIn("Navigation and preview", labels)
        self.assertEqual("Open settings guide", dialog.help_button.text())
        dialog.deleteLater()

    def test_phash_prefilter_result_settings_round_trip_controls(self) -> None:
        dialog = WorkflowSettingsDialog(
            sessions=["Default"],
            current_session="Default",
            winner_mode=WinnerMode.COPY,
            delete_mode=DeleteMode.SAFE_TRASH,
            phash_prefilter_settings=PHashPrefilterSettings(
                enabled=True,
                hamming_threshold=4,
                cache_enabled=False,
                diagnostics_enabled=True,
            ),
        )

        result = dialog.result_settings().phash_prefilter_settings

        self.assertTrue(result.enabled)
        self.assertNotIn("Run timing", {label.text() for label in dialog.findChildren(QLabel)})
        self.assertEqual(4, result.hamming_threshold)
        self.assertFalse(result.cache_enabled)
        self.assertTrue(result.diagnostics_enabled)
        dialog.deleteLater()


if __name__ == "__main__":
    unittest.main()
