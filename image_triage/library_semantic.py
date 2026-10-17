"""Library-wide semantic search: a derived index over every folder's TinyCLIP embeddings.

The per-folder feature stores (``<folder>/.image_triage_ai/.../aiculler.sqlite``,
written by ``semantic_index`` and the library indexing worker) stay the source of
truth. This module keeps a *derived*, fully rebuildable copy of their vectors in
one place (``library_semantic.sqlite3``) so a search can span the whole library
without opening hundreds of folder databases:

- :class:`LibrarySemanticStore` reconciles one folder at a time from its feature
  store, keyed by file signature, so moved, renamed, edited and deleted files drop
  out and nothing is re-embedded just to be copied. Folders on a drive that is not
  connected are kept and marked unavailable, never dropped.
- :class:`LibraryVectorIndex` loads the available vectors into one contiguous,
  L2-normalised float32 matrix and answers similarity queries with a single matrix
  product. Searching never embeds, scans or touches the per-folder stores.
- :class:`RankedResult` is a full ranking cached per query, so a similarity
  threshold can be re-cut without searching again.

Nothing here runs on the image-navigation path.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import time
from collections import OrderedDict
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

import numpy as np

from .scanner import normalize_filesystem_path, normalized_path_key
from .semantic_plan import (
    SEMANTIC_EMBEDDING_DIM,
    _signature_key,
    _source_signature,
    collect_indexable_sources,
)


LIBRARY_SEMANTIC_SCHEMA_VERSION = 1
# How much a "less like this" example pulls the query away; see LibraryVectorIndex.similar_to.
DEFAULT_NEGATIVE_WEIGHT = 0.5
_RESULT_CACHE_SIZE = 8


def library_semantic_db_path() -> Path:
    from .scan_cache import app_data_root

    return app_data_root() / "library_semantic.sqlite3"


def folder_feature_db_path(folder: str | Path) -> Path:
    """Where a folder's own semantic embeddings live (see semantic_index / ai_workflow)."""
    from .ai_workflow import build_ai_workflow_paths

    return build_ai_workflow_paths(folder, resolve=False).artifacts_dir / "aiculler.sqlite"


@contextmanager
def limited_blas_threads(count: int = 1):
    """Keep NumPy's BLAS from spreading over every core while Qt and the editor need them."""
    try:
        from threadpoolctl import threadpool_limits
    except ImportError:
        with nullcontext():
            yield
        return
    with threadpool_limits(limits=max(1, int(count))):
        yield


def _folder_catalog_signature(source_paths: Sequence[str]) -> str:
    digest = hashlib.sha1()
    for key in sorted(normalized_path_key(path) for path in source_paths):
        digest.update(key.encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


def _mtime_ns(path: Path) -> int:
    try:
        return int(path.stat().st_mtime_ns)
    except OSError:
        return 0


@dataclass(frozen=True, slots=True)
class FolderSync:
    """What reconciling one folder found."""
    folder: str
    status: str  # "synced" | "unchanged" | "offline" | "no_store"
    indexed: int = 0  # images in this folder that are now searchable
    needs_embedding: int = 0  # images with no current embedding yet
    removed: int = 0  # library rows dropped because the file is gone or changed


@dataclass(frozen=True, slots=True)
class RootCoverage:
    root: str
    total: int
    indexed: int
    offline: int = 0


@dataclass(frozen=True, slots=True)
class Coverage:
    """How much of the library a semantic search actually covers."""
    total: int
    indexed: int
    offline: int = 0
    roots: tuple[RootCoverage, ...] = ()

    @property
    def fraction(self) -> float:
        return (self.indexed / self.total) if self.total else 0.0

    @property
    def complete(self) -> bool:
        return self.total > 0 and self.indexed >= self.total


class LibrarySemanticStore:
    """Persistent, derived copy of every folder's semantic vectors."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_path = Path(db_path) if db_path is not None else library_semantic_db_path()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            self._create_schema(connection)

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.db_path, timeout=30)
        try:
            yield connection
        finally:
            connection.close()

    def _create_schema(self, connection: sqlite3.Connection) -> None:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS vectors (
                path_key TEXT PRIMARY KEY,
                path TEXT NOT NULL,
                folder_key TEXT NOT NULL,
                root TEXT NOT NULL DEFAULT '',
                model_identity TEXT NOT NULL,
                signature TEXT NOT NULL,
                dim INTEGER NOT NULL,
                vector BLOB NOT NULL,
                available INTEGER NOT NULL DEFAULT 1,
                synced_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_vectors_folder ON vectors(folder_key);
            CREATE INDEX IF NOT EXISTS idx_vectors_model ON vectors(model_identity, available);
            CREATE TABLE IF NOT EXISTS folders (
                folder_key TEXT PRIMARY KEY,
                folder TEXT NOT NULL,
                root TEXT NOT NULL DEFAULT '',
                model_identity TEXT NOT NULL DEFAULT '',
                store_mtime_ns INTEGER NOT NULL DEFAULT 0,
                catalog_signature TEXT NOT NULL DEFAULT '',
                indexed INTEGER NOT NULL DEFAULT 0,
                needs_embedding INTEGER NOT NULL DEFAULT 0,
                available INTEGER NOT NULL DEFAULT 1,
                synced_at REAL NOT NULL DEFAULT 0
            );
            """
        )
        row = connection.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
        if row is None or int(row[0]) != LIBRARY_SEMANTIC_SCHEMA_VERSION:
            # Derived data: on a schema change, start over rather than migrate.
            connection.execute("DELETE FROM vectors")
            connection.execute("DELETE FROM folders")
            connection.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES ('schema_version', ?)",
                (str(LIBRARY_SEMANTIC_SCHEMA_VERSION),),
            )
        connection.commit()

    # -- Reconciling from the per-folder stores ---------------------------------

    def sync_folder(
        self,
        folder: str,
        source_paths: Sequence[str],
        *,
        model_identity: str,
        root: str = "",
        feature_db: str | Path | None = None,
        force: bool = False,
    ) -> FolderSync:
        """Bring this folder's library rows in line with its feature store and current files.

        Cheap when nothing changed (the feature store's mtime and the catalog's file
        list are remembered). Only vectors whose recorded file signature still
        matches the file on disk, and whose model identity is current, are kept.
        """
        folder = normalize_filesystem_path(folder)
        folder_key = normalized_path_key(folder)
        sources = collect_indexable_sources(source_paths)
        if not Path(folder).is_dir():
            self.mark_folder_unavailable(folder)
            return FolderSync(folder=folder, status="offline")
        feature_db = Path(feature_db) if feature_db is not None else folder_feature_db_path(folder)
        store_mtime = _mtime_ns(feature_db)
        catalog_signature = _folder_catalog_signature(sources)
        with self._connect() as connection:
            previous = connection.execute(
                "SELECT store_mtime_ns, catalog_signature, model_identity, available, indexed, needs_embedding "
                "FROM folders WHERE folder_key = ?",
                (folder_key,),
            ).fetchone()
        if (
            not force
            and previous is not None
            and int(previous[0]) == store_mtime
            and previous[1] == catalog_signature
            and previous[2] == model_identity
            and int(previous[3]) == 1
        ):
            return FolderSync(folder=folder, status="unchanged", indexed=int(previous[4]), needs_embedding=int(previous[5]))

        stored = _read_folder_vectors(feature_db, model_identity) if store_mtime else {}
        now = time.time()
        rows: list[tuple] = []
        needs_embedding = 0
        keep_keys: set[str] = set()
        for source in sources:
            key = normalized_path_key(source)
            entry = stored.get(key)
            if entry is None:
                needs_embedding += 1
                continue
            vector, signature = entry
            if signature != _signature_key(_source_signature(Path(source))):
                needs_embedding += 1  # edited or replaced since it was embedded
                continue
            keep_keys.add(key)
            rows.append((key, source, folder_key, root, model_identity, signature, int(vector.size), vector.tobytes(), now))

        with self._connect() as connection:
            existing = {row[0] for row in connection.execute("SELECT path_key FROM vectors WHERE folder_key = ?", (folder_key,))}
            stale = existing - keep_keys
            connection.executemany("DELETE FROM vectors WHERE path_key = ?", [(key,) for key in stale])
            connection.executemany(
                """
                INSERT INTO vectors (path_key, path, folder_key, root, model_identity, signature, dim, vector, available, synced_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                ON CONFLICT(path_key) DO UPDATE SET
                    path = excluded.path, folder_key = excluded.folder_key, root = excluded.root,
                    model_identity = excluded.model_identity, signature = excluded.signature,
                    dim = excluded.dim, vector = excluded.vector, available = 1, synced_at = excluded.synced_at
                """,
                rows,
            )
            connection.execute(
                """
                INSERT INTO folders (folder_key, folder, root, model_identity, store_mtime_ns, catalog_signature,
                                     indexed, needs_embedding, available, synced_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                ON CONFLICT(folder_key) DO UPDATE SET
                    folder = excluded.folder, root = excluded.root, model_identity = excluded.model_identity,
                    store_mtime_ns = excluded.store_mtime_ns, catalog_signature = excluded.catalog_signature,
                    indexed = excluded.indexed, needs_embedding = excluded.needs_embedding,
                    available = 1, synced_at = excluded.synced_at
                """,
                (folder_key, folder, root, model_identity, store_mtime, catalog_signature, len(rows), needs_embedding, now),
            )
            connection.commit()
        return FolderSync(
            folder=folder,
            status="synced" if store_mtime else "no_store",
            indexed=len(rows),
            needs_embedding=needs_embedding,
            removed=len(stale),
        )

    def mark_folder_unavailable(self, folder: str) -> None:
        """A disconnected drive: keep the folder's vectors, just stop searching them."""
        folder_key = normalized_path_key(folder)
        with self._connect() as connection:
            connection.execute("UPDATE vectors SET available = 0 WHERE folder_key = ?", (folder_key,))
            connection.execute("UPDATE folders SET available = 0 WHERE folder_key = ?", (folder_key,))
            connection.commit()

    def forget_folders_except(self, folder_keys: Iterable[str]) -> int:
        """Drop folders no longer in the catalog at all (unlike an offline drive, they are gone for good)."""
        keep = set(folder_keys)
        with self._connect() as connection:
            known = [row[0] for row in connection.execute("SELECT folder_key FROM folders")]
            gone = [key for key in known if key not in keep]
            connection.executemany("DELETE FROM vectors WHERE folder_key = ?", [(key,) for key in gone])
            connection.executemany("DELETE FROM folders WHERE folder_key = ?", [(key,) for key in gone])
            connection.commit()
        return len(gone)

    def folder_needs_embedding(self, folder: str) -> int | None:
        """Images still waiting for an embedding in this folder, or None if it was never reconciled."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT needs_embedding FROM folders WHERE folder_key = ? AND available = 1",
                (normalized_path_key(folder),),
            ).fetchone()
        return None if row is None else int(row[0])

    # -- Coverage -----------------------------------------------------------------

    def coverage(self, totals_by_root: dict[str, int], *, model_identity: str) -> Coverage:
        """``totals_by_root`` is the catalog's count of indexable photos under each root."""
        with self._connect() as connection:
            counts = connection.execute(
                "SELECT root, available, COUNT(*) FROM vectors WHERE model_identity = ? GROUP BY root, available",
                (model_identity,),
            ).fetchall()
        indexed_by_root: dict[str, int] = {}
        offline_by_root: dict[str, int] = {}
        for root, available, count in counts:
            target = indexed_by_root if int(available) else offline_by_root
            target[str(root)] = target.get(str(root), 0) + int(count)
        roots = tuple(
            RootCoverage(
                root=root,
                total=int(total),
                indexed=min(int(total), indexed_by_root.get(root, 0)),
                offline=offline_by_root.get(root, 0),
            )
            for root, total in sorted(totals_by_root.items())
        )
        return Coverage(
            total=sum(item.total for item in roots),
            indexed=sum(item.indexed for item in roots),
            offline=sum(item.offline for item in roots),
            roots=roots,
        )

    # -- Loading for search ---------------------------------------------------------

    def load_matrix(self, *, model_identity: str) -> tuple[list[str], np.ndarray]:
        """(paths, matrix) of every searchable vector for this model, rows L2-normalised."""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT path, vector, dim FROM vectors WHERE model_identity = ? AND available = 1 ORDER BY path_key",
                (model_identity,),
            ).fetchall()
        paths: list[str] = []
        blobs: list[bytes] = []
        dim = 0
        for path, blob, row_dim in rows:
            if dim == 0:
                dim = int(row_dim)
            if int(row_dim) != dim:
                continue
            paths.append(str(path))
            blobs.append(blob)
        if not blobs:
            return [], np.empty((0, dim or SEMANTIC_EMBEDDING_DIM), dtype=np.float32)
        matrix = np.frombuffer(b"".join(blobs), dtype=np.float32).reshape(len(blobs), dim).copy()
        return paths, _normalize_rows(matrix)


def _read_folder_vectors(feature_db: Path, model_identity: str) -> dict[str, tuple[np.ndarray, str]]:
    """{path key: (vector, recorded source signature)} for this model, read-only."""
    if not feature_db.exists():
        return {}
    try:
        connection = sqlite3.connect(f"{feature_db.resolve().as_uri()}?mode=ro", uri=True, timeout=10)
    except sqlite3.Error:
        return {}
    try:
        rows = connection.execute(
            """
            SELECT images.source_path, embeddings.embedding, embeddings.dtype, state.source_signature
            FROM images
            JOIN embeddings ON embeddings.image_id = images.id
            JOIN semantic_index_state AS state ON state.image_id = images.id
            WHERE state.model_identity = ? AND embeddings.dim = state.embedding_dim
            """,
            (model_identity,),
        ).fetchall()
    except sqlite3.Error:
        return {}  # an old or half-written store: treat the folder as not embedded yet
    finally:
        connection.close()
    vectors: dict[str, tuple[np.ndarray, str]] = {}
    for source_path, blob, dtype, signature in rows:
        try:
            vector = np.frombuffer(blob, dtype=np.dtype(str(dtype or "float32"))).astype(np.float32)
        except (TypeError, ValueError):
            continue
        vectors[normalized_path_key(str(source_path))] = (vector, str(signature))
    return vectors


def _normalize_rows(matrix: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0.0] = 1.0
    return (matrix / norms).astype(np.float32, copy=False)


def _normalize(vector: np.ndarray) -> np.ndarray:
    vector = np.asarray(vector, dtype=np.float32).reshape(-1)
    norm = float(np.linalg.norm(vector))
    return vector / norm if norm > 0.0 else vector


def search_key(path: str) -> str:
    """The index's own path key: pure string work, never the filesystem.

    (``normalized_path_key`` resolves through the filesystem - a stat per path, a
    network round trip on a share - which is fine while reconciling in the
    background but not 50,000 times per query.)"""
    return os.path.normpath(str(path)).casefold()


class RankedResult:
    """A whole ranking, best first. Cut it at any similarity without searching again.

    Holds row numbers and scores; paths are only looked up for the rows you ask for."""

    __slots__ = ("rows", "scores", "missing", "searched", "_source_paths")

    def __init__(
        self,
        rows: np.ndarray,
        scores: np.ndarray,
        source_paths: Sequence[str],
        *,
        missing: tuple[str, ...] = (),
        searched: int = 0,
    ) -> None:
        self.rows = rows  # int64 row numbers into the index, best first
        self.scores = scores  # descending cosine similarity, same length as rows
        self.missing = missing  # example photos that are not indexed (so were not used)
        self.searched = searched  # how many photos the search covered
        self._source_paths = source_paths

    @classmethod
    def empty(cls, *, missing: tuple[str, ...] = (), searched: int = 0) -> "RankedResult":
        return cls(np.empty(0, dtype=np.int64), np.empty(0, dtype=np.float32), (), missing=missing, searched=searched)

    def __len__(self) -> int:
        return int(self.rows.size)

    @property
    def paths(self) -> tuple[str, ...]:
        """Every ranked path; builds the whole list, so prefer top() or cut() for big results."""
        return tuple(self._source_paths[int(row)] for row in self.rows)

    def with_missing(self, missing: tuple[str, ...]) -> "RankedResult":
        return RankedResult(self.rows, self.scores, self._source_paths, missing=missing, searched=self.searched)

    def count_at_least(self, threshold: float) -> int:
        # scores are descending; searchsorted wants ascending
        return int(self.scores.size - np.searchsorted(self.scores[::-1], float(threshold), side="left"))

    def cut(self, threshold: float) -> list[tuple[str, float]]:
        return self.top(self.count_at_least(threshold))

    def top(self, limit: int) -> list[tuple[str, float]]:
        limit = max(0, min(int(limit), int(self.rows.size)))
        return [(self._source_paths[int(self.rows[index])], float(self.scores[index])) for index in range(limit)]


@dataclass
class LibraryVectorIndex:
    """All searchable vectors as one matrix. Build it off the GUI thread; queries are a matrix product."""
    paths: list[str]
    matrix: np.ndarray
    model_identity: str = ""
    blas_threads: int = 1
    _row_by_key: dict[str, int] = field(default_factory=dict, repr=False)
    _cache: "OrderedDict[tuple, RankedResult]" = field(default_factory=OrderedDict, repr=False)

    def __post_init__(self) -> None:
        self._row_by_key = {search_key(path): row for row, path in enumerate(self.paths)}

    @classmethod
    def load(cls, store: LibrarySemanticStore, *, model_identity: str, blas_threads: int = 1) -> "LibraryVectorIndex":
        paths, matrix = store.load_matrix(model_identity=model_identity)
        return cls(paths=paths, matrix=matrix, model_identity=model_identity, blas_threads=blas_threads)

    def __len__(self) -> int:
        return len(self.paths)

    def contains(self, path: str) -> bool:
        return search_key(path) in self._row_by_key

    def vector_for(self, path: str) -> np.ndarray | None:
        row = self._row_by_key.get(search_key(path))
        return None if row is None else self.matrix[row]

    def _rows_for(self, paths: Iterable[str]) -> list[int]:
        return [row for row in (self._row_by_key.get(search_key(path)) for path in paths) if row is not None]

    def search(
        self,
        query: Sequence[float] | np.ndarray,
        *,
        within: Iterable[str] | None = None,
        exclude: Iterable[str] = (),
    ) -> RankedResult:
        """Rank every searchable photo (or only ``within``) by cosine similarity to ``query``."""
        query_vector = _normalize(np.asarray(query, dtype=np.float32))
        within_rows = tuple(sorted(self._rows_for(within))) if within is not None else None
        exclude_rows = tuple(sorted(self._rows_for(exclude)))
        cache_key = ("search", query_vector.tobytes(), within_rows, exclude_rows)
        cached = self._cache_get(cache_key)
        if cached is None:
            cached = self._rank(query_vector, within_rows, exclude_rows)
            self._cache_put(cache_key, cached)
        return cached

    def similar_to(
        self,
        paths: Sequence[str],
        *,
        negatives: Sequence[str] = (),
        negative_weight: float = DEFAULT_NEGATIVE_WEIGHT,
        within: Iterable[str] | None = None,
    ) -> RankedResult:
        """Photos like the examples and unlike the ``negatives``:
        q = normalize(mean(P) - negative_weight * mean(N)). Examples are left out of the result."""
        positive_rows = self._rows_for(paths)
        missing = tuple(path for path in paths if search_key(path) not in self._row_by_key)
        if not positive_rows:
            return RankedResult.empty(missing=missing, searched=len(self.paths))
        negative_rows = self._rows_for(negatives)
        within_rows = tuple(sorted(self._rows_for(within))) if within is not None else None
        cache_key = ("similar", tuple(sorted(positive_rows)), tuple(sorted(negative_rows)), float(negative_weight), within_rows)
        cached = self._cache_get(cache_key)
        if cached is None:
            query = self.matrix[positive_rows].mean(axis=0)
            if negative_rows:
                query = query - float(negative_weight) * self.matrix[negative_rows].mean(axis=0)
            cached = self._rank(_normalize(query), within_rows, tuple(sorted({*positive_rows, *negative_rows})))
            self._cache_put(cache_key, cached)
        return cached.with_missing(missing)

    def _rank(self, query: np.ndarray, within_rows: tuple[int, ...] | None, exclude_rows: tuple[int, ...]) -> RankedResult:
        if not len(self.paths) or query.size != self.matrix.shape[1]:
            return RankedResult.empty(searched=len(self.paths))
        if within_rows is not None:
            candidate_rows = np.asarray(within_rows, dtype=np.int64)
            matrix = self.matrix[candidate_rows]
        else:
            candidate_rows = None
            matrix = self.matrix
        with limited_blas_threads(self.blas_threads):
            scores = matrix @ query
        order = np.argsort(-scores, kind="stable")
        rows = candidate_rows[order] if candidate_rows is not None else order.astype(np.int64)
        ranked_scores = scores[order]
        if exclude_rows:
            keep = ~np.isin(rows, np.asarray(exclude_rows, dtype=np.int64))
            rows, ranked_scores = rows[keep], ranked_scores[keep]
        return RankedResult(rows, ranked_scores.astype(np.float32, copy=False), self.paths, searched=int(matrix.shape[0]))

    def _cache_get(self, key: tuple) -> RankedResult | None:
        result = self._cache.get(key)
        if result is not None:
            self._cache.move_to_end(key)
        return result

    def _cache_put(self, key: tuple, result: RankedResult) -> None:
        self._cache[key] = result
        while len(self._cache) > _RESULT_CACHE_SIZE:
            self._cache.popitem(last=False)


def text_query_vector(encoder, text: str, templates: Sequence[str] | None = None) -> np.ndarray:
    """Average a text prompt over the caption templates the per-folder search uses."""
    if templates is None:
        from .semantic_search import SEARCH_QUERY_TEMPLATES as templates  # noqa: N811
    vectors = []
    for template in templates:
        prompt = template.format(text).strip()
        if prompt:
            vectors.append(_normalize(np.asarray(encoder.encode(prompt), dtype=np.float32)))
    if not vectors:
        return _normalize(np.asarray(encoder.encode(text), dtype=np.float32))
    return _normalize(np.mean(np.vstack(vectors), axis=0))


def describe_coverage(coverage: Coverage, *, state: str = "") -> str:
    """One line for menus and status: 'Library search: 36,000 of 50,000 photos (72%) - paused'."""
    if coverage.total == 0:
        return "Library search: add folders to the library first"
    percent = int(round(coverage.fraction * 100))
    text = f"Library search: {coverage.indexed:,} of {coverage.total:,} photos ({percent}%)"
    if coverage.offline:
        text += f", {coverage.offline:,} on disconnected drives"
    if state:
        text += f" — {state}"
    return text


__all__ = (
    "Coverage",
    "DEFAULT_NEGATIVE_WEIGHT",
    "FolderSync",
    "LibrarySemanticStore",
    "LibraryVectorIndex",
    "RankedResult",
    "RootCoverage",
    "describe_coverage",
    "folder_feature_db_path",
    "library_semantic_db_path",
    "limited_blas_threads",
    "search_key",
    "text_query_vector",
)

