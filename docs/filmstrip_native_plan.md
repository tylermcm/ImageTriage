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

## Implementation checkpoint, 2026-10-17

The in-progress native replacement now follows this ownership split. The retained
Qt widget detaches after capability/data confirmation; it is not deleted. The
first isolated candidate passes hosted click/grid navigation, bounded thumbnail
delivery, host/native rating and reject requests, collapsed keyboard navigation,
resize and process-cleanup checks. Native sorting/filtering dispatches through
the host's existing controllers. Old builds retain the Qt presentation.

Reference 19 supplies the expanded strip. Measured at scale 2: tray height 280px,
selected frame 343x274px, top gap 4px, frame thickness 6px, navigation height 80px.
Those dimensions feed live JSON. Fixed whole-strip overlays and 24 individual
regions expose remaining glyph/color/type differences; different photo content
is not evidence of a UI mismatch. The supplied reference shows one thumbnail,
so spacing for multiple thumbnails is not independently established.

Still required before default promotion: final native gates, a clean RAW handoff
including Save/reopen/fullscreen/shutdown, latest-source measured overlays, and
resolution of the remaining navigation/before-after and preference differences.
The replacement is implemented for testing, not certified as complete ACR parity.


The validated shared-chrome checkpoint defaults the PS1 test launchers to
`acr-glyphs`. Native/host checks and Save/reopen/fullscreen/shutdown gates passed;
the previous `acr-filmstrip` executable remains preserved. Current fixed overlays
measure24 filmstrip regions plus full-client controls in refs16-19. Toggle/filter/
reject/comparison paths improve; the worse star trial was discarded. Every-element
parity, other tools/dialogs and independent multi-thumbnail spacing remain open.
See photocraft_handoff.md and the native SHA-based inventory.
