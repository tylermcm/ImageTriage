from __future__ import annotations

import unittest

import numpy as np

from image_triage.quality.aesthetic import (
    DEFAULT_NEGATIVE_PROMPTS,
    DEFAULT_POSITIVE_PROMPTS,
    aesthetic_score,
    build_aesthetic_direction,
)


def mock_encode(prompt: str) -> np.ndarray:
    # "good" -> axis 0, "bad" -> axis 1, anything else -> axis 2.
    if "good" in prompt:
        return np.array([1.0, 0.0, 0.0])
    if "bad" in prompt:
        return np.array([0.0, 1.0, 0.0])
    return np.array([0.0, 0.0, 1.0])


class AestheticProjectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.direction = build_aesthetic_direction(mock_encode, ["good photo"], ["bad photo"])

    def test_direction_is_unit_vector(self) -> None:
        self.assertAlmostEqual(float(np.linalg.norm(self.direction)), 1.0, places=6)

    def test_aligned_image_scores_higher(self) -> None:
        good = aesthetic_score(np.array([1.0, 0.0, 0.0]), self.direction)
        bad = aesthetic_score(np.array([0.0, 1.0, 0.0]), self.direction)
        self.assertGreater(good, bad)
        for score in (good, bad):
            self.assertGreaterEqual(score, 0.0)
            self.assertLessEqual(score, 10.0)

    def test_orthogonal_image_scores_mid(self) -> None:
        neutral = aesthetic_score(np.array([0.0, 0.0, 1.0]), self.direction)
        self.assertAlmostEqual(neutral, 5.0, places=5)

    def test_default_prompts_build_finite_direction(self) -> None:
        rng = np.random.default_rng(0)
        cache: dict[str, np.ndarray] = {}

        def deterministic_encode(prompt: str) -> np.ndarray:
            if prompt not in cache:
                cache[prompt] = rng.normal(size=768)
            return cache[prompt]

        direction = build_aesthetic_direction(
            deterministic_encode, DEFAULT_POSITIVE_PROMPTS, DEFAULT_NEGATIVE_PROMPTS
        )
        self.assertEqual(direction.shape, (768,))
        self.assertTrue(np.all(np.isfinite(direction)))
        self.assertAlmostEqual(float(np.linalg.norm(direction)), 1.0, places=6)


if __name__ == "__main__":
    unittest.main()
