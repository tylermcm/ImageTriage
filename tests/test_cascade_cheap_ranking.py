"""A cull without the AI rankers (no usable GPU, a Mac without the AI runtime) still picks within a stack: by sharpness and exposure.

Before, such a stack kept every frame that was not a near-exact hash twin, so a burst of a slowly moving animal left dozens of keeps."""
from __future__ import annotations

from image_triage.cascade.folder import FolderFrame, run_folder
from image_triage.cascade.model import CascadeSettings, FrameFeatures, FrameRole
from image_triage.cascade.pipeline import cheap_score, run_cascade


def burst(sharpness, extremes=None):
    """One stack of frames: consecutive hashes 10 apart (more than the near-duplicate distance, less than a new stack's jump)."""
    extremes = extremes or [0.0] * len(sharpness)
    return [FrameFeatures(f"f{i}.nef", ((1 << 10) - 1) << (i * 5), s, 0.01, e) for i, (s, e) in enumerate(zip(sharpness, extremes))]



def test_without_rankers_a_stack_keeps_its_two_sharpest_frames():
    frames = burst([200, 300, 180, 280, 220, 150])
    result = run_cascade(frames, CascadeSettings())
    assert len(result.stacks) == 1
    kept = [(d.key, d.rank) for d in result.decisions if d.role is FrameRole.FINAL]
    assert kept == [("f1.nef", 1), ("f3.nef", 2)]
    cut = {d.key for d in result.decisions if d.role is FrameRole.CUT_BY_RANKING}
    assert cut == {"f0.nef", "f2.nef", "f4.nef", "f5.nef"}
    assert all(d.ranked_by == "sharpness" for d in result.decisions)


def test_kept_per_stack_sets_how_many_and_the_switch_turns_it_off():
    frames = burst([200, 300, 180, 280, 220, 150])
    three = run_cascade(frames, CascadeSettings(final_keep=3))
    assert [d.key for d in three.decisions if d.role is FrameRole.FINAL] == ["f1.nef", "f3.nef", "f4.nef"]
    off = run_cascade(frames, CascadeSettings(rank_without_ai=False))
    assert all(d.role is FrameRole.FINAL and d.rank is None and d.ranked_by == "" for d in off.decisions), "as before: every frame kept, unranked"


def test_a_badly_exposed_frame_loses_to_an_equally_sharp_even_one():
    frames = burst([200, 200, 200], extremes=[0.4, 0.0, 0.2])
    kept = [d.key for d in run_cascade(frames, CascadeSettings(final_keep=1)).decisions if d.role is FrameRole.FINAL]
    assert kept == ["f1.nef"]
    assert cheap_score(frames[0]) < cheap_score(frames[2]) < cheap_score(frames[1])


def test_the_ai_rankers_still_decide_when_they_run():
    frames = burst([200, 300, 180, 280, 220, 150])
    favourite = {"f5.nef": 9.0, "f0.nef": 8.0}
    result = run_cascade(frames, CascadeSettings(), aesthetic=lambda keys: {k: favourite.get(k, 0.0) for k in keys})
    assert [d.key for d in result.decisions if d.role is FrameRole.FINAL] == ["f0.nef", "f5.nef"]
    assert all(d.ranked_by == "" for d in result.decisions)


def test_a_stack_of_one_and_flagged_frames_are_left_as_they_were():
    single = run_cascade(burst([100]), CascadeSettings())
    assert single.decisions[0].role is FrameRole.FINAL and single.decisions[0].rank is None
    soft = burst([300, 20, 290])                               # the 20 is soft against its stack: flagged, never ranked
    result = run_cascade(soft, CascadeSettings())
    assert result.decisions[1].role is FrameRole.REJECTED


def test_the_review_says_the_stack_was_ranked_by_sharpness():
    frames = [FolderFrame(f.key, f, float(i)) for i, f in enumerate(burst([200, 300, 180, 280]))]
    outcomes = run_folder(frames).outcomes
    assert outcomes["f1.nef"].summary.startswith("Sharpest of this stack of 4")
    assert outcomes["f3.nef"].summary.startswith("Kept: next sharpest")
    assert outcomes["f0.nef"].summary.startswith("Less sharp than the kept frames")
    assert outcomes["f1.nef"].rank == 1


def test_the_review_footer_says_the_first_choices_came_from_sharpness():
    from types import SimpleNamespace

    from image_triage.qml_shell.cull_model import CullModel

    footer = CullModel._review_footer
    model = SimpleNamespace(_real=True, _picks={"a.jpg"}, _run_notes=[], _run_device="")
    assert footer(model, {"ranked": False}).startswith("1st choice is the sharpest")
    model._picks = set()
    assert footer(model, {"ranked": False}).startswith("Cleanup only")
