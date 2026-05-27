"""CLI entry point for scoring saved clusters with a trained Week 4 ranker."""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.engine import RankingScoreConfig, score_cluster_artifacts
from app.utils.logging_utils import setup_logging


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""

    parser = argparse.ArgumentParser(
        description="Score saved culling clusters with a trained preference ranker."
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("configs/score_clusters.json"),
        help="Path to the JSON scoring config file.",
    )
    parser.add_argument(
        "--artifacts-dir",
        type=Path,
        help="Override the folder containing embeddings and cluster artifacts.",
    )
    parser.add_argument(
        "--checkpoint-path",
        type=Path,
        help="Override the trained ranker checkpoint path.",
    )
    parser.add_argument("--output-dir", type=Path, help="Override the output folder.")
    parser.add_argument(
        "--reference-bank-path",
        type=Path,
        help="Optional reference_bank.npz path for reference-conditioned checkpoints.",
    )
    parser.add_argument(
        "--device",
        type=str,
        help="Override the device: auto, cpu, cuda, or cuda:N.",
    )
    return parser.parse_args()


def main() -> None:
    """Load config, score clusters, and print output summary."""

    args = parse_args()
    config = RankingScoreConfig.from_file(args.config).apply_overrides(
        artifacts_dir=args.artifacts_dir,
        checkpoint_path=args.checkpoint_path,
        output_dir=args.output_dir,
        reference_bank_path=args.reference_bank_path,
        device=args.device,
    )

    setup_logging(
        config.log_level,
        log_file=config.output_dir / "score_clusters.log",
    )

    try:
        outputs = score_cluster_artifacts(config)
        print("Scoring complete.")
        for name, path in outputs.items():
            print(f"{name}: {path}")

        summary = json.loads(Path(outputs["summary"]).read_text(encoding="utf-8"))
        print("")
        print("Summary:")
        print(f"total_images: {summary['total_images']}")
        print(f"total_clusters: {summary['total_clusters']}")
        print(f"singleton_clusters: {summary['singleton_clusters']}")
        print(f"largest_cluster_size: {summary['largest_cluster_size']}")
        print(f"model_architecture: {summary['model_architecture']}")
        print(f"normalize_embeddings: {summary['normalize_embeddings']}")
    finally:
        logging.shutdown()


if __name__ == "__main__":
    main()
