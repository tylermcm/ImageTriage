"""The People index's face analyzer: the shared face finder (landmarker + AdaFace in a worker process) behind the interface ``face_index`` already uses.

It replaces the InsightFace analyzer. A file's faces come from ``cascade.faces.FaceCache`` when the folder cascade or an earlier scan already analysed it; otherwise the
worker reads the file (RAW through its embedded preview), finds the prominent faces and embeds them, and the result is cached. Boxes are stored as pixels of the preview
image the People window crops from, converted from the fractions the worker reports.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from .cascade.blink import BLINK_THRESHOLD
from .cascade.faces import FACE_MODEL_TAG, FaceCache, FaceFinding
from .quality.face import FaceRecord


def face_cache_path(artifacts_dir: str | Path) -> Path:
    """Where the faces of a folder are remembered, shared by the cascade and the People index."""
    return Path(artifacts_dir) / "faces_cache.json"


def findings_to_records(findings: list[FaceFinding], width: int, height: int) -> list[FaceRecord]:
    """The face records the face store keeps, with boxes in pixels of a ``width`` x ``height`` image."""
    records: list[FaceRecord] = []
    for finding in findings:
        left, top, right, bottom = finding.box
        records.append(
            FaceRecord(
                bbox=(left * width, top * height, right * width, bottom * height),
                det_score=1.0,  # the landmarker reports no confidence; a face it returned at this size is a face
                eye_sharpness=None,
                gender=None,  # age and gender are not estimated: the recognizer weights that did it are not licensed for this
                age=None,
                blink=None if finding.eyes_closed is None else finding.eyes_closed >= BLINK_THRESHOLD,
                identity_embedding=finding.embedding,
                identity_model=FACE_MODEL_TAG if finding.embedding else "",
            )
        )
    return records


class FaceStackAnalyzer:
    """``analyze_source(source_path, preview_path)`` -> the dict ``FaceFolderIndexTask`` stores. Construct once per scan; ``close`` stops the worker."""

    wants_source_path = True

    def __init__(
        self,
        *,
        cache: FaceCache | None = None,
        cancel_check: Callable[[], bool] | None = None,
        ranker=None,
    ) -> None:
        self.available = True
        self.initialization_error = ""
        self._cache = cache
        self._cancel_check = cancel_check
        self._ranker = ranker
        self.analysed = 0  # files the worker had to read (the rest came from the cache)

    def _worker(self):
        if self._ranker is None:
            from .cascade.rankers import faces_ranker

            self._ranker = faces_ranker(device=self._worker_device(), cancel_check=self._cancel_check)
        return self._ranker

    @staticmethod
    def _worker_device() -> str:
        """The face finder runs on the CPU when the CPU runtime is installed. With only the GPU runtime installed an explicit CPU request has no profile
        to run in (select_runtime never lends out the GPU profile for one), so the worker is started on the GPU profile instead."""
        try:
            from .ai_runtime_packages import load_ai_runtime_installation_status

            installed = set(load_ai_runtime_installation_status().installed_variants)
        except Exception:
            return "cpu"
        return "cpu" if (not installed or "cpu" in installed) else "cuda"

    def close(self) -> None:
        ranker, self._ranker = self._ranker, None
        if ranker is not None:
            ranker.close()
        if self._cache is not None:
            try:
                self._cache.save()
            except OSError:
                pass

    def analyze_source(self, source_path: str, preview_path: str | Path) -> dict[str, object]:
        from PIL import Image

        findings = self._cache.get(source_path) if self._cache is not None else None
        if findings is None:
            worker = self._worker()
            scores = worker([source_path])
            if source_path in worker.failures or source_path not in scores:
                raise OSError(worker.failures.get(source_path, "the face finder could not read the file"))
            detail = worker.details.get(source_path, {})
            findings = [FaceFinding.from_json(item) for item in detail.get("faces", [])]
            self.analysed += 1
            if self._cache is not None:
                self._cache.put(source_path, findings)
        with Image.open(preview_path) as opened:
            width, height = opened.size
        records = findings_to_records(findings, width, height)
        return {"face_quality": None, "eye_sharpness": None, "blink": None, "face_count": len(records), "faces": records}
