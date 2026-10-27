"""Folder-level cascade logic: which frames form a burst, how exposure brackets are kept out, and the words and numbers each outcome carries."""

from __future__ import annotations

from image_triage.cascade.export import build_rows, cluster_id, cluster_reason, frame_score
from image_triage.cascade.folder import FolderFrame, run_folder, segment_bursts
from image_triage.cascade.model import CascadeSettings, FrameFeatures


def feature(key: str, *, distance: int = 0, sharp: float = 100.0, clipped: float = 0.01, extremes: float = 0.01, blink: bool = False) -> FrameFeatures:
    return FrameFeatures(key, (1 << distance) - 1, sharp, clipped, extremes, blink)


def frame(key: str, at: float | None, **kwargs) -> FolderFrame:
    bracket = kwargs.pop("bracket", None)
    return FolderFrame(key, feature(key, **kwargs), at, bracket)


def test_frames_close_in_time_and_in_the_picture_form_a_burst():
    frames = [frame("a", 0.0), frame("b", 1.0, distance=5), frame("c", 3.0, distance=8)]
    assert segment_bursts(frames) == [[0, 1, 2]]


def test_a_long_gap_a_big_picture_change_or_a_missing_time_ends_a_burst():
    assert segment_bursts([frame("a", 0.0), frame("b", 6.0)]) == [[0], [1]]  # more than 5 s apart
    assert segment_bursts([frame("a", 0.0), frame("b", 1.0, distance=21)]) == [[0], [1]]  # picture differs by more than 20
    assert segment_bursts([frame("a", 0.0), frame("b", None)]) == [[0], [1]]  # no capture time: never joined
    assert segment_bursts([frame("a", None), frame("b", None)]) == [[0], [1]]


def test_a_frame_cannot_join_backwards_in_time():
    assert segment_bursts([frame("a", 10.0), frame("b", 9.0)]) == [[0], [1]]


def test_brackets_and_unreadable_frames_end_a_burst_and_are_not_part_of_one():
    frames = [frame("a", 0.0), frame("br1", 0.5, bracket=0), frame("br2", 1.0, bracket=0), frame("b", 1.5), FolderFrame("bad", None, 2.0), frame("c", 2.5)]
    assert segment_bursts(frames) == [[0], [3], [5]]


def test_an_exposure_bracket_is_kept_together_and_never_compared():
    frames = [frame("br1", 0.0, bracket=0, clipped=0.0), frame("br2", 0.2, bracket=0, clipped=0.5), frame("br3", 0.4, bracket=0, clipped=0.9)]
    result = run_folder(frames)
    assert result.brackets == 1 and result.bursts == 0
    assert {o.role for o in result.outcomes.values()} == {"bracket"}
    assert all(o.flags == () and "kept together" in o.summary for o in result.outcomes.values())


def test_every_readable_frame_gets_an_outcome_and_unreadable_ones_are_counted():
    frames = [frame("a", 0.0), FolderFrame("bad", None, 1.0), frame("b", 50.0)]
    result = run_folder(frames)
    assert set(result.outcomes) == {"a", "b"} and result.unreadable == 1 and result.bursts == 2


def test_the_summary_says_why_in_plain_words():
    frames = [frame("a", 0.0), frame("b", 1.0, sharp=10.0, clipped=0.3, blink=True)]
    outcome = run_folder(frames).outcomes["b"]
    assert outcome.role == "rejected"
    assert outcome.summary == "Flagged: eyes closed, softer than its siblings, more blown highlights than its siblings. Dimmed, not removed."


def test_cancelling_stops_between_bursts():
    frames = [frame("a", 0.0), frame("b", 100.0), frame("c", 200.0)]
    state = {"n": 0}

    def cancelled():
        state["n"] += 1
        return state["n"] > 1

    result = run_folder(frames, cancelled=cancelled)
    assert result.cancelled and len(result.outcomes) == 1


def test_scores_order_picks_before_keeps_before_duplicates_before_flagged_frames():
    from image_triage.cascade.folder import FrameOutcome

    def outcome(role, rank=None, stack_rank=1):
        return FrameOutcome("p", role, (), "", 0, 3, 0, 1, 3, stack_rank, None, rank)

    assert frame_score(outcome("pick", 1)) > frame_score(outcome("final", 2)) > frame_score(outcome("final")) > frame_score(outcome("duplicate"))
    assert frame_score(outcome("duplicate")) > frame_score(outcome("cut")) > frame_score(outcome("rejected"))
    assert frame_score(outcome("final", stack_rank=1)) > frame_score(outcome("final", stack_rank=2))


def test_cluster_ids_and_reasons_describe_stacks_brackets_and_singles():
    frames = [frame("a", 0.0), frame("b", 1.0, distance=19)]  # a pHash jump of 19 splits the burst into two stacks
    result = run_folder(frames)
    a, b = result.outcomes["a"], result.outcomes["b"]
    assert cluster_id(a) == "burst-0001-stack-1" and cluster_id(b) == "burst-0001-stack-2"
    assert "Stack 2 of 2 in a burst of 2 frames" in cluster_reason(b) and "picture difference 19" in cluster_reason(b)
    single = run_folder([frame("s", 0.0)]).outcomes["s"]
    assert cluster_reason(single) == "A single frame."
    bracket = run_folder([frame("x", 0.0, bracket=0), frame("y", 0.1, bracket=0)]).outcomes["x"]
    assert cluster_id(bracket) == "bracket-0001" and cluster_reason(bracket) == "Exposure bracket of 2 frames."


def test_rows_follow_the_folder_order_and_skip_undecided_frames():
    frames = [frame("b", 50.0), frame("a", 0.0)]
    result = run_folder(frames, CascadeSettings(repeat_distance=0))     # two identical pictures minutes apart would otherwise be folded as repeats
    rows = build_rows(result.outcomes, ["a", "missing", "b"])
    assert [row["file_path"] for row in rows] == ["a", "b"]
    assert rows[0]["cascade_role"] == "final" and rows[0]["rank_in_cluster"] == 1


def test_a_repeat_of_the_same_scene_minutes_later_is_folded_into_the_first():
    frames = [frame("a", 0.0), frame("b", 600.0)]      # two bursts, identical pictures, ten minutes apart
    result = run_folder(frames)
    roles = {key: outcome.role for key, outcome in result.outcomes.items()}
    assert sorted(roles.values()) == ["duplicate", "final"]
    folded = next(o for o in result.outcomes.values() if o.role == "duplicate")
    assert folded.duplicate_of in ("a", "b") and "Near-duplicate of" in folded.summary
    assert all(o.role == "final" for o in run_folder(frames, CascadeSettings(repeat_distance=0)).outcomes.values())


def test_a_bracket_that_repeats_an_earlier_bracket_is_folded_whole():
    first = [frame("a1", 0.0, bracket=0), frame("a2", 0.5, bracket=0), frame("a3", 1.0, bracket=0)]
    second = [frame("b1", 300.0, bracket=1), frame("b2", 300.5, bracket=1), frame("b3", 301.0, bracket=1)]
    result = run_folder(first + second)
    roles = sorted(o.role for o in result.outcomes.values())
    assert roles == ["bracket"] * 3 + ["duplicate"] * 3


def test_ssim_pass_folds_a_reframed_repeat_that_phash_misses_and_leaves_a_different_picture():
    import numpy as np

    from image_triage.cascade.similarity import aligned_ssim

    rng = np.random.default_rng(1)
    scene = rng.random((120, 180)).astype("float32")
    scene = (scene + np.roll(scene, 1, 0) + np.roll(scene, 1, 1)) / 3        # some texture
    shifted = np.roll(scene, (3, -4), (0, 1)) * 0.6 + 0.1                     # a small reframe and a different exposure
    other = np.random.default_rng(2).random((120, 180)).astype("float32")
    assert aligned_ssim(scene, shifted) > 0.65
    assert aligned_ssim(scene, other) < 0.3
