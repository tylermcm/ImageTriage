"""Logging configuration helpers."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional


def setup_logging(level: str, *, log_file: Optional[Path] = None) -> None:
    """Configure console and optional file logging for the pipeline."""

    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))

    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        handlers=handlers,
        force=True,
    )
