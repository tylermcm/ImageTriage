from __future__ import annotations

import os
from pathlib import Path
import struct
import sys
import tempfile
from typing import Optional, Union
import zlib

import numpy as np
from PIL import Image, TiffImagePlugin


IMAGE_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".tif",
    ".tiff",
    ".webp",
    ".bmp",
}


def open_image(path: Union[str, Path]) -> Image.Image:
    return Image.open(Path(path))


_ORIENTATION_TAG = 0x0112


def source_metadata(image: Image.Image) -> dict:
    """The colour profile and EXIF of a photo as it was opened, to write into the edited copy.

    The edit applies the EXIF orientation to the pixels, so the tag is dropped from what is carried over:
    left in, a viewer would turn the already-upright picture a second time.
    """
    metadata: dict = {}
    profile = image.info.get("icc_profile")
    if profile:
        metadata["icc_profile"] = profile
    try:
        # A copy: the image's own EXIF must keep its orientation for the edit to apply.
        exif = Image.Exif()
        exif.load(image.getexif().tobytes())
    except Exception:  # damaged EXIF must not stop a render
        exif = None
    if exif:
        exif.pop(_ORIENTATION_TAG, None)
        metadata["exif"] = exif
    return metadata


def save_image(
    image: Image.Image,
    path: Union[str, Path],
    quality: int = 95,
    *,
    icc_profile: Optional[bytes] = None,
    exif=None,
) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    suffix = target.suffix.lower()
    extra: dict = {}
    # Without the profile a wide-gamut photo is read back as sRGB and its colours shift.
    if icc_profile and suffix != ".bmp":
        extra["icc_profile"] = icc_profile
    if exif and suffix != ".bmp":
        extra["exif"] = exif
    if suffix in {".jpg", ".jpeg", ".webp"}:
        image.save(target, quality=quality, optimize=True, **extra)
    else:
        image.save(target, **extra)


# --- 16 bits per channel -------------------------------------------------------------------------------------------------
#
# Pillow has no 16-bit RGB mode: it opens a 16-bit RGB PNG or TIFF as 8-bit and cannot write one. A 16-bit source is
# therefore read through Qt's image reader (PySide6 is already a dependency; QImage needs no application object), held
# as a floating-point array, and written by the two small writers below, which also carry the colour profile and EXIF.

HIGH_DEPTH_SUFFIXES = {".png", ".tif", ".tiff"}
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def bits_per_channel(path: Union[str, Path]) -> int:
    """16 for a PNG or TIFF with 16-bit unsigned integer channels (grey or RGB, with or without alpha), else 8."""
    path = Path(path)
    suffix = path.suffix.lower()
    try:
        if suffix == ".png":
            with path.open("rb") as stream:
                head = stream.read(26)
            # signature, IHDR length and name, width, height, then the bit depth and the colour type (3 = palette)
            if len(head) == 26 and head[:8] == _PNG_SIGNATURE and head[12:16] == b"IHDR" and head[24] == 16 and head[25] in (0, 2, 4, 6):
                return 16
        elif suffix in (".tif", ".tiff"):
            with Image.open(path) as image:
                tags = image.tag_v2
                bits = tags.get(258)
                bits = bits if isinstance(bits, tuple) else (bits,)
                formats = tags.get(339, 1)
                formats = formats if isinstance(formats, tuple) else (formats,)
                if bits and all(value == 16 for value in bits) and all(value == 1 for value in formats) and tags.get(262) in (1, 2):
                    return 16
    except Exception:  # unreadable here: the ordinary path reports it
        pass
    return 8


def _read_rgb16(path: Path) -> Optional[np.ndarray]:
    """The picture's pixels as ``uint16`` RGB, or ``None`` when Qt is unavailable or cannot read the file."""
    try:
        from PySide6.QtGui import QImage, QImageReader
    except Exception:
        return None
    limit = QImageReader.allocationLimit()
    QImageReader.setAllocationLimit(0)       # the default 256 MB is less than one 45-megapixel 16-bit picture
    try:
        reader = QImageReader(str(path))
        reader.setAutoTransform(False)       # the orientation is applied below, from the EXIF Pillow reads
        image = reader.read()
    finally:
        QImageReader.setAllocationLimit(limit)
    if image.isNull():
        return None
    image = image.convertToFormat(QImage.Format.Format_RGBX64)
    width, height = image.width(), image.height()
    rows = np.frombuffer(image.constBits(), dtype=np.uint16).reshape(height, image.bytesPerLine() // 2)
    return rows[:, : width * 4].reshape(height, width, 4)[..., :3].copy()


def _upright(pixels: np.ndarray, orientation: int) -> np.ndarray:
    """``pixels`` turned as EXIF ``orientation`` says (what ``ImageOps.exif_transpose`` does to a Pillow image)."""
    if orientation == 2:
        return pixels[:, ::-1]
    if orientation == 3:
        return pixels[::-1, ::-1]
    if orientation == 4:
        return pixels[::-1]
    if orientation == 5:
        return pixels.transpose(1, 0, 2)
    if orientation == 6:
        return np.rot90(pixels, k=-1)
    if orientation == 7:
        return pixels[::-1, ::-1].transpose(1, 0, 2)
    if orientation == 8:
        return np.rot90(pixels, k=1)
    return pixels


def open_high_depth(path: Union[str, Path]):
    """``(pixels, metadata)`` for a 16-bit PNG or TIFF: float32 RGB in 0..1 (as encoded in the file, upright) and what
    ``source_metadata`` gives for it. ``None`` for any other picture, or when it cannot be read at 16 bits (the caller
    then edits it as an 8-bit picture, as before)."""
    path = Path(path)
    if bits_per_channel(path) != 16:
        return None
    pixels = _read_rgb16(path)
    if pixels is None:
        print(f"warning: {path.name} has 16 bits per channel but could not be read at that depth; editing it at 8 bits", file=sys.stderr)
        return None
    with Image.open(path) as image:
        metadata = source_metadata(image)
        try:
            orientation = int(image.getexif().get(_ORIENTATION_TAG, 1))
        except Exception:
            orientation = 1
    pixels = np.ascontiguousarray(_upright(pixels, orientation))
    return pixels.astype(np.float32) / np.float32(65535.0), metadata


def _png_chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)


def _write_png16(stream, pixels: np.ndarray, icc_profile: Optional[bytes], exif_bytes: Optional[bytes]) -> None:
    height, width = pixels.shape[:2]
    stream.write(_PNG_SIGNATURE)
    stream.write(_png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 16, 2, 0, 0, 0)))
    if icc_profile:
        stream.write(_png_chunk(b"iCCP", b"ICC Profile\x00\x00" + zlib.compress(icc_profile)))
    if exif_bytes:
        stream.write(_png_chunk(b"eXIf", exif_bytes))
    compressor = zlib.compressobj(6)
    for top in range(0, height, 64):
        band = np.ascontiguousarray(pixels[top : top + 64].astype(">u2")).view(np.uint8).reshape(-1, width * 6)
        filtered = np.empty((band.shape[0], width * 6 + 1), dtype=np.uint8)
        filtered[:, 0] = 1                                   # the Sub filter: each byte less the same byte of the pixel before
        filtered[:, 1:7] = band[:, :6]
        filtered[:, 7:] = band[:, 6:] - band[:, :-6]
        data = compressor.compress(filtered.tobytes())
        if data:
            stream.write(_png_chunk(b"IDAT", data))
    stream.write(_png_chunk(b"IDAT", compressor.flush()))
    stream.write(_png_chunk(b"IEND", b""))


_TIFF_LAYOUT_TAGS = frozenset((254, 255, 256, 257, 258, 259, 262, 273, 277, 278, 279, 284, 317, 320, 322, 323, 324, 325, 338, 339, 34665, 34675, 34853))


def _write_tiff16(stream, pixels: np.ndarray, icc_profile: Optional[bytes], exif) -> None:
    """Uncompressed little-endian 16-bit RGB in one strip, with the directory Pillow's TIFF writer builds (so the EXIF
    entries, their sub-directory included, are stored the way Pillow stores them)."""
    height, width = pixels.shape[:2]
    data = np.ascontiguousarray(pixels.astype("<u2")).tobytes()

    def directory(with_exif: bool) -> TiffImagePlugin.ImageFileDirectory_v2:
        ifd = TiffImagePlugin.ImageFileDirectory_v2()
        if with_exif and exif:
            for tag, value in exif.items():
                if tag not in _TIFF_LAYOUT_TAGS:
                    try:
                        ifd[tag] = value
                    except Exception:
                        pass
            try:
                details = dict(exif.get_ifd(0x8769))
            except Exception:
                details = {}
            if details:
                ifd[0x8769] = details
        ifd[256], ifd[257], ifd[258], ifd[259], ifd[262] = width, height, (16, 16, 16), 1, 2
        ifd[273] = (0,)            # Pillow adds where the directory ends, which is where the strip is written
        ifd[277], ifd[278], ifd[279], ifd[284], ifd[339] = 3, height, (len(data),), 1, (1, 1, 1)
        if icc_profile:
            ifd[34675] = icc_profile
            ifd.tagtype[34675] = 7
        return ifd

    start = stream.tell()
    try:
        directory(True).save(stream)
    except Exception:              # an EXIF entry Pillow cannot store in a TIFF: the picture matters more
        stream.seek(start)
        stream.truncate()
        directory(False).save(stream)
    stream.write(data)


def save_high_depth(pixels: np.ndarray, path: Union[str, Path], *, icc_profile: Optional[bytes] = None, exif=None) -> None:
    """Write float RGB (0..1) as a 16-bit RGB PNG or TIFF, with the colour profile and EXIF, through a temporary file."""
    target = Path(path)
    suffix = target.suffix.lower()
    if suffix not in HIGH_DEPTH_SUFFIXES:
        raise ValueError(f"{suffix or 'this format'} cannot hold 16 bits per channel")
    values = np.rint(np.clip(np.asarray(pixels, dtype=np.float32), 0.0, 1.0) * 65535.0).astype(np.uint16)
    if values.ndim != 3 or values.shape[2] != 3:
        raise ValueError("save_high_depth takes an RGB array of shape (height, width, 3)")
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent))
    try:
        with os.fdopen(handle, "w+b") as stream:
            if suffix == ".png":
                exif_bytes = None
                if exif:
                    try:
                        exif_bytes = exif.tobytes()
                        exif_bytes = exif_bytes[6:] if exif_bytes.startswith(b"Exif\x00\x00") else exif_bytes
                    except Exception:
                        exif_bytes = None
                _write_png16(stream, values, icc_profile, exif_bytes)
            else:
                _write_tiff16(stream, values, icc_profile, exif)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def write_text_atomic(path: Union[str, Path], text: str) -> None:
    """Write through a temporary file beside the target and move it into place, so a crash or a full disk part way
    leaves the previous file whole instead of a truncated one."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent))
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def iter_images(path: Union[str, Path]):
    root = Path(path)
    for child in sorted(root.rglob("*")):
        if child.is_file() and child.suffix.lower() in IMAGE_EXTENSIONS:
            yield child
