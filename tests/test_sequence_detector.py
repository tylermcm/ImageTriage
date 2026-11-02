from __future__ import annotations

from image_triage import sequence_detector as sd
from image_triage.sequence_detector import Frame, detect_sequences


def shot(path, stops=0.0, *, at, base=1 / 125, f=8.0, iso=100.0, **kw):
    return Frame(path=path, taken_at=at, shutter=base * 2 ** stops, aperture=f, iso=iso, **kw)


def ladder(prefix, rungs, *, start=0.0, gap=0.5, step=1.0, **kw):
    return [shot(f"{prefix}{i}.nef", r * step, at=start + i * gap, **kw) for i, r in enumerate(rungs)]


def test_three_shot_default_order_is_found():
    groups = detect_sequences(ladder("a", [0, -1, 1]))
    assert len(groups) == 1
    group = groups[0]
    assert group.kind == sd.KIND_EXPOSURE_BRACKET
    assert group.order == "0,-,+" and group.order_support == "confirmed"
    assert group.status == sd.STATUS_UNCONFIRMED  # no scene check yet
    assert group.exposures == 3 and abs(group.step_ev - 1.0) < 0.01


def test_five_shot_ascending_order():
    group = detect_sequences(ladder("a", [-2, -1, 0, 1, 2]))[0]
    assert group.exposures == 5 and group.order == "-,0,+"


def test_unlisted_order_is_possible_not_confirmed():
    group = detect_sequences(ladder("a", [0, 2, 1, -1, -2]))[0]
    assert group.order == "0,+,-" and group.order_support == "possible"


def test_unknown_order_is_still_found_but_marked():
    group = detect_sequences(ladder("a", [1, -2, 0, 2, -1]))[0]
    assert group.order is None and group.warnings


def test_back_to_back_brackets_split_into_two():
    frames = ladder("a", [0, -1, 1]) + ladder("b", [0, -1, 1], start=2.0)
    groups = detect_sequences(frames)
    assert [len(g.paths) for g in groups] == [3, 3]


def test_slow_bracket_is_not_split_by_a_short_window():
    frames = [shot("s0.nef", 0, at=0, base=8.0), shot("s1.nef", -1, at=11, base=8.0), shot("s2.nef", 1, at=27, base=8.0)]
    assert len(detect_sequences(frames)) == 1


def test_far_apart_frames_are_not_grouped():
    frames = [shot("a.nef", 0, at=0), shot("b.nef", -1, at=60), shot("c.nef", 1, at=120)]
    assert detect_sequences(frames) == []


def test_regular_interval_cadence_is_not_trusted():
    frames = ladder("t", [0, -1, 1], gap=10.0)
    groups = detect_sequences(frames)
    assert not groups or groups[0].status == sd.STATUS_UNCERTAIN


def test_same_exposure_burst_is_not_a_bracket():
    assert detect_sequences(ladder("b", [0, 0, 0, 0, 0])) == []


def test_iso_only_ladder_is_found():
    frames = [
        Frame("i0.arw", 0.0, 1 / 60, 5.6, 400.0),
        Frame("i1.arw", 0.4, 1 / 60, 5.6, 200.0),
        Frame("i2.arw", 0.8, 1 / 60, 5.6, 800.0),
    ]
    group = detect_sequences(frames)[0]
    assert group.varies == ("iso",)


def test_constant_ev_aperture_stepping_is_not_called_a_bracket():
    frames = [
        Frame("d0.pef", 0.0, 1 / 125, 8.0, 100.0),
        Frame("d1.pef", 0.4, 1 / 250, 5.6, 100.0),
        Frame("d2.pef", 0.8, 1 / 62.5, 11.0, 100.0),
    ]
    group = detect_sequences(frames)[0]
    assert group.kind == sd.KIND_CONSTANT_EV and group.status == sd.STATUS_UNCERTAIN


def test_scene_check_can_reject_and_confirm():
    frames = ladder("a", [0, -1, 1])
    assert detect_sequences(frames, scene_check=lambda paths: 0.1) == []
    assert detect_sequences(frames, scene_check=lambda paths: 0.9)[0].status == sd.STATUS_CONFIRMED
    undecided = detect_sequences(frames, scene_check=lambda paths: None)[0]
    assert undecided.status == sd.STATUS_UNCONFIRMED


def test_raw_jpeg_pairs_count_as_one_exposure():
    frames = []
    for i, r in enumerate([0, -1, 1]):
        frames.append(shot(f"IMG_{i}.NEF", r, at=i * 0.5))
        frames.append(shot(f"IMG_{i}.JPG", r, at=i * 0.5 + 0.05))
    group = detect_sequences(frames)[0]
    assert group.exposures == 3 and len(group.paths) == 6


def test_different_lens_or_camera_blocks_grouping():
    frames = ladder("a", [0, -1, 1])
    frames[2] = sd.replace(frames[2], lens="other")
    frames[0] = sd.replace(frames[0], lens="kit")
    assert detect_sequences(frames) == []


def test_maker_tag_raises_and_lowers_confidence():
    base = detect_sequences(ladder("a", [0, -1, 1]))[0].confidence
    on = detect_sequences(ladder("a", [0, -1, 1], maker_bracket=True))[0].confidence
    off = detect_sequences(ladder("a", [0, -1, 1], maker_bracket=False))[0].confidence
    assert on > base > off


def test_missing_exposure_data_gives_no_group():
    frames = [Frame("x0", 0.0), Frame("x1", 0.5), Frame("x2", 1.0)]
    assert detect_sequences(frames) == []


def test_backwards_timestamp_splits_a_run():
    frames = ladder("a", [0, -1, 1])
    frames[1] = sd.replace(frames[1], taken_at=-5.0)
    assert detect_sequences(frames) == []
