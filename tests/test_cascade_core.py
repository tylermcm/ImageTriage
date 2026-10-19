"""The stack-first culling cascade core (image_triage/cascade): rules, the review-driven corrections, and a golden run on the reviewed bursts."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from image_triage.cascade import CascadeSettings, FrameFeatures, FrameRole, run_cascade
from image_triage.cascade.flags import flag_stack
from image_triage.cascade.stacking import complete_linkage, hamming, split_into_stacks

GOLDEN = Path(__file__).parent / "fixtures" / "cascade_golden.json"


def bits(count: int) -> int:
    """A hash ``count`` bits away from hash 0, and |a - b| bits away from another such hash."""
    return (1 << count) - 1


def frame(key: str, *, distance: int = 0, sharp: float = 100.0, clipped: float = 0.01, extremes: float = 0.01, blink: bool = False) -> FrameFeatures:
    return FrameFeatures(key, bits(distance), sharp, clipped, extremes, blink)


def scorer(values: dict[str, float], calls: list[list[str]] | None = None):
    def score(keys):
        if calls is not None:
            calls.append(list(keys))
        return {k: values[k] for k in keys}

    return score


def roles(result):
    return {d.key: d.role for d in result.decisions}


# -- stacking ------------------------------------------------------------------------------------------------------------------------------


def test_a_large_consecutive_jump_starts_a_new_stack_and_a_small_one_does_not():
    frames = [frame("a", distance=0), frame("b", distance=4), frame("c", distance=4 + 18), frame("d", distance=4 + 18 + 17)]
    assert split_into_stacks(frames, CascadeSettings()) == [[0, 1], [2, 3]]


def test_stacks_are_judged_on_consecutive_frames_not_distance_from_the_first():
    # Slowly drifting frames never jump by 18 between neighbours, so they stay one stack however far the ends are apart.
    frames = [frame(str(i), distance=i * 10) for i in range(5)]
    assert split_into_stacks(frames, CascadeSettings()) == [[0, 1, 2, 3, 4]]


def test_complete_linkage_does_not_chain_frames_that_are_far_apart():
    frames = [frame("a", distance=0), frame("b", distance=6), frame("c", distance=12)]
    assert complete_linkage([0, 1, 2], frames, 8) == [[0, 1], [2]]
    assert hamming(frames[0].phash, frames[2].phash) == 12


# -- pass 1 flags --------------------------------------------------------------------------------------------------------------------------


def test_soft_is_judged_against_the_stack_best():
    frames = [frame("sharp", sharp=300), frame("ok", sharp=150), frame("soft", sharp=100)]
    flags = flag_stack([0, 1, 2], frames, CascadeSettings())
    assert flags[2] == ("soft",) and flags[0] == () and flags[1] == ()


def test_a_zero_clipped_baseline_does_not_flag_slightly_clipped_siblings():
    # The owner's review found 4% clipped flagged against a 0% sibling. The absolute floor stops that.
    frames = [frame("a", clipped=0.0), frame("b", clipped=0.04), frame("c", clipped=0.21)]
    flags = flag_stack([0, 1, 2], frames, CascadeSettings())
    assert flags[1] == () and flags[2] == ("clipped",)


def test_blink_is_flagged_and_rejects():
    result = run_cascade([frame("a"), frame("b", blink=True)])
    assert roles(result)["b"] is FrameRole.REJECTED
    assert result.decisions_by_key["b"].flags == ("blink",)


def test_a_single_frame_stack_is_never_flagged_soft_or_clipped():
    assert flag_stack([0], [frame("only", sharp=1.0, clipped=0.3)], CascadeSettings())[0] == ()


def test_a_blown_out_frame_among_normal_ones_is_tagged_and_rejected_as_clipped():
    result = run_cascade([frame("a", clipped=0.02), frame("b", clipped=0.45)])
    assert result.decisions_by_key["b"].flags == ("clipped", "blown_out")
    assert roles(result) == {"a": FrameRole.FINAL, "b": FrameRole.REJECTED}


def test_a_stack_where_every_frame_is_blown_out_gets_no_pick():
    result = run_cascade([frame("a", clipped=0.5), frame("b", clipped=0.6), frame("c", clipped=0.47)])
    assert {d.role for d in result.decisions} == {FrameRole.NO_PICK}
    assert result.stacks[0].no_pick and all("blown_out" in d.flags for d in result.decisions)


def test_blown_out_alone_does_not_reject_a_frame():
    # All three are equally and heavily clipped, but one is below the 40% line, so the stack is not a no-pick stack and none is rejected.
    result = run_cascade([frame("a", clipped=0.45), frame("b", clipped=0.46), frame("c", clipped=0.39)])
    assert not result.stacks[0].no_pick
    assert FrameRole.REJECTED not in {d.role for d in result.decisions}


def test_when_every_frame_is_flagged_none_is_dropped_by_pass_1():
    result = run_cascade([frame("a", blink=True), frame("b", blink=True)])
    assert FrameRole.REJECTED not in {d.role for d in result.decisions}
    assert all(d.flags == ("blink",) for d in result.decisions)


# -- duplicates and ranking ----------------------------------------------------------------------------------------------------------------


def test_duplicates_collapse_to_the_most_evenly_exposed_frame():
    frames = [frame("a", distance=0, extremes=0.20), frame("b", distance=2, extremes=0.05), frame("c", distance=3, extremes=0.12)]
    result = run_cascade(frames)
    assert roles(result) == {"a": FrameRole.DUPLICATE, "b": FrameRole.FINAL, "c": FrameRole.DUPLICATE}
    assert result.decisions_by_key["a"].duplicate_of == "b"


def test_an_exposure_tie_keeps_the_earliest_frame():
    result = run_cascade([frame("a", extremes=0.1), frame("b", extremes=0.1)])
    assert roles(result) == {"a": FrameRole.FINAL, "b": FrameRole.DUPLICATE}


def test_rankers_only_score_the_survivors_of_the_cheaper_passes():
    # Five distinct groups (9 bits apart, so no collapse) in one stack (jumps under 18), the last one blinking.
    frames = [frame(str(i), distance=i * 9, blink=(i == 4)) for i in range(5)]
    maniqa_calls: list[list[str]] = []
    aesthetic_calls: list[list[str]] = []
    result = run_cascade(
        frames,
        maniqa=scorer({"0": 5, "1": 4, "2": 3, "3": 1, "4": 9}, maniqa_calls),
        aesthetic=scorer({"0": 1, "1": 9, "2": 5, "3": 8, "4": 7}, aesthetic_calls),
    )
    assert maniqa_calls == [["0", "1", "2", "3"]]  # the blinking frame never reaches a ranker
    assert aesthetic_calls == [["0", "1", "2"]]  # only MANIQA's top three reach the last ranker
    assert result.scored_by_maniqa == 4 and result.scored_by_aesthetic == 3
    assert roles(result) == {
        "0": FrameRole.CUT_BY_RANKING,
        "1": FrameRole.FINAL,
        "2": FrameRole.FINAL,
        "3": FrameRole.CUT_BY_MANIQA,
        "4": FrameRole.REJECTED,
    }
    assert (result.decisions_by_key["1"].rank, result.decisions_by_key["2"].rank) == (1, 2)


def test_maniqa_is_not_called_when_it_could_not_cut_anything():
    frames = [frame(str(i), distance=i * 9) for i in range(3)]
    maniqa_calls: list[list[str]] = []
    run_cascade(frames, maniqa=scorer({"0": 1, "1": 2, "2": 3}, maniqa_calls), aesthetic=scorer({"0": 1, "1": 2, "2": 3}))
    assert maniqa_calls == []


def test_without_rankers_every_representative_is_final_and_unranked():
    result = run_cascade([frame(str(i), distance=i * 9) for i in range(4)])
    assert all(d.role is FrameRole.FINAL and d.rank is None for d in result.decisions)
    assert result.scored_by_maniqa == result.scored_by_aesthetic == 0


def test_rankers_run_per_stack_and_the_second_stack_is_independent():
    frames = [frame("a1", distance=0), frame("a2", distance=9), frame("b1", distance=30), frame("b2", distance=39)]
    result = run_cascade(frames, aesthetic=scorer({"a1": 1, "a2": 2, "b1": 9, "b2": 8}))
    assert [s.keys for s in result.stacks] == [("a1", "a2"), ("b1", "b2")]
    assert result.stacks[1].split_jump >= 18 and result.stacks[0].split_jump is None
    assert result.decisions_by_key["a2"].rank == 1 and result.decisions_by_key["b1"].rank == 1


def test_empty_burst_and_duplicate_keys():
    assert run_cascade([]).decisions == ()
    with pytest.raises(ValueError):
        run_cascade([frame("a"), frame("a")])


# -- golden: the bursts the owner reviewed -------------------------------------------------------------------------------------------------


def _burst_cases():
    data = json.loads(GOLDEN.read_text(encoding="utf-8"))
    return [pytest.param(b, id=f"{b['set']}-{b['id']}") for b in data["bursts"]]


@pytest.mark.parametrize("burst", _burst_cases())
def test_the_core_reproduces_the_reviewed_decisions(burst):
    frames = [FrameFeatures(f["key"], int(f["phash"], 16), f["centre_sharpness"], f["clipped"], f["extremes"], f["blink"]) for f in burst["frames"]]
    maniqa = {f["key"]: f["maniqa"] for f in burst["frames"]}
    aesthetic = {f["key"]: f["aesthetic"] for f in burst["frames"]}
    result = run_cascade(frames, maniqa=scorer(maniqa), aesthetic=scorer(aesthetic))
    keys = [f["key"] for f in burst["frames"]]

    assert [list(s.keys) for s in result.stacks] == [[keys[i] for i in s["indices"]] for s in burst["stacks"]]
    for ref, got in zip(burst["stacks"], result.stacks):
        assert got.no_pick == ref["no_pick"]
        assert got.split_jump == ref["split_jump"]

    by_key = result.decisions_by_key
    for ref in burst["stacks"]:
        if ref["no_pick"]:
            assert all(by_key[keys[i]].role is FrameRole.NO_PICK for i in ref["indices"])
            continue
        final = sorted((by_key[keys[i]] for i in ref["indices"] if by_key[keys[i]].role is FrameRole.FINAL), key=lambda d: d.rank or 0)
        assert [d.key for d in final] == [keys[i] for i in ref["final"]]
        assert {keys[i] for i in ref["indices"] if by_key[keys[i]].role is FrameRole.CUT_BY_MANIQA} == {keys[i] for i in ref["maniqa_cut"]}
        assert {keys[i] for i in ref["indices"] if by_key[keys[i]].role is FrameRole.REJECTED} == {keys[i] for i in ref["rejected"]}
        assert {keys[int(i)]: keys[j] for i, j in ref["duplicate_of"].items()} == {
            d.key: d.duplicate_of for d in (by_key[keys[i]] for i in ref["indices"]) if d.role is FrameRole.DUPLICATE
        }
        for i in ref["indices"]:
            expected = tuple(f.replace(" ", "_") for f in ref["flags"][str(i)])
            assert by_key[keys[i]].flags == expected


def test_the_golden_fixture_covers_both_reviewed_sets():
    data = json.loads(GOLDEN.read_text(encoding="utf-8"))
    sets: dict[str, int] = {}
    for b in data["bursts"]:
        sets[b["set"]] = sets.get(b["set"], 0) + 1
    assert sets == {"unrated": 45, "marked": 63}
