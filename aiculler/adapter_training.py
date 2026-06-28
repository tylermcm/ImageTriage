from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import numpy as np

from aiculler.metrics import CullingMetricRecord, compute_culling_metrics
from aiculler.simple_ml import PrincipalProjector, Standardizer
from aiculler.storage import SQLiteFeatureStore
from aiculler.text_scoring import normalize_scores


BINARY_LABELS = {
    "keep": 1.0,
    "k": 1.0,
    "good": 1.0,
    "yes": 1.0,
    "1": 1.0,
    "reject": 0.0,
    "r": 0.0,
    "bad": 0.0,
    "no": 0.0,
    "0": 0.0,
}

BUCKET_LABELS = {
    "hero": 1.0,
    "portfolio": 1.0,
    "strong": 0.8,
    "keep": 0.75,
    "good": 0.75,
    "maybe": 0.5,
    "weak": 0.25,
    "reject": 0.0,
    "bad": 0.0,
}


@dataclass(frozen=True)
class RatingExample:
    image_id: int
    filename: str
    source_path: str
    label: str
    label_type: str
    numeric_score: float
    weight: float
    label_origin: str
    primary_category: str
    cluster_id: int | None
    folder_id: str
    reason_tags: tuple[str, ...] = ()


@dataclass(frozen=True)
class AdapterScoreRecord:
    image_id: int
    filename: str
    source_path: str
    primary_category: str
    cluster_id: int | None
    base_score: float
    global_score: float
    category_score: float | None
    cluster_score: float | None
    adapter_score: float
    confidence: float
    final_score: float


@dataclass(frozen=True)
class AdapterTrainingResult:
    model_version: str
    scores: list[AdapterScoreRecord]
    metrics: dict


def import_ratings_csv(
    store: SQLiteFeatureStore,
    ratings_csv_path: str | Path,
    *,
    source: str = "csv",
    skip_unmatched: bool = False,
) -> list[RatingExample]:
    unmatched_rows: list[int] = []
    records = load_rating_records(
        store,
        ratings_csv_path,
        source=source,
        skip_unmatched=skip_unmatched,
        unmatched_rows=unmatched_rows,
    )
    existing_count = len(store.list_ratings())
    store.delete_ratings_for_source_origins(source, {record.label_origin for record in records})
    after_delete_count = len(store.list_ratings())
    store.add_ratings(
        [
            {
                "image_id": record.image_id,
                "label": record.label,
                "label_type": record.label_type,
                "numeric_score": record.numeric_score,
                "primary_category": record.primary_category,
                "cluster_id": record.cluster_id,
                "source": source,
                "label_origin": record.label_origin,
                "metadata": {
                    "filename": record.filename,
                    "source_path": record.source_path,
                    "weight": record.weight,
                    "label_origin": record.label_origin,
                    "folder_id": record.folder_id,
                    "reason_tags": list(record.reason_tags),
                },
            }
            for record in records
        ]
    )
    final_count = len(store.list_ratings())
    print(
        "Rating import diagnostics: "
        f"existing={existing_count} after_origin_delete={after_delete_count} final={final_count} "
        f"origins={label_origin_counts(records)} labels={_label_counts(records)} "
        f"skipped_unmatched={len(unmatched_rows)}"
    )
    return records


def load_rating_records(
    store: SQLiteFeatureStore,
    ratings_csv_path: str | Path,
    *,
    source: str = "csv",
    skip_unmatched: bool = False,
    unmatched_rows: list[int] | None = None,
) -> list[RatingExample]:
    context = image_context(store)
    rows = store.list_images(require_embedding=True)
    by_id = {int(row["id"]): row for row in rows}
    by_source = {normalize_key(row["source_path"]): row for row in rows}
    by_filename: dict[str, list] = {}
    by_stem: dict[str, list] = {}
    for row in rows:
        source = Path(row["source_path"])
        by_filename.setdefault(normalize_key(source.name), []).append(row)
        if source.stem:
            by_stem.setdefault(normalize_key(source.stem), []).append(row)

    examples: list[RatingExample] = []
    with Path(ratings_csv_path).open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError("ratings CSV must include headers")
        for line_number, record in enumerate(reader, start=2):
            row = resolve_rating_row(record, by_id=by_id, by_source=by_source, by_filename=by_filename, by_stem=by_stem)
            if row is None:
                if skip_unmatched:
                    if unmatched_rows is not None:
                        unmatched_rows.append(line_number)
                    continue
                raise ValueError(f"ratings row {line_number} did not match any image")
            label = (record.get("label") or record.get("rating") or "").strip().lower()
            label_type, numeric_score = parse_rating_label(label)
            weight = parse_rating_weight(record.get("weight"))
            label_origin = normalize_label_origin(record.get("review_round"))
            image_id = int(row["id"])
            item_context = context.get(image_id, {})
            folder_id = (
                str(record.get("folder_id") or record.get("folder") or "").strip()
                or str(Path(row["source_path"]).parent)
            )
            reason_tags = parse_reason_tags(record.get("reason_tags") or record.get("reasons") or "")
            examples.append(
                RatingExample(
                    image_id=image_id,
                    filename=Path(row["source_path"]).name,
                    source_path=row["source_path"],
                    label=label,
                    label_type=label_type,
                    numeric_score=numeric_score,
                    weight=weight,
                    label_origin=label_origin,
                    primary_category=item_context.get("primary_category") or "uncategorized",
                    cluster_id=item_context.get("cluster_id"),
                    folder_id=folder_id,
                    reason_tags=reason_tags,
                )
            )
    return examples


class AdapterTrainer:
    def __init__(
        self,
        store: SQLiteFeatureStore,
        *,
        projected_dim: int = 64,
        min_category_labels: int = 8,
        min_cluster_labels: int = 12,
        global_weight: float = 0.45,
        category_weight: float = 0.45,
        cluster_weight: float = 0.10,
        base_weight: float = 0.50,
        adapter_weight: float = 0.50,
        holdout_fraction: float = 0.20,
        validation_mode: str = "category_grouped_holdout",
        seed: int = 13,
    ):
        self.store = store
        self.projected_dim = int(projected_dim)
        self.min_category_labels = int(min_category_labels)
        self.min_cluster_labels = int(min_cluster_labels)
        self.global_weight = float(global_weight)
        self.category_weight = float(category_weight)
        self.cluster_weight = float(cluster_weight)
        self.base_weight = float(base_weight)
        self.adapter_weight = float(adapter_weight)
        self.holdout_fraction = float(holdout_fraction)
        self.validation_mode = normalize_validation_mode(validation_mode)
        self.seed = int(seed)

    def train(self, *, model_version: str) -> AdapterTrainingResult:
        rows = self.store.list_images(require_embedding=True)
        if not rows:
            raise ValueError("No embeddings are available for adapter training")
        examples = rating_examples_from_store(self.store)
        if len(examples) < 2:
            raise ValueError("At least two ratings are required for adapter training")
        if len({round(example.numeric_score, 6) for example in examples}) < 2:
            raise ValueError("Ratings must include at least two different label values")

        image_ids = [int(row["id"]) for row in rows]
        embeddings = np.vstack([self.store.get_embedding(image_id) for image_id in image_ids])
        projector = PrincipalProjector(min(self.projected_dim, embeddings.shape[0], embeddings.shape[1]))
        projected = projector.fit_transform(embeddings)
        standardizer = Standardizer()
        features = standardizer.fit_transform(projected)
        index_by_id = {image_id: idx for idx, image_id in enumerate(image_ids)}
        context = image_context(self.store)

        train_examples, holdout_examples, validation_info = split_holdout(
            examples,
            holdout_fraction=self.holdout_fraction,
            seed=self.seed,
            validation_mode=self.validation_mode,
        )
        global_model_candidates = {
            "centroid": fit_preference_model(features, train_examples, index_by_id),
            "regression": fit_regression_model(features, train_examples, index_by_id),
        }
        category_models = fit_context_models(
            features,
            train_examples,
            index_by_id,
            key_fn=lambda example: example.primary_category,
            min_labels=self.min_category_labels,
        )
        cluster_models = fit_context_models(
            features,
            train_examples,
            index_by_id,
            key_fn=lambda example: str(example.cluster_id) if example.cluster_id is not None else "",
            min_labels=self.min_cluster_labels,
        )
        global_score_candidates = {
            key: normalize_vector(model.score(features))
            for key, model in global_model_candidates.items()
        }
        raw_category_scores: dict[str, np.ndarray] = {
            key: normalize_vector(model.score(features))
            for key, model in category_models.items()
        }
        raw_cluster_scores: dict[str, np.ndarray] = {
            key: normalize_vector(model.score(features))
            for key, model in cluster_models.items()
        }

        selected_global_model_name = "centroid"
        records = build_adapter_score_records(
            rows=rows,
            index_by_id=index_by_id,
            context=context,
            global_scores=global_score_candidates[selected_global_model_name],
            raw_category_scores=raw_category_scores,
            raw_cluster_scores=raw_cluster_scores,
            global_weight=self.global_weight,
            category_weight=self.category_weight,
            cluster_weight=self.cluster_weight,
            base_weight=self.base_weight,
            adapter_weight=self.adapter_weight,
        )
        selected_global_model_name, records = select_adapter_blend(
            rows=rows,
            index_by_id=index_by_id,
            context=context,
            global_score_candidates=global_score_candidates,
            train_examples=train_examples,
            holdout_examples=holdout_examples,
            overrides=[dict(row) for row in self.store.list_user_overrides()],
            raw_category_scores=raw_category_scores,
            raw_cluster_scores=raw_cluster_scores,
            global_weight=self.global_weight,
            category_weight=self.category_weight,
            cluster_weight=self.cluster_weight,
            base_weight=self.base_weight,
            adapter_weight=self.adapter_weight,
        )
        global_model = global_model_candidates[selected_global_model_name]
        output_scores = {record.image_id: adapter_record_to_store_payload(record) for record in records}

        overrides = [dict(row) for row in self.store.list_user_overrides()]
        metrics = evaluate_scores(
            records,
            train_examples=train_examples,
            holdout_examples=holdout_examples,
            overrides=overrides,
        )
        metrics["model_selection"] = {
            "selected_global_model": selected_global_model_name,
            "global_model_candidates": sorted(global_model_candidates),
        }
        metrics["validation_health"] = adapter_validation_health(metrics)
        metrics["validation"] = validation_info
        config = {
            "projected_dim": self.projected_dim,
            "min_category_labels": self.min_category_labels,
            "min_cluster_labels": self.min_cluster_labels,
            "global_weight": self.global_weight,
            "category_weight": self.category_weight,
            "cluster_weight": self.cluster_weight,
            "selected_global_model": selected_global_model_name,
            "global_model_candidates": sorted(global_model_candidates),
            "validation_health": metrics["validation_health"],
            "base_weight": self.base_weight,
            "adapter_weight": self.adapter_weight,
            "holdout_fraction": self.holdout_fraction,
            "validation_mode": validation_info["validation_mode"],
            "validation_requested_mode": validation_info["requested_mode"],
            "validation_warning": validation_info.get("warning"),
            "train_folder_count": validation_info.get("train_folder_count"),
            "holdout_folder_count": validation_info.get("holdout_folder_count"),
            "seed": self.seed,
            "train_count": len(train_examples),
            "holdout_count": len(holdout_examples),
            "label_origin_counts": label_origin_counts(examples),
            "category_models": sorted(category_models),
            "cluster_models": sorted(cluster_models),
            "model_data": serialize_adapter_model(
                projector=projector,
                standardizer=standardizer,
                global_model=global_model,
                category_models=category_models,
                cluster_models=cluster_models,
            ),
        }
        self.store.save_adapter_model(model_version, "centroid_style_adapter", config, metrics)
        self.store.save_adapter_scores(model_version, output_scores)
        return AdapterTrainingResult(
            model_version=model_version,
            scores=sorted(records, key=lambda record: record.final_score, reverse=True),
            metrics=metrics,
        )


class CentroidPreferenceModel:
    def __init__(self, weights: np.ndarray, bias: float):
        self.weights = np.asarray(weights, dtype=np.float32).reshape(-1)
        self.bias = float(bias)

    def score(self, values: np.ndarray) -> np.ndarray:
        return np.asarray(values, dtype=np.float32) @ self.weights + self.bias


def build_adapter_score_records(
    *,
    rows: Iterable[dict],
    index_by_id: dict[int, int],
    context: dict[int, dict],
    global_scores: np.ndarray,
    raw_category_scores: dict[str, np.ndarray],
    raw_cluster_scores: dict[str, np.ndarray],
    global_weight: float,
    category_weight: float,
    cluster_weight: float,
    base_weight: float,
    adapter_weight: float,
) -> list[AdapterScoreRecord]:
    records: list[AdapterScoreRecord] = []
    for row in rows:
        image_id = int(row["id"])
        idx = index_by_id[image_id]
        item_context = context.get(image_id, {})
        primary_category = item_context.get("primary_category") or "uncategorized"
        cluster_id = item_context.get("cluster_id")
        category_score = (
            float(raw_category_scores[primary_category][idx])
            if primary_category in raw_category_scores
            else None
        )
        cluster_key = str(cluster_id) if cluster_id is not None else ""
        cluster_score = (
            float(raw_cluster_scores[cluster_key][idx])
            if cluster_key in raw_cluster_scores
            else None
        )
        blend_parts = [(global_weight, float(global_scores[idx]))]
        if category_score is not None:
            blend_parts.append((category_weight, category_score))
        if cluster_score is not None:
            blend_parts.append((cluster_weight, cluster_score))
        weight_sum = sum(weight for weight, _ in blend_parts) or 1.0
        adapter_score = sum(weight * score for weight, score in blend_parts) / weight_sum
        confidence_denominator = global_weight + category_weight + cluster_weight
        confidence = min(1.0, weight_sum / (confidence_denominator or 1.0))
        base_score = float(row["final_score"] if row["final_score"] is not None else row["technical_score"] or 0.0)
        final_score = base_weight * base_score + adapter_weight * adapter_score
        records.append(
            AdapterScoreRecord(
                image_id=image_id,
                filename=Path(row["source_path"]).name,
                source_path=row["source_path"],
                primary_category=primary_category,
                cluster_id=cluster_id,
                base_score=base_score,
                global_score=float(global_scores[idx]),
                category_score=category_score,
                cluster_score=cluster_score,
                adapter_score=float(adapter_score),
                confidence=float(confidence),
                final_score=float(final_score),
            )
        )
    return records


def select_adapter_blend(
    *,
    rows: Iterable[dict],
    index_by_id: dict[int, int],
    context: dict[int, dict],
    global_score_candidates: dict[str, np.ndarray],
    train_examples: list[RatingExample],
    holdout_examples: list[RatingExample],
    overrides: Iterable[dict],
    raw_category_scores: dict[str, np.ndarray],
    raw_cluster_scores: dict[str, np.ndarray],
    global_weight: float,
    category_weight: float,
    cluster_weight: float,
    base_weight: float,
    adapter_weight: float,
) -> tuple[str, list[AdapterScoreRecord]]:
    row_list = list(rows)
    override_list = list(overrides)
    model_names = sorted(global_score_candidates) or ["centroid"]
    best_model_name = model_names[0]
    best_records = build_adapter_score_records(
        rows=row_list,
        index_by_id=index_by_id,
        context=context,
        global_scores=global_score_candidates[best_model_name],
        raw_category_scores=raw_category_scores,
        raw_cluster_scores=raw_cluster_scores,
        global_weight=global_weight,
        category_weight=category_weight,
        cluster_weight=cluster_weight,
        base_weight=base_weight,
        adapter_weight=adapter_weight,
    )
    best_score = adapter_blend_selection_score(
        evaluate_scores(
            best_records,
            train_examples=train_examples,
            holdout_examples=holdout_examples,
            overrides=override_list,
        )
    )
    for model_name in model_names:
        if model_name == best_model_name:
            continue
        records = build_adapter_score_records(
            rows=row_list,
            index_by_id=index_by_id,
            context=context,
            global_scores=global_score_candidates[model_name],
            raw_category_scores=raw_category_scores,
            raw_cluster_scores=raw_cluster_scores,
            global_weight=global_weight,
            category_weight=category_weight,
            cluster_weight=cluster_weight,
            base_weight=base_weight,
            adapter_weight=adapter_weight,
        )
        score = adapter_blend_selection_score(
            evaluate_scores(
                records,
                train_examples=train_examples,
                holdout_examples=holdout_examples,
                overrides=override_list,
            )
        )
        if score > best_score:
            best_model_name = model_name
            best_records = records
            best_score = score
    return best_model_name, best_records


def adapter_blend_selection_score(metrics: dict) -> tuple[float, ...]:
    culling = metrics.get("culling") or {}

    def value(name: str, default: float) -> float:
        metric = culling.get(name)
        return float(metric) if metric is not None else default

    return (
        value("top_30_recall", -1.0),
        value("top_20_recall", -1.0),
        value("keeper_recall", -1.0),
        -value("false_reject_rate", 1.0),
        value("rank_correlation", -1.0),
        value("score_fit_percent", -1.0),
    )


def serialize_adapter_model(
    *,
    projector: PrincipalProjector,
    standardizer: Standardizer,
    global_model: CentroidPreferenceModel,
    category_models: dict[str, CentroidPreferenceModel],
    cluster_models: dict[str, CentroidPreferenceModel],
) -> dict:
    if projector.mean_ is None or projector.components_ is None:
        raise RuntimeError("projector must be fitted before serialization")
    if standardizer.mean_ is None or standardizer.scale_ is None:
        raise RuntimeError("standardizer must be fitted before serialization")
    return {
        "schema_version": 1,
        "projector": {
            "mean": _array_to_payload(projector.mean_),
            "components": _array_to_payload(projector.components_),
        },
        "standardizer": {
            "mean": _array_to_payload(standardizer.mean_),
            "scale": _array_to_payload(standardizer.scale_),
        },
        "global_model": _model_to_payload(global_model),
        "category_models": {
            str(key): _model_to_payload(model)
            for key, model in sorted(category_models.items())
        },
        "cluster_models": {
            str(key): _model_to_payload(model)
            for key, model in sorted(cluster_models.items())
        },
    }


def _array_to_payload(values: np.ndarray) -> list:
    return np.asarray(values, dtype=np.float32).round(6).tolist()


def _model_to_payload(model: CentroidPreferenceModel) -> dict:
    return {
        "weights": _array_to_payload(model.weights),
        "bias": round(float(model.bias), 6),
    }


def _model_from_payload(payload: dict) -> CentroidPreferenceModel:
    return CentroidPreferenceModel(
        np.asarray(payload.get("weights") or [], dtype=np.float32),
        float(payload.get("bias") or 0.0),
    )


def fit_preference_model(
    features: np.ndarray,
    examples: list[RatingExample],
    index_by_id: dict[int, int],
) -> CentroidPreferenceModel:
    if not examples:
        return CentroidPreferenceModel(np.zeros(features.shape[1], dtype=np.float32), 0.0)
    values = np.vstack([features[index_by_id[example.image_id]] for example in examples])
    labels = np.asarray([example.numeric_score for example in examples], dtype=np.float32)
    sample_weights = np.asarray([max(0.05, example.weight) for example in examples], dtype=np.float32)
    if float(labels.max() - labels.min()) == 0.0:
        return CentroidPreferenceModel(np.zeros(features.shape[1], dtype=np.float32), float(labels.mean()))
    high_weights = labels * sample_weights
    low_weights = (1.0 - labels) * sample_weights
    high_center = weighted_center(values, high_weights)
    low_center = weighted_center(values, low_weights)
    weights = high_center - low_center
    bias = -0.5 * float(np.dot(high_center, high_center) - np.dot(low_center, low_center))
    return CentroidPreferenceModel(weights, bias)


def fit_regression_model(
    features: np.ndarray,
    examples: list[RatingExample],
    index_by_id: dict[int, int],
    *,
    l2: float = 2.0,
) -> CentroidPreferenceModel:
    usable = [example for example in examples if example.image_id in index_by_id]
    if not usable:
        return CentroidPreferenceModel(np.zeros(features.shape[1], dtype=np.float32), 0.0)
    values = np.vstack([features[index_by_id[example.image_id]] for example in usable]).astype(np.float32)
    labels = np.asarray([example.numeric_score for example in usable], dtype=np.float32)
    sample_weights = np.asarray([max(0.05, example.weight) for example in usable], dtype=np.float32)
    if len(usable) <= 1 or float(labels.max() - labels.min()) <= 1e-8:
        return CentroidPreferenceModel(np.zeros(features.shape[1], dtype=np.float32), float(labels.mean()))

    design = np.hstack([values, np.ones((values.shape[0], 1), dtype=np.float32)])
    weighted_design = design * np.sqrt(sample_weights)[:, None]
    weighted_labels = labels * np.sqrt(sample_weights)
    penalty = np.eye(design.shape[1], dtype=np.float32) * float(max(0.0, l2))
    penalty[-1, -1] = 0.0
    try:
        coefficients = np.linalg.solve(weighted_design.T @ weighted_design + penalty, weighted_design.T @ weighted_labels)
    except np.linalg.LinAlgError:
        coefficients = np.linalg.lstsq(weighted_design.T @ weighted_design + penalty, weighted_design.T @ weighted_labels, rcond=None)[0]
    weights = np.asarray(coefficients[:-1], dtype=np.float32)
    bias = float(coefficients[-1])
    return CentroidPreferenceModel(weights, bias)


def fit_context_models(
    features: np.ndarray,
    examples: list[RatingExample],
    index_by_id: dict[int, int],
    *,
    key_fn,
    min_labels: int,
) -> dict[str, CentroidPreferenceModel]:
    grouped: dict[str, list[RatingExample]] = {}
    for example in examples:
        key = key_fn(example)
        if key:
            grouped.setdefault(key, []).append(example)
    models = {}
    for key, group in grouped.items():
        if len(group) < min_labels:
            continue
        if len({round(example.numeric_score, 6) for example in group}) < 2:
            continue
        models[key] = fit_preference_model(features, group, index_by_id)
    return models


def weighted_center(values: np.ndarray, weights: np.ndarray) -> np.ndarray:
    total = float(np.sum(weights))
    if total <= 1e-8:
        return values.mean(axis=0)
    return (values * weights[:, None]).sum(axis=0) / total


def label_origin_counts(examples: list[RatingExample]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for example in examples:
        counts[example.label_origin] = counts.get(example.label_origin, 0) + 1
    return dict(sorted(counts.items()))


def _label_counts(examples: list[RatingExample]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for example in examples:
        counts[example.label] = counts.get(example.label, 0) + 1
    return dict(sorted(counts.items()))


def normalize_vector(values: np.ndarray) -> np.ndarray:
    scores = {idx: float(value) for idx, value in enumerate(np.asarray(values).reshape(-1))}
    normalized = normalize_scores(scores, mode="minmax")
    return np.asarray([normalized[idx] for idx in range(len(scores))], dtype=np.float32)


def rating_examples_from_store(store: SQLiteFeatureStore) -> list[RatingExample]:
    examples: list[RatingExample] = []
    for row in store.list_ratings():
        try:
            metadata = json.loads(row["metadata_json"] or "{}")
        except (TypeError, ValueError):
            metadata = {}
        examples.append(
            RatingExample(
                image_id=int(row["image_id"]),
                filename=Path(row["source_path"]).name,
                source_path=row["source_path"],
                label=row["label"],
                label_type=row["label_type"],
                numeric_score=float(row["numeric_score"]),
                weight=parse_rating_weight(metadata.get("weight") if isinstance(metadata, dict) else None),
                label_origin=str(
                    row["label_origin"]
                    if "label_origin" in row.keys()
                    else metadata.get("label_origin") if isinstance(metadata, dict) else "legacy"
                ),
                primary_category=row["primary_category"] or "uncategorized",
                cluster_id=int(row["cluster_id"]) if row["cluster_id"] is not None else None,
                folder_id=str(metadata.get("folder_id") or Path(row["source_path"]).parent),
                reason_tags=tuple(str(value) for value in metadata.get("reason_tags", ()) if str(value).strip())
                if isinstance(metadata.get("reason_tags"), list)
                else (),
            )
        )
    return examples


def image_context(store: SQLiteFeatureStore) -> dict[int, dict]:
    query = """
        SELECT
            images.id AS image_id,
            image_categories.primary_category AS primary_category,
            latest.cluster_id AS cluster_id
        FROM images
        LEFT JOIN image_categories ON image_categories.image_id = images.id
        LEFT JOIN (
            SELECT image_cluster_memberships.image_id, MAX(image_cluster_memberships.cluster_id) AS cluster_id
            FROM image_cluster_memberships
            GROUP BY image_cluster_memberships.image_id
        ) AS latest ON latest.image_id = images.id
    """
    with store.lock:
        rows = store.connection.execute(query).fetchall()
    return {
        int(row["image_id"]): {
            "primary_category": row["primary_category"] or "uncategorized",
            "cluster_id": int(row["cluster_id"]) if row["cluster_id"] is not None else None,
        }
        for row in rows
    }


def resolve_rating_row(record: dict, *, by_id: dict, by_source: dict, by_filename: dict, by_stem: dict | None = None):
    image_id = (record.get("id") or record.get("image_id") or "").strip()
    if image_id:
        try:
            return by_id.get(int(image_id))
        except ValueError as exc:
            raise ValueError(f"invalid image id {image_id!r}") from exc

    source_path = (record.get("source_path") or record.get("path") or "").strip()
    if source_path:
        match = by_source.get(normalize_key(source_path))
        if match is not None:
            return match
        # Fall through to filename lookup so a caller whose source_path uses a
        # different mount form (e.g. X:\... vs \\server\share\...) than what
        # was ingested can still match the right image by basename. The
        # filename can come from an explicit CSV column or be derived from the
        # source_path itself.
        filename_candidate = (record.get("filename") or "").strip()
        if not filename_candidate:
            filename_candidate = Path(source_path).name
        if filename_candidate:
            matches = by_filename.get(normalize_key(filename_candidate), [])
            if len(matches) > 1:
                raise ValueError(
                    f"source_path {source_path!r} did not match exactly and filename "
                    f"{filename_candidate!r} matched multiple images; use id to disambiguate"
                )
            if matches:
                return matches[0]
            stem_matches = _stem_matches(filename_candidate, by_stem)
            if len(stem_matches) > 1:
                raise ValueError(
                    f"source_path {source_path!r} did not match exactly and filename stem "
                    f"{Path(filename_candidate).stem!r} matched multiple images; use id to disambiguate"
                )
            if stem_matches:
                return stem_matches[0]
        return None

    filename = (record.get("filename") or "").strip()
    if filename:
        matches = by_filename.get(normalize_key(filename), [])
        if len(matches) > 1:
            raise ValueError(f"filename {filename!r} matched multiple images; use id or source_path")
        if matches:
            return matches[0]
        stem_matches = _stem_matches(filename, by_stem)
        if len(stem_matches) > 1:
            raise ValueError(f"filename stem {Path(filename).stem!r} matched multiple images; use id or source_path")
        return stem_matches[0] if stem_matches else None
    return None


def _stem_matches(filename_or_path: str, by_stem: dict | None) -> list:
    if not by_stem:
        return []
    stem = Path(filename_or_path).stem
    if not stem:
        return []
    return by_stem.get(normalize_key(stem), [])


def parse_rating_label(label: str) -> tuple[str, float]:
    normalized = label.strip().lower()
    if normalized in BUCKET_LABELS:
        label_type = "binary" if normalized in BINARY_LABELS and normalized not in {"maybe", "weak", "strong", "hero", "portfolio"} else "bucket"
        return label_type, BUCKET_LABELS[normalized]
    raise ValueError(f"unsupported rating label {label!r}")


def parse_rating_weight(value: object) -> float:
    try:
        parsed = float(value or 1.0)
    except (TypeError, ValueError):
        parsed = 1.0
    return float(np.clip(parsed, 0.05, 100.0))


def parse_reason_tags(value: object) -> tuple[str, ...]:
    if isinstance(value, (list, tuple, set)):
        raw_values = list(value)
    else:
        raw_values = re.split(r"[;,|]", str(value or ""))
    normalized: list[str] = []
    seen: set[str] = set()
    for item in raw_values:
        text = str(item or "").strip().lower().replace("-", "_").replace(" ", "_")
        if not text or text in seen:
            continue
        seen.add(text)
        normalized.append(text)
    return tuple(normalized)


def normalize_label_origin(value: object) -> str:
    text = str(value or "").strip().lower()
    if text in {"adapter_global_dispute", "global_dispute"}:
        return "global_dispute"
    if text in {"adapter_global_review", "global", "global_review"}:
        return "global"
    if text in {"adapter_dispute", "dispute", "ai_dispute"}:
        return "dispute"
    if text in {"adapter_internal_review", "internal", "internal_adapter"}:
        return "internal_adapter"
    return "manual"


def normalize_key(value: str) -> str:
    return str(Path(value)).replace("/", "\\").lower()


VALIDATION_MODES = {"random_holdout", "category_grouped_holdout", "folder_grouped_holdout"}


def normalize_validation_mode(value: str | None) -> str:
    text = str(value or "category_grouped_holdout").strip().lower()
    if text == "category":
        text = "category_grouped_holdout"
    if text == "folder":
        text = "folder_grouped_holdout"
    if text == "random":
        text = "random_holdout"
    if text not in VALIDATION_MODES:
        raise ValueError(f"unsupported validation mode {value!r}")
    return text


def split_holdout(
    examples: list[RatingExample],
    *,
    holdout_fraction: float,
    seed: int,
    validation_mode: str = "category_grouped_holdout",
) -> tuple[list[RatingExample], list[RatingExample], dict[str, object]]:
    requested_mode = normalize_validation_mode(validation_mode)
    info: dict[str, object] = {
        "requested_mode": requested_mode,
        "validation_mode": requested_mode,
        "warning": None,
        "train_folder_count": None,
        "holdout_folder_count": None,
    }
    if requested_mode == "random_holdout":
        train, holdout = split_holdout_random(examples, holdout_fraction=holdout_fraction, seed=seed)
    elif requested_mode == "folder_grouped_holdout":
        folder_ids = {example.folder_id for example in examples if example.folder_id}
        if len(folder_ids) < 2:
            info["validation_mode"] = "category_grouped_holdout"
            info["warning"] = "folder_grouped_holdout requires at least 2 labeled folders; fell back to category_grouped_holdout"
            train, holdout = split_holdout_by_category(examples, holdout_fraction=holdout_fraction, seed=seed)
        else:
            train, holdout = split_holdout_by_folder(examples, holdout_fraction=holdout_fraction, seed=seed)
    else:
        train, holdout = split_holdout_by_category(examples, holdout_fraction=holdout_fraction, seed=seed)
    train_folders = {example.folder_id for example in train if example.folder_id}
    holdout_folders = {example.folder_id for example in holdout if example.folder_id}
    info["train_folder_count"] = len(train_folders) if train_folders else None
    info["holdout_folder_count"] = len(holdout_folders) if holdout_folders else None
    return train, holdout, info


def split_holdout_random(
    examples: list[RatingExample],
    *,
    holdout_fraction: float,
    seed: int,
) -> tuple[list[RatingExample], list[RatingExample]]:
    if holdout_fraction <= 0.0:
        return list(examples), []
    rng = np.random.default_rng(seed)
    shuffled = list(examples)
    rng.shuffle(shuffled)
    holdout_count = int(round(len(shuffled) * holdout_fraction))
    if len(shuffled) >= 4:
        holdout_count = max(1, holdout_count)
    train = shuffled[holdout_count:]
    holdout = shuffled[:holdout_count]
    if not train:
        return list(examples), []
    return train, holdout


def split_holdout_by_category(
    examples: list[RatingExample],
    *,
    holdout_fraction: float,
    seed: int,
) -> tuple[list[RatingExample], list[RatingExample]]:
    if holdout_fraction <= 0.0:
        return list(examples), []
    rng = np.random.default_rng(seed)
    train: list[RatingExample] = []
    holdout: list[RatingExample] = []
    grouped: dict[str, list[RatingExample]] = {}
    for example in examples:
        grouped.setdefault(example.primary_category, []).append(example)
    for group in grouped.values():
        shuffled = list(group)
        rng.shuffle(shuffled)
        holdout_count = int(round(len(shuffled) * holdout_fraction))
        if len(shuffled) >= 4:
            holdout_count = max(1, holdout_count)
        holdout.extend(shuffled[:holdout_count])
        train.extend(shuffled[holdout_count:])
    if not train:
        return list(examples), []
    return train, holdout


def split_holdout_by_folder(
    examples: list[RatingExample],
    *,
    holdout_fraction: float,
    seed: int,
) -> tuple[list[RatingExample], list[RatingExample]]:
    if holdout_fraction <= 0.0:
        return list(examples), []
    grouped: dict[str, list[RatingExample]] = {}
    for example in examples:
        grouped.setdefault(example.folder_id, []).append(example)
    folders = sorted(grouped)
    rng = np.random.default_rng(seed)
    rng.shuffle(folders)
    target_holdout = max(1, int(round(len(examples) * holdout_fraction)))
    holdout_folders: list[str] = []
    holdout_count = 0
    for folder_id in folders:
        if len(holdout_folders) and holdout_count >= target_holdout:
            break
        holdout_folders.append(folder_id)
        holdout_count += len(grouped[folder_id])
    holdout_set = set(holdout_folders)
    train = [example for example in examples if example.folder_id not in holdout_set]
    holdout = [example for example in examples if example.folder_id in holdout_set]
    if not train:
        largest_folder = max(folders, key=lambda folder_id: len(grouped[folder_id]))
        train = list(grouped[largest_folder])
        holdout = [example for example in examples if example.folder_id != largest_folder]
    if not holdout:
        return list(examples), []
    return train, holdout


def evaluate_scores(
    records: list[AdapterScoreRecord],
    *,
    train_examples: list[RatingExample],
    holdout_examples: list[RatingExample],
    overrides: Iterable[dict] = (),
) -> dict:
    score_by_id = {record.image_id: record.adapter_score for record in records}
    holdout_metrics = evaluate_examples(holdout_examples, score_by_id)
    train_metrics = evaluate_examples(train_examples, score_by_id)
    culling_examples = holdout_examples or train_examples
    final_score_by_id = {record.image_id: record.final_score for record in records}
    culling_records = [
        CullingMetricRecord(
            image_id=example.image_id,
            label=example.label,
            score=float(final_score_by_id.get(example.image_id, score_by_id.get(example.image_id, 0.0))),
            cluster_id=example.cluster_id,
            folder_id=example.folder_id,
        )
        for example in culling_examples
    ]
    mae = holdout_metrics.get("mae") if holdout_examples else train_metrics.get("mae")
    return {
        "train": train_metrics,
        "holdout": holdout_metrics,
        "train_count": len(train_examples),
        "holdout_count": len(holdout_examples),
        "culling": compute_culling_metrics(culling_records, mae=mae, overrides=overrides),
    }


def adapter_validation_health(metrics: dict) -> dict[str, object]:
    holdout = metrics.get("holdout") if isinstance(metrics, dict) else {}
    culling = metrics.get("culling") if isinstance(metrics, dict) else {}
    holdout_count = int(metrics.get("holdout_count") or 0) if isinstance(metrics, dict) else 0
    reasons: list[str] = []
    status = "pass"

    holdout_rank_lift = _metric_float(holdout, "rank_lift")
    rank_correlation = _metric_float(culling, "rank_correlation")
    top_30_recall = _metric_float(culling, "top_30_recall")
    keeper_recall = _metric_float(culling, "keeper_recall")

    if holdout_count <= 0:
        status = "unknown"
        reasons.append("No holdout labels were available.")
    else:
        if holdout_rank_lift is not None and holdout_rank_lift <= 0.0:
            status = "failed"
            reasons.append("Held-out rank lift is not positive.")
        if rank_correlation is not None and rank_correlation <= 0.0:
            status = "failed"
            reasons.append("Held-out rank correlation is not positive.")
        if top_30_recall is not None and top_30_recall < 0.40:
            status = "failed" if status == "failed" else "warning"
            reasons.append("Top-30 keeper recall is below 40%.")
        if keeper_recall is not None and keeper_recall < 0.40:
            status = "failed" if status == "failed" else "warning"
            reasons.append("Keeper recall is below 40%.")

    return {
        "status": status,
        "reasons": reasons,
        "holdout_rank_lift": holdout_rank_lift,
        "rank_correlation": rank_correlation,
        "top_30_recall": top_30_recall,
        "keeper_recall": keeper_recall,
    }


def _metric_float(metrics: object, key: str) -> float | None:
    if not isinstance(metrics, dict):
        return None
    value = metrics.get(key)
    return float(value) if isinstance(value, (int, float)) else None


def evaluate_examples(examples: list[RatingExample], score_by_id: dict[int, float]) -> dict:
    if not examples:
        return {"count": 0, "mae": None, "top_half_mean_label": None, "bottom_half_mean_label": None}
    labels = np.asarray([example.numeric_score for example in examples], dtype=np.float32)
    scores = np.asarray([score_by_id.get(example.image_id, 0.0) for example in examples], dtype=np.float32)
    weights = np.asarray([max(0.05, example.weight) for example in examples], dtype=np.float32)
    mae = float(np.average(np.abs(scores - labels), weights=weights))
    order = np.argsort(scores)
    split = max(1, len(order) // 2)
    bottom = labels[order[:split]]
    top = labels[order[-split:]]
    return {
        "count": len(examples),
        "mae": mae,
        "top_half_mean_label": float(top.mean()),
        "bottom_half_mean_label": float(bottom.mean()),
        "rank_lift": float(top.mean() - bottom.mean()),
    }


def adapter_score_to_csv(record: AdapterScoreRecord, rank: int) -> dict:
    return {
        "rank": rank,
        "id": record.image_id,
        "filename": record.filename,
        "source_path": record.source_path,
        "primary_category": record.primary_category,
        "cluster_id": record.cluster_id,
        "base_score": record.base_score,
        "global_score": record.global_score,
        "category_score": record.category_score,
        "cluster_score": record.cluster_score,
        "adapter_score": record.adapter_score,
        "confidence": record.confidence,
        "final_score": record.final_score,
    }


def adapter_record_to_store_payload(record: AdapterScoreRecord) -> dict[str, object]:
    return {
        "global_score": record.global_score,
        "category_score": record.category_score,
        "cluster_score": record.cluster_score,
        "adapter_score": record.adapter_score,
        "confidence": record.confidence,
        "primary_category": record.primary_category,
        "cluster_id": record.cluster_id,
    }


def apply_serialized_adapter_model(
    store: SQLiteFeatureStore,
    model_version: str,
) -> AdapterTrainingResult:
    model_row = store.connection.execute(
        """
        SELECT model_version, model_type, training_config_json, metrics_json
        FROM adapter_models
        WHERE model_version = ?
        """,
        (str(model_version),),
    ).fetchone()
    if model_row is None:
        raise ValueError(f"adapter model {model_version!r} was not found")
    try:
        config = json.loads(model_row["training_config_json"] or "{}")
    except (TypeError, ValueError):
        config = {}
    if not isinstance(config, dict):
        config = {}
    model_data = config.get("model_data")
    if not isinstance(model_data, dict):
        raise ValueError(
            f"adapter model {model_version!r} cannot be applied to this folder because it has no serialized model data"
        )

    rows = store.list_images(require_embedding=True)
    if not rows:
        raise ValueError("No embeddings are available for adapter scoring")
    image_ids = [int(row["id"]) for row in rows]
    embeddings = np.vstack([store.get_embedding(image_id) for image_id in image_ids])
    features = _transform_with_serialized_model(embeddings, model_data)
    index_by_id = {image_id: idx for idx, image_id in enumerate(image_ids)}
    context = image_context(store)

    global_model = _model_from_payload(model_data.get("global_model") or {})
    category_models = {
        str(key): _model_from_payload(value)
        for key, value in (model_data.get("category_models") or {}).items()
        if isinstance(value, dict)
    }
    # Cluster IDs are database-local, so serialized cluster models are not
    # portable when a global adapter is applied to a different folder DB.
    cluster_models: dict[str, CentroidPreferenceModel] = {}

    global_scores = normalize_vector(global_model.score(features))
    category_scores_by_key = {
        key: normalize_vector(model.score(features))
        for key, model in category_models.items()
    }
    cluster_scores_by_key = {
        key: normalize_vector(model.score(features))
        for key, model in cluster_models.items()
    }
    global_weight = float(config.get("global_weight") or 0.45)
    category_weight = float(config.get("category_weight") or 0.45)
    cluster_weight = float(config.get("cluster_weight") or 0.10)
    base_weight = float(config.get("base_weight") or 0.0)
    adapter_weight = float(config.get("adapter_weight") or 1.0)

    records: list[AdapterScoreRecord] = []
    for row in rows:
        image_id = int(row["id"])
        idx = index_by_id[image_id]
        item_context = context.get(image_id, {})
        primary_category = item_context.get("primary_category") or "uncategorized"
        cluster_id = item_context.get("cluster_id")
        category_score = (
            float(category_scores_by_key[primary_category][idx])
            if primary_category in category_scores_by_key
            else None
        )
        cluster_key = str(cluster_id) if cluster_id is not None else ""
        cluster_score = (
            float(cluster_scores_by_key[cluster_key][idx])
            if cluster_key in cluster_scores_by_key
            else None
        )
        blend_parts = [(global_weight, float(global_scores[idx]))]
        if category_score is not None:
            blend_parts.append((category_weight, category_score))
        if cluster_score is not None:
            blend_parts.append((cluster_weight, cluster_score))
        weight_sum = sum(weight for weight, _ in blend_parts) or 1.0
        adapter_score = sum(weight * score for weight, score in blend_parts) / weight_sum
        confidence_denominator = global_weight + category_weight + cluster_weight
        confidence = min(1.0, weight_sum / (confidence_denominator or 1.0))
        base_score = float(row["final_score"] if row["final_score"] is not None else row["technical_score"] or 0.0)
        final_score = base_weight * base_score + adapter_weight * adapter_score
        records.append(
            AdapterScoreRecord(
                image_id=image_id,
                filename=Path(row["source_path"]).name,
                source_path=row["source_path"],
                primary_category=primary_category,
                cluster_id=cluster_id,
                base_score=base_score,
                global_score=float(global_scores[idx]),
                category_score=category_score,
                cluster_score=cluster_score,
                adapter_score=float(adapter_score),
                confidence=float(confidence),
                final_score=float(final_score),
            )
        )

    output_scores = {record.image_id: adapter_record_to_store_payload(record) for record in records}
    store.save_adapter_scores(str(model_version), output_scores)
    metrics = {
        "applied": {"count": len(records)},
        "source_metrics": _load_metrics(model_row["metrics_json"]),
    }
    return AdapterTrainingResult(
        model_version=str(model_version),
        scores=sorted(records, key=lambda record: record.final_score, reverse=True),
        metrics=metrics,
    )


def _transform_with_serialized_model(embeddings: np.ndarray, model_data: dict) -> np.ndarray:
    projector_payload = model_data.get("projector") or {}
    standardizer_payload = model_data.get("standardizer") or {}
    mean = np.asarray(projector_payload.get("mean") or [], dtype=np.float32)
    components = np.asarray(projector_payload.get("components") or [], dtype=np.float32)
    std_mean = np.asarray(standardizer_payload.get("mean") or [], dtype=np.float32)
    std_scale = np.asarray(standardizer_payload.get("scale") or [], dtype=np.float32)
    if mean.ndim != 1 or components.ndim != 2 or not len(mean) or not len(components):
        raise ValueError("serialized adapter projector is incomplete")
    projected = (np.asarray(embeddings, dtype=np.float32) - mean) @ components.T
    if std_mean.ndim != 1 or std_scale.ndim != 1 or len(std_mean) != projected.shape[1] or len(std_scale) != projected.shape[1]:
        raise ValueError("serialized adapter standardizer is incomplete")
    return (projected - std_mean) / np.where(std_scale == 0.0, 1.0, std_scale)


def _load_metrics(value: object) -> dict:
    try:
        payload = json.loads(value or "{}")
    except (TypeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def adapter_scores_from_store(
    store: SQLiteFeatureStore,
    model_version: str,
    *,
    base_weight: float,
    adapter_weight: float,
) -> list[AdapterScoreRecord]:
    records = []
    for row in store.list_adapter_scores(model_version):
        base_score = float(row["final_score"] if row["final_score"] is not None else row["technical_score"] or 0.0)
        adapter_score = float(row["adapter_score"])
        final_score = base_weight * base_score + adapter_weight * adapter_score
        records.append(
            AdapterScoreRecord(
                image_id=int(row["image_id"]),
                filename=Path(row["source_path"]).name,
                source_path=row["source_path"],
                primary_category=row["primary_category"] or "uncategorized",
                cluster_id=int(row["cluster_id"]) if row["cluster_id"] is not None else None,
                base_score=base_score,
                global_score=float(row["global_score"]),
                category_score=float(row["category_score"]) if row["category_score"] is not None else None,
                cluster_score=float(row["cluster_score"]) if row["cluster_score"] is not None else None,
                adapter_score=adapter_score,
                confidence=float(row["confidence"]),
                final_score=final_score,
            )
        )
    return sorted(records, key=lambda record: record.final_score, reverse=True)


def evaluation_rows(store: SQLiteFeatureStore, model_version: str) -> list[dict]:
    scores = {int(row["image_id"]): row for row in store.list_adapter_scores(model_version)}
    rows = []
    for rating in store.list_ratings():
        score_row = scores.get(int(rating["image_id"]))
        if score_row is None:
            continue
        try:
            metadata = json.loads(rating["metadata_json"] or "{}")
        except (TypeError, ValueError):
            metadata = {}
        rows.append(
            {
                "id": int(rating["image_id"]),
                "filename": Path(rating["source_path"]).name,
                "source_path": rating["source_path"],
                "label": rating["label"],
                "label_type": rating["label_type"],
                "label_origin": rating["label_origin"] if "label_origin" in rating.keys() else "legacy",
                "numeric_score": float(rating["numeric_score"]),
                "weight": parse_rating_weight(metadata.get("weight") if isinstance(metadata, dict) else None),
                "adapter_score": float(score_row["adapter_score"]),
                "absolute_error": abs(float(score_row["adapter_score"]) - float(rating["numeric_score"])),
                "primary_category": rating["primary_category"],
                "cluster_id": rating["cluster_id"],
            }
        )
    return sorted(rows, key=lambda row: row["absolute_error"], reverse=True)


def adapter_evaluation_report(store: SQLiteFeatureStore, model_version: str) -> dict[str, object]:
    metrics = _stored_model_metrics(store, model_version)
    culling = metrics.get("culling") if isinstance(metrics.get("culling"), dict) else None
    if culling is not None:
        return dict(culling)

    rows = evaluation_rows(store, model_version)
    mae = None
    if rows:
        mae = sum(float(row["absolute_error"]) for row in rows) / len(rows)
    records = [
        CullingMetricRecord(
            image_id=int(row["id"]),
            label=str(row["label"]),
            score=float(row["adapter_score"]),
            cluster_id=int(row["cluster_id"]) if row["cluster_id"] is not None else None,
            folder_id=str(Path(row["source_path"]).parent),
        )
        for row in rows
    ]
    return compute_culling_metrics(
        records,
        mae=mae,
        overrides=[dict(row) for row in store.list_user_overrides()],
    )


def _stored_model_metrics(store: SQLiteFeatureStore, model_version: str) -> dict:
    with store.lock:
        row = store.connection.execute(
            "SELECT metrics_json FROM adapter_models WHERE model_version = ?",
            (str(model_version),),
        ).fetchone()
    if row is None:
        return {}
    return _load_metrics(row["metrics_json"])
