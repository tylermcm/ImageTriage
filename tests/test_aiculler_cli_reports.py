from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from aiculler.telemetry import ensure_user_overrides_schema


class AICullerCliReportTests(unittest.TestCase):
    def test_override_report_outputs_stable_json(self) -> None:
        with tempfile.TemporaryDirectory(prefix="aiculler_override_cli_") as temp_dir:
            db_path = Path(temp_dir) / "aiculler.sqlite"
            connection = sqlite3.connect(db_path)
            try:
                ensure_user_overrides_schema(connection)
                connection.execute(
                    """
                    INSERT INTO user_overrides (
                        image_id, folder_id, cluster_id, category_id,
                        ai_initial_bucket, user_final_bucket, previous_bucket,
                        override_type, action_source,
                        ai_initial_score, base_score, adapter_score, topiq_score,
                        adapter_version, model_version,
                        is_final, ignored_for_training, created_at
                    )
                    VALUES (
                        'one', 'folder-a', 'cluster-a', 'sports',
                        'reject', 'keeper', NULL,
                        'reject_rescue', 'dispute',
                        0.1, 0.2, NULL, 0.2,
                        'adapter-v1', 'adapter-v1',
                        1, 0, '2026-06-20T12:00:00'
                    )
                    """
                )
                connection.commit()
            finally:
                connection.close()

            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "aiculler.cli",
                    "--db",
                    str(db_path),
                    "--no-log",
                    "override-report",
                ],
                cwd=Path(__file__).resolve().parents[1],
                check=True,
                capture_output=True,
                text=True,
            )

        payload = json.loads(result.stdout)
        self.assertEqual(1, payload["override_count"])
        self.assertEqual({"reject_rescue": 1}, payload["training_counts_by_type"])
        self.assertEqual({"sports": 1}, payload["counts_by_category"])

if __name__ == "__main__":
    unittest.main()
