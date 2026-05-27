"""CLI entry point for building modular culling signal artifacts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.engine.signals import build_culling_signals, load_learned_weights, save_culling_signals


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build DINO/technical/specialist culling signal artifacts."
    )
    parser.add_argument("--artifacts-dir", type=Path, required=True, help="Prepared artifact folder.")
    parser.add_argument("--output-dir", type=Path, required=True, help="Folder to write signal artifacts.")
    parser.add_argument("--profile", type=str, default="General Use", help="Scoring profile name.")
    parser.add_argument("--max-preview-side", type=int, default=768, help="Preview side used for technical analysis.")
    parser.add_argument("--skip-technical", action="store_true", help="Skip deterministic technical analysis.")
    parser.add_argument("--skip-specialists", action="store_true", help="Skip specialist layer status slots.")
    parser.add_argument("--weights-path", type=Path, help="Optional learned transparent-combiner weights JSON.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    learned_weights = load_learned_weights(args.weights_path) if args.weights_path else None
    records = build_culling_signals(
        artifacts_dir=args.artifacts_dir,
        profile_name=args.profile,
        run_technical=not args.skip_technical,
        run_specialists=not args.skip_specialists,
        max_preview_side=max(64, int(args.max_preview_side)),
        learned_weights=learned_weights,
    )
    outputs = save_culling_signals(records, args.output_dir)
    summary = _summarize(records)

    print("Culling signal build complete.")
    for name, path in outputs.items():
        print(f"{name}: {path}")
    print("")
    print("Summary:")
    print(json.dumps(summary, indent=2))


def _summarize(records) -> dict[str, object]:
    values = list(records.values())
    buckets: dict[str, int] = {}
    technical_statuses: dict[str, int] = {}
    face_statuses: dict[str, int] = {}
    aesthetic_statuses: dict[str, int] = {}
    layer_statuses: dict[str, int] = {}
    faces_detected = 0
    for record in values:
        buckets[record.final.bucket] = buckets.get(record.final.bucket, 0) + 1
        technical_statuses[record.technical.status] = technical_statuses.get(record.technical.status, 0) + 1
        face_statuses[record.subject.face.status] = face_statuses.get(record.subject.face.status, 0) + 1
        aesthetic_statuses[record.aesthetic.status] = aesthetic_statuses.get(record.aesthetic.status, 0) + 1
        if record.subject.face.face_count > 0:
            faces_detected += 1
        for status in record.layer_statuses:
            key = f"{status.layer_id}:{status.status}"
            layer_statuses[key] = layer_statuses.get(key, 0) + 1
    return {
        "total_images": len(values),
        "final_buckets": buckets,
        "technical_statuses": technical_statuses,
        "face_statuses": face_statuses,
        "faces_detected": faces_detected,
        "aesthetic_statuses": aesthetic_statuses,
        "layer_statuses": layer_statuses,
    }


if __name__ == "__main__":
    main()
