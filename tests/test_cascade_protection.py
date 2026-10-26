"""A winner the person already marked: kept, and carrying none of the flags they have overruled. The explanations the cascade writes say what actually happened."""
from __future__ import annotations

import os
from types import SimpleNamespace

from image_triage.cascade.folder import ROLE_DUPLICATE, ROLE_FINAL, ROLE_FLAGGED, ROLE_PICK, FolderFrame, FolderResult, FrameOutcome, run_folder
from image_triage.cascade.model import FrameFeatures
from image_triage.cascade.task import CascadeRunTask


def outcome(path: str, role: str, flags: tuple[str, ...] = (), summary: str = "x") -> FrameOutcome:
    return FrameOutcome(path, role, flags, summary, 0, 3, 0, 1, 3, 1, None, None)


def protect(paths: list[str], outcomes: list[FrameOutcome]) -> FolderResult:
    result = FolderResult()
    result.outcomes = {o.path: o for o in outcomes}
    stub = SimpleNamespace(protected=frozenset(os.path.normcase(os.path.abspath(p)) for p in paths))
    CascadeRunTask._apply_protection(stub, result)
    return result


def test_a_protected_frame_that_was_flagged_loses_the_flags_and_becomes_a_keeper() -> None:
    for role in (ROLE_FLAGGED, "rejected", ROLE_DUPLICATE):
        result = protect(["/a.jpg"], [outcome("/a.jpg", role, ("blink",))])
        kept = result.outcomes["/a.jpg"]
        assert kept.role == ROLE_FINAL and kept.flags == () and "already marked" in kept.summary


def test_a_protected_keeper_that_still_carried_a_flag_is_cleaned_too() -> None:
    # A frame can be a plain keeper and still carry `blown_out` (blown out alone does not reject): the winner mark overrules that as well.
    result = protect(["/a.jpg"], [outcome("/a.jpg", ROLE_FINAL, ("blown_out",))])
    assert result.outcomes["/a.jpg"].flags == () and result.outcomes["/a.jpg"].role == ROLE_FINAL
    assert "already marked" in result.outcomes["/a.jpg"].summary


def test_a_protected_pick_stays_the_pick() -> None:
    clean = outcome("/a.jpg", ROLE_PICK, (), "Best of this stack of 3 after the cleanup and ranking passes.")
    assert protect(["/a.jpg"], [clean]).outcomes["/a.jpg"] is clean  # nothing adverse: untouched
    flagged = protect(["/a.jpg"], [outcome("/a.jpg", ROLE_PICK, ("blown_out",))]).outcomes["/a.jpg"]
    assert flagged.role == ROLE_PICK and flagged.flags == ()


def test_frames_that_are_not_protected_keep_their_flags() -> None:
    result = protect(["/b.jpg"], [outcome("/a.jpg", ROLE_FLAGGED, ("blink",)), outcome("/b.jpg", ROLE_FLAGGED, ("soft",))])
    assert result.outcomes["/a.jpg"].flags == ("blink",)
    assert result.outcomes["/b.jpg"].flags == ()


def feature(key: str, *, phash: int = 0, sharp: float = 100.0, blink: bool = False) -> FrameFeatures:
    return FrameFeatures(key, phash, sharp, 0.0, 0.0, blink)


def test_the_explanation_for_an_all_flagged_stack_does_not_claim_they_share_one_problem() -> None:
    # One blinks, the other is soft: different problems, both flagged.
    frames = [FolderFrame("a", feature("a", sharp=100.0, blink=True), 0.0), FolderFrame("b", feature("b", sharp=1.0), 1.0)]
    summaries = [o.summary for o in run_folder(frames).outcomes.values() if o.role == ROLE_FLAGGED]
    assert summaries and all("same problem" not in text for text in summaries)
    assert all("was flagged, so none could be set aside" in text for text in summaries)


def test_a_duplicates_explanation_names_the_frame_kept_without_calling_it_better_exposed() -> None:
    frames = [FolderFrame("a.jpg", feature("a.jpg"), 0.0), FolderFrame("b.jpg", feature("b.jpg", phash=1), 1.0)]
    duplicates = [o for o in run_folder(frames).outcomes.values() if o.role == ROLE_DUPLICATE]
    assert len(duplicates) == 1
    assert duplicates[0].summary.startswith("Near-duplicate of a.jpg, the frame kept for this group.")
    assert "better exposed" not in duplicates[0].summary
