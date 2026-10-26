"""Measure what loading the library semantic matrix costs in a fresh process (memory, not speed).

    python scripts/memtrace_matrix_probe.py --vectors 100000

Builds a synthetic library store with N random 512-d float32 vectors in one process, then loads it with the real
``LibraryVectorIndex.load`` in a *new* process (so the allocator holds nothing left over from building the store) and prints
the matrix's exact size against the operating system's private bytes before, at the sampled peak (every 10 ms) and after.
It does not change or optimise the loader; it measures the current one. Run it at 20k / 50k / 100k and on Windows as well as
Linux: how much of the peak is returned to the OS after the load is an allocator property of the platform.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIM = 512
MIB = 1024 * 1024


def build(db: Path, count: int) -> None:
    sys.path.insert(0, str(ROOT))
    import numpy as np

    from image_triage.library_semantic import LibrarySemanticStore

    LibrarySemanticStore(db)
    rng = np.random.default_rng(1)
    connection = sqlite3.connect(db)
    batch = []
    for index in range(count):
        batch.append((f"k{index:07d}", f"/p/{index}.jpg", "f", "", "m", "sig", DIM, rng.standard_normal(DIM).astype(np.float32).tobytes(), 1, 0.0))
        if len(batch) == 5000:
            _insert(connection, batch)
            batch = []
    if batch:
        _insert(connection, batch)
    connection.commit()
    connection.close()


def _insert(connection: sqlite3.Connection, rows: list) -> None:
    connection.executemany(
        "INSERT INTO vectors (path_key,path,folder_key,root,model_identity,signature,dim,vector,available,synced_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        rows,
    )


def measure(db: Path, log_dir: Path) -> dict:
    os.environ["IMAGE_TRIAGE_MEMTRACE"] = "1"
    os.environ["IMAGE_TRIAGE_LOG_DIR"] = str(log_dir)
    sys.path.insert(0, str(ROOT))
    from image_triage.library_semantic import LibrarySemanticStore, LibraryVectorIndex
    from image_triage.perf import perf_logger

    perf_logger().set_enabled(True, reason="matrix_probe")
    index = LibraryVectorIndex.load(LibrarySemanticStore(db), model_identity="m")
    perf_logger().flush()
    events = [json.loads(line) for line in perf_logger().path.read_text().splitlines()]
    span = next(event for event in events if event["event"] == "mem.span")
    matrix = int(index.matrix.nbytes)
    return {
        "vectors": len(index), "matrix_bytes": matrix,
        "private_before": span["private_before"], "private_peak_sampled": span["private_peak_sampled"], "private_after": span["private_after"],
        "os_peak_ws": span["os_peak_ws"], "duration_ms": span["duration_ms"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--vectors", type=int, default=100_000)
    parser.add_argument("--_build", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--_measure", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args._build:
        build(args._build, args.vectors)
        return 0
    if args._measure:
        print(json.dumps(measure(args._measure, args._measure.parent / "logs")))
        return 0
    with tempfile.TemporaryDirectory(prefix="matrix_probe_") as scratch:
        db = Path(scratch) / "lib.sqlite3"
        subprocess.run([sys.executable, __file__, "--vectors", str(args.vectors), "--_build", str(db)], check=True)
        done = subprocess.run([sys.executable, __file__, "--_measure", str(db)], check=True, capture_output=True, text=True)
    result = json.loads(done.stdout.strip().splitlines()[-1])
    matrix = result["matrix_bytes"]
    peak = result["private_peak_sampled"] - result["private_before"]
    kept = result["private_after"] - result["private_before"]
    print(f"vectors                      {result['vectors']:,}")
    print(f"matrix (exact)               {matrix / MIB:8.1f} MiB")
    print(f"private before / peak / after {result['private_before'] / MIB:7.1f} / {result['private_peak_sampled'] / MIB:7.1f} / {result['private_after'] / MIB:7.1f} MiB")
    print(f"peak over before             {peak / MIB:8.1f} MiB  = {peak / matrix:.2f}x the matrix")
    print(f"still held after the load    {kept / MIB:8.1f} MiB  = {kept / matrix:.2f}x the matrix  (matrix itself is 1.00x)")
    print(f"load time                    {result['duration_ms']:.0f} ms")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
