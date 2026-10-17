"""Time the library-wide semantic index: SQLite load, matrix build and queries.

    python benchmarks/library_semantic_search.py [--sizes 10000 50000 100000]

Uses random 512-dim vectors written straight into a throwaway library_semantic
store (the per-folder stores are not involved: this measures the search side).
"""

from __future__ import annotations

import argparse
import statistics
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from image_triage.library_semantic import LibrarySemanticStore, LibraryVectorIndex  # noqa: E402

MODEL = "bench-model"


def fill(store: LibrarySemanticStore, count: int) -> None:
    rng = np.random.default_rng(7)
    vectors = rng.standard_normal((count, 512)).astype(np.float32)
    now = time.time()
    rows = [
        (f"c:\\lib\\f{index // 500}\\img_{index}.jpg", f"C:\\lib\\f{index // 500}\\IMG_{index}.jpg", f"c:\\lib\\f{index // 500}",
         "C:\\lib", MODEL, "sig", 512, vectors[index].tobytes(), now)
        for index in range(count)
    ]
    with store._connect() as connection:
        connection.executemany(
            "INSERT INTO vectors (path_key, path, folder_key, root, model_identity, signature, dim, vector, available, synced_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?)",
            rows,
        )
        connection.commit()


def timed(fn, repeat: int = 7) -> tuple[float, object]:
    samples = []
    result = None
    for _ in range(repeat):
        start = time.perf_counter()
        result = fn()
        samples.append((time.perf_counter() - start) * 1000.0)
    return statistics.median(samples), result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sizes", type=int, nargs="+", default=[10_000, 50_000, 100_000])
    args = parser.parse_args()
    rng = np.random.default_rng(11)
    print(f"numpy {np.__version__}")
    print(f"{'photos':>8} {'load ms':>9} {'MB':>6} {'search ms':>10} {'cached ms':>10} {'similar(4+,2-) ms':>18} {'cut ms':>7}")
    for size in args.sizes:
        with tempfile.TemporaryDirectory() as folder:
            store = LibrarySemanticStore(Path(folder) / "bench.sqlite3")
            fill(store, size)
            load_ms, index = timed(lambda: LibraryVectorIndex.load(store, model_identity=MODEL), repeat=3)
            queries = [rng.standard_normal(512).astype(np.float32) for _ in range(7)]
            samples = []
            for query in queries:
                start = time.perf_counter()
                index.search(query)
                samples.append((time.perf_counter() - start) * 1000.0)
            search_ms = statistics.median(samples)
            cached_ms, _ = timed(lambda: index.search(queries[0]))
            positives = index.paths[:4]
            negatives = index.paths[10:12]
            index._cache.clear()
            similar_ms, result = timed(lambda: (index._cache.clear(), index.similar_to(positives, negatives=negatives))[1], repeat=5)
            cut_ms, _ = timed(lambda: result.cut(0.1))
            megabytes = index.matrix.nbytes / 1e6
            print(f"{size:>8} {load_ms:>9.1f} {megabytes:>6.1f} {search_ms:>10.1f} {cached_ms:>10.3f} {similar_ms:>18.1f} {cut_ms:>7.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
