"""Which neighbours of the photo on screen to decode ahead of time, and in what order.

Browsing is mostly one direction at a time (holding an arrow key, dragging the filmstrip), so the
photos ahead of the user are far more likely to be asked for next than the ones behind. The order is
the photo itself, then two ahead for every one behind, in the direction of travel; with no direction
yet it alternates evenly, as before.
"""
from __future__ import annotations

from collections.abc import Iterator


def candidate_indexes(index: int, total: int, travel: int = 0) -> Iterator[int]:
    """Yield ``index`` then its neighbours, nearest first and biased toward ``travel`` (+1, -1 or 0).

    Indexes outside ``0 <= i < total`` are skipped; every index is yielded at most once.
    """
    if total <= 0:
        return
    seen: set[int] = set()

    def offer(candidate: int) -> Iterator[int]:
        if 0 <= candidate < total and candidate not in seen:
            seen.add(candidate)
            yield candidate

    yield from offer(index)
    direction = 1 if travel >= 0 else -1
    for step in range(1, total):
        if travel == 0:
            yield from offer(index + step)
            yield from offer(index - step)
        else:
            yield from offer(index + direction * (2 * step - 1))
            yield from offer(index + direction * (2 * step))
            yield from offer(index - direction * step)
        if index + step >= total and index - step < 0 and step * 2 > total:
            break
