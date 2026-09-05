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

from image_triage.window import MainWindow


class _FakeTask:
    def __init__(self) -> None:
        self.cancelled = False

    def cancel(self) -> None:
        self.cancelled = True


def _stub(*, with_tasks: bool = True):
    calls: list[tuple[str, object]] = []
    stub = SimpleNamespace(
        _background_indexing_suspended=False,
        _active_semantic_index_task=_FakeTask() if with_tasks else None,
        _active_face_index_task=_FakeTask() if with_tasks else None,
        _semantic_index_active=True,
        _face_index_active=True,
        _background_index_records=[object(), object()],
    )
    stub._maybe_start_semantic_index = lambda recs: calls.append(("semantic", recs))
    return stub, calls


class BackgroundIndexArbitrationTests(unittest.TestCase):
    def test_suspend_cancels_both_passes(self) -> None:
        stub, _ = _stub()
        sem, face = stub._active_semantic_index_task, stub._active_face_index_task
        MainWindow._suspend_background_indexing(stub)
        self.assertTrue(stub._background_indexing_suspended)
        self.assertTrue(sem.cancelled)
        self.assertTrue(face.cancelled)
        self.assertIsNone(stub._active_semantic_index_task)
        self.assertIsNone(stub._active_face_index_task)
        self.assertFalse(stub._semantic_index_active)
        self.assertFalse(stub._face_index_active)

    def test_suspend_is_idempotent(self) -> None:
        stub, _ = _stub()
        MainWindow._suspend_background_indexing(stub)
        # Second call must not raise even though the tasks are already cleared.
        MainWindow._suspend_background_indexing(stub)
        self.assertTrue(stub._background_indexing_suspended)

    def test_resume_restarts_semantic_with_stored_records(self) -> None:
        stub, calls = _stub(with_tasks=False)
        stub._background_indexing_suspended = True
        records = stub._background_index_records
        MainWindow._resume_background_indexing(stub)
        self.assertFalse(stub._background_indexing_suspended)
        self.assertEqual([("semantic", records)], calls)

    def test_resume_is_noop_when_not_suspended(self) -> None:
        stub, calls = _stub(with_tasks=False)
        MainWindow._resume_background_indexing(stub)
        self.assertEqual([], calls)

    def test_resume_without_records_only_clears_flag(self) -> None:
        stub, calls = _stub(with_tasks=False)
        stub._background_indexing_suspended = True
        stub._background_index_records = []
        MainWindow._resume_background_indexing(stub)
        self.assertFalse(stub._background_indexing_suspended)
        self.assertEqual([], calls)


if __name__ == "__main__":
    unittest.main()
