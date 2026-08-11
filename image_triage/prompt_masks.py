from __future__ import annotations

"""Promptable click-to-select masks (SAM 2.1) for the popout editor.

A click on the photo becomes a point prompt; the MaskEngine host's ``prompt``
engine returns a mask isolating just that object/person — including one of
several *touching* people, which BiRefNet (one merged matte) and OneFormer (one
``people`` category) structurally cannot separate.

Interactive: the source preview is embedded once (keyed by file identity), then
each click reuses that embedding. Points are passed normalized (0..1) and mapped
to the preview here, so callers never worry about preview dimensions.
"""

import hashlib
import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np
from PIL import Image, ImageFilter
from PySide6.QtCore import QObject, QRunnable, QSize, Signal
from PySide6.QtGui import QImage

from .ai_model import (
    AIModelInstallation,
    DEFAULT_SAM_MODEL_REPO_ID,
    DEFAULT_SAM_MODEL_REVISION,
    download_birefnet_model,
    download_sam_model,
    resolve_birefnet_model_installation,
    resolve_sam_model_installation,
)
from .imaging import load_image_for_display
from .mask_engine_service import default_mask_engine_service
from .perf import perf_logger
from .semantic_mask_service import validate_semantic_runtime

PROMPT_MASK_PREVIEW_EDGE = 1600
PROMPT_MASK_MODEL_ID = DEFAULT_SAM_MODEL_REPO_ID
PROMPT_MASK_MODEL_VERSION = DEFAULT_SAM_MODEL_REVISION
PROMPT_MASK_MINIMUM_AREA = 0.0005

ProgressCallback = Callable[[str], None]


@dataclass(frozen=True)
class PromptMaskResult:
    source_path: Path
    source_size: tuple[int, int]        # preview dimensions the mask lives in
    mask_path: Path
    bounds: tuple[int, int, int, int]
    coverage: float
    model_id: str
    model_version: str
    weights_hash: str


def default_prompt_mask_cache_root() -> Path:
    if os.name == "nt":
        local_appdata = os.environ.get("LOCALAPPDATA")
        base = Path(local_appdata) if local_appdata else Path.home() / "AppData" / "Local"
    else:
        xdg_cache = os.environ.get("XDG_CACHE_HOME")
        base = Path(xdg_cache) if xdg_cache else Path.home() / ".cache"
    return base / "image_triage_ai_cache" / "prompt_masks"


def _progress(callback: ProgressCallback | None, message: str) -> None:
    if callback is not None:
        callback(message)


def _decode_rgb_preview(path: Path, long_edge: int) -> np.ndarray:
    image, error = load_image_for_display(str(path), QSize(long_edge, long_edge), prefer_embedded=True)
    if image.isNull():
        raise RuntimeError(error or "Could not decode image.")
    converted = image.convertToFormat(QImage.Format.Format_RGB888)
    width, height, stride = converted.width(), converted.height(), converted.bytesPerLine()
    buffer = np.frombuffer(converted.bits(), dtype=np.uint8, count=height * stride)
    return buffer.reshape(height, stride)[:, : width * 3].reshape(height, width, 3).copy()


def _source_cache_key(source: Path, size: int, mtime_ns: int) -> str:
    identity = "\0".join((os.path.normcase(str(source)), str(size), str(mtime_ns), PROMPT_MASK_MODEL_VERSION))
    return hashlib.sha256(identity.encode("utf-8", errors="surrogatepass")).hexdigest()[:24]


PROMPT_MASK_REFINE_DILATION = 25   # px halo kept around SAM's mask for the matte


def _refine_with_birefnet(
    preview_rgb: np.ndarray,
    sam_mask: np.ndarray,
    cache_dir: Path,
    progress_callback: ProgressCallback | None,
) -> np.ndarray | None:
    """Polish a SAM mask's edges with BiRefNet on a suppressed crop.

    SAM reliably picks *which* object; BiRefNet gives matte-quality edges. To
    stop BiRefNet re-merging a touching neighbour, everything outside a dilated
    copy of SAM's mask is greyed out before matting, and the result is clamped
    back to that dilated region. Reuses the host's BiRefNet engine — no new
    model, no extra process."""
    height, width = sam_mask.shape[:2]
    ys, xs = np.where(sam_mask)
    if len(xs) == 0:
        return None
    pad = 40
    x0, x1 = max(0, int(xs.min()) - pad), min(width, int(xs.max()) + pad + 1)
    y0, y1 = max(0, int(ys.min()) - pad), min(height, int(ys.max()) + pad + 1)
    dilated = np.asarray(
        Image.fromarray((sam_mask * 255).astype(np.uint8), mode="L").filter(
            ImageFilter.MaxFilter(PROMPT_MASK_REFINE_DILATION)
        ),
        dtype=np.uint8,
    ) > 0
    crop = preview_rgb[y0:y1, x0:x1]
    dilated_crop = dilated[y0:y1, x0:x1]
    suppressed = np.where(dilated_crop[..., None], crop, 128).astype(np.uint8)

    installation = resolve_birefnet_model_installation()
    if not installation.is_installed:
        _progress(progress_callback, "Downloading edge model...")
        download_birefnet_model(installation)

    _progress(progress_callback, "Refining edges...")
    suppressed_path = cache_dir / "refine-input.png"
    matte_path = cache_dir / "refine-matte.png"
    Image.fromarray(suppressed, mode="RGB").save(suppressed_path)
    try:
        default_mask_engine_service().infer_subject(
            model_dir=installation.install_dir,
            input_path=suppressed_path,
            output_path=matte_path,
            components_dir=cache_dir / "refine-components",
            progress_callback=progress_callback,
        )
        matte = np.asarray(Image.open(matte_path).convert("L"), dtype=np.float32) / 255.0
    finally:
        suppressed_path.unlink(missing_ok=True)
    matte = matte * dilated_crop.astype(np.float32)  # never exceed SAM's region
    refined = np.zeros((height, width), dtype=np.float32)
    refined[y0:y1, x0:x1] = matte
    return refined


def ensure_prompt_mask(
    source_path: str | Path,
    *,
    points_norm: list[tuple[float, float]],
    labels: list[int] | None = None,
    refine: bool = False,
    installation: AIModelInstallation | None = None,
    cache_root: str | Path | None = None,
    progress_callback: ProgressCallback | None = None,
) -> PromptMaskResult:
    source = Path(source_path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    if not points_norm:
        raise ValueError("At least one point is required.")

    model_installation = installation or resolve_sam_model_installation()
    if not model_installation.is_installed:
        _progress(progress_callback, "Downloading selection model...")

        def download_progress(filename: str, current: int, total: int) -> None:
            name = Path(filename).name
            if total > 0:
                _progress(
                    progress_callback,
                    f"Downloading {name}: {current / (1024 * 1024):.1f} / {total / (1024 * 1024):.1f} MB",
                )
            else:
                _progress(progress_callback, f"Downloading {name}...")

        download_sam_model(model_installation, progress_callback=download_progress)

    validate_semantic_runtime()
    stat = source.stat()
    cache_key = _source_cache_key(source, stat.st_size, stat.st_mtime_ns)
    cache_dir = Path(cache_root or default_prompt_mask_cache_root()) / cache_key
    cache_dir.mkdir(parents=True, exist_ok=True)
    preview_path = cache_dir / "preview.png"

    # Decode + write the preview once per image; clicks after the first reuse it
    # (the host also keeps the embedding resident keyed by ``image_key``).
    if not preview_path.is_file():
        _progress(progress_callback, "Preparing image...")
        rgb = _decode_rgb_preview(source, PROMPT_MASK_PREVIEW_EDGE)
        Image.fromarray(rgb, mode="RGB").save(preview_path)
        preview_width, preview_height = int(rgb.shape[1]), int(rgb.shape[0])
    else:
        with Image.open(preview_path) as loaded:
            preview_width, preview_height = loaded.size

    points = [
        (max(0.0, min(1.0, float(nx))) * preview_width, max(0.0, min(1.0, float(ny))) * preview_height)
        for (nx, ny) in points_norm
    ]
    resolved_labels = list(labels) if labels and len(labels) == len(points) else [1] * len(points)

    output_path = cache_dir / f"selection-{uuid.uuid4().hex[:10]}.png"
    _progress(progress_callback, "Selecting...")
    started = time.perf_counter()
    result = default_mask_engine_service().infer_prompt(
        model_dir=model_installation.install_dir,
        input_path=preview_path,
        output_path=output_path,
        points=points,
        labels=resolved_labels,
        image_key=cache_key,
        minimum_area=PROMPT_MASK_MINIMUM_AREA,
        progress_callback=progress_callback,
    )
    perf_logger().duration(
        "ai.mask.prompt.total",
        (time.perf_counter() - started) * 1000.0,
        device=str(result.get("device") or "unknown"),
        coverage=float(result.get("coverage") or 0.0),
    )
    raw_bounds = result.get("bounds")
    bounds = tuple(int(v) for v in raw_bounds) if isinstance(raw_bounds, list) and len(raw_bounds) == 4 else (0, 0, 0, 0)
    coverage = float(result.get("coverage") or 0.0)

    if refine and output_path.is_file():
        # Polish the SAM selection's edges with BiRefNet (best on people/animals).
        refine_started = time.perf_counter()
        try:
            sam_mask = np.asarray(Image.open(output_path).convert("L"), dtype=np.uint8) > 127
            preview_rgb = np.asarray(Image.open(preview_path).convert("RGB"), dtype=np.uint8)
            refined = _refine_with_birefnet(preview_rgb, sam_mask, cache_dir, progress_callback)
        except Exception as exc:  # noqa: BLE001
            perf_logger().log("ai.mask.prompt.refine_failed", error=str(exc))
            refined = None
        if refined is not None and float((refined >= 0.5).mean()) > 0.0:
            Image.fromarray(
                np.clip(refined * 255.0, 0, 255).astype(np.uint8), mode="L"
            ).save(output_path)
            binary = refined >= 0.5
            rows, cols = np.any(binary, axis=1), np.any(binary, axis=0)
            y0, y1 = np.where(rows)[0][[0, -1]]
            x0, x1 = np.where(cols)[0][[0, -1]]
            bounds = (int(x0), int(y0), int(x1) + 1, int(y1) + 1)
            coverage = float(binary.mean())
            perf_logger().duration(
                "ai.mask.prompt.refine",
                (time.perf_counter() - refine_started) * 1000.0,
                coverage=coverage,
            )

    return PromptMaskResult(
        source_path=source,
        source_size=(preview_width, preview_height),
        mask_path=output_path,
        bounds=bounds,
        coverage=coverage,
        model_id=model_installation.repo_id,
        model_version=model_installation.revision,
        weights_hash=f"rev:{model_installation.revision}",
    )


class PromptMaskWarmTask(QRunnable):
    """Warm the host's SAM engine (imports/model) without blocking the UI."""

    def __init__(self, stage: str) -> None:
        super().__init__()
        normalized = stage.strip().casefold()
        if normalized not in {"imports", "model"}:
            raise ValueError(f"Unknown prompt warm stage: {stage}")
        self.stage = normalized
        self.setAutoDelete(True)

    def run(self) -> None:
        started = time.perf_counter()
        logger = perf_logger()
        try:
            installation = resolve_sam_model_installation()
            if not installation.is_installed:
                logger.duration(
                    "ai.mask.prompt.warm.skipped", (time.perf_counter() - started) * 1000.0,
                    stage=self.stage, reason="model_not_installed",
                )
                return
            service = default_mask_engine_service()
            if self.stage == "imports":
                device = service.warm_imports("prompt")
            else:
                device = service.warm_model("prompt", installation.install_dir)
            logger.duration(
                "ai.mask.prompt.warm", (time.perf_counter() - started) * 1000.0,
                stage=self.stage, device=device,
            )
        except Exception as exc:  # noqa: BLE001
            logger.duration(
                "ai.mask.prompt.warm.failed", (time.perf_counter() - started) * 1000.0,
                stage=self.stage, error=str(exc),
            )


class PromptMaskTaskSignals(QObject):
    progress = Signal(str, str)          # request id, message
    finished = Signal(str, str, object)  # request id, source path, PromptMaskResult
    failed = Signal(str, str, str)       # request id, source path, error


class PromptMaskTask(QRunnable):
    def __init__(
        self,
        source_path: str | Path,
        points_norm: list[tuple[float, float]],
        *,
        labels: list[int] | None = None,
        refine: bool = False,
        request_id: str = "",
        installation: AIModelInstallation | None = None,
        cache_root: str | Path | None = None,
    ) -> None:
        super().__init__()
        self.source_path = Path(source_path).expanduser().resolve()
        self.points_norm = list(points_norm)
        self.labels = list(labels) if labels else None
        self.refine = bool(refine)
        self.request_id = request_id or uuid.uuid4().hex[:8]
        self.installation = installation
        self.cache_root = cache_root
        self.signals = PromptMaskTaskSignals()
        self.setAutoDelete(True)

    def run(self) -> None:
        source_text = str(self.source_path)
        try:
            result = ensure_prompt_mask(
                self.source_path,
                points_norm=self.points_norm,
                labels=self.labels,
                refine=self.refine,
                installation=self.installation,
                cache_root=self.cache_root,
                progress_callback=lambda message: self.signals.progress.emit(self.request_id, message),
            )
            self.signals.finished.emit(self.request_id, source_text, result)
        except Exception as exc:  # noqa: BLE001
            self.signals.failed.emit(self.request_id, source_text, str(exc))


__all__ = [
    "PromptMaskResult",
    "PromptMaskTask",
    "PromptMaskWarmTask",
    "ensure_prompt_mask",
]
