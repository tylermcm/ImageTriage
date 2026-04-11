from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from image_triage.models import ImageRecord, SessionAnnotation
from image_triage.window import AnnotationHydrationTask


def _record(path: str) -> ImageRecord:
    file_path = Path(path)
    return ImageRecord(
        path=path,
        name=file_path.name,
        size=100,
        modified_ns=123,
    )


class AnnotationHydrationTaskTests(unittest.TestCase):
    def test_hydration_prioritizes_visible_paths_before_remaining(self) -> None:
        with tempfile.TemporaryDirectory(prefix="image_triage_hydration_") as temp_dir:
            root = Path(temp_dir)
            a = str(root / "a.jpg")
            b = str(root / "b.jpg")
            c = str(root / "c.jpg")
            d = str(root / "d.jpg")
            records = [_record(a), _record(b), _record(c), _record(d)]

            persisted_map = {
                a: SessionAnnotation(winner=True),
                c: SessionAnnotation(reject=True),
            }
            sidecar_map = {
                d: SessionAnnotation(photoshop=True),
            }
            load_calls: list[tuple[str, ...]] = []

            class StubDecisionStore:
                def load_annotations_for_paths(
                    self,
                    session_id: str,
                    records_by_path: dict[str, ImageRecord],
                    paths: list[str] | tuple[str, ...] | set[str],
                ) -> dict[str, SessionAnnotation]:
                    _ = session_id
                    ordered_paths = tuple(path for path in paths if path in records_by_path)
                    load_calls.append(ordered_paths)
                    return {
                        path: persisted_map[path]
                        for path in ordered_paths
                        if path in persisted_map
                    }

            task = AnnotationHydrationTask(
                scope_key="scope",
                token=9,
                session_id="session",
                records=tuple(records),
                prioritized_paths=(c, a, c, str(root / "missing.jpg")),
            )
            chunks: list[dict[str, SessionAnnotation]] = []
            finished: list[tuple[str, int]] = []
            task.signals.chunk.connect(lambda scope, token, chunk: chunks.append(dict(chunk)))
            task.signals.finished.connect(lambda scope, token: finished.append((scope, token)))

            with (
                patch("image_triage.window.DecisionStore", StubDecisionStore),
                patch(
                    "image_triage.window.load_sidecar_annotation",
                    side_effect=lambda path: sidecar_map.get(path, SessionAnnotation()),
                ),
            ):
                task.run()

            self.assertEqual(2, len(load_calls))
            self.assertEqual((c, a), load_calls[0])
            self.assertEqual((b, d), load_calls[1])
            self.assertEqual(2, len(chunks))
            self.assertEqual([c, a], list(chunks[0].keys()))
            self.assertEqual([d], list(chunks[1].keys()))
            self.assertEqual([("scope", 9)], finished)

    def test_hydration_cancel_before_run_skips_work(self) -> None:
        records = (_record("C:/tmp/a.jpg"),)
        load_calls: list[tuple[str, ...]] = []

        class StubDecisionStore:
            def load_annotations_for_paths(
                self,
                session_id: str,
                records_by_path: dict[str, ImageRecord],
                paths: list[str] | tuple[str, ...] | set[str],
            ) -> dict[str, SessionAnnotation]:
                _ = session_id
                load_calls.append(tuple(path for path in paths if path in records_by_path))
                return {}

        task = AnnotationHydrationTask(
            scope_key="scope",
            token=1,
            session_id="session",
            records=records,
            prioritized_paths=(),
        )
        chunks: list[dict[str, SessionAnnotation]] = []
        finished: list[tuple[str, int]] = []
        task.signals.chunk.connect(lambda scope, token, chunk: chunks.append(dict(chunk)))
        task.signals.finished.connect(lambda scope, token: finished.append((scope, token)))
        task.cancel()
        with patch("image_triage.window.DecisionStore", StubDecisionStore):
            task.run()
        self.assertEqual([], load_calls)
        self.assertEqual([], chunks)
        self.assertEqual([], finished)


if __name__ == "__main__":
    unittest.main()
