"""Optional zero-shot semantic classification sidecar for AI review."""

from __future__ import annotations

import csv
import json
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, UnidentifiedImageError
from tqdm import tqdm

from app.config import SemanticClassificationConfig
from app.data.image_loading import load_rgb_for_inference
from app.utils.perf_metrics import emit_metric, metrics_enabled, now_ms


@dataclass(frozen=True)
class SemanticClassificationRow:
    image_id: str
    file_path: str
    relative_path: str
    file_name: str
    primary_label: str
    primary_score: float
    secondary_labels: tuple[str, ...]
    secondary_scores: tuple[float, ...]
    status: str = "ready"
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "image_id": self.image_id,
            "file_path": self.file_path,
            "relative_path": self.relative_path,
            "file_name": self.file_name,
            "primary_label": self.primary_label,
            "primary_score": f"{self.primary_score:.6f}",
            "secondary_labels": "|".join(self.secondary_labels),
            "secondary_scores": "|".join(f"{score:.6f}" for score in self.secondary_scores),
            "status": self.status,
            "error": self.error,
        }


def classify_images_semantically(config: SemanticClassificationConfig) -> dict[str, Path]:
    """Run zero-shot semantic classification over the extraction metadata."""

    run_start = time.perf_counter()
    collect_metrics = metrics_enabled()
    emit_metric(
        "ai.script.semantic.start",
        artifacts_dir=config.artifacts_dir,
        output_dir=config.output_dir,
        batch_size=config.batch_size,
        requested_device=config.device,
        model_name=config.model_name,
        labels=len(config.labels),
        top_k=config.top_k,
    )
    metadata_start = time.perf_counter()
    rows = _load_metadata_rows(config.artifacts_dir / config.metadata_filename)
    emit_metric("ai.script.semantic.metadata", duration_ms=now_ms(metadata_start), rows=len(rows))
    config.output_dir.mkdir(parents=True, exist_ok=True)
    export_path = config.output_dir / config.semantic_export_filename
    summary_path = config.output_dir / config.semantic_summary_filename
    if not rows:
        write_start = time.perf_counter()
        _write_semantic_csv(export_path, [])
        _write_summary(summary_path, [], config)
        emit_metric("ai.script.semantic.write", duration_ms=now_ms(write_start), rows=0)
        emit_metric("ai.script.semantic.total", duration_ms=now_ms(run_start), rows=0)
        return {"semantic_export": export_path, "summary": summary_path}

    emit_metric("ai.script.semantic.dependencies_start")
    dependency_start = time.perf_counter()
    torch, CLIPModel, CLIPProcessor = _load_semantic_dependencies()
    emit_metric("ai.script.semantic.dependencies", duration_ms=now_ms(dependency_start))
    device = _resolve_device(config.device, torch)
    emit_metric(
        "ai.script.semantic.model_load_start",
        model_name=config.model_name,
        requested_device=config.device,
        device=device,
    )
    model_start = time.perf_counter()
    processor = CLIPProcessor.from_pretrained(config.model_name)
    model = CLIPModel.from_pretrained(config.model_name)
    model.to(device)
    model.eval()
    target_short_edge = _processor_target_short_edge(processor)
    emit_metric(
        "ai.script.semantic.model_load",
        duration_ms=now_ms(model_start),
        model_name=config.model_name,
        device=device,
        target_short_edge=target_short_edge,
    )

    labels = tuple(label.strip() for label in config.labels if label.strip())
    prompts = tuple(_label_prompt(label) for label in labels)
    results: list[SemanticClassificationRow] = []
    batch_index = 0
    image_open_total_ms = 0.0
    processor_total_ms = 0.0
    transfer_total_ms = 0.0
    inference_total_ms = 0.0
    topk_total_ms = 0.0

    for batch in tqdm(_batched(rows, config.batch_size), total=_batch_count(len(rows), config.batch_size), desc="Classifying images", unit="batch"):
        batch_index += 1
        opened_images: list[Image.Image] = []
        opened_rows: list[dict[str, str]] = []
        open_start = time.perf_counter()
        slowest_open_ms = 0.0
        slowest_open_file = ""
        for row in batch:
            path = Path(row.get("file_path") or "")
            try:
                image_start = time.perf_counter()
                image = load_rgb_for_inference(path, target_short_edge=target_short_edge)
                image_ms = now_ms(image_start)
                if image_ms > slowest_open_ms:
                    slowest_open_ms = image_ms
                    slowest_open_file = path.name
            except (OSError, ValueError, UnidentifiedImageError) as exc:
                results.append(_failed_row(row, str(exc)))
                continue
            opened_images.append(image)
            opened_rows.append(row)
        image_open_ms = now_ms(open_start)
        image_open_total_ms += image_open_ms

        if not opened_images:
            emit_metric(
                "ai.script.semantic.batch",
                batch_index=batch_index,
                rows=len(batch),
                opened=0,
                failed=len(batch),
                image_open_ms=image_open_ms,
                slowest_open_ms=slowest_open_ms,
                slowest_open_file=slowest_open_file,
                processor_ms=0.0,
                transfer_ms=0.0,
                inference_ms=0.0,
                topk_ms=0.0,
            )
            continue

        try:
            processor_start = time.perf_counter()
            inputs = processor(text=list(prompts), images=opened_images, return_tensors="pt", padding=True)
            processor_ms = now_ms(processor_start)
            processor_total_ms += processor_ms
            transfer_start = time.perf_counter()
            inputs = {key: value.to(device) if hasattr(value, "to") else value for key, value in inputs.items()}
            if collect_metrics:
                _synchronize_if_cuda(torch, device)
            transfer_ms = now_ms(transfer_start)
            transfer_total_ms += transfer_ms
            with torch.no_grad():
                if collect_metrics:
                    _synchronize_if_cuda(torch, device)
                inference_start = time.perf_counter()
                outputs = model(**inputs)
                if collect_metrics:
                    _synchronize_if_cuda(torch, device)
                inference_ms = now_ms(inference_start)
                inference_total_ms += inference_ms
                probabilities = outputs.logits_per_image.softmax(dim=1).detach().cpu()
        finally:
            for image in opened_images:
                image.close()

        topk_start = time.perf_counter()
        top_k = min(config.top_k, len(labels))
        top_scores, top_indexes = probabilities.topk(top_k, dim=1)
        for row, score_values, index_values in zip(opened_rows, top_scores.tolist(), top_indexes.tolist()):
            primary_index = int(index_values[0])
            secondary_indexes = tuple(int(index) for index in index_values[1:])
            results.append(
                SemanticClassificationRow(
                    image_id=row.get("image_id", ""),
                    file_path=row.get("file_path", ""),
                    relative_path=row.get("relative_path", ""),
                    file_name=row.get("file_name", Path(row.get("file_path", "")).name),
                    primary_label=labels[primary_index],
                    primary_score=float(score_values[0]),
                    secondary_labels=tuple(labels[index] for index in secondary_indexes),
                    secondary_scores=tuple(float(score) for score in score_values[1:]),
                )
            )
        topk_ms = now_ms(topk_start)
        topk_total_ms += topk_ms
        emit_metric(
            "ai.script.semantic.batch",
            batch_index=batch_index,
            rows=len(batch),
            opened=len(opened_images),
            failed=len(batch) - len(opened_images),
            image_open_ms=image_open_ms,
            slowest_open_ms=slowest_open_ms,
            slowest_open_file=slowest_open_file,
            processor_ms=processor_ms,
            transfer_ms=transfer_ms,
            inference_ms=inference_ms,
            topk_ms=topk_ms,
            device=device,
        )

    write_start = time.perf_counter()
    _write_semantic_csv(export_path, results)
    _write_summary(summary_path, results, config)
    emit_metric(
        "ai.script.semantic.write",
        duration_ms=now_ms(write_start),
        rows=len(results),
        export_bytes=export_path.stat().st_size if export_path.exists() else 0,
        summary_bytes=summary_path.stat().st_size if summary_path.exists() else 0,
    )
    ready_rows = [row for row in results if row.status == "ready"]
    emit_metric(
        "ai.script.semantic.summary",
        batches=batch_index,
        rows=len(rows),
        classified=len(ready_rows),
        failed=len(results) - len(ready_rows),
        image_open_ms=image_open_total_ms,
        processor_ms=processor_total_ms,
        transfer_ms=transfer_total_ms,
        inference_ms=inference_total_ms,
        topk_ms=topk_total_ms,
    )
    emit_metric("ai.script.semantic.total", duration_ms=now_ms(run_start), rows=len(rows))
    return {"semantic_export": export_path, "summary": summary_path}


def _load_metadata_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        raise FileNotFoundError(f"semantic metadata source not found: {path}")
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        return [dict(row) for row in reader]


def _load_semantic_dependencies():
    try:
        import torch
        from transformers import CLIPModel, CLIPProcessor
    except ImportError as exc:
        raise RuntimeError(
            "Semantic sidecar requires torch and transformers. Install the AI runtime packages first."
        ) from exc
    return torch, CLIPModel, CLIPProcessor


def _resolve_device(requested: str, torch) -> str:
    normalized = (requested or "auto").strip().lower()
    if normalized == "auto":
        return "cuda" if torch.cuda.is_available() else "cpu"
    if normalized.startswith("cuda") and not torch.cuda.is_available():
        return "cpu"
    return normalized


def _synchronize_if_cuda(torch, device: str) -> None:
    if str(device).startswith("cuda") and torch.cuda.is_available():
        torch.cuda.synchronize(device)


def _processor_target_short_edge(processor: Any) -> int:
    image_processor = getattr(processor, "image_processor", None)
    size = getattr(image_processor, "size", None)
    if isinstance(size, dict):
        candidates = [
            size.get("shortest_edge"),
            size.get("height"),
            size.get("width"),
        ]
        for value in candidates:
            try:
                parsed = int(value)
            except (TypeError, ValueError):
                continue
            if parsed > 0:
                return parsed
    try:
        parsed = int(size)
    except (TypeError, ValueError):
        return 224
    return parsed if parsed > 0 else 224


def _label_prompt(label: str) -> str:
    cleaned = " ".join(label.split())
    if cleaned.startswith("a ") or cleaned.startswith("an "):
        return f"a photo of {cleaned}"
    return f"a photo of a {cleaned}"


def _batched(rows: list[dict[str, str]], batch_size: int):
    for index in range(0, len(rows), batch_size):
        yield rows[index:index + batch_size]


def _batch_count(item_count: int, batch_size: int) -> int:
    return (item_count + batch_size - 1) // batch_size


def _failed_row(row: dict[str, str], error: str) -> SemanticClassificationRow:
    return SemanticClassificationRow(
        image_id=row.get("image_id", ""),
        file_path=row.get("file_path", ""),
        relative_path=row.get("relative_path", ""),
        file_name=row.get("file_name", Path(row.get("file_path", "")).name),
        primary_label="",
        primary_score=0.0,
        secondary_labels=(),
        secondary_scores=(),
        status="failed",
        error=error[:240],
    )


def _write_semantic_csv(path: Path, rows: list[SemanticClassificationRow]) -> None:
    fieldnames = [
        "image_id",
        "file_path",
        "relative_path",
        "file_name",
        "primary_label",
        "primary_score",
        "secondary_labels",
        "secondary_scores",
        "status",
        "error",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row.to_dict())


def _write_summary(path: Path, rows: list[SemanticClassificationRow], config: SemanticClassificationConfig) -> None:
    ready_rows = [row for row in rows if row.status == "ready"]
    counts = Counter(row.primary_label for row in ready_rows if row.primary_label)
    payload = {
        "model_name": config.model_name,
        "total_images": len(rows),
        "classified_images": len(ready_rows),
        "failed_images": len(rows) - len(ready_rows),
        "labels": list(config.labels),
        "primary_label_counts": dict(sorted(counts.items(), key=lambda item: (-item[1], item[0]))),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
