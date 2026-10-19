"""Time estimates for ranking, shown before a run so the user can decide (CPU ranking is opt-in)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping

from .spec import MANIQA, QREALIGN, RankerSpec

REFERENCE_NOTE = "Reference timings: V100 GPU and an 8-thread CPU. This machine will differ."


@dataclass(frozen=True)
class RankingCostEstimate:
    device: str  # "gpu" or "cpu"
    maniqa_frames: int
    aesthetic_frames: int
    maniqa_seconds: float
    aesthetic_seconds: float
    note: str = REFERENCE_NOTE

    @property
    def total_seconds(self) -> float:
        return self.maniqa_seconds + self.aesthetic_seconds


def per_frame_seconds(spec: RankerSpec, device: str) -> float:
    return spec.seconds_per_frame_gpu if _family(device) == "gpu" else spec.seconds_per_frame_cpu


def estimate_cost(
    maniqa_frames: int,
    aesthetic_frames: int,
    device: str = "gpu",
    *,
    measured: Mapping[str, float] | None = None,
) -> RankingCostEstimate:
    """frames x per-frame seconds. ``measured`` overrides the reference timings per ranker kind
    (for example ``{"maniqa": ranker.seconds_per_frame}`` once a run has timed this machine)."""
    if maniqa_frames < 0 or aesthetic_frames < 0:
        raise ValueError("frame counts must not be negative")
    family = _family(device)
    measured = measured or {}
    m = measured.get(MANIQA.kind, per_frame_seconds(MANIQA, family))
    a = measured.get(QREALIGN.kind, per_frame_seconds(QREALIGN, family))
    return RankingCostEstimate(family, maniqa_frames, aesthetic_frames, maniqa_frames * m, aesthetic_frames * a)


def estimate_cascade_cost(
    survivors_per_stack: Iterable[int],
    maniqa_keep: int = 3,
    device: str = "gpu",
    *,
    measured: Mapping[str, float] | None = None,
) -> RankingCostEstimate:
    """The cascade scores each stack's survivors with MANIQA only when there are more than ``maniqa_keep``, then at most
    ``maniqa_keep`` of them with the aesthetic ranker when more than one remains (see pipeline.run_cascade)."""
    maniqa = aesthetic = 0
    for count in survivors_per_stack:
        if count > maniqa_keep:
            maniqa += count
        kept = min(count, maniqa_keep)
        if kept > 1:
            aesthetic += kept
    return estimate_cost(maniqa, aesthetic, device, measured=measured)


def _family(device: str) -> str:
    return "gpu" if (device or "").strip().lower().startswith(("gpu", "cuda")) else "cpu"
