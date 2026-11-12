from __future__ import annotations

import os
from pathlib import Path
import tempfile
from typing import Optional, Union

from PIL import Image


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
