"""Artifact loading for the local labeling tool."""

from __future__ import annotations

import csv
import heapq
import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional


ProgressCallback = Callable[[int, int, str], None]

import numpy as np

from app.clustering.hashing import compute_dhash, hamming_distance_int
from app.labeling.models import ClusterItem, DatasetBundle, ImageItem


LOGGER = logging.getLogger(__name__)


def load_labeling_dataset(
    artifacts_dir: Path,
    *,
    metadata_filename: str,
    image_ids_filename: str,
    clusters_filename: str,
    collapse_near_identical: bool = True,
    near_identical_similarity_threshold: float = 0.965,
    near_identical_outlier_deviation: float = 0.004,
    filter_unusable: bool = True,
    unusable_shadow_clip_threshold: float = 0.985,
    unusable_highlight_clip_threshold: float = 0.985,
    unusable_contrast_threshold: float = 0.006,
    unusable_sharpness_threshold: float = 0.015,
    filter_semantic_outliers: bool = True,
    semantic_outlier_similarity_threshold: float = 0.55,
    max_labeling_cluster_images: int = 8,
    group_cluster_near_duplicates: bool = True,
    cluster_near_duplicate_hamming_threshold: int = 6,
    progress_callback: Optional[ProgressCallback] = None,
) -> DatasetBundle:
    """Load the artifact bundle used for local labeling."""

    metadata_path = artifacts_dir / metadata_filename
    image_ids_path = artifacts_dir / image_ids_filename
    clusters_path = artifacts_dir / clusters_filename

    if not metadata_path.exists():
        raise FileNotFoundError(f"Metadata file not found: {metadata_path}")
    if not image_ids_path.exists():
        raise FileNotFoundError(f"Image ID file not found: {image_ids_path}")
    if not clusters_path.exists():
        raise FileNotFoundError(f"Cluster file not found: {clusters_path}")

    metadata_rows = _load_csv_rows(metadata_path)
    cluster_rows = _load_csv_rows(clusters_path)
    image_ids = json.loads(image_ids_path.read_text(encoding="utf-8"))

    metadata_by_id = {row["image_id"]: row for row in metadata_rows}
    cluster_rows_by_id = {row["image_id"]: row for row in cluster_rows}

    missing_cluster_ids = [
        image_id for image_id in image_ids if image_id not in cluster_rows_by_id
    ]
    if missing_cluster_ids:
        raise ValueError(
            "clusters.csv is missing image IDs found in image_ids.json. "
            f"Examples: {missing_cluster_ids[:5]}"
        )

    ordered_images: List[ImageItem] = []
    for image_id in image_ids:
        metadata_row = metadata_by_id.get(image_id)
        cluster_row = cluster_rows_by_id[image_id]
        if metadata_row is None:
            raise ValueError(f"images.csv is missing image_id {image_id}.")

        file_path = Path(cluster_row["file_path"])
        ordered_images.append(
            ImageItem(
                image_id=image_id,
                file_path=file_path,
                relative_path=cluster_row.get("relative_path", metadata_row.get("relative_path", "")),
                file_name=cluster_row.get("file_name", metadata_row["file_name"]),
                cluster_id=cluster_row["cluster_id"],
                cluster_size=int(cluster_row["cluster_size"]),
                embedding_index=_parse_optional_int(cluster_row.get("embedding_index")),
                capture_timestamp=cluster_row.get(
                    "capture_timestamp", metadata_row.get("capture_timestamp", "")
                ),
                capture_time_source=cluster_row.get(
                    "capture_time_source",
                    metadata_row.get("capture_time_source", "missing"),
                ),
                timestamp_available=_parse_bool(
                    cluster_row.get(
                        "timestamp_available",
                        metadata_row.get("timestamp_available", "False"),
                    )
                ),
                file_exists=file_path.exists(),
            )
        )

    images_by_id = {image.image_id: image for image in ordered_images}
    clusters_by_id = _build_clusters(cluster_rows, images_by_id)
    embedding_lookup = _load_embedding_lookup(artifacts_dir)

    quality_summary = _filter_unusable_images(
        artifacts_dir=artifacts_dir,
        ordered_images=ordered_images,
        clusters_by_id=clusters_by_id,
        enabled=filter_unusable,
        shadow_clip_threshold=unusable_shadow_clip_threshold,
        highlight_clip_threshold=unusable_highlight_clip_threshold,
        contrast_threshold=unusable_contrast_threshold,
        sharpness_threshold=unusable_sharpness_threshold,
    )
    if quality_summary["filtered_image_ids"]:
        ordered_images, images_by_id, clusters_by_id = _remove_images_from_dataset(
            ordered_images,
            clusters_by_id,
            set(quality_summary["filtered_image_ids"]),
        )

    collapse_summary = _collapse_near_identical_members(
        artifacts_dir=artifacts_dir,
        ordered_images=ordered_images,
        clusters_by_id=clusters_by_id,
        embedding_lookup=embedding_lookup,
        enabled=collapse_near_identical,
        threshold=near_identical_similarity_threshold,
        outlier_deviation=near_identical_outlier_deviation,
    )
    if collapse_summary["collapsed_image_ids"]:
        ordered_images, images_by_id, clusters_by_id = _remove_images_from_dataset(
            ordered_images,
            clusters_by_id,
            set(collapse_summary["collapsed_image_ids"]),
        )

    semantic_summary = _filter_semantic_outlier_members(
        artifacts_dir=artifacts_dir,
        clusters_by_id=clusters_by_id,
        embedding_lookup=embedding_lookup,
        enabled=filter_semantic_outliers,
        threshold=semantic_outlier_similarity_threshold,
    )
    if semantic_summary["filtered_image_ids"]:
        ordered_images, images_by_id, clusters_by_id = _remove_images_from_dataset(
            ordered_images,
            clusters_by_id,
            set(semantic_summary["filtered_image_ids"]),
        )

    subsample_summary = _subsample_large_clusters(
        artifacts_dir=artifacts_dir,
        clusters_by_id=clusters_by_id,
        embedding_lookup=embedding_lookup,
        max_members=max_labeling_cluster_images,
    )
    if subsample_summary["filtered_image_ids"]:
        ordered_images, images_by_id, clusters_by_id = _remove_images_from_dataset(
            ordered_images,
            clusters_by_id,
            set(subsample_summary["filtered_image_ids"]),
        )

    embedding_lookup = {
        image_id: embedding
        for image_id, embedding in embedding_lookup.items()
        if image_id in images_by_id
    }

    phash_lookup = _load_or_compute_phash_lookup(
        artifacts_dir=artifacts_dir,
        images=ordered_images,
        progress_callback=progress_callback,
    )
    cluster_near_duplicate_groups = _group_near_duplicates_in_clusters(
        clusters_by_id=clusters_by_id,
        phash_lookup=phash_lookup,
        enabled=group_cluster_near_duplicates,
        hamming_threshold=cluster_near_duplicate_hamming_threshold,
    )
    multi_image_clusters = [
        cluster
        for cluster in sorted(clusters_by_id.values(), key=lambda item: item.cluster_id)
        if len(cluster.members) >= 2
    ]
    singleton_images = [
        image
        for image in ordered_images
        if len(clusters_by_id.get(image.cluster_id, ClusterItem(image.cluster_id, [], "", "", "")).members) <= 1
    ]

    missing_files = [image.file_name for image in ordered_images if not image.file_exists]
    if missing_files:
        LOGGER.warning(
            "Found %s missing image files while loading labeling data. "
            "They will display as missing in the UI.",
            len(missing_files),
        )

    return DatasetBundle(
        images_by_id=images_by_id,
        ordered_images=ordered_images,
        clusters_by_id=clusters_by_id,
        multi_image_clusters=multi_image_clusters,
        singleton_images=singleton_images,
        embedding_lookup=embedding_lookup,
        phash_lookup=phash_lookup,
        cluster_near_duplicate_groups=cluster_near_duplicate_groups,
        filtered_unusable_count=len(quality_summary["filtered_image_ids"]),
        semantic_outlier_count=len(semantic_summary["filtered_image_ids"]),
        semantic_outlier_group_count=int(semantic_summary["group_count"]),
        cluster_subsample_hidden_count=len(subsample_summary["filtered_image_ids"]),
        cluster_subsampled_count=int(subsample_summary["group_count"]),
        label_filter_report_path=quality_summary["report_path"],
        collapsed_near_duplicate_count=len(collapse_summary["collapsed_image_ids"]),
        near_duplicate_group_count=int(collapse_summary["group_count"]),
        near_duplicate_outlier_count=int(collapse_summary["outlier_count"]),
        near_duplicate_threshold=float(collapse_summary["threshold"]),
        near_duplicate_compared_pair_count=int(collapse_summary["compared_pair_count"]),
        near_duplicate_max_similarity=collapse_summary["max_similarity"],
        near_duplicate_report_path=collapse_summary["report_path"],
        near_duplicate_candidate_report_path=collapse_summary["candidate_report_path"],
        semantic_outlier_report_path=semantic_summary["report_path"],
        cluster_subsample_report_path=subsample_summary["report_path"],
    )


def _build_clusters(
    cluster_rows: List[Dict[str, str]],
    images_by_id: Dict[str, ImageItem],
) -> Dict[str, ClusterItem]:
    """Build ClusterItem objects from clustering output rows."""

    grouped_rows: Dict[str, List[Dict[str, str]]] = {}
    for row in cluster_rows:
        grouped_rows.setdefault(row["cluster_id"], []).append(row)

    clusters: Dict[str, ClusterItem] = {}
    for cluster_id, rows in grouped_rows.items():
        ordered_rows = sorted(
            rows,
            key=lambda row: (
                _parse_optional_int(row.get("cluster_position")) or 0,
                _parse_optional_int(row.get("embedding_index")) or 0,
            ),
        )
        members = [images_by_id[row["image_id"]] for row in ordered_rows]
        first_row = ordered_rows[0]
        clusters[cluster_id] = ClusterItem(
            cluster_id=cluster_id,
            members=members,
            cluster_reason=first_row.get("cluster_reason", ""),
            window_kind=first_row.get("window_kind", ""),
            time_window_id=first_row.get("time_window_id", ""),
        )

    return clusters


def _remove_images_from_dataset(
    ordered_images: List[ImageItem],
    clusters_by_id: Dict[str, ClusterItem],
    filtered_ids: set[str],
) -> tuple[List[ImageItem], Dict[str, ImageItem], Dict[str, ClusterItem]]:
    kept_images = [image for image in ordered_images if image.image_id not in filtered_ids]
    images_by_id = {image.image_id: image for image in kept_images}
    kept_clusters: Dict[str, ClusterItem] = {}
    for cluster_id, cluster in clusters_by_id.items():
        members = [image for image in cluster.members if image.image_id not in filtered_ids]
        if not members:
            continue
        kept_clusters[cluster_id] = ClusterItem(
            cluster_id=cluster.cluster_id,
            members=members,
            cluster_reason=cluster.cluster_reason,
            window_kind=cluster.window_kind,
            time_window_id=cluster.time_window_id,
        )
    return kept_images, images_by_id, kept_clusters


def _filter_unusable_images(
    *,
    artifacts_dir: Path,
    ordered_images: List[ImageItem],
    clusters_by_id: Dict[str, ClusterItem],
    enabled: bool,
    shadow_clip_threshold: float,
    highlight_clip_threshold: float,
    contrast_threshold: float,
    sharpness_threshold: float,
) -> Dict[str, Any]:
    summary: Dict[str, Any] = {"filtered_image_ids": [], "report_path": None}
    if not enabled:
        return summary

    signal_lookup = _load_signal_lookup(artifacts_dir)
    filtered_ids: set[str] = set()
    rows: list[dict[str, Any]] = []
    cluster_sizes = {cluster_id: len(cluster.members) for cluster_id, cluster in clusters_by_id.items()}
    for image in ordered_images:
        if not image.file_exists:
            result = _missing_file_filter_result()
        else:
            signal = signal_lookup.get(image.image_id)
            if signal is None:
                continue
            result = _signal_quality_filter_result(
                image,
                signal,
                shadow_clip_threshold=shadow_clip_threshold,
                highlight_clip_threshold=highlight_clip_threshold,
                contrast_threshold=contrast_threshold,
                sharpness_threshold=sharpness_threshold,
            )
        if not result.get("filtered"):
            continue
        filtered_ids.add(image.image_id)
        rows.append(
            {
                "image_id": image.image_id,
                "file_name": image.file_name,
                "cluster_id": image.cluster_id,
                "cluster_size": cluster_sizes.get(image.cluster_id, image.cluster_size),
                **result,
            }
        )

    report_path = None
    if rows:
        report_path = artifacts_dir / "labeling_unusable_filter.csv"
        _write_filter_report(
            report_path,
            rows,
            fieldnames=[
                "image_id",
                "file_name",
                "cluster_id",
                "cluster_size",
                "filtered",
                "reason",
                "mean_luma",
                "shadow_clip",
                "highlight_clip",
                "contrast",
                "sharpness_score",
                "detail_score",
                "valid_tiles",
            ],
        )
        LOGGER.info("Filtered %s unusable labeling image(s). Report: %s", len(filtered_ids), report_path)

    summary["filtered_image_ids"] = sorted(filtered_ids)
    summary["report_path"] = report_path
    return summary


def _load_signal_lookup(artifacts_dir: Path) -> dict[str, dict[str, Any]]:
    """Load precomputed culling signals without importing the full signal stack."""

    for candidate in (
        artifacts_dir.parent / "signals" / "culling_signals.json",
        artifacts_dir / "signals" / "culling_signals.json",
    ):
        if not candidate.exists():
            continue
        try:
            payload = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            LOGGER.warning("Unable to load labeling filter signals from %s: %s", candidate, exc)
            continue
        if not isinstance(payload, list):
            continue
        return {
            str(item.get("image_id") or ""): item
            for item in payload
            if isinstance(item, dict) and str(item.get("image_id") or "")
        }
    return {}


def _missing_file_filter_result() -> dict[str, Any]:
    return {
        "filtered": True,
        "reason": "missing_file",
        "mean_luma": None,
        "shadow_clip": None,
        "highlight_clip": None,
        "contrast": None,
        "sharpness_score": None,
        "detail_score": None,
        "valid_tiles": None,
    }


def _signal_quality_filter_result(
    image: ImageItem,
    signal: dict[str, Any],
    *,
    shadow_clip_threshold: float,
    highlight_clip_threshold: float,
    contrast_threshold: float,
    sharpness_threshold: float,
) -> dict[str, Any]:
    technical = signal.get("technical") if isinstance(signal.get("technical"), dict) else {}
    status = str(technical.get("status") or "").strip().lower()
    reason = ""
    shadow_clip = _optional_float(technical.get("shadow_clip_ratio"))
    highlight_clip = _optional_float(technical.get("highlight_clip_ratio"))
    contrast = _optional_float(technical.get("contrast_score"))
    sharpness = _optional_float(technical.get("sharpness_score"))
    detail = _optional_float(technical.get("detail_score"))
    exposure_status = str(technical.get("exposure_status") or "").strip().lower()
    technical_reason = str(technical.get("reason") or "").strip()

    if status == "failed" and image.file_path.suffix.casefold() not in _RAW_EXTENSIONS:
        reason = f"technical_failed: {technical_reason}" if technical_reason else "technical_failed"
    elif (
        shadow_clip is not None
        and shadow_clip >= shadow_clip_threshold
        and exposure_status in {"underexposed", "not_analyzed", ""}
    ):
        reason = "unrecoverable_black"
    elif (
        highlight_clip is not None
        and highlight_clip >= highlight_clip_threshold
        and exposure_status in {"overexposed", "not_analyzed", ""}
    ):
        reason = "unrecoverable_white"
    elif (
        contrast is not None
        and contrast <= contrast_threshold
        and (
            (shadow_clip is not None and shadow_clip >= shadow_clip_threshold * 0.9)
            or (highlight_clip is not None and highlight_clip >= highlight_clip_threshold * 0.9)
        )
    ):
        reason = "flat_lens_cap_frame"
    elif (
        sharpness is not None
        and detail is not None
        and sharpness <= sharpness_threshold
        and detail <= sharpness_threshold
        and contrast is not None
        and contrast >= max(contrast_threshold * 2.0, 0.012)
    ):
        reason = "extreme_motion_blur"

    return {
        "filtered": bool(reason),
        "reason": reason,
        "mean_luma": None,
        "shadow_clip": shadow_clip,
        "highlight_clip": highlight_clip,
        "contrast": contrast,
        "sharpness_score": sharpness,
        "detail_score": detail,
        "valid_tiles": None,
    }


def _filter_semantic_outlier_members(
    *,
    artifacts_dir: Path,
    clusters_by_id: Dict[str, ClusterItem],
    embedding_lookup: Dict[str, np.ndarray],
    enabled: bool,
    threshold: float,
) -> Dict[str, Any]:
    summary: Dict[str, Any] = {"filtered_image_ids": [], "group_count": 0, "report_path": None}
    if not enabled or not embedding_lookup:
        return summary

    threshold = max(0.0, min(1.0, float(threshold)))
    filtered_ids: set[str] = set()
    rows: list[dict[str, Any]] = []
    for cluster_id, cluster in clusters_by_id.items():
        if len(cluster.members) > _MAX_SEMANTIC_OUTLIER_FILTER_MEMBERS:
            continue
        members_with_embeddings = [
            image for image in cluster.members if image.image_id in embedding_lookup
        ]
        if len(members_with_embeddings) < 2:
            continue
        components = _near_identical_components(members_with_embeddings, embedding_lookup, threshold)
        if len(components) <= 1:
            continue
        ordered_components = sorted(
            components,
            key=lambda component: (-len(component), min(cluster.members.index(image) for image in component)),
        )
        largest = ordered_components[0]
        if len(largest) < 2:
            hidden = members_with_embeddings
            kept = []
        else:
            kept_ids = {image.image_id for image in largest}
            hidden = [image for image in members_with_embeddings if image.image_id not in kept_ids]
            kept = largest
        if not hidden:
            continue
        filtered_ids.update(image.image_id for image in hidden)
        summary["group_count"] += 1
        rows.append(
            {
                "cluster_id": cluster_id,
                "cluster_size": len(cluster.members),
                "component_count": len(components),
                "kept_count": len(kept),
                "hidden_count": len(hidden),
                "threshold": threshold,
                "kept_file_names": "; ".join(image.file_name for image in kept),
                "hidden_file_names": "; ".join(image.file_name for image in hidden),
            }
        )

    report_path = None
    if rows:
        report_path = artifacts_dir / "labeling_semantic_outlier_filter.csv"
        _write_filter_report(
            report_path,
            rows,
            fieldnames=[
                "cluster_id",
                "cluster_size",
                "component_count",
                "kept_count",
                "hidden_count",
                "threshold",
                "kept_file_names",
                "hidden_file_names",
            ],
        )
        LOGGER.info("Filtered %s semantic outlier labeling image(s). Report: %s", len(filtered_ids), report_path)

    summary["filtered_image_ids"] = sorted(filtered_ids)
    summary["report_path"] = report_path
    return summary


def _subsample_large_clusters(
    *,
    artifacts_dir: Path,
    clusters_by_id: Dict[str, ClusterItem],
    embedding_lookup: Dict[str, np.ndarray],
    max_members: int,
) -> Dict[str, Any]:
    summary: Dict[str, Any] = {"filtered_image_ids": [], "group_count": 0, "report_path": None}
    max_members = int(max_members)
    if max_members <= 0:
        return summary

    filtered_ids: set[str] = set()
    rows: list[dict[str, Any]] = []
    for cluster_id, cluster in clusters_by_id.items():
        if len(cluster.members) <= max_members:
            continue
        kept = _select_diverse_cluster_members(cluster.members, embedding_lookup, max_members)
        kept_ids = {image.image_id for image in kept}
        hidden = [image for image in cluster.members if image.image_id not in kept_ids]
        if not hidden:
            continue
        filtered_ids.update(image.image_id for image in hidden)
        summary["group_count"] += 1
        rows.append(
            {
                "cluster_id": cluster_id,
                "cluster_size": len(cluster.members),
                "max_members": max_members,
                "kept_count": len(kept),
                "hidden_count": len(hidden),
                "kept_file_names": "; ".join(image.file_name for image in kept),
                "hidden_file_names": "; ".join(image.file_name for image in hidden),
            }
        )

    report_path = None
    if rows:
        report_path = artifacts_dir / "labeling_large_cluster_subsample.csv"
        _write_filter_report(
            report_path,
            rows,
            fieldnames=[
                "cluster_id",
                "cluster_size",
                "max_members",
                "kept_count",
                "hidden_count",
                "kept_file_names",
                "hidden_file_names",
            ],
        )
        LOGGER.info("Subsampled %s oversized labeling cluster(s). Report: %s", summary["group_count"], report_path)

    summary["filtered_image_ids"] = sorted(filtered_ids)
    summary["report_path"] = report_path
    return summary


def _collapse_near_identical_members(
    *,
    artifacts_dir: Path,
    ordered_images: List[ImageItem],
    clusters_by_id: Dict[str, ClusterItem],
    embedding_lookup: Dict[str, np.ndarray],
    enabled: bool,
    threshold: float,
    outlier_deviation: float,
) -> Dict[str, Any]:
    """Hide DINO-near-identical images before they reach label sampling."""

    summary: Dict[str, Any] = {
        "collapsed_image_ids": [],
        "group_count": 0,
        "outlier_count": 0,
        "threshold": max(0.0, min(1.0, float(threshold))),
        "compared_pair_count": 0,
        "max_similarity": None,
        "report_path": None,
        "candidate_report_path": None,
    }
    if not enabled:
        return summary

    if not embedding_lookup:
        LOGGER.info(
            "No embeddings were available for labeling near-duplicate collapse at %s.",
            artifacts_dir,
        )
        return summary

    threshold = max(0.0, min(1.0, float(threshold)))
    outlier_deviation = max(0.0, float(outlier_deviation))
    report_rows: list[dict[str, Any]] = []
    collapsed_ids: set[str] = set()
    outlier_ids: set[str] = set()

    components, candidate_summary = _near_identical_label_components(
        ordered_images=ordered_images,
        clusters_by_id=clusters_by_id,
        embedding_lookup=embedding_lookup,
        threshold=threshold,
    )
    summary["compared_pair_count"] = int(candidate_summary["compared_pair_count"])
    summary["max_similarity"] = candidate_summary["max_similarity"]
    candidate_report_rows = candidate_summary["candidate_rows"]
    if candidate_report_rows:
        candidate_report_path = artifacts_dir / "near_identical_labeling_candidates.csv"
        _write_filter_report(
            candidate_report_path,
            candidate_report_rows,
            fieldnames=[
                "source",
                "left_image_id",
                "left_file_name",
                "left_cluster_id",
                "right_image_id",
                "right_file_name",
                "right_cluster_id",
                "similarity",
                "threshold",
                "would_collapse",
            ],
        )
        summary["candidate_report_path"] = candidate_report_path
    for component_index, component in enumerate(components, start=1):
        if len(component) < 2:
            continue
        representative, hidden, outliers, metrics = _collapse_component(
            component,
            embedding_lookup=embedding_lookup,
            threshold=threshold,
            outlier_deviation=outlier_deviation,
        )
        if not hidden:
            continue
        summary["group_count"] += 1
        hidden_ids = {image.image_id for image in hidden}
        outlier_component_ids = {image.image_id for image in outliers}
        collapsed_ids.update(hidden_ids)
        outlier_ids.update(outlier_component_ids)
        cluster_ids = sorted({image.cluster_id for image in component})
        report_rows.append(
            {
                "cluster_id": "; ".join(cluster_ids),
                "component_index": component_index,
                "component_size": len(component),
                "representative_image_id": representative.image_id,
                "representative_file_name": representative.file_name,
                "hidden_count": len(hidden),
                "outlier_count": len(outliers),
                "threshold": threshold,
                "outlier_deviation": outlier_deviation,
                **metrics,
                "hidden_file_names": "; ".join(image.file_name for image in hidden),
                "outlier_file_names": "; ".join(image.file_name for image in outliers),
            }
        )

    if collapsed_ids:
        for cluster_id, cluster in list(clusters_by_id.items()):
            kept_members = [image for image in cluster.members if image.image_id not in collapsed_ids]
            clusters_by_id[cluster_id] = ClusterItem(
                cluster_id=cluster.cluster_id,
                members=kept_members,
                cluster_reason=cluster.cluster_reason,
                window_kind=cluster.window_kind,
                time_window_id=cluster.time_window_id,
            )

    report_path = None
    if report_rows:
        report_path = artifacts_dir / "near_identical_labeling_collapse.csv"
        _write_collapse_report(report_path, report_rows)
        LOGGER.info(
            "Collapsed %s near-identical labeling images across %s group(s). Report: %s",
            len(collapsed_ids),
            summary["group_count"],
            report_path,
        )

    summary["collapsed_image_ids"] = sorted(collapsed_ids)
    summary["outlier_count"] = len(outlier_ids)
    summary["report_path"] = report_path
    return summary


def _phash_cache_candidates(artifacts_dir: Path) -> List[Path]:
    """Return the locations to check (in order) for a stored pHash cache."""

    return [
        artifacts_dir / "phashes.npz",
        artifacts_dir.parent / "artifacts" / "phashes.npz",
    ]


def _load_phash_cache(artifacts_dir: Path) -> tuple[Dict[str, int], Optional[Path]]:
    """Load any existing pHash cache and return it along with the source path."""

    for candidate in _phash_cache_candidates(artifacts_dir):
        if not candidate.exists():
            continue
        try:
            payload = np.load(candidate, allow_pickle=False)
            image_ids = [str(value) for value in payload["image_ids"].tolist()]
            hashes = payload["hashes"].astype(np.uint64, copy=False).tolist()
        except (OSError, ValueError, KeyError) as exc:
            LOGGER.warning("Unable to load pHash cache from %s: %s", candidate, exc)
            continue
        if len(image_ids) != len(hashes):
            continue
        return {image_id: int(value) for image_id, value in zip(image_ids, hashes)}, candidate
    return {}, None


def _write_phash_cache(path: Path, lookup: Dict[str, int]) -> None:
    """Persist a pHash lookup as a small .npz cache."""

    if not lookup:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        ordered_ids = sorted(lookup.keys())
        image_ids_array = np.asarray(ordered_ids)
        hashes_array = np.asarray([lookup[image_id] for image_id in ordered_ids], dtype=np.uint64)
        np.savez(path, image_ids=image_ids_array, hashes=hashes_array)
    except OSError as exc:
        LOGGER.warning("Unable to persist pHash cache to %s: %s", path, exc)


def _load_or_compute_phash_lookup(
    *,
    artifacts_dir: Path,
    images: List[ImageItem],
    progress_callback: Optional[ProgressCallback] = None,
) -> Dict[str, int]:
    """Return a pHash lookup for the labeling images, computing and caching missing entries."""

    if not images:
        return {}

    cached_lookup, source_path = _load_phash_cache(artifacts_dir)
    relevant_ids = {image.image_id for image in images}
    lookup: Dict[str, int] = {
        image_id: value for image_id, value in cached_lookup.items() if image_id in relevant_ids
    }

    missing = [image for image in images if image.image_id not in lookup and image.file_exists]
    if missing:
        computed = _compute_phash_batch(missing, progress_callback=progress_callback)
        lookup.update(computed)

    if lookup and (source_path is None or set(lookup.keys()) - set(cached_lookup.keys())):
        target_path = source_path or _phash_cache_candidates(artifacts_dir)[0]
        merged = dict(cached_lookup)
        merged.update(lookup)
        _write_phash_cache(target_path, merged)

    return lookup


def _compute_phash_batch(
    images: List[ImageItem],
    *,
    progress_callback: Optional[ProgressCallback] = None,
) -> Dict[str, int]:
    """Compute dHashes for the given images using a small thread pool."""

    if not images:
        return {}

    worker_count = min(8, max(1, (os.cpu_count() or 2)))
    total = len(images)
    report_every = max(1, total // 100)

    def _compute(image: ImageItem) -> tuple[str, Optional[int]]:
        return image.image_id, compute_dhash(image.file_path)

    results: Dict[str, int] = {}
    completed = 0
    with ThreadPoolExecutor(max_workers=worker_count) as pool:
        for image_id, hash_value in pool.map(_compute, images):
            if hash_value is not None:
                results[image_id] = int(hash_value)
            completed += 1
            if progress_callback is not None and (
                completed == total or completed % report_every == 0
            ):
                try:
                    progress_callback(completed, total, "computing_phashes")
                except Exception:
                    LOGGER.debug("pHash progress callback raised; ignoring.", exc_info=True)
    return results


def _group_near_duplicates_in_clusters(
    *,
    clusters_by_id: Dict[str, ClusterItem],
    phash_lookup: Dict[str, int],
    enabled: bool,
    hamming_threshold: int,
) -> Dict[str, List[List[ImageItem]]]:
    """Partition each cluster's members into pHash near-duplicate groups."""

    if not enabled:
        return {
            cluster_id: [[member] for member in cluster.members]
            for cluster_id, cluster in clusters_by_id.items()
        }

    threshold = max(0, int(hamming_threshold))
    groups_by_cluster: Dict[str, List[List[ImageItem]]] = {}
    for cluster_id, cluster in clusters_by_id.items():
        groups_by_cluster[cluster_id] = _partition_members_by_phash(
            cluster.members,
            phash_lookup=phash_lookup,
            hamming_threshold=threshold,
        )
    return groups_by_cluster


def _partition_members_by_phash(
    members: List[ImageItem],
    *,
    phash_lookup: Dict[str, int],
    hamming_threshold: int,
) -> List[List[ImageItem]]:
    """Group members by Hamming distance under the threshold; missing pHashes stay singleton."""

    if not members:
        return []

    parent: Dict[str, str] = {member.image_id: member.image_id for member in members}

    def find(image_id: str) -> str:
        while parent[image_id] != image_id:
            parent[image_id] = parent[parent[image_id]]
            image_id = parent[image_id]
        return image_id

    def union(left: str, right: str) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    hashable_members = [member for member in members if member.image_id in phash_lookup]
    for left_index, left in enumerate(hashable_members):
        left_hash = phash_lookup[left.image_id]
        for right in hashable_members[left_index + 1:]:
            distance = hamming_distance_int(left_hash, phash_lookup[right.image_id])
            if distance <= hamming_threshold:
                union(left.image_id, right.image_id)

    grouped: Dict[str, List[ImageItem]] = {}
    member_order = {member.image_id: index for index, member in enumerate(members)}
    for member in members:
        grouped.setdefault(find(member.image_id), []).append(member)

    return sorted(
        grouped.values(),
        key=lambda group: member_order[group[0].image_id],
    )


def _load_embedding_lookup(artifacts_dir: Path) -> Dict[str, np.ndarray]:
    """Load normalized embeddings for labeling image IDs when available."""

    candidates = [
        artifacts_dir,
        artifacts_dir.parent / "artifacts",
    ]
    for candidate in candidates:
        embeddings_path = candidate / "embeddings.npy"
        image_ids_path = candidate / "image_ids.json"
        if not embeddings_path.exists() or not image_ids_path.exists():
            continue
        try:
            embeddings = np.load(embeddings_path).astype(np.float32, copy=False)
            image_ids = json.loads(image_ids_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            LOGGER.warning("Unable to load labeling dedupe embeddings from %s: %s", candidate, exc)
            continue
        if embeddings.ndim != 2 or len(image_ids) != embeddings.shape[0]:
            continue
        normalized = _l2_normalize(embeddings)
        return {
            str(image_id): normalized[index]
            for index, image_id in enumerate(image_ids)
            if str(image_id)
        }
    return {}


def _near_identical_components(
    members: List[ImageItem],
    embeddings: Dict[str, np.ndarray],
    threshold: float,
) -> List[List[ImageItem]]:
    """Return connected components whose DINO cosine similarity crosses the threshold."""

    parent = {image.image_id: image.image_id for image in members}

    def find(image_id: str) -> str:
        while parent[image_id] != image_id:
            parent[image_id] = parent[parent[image_id]]
            image_id = parent[image_id]
        return image_id

    def union(left: str, right: str) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    for left_index, left in enumerate(members):
        left_embedding = embeddings[left.image_id]
        for right in members[left_index + 1:]:
            similarity = float(left_embedding @ embeddings[right.image_id])
            if similarity >= threshold:
                union(left.image_id, right.image_id)

    grouped: dict[str, list[ImageItem]] = {}
    for image in members:
        grouped.setdefault(find(image.image_id), []).append(image)
    return list(grouped.values())


def _near_identical_label_components(
    *,
    ordered_images: List[ImageItem],
    clusters_by_id: Dict[str, ClusterItem],
    embedding_lookup: Dict[str, np.ndarray],
    threshold: float,
) -> tuple[List[List[ImageItem]], dict[str, Any]]:
    """Return near-identical components from cluster-local and nearby file-order comparisons."""

    members = [image for image in ordered_images if image.image_id in embedding_lookup]
    if len(members) < 2:
        return [], {"compared_pair_count": 0, "max_similarity": None, "candidate_rows": []}

    parent = {image.image_id: image.image_id for image in members}
    member_lookup = {image.image_id: image for image in members}
    seen_pairs: set[tuple[str, str]] = set()
    top_candidates: list[tuple[float, int, dict[str, Any]]] = []
    compared_pair_count = 0
    max_similarity: float | None = None
    candidate_counter = 0

    def find(image_id: str) -> str:
        while parent[image_id] != image_id:
            parent[image_id] = parent[parent[image_id]]
            image_id = parent[image_id]
        return image_id

    def union(left: str, right: str) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    def maybe_union(left: ImageItem, right: ImageItem, *, source: str) -> None:
        nonlocal compared_pair_count, max_similarity, candidate_counter
        pair_key = tuple(sorted((left.image_id, right.image_id)))
        if pair_key in seen_pairs:
            return
        seen_pairs.add(pair_key)
        similarity = float(embedding_lookup[left.image_id] @ embedding_lookup[right.image_id])
        compared_pair_count += 1
        max_similarity = similarity if max_similarity is None else max(max_similarity, similarity)
        row = {
            "source": source,
            "left_image_id": left.image_id,
            "left_file_name": left.file_name,
            "left_cluster_id": left.cluster_id,
            "right_image_id": right.image_id,
            "right_file_name": right.file_name,
            "right_cluster_id": right.cluster_id,
            "similarity": f"{similarity:.6f}",
            "threshold": f"{threshold:.6f}",
            "would_collapse": similarity >= threshold,
        }
        candidate_counter += 1
        if len(top_candidates) < _NEAR_IDENTICAL_CANDIDATE_REPORT_LIMIT:
            heapq.heappush(top_candidates, (similarity, candidate_counter, row))
        elif similarity > top_candidates[0][0]:
            heapq.heapreplace(top_candidates, (similarity, candidate_counter, row))
        if similarity >= threshold:
            union(left.image_id, right.image_id)

    for left_index, left in enumerate(members):
        right_limit = min(len(members), left_index + _NEAR_IDENTICAL_SEQUENCE_WINDOW + 1)
        for right in members[left_index + 1:right_limit]:
            maybe_union(left, right, source="sequence")

    for cluster in clusters_by_id.values():
        cluster_members = [
            image
            for image in cluster.members
            if image.image_id in embedding_lookup and image.image_id in member_lookup
        ]
        for left_index, left in enumerate(cluster_members):
            for right in cluster_members[left_index + 1:]:
                maybe_union(left, right, source="cluster")

    grouped: dict[str, list[ImageItem]] = {}
    for image in members:
        grouped.setdefault(find(image.image_id), []).append(image)
    candidate_rows = [
        item[2]
        for item in sorted(top_candidates, key=lambda item: item[0], reverse=True)
    ]
    return list(grouped.values()), {
        "compared_pair_count": compared_pair_count,
        "max_similarity": max_similarity,
        "candidate_rows": candidate_rows,
    }


def _collapse_component(
    component: List[ImageItem],
    *,
    embedding_lookup: Dict[str, np.ndarray],
    threshold: float,
    outlier_deviation: float,
) -> tuple[ImageItem, list[ImageItem], list[ImageItem], dict[str, Any]]:
    """Choose a medoid representative and keep tight-deviation outliers visible."""

    vectors = np.asarray([embedding_lookup[image.image_id] for image in component], dtype=np.float32)
    similarity = vectors @ vectors.T
    mean_similarity = similarity.mean(axis=1)
    representative_index = int(np.argmax(mean_similarity))
    representative = component[representative_index]
    representative_similarity = similarity[representative_index]
    representative_peer_similarity = np.delete(representative_similarity, representative_index)
    max_distance = 1.0 - float(representative_peer_similarity.min())
    mean_distance = 1.0 - float(np.mean(representative_peer_similarity))
    max_allowed_distance = max(1.0 - threshold, outlier_deviation)

    hidden: list[ImageItem] = []
    outliers: list[ImageItem] = []
    for index, image in enumerate(component):
        if image is representative:
            continue
        distance = 1.0 - float(representative_similarity[index])
        if len(component) >= 3 and distance > max_allowed_distance:
            outliers.append(image)
            continue
        hidden.append(image)

    metrics = {
        "min_pairwise_similarity": float(np.min(similarity[np.triu_indices(len(component), k=1)])),
        "mean_pairwise_similarity": float(np.mean(similarity[np.triu_indices(len(component), k=1)])),
        "min_representative_similarity": float(representative_peer_similarity.min()),
        "mean_representative_similarity": float(np.mean(representative_peer_similarity)),
        "max_representative_distance": max_distance,
        "mean_representative_distance": mean_distance,
        "max_allowed_distance": max_allowed_distance,
    }
    return representative, hidden, outliers, metrics


def _write_collapse_report(path: Path, rows: List[Dict[str, Any]]) -> None:
    fieldnames = [
        "cluster_id",
        "component_index",
        "component_size",
        "representative_image_id",
        "representative_file_name",
        "hidden_count",
        "outlier_count",
        "threshold",
        "outlier_deviation",
        "min_pairwise_similarity",
        "mean_pairwise_similarity",
        "min_representative_similarity",
        "mean_representative_similarity",
        "max_representative_distance",
        "mean_representative_distance",
        "max_allowed_distance",
        "hidden_file_names",
        "outlier_file_names",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field) for field in fieldnames})


def _write_filter_report(path: Path, rows: List[Dict[str, Any]], *, fieldnames: List[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field) for field in fieldnames})


def _select_diverse_cluster_members(
    members: List[ImageItem],
    embedding_lookup: Dict[str, np.ndarray],
    max_members: int,
) -> List[ImageItem]:
    if len(members) <= max_members:
        return members

    source_members = members
    if len(source_members) > _MAX_DIVERSE_SUBSAMPLE_CANDIDATES:
        positions = np.linspace(0, len(source_members) - 1, num=_MAX_DIVERSE_SUBSAMPLE_CANDIDATES)
        source_members = [source_members[int(round(position))] for position in positions]

    indexed = [
        (index, image)
        for index, image in enumerate(source_members)
        if image.image_id in embedding_lookup
    ]
    if len(indexed) < max(2, max_members):
        positions = np.linspace(0, len(source_members) - 1, num=max_members)
        chosen_indices = sorted({int(round(position)) for position in positions})
        while len(chosen_indices) < max_members:
            for index in range(len(source_members)):
                if index not in chosen_indices:
                    chosen_indices.append(index)
                    break
        return [source_members[index] for index in sorted(chosen_indices[:max_members])]

    vectors = np.asarray([embedding_lookup[image.image_id] for _, image in indexed], dtype=np.float32)
    similarity = vectors @ vectors.T
    mean_similarity = similarity.mean(axis=1)
    selected_local_indices = [int(np.argmax(mean_similarity))]
    while len(selected_local_indices) < max_members:
        selected_similarity = similarity[:, selected_local_indices]
        min_distance = 1.0 - selected_similarity.max(axis=1)
        for selected in selected_local_indices:
            min_distance[selected] = -1.0
        selected_local_indices.append(int(np.argmax(min_distance)))

    chosen_original_indices = sorted(indexed[local_index][0] for local_index in selected_local_indices)
    return [source_members[index] for index in chosen_original_indices]


def _l2_normalize(matrix: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms = np.where(norms <= 1e-12, 1.0, norms)
    return matrix / norms


def _optional_float(value: object) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


_RAW_EXTENSIONS = {
    ".arw",
    ".cr2",
    ".cr3",
    ".dng",
    ".nef",
    ".nrw",
    ".orf",
    ".raf",
    ".raw",
    ".rw2",
    ".srw",
}

_MAX_SEMANTIC_OUTLIER_FILTER_MEMBERS = 256
_MAX_DIVERSE_SUBSAMPLE_CANDIDATES = 96
_NEAR_IDENTICAL_SEQUENCE_WINDOW = 24
_NEAR_IDENTICAL_CANDIDATE_REPORT_LIMIT = 250


def _load_csv_rows(path: Path) -> List[Dict[str, str]]:
    """Load a CSV file into row dictionaries."""

    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _parse_bool(value: object) -> bool:
    """Parse bool-like CSV values."""

    return str(value).strip().lower() in {"1", "true", "yes"}


def _parse_optional_int(value: Optional[str]) -> Optional[int]:
    """Parse an optional integer from a CSV field."""

    text = (value or "").strip()
    if not text:
        return None
    return int(text)
