from __future__ import annotations

import json
import os


METRIC_PREFIX = "AI_METRIC "
METRIC_ENV_VAR = "IMAGE_TRIAGE_AI_METRICS"


def metrics_enabled() -> bool:
    return (os.environ.get(METRIC_ENV_VAR, "") or "").strip().casefold() in {
        "1",
        "true",
        "yes",
        "on",
    }


def emit_metric(event: str, **fields: object) -> None:
    if not metrics_enabled():
        return
    payload = {"event": str(event), **fields}
    print(METRIC_PREFIX + json.dumps(payload, default=str, sort_keys=True), flush=True)
