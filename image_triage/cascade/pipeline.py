"""Run the cascade over one burst of frames: stacks, pass 1 flags, duplicate collapse, then the two rankers last, on survivors only."""

from __future__ import annotations

from typing import Callable, Mapping, Sequence

from .flags import flag_stack, is_rejecting
from .model import (
    CascadeResult,
    CascadeSettings,
    FrameDecision,
    FrameFeatures,
    FrameRole,
    StackResult,
)
from .stacking import complete_linkage, hamming, representative, split_into_stacks

# A ranker scores a batch of frame keys and returns a higher-is-better score for each. It is only ever asked about frames that
# survived the cheaper passes, which is the point of running it last.
Scorer = Callable[[Sequence[str]], Mapping[str, float]]


def run_cascade(
    frames: Sequence[FrameFeatures],
    settings: CascadeSettings | None = None,
    *,
    maniqa: Scorer | None = None,
    aesthetic: Scorer | None = None,
) -> CascadeResult:
    """Decide every frame of one burst. ``maniqa`` and ``aesthetic`` are optional: without them (for example on a machine without a usable GPU)
    the cheap passes still run and every duplicate-collapsed representative is left as FINAL, unranked."""
    settings = settings or CascadeSettings()
    keys = [f.key for f in frames]
    if len(set(keys)) != len(keys):
        raise ValueError("frame keys must be unique within a burst")
    decisions: dict[int, FrameDecision] = {}
    stack_results: list[StackResult] = []
    scored_maniqa = scored_aesthetic = 0

    for stack_index, indices in enumerate(split_into_stacks(frames, settings)):
        previous_jump = hamming(frames[indices[0] - 1].phash, frames[indices[0]].phash) if indices[0] > 0 else None
        flags = flag_stack(indices, frames, settings)
        blown_all = all(frames[i].clipped >= settings.blown_out for i in indices)
        stack_results.append(StackResult(stack_index, tuple(keys[i] for i in indices), previous_jump, no_pick=blown_all))

        if blown_all:
            for i in indices:
                decisions[i] = FrameDecision(keys[i], stack_index, FrameRole.NO_PICK, flags[i])
            continue

        alive = [i for i in indices if not is_rejecting(flags[i])]
        fell_back = not alive
        if fell_back:
            alive = list(indices)
        for i in indices:
            if i not in alive:
                decisions[i] = FrameDecision(keys[i], stack_index, FrameRole.REJECTED, flags[i])

        reps: list[int] = []
        if fell_back and settings.all_flagged_skips_collapse:
            clusters = [[i] for i in alive]
        else:
            clusters = complete_linkage(alive, frames, settings.duplicate_distance, settings.linkage)
        for cluster in clusters:
            rep = representative(cluster, frames)
            reps.append(rep)
            for i in cluster:
                if i != rep:
                    decisions[i] = FrameDecision(keys[i], stack_index, FrameRole.DUPLICATE, flags[i], duplicate_of=keys[rep])
        reps.sort()

        survivors = reps
        ranked = False
        cut_roles: dict[int, FrameRole] = {}
        if maniqa is not None and len(survivors) > settings.maniqa_keep:
            scores = maniqa([keys[i] for i in survivors])
            scored_maniqa += len(survivors)
            order = sorted(survivors, key=lambda i: (-scores[keys[i]], i))
            for i in order[settings.maniqa_keep:]:
                cut_roles[i] = FrameRole.CUT_BY_MANIQA
            survivors = order[: settings.maniqa_keep]
            ranked = True
        if aesthetic is not None and len(survivors) > 1:
            scores = aesthetic([keys[i] for i in survivors])
            scored_aesthetic += len(survivors)
            order = sorted(survivors, key=lambda i: (-scores[keys[i]], i))
            for i in order[settings.final_keep:]:
                cut_roles[i] = FrameRole.CUT_BY_RANKING
            survivors = order[: settings.final_keep]
            ranked = True

        for position, i in enumerate(survivors, start=1):
            decisions[i] = FrameDecision(keys[i], stack_index, FrameRole.FINAL, flags[i], rank=position if ranked else None)
        for i, role in cut_roles.items():
            decisions[i] = FrameDecision(keys[i], stack_index, role, flags[i])

    ordered = tuple(decisions[i] for i in range(len(frames)))
    return CascadeResult(tuple(stack_results), ordered, scored_maniqa, scored_aesthetic, {d.key: d for d in ordered})
