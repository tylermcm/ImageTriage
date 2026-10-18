# PhotoCraft RAW smart object: plan

Status: plan only, 2026-10-07. Nothing is implemented. Decision made by the user: do the
smart-object route rather than "develop in Image Triage and hand over pixels", to avoid doing
it twice. Companion to `docs/photocraft_fork_strategy.md`. Code references are to stock
PhotoCraft `main` in the `photocraft` repo, found by reading, not by running.

## 1. Why, and what the problem actually is

Stock PhotoCraft bakes a RAW at open, with fixed defaults, into a 16-bit ProPhoto pixel
document (`crates/io/src/lib.rs:176` -> `raw.rs:46`). After that no RAW-domain control exists.
Photoshop's real advantage is not the Camera Raw *filter* (that also works on baked pixels) but
that a RAW opened as a smart object keeps the RAW and can be re-developed at any time, with
the filter stack re-applied on top.

PhotoCraft already has most of the machinery:

- `SmartObject.source` can embed or link arbitrary bytes (`crates/doc/src/lib.rs:343,390`).
- Every consumer sniffs the format, and `import` already routes RAW bytes to `import_raw`
  (`smart_cmds.rs:66` -> `io/lib.rs:176`). A RAW smart object **already renders today**, but only
  with default develop settings and with no way to change them.
- Re-render order is decode -> place (transform/warp) -> smart filters (`smart_cmds.rs:296-308`),
  so re-developing and re-rendering re-applies the whole filter stack for free.
- `photocraft_raw` is a pure function (`develop`, plus `decode` + `develop_sensor` for decode-once),
  with options for demosaic, white balance (as shot / auto / multipliers) and EV.
- The pref `rawDefaults.openAsSmartObject` exists (`prefs.rs:677`) but is read by nothing and sits
  in the hidden-until-implemented list (`prefs.rs:854`).
- `roadmap.md:149` lists "RAW develop" under M10 "Smart features", and no issue or doc text
  describes a RAW smart object, so upstream seems not to have started it.

## 2. What must be built

| # | Piece | Where | Notes |
|---|---|---|---|
| 1 | Develop settings on the smart object | `doc/src/lib.rs` `SmartObject` | New `raw: Option<RawDevelop>`: plain data (demosaic id, white balance mode, multipliers, EV, schema version). `DevelopOptions` has no serde, so the doc crate holds a mirror struct and the engine converts. |
| 2 | Persistence | `format/src/manifest.rs:281` `ContentM::Smart`, `convert.rs:147` write, `:425` read | New field with `#[serde(default)]`, like `warp` / `stack_mode`. Old files load unchanged. |
| 3 | Render branch | `engine/src/smart_cmds.rs` `render` | If `raw` is set, develop with the stored options instead of `decode_source`. |
| 4 | Decode cache key | `smart_cmds.rs:99,109-113` | Key is `(blake3(bytes), PixelFormat)` today with no options. Must include the options (the `stack_image` suffix trick at `:177` is the precedent). Cache the decoded `Sensor` separately from the developed image, so a white-balance or EV change skips decode. |
| 5 | Command | `smart_cmds.rs::specs()` (`:878`) | e.g. `layer.smartObjects.rawDevelop {layer, demosaic, whiteBalance, exposure}`: `s.edit` + `refresh_or_fail`. One undo step per change. Needs `panic_hunt` coverage and params docs. |
| 6 | Open flow | `file_cmds.rs:313` `open_bytes_as` | If the pref (or an explicit parameter) says so, build a new **16-bit ProPhoto** host document sized to the developed output, with one smart layer whose source is the RAW. Un-hide the pref and make `prefs_usage` pass by actually reading it. |
| 7 | Edit Contents guard | `smart_cmds.rs:645,664,691` | For a RAW source, "Edit Contents" must open the develop dialog, **never** the default-developed child document: `commit_child` would replace the RAW source with an embedded `.pcraft` and destroy it. Also guards `saveContents` (`:888`) and `on_close`. |
| 8 | Develop dialog | new `ui-egui/src/raw_develop_ui.rs` | Same pattern as `camera_raw_ui.rs`: `menu()` + `show()`, one line each in `menus.rs` (near `:178`) and `lib.rs:1066`, menu entry in `menu_catalog.rs`. Live preview from a proxy. |
| 9 | Unsupported RAWs | `io/raw.rs:46` | Nikon compressed NEF, CR3, RAF, X-Trans fall back to the embedded 8-bit JPEG preview. A "RAW smart object" must not silently be a JPEG: record and show "preview only, develop settings unavailable". |
| 10 | Tests | see section 6 | |

## 3. Decisions to make

1. **Embed or link the RAW?**
   - *Embedded* makes the `.pcraft` self-contained, but every save writes the full RAW blob
     (30-60 MB) into the project. For thousands of culled photos, in the hidden
     `.image_triage_edits` folder, that doubles the library on disk.
   - *Linked* keeps the project small but breaks when the original is moved or renamed, and the
     control channel's path rules (capability-scoped, relative to a root) apply.
   - Recommendation: support both in PhotoCraft (it already does); **Image Triage uses Linked**,
     because it owns the file locations and already tracks renames. Needs a relink story.
2. **Proxy rendering.** There is no partial-resolution develop in `photocraft_raw`, and AHD is
   slow. 24 MP times are not documented anywhere. Measure first (phase 0). Likely need a fast
   interactive path (bilinear or Mhc on a downscaled or cropped region) that falls back to the full
   develop when you release the slider.
3. **Colour.** The smart object cache takes the host document's pixel format
   (`doc.pixel_format()`), and render does no ICC conversion. A ProPhoto RAW inside an sRGB host
   would be reinterpreted wrongly. Rule: the RAW host document is always 16-bit ProPhoto; refuse
   or convert on place into any other host.
4. **PSD export.** The RAW goes into `lnk2` verbatim (Photoshop would open it as a Camera Raw
   smart object), but our develop settings are lost, and `source_geometry` runs a full develop
   just to read the size (`psd_export.rs:187`). Low priority; fix the double develop, map settings
   later.
5. **Memory.** The embedded `Arc` is shared across history states, so undo is cheap. The costs are
   the 16-bit cache (~144 MB per history step at 24 MP) and the decode cache (1 GiB cap).

## 4. Features you listed that this does not give you yet

The RAW smart object makes **WB, EV, demosaic** re-developable. `photocraft_raw` has none of the
following today, so they are separate work, ideally as new develop stages in the RAW crate with
options added to `RawDevelop`:

- RAW-domain **denoise** (before demosaic).
- **Highlight reconstruction**. The develop step clips white balance to 1 so blown highlights stay
  white.
- RAW-domain **super resolution** (see `project_super_resolution_direction`: non-generative, 2x,
  fidelity-gated; previous model candidates were rejected).
- **Masking** that works against RAW data. Image Triage's AI masks (scene regions, subject) are
  computed on the image; they would be applied as selections/masks on the smart layer. See
  `select.fromMask` in the fork strategy doc.

The existing `filter.cameraRaw` smart filter stays a pixel filter after the develop step.

## 5. Phases

0. **Measure and ask.** Time the `rawinfo` example on real 24 MP files from your cameras (decode,
   develop per demosaic). Check which of your cameras fall in the unsupported list. Open an
   upstream issue describing the RAW smart object, to avoid duplicate work and agree the shape.
1. **Data model.** Items 1-4 and 9, plus tests. No UI, no open flow. Must keep old `.pcraft` files
   loading and pass `cargo xtask layers`, `cargo xtask wasm` and `panic_hunt`.
2. **Command and open flow.** Items 5-7. At this point Image Triage can open a RAW straight into a
   re-developable document through `app.open`, and settings can be driven over the control channel.
3. **Develop dialog.** Item 8, with the proxy path.
4. **Image Triage integration.** Remove the RAW -> scratch TIFF step from `photocraft_bridge.py`
   (the RAW opens directly, linked), keep `.pcraft` + `.photocraft.png` as the saved outputs,
   un-hide nothing on our side. Update the live check
   (`scripts/check_photocraft_handoff.py`) with a RAW fixture. Surface "preview only" files.
5. **RAW-domain stages** from section 4, one at a time.
6. **PSD settings mapping** (optional).

## 6. Tests to write

Using `photocraft_raw::testgen::{DngSpec, mosaic, scene}` (`engine/src/file_cmds/tests.rs:52` is
the pattern) and the existing smart-object tests (`smart_cmds/tests.rs`):

- `.pcraft` round trip keeps `raw` settings and the RAW source; an old file loads with `raw = None`.
- Changing exposure changes pixels; changing it back restores them exactly (the develop is pure).
- Smart filters re-apply after a re-develop.
- Decode count (`source_decode_count`, `smart_cmds.rs:140`): a white-balance change does not
  decode the RAW again.
- "Edit Contents" on a RAW smart object never calls `commit_child`; the source stays the RAW.
- Unsupported RAW is flagged as preview-only.
- `every_preference_is_read_or_hidden` passes with the pref un-hidden.
- Hostile / truncated RAW with `raw` set does not panic.
- Mutation-check the cache key and the Edit Contents guard.

## 7. Conflict surface (what touches upstream files)

New files: the develop dialog, the RAW render helper, tests. Edits to upstream files, all small:
`SmartObject` field, manifest + convert (both directions), `render` branch, cache key, command
registration, prefs un-hide, one line in each of `menus.rs`, `lib.rs`, `menu_catalog.rs`. Record
each in `PATCHES.md`. This is a moderate, concentrated surface, and if upstream accepts it, all
of it drops out of the fork.

## 8. Order of work

The hosted-mode patch (stash / bind / replace / theme) is on the work PC and **must be recovered
and pushed first**: this plan builds on `app.open`, and Image Triage cannot use stock PhotoCraft
without it. Then phase 0 (measure + issue), then phase 1.
