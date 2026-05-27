"""First-class specialist signal layer slots.

These layers are part of the scoring architecture from day one. They are
capability-gated so the production stack can run before optional model packages
are installed.
"""

from __future__ import annotations

from dataclasses import replace
from importlib.util import find_spec
from pathlib import Path
from typing import Dict

import numpy as np

from app.engine.signals.layers import SignalLayerContext, append_layer_status
from app.engine.signals.models import (
    AestheticSignals,
    FaceSignals,
    ImageSignalRecord,
    LayerStatus,
    SubjectSignals,
)


class FaceEyeSpecialistLayer:
    """Face/eye specialist slot for portrait and people-heavy folders."""

    layer_id = "face_eye"
    display_name = "Face and Eye Specialist"
    required_stack_slot = True

    def status(self) -> LayerStatus:
        backend = "OpenCV Haar" if find_spec("cv2") is not None else ""
        return LayerStatus(
            layer_id=self.layer_id,
            display_name=self.display_name,
            enabled=True,
            available=bool(backend),
            status="ready" if backend else "missing_backend",
            backend=backend,
            reason="" if backend else "Install OpenCV to enable face/eye scoring.",
        )

    def analyze(
        self,
        records: Dict[str, ImageSignalRecord],
        context: SignalLayerContext,
    ) -> Dict[str, ImageSignalRecord]:
        status = self.status()
        updated = dict(records)
        if not status.available:
            for image_id, record in records.items():
                face = FaceSignals(
                    status="not_analyzed",
                    backend=status.backend,
                    reason=status.reason,
                )
                updated[image_id] = replace(record, subject=replace(record.subject, face=face))
            return append_layer_status(updated, status)

        failures = 0
        for image_id, record in records.items():
            face, subject_updates = analyze_face_eye_quality(
                Path(record.file_path),
                max_side=context.max_preview_side,
            )
            if face.status == "failed":
                failures += 1
            updated[image_id] = replace(
                record,
                subject=replace(record.subject, face=face, **subject_updates),
            )
        reason = f"{failures} image(s) failed face/eye analysis." if failures else ""
        return append_layer_status(updated, replace(status, status="analyzed", reason=reason))


def analyze_face_eye_quality(path: Path, *, max_side: int = 768) -> tuple[FaceSignals, dict[str, object]]:
    """Detect faces/eyes with OpenCV Haar cascades and return lightweight subject signals."""

    try:
        import cv2  # type: ignore
    except Exception as exc:  # pragma: no cover - depends on optional runtime package set
        return (
            FaceSignals(status="not_analyzed", backend="", reason=f"OpenCV unavailable: {exc}"),
            {
                "status": "not_analyzed",
                "backend": "",
                "reason": f"OpenCV unavailable: {exc}",
            },
        )

    _quiet_opencv_warnings(cv2)
    try:
        gray = _read_preview_gray(path, cv2=cv2, max_side=max_side)
        face_cascade = _load_cascade(cv2, "haarcascade_frontalface_default.xml")
        eye_cascade = _load_cascade(cv2, "haarcascade_eye_tree_eyeglasses.xml")
        faces = face_cascade.detectMultiScale(
            gray,
            scaleFactor=1.1,
            minNeighbors=5,
            flags=0,
            minSize=(24, 24),
        )
    except Exception as exc:
        return (
            FaceSignals(status="failed", backend="OpenCV Haar", reason=str(exc)),
            {
                "status": "failed",
                "backend": "OpenCV Haar",
                "reason": str(exc),
            },
        )

    face_count = int(len(faces))
    if face_count <= 0:
        return (
            FaceSignals(
                face_count=0,
                status="analyzed",
                backend="OpenCV Haar",
                reason="No face detected.",
            ),
            {
                "primary_subject_label": None,
                "subject_confidence": 0.0,
                "subject_box_area_ratio": None,
                "subject_centering_score": None,
                "detected_labels": [],
                "status": "analyzed",
                "backend": "OpenCV Haar",
                "reason": "No face detected.",
            },
        )

    x, y, width, height = _largest_box(faces)
    face_region = gray[y : y + height, x : x + width]
    face_sharpness = _sharpness_score(face_region)
    eye_score, blink_detected = _eye_open_score(face_region, eye_cascade, cv2=cv2)
    area_ratio = _box_area_ratio(width, height, gray.shape[1], gray.shape[0])
    centering_score = _centering_score(x, y, width, height, gray.shape[1], gray.shape[0])
    confidence = _face_confidence(area_ratio=area_ratio, eye_score=eye_score)
    return (
        FaceSignals(
            face_count=face_count,
            primary_face_confidence=confidence,
            face_sharpness_score=face_sharpness,
            eye_open_score=eye_score,
            blink_detected=blink_detected,
            status="analyzed",
            backend="OpenCV Haar",
            reason="",
        ),
        {
            "primary_subject_label": "person",
            "subject_confidence": confidence,
            "subject_box_area_ratio": area_ratio,
            "subject_centering_score": centering_score,
            "detected_labels": ["person", "face"],
            "status": "analyzed",
            "backend": "OpenCV Haar",
            "reason": "",
        },
    )


class ObjectSubjectSpecialistLayer:
    """General subject/object detection specialist slot."""

    layer_id = "object_subject"
    display_name = "Subject Detection Specialist"
    required_stack_slot = True

    def status(self) -> LayerStatus:
        backend = _first_available_backend(("ultralytics", "onnxruntime"))
        return LayerStatus(
            layer_id=self.layer_id,
            display_name=self.display_name,
            enabled=True,
            available=bool(backend),
            status="ready" if backend else "missing_backend",
            backend=backend,
            reason="" if backend else "Install YOLO/Ultralytics or ONNX Runtime detector backend to enable subject scoring.",
        )

    def analyze(
        self,
        records: Dict[str, ImageSignalRecord],
        context: SignalLayerContext,
    ) -> Dict[str, ImageSignalRecord]:
        status = self.status()
        updated = dict(records)
        if not status.available:
            for image_id, record in records.items():
                updated[image_id] = _replace_subject_status_if_empty(record, status=status)
            return append_layer_status(updated, status)

        for image_id, record in records.items():
            updated[image_id] = _replace_subject_status_if_empty(
                record,
                status=replace(
                    status,
                    status="not_analyzed",
                    reason="Backend detected; inference implementation pending.",
                ),
            )
        return append_layer_status(updated, replace(status, status="implementation_pending"))


class AestheticSpecialistLayer:
    """Aesthetic/composition specialist slot."""

    layer_id = "aesthetic"
    display_name = "Aesthetic Specialist"
    required_stack_slot = True
    backend_name = "Pillow/NumPy composition heuristic"

    def status(self) -> LayerStatus:
        backend = self.backend_name if _pil_available() else ""
        return LayerStatus(
            layer_id=self.layer_id,
            display_name=self.display_name,
            enabled=True,
            available=bool(backend),
            status="ready" if backend else "missing_backend",
            backend=backend,
            reason="" if backend else "Install Pillow to enable heuristic composition scoring.",
        )

    def analyze(
        self,
        records: Dict[str, ImageSignalRecord],
        context: SignalLayerContext,
    ) -> Dict[str, ImageSignalRecord]:
        status = self.status()
        updated = dict(records)
        if not status.available:
            for image_id, record in records.items():
                updated[image_id] = replace(
                    record,
                    aesthetic=AestheticSignals(
                        status="not_analyzed",
                        backend=status.backend,
                        reason=status.reason,
                    ),
                )
            return append_layer_status(updated, status)

        failures = 0
        for image_id, record in records.items():
            aesthetic = analyze_aesthetic_quality(
                Path(record.file_path),
                record=record,
                max_side=context.max_preview_side,
            )
            if aesthetic.status == "failed":
                failures += 1
            updated[image_id] = replace(
                record,
                aesthetic=aesthetic,
            )
        reason = f"{failures} image(s) failed aesthetic analysis." if failures else ""
        return append_layer_status(updated, replace(status, status="analyzed", reason=reason))


def specialist_layers() -> tuple[
    FaceEyeSpecialistLayer,
    ObjectSubjectSpecialistLayer,
    AestheticSpecialistLayer,
]:
    """Return the required specialist stack slots in execution order."""

    return (
        FaceEyeSpecialistLayer(),
        ObjectSubjectSpecialistLayer(),
        AestheticSpecialistLayer(),
    )


def _read_preview_gray(path: Path, *, cv2: object, max_side: int) -> np.ndarray:
    encoded = np.fromfile(str(path), dtype=np.uint8)
    if encoded.size == 0:
        raise ValueError(f"Empty image file: {path}")
    decoded = cv2.imdecode(encoded, cv2.IMREAD_GRAYSCALE)
    if decoded is None:
        raise ValueError(f"OpenCV could not decode image: {path}")
    height, width = decoded.shape[:2]
    largest_side = max(height, width)
    if largest_side > max_side > 0:
        scale = float(max_side) / float(largest_side)
        decoded = cv2.resize(
            decoded,
            (max(1, int(round(width * scale))), max(1, int(round(height * scale)))),
            interpolation=cv2.INTER_AREA,
        )
    return decoded


def analyze_aesthetic_quality(
    path: Path,
    *,
    record: ImageSignalRecord | None = None,
    max_side: int = 768,
) -> AestheticSignals:
    """Estimate composition/aesthetic quality from lightweight image statistics."""

    try:
        from PIL import Image, ImageOps
    except Exception as exc:  # pragma: no cover - depends on optional runtime package set
        return AestheticSignals(
            status="not_analyzed",
            backend=AestheticSpecialistLayer.backend_name,
            reason=f"Pillow unavailable: {exc}",
        )

    try:
        with Image.open(path) as image:
            image = ImageOps.exif_transpose(image)
            image.thumbnail((max_side, max_side))
            rgb = image.convert("RGB")
            array = np.asarray(rgb, dtype=np.float32) / 255.0
    except Exception as exc:
        return AestheticSignals(
            status="failed",
            backend=AestheticSpecialistLayer.backend_name,
            reason=str(exc),
        )

    if array.size == 0 or array.ndim != 3:
        return AestheticSignals(
            status="failed",
            backend=AestheticSpecialistLayer.backend_name,
            reason="Empty image array.",
        )

    gray = (
        array[:, :, 0] * 0.2126
        + array[:, :, 1] * 0.7152
        + array[:, :, 2] * 0.0722
    ).astype(np.float32, copy=False)
    colorfulness = (array.max(axis=2) - array.min(axis=2)).astype(np.float32, copy=False)
    edge = _gradient_magnitude(gray)
    saliency = edge + colorfulness * 0.12
    saliency_max = float(saliency.max())
    if saliency_max > 0:
        saliency = saliency / saliency_max

    composition_score, clutter_score = _composition_scores(saliency, edge)
    saturation_score = _saturation_score(float(colorfulness.mean()))
    contrast_score = _bounded(float(gray.std()), scale=0.28)
    exposure_score = _bounded(record.technical.exposure_score if record is not None else None)
    if exposure_score <= 0:
        exposure_score = 0.55

    aesthetic_score = _bounded(
        composition_score * 0.55
        + saturation_score * 0.15
        + contrast_score * 0.15
        + exposure_score * 0.15
    )
    return AestheticSignals(
        aesthetic_score=aesthetic_score,
        composition_score=composition_score,
        clutter_score=clutter_score,
        status="analyzed",
        backend=AestheticSpecialistLayer.backend_name,
        reason="Heuristic composition estimate.",
    )


def _gradient_magnitude(gray: np.ndarray) -> np.ndarray:
    if gray.shape[0] < 3 or gray.shape[1] < 3:
        return np.zeros_like(gray, dtype=np.float32)
    gy, gx = np.gradient(gray)
    return np.sqrt(gx * gx + gy * gy).astype(np.float32, copy=False)


def _composition_scores(saliency: np.ndarray, edge: np.ndarray) -> tuple[float, float]:
    height, width = saliency.shape[:2]
    if height < 2 or width < 2:
        return 0.0, 1.0

    mass = float(saliency.sum())
    if mass <= 1e-8:
        return 0.35, 0.0

    y_indices, x_indices = np.indices((height, width), dtype=np.float32)
    center_x = float((x_indices * saliency).sum() / mass) / max(1.0, float(width - 1))
    center_y = float((y_indices * saliency).sum() / mass) / max(1.0, float(height - 1))

    thirds_score = _thirds_score(center_x, center_y)
    center_score = _center_score(center_x, center_y)
    balance_score = _balance_score(saliency)
    foreground_score = _bounded(float(saliency.std()), scale=0.26)
    edge_density = float((edge > 0.08).mean())
    clutter_score = _bounded(edge_density, scale=0.30)
    composition_score = _bounded(
        max(thirds_score, center_score * 0.92) * 0.42
        + balance_score * 0.24
        + foreground_score * 0.20
        + (1.0 - clutter_score * 0.65) * 0.14
    )
    return composition_score, clutter_score


def _thirds_score(center_x: float, center_y: float) -> float:
    points = ((1 / 3, 1 / 3), (2 / 3, 1 / 3), (1 / 3, 2 / 3), (2 / 3, 2 / 3))
    distance = min(((center_x - x) ** 2 + (center_y - y) ** 2) ** 0.5 for x, y in points)
    return float(max(0.0, min(1.0, 1.0 - distance / 0.48)))


def _center_score(center_x: float, center_y: float) -> float:
    distance = ((center_x - 0.5) ** 2 + (center_y - 0.5) ** 2) ** 0.5
    return float(max(0.0, min(1.0, 1.0 - distance / 0.52)))


def _balance_score(saliency: np.ndarray) -> float:
    total = float(saliency.sum())
    if total <= 1e-8:
        return 0.5
    height, width = saliency.shape[:2]
    left = float(saliency[:, : width // 2].sum()) / total
    top = float(saliency[: height // 2, :].sum()) / total
    imbalance = abs(left - 0.5) + abs(top - 0.5)
    return float(max(0.0, min(1.0, 1.0 - imbalance * 1.55)))


def _saturation_score(mean_colorfulness: float) -> float:
    if mean_colorfulness <= 0.05:
        return 0.58
    return float(max(0.0, min(1.0, 1.0 - abs(mean_colorfulness - 0.22) / 0.42)))


def _load_cascade(cv2: object, filename: str) -> object:
    cascade_path = Path(cv2.data.haarcascades) / filename
    cascade = cv2.CascadeClassifier(str(cascade_path))
    if cascade.empty():
        raise ValueError(f"OpenCV cascade not found: {cascade_path}")
    return cascade


def _largest_box(boxes: object) -> tuple[int, int, int, int]:
    box_array = np.asarray(boxes, dtype=np.int32)
    areas = box_array[:, 2] * box_array[:, 3]
    x, y, width, height = box_array[int(np.argmax(areas))].tolist()
    return int(x), int(y), int(width), int(height)


def _eye_open_score(face_gray: np.ndarray, eye_cascade: object, *, cv2: object) -> tuple[float | None, bool | None]:
    if face_gray.size == 0:
        return None, None
    upper_face = face_gray[: max(1, int(face_gray.shape[0] * 0.65)), :]
    min_side = max(6, int(min(face_gray.shape[:2]) * 0.10))
    eyes = eye_cascade.detectMultiScale(
        upper_face,
        scaleFactor=1.08,
        minNeighbors=4,
        flags=0,
        minSize=(min_side, min_side),
    )
    eye_count = int(len(eyes))
    if eye_count >= 2:
        return 1.0, False
    if eye_count == 1:
        return 0.55, None
    if min(face_gray.shape[:2]) < 36:
        return None, None
    return 0.0, True


def _sharpness_score(gray: np.ndarray) -> float | None:
    if gray.size == 0 or gray.shape[0] < 8 or gray.shape[1] < 8:
        return None
    normalized = gray.astype(np.float32, copy=False) / 255.0
    laplacian = (
        normalized[1:-1, 1:-1] * 4.0
        - normalized[:-2, 1:-1]
        - normalized[2:, 1:-1]
        - normalized[1:-1, :-2]
        - normalized[1:-1, 2:]
    )
    return _bounded_log_score(float(laplacian.var()))


def _box_area_ratio(width: int, height: int, image_width: int, image_height: int) -> float:
    denominator = max(1, int(image_width) * int(image_height))
    return float(max(0.0, min(1.0, (int(width) * int(height)) / denominator)))


def _centering_score(x: int, y: int, width: int, height: int, image_width: int, image_height: int) -> float:
    center_x = (float(x) + float(width) / 2.0) / max(1.0, float(image_width))
    center_y = (float(y) + float(height) / 2.0) / max(1.0, float(image_height))
    distance = ((center_x - 0.5) ** 2 + (center_y - 0.5) ** 2) ** 0.5
    return float(max(0.0, min(1.0, 1.0 - distance * 2.0)))


def _face_confidence(*, area_ratio: float, eye_score: float | None) -> float:
    size_component = max(0.15, min(1.0, area_ratio / 0.08))
    eye_component = 0.45 if eye_score is None else float(eye_score)
    return float(max(0.0, min(1.0, size_component * 0.55 + eye_component * 0.45)))


def _bounded(value: float | None, *, scale: float = 1.0) -> float:
    if value is None:
        return 0.0
    try:
        numeric = float(value) / scale
    except (TypeError, ValueError, ZeroDivisionError):
        return 0.0
    return float(max(0.0, min(1.0, numeric)))


def _bounded_log_score(value: float) -> float:
    return float(min(1.0, max(0.0, np.log1p(max(0.0, value) * 600.0) / np.log1p(600.0))))


def _replace_subject_status_if_empty(record: ImageSignalRecord, *, status: LayerStatus) -> ImageSignalRecord:
    subject = record.subject
    if (
        subject.primary_subject_label
        or subject.subject_confidence is not None
        or subject.face.status == "analyzed"
    ):
        return record
    return replace(
        record,
        subject=replace(
            subject,
            status=status.status if status.status != "ready" else "not_analyzed",
            backend=status.backend,
            reason=status.reason,
        ),
    )


def _first_available_backend(module_names: tuple[str, ...]) -> str:
    for module_name in module_names:
        if find_spec(module_name) is not None:
            return module_name
    return ""


def _pil_available() -> bool:
    try:
        import PIL  # noqa: F401
    except Exception:
        return False
    return True


def _quiet_opencv_warnings(cv2: object) -> None:
    setter = getattr(cv2, "setLogLevel", None)
    if setter is None:
        return
    try:
        setter(2)
    except Exception:
        return
