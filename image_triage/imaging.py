from __future__ import annotations

import os

from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QSize, Qt
from PySide6.QtGui import QImage, QImageReader

from .models import RAW_SUFFIXES

try:
    import rawpy
except ImportError:  # pragma: no cover - depends on local environment
    rawpy = None


def load_image_for_display(path: str, target_size: QSize, *, prefer_embedded: bool):
    suffix = os.path.splitext(path)[1].lower()
    if suffix in RAW_SUFFIXES:
        return _load_raw_image(path, target_size, prefer_embedded=prefer_embedded, suffix=suffix)
    return _load_standard_image(path, target_size)


def _load_standard_image(path: str, target_size: QSize) -> tuple[QImage, str | None]:
    reader = QImageReader(path)
    reader.setAutoTransform(True)
    source_size = reader.size()
    if source_size.isValid() and _has_target(target_size):
        scaled = source_size.scaled(target_size, Qt.AspectRatioMode.KeepAspectRatio)
        if scaled.isValid():
            reader.setScaledSize(scaled)

    image = reader.read()
    if image.isNull():
        return QImage(), reader.errorString()

    return _scale_if_needed(image, target_size), None


def _load_raw_image(path: str, target_size: QSize, *, prefer_embedded: bool, suffix: str) -> tuple[QImage, str | None]:
    if rawpy is None:
        return QImage(), "RAW support requires the rawpy package."

    try:
        with rawpy.imread(path) as raw:
            use_embedded_preview = prefer_embedded and suffix != ".dng"
            if use_embedded_preview:
                embedded = _load_embedded_thumbnail(raw, target_size)
                if embedded is not None:
                    return embedded, None

            image = _postprocess_raw(raw, target_size, high_quality=not prefer_embedded)
            if image.isNull():
                return QImage(), "Could not decode RAW image."
            return image, None
    except Exception as exc:  # pragma: no cover - library/runtime path
        return QImage(), str(exc)


def _load_embedded_thumbnail(raw, target_size: QSize) -> QImage | None:
    try:
        thumb = raw.extract_thumb()
    except Exception:
        return None

    if thumb.format == rawpy.ThumbFormat.JPEG:
        image = _load_standard_image_from_bytes(bytes(thumb.data), target_size)
    elif thumb.format == rawpy.ThumbFormat.BITMAP:
        image = _qimage_from_rgb_array(thumb.data)
    else:
        return None

    if image.isNull():
        return None
    return _scale_if_needed(image, target_size)


def _load_standard_image_from_bytes(payload: bytes, target_size: QSize) -> QImage:
    byte_array = QByteArray(payload)
    buffer = QBuffer(byte_array)
    if not buffer.open(QIODevice.OpenModeFlag.ReadOnly):
        return QImage()

    reader = QImageReader(buffer)
    reader.setAutoTransform(True)
    source_size = reader.size()
    if source_size.isValid() and _has_target(target_size):
        scaled = source_size.scaled(target_size, Qt.AspectRatioMode.KeepAspectRatio)
        if scaled.isValid():
            reader.setScaledSize(scaled)

    image = reader.read()
    buffer.close()
    if image.isNull():
        return QImage()
    return _scale_if_needed(image, target_size)


def _postprocess_raw(raw, target_size: QSize, *, high_quality: bool) -> QImage:
    options = {
        "use_camera_wb": True,
        "output_bps": 8,
        "auto_bright_thr": 0.01,
    }
    if not high_quality:
        options["half_size"] = True
        options["demosaic_algorithm"] = rawpy.DemosaicAlgorithm.LINEAR

    rgb = raw.postprocess(**options)
    image = _qimage_from_rgb_array(rgb)
    if image.isNull():
        return image
    return _scale_if_needed(image, target_size)


def _qimage_from_rgb_array(rgb) -> QImage:
    if rgb is None or len(getattr(rgb, "shape", ())) < 3:
        return QImage()

    height, width, channels = rgb.shape
    if channels < 3:
        return QImage()

    if not rgb.flags["C_CONTIGUOUS"]:
        rgb = rgb.copy(order="C")

    if channels == 3:
        image = QImage(rgb.data, width, height, rgb.strides[0], QImage.Format.Format_RGB888)
    else:
        image = QImage(rgb.data, width, height, rgb.strides[0], QImage.Format.Format_RGBA8888)
    return image.copy()


def _scale_if_needed(image: QImage, target_size: QSize) -> QImage:
    if image.isNull():
        return image
    if not _has_target(target_size):
        return image
    if image.width() <= target_size.width() and image.height() <= target_size.height():
        return image
    return image.scaled(
        target_size,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )


def _has_target(target_size: QSize) -> bool:
    return target_size.isValid() and target_size.width() > 0 and target_size.height() > 0
