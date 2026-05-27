"""Data models used by the local labeling tool."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np


@dataclass(frozen=True)
class ImageItem:
    """One image resolved from the artifact set."""

    image_id: str
    file_path: Path
    relative_path: str
    file_name: str
    cluster_id: str
    cluster_size: int
    embedding_index: Optional[int]
    capture_timestamp: str
    capture_time_source: str
    timestamp_available: bool
    file_exists: bool


@dataclass(frozen=True)
class ClusterItem:
    """A culling cluster with its ordered image members."""

    cluster_id: str
    members: List[ImageItem]
    cluster_reason: str
    window_kind: str
    time_window_id: str


@dataclass
class DatasetBundle:
    """All images and clusters needed by the labeling app."""

    images_by_id: Dict[str, ImageItem]
    ordered_images: List[ImageItem]
    clusters_by_id: Dict[str, ClusterItem]
    multi_image_clusters: List[ClusterItem]
    singleton_images: List[ImageItem]
    embedding_lookup: Dict[str, np.ndarray] = field(default_factory=dict)
    phash_lookup: Dict[str, int] = field(default_factory=dict)
    cluster_near_duplicate_groups: Dict[str, List[List[ImageItem]]] = field(default_factory=dict)
    filtered_unusable_count: int = 0
    semantic_outlier_count: int = 0
    semantic_outlier_group_count: int = 0
    cluster_subsample_hidden_count: int = 0
    cluster_subsampled_count: int = 0
    label_filter_report_path: Optional[Path] = None
    collapsed_near_duplicate_count: int = 0
    near_duplicate_group_count: int = 0
    near_duplicate_outlier_count: int = 0
    near_duplicate_threshold: float = 0.965
    near_duplicate_compared_pair_count: int = 0
    near_duplicate_max_similarity: Optional[float] = None
    near_duplicate_report_path: Optional[Path] = None
    near_duplicate_candidate_report_path: Optional[Path] = None
    semantic_outlier_report_path: Optional[Path] = None
    cluster_subsample_report_path: Optional[Path] = None
