# Light Depth

A light placed inside the scene, in the in-app RAW editor (Adjust › Light Depth). Modelled on the tool of the same
name in Luminar: Amount, a depth pad with a draggable light, Softness, Warmth, and Advanced Settings (Distance, Reach,
Focus).

## How it works

1. The first time the section is used on a photo, the developed preview is sent to the depth model
   (Depth Anything V2 Small, the existing "Depth" model in Set Up AI) on the warm mask-engine host. The map is kept
   in the managed depth cache, keyed by the photo's path, size and date, so it is made once per photo.
2. The pad shows that map (near is bright). Dragging the handle puts the light on a point of the photo; the light
   takes that point's distance in the scene.
3. The light's reach is turned into the coverage of an ordinary Camera Raw local mask named **Light Depth**:
   fully lit at the light's distance, fading nearer and farther (Reach), optionally narrowed to a pool around the
   handle (Focus), spilling across edges (Softness). Amount is a local Exposure of up to +2 stops and Warmth a local
   Temperature.

Because the result is a normal mask, the native renderer, export, full-resolution renders and the hosted editor
need no change, and the mask can be refined in the Mask tab (subtract a brush stroke, intersect with Subject, add
other local adjustments). Light Depth only replaces the mask's base coverage and its Exposure and Temperature.
Renaming the mask hands it over as an ordinary mask; Light Depth then starts a new one.

## Where things are kept

- `image_triage/light_depth.py`: the arithmetic (depth map and settings in, coverage out), no UI.
- `image_triage/qml_shell/raw_light_depth.py`: the section's model (depth job, undo steps, pad picture).
- `image_triage/qml_shell/qml/RawLightDepth.qml`: the section.
- The slider positions travel in the draft as `lightDepth`, so undo and redo restore them. They are host settings:
  they are removed before anything is sent to the native renderer or written to the recipe, and are saved beside
  the recipe as `<photo>.photocraft.lightdepth.json`, which moves, renames and deletes with the photo's other edit
  files.

## Limits and what has not been checked

- Coverage is authored at 640 pixels on the long edge and scaled by the renderer. Light is smooth, so this is not
  visible as softness in the picture, but the light cannot follow edges finer than the depth map itself.
- Verified with a stand-in depth map and offscreen rendering of the section. Not yet run with the real depth
  model, the native RAW preview helper or on Windows.
