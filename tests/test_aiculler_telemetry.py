from __future__ import annotations

import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

from aiculler.telemetry import (
    TelemetryEvent,
    ThreadedTelemetryLogger,
    build_override_report,
    classify_override,
    ensure_user_overrides_schema,
    format_override_report,
)


class AICullerTelemetryTests(unittest.TestCase):
    @staticmethod
    def _event(image_id: str, *, created_at: str = "2026-06-20T12:00:00") -> TelemetryEvent:
        return TelemetryEvent(
            image_id=image_id,
            folder_id="folder",
            cluster_id="cluster",
            category_id="category",
            ai_initial_bucket="reject",
            user_final_bucket="keeper",
            previous_bucket=None,
            override_type="reject_rescue",
            action_source="dispute",
            ai_initial_score=0.1,
            base_score=0.2,
            adapter_score=None,
            topiq_score=0.2,
            adapter_version="adapter-v1",
            model_version="adapter-v1",
            is_final=1,
            ignored_for_training=0,
            created_at=created_at,
        )

    def test_classify_override_maps_bucket_movements(self) -> None:
        cases = {
            ("reject", "keeper"): "reject_rescue",
            ("needs_review", "ai_pick"): "review_promotion",
            ("keeper", "reject"): "keeper_demotion",
            ("ai_pick", "needs_review"): "pick_demotion",
            ("reject", "needs_review"): "low_bucket_confirmation",
            ("keeper", "ai_pick"): "high_bucket_confirmation",
            ("needs_review", "unlabeled"): "bucket_change",
        }
        for movement, expected in cases.items():
            with self.subTest(movement=movement):
                self.assertEqual(expected, classify_override(*movement))

    def test_schema_creates_indexes(self) -> None:
        with tempfile.TemporaryDirectory(prefix="aiculler_telemetry_schema_") as temp_dir:
            db_path = Path(temp_dir) / "aiculler.sqlite"
            connection = sqlite3.connect(db_path)
            try:
                ensure_user_overrides_schema(connection)
                indexes = {
                    row[1]
                    for row in connection.execute("PRAGMA index_list(user_overrides)").fetchall()
                }
            finally:
                connection.close()

        self.assertIn("idx_user_overrides_image_id", indexes)
        self.assertIn("idx_user_overrides_training", indexes)

    def test_threaded_logger_flushes_on_shutdown(self) -> None:
        with tempfile.TemporaryDirectory(prefix="aiculler_telemetry_logger_") as temp_dir:
            db_path = Path(temp_dir) / "aiculler.sqlite"
            logger = ThreadedTelemetryLogger(db_path, batch_size=10, flush_interval_sec=5.0)
            logger.log_event(self._event("one"))
            logger.shutdown()
            time.sleep(0.05)
            connection = sqlite3.connect(db_path)
            try:
                row = connection.execute("SELECT image_id, override_type, is_final FROM user_overrides").fetchone()
            finally:
                connection.close()

        self.assertEqual(("one", "reject_rescue", 1), row)

    def test_threaded_logger_flushes_when_batch_size_is_reached(self) -> None:
        with tempfile.TemporaryDirectory(prefix="aiculler_telemetry_batch_") as temp_dir:
            db_path = Path(temp_dir) / "aiculler.sqlite"
            logger = ThreadedTelemetryLogger(db_path, batch_size=2, flush_interval_sec=5.0)
            try:
                logger.log_event(self._event("one"))
                logger.log_event(self._event("two"))
                count = self._wait_for_row_count(db_path, 2)
            finally:
                logger.shutdown()

        self.assertEqual(2, count)

    def test_threaded_logger_flushes_on_timer(self) -> None:
        with tempfile.TemporaryDirectory(prefix="aiculler_telemetry_timed_") as temp_dir:
            db_path = Path(temp_dir) / "aiculler.sqlite"
            logger = ThreadedTelemetryLogger(db_path, batch_size=10, flush_interval_sec=0.05)
            try:
                logger.log_event(self._event("one"))
                count = self._wait_for_row_count(db_path, 1)
            finally:
                logger.shutdown()

        self.assertEqual(1, count)

    @staticmethod
    def _wait_for_row_count(db_path: Path, expected: int, *, timeout_sec: float = 2.0) -> int:
        deadline = time.monotonic() + timeout_sec
        last_count = 0
        while time.monotonic() < deadline:
            if not db_path.exists():
                time.sleep(0.02)
                continue
            connection = sqlite3.connect(db_path)
            try:
                row = connection.execute(
                    "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name = 'user_overrides'"
                ).fetchone()
                if not row or int(row[0] or 0) == 0:
                    time.sleep(0.02)
                    continue
                last_count = int(connection.execute("SELECT COUNT(*) FROM user_overrides").fetchone()[0])
            finally:
                connection.close()
            if last_count >= expected:
                return last_count
            time.sleep(0.02)
        return last_count

    def test_build_override_report_counts_failures_by_dimension(self) -> None:
        rows = [
            {
                "override_type": "reject_rescue",
                "folder_id": "folder-a",
                "category_id": "sports",
                "cluster_id": "cluster-1",
                "model_version": "adapter-v1",
                "action_source": "dispute",
                "is_final": 1,
                "ignored_for_training": 0,
            },
            {
                "override_type": "reject_rescue",
                "folder_id": "folder-a",
                "category_id": "sports",
                "cluster_id": "cluster-2",
                "model_version": "adapter-v1",
                "action_source": "keyboard_shortcut",
                "is_final": 1,
                "ignored_for_training": 0,
            },
            {
                "override_type": "pick_demotion",
                "folder_id": "folder-b",
                "category_id": "portrait",
                "cluster_id": "cluster-3",
                "model_version": "adapter-v2",
                "action_source": "dispute",
                "is_final": 1,
                "ignored_for_training": 0,
            },
            {
                "override_type": "reject_rescue",
                "folder_id": "folder-c",
                "category_id": "ignored",
                "model_version": "adapter-v2",
                "action_source": "auto_action",
                "is_final": 0,
                "ignored_for_training": 1,
            },
        ]

        report = build_override_report(rows, top_n=2)

        self.assertEqual(4, report["override_count"])
        self.assertEqual(3, report["training_eligible_count"])
        self.assertEqual(1, report["intermediate_or_ignored_count"])
        self.assertEqual({"reject_rescue": 2, "pick_demotion": 1}, report["training_counts_by_type"])
        self.assertEqual({"sports": 2, "portrait": 1}, report["counts_by_category"])
        self.assertEqual({"folder-a": 2}, report["reject_rescues_by_folder"])
        self.assertEqual([{"key": "sports", "count": 2}], report["worst_categories_by_reject_rescue"])
        self.assertIn("Override Report", format_override_report(report))


if __name__ == "__main__":
    unittest.main()
