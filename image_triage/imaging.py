from __future__ import annotations

import math
import os
import struct

import numpy as np
from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QPointF, QSize, Qt
from PySide6.QtGui import QColor, QImage, QImageReader, QPainter, QPen, QPolygonF

from .formats import MODEL_SUFFIXES, PILLOW_FALLBACK_SUFFIXES, PSD_SUFFIXES, RAW_SUFFIXES, suffix_for_path

try:
    import rawpy
except ImportError:  # pragma: no cover - depends on local environment
    rawpy = None

try:
    from PIL import Image, ImageOps
except ImportError:  # pragma: no cover - depends on local environment
    Image = None
    ImageOps = None
else:  # pragma: no cover - plugin registration depends on local environment
    try:
        import pillow_heif
    except ImportError:
        pillow_heif = None
    else:
        pillow_heif.register_heif_opener()

try:
    from psd_tools import PSDImage
except ImportError:  # pragma: no cover - depends on local environment
    PSDImage = None


_DEFAULT_THUMBNAIL_SIZE = QSize(512, 512)
_MAX_STL_TRIANGLES = 5000


def load_image_for_display(path: str, target_size: QSize, *, prefer_embedded: bool):
    suffix = suffix_for_path(path)
    if suffix in MODEL_SUFFIXES:
        return _load_stl_image(path, target_size)
    if suffix in RAW_SUFFIXES:
        return _load_raw_image(path, target_size, prefer_embedded=prefer_embedded, suffix=suffix)
    if suffix in PSD_SUFFIXES:
        image, error = _load_psd_image(path, target_size)
        if not image.isNull():
            return image, None
        return _load_with_fallbacks(path, target_size, initial_error=error)
    return _load_with_fallbacks(path, target_size)


def load_image_for_resize(path: str, *, target_size: QSize | None = None, ignore_orientation: bool = False) -> tuple[QImage, str | None]:
    suffix = suffix_for_path(path)
    requested_size = target_size if target_size is not None and _has_target(target_size) else QSize()
    if suffix in MODEL_SUFFIXES:
        return QImage(), "This file type cannot be resized yet."
    if suffix in RAW_SUFFIXES:
        return _load_raw_image(path, requested_size, prefer_embedded=False, suffix=suffix)
    if suffix in PSD_SUFFIXES:
        image, error = _load_psd_image(path, requested_size, auto_transform=not ignore_orientation)
        if not image.isNull():
            return image, None
        return _load_with_fallbacks(
            path,
            requested_size,
            initial_error=error,
            auto_transform=not ignore_orientation,
        )
    return _load_with_fallbacks(path, requested_size, auto_transform=not ignore_orientation)


def _load_with_fallbacks(
    path: str,
    target_size: QSize,
    *,
    initial_error: str | None = None,
    auto_transform: bool = True,
) -> tuple[QImage, str | None]:
    image, error = _load_standard_image(path, target_size, auto_transform=auto_transform)
    if not image.isNull():
        return image, None

    if Image is not None:
        pillow_image, pillow_error = _load_pillow_image(path, target_size, auto_transform=auto_transform)
        if not pillow_image.isNull():
            return pillow_image, None
        if initial_error:
            return QImage(), initial_error
        return QImage(), pillow_error or error

    if initial_error:
        return QImage(), initial_error

    suffix = suffix_for_path(path)
    if suffix in PILLOW_FALLBACK_SUFFIXES:
        return QImage(), error or "Additional codecs are required for this format."
    return QImage(), error


def _load_standard_image(path: str, target_size: QSize, *, auto_transform: bool = True) -> tuple[QImage, str | None]:
    reader = QImageReader(path)
    reader.setAutoTransform(auto_transform)
    source_size = reader.size()
    if source_size.isValid() and _qt_decode_likely_exceeds_allocation_limit(source_size):
        return QImage(), "Qt image decoder skipped due allocation limit."
    if source_size.isValid() and _has_target(target_size):
        scaled = source_size.scaled(target_size, Qt.AspectRatioMode.KeepAspectRatio)
        if scaled.isValid():
            reader.setScaledSize(scaled)

    image = reader.read()
    if image.isNull():
        return QImage(), reader.errorString()

    return _scale_if_needed(image, target_size), None


def _load_psd_image(path: str, target_size: QSize, *, auto_transform: bool = True) -> tuple[QImage, str | None]:
    if PSDImage is None:
        return _load_pillow_image(path, target_size, auto_transform=auto_transform)

    try:
        psd = PSDImage.open(path)
        composite = psd.composite()
        return _qimage_from_pillow_image(composite, target_size, auto_transform=auto_transform), None
    except Exception as exc:  # pragma: no cover - library/runtime path
        return QImage(), str(exc)


def _load_pillow_image(path: str, target_size: QSize, *, auto_transform: bool = True) -> tuple[QImage, str | None]:
    if Image is None:
        return QImage(), "Extended format support requires Pillow-based codecs."

    try:
        with Image.open(path) as image:
            return _qimage_from_pillow_image(image, target_size, auto_transform=auto_transform), None
    except Exception as exc:  # pragma: no cover - library/runtime path
        return QImage(), str(exc)


def _qimage_from_pillow_image(image, target_size: QSize, *, auto_transform: bool = True) -> QImage:
    if Image is None:
        return QImage()

    working = image
    if getattr(working, "is_animated", False):
        try:
            working.seek(0)
        except EOFError:
            return QImage()

    if auto_transform and ImageOps is not None:
        working = ImageOps.exif_transpose(working)
    else:
        working = working.copy()

    working.load()
    if working.mode != "RGBA":
        working = working.convert("RGBA")
    else:
        working = working.copy()

    if _has_target(target_size):
        width = max(1, target_size.width())
        height = max(1, target_size.height())
        working.thumbnail((width, height), _pillow_lanczos())

    qimage = QImage(
        working.tobytes("raw", "RGBA"),
        working.width,
        working.height,
        working.width * 4,
        QImage.Format.Format_RGBA8888,
    )
    return qimage.copy()


def _qt_decode_likely_exceeds_allocation_limit(source_size: QSize) -> bool:
    if not source_size.isValid():
        return False
    try:
        limit_mb = int(QImageReader.allocationLimit())
    except Exception:
        return False
    if limit_mb <= 0:
        return False
    width = max(0, int(source_size.width()))
    height = max(0, int(source_size.height()))
    if width <= 0 or height <= 0:
        return False
    estimated_bytes = width * height * 4
    return estimated_bytes > (limit_mb * 1024 * 1024)


def _pillow_lanczos():
    if Image is None:
        return None
    resampling = getattr(Image, "Resampling", None)
    if resampling is not None:
        return resampling.LANCZOS
    return Image.LANCZOS


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

            quality_mode = "fast" if prefer_embedded else "balanced" if _should_use_half_size(raw, target_size) else "high"
            image = _postprocess_raw(raw, target_size, quality_mode=quality_mode)
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


def _postprocess_raw(raw, target_size: QSize, *, quality_mode: str) -> QImage:
    options = {
        "use_camera_wb": True,
        "output_bps": 8,
        "auto_bright_thr": 0.01,
    }
    if quality_mode in {"balanced", "fast"}:
        options["half_size"] = True
    if quality_mode == "fast":
        options["demosaic_algorithm"] = rawpy.DemosaicAlgorithm.LINEAR

    rgb = raw.postprocess(**options)
    image = _qimage_from_rgb_array(rgb)
    if image.isNull():
        return image
    return _scale_if_needed(image, target_size)


def _should_use_half_size(raw, target_size: QSize) -> bool:
    if not _has_target(target_size):
        return False
    source_width, source_height = _raw_output_dimensions(raw)
    if source_width <= 0 or source_height <= 0:
        return False
    desired = QSize(source_width, source_height).scaled(target_size, Qt.AspectRatioMode.KeepAspectRatio)
    desired_width = max(1, desired.width())
    desired_height = max(1, desired.height())
    half_width = max(1, source_width // 2)
    half_height = max(1, source_height // 2)
    return half_width >= int(desired_width * 1.15) and half_height >= int(desired_height * 1.15)


def _raw_output_dimensions(raw) -> tuple[int, int]:
    sizes = getattr(raw, "sizes", None)
    if sizes is None:
        return 0, 0
    for width_name, height_name in (("width", "height"), ("iwidth", "iheight"), ("raw_width", "raw_height")):
        width = int(getattr(sizes, width_name, 0) or 0)
        height = int(getattr(sizes, height_name, 0) or 0)
        if width > 0 and height > 0:
            return width, height
    return 0, 0


def _load_stl_image(path: str, target_size: QSize) -> tuple[QImage, str | None]:
    try:
        triangles, normals = _load_stl_mesh(path)
    except OSError as exc:
        return QImage(), str(exc)
    except Exception as exc:  # pragma: no cover - parser/runtime path
        return QImage(), str(exc)

    if triangles.size == 0:
        return QImage(), "The STL file did not contain any triangles."

    image = _render_stl_mesh(triangles, normals, target_size)
    if image.isNull():
        return QImage(), "Could not render STL thumbnail."
    return image, None


def _load_stl_mesh(path: str) -> tuple[np.ndarray, np.ndarray]:
    with open(path, "rb") as stream:
        payload = stream.read()

    if len(payload) >= 84:
        triangle_count = struct.unpack_from("<I", payload, 80)[0]
        expected_size = 84 + triangle_count * 50
        if triangle_count > 0 and expected_size == len(payload):
            return _parse_binary_stl(payload, triangle_count)
    return _parse_ascii_stl(payload.decode("utf-8", errors="ignore"))


def _parse_binary_stl(payload: bytes, triangle_count: int) -> tuple[np.ndarray, np.ndarray]:
    record_type = np.dtype(
        [
            ("normal", "<f4", (3,)),
            ("vertices", "<f4", (3, 3)),
            ("attribute", "<u2"),
        ]
    )
    records = np.frombuffer(payload, dtype=record_type, offset=84, count=triangle_count)
    triangles = np.array(records["vertices"], dtype=np.float32, copy=True)
    normals = np.array(records["normal"], dtype=np.float32, copy=True)
    return triangles, normals


def _parse_ascii_stl(text: str) -> tuple[np.ndarray, np.ndarray]:
    triangles: list[tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]]] = []
    normals: list[tuple[float, float, float]] = []
    current_normal = (0.0, 0.0, 1.0)
    current_vertices: list[tuple[float, float, float]] = []

    for raw_line in text.splitlines():
        parts = raw_line.strip().split()
        if len(parts) >= 5 and parts[0].lower() == "facet" and parts[1].lower() == "normal":
            try:
                current_normal = (float(parts[2]), float(parts[3]), float(parts[4]))
            except ValueError:
                current_normal = (0.0, 0.0, 1.0)
            continue
        if len(parts) >= 4 and parts[0].lower() == "vertex":
            try:
                current_vertices.append((float(parts[1]), float(parts[2]), float(parts[3])))
            except ValueError:
                current_vertices.clear()
                continue
            if len(current_vertices) == 3:
                triangles.append((current_vertices[0], current_vertices[1], current_vertices[2]))
                normals.append(current_normal)
                current_vertices = []

    if not triangles:
        return np.empty((0, 3, 3), dtype=np.float32), np.empty((0, 3), dtype=np.float32)

    return np.asarray(triangles, dtype=np.float32), np.asarray(normals, dtype=np.float32)


def _render_stl_mesh(triangles: np.ndarray, normals: np.ndarray, target_size: QSize) -> QImage:
    if triangles.shape[0] > _MAX_STL_TRIANGLES:
        step = math.ceil(triangles.shape[0] / _MAX_STL_TRIANGLES)
        triangles = triangles[::step]
        normals = normals[::step]

    normals = _normalize_normals(triangles, normals)
    rotated_triangles, rotated_normals = _rotate_mesh(triangles, normals)
    projected = rotated_triangles[:, :, :2]
    bounds = projected.reshape(-1, 2)
    min_xy = bounds.min(axis=0)
    max_xy = bounds.max(axis=0)

    canvas = _effective_target_size(target_size)
    width = max(96, canvas.width())
    height = max(96, canvas.height())
    padding = max(10, min(width, height) // 12)
    span_x = max(float(max_xy[0] - min_xy[0]), 1e-6)
    span_y = max(float(max_xy[1] - min_xy[1]), 1e-6)
    scale = min((width - padding * 2) / span_x, (height - padding * 2) / span_y)
    center_xy = (min_xy + max_xy) / 2.0

    projected = projected.copy()
    projected[:, :, 0] = (projected[:, :, 0] - center_xy[0]) * scale + (width / 2.0)
    projected[:, :, 1] = (height / 2.0) - ((projected[:, :, 1] - center_xy[1]) * scale)

    light_direction = np.array([0.35, -0.45, 0.82], dtype=np.float32)
    light_direction /= np.linalg.norm(light_direction)
    lighting = np.clip(rotated_normals @ light_direction, -1.0, 1.0)
    lighting = 0.28 + (np.maximum(lighting, 0.0) * 0.72)
    depths = rotated_triangles[:, :, 2].mean(axis=1)
    draw_order = np.argsort(depths)

    image = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)

    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    outline_width = max(0.6, min(width, height) / 320.0)
    outline = QPen(QColor(18, 22, 28, 120), outline_width)
    painter.setPen(outline)

    for index in draw_order:
        shade = float(lighting[index])
        fill = QColor(
            int(112 + (shade * 78)),
            int(129 + (shade * 86)),
            int(147 + (shade * 92)),
            238,
        )
        polygon = QPolygonF(
            [
                QPointF(float(projected[index, 0, 0]), float(projected[index, 0, 1])),
                QPointF(float(projected[index, 1, 0]), float(projected[index, 1, 1])),
                QPointF(float(projected[index, 2, 0]), float(projected[index, 2, 1])),
            ]
        )
        painter.setBrush(fill)
        painter.drawPolygon(polygon)

    painter.end()
    return image


def _normalize_normals(triangles: np.ndarray, normals: np.ndarray) -> np.ndarray:
    computed = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
    computed_lengths = np.linalg.norm(computed, axis=1)
    valid_computed = computed_lengths > 1e-6
    safe_computed = np.zeros_like(computed)
    safe_computed[valid_computed] = computed[valid_computed] / computed_lengths[valid_computed, None]

    if normals.shape != safe_computed.shape:
        return safe_computed

    normalized = np.array(normals, dtype=np.float32, copy=True)
    lengths = np.linalg.norm(normalized, axis=1)
    valid = lengths > 1e-6
    normalized[valid] = normalized[valid] / lengths[valid, None]
    normalized[~valid] = safe_computed[~valid]
    return normalized


def _rotate_mesh(triangles: np.ndarray, normals: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    centered = triangles - triangles.reshape(-1, 3).mean(axis=0)

    pitch = math.radians(33.0)
    yaw = math.radians(-40.0)
    cos_pitch = math.cos(pitch)
    sin_pitch = math.sin(pitch)
    cos_yaw = math.cos(yaw)
    sin_yaw = math.sin(yaw)

    rotation_y = np.array(
        [
            [cos_yaw, 0.0, sin_yaw],
            [0.0, 1.0, 0.0],
            [-sin_yaw, 0.0, cos_yaw],
        ],
        dtype=np.float32,
    )
    rotation_x = np.array(
        [
            [1.0, 0.0, 0.0],
            [0.0, cos_pitch, -sin_pitch],
            [0.0, sin_pitch, cos_pitch],
        ],
        dtype=np.float32,
    )
    rotation = rotation_x @ rotation_y
    return centered @ rotation.T, normals @ rotation.T


def _effective_target_size(target_size: QSize) -> QSize:
    if _has_target(target_size):
        return target_size
    return _DEFAULT_THUMBNAIL_SIZE


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
