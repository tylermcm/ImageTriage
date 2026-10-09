"""Screen-sized JPEG previews that PhotoCraft opens while a photo's RAW is still loading.

Reading a NEF's embedded camera JPEG costs a few MB of I/O, against the whole 50+ MB file, so
the preview appears at once even over a network share. Previews are kept next to the other
edit data so PhotoCraft's scoped read root covers them, and are reused on later visits.
"""
from __future__ import annotations

import hashlib
import logging
import os
import tempfile
from pathlib import Path

from PySide6.QtCore import QSize

PREVIEW_LONG_SIDE = 2560
PREVIEW_DIR = "previews"
PREVIEW_VERSION = 1
_JPEG_QUALITY = 90

_log = logging.getLogger(__name__)


def preview_path_for(path: str, edit_root: Path) -> Path:
    """Where this photo's preview lives; the name changes when the photo does."""
    source = Path(path)
    try:
        stat = source.stat()
        identity = f"{PREVIEW_VERSION}:{PREVIEW_LONG_SIDE}:{stat.st_size}:{stat.st_mtime_ns}"
    except OSError:
        identity = f"{PREVIEW_VERSION}:{PREVIEW_LONG_SIDE}:missing"
    key = hashlib.sha1(identity.encode()).hexdigest()[:12]
    return edit_root / PREVIEW_DIR / f"{source.name}.{key}.jpg"


def ensure_preview(path: str, edit_root: Path) -> Path:
    """The preview JPEG for ``path``, created from the camera's embedded JPEG when missing."""
    target = preview_path_for(path, edit_root)
    if target.is_file():
        return target
    from .imaging import load_image_for_display

    image, error = load_image_for_display(path, QSize(PREVIEW_LONG_SIDE, PREVIEW_LONG_SIDE), prefer_embedded=True)
    if image.isNull():
        raise RuntimeError(f"No preview could be made for {Path(path).name}: {error}")
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(dir=target.parent, suffix=".tmp")
    os.close(fd)
    temporary = Path(temporary_name)
    try:
        if not image.save(str(temporary), "JPEG", _JPEG_QUALITY):
            raise RuntimeError(f"Could not write the preview for {Path(path).name}")
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return target
