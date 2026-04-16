"""Week 4 pairwise ranker training on frozen image embeddings."""

from __future__ import annotations

import logging
from pathlib import Path
import random
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import torch
from torch import nn
from torch.nn import functional as F
from torch.utils.data import DataLoader

from app.config import RankingTrainConfig
from app.engine.ranking.datasets import (
    PairwisePreferenceDataset,
    split_pairwise_preferences,
)
from app.engine.ranking.inference import l2_normalize_embeddings, resolve_device
from app.engine.ranking.models import architecture_name, build_ranker
from app.engine.ranking.reference_bank import build_reference_feature_matrix, summarize_reference_bank
from app.storage.reference_bank import load_reference_bank
from app.storage.ranking_artifacts import (
    load_preference_labels,
    load_ranking_artifacts,
    save_ranking_summary_json,
    save_training_history_csv,
)
from app.utils.io_utils import save_json


LOGGER = logging.getLogger(__name__)


class PairwiseRankerTrainer:
    """Trainer for the first lightweight preference-based ranking model."""

    def __init__(self, config: RankingTrainConfig) -> None:
        self.config = config
        self.device = resolve_device(config.device)

    def run(self) -> Dict[str, Path]:
        """Train the ranker, save artifacts, and return output paths."""

        self.config.output_dir.mkdir(parents=True, exist_ok=True)
        _set_random_seed(self.config.random_seed)

        ranking_artifacts = load_ranking_artifacts(
            self.config.artifacts_dir,
            metadata_filename=self.config.metadata_filename,
            embeddings_filename=self.config.embeddings_filename,
            image_ids_filename=self.config.image_ids_filename,
            clusters_filename=self.config.clusters_filename,
        )
        loaded_labels = load_preference_labels(
            labels_dir=self.config.labels_dir,
            ranking_artifacts=ranking_artifacts,
            pairwise_labels_filename=self.config.pairwise_labels_filename,
            cluster_labels_filename=self.config.cluster_labels_filename,
            include_cluster_label_pairs=self.config.include_cluster_label_pairs,
            skip_ties=self.config.skip_ties,
        )
        if not loaded_labels.preferences:
            raise ValueError(
                "No usable preference pairs were found. "
                "Add pairwise labels or cluster labels before training."
            )

        embeddings = ranking_artifacts.embeddings
        if self.config.normalize_embeddings:
            embeddings = l2_normalize_embeddings(embeddings)

        reference_feature_matrix: Optional[np.ndarray] = None
        reference_feature_names: tuple[str, ...] = ()
        reference_summary: Optional[Dict[str, Any]] = None
        if self.config.reference_bank_path is not None:
            reference_bank = load_reference_bank(self.config.reference_bank_path)
            reference_feature_matrix, reference_feature_names = build_reference_feature_matrix(
                ranking_artifacts.embeddings,
                reference_bank,
                top_k=self.config.reference_top_k,
            )
            reference_summary = {
                "reference_bank_path": str(self.config.reference_bank_path),
                "reference_top_k": self.config.reference_top_k,
                "reference_feature_dim": int(reference_feature_matrix.shape[1]),
                "reference_feature_names": list(reference_feature_names),
                "reference_bucket_counts": summarize_reference_bank(reference_bank),
            }

        train_preferences, validation_preferences = split_pairwise_preferences(
            loaded_labels.preferences,
            validation_fraction=self.config.validation_fraction,
            random_seed=self.config.random_seed,
        )
        if not train_preferences:
            raise ValueError("The training split is empty. Add more labels or reduce validation_fraction.")

        model = build_ranker(
            input_dim=ranking_artifacts.feature_dim,
            hidden_dim=self.config.hidden_dim,
            dropout=self.config.dropout,
            reference_feature_dim=(
                int(reference_feature_matrix.shape[1]) if reference_feature_matrix is not None else 0
            ),
        ).to(self.device)
        optimizer = torch.optim.Adam(
            model.parameters(),
            lr=self.config.learning_rate,
            weight_decay=self.config.weight_decay,
        )

        train_loader = DataLoader(
            PairwisePreferenceDataset(
                embeddings,
                train_preferences,
                reference_features=reference_feature_matrix,
            ),
            batch_size=self.config.batch_size,
            shuffle=True,
        )
        validation_loader: Optional[DataLoader]
        if validation_preferences:
            validation_loader = DataLoader(
                PairwisePreferenceDataset(
                    embeddings,
                    validation_preferences,
                    reference_features=reference_feature_matrix,
                ),
                batch_size=self.config.batch_size,
                shuffle=False,
            )
        else:
            validation_loader = None

        history: List[Dict[str, Any]] = []
        best_metric = float("inf")
        best_epoch = 0
        best_validation_accuracy: Optional[float] = None
        best_validation_loss: Optional[float] = None

        best_checkpoint_path = self.config.output_dir / self.config.best_checkpoint_filename
        last_checkpoint_path = self.config.output_dir / self.config.last_checkpoint_filename
        history_path = self.config.output_dir / self.config.history_filename
        metrics_path = self.config.output_dir / self.config.metrics_filename
        resolved_config_path = self.config.output_dir / self.config.resolved_config_filename

        for epoch in range(1, self.config.num_epochs + 1):
            train_loss = self._run_training_epoch(model, optimizer, train_loader)
            validation_loss, validation_accuracy = self._evaluate(model, validation_loader)
            history_row = {
                "epoch": epoch,
                "train_loss": train_loss,
                "validation_loss": validation_loss,
                "validation_pairwise_accuracy": validation_accuracy,
            }
            history.append(history_row)

            selection_metric = validation_loss if validation_loss is not None else train_loss
            _save_checkpoint(
                last_checkpoint_path,
                model=model,
                config=self.config,
                feature_dim=ranking_artifacts.feature_dim,
                reference_feature_dim=(
                    int(reference_feature_matrix.shape[1]) if reference_feature_matrix is not None else 0
                ),
                reference_feature_names=reference_feature_names,
                reference_summary=reference_summary,
                epoch=epoch,
                metrics=history_row,
            )

            if selection_metric < best_metric:
                best_metric = selection_metric
                best_epoch = epoch
                best_validation_loss = validation_loss
                best_validation_accuracy = validation_accuracy
                _save_checkpoint(
                    best_checkpoint_path,
                    model=model,
                    config=self.config,
                    feature_dim=ranking_artifacts.feature_dim,
                    reference_feature_dim=(
                        int(reference_feature_matrix.shape[1]) if reference_feature_matrix is not None else 0
                    ),
                    reference_feature_names=reference_feature_names,
                    reference_summary=reference_summary,
                    epoch=epoch,
                    metrics=history_row,
                )

            LOGGER.info(
                "Epoch %s/%s train_loss=%.6f validation_loss=%s validation_pairwise_accuracy=%s",
                epoch,
                self.config.num_epochs,
                train_loss,
                _format_optional_float(validation_loss),
                _format_optional_float(validation_accuracy),
            )

        summary = {
            "resolved_device": str(self.device),
            "embedding_dimension": ranking_artifacts.feature_dim,
            "model_architecture": architecture_name(
                self.config.hidden_dim,
                int(reference_feature_matrix.shape[1]) if reference_feature_matrix is not None else 0,
            ),
            "hidden_dim": self.config.hidden_dim,
            "dropout": self.config.dropout,
            "normalize_embeddings": self.config.normalize_embeddings,
            "reference_conditioning_enabled": reference_feature_matrix is not None,
            "reference_feature_dim": (
                int(reference_feature_matrix.shape[1]) if reference_feature_matrix is not None else 0
            ),
            "reference_feature_names": list(reference_feature_names),
            "reference_summary": reference_summary,
            "loss_name": self.config.loss_name,
            "total_preference_pairs": len(loaded_labels.preferences),
            "train_pairs": len(train_preferences),
            "validation_pairs": len(validation_preferences),
            "best_epoch": best_epoch,
            "best_validation_loss": best_validation_loss,
            "best_validation_pairwise_accuracy": best_validation_accuracy,
            "final_train_loss": history[-1]["train_loss"],
            "final_validation_loss": history[-1]["validation_loss"],
            "final_validation_pairwise_accuracy": history[-1]["validation_pairwise_accuracy"],
            "best_checkpoint": str(best_checkpoint_path),
            "last_checkpoint": str(last_checkpoint_path),
            "label_summary": loaded_labels.summary,
        }
        save_training_history_csv(history_path, history)
        save_ranking_summary_json(metrics_path, summary)

        resolved_config = self.config.to_serializable_dict()
        resolved_config["resolved_device"] = str(self.device)
        resolved_config["embedding_dimension"] = ranking_artifacts.feature_dim
        resolved_config["model_architecture"] = architecture_name(
            self.config.hidden_dim,
            int(reference_feature_matrix.shape[1]) if reference_feature_matrix is not None else 0,
        )
        resolved_config["total_preference_pairs"] = len(loaded_labels.preferences)
        resolved_config["train_pairs"] = len(train_preferences)
        resolved_config["validation_pairs"] = len(validation_preferences)
        resolved_config["reference_conditioning_enabled"] = reference_feature_matrix is not None
        resolved_config["reference_feature_names"] = list(reference_feature_names)
        save_json(resolved_config_path, resolved_config)

        return {
            "best_checkpoint": best_checkpoint_path,
            "last_checkpoint": last_checkpoint_path,
            "metrics": metrics_path,
            "history": history_path,
            "resolved_config": resolved_config_path,
        }

    def _run_training_epoch(
        self,
        model: nn.Module,
        optimizer: torch.optim.Optimizer,
        dataloader: DataLoader,
    ) -> float:
        """Run one training epoch and return the average loss."""

        model.train()
        total_loss = 0.0
        total_pairs = 0

        for batch in dataloader:
            preferred = batch["preferred"].to(self.device)
            other = batch["other"].to(self.device)
            preferred_reference = batch.get("preferred_reference_features")
            other_reference = batch.get("other_reference_features")
            if preferred_reference is not None:
                preferred_reference = preferred_reference.to(self.device)
            if other_reference is not None:
                other_reference = other_reference.to(self.device)
            optimizer.zero_grad(set_to_none=True)
            preferred_scores = model(preferred, reference_features=preferred_reference)
            other_scores = model(other, reference_features=other_reference)
            loss = _pairwise_logistic_loss(preferred_scores, other_scores)
            loss.backward()
            optimizer.step()

            batch_size = preferred.shape[0]
            total_loss += float(loss.item()) * batch_size
            total_pairs += batch_size

        return total_loss / max(1, total_pairs)

    def _evaluate(
        self,
        model: nn.Module,
        dataloader: Optional[DataLoader],
    ) -> Tuple[Optional[float], Optional[float]]:
        """Evaluate on the validation split when present."""

        if dataloader is None:
            return None, None

        model.eval()
        total_loss = 0.0
        total_pairs = 0
        total_correct = 0
        with torch.inference_mode():
            for batch in dataloader:
                preferred = batch["preferred"].to(self.device)
                other = batch["other"].to(self.device)
                preferred_reference = batch.get("preferred_reference_features")
                other_reference = batch.get("other_reference_features")
                if preferred_reference is not None:
                    preferred_reference = preferred_reference.to(self.device)
                if other_reference is not None:
                    other_reference = other_reference.to(self.device)
                preferred_scores = model(preferred, reference_features=preferred_reference)
                other_scores = model(other, reference_features=other_reference)
                loss = _pairwise_logistic_loss(preferred_scores, other_scores)

                diff = preferred_scores - other_scores
                batch_size = preferred.shape[0]
                total_loss += float(loss.item()) * batch_size
                total_pairs += batch_size
                total_correct += int((diff > 0).sum().item())

        if total_pairs == 0:
            return None, None
        return total_loss / total_pairs, total_correct / total_pairs


def train_ranker(config: RankingTrainConfig) -> Dict[str, Path]:
    """Train the Week 4 ranker through the public engine API."""

    return PairwiseRankerTrainer(config).run()


def _pairwise_logistic_loss(
    preferred_scores: torch.Tensor,
    other_scores: torch.Tensor,
) -> torch.Tensor:
    """Compute the logistic pairwise ranking loss."""

    return F.softplus(-(preferred_scores - other_scores)).mean()


def _save_checkpoint(
    path: Path,
    *,
    model: nn.Module,
    config: RankingTrainConfig,
    feature_dim: int,
    reference_feature_dim: int,
    reference_feature_names: tuple[str, ...],
    reference_summary: Optional[Dict[str, Any]],
    epoch: int,
    metrics: Dict[str, Any],
) -> None:
    """Save one reusable model checkpoint."""

    path.parent.mkdir(parents=True, exist_ok=True)
    torch.save(
        {
            "epoch": epoch,
            "model_state_dict": model.state_dict(),
            "model_config": {
                "input_dim": feature_dim,
                "hidden_dim": config.hidden_dim,
                "dropout": config.dropout,
                "architecture": architecture_name(config.hidden_dim, reference_feature_dim),
                "reference_feature_dim": reference_feature_dim,
            },
            "normalize_embeddings": config.normalize_embeddings,
            "loss_name": config.loss_name,
            "reference_conditioning": {
                "enabled": reference_feature_dim > 0,
                "reference_bank_path": (
                    str(config.reference_bank_path) if config.reference_bank_path is not None else None
                ),
                "reference_top_k": config.reference_top_k,
                "reference_feature_dim": reference_feature_dim,
                "reference_feature_names": list(reference_feature_names),
                "reference_summary": reference_summary,
            },
            "training_config": config.to_serializable_dict(),
            "metrics": metrics,
        },
        path,
    )


def _set_random_seed(seed: int) -> None:
    """Set deterministic seeds where practical."""

    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)


def _format_optional_float(value: Optional[float]) -> str:
    """Format optional floats for log messages."""

    if value is None:
        return "n/a"
    return f"{value:.6f}"
