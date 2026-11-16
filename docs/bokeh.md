# Bokeh

Creative tab -> **Bokeh** (a collapsible section, after Atmosphere): a depth-of-field blur laid over the photo by distance. Modelled
on Luminar Neo's Bokeh AI (Blur, Focus Distance and six Bokeh Patterns: Ring, Circle, Ellipse, Hexagon, Heart, Star; read from
[Skylum's support page](https://support.skylum.com/editing-tools/portrait-tools/bokeh-ai) and a review: ranges and defaults are not
published, so those are ours).

## Controls
- **Shape tiles:** Ring, Circle, Ellipse, Hexagon, Heart, Star: the shape of the out-of-focus lights (the blur kernel).
- **Blur** (0-100): how strong the blur is (its radius at 100 is 3 % of the picture's long side).
- **Picture:** the photo's depth (near is lighter, with contour lines) with the part that stays sharp tinted warm. **Tap it to focus
  on that place.** The first time Blur is raised, the focus is put on what is in the middle of the picture.
- **Focus Distance** (0-100): where the sharp part lies on the photo's depth, from the nearest thing to the farthest.
- **Sharp Range** (0-100): how wide a band around the focus stays sharp.
- **Advanced -> Highlights:** how much the brightest lights are lifted before they are blurred, so they become bright discs.
- The eye switches the blur off and on; the arrow removes it.

## How it works (`image_triage/bokeh.py`)
Same host-effect pattern as Light Depth and Atmosphere (`host_effects`): the settings live in the draft (`bokeh`) and in
`<photo>.photocraft.bokeh.json` beside the recipe; nothing is written into the recipe, no masks are made, nothing shows in the Mask
tab. The blur is laid over the finished render, **after the fog** (a lens blurs the fog too): the live preview worker's frames
(`raw_preview`, "before" included), a save (`raw_editor._work`, before the crop is published) and a full-size export
(`host_effects.effects_result`, before the crop and Super Resolution). The renderer is never sent it (`HOST_SETTINGS`).

Method, after Wadhwa et al. (*Synthetic Depth-of-Field with a Single-Camera Mobile Phone*, SIGGRAPH 2018), Hammon's *Practical
Post-Process Depth of Field* (GPU Gems 3) and Google's Pixel 4 Portrait Mode post:
- **Distance** is the depth map stretched to the range it uses (0 nearest .. 1 farthest). The blur radius is zero inside the sharp
  band and grows with the distance from it (a piecewise-linear circle of confusion, as production renderers use, not the physical
  formula), 1.25 times faster in front of the focus than behind it.
- **Layers, not a gather.** The picture is split into depth bands (soft weights that sum to 1; two band centres sit exactly on the
  edges of the sharp band), each band is blurred with a kernel of its own radius (FFT convolution) and the bands are composited back
  to front with "over". A blurred foreground spreads over the sharp subject; the sharp subject never smears into a blurred
  background.
- **Linear light and lit highlights:** the blur is done on linear light with the very brightest parts lifted (Highlights), then
  re-encoded. (Google blurs before tone mapping for the same reason: a light stays bright when it is spread.)
- **Grain** is put back in the blurred parts (blurring removes sensor noise and leaves a "cardboard cut-out").
- **Size:** the layers are blurred at 800 px (long side) and a bigger picture is made as `blurred part + sharp picture x sharp
  share`, so what is in focus is the full-size picture itself. A 24 MP export takes about 20 s (decoding and saving included).
- Depth maps are Light Depth's (same models, same cache).

## Not done
- Subject masks (Luminar also separates people/objects): the blur follows the depth map alone, so it is only as good as the depth
  edges (a depth map is soft at hair and thin branches).
- Foreground and background strength as separate controls; a Lightness slider (Luminar's Atmosphere has one; Bokeh's list does not).
- Optical effects: cat's-eye vignetting toward the corners, onion-ring discs, chromatic fringes on the discs.
- The Highlights lift is a simple curve; real specular points in photos have not been tried (only a synthetic light).
- Not checked by eye in the running app: the panel and the tap-to-focus picture are covered by tests and an offscreen layout only.
