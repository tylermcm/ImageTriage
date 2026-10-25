# QML shell: what is not hooked up yet

> **Status: wiring has NOT been done.** The QML shell is a finished front end (layout, look, menus, settings storage, theme) with almost
> nothing behind its buttons. It was built UI first, on purpose, and the connecting work is deferred, not forgotten. Do not treat any
> control as working unless this file marks it **Real**. A control that is missing from this file has not been checked either way.
>
> Planned order once the UI is done: (1) popout (`FullScreenPreview`, opened from the shell, kept as a separate Widgets window) **done,
> see "Popout" below**, (2) editor on E (PhotoCraft host, with the validation protocol in `docs/photocraft_handoff.md`), (3) decisions,
> ratings (**done**) and folder loading written to the real stores, (4) the menus, the dialogs listed below, then the settings that nothing reads yet.
> Whoever wires something must change its row here in the same commit.

## Windows and dialogs not built or not connected

| Window | In the QML shell | Opens from |
|---|---|---|
| Popout / preview (`qml/Viewer.qml`, `viewer_host.py`) | **connected** (a viewer drawn by the shell; the Widgets viewer is behind `--widgets-popout`; see "Popout" below); no editor from it yet, decisions and ratings are saved to the decision store | Enter, Space, double-click, Open preview, palette, photo menu |
| Editor (PhotoCraft host) | not connected | E, Open in editor |
| Settings | built, storage connected, most values not applied | gear, Ctrl+, |
| Command palette | built, 25 of 53 commands run | Ctrl K |
| Filter dialog and saved presets | built; applies decision, rating and RAW / JPEG only (see below) | Filters menu, palette |
| Batch rename, resize, convert | built and **applying is real** (see below) | Rename and Resize pins, palette, Ctrl+Shift+R / E / C |
| PocketDrop page of the side panel | built; runs PocketDrop's own engine once `pocketdrop.dll` is rebuilt (see the PocketDrop section) | PocketDrop button, Share to phone, phone icon in Collections |
| Deliver / Handoff Builder | built; recipes real, **running is not connected** (see its section) | Ctrl Alt H, palette |
| Move / Copy To Folder cards, handoff progress and done cards | built; a handoff run drives them with real progress (a single photo's move or copy does not use them, see below); `--mock` and the `txState` hook play a script | |
| Collections (new / edit dialog, picker, menu), best-of, Tag People | built; collections **real** (the Widgets library store), people real with a face index, best-of real planner with few signals (see its section) | Collections tab (+, menu, right-click), Add All bar, B, Faces tab (All people…), Ctrl Alt B, palette |
| Cull window (prepare, progress, review, apply) and AI Center (setup, models, defaults, recent runs) | built, **the analysis is real for photos from a folder** (see its section) | Analyze button |
| Photo menu, folder menu and toasts | built; some entries say "not connected yet" (see their section) | right click on a photo or on empty grid space |
| Help, shortcuts guide, catalog, file associations, splash | **Built** (see the Help, shortcuts, library, file types and splash section) | |


The QML shell (`image_triage/qml_shell/`, branch `qml-shell`) is a finished *front end* with a thin real back end. This file lists
everything that looks live but is not connected to the application, so nothing is mistaken for working. **Update it in the same commit
whenever something gets wired or a new control is added.** The Widgets application on `main` is untouched and still does all of this.

Legend: **Real** = reads or writes the application's real data. **Memory** = works on screen, lives only in this process, lost on exit.
**Sample** = shows invented data. **Visual** = draws and animates, does nothing. **Stored** = saved to the real settings but nothing in the
shell reacts to it yet.

## What is real today

| Piece | How |
|---|---|
| Folder contents | `scanner.scan_folder` through `PhotoModel.loadFolder` (the Library page, the Open folder dialog, or `--folder`; a newer request replaces one still being read) |
| Thumbnails | `ThumbService` generates with `load_image_for_display` and stores in the app's disk thumbnail cache (`cache.DiskThumbnailCache`), shown as `file://` URLs |
| Settings values | `SettingsModel` reads and writes the **same `QSettings` store and keys** as `window.MainWindow` (`app_identity.user_settings()`), same defaults and limits (checked by `tests/test_qml_shell_settings.py` against the window's constants) |
| Shortcut overrides | `ui.shortcuts.load_shortcut_overrides` / `save_shortcut_overrides` and `SHORTCUT_REGISTRY` |
| Theme | the ten appearance modes are derived from the real palettes in `ui/theme.py` (Slate and Light are the mockup's own palettes exactly) |
| DPI | Qt high-DPI with fractional scales; every size is in device-independent pixels |

## Main window

### Top bar
| Element | Status | To connect |
|---|---|---|
| Brand icon | Visual | |
| File, Review, View, AI | **Real menus** (`TopMenus.qml`; moving along the bar switches the open menu; Esc or a click below the bar closes). Each row runs the command the palette would (same ids, `Main.qml` `runCommand`) or is dimmed and says it is not connected yet. See "Top bar menus" below. They are hidden in the compact layout (900 px and narrower), as in the mockup | the Widgets entries marked not connected; the Widgets menus Edit, Library, Workflow, Tools and Settings have no button in the bar, their entries are spread over these four |
| Help | **Real**: opens the Help menu (Help, Keyboard shortcuts, Browse Library, File associations, Check for updates, About) | |
| Command search field, Ctrl K | Real (opens the command palette, see below) | |
| Sun button | Real (sets and saves the appearance between Slate and Light) | |
| Inspector button | Memory (`UiState.insp`, not remembered) | persist in `window/state` |
| Side panel and inspector width | **Real**: drag the edge (double click resets); remembered in the settings store | |
| Minimize / maximize / close | Real (`tests/test_qml_shell_window_buttons.py` clicks them). The window is frameless and the bar is its title bar: drag moves it, double click maximizes, edges and corners resize (`WindowEdges.qml`) | the system drop shadow and rounded corners, Windows 11 snap layouts on the maximize button, dragging a maximized window to restore it |

### Rail
| Element | Status | To connect |
|---|---|---|
| Library, Faces, Collections, PocketDrop | Memory (switch the panel page). **Clicking the open one again hides the panel** and leaves the rail (its tooltip says "Hide Library"); any of them brings the panel back; the open page is not remembered between runs. **Icon-only** with the name as a tooltip (docs/nav_rail_label_clipping.md, option 4: "Collections" and "PocketDrop" did not fit); `Rail.showLabels` brings the labels back | |
| The pinned buttons and the settings button | The pins shrink (40 down to 30 px) in a short window and the wheel scrolls them when they still do not fit; the settings button always keeps the bottom of the rail | |
| Open folder | **Real**: the folder dialog (also Ctrl+O and the palette's Open Folder) | |
| New folder | Visual | `folder_ops_controller` |
| Undo (rail button, Ctrl+Z, palette) | **Real** for decisions, ratings, deletes and moves (up to 200 steps in the open folder, saved again as they are undone); a copy and the batch tools have none | the undo stack (`file_ops`, window `_undo_stack`) for files |
| Review menu (winner / keep / reject / clear) | **Real**: saved to the Widgets decision store (see the Grid section) | |
| View menu (filenames, badges) | Memory (also not saved) | `view/*` keys |
| Filters menu (rating, format, clear) | Memory ("Advanced filters…" opens the filters window, see below) | `filtering` |
| Accept / Reject buttons | **Real** as decisions (saved); they do not move or delete files (Delete does) | |
| Sort, Columns menus | Memory (sort and density work on the model) | `view/columns`, details sort keys |
| Share to phone | Real (opens PocketDrop with the current photo added) | |
| Open preview | **Real** (opens the popout on the photo the grid is on) | |
| Compare, Winner ladder, Rename, Resize and convert, Open in editor, Keyboard shortcuts | Visual (Compare toggles a flag nothing reads; the popout has its own Compare on C) | `compare` mode, winner ladder, `batch_rename_controller`, `export_jobs_controller`, `handoff_controller`/PocketDrop, PhotoCraft host |
| Command palette pin | Real (opens the palette) | |
| Rename and Resize and convert pins | Real (open the batch window) | |
| "+" customise pinned buttons | Memory (pins are not saved between runs) | `workspace/toolbar_items`, `workspace/bar_state` |
| Settings | Real (opens the settings window) | |

### Side panel
| Page | Status | To connect |
|---|---|---|
| Library: Drives | **Real**: the Widgets `DriveListModel` (drive letters from the OS, each asked on a background thread, so a dead network drive never freezes the window), label, free space of total and the meter. A click shows that drive's photos and puts the tree on it | |
| Library: Favorites | **Real**: the Widgets key `folders/favorites`, so both front ends share the list; right click to Add to or Remove from Favorites; a favorite provably gone from a local drive is not listed (it stays saved). The count shows once the folder has been opened this session | |
| Library: Folders tree | **Real**: the folders of the open folder's drive, listed one level at a time on a worker thread (`scanner.scan_child_folders`, hidden ones per `view/show_hidden_folders`); the way down to the open folder is opened and scrolled into view; the chevron opens or closes a folder, the row opens it in the grid (a folder with no folders gets the folder icon once it has been listed); right click: Open, Favorites, Show in File Explorer. Opening a folder is remembered in the Widgets key `folders/recent_opened` | `view/single_drive_expansion` is not read (the tree is always one drive); a folder with thousands of sub-folders builds every row (no virtual list); counts are not made in the background, only for folders opened this session; no drag and drop, rename, delete or new folder from the tree |
| Library: search field | **Real**: narrows the favorites and the folders the tree has loaded so far (a folder inside one that is closed is not searched) | search of unopened folders |
| Faces: search, people list, All people…, scoping the grid to a person | Real when the open folder has a face index (the Widgets index file), Sample in `--mock`; see the collections section. The face index block shows the sample 86 % in `--mock` and a plain count otherwise (no scan progress yet) | `face_index` progress |
| Collections: Winners, collection rows, +, menu, phone icon, dragging onto the PocketDrop button | Real (see the collections section). Winners opens the winners of the open folder only (the mockup says "every shoot": there is no library-wide decision store to read yet) | decision store across folders |
| PocketDrop | **Real** with the rebuilt native module, otherwise Sample; see the PocketDrop section below | |

### Grid
| Element | Status | To connect |
|---|---|---|
| Decisions and ratings on cards | **Real**: read from the Widgets decision store when a folder opens (`decision_bridge.py`: the same `DecisionStore`, the session named by the "Profile preset" setting) and saved a moment after every change through the Widgets' own `AnnotationPersistenceQueue` (the database row and the XMP sidecar). A photo that was never decided starts empty; a file that changed since it was decided (size or time) loses its saved decision, as in the Widgets app. Winner is the store's winner flag, Reject its reject flag, **Keep is the tag `keep`** (the Widgets' Keep moves a file to a _keep folder, which this is not; other tags, the Photoshop flag and the review round are left alone). `--mock` shows the mockup's values and saves nothing | a Winners scope across folders (`DecisionStore.winner_paths`) |
| AI scores (the grid's "AI score" sort, the inspector's ring) | **Real when the folder has a saved AI report**: the photo's percentile in its folder, the number the Widgets badge shows (`ai_scores.py`). A photo with no score is -1, and sorts last. Nothing else on a card shows it (the AI badges are not drawn, see "AI tags on cards") | the report is read when a folder opens; an analysis finishing does not refresh it yet (`aiScores.reload()` is the hook) |
| The name on a card | **Real**: the file name without its extension (the format is the tag beside the stars); the inspector, menus and toasts still use the full name | |
| The ink on a card (name, stars, format tag) | **Real**: light ink on a dark scrim, or dark ink on a light one when the bottom strip of the photo is bright (`ThumbService.tone` measures the cached thumbnail, only for the card shown; a rejected or flagged photo is measured as dimmed). Unknown until the thumbnail exists, then the usual look | the threshold (0.6 of sRGB luma) is a judgement, tune it on real shoots; a strip that is half bright and half dark gets one answer for the whole strip |
| W / K / X / U / 1-5 keys | **Real** (saved, see above) | the shell binds fixed keys, not the user's overrides (the shortcut registry) |
| Enter / Space / double-click | **Real**: open the popout (`popout.py`), see "Popout" below | |
| Selection | **Real**: click picks one; Ctrl+click adds or removes, Shift+click and Shift+arrows pick a range, Ctrl+A picks all, Esc clears (`photos.py`, the pick is kept by path across sorts and filters and goes with the folder). The picked cells wear the same accent ring as the current one. No rubber band | the rubber band; `records_view`'s selection model |
| W, K, X, U and 1 to 5 with a pick | **Real**: they apply to every photo picked, as one undo step (the same key again clears them when all have it); W also adds them to the collection that collects winners. Delete, Move, Copy, B and the photo menu still act on the one photo the grid is on | multi-photo file actions with progress cards |
| Drag and drop out of or into the grid | None | `dragdrop_controller` |
| Context menu | Built (see the Context menus and toasts section); multi-select menus do not exist (one selected cell) | `context_menu_controller` |
| Burst groups and stacks | None (settings stored, no grouping) | `bursts`, `face_stack`, `annotation_controller` |
| Compare mode, zoom, details view | None | `details_view`, `compare` |
| AI tags on cards | Badge for the decision only; the AI badges and hover reasons are not drawn | `show_ai_tags_in_grid` |
| Empty and loading states | A "Reading folder…" status text only; no empty-folder or error view | |
| Folder watching, refresh, catalog | None | `scan`, `catalog` |

### Inspector
| Element | Status | To connect |
|---|---|---|
| File name, "RAW + JPG", pixel size, camera, lens, exposure, focal length, capture time | Sample (hash derived) except the name and extension; **the pixel size beside the name is also made up**, so it can disagree with the real measurements on the Quality tab | `metadata.py` / EXIF reader |
| Histogram (the box on the preview, and the Quality tab's) | **Real**: the selected photo's own counts, from `review_tools.build_histogram_stats` (the Widgets app's counting) on a 512 px read of the photo (a RAW is counted from its embedded preview), folded to 64 bars, on a worker thread (`histograms.py`) and cached per photo and modification time. The preview box is brightness; the Quality tab's is red, green and blue. Nothing is drawn until the counts arrive, or if the photo cannot be read | the bars are scaled by the tallest one, ignoring the pure black and pure white ends so a clipped photo does not flatten the rest; the Widgets app's exposure sentence is on the box as its accessible description only; no clipping warnings or probe |
| Decision buttons, stars | **Real** (saved) | |
| Quality tab (focus, exposure, noise, motion blur) | **Real**: the Widgets app's own `review_tools.build_inspection_stats` on the photo read at up to 1200 px (the embedded preview for a RAW), on a worker thread and only while the Quality or AI tab is showing (`inspection.py`, cached per photo and modification time). Focus and exposure use the cut-offs `ai_why` pins; the words are the Widgets readout's. "Inconclusive" when too few regions have texture to judge focus. A photo that cannot be read says so | the Widgets app measures its preview picture, which may be a different size than this one, so a score can differ by a few points; no neighbour prefetch, so the first look at a photo shows "Measuring..." for a moment |
| AI tab: score ring, verdict, reasons, "in this group" | **Real for a folder with a saved AI report** (the `ranked_clusters_export.csv` the Widgets app writes, read with `ai_results.load_ai_bundle`): the ring is the folder percentile, the call is the bucket label ("Likely winner", "Needs review", ...), the reasons are `ai_why`'s (the AI's own sentences and its flags) followed by what was measured, and "in this group" is the photo's AI cluster, ranked, a click going to that photo. A folder with no report shows "Not analyzed yet", the measured findings under "What we measured", and an Analyze folder button (the Cull window, whose analysis is scripted). A photo that is not in the report says so | the shell cannot produce a report itself (the Analyze button is scripted), so this shows what the Widgets app analyzed; the group is the AI's cluster, not the burst grouping of `review_intelligence`; the Widgets app's catalog cache of the report is not used (the CSV is parsed on every folder open); the sample photos of `--mock` keep the mockup's numbers |
| Previous / next | Real on the model | |

### Floating bar
| Element | Status | To connect |
|---|---|---|
| Review, View, Sort menus; All / Winners / Keep / Rejected; size buttons | Memory (same as the rail) | as above |
| Counts next to the filters | Real on the loaded folder | |
| Analyze button | **Real**: opens the Cull window, whose run is the Widgets' own analysis | |

### Status bar
| Element | Status |
|---|---|
| Photo count, "Thumbnails ready" | Real (count, loading flag) |
| "1 selected" | The number picked (1 when nothing is picked on purpose) |
| "Sample data" | Fixed text |
| Folder path, scan progress, AI status chip, update indicator, PhotoCraft status | Not present |

### Window and application
- No saved window geometry or open tab (`window/geometry`, `window/state`); the panel and inspector widths are saved, and the window always starts maximized.
- **Session memory is real**: the shell reopens the folder that was open when it was closed (the Widgets key `window/last_folder`, unless `--folder` is given) and puts the grid back on the photo the person was on (`current` in `view/folder_state`, per folder; "Reopen folders where I left off" turns the photo part off). The photo is saved a moment after it changes and when the window closes. Not remembered: the open side panel tab, the inspector, the window's size and place, the grid's scroll position, filters, and the sort order (the Widgets app's own `sort` and `scroll` in that key are left as they are). Recent folders are recorded for the Widgets app and shown in the File menu.
- No startup tasks: update check, catalog refresh, PhotoCraft prestart, AI setup prompt.
- No application menu, tray, file associations, single-instance handoff, crash/diagnostics hooks (`app_logging`).
- The Inter font is expected from the system. It is not bundled; on a machine without it the fallback is Segoe UI, and text metrics differ.
- Light and high-contrast accessibility: contrast is checked for the ten themes (tests), but there is no screen-reader pass beyond `Accessible.name` on controls.
- Text size: `Interface size` is stored but the shell does not scale its type or spacing by it (only the operating system's display scale).
- Window size under 640 x 520 is refused; below 900 wide the compact layout is used (rail icons only, panel and inspector as sheets).

## Command palette

Ctrl K, the top bar's search field and the rail's palette pin open it (`CommandPalette.qml`, floating near the top over the blurred window;
Esc, a click outside or Ctrl K again closes it). Ranking is the Widgets palette's own function (`ui.command_palette._rank_commands`, checked by
a test), shortcuts come from the shortcut registry with the user's overrides, and recent commands use the Widgets key `commands/recent`.

| Piece | Status | Notes |
|---|---|---|
| Search, ranking, highlight, recents, keyboard (up, down, Enter, Esc), mouse | Real | |
| Command list | Partly real | 53 of the Widgets palette's ids, titles and keywords (`palette_model.py`). Left out: per-collection, per-saved-search, per-recipe, per-panel, per-sort and per-column commands, and the AI state filters, which need live window state |
| Commands that run something | 25 of 53 | Settings, Open Folder, Open Preview (the popout), Advanced Filters and Save Current Search (both open the filters window), Rename Image, Batch Rename, Batch Resize and Batch Convert (all open the batch window), the three themes (Slate, Light, Midnight), sort by name and by AI score, Clear Filters, Compare, Reset Layout, Deliver / Handoff Builder (opens its window), Best-of-Set Auto Assembly, Create Virtual Collection, Add Selection To Collection and People (open their windows), Mark Winner, Reject, Move To _keep (this one only sets the Keep decision). Every other command is listed, can be chosen and is remembered as recent, **and then does nothing** |
| On / Off tag | Compare only | The Widgets palette shows On or Off for ten toggles (zen, bursts, auto advance, performance logging ...); the shell has none of those states. In the demo data they are fixed |
| "Current view" tag | Not shown | needs the grid / details view state |
| Preview Commands and Zen Commands | Model only | the palette can show them (`UiState.paletteContext`), nothing opens it from the popout or zen mode: neither exists in the shell |
| Smart filter and saved search commands | One fixed sample ("Sharp keepers") | the Widgets palette adds one per built-in and saved filter preset |
| Opening the palette while another overlay is up | Not handled | Ctrl K over the settings window opens it on top of it |

## Batch rename, resize and convert window

One window with a Rename / Resize / Convert switch. Opens from the rail's Rename and Resize and convert pins (when pinned), the palette (Rename Image, Batch
Rename / Resize / Convert) and Ctrl+Shift+R, E and C (the shell binds these keys itself, so changed shortcuts in Settings do not move them).
The plans are built by the Widgets app's own planners (`batch_rename.py`, `image_resize.py`, `image_convert.py`), so names, statuses, error messages and
what blocks the apply button are the same as in the Widgets dialogs (tests check this against the planners and against real files).

| Piece | Status | Notes |
|---|---|---|
| Options, live preview, summary, errors, which photos are included | Real | the batch is the photos picked (Ctrl, Shift, Ctrl+A), **else every photo the grid shows**; capped at 20,000 files, the table shows the first 1,200 rows as the Widgets dialogs do |
| **Apply rename / Resize / Convert** | **Real** (not for `--mock`'s sample photos, which only count) | runs the Widgets' own `BatchRenameApplyTask`, `ResizeApplyTask`, `ConvertApplyTask` on a worker thread; a toast follows the progress and the folder is read again. A rename moves the saved decisions with the files and **can be undone** (Ctrl+Z or the toast); resize and convert write copies (or overwrite when that option is on) and have no undo, as in the Widgets app. Photos the batch left out are listed in the toast. The footer says "Sample photos: no file will change" in `--mock` |
| After a rename the grid | Real: the folder is read again, so the grid shows the new names and the decisions that followed | |
| One-photo layout | Not built | the Widgets Resize and Convert dialogs shrink to a small form without a table for one image; the shell always shows the table |
| Previous settings | Not remembered | the dialogs reopen with defaults, as the Widgets ones do |
| `--mock` mode | Plans run on empty files staged in a temporary folder named like the sample photos | |

## Advanced filters window

Opens from the rail's Filters menu ("Advanced filters…") and from the palette (Advanced Filters, Save Current Search). The query, its wording
(`active_filter_labels`), the eight built-in presets and the saved-search format are the Widgets app's own (`filtering.py`); saved searches are
stored under the same key, `filters/saved_queries`, so both front ends share them (tests check the format).

| Piece | Status | Notes |
|---|---|---|
| Form, chips, built-in and saved presets, save / replace / delete, Reset, Cancel | Real | the Widgets app's name prompt and overwrite popup became an inline name box with a "Replace" button, and delete asks in place |
| Apply: **decision filters** (quick filter Winners, Rejects, Unreviewed; review state Accepted, Rejected, Unreviewed), **minimum rating**, **file type RAW and JPEG** | Real, on the in-memory sample ratings and decisions | nothing is stored per folder; the grid forgets them on restart |
| Quick filters Edited, Smart Groups, Duplicates and the AI ones; file types other than RAW and JPEG; AI state; orientation; camera, lens, tags, folder; captured dates; ISO; focal length; search confidence | **Stored in the query and saved with presets, not applied** | the shell has no metadata, no AI results and no duplicate or burst grouping to filter on. The footer says how many set filters are not applied |
| "N of M photos match" | Real for the filters the shell applies | ignores the others, so it can read higher than the Widgets app would |
| The grid after Apply | Gains no highlight in the floating bar's All / Winners / Keep / Rejected control or the rail's rating and format ticks | those keep their own state; "Clear all filters" clears both |
| Preset "Sharp keepers" in the palette | Fixed sample, not a saved search | |
| Dates | Typed as YYYY-MM-DD; no calendar popup (the Widgets dialog has one) | |
| Not stored | The applied query is not remembered between runs or per folder (the Widgets app keeps it in the folder session) | |

## Settings window

The page, rows, controls, search, section list, unsaved-change tracking, conflict check and the live theme preview all work. What each
control *does* once saved:

| Setting | Saved to the real store | Applied by the shell | Notes |
|---|---|---|---|
| Profile preset | Yes (`workflow/session`) | No | The list is a fixed demo list (Default, Wedding, Sports, Travel); the real list is `decision_store.list_sessions()` plus saved workflow presets. "Save preset" does nothing. New profile names cannot be typed (the Widgets dialog's combo is editable) |
| Delete behavior | Yes | No | |
| Check for app updates on startup | Yes | No (no update check in the shell) | |
| Log detailed performance timings | Yes | No | the Widgets app also starts or stops the logger when this changes (`handle_performance_logging_toggled`) |
| Show AI tags on cards | Yes | No | |
| Apply edits before sending to PocketDrop | Yes | No | |
| Theme | Yes | **Yes, live** (preview while editing, reverted by Cancel) | "Auto" follows the system colour scheme at startup only, not when it changes |
| Accent colour, Text contrast | Yes, **new keys** `view/accent_hue`, `view/text_contrast` | **Yes, live** | shell-only features; the Widgets app ignores them |
| Interface size | Yes | No | |
| UI gamma | Yes | No | the Reset button sets 1.00 |
| Card style | Yes | No | the Widgets app restricts the allowed styles on small displays (`display_controller`); the shell offers all three |
| Free smooth scrolling | Yes | No | the grid always scrolls with Qt's default |
| Preview preload | Yes | **Yes** (read by the popout when it is built, so a change applies after a restart) | |
| Editor browsing, editor on demand, starts open, prestart, Camera Raw first | Yes | No | no PhotoCraft host in the shell. The "starts open" row is greyed when "on demand" is off, as in the dialog |
| Advance after Accept or Reject | Yes | **In the popout only** (read when it is built); the grid's own keys do not advance | |
| Group burst sequences, Stack similar burst frames | Yes | No | |
| Show hidden folders | Yes | **Yes**: the Library page's folder tree lists them, and the View menu switch reloads it | |
| Reopen folders where I left off | Yes | **Yes**: the grid goes back to the photo (and the shell reopens the last folder) | the open tab and window place are not remembered |
| One branch expanded, watch folder | Yes | No | the tree is always one drive, and there is no folder watcher |
| Catalog cache | Yes | No | |
| Catalog summary | n/a | **Sample** text | the Widgets dialog shows `scan.catalog_debug_summary()` |
| AI batch size, detailed progress, CPU ranking | Yes | No | |
| Likely winners, Review band, Dispute weight, Base score weight | Yes (clamped to the Widgets limits) | No | the Widgets app pushes the cull thresholds into the classifier on save (`apply_cull_thresholds_to_classifier`, `apply_base_score_blend_to_workflow`); the shell does not |
| Cull breakdown line | n/a | Real (computed from the two sliders; the Widgets dialog also clamps the band so winners + review <= 100) | |
| pHash prefilter, distance, cache, diagnostics | Yes | No | |
| Shortcuts (45 commands from `SHORTCUT_REGISTRY`) | Yes (overrides only, defaults are not written) | No | the shell binds no registry shortcut. Conflicts are checked among the registry rows only, not against fixed keys such as the grid's W/K/X. There is no per-row "Reset" (only the section's Reset to defaults) and no "clear this shortcut" |

Also not done in the settings window:
- **Cancel / Esc after a save**: Save keeps the window open ("All changes saved"); Cancel then closes without undoing the save (it only discards unsaved edits). The Widgets dialog's OK closes.
- The title bar's minimize glyph is decorative; the window cannot be dragged or resized (it is an overlay in the main window, by design: that is what makes the blur live).
- "Open the settings guide" opens Help on the **Settings guide**, an article written from the settings schema itself (every setting under its page, in the Settings window's own words, and searchable in Help); in a window too short for the whole help block only that link stays and the section list scrolls above it. The version line shows `image_triage._version` (and a demo string in `--mock` mode), not PhotoCraft's version.
- Keyboard: Tab order is the declaration order with no focus ring styling beyond Qt's default; arrow keys do not move through the section list.
- The Widgets dialog remembers its size and position (`dialogs/geometry`); the overlay always opens centred.
- The Widgets "Reset" semantics per section differ: here Reset to defaults resets the visible section to the **documented defaults** and marks the rows unsaved.

## PocketDrop panel

`PocketDropPanel.qml` (with `PocketDropMenu.qml`, `PocketDropLibrary.qml`, `PocketDropGhost.qml`, `ConfirmCard.qml`) shows the state of `pocketdrop_model.py`.
**With `pocketdrop.dll` built from this repository's `native/pocketdrop` (API level 2) everything below marked Real is PocketDrop's own engine.** An older
dll (built before API level 2) still serves the Widgets panel, but the QML page then shows "Rebuild it with native/pocketdrop/build_windows.bat" and runs
as Sample. Linux builds of the same sources are what `tests/test_qml_shell_pocketdrop.py` runs against; **the Windows dll and its offline hotspot,
Bluetooth setup and share sheet have not been built or run by whoever wrote this.**

| Element | Status | To connect |
|---|---|---|
| Server, links, QR code, mode switch (Same Wi-Fi, Anywhere, Offline), tunnel download and start, hotspot, Bluetooth setup card, receive code, transfers with speed, received files and notes, "stop sharing after a download", network address, received-files folder | Real (the engine) | |
| Copy / Copy QR / Share / Open link / New link | Real (clipboard through Qt; the Windows share sheet needs the window handle, which the shell passes) | |
| Anywhere warning | Real (the engine's own text and "don't show again"), in `ConfirmCard.qml` | |
| Add files / Folder / received folder dialogs | Real (QtQuick.Dialogs; the engine's own browse callbacks are not used) | |
| Drop files or text onto the card | Real (`DropArea`) | |
| Paste (Ctrl V in the menu, Paste button) | Real. **No keyboard shortcuts** are bound in the shell yet (the Ctrl O / V / C labels in the menu are labels only) | shortcut registry |
| Always on top | Not offered (it makes no sense inside the main window; the engine still has it) | |
| Library button and picker: Selection, Winners, Keep, your collections | Real for Selection, Winners and Keep (the grid's decisions, which are themselves Sample data in folders) and for your collections (the members that are in the open folder; a collection can also hold photos from other folders, which PocketDrop does not list). In `--mock` the five sample collections | |
| A Library group is one row; its files (and a RAW's JPG) are what the engine serves | Real; groups live only in this process | persist groups with the session |
| "Send as: Originals / Proofing JPEGs" chip | **Visual**: the size estimate changes, the originals are always what is sent | handoff recipes (`handoff_controller`), export on send |
| Dragging a collection onto the PocketDrop button, hover to open, drop on the page | Real for the collections in the Collections tab. Photos in the grid cannot be dragged yet | grid drag source (`dragdrop_controller`) |
| Phone icon on collections, Share to phone pin | Real (adds that group, opens the page) | |
| Handoff builder, transfer cards, "Move / Copy To Folder" dialogs | built (see the next section) | |
| `--mock` and tests without the dll | Sample (scripted states built to look like the mockup, nothing listens on a port) | |

Source of truth for the C++: `native/pocketdrop/src/ui` is now Image Triage's own copy (new `Ui::stateJson`, `command`, `removePaths`, `menuCommand`); `scripts/sync_pocketdrop.py` refuses to overwrite it without `--force`.

## Deliver / Handoff Builder and transfer cards

`HandoffWindow.qml` (`handoff_model.py`) and `TransferCards.qml` (`transfer_model.py`). The window's left side tightens its spacing on shorter windows and does not scroll
down to a 690 px tall window (checked at 900, 800 and 720 px tall); below that it scrolls as a last resort.

| Piece | Status | To connect |
|---|---|---|
| Recipes: the five built-ins, their wording, the preview lines (`recipe_summary_lines`), resize presets, convert formats, archive formats | Real (the Widgets app's own) | |
| Saved recipes: Save recipe / Update saved / Delete saved, the Load list | Real: the Widgets settings key `workflow/recipes`, so the Widgets app sees them and the other way round (`--mock` shows a fixed "Wedding handoff" and stores nothing) | |
| Which fields apply (transfer and folder-per-record for bundles; resize, convert, filename, metadata, archive for exports) | Real (the Widgets dialog's rules) | |
| Scope: "N image bundles" | **Real**: the photos picked in the grid, else every photo it shows; "N image bundles" follows the pick |  |
| Destination root: starts at the folder being viewed; Browse… | Real (folder dialog) | |
| **Run workflow** | **Real** (not for `--mock`'s sample photos, which play the cards' script): `workflow_runner.py` runs the recipe with the Widgets' own code on worker threads: Export (`build_workflow_export_plan` + `WorkflowExportTask`, then optionally `CreateArchiveTask`), Bundles + Archive (`CreateArchiveTask`), Bundles + Copy or Move (`file_ops.copy_paths` / `move_paths`, a folder per photo when the recipe groups them). The scope is every photo the grid shows. A **move** takes the saved decisions with the files, takes the photos out of the grid and is undone by one Ctrl+Z; a run that wrote into the open folder reads it again. Photos left out (an edit that could not be rendered, a failed copy) are named in a toast | the Widgets' status-bar message and edited-render handling of a RAW with edits (the shell exports the file itself) |
| Recipe that moves bundles asks "Run destructive workflow?" first | Real wording and rule | |
| Move / Copy confirm, progress, done cards; "Include paired RAW/JPEG and sidecar files"; Cancel; Open destination | Cards and wording follow the mockup; the progress **follows the run** (percent, the photo being handled, the speed in MB/s and the time left, measured). **Cancel** works for copy and move (it stops between photos and says how far it got) and is hidden for export and archive, which have no cancel. "Include paired RAW/JPEG and sidecar files" is real for copy, move and a bundle archive (off: only each photo's own file); it is remembered only on the card, and an export is unaffected. Open destination opens the folder only if it exists | |
| Opening Move / Copy To Folder, To Recent Destination | **Real, without the cards**: Copy to folder / Move to folder (the photo menu's Copy or move, a folder dialog), Move Selection in the palette, and the recent destinations (`folders/recent_destinations`, the Widgets' list) move the photo at once, with a toast and Undo; the Widgets' progress cards are not used for a single photo | multi-photo moves with the progress cards |
| Ctrl Alt H, palette "Deliver / Handoff Builder" | Real (open the window) | |

## Collections, best-of and Tag People

`SidePanel.qml` (Collections and Faces pages and the collection menu), `CollectionDialog.qml`, `CollectionPicker.qml`, `BestOfDialog.qml`, `TagPeopleWindow.qml`, the header and the Add All bar in `Main.qml`, the
collection badge in `PhotoGrid.qml`; models `collections_model.py`, `people_model.py`, `bestof_model.py`, and `PhotoModel.set_scope` (the grid showing a collection, the winners, a person or a shortlist).

| Piece | Status | To connect |
|---|---|---|
| Collections: create, rename, colour, delete, add and remove photos, "New winners go here" | **Real**: `library_store.LibraryStore` (`library.sqlite3`) and the Widgets settings key `collections/also_add_to`, so the Widgets app sees the same collections. Names follow the Widgets dialog's rules | |
| Collection icons (24 icons and a tint; the Icon tab is first and the default, and a new collection starts with the Landscape icon) | **Stored beside the Widgets data** (settings key `collections/icons`, id to icon); the Widgets app ignores them and shows its colour only | the store's schema (a column) |
| "More…" colour button | **Real**: the system colour picker; the colour chosen is stored like any other (a #rrggbb string) and shows as an extra swatch | |
| Export… in the collection menu | **Visual** | `export` of a collection |
| "+" (your own icon) | Removed: the tile is now a 24th icon (Places) | icon import |
| Opening a collection / Winners / a person / a shortlist | Real, as a view of the open folder: the grid shows the members that are in the folder being viewed. Photos of the collection that live in other folders are counted (the sidebar's number) but not shown. "Winners" is the open folder's winners, not "every shoot" | multi-folder grid, catalog |
| W also adds to the collecting collection, B adds the photo you are on (or opens the picker when none collects) | Real | the shortcut registry (fixed keys here) |
| Add All bar, Add All to Collection… picker, Recent first | Real (adds everything the grid shows) | |
| Dropping photos on a collection row, "Drop here" state | The row accepts drops and shows the state; **the grid is not a drag source yet**, so nothing can be dropped on it (`--ui cldrop=<id>` shows the look) | grid drag source |
| Right-click on a photo > Collections | not built (the photo context menu is a later window) | context menu |
| Collection badge on photos in the collecting collection | Real (plain folder view only, as in the mockup) | |
| Delete collection | Asks first (the mockup deletes at once); only the collection is removed | |
| The people list in Faces and Tag People | **Real** from the open folder's face index (the Widgets index file: `ui.face_groups`, `people_search`) when one exists, else the empty text. In `--mock`: 14 sample people | |
| Naming a person, Merge Selected (asks for a name when none of the faces has one), Ignore + Undo, Clear Selected (clears the names) | Real: `people_search.assign_person_names` / `set_clusters_ignored` on the same index | |
| **Rescan Faces**, face crops (the avatars are a coloured disc with the initial), index progress | **Not connected** | `face_index` scan job, crop cache |
| Tag People window layout | Footer always at the bottom (in the mockup the footer jumps up when nothing is selected, a grid bug) | |
| Best-of-Set: strategies, proposal size, the planner | **Real planner** (`workflows.best_of.build_best_of_set_plan`) with the signals the shell has: your winners. AI scores, review results and burst groups are not passed in (they do not exist in the shell yet), so Balanced and Top N differ little | AI results, bursts |
| Best-of result | Shown as a **shortlist view of the folder** with a "Back to folder" button (the Widgets dialog selects the photos; the shell has one selected cell). The dialog's wording says so | multi-select |
| Palette: Best-of-Set Auto Assembly, Create Virtual Collection, Add Selection To Collection, People | Run (Ctrl Alt B opens best-of) | |

## Cull window and AI Center

`CullWindow.qml` (four pages: Prepare, Analyze, Review, Apply, and a done page), `AiCenterWindow.qml`, `CullMini.qml` (the progress card when you choose Keep browsing), the Analyze chip (`AiChip.qml`), and the dim and reason
badge on flagged frames in `PhotoGrid.qml`. Model: `cull_model.py` (`cull`). Designed from the v5 culling plan (`docs/ai_v5_plan.md`, `docs/ai_v5_implementation.md`): reject-only, flag and dim, never hide, no automatic winner.
Mockup: `scripts/qml_parity/cull.dc.html` with `harness_cull.html` (`?scene=cu-prep|cu-missing|cu-installing|cu-error|cu-gpu|cu-cpu|cu-tune|cu-run|cu-mini|cu-review|cu-eyes|cu-ranked|cu-restored|cu-apply|cu-bin|cu-done|cu-grid|ac-ready|ac-fresh|ac-error`);
in the shell `--ui "cullScene=cu-review"` (the same names).

| Piece | Status | To connect |
|---|---|---|
| Wording, reasons (Eyes closed, Soft, Clipped, Blown out, Near-duplicate), the three strictness presets, the fine-tune values | Real wording; Balanced is the cascade's measured `CascadeSettings` (`tests` check it). Careful and Strict are **invented presets** around it | review on other genres (plan, risks) |
| The CPU-ranking warning and its timings | Real text (`cascade.launch`) | |
| "Rank on the CPU" default in AI Center | **Real**: the Widgets settings key `ai/cascade_rank_on_cpu` | |
| **The analysis** (stages, progress, which frames are flagged, the numbers in the detail panel) | **Real** for photos read from a folder (`cull_real.py`, `cascade.task.CascadeRunTask`, the Widgets app's own Analyze run on a worker thread): it reads capture times and previews, finds bursts and stacks, flags blinks, soft, clipped and blown-out frames and near-duplicates, and (when the AI runtime and a GPU, or the CPU switch, allow) runs the eyes check and the two rankers. The window's fine-tune values are passed to the cascade (`CascadeSettings`, and the eyes-closed threshold through `CascadeRunSettings.blink_threshold`, a small addition to the Widgets code; the Widgets app always uses 0.6). The detail panel shows the cascade's own sentence for the frame and its place in its stack; the invented numbers ("31%", "0.82") are only in `--mock`. Frames marked as winners are never flagged. `--mock` and the sample photos keep the scripted run | a per-frame measurements table (the cascade keeps only roles, flags and a sentence) |
| Frames you marked as winners are never flagged | Real rule | |
| AI Center: the AI runtime, graphics card, Visual C++ runtime and the three models (face landmarker, MANIQA, Q-ReAlign) | **Real in the app** (`ai_center.py`; the mockup's scripted states in `--mock` and the tests). Detection: `ai_runtime_packages.load_ai_runtime_installation_status` (installed, with PyTorch), `ai_platform.nvidia_gpu_name` and `vc_runtime_version`, `ai_model_store.bundle_status` (size, pinned revision, file digests); it looks again after every job and on "Check again". A graphics card only counts when the GPU build of the runtime is installed. | the runtime "starts" check is the Visual C++ version, not an import of PyTorch (that runs inside the installer) |
| AI Center buttons | **Real**: Set up AI and Repair run the Widgets installer (`packaging/ai_runtime_installer.py`, the same process Set Up AI runs) after a card that says the size, then install the face model; Install, Repair and Remove on a model use `ai_model_store.install_bundle` / `repair_bundle` / `uninstall_bundle` (hash checked, resumable, cancellable, a card asks before a ranker is deleted); one job at a time, in the background, with one honest percent (`ai_setup_progress.SetupProgress`); a failure says why and the next try clears it; Cancel keeps what was downloaded | the model licences are unverified for commercial use (`docs/ai_v5_model_hosting.md`); nothing has been downloaded by whoever wrote this: the tests use a fake |
| How strict by default (AI Center) | **Real**: kept in the new key `ai/cascade_default_strictness`, and the Cull window starts there | the Careful and Strict presets are still invented |
| Ranking and its "1st choice" marks | **Real** when the rankers ran: the frame the cascade ranked first in its stack (`pick`); without rankers no frame is marked, as in the Widgets app. `--mock` marks the first unflagged frame | |
| Recent runs table | In the app: only the run made in this window (the report saved beside a folder keeps one run, not a history); `--mock` keeps the mockup's two sample rows. **Clear all results** forgets what the window found and applied (rejected frames stay rejected) | a table of runs saved with each folder, once the analysis is real |
| Reject (Apply > Mark as Rejects), Undo, Restore, Keep both | **Real**: the grid's own Reject decision, with Undo | |
| **Move to the recycle bin** | **Real** for photos from a folder (`file_actions.trashPaths`: the Widgets' safe trash, each file with its RAW or JPEG partner and sidecars, one toast and one Undo; also Undo on the done page). On sample photos it marks Rejects and says so | |
| Show in grid | Real (moves the grid to that frame) | |
| Results saved with the folder / reopened later | **Real**: a run writes the Widgets' report beside the folder (`ranked_clusters_export.csv` with each frame's role, flags and sentence) and opening the folder again reads it back, so the flags, stacks and picks return and the inspector's AI tab fills in after a run. Reports written by the Widgets app's older culler have no cascade roles and show nothing here | catalog migration 9 (a catalog copy) |
| Cull on `--mock` | Starts from the mockup's flags; scenes reset those frames to undecided | |

## Context menus and toasts

`ContextMenu.qml` (the photo menu, the folder menu and their fly-out submenus), `Toasts.qml`; models `menu_model.py` (`ctxMenu`, `toasts`). Right click a photo or empty grid space. Mockup:
`scripts/qml_parity/menu.dc.html` with `harness_menu.html` (`?scene=cx-photo|cx-coll|cx-with|cx-move|cx-flag|cx-bg|cx-sort|tt-undo|tt-stack|tt-warn`); in the shell `--ui "ctxScene=cx-photo"` (the same names).

| Entry | Status | To connect |
|---|---|---|
| Winner / Keep / Reject row | **Real** (the grid's decision), with a toast and Undo | the decision store |
| Collections (tick list, New collection…) | **Real** (library store), toast with Undo | |
| Share to phone | **Real** (adds the selection to PocketDrop and opens it) | |
| Rename… / Resize… / Convert… | Real (open the batch window, which applies) | |
| Copy path, Copy file name, Copy folder path | **Real** (clipboard) | |
| Show in File Explorer, Show folder in File Explorer | **Real** (`shell_actions`, Qt) | |
| Refresh folder, Sort by, View (names, badges, size), Clear filters | **Real** (sort goes through the photo model) | |
| Flagged banner: Why was it flagged? / Restore | **Real** (opens the Cull review at that frame; restoring un-flags it, with Undo) | the cull results are Sample |
| Open (Space) | **Real**: opens the popout on that photo | |
| **Open in editor (E)** | **Not connected**: asks the window, nothing listens; a toast says so | the PhotoCraft host |
| **Open with…** | **Not connected**: a toast says so | `shell_actions.open_with_default` |
| **Copy to folder…, Move to folder…, Recent** | **Real** (`file_actions.py`: the bundle with its RAW or JPEG companion and sidecars, a name already taken gets `_1`, a saved decision follows a moved photo; a move can be undone, a copy cannot, as in the Widgets app). Moving into the folder the photo is in is refused | multi-select |
| **Delete (Del)** | **Real** (`file_actions.py`, the Widgets' rules): Safe Trash moves the bundle to the recycle folder and can be undone (Ctrl+Z or the toast), System Trash uses the system trash and asks before deleting for good if the system refuses, a file inside a "recycle bin" folder is deleted for good after a question. The setting is "Delete behavior". The saved decision is dropped and comes back with an undo | |
| Multi-photo menu, Photoshop entry, "Send to" actions, AI group entries | not offered (no multi-select, Photoshop is inside Open with, the new cull has no AI groups) | multi-select |
| Toasts (up to three, 6 seconds, Undo / Show / Open settings buttons) | **Real** component; used by the menu only so far (the other windows do not raise toasts yet) | other actions |
| "Open settings" and "Learn more" toast buttons from the mockup | Only in the mockup's samples | |

## Popout

`viewer_host.py` (`ViewerHost`, the context property `popout`) and `qml/Viewer.qml` are the popout: one photo at full size over the whole main window, an
action bar and a full-width filmstrip (mockup: `scripts/qml_parity/viewer.dc.html`). It replaces the Widgets viewer, which is kept behind
`--widgets-popout` (`popout.py`, `PopoutHost`, tests in `tests/test_qml_shell_popout.py`). The viewer is always dark whatever the theme is; only the
accent follows it. Tests: `tests/test_qml_shell_viewer.py`.

| Piece | Status | To connect |
|---|---|---|
| Opens from Enter, Space, double click, the rail's Open preview pin, the palette's Open Preview, the photo menu's Open | **Real** | |
| The photo | **Real**: a JPEG, PNG, WebP, BMP or GIF is shown from its own file; anything else (RAW, TIFF, PSD...) is read with the Widgets app's `imaging.load_image_for_display` on a worker thread (the embedded preview for a RAW) and shown from a temporary JPEG, removed at quit. The grid's thumbnail, blurred, with a spinner and "Loading full resolution..." shows until it is there. A photo that cannot be read says why | FITS stretch settings and the display colour profile are not given to it; edited variants (`discover_edited_paths`) and "show the saved edit" are not shown |
| Stepping (arrows, A and D, PgUp and PgDn, Home, End, the edge arrows, the counter's arrows, a click on the filmstrip) and the preload of the neighbours (`preview/preload_batch_size`, ahead first) | **Real** | |
| The filmstrip | **Real**: the shell's thumbnails of every photo the grid shows, the photo you are on always in the middle of the screen, the two beside it lifted a little (a parabola of the distance, the lift following in turn). The strip's height is dragged on the grip at its top edge (or the grip's up and down keys), 84 to 260 px, and kept (`viewer/strip_height`); a taller strip has bigger thumbnails and fewer of them | the status dots show the decision only (not the "in the active collection" bar) |
| Winner, Keep, Reject (W, K, X, or the buttons), U clears, 1 to 5 and the stars | **Real**: the photo model's own decision and rating (so saved to the decision store and undoable with Ctrl+Z or the toast's Undo). W also adds the photo to the collection that collects winners; Winner and Reject move on to the next photo when `view/auto_advance` is on (the Widgets setting) | the keys differ from the Widgets viewer in one place: 1 to 5 rate here (1 was "100% zoom" there; Z does that) |
| B (or the collections button) | **Real**: a list of the collections with a tick for those the photo is in; a click adds or removes it. "New collection..." opens the collection dialog (the photo is not added by it) | |
| Delete, M (move) | **Real** (`file_actions.py`; the move asks for a folder in the shell's folder dialog, and the viewer shows the photo that took its place or closes when none is left) | |
| Z, 0 and the Fit / 100% pill | **Real**: 100% shows the photo's own pixels (a RAW's embedded preview may be smaller than the sensor), drag to pan, a thumbnail map shows where you are. L, the loupe, is 100% that follows the pointer | the loupe's zoom steps (Alt+L) and the Widgets viewer's other zoom levels |
| F, focus assist (and Shift+F for the colour) | **Real**: the Widgets app's `review_tools.build_focus_assist_image` on the photo read at up to 2400 px, shown in place of the photo; the on/off and colour are the viewer's own settings keys (`preview/focus_assist_*`) | strength and dim-background have no control here |
| Cull's flag | **Real** when an analysis has run: a "Flagged by Cull" chip, the banner with the reason and **Keep it** (restores the frame and keeps it) or **Reject**, and a bar on the frame's thumbnail | |
| H (hide the controls), the toast | **Real** | |
| **E, Edit in PhotoCraft** | **Not connected**: the button looks ready and says so in a toast; the "Opening in PhotoCraft" screen exists (`--ui "vwScene=opening"`) and nothing starts it. The greyed "PhotoCraft is not installed" look (`vwScene=noedit`) is drawn but nothing decides it yet | step 2 of the plan, `PreviewController`'s PhotoCraft half |
| **T (tag)** | **Not connected**: a toast says so | tags |
| Compare (C), the Winner Ladder, Preview Commands, the Photoshop button, the collections right-click menu, the AI result and review summary lines | **Dropped** from this viewer (compare was left out of the design on purpose); the Widgets viewer behind `--widgets-popout` still has them | |
| Full-screen | The viewer fills the main window (which opens maximised); there is no separate full-screen window and no second-monitor choice | |
| Check hooks | `--ui "vwScene=view\|zoom\|loupe\|focus\|collect\|zen\|opening\|loading\|noedit\|flagged;vwRow=1;vwFh=210"` opens the viewer in the mockup's scene | |
| What was compared with the mockup | view, collect, tall, short, zen, opening, noedit and loading, against `viewer.dc.html` in Chromium on the mockup's 12 photos. The mockup's zoom scene scales the picture by 2.3 and its focus scene draws pretend shapes; here zoom is 100% and focus is the real edge map, so those two are not pixel comparisons. Nothing here was run on Windows or with a GPU | |

## Top bar menus

`TopMenus.qml`, one card (and a fly-out beside it) under the File, Review, View and AI buttons, in the Help menu's look; Help keeps `HelpMenu.qml`. Rows are built in `build()` from the Widgets menu bar (`ui/menus.py`).
Tests: `tests/test_qml_shell_menubar.py`.

| Menu | Real | Not connected (dimmed, a toast says so) |
|---|---|---|
| File | Open Folder (also Ctrl+O), recent folders (the Widgets key, newest five), Refresh Folder (F5), Show in File Explorer, Browse Library, Batch Rename, Resize and Convert (their windows; applying is still not connected), Deliver / Handoff Builder, Share to Phone, Settings, Exit | New Folder (and Empty Recycle Bin, not listed) |
| Review | Open Preview, Mark Winner, Keep, Reject, Clear Decision, Rating 1 to 5 and none (in memory), Rename Selection, Add to Collection, New Collection, Show in File Explorer | Winner Ladder, Move, Delete, Open in Photoshop, Open in Editor |
| View | Appearance (the ten modes), Thumbnail Size, Sort By, Show Photos (decision, RAW / JPG, Advanced Filters, Clear), Filenames, Decision Badges, Inspector, Side Panel, Show Hidden Folders (the Widgets setting, reloads the tree), Reset Layout | the Widgets' Columns, Details view, Zen mode, Burst groups and stacks, Compare mode, Auto-advance switches (not listed) |
| AI | Analyze Folder (the cull window, whose analysis is scripted), AI Center, People, Best-of-Set Auto Assembly | Re-rank AI Culling, Sort into Semantic Folders, Next AI Pick (the Widgets' other AI entries are not listed) |

The keys shown beside a row are the shell's own fixed keys or the registry's defaults, not the user's overrides (see "Grid").

## Tooling that is not part of the product
- `--mock`, `--set`, `--ui`, `--click`, `--hover`, `--grab` are test hooks of `python -m image_triage.qml_shell`.
- `scripts/qml_parity/` renders the HTML mockup for pixel comparison; `scripts/qml_parity.py` overlays two screenshots.

## Help, shortcuts, library, file types and splash

`HelpMenu.qml`, `HelpWindow.qml`, `ShortcutsWindow.qml`, `LibraryWindow.qml`, `FileAssocWindow.qml`, `SplashCard.qml`, `SplashWindow.qml`; one model, `reference_model.py` (`reference`), with the words and tables in `reference_content.py`.
Open them from the top bar's Help menu, F1 (Help) and Ctrl+/ (shortcuts). Mockup: `scripts/qml_parity/help.dc.html` with `harness_help.html` (`?scene=hlp-start|hlp-search|hlp-cull|hlp-trouble|hlp-about|hlp-about-new|hm-open|sk-main|sk-search|lib-results|lib-search|lib-empty|fa-main|fa-none|fa-other-os|sp-start|sp-mid|sp-end`);
in the shell `--ui "refScene=hlp-start"` (the same names). `--mock` and the tests use sample data where the real thing needs the disk or Windows; every row says which.

| Element | Status | To connect |
|---|---|---|
| Help topics and their text | **Real, written for the shell** (they describe the rail, tabs and windows of the QML shell, not the Widgets help pages) | `ui/help_topics.py` is the Widgets text and was not reused |
| Search in Help | Real (matches topic titles, leads and every paragraph; the number is how many blocks matched) | |
| Buttons in articles (Open Collections, Faces, Cull, PocketDrop, Settings, See every shortcut) | **Real** (they open that tab or window) | |
| About: version | **Real** (`_version.__version__`) | |
| About: Check for updates / Check again | **Real**: `updater.check_for_update()` in a worker thread (states: not asked yet, checking, up to date, newer version, could not check, with Cancel) | |
| About: **Download** (a newer version) | **Not connected**: a toast says so | `help_update_controller` (download, verify the SHA-256, install, restart) |
| About: Release notes, Report a problem | **Real** (the default browser: the release's notes URL when the check found one, otherwise the repository's releases / new issue page) | |
| About: Open the log folder | **Real** (`perf.performance_log_dir()`); in `--mock` a toast says it is sample data | |
| About: **Licences and credits** | **Not connected**: a toast says so | a licences page (the LICENSE file and `docs/ai_model_licence_audit.md` hold the text) |
| About: icon | `app_icon-v4.png`: the mark in the top left of the bar, drawn by `scripts/icon_art/render.py` (it is also the window and taskbar icon of the shell, and the .ico the Widgets app and the installer use) | |
| Keyboard shortcuts: the list | **Real, curated**: the shell's own keys plus the registry actions it has; a row tied to a registry action shows the key the user set (`ui/shortcuts.load_shortcut_overrides`) in blue | the shell binds fixed keys, not the user's overrides (also noted under Grid) |
| Keyboard shortcuts: search | Real (matches the action, the category and an exact key name) | |
| Change shortcuts in Settings | Opens the Settings window (the shortcuts section of the settings window is itself a sample list) | the registry editor |
| `--mock` | One key is shown as changed (Compare is V) | |
| Browse Library: roots, counts, search, scope | **Real** (`LibraryStore`: `list_catalog_roots`, `search_catalog`; the counts are the indexed bundles per folder); `--mock` shows the mockup's three sample folders and the folder's photos as the results | |
| Browse Library: Add folder, Index a folder | **Real** (adds the root and indexes it in a worker thread; the footer says "Indexing..." meanwhile); `--mock` adds an empty sample row | |
| Browse Library: Re-index, Remove | **Real** (re-index in a worker thread; remove asks nothing and moves no files, with a toast) | confirm on remove |
| Browse Library: results | At most 120 per search. Cards show the thumbnail from the shell's thumbnail cache | paging |
| Browse Library: **Show N in the grid** | **Not connected**: a toast says so; the results stay in this window | a virtual scope for folders that are not open (`records_view.load_virtual_scope_records`) |
| File associations: states, counts, groups | **Real on Windows** (`file_associations.query_windows_file_association_states`; the types are the formats the app opens, so the totals are not the mockup's 58); `--mock` shows a sample mix | |
| Make available for all types / Remove all | **Real on Windows** (`register_` and `remove_windows_file_associations`) | |
| Make default | **Real on Windows**: opens the Windows chooser for that type and looks again a few seconds later | |
| Windows Default Apps… | **Real on Windows** (`open_windows_default_apps_settings`) | |
| File associations on any other system | The window says "Managed by your system" and hides the Windows buttons (the mockup keeps them visible) | |
| Splash | **Real**: a frameless window shown before the main window is built (status "Starting...", "Loading the interface...", "Reading your folder...", "Ready"; the bar follows); not shown for `--mock` or `--grab` | startup steps are three fixed stages, not measured |
| Splash artwork | `ui/assets/splash_background-v8.png` (redrawn from code, `scripts/splash_art/`) | |
| Closing a window | Esc, the close button, Done or a click outside | |
