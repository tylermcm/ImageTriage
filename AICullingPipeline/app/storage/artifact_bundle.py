"""Artifact-bundle discovery helpers for host-app integration."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from app.contracts import ARTIFACT_CONTRACT_VERSION, ArtifactBundleContract


@dataclass(frozen=True)
class ArtifactBundlePaths:
    """Resolved filesystem paths for one reusable engine artifact bundle."""

    artifacts_dir: Path
    source_input_dir: Path | None
    metadata_path: Path | None
    embeddings_path: Path | None
    image_ids_path: Path | None
    clusters_path: Path | None
    pairwise_labels_path: Path | None = None
    cluster_labels_path: Path | None = None
    scored_clusters_path: Path | None = None
    ranked_export_path: Path | None = None
    evaluation_metrics_path: Path | None = None
    reference_bank_path: Path | None = None
    resolved_config_paths: tuple[Path, ...] = ()

    def to_contract(self) -> ArtifactBundleContract:
        """Convert the discovered bundle into the stable host-facing contract."""

        available_artifacts = {
            key: str(path)
            for key, path in {
                "metadata": self.metadata_path,
                "embeddings": self.embeddings_path,
                "image_ids": self.image_ids_path,
                "clusters": self.clusters_path,
                "pairwise_labels": self.pairwise_labels_path,
                "cluster_labels": self.cluster_labels_path,
                "scored_clusters": self.scored_clusters_path,
                "ranked_export": self.ranked_export_path,
                "evaluation_metrics": self.evaluation_metrics_path,
                "reference_bank": self.reference_bank_path,
            }.items()
            if path is not None
        }

        return ArtifactBundleContract(
            contract_version=ARTIFACT_CONTRACT_VERSION,
            artifacts_dir=str(self.artifacts_dir),
            source_input_dir=str(self.source_input_dir) if self.source_input_dir else None,
            metadata_path=str(self.metadata_path) if self.metadata_path else None,
            embeddings_path=str(self.embeddings_path) if self.embeddings_path else None,
            image_ids_path=str(self.image_ids_path) if self.image_ids_path else None,
            clusters_path=str(self.clusters_path) if self.clusters_path else None,
            pairwise_labels_path=(
                str(self.pairwise_labels_path) if self.pairwise_labels_path else None
            ),
            cluster_labels_path=str(self.cluster_labels_path) if self.cluster_labels_path else None,
            scored_clusters_path=(
                str(self.scored_clusters_path) if self.scored_clusters_path else None
            ),
            ranked_export_path=str(self.ranked_export_path) if self.ranked_export_path else None,
            evaluation_metrics_path=(
                str(self.evaluation_metrics_path) if self.evaluation_metrics_path else None
            ),
            reference_bank_path=str(self.reference_bank_path) if self.reference_bank_path else None,
            resolved_config_paths=tuple(str(path) for path in self.resolved_config_paths),
            available_artifacts=available_artifacts,
        )


def discover_artifact_bundle(
    artifacts_dir: Path,
    *,
    labels_dir: Path | None = None,
    ranking_output_dir: Path | None = None,
    evaluation_output_dir: Path | None = None,
    reference_bank_path: Path | None = None,
    metadata_filename: str = "images.csv",
    embeddings_filename: str = "embeddings.npy",
    image_ids_filename: str = "image_ids.json",
    clusters_filename: str = "clusters.csv",
    pairwise_labels_filename: str = "pairwise_labels.jsonl",
    cluster_labels_filename: str = "cluster_labels.jsonl",
    scored_clusters_filename: str = "scored_clusters.csv",
    ranked_export_filename: str = "ranked_clusters_export.csv",
    evaluation_metrics_filename: str = "ranker_evaluation.json",
) -> ArtifactBundlePaths:
    """Discover the standard artifact files that make up one engine output bundle."""

    artifacts_dir = artifacts_dir.expanduser().resolve()
    labels_dir = _resolve_optional_dir(labels_dir) or _resolve_child_dir(artifacts_dir, "labels")
    ranking_output_dir = _resolve_optional_dir(ranking_output_dir)
    evaluation_output_dir = _resolve_optional_dir(evaluation_output_dir)
    reference_bank_path = reference_bank_path.expanduser().resolve() if reference_bank_path else None

    resolved_configs = tuple(sorted(artifacts_dir.glob("*resolved_config*.json")))
    source_input_dir = _load_source_input_dir(resolved_configs)

    return ArtifactBundlePaths(
        artifacts_dir=artifacts_dir,
        source_input_dir=source_input_dir,
        metadata_path=_optional_existing_path(artifacts_dir / metadata_filename),
        embeddings_path=_optional_existing_path(artifacts_dir / embeddings_filename),
        image_ids_path=_optional_existing_path(artifacts_dir / image_ids_filename),
        clusters_path=_optional_existing_path(artifacts_dir / clusters_filename),
        pairwise_labels_path=_discover_labels_file(labels_dir, pairwise_labels_filename, "pairwise_labels.json"),
        cluster_labels_path=_discover_labels_file(labels_dir, cluster_labels_filename, "cluster_labels.json"),
        scored_clusters_path=(
            _optional_existing_path(ranking_output_dir / scored_clusters_filename)
            if ranking_output_dir is not None
            else None
        ),
        ranked_export_path=(
            _optional_existing_path(ranking_output_dir / ranked_export_filename)
            if ranking_output_dir is not None
            else None
        ),
        evaluation_metrics_path=(
            _optional_existing_path(evaluation_output_dir / evaluation_metrics_filename)
            if evaluation_output_dir is not None
            else None
        ),
        reference_bank_path=_optional_existing_path(reference_bank_path) if reference_bank_path else None,
        resolved_config_paths=resolved_configs,
    )


def build_artifact_contract(*args: object, **kwargs: object) -> ArtifactBundleContract:
    """Discover an artifact bundle and return the versioned contract manifest."""

    return discover_artifact_bundle(*args, **kwargs).to_contract()


def _resolve_optional_dir(path: Path | None) -> Path | None:
    """Resolve an optional directory path when provided."""

    if path is None:
        return None
    return path.expanduser().resolve()


def _resolve_child_dir(parent: Path, name: str) -> Path | None:
    """Return a child directory when it exists."""

    candidate = parent / name
    return candidate if candidate.exists() else None


def _optional_existing_path(path: Path | None) -> Path | None:
    """Return the path when it exists, else None."""

    if path is None:
        return None
    return path if path.exists() else None


def _discover_labels_file(labels_dir: Path | None, primary_name: str, fallback_name: str) -> Path | None:
    """Resolve the preferred labels file, allowing JSONL or JSON."""

    if labels_dir is None:
        return None

    primary = labels_dir / primary_name
    if primary.exists():
        return primary

    fallback = labels_dir / fallback_name
    if fallback.exists():
        return fallback
    return None


def _load_source_input_dir(resolved_config_paths: tuple[Path, ...]) -> Path | None:
    """Load the original extraction input root from resolved config artifacts when present."""

    for path in resolved_config_paths:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue

        input_dir = payload.get("input_dir")
        if not input_dir:
            continue
        return Path(str(input_dir)).expanduser().resolve()
    return None
