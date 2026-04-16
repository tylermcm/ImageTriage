"""CLI entry point for Week 5 ranker evaluation."""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.engine import RankingEvaluationConfig, evaluate_ranker
from app.utils.logging_utils import setup_logging


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""

    parser = argparse.ArgumentParser(
        description="Evaluate a trained culling ranker against saved labels."
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("configs/evaluate_ranker.json"),
        help="Path to the JSON evaluation config file.",
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
        "--score-batch-size",
        type=int,
        help="Override the embedding scoring batch size.",
    )
    parser.add_argument(
        "--device",
        type=str,
        help="Override the device: auto, cpu, cuda, or cuda:N.",
    )
    return parser.parse_args()


def main() -> None:
    """Load config, evaluate the ranker, and print key metrics."""

    args = parse_args()
    config = RankingEvaluationConfig.from_file(args.config).apply_overrides(
        artifacts_dir=args.artifacts_dir,
        labels_dir=args.labels_dir,
        checkpoint_path=args.checkpoint_path,
        output_dir=args.output_dir,
        reference_bank_path=args.reference_bank_path,
        score_batch_size=args.score_batch_size,
        device=args.device,
    )

    setup_logging(
        config.log_level,
        log_file=config.output_dir / "evaluate_ranker.log",
    )

    try:
        outputs = evaluate_ranker(config)
        print("Evaluation complete.")
        for name, path in outputs.items():
            print(f"{name}: {path}")

        summary = json.loads(Path(outputs["metrics"]).read_text(encoding="utf-8"))
        print("")
        print("Summary:")
        print(
            "pairwise_accuracy(all_preferences): "
            f"{_format_metric(summary['pairwise_evaluation']['all_preferences']['accuracy'])}"
        )
        print(
            "pairwise_evaluated_pairs: "
            f"{summary['pairwise_evaluation']['all_preferences']['evaluated_pairs']}"
        )
        print(
            "cluster_top1_accuracy: "
            f"{_format_metric(summary['cluster_evaluation']['top_k_metrics']['top_1']['hit_rate'])}"
        )
        top_3_metrics = summary["cluster_evaluation"]["top_k_metrics"].get("top_3")
        if top_3_metrics is not None:
            print(
                "cluster_top3_hit_rate: "
                f"{_format_metric(top_3_metrics['hit_rate'])}"
            )
        print(
            "evaluated_clusters: "
            f"{summary['cluster_evaluation']['evaluated_clusters']}"
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
