"""Face / eye / gender-age dimensions (InsightFace buffalo_l, detection + landmarks + genderage).

A **reject-side** signal for people genres (soft-focus faces) plus the data the
inspector/zoom UI needs (per-face boxes, gender/age). It does NOT move the
per-folder winner ranking — embeddings already own that.

Design choices, validated on real portraits:
- Eyes are located by the **detector's eye keypoints** (`face.kps`), not hardcoded
  106-landmark indices, which proved error-prone to pin down. Eye sharpness is the
  Laplacian variance of a crop around each eye keypoint.
- **Blink** (Eye Aspect Ratio) is **deferred**: reliable EAR needs the exact eye-
  contour indices, which need a dedicated calibration against a known closed-eye
  reference. The pure `eye_aspect_ratio`/`is_blink` helpers are kept for that pass;
  the analyzer returns `blink=None` until then (shipping a wrong blink would cause
  false rejects).

The math is pure and tested here. The InsightFace wiring is lazy and degrades
gracefully: if `insightface`/`buffalo_l` are unavailable, dims stay `None`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np

from .technical import _LAPLACIAN_KERNEL, _conv3, _to_gray

_BLINK_EAR_THRESHOLD = 0.21
_POSE_LIMIT_DEG = 35.0
_EYE_CROP_RADIUS_FRACTION = 0.18  # of inter-ocular distance


@dataclass(slots=True)
class FaceRecord:
    """Per-face detection record for the inspector + zoom UI."""

    bbox: tuple[float, float, float, float]
    det_score: float
    eye_sharpness: float | None = None
    gender: str | None = None  # "M"/"F" estimate (noisy — display as estimate)
    age: int | None = None     # estimate
    blink: bool | None = None  # deferred until EAR calibration


# -- Pure, tested helpers ---------------------------------------------------

def eye_aspect_ratio(points: Sequence[Sequence[float]]) -> float:
    """EAR from 6 eye points: p0/p3 horizontal corners, (p1,p5)/(p2,p4) vertical
    lid pairs. Low EAR == closed eye. (Kept for the future blink-calibration pass.)"""
    p = np.asarray(points, dtype=np.float64)
    horizontal = float(np.linalg.norm(p[0] - p[3]))
    if horizontal <= 0:
        return 0.0
    v1 = float(np.linalg.norm(p[1] - p[5]))
    v2 = float(np.linalg.norm(p[2] - p[4]))
    return (v1 + v2) / (2.0 * horizontal)


def eye_region_sharpness(eye_image: np.ndarray) -> float:
    """Laplacian variance on an eye crop, normalized by mean intensity, 0-10."""
    gray = _to_gray(eye_image)
    if gray.size == 0:
        return 0.0
    variance = float(_conv3(gray, _LAPLACIAN_KERNEL).var())
    score = variance / (float(gray.mean()) + 1.0)
    return float(min(10.0, max(0.0, score)))


def aggregate_face_quality(confidences: Sequence[float]) -> float | None:
    """0.7*min + 0.3*avg of detection confidences (emphasize the weakest face), 0-10."""
    values = [float(c) for c in confidences if c is not None]
    if not values:
        return None
    score = 0.7 * min(values) + 0.3 * (sum(values) / len(values))
    return float(min(10.0, max(0.0, score * 10.0)))


def is_blink(
    ear: float,
    *,
    yaw: float = 0.0,
    pitch: float = 0.0,
    threshold: float = _BLINK_EAR_THRESHOLD,
    pose_limit: float = _POSE_LIMIT_DEG,
) -> bool:
    """Eyes-closed if EAR is below threshold, only when the head is roughly frontal."""
    if abs(yaw) > pose_limit or abs(pitch) > pose_limit:
        return False
    return ear < threshold


def _eye_sharpness_from_keypoints(image_bgr: np.ndarray, kps: np.ndarray) -> float | None:
    """Laplacian sharpness of crops around the two detector eye keypoints, averaged."""
    img = np.asarray(image_bgr)
    h, w = img.shape[:2]
    inter = float(np.linalg.norm(kps[0] - kps[1])) or 1.0
    half = max(4, int(_EYE_CROP_RADIUS_FRACTION * inter))
    values: list[float] = []
    for center in kps[:2]:
        cx, cy = int(center[0]), int(center[1])
        crop = img[max(0, cy - half): min(h, cy + half), max(0, cx - half): min(w, cx + half)]
        if crop.size:
            values.append(eye_region_sharpness(crop))
    return float(np.mean(values)) if values else None


# -- InsightFace wiring (lazy, graceful) ------------------------------------

class FaceQualityAnalyzer:
    """Lazy InsightFace wrapper. Construct once; call `analyze(bgr)` per image.

    Loads detection + landmark + genderage. If insightface or the buffalo_l models
    are unavailable, `available` is False and `analyze` returns all-None dims.
    """

    def __init__(
        self,
        *,
        root: str | None = None,
        name: str = "buffalo_l",
        det_size: int = 640,
        ctx_id: int = -1,
    ) -> None:
        """``root`` is the model directory containing ``models/<name>/`` (the app's
        local model store from ai_model.download_aiculler_face_model). Defaults to
        that download location, falling back to InsightFace's own cache."""
        self.available = False
        self._app = None
        if root is None:
            try:
                from ..ai_model import aiculler_face_model_root

                candidate = aiculler_face_model_root()
                if (candidate / "models" / name).is_dir():
                    root = str(candidate)
            except Exception:
                root = None
        try:
            from insightface.app import FaceAnalysis  # type: ignore

            kwargs = {
                "name": name,
                "allowed_modules": ["detection", "landmark_2d_106", "genderage"],
            }
            if root is not None:
                kwargs["root"] = root
            app = FaceAnalysis(**kwargs)
            app.prepare(ctx_id=ctx_id, det_size=(det_size, det_size))
            self._app = app
            self.available = True
        except Exception:
            self.available = False

    def analyze(self, image_bgr: np.ndarray) -> dict[str, object]:
        empty: dict[str, object] = {
            "face_quality": None,
            "eye_sharpness": None,
            "blink": None,
            "face_count": 0,
            "faces": [],
        }
        if not self.available or self._app is None:
            return empty
        try:
            faces = self._app.get(np.asarray(image_bgr))
        except Exception:
            return empty
        if not faces:
            return empty

        records: list[FaceRecord] = []
        confidences: list[float] = []
        for face in faces:
            det = float(getattr(face, "det_score", 0.0))
            confidences.append(det)
            kps = np.asarray(getattr(face, "kps", np.empty((0, 2))), dtype=np.float64)
            eye_sharpness = (
                _eye_sharpness_from_keypoints(image_bgr, kps) if kps.shape[0] >= 2 else None
            )
            records.append(
                FaceRecord(
                    bbox=tuple(float(v) for v in getattr(face, "bbox", (0, 0, 0, 0))),
                    det_score=det,
                    eye_sharpness=eye_sharpness,
                    gender=self._gender(face),
                    age=self._age(face),
                    blink=None,  # deferred until EAR calibration
                )
            )

        main_idx = max(range(len(faces)), key=lambda i: confidences[i])
        return {
            "face_quality": aggregate_face_quality(confidences),
            "eye_sharpness": records[main_idx].eye_sharpness,
            "blink": None,
            "face_count": len(faces),
            "faces": records,
        }

    @staticmethod
    def _gender(face) -> str | None:
        sex = getattr(face, "sex", None)
        if isinstance(sex, str) and sex:
            return sex.upper()[:1]
        gender = getattr(face, "gender", None)
        if gender is None:
            return None
        return "M" if int(gender) == 1 else "F"

    @staticmethod
    def _age(face) -> int | None:
        age = getattr(face, "age", None)
        return int(age) if age is not None else None
