"""Run a CPU-only SegFormer semantic-mask quality benchmark.

The benchmark is intentionally separate from the editor UI. It reuses Image
Triage's display decoder so JPEG orientation and RAW embedded-preview handling
match the application, then writes review artifacts under an ignored output
directory.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import cv2
import numpy as np
import onnxruntime as ort
from PIL import Image, ImageDraw, ImageFont
from PySide6.QtCore import QSize
from PySide6.QtGui import QImage


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from image_triage.imaging import load_image_for_display  # noqa: E402


MODEL_REVISION = "b9175de73a0a34f7843135853d27629aa6987b2f"
SUPPORTED_SUFFIXES = frozenset(
    {
        ".arw",
        ".cr2",
        ".cr3",
        ".dng",
        ".heic",
        ".heif",
        ".jpeg",
        ".jpg",
        ".nef",
        ".orf",
        ".png",
        ".raf",
        ".rw2",
        ".tif",
        ".tiff",
    }
)
RAW_SUFFIXES = frozenset({".arw", ".cr2", ".cr3", ".dng", ".nef", ".orf", ".raf", ".rw2"})
SKIP_DIRECTORY_NAMES = frozenset(
    {
        ".image_triage_ai",
        ".thumbnails",
        "__macosx",
        "@eadir",
        "$recycle.bin",
        "system volume information",
    }
)

CATEGORY_LABELS = {
    "sky": ("sky",),
    "trees": ("tree", "palm"),
    "foliage": ("grass", "plant", "field", "flower"),
    "water": ("water", "sea", "river", "swimming pool", "waterfall", "lake"),
    "mountains": ("mountain", "hill"),
    "people": ("person",),
}
CATEGORY_COLORS = {
    "sky": (70, 150, 255),
    "trees": (24, 164, 88),
    "foliage": (158, 214, 64),
    "water": (18, 210, 220),
    "mountains": (228, 142, 52),
    "people": (240, 68, 114),
}


@dataclass(frozen=True)
class BenchmarkItem:
    source: Path
    relative_path: Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="Photo folder to sample recursively.")
    parser.add_argument(
        "--model-dir",
        type=Path,
        default=REPO_ROOT / ".image-triage" / "models" / "segformer-b0-ade20k-onnx",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=REPO_ROOT / ".benchmarks" / "semantic_masks" / "cpu",
    )
    parser.add_argument("--limit", type=int, default=36)
    parser.add_argument("--raw-count", type=int, default=12)
    parser.add_argument("--preview-long-edge", type=int, default=1280)
    parser.add_argument("--threads", type=int, default=max(1, min(8, (os_cpu_count() or 4) // 2)))
    return parser.parse_args()


def os_cpu_count() -> int | None:
    try:
        import os

        return os.cpu_count()
    except Exception:
        return None


def discover_images(source: Path) -> list[Path]:
    images: list[Path] = []
    for path in source.rglob("*"):
        if not path.is_file() or path.suffix.casefold() not in SUPPORTED_SUFFIXES:
            continue
        relative_parts = path.relative_to(source).parts[:-1]
        if any(part.casefold() in SKIP_DIRECTORY_NAMES for part in relative_parts):
            continue
        if path.name.startswith("._"):
            continue
        images.append(path)
    return sorted(images, key=lambda path: str(path).casefold())


def evenly_spaced(items: list[Path], count: int) -> list[Path]:
    if count <= 0 or not items:
        return []
    if count >= len(items):
        return list(items)
    indices = np.linspace(0, len(items) - 1, count, dtype=np.int64)
    return [items[int(index)] for index in indices]


def select_sample(source: Path, candidates: list[Path], limit: int, raw_count: int) -> list[BenchmarkItem]:
    requested = max(1, min(int(limit), len(candidates)))
    raw_target = max(0, min(int(raw_count), requested))
    raw_candidates = [path for path in candidates if path.suffix.casefold() in RAW_SUFFIXES]
    raster_candidates = [path for path in candidates if path.suffix.casefold() not in RAW_SUFFIXES]

    selected: list[Path] = []
    seen_stems: set[str] = set()

    def add(paths: Iterable[Path], target: int) -> None:
        available = [path for path in paths if path.stem.casefold() not in seen_stems]
        for path in evenly_spaced(available, target):
            stem = path.stem.casefold()
            if stem in seen_stems:
                continue
            selected.append(path)
            seen_stems.add(stem)

    add(raw_candidates, raw_target)
    add(raster_candidates, requested - len(selected))
    if len(selected) < requested:
        add(candidates, requested - len(selected))

    selected.sort(key=lambda path: str(path).casefold())
    return [BenchmarkItem(path, path.relative_to(source)) for path in selected[:requested]]


def qimage_to_rgb(image: QImage) -> np.ndarray:
    converted = image.convertToFormat(QImage.Format.Format_RGB888)
    width = converted.width()
    height = converted.height()
    stride = converted.bytesPerLine()
    buffer = np.frombuffer(converted.bits(), dtype=np.uint8, count=height * stride)
    rows = buffer.reshape(height, stride)
    return rows[:, : width * 3].reshape(height, width, 3).copy()


def decode_preview(path: Path, long_edge: int) -> tuple[np.ndarray, float]:
    started = time.perf_counter()
    image, error = load_image_for_display(
        str(path),
        QSize(long_edge, long_edge),
        prefer_embedded=True,
    )
    if image.isNull():
        raise RuntimeError(error or "Could not decode image.")
    return qimage_to_rgb(image), elapsed_ms(started)


def preprocess(rgb: np.ndarray, config: dict[str, object]) -> np.ndarray:
    size = config.get("size", {})
    width = int(size.get("width", 512)) if isinstance(size, dict) else 512
    height = int(size.get("height", 512)) if isinstance(size, dict) else 512
    resized = cv2.resize(rgb, (width, height), interpolation=cv2.INTER_LINEAR)
    values = resized.astype(np.float32) * float(config.get("rescale_factor", 1.0 / 255.0))
    mean = np.asarray(config.get("image_mean", [0.485, 0.456, 0.406]), dtype=np.float32)
    std = np.asarray(config.get("image_std", [0.229, 0.224, 0.225]), dtype=np.float32)
    values = (values - mean) / std
    return np.transpose(values, (2, 0, 1))[None, ...].astype(np.float32, copy=False)


def softmax(values: np.ndarray, axis: int) -> np.ndarray:
    shifted = values - np.max(values, axis=axis, keepdims=True)
    exponent = np.exp(shifted)
    return exponent / np.sum(exponent, axis=axis, keepdims=True)


def resolve_category_indices(config: dict[str, object]) -> dict[str, list[int]]:
    raw_labels = config.get("id2label", {})
    if not isinstance(raw_labels, dict):
        raise ValueError("Model config is missing id2label.")
    normalized = {int(index): str(label).strip().casefold() for index, label in raw_labels.items()}
    result: dict[str, list[int]] = {}
    for category, labels in CATEGORY_LABELS.items():
        wanted = {label.casefold() for label in labels}
        indices = [index for index, label in normalized.items() if label in wanted]
        if not indices:
            raise ValueError(f"Model has no labels for category {category!r}.")
        result[category] = indices
    return result


def infer_category_masks(
    session: ort.InferenceSession,
    rgb: np.ndarray,
    processor_config: dict[str, object],
    category_indices: dict[str, list[int]],
) -> tuple[dict[str, np.ndarray], float]:
    model_input = preprocess(rgb, processor_config)
    started = time.perf_counter()
    logits = session.run(["logits"], {"pixel_values": model_input})[0]
    inference_ms = elapsed_ms(started)
    probabilities = softmax(logits[0], axis=0)
    height, width = rgb.shape[:2]
    masks = {
        category: np.clip(
            cv2.resize(
                probabilities[indices].sum(axis=0),
                (width, height),
                interpolation=cv2.INTER_LINEAR,
            ),
            0.0,
            1.0,
        ).astype(np.float32)
        for category, indices in category_indices.items()
    }
    return masks, inference_ms


def guided_filter(guide: np.ndarray, mask: np.ndarray, radius: int = 8, epsilon: float = 1e-3) -> np.ndarray:
    gray = cv2.cvtColor(guide, cv2.COLOR_RGB2GRAY).astype(np.float32) / 255.0
    source = mask.astype(np.float32, copy=False)
    kernel = (radius * 2 + 1, radius * 2 + 1)
    mean_i = cv2.boxFilter(gray, cv2.CV_32F, kernel, borderType=cv2.BORDER_REFLECT)
    mean_p = cv2.boxFilter(source, cv2.CV_32F, kernel, borderType=cv2.BORDER_REFLECT)
    corr_i = cv2.boxFilter(gray * gray, cv2.CV_32F, kernel, borderType=cv2.BORDER_REFLECT)
    corr_ip = cv2.boxFilter(gray * source, cv2.CV_32F, kernel, borderType=cv2.BORDER_REFLECT)
    variance_i = corr_i - mean_i * mean_i
    covariance_ip = corr_ip - mean_i * mean_p
    a = covariance_ip / (variance_i + epsilon)
    b = mean_p - a * mean_i
    mean_a = cv2.boxFilter(a, cv2.CV_32F, kernel, borderType=cv2.BORDER_REFLECT)
    mean_b = cv2.boxFilter(b, cv2.CV_32F, kernel, borderType=cv2.BORDER_REFLECT)
    return np.clip(mean_a * gray + mean_b, 0.0, 1.0)


def tighten_mask_confidence(mask: np.ndarray, gamma: float = 1.6) -> np.ndarray:
    values = np.clip(mask.astype(np.float32, copy=False), 0.0, 1.0)
    selected = np.power(values, gamma)
    rejected = np.power(1.0 - values, gamma)
    return np.divide(
        selected,
        selected + rejected,
        out=np.zeros_like(selected),
        where=(selected + rejected) > 0.0,
    )


def repair_sky_mask_boundaries(guide: np.ndarray, mask: np.ndarray) -> np.ndarray:
    values = np.clip(mask.astype(np.float32, copy=False), 0.0, 1.0)
    height, width = values.shape
    definite_background = values < 0.05
    definite_foreground = values >= 0.90
    selected = values >= 0.35
    if (
        height < 16
        or width < 16
        or np.count_nonzero(definite_background) < 64
        or np.count_nonzero(definite_foreground) < 64
        or float(np.mean(definite_foreground)) < 0.10
        or np.count_nonzero(selected[0]) < max(4, int(round(width * 0.05)))
    ):
        return values.copy()

    labels = np.full(values.shape, cv2.GC_PR_BGD, dtype=np.uint8)
    labels[definite_background] = cv2.GC_BGD
    labels[(values >= 0.38) & (values < 0.90)] = cv2.GC_PR_FGD
    labels[definite_foreground] = cv2.GC_FGD
    try:
        cv2.grabCut(
            cv2.cvtColor(guide, cv2.COLOR_RGB2BGR),
            labels,
            None,
            np.zeros((1, 65), dtype=np.float64),
            np.zeros((1, 65), dtype=np.float64),
            1,
            cv2.GC_INIT_WITH_MASK,
        )
    except cv2.error:
        return values.copy()

    grabcut_foreground = np.isin(labels, (cv2.GC_FGD, cv2.GC_PR_FGD))
    distance = cv2.distanceTransform(
        np.logical_not(selected).astype(np.uint8),
        cv2.DIST_L2,
        3,
    )
    promotion = (
        grabcut_foreground
        & np.logical_not(selected)
        & (distance <= max(4.0, float(max(height, width)) * 0.04))
    )
    promotion_coverage = float(np.mean(promotion))
    if promotion_coverage <= 0.0 or promotion_coverage > 0.03:
        return values.copy()

    delta = np.where(
        promotion,
        np.maximum(0.72 - values, 0.0),
        0.0,
    ).astype(np.float32)
    return np.clip(values + cv2.GaussianBlur(delta, (0, 0), 1.1), 0.0, 1.0)


def refine_water_mask_topology(mask: np.ndarray) -> np.ndarray:
    values = np.clip(mask.astype(np.float32, copy=False), 0.0, 1.0)
    height, width = values.shape
    if height < 16 or width < 16:
        return values.copy()
    count, labels, stats, centroids = cv2.connectedComponentsWithStats(
        (values >= 0.35).astype(np.uint8),
        8,
    )
    if count <= 1:
        return values.copy()
    areas = stats[1:, cv2.CC_STAT_AREA]
    largest_label = 1 + int(np.argmax(areas))
    largest_area = int(areas[largest_label - 1])
    if largest_area < max(64, int(round(values.size * 0.005))):
        return values.copy()

    retained = []
    for label in range(1, count):
        area = int(stats[label, cv2.CC_STAT_AREA])
        center_y = float(centroids[label, 1])
        if (
            label == largest_label
            or area >= max(64, int(round(largest_area * 0.05)))
            or (
                center_y >= height * 0.50
                and area >= max(64, int(round(largest_area * 0.002)))
            )
        ):
            retained.append(label)
    core = np.isin(labels, retained).astype(np.uint8)
    radius = max(3, int(round(max(height, width) * 0.015)))
    support = cv2.dilate(
        core,
        cv2.getStructuringElement(
            cv2.MORPH_ELLIPSE,
            (radius * 2 + 1, radius * 2 + 1),
        ),
    )
    return values * support.astype(np.float32)


def overlay_mask(rgb: np.ndarray, mask: np.ndarray, color: tuple[int, int, int]) -> np.ndarray:
    alpha = np.clip(mask * 0.78, 0.0, 0.72)[..., None]
    tint = np.empty_like(rgb)
    tint[:, :] = color
    return np.clip(rgb * (1.0 - alpha) + tint * alpha, 0, 255).astype(np.uint8)


def all_category_overlay(rgb: np.ndarray, masks: dict[str, np.ndarray]) -> np.ndarray:
    output = rgb.astype(np.float32)
    for category, mask in masks.items():
        alpha = np.clip(mask * 0.68, 0.0, 0.58)[..., None]
        color = np.asarray(CATEGORY_COLORS[category], dtype=np.float32)
        output = output * (1.0 - alpha) + color * alpha
    return np.clip(output, 0, 255).astype(np.uint8)


def exposure_preview(rgb: np.ndarray, mask: np.ndarray) -> np.ndarray:
    linear = rgb.astype(np.float32) / 255.0
    brightened = np.clip(linear * 2.5, 0.0, 1.0)
    alpha = np.clip(mask, 0.0, 1.0)[..., None]
    adjusted = linear * (1.0 - alpha) + brightened * alpha
    return np.clip(adjusted * 255.0, 0, 255).astype(np.uint8)


def save_mask(path: Path, mask: np.ndarray) -> None:
    Image.fromarray(np.clip(mask * 255.0, 0, 255).astype(np.uint8), mode="L").save(path)


def labeled_tile(
    rgb: np.ndarray,
    label: str,
    *,
    width: int = 360,
    height: int = 260,
) -> Image.Image:
    canvas = Image.new("RGB", (width, height), (23, 23, 23))
    image = Image.fromarray(rgb, mode="RGB")
    image.thumbnail((width, height - 28), Image.Resampling.LANCZOS)
    x = (width - image.width) // 2
    y = 28 + (height - 28 - image.height) // 2
    canvas.paste(image, (x, y))
    draw = ImageDraw.Draw(canvas)
    draw.text((8, 7), label, fill=(245, 245, 245), font=ImageFont.load_default())
    return canvas


def make_review_panel(
    rgb: np.ndarray,
    masks: dict[str, np.ndarray],
    title: str,
    *,
    adjusted: bool,
) -> Image.Image:
    tiles: list[Image.Image] = []
    for category in CATEGORY_LABELS:
        mask = masks[category]
        rendered = exposure_preview(rgb, mask) if adjusted else overlay_mask(rgb, mask, CATEGORY_COLORS[category])
        coverage = float(np.mean(mask >= 0.35) * 100.0)
        tiles.append(labeled_tile(rendered, f"{category.title()}  {coverage:.1f}%"))
    columns = 3
    rows = math.ceil(len(tiles) / columns)
    panel = Image.new("RGB", (columns * 360, rows * 260 + 30), (12, 12, 12))
    draw = ImageDraw.Draw(panel)
    draw.text((8, 8), title, fill=(250, 250, 250), font=ImageFont.load_default())
    for index, tile in enumerate(tiles):
        panel.paste(tile, ((index % columns) * 360, 30 + (index // columns) * 260))
    return panel


def make_contact_sheet(entries: list[tuple[np.ndarray, str]], output: Path) -> None:
    if not entries:
        return
    columns = 4
    tiles = [labeled_tile(image, label, width=320, height=240) for image, label in entries]
    rows = math.ceil(len(tiles) / columns)
    sheet = Image.new("RGB", (columns * 320, rows * 240), (12, 12, 12))
    for index, tile in enumerate(tiles):
        sheet.paste(tile, ((index % columns) * 320, (index // columns) * 240))
    sheet.save(output, quality=90)


def safe_output_name(index: int, relative_path: Path) -> str:
    flattened = "__".join(relative_path.parts)
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", flattened).strip("._")
    return f"{index:03d}_{cleaned[:120]}"


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def elapsed_ms(started: float) -> float:
    return (time.perf_counter() - started) * 1000.0


def write_metrics(path: Path, rows: list[dict[str, object]]) -> None:
    if not rows:
        return
    fieldnames: list[str] = []
    for row in rows:
        for field in row:
            if field not in fieldnames:
                fieldnames.append(field)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    args = parse_args()
    source = args.source.resolve()
    model_dir = args.model_dir.resolve()
    output = args.output.resolve()
    model_path = model_dir / "model.onnx"
    config_path = model_dir / "config.json"
    processor_path = model_dir / "preprocessor_config.json"
    for required in (source, model_path, config_path, processor_path):
        if not required.exists():
            raise FileNotFoundError(required)

    output.mkdir(parents=True, exist_ok=True)
    with config_path.open("r", encoding="utf-8") as handle:
        model_config = json.load(handle)
    with processor_path.open("r", encoding="utf-8") as handle:
        processor_config = json.load(handle)
    category_indices = resolve_category_indices(model_config)

    options = ort.SessionOptions()
    options.intra_op_num_threads = max(1, int(args.threads))
    options.inter_op_num_threads = 1
    options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_BASIC
    model_load_started = time.perf_counter()
    session = ort.InferenceSession(
        str(model_path),
        sess_options=options,
        providers=["CPUExecutionProvider"],
    )
    model_load_ms = elapsed_ms(model_load_started)

    candidates = discover_images(source)
    items = select_sample(source, candidates, args.limit, args.raw_count)
    if not items:
        raise RuntimeError(f"No supported images found under {source}")

    metrics: list[dict[str, object]] = []
    failures: list[dict[str, str]] = []
    all_contact_entries: list[tuple[np.ndarray, str]] = []
    category_contacts: dict[str, list[tuple[np.ndarray, str]]] = {
        category: [] for category in CATEGORY_LABELS
    }
    started_all = time.perf_counter()

    for index, item in enumerate(items, start=1):
        item_started = time.perf_counter()
        display_name = str(item.relative_path)
        print(f"[{index:02d}/{len(items):02d}] {display_name}", flush=True)
        try:
            rgb, decode_ms = decode_preview(item.source, args.preview_long_edge)
            masks, inference_ms = infer_category_masks(
                session,
                rgb,
                processor_config,
                category_indices,
            )
            refine_started = time.perf_counter()
            refined = {}
            for category, mask in masks.items():
                guided = guided_filter(rgb, mask)
                if category == "sky":
                    guided = repair_sky_mask_boundaries(rgb, guided)
                elif category == "water":
                    guided = refine_water_mask_topology(guided)
                refined[category] = tighten_mask_confidence(guided)
            refine_ms = elapsed_ms(refine_started)

            item_dir = output / safe_output_name(index, item.relative_path)
            item_dir.mkdir(parents=True, exist_ok=True)
            Image.fromarray(rgb, mode="RGB").save(item_dir / "source.jpg", quality=92)
            all_overlay = all_category_overlay(rgb, refined)
            Image.fromarray(all_overlay, mode="RGB").save(item_dir / "all_overlay.jpg", quality=92)

            for category in CATEGORY_LABELS:
                save_mask(item_dir / f"mask_{category}_raw.png", masks[category])
                save_mask(item_dir / f"mask_{category}_refined.png", refined[category])
                category_overlay = overlay_mask(rgb, refined[category], CATEGORY_COLORS[category])
                category_contacts[category].append((category_overlay, display_name))

            make_review_panel(
                rgb,
                refined,
                display_name,
                adjusted=False,
            ).save(item_dir / "mask_review.jpg", quality=90)
            make_review_panel(
                rgb,
                refined,
                display_name,
                adjusted=True,
            ).save(item_dir / "halo_review.jpg", quality=90)
            all_contact_entries.append((all_overlay, display_name))

            row: dict[str, object] = {
                "index": index,
                "source": str(item.source),
                "relative_path": display_name,
                "suffix": item.source.suffix.casefold(),
                "width": rgb.shape[1],
                "height": rgb.shape[0],
                "decode_ms": round(decode_ms, 2),
                "inference_ms": round(inference_ms, 2),
                "refine_ms": round(refine_ms, 2),
                "total_ms": round(elapsed_ms(item_started), 2),
            }
            for category, mask in refined.items():
                row[f"{category}_mean"] = round(float(mask.mean()), 5)
                row[f"{category}_coverage_035"] = round(float(np.mean(mask >= 0.35)), 5)
            metrics.append(row)
        except Exception as exc:
            failures.append({"source": str(item.source), "error": str(exc)})
            print(f"  FAILED: {exc}", flush=True)

    make_contact_sheet(all_contact_entries, output / "contact_all_categories.jpg")
    for category, entries in category_contacts.items():
        make_contact_sheet(entries, output / f"contact_{category}.jpg")
    write_metrics(output / "timings.csv", metrics)

    successful_times = [float(row["inference_ms"]) for row in metrics]
    summary = {
        "source": str(source),
        "output": str(output),
        "model": "nvidia/segformer-b0-finetuned-ade-512-512",
        "model_revision": MODEL_REVISION,
        "model_sha256": file_sha256(model_path),
        "execution_provider": session.get_providers(),
        "threads": max(1, int(args.threads)),
        "model_load_ms": round(model_load_ms, 2),
        "candidate_count": len(candidates),
        "requested_count": len(items),
        "successful_count": len(metrics),
        "failure_count": len(failures),
        "wall_time_seconds": round(time.perf_counter() - started_all, 2),
        "inference_ms": {
            "median": round(float(np.median(successful_times)), 2) if successful_times else None,
            "p90": round(float(np.percentile(successful_times, 90)), 2) if successful_times else None,
            "maximum": round(max(successful_times), 2) if successful_times else None,
        },
        "categories": {
            category: {
                "labels": list(CATEGORY_LABELS[category]),
                "model_indices": category_indices[category],
                "color_rgb": list(CATEGORY_COLORS[category]),
            }
            for category in CATEGORY_LABELS
        },
        "failures": failures,
    }
    with (output / "summary.json").open("w", encoding="utf-8") as handle:
        json.dump(summary, handle, indent=2)

    print(json.dumps(summary, indent=2), flush=True)
    return 0 if metrics else 1


if __name__ == "__main__":
    raise SystemExit(main())
