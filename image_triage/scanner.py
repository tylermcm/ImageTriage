from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, Signal

from .formats import EDIT_PRIORITY, EDIT_SUFFIXES, IMAGE_SUFFIXES, JPEG_SUFFIXES, RAW_SUFFIXES, ROOT_PRIMARY_PRIORITY
from .models import ImageRecord, ImageVariant, SortMode, sort_records
from .scan_cache import FolderScanCache


JPEG_PAIR_DIRECTORIES = {
    "jpeg",
    "jpg",
}

EDIT_DIRECTORIES = {
    "edit",
    "edits",
}


def normalize_filesystem_path(path: str | Path) -> str:
    raw = str(path).strip()
    if not raw:
        return ""

    candidate = Path(raw).expanduser()
    try:
        candidate = candidate.resolve(strict=False)
    except OSError:
        candidate = candidate.absolute()
    return os.path.normpath(str(candidate))


def normalized_path_key(path: str | Path) -> str:
    return normalize_filesystem_path(path).casefold()


def _path_key_fast(path: str) -> str:
    return os.path.normpath(path).casefold()


@dataclass(slots=True, frozen=True)
class ScannedFile:
    path: str
    name: str
    suffix: str
    size: int
    modified_ns: int

    @property
    def stem_key(self) -> str:
        return os.path.splitext(self.name)[0].lower()


def scan_folder(folder: str) -> list[ImageRecord]:
    return _scan_folder_impl(folder, include_stat=True)


def scan_folder_quick(folder: str) -> list[ImageRecord]:
    return _scan_folder_impl(folder, include_stat=False)


def _scan_folder_impl(folder: str, *, include_stat: bool) -> list[ImageRecord]:
    folder = normalize_filesystem_path(folder)
    root_files: list[ScannedFile] = []
    paired_jpegs: dict[str, list[ScannedFile]] = {}
    nested_edit_files: list[ScannedFile] = []

    with os.scandir(folder) as entries:
        for entry in entries:
            suffix = os.path.splitext(entry.name)[1].lower()
            if suffix in IMAGE_SUFFIXES:
                scanned = to_scanned_file(entry, IMAGE_SUFFIXES, include_stat=include_stat, parent_folder=folder)
                if scanned is not None:
                    root_files.append(scanned)
                continue
            if not entry.is_dir(follow_symlinks=False):
                continue

            child_folder = os.path.normpath(os.path.join(folder, entry.name))
            with os.scandir(entry.path) as child_entries:
                for child in child_entries:
                    if entry.name.lower() in JPEG_PAIR_DIRECTORIES:
                        scanned = to_scanned_file(child, JPEG_SUFFIXES, include_stat=include_stat, parent_folder=child_folder)
                        if scanned is not None:
                            paired_jpegs.setdefault(scanned.stem_key, []).append(scanned)
                        continue
                    if entry.name.lower() in EDIT_DIRECTORIES:
                        scanned = to_scanned_file(child, EDIT_SUFFIXES, include_stat=include_stat, parent_folder=child_folder)
                        if scanned is not None:
                            nested_edit_files.append(scanned)

    raws_by_stem: dict[str, list[ScannedFile]] = {}
    root_jpegs_by_stem: dict[str, list[ScannedFile]] = {}
    root_files_by_family: dict[str, list[ScannedFile]] = {}
    exact_stems = {scanned.stem_key for scanned in root_files}
    nested_edit_files_by_family: dict[str, list[ScannedFile]] = {}
    for scanned in root_files:
        family = variant_family_key(scanned.stem_key, exact_stems)
        root_files_by_family.setdefault(family, []).append(scanned)
        if scanned.suffix in RAW_SUFFIXES:
            raws_by_stem.setdefault(scanned.stem_key, []).append(scanned)
        elif scanned.suffix in JPEG_SUFFIXES:
            root_jpegs_by_stem.setdefault(scanned.stem_key, []).append(scanned)
    for scanned in nested_edit_files:
        family = variant_family_key(scanned.stem_key, exact_stems)
        nested_edit_files_by_family.setdefault(family, []).append(scanned)

    records: list[ImageRecord] = []
    consumed_root_paths: set[str] = set()

    for raw_files in raws_by_stem.values():
        for raw in raw_files:
            family = variant_family_key(raw.stem_key, exact_stems)
            companion_files = dedupe_scanned([*root_jpegs_by_stem.get(raw.stem_key, []), *paired_jpegs.get(raw.stem_key, [])])
            companions = tuple(item.path for item in companion_files)
            consumed_root_paths.update(path for path in companions if os.path.normpath(os.path.dirname(path)) == folder)
            excluded = {_path_key_fast(raw.path), *[_path_key_fast(item.path) for item in companion_files]}
            root_variant_files = [
                item
                for item in root_files_by_family.get(family, [])
                if _path_key_fast(item.path) not in excluded and edit_stem_matches(raw.stem_key, item.stem_key)
            ]
            nested_variant_files = [
                item
                for item in nested_edit_files_by_family.get(family, [])
                if edit_stem_matches(raw.stem_key, item.stem_key)
            ]
            root_variant_files = sorted(root_variant_files, key=lambda item: edited_candidate_sort_key(raw.stem_key, item))
            nested_variant_files = sorted(nested_variant_files, key=lambda item: edited_candidate_sort_key(raw.stem_key, item))
            edit_files = dedupe_scanned([*root_variant_files, *nested_variant_files])
            stack_base = preferred_stack_base(raw, companion_files)
            if edit_files:
                stack_variants = tuple(to_variant(item) for item in dedupe_scanned([stack_base, *edit_files]))
            elif _path_key_fast(stack_base.path) != _path_key_fast(raw.path):
                stack_variants = (to_variant(stack_base),)
            else:
                stack_variants = ()
            consumed_root_paths.update(item.path for item in root_variant_files)
            variant_sizes = [raw.size, *[item.size for item in companion_files], *[item.size for item in edit_files]]
            variant_modified = [raw.modified_ns, *[item.modified_ns for item in companion_files], *[item.modified_ns for item in edit_files]]
            records.append(
                ImageRecord(
                    path=raw.path,
                    name=raw.name,
                    size=sum(variant_sizes),
                    modified_ns=max(variant_modified),
                    companion_paths=companions,
                    edited_paths=tuple(item.path for item in edit_files),
                    variants=stack_variants,
                )
            )

    remaining_by_family: dict[str, list[ScannedFile]] = {}
    consumed_keys = {_path_key_fast(path) for path in consumed_root_paths}
    for scanned in root_files:
        if _path_key_fast(scanned.path) in consumed_keys or scanned.suffix in RAW_SUFFIXES:
            continue
        family = variant_family_key(scanned.stem_key, exact_stems)
        remaining_by_family.setdefault(family, []).append(scanned)

    for family, family_files in remaining_by_family.items():
        primary = sorted(family_files, key=lambda item: root_primary_sort_key(family, item))[0]
        root_variant_files = [
            item for item in family_files
            if _path_key_fast(item.path) != _path_key_fast(primary.path)
        ]
        root_variant_files = sorted(root_variant_files, key=lambda item: edited_candidate_sort_key(primary.stem_key, item))
        nested_variant_files = [
            item
            for item in nested_edit_files_by_family.get(family, [])
            if edit_stem_matches(family, item.stem_key)
        ]
        nested_variant_files = sorted(nested_variant_files, key=lambda item: edited_candidate_sort_key(primary.stem_key, item))
        edit_files = dedupe_scanned([*root_variant_files, *nested_variant_files])
        stack_variants = ()
        if edit_files:
            stack_variants = tuple(to_variant(item) for item in dedupe_scanned([primary, *edit_files]))
        consumed_root_paths.update(item.path for item in family_files)
        records.append(
            ImageRecord(
                path=primary.path,
                name=primary.name,
                size=primary.size + sum(item.size for item in edit_files),
                modified_ns=max([primary.modified_ns, *[item.modified_ns for item in edit_files]]),
                edited_paths=tuple(item.path for item in edit_files),
                variants=stack_variants,
            )
        )
    return records


def discover_edited_paths(record: ImageRecord) -> tuple[str, ...]:
    primary = Path(normalize_filesystem_path(record.path))
    folder = primary.parent
    stem_key = primary.stem.casefold()
    excluded = {os.path.normpath(path) for path in record.stack_paths}
    candidates: list[ScannedFile] = []

    def add_candidate(entry: os.DirEntry[str]) -> None:
        scanned = to_scanned_file(entry, EDIT_SUFFIXES)
        if scanned is None or not edit_stem_matches(stem_key, scanned.stem_key):
            return
        if os.path.normpath(scanned.path) in excluded:
            return
        candidates.append(scanned)

    try:
        with os.scandir(folder) as entries:
            for entry in entries:
                if entry.is_file(follow_symlinks=False):
                    add_candidate(entry)
                elif entry.is_dir(follow_symlinks=False) and entry.name.lower() in EDIT_DIRECTORIES:
                    with os.scandir(entry.path) as child_entries:
                        for child in child_entries:
                            if child.is_file(follow_symlinks=False):
                                add_candidate(child)
    except OSError:
        return ()

    candidates.sort(key=lambda item: edited_candidate_sort_key(stem_key, item))
    return tuple(item.path for item in candidates)


def variant_family_key(stem: str, exact_stems: set[str]) -> str:
    family = stem
    while True:
        stripped = re.sub(r"([_\- ]\d+)$", "", family)
        if stripped == family or not stripped:
            return family
        if stripped in exact_stems:
            family = stripped
            continue
        return family


def edit_stem_matches(primary_stem: str, candidate_stem: str) -> bool:
    if candidate_stem == primary_stem:
        return True
    for separator in ("_", "-", " "):
        prefix = f"{primary_stem}{separator}"
        if candidate_stem.startswith(prefix):
            suffix = candidate_stem[len(prefix):]
            if suffix and all(part.isdigit() for part in suffix.split(separator) if part):
                return True
    return False


def edited_candidate_sort_key(primary_stem: str, item: ScannedFile) -> tuple[int, int, int, str]:
    variant_priority = 0 if item.stem_key != primary_stem else 1
    return (
        variant_priority,
        EDIT_PRIORITY.get(item.suffix, 99),
        -item.modified_ns,
        item.path.casefold(),
    )


def root_primary_sort_key(family_stem: str, item: ScannedFile) -> tuple[int, int, int, str]:
    return (
        0 if item.stem_key == family_stem else 1,
        ROOT_PRIMARY_PRIORITY.get(item.suffix, 99),
        -item.modified_ns,
        item.path.casefold(),
    )


def to_variant(item: ScannedFile) -> ImageVariant:
    return ImageVariant(
        path=item.path,
        name=item.name,
        size=item.size,
        modified_ns=item.modified_ns,
    )


def dedupe_scanned(items: list[ScannedFile]) -> list[ScannedFile]:
    ordered: list[ScannedFile] = []
    seen: set[str] = set()
    for item in items:
        key = _path_key_fast(item.path)
        if key in seen:
            continue
        seen.add(key)
        ordered.append(item)
    return ordered


def preferred_stack_base(primary: ScannedFile, companions: list[ScannedFile]) -> ScannedFile:
    for item in companions:
        if item.suffix in JPEG_SUFFIXES:
            return item
    return primary


def to_scanned_file(
    entry: os.DirEntry[str],
    allowed_suffixes: set[str],
    *,
    include_stat: bool = True,
    parent_folder: str | None = None,
) -> ScannedFile | None:
    suffix = os.path.splitext(entry.name)[1].lower()
    if suffix not in allowed_suffixes:
        return None

    size = 0
    modified_ns = 0
    if include_stat:
        stat_result = entry.stat(follow_symlinks=False)
        size = stat_result.st_size
        modified_ns = getattr(stat_result, "st_mtime_ns", int(stat_result.st_mtime * 1_000_000_000))
    path = os.path.normpath(os.path.join(parent_folder, entry.name)) if parent_folder else normalize_filesystem_path(entry.path)
    return ScannedFile(
        path=path,
        name=entry.name,
        suffix=suffix,
        size=size,
        modified_ns=modified_ns,
    )


class FolderScanSignals(QObject):
    cached = Signal(str, int, object)
    finished = Signal(str, int, object)
    failed = Signal(str, int, str)


class FolderScanTask(QRunnable):
    _cache = FolderScanCache()

    def __init__(self, folder: str, token: int, sort_mode: SortMode, *, prefer_cached_only: bool = False) -> None:
        super().__init__()
        self.folder = normalize_filesystem_path(folder)
        self.token = token
        self.sort_mode = sort_mode
        self.prefer_cached_only = prefer_cached_only
        self.signals = FolderScanSignals()
        # Keep the runnable alive until the window releases it after the final signal.
        self.setAutoDelete(False)

    def run(self) -> None:
        try:
            cached_records = self._cache.load(self.folder)
            if cached_records:
                sorted_cached = sort_records(cached_records, self.sort_mode)
                if self.prefer_cached_only:
                    self.signals.finished.emit(self.folder, self.token, sorted_cached)
                    return
                self.signals.cached.emit(self.folder, self.token, sorted_cached)
            records = sort_records(scan_folder(self.folder), self.sort_mode)
            self._cache.save(self.folder, records)
        except Exception as exc:  # pragma: no cover - legacy UI error path
            self.signals.failed.emit(self.folder, self.token, str(exc))
            return

        self.signals.finished.emit(self.folder, self.token, records)


__all__ = ["FolderScanTask", "discover_edited_paths", "ImageRecord", "normalize_filesystem_path", "normalized_path_key", "scan_folder", "scan_folder_quick"]
