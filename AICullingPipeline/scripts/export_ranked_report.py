"""CLI entry point for Week 5 ranked export and HTML reporting."""

from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path
import sys
import time


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _emit_startup_metric(event: str, **fields: object) -> None:
    if (os.environ.get("IMAGE_TRIAGE_AI_METRICS", "") or "").strip().casefold() not in {"1", "true", "yes", "on"}:
        return
    payload = {"event": event}
    payload.update(fields)
    print("AI_METRIC " + json.dumps(payload, default=str), flush=True)


_dependency_start = time.perf_counter()
_emit_startup_metric("ai.script.report.dependencies_start")
from app.engine import RankingReportConfig, export_ranked_results
from app.utils.logging_utils import setup_logging
from app.utils.perf_metrics import emit_metric, now_ms
_emit_startup_metric("ai.script.report.dependencies", duration_ms=(time.perf_counter() - _dependency_start) * 1000.0)


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""

    parser = argparse.ArgumentParser(
        description="Export ranked cluster results and build an HTML inspection report."
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("configs/export_ranked_report.json"),
        help="Path to the JSON ranked-report config file.",
    )
    parser.add_argument("--artifacts-dir", type=Path, help="Override the artifact folder.")
    parser.add_argument("--labels-dir", type=Path, help="Override the labels folder.")
    parser.add_argument("--checkpoint-path", type=Path, help="Override the checkpoint path.")
    parser.add_argument("--output-dir", type=Path, help="Override the output folder.")
    parser.add_argument(
        "--reference-bank-path",
        type=Path,
        help="Optional reference_bank.npz path for reference-conditioned checkpoints.",
    )
    parser.add_argument(
        "--html-max-clusters",
        type=int,
        help="Limit the HTML report to the first N clusters after sorting.",
    )
    parser.add_argument(
        "--score-batch-size",
        type=int,
        help="Override the embedding scoring batch size.",
    )
    parser.add_argument(
        "--device",
        type=str,
        help="Override the device: auto, cpu, cuda, or cuda:N.",
    )
    parser.add_argument(
        "--html-include-singletons",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Include singleton clusters in the HTML report.",
    )
    return parser.parse_args()


def main() -> None:
    """Load config, export ranked results, and print output summary."""

    args = parse_args()
    config = RankingReportConfig.from_file(args.config).apply_overrides(
        artifacts_dir=args.artifacts_dir,
        labels_dir=args.labels_dir,
        checkpoint_path=args.checkpoint_path,
        output_dir=args.output_dir,
        reference_bank_path=args.reference_bank_path,
        html_max_clusters=args.html_max_clusters,
        html_include_singletons=args.html_include_singletons,
        score_batch_size=args.score_batch_size,
        device=args.device,
    )

    setup_logging(
        config.log_level,
        log_file=config.output_dir / "export_ranked_report.log",
    )

    run_start = time.perf_counter()
    try:
        outputs = export_ranked_results(config)
        emit_metric("ai.script.report.total", duration_ms=now_ms(run_start))
        print("Ranked export complete.")
        for name, path in outputs.items():
            print(f"{name}: {path}")

        summary = json.loads(Path(outputs["summary"]).read_text(encoding="utf-8"))
        print("")
        print("Summary:")
        print(f"total_images: {summary['total_images']}")
        print(f"total_clusters: {summary['total_clusters']}")
        print(f"labeled_clusters: {summary['labeled_clusters']}")
        print(
            "model_top1_human_best_match_rate: "
            f"{_format_metric(summary['model_top1_human_best_match_rate'])}"
        )
        print(
            "model_top1_non_reject_rate: "
            f"{_format_metric(summary['model_top1_non_reject_rate'])}"
        )
    finally:
        logging.shutdown()


def _format_metric(value: object) -> str:
    """Format optional float metrics for console output."""

    if value is None:
        return "n/a"
    return f"{float(value):.4f}"


if __name__ == "__main__":
    main()
