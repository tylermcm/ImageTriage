"""Small greyscale pictures of frames, decoded on demand and kept for a moment: what the SSIM and ORB repeat passes compare."""

from __future__ import annotations

from collections import OrderedDict
import threading

import numpy as np
from PIL import Image

from .extract import PreviewSource, _load_preview


class PreviewPixels:
    """``gray(path, edge)``: the frame's preview as a float32 greyscale array whose long edge is ``edge`` (None if it cannot be read).

    The newest ``capacity`` pictures are kept, which is enough for a pass that compares each frame with the few before it."""

    def __init__(self, preview_source: PreviewSource | None = None, capacity: int = 32) -> None:
        self._source = preview_source
        self._capacity = capacity
        self._kept: OrderedDict[tuple[str, int], np.ndarray | None] = OrderedDict()
        self._lock = threading.Lock()

    def gray(self, path: str, edge: int) -> np.ndarray | None:
        key = (path, edge)
        with self._lock:
            if key in self._kept:
                self._kept.move_to_end(key)
                return self._kept[key]
        array = self._decode(path, edge)
        with self._lock:
            self._kept[key] = array
            while len(self._kept) > self._capacity:
                self._kept.popitem(last=False)
        return array

    def _decode(self, path: str, edge: int) -> np.ndarray | None:
        try:
            image = _load_preview(path, self._source)
        except Exception:
            return None
        if image is None:
            return None
        with image:
            gray = image.convert("L")
            scale = edge / max(gray.size)
            gray = gray.resize((max(1, round(gray.width * scale)), max(1, round(gray.height * scale))), Image.Resampling.BOX)
            return np.asarray(gray, dtype=np.float32)
