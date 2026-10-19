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
        if current and hamming(frames[index - 1].phash, frame.phash) >= settings.split_jump:
            stacks.append(current)
            current = []
        current.append(index)
    if current:
        stacks.append(current)
    return stacks


def complete_linkage(indices: Sequence[int], frames: Sequence[FrameFeatures], threshold: int) -> list[list[int]]:
    """Merge clusters while every cross pair is within ``threshold``. Deterministic: the first mergeable pair, in index order, wins."""
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


def representative(cluster: Sequence[int], frames: Sequence[FrameFeatures]) -> int:
    """The most evenly exposed frame of a near-duplicate cluster (fewest pixels at either extreme); the earliest frame wins a tie."""
    return min(cluster, key=lambda i: (frames[i].extremes, i))
