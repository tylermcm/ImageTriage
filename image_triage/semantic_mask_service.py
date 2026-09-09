from __future__ import annotations

"""Shared mask-worker transport primitives + the scene-mask warm task.

The persistent inference process is the unified MaskEngine host
(:mod:`image_triage.mask_engine_service`). This module keeps the small shared
pieces that the host service reuses — metric parsing, the transport error type,
and the semantic result dataclass — plus AI-runtime validation and the
OneFormer scene-mask warm task.
"""

import json
import time
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QRunnable

from .ai_model import resolve_segmentation_model_installation
from .ai_runtime_packages import resolve_ai_runtime_site_packages
from .ai_workflow import AI_METRIC_PREFIX, AIWorkflowRuntime, default_ai_workflow_runtime
from .perf import perf_logger


@dataclass(frozen=True)
class SemanticWorkerResult:
    device: str
    source_size: tuple[int, int]
    category_stats: dict[str, dict[str, object]]
    timings_ms: dict[str, float]


def _elapsed_ms(started: float) -> float:
    return (time.perf_counter() - started) * 1000.0


def _record_worker_metric(payload: dict[str, object]) -> None:
    logger = perf_logger()
    if not logger.enabled:
        return
    fields = dict(payload)
    event = str(fields.pop("event", "ai.mask.worker.metric"))
    duration = fields.pop("duration_ms", None)
    fields["source"] = "worker"
    if isinstance(duration, (int, float)):
        logger.duration(event, float(duration), **fields)
    else:
        logger.log(event, **fields)


def _parse_worker_metric(line: str) -> dict[str, object] | None:
    if not line.startswith(AI_METRIC_PREFIX):
        return None
    try:
        payload = json.loads(line.removeprefix(AI_METRIC_PREFIX))
    except (TypeError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


class _WorkerTransportError(RuntimeError):
    pass


def _resolve_semantic_runtime() -> tuple[AIWorkflowRuntime, tuple[Path, ...]]:
    runtime = default_ai_workflow_runtime()
    site_packages = resolve_ai_runtime_site_packages(device=runtime.device)
    if not site_packages:
        raise RuntimeError(
            "The AI runtime is unavailable. Install the PyTorch AI runtime first."
        )
    required_modules = ("torch", "transformers", "safetensors", "PIL", "numpy")
    missing = [
        name
        for name in required_modules
        if not any((site_dir / name).exists() for site_dir in site_packages)
    ]
    if missing:
        raise RuntimeError(
            "The installed AI runtime is missing scene-mask dependencies: "
            + ", ".join(missing)
            + ". Open Settings, run Set Up AI again, and reinstall the selected runtime "
            "to add editor masking support."
        )
    return runtime, site_packages


_RUNTIME_VALIDATED = False


def validate_semantic_runtime() -> None:
    # Resolving the runtime (env + filesystem existence checks) costs ~140 ms
    # and is stable within a session, so validate once. The host spawn path
    # re-resolves independently, so a genuinely broken runtime still surfaces.
    global _RUNTIME_VALIDATED
    if _RUNTIME_VALIDATED:
        return
    _resolve_semantic_runtime()
    _RUNTIME_VALIDATED = True


def reset_runtime_validation_cache() -> None:
    """Force the next ``validate_semantic_runtime`` to re-check (tests, or after
    the AI runtime is (un)installed / the device changes mid-session)."""
    global _RUNTIME_VALIDATED
    _RUNTIME_VALIDATED = False


class SemanticMaskWarmTask(QRunnable):
    """Warm the MaskEngine host's OneFormer engine without blocking the UI.

    A ``QRunnable`` so callers can schedule it on the shared thread pool exactly
    like ``SubjectMaskWarmTask``.
    """

    def __init__(self, stage: str) -> None:
        super().__init__()
        normalized = stage.strip().casefold()
        if normalized not in {"imports", "model"}:
            raise ValueError(f"Unknown OneFormer warm stage: {stage}")
        self.stage = normalized
        self.setAutoDelete(True)

    def run(self) -> None:
        started = time.perf_counter()
        logger = perf_logger()
        try:
            # No scene-mask model, nothing to warm for — don't spin up torch/CUDA
            # just because the editor opened; masking would prompt a download.
            installation = resolve_segmentation_model_installation()
            if not installation.is_installed:
                logger.duration(
                    "ai.mask.oneformer.warm.skipped",
                    _elapsed_ms(started),
                    stage=self.stage,
                    reason="model_not_installed",
                )
                return
            from .mask_engine_service import default_mask_engine_service

            engine_service = default_mask_engine_service()
            if self.stage == "imports":
                device = engine_service.warm_imports("semantic")
            else:
                device = engine_service.warm_model("semantic", installation.install_dir)
            logger.duration(
                "ai.mask.oneformer.warm",
                _elapsed_ms(started),
                stage=self.stage,
                device=device,
            )
        except Exception as exc:
            logger.duration(
                "ai.mask.oneformer.warm.failed",
                _elapsed_ms(started),
                stage=self.stage,
                error=str(exc),
            )


__all__ = [
    "SemanticMaskWarmTask",
    "SemanticWorkerResult",
    "reset_runtime_validation_cache",
    "validate_semantic_runtime",
]
