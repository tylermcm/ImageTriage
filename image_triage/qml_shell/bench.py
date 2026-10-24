"""Scroll benchmark for the QML grid: frame intervals, thumbnail latency and memory while a large folder scrolls.

    python -m image_triage.qml_shell.bench --folder D:\\Photos\\huge --phases slow,fast,jumps

Opens the real window, fills the model straight from ``os.scandir`` (so the scanner's time is not part of what is measured),
then drives the grid's ``contentY`` the way a wheel, a fling and a scrollbar jump would, and records every ``frameSwapped``.
Memory and decode events go to the performance log through memtrace (``IMAGE_TRIAGE_MEMTRACE`` is set here), and
``scripts/memtrace_report.py`` turns that into the usual report. Run it on a real GPU for frame numbers that mean anything:
under software rendering (CI, containers) the frame intervals measure the CPU rasteriser, and only the memory, request and
decode figures carry over.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from pathlib import Path

os.environ.setdefault("IMAGE_TRIAGE_MEMTRACE", "1")

PHASES = ("settle", "slow", "fast", "jumps", "idle")


def parse(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--folder", required=True)
    parser.add_argument("--phases", default=",".join(PHASES))
    parser.add_argument("--limit", type=int, default=0, help="use only the first N files of the folder")
    parser.add_argument("--slow-px-s", type=float, default=1500, help="slow scroll speed (default 1500 px/s)")
    parser.add_argument("--fast-px-s", type=float, default=18000, help="fast scroll speed (default 18000 px/s)")
    parser.add_argument("--seconds", type=float, default=10.0, help="duration of each scroll phase (default 10)")
    parser.add_argument("--jumps", type=int, default=20)
    parser.add_argument("--idle-seconds", type=float, default=15.0)
    parser.add_argument("--size", default="1440x900")
    parser.add_argument("--dens", default="m", choices=("s", "m", "l"))
    parser.add_argument("--out", help="write the summary JSON here")
    return parser.parse_args(argv)


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[max(0, min(len(ordered) - 1, int(round(fraction * (len(ordered) - 1)))))]


def main(argv=None) -> int:
    args = parse(argv)
    from PySide6.QtCore import QCoreApplication, QObject, QTimer, QUrl
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtQml import QQmlApplicationEngine

    from .. import memtrace
    from ..formats import IMAGE_SUFFIXES, RAW_SUFFIXES
    from ..perf import perf_logger
    from .app import QML_DIR
    from .photos import PhotoModel, _sample
    from .theme import Theme
    from .thumbs import ThumbService

    os.environ.setdefault("QT_QUICK_CONTROLS_STYLE", "Basic")
    QCoreApplication.setOrganizationName("Image Triage")
    QCoreApplication.setApplicationName("Image Triage")
    app = QGuiApplication(sys.argv[:1])
    memtrace.start(app)

    started = time.perf_counter()
    rows = []
    with os.scandir(args.folder) as entries:
        for entry in entries:
            suffix = Path(entry.name).suffix.lower()
            if suffix in IMAGE_SUFFIXES and entry.is_file():
                decision, rating, ai = _sample(entry.name)
                rows.append({"path": entry.path, "name": entry.name, "ext": suffix.lstrip(".").upper(), "raw": suffix in RAW_SUFFIXES,
                             "decision": decision, "rating": rating, "ai": ai})
    rows.sort(key=lambda r: r["name"].casefold())
    if args.limit:
        rows = rows[: args.limit]
    scan_s = time.perf_counter() - started

    theme = Theme(parent=app)
    photos = PhotoModel(parent=app)
    thumbs = ThumbService(parent=app)
    engine = QQmlApplicationEngine()
    context = engine.rootContext()
    context.setContextProperty("theme", theme)
    context.setContextProperty("photos", photos)
    context.setContextProperty("thumbs", thumbs)
    context.setContextProperty("startup", f"dens={args.dens};insp=false;tab=")
    engine.addImportPath(str(QML_DIR))
    engine.load(QUrl.fromLocalFile(str(QML_DIR / "Main.qml")))
    window = engine.rootObjects()[0]
    width, height = (int(p) for p in args.size.lower().split("x"))
    window.setWidth(width)
    window.setHeight(height)

    frames: list[float] = []
    window.frameSwapped.connect(lambda: frames.append(time.perf_counter()))

    marks = memtrace.mark
    t0 = time.perf_counter()
    photos.setRows(rows, args.folder)
    model_s = time.perf_counter() - t0
    marks("bench:model_loaded", rows=len(rows), scan_s=round(scan_s, 3), set_rows_s=round(model_s, 3))

    grid = window.findChild(QObject, "photoGrid")
    results: dict[str, dict] = {"rows": len(rows), "scan_s": round(scan_s, 3), "set_rows_s": round(model_s, 3), "phases": {}}

    state = {"phase": None, "t_start": 0.0, "speed": 0.0, "last": 0.0, "pos": 0.0}
    timer = QTimer()
    timer.setInterval(4)

    def content_height() -> float:
        return float(grid.property("contentHeight"))

    def max_y() -> float:
        return max(0.0, content_height() - float(grid.property("height")) + float(grid.property("bottomMargin")))

    def tick() -> None:
        now = time.perf_counter()
        dt = now - state["last"]
        state["last"] = now
        state["pos"] += state["speed"] * dt
        top = max_y()
        if state["pos"] > top:
            state["pos"] = 0.0 if state["wrap"] else top
        grid.setProperty("contentY", state["pos"])

    timer.timeout.connect(tick)

    def run_phase(name: str, seconds: float, speed: float = 0.0, wrap: bool = True) -> None:
        print(f"[bench] {name} ...", file=sys.stderr, flush=True)
        marks(f"phase:{name}:start")
        first_frame = len(frames)
        state.update(phase=name, speed=speed, pos=float(grid.property("contentY")), last=time.perf_counter(), wrap=wrap)
        if speed:
            timer.start()
        end = time.perf_counter() + seconds
        next_note = time.perf_counter() + 2.0
        while time.perf_counter() < end:
            app.processEvents()
            time.sleep(0.001)
            if time.perf_counter() > next_note:
                next_note += 2.0
                print(f"[bench]   y={state['pos']:.0f} frames={len(frames)}", file=sys.stderr, flush=True)
        timer.stop()
        marks(f"phase:{name}:end")
        stamps = frames[first_frame:]
        intervals = [(b - a) * 1000.0 for a, b in zip(stamps, stamps[1:])]
        results["phases"][name] = {
            "seconds": seconds, "frames": len(stamps), "fps": round(len(stamps) / seconds, 1),
            "frame_ms": {"p50": round(percentile(intervals, 0.5), 1), "p95": round(percentile(intervals, 0.95), 1),
                         "p99": round(percentile(intervals, 0.99), 1), "max": round(max(intervals), 1) if intervals else 0.0},
            "over_33ms": sum(1 for v in intervals if v > 33.4), "over_100ms": sum(1 for v in intervals if v > 100.0),
        }

    wanted = [p.strip() for p in args.phases.split(",") if p.strip()]
    rng = random.Random(3)
    for name in wanted:
        if name == "settle":
            run_phase("settle", 6.0)
        elif name == "slow":
            run_phase("slow", args.seconds, args.slow_px_s)
        elif name == "fast":
            run_phase("fast", args.seconds, args.fast_px_s)
        elif name == "jumps":
            marks("phase:jumps:start")
            first_frame = len(frames)
            for _ in range(args.jumps):
                grid.setProperty("contentY", rng.random() * max_y())
                end = time.perf_counter() + 0.5
                while time.perf_counter() < end:
                    app.processEvents()
                    time.sleep(0.001)
            marks("phase:jumps:end")
            stamps = frames[first_frame:]
            intervals = [(b - a) * 1000.0 for a, b in zip(stamps, stamps[1:])]
            results["phases"]["jumps"] = {"jumps": args.jumps, "frames": len(stamps),
                                          "frame_ms": {"p50": round(percentile(intervals, 0.5), 1), "p95": round(percentile(intervals, 0.95), 1),
                                                       "p99": round(percentile(intervals, 0.99), 1), "max": round(max(intervals), 1) if intervals else 0.0}}
        elif name == "idle":
            run_phase("idle", args.idle_seconds)
    marks("workload_done")

    results["thumbnail_cache"] = {"dir": str(thumbs.disk.root)}
    own = memtrace.process_memory()
    if own:
        results["process"] = {"private_mib": round(own.private_bytes / 2**20, 1), "peak_ws_mib": round(own.peak_working_set / 2**20, 1)}
    log = perf_logger().path
    results["perf_log"] = str(log) if log else ""
    memtrace.stop()
    text = json.dumps(results, indent=2)
    print(text)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
