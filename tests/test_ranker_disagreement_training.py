from __future__ import annotations

import unittest
import importlib.util
from dataclasses import dataclass
from pathlib import Path


MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "AICullingPipeline"
    / "app"
    / "engine"
    / "ranking"
    / "preference_sampling.py"
)
spec = importlib.util.spec_from_file_location("ranker_preference_sampling_test_module", MODULE_PATH)
assert spec is not None
preference_sampling = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(preference_sampling)
oversample_disagreement_preferences = preference_sampling.oversample_disagreement_preferences


@dataclass(frozen=True)
class Preference:
    source_mode: str
    index: int


def _preference(source_mode: str, index: int) -> Preference:
    return Preference(source_mode=source_mode, index=index)


class RankerDisagreementTrainingTests(unittest.TestCase):
    def test_disagreement_pairs_are_oversampled_by_factor(self) -> None:
        preferences = [
            _preference("manual", 0),
            _preference("ai_disagreement", 1),
            _preference("cluster_label", 2),
        ]

        oversampled = oversample_disagreement_preferences(preferences, factor=3)

        self.assertEqual(5, len(oversampled))
        self.assertEqual(
            3,
            sum(1 for preference in oversampled if preference.source_mode == "ai_disagreement"),
        )

    def test_oversample_factor_one_is_noop(self) -> None:
        preferences = [_preference("ai_disagreement", 0), _preference("manual", 1)]

        oversampled = oversample_disagreement_preferences(preferences, factor=1)

        self.assertEqual(preferences, oversampled)


if __name__ == "__main__":
    unittest.main()
