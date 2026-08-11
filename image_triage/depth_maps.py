from __future__ import annotations

"""Monocular depth maps (Depth Anything V2) for depth-aware editor effects.

The first non-mask model output the editor consumes: a relative-depth map
(255 = nearest) that drives Lens Blur and, later, atmosphere and depth-aware
light. Computed once per image via the MaskEngine host's ``depth`` engine and
cached on disk keyed by the source (so a reload is a cache hit), mirroring the
subject/prompt mask services.
"""

import hashlib
import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from PIL import Image
from PySide6.QtCore import QObject, QRunnable, Signal

from .ai_model import (
    AIModelInstallation,
    DEFAULT_DEPTH_MODEL_REPO_ID,
    DEFAULT_DEPTH_MODEL_REVISION,
    download_depth_model,
    resolve_depth_model_installation,
)
from .mask_engine_service import default_mask_engine_service
from .perf import perf_logger
from .prompt_masks import _decode_rgb_preview
from .semantic_mask_service import validate_semantic_runtime

DEPTH_PREVIEW_EDGE = 1600
DEPTH_MODEL_ID = DEFAULT_DEPTH_MODEL_REPO_ID
DEPTH_MODEL_VERSION = DEFAULT_DEPTH_MODEL_REVISION

ProgressCallback = Callable[[str], None]


@dataclass(frozen=True)
class DepthMapResult:
    source_path: Path
    source_size: tuple[int, int]   # preview dimensions the depth map lives in
    depth_path: Path
    model_id: str
    model_version: str


def default_depth_cache_root() -> Path:
    if os.name == "nt":
        local_appdata = os.environ.get("LOCALAPPDATA")
        base = Path(local_appdata) if local_appdata else Path.home() / "AppData" / "Local"
    else:
        xdg_cache = os.environ.get("XDG_CACHE_HOME")
        base = Path(xdg_cache) if xdg_cache else Path.home() / ".cache"
    return base / "image_triage_ai_cache" / "depth_maps"


def _progress(callback: ProgressCallback | None, message: str) -> None:
    if callback is not None:
        callback(message)


def _source_cache_key(source: Path, size: int, mtime_ns: int) -> str:
    identity = "\0".join(
        (os.path.normcase(str(source)), str(size), str(mtime_ns), DEPTH_MODEL_VERSION)
    )
    return hashlib.sha256(identity.encode("utf-8", errors="surrogatepass")).hexdigest()[:24]


def ensure_depth_map(
    source_path: str | Path,
    *,
    installation: AIModelInstallation | None = None,
    cache_root: str | Path | None = None,
    progress_callback: ProgressCallback | None = None,
) -> DepthMapResult:
    source = Path(source_path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(source)

    model_installation = installation or resolve_depth_model_installation()
    if not model_installation.is_installed:
        _progress(progress_callback, "Downloading depth model...")

        def download_progress(filename: str, current: int, total: int) -> None:
            name = Path(filename).name
            if total > 0:
                _progress(
                    progress_callback,
                    f"Downloading {name}: {current / (1024 * 1024):.1f} / {total / (1024 * 1024):.1f} MB",
                )
            else:
                _progress(progress_callback, f"Downloading {name}...")

        download_depth_model(model_installation, progress_callback=download_progress)

    validate_semantic_runtime()
    stat = source.stat()
    cache_key = _source_cache_key(source, stat.st_size, stat.st_mtime_ns)
    cache_dir = Path(cache_root or default_depth_cache_root()) / cache_key
    cache_dir.mkdir(parents=True, exist_ok=True)
    depth_path = cache_dir / "depth.png"

    if depth_path.is_file():
        with Image.open(depth_path) as loaded:
            width, height = loaded.size
        return DepthMapResult(
            source_path=source,
            source_size=(int(width), int(height)),
            depth_path=depth_path,
            model_id=model_installation.repo_id,
            model_version=model_installation.revision,
        )

    preview_path = cache_dir / "preview.png"
    _progress(progress_callback, "Preparing image...")
    rgb = _decode_rgb_preview(source, DEPTH_PREVIEW_EDGE)
    Image.fromarray(rgb, mode="RGB").save(preview_path)
    preview_width, preview_height = int(rgb.shape[1]), int(rgb.shape[0])

    _progress(progress_callback, "Estimating depth...")
    started = time.perf_counter()
    scratch = cache_dir / f"depth-{uuid.uuid4().hex[:10]}.png"
    result = default_mask_engine_service().infer_depth(
        model_dir=model_installation.install_dir,
        input_path=preview_path,
        output_path=scratch,
        progress_callback=progress_callback,
    )
    # Atomic-ish publish so a partial file is never cached under depth.png.
    scratch.replace(depth_path)
    perf_logger().duration(
        "ai.mask.depth.total",
        (time.perf_counter() - started) * 1000.0,
        device=str(result.get("device") or "unknown"),
    )
    return DepthMapResult(
        source_path=source,
        source_size=(preview_width, preview_height),
        depth_path=depth_path,
        model_id=model_installation.repo_id,
        model_version=model_installation.revision,
    )


class DepthMapTaskSignals(QObject):
    progress = Signal(str, str)          # request id, message
    finished = Signal(str, str, object)  # request id, source path, DepthMapResult
    failed = Signal(str, str, str)       # request id, source path, error


class DepthMapTask(QRunnable):
    def __init__(
        self,
        source_path: str | Path,
        *,
        request_id: str = "",
        cache_root: str | Path | None = None,
    ) -> None:
        super().__init__()
        self.source_path = Path(source_path).expanduser().resolve()
        self.request_id = request_id or uuid.uuid4().hex[:8]
        self.cache_root = cache_root
        self.signals = DepthMapTaskSignals()
        self.setAutoDelete(True)

    def run(self) -> None:
        source_text = str(self.source_path)
        try:
            result = ensure_depth_map(
                self.source_path,
                cache_root=self.cache_root,
                progress_callback=lambda message: self.signals.progress.emit(
                    self.request_id, message
                ),
            )
            self.signals.finished.emit(self.request_id, source_text, result)
        except Exception as exc:  # noqa: BLE001
            self.signals.failed.emit(self.request_id, source_text, str(exc))


class DepthWarmTask(QRunnable):
    """Warm the host's depth engine (imports/model) without blocking the UI."""

    def __init__(self, stage: str = "model") -> None:
        super().__init__()
        normalized = stage.strip().casefold()
        self.stage = normalized if normalized in {"imports", "model"} else "model"
        self.setAutoDelete(True)

    def run(self) -> None:
        try:
            installation = resolve_depth_model_installation()
            if not installation.is_installed:
                return
            service = default_mask_engine_service()
            if self.stage == "imports":
                service.warm_imports("depth")
            else:
                service.warm_model("depth", installation.install_dir)
        except Exception as exc:  # noqa: BLE001
            perf_logger().log("ai.mask.depth.warm.failed", error=str(exc))


__all__ = [
    "DepthMapResult",
    "ensure_depth_map",
    "default_depth_cache_root",
    "DepthMapTask",
    "DepthWarmTask",
]
