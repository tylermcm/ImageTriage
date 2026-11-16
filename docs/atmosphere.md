# Atmosphere

Creative tab -> **Atmosphere** (between Light Depth and Sky Replacement): fog laid over the photo by distance. Modelled on
Luminar Neo's Atmosphere AI, checked side by side with Luminar itself on the same photo (Wispy, Heavy and Ground Fog, the Amount
slider and the Clouds Drift pad).

## Controls
- **Presets:** Wispy Fog, Heavy Fog, Ground Fog. A tile sets Amount, Height, Patchiness, Cloud Size, Vibrance and Depth in one go
  (and switches the fog on), as Luminar's do (Heavy Fog is a low Amount over the whole frame, Ground Fog a full one low down).
  Moving any of those sliders afterwards clears the highlight.
- **Amount** (0-100): how thick the fog is.
- **Picture:** where the fog lies on the photo (the photo's depth, with the fog white). It follows every slider.
- **Clouds Drift** (pad): slides the patches of fog about. The fog stays the same; it only lies elsewhere.
- **Height** (0-100): how far above the ground the fog reaches (100 = all of the frame, the sky too; low values keep it to a
  bank lying on the ground).
- **Advanced:** **Patchiness** (even fog to broken clumps), **Cloud Size** (size of the clumps), **Vibrance** (how much of the
  photo's own colour the fog takes; 50 is nearly a neutral cool grey-blue fog), **Depth** (how far from the camera the fog
  begins: raise it to keep the foreground clear).
- The eye switches the fog off and on; the arrow removes it.

## How it works
The settings live in the draft (`atmosphere`) and in `<photo>.photocraft.atmosphere.json` beside the recipe; nothing is written
into the recipe, no masks are made for it and nothing shows in the Mask tab (it takes none of the photo's 16 masks).

The fog is laid over the **finished render** by the host (`atmosphere.composite`), because the renderer cannot blend toward a
colour:

- **Density** (`atmosphere.density`): rises with distance from the depth map (from the Depth setting on); held by Height to a
  band above the *ground line* (`_above_ground`: the ground at a given distance lies at the row where that distance is typical,
  so Ground Fog is a bank across the far ground and not the bottom edge of the picture; the sky and what stands up are above
  it); broken into horizontally stretched patches by six octaves of smooth noise seeded from the photo's depth map (the same every
  render; Drift slides the window over a larger field of the same noise).
- **The blend:** each pixel goes toward the fog colour by `1 - exp(-5.5 x Amount x density)`. The fog colour is a cool grey-blue,
  as bright as the photo is there (the photo blurred to ~48 px) with some of the light's own colour (Vibrance), so distant things
  disappear into it, dark ground lifts to the fog's grey, and the near foreground stays dark.
- **Where it is applied:** the live preview worker's frames (`raw_preview`, "before" included), a save (`raw_editor._work`, on the
  full render before it is cropped and published) and a full-size export (`host_effects.fog_result`, before the crop and Super
  Resolution). The renderer is never sent the fog (`HOST_SETTINGS`). Full-size pictures are done in strips.
- **Depth maps** are Light Depth's (the same models, the same cache), so a photo that has one has the other. Large is preferred,
  then Base, then Small, whichever is installed.

## Not done
- Light-aware fog (Luminar's fog is lit by suns, street lights and headlights): it would need a brightness map of the photo, kept
  beside the depth map. Today the fog's brightness follows the photo blurred, not point lights.
- Subject protection beyond what the depth gives (Luminar also uses content-aware masks to keep fog off people and trees).
- Luminar's wisps have finer structure than a few octaves of value noise give; a flow-noise or warped-noise field would be closer.
- Not wired into the live preview's other consumers (thumbnails of unsaved edits, the hosted editor's own view).
- Base and Large depth models are CC-BY-NC; a commercial release must default to Small (see the licence audit).
