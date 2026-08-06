"""Non-destructive refinements for generated bitmap masks."""
from __future__ import annotations

from typing import Any

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage


REFINEMENT_DEFAULTS: dict[str, float] = {
    "edgeDetectionRadius": 0.0,
    "edgeSmooth": 0.0,
    "edgeFeather": 0.0,
    "edgeContrast": 0.0,
    "edgeShift": 0.0,
}

def has_mask_refinements(params: dict[str, Any]) -> bool:
    return any(
        abs(float(params.get(key, default) or 0.0)) > 1e-6
        for key, default in REFINEMENT_DEFAULTS.items()
    )


def _qimage_gray_array(image: QImage) -> np.ndarray:
    converted = image.convertToFormat(QImage.Format.Format_Grayscale8)
    values = np.frombuffer(bytes(converted.constBits()), dtype=np.uint8).reshape(
        converted.height(), converted.bytesPerLine()
    )
    return values[:, : converted.width()].copy()


def _qimage_rgb_array(image: QImage) -> np.ndarray:
    converted = image.convertToFormat(QImage.Format.Format_RGB888)
    values = np.frombuffer(bytes(converted.constBits()), dtype=np.uint8).reshape(
        converted.height(), converted.bytesPerLine()
    )
    return values[:, : converted.width() * 3].reshape(
        converted.height(), converted.width(), 3
    ).copy()


def _gray_array_qimage(values: np.ndarray) -> QImage:
    pixels = np.ascontiguousarray(np.clip(values, 0.0, 1.0) * 255.0, dtype=np.uint8)
    return QImage(
        pixels.data,
        pixels.shape[1],
        pixels.shape[0],
        pixels.strides[0],
        QImage.Format.Format_Grayscale8,
    ).copy()


def _scaled_radius(value: float, scale: float, *, maximum: int) -> int:
    if value <= 0.0:
        return 0
    return max(1, min(maximum, int(round(value * max(1e-6, scale)))))


def _box_mean(values: np.ndarray, radius: int) -> np.ndarray:
    """Return a reflected-border box mean without optional image libraries."""
    if radius <= 0:
        return np.asarray(values, dtype=np.float32).copy()
    source = np.asarray(values, dtype=np.float32)
    padded = np.pad(source, ((radius, radius), (radius, radius)), mode="reflect")
    integral = np.pad(padded, ((1, 0), (1, 0)), mode="constant")
    integral = np.cumsum(integral, axis=0, dtype=np.float32)
    integral = np.cumsum(integral, axis=1, dtype=np.float32)
    kernel = radius * 2 + 1
    sums = (
        integral[kernel:, kernel:]
        - integral[:-kernel, kernel:]
        - integral[kernel:, :-kernel]
        + integral[:-kernel, :-kernel]
    )
    return sums / float(kernel * kernel)


def _soft_box_blur(values: np.ndarray, radius: int, *, passes: int = 3) -> np.ndarray:
    """Approximate a Gaussian blur with fast dependency-free box passes."""
    result = np.asarray(values, dtype=np.float32)
    pass_radius = max(1, int(round(radius / max(1.0, passes**0.5))))
    for _ in range(passes):
        result = _box_mean(result, pass_radius)
    return result


def _extreme_filter(values: np.ndarray, radius: int, *, maximum: bool) -> np.ndarray:
    """Apply a separable square max/min filter for edge expansion/contraction."""
    source = np.asarray(values, dtype=np.float32)
    if radius <= 0:
        return source.copy()
    width_padded = np.pad(source, ((0, 0), (radius, radius)), mode="edge")
    width_windows = np.lib.stride_tricks.sliding_window_view(
        width_padded, radius * 2 + 1, axis=1
    )
    horizontal = (
        np.max(width_windows, axis=-1)
        if maximum
        else np.min(width_windows, axis=-1)
    )
    height_padded = np.pad(horizontal, ((radius, radius), (0, 0)), mode="edge")
    height_windows = np.lib.stride_tricks.sliding_window_view(
        height_padded, radius * 2 + 1, axis=0
    )
    return (
        np.max(height_windows, axis=-1)
        if maximum
        else np.min(height_windows, axis=-1)
    )


def _guided_filter(guide_rgb: np.ndarray, mask: np.ndarray, radius: int) -> np.ndarray:
    rgb = guide_rgb.astype(np.float32) / 255.0
    guide = rgb[..., 0] * 0.299 + rgb[..., 1] * 0.587 + rgb[..., 2] * 0.114
    source = mask.astype(np.float32, copy=False)
    mean_i = _box_mean(guide, radius)
    mean_p = _box_mean(source, radius)
    variance_i = _box_mean(guide * guide, radius) - mean_i * mean_i
    covariance_ip = _box_mean(guide * source, radius) - mean_i * mean_p
    coefficient = covariance_ip / (variance_i + 1e-3)
    intercept = mean_p - coefficient * mean_i
    return np.clip(
        _box_mean(coefficient, radius) * guide + _box_mean(intercept, radius),
        0.0,
        1.0,
    )


def refine_mask_array(
    mask: np.ndarray,
    *,
    guide_rgb: np.ndarray | None = None,
    edge_radius: float = 0.0,
    smooth: float = 0.0,
    feather: float = 0.0,
    contrast: float = 0.0,
    shift_edge: float = 0.0,
    scale: float = 1.0,
) -> np.ndarray:
    """Apply Photoshop-style generated-mask controls to a 0..1 strength map."""
    values = np.clip(np.asarray(mask, dtype=np.float32), 0.0, 1.0)
    edge_px = _scaled_radius(edge_radius, scale, maximum=64)
    if edge_px and guide_rgb is not None and guide_rgb.shape[:2] == values.shape:
        values = _guided_filter(guide_rgb, values, edge_px)

    smooth_px = _scaled_radius(float(smooth) * 0.20, scale, maximum=15)
    if smooth_px:
        averaged = _box_mean(values, smooth_px)
        values = np.clip((averaged - 0.25) * 2.0, 0.0, 1.0)
        values = values * values * (3.0 - 2.0 * values)

    feather_px = _scaled_radius(feather, scale, maximum=256)
    if feather_px:
        # Feather generated selections outward: retain every existing strength
        # value and add only the blurred falloff beyond the current boundary.
        values = np.maximum(values, _soft_box_blur(values, feather_px))

    contrast = max(0.0, min(100.0, float(contrast)))
    if contrast:
        factor = 1.0 + contrast / 20.0
        values = np.clip((values - 0.5) * factor + 0.5, 0.0, 1.0)

    shift_edge = max(-100.0, min(100.0, float(shift_edge)))
    if abs(shift_edge) > 1e-6:
        reference_radius = max(float(edge_radius), 20.0)
        shift_px = _scaled_radius(
            reference_radius * abs(shift_edge) / 100.0,
            scale,
            maximum=31,
        )
        if shift_px:
            values = _extreme_filter(
                values,
                shift_px,
                maximum=shift_edge > 0.0,
            )

    return np.clip(values, 0.0, 1.0)


def refine_bitmap_qimage(
    bitmap: QImage,
    params: dict[str, Any],
    *,
    guide_image: QImage | None = None,
    scale: float = 1.0,
) -> QImage:
    if bitmap.isNull() or not has_mask_refinements(params):
        return bitmap.convertToFormat(QImage.Format.Format_Grayscale8)
    source_size = bitmap.size()
    working_bitmap = bitmap.convertToFormat(QImage.Format.Format_Grayscale8)
    working_scale = scale
    requested_feather_px = (
        float(params.get("edgeFeather", 0.0) or 0.0) * max(1e-6, scale)
    )
    # Large source-pixel feathers are evaluated on a proportional working mask.
    # This preserves their visual radius without allocating enormous padded
    # arrays during full-resolution export.
    if requested_feather_px > 192.0:
        ratio = 192.0 / requested_feather_px
        target_width = max(1, int(round(bitmap.width() * ratio)))
        target_height = max(1, int(round(bitmap.height() * ratio)))
        working_bitmap = working_bitmap.scaled(
            target_width,
            target_height,
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        working_scale *= ratio
    guide_rgb = None
    if guide_image is not None and not guide_image.isNull():
        guide = guide_image
        if guide.size() != working_bitmap.size():
            guide = guide.scaled(
                working_bitmap.size(),
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        guide_rgb = _qimage_rgb_array(guide)
    refined = refine_mask_array(
        _qimage_gray_array(working_bitmap).astype(np.float32) / 255.0,
        guide_rgb=guide_rgb,
        edge_radius=float(params.get("edgeDetectionRadius", 0.0) or 0.0),
        smooth=float(params.get("edgeSmooth", 0.0) or 0.0),
        feather=float(params.get("edgeFeather", 0.0) or 0.0),
        contrast=float(params.get("edgeContrast", 0.0) or 0.0),
        shift_edge=float(params.get("edgeShift", 0.0) or 0.0),
        scale=working_scale,
    )
    result = _gray_array_qimage(refined)
    if result.size() != source_size:
        result = result.scaled(
            source_size,
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
    return result


__all__ = [
    "REFINEMENT_DEFAULTS",
    "has_mask_refinements",
    "refine_bitmap_qimage",
    "refine_mask_array",
]
