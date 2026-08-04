from __future__ import annotations

import hashlib
import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np
from PIL import Image, ImageOps
from PySide6.QtCore import QObject, QRunnable, QSize, Signal
from PySide6.QtGui import QImage

from .ai_model import (
    AIModelInstallation,
    DEFAULT_BIREFNET_MODEL_REPO_ID,
    DEFAULT_BIREFNET_MODEL_REVISION,
    download_birefnet_model,
    resolve_birefnet_model_installation,
)
from .ai_runtime_packages import resolve_ai_runtime_site_packages
from .ai_workflow import (
    AIWorkflowRuntime,
    default_ai_workflow_runtime,
    resolve_ai_python_script_command,
)
from .imaging import load_image_for_display


SUBJECT_MASK_REQUESTS: tuple[str, ...] = ("subject", "background")
SUBJECT_MASK_PREVIEW_EDGE = 2048
SUBJECT_MASK_REFINEMENT_VERSION = "birefnet-soft-mask-components-2"

ProgressCallback = Callable[[str], None]


@dataclass(frozen=True)
class SubjectMaskComponent:
    component_id: str
    mask_path: Path
    bbox: tuple[int, int, int, int]
    centroid: tuple[float, float]
    area_fraction: float


@dataclass(frozen=True)
class SubjectMaskResult:
    source_path: Path
    source_size: tuple[int, int]
    mask_paths: dict[str, Path]
    model_id: str
    model_version: str
    weights_hash: str
    runtime_device: str
    cache_hit: bool
    components: tuple[SubjectMaskComponent, ...] = ()
    refinement_version: str = SUBJECT_MASK_REFINEMENT_VERSION


def default_subject_mask_cache_root() -> Path:
    if os.name == "nt":
        local_appdata = os.environ.get("LOCALAPPDATA")
        base = Path(local_appdata) if local_appdata else Path.home() / "AppData" / "Local"
    else:
        xdg_cache = os.environ.get("XDG_CACHE_HOME")
        base = Path(xdg_cache) if xdg_cache else Path.home() / ".cache"
    return base / "image_triage_ai_cache" / "subject_masks"


def ensure_subject_masks(
    source_path: str | Path,
    *,
    installation: AIModelInstallation | None = None,
    cache_root: str | Path | None = None,
    progress_callback: ProgressCallback | None = None,
) -> SubjectMaskResult:
    source = Path(source_path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    model_installation = installation or resolve_birefnet_model_installation()
    if not model_installation.is_installed:
        _validate_subject_runtime()
        _progress(progress_callback, "Downloading BiRefNet subject model...")

        def download_progress(filename: str, current: int, total: int) -> None:
            name = Path(filename).name
            if total > 0:
                _progress(
                    progress_callback,
                    f"Downloading {name}: {current / (1024 * 1024):.1f} / "
                    f"{total / (1024 * 1024):.1f} MB",
                )
            else:
                _progress(progress_callback, f"Downloading {name}...")

        download_birefnet_model(
            model_installation,
            progress_callback=download_progress,
        )

    model_path = model_installation.install_dir / "model.safetensors"
    if not model_installation.is_installed or not model_path.is_file():
        missing = ", ".join(path.name for path in model_installation.missing_files)
        raise FileNotFoundError(f"BiRefNet installation is incomplete: {missing}")

    weights_hash = _sha256_file(model_path)
    stat = source.stat()
    cache_key = _source_cache_key(source, stat.st_size, stat.st_mtime_ns, weights_hash)
    cache_dir = Path(cache_root or default_subject_mask_cache_root()) / cache_key
    metadata_path = cache_dir / "metadata.json"
    mask_paths = {
        "subject": cache_dir / "subject.png",
        "background": cache_dir / "background.png",
    }
    components_dir = cache_dir / "components"
    cached_metadata = _load_json(metadata_path)
    if (
        cached_metadata.get("sourceSizeBytes") == stat.st_size
        and cached_metadata.get("sourceMtimeNs") == stat.st_mtime_ns
        and cached_metadata.get("weightsHash") == weights_hash
        and cached_metadata.get("refinementVersion") == SUBJECT_MASK_REFINEMENT_VERSION
        and all(path.is_file() for path in mask_paths.values())
    ):
        source_size = tuple(cached_metadata.get("sourceSize") or ())
        if len(source_size) == 2 and all(int(value) > 0 for value in source_size):
            _progress(progress_callback, "Using cached subject masks")
            raw_cached_components = cached_metadata.get("components")
            raw_cached_components = (
                raw_cached_components if isinstance(raw_cached_components, list) else []
            )
            components = _components_from_metadata(cached_metadata, components_dir)
            if len(components) == len(raw_cached_components):
                return SubjectMaskResult(
                    source_path=source,
                    source_size=(int(source_size[0]), int(source_size[1])),
                    mask_paths=mask_paths,
                    model_id=model_installation.repo_id,
                    model_version=model_installation.revision,
                    weights_hash=f"sha256:{weights_hash}",
                    runtime_device=str(cached_metadata.get("runtimeDevice") or "unknown"),
                    cache_hit=True,
                    components=components,
                )

    _validate_subject_runtime()
    _progress(progress_callback, "Decoding image...")
    rgb = _decode_rgb_preview(source, SUBJECT_MASK_PREVIEW_EDGE)
    cache_dir.mkdir(parents=True, exist_ok=True)
    worker_input = cache_dir / "worker-input.png"
    Image.fromarray(rgb, mode="RGB").save(worker_input)
    try:
        runtime_device, raw_components = _run_subject_worker(
            model_dir=model_installation.install_dir,
            input_path=worker_input,
            output_path=mask_paths["subject"],
            components_dir=components_dir,
            progress_callback=progress_callback,
        )
    finally:
        worker_input.unlink(missing_ok=True)
    if not mask_paths["subject"].is_file():
        raise RuntimeError("BiRefNet completed without producing a subject mask.")
    with Image.open(mask_paths["subject"]) as foreground:
        subject_mask = foreground.convert("L")
        ImageOps.invert(subject_mask).save(mask_paths["background"])
    components = _components_from_worker(raw_components, components_dir)

    metadata = {
        "sourcePath": str(source),
        "sourceSizeBytes": stat.st_size,
        "sourceMtimeNs": stat.st_mtime_ns,
        "sourceSize": [int(rgb.shape[1]), int(rgb.shape[0])],
        "modelId": model_installation.repo_id,
        "modelVersion": model_installation.revision,
        "weightsHash": weights_hash,
        "runtimeDevice": runtime_device,
        "refinementVersion": SUBJECT_MASK_REFINEMENT_VERSION,
        "components": [_component_metadata(component, components_dir) for component in components],
    }
    metadata_path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    return SubjectMaskResult(
        source_path=source,
        source_size=(int(rgb.shape[1]), int(rgb.shape[0])),
        mask_paths=mask_paths,
        model_id=model_installation.repo_id,
        model_version=model_installation.revision,
        weights_hash=f"sha256:{weights_hash}",
        runtime_device=runtime_device,
        cache_hit=False,
        components=components,
    )


def _run_subject_worker(
    *,
    model_dir: Path,
    input_path: Path,
    output_path: Path,
    components_dir: Path,
    progress_callback: ProgressCallback | None,
) -> tuple[str, list[dict[str, object]]]:
    runtime, site_packages = _resolve_subject_runtime()

    worker_path = Path(__file__).with_name("birefnet_worker.py")
    if not worker_path.is_file() and runtime.python_executable is not None:
        worker_path = runtime.python_executable.parent / "ai_workers" / "birefnet_worker.py"
    command = resolve_ai_python_script_command(
        worker_path,
        "--model-dir",
        str(model_dir),
        "--input",
        str(input_path),
        "--output",
        str(output_path),
        "--components-dir",
        str(components_dir),
        "--device",
        runtime.device,
        runtime=runtime,
    )
    env = dict(os.environ)
    existing_pythonpath = [part for part in env.get("PYTHONPATH", "").split(os.pathsep) if part]
    env["PYTHONPATH"] = os.pathsep.join(
        [*(str(path) for path in site_packages), *existing_pythonpath]
    )
    env["PYTHONUNBUFFERED"] = "1"
    env["HF_HOME"] = str(default_subject_mask_cache_root().parent / "huggingface")
    env["HF_HUB_OFFLINE"] = "1"
    env["TRANSFORMERS_OFFLINE"] = "1"
    creationflags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0) or 0)
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        creationflags=creationflags,
    )
    lines: list[str] = []
    device = "unknown"
    components: list[dict[str, object]] = []
    assert process.stdout is not None
    for raw_line in process.stdout:
        line = raw_line.strip()
        if not line:
            continue
        lines.append(line)
        if line.startswith("PROGRESS "):
            _progress(progress_callback, line.removeprefix("PROGRESS "))
        elif line.startswith("DEVICE "):
            device = line.removeprefix("DEVICE ").strip() or device
        elif line.startswith("RESULT "):
            try:
                result = json.loads(line.removeprefix("RESULT "))
                device = str(result.get("device") or device)
                raw_components = result.get("components")
                if isinstance(raw_components, list):
                    components = [
                        item for item in raw_components if isinstance(item, dict)
                    ]
            except (TypeError, ValueError):
                pass
    return_code = process.wait()
    if return_code != 0:
        detail = "\n".join(lines[-20:])
        raise RuntimeError(
            f"BiRefNet worker failed with exit code {return_code}."
            + (f"\n{detail}" if detail else "")
        )
    return device, components


def combine_subject_components(
    result: SubjectMaskResult,
    component_ids: tuple[str, ...],
) -> Path:
    selected = [
        component
        for component in result.components
        if component.component_id in set(component_ids)
    ]
    if not selected:
        raise ValueError("Select at least one subject.")
    if len(selected) == 1:
        return selected[0].mask_path
    if len(selected) == len(result.components):
        return result.mask_paths["subject"]
    key = "-".join(component.component_id for component in selected)
    output_path = result.mask_paths["subject"].parent / f"selection-{key}.png"
    merged: np.ndarray | None = None
    for component in selected:
        with Image.open(component.mask_path) as loaded:
            values = np.asarray(loaded.convert("L"), dtype=np.uint8)
        merged = values.copy() if merged is None else np.maximum(merged, values)
    assert merged is not None
    Image.fromarray(merged, mode="L").save(output_path)
    return output_path


def _components_from_worker(
    values: list[dict[str, object]],
    components_dir: Path,
) -> tuple[SubjectMaskComponent, ...]:
    return _parse_components(values, components_dir)


def _components_from_metadata(
    metadata: dict[str, object],
    components_dir: Path,
) -> tuple[SubjectMaskComponent, ...]:
    values = metadata.get("components")
    return _parse_components(values if isinstance(values, list) else [], components_dir)


def _parse_components(
    values: list[object],
    components_dir: Path,
) -> tuple[SubjectMaskComponent, ...]:
    components: list[SubjectMaskComponent] = []
    for value in values:
        if not isinstance(value, dict):
            continue
        component_id = str(value.get("id") or "").strip()
        relative_path = str(value.get("path") or "").strip()
        bbox = value.get("bbox")
        centroid = value.get("centroid")
        path = components_dir / relative_path
        if (
            not component_id
            or not relative_path
            or not path.is_file()
            or not isinstance(bbox, list)
            or len(bbox) != 4
            or not isinstance(centroid, list)
            or len(centroid) != 2
        ):
            continue
        components.append(
            SubjectMaskComponent(
                component_id=component_id,
                mask_path=path,
                bbox=tuple(int(item) for item in bbox),
                centroid=tuple(float(item) for item in centroid),
                area_fraction=float(value.get("areaFraction") or 0.0),
            )
        )
    return tuple(components)


def _component_metadata(
    component: SubjectMaskComponent,
    components_dir: Path,
) -> dict[str, object]:
    return {
        "id": component.component_id,
        "path": component.mask_path.relative_to(components_dir).as_posix(),
        "bbox": list(component.bbox),
        "centroid": list(component.centroid),
        "areaFraction": component.area_fraction,
    }


def _validate_subject_runtime() -> None:
    _resolve_subject_runtime()


def _resolve_subject_runtime() -> tuple[AIWorkflowRuntime, tuple[Path, ...]]:
    runtime = default_ai_workflow_runtime()
    site_packages = resolve_ai_runtime_site_packages(device=runtime.device)
    if not site_packages:
        raise RuntimeError(
            "The AI runtime is unavailable. Install the PyTorch AI runtime first."
        )
    required_modules = (
        "torch",
        "transformers",
        "timm",
        "safetensors",
    )
    missing = [
        name
        for name in required_modules
        if not any((site_dir / name).exists() for site_dir in site_packages)
    ]
    if missing:
        raise RuntimeError(
            "The installed AI runtime is missing BiRefNet dependencies: "
            + ", ".join(missing)
        )
    return runtime, site_packages


def _decode_rgb_preview(path: Path, long_edge: int) -> np.ndarray:
    image, error = load_image_for_display(
        str(path),
        QSize(long_edge, long_edge),
        prefer_embedded=True,
    )
    if image.isNull():
        raise RuntimeError(error or "Could not decode image.")
    converted = image.convertToFormat(QImage.Format.Format_RGB888)
    width = converted.width()
    height = converted.height()
    stride = converted.bytesPerLine()
    buffer = np.frombuffer(converted.bits(), dtype=np.uint8, count=height * stride)
    return buffer.reshape(height, stride)[:, : width * 3].reshape(height, width, 3).copy()


def _progress(callback: ProgressCallback | None, message: str) -> None:
    if callback is not None:
        callback(message)


def _source_cache_key(source: Path, size: int, mtime_ns: int, weights_hash: str) -> str:
    identity = "\0".join(
        (
            os.path.normcase(str(source)),
            str(size),
            str(mtime_ns),
            weights_hash,
            SUBJECT_MASK_REFINEMENT_VERSION,
        )
    )
    return hashlib.sha256(identity.encode("utf-8", errors="surrogatepass")).hexdigest()[:24]


def _load_json(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class SubjectMaskTaskSignals(QObject):
    progress = Signal(str, str)
    finished = Signal(str, str, object)
    failed = Signal(str, str, str)


class SubjectMaskTask(QRunnable):
    def __init__(
        self,
        source_path: str | Path,
        request: str,
        *,
        installation: AIModelInstallation | None = None,
        cache_root: str | Path | None = None,
    ) -> None:
        super().__init__()
        normalized = request.strip().casefold()
        if normalized not in SUBJECT_MASK_REQUESTS:
            raise ValueError(f"Unknown subject mask request: {request}")
        self.source_path = Path(source_path).expanduser().resolve()
        self.request = normalized
        self.installation = installation
        self.cache_root = cache_root
        self.signals = SubjectMaskTaskSignals()
        self.setAutoDelete(True)

    def run(self) -> None:
        source_text = str(self.source_path)
        try:
            result = ensure_subject_masks(
                self.source_path,
                installation=self.installation,
                cache_root=self.cache_root,
                progress_callback=lambda message: self.signals.progress.emit(
                    self.request,
                    message,
                ),
            )
            self.signals.finished.emit(self.request, source_text, result)
        except Exception as exc:
            self.signals.failed.emit(self.request, source_text, str(exc))


__all__ = [
    "DEFAULT_BIREFNET_MODEL_REPO_ID",
    "DEFAULT_BIREFNET_MODEL_REVISION",
    "SUBJECT_MASK_REQUESTS",
    "SubjectMaskComponent",
    "SubjectMaskResult",
    "SubjectMaskTask",
    "combine_subject_components",
    "ensure_subject_masks",
]
