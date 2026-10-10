r"""Measure how long the popout takes to put a photo on screen after a navigation input.

This drives the popout's own browsing picture (the part of the pipeline Image Triage controls); the
editor behind it is out of the loop, which is the point: browsing must not wait on it. It reports the
stages ``first_pixel`` (anything of the photo painted: the grid thumbnail) and ``preview`` (the
screen-sized picture) as p50 / p95 / max against the 100 ms target and 200 ms ceiling.

    python scripts/measure_navigation.py --make-set C:\temp\navset --count 120
    python scripts/measure_navigation.py C:\temp\navset --interval-ms 60 --preload 10
    python scripts/measure_navigation.py C:\temp\navset --region off     # the editor-draws-everything baseline

``--interval-ms`` is the gap between navigation inputs: 0 steps as fast as the event loop allows,
~33 is a held arrow key, ~250 is a person stepping through. Cold-cache runs need the OS file cache
dropped between runs; the test set is generated with distinct content per file so a warm cache does
not make every photo look equally fast.

Real RAW files: point the folder argument at them (the camera's embedded JPEG is what browsing
decodes). The generated set is JPEG, a stand-in with similar decode cost per pixel; it says nothing
about NAS throughput.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def make_set(folder: Path, count: int, width: int, height: int) -> None:
    import numpy as np
    from PIL import Image

    folder.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(1)
    ys, xs = np.mgrid[0:height, 0:width]
    for index in range(count):
        # Distinct smooth content plus noise: compresses like a photo and differs per file.
        phase = index * 0.37
        base = np.stack(
            [
                128 + 100 * np.sin(xs / 900.0 + phase),
                128 + 100 * np.sin(ys / 700.0 + phase * 1.3),
                128 + 100 * np.sin((xs + ys) / 1100.0 + phase * 0.7),
            ],
            axis=-1,
        )
        noise = rng.normal(0, 6, size=base.shape)
        pixels = np.clip(base + noise, 0, 255).astype("uint8")
        Image.fromarray(pixels, "RGB").save(folder / f"frame_{index:04d}.jpg", quality=92)
    print(f"wrote {count} photos of {width}x{height} to {folder}")


def run(folder: Path, region: str, interval_ms: int, preload: int, repeat: int, size: tuple[int, int]) -> int:
    from PySide6.QtCore import QCoreApplication, QSize, Qt, QTimer
    from PySide6.QtGui import QImage
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    from image_triage.formats import is_image_file_candidate
    from image_triage.models import ImageRecord
    from image_triage.nav_timing import NavigationTimer, format_summary, summarize
    from image_triage.preview import FullScreenPreview, PreviewEntry
    from image_triage.scanner import normalized_path_key

    paths = sorted(p for p in folder.iterdir() if p.is_file() and is_image_file_candidate(p))
    if len(paths) < 3:
        print(f"need at least 3 photos in {folder}", file=sys.stderr)
        return 2
    QCoreApplication.setOrganizationName("Image Triage")
    QCoreApplication.setApplicationName("Image Triage Navigation Benchmark")
    app = QApplication.instance() or QApplication([sys.argv[0]])

    # The grid's thumbnails exist before the popout opens; stand them in with small decodes.
    thumbs: dict[str, QImage] = {}
    for path in paths:
        image = QImage(str(path))
        thumbs[str(path)] = image.scaled(QSize(256, 256), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation) if not image.isNull() else QImage()

    timer = NavigationTimer()
    preview = FullScreenPreview()
    preview.resize(*size)
    preview.set_photocraft_available(True)
    preview.set_overlay_region(region)
    preview.set_preload_batch_size(preload)

    def painted(path: str, placeholder: bool) -> None:
        key = normalized_path_key(path)
        timer.mark(key, "first_pixel")
        if not placeholder:
            timer.mark(key, "preview")

    preview.native_layer_painted.connect(painted)

    def entry(path: Path) -> PreviewEntry:
        record = ImageRecord(path=str(path), name=path.name, size=path.stat().st_size, modified_ns=path.stat().st_mtime_ns)
        return PreviewEntry(record=record, source_path=str(path), placeholder_image=thumbs.get(str(path)))

    from itertools import islice

    from image_triage.preload_order import candidate_indexes

    def neighbours(index: int) -> list[str]:
        return [str(paths[i]) for i in islice(candidate_indexes(index, len(paths), 1), preload)]

    current = {"index": 0}
    preload_timer = QTimer()
    preload_timer.setSingleShot(True)
    preload_timer.setInterval(120)
    preload_timer.timeout.connect(lambda: preview.preload_paths(neighbours(current["index"])))

    # Note: with the region "off" the popout does not decode at all (the editor draws everything), so the
    # numbers for it show how little the baseline does *in this process*; the editor's own time is
    # measured by running the real app with IMAGE_TRIAGE_NAV_TIMING set.
    for _ in range(repeat):
        for index, path in enumerate(paths):
            started = timer.now()
            timer.begin(normalized_path_key(str(path)), started)
            preview.show_entries([entry(path)])
            if preload and preview.native_first_active():
                # The app throttles preloading (window._preview_preload_timer, 120 ms).
                current["index"] = index
                if not preload_timer.isActive():  # throttled, as in the app
                    preload_timer.start()
            QTest.qWait(max(1, interval_ms))
        # let the last decode land
        QTest.qWait(500)
    timer.flush()
    summary = summarize(record.as_dict() for record in timer.finished)
    print(f"region={region} interval={interval_ms}ms preload={preload} photos={len(paths)} x{repeat}")
    print(format_summary(summary))
    preview.close()
    app.processEvents()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("folder", type=Path, help="folder of photos to step through")
    parser.add_argument("--make-set", action="store_true", help="generate a JPEG test set in FOLDER and exit")
    parser.add_argument("--count", type=int, default=120)
    parser.add_argument("--make-size", default="6000x4000", help="size of generated photos")
    parser.add_argument("--region", choices=["canvas", "full", "off"], default="canvas")
    parser.add_argument("--interval-ms", type=int, default=60)
    parser.add_argument("--preload", type=int, default=10, help="neighbours preloaded each step (0 = none)")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--size", default="1920x1080", help="popout size")
    parser.add_argument("--offscreen", action="store_true", help="use the offscreen Qt platform")
    args = parser.parse_args(argv)

    def parse(text: str) -> tuple[int, int]:
        width, height = text.lower().split("x", 1)
        return int(width), int(height)

    if args.offscreen:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
    if args.make_set:
        make_set(args.folder, args.count, *parse(args.make_size))
        return 0
    return run(args.folder, args.region, args.interval_ms, args.preload, args.repeat, parse(args.size))


if __name__ == "__main__":
    raise SystemExit(main())
