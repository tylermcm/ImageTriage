"""Training helpers for the transparent culling-signal combiner."""

from __future__ import annotations

import csv
import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

from app.engine.signals.combiner import FEATURE_NAMES, choose_profile
from app.engine.signals.models import ImageSignalRecord, record_from_dict
from app.engine.signals.preference_features import build_preference_feature_rows
from app.storage.ranking_artifacts import PairwisePreferenceRecord, RankingArtifacts, load_preference_labels, load_ranking_artifacts


SIGNAL_COMBINER_WEIGHTS_FILENAME = "personal_combiner_weights.json"
SIGNAL_COMBINER_FEATURES_FILENAME = "personal_combiner_training_rows.csv"
SPARSE_SPECIALIST_FEATURES = {
    "subject_confidence",
    "subject_size",
    "subject_centering",
    "face_quality",
    "eye_open",
}


@dataclass(frozen=True)
class SignalCombinerSourceConfig:
    """One source of signal records and preference labels for combiner tuning."""

    artifacts_dir: Path
    labels_dir: Path
    signals_path: Path
    source_name: str = ""


@dataclass(frozen=True)
class SignalCombinerTrainingConfig:
    """Config for lightweight transparent combiner tuning."""

    artifacts_dir: Path
    labels_dir: Path
    signals_path: Path
    output_dir: Path
    sources: tuple[SignalCombinerSourceConfig, ...] = ()
    profile_name: str = "General Use"
    metadata_filename: str = "images.csv"
    embeddings_filename: str = "embeddings.npy"
    image_ids_filename: str = "image_ids.json"
    clusters_filename: str = "clusters.csv"
    pairwise_labels_filename: str = "pairwise_labels.jsonl"
    cluster_labels_filename: str = "cluster_labels.jsonl"
    include_cluster_label_pairs: bool = True
    epochs: int = 400
    learning_rate: float = 0.08
    validation_fraction: float = 0.20
    anchor_strength: float = 0.015
    l2_strength: float = 0.002
    max_abs_weight: float = 1.0
    filter_near_identical_pairs: bool = True
    near_identical_similarity_threshold: float = 0.965
    min_feature_delta_coverage: float = 0.03
    min_feature_standalone_accuracy: float = 0.52
    seed: int = 17


def train_signal_combiner(config: SignalCombinerTrainingConfig) -> dict[str, Path]:
    """Train transparent feature weights from saved pairwise/cluster labels."""

    artifacts_dir = Path(config.artifacts_dir).expanduser().resolve()
    labels_dir = Path(config.labels_dir).expanduser().resolve()
    signals_path = Path(config.signals_path).expanduser().resolve()
    output_dir = Path(config.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    sources = config.sources or (
        SignalCombinerSourceConfig(
            artifacts_dir=artifacts_dir,
            labels_dir=labels_dir,
            signals_path=signals_path,
            source_name=str(artifacts_dir.parent),
        ),
    )
    rows: list[dict[str, Any]] = []
    source_summaries: list[dict[str, Any]] = []
    for source_index, source in enumerate(sources, start=1):
        source_name = str(source.source_name or source.artifacts_dir.parent)
        ranking_artifacts = load_ranking_artifacts(
            Path(source.artifacts_dir).expanduser().resolve(),
            metadata_filename=config.metadata_filename,
            embeddings_filename=config.embeddings_filename,
            image_ids_filename=config.image_ids_filename,
            clusters_filename=config.clusters_filename,
        )
        signal_records = load_signal_records(Path(source.signals_path).expanduser().resolve())
        loaded_labels = load_preference_labels(
            labels_dir=Path(source.labels_dir).expanduser().resolve(),
            ranking_artifacts=ranking_artifacts,
            pairwise_labels_filename=config.pairwise_labels_filename,
            cluster_labels_filename=config.cluster_labels_filename,
            include_cluster_label_pairs=config.include_cluster_label_pairs,
            skip_ties=True,
        )
        filtered_preferences, filter_summary = _filter_near_identical_preferences(
            loaded_labels.preferences,
            ranking_artifacts=ranking_artifacts,
            enabled=config.filter_near_identical_pairs,
            threshold=config.near_identical_similarity_threshold,
        )
        source_rows = build_preference_feature_rows(signal_records, filtered_preferences)
        for row in source_rows:
            mutable_row = dict(row)
            mutable_row["training_source"] = source_name
            mutable_row["training_source_index"] = source_index
            rows.append(mutable_row)
        source_summaries.append(
            {
                "source_name": source_name,
                "artifacts_dir": str(source.artifacts_dir),
                "labels_dir": str(source.labels_dir),
                "signals_path": str(source.signals_path),
                "input_preference_pairs": len(loaded_labels.preferences),
                "near_identical_pairs_filtered": filter_summary["filtered_pairs"],
                "near_identical_threshold": filter_summary["threshold"],
                "preference_rows": len(source_rows),
            }
        )
    if not rows:
        raise ValueError("No usable signal feature rows could be built from the saved labels.")

    feature_rows_path = output_dir / SIGNAL_COMBINER_FEATURES_FILENAME
    _save_feature_rows(feature_rows_path, rows)

    profile = choose_profile(config.profile_name)
    base_weights = {feature: float(profile.weights.get(feature, 0.0)) for feature in FEATURE_NAMES}
    feature_diagnostics = _feature_diagnostics(rows)
    enabled_features = _enabled_features_from_diagnostics(
        feature_diagnostics,
        min_feature_delta_coverage=max(0.0, min(1.0, float(config.min_feature_delta_coverage))),
        min_feature_standalone_accuracy=max(0.0, min(1.0, float(config.min_feature_standalone_accuracy))),
    )
    base_weights = {
        feature: (value if feature in enabled_features else 0.0)
        for feature, value in base_weights.items()
    }
    result = _fit_pairwise_logistic(
        rows,
        base_weights=base_weights,
        enabled_features=enabled_features,
        epochs=max(1, int(config.epochs)),
        learning_rate=max(1e-6, float(config.learning_rate)),
        validation_fraction=max(0.0, min(0.45, float(config.validation_fraction))),
        anchor_strength=max(0.0, float(config.anchor_strength)),
        l2_strength=max(0.0, float(config.l2_strength)),
        max_abs_weight=max(0.05, float(config.max_abs_weight)),
        seed=int(config.seed),
    )
    final_weights = result["final_weights"]
    learned_weights = {
        feature: float(final_weights[feature] - base_weights.get(feature, 0.0))
        for feature in FEATURE_NAMES
        if abs(float(final_weights[feature] - base_weights.get(feature, 0.0))) > 1e-9
    }
    payload = {
        "schema_version": "transparent_combiner_weights.v1",
        "profile_name": profile.name,
        "feature_names": list(FEATURE_NAMES),
        "base_weights": base_weights,
        "final_weights": final_weights,
        "learned_weights": learned_weights,
        "training": {
            "row_count": len(rows),
            "source_count": len(sources),
            "sources": source_summaries,
            "source_row_distribution": dict(Counter(str(row.get("training_source") or "") for row in rows)),
            "pair_source_distribution": dict(Counter(str(row.get("label_origin") or "") for row in rows)),
            "source_mode_distribution": dict(Counter(str(row.get("source_mode") or "") for row in rows)),
            "epochs": int(config.epochs),
            "learning_rate": float(config.learning_rate),
            "validation_fraction": float(config.validation_fraction),
            "anchor_strength": float(config.anchor_strength),
            "l2_strength": float(config.l2_strength),
            "max_abs_weight": float(config.max_abs_weight),
            "filter_near_identical_pairs": bool(config.filter_near_identical_pairs),
            "near_identical_similarity_threshold": float(config.near_identical_similarity_threshold),
            "min_feature_delta_coverage": float(config.min_feature_delta_coverage),
            "min_feature_standalone_accuracy": float(config.min_feature_standalone_accuracy),
            "filtered_near_identical_pairs": int(sum(int(source["near_identical_pairs_filtered"]) for source in source_summaries)),
            "enabled_features": sorted(enabled_features),
            "disabled_features": sorted(set(FEATURE_NAMES) - set(enabled_features)),
            "feature_diagnostics": feature_diagnostics,
            "metrics": result["metrics"],
        },
        "notes": [
            "Weights are transparent linear feature weights trained from pairwise and cluster-derived preferences.",
            "learned_weights are additive deltas applied on top of the named base profile.",
            "File/sequence order is evaluated as a baseline only and is not a trainable combiner feature.",
            "Near-identical preference pairs are filtered during tuning by default so bracket noise does not dominate learned weights.",
            "Sparse features are disabled during tuning when too few preference rows contain a non-zero delta.",
            "Weak standalone features are disabled during tuning until they beat the configured preference-accuracy floor.",
        ],
    }
    weights_path = output_dir / SIGNAL_COMBINER_WEIGHTS_FILENAME
    weights_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return {"weights": weights_path, "feature_rows": feature_rows_path}


def load_signal_records(path: Path) -> dict[str, ImageSignalRecord]:
    """Load saved signal JSON records."""

    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        payload = payload.get("records", [])
    if not isinstance(payload, list):
        raise ValueError(f"Expected culling signals JSON list at {path}")
    records: dict[str, ImageSignalRecord] = {}
    for item in payload:
        if not isinstance(item, dict):
            continue
        record = record_from_dict(item)
        if record.image_id:
            records[record.image_id] = record
    if not records:
        raise ValueError(f"No usable culling signal records were found in {path}")
    return records


def load_learned_weights(path: Path) -> dict[str, float]:
    """Load additive learned combiner weights from JSON."""

    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected learned weight JSON object at {path}")
    weights = payload.get("learned_weights")
    if not isinstance(weights, dict):
        raise ValueError(f"Missing learned_weights object in {path}")
    loaded: dict[str, float] = {}
    for feature in FEATURE_NAMES:
        value = weights.get(feature)
        if value is None:
            continue
        try:
            loaded[feature] = float(value)
        except (TypeError, ValueError):
            continue
    return loaded


def _filter_near_identical_preferences(
    preferences: Sequence[PairwisePreferenceRecord],
    *,
    ranking_artifacts: RankingArtifacts,
    enabled: bool,
    threshold: float,
) -> tuple[list[PairwisePreferenceRecord], dict[str, Any]]:
    threshold = max(-1.0, min(1.0, float(threshold)))
    if not enabled or not preferences:
        return list(preferences), {
            "enabled": bool(enabled),
            "threshold": threshold,
            "input_pairs": len(preferences),
            "filtered_pairs": 0,
            "kept_pairs": len(preferences),
        }

    embeddings = _l2_normalize(ranking_artifacts.embeddings.astype(np.float32, copy=False))
    kept: list[PairwisePreferenceRecord] = []
    similarities: list[float] = []
    filtered_similarities: list[float] = []
    by_origin: dict[str, int] = {}
    for preference in preferences:
        similarity = float(embeddings[preference.preferred_index] @ embeddings[preference.other_index])
        similarities.append(similarity)
        if similarity >= threshold:
            filtered_similarities.append(similarity)
            by_origin[preference.label_origin] = by_origin.get(preference.label_origin, 0) + 1
            continue
        kept.append(preference)

    return kept, {
        "enabled": True,
        "threshold": threshold,
        "input_pairs": len(preferences),
        "filtered_pairs": len(filtered_similarities),
        "kept_pairs": len(kept),
        "filtered_fraction": len(filtered_similarities) / len(preferences),
        "mean_similarity": float(np.mean(similarities)) if similarities else None,
        "median_similarity": float(np.median(similarities)) if similarities else None,
        "min_filtered_similarity": float(min(filtered_similarities)) if filtered_similarities else None,
        "max_filtered_similarity": float(max(filtered_similarities)) if filtered_similarities else None,
        "filtered_by_label_origin": by_origin,
    }


def _feature_diagnostics(rows: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, float | int | None]]:
    diagnostics: dict[str, dict[str, float | int | None]] = {}
    row_count = len(rows)
    for feature in FEATURE_NAMES:
        values = np.asarray([float(row.get(feature, 0.0) or 0.0) for row in rows], dtype=np.float32)
        nonzero = np.abs(values) > 1e-8
        nonzero_count = int(nonzero.sum())
        if values.size:
            positive_accuracy = float((values > 0.0).mean())
            inverse_accuracy = float((values < 0.0).mean())
            best_standalone = max(positive_accuracy, inverse_accuracy)
            preferred_direction = "positive" if positive_accuracy >= inverse_accuracy else "negative"
            mean_delta = float(values.mean())
            mean_abs_delta = float(np.abs(values).mean())
        else:
            positive_accuracy = None
            inverse_accuracy = None
            best_standalone = None
            preferred_direction = ""
            mean_delta = None
            mean_abs_delta = None
        diagnostics[feature] = {
            "rows": row_count,
            "nonzero_delta_rows": nonzero_count,
            "delta_coverage": (nonzero_count / row_count) if row_count else 0.0,
            "positive_accuracy": positive_accuracy,
            "inverse_accuracy": inverse_accuracy,
            "best_standalone_accuracy": best_standalone,
            "preferred_direction": preferred_direction,
            "mean_delta": mean_delta,
            "mean_abs_delta": mean_abs_delta,
        }
    return diagnostics


def _enabled_features_from_diagnostics(
    diagnostics: Mapping[str, Mapping[str, float | int | None]],
    *,
    min_feature_delta_coverage: float,
    min_feature_standalone_accuracy: float,
) -> set[str]:
    enabled: set[str] = set()
    for feature, payload in diagnostics.items():
        try:
            coverage = float(payload.get("delta_coverage") or 0.0)
        except (TypeError, ValueError):
            coverage = 0.0
        try:
            standalone_accuracy = float(payload.get("best_standalone_accuracy") or 0.0)
        except (TypeError, ValueError):
            standalone_accuracy = 0.0
        feature_threshold = max(min_feature_delta_coverage, 0.08) if feature in SPARSE_SPECIALIST_FEATURES else min_feature_delta_coverage
        if coverage >= feature_threshold and standalone_accuracy >= min_feature_standalone_accuracy:
            enabled.add(feature)
    return enabled


def _l2_normalize(matrix: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms = np.where(norms <= 1e-12, 1.0, norms)
    return matrix / norms


def _fit_pairwise_logistic(
    rows: Sequence[Mapping[str, Any]],
    *,
    base_weights: Mapping[str, float],
    enabled_features: set[str],
    epochs: int,
    learning_rate: float,
    validation_fraction: float,
    anchor_strength: float,
    l2_strength: float,
    max_abs_weight: float,
    seed: int,
) -> dict[str, Any]:
    feature_names = tuple(FEATURE_NAMES)
    x_positive = np.asarray(
        [[float(row.get(feature, 0.0) or 0.0) for feature in feature_names] for row in rows],
        dtype=np.float32,
    )
    x = np.concatenate([x_positive, -x_positive], axis=0)
    y = np.concatenate(
        [
            np.ones((x_positive.shape[0],), dtype=np.float32),
            np.zeros((x_positive.shape[0],), dtype=np.float32),
        ],
        axis=0,
    )
    rng = np.random.default_rng(seed)
    order = np.arange(x.shape[0])
    rng.shuffle(order)
    x = x[order]
    y = y[order]

    validation_count = int(round(x.shape[0] * validation_fraction))
    if x.shape[0] < 20:
        validation_count = 0
    validation_count = min(max(validation_count, 0), max(0, x.shape[0] - 2))
    if validation_count > 0:
        x_val = x[:validation_count]
        y_val = y[:validation_count]
        x_train = x[validation_count:]
        y_train = y[validation_count:]
    else:
        x_train = x
        y_train = y
        x_val = np.empty((0, x.shape[1]), dtype=np.float32)
        y_val = np.empty((0,), dtype=np.float32)

    base_vector = np.asarray([float(base_weights.get(feature, 0.0)) for feature in feature_names], dtype=np.float32)
    enabled_mask = np.asarray([feature in enabled_features for feature in feature_names], dtype=bool)
    weights = base_vector.copy()
    weights[~enabled_mask] = 0.0
    history: list[dict[str, float | int | None]] = []
    for epoch in range(1, epochs + 1):
        logits = x_train @ weights
        probabilities = _sigmoid(logits)
        error = probabilities - y_train
        gradient = (x_train.T @ error) / max(1, x_train.shape[0])
        gradient += anchor_strength * (weights - base_vector)
        gradient += l2_strength * weights
        gradient[~enabled_mask] = 0.0
        weights -= learning_rate * gradient
        weights = np.clip(weights, -max_abs_weight, max_abs_weight)
        weights[~enabled_mask] = 0.0
        if epoch == 1 or epoch == epochs or epoch % 25 == 0:
            train_metrics = _metrics(x_train, y_train, weights)
            val_metrics = _metrics(x_val, y_val, weights) if x_val.shape[0] else {}
            history.append(
                {
                    "epoch": epoch,
                    "train_loss": train_metrics.get("loss"),
                    "train_accuracy": train_metrics.get("accuracy"),
                    "validation_loss": val_metrics.get("loss"),
                    "validation_accuracy": val_metrics.get("accuracy"),
                }
            )

    final_weights = {feature: float(weights[index]) for index, feature in enumerate(feature_names)}
    original_metrics = _preference_metrics(x_positive, weights)
    train_metrics = _metrics(x_train, y_train, weights)
    val_metrics = _metrics(x_val, y_val, weights) if x_val.shape[0] else {}
    return {
        "final_weights": final_weights,
        "metrics": {
            "preference_accuracy": original_metrics["accuracy"],
            "mean_margin": original_metrics["mean_margin"],
            "median_margin": original_metrics["median_margin"],
            "train_accuracy": train_metrics.get("accuracy"),
            "train_loss": train_metrics.get("loss"),
            "validation_accuracy": val_metrics.get("accuracy"),
            "validation_loss": val_metrics.get("loss"),
            "train_rows": int(x_train.shape[0]),
            "validation_rows": int(x_val.shape[0]),
            "history": history,
        },
    }


def _sigmoid(values: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(values, -40.0, 40.0)))


def _metrics(x: np.ndarray, y: np.ndarray, weights: np.ndarray) -> dict[str, float]:
    if x.shape[0] <= 0:
        return {}
    logits = x @ weights
    probabilities = _sigmoid(logits)
    loss = -np.mean(y * np.log(np.clip(probabilities, 1e-7, 1.0)) + (1.0 - y) * np.log(np.clip(1.0 - probabilities, 1e-7, 1.0)))
    predictions = probabilities >= 0.5
    return {"loss": float(loss), "accuracy": float(np.mean(predictions == y.astype(bool)))}


def _preference_metrics(x_positive: np.ndarray, weights: np.ndarray) -> dict[str, float]:
    margins = x_positive @ weights
    return {
        "accuracy": float(np.mean(margins > 0.0)) if margins.size else 0.0,
        "mean_margin": float(np.mean(margins)) if margins.size else 0.0,
        "median_margin": float(np.median(margins)) if margins.size else 0.0,
    }


def _save_feature_rows(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    fieldnames = [
        "preferred_image_id",
        "other_image_id",
        "cluster_id",
        "training_source",
        "training_source_index",
        "source_mode",
        "label_origin",
        "target",
        *FEATURE_NAMES,
    ]
    with Path(path).open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field) for field in fieldnames})
