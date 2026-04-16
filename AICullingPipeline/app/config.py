"""Configuration loading and validation for extraction, clustering, labeling, and ranking."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
import json
import os
from pathlib import Path
from typing import Any, Optional, Union


DEFAULT_MODEL_NAME = "vit_base_patch14_dinov2.lvd142m"
DEFAULT_FALLBACK_MODEL_NAME = "vit_small_patch14_dinov2.lvd142m"
DEFAULT_EXTENSIONS = (
    ".jpg",
    ".jpeg",
    ".png",
    ".bmp",
    ".tif",
    ".tiff",
    ".webp",
)
DEFAULT_REFERENCE_BUCKETS = ("terrible", "bad", "okay", "good", "great")


@dataclass
class ExtractionConfig:
    """Serializable runtime configuration for the embedding pipeline."""

    input_dir: Path
    output_dir: Path
    model_name: str = DEFAULT_MODEL_NAME
    fallback_model_name: Optional[str] = DEFAULT_FALLBACK_MODEL_NAME
    allow_model_fallback: bool = True
    batch_size: int = 8
    num_workers: int = 0
    scan_workers: int = field(default_factory=lambda: max(1, min(8, os.cpu_count() or 4)))
    device: str = "auto"
    image_size: Optional[int] = None
    supported_extensions: tuple[str, ...] = DEFAULT_EXTENSIONS
    metadata_filename: str = "images.csv"
    embeddings_filename: str = "embeddings.npy"
    image_ids_filename: str = "image_ids.json"
    log_level: str = "INFO"

    @classmethod
    def from_file(cls, path: Union[str, Path]) -> "ExtractionConfig":
        """Load configuration from a JSON file."""

        config_path = Path(path).expanduser().resolve()
        payload = json.loads(config_path.read_text(encoding="utf-8"))
        return cls.from_dict(payload, base_dir=config_path.parent)

    @classmethod
    def from_dict(
        cls,
        payload: dict[str, Any],
        *,
        base_dir: Optional[Path] = None,
    ) -> "ExtractionConfig":
        """Create a config object from a dictionary payload."""

        base_dir = base_dir or Path.cwd()
        normalized: dict[str, Any] = dict(payload)
        normalized["input_dir"] = _resolve_path(normalized["input_dir"], base_dir)
        normalized["output_dir"] = _resolve_path(normalized["output_dir"], base_dir)
        normalized["model_name"] = _resolve_optional_model_reference(
            normalized["model_name"], base_dir
        )
        normalized["fallback_model_name"] = _resolve_optional_model_reference(
            normalized.get("fallback_model_name"), base_dir
        )

        if "supported_extensions" in normalized:
            normalized["supported_extensions"] = tuple(
                ext.lower() for ext in normalized["supported_extensions"]
            )

        config = cls(**normalized)
        config.validate()
        return config

    def apply_overrides(self, **overrides: Any) -> "ExtractionConfig":
        """Return a new config with non-null override values applied."""

        updated = asdict(self)
        for key, value in overrides.items():
            if value is None:
                continue

            if key in {"input_dir", "output_dir"}:
                updated[key] = _resolve_path(value, Path.cwd())
            elif key in {"model_name", "fallback_model_name"}:
                updated[key] = _resolve_optional_model_reference(value, Path.cwd())
            elif key == "supported_extensions":
                updated[key] = tuple(ext.lower() for ext in value)
            else:
                updated[key] = value

        config = ExtractionConfig(**updated)
        config.validate()
        return config

    def validate(self) -> None:
        """Validate configuration values."""

        if self.batch_size <= 0:
            raise ValueError("batch_size must be greater than 0.")

        if self.num_workers < 0:
            raise ValueError("num_workers must be 0 or greater.")

        if self.scan_workers <= 0:
            raise ValueError("scan_workers must be greater than 0.")

        if self.image_size is not None and self.image_size <= 0:
            raise ValueError("image_size must be null or greater than 0.")

        if not self.model_name:
            raise ValueError("model_name must be provided.")

        if self.device not in {"auto", "cpu"} and not self.device.startswith("cuda"):
            raise ValueError("device must be 'auto', 'cpu', or a cuda device string.")

        if not self.supported_extensions:
            raise ValueError("supported_extensions must not be empty.")

    def to_serializable_dict(self) -> dict[str, Any]:
        """Convert the config to a JSON-safe dictionary."""

        payload = asdict(self)
        payload["input_dir"] = str(self.input_dir)
        payload["output_dir"] = str(self.output_dir)
        payload["supported_extensions"] = list(self.supported_extensions)
        return payload


@dataclass
class ClusteringConfig:
    """Serializable runtime configuration for culling-oriented clustering."""

    artifacts_dir: Path
    output_dir: Path
    clustering_method: str = "graph"
    similarity_threshold: float = 0.94
    dbscan_eps: float = 0.1
    dbscan_min_samples: int = 2
    minimum_cluster_size: int = 1
    max_time_gap_seconds: float = 30.0
    time_filter_required: bool = True
    timestamp_fallback_mode: str = "none"
    filename_order_window: int = 3
    enrich_missing_timestamps: bool = True
    use_perceptual_hash_filter: bool = False
    max_hash_distance: Optional[int] = 8
    hash_size: int = 8
    use_akaze_verifier: bool = False
    akaze_max_side: int = 1024
    akaze_ratio_test_threshold: float = 0.8
    akaze_min_good_matches: int = 12
    akaze_min_inliers: int = 8
    akaze_min_inlier_ratio: float = 0.4
    representative_gate_enabled: bool = True
    representative_gate_min_cluster_size: int = 5
    representative_min_matches: int = 2
    cluster_relink_enabled: bool = False
    cluster_relink_similarity_threshold: float = 0.98
    cluster_relink_centroid_threshold: float = 0.96
    cluster_relink_max_sequence_gap: int = 96
    cluster_relink_min_matches: int = 2
    metadata_filename: str = "images.csv"
    embeddings_filename: str = "embeddings.npy"
    image_ids_filename: str = "image_ids.json"
    clusters_filename: str = "clusters.csv"
    summary_filename: str = "cluster_summary.json"
    report_filename: str = "clusters_report.txt"
    log_level: str = "INFO"

    @classmethod
    def from_file(cls, path: Union[str, Path]) -> "ClusteringConfig":
        """Load clustering configuration from a JSON file."""

        config_path = Path(path).expanduser().resolve()
        payload = json.loads(config_path.read_text(encoding="utf-8"))
        return cls.from_dict(payload, base_dir=config_path.parent)

    @classmethod
    def from_dict(
        cls,
        payload: dict[str, Any],
        *,
        base_dir: Optional[Path] = None,
    ) -> "ClusteringConfig":
        """Create a clustering config from a dictionary payload."""

        base_dir = base_dir or Path.cwd()
        normalized: dict[str, Any] = dict(payload)
        normalized["artifacts_dir"] = _resolve_path(normalized["artifacts_dir"], base_dir)
        normalized["output_dir"] = _resolve_path(normalized["output_dir"], base_dir)

        config = cls(**normalized)
        config.validate()
        return config

    def apply_overrides(self, **overrides: Any) -> "ClusteringConfig":
        """Return a new clustering config with non-null override values applied."""

        updated = asdict(self)
        for key, value in overrides.items():
            if value is None:
                continue

            if key in {"artifacts_dir", "output_dir"}:
                updated[key] = _resolve_path(value, Path.cwd())
            else:
                updated[key] = value

        config = ClusteringConfig(**updated)
        config.validate()
        return config

    def validate(self) -> None:
        """Validate clustering configuration values."""

        if self.clustering_method not in {"graph", "dbscan"}:
            raise ValueError("clustering_method must be 'graph' or 'dbscan'.")

        if not 0.0 <= self.similarity_threshold <= 1.0:
            raise ValueError("similarity_threshold must be between 0.0 and 1.0.")

        if not 0.0 <= self.dbscan_eps <= 2.0:
            raise ValueError("dbscan_eps must be between 0.0 and 2.0.")

        if not 0.0 <= self.cluster_relink_similarity_threshold <= 1.0:
            raise ValueError(
                "cluster_relink_similarity_threshold must be between 0.0 and 1.0."
            )

        if not 0.0 <= self.cluster_relink_centroid_threshold <= 1.0:
            raise ValueError(
                "cluster_relink_centroid_threshold must be between 0.0 and 1.0."
            )

        if self.dbscan_min_samples <= 0:
            raise ValueError("dbscan_min_samples must be greater than 0.")

        if self.minimum_cluster_size <= 0:
            raise ValueError("minimum_cluster_size must be greater than 0.")

        if self.max_time_gap_seconds < 0:
            raise ValueError("max_time_gap_seconds must be 0 or greater.")

        if self.timestamp_fallback_mode not in {"none", "filename_order"}:
            raise ValueError(
                "timestamp_fallback_mode must be 'none' or 'filename_order'."
            )

        if self.filename_order_window <= 0:
            raise ValueError("filename_order_window must be greater than 0.")

        if self.hash_size <= 0:
            raise ValueError("hash_size must be greater than 0.")

        if self.akaze_max_side <= 0:
            raise ValueError("akaze_max_side must be greater than 0.")

        if not 0.0 < self.akaze_ratio_test_threshold < 1.0:
            raise ValueError("akaze_ratio_test_threshold must be between 0.0 and 1.0.")

        if self.akaze_min_good_matches <= 0:
            raise ValueError("akaze_min_good_matches must be greater than 0.")

        if self.akaze_min_inliers <= 0:
            raise ValueError("akaze_min_inliers must be greater than 0.")

        if not 0.0 <= self.akaze_min_inlier_ratio <= 1.0:
            raise ValueError("akaze_min_inlier_ratio must be between 0.0 and 1.0.")

        if self.max_hash_distance is not None and self.max_hash_distance < 0:
            raise ValueError("max_hash_distance must be null or greater than or equal to 0.")

        if self.representative_gate_min_cluster_size <= 0:
            raise ValueError(
                "representative_gate_min_cluster_size must be greater than 0."
            )

        if self.representative_min_matches <= 0:
            raise ValueError("representative_min_matches must be greater than 0.")

        if self.cluster_relink_max_sequence_gap < 0:
            raise ValueError("cluster_relink_max_sequence_gap must be 0 or greater.")

        if self.cluster_relink_min_matches <= 0:
            raise ValueError("cluster_relink_min_matches must be greater than 0.")

    def to_serializable_dict(self) -> dict[str, Any]:
        """Convert the clustering config to a JSON-safe dictionary."""

        payload = asdict(self)
        payload["artifacts_dir"] = str(self.artifacts_dir)
        payload["output_dir"] = str(self.output_dir)
        return payload


@dataclass
class LabelingConfig:
    """Serializable runtime configuration for the local labeling app."""

    artifacts_dir: Path
    output_dir: Path
    metadata_filename: str = "images.csv"
    image_ids_filename: str = "image_ids.json"
    clusters_filename: str = "clusters.csv"
    pairwise_labels_filename: str = "pairwise_labels.jsonl"
    cluster_labels_filename: str = "cluster_labels.jsonl"
    annotator_id: Optional[str] = None
    pair_preview_max_width: int = 1100
    pair_preview_max_height: int = 900
    cluster_preview_height: int = 280
    cluster_grid_columns: int = 3
    random_seed: int = 7
    default_arbitrary_singletons_only: bool = True
    log_level: str = "INFO"

    @classmethod
    def from_file(cls, path: Union[str, Path]) -> "LabelingConfig":
        """Load labeling configuration from a JSON file."""

        config_path = Path(path).expanduser().resolve()
        payload = json.loads(config_path.read_text(encoding="utf-8"))
        return cls.from_dict(payload, base_dir=config_path.parent)

    @classmethod
    def from_dict(
        cls,
        payload: dict[str, Any],
        *,
        base_dir: Optional[Path] = None,
    ) -> "LabelingConfig":
        """Create a labeling config from a dictionary payload."""

        base_dir = base_dir or Path.cwd()
        normalized: dict[str, Any] = dict(payload)
        normalized["artifacts_dir"] = _resolve_path(normalized["artifacts_dir"], base_dir)
        normalized["output_dir"] = _resolve_path(normalized["output_dir"], base_dir)

        config = cls(**normalized)
        config.validate()
        return config

    def apply_overrides(self, **overrides: Any) -> "LabelingConfig":
        """Return a new labeling config with non-null override values applied."""

        updated = asdict(self)
        for key, value in overrides.items():
            if value is None:
                continue

            if key in {"artifacts_dir", "output_dir"}:
                updated[key] = _resolve_path(value, Path.cwd())
            else:
                updated[key] = value

        config = LabelingConfig(**updated)
        config.validate()
        return config

    def validate(self) -> None:
        """Validate labeling configuration values."""

        if self.pair_preview_max_width <= 0 or self.pair_preview_max_height <= 0:
            raise ValueError("Pair preview dimensions must be greater than 0.")

        if self.cluster_preview_height <= 0:
            raise ValueError("cluster_preview_height must be greater than 0.")

        if self.cluster_grid_columns <= 0:
            raise ValueError("cluster_grid_columns must be greater than 0.")

        if self.random_seed < 0:
            raise ValueError("random_seed must be 0 or greater.")

    def to_serializable_dict(self) -> dict[str, Any]:
        """Convert the labeling config to a JSON-safe dictionary."""

        payload = asdict(self)
        payload["artifacts_dir"] = str(self.artifacts_dir)
        payload["output_dir"] = str(self.output_dir)
        return payload


@dataclass
class ReferenceBankBuildConfig:
    """Serializable runtime configuration for Week 6 reference-bank extraction."""

    reference_dir: Path
    output_dir: Path
    model_name: str = DEFAULT_MODEL_NAME
    fallback_model_name: Optional[str] = DEFAULT_FALLBACK_MODEL_NAME
    allow_model_fallback: bool = True
    batch_size: int = 8
    num_workers: int = 0
    scan_workers: int = field(default_factory=lambda: max(1, min(8, os.cpu_count() or 4)))
    device: str = "auto"
    image_size: Optional[int] = None
    supported_extensions: tuple[str, ...] = DEFAULT_EXTENSIONS
    reference_buckets: tuple[str, ...] = DEFAULT_REFERENCE_BUCKETS
    metadata_filename: str = "reference_images.csv"
    bank_filename: str = "reference_bank.npz"
    summary_filename: str = "reference_bank_summary.json"
    resolved_config_filename: str = "resolved_config.json"
    log_level: str = "INFO"

    @classmethod
    def from_file(cls, path: Union[str, Path]) -> "ReferenceBankBuildConfig":
        """Load reference-bank configuration from a JSON file."""

        config_path = Path(path).expanduser().resolve()
        payload = json.loads(config_path.read_text(encoding="utf-8"))
        return cls.from_dict(payload, base_dir=config_path.parent)

    @classmethod
    def from_dict(
        cls,
        payload: dict[str, Any],
        *,
        base_dir: Optional[Path] = None,
    ) -> "ReferenceBankBuildConfig":
        """Create a reference-bank config from a dictionary payload."""

        base_dir = base_dir or Path.cwd()
        normalized: dict[str, Any] = dict(payload)
        normalized["reference_dir"] = _resolve_path(normalized["reference_dir"], base_dir)
        normalized["output_dir"] = _resolve_path(normalized["output_dir"], base_dir)
        normalized["model_name"] = _resolve_optional_model_reference(
            normalized["model_name"], base_dir
        )
        normalized["fallback_model_name"] = _resolve_optional_model_reference(
            normalized.get("fallback_model_name"), base_dir
        )

        if "supported_extensions" in normalized:
            normalized["supported_extensions"] = tuple(
                ext.lower() for ext in normalized["supported_extensions"]
            )
        if "reference_buckets" in normalized:
            normalized["reference_buckets"] = tuple(
                str(bucket).strip().lower() for bucket in normalized["reference_buckets"]
            )

        config = cls(**normalized)
        config.validate()
        return config

    def apply_overrides(self, **overrides: Any) -> "ReferenceBankBuildConfig":
        """Return a new reference-bank config with non-null override values applied."""

        updated = asdict(self)
        for key, value in overrides.items():
            if value is None:
                continue

            if key in {"reference_dir", "output_dir"}:
                updated[key] = _resolve_path(value, Path.cwd())
            elif key in {"model_name", "fallback_model_name"}:
                updated[key] = _resolve_optional_model_reference(value, Path.cwd())
            elif key == "supported_extensions":
                updated[key] = tuple(ext.lower() for ext in value)
            elif key == "reference_buckets":
                updated[key] = tuple(str(bucket).strip().lower() for bucket in value)
            else:
                updated[key] = value

        config = ReferenceBankBuildConfig(**updated)
        config.validate()
        return config

    def validate(self) -> None:
        """Validate reference-bank configuration values."""

        if self.batch_size <= 0:
            raise ValueError("batch_size must be greater than 0.")

        if self.num_workers < 0:
            raise ValueError("num_workers must be 0 or greater.")

        if self.scan_workers <= 0:
            raise ValueError("scan_workers must be greater than 0.")

        if self.image_size is not None and self.image_size <= 0:
            raise ValueError("image_size must be null or greater than 0.")

        if not self.model_name:
            raise ValueError("model_name must be provided.")

        if self.device not in {"auto", "cpu"} and not self.device.startswith("cuda"):
            raise ValueError("device must be 'auto', 'cpu', or a cuda device string.")

        if not self.supported_extensions:
            raise ValueError("supported_extensions must not be empty.")

        if not self.reference_buckets:
            raise ValueError("reference_buckets must not be empty.")

        if len(set(self.reference_buckets)) != len(self.reference_buckets):
            raise ValueError("reference_buckets must contain unique bucket names.")

    def to_serializable_dict(self) -> dict[str, Any]:
        """Convert the reference-bank config to a JSON-safe dictionary."""

        payload = asdict(self)
        payload["reference_dir"] = str(self.reference_dir)
        payload["output_dir"] = str(self.output_dir)
        payload["supported_extensions"] = list(self.supported_extensions)
        payload["reference_buckets"] = list(self.reference_buckets)
        return payload


@dataclass
class RankingTrainConfig:
    """Serializable runtime configuration for Week 4 pairwise ranker training."""

    artifacts_dir: Path
    labels_dir: Path
    output_dir: Path
    metadata_filename: str = "images.csv"
    embeddings_filename: str = "embeddings.npy"
    image_ids_filename: str = "image_ids.json"
    clusters_filename: str = "clusters.csv"
    pairwise_labels_filename: str = "pairwise_labels.jsonl"
    cluster_labels_filename: str = "cluster_labels.jsonl"
    include_cluster_label_pairs: bool = True
    skip_ties: bool = True
    normalize_embeddings: bool = True
    reference_bank_path: Optional[Path] = None
    reference_top_k: int = 3
    validation_fraction: float = 0.2
    random_seed: int = 7
    batch_size: int = 64
    num_epochs: int = 30
    learning_rate: float = 0.001
    weight_decay: float = 0.0001
    hidden_dim: int = 0
    dropout: float = 0.0
    loss_name: str = "logistic"
    device: str = "auto"
    best_checkpoint_filename: str = "best_ranker.pt"
    last_checkpoint_filename: str = "last_ranker.pt"
    metrics_filename: str = "training_metrics.json"
    history_filename: str = "training_history.csv"
    resolved_config_filename: str = "resolved_config.json"
    log_level: str = "INFO"

    @classmethod
    def from_file(cls, path: Union[str, Path]) -> "RankingTrainConfig":
        """Load training configuration from a JSON file."""

        config_path = Path(path).expanduser().resolve()
        payload = json.loads(config_path.read_text(encoding="utf-8"))
        return cls.from_dict(payload, base_dir=config_path.parent)

    @classmethod
    def from_dict(
        cls,
        payload: dict[str, Any],
        *,
        base_dir: Optional[Path] = None,
    ) -> "RankingTrainConfig":
        """Create a training config from a dictionary payload."""

        base_dir = base_dir or Path.cwd()
        normalized: dict[str, Any] = dict(payload)
        normalized["artifacts_dir"] = _resolve_path(normalized["artifacts_dir"], base_dir)
        labels_dir_value = normalized.get("labels_dir", normalized["artifacts_dir"] / "labels")
        normalized["labels_dir"] = _resolve_path(labels_dir_value, base_dir)
        normalized["output_dir"] = _resolve_path(normalized["output_dir"], base_dir)
        reference_bank_value = normalized.get("reference_bank_path")
        if reference_bank_value is not None:
            normalized["reference_bank_path"] = _resolve_path(reference_bank_value, base_dir)

        config = cls(**normalized)
        config.validate()
        return config

    def apply_overrides(self, **overrides: Any) -> "RankingTrainConfig":
        """Return a new training config with non-null override values applied."""

        updated = asdict(self)
        for key, value in overrides.items():
            if value is None:
                continue

            if key in {"artifacts_dir", "labels_dir", "output_dir", "reference_bank_path"}:
                updated[key] = _resolve_path(value, Path.cwd())
            else:
                updated[key] = value

        config = RankingTrainConfig(**updated)
        config.validate()
        return config

    def validate(self) -> None:
        """Validate training configuration values."""

        if self.validation_fraction < 0.0 or self.validation_fraction >= 1.0:
            raise ValueError("validation_fraction must be in the range [0.0, 1.0).")

        if self.random_seed < 0:
            raise ValueError("random_seed must be 0 or greater.")

        if self.batch_size <= 0:
            raise ValueError("batch_size must be greater than 0.")

        if self.num_epochs <= 0:
            raise ValueError("num_epochs must be greater than 0.")

        if self.learning_rate <= 0:
            raise ValueError("learning_rate must be greater than 0.")

        if self.weight_decay < 0:
            raise ValueError("weight_decay must be greater than or equal to 0.")

        if self.hidden_dim < 0:
            raise ValueError("hidden_dim must be 0 or greater.")

        if self.dropout < 0.0 or self.dropout >= 1.0:
            raise ValueError("dropout must be in the range [0.0, 1.0).")

        if self.reference_top_k <= 0:
            raise ValueError("reference_top_k must be greater than 0.")

        if self.loss_name not in {"logistic"}:
            raise ValueError("loss_name must currently be 'logistic'.")

        if self.device not in {"auto", "cpu"} and not self.device.startswith("cuda"):
            raise ValueError("device must be 'auto', 'cpu', or a cuda device string.")

    def to_serializable_dict(self) -> dict[str, Any]:
        """Convert the training config to a JSON-safe dictionary."""

        payload = asdict(self)
        payload["artifacts_dir"] = str(self.artifacts_dir)
        payload["labels_dir"] = str(self.labels_dir)
        payload["output_dir"] = str(self.output_dir)
        payload["reference_bank_path"] = (
            str(self.reference_bank_path) if self.reference_bank_path is not None else None
        )
        return payload


@dataclass
class RankingScoreConfig:
    """Serializable runtime configuration for Week 4 ranker scoring."""

    artifacts_dir: Path
    checkpoint_path: Path
    output_dir: Path
    reference_bank_path: Optional[Path] = None
    metadata_filename: str = "images.csv"
    embeddings_filename: str = "embeddings.npy"
    image_ids_filename: str = "image_ids.json"
    clusters_filename: str = "clusters.csv"
    scored_clusters_filename: str = "ranked_clusters.csv"
    summary_filename: str = "ranked_clusters_summary.json"
    device: str = "auto"
    log_level: str = "INFO"

    @classmethod
    def from_file(cls, path: Union[str, Path]) -> "RankingScoreConfig":
        """Load scoring configuration from a JSON file."""

        config_path = Path(path).expanduser().resolve()
        payload = json.loads(config_path.read_text(encoding="utf-8"))
        return cls.from_dict(payload, base_dir=config_path.parent)

    @classmethod
    def from_dict(
        cls,
        payload: dict[str, Any],
        *,
        base_dir: Optional[Path] = None,
    ) -> "RankingScoreConfig":
        """Create a scoring config from a dictionary payload."""

        base_dir = base_dir or Path.cwd()
        normalized: dict[str, Any] = dict(payload)
        normalized["artifacts_dir"] = _resolve_path(normalized["artifacts_dir"], base_dir)
        normalized["checkpoint_path"] = _resolve_path(normalized["checkpoint_path"], base_dir)
        normalized["output_dir"] = _resolve_path(normalized["output_dir"], base_dir)
        reference_bank_value = normalized.get("reference_bank_path")
        if reference_bank_value is not None:
            normalized["reference_bank_path"] = _resolve_path(reference_bank_value, base_dir)

        config = cls(**normalized)
        config.validate()
        return config

    def apply_overrides(self, **overrides: Any) -> "RankingScoreConfig":
        """Return a new scoring config with non-null override values applied."""

        updated = asdict(self)
        for key, value in overrides.items():
            if value is None:
                continue

            if key in {"artifacts_dir", "checkpoint_path", "output_dir", "reference_bank_path"}:
                updated[key] = _resolve_path(value, Path.cwd())
            else:
                updated[key] = value

        config = RankingScoreConfig(**updated)
        config.validate()
        return config

    def validate(self) -> None:
        """Validate scoring configuration values."""

        if self.device not in {"auto", "cpu"} and not self.device.startswith("cuda"):
            raise ValueError("device must be 'auto', 'cpu', or a cuda device string.")

    def to_serializable_dict(self) -> dict[str, Any]:
        """Convert the scoring config to a JSON-safe dictionary."""

        payload = asdict(self)
        payload["artifacts_dir"] = str(self.artifacts_dir)
        payload["checkpoint_path"] = str(self.checkpoint_path)
        payload["output_dir"] = str(self.output_dir)
        payload["reference_bank_path"] = (
            str(self.reference_bank_path) if self.reference_bank_path is not None else None
        )
        return payload


@dataclass
class RankingEvaluationConfig:
    """Serializable runtime configuration for Week 5 ranker evaluation."""

    artifacts_dir: Path
    labels_dir: Path
    checkpoint_path: Path
    output_dir: Path
    reference_bank_path: Optional[Path] = None
    metadata_filename: str = "images.csv"
    embeddings_filename: str = "embeddings.npy"
    image_ids_filename: str = "image_ids.json"
    clusters_filename: str = "clusters.csv"
    pairwise_labels_filename: str = "pairwise_labels.jsonl"
    cluster_labels_filename: str = "cluster_labels.jsonl"
    include_cluster_label_pairs: bool = True
    top_k_values: tuple[int, ...] = (1, 3)
    score_batch_size: int = 2048
    metrics_filename: str = "ranker_evaluation.json"
    pairwise_breakdown_filename: str = "pairwise_evaluation.csv"
    cluster_breakdown_filename: str = "cluster_evaluation.csv"
    device: str = "auto"
    log_level: str = "INFO"

    @classmethod
    def from_file(cls, path: Union[str, Path]) -> "RankingEvaluationConfig":
        """Load evaluation configuration from a JSON file."""

        config_path = Path(path).expanduser().resolve()
        payload = json.loads(config_path.read_text(encoding="utf-8"))
        return cls.from_dict(payload, base_dir=config_path.parent)

    @classmethod
    def from_dict(
        cls,
        payload: dict[str, Any],
        *,
        base_dir: Optional[Path] = None,
    ) -> "RankingEvaluationConfig":
        """Create an evaluation config from a dictionary payload."""

        base_dir = base_dir or Path.cwd()
        normalized: dict[str, Any] = dict(payload)
        normalized["artifacts_dir"] = _resolve_path(normalized["artifacts_dir"], base_dir)
        labels_dir_value = normalized.get("labels_dir", normalized["artifacts_dir"] / "labels")
        normalized["labels_dir"] = _resolve_path(labels_dir_value, base_dir)
        normalized["checkpoint_path"] = _resolve_path(normalized["checkpoint_path"], base_dir)
        normalized["output_dir"] = _resolve_path(normalized["output_dir"], base_dir)
        reference_bank_value = normalized.get("reference_bank_path")
        if reference_bank_value is not None:
            normalized["reference_bank_path"] = _resolve_path(reference_bank_value, base_dir)
        normalized["top_k_values"] = _normalize_top_k_values(normalized.get("top_k_values", (1, 3)))

        config = cls(**normalized)
        config.validate()
        return config

    def apply_overrides(self, **overrides: Any) -> "RankingEvaluationConfig":
        """Return a new evaluation config with non-null override values applied."""

        updated = asdict(self)
        for key, value in overrides.items():
            if value is None:
                continue

            if key in {
                "artifacts_dir",
                "labels_dir",
                "checkpoint_path",
                "output_dir",
                "reference_bank_path",
            }:
                updated[key] = _resolve_path(value, Path.cwd())
            elif key == "top_k_values":
                updated[key] = _normalize_top_k_values(value)
            else:
                updated[key] = value

        config = RankingEvaluationConfig(**updated)
        config.validate()
        return config

    def validate(self) -> None:
        """Validate evaluation configuration values."""

        if not self.top_k_values:
            raise ValueError("top_k_values must contain at least one positive integer.")

        if any(value <= 0 for value in self.top_k_values):
            raise ValueError("top_k_values must contain only positive integers.")

        if self.score_batch_size <= 0:
            raise ValueError("score_batch_size must be greater than 0.")

        if self.device not in {"auto", "cpu"} and not self.device.startswith("cuda"):
            raise ValueError("device must be 'auto', 'cpu', or a cuda device string.")

    def to_serializable_dict(self) -> dict[str, Any]:
        """Convert the evaluation config to a JSON-safe dictionary."""

        payload = asdict(self)
        payload["artifacts_dir"] = str(self.artifacts_dir)
        payload["labels_dir"] = str(self.labels_dir)
        payload["checkpoint_path"] = str(self.checkpoint_path)
        payload["output_dir"] = str(self.output_dir)
        payload["reference_bank_path"] = (
            str(self.reference_bank_path) if self.reference_bank_path is not None else None
        )
        payload["top_k_values"] = list(self.top_k_values)
        return payload


@dataclass
class RankingReportConfig:
    """Serializable runtime configuration for Week 5 ranked export and HTML reporting."""

    artifacts_dir: Path
    checkpoint_path: Path
    output_dir: Path
    labels_dir: Optional[Path] = None
    reference_bank_path: Optional[Path] = None
    metadata_filename: str = "images.csv"
    embeddings_filename: str = "embeddings.npy"
    image_ids_filename: str = "image_ids.json"
    clusters_filename: str = "clusters.csv"
    cluster_labels_filename: str = "cluster_labels.jsonl"
    ranked_export_filename: str = "ranked_clusters_export.csv"
    summary_filename: str = "ranked_export_summary.json"
    html_report_filename: str = "ranked_clusters_report.html"
    html_include_singletons: bool = False
    html_max_clusters: Optional[int] = None
    score_batch_size: int = 2048
    device: str = "auto"
    log_level: str = "INFO"

    @classmethod
    def from_file(cls, path: Union[str, Path]) -> "RankingReportConfig":
        """Load export/report configuration from a JSON file."""

        config_path = Path(path).expanduser().resolve()
        payload = json.loads(config_path.read_text(encoding="utf-8"))
        return cls.from_dict(payload, base_dir=config_path.parent)

    @classmethod
    def from_dict(
        cls,
        payload: dict[str, Any],
        *,
        base_dir: Optional[Path] = None,
    ) -> "RankingReportConfig":
        """Create an export/report config from a dictionary payload."""

        base_dir = base_dir or Path.cwd()
        normalized: dict[str, Any] = dict(payload)
        normalized["artifacts_dir"] = _resolve_path(normalized["artifacts_dir"], base_dir)
        normalized["checkpoint_path"] = _resolve_path(normalized["checkpoint_path"], base_dir)
        normalized["output_dir"] = _resolve_path(normalized["output_dir"], base_dir)

        labels_dir_value = normalized.get("labels_dir")
        if labels_dir_value is None:
            default_labels_dir = normalized["artifacts_dir"] / "labels"
            normalized["labels_dir"] = default_labels_dir if default_labels_dir.exists() else None
        else:
            normalized["labels_dir"] = _resolve_path(labels_dir_value, base_dir)

        reference_bank_value = normalized.get("reference_bank_path")
        if reference_bank_value is not None:
            normalized["reference_bank_path"] = _resolve_path(reference_bank_value, base_dir)

        config = cls(**normalized)
        config.validate()
        return config

    def apply_overrides(self, **overrides: Any) -> "RankingReportConfig":
        """Return a new export/report config with non-null override values applied."""

        updated = asdict(self)
        for key, value in overrides.items():
            if value is None:
                continue

            if key in {
                "artifacts_dir",
                "checkpoint_path",
                "output_dir",
                "labels_dir",
                "reference_bank_path",
            }:
                updated[key] = _resolve_path(value, Path.cwd())
            else:
                updated[key] = value

        config = RankingReportConfig(**updated)
        config.validate()
        return config

    def validate(self) -> None:
        """Validate export/report configuration values."""

        if self.html_max_clusters is not None and self.html_max_clusters <= 0:
            raise ValueError("html_max_clusters must be null or greater than 0.")

        if self.score_batch_size <= 0:
            raise ValueError("score_batch_size must be greater than 0.")

        if self.device not in {"auto", "cpu"} and not self.device.startswith("cuda"):
            raise ValueError("device must be 'auto', 'cpu', or a cuda device string.")

    def to_serializable_dict(self) -> dict[str, Any]:
        """Convert the export/report config to a JSON-safe dictionary."""

        payload = asdict(self)
        payload["artifacts_dir"] = str(self.artifacts_dir)
        payload["checkpoint_path"] = str(self.checkpoint_path)
        payload["output_dir"] = str(self.output_dir)
        payload["labels_dir"] = str(self.labels_dir) if self.labels_dir is not None else None
        payload["reference_bank_path"] = (
            str(self.reference_bank_path) if self.reference_bank_path is not None else None
        )
        return payload


def _resolve_path(value: Union[str, Path], base_dir: Path) -> Path:
    """Resolve a config path relative to a base directory."""

    path = Path(value).expanduser()
    if not path.is_absolute():
        path = (base_dir / path).resolve()
    return path


def _resolve_optional_model_reference(
    value: Optional[Union[str, Path]],
    base_dir: Path,
) -> Optional[str]:
    """Resolve model references that are local paths while preserving timm IDs."""

    if value is None:
        return None

    text = str(value)
    if text.startswith(("hf-hub:", "local-dir:")):
        return text

    if _looks_like_path_reference(text):
        return str(_resolve_path(text, base_dir))

    return text


def _looks_like_path_reference(value: str) -> bool:
    """Heuristically detect whether a config string is a filesystem path."""

    return (
        value.startswith(".")
        or "/" in value
        or "\\" in value
        or Path(value).is_absolute()
    )


def _normalize_top_k_values(values: Any) -> tuple[int, ...]:
    """Normalize top-k config values into a stable tuple of unique positive integers."""

    if isinstance(values, int):
        normalized = [int(values)]
    else:
        normalized = [int(value) for value in values]

    ordered: list[int] = []
    seen: set[int] = set()
    for value in normalized:
        if value <= 0 or value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return tuple(ordered)


def supported_override_fields() -> set[str]:
    """Return the config fields that can be overridden by the CLI."""

    return {field.name for field in fields(ExtractionConfig)}
