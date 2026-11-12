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

## Grid navigation and inspector

The library grid supports middle-button autoscroll: click to set an anchor, then
move above or below it to scroll, with speed increasing with distance. Another
click or Escape stops it without changing selection. Holding the middle button
and dragging stops on release; leaving the grid or changing folders also stops it.
The inspector remembers its selected Overview, Quality, or AI tab between sessions.
Entering a collection bookmarks the folder's current photo and viewport separately.
Both Back to folder and clicking the selected collection restore that position;
collection browsing does not overwrite the folder's saved current photo.
The photo context menu offers a direct, named Add to entry for the last collection
used to add photos. That destination survives restarts; deleted collections are
skipped, and photos already in the destination show a disabled checkmarked entry.
On Windows, the autoscroll anchor and directional cursors use the system's
common-control cursor resources, loaded at runtime rather than redrawn or bundled.

## Viewer zoom

Settings > Appearance > Photo review includes Gray out rejected photos. It
previews dimming and desaturation in the grid and preview filmstrip immediately,
and Save persists the choice. It defaults on, leaves decision badges unchanged,
and the Rejected tab's grid always shows normal color and brightness.

The wheel over the photo changes magnification incrementally, keeping the image
point under the pointer fixed where the pan bounds allow. The live percentage is
shown beside Fit and 100%. Z selects 100% from a variable scale, then toggles back
to Fit; 0 always fits. Drag the enlarged image to pan. The separate loupe mode
and L shortcut are removed.
Zoom-out stops at half the fitted image size (with native 100% still available
for intrinsically small images). RAW thumbnail metadata cannot override larger
decoded dimensions; larger camera dimensions are accepted only when their
oriented aspect ratio matches the preview. This keeps 100% tied to photo pixels.
The zoom ceiling is 64 times the reported pixel scale or 32 times Fit, whichever
is larger, so small embedded-preview dimensions cannot restrict inspection to a
slightly enlarged view. The unused tagging button and T placeholder are removed.

The bottom navigator remains visible at variable zoom. Its 35%-opaque viewport
outline follows both panning and scale, including portrait images and zoom levels
below Fit. Wheels over the filmstrip or editor retain their own scrolling behavior.
Photo navigation switches directly between the placeholder and ready image,
without a fade or image-to-image blend. Live RAW edits also stay immediate.
The zoom navigator and filmstrip share one animated vertical offset when controls
are hidden, leaving the navigator 20 pixels above the window's bottom edge.
The preview blocks input to the library and main titlebar beneath it, including
during its closing fade. Its X returns to the library. When controls are hidden,
hovering within the top 52 pixels temporarily reveals the banner; moving away
hides it again. The banner's controls button restores the full preview UI.

## Validation and packaging

Cull review keeps a separate stack model, so choosing a photo updates the detail
without resetting the list. Native rejection reasons are grouped into quality,
exposure, blank frames, burst ranking, scene ranking and bracket selection;
unrecognized reasons remain under Other rejects. The full report explanation
is retained in the detail. Needs review stays separate and cannot be applied as
a rejection. Sort by Flag reason groups the flagged photos within their stacks,
and Apply lets each reason category be included or excluded. Review uses a
200-pixel wheel step, a scrollbar and native middle-click autoscroll; right-click
offers Restore, Show in grid and Filter by this reason. All stacks remain
accessible through the virtualized list, without the old 300-stack limit.

Marking Cull results as Rejects shows an enlarged animated logo and progress
directly over the Cull window. Decision updates run in 32-photo batches between
Qt event-loop turns, with a single grid/count refresh and one undo entry at the
end. Existing rejects are not toggled off; winners remain protected. Annotation
persistence continues through the existing background queue. Shutdown finishes
accepted batches before that queue is flushed.

Modal dialogs identify the application layer they cover with `sourceItem`.
While one is shown or fading out, Main disables input to the application and
popout viewer, then restores focus after dismissal. Popovers consume mouse
presses and exclusively grab taps so controls behind their menus cannot react.

`tests/test_qml_only_architecture.py` checks that retired screens and Widgets
imports stay absent, and starts the real QML application with Widgets imports
blocked. The QML regression suite covers the grid, viewer, settings, collections,
menus, search, culling, file operations and editor host. Backend tests cover the
shared services separately; screen-only legacy tests and launch scripts are retired.

`freeze_support.py` and `pyproject.toml` package QML resources and the neutral
application asset directory. A source cleanup is not a new portable release:
validate a frozen candidate before promoting it.

The popout Crop tool supports drawing a rectangle on the photo, moving it from
inside and resizing its corners. Original, Free, square and common aspect ratios
are available; holding Ctrl temporarily releases the ratio, including during a
drag. Escape cancels a drag. Each completed drag is one Undo step. Switch back to
Adjust to view the cropped result; Crop always reveals the full photo again.

Crop is stored non-destructively as normalized `cameraRaw.hostCrop` coordinates.
The host strips this field before sending development settings to PhotoCraft,
then crops its full-resolution output, preserving 16-bit pixels and the color
profile. Done publishes a cropped grid preview and recipe using the existing
manifest contract; exports apply the same crop before optional Super Resolution.
The RAW stays untouched. Rotation, flips and perspective remain pending tools.

The Mask tab authors PhotoCraft's native `cameraRaw.masks` recipes: paint/erase
brush masks, linear/radial gradients and luminance ranges, with per-mask amount,
enable, invert and twelve local adjustments. Brush maps use PhotoCraft's bounded
coverage RLE format. Drawing over a cropped preview maps coordinates back to the
uncropped RAW. Gradient redraw, rename, duplicate, delete and adjustments share
the editor's undo history and per-photo drafts. The red coverage overlay is a
display aid; PhotoCraft performs every local adjustment in previews and output.
AI Masks reuse the shared MaskEngine services: BiRefNet for Subject/Background,
OneFormer for Sky and Scene regions, and OneFormer → SAM → BiRefNet for People.
Objects uses SAM click prompts: click to include, Ctrl-click to exclude. Further
clicks reuse the image embedding and update the same mask while preserving its
adjustments. Subject, Sky and Background create immediately. Landscape opens a
category chooser; multiple selected regions become one editable mask. People
offers individual person selection after background detection when Mask opens.
Person coverage is cached by source fingerprint and model revisions. Existing
culling face detections are reused as hints; face embeddings alone are not
whole-person coverage, so segmentation still runs the first time.

AI inference runs off the GUI thread, with progress and cancellation. A photo
switch or save invalidates pending replies; source fingerprints prevent importing
a result after the underlying file changes. Coverage maps refer to the uncropped
preview and become ordinary native PhotoCraft raster masks, including Background's
inversion. Model buttons are gated independently by their installed bundles.
The mask list floats at the viewport's upper right, below the top rail and left
of the editor. Brush and range tuning appears above local adjustments. Color
and luminance samples come from PhotoCraft's native working pixels, excluding
local masks. Click samples a point, drag samples an area, and Shift-click adds
another color component. Native range coverage supplies overlays and thumbnails.
Rebuild the RAW preview helper after updating its sampling/coverage protocol;
older helpers report a clear error rather than returning misleading samples.

The floating mask list exposes mask actions, component operations and the native
Add/Subtract/Intersect tool menu. Brush and SAM refinements keep the original
base mask and its local adjustments. The legacy editor's six display modes are
preserved in the UI-independent `mask_display` helper: color overlay, color on
B&W, image on B&W, image on black/white, and white on black. Color and opacity
are display-only; automatic overlay hides coverage during adjustment gestures.
Pins and automatic panel hiding are independent display controls. Compound-mask
inversion stays unavailable at group level because PhotoCraft inverts the base
shape before composition; individual components can still be inverted.

Mask adjustments and global adjustments share `RawAdjustmentSection`,
`RawBasicAdjustments`, and `RawColorSlider`. Local bindings write only the
selected mask, with the same numeric editing, slider gestures, color tracks and
section geometry. Local recipes also support Vibrance, parametric and RGB point
curves, the eight-band Color Mixer, all four Color Grading wheels, and
Calibration. These reuse PhotoCraft's global algorithms before coverage blending.
Detail, spatial effects, crop and decoding controls stay image-wide. The native
preview helper and hosted PhotoCraft executable must both include the expanded
local recipe schema; the source launcher prefers the validated hosted build
when it is present and no explicit executable override was supplied.

The in-popout RAW editor remembers its open/closed state between viewer visits
and application launches. Closing the popout hides the window in one action and
keeps its current in-memory edit session. Explicitly closing the editor updates
the preference; application shutdown cleanup does not overwrite it.

For offscreen captures use `python -m image_triage.qml_shell.app --folder
<synthetic-folder> --grab <output.png> --size 1440x1000`. Explicit `--hover` and
`--click` hooks use QtTest; ordinary startup and passive captures do not.


### RAW retouch

The popout Remove tab provides Remove, Spot Healing, Heal and Clone using
PhotoCraft's existing completion and Poisson-blending kernels. Paint directly
on the image; Alt-click chooses the source for Heal and Clone. Size, Feather
and Opacity use the same controls as the adjustment pane. A stroke is one undo
step, with individual deletion and Clear available in the pane. Source markers
and the brush outline are view-only.

Strokes are normalized coordinates in the uncropped developed image. They live
in `cameraRaw.retouch`, replay after global development and before local masks,
and travel through native preview, full-resolution Save and export. Cropping
maps pointer coordinates back into the full image; the original RAW is never
modified. Proxy completion is an interactive approximation; full-resolution
Save/export recomputes completion using full-resolution pixels.

The paired helper and hosted editor are required. `cameraRawRetouch: 1` gates
opening/saving a retouched recipe with an editor; older editors return an error
before accepting it. Retouch falls back to CPU development to prevent a GPU
path from omitting strokes. Limits are 128 strokes, 4,096 points per stroke,
32,768 total points and a 16-megapixel dense domain per stroke. Authoring and
native preview/full-render entry points reject oversized domains with a request
for smaller separate strokes. Generative AI and automatic distraction removal
remain unavailable.

## Red Eye

The RAW popout Red Eye pane uses PhotoCraft's existing correction algorithm.
Set Pupil Size and Darken, then click a red pupil. Click a correction marker or
its list entry to adjust it; Next pupil starts another correction. Delete, Clear
and normal editor undo/redo are available. Auto and Pet Eye are disabled.

Corrections persist in `cameraRaw.redEye` and replay for preview, native Save and
export without changing the original RAW. At most 64 corrections are accepted.
The paired helper/editor must advertise Red Eye support; older binaries reject
these recipes rather than silently ignoring corrections.
