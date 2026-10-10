"""Navigation latency: how long a photo takes to reach the screen after the key press that asked for it.

Image Triage's speed budget is measured from the navigation input to the first correct picture on
screen, not from whenever a controller happens to start work. The popout records three stages per
navigation, each in milliseconds since the input:

* ``first_pixel``: something of the photo is on screen (its grid thumbnail, at worst);
* ``preview``: the screen-sized picture (the camera's JPEG, or the cached render of the edits);
* ``refined``: the editable document is showing (PhotoCraft has the RAW or the sidecar open).

A navigation that is superseded before a stage is reached simply lacks that stage, so the
percentiles are over the navigations that reached it and the ``superseded`` count says how many
were abandoned. Set ``IMAGE_TRIAGE_NAV_TIMING`` to a file path to append one JSON line per
finished navigation; ``python -m image_triage.nav_timing <file>`` prints the summary.
"""
from __future__ import annotations

import json
import math
import os
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Iterable

TARGET_MS = 100.0
CEILING_MS = 200.0
STAGES = ("first_pixel", "preview", "refined")
ENV_VAR = "IMAGE_TRIAGE_NAV_TIMING"
_KEEP = 2000


@dataclass
class Navigation:
    token: int
    path: str
    started: float
    stages: dict[str, float] = field(default_factory=dict)
    superseded: bool = False

    def as_dict(self) -> dict:
        return {"path": self.path, "stages": dict(self.stages), "superseded": self.superseded}


class NavigationTimer:
    """Thread-safe recorder; the GUI thread calls ``begin``/``mark``, workers may call ``mark``."""

    def __init__(
        self,
        sink: Callable[[dict], None] | None = None,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        self._clock = clock
        self._sink = sink if sink is not None else _sink_from_environment()
        self._lock = threading.Lock()
        self._current: Navigation | None = None
        self._next_token = 0
        self.finished: deque[Navigation] = deque(maxlen=_KEEP)

    def now(self) -> float:
        return self._clock()

    def begin(self, path: str, started: float | None = None) -> int:
        """A navigation to ``path`` was requested now (or at ``started``, a ``now()`` reading)."""
        with self._lock:
            previous = self._current
            if previous is not None:
                previous.superseded = not self._complete(previous)
                self._retire(previous)
            self._next_token += 1
            self._current = Navigation(self._next_token, path, self._clock() if started is None else started)
            return self._current.token

    def mark(self, path: str, stage: str) -> float | None:
        """``stage`` was reached for ``path``. Returns its milliseconds since the input, or ``None``
        when ``path`` is not the navigation in flight, or the stage was already recorded."""
        if stage not in STAGES:
            raise ValueError(f"unknown stage {stage!r}")
        with self._lock:
            current = self._current
            if current is None or current.path != path or stage in current.stages:
                return None
            elapsed = round((self._clock() - current.started) * 1000.0, 3)
            current.stages[stage] = elapsed
            return elapsed

    def flush(self) -> None:
        """Retire the navigation in flight (end of a session, or before reading ``finished``)."""
        with self._lock:
            if self._current is not None:
                self._current.superseded = not self._complete(self._current)
                self._retire(self._current)
                self._current = None

    @staticmethod
    def _complete(navigation: Navigation) -> bool:
        return "refined" in navigation.stages or "preview" in navigation.stages

    def _retire(self, navigation: Navigation) -> None:
        self.finished.append(navigation)
        if self._sink is not None:
            try:
                self._sink(navigation.as_dict())
            except Exception:  # a broken log file must never slow or break navigation
                self._sink = None


def _sink_from_environment() -> Callable[[dict], None] | None:
    target = os.environ.get(ENV_VAR, "").strip()
    if not target:
        return None
    lock = threading.Lock()

    def write(record: dict) -> None:
        line = json.dumps(record, separators=(",", ":"))
        with lock, open(target, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    return write


def percentile(values: list[float], fraction: float) -> float:
    """Nearest-rank percentile; 0.0 for no data."""
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, min(len(ordered), math.ceil(fraction * len(ordered))))
    return ordered[rank - 1]


def summarize(records: Iterable[dict]) -> dict:
    """Per-stage count, p50, p95 and max, and how many exceeded the 100 ms target / 200 ms ceiling."""
    records = list(records)
    result: dict = {"navigations": len(records), "superseded": sum(1 for r in records if r.get("superseded"))}
    for stage in STAGES:
        values = [r["stages"][stage] for r in records if stage in r.get("stages", {})]
        result[stage] = {
            "count": len(values),
            "p50": round(percentile(values, 0.50), 1),
            "p95": round(percentile(values, 0.95), 1),
            "max": round(max(values), 1) if values else 0.0,
            "over_target": sum(1 for v in values if v > TARGET_MS),
            "over_ceiling": sum(1 for v in values if v > CEILING_MS),
        }
    return result


def format_summary(summary: dict) -> str:
    lines = [f"{summary['navigations']} navigations, {summary['superseded']} superseded"
             f" (target {TARGET_MS:.0f} ms, ceiling {CEILING_MS:.0f} ms)"]
    for stage in STAGES:
        row = summary[stage]
        lines.append(
            f"  {stage:<12} n={row['count']:<5} p50={row['p50']:>7.1f}  p95={row['p95']:>7.1f}  max={row['max']:>7.1f}"
            f"  over target={row['over_target']}  over ceiling={row['over_ceiling']}"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("usage: python -m image_triage.nav_timing <timing.jsonl>", file=sys.stderr)
        return 2
    with open(args[0], encoding="utf-8") as handle:
        records = [json.loads(line) for line in handle if line.strip()]
    print(format_summary(summarize(records)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
