from __future__ import annotations

from contextlib import redirect_stdout
import io
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

import numpy as np

from aiculler.storage import SQLiteFeatureStore
from aiculler.telemetry import ensure_user_overrides_schema
from aiculler.cli import _ingestion_speed_summary, _log_speed_phase


class AICullerCliReportTests(unittest.TestCase):
    def test_ingestion_summary_counts_each_preview_once_and_reports_feature_phases(self) -> None:
        events = [
            {
                "status": "previewed",
                "preview_seconds": 0.25,
                "feature_seconds": 0.0,
                "persistence_seconds": 0.0,
                "total_seconds": 0.25,
                "feature_timings": {},
            },
            {
                "status": "ready",
                "preview_seconds": 0.25,
                "feature_seconds": 0.5,
                "persistence_seconds": 0.1,
                "total_seconds": 0.85,
                "feature_timings": {"clip_inference": 0.2, "topiq_inference": 0.15},
            },
        ]

        summary = _ingestion_speed_summary(events, 1.0)

        self.assertEqual(0.25, summary["preview_total_seconds"])
        self.assertEqual(0.5, summary["feature_total_seconds"])
        self.assertEqual(0.1, summary["persistence_total_seconds"])
        self.assertEqual(0.2, summary["feature_phase_total_seconds"]["clip_inference"])
        self.assertEqual(0.15, summary["feature_phase_total_seconds"]["topiq_inference"])

    def test_speed_phase_emits_cross_process_performance_metric(self) -> None:
        class LoggerStub:
            def event(self, _event: str, _payload: dict) -> None:
                return

        output = io.StringIO()
        previous = os.environ.get("IMAGE_TRIAGE_AI_METRICS")
        os.environ["IMAGE_TRIAGE_AI_METRICS"] = "1"
        try:
            with redirect_stdout(output):
                _log_speed_phase(LoggerStub(), "assign_categories", time.perf_counter(), rows=12)
        finally:
            if previous is None:
                os.environ.pop("IMAGE_TRIAGE_AI_METRICS", None)
            else:
                os.environ["IMAGE_TRIAGE_AI_METRICS"] = previous

        line = output.getvalue().strip()
        self.assertTrue(line.startswith("AI_METRIC "))
        payload = json.loads(line.removeprefix("AI_METRIC "))
        self.assertEqual("ai.script.aiculler.phase", payload["event"])
        self.assertEqual("assign_categories", payload["phase"])
        self.assertEqual(12, payload["rows"])
        self.assertGreaterEqual(payload["duration_ms"], 0.0)

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

    def test_diagnose_adapter_writes_stable_json_file(self) -> None:
        with tempfile.TemporaryDirectory(prefix="aiculler_diagnose_cli_") as temp_dir:
            root = Path(temp_dir)
            db_path = root / "aiculler.sqlite"
            out_path = root / "diagnostics.json"
            store = SQLiteFeatureStore(db_path)
            try:
                store.save_adapter_model("adapter-v1", "centroid_style_adapter", {"base_weight": 0.0, "adapter_weight": 1.0}, {})
                ratings = []
                scores = {}
                for folder_index in range(3):
                    folder_id = f"folder-{folder_index}"
                    for row_index in range(20):
                        image_id = store.upsert_image(root / folder_id / f"image-{row_index:03d}.jpg", status="ready")
                        numeric_score = 1.0 if row_index < 5 else 0.0
                        label = "hero" if numeric_score == 1.0 else "reject"
                        base_score = 1.0 - numeric_score
                        adapter_score = numeric_score
                        store.save_features(
                            image_id,
                            np.asarray([row_index, folder_index], dtype=np.float32),
                            technical_score=base_score,
                            final_score=base_score,
                            metadata={"folder_id": folder_id},
                        )
                        ratings.append(
                            {
                                "image_id": image_id,
                                "label": label,
                                "label_type": "bucket",
                                "numeric_score": numeric_score,
                                "source": "test",
                                "label_origin": "internal_adapter",
                                "metadata": {"folder_id": folder_id},
                            }
                        )
                        scores[image_id] = {
                            "global_score": adapter_score,
                            "category_score": None,
                            "cluster_score": None,
                            "adapter_score": adapter_score,
                            "confidence": 1.0,
                            "primary_category": "cat",
                            "cluster_id": None,
                        }
                store.add_ratings(ratings)
                store.save_adapter_scores("adapter-v1", scores)
            finally:
                store.close()

            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "aiculler.cli",
                    "--db",
                    str(db_path),
                    "--no-log",
                    "diagnose-adapter",
                    "--model-version",
                    "adapter-v1",
                    "--out",
                    str(out_path),
                ],
                cwd=Path(__file__).resolve().parents[1],
                check=True,
                capture_output=True,
                text=True,
            )
            payload = json.loads(out_path.read_text(encoding="utf-8"))

        self.assertEqual("", result.stdout)
        self.assertEqual("adapter-v1", payload["model_version"])
        self.assertIn("base_vs_adapter", payload)
        self.assertEqual("advisory", payload["health"]["state"])

if __name__ == "__main__":
    unittest.main()
