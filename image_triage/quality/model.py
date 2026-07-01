"""Per-image quality dimension scores (FACET-style).

Each dimension is an interpretable 0-10 axis describing one aspect of image
quality or execution. The ``v4`` AI approach measures these explicitly (mostly
classical CV plus a few pretrained specialists) instead of trying to learn
quality from frozen embeddings, then personalizes with a small per-category
weighting over the vector.

Phase 1 fills the classical-CV dimensions; the pretrained-specialist fields
(aesthetic, composition, saliency, face/eye) are added in Phase 2 and default
to ``None`` until then.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(slots=True)
class DimensionScores:
    # -- Phase 1: classical CV (no models, no data) -----------------------
    sharpness: float | None = None
    exposure: float | None = None
    dynamic_range: float | None = None
    noise: float | None = None
    contrast: float | None = None
    color_harmony: float | None = None
    monochrome: bool | None = None

    # -- Phase 2: pretrained specialists (filled later) -------------------
    aesthetic: float | None = None
    composition: float | None = None
    saliency: float | None = None
    face_quality: float | None = None
    eye_sharpness: float | None = None
    blink: bool | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)
