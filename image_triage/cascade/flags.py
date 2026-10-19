"""Pass 1: reasons a frame is flagged, judged against its own stack so a zoomed-in or re-exposed sibling never counts against it."""

from __future__ import annotations

from typing import Sequence

from .model import FLAG_BLINK, FLAG_BLOWN_OUT, FLAG_CLIPPED, FLAG_SOFT, CascadeSettings, FrameFeatures


def flag_stack(indices: Sequence[int], frames: Sequence[FrameFeatures], settings: CascadeSettings) -> dict[int, tuple[str, ...]]:
    """Flags per frame index of one stack. Soft and clipped are relative to the stack's siblings and need at least two frames."""
    members = [frames[i] for i in indices]
    best_sharpness = max(f.centre_sharpness for f in members)
    lowest_clipped = min(f.clipped for f in members)
    compare = len(members) > 1
    flags: dict[int, tuple[str, ...]] = {}
    for index in indices:
        frame = frames[index]
        found: list[str] = []
        if frame.blink:
            found.append(FLAG_BLINK)
        if compare and frame.centre_sharpness < settings.soft_ratio * best_sharpness:
            found.append(FLAG_SOFT)
        if compare and frame.clipped > max(settings.clip_ratio * lowest_clipped, settings.clip_floor):
            found.append(FLAG_CLIPPED)
        if frame.clipped >= settings.blown_out:
            found.append(FLAG_BLOWN_OUT)
        flags[index] = tuple(found)
    return flags


def is_rejecting(flags: Sequence[str]) -> bool:
    """Blown out alone does not reject a frame; it only matters when the whole stack is blown out."""
    return any(flag != FLAG_BLOWN_OUT for flag in flags)
