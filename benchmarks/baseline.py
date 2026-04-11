from __future__ import annotations

import argparse
import json
import os
import platform
import statistics
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

# Keep Qt offscreen for repeatable local benchmarks.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from image_triage.ai_workflow import _parse_tqdm_progress, _run_command_with_live_output
from image_triage.library_store import LibraryStore
from image_triage.window import MainWindow


@dataclass(slots=True)
class MetricSummary:
    samples_ms: list[float]
    count: int
    minimum_ms: float
    maximum_ms: float
    mean_ms: float
    p50_ms: float
    p95_ms: float


def _percentile(samples: list[float], percentile: float) -> float:
    if not samples:
        return 0.0
    ordered = sorted(samples)
    position = max(0.0, min(1.0, percentile)) * (len(ordered) - 1)
    lower_index = int(position)
    upper_index = min(lower_index + 1, len(ordered) - 1)
    lower_value = ordered[lower_index]
    upper_value = ordered[upper_index]
    blend = position - lower_index
    return lower_value + ((upper_value - lower_value) * blend)


def _summary(samples_ms: list[float]) -> MetricSummary:
    if not samples_ms:
        return MetricSummary(
            samples_ms=[],
            count=0,
            minimum_ms=0.0,
            maximum_ms=0.0,
            mean_ms=0.0,
            p50_ms=0.0,
            p95_ms=0.0,
        )
    return MetricSummary(
        samples_ms=samples_ms,
        count=len(samples_ms),
        minimum_ms=min(samples_ms),
        maximum_ms=max(samples_ms),
        mean_ms=statistics.fmean(samples_ms),
        p50_ms=_percentile(samples_ms, 0.50),
        p95_ms=_percentile(samples_ms, 0.95),
    )


def _print_summary(label: str, summary: MetricSummary) -> None:
    print(
        f"{label}: n={summary.count} "
        f"min={summary.minimum_ms:.2f}ms "
        f"p50={summary.p50_ms:.2f}ms "
        f"p95={summary.p95_ms:.2f}ms "
        f"mean={summary.mean_ms:.2f}ms "
        f"max={summary.maximum_ms:.2f}ms"
    )


def _time_call_ms(callback) -> float:
    started = time.perf_counter()
    callback()
    return (time.perf_counter() - started) * 1000.0


def _wait_until(predicate, *, timeout_s: float = 120.0, poll_s: float = 0.004) -> None:
    deadline = time.perf_counter() + timeout_s
    while time.perf_counter() < deadline:
        QApplication.processEvents()
        if predicate():
            return
        time.sleep(poll_s)
    raise TimeoutError("Timed out waiting for benchmark condition.")


def _ensure_qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def _write_dataset(folder: Path, record_count: int) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    file_payload = b"\xff\xd8\xff\xe0IMAGETRIAGE\xff\xd9"
    for index in range(record_count):
        path = folder / f"img_{index:05d}.jpg"
        path.write_bytes(file_payload)


def _scan_completed(window: MainWindow) -> bool:
    return (not window._scan_in_progress) and bool(window._all_records)


def _quiesce_background(window: MainWindow) -> None:
    active_review_task = window._active_review_intelligence_task
    if active_review_task is not None:
        active_review_task.cancel()
    window._active_review_intelligence_task = None
    window._review_intelligence_token += 1
    window._annotation_hydration_token += 1
    window._active_annotation_hydration_task = None
    window._annotation_reapply_timer.stop()
    window._metadata_request_timer.stop()
    window._annotation_persistence_queue.flush_blocking()
    QApplication.processEvents()


def _benchmark_folder_open(
    window: MainWindow,
    folder: Path,
    *,
    warm_runs: int,
) -> dict[str, MetricSummary]:
    cold_samples: list[float] = []
    warm_cached_view_samples: list[float] = []
    warm_full_refresh_samples: list[float] = []

    def _load_once(*, force_refresh: bool) -> float:
        started = time.perf_counter()
        window._load_folder(str(folder), force_refresh=force_refresh)
        _wait_until(lambda: _scan_completed(window))
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        _quiesce_background(window)
        return elapsed_ms

    cold_samples.append(_load_once(force_refresh=True))
    for _ in range(max(1, warm_runs)):
        started = time.perf_counter()
        window._load_folder(str(folder), force_refresh=False)
        _wait_until(lambda: window._scan_showed_cached or _scan_completed(window))
        warm_cached_view_samples.append((time.perf_counter() - started) * 1000.0)
        _wait_until(lambda: _scan_completed(window))
        warm_full_refresh_samples.append((time.perf_counter() - started) * 1000.0)
        _quiesce_background(window)

    return {
        "cold_open": _summary(cold_samples),
        "warm_open_cached_first_view": _summary(warm_cached_view_samples),
        "warm_open_cached_full_refresh": _summary(warm_full_refresh_samples),
    }


def _benchmark_annotation_toggles(
    window: MainWindow,
    *,
    toggle_iterations: int,
) -> dict[str, MetricSummary]:
    if not window._records:
        raise RuntimeError("No records loaded before toggle benchmark.")
    target_index = min(len(window._records) // 2, len(window._records) - 1)
    target_path = window._records[target_index].path
    winner_samples: list[float] = []
    reject_samples: list[float] = []

    for index in range(max(1, toggle_iterations)):
        winner_samples.append(
            _time_call_ms(
                lambda: window._toggle_winner(
                    target_index,
                    advance_override=False,
                    current_path_override=target_path,
                )
            )
        )
        if (index + 1) % 24 == 0:
            window._annotation_persistence_queue.flush_blocking()

    for index in range(max(1, toggle_iterations)):
        reject_samples.append(
            _time_call_ms(
                lambda: window._toggle_reject(
                    target_index,
                    advance_override=False,
                    current_path_override=target_path,
                )
            )
        )
        if (index + 1) % 24 == 0:
            window._annotation_persistence_queue.flush_blocking()

    window._annotation_persistence_queue.flush_blocking()
    return {
        "toggle_winner": _summary(winner_samples),
        "toggle_reject": _summary(reject_samples),
    }


def _benchmark_batch_accept(
    window: MainWindow,
    *,
    batch_size: int,
    sample_runs: int,
) -> MetricSummary:
    if len(window._records) < batch_size:
        raise RuntimeError(f"Need at least {batch_size} records for batch benchmark.")
    records = list(window._records[:batch_size])
    samples: list[float] = []
    for _ in range(max(1, sample_runs)):
        window._batch_set_reject(records)
        window._annotation_persistence_queue.flush_blocking()
        samples.append(_time_call_ms(lambda: window._batch_set_winner(records)))
        window._annotation_persistence_queue.flush_blocking()
    return _summary(samples)


def _benchmark_catalog_refresh(
    roots: list[Path],
    *,
    warm_runs: int,
) -> dict[str, MetricSummary]:
    store = LibraryStore()
    cold_samples: list[float] = []
    warm_samples: list[float] = []
    changed_subset_samples: list[float] = []

    for root in roots:
        store.add_catalog_root(str(root))
        cold_samples.append(_time_call_ms(lambda: store.refresh_catalog((str(root),))))
        for _ in range(max(1, warm_runs)):
            warm_samples.append(_time_call_ms(lambda: store.refresh_catalog((str(root),))))
        first_file = next(root.glob("*.jpg"), None)
        if first_file is not None:
            first_file.write_bytes(first_file.read_bytes() + b"u")
            changed_subset_samples.append(_time_call_ms(lambda: store.refresh_catalog((str(root),))))

    return {
        "catalog_refresh_cold": _summary(cold_samples),
        "catalog_refresh_warm": _summary(warm_samples),
        "catalog_refresh_changed_subset": _summary(changed_subset_samples),
    }


def _benchmark_ai_stream_parser(
    temp_root: Path,
    *,
    sample_runs: int,
    line_count: int,
) -> dict[str, MetricSummary]:
    script_path = temp_root / "emit_ai_progress.py"
    script_path.write_text(
        (
            "import sys\n"
            f"total={int(line_count)}\n"
            "for i in range(1, total + 1):\n"
            "    sys.stdout.write(f\"Scanning images: 100%|██████████| {i}/{total} [00:00<00:00]\\n\")\n"
            "sys.stdout.write(\"final tail\")\n"
            "sys.stdout.flush()\n"
        ),
        encoding="utf-8",
    )

    stream_samples: list[float] = []
    parse_only_samples: list[float] = []
    synthetic_lines = [
        f"Extracting embeddings: 100%|##########| {index}/{line_count} [00:00<00:00]"
        for index in range(1, line_count + 1)
    ]

    for _ in range(max(1, sample_runs)):
        parsed_lines = {"count": 0}
        stream_samples.append(
            _time_call_ms(
                lambda: _run_command_with_live_output(
                    [sys.executable, str(script_path)],
                    cwd=temp_root,
                    progress_callback=lambda line: parsed_lines.__setitem__(
                        "count",
                        parsed_lines["count"] + (1 if _parse_tqdm_progress(line) is not None else 0),
                    ),
                )
            )
        )
        parse_only_samples.append(
            _time_call_ms(
                lambda: [
                    _parse_tqdm_progress(line)
                    for line in synthetic_lines
                ]
            )
        )

    return {
        "ai_stream_processing": _summary(stream_samples),
        "ai_progress_parse_only": _summary(parse_only_samples),
    }


def run_baseline(
    *,
    record_counts: list[int],
    open_warm_runs: int,
    toggle_iterations: int,
    batch_size: int,
    batch_runs: int,
    ai_stream_runs: int,
    ai_stream_lines: int,
) -> dict[str, object]:
    _ensure_qapp()
    with tempfile.TemporaryDirectory(prefix="image_triage_bench_") as temp_dir:
        temp_root = Path(temp_dir)
        appdata_root = temp_root / "appdata"
        os.environ["IMAGE_TRIAGE_APPDATA"] = str(appdata_root)
        dataset_roots: list[Path] = []
        for count in record_counts:
            folder = temp_root / f"dataset_{count}"
            _write_dataset(folder, count)
            dataset_roots.append(folder)

        window = MainWindow()
        results: dict[str, object] = {
            "meta": {
                "generated_at_utc": datetime.now(timezone.utc).isoformat(),
                "python": sys.version,
                "platform": platform.platform(),
                "record_counts": record_counts,
            },
            "folder_open": {},
            "annotation_toggle": {},
            "batch_accept_100": {},
            "catalog_refresh": {},
            "ai_stage_proxy": {},
        }

        try:
            for folder in dataset_roots:
                key = folder.name
                open_metrics = _benchmark_folder_open(
                    window,
                    folder,
                    warm_runs=open_warm_runs,
                )
                results["folder_open"][key] = {name: asdict(metric) for name, metric in open_metrics.items()}

            # Use the smallest loaded dataset for interaction latency benchmarks.
            focus_folder = dataset_roots[0]
            window._load_folder(str(focus_folder), force_refresh=False)
            _wait_until(lambda: _scan_completed(window))
            _quiesce_background(window)

            toggle_metrics = _benchmark_annotation_toggles(
                window,
                toggle_iterations=toggle_iterations,
            )
            results["annotation_toggle"] = {name: asdict(metric) for name, metric in toggle_metrics.items()}

            batch_metric = _benchmark_batch_accept(
                window,
                batch_size=batch_size,
                sample_runs=batch_runs,
            )
            results["batch_accept_100"] = asdict(batch_metric)

            catalog_metrics = _benchmark_catalog_refresh(dataset_roots, warm_runs=open_warm_runs)
            results["catalog_refresh"] = {name: asdict(metric) for name, metric in catalog_metrics.items()}

            ai_stream_metrics = _benchmark_ai_stream_parser(
                temp_root=temp_root,
                sample_runs=ai_stream_runs,
                line_count=ai_stream_lines,
            )
            results["ai_stage_proxy"] = {name: asdict(metric) for name, metric in ai_stream_metrics.items()}
        finally:
            window._annotation_persistence_queue.flush_blocking()
            window.close()
            window.deleteLater()
            QApplication.processEvents()

    return results


def _print_results(payload: dict[str, object]) -> None:
    print("\n=== Baseline Results ===")
    folder_open = payload.get("folder_open", {})
    if isinstance(folder_open, dict):
        for dataset, metrics in folder_open.items():
            print(f"\n[{dataset}]")
            if isinstance(metrics, dict):
                for metric_name, metric_payload in metrics.items():
                    if isinstance(metric_payload, dict):
                        _print_summary(metric_name, MetricSummary(**metric_payload))

    annotation_toggle = payload.get("annotation_toggle", {})
    if isinstance(annotation_toggle, dict):
        print("\n[annotation_toggle]")
        for metric_name, metric_payload in annotation_toggle.items():
            if isinstance(metric_payload, dict):
                _print_summary(metric_name, MetricSummary(**metric_payload))

    batch_metric = payload.get("batch_accept_100")
    if isinstance(batch_metric, dict):
        print("\n[batch_accept_100]")
        _print_summary("batch_set_winner", MetricSummary(**batch_metric))

    catalog_metrics = payload.get("catalog_refresh", {})
    if isinstance(catalog_metrics, dict):
        print("\n[catalog_refresh]")
        for metric_name, metric_payload in catalog_metrics.items():
            if isinstance(metric_payload, dict):
                _print_summary(metric_name, MetricSummary(**metric_payload))

    ai_metrics = payload.get("ai_stage_proxy", {})
    if isinstance(ai_metrics, dict):
        print("\n[ai_stage_proxy]")
        for metric_name, metric_payload in ai_metrics.items():
            if isinstance(metric_payload, dict):
                _print_summary(metric_name, MetricSummary(**metric_payload))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Image Triage baseline latency benchmarks.")
    parser.add_argument(
        "--record-counts",
        type=int,
        nargs="+",
        default=[1000, 10000],
        help="Record-count datasets for folder-open and catalog-refresh baselines.",
    )
    parser.add_argument(
        "--open-warm-runs",
        type=int,
        default=3,
        help="Number of warm/cached folder-open and catalog refresh runs per dataset.",
    )
    parser.add_argument(
        "--toggle-iterations",
        type=int,
        default=120,
        help="Iterations per toggle type (winner/reject).",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=100,
        help="Batch size for batch-accept benchmark.",
    )
    parser.add_argument(
        "--batch-runs",
        type=int,
        default=6,
        help="Number of batch-accept samples.",
    )
    parser.add_argument(
        "--ai-stream-runs",
        type=int,
        default=8,
        help="Number of AI stream parser proxy samples.",
    )
    parser.add_argument(
        "--ai-stream-lines",
        type=int,
        default=3000,
        help="Number of synthetic progress lines emitted per AI stream run.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("benchmarks") / "baseline_metrics.json",
        help="Path to JSON output file.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    results = run_baseline(
        record_counts=[max(1, int(value)) for value in args.record_counts],
        open_warm_runs=max(1, int(args.open_warm_runs)),
        toggle_iterations=max(1, int(args.toggle_iterations)),
        batch_size=max(1, int(args.batch_size)),
        batch_runs=max(1, int(args.batch_runs)),
        ai_stream_runs=max(1, int(args.ai_stream_runs)),
        ai_stream_lines=max(20, int(args.ai_stream_lines)),
    )
    output_path = args.output if args.output.is_absolute() else (Path.cwd() / args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
    _print_results(results)
    print(f"\nSaved metrics to: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
