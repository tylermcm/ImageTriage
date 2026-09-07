from __future__ import annotations

import unittest

from PySide6.QtWidgets import QApplication, QLabel

from image_triage.models import DeleteMode, WinnerMode
from image_triage.dino_prefilter import DINOPrefilterSettings
from image_triage.phash_prefilter import PHashPrefilterSettings
from image_triage.settings_dialog import WorkflowSettingsDialog, _settings_tooltip


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

    def test_dino_workers_default_to_recommended_and_respect_hardware_limit(self) -> None:
        dialog = WorkflowSettingsDialog(
            sessions=["Default"],
            current_session="Default",
            winner_mode=WinnerMode.COPY,
            delete_mode=DeleteMode.SAFE_TRASH,
            ai_dino_worker_count=8,
            ai_dino_worker_capacity=4,
        )

        result = dialog.result_settings()

        self.assertEqual(4, dialog.ai_dino_worker_spin.maximum())
        self.assertEqual(4, result.ai_dino_worker_count)
        self.assertIn("Recommended: 4 workers", dialog.ai_dino_worker_summary_label.text())
        dialog.deleteLater()

    def test_dino_workers_warn_when_changed_from_recommendation(self) -> None:
        dialog = WorkflowSettingsDialog(
            sessions=["Default"],
            current_session="Default",
            winner_mode=WinnerMode.COPY,
            delete_mode=DeleteMode.SAFE_TRASH,
            ai_dino_worker_count=4,
            ai_dino_worker_capacity=8,
        )
        dialog.ai_dino_worker_spin.setValue(8)

        result = dialog.result_settings()

        self.assertEqual(8, result.ai_dino_worker_count)
        self.assertIn("Warning: 4 workers is recommended", dialog.ai_dino_worker_summary_label.text())
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

    def test_result_settings_returns_label_duplicate_threshold(self) -> None:
        dialog = WorkflowSettingsDialog(
            sessions=["Default"],
            current_session="Default",
            winner_mode=WinnerMode.COPY,
            delete_mode=DeleteMode.SAFE_TRASH,
            ai_label_near_duplicate_threshold=0.965,
        )
        dialog.ai_label_near_duplicate_slider.setValue(940)

        result = dialog.result_settings()

        self.assertEqual(0.940, result.ai_label_near_duplicate_threshold)
        dialog.deleteLater()

    def test_dino_prefilter_is_not_exposed_in_current_settings(self) -> None:
        dialog = WorkflowSettingsDialog(
            sessions=["Default"],
            current_session="Default",
            winner_mode=WinnerMode.COPY,
            delete_mode=DeleteMode.SAFE_TRASH,
        )

        pages = [dialog.section_list.item(index).text() for index in range(dialog.section_list.count())]
        result = dialog.result_settings()

        self.assertNotIn("DINO Prefilter", pages)
        self.assertFalse(result.dino_prefilter_settings.enabled)
        dialog.deleteLater()

    def test_dino_prefilter_result_settings_round_trip_controls(self) -> None:
        dialog = WorkflowSettingsDialog(
            sessions=["Default"],
            current_session="Default",
            winner_mode=WinnerMode.COPY,
            delete_mode=DeleteMode.SAFE_TRASH,
            dino_prefilter_settings=DINOPrefilterSettings(
                enabled=True,
                aggressiveness_percent=92,
                technical_trash_enabled=False,
                duplicate_trash_enabled=True,
                low_information_enabled=True,
                diagnostics_enabled=True,
            ),
        )

        result = dialog.result_settings().dino_prefilter_settings

        self.assertFalse(result.enabled)
        self.assertEqual(92, result.aggressiveness_percent)
        self.assertFalse(result.technical_trash_enabled)
        self.assertTrue(result.low_information_enabled)
        labels = {label.text() for label in dialog.findChildren(QLabel)}
        self.assertNotIn("Rescue rules", labels)
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
