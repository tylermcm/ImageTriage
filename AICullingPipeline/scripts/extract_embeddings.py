"""CLI entry point for frozen DINO embedding extraction."""

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
_emit_startup_metric("ai.script.extract.dependencies_start")
from app.engine import ExtractionConfig, run_embedding_extraction
from app.utils.logging_utils import setup_logging
_emit_startup_metric("ai.script.extract.dependencies", duration_ms=(time.perf_counter() - _dependency_start) * 1000.0)


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""

    parser = argparse.ArgumentParser(
        description="Extract frozen DINO embeddings from an image directory."
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
    parser.add_argument(
        "--include-paths-file",
        type=Path,
        help="Optional text file of relative or absolute image paths to embed.",
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
        include_paths_file=args.include_paths_file,
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
