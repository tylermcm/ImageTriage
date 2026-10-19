"""Stack-first culling cascade: pure decision logic for AI v5 (see docs/ai_v5_implementation.md)."""

from __future__ import annotations

from .model import (
    FLAG_BLINK,
    FLAG_BLOWN_OUT,
    FLAG_CLIPPED,
    FLAG_SOFT,
    CascadeResult,
    CascadeSettings,
    FrameDecision,
    FrameFeatures,
    FrameRole,
    StackResult,
)
from .pipeline import Scorer, run_cascade

__all__ = [
    "FLAG_BLINK",
    "FLAG_BLOWN_OUT",
    "FLAG_CLIPPED",
    "FLAG_SOFT",
    "CascadeResult",
    "CascadeSettings",
    "FrameDecision",
    "FrameFeatures",
    "FrameRole",
    "Scorer",
    "StackResult",
    "run_cascade",
]
