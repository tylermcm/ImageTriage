from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

import numpy as np

from aiculler.storage import SQLiteFeatureStore
from image_triage.metadata import CaptureMetadata
from image_triage.people_search import (
    assign_person_name,
    cluster_face_identities,
    image_ids_matching_people,
)
from image_triage.quality.face import FaceRecord
from image_triage.quality.store import fetch_faces, upsert_faces
from image_triage.semantic_search import (
    FeatureStoreSemanticSearch,
    SearchFilters,
    SemanticVectorIndex,
    filename_match_score,
    parse_search_query,
)


class _TextEncoder:
    def __init__(self, vectors: dict[str, list[float]]) -> None:
        self.vectors = vectors

    def encode(self, prompt: str) -> np.ndarray:
        return np.asarray(self.vectors[prompt], dtype=np.float32)


class SemanticPeopleSearchTests(unittest.TestCase):
    def test_vector_index_ranks_by_cosine_and_applies_confidence_floor(self) -> None:
        index = SemanticVectorIndex(
            [1, 2, 3],
            ["beach.jpg", "car.jpg", "dog.jpg"],
            [[1.0, 0.0], [0.0, 1.0], [0.8, 0.2]],
        )

        hits = index.search([1.0, 0.0], min_confidence=0.9)

        self.assertEqual([1, 3], [hit.image_id for hit in hits])
        self.assertGreater(hits[0].confidence, hits[1].confidence)

    def test_face_identity_vectors_round_trip_through_face_store(self) -> None:
        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        upsert_faces(
            connection,
            10,
            [
                FaceRecord(
                    bbox=(1.0, 2.0, 3.0, 4.0),
                    det_score=0.91,
                    identity_embedding=(0.25, 0.75),
                    identity_model="test-face",
                )
            ],
        )

        faces = fetch_faces(connection, 10)

        self.assertEqual(1, len(faces))
        self.assertEqual((0.25, 0.75), faces[0].identity_embedding)
        self.assertEqual("test-face", faces[0].identity_model)

    def test_clusters_faces_and_preserves_assigned_name_on_rebuild(self) -> None:
        with tempfile.TemporaryDirectory(prefix="people_search_") as temp_dir:
            store = SQLiteFeatureStore(Path(temp_dir) / "features.sqlite")
            try:
                claire_a = store.upsert_image(Path(temp_dir) / "claire-a.jpg", status="ready")
                claire_b = store.upsert_image(Path(temp_dir) / "claire-b.jpg", status="ready")
                other = store.upsert_image(Path(temp_dir) / "other.jpg", status="ready")
                upsert_faces(store.connection, claire_a, [FaceRecord((0, 0, 10, 10), 0.95, identity_embedding=(1.0, 0.0))])
                upsert_faces(store.connection, claire_b, [FaceRecord((0, 0, 10, 10), 0.93, identity_embedding=(0.98, 0.05))])
                upsert_faces(store.connection, other, [FaceRecord((0, 0, 10, 10), 0.94, identity_embedding=(0.0, 1.0))])
                store.connection.commit()

                clusters = cluster_face_identities(store.connection, threshold=0.9)
                claire_cluster = max(clusters, key=lambda cluster: cluster.face_count)
                assign_person_name(store.connection, claire_cluster.cluster_id, "Claire")
                rebuilt = cluster_face_identities(store.connection, threshold=0.9)

                named = [cluster for cluster in rebuilt if cluster.name == "Claire"]
                self.assertEqual(1, len(named))
                self.assertEqual(2, named[0].face_count)
                self.assertEqual({claire_a, claire_b}, image_ids_matching_people(store.connection, ("Claire",)))
            finally:
                store.close()

    def test_parse_search_query_extracts_known_people_and_keeps_semantic_terms(self) -> None:
        parsed = parse_search_query("photos of Claire and me at a restaurant", known_people=("Claire",), self_name="Tyler")

        self.assertEqual(("Claire", "Tyler"), parsed.people)
        self.assertEqual("restaurant", parsed.semantic_text)

    def test_filename_match_score_handles_natural_terms_without_a_mode_switch(self) -> None:
        self.assertEqual(1.0, filename_match_score(r"C:\shoot\red-car-final.jpg", "red car"))
        self.assertEqual(0.0, filename_match_score(r"C:\shoot\portrait.jpg", "red car"))

    def test_feature_store_search_combines_semantic_people_and_metadata_filters(self) -> None:
        with tempfile.TemporaryDirectory(prefix="semantic_search_") as temp_dir:
            root = Path(temp_dir)
            store = SQLiteFeatureStore(root / "features.sqlite")
            try:
                beach = store.upsert_image(root / "set-a" / "claire-beach.jpg", status="ready")
                restaurant = store.upsert_image(root / "set-b" / "claire-restaurant.jpg", status="ready")
                car = store.upsert_image(root / "set-b" / "red-car.jpg", status="ready")
                store.save_features(beach, np.asarray([1.0, 0.0], dtype=np.float32), technical_score=0.5)
                store.save_features(restaurant, np.asarray([0.95, 0.05], dtype=np.float32), technical_score=0.5)
                store.save_features(car, np.asarray([0.0, 1.0], dtype=np.float32), technical_score=0.5)
                upsert_faces(store.connection, beach, [FaceRecord((0, 0, 10, 10), 0.95, identity_embedding=(1.0, 0.0))])
                upsert_faces(store.connection, restaurant, [FaceRecord((0, 0, 10, 10), 0.94, identity_embedding=(1.0, 0.0))])
                upsert_faces(store.connection, car, [FaceRecord((0, 0, 10, 10), 0.93, identity_embedding=(0.0, 1.0))])
                store.connection.commit()
                claire_cluster = cluster_face_identities(store.connection, threshold=0.9)[0]
                assign_person_name(store.connection, claire_cluster.cluster_id, "Claire")

                service = FeatureStoreSemanticSearch(store, _TextEncoder({"restaurant": [1.0, 0.0]}))
                metadata_by_path = {
                    str(root / "set-a" / "claire-beach.jpg"): CaptureMetadata(
                        path=str(root / "set-a" / "claire-beach.jpg"),
                        camera="Canon R5",
                        lens="RF 50mm",
                        captured_at_value=datetime(2026, 5, 1, 10, 0),
                    ),
                    str(root / "set-b" / "claire-restaurant.jpg"): CaptureMetadata(
                        path=str(root / "set-b" / "claire-restaurant.jpg"),
                        camera="Canon R5",
                        lens="RF 85mm",
                        captured_at_value=datetime(2026, 6, 2, 20, 0),
                    ),
                    str(root / "set-b" / "red-car.jpg"): CaptureMetadata(
                        path=str(root / "set-b" / "red-car.jpg"),
                        camera="Sony A7",
                        lens="GM 35mm",
                        captured_at_value=datetime(2026, 6, 3, 8, 0),
                    ),
                }

                hits = service.search(
                    "Claire at a restaurant",
                    known_people=("Claire",),
                    filters=SearchFilters(
                        min_confidence=0.9,
                        captured_after=date(2026, 6, 1),
                        camera_text="Canon",
                        lens_text="85",
                        min_rating=4,
                        folder_text="set-b",
                    ),
                    metadata_by_path=metadata_by_path,
                    rating_by_path={str(root / "set-b" / "claire-restaurant.jpg"): 5},
                )

                self.assertEqual([restaurant], [hit.image_id for hit in hits])
                self.assertEqual(("Claire",), hits[0].people)
                self.assertEqual(("filename", "people", "semantic"), hits[0].matched_by)
            finally:
                store.close()

    def test_feature_store_search_merges_filename_and_semantic_hits_natively(self) -> None:
        with tempfile.TemporaryDirectory(prefix="unified_search_") as temp_dir:
            root = Path(temp_dir)
            store = SQLiteFeatureStore(root / "features.sqlite")
            try:
                filename_hit = store.upsert_image(root / "red-car-final.jpg", status="ready")
                semantic_hit = store.upsert_image(root / "sports-field.jpg", status="ready")
                store.save_features(filename_hit, np.asarray([0.0, 1.0], dtype=np.float32), technical_score=0.5)
                store.save_features(semantic_hit, np.asarray([1.0, 0.0], dtype=np.float32), technical_score=0.5)

                service = FeatureStoreSemanticSearch(store, _TextEncoder({"red car": [1.0, 0.0]}))
                hits = service.search("red car", filters=SearchFilters(min_confidence=0.8))

                self.assertEqual([filename_hit, semantic_hit], [hit.image_id for hit in hits])
                self.assertEqual(("filename",), hits[0].matched_by)
                self.assertEqual(("semantic",), hits[1].matched_by)
            finally:
                store.close()

    def test_people_only_search_does_not_require_text_encoder(self) -> None:
        with tempfile.TemporaryDirectory(prefix="people_only_search_") as temp_dir:
            root = Path(temp_dir)
            store = SQLiteFeatureStore(root / "features.sqlite")
            try:
                claire = store.upsert_image(root / "claire.jpg", status="ready")
                other = store.upsert_image(root / "other.jpg", status="ready")
                store.save_features(claire, np.asarray([1.0, 0.0], dtype=np.float32), technical_score=0.5)
                store.save_features(other, np.asarray([0.0, 1.0], dtype=np.float32), technical_score=0.5)
                upsert_faces(store.connection, claire, [FaceRecord((0, 0, 10, 10), 0.95, identity_embedding=(1.0, 0.0))])
                upsert_faces(store.connection, other, [FaceRecord((0, 0, 10, 10), 0.94, identity_embedding=(0.0, 1.0))])
                store.connection.commit()
                claire_cluster = cluster_face_identities(store.connection, threshold=0.9)[0]
                assign_person_name(store.connection, claire_cluster.cluster_id, "Claire")

                service = FeatureStoreSemanticSearch(store, None)
                hits = service.search("photos of Claire", known_people=("Claire",), filters=SearchFilters(min_confidence=0.99))

                self.assertEqual([claire], [hit.image_id for hit in hits])
                self.assertEqual(("people",), hits[0].matched_by)
            finally:
                store.close()

    def test_confidence_floor_does_not_suppress_filename_matches(self) -> None:
        with tempfile.TemporaryDirectory(prefix="filename_confidence_") as temp_dir:
            root = Path(temp_dir)
            store = SQLiteFeatureStore(root / "features.sqlite")
            try:
                filename_hit = store.upsert_image(root / "red-car-final.jpg", status="ready")
                store.save_features(filename_hit, np.asarray([0.0, 1.0], dtype=np.float32), technical_score=0.5)

                service = FeatureStoreSemanticSearch(store, _TextEncoder({"red car": [1.0, 0.0]}))
                hits = service.search("red car", filters=SearchFilters(min_confidence=0.99))

                self.assertEqual([filename_hit], [hit.image_id for hit in hits])
                self.assertEqual(("filename",), hits[0].matched_by)
            finally:
                store.close()

    def test_auto_confidence_keeps_the_best_semantic_band(self) -> None:
        with tempfile.TemporaryDirectory(prefix="auto_confidence_") as temp_dir:
            root = Path(temp_dir)
            store = SQLiteFeatureStore(root / "features.sqlite")
            try:
                best = store.upsert_image(root / "best.jpg", status="ready")
                close = store.upsert_image(root / "close.jpg", status="ready")
                weak = store.upsert_image(root / "weak.jpg", status="ready")
                store.save_features(best, np.asarray([1.0, 0.0], dtype=np.float32), technical_score=0.5)
                store.save_features(close, np.asarray([0.999, 0.045], dtype=np.float32), technical_score=0.5)
                store.save_features(weak, np.asarray([0.8, 0.6], dtype=np.float32), technical_score=0.5)

                service = FeatureStoreSemanticSearch(store, _TextEncoder({"mountains": [1.0, 0.0]}))
                hits = service.search("mountains", filters=SearchFilters(min_confidence=0.0))

                self.assertEqual([best, close], [hit.image_id for hit in hits])
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
