from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from PySide6.QtGui import QImage


@dataclass(slots=True, frozen=True)
class InspectionStats:
    width: int
    height: int
    mean_luminance: float
    median_luminance: float
    shadow_clip_pct: float
    highlight_clip_pct: float
    detail_score: float
    histogram_luma: tuple[int, ...]
    histogram_red: tuple[int, ...]
    histogram_green: tuple[int, ...]
    histogram_blue: tuple[int, ...]
    noise_score: float = 0.0
    motion_blur_score: float = 0.0
    detail_valid_tile_count: int = 0
    detail_tile_count: int = 0
    detail_tile_score_p75: float = 0.0
    detail_tile_score_p90: float = 0.0
    detail_confidence: float = 0.0


EMPTY_INSPECTION_STATS = InspectionStats(
    width=0,
    height=0,
    mean_luminance=0.0,
    median_luminance=0.0,
    shadow_clip_pct=0.0,
    highlight_clip_pct=0.0,
    detail_score=0.0,
    noise_score=0.0,
    motion_blur_score=0.0,
    histogram_luma=tuple(0 for _ in range(256)),
    histogram_red=tuple(0 for _ in range(256)),
    histogram_green=tuple(0 for _ in range(256)),
    histogram_blue=tuple(0 for _ in range(256)),
)


@dataclass(slots=True, frozen=True)
class _TileDetailStats:
    score: float
    valid_tile_count: int
    tile_count: int
    p75: float
    p90: float
    confidence: float


@dataclass(slots=True, frozen=True)
class FocusAssistColor:
    id: str
    label: str
    low_rgb: tuple[int, int, int]
    high_rgb: tuple[int, int, int]


@dataclass(slots=True, frozen=True)
class FocusAssistStrength:
    id: str
    label: str
    percentile: float
    gamma: float
    blend: float
    base_luma: float


FOCUS_ASSIST_COLORS = (
    FocusAssistColor(
        id="red",
        label="Red",
        low_rgb=(130, 34, 34),
        high_rgb=(255, 92, 76),
    ),
    FocusAssistColor(
        id="blue",
        label="Blue",
        low_rgb=(10, 40, 108),
        high_rgb=(52, 122, 230),
    ),
    FocusAssistColor(
        id="yellow",
        label="Yellow",
        low_rgb=(142, 116, 24),
        high_rgb=(255, 232, 94),
    ),
    FocusAssistColor(
        id="white",
        label="White",
        low_rgb=(182, 182, 182),
        high_rgb=(255, 255, 255),
    ),
)

DEFAULT_FOCUS_ASSIST_COLOR_ID = "blue"
FOCUS_ASSIST_STRENGTHS = (
    FocusAssistStrength(
        id="low",
        label="Low",
        percentile=88.0,
        gamma=0.80,
        blend=0.92,
        base_luma=0.34,
    ),
    FocusAssistStrength(
        id="medium",
        label="Medium",
        percentile=81.0,
        gamma=0.72,
        blend=0.96,
        base_luma=0.30,
    ),
    FocusAssistStrength(
        id="strong",
        label="Strong",
        percentile=74.0,
        gamma=0.64,
        blend=0.985,
        base_luma=0.26,
    ),
)
DEFAULT_FOCUS_ASSIST_STRENGTH_ID = "low"


def build_inspection_stats(image: QImage) -> InspectionStats:
    rgba = _qimage_to_rgba_array(image)
    if rgba.size == 0:
        return EMPTY_INSPECTION_STATS

    rgb = rgba[:, :, :3]
    luminance = _luminance_channel(rgb)

    histogram_red = np.bincount(rgb[:, :, 0].ravel(), minlength=256)
    histogram_green = np.bincount(rgb[:, :, 1].ravel(), minlength=256)
    histogram_blue = np.bincount(rgb[:, :, 2].ravel(), minlength=256)
    luma_8bit = np.clip(np.rint(luminance), 0, 255).astype(np.uint8)
    histogram_luma = np.bincount(luma_8bit.ravel(), minlength=256)
    detail = _tile_detail_stats(luminance)

    return InspectionStats(
        width=int(rgb.shape[1]),
        height=int(rgb.shape[0]),
        mean_luminance=float(np.mean(luminance)),
        median_luminance=float(np.median(luminance)),
        shadow_clip_pct=float(np.mean(luminance <= 6.0) * 100.0),
        highlight_clip_pct=float(np.mean(luminance >= 249.0) * 100.0),
        detail_score=detail.score,
        noise_score=_noise_score(luminance),
        motion_blur_score=_motion_blur_score(luminance, detail_score=detail.score),
        histogram_luma=tuple(int(value) for value in histogram_luma.tolist()),
        histogram_red=tuple(int(value) for value in histogram_red.tolist()),
        histogram_green=tuple(int(value) for value in histogram_green.tolist()),
        histogram_blue=tuple(int(value) for value in histogram_blue.tolist()),
        detail_valid_tile_count=detail.valid_tile_count,
        detail_tile_count=detail.tile_count,
        detail_tile_score_p75=detail.p75,
        detail_tile_score_p90=detail.p90,
        detail_confidence=detail.confidence,
    )


def focus_assist_color_by_id(color_id: str) -> FocusAssistColor:
    normalized = (color_id or "").strip().casefold()
    for color in FOCUS_ASSIST_COLORS:
        if color.id == normalized:
            return color
    return FOCUS_ASSIST_COLORS[1]


def focus_assist_strength_by_id(strength_id: str) -> FocusAssistStrength:
    normalized = (strength_id or "").strip().casefold()
    for strength in FOCUS_ASSIST_STRENGTHS:
        if strength.id == normalized:
            return strength
    return FOCUS_ASSIST_STRENGTHS[0]


def build_focus_assist_image(
    image: QImage,
    color: FocusAssistColor | str = DEFAULT_FOCUS_ASSIST_COLOR_ID,
    strength: FocusAssistStrength | str = DEFAULT_FOCUS_ASSIST_STRENGTH_ID,
    *,
    dim_background: bool = True,
) -> QImage:
    rgba = _qimage_to_rgba_array(image)
    if rgba.size == 0:
        return QImage()

    focus_color = focus_assist_color_by_id(color) if isinstance(color, str) else color
    focus_strength = focus_assist_strength_by_id(strength) if isinstance(strength, str) else strength
    rgb = rgba[:, :, :3].astype(np.float32)
    luminance = _luminance_channel(rgb)
    edge_strength = _edge_strength(luminance)
    peak_threshold = float(np.percentile(edge_strength, focus_strength.percentile))
    max_edge = float(edge_strength.max())
    if max_edge <= 0.0:
        return image

    normalized = np.clip((edge_strength - peak_threshold) / max(1.0, max_edge - peak_threshold), 0.0, 1.0)
    normalized = np.power(normalized, focus_strength.gamma).astype(np.float32, copy=False)

    if dim_background:
        base = np.clip(luminance * focus_strength.base_luma, 0.0, 255.0)
        base_rgb = np.stack((base, base, base), axis=-1)
    else:
        base_rgb = rgb.copy()
    low = np.asarray(focus_color.low_rgb, dtype=np.float32)
    high = np.asarray(focus_color.high_rgb, dtype=np.float32)
    highlight_rgb = low + ((high - low) * normalized[..., None])

    blend_strength = focus_strength.blend if dim_background else min(1.0, focus_strength.blend + 0.1)
    blend = np.clip(normalized[..., None] * blend_strength, 0.0, 1.0)
    output_rgb = np.clip(base_rgb * (1.0 - blend) + highlight_rgb * blend, 0.0, 255.0).astype(np.uint8)
    output = np.empty_like(rgba)
    output[:, :, :3] = output_rgb
    output[:, :, 3] = rgba[:, :, 3]
    return _rgba_array_to_qimage(output)


def _detail_score(luminance: np.ndarray) -> float:
    return _tile_detail_stats(luminance).score


def _tile_detail_stats(luminance: np.ndarray, *, grid_size: int = 8) -> _TileDetailStats:
    if luminance.size == 0:
        return _TileDetailStats(0.0, 0, 0, 0.0, 0.0, 0.0)

    height, width = luminance.shape
    if height < 16 or width < 16:
        return _TileDetailStats(0.0, 0, 0, 0.0, 0.0, 0.0)

    tile_scores: list[float] = []
    tile_count = 0
    y_edges = np.linspace(0, height, grid_size + 1, dtype=np.int32)
    x_edges = np.linspace(0, width, grid_size + 1, dtype=np.int32)
    for y_index in range(grid_size):
        y0 = int(y_edges[y_index])
        y1 = int(y_edges[y_index + 1])
        if y1 - y0 < 8:
            continue
        for x_index in range(grid_size):
            x0 = int(x_edges[x_index])
            x1 = int(x_edges[x_index + 1])
            if x1 - x0 < 8:
                continue
            tile_count += 1
            tile = luminance[y0:y1, x0:x1].astype(np.float32, copy=False)
            mean_luma = float(np.mean(tile))
            if mean_luma < 18.0 or mean_luma > 238.0:
                continue
            contrast = float(np.percentile(tile, 95.0) - np.percentile(tile, 5.0))
            if contrast < 12.0:
                continue

            edge_strength = _edge_strength(tile)
            if edge_strength.size == 0:
                continue
            strong_edges = float(np.percentile(edge_strength, 90.0))
            mean_edges = float(np.mean(edge_strength))
            if strong_edges < 4.0 and mean_edges < 2.0:
                continue

            padded = np.pad(tile, 1, mode="edge")
            laplacian = (
                (4.0 * padded[1:-1, 1:-1])
                - padded[:-2, 1:-1]
                - padded[2:, 1:-1]
                - padded[1:-1, :-2]
                - padded[1:-1, 2:]
            )
            sobel_score = (strong_edges * 0.70 + mean_edges * 0.30) / 2.6
            laplacian_score = (float(np.var(laplacian)) ** 0.5) / 2.8
            tile_scores.append(min(100.0, max(0.0, max(sobel_score, laplacian_score))))

    if not tile_scores:
        return _TileDetailStats(0.0, 0, tile_count, 0.0, 0.0, 0.0)

    scores = np.asarray(tile_scores, dtype=np.float32)
    valid_count = int(scores.size)
    p75 = float(np.percentile(scores, 75.0))
    p90 = float(np.percentile(scores, 90.0))
    top_count = max(1, int(np.ceil(valid_count * 0.25)))
    top_average = float(np.mean(np.sort(scores)[-top_count:]))
    score = min(100.0, max(0.0, (p90 * 0.50) + (top_average * 0.35) + (p75 * 0.15)))
    confidence = min(100.0, (valid_count / 12.0) * 100.0)
    return _TileDetailStats(score, valid_count, tile_count, p75, p90, confidence)


def _noise_score(luminance: np.ndarray) -> float:
    if luminance.size == 0:
        return 0.0
    padded = np.pad(luminance.astype(np.float32), 1, mode="edge")
    local_mean = (
        padded[:-2, :-2]
        + padded[:-2, 1:-1]
        + padded[:-2, 2:]
        + padded[1:-1, :-2]
        + padded[1:-1, 1:-1]
        + padded[1:-1, 2:]
        + padded[2:, :-2]
        + padded[2:, 1:-1]
        + padded[2:, 2:]
    ) / 9.0
    residual = np.abs(luminance.astype(np.float32) - local_mean)
    flat_mask = _edge_strength(luminance) < 5.0
    sample = residual[flat_mask] if np.any(flat_mask) else residual
    return min(100.0, max(0.0, float(np.percentile(sample, 90.0)) * 6.0))


def _motion_blur_score(luminance: np.ndarray, *, detail_score: float | None = None) -> float:
    edge_strength = _edge_strength(luminance)
    if edge_strength.size == 0:
        return 0.0
    vertical_diff = np.abs(np.diff(luminance.astype(np.float32), axis=0))
    horizontal_diff = np.abs(np.diff(luminance.astype(np.float32), axis=1))
    vertical_mean = float(np.mean(vertical_diff)) if vertical_diff.size else 0.0
    horizontal_mean = float(np.mean(horizontal_diff)) if horizontal_diff.size else 0.0
    strongest = max(vertical_mean, horizontal_mean)
    weakest = min(vertical_mean, horizontal_mean)
    directional_bias = (strongest - weakest) / strongest if strongest > 0 else 0.0
    if detail_score is None:
        detail_score = _detail_score(luminance)
    weak_detail = max(0.0, 1.0 - (detail_score / 55.0))
    return min(100.0, max(0.0, (weak_detail * 70.0) + (directional_bias * 30.0)))


def _edge_strength(luminance: np.ndarray) -> np.ndarray:
    padded = np.pad(luminance.astype(np.float32), 1, mode="edge")
    gx = (
        padded[:-2, 2:]
        + (2.0 * padded[1:-1, 2:])
        + padded[2:, 2:]
        - padded[:-2, :-2]
        - (2.0 * padded[1:-1, :-2])
        - padded[2:, :-2]
    )
    gy = (
        padded[2:, :-2]
        + (2.0 * padded[2:, 1:-1])
        + padded[2:, 2:]
        - padded[:-2, :-2]
        - (2.0 * padded[:-2, 1:-1])
        - padded[:-2, 2:]
    )
    return np.hypot(gx, gy)


def _luminance_channel(rgb: np.ndarray) -> np.ndarray:
    rgb_float = rgb.astype(np.float32, copy=False)
    return (
        rgb_float[:, :, 0] * 0.2126
        + rgb_float[:, :, 1] * 0.7152
        + rgb_float[:, :, 2] * 0.0722
    )


def _qimage_to_rgba_array(image: QImage) -> np.ndarray:
    if image.isNull():
        return np.empty((0, 0, 4), dtype=np.uint8)
    converted = image.convertToFormat(QImage.Format.Format_RGBA8888)
    width = converted.width()
    height = converted.height()
    if width <= 0 or height <= 0:
        return np.empty((0, 0, 4), dtype=np.uint8)

    buffer = converted.constBits()
    try:
        buffer.setsize(converted.sizeInBytes())
    except AttributeError:
        pass
    array = np.frombuffer(buffer, dtype=np.uint8)
    array = array.reshape((height, converted.bytesPerLine()))
    array = array[:, : width * 4]
    return array.reshape((height, width, 4)).copy()


def _rgba_array_to_qimage(array: np.ndarray) -> QImage:
    if array.size == 0:
        return QImage()
    contiguous = np.ascontiguousarray(array, dtype=np.uint8)
    height, width, _channels = contiguous.shape
    image = QImage(contiguous.data, width, height, width * 4, QImage.Format.Format_RGBA8888)
    return image.copy()
