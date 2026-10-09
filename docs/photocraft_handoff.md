# PhotoCraft handoff — remove-builtin-editor

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
`C:\Users\tylle\Documents\photocraft`; `cargo build --release -p photocraft`; set
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
`C:/Users/ADMIN2/.codex/visualizations/2026/10/08/photocraft-raw/`.

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
`C:/Users/ADMIN2/.codex/visualizations/2026/10/08/upstream-652b972`.
The remaining-workspace runner there uses the compiled Cargo artifact list and
serial execution for GPU-backed tests. About 53 GiB of disposable debug
incremental cache accumulated; reclaim it before further large builds if space
is tight, after confirming no compiler is running. Preserve host binaries,
test executables, corpus files and original camera samples.

At the user's explicit request, checkpoint push destinations are
`tylermcm/photocraft:codex/image-triage-integration` and
`tylermcm/ImageTriage:remove-builtin-editor`, including this handoff.
No upstream push or PR is involved. Step 1 is not yet closed out.
