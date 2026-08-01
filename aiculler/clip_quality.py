from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
import time
from typing import Mapping, Sequence

import numpy as np


@dataclass(frozen=True)
class ClipQualityThresholds:
    minimum_mean_embedding_cosine: float = 0.995
    minimum_pairwise_spearman: float = 0.995
    minimum_neighbor_overlap: float = 0.95
    minimum_category_agreement: float = 0.98


@dataclass(frozen=True)
class ClipVariantSpec:
    name: str
    vision_model: Path
    text_model: Path
    provider_mode: str = "cpu"


def run_clip_quality_matrix(
    preview_paths: Sequence[Path],
    *,
    reference: ClipVariantSpec,
    candidates: Sequence[ClipVariantSpec],
    tokenizer: Path,
    category_prompts: Mapping[str, Sequence[str]],
    neighbor_count: int = 5,
) -> dict[str, object]:
    from aiculler.features import HeadlessFeatureExtractor
    from aiculler.text_scoring import CLIPTextEncoder

    if not preview_paths:
        raise ValueError("At least one preview path is required")

    reference_providers = _providers_for_mode(reference.provider_mode)
    started_at = time.perf_counter()
    reference_extractor = HeadlessFeatureExtractor(
        reference.vision_model,
        providers=reference_providers,
        enable_face_quality=False,
    )
    reference_build_seconds = time.perf_counter() - started_at

    started_at = time.perf_counter()
    prepared_inputs = [
        reference_extractor.prepare_model_inputs(path)["clip"]
        for path in preview_paths
    ]
    preprocess_seconds = time.perf_counter() - started_at
    reference_embeddings, reference_inference_seconds = _run_vision_model(
        reference_extractor,
        prepared_inputs,
    )

    started_at = time.perf_counter()
    reference_text_encoder = CLIPTextEncoder(
        reference.text_model,
        tokenizer,
        providers=reference_providers,
    )
    reference_category_vectors = build_category_vectors(reference_text_encoder, category_prompts)
    reference_text_seconds = time.perf_counter() - started_at

    results: dict[str, dict[str, object]] = {}
    for candidate in candidates:
        candidate_providers = _providers_for_mode(candidate.provider_mode)
        started_at = time.perf_counter()
        extractor = HeadlessFeatureExtractor(
            candidate.vision_model,
            providers=candidate_providers,
            enable_face_quality=False,
        )
        build_seconds = time.perf_counter() - started_at
        if extractor.clip_input_size != reference_extractor.clip_input_size:
            raise ValueError(
                f"{candidate.name} requires {extractor.clip_input_size}px input; "
                f"reference requires {reference_extractor.clip_input_size}px"
            )
        candidate_embeddings, inference_seconds = _run_vision_model(extractor, prepared_inputs)

        started_at = time.perf_counter()
        text_encoder = CLIPTextEncoder(
            candidate.text_model,
            tokenizer,
            providers=candidate_providers,
        )
        category_vectors = build_category_vectors(text_encoder, category_prompts)
        text_seconds = time.perf_counter() - started_at
        comparison = compare_clip_outputs(
            candidate_embeddings,
            reference_embeddings,
            candidate_category_vectors=category_vectors,
            reference_category_vectors=reference_category_vectors,
            neighbor_count=neighbor_count,
        )
        comparison.update(
            {
                "models": {
                    "vision": str(candidate.vision_model),
                    "text": str(candidate.text_model),
                },
                "provider_mode": candidate.provider_mode,
                "runtime": extractor.runtime_details(),
                "timing_seconds": {
                    "session_build": round(build_seconds, 6),
                    "vision_inference": round(inference_seconds, 6),
                    "text_model_and_prompts": round(text_seconds, 6),
                },
                "model_bytes": int(candidate.vision_model.stat().st_size + candidate.text_model.stat().st_size),
            }
        )
        results[candidate.name] = comparison

    return {
        "sample_count": len(preview_paths),
        "reference": {
            "name": reference.name,
            "models": {
                "vision": str(reference.vision_model),
                "text": str(reference.text_model),
            },
            "provider_mode": reference.provider_mode,
            "runtime": reference_extractor.runtime_details(),
            "model_bytes": int(reference.vision_model.stat().st_size + reference.text_model.stat().st_size),
            "timing_seconds": {
                "session_build": round(reference_build_seconds, 6),
                "shared_preprocess": round(preprocess_seconds, 6),
                "vision_inference": round(reference_inference_seconds, 6),
                "text_model_and_prompts": round(reference_text_seconds, 6),
            },
        },
        "candidates": results,
    }


def compare_clip_outputs(
    candidate_embeddings: Sequence[np.ndarray],
    reference_embeddings: Sequence[np.ndarray],
    *,
    candidate_category_vectors: Mapping[str, np.ndarray] | None = None,
    reference_category_vectors: Mapping[str, np.ndarray] | None = None,
    neighbor_count: int = 5,
    thresholds: ClipQualityThresholds | None = None,
) -> dict[str, object]:
    """Compare a candidate CLIP export against a higher-precision reference.

    Embedding coordinates are compared directly, then through the relationships
    that drive culling: pairwise similarity, nearest neighbors, and semantic
    category routing.
    """
    candidate = _normalized_matrix(candidate_embeddings)
    reference = _normalized_matrix(reference_embeddings)
    if candidate.shape != reference.shape:
        raise ValueError(
            f"Embedding matrices differ: candidate {candidate.shape}, reference {reference.shape}"
        )
    if candidate.shape[0] == 0:
        raise ValueError("At least one embedding pair is required")

    paired_cosines = np.sum(candidate * reference, axis=1)
    candidate_similarity = candidate @ candidate.T
    reference_similarity = reference @ reference.T
    pairwise_candidate, pairwise_reference = _upper_triangle_pair(
        candidate_similarity,
        reference_similarity,
    )
    pairwise_deltas = np.abs(pairwise_candidate - pairwise_reference)

    resolved_neighbor_count = min(max(0, int(neighbor_count)), max(0, candidate.shape[0] - 1))
    neighbor_overlaps = _neighbor_overlaps(
        candidate_similarity,
        reference_similarity,
        resolved_neighbor_count,
    )

    report: dict[str, object] = {
        "sample_count": int(candidate.shape[0]),
        "embedding_dimension": int(candidate.shape[1]),
        "embedding_cosine": _distribution(paired_cosines),
        "pairwise_similarity": {
            "comparison_count": int(pairwise_candidate.size),
            "spearman": _spearman(pairwise_candidate, pairwise_reference),
            "mean_absolute_delta": _float_or_none(np.mean(pairwise_deltas)) if pairwise_deltas.size else None,
            "maximum_absolute_delta": _float_or_none(np.max(pairwise_deltas)) if pairwise_deltas.size else None,
        },
        "nearest_neighbors": {
            "k": resolved_neighbor_count,
            "overlap": _distribution(neighbor_overlaps),
        },
    }

    category_report = _compare_categories(
        candidate,
        reference,
        candidate_category_vectors,
        reference_category_vectors,
    )
    if category_report is not None:
        report["semantic_categories"] = category_report

    limits = thresholds or ClipQualityThresholds()
    checks = {
        "mean_embedding_cosine": float(np.mean(paired_cosines)) >= limits.minimum_mean_embedding_cosine,
        "pairwise_spearman": (
            _spearman(pairwise_candidate, pairwise_reference) or 0.0
        ) >= limits.minimum_pairwise_spearman,
        "mean_neighbor_overlap": (
            float(np.mean(neighbor_overlaps)) if neighbor_overlaps.size else 1.0
        ) >= limits.minimum_neighbor_overlap,
    }
    if category_report is not None:
        checks["category_agreement"] = (
            float(category_report["primary_category_agreement"])
            >= limits.minimum_category_agreement
        )
    report["thresholds"] = asdict(limits)
    report["checks"] = checks
    report["candidate_recommended"] = all(checks.values())
    return report


def build_category_vectors(
    encoder,
    category_prompts: Mapping[str, Sequence[str]],
) -> dict[str, np.ndarray]:
    vectors: dict[str, np.ndarray] = {}
    for category, prompts in category_prompts.items():
        prompt_vectors = [_normalize_vector(encoder.encode(prompt)) for prompt in prompts]
        if prompt_vectors:
            vectors[str(category)] = _normalize_vector(np.mean(np.vstack(prompt_vectors), axis=0))
    return vectors


def _providers_for_mode(mode: str) -> list[str] | None:
    normalized = str(mode).strip().lower()
    if normalized == "cpu":
        return ["CPUExecutionProvider"]
    if normalized == "auto":
        return None
    raise ValueError(f"Unknown provider mode: {mode}")


def _run_vision_model(extractor, prepared_inputs: Sequence[np.ndarray]) -> tuple[list[np.ndarray], float]:
    started_at = time.perf_counter()
    embeddings = [
        np.asarray(
            extractor.clip_session.run(
                [extractor.clip_output_name],
                {extractor.clip_input_name: model_input},
            )[0]
        ).reshape(-1)
        for model_input in prepared_inputs
    ]
    return embeddings, time.perf_counter() - started_at


def _compare_categories(
    candidate_embeddings: np.ndarray,
    reference_embeddings: np.ndarray,
    candidate_vectors: Mapping[str, np.ndarray] | None,
    reference_vectors: Mapping[str, np.ndarray] | None,
) -> dict[str, object] | None:
    if candidate_vectors is None and reference_vectors is None:
        return None
    if candidate_vectors is None or reference_vectors is None:
        raise ValueError("Both candidate and reference category vectors are required")
    categories = sorted(set(candidate_vectors) & set(reference_vectors))
    if not categories:
        raise ValueError("Candidate and reference category vectors have no categories in common")

    candidate_text = _normalized_matrix([candidate_vectors[name] for name in categories])
    reference_text = _normalized_matrix([reference_vectors[name] for name in categories])
    if candidate_text.shape != reference_text.shape:
        raise ValueError("Candidate and reference category vector dimensions differ")
    candidate_scores = candidate_embeddings @ candidate_text.T
    reference_scores = reference_embeddings @ reference_text.T
    candidate_labels = np.argmax(candidate_scores, axis=1)
    reference_labels = np.argmax(reference_scores, axis=1)
    agreements = candidate_labels == reference_labels
    disagreements: dict[str, int] = {}
    for candidate_index, reference_index in zip(candidate_labels, reference_labels):
        if candidate_index == reference_index:
            continue
        key = f"{categories[int(reference_index)]} -> {categories[int(candidate_index)]}"
        disagreements[key] = disagreements.get(key, 0) + 1

    rank_correlations = [
        _spearman(candidate_scores[:, index], reference_scores[:, index])
        for index in range(len(categories))
    ]
    valid_rank_correlations = np.asarray(
        [value for value in rank_correlations if value is not None],
        dtype=np.float64,
    )
    score_deltas = np.abs(candidate_scores - reference_scores)
    text_cosines = np.sum(candidate_text * reference_text, axis=1)
    return {
        "category_count": len(categories),
        "categories": categories,
        "primary_category_agreement": float(np.mean(agreements)),
        "disagreement_count": int(np.count_nonzero(~agreements)),
        "disagreements": disagreements,
        "category_vector_cosine": _distribution(text_cosines),
        "score_spearman": _spearman(candidate_scores.reshape(-1), reference_scores.reshape(-1)),
        "per_category_rank_spearman": _distribution(valid_rank_correlations),
        "score_mean_absolute_delta": float(np.mean(score_deltas)),
        "score_maximum_absolute_delta": float(np.max(score_deltas)),
    }


def _normalized_matrix(values: Sequence[np.ndarray]) -> np.ndarray:
    if len(values) == 0:
        return np.empty((0, 0), dtype=np.float32)
    matrix = np.vstack([np.asarray(value, dtype=np.float32).reshape(1, -1) for value in values])
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    return np.divide(matrix, norms, out=np.zeros_like(matrix), where=norms > 0.0)


def _normalize_vector(value: np.ndarray) -> np.ndarray:
    vector = np.asarray(value, dtype=np.float32).reshape(-1)
    norm = float(np.linalg.norm(vector))
    return vector if norm == 0.0 else vector / norm


def _upper_triangle_pair(left: np.ndarray, right: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    indices = np.triu_indices(left.shape[0], k=1)
    return left[indices].astype(np.float64), right[indices].astype(np.float64)


def _neighbor_overlaps(
    candidate_similarity: np.ndarray,
    reference_similarity: np.ndarray,
    neighbor_count: int,
) -> np.ndarray:
    if neighbor_count <= 0:
        return np.empty(0, dtype=np.float64)
    candidate = candidate_similarity.copy()
    reference = reference_similarity.copy()
    np.fill_diagonal(candidate, -np.inf)
    np.fill_diagonal(reference, -np.inf)
    candidate_neighbors = np.argsort(candidate, axis=1)[:, -neighbor_count:]
    reference_neighbors = np.argsort(reference, axis=1)[:, -neighbor_count:]
    return np.asarray(
        [
            len(set(candidate_row) & set(reference_row)) / neighbor_count
            for candidate_row, reference_row in zip(candidate_neighbors, reference_neighbors)
        ],
        dtype=np.float64,
    )


def _spearman(left: np.ndarray, right: np.ndarray) -> float | None:
    left_values = np.asarray(left, dtype=np.float64).reshape(-1)
    right_values = np.asarray(right, dtype=np.float64).reshape(-1)
    if left_values.size != right_values.size:
        raise ValueError("Spearman inputs must have equal lengths")
    if left_values.size < 2:
        return None
    left_ranks = _average_ranks(left_values)
    right_ranks = _average_ranks(right_values)
    left_centered = left_ranks - np.mean(left_ranks)
    right_centered = right_ranks - np.mean(right_ranks)
    denominator = float(np.linalg.norm(left_centered) * np.linalg.norm(right_centered))
    if denominator == 0.0:
        return 1.0 if np.array_equal(left_values, right_values) else 0.0
    return float(np.dot(left_centered, right_centered) / denominator)


def _average_ranks(values: np.ndarray) -> np.ndarray:
    order = np.argsort(values, kind="mergesort")
    sorted_values = values[order]
    ranks = np.empty(values.size, dtype=np.float64)
    start = 0
    while start < values.size:
        end = start + 1
        while end < values.size and sorted_values[end] == sorted_values[start]:
            end += 1
        ranks[order[start:end]] = (start + end - 1) / 2.0
        start = end
    return ranks


def _distribution(values: np.ndarray) -> dict[str, float | int | None]:
    array = np.asarray(values, dtype=np.float64).reshape(-1)
    if not array.size:
        return {"count": 0, "minimum": None, "p05": None, "median": None, "mean": None}
    return {
        "count": int(array.size),
        "minimum": float(np.min(array)),
        "p05": float(np.percentile(array, 5)),
        "median": float(np.median(array)),
        "mean": float(np.mean(array)),
    }


def _float_or_none(value: np.floating | float) -> float | None:
    result = float(value)
    return result if np.isfinite(result) else None
