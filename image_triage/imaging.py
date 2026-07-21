from __future__ import annotations

import math
import os
import struct
import time
from dataclasses import dataclass
from typing import Callable

import numpy as np
from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QPointF, QSize, Qt
from PySide6.QtGui import QColor, QImage, QImageReader, QPainter, QPen, QPolygonF, QTransform

from .fits_support import (
    is_basic_displayable_fits_array,
    load_basic_fits_image,
    normalize_basic_fits_array,
)
from .formats import (
    FITS_SUFFIXES,
    JPEG_SUFFIXES,
    MODEL_SUFFIXES,
    PILLOW_FALLBACK_SUFFIXES,
    PSD_SUFFIXES,
    RAW_SUFFIXES,
    is_appledouble_path,
    suffix_for_path,
)
from .perf import perf_logger
from .plugins import DisplayLoadRequest, register_display_provider, resolve_display_provider
from .raw_embedded_jpeg import extract_embedded_jpeg

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
_BYTES_PER_MIB = 1024 * 1024
THUMBNAIL_SKIP_PSD_BYTES = 100 * _BYTES_PER_MIB
THUMBNAIL_SKIP_GENERAL_BYTES = 500 * _BYTES_PER_MIB
THUMBNAIL_SKIP_MAX_PIXELS = 160_000_000
TIFF_SUFFIXES = frozenset({".tif", ".tiff"})
_ASTROPY_IMPORT_ATTEMPTED = False
_ASTROPY_FITS = None
_ASTROPY_ZSCALE_INTERVAL = None
_BUILTIN_DISPLAY_PROVIDERS_REGISTERED = False
_JPEG_EXIF_MAX_SCAN_BYTES = 2 * 1024 * 1024
_JPEG_EXIF_ORIENTATION = 0x0112
_JPEG_EXIF_TYPE_SHORT = 3


@dataclass(slots=True, frozen=True)
class FitsStfPreset:
    id: str
    label: str
    asinh_strength: float


@dataclass(slots=True, frozen=True)
class FitsDisplaySettings:
    stf_preset_id: str = "auto"

    @property
    def preset(self) -> FitsStfPreset:
        return fits_stf_preset_by_id(self.stf_preset_id)

    def cache_key(self) -> tuple[str]:
        return (self.preset.id,)


FITS_STF_PRESETS: tuple[FitsStfPreset, ...] = (
    FitsStfPreset("auto", "Auto STF", 6.0),
    FitsStfPreset("linear", "Linear", 0.0),
    FitsStfPreset("soft", "Soft", 3.0),
    FitsStfPreset("medium", "Medium", 6.0),
    FitsStfPreset("strong", "Strong", 12.0),
)


def fits_stf_preset_by_id(value: str) -> FitsStfPreset:
    for preset in FITS_STF_PRESETS:
        if preset.id == value:
            return preset
    return FITS_STF_PRESETS[0]


def normalize_fits_display_settings(settings: FitsDisplaySettings | None) -> FitsDisplaySettings:
    if settings is None:
        return FitsDisplaySettings()
    return FitsDisplaySettings(stf_preset_id=fits_stf_preset_by_id(settings.stf_preset_id).id)


@dataclass(slots=True, frozen=True)
class _SuffixDisplayProvider:
    provider_id: str
    suffixes: frozenset[str]
    loader: Callable[[DisplayLoadRequest], tuple[QImage, str | None]]

    def can_handle_display(self, request: DisplayLoadRequest) -> bool:
        return request.suffix in self.suffixes

    def load_for_display(self, request: DisplayLoadRequest) -> tuple[QImage, str | None]:
        return self.loader(request)


@dataclass(slots=True, frozen=True)
class _DefaultDisplayProvider:
    provider_id: str = "default"

    def can_handle_display(self, request: DisplayLoadRequest) -> bool:
        return True

    def load_for_display(self, request: DisplayLoadRequest) -> tuple[QImage, str | None]:
        return _load_with_fallbacks(request.path, request.target_size)


def _load_model_display(request: DisplayLoadRequest) -> tuple[QImage, str | None]:
    return _load_stl_image(request.path, request.target_size)


def _load_fits_display(request: DisplayLoadRequest) -> tuple[QImage, str | None]:
    return _load_fits_image(
        request.path,
        request.target_size,
        fits_display_settings=request.fits_display_settings,
    )


def _load_raw_display(request: DisplayLoadRequest) -> tuple[QImage, str | None]:
    return _load_raw_image(
        request.path,
        request.target_size,
        prefer_embedded=request.prefer_embedded,
        suffix=request.suffix,
    )


def _load_psd_display(request: DisplayLoadRequest) -> tuple[QImage, str | None]:
    image, error = _load_psd_image(request.path, request.target_size)
    if not image.isNull():
        return image, None
    return _load_with_fallbacks(request.path, request.target_size, initial_error=error)


def _ensure_builtin_display_providers() -> None:
    global _BUILTIN_DISPLAY_PROVIDERS_REGISTERED
    if _BUILTIN_DISPLAY_PROVIDERS_REGISTERED:
        return
    register_display_provider(
        _SuffixDisplayProvider(
            provider_id="model",
            suffixes=MODEL_SUFFIXES,
            loader=_load_model_display,
        )
    )
    register_display_provider(
        _SuffixDisplayProvider(
            provider_id="fits",
            suffixes=FITS_SUFFIXES,
            loader=_load_fits_display,
        )
    )
    register_display_provider(
        _SuffixDisplayProvider(
            provider_id="raw",
            suffixes=RAW_SUFFIXES,
            loader=_load_raw_display,
        )
    )
    register_display_provider(
        _SuffixDisplayProvider(
            provider_id="psd",
            suffixes=PSD_SUFFIXES,
            loader=_load_psd_display,
        )
    )
    register_display_provider(_DefaultDisplayProvider())
    _BUILTIN_DISPLAY_PROVIDERS_REGISTERED = True


def display_provider_id_for_path(path: str) -> str:
    request = DisplayLoadRequest(
        path=path,
        suffix=suffix_for_path(path),
        target_size=QSize(),
        prefer_embedded=False,
    )
    _ensure_builtin_display_providers()
    provider = resolve_display_provider(request)
    return provider.provider_id if provider is not None else ""


def load_image_for_display(
    path: str,
    target_size: QSize,
    *,
    prefer_embedded: bool,
    fits_display_settings: FitsDisplaySettings | None = None,
):
    if is_appledouble_path(path):
        return QImage(), "macOS metadata sidecar; not an image file."
    request = DisplayLoadRequest(
        path=path,
        suffix=suffix_for_path(path),
        target_size=target_size,
        prefer_embedded=prefer_embedded,
        fits_display_settings=fits_display_settings,
    )
    logger = perf_logger()
    start = time.perf_counter() if logger.enabled else 0.0
    provider_id = ""
    _ensure_builtin_display_providers()
    try:
        provider = resolve_display_provider(request)
        if provider is None:
            provider_id = "fallback"
            image, error = _load_with_fallbacks(path, target_size)
        else:
            provider_id = provider.provider_id
            image, error = provider.load_for_display(request)
    except Exception as exc:
        if logger.enabled:
            logger.duration(
                "image.load_display.failed",
                (time.perf_counter() - start) * 1000.0,
                path=path,
                suffix=request.suffix,
                provider=provider_id,
                target_width=target_size.width(),
                target_height=target_size.height(),
                prefer_embedded=prefer_embedded,
                error=str(exc),
            )
        raise
    if logger.enabled:
        logger.duration(
            "image.load_display",
            (time.perf_counter() - start) * 1000.0,
            path=path,
            suffix=request.suffix,
            provider=provider_id,
            target_width=target_size.width(),
            target_height=target_size.height(),
            prefer_embedded=prefer_embedded,
            image_width=image.width(),
            image_height=image.height(),
            status="failed" if image.isNull() else "ready",
            error=error or "",
        )
    return image, error


def thumbnail_skip_reason(path: str, target_size: QSize) -> str:
    """Return a user-facing reason when thumbnail decode should be skipped."""
    suffix = suffix_for_path(path)
    try:
        file_size = os.path.getsize(path)
    except OSError:
        file_size = 0

    if suffix in PSD_SUFFIXES and file_size > THUMBNAIL_SKIP_PSD_BYTES:
        return "Large PSD placeholder"
    if file_size > THUMBNAIL_SKIP_GENERAL_BYTES:
        return "Large file placeholder"

    if suffix not in PSD_SUFFIXES:
        source_size = QImageReader(path).size()
        if source_size.isValid():
            pixels = int(source_size.width()) * int(source_size.height())
            if pixels > THUMBNAIL_SKIP_MAX_PIXELS and _has_target(target_size):
                return "Large image placeholder"
    return ""


def sanitize_display_error(message: str | None, *, path: str = "") -> str:
    """Collapse decoder-specific failures into short text safe for tile UI."""
    raw = (message or "").strip()
    if not raw:
        return "Could not decode image."

    lowered = raw.casefold()
    suffix = suffix_for_path(path) if path else ""
    if "qt image decoder skipped due allocation limit" in lowered:
        return "Image is too large to preview safely."
    if suffix in PSD_SUFFIXES and (
        "not a psd" in lowered
        or "not a psb" in lowered
        or "psd" in lowered
        or "psb" in lowered
    ):
        return "Could not decode PSD composite."
    if "cannot identify image file" in lowered:
        return "Could not decode image."
    if "unsupported file format or not raw file" in lowered:
        return "File is not a valid RAW image."
    if "truncated" in lowered or "broken data stream" in lowered:
        return "Image file appears incomplete or corrupt."
    if "permission" in lowered or "access is denied" in lowered:
        return "Could not access image file."
    if len(raw) > 160:
        return raw[:157].rstrip() + "..."
    return raw


def load_image_for_resize(path: str, *, target_size: QSize | None = None, ignore_orientation: bool = False) -> tuple[QImage, str | None]:
    if is_appledouble_path(path):
        return QImage(), "macOS metadata sidecar; not an image file."
    suffix = suffix_for_path(path)
    requested_size = target_size if target_size is not None and _has_target(target_size) else QSize()
    if suffix in MODEL_SUFFIXES:
        return QImage(), "This file type cannot be resized yet."
    if suffix in FITS_SUFFIXES:
        return QImage(), "FITS files are view-only for now."
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
    suffix = suffix_for_path(path)
    if suffix in TIFF_SUFFIXES and Image is not None:
        pillow_image, pillow_error = _load_pillow_image(path, target_size, auto_transform=auto_transform)
        if not pillow_image.isNull():
            return pillow_image, None
        if initial_error:
            return QImage(), initial_error
        image, error = _load_standard_image(path, target_size, auto_transform=auto_transform)
        if not image.isNull():
            return image, None
        return QImage(), pillow_error or error

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

    if suffix in PILLOW_FALLBACK_SUFFIXES:
        return QImage(), error or "Additional codecs are required for this format."
    return QImage(), error


def _load_standard_image(path: str, target_size: QSize, *, auto_transform: bool = True) -> tuple[QImage, str | None]:
    reader = QImageReader(path)
    source_size = reader.size()
    reader.setAutoTransform(auto_transform)
    if source_size.isValid() and _qt_decode_likely_exceeds_allocation_limit(source_size):
        return QImage(), "Qt image decoder skipped due allocation limit."
    if source_size.isValid() and _has_target(target_size):
        scaled = source_size.scaled(target_size, Qt.AspectRatioMode.KeepAspectRatio)
        if scaled.isValid():
            reader.setScaledSize(scaled)

    image = reader.read()
    if image.isNull():
        return QImage(), reader.errorString()

    if auto_transform:
        image = _apply_jpeg_orientation_fallback(path, image, source_size)

    return _scale_if_needed(image, target_size), None


def _apply_jpeg_orientation_fallback(path: str, image: QImage, source_size: QSize) -> QImage:
    """Rotate EXIF-oriented JPEGs when Qt leaves the decoded pixels unrotated."""

    if image.isNull() or suffix_for_path(path) not in JPEG_SUFFIXES:
        return image
    orientation = _read_jpeg_exif_orientation(path)
    if orientation not in (5, 6, 7, 8):
        return image
    # Most camera portrait JPEGs are stored as landscape pixels plus EXIF
    # orientation. If Qt already honored the tag, the decoded image is portrait;
    # if it did not, rotate it here. Avoid using QImageReader.size() for this
    # decision because some Qt plugins report the transformed size there.
    if image.height() > image.width():
        return image
    return _apply_exif_orientation(image, orientation)


def _read_jpeg_exif_orientation(path: str) -> int:
    try:
        with open(path, "rb") as stream:
            if stream.read(2) != b"\xff\xd8":
                return 1
            scanned = 2
            while scanned < _JPEG_EXIF_MAX_SCAN_BYTES:
                marker_prefix = stream.read(1)
                scanned += 1
                if marker_prefix != b"\xff":
                    return 1
                marker = stream.read(1)
                scanned += 1
                while marker == b"\xff":
                    marker = stream.read(1)
                    scanned += 1
                if not marker:
                    return 1
                marker_value = marker[0]
                if marker_value in {0x01, *range(0xD0, 0xD9)}:
                    continue
                length_data = stream.read(2)
                scanned += 2
                if len(length_data) != 2:
                    return 1
                segment_length = struct.unpack(">H", length_data)[0]
                if segment_length < 2:
                    return 1
                payload_length = segment_length - 2
                if marker_value == 0xE1:
                    payload = stream.read(payload_length)
                    scanned += payload_length
                    orientation = _jpeg_exif_orientation_from_payload(payload)
                    if orientation != 1:
                        return orientation
                    continue
                stream.seek(payload_length, os.SEEK_CUR)
                scanned += payload_length
    except (OSError, struct.error):
        return 1
    return 1


def _jpeg_exif_orientation_from_payload(payload: bytes) -> int:
    if len(payload) < 14 or not payload.startswith(b"Exif\x00\x00"):
        return 1
    tiff = payload[6:]
    if tiff[:2] == b"II":
        endian = "<"
    elif tiff[:2] == b"MM":
        endian = ">"
    else:
        return 1
    if len(tiff) < 8 or struct.unpack(endian + "H", tiff[2:4])[0] != 42:
        return 1
    first_ifd = struct.unpack(endian + "I", tiff[4:8])[0]
    entry_count_offset = first_ifd
    if entry_count_offset + 2 > len(tiff):
        return 1
    entry_count = struct.unpack(endian + "H", tiff[entry_count_offset : entry_count_offset + 2])[0]
    entries_start = entry_count_offset + 2
    entries_end = entries_start + entry_count * 12
    if entry_count <= 0 or entries_end > len(tiff):
        return 1
    for offset in range(entries_start, entries_end, 12):
        entry = tiff[offset : offset + 12]
        tag, value_type, value_count = struct.unpack(endian + "HHI", entry[:8])
        if tag != _JPEG_EXIF_ORIENTATION:
            continue
        if value_type != _JPEG_EXIF_TYPE_SHORT or value_count < 1:
            return 1
        orientation = struct.unpack(endian + "H", entry[8:10])[0]
        return orientation if 1 <= orientation <= 8 else 1
    return 1


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


def _import_astropy_modules():
    global _ASTROPY_IMPORT_ATTEMPTED, _ASTROPY_FITS, _ASTROPY_ZSCALE_INTERVAL
    if not _ASTROPY_IMPORT_ATTEMPTED:
        _ASTROPY_IMPORT_ATTEMPTED = True
        try:
            from astropy.io import fits as astropy_fits
            from astropy.visualization import ZScaleInterval
        except ImportError:
            _ASTROPY_FITS = None
            _ASTROPY_ZSCALE_INTERVAL = None
        else:  # pragma: no branch - exercised when dependency is installed
            _ASTROPY_FITS = astropy_fits
            _ASTROPY_ZSCALE_INTERVAL = ZScaleInterval
    return _ASTROPY_FITS, _ASTROPY_ZSCALE_INTERVAL


def _load_fits_image(
    path: str,
    target_size: QSize,
    *,
    fits_display_settings: FitsDisplaySettings | None = None,
) -> tuple[QImage, str | None]:
    astropy_fits, zscale_interval = _import_astropy_modules()
    display_settings = normalize_fits_display_settings(fits_display_settings)
    astropy_error: str | None = None

    if astropy_fits is not None and zscale_interval is not None:
        try:
            with astropy_fits.open(path, memmap=True, lazy_load_hdus=True) as hdul:
                data = _first_fits_display_data(hdul)
        except Exception as exc:  # pragma: no cover - depends on local FITS parser/runtime
            astropy_error = str(exc)
        else:
            if data is not None:
                try:
                    image = _qimage_from_fits_data(
                        data,
                        target_size=target_size,
                        zscale_interval=zscale_interval,
                        fits_display_settings=display_settings,
                    )
                except Exception as exc:  # pragma: no cover - depends on source data/runtime
                    astropy_error = str(exc)
                else:
                    if not image.isNull():
                        return image, None
                    astropy_error = "Could not render FITS image."
            else:
                astropy_error = "The FITS file did not contain displayable image data."

    basic_image, basic_error = load_basic_fits_image(path)
    if basic_image is not None:
        try:
            image = _qimage_from_fits_data(
                basic_image.data,
                target_size=target_size,
                zscale_interval=_fallback_zscale_interval,
                fits_display_settings=display_settings,
            )
        except Exception as exc:  # pragma: no cover - depends on source data/runtime
            basic_error = str(exc)
        else:
            if not image.isNull():
                return image, None
            basic_error = "Could not render FITS image."

    return QImage(), basic_error or astropy_error or "FITS support requires the astropy package."


def _first_fits_display_data(hdul) -> np.ndarray | None:
    for hdu in hdul:
        if not getattr(hdu, "is_image", True):
            continue
        data = getattr(hdu, "data", None)
        if data is None:
            continue
        array = np.asarray(data)
        if array.size <= 0 or not is_basic_displayable_fits_array(array):
            continue
        return normalize_basic_fits_array(array)
    return None


def _qimage_from_fits_data(
    data: np.ndarray,
    *,
    target_size: QSize,
    zscale_interval,
    fits_display_settings: FitsDisplaySettings,
) -> QImage:
    working = np.asarray(data)
    while working.ndim > 3:
        working = working[0]

    if working.ndim == 3:
        if working.shape[-1] in {3, 4}:
            return _qimage_from_rgb_array(
                _normalize_fits_rgb(working, target_size, fits_display_settings=fits_display_settings)
            )
        if working.shape[0] in {3, 4}:
            return _qimage_from_rgb_array(
                _normalize_fits_rgb(
                    np.moveaxis(working, 0, -1),
                    target_size,
                    fits_display_settings=fits_display_settings,
                )
            )
        working = working[0]

    if working.ndim == 1:
        working = working[np.newaxis, :]
    if working.ndim != 2:
        return QImage()

    normalized = _normalize_fits_grayscale(
        working,
        target_size,
        zscale_interval=zscale_interval,
        fits_display_settings=fits_display_settings,
    )
    if normalized.size <= 0:
        return QImage()
    image = QImage(
        normalized.data,
        int(normalized.shape[1]),
        int(normalized.shape[0]),
        int(normalized.strides[0]),
        QImage.Format.Format_Grayscale8,
    )
    return image.copy()


def _normalize_fits_rgb(
    array: np.ndarray,
    target_size: QSize,
    *,
    fits_display_settings: FitsDisplaySettings,
) -> np.ndarray:
    working = np.asarray(array, dtype=np.float32)
    working = _downsample_fits_array(working, target_size)
    if working.ndim != 3 or working.shape[-1] < 3:
        return np.zeros((1, 1, 3), dtype=np.uint8)
    working = working[..., :3]
    normalized_channels: list[np.ndarray] = []
    for index in range(working.shape[-1]):
        normalized_channels.append(
            _normalize_fits_channel(working[..., index], fits_display_settings=fits_display_settings)
        )
    return np.stack(normalized_channels, axis=-1)


def _normalize_fits_grayscale(
    array: np.ndarray,
    target_size: QSize,
    *,
    zscale_interval,
    fits_display_settings: FitsDisplaySettings,
) -> np.ndarray:
    working = np.asarray(array, dtype=np.float32)
    working = _downsample_fits_array(working, target_size)
    if working.size <= 0:
        return np.zeros((1, 1), dtype=np.uint8)

    finite = np.isfinite(working)
    if not finite.any():
        return np.zeros(working.shape[:2], dtype=np.uint8)

    values = working[finite]
    try:
        interval = zscale_interval(n_samples=min(4000, max(256, int(values.size))))
        normalized = interval(working, clip=True)
    except Exception:
        low = float(np.nanpercentile(values, 1.0))
        high = float(np.nanpercentile(values, 99.5))
        if not math.isfinite(low) or not math.isfinite(high) or abs(high - low) < 1e-9:
            low = float(np.nanmin(values))
            high = float(np.nanmax(values))
        if not math.isfinite(low) or not math.isfinite(high) or abs(high - low) < 1e-9:
            return np.zeros(working.shape[:2], dtype=np.uint8)
        normalized = np.clip((working - low) / (high - low), 0.0, 1.0)

    stretched = _apply_fits_stf(normalized, fits_display_settings=fits_display_settings)
    return np.ascontiguousarray(np.clip(stretched * 255.0, 0.0, 255.0).astype(np.uint8))


def _normalize_fits_channel(channel: np.ndarray, *, fits_display_settings: FitsDisplaySettings) -> np.ndarray:
    finite = np.isfinite(channel)
    if not finite.any():
        return np.zeros(channel.shape[:2], dtype=np.uint8)
    values = channel[finite]
    low = float(np.nanpercentile(values, 1.0))
    high = float(np.nanpercentile(values, 99.5))
    if not math.isfinite(low) or not math.isfinite(high) or abs(high - low) < 1e-9:
        low = float(np.nanmin(values))
        high = float(np.nanmax(values))
    if not math.isfinite(low) or not math.isfinite(high) or abs(high - low) < 1e-9:
        return np.zeros(channel.shape[:2], dtype=np.uint8)
    normalized = np.clip((channel - low) / (high - low), 0.0, 1.0)
    normalized = _apply_fits_stf(normalized, fits_display_settings=fits_display_settings)
    return np.ascontiguousarray((normalized * 255.0).astype(np.uint8))


def _apply_fits_stf(normalized: np.ndarray, *, fits_display_settings: FitsDisplaySettings) -> np.ndarray:
    settings = normalize_fits_display_settings(fits_display_settings)
    clipped = np.nan_to_num(np.clip(normalized, 0.0, 1.0), nan=0.0, posinf=1.0, neginf=0.0)
    strength = settings.preset.asinh_strength
    if strength <= 0.0:
        return clipped
    stretched = np.arcsinh(clipped * strength) / math.asinh(strength)
    return np.nan_to_num(np.clip(stretched, 0.0, 1.0), nan=0.0, posinf=1.0, neginf=0.0)


def _fallback_zscale_interval(*, n_samples: int):
    class _PercentileInterval:
        def __call__(self, array: np.ndarray, clip: bool = True) -> np.ndarray:
            working = np.asarray(array, dtype=np.float32)
            finite = np.isfinite(working)
            if not finite.any():
                return np.zeros(working.shape, dtype=np.float32)
            values = working[finite]
            low = float(np.nanpercentile(values, 1.0))
            high = float(np.nanpercentile(values, 99.5))
            if not math.isfinite(low) or not math.isfinite(high) or abs(high - low) < 1e-9:
                low = float(np.nanmin(values))
                high = float(np.nanmax(values))
            if not math.isfinite(low) or not math.isfinite(high) or abs(high - low) < 1e-9:
                return np.zeros(working.shape, dtype=np.float32)
            normalized = (working - low) / (high - low)
            if clip:
                normalized = np.clip(normalized, 0.0, 1.0)
            return normalized

    return _PercentileInterval()


def _downsample_fits_array(array: np.ndarray, target_size: QSize) -> np.ndarray:
    if not _has_target(target_size) or array.ndim < 2:
        return np.ascontiguousarray(array)
    height = max(1, int(array.shape[0]))
    width = max(1, int(array.shape[1]))
    step_y = max(1, math.ceil(height / max(1, target_size.height() * 2)))
    step_x = max(1, math.ceil(width / max(1, target_size.width() * 2)))
    slices = (slice(None, None, step_y), slice(None, None, step_x))
    if array.ndim == 2:
        return np.ascontiguousarray(array[slices])
    return np.ascontiguousarray(array[slices[0], slices[1], ...])


def _load_raw_image(path: str, target_size: QSize, *, prefer_embedded: bool, suffix: str) -> tuple[QImage, str | None]:
    if prefer_embedded:
        logger = perf_logger()
        start = time.perf_counter() if logger.enabled else 0.0
        embedded_jpeg = extract_embedded_jpeg(path)
        if logger.enabled:
            logger.duration(
                "raw.embedded_jpeg_extract",
                (time.perf_counter() - start) * 1000.0,
                path=path,
                status="hit" if embedded_jpeg is not None else "miss",
                source=embedded_jpeg.source if embedded_jpeg is not None else "",
                jpeg_bytes=embedded_jpeg.byte_count if embedded_jpeg is not None else 0,
            )
        if embedded_jpeg is not None:
            # Skip QImageReader auto-transform — most Nikon NEF embedded
            # JPEGs don't carry their own orientation EXIF, and applying the
            # container's tag 0x0112 below is the authoritative source.
            # (For containers where the embedded JPEG DOES carry its own
            # orientation, the IFD walker reads the same value from 0x0112
            # so we still apply the correct rotation once.)
            image = _load_standard_image_from_bytes(
                embedded_jpeg.payload, target_size, apply_exif_transform=False
            )
            if not image.isNull():
                image = _apply_exif_orientation(image, embedded_jpeg.orientation)
                return _scale_if_needed(image, target_size), None

    if rawpy is None:
        return QImage(), "RAW support requires the rawpy package."

    try:
        with rawpy.imread(path) as raw:
            use_embedded_preview = prefer_embedded
            if use_embedded_preview:
                embedded = _load_embedded_thumbnail(raw, target_size)
                if embedded is not None:
                    return embedded, None
                return QImage(), "No fast embedded RAW preview available."

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
        # Nikon NEFs (and others) sometimes strip the orientation EXIF tag
        # from the embedded JPEG preview because the camera knows it should
        # render the preview using the raw container's orientation field.
        # Skip QImageReader's auto-transform here so we don't double-rotate
        # when the JPEG DOES carry orientation, then apply raw.sizes.flip
        # below as the authoritative source.
        image = _load_standard_image_from_bytes(
            bytes(thumb.data), target_size, apply_exif_transform=False
        )
    elif thumb.format == rawpy.ThumbFormat.BITMAP:
        image = _qimage_from_rgb_array(thumb.data)
    else:
        return None

    if image.isNull():
        return None
    image = _apply_raw_orientation(image, getattr(raw.sizes, "flip", 0))
    return _scale_if_needed(image, target_size)


def _apply_exif_orientation(image: QImage, orientation: int) -> QImage:
    """Apply the standard EXIF orientation field (1-8) to an image.

      1: no rotation
      2: flip horizontal
      3: 180° rotation
      4: flip vertical
      5: transpose (90° CCW + flip horizontal)
      6: 90° CW
      7: transverse (90° CW + flip horizontal)
      8: 90° CCW

    Used by the fast-path TIFF/IFD JPEG extractor, which reads tag 0x0112
    from the RAW container — Nikon NEFs carry the camera-recorded
    orientation here even when the embedded preview JPEG itself doesn't.
    """

    if image.isNull() or orientation == 1 or orientation < 1 or orientation > 8:
        return image
    transform = QTransform()
    if orientation == 2:
        transform.scale(-1.0, 1.0)
    elif orientation == 3:
        transform.rotate(180)
    elif orientation == 4:
        transform.scale(1.0, -1.0)
    elif orientation == 5:
        transform.rotate(90)
        transform.scale(-1.0, 1.0)
    elif orientation == 6:
        transform.rotate(90)
    elif orientation == 7:
        transform.rotate(-90)
        transform.scale(-1.0, 1.0)
    elif orientation == 8:
        transform.rotate(-90)
    rotated = image.transformed(transform, Qt.TransformationMode.SmoothTransformation)
    return rotated if not rotated.isNull() else image


def _apply_raw_orientation(image: QImage, flip: int) -> QImage:
    """Apply the camera-recorded orientation that libraw / rawpy expose via
    ``raw.sizes.flip``. Values follow the standard EXIF orientation scheme:

      0 - no rotation (sensor was held in landscape)
      3 - 180° (camera upside-down)
      5 - 90° counter-clockwise (camera held right side up, right edge down)
      6 - 90° clockwise (camera held right side up, left edge down)

    The embedded JPEG preview in many Nikon NEFs doesn't carry its own
    orientation EXIF tag, and the raw BITMAP / postprocessed pixel buffers
    carry no orientation metadata at all — so this is where portrait shots
    need to actually become portrait."""

    if image.isNull() or flip in (0, None):
        return image
    transform = QTransform()
    if flip == 3:
        transform.rotate(180)
    elif flip == 5:
        transform.rotate(-90)
    elif flip == 6:
        transform.rotate(90)
    else:
        return image
    rotated = image.transformed(transform, Qt.TransformationMode.SmoothTransformation)
    return rotated if not rotated.isNull() else image


def _load_standard_image_from_bytes(
    payload: bytes,
    target_size: QSize,
    *,
    apply_exif_transform: bool = True,
) -> QImage:
    byte_array = QByteArray(payload)
    buffer = QBuffer(byte_array)
    if not buffer.open(QIODevice.OpenModeFlag.ReadOnly):
        return QImage()

    reader = QImageReader(buffer)
    reader.setAutoTransform(apply_exif_transform)
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
    # postprocess returns sensor-orientation pixels; apply the camera-recorded
    # orientation the same way the embedded-preview path does so portrait raws
    # actually render portrait.
    image = _apply_raw_orientation(image, getattr(raw.sizes, "flip", 0))
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
