"""scripts/memtrace_report.py turns a memtrace log into numbers a person can act on."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

MIB = 1024 * 1024
SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "memtrace_report.py"


def load_report_module():
    spec = importlib.util.spec_from_file_location("memtrace_report", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sample(t, private, *, ai=0, cache_bytes=0, lag=0.0, event="mem.sample", **extra):
    return {
        "event": event, "elapsed_ms": t * 1000.0,
        "tree": {
            "own": {"private": private * MIB, "ws": private * MIB, "peak_ws": private * MIB},
            "roles": {"ai": {"n": 1, "private": ai * MIB, "ws": ai * MIB, "peak_ws": ai * MIB}} if ai else {},
            "total_private": (private + ai) * MIB, "total_ws": (private + ai) * MIB,
        },
        "caches": {
            "thumbnail.memory_cache": {"kind": "exact", "bytes": cache_bytes * MIB, "entries": 5, "peak_bytes": cache_bytes * MIB,
                                       "peak_entries": 5, "hits": 8, "misses": 2, "evictions": 1, "invalidations": 0, "puts": 3, "hit_rate": 0.8},
            "preview.cache": {"kind": "exact", "bytes": 40 * MIB, "entries": 4, "peak_bytes": 40 * MIB, "peak_entries": 4, "hits": 0, "misses": 0,
                              "evictions": 0, "invalidations": 0, "puts": 4, "unique_bytes": 20 * MIB, "peak_unique_bytes": 20 * MIB},
        },
        "jobs": {"preview.decode": {"queued": 0, "running": 1, "est_bytes_in_flight": 8 * MIB, "peak_queued": 3, "peak_running": 2,
                                    "peak_est_bytes_in_flight": 16 * MIB, "started": 5, "finished": 5, "max_result_bytes": 4 * MIB}},
        "counters": {}, "gauges": {}, "ui_lag_ms_max": lag, **extra,
    }


def mark(t, label, private=100):
    record = sample(t, private, event="mem.mark")
    record["label"] = label
    return record


def synthetic_log():
    events = [{"event": "mem.start", "elapsed_ms": 0.0, "platform": "win32", "python": "3.13", "interval_s": 1.0}]
    t = 0
    for i in range(10):
        events.append(sample(t, 100 + i * 0.2, cache_bytes=5)); t += 1
    events.append(mark(t, "window_ready", 102))
    events.append(mark(t, "phase:settle:start", 102))
    for i in range(5):
        events.append(sample(t, 102, cache_bytes=5)); t += 1
    events.append(mark(t, "phase:settle:end", 102))
    events.append(mark(t, "phase:folders:start", 102))
    for cycle in range(1, 9):  # steady growth of 12 MiB per folder change
        for _ in range(2):
            events.append(sample(t, 102 + cycle * 12, ai=300, cache_bytes=40 + cycle, lag=120.0 if cycle == 4 else 5.0)); t += 1
        events.append(mark(t, f"folder_cycle:{cycle}", 102 + cycle * 12))
    events.append(mark(t, "phase:folders:end", 198))
    events.append(mark(t, "idle:start", 198))
    for _ in range(5):
        events.append(sample(t, 198, ai=300, cache_bytes=48)); t += 1
    events.append(mark(t, "idle:end", 198))
    events.append(mark(t, "after_gc", 190))
    events.append(sample(t + 2, 190, ai=300, cache_bytes=48))
    events.append(mark(t + 2, "after_gc_settled", 190))
    events.append(sample(t + 3, 190, ai=300, cache_bytes=48, event="mem.final"))
    events.append({"event": "mem.span", "elapsed_ms": 5000.0, "label": "semantic.load_matrix", "duration_ms": 900.0,
                   "private_before": 100 * MIB, "private_after": 300 * MIB, "private_peak_sampled": 720 * MIB})
    events.append({"event": "preview.task", "elapsed_ms": 6000.0, "state": "ready", "duration_ms": 30.0})
    return events


def test_report_summarises_baseline_peak_retention_and_growth(tmp_path):
    report = load_report_module()
    log = tmp_path / "performance_x_1.jsonl"
    log.write_text("\n".join(json.dumps(e) for e in synthetic_log()) + "\n{broken", encoding="utf-8")

    assert report.main([str(log), "--quiet", "--out", str(tmp_path)]) == 0

    data = json.loads(next(tmp_path.glob("memtrace_report_*.json")).read_text())
    process = data["process"]
    assert round(process["baseline"]["own_private"] / MIB) == 102
    assert round(process["peak"]["own_private"] / MIB) == 198
    assert process["peak"]["own_private_phase"] in ("folders", "-")
    assert round(process["retained"]["own_private_vs_baseline"] / MIB) == 88  # 190 after gc - 102 baseline
    assert process["retained"]["measured_at"] == "after_gc_settled"
    assert round(process["idle_recovery"]["own_private_released_during_idle"] / MIB) == 0

    folders = next(row for row in data["retention"] if row["repeat"] == "folder_cycle")
    assert round(folders["slope_bytes_per_cycle"] / MIB) == 12 and folders["r2"] > 0.99
    assert folders["verdict"].startswith("GROWING")

    matrix = next(row for row in data["spans"] if row["label"] == "semantic.load_matrix")
    assert round(matrix["peak_over_before"] / MIB) == 620

    preview = next(row for row in data["caches"] if row["cache"] == "preview.cache")
    assert preview["unique_bytes"] == 20 * MIB and preview["final_bytes"] == 40 * MIB
    assert data["latency"]["worst_ui_stalls"][0]["ui_lag_ms"] == 120.0

    markdown = next(tmp_path.glob("memtrace_report_*.md")).read_text()
    assert "Retained after returning to idle" in markdown and "GROWING" in markdown
    assert "charges 40.0 MiB against its limit for 20.0 MiB" in markdown
    html = next(tmp_path.glob("memtrace_report_*.html")).read_text()
    assert html.count("<svg") >= 4 and "<table>" in html


def test_report_refuses_a_log_without_samples(tmp_path, capsys):
    report = load_report_module()
    log = tmp_path / "performance_empty.jsonl"
    log.write_text(json.dumps({"event": "window.update_action_states"}) + "\n")
    assert report.main([str(log), "--quiet"]) == 2
    assert "IMAGE_TRIAGE_MEMTRACE=1" in capsys.readouterr().err
