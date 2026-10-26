# Briefing: Camera Raw–first browsing for Image Triage + PhotoCraft

Written 2026-10-09 for a second opinion. Facts are marked **verified** (I ran or read it), **estimate**
(my judgement, not measured) or **not tested**. The project owner uses the GitHub handle `tylermcm`.

## 1. Goal

Image Triage is a fast photo-culling app (PySide6/Qt, Windows) built to replace IrfanView; speed is its
whole identity. The owner wants this workflow:

1. Scroll through photos quickly with the filmstrip, each shown **inside a Camera Raw-style editor**
   (not the full editor), and make adjustments there.
2. From there, open the photo in the **full editor** (PhotoCraft), edit, save.
3. Closing the file returns to the **same photo in Camera Raw with the filmstrip**, ready for the next.

### Non-negotiable constraints (from the owner, verbatim in spirit)
* Speed first. **Target < 100 ms from navigation input to the photo on screen; hard ceiling 200 ms.**
  UI thread never blocks. Any design that sacrifices this is rejected outright.
* No buttons / manual steps to load or save anything; no mode switch.
* RAW editing must still work.
* Preview-then-refine. No full RAW decode per view (memory + time).
* Edited photos show their edits immediately from a cached render, inside the same budget.
* Edits autostash; nothing requires a manual save.
* Background decode / preload / cache are fine if they never delay navigation; cancel stale work at once.
* The trigger for loading the full editable RAW must not be a button or mode. (I chose dwell + click.)
* The owner wants **both** "Camera Raw image area only" (default) and "whole editor" variants as a setting,
  so the choice can be kept or collapsed later without rework.

## 2. Repos and branches

| Repo | Path (the owner's PC) | Branch | State |
|---|---|---|---|
| Image Triage | `C:\Users\developer\Documents\ImageTriage` (main checkout, branch `main`) | the feature is on `remove-builtin-editor` + my branch `camera-raw-first-nav` | **uncommitted**, in a git worktree inside the Claude session's temp folder |
| PhotoCraft fork | `C:\Users\developer\Documents\photocraft` | `codex/image-triage-integration` (tracks `origin` = `tylermcm/photocraft`) | **uncommitted** |

* `remove-builtin-editor` (Image Triage, `a4caa3a` when I started) removed the built-in editor; PhotoCraft
  (a Rust/egui Photoshop-like editor, workspace of `photocraft-*` crates, MIT/Apache) is now embedded in
  the fullscreen popout as a child window.
* The photocraft clone has **no upstream remote** (storytold/photocraft); only the fork. I could not check
  upstream this session (AGENTS.md in both repos asks for that each session).
* Both repos' AGENTS.md: never push to storytold, never force-push, pushes to the fork only on request,
  record merges in `docs/photocraft_handoff.md`. Rust code must not panic (clippy-enforced), needs tests.
* **My changes are saved as patches** in this folder:
  `image_triage_camera_raw_first_nav.patch` (apply on `a4caa3a`) and
  `photocraft_stash_display_viewport.patch` (apply on fork `ae56dc1`). Nothing is committed or pushed.
* A rollback PhotoCraft binary exists: `photocraft\target\release\photocraft-known-good-ae56dc1.exe`.

## 3. How it worked before (verified by reading the code)

* PhotoCraft is a separate process, embedded as a native child window (HWND) in the popout and driven over
  a TCP/JSON control channel (`docs/control-protocol.md` in photocraft). `image_triage/preview_controller.py`
  (~1,400 lines) owns the lifecycle; `photocraft_bridge.py` the process/IPC.
* **PhotoCraft was the only thing painting the photo.** `FullScreenPreview` turned off its own decode,
  cache and neighbour preloading whenever PhotoCraft was in use (`_request_preview_loads` and
  `preload_paths` returned early). Every navigation = `app.open` over IPC + GPU document swap, with the
  host disabled and an "Opening PhotoCraft…" cover.
* Previous session's two-stage flow: open a cached screen-size JPEG in PhotoCraft first (30–90 ms warm
  local), then the RAW after a 500 ms dwell. For 45 MP Nikon NEFs on a NAS (~107 MB/s) a swap took 3 s+.
* **Edited photos were the slow case:** if a sidecar existed there was no preview stage; the 420–490 MB
  `.pcraft` was opened directly.
* **Autostash already existed and is sound** (kept as is): 1.5 s timer polls the document revision;
  `app.stash` runs async (`wait:false`); pending/failed stashes tracked, retried on return, restored from
  memory; navigation/close/shutdown stash first. It writes the `.pcraft` project and a **full-size**
  `*.photocraft.png` render (~240 MB at 45 MP) used for grid thumbnails.
* Nothing measured input-to-pixels (`preview.navigation` timed controller work only).
* Known open issues from the previous session (not mine): RAWs look flatter than the camera JPEG /
  Photoshop (PhotoCraft's develop has no camera profile / tone curve; a clean-room profile mechanism exists
  in `crates/raw/src/cameras.rs` but only for the Nikon D4); saves write ~730 MB per edited 45 MP photo and
  take 4–23 s; edits in the first 500 ms on a preview were lost.
* Target cameras: Nikon Z7 / Z7 II NEF. Compressed NEFs go through an Image Triage adapter (rawpy →
  undeveloped sensor DNG) because PhotoCraft's decoder couldn't unpack them; upstream has since added a
  compressed-NEF decoder (#1310), not yet adopted. The owner has **no RAW files on this PC**.

## 4. What I built (all **verified** as stated)

### Image Triage (`camera-raw-first-nav`)
* **Native layer** `image_triage/ui/native_image_layer.py`: a native child window stacked above PhotoCraft's
  HWND that paints the photo itself (grid thumbnail instantly → screen-size decode / cached edited render).
  `FullScreenPreview` (preview.py) re-enabled its decode/cache/preload while PhotoCraft is in use and feeds
  the layer from `_render_pane`. Released ~150 ms after PhotoCraft reports the same photo open.
* **Setting** "Editor browsing" (Settings ▸ Interface): `canvas` (default; layer covers only PhotoCraft's
  image area via `ui.viewport`, falling back to whole window until known), `full`, `off` (old behaviour,
  baseline). One mechanism, differing only in the rectangle.
* **Trigger for the editable document:** 300 ms dwell after the selection rests, or immediately on a click
  on the picture; first photo of a folder starts the editor at once. No preview document is opened in
  PhotoCraft while browsing. Moving on restarts the dwell and cancels a running `app.open`.
* **Edited photos:** `photocraft_bridge.saved_render_path()` prefers a small display JPEG over the PNG
  (ignored if >2 min older than the PNG; empty files ignored).
* **Timing:** `nav_timing.py` (stages first_pixel / preview / refined from the real key press; stamped in
  the layer's `paintEvent`; `IMAGE_TRIAGE_NAV_TIMING=file.jsonl`; `python -m image_triage.nav_timing`),
  `scripts/measure_navigation.py` (generates a test set, steps at a chosen rate, p50/p95/max).
* **Speed fixes found by profiling:** (a) `PreviewPane._apply_style` re-applied identical stylesheets
  (~19 `setStyleSheet` calls per navigation, ~25 ms): guard added, `show_entries` 32 ms → 6 ms median;
  (b) preload was *debounced* 120 ms so it never ran while stepping faster than that → now throttled and
  biased two-ahead/one-behind in the direction of travel (`preload_order.py`); (c) queued decodes for photos
  already passed drop themselves (`PreviewTask.is_current`).
* Docs: `docs/camera_raw_first_browsing.md` (design, measurements, phases, risks) and an entry in
  `docs/photocraft_handoff.md`.

### PhotoCraft fork
* `app.stash` takes optional `display` (`.jpg`): `photocraft_io::export_display_jpeg` builds a 2560 px
  untagged-sRGB JPEG from the compositor's reduced render, in parallel with the project encode, written as
  soon as ready; failure only warns. Real editor output checked: right size, RGB, no ICC.
* New read-only `ui.viewport`: window, canvas rect and (when open) Camera Raw `view`/`preview` rects, in
  physical pixels. Documented in `docs/control-protocol.md`.

### Measured (isolated popout, offscreen Qt, 60 generated 6000×4000 JPEGs, 8-core PC, warm cache, one run each, ±~10 ms)
| Input rate | first_pixel p50 / p95 | screen-sized picture p50 / p95 |
|---|---|---|
| 250 ms | 7.9 / 10.4 ms | 7.9 / 10.4 (58/60 photos) |
| 100 ms | 9.5 / 16.4 | 9.6 / 16.5 (51/60) |
| 33 ms (held key) | 7.5 / 9.0 | rarely (cold 24 MP decode ≈160 ms); thumbnail at 8 ms, sharp on release |

First navigation after the popout opens: 150–225 ms (window opening cold).

### Verification
* 51 new Python tests + related existing files: 211 passed; the 5 failures in `test_preview_right_click.py`
  fail identically on the untouched branch (10 environment-related baseline failures in the full suite).
  The whole 1,700-test suite was **not** re-run after the final changes (the owner asked for targeted runs).
* Rust: 6 new `photocraft-io` tests, 11 `ui-egui` tests; clippy clean for touched crates.
* `scripts/check_photocraft_handoff.py` against the rebuilt release binary: **passed** (hidden start, embed,
  switch/return in same process, native + menu Save, closed-tab reopen, 5 themes, cleanup, originals
  unchanged). I changed two assertions that encoded the old flow. Desktop-capture check reported false
  (not investigated).

### Not tested / known risks
* Real RAW files, a real display by eye, the NAS, the `off` baseline through the real editor, the `refined`
  stage timing in the real app.
* The layer is a separate window over a foreign one: possible blank/half-drawn frame at the swap;
  `NATIVE_LAYER_RELEASE_MS = 150` is a guess (proper fix: a "frame presented for revision N" signal).
* Image Triage fits the picture itself; PhotoCraft's Fit on Screen margins may differ → small size jump.
* While a photo is loading, clicks on PhotoCraft's own panels land on a disabled window: they neither
  trigger the load nor are replayed (only a click on the picture escalates).
* Display JPEG size/quality (2560 px, q90) untuned.

## 5. What is NOT built: Camera Raw itself (the key open question)

Facts from reading `photocraft/crates/ui-egui/src/camera_raw_ui.rs` (1,367 lines) and neighbours:
* Camera Raw in PhotoCraft is a **modal dialog in its own OS window** (an egui viewport via
  `ctx.show_viewport_immediate`, 1360×900), dimming the main window. It exists only in the PhotoCraft process.
  The web build already draws the same dialog over the main window (`ctx.embed_viewports()` path).
* Two different RAW tools: the "RAW development" dialog (`raw_develop_ui.rs`: sensor exposure / white
  balance / demosaic; for RAW-backed smart objects) and the **Camera Raw filter** (full panels; a smart
  filter on developed pixels). The PS-like panels are the filter.
* The dialog is tied to `PhotocraftApp` state (session, document, engine commands, preview rendering).
* The control channel already has `ui.rawDevelopment {open, commit}`; there is no equivalent for the filter.

## 6. Proposal for Camera Raw — two options (please weigh in)

**Option 1 — dedicated Camera Raw program (my lean).** Add `apps/photocraft-camera-raw` to the PhotoCraft
workspace: a minimal app that links the *same crates* (no copy-paste, so upstream merges keep working;
license is MIT/Apache and it's the owner's fork), opens one document and shows the Camera Raw dialog filling the
whole window using the embed-viewport path. No menus/panels/tools → should start faster. Image Triage embeds
it in the popout next to the filmstrip as one persistent process (as it does PhotoCraft now). Flow: browse
and adjust in Camera Raw, settings stash to the same `.pcraft` sidecar; "Open in full editor" saves and opens
the photo in the full PhotoCraft process; closing it returns to Camera Raw on the same photo. Mirrors
Photoshop's Bridge ▸ Camera Raw ▸ Open Image.
* Costs (**estimates**): two processes (two GPU contexts, more memory); coupling to `PhotocraftApp` means
  the thin app must still boot enough of it; Camera Raw settings must survive the handoff via the saved
  file (**unchecked**); two embedded windows to manage in the popout.

**Option 2 — host the dialog in the existing PhotoCraft process.** Add a host-controlled flag that draws the
dialog inside the main/embedded window (the web path), plus `ui.cameraRaw {open, commit}` commands, auto-open
after a document opens, commit before switch/stash/close. Less code, one process. But it edits
`camera_raw_ui.rs`, where upstream is most active (merge friction), and keeps Camera Raw and the full editor
in one process (the round trip is then a mode inside one app, not a handoff).

**Suggested first step:** a spike — a binary that opens one JPEG straight into a full-window Camera Raw —
to learn how deep the coupling goes before choosing.

## 7. Questions for the reviewer
1. Option 1 vs 2? Any reason the dedicated program would be much harder than I think (state coupling,
   GPU/texture ownership, startup cost)?
2. Is the native-layer-over-foreign-HWND approach sound on Windows (z-order with DX12 swapchains, DPI,
   flicker at the swap), or should the swap be signalled from PhotoCraft ("frame presented for revision N")?
3. Camera Raw on a RAW smart object: the filter works on developed pixels and the RAW development dialog on
   sensor data. For a browse-and-adjust workflow, which should be the "Camera Raw" the user sees? Should
   they be unified (Photoshop's ACR is one dialog)?
4. Can the cached edited render be improved (e.g. produce the display JPEG without a second composite, or
   from the same pass as the PNG)? Should the 240 MB PNG stop being written?
5. The Z7 colour-profile work (clean-room fit from embedded JPEGs; `dng.rs` ignores tone curves so the
   adapter path wouldn't pick up a `cameras.rs` profile) is deferred. Native NEF decoder vs adapter?
6. Any concerns about the dwell (300 ms) + click-to-escalate trigger, given clicks on the editor's panels
   during load are dropped?

## 8. Where things are
* Design / measurements: `docs/camera_raw_first_browsing.md` (in the Image Triage branch / patch).
* Previous-session handoff: `docs/photocraft_handoff.md` and `docs/photocraft_raw_plan.md` on
  `remove-builtin-editor`.
* Key files — Image Triage: `image_triage/preview.py`, `preview_controller.py`, `photocraft_bridge.py`,
  `ui/native_image_layer.py`, `nav_timing.py`, `preload_order.py`, `scripts/measure_navigation.py`,
  `scripts/check_photocraft_handoff.py`, `tests/test_native_first_browsing.py`.
  PhotoCraft: `crates/ui-egui/src/camera_raw_ui.rs`, `raw_develop_ui.rs`, `control.rs`, `control_files.rs`,
  `crates/io/src/flat.rs`, `crates/raw/src/cameras.rs`.
* Python env used for tests: a venv in the Claude scratchpad (`pip install -e .[dev]`); Rust 1.99 via rustup.

## 9. Update: the `--raw-workspace` spike (built and run)

Decision after review: no second program; a **launch mode of the same binary**, `photocraft --raw-workspace`.
Built in the fork (`crates/ui-egui/src/raw_workspace.rs`, plus a 1-line hook in `camera_raw_ui::show`, the flag
in `apps/photocraft/src/main.rs`, and a frame hook in `lib.rs`):
* Camera Raw opens by itself on the active document (once per document) and is drawn **inside the main
  window** via the same path the web build uses (`ctx.embed_viewports()`), instead of its own OS window.
* OK/Cancel closes the dialog; the ordinary editor is then on the same still-open document and Camera Raw
  does not reopen for it. A document Camera Raw can't open on is left to the full editor with the reason in
  the status bar.
* Verified: 4 new unit tests + the existing 16 Camera Raw tests pass; clippy clean for touched files;
  `scripts/check_raw_workspace.py` launches the real release binary, sees Camera Raw open by itself with a
  1004×578 view in a 1366×720 window, sets exposure, commits, and finds exactly 1 document open in the full
  editor and no reopen. Screenshots (the editor's own renders): `spike-1-camera-raw-open.png`,
  `spike-3-full-editor.png` in this folder.
* **Not done:** (a) `ui.cameraRaw {open, commit}` control commands and Image Triage launching with the flag
  (it currently passes `--hosted` only; `--raw-workspace` should work together but is untested hosted);
  (b) Camera Raw staying open across photo switches (today a switch is `app.open replace`, and the dialog
  opens once per document); (c) a "present the photo while Camera Raw loads" check — Camera Raw opened
  ~50 ms after the document did, but the document itself took ~14 s to appear in this standalone run
  (4.2 s to control-ready), cause not investigated (standalone start waits ~2 s for monitor profiles; first
  GPU frames; untested whether hosted/warm is much faster); (d) Camera Raw's RAW-smart-object path
  (`Target::OpenRaw` vs `NewFilter`) — the spike used a PNG; (e) wasm build not re-checked.
* Open design question this raises: for a RAW smart object, which dialog opens first — the sensor "RAW
  development" dialog or the Camera Raw filter? The spike used the filter (`Target::NewFilter`).

## 10. Update: next steps done (hosted Camera Raw workspace, end to end)

Built on the spike: `ui.cameraRaw {open, commit, cancel}`; `app.open`/`app.stash` settle an open dialog first
(changed -> one history step; untouched -> just closes, no edit/dirty/re-save); the workspace reopens Camera
Raw per document (including a photo restored from memory with the same id). Image Triage: setting "Open
photos in Camera Raw first (experimental)" (off by default) launches the editor with `--raw-workspace`;
commits Camera Raw before the revision is read and the photo saved; autosave leaves an open dialog alone;
when the full editor's document is closed the same photo is shown again in Camera Raw; the browsing picture
stays until Camera Raw has laid out its view.
Verified: `scripts/check_camera_raw_first.py` against the real editor (hosted, embedded) passes the whole
loop on synthetic photos (adjust -> next photo keeps it (red 64 -> 125) -> back -> OK = full editor, 1 doc,
no reopen -> close doc -> back in Camera Raw). Rust: 1,058 ui-egui lib tests pass (11 GPU-adapter tests crash
natively on this PC and were skipped; unrelated canvas LUT tests); 6 io tests; clippy clean for touched files.
Python: 237 targeted tests pass, the same 5 pre-existing right-click failures remain.
Numbers: photo -> Camera Raw open ~1.1-1.3 s per switch; first pixel p50 82 ms (n=3, 199 ms for the first,
popout opening over a launching editor); editor cold start ~21 s on this PC (baseline too).
Open: real RAW files / smart-object path (and whether Camera Raw should be an editable smart filter rather
than baked), one persistent dialog instead of close/reopen per photo, saves of Camera Raw edits only on
switch/close/shutdown. Screenshot: `hosted-3-back-in-camera-raw.png` in this folder.


## 11. Update: recipe workflow on real NEFs, and where I stopped (2026-10-09)

**Decided with the owner:** Photoshop parity. A raw opens in **Camera Raw (the open-time dialog), not the Camera Raw filter**;
scroll the filmstrip; the settings are a **per-photo recipe** (like an XMP sidecar), not baked into a document; the full editor
document is separate; the latest render shows.

**Built (PhotoCraft fork, `codex/image-triage-integration`, uncommitted):**
* `app.open {cameraRaw: {recipe, recipePath, displayPath, continueFrom}}` in the workspace: native decode of the raw with the
  fast MHC demosaic (`import_raw_browse`), opened in Camera Raw seeded with the stored recipe. `ui.cameraRaw {done}` = leave the
  photo: recipe written (`*.cameraraw.json`) and a 2560 px render of it (`*.photocraft.display.jpg`) made in the background; **nothing
  developed or saved**. `commit` on a raw's dialog = **Open**: develop with the settings, or continue the photo's saved full-editor
  document (`continueFrom`) so earlier work is never replaced. `app.handoff` advertises `cameraRaw: 1`.
* New: `raw_recipe.rs` (recipe file + render), `raw_workspace.rs` (spec parsing, dialog queueing, claimed-document tracking so Camera
  Raw never reopens by itself on a document Open produced), `io::raw::import_raw_browse`, `io::display_jpeg_from_buffer`.
* Camera Raw's proxy build is parallel; the covered canvas is not drawn while the workspace dialog is up (see "open problem").
* Rust: 1,076 `photocraft-ui-egui` lib tests pass (11 GPU-adapter tests crash natively on this PC and are skipped), clippy clean.

**Built (Image Triage, branch `camera-raw-first-nav`, uncommitted):** recipe store (`photocraft_bridge.recipe_path/load_recipe`; a
damaged recipe is set aside as `.bad`), `app_open(camera_raw=...)` with fallback to the retained-RAW/adapter route when the editor can't
develop a file from sensor data, `_camera_raw_spec`, raws open from the raw (not the sidecar) in the workspace, `done` on every switch,
re-baselining after Open so developing is not mistaken for editing, thumbnails/edit-state follow the newest render. Python: 260 targeted
tests pass (the same 5 pre-existing right-click failures remain).

**Live check on real NEFs** (`scripts/check_camera_raw_recipe.py`; Z7 lossless, Z7 II, and the Z7 *uncompressed* file as the unsupported
case) passes end to end: Camera Raw on a raw from sensor data -> adjust -> next photo keeps the recipe + render, saves nothing -> back,
recipe restored -> Open develops (not mistaken for editing) -> edit in the full editor saves a document -> close returns to Camera Raw with
the recipe -> Open continues the saved document -> the unsupported file takes the retained-RAW route. Native decode works on 4 of the 5
samples (Z7 lossless + compressed, Z7 II x2); the Z7 uncompressed file is not supported natively (falls back).
Sample NEFs: raw.pixls.us (Z7 `3-...lossless`, `2-...compressed`, `1-...uncompressed`; Z7 II `Z72_0027/0028`), in the Claude scratchpad
`nefs/` folder, not in the repo.

**Measured on real 45 MP NEFs (this PC, Intel UHD 620 laptop GPU, release build):**
decode 1.1 s; develop bilinear 0.7 / MHC 1.05 / AHD (default) 3.1 s; `app.open` job 2.0-4.4 s with MHC; first pixel in the popout
p50 ~130 ms cold with no placeholder or prefetch (the 8256x5504 embedded JPEG decode); photo to Camera Raw 5-7 s.

**OPEN PROBLEM (important, not fixed):** after any 45 MP document opens, the editor's UI thread freezes ~10-12 s (every control call,
including `app.bind`, waits). The editor's own `perf` shows `compositeMs` ~12.4 s, `lastRefresh: gpu-full` of 45.7 MP: it is the
GPU canvas compositing the new 16-bit document on this weak integrated GPU. It happens with and without Camera Raw (plain `app.open`
too). My change skips drawing the canvas while the workspace dialog is up, and moves the auto-open before the canvas draw so the frame
the document lands in is covered, but **I did not get to re-measure** whether that removes the stall, and the stall will move to the moment
the user presses Open (the full editor shows the canvas). The earlier session measured ~130 ms canvas composites on the owner's own
machine for smart objects, so this may be specific to this PC. Next step: rebuild the release binary (the exe in `target/release` is
one source edit behind: it has the first covered-canvas change but not the auto_open move), run `stall_probe.py` (scratchpad) in
workspace mode, and see.

**Mistakes I made and fixed:** I deleted two tracked files (`crates/raw/examples/mutate.rs`, `rawinfo.rs`) while cleaning up a temporary
timing example, and restored them with `git checkout`; the working tree now shows only intended changes. I also killed a compile
mid-way, which corrupted that crate's incremental cache (linker error); I cleared it and the suite passes.

**Not done:** the release binary rebuild + the re-measure above; the recipe/Camera Raw flow for non-RAW photos is the old filter-on-pixels
path; no work on the Z7 colour profile (deferred by the owner); nothing committed or pushed.


## 12. Speed work, round 2 (2026-10-10), on real Nikon Z7 / Z7 II NEFs

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
