"""End-to-end pipeline for image scanning and embedding extraction."""

from __future__ import annotations

import logging
from pathlib import Path
import time

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
from app.utils.perf_metrics import emit_metric, metrics_enabled, now_ms


LOGGER = logging.getLogger(__name__)


class EmbeddingExtractionPipeline:
    """Pipeline that produces reusable image embeddings and metadata artifacts."""

    def __init__(self, config: ExtractionConfig) -> None:
        self.config = config

    def run(self) -> dict[str, Path]:
        """Execute the full extraction workflow and return output artifact paths."""

        run_start = time.perf_counter()
        self.config.output_dir.mkdir(parents=True, exist_ok=True)
        emit_metric(
            "ai.script.extract.start",
            input_dir=self.config.input_dir,
            output_dir=self.config.output_dir,
            batch_size=self.config.batch_size,
            num_workers=self.config.num_workers,
            scan_workers=self.config.scan_workers,
            requested_device=self.config.device,
            image_size=self.config.image_size or 0,
        )

        metadata_path = self.config.output_dir / self.config.metadata_filename
        embeddings_path = self.config.output_dir / self.config.embeddings_filename
        image_ids_path = self.config.output_dir / self.config.image_ids_filename
        resolved_config_path = self.config.output_dir / "resolved_config.json"

        scan_start = time.perf_counter()
        include_paths = _load_include_paths(self.config.include_paths_file)
        all_records, valid_records = scan_image_directory(
            self.config.input_dir,
            self.config.supported_extensions,
            scan_workers=self.config.scan_workers,
            include_paths=include_paths,
        )
        emit_metric(
            "ai.script.extract.scan",
            duration_ms=now_ms(scan_start),
            total_records=len(all_records),
            valid_records=len(valid_records),
            skipped_records=len(all_records) - len(valid_records),
            include_paths=len(include_paths) if include_paths is not None else 0,
            supported_extensions=list(self.config.supported_extensions),
        )

        if not all_records:
            save_start = time.perf_counter()
            save_metadata_csv(metadata_path, [])
            save_json(image_ids_path, [])
            emit_metric("ai.script.extract.empty_save", duration_ms=now_ms(save_start))
            raise RuntimeError(
                f"No supported image files were found in {self.config.input_dir}."
            )

        emit_metric(
            "ai.script.extract.model_load_start",
            requested_model=self.config.model_name,
            requested_device=self.config.device,
        )
        model_start = time.perf_counter()
        extractor = DINOv2EmbeddingExtractor(
            self.config.model_name,
            device=self.config.device,
            image_size=self.config.image_size,
            fallback_model_name=self.config.fallback_model_name,
            allow_fallback=self.config.allow_model_fallback,
        )
        emit_metric(
            "ai.script.extract.model_load",
            duration_ms=now_ms(model_start),
            model_name=extractor.model_name,
            requested_model=self.config.model_name,
            backend=getattr(extractor, "backend", ""),
            device=str(extractor.device),
            feature_dim=extractor.feature_dim,
            input_height=extractor.preprocessing.height,
            input_width=extractor.preprocessing.width,
        )

        if not valid_records:
            empty_embeddings = np.empty((0, extractor.feature_dim), dtype=np.float32)
            save_start = time.perf_counter()
            save_numpy_array(embeddings_path, empty_embeddings)
            save_metadata_csv(metadata_path, all_records)
            save_json(image_ids_path, [])
            save_resolved_config(resolved_config_path, self.config, extractor.model_name)
            emit_metric("ai.script.extract.save", duration_ms=now_ms(save_start), embeddings=0)
            LOGGER.warning("No readable images were available for embedding extraction.")
            return {
                "metadata": metadata_path,
                "embeddings": embeddings_path,
                "image_ids": image_ids_path,
                "resolved_config": resolved_config_path,
            }

        embeddings = self._extract_embeddings(valid_records, extractor)

        save_start = time.perf_counter()
        save_numpy_array(embeddings_path, embeddings)
        save_metadata_csv(metadata_path, all_records)
        save_json(
            image_ids_path,
            [record.image_id for record in _embedded_records(valid_records)],
        )
        save_resolved_config(resolved_config_path, self.config, extractor.model_name)
        emit_metric(
            "ai.script.extract.save",
            duration_ms=now_ms(save_start),
            embeddings=int(embeddings.shape[0]) if embeddings.ndim == 2 else 0,
            feature_dim=int(embeddings.shape[1]) if embeddings.ndim == 2 else 0,
            metadata_bytes=metadata_path.stat().st_size if metadata_path.exists() else 0,
            embeddings_bytes=embeddings_path.stat().st_size if embeddings_path.exists() else 0,
        )

        LOGGER.info(
            "Saved %s embeddings with dimension %s to %s.",
            embeddings.shape[0],
            embeddings.shape[1] if embeddings.ndim == 2 else 0,
            embeddings_path,
        )

        outputs = {
            "metadata": metadata_path,
            "embeddings": embeddings_path,
            "image_ids": image_ids_path,
            "resolved_config": resolved_config_path,
        }
        emit_metric("ai.script.extract.total", duration_ms=now_ms(run_start))
        return outputs

    def _extract_embeddings(
        self,
        valid_records: list[ImageRecord],
        extractor: DINOv2EmbeddingExtractor,
    ) -> np.ndarray:
        """Run batched inference and return the final embedding matrix."""

        collect_timings = metrics_enabled()
        dataloader_setup_start = time.perf_counter()
        dataset = ImageDataset(
            valid_records,
            extractor.transform,
            collect_timings=collect_timings,
            target_short_edge=max(extractor.preprocessing.height, extractor.preprocessing.width),
        )
        loader_kwargs = dict(
            dataset=dataset,
            batch_size=self.config.batch_size,
            shuffle=False,
            num_workers=self.config.num_workers,
            pin_memory=extractor.device.type == "cuda",
            collate_fn=collate_image_batch,
        )
        if self.config.num_workers > 0:
            loader_kwargs["persistent_workers"] = True
            loader_kwargs["prefetch_factor"] = 2
        dataloader = DataLoader(**loader_kwargs)
        emit_metric(
            "ai.script.extract.dataloader",
            duration_ms=now_ms(dataloader_setup_start),
            records=len(valid_records),
            batch_size=self.config.batch_size,
            num_workers=self.config.num_workers,
            pin_memory=extractor.device.type == "cuda",
            persistent_workers=self.config.num_workers > 0,
            prefetch_factor=2 if self.config.num_workers > 0 else 0,
        )

        embedding_batches: list[np.ndarray] = []
        next_embedding_index = 0
        batch_index = 0
        dataloader_wait_total_ms = 0.0
        encode_total_ms = 0.0
        numpy_total_ms = 0.0
        image_load_total_ms = 0.0
        transform_total_ms = 0.0
        sample_total_ms = 0.0

        progress = tqdm(total=len(valid_records), desc="Extracting embeddings", unit="image")
        try:
            iterator_start = time.perf_counter()
            dataloader_iter = iter(dataloader)
            iterator_ms = now_ms(iterator_start)
            emit_metric(
                "ai.script.extract.iterator",
                duration_ms=iterator_ms,
                records=len(valid_records),
                batch_size=self.config.batch_size,
                num_workers=self.config.num_workers,
                pin_memory=extractor.device.type == "cuda",
                persistent_workers=self.config.num_workers > 0,
            )
            first_batch_start = time.perf_counter()
            first_batch_pending = True
            while True:
                wait_start = time.perf_counter()
                try:
                    batch = next(dataloader_iter)
                except StopIteration:
                    break
                dataloader_wait_ms = now_ms(wait_start)
                if first_batch_pending:
                    emit_metric(
                        "ai.script.extract.first_batch_ready",
                        duration_ms=now_ms(first_batch_start),
                        iterator_ms=iterator_ms,
                        next_wait_ms=dataloader_wait_ms,
                        records=len(valid_records),
                        batch_size=self.config.batch_size,
                        num_workers=self.config.num_workers,
                    )
                    first_batch_pending = False
                dataloader_wait_total_ms += dataloader_wait_ms
                batch_index += 1
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
                timings = batch.get("timings") or {}
                image_load_total_ms += float(timings.get("load_ms") or 0.0)
                transform_total_ms += float(timings.get("transform_ms") or 0.0)
                sample_total_ms += float(timings.get("total_ms") or 0.0)
                if pixel_values is None:
                    emit_metric(
                        "ai.script.extract.batch",
                        batch_index=batch_index,
                        processed=processed_count,
                        embedded=0,
                        failures=len(batch["failures"]),
                        dataloader_wait_ms=dataloader_wait_ms,
                        image_load_ms=float(timings.get("load_ms") or 0.0),
                        transform_ms=float(timings.get("transform_ms") or 0.0),
                        sample_total_ms=float(timings.get("total_ms") or 0.0),
                        sample_max_ms=float(timings.get("max_ms") or 0.0),
                        sample_max_file=str(timings.get("max_file") or ""),
                        encode_ms=0.0,
                        numpy_ms=0.0,
                        tensor_shape=[],
                    )
                    progress.update(processed_count)
                    continue

                encode_start = time.perf_counter()
                batch_embeddings = extractor.encode_batch(pixel_values)
                encode_ms = now_ms(encode_start)
                encode_total_ms += encode_ms
                record_indices: list[int] = batch["record_indices"]
                if batch_embeddings.shape[0] != len(record_indices):
                    raise RuntimeError("Mismatch between batch size and returned embeddings.")

                for row_offset, record_index in enumerate(record_indices):
                    record = valid_records[record_index]
                    record.status = "embedded"
                    record.error = ""
                    record.embedding_index = next_embedding_index + row_offset

                next_embedding_index += batch_embeddings.shape[0]
                numpy_start = time.perf_counter()
                embedding_batches.append(batch_embeddings.numpy())
                numpy_ms = now_ms(numpy_start)
                numpy_total_ms += numpy_ms
                emit_metric(
                    "ai.script.extract.batch",
                    batch_index=batch_index,
                    processed=processed_count,
                    embedded=int(batch_embeddings.shape[0]),
                    failures=len(batch["failures"]),
                    dataloader_wait_ms=dataloader_wait_ms,
                    image_load_ms=float(timings.get("load_ms") or 0.0),
                    transform_ms=float(timings.get("transform_ms") or 0.0),
                    sample_total_ms=float(timings.get("total_ms") or 0.0),
                    sample_max_ms=float(timings.get("max_ms") or 0.0),
                    sample_max_file=str(timings.get("max_file") or ""),
                    encode_ms=encode_ms,
                    numpy_ms=numpy_ms,
                    tensor_shape=list(pixel_values.shape),
                    device=str(extractor.device),
                )
                progress.update(processed_count)
        finally:
            progress.close()

        if not embedding_batches:
            emit_metric(
                "ai.script.extract.inference_summary",
                batches=batch_index,
                embedded=0,
                dataloader_wait_ms=dataloader_wait_total_ms,
                image_load_ms=image_load_total_ms,
                transform_ms=transform_total_ms,
                sample_total_ms=sample_total_ms,
                encode_ms=encode_total_ms,
                numpy_ms=numpy_total_ms,
            )
            return np.empty((0, extractor.feature_dim), dtype=np.float32)

        concat_start = time.perf_counter()
        embeddings = np.concatenate(embedding_batches, axis=0).astype(np.float32, copy=False)
        emit_metric(
            "ai.script.extract.inference_summary",
            duration_ms=now_ms(concat_start),
            batches=batch_index,
            embedded=int(embeddings.shape[0]),
            feature_dim=int(embeddings.shape[1]) if embeddings.ndim == 2 else 0,
            dataloader_wait_ms=dataloader_wait_total_ms,
            image_load_ms=image_load_total_ms,
            transform_ms=transform_total_ms,
            sample_total_ms=sample_total_ms,
            encode_ms=encode_total_ms,
            numpy_ms=numpy_total_ms,
        )
        return embeddings


def _embedded_records(records: list[ImageRecord]) -> list[ImageRecord]:
    """Return records that produced embeddings in embedding order."""

    return sorted(
        (record for record in records if record.embedding_index is not None),
        key=lambda record: int(record.embedding_index),
    )


def _load_include_paths(path: Path | None) -> set[str] | None:
    """Load an optional newline-delimited include path list."""

    if path is None:
        return None
    try:
        values = {line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()}
    except OSError:
        return None
    return values or None
