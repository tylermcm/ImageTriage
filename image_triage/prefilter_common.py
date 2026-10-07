"""Shared types and helpers for the duplicate (pHash) prefilter."""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import AbstractSet, Iterable, Mapping


@dataclass(slots=True, frozen=True)
class PrefilterDecision:
    path: str
    action: str
    reason: str = ""
    score: float = 0.0
    rescue_reasons: tuple[str, ...] = ()

    @property
    def is_candidate(self) -> bool:
        return self.action in {"quarantine", "remove_from_pool"}

    @property
    def is_rescued(self) -> bool:
        return self.action == "rescued"

    def to_row(self) -> dict[str, object]:
        return {
            "path": self.path,
            "action": self.action,
            "reason": self.reason,
            "score": self.score,
            "rescue_reasons": list(self.rescue_reasons),
        }


def clamped_score(value: object) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, parsed))


def bool_value(value: object, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().casefold()
    if not text:
        return default
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return default


def preserve_duplicate_group_representatives(
    decisions: Iterable[PrefilterDecision],
    duplicate_groups: Mapping[str, Mapping[str, int]],
    *,
    duplicate_reasons: tuple[str, ...] = ("duplicate_trash",),
    independent_trash_paths: AbstractSet[str] = frozenset(),
) -> list[PrefilterDecision]:
    """Prevent duplicate classification alone from emptying a similarity group."""
    preserved = list(decisions)
    indexes_by_path = {decision.path: index for index, decision in enumerate(preserved)}
    for members in duplicate_groups.values():
        group_indexes = [indexes_by_path[path] for path in members if path in indexes_by_path]
        if len(group_indexes) < 2:
            continue
        group_decisions = [preserved[index] for index in group_indexes]
        if any(not decision.is_candidate for decision in group_decisions):
            continue
        duplicate_only = [
            index
            for index in group_indexes
            if preserved[index].reason in duplicate_reasons
            and preserved[index].path not in independent_trash_paths
        ]
        if not duplicate_only:
            continue
        representative_index = min(
            duplicate_only,
            key=lambda index: (members.get(preserved[index].path, 2**31 - 1), preserved[index].path),
        )
        representative = preserved[representative_index]
        preserved[representative_index] = replace(
            representative,
            action="rescued",
            rescue_reasons=(*representative.rescue_reasons, "duplicate_group_representative"),
        )
    return preserved
