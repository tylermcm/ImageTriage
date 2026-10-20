"""Optional cascade behaviours (linkage, stack span, focal and face splits, all-flagged collapse). Defaults must reproduce the original output."""

from __future__ import annotations

import json
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest

from image_triage.cascade import CascadeSettings, FrameFeatures, FrameRole, run_cascade
from image_triage.cascade.stacking import complete_linkage, split_into_stacks

GOLDEN = Path(__file__).parent / "fixtures" / "cascade_golden.json"


def make(key: str, phash: int = 0, *, sharp: float = 100.0, clipped: float = 0.01, blink: bool = False, **extra) -> FrameFeatures:
    return FrameFeatures(key, phash, sharp, clipped, 0.01, blink, **extra)


def bits(count: int) -> int:
    return (1 << count) - 1


def explicit_defaults() -> CascadeSettings:
    return CascadeSettings(
        linkage="first_fit",
        max_stack_span=None,
        split_focal_ratio=None,
        split_face_ratio=None,
        all_flagged_skips_collapse=False,
    )


def test_default_option_values():
    assert CascadeSettings() == explicit_defaults()


def test_settings_stay_frozen_and_hashable():
    settings = CascadeSettings(linkage="closest", max_stack_span=18)
    with pytest.raises(FrozenInstanceError):
        settings.linkage = "first_fit"  # type: ignore[misc]
    assert hash(settings) == hash(CascadeSettings(linkage="closest", max_stack_span=18))
    assert {settings, CascadeSettings()}


def test_defaults_match_explicit_defaults_on_the_golden_fixture():
    data = json.loads(GOLDEN.read_text(encoding="utf-8"))
    for burst in data["bursts"]:
        frames = [FrameFeatures(f["key"], int(f["phash"], 16), f["centre_sharpness"], f["clipped"], f["extremes"], f["blink"]) for f in burst["frames"]]
        maniqa = {f["key"]: f["maniqa"] for f in burst["frames"]}
        aesthetic = {f["key"]: f["aesthetic"] for f in burst["frames"]}
        plain = run_cascade(frames, maniqa=lambda ks: {k: maniqa[k] for k in ks}, aesthetic=lambda ks: {k: aesthetic[k] for k in ks})
        explicit = run_cascade(
            frames, explicit_defaults(), maniqa=lambda ks: {k: maniqa[k] for k in ks}, aesthetic=lambda ks: {k: aesthetic[k] for k in ks}
        )
        assert plain == explicit


# -- linkage ----------------------------------------------------------------------------------------------------------------------------


def abc_frames(order: str) -> list[FrameFeatures]:
    hashes = {"A": 0, "B": 255, "C": 3843}
    return [make(k, hashes[k]) for k in order]


def keys_of(clusters, frames):
    return sorted(sorted(frames[i].key for i in c) for c in clusters)


@pytest.mark.parametrize("order,expected", [("ABC", [["A", "B"], ["C"]]), ("ACB", [["A", "C"], ["B"]])])
def test_first_fit_depends_on_input_order(order, expected):
    frames = abc_frames(order)
    assert keys_of(complete_linkage(range(3), frames, 8), frames) == expected
    assert keys_of(complete_linkage(range(3), frames, 8, "first_fit"), frames) == expected


@pytest.mark.parametrize("order", ["ABC", "ACB", "BAC", "CBA"])
def test_closest_linkage_is_order_independent(order):
    frames = abc_frames(order)
    assert keys_of(complete_linkage(range(3), frames, 8, "closest"), frames) == [["A", "C"], ["B"]]


def test_closest_linkage_enforces_the_diameter_bound():
    frames = [make(str(i), bits(i * 4)) for i in range(6)]  # a chain: neighbours 4 apart, ends 20 apart
    for linkage in ("first_fit", "closest"):
        for cluster in complete_linkage(range(6), frames, 8, linkage):
            assert all(bin(frames[i].phash ^ frames[j].phash).count("1") <= 8 for i in cluster for j in cluster)


def test_closest_linkage_ties_prefer_lowest_indices():
    frames = [make("a", 0), make("b", bits(4)), make("c", bits(8))]  # ab=4, bc=4, ac=8
    assert complete_linkage(range(3), frames, 7, "closest") == [[0, 1], [2]]


def test_unknown_linkage_is_rejected():
    frames = [make("a"), make("b")]
    with pytest.raises(ValueError):
        complete_linkage(range(2), frames, 8, "average")
    with pytest.raises(ValueError):
        run_cascade(frames, CascadeSettings(linkage="average"))


def test_run_cascade_passes_the_linkage_through():
    frames = abc_frames("ABC")
    first_fit = run_cascade(frames).decisions_by_key
    closest = run_cascade(frames, CascadeSettings(linkage="closest")).decisions_by_key
    assert first_fit["B"].duplicate_of == "A" and first_fit["C"].role is FrameRole.FINAL
    assert closest["C"].duplicate_of == "A" and closest["B"].role is FrameRole.FINAL


# -- max_stack_span ---------------------------------------------------------------------------------------------------------------------


def test_max_stack_span_stops_drift():
    frames = [make("a", bits(0)), make("b", bits(10)), make("c", bits(20))]  # each step 10, ends 20 apart
    assert split_into_stacks(frames, CascadeSettings()) == [[0, 1, 2]]
    assert split_into_stacks(frames, CascadeSettings(max_stack_span=18)) == [[0, 1], [2]]
    assert split_into_stacks(frames, CascadeSettings(max_stack_span=21)) == [[0, 1, 2]]


def test_max_stack_span_reports_split_jump_unchanged():
    frames = [make("a", bits(0)), make("b", bits(10)), make("c", bits(20))]
    result = run_cascade(frames, CascadeSettings(max_stack_span=18))
    assert [s.split_jump for s in result.stacks] == [None, 10]


# -- focal and face ratios --------------------------------------------------------------------------------------------------------------


def test_focal_ratio_splits_on_zoom_change():
    frames = [make("a", focal_mm=24.0), make("b", focal_mm=26.0), make("c", focal_mm=70.0)]
    assert split_into_stacks(frames, CascadeSettings()) == [[0, 1, 2]]
    assert split_into_stacks(frames, CascadeSettings(split_focal_ratio=1.5)) == [[0, 1], [2]]
    # Zooming out splits too (the ratio is symmetric).
    assert split_into_stacks(frames[::-1], CascadeSettings(split_focal_ratio=1.5)) == [[0], [1, 2]]


def test_focal_ratio_ignores_missing_values():
    frames = [make("a", focal_mm=24.0), make("b"), make("c", focal_mm=200.0)]
    assert split_into_stacks(frames, CascadeSettings(split_focal_ratio=1.1)) == [[0, 1, 2]]


def test_face_ratio_splits_when_the_face_grows():
    frames = [make("a", face_area=0.05), make("b", face_area=0.06), make("c", face_area=0.25)]
    assert split_into_stacks(frames, CascadeSettings()) == [[0, 1, 2]]
    assert split_into_stacks(frames, CascadeSettings(split_face_ratio=2.0)) == [[0, 1], [2]]


def test_face_ratio_ignores_missing_and_tiny_faces():
    frames = [make("a", face_area=0.05), make("b"), make("c", face_area=0.5), make("d", face_area=0.005), make("e", face_area=0.9)]
    assert split_into_stacks(frames, CascadeSettings(split_face_ratio=2.0)) == [[0, 1, 2, 3, 4]]


def test_frame_features_extra_fields_default_to_none():
    frame = FrameFeatures("a", 0, 1.0, 0.0, 0.0, False)
    assert frame.focal_mm is None and frame.face_area is None
    assert replace(frame, focal_mm=35.0).focal_mm == 35.0


# -- all_flagged_skips_collapse ---------------------------------------------------------------------------------------------------------


def all_blink_stack() -> list[FrameFeatures]:
    return [make("a", 0, blink=True), make("b", bits(2), blink=True), make("c", bits(3), blink=True)]


def test_all_flagged_stack_collapses_by_default():
    decisions = run_cascade(all_blink_stack()).decisions_by_key
    assert sum(d.role is FrameRole.DUPLICATE for d in decisions.values()) == 2


def test_all_flagged_stack_can_skip_collapse():
    result = run_cascade(all_blink_stack(), CascadeSettings(all_flagged_skips_collapse=True, final_keep=3))
    roles = [d.role for d in result.decisions]
    assert FrameRole.DUPLICATE not in roles
    assert roles == [FrameRole.FINAL] * 3


def test_skip_collapse_does_not_change_stacks_with_a_clean_frame():
    frames = [make("a", 0, blink=True), make("b", bits(2))]
    assert run_cascade(frames) == run_cascade(frames, CascadeSettings(all_flagged_skips_collapse=True))
