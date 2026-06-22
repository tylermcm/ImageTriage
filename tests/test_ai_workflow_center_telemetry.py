from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from aiculler.telemetry import ensure_user_overrides_schema
from image_triage.ai_workflow_center import AIWorkflowCenterDialog, _load_telemetry_health


class _Spy:
    def __init__(self, return_value=None) -> None:
        self.return_value = return_value
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return self.return_value


class WorkflowCenterTelemetryTests(unittest.TestCase):
    def test_load_telemetry_health_counts_final_and_ignored_rows(self) -> None:
        with tempfile.TemporaryDirectory(prefix="workflow_telemetry_") as temp_dir:
            db_path = Path(temp_dir) / "aiculler.sqlite"
            connection = sqlite3.connect(db_path)
            try:
                ensure_user_overrides_schema(connection)
                rows = [
                    ("img1", "reject", "keeper", "rescue", "adapter_label", 1, 0, "2026-06-21T01:00:00"),
                    ("img2", "keeper", "reject", "demotion", "adapter_label", 0, 1, "2026-06-21T01:01:00"),
                    ("img3", "needs_review", "keeper", "promotion", "auto_action", 1, 1, "2026-06-21T01:02:00"),
                ]
                connection.executemany(
                    """
                    INSERT INTO user_overrides (
                        image_id, folder_id, ai_initial_bucket, user_final_bucket,
                        override_type, action_source, is_final, ignored_for_training, created_at
                    )
                    VALUES (?, 'folder', ?, ?, ?, ?, ?, ?, ?)
                    """,
                    rows,
                )
                connection.commit()
            finally:
                connection.close()

            health = _load_telemetry_health(db_path)

        self.assertEqual(3, health["override_count"])
        self.assertEqual(1, health["final_usable_override_count"])
        self.assertEqual(2, health["ignored_intermediate_override_count"])
        self.assertEqual("2026-06-21T01:02:00", health["latest_override_created_at"])

    def test_load_telemetry_health_missing_table_returns_zeroes(self) -> None:
        with tempfile.TemporaryDirectory(prefix="workflow_telemetry_empty_") as temp_dir:
            db_path = Path(temp_dir) / "aiculler.sqlite"
            sqlite3.connect(db_path).close()

            health = _load_telemetry_health(db_path)

        self.assertEqual(0, health["override_count"])
        self.assertEqual(0, health["final_usable_override_count"])
        self.assertEqual(0, health["ignored_intermediate_override_count"])
        self.assertEqual("", health["latest_override_created_at"])


class WorkflowCenterReviewVisibilityTests(unittest.TestCase):
    def test_restore_after_adapter_review_only_when_hidden_by_review(self) -> None:
        dialog = SimpleNamespace(
            _hidden_for_adapter_review=False,
            isVisible=_Spy(False),
            hide=_Spy(),
            refresh=_Spy(),
            show=_Spy(),
            raise_=_Spy(),
            activateWindow=_Spy(),
        )

        AIWorkflowCenterDialog.hide_for_adapter_review(dialog)
        AIWorkflowCenterDialog.restore_after_adapter_review(dialog)

        self.assertEqual(0, dialog.hide.calls)
        self.assertEqual(0, dialog.show.calls)

        dialog.isVisible = _Spy(True)
        AIWorkflowCenterDialog.hide_for_adapter_review(dialog)
        AIWorkflowCenterDialog.restore_after_adapter_review(dialog)

        self.assertEqual(1, dialog.hide.calls)
        self.assertEqual(1, dialog.refresh.calls)
        self.assertEqual(1, dialog.show.calls)
        self.assertEqual(1, dialog.raise_.calls)
        self.assertEqual(1, dialog.activateWindow.calls)
        self.assertFalse(dialog._hidden_for_adapter_review)


if __name__ == "__main__":
    unittest.main()
