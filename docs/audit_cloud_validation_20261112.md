# Audit cloud continuation — 2026-11-12

Continues `docs/audit_windows_handoff_20261112.md` from checkpoint `2051d1d`.
Implementation is on `codex/audit-cloud-continuation-20261112`, commits `5063516`, `2fefbd0`, `542f69a`, and `651279e`.
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

Final Windows/Linux CI: https://github.com/tylermcm/ImageTriage/actions/runs/43190879197

The full remote jobs were still running when this record was saved. Prior source
checkpoints passed their full Linux CI suites; superseded Windows runs were canceled
by branch concurrency and are not reported as completed full Windows passes.

The continuation branch runs both existing required test jobs. Windows additionally
runs the handoff subset first, builds a source wheel only after passing tests, and
retains JUnit reports and the wheel as a workflow artifact. No MSI workflow or release
tag is triggered by this branch.

## Build

Built `image_triage-2.4.0-py3-none-any.whl`, SHA256:
`37d077f60cae0ce7ecce835e71e85174f7bf304b712865fd179cedd381c0c197`.
The wheel was installed into a separate target directory; imports were checked to
come from that installed copy. Archive integrity, worker/bootstrap files, and QML
package contents passed. The installed app loaded four synthetic JPEGs, captured a
1200×800 startup image, and exited with no QML warnings.

These are Python application sources and assets. The wheel does not contain a newly
built PhotoCraft executable, RAW helper, managed AI runtime or Windows installer.

## Companion and integration review

Both remotes fetched successfully. Fork integration remains
`b5b39157c4f837aae681658036540962d4a99fc4`; upstream main is
`74330157cb601573ae4523a95bb9132822fc3918`. Divergence is 86 host commits / 3,959
upstream commits. The non-mutating merge assessment still reports 42 conflicted paths,
including RAW algorithms/IO, Camera Raw UI, control, startup and translations. No
companion merge, source edit or binary promotion was performed. The separate v0.22
candidate remains `a826e4f066b043e9e78f1063ff4685853b91c7d7`.

Reviewed the checkpoint's combined RAW preview guard/watchdog, application startup and
quit wiring, native editor Save/confirmation/job polling, menus, and theme/control
changes. The Red Eye capability check still precedes the watchdog. The full source
suite covers the merged paths; this review does not replace live native acceptance.
Graphify was not installed in this environment.

## Remaining release gates

1. Repeat the process and full UI checks with the owner's Microsoft Store Python/venv
   and actual managed culler runtime. GitHub's standard CPython is different evidence.
2. Run actual native mask generation and warm-up with installed model weights. Current
   routing/worker checks use synthetic hosts; this cloud checkout has no managed models.
3. Repeat the sealed native editor/helper lifecycle acceptance: persistent navigation,
   stash/restore, Save/grid propagation, reopen, hidden startup, failed-save confirmation,
   theme/resize/fullscreen, shutdown and cleanup. Windows hardware/native binaries from
   the original machine are not present here. Existing executable copies are untouched.
4. Resolve the companion v0.22 release SVG stack overflow before promoting that candidate;
   combine/validate its later Red Eye work deliberately.
5. Advance the packaging pin only to a validated compatible editor. It is still
   `0988078e4f9f8735bb99587a5f6e1d67a4b511e4`, render algorithm 2, while the host expects 3.
   The MSI workflow correctly refuses this combination. Do not bypass its guard.

Main was not advanced and no older editor was installed. Remaining native/packaging
gates are explicit so the source wheel is not mistaken for a release-ready installer.
