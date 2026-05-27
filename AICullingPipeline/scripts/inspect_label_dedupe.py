"""Inspect the pHash near-duplicate grouping produced by the labeling loader.

Run this against an existing labeling artifacts directory to see whether the new
dHash-based grouping catches the visually-identical clusters you've been seeing
in the labeling UI. No labels are written and the labeling UI is not launched.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def main() -> int:
    from app.clustering.hashing import hamming_distance_int
    from app.labeling.loaders import load_labeling_dataset

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--artifacts-dir",
        type=Path,
        required=True,
        help="Path to the labeling_artifacts folder for the source you want to inspect.",
    )
    parser.add_argument(
        "--hamming-threshold",
        type=int,
        default=6,
        help="Hamming distance threshold for grouping (default 6).",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=25,
        help="Number of tightest multi-member groups to print (default 25).",
    )
    args = parser.parse_args()

    artifacts_dir = args.artifacts_dir.expanduser().resolve()
    if not artifacts_dir.exists():
        print(f"Artifacts dir not found: {artifacts_dir}")
        return 1

    dataset = load_labeling_dataset(
        artifacts_dir,
        metadata_filename="images.csv",
        image_ids_filename="image_ids.json",
        clusters_filename="clusters.csv",
        collapse_near_identical=False,
        filter_unusable=False,
        filter_semantic_outliers=False,
        max_labeling_cluster_images=10 ** 9,
        group_cluster_near_duplicates=True,
        cluster_near_duplicate_hamming_threshold=args.hamming_threshold,
    )

    total_clusters = len(dataset.cluster_near_duplicate_groups)
    multi_image_clusters = sum(
        1 for cluster in dataset.clusters_by_id.values() if len(cluster.members) >= 2
    )

    multi_member_groups: list[tuple[str, list, int]] = []
    for cluster_id, groups in dataset.cluster_near_duplicate_groups.items():
        for group in groups:
            if len(group) < 2:
                continue
            hashes = [
                dataset.phash_lookup[image.image_id]
                for image in group
                if image.image_id in dataset.phash_lookup
            ]
            if len(hashes) < 2:
                continue
            max_distance = max(
                hamming_distance_int(hashes[i], hashes[j])
                for i in range(len(hashes))
                for j in range(i + 1, len(hashes))
            )
            multi_member_groups.append((cluster_id, group, max_distance))

    multi_member_groups.sort(key=lambda item: (item[2], -len(item[1])))
    affected_clusters = {item[0] for item in multi_member_groups}

    print(f"Labeling artifacts dir:        {artifacts_dir}")
    print(f"Total clusters:                {total_clusters}")
    print(f"Multi-image clusters:          {multi_image_clusters}")
    print(f"pHash lookup size:             {len(dataset.phash_lookup)} / {len(dataset.ordered_images)} images")
    print(f"Hamming threshold:             {args.hamming_threshold}")
    print(f"Multi-member near-dup groups:  {len(multi_member_groups)}")
    print(f"Clusters with >=1 such group:  {len(affected_clusters)}")
    print()

    if not multi_member_groups:
        print("No near-duplicate groups detected at this threshold.")
        print("Try a looser threshold (e.g. --hamming-threshold 10) to see what the next-closest")
        print("matches look like, or confirm phashes.npz was written to the artifacts dir.")
        return 0

    limit = min(args.top, len(multi_member_groups))
    print(f"Top {limit} tightest groups (smallest max internal Hamming distance first):")
    print("-" * 80)
    for cluster_id, group, max_distance in multi_member_groups[:limit]:
        names = ", ".join(image.file_name for image in group)
        print(f"  cluster={cluster_id} max_hamming={max_distance} size={len(group)}")
        print(f"    {names}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
