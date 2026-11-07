# QML application

QML is the only Image Triage desktop UI. Run `python -m image_triage`, optionally
with a folder argument. The old Widgets main window, preview, dialogs, controllers
and selectors have been removed from the source tree. Historical versions remain
in Git history.

## Structure

- `image_triage/qml_shell/qml/`: screens, dialogs, menus and the full-screen viewer.
- `image_triage/qml_shell/app.py`: `QGuiApplication`, QML engine and context objects.
- `image_triage/qml_shell/*_model.py`: screen state and thin backend adapters.
- `image_triage/qml_shell/viewer_host.py`: image loading and viewer state.
- `image_triage/qml_shell/editor_host.py`: hosting the native PhotoCraft editor.
- `image_triage/palette_commands.py`, `shortcuts.py`, `theme_tokens.py` and
  `face_groups.py`: reusable data, query and appearance logic without screen classes.
- `image_triage/file_ops.py` and `safe_trash.py`: file bundles, moved records,
  safe-trash location and restore manifests.
- `image_triage/unified_search_task.py` and `semantic_runtime.py`: background
  photo/people search and installed TinyCLIP model resolution.
- `image_triage/assets/`: application icons; QML assets stay beside the QML files.

The Python backends remain part of the application. Removing the old UI does not
remove image decoding, saved decisions, collections, AI workers, file operations
or the native PhotoCraft integration. Application modules must not import
`PySide6.QtWidgets` or restore an alternative desktop entry point.

## Search and culling

The top-bar field searches photos; Ctrl+F focuses it. Commands have a separate
button and Ctrl+K shortcut. Filename matches appear immediately; semantic/people
matches arrive from a debounced worker. Search intersects the existing grid scope
and filters. Without a usable index/runtime, filename search remains available.

The native CLI-Culler source launcher is `scripts/run_cli_culler_qml.ps1`.
Its preparation page keeps four feature cards and strictness choices visible;
Fine-tune opens detailed settings only when requested. Semantic search is on in
new default profiles, and saved opt-outs are respected. Native settings are
validated by the isolated engine. See `cli_culler_cpu_gpu_investigation.md` for
runtime provenance, validation evidence and the remaining portable-build issue.

## Viewer zoom

The wheel over the photo changes magnification incrementally, keeping the image
point under the pointer fixed where the pan bounds allow. The live percentage is
shown beside Fit and 100%. Z selects 100% from a variable scale, then toggles back
to Fit; 0 always fits. Drag the enlarged image to pan. The separate loupe mode
and L shortcut are removed.

The bottom navigator remains visible at variable zoom. Its 35%-opaque viewport
outline follows both panning and scale, including portrait images and zoom levels
below Fit. Wheels over the filmstrip or editor retain their own scrolling behavior.

## Validation and packaging

`tests/test_qml_only_architecture.py` checks that retired screens and Widgets
imports stay absent, and starts the real QML application with Widgets imports
blocked. The QML regression suite covers the grid, viewer, settings, collections,
menus, search, culling, file operations and editor host. Backend tests cover the
shared services separately; screen-only legacy tests and launch scripts are retired.

`freeze_support.py` and `pyproject.toml` package QML resources and the neutral
application asset directory. A source cleanup is not a new portable release:
validate a frozen candidate before promoting it.

For offscreen captures use `python -m image_triage.qml_shell.app --folder
<synthetic-folder> --grab <output.png> --size 1440x1000`. Explicit `--hover` and
`--click` hooks use QtTest; ordinary startup and passive captures do not.
