# AI Erase

Remove tab -> **Erase (AI)**. Click an object (SAM selects it), adjust **Grow** so shadows and soft edges go with it, press
**Erase**. The result is one `patch` retouch stroke (one undo step); the photo is never changed and later global edits still apply.

## How it works
1. `RawErase.select` asks the RAW preview worker for a render of the photo as the retouch stage sees it (no masks, sky or Light
   Depth), then `prompt_masks.ensure_prompt_mask` finds the object at the click.
2. `erase.make_patch` grows the mask, crops the photo around it, sends the crop to LaMa (`inpaint_worker`, on the warm mask-engine
   host, model `Carve/LaMa-ONNX` `lama_fp32.onnx`, 512 px), matches the photo's grain, feathers the edge, converts sRGB to the
   renderer's working encoding and stores a small RGBA PNG under `~/.image-triage/AI/erase/<photo hash>/`.
3. The recipe gets `{"kind":"patch","patch":{"path":...,"region":[x0,y0,x1,y1]}}`. PhotoCraft (`retouch.rs`) resamples the picture
   into the region and blends it with its seamless clone. A missing patch file does nothing.

The patch keeps a ring of the photo's own pixels (alpha 0) around the fill: the seamless clone measures the tone mismatch there,
and without it the fill fades away.

## Install
The fill model is installed by hand for now: put `lama_fp32.onnx` (sha256 in `ai_model.py`) in
`~/.image-triage/AI/Editor/LaMa-ONNX/`. The worker and PhotoCraft must be builds that report `patch` support (the preview worker's
hello has `"patch":1`); an older worker refuses with "AI Erase needs the updated RAW preview worker."

## Known limits
- The fill is made from the preview-size render (about 1800-2064 px), so large fills are soft at 100% zoom.
- Click-only selection (no add/subtract yet); a hole over 25% of the photo is refused.
- Cold start of the mask host is slow on first use (about a minute); later fills take about a second.
- LaMa is Apache-2.0 but trained on Places365; see `ai_model_licence_audit.md` and `creative_tools_plan.md`.
- Patches are keyed by absolute photo path; moving the photo orphans them.
