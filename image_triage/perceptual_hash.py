from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps


@dataclass(slots=True, frozen=True)
class PerceptualDuplicateGroup:
    representative: str
    members: tuple[str, ...]
    max_distance: int


@dataclass(slots=True, frozen=True)
class PerceptualHashStats:
    total_paths: int
    cached: int
    computed: int
    failed: int
    cache_path: str = ""


@dataclass(slots=True, frozen=True)
class PerceptualDuplicateResult:
    groups: list[PerceptualDuplicateGroup]
    stats: PerceptualHashStats


def compute_phash(path: str | Path, *, hash_size: int = 8, highfreq_factor: int = 4) -> int | None:
    sample_size = hash_size * highfreq_factor
    try:
        with Image.open(path) as image:
            image.draft("L", (sample_size, sample_size))
            oriented = ImageOps.exif_transpose(image)
            grayscale = oriented.convert("L").resize(
                (sample_size, sample_size),
                Image.Resampling.LANCZOS,
            )
            pixels = np.asarray(grayscale, dtype=np.float32)
    except (OSError, ValueError):
        return None

    dct = _dct_2d(pixels)
    low_frequency = dct[:hash_size, :hash_size].copy()
    comparable = low_frequency.flatten()[1:]
    median = float(np.median(comparable)) if comparable.size else 0.0
    low_frequency[0, 0] = median
    bits = low_frequency.flatten() > median
    value = 0
    for bit in bits:
        value = (value << 1) | int(bit)
    return int(value)


def hamming_distance_int(left: int, right: int) -> int:
    return bin(int(left) ^ int(right)).count("1")


def find_perceptual_duplicate_groups(
    paths: list[str],
    *,
    hamming_threshold: int = 6,
) -> list[PerceptualDuplicateGroup]:
    return find_perceptual_duplicate_groups_with_stats(
        paths,
        hamming_threshold=hamming_threshold,
    ).groups


def find_perceptual_duplicate_groups_with_stats(
    paths: list[str],
    *,
    hamming_threshold: int = 6,
    cache_path: str | Path | None = None,
    max_workers: int | None = None,
) -> PerceptualDuplicateResult:
    hashes, stats = _load_or_compute_hashes(paths, cache_path=cache_path, max_workers=max_workers)

    if len(hashes) < 2:
        return PerceptualDuplicateResult(groups=[], stats=stats)

    threshold = max(0, min(64, int(hamming_threshold)))
    parent = list(range(len(hashes)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    for left_index, (_left_path, left_hash) in enumerate(hashes):
        for right_index in range(left_index + 1, len(hashes)):
            _right_path, right_hash = hashes[right_index]
            if hamming_distance_int(left_hash, right_hash) <= threshold:
                union(left_index, right_index)

    grouped_indexes: dict[int, list[int]] = {}
    for index in range(len(hashes)):
        grouped_indexes.setdefault(find(index), []).append(index)

    groups: list[PerceptualDuplicateGroup] = []
    for indexes in grouped_indexes.values():
        if len(indexes) < 2:
            continue
        max_distance = 0
        for offset, left_index in enumerate(indexes):
            for right_index in indexes[offset + 1 :]:
                max_distance = max(max_distance, hamming_distance_int(hashes[left_index][1], hashes[right_index][1]))
        representative = hashes[indexes[0]][0]
        members = tuple(hashes[index][0] for index in indexes)
        groups.append(
            PerceptualDuplicateGroup(
                representative=representative,
                members=members,
                max_distance=max_distance,
            )
        )

    return PerceptualDuplicateResult(groups=groups, stats=stats)


def _load_or_compute_hashes(
    paths: list[str],
    *,
    cache_path: str | Path | None,
    max_workers: int | None,
) -> tuple[list[tuple[str, int]], PerceptualHashStats]:
    cache_file = Path(cache_path).expanduser().resolve() if cache_path is not None else None
    cache = _read_cache(cache_file)
    entries = cache.setdefault("entries", {})
    hashes_by_path: dict[str, int] = {}
    missing: list[tuple[str, Path, dict[str, object], str]] = []
    cached = 0

    for raw_path in paths:
        path = Path(raw_path)
        signature = _file_signature(path)
        if signature is None:
            continue
        key = _cache_key(path)
        entry = entries.get(key)
        if _cache_entry_matches(entry, signature):
            cached_hash = entry.get("hash")
            if isinstance(cached_hash, int):
                hashes_by_path[raw_path] = cached_hash
            cached += 1
            continue
        missing.append((raw_path, path, signature, key))

    computed = 0
    failed = 0
    if missing:
        worker_count = _worker_count(max_workers=max_workers, item_count=len(missing))
        if worker_count <= 1:
            computed_rows = [_compute_cache_row(item) for item in missing]
        else:
            with ThreadPoolExecutor(max_workers=worker_count) as executor:
                computed_rows = list(executor.map(_compute_cache_row, missing))
        for raw_path, key, signature, value in computed_rows:
            entries[key] = {**signature, "hash": value}
            if value is None:
                failed += 1
            else:
                hashes_by_path[raw_path] = value
                computed += 1

    if cache_file is not None and missing:
        _write_cache(cache_file, cache)

    hashes = [(path, hashes_by_path[path]) for path in paths if path in hashes_by_path]
    stats = PerceptualHashStats(
        total_paths=len(paths),
        cached=cached,
        computed=computed,
        failed=failed,
        cache_path=str(cache_file) if cache_file is not None else "",
    )
    return hashes, stats


def _compute_cache_row(item: tuple[str, Path, dict[str, object], str]) -> tuple[str, str, dict[str, object], int | None]:
    raw_path, path, signature, key = item
    return raw_path, key, signature, compute_phash(path)


def _read_cache(cache_file: Path | None) -> dict[str, object]:
    if cache_file is None or not cache_file.exists():
        return {"schema_version": 1, "entries": {}}
    try:
        payload = json.loads(cache_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"schema_version": 1, "entries": {}}
    if not isinstance(payload, dict):
        return {"schema_version": 1, "entries": {}}
    entries = payload.get("entries")
    if not isinstance(entries, dict):
        payload["entries"] = {}
    payload["schema_version"] = 1
    return payload


def _write_cache(cache_file: Path, cache: dict[str, object]) -> None:
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    temporary = cache_file.with_suffix(cache_file.suffix + ".tmp")
    temporary.write_text(json.dumps(cache, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    temporary.replace(cache_file)


def _file_signature(path: Path) -> dict[str, object] | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return {
        "size": int(stat.st_size),
        "mtime_ns": int(stat.st_mtime_ns),
    }


def _cache_key(path: Path) -> str:
    try:
        return str(path.expanduser().resolve()).casefold()
    except OSError:
        return str(path.expanduser()).casefold()


def _cache_entry_matches(entry: object, signature: dict[str, object]) -> bool:
    if not isinstance(entry, dict):
        return False
    return entry.get("size") == signature["size"] and entry.get("mtime_ns") == signature["mtime_ns"] and "hash" in entry


def _worker_count(*, max_workers: int | None, item_count: int) -> int:
    if item_count <= 1:
        return 1
    if max_workers is not None:
        return max(1, min(int(max_workers), item_count))
    return max(1, min(8, os.cpu_count() or 4, item_count))


def _dct_2d(values: np.ndarray) -> np.ndarray:
    rows, columns = values.shape
    row_basis = _dct_basis(rows)
    column_basis = _dct_basis(columns)
    return row_basis @ values @ column_basis.T


@lru_cache(maxsize=16)
def _dct_basis(size: int) -> np.ndarray:
    indexes = np.arange(size, dtype=np.float32)
    basis = np.empty((size, size), dtype=np.float32)
    scale0 = np.sqrt(1.0 / size)
    scale = np.sqrt(2.0 / size)
    for frequency in range(size):
        factor = scale0 if frequency == 0 else scale
        basis[frequency, :] = factor * np.cos(np.pi * (2.0 * indexes + 1.0) * frequency / (2.0 * size))
    return basis
