"""Aesthetic dimension via CLIP text-projection (Phase 2).

The classical dimensions capture technical/reject signal but are blind to the
content/composition that actually drives keepers. This adds a cheap content-
aesthetic axis by reusing the already-stored CLIP image embeddings: build an
"aesthetic direction" in CLIP space from positive/negative text prompts, then
score each image by the cosine of its embedding onto that direction.

No new dependency: the projection math here is pure NumPy and takes an ``encode``
callable, so the caller supplies ``aiculler.text_scoring.CLIPTextEncoder.encode``
(ONNX) at runtime, and tests can supply a mock. Honest expectation (per FACET):
this is a fast supplementary signal (~0.4 corr with AVA), not a TOPIQ replacement.
"""

from __future__ import annotations

from typing import Callable, Sequence

import numpy as np

EncodeFn = Callable[[str], np.ndarray]

DEFAULT_POSITIVE_PROMPTS: tuple[str, ...] = (
    "a beautiful professional photograph",
    "a striking, well-composed image",
    "an award-winning photo with great light",
    "a stunning, visually compelling photograph",
    "a captivating photo with strong subject and depth",
)
DEFAULT_NEGATIVE_PROMPTS: tuple[str, ...] = (
    "a boring, dull snapshot",
    "a poorly composed, cluttered photo",
    "a bland, forgettable image",
    "an amateur photo with flat lighting",
    "a throwaway, uninteresting picture",
)


def _l2(vector: np.ndarray) -> np.ndarray:
    v = np.asarray(vector, dtype=np.float64).reshape(-1)
    norm = float(np.linalg.norm(v))
    return v / norm if norm > 0 else v


def build_aesthetic_direction(
    encode: EncodeFn,
    positive: Sequence[str] = DEFAULT_POSITIVE_PROMPTS,
    negative: Sequence[str] = DEFAULT_NEGATIVE_PROMPTS,
) -> np.ndarray:
    """Unit vector in CLIP space pointing from 'boring' toward 'beautiful'."""
    pos = np.mean([_l2(encode(p)) for p in positive], axis=0)
    neg = np.mean([_l2(encode(p)) for p in negative], axis=0)
    return _l2(pos - neg)


def aesthetic_score(image_embedding: np.ndarray, direction: np.ndarray) -> float:
    """Cosine of the image embedding onto the aesthetic direction, mapped to 0-10.

    Spearman ranking is unaffected by the linear remap; the 0-10 scale just keeps
    it consistent with the other dimensions. Folder-relative normalization (for
    cross-folder use) is applied downstream.
    """
    cosine = float(np.dot(_l2(image_embedding), _l2(direction)))
    return float(min(10.0, max(0.0, (cosine + 1.0) * 5.0)))
