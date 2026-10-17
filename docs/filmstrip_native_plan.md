# Native PhotoCraft filmstrip direction

The owner prefers a filmstrip rendered inside PhotoCraft. Adopt that as the
preferred architecture while preserving Image Triage browsing and data behavior.
The existing docked strip stays available until the native replacement passes
the handoff gates; never display both strips together.

## Source finding, 2026-10-15

The fetched PhotoCraft integration tree at fa8a1f21 contains no filmstrip-named
implementation (searched tracked and hidden source/docs outside build artifacts).
`crates/ui-egui/src/raw_workspace.rs` provides persistent RAW workspace switching,
loading shells, prefetch and recipe continuation, but no thumbnail browser.
The observed filmstrip may belong to another build or branch. Reuse any existing
native implementation if it is located; do not assume its behavior from ACR's UI.

The current strip lives in Image Triage `image_triage/ui/preview_studio.py`:
`Filmstrip`, `FilmstripThumb`, `FilmstripHandle`. It supports centered current
photo, cached thumbnails/tags, click/wheel/page navigation, resize and collapse.
`image_triage/preview_controller.py` supplies the photo-only browse order,
selection/grid synchronization, thumbnail fallback/cache and edit-state updates.
The PhotoCraft bridge owns the existing stash/restore and Save handoff.

## Proposed ownership

| Responsibility | Owner |
|---|---|
| Native strip drawing, hit targets, scrolling, resize/collapse, view settings | PhotoCraft Rust/egui |
| Filtered/sorted photo list, stable identity, rating/label/reject state | Image Triage |
| Selection request from native strip | Host validates and runs its existing navigation pipeline |
| Stash/restore, prefetch, recipe persistence, native Save/grid refresh | Existing bridge/controller path |
| Thumbnail delivery | Bounded visible-range requests, cached by photo/edit revision |

Render the strip as a native view over host-supplied browse data first. Port the
presentation and interactions without duplicating the photo database or replacing
the proven navigation lifecycle. Stable photo IDs and browse generations must
reject stale responses during rapid switching, sorting and filtering. Thumbnail
work stays asynchronous and bounded; no full-folder image decode per repaint.
Control requests must retain the existing authorization/scoped file rules.

## Acceptance before switching the default

- Equivalent click, arrow-key, wheel and page navigation; folder entries excluded.
- Grid/strip selection, rating/label/reject changes and edit badges stay synchronized.
- Stash/restore, Save propagation, closed-tab reopen and return-to-editor work.
- Rapid RAW navigation preserves current prefetch/cache performance without flashes.
- Resize/collapse, theme, fullscreen, hidden startup and shutdown/process cleanup pass.
- Host hides its strip only after native capability is confirmed; fallback restores it.

This is the recommended migration plan, not a completed native filmstrip or a
claim that the currently fetched PhotoCraft build already supplies one.
