"""Pass 0 and pass 1b: split a burst into stacks, and collapse near-duplicates inside a stack."""

from __future__ import annotations

from typing import Sequence

from .model import CascadeSettings, FrameFeatures


def hamming(left: int, right: int) -> int:
    return (int(left) ^ int(right)).bit_count()


def split_into_stacks(frames: Sequence[FrameFeatures], settings: CascadeSettings) -> list[list[int]]:
    """Index lists, in capture order. A new stack starts wherever the picture changes a lot between consecutive frames
    (a different pose or framing), however close in time."""
    stacks: list[list[int]] = []
    current: list[int] = []
    for index, frame in enumerate(frames):
        if current and _starts_new_stack(frames, current, index, settings):
            stacks.append(current)
            current = []
        current.append(index)
    if current:
        stacks.append(current)
    return stacks


def _ratio_split(left: float | None, right: float | None, ratio: float | None, minimum: float) -> bool:
    if ratio is None or left is None or right is None or left < minimum or right < minimum:
        return False
    return max(left, right) / min(left, right) > ratio


def _starts_new_stack(frames: Sequence[FrameFeatures], current: Sequence[int], index: int, settings: CascadeSettings) -> bool:
    frame = frames[index]
    previous = frames[index - 1]
    if hamming(previous.phash, frame.phash) >= settings.split_jump:
        return True
    if settings.max_stack_span is not None and any(
        hamming(frames[i].phash, frame.phash) >= settings.max_stack_span for i in current
    ):
        return True
    if _ratio_split(previous.focal_mm, frame.focal_mm, settings.split_focal_ratio, 1e-9):
        return True
    return _ratio_split(previous.face_area, frame.face_area, settings.split_face_ratio, 0.01)


def complete_linkage(
    indices: Sequence[int], frames: Sequence[FrameFeatures], threshold: int, linkage: str = "first_fit"
) -> list[list[int]]:
    """Merge clusters while every cross pair is within ``threshold`` (so every cluster's diameter stays within it).

    ``first_fit``: the first mergeable pair, in index order, wins (output depends on input order).
    ``closest``: repeatedly merge the pair with the smallest maximum cross distance; ties go to the lowest first then second cluster position."""
    if linkage == "closest":
        return _closest_linkage(indices, frames, threshold)
    if linkage != "first_fit":
        raise ValueError(f"unknown linkage {linkage!r}; expected 'first_fit' or 'closest'")
    clusters = [[i] for i in indices]
    merged = True
    while merged:
        merged = False
        for a in range(len(clusters)):
            for b in range(a + 1, len(clusters)):
                if max(hamming(frames[i].phash, frames[j].phash) for i in clusters[a] for j in clusters[b]) <= threshold:
                    clusters[a] = clusters[a] + clusters.pop(b)
                    merged = True
                    break
            if merged:
                break
    return clusters


def _closest_linkage(indices: Sequence[int], frames: Sequence[FrameFeatures], threshold: int) -> list[list[int]]:
    clusters = [[i] for i in indices]
    while True:
        best: tuple[int, int, int] | None = None
        for a in range(len(clusters)):
            for b in range(a + 1, len(clusters)):
                worst = max(hamming(frames[i].phash, frames[j].phash) for i in clusters[a] for j in clusters[b])
                if worst <= threshold and (best is None or (worst, a, b) < best):
                    best = (worst, a, b)
        if best is None:
            return clusters
        _, a, b = best
        clusters[a] = clusters[a] + clusters.pop(b)


def representative(cluster: Sequence[int], frames: Sequence[FrameFeatures]) -> int:
    """The most evenly exposed frame of a near-duplicate cluster (fewest pixels at either extreme); the earliest frame wins a tie."""
    return min(cluster, key=lambda i: (frames[i].extremes, i))
