# Audit integration checkpoint — 2026-11-12

This is an unfinished cloud handoff, NOT a validated release. The user requested a quick push so cloud work can continue overnight. Do not build or promote binaries or merge into main until the outstanding validation is resolved.

## Integration state

- Host baseline: e13524d. Audit branch: claude/hopeful-meitner-t95hop at 6705296 (33 commits).
- This checkpoint completes the local merge only to publish the working state. The sole textual conflict was image_triage/qml_shell/raw_preview.py: retain our Red Eye worker capability guard and the audit watchdog, _exchange and _error_tail. The capability guard runs before the watchdog starts.
- Additional Windows fixes are included. Review automatic merges in startup, services, editor hosting, jobs, menus and themes before accepting the integration.
- QML is the only application UI. Preserve known-good binaries and unrelated companion edits.

## Evidence collected on Windows

- 62 focused face-cache/editor/Red Eye checks passed.
- Synthetic native editor and RAW lifecycle smoke passed: persistent process navigation, stash/restore, Save/grid notification, closed-tab reopen, themes, resize/fullscreen and process cleanup. Native child hide/restore for shell confirmation also passed.
- The full single-process test suite crashed around 53% during Python garbage collection. An isolated test-file sweep completed, exposing the failures below. Do not describe the suite as green.
- Latest focused run of test_cli_culler_task.py, test_mask_engine_worker.py, test_safe_trash.py and test_quality_face.py: 49 passed, 2 skipped, 4 failed. All four failures were in the culler task tests.
- No binary promotion or release build occurred. Native mask generation/warmup still needs actual managed-runtime validation; updater/startup/quit checks and packaging compatibility remain outstanding.

## Fixes included and remaining failures

### Culler shutdown — unresolved

Experimental change in cli_culler_process.py and cli_culler_task.py: create the Windows launcher suspended, attach its cleanup job, then resume its primary thread using Toolhelp/OpenThread/ResumeThread. TerminateJobObject errors are checked. This did NOT resolve cancellation; do not claim otherwise.

Four failing cases in test_cli_culler_task.py:
- malformed JSON failure leaves run_root behind;
- cancellation at metadata;
- cancellation at inference;
- worker exits while a descendant still holds stdout.

A 12-second faulthandler trace shows the reader blocked in stdout.readline, and the task blocked in stdout.close after its reader.join timeout. Descendants remain alive until their normal sleep timeout. Investigate actual process/job membership, particularly Microsoft Store Python and venv launchers. This is a hypothesis, not an established cause. The application test venv uses Store Python 3.13.14. Repeat with the managed culler Python identified by build/native-cuda-candidate/runtime.json on the Windows machine (that local runtime is not shipped in Git).

### Qt crashes and UI tests — unresolved

Isolated files with access violations: test_qml_cull_honesty, test_qml_shell_batch, test_qml_shell_filters, test_qml_shell_palette, test_qml_shell_reference, test_qml_shell_settings. Batch passed one diagnostic rerun; intermittent crashes remain. test_qml_shell_shortcut_keys also returned failure.

A disposable diagnostic invocation disabled GC solely to expose the underlying assertion; no GC suppression was added to the application or tests. It revealed:

    test_qml_cull_honesty.py:479
    assert find(window, "aiFolderData").property("visible")
    test_qml_shell_ai_center.py:357
    AttributeError: 'Section' object has no attribute 'objectName'

Investigate recursive QML traversal, Python wrapper/type lifetime, and teardown. Do not hide the problem by disabling garbage collection.

### Included targeted fixes

- FaceCache serializes read/merge/write with an in-process RLock; regression checks all eight concurrent entries survive. This does not claim cross-process locking.
- EditorHost.setPresentationBlocked hides the native editor for the QML failed-save confirmation and restores it afterward without saving; focused and native checks passed.
- Invalid mask engine names are rejected before heavy imports. Both fake-host test factories stub heavy imports, avoiding accidental real Torch initialization in routing tests.
- POSIX mount parsing uses posixpath rather than Windows path normalization.
- The test mask-worker subprocess guard remains a Popen subclass, allowing Windows asyncio to subclass Popen during import. The mask, mount and quality-face tests passed in the latest combined run.

## PhotoCraft companion state

User fork and upstream were fetched successfully. Original integration HEAD b5b39157c; upstream main 74330157c; divergence 86 host commits / 3959 upstream commits. Large conflicts remain, including Camera Raw. No new upstream merge attempted in this audit task.

Separate v0.22 integration candidate a826e4f06 on codex/photocraft-v022-integration combines 150587cb8 with release 5000240c6. It lacks the later b5b39157c Red Eye work. Promotion remains blocked by the release SVG stack-overflow test. Do not blindly replace the packaging pin (0988078e) or claim protocol compatibility; host render version is 3 versus pinned 2.

Original companion has unrelated edits in these files; preserve them:
- crates/automation/examples/nas_probe.rs
- crates/ui-egui/src/dialogs/camera_raw_detail_ui.rs
- crates/ui-egui/src/dialogs/camera_raw_ui.rs
- crates/ui-egui/src/dialogs/camera_raw_ui/host_controls.rs
- crates/ui-egui/src/dialogs/camera_raw_ui/open.rs
- crates/ui-egui/src/raw_workspace.rs

## Resume order

1. Read AGENTS.md, docs/audit_20261111.md, tests/FAILURE_LEDGER.md and this handoff.
2. Review experimental Windows process changes; resolve culler cleanup and Qt failures without weakening assertions.
3. Run focused tests, then the complete suite. Cloud/Linux results do not substitute for Windows native validation.
4. Complete actual mask/warmup, failed-save confirmation, updater/startup/single-instance, shutdown and native editor gates using synthetic fixtures. Do not install an older release on the user's machine.
5. Update audit evidence and failure ledger, refresh Graphify if available, review privacy and packaging gates before publishing to main. No release build is authorized by this checkpoint request.

Private Windows diagnostics are retained in the local audit-20261112 validation directory; they are not committed because logs contain machine paths. This document contains the actionable findings for cloud continuation.

## Cloud continuation

See [audit_cloud_validation_20261112.md](audit_cloud_validation_20261112.md) for the
implemented culler changes, native Windows CI evidence, source-wheel build, and
remaining machine/runtime and installer gates. This checkpoint's historical failures
above describe `2051d1d`; use the continuation record for current results.

## Local acceptance follow-up

See [audit_windows_acceptance_20261112.md](audit_windows_acceptance_20261112.md) for the actual managed runtime, native candidate and Qt-version findings. Historical checkpoint failures above are retained as evidence.
