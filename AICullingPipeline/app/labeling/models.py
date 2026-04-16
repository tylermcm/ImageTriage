"""Data models used by the local labeling tool."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional


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


@dataclass(frozen=True)
class PairCandidate:
    """A pair of images presented for pairwise preference labeling."""

    image_a: ImageItem
    image_b: ImageItem
    source_mode: str
    cluster_id: Optional[str]


@dataclass
class DatasetBundle:
    """All images and clusters needed by the labeling app."""

    images_by_id: Dict[str, ImageItem]
    ordered_images: List[ImageItem]
    clusters_by_id: Dict[str, ClusterItem]
    multi_image_clusters: List[ClusterItem]
    singleton_images: List[ImageItem]
