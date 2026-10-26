from __future__ import annotations

import pytest

from image_triage.cascade import audit
from image_triage.cascade.audit import FrameRow
from image_triage.cascade.model import CascadeSettings, FrameFeatures


def test_needed_zero_miss_is_598():
    assert audit.needed_zero_miss() == 598
    assert audit.clopper_pearson_lower(598, 598) > 0.995
    assert audit.clopper_pearson_lower(597, 597) <= 0.995


def test_clopper_pearson_known_values():
    assert audit.clopper_pearson_lower(200, 200) == pytest.approx(0.9851, abs=1e-4)
    assert audit.clopper_pearson_lower(0, 10) == 0.0
    assert audit.clopper_pearson_lower(0, 0) == 0.0
    # 9 of 10: exact one-sided 95% lower bound is 0.6058
    assert audit.clopper_pearson_lower(9, 10) == pytest.approx(0.6058, abs=1e-3)
    # 95 of 100: 0.8993
    assert audit.clopper_pearson_lower(95, 100) == pytest.approx(0.8993, abs=2e-3)
    with pytest.raises(ValueError):
        audit.clopper_pearson_lower(5, 4)


def test_lower_bound_grows_with_trials_and_falls_with_confidence():
    assert audit.clopper_pearson_lower(99, 100) < audit.clopper_pearson_lower(990, 1000)
    assert audit.clopper_pearson_lower(95, 100, 0.99) < audit.clopper_pearson_lower(95, 100, 0.95)


def _rows():
    return [
        FrameRow("a", "final", (), 3),
        FrameRow("b", "rejected", ("soft",), 3),
        FrameRow("c", "rejected", ("clipped", "blown_out"), 3),
        FrameRow("d", "flagged", ("blink",), 1),  # alone: kept by the fallback
        FrameRow("e", "no_pick", ("blown_out",), 1),
        FrameRow("f", "final", ("blown_out",), 2),  # blown_out alone does not reject
        FrameRow("g", "duplicate", (), 2),
        FrameRow("h", "bracket", (), 1),
    ]


def test_rule_attribution_and_eligibility():
    table = audit.rule_recall(_rows(), {"a", "b", "c", "d", "e", "f", "h"})
    assert table["soft"].flagged == 1 and table["soft"].misses == ["b"]
    assert table["soft"].eligible == 4  # a, b, c, f (d and e are alone; h is a bracket)
    assert table["clipped"].misses == ["c"]
    assert table["blown_out"].flagged == 3 and table["blown_out"].eligible == 6
    assert table["blink"].misses == ["d"] and table["blink"].eligible == 6
    assert table["soft"].recall == pytest.approx(0.75)
    assert table["soft"].lower_bound() < 0.75


def test_blink_not_measured():
    table = audit.rule_recall(_rows(), {"a", "d"}, blink_measured=False)
    assert table["blink"].eligible == 0 and not table["blink"].measured
    table = audit.rule_recall(_rows(), {"a", "d"}, blink_keys={"a"})
    assert table["blink"].eligible == 1 and table["blink"].flagged == 0


def test_union_fallback_nopick_survival():
    out = audit.keeper_outcomes(_rows(), {"a", "b", "c", "d", "e", "f", "g", "h"})
    assert out["eligible"] == 7  # bracket excluded
    assert sorted(out["union_misses"]) == ["b", "c", "d"]  # f is blown_out only; e likewise
    assert out["union_flagged"] == 3
    assert out["rejected"] == 2
    assert out["saved_by_all_flagged_fallback_keys"] == ["d"]
    assert out["no_pick"] == 1 and out["no_pick_keys"] == ["e"]
    assert out["survived"] == 3  # a, d (flagged), f
    assert sorted(out["lost"]) == ["b", "c", "e", "g"]
    assert out["survival"] == pytest.approx(3 / 7)
    assert out["union_recall"] == pytest.approx(4 / 7)


def test_flag_rates():
    rates = audit.flag_rates(_rows())
    assert rates["decided"] == 7
    assert rates["flag_rate"] == pytest.approx(3 / 7)
    assert rates["reject_rate"] == pytest.approx(2 / 7)
    assert rates["blown_out_rate"] == pytest.approx(3 / 7)


def _feat(key, sharp, clipped=0.0, phash=0):
    return FrameFeatures(key, phash, sharp, clipped, clipped)


def _evaluate_factory(frames):
    from image_triage.cascade.flags import flag_stack

    def evaluate(settings):
        idx = list(range(len(frames)))
        flags = flag_stack(idx, frames, settings)
        return [FrameRow(frames[i].key, "rejected" if audit.is_rejecting(flags[i]) else "final", flags[i], len(frames)) for i in idx]

    return evaluate


def test_sweep_monotone_and_unknown_fields():
    frames = [_feat(f"f{i}", sharp) for i, sharp in enumerate([100, 90, 70, 50, 30, 10])]
    keepers = {f.key for f in frames}
    result = audit.run_sweep(_evaluate_factory(frames), CascadeSettings(), {"soft_ratio": [0.1, 0.3, 0.5, 0.7, 0.9], "nonsense": [1]}, keepers)
    assert result["unknown_fields"] == ["nonsense"]
    entries = result["entries"]
    assert entries[0]["label"] == "baseline" and len(entries) == 6
    recalls = [e["union_recall"] for e in entries[1:]]
    rates = [e["flag_rate"] for e in entries[1:]]
    assert recalls == sorted(recalls, reverse=True)  # a stricter soft rule flags more keepers
    assert rates == sorted(rates)
    assert rates[-1] > rates[0]


def test_sweep_plan():
    plan = audit.sweep_plan({"a": [1, 2], "b": [3, 4, 5]}, full_grid=True)
    assert len(plan) == 6 and {"a": 2, "b": 5} in plan
    assert len(audit.sweep_plan({"a": [1, 2], "b": [3, 4, 5]})) == 5
    assert audit.sweep_plan({}) == []


def test_diameter_diagnostics():
    # neighbours differ by 6, 6, 6, 6 bits; the ends by 24
    hashes = [0, 0x3F, 0xFFF, 0x3FFFF, 0xFFFFFF]
    features = {f"k{i}": _feat(f"k{i}", 1.0, phash=h) for i, h in enumerate(hashes)}
    features["x"] = _feat("x", 1.0, phash=0)
    features["y"] = _feat("y", 1.0, phash=1)
    stacks = [[f"k{i}" for i in range(5)], ["x", "y"], ["x"]]
    diag = audit.stack_diagnostics(stacks, features, focal={"k0": 50.0, "k1": 50.0, "k4": 100.0}, faces={"x": 0.1, "y": 0.3})
    assert diag["stacks_with_pairs"] == 2
    assert diag["max_diameter"] == 24
    assert diag["drifting_count"] == 1
    drift = diag["drifting"][0]
    assert drift["endpoints"] == ["k0", "k4"] and drift["focal_ratio"] == pytest.approx(2.0)
    assert diag["face_ratio_over_limit"] == 1 and diag["focal_ratio_over_limit"] == 1


def test_features_read_with_getattr():
    class WithFocal:
        phash = 0
        focal_mm = 35.0

    assert audit.feature_value({"a": WithFocal()}, "a", "focal_mm") == 35.0
    assert audit.feature_value({"a": WithFocal()}, "a", "face_area") is None
    assert audit.feature_value({"a": WithFocal()}, "a", "face_area", {"a": 0.2}) == 0.2


def test_review_pairs_suspects_and_controls():
    features = {f"s{i}": _feat(f"s{i}", 1.0, phash=0) for i in range(6)}
    features.update({f"t{i}": _feat(f"t{i}", 1.0, phash=0) for i in range(6)})
    focal = {"s0": 50.0, "s1": 50.0, "s2": 85.0, "s3": 85.0, "s4": 85.0, "s5": 85.0}
    focal.update({f"t{i}": 50.0 for i in range(6)})
    stacks = [[f"s{i}" for i in range(6)], [f"t{i}" for i in range(6)]]
    pairs = audit.select_review_pairs(stacks, features, 4, focal=focal)
    assert len(pairs) == 4
    reasons = [p["reason"] for p in pairs]
    assert any(r.startswith("focal") for r in reasons) and "control" in reasons
    for p in pairs:
        assert p["a"][0] == p["b"][0]  # same stack
    assert len({p["id"] for p in pairs}) == 4
