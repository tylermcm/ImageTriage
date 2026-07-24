from __future__ import annotations

import hashlib
import importlib
import json
import os
import sys
import threading
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable

import numpy as np

from PIL import Image
from PySide6.QtCore import QObject, QRunnable, QSize, Signal
from PySide6.QtGui import QImage

from .ai_model import (
    AIModelInstallation,
    DEFAULT_SEGMENTATION_MODEL_REPO_ID,
    DEFAULT_SEGMENTATION_MODEL_REVISION,
    download_segmentation_model,
    resolve_segmentation_model_installation,
)
from .imaging import load_image_for_display


SEMANTIC_MASK_CATEGORIES: tuple[str, ...] = (
    "sky",
    "trees",
    "foliage",
    "water",
    "mountains",
    "animals",
    "people",
)
SEMANTIC_MASK_LABELS: dict[str, tuple[str, ...]] = {
    "sky": ("sky",),
    "trees": ("tree", "palm"),
    "foliage": ("grass", "plant", "field", "flower"),
    "water": ("water", "sea", "river", "swimming pool", "waterfall", "lake"),
    "mountains": ("mountain", "hill"),
    "animals": ("animal",),
    "people": ("person",),
}
SEMANTIC_MASK_MODEL_ID = DEFAULT_SEGMENTATION_MODEL_REPO_ID
SEMANTIC_MASK_MODEL_VERSION = DEFAULT_SEGMENTATION_MODEL_REVISION
SEMANTIC_MASK_PREVIEW_EDGE = 1280
SEMANTIC_MASK_INVENTORY_REQUEST = "inventory"
SEMANTIC_MASK_REFINEMENT_VERSION = (
    "guided-grabcut-sky-water-topology-confidence-1.6-v4"
)
SEMANTIC_MASK_EDGE_GAMMA = 1.6
SEMANTIC_SKY_REPAIR_MIN_CONFIDENT_COVERAGE = 0.10
SEMANTIC_SKY_REPAIR_MAX_PROMOTION_COVERAGE = 0.03
SEMANTIC_SKY_REPAIR_MAX_DISTANCE_RATIO = 0.04
SEMANTIC_PRESENCE_SAMPLE_SIZE = 128
SEMANTIC_PRESENCE_RULES: dict[str, tuple[float, int]] = {
    "animals": (0.35, 4),
    "foliage": (0.35, 4),
    "people": (0.50, 2),
}
SEMANTIC_PRESENCE_DEFAULT_RULE = (0.35, 8)

ProgressCallback = Callable[[str], None]


ort: Any | None = None
_ORT_IMPORT_ERROR = ""
_ORT_DLL_HANDLES: list[object] = []
cv2: Any | None = None


@dataclass(frozen=True)
class SemanticCategoryPresence:
    present: bool
    coverage: float
    largest_component_coverage: float
    peak_confidence: float
    mean_confidence: float


@dataclass(frozen=True)
class SemanticMaskResult:
    source_path: Path
    source_size: tuple[int, int]
    mask_paths: dict[str, Path]
    model_id: str
    model_version: str
    weights_hash: str
    cache_hit: bool
    refinement_version: str = SEMANTIC_MASK_REFINEMENT_VERSION
    presence: dict[str, SemanticCategoryPresence] = field(default_factory=dict)

    @property
    def detected_categories(self) -> tuple[str, ...]:
        order = {category: index for index, category in enumerate(SEMANTIC_MASK_CATEGORIES)}
        return tuple(
            category
            for category, stats in sorted(
                self.presence.items(),
                key=lambda item: (
                    -item[1].coverage,
                    order.get(item[0], len(order)),
                ),
            )
            if stats.present
        )


_SESSION_LOCK = threading.Lock()
_SESSION_CACHE: dict[tuple[str, int, int], Any] = {}


def _candidate_onnxruntime_site_packages() -> tuple[Path, ...]:
    try:
        from .ai_runtime_packages import resolve_ai_runtime_site_packages

        return tuple(resolve_ai_runtime_site_packages(device="cpu"))
    except Exception:
        return ()


def _register_onnxruntime_path(site_packages: Path) -> None:
    path_text = str(site_packages)
    if path_text not in sys.path:
        # Keep the frozen application's bundled packages authoritative. This
        # fallback is only for modules, such as ONNX Runtime, absent from it.
        sys.path.append(path_text)
    if os.name != "nt":
        return
    add_dll_directory = getattr(os, "add_dll_directory", None)
    if add_dll_directory is None:
        return
    binary_dirs = [
        site_packages / "onnxruntime" / "capi",
        *site_packages.glob("*.libs"),
    ]
    for directory in binary_dirs:
        if not directory.is_dir():
            continue
        try:
            _ORT_DLL_HANDLES.append(add_dll_directory(str(directory)))
        except OSError:
            continue


def _load_onnxruntime() -> Any | None:
    global ort, _ORT_IMPORT_ERROR
    if ort is not None:
        return ort

    errors: list[str] = []
    try:
        ort = importlib.import_module("onnxruntime")
        _ORT_IMPORT_ERROR = ""
        return ort
    except Exception as exc:
        errors.append(str(exc))

    for site_packages in _candidate_onnxruntime_site_packages():
        _register_onnxruntime_path(site_packages)
        try:
            ort = importlib.import_module("onnxruntime")
            _ORT_IMPORT_ERROR = ""
            return ort
        except Exception as exc:
            errors.append(f"{site_packages}: {exc}")

    _ORT_IMPORT_ERROR = "; ".join(error for error in errors if error)
    return None


def _load_opencv() -> Any | None:
    global cv2
    if cv2 is not None:
        return cv2
    try:
        cv2 = importlib.import_module("cv2")
        return cv2
    except Exception:
        pass

    for site_packages in _candidate_onnxruntime_site_packages():
        path_text = str(site_packages)
        if path_text not in sys.path:
            sys.path.append(path_text)
        try:
            cv2 = importlib.import_module("cv2")
            return cv2
        except Exception:
            continue
    return None


def default_semantic_mask_cache_root() -> Path:
    if os.name == "nt":
        local_appdata = os.environ.get("LOCALAPPDATA")
        if local_appdata:
            base = Path(local_appdata)
        else:
            try:
                base = Path.home() / "AppData" / "Local"
            except RuntimeError:
                base = Path.cwd() / ".image-triage-cache"
    else:
        xdg_cache = os.environ.get("XDG_CACHE_HOME")
        if xdg_cache:
            base = Path(xdg_cache)
        else:
            try:
                base = Path.home() / ".cache"
            except RuntimeError:
                base = Path.cwd() / ".cache"
    return base / "image_triage_ai_cache" / "semantic_masks"


def ensure_semantic_masks(
    source_path: str | Path,
    *,
    installation: AIModelInstallation | None = None,
    cache_root: str | Path | None = None,
    progress_callback: ProgressCallback | None = None,
) -> SemanticMaskResult:
    source = Path(source_path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    model_installation = installation or resolve_segmentation_model_installation()
    if not model_installation.is_installed:
        _progress(progress_callback, "Downloading masking model...")

        def download_progress(filename: str, current: int, total: int) -> None:
            if total > 0:
                _progress(
                    progress_callback,
                    f"Downloading {Path(filename).name}: {current / (1024 * 1024):.1f} / "
                    f"{total / (1024 * 1024):.1f} MB",
                )
            else:
                _progress(progress_callback, f"Downloading {Path(filename).name}...")

        download_segmentation_model(
            model_installation,
            progress_callback=download_progress,
        )

    model_dir = model_installation.install_dir / "onnx"
    model_path = model_dir / "model.onnx"
    config_path = model_dir / "config.json"
    processor_path = model_dir / "preprocessor_config.json"
    for required in (model_path, config_path, processor_path):
        if not required.is_file():
            raise FileNotFoundError(required)

    weights_hash = _sha256_file(model_path)
    stat = source.stat()
    cache_key = _source_cache_key(source, stat.st_size, stat.st_mtime_ns, weights_hash)
    cache_dir = Path(cache_root or default_semantic_mask_cache_root()) / cache_key
    metadata_path = cache_dir / "metadata.json"
    expected_paths = {
        category: cache_dir / f"{category}.png" for category in SEMANTIC_MASK_CATEGORIES
    }
    cached_metadata = _load_json(metadata_path)
    if (
        cached_metadata.get("sourceSizeBytes") == stat.st_size
        and cached_metadata.get("sourceMtimeNs") == stat.st_mtime_ns
        and cached_metadata.get("weightsHash") == weights_hash
        and cached_metadata.get("refinementVersion") == SEMANTIC_MASK_REFINEMENT_VERSION
        and all(path.is_file() for path in expected_paths.values())
    ):
        source_size = tuple(cached_metadata.get("sourceSize") or ())
        if len(source_size) == 2 and all(int(value) > 0 for value in source_size):
            presence = _presence_from_metadata(cached_metadata.get("categoryStats"))
            if set(presence) != set(SEMANTIC_MASK_CATEGORIES):
                presence = _presence_from_mask_paths(expected_paths)
                cached_metadata["categoryStats"] = _presence_to_metadata(presence)
                try:
                    metadata_path.write_text(
                        json.dumps(cached_metadata, indent=2) + "\n",
                        encoding="utf-8",
                    )
                except OSError:
                    pass
            _progress(progress_callback, "Using cached semantic masks")
            return SemanticMaskResult(
                source_path=source,
                source_size=(int(source_size[0]), int(source_size[1])),
                mask_paths=expected_paths,
                model_id=model_installation.repo_id,
                model_version=model_installation.revision,
                weights_hash=f"sha256:{weights_hash}",
                cache_hit=True,
                presence=presence,
            )

    _progress(progress_callback, "Decoding image...")
    rgb = _decode_rgb_preview(source, SEMANTIC_MASK_PREVIEW_EDGE)
    _progress(progress_callback, "Finding scene regions...")
    with config_path.open("r", encoding="utf-8") as handle:
        model_config = json.load(handle)
    with processor_path.open("r", encoding="utf-8") as handle:
        processor_config = json.load(handle)
    category_indices = _resolve_category_indices(model_config)
    session = _model_session(model_path)
    masks = _infer_masks(session, rgb, processor_config, category_indices)

    _progress(progress_callback, "Refining mask edges...")
    refined: dict[str, np.ndarray] = {}
    for category, mask in masks.items():
        guided = _guided_filter(rgb, mask)
        if category == "sky":
            guided = _repair_sky_mask_boundaries(rgb, guided)
        elif category == "water":
            guided = _refine_water_mask_topology(guided)
        refined[category] = _tighten_mask_confidence(guided)
    presence = {
        category: _measure_semantic_presence(category, mask)
        for category, mask in refined.items()
    }
    presence = _resolve_semantic_presence_conflicts(refined, presence)
    cache_dir.mkdir(parents=True, exist_ok=True)
    for category, mask in refined.items():
        _save_mask(expected_paths[category], mask)
    metadata = {
        "sourcePath": str(source),
        "sourceSizeBytes": stat.st_size,
        "sourceMtimeNs": stat.st_mtime_ns,
        "sourceSize": [int(rgb.shape[1]), int(rgb.shape[0])],
        "modelId": model_installation.repo_id,
        "modelVersion": model_installation.revision,
        "weightsHash": weights_hash,
        "refinementVersion": SEMANTIC_MASK_REFINEMENT_VERSION,
        "categories": list(SEMANTIC_MASK_CATEGORIES),
        "categoryStats": _presence_to_metadata(presence),
    }
    metadata_path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    return SemanticMaskResult(
        source_path=source,
        source_size=(int(rgb.shape[1]), int(rgb.shape[0])),
        mask_paths=expected_paths,
        model_id=model_installation.repo_id,
        model_version=model_installation.revision,
        weights_hash=f"sha256:{weights_hash}",
        cache_hit=False,
        presence=presence,
    )


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
            SEMANTIC_MASK_REFINEMENT_VERSION,
        )
    )
    return hashlib.sha256(identity.encode("utf-8", errors="surrogatepass")).hexdigest()[:24]


def _load_json(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _presence_from_metadata(value: object) -> dict[str, SemanticCategoryPresence]:
    if not isinstance(value, dict):
        return {}
    presence: dict[str, SemanticCategoryPresence] = {}
    for category, raw_stats in value.items():
        if category not in SEMANTIC_MASK_CATEGORIES or not isinstance(raw_stats, dict):
            continue
        try:
            presence[category] = SemanticCategoryPresence(
                present=bool(raw_stats["present"]),
                coverage=float(raw_stats["coverage"]),
                largest_component_coverage=float(raw_stats["largestComponentCoverage"]),
                peak_confidence=float(raw_stats["peakConfidence"]),
                mean_confidence=float(raw_stats["meanConfidence"]),
            )
        except (KeyError, TypeError, ValueError):
            continue
    return presence


def _presence_to_metadata(
    presence: dict[str, SemanticCategoryPresence],
) -> dict[str, dict[str, float | bool]]:
    return {
        category: {
            "present": stats.present,
            "coverage": stats.coverage,
            "largestComponentCoverage": stats.largest_component_coverage,
            "peakConfidence": stats.peak_confidence,
            "meanConfidence": stats.mean_confidence,
        }
        for category, stats in presence.items()
    }


def _presence_from_mask_paths(
    mask_paths: dict[str, Path],
) -> dict[str, SemanticCategoryPresence]:
    presence: dict[str, SemanticCategoryPresence] = {}
    masks: dict[str, np.ndarray] = {}
    for category, path in mask_paths.items():
        with Image.open(path) as image:
            sampled = np.asarray(
                image.convert("L").resize(
                    (SEMANTIC_PRESENCE_SAMPLE_SIZE, SEMANTIC_PRESENCE_SAMPLE_SIZE),
                    Image.Resampling.BILINEAR,
                ),
                dtype=np.float32,
            ) / 255.0
        masks[category] = sampled
        presence[category] = _measure_semantic_presence(category, sampled)
    return _resolve_semantic_presence_conflicts(masks, presence)


def _largest_component(
    binary: np.ndarray,
) -> tuple[int, tuple[int, int, int, int] | None]:
    values = np.asarray(binary, dtype=bool)
    if values.ndim != 2 or not values.any():
        return 0, None
    height, width = values.shape
    visited = np.zeros_like(values, dtype=bool)
    largest = 0
    largest_bounds: tuple[int, int, int, int] | None = None
    for start_y, start_x in np.argwhere(values):
        y = int(start_y)
        x = int(start_x)
        if visited[y, x]:
            continue
        visited[y, x] = True
        stack = [(y, x)]
        size = 0
        min_x = max_x = x
        min_y = max_y = y
        while stack:
            current_y, current_x = stack.pop()
            size += 1
            min_x = min(min_x, current_x)
            max_x = max(max_x, current_x)
            min_y = min(min_y, current_y)
            max_y = max(max_y, current_y)
            for neighbor_y in range(max(0, current_y - 1), min(height, current_y + 2)):
                for neighbor_x in range(max(0, current_x - 1), min(width, current_x + 2)):
                    if (
                        (neighbor_y != current_y or neighbor_x != current_x)
                        and values[neighbor_y, neighbor_x]
                        and not visited[neighbor_y, neighbor_x]
                    ):
                        visited[neighbor_y, neighbor_x] = True
                        stack.append((neighbor_y, neighbor_x))
        if size > largest:
            largest = size
            largest_bounds = (min_x, min_y, max_x + 1, max_y + 1)
    return largest, largest_bounds


def _sample_semantic_mask(mask: np.ndarray) -> np.ndarray:
    sampled_image = Image.fromarray(
        np.asarray(mask, dtype=np.float32),
        mode="F",
    ).resize(
        (SEMANTIC_PRESENCE_SAMPLE_SIZE, SEMANTIC_PRESENCE_SAMPLE_SIZE),
        Image.Resampling.BILINEAR,
    )
    return np.clip(np.asarray(sampled_image, dtype=np.float32), 0.0, 1.0)


def _semantic_presence_binary(category: str, mask: np.ndarray) -> np.ndarray:
    threshold, _minimum_component_pixels = SEMANTIC_PRESENCE_RULES.get(
        category,
        SEMANTIC_PRESENCE_DEFAULT_RULE,
    )
    return _sample_semantic_mask(mask) >= threshold


def _measure_semantic_presence(
    category: str,
    mask: np.ndarray,
) -> SemanticCategoryPresence:
    sampled = _sample_semantic_mask(mask)
    threshold, minimum_component_pixels = SEMANTIC_PRESENCE_RULES.get(
        category,
        SEMANTIC_PRESENCE_DEFAULT_RULE,
    )
    confident = sampled >= threshold
    confident_count = int(np.count_nonzero(confident))
    largest_component, _bounds = _largest_component(confident)
    total = max(1, sampled.size)
    return SemanticCategoryPresence(
        present=largest_component >= minimum_component_pixels,
        coverage=confident_count / total,
        largest_component_coverage=largest_component / total,
        peak_confidence=float(sampled.max(initial=0.0)),
        mean_confidence=(
            float(sampled[confident].mean())
            if confident_count
            else 0.0
        ),
    )


def _bounds_overlap_fraction(
    first: tuple[int, int, int, int] | None,
    second: tuple[int, int, int, int] | None,
) -> float:
    if first is None or second is None:
        return 0.0
    left = max(first[0], second[0])
    top = max(first[1], second[1])
    right = min(first[2], second[2])
    bottom = min(first[3], second[3])
    intersection = max(0, right - left) * max(0, bottom - top)
    first_area = max(1, (first[2] - first[0]) * (first[3] - first[1]))
    second_area = max(1, (second[2] - second[0]) * (second[3] - second[1]))
    return intersection / min(first_area, second_area)


def _binary_bounds(binary: np.ndarray) -> tuple[int, int, int, int] | None:
    rows, columns = np.where(np.asarray(binary, dtype=bool))
    if not len(rows):
        return None
    return (
        int(columns.min()),
        int(rows.min()),
        int(columns.max()) + 1,
        int(rows.max()) + 1,
    )


def _resolve_semantic_presence_conflicts(
    masks: dict[str, np.ndarray],
    presence: dict[str, SemanticCategoryPresence],
) -> dict[str, SemanticCategoryPresence]:
    animal = presence.get("animals")
    person = presence.get("people")
    if (
        animal is None
        or person is None
        or not animal.present
        or not person.present
        or animal.coverage < person.coverage * 2.0
    ):
        return presence
    animal_bounds = _binary_bounds(
        _semantic_presence_binary("animals", masks["animals"])
    )
    person_bounds = _binary_bounds(
        _semantic_presence_binary("people", masks["people"])
    )
    if _bounds_overlap_fraction(animal_bounds, person_bounds) < 0.70:
        return presence
    resolved = dict(presence)
    resolved["people"] = replace(person, present=False)
    return resolved


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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


def _model_session(model_path: Path) -> Any:
    runtime = _load_onnxruntime()
    if runtime is None:
        detail = f" ({_ORT_IMPORT_ERROR})" if _ORT_IMPORT_ERROR else ""
        raise RuntimeError(
            "ONNX Runtime is unavailable. Install it from "
            "AI > Runtime And Cache > Install AI Runtime."
            f"{detail}"
        )
    stat = model_path.stat()
    key = (str(model_path.resolve()), stat.st_size, stat.st_mtime_ns)
    with _SESSION_LOCK:
        cached = _SESSION_CACHE.get(key)
        if cached is not None:
            return cached
        options = runtime.SessionOptions()
        options.intra_op_num_threads = max(1, min(8, (os.cpu_count() or 4) // 2))
        options.inter_op_num_threads = 1
        options.graph_optimization_level = runtime.GraphOptimizationLevel.ORT_ENABLE_BASIC
        session = runtime.InferenceSession(
            str(model_path),
            sess_options=options,
            providers=["CPUExecutionProvider"],
        )
        _SESSION_CACHE.clear()
        _SESSION_CACHE[key] = session
        return session


def _preprocess(rgb: np.ndarray, config: dict[str, object]) -> np.ndarray:
    size = config.get("size", {})
    width = int(size.get("width", 512)) if isinstance(size, dict) else 512
    height = int(size.get("height", 512)) if isinstance(size, dict) else 512
    resized = np.asarray(
        Image.fromarray(rgb, mode="RGB").resize((width, height), Image.Resampling.BILINEAR),
        dtype=np.float32,
    )
    values = resized * float(config.get("rescale_factor", 1.0 / 255.0))
    mean = np.asarray(config.get("image_mean", [0.485, 0.456, 0.406]), dtype=np.float32)
    std = np.asarray(config.get("image_std", [0.229, 0.224, 0.225]), dtype=np.float32)
    values = (values - mean) / std
    return np.transpose(values, (2, 0, 1))[None, ...].astype(np.float32, copy=False)


def _resolve_category_indices(config: dict[str, object]) -> dict[str, list[int]]:
    raw_labels = config.get("id2label", {})
    if not isinstance(raw_labels, dict):
        raise ValueError("Segmentation model config is missing id2label.")
    normalized = {
        int(index): str(label).strip().casefold()
        for index, label in raw_labels.items()
    }
    resolved: dict[str, list[int]] = {}
    for category, labels in SEMANTIC_MASK_LABELS.items():
        wanted = {label.casefold() for label in labels}
        indices = [index for index, label in normalized.items() if label in wanted]
        if not indices:
            raise ValueError(f"Segmentation model has no labels for {category}.")
        resolved[category] = indices
    return resolved


def _softmax(values: np.ndarray, axis: int) -> np.ndarray:
    shifted = values - np.max(values, axis=axis, keepdims=True)
    exponent = np.exp(shifted)
    return exponent / np.sum(exponent, axis=axis, keepdims=True)


def _infer_masks(
    session: Any,
    rgb: np.ndarray,
    processor_config: dict[str, object],
    category_indices: dict[str, list[int]],
) -> dict[str, np.ndarray]:
    model_input = _preprocess(rgb, processor_config)
    logits = session.run(["logits"], {"pixel_values": model_input})[0]
    probabilities = _softmax(logits[0], axis=0)
    height, width = rgb.shape[:2]
    masks: dict[str, np.ndarray] = {}
    for category, indices in category_indices.items():
        low_resolution = probabilities[indices].sum(axis=0).astype(np.float32)
        resized = Image.fromarray(low_resolution, mode="F").resize(
            (width, height),
            Image.Resampling.BILINEAR,
        )
        masks[category] = np.clip(np.asarray(resized, dtype=np.float32), 0.0, 1.0)
    return masks


def _box_mean(values: np.ndarray, radius: int) -> np.ndarray:
    if radius <= 0:
        return values.astype(np.float32, copy=True)
    padded = np.pad(values, ((radius, radius), (radius, radius)), mode="reflect")
    integral = np.pad(padded, ((1, 0), (1, 0)), mode="constant")
    integral = np.cumsum(np.cumsum(integral, axis=0, dtype=np.float32), axis=1, dtype=np.float32)
    kernel = radius * 2 + 1
    sums = (
        integral[kernel:, kernel:]
        - integral[:-kernel, kernel:]
        - integral[kernel:, :-kernel]
        + integral[:-kernel, :-kernel]
    )
    return sums / float(kernel * kernel)


def _guided_filter(
    guide_rgb: np.ndarray,
    mask: np.ndarray,
    *,
    radius: int = 8,
    epsilon: float = 1e-3,
) -> np.ndarray:
    rgb = guide_rgb.astype(np.float32) / 255.0
    gray = rgb[..., 0] * 0.299 + rgb[..., 1] * 0.587 + rgb[..., 2] * 0.114
    source = mask.astype(np.float32, copy=False)
    mean_i = _box_mean(gray, radius)
    mean_p = _box_mean(source, radius)
    corr_i = _box_mean(gray * gray, radius)
    corr_ip = _box_mean(gray * source, radius)
    variance_i = corr_i - mean_i * mean_i
    covariance_ip = corr_ip - mean_i * mean_p
    a = covariance_ip / (variance_i + epsilon)
    b = mean_p - a * mean_i
    return np.clip(_box_mean(a, radius) * gray + _box_mean(b, radius), 0.0, 1.0)


def _tighten_mask_confidence(
    mask: np.ndarray,
    *,
    gamma: float = SEMANTIC_MASK_EDGE_GAMMA,
) -> np.ndarray:
    """Compress uncertain edge tails without moving the 50% boundary."""
    values = np.clip(np.asarray(mask, dtype=np.float32), 0.0, 1.0)
    if gamma <= 1.0:
        return values.copy()
    selected = np.power(values, gamma)
    rejected = np.power(1.0 - values, gamma)
    return np.divide(
        selected,
        selected + rejected,
        out=np.zeros_like(selected),
        where=(selected + rejected) > 0.0,
    )


def _repair_sky_mask_boundaries(
    guide_rgb: np.ndarray,
    mask: np.ndarray,
) -> np.ndarray:
    """Promote small image-supported gaps along a confident sky boundary."""
    values = np.clip(np.asarray(mask, dtype=np.float32), 0.0, 1.0)
    height, width = values.shape
    if height < 16 or width < 16:
        return values.copy()

    definite_background = values < 0.05
    definite_foreground = values >= 0.90
    selected = values >= 0.35
    if (
        np.count_nonzero(definite_background) < 64
        or np.count_nonzero(definite_foreground) < 64
        or float(np.mean(definite_foreground))
        < SEMANTIC_SKY_REPAIR_MIN_CONFIDENT_COVERAGE
        or np.count_nonzero(selected[0]) < max(4, int(round(width * 0.05)))
    ):
        return values.copy()

    opencv = _load_opencv()
    if opencv is None:
        return values.copy()

    labels = np.full(values.shape, opencv.GC_PR_BGD, dtype=np.uint8)
    labels[definite_background] = opencv.GC_BGD
    labels[(values >= 0.38) & (values < 0.90)] = opencv.GC_PR_FGD
    labels[definite_foreground] = opencv.GC_FGD
    background_model = np.zeros((1, 65), dtype=np.float64)
    foreground_model = np.zeros((1, 65), dtype=np.float64)
    try:
        opencv.grabCut(
            opencv.cvtColor(guide_rgb, opencv.COLOR_RGB2BGR),
            labels,
            None,
            background_model,
            foreground_model,
            1,
            opencv.GC_INIT_WITH_MASK,
        )
    except Exception:
        return values.copy()

    grabcut_foreground = np.isin(
        labels,
        (opencv.GC_FGD, opencv.GC_PR_FGD),
    )
    distance = opencv.distanceTransform(
        np.logical_not(selected).astype(np.uint8),
        opencv.DIST_L2,
        3,
    )
    max_distance = max(
        4.0,
        float(max(height, width)) * SEMANTIC_SKY_REPAIR_MAX_DISTANCE_RATIO,
    )
    promotion = grabcut_foreground & np.logical_not(selected) & (distance <= max_distance)
    promotion_coverage = float(np.mean(promotion))
    if (
        promotion_coverage <= 0.0
        or promotion_coverage > SEMANTIC_SKY_REPAIR_MAX_PROMOTION_COVERAGE
    ):
        return values.copy()

    delta = np.where(
        promotion,
        np.maximum(0.72 - values, 0.0),
        0.0,
    ).astype(np.float32)
    delta = opencv.GaussianBlur(delta, (0, 0), 1.1)
    return np.clip(values + delta, 0.0, 1.0)


def _refine_water_mask_topology(mask: np.ndarray) -> np.ndarray:
    """Remove small, disconnected high-confidence regions from water masks."""
    values = np.clip(np.asarray(mask, dtype=np.float32), 0.0, 1.0)
    height, width = values.shape
    if height < 16 or width < 16:
        return values.copy()

    opencv = _load_opencv()
    if opencv is None:
        return values.copy()

    component_count, labels, stats, centroids = opencv.connectedComponentsWithStats(
        (values >= 0.35).astype(np.uint8),
        8,
    )
    if component_count <= 1:
        return values.copy()

    areas = stats[1:, opencv.CC_STAT_AREA]
    largest_label = 1 + int(np.argmax(areas))
    largest_area = int(areas[largest_label - 1])
    if largest_area < max(64, int(round(values.size * 0.005))):
        return values.copy()

    substantial_area = max(64, int(round(largest_area * 0.05)))
    lower_fragment_area = max(64, int(round(largest_area * 0.002)))
    retained_labels: list[int] = []
    for label in range(1, component_count):
        area = int(stats[label, opencv.CC_STAT_AREA])
        center_y = float(centroids[label, 1])
        if (
            label == largest_label
            or area >= substantial_area
            or (center_y >= height * 0.50 and area >= lower_fragment_area)
        ):
            retained_labels.append(label)

    retained_core = np.isin(labels, retained_labels).astype(np.uint8)
    radius = max(3, int(round(max(height, width) * 0.015)))
    kernel = opencv.getStructuringElement(
        opencv.MORPH_ELLIPSE,
        (radius * 2 + 1, radius * 2 + 1),
    )
    support = opencv.dilate(retained_core, kernel)
    return values * support.astype(np.float32)


def _save_mask(path: Path, mask: np.ndarray) -> None:
    pixels = np.clip(mask * 255.0, 0, 255).astype(np.uint8)
    Image.fromarray(pixels, mode="L").save(path)


class SemanticMaskTaskSignals(QObject):
    progress = Signal(str, str)
    finished = Signal(str, str, object)
    failed = Signal(str, str, str)


class SemanticMaskTask(QRunnable):
    def __init__(
        self,
        source_path: str | Path,
        category: str = SEMANTIC_MASK_INVENTORY_REQUEST,
        *,
        installation: AIModelInstallation | None = None,
        cache_root: str | Path | None = None,
    ) -> None:
        super().__init__()
        normalized = category.strip().casefold()
        if (
            normalized not in SEMANTIC_MASK_CATEGORIES
            and normalized != SEMANTIC_MASK_INVENTORY_REQUEST
        ):
            raise ValueError(f"Unknown semantic mask category: {category}")
        self.source_path = Path(source_path).expanduser().resolve()
        self.category = normalized
        self.installation = installation
        self.cache_root = cache_root
        self.signals = SemanticMaskTaskSignals()
        self.setAutoDelete(True)

    def run(self) -> None:
        source_text = str(self.source_path)
        try:
            result = ensure_semantic_masks(
                self.source_path,
                installation=self.installation,
                cache_root=self.cache_root,
                progress_callback=lambda message: self.signals.progress.emit(
                    self.category,
                    message,
                ),
            )
            self.signals.finished.emit(self.category, source_text, result)
        except Exception as exc:
            self.signals.failed.emit(self.category, source_text, str(exc))
