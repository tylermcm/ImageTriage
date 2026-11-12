# Test failure ledger

Last checked 2026-11-12 on Linux (Python 3.13, PySide6 6.12, `QT_QPA_PLATFORM=offscreen`).

Rules:

- A test that fails because the application is wrong stays failing and is listed under
  **Open failures** with its cause. It is not skipped, `xfail`ed or deleted to make the run
  green; it leaves this list when the code is fixed.
- A test whose expectation was retired on purpose is a **strict** `xfail` (it fails the run
  if it starts passing) and is listed under **Expected failures**. `pytest tests -rx` lists them.
- A test that cannot run where it is (Windows-only behaviour, a missing optional dependency)
  skips with a reason that says what it needs. `pytest tests -rs` lists them.
- Update this file whenever a row is added or removed.

The suite is not green: 14 tests fail for the three causes below.

## Open failures

| Test | Count | Cause | Needs |
|---|---|---|---|
| `test_camera_raw_host_jobs` (every test in the file) | 12 | The tests are the mask-capable runner's: `HostJobRunner(warmer=, subject_mask=, scene_masks=, people_masks=)`, `KIND_SUBJECT_MASK`, `KIND_SCENE_MASKS`, `KIND_PEOPLE_MASKS`. The merge of the QML shell (`7769920`) kept these tests, `mask_warmup.py`, `scene_mask_jobs.py` and `subject_mask_service.py` from one parent (`c712256`) and took `camera_raw_host_jobs.py` from the other (`83b0efb`), which has the model gate and only the Super Resolution kind. The runner therefore lost the mask jobs and the warmer. Nothing in the QML shell instantiates `HostJobRunner` either. | A decision: restore the mask kinds and warmer in the runner and have the shell poll it, or retire the editor's host-job channel and delete the runner, `mask_warmup.py` and both test files together. `test_editor_tools.py` imports its fakes from this test file. |
| `test_qml_shell_collection_dialog::test_the_hook_and_an_existing_colour_only_collection_open_on_color` | 1 | `CollectionDialog.qml` loads its fields in `onShownChanged`, which reads the `editing` binding. `shown` and `editing` both follow `UiState.collDlg`, and with Qt 6.12 `shown` is updated first, so `load()` still sees `editing === false`: Edit opens with an empty name, no colour and the default icon instead of the collection's own values. | `load()` should decide from `UiState.collDlg === "edit"` itself (or run after both bindings have settled). |
| `test_qml_photo_search::test_imported_native_vectors_are_searchable_in_qml_grid` | 1 | Content-search hits are keyed with `casefold()` (`unified_search_task._search_match_path_key`) and looked up with `os.path.normcase` (`qml_shell/photos._path_key`). The two agree on Windows only; on a case-sensitive file system a hit whose path has a capital letter (`DSC_0001.jpg`) is never shown. | One key function for both sides. |

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
