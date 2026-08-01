"""End-to-end pipeline for image scanning and embedding extraction."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import time

import numpy as np
from torch.utils.data import DataLoader
from tqdm.auto import tqdm

from app.config import ExtractionConfig
from app.data.image_dataset import ImageDataset, collate_image_batch
from app.data.image_loading import resolve_inference_read_block_bytes
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

        output_paths = {
            "metadata": metadata_path,
            "embeddings": embeddings_path,
            "image_ids": image_ids_path,
            "resolved_config": resolved_config_path,
        }
        ready_file = self.config.include_paths_ready_file
        deferred_payload = _read_ready_payload(ready_file)
        if deferred_payload is not None and _reuse_existing_outputs(deferred_payload, output_paths):
            emit_metric("ai.script.extract.deferred_cache_reuse", model_loaded=False)
            return output_paths

        extractor = None
        if ready_file is not None:
            extractor = self._load_extractor()
            deferred_payload = deferred_payload or _wait_for_ready_payload(ready_file)
            if _reuse_existing_outputs(deferred_payload, output_paths):
                emit_metric("ai.script.extract.deferred_cache_reuse", model_loaded=True)
                return output_paths

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

        if extractor is None:
            extractor = self._load_extractor()

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

    def _load_extractor(self) -> DINOv2EmbeddingExtractor:
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
        return extractor

    def _extract_embeddings(
        self,
        valid_records: list[ImageRecord],
        extractor: DINOv2EmbeddingExtractor,
    ) -> np.ndarray:
        """Run batched inference and return the final embedding matrix."""

        collect_timings = metrics_enabled()
        decoder_read_block_bytes = resolve_inference_read_block_bytes()
        # Opt-in fusion: compute technical signals from the same decode and
        # pre-populate the signals-stage cache. Off by default (a scoring change
        # that must be validated by ranking first).
        fuse_technical = _fuse_technical_enabled()
        dataloader_setup_start = time.perf_counter()
        dataset = ImageDataset(
            valid_records,
            extractor.transform,
            collect_timings=collect_timings,
            target_short_edge=max(extractor.preprocessing.height, extractor.preprocessing.width),
            compute_technical=fuse_technical,
            technical_max_side=_fuse_technical_max_side(),
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
            decoder_read_block_bytes=decoder_read_block_bytes,
        )

        embedding_batches: list[np.ndarray] = []
        technical_entries: list = []
        next_embedding_index = 0
        batch_index = 0
        dataloader_wait_total_ms = 0.0
        encode_total_ms = 0.0
        numpy_total_ms = 0.0
        image_load_total_ms = 0.0
        transform_total_ms = 0.0
        transform_cpu_total_ms = 0.0
        technical_total_ms = 0.0
        technical_cpu_total_ms = 0.0
        sample_total_ms = 0.0
        sample_cpu_total_ms = 0.0
        io_totals: dict[str, object] = {}

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
                transform_cpu_total_ms += float(timings.get("transform_cpu_ms") or 0.0)
                technical_total_ms += float(timings.get("technical_ms") or 0.0)
                technical_cpu_total_ms += float(timings.get("technical_cpu_ms") or 0.0)
                sample_total_ms += float(timings.get("total_ms") or 0.0)
                sample_cpu_total_ms += float(timings.get("total_cpu_ms") or 0.0)
                io_summary = timings.get("io") if isinstance(timings.get("io"), dict) else {}
                if io_summary:
                    _merge_io_summary(io_totals, io_summary)
                    emit_metric(
                        "ai.script.extract.io_batch",
                        batch_index=batch_index,
                        processed=processed_count,
                        **_serializable_io_summary(io_summary),
                    )
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
                        transform_cpu_ms=float(timings.get("transform_cpu_ms") or 0.0),
                        technical_ms=float(timings.get("technical_ms") or 0.0),
                        technical_cpu_ms=float(timings.get("technical_cpu_ms") or 0.0),
                        sample_total_ms=float(timings.get("total_ms") or 0.0),
                        sample_cpu_ms=float(timings.get("total_cpu_ms") or 0.0),
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
                    transform_cpu_ms=float(timings.get("transform_cpu_ms") or 0.0),
                    technical_ms=float(timings.get("technical_ms") or 0.0),
                    technical_cpu_ms=float(timings.get("technical_cpu_ms") or 0.0),
                    sample_total_ms=float(timings.get("total_ms") or 0.0),
                    sample_cpu_ms=float(timings.get("total_cpu_ms") or 0.0),
                    sample_max_ms=float(timings.get("max_ms") or 0.0),
                    sample_max_file=str(timings.get("max_file") or ""),
                    encode_ms=encode_ms,
                    numpy_ms=numpy_ms,
                    tensor_shape=list(pixel_values.shape),
                    device=str(extractor.device),
                )
                technical_entries.extend(batch.get("technical") or [])
                progress.update(processed_count)
        finally:
            progress.close()

        if fuse_technical and technical_entries:
            try:
                from app.engine.signals.technical import populate_technical_cache

                written = populate_technical_cache(self.config.output_dir, technical_entries)
                emit_metric("ai.script.extract.technical_fused", written=written)
            except Exception as exc:  # never let the cache write fail extraction
                LOGGER.warning("Technical-signal fusion cache write failed: %s", exc)

        if not embedding_batches:
            if io_totals:
                emit_metric("ai.script.extract.io_summary", **_serializable_io_summary(io_totals))
            emit_metric(
                "ai.script.extract.inference_summary",
                batches=batch_index,
                embedded=0,
                dataloader_wait_ms=dataloader_wait_total_ms,
                image_load_ms=image_load_total_ms,
                transform_ms=transform_total_ms,
                transform_cpu_ms=transform_cpu_total_ms,
                technical_ms=technical_total_ms,
                technical_cpu_ms=technical_cpu_total_ms,
                sample_total_ms=sample_total_ms,
                sample_cpu_ms=sample_cpu_total_ms,
                encode_ms=encode_total_ms,
                numpy_ms=numpy_total_ms,
            )
            return np.empty((0, extractor.feature_dim), dtype=np.float32)

        concat_start = time.perf_counter()
        embeddings = np.concatenate(embedding_batches, axis=0).astype(np.float32, copy=False)
        if io_totals:
            emit_metric("ai.script.extract.io_summary", **_serializable_io_summary(io_totals))
        emit_metric(
            "ai.script.extract.inference_summary",
            duration_ms=now_ms(concat_start),
            batches=batch_index,
            embedded=int(embeddings.shape[0]),
            feature_dim=int(embeddings.shape[1]) if embeddings.ndim == 2 else 0,
            dataloader_wait_ms=dataloader_wait_total_ms,
            image_load_ms=image_load_total_ms,
            transform_ms=transform_total_ms,
            transform_cpu_ms=transform_cpu_total_ms,
            technical_ms=technical_total_ms,
            technical_cpu_ms=technical_cpu_total_ms,
            sample_total_ms=sample_total_ms,
            sample_cpu_ms=sample_cpu_total_ms,
            encode_ms=encode_total_ms,
            numpy_ms=numpy_total_ms,
        )
        return embeddings


def _fuse_technical_enabled() -> bool:
    return (os.environ.get("AICULLING_FUSE_TECHNICAL", "") or "").strip().casefold() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _merge_io_summary(target: dict[str, object], source: dict[str, object]) -> None:
    for key, value in source.items():
        if key == "worker_pids" and isinstance(value, list):
            worker_pids = target.setdefault(key, set())
            if isinstance(worker_pids, set):
                worker_pids.update(int(item) for item in value)
            continue
        if key in {"formats", "frame_counts"} and isinstance(value, dict):
            counts = target.setdefault(key, {})
            if isinstance(counts, dict):
                for label, count in value.items():
                    counts[str(label)] = int(counts.get(str(label), 0)) + int(count)
            continue
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            target[key] = float(target.get(key, 0.0)) + float(value)


def _serializable_io_summary(summary: dict[str, object]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in summary.items():
        if isinstance(value, set):
            result[key] = sorted(value)
        elif isinstance(value, dict):
            result[key] = dict(value)
        elif key in {"samples", "read_calls", "seek_calls"} or key.endswith(("_calls", "_bytes")):
            result[key] = int(value)
        else:
            result[key] = value
    return result


def _fuse_technical_max_side() -> int:
    raw = (os.environ.get("AICULLING_TECHNICAL_MAX_SIDE", "") or "").strip()
    try:
        return max(64, int(raw))
    except ValueError:
        return 768


def _embedded_records(records: list[ImageRecord]) -> list[ImageRecord]:
    """Return records that produced embeddings in embedding order."""

    return sorted(
        (record for record in records if record.embedding_index is not None),
        key=lambda record: int(record.embedding_index),
    )


def _read_ready_payload(path: Path | None) -> dict[str, object] | None:
    if path is None or not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _wait_for_ready_payload(path: Path) -> dict[str, object]:
    timeout_seconds = _deferred_include_timeout_seconds()
    wait_start = time.perf_counter()
    emit_metric(
        "ai.script.extract.include_wait_start",
        ready_file=path,
        timeout_seconds=timeout_seconds,
    )
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        payload = _read_ready_payload(path)
        if payload is not None:
            emit_metric(
                "ai.script.extract.include_ready",
                duration_ms=now_ms(wait_start),
                include_paths=int(payload.get("include_paths") or 0),
                duplicate_candidates=int(payload.get("duplicate_candidates") or 0),
                cache_hit=bool(payload.get("reuse_existing_outputs")),
                fallback=bool(payload.get("fallback")),
            )
            return payload
        time.sleep(0.05)
    raise TimeoutError(f"Timed out waiting for deferred image list: {path}")


def _reuse_existing_outputs(payload: dict[str, object], outputs: dict[str, Path]) -> bool:
    return bool(payload.get("reuse_existing_outputs")) and all(path.exists() for path in outputs.values())


def _deferred_include_timeout_seconds() -> float:
    raw = (os.environ.get("AICULLING_DEFERRED_INCLUDE_TIMEOUT_SECONDS", "") or "").strip()
    try:
        return max(5.0, float(raw))
    except ValueError:
        return 300.0


def _load_include_paths(path: Path | None) -> set[str] | None:
    """Load an optional newline-delimited include path list."""

    if path is None:
        return None
    try:
        values = {line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()}
    except OSError:
        return None
    return values or None
