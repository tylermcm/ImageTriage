"""Point-at-it selection for AI-detected scene regions.

The segmentation pass already writes one mask per detected category next to the
image. :class:`SceneRegionIndex` collapses that pile into a single label map so
the overlay can answer "what is under the cursor" with one array lookup per
mouse move, and hand back a tinted highlight for whatever the cursor is over.

That is the whole point of the thing: a category stops being a button you have
to name and becomes a region you point at.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping

import numpy as np
from PIL import Image
from PySide6.QtCore import QObject, QRunnable, Qt, Signal
from PySide6.QtGui import QColor, QImage

# Deliberately not OVERLAY_RED: red means "this is a mask you own", blue means
# "this is a candidate you could pick".
SCENE_HIGHLIGHT = QColor(74, 158, 255)
SCENE_MAX_ALPHA = 110
# The label map only decides which region a cursor is in, so it can be small.
INDEX_MAX_EDGE = 512
PRESENT_THRESHOLD = 128


def _category_label(category: str) -> str:
    return category.replace("_", " ").strip().title()


@dataclass
class SceneRegionIndex:
    """Winner-takes-all label map over the detected category masks."""

    source_size: tuple[int, int]
    categories: tuple[str, ...]
    _labels: np.ndarray            # uint8 (h, w); 0 = no region, else index + 1
    _masks: dict[str, np.ndarray]  # category -> uint8 (h, w) coverage

    def __post_init__(self) -> None:
        self._highlight_cache: dict[tuple[str, int, int], QImage] = {}

    # -- construction ---------------------------------------------------------
    @classmethod
    def from_mask_paths(
        cls,
        source_size: tuple[int, int],
        mask_paths: Mapping[str, Path],
        categories: Iterable[str],
        *,
        max_edge: int = INDEX_MAX_EDGE,
    ) -> "SceneRegionIndex | None":
        source_width, source_height = (int(source_size[0]), int(source_size[1]))
        if source_width < 1 or source_height < 1:
            return None
        scale = min(1.0, max_edge / max(source_width, source_height))
        width = max(1, int(round(source_width * scale)))
        height = max(1, int(round(source_height * scale)))

        names: list[str] = []
        planes: list[np.ndarray] = []
        for category in categories:
            path = mask_paths.get(category)
            if path is None or not Path(path).is_file():
                continue
            try:
                with Image.open(path) as handle:
                    resized = handle.convert("L").resize(
                        (width, height), Image.Resampling.BILINEAR
                    )
            except (OSError, ValueError):
                continue
            plane = np.asarray(resized, dtype=np.uint8)
            if not plane.any():
                continue
            names.append(category)
            planes.append(plane)

        if not planes:
            return None
        stack = np.stack(planes)
        winner = stack.argmax(axis=0).astype(np.uint8)
        strongest = stack.max(axis=0)
        labels = np.where(strongest >= PRESENT_THRESHOLD, winner + 1, 0).astype(np.uint8)
        if not labels.any():
            return None
        return cls(
            source_size=(source_width, source_height),
            categories=tuple(names),
            _labels=labels,
            _masks={name: plane for name, plane in zip(names, planes)},
        )

    # -- lookup ---------------------------------------------------------------
    def category_at(
        self,
        source_x: float,
        source_y: float,
        *,
        coordinate_size: tuple[int, int] | None = None,
    ) -> str | None:
        """Category under a point, or ``None``.

        ``coordinate_size`` is the size of the coordinate space that produced
        the point. Semantic masks are generated from a bounded preview, while
        editor geometry uses the full source dimensions, so those sizes often
        differ even though they describe the same normalized image position.
        """
        source_width, source_height = coordinate_size or self.source_size
        if source_width < 1 or source_height < 1:
            return None
        height, width = self._labels.shape
        column = int(source_x / source_width * width)
        row = int(source_y / source_height * height)
        if not (0 <= column < width and 0 <= row < height):
            return None
        index = int(self._labels[row, column])
        if index == 0:
            return None
        return self.categories[index - 1]

    def label_for(self, category: str) -> str:
        return _category_label(category)

    # -- painting -------------------------------------------------------------
    def highlight(self, category: str, width: int, height: int) -> QImage | None:
        """Tinted candidate overlay for ``category``, scaled to the display."""
        if width < 1 or height < 1:
            return None
        plane = self._masks.get(category)
        if plane is None:
            return None
        key = (category, width, height)
        cached = self._highlight_cache.get(key)
        if cached is not None:
            return cached

        rows, columns = plane.shape
        rgba = np.empty((rows, columns, 4), dtype=np.uint8)
        rgba[..., 0] = SCENE_HIGHLIGHT.red()
        rgba[..., 1] = SCENE_HIGHLIGHT.green()
        rgba[..., 2] = SCENE_HIGHLIGHT.blue()
        rgba[..., 3] = (plane.astype(np.uint16) * SCENE_MAX_ALPHA // 255).astype(np.uint8)
        # copy() so the QImage owns its bytes once ``rgba`` goes out of scope.
        image = QImage(
            rgba.data, columns, rows, columns * 4, QImage.Format.Format_RGBA8888
        ).copy()
        scaled = image.scaled(
            width,
            height,
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        # One hovered region at a time; a couple of display sizes at most.
        if len(self._highlight_cache) > 4:
            self._highlight_cache.clear()
        self._highlight_cache[key] = scaled
        return scaled


class SceneIndexSignals(QObject):
    ready = Signal(str, object)  # source path, SceneRegionIndex | None


class SceneIndexTask(QRunnable):
    """Builds a :class:`SceneRegionIndex` off the UI thread.

    Decoding seven full-size category masks costs ~100ms, which is a visible
    hitch if it lands on the frame where the Masks tab opens.
    """

    def __init__(
        self,
        source_path: Path,
        source_size: tuple[int, int],
        mask_paths: Mapping[str, Path],
        categories: Iterable[str],
    ) -> None:
        super().__init__()
        self.signals = SceneIndexSignals()
        self._source_path = source_path
        self._source_size = source_size
        self._mask_paths = dict(mask_paths)
        self._categories = tuple(categories)

    def run(self) -> None:
        try:
            index = SceneRegionIndex.from_mask_paths(
                self._source_size, self._mask_paths, self._categories
            )
        except Exception:
            index = None
        self.signals.ready.emit(str(self._source_path), index)
