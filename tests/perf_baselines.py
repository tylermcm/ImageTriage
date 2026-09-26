"""On-demand performance baselines (WI-0.6). Not collected by the default run.

    py -3.13 -m pytest tests/perf_baselines.py -s -p no:cacheprovider

Runs headless against synthetic data in the test sandbox, so numbers are
comparable run to run on the same machine, not absolute user-experience
figures. Set BASELINE_OUT to also write the results as JSON. NAS folder-open
and interactive slider/popout latency need a real display and are in the
manual smoke script (docs/manual_smoke_test.md).
"""
from __future__ import annotations

import json
import os
import statistics
import time

from PySide6.QtGui import QColor, QImage

from image_triage.models import WinnerMode
from tests.harness import make_jpegs, open_folder

RESULTS: dict[str, object] = {}


def _ms(seconds: float) -> float:
    return round(seconds * 1000.0, 2)


def _record(name: str, value: object) -> None:
    RESULTS[name] = value
    print(f"BASELINE {name} = {value}")


def test_baseline_startup(main_window) -> None:
    from image_triage.window import MainWindow  # noqa: F401  (import cost is in the fixture)

    from tests.harness import dispose_window, make_main_window

    start = time.perf_counter()
    window = make_main_window()
    _record("mainwindow_construct_ms", _ms(time.perf_counter() - start))
    dispose_window(window)


def test_baseline_folder_open_and_winner_toggle(main_window, tmp_path) -> None:
    count = 300
    make_jpegs(tmp_path, [f"img_{index:04d}.jpg" for index in range(count)])

    start = time.perf_counter()
    open_folder(main_window, tmp_path, count)
    _record(f"local_folder_open_{count}_jpegs_ms", _ms(time.perf_counter() - start))

    for mode in (WinnerMode.LOGICAL, WinnerMode.COPY):
        main_window._winner_mode = mode
        timings = []
        for index in range(20):
            begin = time.perf_counter()
            main_window._toggle_winner(index, advance_override=False)
            timings.append(time.perf_counter() - begin)
        _record(f"winner_toggle_{mode.name.lower()}_median_ms", _ms(statistics.median(timings)))
        _record(f"winner_toggle_{mode.name.lower()}_max_ms", _ms(max(timings)))


def test_baseline_editor_render(main_window) -> None:
    from image_triage.editor_render import CpuEditorRenderBackend
    from image_triage.ui.photo_editor_panel import EditRecipe

    backend = CpuEditorRenderBackend()
    for label, (width, height) in (("2mp", (1600, 1200)), ("12mp", (4000, 3000))):
        image = QImage(width, height, QImage.Format.Format_RGB32)
        image.fill(QColor(90, 120, 150))
        timings = []
        for tick in range(6):
            recipe = EditRecipe(exposure=0.1 * tick, contrast=10)
            begin = time.perf_counter()
            backend.render(image, recipe, [], base_key=(label,))
            timings.append(time.perf_counter() - begin)
        _record(f"editor_render_{label}_first_ms", _ms(timings[0]))
        _record(f"editor_render_{label}_tick_median_ms", _ms(statistics.median(timings[1:])))


def test_zz_write_results_last() -> None:
    out = os.environ.get("BASELINE_OUT")
    if out:
        with open(out, "w", encoding="utf-8") as handle:
            json.dump(RESULTS, handle, indent=2, sort_keys=True)
