"""Checkpoint loading, device resolution, and batched score inference."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Tuple

import numpy as np
import torch

from app.engine.ranking.models import build_ranker


@dataclass(frozen=True)
class ScoreBreakdown:
    """Batched score outputs with optional reference-conditioning details."""

    final_scores: np.ndarray
    base_scores: np.ndarray
    reference_adjustments: np.ndarray
    reference_features: np.ndarray | None
    reference_feature_names: tuple[str, ...]


def resolve_device(requested_device: str) -> torch.device:
    """Resolve an explicit or automatic device request."""

    if requested_device == "auto":
        return torch.device("cuda" if torch.cuda.is_available() else "cpu")
    return torch.device(requested_device)


def l2_normalize_embeddings(embeddings: np.ndarray) -> np.ndarray:
    """Return L2-normalized embeddings for stable ranking input scaling."""

    array = np.asarray(embeddings, dtype=np.float32)
    norms = np.linalg.norm(array, axis=1, keepdims=True)
    norms = np.clip(norms, a_min=1e-12, a_max=None)
    return array / norms


def load_ranker_checkpoint(
    checkpoint_path: Path,
    *,
    device: str = "auto",
) -> Tuple[torch.nn.Module, Dict[str, Any], torch.device]:
    """Load a saved ranker checkpoint and rebuild the model."""

    resolved_device = resolve_device(device)
    checkpoint = torch.load(checkpoint_path, map_location=resolved_device)
    model_config = checkpoint["model_config"]
    model = build_ranker(
        input_dim=int(model_config["input_dim"]),
        hidden_dim=int(model_config["hidden_dim"]),
        dropout=float(model_config["dropout"]),
        reference_feature_dim=int(model_config.get("reference_feature_dim", 0)),
    )
    model.load_state_dict(checkpoint["model_state_dict"])
    model.to(resolved_device)
    model.eval()
    return model, checkpoint, resolved_device


def score_embedding_batch(
    model: torch.nn.Module,
    embeddings: np.ndarray,
    *,
    reference_features: np.ndarray | None = None,
    device: torch.device,
    batch_size: int = 2048,
) -> np.ndarray:
    """Score many embeddings in batches without tracking gradients."""

    return score_embedding_batch_with_details(
        model,
        embeddings,
        reference_features=reference_features,
        device=device,
        batch_size=batch_size,
    ).final_scores


def score_embedding_batch_with_details(
    model: torch.nn.Module,
    embeddings: np.ndarray,
    *,
    reference_features: np.ndarray | None = None,
    reference_feature_names: tuple[str, ...] = (),
    device: torch.device,
    batch_size: int = 2048,
) -> ScoreBreakdown:
    """Score many embeddings in batches and return score-component details."""

    if embeddings.ndim != 2:
        raise ValueError(
            f"Expected embeddings with shape [N, D], received {tuple(embeddings.shape)}."
        )

    if reference_features is not None:
        reference_array = np.asarray(reference_features, dtype=np.float32)
        if reference_array.ndim != 2:
            raise ValueError(
                "Expected reference_features with shape [N, F] when provided."
            )
        if reference_array.shape[0] != embeddings.shape[0]:
            raise ValueError(
                "reference_features must have the same row count as embeddings."
            )
    else:
        reference_array = None

    rows = embeddings.shape[0]
    if rows == 0:
        empty = np.empty((0,), dtype=np.float32)
        empty_features = (
            np.empty((0, len(reference_feature_names)), dtype=np.float32)
            if reference_feature_names
            else None
        )
        return ScoreBreakdown(
            final_scores=empty,
            base_scores=empty,
            reference_adjustments=empty,
            reference_features=empty_features,
            reference_feature_names=reference_feature_names,
        )

    final_scores: list[np.ndarray] = []
    base_scores: list[np.ndarray] = []
    reference_adjustments: list[np.ndarray] = []
    with torch.inference_mode():
        for start in range(0, rows, batch_size):
            end = min(rows, start + batch_size)
            batch = torch.from_numpy(embeddings[start:end].astype(np.float32, copy=False)).to(device)
            if reference_array is None:
                batch_reference = None
            else:
                batch_reference = torch.from_numpy(
                    reference_array[start:end].astype(np.float32, copy=False)
                ).to(device)

            details = model.forward_with_details(
                batch,
                reference_features=batch_reference,
            )
            final_scores.append(
                details["final_scores"].detach().cpu().numpy().astype(np.float32, copy=False)
            )
            base_scores.append(
                details["base_scores"].detach().cpu().numpy().astype(np.float32, copy=False)
            )
            reference_adjustments.append(
                details["reference_adjustments"]
                .detach()
                .cpu()
                .numpy()
                .astype(np.float32, copy=False)
            )

    materialized_reference_features = None
    if reference_array is not None:
        materialized_reference_features = reference_array.astype(np.float32, copy=False)

    return ScoreBreakdown(
        final_scores=np.concatenate(final_scores, axis=0).astype(np.float32, copy=False),
        base_scores=np.concatenate(base_scores, axis=0).astype(np.float32, copy=False),
        reference_adjustments=np.concatenate(reference_adjustments, axis=0).astype(np.float32, copy=False),
        reference_features=materialized_reference_features,
        reference_feature_names=reference_feature_names,
    )
