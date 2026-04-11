from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize
from PySide6.QtGui import QImage

from .image_resize import _load_resize_image, _path_key, _save_resized_image


def normalized_output_path_key(path: str | Path) -> str:
    return _path_key(path)


def load_image_for_transform(
    source_path: str,
    *,
    target_size: QSize,
    ignore_orientation: bool,
    strip_metadata: bool,
):
    return _load_resize_image(
        source_path,
        target_size=target_size,
        ignore_orientation=ignore_orientation,
        strip_metadata=strip_metadata,
    )


def save_transformed_image(
    image: QImage,
    *,
    target_path: str,
    target_suffix: str,
    exif_bytes: bytes | None,
    icc_profile: bytes | None,
) -> None:
    _save_resized_image(
        image,
        target_path=target_path,
        target_suffix=target_suffix,
        exif_bytes=exif_bytes,
        icc_profile=icc_profile,
    )
