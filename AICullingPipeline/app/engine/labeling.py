"""Reusable engine entry points for the Week 3 labeling workflow."""

from __future__ import annotations

from app.config import LabelingConfig
from app.labeling.session import LabelingSession


def create_labeling_session(config: LabelingConfig) -> LabelingSession:
    """Create a reusable labeling session without launching the temporary UI."""

    return LabelingSession(config)
