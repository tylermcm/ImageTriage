# RAW editing integration plan

Research date: 2026-10-08. Initial implementation validated; see the
[handoff](photocraft_handoff.md) for the promoted build, checks and performance limits.

## Intended workflow

Opening a fresh RAW from the filmstrip creates a RAW-backed smart object in
PhotoCraft. The original camera file stays unchanged. Its bytes and editable
development settings survive switching photos, automatic stashing, Save, closing
the editor, and restarting Image Triage. Double-clicking the RAW object's contents
reopens RAW development. Saving regenerates the rendered preview used by the main
app, while the `.pcraft` project keeps the source and settings.

The existing persistent editor window, filmstrip, hidden startup, themes, and
fullscreen behavior remain the foundation. No editor restart per image.

## Baseline traced before implementation

- `image_triage/photocraft_bridge.py`: `NATIVE_SUFFIXES` excludes camera RAW;
  `materialize_for_photocraft` decodes through Image Triage and saves a TIFF.
- `image_triage/preview_controller.py`: `_resolve_photocraft_target` prioritizes
  saved projects, then legacy rendered edits, then native files or converted TIFFs.
- PhotoCraft `crates/io/src/raw.rs`: even direct RAW import develops default
  settings into a 16-bit ProPhoto RGB pixel document. Unsupported variants may
  open an embedded JPEG preview with a warning.
- `crates/doc/src/lib.rs`: `SmartSource::Embedded` already retains named source
  bytes. `SmartObject` has transforms, filters, and a rendered cache, but no RAW
  development settings. `crates/format/src/convert.rs` saves embedded bytes.
- `crates/engine/src/smart_cmds.rs`: source rendering imports and flattens the
  source. Converting an existing pixel layer embeds a nested `.pcraft`, not the
  original camera file. Generic Edit Contents opens a decoded child document;
  saving that child replaces the source with a `.pcraft`. That path must not be
  used for RAW development because it discards the RAW source identity.
- `crates/engine/src/lens_cmds.rs` and `crates/ui-egui/src/camera_raw_ui.rs`:
  `filter.cameraRaw` processes a pixel surface, including on smart objects. It is
  a post-development filter, not a sensor-development interface. It is available
  on supported pixel documents; retaining RAW bytes alone does not change it.
- `crates/raw/src/develop.rs`: the native decoder exposes as-shot/auto/explicit
  camera-channel white balance, demosaic choice, and exposure. Output is currently
  gamma-encoded `u16`; intermediate clipping and output encoding need review
  before claiming unclipped highlight recovery or an HDR workflow.
- `crates/raw/src/lib.rs`: sensor decoding supports DNG, CR2, selected TIFF-EP
  variants, Sony cRAW, RW2 RawFormat 5, and uncompressed ORF. CR3 and RAF sensor
  decoding explicitly return unsupported. Filename recognition is not a promise
  that a particular camera's compression is supported.

## Upstream activity and conflict review

Checked on 2026-10-08 against fetched upstream `e5e3e39`, the local dev log,
tracked roadmap/scorecard/docs, relevant commit history, and all pages of public
open pull requests. Fetch found no newer upstream commit during this follow-up.

`../photocraft/log/devlog.md` is Git-ignored and contains our Image Triage entries
only. It cannot establish what upstream developers are currently doing. No local
`plan/` directory exists. Public issues and PRs provide the best available activity
record; unpublished branches and other developers' private logs remain unknown.

| Evidence | What it means for this plan |
| --- | --- |
| [RAW decoder issue #50](https://github.com/storytold/photocraft/issues/50), open; latest comment 2026-10-05 | Their first-cut scope explicitly opens a normal developed document. Remaining work includes compressed camera formats and camera calibration. It does not describe persisted RAW-backed smart objects. Comments are older than some current code; use the source support matrix as well. |
| [File compatibility issue #215](https://github.com/storytold/photocraft/issues/215), open | Explicit overlap: highlight reconstruction, XMP, RAW preferences, camera coverage, Smart Objects/Filters, and Camera Raw dialog persistence. Issue checkboxes lag landed code, so an unchecked item is not proof that nothing exists. |
| `docs/roadmap.md` and `scorecard/file_compat.toml` | Camera Raw is a stated focus, with RAW preferences including `open_as_smart_object` still reported unread. No committed RAW-smart-object development design was found. Preserve/reuse the existing preference rather than add a competing preference system. |
| Landed `f947906` ([PR #781](https://github.com/storytold/photocraft/pull/781)) and `4b3da49` ([PR #459](https://github.com/storytold/photocraft/pull/459)), dated 2026-10-08 | Camera Raw preview/navigation and PSD smart-filter settings recently changed. These are post-development pixel-filter improvements already in our merged base; do not replace their preview or descriptor machinery. |
| [Open PR #1037](https://github.com/storytold/photocraft/pull/1037) | Reuses an already-open generic Edit Contents document. Direct overlap with `smart_cmds.rs` and its tests; RAW-specific routing must preserve this behavior if/when merged. |
| [Open PR #1183](https://github.com/storytold/photocraft/pull/1183) | Camera Raw area/overflow guard in `lens_cmds.rs`. Keep it separate from new sensor-development commands and preserve its safeguards. |
| [Open PR #1247](https://github.com/storytold/photocraft/pull/1247) | Adds a Low Resolution Previews preference across shared preview/canvas code. RAW preview scheduling must respect the eventual preference and retain detailed/full-resolution inspection; avoid a second conflicting global preview implementation. |
| [Open PR #1090](https://github.com/storytold/photocraft/pull/1090) and [DNG crop issue #950](https://github.com/storytold/photocraft/issues/950) | Decoder error classification and crop behavior are still changing. Reuse upstream fixes and keep real-camera/crop/error regression cases in our acceptance set. |

Conclusion: there is active adjacent work and substantial potential overlap, but
no public open PR or committed implementation for the exact persistent RAW-backed
smart-object workflow was found in this review. That is a dated observation, not
a guarantee that upstream is not privately implementing it. Do not wait indefinitely
for an unannounced feature, and do not build a parallel Camera Raw UI/decoder.

### Revised approach to keep the fork maintainable

1. Before implementation, refresh upstream and recheck #50, #215, and the PRs above.
   If the exact RAW-source/settings feature appears, reassess and prefer adapting
   its API to our host instead of duplicating it. Open PRs are watch items, not
   automatically approved dependencies; do not merge unreviewed PR branches.
2. Split work into small independently reviewable changes: source/settings and
   serialization first; sensor-development commands/cache next; dialog adapter;
   finally the Image Triage capability/handoff. Keep host-only changes separate
   from generic PhotoCraft RAW behavior so future merges can replace generic pieces.
3. Put development commands in a dedicated engine module and RAW-dialog targeting
   in a small UI adapter. Reuse `photocraft-raw` and current Camera Raw components.
   Limit edits to `smart_cmds.rs`, `lens_cmds.rs`, `camera_raw_ui.rs`, the command
   registry, and shared startup/control files to narrow integration points.
4. Make an explicit raw-source target/capability instead of guessing from a filename
   or altering `filter.cameraRaw` globally. Keep RAW development settings separate
   from the existing PSD Camera Raw smart-filter parameters/descriptors.
5. Track a file-level conflict watchlist in the handoff: RAW import/develop,
   smart-object model/native serialization, Edit Contents, Camera Raw dialog/detail
   preview, shared proxy/canvas preferences, and hosted open/stash. Record upstream
   PR state and commit IDs when actually integrating, not just issue labels.
6. After each upstream update, test behavior even without text conflicts: RAW bytes
   remain identical after Edit Contents/Save; settings are not applied twice;
   preview preferences work; crop/orientation/profile stay correct; filmstrip jobs
   cannot apply to the wrong document. This is necessary because these APIs share
   state and can merge cleanly while changing behavior.

No upstream contact, issue, PR, feature coding, or push is needed for this review.

## Implementation sequence

### 1. Establish a real-camera baseline

Collect representative files from the cameras actually used in Image Triage,
including any CR3/RAF and compressed NEF/ARW variants. Inspect sensor decode,
orientation, calibration, dimensions, black/white levels, and white-balance
metadata. Record which files are genuinely decoded versus preview-only.

Use synthetic DNGs for repeatable tests, including bright values that clip in the
default rendered image but remain distinguishable in sensor data. Verify what
lowering exposure can recover, and distinguish output clipping from sensor
saturation. Do not promise recovery of genuinely saturated sensor samples.

If required cameras are unsupported, choose an explicit additional decoder
strategy before rollout. An external decode service would need to preserve sensor
samples and calibration metadata, not merely return a developed TIFF. PhotoCraft's
Rust-only and clean-room source rules apply; do not copy LibRaw/dcraw code into it.

### 2. Add durable RAW-backed smart objects

Reuse embedded source bytes rather than inventing a separate sidecar RAW copy.
Add a typed, versioned optional RAW-development settings record to smart objects,
with backward-compatible defaults in the native format. Import as a smart object
only after successful sensor decoding; keep ordinary import behavior compatible
for callers that still request pixels.

Keep original source bytes immutable. Development, transforms, filters, undo, and
redo must never replace them with a decoded `.pcraft`. Cache pixels are derived
data and may be rebuilt. `.pcraft` remains the authoritative portable edit file.
PSD export needs an explicit policy: preserve embedded source where supported,
and warn when PhotoCraft-specific RAW settings cannot round-trip to Photoshop.

### 3. Render settings from sensor data

Introduce an engine path that applies RAW settings before final RGB encoding.
Cache the decoded sensor by source hash and limits; key developed results by
source, settings/version, output format/profile, and preview scale. Bound both
caches by memory, and avoid retaining full-size sensor buffers for every filmstrip
photo. Keep development off the UI thread and coalesce obsolete slider requests.

Start with as-shot/auto/manual sensor white balance, exposure, and demosaic choice.
Temperature/tint needs a calibrated mapping to camera-channel white balance; do
not reuse the existing pixel filter's relative temperature slider as if it were
an absolute sensor white balance. Audit early clipping and consider a linear float
working result so development does not discard headroom before later adjustments.

The render order is RAW development, then smart-object placement/warp, then the
existing smart filters, masks, and layer composition. Profile conversion must be
correct at the source-to-document boundary, with orientation applied once.

### 4. Expose RAW development through PhotoCraft

Add engine commands for inspecting and changing RAW development, with validation,
undo/redo, and control-channel access. Reuse Camera Raw dialog components where
practical, but give the dialog an explicit RAW-development target. Commit one
settings change on OK; Cancel leaves the document unchanged.

Route Edit Contents / double-click on a RAW-backed object to this target instead
of the generic decoded child document. Ordinary smart objects keep their current
behavior. The existing Camera Raw Filter remains available as a later pixel smart
filter; its settings must not be applied twice or mistaken for sensor settings.

The hosted editor must settle or cancel an active development dialog before a
filmstrip switch, using consistent existing modal behavior. A late worker result
must never modify a newly selected photo.

### 5. Change Image Triage's handoff

Advertise RAW-smart-object support through `app.handoff` capabilities, including
decode outcome and warnings. Add an explicit RAW-smart-object import option to
`app.open` rather than globally changing all imports. The existing scoped read
authority reads the original RAW; project/preview writes stay in the edit root.

For fresh RAWs, bypass TIFF materialization only when the selected native build
supports this workflow. Do not silently label a fallback TIFF/JPEG as RAW editing.
Show a clear unsupported-format result with an explicit developed-preview option
if needed. Keep TIFF conversion for genuinely non-RAW unsupported image formats.

Saved `.pcraft` projects remain first priority. Stash/restore and native Save use
the existing pipeline, now carrying source bytes/settings as well as cached pixels.
Thumbnail propagation continues to use the saved rendered PNG. Test the same PID
and HWND throughout RAW-to-RAW and RAW-to-normal-image navigation.

### 6. Protect existing edits

Do not automatically replace previously edited TIFF-backed projects with RAW;
their adjustments were made against a different base image. Preserve those
projects and legacy built-in edit renders exactly.

Provide a deliberate upgrade/start-from-original action later, after inspecting
which layers and operations can safely transfer. Back up the old project before
migration. Existing edits cannot be faithfully converted into RAW development
settings merely by embedding the camera file underneath them.

## Acceptance checks before promoting a build

- Synthetic DNG import embeds byte-identical camera data; exposure/WB changes
  redevelop from that data rather than the last rendered result.
- Undo/redo, Cancel, transforms, filters, source export, `.pcraft` round-trip,
  stash/restore, Save, tab close/reopen, and restart retain source and settings.
- Lower-exposure rendering matches a fresh sensor development with those settings;
  malformed input and unsupported compression fail without dropping active edits.
- Real-camera files verify calibration, color, orientation, and full resolution.
- Old pixel-based projects retain their appearance and are not silently migrated.
- Rapid navigation and slider input discard stale jobs without freezing the UI;
  24–36 MP measurements cover cold/warm open, previews, full development, stash,
  and peak memory. No cache growth proportional to the whole library.
- Existing live handoff checks still pass: window persistence, Save propagation,
  hidden startup, filmstrip/theme/fullscreen, shutdown, and process/token cleanup.
- Run affected Rust/Python tests, clippy, format/layers/wasm, command panic tests,
  and applicable format/IO corpus checks. Preserve the known-good hosted binary
  until the new build passes. Record outcomes in the handoff and local dev log.

## Session update note

Fetched upstream and fork for this research session. Upstream moved from
`bce7e54` to `e5e3e39` (release version bump from 0.4.1 to 0.5.0). It was merged
locally as `dca3aa7` before the request to keep this task to planning. Only
`Cargo.toml` and `Cargo.lock` changed; no conflicts or runtime source changes.
No fork-only changes were found. No RAW feature code, rebuild, binary promotion,
or push was performed during the planning session. The validated
`photocraft-host-0c05109.exe` remains available as the rollback build.

## Implementation update

Target cameras are Nikon Z7 and Z7 II, NEF. Their compressed NEFs require a
sensor adapter because PhotoCraft's native decoder cannot unpack them. Image
Triage uses its existing rawpy dependency to read the undeveloped Bayer plane
and metadata. It never calls `postprocess`, extracts a JPEG, subtracts black,
applies white balance, or produces RGB pixels. A small DNG container writer
preserves CFA phase, black/white levels, camera WB, calibration, orientation and
default crop. Both original NEF and sensor DNG are read through the existing
scoped automation capability and embedded separately in the smart object.
The temporary transfer is removed after import. Export Contents retains the NEF.

LibRaw's matrix direction was checked against its maintainer's
[clarification](https://www.libraw.org/node/2099): `cam_xyz` is DNG ColorMatrix,
XYZ to camera, despite the rawpy property name. No camera tables or decoder code
were copied into PhotoCraft. Its upstream RAW and Camera Raw algorithms stay intact.

Initial controls are sensor exposure, as-shot/auto/custom-channel WB, and
AHD/MHC/bilinear demosaic. Edit Contents opens a small source-development dialog;
the existing Camera Raw Filter remains downstream. Temperature/tint, additional
highlight reconstruction, HDR/linear-float output, lens profiles and migration of
older pixel-backed projects remain future work. The current native developer
still emits 16-bit ProPhoto-compatible RGB and clips within development; retaining
the sensor source does not mean every later pixel operation retains its headroom.

New modules hold most changes: `raw_development`, `raw_smart`, `raw_develop_cmds`,
`raw_develop_ui` and `photocraft_raw_source`. Shared hooks cover rendering,
Edit Contents routing, native-format fields and hosted open/stash capabilities.
Track the existing upstream watchlist each session and adopt upstream source
development if it lands rather than maintaining duplicate algorithms.

Public CC0 samples from [raw.pixls.us](https://raw.pixls.us/) cover Z7 14-bit
lossless compressed (2790), Z7 II 14-bit lossless (4213), 12-bit lossless (4214),
and 14-bit lossy compression (4215). The listing for 2790 is inconsistent; its
notes and decoded white level identify the 14-bit lossless file. Sensor samples
matched rawpy's original NEF decode element-for-element in all four. Calibration,
default crop (16,8,8256,5504), original/sensor retention and native-format
round-trip passed. Downloads match the repository's SHA-256 checksums.
Provenance and results are under
`C:/Users/ADMIN2/.codex/visualizations/2026/10/08/photocraft-raw/`.

Release observations: 24 MP ordinary import 563 ms, RAW-smart import 544 ms,
cached-sensor redevelopment 515 ms. The four 45 MP Nikon samples imported in
1050–1160 ms and redeveloped in 1953–2231 ms; peak process RSS was 2207–2371 MiB
while saving/loading/exporting. Native projects were 419–458 MB. These are
single-run observations on this machine. Final live checks are recorded in the handoff.
