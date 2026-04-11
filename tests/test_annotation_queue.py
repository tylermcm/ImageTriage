from __future__ import annotations

from queue import Empty, SimpleQueue
import unittest
from unittest.mock import patch

from PySide6.QtWidgets import QApplication

from image_triage.annotation_queue import (
    AnnotationPersistenceQueue,
    AnnotationQueueEntry,
    _AnnotationPersistTask,
)
from image_triage.models import ImageRecord, SessionAnnotation


def _ensure_app() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def _record(path: str) -> ImageRecord:
    return ImageRecord(path=path, name=path.rsplit("/", 1)[-1], size=123, modified_ns=1)


def _drain_queue(result_queue: SimpleQueue) -> list[tuple]:
    events: list[tuple] = []
    while True:
        try:
            events.append(result_queue.get_nowait())
        except Empty:
            break
    return events


class AnnotationPersistTaskTests(unittest.TestCase):
    def test_task_persists_by_session_and_emits_ok_done(self) -> None:
        save_calls: list[tuple[str, tuple[str, ...]]] = []

        class FakeStore:
            def save_annotations(self, session_id, pairs):
                save_calls.append((session_id, tuple(record.path for record, _annotation in pairs)))

            def save_annotation(self, session_id, record, annotation):  # pragma: no cover - fallback path
                raise AssertionError("fallback save_annotation should not be called")

            def delete_annotation(self, session_id, record_path):  # pragma: no cover - fallback path
                raise AssertionError("fallback delete_annotation should not be called")

        entries = (
            AnnotationQueueEntry("C:/shots/a.jpg", _record("C:/shots/a.jpg"), "session-1", SessionAnnotation(winner=True)),
            AnnotationQueueEntry("C:/shots/b.jpg", _record("C:/shots/b.jpg"), "session-1", SessionAnnotation(reject=True)),
            AnnotationQueueEntry("C:/shots/c.jpg", _record("C:/shots/c.jpg"), "session-2", SessionAnnotation(winner=False, reject=False)),
        )
        queue = SimpleQueue()
        task = _AnnotationPersistTask(entries, queue)

        with patch("image_triage.annotation_queue.DecisionStore", FakeStore), patch(
            "image_triage.annotation_queue.sync_sidecar_annotation",
            return_value=None,
        ):
            task.run()

        self.assertEqual(len(save_calls), 2)
        self.assertEqual(save_calls[0][0], "session-1")
        self.assertEqual(save_calls[1][0], "session-2")
        events = _drain_queue(queue)
        ok_paths = [event[1] for event in events if event and event[0] == "ok"]
        self.assertEqual(set(ok_paths), {"C:/shots/a.jpg", "C:/shots/b.jpg", "C:/shots/c.jpg"})
        self.assertIn(("done", 3), events)

    def test_task_emits_failed_when_fallback_single_write_fails(self) -> None:
        failed_paths: list[str] = []

        class FakeStore:
            def save_annotations(self, session_id, pairs):
                raise RuntimeError("bulk write unavailable")

            def save_annotation(self, session_id, record, annotation):
                if record.path.endswith("b.jpg"):
                    raise RuntimeError("single write failed")

            def delete_annotation(self, session_id, record_path):  # pragma: no cover - not used in this test
                return None

        entries = (
            AnnotationQueueEntry("C:/shots/a.jpg", _record("C:/shots/a.jpg"), "session-1", SessionAnnotation(winner=True)),
            AnnotationQueueEntry("C:/shots/b.jpg", _record("C:/shots/b.jpg"), "session-1", SessionAnnotation(reject=True)),
        )
        queue = SimpleQueue()
        task = _AnnotationPersistTask(entries, queue)

        with patch("image_triage.annotation_queue.DecisionStore", FakeStore), patch(
            "image_triage.annotation_queue.sync_sidecar_annotation",
            return_value=None,
        ):
            task.run()

        events = _drain_queue(queue)
        for event in events:
            if event and event[0] == "failed":
                failed_paths.append(event[1])
        self.assertEqual(failed_paths, ["C:/shots/b.jpg"])
        self.assertIn(("ok", "C:/shots/a.jpg"), events)
        self.assertIn(("done", 2), events)


class AnnotationPersistenceQueueTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        _ensure_app()

    def test_enqueue_coalesces_latest_annotation_for_same_path(self) -> None:
        queue = AnnotationPersistenceQueue(flush_interval_ms=999_999)
        record = _record("C:/shots/sample.jpg")
        first = SessionAnnotation(winner=True)
        second = SessionAnnotation(reject=True)

        queue.enqueue(
            record.path,
            first,
            record=record,
            session_id="session-1",
            previous_annotation=SessionAnnotation(),
        )
        queue.enqueue(
            record.path,
            second,
            record=record,
            session_id="session-1",
            previous_annotation=first,
        )

        self.assertEqual(len(queue._pending), 1)  # type: ignore[attr-defined]
        pending = queue._pending[record.path]  # type: ignore[attr-defined]
        self.assertTrue(pending.annotation is not None and pending.annotation.reject)
        self.assertTrue(pending.previous_annotation is not None and pending.previous_annotation.winner)

    def test_rollback_returns_cloned_annotation(self) -> None:
        queue = AnnotationPersistenceQueue(flush_interval_ms=999_999)
        record = _record("C:/shots/sample.jpg")
        previous = SessionAnnotation(winner=True, rating=3, tags=("hero",))
        queue.enqueue(
            record.path,
            SessionAnnotation(reject=True),
            record=record,
            session_id="session-1",
            previous_annotation=previous,
        )

        rolled_back = queue.rollback(record.path)
        self.assertIsNotNone(rolled_back)
        assert rolled_back is not None
        self.assertEqual(rolled_back, previous)
        self.assertIsNot(rolled_back, previous)
        self.assertIsNone(queue.rollback(record.path))


if __name__ == "__main__":
    unittest.main()

