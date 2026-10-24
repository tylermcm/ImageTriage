# QML shell: what is not hooked up yet

> **Status: wiring has NOT been done.** The QML shell is a finished front end (layout, look, menus, settings storage, theme) with almost
> nothing behind its buttons. It was built UI first, on purpose, and the connecting work is deferred, not forgotten. Do not treat any
> control as working unless this file marks it **Real**. A control that is missing from this file has not been checked either way.
>
> Planned order once the UI is done: (1) popout (`FullScreenPreview`, opened from the shell, kept as a separate Widgets window),
> (2) editor on E (PhotoCraft host, with the validation protocol in `docs/photocraft_handoff.md`), (3) decisions, ratings and folder
> loading written to the real stores, (4) the menus, the dialogs listed below, then the settings that nothing reads yet.
> Whoever wires something must change its row here in the same commit.

## Windows and dialogs not built or not connected

| Window | In the QML shell | Opens from |
|---|---|---|
| Popout / preview (`preview.py`) | not connected (stays a Widgets window) | Enter, Space, double-click, Open preview |
| Editor (PhotoCraft host) | not connected | E, Open in editor |
| Settings | built, storage connected, most values not applied | gear, Ctrl+, |
| Command palette | built, 12 of 53 commands run | Ctrl K |
| Filter dialog and saved presets | not built | Filters, Advanced Filters |
| Batch rename, resize, convert | not built | Rename, Resize and convert |
| Share to phone, handoff, transfers (PocketDrop) | not built | Share to phone |
| Collections, best-of, people / faces | not built | Collections tab, Faces tab |
| AI setup, review, apply decisions, cull preferences, workflow center, progress | not built | Analyze |
| Help, shortcuts guide, catalog, file associations, splash, toasts, context menu | not built | menus, right click |


The QML shell (`image_triage/qml_shell/`, branch `qml-shell`) is a finished *front end* with a thin real back end. This file lists
everything that looks live but is not connected to the application, so nothing is mistaken for working. **Update it in the same commit
whenever something gets wired or a new control is added.** The Widgets application on `main` is untouched and still does all of this.

Legend: **Real** = reads or writes the application's real data. **Memory** = works on screen, lives only in this process, lost on exit.
**Sample** = shows invented data. **Visual** = draws and animates, does nothing. **Stored** = saved to the real settings but nothing in the
shell reacts to it yet.

## What is real today

| Piece | How |
|---|---|
| Folder contents | `scanner.scan_folder` through `PhotoModel.loadFolder` (command line `--folder` only) |
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
| File / Review / View / AI / Help | Visual (hover only, no menus) | the Widgets menu bar actions (`main_window_actions`) |
| Command search field, Ctrl K | Real (opens the command palette, see below) | |
| Sun button | Real (sets and saves the appearance between Slate and Light) | |
| Inspector button | Memory (`UiState.insp`, not remembered) | persist in `window/state` |
| Minimize / maximize / close glyphs | Visual. The shell still uses the operating system's frame, so these duplicate it | custom frameless window with native resize, snap and shadows |

### Rail
| Element | Status | To connect |
|---|---|---|
| Library, Faces, Collections, PocketDrop | Memory (switch the panel page) | |
| Open folder, New folder | Visual | folder dialog and `folder_ops_controller`; today only `--folder` loads a folder |
| Undo | Visual | the undo stack (`file_ops`, window `_undo_stack`) |
| Review menu (winner / keep / reject / clear) | Memory (decisions on the in-memory model) | `decision_store` per session |
| View menu (filenames, badges) | Memory (also not saved) | `view/*` keys |
| Filters menu (rating, format, clear) | Memory | `filtering`, saved filter presets |
| Accept / Reject buttons | Memory | `decision_store`, move/delete via `file_ops` and the delete mode |
| Sort, Columns menus | Memory (sort and density work on the model) | `view/columns`, details sort keys |
| Compare, Open preview, Winner ladder, Rename, Resize and convert, Share to phone, Open in editor, Keyboard shortcuts | Visual (Compare toggles a flag nothing reads) | `compare` mode, `preview` popout, `batch_rename_controller`, `export_jobs_controller`, `handoff_controller`/PocketDrop, PhotoCraft host |
| Command palette pin | Real (opens the palette) | |
| "+" customise pinned buttons | Memory (pins are not saved between runs) | `workspace/toolbar_items`, `workspace/bar_state` |
| Settings | Real (opens the settings window) | |

### Side panel
| Page | Status | To connect |
|---|---|---|
| Library: search field | Visual | folder filter |
| Library: Favorites, Drives (names, sizes), Folders tree | Sample (the tree does not navigate) | `drive_list_model`, `load_favorites`, recent folders, the real folder tree and its expansion rules (`view/single_drive_expansion`, `view/show_hidden_folders`) |
| Faces: people, counts, indexing progress | Sample | `face_index`, `face_stack` |
| Collections: Winners, projects | Sample (the winner count is a fixed 96) | collections store (`docs/collections_and_future_projects_decision.md`) |
| PocketDrop: device, drop zone, transfers | Sample (drop zone has no drop handling; transfers are fixed) | `handoff_controller`, PocketDrop transport |

### Grid
| Element | Status | To connect |
|---|---|---|
| Decisions, ratings, AI scores on cards | Sample (derived from a hash of the file name) in folders, mockup values in `--mock` | `decision_store`, catalog, `ai_results` |
| W / K / X / U / 1-5 keys | Memory | the shortcut registry (the shell binds fixed keys, not the user's overrides) |
| Enter / Space / double-click | `openRequested(row)` is emitted and **nothing listens** | the popout (`preview.py`, `FullScreenPreview`) in its own window |
| Selection | One cell only; no multi-select, range select, rubber band | `records_view`, selection model |
| Drag and drop out of or into the grid | None | `dragdrop_controller` |
| Context menu | None | `context_menu_controller` |
| Burst groups and stacks | None (settings stored, no grouping) | `bursts`, `face_stack`, `annotation_controller` |
| Compare mode, zoom, details view | None | `details_view`, `compare` |
| AI tags on cards | Badge for the decision only; the AI badges and hover reasons are not drawn | `show_ai_tags_in_grid` |
| Empty and loading states | A "Reading folder…" status text only; no empty-folder or error view | |
| Folder watching, refresh, catalog | None | `scan`, `catalog` |

### Inspector
| Element | Status | To connect |
|---|---|---|
| File name, "RAW + JPG", pixel size, camera, lens, exposure, focal length, capture time | Sample (hash derived) except the name and extension | `metadata.py` / EXIF reader |
| Histogram | Sample (a fixed drawing) | real histogram from the preview image |
| Decision buttons, stars | Memory | `decision_store` |
| Quality tab (focus, exposure, noise, motion blur) | Sample | `ai_results`, inspection stats in `preview.py` |
| AI tab: score ring, verdict, reasons, "in this group" | Sample (group is the next three photos) | `ai_results`, `ai_why`, burst groups |
| Previous / next | Real on the model | |

### Floating bar
| Element | Status | To connect |
|---|---|---|
| Review, View, Sort menus; All / Winners / Keep / Rejected; size buttons | Memory (same as the rail) | as above |
| Counts next to the filters | Real on the loaded folder | |
| Analyze button | Visual: a 2.6 second timer fakes "analyzing" and "AI ready" | `ai_run_controller`, `ai_workflow_center` |

### Status bar
| Element | Status |
|---|---|
| Photo count, "Thumbnails ready" | Real (count, loading flag) |
| "1 selected" | Fixed text |
| "Sample data" | Fixed text |
| Folder path, scan progress, AI status chip, update indicator, PhotoCraft status | Not present |

### Window and application
- No saved window geometry, panel widths or open tab (`window/geometry`, `window/state`, pane width ratios).
- No folder session restore (`view/restore_folder_position`), recent folders, favorites.
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
| Commands that run something | 12 of 53 | Settings, the three themes (Slate, Light, Midnight), sort by name and by AI score, Clear Filters, Compare, Reset Layout, Mark Winner, Reject, Move To _keep (this one only sets the Keep decision). Every other command is listed, can be chosen and is remembered as recent, **and then does nothing** |
| On / Off tag | Compare only | The Widgets palette shows On or Off for ten toggles (zen, bursts, auto advance, performance logging ...); the shell has none of those states. In the demo data they are fixed |
| "Current view" tag | Not shown | needs the grid / details view state |
| Preview Commands and Zen Commands | Model only | the palette can show them (`UiState.paletteContext`), nothing opens it from the popout or zen mode: neither exists in the shell |
| Smart filter and saved search commands | One fixed sample ("Sharp keepers") | the Widgets palette adds one per built-in and saved filter preset |
| Opening the palette while another overlay is up | Not handled | Ctrl K over the settings window opens it on top of it |

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
| Preview preload | Yes | No | no popout yet |
| Editor browsing, editor on demand, starts open, prestart, Camera Raw first | Yes | No | no PhotoCraft host in the shell. The "starts open" row is greyed when "on demand" is off, as in the dialog |
| Advance after Accept or Reject | Yes | No | |
| Group burst sequences, Stack similar burst frames | Yes | No | |
| Show hidden folders, one branch expanded, watch folder, reopen where I left off | Yes | No | the tree and watcher do not exist in the shell |
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
- "Open the settings guide" does nothing; the version line shows `image_triage._version` (and a demo string in `--mock` mode), not PhotoCraft's version.
- Keyboard: Tab order is the declaration order with no focus ring styling beyond Qt's default; arrow keys do not move through the section list.
- The Widgets dialog remembers its size and position (`dialogs/geometry`); the overlay always opens centred.
- The Widgets "Reset" semantics per section differ: here Reset to defaults resets the visible section to the **documented defaults** and marks the rows unsaved.

## Tooling that is not part of the product
- `--mock`, `--set`, `--ui`, `--click`, `--hover`, `--grab` are test hooks of `python -m image_triage.qml_shell`.
- `scripts/qml_parity/` renders the HTML mockup for pixel comparison; `scripts/qml_parity.py` overlays two screenshots.
