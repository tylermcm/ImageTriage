from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

from aiculler.storage import SQLiteFeatureStore
from image_triage.face_index import (
    FACE_INDEX_SCHEMA_VERSION,
    FaceFolderIndexTask,
    plan_face_index,
)
from image_triage.quality.face import FaceRecord

ACTIVE_MODEL = "insightface:auraface"


class _FakeAnalyzer:
    """Stand-in for FaceQualityAnalyzer: returns one face per image, records calls."""

    available = True

    def __init__(self, *, identity=(1.0, 0.0)) -> None:
        self.calls: list[int] = []
        self.identity = identity

    def analyze(self, bgr) -> dict:
        self.calls.append(1)
        return {
            "faces": [
                FaceRecord(
                    bbox=(0, 0, 10, 10),
                    det_score=0.95,
                    identity_embedding=self.identity,
                    identity_model=ACTIVE_MODEL,
                )
            ]
        }


def _fake_loader(_source_path: str) -> np.ndarray:
    return np.zeros((16, 16, 3), dtype=np.uint8)


def _write_jpeg(path: Path) -> Path:
    Image.new("RGB", (8, 8), (10, 20, 30)).save(path, "JPEG")
    return path


def _run(task: FaceFolderIndexTask) -> dict:
    events = {"progress": [], "finished": [], "failed": []}
    task.signals.progress.connect(lambda f, t, c, n: events["progress"].append((c, n)))
    task.signals.finished.connect(lambda f, t, i, p: events["finished"].append((i, p)))
    task.signals.failed.connect(lambda f, t, m: events["failed"].append(m))
    task.run()
    return events


class FaceIndexTests(unittest.TestCase):
    def _seed(self, temp_dir: str, specs: list[tuple[str, float]]):
        """Create images with a stored embedding whose first component is the
        person-presence score the injected scorer will read back."""
        folder = Path(temp_dir)
        store = SQLiteFeatureStore(folder / "aiculler.sqlite")
        paths = []
        for name, prob in specs:
            path = _write_jpeg(folder / name)
            paths.append(str(path))
            image_id = store.ensure_image(path)
            vec = np.zeros(512, dtype=np.float32)
            vec[0] = prob
            store.save_semantic_embedding(image_id, vec)
        store.close()
        return folder, paths

    @staticmethod
    def _scorer(embedding) -> float:
        return float(np.asarray(embedding, dtype=np.float32).reshape(-1)[0])

    def _task(self, folder: Path, analyzer, token=1, model_identity=ACTIVE_MODEL) -> FaceFolderIndexTask:
        return FaceFolderIndexTask(
            folder=str(folder),
            token=token,
            db_path=folder / "aiculler.sqlite",
            model_identity=model_identity,
            person_scorer=self._scorer,
            analyzer=analyzer,
            image_loader=_fake_loader,
        )

    def test_only_flagged_images_run_auraface(self) -> None:
        with tempfile.TemporaryDirectory(prefix="face_idx_") as temp_dir:
            folder, _ = self._seed(temp_dir, [("person.jpg", 0.9), ("scenery.jpg", 0.0), ("crowd.jpg", 0.8)])
            analyzer = _FakeAnalyzer()
            events = _run(self._task(folder, analyzer))

            self.assertEqual([], events["failed"])
            self.assertEqual(2, sum(analyzer.calls))  # only the two person images
            store = SQLiteFeatureStore(folder / "aiculler.sqlite")
            try:
                # below-threshold image still gets a state row (face_count 0)
                scenery = next(r for r in store.list_images() if r["source_path"].endswith("scenery.jpg"))
                state = store.get_face_index_state(int(scenery["id"]))
                self.assertIsNotNone(state)
                self.assertEqual(0, int(state["face_count"]))
            finally:
                store.close()

    def test_incremental_skip_on_second_run(self) -> None:
        with tempfile.TemporaryDirectory(prefix="face_idx_") as temp_dir:
            folder, _ = self._seed(temp_dir, [("a.jpg", 0.9), ("b.jpg", 0.9)])
            first = _FakeAnalyzer()
            _run(self._task(folder, first))
            self.assertEqual(2, sum(first.calls))

            second = _FakeAnalyzer()
            events = _run(self._task(folder, second, token=2))
            self.assertEqual(0, sum(second.calls))  # both skipped
            self.assertEqual((0,), events["finished"][0][:1])

    def test_changed_file_is_reprocessed(self) -> None:
        with tempfile.TemporaryDirectory(prefix="face_idx_") as temp_dir:
            folder, paths = self._seed(temp_dir, [("a.jpg", 0.9), ("b.jpg", 0.9)])
            _run(self._task(folder, _FakeAnalyzer()))
            time.sleep(0.01)
            _write_jpeg(Path(paths[1]))  # rewrite b.jpg -> new signature

            analyzer = _FakeAnalyzer()
            _run(self._task(folder, analyzer, token=2))
            self.assertEqual(1, sum(analyzer.calls))  # only the changed one

    def test_model_identity_change_reprocesses(self) -> None:
        with tempfile.TemporaryDirectory(prefix="face_idx_") as temp_dir:
            folder, _ = self._seed(temp_dir, [("a.jpg", 0.9)])
            _run(self._task(folder, _FakeAnalyzer()))
            analyzer = _FakeAnalyzer()
            _run(self._task(folder, analyzer, token=2, model_identity="insightface:other"))
            self.assertEqual(1, sum(analyzer.calls))

    def test_faces_are_clustered(self) -> None:
        with tempfile.TemporaryDirectory(prefix="face_idx_") as temp_dir:
            folder, _ = self._seed(temp_dir, [("a.jpg", 0.9), ("b.jpg", 0.9)])
            events = _run(self._task(folder, _FakeAnalyzer(identity=(1.0, 0.0))))
            # both faces share an identity -> one person cluster
            self.assertEqual(1, events["finished"][0][1])

    def test_decode_failure_is_nonfatal(self) -> None:
        with tempfile.TemporaryDirectory(prefix="face_idx_") as temp_dir:
            folder, _ = self._seed(temp_dir, [("a.jpg", 0.9), ("b.jpg", 0.9)])

            def boom(path: str):
                if path.endswith("a.jpg"):
                    raise RuntimeError("corrupt")
                return _fake_loader(path)

            analyzer = _FakeAnalyzer()
            task = self._task(folder, analyzer)
            task._image_loader = boom
            events = _run(task)
            self.assertEqual([], events["failed"])
            store = SQLiteFeatureStore(folder / "aiculler.sqlite")
            try:
                bad = next(r for r in store.list_images() if r["source_path"].endswith("a.jpg"))
                state = store.get_face_index_state(int(bad["id"]))
                self.assertEqual(0, int(state["face_count"]))  # recorded, won't loop
            finally:
                store.close()

    def test_cancelled_task_does_no_work(self) -> None:
        with tempfile.TemporaryDirectory(prefix="face_idx_") as temp_dir:
            folder, _ = self._seed(temp_dir, [("a.jpg", 0.9)])
            analyzer = _FakeAnalyzer()
            task = self._task(folder, analyzer)
            task.cancel()
            events = _run(task)
            self.assertEqual(0, sum(analyzer.calls))
            self.assertEqual([], events["finished"])

    def test_plan_partitions_flagged_and_marked(self) -> None:
        with tempfile.TemporaryDirectory(prefix="face_idx_") as temp_dir:
            folder, _ = self._seed(temp_dir, [("a.jpg", 0.9), ("b.jpg", 0.05)])
            store = SQLiteFeatureStore(folder / "aiculler.sqlite")
            try:
                plan = plan_face_index(store, self._scorer, model_identity=ACTIVE_MODEL)
                self.assertEqual(1, len(plan.to_embed))
                self.assertEqual(1, len(plan.to_mark))
                self.assertEqual(FACE_INDEX_SCHEMA_VERSION, FACE_INDEX_SCHEMA_VERSION)
            finally:
                store.close()

    def test_animal_dominant_image_is_marked_without_running_auraface(self) -> None:
        with tempfile.TemporaryDirectory(prefix="face_idx_") as temp_dir:
            folder, _ = self._seed(temp_dir, [("monkey.jpg", 0.9), ("person.jpg", 0.8)])
            store = SQLiteFeatureStore(folder / "aiculler.sqlite")
            try:
                plan = plan_face_index(
                    store,
                    self._scorer,
                    model_identity=ACTIVE_MODEL,
                    animal_scorer=lambda embedding: -0.08 if self._scorer(embedding) > 0.85 else 0.01,
                )
                self.assertEqual(["person.jpg"], [Path(item.source_path).name for item in plan.to_embed])
                self.assertEqual(["monkey.jpg"], [Path(item.source_path).name for item in plan.to_mark])
            finally:
                store.close()

    def test_store_configures_busy_timeout(self) -> None:
        with tempfile.TemporaryDirectory(prefix="face_idx_") as temp_dir:
            store = SQLiteFeatureStore(Path(temp_dir) / "aiculler.sqlite")
            try:
                timeout = store.connection.execute("PRAGMA busy_timeout").fetchone()[0]
                self.assertEqual(30_000, int(timeout))
            finally:
                store.close()

    def test_filter_schema_migration_preserves_accepted_faces_without_auraface(self) -> None:
        with tempfile.TemporaryDirectory(prefix="face_idx_") as temp_dir:
            folder, _ = self._seed(temp_dir, [("person.jpg", 0.9)])
            first = _FakeAnalyzer()
            _run(self._task(folder, first))
            self.assertEqual(1, sum(first.calls))

            store = SQLiteFeatureStore(folder / "aiculler.sqlite")
            try:
                image = store.list_images()[0]
                state = store.get_face_index_state(int(image["id"]))
                store.set_face_index_state(
                    int(image["id"]),
                    model_identity=ACTIVE_MODEL,
                    source_signature=str(state["source_signature"]),
                    person_prob=0.9,
                    face_count=1,
                    schema_version=FACE_INDEX_SCHEMA_VERSION - 1,
                )
            finally:
                store.close()

            second = _FakeAnalyzer()
            events = _run(self._task(folder, second, token=2))
            self.assertEqual([], events["failed"])
            self.assertEqual(0, sum(second.calls))
            store = SQLiteFeatureStore(folder / "aiculler.sqlite")
            try:
                image = store.list_images()[0]
                state = store.get_face_index_state(int(image["id"]))
                self.assertEqual(FACE_INDEX_SCHEMA_VERSION, int(state["schema_version"]))
                self.assertEqual(1, int(state["face_count"]))
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
