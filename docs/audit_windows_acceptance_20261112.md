# Windows audit acceptance — 2026-11-12

Continues the checkpoint and cloud continuation at `baf5137`. The audit worktree was
fast-forwarded from `2051d1d`; unrelated host and companion worktrees were preserved.

## Runtime finding and fix

The existing application venv uses Microsoft Store Python 3.13.14 and PySide6 6.11.0.
Its full suite reproduced an access violation during garbage collection around the
QML tests. `test_clear_cancel_and_delete_ask_first` also reproduced it alone.
With PySide6/Shiboken 6.12.0 installed in a separate validation directory, the same
25 honesty tests plus the lifetime regression pass (26 total), with GC enabled.
The application dependency now requires PySide6 >=6.12. The existing venv was not
modified. This is a version-dependent reproduction, not a claim to have located
the underlying Qt memory defect.

The Store-Python application process controlling the actual managed CUDA culler
passes all 33 process/QML checks, including descendant cleanup, partial protocol
lines, real generation activation and reopen. This is the intended deployed setup.
Using the Store venv itself as the *worker* instead still fails two cancellation
checks: child launches through its app-execution alias leave the named cleanup job
and hold the staging directory. A direct native-image launch retained membership
in a diagnostic probe. No unsafe process enumeration/termination workaround or
weakened assertion was added. Direct Store-Python worker support remains open;
select the managed runtime for worker acceptance. Cancellation assertions now
include the failure diagnostics.

## Paired candidate

The latest Windows build and both CI suites pass at `baf5137`:
- https://github.com/tylermcm/ImageTriage/actions/runs/43275780716
- https://github.com/tylermcm/ImageTriage/actions/runs/43275780735

The downloaded artifact SHA256 matches GitHub's digest:
`4a8b1bca53e4ad9d4fcdd9217a5e63efc7565283c49a366709330a3ff4371cc5`.
The MSI was administratively extracted into a separate validation directory, not
installed. Its paired editor/helper are built from `97ceee530b1f91977cfff896d8d859c2440c329a`,
with render algorithm 3. The production packaging pin is advanced to that validated
fork commit; existing installed binaries are not replaced.

Local acceptance against that extracted candidate:
- Frozen QML startup exits successfully and renders a visually inspected 1200x800
  screenshot with two synthetic photos.
- Native hosted lifecycle passes independently with PNG and synthetic DNG files:
  hidden startup, persistent process navigation, stash/restore, native Save and
  grid notification, closed-tab reopen, five themes, resize/fullscreen and cleanup.
  Original input hashes remain unchanged.
- Injected stash failure with a live editor preserves the editing state, allows
  hiding the native child for the confirmation, returns to grid on discard, and
  reopens another photo in the same process. Original input hashes remain unchanged.
- The paired RAW helper corrects the selected synthetic red pupil at both 512 and
  256 pixel preview sizes, leaving the other eye and background unchanged.
- Installed OneFormer, BiRefNet and SAM models warm on CUDA (approximately 61.6,
  2.7 and 1.1 seconds respectively; the first includes runtime startup/imports).
  BiRefNet produces a subject mask. SAM object and scene inference return regions;
  the people pass correctly returns none for the fixture. Worker shutdown passes.

Private logs, scripts, hashes and images remain in the local audit validation
folder; machine-specific paths are deliberately omitted here.

## Companion assessment

Both remotes fetched successfully after restoring the shell's Windows environment.
The fork integration remains `b5b39157c`; upstream moved from `74330157c` to
`134208d7fddac75b79a7ae2e1b0d255b66d5608d` (113 additional commits). Divergence is
86 host-only / 4,072 upstream-only commits. The fork integration ref matches HEAD.
The dry merge still reports 42 conflicted paths across RAW algorithms/IO, Camera
Raw UI, desktop startup/control, jobs, translations and protocol documentation.
These are large conflicts. No broad upstream merge or edits to the six companion
drafts were made. The separately validated v0.22 candidate includes the SVG repair,
Red Eye implementation and scalar pixelate repair recorded in the cloud handoff.

## Final local gate

The complete suite on Store Python 3.13.14, isolated PySide6/Shiboken 6.12.0,
Basic/offscreen Qt, and the actual managed CUDA worker passed: **2,634 passed,
18 skipped, 5 expected failures, 129 passing subtests**, in 881.17 seconds.
Garbage collection stayed enabled. Packaging/workflow checks separately passed
16 tests after the pin/dependency changes. Graphify refreshed to 13,502 nodes
and 32,032 edges. All extracted candidate processes were confirmed stopped.

## Scope and remaining limits

This completes acceptance of the audit source changes for the managed worker
configuration covered by the full local gate above. The audit's explicitly deferred
feature projects remain deferred. Direct Store-Python *worker* support, destructive
installer uninstall/upgrade acceptance, and signer validation are not established by
these tests. No release tag, installer deployment, or unrelated upstream merge is
part of this acceptance.
