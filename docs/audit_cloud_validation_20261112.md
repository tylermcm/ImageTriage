# Audit cloud continuation — 2026-11-12

Continues `docs/audit_windows_handoff_20261112.md` from checkpoint `2051d1d`.
Implementation is on `codex/audit-cloud-continuation-20261112`, commits `5063516`, `2fefbd0`, `542f69a`, `651279e`, `5bc1944`, `427a2bb`, and `83b4cb5`.
The checkpoint is preserved. This is a source build and validation record, not an MSI release.

## Culler cleanup

The task no longer starts a blocking stdout reader thread. Protocol reads poll available
bytes (`select` on POSIX, `PeekNamedPipe` on Windows), retain incomplete UTF-8 until a
whole line arrives, and enforce a 1 MiB byte limit. Cancellation is checked between
reads. When the launcher exits, available events are drained without waiting for an
inherited pipe handle to close. Closing stdout cannot wait on another thread's buffer lock.

Windows now creates its named kill-on-close job before launch. A stdlib bootstrap
joins and verifies membership from the interpreter that actually runs the worker, then
executes the worker in that same process. This replaces the suspended-launcher/Toolhelp
experiment: launcher membership is no longer used as proof that the real interpreter
is owned. The temporary child job handle is closed before the worker starts; the host
holds the lasting handle. The bootstrap is selected from the current host (or its
frozen external-worker bundle), so an older managed-runtime source snapshot cannot
supply an incompatible cleanup protocol. Cleanup checks job accounting until descendants have stopped,
with a bounded wait, and closes handles even on errors. Launcher and pipe cleanup runs
if job cleanup raises. POSIX keeps its separate session/subreaper.

Regressions cover cancellation at metadata/inference, a partial JSON line, inherited
stdout after exit, oversized unterminated events, split UTF-8, and draining 150 progress
events plus completion without a final newline. Windows cancellation additionally
asserts that recorded worker/descendant processes have stopped. A missing cleanup job
must refuse to execute the worker.

## Qt and schema checks

Test startup selects the same Basic Quick Controls style as the app before QML loads.
A new regression repeatedly creates and deletes the QML engine, opens AI Center and
Settings, traverses native items, and forces `gc.collect()` while the engine is alive.
It checks wrapper types and validity; garbage collection is not disabled and invalid
objects are not ignored. This tests the handoff's failure mechanism but does not establish
the cause of the original Windows access violations.

The existing fake QML culler shutdown check now uses the test interpreter and a real
bootstrap source root. It no longer skips merely because an optional engine runtime
is absent; no models are needed by its stdlib fixture.

The companion schema check was stale: upstream calibration fields have serde aliases
for Image Triage's names, and `raw_recipe.rs` writes those host names back. The check now
recognizes the explicit aliases and excludes host-only crop. It still rejects missing
or unexpected native fields; host recipe validation and rendering were not relaxed.

## File-launch selection follow-up

An installed-wheel, non-grab startup check exposed a real integration defect: a
second launch was acknowledged, but the requested photo reverted to the first grid
row. `LibraryNav` restored it on `loadingChanged`; the later `folderChanged` handler
then reset the grid cursor. Model/backend-only tests saw the correct restored signal
but did not check the displayed row.

`PhotoModel` now publishes folder/subfolder context before announcing loading
completion. The requested/restored cursor is therefore applied after QML's reset.
Two QML regressions (initial file launch and a second launch, with child-folder rows
before photos) both failed before the fix and pass afterward. The related startup,
folder scrolling, shell and collection subset passed 39 cases. The installed wheel
then passed normal startup, a real second-process file handover, selection of the
requested photo, and ordinary window-close/shutdown on Linux.

## Demo detail and viewer lifetime follow-up

A later full local run exposed one intermittent retouch button failure (2,590 passed,
49 skipped, 5 xfailed; one failure). The retouch assertions were retained. Inspection
found that edits could arm the full-resolution detail timer even in memory/demo mode;
its callback started a real native job and temporarily set the editor busy. A new
regression failed before the fix for demo edits and passed for real edits. The callback
now refuses native work in demo mode while continuing to queue real editor detail.
The preview/retouch subset passed 29 cases after the guard.

Viewer fixtures also explicitly shut their worker service down and delete their QML
engine: an unsaved synthetic draft can reject ordinary window close. The pointer test
additionally verifies that the mouse area's pressed state is released. No original
assertion was removed or relaxed, and GC is enabled. The affected pointer check passed
20 consecutive diagnostic repetitions; the broader RAW/viewer group passed 121 cases.
Disposable diagnostic tests and screenshots were not committed.

## Windows path and cleanup follow-up

The completed `651279e` CI run passed Linux but failed 25 Windows checks: temporary
paths used both `RUNNER~1` and `runneradmin`. Most failures were assertions comparing
spellings. The runtime failures also exposed duplicate equivalent Python/DLL search
entries. Bootstrap search paths now deduplicate by canonical filesystem identity,
while preserving the empty Python path entry's dynamic working-directory meaning.
Runtime precedence checks recognize aliases without allowing bundled packages to
shadow the managed runtime. Location assertions resolve their expected fixture paths;
no test redirects the temporary directory or skips these checks.

A second run at `5bc1944` passed 133 path checks and failed one: edit discovery could
rediscover a stack member recorded through an alias. External stack paths now use
the scanner's canonical key; candidates retain the existing normalized-parent fast
path. An alias regression failed before this fix and passes afterward. The complete
local path subset passed **132 tests**, with five platform/optional-runtime skips and
15 passing subtests. Windows alias regressions use real 8.3 names; Linux uses symlinks.

The next Windows run caught one intermittent cancellation cleanup failure: cancellation
returned promptly but the staging directory remained. The task had suppressed all deletion
errors. It now retries Windows access/sharing/lock/directory-in-use errors for at most
one second, then reports an explicit failure with diagnostics if cleanup still fails.
Tests cover a transient lock, a persistent lock/deadline and preservation of the active
generation. The final local handoff subset passed **36 tests**, with three skips.
The final host run at `83b4cb5` passed both platforms: **Windows 2,622 passed,
29 skipped, 5 xfailed, 129 passing subtests**; **Linux 2,602 passed, 51 skipped,
5 xfailed, 117 passing subtests**. The separate Windows gates passed 37 handoff
tests (two skips) and 135 path tests (27 passing subtests, no skips). CI:
https://github.com/tylermcm/ImageTriage/actions/runs/43246330088.

## Validation

- Full local Python 3.13.5 / PySide6 6.12.0 suite after the selection and fixture
  fixes: **2,591 passed, 49 skipped, 5 xfailed**, plus 117 passing subtests, in
  714.22 s. GC stayed enabled. The final demo-detail guard and its two new cases
  were then covered by the 29-case preview/retouch subset and native Windows checks.
- Windows handoff CI passed at final application commit `651279e`: culler process
  ownership/cancellation, QML shutdown/lifetime, file-launch selection, demo/native
  detail routing and the retouch viewport/control check. This is standard CPython;
  the owner's Store Python/managed-runtime acceptance remains separate.
- Built the unchanged pinned CLI-Culler and bracketeering wheels, resolved/installed a
  separate hashed Linux core runtime, and verified its installed distribution identities
  against `runtime.json`. With that interpreter selected, task/QML worker tests passed
  **25 cases with no skips**, including real generation activation/reopen and shutdown.
  These use `models=[]`: foundation/core execution, not ranker or mask inference.
- Related startup/grid/collection checks: 39 passed. RAW/viewer checks: 121 passed.
  The original suspect UI files passed 166 cases on Python 3.13 with GC enabled.

The earlier full run at `651279e` completed: Linux passed, Windows failed the path
checks described above. Later partial runs are diagnostic evidence, not full Windows
passes. Current Windows/Linux CI is linked in the follow-up above.

The continuation branch runs both test jobs. Windows additionally runs the handoff
and path subsets first, builds a source wheel only after passing tests, and retains
JUnit reports and the wheel. Its successful Windows-built wheel has SHA256
`bf0b1ff4230bc628f8301f86ad78fa04fd0f5734c302c9a5eeed12e59bc02cfd`
and is retained in [the CI artifact](https://github.com/tylermcm/ImageTriage/actions/runs/43246330088/artifacts/13957967622).

## Build

Built `image_triage-2.4.0-py3-none-any.whl`, SHA256:
`b7d34be179f13d18aa82442870eb0dc9d1851bb2dc9b71a9a7c403a6fc9239a3`
from source `83b4cb5`.
The wheel was installed into a separate target directory; imports were checked to
come from that installed copy. Archive integrity, worker/bootstrap files, and QML
package contents passed. The installed app loaded four synthetic JPEGs, captured a
1200×800 startup image, and exited with no QML warnings.

These are Python application sources and assets. The wheel does not contain a newly
built PhotoCraft executable, RAW helper, managed AI runtime or Windows installer.

## Companion and integration review

Both remotes fetched successfully. Fork integration remains
`b5b39157c4f837aae681658036540962d4a99fc4`; upstream main is
`7ca5a9c40f8088c949007905c533b5ff9cd1b624`. Divergence is 86 host commits / 4,044
upstream commits. The non-mutating merge assessment still reports 42 conflicted paths,
including RAW algorithms/IO, Camera Raw UI, control, startup and translations. No
broad upstream merge or binary promotion was performed. The separate v0.22
candidate was repaired on the isolated `codex/audit-svg-validation-20261112` branch;
see the focused native follow-up below.

Reviewed the checkpoint's combined RAW preview guard/watchdog, application startup and
quit wiring, native editor Save/confirmation/job polling, menus, and theme/control
changes. The Red Eye capability check still precedes the watchdog. The full source
suite covers the merged paths; this review does not replace live native acceptance.
Graphify was not installed in this environment.

## Focused native candidate and Windows packaging

The unmodified v0.22 candidate reproduces the original small-stack SVG failure on
Linux too: the exact release test aborts with SIGABRT. Commit `590a6d811` backports
only `svg.rs` and its tests from upstream `453ea8c9ca93c405b44f0a0dfb741a4b817706ab`.
Parsing, lowering/rendering and recursive tree destruction stay on one fixed-size
native worker; embedded images reuse it. XML, decoded-image and aggregate budgets
remain unchanged. The test still supplies a 1 MiB caller stack. With the backport,
the same exact release test and **642 I/O library tests pass**, with two existing
ignores (a benchmark and a user-supplied PSD). The later Red Eye commit was applied
cleanly as `bfb4a91c65cc5e2f8c4ccfe6f802664fdbbc8b85`; all five native Red Eye tests pass.
The two automatic UI merges retain upstream context and advertise the Red Eye
capability only with its native implementation present.

Strict all-target release clippy passes for I/O and algorithms. Dependency layers
(31 crates), panel-command routing, and WASM (24 crates plus codecs HEIF) pass. All
pinned corpora are fetched and verified. PngSuite's original HTTP host was blocked;
the 178 files were recovered from GitHub mirrors and each matches the unchanged
committed SHA-256 manifest, including the original README line endings. No corpus
pin, checksum or assertion was changed.

The broader algorithm release sweep initially exposed two NaN sign-bit failures:
`pixelate::tests::facet_reuse_matches_scalar_in_every_model` and
`pixelate::tests::cell_filters_match_scalar_models_edges_and_fractional_cells`.
Commit `97ceee530b1f91977cfff896d8d859c2440c329a` preserves the existing scalar
arithmetic for nonfinite samples, retaining cached processing for finite input.
The original strict bitwise assertions are unchanged. All nine pixelate checks and
**836 algorithm library tests pass**, with 26 existing ignores. Final strict clippy,
WASM and layers pass. The targeted quick performance run completes; its regression
comparison correctly skips the unrelated machine-class baseline. No baseline was
updated. Separate 1.5 MP RGB medians are 61 ms Facet, 39 ms Pointillize and 36 ms
Crystallize; these are diagnostic timings, not a before/after speedup claim.

The full I/O corpus gate passes **1,287 tests**, with 12 existing ignores across four
binaries. It uses environment-only release `LTO=false`, 16 codegen units and serial
tests. Parallel tests first hit the existing memory-admission policy for a large
CMYK spot PSD; the serial oracle then passed, but allocator retention in the long
process left the later spot check short of admission headroom. That check passes in
a fresh process. The complete serial command passes with `MALLOC_ARENA_MAX=2`,
`MALLOC_TRIM_THRESHOLD_=65536`, and `MALLOC_MMAP_THRESHOLD_=131072`, returning freed
buffers to the OS. Application budgets, approval flags, corpus hashes, the oracle
floor and assertions are unchanged. The exact stack regression and algorithm sweep
also pass separately under the normal release profile.

The final host process/QML subset also passes **28 tests with no skips** using the
actual isolated core runtime interpreter. Models remain `[]`; this is not model
inference or GPU acceptance.

Host commit `4ca3813` adds a separate immutable validation pin and a branch-only
Windows artifact build. Tag releases continue to use the unchanged production pin
and render-algorithm guard. The candidate workflow first runs native release I/O
and Red Eye regressions, builds the editor and RAW helper from the same checkout,
checks the host schema against that explicit source, freezes the app and checks
QML startup with synthetic photos. It does not create a release or install on the
owner's machine. Both complete host suites also pass at `4ca3813`:
https://github.com/tylermcm/ImageTriage/actions/runs/43249852541.
The first Windows prototype has passed native release I/O/Red Eye tests, the editor
build and the explicit host schema check; its RAW helper/packaging remain in progress:
https://github.com/tylermcm/ImageTriage/actions/runs/43249852916.

Host `531e89e` advances only the validation pin to `97ceee530`, adds the complete
native algorithm sweep, and separates the Windows native steps for diagnosis.
Current runs:

- https://github.com/tylermcm/ImageTriage/actions/runs/43257733516 (corrected MSI candidate)
- https://github.com/tylermcm/ImageTriage/actions/runs/43257733290 (host tests)

## Remaining release gates

1. Repeat the process and full UI checks with the owner's Microsoft Store Python/venv
   and actual managed culler runtime. GitHub's standard CPython is different evidence.
2. Run actual native mask generation and warm-up with installed model weights. Current
   routing/worker checks use synthetic hosts; this cloud checkout has no managed models.
3. Repeat the sealed native editor/helper lifecycle acceptance: persistent navigation,
   stash/restore, Save/grid propagation, reopen, hidden startup, failed-save confirmation,
   theme/resize/fullscreen, shutdown and cleanup. Windows hardware/native binaries from
   the original machine are not present here. Existing executable copies are untouched.
4. Complete the corrected Windows native build and hosted acceptance before promotion.
   The SVG/NaN fixes, native algorithm sweep, WASM and I/O corpus now pass locally.
5. Advance the packaging pin only to a validated compatible editor. It is still
   `0988078e4f9f8735bb99587a5f6e1d67a4b511e4`, render algorithm 2, while the host expects 3.
   Tag release builds correctly refuse this combination. Validation artifacts use
   the separate candidate pin and retain the same algorithm check.

Main was not advanced and no older editor was installed. Remaining native/packaging
gates are explicit so the source wheel is not mistaken for a release-ready installer.
