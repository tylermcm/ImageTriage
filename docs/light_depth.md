# Light Depth

Light a photo near and far apart, in the in-app RAW editor (Adjust › Light Depth). Modelled on the tool of the same name in
Luminar Neo: the effect belongs to its own panel, shows nowhere else, is adjusted in the same panel, and is applied live.

## What the person sees

Light Depth works like Luminar's (checked in Luminar Neo on 2026-11-15): it changes what is **nearer** and what is
**farther** than a chosen depth, and leaves what lies *at* that depth as it is. Amount alone adds no light there. With the
default Brightness of -50 on both sides, the photo darkens around the subject and the subject stands out.

- **Amount** (0-100): how strongly the effect is applied. At 0 nothing happens and nothing is drawn on the photo.
- **Depth**: the distance that is left as it is. Set it by clicking the photo with the dropper (the same way Point Color picks
  a colour; Esc cancels), with the Depth slider (0 = nearest, 100 = farthest), or by dragging up and down in the depth
  picture. The depth picture is a secondary view: the photo's depth map (near is bright) with what lies at the chosen depth
  tinted amber.
- **Softness** (0-100, default 10): how far the zones spill across object edges.
- **Warmth** (-100...100): colour temperature of the whole effect, applied to every zone, the depth included.
- **Advanced Settings**: **Brightness Near/Far** (-100...100, default -50) and **Warmth Near/Far** (-100...100, default 0),
  which change what is nearer than, or beyond, the depth; plus **Size** and **Wrap** (0-100).
- **On the photo**, while the section is open and Amount is above zero: a *position* handle (the centre of a pool), a *size*
  handle on a ring (the pool's radius; at Size 100 there is no ring and the effect covers the whole photo) and a *wrap*
  handle on a short track under the position handle. Dragging them changes the same settings as the sliders, as one undo step
  per drag.
- The eye switches the whole effect off and on without losing its settings; the arrow removes it.

Size and Wrap are this tool's own additions (Luminar has no equivalents). **Position and size** narrow the effect to a pool
around the handle; **Wrap** is the thickness of the slab around the chosen depth that is left alone.

## How it works

1. The first time the section is used on a photo, the developed preview is sent to the depth model (Depth Anything V2
   Small, the "Depth" model in Set Up AI) on the warm mask-engine host. The map is kept in the managed depth cache, keyed by
   the photo's path, size and date, so it is made once per photo.
2. The draft holds only the section's settings, `lightDepth`. **There is no mask in the draft and none in the saved recipe,
   so nothing about Light Depth appears in the Mask tab.**
3. When a render is requested (preview, save, export) the settings and the cached depth map are turned into up to three
   ordinary Camera Raw local masks (`light_depth.zones`, `light_depth.render_masks`): **near** (nearer than the slab) and
   **far** (beyond it), each with exposure = Amount x its Brightness and temperature = Amount x (Warmth + its Warmth), and a
   **depth** mask for the slab itself, which only gets the overall Warmth's temperature. A zone that changes nothing gets no
   mask, so by default two masks are used. The three zones add up to the whole photo. The masks are appended after the
   person's own, only in what is sent to the native renderer (`light_depth.with_masks`), and cached for the last few settings.
4. The live preview helper (`raw_preview.PreviewWorker`) builds them the same way, for the draft and for the saved picture.
5. Because the renderer only ever sees ordinary masks, it, the full-resolution render and export need no change.

Where the masks are built: `raw_preview.PreviewWorker` (the fast live preview), `raw_editor._recipe(settings, image_path)`
(the slower per-edit preview), the save path in `RawEditor._work` (the saved recipe is written from the draft *without*
them), and `full_render.FullRenderService._request` (export: `light_depth.recipe_for_render`, which also treats a photo whose
only edit is Light Depth as edited).

## Where things are kept

- `image_triage/light_depth.py`: the arithmetic (depth map and settings in, coverage out), the render-time masks, the legacy
  mask filter and the settings file. No UI.
- `image_triage/qml_shell/raw_light_depth.py`: the section's model (depth job, picking, undo steps, pad picture).
- `image_triage/qml_shell/qml/RawLightDepth.qml`: the section. `RawLightDepthHandles.qml`: the handles drawn on the photo
  (placed by `Viewer.qml`, with the dropper's click routed the way Point Color's is).
- The settings travel in the draft as `lightDepth`, so undo and redo restore them. They are host settings: removed before
  anything is sent to the native renderer or written to the recipe, and saved beside the recipe as
  `<photo>.photocraft.lightdepth.json` (version 2) while Amount is above zero. The file moves, renames and deletes with the
  photo's other edit files.

## Photos edited with the first version

The first version stored the effect as an ordinary mask named "Light Depth" in the recipe. On load that mask is removed from
the photo's masks and never shown in the Mask tab (a stored draft is filtered the same way, and so is the recipe compared
against when saving). If a version-1 settings file sits beside it, the effect is **converted** and carries on as the panel's
effect (approximately: the old light brightened what lay at its depth, which this model cannot express, so its depth, reach and
focus carry over and the near and far zones get the defaults). Without the settings the old mask is **dropped**: its settings are unknown and it was only a raster. Anything the
person added to that mask in the Mask tab (subtracted strokes, other adjustments) is lost. Version-1 settings beside a recipe
that no longer has the mask are ignored. A mask the person made themselves and named "Light Depth" is treated the same way,
which is the price of recognising the old ones by name.

## Limits and what has not been checked

- The effect needs the depth map in the managed cache whenever it is rendered. If the cache file is gone, preview, save and
  export fail with "Light Depth's depth map is missing. Open Light Depth to make it again." rather than rendering without it.
- Each zone that changes takes one of a photo's 16 mask slots (two by default, three with an overall Warmth); with too few
  free, Light Depth says so.
- Coverage is authored at the depth map's own size (1024 pixels on the long edge) and scaled by the renderer, and is only
  lightly smoothed, so the effect follows the objects the map shows (trees, grass) rather than the rows they stand on. It
  cannot follow edges finer than the depth map itself, and in a scene dominated by a ground plane a slab of depth is naturally
  a band across the ground.
- Clicks and handles use the picture as the editor shows it. With a host crop applied, the correspondence between the shown
  picture and the depth map has not been checked.
- Other places that decide whether a photo "has edits" from its recipe alone (badges, sync) do not know about Light Depth's
  settings file; only export was changed.
- The Windows run of the installed depth model and the paired native RAW preview helper from 2026-11-13 predates this design;
  the near/far masks have not been rendered by the native helper yet.
