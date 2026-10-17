"""Exporting a photo with its edits (audit findings F2/F3): the full-size picture of the edits, never the screen-sized one and never
the original in place of edits that could not be rendered; the picture's own colour profile and bit depth, converted only where the
output format cannot hold it, and then said so.

The batch exports (resize, convert, workflow export) leave a photo whose edits cannot be rendered out, carry on with the rest, and
report it: they return a ``BatchOutcome``, a tuple of the written paths that also lists what was left out and what was converted.
"""
from __future__ import annotations

import struct
import os
import tempfile
import zlib
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtGui import QImage, QImageWriter


class EditedRenderFailed(Exception):
    """The photo has edits, and they could not be rendered: it must not be exported (as the original or otherwise)."""

    def __init__(self, source_path: str, reason: str) -> None:
        super().__init__(f"{Path(source_path).name}: its edits could not be rendered ({reason})")
        self.source_path = source_path
        self.reason = reason


class BatchOutcome(tuple):
    """The paths a batch wrote (it is that tuple), plus the photos it left out, as ``(source path, reason)``, and notes on
    conversions it made."""

    failed: tuple[tuple[str, str], ...]
    notes: tuple[str, ...]

    def __new__(cls, written=(), failed=(), notes=()):
        outcome = super().__new__(cls, tuple(written))
        outcome.failed = tuple(failed)
        outcome.notes = tuple(dict.fromkeys(notes))
        return outcome

    def report(self) -> str:
        """What a person should be told beyond the count, or ``""``."""
        lines = []
        if self.failed:
            lines.append(f"{len(self.failed)} photo(s) were not exported because their edits could not be rendered:")
            lines.extend(f"  {Path(source).name}: {reason}" for source, reason in self.failed)
        lines.extend(self.notes)
        return "\n".join(lines)


@dataclass(frozen=True)
class EditedImage:
    image: QImage
    icc_profile: bytes | None


def bit_depth(image: QImage) -> int:
    fmt = image.format()
    if fmt in (QImage.Format.Format_RGBA32FPx4, QImage.Format.Format_RGBX32FPx4, QImage.Format.Format_RGBA32FPx4_Premultiplied):
        return 32
    if fmt in (QImage.Format.Format_RGBA16FPx4, QImage.Format.Format_RGBX16FPx4, QImage.Format.Format_RGBA16FPx4_Premultiplied,
               QImage.Format.Format_RGBA64, QImage.Format.Format_RGBX64, QImage.Format.Format_RGBA64_Premultiplied, QImage.Format.Format_Grayscale16):
        return 16
    return 8


def edited_image_for_export(source_path: str) -> EditedImage | None:
    """The photo's edits at full size, decoded at the size they were rendered (never enlarged), with the render's own colour profile;
    ``None`` when the photo has no PhotoCraft edits. Raises ``EditedRenderFailed`` when it has edits that could not be rendered."""
    from .full_render import FAILED, RENDERED, request_full_render

    result = request_full_render(source_path)
    if result.outcome == FAILED:
        raise EditedRenderFailed(source_path, result.reason)
    if result.outcome != RENDERED or result.path is None:
        return None
    try:
        image = QImage(str(result.path))
    finally:
        result.release()
    if image.isNull():
        raise EditedRenderFailed(source_path, f"the rendered picture could not be read ({result.path.name})")
    if result.width and result.height and (image.width(), image.height()) != (result.width, result.height):
        raise EditedRenderFailed(source_path, f"the rendered picture is {image.width()}x{image.height()}, not {result.width}x{result.height}")
    if result.depth and bit_depth(image) < result.depth:
        raise EditedRenderFailed(source_path, f"the image reader reduced the {result.depth}-bit render to {bit_depth(image)} bits")
    profile = image.colorSpace().iccProfile() if image.colorSpace().isValid() else b""
    if result.icc and not profile:
        raise EditedRenderFailed(source_path, "the rendered picture's colour profile could not be read")
    return EditedImage(image, bytes(profile) or None)


# Output formats that can hold more than 8 bits per channel, and how many.
_DEEP_FORMATS = {".png": 16, ".tif": 32, ".tiff": 32}


def save_preserving_depth(
    image: QImage, *, target_path: str, target_suffix: str, exif_bytes: bytes | None, icc_profile: bytes | None,
) -> str | None:
    """Writes a picture of more than 8 bits per channel to ``target_path`` keeping as many bits as the format holds, with
    ``icc_profile``; a PNG keeps ``exif_bytes`` too. Returns a note when bits were dropped or metadata left out, else ``None``.
    Not for 8-bit pictures or formats that only hold 8 bits: those go through the usual writer, which converts."""
    from PySide6.QtGui import QColorSpace

    depth = bit_depth(image)
    holds = _DEEP_FORMATS[target_suffix]
    notes = []
    if depth > holds:
        image = image.convertToFormat(QImage.Format.Format_RGBA64)
        notes.append(f"{Path(target_path).name}: written with 16 bits per channel; the edits were rendered with {depth}")
    if icc_profile:
        space = QColorSpace.fromIccProfile(icc_profile)
        if space.isValid():
            image.setColorSpace(space)
    fd, temporary = tempfile.mkstemp(dir=str(Path(target_path).parent), suffix=target_suffix)
    os.close(fd)
    try:
        writer = QImageWriter(temporary, b"png" if target_suffix == ".png" else b"tiff")
        if not writer.write(image):
            raise OSError(f"Could not write {target_path}: {writer.errorString()}")
        del writer
        if exif_bytes:
            if target_suffix == ".png":
                _add_png_exif(temporary, exif_bytes)
            else:
                notes.append(f"{Path(target_path).name}: the camera's EXIF data is not carried in a {depth}-bit TIFF")
        os.replace(temporary, target_path)
    finally:
        if os.path.exists(temporary):
            os.remove(temporary)
    return "; ".join(notes) or None


def _add_png_exif(path: str, exif_bytes: bytes) -> None:
    """Adds an ``eXIf`` chunk (the EXIF without its ``Exif\\0\\0`` header) after the PNG's header chunk."""
    payload = exif_bytes[6:] if exif_bytes.startswith(b"Exif\x00\x00") else exif_bytes
    data = Path(path).read_bytes()
    ihdr_end = 8 + 8 + struct.unpack(">I", data[8:12])[0] + 4
    chunk = struct.pack(">I", len(payload)) + b"eXIf" + payload + struct.pack(">I", zlib.crc32(b"eXIf" + payload) & 0xFFFFFFFF)
    Path(path).write_bytes(data[:ihdr_end] + chunk + data[ihdr_end:])
