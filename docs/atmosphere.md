# Atmosphere

Creative tab -> **Atmosphere** (between Light Depth and Sky Replacement): fog laid over the photo by distance. Modelled on the
improved Atmosphere AI of Luminar Neo 1.29 (read from Skylum's support page and a review; Luminar itself was not run).

## Controls
- **Presets:** Wispy Fog, Heavy Fog, Ground Fog. A tile sets Amount, Height, Patchiness, Cloud Size, Vibrance and Depth in one go
  (and switches the fog on). Moving any of those sliders afterwards clears the highlight.
- **Amount** (0-100): how strongly the fog is applied.
- **Picture:** where the fog lies on the photo (the photo's depth, with the fog white). It follows every slider.
- **Clouds Drift** (pad): slides the patches of fog about. The fog stays the same; it only lies elsewhere.
- **Height** (0-100): how far up the frame the fog reaches (100 = all of it; low values keep it to the ground).
- **Advanced:** **Patchiness** (even fog to broken clumps), **Cloud Size** (size of the clumps), **Vibrance** (how much of the
  photo's own colour the fog keeps; 50 is a neutral grey fog), **Depth** (how far from the camera the fog begins: raise it to keep
  the foreground clear).
- The eye switches the fog off and on; the arrow removes it.

## How it works
Same pattern as Light Depth (`docs/light_depth.md`). The settings live in the draft (`atmosphere`) and in
`<photo>.photocraft.atmosphere.json` beside the recipe; nothing is written into the recipe's masks and nothing shows in the Mask
tab. `host_effects.with_masks` turns the settings of every host effect into the masks the renderer gets, for the live preview
worker, saves and exports alike (Light Depth first, then Atmosphere).

- **Density** (`atmosphere.density`): rises with distance from the depth map, from the Depth setting on; held to the lower part
  of the frame by Height (with a soft top); broken into patches by smooth noise (four octaves, seeded from the photo's own depth
  map so it is the same every render; Drift slides the window over a larger field of the same noise).
- **The look:** the renderer cannot blend a pixel toward a colour, so the fog is one local mask whose coverage is the density
  and whose adjustment is negative Dehaze, a lift of exposure, blacks and shadows, less contrast, clarity and texture, and less
  colour. Because it acts on the photo's pixels, the fog keeps the photo's colour and light.
- **Depth maps** are Light Depth's (the same models, the same cache), so a photo that has one has the other. Large is preferred,
  then Base, then Small, whichever is installed.
- **Masks:** one. The budget (16) counts the person's masks, Light Depth's and Atmosphere's together.

## Not done (needs the engine, or more work)
- True fog colour: a blend toward a colour by density needs a small `fog` stage in PhotoCraft (the same shape as the sky stage);
  see `creative_tools_plan.md`. Until then the fog is the milky, low-contrast look a local adjustment can make.
- Light-aware fog (Luminar's fog is lit by suns, street lights and headlights): it would need a brightness map of the photo, kept
  beside the depth map.
- Subject protection beyond what the depth gives (Luminar also uses content-aware masks to keep fog off people and trees).
- Base and Large depth models are CC-BY-NC; a commercial release must default to Small (see the licence audit).
