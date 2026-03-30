from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from enum import Enum

from .formats import EDIT_PRIORITY, EDIT_SUFFIXES, IMAGE_SUFFIXES, JPEG_SUFFIXES, RAW_SUFFIXES


@dataclass(slots=True, frozen=True)
class ImageVariant:
    path: str
    name: str
    size: int
    modified_ns: int


@dataclass(slots=True, frozen=True)
class ImageRecord:
    path: str
    name: str
    size: int
    modified_ns: int
    companion_paths: tuple[str, ...] = ()
    edited_paths: tuple[str, ...] = ()
    variants: tuple[ImageVariant, ...] = ()

    @property
    def all_paths(self) -> tuple[str, ...]:
        return (self.path, *self.companion_paths)

    @property
    def display_variants(self) -> tuple[ImageVariant, ...]:
        if self.variants:
            return self.variants
        return (
            ImageVariant(
                path=self.path,
                name=self.name,
                size=self.size,
                modified_ns=self.modified_ns,
            ),
        )

    @property
    def stack_count(self) -> int:
        return len(self.display_variants)

    @property
    def has_variant_stack(self) -> bool:
        return self.stack_count > 1

    @property
    def stack_paths(self) -> tuple[str, ...]:
        ordered: list[str] = []
        seen: set[str] = set()
        for path in (
            self.path,
            *self.companion_paths,
            *self.edited_paths,
            *[variant.path for variant in self.display_variants],
        ):
            normalized = path.casefold()
            if normalized in seen:
                continue
            seen.add(normalized)
            ordered.append(path)
        return tuple(ordered)

    @property
    def bundle_label(self) -> str:
        if not self.companion_paths:
            return ""

        suffixes = {os.path.splitext(path)[1].lower() for path in self.all_paths}
        if suffixes & RAW_SUFFIXES and suffixes & JPEG_SUFFIXES:
            return "RAW+JPG"
        return f"{len(self.all_paths)} files"

    @property
    def has_edits(self) -> bool:
        return bool(self.edited_paths)

    @property
    def preferred_edit_path(self) -> str:
        if not self.edited_paths:
            return ""
        return sorted(
            self.edited_paths,
            key=lambda path: (EDIT_PRIORITY.get(os.path.splitext(path)[1].lower(), 99), path.casefold()),
        )[0]


@dataclass(slots=True)
class SessionAnnotation:
    winner: bool = False
    reject: bool = False
    photoshop: bool = False
    rating: int = 0
    tags: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_empty(self) -> bool:
        return not self.winner and not self.reject and not self.photoshop and self.rating == 0 and not self.tags


class SortMode(str, Enum):
    NAME = "Filename"
    DATE = "Date Modified"
    SIZE = "File Size"


class FilterMode(str, Enum):
    ALL = "All"
    WINNERS = "Winners Only"
    REJECTS = "Rejects Only"
    UNREVIEWED = "Unreviewed"
    EDITED = "Edited"
    AI_TOP_PICKS = "AI Top Picks"
    AI_GROUPED = "AI Grouped"


class WinnerMode(str, Enum):
    COPY = "Copy To _winners"
    HARDLINK = "Link To _winners"
    LOGICAL = "Annotation Only"


class DeleteMode(str, Enum):
    SAFE_TRASH = "Safe Trash (Undoable)"
    SYSTEM_TRASH = "System Trash"


def sort_records(records: list[ImageRecord], sort_mode: SortMode) -> list[ImageRecord]:
    if sort_mode == SortMode.DATE:
        return sorted(records, key=lambda record: (-record.modified_ns, _natural_name_key(record.name)))
    if sort_mode == SortMode.SIZE:
        return sorted(records, key=lambda record: (-record.size, _natural_name_key(record.name)))
    return sorted(records, key=lambda record: _natural_name_key(record.name))


def _natural_name_key(value: str) -> tuple[tuple[int, object], ...]:
    parts = re.split(r"(\d+)", value.casefold())
    key: list[tuple[int, object]] = []
    for part in parts:
        if not part:
            continue
        if part.isdigit():
            key.append((0, int(part)))
        else:
            key.append((1, part))
    return tuple(key)
