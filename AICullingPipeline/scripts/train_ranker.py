"""CLI entry point for Week 4 pairwise ranker training."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.engine import RankingTrainConfig, train_ranker
from app.utils.logging_utils import setup_logging


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""

    parser = argparse.ArgumentParser(
        description="Train the first preference-based ranker on frozen image embeddings."
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("configs/train_ranker.json"),
        help="Path to the JSON training config file.",
    )
    parser.add_argument(
        "--artifacts-dir",
        type=Path,
        help="Override the folder containing images.csv, embeddings.npy, image_ids.json, and clusters.csv.",
    )
    parser.add_argument(
        "--labels-dir",
        type=Path,
        help="Override the folder containing pairwise and cluster label files.",
    )
    parser.add_argument("--output-dir", type=Path, help="Override the output folder.")
    parser.add_argument(
        "--reference-bank-path",
        type=Path,
        help="Optional reference_bank.npz path for exemplar-conditioned training.",
    )
    parser.add_argument(
        "--reference-top-k",
        type=int,
        help="Override the top-k similarity pooling count used for reference features.",
    )
    parser.add_argument("--num-epochs", type=int, help="Override the number of training epochs.")
    parser.add_argument("--batch-size", type=int, help="Override the training batch size.")
    parser.add_argument("--learning-rate", type=float, help="Override the optimizer learning rate.")
    parser.add_argument("--hidden-dim", type=int, help="Use a one-hidden-layer MLP when greater than 0.")
    parser.add_argument(
        "--device",
        type=str,
        help="Override the device: auto, cpu, cuda, or cuda:N.",
    )
    return parser.parse_args()


def main() -> None:
    """Load config, train the ranker, and print artifact locations."""

    args = parse_args()
    config = RankingTrainConfig.from_file(args.config).apply_overrides(
        artifacts_dir=args.artifacts_dir,
        labels_dir=args.labels_dir,
        output_dir=args.output_dir,
        reference_bank_path=args.reference_bank_path,
        reference_top_k=args.reference_top_k,
        num_epochs=args.num_epochs,
        batch_size=args.batch_size,
        learning_rate=args.learning_rate,
        hidden_dim=args.hidden_dim,
        device=args.device,
    )

    setup_logging(
        config.log_level,
        log_file=config.output_dir / "train_ranker.log",
    )

    try:
        outputs = train_ranker(config)
        print("Training complete.")
        for name, path in outputs.items():
            print(f"{name}: {path}")
    finally:
        logging.shutdown()


if __name__ == "__main__":
    main()
