# Sky Replacement

Creative tab -> **Sky Replacement**. The photo's sky is swapped for a picture from the sky library by PhotoCraft's renderer
(`crates/algo/src/camera_raw/sky.rs`, the recipe's `cameraRaw.sky`); the host finds the sky (and the water and the people) with
the scene model and writes them, as raster masks, with the sliders. Modelled on Luminar Neo's Sky AI
([Skylum's Sky AI page](https://support.skylum.com/editing-tools/landscape-tools/sky-ai)); Skylum publishes no ranges or defaults,
so those are ours.

## Controls
- **Sky library** (cards, + Add sky; right-click a card to remove it).
- **Amount, Fade Edge, Shift Edge, Match Colors, Relight Scene**: the blend (Relight Scene is how much the change in the
  sky's light changes the scene's exposure, strongest beside the sky).
- **Advanced Settings**, in Luminar's groups (each folds away):
  - **Sky Orientation**: *Horizon Position* moves the new sky's horizon (the bottom of the sky picture) below or above the photo's
    own horizon, which is found from the matte. *Vertical Position* pans the sky picture up (a higher part of it shows) or
    lowers it below the horizon; *Horizontal Position* pans it sideways (the picture is enlarged as it is panned so its edge never
    shows). *Sky Size*, *Flip*.
  - **Mask Refinement**: *Global* lets more of the new sky into areas that are only partly sky (haze at the horizon, thin
    branches). *Close Gaps* fills the holes the matte left where the old sky still shows through near the sky (between branches,
    through wires and arches): close to the sky, pixels with the old sky's colour and brightness become sky. *Fix Details* fits
    the edge more tightly to fine detail (a second, tighter guided filter) and clears small specks.
  - **Scene Relighting**: *Relight Saturation* is how much of the sky's colour comes with it
    (50 = its own colour, 0 = only its brightness). People are left out of the scene's relight; *Relight Human* relights them on
    their own.
  - **Reflection**: *Reflection Amount* reflects the new sky in the water in place of the old sky's reflection (each column is
    mirrored about its shoreline; the water's ripples and colour stay). *Water Blur* softens the reflection for rippled water.
  - **Sky Adjustments**: *Defocus* (to match a shallow depth of field), *Grain* (to match the photo's), *Atmospheric Haze*
    (thickest at the new sky's horizon), *Warmth* and *Brightness* of the new sky.

## How it works
- **Masks:** one run of the scene model (OneFormer, ADE20k) finds the sky, the water and the people; each is brought to the
  photo's size, snapped to its edges with a colour-guided filter and kept at 1536 px as a raster mask. The matte is always in the
  recipe; the water only while Reflection is used, the people only while a relight is. A photo saved before these existed finds
  them the first time a setting needs them. A photo with no water (or no people) says so and the job is not run again.
- **Compatibility:** the newer settings (`sky_replace.NEWER`) are written to the recipe only when they are not at their defaults,
  so a photo that does not use them still renders in an older renderer. The preview worker reports `"sky": 2` when it knows them;
  an older worker refuses those settings with a message instead of ignoring them.
- **Order in the renderer:** after retouch and red eye, before local masks (as before). The sky is laid, toned (brightness,
  warmth, match), adjusted (defocus, haze, grain); the scene is relit; the reflection is added to the water; then the sky is
  composited through the matte.
- All distances are a share of the frame, so a preview matches the full-size render.

## Not done
- Luminar's Masking tab for Sky AI (brush, gradients, colour and luminosity ranges on the sky's own mask): the Mask tab can mask
  any adjustment, but not the sky's matte itself.
- Rotation of the sky picture.
- Reflection uses the water the scene model finds; a puddle or a window it misses gets none.
