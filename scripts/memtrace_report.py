"""Turn a memtrace performance log into a readable report: Markdown, a self-contained HTML page with charts, and a JSON summary.

    python scripts/memtrace_report.py <performance_*.jsonl> [--nav nav_timing_*.jsonl] [--out DIR]

Reads the ``mem.*`` events (``mem.sample`` every interval, ``mem.mark`` at phase boundaries, ``mem.span`` around short
transients) plus ``nav.finished`` / ``preview.task`` / ``thumbnail.task`` for latency correlation. Standard library only, so it runs
on any machine that has Python; charts are inline SVG.

What the numbers are: process figures (private bytes, working set) come from the operating system; cache figures are what the
application says it holds (``exact`` = a byte count it keeps, ``estimated`` = derived). They are reported side by side and never
added together. Conclusions that need more than one run are labelled as such.
"""
from __future__ import annotations

import argparse
import html
import json
import math
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

MIB = 1024 * 1024
RETENTION_FLAG_BYTES_PER_CYCLE = 5 * MIB
RETENTION_FLAG_R2 = 0.8


def mib(value: float | int | None, digits: int = 1) -> str:
    if value is None:
        return "-"
    return f"{value / MIB:,.{digits}f}"


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, min(len(ordered), math.ceil(fraction * len(ordered))))
    return ordered[rank - 1]


def load_events(path: Path) -> list[dict[str, Any]]:
    events = []
    with path.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # a truncated last line from a killed process
    return events


def seconds(event: dict[str, Any]) -> float:
    return float(event.get("elapsed_ms", 0.0)) / 1000.0


# --------------------------------------------------------------------------------------------------------------------------
# Model
# --------------------------------------------------------------------------------------------------------------------------


class Run:
    def __init__(self, events: list[dict[str, Any]]) -> None:
        self.events = events
        self.start = next((e for e in events if e["event"] == "mem.start"), {})
        self.samples = [e for e in events if e["event"] in ("mem.sample", "mem.final") and "tree" in e]
        self.marks = [e for e in events if e["event"] == "mem.mark"]
        self.spans = [e for e in events if e["event"] == "mem.span"]
        self.navs = [e for e in events if e["event"] == "nav.finished"]
        self.errors = [e for e in events if e["event"] == "mem.error"]
        self.phases = self._phases()

    def _phases(self) -> list[dict[str, Any]]:
        open_: dict[str, float] = {}
        phases = []
        for mark in self.marks:
            label = str(mark.get("label", ""))
            if label.startswith("phase:") and label.endswith(":start"):
                open_[label[6:-6]] = seconds(mark)
            elif label.startswith("phase:") and label.endswith(":end"):
                name = label[6:-4]
                if name in open_:
                    phases.append({"name": name, "start": open_.pop(name), "end": seconds(mark)})
        return phases

    def mark_time(self, label: str) -> float | None:
        for mark in self.marks:
            if mark.get("label") == label:
                return seconds(mark)
        return None

    def phase_at(self, t: float) -> str:
        for phase in self.phases:
            if phase["start"] <= t <= phase["end"]:
                return phase["name"]
        return "-"

    def sample_at_or_before(self, t: float) -> dict[str, Any] | None:
        best = None
        for sample in self.samples:
            if seconds(sample) <= t:
                best = sample
            else:
                break
        return best

    def sample_near(self, t: float) -> dict[str, Any] | None:
        if not self.samples:
            return None
        return min(self.samples, key=lambda s: abs(seconds(s) - t))

    def window(self, a: float, b: float) -> list[dict[str, Any]]:
        return [s for s in self.samples if a <= seconds(s) <= b]


def own(sample: dict[str, Any], key: str) -> int:
    return int(sample["tree"]["own"].get(key, 0))


def role(sample: dict[str, Any], name: str, key: str = "private") -> int:
    return int(sample["tree"].get("roles", {}).get(name, {}).get(key, 0))


def tree_total(sample: dict[str, Any], key: str = "total_private") -> int:
    return int(sample["tree"].get(key, 0))


def cache_row(sample: dict[str, Any], name: str) -> dict[str, Any]:
    return sample.get("caches", {}).get(name, {})


EXACT_NOT_SHARED_NOTE = "exact caches only, leaving out the ones that share pixels with another cache"


def accounted_exact_bytes(sample: dict[str, Any]) -> int:
    return sum(
        int(row.get("bytes", 0)) for row in sample.get("caches", {}).values()
        if row.get("kind") == "exact" and not row.get("shared_with")
    )


# --------------------------------------------------------------------------------------------------------------------------
# Analyses
# --------------------------------------------------------------------------------------------------------------------------


def process_summary(run: Run) -> dict[str, Any]:
    samples = run.samples
    if not samples:
        return {}
    ready = run.mark_time("window_ready") or seconds(samples[0])
    first_phase = min((p["start"] for p in run.phases), default=None)
    settle = next((p for p in run.phases if p["name"] == "settle"), None)
    if settle is not None:
        window = run.window(settle["end"] - 5.0, settle["end"])
    elif first_phase is not None:
        window = run.window(max(ready, first_phase - 5.0), first_phase)
    else:
        window = samples[:5]
    baseline_samples = window or samples[:3]

    def med(fn) -> float:
        return statistics.median(fn(s) for s in baseline_samples)

    peak_sample = max(samples, key=lambda s: own(s, "private"))
    peak_tree = max(samples, key=lambda s: tree_total(s))
    done_t = run.mark_time("workload_done")
    idle_start = run.mark_time("idle:start")
    idle_end = run.mark_time("idle:end")
    gc_t = run.mark_time("after_gc")
    settled_t = run.mark_time("after_gc_settled")
    post_t = idle_start if idle_start is not None else done_t

    def at(t: float | None):
        return run.sample_near(t) if t is not None else None

    post = run.sample_at_or_before(post_t) if post_t is not None else None
    after_idle = at(idle_end)
    after_gc = at(gc_t)
    settled = at(settled_t)
    final = samples[-1]
    summary: dict[str, Any] = {
        "samples": len(samples),
        "duration_s": round(seconds(samples[-1]) - seconds(samples[0]), 1),
        "baseline": {
            "own_private": med(lambda s: own(s, "private")),
            "own_ws": med(lambda s: own(s, "ws")),
            "tree_private": med(lambda s: tree_total(s)),
            "ai_private": med(lambda s: role(s, "ai")),
            "photocraft_private": med(lambda s: role(s, "photocraft")),
            "from_samples": len(baseline_samples),
        },
        "peak": {
            "own_private": own(peak_sample, "private"),
            "own_private_at_s": round(seconds(peak_sample), 1),
            "own_private_phase": run.phase_at(seconds(peak_sample)),
            "own_ws": max(own(s, "ws") for s in samples),
            "os_peak_ws": max(own(s, "peak_ws") for s in samples),
            "tree_private": tree_total(peak_tree),
            "tree_private_at_s": round(seconds(peak_tree), 1),
            "ai_private": max(role(s, "ai") for s in samples),
            "photocraft_private": max(role(s, "photocraft") for s in samples),
        },
    }
    for key, sample in (("post_workload", post), ("after_idle", after_idle), ("after_gc", after_gc), ("after_gc_settled", settled)):
        if sample is not None:
            summary[key] = {
                "own_private": own(sample, "private"),
                "own_ws": own(sample, "ws"),
                "tree_private": tree_total(sample),
                "ai_private": role(sample, "ai"),
                "photocraft_private": role(sample, "photocraft"),
                "accounted_exact_cache_bytes": accounted_exact_bytes(sample),
            }
    reference = settled or after_gc or after_idle or final
    summary["retained"] = {
        "own_private_vs_baseline": own(reference, "private") - summary["baseline"]["own_private"],
        "tree_private_vs_baseline": tree_total(reference) - summary["baseline"]["tree_private"],
        "measured_at": "after_gc_settled" if settled else "after_gc" if after_gc else "after_idle" if after_idle else "last sample",
        "accounted_exact_cache_bytes": accounted_exact_bytes(reference),
    }
    if "post_workload" in summary and after_idle is not None:
        summary["idle_recovery"] = {
            "own_private_released_during_idle": summary["post_workload"]["own_private"] - own(after_idle, "private"),
            "own_private_released_by_gc": own(after_idle, "private") - own(after_gc, "private") if after_gc else None,
        }
    traced = [s for s in samples if "tracemalloc" in s]
    if traced:
        last = traced[-1]
        summary["python_vs_native"] = {
            "tracemalloc_current": last["tracemalloc"]["current"],
            "tracemalloc_peak": max(s["tracemalloc"]["peak"] for s in traced),
            "own_private": own(last, "private"),
            "python_share_of_private": round(last["tracemalloc"]["current"] / max(1, own(last, "private")), 3),
            "note": "tracemalloc sees Python-domain allocations (including numpy); Qt/Pillow/libraw native buffers are outside it.",
        }
    return summary


def phase_table(run: Run) -> list[dict[str, Any]]:
    rows = []
    for phase in run.phases:
        inside = run.window(phase["start"], phase["end"])
        before = run.sample_at_or_before(phase["start"]) or (inside[0] if inside else None)
        after = run.sample_at_or_before(phase["end"]) or (inside[-1] if inside else None)
        if before is None or after is None:
            continue
        jobs: dict[str, int] = defaultdict(int)
        lag = 0.0
        for sample in inside:
            for name, row in sample.get("jobs", {}).items():
                jobs[name] = max(jobs[name], int(row.get("peak_est_bytes_in_flight", 0)))
            lag = max(lag, float(sample.get("ui_lag_ms_max", 0.0)))
        rows.append({
            "phase": phase["name"],
            "seconds": round(phase["end"] - phase["start"], 1),
            "own_private_start": own(before, "private"),
            "own_private_end": own(after, "private"),
            "own_private_delta": own(after, "private") - own(before, "private"),
            "own_private_peak": max((own(s, "private") for s in inside), default=own(after, "private")),
            "tree_private_peak": max((tree_total(s) for s in inside), default=tree_total(after)),
            "ai_private_peak": max((role(s, "ai") for s in inside), default=0),
            "photocraft_private_peak": max((role(s, "photocraft") for s in inside), default=0),
            "decode_est_peak_bytes": dict(jobs),
            "ui_lag_ms_max": round(lag, 1),
        })
    return rows


def cache_table(run: Run) -> list[dict[str, Any]]:
    if not run.samples:
        return []
    last = run.samples[-1]
    names = set()
    for sample in run.samples:
        names.update(sample.get("caches", {}))
    rows = []
    for name in sorted(names):
        final = cache_row(last, name)
        peak_bytes = max(int(cache_row(s, name).get("peak_bytes", 0)) for s in run.samples)
        peak_entries = max(int(cache_row(s, name).get("peak_entries", 0)) for s in run.samples)
        rows.append({
            "cache": name,
            "kind": final.get("kind", ""),
            "shared_with": final.get("shared_with", ""),
            "final_bytes": int(final.get("bytes", 0)),
            "peak_bytes": peak_bytes,
            "final_entries": int(final.get("entries", 0)),
            "peak_entries": peak_entries,
            "hits": int(final.get("hits", 0)),
            "misses": int(final.get("misses", 0)),
            "hit_rate": final.get("hit_rate"),
            "evictions": int(final.get("evictions", 0)),
            "invalidations": int(final.get("invalidations", 0)),
            "unique_bytes": final.get("unique_bytes"),
            "peak_unique_bytes": final.get("peak_unique_bytes"),
        })
    return rows


def job_table(run: Run) -> list[dict[str, Any]]:
    if not run.samples:
        return []
    last = run.samples[-1]
    return [{"jobs": name, **row} for name, row in sorted(last.get("jobs", {}).items())]


def counter_table(run: Run) -> list[dict[str, Any]]:
    if not run.samples:
        return []
    return [{"counter": name, **row} for name, row in sorted(run.samples[-1].get("counters", {}).items())]


def gauge_table(run: Run) -> list[dict[str, Any]]:
    if not run.samples:
        return []
    return [{"gauge": name, **row} for name, row in sorted(run.samples[-1].get("gauges", {}).items())]


def span_table(run: Run) -> list[dict[str, Any]]:
    matrix_bytes = 0
    for sample in run.samples:
        matrix_bytes = max(matrix_bytes, int(cache_row(sample, "semantic.matrix").get("peak_bytes", 0)))
    rows = []
    for span in run.spans:
        transient = int(span.get("private_peak_sampled", 0)) - int(span.get("private_before", 0))
        row = {
            "label": span.get("label", ""),
            "at_s": round(seconds(span), 1),
            "duration_ms": span.get("duration_ms"),
            "private_before": span.get("private_before"),
            "private_after": span.get("private_after"),
            "private_peak_sampled": span.get("private_peak_sampled"),
            "peak_over_before": transient,
            "kept": int(span.get("private_after", 0)) - int(span.get("private_before", 0)),
        }
        if span.get("label") == "semantic.load_matrix" and matrix_bytes:
            row["matrix_bytes_exact"] = matrix_bytes
            row["peak_over_before_in_matrix_sizes"] = round(transient / matrix_bytes, 2)
        rows.append(row)
    return rows


def linear_fit(points: list[tuple[float, float]]) -> tuple[float, float]:
    """(slope, r squared) of y against x."""
    if len(points) < 3:
        return 0.0, 0.0
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    mean_x, mean_y = statistics.fmean(xs), statistics.fmean(ys)
    sxx = sum((x - mean_x) ** 2 for x in xs)
    syy = sum((y - mean_y) ** 2 for y in ys)
    sxy = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    if sxx == 0 or syy == 0:
        return 0.0, 0.0
    return sxy / sxx, (sxy * sxy) / (sxx * syy)


def retention_table(run: Run) -> list[dict[str, Any]]:
    groups: dict[str, list[tuple[int, dict[str, Any]]]] = defaultdict(list)
    for mark in run.marks:
        label = str(mark.get("label", ""))
        for prefix in ("folder_cycle:", "openclose:"):
            if label.startswith(prefix) and label[len(prefix):].isdigit():
                groups[prefix[:-1]].append((int(label[len(prefix):]), mark))
    rows = []
    for name, items in groups.items():
        items.sort(key=lambda item: item[0])
        points = [(float(index), float(own(mark, "private"))) for index, mark in items if "tree" in mark]
        slope, r2 = linear_fit(points)
        cache_points = [(float(index), float(accounted_exact_bytes(mark))) for index, mark in items if "tree" in mark]
        cache_slope, _ = linear_fit(cache_points)
        flagged = len(points) >= 4 and slope > RETENTION_FLAG_BYTES_PER_CYCLE and r2 > RETENTION_FLAG_R2
        rows.append({
            "repeat": name,
            "points": len(points),
            "first_private": int(points[0][1]) if points else 0,
            "last_private": int(points[-1][1]) if points else 0,
            "slope_bytes_per_cycle": round(slope),
            "r2": round(r2, 3),
            "cache_bytes_slope_per_cycle": round(cache_slope),
            "verdict": (
                "GROWING: private bytes rise steadily across repeats (see cache slope to tell caches from the rest)"
                if flagged else "no steady growth detected (heuristic: needs >5 MiB/cycle and r2>0.8)"
            ),
            "series": [(int(x), int(y)) for x, y in points],
        })
    return rows


def latency_correlation(run: Run, nav_file_records: list[dict[str, Any]] | None) -> dict[str, Any]:
    result: dict[str, Any] = {}
    rows = []
    for nav in run.navs:
        stage = nav.get("stages", {})
        value = stage.get("preview", stage.get("refined", stage.get("first_pixel")))
        if value is None:
            continue
        started = nav.get("started_elapsed_ms")
        t = float(started) / 1000.0 if started is not None else seconds(nav)
        sample = run.sample_at_or_before(t) or run.sample_near(t)
        if sample is None:
            continue
        rows.append((float(value), own(sample, "private"), float(sample.get("ui_lag_ms_max", 0.0)), run.phase_at(t)))
    if len(rows) >= 8:
        privates = sorted(r[1] for r in rows)
        cut = privates[len(privates) // 2]
        low = [r[0] for r in rows if r[1] <= cut]
        high = [r[0] for r in rows if r[1] > cut]
        result["navigation_by_memory_half"] = {
            "n": len(rows),
            "split_at_own_private": cut,
            "low_memory": {"n": len(low), "p50": percentile(low, 0.5), "p95": percentile(low, 0.95), "p99": percentile(low, 0.99)},
            "high_memory": {"n": len(high), "p50": percentile(high, 0.5), "p95": percentile(high, 0.95), "p99": percentile(high, 0.99)},
            "note": "stage used: preview, else refined, else first_pixel. A difference here is a correlation, not proof of cause.",
        }
        values = [r[0] for r in rows]
        result["navigation_overall"] = {
            "n": len(values), "p50": percentile(values, 0.5), "p95": percentile(values, 0.95), "p99": percentile(values, 0.99),
            "over_100ms": sum(1 for v in values if v > 100.0), "over_200ms": sum(1 for v in values if v > 200.0),
        }
    elif nav_file_records:
        values = [r["stages"].get("preview", r["stages"].get("refined")) for r in nav_file_records]
        values = [v for v in values if v is not None]
        if values:
            result["navigation_overall"] = {
                "n": len(values), "p50": percentile(values, 0.5), "p95": percentile(values, 0.95), "p99": percentile(values, 0.99),
                "over_100ms": sum(1 for v in values if v > 100.0), "over_200ms": sum(1 for v in values if v > 200.0),
            }
    decode: dict[str, list[float]] = defaultdict(list)
    for event in run.events:
        if event["event"] in ("preview.task", "thumbnail.task") and "duration_ms" in event:
            state = event.get("state") or event.get("source") or ""
            decode[f"{event['event']}:{state}"].append(float(event["duration_ms"]))
    result["decode_ms"] = {
        key: {"n": len(v), "p50": round(percentile(v, 0.5), 1), "p95": round(percentile(v, 0.95), 1), "p99": round(percentile(v, 0.99), 1),
              "max": round(max(v), 1)}
        for key, v in sorted(decode.items()) if len(v) >= 3
    }
    stalls = sorted(
        ((float(s.get("ui_lag_ms_max", 0.0)), s) for s in run.samples if float(s.get("ui_lag_ms_max", 0.0)) > 0),
        key=lambda item: -item[0],
    )[:8]
    result["worst_ui_stalls"] = [
        {"ui_lag_ms": round(lag, 1), "at_s": round(seconds(s), 1), "phase": run.phase_at(seconds(s)), "own_private": own(s, "private")}
        for lag, s in stalls
    ]
    lags = [float(s.get("ui_lag_ms_max", 0.0)) for s in run.samples if "ui_lag_ms_max" in s]
    if lags:
        result["ui_lag_ms"] = {"p50": round(percentile(lags, 0.5), 1), "p95": round(percentile(lags, 0.95), 1), "max": round(max(lags), 1)}
    return result


# --------------------------------------------------------------------------------------------------------------------------
# Charts (inline SVG)
# --------------------------------------------------------------------------------------------------------------------------

PALETTE = ["#2a6fdb", "#d9534f", "#2e9e5b", "#e08e0b", "#8e5bd9", "#16a3b8", "#7a7a7a", "#c2185b"]


def svg_chart(title: str, series: list[tuple[str, list[tuple[float, float]]]], phases: list[dict[str, Any]], unit: str = "MiB",
              scale: float = MIB, width: int = 900, height: int = 280, marks: list[tuple[float, str]] | None = None,
              x_suffix: str = "s", y_from_zero: bool = True) -> str:
    series = [(name, pts) for name, pts in series if pts]
    if not series:
        return ""
    left, right, top, bottom = 58, 12, 26, 34
    xs = [x for _, pts in series for x, _ in pts]
    ys = [y / scale for _, pts in series for _, y in pts]
    x0, x1 = min(xs), max(xs)
    y_low = 0.0 if y_from_zero else min(ys) - 0.05 * (max(ys) - min(ys) or 1.0)
    y1 = max(ys) * 1.08 if y_from_zero else max(ys) + 0.05 * (max(ys) - min(ys) or 1.0)
    y1 = y1 or 1.0
    x1 = x1 if x1 > x0 else x0 + 1
    plot_w, plot_h = width - left - right, height - top - bottom

    def px(x: float) -> float:
        return left + (x - x0) / (x1 - x0) * plot_w

    def py(y: float) -> float:
        return top + plot_h - ((y - y_low) / (y1 - y_low)) * plot_h

    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" role="img" aria-label="{html.escape(title)}" '
             f'style="width:100%;max-width:{width}px;height:auto">',
             f'<text x="{left}" y="16" font-size="13" font-weight="600" fill="currentColor">{html.escape(title)}</text>']
    for index, phase in enumerate(phases):
        a, b = px(max(phase["start"], x0)), px(min(phase["end"], x1))
        if b > a:
            parts.append(f'<rect x="{a:.1f}" y="{top}" width="{b - a:.1f}" height="{plot_h}" fill="{PALETTE[index % len(PALETTE)]}" opacity="0.07"/>')
            parts.append(f'<text x="{a + 2:.1f}" y="{top + 10}" font-size="9" fill="currentColor" opacity="0.7">{html.escape(phase["name"])}</text>')
    for step in range(5):
        value = y_low + (y1 - y_low) * step / 4
        y = py(value)
        parts.append(f'<line x1="{left}" x2="{width - right}" y1="{y:.1f}" y2="{y:.1f}" stroke="currentColor" opacity="0.12"/>')
        parts.append(f'<text x="{left - 6}" y="{y + 3:.1f}" font-size="10" text-anchor="end" fill="currentColor">{value:,.0f}</text>')
    for step in range(6):
        value = x0 + (x1 - x0) * step / 5
        parts.append(f'<text x="{px(value):.1f}" y="{height - 14}" font-size="10" text-anchor="middle" fill="currentColor">{value:,.0f}{x_suffix}</text>')
    parts.append(f'<text x="12" y="{top + plot_h / 2}" font-size="10" fill="currentColor" transform="rotate(-90 12 {top + plot_h / 2})">{unit}</text>')
    legend_x = left + 8
    for index, (name, pts) in enumerate(series):
        colour = PALETTE[index % len(PALETTE)]
        path = " ".join(f"{'M' if i == 0 else 'L'}{px(x):.1f},{py(y / scale):.1f}" for i, (x, y) in enumerate(pts))
        parts.append(f'<path d="{path}" fill="none" stroke="{colour}" stroke-width="1.6"/>')
        lx = legend_x
        legend_x += 24 + int(len(name) * 5.6)
        parts.append(f'<rect x="{lx}" y="{height - 4 - 8}" width="10" height="3" fill="{colour}"/>')
        parts.append(f'<text x="{lx + 14}" y="{height - 4}" font-size="10" fill="currentColor">{html.escape(name)}</text>')
    for t, label in marks or []:
        if x0 <= t <= x1:
            parts.append(f'<line x1="{px(t):.1f}" x2="{px(t):.1f}" y1="{top}" y2="{top + plot_h}" stroke="currentColor" opacity="0.35" stroke-dasharray="3 3"/>')
            parts.append(f'<text x="{px(t) + 2:.1f}" y="{top + plot_h - 3}" font-size="9" fill="currentColor" opacity="0.8">{html.escape(label)}</text>')
    parts.append("</svg>")
    return "".join(parts)


def charts(run: Run) -> list[str]:
    samples = run.samples
    t = [seconds(s) for s in samples]
    phases = run.phases
    key_marks = [(run.mark_time(m), m) for m in ("idle:start", "after_gc", "workload_done") if run.mark_time(m) is not None]
    out = []
    out.append(svg_chart("Process memory (OS figures)", [
        ("this process: private", [(x, own(s, "private")) for x, s in zip(t, samples)]),
        ("this process: working set", [(x, own(s, "ws")) for x, s in zip(t, samples)]),
        ("process tree: private", [(x, tree_total(s)) for x, s in zip(t, samples)]),
        ("AI workers: private", [(x, role(s, "ai")) for x, s in zip(t, samples)]),
        ("PhotoCraft: private", [(x, role(s, "photocraft")) for x, s in zip(t, samples)]),
    ], phases, marks=key_marks))
    names = []
    for s in samples:
        for name in s.get("caches", {}):
            if name not in names:
                names.append(name)
    biggest = sorted(names, key=lambda n: -max(int(cache_row(s, n).get("bytes", 0)) for s in samples))[:6]
    out.append(svg_chart("Cache residency (application-reported; exact unless noted in the table)", [
        (name, [(x, int(cache_row(s, name).get("bytes", 0))) for x, s in zip(t, samples)]) for name in biggest
    ], phases))
    out.append(svg_chart("Accounted vs. unaccounted memory", [
        ("this process: private (OS)", [(x, own(s, "private")) for x, s in zip(t, samples)]),
        ("exact caches (app)", [(x, accounted_exact_bytes(s)) for x, s in zip(t, samples)]),
        ("gap: private - caches", [(x, max(0, own(s, "private") - accounted_exact_bytes(s))) for x, s in zip(t, samples)]),
    ], phases))
    job_names = sorted({name for s in samples for name in s.get("jobs", {})})
    out.append(svg_chart("Estimated decode buffers in flight (estimates, not measurements)", [
        (name, [(x, int(s.get("jobs", {}).get(name, {}).get("est_bytes_in_flight", 0))) for x, s in zip(t, samples)]) for name in job_names
    ], phases))
    out.append(svg_chart("Longest UI-thread stall per sample", [
        ("ui lag", [(x, float(s.get("ui_lag_ms_max", 0.0))) for x, s in zip(t, samples) if "ui_lag_ms_max" in s]),
    ], phases, unit="ms", scale=1.0))
    for row in retention_table(run):
        out.append(svg_chart(f"Private bytes at each {row['repeat']} repeat (slope {row['slope_bytes_per_cycle'] / MIB:+.2f} MiB/cycle)", [
            ("this process: private", [(float(x), float(y)) for x, y in row["series"]]),
        ], [], width=600, height=220, x_suffix="", y_from_zero=False))
    return [c for c in out if c]


# --------------------------------------------------------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------------------------------------------------------


def md_table(headers: list[str], rows: list[list[Any]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    for row in rows:
        lines.append("| " + " | ".join(str(cell) for cell in row) + " |")
    return "\n".join(lines)


def build_markdown(run: Run, data: dict[str, Any]) -> str:
    out: list[str] = []
    start = run.start
    out.append("# Image Triage memory report\n")
    out.append(f"Platform `{start.get('platform', '?')}`, Python {start.get('python', '?')}, sample interval {start.get('interval_s', '?')} s, "
               f"{data['process'].get('samples', 0)} samples over {data['process'].get('duration_s', 0)} s"
               f"{', tracemalloc on' if start.get('tracemalloc') else ''}{', GPU sampling on' if start.get('gpu') else ''}.\n")
    out.append("Process figures are the operating system's. Cache figures are what the application reports (`exact` or `estimated`); "
               "they are never added to process figures.\n")
    if run.errors:
        out.append(f"**{len(run.errors)} sampler errors** (first: `{run.errors[0].get('error')}`).\n")

    process = data["process"]
    if process:
        out.append("## 1. Process memory (MiB)\n")
        base, peak = process["baseline"], process["peak"]
        rows = [["baseline (settled, before the workload)", mib(base["own_private"]), mib(base["own_ws"]), mib(base["tree_private"]),
                 mib(base["ai_private"]), mib(base["photocraft_private"])]]
        rows.append(["peak", mib(peak["own_private"]), mib(peak["own_ws"]), mib(peak["tree_private"]), mib(peak["ai_private"]),
                     mib(peak["photocraft_private"])])
        for key, label in (("post_workload", "after the workload, before idle"), ("after_idle", "after idle"), ("after_gc", "after forced gc.collect()"),
                           ("after_gc_settled", "after gc, settled")):
            if key in process:
                row = process[key]
                rows.append([label, mib(row["own_private"]), mib(row["own_ws"]), mib(row["tree_private"]), mib(row["ai_private"]),
                             mib(row["photocraft_private"])])
        out.append(md_table(["", "app private", "app working set", "tree private", "AI workers", "PhotoCraft"], rows))
        out.append(f"\nOS peak working set of the app process: **{mib(peak['os_peak_ws'])} MiB**. App private peak {mib(peak['own_private'])} MiB "
                   f"at {peak['own_private_at_s']} s (phase `{peak['own_private_phase']}`).\n")
        retained = process["retained"]
        out.append(f"**Retained after returning to idle** (measured at *{retained['measured_at']}*): app private "
                   f"{retained['own_private_vs_baseline'] / MIB:+,.1f} MiB vs. baseline; whole process tree {retained['tree_private_vs_baseline'] / MIB:+,.1f} MiB. "
                   f"Exact application caches still hold {mib(retained['accounted_exact_cache_bytes'])} MiB of that "
                   f"({EXACT_NOT_SHARED_NOTE}).\n")
        if "idle_recovery" in process:
            recovery = process["idle_recovery"]
            gc_part = recovery["own_private_released_by_gc"]
            out.append(f"Released while idle: {recovery['own_private_released_during_idle'] / MIB:+,.1f} MiB"
                       f"{'' if gc_part is None else f'; by the forced collection: {gc_part / MIB:+,.1f} MiB'}. "
                       "Memory that falls only after `gc.collect()` was still referenced by Python cycles; memory that never falls is either "
                       "intentionally cached or freed-but-not-returned by the allocator (this report cannot tell those two apart without a heap profile).\n")
        if "python_vs_native" in process:
            split = process["python_vs_native"]
            out.append(f"Python-level allocations (tracemalloc): {mib(split['tracemalloc_current'])} MiB = {split['python_share_of_private'] * 100:.0f}% of app private bytes. {split['note']}\n")
        else:
            out.append("_Python-vs-native split not available: rerun with `IMAGE_TRIAGE_MEMTRACE_TRACEMALLOC=1` (slower) to measure it._\n")

    out.append("## 2. Per-phase memory (MiB)\n")
    rows = []
    for row in data["phases"]:
        decode = ", ".join(f"{name.split('.')[0]} {mib(value)}" for name, value in row["decode_est_peak_bytes"].items() if value) or "-"
        rows.append([row["phase"], row["seconds"], mib(row["own_private_start"]), mib(row["own_private_end"]), f"{row['own_private_delta'] / MIB:+,.1f}",
                     mib(row["own_private_peak"]), mib(row["tree_private_peak"]), mib(row["ai_private_peak"]), mib(row["photocraft_private_peak"]),
                     decode, row["ui_lag_ms_max"]])
    out.append(md_table(["phase", "s", "start", "end", "delta", "app peak", "tree peak", "AI peak", "PhotoCraft peak", "est. decode peak", "max UI lag ms"], rows)
               if rows else "_No phase marks in this log (run scripts/memtrace_workload.py)._")

    out.append("\n## 3. Caches (application-reported)\n")
    rows = [[row["cache"], row["kind"] + (f" (shares pixels with {row['shared_with']})" if row["shared_with"] else ""), mib(row["final_bytes"]),
             mib(row["peak_bytes"]), row["final_entries"], row["peak_entries"], row["hits"], row["misses"],
             "-" if row["hit_rate"] is None else f"{row['hit_rate'] * 100:.0f}%", row["evictions"], row["invalidations"],
             "-" if row["unique_bytes"] is None else f"{mib(row['unique_bytes'])} (peak {mib(row['peak_unique_bytes'])})"] for row in data["caches"]]
    out.append(md_table(["cache", "kind", "final MiB", "peak MiB", "entries", "peak entries", "hits", "misses", "hit rate", "evictions", "invalidations",
                         "distinct pixel buffers MiB"], rows))
    charged_twice = [r for r in data["caches"] if r["unique_bytes"] is not None and r["final_bytes"] > 1.2 * max(1, r["unique_bytes"])]
    for r in charged_twice:
        out.append(f"\n`{r['cache']}` charges {mib(r['final_bytes'])} MiB against its limit for {mib(r['unique_bytes'])} MiB of distinct pixel data: "
                   "the same image is stored under more than one key and counted for each, so the cache evicts earlier than its real footprint requires.")
    if process:
        last_exact = process["retained"]["accounted_exact_cache_bytes"]
        out.append(f"\nAt the end, exact non-overlapping caches account for {mib(last_exact)} MiB of {mib(own(run.samples[-1], 'private'))} MiB private bytes. "
                   "The rest is Python and Qt objects, loaded libraries and models, allocator slack and anything not instrumented; this is the number that "
                   "decides how much a different language could save.\n")

    out.append("\n## 4. Decode jobs (estimates unless noted)\n")
    rows = [[row["jobs"], row["started"], row["finished"], row["peak_queued"], row["peak_running"], mib(row["peak_est_bytes_in_flight"]),
             mib(row["max_result_bytes"])] for row in data["jobs"]]
    out.append(md_table(["jobs", "started", "finished", "peak queued", "peak running", "peak est. MiB in flight", "largest result MiB (exact)"], rows) if rows else "_none_")
    if data["counters"]:
        out.append("\n" + md_table(["counter", "count", "total est. MiB", "max est. MiB"],
                                   [[r["counter"], r["count"], mib(r["total_est_bytes"]), mib(r["max_est_bytes"])] for r in data["counters"]]))
    if data["gauges"]:
        out.append("\n" + md_table(["queue / gauge", "last", "peak"], [[r["gauge"], r["value"], r["peak"]] for r in data["gauges"]]))

    out.append("\n## 5. Short transients (sampled every 10 ms)\n")
    rows = [[r["label"], r["at_s"], r["duration_ms"], mib(r["private_before"]), mib(r["private_after"]), mib(r["private_peak_sampled"]),
             f"{r['peak_over_before'] / MIB:+,.1f}", f"{r['kept'] / MIB:+,.1f}",
             (f"{r['peak_over_before_in_matrix_sizes']}x of {mib(r['matrix_bytes_exact'])} MiB matrix" if "matrix_bytes_exact" in r else "")] for r in data["spans"]]
    out.append(md_table(["span", "at s", "ms", "before", "after", "sampled peak", "peak over before", "kept", "note"], rows) if rows else "_none recorded_")

    out.append("\n## 6. Repeat-stability (retention)\n")
    rows = [[r["repeat"], r["points"], mib(r["first_private"]), mib(r["last_private"]), f"{r['slope_bytes_per_cycle'] / MIB:+,.2f}", r["r2"],
             f"{r['cache_bytes_slope_per_cycle'] / MIB:+,.2f}", r["verdict"]] for r in data["retention"]]
    out.append(md_table(["repeat", "n", "first MiB", "last MiB", "slope MiB/cycle", "r2", "exact-cache slope MiB/cycle", "verdict"], rows)
               if rows else "_No repeat marks (folders / openclose phases)._")

    out.append("\n## 7. Latency and stalls\n")
    latency = data["latency"]
    if "navigation_overall" in latency:
        o = latency["navigation_overall"]
        out.append(f"Navigation (to first usable picture): n={o['n']}, p50 {o['p50']:.0f} ms, p95 {o['p95']:.0f} ms, p99 {o['p99']:.0f} ms; "
                   f"{o['over_100ms']} over 100 ms, {o['over_200ms']} over 200 ms.\n")
    if "navigation_by_memory_half" in latency:
        h = latency["navigation_by_memory_half"]
        out.append(md_table(["", "n", "p50 ms", "p95 ms", "p99 ms"], [
            [f"app private <= {mib(h['split_at_own_private'])} MiB", h["low_memory"]["n"], f"{h['low_memory']['p50']:.0f}", f"{h['low_memory']['p95']:.0f}", f"{h['low_memory']['p99']:.0f}"],
            [f"app private > {mib(h['split_at_own_private'])} MiB", h["high_memory"]["n"], f"{h['high_memory']['p50']:.0f}", f"{h['high_memory']['p95']:.0f}", f"{h['high_memory']['p99']:.0f}"],
        ]) + f"\n\n_{h['note']}_\n")
    if "ui_lag_ms" in latency:
        l = latency["ui_lag_ms"]
        out.append(f"GUI-thread lateness of a 50 ms heartbeat, per-sample maxima: p50 {l['p50']} ms, p95 {l['p95']} ms, max {l['max']} ms.\n")
    if latency.get("worst_ui_stalls"):
        out.append(md_table(["UI lag ms", "at s", "phase", "app private MiB"], [[w["ui_lag_ms"], w["at_s"], w["phase"], mib(w["own_private"])] for w in latency["worst_ui_stalls"]]))
    if latency.get("decode_ms"):
        out.append("\n\n" + md_table(["decode event", "n", "p50 ms", "p95 ms", "p99 ms", "max ms"], [[k, v["n"], v["p50"], v["p95"], v["p99"], v["max"]] for k, v in latency["decode_ms"].items()]))

    out.append("\n\n## 8. What this report cannot say\n")
    out.append("- Which allocator keeps freed memory (needs a heap profile: VMMap/UMDH on Windows, heaptrack on Linux).\n"
               "- GPU memory per process on Windows WDDM (`nvidia-smi` usually reports only the total there); use Task Manager's per-process GPU columns or `typeperf` counters.\n"
               "- Anything from a single run: repeat the workload at least three times and compare the `--json` summaries before drawing a conclusion.\n")
    return "\n".join(out) + "\n"


def build_html(markdown: str, chart_svgs: list[str]) -> str:
    body = []
    for line_block in markdown.split("\n\n"):
        block = line_block.strip("\n")
        if not block:
            continue
        if block.startswith("|"):
            rows = [r for r in block.splitlines() if r.startswith("|")]
            cells = [[c.strip() for c in r.strip("|").split("|")] for r in rows]
            head, data = cells[0], [c for c in cells[2:]]
            table = "<table><thead><tr>" + "".join(f"<th>{html.escape(c)}</th>" for c in head) + "</tr></thead><tbody>"
            table += "".join("<tr>" + "".join(f"<td>{html.escape(c)}</td>" for c in row) + "</tr>" for row in data) + "</tbody></table>"
            body.append(table)
        else:
            for line in block.splitlines():
                if line.startswith("# "):
                    body.append(f"<h1>{html.escape(line[2:])}</h1>")
                elif line.startswith("## "):
                    body.append(f"<h2>{html.escape(line[3:])}</h2>")
                elif line.strip():
                    body.append(f"<p>{html.escape(line).replace('**', '')}</p>")
    figures = "".join(f"<figure>{svg}</figure>" for svg in chart_svgs)
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Image Triage memory report</title>
<style>:root{{color-scheme:light dark;--bg:#fff;--fg:#1c2330;--line:#d5dae3}}
@media (prefers-color-scheme: dark){{:root{{--bg:#12161d;--fg:#e4e8ef;--line:#2c3442}}}}
body{{background:var(--bg);color:var(--fg);font:14px/1.5 system-ui,Segoe UI,sans-serif;margin:0 auto;padding:16px;max-width:1000px}}
table{{border-collapse:collapse;width:100%;margin:8px 0 16px;font-size:12px;display:block;overflow-x:auto}}
th,td{{border:1px solid var(--line);padding:3px 7px;text-align:right;white-space:nowrap}}th:first-child,td:first-child{{text-align:left}}
figure{{margin:12px 0}}svg{{color:var(--fg)}}h1,h2{{font-weight:600}}</style></head>
<body>{''.join(body[:1])}{figures}{''.join(body[1:])}</body></html>"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("log", type=Path, help="a performance_*.jsonl written with IMAGE_TRIAGE_MEMTRACE=1")
    parser.add_argument("--nav", type=Path, help="the nav_timing JSONL (IMAGE_TRIAGE_NAV_TIMING) for percentile context")
    parser.add_argument("--out", type=Path, help="output directory (default: beside the log)")
    parser.add_argument("--quiet", action="store_true", help="write files only, do not print the Markdown")
    args = parser.parse_args(argv)

    events = load_events(args.log)
    run = Run(events)
    if not run.samples:
        print(f"No mem.sample events in {args.log}. Was IMAGE_TRIAGE_MEMTRACE=1 set when the app started?", file=sys.stderr)
        return 2
    nav_records = None
    if args.nav and args.nav.exists():
        nav_records = [json.loads(line) for line in args.nav.read_text(encoding="utf-8").splitlines() if line.strip()]
    data = {
        "log": str(args.log),
        "process": process_summary(run),
        "phases": phase_table(run),
        "caches": cache_table(run),
        "jobs": job_table(run),
        "counters": counter_table(run),
        "gauges": gauge_table(run),
        "spans": span_table(run),
        "retention": retention_table(run),
        "latency": latency_correlation(run, nav_records),
    }
    markdown = build_markdown(run, data)
    out_dir = args.out or args.log.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = out_dir / f"memtrace_report_{args.log.stem.replace('performance_', '')}"
    stem.with_suffix(".md").write_text(markdown, encoding="utf-8")
    stem.with_suffix(".html").write_text(build_html(markdown, charts(run)), encoding="utf-8")
    for row in data["retention"]:
        row.pop("series", None)
    stem.with_suffix(".json").write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    if not args.quiet:
        print(markdown)
    print(f"report written: {stem.with_suffix('.md')}\n                {stem.with_suffix('.html')}\n                {stem.with_suffix('.json')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
