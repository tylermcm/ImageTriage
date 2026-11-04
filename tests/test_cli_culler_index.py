import json

import numpy as np
from PIL import Image
import pytest

from aiculler.storage import SQLiteFeatureStore
from image_triage.cli_culler_index import import_native_index


def test_search_index_propagates_capture_to_siblings_and_retains_provenance(tmp_path):
    files = [str(tmp_path / "a.jpg"), str(tmp_path / "copy.jpg")]
    for path in files:
        Image.new("RGB", (100, 80), "gray").save(path)
    index = tmp_path / "index.npz"
    meta = {"schema": "aiculler-index/1", "search_model": "tinyclip-revision"}
    np.savez_compressed(index, meta=np.asarray(json.dumps(meta)),
                        captures=np.asarray(["capture"]), files=np.asarray(files),
                        file_capture=np.asarray([0, 0]), clip_capture=np.asarray([0]),
                        clip=np.asarray([[1, 0, 0]], dtype=np.float32))
    result = import_native_index(index, tmp_path / "host.sqlite", semantic_identity="tinyclip-test")
    assert result["search_files"] == 2
    store = SQLiteFeatureStore(tmp_path / "host.sqlite")
    try:
        for file in files:
            image = store.ensure_image(file)
            np.testing.assert_array_equal(store.get_embedding(image), [1, 0, 0])
            assert store.get_semantic_index_state(image)["model_identity"] == "tinyclip-test"
        row = store.connection.execute("SELECT metadata_json FROM native_index_provenance").fetchone()
        assert json.loads(row[0]) == meta
    finally:
        store.close()


def test_index_with_invalid_vectors_is_not_imported(tmp_path):
    index = tmp_path / "index.npz"
    np.savez(index, meta=np.asarray('{"schema":"aiculler-index/1"}'),
             files=np.asarray(["a"]), file_capture=np.asarray([0]),
             clip=np.asarray([[float("nan"), 1]]))
    with pytest.raises(ValueError, match="finite and normalized"):
        import_native_index(index, tmp_path / "host.sqlite", semantic_identity="test")
    assert not (tmp_path / "host.sqlite").exists()


def test_people_import_maps_run_ids_and_keeps_named_people(tmp_path):
    from image_triage.cascade.rankers.bundles import FACES_IDENTITY_REVISION
    photo = tmp_path / "portrait.jpg"
    Image.new("RGB", (100, 80), "gray").save(photo)
    index = tmp_path / "index.npz"
    meta = {"schema": "aiculler-index/1", "people_model": FACES_IDENTITY_REVISION,
            "face_cluster_threshold": 0.45}
    values = dict(meta=np.asarray(json.dumps(meta)), captures=np.asarray(["capture"]),
                  files=np.asarray([str(photo)]), file_capture=np.asarray([0]),
                  face_capture=np.asarray([0]), face_index=np.asarray([0]),
                  face_person=np.asarray([73]), face_box=np.asarray([[0.1, 0.2, 0.5, 0.8]]),
                  face_eyes_closed=np.asarray([np.nan]),
                  face_embedding=np.asarray([[1.0, 0.0, 0.0]]))
    np.savez_compressed(index, **values)
    db = tmp_path / "host.sqlite"
    import_native_index(index, db, semantic_identity="unused")
    store = SQLiteFeatureStore(db)
    try:
        row = store.connection.execute("SELECT x1,y1,x2,y2,cluster_id FROM image_faces").fetchone()
        np.testing.assert_allclose(tuple(row)[:4], [10, 16, 50, 64])
        cluster_id = row[4]
        store.connection.execute("UPDATE face_identity_clusters SET name='Person A' WHERE cluster_id=?", (cluster_id,))
        store.connection.commit()
        mapping = store.connection.execute("SELECT person FROM native_person_mapping").fetchone()
        assert mapping[0] == 73 and cluster_id != 73
    finally:
        store.close()
    import_native_index(index, db, semantic_identity="unused")
    store = SQLiteFeatureStore(db)
    try:
        assert store.connection.execute("SELECT name FROM face_identity_clusters WHERE cluster_id=?", (cluster_id,)).fetchone()[0] == "Person A"
    finally:
        store.close()


def test_incompatible_search_revision_is_not_labelled_as_the_host_model(tmp_path):
    index = tmp_path / "index.npz"
    np.savez(index, meta=np.asarray('{"schema":"aiculler-index/1","search_model":"different"}'))
    with pytest.raises(ValueError, match="differs from the host"):
        import_native_index(index, tmp_path / "host.sqlite",
                            semantic_identity='{"revision":"expected"}')
