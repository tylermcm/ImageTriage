"""CLI entry point for optional semantic image classification."""

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
_emit_startup_metric("ai.script.semantic.host_dependencies_start")
from app.config import SemanticClassificationConfig
from app.engine import classify_images_semantically
from app.utils.logging_utils import setup_logging
_emit_startup_metric("ai.script.semantic.host_dependencies", duration_ms=(time.perf_counter() - _dependency_start) * 1000.0)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Classify images with an optional zero-shot semantic sidecar.")
    parser.add_argument("--config", type=Path, default=Path("configs/semantic_classification.json"))
    parser.add_argument("--artifacts-dir", type=Path, help="Override the artifact folder.")
    parser.add_argument("--output-dir", type=Path, help="Override the output folder.")
    parser.add_argument("--model-name", type=str, help="Override the semantic model name.")
    parser.add_argument("--batch-size", type=int, help="Override the classification batch size.")
    parser.add_argument("--device", type=str, help="Override the device: auto, cpu, cuda, or cuda:N.")
    parser.add_argument("--top-k", type=int, help="Override the number of labels stored per image.")
    parser.add_argument("--labels", nargs="+", help="Override the semantic labels.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = SemanticClassificationConfig.from_file(args.config).apply_overrides(
        artifacts_dir=args.artifacts_dir,
        output_dir=args.output_dir,
        model_name=args.model_name,
        batch_size=args.batch_size,
        device=args.device,
        top_k=args.top_k,
        labels=args.labels,
    )

    setup_logging(
        config.log_level,
        log_file=config.output_dir / "semantic_classification.log",
    )

    try:
        outputs = classify_images_semantically(config)
        print("Semantic classification complete.")
        for name, path in outputs.items():
            print(f"{name}: {path}")

        summary = json.loads(Path(outputs["summary"]).read_text(encoding="utf-8"))
        print("")
        print("Summary:")
        print(f"total_images: {summary['total_images']}")
        print(f"classified_images: {summary['classified_images']}")
        print(f"failed_images: {summary['failed_images']}")
    finally:
        logging.shutdown()


if __name__ == "__main__":
    main()
