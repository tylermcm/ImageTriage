from __future__ import annotations

import csv
import json
import math
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image


AICULLING_ROOT = Path(__file__).resolve().parents[1] / "AICullingPipeline"
if str(AICULLING_ROOT) not in sys.path:
    sys.path.insert(0, str(AICULLING_ROOT))

from app.clustering.hashing import compute_dhash, hamming_distance_int
from app.labeling.loaders import (
    _partition_members_by_phash,
    load_labeling_dataset,
)
from app.labeling.models import ImageItem


class LabelingDataQualityTests(unittest.TestCase):
    def test_near_identical_images_are_collapsed_before_label_sampling(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            artifacts_dir = _artifacts_dir(temp_dir)
            image_ids = ["image_a", "image_b", "image_c"]
            _write_labeling_artifacts(artifacts_dir, image_ids=image_ids)
            np.save(
                artifacts_dir / "embeddings.npy",
                np.asarray(
                    [
                        _unit_vector(0.0),
                        _unit_vector(2.0),
                        _unit_vector(-2.0),
                    ],
                    dtype=np.float32,
                ),
            )

            dataset = load_labeling_dataset(
                artifacts_dir,
                metadata_filename="images.csv",
                image_ids_filename="image_ids.json",
                clusters_filename="clusters.csv",
                collapse_near_identical=True,
                near_identical_similarity_threshold=0.985,
                near_identical_outlier_deviation=0.004,
                filter_unusable=False,
                filter_semantic_outliers=False,
            )

            self.assertEqual(dataset.collapsed_near_duplicate_count, 2)
            self.assertEqual(dataset.near_duplicate_group_count, 1)
            self.assertEqual(len(dataset.ordered_images), 1)
            self.assertEqual(len(dataset.multi_image_clusters), 0)
            self.assertTrue((artifacts_dir / "near_identical_labeling_collapse.csv").exists())

    def test_default_near_identical_threshold_collapses_functional_duplicates(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            artifacts_dir = _artifacts_dir(temp_dir)
            image_ids = ["image_a", "image_b", "image_c"]
            _write_labeling_artifacts(artifacts_dir, image_ids=image_ids)
            np.save(
                artifacts_dir / "embeddings.npy",
                np.asarray(
                    [
                        _unit_vector(0.0),
                        _unit_vector(10.0),
                        _unit_vector(-10.0),
                    ],
                    dtype=np.float32,
                ),
            )

            dataset = load_labeling_dataset(
                artifacts_dir,
                metadata_filename="images.csv",
                image_ids_filename="image_ids.json",
                clusters_filename="clusters.csv",
                filter_unusable=False,
                filter_semantic_outliers=False,
            )

            self.assertEqual(dataset.collapsed_near_duplicate_count, 2)
            self.assertEqual(len(dataset.ordered_images), 1)

    def test_near_identical_threshold_collapses_adjacent_split_clusters(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            artifacts_dir = _artifacts_dir(temp_dir)
            image_ids = ["image_a", "image_b", "image_c"]
            _write_labeling_artifacts(
                artifacts_dir,
                image_ids=image_ids,
                cluster_ids=["cluster_a", "cluster_b", "cluster_c"],
            )
            np.save(
                artifacts_dir / "embeddings.npy",
                np.asarray(
                    [
                        _unit_vector(0.0),
                        _unit_vector(2.0),
                        _unit_vector(90.0),
                    ],
                    dtype=np.float32,
                ),
            )

            loose_dataset = load_labeling_dataset(
                artifacts_dir,
                metadata_filename="images.csv",
                image_ids_filename="image_ids.json",
                clusters_filename="clusters.csv",
                collapse_near_identical=True,
                near_identical_similarity_threshold=0.990,
                filter_unusable=False,
                filter_semantic_outliers=False,
            )
            strict_dataset = load_labeling_dataset(
                artifacts_dir,
                metadata_filename="images.csv",
                image_ids_filename="image_ids.json",
                clusters_filename="clusters.csv",
                collapse_near_identical=True,
                near_identical_similarity_threshold=0.9999,
                filter_unusable=False,
                filter_semantic_outliers=False,
            )

            self.assertEqual(loose_dataset.collapsed_near_duplicate_count, 1)
            self.assertEqual(len(loose_dataset.ordered_images), 2)
            self.assertEqual(strict_dataset.collapsed_near_duplicate_count, 0)
            self.assertEqual(len(strict_dataset.ordered_images), 3)

    def test_semantic_outliers_are_removed_from_cluster_labeling(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            artifacts_dir = _artifacts_dir(temp_dir)
            image_ids = ["image_a", "image_b", "image_c"]
            _write_labeling_artifacts(artifacts_dir, image_ids=image_ids)
            np.save(
                artifacts_dir / "embeddings.npy",
                np.asarray(
                    [
                        _unit_vector(0.0),
                        _unit_vector(3.0),
                        _unit_vector(150.0),
                    ],
                    dtype=np.float32,
                ),
            )

            dataset = load_labeling_dataset(
                artifacts_dir,
                metadata_filename="images.csv",
                image_ids_filename="image_ids.json",
                clusters_filename="clusters.csv",
                collapse_near_identical=False,
                filter_unusable=False,
                filter_semantic_outliers=True,
                semantic_outlier_similarity_threshold=0.55,
            )

            self.assertEqual(dataset.semantic_outlier_count, 1)
            self.assertEqual([image.image_id for image in dataset.multi_image_clusters[0].members], ["image_a", "image_b"])
            self.assertTrue((artifacts_dir / "labeling_semantic_outlier_filter.csv").exists())

    def test_lens_cap_black_images_are_filtered_before_labeling(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            artifacts_dir = _artifacts_dir(temp_dir)
            image_ids = ["image_a", "image_b"]
            _write_labeling_artifacts(artifacts_dir, image_ids=image_ids)
            np.save(
                artifacts_dir / "embeddings.npy",
                np.asarray([_unit_vector(0.0), _unit_vector(2.0)], dtype=np.float32),
            )
            _write_signal_records(
                artifacts_dir.parent / "signals" / "culling_signals.json",
                [
                    {
                        "image_id": "image_a",
                        "technical": {
                            "status": "analyzed",
                            "shadow_clip_ratio": 1.0,
                            "highlight_clip_ratio": 0.0,
                            "contrast_score": 0.0,
                            "sharpness_score": 0.0,
                            "detail_score": 0.0,
                            "exposure_status": "underexposed",
                        },
                    },
                    {
                        "image_id": "image_b",
                        "technical": {
                            "status": "analyzed",
                            "shadow_clip_ratio": 0.0,
                            "highlight_clip_ratio": 0.0,
                            "contrast_score": 0.2,
                            "sharpness_score": 0.5,
                            "detail_score": 0.5,
                            "exposure_status": "properly_exposed",
                        },
                    },
                ],
            )

            dataset = load_labeling_dataset(
                artifacts_dir,
                metadata_filename="images.csv",
                image_ids_filename="image_ids.json",
                clusters_filename="clusters.csv",
                collapse_near_identical=False,
                filter_unusable=True,
                filter_semantic_outliers=False,
            )

            self.assertEqual(dataset.filtered_unusable_count, 1)
            self.assertEqual([image.image_id for image in dataset.ordered_images], ["image_b"])
            self.assertTrue((artifacts_dir / "labeling_unusable_filter.csv").exists())

    def test_unusable_filter_does_not_decode_images_without_signals(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            artifacts_dir = _artifacts_dir(temp_dir)
            image_ids = ["image_a", "image_b"]
            _write_labeling_artifacts(artifacts_dir, image_ids=image_ids)
            np.save(
                artifacts_dir / "embeddings.npy",
                np.asarray([_unit_vector(0.0), _unit_vector(2.0)], dtype=np.float32),
            )

            dataset = load_labeling_dataset(
                artifacts_dir,
                metadata_filename="images.csv",
                image_ids_filename="image_ids.json",
                clusters_filename="clusters.csv",
                collapse_near_identical=False,
                filter_unusable=True,
                filter_semantic_outliers=False,
            )

            self.assertEqual(dataset.filtered_unusable_count, 0)
            self.assertEqual(len(dataset.ordered_images), 2)

    def test_large_clusters_are_subsampled_before_labeling(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            artifacts_dir = _artifacts_dir(temp_dir)
            image_ids = [f"image_{index}" for index in range(10)]
            _write_labeling_artifacts(artifacts_dir, image_ids=image_ids)
            np.save(
                artifacts_dir / "embeddings.npy",
                np.asarray([_unit_vector(float(index * 2)) for index in range(10)], dtype=np.float32),
            )

            dataset = load_labeling_dataset(
                artifacts_dir,
                metadata_filename="images.csv",
                image_ids_filename="image_ids.json",
                clusters_filename="clusters.csv",
                collapse_near_identical=False,
                filter_unusable=False,
                filter_semantic_outliers=False,
                max_labeling_cluster_images=4,
            )

            self.assertEqual(dataset.cluster_subsample_hidden_count, 6)
            self.assertEqual(len(dataset.multi_image_clusters[0].members), 4)
            self.assertTrue((artifacts_dir / "labeling_large_cluster_subsample.csv").exists())

    def test_dhash_is_deterministic_and_matches_for_identical_images(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path_a = Path(temp_dir) / "a.png"
            path_b = Path(temp_dir) / "b.png"
            _write_test_image(path_a, _solid_gradient())
            _write_test_image(path_b, _solid_gradient())

            hash_a = compute_dhash(path_a)
            hash_a_again = compute_dhash(path_a)
            hash_b = compute_dhash(path_b)

            self.assertIsNotNone(hash_a)
            self.assertEqual(hash_a, hash_a_again)
            self.assertEqual(hash_a, hash_b)
            self.assertEqual(hamming_distance_int(hash_a, hash_b), 0)

    def test_dhash_detects_visually_distinct_images(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            gradient_path = Path(temp_dir) / "gradient.png"
            checkerboard_path = Path(temp_dir) / "checker.png"
            _write_test_image(gradient_path, _solid_gradient())
            _write_test_image(checkerboard_path, _checkerboard())

            gradient_hash = compute_dhash(gradient_path)
            checkerboard_hash = compute_dhash(checkerboard_path)

            self.assertIsNotNone(gradient_hash)
            self.assertIsNotNone(checkerboard_hash)
            self.assertGreater(
                hamming_distance_int(gradient_hash, checkerboard_hash),
                16,
            )

    def test_dhash_tolerates_small_brightness_shift(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base_path = Path(temp_dir) / "base.png"
            shifted_path = Path(temp_dir) / "shifted.png"
            base = _solid_gradient()
            shifted = np.clip(base.astype(np.int32) + 12, 0, 255).astype(np.uint8)
            _write_test_image(base_path, base)
            _write_test_image(shifted_path, shifted)

            base_hash = compute_dhash(base_path)
            shifted_hash = compute_dhash(shifted_path)

            self.assertLessEqual(hamming_distance_int(base_hash, shifted_hash), 6)

    def test_partition_groups_identical_members_together(self) -> None:
        members = [
            _make_image_item("image_a"),
            _make_image_item("image_b"),
            _make_image_item("image_c"),
        ]
        phash_lookup = {"image_a": 0, "image_b": 0, "image_c": 0}

        groups = _partition_members_by_phash(members, phash_lookup=phash_lookup, hamming_threshold=6)

        self.assertEqual(len(groups), 1)
        self.assertEqual([image.image_id for image in groups[0]], ["image_a", "image_b", "image_c"])

    def test_partition_separates_dissimilar_members(self) -> None:
        members = [_make_image_item("image_a"), _make_image_item("image_b")]
        phash_lookup = {
            "image_a": 0,
            "image_b": (1 << 64) - 1,
        }

        groups = _partition_members_by_phash(members, phash_lookup=phash_lookup, hamming_threshold=6)

        self.assertEqual(len(groups), 2)
        self.assertEqual([group[0].image_id for group in groups], ["image_a", "image_b"])

    def test_partition_treats_missing_phashes_as_singletons(self) -> None:
        members = [
            _make_image_item("image_a"),
            _make_image_item("image_b"),
            _make_image_item("image_c"),
        ]
        phash_lookup = {"image_a": 0, "image_b": 0}

        groups = _partition_members_by_phash(members, phash_lookup=phash_lookup, hamming_threshold=6)

        grouped_ids = [[image.image_id for image in group] for group in groups]
        self.assertIn(["image_a", "image_b"], grouped_ids)
        self.assertIn(["image_c"], grouped_ids)
        self.assertEqual(len(groups), 2)

    def test_load_labeling_dataset_groups_visually_identical_cluster_members(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            artifacts_dir = _artifacts_dir(temp_dir)
            image_ids = ["image_a", "image_b", "image_c"]
            _write_labeling_artifacts(
                artifacts_dir,
                image_ids=image_ids,
                image_factory=lambda index: _solid_gradient() if index < 2 else _checkerboard(),
            )
            np.save(
                artifacts_dir / "embeddings.npy",
                np.asarray(
                    [_unit_vector(0.0), _unit_vector(20.0), _unit_vector(140.0)],
                    dtype=np.float32,
                ),
            )

            dataset = load_labeling_dataset(
                artifacts_dir,
                metadata_filename="images.csv",
                image_ids_filename="image_ids.json",
                clusters_filename="clusters.csv",
                collapse_near_identical=False,
                filter_unusable=False,
                filter_semantic_outliers=False,
                group_cluster_near_duplicates=True,
                cluster_near_duplicate_hamming_threshold=6,
            )

            self.assertIn("cluster_000", dataset.cluster_near_duplicate_groups)
            groups = dataset.cluster_near_duplicate_groups["cluster_000"]
            grouped_ids = sorted(sorted(image.image_id for image in group) for group in groups)
            self.assertEqual(grouped_ids, [["image_a", "image_b"], ["image_c"]])
            self.assertTrue((artifacts_dir / "phashes.npz").exists())

    def test_phash_cache_is_reused_across_loads(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            artifacts_dir = _artifacts_dir(temp_dir)
            image_ids = ["image_a", "image_b"]
            _write_labeling_artifacts(
                artifacts_dir,
                image_ids=image_ids,
                image_factory=lambda index: _solid_gradient(),
            )
            np.save(
                artifacts_dir / "embeddings.npy",
                np.asarray([_unit_vector(0.0), _unit_vector(2.0)], dtype=np.float32),
            )

            first = load_labeling_dataset(
                artifacts_dir,
                metadata_filename="images.csv",
                image_ids_filename="image_ids.json",
                clusters_filename="clusters.csv",
                collapse_near_identical=False,
                filter_unusable=False,
                filter_semantic_outliers=False,
            )
            self.assertEqual(set(first.phash_lookup.keys()), {"image_a", "image_b"})
            cache_path = artifacts_dir / "phashes.npz"
            self.assertTrue(cache_path.exists())
            original_mtime = cache_path.stat().st_mtime_ns

            second = load_labeling_dataset(
                artifacts_dir,
                metadata_filename="images.csv",
                image_ids_filename="image_ids.json",
                clusters_filename="clusters.csv",
                collapse_near_identical=False,
                filter_unusable=False,
                filter_semantic_outliers=False,
            )
            self.assertEqual(second.phash_lookup, first.phash_lookup)
            self.assertEqual(cache_path.stat().st_mtime_ns, original_mtime)

    def test_session_cluster_member_groups_returns_partitioned_view(self) -> None:
        from app.config import LabelingConfig
        from app.labeling.session import LabelingSession

        with tempfile.TemporaryDirectory() as temp_dir:
            artifacts_dir = _artifacts_dir(temp_dir)
            output_dir = Path(temp_dir) / "labels"
            output_dir.mkdir()
            image_ids = ["image_a", "image_b", "image_c"]
            _write_labeling_artifacts(
                artifacts_dir,
                image_ids=image_ids,
                image_factory=lambda index: _solid_gradient() if index < 2 else _checkerboard(),
            )
            np.save(
                artifacts_dir / "embeddings.npy",
                np.asarray(
                    [_unit_vector(0.0), _unit_vector(20.0), _unit_vector(140.0)],
                    dtype=np.float32,
                ),
            )
            config = LabelingConfig(
                artifacts_dir=artifacts_dir,
                output_dir=output_dir,
                collapse_near_identical_for_labeling=False,
                filter_unusable_for_labeling=False,
                filter_semantic_outliers_for_labeling=False,
            )
            session = LabelingSession(config)

            groups = session.cluster_member_groups("cluster_000")
            grouped_ids = sorted(sorted(image.image_id for image in group) for group in groups)
            self.assertEqual(grouped_ids, [["image_a", "image_b"], ["image_c"]])

            missing = session.cluster_member_groups("cluster_does_not_exist")
            self.assertEqual(missing, [])

    def test_group_initial_assignment_collapses_uniform_labels(self) -> None:
        from app.labeling.ui import _group_initial_assignment, _has_mixed_saved_assignments

        members = [_make_image_item("image_a"), _make_image_item("image_b")]
        uniform = {"image_a": "accept", "image_b": "accept"}
        mixed = {"image_a": "accept", "image_b": "reject"}
        partial = {"image_a": "accept"}

        self.assertEqual(_group_initial_assignment(members, uniform), "accept")
        self.assertEqual(_group_initial_assignment(members, mixed), "unlabeled")
        self.assertEqual(_group_initial_assignment(members, partial), "unlabeled")
        self.assertFalse(_has_mixed_saved_assignments(members, uniform))
        self.assertTrue(_has_mixed_saved_assignments(members, mixed))
        self.assertTrue(_has_mixed_saved_assignments(members, partial))

    def test_phash_progress_callback_reports_completion_counts(self) -> None:
        from app.labeling.loaders import _compute_phash_batch

        with tempfile.TemporaryDirectory() as temp_dir:
            members: list = []
            for index in range(5):
                path = Path(temp_dir) / f"image_{index}.png"
                _write_test_image(path, _solid_gradient())
                members.append(_make_image_item_with_path(f"image_{index}", path))

            observations: list[tuple[int, int, str]] = []

            def callback(done: int, total: int, state: str) -> None:
                observations.append((done, total, state))

            result = _compute_phash_batch(members, progress_callback=callback)

            self.assertEqual(len(result), 5)
            self.assertGreaterEqual(len(observations), 1)
            self.assertEqual(observations[-1][0], 5)
            self.assertEqual(observations[-1][1], 5)
            self.assertEqual(observations[-1][2], "computing_phashes")

def _write_labeling_artifacts(
    artifacts_dir: Path,
    *,
    image_ids: list[str],
    cluster_ids: list[str] | None = None,
    image_factory=None,
) -> None:
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    (artifacts_dir / "image_ids.json").write_text(json.dumps(image_ids), encoding="utf-8")
    metadata_rows = []
    cluster_rows = []
    for index, image_id in enumerate(image_ids):
        cluster_id = cluster_ids[index] if cluster_ids is not None else "cluster_000"
        if image_factory is not None:
            image_path = artifacts_dir / f"{image_id}.png"
            _write_test_image(image_path, image_factory(index))
        else:
            image_path = artifacts_dir / f"{image_id}.jpg"
            image_path.write_bytes(b"test")
        common = {
            "image_id": image_id,
            "file_path": str(image_path),
            "relative_path": image_path.name,
            "file_name": image_path.name,
            "embedding_index": str(index),
            "capture_timestamp": "",
            "capture_time_source": "missing",
            "timestamp_available": "False",
        }
        metadata_rows.append(common)
        cluster_rows.append(
            {
                **common,
                "cluster_id": cluster_id,
                "cluster_size": str(len(image_ids)),
                "cluster_position": str(index),
                "cluster_reason": "test",
                "window_kind": "test",
                "time_window_id": "test",
            }
        )
    _write_csv(artifacts_dir / "images.csv", metadata_rows)
    _write_csv(artifacts_dir / "clusters.csv", cluster_rows)


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def _write_signal_records(path: Path, records: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(records), encoding="utf-8")


def _artifacts_dir(temp_dir: str) -> Path:
    return Path(temp_dir) / "labeling_artifacts"


def _unit_vector(angle_degrees: float) -> list[float]:
    radians = math.radians(angle_degrees)
    return [math.cos(radians), math.sin(radians)]


def _write_test_image(path: Path, pixels: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(np.asarray(pixels, dtype=np.uint8)).save(path)


def _solid_gradient() -> np.ndarray:
    column = np.linspace(0, 255, 64, dtype=np.float32)
    return np.tile(column, (64, 1)).astype(np.uint8)


def _checkerboard() -> np.ndarray:
    tile = np.indices((64, 64)).sum(axis=0) // 8
    return ((tile % 2) * 255).astype(np.uint8)


def _make_image_item(image_id: str) -> ImageItem:
    return ImageItem(
        image_id=image_id,
        file_path=Path(f"{image_id}.png"),
        relative_path=f"{image_id}.png",
        file_name=f"{image_id}.png",
        cluster_id="cluster_000",
        cluster_size=1,
        embedding_index=None,
        capture_timestamp="",
        capture_time_source="missing",
        timestamp_available=False,
        file_exists=False,
    )


def _make_image_item_with_path(image_id: str, path: Path) -> ImageItem:
    return ImageItem(
        image_id=image_id,
        file_path=path,
        relative_path=path.name,
        file_name=path.name,
        cluster_id="cluster_000",
        cluster_size=1,
        embedding_index=None,
        capture_timestamp="",
        capture_time_source="missing",
        timestamp_available=False,
        file_exists=path.exists(),
    )


if __name__ == "__main__":
    unittest.main()
