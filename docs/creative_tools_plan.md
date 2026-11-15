# Creative tools plan

Planned 2026-11-15. Six tools for the RAW editor, chosen from a comparison with Luminar Neo's tool list (read from the app) and
what our editor and the PhotoCraft engine already do. Effort figures are rough working estimates, not commitments.

| # | Tool | What the person gets | Needs engine change | Rough effort |
|---|---|---|---|---|
| 1 | Auto tone and white balance | One click: exposure, contrast, tonal sliders and white balance set from the photo | No | 2-3 days |
| 2 | Edge refinement for AI masks | AI masks in the Mask tab hug the subject, no soft halo | Phase 2 only (full-size accuracy) | 1-2 days, then about 3 |
| 3 | Atmosphere | Depth-based haze, mist and fog: far layers lighter, cooler, softer | Phase 2 only (true fog colour) | 3-4 days, then about 2 |
| 4 | Region enhancers | Sliders per sky, water, foliage, mountains, buildings | No | 4-6 days |
| 5 | Match Look | Take the colour and tone of a reference photo | No | 3-4 days |
| 6 | AI Erase | Click an object, remove it, fill the gap | Yes (a patch stage) | spike 2 days, then 2-3 weeks |

## Shared ground work (do first, about 3 days)

Light Depth and Sky Replacement each grew their own copy of the same plumbing. Three more tools would copy it three more times.

1. **Host effects.** One module that turns a tool's saved settings, plus its cached maps, into the recipe masks a render needs
   (`light_depth.with_masks` is the template). The renderer, the live preview worker, saves and full-size exports all call it.
   Settings live in the draft and in a small file beside the recipe; nothing is written into the recipe's masks.
2. **Mask budget.** A photo holds at most 16 masks and every tool spends some (Light Depth 2-3, Atmosphere 1-2, region
   enhancers up to 5). The budget has to be counted in one place and refused with a clear message. Decision needed: raise the
   limit in PhotoCraft (a one-line engine change, and a rebuild of the worker and the hosted editor), or combine zones that
   have the same adjustment.
3. **Matte refinement.** Move `sky_replace.refine_matte` (colour-guided filter) into a shared module. Tools 2 and 4 use it.
4. **Region cache.** The landscape model finds every region (sky, water, mountains, vegetation, buildings, animals) in one
   run (about 8 s the first time, then cached per photo). Cache each region's refined matte in the managed cache, as the depth
   maps already are.
5. **Real paths.** Anything the native renderer reads must sit under `~/.image-triage`, never under the app data folder
   (Microsoft Store Python virtualizes that one; see the sky library fix).

## 1. Auto tone and white balance

- **White balance:** estimate the light's colour from near-neutral, unclipped pixels (grey-edge or shades-of-grey) in the
  developed preview, then convert to our temperature and tint with the existing `_white_balance_from_image` path the
  white-balance dropper already uses. Leave the camera's balance alone unless there is a clear cast.
- **Tone:** set exposure so the median sits near mid-grey without clipping the highlights, set highlights and shadows from the
  percentile spread, set whites and blacks to clip about 0.2%, and add a little vibrance.
- **UI:** Auto Tone, Auto WB and Auto buttons, plus a strength slider. One undo step. Available in sync, so a whole batch of
  similar frames can be auto-edited the same way.
- **Tests:** synthetic casts and exposure shifts are corrected; clipping stays below the target; results stay in range.
- **Later:** replace the rules with a learned style profile (see the AI editing notes).

## 2. Edge refinement for AI masks

- **Phase 1 (no engine change):** when an AI mask (subject, sky, background, object, scene region) arrives, bring it to the
  preview size and snap it to the photo's edges with the shared colour-guided filter, then write it at 1536 px. Add Edge
  controls to the mask options: Refine on/off, Feather, Shift. This is the fix that stopped the sky edge looking blocky and
  misty.
- **Phase 2 (engine):** a `refine` option on raster masks so the renderer snaps the mask at full size, where trees and hair
  are finest. Needs a PhotoCraft schema change, a worker rebuild (about 2 minutes) and a hosted editor rebuild (about 10).
- **Limits:** a 3 px tree the model lost completely cannot be recovered; 5-8 px ones can.
- **Tests:** the synthetic horizon-and-tree matte used for the sky, plus a real-photo comparison.

## 3. Atmosphere

- **Idea:** the depth map already gives every pixel a distance. Atmosphere is one continuous mask, strongest far away, that
  applies negative Dehaze, a little exposure and less saturation and contrast, with a cool or warm tilt. Sliders: Amount, Haze,
  Mist (softness), Warmth, and a Depth start so the nearest layers stay clean.
- **Depth model:** shares the depth maps Light Depth makes (Small, Base or Large), so the first run is already cached.
- **Phase 2 (engine):** local adjustments cannot blend toward a colour, so real fog needs a small `fog` stage (colour blend by
  depth coverage), the same shape as the sky stage. Phase 1 approximates it.
- **Licence note:** Base and Large depth models are CC-BY-NC. If the app ships commercially, Atmosphere and Light Depth must
  default to Small (Apache-2.0).
- **Tests:** coverage rises with distance; the nearest layer is untouched; settings survive save, reload and export.

## 4. Region enhancers

- **Idea:** package the landscape model's regions with curated adjustments. A section per detected region (Sky, Water,
  Vegetation, Mountains, Buildings) with Brightness, Contrast, Saturation, Warmth, Clarity and Dehaze, plus a master Amount
  and a few starting looks (Deepen sky, Lush greens, Clear water).
- **Masks:** regions come from the cached, refined mattes, so edges are good before the sliders are touched. Built at render time
  as masks (host effects); no mask appears in the Mask tab.
- **Dependencies:** shared ground work, and tool 2's refinement.
- **Tests:** a region absent from the photo is not offered; sliders at 0 add no masks; the mask budget is respected.

## 5. Match Look

- **Idea:** take the colour and tone of a reference photo and write them as ordinary recipe values, so nothing depends on
  the reference afterwards and it works everywhere the recipe does.
- **Method:** develop both photos the same neutral way; match the luminance distribution to a tone curve (up to 16 points) and
  the red, green and blue distributions to the three channel curves; fit the saturation per hue band (8 bands); tint shadows
  and highlights through the grading wheels. A strength slider blends everything toward no change. Options to match tone only
  or colour only.
- **Care:** a sunset reference on a forest should not be forced to match; keep the matching regularised and the strength
  default moderate.
- **Tests:** a warm reference moves a neutral photo warmer; results validate as a recipe; strength 0 is the identity.

## 6. AI Erase

### Model candidates and licences

Read on 2026-11-15 from the upstream files; this is not legal advice. Re-read at source before it goes in a legal file.

| Model | Code | Weights | Trained on | Notes |
|---|---|---|---|---|
| LaMa (big-lama) | Apache-2.0 | Apache-2.0 on the Carve ONNX port and the Hugging Face copy (a community port, to be re-checked against upstream) | Places365-Challenge | Best large-mask quality of the two. ONNX at a fixed 512 x 512, about 200 MB |
| MI-GAN | MIT | MIT (its own `LICENSE-WEIGHTS` file is the MIT text) | Places2 (512) and FFHQ (256, faces) | Small and fast, built for phones, an ONNX pipeline script is provided; weaker on large gaps |

- **The open question for both:** the dataset. Places2's annotations are CC BY, but "the copyright of all the images belongs to
  the image owners". The code and weights are licensed for commercial use; whether training on those images carries any risk
  is the same amber class as the OneFormer row in `docs/ai_model_licence_audit.md`, and needs the same lawyer review.
- **Not suitable (from memory, not re-checked today):** MAT and CoModGAN (non-commercial), FLUX Fill dev (non-commercial).
  Stable Diffusion inpainting is commercial under its OpenRAIL licence but heavy, generative and trained on web-scale data.
- **Recommendation:** LaMa as the quality model and MI-GAN as the light fallback, after the spike below and the licence review.

### Pipeline

1. **Select:** click the object with SAM 2.1 (already installed), or paint; grow the mask by 8-16 px.
2. **Fill:** cut a crop with generous context around the mask, scale it to the model's size, fill, scale back.
3. **Blend:** feather the edge, match colour and tone to the surroundings, and put the photo's grain back so the patch does not
   look clean.
4. **Store:** keep each fix as a small patch picture beside the recipe (under `~/.image-triage`) plus where it goes. The engine
   needs a stage that composites the patch early in the develop, so later exposure and colour edits still apply and full-size
   renders and exports include it (the sky stage is the template).
5. **Limit:** warn on very large fills; they are where these models make things up.

### Spike results (2026-11-15)

Both models run through onnxruntime on 12 MP and 9 MP developed previews (half-size RAW) of your lake and bear photos, on the
RTX 4080 SUPER and on CPU, each fill in a local crop with context and a feathered paste-back. Files: LaMa
`Carve/LaMa-ONNX` `lama_fp32.onnx` (208 MB, fixed 512 x 512 float input) and the authors' MI-GAN pipeline
`andraniksargsyan/migan` `migan_pipeline_v2.onnx` (28 MB, uint8, any size). Telea (OpenCV) is the classical baseline.

| Case | Hole | GPU: MI-GAN / LaMa | CPU: MI-GAN / LaMa | What the 100% crops show |
|---|---|---|---|---|
| 8 dust spots in sky | 0.1% | 0.60 / 0.97 s (all 8) | 2.7 / 8.9 s | All three are seamless. PSNR on the filled pixels (higher is better): LaMa 39.2, MI-GAN 38.3, Telea 37.5 |
| Wire across forest and water (6 tiles) | 0.1% | 0.47 / 0.77 s | 1.8 / 7.0 s | MI-GAN is seamless (25.9 dB). LaMa leaves a faint smudged band (23.5 dB, the same as Telea at 23.3) |
| Two spruces against sky | 0.3% | 0.12 / 0.16 s | 0.38 / 1.2 s | Both give a believable tree line and cloud; neither is perfect |
| Gull on water | 1.4% | 0.12 / 0.18 s | 0.37 / 1.4 s | Both give believable water; LaMa is a little blurrier at the shoreline |
| Bear (large object) | 7.6% | 0.40 / 0.46 s | 0.67 / 1.4 s | **MI-GAN fails badly**: it invents a bright blue and orange object. LaMa gives plausible, soft water and shore |

- **Memory:** about +540 MB of GPU memory with both models loaded.
- **LaMa is the robust choice.** On large holes it degrades gracefully (soft, plausible); MI-GAN can fail outright. MI-GAN is
  only better on thin things such as a wire, and Telea is already close to both on small spots. Recommendation: ship LaMa
  only; keep MI-GAN as an optional fast path for small and thin masks if CPU speed turns out to matter. LaMa on CPU is
  about 1.2-1.4 s per object, which is still interactive.
- **Mask quality decides the result.** A GrabCut mask that missed a gull's head and legs left ghosts in both models; with a
  complete mask grown by 18 px they vanished. Erase must use SAM masks plus a Grow slider, not a tight cutout.
- **Large holes must be one fill, not tiles.** Cutting a compact object into tiles (my first run) ruined the result. Tile
  only long thin masks.
- **Resolution is the open risk.** Both models work at 512 px internally, so a fill is softer than the 12 MP photo around it,
  and our test photos were half-size. A 45 MP RAW is not tested. Plan to restore detail (carry the surrounding grain and
  high-frequency texture over the fill) and test at full size before the engine work starts.
- **Integration note:** onnxruntime's CUDA provider only loads in this runtime after `import torch` has loaded the CUDA
  libraries; the managed runtime has torch, so the host should import it first.
- **Licences are unchanged:** both permissive; Places2 training-data question open (see above).

## Order

1. Shared ground work.
2. Auto tone and white balance, and Edge refinement phase 1 (independent, quick wins).
3. Atmosphere, then Region enhancers (both use the shared ground work).
4. Match Look (independent; can run in parallel with step 3).
5. AI Erase spike, then the build.
6. Engine phases (refine, fog, patch stage) together, so the worker and the hosted editor are rebuilt once.

## Decisions needed

1. Mask budget: raise PhotoCraft's limit, or combine zones.
2. Whether Atmosphere and Light Depth should default to the Small depth model for a commercial release.
3. LaMa or MI-GAN for Erase, after the spike and licence review.
4. Whether these tools sit in the Creative tab or a new Landscape tab for the region enhancers.
