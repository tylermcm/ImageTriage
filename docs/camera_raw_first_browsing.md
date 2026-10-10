# Camera Raw first browsing

Status: phase 1 (the fast browsing layer) is implemented on branch `camera-raw-first-nav`, unit-tested,
timed in isolation and run through the live editor check; it has **not** been exercised against real RAW
files, looked at on a real display, or timed on the owner's machine.
Phases 2 and 3 are specified here and not built. See [Status](#status).

## The workflow

1. Move through a folder with the filmstrip or arrow keys. Each photo is on screen as fast as the
   keys repeat, and the editor's Camera Raw controls are there for it.
2. Make adjustments (autostash saves them; there is nothing to press).
3. Open the photo in the full editor, change it, save, close it, and land back on the same photo in
   Camera Raw with the filmstrip, ready for the next one.

## Constraints (not negotiable)

* Speed first. Each photo on screen in under **100 ms** from the navigation input; **200 ms** is the
  ceiling. The UI thread never blocks.
* No buttons or manual steps to load or save anything. No mode switch.
* RAW editing stays available.
* Preview-then-refine. Never a full RAW decode per view.
* A photo with edits shows its edits straight away, from a cached render, inside the same budget.
* Background decode, preloading and caching are fine as long as they never delay navigation, and
  stale work is cancelled the moment the user moves on.

## What the code did before this work

Read from `remove-builtin-editor` at `a4caa3a`.

* **PhotoCraft was the only thing that painted a photo in the popout.** `FullScreenPreview` switched
  off its own decode, cache and neighbour preloading whenever the editor was in use
  (`_request_preview_loads` and `preload_paths` returned early on `_uses_photocraft_editor()`).
  Every navigation was an `app.open` over IPC into another process, with the viewer disabled and
  an "Opening PhotoCraft…" cover until the document arrived.
* **Edited photos were the slow case.** `_photocraft_fast_lane_ready` is false when a sidecar
  exists, so there was no preview stage: the 420-490 MB `.pcraft` was opened directly.
* **Autostash was already there and sound**, and is kept as it is: a 1.5 s timer polls the
  document revision; `app.stash` runs asynchronously (`wait:false`); pending and failed stashes are
  tracked, retried on return, and restored from memory; navigation, close and shutdown stash first.
  It writes the project, and a full-size `*.photocraft.png` render used for grid thumbnails.
* **Nothing measured input-to-pixels.** `preview.navigation` timed controller work only.

## Design

### Layers

```
 key / filmstrip click
        │
        ▼
 L0  popout paints the photo itself (NativeImageLayer, a native window above PhotoCraft's)
        │    grid thumbnail at once  →  screen-sized decode / cached edited render
        ▼
 L1  PhotoCraft opens the editable document behind it (after a dwell, or at once on a click)
        │
        ▼
     layer released ~150 ms after the editor reports the same photo is open
```

L0 reuses what already existed and had been switched off: the popout's decode pool, its 320 MB
preview cache, `_prime_entry_images_from_cache` (instant on a hit), placeholder seeding from the
grid thumbnail, neighbour preloading, and token-based discarding of stale results. Nothing in L0
talks to PhotoCraft. While a photo is browsed PhotoCraft is not asked to open a preview document
either; its current document just stays where it is, behind the picture.

### Setting: `Editor browsing` (Settings ▸ Interface ▸ Navigation and preview)

| Value | Layer covers | Notes |
|---|---|---|
| `canvas` (default) | PhotoCraft's image area only (from `ui.viewport`), so its panels stay visible | falls back to `full` until the rectangle is known, or if an older PhotoCraft lacks `ui.viewport` |
| `full` | the whole embedded editor | panels appear once the photo settles |
| `off` | nothing | the previous behaviour: PhotoCraft draws every photo; the baseline for measurement |

One mechanism, one rectangle: `canvas` and `full` differ only in the geometry handed to the layer, so
the choice can be kept as a setting or collapsed to a permanent default later without rework.
`off` exists as a rollback and as the measurement baseline.

### Triggering the editable document

Dwell, plus an immediate escalation: `PHOTOCRAFT_NATIVE_DWELL_MS = 300` after the selection rests,
or at once when the layer is clicked. No button, no mode.

* Dwell, because while the user is swiping a RAW load is wasted work (and 45 MP unpack is heavy);
  300 ms is below the point where a person who has stopped notices the editor is not ready.
  It is a starting value to tune from measurement.
* Escalate on click, because a click on the picture means "I want to work on this one now".
* The very first photo of a folder starts the editor immediately (it has to launch anyway).
* Moving on restarts the dwell and cancels an `app.open` that is already running (never a save).
* Known limit: a click on the editor's own panels while the photo is still loading lands on a
  disabled window, so it cannot trigger the load and its value is not replayed. With `canvas` the
  panels are visible for at most the dwell plus the load; this is the main thing to judge by hand.

### Edited photos

Autostash now also asks PhotoCraft for a screen-sized (2560 px) sRGB JPEG of the same snapshot
(`app.stash {display}`). It is built from the compositor's reduced render beside the project encode and
written as soon as it exists, so a quick return to a just-edited photo has it before the project lands.
`saved_render_path()` prefers it over the full-size PNG (hundreds of MB at 45 MP) and ignores it if it
predates the PNG by more than two minutes. Edits stashed by older builds still fall back to the PNG,
which is slow to decode: they get a display render the next time they are saved.

### Cancellation and ordering

All existing generation checks stay. New: the popout's own results are dropped by its load token;
obsolete PhotoCraft opens are cancelled on the fast-lane connection; the layer is released only for
the photo it is showing (`photocraft_document_ready(path)`), and a new navigation cancels a pending
release.

## Measuring

`nav_timing.py` records three stages per navigation in ms since the input: `first_pixel` (anything of
the photo painted), `preview` (screen-sized picture), `refined` (editable document open). The clock
starts at the key press (the controller passes the real input time through), and the pixel stages are
stamped in the layer's `paintEvent`, not when work is requested.

* Real use: `IMAGE_TRIAGE_NAV_TIMING=C:\path\nav.jsonl`, then
  `python -m image_triage.nav_timing C:\path\nav.jsonl`.
* Isolated popout: `scripts/measure_navigation.py` (generates a test set, steps through it at a
  chosen rate, prints p50 / p95 / max and misses against 100 / 200 ms).
* Baseline: the same runs with `Editor browsing = off`, compared to `canvas` and `full`.

### Test set

* Generated JPEGs (`--make-set`, 6000x4000, distinct content each) as the decode-cost control.
* The four CC0 Nikon Z7 / Z7 II NEFs from raw.pixls.us (listed with SHA-256 in the project's earlier
  validation notes) for real embedded-JPEG extraction, replicated into a few hundred distinctly named
  files. They are not on this machine; fetching them needs the owner's go-ahead.
* ~50 photos with real edits made through the control channel, for the cached-edited-render case.
* Scenarios: single step, held key (~33 ms), stepping (~250 ms), filmstrip drag, return to an edited
  photo, leave and return during a pending stash. Cold file cache per run.
* NAS: cannot be reproduced here. For it, record bytes read per navigation; the browsing path reads
  only the embedded JPEG, not the RAW.

## Measured

Isolated popout, `scripts/measure_navigation.py`, offscreen Qt, 60 generated 6000x4000 JPEGs, an 8-core
PC, warm file cache, one run each (noise between runs is roughly +-10 ms). Milliseconds from the
navigation input to the paint.

| Input rate | Preload | first_pixel p50 / p95 | screen-sized picture p50 / p95 (photos reached) |
|---|---|---|---|
| 250 ms (stepping) | 10 | 7.9 / 10.4 | 7.9 / 10.4 (58 of 60) |
| 100 ms | 10 | 9.5 / 16.4 | 9.6 / 16.5 (51 of 60) |
| 33 ms (held key) | 10 | 7.5 / 9.0 | rarely: a cold 24 MP decode is ~160 ms, so the sharp picture lands when the key is released; the grid thumbnail is up at 8 ms |

The one outlier in every run (150-225 ms) is the very first navigation, which opens the popout window.

Three things were needed to get there, none of them the layer itself:

* `PreviewPane._apply_style` re-applied identical style sheets (~19 `setStyleSheet` calls per
  navigation); Qt re-polishes on each. Skipping unchanged sheets took `show_entries` from a 32 ms median
  to 6 ms. This helps every mode, including `off`.
* Preloading was debounced, so it never ran while the user was stepping faster than 120 ms; it is now
  throttled and biased two-ahead-one-behind in the direction of travel (`preload_order.py`). Before
  this, 1 of 60 sharp pictures arrived during a 100 ms swipe; after, 51.
* Queued decodes now drop themselves if the user has already moved on, so a worker is never busy with a
  photo that is gone.

Not measured: real RAW files (embedded-JPEG extraction), the editor catching up (`refined`) in the real
app, a real display, a NAS, or the baseline `off` mode through the real editor. The live check's
`ready_ms` (~0.9-1.0 s for 1 MP synthetic photos including the 300 ms dwell) is dominated by the editor and
is not a browsing number: `selection_ms` (7-15 ms) is the popout's side.

## Verification

* Image Triage: 51 new tests (`test_nav_timing`, `test_native_first_browsing`,
  `test_navigation_speed_pieces`) plus the existing preview, PhotoCraft handoff and settings test
  files (211 passed in total; the 5 failures in `test_preview_right_click.py` fail identically on the
  untouched branch). The whole suite was not re-run after the final changes.
* PhotoCraft fork: `photocraft-io` display JPEG tests (6), `control_files` and `ui.viewport` tests (11),
  clippy clean for the touched crates (warnings only in `photocraft-gpu`, not touched).
* `scripts/check_photocraft_handoff.py` against the rebuilt release binary: passed (hidden start, embed,
  switch and return in the same process, native Save and menu Save, closed-tab reopen, five themes,
  shutdown and process cleanup, originals unchanged). Two of its assertions encoded the old flow and were
  adapted: the filmstrip is now visible from the start, and a reopen after closing a tab waits for the
  dwell. Desktop capture verification reported false (not investigated).
* The display JPEG written by the real editor is the photo's size, RGB, no embedded profile.

## Status

| Piece | State |
|---|---|
| `NavigationTimer`, JSONL sink, summary CLI | done, unit-tested |
| Native layer (L0), settings, dwell/escalation, cancellation | done; unit-tested offscreen; live check passes; **not seen by eye** |
| Faster browsing (style guard, throttled directional preload, stale-decode skip) | done, measured above |
| Display JPEG in `app.stash`; `ui.viewport` | done in the PhotoCraft fork, tested, verified with the real editor |
| `scripts/measure_navigation.py` | done |
| Camera Raw workspace (`--raw-workspace`, `ui.cameraRaw`, settle on switch, return after close) | built, unit-tested, live-checked on synthetic photos; setting off by default |
| Real RAW files, real display, NAS | not tested |

## Camera Raw workspace (built; not yet used on real RAWs)

`photocraft --raw-workspace` (a mode of the one editor, not a second program). Image Triage launches the
editor with it when **Settings > Interface > Camera Raw > "Open photos in Camera Raw first (experimental)"**
is on (default off until it has been used on real RAW files; applies the next time the editor starts).

* Camera Raw opens by itself on each document, drawn inside the main window (the web build's embedded
  path) instead of in an OS window of its own. OK or Cancel leaves the ordinary editor on the same,
  still-open document; it is not reopened for that document. That is the step from Camera Raw to the full
  editor: nothing is saved, reopened or decoded again.
* `ui.cameraRaw {open?, commit?, cancel?}`. `app.open` and `app.stash` also settle an open dialog first
  (changed settings become one history step; an untouched dialog just closes, so browsing past a photo
  never edits, dirties or re-saves it).
* Moving to another photo: the controller commits Camera Raw before reading the revision and saving, then
  opens the next photo; the next document gets Camera Raw (also when a photo is restored from memory with
  the same document id, because the app notices the empty frame in between). Autosave never closes a dialog
  being worked in, so adjustments are saved on switch/close/shutdown, not by the 1.5 s timer.
* Closing the full editor's document: the controller sees the document gone on its next poll (up to 1.5 s)
  and shows the same photo again in Camera Raw (browsing picture first, then the editor).
* The browsing picture stays up until Camera Raw has laid out its view (bounded to 1.5 s), and with
  `Editor browsing = canvas` it covers exactly Camera Raw's view rectangle (`ui.viewport.cameraRaw.view`).

What it does with a RAW is untested: the spike and live checks used PNG/JPEG photos, where Camera Raw opens
as the *filter* on the pixel layer (commit bakes the adjustment into the document, one history step). A RAW
smart object has the sensor "RAW development" dialog as well; which should come first is an open design
question, as is keeping the adjustment editable (a smart filter) instead of baked.

### Live check (real editor, this PC, synthetic 1200x800 photos): `scripts/check_camera_raw_first.py`

Passed: a photo opens in Camera Raw inside the embedded window; adjusting it and moving on keeps the
adjustment (saved render brightened, red 64 -> 125) and the next photo is in Camera Raw; returning restores
it; OK leaves 1 open document and Camera Raw does not reopen; closing the document returns to Camera Raw.
Timings from that run: photo to Camera Raw open 1.3 s (second photo) and 1.1 s (return); the **first** photo
took 21 s because the editor's cold start on this PC takes ~21 s (the unmodified check shows 20.7 s too; not
caused by the workspace). First pixel from the popout: 82 ms p50, 199 ms for the very first (popout opening
over a launching editor), n=3.

## Remaining

* Used on real RAW files, by eye, and over the NAS.
* Camera Raw staying open across a switch as one persistent dialog (today: closed and reopened per photo,
  ~1 s each way, with the browsing picture covering it).
* Editable (non-baked) adjustments, and the RAW smart-object path.
* Camera Raw's changes are only saved on switch/close/shutdown (autosave leaves an open dialog alone).
* Z7 colour profile (deferred by the owner).

## Risks

* The native layer is a separate window stacked over a foreign one; a swap that shows a blank or
  half-drawn frame is possible. `NATIVE_LAYER_RELEASE_MS = 150` is a guess to tune. The proper fix is
  a "frame presented for revision N" signal from PhotoCraft.
* The picture is fitted by Image Triage; PhotoCraft's own Fit on Screen margins may differ, which would
  show as a small size jump at the swap.
* The layer takes pointer clicks; PhotoCraft's panels, while the photo is loading, do not.
* Display JPEG quality / size (2560 px, q90) are untuned.


## Speed work, round 2 (2026-10-10), on real Nikon Z7 / Z7 II NEFs

Measured on this PC (Intel UHD 620 laptop, release build, 45 MP files). All of it is uncommitted.

| What | Before | After |
|---|---|---|
| Editor UI freeze when a 45 MP raw opens in Camera Raw | 10-12 s | 0.4-0.8 s (the covered canvas is not drawn) |
| Full 45 MP canvas draw (what Open triggers) | 12-23 s (wgpu compositor) | 2.6 s (banded CPU path, chosen for large documents on non-discrete GPUs) |
| Photo to Camera Raw, not prepared ahead | 6.6-11.4 s | 4.5-5 s |
| Photo to Camera Raw, prepared ahead (`app.prefetch`) | n/a | **~1.0 s** (the editor's open job: 16 ms) |
| Editor cold start to the first photo in Camera Raw | ~19-23 s | ~8.5 s |
| Cold decode of the 45 MP embedded JPEG for the browsing picture | 160-185 ms | 130-160 ms (Pillow draft decode; entropy decoding is the floor) |

What changed: the canvas is not drawn while Camera Raw (or a raw only being browsed) has the window; large full refreshes use the CPU compositor on
integrated GPUs; the Camera Raw preview proxy is built in parallel; the recipe's background render runs on one thread, serialised, after a delay, and is
dropped if a newer one for the same photo is queued; after a photo lands in Camera Raw, the next raw in the direction of travel is developed in the
background (one slot, matched by size+mtime signature) so opening it is nearly instant.

Known limits: the first step *backwards* is not prepared (one slot, in the direction of travel); a 12-23 s GPU path remains for large documents on
discrete-less machines only if `PHOTOCRAFT_FULL_REFRESH=gpu`; Open still redevelops a raw whose exposure/white balance changed (2.5-5 s); the cold
sharp picture (130-160 ms) is above the 100 ms target unless the neighbour was preloaded or the grid thumbnail covers it (first pixel ~10-30 ms).


## Upstream merge of 2026-10-10 and the Nikon colour fit

The PhotoCraft fork was merged with upstream `7dc8bbd` (519 commits) and the open upstream PR
storytold/photocraft#2135 (colour for raws without a calibration, fitted to the camera's own JPEG) was applied on top.

What the host sees:

- **Menu commands are refused while Camera Raw is open** (upstream #1671: "menu command is unavailable while a modal
  dialog is open"), which in the Camera Raw workspace is nearly always. The host's fit-on-screen call therefore uses
  `engine.execute {command: "view.fitOnScreen"}` instead of `ui.menu.invoke`, and the live checks adjust the open dialog
  with `ui.cameraRaw {set: {...}}` (new in the fork) instead of `ui.menu.invoke filter.cameraRaw {ui: {set}}`.
- A control connection carries one request at a time. The controller keeps one worker per connection; a script polling
  on the second connection from its own thread must use a connection of its own (the live check now does). An unreadable
  reply now raises an error that quotes what the editor sent.
- Live check on the real NEFs after the merge: first photo in Camera Raw 7.6 s, the next one (prepared ahead) 0.93 s,
  return to the first 3.2 s, Open continues the saved document, the unsupported Z7 file takes the retained-RAW route.

Colour of Nikon files (PhotoCraft's `rawlook` example, mean CIE76 dE against the camera's own JPEG, neutral fallback to fitted):

| File | Result |
|---|---|
| Z7 II (two files) | fit used, 19.7 to 6.7 and 20.1 to 6.7; brightness and tone match the camera JPEG; colour errors remain (yellow blotches in sunlit grass, a blue patch in a sky corner, duller red roofs) |
| Z7 lossless | fit rejected by its own checks: the Z7 JPEG is about 4% tighter than the raw (in-camera distortion correction), lightness correlation 0.63 against the 0.7 gate; keeps the dark, flat neutral fallback |
| Z7 lossy-compressed | decodes to a black picture without an error (develop takes 0 ms): a decoder gap, probably the "lossy after split" case in upstream's roadmap (#50) |
| Z7 uncompressed | not decoded natively; takes the retained-RAW route with the rawpy adapter, as before |

The fit is made at the as-shot white balance whatever balance Camera Raw asks for. Fitted to a changed balance, a 3x3
matrix absorbs the change and the white balance control would do nothing on any NEF (measured: red/blue 0.934 against
0.941 as shot with a 1.5/0.65 multiplier; with the fix the ratio moves by more than 15%).

The proper fix for the Z7 and Z7 II is a measured camera profile like upstream's Nikon D4 one. The `z7_lossless` sample is a
ColorChecker Passport frame, which is what that needs.
