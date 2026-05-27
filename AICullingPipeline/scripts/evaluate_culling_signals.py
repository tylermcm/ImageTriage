"""CLI entry point for checkpoint-free culling signal evaluation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.engine.signals import evaluate_culling_signals


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate culling signal scores against saved training labels."
    )
    parser.add_argument("--artifacts-dir", type=Path, required=True)
    parser.add_argument("--labels-dir", type=Path, required=True)
    parser.add_argument("--signals-path", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--top-k", type=int, nargs="*", default=[1, 3])
    parser.add_argument("--metadata-filename", default="images.csv")
    parser.add_argument("--embeddings-filename", default="embeddings.npy")
    parser.add_argument("--image-ids-filename", default="image_ids.json")
    parser.add_argument("--clusters-filename", default="clusters.csv")
    parser.add_argument("--pairwise-labels-filename", default="pairwise_labels.jsonl")
    parser.add_argument("--cluster-labels-filename", default="cluster_labels.jsonl")
    parser.add_argument("--skip-cluster-label-pairs", action="store_true")
    parser.add_argument(
        "--near-identical-threshold",
        type=float,
        default=0.985,
        help="DINO cosine similarity threshold used to report distinct-only pairwise metrics.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    outputs = evaluate_culling_signals(
        artifacts_dir=args.artifacts_dir,
        labels_dir=args.labels_dir,
        signals_path=args.signals_path,
        output_dir=args.output_dir,
        metadata_filename=args.metadata_filename,
        embeddings_filename=args.embeddings_filename,
        image_ids_filename=args.image_ids_filename,
        clusters_filename=args.clusters_filename,
        pairwise_labels_filename=args.pairwise_labels_filename,
        cluster_labels_filename=args.cluster_labels_filename,
        include_cluster_label_pairs=not args.skip_cluster_label_pairs,
        top_k_values=tuple(args.top_k),
        near_identical_similarity_threshold=float(args.near_identical_threshold),
    )

    print("Culling signal evaluation complete.")
    for name, path in outputs.items():
        print(f"{name}: {path}")

    summary = json.loads(Path(outputs["metrics"]).read_text(encoding="utf-8"))
    print("")
    print("Summary:")
    print(
        "pairwise_accuracy(all_preferences): "
        f"{_format_metric(summary['pairwise_evaluation']['all_preferences']['accuracy'])}"
    )
    distinct_pairwise = summary["pairwise_evaluation"].get("all_preferences_distinct", {})
    if distinct_pairwise:
        print(
            "pairwise_accuracy(distinct_only): "
            f"{_format_metric(distinct_pairwise.get('accuracy'))}"
        )
    print(
        "pairwise_evaluated_pairs: "
        f"{summary['pairwise_evaluation']['all_preferences']['evaluated_pairs']}"
    )
    near_identical = summary.get("near_identical_pair_filter", {})
    if isinstance(near_identical, dict):
        print(
            "near_identical_pairs_filtered: "
            f"{near_identical.get('flagged_pairs', 0)}/{near_identical.get('total_pairs', 0)} "
            f"@ {near_identical.get('threshold', 'n/a')}"
        )
    print(
        "cluster_top1_accuracy: "
        f"{_format_metric(summary['cluster_evaluation']['top_k_metrics']['top_1']['hit_rate'])}"
    )
    top_3_metrics = summary["cluster_evaluation"]["top_k_metrics"].get("top_3")
    if top_3_metrics is not None:
        print(f"cluster_top3_hit_rate: {_format_metric(top_3_metrics['hit_rate'])}")
    print(f"evaluated_clusters: {summary['cluster_evaluation']['evaluated_clusters']}")

    comparison = summary.get("baseline_comparison", {}).get("scorers", {})
    if comparison:
        print("")
        print("Signal comparison:")
        for key in ("random_expected", "file_order", "dino_centrality", "transparent_combiner"):
            scorer = comparison.get(key)
            if not isinstance(scorer, dict):
                continue
            pairwise = scorer.get("pairwise_evaluation", {}).get("all_preferences", {})
            distinct = scorer.get("pairwise_evaluation", {}).get("all_preferences_distinct", {})
            cluster = scorer.get("cluster_evaluation", {})
            top_k = cluster.get("top_k_metrics", {})
            top1 = top_k.get("top_1", {})
            top3 = top_k.get("top_3", {})
            print(
                f"{scorer.get('display_name', key)}: "
                f"pairwise={_format_metric(pairwise.get('accuracy'))} "
                f"distinct={_format_metric(distinct.get('accuracy'))} "
                f"top1={_format_metric(top1.get('hit_rate'))} "
                f"top3={_format_metric(top3.get('hit_rate'))} "
                f"mean_best_rank={_format_metric(cluster.get('mean_first_human_best_rank'))}"
            )

        feature_rows = []
        for key, scorer in comparison.items():
            if not isinstance(scorer, dict) or not str(key).startswith("feature:"):
                continue
            pairwise = scorer.get("pairwise_evaluation", {}).get("all_preferences", {})
            distinct = scorer.get("pairwise_evaluation", {}).get("all_preferences_distinct", {})
            cluster = scorer.get("cluster_evaluation", {})
            top_k = cluster.get("top_k_metrics", {})
            top1 = top_k.get("top_1", {})
            accuracy = pairwise.get("accuracy")
            distinct_accuracy = distinct.get("accuracy")
            feature_rows.append(
                (
                    -1.0 if distinct_accuracy is None else float(distinct_accuracy),
                    str(scorer.get("feature_name") or str(key).partition(":")[2]),
                    accuracy,
                    top1.get("hit_rate"),
                    cluster.get("mean_first_human_best_rank"),
                )
            )
        if feature_rows:
            print("")
            print("Feature diagnostics:")
            for distinct_accuracy, feature_name, accuracy, top1, mean_rank in sorted(feature_rows, reverse=True):
                print(
                    f"{feature_name}: "
                    f"pairwise={_format_metric(accuracy)} "
                    f"distinct={_format_metric(None if distinct_accuracy < 0 else distinct_accuracy)} "
                    f"top1={_format_metric(top1)} "
                    f"mean_best_rank={_format_metric(mean_rank)}"
                )


def _format_metric(value: object) -> str:
    if value is None:
        return "n/a"
    return f"{float(value):.4f}"


if __name__ == "__main__":
    main()
