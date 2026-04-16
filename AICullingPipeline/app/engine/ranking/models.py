"""Small scalar scoring models used for preference-based ranking."""

from __future__ import annotations

import torch
from torch import nn


class LinearRanker(nn.Module):
    """A single linear layer that maps an embedding to one scalar score."""

    def __init__(self, input_dim: int) -> None:
        super().__init__()
        self.output = nn.Linear(input_dim, 1)

    def forward(
        self,
        embeddings: torch.Tensor,
        reference_features: torch.Tensor | None = None,
    ) -> torch.Tensor:
        """Return one scalar score per embedding."""

        return self.output(embeddings).squeeze(-1)

    def forward_with_details(
        self,
        embeddings: torch.Tensor,
        reference_features: torch.Tensor | None = None,
    ) -> dict[str, torch.Tensor]:
        """Return score components in a stable inspection-friendly format."""

        final_scores = self.forward(embeddings, reference_features=reference_features)
        zeros = torch.zeros_like(final_scores)
        return {
            "base_scores": final_scores,
            "reference_adjustments": zeros,
            "final_scores": final_scores,
        }


class MLPRanker(nn.Module):
    """A lightweight MLP that maps an embedding to one scalar score."""

    def __init__(self, input_dim: int, hidden_dim: int, dropout: float = 0.0) -> None:
        super().__init__()
        self.network = nn.Sequential(
            nn.Linear(input_dim, hidden_dim),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(hidden_dim, 1),
        )

    def forward(
        self,
        embeddings: torch.Tensor,
        reference_features: torch.Tensor | None = None,
    ) -> torch.Tensor:
        """Return one scalar score per embedding."""

        return self.network(embeddings).squeeze(-1)

    def forward_with_details(
        self,
        embeddings: torch.Tensor,
        reference_features: torch.Tensor | None = None,
    ) -> dict[str, torch.Tensor]:
        """Return score components in a stable inspection-friendly format."""

        final_scores = self.forward(embeddings, reference_features=reference_features)
        zeros = torch.zeros_like(final_scores)
        return {
            "base_scores": final_scores,
            "reference_adjustments": zeros,
            "final_scores": final_scores,
        }


class ReferenceConditionedRanker(nn.Module):
    """A base embedding scorer plus a learned exemplar-conditioned adjustment."""

    def __init__(
        self,
        input_dim: int,
        reference_feature_dim: int,
        hidden_dim: int,
        dropout: float = 0.0,
    ) -> None:
        super().__init__()
        if reference_feature_dim <= 0:
            raise ValueError("reference_feature_dim must be greater than 0.")

        self.base_ranker = _build_base_ranker(
            input_dim=input_dim,
            hidden_dim=hidden_dim,
            dropout=dropout,
        )
        conditioner_input_dim = input_dim + reference_feature_dim
        if hidden_dim <= 0:
            self.reference_adjustment = nn.Linear(conditioner_input_dim, 1)
        else:
            self.reference_adjustment = nn.Sequential(
                nn.Linear(conditioner_input_dim, hidden_dim),
                nn.ReLU(),
                nn.Dropout(dropout),
                nn.Linear(hidden_dim, 1),
            )

    def forward(
        self,
        embeddings: torch.Tensor,
        reference_features: torch.Tensor | None = None,
    ) -> torch.Tensor:
        """Return one scalar score per embedding with exemplar conditioning."""

        return self.forward_with_details(
            embeddings,
            reference_features=reference_features,
        )["final_scores"]

    def forward_with_details(
        self,
        embeddings: torch.Tensor,
        reference_features: torch.Tensor | None = None,
    ) -> dict[str, torch.Tensor]:
        """Return base, adjustment, and final score components."""

        if reference_features is None:
            raise ValueError(
                "reference_features are required for ReferenceConditionedRanker forward passes."
            )
        if reference_features.ndim != 2:
            raise ValueError(
                "reference_features must have shape [N, F] for reference-conditioned scoring."
            )

        base_scores = self.base_ranker(embeddings)
        combined = torch.cat([embeddings, reference_features], dim=-1)
        reference_adjustments = self.reference_adjustment(combined).squeeze(-1)
        final_scores = base_scores + reference_adjustments
        return {
            "base_scores": base_scores,
            "reference_adjustments": reference_adjustments,
            "final_scores": final_scores,
        }


def _build_base_ranker(
    *,
    input_dim: int,
    hidden_dim: int = 0,
    dropout: float = 0.0,
) -> nn.Module:
    """Build the original embedding-only scalar scorer."""

    if hidden_dim <= 0:
        return LinearRanker(input_dim)
    return MLPRanker(input_dim, hidden_dim=hidden_dim, dropout=dropout)


def build_ranker(
    *,
    input_dim: int,
    hidden_dim: int = 0,
    dropout: float = 0.0,
    reference_feature_dim: int = 0,
) -> nn.Module:
    """Build the configured scalar ranker."""

    if reference_feature_dim > 0:
        return ReferenceConditionedRanker(
            input_dim=input_dim,
            reference_feature_dim=reference_feature_dim,
            hidden_dim=hidden_dim,
            dropout=dropout,
        )
    return _build_base_ranker(
        input_dim=input_dim,
        hidden_dim=hidden_dim,
        dropout=dropout,
    )


def architecture_name(hidden_dim: int, reference_feature_dim: int = 0) -> str:
    """Return a stable architecture name for summaries and checkpoints."""

    if reference_feature_dim > 0:
        return "reference_conditioned_linear" if hidden_dim <= 0 else "reference_conditioned_mlp"
    return "linear" if hidden_dim <= 0 else "mlp"
