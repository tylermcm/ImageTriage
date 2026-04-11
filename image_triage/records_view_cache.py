from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class ViewInvalidationReason(str, Enum):
    SORT_CHANGED = "sort_changed"
    FILTER_CHANGED = "filter_changed"
    ANNOTATION_CHANGED = "annotation_changed"
    AI_CHANGED = "ai_changed"
    REVIEW_CHANGED = "review_changed"
    LOAD_CHANGED = "load_changed"


@dataclass(slots=True)
class RecordsViewCache:
    dirty_reasons: set[ViewInvalidationReason] = field(default_factory=set)
    dirty_paths: set[str] = field(default_factory=set)

    def mark(
        self,
        reason: ViewInvalidationReason,
        *,
        paths: list[str] | tuple[str, ...] | set[str] | None = None,
    ) -> None:
        self.dirty_reasons.add(reason)
        if paths:
            self.dirty_paths.update(path for path in paths if path)

    def clear(self) -> None:
        self.dirty_reasons.clear()
        self.dirty_paths.clear()

    def consume(self) -> tuple[set[ViewInvalidationReason], set[str]]:
        reasons = set(self.dirty_reasons)
        paths = set(self.dirty_paths)
        self.clear()
        return reasons, paths

