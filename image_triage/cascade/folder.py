"""Run the cascade over a whole folder: split it into bursts, keep exposure brackets out, decide every frame, and say why in plain words.

Pure logic (no Qt, no models): the extractor supplies features and capture times, the rankers are optional ``Scorer`` callables.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Sequence

from .model import (
    FLAG_BLINK,
    FLAG_BLOWN_OUT,
    FLAG_CLIPPED,
    FLAG_SOFT,
    CascadeSettings,
    FrameDecision,
    FrameFeatures,
    FrameRole,
)
from .pipeline import Scorer, run_cascade
from .stacking import hamming

# What the app stores per frame (the string goes into the results CSV and AIImageResult.cascade_role).
ROLE_PICK = "pick"  # ranked first among several survivors
ROLE_FINAL = "final"  # survived every pass (unranked, runner-up, or alone)
ROLE_REJECTED = "rejected"  # flagged by pass 1 (dimmed, never hidden)
ROLE_DUPLICATE = "duplicate"
ROLE_CUT = "cut"  # ranked below the kept frames in its stack
ROLE_NO_PICK = "no_pick"
ROLE_BRACKET = "bracket"  # an exposure bracket: kept together, not compared

_FLAG_TEXT = {
    FLAG_BLINK: "eyes closed",
    FLAG_SOFT: "softer than its siblings",
    FLAG_CLIPPED: "more blown highlights than its siblings",
    FLAG_BLOWN_OUT: "highlights blown out",
}

DEFAULT_BURST_GAP_SECONDS = 5.0  # consecutive frames further apart than this are never one burst
DEFAULT_BURST_HAMMING = 20  # ... nor are consecutive frames whose pHash differs by more than this


@dataclass(slots=True, frozen=True)
class FolderFrame:
    path: str
    features: FrameFeatures | None  # None: unreadable, so it gets no decision
    captured: float | None = None  # seconds (any epoch); None when the EXIF time is missing
    bracket_id: int | None = None  # frames sharing an id are one exposure bracket


@dataclass(slots=True, frozen=True)
class FrameOutcome:
    path: str
    role: str
    flags: tuple[str, ...]
    summary: str
    burst_index: int  # a negative number (one per bracket) for exposure brackets
    burst_size: int
    stack_index: int
    stack_count: int
    stack_size: int
    stack_rank: int  # 1-based position inside the stack, best first
    split_jump: int | None
    rank: int | None  # set only when a ranker actually ordered the stack's survivors
    duplicate_of: str | None = None


@dataclass(slots=True)
class FolderResult:
    outcomes: dict[str, FrameOutcome] = field(default_factory=dict)
    bursts: int = 0
    stacks: int = 0
    brackets: int = 0
    scored_by_maniqa: int = 0
    scored_by_aesthetic: int = 0
    unreadable: int = 0
    cancelled: bool = False

    def counts(self) -> dict[str, int]:
        tally: dict[str, int] = {}
        for outcome in self.outcomes.values():
            tally[outcome.role] = tally.get(outcome.role, 0) + 1
        return tally


def segment_bursts(
    frames: Sequence[FolderFrame],
    *,
    max_gap_seconds: float = DEFAULT_BURST_GAP_SECONDS,
    max_hamming: int = DEFAULT_BURST_HAMMING,
) -> list[list[int]]:
    """Index lists of consecutive frames that belong to one burst, in folder order. A frame joins the previous one only when both have a capture
    time no more than ``max_gap_seconds`` apart and their hashes are within ``max_hamming``; a frame with no time or no features is never joined.
    Exposure brackets and unreadable frames end a burst and are not part of one."""
    bursts: list[list[int]] = []
    current: list[int] = []
    previous: FolderFrame | None = None
    for index, frame in enumerate(frames):
        if frame.features is None or frame.bracket_id is not None:
            if current:
                bursts.append(current)
            current, previous = [], None
            continue
        joins = (
            previous is not None
            and previous.captured is not None
            and frame.captured is not None
            and 0.0 <= frame.captured - previous.captured <= max_gap_seconds
            and hamming(previous.features.phash, frame.features.phash) <= max_hamming  # type: ignore[union-attr]
        )
        if current and not joins:
            bursts.append(current)
            current = []
        current.append(index)
        previous = frame
    if current:
        bursts.append(current)
    return bursts


def run_folder(
    frames: Sequence[FolderFrame],
    settings: CascadeSettings | None = None,
    *,
    maniqa: Scorer | None = None,
    aesthetic: Scorer | None = None,
    progress: Callable[[int, int], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> FolderResult:
    """Decide every readable frame. ``progress(done_bursts, total_bursts)`` is called as each burst finishes."""
    settings = settings or CascadeSettings()
    result = FolderResult()
    result.unreadable = sum(1 for f in frames if f.features is None)

    # Exposure brackets: kept together, never compared.
    bracket_members: dict[int, list[int]] = {}
    for index, frame in enumerate(frames):
        if frame.bracket_id is not None and frame.features is not None:
            bracket_members.setdefault(frame.bracket_id, []).append(index)
    for number, members in enumerate(bracket_members.values(), start=1):
        result.brackets += 1
        for index in members:
            result.outcomes[frames[index].path] = FrameOutcome(
                frames[index].path, ROLE_BRACKET, (), f"Exposure bracket of {len(members)} frames: kept together, not compared.",
                -number, len(members), 0, 1, len(members), 1, None, None,
            )

    bursts = segment_bursts(frames)
    result.bursts = len(bursts)
    for burst_index, indices in enumerate(bursts):
        if cancelled is not None and cancelled():
            result.cancelled = True
            break
        burst_frames = [frames[i].features for i in indices]
        cascade = run_cascade(burst_frames, settings, maniqa=maniqa, aesthetic=aesthetic)  # type: ignore[arg-type]
        result.scored_by_maniqa += cascade.scored_by_maniqa
        result.scored_by_aesthetic += cascade.scored_by_aesthetic
        result.stacks += len(cascade.stacks)
        for stack in cascade.stacks:
            members = [cascade.decisions_by_key[key] for key in stack.keys]
            order = sorted(members, key=_order_key(stack.keys))
            position = {d.key: n for n, d in enumerate(order, start=1)}
            for decision in members:
                result.outcomes[decision.key] = _outcome(
                    decision, burst_index, len(indices), stack.index, len(cascade.stacks), len(stack.keys), position[decision.key],
                    stack.split_jump, ranked=any(d.rank is not None for d in members),
                )
        if progress is not None:
            progress(burst_index + 1, len(bursts))
    return result


def _order_key(stack_keys: Sequence[str]):
    priority = {FrameRole.FINAL: 0, FrameRole.DUPLICATE: 1, FrameRole.CUT_BY_RANKING: 2, FrameRole.CUT_BY_MANIQA: 3, FrameRole.NO_PICK: 4, FrameRole.REJECTED: 5}
    index = {key: n for n, key in enumerate(stack_keys)}

    def key(decision: FrameDecision):
        return (priority[decision.role], decision.rank or 0, index[decision.key])

    return key


def _outcome(
    decision: FrameDecision, burst_index: int, burst_size: int, stack_index: int, stack_count: int, stack_size: int, stack_rank: int,
    split_jump: int | None, *, ranked: bool,
) -> FrameOutcome:
    role = decision.role
    name = decision.duplicate_of.rsplit("/", 1)[-1].rsplit("\\", 1)[-1] if decision.duplicate_of else ""
    if role is FrameRole.FINAL:
        if decision.rank == 1 and stack_size > 1:
            kind, text = ROLE_PICK, f"Best of this stack of {stack_size} after the cleanup and ranking passes."
        elif decision.rank is not None:
            kind, text = ROLE_FINAL, f"Kept: runner-up in a stack of {stack_size}."
        elif stack_size == 1:
            kind, text = ROLE_FINAL, "Kept: no sibling frames to compare with."
        else:
            kind, text = ROLE_FINAL, f"Kept: survived the cleanup passes in a stack of {stack_size} (not ranked)."
    elif role is FrameRole.REJECTED:
        kind, text = ROLE_REJECTED, "Flagged: " + _flag_text(decision.flags) + ". Dimmed, not removed."
    elif role is FrameRole.DUPLICATE:
        kind, text = ROLE_DUPLICATE, f"Near-duplicate of {name}, which is better exposed." if name else "Near-duplicate of a better-exposed frame."
    elif role is FrameRole.NO_PICK:
        kind, text = ROLE_NO_PICK, "Every frame in this stack is blown out, so no pick is offered."
    else:
        kind, text = ROLE_CUT, "Ranked below the kept frames in this stack."
    return FrameOutcome(
        decision.key, kind, decision.flags, text, burst_index, burst_size, stack_index, stack_count, stack_size, stack_rank, split_jump,
        decision.rank if ranked else None, decision.duplicate_of,
    )


def _flag_text(flags: Sequence[str]) -> str:
    return ", ".join(_FLAG_TEXT.get(flag, flag) for flag in flags) or "see reasons"
