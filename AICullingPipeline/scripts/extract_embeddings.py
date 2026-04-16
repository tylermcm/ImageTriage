"""CLI entry point for frozen DINOv2 embedding extraction."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.engine import ExtractionConfig, run_embedding_extraction
from app.utils.logging_utils import setup_logging


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""

    parser = argparse.ArgumentParser(
        description="Extract frozen DINOv2 embeddings from an image directory."
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("configs/extract_embeddings.json"),
        help="Path to the JSON config file.",
    )
    parser.add_argument("--input-dir", type=Path, help="Override the input image folder.")
    parser.add_argument("--output-dir", type=Path, help="Override the output folder.")
    parser.add_argument("--batch-size", type=int, help="Override the inference batch size.")
    parser.add_argument("--model-name", type=str, help="Override the primary timm model name.")
    parser.add_argument(
        "--device",
        type=str,
        help="Override the device: auto, cpu, cuda, or cuda:N.",
    )
    parser.add_argument(
        "--image-size",
        type=int,
        help="Optional square resize override for preprocessing.",
    )
    parser.add_argument(
        "--num-workers",
        type=int,
        help="Override the PyTorch DataLoader worker count.",
    )
    parser.add_argument(
        "--scan-workers",
        type=int,
        help="Override the filesystem scan worker count.",
    )
    return parser.parse_args()


def main() -> None:
    """Load config, run the pipeline, and print output locations."""

    args = parse_args()
    config = ExtractionConfig.from_file(args.config).apply_overrides(
        input_dir=args.input_dir,
        output_dir=args.output_dir,
        batch_size=args.batch_size,
        model_name=args.model_name,
        device=args.device,
        image_size=args.image_size,
        num_workers=args.num_workers,
        scan_workers=args.scan_workers,
    )

    setup_logging(
        config.log_level,
        log_file=config.output_dir / "extract_embeddings.log",
    )

    try:
        outputs = run_embedding_extraction(config)

        print("Extraction complete.")
        for name, path in outputs.items():
            print(f"{name}: {path}")
    finally:
        logging.shutdown()


if __name__ == "__main__":
    main()
