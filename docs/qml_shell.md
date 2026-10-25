# QML shell (branch `qml-shell`)

An all-QML main window for Image Triage (rail, side panel, grid, inspector, floating bar, menus). The popout/preview stays a separate
Qt Widgets window; the widget grid on `main` is untouched and remains the fallback.

```bash
python -m image_triage.qml_shell --folder D:\Photos\Shoot1        # a real folder
python -m image_triage.qml_shell --folder ... --light              # light theme
python -m image_triage.qml_shell --mock <dir with p01..p12.jpg>    # the mockup's data (for pixel comparison)
```

Test hooks: `--ui "k=v;..."` (interface state), `--set "id=value;..."` (settings rows), `--hover x,y`, `--click`, `--grab`, `--size`.
Ctrl+, or the rail's gear opens the settings window.

## Design

* `qml/` holds the UI; `UiState.qml` (singleton) holds interface state (tab, inspector, open menu, filters, pins, `compact`).
  Context properties from Python: `theme` (palette derived from base/accent/contrast in OKLab), `photos` (`PhotoModel`), `thumbs`
  (`ThumbService`), `startup` (test hook).
* **Thumbnails never run Python on Qt's loader threads.** An earlier `QQuickAsyncImageProvider` deadlocked with the GUI thread's GIL under
  a fast fling. `ThumbService.url(path, bucket)` returns the disk-cache file URL when it exists (QML loads it natively) and queues generation
  on a worker otherwise; `ready(path)` tells the delegate to re-ask. Buckets share cache files between neighbours.
* The grid is a `GridView` (virtualized, `reuseItems`) over a flat model; 100,000 photos load in ~0.2 s with flat memory.
* Frosted glass (`Glass.qml`) = `ShaderEffectSource` of the window content under the item + `MultiEffect` blur/saturation + tint, with the
  CSS box-shadows drawn only outside the shape (inverted mask). Menus (the top bar's, Help, context, the rail's and floating bar's popovers,
  dropdowns) are not frosted: `Glass { frosted: false }` is a solid card, so nothing behind a menu shows through it.
* Responsive: `window.width <= 900` switches to the compact layout (52px rail, panel and inspector as sheets over the grid, menus as a bottom
  sheet); the bar drops labels below 1080px of grid width and groups below 860/640.
* Fractional font sizes (10.5, 11.5 ...) go through `pointSize` (`UiState.pt()`), which assumes 96 dpi; the base font is Inter, falling back to
  Segoe UI, with tabular numerals.

## Top bar menus

The File, Review, View and AI buttons open `TopMenus.qml`; Help opens `HelpMenu.qml`. A row runs a command by the palette's id through `runCommand` in `Main.qml` (the palette, the menus and the F5 key share it) or is dimmed with a toast saying it is not connected.
Which rows are real: docs/qml_unwired.md, section "Top bar menus". Tests: `tests/test_qml_shell_menubar.py`.

## Library page

`library_nav.py` (`LibraryNavModel`, the context property `libraryNav`) feeds the side panel's Library page from what the Widgets app uses: the `folders/favorites` and `folders/recent_opened` settings keys, `DriveListModel` for the drives and `scanner.scan_child_folders` for the tree
(one level at a time, on a worker thread). The shell reopens the last folder and the photo you were on (the Widgets keys `window/last_folder` and `view/folder_state`). Clicking a drive, a favorite or a folder opens it in the grid; the rail's Open folder pin, Ctrl+O and the palette open the folder dialog. `--mock` shows the mockup's sample page.
What is and is not real: docs/qml_unwired.md, section "Side panel". Tests: `tests/test_qml_shell_library.py`.

## Popout

Enter, Space, a double click, the rail's Open preview pin, the palette's Open Preview and the photo menu's Open show the photo in `preview.FullScreenPreview`, the Widgets app's own full-screen viewer, in its own window. `popout.py` (`PopoutHost`)
builds it, hands it the grid's photos, steps and preloads, feeds its filmstrip from the shell's thumbnail cache and writes its decisions and ratings back to the photo model. The shell runs on a `QApplication` for this.
Closing the viewer puts the grid on the photo it ended on. No editor starts from it yet. What is and is not connected: docs/qml_unwired.md, section "Popout". Tests: `tests/test_qml_shell_popout.py`.

## Histogram

The inspector's histogram (the box on the preview, and the Quality tab's red, green and blue) shows the selected photo's own counts. `histograms.py` (`HistogramService`, the context property `histograms`) counts a 512 px read of the photo with the Widgets app's `review_tools.build_histogram_stats`
on one worker thread, skips photos the person has already flipped past, and keeps the result per photo and modification time; `Histogram.qml` draws it. Note for QML authors: do not name a property `data` on an Item, it is the default property that holds the item's children.
What is and is not real: docs/qml_unwired.md, section "Inspector". Tests: `tests/test_qml_shell_histograms.py`.

## Inspector: Quality and AI

The Quality tab's four checks are measured on the photo itself: `inspection.py` (`InspectionService`, the context property `inspection`) runs the Widgets app's `build_inspection_stats` on a 1200 px read of the photo, on the same one-thread worker as the histogram (`photo_jobs.py`:
photos you flip past are skipped, results are kept per photo and modification time), and only while the Quality or AI tab is showing. The AI tab reads the folder's saved AI report (`ai_scores.py`, `AiScores`, the context property `aiScores`: the Widgets app's `ranked_clusters_export.csv`, parsed by `ai_results.load_ai_bundle` on a worker thread when a folder opens) and
gives the grid its AI scores (-1 for a photo with none). `Inspector.qml` draws what `inspection.get(path)` and `aiScores.get(path)` answer; the mockup's twelve sample photos carry ready-made answers of the same shape (`inspector_sample.py`).
What is and is not real: docs/qml_unwired.md, section "Inspector". Tests: `tests/test_qml_shell_inspector_data.py`.

## Window frame

The main window is frameless (`Qt.FramelessWindowHint`): no Windows title bar. `TopBar.qml` is the title bar. Dragging an empty part calls `startSystemMove()` (the system's own move, so Windows snapping works),
a double click toggles maximized, and the three buttons at the right minimize, maximize or restore, and close. `WindowEdges.qml` puts thin grips on the edges and corners that call `startSystemResize()`;
they are off while the window is maximized. The buttons show in the compact layout too, since the window has no other way to close. `tests/test_qml_shell.py` checks the flag, the buttons and the double click.
The real app opens maximized (not with `--size`, `--grab` or `--mock`, which keep their exact size); restoring gives 1440 x 900, centred.

The side panel and the inspector are resized by dragging their edge (`SideResizer.qml`; a double click puts the width back). Minimums keep their contents from clipping (panel 260, inspector 300), the ceiling is the default width (300 and 352), so they only ever get narrower than the mockup's, and the grid always keeps at least 400 px: when the window is narrow the panels shrink toward their minimums first. The widths are remembered (`settings.layoutWidth`, QSettings `window/panel_width`, `window/inspector_width`). The compact layout (900 px and narrower) uses fixed sheets and has no handles. `--ui "panelW=300;inspW=352"` sets them for checks. Tests: `tests/test_qml_shell_layout.py`.

## Settings window

`SettingsWindow.qml` is an in-window overlay (not a second OS window), so the live app behind it can be blurred. Rows and controls are
`SRow`, `SSwitch`, `SSelect`, `SStepper`, `SSlider`, `SSwatches`, `SKeys`, `SButton`. The page is generated from the schema in
`settings_model.py`: `SettingsModel` reads and writes the same `QSettings` keys, defaults and limits as the Widgets settings dialog
(`tests/test_qml_shell_settings.py` checks them against `window.MainWindow`). Theme is one of the real ten appearance modes and previews
live; accent hue and text contrast are new shell-only keys. Save keeps the window open; Cancel discards unsaved edits and closes.
Most saved settings are not yet applied by the shell: see `docs/qml_unwired.md`, which lists everything not hooked up, in both windows.

## Command palette

`CommandPalette.qml` is a card floating near the top (96 px down) over the dimmed and blurred window (Ctrl K, the top bar's search field, the rail pin).
`palette_model.py` supplies the commands (ids, titles, keywords of the Widgets palette), the Widgets ranking function, shortcuts from the
shortcut registry and recents under `commands/recent`. Only a few commands run an action so far; see `docs/qml_unwired.md`.
`scripts/qml_parity/palette.dc.html` is the mockup (`?pal=1&q=...&pi=N&ctx=preview&light=1` in `harness_palette.html`).

## Advanced filters

`FiltersWindow.qml` (overlay like the settings window; `filters_model.py` holds the query, presets and saved searches in the Widgets app's own
format and key). Only decisions, rating and RAW / JPEG are applied to the grid so far; see `docs/qml_unwired.md`.
`scripts/qml_parity/filters.dc.html` is the mockup (`?flt=1&fsel=b0&fopen=ai&fsave=&fdel=Low%20light&fscroll=400&light=1`).

## Batch tools

`BatchWindow.qml` (rename, resize and convert in one overlay window; `batch_model.py` builds the plans with the Widgets app's planners). Applying is not
connected: no file is changed. `scripts/qml_parity/batch.dc.html` is the mockup (`?bt=rename|resize|convert&bset=preset:custom;cw:0&bopen=fmt`).

## PocketDrop

The PocketDrop page runs PocketDrop's own C++ engine (`native/pocketdrop`) as a model: `Ui::stateJson()` is what `paint()` would have drawn, `Ui::command()` is what a click would have done, both reached through
`pd_state_json` / `pd_command` / `pd_remove_paths` (API level 2, additive; `pd_abi_version` stays 1 so the Widgets panel keeps working with the same dll). `image_triage/pocketdrop/host.py` supplies the OS services, `qml_shell/pocketdrop_model.py` adds
Library groups (a selection, the winners, a collection as one row) and a scripted sample state for `--mock`. `scripts/qml_parity/pocketdrop.dc.html` is the mockup
(`?tab=drop&pdmode=wifi|anywhere|offline&pdstate=ready|drag|busy|missing|downloading|failed|bluetooth|nonet&pdact=sending|receiving|done&pdlist=empty|shared|mixed&menu=1|lib=1&pdflow=dragging`);
the same states open in the shell with `--ui "tab=drop;pdMode=anywhere;pdState=missing;pdActivity=none;pdList=mixed;menu=pdmenu|pdlib;pdFlow=dragging"`.
Build the Windows dll with `native/pocketdrop/build_windows.bat`; `tests/test_qml_shell_pocketdrop.py` builds a Linux copy.

## Collections, best-of and people

`SidePanel.qml` (Collections and Faces pages, the collection menu), `CollectionDialog.qml`, `CollectionPicker.qml`, `BestOfDialog.qml`, `TagPeopleWindow.qml`; models `collections_model.py` (the Widgets `LibraryStore`),
`people_model.py` (the face index) and `bestof_model.py` (the Widgets planner). The grid can show "a view of the folder": `PhotoModel.set_scope(kind, id, predicate)` filters the loaded rows, the header and the Add All bar follow `photos.scopeKind`.
Mockup: `scripts/qml_parity/collections.dc.html` with `harness_collections.html` (`?scene=new|edit|icons|name-error|picker|menu|viewing|winners|addbar|empty|bestof|faces|faces-none|person|people|people-selected|people-undo|people-unnamed`);
in the shell `--ui "tab=coll;sel=1;collDlg=new"`, `collDlgId=c3;collDlg=edit`, `collMenu=c2`, `filter=win;collPicker=true`, `bestOfOpen=true;boN=6`, `scope=c:c1|winners|p:p1`, `tab=faces`, `peopleOpen=true;peopleSel=p5,p6;peopleFilter=unnamed`
(put an id before the flag that opens the window; `--mock` supplies the sample collections c1 to c5 and people p1 to p14). The Wedding icon is a diamond ring (the mockup copy carries the same path).
What is real and what is not: docs/qml_unwired.md, section "Collections, best-of and Tag People". Tests: `tests/test_qml_shell_collections.py`.

## Cull window and AI Center

`CullWindow.qml`, `AiCenterWindow.qml`, `CullMini.qml`; model `cull_model.py` (the Analyze chip opens it). Mockup `scripts/qml_parity/cull.dc.html` with `harness_cull.html` (`?scene=cu-prep` and the others listed in docs/qml_unwired.md);
in the shell `--ui "cullScene=cu-review"`. Flagged frames draw a reason badge and are dimmed in the grid (`cull.gridFlag(path)`). **The analysis is scripted**, but the AI Center is real in the app: `ai_center.py` looks at what is installed on this PC (runtime, graphics card, Visual C++, the three models) and its buttons install, repair and remove them with the Widgets app's own code (`--mock` and the tests keep the mockup's scripted one; `tests/test_qml_shell_ai_center.py` uses a fake PC, nothing is downloaded). See docs/qml_unwired.md, section "Cull window and AI Center". Tests: `tests/test_qml_shell_cull.py`.

## Context menus and toasts

`ContextMenu.qml`, `Toasts.qml`; models `menu_model.py` (`ctxMenu`, `toasts`). Right click in `PhotoGrid.qml` asks `Main.qml` to open the menu at the pointer. Mockup `scripts/qml_parity/menu.dc.html` with `harness_menu.html`;
in the shell `--ui "ctxScene=cx-photo"`. What is real and what says "not connected yet": docs/qml_unwired.md, section "Context menus and toasts". Tests: `tests/test_qml_shell_menu.py`.

## Help, shortcuts, library, file associations and splash

`HelpMenu.qml`, `HelpWindow.qml`, `ShortcutsWindow.qml`, `LibraryWindow.qml`, `FileAssocWindow.qml`, `SplashCard.qml`, `SplashWindow.qml`; model `reference_model.py` (`reference`). The top bar's Help button, F1 and Ctrl+/ open them.
Mockup `scripts/qml_parity/help.dc.html` with `harness_help.html`; in the shell `--ui "refScene=hlp-start"`. What is real and what says "not connected yet": docs/qml_unwired.md, section "Help, shortcuts, library, file types and splash". Tests: `tests/test_qml_shell_reference.py`.

## Handoff builder and transfer cards

`HandoffWindow.qml` and `TransferCards.qml`, models `handoff_model.py` and `transfer_model.py`. Mockup: `scripts/qml_parity/handoff.dc.html` (`?ho=1&hrecipe=saved:wedding_handoff`, `?tx=confirm|progress|done&txk=copy|move|wf&txp=0.42`);
in the shell `--ui "handoffOpen=true;hoRecipe=saved:wedding_handoff"` and `--ui "txState=progress;txKind=copy;txP=0.42"`. Running a recipe is not connected (docs/qml_unwired.md).

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

Full list: `docs/qml_unwired.md`. In short:


The editor on E (the popout itself is connected, see below), decisions and ratings persisted to the catalog (the shell shows sample values for
real folders), drag and drop, multi-select, compare in the grid, PhotoCraft/AI integration, the frameless window's system shadow and snap layouts (`docs/qml_unwired.md`), high-DPI checks, Windows/GPU
testing (everything above was verified headlessly with a software renderer), `topAligned` switch UI, bundling the Inter font.

## Tests

`tests/test_qml_shell.py`, `tests/test_qml_shell_settings.py`, `tests/test_qml_shell_palette.py`, `tests/test_qml_shell_filters.py` and `tests/test_qml_shell_batch.py`, `tests/test_qml_shell_pocketdrop.py`, `tests/test_qml_shell_handoff.py`, `tests/test_qml_shell_collections.py`, `tests/test_qml_shell_cull.py`, `tests/test_qml_shell_menu.py`, `tests/test_qml_shell_reference.py`, `tests/test_qml_shell_popout.py`, `tests/test_qml_shell_library.py`, `tests/test_qml_shell_layout.py` (theme contrast, model, thumbnails, headless load) and `scripts/memtrace_*` for memory.
