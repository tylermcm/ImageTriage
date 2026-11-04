"""Import native search/people arrays into host stores without changing verdicts."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np


def import_native_index(path: Path, db_path: Path, *, semantic_identity: str) -> dict:
    # These are the host's legacy storage APIs, never the engine's ScoreCache.
    from aiculler.features import PreviewExtractor, _file_signature
    from aiculler.storage import SQLiteFeatureStore
    from .quality.face import FaceRecord
    from .quality.store import upsert_faces
    from .people_search import cluster_face_identities, remember_named_faces
    from .cascade.faces import FACE_MODEL_TAG
    from .cascade.rankers.bundles import FACES_IDENTITY_REVISION
    from .cascade.blink import BLINK_THRESHOLD
    from .semantic_plan import SEMANTIC_INDEX_SCHEMA_VERSION

    with np.load(path, allow_pickle=False) as archive:
        arrays = {key: archive[key] for key in archive.files}
    meta = json.loads(str(arrays["meta"]))
    if meta.get("schema") != "aiculler-index/1":
        raise ValueError("Unsupported native index schema")
    if semantic_identity.startswith("{"):
        expected_revision = json.loads(semantic_identity).get("revision")
        if meta.get("search_model") and expected_revision not in str(meta["search_model"]):
            raise ValueError("Native search model differs from the host search model")
    face_identity = FACE_MODEL_TAG if FACES_IDENTITY_REVISION in str(meta.get("people_model", "")) else "native:" + str(meta.get("people_model", "unknown"))
    files = arrays["files"].tolist()
    by_capture = {}
    for filename, capture in zip(files, arrays["file_capture"].tolist()):
        by_capture.setdefault(capture, []).append(filename)
    vectors = arrays.get("clip")
    if vectors is not None:
        if vectors.ndim != 2 or not np.isfinite(vectors).all() or not np.allclose(np.linalg.norm(vectors, axis=1), 1, atol=1e-4):
            raise ValueError("Native search vectors must be finite and normalized")
    face_vectors = arrays.get("face_embedding")
    if face_vectors is not None and face_vectors.size:
        if not np.isfinite(face_vectors).all() or not np.allclose(np.linalg.norm(face_vectors, axis=1), 1, atol=1e-4):
            raise ValueError("Native face vectors must be finite and normalized")
    store = SQLiteFeatureStore(db_path)
    imported = 0
    try:
        image_ids = {filename: store.ensure_image(filename) for filename in files}
        store.connection.execute(
            "CREATE TABLE IF NOT EXISTS native_index_provenance (run TEXT PRIMARY KEY, metadata_json TEXT NOT NULL)")
        store.connection.execute(
            "INSERT OR REPLACE INTO native_index_provenance VALUES (?, ?)", (str(path.resolve()), json.dumps(meta)))
        if vectors is not None:
            for capture, vector in zip(arrays["clip_capture"].tolist(), vectors):
                for filename in by_capture.get(capture, []):
                    image_id = image_ids[filename]
                    store.save_semantic_embedding(image_id, vector, commit=False)
                    store.set_semantic_index_state(
                        image_id, model_identity=semantic_identity,
                        source_signature=json.dumps(_file_signature(Path(filename)), sort_keys=True),
                        embedding_dim=int(vector.size), schema_version=SEMANTIC_INDEX_SCHEMA_VERSION,
                        commit=False)
                    imported += 1
        if "face_capture" in arrays:
            # Run-local person numbers are kept as provenance, never host IDs.
            remember_named_faces(store.connection, "native-import")
            store.connection.execute("""CREATE TABLE IF NOT EXISTS native_person_mapping (
                run TEXT, person INTEGER, image_id INTEGER, face_index INTEGER,
                PRIMARY KEY(run, person, image_id, face_index))""")
            previews = PreviewExtractor(db_path.parent / "native_index_previews")
            faces_by_capture = {}
            for row, capture in enumerate(arrays["face_capture"].tolist()):
                faces_by_capture.setdefault(capture, []).append(row)
            for capture, names in by_capture.items():
                for filename in names:
                    rows = faces_by_capture.get(capture, [])
                    if not rows:
                        continue
                    _, (width, height) = previews.extract(filename)
                    faces = []
                    for row in rows:
                        left, top, right, bottom = arrays["face_box"][row].tolist()
                        faces.append(FaceRecord(
                            bbox=(left * width, top * height, right * width, bottom * height),
                            det_score=1, identity_embedding=tuple(arrays["face_embedding"][row].tolist()),
                            identity_model=face_identity,
                            blink=None if np.isnan(arrays["face_eyes_closed"][row]) else bool(arrays["face_eyes_closed"][row] >= BLINK_THRESHOLD)))
                    upsert_faces(store.connection, image_ids[filename], faces)
                    for face_index, row in enumerate(rows):
                        store.connection.execute("INSERT OR REPLACE INTO native_person_mapping VALUES (?, ?, ?, ?)",
                            (str(path.resolve()), int(arrays["face_person"][row]), image_ids[filename], face_index))
            cluster_face_identities(store.connection, identity_model=face_identity,
                                    threshold=float(meta["face_cluster_threshold"]))
        store.connection.commit()
    except BaseException:
        store.connection.rollback()
        raise
    finally:
        store.close()
    return {"search_files": imported, "models": meta}
