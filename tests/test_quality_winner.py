from __future__ import annotations

import unittest

import numpy as np

from image_triage.quality.analysis import spearman
from image_triage.quality.winner import rank_folder_winners


class WinnerRankingTests(unittest.TestCase):
    def setUp(self) -> None:
        rng = np.random.default_rng(0)
        self.E = rng.normal(size=(100, 5))
        self.ids = list(range(100))
        self.labels = self.E[:, 0]  # "wow" is driven by dim 0
        self.global_scores = list(self.E[:, 1])  # global prior orders by a different dim

    def _blended_array(self, results):
        m = {r.image_id: r.blended for r in results}
        return np.array([m[i] for i in self.ids])

    def test_per_folder_dominates_with_many_labels(self) -> None:
        idx = list(range(60))
        res = rank_folder_winners(
            self.E[idx], self.labels[idx], self.ids, self.E, self.global_scores,
            alpha=1.0, ramp=20, min_labels=8,
        )
        b = self._blended_array(res)
        rho_local, _ = spearman(b, self.E[:, 0])
        rho_global, _ = spearman(b, self.E[:, 1])
        self.assertGreater(rho_local, rho_global)
        self.assertGreater(rho_local, 0.5)
        self.assertTrue(all(r.source == "blend" for r in res))

    def test_cold_start_uses_global(self) -> None:
        idx = list(range(3))  # below min_labels
        res = rank_folder_winners(
            self.E[idx], self.labels[idx], self.ids, self.E, self.global_scores, min_labels=8
        )
        self.assertTrue(all(r.source == "global" for r in res))
        rho_global, _ = spearman(self._blended_array(res), self.E[:, 1])
        self.assertGreater(rho_global, 0.9)

    def test_no_global_uses_per_folder_only(self) -> None:
        idx = list(range(60))
        res = rank_folder_winners(
            self.E[idx], self.labels[idx], self.ids, self.E, None, alpha=1.0, min_labels=8
        )
        self.assertTrue(all(r.source == "per_folder" for r in res))

    def test_results_sorted_descending(self) -> None:
        idx = list(range(60))
        res = rank_folder_winners(self.E[idx], self.labels[idx], self.ids, self.E, self.global_scores)
        blended = [r.blended for r in res]
        self.assertEqual(blended, sorted(blended, reverse=True))


if __name__ == "__main__":
    unittest.main()
