from __future__ import annotations

import unittest

from image_triage.quality.analysis import dimension_label_correlations, spearman


class SpearmanTests(unittest.TestCase):
    def test_monotonic_increasing(self) -> None:
        rho, n = spearman([1, 2, 3, 4, 5], [10, 20, 30, 40, 50])
        self.assertAlmostEqual(rho, 1.0, places=6)
        self.assertEqual(n, 5)

    def test_monotonic_decreasing(self) -> None:
        rho, _ = spearman([1, 2, 3, 4, 5], [5, 4, 3, 2, 1])
        self.assertAlmostEqual(rho, -1.0, places=6)

    def test_constant_returns_none(self) -> None:
        rho, _ = spearman([1, 2, 3], [7, 7, 7])
        self.assertIsNone(rho)

    def test_small_n_returns_none(self) -> None:
        rho, n = spearman([1, 2], [2, 4])
        self.assertIsNone(rho)
        self.assertEqual(n, 2)


class ReasonCorrelationTests(unittest.TestCase):
    def _rows(self):
        # 16 rows: sharpness increases with label; technical_failure tagged on
        # the low-sharpness half. So sharpness should correlate +1 with label
        # and strongly negative with the technical_failure indicator.
        rows = []
        for i in range(16):
            rows.append(
                {
                    "sharpness": float(i),
                    "numeric_score": float(i),
                    "folder_id": "f1",
                    "reason_tags": ("technical_failure",) if i < 8 else (),
                }
            )
        return rows

    def test_dimension_predicts_its_reason(self) -> None:
        result = dimension_label_correlations(
            self._rows(), dimensions=["sharpness"], reasons=["technical_failure"]
        )
        feat = result["features"]["sharpness"]
        self.assertAlmostEqual(feat["vs_label"]["rho"], 1.0, places=6)
        self.assertEqual(feat["vs_label"]["n"], 16)
        self.assertTrue(feat["vs_label"]["trusted"])
        self.assertLess(feat["vs_reason:technical_failure"]["rho"], -0.5)

    def test_auto_discovery_filters_low_support_reasons(self) -> None:
        rows = self._rows()
        # Add a rare reason that appears only 3 times (< min_n) -> not analyzed.
        for r in rows[:3]:
            r["reason_tags"] = (*r["reason_tags"], "rare_reason")
        result = dimension_label_correlations(rows, dimensions=["sharpness"])
        self.assertIn("technical_failure", result["reasons_analyzed"])  # 8 occurrences
        self.assertNotIn("rare_reason", result["reasons_analyzed"])  # only 3
        self.assertEqual(result["reason_counts"]["rare_reason"], 3)


if __name__ == "__main__":
    unittest.main()
