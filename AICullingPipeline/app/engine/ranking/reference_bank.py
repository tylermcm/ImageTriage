"""Week 6 reference-bank building and exemplar-similarity feature computation."""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np
from torch.utils.data import DataLoader
from tqdm.auto import tqdm

from app.config import ReferenceBankBuildConfig
from app.data.image_dataset import ImageDataset, collate_image_batch
from app.data.image_scanner import ImageRecord, scan_image_directory
from app.engine.ranking.inference import l2_normalize_embeddings
from app.models.dinov2_extractor import DINOv2EmbeddingExtractor
from app.storage.reference_bank import (
    REFERENCE_BUCKETS,
    ReferenceBankArtifacts,
    ReferenceImageArtifact,
    save_reference_bank_npz,
    save_reference_metadata_csv,
    save_reference_summary_json,
)
from app.utils.io_utils import save_json


LOGGER = logging.getLogger(__name__)
REFERENCE_FEATURE_STATS = (
    "mean_similarity",
    "max_similarity",
    "topk_mean_similarity",
    "bucket_present",
)


class ReferenceBankBuilder:
    """Build a saved reference bank from bucketed exemplar images."""

    def __init__(self, config: ReferenceBankBuildConfig) -> None:
        self.config = config

    def run(self) -> Dict[str, Path]:
        """Extract embeddings for the configured reference set and save artifacts."""

        self.config.output_dir.mkdir(parents=True, exist_ok=True)

        metadata_path = self.config.output_dir / self.config.metadata_filename
        bank_path = self.config.output_dir / self.config.bank_filename
        summary_path = self.config.output_dir / self.config.summary_filename
        resolved_config_path = self.config.output_dir / self.config.resolved_config_filename

        all_records, valid_records, bucket_scan_summary = _scan_reference_buckets(
            self.config.reference_dir,
            self.config.reference_buckets,
            self.config.supported_extensions,
            scan_workers=self.config.scan_workers,
        )
        if not all_records:
            raise RuntimeError(
                "No supported reference images were found. "
                f"Expected exemplar images inside subfolders of {self.config.reference_dir}."
            )

        extractor = DINOv2EmbeddingExtractor(
            self.config.model_name,
            device=self.config.device,
            image_size=self.config.image_size,
            fallback_model_name=self.config.fallback_model_name,
            allow_fallback=self.config.allow_model_fallback,
        )

        if not valid_records:
            raise RuntimeError(
                "No readable reference images were available after scanning. "
                "Fix unreadable files or add usable exemplars before building a bank."
            )

        embeddings = self._extract_embeddings(valid_records, extractor)
        reference_bank = _build_reference_bank_from_records(
            all_records=all_records,
            embeddings=embeddings,
            bucket_order=self.config.reference_buckets,
        )

        save_reference_metadata_csv(metadata_path, all_records)
        save_reference_bank_npz(bank_path, reference_bank)
        save_reference_summary_json(
            summary_path,
            {
                "reference_dir": str(self.config.reference_dir),
                "resolved_model_name": extractor.model_name,
                "feature_dim": reference_bank.feature_dim,
                "bucket_counts": reference_bank.bucket_counts(),
                "bucket_scan_summary": bucket_scan_summary,
                "total_reference_images": len(all_records),
                "embedded_reference_images": sum(
                    1 for record in all_records if record.embedding_index >= 0
                ),
            },
        )

        resolved_config = self.config.to_serializable_dict()
        resolved_config["resolved_model_name"] = extractor.model_name
        resolved_config["feature_dim"] = reference_bank.feature_dim
        save_json(resolved_config_path, resolved_config)

        return {
            "metadata": metadata_path,
            "reference_bank": bank_path,
            "summary": summary_path,
            "resolved_config": resolved_config_path,
        }

    def _extract_embeddings(
        self,
        valid_records: List[ReferenceImageArtifact],
        extractor: DINOv2EmbeddingExtractor,
    ) -> np.ndarray:
        """Run batched DINO inference over valid reference exemplars."""

        dataset = ImageDataset(valid_records, extractor.transform)
        dataloader = DataLoader(
            dataset,
            batch_size=self.config.batch_size,
            shuffle=False,
            num_workers=self.config.num_workers,
            pin_memory=extractor.device.type == "cuda",
            collate_fn=collate_image_batch,
        )

        embedding_batches: List[np.ndarray] = []
        next_embedding_index = 0

        for batch in tqdm(dataloader, desc="Extracting reference embeddings", unit="batch"):
            for failure in batch["failures"]:
                record = valid_records[failure["record_index"]]
                record.status = "inference_error"
                record.error = failure["error"]
                record.embedding_index = -1
                LOGGER.warning(
                    "Skipping reference image during inference %s: %s",
                    record.file_path,
                    failure["error"],
                )

            pixel_values = batch["pixel_values"]
            if pixel_values is None:
                continue

            batch_embeddings = extractor.encode_batch(pixel_values)
            record_indices: List[int] = batch["record_indices"]
            if batch_embeddings.shape[0] != len(record_indices):
                raise RuntimeError("Mismatch between reference batch size and returned embeddings.")

            for row_offset, record_index in enumerate(record_indices):
                record = valid_records[record_index]
                record.status = "embedded"
                record.error = ""
                record.embedding_index = next_embedding_index + row_offset

            next_embedding_index += batch_embeddings.shape[0]
            embedding_batches.append(batch_embeddings.numpy())

        if not embedding_batches:
            return np.empty((0, extractor.feature_dim), dtype=np.float32)

        return np.concatenate(embedding_batches, axis=0).astype(np.float32, copy=False)


def build_reference_bank(config: ReferenceBankBuildConfig) -> Dict[str, Path]:
    """Build a saved reference bank through the public engine API."""

    return ReferenceBankBuilder(config).run()


def build_reference_feature_matrix(
    embeddings: np.ndarray,
    reference_bank: ReferenceBankArtifacts,
    *,
    top_k: int,
) -> Tuple[np.ndarray, tuple[str, ...]]:
    """Compute fixed reference-similarity features for one or more candidate embeddings."""

    candidate_embeddings = np.asarray(embeddings, dtype=np.float32)
    if candidate_embeddings.ndim != 2:
        raise ValueError(
            f"Expected candidate embeddings with shape [N, D], received {tuple(candidate_embeddings.shape)}."
        )

    if top_k <= 0:
        raise ValueError("top_k must be greater than 0.")

    rows = candidate_embeddings.shape[0]
    bucket_order = reference_bank.bucket_order or REFERENCE_BUCKETS
    feature_names = tuple(
        f"{bucket}_{stat_name}"
        for bucket in bucket_order
        for stat_name in REFERENCE_FEATURE_STATS
    )
    if rows == 0:
        return np.empty((0, len(feature_names)), dtype=np.float32), feature_names

    normalized_candidates = l2_normalize_embeddings(candidate_embeddings)
    feature_blocks: List[np.ndarray] = []

    for bucket in bucket_order:
        bucket_embeddings = np.asarray(
            reference_bank.bucket_embeddings.get(bucket, np.empty((0, 0), dtype=np.float32)),
            dtype=np.float32,
        )
        if bucket_embeddings.size == 0:
            feature_blocks.extend(
                [
                    np.zeros((rows, 1), dtype=np.float32),
                    np.zeros((rows, 1), dtype=np.float32),
                    np.zeros((rows, 1), dtype=np.float32),
                    np.zeros((rows, 1), dtype=np.float32),
                ]
            )
            continue

        similarities = normalized_candidates @ bucket_embeddings.T
        max_similarity = np.max(similarities, axis=1, keepdims=True)
        mean_similarity = np.mean(similarities, axis=1, keepdims=True)
        top_k_count = min(top_k, similarities.shape[1])
        if top_k_count == similarities.shape[1]:
            topk_mean_similarity = mean_similarity
        else:
            topk_partition = np.partition(similarities, kth=similarities.shape[1] - top_k_count, axis=1)
            topk_values = topk_partition[:, -top_k_count:]
            topk_mean_similarity = np.mean(topk_values, axis=1, keepdims=True)
        bucket_present = np.ones((rows, 1), dtype=np.float32)
        feature_blocks.extend(
            [
                mean_similarity.astype(np.float32, copy=False),
                max_similarity.astype(np.float32, copy=False),
                topk_mean_similarity.astype(np.float32, copy=False),
                bucket_present,
            ]
        )

    return np.concatenate(feature_blocks, axis=1).astype(np.float32, copy=False), feature_names


def summarize_reference_bank(reference_bank: ReferenceBankArtifacts) -> Dict[str, int]:
    """Return a compact per-bucket exemplar count summary."""

    return reference_bank.bucket_counts()


def _scan_reference_buckets(
    reference_dir: Path,
    bucket_order: tuple[str, ...],
    supported_extensions: tuple[str, ...],
    *,
    scan_workers: int,
) -> Tuple[List[ReferenceImageArtifact], List[ReferenceImageArtifact], Dict[str, Dict[str, int]]]:
    """Scan each configured reference bucket and wrap records for embedding."""

    all_records: List[ReferenceImageArtifact] = []
    valid_records: List[ReferenceImageArtifact] = []
    bucket_scan_summary: Dict[str, Dict[str, int]] = {}

    for bucket in bucket_order:
        bucket_dir = reference_dir / bucket
        if not bucket_dir.exists() or not bucket_dir.is_dir():
            bucket_scan_summary[bucket] = {
                "supported_files": 0,
                "readable_files": 0,
                "skipped_files": 0,
            }
            LOGGER.info("Reference bucket %s is missing at %s; continuing.", bucket, bucket_dir)
            continue

        scanned_records, readable_records = scan_image_directory(
            bucket_dir,
            supported_extensions,
            scan_workers=scan_workers,
        )
        bucket_scan_summary[bucket] = {
            "supported_files": len(scanned_records),
            "readable_files": len(readable_records),
            "skipped_files": len(scanned_records) - len(readable_records),
        }
        wrapped_records = [_wrap_reference_record(bucket, record) for record in scanned_records]
        wrapped_valid_records = [record for record in wrapped_records if record.status == "ready"]
        all_records.extend(wrapped_records)
        valid_records.extend(wrapped_valid_records)

    return all_records, valid_records, bucket_scan_summary


def _wrap_reference_record(bucket: str, record: ImageRecord) -> ReferenceImageArtifact:
    """Convert one scanned image record into a reference-bank artifact row."""

    relative_path = f"{bucket}/{record.relative_path}".replace("\\", "/")
    return ReferenceImageArtifact(
        bucket=bucket,
        image_id=_build_reference_image_id(bucket, record.relative_path),
        embedding_index=-1,
        file_path=record.file_path,
        relative_path=relative_path,
        file_name=record.file_name,
        width=record.width,
        height=record.height,
        status=record.status,
        error=record.error,
    )


def _build_reference_bank_from_records(
    *,
    all_records: List[ReferenceImageArtifact],
    embeddings: np.ndarray,
    bucket_order: tuple[str, ...],
) -> ReferenceBankArtifacts:
    """Build a normalized in-memory reference bank from embedded exemplar rows."""

    embedded_rows = sorted(
        (record for record in all_records if record.embedding_index >= 0 and record.status == "embedded"),
        key=lambda record: int(record.embedding_index),
    )
    if len(embedded_rows) != embeddings.shape[0]:
        raise ValueError(
            "Mismatch between embedded reference records and extracted embeddings: "
            f"{len(embedded_rows)} != {embeddings.shape[0]}"
        )

    normalized_embeddings = l2_normalize_embeddings(embeddings)
    bucket_embeddings: Dict[str, np.ndarray] = {
        bucket: np.empty((0, embeddings.shape[1]), dtype=np.float32) for bucket in bucket_order
    }
    bucket_image_ids: Dict[str, tuple[str, ...]] = {bucket: tuple() for bucket in bucket_order}
    bucket_file_paths: Dict[str, tuple[str, ...]] = {bucket: tuple() for bucket in bucket_order}

    for bucket in bucket_order:
        members = [record for record in embedded_rows if record.bucket == bucket]
        if not members:
            continue
        bucket_embeddings[bucket] = np.stack(
            [normalized_embeddings[record.embedding_index] for record in members],
            axis=0,
        ).astype(np.float32, copy=False)
        bucket_image_ids[bucket] = tuple(record.image_id for record in members)
        bucket_file_paths[bucket] = tuple(record.file_path for record in members)

    return ReferenceBankArtifacts(
        bucket_order=bucket_order,
        bucket_embeddings=bucket_embeddings,
        bucket_image_ids=bucket_image_ids,
        bucket_file_paths=bucket_file_paths,
        ordered_images=embedded_rows,
        feature_dim=int(embeddings.shape[1]) if embeddings.ndim == 2 else 0,
    )


def _build_reference_image_id(bucket: str, relative_path: str) -> str:
    """Create a deterministic exemplar ID from bucket name and relative path."""

    normalized_relative_path = relative_path.replace("\\", "/")
    payload = f"{bucket}/{normalized_relative_path}"
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()
