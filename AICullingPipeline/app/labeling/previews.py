"""Preview helpers for the local labeling UI."""

from __future__ import annotations

from functools import lru_cache
import os
from pathlib import Path
import sys
from typing import Callable, Optional

from PIL import Image, ImageOps
from PIL.ImageQt import ImageQt
from PySide6.QtCore import QSize
from PySide6.QtGui import QImage, QPixmap


_HOST_ROOT_ENV = "IMAGE_TRIAGE_HOST_ROOT"


def load_oriented_pixmap(path: Path, *, max_side: Optional[int] = None) -> QPixmap:
    """Load an oriented preview pixmap, optionally downscaled for faster UI rendering."""

    return _load_oriented_pixmap_cached(str(path), max_side or 0)


@lru_cache(maxsize=256)
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
        image, _error = loader(path_text, target_size=target_size, ignore_orientation=False)
    except Exception:
        return None

    if image.isNull():
        return None
    return image


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
        from image_triage.imaging import load_image_for_resize
    except Exception:
        return None
    return load_image_for_resize


def _pillow_lanczos():
    resampling = getattr(Image, "Resampling", None)
    if resampling is not None:
        return resampling.LANCZOS
    return Image.LANCZOS
