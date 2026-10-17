# Image Triage / PhotoCraft audit — 2026-10-14

The integration has nine actionable defects. Four affect preservation or faithful export of edits. Passing unit suites and the existing live checks do not cover these cases. This was an audit, not a repair or a release promotion; application sources and the hosted release executable were preserved.

## Revisions and method

Image Triage: `1f6ebcc17d0df8cfbc8d17ab39797458a2132c08` (exclusive) through `ac4693d2071d0089b2342b3dfc05aad5d6b95bd8`, on `main`. PhotoCraft: `c6092f867a4cb5b168077f7e558a170248392ec8` (exclusive) through `e0569ef00dd1c77d07d4f7ec02ef61588e497361`, on `codex/image-triage-integration`. Both trees were initially clean and both owner remotes were fetched successfully.

PhotoCraft's configured `origin` is the owner's fork, not storytold. `git fetch fork` failed because that remote does not exist. Direct URL fetches succeeded into `audit-upstream/main` and `audit-fork/integration`. The fetched fork tip equals `e0569ef0`. The integrated upstream base is `99e1138877e6aa7e856c6883b415e708d042fc59`; the newly fetched upstream tip is `7250e6e29a9248ac17bc0cb20296e03e7cbed86a`, 45 commits ahead. No fork-only updates await integration. No upstream merge was performed during this audit of fixed revisions; compatibility with those newer commits remains unvalidated.

Read both AGENTS files and the handoff history. Reviewed the scoped host diff, fork first-parent history and host delta against `99e11388`, concentrating on startup/services, control authorization and exchange ordering, document replacement, save jobs, recipes, renders, native window ownership, theme/resize/fullscreen, legacy edit readers, settings and packaging. Reviewed the upstream/fork overlap in shared integration files, including the redraw revert and the remaining compositor warmup and conversion code. The companion `../craftrules` checkout was unavailable. No subagents were used.

Reproductions use temporary files, fake controls for host bookkeeping, and real Rust application/control functions with synthetic DNGs for fork behavior. They intentionally assert the current faulty behavior; they are evidence scripts, not regression tests asserting desired behavior. The final Rust reproduction was copied temporarily into `crates/ui-egui/tests/audit_20261014.rs`, run, and removed. No product fixes, commits, pushes, upstream PRs or executable promotions were made.

## Findings

| ID | Priority | Defect | Primary location |
|---|---|---|---|
| F1 | P1 | An edit before the first post-Open poll becomes the unsaved baseline | `image_triage/preview_controller.py:1351` |
| F2 | P1 | Recipe-only adjustments disappear from grid thumbnails and exports | `image_triage/edit_storage.py:86` |
| F3 | P1 | Full-resolution export accepts the display JPEG and enlarges it to 16,384 pixels | `image_triage/edit_render_headless.py:195` |
| F4 | P1 | Failed recipe writes report success and discard the only copy of adjustments | PhotoCraft `crates/ui-egui/src/raw_recipe.rs:81` |
| F5 | P2 | Camera Raw document replacement invalidates native Save's host binding | PhotoCraft `crates/ui-egui/src/camera_raw_ui.rs:501` |
| F6 | P2 | Saved recipe previews do not use the dialog's raw-stage processing | PhotoCraft `crates/ui-egui/src/raw_recipe.rs:59` |
| F7 | P2 | Native Save leaves the display JPEG stale, and browsing prefers it | PhotoCraft `crates/ui-egui/src/control_files.rs:146`; host `photocraft_bridge.py:159` |
| F8 | P2 | Shutdown can return while background editor startup is still running | `image_triage/preview_controller.py:1575` |
| F9 | P2 | Live theme checks write the user's PhotoCraft preferences | `scripts/check_photocraft_handoff.py:193` |

### F1 — preserve edits made before rebaseline

After a RAW's Camera Raw dialog closes, `camera_raw_rebaseline` waits for non-stash jobs, reads the **current** document revision, assigns it to `opened_revision`, clears the flag and returns. It cannot distinguish the development revision from user edits made since development finished. The poll occurs on a 1.5-second timer and can also be delayed by queued work.

Reproduction: development ends at revision 1, the user edits to revision 2 before the poll, then autosave and leave both run. Both issue no `app.stash`; revision 2 becomes the baseline. Navigation can replace the document and lose that edit. The existing test `test_edits_after_open_are_saved_once_the_baseline_is_taken` edits only **after** explicitly running the baseline poll, so it excludes the failure. The live recipe check similarly waits multiple timer intervals before editing.

Remedy: have the editor report the completed development/continuation baseline or inspect its `saved_revision`/dirty state. Do not treat a later live revision as proof of unchanged content. Add an edit-before-first-poll test, including immediate navigation and quit.

### F2 — recognize the recipe workflow in all edit readers

`session_has_edits` recognizes a `.pcraft` project or legacy JSON operations/masks, but never a `.cameraraw.json` recipe. `ThumbnailTask.run` and `image_resize.apply_edits_if_present` both use that gate. The default RAW workflow intentionally writes only the recipe and display JPEG when leaving Camera Raw, so the common case cannot pass it.

Reproduction: a blue original, a valid nondefault recipe and a red display render produce `session_has_edits == False`; the thumbnail worker caches the blue original even under a nonzero edited cache key, and the convert/resize/workflow-export helper returns the original. The viewer's separate saved-render reader can show the red edits, creating inconsistent results across surfaces. The scanner's built-in-edit check also delegates to this gate.

Remedy: include validated, nonidentity Camera Raw recipes in edit detection, then route exports through a full-resolution recipe renderer. Merely opening or resetting a recipe to identity should not mark an untouched photo edited. Test a recipe without any `.pcraft` file through thumbnail and export entry points.

### F3 — keep browsing caches out of full-resolution exports

`full_render_path` falls back to the 2560-pixel display JPEG when a lean save has no current full-size PNG. `render_edited_image` accepts that fallback for a full-size request and decodes it with `QSize(16384, 16384)`. `_load_standard_image` calls `QImageReader.setScaledSize` without clamping to the source dimensions, which enlarges the JPEG rather than recovering its missing detail.

Reproduction through `apply_edits_if_present`: a 6000×4000 source and a 2560×1707 display JPEG, with a project present, return a 16384×10924 QImage. That is approximately 179 million pixels / 683 MiB at four bytes per pixel, created from a lossy browsing cache. An immediate export after autosave, or after a quit that leaves the render out, can therefore lose detail and bit depth and allocate far more memory than the source warrants. The original file remains intact; the defect is in the produced edited image. The fixed 16384 decode bound also merits checking for full-size PNG readers, not only the fallback JPEG.

Remedy: distinguish full-resolution materialization from browsing-cache selection. Render the saved project/RAW recipe at its real dimensions, or explicitly wait for a current full-size render. Decode without enlargement when no size was requested. Verify dimensions, detail, bit depth and profile on the export path, including after a lean quit.

### F4 — recipe writes are user data, not an optional cache

`raw_recipe::persist` returns `()` and only logs an error from the synchronous recipe write. `camera_raw_ui::done` clears `app.camera_raw` before calling it and returns success. `leave_in_place` and Open also call the same infallible persistence function. No error reaches Python's close guard, after-close error signal or save-error tracking.

Real Rust reproduction: inject a writer returning `disk full`, change exposure to +1, then send Done. It reports `open:false` and `changed:true` successfully; the dialog closes. The next visit has default exposure, because the adjustment is neither on disk nor retained in the document. The comment claiming retry on the next visit is incorrect: that visit loads the old recipe and has no copy of the failed settings to retry.

Remedy: return a recipe-write `Result`, retain the dialog/settings on failure, and surface the failure to the host before replacing or closing the photo. The JPEG can remain an optional cache. Cover full disk, read-only media and disconnected storage with a recovery test.

### F5 — transfer the host binding to the new document

`app.bind` stores `(DocId, project, preview)`. Hosted Save requires that ID to equal the active document's ID. Camera Raw redevelopment closes the browsing document and opens a new one; continuing a saved project also replaces it. Neither path transfers `host_target`, and Python does not rebind after the user presses Open.

Real Rust reproduction: bind a synthetic RAW, change exposure, Open, edit and invoke native Save. The new document ID differs and Save requests a Save As dialog instead of stashing under the granted host target. Repeating with `continueFrom` also bypasses hosted Save; in the test's deliberately minimal services it fails with `no exporter configured`, while the desktop has a regular native exporter. Large RAWs use reduced stand-ins and must redevelop even with default settings, so this is not limited to an unusual adjustment.

Remedy: transfer the binding during redevelopment and continuation, or send an explicit host rebind when the replacement completes. Test Ctrl+S after fresh RAW Open, reduced-RAW Open and saved-project continuation, including PNG and grid propagation. The existing handoff's Save check uses flat images whose document IDs never change.

### F6 — render saved previews with the same raw-stage math

The dialog's `render` applies `raw::retone` with the RAW tone curve for Shadows, Highlights, Clarity, Texture and Dehaze, clears those fields and then runs the remaining RGB adjustments. `raw_recipe::render_display` instead sends all settings directly through the RGB Camera Raw filter. It retains neither the curve nor the full-size context of the reduced RAW proxy.

Real Rust reproduction on a 48×32 synthetic DNG, shadows +75 and highlights −70: JPEGs produced from identical base pixels differ by mean **8.76 / 255 RGB code values**, using identical JPEG quality and color conversion. This isolates the processing mismatch from compression and reduced-resolution effects. The cached picture after leaving the dialog can differ visibly from the picture the user adjusted.

Remedy: share the dialog's raw preview pipeline with recipe rendering, passing the curve and original dimensions. Keep full-resolution output tied to the full RAW development. Compare saved renders with the dialog for each raw-stage setting, rather than asserting only that a center pixel became brighter.

### F7 — native Save must refresh or invalidate the display cache

Hosted native Save calls `stash`, which supplies only project and PNG paths. It never writes the display JPEG that autosave created earlier. `saved_render_path` nevertheless accepts a display JPEG up to 120 seconds older than the new PNG; this is a timestamp difference, not a timeout that expires while waiting. Python observes the native stash completion and can advance `opened_revision`, suppressing a follow-up autosave that might refresh the JPEG.

Reproductions: the real Rust native Save writer receives exactly project and PNG, no JPEG. The host selects an old blue display JPEG over a new red PNG written ten seconds later. That stale result persists until another write changes the timestamps, and affects viewer/grid browsing.

Remedy: bind a display target and refresh it in native Save, or invalidate/version the cached display image on a native stash. Do not infer equal revisions from a broad mtime tolerance. Test native Save after an earlier autosave using different pixel content.

### F8 — track background startup during shutdown

`run_deferred_editor_start` submits `_prestart_photocraft` but discards its Future. `shutdown_photocraft` returns immediately when `_photocraft` is still `None` and `_photocraft_future` is absent/done. A slow startup can therefore publish a running process after shutdown, without a shutdown request.

Reproduction: block the launch on an Event, invoke shutdown, release launch. `_photocraft` now holds the late process and its shutdown method has never run. Windows job ownership eventually kills the editor when the host process exits, but does not make the shutdown call orderly; executor threads can delay host exit. Development restart uses `os._exit` and follows a different teardown route.

Remedy: keep a startup Future, use a shutdown flag to prevent late publication, drain/cancel queued startup work and close any process whose launch finishes during shutdown. Test closing/restarting while startup is blocked, not only an already-running editor.

### F9 — isolate both programs' settings in live checks

`use_throwaway_settings` changes only Image Triage's registry key. The launched editor inherits the normal PhotoCraft profile unless `PHOTOCRAFT_CONFIG_DIR` is already set. The handoff cycles through five themes ending in Classic; PhotoCraft's `ui.set` calls `set_theme`, which runs `prefs.set` and persists `preferences.json`.

After the existing handoff run, the ordinary `C:\Users\Checkout\AppData\Roaming\Photocraft\preferences.json` reported `interface.theme == "classic"`. The prior theme was not captured, so this audit cannot assert whether that run changed its previous value. The code proves the test writes the normal profile and has no restoration. An isolated PhotoCraft config was used for subsequent live lean-save checking. The test should be safe even if interrupted, as the host registry isolation already intends.

Remedy: give every live check a private `PHOTOCRAFT_CONFIG_DIR` before launching, optionally copying a baseline profile into it. Do not restore a shared profile by overwriting it after the test, since another running editor may have changed it meanwhile.

## Validation and limits

- Full Image Triage suite at `ac4693d`: **1940 passed, 5 skipped, 11 xfailed, 238 subtests passed**, in 316.36 seconds, exit 0. Used Python 3.13.14, `QT_QPA_PLATFORM=offscreen`, explicit registration of the repository's `tests` package, `-X faulthandler`, verbose per-test logging and no pytest cache provider. Six warnings included a telemetry-thread exception trying to create a fake `\\nas\photos\...` path. No segmentation fault or reported move/undo failure occurred in this one run.
- Host reproduction script: six confirmations covering F1/F2/F3/F7/F8, including the actual thumbnail worker and export helper. All assertions passed.
- Rust reproduction integration tests at `e0569ef0`: F4/F5/F6/F7 confirmed through actual application/control functions. Initial harness iterations needed egui 0.36 API/font/texture setup corrections; those harness errors are not product findings.
- PhotoCraft UI library at `e0569ef0`: **2756 passed, 12 ignored**, no failures, 60.39 seconds after rebuilding its test binary with `-j 4`.
- PhotoCraft RAW library and integration tests at `e0569ef0`: **172 passed**, no failures. The library subset was also run separately (93 passed). `cargo xtask layers` passed: 29 crates, no layering violations.
- Existing live handoff at 1 MP, using the preserved desktop release executable from `9e94b0c0`: passed hidden start, embedding/fullscreen geometry, persistent navigation with zero host hides, five themes, native Save, grid notifications, closed-tab reopen, unchanged originals, token deletion and process cleanup. Inspected the native editor screenshot. Native screenshots were verified; desktop captures reported false on the background virtual desktop, so they do not prove a visible composite on the owner's active desktop.
- Existing live recipe check on Z 7II 0027/0030, same preserved executable: passed recipe persistence/restoration, RAW Open, editing/autosave, closed-tab return and saved-project continuation. The formerly reported first-adjustment assertion failure did not recur in this run.
- Existing live lean-save check on 45 MP Z 7II 0027, same preserved executable and an isolated PhotoCraft profile: passed. Autosave 5.1 s; close 3 ms; full render ready 4.5 s after close; same-process reopen 679 ms; close while busy 118 ms; quit 1129 ms. These are a single run under audit load, not a performance comparison.

Native checks used `IMAGE_TRIAGE_CHECK_DESKTOP_OF=claude.exe`; cleanup operated only on the checks' own process objects. No editor was killed by image name. Timings under audit/build load are not clean performance comparisons. The live tests validate the known-good binary, while the Rust tests validate `e0569ef0` source; this audit does not claim the desktop release executable was rebuilt from the final layout commit.

Not established: the root cause of the prior segmentation faults, the one exit-127 handoff, the intermittent undo test, the laptop first-save slowdown or its onnxruntime crash. The folder reload has an identifiable trigger (`_prestart_photocraft` creates the hidden edits directory; the parent-directory watcher schedules a force refresh), but no new real-app reproduction was performed here. It remains a separate follow-up rather than an additional confirmed finding.

Not run: the development-restart end-to-end path, a clean-PC MSI install/upgrade, the complete desktop/UI integration suites, wasm, clippy, corpus or full performance gates. Installer file selection/licensing and frozen executable discovery were reviewed, and the bundled-editor tests passed in the full host suite. The new upstream tip was fetched but not merged, built or promoted. These limits prevent treating this audit as a release certification.

## Reproduce

From Image Triage, run `python docs/audit_20261014/reproduce_host_findings.py`.

For the fork, copy `reproduce_fork_findings.rs` into `crates/ui-egui/tests/audit_20261014.rs`, then run `cargo test -j 4 -p photocraft-ui-egui --test audit_20261014 -- --nocapture` and remove that temporary copy. Run one Cargo command at a time. The reproduction tests confirm defects; they are expected to require changes when the defects are repaired.

Raw logs and live artifacts for this session are under `C:\Users\Checkout\AppData\Local\Temp\image_triage_audit_20261014`. The handoff document records the final gate results and repository state.
