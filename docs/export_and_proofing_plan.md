# Batch Export, export presets and soft proofing (plan)

Planned 2026-11-17. Decisions: the Batch window becomes **Rename + Export** (Export replaces Resize and Convert; their menu
commands open Export with that step filled in), and soft proofing lives in **both** Batch Export and the RAW editor's viewer,
with the same profiles and settings.

## What exists today

- Batch window (`BatchWindow.qml`, `batch_model.py`): rename, resize, convert. Its "preview" is a list of names and statuses.
- Handoff workflows (`workflows/models.py` `WorkflowRecipe`, `workflows/export.py`): their own resize preset, format, strip
  metadata and rename prefix/suffix.
- Edited photos export at full size through `edited_export` / `full_render` (Super Resolution and AI Denoise applied at output).
- Pillow's LittleCMS (2.19) is available for ICC conversion; no soft proofing, no output sharpening, no watermark, no presets.

## Export pipeline (one module, `export_pipeline.py`)

Per photo, in this order, the same code for the preview and for the real export:

1. **Source**: the full-size render of the edits (`edited_export`), or the file itself when it has none.
2. **Size**: preset, long edge, short edge, width x height, megapixels, percentage; "don't enlarge".
3. **Output sharpening**: Screen, Matte paper, Glossy paper; Low, Standard, High (Lightroom's set), scaled to the output size.
4. **Watermark**: text or PNG; position (9 anchors), inset, size as a share of the short edge, opacity.
5. **Colour**: output profile (sRGB, Display P3, Adobe RGB 1998, ProPhoto, or an ICC file), rendering intent (perceptual,
   relative colorimetric, saturation), black point compensation. The profile is embedded.
6. **Format**: JPEG (quality, chroma subsampling), PNG / TIFF (8 or 16 bit), WebP, AVIF when the encoder is present.
7. **Metadata**: all, all but location, copyright and contact only, none.
8. **Name and place**: the rename rules (existing planner), destination folder, subfolder per source folder, when the file exists
   (unique name, overwrite, skip).

## Export presets

A preset is the whole pipeline's settings with a name ("Web 2048 sRGB", "Instagram 1080 x 1350", "Print, Epson glossy").
Stored as JSON in the app data folder; built-in presets are read-only and can be duplicated. Handoff workflows refer to a preset
instead of keeping their own resize/format/strip/rename fields (old workflows are migrated to an equivalent preset on load).

## Soft proofing (`soft_proof.py`)

- Settings: proof profile (any output profile or printer ICC), intent, black point compensation, **Simulate paper and ink**
  (absolute colorimetric to the display, paper white and ink black visible), **Gamut warning** (colours the profile cannot hold,
  shown in a flat overlay colour).
- Built on LittleCMS proofing transforms (`ImageCms.buildProofTransform`), display profile from the system (sRGB fallback).
- **Batch Export**: the preview shows the proof of the export's own colour settings, so what is proofed is what is written.
- **RAW editor**: a Soft Proof toggle in the viewer with the same settings; editing continues on the proofed view. Applied to the
  displayed frame only (the render pipeline is unchanged).

## Result preview (all Batch modes)

A medium preview pane beside the settings:
- The selected photo as it will be written (size, sharpening, watermark, colour, proof), before/after toggle, fit and 100%.
- Arrow keys and the list step through the batch; hovering a row follows it.
- Final pixel size, estimated file size (encoded at preview resolution and scaled), and any per-photo problem.
- Rendered at preview size through the same pipeline, on a worker thread, cancelled when settings change; full-size renders of
  edited photos are cached so stepping back is instant.
- Rename shows the picture too, with the new name.

## Phases

1. Export pipeline + Export mode (size, format, colour, metadata, name, destination) replacing Resize/Convert, with the preview
   pane. Presets (save, load, built-ins).
2. Output sharpening and watermark.
3. Soft proofing in Batch Export (proof, paper simulation, gamut warning).
4. Soft proof toggle in the RAW editor viewer.
5. Handoff workflows on presets (with migration).

## Tests

Pipeline steps on synthetic images (sizes, ICC round trips, gamut mask for a saturated patch, sharpening only at output size,
watermark placement); preset save/load/migration; preview equals the written file (pixel compare at preview size); Batch window
QML (modes, preview stepping); proofing toggle in the editor.
