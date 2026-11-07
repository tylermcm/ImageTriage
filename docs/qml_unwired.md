# QML integration status

The QML shell is the only desktop frontend. The old Widgets screens and their
controllers are removed; they are not a fallback or a second implementation to
continue developing. See `qml_shell.md` for the current architecture.

## Connected services

The shell uses shared Python backends for folder scanning, thumbnail/preview
loading, saved decisions and ratings, collections and people, metadata and
histograms, file bundles and undo, batch plans, transfers, AI setup and culling.
`Viewer.qml` and `viewer_host.py` implement the full-screen viewer. PhotoCraft is
hosted through `editor_host.py`, `editor_filmstrip.py` and `photocraft_bridge.py`.

The top-bar photo search is connected to filenames and the existing semantic/
people search worker. It does not open the command palette. Native culling exports
an index which is imported on a worker; an import failure retains culling decisions
and reports the search problem. Default profiles include semantic indexing.

## Boundaries still requiring validation

- Disabled commands and controls must remain visibly unavailable; do not restore
  deleted screens to provide them.
- Native culling remains explicitly selected through its runtime/validation
  configuration. Its source validation does not certify the old portable build.
- The recorded portable Qt crash and the unapplied face-session lifetime fix
  remain separate runtime work; see `cli_culler_cpu_gpu_investigation.md`.
- Search wiring was exercised with real TinyCLIP on synthetic images. Search
  relevance on a varied album still needs expected-match validation.
- Changes to PhotoCraft require the companion integration checks and real hosted
  lifecycle validation described in `photocraft_handoff.md`.

Build new UI in QML and extract reusable behavior into UI-independent backend
modules. Keep the QML-only architecture guard and relevant feature regressions
passing; do not recreate Widgets dialogs or add another UI launch selector.
