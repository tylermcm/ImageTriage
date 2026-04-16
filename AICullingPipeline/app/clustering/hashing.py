"""Optional perceptual hashing utilities for culling-oriented grouping."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import numpy as np
from PIL import Image


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


def hamming_distance(left: Optional[np.ndarray], right: Optional[np.ndarray]) -> Optional[int]:
    """Compute Hamming distance between two perceptual hashes."""

    if left is None or right is None:
        return None

    if left.shape != right.shape:
        raise ValueError("Perceptual hashes must have the same shape.")

    return int(np.count_nonzero(left != right))
