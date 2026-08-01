from __future__ import annotations

import unittest

import numpy as np

from aiculler.clip_quality import compare_clip_outputs
from aiculler.cli import _parse_clip_variant_spec


class ClipQualityTests(unittest.TestCase):
    def test_identical_outputs_pass_every_quality_check(self) -> None:
        embeddings = [
            np.array([1.0, 0.0, 0.0], dtype=np.float32),
            np.array([0.9, 0.1, 0.0], dtype=np.float32),
            np.array([0.0, 1.0, 0.0], dtype=np.float32),
            np.array([0.0, 0.1, 0.9], dtype=np.float32),
        ]
        categories = {
            "first": np.array([1.0, 0.0, 0.0], dtype=np.float32),
            "second": np.array([0.0, 1.0, 0.0], dtype=np.float32),
            "third": np.array([0.0, 0.0, 1.0], dtype=np.float32),
        }

        report = compare_clip_outputs(
            embeddings,
            embeddings,
            candidate_category_vectors=categories,
            reference_category_vectors=categories,
            neighbor_count=2,
        )

        self.assertTrue(report["candidate_recommended"])
        self.assertEqual(1.0, report["embedding_cosine"]["mean"])
        self.assertAlmostEqual(1.0, report["pairwise_similarity"]["spearman"])
        self.assertEqual(1.0, report["nearest_neighbors"]["overlap"]["mean"])
        self.assertEqual(1.0, report["semantic_categories"]["primary_category_agreement"])

    def test_changed_geometry_and_categories_fail_recommendation(self) -> None:
        reference = [
            np.array([1.0, 0.0], dtype=np.float32),
            np.array([0.9, 0.1], dtype=np.float32),
            np.array([0.0, 1.0], dtype=np.float32),
        ]
        candidate = [
            np.array([0.0, 1.0], dtype=np.float32),
            np.array([0.9, 0.1], dtype=np.float32),
            np.array([1.0, 0.0], dtype=np.float32),
        ]
        reference_categories = {
            "horizontal": np.array([1.0, 0.0], dtype=np.float32),
            "vertical": np.array([0.0, 1.0], dtype=np.float32),
        }
        candidate_categories = {
            "horizontal": np.array([1.0, 0.0], dtype=np.float32),
            "vertical": np.array([0.0, 1.0], dtype=np.float32),
        }

        report = compare_clip_outputs(
            candidate,
            reference,
            candidate_category_vectors=candidate_categories,
            reference_category_vectors=reference_categories,
            neighbor_count=1,
        )

        self.assertFalse(report["candidate_recommended"])
        self.assertEqual(2, report["semantic_categories"]["disagreement_count"])
        self.assertFalse(report["checks"]["mean_embedding_cosine"])

    def test_mismatched_embedding_shapes_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "Embedding matrices differ"):
            compare_clip_outputs(
                [np.ones(2, dtype=np.float32)],
                [np.ones(3, dtype=np.float32)],
            )

    def test_matrix_candidate_spec_parses_paths_and_provider(self) -> None:
        spec = _parse_clip_variant_spec("int8|vision.onnx|text.onnx|cpu")

        self.assertEqual("int8", spec.name)
        self.assertEqual("vision.onnx", spec.vision_model.name)
        self.assertEqual("text.onnx", spec.text_model.name)
        self.assertEqual("cpu", spec.provider_mode)

    def test_matrix_candidate_spec_rejects_unknown_provider(self) -> None:
        with self.assertRaisesRegex(ValueError, "provider must be cpu or auto"):
            _parse_clip_variant_spec("bad|vision.onnx|text.onnx|cuda")


if __name__ == "__main__":
    unittest.main()
