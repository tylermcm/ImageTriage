# Combined audit branch: Windows acceptance, 2026-11-13

Started at `944e786` on `claude/adoring-hopper-mm2j42`, which already contains
`d2995d7` and the previous P13/P14 promotion. This record covers the combined
incoming work plus the follow-ups in this commit. It does not certify an MSI
release or the unmerged PhotoCraft v0.23.1 update.

## Environment and automated checks

Windows, Microsoft Store Python 3.13.14 in the application's build venv,
PySide6 6.12, offscreen/Basic Qt, plugin autoload disabled, and the previously
validated managed native CUDA interpreter. GC remains enabled. Tests redirect
settings and application data to temporary directories.

The first combined run crashed when a legacy QWidget helper followed a newly
earlier QML test that had created QGuiApplication. The desktop test passed alone.
A session-owned QApplication now serves both kinds of test helper; application
code remains QML-only.

Six control-port protocol tests initially failed on Windows because their fake
editor process IDs did not own their real test sockets. Their listener fixture
now explicitly exercises the portable ownership-unavailable path. The existing
ownership tests still override that result, and a new Windows-only test checks
real TCP ownership and rejection of a foreign PID. All twelve port tests pass.
No application ownership guard or security assertion was weakened.

The completed diagnostic run reported 2,954 passed, nine failed, 60 skipped,
five expected failures and 135 passing subtests. Besides the six port fixtures,
two updater fixtures passed synthetic bytes to the real Windows signature API;
they now supply explicit simulated signature results while retaining separate
publisher-policy tests. The Safe Trash fixture compared Qt's forward-slash path
string to a Windows backslash string; it now compares Path values. The affected
checks pass independently, including 40 updater/trash tests (one platform skip),
seven update-dialog checks and 25 native-settings/control-port checks.

Final combined-suite result: **2,964 passed, 60 skipped, five expected failures,
135 passing subtests** in 1,009.91 seconds, exit code 0. This was one complete
process with GC enabled, after all source and fixture fixes. No failing case was
hidden with a new skip or expected-failure marker.

Default-suite skips include POSIX/case-sensitive paths, unavailable 8.3 aliases,
the opt-in staged PocketDrop DLL group, real ranker/model dependencies and tests
that expect the companion checkout beside this worktree. The separate native
checks below cover depth inference, CUDA computation and hosted-editor behavior;
they do not turn those skipped cases into passing tests.

## Follow-up changes

- Native settings Save validates on a cancellable worker. Newer edits stay dirty;
  shutdown discards pending results; Start/Close continue only after validation.
- Explicit CUDA runs probe the native culler's own interpreter before analysis.
  Failure is reported instead of silently selecting CPU.
- File-copy/move workers surface destination-name compatibility warnings.
- Cull review points to File > Safe Trash; the unused import is removed.

## Real Windows and native evidence

| Check | Result and limit |
|---|---|
| WinVerifyTrust | Recognized a signed Cloudflare executable and publisher, classified an unsigned DLL, rejected a byte-modified signed executable. No MSI launched. |
| Control channel | Windows identified the real socket owner; a foreign process was rejected before connection. A real child's live/exited state was distinguished. E23's residual launch race remains. |
| Native CUDA | Real tensor allocation, matrix computation and synchronization passed in the native culler interpreter. |
| Depth inference | Installed Depth Anything V2 Small produced a nonconstant 1208x804 map in about 52 seconds. |
| Light Depth render | Coverage from that map was encoded and accepted by the paired native RAW helper; rendered pixels changed. A synthetic RAW was used for the helper check, not the inference source photo. |
| Hosted editor | Hidden startup, blocked presentation hide/restore, persistent navigation, stash/restore, native Save/grid notification, closed-tab reopen, five themes, resize/fullscreen and process cleanup passed against the preserved candidate. |
| QML | Safe Trash and Light Depth captures were visually inspected; labels/controls fit and there were no reference/type warnings. Safe Trash capture used sample rows. |
| Source package | Built `image_triage-2.4.0-py3-none-any.whl`; inspected new Python modules and QML assets, including asynchronous native Save controls. This is not an MSI acceptance test. |

Local evidence is under `K:/Builds/ImageTriage/validation/audit-20261113/`:
`combined-clean.log`, `combined-clean.xml`, `combined-final.log` (diagnostic run),
`native-host.log`, `depth.log`,
`signer-probe.json`, `safe-trash.png`, `light-depth-ui.png`, and the `light-depth/`
render pair. The reused native editor/helper candidate is from the prior
`audit-20261112/candidate-baf5137` acceptance. No executable was promoted here.
The source wheel is in `wheel/`, with SHA-256
`648bf0d5f8208c110066c3264964baa67a9ccf0a9b6971cc5ff1076f08a75a55`.

## Remaining acceptance and backlog

- R6: trusted publisher pins are empty while releases remain unsigned. The API
  works, but publisher signing and a release policy must be configured before
  claiming authenticated installer updates.
- R7: uninstall custom-action source is implemented, preserving user databases
  and settings. Actual MSI install/upgrade/uninstall was not exercised here.
- E23: Windows ownership checks and bounded retries mitigate the race; removing
  it requires an editor-side launch/protocol change.
- Physical hotspot pairing and actual Linux/macOS hardware paths are not covered
  by these Windows tests. System-trash behavior is simulated in unit tests.
- PhotoCraft v0.23.1 remains unmerged: 41 large conflicts, ten in Camera Raw.
  See [the companion handoff](photocraft_handoff.md).
- Compare, sorting, Undecided filtering, XMP import, score details, crop
  rotate/flip, cross-photo settings sync, E22 full-resolution masks and P14 local
  encryption remain feature work. Architecture/cleanup recommendations remain
  separately tracked in [the audit](audit_20261111.md).
