"""CLI entry point for Week 6 reference-bank extraction."""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.engine import ReferenceBankBuildConfig, build_reference_bank
from app.utils.logging_utils import setup_logging


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""

    parser = argparse.ArgumentParser(
        description="Build a Week 6 exemplar reference bank from terrible/bad/okay/good/great images."
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("configs/build_reference_bank.json"),
        help="Path to the JSON reference-bank config file.",
    )
    parser.add_argument(
        "--reference-dir",
        type=Path,
        help="Override the root folder containing terrible/bad/okay/good/great subfolders.",
    )
    parser.add_argument("--output-dir", type=Path, help="Override the output folder.")
    parser.add_argument("--batch-size", type=int, help="Override the embedding batch size.")
    parser.add_argument("--model-name", type=str, help="Override the model source.")
    parser.add_argument(
        "--device",
        type=str,
        help="Override the device: auto, cpu, cuda, or cuda:N.",
    )
    return parser.parse_args()


def main() -> None:
    """Load config, build the reference bank, and print artifact locations."""

    args = parse_args()
    config = ReferenceBankBuildConfig.from_file(args.config).apply_overrides(
        reference_dir=args.reference_dir,
        output_dir=args.output_dir,
        batch_size=args.batch_size,
        model_name=args.model_name,
        device=args.device,
    )

    setup_logging(
        config.log_level,
        log_file=config.output_dir / "build_reference_bank.log",
    )

    try:
        outputs = build_reference_bank(config)
        print("Reference bank build complete.")
        for name, path in outputs.items():
            print(f"{name}: {path}")

        summary = json.loads(Path(outputs["summary"]).read_text(encoding="utf-8"))
        print("")
        print("Summary:")
        print(f"total_reference_images: {summary['total_reference_images']}")
        print(f"embedded_reference_images: {summary['embedded_reference_images']}")
        print(f"feature_dim: {summary['feature_dim']}")
        print(f"bucket_counts: {summary['bucket_counts']}")
    finally:
        logging.shutdown()


if __name__ == "__main__":
    main()
