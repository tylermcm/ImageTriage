"""Stable image-identity helpers shared by engine modules and host adapters."""

from __future__ import annotations

import hashlib
from pathlib import Path, PurePosixPath


def normalize_relative_path(relative_path: str | Path) -> str:
    """Normalize a relative image path into a stable POSIX-style key."""

    text = str(relative_path).replace("\\", "/").strip()
    return PurePosixPath(text).as_posix().lstrip("./")


def build_stable_image_id(relative_path: str | Path) -> str:
    """Create a deterministic image ID from a normalized relative path."""

    normalized = normalize_relative_path(relative_path)
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()
