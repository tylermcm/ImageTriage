"""CLI: harvest Image Triage manual-cull decisions into a JSONL labels file.

Reads from the host's DecisionStore (decisions.sqlite3) and emits one row per
image in a labeling artifacts directory. Use it to feed the AI training
pipeline directly from the manual culling you already do in the main app.

Example:

    python scripts/harvest_decisions.py \\
        --artifacts-dir "C:/.../label_sources/<ns>/labeling_artifacts"

If --output-path is omitted, the labels file is written to
<source_root>/labels/decision_labels.jsonl.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def main() -> int:
    from app.decision_harvest import (
        find_decision_store_db,
        harvest_decisions_for_artifacts,
    )

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--artifacts-dir",
        type=Path,
        required=True,
        help="Path to the labeling_artifacts folder produced by the prepare step.",
    )
    parser.add_argument(
        "--output-path",
        type=Path,
        default=None,
        help="Output JSONL path. Defaults to <source_root>/labels/decision_labels.jsonl.",
    )
    parser.add_argument(
        "--db-path",
        type=Path,
        default=None,
        help="Override the path to decisions.sqlite3 (autodetected if omitted).",
    )
    parser.add_argument(
        "--session-id",
        type=str,
        default="Default",
        help="DecisionStore session to read (default: 'Default').",
    )
    parser.add_argument(
        "--require-unchanged-file",
        action="store_true",
        help="Mark a stored decision as stale if the file's mtime/size has changed since it was saved.",
    )
    parser.add_argument(
        "--skip-cluster-labels",
        action="store_true",
        help="Do not emit the cluster_labels.jsonl bridge file (per-image JSONL only).",
    )
    parser.add_argument(
        "--cluster-labels-path",
        type=Path,
        default=None,
        help="Output path for cluster_labels.jsonl. Defaults to <output_path>.parent/cluster_labels.jsonl.",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default="INFO",
        help="Logging level (default INFO).",
    )
    args = parser.parse_args()

    logging.basicConfig(level=args.log_level.upper(), format="%(levelname)s %(message)s")

    artifacts_dir = args.artifacts_dir.expanduser().resolve()
    if not artifacts_dir.exists():
        print(f"Artifacts dir not found: {artifacts_dir}", file=sys.stderr)
        return 1

    db_path = find_decision_store_db(override=args.db_path)
    if db_path is None:
        print(
            "decisions.sqlite3 not found in any known location. "
            "Pass --db-path to point at it explicitly.",
            file=sys.stderr,
        )
        return 2

    summary = harvest_decisions_for_artifacts(
        artifacts_dir=artifacts_dir,
        output_path=args.output_path,
        db_path_override=db_path,
        session_id=args.session_id,
        require_unchanged_file=args.require_unchanged_file,
        emit_cluster_labels=not args.skip_cluster_labels,
        cluster_labels_path=args.cluster_labels_path,
    )

    print(f"Source artifacts:  {summary.artifacts_dir}")
    print(f"DecisionStore DB:  {summary.db_path}")
    print(f"Session:           {summary.session_id}")
    print(f"Per-image labels:  {summary.output_path}")
    if summary.cluster_labels_path is not None:
        print(f"Cluster labels:    {summary.cluster_labels_path}")
        print(f"  Clusters w/labels: {summary.clusters_with_labels}")
        print(f"  Derivable pairs:   {summary.derivable_pairs}")
    print(f"Total images:      {summary.total_images}")
    print(f"With decisions:    {summary.matched_decisions}")
    print(f"  Winners:         {summary.winners}")
    print(f"  Rejects:         {summary.rejects}")
    print(f"  Rated (>0 star): {summary.rated}")
    print(f"  Photoshop:       {summary.photoshop_marks}")
    if summary.skipped_no_file:
        print(f"Missing files:     {summary.skipped_no_file}")
    if summary.skipped_modified:
        print(f"Stale (file changed since decision): {summary.skipped_modified}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
