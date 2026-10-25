# PhotoCraft handoff

## Upstream v0.16.0 merged on a scratch branch — 2026-10-25 (not promoted)

Upstream cut a release, so this is the one merge for it: `d95fdf318` (v0.10.0) to
`b1794c6e0` (v0.16.0, 3115 commits), merged into `codex/image-triage-integration`
(`0988078e4`) on **`scratch/upstream-merge-2026-10-25`**: merge commit `dcac9b1df`,
then post-merge fixes `d18743cd9`. Pushed to the fork (`tylermcm/photocraft`) as that
branch only. `codex/image-triage-integration` is still `0988078e4` on the fork and
in the checkout; nothing is promoted, and nothing went to storytold.

31 textual conflicts, decided per file (the merge commit message has the list):
upstream's clap `launch_args` (the host's `--hosted` and `--raw-workspace` declared
there); upstream's off-thread Camera Raw preview (`ProxyJob`, `request_render`,
`poll_render`) replaces the fork's preview worker, which is deleted, and
`ProxyJob::run` calls `raw_recipe::apply`, the one definition shared with the recipe
render; the section "before" (`preview_without`), RawOpen fields, proxy-ahead,
swap-in-place and the workspace panels stay as host behaviour; upstream's 16-bit and
colour-managed GPU tile conversions are taken (the fork's duplicate lookup-table
path is dropped); `control` keeps `app.bind`, `app.render` and `app.stash` and takes
upstream's `app.save`; `i18n/mod.rs` is upstream's and the 20 `.tsv` catalogs are the
union of both sides with no key lost; the engine presets test is upstream's.

After the merge, compile and test fixes only: `developed_document` and `describe`
arguments, the `json!` recursion limit in `control`, `RowGestures.curve`, the moved
`hue_color`, the proxy `Arc` types, the `ImportOptions` callback and `RawOpen`
fields. The Camera Raw workspace tests wait for the asynchronous preview, and Super
Resolution is a recipe setting here (toggled and restored in its test). Four clippy
lints of the 1.99 toolchain in fork files were fixed.

Validated on the isolated target dir `target/merge-v0.16.0` (the known-good
executables under `target/release` and `target/acr18` were not rebuilt):

- `cargo clippy --workspace --all-targets -- -D warnings`: clean.
- `camera_raw_workspace`: 11 passed. Earlier in the merge: engine presets 79, app
  binaries 172 and app usage 8 passed.

**Not done:** the Image Triage handoff checks against the candidate
`target/merge-v0.16.0/debug/photocraft.exe` (persistent filmstrip navigation,
stash/restore, native Save and grid propagation, closed-tab reopen, hidden startup,
theme and resize, fullscreen, shutdown and process cleanup), and a full
`cargo test --workspace` in the final state. A merge without textual conflicts is
not proof of compatibility, so the candidate stays unpromoted until those pass.
Run it with `IMAGE_TRIAGE_PHOTOCRAFT_EXE` pointing at the candidate.

## Local Image Triage testing — 2026-10-17

Owner requested the new Camera Raw UI inside Image Triage for local testing,
explicitly without a release build. Both repositories remain on
`codex/image-triage-integration`; companion fetches succeeded again and upstream
and fork tips remain `5f65579e` and `00c154c4`, already accounted for below.
The existing dev executable `target/acr18/debug/photocraft.exe` is unchanged.

Added `scripts/test_camera_raw.ps1`. It launches this host checkout from source
using the development executable through a process-local override, loads live
`design/camera-raw/layout.json` and `tokens.json`, and gives the editor a separate
`target/acr18/image-triage-test/editor-profile`. Logs are in that test root's
`logs` folder. It restores the invoking PowerShell's environment after launch.
Optional `-OpenPath` accepts a photo or folder; `-PhotoCraftRoot` selects another
companion checkout. The launcher reports the selected editor and live JSON paths.
PowerShell parsing, path quoting, exact editor selection and environment
restoration were verified. No global discovery, installed editor or release
packaging changes.

Live checks passed against this exact dev executable with live JSON enabled:

- `check_photocraft_handoff.py`, both 1 MP and 24 MP: hidden startup, native
  parent, fullscreen, persistent filmstrip/navigation with zero host hides,
  stash/restore, native Save, grid propagation, closed-tab reopening, five
  themes with verified native/desktop captures, shutdown and token/process cleanup.
- `check_camera_raw_first.py`: adjustment persistence when navigating, return
  restoration, Open into the full editor, and Camera Raw return after tab close.
- `check_camera_raw_recipe.py`, pinned public Nikon D3 and Z6 samples: failed
  recipe writes keep drafts, recipe-only exports match live full-size results,
  first-poll edits persist, Open continuation and native Save both work.

Inspected the native Camera Raw capture from the hosted test. Evidence is under
the session artifact directory's `hosted-dev` folder. These are local development
handoff results; full native/wasm/release gates remain unclaimed.

An owner's screenshot then exposed a separate stale app: `ImageTriage-main` on
`main` was still running with an explicit old `target/release/photocraft.exe`
override. That binary treated `--raw-workspace` as a filename. Requested a normal
window close (no force termination), confirmed the stale app/editor exited, and
relaunched this checkout's test app on the synthetic fixture. Verified the running
test process's checkout, exact development-editor override, live layout and
private editor profile. Created Desktop shortcut **Image Triage - Camera Raw Test**
pointing at the new launcher so normal shortcuts don't reintroduce that old
override. Press E in the popout to enter Camera Raw. The known-good release
executable retains SHA256 `EA42E1B82F1346091E18C210BD821194AD99CC446CB2FB9425108443049030C5`.
No push, release build or promotion.

## Camera Raw JSON preview rebuild — 2026-10-17 (preview open)

Owner clarified that this request is to compile/launch the PS1 Camera Raw preview
and see the live JSON mockup. Stopped only this session's release-build, native
UI-test and full-host-test process trees; no full-suite/native result is claimed.
The source integration below was already complete. Hosted promotion and live
handoff checks are outside the clarified scope. Reusing the normal debug cache
for compilation, copying the result to `target/acr18/debug/photocraft.exe`, then
restoring the original ordinary debug executable and timestamp before launching
`scripts/preview_camera_raw.ps1`. This keeps the preview out of host discovery.

Both checkouts are on `codex/image-triage-integration`. Host fast-forwarded
`d2d0f08` to fetched `27b8bb3` (handoff documentation only); pre-existing
`main.py`, Qt logging, strategy/plan and screenshot changes are preserved.
Companion is `C:/Users/user0/Documents/photocraft`; the literal `../photocraft`
beside this OneDrive host checkout does not exist. Its remote names differ from
the protocol examples: `upstream` is storytold, `origin` is tylermcm's fork.

Initial fetches failed with `getaddrinfo() thread failed to start`. Restoring
missing Windows system/profile environment variables fixed networking; fetches
then succeeded for all three remotes. Fork integration matched local
`00c154c46d4b8bc859aa07548224138d0e9a7c42` with no fork-only incoming work.
Upstream advanced 71 commits from
`b630d7cb9aeb2a79ab5224ebf6e8266f1d858873` to
`5f65579ecb44047c2a619036c18ff273202e1389`, merged as
`91270cce2acea35ec6a51e026eb2b08a7752ef3c`. Pre-merge source is retained on
`codex/pre-host-build-20261017`. No push.

Conflicts reviewed and resolved individually:

- `apps/photocraft/src/main.rs`: keep hosted/raw-workspace flags and hidden
  native-window behavior; adopt upstream single-instance support. Both host
  flags count as process-specific options so they never forward to a standalone
  editor; control launches also retain upstream's own-process exemption.
- `crates/ui-egui/src/gpu_canvas.rs`: adopt upstream adapter VRAM and shared
  canvas-budget fields, while retaining the hosted off-thread compositor warmup
  and CPU fallback. Upstream does not supply that warmup behavior.
- `crates/ui-egui/src/widgets.rs`: retain Camera Raw fixed-decimal and left
  alignment controls; apply upstream's numeric-input length guard before parsing.
- All 20 `crates/ui-egui/src/i18n/*.tsv` catalogs: keep independent Camera Raw
  and upstream GPU/Save translations. Checked each block's keys for overlap;
  none collide. No whole-file ours/theirs selection.

Reviewed automatic merges in desktop services/startup, control/files, jobs,
menus, state, theme/window integration and automation authorization. Adopted
upstream recovery-directory policy, Save disk-change prompts, GPU budgets and
token-file checks. Bound hosted Save still routes through `save_bound` before
ordinary standalone Save prompting; scoped host I/O, stash/restore and host
theme hooks remain. The previous fork pixels fix already matches upstream, with
no remaining `pixels.rs` delta. The companion's private `../craftrules` is absent.

The release compile caught a semantic automatic-merge issue in the independent
preset-set loader: its new `Persisted` initializer omitted the fork's export
presets. Restored that field and extended the damaged-set preservation test
(`77914c86`); formatted merged GPU initialization in `516b6d2b`. These tests
await execution. Final source is `516b6d2b4e42f12b449040589e54a14f2e207290`.

Validation so far: final rustfmt and Git whitespace checks pass; focused host
regressions **188 passed, 1 skipped**. The initial release build exposed a missing
MSVC linker environment, repaired using the installed VS 2022 Preview toolchain.
Installed the missing wasm target, but no wasm result is claimed. The release
retry and broader tests were stopped at the owner's clarification. The active
preview build is `cargo build -p photocraft -j 6`, using the existing debug cache.
No hosted candidate has been promoted.

Known-good `target/release/photocraft.exe` SHA256:
`EA42E1B82F1346091E18C210BD821194AD99CC446CB2FB9425108443049030C5`.
Owner's `%APPDATA%/Photocraft/preferences.json` starting SHA256:
`C9BD660D82FA1772B6881A02AEBD145792EF57F141A02395BDD6C11E5AFEF46E`.
Logs/scripts are under
`C:/Users/user0/.codex/visualizations/2026/10/17/01a14901-444b-7781-b5e6-48730a8f5183`.

Preview build passed in **5m10s**. Launched via the companion PS1 using the
synthetic color fixture and separate preview profile; running PID **18464**.
Isolated executable: `target/acr18/debug/photocraft.exe`, SHA256
`E5464F3DD48B2AD95A96FAD6630511DDC8DEFACECA1B2684D00BCE5C54B40930`.
Authenticated control inspection confirms Camera Raw is open and every supplied
layout value, including the live preview preset, matches `layout.json`.
Inspected `target/acr18/preview.png`: native mockup is visible with the current
Light/Color/Effects layout. This is preview verification, not ACR parity or
hosted-handoff certification. Leave this window open for live JSON work.

The ordinary debug executable was restored byte-for-byte, including its original
timestamp (SHA256 `B7447438BD9459FF61C3E0BE89D1BACF65DEC911FDD60DBF67D2D4CFCF6F3B53`).
The ordinary release executable remains byte-identical to the starting hash.
Normal `scripts/preview_camera_raw.ps1` now opens the isolated preview without
compiling. No design JSON was modified in this build session, and nothing pushed.

## Keep visual iteration in JSON — 2026-10-15

Owner clarified that this design pass should use live JSON and the PS1 launcher,
without native rebuilds. Stopped only the owned Camera Raw test-build tree before
results; no test pass is claimed. Preserved the tentative native numeric-alignment
patch (including tests) outside Git at acr-parity-overlays/
deferred-native-field-alignment.patch and restored widgets.rs to the committed
source. The existing preview process remains open; its executable was not rebuilt.

The companion PS1 now reuses the isolated debug executable, or target/debug when
that isolated build is absent. It invokes Cargo only with explicit -Build; a
normal launch with no available executable reports the one-time requirement.
PowerShell parsing passed, and a dry launch with Cargo set to throw proved that
the normal path selects the existing executable without compiling or starting a
duplicate preview. Inactive numeric left alignment remains a native backlog item;
the current JSON option cannot correct that widget behavior by itself.

## Overlay design resumed on laptop — 2026-10-15

The merged native development build (38038711) passed in 33 min 27 s. Launched
it from target/debug with separate comparison settings and the synthetic image;
closed the pre-redesign base at the owner's clarification. The owner then
explicitly resumed design work with 19 local ACR 18.6 reference screenshots,
superseding the temporary instruction to make no changes after the build.

Compared source underneath PhotoCraft, including a corrected registration:
anchor the entire 540px settings dock, not its 512px inner expanded content.
The latter normalized to our 270-point full dock hid the reserved-scrollbar
width and enlarged the reference about 5%. Uniform 0.5 reference scaling now
keeps the real difference visible. Source images remain outside Git.

Companion live JSON now uses 40pt section headers, 31pt slider-row pitch and
adjusted insets/field metrics. At normalized width 540px all six Light tracks
match reference vertical bands: 126–131, 188–193, 250–255, 312–317, 374–379,
436–441. Before, offsets increased from 8 to 26px. This verifies those track
positions only, not complete visual or functional parity. Inactive numeric
text still centers despite the left-aligned option; that needs a native fix.
Handles, header glyphs/bypass, scrollbar behavior, Curve/Grading selectors and
panel-specific geometry remain. Optics, Lens Blur, Calibration, several rail
tools, RAW WB/Kelvin, profiles, filmstrip and ACR dialogs remain separate work.

Added an interactive index.html to the companion comparison script: reference
underneath, opacity/blink/uniform zoom, difference and alignment metadata.
Five native view-state before/after captures retained photo parameters and
preview revision in the same running process, without recompiling. Python/JSON
syntax, viewer/image HTTP delivery and git diff --check passed. No new native
code changes or hosted promotion. The native preview stays open on Light/Color.

Artifacts: the chat visualization directory's acr-parity-overlays/ contains
reference hashes, cropped overlays, reload-proof.json, light-metrics.json,
initial/captured states and gap-notes.md. Local overlay viewer is served on
127.0.0.1:52180 (private helper; it can be stopped after review). It is a design
workbench, not a product dependency. No push.

## Laptop pull and comparison preview — 2026-10-15

Fetched both user origins successfully and PhotoCraft upstream by URL (this
checkout's origin is tylermcm; no fork remote is configured). Fast-forwarded host
127c7ca -> 6f51b78 and companion 242daf70 -> c00bd4aa, preserving the ACR 18.6
redesign. Existing untracked codex_briefing/ was untouched. Upstream advanced
1b666f4790f9f2e9907218a8e786a3af5305f200 ->
b630d7cb9aeb2a79ab5224ebf6e8266f1d858873, merged as
38038711. No pushes.

Conflicts: apps/photocraft/src/main.rs, crates/ui-egui/src/control.rs and all
20 translation catalogs. Main retains hosted/raw-workspace flags and upstream
presentation/trace options; control retains both the host-theme regression and
upstream trace regression. Catalog additions are independent and both retained.
Reviewed automatic merges in lib.rs, menus.rs, GPU, engine jobs and authorization:
upstream owns presentation throttling, engine shell-command catalog and stricter
stored-filter authorization/authentication deadlines. Host hidden startup,
geometry/chrome, Save routing, workspace/design modules and host controls remain.
No equivalent upstream ACR redesign replaces the fork's new modules. Rust syntax
formatting and git diff --check passed; native compilation is in progress.

The immediate request is an isolated visual preview, not hosted promotion.
Building and launching a development executable from the existing target/debug
cache (separate from release executable discovery). The base
comparison uses the already-built pre-redesign 8ddc0ffd candidate, rather than
claiming it is the exact parent c00bd4aa^ or current upstream. Both use the same
synthetic image and separate temporary comparison profiles. No personal image,
normal preferences or known-good hosted executable is modified. Further source
and native handoff gates remain required before promotion. The owner requested
no further changes after this build; only launch the redesigned preview. The
base comparison window was closed at the owner's clarification; its captured
image remains available for comparison without another visible base window.

## Source sync and ACR 18.4 reference audit — 2026-10-15

Both working trees were clean before this session. Fetches succeeded for Image
Triage `origin`, PhotoCraft upstream `origin` (storytold), and PhotoCraft `fork`
(tylermcm). No remote changes were pushed.

- Image Triage started on `remove-builtin-editor` at `9601f2a`; fast-forwarded
  that branch to fetched `a4caa3a`, then checked out the newer remote integration
  branch as local `codex/image-triage-integration` at
  `127c7ca187126555916fc845672e4995a31c5357`. The previous branches/commits are
  retained. The integration tip includes `origin/remove-builtin-editor`.
- PhotoCraft started at `0a3bbea1` on `codex/image-triage-integration` (last
  merged upstream `652b9726`). Fast-forwarded to the user's fetched integration
  `242daf70ae47baec19584420831c3fdbef3049c2`, including the fork's hosted RAW
  workspace, recipe/render, lifecycle and audit work. Its upstream base was
  `4446ec6d65049b92a1b73f02e79d97ef4153135a`.
- Fetched upstream advanced that base by 18 commits to
  `1b666f4790f9f2e9907218a8e786a3af5305f200`; merged with a merge commit,
  `fa8a1f218b00879d14867f17cec2397b18fbf5d0`. The merge had no textual conflicts.
  All 47 fork/integration-only commits relative to this upstream remain in the
  ancestry; no wholesale ours/theirs resolution or history rewrite was used.
- Reviewed incoming automatic merges and the retained integration differences
  in desktop startup/services, control I/O/authorization, jobs, menus, themes and
  native window handling. Incoming services changes add PDF to Save As and its
  test; startup, host Save routing, authorization, jobs, menus and theme/window
  integration have no new upstream changes in this 18-commit merge. Retained
  hosted hidden geometry/no native caption, scoped bound Save, RAW workspace
  handling and snapshot bookkeeping. Incoming shared GPU/IO/RAW changes still
  require native/corpus validation; textual review is not compatibility proof.

Validation: the focused Image Triage integration run passed **273 tests** in
21.63 seconds using Python 3.13 and offscreen Qt. It covered handoff mocks,
Camera Raw recipe/first-open, first editor key, prestart, full render, lean
autosave, filmstrip no-flash, control robustness/idle, manifests and bundled
editor detection. `git diff --check` passed in both repositories. These are
unit/regression results, not live handoff results.

An isolated `cargo check -p photocraft-ui-egui -p photocraft -j 2` was started
under `target/acr18` (dev debug=0, incremental=false). Dependency downloads and
compilation began; it was deliberately stopped while establishing the missing
18.4 visual reference, before producing a check result. Native tests/clippy,
wasm/layers/corpus, candidate build and live handoff have **not** been completed
in this session. No candidate was promoted, and the hosted executables remain
unchanged. The companion's referenced `../craftrules/README.md` is absent on this
machine; the available companion AGENTS.md and public docs were read.

The owner selected **ACR 18.4, interface and functional behavior**. Adobe's current
overview page links an image explicitly labeled Camera Raw 13.3; the owner
identified it and it is rejected as a redesign reference. No UI implementation
changes were made from that image. Versioned Adobe 18.4 release notes were
consulted; living documentation mixes later-version features. The source audit,
reference gate, outstanding functional gaps and acceptance order are recorded in
[`acr18_parity.md`](acr18_parity.md). A verified 18.4 visual baseline is still
needed before changing layout or claiming visual parity.

## Supplied Camera Raw screen references — 2026-10-15

The owner supplied 19 local image files covering the whole workspace, edit
sections, Crop/Remove/Masking/Red Eye, Snapshots/Presets/Info, grid/sampler,
settings menu, Save Options and Preferences General. Inventory:
[`acr_reference_inventory.md`](acr_reference_inventory.md). These are visual
reference data; no source image or personal metadata was copied into the repo.

The full-window title says Camera Raw 18.6; Preferences identifies 18.6.0.2698.
This differs from the earlier selected 18.4 target, and a target-version
clarification is pending. The dark JPEG has no readable interface. The remaining
captures provide enough reference coverage to begin the main workspace; hidden
states can be verified as their features are implemented. No redesign code has
been applied while the exact target version is being clarified.

The isolated target/acr18 native source check was resumed at four Cargo jobs;
its result will be recorded when available. This is compilation only and does
not authorize promotion or substitute for the live handoff gates.

## ACR 18.6 native work and live JSON — 2026-10-15

The owner confirmed using the supplied ACR 18.6.0.2698 captures instead of 18.4,
and requested live JSON design edits instead of recompiling for each visual
change. No further access to a reference version is required to start. Source
work remains on both repositories' codex/image-triage-integration branches.

Companion work in progress adds a separate rail/navigation strip, full-width
section headers with multiple/single-panel behavior, interactive grading wheels,
per-color HSL and RGB point-curve channels, compact Effects/Detail controls,
correct nonzero slider resets, Reset to Open/Default and RAW Done routing. These
use the existing filter parameters and processing; unsupported ACR tools are
not represented as enabled no-op controls. Full ACR parity is not complete.

Live design files are companion design/camera-raw/layout.json (geometry,
spacing, typography, panel order and preview selection) and tokens.json (the
existing native debug live-theme loader). A new bounded/validated layout loader
retains the last good layout on invalid writes. Drawing and ui.viewport share
that layout, including the host's loading overlay geometry. External reload is
native-debug-only and explicitly enabled by PHOTOCRAFT_CAMERA_RAW_LAYOUT_FILE;
release/wasm and normal launches use embedded defaults. The JSON accepts view
state, not executable actions or photo adjustment values. The preview preset
only reapplies when its JSON value changes.

The one-time native preview build is isolated in target/acr18, outside Image
Triage discovery. scripts/preview_camera_raw.ps1 opens it with a separate profile
and generated synthetic fixture; visual changes then reload without rebuilding.
No installed/hosted executable is replaced. Native checks, visual inspection and
a same-process JSON reload proof are being completed before delivery.

The first affected integration run passed histogram/localization and 19 of 21
scope tests. Two scope regressions exposed an inherited header height in body
controls and a test's fixed 260px curve assumption. Body spacing was restored;
the curve test now derives coordinates from the actual graph. Final rerun is
pending; do not record those preliminary failures as a passing suite.

## Live JSON proof and native filmstrip direction — 2026-10-15

Built isolated native debug target/acr18/debug/photocraft.exe. Its live preview
passed same-process geometry (270 to 330 panel width), panel-selection and theme
reload checks, invalid JSON fallback, unchanged photo parameters/preview revision
and unchanged executable SHA-256. Both source JSON files were restored afterward.
The synthetic-fixture screenshots were visually inspected. The native preview
remains separate from the hosted executable; no editor build was promoted.

The owner favors a native PhotoCraft filmstrip. Source inspection found no
filmstrip implementation in the fetched companion tree. Its RAW switching and
prefetch infrastructure exists; the current strip is host Qt Filmstrip in
image_triage/ui/preview_studio.py. Proposed ownership and migration acceptance
are recorded in filmstrip_native_plan.md. Native presentation should retain the
host's browse order, selection/metadata and stash/restore/Save pipeline. Do not
remove the docked strip before replacement validation or display duplicate strips.

## Slider fidelity correction and validation follow-up — 2026-10-15

Owner identified the generic circular handles and excessive spacing, and
reaffirmed closest-possible 1:1 parity as the acceptance standard. Added compact
Camera Raw-only slider metrics, upward-facing handles and gray-to-light tracks;
exposed density/handle controls in live layout JSON. Other editor sliders retain
their existing rendering. Fixed decimal display for Camera Raw fields is separate
from the general editor's trailing-zero formatting.

The isolated native preview was rebuilt and the live reload proof repeated.
Selected workspace 4, zoom 6, scopes 21 and RAW workspace unit filter 60 tests
passed before the last visual correction. Native all-target clippy passed before
that correction. Layering passed (30 crates); wasm passed all 24 checks with
existing dead-code warnings. The first broad parallel UI run exited with Windows
STATUS_ACCESS_VIOLATION. Serial audit of that binary completed without the native
crash: 3030 passed, 13 ignored, two localization failures, two known new tests
excluded. Translation coverage and test texture cleanup were corrected; final
serial UI, selected integrations and final clippy are pending. No hosted promotion.

## Overlay comparison and full UI checkpoint — 2026-10-15

The owner requested regular source overlays as the visual acceptance workflow.
Added companion scripts/compare_camera_raw.py: explicit matching crop anchors,
uniform scaling only, 50% overlay, absolute difference and alignment metadata.
Outputs and reference crops remain in the local visualization directory, outside
Git. The first Light/Color overlay exposed header/chevron indentation, generic
track-end insets, row pitch and section-bottom spacing. Added JSON controls for
those metrics. The tuned Light track centers measured within one normalized
pixel of the supplied 13289 reference; this is not a whole-interface parity claim.
White-balance selector/Kelvin behavior and other known visual/functional gaps
remain open.

Full serial UI suite checkpoint: 3034 passed, 13 ignored, no failures. The 35
focused Camera Raw integration checks passed. An intermediate geometry revision
exposed two real curve-grab regressions: scrollbar appearance resized the graph
when selected-point fields appeared. Made settings scrollbars float so overflow
cannot change curve width; both existing capture regressions now pass, as does
the complete focused run. Native all-target clippy passed again. Final preview
capture and wasm recheck are being recorded below; hosted handoff/promotion remain
pending and the known-good executable has not been replaced.

## Final native visual proof — 2026-10-15

Rebuilt target/acr18/debug/photocraft.exe after the floating-scrollbar fix.
SHA-256 971bfc1cb3b34947fc2342a86ac70d47010ce7e27742356d190af671b24b870f.
Repeated same-process layout, theme, panel selection and invalid-write recovery
checks; photo parameters and preview revision stayed unchanged and both source
JSON files were restored. Captured and visually inspected the final Light overlay
against 13289.png. Reference crop (38,594,550,1092), preview crop
(1134,211,1404,474), uniform normalized width 270. The reference and native Light
section borders, row pitch and header indentation now closely align; eye/bypass
controls and exact glyph/field/track details remain visible differences. Full
18.6 visual/functional parity is still incomplete.

Native all-target clippy passed on the final source; wasm UI check passed with
pre-existing dead-code warnings. Final repeated focused run passed all 35 tests, including both curve-capture regressions.
No hosted build was promoted, no remote was pushed, no personal reference image
was bundled. Native filmstrip remains the preferred migration plan, not an
implemented replacement.

## Laptop validation resumed - 2026-10-14 (candidate not promoted)

Started from host audit branch `ea41cc6`; local host changes are on
`codex/image-triage-integration`. The fork's audit tip `c105a3f7` is included
in local integration `6b841d56` (upstream `7bb10a77`); its four conflicts took
upstream's Nikon decoder and Posterize/Threshold implementation/tests, while
keeping hosted control modules alongside upstream's corner-radius module.
That source passed rustfmt, desktop cargo check and layers. All user work and
the untracked `codex_briefing/` folder were preserved; nothing was pushed.

Fresh fork/upstream fetches succeeded. Upstream advanced another 15 commits,
`7bb10a77` -> `fd28da56`, merged without conflicts as `8ddc0ffd`. Reviewed native
startup/runtime icon and UI automatic merges: hosted hidden-start setup remains
after standalone geometry decisions; native chrome is still disabled for hosted
mode. The other incoming changes are upstream UI fixes, including modal titlebar
dragging, layer-scroll state and proxy preview resolution. No host authority
or Save-routing change was introduced. The earlier fork-only fixes are included.

This laptop has no `target/audit-20261014` candidate from the other machine.
Building the current merged source there with the normal release profile and
four Cargo jobs. The wasm target was installed to verify the inconsistent prior
wasm notes. A fresh Python 3.11 test venv is under LocalAppData/Temp; full host
suite started with offscreen Qt and registered Windows fonts. Live checks will
run on a private non-input Win32 desktop (native Qt HWND preflight passed), with
private editor profiles and desktop capture disabled. They use copies of the
available 12 MP NEF plus synthetic 1/24 MP photos. No owner's active desktop is
captured. Only check-owned processes are cleaned up.

Baseline hashes on this machine (different from the Checkout desktop):
- `target/release/photocraft.exe`: `3EB062191F360AACF33BA4D27430B942FE0B595F3FEB8DFA6477388E3842B1D4`.
- Normal PhotoCraft `preferences.json`: `0C6081BB0790A5E0F56210C7087365FD277D421FD0D3EE443755BB7D549280E4`.

Host coverage: all 2,016 collected nodes accounted for across the interrupted
monolithic run, the isolated grid module and five fresh-process batches: 2,002
passed, 11 xfailed, 3 failed; four additional collection skips. The failures are
unchanged tests assuming Python 3.13 AI lock files (two) or os.listdrives (one,
not present in Python 3.11). Audit regression tests passed. The monolithic run
stalled in thumbnails._drain_results at thumbnail_failed.emit after about 51%;
two py-spy stack dumps agree. Stopped only its PID 17276. That grid module passed
all 11 tests alone. This is combined coverage, not a successful single full run.

The old executable baseline handoff failed safely at full-resolution export
because that binary lacks fullRender. Its owned process cleaned up; the owner
editor (PID 18904, parent 10840) was left alone. The private-desktop runner now
uses an outer cleanup job too. All pinned test corpora downloaded and verified.

Candidate built from 8ddc0ffd with the normal release profile in 35 min 41 s:
SHA-256 A714F5611D36E9F792B72FAC940BA123A4E79C7E97C72998C48E7BD1199B1C1B,
PhotoCraft 0.9.0. Hosted native gates passed: handoff at 1/24 MP (zero editor hides,
menu Save, grid propagation, reopen, five themes, hidden start, full-screen sizing
and cleanup), Camera Raw first, recipe persistence and failed-write retention,
full-size live/disk recipe equality, edit-before-first-poll, saved-project
continuation, native Save after continuation, lean save/close/reopen, hidden
16-bit ICC render, cancellation during rendering and shutdown during a real launch.
The NEF fixture is 12 MP (two copies for navigation), not the other machine's
45 MP Z 7II samples. Native screenshots were inspected; no desktop capture.

Fixed three check scripts rather than changing the editor for stale assertions:
the handoff used blocking Future.result on Qt's thread (24 MP native Save
replied only when shutdown resumed message pumping); it now pumps while waiting
with the same deadlines. The recipe check now accepts a synchronous cached
continuation only when its returned path names the expected project, as well as
an asynchronous continuation; its native Save also pumps events. The full-render
check accepts either of LibRaw's exact visible/cropped or sensor extents, reporting
both: this NEF exports 4320x2868, while LibRaw's visible crop is 4312x2868.
Depth, ICC, cancellation, output cleanup and original checks remain required.
All affected native checks were rerun successfully on fresh output folders.

Separate limitation: the initial standalone --raw-workspace smoke, given an
unbound NEF, timed out waiting for Camera Raw to close after Open. This path is
not the host's bound RAW open; it remains unresolved. The intended raster
workspace smoke passes; the hosted NEF recipe/Open/continuation path passes.
Normal PhotoCraft preferences still match this session's baseline.

Final source clippy (selected host/raw/IO/engine/UI crates, all targets) and
layers passed. All 24 wasm checks and the RAW Rust suites also passed. The
serial source runner is compiling/testing IO, then engine, UI (print tests
separately and serially), desktop, GPU warmup, panic hunt, corpus and quick
performance. GPU crate tests and GPU/compose Clippy remain to be run after that
runner; they were added after its argument list was selected. No candidate
executable has been promoted. Logs and
the isolated desktop runners are in
`C:/Users/owner/.codex/visualizations/2026/10/15/01a13d25-9afa-7c50-8c2e-28d668c0d556/audit_validation`.

## 2026-10-14 continuation — validation in progress

Resumed Claude's scratch branch at `35e84fb3fc042115e11bd0474440f7db870e6132`
and the host at `2ab676b580bdaa33665149acf5ea343049019e56`. Preserved the working
tree and its uncommitted exports work; a patch and original copies are under
`C:/Users/Checkout/AppData/Local/Temp/image_triage_resume_20261014`.

Fetched the host origin and companion's configured origin successfully. The
companion origin remains tylermcm's fork; `git fetch fork` failed because that
remote is absent. Direct upstream/fork fetches succeeded into
`session-upstream/main` and `session-fork/integration`. Fork tip remains
`e0569ef00dd1c77d07d4f7ec02ef61588e497361`; no additional fork-only work.
Upstream advanced from Claude's merged `c699e3cd` to
`620bccef42b389d0735e753eb8a2f688a7092045` (64 commits). Merge `45933f08` stays
on the scratch branch until candidate validation passes.

Conflicts: `camera_raw_ui.rs` and `gpu_canvas.rs`. Kept upstream Camera Raw
undo/redo/history handling and Intel-only DX12 mip-target behavior; retained
hosted footer controls, exposed draft/commit access, and compositor warmup,
which upstream does not provide. Reviewed automatic merges in control, jobs,
menus, native startup/services/window frame, theme, and GPU code. The hosted
open worker now uses upstream's layer-bounds warmup too. No authority expansion.

Finished on-demand export handling on host branch `codex/image-triage-integration`:
live changed RAW draft, then immutable pending full-editor snapshot / saved
project, then saved recipe. An unchanged reopened RAW dialog does not override
full-editor work. Missing, corrupt, invalid-UTF8 or quarantined recipes fail
exports instead of becoming originals. Requests check the dialog's source and
the returned output path, dimensions, depth and profile; a display JPEG never
serves as a full-resolution render. Temporary editors and outputs are cleaned
up, including shutdown while launch is in flight. Export/resize/convert and
PocketDrop report skipped photos and depth conversions. PNG/TIFF keep supported
precision and ICC; JPEG conversion uses sRGB for deep renders. Existing source
EXIF is retained where available; deep TIFF output reports omitted EXIF.

Fork `036321dd17361d1f5a6df1da1e97535ce63f8b5b` rejects missing/malformed pending
snapshot keys, reports the open RAW source, records project depth separately
from encoded output depth, and enables SHA-256 on wasm. Render algorithm is 2
on both sides; older manifest renders are invalidated. A quantized PNG cannot
stand in for a float project. Unique paths fix parallel recipe-test collisions.

The owner explicitly approved fixing the newly found upstream regression in
16-bit destructive Posterize/Threshold. Commit `0d674c0d` passes the sample depth
for those adjustments, aligning saved pixels with upstream's preview rules;
other integer tone-curve behavior is retained. The former failing preview test
now passes, with an additional bin-boundary/luminosity regression.

Completed so far: frozen full host suite 2,004 passed, 5 skipped, 11 xfailed and
238 subtests (six warnings, including existing fake-NAS telemetry). An earlier
repeat exited without a summary near 49%; the verbose diagnostic rerun passed
the complete suite in 454 s. Export/schema/failure guards also passed separately.
Rust RAW 172 and IO 582 tests; engine 1,817 before the depth correction, plus its
new regression and the complete engine suite in the release corpus gate.
UI 2,819 passed (12 ignored), print tests 3 passed separately, compositor warmup
integration passed. Final engine/UI clippy, layers, 24 wasm checks and ignored
adversarial panic hunt passed. The wasm target was installed with explicit user
approval. IO corpus passed 610 tests across 65 binaries; the complete
`cargo xtask test-corpus` gate passed 3,582 tests across 157 binaries, including
HEIF, PSD/Photoshop oracles and mutation checks. `-p gpu` is unsupported by the
runner; its default selection covers compositor/GPU changes. Corpus release
tests used LTO off/codegen 16 to avoid repeated thin-LTO links; production build
retains normal release settings. Candidate build/performance/native handoff
validation remains pending. The production candidate built successfully in
13 m 37 s at `target/audit-20261014/release/photocraft.exe` from `036321dd`,
SHA-256 `92FE980C17C40F479F9E7882D521A933927AF68326545C3F61608585B1002A85`.
`cargo xtask perf --quick` passed on Intel UHD 770. Camera Raw's 6000×4000 proxy
update median was 12.09 ms (p95 12.37 ms); type-size drag total median 5.41 ms.
Complex 174-layer layout updates remain slow (about 346–436 ms for opacity and
some move commits); quick mode tracks these without enforcing full-mode budgets.
Reports are in `target/audit-20261014/perf`, not published as a new baseline.

The first candidate passed native handoff at 1 MP and 24 MP, all five themes,
Save/grid propagation/reopen/cleanup, and Camera Raw-first. Its live recipe
export failed safely because the dialog reported a null source: the host's
byte-backed RAW handoff dropped `PendingRaw.path`. Commit `c105a3f7` preserves
that existing read-root path alongside the retained bytes; developing still
uses those bytes. Its A→B dialog-source regression passed. UI/clippy/wasm checks
and an isolated release rebuild are being repeated before fresh native checks.
The first candidate is unpromoted; no original-photo fallback was written.

The known-good `target/release/photocraft.exe` remains unchanged:
SHA-256 `548BF3688B1E332FEA6E67E8E635099BC4001F63E12F578AFE38FF02754FCB2B`.
Build the candidate in isolated `target/audit-20261014`, outside normal discovery.
Keep its release executable unpromoted until the native checks pass. Checks use
private profiles and `IMAGE_TRIAGE_CHECK_DESKTOP_OF=claude.exe`; the marker's
desktop currently exists and is inactive. Do not capture the owner's active
desktop. Initial ordinary PhotoCraft preferences SHA-256:
`E4F299B3EFEB97FA60DD8083682F68B1CB8F21097E0A3F07B29FD27F3AA09BF5`.
No pushes or upstream PRs.

## Audit handoff — 2026-10-14 (Checkout desktop)

Audited Image Triage `1f6ebcc..ac4693d` and PhotoCraft `c6092f86..e0569ef0` at the
owner's request. Both trees were initially clean. Image Triage's origin and
PhotoCraft's configured origin (tylermcm's fork) fetched successfully. Fetching
`fork` failed because that remote is not configured; direct URL fetches then
succeeded into `audit-upstream/main` and `audit-fork/integration`.

Integrated upstream base: `99e1138877e6aa7e856c6883b415e708d042fc59`.
Fetched upstream tip: `7250e6e29a9248ac17bc0cb20296e03e7cbed86a` (45 new commits).
Fetched fork integration tip: `e0569ef00dd1c77d07d4f7ec02ef61588e497361`, equal
to the integration branch; no new fork-only work. This was an audit of fixed
revisions: no upstream merge, conflicts/resolutions, product edits, commits,
pushes, upstream PR or executable promotion. Integration/validation of the
new upstream tip remains pending. The existing desktop release executable
from `9e94b0c0` was preserved.

**Findings:** [full report and reproductions](audit_20261014/report.md).
Nine actionable defects: early post-Open edits swallowed by rebaseline;
recipe-only edits omitted from thumbnails/exports; full exports using an
enlarged display JPEG; recipe-write errors discarding adjustments; native Save
binding lost on RAW replacement; saved recipe previews using different math;
native Save leaving the display JPEG stale; startup not tracked during
shutdown; live theme checks writing the normal PhotoCraft preferences.
The host and Rust reproductions confirm the first eight; the ninth follows
the live check and its actual preference-write path. No repairs were made.

**Validation:** full host suite 1940 passed, 5 skipped, 11 xfailed, 238 subtests;
no segmentation fault or intermittent move/undo failure in this one run.
Six warnings included a telemetry-thread exception against a fake NAS path.
Current fork source: UI library 2756 passed / 12 ignored; RAW library and
integration tests 172 passed; four Rust reproduction tests passed (asserting
the faulty behavior); layers passed (29 crates). Cargo used `-j 4`, one command
at a time. Temporary Rust integration test removed after verifying its hash
against the retained reproduction source.

**Live, known-good desktop binary:** handoff at 1 MP passed hidden startup,
embedding/fullscreen geometry, persistent navigation, five themes, native Save,
grid notifications, tab reopen and process/token cleanup; native screenshot
inspected. Desktop captures were not verified on the background virtual desktop.
Recipe check passed on Z 7II 0027/0030. Lean-save check on 45 MP Z 7II 0027 passed:
autosave 5.1 s, close 3 ms, full render 4.5 s after close, same-process reopen
679 ms, busy close 118 ms, quit 1129 ms. Single-run timings under audit load.
`IMAGE_TRIAGE_CHECK_DESKTOP_OF=claude.exe` was set; only check-owned processes
were cleaned up. The existing theme check uses the normal editor profile
unless `PHOTOCRAFT_CONFIG_DIR` is set; its ordinary preferences reported Classic
after the check (prior value not recorded). Later lean checking used a private
PhotoCraft profile. Never used process-name kills.

**Limits:** no new editor build was promoted; live checks exercised `9e94b0c0`,
not the final `e0569ef0` layout commit. Root causes of older native crashes,
exit-127, undo flake and laptop slowdown remain unproved. No development-restart
end-to-end run, clean-PC MSI upgrade/install, complete desktop/UI integration
suites, wasm, clippy, corpus or performance gates. Audit logs/artifacts:
`C:\Users\Checkout\AppData\Local\Temp\image_triage_audit_20261014`.

## Quick handoff - 2026-10-14 (later: the editor is inside the installer; release 2.3.1)

Image Triage `main` at the 2.3.1 commit; fork unchanged at `e0569ef0`. Laptop.

**One program.** The 2.3.0 installer did not contain the editor (it was a separate download). Now `setup_msi.py` puts
`photocraft\photocraft.exe` and the licences PhotoCraft asks to be kept (`LICENSE-APACHE`, `LICENSE-MIT`, `NOTICE`, `ATTRIBUTION.md`) in the
install folder (`freeze_support.photocraft_bundle_files`), and the installed app looks there first
(`shell_actions.bundled_photocraft_executable`, only when frozen; `IMAGE_TRIAGE_PHOTOCRAFT_EXE` still wins, then the companion checkout and
`PATH` as before, so running from source is unchanged). Building the installer takes the editor from `IMAGE_TRIAGE_BUNDLE_PHOTOCRAFT_EXE`, else
`..\photocraft\target\release\photocraft.exe`; **a build that finds no editor stops** (a missing licence file is only a warning), and
`IMAGE_TRIAGE_BUNDLE_PHOTOCRAFT=0` builds one without it on purpose. So: build the editor first, then `python setup_msi.py bdist_msi`.

Checked: 12 tests in `tests/test_bundled_editor.py` (each of 7 mutations of the change fails one of them); the MSI has the same upgrade code,
version 2.3.1, `photocraft\photocraft.exe` (77.5 MB, same sha256 as the editor that shipped beside 2.3.0) and the four licence files; an
administrative install (`msiexec /a`) into a scratch folder lays them out as `<dir>\photocraft\...`, and that copy answers `--version`
(`photocraft 0.8.2`). The MSI went from 115 MB to 147 MB. Not done: a full install and a run of the installed app on a clean PC (the
owner is testing the compiled program on another PC); an upgrade over a running 2.3.0 may ask for a restart as before.

## Quick handoff - 2026-10-14 (the Camera Raw layout; release 2.3.0 built by hand)

Image Triage `main`; fork `codex/image-triage-integration` at `e0569ef0`. Both pushed to the owner's repositories (nothing to storytold). Laptop.

**Camera Raw layout in the workspace** (fork `e0569ef0`, `camera_raw_ui.rs`): the strip under the photo is gone and its tools (zoom, hand,
Fit in View, 100%, the zoom menu) are in the footer, in what is free left of Load/Save Settings, Before, Open and Cancel (clipped, never the
buttons, in a window too narrow for both); the photo margin is 8 px instead of 16; the settings panel is 270 px instead of 330
(`WORKSPACE_PANEL_W`, `WORKSPACE_VIEW_MARGIN`). At 1365x767 the photo area went from 1003x625 to 1079x673. The dialog that is the filter's own
window (`own_window`) keeps upstream's layout, so the merge cost is small. `layout()` is also what `ui.viewport` reports as `cameraRaw.view`, so
the host's picture of the photo follows by itself. Tests: three layout tests; the 15 `camera_raw_ui` and the `raw_workspace` tests pass.
Live on the laptop: handoff check (1 MP), camera-raw-first and lean-save pass; screenshots at 1365x767 and 1000x700 look right (nothing
overlaps; the panel scrolls). **`check_camera_raw_recipe` failed once** (13 s in, `assert camera_raw()["changed"] is True` right after the
first adjustment) and passed in the next two runs: not explained, probably a race in the check; not looked into.

**Release 2.3.0, made by hand.** `.github/workflows/build-windows-msi-release.yml` is not registered any more (the repo's only workflow is
`tests.yml`, which fails in 0 s), so nothing builds on a tag. Done instead: version bump (`cccbcb3`), `python setup_msi.py bdist_msi` in a
clean worktree of that commit with the scratch venv (cx_Freeze 8.7.1 installed into it; about 2 minutes, 115 MB; the MSI's upgrade code is
unchanged, so it upgrades in place), then `gh release create v2.3.0 -R tylermcm/ImageTriage --target <full commit hash>` (a short hash is
refused) with `Image.Triage-2.3.0-win64.msi`, `photocraft.exe` (the editor built from the fork at `e0569ef0`) and `SHA256SUMS.txt`; the
digests GitHub shows equal the local ones. It is the repository's latest release, so `Help > Check For Updates...` offers it (the previous one
was "ImageTriage V2.1.1", tag `V2`). Not code-signed. The MSI does not contain the editor: `photocraft.exe` goes beside it (environment
variable `IMAGE_TRIAGE_PHOTOCRAFT_EXE`, `PATH`, or `C:\Program Files\PhotoCraft\`). The tag is on the build commit; this note and anything
after it are later commits.

**Mistake of mine, tonight.** My test scripts cleaned up with `taskkill /IM photocraft.exe`, which kills every editor, including the one of the
owner's running Image Triage (that was how I found out: the owner's app kept its editor running from `target
elease`, a build could not replace the
locked `photocraft.exe`, and a script of mine then ended that process). They no longer do that
(the checks end the editor they started; it is tied to its parent by a job object). The two "PhotoCraft handoff failed" entries of
2026-10-11 (17:29 and 17:44) may owe something to the same kills, besides the 30 s idle drop that was found and fixed.

**Also seen.** `import onnxruntime` crashed (segmentation fault) in the scratch venv (onnxruntime 1.31.0, Python 3.11): quite possibly the
intermittent native crash of the full suite. Not chased.

## Quick handoff - 2026-10-13, night (the laptop: the merged editor built and run here)

Image Triage `main` at `91e3132`, fork `codex/image-triage-integration` at `9e94b0c0`, both pulled onto the laptop (Intel UHD 620, 4 cores / 8
threads, 15.9 GB). Nothing changed in the code; this is the "not run on the laptop" gap closed.

**Built.** `target/release/photocraft.exe` from `9e94b0c0` (upstream tip `99e11388` merged): 15 min 8 s with `-j 4`, no warnings, 77.5 MB. It
needed crates the laptop did not have (downloaded with the owner's word). The build before it is kept as
`photocraft-known-good-2026-10-12-ca2d3298.exe`, beside `photocraft-baseline-823d9890.exe` and `photocraft-known-good-2026-10-11-c6092f86.exe`.

**Live checks on the laptop, all pass** (the checks of `main`, on this editor): `check_photocraft_handoff` at 1 MP and twice at 24 MP,
`check_camera_raw_first`, `check_camera_raw_recipe` on two Z 7II NEFs and on the two files that used to decode black (`z7_compressed`,
`z7_lossless`: the recipe render is not black, red level 190 against 88 for the Z 7II pair), `check_lean_save`. Closing the popout held the UI thread 15 ms in the lean-save check
(3.2 s on 2026-10-12). Camera Raw on a 45 MP NEF on this laptop: first raw 6.5-6.6 s, the second 2.0-2.2 s, back to the first 0.5-0.6 s.

**One number worth a second look.** The lean save of a 45 MP photo (`measure_stash_stall.py --lean --redraw`, run once for each editor): the
editor before the merge took 8.3 and 8.8 s; the new one took 16.4 s for the first save and 9.3 s for the second. The first-save cost may be a
one-off of the merged editor (the lean-save check showed 16.7 s for its autosave too); the second save matches. Slow answers while saving
were 2-5 over 100 ms (worst 0.6-1.3 s) with either editor on a machine that was not idle, against none in quiet runs on 2026-10-12.

**Open on the laptop.** The viewer now opens with the editor up by default (`preview/editor_starts_open`), which is the slower
browsing on-demand was built to avoid: flipping speed with that setting on was not re-measured here. Not run: the Rust suites, clippy, layers, wasm.

## Quick handoff - 2026-10-13, late (the viewer opens with the editor up, as a setting)

The owner wants to try the viewer opening with the editor already on the photo. New setting
`preview/editor_starts_open` (Settings > Interface > "Open the viewer with the editor up"), on by default
(`EDITOR_STARTS_OPEN_DEFAULT`); it only applies while "Open the editor only when I press E" is on, and its checkbox
is greyed out otherwise. When the viewer goes from hidden to shown, `show_entries` calls `open_editor()`: exactly the
editor key, without the dwell. E still puts the editor away and brings it back; put away, it stays away until the
viewer is next opened. It is a trial: if the owner turns it off, the viewer opens plain as before.

Real app, two runs: the editor is up 1.18-1.19 s after the viewer opens (nothing was developed ahead, so this is the
"key pressed at once" case), 1.26-1.38 s on reopening, E puts it away and brings it back in 1.2 s, close 5 ms.
Flipping through photos with the editor up is the slower browsing that on-demand was built to avoid; with this on,
that is what the viewer does until E is pressed.

Tests: `test_native_first_browsing.py` (the `plain` fixture is now the setting off; new `starting` tests). Full suite
1,927 passed; `test_char_move_delete_undo` failed once in the full run and passed three times on its own (unrelated,
intermittent). The suite also died with a segmentation fault twice today in about fifteen full runs, with no test named
and nothing in two traced reruns: the old intermittent native crash, not chased.

## Quick handoff - 2026-10-13, evening (closing the popout hides it)

Image Triage `main`; the fork is unchanged (`9e94b0c0`, pushed). Desktop only.

**Why.** Closing the popout moved the editor's window to the parking window first (Qt destroys a closed top-level's
native window, and a child window with it). `SetParent` across processes waits for the thread that owns the window:
with the editor redrawing a 45 MP photo after an edit the close was held for 26 ms to 6.3 s, depending on how long
before the close the edit was made (0.1 s before: 5.5-6.3 s; 0.3 s: 0.4-0.5 s; 1 s: under 30 ms). Hiding the popout
waits for nothing: 3-9 ms in the same ten rounds, the editor's window intact and answering afterwards.

**What changed.** `FullScreenPreview.closeEvent` does its clean-up, emits `closed`, then ignores the event and hides
(`hide_on_close`, on by default). The editor stays embedded in the hidden popout, where it keeps running and saving;
the next time the popout shows, nothing has to be re-attached. `prepare_photocraft_preview_close` moves the editor to
the parking window only when the popout closes for real (`IMAGE_TRIAGE_POPOUT_REAL_CLOSE=1`), which is kept as the
fallback and still tested. `preview.close()` now returns False (the event is ignored); nothing in the app reads it.

**Not a fallback, tried:** moving the editor out when Qt announces the native window's destruction
(`QEvent.PlatformSurface`, `SurfaceAboutToBeDestroyed`). The event arrives and the move runs with the editor's window
still alive, but the window is gone afterwards all the same. So if Qt re-creates the popout's native window for some
other reason (a change of window flags or of screen), the embedded editor is lost, as it always was.

**Measured.** Real app: close 4-7 ms in three runs (135 ms to 2.5 s before, and ~3 s before 2026-10-13).
`check_lean_save.py` has a new step, closing a tenth of a second into an edit to the whole photo: 4-34 ms in three runs;
with `IMAGE_TRIAGE_POPOUT_REAL_CLOSE=1` the step fails (the close waited over a second). Quitting the app with the popout
open and an unsaved edit still waits for the 304 MB project save: 2.6-4.9 s over four runs on the merged editor (1.9 s
on the editor before the merge); two runs each way did not separate hide from real close.

**Checked.** Full suite 1,922 passed, none failed. Live: handoff 1 MP and 24 MP, camera-raw-first, camera-raw-recipe,
lean save three times, the real-app trial four times, two random-navigation runs without a wrong state.

**Open.** Not run on the laptop or in the development restart. The upstream fix for the black Z-body files is PR
storytold/photocraft#3238 (a comment confirming the change was left there); the fork carries the same line until it lands.

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
- (Fixed the same evening, see the section above: closing the popout while the editor was busy could take seconds.)
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

## 2026-10-14: audit fixes (F1-F9), state at hand-over from Claude to Codex

Plan: `C:/Users/Checkout/.claude/plans/modular-growing-sutherland.md` (approved, with the owner's amendments). Audit:
`docs/audit_20261014/` (report and repro scripts; still untracked, Codex's).

### Committed, not pushed

Image Triage `main` (ahead of origin):
- `6a85a74` F1: an edit made before the first poll after Camera Raw is saved (document_state, save_required keyed by sidecar + document generation + revision).
- `817a7e4` F8: shutdown waits for / cancels the background editor start.
- `bb2a8ae` F9: live checks give the editor a private profile (`PHOTOCRAFT_CONFIG_DIR` temp dir). Owner's `%APPDATA%/Photocraft/preferences.json` sha256 before any live run: `e4f299b3efeb97fa60dd8083682f68b1cb8f21097e0a3f07b29fd27f3aa09bf5` (check it is unchanged after the live checks).
- `2ab676b` F7 host: readers follow `<photo>.photocraft.renders.json` (tests/test_render_manifest.py).

PhotoCraft fork, branch `scratch/upstream-merge-2026-10-14` (integration branch `codex/image-triage-integration` is still at `e0569ef0`; fast-forward it only after the release build passes the live checks):
- `13b0193a` merge of upstream `c699e3cd` (Release 0.9.0), no conflicts.
- `d35f4c96` F4: recipe written before the dialog changes; failure leaves dialog/settings/original untouched.
- `92281079` F5: host binding follows the photo through Open (redevelop, continuation); fixed the in-memory continuation leaving `expect_continue` set (next photo got no Camera Raw dialog).
- `7caaba23` F7: generation-named pictures + render manifest (SHA-256, not BLAKE3: host has no blake3), `renderManifest: 1`; bind carries display + manifest.
- `e41f07f7` F6: `raw_recipe::apply` shared by the dialog and the recipe render.
- `043c6af2` test isolation (process-wide manifest/render queues keyed by path).
- `35e84fb3` `app.render` (`fullRender: 1`): project (snapshot or file) or recipe developed exactly as Open; PNG 8/16, float TIFF; recipes carry `"identity"`.

Fork gates on that branch: fmt, clippy -D warnings, xtask layers OK; raw, io, engine suites pass; ui-egui lib 2783 passed. Upstream's new print tests (`file_ui::tests::print*`, from upstream commit a9ac922f) crash the test process (0xC0000005) when run in parallel, 15/15; each passes alone. Run the suite with `--skip file_ui::tests::print`, then those with `--test-threads 1`. Upstream bug, not ours. `cargo xtask wasm` NOT run: needs `rustup target add wasm32-unknown-unknown` (a download; ask the owner).

### Uncommitted, in progress: F2/F3 host side (Image Triage working tree)

- `image_triage/full_render.py` (new): `request_full_render(path)` -> NO_EDITS / rendered / failed. Precedence: live Camera Raw dialog on the photo (read via `ui.cameraRaw {}`), unsaved full-editor changes (saved first via `_save_open_photo_for_render`), saved project (manifest-verified PNG, else `app.render`, snapshot while a save is pending), recipe (`recipe_state`). Own render lane, one at a time, cancellable, hidden temp editor when no running editor covers the folder; `shutdown()`.
- `image_triage/edited_export.py` (new): `EditedRenderFailed`, `BatchOutcome` (tuple of written paths + failed + notes), `edited_image_for_export` (decodes at rendered size, render's ICC), `save_preserving_depth` (16-bit PNG/TIFF via QImageWriter; PNG gets eXIf; 16/32-bit TIFF drops EXIF and says so).
- `photocraft_bridge.py`: `classify_recipe`/`recipe_state` (read-only; invalid = edit data, not "no edits"; only numeric limit is exposure +-10, the editor has no other ranges), `render_lane()`, `full_render_supported`.
- `edit_storage.py`: `session_has_edits` now includes edited/invalid recipes; old meaning moved to `builtin_session_has_edits` (used by the legacy built-in-session hand-off branch in preview_controller).
- `edit_render_headless.py`: full size no longer uses PhotoCraft pictures (no 2560 px enlargement); thumbnails unchanged.
- `image_resize.py`, `image_convert.py`, `image_ops.py`, `workflows/export.py`: `apply_edits_if_present` raises `EditedRenderFailed`; loops skip and list; `_save_resized_image` keeps depth or returns a note. `export_jobs_controller.py`: `_report_left_out` shows failures/notes.
- `handoff_controller.py`: PocketDrop renders with `_wait_with_events`, leaves out and lists photos whose edits fail (no original sent).
- `preview_controller.py`: registers `FullRenderService`, shuts it down first in `shutdown_photocraft`.

### What is left

1. Finish the host test run for the files above (it was running at hand-over) and fix tests that assumed the old fallbacks (PocketDrop/export "falls back to original" tests should now expect left-out + report). Then add tests: full_render precedence (dialog > dirty editor > project > recipe; cached valid render then dialog changed then export uses dialog; project + adjusted dialog renders dialog), pending save uses snapshot, invalid recipe -> failed, identity -> no edits, render failure -> batch leaves photo out, temp editor shut down, shutdown cancels a running render, 16-bit ProPhoto render -> 16-bit TIFF/PNG with ICC and an 8-bit JPEG noted, never enlarged. Commit F2/F3 host as one or two commits (no AI attribution).
2. Full Image Triage suite (repo `tests` registered first, offscreen; one timing test `test_the_ui_thread_wait_keeps_its_message_queue_running` can flake under heavy CPU, passes alone), ratchet/stylesheet golden if touched.
3. Rebuild the release editor from the fork branch (keep the current exe as known-good), then the handoff/camera-raw/lean-save/stress live checks with `IMAGE_TRIAGE_CHECK_DESKTOP_OF=claude.exe`, plus the plan's new E2E checks (F1 edit-before-poll, F8 restart during start/render, F4 read-only edits folder, F5 Ctrl+S after Open/continuation, F7 native Save after autosave, F2/F3 recipe-only raw export at source size). Confirm preferences.json hash unchanged.
4. Invert `docs/audit_20261014/reproduce_*` defect assertions into permanent tests where not already covered.
5. Then fast-forward `codex/image-triage-integration`, update this file's validation record. Push only when the owner asks; never push to storytold.

### Update after Codex's session (2026-10-14 evening)

Codex committed the F2/F3 host work (`ecba799`, tests in `tests/test_full_render.py`) and live-check scripts (`70829a0`,
`af55d93`, `007266b`, new `scripts/check_full_render.py`) on Image Triage branch `codex/image-triage-integration`
(pushed as `audit-fixes-2026-10-14`; `main` not moved). In the fork it merged upstream `620bccef` (`45933f08`) and added
`0d674c0d` (16-bit Posterize/Threshold, an upstream-code change: check whether it belongs upstream), `036321dd`, `c105a3f7`
on `scratch/upstream-merge-2026-10-14`. It ran out of tokens during live checks with a new build at
`photocraft/target/audit-20261014/release/photocraft.exe` (16:10); which checks passed is not recorded.

Verified on the final commits (Claude): Image Triage full suite 2004 passed, 5 skipped, 11 xfailed, 0 failed.
Fork: fmt OK, clippy -D warnings (workspace) OK, ui-egui lib 2819 passed (print tests skipped) + print tests 3 passed
serially. Owner's PhotoCraft preferences.json unchanged (same sha256 as above).

Still to do: live checks on the new build (keep `target/release/photocraft.exe` as known-good until they pass), promote the
build, merge to `main`, fast-forward `codex/image-triage-integration` in the fork. wasm gate still not run.

Posterize/Threshold (`0d674c0d`): a real bug (16-bit commands split on an 8-bit grid, unlike their preview), but upstream
fixed the same thing in storytold/photocraft#5711 (`1c6f1387`), after the `620bccef` we merged. No upstream PR. At the next
upstream merge, expect a conflict in `crates/engine/src/pixels.rs`: take upstream's side and drop our commit and its test
(`posterize_and_threshold_keep_the_16_bit_sample_grid_in_committed_pixels`).

## Camera Raw bars: uncompiled design batch (2026-10-16)

Owner requested seven native chrome corrections against the supplied ACR 18.6 reference: Curve selectors, White balance combo/dropper, Profile/browser glyph, Edit controls, metadata row, filled corner histogram triangles and top actions. Work is in companion camera_raw_ui.rs, new child module camera_raw_bars_ui.rs, camera_raw_scope_ui.rs, camera_raw_workspace_ui.rs and camera_raw_design.rs. New visual metrics are documented in design/camera-raw/README.md but not added to active layout.json because the running executable rejects unknown properties. The previous section border correction was rebuilt at owner request; the new bar batch has NOT been compiled. Rustfmt and git diff --check pass; added tests await execution. Additional camera profiles, Auto, HDR and tutorials remain disabled, rather than pretending processing parity. B&W reuses saturation, and a neutral picker uses the existing relative gain model. Preserve target/release/photocraft.exe: no hosted promotion or new handoff regression claim. No new upstream integration performed in this continuation; another agent is working independently elsewhere.

### Native bar rebuild authorized and launched (2026-10-16)

Owner requested the quick rebuild. Cached dev build passed in 3m08s and scripts/preview_camera_raw.ps1 launched the rebuilt native preview (PID 49864). Executable SHA256 A302F1C84B5931449875F02E195BCA09CB6B7971CF02463CC55EC2D274C27CF0. The previous preview remains running, with its executable preserved as target/debug/photocraft-before-bars-64192.exe. No hosted promotion. Unit tests and screenshot parity validation remain pending; compilation success is not a claim of exact visual parity.

## 2026-10-16 — JSON overlay parity resumed

Both user origins fetched successfully and integration branches were current.
PhotoCraft upstream fetched: old integrated base
b630d7cb9aeb2a79ab5224ebf6e8266f1d858873, new upstream tip
2a952f16 (28 commits). Merge conflicted in apps/photocraft/src/main.rs and
crates/ui-egui/src/gpu_canvas.rs. Owner requested skipping conflicting updates;
merge aborted, preserving 0f488ea3. No resolution or integration claimed.

Existing debug executable launched with isolated profile and synthetic fixture.
All 19 references hashed in companion design/camera-raw/reference-inventory.json.
Reference 10 full-dock overlays show histogramHeight=124 and metadataHeight=24
close the 8-pixel downstream offset. Measured separators now align within 1-2
reference pixels. Evidence/crop anchors: companion README and local
target/acr18/parity-20261016. Every-tab/every-element parity remains incomplete.
No rebuild, hosted promotion, full handoff validation or processing parity claim.

### Native support authorized; Grading overlay follow-up

Owner explicitly authorized native UI support for missing reference screens.
Companion now has reference-wide observed-control inventory and baseline captures
for Curve, Mixer, Grading, Detail, collapsed Edit and Info. Before overlays for
references 7/11/12/14 retain uniform full-dock scaling and missing coverage.
Grading glyph-selector enabling build passed (3m42s), launched isolated preview
PID 45928/control 57832. Its executable is preserved under
 target/debug/photocraft-parity-selectors-45928.exe. Old owned preview 21876 stopped.
The overlay exposed incorrect wheel artwork brightness and extra luminance fields.
Follow-up source adds display-referred HSV wheel art and compact editable tracks,
with geometry/color/interaction tests; these latest edits remain unvalidated.

Live test/build handles and exact commands are recorded in companion
 target/acr18/parity-20261016/active-work.json. Test session 16583 was compiling;
build session 15032 queued behind its target lock. Revalidate their live state
before deciding to restart. Rerun current-source tests if the first compilation
read earlier source. Clippy, current xtask layers/wasm and fresh wheel overlay
remain outstanding. Cached xtask.exe is stale (did not know web-boot); its layers
output is not a current validation result. No hosted promotion/full parity claim.

### Wheel build captured; verification continues

Wheel follow-up build passed in 7m57s. Preview PID 65360/control 57833 uses
isolated parity-profile-v3 and synthetic fixture. Prior owned preview45928 was
stopped after validating all six new states. Capture script now lives in
companion scripts/capture_camera_raw.py: all six screenshots retained identical
photo parameters/document revision and restored original layout bytes.

Reference-12 fixed wheel-interior mask mean RGB errors improved from
[46.06,36.53,66.71] to [7.52,7.05,8.52]. This is a narrow visual measurement,
not full-panel parity. Rail measured80 reference pixels; JSON railWidth40.
Lower-wheel positions, label offsets, tracks, handles and missing dots/eyes
remain measured work. Companion grading-next-layout.json records exact bounds.

First camera_raw suite:48 passed/2 failed (geometry test measurement and stale
i18n source scan). Corrected tests/scanner and added Adjust translations to20
catalogs. All-target clippy then passed; corrected current-source tests are
running in session53449 (compiler64460), with fresh layers/wasm queued.
Offscreen snapshot --raw-workspace flag added after clippy; example build in
session59323 queued. That flag needs build/render and updated clippy validation.
No host build, known-good release replacement or handoff promotion.

### Validation passed; next native batch building

Prior wheel batch:52 Camera Raw tests passed, all-target clippy passed, layers
30-crate check passed, xtask wasm all listed crates passed (4 existing dead-code
warnings). Offscreen root Camera Raw rendered1440x1400 and full-height overlay
captured without vertical clipping. Capture runner now checks every requested
preview field, fixing acceptance of stale pre-reload state. Strict six-state
recapture verified matching state and unchanged photo parameters/revision.

Latest native source batch adds measured Grading layout controls and a constant
settings gutter, Info card/metadata/credentials layout and an operational
in-dialog Snapshots tab. Snapshots are not durable across dialog close/photo
switch yet; no broader persistence claim. New batch all-target clippy passed;
example/app builds, tests and fresh overlays pending in session82463. Desktop
preview65360 remains the preceding wheel build, preserved under
photocraft-parity-wheel-65360.exe. Active handles/plans are recorded in companion
 target/acr18/parity-20261016/active-work.json. No hosted release promotion.

## 2026-10-16 — ACR overlay iteration v6 (current 19-reference set)

Resumed the existing native JSON ACR parity work. Read current source and generated
evidence; old build handle82463 was absent and its recorded compiler/preview
processes were gone. Current reference set is
c0f524f7-ece8-4a8b-a8f6-7f0f327cf262. All19 hashes matched the earlier references,
but numbering differed; companion inventory now preserves legacyImage and uses
current numbering. Snapshots/Info/Grading are now references1/4/9.

Session update check: initial direct upstream and fork fetches failed with HTTP/2
stream errors. Both retries with git http.version=HTTP/1.1 succeeded. The configured
origin still points to the user's fork, with no fork remote, so explicit URLs and
separate session refs were used. Old integrated upstream base remains
b630d7cb9aeb2a79ab5224ebf6e8266f1d858873; fetched upstream tip remains
2a952f164c0b4cf936b1c6497db10dd095341c00 (28 commits pending). Fetched fork
integration and local integration HEAD both remain
0f488ea3def930fac9fa46a458225b2b2548aebf. No new fork integration commits.
The earlier recorded request to skip conflicting updates remains in effect: no
new merge attempted, conflicts resolved or automatic merge compatibility claimed.
All existing Camera Raw work preserved; no push or upstream PR.

Native changes refine Info/Snapshots heading alignment, typography and spacing;
Grading selector group geometry/separators, wheel antialiasing, lower labels,
luminance tracks/handles and footer spacing. The shared numeric field now paints
inactive Camera Raw values at the requested left edge while keeping full-width
click-to-edit and typed arithmetic behavior. Measured geometry remains live JSON.

Validation passed:56 camera_raw library tests,5 camera_raw_workspace integration
tests, shared widget unit tests, all-target ui-egui clippy -D warnings,30-crate
xtask layers, and every xtask wasm check. Four existing wasm dead-code warnings.
Overlay auditor has4 passing Python regression tests. Native snapshot executable
SHA256 F2832DA7C1285F296FF7F4BA08C6BE72A1C9EF584C9C6373DA85F85FBDA8CEAC.
All11 supported named states captured at1440x1400 with requested views verified
and photo parameters/document identity unchanged. Whole-window Info/grid/Edit
captures and source-sized comparisons for references16-19 also generated.

Evidence and exact current state: companion target/acr18/parity-20261016,
active-work.json, native-v6-offscreen/capture-report.json and v6 overlay folders.
64 named element regions are audited across Grading/Info/Snapshots under a single
registration per comparison; no independent control alignment or image warping.
They are improved, not pixel-identical. Missing native tabs/sections and individual
controls are recorded per reference. The full19-reference parity goal remains
active/incomplete; no full UI or processing parity claim. No desktop editor rebuild,
hosted executable replacement or live handoff validation/promotion in this batch.

## 2026-10-16 — ACR native sections v8 and Detail v9 follow-up

The v8 native snapshot batch added all ten Edit headers, temporary press-and-hold
bypass for seven implemented stages, and Optics/Lens Blur/Calibration layouts.
Unsupported processing remains disabled. Canonical parameters/history/export
remain unchanged by view-only bypass. Capture ordering now preserves neighboring
sections; thirteen native states were captured with requested views and invariant
photo identity. Added 40 collapsed-header and 93 lower-panel element regions.
V8 passed 58 Camera Raw library tests, 7 workspace tests, all-target clippy,
30-crate layers, wasm (four existing warnings) and 5 Python auditor tests.
Snapshot SHA256: 27657465fe8e3fe49c7c4164a6aea28545180bb5eca2dbff90eb7e7b31b6dab8.
Evidence: companion target/acr18/parity-20261016/native-v8-offscreen and v8 overlays.
These measurements do not establish complete visual or processing parity.

Fresh session fetches succeeded for upstream and user fork via explicit HTTPS
URLs and HTTP/1.1. Upstream moved from 2a952f164c0b4cf936b1c6497db10dd095341c00
to db46713c2ca0140e119956ed695cf92837fc50e3 (45 commits after the integrated base
b630d7cb9aeb2a79ab5224ebf6e8266f1d858873). Fork integration and local HEAD remain
0f488ea3def930fac9fa46a458225b2b2548aebf, with no new fork integration work.
The recorded owner direction to skip conflicting updates still applies; no new
merge, resolution, or automatic-merge compatibility is claimed. Earlier conflicts
were apps/photocraft/src/main.rs and crates/ui-egui/src/gpu_canvas.rs.

V9 source adds Detail enhancement rows with unavailable processing, an operational
Manual Noise Reduction disclosure, procedural focus/Defringe glyphs, responsive
Bokeh sizing and hard-edged section divider meshes. Validation and fresh overlays
are pending; active handles are recorded in companion active-work.json.
Full 19-reference/every-element parity remains active and incomplete. No hosted
build was replaced, no live handoff promotion was attempted, and nothing pushed.

## 2026-10-17 - PhotoCraft source push checkpoint

Owner requested pushing all PhotoCraft work. Both fetches succeeded. The checkout
still configures origin as tylermcm/photocraft; fetched that remote and used the
explicit storytold URL into refs/remotes/upstream/* without changing remotes.
Previously fetched upstream db46713c2ca0140e119956ed695cf92837fc50e3 advanced to
5f65579ecb44047c2a619036c18ff273202e1389. Integrated upstream remains
b630d7cb9aeb2a79ab5224ebf6e8266f1d858873, with 71 upstream commits pending.
Fetched fork integration matched local pre-commit HEAD 0f488ea3; no new fork-only
integration commits. This push checkpoint preserves the recorded upstream merge
deferral; no merge/conflict resolution or automatic-merge validation performed.

Committed all 44 pending PhotoCraft files as 86de9dca (Camera Raw panels,
translations, reference inventories/regions, capture scripts and regression tests).
Current checks: 58 Camera Raw library tests, 8 workspace tests and 5 Python tests
passed; all-target ui-egui clippy, 30-crate layers and xtask wasm passed. Full
ui-egui suite compiled but its library test process exited with Windows access
violation 0xc0000005; no full-suite pass claimed. Known-good hosted executable
preserved; no editor promotion, hosted handoff validation, or full parity claim.

## 2026-10-17 - Resumed every-reference Camera Raw overlay work

Continued on codex/image-triage-integration after requested push86de9dca. Both
fetches in this session succeeded: fetched upstream5f65579ecb44047c2a619036c18ff273202e1389,
integrated base remains b630d7cb9aeb2a79ab5224ebf6e8266f1d858873; previous fetched
upstream db46713c2ca0140e119956ed695cf92837fc50e3. Existing recorded owner deferral
of conflicting upstream updates retained; no merge/resolution/promotion claimed.

Goal reference set004b6ab0-f1b0-4278-a478-4890b6fff1d0 matches all19 old hashes,
with changed numbering. Remapped inventory and SHA-bound region specifications.
Added native Red Eye presentation/live JSON layout; unavailable Camera Raw
processing correctly disabled. Nine workspace tests and six Python auditor tests
pass. Native14-state capture verified view/photo invariants. A19-reference audit
reports235 named regions and13 references without individual audits; no full
reference is complete. Shared chrome and missing tabs/dialogs remain.

Overlay-driven body font11 matches reference text widths. Grading footer was20px
early; footer gap14.5 corrected it but shifted Detail20px. Added independent
Grading bottom padding13 to preserve Detail while moving footer; combined final
source rebuild/recapture/gates are running in session53266. Preceding lib tests
33138 and gates29319 were live on inspection. Current evidence/hashes/next steps:
companion target/acr18/parity-20261017/active-work.json. Current source has not yet
passed final rebuilt validation. Known-good hosted editor preserved; no native
handoff promotion, full parity claim, or push of this new batch.

### Verified padding fix; next native Curve batch

Padding build b4c1e230ea47dd4da1875c8e1354b8ff0a3b9ff52b9dd76dc4eb975eee1b6728
captured14 states with requested-view/photo invariants. Grading footer correction
keeps Detail's divider: RGB means5.05 and7.16 respectively; no pixel parity claim.
Added114 Edit and98 Curve/Mixer region specifications, for447 current regions and
11 references without individual audits. Baseline overlays identify graph width/
top spacing, missing parametric split strip and early White Balance rows.

New source adds live Curve-specific geometry, functional parametric split handles
using existing parameters, continuous histogram mesh/frame/grid ink corrections,
and White Balance bottom gap/chevron. New drag/clamp/history regression test.
A preceding library run had58 pass/1 missing Not available translation; added the
translation in20 catalogs. New current-source pipeline95549 is queued/live; check
80389 and preceding padding pipeline53266 also live on inspection. See companion
active-work.json for hashes, evidence, scope and next steps. New Curve source has
not yet passed rebuilt native validation. Known-good hosted executable untouched.

## 2026-10-17 - Push all code checkpoint

Owner requested pushing all code. Fetched Image Triage origin and PhotoCraft's
configured origin (tylermcm fork) successfully. A fetch of the named fork remote
failed because that remote is absent; the successful origin fetch covers the user
fork. Explicit storytold upstream fetch succeeded into refs/remotes/upstream/*.
Old/new fetched upstream both remain 5f65579ecb44047c2a619036c18ff273202e1389;
integrated upstream remains b630d7cb9aeb2a79ab5224ebf6e8266f1d858873, with 71
upstream commits pending. Fork integration matched pre-commit HEAD 86de9dca.
Preserved the previously recorded owner deferral of conflicting upstream updates;
no new merge, conflict resolution, or automatic-merge validation performed.

Committed all 51 pending PhotoCraft files as
00c154c46d4b8bc859aa07548224138d0e9a7c42 and pushed the integration branch to
https://github.com/tylermcm/photocraft.git. Current Python auditor tests: 6 passed;
staged diff whitespace check passed. Native Curve batch validation remains pending
as recorded above; this is a source checkpoint, with no hosted editor promotion
or full handoff/parity validation claim. Known-good hosted executable preserved.

## 2026-10-17 - Native ACR filmstrip work in progress

Both checkouts remain on `codex/image-triage-integration`. Actual companion is
`C:/Users/user0/Documents/photocraft`; its configured `upstream` is storytold and
`origin` is tylermcm. Both fetches succeeded. Previously fetched upstream
`5f65579ecb44047c2a619036c18ff273202e1389` advanced to
`efa1edd079b259b0b1f77b835151ee904437a231`. The earlier deferred upstream base
was integrated by `91270cce`; the new two-commit update is merged in
`3b89ae1eaf3933f2d49ad92178017806724461f9`. Fork integration remains `00c154c4`,
already an ancestor; no additional fork-only work was pending.

The two-commit update had no textual conflicts. Reviewed both automatic merges:
upstream's export-presets initializer matches our build repair; its version is
retained alongside the existing regression test. The other change only gates
algorithm test helpers with cfg(test). No new upstream startup, control, jobs,
menus, theme, native-window or authorization changes occur in those two commits.
Nothing was pushed.

New native module `camera_raw_filmstrip_ui.rs` owns drawing, hit targets, collapse,
scrolling and keyboard requests. `ui.filmstrip` exchanges a bounded metadata window
and at most one 384px RGBA display thumbnail per request. Stable identities,
browse generations and acknowledged serials protect against reordered/late
responses. Image Triage retains browse order/filter/sort, annotations, undo,
navigation, stash/restore and Save. New `photocraft_filmstrip.py` connects those
existing paths. The old Qt strip is removed from its layout and retained intact;
capability/data negotiation precedes detachment, then the native tray is enabled.
A lost reply keeps the last presentation and retries so both strips cannot appear.
Process exit restores the same Qt widget.

135 focused host regressions pass. Hosted synthetic check v2 passes native clicks,
grid selection, persistent PID, folder exclusion, host/native ratings, reject,
collapsed keyboard navigation, collapse/expand, host resize, unchanged originals
and process cleanup. Public RAW fixture testing also observed restored +1 exposure;
one earlier occluded-window run logged a control dispatch timeout during teardown,
so complete RAW/shutdown acceptance still needs a clean final run. Native full
suite caught and prompted corrections for loading-shell geometry and test texture
cleanup; final native gates and current-source overlays are still in progress.

Candidate builds stay in `target/acr-filmstrip/debug/photocraft.exe`. Ordinary
debug executable bytes and timestamp are restored after each cached build;
release and the existing `target/acr18` hosted build are preserved. No default
launcher promotion or release packaging has occurred. This is not a 1:1 parity pass.

All 19 resupplied screenshots remain in the private attachment directory.
Companion `design/camera-raw/reference-inventory.json` now resolves their current
numbers by SHA256, and the auditor resolves registrations by hash. Reference 19
has 24 explicit filmstrip/navigation regions. Artifacts, original screenshot pixels,
native captures, inspection JSON, raw 50% overlays and element differences remain
outside Git under the chat's `acr-filmstrip` visualization directory. The full
every-element/every-reference goal remains active, including shared chrome,
dialogs and unsupported tool behavior.

### Validated filmstrip development checkpoint

PhotoCraft source is committed locally as
`2c2526820f045265cdf7ca7f73ba18c453f33633`. Isolated executable SHA256:
`7285F75BDC2E95C56C46B77539D24DF2B58AC5F7CC6FBE6E2B5354B3497A0D92`.
Both PS1 development launchers now select this candidate; `-EditorBuild acr18`
selects the preserved preceding build. Ordinary installed-editor discovery is
unchanged. No release build, packaging or push.

Final checks: 137 host regressions; 3113 native library tests (13 ignored);
146 desktop tests; 15 Camera Raw/workspace integrations; six overlay-auditor
tests. All-target clippy, layers (30 crates) and wasm (24 checks) pass. Older
Camera Raw catalog omissions were translated in all applicable catalogs to make
the full native suite green, rather than exempting its translation test.

MainWindow synthetic final13 and Nikon RAW final12 pass native clicks, retained
Qt detachment, grid selection, annotations, collapsed arrow/Home navigation,
split comparison, resize/fullscreen, private live-theme reload and clean draft
saves/shutdown. The synthetic check also verifies different original/adjusted
pixels across the comparison split. Two-camera recipe flow passes Nikon D3/Z6
recipe/export equivalence, edit-before-first-poll persistence, document continuation
and native Save. Separate full-editor handoff passes native Save, four grid save
notifications, closed-tab reopen, hidden startup, five themes and zero navigation
hides. Native rendered screenshots were reviewed; desktop-composite screenshots
were not certified on the inactive virtual desktop.

Live validation exposed two host issues and their regressions are covered:
rescans briefly clear `_records`, so the adapter retains the committed browse
window and defers actions until scanning finishes; fullscreen close can destroy
the embedded window while the old Qt layout is restored, so the close guard parks
the editor before that transition. Final checks explicitly reject close-save errors.

Cargo desktop integration tests regenerated the ordinary debug executable too;
after all Cargo commands it was restored to hash
`B7447438BD9459FF61C3E0BE89D1BACF65DEC911FDD60DBF67D2D4CFCF6F3B53`
and original UTC timestamp `2026-10-09T03:15:19.5133667Z`. Release remains
`EA42E1B82F1346091E18C210BD821194AD99CC446CB2FB9425108443049030C5`.

Current evidence has 14 verified native view captures, 447 body-region comparisons
and 24 filmstrip/navigation comparisons (471 total). Frame edges and tray/nav
background average about 0.3 RGB levels from the dithered reference; this limited
measurement is not a complete parity pass. Glyphs, type, remaining chrome and
photo-dependent Fit/thumbnail geometry remain under review. References
6, 7, 10, 11, 13, 14, 15, 16, 17 and 18 still lack individual matching-view audits.
The broader all-19/every-element goal remains active.


### Shared chrome and full-window development checkpoint (2026-10-17)

Companion source commit: `ba7a6316620d7f13bda7675bebe3ba8c6b61fa47`.

Both checkouts remain on `codex/image-triage-integration`. The upstream merge
record above remains the integration base: upstream through
`efa1edd079b259b0b1f77b835151ee904437a231`; no further merge/conflicts in this
continuous session. Actual remotes are `upstream` (storytold) and `origin`
(tylermcm); both fetches succeeded during this session. No push.

The isolated `target/acr-glyphs/debug/photocraft.exe` contains measured filmstrip
glyph corrections, fractional zoom labels, the 39-point header, a filename/camera
heading centered over the preview, Grid and Color Sampler bars, and an ACR workspace
footer using existing Cancel/Done/Open transactions. Sampler Reset/New Sample/Close
and grid density/opacity modify view state. Open Object is disabled pending RAW
object continuation. Workflow output settings remain absent; no fabricated output
profile/bit-depth/ppi label. The Qt fallback detaches only after native readiness.

Both PS1 test launchers now select `acr-glyphs`. `-EditorBuild acr-filmstrip` and
`-EditorBuild acr18` select preserved earlier builds. Existing user processes were
not stopped or overwritten. Re-run the launcher to use the new binary. SHA256:
`7B32C91A72A25865E513BD7807114AA1A42326712490B2C3C59CC5EF934EB841`.
Ordinary debug remains B7447438... with UTC timestamp
`2026-10-09T03:15:19.5133667Z`; release remains EA42E1B8... . No release packaging.

Checks pass: 3116 native library tests (13 ignored), 41 Camera Raw integrations,
146 desktop tests, 137 host regressions, six overlay-auditor tests; all-target
clippy, layers30 and wasm24. Localization assertions inspect the separate
white-balance label/value and three named wheel luminance controls. Alt-drag's
ownership test now keeps the Curve controls inside its viewport; it previously
aimed below the visible area. Native Save, four grid notifications, closed-tab
reopen, hidden startup, five themes, fullscreen, live theme reload, stash/restore,
cleanup and unchanged originals passed live. D3/Z6 recipe exports match live and
persist through document continuation and Save. One refined timing exceeded the
ceiling; timing is diagnostic, not certified. Final synthetic lifecycle and all
18 captures were repeated after reverting only the unsuccessful star trial;
RAW and full-editor checks preceded that visual-only revert.

Current evidence: 447 panel regions, 358 full-client regions and 24 filmstrip
regions, totaling829 with overlaps/repeated controls. Fixed client crops use one
scale/translation, prohibiting individual-control realignment. Native inventory
records exact hashes. Private evidence: this chat's `acr-filmstrip` directory,
`full-windows-final2`, `all-views-shared-ui-final2`,
`all-reference-audit-shared-ui-final2`, `glyph-overlay-final2`, and
`combined-reference-audit-shared-ui-final2.json`. Synthetic gray geometry and its
thumbnail are explicit; photo, metadata and output text differences remain.

Mean RGB differences improve for toggle7.552 to2.556, filter4.485 to2.258,
reject6.817 to3.479 and comparison5.550 to1.659. Thicker stars increased error and
were discarded; final first-star error returns to1.428. No full parity pass.
Refs6,7,10,11,13,14,15 still lack matching individual audits. Full rail, workflow,
grid rendering/settings, fonts/glyphs/spacing, Save Options, Preferences, Presets,
settings menu, Masking, Crop/Expand, Remove, Open Object and every remaining element
across all19 stay in the active goal.


### Accepted settings-menu development checkpoint (2026-10-17)

Companion source commit: `28153cbb`.

The native Camera Raw settings menu now renders all 22 observed rows in 12
sections. The user accepted the displayed reference-11 comparison as practically
identical and requested no further refinement. Its fixed registration measures
41 regions with one scale/translation, including the complete frame, labels,
shortcuts, separators and submenu arrow. Evidence: this chat's private
`acr-filmstrip/settings-menu/accepted-capture/settings-menu-audit.json` and
`overlay/element-audit.json`. It preserves the underlying recipe/document identity.
870 measured regions now include overlaps/repeated controls; this is not a full
parity pass. References6,7,10,13,14,15 still need matching-view audits.

Implemented operations use the existing Camera Raw transactions: reset, committed
previous settings, named dialog snapshots, recipe JSON clipboard copy/paste,
supported XMP/JSON loading and saving. Paste validates before applying atomically,
preserves destination encoding/proxy fields, has a bounded input and a timeout,
and supports undo. Keyboard navigation skips disabled rows. Menu Escape and
focus cannot edit the image behind it. Closing/reopening clears transient popup
state while retaining clipboard availability and previous committed parameters.
Unavailable preset, selected-settings, AI, DNG, export and defaults operations
remain disabled. First external clipboard paste uses the shortcut; the menu is
not enabled until a known copy/valid paste. OS clipboard roundtrip is not certified.

The development launchers now default to `acr-settings-menu`. Executable SHA256:
`38112b97bd45d648edf0a9af7a03376381fd0cf416f20364f31e328232ab5d6f`.
The offscreen snapshot renderer is `d66b09bd92f9523f1914842e4ed3815f282685545ce0260a77c58d3e75bbf3c9`.
Earlier acr-glyphs/acr-filmstrip builds use frozen validated JSON, so newer design
fields cannot break rollback launches. Their executables, acr18, normal debug
(B7447438...; timestamp2026-10-09T03:15:19.5133667Z) and release remain preserved.
No release build or push. Both branches remain codex/image-triage-integration;
upstream/fork fetch and merge records earlier in this continuous session apply.

Checks:3117 native library tests/13ignored, eight menu interactions,41 standard
Camera Raw regressions,146 desktop tests,137 host regressions; all-target clippy,
layers30 and wasm24. Live synthetic and Nikon RAW MainWindow checks passed native
filmstrip detach/restore, navigation, ratings/reject, theme/resize/fullscreen and
cleanup. Full editor handoff passed Save, four grid notifications, closed-tab
reopen, hidden startup and five themes. D3/Z6 recipes matched exports and survived
continuation/Save, although that lightweight harness lacks a thumbnail stub and
logged errors; real MainWindow filmstrip tests independently passed. Timing
ceilings were exceeded and remain diagnostic. Desktop screenshot capture was
not verified by the handoff runner; native/offscreen and hosted screenshots are
separate evidence, not a claim about focus-safe execution.

The user is playing a game: all further agent-driven GUI launches must stay on
secondary monitor without activation. Previous GUI pipeline completed and its
candidate processes exited. No new visible launches have occurred since that
instruction. Test helper now accepts IMAGE_TRIAGE_CHECK_MONITOR=secondary (or an
exact screen name), fails before native-window creation if unavailable, sets
Qt no-focus/no-activation flags and WS_EX_NOACTIVATE before showing, and no longer
calls SetForegroundWindow as reactive recovery. Preview respects the no-focus
flag instead of explicitly activating on open/close. Five offscreen regression
checks cover selection/failure-before-create, placement without a virtual desktop
marker, and absent preview activation calls. Live foreground preservation remains
unverified: continue offscreen until a properly guarded secondary-monitor run.
This is a test helper option, not a change to normal interactive user launches.


### Presets source and offscreen overlay checkpoint (2026-10-17)

Companion source commit: `c5d76df4`, on codex/image-triage-integration. This is
unpromoted UI source; the PS1 launchers continue selecting the validated
acr-settings-menu executable. No desktop executable was rebuilt/promoted for this
step, no visible window opened, no release build or push. Normal debug B744...,
validated editor38112... and releaseEA42... were hash-verified unchanged.
Earlier upstream/fork fetch and merge records apply to this continuous session.

The native tool rail now reaches a Presets category browser with38 observed
category titles. Serialized workspace fields tab=presets and presetGroups control
which groups are expanded. Pointer/keyboard disclosures change only view state;
unknown/duplicate names and mixed invalid requests are rejected atomically.
Preset recipes are absent. Expanding a group explicitly says Not available;
creation, amount and preset management remain disabled. The header management
button does not impersonate the global Camera Raw menu; the existing rail menu
remains usable while the tab is open. Catalog titles are retained verbatim;
their localization is not certified. Other labels use existing translations.

Native Rust Design accepts the measured presets-design.json fragment, and Tokens
supplies scrollbar colors/radii (presets-tokens.json). The auditor merges these
into private layout/token copies, keeping the validated launcher's older JSON
schema usable. The scrollbar has a consistent native width rather than the
generic thin overlay style; its scoped styling does not alter other tabs.

Final evidence: this chat's acr-filmstrip/presets-v4/presets-audit.json and
presets-v4/overlay/comparison.png. Reference10 SHA b7cb9423... is pinned. All88
specified regions have full candidate crop coverage at one fixed registration:
source[26,432,566,2532], candidate[2260,374,2800,2474], 540x2100 pixels at scale2
with viewport1440x1297. Edit/Presets captures retain identical document/photo/
recipe identity, and the synthetic source hash is unchanged. No source photo,
font or proprietary preset assets entered the product. Final PNG was inspected.

Snapshot SHA256:
`69c9aacc8c908da1b08c787889f86bcddb6021dffaf758d71d68655d5bd174bb`.
Private merged layout:
`4ce0258e8ffbef43e600f237116110bf4bc1eba731f2b06b31535febfd98c00c`.
Private merged tokens:
`1459093c9f1170620613602abc82be66a865d29dc55153f1ddeb3d1147d45e56`.
The mean of the88 region RGB means improves13.792 to5.973; scrollbar35.853
to14.286. These are diagnostics, not parity certification. Font/glyph differences,
scrollbar extent, disabled capabilities and shared rail/chrome remain explicit.

Final source passes3118 library tests/13ignored,51 Camera Raw integrations
(including two new view tests), six overlay-auditor tests, all-target clippy,
layers30 and wasm24. Clippy/wasm began after the last Rust source change. Full
native/hosted validation is still required before any new development editor is
selected. No claim is made about live focus preservation: secondary-monitor-only,
nonactivating launches remain mandatory, and this step was entirely offscreen.

The versioned evidence now includes958 regions with overlap/repeated controls.
References6(Save Options),7(Preferences),13(Masking),14(Crop) and15(Remove)
still lack matching-view individual audits. All19 remain in the every-element
goal; no full-reference or complete behavior parity pass is claimed.


### Save Options native source checkpoint (2026-10-17)

Companion source commit: ad960e14, codex/image-triage-integration. No desktop
binary was rebuilt or promoted. The PS1 default remains the validated
acr-settings-menu build38112...; ordinary debugB744... and releaseEA42... were
hash-verified unchanged. Both checkouts stay on the integration branch, and the
upstream/fork fetch/merge records from this continuous session still apply.
No visible GUI launch, release or push occurred.

Save Image in the native settings menu now opens Save Options. It is a real egui
modal driven by72 JSON widget definitions, with typed draft settings in UiState.
Naming choices, extension case and three DNG checkbox drafts support pointer/
keyboard interactions; invalid mixed input is atomic. Cancel/Escape roll back
only the export draft; parent close/reopen clears transient state. The modal has
an explicit height and foreground parent relationship. Its720x900 test verifies
actual wheel scrolling, not merely a visible scroll area. Paste/Y/Escape do not
edit or cancel the photo behind it.

No DNG writer is available in the existing export path. Save remains disabled,
as do unsupported folder/provider actions. Format/compatibility/preview values
are observed readonly choices; other output sections are disabled in DNG view.
No differently encoded file is written with a DNG extension, and no success or
compatibility is simulated. Complete export bindings remain unfinished. Dynamic
filename/folder information comes from the actual document; no reference path,
file stem or Adobe account identity was copied into the product. Default dialog
labels and code labels have contextual/plain lookup coverage in all20 catalogs;
this is not certification of all localized rendered layouts.

The auditor merges save-options-design.json and save-options-tokens.json into
private root layout/token copies, retaining the older validated launch schema.
Final evidence: this chat's acr-filmstrip/save-options-v2/save-options-audit.json
and overlay/comparison.png. All104 specified regions have complete candidate
coverage. The full dialog uses one scale/translation: source[0,0,1574,2256],
candidate[653, 272, 2227, 2528], scale2 at1440x1400. No individual row realignment or
context-value masking. Closed/open captures retain identical document/photo/
recipe identity and the synthetic original. Final PNG was inspected.

Snapshot SHA256:
c05abcf666e2545f316e6321bfd8983b2996c6cc9f19e4f99bef2f59fca944f5.
Private merged layout:
6fae93b6914663bc46e0dd2b61175a86e25ab4dc14f7477a16c0a1cd1d9b944f.
Private merged tokens:
5f7aca24cf6fc78a963b4521d45c480340ef7949271bc03e6d45985cfb022f3e.
Region-mean RGB error improves11.798 to8.856. Source font/glyph/border differences,
capability differences and dynamic context values remain visible; no parity pass.

Checks:3120 library tests/13ignored,54 Camera Raw integrations, six overlay-auditor
tests, final all-target clippy, layers30 and wasm24 pass. Three new integrations
cover menu access, real draft controls/extension selection/Space, Cancel rollback,
atomic validation, protected clipboard/Y/Escape and actual small-viewport scroll.
Geometry validation covers finite/bounded coordinates, duplicate/mandatory IDs,
and a design's inability to enable an output action. All checks were offscreen.
Live secondary-monitor/no-activation preservation remains unverified and remains
mandatory for any future agent-driven launch. Hosted validation is still required
before selecting a new editor executable.

Versioned measured regions now total1062 with overlaps/repeated controls.
References7(Preferences),13(Masking),14(Crop) and15(Remove) still lack individual
matching-view audits. All19 remain in the complete every-element goal, including
unimplemented output behavior and every remaining shared/native element.


## Preferences General source checkpoint — 2026-10-17 (unpromoted)

Native source commit66c5b156 adds the measured Preferences General modal to the
existing gear popup. Both repositories remain on codex/image-triage-integration.
This continues the same integration session; no new upstream merge or fetch is
claimed here. The previously recorded fetched/merged upstream efa1edd079b259b0b1f77b835151ee904437a231
and merge3b89ae1e remain the integration baseline. No conflict resolution occurs
in this source checkpoint. No push or release build occurred.

Supported settings use existing native view behavior: Single/Multiple edit
panels, filmstrip filenames and50/100/200% default image click zoom. A typed
serialized draft uses OK to persist and Cancel/Escape to discard. Startup
validates stored profiles; malformed values report an error and do not apply.
Unset preferences retain the JSON filmstrip flags. Preferences are independent
of photo pixels/recipe/history. Unsupported pages/options stay visibly disabled,
including ratings+color labels, orientation, theme, rich tooltips, Lightroom-style
zoom/pan, compact layout and legacy undo/TAT. The title does not impersonate
Adobe's version number. Existing scope gear entries remain usable. Dialogs are
mutually exclusive; parent close clears transient drafts. Corrected optional
filmstrip rating-star/tooltip-separator encoding. Label lookup coverage is checked
in all20 language catalogs, without claiming all localized layouts are audited.

Private auditor layout/tokens merge preferences-design.json/preferences-tokens.json;
main live layout/tokens and the PS1 default acr-settings-menu remain unchanged.
Final evidence in this chat's acr-filmstrip/preferences-v2/preferences-audit.json
and overlay/comparison.png:53 regions, complete candidate coverage, one full-dialog
registration. Source[0,0,2004,1530], candidate[438,635,2442,2165], scale2 at1440x1400.
Both captures preserve photo/document/recipe identity and original fixture hash.
Final PNG inspected; type, glyph, disabled capability and dynamic title differences
remain visible. No every-element parity pass is claimed.

Snapshot SHA25655d537f8a8ed38568529c84f968f58ab63245963699d66c6895face8d9f2c326.
Private layout0b31d384e0b18a1779493b43295295f3a677d613d971f54d7df240fe4650c0f3.
Private tokens7f0ca6148704869c2c76932acc517479ca3327530c1e0f3787ef83a46de41853.
The shared auditor also rechecked Save Options in preferences-save-regression.

Final checks:3122 library tests/13ignored,58 Camera Raw integrations,6 auditor tests,
all-target ui+desktop clippy,layers30,wasm24 pass. Four new integration tests cover
actual pointer controls/OK/Cancel, persisted startup settings and malformed profiles,
click-zoom behavior, atomic invalid patches and Escape ownership. Initial scanner
empty-label and test lint issues were fixed before final checks. No visible window
was launched. Secondary-monitor/no-activation focus preservation remains unverified;
it is mandatory for any future agent-driven visible run. Native+hosted behavior
validation remains required before promoting a new desktop executable.

Rechecked protected hashes:target/debug/photocraft.exe
b7447438bd9459ff61c3e0be89d1bacf65dec911fdd60dbf67d2d4cfcf6f3b53;
selected target/acr-settings-menu/debug/photocraft.exe
38112b97bd45d648edf0a9af7a03376381fd0cf416f20364f31e328232ab5d6f;
release ea42e1b82f1346091e18c210bd821194ad99cc446cb2fb9425108443049030c5.
All unchanged. Original Qt filmstrip remains retained/detached.

Versioned measured regions total1115, including overlapping/repeated controls.
Matching-view individual audits still absent for13(Masking),14(Crop),15(Remove).
Every element across all19 and unavailable behaviors remain in the active goal.


## Crop & Expand source checkpoint — 2026-10-17 (unpromoted)

Native commitc3938d48 adds the measured Crop & Expand panel and native rail entry.
Both branches remain codex/image-triage-integration. This continues the same
integration session; no new fetch/merge is claimed. Previously recorded upstream
efa1edd079b259b0b1f77b835151ee904437a231 and merge3b89ae1e remain the baseline.
No merge conflicts or resolutions occur in this checkpoint. Shared workspace,
control validation and rail changes were reviewed; existing scope/settings/
presets/preferences/save/zoom integrations pass. Host code is unchanged.

crop-design.json contains measured geometry for preset, angle, rotation/flip,
Generative Expand, Trim, Geometry/Upright and Anamorphic. Workspace tab "crop"
and cropGroups (default["Geometry"]) are serialized pure view state. Pointer and
Space/Enter operate disclosures. Invalid/duplicate groups reject mixed requests
atomically before any photo edit. Geometry collapse moves lower sections up;
unsupported expanded sections explicitly show Not available. Processing controls
have disabled accessibility state and unavailable hints. Camera Raw's current
recipe/preview/commit has no crop/geometry transforms; no transform is simulated.
Original vector marks use native shapes, with no copied source assets/fonts.
Label lookup coverage spans20 catalogs; localized layouts are not all certified.

Final evidence: this chat's acr-filmstrip/crop-v3/crop-audit.json and
 overlay/comparison.png.81 named regions have complete candidate coverage at one
fixed dock registration: source[98,432,638,1892],candidate[2260,374,2800,1834],
1440x1000/scale2. The auditor checks the exact loaded JSON descriptor, pinned
reference hash, unchanged renderer/original fixture and identical photo/document/
recipe identity between Edit and Crop captures. Final PNG inspected. Two stale
spec entries (absent Trim eye and duplicate Expand options) were removed after
reference inspection. Remaining font, icon and capability differences are visible.
Mean region RGB error15.042 versus initial14.831; no numerical improvement or
pixel-parity pass is claimed. Geometry background extent and semantic glyphs were
corrected while retaining the measured differences.

Renderer SHA256b1d54c6c7a7cd317919c3c473d70c37c78e83c1ae27ebf728772b66406061a43.
Private layout1b55cb63e522a7538af762cf927b65c6b65513fbd14e8eb3d84481ba68e30a55.
Private tokensd94d6de0cb20d7f0b00be2d2317af93b76f07a2498a841d7090fb1e8e917e896.
3123 library tests/13ignored,60 Camera Raw integrations,6 auditor tests,
all-target native ui+desktop clippy,layers30,wasm24 pass. An initial rail-label
translation lookup failure was corrected before final gates. No GUI window,
new desktop executable/promotion, release or push. Selected PS1 editor remains
acr-settings-menu. Protected debug/selected/release executable hashes rechecked
and unchanged from the Preferences checkpoint. The old Qt strip remains retained
and detached. Live secondary-monitor/no-activation behavior remains unverified;
any future agent-driven visible launch must enforce it. Hosted behavior validation
is still required before selecting a new editor build.

Inventory correction: summing all21 region-spec files gives1280 regions, including
overlap/repeated controls and unaudited Masking/Remove specs. Earlier1062/1115
numbers were partial counts, not full totals or parity evidence. Matching-view
individual audits remain absent for13(Masking) and15(Remove). Every element across
all19, unavailable processing and complete hosted validation remain in scope.


## Masking creation source checkpoint — 2026-10-17 (unpromoted)

Native source advances c3938d48 to074f8864 on codex/image-triage-integration;
host branch is unchanged. This continues the same integration session, with no
new fetch/merge claimed. Previously recorded upstream efa1edd079b259b0b1f77b835151ee904437a231
and merge3b89ae1e remain the baseline. No merge conflicts/resolutions occur here.
Shared workspace, rail, keyboard gating and close/reopen changes were reviewed.
No host code, release build, push or editor promotion.

Masking now has measured native creation cards, tool rows/keycaps, People status
and help. Geometry comes from masking-design.json, colors/radii from existing
Tokens, glyphs from original native vectors/existing licensed icons. No reference
assets/fonts copied. Serialized view flags maskingPeople and maskingHelp support
actual pointer/Space/Enter disclosure and native availability help. Escape/Y do
not reach the photo while help is open; closing Camera Raw clears transient help.

Local mask creation cannot preview/replay in Camera Raw's current recipe/commit.
Whole-filter selection/filter-mask coverage is a different existing capability.
Subject/Sky/Background/Landscape/Objects/Brush/Linear Gradient/Radial Gradient/Range
creation controls remain disabled. People text reports actual unavailability rather
than fabricating person detection on the synthetic image. Help is local guidance,
not the external Adobe documentation workflow. These capabilities and text differences
remain unfinished parity work. Label lookups are covered in all20 catalogs;
localized layouts are not all certified.

Final evidence: this chat's acr-filmstrip/masking-v2/masking-audit.json and
 overlay/comparison.png.35 named regions have complete candidate coverage at one
fixed dock registration: source[34,432,574,1492],candidate[2260,374,2800,1434],
1440x1000/scale2. Auditor checks pinned source hash, exact loaded JSON descriptor,
stable renderer/original fixture and identical photo/document/recipe identity
between Edit/Masking captures. Card bounds were remeasured from border pixels,
dark fills added, and Range indicator corrected after the initial render. Final
PNG inspected; mean region RGB difference19.873 to17.231. Font, glyph, People text
and unsupported processing differences remain; no parity pass or masking of them.

Renderer SHA2562d3d6d44a2923e13f03315eb1fb18169fae538f37ac57fb427b8fe0930b165b7.
Private layoutea4059c724858cca5276fabedb5df67faef4f7d023e2b7d25186e9ba413cf280.
Private tokensd94d6de0cb20d7f0b00be2d2317af93b76f07a2498a841d7090fb1e8e917e896.
3124 library tests/13ignored,62 Camera Raw integrations,6 auditors,final all-target
ui+desktop clippy,layers30,wasm24 pass. Two new native integrations cover disabled
cards/tool rows, People pointer/Space, inactive K/G/J photo actions, help Escape/Y
ownership, parent-close/reopen cleanup and atomic malformed requests. Initial
Background-label query ambiguity and one test-only initializer lint were fixed;
the affected unit was rerun after the lint fix. No runtime behavior change in that
lint repair.

Main live JSON/tokens and PS1 selected acr-settings-menu editor remain unchanged.
Protected debug/selected/release hashes rechecked and unchanged from the previous
checkpoint. All work ran offscreen; no game focus interaction. Live secondary-
monitor/no-activation behavior remains unverified and mandatory for a future
agent-driven visible launch. Complete native+hosted gates remain required before
selecting a new editor executable. Remove15 is now the only view without a matching
individual screenshot audit; every element across all19, processing bindings and
hosted validation remain in the active goal.


## Remove source checkpoint — 2026-10-17 (unpromoted)

Native source advances074f8864 to4d022559 on codex/image-triage-integration;
host branch is unchanged. This continues the same integration session with no
new fetch/merge claimed. Previously recorded upstream efa1edd079b259b0b1f77b835151ee904437a231
and merge3b89ae1e remain the baseline. No conflicts/resolutions occur here.
Shared workspace/rail, control validation, keyboard gating and close/reopen
changes were reviewed. No host code changes, release, push or promotion.

Remove now renders the observed modes, settings/feedback/overlay/spots rows and
Reflections/People/Blemishes/Dust sections from remove-design.json. Original
native glyphs use existing Tokens; source assets/fonts were not copied. Serialized
removeGroups/removeHelp support pointer/Space/Enter header disclosure, correct
lower-row displacement and native availability help. Escape/Y cannot reach the
photo behind help; parent close clears it. Invalid/duplicate group requests and
malformed help flags reject mixed requests atomically.

Current Camera Raw cannot store/replay Remove/Heal/Clone or automatic distraction
operations. Processing, reset/preview, size/detection/overlay/spots settings and
feedback submission remain disabled. Checked Generative AI and Size7 are readonly
observed presentation, not active processing or editable brush settings. No remote
operation, feedback message or successful retouch/detection is simulated. Full
processing behavior remains unfinished. Descriptor/help labels have lookup coverage
in20 catalogs; localized layouts are not all certified.

Final evidence: this chat's acr-filmstrip/remove-v2/remove-audit.json and
 overlay/comparison.png.49 named regions have complete candidate coverage at one
fixed dock registration: source[88,432,628,1522],candidate[2260,374,2800,1464],
1440x1000/scale2. Auditor checks pinned source hash, exact loaded JSON descriptor,
stable renderer/original and identical photo/document/recipe identity between
Edit/Remove captures. Initial overlay exposed oversized selection/icon plates;
blue-frame bounds and icon-plate bounds/colors were remeasured from reference
pixels. Glyph ROIs now include complete backplates. No independent alignment or
capability masking. Final PNG inspected. Mean region RGB difference15.995 to13.947
using identical final ROIs; initial capture was re-audited in
remove-v1/overlay-final-regions. Font/glyph/capability differences remain; no pass.

Renderer SHA2566d8410b809036c6f7364e8a233407cf380cb20a0d6f715994a76c243de13bfda.
Private layout2c43d2a4804893da80d014b3a45524c3a2cda34b5d74d2dc229f75ebb4c1926d.
Private tokensd94d6de0cb20d7f0b00be2d2317af93b76f07a2498a841d7090fb1e8e917e896.
3125 library tests/13ignored,64 Camera Raw integrations,6 auditors,all-target
ui+desktop clippy,layers30,wasm24 pass. Two new integrations cover disabled
processing/feedback, actual header/Space disclosures and lower-row displacement,
atomic invalid requests, help Escape/Y ownership and close/reopen cleanup.
All work offscreen; no visible app launch or focus interaction. Main live layout/
tokens, selected PS1 acr-settings-menu editor and protected debug/selected/release
hashes were rechecked and remain unchanged. Live secondary-monitor/no-activation
behavior remains unverified and mandatory for any future agent-driven visible run.
Native+hosted gates remain required before selecting a new editor executable.

All19 supplied views now have recorded matching-view captures at some source
revision. This is not a current-build or every-element pass: revised Red Eye,
fresh all19 source matrix, incomplete element/shared-chrome coverage, remaining
visual differences, processing and full hosted validation remain in the goal.
The accepted settings-menu comparison is not reopened for cosmetic refinement.


## Common 19-reference matrix — 2026-10-17 (unpromoted)

Native audit/tooling commitdcda93d8 builds on runtime4d022559; no Rust UI/runtime
change. Both branches remain codex/image-triage-integration. Same integration
session; no new fetch/merge is claimed. Previously recorded upstream
efa1edd079b259b0b1f77b835151ee904437a231 and merge3b89ae1e remain the baseline.
No conflicts/resolutions or host code changes in this checkpoint.

Added scripts/audit_camera_raw_matrix.py and six verifier tests. The matrix merges
six design fragments and three token fragments into private copies, preserving
the accepted menu geometry. All19 views use one renderer and common base profile.
It pins renderer/design/reference/checker inputs, verifies actual loaded fragments,
workspace/panel ordering, scope/navigation/filmstrip and output sizes, and checks
photo/document/recipe identity. A real setup commit creates Previous Settings;
its menu-open capture is compared against an equally committed closed-menu baseline.
Every section in one reference shares one scale/translation; the extra filmstrip
crop derives directly from the same full-client transform.

The first attempt verified1–18, then its stricter navigation check stopped on19:
command zoom "fit" serializes as native null. This was a checker representation
issue, not a render failure. Added strict fit/numeric-zoom tests and reran the
complete matrix. Final matrix-v2/matrix-audit.json verifies all19 captures,21
versioned specifications and1280 overlapping/repeated named regions. Six matrix
and six existing comparator tests pass. Runtime native checks from the preceding
checkpoint remain applicable; no Rust or production layout/token files changed.

Renderer SHA2566d8410b809036c6f7364e8a233407cf380cb20a0d6f715994a76c243de13bfda.
Merged base layout47ddc3d1fafc86f20ce360320a6075b6ca53aab571f3195a5d397db5756cb450.
Merged tokensb4aace2dc4d44f3976d06eed7590ba9bde8bbc20363bddb491e7f7aca2db8d30.
All21 source/native pairs inspected through four contact sheets; Curve/Mixer and
lower panels also inspected individually. Current Red Eye is now recaptured.

This is not a parity pass. Visual review found concrete structural gaps:
- Color Mixer lacks Mixer/Point Color row, Adjust caption and ninth swatch;
  its selector/gradients differ and missing rows shift later sections upward.
- sidebar-ref02-diagnostic/comparison.png aligns the fixed histogram/metadata;
  native Edit/Profile bars remain visible where ACR has scrolled them away.
  Rotated panel order and section-only crops conceal the whole-sidebar mismatch.
  Panel captures also use taller viewports than the source client.
- Curve target control/Adjust spacing, wheel adornments, lower-panel spacing,
  glyphs/disabled styles, context-dependent values and unavailable operations
  remain different. Partial crops leave surrounding chrome/footer areas unreviewed.

See design/camera-raw/matrix-followup.json for specific next work. Compared source
crop fractions are recorded but are not element-completeness estimates; missing
UI can still have complete candidate pixel coverage. No difference masking,
independent element alignment or full-reference/every-element completion claim.
The accepted settings-menu comparison is not reopened for cosmetic refinement.

Everything remained offscreen. No desktop executable, release, push, visible launch
or promotion. Selected PS1 acr-settings-menu editor and main live JSON remain
unchanged. Live secondary-monitor/no-activation behavior is still unverified and
mandatory for future agent-driven visible runs. Structural fixes, fresh matrix
and full native+hosted gates remain required before selecting a new editor build.
The every-element/all19 goal remains active.


## 2026-10-18: visual acceptance and functionality priority

The user accepted the remaining all19-gallery appearance, with two explicit exceptions:
excess gap below Saturation and missing Glow row. Further discretionary cosmetic
refinement is deferred; prioritize tool functionality. This is owner acceptance,
not a new claim of pixel-identical/every-element measured parity.

Implemented a separate Color bottom padding of12pt instead of the shared23pt,
and restored the35pt Glow slider/disclosure row. Glow remains disabled at zero:
there is no CameraRaw Glow operation, and no invented recipe key is written.
All20 catalogs include its label. Kept the previous uncommitted Mixer work.
No backend/tool implementation is claimed in this visual correction.

Fresh offscreen source/native comparison:
`acr-filmstrip/effects-fix-capture/effects-comparison.png` in the task visualization
directory. Effects and Curve dividers now line up with the source in the fixed
registration. The other visual differences are left alone per user instruction.

Next functional milestone: Red Eye. Existing `paint.redEye`/`algo::redeye` can
supply correction, but `paint.redEye` directly edits document pixels. Do not call
it on the real photo from inside an uncommitted Camera Raw dialog. Add durable
per-photo correction operations and preview/replay integration first, with Cancel,
undo/redo, navigation/reopen and saved-display consistency tests. The current v1
recipe serializes only CameraRaw settings and has no red-eye operation list.

Both remotes fetched successfully during the PR investigation: upstream main
advanced from efa1edd079b259b0b1f77b835151ee904437a231 to5c89f16b.
That new tip was inspected but not merged into this divergent integration while
preparing visual-only contribution options; no conflicts were resolved or merge
validation claimed. No PR or push was made. Existing host user edits preserved.

No desktop launch, release or editor promotion. PS1-selected known-good settings-
menu executable remains unchanged. New source appearance requires a new validated
candidate before promotion; hosted handoff checks remain outstanding.

Validation: initial library run3125 passed/13ignored with one new tooltip-context translation failure; corrected to the existing cameraRaw translation context and that test passed on rerun. Camera Raw workspace/Mixer/localization integration checks passed (including disabled Glow/no recipe or history mutation); all-target UI clippy, layers and wasm passed. Fresh source/native PNG inspected. No further cosmetic work or hosted promotion performed.

2026-10-18 follow-up: added muted-spectrum Vibrance/Saturation tracks from the supplied ACR reference, via a shared theme token palette. Existing slider ranges, bindings, recipe semantics and spacing unchanged. Workspace tests11/11, all-target UI clippy, layers and wasm passed; fresh offscreen comparison inspected at acr-filmstrip/color-gradients-capture/comparison.png. No visible launch or editor promotion; launcher remains on preserved older build.


## 2026-10-18: usable main-adjustments development candidate

Implemented the three requested features: Glow processing (Diffusion/Bloom/Halation,
Range/Spread/Warmth), Color Mixer All Colors with all eight HSL bands, and Point
Color (eight samples, picker, H/S/L/Variance/Range and view-only range visualization).
Shared CameraRaw serialization preserves controls through per-photo recipes and
native project history; private PhotoCraft XMP extensions round-trip the new
settings. Unsupported PSD adjustments fail explicitly. Older hosted editors and
RAW-recipe renderers are rejected when these new settings would otherwise be lost.
Processing is independently implemented; Adobe pixel-output parity is not claimed.

Candidate: C:/Users/user0/Documents/photocraft/target/acr-main-adjustments/debug/photocraft.exe
SHA256: cac833711778b15699ccac589a30e1cb1ad2fbf3ad88ac0933b261b0fc0f1097
Test launch (user initiated):
`& "C:/Users/user0/OneDrive/Documents/Playground/scripts/test_camera_raw.ps1" -EditorBuild acr-main-adjustments`
Open a photo in the popout and press E. Launcher default remains acr-settings-menu.
No desktop launch, release build, push, PR or default promotion was performed.
Both checkouts remain on codex/image-triage-integration; existing edits preserved.
Upstream remains the previously recorded fetched-but-unmerged 5c89f16b tip.

Validation: host focused suite111 passed/2 skipped; UI library3127 passed/13 ignored
before final narrow fixes; latest focused workspace/settings/localization/Mixer26
passed; final Mixer6 passed; algorithm finishing5 passed; engine finishing2 and
noise3 passed; adversarial command test passed; I/O13 passed/1 ignored.
Final native development app and snapshot renderer built; affected-crate all-target
clippy, layers and wasm checks passed. Temporary external opt-level0 overrides for
algorithm/engine avoid local optimized compiler stalls; repository profiles unchanged.
Full real-file corpus attempt blocked by missing local photocraft-corpus checkout.

Offscreen native processing proof and metadata:
C:/Users/user0/.codex/visualizations/2026/10/17/01a14901-444b-7781-b5e6-48730a8f5183/acr-filmstrip/main-adjustments-proof
Captures precede the final small SDR picker-selection clamp fix (covered by regression).
Known-good target/debug and acr-settings-menu executables retained and hash verified.
Live hosted navigation, stash/restore, native Save/grid propagation, closed-tab reopen,
hidden startup, theme/resize/fullscreen, shutdown and process-cleanup gates remain
pending user testing. Do not promote this candidate before these checks pass.
Other disabled controls remain in Optics, Lens Blur, Calibration and AI Detail;
completion applies to the three features requested here, not the entire window.


2026-10-18 push checkpoint: see CLAUDE_HANDOFF.md. Fresh fetch succeeded; upstream advanced5c89f16b ->56dbc89a. Merge deferred for this user-requested tested-state handoff; no conflict resolution or merge validation claimed. Both integration branches pushed only to user forks.


## 2026-10-19 upstream merge: storytold/photocraft d95fdf31 (v0.10.0)

* Old upstream tip merged earlier: 56dbc89a (recorded above as deferred); new upstream tip `d95fdf31` (`origin/main`, tag `v0.10.0`).
  Fork tip before the merge: `12b459f5` (`fork/codex/image-triage-integration`, fast-forwarded from `c00bd4aa`).
  Merge commit on `codex/image-triage-integration` in `../photocraft`: `99cdc8b4`. Not pushed.
* 292 upstream commits, 303 files; 22 textual conflicts, resolved individually:
  * `apps/photocraft/src/main.rs`: took upstream's `parse_launch_args`/`LaunchArgs`/`--help`/`--`; unknown `-` options are now
    exit 2, so the host's flags must be registered. Re-added only `--hosted` and `--raw-workspace` (host behaviour upstream lacks).
    Every flag `photocraft_bridge.py` and `preview_controller.py` pass (`--hosted --control --control-token-file
    --automation-read-root --automation-write-root --raw-workspace`) is accepted. Upstream's missing-value usage errors replace
    our silent `--control-token` fallback. Automation authorization is otherwise upstream's.
  * `crates/algo/src/camera_raw/local_tone.rs`: kept ours. Our Shadows/Highlights already contained upstream PR #7141 (`4b660fcf`)
    and adds the parallel per-reference loop on top; upstream has nothing else in the file.
  * `crates/ui-egui/src/camera_raw_scope_ui.rs`: upstream's `paint_with(.., Spikes::Clip)` (#7419) with our
    `camera_raw_design.histogram_corner_size` token instead of upstream's literal 18.0.
  * `crates/engine/src/lens_cmds.rs`: both kept: upstream's exposure clamp -5..5 (#6876) and our `validate_finishing()`.
  * 18 `crates/ui-egui/src/i18n/*.tsv`: union of both sides' entries (checked: no key from either side lost).
  * Clean merges reviewed for CR: `raw/develop.rs`, `raw/tone.rs` (#7416 NaN fix), `engine/tests/camera_raw_exposure_clamp.rs`.
* Validation: `cargo check -p photocraft --all-targets` ok. `shadows_bench --hash`: all 20 hashes identical to before the merge;
  Shadows +60 ~70 ms, Highlights -60 ~73 ms (900x600 proxy; was ~64/66). Tests: algo+raw 587+ passed; engine lib 1776 passed
  with `--test-threads=1` (the parallel run of the lib binary crashed with 0xc0000005, and a parallel link of all engine test
  binaries hit linker 1140/0xc00000fd; both look environmental, not seen single-threaded or with `-j 2`); exposure clamp 2 passed;
  app bins 152 passed (launch-args tests); ui-egui `camera_raw` 95 passed.
* Not done: clippy, `cargo xtask layers|wasm`, a rebuilt hosted executable, and the live Image Triage handoff gates (persistent
  filmstrip navigation, stash/restore, native Save and grid propagation, closed-tab reopen, hidden startup, theme/resize,
  fullscreen, shutdown/process cleanup). Keep the known-good hosted executable until those pass.
* Candidate build of merge `99cdc8b4`: `C:/Users/user00/Documents/photocraft/target/merge-d95fdf31/release/photocraft.exe`
  (`cargo build --release -p photocraft -j 4`, own target dir, 15m56s). PE subsystem = GUI (2), so no console window (the
  `windows_subsystem` attribute in `main.rs` only applies without debug assertions; dev builds show a console).
  SHA256 f89342ebc8735edff6ae0403348ded82e9a2513cc9280527ef2e7f604bc90000. Not promoted; known-good executables untouched.

## 2026-10-20: cascade audit session status check (no integration)

Both `origin` and `fork` fetched successfully. The cached upstream main advanced
`46e538b3` -> `9b9643764f3fb77d9f0e48d7510983ae50621e11`.
Local `codex/image-triage-integration` and its fork tracking branch both point to
`74dc329068732f419c43f1548827c434977fb657`; clean checkout, no fork-only commits.
HEAD versus origin/main is 78 / 119 exclusive commits. Latest version tag remains
v0.10.0, already integrated; newly fetched older tags do not call for a merge.

`git merge-tree --write-tree --name-only HEAD origin/main` reports 21 conflicts:
`crates/ui-egui/src/widgets.rs` and `crates/ui-egui/src/i18n/` tables
cs, de, el, es, fr, hu, id, it, ja, ko, nl, pl, pt-br, ru, sr, tr, uk, vi,
zh-hans and zh-hant. No textual Camera Raw conflict. The widget conflict is one
small FieldOpts destructuring hunk; each translation has two hunks (larger in de/it).
The textual conflict surface does not look like a large Camera Raw integration
conflict. Automatic merges include Camera Raw, services, menus and state and would
still require individual compatibility review on integration.

Decision: defer merging until a new release under the repository policy. No
resolutions, rebuild, live handoff validation, promotion or push in this session;
known-good hosted executables preserved. The session's work is the read-only
cascade logic/evidence audit recorded in `docs/cascade_logic_audit.md`.

## 2026-10-20 evening: editor mask buttons follow the host's models

PhotoCraft `codex/image-triage-integration` fast-forwarded from `74dc3290` to `0988078e` (one commit): `ui.hostJob` accepts `tools` (a list of host-job kinds the host can run; `[]` = none; not sent = nothing greyed), capability `cameraRawHostTools: 1` in `app.handoff`; the Subject and Background cards, the Landscape link and the People link are disabled with "Needs Editor tools in Image Triage (Set Up AI)." when their kind is missing. Image Triage sends the list only to editors that advertise the capability (`HostJobRunner.poll`, `photocraft_bridge.camera_raw_host_tools_supported`). PhotoCraft checks run: `cargo check -p photocraft-ui-egui`, the `host_job` and `host` unit tests (17 and 48 passed). `cargo clippy -D warnings` fails on warnings that were already there (`photocraft-algo` `calibration.rs:74`, `masks.rs:401`, three collapsible-ifs in `camera_raw_mask_edit_ui.rs`), none in the new lines. Not run: the Image Triage handoff checks against a rebuilt hosted editor. **The hosted executable must be rebuilt before the behaviour appears**; keep the known-good build until those checks pass. Pushed to the fork: `codex/image-triage-integration` at `0988078e`.
