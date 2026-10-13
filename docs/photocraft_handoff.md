# PhotoCraft handoff

## Quick handoff - 2026-10-13, later (the editor key, two upstream raw problems, upstream tip merged)

Image Triage `main` at `3bca249` plus this note; fork `scratch/upstream-merge-2026-10-13` at `9e94b0c0` (local; the
integration branch has not been moved). Nothing pushed. Desktop only. **Another session (Codex) is editing this
working copy at the same time** (inspector wrapping, top-right sizing; its note is at the end of this file): commit only
your own files.

**The editor key** (real app, 45 MP raw, editor started in the background): 1.85 s to the editor on screen, measured
step by step: 0.30 s a dwell meant for a selection that may move on, 0.73 s the editor developing the raw, 0.65 s the
editor decoding the raw a second time on its own thread for the dialog's tone curve (upstream's `raw::tone_curve` call
in `open_raw`), 0.13 s asking for the theme. Now: the key skips the dwell; a raw on screen for 0.6 s with the editor
up but idle is developed ahead (`_rest_on_photo`, `app.prefetch`); the theme is asked once per editor; the window is
attached before the open is sent; in the fork the browsing worker hands the curve over with the dialog's proxy, and an
open waits for a prefetch of the same photo that is in flight. Result: 0.50 s when the photo had been on screen two
seconds, 0.71 s for another photo later, about 1.45 s when the key is pressed within the first second (the develop
itself got slower with upstream's new colour fit).

**A photo come back to opened in the full editor.** Leaving a photo (the key again, or closing the popout now that the
editor outlives it) settles it, which closes its Camera Raw dialog; "already open" then skipped the reopen. A left
photo (`PhotoCraftProcess.left`) is opened again. Random navigation in the real app: 2 of 30 steps wrong before, 0 of
120 after.

**Black pictures: upstream's Nikon decoder.** Lossy-compressed NEFs from Z bodies (maker note 0x0096 version
`0x44 0x40`: Z 7 14-bit, Z 7II 12-bit) decoded to black: their 257 curve points are `2^(bits-2)/256` codes apart, not
`2^bits/256`. One line in `crates/raw/src/nefc.rs` (fork commit `7c2d5716`). The owner wanted it sent upstream as a
PR; it was prepared (branch `fix/nef-lossy-0x40-curve` on the fork, one commit on upstream `99e11388`) and then not
opened: upstream PR storytold/photocraft#3238 (open, by another contributor) already makes the same one-line change,
with packed-NEF and crop fixes. When #3238 is merged and reaches us, drop `7c2d5716` for it. All 45,749,760 samples of each file equal LibRaw's
(`rawpy.raw_image`), also for the D850 lossy and Z 7 lossless files that already worked.

**Flat colour: upstream tip merged** (`99e11388`, 396 commits past 0.8.2, not a release; the owner's call, for #4598).
At 0.8.2 the colour fit was rejected for a Z 7 lossless NEF; at the tip it is fitted. Before the merge the fork's own
background full redraw was dropped for upstream's (#5001); the off-thread compositor build stays. Two conflicts
(`camera_raw_ui.rs`, `menus.rs`). Two semantic changes the merge did not flag:
- upstream names a loaded project after its file (#3571): two fork tests updated;
- upstream centres a window with no saved position (#4850) after the hosted set-up ran: the hosted editor flashed as a
  window of its own. The hosted block now comes after that line. The live handoff check caught it.
Fork delta after all this: see `git diff --shortstat refs/upstream-peek/main` in the fork.

**Checked.** Fork: rustfmt, clippy `-D warnings`; raw 171, io 573, engine 1,771, ui-egui library 2,753 passed, none
failed (upstream's two catalog failures are gone). Image Triage: 208 related tests; the full suite was last run before
the other session's edits. Live on the merged release editor: handoff 1 MP and 24 MP, camera-raw-first, camera-raw-recipe
on the two formerly black files plus the Z 9 High Efficiency file, lean save, two random-navigation runs.

**What was wrong on 2026-10-12.** The retained-raw route was dropped on the grounds that it covered nothing upstream
did not. It did: LibRaw read these lossy Z files correctly and supplied camera colour. Files were checked for
opening, not for coming out right.

**Open.**
- Colour of Z-body files is fitted but not close: mean dE about 7 against the camera JPEG (2 for the D850); oranges
  and reds lean yellow (the Z 7II sample's roof, the chart's orange patches). Upstream's fit; not touched.
- Closing the popout while the editor is busy (right after an edit to a 45 MP document) can still take up to ~2.5 s:
  moving the editor's window out waits for the editor's thread. 135 ms otherwise. Not closing the popout's native
  window at all would avoid the move; not tried.
- The app reloads the photo list once about 5 s after start (the background editor start creates the hidden edits
  folder); the list is empty for a moment.
- After a close the save can take ~11 s to reach the disk when an autosave was already running.
- Not run: the development restart; anything on the laptop; the full Image Triage suite with the other session's edits.

## Quick handoff - 2026-10-13 (closing the popout keeps the editor)

Image Triage `main`, local commits on top of `b7bcbdf`; the fork is unchanged (`ca2d3298`). Desktop only (`C:\Users\Checkout`); not
run on the laptop.

**What was wrong.** Two separate things, both found by experiment (a stand-in popout, then the real one):
- **Qt destroys the popout's native window when it closes**, and a child window goes with its parent whatever process owns it. The
  editor left inside lost its window, drew no more frames and so never answered a control request again: that was "nothing in 84 s".
  Hiding the popout (not closing it) does not do this, which is why a stand-in that only hid its window showed nothing wrong. An
  editor moved out first keeps its window and answers in ~110 ms.
- **The UI thread waited for the editor in a blocking wait.** The editor's window is a child of one of ours; closing it waits for
  its parent's thread. With that thread blocked the editor could not exit (not in 10 s; 0.6 s with the thread's message queue
  running), so every quit ran into the 3 s timeout and ended in `terminate()`. That was the ~3 s freeze.

**What changed** (`preview_controller.py`, `photocraft_bridge.py`):
- Closing the popout no longer quits the editor and waits for nothing. `prepare_photocraft_preview_close` parks the editor
  (`_park_photocraft`: `SetParent` into a hidden frameless window of its own, kept for the life of the app; not a child of the main
  window, whose native window Qt re-creates when its flags change). `handle_preview_closed` queues `_settle_closed_photocraft`, which
  saves what changed (the project first, then the full-size render). A save that fails there is reported in the status bar for 30 s
  and logged; the photo is still open in the parked editor, so nothing is lost. (The Cancel / Discard dialog is no longer shown when
  the popout closes; it still is when the app quits or restarts, where the save is waited for.) A parked editor is treated like one
  that was never in a window: the next open puts it in the popout before the photo renders.
- UI-thread waits on the editor's worker go through `_pump_until_done` (the message queue keeps running, user input stays queued):
  the close-time save, Discard, and `shutdown_photocraft` (app exit, development restart).
- `bridge.tie_to_this_process`: the editor is put in a Windows job object that ends it when Image Triage's process ends, however it
  ends (the editor now lives as long as the app; a crash or a kill runs no cleanup). Tested on a throwaway child: killed with its
  parent when tied, left running when not.

**Measured** (release editor `ca2d3298`, 45 MP Z 7II NEF unless noted, `check_lean_save.py`):
- Closing holds the UI thread 75-105 ms in the check's stand-in window and 410 ms in the real app, edited photo or not (was ~3 s,
  plus the save). A first version still waited for the project save: 2.1 s in the real app right after an edit to a developed raw,
  which is why the save moved to after the close.
- Close right after an edit: the project and the full-size render are on disk 4.4-5.8 s after the close (the render used to be left
  behind).
- Reopen the same photo: 0.53-0.59 s, same process (a cold start here is ~1.8-2.3 s; ~3.6-6 s on the laptop).
- Quit: 0.65-0.73 s, the editor exits by itself (was 3 s and a kill).
- Parked, the editor answers in ~110 ms, also with the main window minimized, and also while the UI thread is blocked.

**Checked.** Full suite 1,893 passed, none failed (the laptop session's test fixes are in). New tests in `test_lean_autosave.py` and
`test_photocraft_handoff.py` (parking before the native host closes, parking once, the render after the close, re-attaching a parked
editor, the pumping wait); breaking the park call or the re-attach condition fails four of them. Live, all passing: handoff at 1 MP
and 24 MP, camera-raw-first, camera-raw-recipe (two Z 7II NEFs and the Z 9 High Efficiency file), lean save both ways.
`check_lean_save.py` now checks the new behaviour (editor still up and answering after the close, the render written after it,
reopen without a new editor, quit under 2.5 s).

**Live checks and the owner's desktops.** `scripts/_desktop.py`: with `IMAGE_TRIAGE_CHECK_DESKTOP_OF=claude.exe` the popout-based
checks open on the virtual desktop holding that program, at the bottom of the window stack, without taking the keyboard (the owner
works on another desktop of the same PC). The two scripts that start the editor as its own window (`measure_stash_stall.py`,
`check_raw_workspace.py`) wait for that desktop to be the active one instead.

**The real app** (a throwaway driver: the real `MainWindow`, its popout and controllers, default settings, sandboxed app data, two
Z 7II NEFs): the editor started in the background is the one used throughout (same process); first E 1.7-1.8 s; Camera Raw, Open,
an edit; close 410 ms with the editor parked and its window intact; project and render written 5.8 s later; E after reopening the
popout 2.0 s (the saved 304 MB document is loaded again); closing the main window 1.1 s, the editor exits with code 0. Quitting with
the popout still open and an unsaved edit: 1.9 s (the project is saved first), exit code 0, no full-size render (as designed: it is
caught up when the photo is next left).

**Open.**
- Not run: the development restart. Not run on the laptop.
- The parked editor keeps the last photo open (a 45 MP document in memory) until the next open, a folder change or quit. Releasing
  it after the render was left out: in the Camera Raw workspace a closed document is reopened by the host.
- A full-size export of a photo whose render is behind still falls back to the 2,560 px JPEG without saying so (the window for it is
  now a few seconds after a close instead of "until the photo is next opened").
- From the section below, still open: `_entry_source_signature` on the UI thread; the 310 MB project.

## Quick handoff - 2026-10-12/13, later (the laptop: the "still open" items)

Worked through the previous section's **Open** list on the laptop (Intel UHD 620, 4 cores / 8 threads, 15.9 GB). Image Triage commits are
on top of `df54738` (merged into `main`); the fork's one commit is on `codex/image-triage-integration`. Nothing was sent to storytold.

**What changed**
- **Edit state is looked up once and remembered** (`cache.EditStateMemo`, `ThumbnailManager.edit_states`). The grid behind the popout
  asked the disk about every visible cell, twice, on every repaint; with the filmstrip that was ~55 file lookups per key press, 1.3 s of
  the UI thread's 2.2 s while flipping 22 photos. Now 17 `resolve_session_for_read` and 128 `saved_render_path` calls over the same
  presses (0.15 s). What is left comes from the popout itself (`preview.py` `_entry_source_signature`, ~3.5 ms a call on the card).
  The filmstrip also keeps its thumbs instead of rebuilding them (`Filmstrip._populate`, `FilmstripThumb.show_state`). Measured with a
  key every 80 ms through 45 MP NEFs, popout alone: 5 of 20 photos shown / 5.4 s of pauses before the filmstrip work, 19-20 shown / ~0.4 s after.
- **The editor's own output is kept** (`photocraft_editor_*.log` in the log folder, last five starts; `PhotoCraftProcess.exit_note()` in the
  handoff-failure log line).
- **The editor's control connection** drops after 30 s of quiet; the client now reconnects first (`IDLE_RECONNECT_S`). That was the "PhotoCraft
  could not open this photo ... WinError 10053" of 2026-10-11 (first E after five minutes idle).
- **Saves: the full-size PNG is no longer written on every autosave.** The fork's `app.stash` takes `preview` as optional
  (`stashWithoutPreview` in `app.handoff`, fork commit `ca2d3298`); the autosave leaves it out. Measured on a 45 MP Z 7II NEF with a zoom running
  during the save: the save takes ~9 s instead of 16-18 s, the window keeps answering (slowest answer 61-96 ms; the old save 125-312 ms
  with 4-14 slow answers); held to 4 logical processors 14-15 s with at most one slow answer instead of 27-28 s with 19-38. Writes
  310 MB per save instead of 520 MB. The PNG is still written, so hand-offs, resize/export and the edited version beside the original keep
  full size: when the photo is left (E, or a photo switch), and after 10 s without an edit (`RENDER_IDLE_S`, in `_autosave_photocraft`).
  `bridge.full_render_path` (grid's edited version, full-size export) takes the PNG only when it is not older than the display JPEG, else the
  JPEG; `bridge.render_is_behind` is the file-based test (project newer than render), so a render left behind in an earlier session is caught
  up the next time the photo is left.
- **Closing the popout or quitting does not write the PNG** (and cannot): an embedded editor under a *hidden popout* does not answer at all
  (measured: nothing in 84 s), while hiding only its host inside the popout (what E does) leaves it answering in ~150 ms. So "edit, then close
  the popout within 10 s" leaves the PNG behind (the JPEG is current; readers use it, so a full-size export of that photo is 2560 px until the
  photo is next opened and left). Fixing that needs a parking window for the editor; not done.
- **The live checks** (`check_camera_raw_first`, `check_camera_raw_recipe`, `check_photocraft_handoff`, new `check_lean_save`) run on a
  throwaway settings key (`scripts/_check_settings.py`), so one that dies no longer leaves settings pinned in the real profile. Their stand-in
  windows need a `thumbnail_manager` (a test guards it). `measure_stash_stall.py --lean`.
- Tests: the right-click test measured hidden bars; the three sandbox test modules skip when their (git-ignored) code is absent and the import
  check no longer counts them. Targeted suites pass; the architecture ratchet is unchanged by this work (no new private reaches).

**The editor on this laptop.** `target/release/photocraft.exe` is rebuilt from the fork (0.8.2 + `ca2d3298`, 11-17 min with `-j 4`);
`photocraft-baseline-823d9890.exe` (the build before the lean save) and `photocraft-known-good-2026-10-11-c6092f86.exe` are kept beside it.
Fork: formatted, 14 `control_files` tests pass. Live on the new editor: handoff check passes at 1 MP and in 8 runs at 24 MP (the run that ended with
the Python process gone on the other machine did not recur), camera-raw-first passes, camera-raw-recipe passes, `check_lean_save` passes both
ways (render written 19 s after a pause; closing right after an edit leaves it behind).

**Open** (the first item, and the render left behind by a quick close, were fixed on 2026-10-13: see the section above)
- Closing the popout holds the UI thread ~3 s even with nothing to save: `PhotoCraftProcess.shutdown` waits 3 s for the editor to exit and then
  terminates it. Quitting takes 0.4-0.6 s with the window shown or hidden on its own, so it is the embedded-under-a-closing-popout case; not looked into.
  Closing the popout also always quits the editor (the prestarted one too), so the next E after reopening the popout pays the cold start.
- The `jobs.list` timeout at the end of one `check_camera_raw_recipe` run (60 s, at shutdown) did not recur in four more runs.
- Not run: the ui-egui integration tests, the desktop app's tests, clippy, the layers and wasm checks (the editor was built and its `control_files` unit tests ran).
- Still on the UI thread: `_entry_source_signature` (3 lookups per key press, ~3.5 ms each on the card) and the popout's source-update poll.
- Format decisions about the 310 MB project (upstream's) are still the owner's.

## Quick handoff - 2026-10-12 (upstream 0.8.2, a smaller fork, Camera Raw first by default)

**Where things are.** Image Triage: everything is on `main` (the `camera-raw-first-nav` branch was fast-forwarded into
it). PhotoCraft fork: `codex/image-triage-integration`, on upstream release 0.8.2 (`d43db3e1`) plus the fork's own
commits; fork delta 24 files, +3,846 / -84. Both pushed to the owner's GitHub repositories. Details, numbers and what
was not run: the two "2026-10-12 update" sections below.

**Rules the owner set this day** (also in both AGENTS.md files):
- The fork carries only what hosting PhotoCraft in Image Triage needs. Where upstream has built the same thing, now or
  later, drop ours and take theirs.
- Nothing is ever sent to storytold: no pull requests, no pushes.
- Merge upstream at its release tags, not at every tip. (No release after 0.8.2 yet; main was 33 commits ahead of it
  and merged cleanly in a trial.)
- No AI attribution line on commits.

**What changed.**
- Upstream 0.8.2 merged (310 commits, ten conflicts). The raw develop pipeline and colour fit are upstream's; the
  reduced browse development was rewritten to call upstream's develop unchanged.
- The retained-raw smart object route is gone from both repositories (upstream decodes Nikon compressed NEFs).
  A raw the editor cannot develop (Z 8 / Z 9 High Efficiency) opens as its embedded preview.
- "Open photos in Camera Raw first" is on by default: Camera Raw, then the editor, settings kept per photo, as in
  Photoshop. It is the only mode with raw-level editing.
- The control connection is locked per request (the occasional empty reply). The hosted save encodes on a pool of
  its own (no stall with the editor held to 8 logical processors; unchanged at 4).
- The architecture ratchet was re-registered at the numbers the PhotoCraft work brought (main window 2,848 lines,
  110 methods, 343 attributes; `preview_controller` 164, `settings_controller` 347, `startup_controller` 27).

**This machine** (`C:\Users\Checkout`, 20 threads, 16 GB): Rust stable via rustup (`~/.cargo/bin`); run cargo with
`-j 6` to `-j 8` and one crate at a time (a fully parallel workspace test build froze it). The release editor is
`..\photocraft\target\release\photocraft.exe` (built from `823d9890`); `photocraft-known-good-8dfc5340.exe` beside
it is the build before the save pool. Sample NEFs (raw.pixls.us: D850, Z 7, Z 7II, Z 8, Z 9) are in
`C:\Users\Checkout\Pictures\nef-samples`. Python's site-packages has a `tests` package of its own (pyppeteer2) that
hides `tests.harness`: run the suite with the repository's `tests` registered first (see the later section).

**For the owner to try on the home PC and the laptop** (nothing here was run on the owner's photos or machines):
- Camera Raw first on real Z 7II NEFs from the NAS; browsing speed there.
- The save after an edit on the laptop (the 9-12 s stall was seen there): `scripts/measure_stash_stall.py`.
- A `.pcraft` saved earlier with a RAW-backed smart object: it should open, without its development settings.
- If the settings dialog was accepted on that machine before, "Open photos in Camera Raw first" is stored as off
  there and stays off until ticked.

**Open.**
- Each save of a 45 MP photo writes about 520 MB (310 MB project, 211 MB preview PNG): format decisions are the owner's.
- The save stall on four logical processors or fewer (upstream's encoders start a thread per core themselves).
- One handoff check run of five ended with the Python process gone and no traceback; not reproduced.
- Not run: the ui-egui integration tests, the desktop app's tests, the layers and wasm checks.
- Still on the UI thread while the editor runs: about 53 file-system probes per key press; the editor's stderr is discarded.
- Pre-existing test failures: `test_preview_right_click` (5 assertions); the three sandbox packages missing from the checkout.

## Quick handoff - 2026-10-11 (the editor is on demand)

The popout browses on its own; the editor opens only after **E** (setting `preview/editor_on_demand`, default on; the settings
dialog's "Open the editor only when I press E"). Measured with the real app and a fixed harness (precise key timing, a heartbeat
timer for UI-thread pauses): 20 presses 150 ms apart show 18-20 photos with 0.7-1.2 s of pauses (editor following every photo:
11 photos, 2.3 s), against 20 photos and 0.3 s with no editor at all. Also found: while the editor runs, the per-photo
"has a saved edit render?" lookups (`saved_render_path`, `resolve_session_for_read`: ~53 file-system probes per key press, on the
card) cost a lot (stubbing them cut the stalls from 2.3 s to 0.7 s); they are still done on the UI thread. See
camera_raw_first_browsing.md.

The editor also starts quietly in the background a few seconds after boot (`preview/editor_prestart`, default on; no photo open,
below-normal priority, rooted at the browsed folder, which needs its hidden `.image_triage_edits` folder to exist), so the first E
takes ~3.6 s instead of ~6.3 s. `IMAGE_TRIAGE_NO_EDITOR_PRESTART=1` turns it off.

**The editor's control server drops a connection that has been quiet for 30 s** (`IO_TIMEOUT` in PhotoCraft's `automation` crate; its own
client reconnects after 25 s). The background editor sits idle for minutes, so the first E after a wait failed with "PhotoCraft could not
open this photo ... `app.open` failed: [WinError 10053] An established connection was aborted" (seen on the owner's machine 5 minutes
after boot; reproduced with a 75 s wait). `PhotoCraftControl` now replaces a connection that has been quiet for `IDLE_RECONNECT_S`
(20 s) with a fresh one (same token) before the next request, for the main connection and the cancel connection alike; `quit()` does
the same with its short timeouts. The editor's own output (stdout and stderr: its log lines, and the message and backtrace of a panic,
with `RUST_BACKTRACE=1` set unless the user set it) used to be thrown away (`subprocess.DEVNULL`); it now goes to
`photocraft_editor_<time>_<pid>_<n>.log` in Image Triage's log folder (`%USERPROFILE%\AppData\LocalLow\ImageTriage\logs`, the last five
starts kept), `PhotoCraftProcess.log_path`, and a failed handoff's line in `errors.log` says whether the editor has exited, with what
code, and where that file is (`exit_note`).

## Quick handoff - 2026-10-11 (Camera Raw browsing without repaints; upstream merged)

Branch `camera-raw-first-nav` (Image Triage) and the fork's `codex/image-triage-integration`; the upstream merge of
this day is on the fork's `scratch/upstream-merge-2026-10-11` until the user fast-forwards the integration branch.

* Fork: Camera Raw stays up between photos (in-place swap, `cameraRawSwitch`, `busy`/`loading` in `ui.viewport`),
  a raw is browsed from a reduced-resolution development (Open re-develops it in full), the camera colour fit is
  made at the as-shot white balance. The fork's own `ui.cameraRaw {set}` was removed: a host adjusts the open dialog
  with upstream's `ui.menu.invoke {id: "filter.cameraRaw", params: {ui: {set}}}` (upstream #2988). Details:
  `docs/control-protocol.md` in the fork and [camera_raw_first_browsing.md](camera_raw_first_browsing.md).
* Image Triage: the popout keeps the editor's Camera Raw dialog up when moving to the next raw
  (`_switch_photocraft_document` with no `done`), waits for `busy: false`, prefetches the next photo, and no longer
  trusts a `ui.viewport` measured at another window size (hiding or shrinking the filmstrip left a stale picture
  over the dialog; the picture now covers the window until the editor reports again, and a resize re-asks).
* Measured on real 45 MP NEFs (Intel UHD 620): picture on screen 34-65 ms after the key; dialog swap to a prepared
  photo ~140-170 ms, to an unprepared one ~1.7-2.2 s (was ~4 s).
* Open no longer freezes the editor: its first canvas draw builds the GPU compositor on a worker thread (the driver's
  shader compile took ~14 s on the UI thread on this laptop) and the canvas draws on the CPU meanwhile. An Open pressed as
  soon as the first photo's dialog is up now gets its first control answer after ~0.2 s instead of 14 s. The compositor is
  deliberately not built ahead of time while browsing: that slowed every swap it overlapped (prepared 165 -> 189 ms,
  unprepared 1.72 -> 1.99 s). The full 45 MP CPU redraw after Open (~2.4 s) used to block the editor that long; it now runs
  on a worker thread and the photo fills in from the top (slowest control answer after Open: 2.35 s -> 0.22-0.50 s).
* Still open: the hosted autosave (`app.stash`) of an edited 45 MP photo leaves the editor unresponsive for ~9-12 s, starting
  ~4 s after the edit, with or without the GPU compositor. It encodes the project, a full-size PNG preview and the display JPEG
  at once across every core; one rayon thread cuts the stall to ~1.9 s but slows the edit and the save a lot.
* Checks that need the host's Qt event loop pumped while they wait (an embedded editor shares its parent's input
  queue): `scripts/check_camera_raw_recipe.py` now polls with short timeouts for that reason.

## Quick handoff - 2026-10-09 (browsing in front of the editor)

Branch `camera-raw-first-nav` (Image Triage, from `a4caa3a`) and the fork's `codex/image-triage-integration`
(from `ae56dc1`), both uncommitted. Design, measurements and verification:
[camera_raw_first_browsing.md](camera_raw_first_browsing.md).

* Fork: `app.stash` takes an optional `display` `.jpg` (screen-sized sRGB render, written first, failures
  only warn); new read-only `ui.viewport`; `photocraft_io::export_display_jpeg`. Documented in
  `docs/control-protocol.md`. Rollback binary kept as `target/release/photocraft-known-good-ae56dc1.exe`.
* Image Triage: the popout paints each browsed photo itself above the embedded editor (setting
  "Editor browsing": image area / whole editor / off); the editable document loads after a 300 ms dwell or
  on a click; edited photos show from the display render; per-navigation timing
  (`IMAGE_TRIAGE_NAV_TIMING`); faster navigation overall (see the measurements).
* Upstream: the clone has only the user's fork as `origin`; no upstream remote was configured, so no upstream
  merge was checked this session.
* Camera Raw workspace: `photocraft --raw-workspace` + `ui.cameraRaw` (fork) and the setting "Open photos in Camera
  Raw first (experimental)" (Image Triage, off by default). See camera_raw_first_browsing.md. Untested on RAW files.

## Quick handoff — 2026-10-09 (RAW swap speed)

Pushed: ImageTriage `remove-builtin-editor` at `1c4b886`; photocraft fork
`codex/image-triage-integration` at `ae56dc16` (merge of upstream `47ad748` is
`fb3c2e2e`). Nothing was pushed to storytold.

**Problem.** Swapping 45 MP Nikon NEFs through the embedded PhotoCraft took 3 s or
more, and the photos live on a NAS (`\\192.168.1.200\...`, measured 107 MB/s), where
each swap moves the NEF twice plus an 87 MB unpacked sensor DNG over the network.

**Measured (release build, DX12, local disk, PhotoCraft window shown).** RAW smart
object open: worker ~1.3 s -> ~0.55 s; canvas composite ~1000 ms -> ~130 ms; swap with
the unpack cached ~1.0 s. Cached 5 MP JPEG preview opens in ~30-90 ms. Harness numbers
taken with the PhotoCraft window hidden are ~200 ms worse than the real app. The NAS
case has not been measured end to end.

**Image Triage (`preview_controller.py`, `photocraft_bridge.py`, `photocraft_preview.py`).**
- Each swap first opens a cached screen-sized JPEG (from the NEF's embedded JPEG, kept
  in `.image_triage_edits/previews/`) in PhotoCraft; the RAW smart object loads after the
  selection rests 500 ms (`PHOTOCRAFT_RAW_DWELL_MS`) and replaces it in place.
- Previews use a second control connection (`PhotoCraftProcess.fast_lane()`), so they
  never queue behind a RAW open. Moving on cancels a running open job
  (`cancel_open_jobs`, only `app.open`, never a save) or abandons the open before it is
  sent (`should_continue` / `PhotoCraftSuperseded`); a stale RAW that still finishes is
  covered again by the current preview. First photo of a folder, photos with saved
  edits, and flat files skip the preview stage.
- Unpacked sensor DNGs are kept in a bounded LRU cache (4 files / 512 MB) in
  `.image_triage_edits/raw-sensors` instead of being deleted after each open.
- PhotoCraft is found as unversioned `photocraft.exe` (also in `~/Documents/photocraft`)
  and launched with `WGPU_BACKEND=dx12`: Vulkan overlay layers (Epic, Galaxy, ReShade)
  left the embedded canvas blank on this machine.

**PhotoCraft (`gpu/src/lib.rs`, `io/src/flat.rs`, `codecs/src/image.rs`,
`ui-egui/src/control_files.rs`).** 16-bit RGBA GPU page upload uses a 64K lookup table and
all cores (bit-identical to the generic path, covered by a test); tile build converts row
bands on several threads; `app.stash` encodes the project and its preview concurrently
(~0.8 s on a quiet machine, no change in control latency or peak memory).

**Open.**
- The RAW looks flatter than the camera JPEG/Photoshop (measured on a test shot: luma
  5th-95th percentile 6-57 against 3-101, saturation 0.37 against 0.80). PhotoCraft's
  develop has no tone curve or camera profile. Proposed, not built: fit a tone curve and
  colour matrix from the embedded JPEG so the RAW opens in the camera look.
- Saving writes ~730 MB per edited photo (493 MB `.pcraft` + 240 MB preview PNG) and
  took 4-23 s (disk-write spikes). Compression ratios measured: NEF 98%, sensor DNG 92%,
  developed tiles 91%. Decisions pending: compression policy, not saving the developed
  pixels for RAW smart objects, a smaller preview PNG.
- Edits made on the preview in its first half second are lost when the RAW replaces it.
- Local staging of RAW files was considered and rejected by the owner.
- Not run: the full Image Triage and workspace suites, clippy/layers/wasm checks and
  `scripts/check_photocraft_handoff.py` on the final build. Focused tests that pass:
  71 PhotoCraft-related Python tests; gpu, codecs, io raw/roundtrip, engine
  raw_development and ui-egui control_files in the fork. Five tests in the wider
  preview suite fail identically on the committed code before these changes.
- Uncommitted and unrelated here: the `qt_logging` work in `main.py`.

**Build and run on this machine.** Rust stable 1.99 via rustup; clone at
`C:\Users\user0\Documents\photocraft`; `cargo build --release -p photocraft`; set
`IMAGE_TRIAGE_PHOTOCRAFT_EXE` to `...\target\release\photocraft.exe` and fully restart
Image Triage (a running session keeps its old PhotoCraft process).

## Quick handoff — 2026-10-07

- Removed the built-in editor and its obsolete tool/AI workers, retaining saved
  recipe rendering, exports and comparison views.
- PhotoCraft opens automatically inside the fullscreen popout. One embedded
  editor persists through filmstrip selection; edits stash automatically, restore
  when returning to a photo and refresh main-app thumbnails after saving.
- Fixed startup freezes, incompatible executable selection, failed-open recovery,
  reopening after closing a document tab, save/close/shutdown and child cleanup.
- Hid the old viewer and filmstrip during initial loading. Removed the extra
  popout bars and filename footer. Filmstrip panels and the six-pixel resize gap
  follow all five native themes; the resize highlight appears on hover/drag.
- Companion PhotoCraft source adds hosted startup, background open/stash,
  atomic document replacement, cached snapshots, hosted Save and `ui.theme`.
  Image Triage and the companion integration branch belong in the user's own
  repositories; do not push host changes to storytold. Rebuild the companion. Local
  `photocraft-host-v5.exe` is a build artifact, not a committed executable.
- Brush-hover lag on this machine came from software rendering; local preferences
  were switched to GPU/DX12. That preference change is not included in Git.
- Validation: 49 focused Python tests, 683 native UI tests (3 ignored), native
  all-target clippy, dependency layers and all 23 wasm checks passed. Live checks
  covered theme pixels, fullscreen, switching, stash/restore, native Save, closed
  tab reopening and process cleanup; 24 MP lifecycle checks also passed.
- Existing JSON edits retain their rendered appearance; their operations are not
  converted into editable PhotoCraft adjustment layers. Switching source folders
  starts a new process to maintain scoped read/write roots.

Build the companion with `cargo build -p photocraft`; Image Triage discovers the
local debug/release build. The opt-in live check is
`python scripts/check_photocraft_handoff.py --executable <exe> --output <folder>`.

## Upstream updates and conflict tracking

At the start of every development session, follow `../AGENTS.md`: fetch upstream
and the user's fork, review divergence, merge into the companion's
`codex/image-triage-integration` branch, and validate before promoting an editor
build. Keep upstream updates as merge commits so our original host commit remains
identifiable; avoid rewriting or force-pushing the fork's history.

### 2026-10-08 update

- Last validated upstream: `47f9306` (parent of host commit `54526e4`).
- Incoming upstream: `bce7e54a639eb043abf344293e5d20e36238a3a2`, 157 new commits.
- Fetched fork tip: `8948511`; it is an ancestor of incoming upstream, 59 commits
  behind it, with no fork-only work to reconcile.
- Resolved companion merge: `0c05109eb27de500afc9c62660eae9932d588ad9` on
  `codex/image-triage-integration`; the original host commit remains in its ancestry.
- Conflicts: startup `main.rs` retains both `--hosted` and upstream's
  `--in-window-menus`; `lib.rs` retains `control_files` and upstream's `credits`;
  the manifest retains upstream's normal codecs dependency and drops its redundant
  dev entry; protocol docs retain `ui.theme` alongside upstream's `colorPanel` API.
- Automatic merges reviewed: scoped I/O callbacks, control dispatch, hosted Save,
  stash bookkeeping and window setup. Upstream's new engine authorization remains
  installed for automation; host I/O still uses granted read/write capabilities.
- Semantic conflict: upstream's new custom titlebar enables standalone caption
  buttons and edge-resize zones. Hosted mode disables those controls because the
  Image Triage parent owns the window and filmstrip sizing. The hosted Save test
  now also installs the upstream engine authorization gate and verifies that
  scoped Save succeeds, Save As remains denied and the gate does not leak.
- Validation complete: 49 focused Python tests, 846 native UI tests (3 ignored),
  57 desktop tests, native all-target clippy, formatting, layers and all 23 wasm
  checks passed. The 24 MP live handoff passed persistent navigation, stash/restore,
  native Save, grid updates, closed-tab reopen, all five themes, hidden startup,
  fullscreen geometry and child cleanup, with zero editor hides during navigation.
- A v5 `.pcraft` fixture opened in the updated editor and produced matching sampled
  render pixels at the original dimensions. The old project/render files remained
  unchanged. This checks basic saved-project compatibility, not every possible
  document feature.
- Native Pro and Studio Light screenshots were reviewed: the editor renders and
  hosted window buttons are absent. Filmstrip/gap/hover pixels passed in all five
  themes. Desktop captures were black in this session, so the harness now reports
  `native_screenshots_verified` and `desktop_captures_verified` separately rather
  than silently treating an unusable desktop image as visual proof.
- Validated executable: `target/debug/photocraft-host-0c05109.exe` (PhotoCraft 0.4.1,
  source commit `0c05109`). The previous `photocraft-host-v5.exe` is retained.
- Artifacts: the thread's `upstream-bce7e54`, `upstream-bce7e54-visual` and
  `upstream-bce7e54-legacy` folders. No remote changes were pushed.

### 2026-10-09 update

- Previous upstream in the branch: `652b972`. Incoming: `47ad7486` (103 commits, 233
  files). Merge commit `fb3c2e2e` on `codex/image-triage-integration`; the fork's earlier
  history is unchanged.
- Three textual conflicts, all trivial: `engine/src/lib.rs` keeps both `raw_develop_cmds`
  and upstream's `redeye_cmds`; `docs/control-protocol.md` keeps `ui.theme` and upstream's
  `ui.set` entry; the Windows crosshair cursor test takes upstream's wording. 17 files
  merged automatically, including `control.rs`, `services.rs` and `main.rs`; type-check is
  clean, but those automatic merges have not had the review described above.
- Validation: `cargo check` of photocraft, io, engine and ui-egui, and the focused tests
  listed in the quick handoff. Full native UI suite, clippy, layers, wasm and the live
  handoff were not run for this merge.

### 2026-10-11 update

- Previous upstream in the branch: `7dc8bbd8` (merged 2026-10-10, 519 commits; not recorded here at the time).
  Incoming: `d45b3718` (storytold/photocraft main, 2026-10-11 11:15 UTC-3), 180 commits, 418 files. Merge commit
  `0824377f` on the fork's `scratch/upstream-merge-2026-10-11`, made from `1a64b31e` (the fork's commits since
  `bf7a0a16`: reduced-resolution development, in-place swap). Nothing pushed. Upstream was fetched by URL into a local
  ref (`upstream-peek/main`); the clone's only remote is the fork.
- Twelve conflicted files. Smart objects: upstream moved rendering onto a stage cache (`render_in`, `place`,
  `smart_cmds/stack_cache.rs`); upstream's signatures stay as they are, RAW-developed objects go through a sibling
  `place_developed`, and their stage key includes the development settings and the document's colour profile.
  `io::flat`: our display-JPEG export and upstream's Targa options both kept. `raw` / `io::raw`: upstream merged #2135
  (Nikon colour fit) identical to the commit already on the branch, so ours stays (it also fits at the as-shot white
  balance). `ui-egui`: `Services` keeps our scoped background read/write callbacks and gains `automation_open_pcraft`;
  `app.open` keeps the RAW smart-object guard and gains the directory `.pcraft` open; `app.save` is upstream's (save
  worker) beside our `app.bind` / `app.stash`; job events keep our `app.open` / `app.stash` handling beside Beep When
  Done; the `ui.menu.invoke` modal gate is upstream's #2988 with our hosted-save exemption in the authorisation check.
- Automatic merges worth a second look: `control.rs` (dispatch, authorisation), `jobs_ui.rs`, `lib.rs` (commands,
  `Services`), `gpu_canvas.rs`. The live check on the merged release editor passed (real NEFs: prepared swap 167 ms,
  return to an unprepared photo 1.66 s, recipe and render written, Open continues the saved document, unsupported-raw
  fallback). It does not exercise Save routing, themes or window handling.
- Validation: rustfmt; clippy `-D warnings` over the whole workspace; tests for raw, io and engine (1,956 passed) and
  the ui-egui library (2,094 passed; one render-timing test failed once under load and passed on rerun). The ui-egui wgpu
  integration tests (11 binaries, 19 tests) fail on this laptop's adapter ("expected the GPU canvas", 2048 px texture
  limit); they were not run against unmerged upstream. Not run: the desktop app's tests, the layers check, the wasm
  checks, the full live handoff.
- Semantic change: the fork no longer has `ui.cameraRaw {set}`; scripts use upstream's `ui.menu.invoke` route.

### 2026-10-12 update (upstream 0.8.2; the fork takes upstream's side)

- Previous upstream in the branch: `d45b3718`. Incoming: `d43db3e1` (storytold/photocraft main, "Release 0.8.2"), 310
  commits. Merge commit `14ac59e9` on the fork's local `scratch/upstream-merge-0.8.2`, made from `c6092f86`. Nothing
  pushed; `codex/image-triage-integration` is unchanged until the user fast-forwards it. Upstream was fetched by URL
  into `refs/upstream-peek/main`.
- Rule from the owner, now in both AGENTS.md files: the fork carries only what hosting needs; where upstream built the
  same thing, ours is dropped. Nothing is ever sent to storytold.
- Ten conflicted files. Dropped for upstream's: the fork's split of `raw/develop.rs` (the file is upstream's again,
  byte for byte), the as-shot colour fit and `with_fitted_look` in `io/raw.rs` (upstream's `default_look` does this
  since #3201), `import_raw_browse` (MHC demosaic), and the fork's white-balance and exposure fit tests.
- Kept beside upstream's: `raw/reduced.rs`, rewritten so it no longer edits upstream code (it averages the sensor
  into a small Bayer mosaic and calls upstream's `develop_sensor`; sensors with black-level deltas or gain maps are
  developed in full, as are non-Bayer ones); the Camera Raw workspace's busy state, prepared proxy and stand-in size
  in `camera_raw_ui.rs` beside upstream's raw tone curve and display colour; the background full redraw in
  `canvas.rs` / `gpu_canvas.rs` beside upstream's canvas texture budget; hosted start not waiting for the monitor
  profile in `main.rs`; the display JPEG exports in `io/lib.rs`.
- Fork delta against upstream after the merge: 45 files, +5,002 / -98 (was 47 files, +5,208 / -205).
- Validation on this machine (Rust 1.99, first build here): `cargo check --workspace --all-targets`, rustfmt and
  clippy `-D warnings` clean. Tests: raw 142, io 501, doc 58, format 114, codecs 664, gpu 77, engine 1,582 passed, none
  failed; ui-egui library 2,389 passed, 2 failed. Both failures are upstream's: the Turkish catalog (`i18n/tr.tsv`) at
  `d43db3e1` has no entry for "8 bit" and some preference labels, in files the fork does not touch.
- Image Triage side: 221 PhotoCraft-related Python tests pass (handoff, control, two-stage, sensor cache, Camera Raw
  first, prefetch, recipe flow, editor prestart, native-first browsing).
- Live, release build of `14ac59e9` (`target/release/photocraft.exe`, PhotoCraft 0.8.2), synthetic photos only:
  `check_raw_workspace.py` passed (control ready 1.7 s, Camera Raw opens, adjust, commit, one document left);
  `check_camera_raw_first.py` passed 3 runs of 4. The failed run got an empty line instead of a JSON reply to
  `ui.cameraRaw` right after `file.close`; cause not established (the script and the controller both use the
  fast-lane connection at that moment, which would explain it; not shown to predate this merge).
- `check_camera_raw_first.py` and `check_camera_raw_recipe.py` now turn `preview/editor_on_demand` off while they
  run: since the editor opens only after E, they timed out waiting for the first photo. `check_photocraft_handoff.py`
  still fails at once on "filmstrip flashed during editor startup": it predates the popout changes of 2026-10-11 and
  was not updated.
- Real NEFs (public samples from raw.pixls.us, kept in `C:\Users\Checkout\Pictures\nef-samples`; desktop with 20
  threads, not the laptop). Upstream's decoder reads D850 12/14-bit lossy and 14-bit lossless, Z 7 14-bit lossy and
  lossless and Z 8 14-bit lossless; it refuses Z 8 / Z 9 High Efficiency ("Nikon compressed NEF without a
  linearization table"). Library timings per file: decode 0.4-0.65 s, reduced browse import 0.5-0.85 s (2072 x 1380),
  full default import 1.2-2.1 s.
- `check_camera_raw_recipe.py` on D850 14-bit lossy + Z 8 14-bit lossless passed: first raw in Camera Raw 2.3 s,
  second 1.5 s, return to the first 0.9 s, recipe and render written, Open continues the saved document; picture on
  screen 121-190 ms after the key. With `--unsupported Z9-high-efficiency.NEF` it fails at that step: the sensor
  DNG fallback cannot read the file either (LibRaw: "Unsupported file format or not RAW file"); the popout keeps
  the previous photo and reports it.
- So the retained-raw smart object route (`rawSmartObject`, `rawSensorPath`, `raw_smart.rs`, `raw_development.rs`,
  `raw_develop_cmds.rs`, `raw_develop_ui.rs`, about 1,000 lines in the fork, and `photocraft_raw_source.py` here)
  no longer covers anything upstream does not for these cameras: compressed NEFs open upstream's way, and High
  Efficiency files open neither way. It is the next thing to drop, pending the owner's word.
- Not run: the ui-egui integration tests, the desktop app's tests, the layers and wasm checks, Save routing, themes
  and window handling in the live app. Automatic merges in `control.rs`, `jobs_ui.rs`, `lib.rs` and `services.rs`
  were compiled and tested, not read.
- Removed the same day, on the owner's word: the retained-raw smart object route, from both repositories. Fork
  (`scratch/drop-raw-smart-route`): `raw_development.rs`, `raw_develop_cmds.rs`, `raw_smart.rs`, `raw_develop_ui.rs`,
  their tests and examples, `rawSmartObject` / `rawSensorPath` on `app.open`, `ui.rawDevelopment`, and the two
  capability flags; `doc`, `engine`, `format`, `psd_export` / `psd_import` and `shortcuts.rs` are upstream's files
  again. Image Triage: `photocraft_raw_source.py`, its two test modules, `check_photocraft_raw.py`; `app_open` sends
  one plain `app.open` (with `cameraRaw` in the Camera Raw workspace). A raw the editor cannot develop from sensor
  data (Z 8 / Z 9 High Efficiency today) opens as the camera's embedded preview, with no Camera Raw dialog.
  `.pcraft` projects saved earlier with a RAW-backed smart object still load (the extra manifest fields are ignored)
  but lose their development settings; this was not tested on a real saved project.
- After the removal (fork commit `8dfc5340`, release build): fork delta against upstream 25 files, +3,808 / -88.
  rustfmt and clippy clean; raw 142, io 501, doc 58, format 114, engine 1,578 passed; ui-egui library 2,381 passed
  and the same 2 upstream catalog failures. Image Triage: 237 PhotoCraft-related tests pass. Live:
  `check_camera_raw_recipe.py` on two Z 7II NEFs (the owner's camera) plus the Z 9 High Efficiency file passed
  (second raw 1.4 s, return 1.5 s, the High Efficiency file opens as its preview in 1.0 s with no dialog; the first
  photo took 7.5 s on the new executable's first launch); `check_camera_raw_first.py` passed.
- With "Open photos in Camera Raw first" off (the default), a raw now opens as upstream's ordinary developed
  document: there is no sensor-level re-development after the open in that mode. Not run live in that mode
  (`check_photocraft_handoff.py` is stale).
- "Open photos in Camera Raw first" is now on by default (`CAMERA_RAW_FIRST_DEFAULT`), and no longer labelled
  experimental: it is the mode that works as Photoshop does (Camera Raw, then the editor, settings kept per photo),
  and the only one with raw-level editing. A profile where the settings dialog was accepted before has the old
  value stored and keeps it.
- Run cargo with `-j 6` or so on this 16 GB machine: a fully parallel workspace test build froze it.

### 2026-10-12 update, later (validation gaps, the save stall, more of the fork dropped)

Fork `codex/image-triage-integration` at `823d9890`, Image Triage `main`; both pushed at the end of the day.

- Upstream: no release after 0.8.2; main is 33 commits ahead of it and merges without conflicts. Nothing merged
  (releases only, per the owner).
- `check_photocraft_handoff.py` runs again (it pins the two settings it needs) and passes at 1 MP and 24 MP: hidden
  start, embedding, stash and restore, PhotoCraft's own Save, reopen after closing the tab, five themes, cleanup,
  no editor hides. `desktop_captures_verified` is false on this machine, as in earlier sessions. One 24 MP run of
  five ended with the Python process gone (exit 127, no traceback) and the editor left running; not reproduced in
  three reruns, cause unknown. A check that dies leaves its pinned settings in the real profile
  (`preview/photocraft_camera_raw_first`, `preview/editor_on_demand`): remove them by hand if that happens.
- The fork's changes in `main.rs`, `services.rs`, `control.rs`, `jobs_ui.rs`, `lib.rs` and `menus.rs` were read
  against upstream after the merge: nothing found.
- The occasional empty control reply: `PhotoCraftControl` had no lock, and the fast-lane connection is used from
  several threads (prefetch, viewport, saves). Each request and its reply are now one locked exchange. With it,
  `check_camera_raw_first.py` passed 6 runs of 6 (1 of 4 failed before).
- Save stall. `scripts/measure_stash_stall.py` (new) opens a photo, edits it, saves with `app.stash` and times
  `ui.viewport` while zooming; `--cores N` holds the editor to N logical processors. On a 45 MP Z 7II NEF: all 20
  threads, no stall either way (save 3.0-3.7 s). Held to 8: slowest answer 97-901 ms before; with the fork's
  encoders on a pool of their own (`100aedc2`) 35-154 ms, save 5.5-8.8 s against 5.2-5.9 s. Held to 4: unchanged,
  about 1-3 s (the two large encoders start a thread per core themselves; letting them take turns did not help
  and was not kept). Not measured on the laptop, where the 9-12 s stall was seen.
- A save still writes 310 MB (`.pcraft`) + 211 MB (preview PNG) for a 45 MP photo. Unchanged; the format decisions
  are the owner's.
- Dropped from the fork: its tweak inside upstream's `codecs/image.rs`. Checked against upstream and kept, as
  upstream has no equivalent: the 16-bit canvas upload table (`gpu/src/lib.rs`), the background compositor build
  and full redraw (`gpu_canvas.rs`), the reduced browse development. Fork delta: 24 files, +3,846 / -84.
- Full Image Triage suite on this machine: 1,814 passed, 7 failed, 3 collection errors. All ten are the same at
  `f10ba09` (before this day's work): the three sandbox packages are not in the checkout (3 errors, 1 failure);
  `test_preview_right_click` (5); and the architecture ratchet, which the PhotoCraft branch exceeded before it was
  merged (main window 2,829 -> 2,848 lines, 109 -> 110 methods, 342 -> 343 attributes; private reaches in
  `preview_controller` 164/163, `settings_controller` 347/343, `startup_controller` 27/25). Re-registered at
  those numbers afterwards, on the owner's word; a local named `window` that the checker read as the main window
  (`preview_controller.py`) was renamed. This Python has a `tests` package of its own in site-packages (from pyppeteer2) that hides
  `tests.harness`; the suite was run with the repository's `tests` registered first.

PhotoCraft is the popout's default editor. The current filmstrip, navigation,
review controls and comparison views remain in Image Triage. Opening a photo
automatically loads the native editor in the image pane; there is no editor
button to press.

## Lifecycle

1. `FullScreenPreview.show_entries` requests the selected source photo. The
   controller queues the handoff on one worker, keeps Qt responsive and skips
   superseded requests. Only the latest request may embed/show its window.
2. A source folder gets one PhotoCraft process with that folder as its read
   root and its hidden `.image_triage_edits` folder as its write root. A nested
   folder also needs a new process because it has a different write root.
   Unsupported sources and existing JSON edits are materialized on the worker.
3. Before switching, `app.stash` captures COW pixels without locking editing.
   PhotoCraft writes `<source filename>.pcraft` and
   `<source filename>.photocraft.png` on its background worker. The current
   document stays visible while the next decodes. `app.open {replace: true}`
   commits the replacement in one update, retaining the previous document on
   failure. Its native window remains attached and visible throughout filmstrip
   navigation. Only one document tab remains open.
   Three recent saved snapshots remain warm; pending and failed saves remain
   available in memory. Returning can restore the snapshot before disk I/O
   finishes. The source suffix in each filename avoids RAW/JPEG collisions.
4. A 1.5-second poll stashes changes while editing. PhotoCraft's File > Save
   is bound to the same scoped project/render operation. Completion refreshes
   grid thumbnails and preview caches. Headless resize/export paths read the
   saved render, and edited-variant discovery exposes it for comparison.
5. Close and normal app shutdown wait for saves; a failed save offers Cancel
   to retain the document/snapshot or an explicit Discard to close. The child process
   exits **before** Qt closes its native host. `aboutToQuit` provides final
   cleanup, and development restart drains saves/terminates PhotoCraft before
   launching the replacement or hard-exiting. Quit has bounded transport and
   process waits, including waiting after a force kill. Launch/setup failures
   close the socket, reap the process and remove its token.

## Concrete regressions repaired

- Saved projects/conversions previously narrowed the read root to the hidden
  folder and could not save/follow ordinary neighboring photos.
- Only arrow navigation followed the editor; direct opens and other entry
  changes could leave the previous photo on screen.
- Dead processes were reused; setup errors could leave an unowned process.
- Same-stem files shared projects and converted TIFFs.
- Failed revision inspection or out-of-root saves silently allowed discard.
- Reopening the same selection could close/reopen it and lose unsaved state.
- Resizing moved the host but not its child; 64-bit HWND handling and physical
  pixel sizing were incomplete.
- `.pcraft` saves did not invalidate thumbnails or feed the retained headless
  edit/export paths.
- Old JSON edits would disappear on the first PhotoCraft open. They now supply
  a full-resolution rendered base. Their original recipe files remain intact;
  this preserves appearance but does not translate their operations into
  editable PhotoCraft adjustment layers.
- Closing the host before quitting its native child could stall control I/O.
  Closing the main window with the popout still open also needed explicit
  process and popout cleanup.

## Companion source and validation

The companion checkout at `../photocraft` adds capability-scoped background
open/stash services, snapshot caching, `app.bind`, and hosted Save routing.
The ordinary UI and agent Save As paths retain their existing authority rules.
Native builds are available as `target/debug/photocraft-host.exe` and
`target/release/photocraft-host.exe`; source-checkout detection selects the newer
hosted executable. An explicit executable override still takes precedence.
Companion hosted builds precede ordinary PATH and installed copies.

- Combined handoff, navigation, preview-polling, headless rendering and
  collection-mode regression run: 45 passed.
- Additional zoom/theme/filmstrip/extractor checks: 43 passed, 7 subtests.
- Thumbnail/headless rendering checks: 25 passed in the focused run.
- Collection-mode checks: 11 passed.
- PhotoCraft UI library: 680 passed, 3 ignored; desktop binary: 51 passed.
  Native clippy (all targets) and
  dependency-layer checks passed. All 23 WebAssembly checks passed.
- The full Image Triage suite cannot collect three missing sandbox packages
  (`efficientvit_sam`, `grounded_sam`, `oneformer`). A broad native Cargo test
  build exhausted Windows paging capacity; the UI library passed when rerun
  with `-j 1`. These are verification limits, not passing-suite claims.
- `scripts/check_photocraft_handoff.py` runs the real editor with synthetic
  photos, checks save/restore, one-document ownership, main-grid notifications,
  original checksums, native embedding, token removal and process exit.

On the 24 MP debug-build smoke run, selection calls took 19.1 ms for switching
and 12.7 ms for returning. Native editor readiness took 1.18 s and 0.80 s;
decoding/uploading remains asynchronous. Cold process open took 3.74 s. These
measurements were taken while builds were running and are not a zero-latency
claim. The latest smoke artifacts and timing JSON are in the current chat's
`photocraft-handoff-final` visualization folder.

A release-build run also verified the native window parent and cleanup:
switch/return selection calls took 22.5/16.2 ms, with editor readiness at
1.47/0.85 s. Its older executable skipped the newly added hosted menu-Save
control check; the latest debug executable passed that check. Release artifacts
are in `photocraft-handoff-release`. The current source detector selects the
newer debug hosted executable; restart Image Triage to use the integration.

## Startup freeze follow-up

The reported splash freeze was reproduced with a timed Python stack dump:
MainWindow construction called AI runtime detection, which waited indefinitely
inside Python 3.13 platform.machine() → Windows WMI. Windows runtime tags now
use sysconfig's interpreter build platform and a literal Windows system name;
both AI path and runtime-package detection share that implementation. This
also correctly identifies an x64 interpreter running on ARM64 Windows.
Regression tests reject calls to platform.machine/system on Windows and cover
x64, ARM64 and x86. AI path/runtime checks: 50 passed, 17 subtests. Actual
MainWindow construction completed in 2.85 seconds after the change.

## Incompatible executable and close-loop follow-up

The reported `unknown method app.stash` error identifies an incompatible
PhotoCraft binary. Hosted source builds now precede installed/PATH copies.
Before opening any document, the bridge probes app.stash/app.bind validation
without writes; incompatibility reaps the process and removes its token.
An initial bind failure also releases the process. Failed switching keeps the
previous editor visible and explains which photo it still owns. A close-time
save error offers Cancel (retain the editor) or explicit Discard (quit without
another save attempt), rather than trapping the user behind an OK-only warning.

Regression run: 46 passed. The live `photocraft-compatibility-check` smoke used
normal executable discovery (no override), selected the current debug hosted
build, and passed switching, stash/restore, menu Save, main-grid notification,
original checksums, native parent, token removal and process exit.

The subsequent launch flash was traced to the actual running app's inherited
environment: IMAGE_TRIAGE_PHOTOCRAFT_EXE selected the old ordinary release exe,
while the diagnostic shell had no override. The launcher now retries companion
hosted builds only after protocol incompatibility, before any document opens,
and the controller remembers the successful executable for subsequent sessions.
Compatible overrides remain honored; image/I/O failures do not trigger retries.
The `photocraft-stale-override` live run explicitly selected that same old release
exe, rejected/reaped it, then passed the full hosted workflow on the companion
build. Follow-up regression run: 49 passed.

## Persistent visible editor follow-up

Filmstrip requests no longer hide the host. The HWND is attached once, rather
than reparented and temporarily sized to zero after every open. Hosted protocol
v2 replaces the active document after decoding succeeds, without exposing an
empty-document frame; failed imports retain the old document and edits. The
local Qt host disables input during replacement without blocking on a native
cross-process EnableWindow call. The editor remains visible throughout.

The current executable is `target/debug/photocraft-host-v2.exe`; detection
selects the newest `photocraft-host*.exe`. Overrides naming the same companion
checkout's ordinary release/debug exe select its hosted build directly, avoiding
an incompatible launch flash. Hosted startup disables persistence, centering,
activation and taskbar presence, starts off the desktop (eframe forcibly shows
its first rendered frame), and is hidden by the bridge until embedded.

The final 24 MP `photocraft-persistent-editor-responsive` live run verified
zero host hide events, one PID and HWND, no standalone window on the desktop,
one active document, stash/restore, hosted Save, unchanged originals and cleanup.
Selection switch/return took 14.4/15.1 ms; replacement readiness took 2.43/1.28 s
on this debug build, with the previous canvas kept visible. Python checks:
51 passed. PhotoCraft UI library: 682 passed, 3 ignored; desktop: 51 passed.
Native all-target clippy and all 23 wasm checks passed.

The final fitted-canvas rerun (`photocraft-persistent-editor-fitted`) also passed
all visibility and lifecycle assertions: selection switch/return 14.5/10.0 ms,
replacement ready 2.50/1.47 s. Initial attachment now fits the image once to its
actual pane size rather than retaining the startup viewport's small fit scale.

## Startup loading cover and reduced work

The popout now covers its original canvas before the first window paint with
“Opening PhotoCraft…”. Once editing is active, navigation keeps that editor
visible. Failed initial launches show an error on the cover rather than reverting
to the old viewer. Compare/collection/before-after still use their inspection
canvas intentionally. Normal editor mode skips the old full-resolution decoder
and its preloads, so both apps no longer decode the same photo for one pane.

The current hosted binary is `target/debug/photocraft-host-v3.exe`. Display
profiles arrive asynchronously without the hosted startup's former two-second
wait. Theme setup precedes import. Fresh imports/restores start at the engine's
DocState revision 1, avoiding redundant revision inspections before binding.
The shell attaches while it is still empty, behind the cover, rather than waiting
for full-resolution rendering before the native window operations.

The same v3 24 MP fixture reached the editor in 2.62 s before early attachment
and 2.30 s with it (single-run comparison, not a universal latency guarantee).
Source/process launch stage timings are recorded by the smoke script; one first
run of the newly built exe spent 5.08 s in process creation, while subsequent
creation took about 0.22 s. The final early-attach live check passed native
embedding, zero navigation hides, stash/restore, menu Save and cleanup.

## Brush cursor performance on Windows

PhotoCraft draws brush-type cursor outlines inside its rendered frame. A software
window renderer can therefore make pointer movement appear slow even without a
stroke. On this machine, persisted CPU mode selected Microsoft Basic Render Driver:
Brush/Eraser/Clone Stamp/Dodge hovered at 6.5–7 FPS on a 24 MP photo, despite only
about 1 ms of UI work per frame. Explicit DirectX 12 on Intel UHD Graphics 770
reached 54–57 FPS in the same embedded-window check. The local PhotoCraft graphics
preferences now select GPU mode and DirectX 12; the original preferences are backed
up in the brush-hover diagnostic artifacts. No global override was added to the host.

A full 24 MP handoff check with the updated settings passed stash/restore, menu
Save, persistent filmstrip navigation, hidden startup and cleanup. Switch/return
readiness was 743/175 ms; initial readiness was 3.63 s. Measurements are single-run
observations on this machine, not universal performance guarantees.

## Reopening after closing a document tab

Filmstrip selection checks the live session rather than trusting the last source
path. An empty editor clears its active save binding, while pending saves remain
tracked. Clicking the current thumbnail also requests an open, but leaves an
existing document and its unsaved edits intact. Pending/failed snapshots reopen
using their write-root stash key; disk imports use the read-root path.

47 focused Python tests passed. The 24 MP live check closed PhotoCraft's document
through its menu and reopened both the same thumbnail and a different thumbnail
in the same process, with zero host hides. Save/restore and cleanup also passed.

## Native theme integration

The filmstrip uses PhotoCraft's active panel, accent and text tokens from the
lightweight `ui.theme` control request. The host refreshes the palette on opening
and in its existing background poll, without inspecting document pixels. It no
longer forces the Pro theme at launch. The filename footer has been removed from
the layout; a full-width two-pixel accent divider with a six-pixel hit area replaces
the grip. The gap uses PhotoCraft's active `dock` token, distinct from the panel
background. The divider appears only on hover or while pressed for resizing.
Resize/collapse and thumbnail selection behavior are retained. The filmstrip stays
hidden during initial editor loading, then appears when the editor is ready;
ordinary file switches keep it visible. The popout uses true fullscreen, including
the taskbar area.


The updated native build is `target/debug/photocraft-host-v4.exe`. The 24 MP live
handoff verified panel/divider pixels against the active native tokens in all five
themes, plus tab-close reopen, Save/stash/restore and cleanup. Pro and Studio Light
were visually checked in desktop captures. Validation: 49 Python tests, 683 native
UI tests (3 ignored), native all-target clippy, dependency layers and all 23 wasm
checks passed.

The hover/fullscreen follow-up passed the same 49 focused Python tests and the
live native handoff in all five themes. The check verified that the idle resize
gap matches the theme's dock color, hover reveals the accent line, the filmstrip is hidden
at startup and visible after loading, and the window covers the full screen
geometry. Save/restore, tab-close reopen and process cleanup still passed with
zero editor hides during navigation. A desktop capture confirmed taskbar coverage.

The visible-gap follow-up adds `dock` to `ui.theme` and paints the six-pixel resize
gap in that token, with a darker fallback for older builds. Built
`target/debug/photocraft-host-v5.exe`. All five themes passed live pixel checks;
Pro and Studio Light desktop captures were reviewed. The 49 Python tests, 683
native UI tests, all-target clippy, layers and all 23 wasm checks passed.
# RAW editing research (2026-10-08)

See [the RAW integration plan](photocraft_raw_plan.md) for the source trace,
proposed RAW-backed smart-object workflow, compatibility limits, migration policy,
and acceptance checks. The initial research session added the plan without
changing RAW feature code or the editor build.

Session fetch found upstream `e5e3e39`, a release-only version bump from the
previous `bce7e54`. Merged locally as `dca3aa7` before the planning-only clarification;
only Cargo.toml/Cargo.lock changed, without conflicts. Fork-only work: none.
The known-good `photocraft-host-0c05109.exe` is retained; the new version-only
merge has not been rebuilt or promoted. Nothing pushed.

## RAW-backed editing implementation (validated)

The build session fetched upstream and fork again; no newer upstream commits or
fork-only work were found. Source base remains integration `dca3aa7`, upstream
`e5e3e39`. No merge conflicts occurred. The previous validated host remains available.

Fresh RAW files bypass developed TIFF conversion. PhotoCraft advertises explicit
RAW-smart and sensor-adapter capabilities. Nikon Z7/Z7 II compressed NEFs use a
small Image Triage adapter over the existing rawpy dependency: untouched Bayer
samples and CFA/black/white/WB/calibration/crop/orientation metadata are written
to a temporary sensor DNG. Scoped `app.open` reads both the original NEF and
adapter, embeds both, and removes the transfer after replying. Original NEF bytes
stay the Export Contents source; `.pcraft` keeps sensor settings and derived pixels.
Unsupported sensor input fails clearly and keeps the previous photo open.

Edit Contents on RAW-backed objects opens sensor exposure, WB and demosaic
controls. Preview requests coalesce on a background worker. OK commits one undo
step; Cancel preserves the document. Filmstrip switching and shell close settle
the draft before stashing. Periodic autosave leaves an active draft open.
Finished jobs must be read from the completed-job table, not just the running-job
lookup; UI regression checks caught and corrected a stuck “applying” state.
Commit failures retain recoverable settings. Existing pixel Camera Raw filters
remain downstream; older TIFF-backed projects preserve their edits.

Automated RAW dialog opening uses `ui.rawDevelopment {open:true}`, restricted to
embedded source bytes. Generic Edit Contents keeps upstream's ambient-filesystem
authorization gate. Regression tests cover combined open/settings/commit and
reject linked sources; pure queries remain available during a background commit.

Most source changes are isolated in new doc/io/engine/UI and Python modules.
Shared hooks are smart-object rendering and Edit Contents, two optional native
format fields, control capabilities, and open/stash settlement. Follow the RAW
plan's upstream watchlist and review these hooks at each merge.

Current validation: 58 focused Python tests; full doc/engine/format/IO non-corpus
Rust suites passed. Four public CC0 Z7/Z7 II NEFs match rawpy sensor values exactly
and retain original/sensor data, calibration and full 8256×5504 default crop through
native-format round-trip. Release 24 MP RAW-smart import was 544 ms versus 563 ms
ordinary import; real 45 MP imports were about 1–1.2 s. Native projects are roughly
419–458 MB and the standalone save/load/export check peaked at 2.2–2.4 GiB RSS.
Provenance and measurements live under
`C:/Users/user00/.codex/visualizations/2026/10/08/photocraft-raw/`.

Live checks passed with a small sensor DNG, a 24 MP DNG, and full-resolution Z7
and Z7 II NEFs. They verify coalesced source previews, periodic autosave retaining
the draft, navigation committing/stashing it, Save/grid propagation, RAW/PNG
switching, same-tab reopening, same process/HWND, disk restore in a fresh process,
unchanged originals, no developed TIFF, temporary-transfer removal and process/token
cleanup. The RAW dialog screenshot was visually inspected.

Debug-build observations: 24 MP cold open 6.2 s, edited switch 9.0 s, cached return
2.1 s, peak editor RSS 3.0 GiB. Z7/Z7 II 45 MP cold open 8.7–10.4 s, edited switch
17.4–22.4 s, cached return 3.8 s, peak editor RSS 4.8–5.0 GiB. Cold open includes
launch and hosting. Edited switching includes full development and project/preview
stash; these costs remain a performance limitation. Measurements ran alongside
compiler work and are single-run observations, not an interactive-latency guarantee.

The ordinary 24 MP handoff also passed all five theme pixel checks, hidden startup,
fullscreen geometry, native Save, closed-tab reopening and cleanup, with zero editor
hides during navigation. Native screenshots were checked; desktop capture returned
black as in the prior session and is reported separately by the harness.

Full UI suite: 852 passed, 3 ignored; final RAW UI tests: 7 passed. Final RAW engine
tests and all-target clippy passed. Layers, parity (627/627), panic hunt and release
quick performance checks passed. All 23 wasm checks and the release IO corpus
gate passed, including Photoshop import/render oracles, adversarial mutations,
smart-object PSD preservation and layered TIFF round-trips. Format has no separate
corpus target; its unit/integration tests and the IO round-trips cover persistence.

An additional live Z7 II check closed the host with an uncommitted RAW draft and
restored the new settings in a fresh process. Under the concurrent corpus workload,
it measured 14.3 s cold open, 37.2 s edited switch, 5.1 s cached return and 5.0 GiB
peak RSS. This confirms lifecycle correctness under load, not a faster latency target.

Promoted the tested executable as `target/debug/photocraft-host-raw-v1.exe`;
Image Triage's companion discovery selects it. Retained
`photocraft-host-0c05109.exe` and prior host builds as rollback options. Restart
Image Triage to start a new editor process with the RAW-capable host. Nothing
committed or pushed during this implementation step.

## Commit/push checkpoint (2026-10-08)

At the user's request, committed the validated PhotoCraft RAW implementation as
`91c7dba` on `codex/image-triage-integration`. Its push destination is the user's
`fork` remote (`tylermcm/photocraft`), on the same integration branch. Image Triage
is committed/pushed on `remove-builtin-editor`. No upstream push or PR is involved.

A fresh fetch at this publishing checkpoint found 36 upstream commits since the
validated `e5e3e39` base, ending at `dd55521`. Fork/main still has no unique work,
and Image Triage's remote branch has no new commits. The publishing checkpoint
keeps the tested snapshot; these newly arrived upstream changes are pending the
next development session's merge and validation. They touch smart objects,
control/menus, shortcuts, doc fields and IO as well as unrelated bug fixes, so
review shared hooks carefully. The promoted executable still represents the
validated RAW implementation on the earlier upstream base.

## Upstream integration update (2026-10-08, checkpointed; validation incomplete)

Fetched both remotes at the start of this session. Upstream now ends at `652b972`,
120 commits after our validated `e5e3e39` base; fork/integration matches `91c7dba`
with no unique work. Merged as `65fab5e` on `codex/image-triage-integration`.

Resolved four conflicted files individually:

- Desktop startup: retained hosted flag, offscreen hidden startup, no persistence,
  no taskbar entry and host-owned chrome. Adopted upstream's Unicode-safe argument
  handling, persisted title-bar preference, GPU startup changes and tablet setup.
  Hosted mode overrides standalone title-bar preferences and decorations.
- Smart objects: retained RAW sensor selection and embedded-only guard, alongside
  upstream's scale-aware vector smart-object rendering and existing child-tab reuse.
- UI manifest: retained one shared RAW/testgen dependency, with the new SVG import
  dependency/features from upstream.
- UI state: retained RAW source-development state plus upstream's initial RAW-open
  state, background histogram, blend preview and clone preview state.

Reviewed automatic merges in scoped I/O, authorization, job waiters/completion,
native Save binding, themes, menu routing, shortcuts and project format. Added
assertions that hosted RAW import does not queue upstream's standalone RAW dialog.

Important upstream changes: compressed NEF decoder (#1310), initial Camera Raw
Open/Cancel workflow (#1309), Edit Contents tab reuse (#1037), finite-float save
validation (#1172), SVG smart objects (#1305), async histogram and banded PSD
shuffles/export (#1185/#1230), plus many bounds/lock/shortcut fixes.

The native NEF decoder still documents unsupported lossy-after-split variants.
Our existing sensor adapter and saved projects remain supported while real Z7/Z7 II
coverage is reviewed. Upstream's initial RAW dialog produces a developed document;
it does not replace our persisted RAW smart-object settings. Native decoder adoption
can be assessed in the performance phase without silently dropping source state.

The live pre-update project check exposed a Save race in our hosted snapshot
restore: rebuilding the document state reset its revision to zero while the
background stash still owned the original revision. Save rejected that same
snapshot as conflicting edits. Fix `73cc2ff` preserves the snapshot revision;
a gated-writer regression verifies immediate Save reuses the pending job and
completion marks the restored revision clean.

The live scripts now verify the requested executable actually launched, preventing
a fallback host from passing candidate validation. The RAW script also accepts
existing project exposure settings instead of assuming every input starts at zero.
Python integration checks passed (85 tests and 7 subtests).

The broad native run also reproduced a GPU test false positive: a one-level
8-bit difference exceeded its tolerance by f32 rounding during alpha
premultiplication. Test-only fix `c9e94d9` keeps the one-level budget with four
f32 epsilons of arithmetic allowance. Its regression still rejects two levels
and NaNs; no compositor/shader code changed.

The new upstream eyedropper test expected the system crosshair in Precise mode
on every platform, conflicting with the existing Windows canvas-drawn crosshair
fix (#737). Test-only fix `0a3bbea` expects a hidden system cursor on Windows
while retaining Crosshair elsewhere; the existing canvas crosshair regression
still tests the platform drawing behavior. No cursor implementation changed.

Native workspace unit/integration checks passed across package batches after
the two test fixes: engine 831 passed/11 ignored; UI 931 passed/3 ignored;
GPU parity 28 passed. The full UI integration tests, web/vector crates and xtask
also passed. A concurrent color-managed GPU test process exited without an
assertion report; both cases passed serially, as did the remaining GPU-backed
UI tests. All-target workspace clippy, adversarial command panic hunt, formatting
and dependency layering passed.

Menu parity (627/627) and scorecard freshness also passed. At the user's request
to pack up, stopped the validation pipeline during the wasm checks. No final
corrected native candidate was built or promoted. The selected
`photocraft-host-raw-v1.exe` remains in place; the earlier transient candidate
`photocraft-merge-candidate-65fab5e.exe` lacks the snapshot fix and must not be
promoted.

Resume from PhotoCraft `0a3bbea`. Remaining gates: all wasm checks, native build,
release quick performance and full corpus, then the final live handoff. Repeat
ordinary 24 MP theme/hidden-start/fullscreen/navigation/Save/reopen/cleanup,
real Z7 II `4213.NEF`, and the pre-update project
`photocraft-raw/live-z7ii/.image_triage_edits/first.NEF.pcraft` using the corrected
candidate. Only then promote a byte-identical host build and verify discovery.
The initial merge candidate passed ordinary 24 MP and fresh Z7 II lifecycle
checks; old-project compatibility exposed the Save race and has not passed on
the corrected build yet.

Artifacts and validation logs are under
`C:/Users/user00/.codex/visualizations/2026/10/08/upstream-652b972`.
The remaining-workspace runner there uses the compiled Cargo artifact list and
serial execution for GPU-backed tests. About 53 GiB of disposable debug
incremental cache accumulated; reclaim it before further large builds if space
is tight, after confirming no compiler is running. Preserve host binaries,
test executables, corpus files and original camera samples.

At the user's explicit request, checkpoint push destinations are
`tylermcm/photocraft:codex/image-triage-integration` and
`tylermcm/ImageTriage:remove-builtin-editor`, including this handoff.
No upstream push or PR is involved. Step 1 is not yet closed out.


## 2026-10-13 — inspector wrapping and top-right control sizing

Host-only UI change: inspector sections keep their baseline heights but grow to
fit wrapped body content; the AI invitation also grows and its chips wrap.
Settings/update hit areas and window-control glyphs now follow the actual top-bar
height, including the 36px small-screen floor.

Session fetch: the companion checkout is on `scratch/dialog-curve`, HEAD
`ca2d32985370e486e3b98f3a1e25be471b3c2ba2`, with active user/Claude edits.
Its configured `origin` is tylermcm/photocraft, and `git fetch fork` failed because
there is no `fork` remote. Both repositories were then fetched directly into
`session-upstream/main` and `session-fork/integration`. Integrated upstream base:
`d43db3e153c76aefce9b32cef9099e458eaf17a8`; fetched upstream tip:
`99e1138877e6aa7e856c6883b415e708d042fc59`; fetched fork integration tip:
`ca2d32985370e486e3b98f3a1e25be471b3c2ba2` (no fork-only updates).
At the user's instruction to avoid Claude's active work, no companion merge,
conflict resolution, rebuild, executable promotion or push was performed.
Upstream integration and its full native handoff validation remain pending.

Validation: 56 tests passed plus 11 subtests across inspector, top-right sizing,
top-bar rebuild and theme suites, using registered Windows fonts for offscreen
Qt. Inspected the rendered inspector at 220px width with wrapped Nikon capture
metadata and the AI invitation; content stays within the pane. `git diff --check`
passed. Existing preview/bridge/autosave edits were left untouched.
