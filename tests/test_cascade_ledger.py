from __future__ import annotations

from image_triage.cascade.folder import ROLE_BRACKET, ROLE_CUT, ROLE_DUPLICATE, ROLE_FINAL, ROLE_FLAGGED, ROLE_NO_PICK, ROLE_PICK, ROLE_REJECTED, FolderFrame, FrameOutcome, run_folder
from image_triage.cascade.ledger import DISPOSITION_OF_ROLE, UI_FLAGGED, build_ledger, diff_ledgers, disposition_of, read_ledger, summarize, write_ledger
from image_triage.cascade.model import CascadeSettings, FrameFeatures
from image_triage.qml_shell.cull_real import Outcome, reason_of


def feature(key: str, *, distance: int = 0, sharp: float = 100.0, clipped: float = 0.01, extremes: float = 0.01) -> FrameFeatures:
    return FrameFeatures(key, (1 << distance) - 1, sharp, clipped, extremes, False)


def frame(key: str, at: float | None, **kwargs) -> FolderFrame:
    bracket = kwargs.pop("bracket", None)
    return FolderFrame(key, feature(key, **kwargs), at, bracket)


def outcome(role: str, **kwargs) -> FrameOutcome:
    return FrameOutcome("x", role, kwargs.pop("flags", ()), "", 0, 1, 0, 1, 1, 1, None, None, **kwargs)


def test_every_role_has_one_disposition_and_the_ledger_agrees_with_the_cull_windows_idea_of_flagged():
    assert set(DISPOSITION_OF_ROLE) == {ROLE_PICK, ROLE_FINAL, ROLE_BRACKET, ROLE_FLAGGED, ROLE_NO_PICK, ROLE_REJECTED, ROLE_DUPLICATE, ROLE_CUT}
    for role in DISPOSITION_OF_ROLE:
        flags = ("clipped",) if role in (ROLE_FLAGGED, ROLE_REJECTED) else ()
        window = Outcome(role, flags, "", "c", 1, 1, False)
        assert (disposition_of(outcome(role, flags=flags)) in UI_FLAGGED) == (reason_of(window) is not None), role


def test_dispositions_are_disjoint_and_add_up_to_the_frames_of_the_folder():
    frames = [frame("a", 0.0), frame("b", 600.0), frame("c", 1200.0, distance=40), frame("x1", 2000.0, bracket=0, distance=60), frame("x2", 2000.5, bracket=0, distance=60)]
    result = run_folder(frames, CascadeSettings())
    order = ["a", "b", "c", "x1", "x2", "unread"]                 # "unread" has no outcome at all
    rows = build_ledger(result.outcomes, order)
    summary = summarize(rows)
    assert summary["frames"] == 6 and summary["dispositions_add_up"]
    assert sum(summary["dispositions"].values()) == 6
    assert summary["dispositions"]["UNDECIDED"] == 1 and summary["dispositions"]["KEPT_BRACKET"] == 2
    folded = [r for r in rows if r["disposition"] == "DUPLICATE"]
    assert len(folded) == 1 and folded[0]["duplicate_by"] == "phash" and "repeat:phash" in folded[0]["reasons"]
    assert summary["ui_flagged"] == 1


def test_a_protected_winner_is_marked_and_the_ledger_round_trips_through_its_file(tmp_path):
    frames = [frame("a", 0.0), frame("b", 600.0)]
    result = run_folder(frames, CascadeSettings())
    rows = build_ledger(result.outcomes, ["a", "b"], protected=["b"])
    assert [r["protected"] for r in rows] == [0, 1]
    written = write_ledger(tmp_path, rows, result, settings=CascadeSettings(), ranked_with=("quality",), note="n")
    back = read_ledger(written["csv"])
    assert [r["file_name"] for r in back] == ["a", "b"] and back[1]["protected"] == "1"
    assert "ssim_threshold" in written["summary"].read_text(encoding="utf-8")


def test_diff_names_the_frames_two_runs_disagree_about():
    frames = [frame("a", 0.0), frame("b", 600.0)]
    on = build_ledger(run_folder(frames, CascadeSettings()).outcomes, ["a", "b"])
    off = build_ledger(run_folder(frames, CascadeSettings(repeat_distance=0)).outcomes, ["a", "b"])
    difference = diff_ledgers(off, on)
    assert difference["transitions"] == {"KEPT -> DUPLICATE": 1}
    assert len(difference["ui_flagged_differs"]) == 1 and not difference["only_in_left"]
