from __future__ import annotations

import unittest

import numpy as np

from image_triage.quality.analysis import spearman
from image_triage.quality.learner import (
    RidgePreferenceLearner,
    blend_local_global,
    blend_weight,
    cross_val_predict,
    feature_matrix,
)


class FeatureMatrixTests(unittest.TestCase):
    def test_builds_and_imputes(self) -> None:
        rows = [
            {"sharpness": 8.0, "noise": 6.0},
            {"sharpness": None, "noise": 4.0},  # sharpness imputed to col mean (8.0)
        ]
        X, names = feature_matrix(rows, ["sharpness", "noise"])
        self.assertEqual(names, ("sharpness", "noise"))
        self.assertEqual(X.shape, (2, 2))
        self.assertAlmostEqual(X[1, 0], 8.0)  # imputed
        self.assertFalse(np.isnan(X).any())


class LearnerTests(unittest.TestCase):
    def setUp(self) -> None:
        rng = np.random.default_rng(0)
        self.X = rng.normal(size=(200, 5))
        true_w = np.array([2.0, -1.0, 0.5, 0.0, 0.0])
        self.y = self.X @ true_w + rng.normal(scale=0.3, size=200)

    def test_fit_predict_recovers_signal_on_holdout(self) -> None:
        train, test = slice(0, 150), slice(150, 200)
        model = RidgePreferenceLearner(alpha=1.0).fit(self.X[train], self.y[train])
        pred = model.predict(self.X[test])
        rho, _ = spearman(pred, self.y[test])
        self.assertGreater(rho, 0.8)

    def test_cross_val_predict_is_honest_and_correlated(self) -> None:
        pred = cross_val_predict(self.X, self.y, folds=5, alpha=1.0)
        self.assertEqual(pred.shape, (200,))
        rho, _ = spearman(pred, self.y)
        self.assertGreater(rho, 0.8)

    def test_predict_before_fit_raises(self) -> None:
        with self.assertRaises(RuntimeError):
            RidgePreferenceLearner().predict(self.X)


class BlendTests(unittest.TestCase):
    def test_blend_weight_ramps(self) -> None:
        self.assertEqual(blend_weight(0, ramp=20), 0.0)
        self.assertAlmostEqual(blend_weight(20, ramp=20), 0.5)
        self.assertGreater(blend_weight(80, ramp=20), 0.7)
        # monotonic
        self.assertLess(blend_weight(5, ramp=20), blend_weight(40, ramp=20))

    def test_blend_cold_start_is_global(self) -> None:
        self.assertAlmostEqual(blend_local_global(9.0, 1.0, n_local=0, ramp=20), 1.0)

    def test_blend_warm_leans_local(self) -> None:
        blended = blend_local_global(9.0, 1.0, n_local=200, ramp=20)
        self.assertGreater(blended, 7.5)


if __name__ == "__main__":
    unittest.main()
