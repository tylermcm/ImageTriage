# QML-only source retirement

The current source tree has one desktop frontend: `image_triage/qml_shell`.
The Widgets main window, preview, dialogs, grid, controllers, PocketDrop panel
and unused Widgets popout adapter are removed, along with their launchers and
screen-specific test coverage. They remain available only in Git history and
the ignored local pre-removal snapshots.

Reusable behavior was extracted rather than discarded:

| Backend | Current module |
| --- | --- |
| Command descriptors and ranking | `image_triage/palette_commands.py` |
| Shortcut registry and persistence | `image_triage/shortcuts.py` |
| Appearance tokens | `image_triage/theme_tokens.py` |
| Face-group queries and representative ranking | `image_triage/face_groups.py` |
| Moved-record reconstruction | `image_triage/file_ops.py` |
| Safe-trash locations and manifests | `image_triage/safe_trash.py` |
| Library-index workers and idle gating | `image_triage/library_index_tasks.py` |

The application starts with `QGuiApplication`. Retired UI selectors are rejected.
Icons and the splash used by QML moved to `image_triage/assets`; packaging and
render references use that location. No application module imports
`PySide6.QtWidgets`. Explicit input-simulation capture hooks use QtTest, which
may load the Qt Widgets binding internally; ordinary startup and passive
captures do not.

`tests/test_qml_only_architecture.py` checks retired module absence, forbidden
imports, static QML image paths, and actual startup/capture while Widgets imports
are blocked. The guard runs in the portable, Windows MSI and Linux build gates.

## Validation

- Full QML run: 504 passed, four stale expectations failed, five skipped.
  The four checks were updated to the existing QML behavior and all passed on a
  focused rerun: current settings-guide wording, Keep counting all non-rejects,
  and a 4:3 thumbnail fitting its 3:2 card without stretching. Production behavior
  for these checks was unchanged. This gives 508 passing QML checks across the
  full run and focused rerun.
- Backend extraction, icons, freeze layout, default launch, native worker,
  search and architecture checks: 103 passed.
- Remaining backend suite: 1,684 passed, ten skipped, six existing expected
  failures and 126 subtests passed. Three unchanged failures remain: the active
  face-model tag expectation and two cascade survivor-role expectations.
- `test_camera_raw_host_jobs.py` was kept separate: its unchanged tests pass a
  `warmer` argument which the unchanged `HostJobRunner` constructor does not
  accept. This unrelated backend mismatch is not repaired or hidden by the UI
  retirement.
- The QML source app rendered a synthetic folder after backend extraction;
  the strict blocked-Widgets startup/capture regression passes.

No portable release, installed application or PhotoCraft executable was rebuilt
or promoted. A new frozen candidate must pass its packaging and hosted-editor
checks before replacing a known-good build. Existing local UI/search work was
preserved, and companion PhotoCraft edits were not modified.
