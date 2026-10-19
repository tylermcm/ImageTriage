"""Eyes-closed ("blink") decision from a face landmarker's blendshape scores. Pure logic: no models, no Qt.

MediaPipe's Face Landmarker (Apache-2.0) returns, per face, a landmark set and a list of blendshape scores in [0, 1] that include ``eyeBlinkLeft``
and ``eyeBlinkRight`` (1 means the lid is fully closed). A frame's score is the highest eyes-closed value among the faces that are large enough to
matter, so one person blinking in a group shot counts and a tiny background face does not. The worker (``rankers/worker.py``) calls
``frame_blink_score``; the folder runner thresholds it with ``is_blink``.

Thresholds are conservative on purpose (a wrong blink flag rejects a good frame): looking down also lowers the lids, which scores well below 0.6.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Iterable, Mapping, Sequence

BLINK_LEFT = "eyeBlinkLeft"
BLINK_RIGHT = "eyeBlinkRight"
BLINK_THRESHOLD = 0.6  # mean of both eyes at or above this is a blink
MIN_FACE_AREA = 0.01  # a face must cover at least this fraction of the frame to count


def face_area(landmarks: Iterable[tuple[float, float]]) -> float:
    """Bounding-box area of a face's normalised (0..1) landmark points, as a fraction of the frame."""
    xs: list[float] = []
    ys: list[float] = []
    for x, y in landmarks:
        xs.append(float(x))
        ys.append(float(y))
    if not xs:
        return 0.0
    return max(0.0, max(xs) - min(xs)) * max(0.0, max(ys) - min(ys))


def eyes_closed(blendshapes: Mapping[str, float]) -> float | None:
    """Mean of the two eyelid-closure scores, or None when the landmarker did not report both."""
    left, right = blendshapes.get(BLINK_LEFT), blendshapes.get(BLINK_RIGHT)
    if left is None or right is None:
        return None
    return (float(left) + float(right)) / 2.0


def frame_blink_score(faces: Sequence[tuple[float, Mapping[str, float]]], *, min_area: float = MIN_FACE_AREA) -> float:
    """Highest eyes-closed score among the prominent faces; 0.0 when no face is large enough or none reports blendshapes.
    ``faces`` is a sequence of ``(area_fraction, blendshape_scores)``."""
    best = 0.0
    for area, shapes in faces:
        if area < min_area:
            continue
        closed = eyes_closed(shapes)
        if closed is not None and closed > best:
            best = closed
    return best


def is_blink(score: float, threshold: float = BLINK_THRESHOLD) -> bool:
    """An unreadable frame scores minus infinity and is never a blink."""
    return score >= threshold


class BlinkScoreCache:
    """Eyes-closed scores remembered per file (size and modified time), so re-analysing a folder does not run the face model again.

    ``model`` names the model and its thresholds-independent version: a different model invalidates every entry. Unreadable frames (minus infinity)
    are not stored, so they are retried next time."""

    VERSION = 1

    def __init__(self, path: str | Path | None, model: str) -> None:
        self._path = Path(path) if path is not None else None
        self._model = model
        self._entries: dict[str, list[float]] = {}
        self._dirty = False
        if self._path is not None:
            try:
                data = json.loads(self._path.read_text(encoding="utf-8"))
                if data.get("version") == self.VERSION and data.get("model") == model:
                    self._entries = {str(k): list(v) for k, v in dict(data.get("entries", {})).items()}
            except (OSError, ValueError, AttributeError, TypeError):
                self._entries = {}

    @staticmethod
    def _signature(path: str) -> tuple[int, int] | None:
        try:
            stat = os.stat(path)
        except OSError:
            return None
        return int(stat.st_size), int(stat.st_mtime_ns)

    def get(self, path: str) -> float | None:
        entry = self._entries.get(path)
        signature = self._signature(path)
        if entry is None or signature is None or len(entry) != 3 or (int(entry[0]), int(entry[1])) != signature:
            return None
        return float(entry[2])

    def put(self, path: str, score: float) -> None:
        signature = self._signature(path)
        if signature is None or score != score or score in (float("inf"), float("-inf")):
            return
        self._entries[path] = [signature[0], signature[1], float(score)]
        self._dirty = True

    def save(self) -> None:
        if self._path is None or not self._dirty:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._path.with_name(self._path.name + ".tmp")
        temporary.write_text(json.dumps({"version": self.VERSION, "model": self._model, "entries": self._entries}), encoding="utf-8")
        os.replace(temporary, self._path)
        self._dirty = False
