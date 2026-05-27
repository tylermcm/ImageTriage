"""Preview helpers for the local labeling UI."""

from __future__ import annotations

from functools import lru_cache
import os
from pathlib import Path
import sys
from typing import Callable, Optional

from PIL import Image, ImageOps
from PIL.ImageQt import ImageQt
from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QImage, QPixmap, QTransform

try:  # pragma: no cover - optional runtime dependency
    import exifread
except Exception:  # pragma: no cover - optional runtime dependency
    exifread = None


_HOST_ROOT_ENV = "IMAGE_TRIAGE_HOST_ROOT"


def load_oriented_pixmap(path: Path, *, max_side: Optional[int] = None) -> QPixmap:
    """Load an oriented preview pixmap, optionally downscaled for faster UI rendering."""

    return _load_oriented_pixmap_cached(str(path), max_side or 0)


@lru_cache(maxsize=64)
def _load_oriented_pixmap_cached(path_text: str, max_side: int) -> QPixmap:
    """Cache oriented preview pixmaps by path and requested preview size."""

    image = _load_with_image_triage(path_text, max_side)
    if image is not None and not image.isNull():
        return QPixmap.fromImage(image)

    try:
        with Image.open(path_text) as image:
            oriented = ImageOps.exif_transpose(image)
            if max_side > 0:
                oriented.thumbnail((max_side, max_side), _pillow_lanczos())
            oriented = oriented.convert("RGBA")
            qt_image = ImageQt(oriented)
    except (OSError, ValueError):
        return QPixmap()

    return QPixmap.fromImage(qt_image)


def _load_with_image_triage(path_text: str, max_side: int) -> QImage | None:
    loader = _resolve_image_triage_loader()
    if loader is None:
        return None

    target_size = QSize(max_side, max_side) if max_side > 0 else None
    try:
        image, _error = loader(path_text, target_size=target_size)
    except Exception:
        return None

    if image.isNull():
        return None
    return _apply_source_orientation(path_text, image)


def _apply_source_orientation(path_text: str, image: QImage) -> QImage:
    """Correct raw embedded previews when the extracted JPEG lacks orientation."""

    orientation = _source_orientation(path_text)
    if orientation not in {2, 3, 4, 5, 6, 7, 8}:
        return image
    if orientation in {5, 6, 7, 8} and image.height() > image.width():
        return image

    transform = QTransform()
    if orientation == 2:
        transform.scale(-1, 1)
    elif orientation == 3:
        transform.rotate(180)
    elif orientation == 4:
        transform.scale(1, -1)
    elif orientation == 5:
        transform.rotate(90)
        transform.scale(-1, 1)
    elif orientation == 6:
        transform.rotate(90)
    elif orientation == 7:
        transform.rotate(270)
        transform.scale(-1, 1)
    elif orientation == 8:
        transform.rotate(270)

    corrected = image.transformed(transform, Qt.TransformationMode.SmoothTransformation)
    return corrected if not corrected.isNull() else image


@lru_cache(maxsize=512)
def _source_orientation(path_text: str) -> int:
    """Read the source EXIF orientation tag without decoding the full image."""

    if exifread is not None:
        try:
            with open(path_text, "rb") as stream:
                tags = exifread.process_file(stream, details=False, stop_tag="Image Orientation")
        except Exception:
            tags = {}
        value = tags.get("Image Orientation") or tags.get("EXIF Orientation")
        numeric = _orientation_to_int(value)
        if numeric is not None:
            return numeric

    try:
        with Image.open(path_text) as image:
            numeric = _orientation_to_int(image.getexif().get(274))
    except Exception:
        numeric = None
    return numeric or 1


def _orientation_to_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        numeric_values = getattr(value, "values", None)
        if numeric_values:
            return int(numeric_values[0])
        return int(value)  # type: ignore[arg-type]
    except Exception:
        text = str(value).casefold()
    if "180" in text:
        return 3
    if "270" in text or "ccw" in text or "left" in text:
        return 8
    if "90" in text or "cw" in text or "right" in text:
        return 6
    return None


@lru_cache(maxsize=1)
def _resolve_image_triage_loader() -> Callable[..., tuple[QImage, str | None]] | None:
    host_root_text = os.environ.get(_HOST_ROOT_ENV, "").strip()
    if not host_root_text:
        return None

    host_root = Path(host_root_text).expanduser().resolve()
    if not host_root.exists():
        return None

    if str(host_root) not in sys.path:
        sys.path.insert(0, str(host_root))

    try:
        from image_triage.imaging import load_image_for_display
    except Exception:
        return None

    def _load_preview(path_text: str, *, target_size: QSize | None = None) -> tuple[QImage, str | None]:
        return load_image_for_display(
            path_text,
            target_size or QSize(),
            prefer_embedded=True,
        )

    return _load_preview


def _pillow_lanczos():
    resampling = getattr(Image, "Resampling", None)
    if resampling is not None:
        return resampling.LANCZOS
    return Image.LANCZOS
