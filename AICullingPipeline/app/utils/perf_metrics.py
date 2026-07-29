"""Structured stdout metrics for host-side performance logging."""

from __future__ import annotations

from contextlib import contextmanager
import json
import os
import time
from pathlib import Path
from typing import Any, Iterator


METRIC_PREFIX = "AI_METRIC "
METRIC_ENV_VAR = "IMAGE_TRIAGE_AI_METRICS"


def metrics_enabled() -> bool:
    value = os.environ.get(METRIC_ENV_VAR, "")
    return value.strip().casefold() in {"1", "true", "yes", "on"}


def now_ms(start: float) -> float:
    return (time.perf_counter() - start) * 1000.0


def emit_metric(event: str, **fields: object) -> None:
    if not metrics_enabled():
        return
    payload: dict[str, object] = {"event": event}
    payload.update({key: _safe_metric_value(value) for key, value in fields.items()})
    print(METRIC_PREFIX + json.dumps(payload, ensure_ascii=False, separators=(",", ":")), flush=True)


@contextmanager
def metric_span(event: str, **fields: object) -> Iterator[dict]:
    """Time a block and emit ``event`` with its duration when metrics are on.

    Yields a dict the caller can populate with results known only after the
    block (counts, sizes); those merge into the emitted fields. Zero overhead
    when metrics are disabled.
    """
    if not metrics_enabled():
        yield {}
        return
    extra: dict[str, object] = {}
    start = time.perf_counter()
    try:
        yield extra
    finally:
        emit_metric(event, duration_ms=now_ms(start), **{**fields, **extra})


def _safe_metric_value(value: object) -> object:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (list, tuple)):
        return [_safe_metric_value(item) for item in value[:20]]
    if isinstance(value, dict):
        return {str(key): _safe_metric_value(item_value) for key, item_value in list(value.items())[:20]}
    return str(value)
