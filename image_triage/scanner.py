from __future__ import annotations

import functools
import os
import re
import stat
import time
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from .catalog import CatalogRepository, catalog_cache_enabled
from .formats import EDIT_PRIORITY, EDIT_SUFFIXES, IMAGE_SUFFIXES, JPEG_SUFFIXES, RAW_SUFFIXES, ROOT_PRIMARY_PRIORITY, suffix_for_path
from .models import ImageRecord, ImageVariant, SortMode, sort_records
from .perf import perf_logger


JPEG_PAIR_DIRECTORIES = {
    "jpeg",
    "jpg",
}

EDIT_DIRECTORIES = {
    "edit",
    "edits",
}

# Photo-editor bitmap masks live beside their source in directories such as
# ``IMG_0001.edit-assets``. They are implementation data, not library photos.
EDITOR_ASSET_DIR_SUFFIX = ".edit-assets"


def is_editor_asset_path(path: str | Path) -> bool:
    """Return whether ``path`` is inside an editor-generated asset directory."""
    normalized_parts = str(path).replace("\\", "/").split("/")
    return any(part.casefold().endswith(EDITOR_ASSET_DIR_SUFFIX) for part in normalized_parts if part)


@functools.lru_cache(maxsize=16384)
def _normalize_filesystem_path_cached(raw: str) -> str:
    candidate = Path(raw).expanduser()
    try:
        candidate = candidate.resolve(strict=False)
    except OSError:
        candidate = candidate.absolute()
    return os.path.normpath(str(candidate))


def normalize_filesystem_path(path: str | Path) -> str:
    """Resolve and normalize a filesystem path string.

    Memoized via an LRU cache because Path.resolve() does a filesystem stat,
    and on UNC paths each call is a network round-trip. Hot callers
    (load_ai_bundle, adapter review path translation, etc.) hit this thousands
    of times per session with mostly-repeating inputs, so caching turns
    those into O(1) dict lookups after the first call per unique path.
    """

    raw = str(path).strip()
    if not raw:
        return ""
    return _normalize_filesystem_path_cached(raw)


def normalized_path_key(path: str | Path) -> str:
    return normalize_filesystem_path(path).casefold()


def _path_key_fast(path: str) -> str:
    return os.path.normpath(path).casefold()


@dataclass(slots=True, frozen=True)
class ScannedFile:
    path: str
    path_key: str
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


def scan_child_folders(folder: str, *, include_hidden: bool = False) -> list[ImageRecord]:
    folder = normalize_filesystem_path(folder)
    records: list[ImageRecord] = []
    try:
        entries = list(os.scandir(folder))
    except OSError:
        return records
    for entry in entries:
        if entry.name.casefold().endswith(EDITOR_ASSET_DIR_SUFFIX):
            continue
        try:
            if not entry.is_dir(follow_symlinks=False):
                continue
            stat_result = entry.stat(follow_symlinks=False)
        except OSError:
            continue
        if not include_hidden and _is_hidden_directory_entry(entry, stat_result):
            continue
        path = normalize_filesystem_path(entry.path)
        records.append(
            ImageRecord(
                path=path,
                name=entry.name,
                size=0,
                modified_ns=getattr(stat_result, "st_mtime_ns", int(stat_result.st_mtime * 1_000_000_000)),
                is_folder=True,
            )
        )
    return sort_records(records, SortMode.NAME)


def _is_hidden_directory_entry(entry: os.DirEntry[str], stat_result: os.stat_result | None = None) -> bool:
    if entry.name.startswith("."):
        return True
    hidden_attribute = getattr(stat, "FILE_ATTRIBUTE_HIDDEN", 0)
    if not hidden_attribute:
        return False
    try:
        resolved_stat = stat_result if stat_result is not None else entry.stat(follow_symlinks=False)
    except OSError:
        return False
    return bool(getattr(resolved_stat, "st_file_attributes", 0) & hidden_attribute)


def _scan_folder_impl(folder: str, *, include_stat: bool) -> list[ImageRecord]:
    folder = normalize_filesystem_path(folder)
    if is_editor_asset_path(folder):
        return []
    folder_key = _path_key_fast(folder)
    root_files: list[ScannedFile] = []
    paired_jpegs: dict[str, list[ScannedFile]] = {}
    nested_edit_files: list[ScannedFile] = []

    with os.scandir(folder) as entries:
        for entry in entries:
            suffix = suffix_for_path(entry.name)
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
    family_by_stem: dict[str, str] = {}

    def _family_for_stem(stem_key: str) -> str:
        cached_family = family_by_stem.get(stem_key)
        if cached_family is not None:
            return cached_family
        resolved_family = variant_family_key(stem_key, exact_stems)
        family_by_stem[stem_key] = resolved_family
        return resolved_family

    for scanned in root_files:
        family = _family_for_stem(scanned.stem_key)
        root_files_by_family.setdefault(family, []).append(scanned)
        if scanned.suffix in RAW_SUFFIXES:
            raws_by_stem.setdefault(scanned.stem_key, []).append(scanned)
        elif scanned.suffix in JPEG_SUFFIXES:
            root_jpegs_by_stem.setdefault(scanned.stem_key, []).append(scanned)
    for scanned in nested_edit_files:
        family = _family_for_stem(scanned.stem_key)
        nested_edit_files_by_family.setdefault(family, []).append(scanned)

    raw_family_by_stem = {stem_key: _family_for_stem(stem_key) for stem_key in raws_by_stem}

    remaining_by_family: dict[str, list[ScannedFile]] = {}
    for scanned in root_files:
        if scanned.suffix in RAW_SUFFIXES:
            continue
        family = _family_for_stem(scanned.stem_key)
        remaining_by_family.setdefault(family, []).append(scanned)

    primary_by_family: dict[str, ScannedFile] = {}
    for family, family_files in remaining_by_family.items():
        primary_by_family[family] = sorted(family_files, key=lambda item: root_primary_sort_key(family, item))[0]

    variant_targets_by_family: dict[str, set[str]] = {}
    for stem_key, family in raw_family_by_stem.items():
        variant_targets_by_family.setdefault(family, set()).add(stem_key)
    for family, primary in primary_by_family.items():
        variant_targets_by_family.setdefault(family, set()).add(primary.stem_key)

    sorted_variants_by_key: dict[tuple[str, str], tuple[list[ScannedFile], list[ScannedFile]]] = {}
    for family, stems in variant_targets_by_family.items():
        root_family_files = root_files_by_family.get(family, [])
        nested_family_files = nested_edit_files_by_family.get(family, [])
        for stem_key in stems:
            root_candidates = [item for item in root_family_files if edit_stem_matches(stem_key, item.stem_key)]
            nested_candidates = [item for item in nested_family_files if edit_stem_matches(stem_key, item.stem_key)]
            sorted_variants_by_key[(family, stem_key)] = (
                sorted(root_candidates, key=lambda item, _stem=stem_key: edited_candidate_sort_key(_stem, item)),
                sorted(nested_candidates, key=lambda item, _stem=stem_key: edited_candidate_sort_key(_stem, item)),
            )

    records: list[ImageRecord] = []
    consumed_root_keys: set[str] = set()

    for raw_files in raws_by_stem.values():
        for raw in raw_files:
            family = raw_family_by_stem.get(raw.stem_key, _family_for_stem(raw.stem_key))
            companion_files = dedupe_scanned([*root_jpegs_by_stem.get(raw.stem_key, []), *paired_jpegs.get(raw.stem_key, [])])
            companions = tuple(item.path for item in companion_files)
            consumed_root_keys.update(
                item.path_key
                for item in companion_files
                if _path_key_fast(os.path.dirname(item.path)) == folder_key
            )
            excluded = {raw.path_key, *[item.path_key for item in companion_files]}
            sorted_root_variants, sorted_nested_variants = sorted_variants_by_key.get((family, raw.stem_key), ([], []))
            root_variant_files = [item for item in sorted_root_variants if item.path_key not in excluded]
            nested_variant_files = list(sorted_nested_variants)
            edit_files = dedupe_scanned([*root_variant_files, *nested_variant_files])
            stack_base = preferred_stack_base(raw, companion_files)
            if edit_files:
                stack_variants = tuple(to_variant(item) for item in dedupe_scanned([stack_base, *edit_files]))
            elif stack_base.path_key != raw.path_key:
                stack_variants = (to_variant(stack_base),)
            else:
                stack_variants = ()
            consumed_root_keys.update(item.path_key for item in root_variant_files)
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

    consumed_keys = set(consumed_root_keys)
    remaining_by_family = {}
    for scanned in root_files:
        if scanned.path_key in consumed_keys or scanned.suffix in RAW_SUFFIXES:
            continue
        family = _family_for_stem(scanned.stem_key)
        remaining_by_family.setdefault(family, []).append(scanned)

    for family, family_files in remaining_by_family.items():
        primary = primary_by_family.get(family)
        if primary is None or primary.path_key in consumed_keys:
            primary = sorted(family_files, key=lambda item: root_primary_sort_key(family, item))[0]
        sorted_root_variants, sorted_nested_variants = sorted_variants_by_key.get((family, primary.stem_key), ([], []))
        root_variant_files = [item for item in sorted_root_variants if item.path_key != primary.path_key]
        nested_variant_files = list(sorted_nested_variants)
        edit_files = dedupe_scanned([*root_variant_files, *nested_variant_files])
        stack_variants = ()
        if edit_files:
            stack_variants = tuple(to_variant(item) for item in dedupe_scanned([primary, *edit_files]))
        consumed_root_keys.update(item.path_key for item in family_files)
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
    excluded = {_path_key_fast(path) for path in record.stack_paths}
    candidates: list[ScannedFile] = []

    def add_candidate(entry: os.DirEntry[str]) -> None:
        scanned = to_scanned_file(entry, EDIT_SUFFIXES)
        if scanned is None or not edit_stem_matches(stem_key, scanned.stem_key):
            return
        if scanned.path_key in excluded:
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
        if item.path_key in seen:
            continue
        seen.add(item.path_key)
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
    suffix = suffix_for_path(entry.name)
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
        path_key=_path_key_fast(path),
        name=entry.name,
        suffix=suffix,
        size=size,
        modified_ns=modified_ns,
    )


class FolderScanSignals(QObject):
    cached = Signal(str, int, object, str)
    finished = Signal(str, int, object, str)
    failed = Signal(str, int, str)


class FolderRecordsPersistTask(QRunnable):
    def __init__(self, folder: str, records: list[ImageRecord]) -> None:
        super().__init__()
        self.folder = folder
        self.records = list(records)

    def run(self) -> None:
        logger = perf_logger()
        persist_start = time.perf_counter() if logger.enabled else 0.0
        try:
            CatalogRepository().save_folder_records(self.folder, self.records, source="scan")
        except Exception as exc:  # pragma: no cover - cache writes should not block folder display
            if logger.enabled:
                logger.duration(
                    "folder_scan.persist.failed",
                    (time.perf_counter() - persist_start) * 1000.0,
                    folder=self.folder,
                    record_count=len(self.records),
                    error=str(exc),
                )
        else:
            if logger.enabled:
                logger.duration("folder_scan.persist", (time.perf_counter() - persist_start) * 1000.0, folder=self.folder, record_count=len(self.records))


class FolderScanTask(QRunnable):
    def __init__(
        self,
        folder: str,
        token: int,
        sort_mode: SortMode,
        *,
        prefer_cached_only: bool = False,
        use_catalog_cache: bool = True,
        read_cached_records: bool = True,
    ) -> None:
        super().__init__()
        self.folder = normalize_filesystem_path(folder)
        self.token = token
        self.sort_mode = sort_mode
        self.prefer_cached_only = prefer_cached_only
        self.use_catalog_cache = use_catalog_cache
        self.read_cached_records = read_cached_records
        self.signals = FolderScanSignals()
        # Keep the runnable alive until the window releases it after the final signal.
        self.setAutoDelete(False)

    def run(self) -> None:
        logger = perf_logger()
        start = time.perf_counter() if logger.enabled else 0.0
        try:
            cached_records, cache_source = self._load_cached_records()
            if logger.enabled:
                logger.duration(
                    "folder_scan.cache_lookup",
                    (time.perf_counter() - start) * 1000.0,
                    folder=self.folder,
                    cache_source=cache_source,
                    record_count=len(cached_records) if cached_records is not None else 0,
                )
            if cached_records is not None:
                cached_sort_start = time.perf_counter() if logger.enabled else 0.0
                sorted_cached = sort_records(cached_records, self.sort_mode)
                if logger.enabled:
                    logger.duration(
                        "folder_scan.cache_sort",
                        (time.perf_counter() - cached_sort_start) * 1000.0,
                        folder=self.folder,
                        record_count=len(sorted_cached),
                    )
                if self.prefer_cached_only:
                    self.signals.finished.emit(self.folder, self.token, sorted_cached, cache_source)
                    if logger.enabled:
                        logger.duration("folder_scan.total", (time.perf_counter() - start) * 1000.0, folder=self.folder, source=cache_source, record_count=len(sorted_cached))
                    return
                if sorted_cached:
                    self.signals.cached.emit(self.folder, self.token, sorted_cached, cache_source)
            live_start = time.perf_counter() if logger.enabled else 0.0
            records = sort_records(scan_folder(self.folder), self.sort_mode)
            if logger.enabled:
                logger.duration(
                    "folder_scan.live_scan",
                    (time.perf_counter() - live_start) * 1000.0,
                    folder=self.folder,
                    record_count=len(records),
                )
        except Exception as exc:  # pragma: no cover - legacy UI error path
            if logger.enabled:
                logger.duration("folder_scan.failed", (time.perf_counter() - start) * 1000.0, folder=self.folder, error=str(exc))
            self.signals.failed.emit(self.folder, self.token, str(exc))
            return

        if logger.enabled:
            logger.duration("folder_scan.total", (time.perf_counter() - start) * 1000.0, folder=self.folder, source="live", record_count=len(records))
        self.signals.finished.emit(self.folder, self.token, records, "live")
        QThreadPool.globalInstance().start(FolderRecordsPersistTask(self.folder, records), -100)

    def _load_cached_records(self) -> tuple[list[ImageRecord] | None, str]:
        if not self.read_cached_records:
            return None, ""
        if catalog_cache_enabled(self.use_catalog_cache):
            cached_records = CatalogRepository().load_folder_records(self.folder)
            if cached_records is not None:
                return cached_records, "catalog"
        return None, ""

__all__ = [
    "EDITOR_ASSET_DIR_SUFFIX",
    "FolderScanTask",
    "discover_edited_paths",
    "ImageRecord",
    "is_editor_asset_path",
    "normalize_filesystem_path",
    "normalized_path_key",
    "scan_child_folders",
    "scan_folder",
    "scan_folder_quick",
]
