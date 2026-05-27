"""CLI entry point for transparent culling-signal combiner tuning."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.engine.signals import SignalCombinerSourceConfig, SignalCombinerTrainingConfig, train_signal_combiner


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Train transparent culling-signal weights from saved labels."
    )
    parser.add_argument("--artifacts-dir", type=Path)
    parser.add_argument("--labels-dir", type=Path)
    parser.add_argument("--signals-path", type=Path)
    parser.add_argument("--source-manifest", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--profile", type=str, default="General Use")
    parser.add_argument("--epochs", type=int, default=400)
    parser.add_argument("--learning-rate", type=float, default=0.08)
    parser.add_argument("--validation-fraction", type=float, default=0.20)
    parser.add_argument("--anchor-strength", type=float, default=0.015)
    parser.add_argument("--l2-strength", type=float, default=0.002)
    parser.add_argument("--max-abs-weight", type=float, default=1.0)
    parser.add_argument("--seed", type=int, default=17)
    parser.add_argument("--skip-cluster-label-pairs", action="store_true")
    parser.add_argument(
        "--keep-near-identical-pairs",
        action="store_true",
        help="Do not filter near-identical preference pairs while tuning.",
    )
    parser.add_argument("--near-identical-threshold", type=float, default=0.985)
    parser.add_argument("--min-feature-delta-coverage", type=float, default=0.03)
    parser.add_argument("--min-feature-standalone-accuracy", type=float, default=0.52)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    sources = _load_sources(args.source_manifest)
    if not sources and (args.artifacts_dir is None or args.labels_dir is None or args.signals_path is None):
        raise SystemExit("Provide either --source-manifest or --artifacts-dir, --labels-dir, and --signals-path.")
    config = SignalCombinerTrainingConfig(
        artifacts_dir=args.artifacts_dir or Path("."),
        labels_dir=args.labels_dir or Path("."),
        signals_path=args.signals_path or Path("."),
        output_dir=args.output_dir,
        sources=tuple(sources),
        profile_name=args.profile,
        epochs=args.epochs,
        learning_rate=args.learning_rate,
        validation_fraction=args.validation_fraction,
        anchor_strength=args.anchor_strength,
        l2_strength=args.l2_strength,
        max_abs_weight=args.max_abs_weight,
        seed=args.seed,
        include_cluster_label_pairs=not args.skip_cluster_label_pairs,
        filter_near_identical_pairs=not args.keep_near_identical_pairs,
        near_identical_similarity_threshold=args.near_identical_threshold,
        min_feature_delta_coverage=args.min_feature_delta_coverage,
        min_feature_standalone_accuracy=args.min_feature_standalone_accuracy,
    )
    outputs = train_signal_combiner(config)
    print("Culling combiner tuning complete.")
    for name, path in outputs.items():
        print(f"{name}: {path}")

    payload = json.loads(Path(outputs["weights"]).read_text(encoding="utf-8"))
    metrics = payload.get("training", {}).get("metrics", {})
    print("")
    print("Summary:")
    print(f"rows: {payload.get('training', {}).get('row_count', 0)}")
    print(f"sources: {payload.get('training', {}).get('source_count', 1)}")
    print(f"filtered_near_identical_pairs: {payload.get('training', {}).get('filtered_near_identical_pairs', 0)}")
    print(f"preference_accuracy: {_format_metric(metrics.get('preference_accuracy'))}")
    print(f"validation_accuracy: {_format_metric(metrics.get('validation_accuracy'))}")
    disabled_features = payload.get("training", {}).get("disabled_features", [])
    if disabled_features:
        print(f"disabled_features: {', '.join(str(item) for item in disabled_features)}")
    print("learned_weights:")
    for feature, value in sorted(payload.get("learned_weights", {}).items()):
        print(f"  {feature}: {float(value):+.4f}")


def _format_metric(value: object) -> str:
    if value is None:
        return "n/a"
    return f"{float(value):.4f}"


def _load_sources(path: Path | None) -> list[SignalCombinerSourceConfig]:
    if path is None:
        return []
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        payload = payload.get("sources", [])
    if not isinstance(payload, list):
        raise ValueError(f"Expected source manifest list at {path}")
    sources: list[SignalCombinerSourceConfig] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        artifacts_dir = item.get("artifacts_dir")
        labels_dir = item.get("labels_dir")
        signals_path = item.get("signals_path")
        if not artifacts_dir or not labels_dir or not signals_path:
            continue
        sources.append(
            SignalCombinerSourceConfig(
                artifacts_dir=Path(str(artifacts_dir)),
                labels_dir=Path(str(labels_dir)),
                signals_path=Path(str(signals_path)),
                source_name=str(item.get("source_name") or item.get("folder") or artifacts_dir),
            )
        )
    if not sources:
        raise ValueError(f"No usable sources were found in {path}")
    return sources


if __name__ == "__main__":
    main()
