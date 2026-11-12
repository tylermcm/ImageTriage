# Test failure ledger

Last checked 2026-11-12 on Windows (Store Python 3.13.14, PySide6 6.12, managed CUDA worker, offscreen/Basic Qt): **2,634 passed, 18 skipped, 5 expected failures, 129 passing subtests**. GC enabled. Earlier platform results are retained below.

Rules:

- A test that fails because the application is wrong stays failing and is listed under
  **Open failures** with its cause. It is not skipped, `xfail`ed or deleted to make the run
  green; it leaves this list when the code is fixed.
- A test whose expectation was retired on purpose is a **strict** `xfail` (it fails the run
  if it starts passing) and is listed under **Expected failures**. `pytest tests -rx` lists them.
- A test that cannot run where it is (Windows-only behaviour, a missing optional dependency)
  skips with a reason that says what it needs. `pytest tests -rs` lists them.
- Update this file whenever a row is added or removed.

The cloud continuation's full Linux suite passed: **2,591 passed, 49 skipped, 5 expected
failures**, plus 117 passing subtests. The host-bootstrap compatibility follow-up passed
44 focused cases (3 platform/optional-runtime skips), then 25 task/QML cases with the
actual isolated core engine interpreter (no skips). Standard-CPython Windows handoff
checks and both complete host suites pass at `83b4cb5`: Windows **2,622 passed,
29 skipped, 5 xfailed**, plus 129 passing subtests; Linux **2,602 passed, 51 skipped,
5 xfailed**, plus 117 passing subtests. Full CI and native acceptance gates are
recorded in [the validation handoff](../docs/audit_cloud_validation_20261112.md). These results do
not substitute for the original machine's Microsoft Store Python, managed GPU/model
runtime or sealed native editor acceptance.

## Open failures

Original-machine follow-up: direct Store-venv workers still fail the two
`test_cancellation_stops_descendant_and_discards_output` cases because alias-launched
children escape the named cleanup job. The actual managed worker passes both, and
all 33 process/QML checks, with the Store venv running the application. No assertions
are weakened or marked xfail. PySide6 6.11 also reproduces a QML GC access violation;
6.12 passes the same 26-case subset and is now the minimum dependency. See
[local acceptance](../docs/audit_windows_acceptance_20261112.md) for full-suite results.

Historical cloud result: none in the final host suite at `83b4cb5`. Windows path aliases now deduplicate
runtime search paths and exclude existing stack edits correctly. Transient Windows
staging-directory locks receive a bounded retry; persistent cleanup failures are
reported rather than suppressed. All original assertions remain in force. Separate
native-candidate gates and algorithm NaN failures are tracked in the validation
handoff and do not count as passing native acceptance.

The three causes listed here on 2026-11-12 were fixed the same day: the mask-capable host-job runner was
restored (`test_camera_raw_host_jobs`), Edit Collection no longer opens blank (`test_qml_shell_collection_dialog`),
and content-search hits match on case-sensitive file systems (`test_qml_photo_search`).

The continuation repaired one further stale schema assertion in `test_full_render`:
PhotoCraft's explicit calibration aliases preserve the host names, and crop is a host
field rather than a native CameraRaw field. The test still rejects schema drift.
A subsequent installed-app check reproduced requested file selection being reset to
the first grid photo. Folder context is now published before loading completion, and
two visible-QML regressions cover initial and second-launch selection (39 related
startup/grid/collection checks passed).
One later diagnostic run exposed an intermittent retouch button failure. Demo edits
could start a real native detail job and briefly disable controls; the detail callback
now honors demo mode. A new demo/real routing regression reproduced the defect before
the fix, and 29 preview/retouch checks passed afterward. Viewer fixtures also stop their
workers and delete engines explicitly; no assertion was removed or relaxed.
Culler shutdown and Qt lifetime regressions now run in the Windows handoff CI step;
original-machine reproduction and native release gates remain in the linked record.

## Expected failures (strict `xfail`)

| Test | Count | Reason |
|---|---|---|
| `test_ai_results_phase1` | 5 | Behaviour changed on purpose: the AI-Culler foundation pass is observational only and no longer changes bucket assignment. |

## Skipped off Windows or without an optional dependency

Added 2026-11-12; each skip carries its reason in the test.

- `test_ai_model::test_default_model_installation_uses_managed_root_not_local_appdata`,
  `test_ai_paths` x2 (`test_windows_root_ignores_store_virtualized_local_appdata`,
  `test_managed_root_is_short_enough_for_deep_package_paths`),
  `test_file_associations` x2 (`test_current_command_uses_executable_when_frozen`,
  `test_open_windows_file_association_chooser_creates_probe_and_opens_dialog`),
  `test_editor_log::test_the_editor_is_launched_without_a_console_window`: Windows-only
  behaviour (Store Python path redirection, `WindowsPath`, the registry chooser, process creation flags).
- `test_semantic_masks` x2 (`test_sky_boundary_repair_fills_an_image_supported_notch`,
  `test_water_topology_removes_disconnected_upper_false_positives`): need OpenCV, which ships
  in the managed AI runtime and not with the application.

## Repaired on 2026-11-12 (failed on Linux, now pass everywhere)

- Paths spelled for the platform instead of with a drive letter (`C:` holds `os.pathsep` and is not
  a root off Windows): `test_ai_env` x2, `test_ai_python_runner` x2, `test_aiculler_workflow`
  (`..._route_managed_gpu_profile`), `test_production_workflows`.
- The XDG variables set next to `LOCALAPPDATA` / `USERPROFILE`: `test_ai_paths` migration x2,
  `test_aiculler_workflow` (`..._prefers_image_triage_cache`), `test_ai_runtime_packages`
  (`..._without_home_lookup`).
- `test_ai_clean_machine_check` x2: helper binaries named as the platform names them (`.exe` only on Windows).
- `test_ai_runtime_packages` x2: the compact install is recognised by its `-base` lock where a lock
  ships for the interpreter, and by the absence of PyTorch requirements where none does.
- `test_app_data_migration` x5: the migration is a no-op off Windows by design; the tests tell the
  module it is on Windows so its copy and verify logic is exercised on every platform.
- `test_editor_log` x2: a stand-in for `ctypes.windll` off Windows.
- `test_photocraft_control_idle`: the fake editor shuts its listener down; closing alone does not stop
  a blocked `accept()` on Linux.
- `test_qml_shell_people_sidebar` x3: the fixture now registers the `thumbs` context property.
- `test_qml_grid_autoscroll`: the anchor mark is Windows' reader-mode cursor; off Windows there is
  none to show, and the test says so.
- Test drift: `test_cascade_run` x2 (the folder-wide repeat passes, added after the tests, folded the
  second survivor; the tests now turn them off as they already did the duplicate collapse),
  `test_face_stack` (the face tag gained a `+tiles` suffix).

## Removed

- 2026-11-12: `test_ai_training.py` (its one strict `xfail`,
  `test_registered_training_source_enabled_state_is_persisted`, with it) and `test_ai_status.py`,
  deleted with `ai_training.py` and `ai_status.py`, which nothing imported.
- Rows for `test_window_catalog_cache` and `test_topbar_style` were dropped from this file: both
  test files went with the Widgets main window.

## Repaired in WI-0.3
- `test_catalog_repository` x2: stub drift (patched a removed `_catalog` attribute). Repaired; real behaviour unchanged.
- `test_aiculler_technical_tags`: bit-exact float comparison relaxed to 1e-6.
- `test_ai_results_phase1` tag definitions: expectations rewritten to the current design's invariants.
- (`test_decision_harvest`, `test_grid_failures`, `test_nav_rail` and `test_preview_polling`, also
  repaired then, have since been removed with the Widgets UI.)
