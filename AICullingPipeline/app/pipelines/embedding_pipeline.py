"""End-to-end pipeline for image scanning and embedding extraction."""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
from torch.utils.data import DataLoader
from tqdm.auto import tqdm

from app.config import ExtractionConfig
from app.data.image_dataset import ImageDataset, collate_image_batch
from app.data.image_scanner import ImageRecord, scan_image_directory
from app.models.dinov2_extractor import DINOv2EmbeddingExtractor
from app.utils.io_utils import (
    save_json,
    save_metadata_csv,
    save_numpy_array,
    save_resolved_config,
)


LOGGER = logging.getLogger(__name__)


class EmbeddingExtractionPipeline:
    """Pipeline that produces reusable image embeddings and metadata artifacts."""

    def __init__(self, config: ExtractionConfig) -> None:
        self.config = config

    def run(self) -> dict[str, Path]:
        """Execute the full extraction workflow and return output artifact paths."""

        self.config.output_dir.mkdir(parents=True, exist_ok=True)

        metadata_path = self.config.output_dir / self.config.metadata_filename
        embeddings_path = self.config.output_dir / self.config.embeddings_filename
        image_ids_path = self.config.output_dir / self.config.image_ids_filename
        resolved_config_path = self.config.output_dir / "resolved_config.json"

        all_records, valid_records = scan_image_directory(
            self.config.input_dir,
            self.config.supported_extensions,
            scan_workers=self.config.scan_workers,
        )

        if not all_records:
            save_metadata_csv(metadata_path, [])
            save_json(image_ids_path, [])
            raise RuntimeError(
                f"No supported image files were found in {self.config.input_dir}."
            )

        extractor = DINOv2EmbeddingExtractor(
            self.config.model_name,
            device=self.config.device,
            image_size=self.config.image_size,
            fallback_model_name=self.config.fallback_model_name,
            allow_fallback=self.config.allow_model_fallback,
        )

        if not valid_records:
            empty_embeddings = np.empty((0, extractor.feature_dim), dtype=np.float32)
            save_numpy_array(embeddings_path, empty_embeddings)
            save_metadata_csv(metadata_path, all_records)
            save_json(image_ids_path, [])
            save_resolved_config(resolved_config_path, self.config, extractor.model_name)
            LOGGER.warning("No readable images were available for embedding extraction.")
            return {
                "metadata": metadata_path,
                "embeddings": embeddings_path,
                "image_ids": image_ids_path,
                "resolved_config": resolved_config_path,
            }

        embeddings = self._extract_embeddings(valid_records, extractor)

        save_numpy_array(embeddings_path, embeddings)
        save_metadata_csv(metadata_path, all_records)
        save_json(
            image_ids_path,
            [record.image_id for record in _embedded_records(valid_records)],
        )
        save_resolved_config(resolved_config_path, self.config, extractor.model_name)

        LOGGER.info(
            "Saved %s embeddings with dimension %s to %s.",
            embeddings.shape[0],
            embeddings.shape[1] if embeddings.ndim == 2 else 0,
            embeddings_path,
        )

        return {
            "metadata": metadata_path,
            "embeddings": embeddings_path,
            "image_ids": image_ids_path,
            "resolved_config": resolved_config_path,
        }

    def _extract_embeddings(
        self,
        valid_records: list[ImageRecord],
        extractor: DINOv2EmbeddingExtractor,
    ) -> np.ndarray:
        """Run batched inference and return the final embedding matrix."""

        dataset = ImageDataset(valid_records, extractor.transform)
        dataloader = DataLoader(
            dataset,
            batch_size=self.config.batch_size,
            shuffle=False,
            num_workers=self.config.num_workers,
            pin_memory=extractor.device.type == "cuda",
            collate_fn=collate_image_batch,
        )

        embedding_batches: list[np.ndarray] = []
        next_embedding_index = 0

        progress = tqdm(total=len(valid_records), desc="Extracting embeddings", unit="image")
        try:
            for batch in dataloader:
                for failure in batch["failures"]:
                    record = valid_records[failure["record_index"]]
                    record.status = "inference_error"
                    record.error = failure["error"]
                    record.embedding_index = None
                    LOGGER.warning(
                        "Skipping image during inference %s: %s",
                        record.file_path,
                        failure["error"],
                    )

                pixel_values = batch["pixel_values"]
                processed_count = len(batch["record_indices"]) + len(batch["failures"])
                if pixel_values is None:
                    progress.update(processed_count)
                    continue

                batch_embeddings = extractor.encode_batch(pixel_values)
                record_indices: list[int] = batch["record_indices"]
                if batch_embeddings.shape[0] != len(record_indices):
                    raise RuntimeError("Mismatch between batch size and returned embeddings.")

                for row_offset, record_index in enumerate(record_indices):
                    record = valid_records[record_index]
                    record.status = "embedded"
                    record.error = ""
                    record.embedding_index = next_embedding_index + row_offset

                next_embedding_index += batch_embeddings.shape[0]
                embedding_batches.append(batch_embeddings.numpy())
                progress.update(processed_count)
        finally:
            progress.close()

        if not embedding_batches:
            return np.empty((0, extractor.feature_dim), dtype=np.float32)

        return np.concatenate(embedding_batches, axis=0).astype(np.float32, copy=False)


def _embedded_records(records: list[ImageRecord]) -> list[ImageRecord]:
    """Return records that produced embeddings in embedding order."""

    return sorted(
        (record for record in records if record.embedding_index is not None),
        key=lambda record: int(record.embedding_index),
    )
