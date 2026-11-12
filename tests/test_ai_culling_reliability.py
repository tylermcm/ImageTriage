"""The culling back end says what it did and survives the awkward cases: tied scores, a single ranker, a missing model, a read-only folder,
one bad frame, a thousand faces of one person, two writers of one cache, and a helper process that stops answering. No models, no UI."""

from __future__ import annotations

import io
import json
import subprocess
import sys
import threading
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

from aiculler.storage import SQLiteFeatureStore
from image_triage.ai_results import _build_folder_percentile_map, normalized_path_key
from image_triage.ai_workflow import build_ai_workflow_paths
from image_triage.cascade import launch
from image_triage.cascade.faces import FaceCache, FaceFinding
from image_triage.cascade.model import CascadeSettings, FrameFeatures, FrameRole
from image_triage.cascade.pipeline import run_cascade
from image_triage.cascade.rankers import worker
from image_triage.cascade.task import CascadeRunTask, Scorers
from image_triage.models import ImageRecord
from image_triage.people_search import (
    FaceIdentity,
    PersonCluster,
    _group_faces,
    _match_previous_clusters,
    assign_person_name,
    cluster_face_identities,
    image_ids_matching_people,
)
from image_triage.quality.face import FaceRecord
from image_triage.quality.store import upsert_faces


# ---- the "AI score" ---------------------------------------------------------------------------------------------------------------


def _scored(name: str, score: float):
    return SimpleNamespace(score=score, file_name=name, file_path=f"/shoot/{name}")


def test_photos_with_the_same_score_share_one_percentile():
    keepers = tuple(_scored(f"keep_{n}.jpg", 0.799) for n in range(9))
    percentiles = _build_folder_percentile_map({"a": keepers})
    values = {percentiles[normalized_path_key(item.file_path)] for item in keepers}
    assert values == {50.0}  # nine identical keepers: not 100 down to 0 in the order of their names


def test_a_tie_sits_between_the_better_and_the_worse_photos():
    groups = {"a": (_scored("pick.jpg", 0.999),), "b": (_scored("b.jpg", 0.799), _scored("a.jpg", 0.799)), "c": (_scored("flagged.jpg", 0.099),)}
    percentiles = _build_folder_percentile_map(groups)
    at = lambda name: percentiles[normalized_path_key(f"/shoot/{name}")]
    assert at("pick.jpg") == 100.0 and at("flagged.jpg") == 0.0
    assert at("a.jpg") == at("b.jpg") == 50.0


# ---- ranking with one ranker -------------------------------------------------------------------------------------------------------


def _frame(key: str, phash: int) -> FrameFeatures:
    return FrameFeatures(key, phash, 100.0, 0.01, 0.01)


def test_the_quality_ranker_alone_orders_a_stack_of_two():
    # twelve bits apart: one stack (under the split distance), not near-duplicates (over the duplicate distance)
    frames = [_frame("a", 0), _frame("b", 0b111111111111)]
    asked: list[list[str]] = []

    def quality(keys):
        asked.append(list(keys))
        return {"a": 0.2, "b": 0.9}

    result = run_cascade(frames, CascadeSettings(), maniqa=quality)
    assert asked == [["a", "b"]]
    by_key = result.decisions_by_key
    assert by_key["b"].role is FrameRole.FINAL and by_key["b"].rank == 1
    assert by_key["a"].role is FrameRole.FINAL and by_key["a"].rank == 2  # ordered, never cut: three or fewer are all kept


def test_with_both_rankers_a_small_stack_still_goes_straight_to_the_appeal_ranker():
    frames = [_frame("a", 0), _frame("b", 0b111111111111)]
    quality_calls: list[list[str]] = []
    result = run_cascade(frames, CascadeSettings(), maniqa=lambda keys: quality_calls.append(list(keys)) or {k: 0.5 for k in keys},
                         aesthetic=lambda keys: {"a": 0.9, "b": 0.1})
    assert quality_calls == [] and result.decisions_by_key["a"].rank == 1


# ---- the graphics card build on Linux -----------------------------------------------------------------------------------------------


def test_the_gpu_profile_is_recognised_by_its_linux_libraries(tmp_path):
    from image_triage.ai_runtime_packages import _torch_cuda_binaries_present

    library = tmp_path / "torch" / "lib"
    library.mkdir(parents=True)
    assert not _torch_cuda_binaries_present(tmp_path)
    (library / "libtorch_cuda.so").write_bytes(b"")
    assert _torch_cuda_binaries_present(tmp_path)


# ---- one bad frame ----------------------------------------------------------------------------------------------------------------


class _MoodyEngine:
    def __init__(self, bad: set[str]) -> None:
        self.bad = bad

    def score(self, path: str) -> float:
        if path in self.bad:
            raise RuntimeError(f"tensor shape mismatch for {path}")
        return 0.5


def _serve(engine, paths):
    events: list[dict] = []
    request = {"op": "score", "id": 1, "items": [{"key": p, "path": p} for p in paths]}
    worker.serve(engine, io.StringIO(json.dumps(request) + "\n"), events.append)
    return events


def test_one_frame_the_engine_chokes_on_does_not_end_the_worker():
    events = _serve(_MoodyEngine({"b"}), ["a", "b", "c"])
    assert [(e["event"], e.get("key")) for e in events] == [("score", "a"), ("frame_error", "b"), ("score", "c"), ("done", None)]
    assert "tensor shape mismatch" in events[1]["message"]


def test_an_engine_that_fails_on_every_frame_still_stops_with_its_error():
    with pytest.raises(RuntimeError, match="tensor shape mismatch"):
        _serve(_MoodyEngine({"a", "b", "c", "d"}), ["a", "b", "c", "d"])


# ---- nothing is downloaded from inside a run, and the run says what it left out ------------------------------------------------------


def test_the_run_never_downloads_a_model_and_skips_people_when_the_recognizer_is_missing(monkeypatch):
    from image_triage.cascade import rankers

    made: dict[str, dict] = {}
    for name in ("maniqa_ranker", "qrealign_ranker", "blink_ranker", "faces_ranker"):
        monkeypatch.setattr(rankers, name, lambda _name=name, **kwargs: made.setdefault(_name, kwargs) and SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(launch, "face_recognizer_missing", lambda: "the face recognizer (about 166 MB) is not installed.")
    scorers = launch.default_scorer_factory("cpu")(lambda: False, lambda done, total, label: None)
    assert set(made) == {"maniqa_ranker", "qrealign_ranker", "blink_ranker"}  # no face finder: its recognizer is not there
    assert all(kwargs["download"] is False for kwargs in made.values())
    assert scorers.faces is None and scorers.notes == ("People were not updated: the face recognizer (about 166 MB) is not installed.",)

    made.clear()
    monkeypatch.setattr(launch, "face_recognizer_missing", lambda: "")
    scorers = launch.default_scorer_factory("cpu", rankers=False)(lambda: False, lambda done, total, label: None)
    assert made["faces_ranker"]["download"] is False and scorers.notes == ()


def _photo(path: Path, shade: int) -> ImageRecord:
    Image.new("RGB", (64, 48), (shade, shade, shade)).save(path, "JPEG")
    stat = path.stat()
    return ImageRecord(path=str(path), name=path.name, size=stat.st_size, modified_ns=stat.st_mtime_ns)


def _run(task: CascadeRunTask) -> dict[str, list[tuple]]:
    events: dict[str, list[tuple]] = {name: [] for name in ("stage", "finished", "failed")}
    for name in events:
        getattr(task.signals, name).connect(lambda *args, _name=name: events[_name].append(args))
    task.setAutoDelete(False)
    task.run()
    return events


def test_what_a_run_skipped_and_what_ranked_it_are_kept_on_the_task(tmp_path):
    records = [_photo(tmp_path / "a.jpg", 90), _photo(tmp_path / "b.jpg", 160)]
    factory = lambda cancel, progress: Scorers(maniqa=lambda keys: {k: 0.5 for k in keys}, names=("maniqa",), notes=("People were not updated: not installed.",))
    task = CascadeRunTask(folder=tmp_path, records=records, paths=build_ai_workflow_paths(tmp_path), scorer_factory=factory)
    events = _run(task)
    assert len(events["finished"]) == 1 and not events["failed"]
    assert task.notes == ("People were not updated: not installed.",) and task.ranked_with == ("maniqa",)
    summary = json.loads((build_ai_workflow_paths(tmp_path).report_dir / "ranked_export_summary.json").read_text(encoding="utf-8"))
    assert summary["note"] == "People were not updated: not installed."

    def broken(cancel, progress):
        raise RuntimeError("the AI runtime is not installed")

    task = CascadeRunTask(folder=tmp_path, records=records, paths=build_ai_workflow_paths(tmp_path), scorer_factory=broken)
    _run(task)
    assert task.ranked_with == () and task.notes == ("Ranking skipped: the AI runtime is not installed",)


def test_the_device_a_ranker_really_ran_on_is_kept(tmp_path):
    records = [_photo(tmp_path / "a.jpg", 90)]

    class Ranker:
        active_device = "cpu"

        def __call__(self, keys):
            return {k: 0.5 for k in keys}

    task = CascadeRunTask(folder=tmp_path, records=records, paths=build_ai_workflow_paths(tmp_path),
                          scorer_factory=lambda cancel, progress: Scorers(maniqa=Ranker(), names=("maniqa",)))
    _run(task)
    assert task.ranking_device == "cpu"


def test_a_folder_that_cannot_hold_the_results_fails_before_anything_is_read(tmp_path):
    records = [_photo(tmp_path / "a.jpg", 90)]
    blocker = tmp_path / "not-a-folder"
    blocker.write_text("a file where the results folder would go")
    paths = replace(build_ai_workflow_paths(tmp_path), hidden_root=blocker, artifacts_dir=blocker / "artifacts", report_dir=blocker / "report")
    events = _run(CascadeRunTask(folder=tmp_path, records=records, paths=paths))
    assert not events["finished"] and len(events["failed"]) == 1
    assert "cannot be saved" in events["failed"][0][1] and "read-only" in events["failed"][0][1]
    assert events["stage"] == []  # it stopped before the first stage: no previews were read for nothing


# ---- people ---------------------------------------------------------------------------------------------------------------------


def _one_person(count: int, dim: int = 512) -> list[np.ndarray]:
    rng = np.random.default_rng(7)
    centre = rng.normal(size=dim)
    return [centre + 0.05 * rng.normal(size=dim) for _ in range(count)]


def test_a_thousand_faces_of_one_person_cluster_in_well_under_a_second(tmp_path):
    store = SQLiteFeatureStore(tmp_path / "features.sqlite")
    try:
        for index, vector in enumerate(_one_person(1000)):
            image_id = store.upsert_image(tmp_path / f"p{index:04d}.jpg", status="ready")
            upsert_faces(store.connection, image_id, [FaceRecord((0, 0, 10, 10), 0.9, identity_embedding=tuple(float(v) for v in vector))])
        store.connection.commit()
        started = time.perf_counter()
        clusters = cluster_face_identities(store.connection, threshold=0.4)
        elapsed = time.perf_counter() - started
    finally:
        store.close()
    assert [cluster.face_count for cluster in clusters] == [1000]
    assert elapsed < 1.0, f"clustering 1,000 faces took {elapsed:.2f} s"


def test_the_running_centre_is_the_mean_of_the_group():
    vectors = _one_person(40, dim=16)
    faces = [FaceIdentity(image_id=n, face_index=0, det_score=0.9, embedding=tuple(float(v) for v in vector)) for n, vector in enumerate(vectors)]
    groups, centres = _group_faces(faces, 0.4)
    assert [len(group) for group in groups] == [40]
    unit = np.vstack([v / np.linalg.norm(v) for v in vectors]).astype(np.float32)
    expected = unit.mean(axis=0)
    np.testing.assert_allclose(centres[0], expected / np.linalg.norm(expected), atol=1e-5)


def _direction(degrees: float) -> tuple[float, float]:
    return (float(np.cos(np.radians(degrees))), float(np.sin(np.radians(degrees))))


def test_a_saved_name_goes_to_the_group_that_matches_it_best():
    ann = PersonCluster(cluster_id=7, name="Ann", face_count=3, centroid=_direction(0))
    lookalike, same_person = np.asarray(_direction(40), dtype=np.float32), np.asarray(_direction(-10), dtype=np.float32)
    matched = _match_previous_clusters([ann], [lookalike, same_person], threshold=0.7)
    assert matched == {1: ann}  # both are over the threshold; the first one in line is not the closer one


def test_a_name_follows_its_person_through_a_rebuild(tmp_path):
    store = SQLiteFeatureStore(tmp_path / "features.sqlite")
    try:
        lookalike = store.upsert_image(tmp_path / "a-lookalike.jpg", status="ready")
        same_person = store.upsert_image(tmp_path / "b-ann-again.jpg", status="ready")
        first = store.upsert_image(tmp_path / "c-ann.jpg", status="ready")
        upsert_faces(store.connection, first, [FaceRecord((0, 0, 10, 10), 0.9, identity_embedding=_direction(0))])
        store.connection.commit()
        (cluster,) = cluster_face_identities(store.connection, threshold=0.7)
        assign_person_name(store.connection, cluster.cluster_id, "Ann")
        # the photo she was named on is gone; two new photos are both close enough to her old cluster, and the look-alike comes first
        upsert_faces(store.connection, first, [])
        upsert_faces(store.connection, lookalike, [FaceRecord((0, 0, 10, 10), 0.9, identity_embedding=_direction(40))])
        upsert_faces(store.connection, same_person, [FaceRecord((0, 0, 10, 10), 0.9, identity_embedding=_direction(-10))])
        store.connection.commit()
        rebuilt = cluster_face_identities(store.connection, threshold=0.7)
        assert len(rebuilt) == 2
        assert image_ids_matching_people(store.connection, ("Ann",)) == {same_person}
    finally:
        store.close()


# ---- the shared face cache --------------------------------------------------------------------------------------------------------


def test_two_writers_of_the_face_cache_keep_each_others_entries(tmp_path):
    one, two = tmp_path / "one.jpg", tmp_path / "two.jpg"
    one.write_bytes(b"1")
    two.write_bytes(b"22")
    store = tmp_path / "faces_cache.json"
    cull, people = FaceCache(store), FaceCache(store)  # both read the (empty) file before either has saved
    finding = FaceFinding(box=(0.1, 0.1, 0.2, 0.2), area=0.01, embedding=(1.0, 0.0))
    cull.put(str(one), [finding])
    people.put(str(two), [])
    cull.save()
    people.save()
    merged = FaceCache(store)
    assert merged.get(str(one)) == [finding] and merged.get(str(two)) == []
    assert [p.name for p in tmp_path.iterdir() if p.name.endswith(".tmp")] == []


def test_saves_from_two_threads_never_share_a_temporary_file(tmp_path):
    store = tmp_path / "faces_cache.json"
    photos = []
    for index in range(8):
        photo = tmp_path / f"{index}.jpg"
        photo.write_bytes(bytes([index]) * (index + 1))
        photos.append(photo)
    failures: list[BaseException] = []

    def writer(mine: list[Path]) -> None:
        try:
            for photo in mine:
                cache = FaceCache(store)
                cache.put(str(photo), [])
                cache.save()
        except BaseException as error:  # noqa: BLE001 - reported by the assertion below
            failures.append(error)

    threads = [threading.Thread(target=writer, args=(photos[n::2],)) for n in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert failures == []
    data = json.loads(store.read_text(encoding="utf-8"))
    assert set(data["entries"]) == {str(photo) for photo in photos}
    assert [p.name for p in tmp_path.iterdir() if p.name.endswith(".tmp")] == []


# ---- a helper that stops answering ------------------------------------------------------------------------------------------------


def _hung_host() -> "subprocess.Popen[str]":
    """A process that takes a command and then never answers."""
    return subprocess.Popen([sys.executable, "-c", "import sys, time; sys.stdin.readline(); time.sleep(120)"],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, encoding="utf-8", bufsize=1)


def test_a_mask_host_that_stops_answering_is_ended_and_the_request_fails():
    from image_triage.mask_engine_service import MaskEngineService, MaskEngineTimeout

    service = MaskEngineService(idle_timeout_seconds=3600)
    service.command_timeout_seconds = 0.5
    process = _hung_host()
    service._process = process
    try:
        started = time.monotonic()
        with pytest.raises(MaskEngineTimeout):
            service._command_locked({"command": "warm-imports"})
        assert time.monotonic() - started < 10
        process.wait(timeout=5)
        assert process.poll() is not None and service._process is None  # ended, and not started again to hang a second time
    finally:
        if process.poll() is None:
            process.kill()


def test_shutdown_does_not_wait_behind_a_request_that_hangs():
    from image_triage.mask_engine_service import MaskEngineService, MaskEngineTimeout

    service = MaskEngineService(idle_timeout_seconds=3600)
    service.command_timeout_seconds = 60.0
    service.shutdown_timeout_seconds = 0.3
    process = _hung_host()
    service._process = process
    outcome: list[BaseException] = []

    def request() -> None:
        try:
            with service._lock:
                service._send_command_locked({"command": "infer"})
        except BaseException as error:  # noqa: BLE001
            outcome.append(error)

    worker_thread = threading.Thread(target=request, daemon=True)
    worker_thread.start()
    try:
        time.sleep(0.3)  # the request now holds the lock, waiting for an answer that will not come
        started = time.monotonic()
        service.shutdown()
        assert time.monotonic() - started < 5
        worker_thread.join(timeout=10)
        assert not worker_thread.is_alive() and process.poll() is not None
        assert outcome and not isinstance(outcome[0], MaskEngineTimeout)  # the request saw its host go away
    finally:
        if process.poll() is None:
            process.kill()
