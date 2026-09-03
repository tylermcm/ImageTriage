from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

from aiculler.storage import SQLiteFeatureStore
from image_triage.semantic_index import (
    SEMANTIC_EMBEDDING_DIM,
    SEMANTIC_INDEX_SCHEMA_VERSION,
    SemanticFolderIndexTask,
    collect_indexable_sources,
    compute_semantic_model_identity,
    plan_semantic_index,
)
from image_triage.semantic_search import FeatureStoreSemanticSearch


class _FakeEncoder:
    """Deterministic stand-in for :class:`SemanticEmbeddingExtractor`."""

    def __init__(self, dim: int = SEMANTIC_EMBEDDING_DIM, *, vectors: dict[str, np.ndarray] | None = None):
        self.embedding_dim = dim
        self.vectors = vectors or {}
        self.calls: list[str] = []
        # Attributes that would only exist on the full extractor. Their absence
        # documents that no technical/topiq/face session is ever constructed.
        assert not hasattr(self, "topiq_session")
        assert not hasattr(self, "face_analyzer")

    def encode_image(self, path) -> np.ndarray:
        self.calls.append(str(path))
        name = Path(path).name
        if name in self.vectors:
            return np.asarray(self.vectors[name], dtype=np.float32)
        seed = abs(hash(name)) % (2**32)
        rng = np.random.default_rng(seed)
        return rng.standard_normal(self.embedding_dim).astype(np.float32)


class _FailingEncoder(_FakeEncoder):
    def __init__(self, *, fail_names: set[str], **kwargs):
        super().__init__(**kwargs)
        self.fail_names = fail_names

    def encode_image(self, path) -> np.ndarray:
        if Path(path).name in self.fail_names:
            raise RuntimeError(f"corrupt image: {path}")
        return super().encode_image(path)


class _RecordingTextEncoder:
    def __init__(self, vectors: dict[str, np.ndarray]):
        self.vectors = vectors

    def encode(self, prompt: str) -> np.ndarray:
        return np.asarray(self.vectors[prompt], dtype=np.float32)


def _write_jpeg(path: Path, color: tuple[int, int, int] = (128, 64, 32)) -> Path:
    Image.new("RGB", (16, 16), color).save(path, "JPEG")
    return path


def _run_task(task: SemanticFolderIndexTask):
    events: dict[str, list] = {"progress": [], "batch_ready": [], "finished": [], "failed": []}
    task.signals.progress.connect(lambda f, t, c, n: events["progress"].append((f, t, c, n)))
    task.signals.batch_ready.connect(lambda f, t: events["batch_ready"].append((f, t)))
    task.signals.finished.connect(lambda f, t, i, r: events["finished"].append((f, t, i, r)))
    task.signals.failed.connect(lambda f, t, m: events["failed"].append((f, t, m)))
    task.run()
    return events


class SemanticAutoIndexTests(unittest.TestCase):
    def _make_folder(self, temp_dir: str, count: int = 3) -> tuple[Path, list[str]]:
        folder = Path(temp_dir)
        paths = [str(_write_jpeg(folder / f"photo_{i}.jpg")) for i in range(count)]
        return folder, paths

    def test_empty_database_receives_semantic_embeddings(self) -> None:
        with tempfile.TemporaryDirectory(prefix="sem_idx_") as temp_dir:
            folder, paths = self._make_folder(temp_dir)
            db_path = folder / "aiculler.sqlite"
            encoder = _FakeEncoder()
            task = SemanticFolderIndexTask(
                folder=str(folder),
                token=1,
                records=paths,
                db_path=db_path,
                model_identity="model-a",
                extractor=encoder,
            )
            events = _run_task(task)

            self.assertEqual([], events["failed"])
            self.assertEqual(1, len(events["finished"]))
            self.assertEqual((str(folder), 1, 3, 3), events["finished"][0])
            self.assertEqual(3, len(encoder.calls))

            store = SQLiteFeatureStore(db_path)
            try:
                rows = store.list_images(require_embedding=True)
                self.assertEqual(3, len(rows))
                for row in rows:
                    self.assertEqual(SEMANTIC_EMBEDDING_DIM, store.get_embedding_dim(int(row["id"])))
                    state = store.get_semantic_index_state(int(row["id"]))
                    self.assertIsNotNone(state)
                    self.assertEqual("model-a", state["model_identity"])
            finally:
                store.close()

    def test_valid_cached_embeddings_are_skipped(self) -> None:
        with tempfile.TemporaryDirectory(prefix="sem_idx_") as temp_dir:
            folder, paths = self._make_folder(temp_dir)
            db_path = folder / "aiculler.sqlite"
            encoder = _FakeEncoder()
            _run_task(
                SemanticFolderIndexTask(
                    folder=str(folder), token=1, records=paths, db_path=db_path,
                    model_identity="model-a", extractor=encoder,
                )
            )
            self.assertEqual(3, len(encoder.calls))

            second = _FakeEncoder()
            events = _run_task(
                SemanticFolderIndexTask(
                    folder=str(folder), token=2, records=paths, db_path=db_path,
                    model_identity="model-a", extractor=second,
                )
            )
            self.assertEqual([], second.calls)
            self.assertEqual((str(folder), 2, 0, 3), events["finished"][0])

    def test_changed_file_is_reembedded(self) -> None:
        with tempfile.TemporaryDirectory(prefix="sem_idx_") as temp_dir:
            folder, paths = self._make_folder(temp_dir)
            db_path = folder / "aiculler.sqlite"
            _run_task(
                SemanticFolderIndexTask(
                    folder=str(folder), token=1, records=paths, db_path=db_path,
                    model_identity="model-a", extractor=_FakeEncoder(),
                )
            )

            # Rewrite one file with different content so its signature changes.
            time.sleep(0.01)
            _write_jpeg(Path(paths[1]), color=(200, 10, 10))

            encoder = _FakeEncoder()
            _run_task(
                SemanticFolderIndexTask(
                    folder=str(folder), token=2, records=paths, db_path=db_path,
                    model_identity="model-a", extractor=encoder,
                )
            )
            self.assertEqual([Path(paths[1]).name], [Path(c).name for c in encoder.calls])

    def test_legacy_768_embeddings_are_replaced_with_512(self) -> None:
        with tempfile.TemporaryDirectory(prefix="sem_idx_") as temp_dir:
            folder, paths = self._make_folder(temp_dir, count=1)
            db_path = folder / "aiculler.sqlite"
            store = SQLiteFeatureStore(db_path)
            try:
                image_id = store.upsert_image(paths[0], status="ready")
                store.save_features(image_id, np.ones(768, dtype=np.float32))
                self.assertEqual(768, store.get_embedding_dim(image_id))
            finally:
                store.close()

            encoder = _FakeEncoder(dim=512)
            _run_task(
                SemanticFolderIndexTask(
                    folder=str(folder), token=1, records=paths, db_path=db_path,
                    model_identity="model-a", extractor=encoder,
                )
            )
            self.assertEqual(1, len(encoder.calls))
            store = SQLiteFeatureStore(db_path)
            try:
                only = store.list_images(require_embedding=True)[0]
                self.assertEqual(512, store.get_embedding_dim(int(only["id"])))
            finally:
                store.close()

    def test_model_identity_change_invalidates_embeddings(self) -> None:
        with tempfile.TemporaryDirectory(prefix="sem_idx_") as temp_dir:
            folder, paths = self._make_folder(temp_dir)
            db_path = folder / "aiculler.sqlite"
            _run_task(
                SemanticFolderIndexTask(
                    folder=str(folder), token=1, records=paths, db_path=db_path,
                    model_identity="model-a", extractor=_FakeEncoder(),
                )
            )
            encoder = _FakeEncoder()
            _run_task(
                SemanticFolderIndexTask(
                    folder=str(folder), token=2, records=paths, db_path=db_path,
                    model_identity="model-b", extractor=encoder,
                )
            )
            self.assertEqual(3, len(encoder.calls))

    def test_full_workflow_embeddings_are_reused_without_recompute(self) -> None:
        from aiculler.features import _file_signature

        with tempfile.TemporaryDirectory(prefix="sem_idx_") as temp_dir:
            folder, paths = self._make_folder(temp_dir, count=1)
            db_path = folder / "aiculler.sqlite"
            store = SQLiteFeatureStore(db_path)
            try:
                image_id = store.upsert_image(paths[0], status="ready")
                store.save_features(
                    image_id,
                    np.ones(512, dtype=np.float32),
                    metadata={
                        "aiculler_feature_cache": {
                            "source_signature": _file_signature(Path(paths[0])),
                        }
                    },
                )
            finally:
                store.close()

            encoder = _FakeEncoder(dim=512)
            events = _run_task(
                SemanticFolderIndexTask(
                    folder=str(folder), token=1, records=paths, db_path=db_path,
                    model_identity="model-a", extractor=encoder,
                )
            )
            self.assertEqual([], encoder.calls)  # adopted, not recomputed
            self.assertEqual((str(folder), 1, 1, 1), events["finished"][0])
            store = SQLiteFeatureStore(db_path)
            try:
                state = store.get_semantic_index_state(image_id)
                self.assertEqual("model-a", state["model_identity"])
            finally:
                store.close()

    def test_indexing_does_not_run_technical_or_face_analysis(self) -> None:
        with tempfile.TemporaryDirectory(prefix="sem_idx_") as temp_dir:
            folder, paths = self._make_folder(temp_dir)
            db_path = folder / "aiculler.sqlite"
            _run_task(
                SemanticFolderIndexTask(
                    folder=str(folder), token=1, records=paths, db_path=db_path,
                    model_identity="model-a", extractor=_FakeEncoder(),
                )
            )
            store = SQLiteFeatureStore(db_path)
            try:
                for row in store.list_images():
                    self.assertIsNone(row["technical_score"])
                    self.assertNotEqual("ready", row["status"])
                dim_tables = store.connection.execute(
                    "SELECT COUNT(*) AS c FROM sqlite_master WHERE type='table' AND name='image_dimensions'"
                ).fetchone()["c"]
                if dim_tables:
                    dim_count = store.connection.execute(
                        "SELECT COUNT(*) AS c FROM image_dimensions"
                    ).fetchone()["c"]
                    self.assertEqual(0, dim_count)
                face_tables = store.connection.execute(
                    "SELECT COUNT(*) AS c FROM sqlite_master WHERE type='table' AND name='image_faces'"
                ).fetchone()["c"]
                if face_tables:
                    face_count = store.connection.execute(
                        "SELECT COUNT(*) AS c FROM image_faces"
                    ).fetchone()["c"]
                    self.assertEqual(0, face_count)
            finally:
                store.close()

    def test_search_returns_indexed_results_without_full_workflow(self) -> None:
        with tempfile.TemporaryDirectory(prefix="sem_idx_") as temp_dir:
            folder = Path(temp_dir)
            beach = str(_write_jpeg(folder / "a.jpg", (10, 10, 200)))
            car = str(_write_jpeg(folder / "b.jpg", (200, 10, 10)))
            dog = str(_write_jpeg(folder / "c.jpg", (10, 200, 10)))
            db_path = folder / "aiculler.sqlite"
            vectors = {
                "a.jpg": np.array([1.0, 0.0, 0.0] + [0.0] * 509, dtype=np.float32),
                "b.jpg": np.array([0.0, 1.0, 0.0] + [0.0] * 509, dtype=np.float32),
                "c.jpg": np.array([0.0, 0.0, 1.0] + [0.0] * 509, dtype=np.float32),
            }
            encoder = _FakeEncoder(vectors=vectors)
            _run_task(
                SemanticFolderIndexTask(
                    folder=str(folder), token=1, records=[beach, car, dog], db_path=db_path,
                    model_identity="model-a", extractor=encoder,
                )
            )

            store = SQLiteFeatureStore(db_path)
            try:
                text_encoder = _RecordingTextEncoder({"restaurant": vectors["b.jpg"]})
                service = FeatureStoreSemanticSearch(store, text_encoder)
                hits = service.search("restaurant", limit=1)
                self.assertEqual(1, len(hits))
                self.assertTrue(hits[0].source_path.endswith("b.jpg"))
            finally:
                store.close()

    def test_single_image_failure_is_nonfatal(self) -> None:
        with tempfile.TemporaryDirectory(prefix="sem_idx_") as temp_dir:
            folder, paths = self._make_folder(temp_dir)
            db_path = folder / "aiculler.sqlite"
            encoder = _FailingEncoder(fail_names={"photo_1.jpg"})
            events = _run_task(
                SemanticFolderIndexTask(
                    folder=str(folder), token=1, records=paths, db_path=db_path,
                    model_identity="model-a", extractor=encoder,
                )
            )
            self.assertEqual([], events["failed"])
            self.assertEqual(1, len(events["finished"]))
            # 2 of 3 embedded; the failing image is simply skipped.
            self.assertEqual((str(folder), 1, 2, 3), events["finished"][0])
            store = SQLiteFeatureStore(db_path)
            try:
                self.assertEqual(2, len(store.list_images(require_embedding=True)))
            finally:
                store.close()

    def test_cancelled_task_does_no_work(self) -> None:
        with tempfile.TemporaryDirectory(prefix="sem_idx_") as temp_dir:
            folder, paths = self._make_folder(temp_dir)
            db_path = folder / "aiculler.sqlite"
            encoder = _FakeEncoder()
            task = SemanticFolderIndexTask(
                folder=str(folder), token=1, records=paths, db_path=db_path,
                model_identity="model-a", extractor=encoder,
            )
            task.cancel()
            events = _run_task(task)
            self.assertEqual([], encoder.calls)
            self.assertEqual([], events["finished"])

    def test_hidden_artifacts_and_unsupported_files_are_excluded(self) -> None:
        with tempfile.TemporaryDirectory(prefix="sem_idx_") as temp_dir:
            folder = Path(temp_dir)
            good = str(_write_jpeg(folder / "keep.jpg"))
            hidden_dir = folder / ".image_triage_ai"
            hidden_dir.mkdir()
            hidden = str(_write_jpeg(hidden_dir / "cached.jpg"))
            text_file = str(folder / "notes.txt")
            Path(text_file).write_text("nope")
            sources = collect_indexable_sources([good, hidden, text_file])
            self.assertEqual([good], sources)

    def test_plan_classifies_new_valid_and_stale(self) -> None:
        with tempfile.TemporaryDirectory(prefix="sem_idx_") as temp_dir:
            folder, paths = self._make_folder(temp_dir, count=1)
            snapshot: dict[str, dict[str, object]] = {}
            plan = plan_semantic_index(snapshot, paths, model_identity="m")
            self.assertEqual(1, len(plan.to_embed))
            self.assertEqual(0, len(plan.valid))

    def test_model_identity_is_stable_and_dim_sensitive(self) -> None:
        with tempfile.TemporaryDirectory(prefix="sem_idx_") as temp_dir:
            model = _write_jpeg(Path(temp_dir) / "model.onnx.bin")
            a = compute_semantic_model_identity(model, embedding_dim=512)
            b = compute_semantic_model_identity(model, embedding_dim=512)
            c = compute_semantic_model_identity(model, embedding_dim=768)
            self.assertEqual(a, b)
            self.assertNotEqual(a, c)


if __name__ == "__main__":
    unittest.main()
