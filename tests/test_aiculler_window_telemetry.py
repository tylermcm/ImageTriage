from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from aiculler.telemetry import TelemetryEvent
from image_triage.ai_results import AIConfidenceBucket, AIImageResult
from image_triage.models import ImageRecord
import image_triage.window as window_module
from image_triage.window import MainWindow


class _TelemetryWindowStub:
    def __init__(self, raw_result: AIImageResult, folder: Path) -> None:
        self.raw_result = raw_result
        self._current_folder = str(folder)
        self.events = []

    def _raw_ai_result_for_record(self, record):
        return self.raw_result

    def _aiculler_paths_for_current_folder(self):
        return None

    def _aiculler_bucket_for_user_label(self, label: str) -> str:
        return MainWindow._aiculler_bucket_for_user_label(label)

    def _queue_aiculler_telemetry_event(self, event):
        self.events.append(event)


class _QueueWindowStub:
    def __init__(self) -> None:
        self._aiculler_pending_telemetry_events = {}
        self.logged_events = []

    def _aiculler_telemetry_logger_for_current_folder(self):
        return object()

    def _log_aiculler_telemetry_now(self, event):
        self.logged_events.append(event)


class _FakeSignal:
    def connect(self, callback):
        self.callback = callback


class _FakeTimer:
    def __init__(self, owner=None) -> None:
        self.timeout = _FakeSignal()
        self.stopped = False
        self.interval = None

    def setSingleShot(self, value: bool) -> None:
        self.single_shot = value

    def setInterval(self, value: int) -> None:
        self.interval = value

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True


class AICullerWindowTelemetryTests(unittest.TestCase):
    @staticmethod
    def _event(image_id: str, user_bucket: str, *, previous_bucket: str | None = None) -> TelemetryEvent:
        return TelemetryEvent(
            image_id=image_id,
            folder_id="folder",
            cluster_id="cluster",
            category_id="category",
            ai_initial_bucket="reject",
            user_final_bucket=user_bucket,
            previous_bucket=previous_bucket,
            override_type="reject_rescue",
            action_source="adapter_label",
            ai_initial_score=0.1,
            base_score=0.2,
            adapter_score=None,
            topiq_score=0.2,
            adapter_version=None,
            model_version=None,
            is_final=1,
            ignored_for_training=0,
            created_at="2026-06-20T12:00:00",
        )

    def test_override_telemetry_uses_raw_ai_bucket_and_label_fields(self) -> None:
        with tempfile.TemporaryDirectory(prefix="aiculler_window_telemetry_") as temp_dir:
            folder = Path(temp_dir)
            image_path = folder / "one.jpg"
            record = ImageRecord(path=str(image_path), name="one.jpg", size=1, modified_ns=1)
            raw_result = AIImageResult(
                image_id="raw-id",
                file_path=str(image_path),
                file_name="one.jpg",
                group_id="cluster-a",
                group_size=1,
                rank_in_group=1,
                score=0.12,
                technical_score=0.2,
                confidence_bucket=AIConfidenceBucket.LIKELY_REJECT,
                confidence_summary="Raw model reject.",
            )
            stub = _TelemetryWindowStub(raw_result, folder)

            MainWindow._record_aiculler_override_telemetry(
                stub,
                record,
                user_label="keep",
                previous_label="reject",
                action_source="adapter_label",
            )

        self.assertEqual(1, len(stub.events))
        event = stub.events[0]
        self.assertEqual("raw-id", event.image_id)
        self.assertEqual(str(folder), event.folder_id)
        self.assertEqual("cluster-a", event.cluster_id)
        self.assertEqual("reject", event.ai_initial_bucket)
        self.assertEqual("keeper", event.user_final_bucket)
        self.assertEqual("reject", event.previous_bucket)
        self.assertEqual("reject_rescue", event.override_type)
        self.assertEqual("adapter_label", event.action_source)
        self.assertEqual(1, event.is_final)
        self.assertEqual(0, event.ignored_for_training)

    def test_label_clear_is_ignored_for_training_only_when_replacing_existing_label(self) -> None:
        with tempfile.TemporaryDirectory(prefix="aiculler_window_clear_") as temp_dir:
            folder = Path(temp_dir)
            image_path = folder / "one.jpg"
            record = ImageRecord(path=str(image_path), name="one.jpg", size=1, modified_ns=1)
            raw_result = AIImageResult(
                image_id="raw-id",
                file_path=str(image_path),
                file_name="one.jpg",
                group_id="cluster-a",
                group_size=1,
                rank_in_group=1,
                score=0.92,
                confidence_bucket=AIConfidenceBucket.LIKELY_KEEPER,
                confidence_summary="Raw model keeper.",
            )
            stub = _TelemetryWindowStub(raw_result, folder)

            MainWindow._record_aiculler_override_telemetry(
                stub,
                record,
                user_label="",
                previous_label="keep",
                action_source="adapter_label_clear",
            )
            MainWindow._record_aiculler_override_telemetry(
                stub,
                record,
                user_label="",
                previous_label=None,
                action_source="adapter_label_clear",
            )

        self.assertEqual(1, len(stub.events))
        event = stub.events[0]
        self.assertEqual("unlabeled", event.user_final_bucket)
        self.assertEqual("keeper", event.previous_bucket)
        self.assertEqual(1, event.ignored_for_training)

    def test_queue_coalesces_same_image_and_marks_intermediate_event_ignored(self) -> None:
        stub = _QueueWindowStub()
        original_qtimer = window_module.QTimer
        try:
            window_module.QTimer = _FakeTimer
            first = self._event("raw-id", "keeper")
            second = self._event("raw-id", "needs review")

            MainWindow._queue_aiculler_telemetry_event(stub, first)
            MainWindow._queue_aiculler_telemetry_event(stub, second)
            MainWindow._flush_pending_aiculler_telemetry(stub)
        finally:
            window_module.QTimer = original_qtimer

        self.assertEqual(2, len(stub.logged_events))
        intermediate, final = stub.logged_events
        self.assertEqual(0, intermediate.is_final)
        self.assertEqual(1, intermediate.ignored_for_training)
        self.assertEqual(1, final.is_final)
        self.assertEqual(0, final.ignored_for_training)
        self.assertEqual("reject", final.previous_bucket)


if __name__ == "__main__":
    unittest.main()
