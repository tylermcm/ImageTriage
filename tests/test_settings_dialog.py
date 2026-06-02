from __future__ import annotations

import unittest

from PySide6.QtWidgets import QApplication

from image_triage.models import DeleteMode, WinnerMode
from image_triage.dino_prefilter import DINOPrefilterMode, DINOPrefilterSettings
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

    def test_result_settings_returns_clip_model_variant(self) -> None:
        dialog = WorkflowSettingsDialog(
            sessions=["Default"],
            current_session="Default",
            winner_mode=WinnerMode.COPY,
            delete_mode=DeleteMode.SAFE_TRASH,
            ai_clip_model_variant="uint8",
        )
        dialog.ai_clip_model_combo.setCurrentIndex(dialog.ai_clip_model_combo.findData("q4"))

        result = dialog.result_settings()

        self.assertEqual("q4", result.ai_clip_model_variant)
        self.assertIn("Warning:", dialog.ai_clip_model_warning_label.text())
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

    def test_dino_prefilter_defaults_off_and_has_settings_page(self) -> None:
        dialog = WorkflowSettingsDialog(
            sessions=["Default"],
            current_session="Default",
            winner_mode=WinnerMode.COPY,
            delete_mode=DeleteMode.SAFE_TRASH,
        )

        pages = [dialog.section_list.item(index).text() for index in range(dialog.section_list.count())]
        result = dialog.result_settings()

        self.assertIn("DINO Prefilter", pages)
        self.assertFalse(result.dino_prefilter_settings.enabled)
        self.assertEqual(DINOPrefilterMode.SOFT_QUARANTINE, result.dino_prefilter_settings.mode)
        dialog.deleteLater()

    def test_dino_prefilter_result_settings_round_trip_controls(self) -> None:
        dialog = WorkflowSettingsDialog(
            sessions=["Default"],
            current_session="Default",
            winner_mode=WinnerMode.COPY,
            delete_mode=DeleteMode.SAFE_TRASH,
            dino_prefilter_settings=DINOPrefilterSettings(
                enabled=True,
                mode=DINOPrefilterMode.POOL_REMOVAL,
                aggressiveness_percent=92,
                technical_trash_enabled=False,
                duplicate_trash_enabled=True,
                phash_duplicate_enabled=False,
                phash_hamming_threshold=4,
                low_information_enabled=True,
                rescue_ai_high_score_enabled=False,
                rescue_user_keep_enabled=True,
                rescue_semantic_unique_enabled=False,
                rescue_best_representative_enabled=True,
                diagnostics_enabled=True,
            ),
        )

        result = dialog.result_settings().dino_prefilter_settings

        self.assertTrue(result.enabled)
        self.assertEqual(DINOPrefilterMode.POOL_REMOVAL, result.mode)
        self.assertEqual(92, result.aggressiveness_percent)
        self.assertFalse(result.technical_trash_enabled)
        self.assertFalse(result.phash_duplicate_enabled)
        self.assertEqual(4, result.phash_hamming_threshold)
        self.assertTrue(result.low_information_enabled)
        self.assertFalse(result.rescue_ai_high_score_enabled)
        self.assertFalse(result.rescue_semantic_unique_enabled)
        dialog.deleteLater()


if __name__ == "__main__":
    unittest.main()
