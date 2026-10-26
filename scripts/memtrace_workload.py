"""Drive the real Image Triage window through a repeatable culling workload while memtrace records memory.

    python scripts/memtrace_workload.py --folder D:\\Photos\\Shoot1 --folder D:\\Photos\\Shoot2

Runs the genuine ``MainWindow`` (the same start-up as ``image_triage.main``: splash skipped, preview built and editor started
in idle time unless ``--no-editor``) and walks it through labelled phases. Each phase is bracketed by ``mem.mark`` events in the
performance log (``phase:<name>:start`` / ``phase:<name>:end``); ``scripts/memtrace_report.py`` turns the log into the report.

Phases, in order (select with ``--phases``): ``settle`` ``open`` ``scroll`` ``browse`` ``openclose`` ``folders`` ``semantic``
``idle``. With no ``--folder`` a synthetic JPEG corpus is generated (two folders) so the script is runnable anywhere; real
folders with mixed JPEG and NEF are what matter for conclusions.

Nothing here changes application behaviour: it calls the same entry points the UI does (``_scan.load_folder``,
``_preview_ctl.open_preview``, the popout's navigation signal) and, at the end, forces ``gc.collect()`` once to separate memory
Python still references from memory it has freed but the allocator has not returned (the "after_gc" mark).
"""
from __future__ import annotations

import argparse
import gc
import os
import random
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Must be set before image_triage is imported: ENABLED is read once, at import.
os.environ.setdefault("IMAGE_TRIAGE_MEMTRACE", "1")

ALL_PHASES = ("settle", "open", "scroll", "browse", "openclose", "folders", "semantic", "idle")


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--folder", action="append", default=[], help="a photo folder; repeat for several (>=2 for the folder-change phase)")
    parser.add_argument("--phases", default=",".join(ALL_PHASES), help=f"comma-separated subset of: {', '.join(ALL_PHASES)}")
    parser.add_argument("--settle-seconds", type=float, default=10.0, help="baseline wait after the window is up (default 10)")
    parser.add_argument("--browse-count", type=int, default=300, help="photos stepped through in the popout (default 300)")
    parser.add_argument("--browse-hz", type=float, default=12.0, help="steps per second in the popout (default 12)")
    parser.add_argument("--openclose-count", type=int, default=30, help="popout open/close cycles (default 30)")
    parser.add_argument("--folder-cycles", type=int, default=10, help="folder changes in the folders phase (default 10)")
    parser.add_argument("--idle-seconds", type=float, default=90.0, help="idle time at the end (default 90)")
    parser.add_argument("--semantic-timeout", type=float, default=900.0, help="seconds to wait for semantic indexing (default 900)")
    parser.add_argument("--no-editor", action="store_true", help="do not start the PhotoCraft editor in idle time")
    parser.add_argument("--offscreen", action="store_true", help="QT_QPA_PLATFORM=offscreen (no display; for CI and Linux containers)")
    parser.add_argument("--synthetic-count", type=int, default=240, help="photos per synthetic folder (default 240)")
    parser.add_argument("--synthetic-size", default="3000x2000", help="synthetic photo size (default 3000x2000)")
    parser.add_argument("--no-report", action="store_true", help="do not run memtrace_report.py at the end")
    parser.add_argument("--keep-synthetic", action="store_true", help="keep the generated folders")
    return parser.parse_args(argv)


def make_synthetic_folder(directory: Path, count: int, size: tuple[int, int], seed: int) -> None:
    import numpy as np
    from PIL import Image

    directory.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(seed)
    width, height = size
    # Smooth gradients plus noise: JPEG sizes (and decode cost) are those of a real photo, not of a flat colour.
    x = np.linspace(0, 255, width, dtype=np.float32)[None, :, None]
    y = np.linspace(0, 255, height, dtype=np.float32)[:, None, None]
    base = np.concatenate([x + 0 * y, y + 0 * x, (x + y) / 2], axis=2)
    for index in range(count):
        noise = rng.normal(0, 18, size=(height, width, 3)).astype(np.float32)
        shift = (index * 37) % 255
        pixels = np.clip((base + shift) % 256 + noise, 0, 255).astype(np.uint8)
        Image.fromarray(pixels, "RGB").save(directory / f"synthetic_{index:04d}.jpg", "JPEG", quality=88)


class Driver:
    def __init__(self, args: argparse.Namespace) -> None:
        from PySide6.QtWidgets import QApplication

        from image_triage import memtrace

        self.args = args
        self.memtrace = memtrace
        self.app = QApplication.instance() or QApplication(sys.argv[:1])
        self.window = None
        self.folders: list[str] = []

    # -- helpers ------------------------------------------------------------------------------------------------------------

    def pump(self, seconds: float) -> None:
        deadline = time.perf_counter() + max(0.0, seconds)
        while True:
            self.app.processEvents()
            remaining = deadline - time.perf_counter()
            if remaining <= 0:
                break
            time.sleep(min(0.005, remaining))

    def pump_until(self, condition, timeout: float, poll: float = 0.02) -> bool:
        deadline = time.perf_counter() + timeout
        while time.perf_counter() < deadline:
            self.app.processEvents()
            if condition():
                return True
            time.sleep(poll)
        self.app.processEvents()
        return bool(condition())

    def phase(self, name: str):
        driver = self

        class _Phase:
            def __enter__(self_inner):
                print(f"[phase] {name} ...", flush=True)
                driver.memtrace.mark(f"phase:{name}:start")
                self_inner.started = time.perf_counter()

            def __exit__(self_inner, *exc):
                driver.memtrace.mark(f"phase:{name}:end", seconds=round(time.perf_counter() - self_inner.started, 2))
                print(f"[phase] {name} done in {time.perf_counter() - self_inner.started:.1f}s", flush=True)

        return _Phase()

    def thumbnails_settled(self) -> bool:
        manager = self.window.thumbnail_manager
        return not manager._pending

    def wait_loaded(self, folder_hint: str = "", timeout: float = 180.0) -> int:
        self.pump_until(lambda: not getattr(self.window, "_scan_in_progress", False) and bool(self.window._records), timeout)
        self.pump_until(self.thumbnails_settled, 60.0)
        return len(self.window._records)

    def photo_indexes(self) -> list[int]:
        return [i for i, record in enumerate(self.window._records) if not getattr(record, "is_folder", False)]

    # -- setup ------------------------------------------------------------------------------------------------------------------

    def build_window(self) -> None:
        from image_triage.window import MainWindow

        self.window = MainWindow()
        self.window.show()
        self.window.schedule_deferred_preview_build()
        if not self.args.no_editor:
            self.window.schedule_deferred_editor_start()
        self.pump(1.0)

    # -- phases -------------------------------------------------------------------------------------------------------------------

    def settle(self) -> None:
        with self.phase("settle"):
            self.pump(self.args.settle_seconds)

    def open(self) -> None:
        with self.phase("open"):
            self.window._scan.load_folder(self.folders[0])
            count = self.wait_loaded()
            print(f"        {count} records in {self.folders[0]}", flush=True)

    def scroll(self) -> None:
        with self.phase("scroll"):
            grid = self.window.grid
            bar = grid.verticalScrollBar()
            top, bottom = bar.minimum(), bar.maximum()
            step = max(1, grid.viewport().height() // 2)
            for value in list(range(top, bottom + 1, step)) + list(range(bottom, top - 1, -step)):
                bar.setValue(value)
                self.pump(0.04)
            self.pump_until(self.thumbnails_settled, 60.0)

    def browse(self) -> None:
        indexes = self.photo_indexes()
        if not indexes:
            print("        no photos; skipped", flush=True)
            return
        with self.phase("browse"):
            self.window._preview_ctl.open_preview(indexes[0])
            self.pump(1.5)
            preview = self.window._preview_ctl.preview_if_built()
            if preview is None:
                print("        the popout did not open; skipped", flush=True)
                return
            period = 1.0 / max(0.5, self.args.browse_hz)
            forward = max(1, self.args.browse_count)
            for _ in range(forward):
                preview.navigate_relative(1)
                self.pump(period)
            for _ in range(forward // 3):
                preview.navigate_relative(-1)
                self.pump(period)
            self.pump(2.0)
            preview.close()
            self.pump(1.0)

    def openclose(self) -> None:
        indexes = self.photo_indexes()
        if not indexes:
            return
        with self.phase("openclose"):
            rng = random.Random(7)
            for cycle in range(self.args.openclose_count):
                self.window._preview_ctl.open_preview(rng.choice(indexes))
                self.pump(0.6)
                preview = self.window._preview_ctl.preview_if_built()
                if preview is not None:
                    preview.close()
                self.pump(0.2)
                if cycle % 10 == 9:
                    self.memtrace.mark(f"openclose:{cycle + 1}")

    def folders_phase(self) -> None:
        if len(self.folders) < 2:
            print("        needs two folders; skipped", flush=True)
            return
        with self.phase("folders"):
            for cycle in range(self.args.folder_cycles):
                folder = self.folders[(cycle + 1) % len(self.folders)]
                self.window._scan.load_folder(folder)
                self.wait_loaded()
                self.pump(1.0)
                self.memtrace.mark(f"folder_cycle:{cycle + 1}", folder=folder)

    def semantic(self) -> None:
        with self.phase("semantic"):
            window = self.window
            if getattr(window, "_active_semantic_index_task", None) is None and not getattr(window, "_semantic_index_active", False):
                print("        no semantic indexing running (no CLIP model configured, or the folder is already indexed); "
                      "open an unindexed folder with the AI runtime installed to exercise it", flush=True)
                return
            done = self.pump_until(
                lambda: getattr(window, "_active_semantic_index_task", None) is None
                and not getattr(window, "_semantic_index_active", False),
                self.args.semantic_timeout,
                poll=0.1,
            )
            print(f"        semantic indexing {'finished' if done else 'still running at timeout'}", flush=True)

    def idle(self) -> None:
        with self.phase("idle"):
            self.memtrace.mark("idle:start")
            self.pump(self.args.idle_seconds)
            self.memtrace.mark("idle:end")
        # One forced collection, measured: what Python still referenced vs. what the allocator has not given back.
        from PySide6.QtCore import QCoreApplication, QEvent

        gc.collect()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.pump(3.0)
        self.memtrace.mark("after_gc")
        self.pump(self.args.settle_seconds / 2)
        self.memtrace.mark("after_gc_settled")

    # -- run ----------------------------------------------------------------------------------------------------------------------

    def run(self, folders: list[str]) -> None:
        self.folders = folders
        self.memtrace.start(self.app)
        self.build_window()
        self.memtrace.mark("window_ready")
        wanted = [name.strip() for name in self.args.phases.split(",") if name.strip()]
        table = {
            "settle": self.settle, "open": self.open, "scroll": self.scroll, "browse": self.browse,
            "openclose": self.openclose, "folders": self.folders_phase, "semantic": self.semantic, "idle": self.idle,
        }
        for name in wanted:
            if name not in table:
                raise SystemExit(f"unknown phase {name!r}; choose from {', '.join(ALL_PHASES)}")
        for name in wanted:
            table[name]()
        self.memtrace.mark("workload_done")
        self.window.close()
        self.pump(3.0)
        self.memtrace.stop()


def main(argv=None) -> int:
    args = parse_args(argv)
    if args.offscreen:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
    scratch = None
    folders = [str(Path(folder)) for folder in args.folder]
    if not folders:
        scratch = Path(tempfile.mkdtemp(prefix="memtrace_corpus_"))
        width, height = (int(part) for part in args.synthetic_size.lower().split("x"))
        for number in (1, 2):
            print(f"[setup] writing {args.synthetic_count} synthetic {width}x{height} photos to folder {number} ...", flush=True)
            make_synthetic_folder(scratch / f"set{number}", args.synthetic_count, (width, height), seed=number)
        folders = [str(scratch / "set1"), str(scratch / "set2")]

    from PySide6.QtCore import QCoreApplication

    QCoreApplication.setOrganizationName("Image Triage")
    QCoreApplication.setApplicationName("Image Triage")

    from image_triage.perf import perf_logger, performance_log_dir

    nav_file = os.environ.get("IMAGE_TRIAGE_NAV_TIMING")
    if not nav_file:
        log_dir = performance_log_dir()
        log_dir.mkdir(parents=True, exist_ok=True)
        nav_file = str(log_dir / f"nav_timing_{time.strftime('%Y%m%d_%H%M%S')}.jsonl")
        os.environ["IMAGE_TRIAGE_NAV_TIMING"] = nav_file

    driver = Driver(args)
    try:
        driver.run(folders)
    finally:
        log_path = perf_logger().path
        print(f"\nperformance log : {log_path}")
        print(f"navigation log  : {nav_file}")
        if scratch is not None and not args.keep_synthetic:
            import shutil

            shutil.rmtree(scratch, ignore_errors=True)
    if log_path is not None and not args.no_report:
        from scripts import memtrace_report  # noqa: PLC0415 - sibling script

        return memtrace_report.main([str(log_path), "--nav", nav_file])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
