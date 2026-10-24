# QML shell (branch `qml-shell`)

An all-QML main window for Image Triage (rail, side panel, grid, inspector, floating bar, menus). The popout/preview stays a separate
Qt Widgets window; the widget grid on `main` is untouched and remains the fallback.

```bash
python -m image_triage.qml_shell --folder D:\Photos\Shoot1        # a real folder
python -m image_triage.qml_shell --folder ... --light              # light theme
python -m image_triage.qml_shell --mock <dir with p01..p12.jpg>    # the mockup's data (for pixel comparison)
```

## Design

* `qml/` holds the UI; `UiState.qml` (singleton) holds interface state (tab, inspector, open menu, filters, pins, `compact`).
  Context properties from Python: `theme` (palette derived from base/accent/contrast in OKLab), `photos` (`PhotoModel`), `thumbs`
  (`ThumbService`), `startup` (test hook).
* **Thumbnails never run Python on Qt's loader threads.** An earlier `QQuickAsyncImageProvider` deadlocked with the GUI thread's GIL under
  a fast fling. `ThumbService.url(path, bucket)` returns the disk-cache file URL when it exists (QML loads it natively) and queues generation
  on a worker otherwise; `ready(path)` tells the delegate to re-ask. Buckets share cache files between neighbours.
* The grid is a `GridView` (virtualized, `reuseItems`) over a flat model; 100,000 photos load in ~0.2 s with flat memory.
* Frosted glass (`Glass.qml`) = `ShaderEffectSource` of the window content under the item + `MultiEffect` blur/saturation + tint, with the
  CSS box-shadows drawn only outside the shape (inverted mask).
* Responsive: `window.width <= 900` switches to the compact layout (52px rail, panel and inspector as sheets over the grid, menus as a bottom
  sheet); the bar drops labels below 1080px of grid width and groups below 860/640.
* Fractional font sizes (10.5, 11.5 ...) go through `pointSize` (`UiState.pt()`), which assumes 96 dpi; the base font is Inter, falling back to
  Segoe UI, with tabular numerals.

## Resolution and scaling

Every size is in device-independent pixels. Qt multiplies by the screen's scale (Windows "Scale", Linux `QT_SCALE_FACTOR`/compositor scale), and
the rounding policy is `PassThrough`, so 125%, 150% and 175% are used exactly. A 1080p screen at 100%, a 4K screen at 200% and a 1440p
screen at 133% all lay the window out identically (1440 x 900 dips); only a screen with less than 1440 x 900 dips available shrinks it
(92% of the work area, centred). Headless checks at 1.0, 1.25, 1.5 and 2.0 against the mockup rendered at the same `deviceScaleFactor`
(`scripts/qml_parity/shotd.js`) differ by 3.1 to 3.5 / 255, the same as at 1.0. Thumbnails are requested at the device pixel size.

## Parity with the HTML mockup

See `scripts/qml_parity/README.md`. The mockup is rendered in Chromium and compared pixel for pixel in dark and light, every rail tab, all
three inspector tabs, the bar's menus, closed panels, three densities, and 1100/900 px windows.

## Not done yet

Popout wiring (Enter/Space/double-click -> `openRequested`), decisions and ratings persisted to the catalog (the shell shows sample values for
real folders), drag and drop, multi-select, zoom, compare, PhotoCraft/AI integration, a custom window frame, high-DPI checks, Windows/GPU
testing (everything above was verified headlessly with a software renderer), `topAligned` switch UI, bundling the Inter font.

## Tests

`tests/test_qml_shell.py` (theme contrast, model, thumbnails, headless load) and `scripts/memtrace_*` for memory.
