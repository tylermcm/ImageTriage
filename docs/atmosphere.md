# Atmosphere

Creative tab -> **Atmosphere** (between Light Depth and Sky Replacement): fog laid over the photo by distance. Modelled on
Luminar Neo's Atmosphere AI, checked side by side with Luminar itself on the same photo (Wispy, Heavy and Ground Fog, the Amount
slider and the Clouds Drift pad).

## Controls
- **Presets:** Wispy Fog, Heavy Fog, Ground Fog. A tile sets Amount, Height, Patchiness, Cloud Size, Vibrance and Depth in one go
  (and switches the fog on), as Luminar's do (Heavy Fog is a low Amount over the whole frame, Ground Fog a full one low down).
  Moving any of those sliders afterwards clears the highlight.
- **Amount** (0-100): the fog's brightness (the shape does not change with it).
- **Picture:** where the fog lies on the photo (the photo's depth, with the fog white). It follows every slider.
- **Clouds Drift** (pad): slides the patches of fog about. The fog stays the same; it only lies elsewhere.
- **Height** (0-100): how far above the ground the fog reaches. The orange marker on the picture is this slider: bottom edge = 0, top = 100.
- **Advanced:** **Patchiness** (even fog to broken clumps), **Cloud Size** (size of the clumps), **Vibrance** (how much of the
  photo's own colour the fog takes; 50 is nearly a neutral cool grey-blue fog), **Depth** (how far from the camera the fog
  begins: raise it to keep the foreground clear).
- The eye switches the fog off and on; the arrow removes it.

## How it works
The settings live in the draft (`atmosphere`) and in `<photo>.photocraft.atmosphere.json` beside the recipe; nothing is written
into the recipe, no masks are made for it and nothing shows in the Mask tab (it takes none of the photo's 16 masks).

The fog is laid over the **finished render** by the host (`atmosphere.composite`), because the renderer cannot blend toward a
colour:

- **The fog is a volume, not a pattern on the picture** (`atmosphere.volume`). An earlier version textured the picture with 2D noise
  steered by the depth map; it stretched along the ground and stood up like a wall where the depth jumps. The fog is now what
  volumetric fog is in a renderer ([Quilez, "Better fog"](https://iquilezles.org/articles/fog/); Wronski's volumetric fog,
  SIGGRAPH 2014): a density in 3D space, thickest at the ground and thinning exponentially with height (Height is how far up),
  broken into puffs by 3D billow noise (Cloud Size is their size, Patchiness how much of the space is empty, Drift slides the noise
  through the volume), added up along each pixel's view ray as far as the surface that pixel shows. Marched in 28 planes parallel
  to the picture, each pixel sampling a jittered position in its slice, then smoothed; worked out at 288 px and scaled up.
- **Distance** is not known in metres, so it is read from the picture's own geometry: for a camera at height 1 over flat ground the
  ground at row `y` is at distance `focal / (y - horizon)`, and `_ground` finds the row each pixel's depth corresponds to (the rows'
  median depth), so trees, hills and the sky are at the distance of the ground line they stand on (the sky and far hills at the far
  limit). Depth start fades the fog in from a distance; Amount is the fog's brightness only.
- **The blend:** each pixel goes toward the fog colour by `0.95 x Amount x density`. The fog colour is a cool grey-blue,
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
- **Structure is too smooth.** Adding many planes along a ray averages the puffs out, so the fog reads as a smooth mist layer where Luminar's has defined cumulus. Needs sparser, denser puffs (or a few thick sprites per ray), internal lighting (a light direction marched through the volume), and a finer detail pass at full size.
- Not wired into the live preview's other consumers (thumbnails of unsaved edits, the hosted editor's own view).
- Base and Large depth models are CC-BY-NC; a commercial release must default to Small (see the licence audit).
