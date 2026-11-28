"""Pictures for the quick viewer, made fast: only what the screen shows, and only the modules the photo needs.

* A photo with edits saved in Image Triage shows them: the editor's saved render (its screen-sized JPEG), as the library's viewer does.
  The edit lookup is imported only when the photo's folder has an edits folder at all.
* A JPEG, PNG, WebP or TIFF is read by Qt at the size asked for (a JPEG is decoded at 1/2, 1/4 or 1/8 of its size directly).
* A RAW shows the JPEG preview the camera embedded in it (``raw_embedded_jpeg``, plain Python), turned the way the camera was held.
* Anything else (HEIC, AVIF, a RAW without a usable preview) goes through the app's full loader (``imaging``), imported then.

Everything here runs on worker threads and returns a ``QImage`` (never a ``QPixmap``).
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QSize, Qt
from PySide6.QtGui import QImage, QImageIOHandler, QImageReader, QTransform

from ..formats import EDITOR_SUFFIXES, RAW_SUFFIXES, suffix_for_path

EDIT_ROOT_NAME = ".image_triage_edits"           # scanner.EDIT_STORAGE_ROOT_NAME, without importing the scanner


@dataclass(slots=True)
class Picture:
    image: QImage
    width: int = 0              # the photo's own size (what 100% means), as it is shown (turned upright)
    height: int = 0
    kind: str = ""              # "edited", "embedded" (a RAW's preview), "file" or "loader"
    error: str = ""


def _scaled_read(reader: QImageReader, longest: int) -> QImage:
    """Read with ``reader`` at most ``longest`` pixels on the long side (0: the full size), upright."""
    reader.setAutoTransform(True)
    size = reader.size()
    if longest > 0 and size.isValid() and max(size.width(), size.height()) > longest:
        reader.setScaledSize(size.scaled(QSize(longest, longest), Qt.AspectRatioMode.KeepAspectRatio))
    return reader.read()


def _own_size(reader: QImageReader) -> tuple[int, int]:
    """The photo's full size as it is shown (a portrait taken with the camera turned is taller than wide)."""
    size = reader.size()
    if not size.isValid():
        return 0, 0
    turned = reader.autoTransform() and bool(reader.transformation() & QImageIOHandler.Transformation.TransformationRotate90)
    return (size.height(), size.width()) if turned else (size.width(), size.height())


_ORIENT = {2: QTransform(-1, 0, 0, 1, 0, 0), 3: QTransform().rotate(180), 4: QTransform(1, 0, 0, -1, 0, 0),
           5: QTransform(0, 1, 1, 0, 0, 0), 6: QTransform().rotate(90), 7: QTransform(0, -1, -1, 0, 0, 0), 8: QTransform().rotate(270)}


def _upright(image: QImage, orientation: int) -> QImage:
    transform = _ORIENT.get(int(orientation or 1))
    return image if transform is None or image.isNull() else image.transformed(transform, Qt.TransformationMode.SmoothTransformation)


def _read_file(path: str, longest: int, kind: str) -> Picture | None:
    reader = QImageReader(path)
    reader.setAutoTransform(True)
    if not reader.canRead():
        return None
    width, height = _own_size(reader)
    image = _scaled_read(reader, longest)
    if image.isNull():
        return None
    return Picture(image, width or image.width(), height or image.height(), kind)


def _read_embedded(path: str, longest: int) -> Picture | None:
    from ..raw_embedded_jpeg import extract_embedded_jpeg

    embedded = extract_embedded_jpeg(path)
    if embedded is None:
        return None
    data = QByteArray(embedded.payload)
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.ReadOnly)
    reader = QImageReader(buffer, QByteArray(b"jpeg"))
    reader.setAutoTransform(False)                          # the RAW's orientation applies, not whatever the preview says
    size = reader.size()
    if longest > 0 and size.isValid() and max(size.width(), size.height()) > longest:
        reader.setScaledSize(size.scaled(QSize(longest, longest), Qt.AspectRatioMode.KeepAspectRatio))
    image = reader.read()
    buffer.close()
    if image.isNull() or max(image.width(), image.height()) < 320:       # a thumbnail, not a preview: the full loader does better
        return None
    width, height = (size.width(), size.height()) if size.isValid() else (image.width(), image.height())
    if embedded.orientation in (5, 6, 7, 8):
        width, height = height, width
    return Picture(_upright(image, embedded.orientation), width, height, "embedded")


def saved_render(path: str) -> str:
    """The picture of the photo's saved edits, or "" (no edits folder beside it: nothing is imported to find that out)."""
    if suffix_for_path(path) not in EDITOR_SUFFIXES or not os.path.isdir(os.path.join(os.path.dirname(path), EDIT_ROOT_NAME)):
        return ""
    try:
        from ..photocraft_bridge import saved_render_path

        render = saved_render_path(path)
    except Exception:
        return ""
    return str(render) if render is not None else ""


def load(path: str, longest: int) -> Picture:
    """The photo at ``path``, at most ``longest`` pixels on its long side (0: as large as it comes)."""
    if not os.path.isfile(path):
        return Picture(QImage(), error="The photo is not there any more.")
    try:
        render = saved_render(path)
        if render:
            picture = _read_file(render, longest, "edited")
            if picture is not None:
                return picture
        if suffix_for_path(path) in RAW_SUFFIXES:
            picture = _read_embedded(path, longest)
            if picture is not None:
                return picture
        else:
            picture = _read_file(path, longest, "file")
            if picture is not None:
                return picture
        return _read_with_loader(path, longest)
    except Exception as error:  # a damaged file must not end the viewer
        return Picture(QImage(), error=str(error) or "The photo could not be read.")


def _read_with_loader(path: str, longest: int) -> Picture:
    """The app's own loader: HEIC and AVIF, and RAW files the embedded preview does not cover. Imported only now."""
    from ..imaging import load_image_for_display, sanitize_display_error

    target = QSize(longest, longest) if longest > 0 else QSize()
    image, error = load_image_for_display(path, target)
    if image.isNull():
        return Picture(QImage(), error=sanitize_display_error(error, path=path) or "The photo could not be read.")
    return Picture(image, image.width(), image.height(), "loader")


def folder_photos(path: str) -> list[str]:
    """The photos in ``path``'s folder, in name order (numbers in names counted as numbers), one per shot: when a RAW and its JPEG
    share a name, the one that was opened stays, and otherwise the JPEG (it is the quicker to show)."""
    from ..formats import is_image_file_candidate

    folder = os.path.dirname(path)
    opened = os.path.normcase(os.path.abspath(path))
    try:
        with os.scandir(folder) as entries:
            names = [e.name for e in entries if not e.name.startswith(".") and is_image_file_candidate(e.name) and e.is_file()]
    except OSError:
        return [path]
    by_stem: dict[str, str] = {}
    for name in names:
        full = os.path.join(folder, name)
        stem = os.path.splitext(name)[0].casefold()
        held = by_stem.get(stem)
        if held is None:
            by_stem[stem] = full
            continue
        if os.path.normcase(os.path.abspath(held)) == opened:
            continue
        if os.path.normcase(os.path.abspath(full)) == opened or (suffix_for_path(held) in RAW_SUFFIXES and suffix_for_path(full) not in RAW_SUFFIXES):
            by_stem[stem] = full
    photos = sorted(by_stem.values(), key=natural_key)
    if not any(os.path.normcase(os.path.abspath(p)) == opened for p in photos):
        photos.append(path)
        photos.sort(key=natural_key)
    return photos


def natural_key(path: str) -> list:
    import re

    name = Path(path).name.casefold()
    return [int(part) if part.isdigit() else part for part in re.split(r"(\d+)", name)]
