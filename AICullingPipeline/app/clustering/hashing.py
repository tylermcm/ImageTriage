"""Optional perceptual hashing utilities for culling-oriented grouping."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import numpy as np
from PIL import Image, ImageOps


def compute_average_hash(path: Path, *, hash_size: int = 8) -> Optional[np.ndarray]:
    """Compute a simple perceptual average hash for an image file."""

    try:
        with Image.open(path) as image:
            grayscale = image.convert("L").resize(
                (hash_size, hash_size),
                Image.Resampling.LANCZOS,
            )
            pixels = np.asarray(grayscale, dtype=np.float32)
    except (OSError, ValueError):
        return None

    threshold = float(pixels.mean())
    return (pixels >= threshold).reshape(-1)


def compute_dhash(path: Path, *, hash_size: int = 8) -> Optional[int]:
    """Compute a 64-bit gradient (difference) hash for an image file.

    Returns an unsigned 64-bit value packed into a Python int, or None on read failure.
    dHash is more robust than aHash to global brightness shifts and is well-suited
    to detecting "same shot with a slight pose/blink shift" near-duplicates.
    """

    try:
        with Image.open(path) as image:
            oriented = ImageOps.exif_transpose(image)
            grayscale = oriented.convert("L").resize(
                (hash_size + 1, hash_size),
                Image.Resampling.LANCZOS,
            )
            pixels = np.asarray(grayscale, dtype=np.int32)
    except (OSError, ValueError):
        return None

    diff = pixels[:, 1:] > pixels[:, :-1]
    bits = diff.flatten()
    value = 0
    for bit in bits:
        value = (value << 1) | int(bit)
    return int(value)


def hamming_distance(left: Optional[np.ndarray], right: Optional[np.ndarray]) -> Optional[int]:
    """Compute Hamming distance between two perceptual hashes."""

    if left is None or right is None:
        return None

    if left.shape != right.shape:
        raise ValueError("Perceptual hashes must have the same shape.")

    return int(np.count_nonzero(left != right))


def hamming_distance_int(left: int, right: int) -> int:
    """Compute Hamming distance between two integer-packed perceptual hashes."""

    return bin(int(left) ^ int(right)).count("1")
