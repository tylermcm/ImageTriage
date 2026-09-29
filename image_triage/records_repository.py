from __future__ import annotations

from typing import Iterable

from .models import ImageRecord


class RecordsRepository:
    """Owns the currently open folder/scope's in-memory record list and its
    path index (`MainWindow._all_records`/`_all_records_by_path` before
    WI-4.4's move/copy/delete/undo pass). Everywhere else in the app keeps
    reading `window._all_records`/`window._all_records_by_path` unchanged
    (MainWindow exposes them as read-only properties delegating here) -
    only the handful of places that used to reassign those two attributes
    directly now go through one of this class's named operations instead.

    Deliberately narrow: the shapes below (`clear`, `reload`, `remove_paths`,
    `replace_by_old_path`) are exactly the four ways the app ever changes
    this list - a generic add/update/remove-one-record API was considered
    and rejected because no caller ever needs to add or update a single
    record without also needing a batch/refocus/rekey step alongside it,
    which stays the caller's job, not the repository's."""

    def __init__(self) -> None:
        self._records: list[ImageRecord] = []
        self._by_path: dict[str, ImageRecord] = {}

    @property
    def all_records(self) -> list[ImageRecord]:
        return self._records

    @property
    def all_records_by_path(self) -> dict[str, ImageRecord]:
        return self._by_path

    def by_path(self, path: str) -> ImageRecord | None:
        return self._by_path.get(path)

    def clear(self) -> None:
        self._records = []
        self._by_path = {}

    def reload(self, records: list[ImageRecord]) -> None:
        """Replace the whole list wholesale - a fresh folder scan, or a
        virtual-scope (catalog/collection) load."""
        self._records = records
        self._by_path = {record.path: record for record in records}

    def remove_paths(self, paths: Iterable[str]) -> None:
        removed_paths = set(paths)
        if not removed_paths:
            return
        self._records = [item for item in self._records if item.path not in removed_paths]
        for path in removed_paths:
            self._by_path.pop(path, None)

    def replace_by_old_path(self, records_by_old_path: dict[str, ImageRecord]) -> None:
        """Swap in post-rename/post-move records in place, keyed by the path
        they used to have, preserving list order. Covers both a single
        rename (a length-1 mapping) and a batch of moves."""
        if not records_by_old_path:
            return
        self._records = [
            records_by_old_path.get(existing.path, existing)
            for existing in self._records
        ]
        self._by_path = {record.path: record for record in self._records}
