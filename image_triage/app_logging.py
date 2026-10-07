"""Always-on error/warning logging for Image Triage.

This is deliberately separate from :mod:`image_triage.perf`'s
``PerformanceLogger``: that one is an opt-in JSONL *timing* stream gated
behind a Settings toggle (off by default). This module is a plain
``logging``-based error logger that is always on, requires no user action,
and is safe to use from anywhere via the usual
``logging.getLogger(__name__)`` pattern.

Call :func:`configure_app_logging` once at startup (see ``main.py``). After
that, any module can do::

    import logging
    _logger = logging.getLogger(__name__)

    try:
        ...
    except Exception:
        _logger.exception("something failed")

and it will land in the rotating ``errors.log`` file described below, with
no further setup required.
"""

from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path

from .perf import performance_log_dir

_LOG_FILENAME = "errors.log"
_MAX_BYTES = 5 * 1024 * 1024
_BACKUP_COUNT = 3

_configured = False


def app_log_path() -> Path:
    """Where the always-on error log lives: alongside the perf JSONL logs
    and the execution.log, so all of Image Triage's diagnostic output is
    found in one directory."""

    return performance_log_dir() / _LOG_FILENAME


def configure_app_logging(level: int = logging.WARNING) -> logging.Logger:
    """Configure the root logger with a rotating file handler, once.

    Safe to call multiple times (subsequent calls are no-ops) and safe to
    call before the log directory exists — it is created here.
    """

    global _configured
    root_logger = logging.getLogger()
    if _configured:
        return root_logger

    log_dir = performance_log_dir()
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / _LOG_FILENAME

    handler = logging.handlers.RotatingFileHandler(
        log_path,
        maxBytes=_MAX_BYTES,
        backupCount=_BACKUP_COUNT,
        encoding="utf-8",
    )
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s %(levelname)s %(name)s: %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        )
    )
    handler.setLevel(level)

    root_logger.addHandler(handler)
    if root_logger.level == logging.NOTSET or root_logger.level > level:
        root_logger.setLevel(level)

    _configured = True
    return root_logger


def get_logger(name: str) -> logging.Logger:
    """Convenience accessor. Equivalent to ``logging.getLogger(name)`` —
    provided so callers don't need to import both modules."""

    return logging.getLogger(name)
