"""Choose a folder's best photos from the AI report that is already saved for it. Nothing is analysed again. No Qt.

Only scores the report really holds are used (see `ai_score_details`):

* `appeal`  Q-ReAlign, 0..1. The cascade keeps it for the frames the appeal ranker read (the survivors of a stack); the native engine
  for the shortlist of each comparison set.
* `quality` MANIQA, 0..1. Kept for the frames the quality ranker read.
* `sharpness` the preview's Laplacian variance. It has no fixed scale, so it only orders the frames of one stack, never the folder.
* `rank`    the native engine's own place in its comparison set (1 is best). The cascade's CSV does not hold a ranking of its own.

A strategy that needs a score the report does not have is refused in plain words; a photo without the chosen score is never picked,
and the plan says how many were left out for that reason. Photos the person has rejected are not offered.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Mapping, Sequence

TOP_OVERALL = "ai_overall"
TOP_PER_STACK = "ai_stack"
TOP_PER_PERSON = "ai_person"

STRATEGIES = (
    (TOP_OVERALL, "Top photos overall", "The highest-scoring photos of the folder, whatever stack they are in."),
    (TOP_PER_STACK, "Top photos per stack", "The highest-scoring photos of every stack or burst of similar shots."),
    (TOP_PER_PERSON, "Top photos per person", "The highest-scoring photos of each person found in this folder."),
)
# key: (label, whether it can be compared across the whole folder, whether a higher value is better)
MEASURES = {
    "appeal": ("Appeal (Q-ReAlign)", True, True),
    "quality": ("Quality (MANIQA)", True, True),
    "sharpness": ("Sharpness within the stack", False, True),
    "rank": ("The engine's own ranking", False, False),
}
_NO_REPORT = ("This folder has no saved AI results. Analyze it in the Cull window first; choosing from AI results reads what that analysis saved "
              "and never runs it again.")


@dataclass(frozen=True)
class Candidate:
    path: str
    stack: str                              # frames of one stack or comparison set share it; a lone frame has its own
    values: Mapping[str, float] = field(default_factory=dict)       # the measures recorded for it (keys of MEASURES)


@dataclass(frozen=True)
class Plan:
    paths: tuple[str, ...] = ()
    problem: str = ""                       # why nothing can be chosen ("" when something was)
    note: str = ""                          # what was left out, and why


def candidates_from_bundle(bundle, rows: Sequence[Mapping[str, object]]) -> list[Candidate]:
    """One candidate per photo of `rows` ({"path", "record"?, "decision"?}) that the saved report decided. Rejected photos are left out."""
    from .ai_results import find_ai_result_for_record

    out: list[Candidate] = []
    if bundle is None:
        return out
    for row in rows:
        if row.get("decision") == "rej":
            continue
        path = str(row["path"])
        record = row.get("record")
        result = find_ai_result_for_record(bundle, record, preferred_path=path) if record is not None else bundle.result_for_path(path)
        if result is None:
            continue
        values: dict[str, float] = {}
        native = getattr(result, "native_payload", None)
        if native is not None:
            scores = native.get("scores") if isinstance(native.get("scores"), Mapping) else {}
            for key, name in (("appeal", "appeal"), ("quality", "maniqa"), ("sharpness", "sharpness")):
                value = scores.get(name)
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    values[key] = float(value)
            rank, size = native.get("rank_in_set"), native.get("set_size")
            if isinstance(rank, int) and not isinstance(rank, bool) and isinstance(size, int) and size > 1:
                values["rank"] = float(rank)
            stack = str(native.get("comparison_set") or "alone:" + path)
        else:
            recorded = dict(getattr(result, "measures", ()) or ())
            for key, name in (("appeal", "aesthetic_score"), ("quality", "maniqa_score"), ("sharpness", "centre_sharpness")):
                if name in recorded:
                    values[key] = float(recorded[name])
            stack = result.group_id if result.group_id and result.group_id != "unassigned" else "alone:" + path
        out.append(Candidate(path, stack, values))
    return out


def coverage(candidates: Iterable[Candidate]) -> dict[str, int]:
    """How many candidates hold each measure."""
    counts = {key: 0 for key in MEASURES}
    for candidate in candidates:
        for key in candidate.values:
            if key in counts:
                counts[key] += 1
    return counts


def default_measure(candidates: Sequence[Candidate], strategy: str) -> str:
    """The measure to start on: the first one, in the order above, that this report holds and this strategy can use ("" for none)."""
    counts = coverage(candidates)
    return next((key for key, (_label, overall, _up) in MEASURES.items() if counts[key] and (overall or strategy == TOP_PER_STACK)), "")


def _best(members: Sequence[Candidate], measure: str, count: int) -> list[Candidate]:
    higher = MEASURES[measure][2]
    scored = [c for c in members if measure in c.values]
    scored.sort(key=lambda c: (-c.values[measure] if higher else c.values[measure], c.path.casefold()))
    return scored[:count]


def plan(candidates: Sequence[Candidate], strategy: str, measure: str, count: int, *, has_report: bool = True,
         people: Mapping[str, Iterable[str]] | None = None) -> Plan:
    """The photos to choose. `people` maps a person's label to the paths of the photos that person is in (None: no people data)."""
    if not has_report:
        return Plan(problem=_NO_REPORT)
    if not candidates:
        return Plan(problem="The saved AI results do not cover any of the photos shown (or all of them are Rejects).")
    if strategy not in (TOP_OVERALL, TOP_PER_STACK, TOP_PER_PERSON):
        return Plan(problem="Unknown way of choosing.")
    if measure not in MEASURES:
        return Plan(problem="The saved AI results hold no score this can rank by. Scores are kept when the quality or appeal ranker runs; "
                            "results saved before scores were kept have none. Analyze the folder again to record them.")
    label, overall, _up = MEASURES[measure]
    count = max(1, int(count))
    have = sum(1 for c in candidates if measure in c.values)
    if not have:
        missing = {"appeal": "They are recorded when the appeal ranker runs.", "quality": "They are recorded when the quality ranker runs.",
                   "sharpness": "They are recorded by the cleanup pass.", "rank": "Only the native culling engine records a ranking."}[measure]
        return Plan(problem=f"The saved AI results hold no {label} values for these photos. {missing} "
                            "Results saved before scores were kept have none; analyze the folder again to record them.")
    if strategy != TOP_PER_STACK and not overall:
        return Plan(problem=f"{label} only compares the photos of one stack, so it cannot rank the whole folder or a person's photos. "
                            "Choose per stack, or rank by a score that is on one scale for every photo.")
    without = len(candidates) - have
    left = f"{without} of {len(candidates)} photos have no {label} value and cannot be chosen." if without else ""
    if strategy == TOP_OVERALL:
        return Plan(tuple(c.path for c in _best(candidates, measure, count)), note=left)
    if strategy == TOP_PER_STACK:
        stacks: dict[str, list[Candidate]] = {}
        for candidate in candidates:
            stacks.setdefault(candidate.stack, []).append(candidate)
        chosen: list[Candidate] = []
        empty = 0
        for members in stacks.values():
            best = _best(members, measure, count)
            empty += not best
            chosen.extend(best)
        notes = [left] if left else []
        if empty:
            notes.append(f"{empty} of {len(stacks)} stacks have no photo with that value and give none.")
        return Plan(tuple(c.path for c in chosen), note=" ".join(notes))
    if people is None or not people:
        return Plan(problem="No people have been found in this folder, so there is nobody to choose for. People are found by an analysis "
                            "with the face model installed (AI Center), and listed under Faces.")
    by_path = {c.path: c for c in candidates}
    picked: dict[str, None] = {}
    nobody = 0
    for _name, paths in people.items():
        best = _best([by_path[p] for p in paths if p in by_path], measure, count)
        nobody += not best
        for candidate in best:
            picked.setdefault(candidate.path)
    notes = [left] if left else []
    if nobody:
        notes.append(f"{nobody} of {len(people)} people have no photo with that value and get none.")
    if not picked:
        return Plan(problem=f"None of the photos with people in them has a {label} value.", note=" ".join(notes))
    return Plan(tuple(picked), note=" ".join(notes))
