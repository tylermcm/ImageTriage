"""The People index on the shared face finder: records from findings, the cache, and names surviving the change of recognizer."""

from __future__ import annotations

import numpy as np
import pytest
from PIL import Image

from aiculler.storage import SQLiteFeatureStore
from image_triage.ai_model import LEGACY_FACE_IDENTITY_MODEL, active_face_identity_model
from image_triage.cascade.faces import FACE_MODEL_TAG, FaceCache, FaceFinding
from image_triage.face_index import FACE_CLUSTER_THRESHOLD, FaceFolderIndexTask, plan_face_index
from image_triage.face_stack import FaceStackAnalyzer, face_cache_path, findings_to_records
from image_triage.people_search import (
    apply_remembered_names,
    assign_person_name,
    cluster_face_identities,
    list_person_clusters,
    remember_named_faces,
)
from image_triage.quality.face import FaceRecord
from image_triage.quality.store import upsert_faces


def test_the_active_recognizer_is_the_new_one_and_the_old_tag_is_kept_for_migration():
    assert active_face_identity_model() == FACE_MODEL_TAG == "adaface:ir50"
    assert LEGACY_FACE_IDENTITY_MODEL == "insightface:auraface" and LEGACY_FACE_IDENTITY_MODEL != FACE_MODEL_TAG


def test_findings_become_face_records_in_preview_pixels():
    open_eyes = FaceFinding(box=(0.1, 0.2, 0.3, 0.6), area=0.08, embedding=(1.0, 0.0), eyes_closed=0.1)
    blinking = FaceFinding(box=(0.5, 0.5, 0.6, 0.7), area=0.02, embedding=(0.0, 1.0), eyes_closed=0.9)
    unknown = FaceFinding(box=(0.0, 0.0, 0.1, 0.1), area=0.01, embedding=(), eyes_closed=None)
    records = findings_to_records([open_eyes, blinking, unknown], 1000, 500)
    assert records[0].bbox == pytest.approx((100, 100, 300, 300)) and records[0].blink is False
    assert records[1].blink is True and records[2].blink is None
    assert records[0].identity_model == FACE_MODEL_TAG and records[2].identity_model == ""
    assert all(record.gender is None and record.age is None for record in records)  # age and gender are not estimated


class FakeFaces:
    """A face-finder worker stand-in: ``details`` per key, ``failures`` for unreadable files."""

    def __init__(self, found=None, unreadable=()):
        self.found = found or {}
        self.unreadable = set(unreadable)
        self.calls: list[list[str]] = []
        self.details: dict = {}
        self.failures: dict = {}
        self.closed = False

    def __call__(self, keys):
        self.calls.append(list(keys))
        self.details, self.failures, scores = {}, {}, {}
        for key in keys:
            if key in self.unreadable:
                self.failures[key] = "unreadable"
                scores[key] = float("-inf")
                continue
            faces = [finding.to_json() for finding in self.found.get(key, [])]
            self.details[key] = {"faces": faces, "width": 800, "height": 600}
            scores[key] = float(len(faces))
        return scores

    def close(self):
        self.closed = True


def _photo(tmp_path, name="a.jpg", size=(400, 200)):
    path = tmp_path / name
    Image.new("RGB", size, (90, 90, 90)).save(path, "JPEG")
    return path


def test_the_analyzer_reads_a_new_file_once_and_then_answers_from_the_cache(tmp_path):
    photo = _photo(tmp_path)
    finding = FaceFinding(box=(0.25, 0.25, 0.5, 0.75), area=0.1, embedding=(1.0, 0.0))
    worker = FakeFaces({str(photo): [finding]})
    cache = FaceCache(tmp_path / "faces_cache.json")
    analyzer = FaceStackAnalyzer(cache=cache, ranker=worker)
    result = analyzer.analyze_source(str(photo), photo)
    assert result["face_count"] == 1 and result["faces"][0].bbox == pytest.approx((100, 50, 200, 150))  # fractions of the 400 x 200 preview
    again = analyzer.analyze_source(str(photo), photo)
    assert again["face_count"] == 1 and len(worker.calls) == 1 and analyzer.analysed == 1
    analyzer.close()
    assert worker.closed and FaceCache(tmp_path / "faces_cache.json").get(str(photo)) == [finding]  # saved for the next scan, and for the cascade


def test_a_file_the_worker_cannot_read_is_an_error_not_an_empty_face_list(tmp_path):
    photo = _photo(tmp_path)
    analyzer = FaceStackAnalyzer(ranker=FakeFaces(unreadable=[str(photo)]))
    with pytest.raises(OSError):
        analyzer.analyze_source(str(photo), photo)


def test_the_cache_path_sits_beside_the_other_cascade_artifacts(tmp_path):
    assert face_cache_path(tmp_path).parent == tmp_path and face_cache_path(tmp_path).suffix == ".json"


def _seed(tmp_path, names):
    store = SQLiteFeatureStore(tmp_path / "aiculler.sqlite")
    ids = []
    for name in names:
        path = _photo(tmp_path, name)
        image_id = store.ensure_image(path)
        embedding = np.zeros(512, dtype=np.float32)
        embedding[0] = 0.0  # a person-presence score of zero: only the face cache can promote these
        store.save_semantic_embedding(image_id, embedding)
        ids.append((image_id, str(path)))
    return store, ids


def test_a_cached_file_skips_the_person_pre_filter(tmp_path):
    store, ids = _seed(tmp_path, ["a.jpg", "b.jpg"])
    try:
        cache = FaceCache(tmp_path / "faces_cache.json")
        cache.put(ids[0][1], [FaceFinding(box=(0.1, 0.1, 0.2, 0.2), area=0.01, embedding=(1.0,))])
        cache.put(ids[1][1], [])  # analysed, no face
        plan = plan_face_index(store, lambda e: 0.0, model_identity=FACE_MODEL_TAG, face_cache=cache)
        assert [item.source_path for item in plan.to_embed] == [ids[0][1]] and [item.source_path for item in plan.to_mark] == [ids[1][1]]
        without = plan_face_index(store, lambda e: 0.0, model_identity=FACE_MODEL_TAG)
        assert without.to_embed == ()  # without the cache the zero person score would have rejected both
    finally:
        store.close()


# -- names across the change of recognizer -------------------------------------------------------------------------------------------------


def _old_named_face(connection, image_id, box, name, vector=(1.0, 0.0)):
    upsert_faces(connection, image_id, [FaceRecord(bbox=box, det_score=0.9, identity_embedding=vector, identity_model=LEGACY_FACE_IDENTITY_MODEL)])
    cluster_face_identities(connection, identity_model=LEGACY_FACE_IDENTITY_MODEL)
    cluster = next(c for c in list_person_clusters(connection) if not c.name)
    assign_person_name(connection, cluster.cluster_id, name)


def test_a_name_given_under_the_old_recognizer_follows_the_face_to_the_new_one(tmp_path):
    store, ids = _seed(tmp_path, ["a.jpg"])
    try:
        connection = store.connection
        image_id = ids[0][0]
        _old_named_face(connection, image_id, (100, 50, 200, 150), "Sam")
        assert remember_named_faces(connection, FACE_MODEL_TAG) == 1
        # The scan replaces the old face with a new one (different box style, slightly smaller) made by the new recognizer.
        upsert_faces(connection, image_id, [FaceRecord(bbox=(110, 60, 190, 140), det_score=1.0, identity_embedding=(0.0, 1.0), identity_model=FACE_MODEL_TAG)])
        cluster_face_identities(connection, threshold=FACE_CLUSTER_THRESHOLD, identity_model=FACE_MODEL_TAG)
        assert [c.name for c in list_person_clusters(connection)] == ["Sam"]
    finally:
        store.close()


def test_clustering_before_any_new_face_exists_does_not_lose_the_names(tmp_path):
    store, ids = _seed(tmp_path, ["a.jpg"])
    try:
        connection = store.connection
        image_id = ids[0][0]
        _old_named_face(connection, image_id, (100, 50, 200, 150), "Sam")
        # The People window opens and clusters under the new recognizer before the rescan has produced a single new face.
        cluster_face_identities(connection, identity_model=FACE_MODEL_TAG)
        assert list_person_clusters(connection) == []  # nothing to show yet...
        upsert_faces(connection, image_id, [FaceRecord(bbox=(105, 55, 195, 145), det_score=1.0, identity_embedding=(0.0, 1.0), identity_model=FACE_MODEL_TAG)])
        cluster_face_identities(connection, identity_model=FACE_MODEL_TAG)
        assert [c.name for c in list_person_clusters(connection)] == ["Sam"]  # ...but the name came back when the face did
    finally:
        store.close()


def test_a_different_face_in_the_same_photo_does_not_inherit_the_name(tmp_path):
    store, ids = _seed(tmp_path, ["a.jpg"])
    try:
        connection = store.connection
        _old_named_face(connection, ids[0][0], (100, 50, 200, 150), "Sam")
        remember_named_faces(connection, FACE_MODEL_TAG)
        upsert_faces(connection, ids[0][0], [FaceRecord(bbox=(300, 50, 380, 150), det_score=1.0, identity_embedding=(0.0, 1.0), identity_model=FACE_MODEL_TAG)])
        cluster_face_identities(connection, identity_model=FACE_MODEL_TAG)
        assert [c.name for c in list_person_clusters(connection)] == [""]
    finally:
        store.close()


def test_two_new_clusters_never_end_up_with_the_same_name(tmp_path):
    store, ids = _seed(tmp_path, ["a.jpg", "b.jpg"])
    try:
        connection = store.connection
        _old_named_face(connection, ids[0][0], (100, 50, 200, 150), "Sam")
        upsert_faces(connection, ids[1][0], [FaceRecord(bbox=(100, 50, 200, 150), det_score=0.9, identity_embedding=(1.0, 0.0), identity_model=LEGACY_FACE_IDENTITY_MODEL)])
        remember_named_faces(connection, FACE_MODEL_TAG)
        # The new recognizer splits what the old one called one person into two clusters.
        upsert_faces(connection, ids[0][0], [FaceRecord(bbox=(100, 50, 200, 150), det_score=1.0, identity_embedding=(1.0, 0.0), identity_model=FACE_MODEL_TAG)])
        upsert_faces(connection, ids[1][0], [FaceRecord(bbox=(100, 50, 200, 150), det_score=1.0, identity_embedding=(0.0, 1.0), identity_model=FACE_MODEL_TAG)])
        cluster_face_identities(connection, identity_model=FACE_MODEL_TAG)
        names = sorted(c.name for c in list_person_clusters(connection))
        assert names == ["", "Sam"]
        assert apply_remembered_names(connection, FACE_MODEL_TAG) == 0  # nothing left to hand out
    finally:
        store.close()


def test_the_index_task_with_the_stack_analyzer_carries_the_names_through_a_rescan(tmp_path):
    store, ids = _seed(tmp_path, ["a.jpg", "b.jpg"])
    try:
        for image_id, _path in ids:
            _old_named_face(store.connection, image_id, (100, 50, 200, 150), "Sam") if image_id == ids[0][0] else upsert_faces(
                store.connection, image_id, [FaceRecord(bbox=(100, 50, 200, 150), det_score=0.9, identity_embedding=(1.0, 0.0), identity_model=LEGACY_FACE_IDENTITY_MODEL)]
            )
        cluster_face_identities(store.connection, identity_model=LEGACY_FACE_IDENTITY_MODEL)
        store.connection.commit()
    finally:
        store.close()
    finding = FaceFinding(box=(0.25, 0.25, 0.5, 0.75), area=0.1, embedding=(0.0, 1.0))
    worker = FakeFaces({path: [finding] for _id, path in ids})
    task = FaceFolderIndexTask(
        folder=str(tmp_path), token=1, db_path=tmp_path / "aiculler.sqlite", person_scorer=lambda e: 1.0,
        analyzer=FaceStackAnalyzer(ranker=worker), face_cache_path=tmp_path / "faces_cache.json",
    )
    finished = []
    task.signals.finished.connect(lambda folder, token, faces, people: finished.append((faces, people)))
    task.run()
    assert finished == [(2, 1)]
    store = SQLiteFeatureStore(tmp_path / "aiculler.sqlite")
    try:
        assert [c.name for c in list_person_clusters(store.connection)] == ["Sam"]  # both photos, one person, still called Sam
    finally:
        store.close()
