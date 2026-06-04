from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np

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

        train_examples, holdout_examples = split_holdout_by_category(
            examples,
            holdout_fraction=self.holdout_fraction,
            seed=self.seed,
        )
        global_model = fit_preference_model(features, train_examples, index_by_id)
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

        raw_global = global_model.score(features)
        global_scores = normalize_vector(raw_global)
        raw_category_scores: dict[str, np.ndarray] = {
            key: normalize_vector(model.score(features))
            for key, model in category_models.items()
        }
        raw_cluster_scores: dict[str, np.ndarray] = {
            key: normalize_vector(model.score(features))
            for key, model in cluster_models.items()
        }

        output_scores: dict[int, dict] = {}
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
            blend_parts = [(self.global_weight, float(global_scores[idx]))]
            if category_score is not None:
                blend_parts.append((self.category_weight, category_score))
            if cluster_score is not None:
                blend_parts.append((self.cluster_weight, cluster_score))
            weight_sum = sum(weight for weight, _ in blend_parts) or 1.0
            adapter_score = sum(weight * score for weight, score in blend_parts) / weight_sum
            confidence = min(1.0, weight_sum / (self.global_weight + self.category_weight + self.cluster_weight))
            base_score = float(row["final_score"] if row["final_score"] is not None else row["technical_score"] or 0.0)
            final_score = self.base_weight * base_score + self.adapter_weight * adapter_score
            output_scores[image_id] = {
                "global_score": float(global_scores[idx]),
                "category_score": category_score,
                "cluster_score": cluster_score,
                "adapter_score": float(adapter_score),
                "confidence": float(confidence),
                "primary_category": primary_category,
                "cluster_id": cluster_id,
            }
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

        metrics = evaluate_scores(
            records,
            train_examples=train_examples,
            holdout_examples=holdout_examples,
        )
        config = {
            "projected_dim": self.projected_dim,
            "min_category_labels": self.min_category_labels,
            "min_cluster_labels": self.min_cluster_labels,
            "global_weight": self.global_weight,
            "category_weight": self.category_weight,
            "cluster_weight": self.cluster_weight,
            "base_weight": self.base_weight,
            "adapter_weight": self.adapter_weight,
            "holdout_fraction": self.holdout_fraction,
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


def evaluate_scores(
    records: list[AdapterScoreRecord],
    *,
    train_examples: list[RatingExample],
    holdout_examples: list[RatingExample],
) -> dict:
    score_by_id = {record.image_id: record.adapter_score for record in records}
    return {
        "train": evaluate_examples(train_examples, score_by_id),
        "holdout": evaluate_examples(holdout_examples, score_by_id),
        "train_count": len(train_examples),
        "holdout_count": len(holdout_examples),
    }


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

    output_scores: dict[int, dict] = {}
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
        confidence = min(1.0, weight_sum / (global_weight + category_weight + cluster_weight))
        base_score = float(row["final_score"] if row["final_score"] is not None else row["technical_score"] or 0.0)
        final_score = base_weight * base_score + adapter_weight * adapter_score
        output_scores[image_id] = {
            "global_score": float(global_scores[idx]),
            "category_score": category_score,
            "cluster_score": cluster_score,
            "adapter_score": float(adapter_score),
            "confidence": float(confidence),
            "primary_category": primary_category,
            "cluster_id": cluster_id,
        }
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

    store.save_adapter_scores(str(model_version), output_scores)
    metrics = {"applied": {"count": len(records)}, "source_metrics": _load_metrics(model_row["metrics_json"])}
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
