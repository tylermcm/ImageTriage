from __future__ import annotations

import unittest

from aiculler.metrics import (
    METRIC_KEYS,
    CullingMetricRecord,
    compute_culling_metrics,
    format_metric,
    is_keeper_label,
    is_reject_label,
)


class AICullerMetricsTests(unittest.TestCase):
    def test_unavailable_metrics_return_none_not_zero(self) -> None:
        metrics = compute_culling_metrics([])

        self.assertIsNone(metrics["keeper_recall"])
        self.assertIsNone(metrics["duplicate_winner_agreement"])
        self.assertIsNone(metrics["rank_correlation"])
        self.assertEqual("N/A", format_metric(metrics["keeper_recall"]))

    def test_computes_core_culling_metrics(self) -> None:
        records = [
            CullingMetricRecord(1, "hero", 0.95, cluster_id=10, folder_id="a"),
            CullingMetricRecord(2, "reject", 0.80, cluster_id=10, folder_id="a"),
            CullingMetricRecord(3, "keep", 0.70, cluster_id=11, folder_id="b"),
            CullingMetricRecord(4, "reject", 0.10, cluster_id=11, folder_id="b"),
        ]
        overrides = [
            {"override_type": "reject_rescue", "is_final": 1, "ignored_for_training": 0},
            {"override_type": "pick_demotion", "is_final": 0, "ignored_for_training": 1},
        ]

        metrics = compute_culling_metrics(records, mae=0.2, overrides=overrides)

        self.assertEqual(80.0, metrics["score_fit_percent"])
        self.assertEqual(0.5, metrics["keeper_recall"])
        self.assertEqual(0.5, metrics["false_reject_rate"])
        self.assertEqual(0.0, metrics["false_keep_rate"])
        self.assertEqual(0.5, metrics["top_30_recall"])
        self.assertEqual(1.0, metrics["duplicate_winner_agreement"])
        self.assertEqual(1.0, metrics["reject_rescue_rate"])
        self.assertEqual(0.0, metrics["pick_demotion_rate"])
        self.assertEqual(2, metrics["folder_count"])
        self.assertEqual(2, metrics["cluster_count"])
        self.assertEqual(2, metrics["override_count"])

    def test_stable_keys_and_label_aliases_are_supported(self) -> None:
        records = [
            CullingMetricRecord(1, "hero", 0.70, cluster_id=10, folder_id="a"),
            CullingMetricRecord(2, "bad", 0.90, cluster_id=10, folder_id="a"),
            CullingMetricRecord(3, "ai pick", 0.85, cluster_id=11, folder_id="b"),
            CullingMetricRecord(4, "no", 0.15, cluster_id=11, folder_id="b"),
            CullingMetricRecord(5, "maybe", 0.45, cluster_id=None, folder_id="b"),
        ]

        metrics = compute_culling_metrics(records, mae=0.1)

        self.assertEqual(tuple(metrics), METRIC_KEYS)
        self.assertTrue(is_keeper_label("ai pick"))
        self.assertTrue(is_keeper_label("yes"))
        self.assertTrue(is_reject_label("bad"))
        self.assertTrue(is_reject_label("0"))
        self.assertFalse(is_keeper_label("maybe"))
        self.assertFalse(is_reject_label("maybe"))
        self.assertEqual(90.0, metrics["score_fit_percent"])
        self.assertEqual(0.0, metrics["duplicate_winner_agreement"])
        self.assertEqual(1.0, metrics["duplicate_winner_top2_agreement"])
        self.assertIsNotNone(metrics["top_10_recall"])
        self.assertIsNotNone(metrics["top_20_recall"])
        self.assertIsNotNone(metrics["top_30_recall"])
        self.assertIsNotNone(metrics["rank_correlation"])


if __name__ == "__main__":
    unittest.main()
