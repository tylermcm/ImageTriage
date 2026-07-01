"""Quality dimension scoring for the v4 AI approach.

Computes interpretable per-image quality dimensions (FACET-style) that the
reject filter and the per-category preference weighting are built on. See
``docs/ai_v4_plan.md``.
"""

from __future__ import annotations

from .model import DimensionScores
from .technical import analyze_technical

__all__ = ["DimensionScores", "analyze_technical"]
