from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QAbstractButton, QLabel

from image_triage.ai_workflow_center import AIWorkflowCenterDialog, WorkflowSnapshot
from image_triage.phash_prefilter import default_phash_prefilter_settings
from image_triage.ui.ai_cull_preferences_dialog import GuidedAICullPreferencesDialog


class CurrentAIWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_guided_cull_only_exposes_live_pipeline_controls(self) -> None:
        dialog = GuidedAICullPreferencesDialog(
            folder_name="Sample",
            image_count=100,
            keep_top_percent=12,
            review_band_percent=8,
            phash_prefilter_settings=default_phash_prefilter_settings(),
        )

        text_widgets = [
            *dialog.findChildren(QLabel),
            *dialog.findChildren(QAbstractButton),
        ]
        visible_text = "\n".join(widget.text() for widget in text_widgets if widget.text())
        self.assertIn("CLIP", visible_text)
        self.assertIn("TOPIQ", visible_text)
        self.assertIn("InsightFace", visible_text)
        self.assertIn("near-duplicate", visible_text)
        self.assertNotIn("Adapter", visible_text)
        self.assertNotIn("DINO", visible_text)

        dialog.keep_slider.setValue(20)
        dialog.review_slider.setValue(15)
        preferences = dialog.result_preferences()
        self.assertEqual(preferences.keep_top_percent, 20)
        self.assertEqual(preferences.review_band_percent, 15)
        self.assertFalse(hasattr(preferences, "base_score_weight_percent"))
        self.assertFalse(hasattr(preferences, "dino_prefilter_settings"))
        dialog.close()

    def test_workflow_center_returns_current_four_step_pipeline(self) -> None:
        snapshot = WorkflowSnapshot(
            runtime_ready=True,
            runtime_source="runtime",
            model_root="models",
            runtime_note="",
            clip_model_label="FP32",
            folder_open=True,
            db_exists=True,
            indexed_count=100,
            cluster_run_id="run-1",
            can_rerank=True,
            label_count=0,
            pending_label_count=0,
            global_label_count=0,
            global_label_values=0,
            global_matching_label_count=0,
            global_matching_label_values=0,
            global_matching_dispute_count=0,
            telemetry_override_count=0,
            telemetry_final_usable_override_count=0,
            telemetry_ignored_intermediate_override_count=0,
            telemetry_latest_override_created_at="",
            adapter_version="",
            adapter_created_at="",
            train_mae=None,
            holdout_mae=None,
            train_rank_lift=None,
            scored_count=0,
            folder_path="C:/photos",
            file_count=100,
        )

        steps = AIWorkflowCenterDialog._build_steps(object(), snapshot)

        self.assertEqual(tuple(steps), ("setup", "index", "review", "apply"))
        all_text = "\n".join(
            text
            for step in steps.values()
            for text in (
                step.title,
                step.subtitle,
                step.description,
                *(action.label for action in step.actions),
            )
        )
        self.assertIn("Cull & Score", all_text)
        self.assertIn("Quick Rerank", all_text)
        self.assertNotIn("adapter", all_text.casefold())
        self.assertNotIn("DINO", all_text)


if __name__ == "__main__":
    unittest.main()
