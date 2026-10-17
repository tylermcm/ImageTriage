"""Library-wide semantic index: reconciling from per-folder stores, search, coverage,
idle-yield, and the embedding worker process (with a fake extractor)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PIL import Image

from image_triage.library_index_controller import IdleGate, _ReconcileTask
from image_triage.library_semantic import (
    LibrarySemanticStore,
    LibraryVectorIndex,
    describe_coverage,
    folder_feature_db_path,
)
from image_triage.semantic_index import SemanticFolderIndexTask

REPO_ROOT = Path(__file__).resolve().parents[1]
MODEL = "model-a"


class _FakeEncoder:
    def __init__(self, vectors: dict[str, np.ndarray] | None = None) -> None:
        self.vectors = vectors or {}

    def encode_image(self, path) -> np.ndarray:
        name = Path(path).name
        if name in self.vectors:
            return np.asarray(self.vectors[name], dtype=np.float32)
        rng = np.random.default_rng(abs(hash(name)) % (2**32))
        return rng.standard_normal(512).astype(np.float32)


def _jpegs(folder: Path, names: list[str]) -> list[str]:
    folder.mkdir(parents=True, exist_ok=True)
    paths = []
    for index, name in enumerate(names):
        path = folder / name
        Image.new("RGB", (16, 12), (20 * index, 80, 120)).save(path, "JPEG")
        paths.append(str(path))
    return paths


def _embed(folder: Path, paths: list[str], *, model: str = MODEL, encoder=None) -> None:
    """Run the app's own per-folder indexer, so the store has exactly the real layout."""
    db = folder_feature_db_path(folder)
    db.parent.mkdir(parents=True, exist_ok=True)
    task = SemanticFolderIndexTask(
        folder=str(folder), token=1, records=paths, db_path=db, model_identity=model, extractor=encoder or _FakeEncoder()
    )
    task.run()


class LibrarySemanticStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="library_semantic_")
        self.root = Path(self.temp.name)
        self.store = LibrarySemanticStore(self.root / "library_semantic.sqlite3")
        self.shoot = self.root / "drive" / "Moab"
        self.paths = _jpegs(self.shoot, ["a.jpg", "b.jpg", "c.jpg"])

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _sync(self, paths=None, **kwargs):
        return self.store.sync_folder(str(self.shoot), paths or self.paths, model_identity=MODEL, root="R", **kwargs)

    def test_sync_copies_embedded_files_and_counts_the_rest(self) -> None:
        _embed(self.shoot, self.paths[:2])
        result = self._sync()
        self.assertEqual(("synced", 2, 1), (result.status, result.indexed, result.needs_embedding))
        paths, matrix = self.store.load_matrix(model_identity=MODEL)
        self.assertEqual(2, len(paths))
        self.assertEqual((2, 512), matrix.shape)
        self.assertTrue(np.allclose(np.linalg.norm(matrix, axis=1), 1.0, atol=1e-5))

    def test_unchanged_folder_is_skipped_cheaply(self) -> None:
        _embed(self.shoot, self.paths)
        self._sync()
        self.assertEqual("unchanged", self._sync().status)

    def test_edited_and_deleted_files_drop_out(self) -> None:
        _embed(self.shoot, self.paths)
        self.assertEqual(3, self._sync().indexed)
        time.sleep(0.02)
        Image.new("RGB", (20, 20), (255, 0, 0)).save(self.paths[0], "JPEG")  # edited after embedding
        os.remove(self.paths[1])
        result = self._sync(paths=[self.paths[0], self.paths[2]], force=True)
        self.assertEqual((1, 1, 2), (result.indexed, result.needs_embedding, result.removed))

    def test_other_model_identity_is_not_searchable(self) -> None:
        _embed(self.shoot, self.paths, model="model-old")
        result = self._sync()
        self.assertEqual((0, 3), (result.indexed, result.needs_embedding))

    def test_offline_folder_is_kept_but_not_searched(self) -> None:
        _embed(self.shoot, self.paths)
        self._sync()
        moved = self.root / "unplugged"
        self.shoot.rename(moved)
        self.assertEqual("offline", self._sync().status)
        self.assertEqual(0, len(self.store.load_matrix(model_identity=MODEL)[0]))
        coverage = self.store.coverage({"R": 3}, model_identity=MODEL)
        self.assertEqual((3, 0, 3), (coverage.total, coverage.indexed, coverage.offline))
        moved.rename(self.shoot)  # drive back: searchable again
        self.assertEqual(3, self._sync().indexed)
        self.assertEqual(3, self.store.coverage({"R": 3}, model_identity=MODEL).indexed)

    def test_folders_removed_from_the_catalog_are_forgotten(self) -> None:
        _embed(self.shoot, self.paths)
        self._sync()
        self.assertEqual(1, self.store.forget_folders_except([]))
        self.assertEqual(0, len(self.store.load_matrix(model_identity=MODEL)[0]))

    def test_coverage_text(self) -> None:
        _embed(self.shoot, self.paths[:2])
        self._sync()
        coverage = self.store.coverage({"R": 3, "S": 1}, model_identity=MODEL)
        self.assertEqual((4, 2), (coverage.total, coverage.indexed))
        self.assertEqual("Library search: 2 of 4 photos (50%) \u2014 paused", describe_coverage(coverage, state="paused"))

    def test_reconcile_task_lists_folders_that_need_the_worker(self) -> None:
        second = self.root / "drive" / "Zion"
        second_paths = _jpegs(second, ["z.jpg"])
        _embed(self.shoot, self.paths)
        results = []
        task = _ReconcileTask(
            store_factory=lambda: self.store,
            folders={str(self.shoot): ("R", self.paths), str(second): ("R", second_paths)},
            model_identity=MODEL,
        )
        task.signals.finished.connect(results.append)
        task.run()
        self.assertEqual([str(second)], [job["folder"] for job in results[0].jobs])
        self.assertEqual((4, 3), (results[0].coverage.total, results[0].coverage.indexed))


class LibraryVectorIndexTests(unittest.TestCase):
    def setUp(self) -> None:
        basis = np.eye(512, dtype=np.float32)
        self.paths = [f"C:/p/{name}.jpg" for name in ("mtn1", "mtn2", "mtn3", "beach1", "beach2")]
        vectors = [
            basis[0] + 0.10 * basis[1],
            basis[0] + 0.20 * basis[1],
            basis[0] + 0.05 * basis[2],
            basis[3] + 0.10 * basis[1],
            basis[3],
        ]
        matrix = np.vstack(vectors)
        matrix /= np.linalg.norm(matrix, axis=1, keepdims=True)
        self.index = LibraryVectorIndex(paths=list(self.paths), matrix=matrix.astype(np.float32))

    def test_search_ranks_by_cosine_and_caches(self) -> None:
        result = self.index.search(np.eye(512, dtype=np.float32)[3])
        self.assertEqual("C:/p/beach2.jpg", result.paths[0])
        self.assertIs(result, self.index.search(np.eye(512, dtype=np.float32)[3]))

    def test_similar_to_leaves_out_the_examples(self) -> None:
        result = self.index.similar_to(["C:/p/mtn1.jpg"])
        self.assertNotIn("C:/p/mtn1.jpg", result.paths)
        self.assertEqual({"C:/p/mtn2.jpg", "C:/p/mtn3.jpg"}, set(result.paths[:2]))

    def test_negatives_push_results_away(self) -> None:
        plain = self.index.similar_to(["C:/p/mtn1.jpg"])
        steered = self.index.similar_to(["C:/p/mtn1.jpg"], negatives=["C:/p/beach1.jpg"])
        # beach1 shares the basis[1] tint with mtn1/mtn2; "less like beach1" demotes mtn2 below mtn3.
        self.assertEqual("C:/p/mtn2.jpg", plain.paths[0])
        self.assertEqual("C:/p/mtn3.jpg", steered.paths[0])
        self.assertNotIn("C:/p/beach1.jpg", steered.paths)

    def test_threshold_recut_and_within_and_missing(self) -> None:
        result = self.index.similar_to(["C:/p/mtn1.jpg", "C:/p/not-indexed.jpg"])
        self.assertEqual(("C:/p/not-indexed.jpg",), result.missing)
        high = result.cut(0.9)
        self.assertTrue(all(score >= 0.9 for _path, score in high))
        self.assertEqual(len(high), result.count_at_least(0.9))
        self.assertEqual(len(result.paths), result.count_at_least(-1.0))
        within = self.index.similar_to(["C:/p/mtn1.jpg"], within=["C:/p/beach2.jpg", "C:/p/mtn3.jpg"])
        self.assertEqual(("C:/p/mtn3.jpg", "C:/p/beach2.jpg"), within.paths)

    def test_no_indexed_examples_gives_an_empty_result(self) -> None:
        result = self.index.similar_to(["C:/elsewhere.jpg"])
        self.assertEqual((), result.paths)
        self.assertEqual(("C:/elsewhere.jpg",), result.missing)


class IdleGateTests(unittest.TestCase):
    def test_waits_for_quiet_and_respects_blockers(self) -> None:
        now = [100.0]
        gate = IdleGate(idle_seconds=20.0, clock=lambda: now[0], last_activity=100.0)
        self.assertFalse(gate.may_run(blocked=False))
        now[0] = 121.0
        self.assertTrue(gate.may_run(blocked=False))
        self.assertFalse(gate.may_run(blocked=True))
        gate.touch()
        self.assertFalse(gate.may_run(blocked=False))

    def test_os_input_idle_replaces_app_stamps(self) -> None:
        idle = [5.0]
        gate = IdleGate(idle_seconds=20.0, input_idle=lambda: idle[0])
        self.assertFalse(gate.may_run(blocked=False))
        idle[0] = 25.0
        self.assertTrue(gate.may_run(blocked=False))
        self.assertFalse(gate.may_run(blocked=True))


class WorkerProcessTests(unittest.TestCase):
    """Runs aiculler/library_index_worker.py as the app would (a separate Python), with the fake extractor."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="library_worker_")
        self.root = Path(self.temp.name)
        self.folder = self.root / "Shoot"
        self.paths = _jpegs(self.folder, ["one.jpg", "two.jpg", "three.jpg"])
        self.jobs = self.root / "jobs.json"
        self.control = self.root / "control.txt"
        self.jobs.write_text(
            json.dumps(
                {
                    "model_identity": MODEL,
                    "embedding_dim": 512,
                    "folders": [
                        {"folder": str(self.folder), "db": str(folder_feature_db_path(self.folder)), "sources": self.paths},
                        {"folder": str(self.root / "unplugged"), "db": str(self.root / "x.sqlite"), "sources": []},
                    ],
                }
            ),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _start(self) -> subprocess.Popen:
        env = dict(os.environ, PYTHONPATH=str(REPO_ROOT), IMAGE_TRIAGE_LIBRARY_INDEX_FAKE_EXTRACTOR="1", PYTHONUNBUFFERED="1")
        return subprocess.Popen(
            [sys.executable, str(REPO_ROOT / "aiculler" / "library_index_worker.py"), "--jobs", str(self.jobs), "--control", str(self.control)],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=env,
            cwd=str(REPO_ROOT),
        )

    @staticmethod
    def _events(output: str) -> list[dict]:
        return [json.loads(line[len("LIBRARY_INDEX "):]) for line in output.splitlines() if line.startswith("LIBRARY_INDEX ")]

    def test_worker_fills_the_folder_store_then_the_library_index_sees_it(self) -> None:
        self.control.write_text("run", encoding="utf-8")
        output, _ = self._start().communicate(timeout=120)
        events = self._events(output)
        kinds = [event["event"] for event in events]
        self.assertIn("folder_done", kinds, output)
        self.assertIn("folder_offline", kinds)
        self.assertEqual("finished", kinds[-1])
        done = next(event for event in events if event["event"] == "folder_done")
        self.assertEqual((3, 3, 0), (done["done"], done["total"], done["failed"]))

        store = LibrarySemanticStore(self.root / "library_semantic.sqlite3")
        result = store.sync_folder(str(self.folder), self.paths, model_identity=MODEL)
        self.assertEqual((3, 0), (result.indexed, result.needs_embedding))

    def test_worker_pauses_and_stops_on_the_control_file(self) -> None:
        self.control.write_text("pause", encoding="utf-8")
        process = self._start()
        deadline = time.time() + 60
        saw_paused = False
        lines = []
        while time.time() < deadline:
            line = process.stdout.readline()
            if not line:
                break
            lines.append(line)
            if '"paused"' in line:
                saw_paused = True
                self.control.write_text("stop", encoding="utf-8")
        process.wait(timeout=30)
        self.assertTrue(saw_paused, "".join(lines))
        self.assertIn('"stopped"', "".join(lines))
        # Stopped while paused before the first image: nothing was embedded.
        store = LibrarySemanticStore(self.root / "library_semantic.sqlite3")
        self.assertEqual(0, store.sync_folder(str(self.folder), self.paths, model_identity=MODEL).indexed)


if __name__ == "__main__":
    unittest.main()
