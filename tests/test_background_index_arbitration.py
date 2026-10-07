"""Background GPU indexing must yield to the interactive editor.

When the full-screen preview/editor opens it needs the GPU for masking (SAM /
OneFormer / BiRefNet), so the background semantic + face index passes are
suspended (cancelled) and resumed on close. These tests exercise the
suspend/resume logic on a stub ``self`` so the whole MainWindow need not be
constructed.
"""

from __future__ import annotations

import os
import unittest
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from image_triage.records_view_controller import RecordsViewController


class _FakeTask:
    def __init__(self) -> None:
        self.cancelled = False

    def cancel(self) -> None:
        self.cancelled = True


def _stub(*, with_tasks: bool = True):
    calls: list[tuple[str, object]] = []
    window = SimpleNamespace(
        _background_indexing_suspended=False,
        _active_semantic_index_task=_FakeTask() if with_tasks else None,
        _active_face_index_task=_FakeTask() if with_tasks else None,
        _semantic_index_active=True,
        _face_index_active=True,
        _background_index_records=[object(), object()],
    )
    controller = RecordsViewController(window)
    controller.maybe_start_semantic_index = lambda recs: calls.append(("semantic", recs))
    return window, controller, calls


class BackgroundIndexArbitrationTests(unittest.TestCase):
    def test_suspend_cancels_both_passes(self) -> None:
        window, controller, _ = _stub()
        sem, face = window._active_semantic_index_task, window._active_face_index_task
        controller.suspend_background_indexing()
        self.assertTrue(window._background_indexing_suspended)
        self.assertTrue(sem.cancelled)
        self.assertTrue(face.cancelled)
        self.assertIsNone(window._active_semantic_index_task)
        self.assertIsNone(window._active_face_index_task)
        self.assertFalse(window._semantic_index_active)
        self.assertFalse(window._face_index_active)

    def test_suspend_is_idempotent(self) -> None:
        window, controller, _ = _stub()
        controller.suspend_background_indexing()
        # Second call must not raise even though the tasks are already cleared.
        controller.suspend_background_indexing()
        self.assertTrue(window._background_indexing_suspended)

    def test_resume_restarts_semantic_with_stored_records(self) -> None:
        window, controller, calls = _stub(with_tasks=False)
        window._background_indexing_suspended = True
        records = window._background_index_records
        controller.resume_background_indexing()
        self.assertFalse(window._background_indexing_suspended)
        self.assertEqual([("semantic", records)], calls)

    def test_resume_is_noop_when_not_suspended(self) -> None:
        window, controller, calls = _stub(with_tasks=False)
        controller.resume_background_indexing()
        self.assertEqual([], calls)

    def test_resume_without_records_only_clears_flag(self) -> None:
        window, controller, calls = _stub(with_tasks=False)
        window._background_indexing_suspended = True
        window._background_index_records = []
        controller.resume_background_indexing()
        self.assertFalse(window._background_indexing_suspended)
        self.assertEqual([], calls)


if __name__ == "__main__":
    unittest.main()
