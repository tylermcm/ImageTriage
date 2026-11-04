"""The ledger of a cull run: one row per frame saying where it ended up, and a summary whose counts are made from those rows.

A run reports its result in several places (the role counts in the export summary, the Cull window's tiles, the Apply page, the grid's filters), each
counting a little differently. Adding counters from different stages cannot be trusted, because the sets they count overlap or leave frames out. Here
every frame gets exactly one `disposition` (the dispositions are disjoint and add up to the frames of the folder, which `summarize` checks), plus
`reasons` that may overlap (why it was flagged), and `ui_flagged`: whether the Cull window lists the frame as something the run found (a flagged frame,
a frame no pick could be made from, or a near-duplicate). `diff_ledgers` lists the frames two runs disagree about, so two counts that differ can be
traced to the frames that make the difference.
"""

from __future__ import annotations

import csv
import io
import json
import os
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from .folder import (
    ROLE_BRACKET,
    ROLE_CUT,
    ROLE_DUPLICATE,
    ROLE_FINAL,
    ROLE_FLAGGED,
    ROLE_NO_PICK,
    ROLE_PICK,
    ROLE_REJECTED,
    FolderResult,
    FrameOutcome,
)

LEDGER_FILENAME = "cull_ledger.csv"
LEDGER_SUMMARY_FILENAME = "cull_ledger_summary.json"

# One disposition per frame. KEPT_* frames stay as they are; the rest are what the run set aside or could not decide.
DISPOSITION_OF_ROLE = {
    ROLE_PICK: "KEPT_PICK",                # ranked first of several survivors
    ROLE_FINAL: "KEPT",                    # survived every pass
    ROLE_BRACKET: "KEPT_BRACKET",          # an exposure bracket, kept whole and never compared
    ROLE_FLAGGED: "FLAGGED_KEPT",          # flagged, but kept because every frame of its stack was flagged (or it was alone)
    ROLE_NO_PICK: "NO_PICK",               # every frame of its stack is blown out
    ROLE_REJECTED: "REJECTED",             # flagged and dimmed
    ROLE_DUPLICATE: "DUPLICATE",           # a near-duplicate of a better frame
    ROLE_CUT: "CUT_BY_RANKING",            # ranked below the frames kept from its stack
}
DISPOSITIONS = tuple(DISPOSITION_OF_ROLE.values()) + ("UNDECIDED",)   # UNDECIDED: the run gave the frame no outcome (unreadable or never reached)
# What the Cull window lists as found by the run (its Flagged and Near-duplicates tiles). Frames flagged but kept, and frames with no pick, are in it.
UI_FLAGGED = frozenset({"REJECTED", "FLAGGED_KEPT", "NO_PICK", "DUPLICATE"})
TECHNICAL_FLAGS = ("clipped", "soft", "blown_out", "blink")

COLUMNS = ("file_path", "file_name", "disposition", "ui_flagged", "reasons", "protected", "duplicate_of", "duplicate_by", "burst", "stack", "stack_size",
           "stack_rank", "rank", "bracket", "split_jump")


def disposition_of(outcome: FrameOutcome | None) -> str:
    return "UNDECIDED" if outcome is None else DISPOSITION_OF_ROLE.get(outcome.role, "UNDECIDED")


def reasons_of(outcome: FrameOutcome | None) -> list[str]:
    """Why a frame was set aside or flagged: its technical flags, and the check that folded it when it is a duplicate. May hold several."""
    if outcome is None:
        return []
    reasons = list(outcome.flags)
    if outcome.duplicate_of:
        reasons.append("repeat:" + (outcome.duplicate_by or "unknown"))
    if outcome.role == ROLE_NO_PICK and "blown_out" not in reasons:
        reasons.append("no_pick")
    if outcome.role == ROLE_CUT:
        reasons.append("ranked_below")
    return reasons


def build_ledger(outcomes: Mapping[str, FrameOutcome], order: Sequence[str], *, protected: Iterable[str] = ()) -> list[dict[str, object]]:
    """One row for every path in ``order`` (the folder's frames), whether or not the run decided it."""
    held = {os.path.normcase(os.path.abspath(p)) for p in protected}
    rows: list[dict[str, object]] = []
    for path in order:
        outcome = outcomes.get(path)
        disposition = disposition_of(outcome)
        rows.append({
            "file_path": path,
            "file_name": Path(path).name,
            "disposition": disposition,
            "ui_flagged": int(disposition in UI_FLAGGED),
            "reasons": "|".join(reasons_of(outcome)),
            "protected": int(os.path.normcase(os.path.abspath(path)) in held),
            "duplicate_of": Path(outcome.duplicate_of).name if outcome is not None and outcome.duplicate_of else "",
            "duplicate_by": (outcome.duplicate_by or "") if outcome is not None else "",
            "burst": "" if outcome is None else outcome.burst_index,
            "stack": "" if outcome is None else outcome.stack_index,
            "stack_size": "" if outcome is None else outcome.stack_size,
            "stack_rank": "" if outcome is None else outcome.stack_rank,
            "rank": "" if outcome is None or outcome.rank is None else outcome.rank,
            "bracket": "" if outcome is None or outcome.burst_index >= 0 else -outcome.burst_index,
            "split_jump": "" if outcome is None or outcome.split_jump is None else outcome.split_jump,
        })
    return rows


def summarize(rows: Sequence[Mapping[str, object]]) -> dict[str, object]:
    """Counts made from the rows alone. The dispositions are disjoint, so they must add up to the number of rows; a mismatch is reported, not hidden."""
    counts = {name: 0 for name in DISPOSITIONS}
    ui_flagged = protected = 0
    reasons: dict[str, int] = {}
    overlap: dict[str, int] = {}
    for row in rows:
        name = str(row["disposition"])
        counts[name] = counts.get(name, 0) + 1
        ui_flagged += int(row["ui_flagged"])
        protected += int(row["protected"])
        held = [r for r in str(row["reasons"]).split("|") if r]
        for reason in held:
            reasons[reason] = reasons.get(reason, 0) + 1
        if name == "DUPLICATE" and any(r in TECHNICAL_FLAGS for r in held):
            overlap["DUPLICATE_AND_TECHNICAL_FLAG"] = overlap.get("DUPLICATE_AND_TECHNICAL_FLAG", 0) + 1
    total = len(rows)
    return {
        "frames": total,
        "dispositions": counts,
        "dispositions_add_up": sum(counts.values()) == total,
        "ui_flagged": ui_flagged,
        "ui_flagged_means": sorted(UI_FLAGGED),
        "protected_winners": protected,
        "reasons": dict(sorted(reasons.items())),
        "overlaps": overlap,
    }


def write_ledger(report_dir: str | Path, rows: Sequence[Mapping[str, object]], result: FolderResult | None = None, *, settings=None,
                 ranked_with: Sequence[str] = (), blink_checked: bool = False, note: str = "") -> dict[str, Path]:
    """Write the ledger CSV and its summary JSON into ``report_dir``. The summary records the settings and what ran, so two ledgers can be compared."""
    folder = Path(report_dir)
    folder.mkdir(parents=True, exist_ok=True)
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=COLUMNS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    csv_path = folder / LEDGER_FILENAME
    csv_path.write_text(buffer.getvalue(), encoding="utf-8")
    summary = summarize(rows)
    summary.update({
        "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "settings": asdict(settings) if settings is not None else {},
        "ranked_with": list(ranked_with),
        "blink_checked": bool(blink_checked),
        "bursts": getattr(result, "bursts", None),
        "stacks": getattr(result, "stacks", None),
        "brackets": getattr(result, "brackets", None),
        "note": note,
    })
    summary_path = folder / LEDGER_SUMMARY_FILENAME
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return {"csv": csv_path, "summary": summary_path}


def read_ledger(path: str | Path) -> list[dict[str, str]]:
    with open(path, newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def diff_ledgers(left: Sequence[Mapping[str, object]], right: Sequence[Mapping[str, object]]) -> dict[str, object]:
    """The frames the two runs disagree about, by file name: those with a different disposition, those only one run has, and the ones the Cull window
    would list in one run and not the other."""
    a = {str(r["file_name"]): r for r in left}
    b = {str(r["file_name"]): r for r in right}
    both = a.keys() & b.keys()
    changed = {name: (a[name]["disposition"], b[name]["disposition"]) for name in sorted(both) if a[name]["disposition"] != b[name]["disposition"]}
    flagged = {name: (int(a[name]["ui_flagged"]), int(b[name]["ui_flagged"])) for name in sorted(both) if int(a[name]["ui_flagged"]) != int(b[name]["ui_flagged"])}
    transitions: dict[str, int] = {}
    for old, new in changed.values():
        key = f"{old} -> {new}"
        transitions[key] = transitions.get(key, 0) + 1
    return {
        "only_in_left": sorted(a.keys() - b.keys()),
        "only_in_right": sorted(b.keys() - a.keys()),
        "changed": changed,
        "transitions": dict(sorted(transitions.items(), key=lambda item: -item[1])),
        "ui_flagged_differs": flagged,
    }


def main(argv: Sequence[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Compare two cull ledgers (cull_ledger.csv): which frames ended up somewhere different.")
    parser.add_argument("left")
    parser.add_argument("right")
    args = parser.parse_args(argv)
    left, right = read_ledger(args.left), read_ledger(args.right)
    result = diff_ledgers(left, right)
    for label, rows in (("left", left), ("right", right)):
        summary = summarize(rows)
        print(f"{label}: {summary['frames']} frames, ui_flagged {summary['ui_flagged']}, {summary['dispositions']}")
    print("transitions (left -> right):")
    for key, count in result["transitions"].items():
        print(f"  {count:5d}  {key}")
    print(f"frames only in left: {len(result['only_in_left'])}, only in right: {len(result['only_in_right'])}")
    print(f"frames the Cull window would list in one run only: {len(result['ui_flagged_differs'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
