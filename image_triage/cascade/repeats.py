"""Pass 2 of the folder cascade: repeats of the same scene across the folder, whatever the time between them.

The per-burst cascade only compares frames that were shot within seconds of each other, and keeps every exposure bracket whole. A tripod series
(the same composition, set after set, minutes apart) therefore sails through. This pass looks at the survivors in capture order, treats an exposure
bracket as one unit (its middle exposure stands for it), and folds a unit into an earlier one when their pHash distance is within the repeat
distance, comparing only inside a window of recent units so the cost stays linear. Each pass is one `_Pass`; a later pass (SSIM, ORB) adds one
more entry to `PASSES` and works on what the earlier ones left.

A folded unit is dimmed and labelled with the frame it repeats, never removed.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Callable, Sequence

from .model import CascadeSettings, FrameFeatures
from .similarity import SSIM_EDGE, aligned_ssim, orb_match
from .stacking import hamming

ORB_EDGE = 640  # long edge of the pictures ORB matches


@dataclass(slots=True, frozen=True)
class Unit:
    """One thing that can repeat another: a single surviving frame, or a whole exposure bracket."""

    frames: tuple[int, ...]  # indexes into the capture-ordered frame list
    rep: int  # the frame whose picture stands for the unit (a bracket's middle exposure)
    preferred: bool  # the cascade's own pick of its stack: wins a tie over an unranked frame
    is_set: bool


@dataclass(slots=True, frozen=True)
class _Pass:
    name: str
    label: str  # the words in the frame's explanation
    near: Callable  # (left FolderFrame, right FolderFrame, settings, pixels) -> bool
    needs_pixels: bool = False


def _phash_near(left, right, settings: CascadeSettings, pixels) -> bool:
    return hamming(left.features.phash, right.features.phash) <= settings.repeat_distance


def _gated(left, right, settings: CascadeSettings) -> bool:
    """Two pictures whose coarse layout is nowhere near alike are not worth decoding for."""
    return hamming(left.features.phash, right.features.phash) <= settings.repeat_gate


def _ssim_near(left, right, settings: CascadeSettings, pixels) -> bool:
    if settings.ssim_threshold <= 0 or pixels is None or not _gated(left, right, settings):
        return False
    a, b = pixels.gray(left.path, SSIM_EDGE), pixels.gray(right.path, SSIM_EDGE)
    return a is not None and b is not None and aligned_ssim(a, b) >= settings.ssim_threshold


def _orb_near(left, right, settings: CascadeSettings, pixels) -> bool:
    if settings.orb_inliers <= 0 or pixels is None or not _gated(left, right, settings):
        return False
    a, b = pixels.gray(left.path, ORB_EDGE), pixels.gray(right.path, ORB_EDGE)
    if a is None or b is None:
        return False
    inliers, share = orb_match(a.astype("uint8"), b.astype("uint8"))
    return inliers >= settings.orb_inliers and share >= settings.orb_share


PASSES: tuple[_Pass, ...] = (
    _Pass("phash", "same scene, by perceptual hash", _phash_near),
    _Pass("ssim", "same scene, by structural similarity", _ssim_near, True),
    _Pass("orb", "same scene, by matching features", _orb_near, True),
)


def _keeper_key(unit: Unit, features: Sequence[FrameFeatures]):
    rep = features[unit.rep]
    return (0 if unit.preferred else 1, rep.extremes, -rep.centre_sharpness, unit.rep)


def cluster_units(units: Sequence[Unit], frames: Sequence, near: Callable, window: int) -> list[list[int]]:
    """Clusters of unit positions (units are in capture order). A unit joins the most recent cluster that has a member among the last ``window`` units
    and every one of whose members it is near (complete linkage, so a cluster never chains from one scene into the next)."""
    clusters: list[list[int]] = []
    for position, unit in enumerate(units):
        joined = None
        for cluster in reversed(clusters):
            if cluster[-1] < position - window:
                continue
            if all(near(frames[units[other].rep], frames[unit.rep]) for other in cluster):
                joined = cluster
                break
        if joined is None:
            clusters.append([position])
        else:
            joined.append(position)
    return clusters


def collect_units(frames: Sequence, result, outcome_roles: dict) -> list[Unit]:
    """The things that can repeat one another, in capture order: each surviving single frame and each exposure bracket (by its middle exposure)."""
    eligible = set(outcome_roles["eligible"])
    pick = outcome_roles["pick"]
    bracket_role = outcome_roles["bracket"]
    sets: dict[int, list[int]] = {}
    for index, frame in enumerate(frames):
        outcome = result.outcomes.get(frame.path)
        if frame.features is not None and frame.bracket_id is not None and outcome is not None and outcome.role == bracket_role:
            sets.setdefault(frame.bracket_id, []).append(index)
    in_set = {index for members in sets.values() for index in members}
    units: list[Unit] = []
    for index, frame in enumerate(frames):
        outcome = result.outcomes.get(frame.path)
        if frame.features is None or outcome is None:
            continue
        if index in in_set:
            members = sets.get(frame.bracket_id)
            if members is not None and members[0] == index:
                ordered = sorted(members, key=lambda i: (frames[i].exposure is None, frames[i].exposure or 0.0, i))
                units.append(Unit(tuple(members), ordered[len(ordered) // 2], False, True))
        elif outcome.role in eligible:
            units.append(Unit((index,), index, outcome.role == pick, False))
    return units


def fold_repeats(frames: Sequence, result, settings: CascadeSettings, outcome_roles: dict[str, str], pixels=None) -> int:
    """Fold repeats in ``result`` (a FolderResult) in place and return how many frames were folded. ``frames`` is the capture-ordered FolderFrame list;
    ``outcome_roles`` names the roles that make a single frame eligible, and the one that marks a stack's pick."""
    if settings.repeat_distance <= 0 or settings.repeat_window <= 0:
        return 0
    folded_total = 0
    for _round in range(max(1, settings.repeat_rounds)):
        folded = _fold_round(frames, result, settings, outcome_roles, pixels)
        folded_total += folded
        if folded == 0:        # nothing left that repeats: a further round would find nothing either
            break
    return folded_total


def _fold_round(frames: Sequence, result, settings: CascadeSettings, outcome_roles: dict[str, str], pixels) -> int:
    """One run through every pass. What a pass folds leaves the pool, so the next pass (and the next round) compares the survivors, which now sit
    closer together in the window than they did."""
    folded_total = 0
    for step in PASSES:
        if step.needs_pixels and pixels is None:
            continue
        units = collect_units(frames, result, outcome_roles)
        features = [frame.features for frame in frames]
        clusters = cluster_units(units, frames, lambda a, b: step.near(a, b, settings, pixels), settings.repeat_window)
        for cluster in clusters:
            if len(cluster) < 2:
                continue
            keeper = min(cluster, key=lambda position: _keeper_key(units[position], features))  # type: ignore[arg-type]
            kept_frame = frames[units[keeper].rep]
            kept_name = kept_frame.path.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
            for position in cluster:
                if position == keeper:
                    continue
                for index in units[position].frames:
                    path = frames[index].path
                    outcome = result.outcomes[path]
                    result.outcomes[path] = replace(
                        outcome, role=outcome_roles["duplicate"], flags=(), rank=None, duplicate_of=kept_frame.path,
                        summary=f"Near-duplicate of {kept_name}, the frame kept for this scene ({step.label}).",
                    )
                    folded_total += 1
    return folded_total
