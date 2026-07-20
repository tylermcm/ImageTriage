from __future__ import annotations

from pathlib import Path
from typing import Union

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


def save_image(image: Image.Image, path: Union[str, Path], quality: int = 95) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    suffix = target.suffix.lower()
    if suffix in {".jpg", ".jpeg", ".webp"}:
        image.save(target, quality=quality, optimize=True)
    else:
        image.save(target)


def iter_images(path: Union[str, Path]):
    root = Path(path)
    for child in sorted(root.rglob("*")):
        if child.is_file() and child.suffix.lower() in IMAGE_EXTENSIONS:
            yield child
