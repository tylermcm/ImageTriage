# MainWindow decomposition: what was found, what was done, and how to verify it

Written 2026-10-04 for an independent reviewer (human or AI) who has **no prior knowledge of this project**. Everything
below is a claim. Section 9 says how to check each claim yourself. Section 8 lists what I could not or did not verify.
Please try to prove me wrong.

---

## Read these four things first

Before reviewing the decomposition itself, understand four limitations. They are the main qualifications on any claim that
the `MainWindow` decomposition is complete or verified.

1. **The application has not been manually tested.** Nobody has launched the app and exercised the affected workflows by
   hand. Confidence comes from automated tests, characterization (snapshot) tests and static architecture gates. A
   regression in a GUI path that no test exercises could still exist.
2. **~~The full test suite was not re-run after the final snapshot regeneration.~~ RESOLVED.** After the final golden
   updates a complete run was made on the final working tree: **1,955 passed, 2 failed, 1 skipped, 11 xfailed, 650
   subtests passed (19 min).** The 2 failures are the two known-flaky tests in `tests/test_aiculler_cli_reports.py`
   (`WinError 6` under this environment's `pythonw`), which also failed in several earlier runs and are unrelated to
   this work. Nothing else failed. (Before that run, only the five directly affected files had been re-run, 45 passed.)
3. **`MainWindow` became much smaller, but coupling increased substantially.** It went from 19,133 lines / 976 methods to
   2,830 lines / 109 methods, but private accesses from other modules rose from 1,381 to 3,604 and distinct private names
   touched rose from 391 to 926. Code moved out of the god object; shared state and the dependency structure did not.
   This is a major structural *decomposition*, not yet an architectural *decoupling*. "`MainWindow` is fixed" would be an
   overstatement; "`MainWindow` is substantially decomposed, with state ownership and coupling as the next phase" is what
   the numbers support.
4. **The extraction tooling is not reproducible from the repository.** The scripts that performed the mechanical
   extraction live in the author's session scratchpad, not in the repo. A reviewer can inspect the diff, the tests and the
   gates, but cannot re-run the extraction from the checked-out project.

## 1. The project and the problem

**Image Triage** is a Windows desktop application for photographers ("culling": going through hundreds of photos and
marking winners and rejects). It is written in **Python 3.13 with PySide6 (Qt 6)**. Repository root:
`C:\Users\tylle\OneDrive\Documents\Playground`, branch `codex/ui-ux-polish`. The application package is `image_triage/`;
tests are in `tests/` (pytest).

The application has one top-level window class, `MainWindow`, in `image_triage/window.py`. An earlier engineering audit
found it was a **"god object"**: one class that owned almost everything (UI building, folder scanning, annotations,
AI runs, settings, preview, export, updates...). At the start of this work it was **19,133 lines, 976 methods and 526
instance attributes**. A first remediation had marked this problem "closed", but it had only moved code into helper
classes that still reached back into the window for all of their state, so the problem was falsely closed. The user then
asked for a dedicated plan (`docs/mainwindow_decomposition_plan.md`) and for the window to actually shrink.

**Instruction from the user that shaped the work:** back-references from helper classes to the window
(`self._window.something`) are acceptable. What is not acceptable is `MainWindow` still containing the code. So the
priority was to move whole clusters of methods out, not to purify coupling.

**Other standing constraints from the user:** the user makes all git commits (nothing is committed by me); I must not
delete things destructively; behaviour must not change.

## 2. Vocabulary used below

- **Controller**: a new class (a `QObject` subclass) in its own module, e.g. `image_triage/scan_controller.py`
  (`ScanController`). It holds methods that used to live on `MainWindow`. Its constructor takes the window and stores
  it as `self._window`. It is created with the window as its Qt parent, so it lives in the GUI thread and Qt callbacks
  into it behave as they did when they were window methods.
- **Handle**: the attribute on the window that holds a controller, e.g. `self._scan = ScanController(self)`. Callers
  write `window._scan.refresh_folder()`. Handles in use: `_ai_setup, _ai_run, _aiculler, _toolbar, _appearance,
  _tool_mode, _zen` (these seven were already committed), and, new and uncommitted: `_export_jobs, _help_update,
  _preview_ctl, _annotation_ctl, _navigation, _settings_ctl, _scan, _inspector, _context_menus, _display, _startup,
  _handoff, _views, _projects, _dragdrop`. (`_annotation_ctl` and `_settings_ctl` have those names because
  `MainWindow._annotations` is the annotation dictionary and `MainWindow._settings` is the `QSettings`.)
- **Public rename on move**: a moved method loses its leading underscore (`_refresh_folder` becomes `refresh_folder`).
- **Forwarding delegate**: a one-line method left on `MainWindow` that just calls a controller
  (`def _x(self): return self._scan.x()`), kept so old callers keep working.
- **Ratchet**: `docs/architecture_ratchet.json` records numbers (lines, methods, delegates, per-module counts). A script
  and a test fail if a number gets worse. Numbers may only fall, unless growth is explicitly accepted on the command line.
- **Characterization / snapshot test**: a test that pins the exact observable output of a builder (e.g. the dump of
  every QAction and what its slot calls) as a SHA-256 plus length, so refactors cannot silently change behaviour. The
  pinned table is called the **golden**.

## 3. Baseline and current state

Git: `HEAD` is commit `77bde1f` ("MainWindow: 10,412 lines, down from 19,133"). The user committed that after I had
extracted seven controllers. **Everything after that point is uncommitted** in the working tree:
49 modified files (`git diff HEAD --shortstat`: 1,293 insertions, 8,810 deletions) and 17 untracked files (15 new
controller modules, `image_triage/ui/project_rows.py`, and one stray file, see section 8).

| Metric (from `docs/architecture_ratchet.json`) | Start of whole effort | `HEAD` | Now |
|---|---|---|---|
| `MainWindow` class lines | 19,133 | 10,412 | **2,830** |
| `MainWindow` methods | 976 | 568 | **109** |
| `MainWindow` attributes | 526 | 386 | **342** (see the metric note below) |
| Other classes in `window.py` | 38 | 0 | 0 |
| Forwarding delegates | 129 | 58 | **1** |
| Private accesses (window privates touched from other modules) | 1,381 | 1,969 | **3,604** |
| Distinct private names touched | 391 | 602 | **926** |

`window.py` the file: 10,864 lines at `HEAD`, 3,103 now (`git show HEAD:image_triage/window.py | wc -l`).

**The last two rows went UP, on purpose, and that is not good news in itself.** Code that used to say `self.x` inside the
window now says `self._window.x` inside a controller, so coupling that was hidden inside one class is now visible and
counted. Reducing it means moving the shared state itself into owners; that has not been done (section 8).

**Metric note:** the attribute count now excludes controller handles (`self._x = SomeController(self)`). I changed the
metric (`_state_attributes` in `scripts/architecture_report.py`) because every extraction needs one handle, so counting
it would punish the intended change. The "386" at `HEAD` still counted handles, so 386 and 342 are not the same
measurement. Judge that decision yourself.

What is left in the 2,830 lines: about 1,170 lines of construction phases (`_init_*` methods that build and wire
widgets), about 520 lines of class-level constants, about 850 lines of methods (Qt event overrides `nativeEvent`,
`eventFilter`, `closeEvent`, `showEvent`, `resizeEvent`, shared selection helpers, dialog-geometry helpers, static
normalizers), and the remainder is decorators, blank lines and the class docstring.

## 4. What was done, in order

### 4.1 Verified the earlier batch
The previous session's four "chrome" controllers (toolbar, appearance, tool_mode, zen) had no complete full-suite run.
I ran the full suite: **1,957 passed, 1 skipped, 11 xfailed, no failures.**

### 4.2 Extracted 15 more controllers (all new since `HEAD`)

Each row: module, class, handle, approximate methods moved, approximate lines moved.

| Module | Class | Handle | What it owns | Methods / lines |
|---|---|---|---|---|
| `export_jobs_controller.py` | `ExportJobsController` | `_export_jobs` | resize, convert, workflow-export and archive dialogs, tasks, progress dialogs, saved-recipe runs | 55 / ~820 |
| `help_update_controller.py` | `HelpUpdateController` | `_help_update` | help pages, About, the app updater (check, prompt, download, progress) | 25 / ~430 |
| `preview_controller.py` | `PreviewController` | `_preview_ctl` | full-screen preview: build/open, navigation, filmstrip, preload, requests from it, winner ladder | 45 / ~750 |
| `annotation_controller.py` | `AnnotationController` | `_annotation_ctl` | winners/rejects/tags, batch marks, persistence and winner sync, pairwise feedback, review counts | 38 / ~780 |
| `navigation_controller.py` | `NavigationController` | `_navigation` | folder tree, drives, favorites, recents, path bar, parent/child moves, folder context menus | 35 / ~520 |
| `settings_controller.py` | `SettingsController` | `_settings_ctl` | settings dialog, persisted lists, window/pane geometry, shortcut overrides, workspace presets | 54 / ~910 |
| `scan_controller.py` | `ScanController` | `_scan` | loading/refreshing a folder, folder watching, scan results, scope enrichment, review-intelligence, cache status | 49 / ~680 |
| `inspector_controller.py` | `InspectorController` | `_inspector` | inspector context, thumbnails, face/category previews, status line, per-record insight lookups, action enablement | 26 / ~600 |
| `context_menu_controller.py` | `ContextMenuController` | `_context_menus` | the grid's right-click menus | 5 / ~360 |
| `display_controller.py` | `DisplayController` | `_display` | window frame and display: maximize, native frame styling, display class/policy, screen changes, app-bar alignment | 15 / ~200 |
| `startup_controller.py` | `StartupController` | `_startup` | start folder, launch target, quick view, startup fixes, restart-for-development | 8 / ~170 |
| `handoff_controller.py` | `HandoffController` | `_handoff` | handoff and best-of-set builders, PocketDrop send, send-to-editor, AI workflow center, semantic-folder sort | 12 / ~230 |
| `view_controller.py` | `ViewController` | `_views` | sort mode, columns/zoom, grid/details view modes, details density, view toggles | 24 / ~270 |
| `projects_controller.py` | `ProjectsController` | `_projects` | sidebar projects/collections/face groups, search and path controls | 22 / ~250 |
| `dragdrop_controller.py` | `DragDropController` | `_dragdrop` | drop targets and copy-vs-move for dropping photos on folders and favorites | 5 / ~60 |

Also created: `image_triage/ui/project_rows.py` (three row-size constants relocated out of `window.py` so the moved code
could import them).

### 4.3 Retired 41 of the 42 forwarding delegates
Once the controllers existed, almost every remaining delegate was a one-hop forwarder. A script rewrote their **113
references** (action slot bindings in `image_triage/ui/actions.py`, Qt signal connections, command-palette callbacks,
`patch.object`/`monkeypatch.setattr` calls in tests) to point at the controller method, then deleted 41 delegates.
Forwarding delegates went from 42 to 1. The one left, `_is_slow_source_folder` (19 call sites, many test stubs), is
a pure path-policy predicate and should move into `image_triage/path_policy.py`; I left it because moving it
forces edits to a dozen test stubs.

### 4.4 Tooling and gates added in the repo (these ARE in the repository)
- `scripts/architecture_report.py` gained **`check_controller_calls`**: for every `<window>.<handle>.<name>` in the
  package and tests, fail unless the controller behind that handle defines `<name>`. I mutation-checked it (re-pointed
  one call at the wrong controller and confirmed it fails; restored the file byte-for-byte).
- The same script's attribute metric now excludes controller handles (see section 3).
- `tests/harness.py` gained **`controller_over(ControllerClass, stand_in_window, "handle", **overrides)`**: puts a real
  controller over a fake window, so tests that used to call `MainWindow._method(stub)` can call the controller.
- `tests/conftest.py` gained a session-wide autouse fixture, **`_no_startup_update_check`**, that turns
  `HelpUpdateController.check_for_updates_on_startup` into a no-op. Reason: a freshly built window schedules an update
  check 2.5 s later, which in a test session is a real network request and toggled the "Check for updates" action
  while a snapshot test was reading action state, causing an intermittent failure.
- `tests/test_actions_snapshot.py` has a map `_controllers_by_window_attribute()` of handle to controller class; every new
  handle was added.
- Docs: `docs/mainwindow_decomposition_plan.md` (progress table, per-slice notes) and the ratchet file updated.

### 4.5 Tooling that is NOT in the repository (important for a reviewer)
The extraction itself was done by scripts kept in my session scratchpad, **not committed**:
`...\scratchpad\post_audit\extract_slice.py` and helpers (`gen_spec.py`, `rehome.py`, `drop_delegates.py`,
`retarget_delegates.py`, `verify_rename.py`, `splice_golden.py`, `add_ctl_map.py`, `tests_for.py`). A reviewer cannot
inspect them from the repo. What `extract_slice.py` does, given a list of method names:
1. Moves the methods **verbatim** from `MainWindow` into a new `QObject` controller class.
2. Rewrites `self.X` to `self._window.X` unless `X` also moved, is an attribute only the moved code uses
   ("exclusive": moved onto the controller), or is created only by the moved code ("owned": other modules' reads are
   rewritten to `window.<handle>.X`).
3. Pulls in extra methods by call-closure when only moved code calls them.
4. Retargets callers: plain calls and GUI-thread signal connections go straight to the controller; callbacks wired to
   worker-thread signals keep a thin delegate on the window (later retired in 4.3 once each was safe to retarget).
5. Leaves `@staticmethod`/`@classmethod` methods where they are.
6. Works out the controller's imports with Python's `symtable` (scope analysis) rather than text search.
It must run under Python 3.12+ because Python 3.9's `ast` reports wrong column positions inside f-strings (this
corrupted an early attempt; see section 6).

## 5. How I checked it (evidence)

- **Gates now:** `python scripts/check_undefined_names.py` reports 0 undefined names in 492 files.
  `python scripts/architecture_report.py --check` passes; every metric equals its recorded value.
- **Full suite:** the last complete run before the final golden regeneration: **1,954 passed, 1 skipped, 11 xfailed,
  650 subtests passed, 3 failed**. The 3 failures were two snapshot tests that were expected to differ (renamed slot
  names) plus one known order-dependent test (`test_char_batch_rename_controller.py::test_apply_preview_renames_files_rekeys_records_and_pushes_undo`,
  passes when run alone). The two aiculler CLI tests (`tests/test_aiculler_cli_reports.py`), which fail intermittently
  with `WinError 6` in this environment, passed in that run but failed in earlier ones.
- **After that run** I regenerated the two goldens, registered the ratchet, and ran the affected files
  (`test_actions_snapshot`, `test_palette_commands_snapshot`, `test_menu_tree_snapshot`, `test_architecture_rules`,
  `test_char_batch_rename_controller`): 45 passed. I then ran the **whole suite on the final state: 1,955 passed, 2
  failed, 1 skipped, 11 xfailed, 650 subtests passed.** The 2 failures are the known-flaky aiculler CLI tests; the
  order-dependent batch-rename test passed in that run.
- **Snapshot changes were checked as rename-only, not just accepted.** Procedure: take the new dump of the actions and
  the palette, map the new names back to the old ones, and compare SHA-256 and length to the old golden.
  - Palette: 12 of 12 cases matched exactly.
  - Menu tree: 13 of 13 matched (it did not change).
  - Actions snapshot, final round: the changed names were exactly 16 slot names (for example `_browse_catalog` became
    `_catalog.browse_catalog`); with the exact per-name mapping the length (89,716) and digest matched the old golden
    byte for byte. (An earlier, looser mapping wrongly showed a mismatch; I re-did it precisely before trusting it. I had
    regenerated that golden slightly before finishing this check, then confirmed it afterwards.)
  The test files that hold the goldens are `tests/test_actions_snapshot.py` and `tests/test_palette_commands_snapshot.py`.
- **Per-slice discipline:** before each extraction I copied `image_triage`, `tests`, `scripts`, `docs` into a scratch
  backup; each slice was dry-run first, applied, then gates + targeted tests + (every one or two slices) the full suite.

## 6. Problems found and how they were handled (including my mistakes)

1. **Python 3.9 `ast` bug** corrupted an early edit (wrong f-string column positions). Fix: the tool refuses to run
   below 3.12; restored from backup (checksums matched).
2. **Handle name collisions.** My first name for the annotation controller, `_annotations`, collided with the existing
   dictionary of the same name, and the controller would have been silently overwritten at init. Caught by reading the
   code, renamed to `_annotation_ctl`; `_settings` likewise became `_settings_ctl`.
3. **Tests silently stop patching moved code.** Several tests patch `image_triage.window.QMenu`,
   `...FolderModifiedCheckTask`, the prefilter loader, etc. After the move the moved code uses its own import in the
   controller module, so the real function ran. One of these made a test open a real blocking `QMenu` and the test
   process crashed with an access violation. All such patches were audited (script: every `window_module`/
   `image_triage.window.X` patch checked against controller modules) and retargeted.
4. **Dangling references after deleting forwarders.** I deleted three forwarding methods inside `ScanController`, but
   code in `window.py` still called them through `self._scan`; about 20 tests (including the action sweep) failed with
   `AttributeError`. This led to the `check_controller_calls` gate (4.4) so it cannot recur silently.
5. **Extraction tool bug.** When asked to move two top-level helper functions at once, the tool deleted them top-down
   with stale line numbers and **cut a chunk out of `window.py`** (it no longer parsed). The tool writes before it
   self-checks. I fixed the tool (delete bottom-up) and repaired `window.py` by restoring the exact missing region from
   the pre-batch backup, then completed the tool's final two steps (constructing the controller, importing it) by hand.
   Result checked by: file parses, 0 undefined names, ratchet unchanged, full suite green. A reviewer should read
   `window.py` around the class header and the imports with this in mind.
6. **Unused imports.** After many moves `window.py` had 133 unused imports. I removed 131 after checking, with the AST,
   that no test or module imports them from `image_triage.window` (a first regex-based check missed multi-line
   `from image_triage.window import (...)` blocks and broke test collection once; fixed by moving
   `AIReviewCompleteDialog`'s import in one test to its real module).
7. **A source-text test** (`test_settings_dialog.py::test_every_settings_result_field_has_a_consumer_in_window_py`)
   scans `window.py` for `result.<field>` reads; the settings handler moved, so I made it scan `settings_controller.py`
   too.
8. **Flaky test caused by a real network call** (the startup update check, 4.4).
9. **Two full-suite runs died with a Windows access violation** (once in a folder-context-menu test at 65%, once in an
   unrelated picker test at 28%). The first is explained by item 3. The second I could not reproduce in two later full
   runs; my hypothesis (the real update check thread during test teardown) is unproven, and it now cannot occur
   because the update check is disabled in tests.
10. **A process mistake:** I once regenerated a snapshot golden before finishing the rename-only proof (section 5).
    It turned out to be correct, but the order was wrong.

## 7. Design decisions a reviewer may want to challenge

- Controllers keep a back-reference to the window and reach into its private attributes: **3,604 private accesses
  across 34 modules** (per-module counts are recorded in the ratchet file). The largest are `records_view_controller`
  584 (that module pre-dates this work), `ai_run_controller` 507, `settings_controller` 343, `scan_controller` 315,
  `annotation_controller` 191, `preview_controller` 166, `inspector_controller` 144, `view_controller` 80.
  This is what the user accepted, and it is what a reviewer should call out as the remaining real problem.
- Thread safety rests on one claim: because each controller is a `QObject` whose parent is the window, a Qt signal from
  a worker thread connected to a controller method (with `Qt.ConnectionType.QueuedConnection`) is delivered in the GUI
  thread, as before. This has not been tested with a thread-affinity assertion; it is inferred from Qt semantics and
  from the app and test suite continuing to work.
- The ratchet allows controller access counts to **grow** only with an explicit `--accept-growth MODULE`. I used it for
  `ai_run_controller`, `aiculler_controller` and `appearance_controller` (retargeting calls to new handles added
  accesses), and registered each new module at its starting count.
- Class constants (about 520 lines) and the init phases (about 1,170 lines) were deliberately not touched. Moving the
  constants needs a mixin or a metrics object because they are referenced as `MainWindow.X` and `self.X` everywhere.

## 8. What is NOT verified, and open items (read this section first if you are short of time)

- **Nobody has run the app by hand.** All evidence is automated tests, snapshot tests and static gates. A
  behaviour regression in a path no test exercises (for example the grid right-click menus, the drag-and-drop paths,
  the updater UI, PocketDrop send) would not have been caught. `ContextMenuController` and `DragDropController` in
  particular have thin direct tests.
- ~~The full suite was not re-run after the last golden regeneration.~~ It has now been: 1,955 passed, 2 failed (the
  two known-flaky aiculler CLI tests); see caveat 2 at the top.
- **Two full-suite runs crashed with access violations** (item 9 above) and one cause is a hypothesis.
- **Known flaky tests, unchanged by this work:** two in `tests/test_aiculler_cli_reports.py` (`WinError 6` under this
  environment's `pythonw`) and one order-dependent test in `test_char_batch_rename_controller.py`.
- **Unused imports are creeping back:** `python scripts/reachability_report.py` now lists **27 unused imports** in
  `window.py` created by the last few slices (the earlier cleanup took it to 2). Not gated in CI. Harmless but untidy.
- **A stray file I created by accident:** `spec_export_jobs.json` in the repository root (untracked), written by one
  of my helper scripts running in the wrong directory. It is not part of the work and should be deleted by the user
  (I do not delete files myself in this project).
- **The extraction tooling is outside the repo** (section 4.5), so the mechanical correctness of each move can only be
  judged from the diff, the gates and the tests, not by re-running the tool.
- **Metric definition changed** (section 3), so the 386 to 342 attribute drop is partly definitional.
- **Coupling got worse by the ratchet's own numbers** (section 3). Calling the god object "gone" would be wrong:
  the code left the window; the dependencies did not.
- Everything is uncommitted.

## Addendum: two real bugs found while testing, and their fixes (after the review above)

The author tested the running app after the refactor and found two bugs. Both were investigated against old commits to
tell "caused by the refactor" from "already there". **Neither was caused by the refactor**; both were pre-existing.

**1. The popout's filmstrip let you scroll out of the folder.** A folder's record list also holds its subfolders as
entries (sorted first). The popout's filmstrip and next/previous walked that list, so stepping back from the first photo
landed on a subfolder entry and `open_preview` entered that folder. Reproduced on the pre-refactor commit `89c6cd9` and on
`HEAD`. Fix (`image_triage/preview_controller.py`): the popout browses photos only (`browsable_indexes`, positions
translated to record indexes); a request to open a folder while the popout is showing is ignored. Tests:
`tests/test_preview_filmstrip_folders.py` (5; two fail with the fix removed).

**2. With a mapped network drive offline, the Drives list and Folders tree were empty and the app froze.** The machine had
`P:` mapped to `\192.168.1.219\dixie`, which was off. Measured with plain Qt, no app code: any query to `P:\` (existence,
size, label) takes ~21 s per fresh probe before failing. The Drives list and the Folders tree shared one `QFileSystemModel`
rooted at "all drives", which asks about every drive on its single worker thread, so the Drives list stayed empty and
every folder listing waited ~20 s; the drive list's usage bar also called `QStorageInfo` on the GUI thread. The moved
navigation code diffed identical to `HEAD`, so this is not a regression from the move. Fix:
- `image_triage/drive_list_model.py` (new): `DriveListModel`, the Drives list's own model. Rows come from drive letters and
  `GetDriveTypeW` (`path_policy.drive_roots()`, which never touches a drive). **No drive is asked anything on the GUI
  thread**, not even a local one (an external disk that has spun down is slow too): every drive is checked on a daemon
  thread; local fixed drives show as ready immediately, others as "checking"; a drive that does not answer becomes a
  dimmed, unclickable "offline" row and fills in if it answers later. Stale answers from an older refresh are ignored.
- `NavigationController` (`navigation_controller.py`): the folder model is never rooted at the empty path
  (`root_tree_at(drive_root)` roots it at one drive); the tree shows an empty placeholder model until a drive is rooted;
  `refresh_folder_tree` never attaches an unrooted model; `sync_drive_roots` picks up a drive letter that appears or goes
  away (called when the app is re-activated). The drive model is owned by this controller (`_navigation.drive_model`).
- `image_triage/ui/prototype_style.py`: the usage bar asks the model, never `QStorageInfo` for a non-local drive.
- Tests: `tests/test_drive_list_model.py` (10, using a simulated dead drive; two mutations checked: rooting at "" again
  and probing synchronously each make a test fail).
- Measured on the author's machine with `P:` really offline: worst single GUI stall 161 ms (was ~20 s), all drives
  listed, `P:` shown as "Network Drive (P:) - offline", `C:` tree listed within one event-loop turn (worst 70 ms).
- Honest limit: I could not reproduce the author's exact first-launch experience; the same 21 s stall did not recur on a
  second run (Windows caches the failure for a while), so the pure-Qt timings above are the evidence, plus the mutation
  tests. Also, the earlier claim in the repo's history that "the GUI thread never waits on a share" (WI-8.5) was too
  strong: this path was missed.

**Follow-up: the first version of this fix made the app hang at its splash screen, and that was my mistake.** The author
relaunched and the app never got past the splash. The app's own stall tracer (`LocalLow\ImageTriage\logs\ui_stall_traces.txt`)
showed the main thread stuck. Two defects, both in the new drive code, both only visible with the failed drive's error
*uncached* (Windows remembers a failed network query for a minute or two, so back-to-back probes looked fine):
1. The probe used `QStorageInfo`. A thread blocked in it for the dead drive held Python's interpreter lock, freezing every
   thread. Replaced with `ctypes` `GetVolumeInformationW` and `shutil.disk_usage` (both release the lock while waiting;
   measured with a cold cache: another thread starts in 0 ms while the probe is blocked 21 s).
2. `_index_is_drive` called `QFileInfo(path).isRoot()` on the GUI thread. Despite the name it queries the file system, so
   sizing the `P:` row blocked the window ~21 s. The drive model now answers `is_drive()` itself and offers no `fileInfo()`.
Verified by building the real `MainWindow` with a cold cache and `P:` really offline: **1.4 s** (22.2 s before the second
fix), worst GUI stall 72 ms, `P:` settles to "offline" in the background after ~21 s. New regression tests: the probe
never uses `QStorageInfo`; `storage_numbers` on a real and a missing drive; painting a drive list with a dead drive never
reaches `QFileInfo`/`QStorageInfo` (mutation-checked: restoring the old `_index_is_drive` fails it). One of my own new tests
resized the shared test window and made two later top-bar tests fail; rewritten to use its own view. Lesson recorded: a
timing fix for "a dead network drive" cannot be validated with a warm cache; wait for the cache to go cold, or the fix
only appears to work.

**Later changes to the Drives list (author's feedback after trying it):** the dimmed "offline" and "no media" rows were
clutter, so `DriveListModel` now lists **only drives that are ready**. An offline share, an empty card reader, or a drive
still being asked is not in the list; a drive that was ready stays listed while it is re-checked; a drive that becomes
ready appears by itself (rows are inserted in drive-letter order). The model keeps every drive's state internally
(`state_of(path)`) so it can be re-asked, but `rowCount`/`index_for_path` cover listed drives only. The per-row hover
tooltip was removed. On the author's machine the list is now exactly the 8 ready drives (C, G, I, K, L, X, Y, Z). Tests in
`tests/test_drive_list_model.py` were updated accordingly (14 tests; two mutations from before still fail the suite).
Statements above that mention a "dimmed offline row" describe the intermediate design.

**Current numbers (supersede section 3):** `MainWindow` 2,829 lines / 109 methods / 342 attributes / 1 delegate; private
accesses 3,599; distinct private names 927. **Full suite on the final working tree: 1,976 passed, 2 failed, 1 skipped, 11
xfailed, 650 subtests passed** (run before the tooltip removal; the drive, topbar and share test files were re-run after it: 73 passed). The 2 failures are the same two known-flaky aiculler CLI tests.

## Appendix A. Files touched

New modules (untracked): the 15 controllers in section 4.2 and `image_triage/ui/project_rows.py`.
Modified production files include `image_triage/window.py`, `image_triage/ui/actions.py`,
`image_triage/ai_workflow_center.py` (its string-based `_invoke("...")` resolver already accepted dotted names like
`"_handoff.open_current_ai_review"`), `image_triage/appearance_controller.py`, `toolbar_controller.py`,
`records_view_controller.py`, `ai_run_controller.py`, `aiculler_controller.py`, `command_palette_controller.py`,
`catalog_controller.py`, `record_ops_controller.py`, `recycle_bin_controller.py`, `folder_ops_controller.py`,
`batch_rename_controller.py`, and `scripts/architecture_report.py`. About 30 test files were adapted (mostly: calls that
went through the window now go through a controller; stubs gained the handles the moved code now uses;
`patch` targets were moved to the controller modules).

## 9. How to verify (commands, run from the repository root)

Environment note: on this machine plain `python` is 3.9 and cannot run the app (it crashes Qt). The app and tests run
with the Windows Store Python 3.13 through a wrapper that captures output and prints `__EXIT__=<code>` at the end:

```
"C:/Users/tylle/AppData/Local/Microsoft/WindowsApps/pythonw3.13.exe" scripts/run313.py <logfile> pytest -p no:cacheprovider tests -v
```

Then read `<logfile>`. Gates run fine on 3.9:

```
python scripts/check_undefined_names.py          # expect: 0 undefined name(s) in 492 files
python scripts/architecture_report.py --check   # expect: all metrics equal "recorded", no FAIL lines
python scripts/reachability_report.py            # expect: orphan_modules 0; unused_imports 27 (see section 8)
git diff HEAD --shortstat                        # expect: 49 files, +1293 / -8810
git status --short                               # expect: 17 untracked, incl. the 15 controllers
```

Independent checks worth doing, in rough order of value:

1. **Behaviour preservation by diff.** For a few controllers, compare the moved method bodies against
   `git show HEAD:image_triage/window.py`. They should be identical except for `self.` becoming `self._window.` (or
   `self.` for state that moved with them), underscores stripped from public names, and `window.<x>` receivers in
   callers. Pick methods with threads, timers and Qt signals (`ScanController.start_scope_enrichment_task`,
   `AnnotationController.queue_annotation_persist`, `PreviewController.open_preview`).
2. **Dangling references.** Grep the package and tests for `\._[a-z_]+\.[a-z_]+\(` through every handle, or just read
   `check_controller_calls` and confirm it cannot be fooled (for example by names built from strings, `getattr`
   with a string, or an attribute lookup on a controller that exists only on `MainWindow`).
3. **String-based lookups.** Search for `getattr(` with a string name on a window or a controller, and for
   `_invoke("` in `image_triage/ai_workflow_center.py`; these are invisible to the gates. A handful were retargeted by
   hand (for example `getattr(self._window._toolbar, "_nav_back", None)`, and `_ai_workflow_center_dialog` now read
   through `_handoff`).
4. **Worker-thread signal connections.** Search for `task.signals.` connections in `scan_controller.py`,
   `records_view_controller.py`, `export_jobs_controller.py`, `help_update_controller.py`, `ai_run_controller.py`
   and confirm each receiver is a controller method (QObject, GUI thread) and is connected with the connection
   type it had before.
5. **Init order.** `tests/test_main_window_init_phases.py` pins the construction order; each controller is created
   right after `self._folder_session = FolderSession(self)` in `_init_window_frame_and_launch_state`. Check no
   controller's `__init__` touches a window attribute that does not exist yet.
6. **Snapshot honesty.** Re-run section 5's rename-only check for the actions snapshot: take the dump
   (`%TEMP%\image_triage_test_actions_snapshot_actual\fields_slots.txt` is written only when the golden mismatches),
   map the 16 renamed slot names back, and confirm digest and length match the golden at `HEAD`
   (`git show HEAD:tests/test_actions_snapshot.py`, the `fields+slots` entry).
7. **Run the app and click.** Open a folder, mark winners/rejects, open the preview and the winner ladder, use the
   grid right-click menu on a photo and on empty space, drag photos onto a folder in the tree, open Settings, change a
   setting, resize and maximize the window, run a resize/convert/archive job, and "Check for Updates". None of this was
   done by hand.
