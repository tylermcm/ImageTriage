# Camera Raw 18.6 redesign

Target updated by the owner on 2026-10-15: match the supplied Adobe Camera Raw
**18.6.0.2698** screenshots, interface and functional behavior. This replaces
the earlier 18.4 selection; access to an older version is not required. This is a development brief and source audit, not a
claim of parity or a completed redesign.

**Reference update:** the supplied 18 PNGs cover the main workspace, panels,
tools, menu, save options and General preferences. One additional JPEG is too
dark to read. See [`acr_reference_inventory.md`](acr_reference_inventory.md).
The native implementation is in progress; the first UI slice adds live layout
JSON, a separate rail/navigation strip, panel expansion modes, color-based HSL,
grading wheels, RGB curve channels, compact Effects/Detail and reset controls.
These use existing processing, rather than claiming new ACR algorithms.

The owner requested live JSON editing instead of recompiling for visual changes.
Companion `design/camera-raw/layout.json` and `tokens.json` now hold the tunable
layout and palette. `scripts/preview_camera_raw.ps1` launches the isolated debug
preview. One native build is required for the reload support/new behaviors;
subsequent geometry, palette and preview-section edits do not require rebuilding.
The native debug preview passed live reload checks for geometry, panel selection,
theme, invalid JSON fallback, unchanged photo parameters and unchanged executable
SHA-256 in one running process. No hosted build has been promoted.

## Reference gate

Use observed 18.6 behavior and public documentation; implement independently in
PhotoCraft's Rust/egui UI and engine. Do not copy Adobe code, profiles, icons or
other product assets.

The image linked from Adobe's current Introduction to Camera Raw page is labeled
**Camera Raw 13.3**. It was inspected during this session and rejected by the owner
as the design reference. Do not use it to establish 18.4 layout, panel order,
spacing, tool placement or screenshots for acceptance. A page's update date is
not evidence of the version pictured inside it.

The owner-supplied 18.6 captures are the visual baseline. Their original pixel
sizes and hashes are recorded locally. Exact OS display scale is not recorded;
measure proportions from the full captures and inspect native renders. Missing
hidden states can be verified as needed without blocking work on known screens.

Authoritative sources consulted:

- [Adobe release notes](https://helpx.adobe.com/camera-raw/desktop/whats-new/release-notes.html),
  specifically June 2026 / 18.4. These distinguish 18.4 from later releases.
- [Adobe's 18.4 announcement](https://community.adobe.com/announcements-561/camera-raw-18-4-is-live-precision-masking-vectorscope-and-faster-workflows-1628342).
- [Color and tonal adjustments](https://helpx.adobe.com/camera-raw/desktop/using/make-color-tonal-adjustments-camera.html).
  This is a living document; individual features need version checks.
- [Camera Raw versus Camera Raw Filter](https://helpx.adobe.com/camera-raw/desktop/get-started/overview-and-setup/differences-camera-raw-camera-raw.html).
  This page dates to 2023, so its restrictions need confirmation in 18.4.

The version-specific scope now includes the features shown in 18.6 (such as
Glow, Trim, metadata and blemish removal). They remain open functional work until
implemented and verified. Features from 18.7 are not required by these captures.

## Entry points

The owner calls this the Camera Raw filter. Image Triage also uses the same shell
for RAW development, so both routes must remain functional:

- Pixel/Smart Filter: preview the selected layer; OK commits exactly one command,
  Cancel retains the document; reopening a Smart Filter updates it in place.
- RAW workspace: retain sensor-source identity and editable recipes; Open develops
  into the full editor; navigation, autosave, returning and full-size exports keep
  the same edits. Image Triage currently owns the filmstrip and native parent; native strip migration
  is the preferred direction under evaluation (see filmstrip_native_plan.md).

Do not assume that a tool available during RAW opening is available to the pixel
filter, or show both host and native filmstrips at once.

## Source audit at PhotoCraft fa8a1f21

| Area | Existing implementation | Work required before claiming parity |
|---|---|---|
| Workspace | `camera_raw_ui.rs`: seven collapsible edit sections; fixed histogram; different standalone/hosted layout | Measure verified 18.6 shell, panel order, rail, footer, spacing, display-scale behavior and panel interactions |
| Light / Color / Effects | Exposure, contrast, highlights, shadows, whites, blacks, relative temperature/tint, vibrance, saturation, texture, clarity, dehaze, vignette, grain | Compare ranges, defaults, resets, numeric editing, Alt diagnostics and processing against 18.6 fixtures; WB caption is currently always As Shot |
| Curve | Parametric regions and master/R/G/B point curves, shared point editor | Verify channel controls, presets, gestures, targeted adjustment and outputs |
| Color Mixer | Eight HSL bands | Point Color and any other 18.6 modes need separate model, processing, UI and serialization |
| Color Grading | Four sets of numeric H/S/L controls, blending and balance | Interactive wheel layout and gestures, including local grading, are not supplied by these numeric controls |
| Detail | Sharpening and luminance/color noise amounts/detail | Noise contrast/smoothness, enhancement eligibility, preview and quality parity remain unverified or missing |
| Optics / Geometry / Calibration | No corresponding settings in the `CameraRaw` parameter struct | Add real processing stages and persistent settings; do not substitute unrelated destructive document commands |
| Scopes / navigation | RGB histogram, clipping, probes, vectorscope, zoom/pan, native detail refinement | Compare 18.6 scope geometry/interaction and preview quality; floating scopes and some options are later-version extensions |
| Local adjustments | Scope can analyze the document selection | Selection analysis is not Camera Raw mask authoring; masks, combinations, ranges, feather/edge, two-sided gradients and local grading need implementation |
| Remove / Lens Blur / AI | Not implemented by the Camera Raw pipeline | Build or integrate appropriate processing; verify tool eligibility, failure/cancel, saved results and offline/reopen behavior |
| RAW source / color | Native raw crate and hosted recipes/prefetch | Verify Kelvin WB, profiles, camera color, demosaic and tone behavior using matched source fixtures; relative filter WB is a different contract |
| Persistence | `.pcraft`/recipe settings, XMP subset, verified PSD descriptor subset | Round-trip every newly implemented setting, preserve unknown Adobe descriptors, verify Photoshop acceptance independently |
| Preview / output | Float CPU proxy and native full-resolution refinement; same develop path used for output | Characterize full-resolution differences and latency; approximate proxy scopes and float input do not establish HDR parity |

Relevant companion sources: `crates/ui-egui/src/camera_raw_ui.rs`,
`camera_raw_scope_ui.rs`, `camera_raw_preview_ui.rs`, `camera_raw_detail_ui.rs`,
`raw_workspace.rs`, `raw_recipe.rs`; `crates/algo/src/camera_raw.rs`;
`crates/io/src/camera_raw_map.rs` and `camera_raw_xmp.rs`.
Existing evidence: companion `docs/camera-raw-histogram.md`,
`docs/camera-raw-zoom.md`, `docs/file-format-parity.md`, and gap G14.

## Implementation and acceptance order

1. Establish verified 18.6 visual and interaction references, with a control/range/
   default/eligibility inventory for both entry points. Record missing references.
2. Rebuild the native workspace shell and panel/control behavior against those
   references. Keep processing unchanged while checking visual geometry. Verify
   narrow windows, display scaling and all host themes with actual renders.
3. Close deterministic processing gaps in small stages. Each addition needs
   validated engine parameters, malformed-input tests, preview/output agreement,
   undo and file/recipe round trips before exposing its controls.
4. Add the local-mask model, authoring tools, combinations and local adjustments;
   then Remove/Lens Blur/AI pipelines with reusable engine commands. Measure actual
   output quality instead of equating similarly named tools with equivalence.
5. Compare matched inputs/settings with 18.6 output fixtures at relevant bit depths
   and color spaces. Document numerical differences and unsupported behavior.
6. Rebuild an isolated candidate and run native/wasm/layer/clippy/corpus checks as
   required by the companion AGENTS.md, then the Image Triage live lifecycle suite.
   Preserve the known-good executable until all required promotion gates pass.

Acceptance includes persistent filmstrip navigation, stash/restore, native Save
and grid propagation, close-tab/reopen, hidden startup, theme/resize integration,
fullscreen, shutdown/process cleanup, RAW recipe persistence and full-size export
agreement. The source merge, UI similarity and a green unit test suite each cover
only part of this contract. No overall parity percentage is assigned.

## Visual acceptance standard

The owner reiterated that the objective is the closest possible 1:1 parity,
not a broadly similar editor. Use the supplied 18.6 captures as the baseline.
Compare native screenshots at an equivalent logical scale; measure panel and
rail widths, row pitch, field bounds, control shapes, fonts and panel hierarchy.
Use JSON for tunable geometry/palette, with native code only for missing renderer
capabilities and functional behavior. Track every remaining visible mismatch.

The first preview used generic circular slider handles and excessive row pitch.
Owner identified this discrepancy. Camera Raw now uses upward-pointing house-like
handles and compact field/track rows, configured by sliderFieldHeight=18,
sliderTrackHeight=10, sliderInnerGap=0, sliderBottomGap=0, rowGap=0.
Other PhotoCraft sliders retain their established appearance.

Outstanding visible differences include missing Auto/B&W/HDR/Profile controls,
unsupported edit sections/tools, exact header/rail/footer icons and control
arrangement, white-balance selector/Kelvin mode, color-wheel selector icons,
curve selector icons, scope presentation and precise section/slider details.
Do not describe the current preview as full visual or functional parity.

## Overlay comparison workflow

Owner requested regularly overlaying the implementation over the supplied source.
Companion `scripts/compare_camera_raw.py` produces reference/preview/50% overlay/
absolute-difference images plus the crop coordinates and uniform scale factors.
Match control states and anchor the same panel edge. Do not independently stretch
width/height or align each row separately; that would conceal spacing drift.
Reference crops and outputs remain outside the repository.

The initial Light/Color overlay used source 13289.png crop (38,594,550,1514)
and native 1440x900 preview crop (1134,211,1404,696), normalized to width 270.
It exposed header title/chevron indentation, unnecessary slider-track end insets,
and remaining row-pitch drift. Those corrections have dedicated live JSON fields:
sectionTextInset, sectionIconInset, sliderTrackInset and sliderLabelRowHeight.
WB controls, enable/disable eyes and other missing features remain visibly open.
An image difference score alone is not proof of parity, particularly when values,
font rasterization and OS display scaling differ.

Validation checkpoint: full serial UI suite 3034 passed, 13 ignored; focused Camera
Raw integrations 35 passed; native all-target clippy, 24 wasm checks and layering
passed. Final overlay-driven geometry receives another native screenshot and
focused interaction run. Hosted handoff validation/promotion remain outstanding.

Final overlay checkpoint: the native preview was rebuilt after the scrollbar
fix; the repeated 35-test focused run passed. Final Light comparison uses source
crop (38,594,550,1092), native crop (1134,211,1404,474), normalized width 270.
Measured six track-center offsets: 0.5, 1, 1, 0, 0.5, 1 pixels. The section bounds
and header indentation closely align. This limited measurement does not certify
the absent bypass icons, exact typography, other panels or image-processing parity.

## Field and track fidelity correction

Owner identified differences still visible at the right edge of the Light
sliders and their numeric fields. The earlier track-center measurement covered
vertical alignment only; it did not establish endpoint, field or text parity.
At normalized width 270, the earlier preview field ended at x239 versus the
reference edge around x247, and its track was similarly short. Its single zero
started near x216 while all reference values started at x208: the preview was
centered and monospaced, while the reference uses a fixed left inset.

Added independent right padding, field height/offset and numeric font/alignment/
padding/baseline settings to the live JSON. Reference field bounds are about
x204..247, y43..62 for Exposure; reference ink x208..231 for 0.00 and x208..213
for 0. Crop scaling introduces antialiasing at the rectangle edges; compare
fresh overlays and multiple rows rather than using only a single pixel cutoff.
The wider numeric control must retain its full interactive width and support
click-to-type, arithmetic, keyboard stepping and scrubbing.
