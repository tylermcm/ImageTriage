# Handoff: popout editor Camera Raw parity

Branch `codex/ui-ux-polish`, all changes **uncommitted**. 13 files modified, 6 added,
+2507/-432. Nothing has been committed — the tree is yours to review, amend or discard.

## What was asked for

Bring the popout editor (`image_triage/ui/photo_editor_panel.py`, hosted by
`image_triage/preview.py` in studio layout) closer to Photoshop Camera Raw:

- **Into the main adjustments panel:** Texture, Grain, HSL/Color Mixer, Point Color,
  Color Grading, Defringe, Color Calibration.
- **As their own categorised tools:** a crop suite, removal tools (spot heal, heal,
  clone), red eye.
- **All of it on a vertical icon tool rail** (reference: the third mockup image — a
  TOOLS/PRESETS column, not to be copied literally).
- Explicitly out of scope: optics, lens blur *corrections*, AI distraction removal.

Decisions the user made when asked: the rail **replaces** the old Adjust/Masks/Presets
tab bar; **all** tools live on it, including Background and Lens Blur (pulled out of the
studio top toolbar); deliver everything in staged commits.

## THE BLOCKER — read this first

**The full test suite crashes partway through.** The Python process dies natively
(no traceback, no faulthandler dump — confirmed via process sampling that the process
is *gone*, not hung). Verified as a real regression from this work:

| Tree | Result |
|---|---|
| clean `HEAD` worktree | completes, 67.6s (61 failed, 937 passed — pre-existing) |
| this tree | dies at ~42% (~test 455) |

What is known about it, so you don't repeat the search:

- **Each half passes on its own.** Modules 1–65 (+532 passed) and 66–132 (543 passed)
  both complete. Only the whole run dies.
- The reported stall point is `tests/test_labeling_data_quality.py::LabelingDataQualityTests::test_default_near_identical_threshold_collapses_functional_duplicates`
  (module #65 alphabetically), but that module runs fine standalone and fine as part of
  1–65. It is almost certainly just where output stops.
- Because pytest imports **every** module at collection before running any test, the
  live hypothesis is that **a module later than #65 does something at import time** that
  makes an earlier test crash. The experiment to run next: collect all modules but
  `--deselect` files 66–132, so the late modules are still imported but not executed. If
  that crashes, bisect the deselected range. (I was mid-way through this when I stopped;
  the command is in the shell history and reproduced below.)

```bash
PYW="$LOCALAPPDATA/Microsoft/WindowsApps/pythonw3.13.exe"
ls tests/test_*.py | sort > /tmp/mods.txt
DESEL=$(sed -n '66,132p' /tmp/mods.txt | sed 's/^/--deselect /' | tr '\n' ' ')
"$PYW" run313.py <abs-log-path> pytest tests/ $DESEL -q --continue-on-collection-errors
```

Things already ruled out: GDI/USER handle exhaustion (offscreen Qt allocates none);
120 sequential `PhotoEditorPanel()` constructions in one process (survives); the
`_ColorWheel` QImage build (was doing a raw `scanLine()` write — **fixed anyway**, it
was genuinely unsafe, but it was not the cause).

One thing worth suspecting that I did not get to: the panel is now **921 QWidgets**
(all eight rail pages are built eagerly in `__init__`). Making pages lazy — build on
first visit — is the right design regardless, would cut that by most, and may well make
the problem go away. The obstacle is that a lot of code and several tests reach directly
for widgets on pages they never opened (`panel.mask_stack`, `panel.remove_spot_list`,
`panel.lensblur_amount_slider`, …), so lazy construction needs those call sites audited.

## How to run anything at all

`python` on this machine is **Anaconda 3.9** — below the project's `requires-python
>=3.11`, and `QApplication([])` dies there with `0xC0000409` and no traceback, which
looks exactly like a code crash but is not. `py -3.13` cannot launch (WindowsApps alias
restriction). The only working entry point is
`C:\Users\tylle\AppData\Local\Microsoft\WindowsApps\pythonw3.13.exe`, which is
windowless, so output must be redirected from inside the script:

```python
# run313.py
import os, sys, runpy, traceback
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
log = open(sys.argv[1], "w", encoding="utf-8", buffering=1)
sys.stdout = sys.stderr = log
sys.argv = sys.argv[2:]
code = 0
try:
    target = sys.argv[0]
    if target.endswith(".py"):
        runpy.run_path(target, run_name="__main__")
    else:
        runpy.run_module(target, run_name="__main__", alter_sys=True)
except SystemExit as exc:
    code = exc.code if isinstance(exc.code, int) else 1
except BaseException:
    traceback.print_exc(); code = 1
log.write(f"\n__EXIT__={code}\n"); log.close()
```

`__EXIT__=` present in the log means the process finished; absent means it died.

Pre-existing collection failures unrelated to this work: `test_tone_controls.py`
(`No module named 'cli_editor'`), `test_aiculler_topiq_onnx.py` (no `onnx`),
`test_dinov2_extractor.py` / `test_ranking_dino_fallback.py` (no `torch`).

## What is done and verified

These all pass. `tests/test_editor_tool_rail.py` (30 tests) and
`tests/test_editor_crop_retouch.py` (48 tests) are new and green, and the pre-existing
editor suites — `test_mask_panes` (52), `test_popout_editor_masks`, `test_scene_regions`,
`test_subject_masks`, `test_semantic_masks`, `test_curves`, `test_photo_presets`,
`test_adjustments_vectorized`, `test_editor_render`, `test_editor_copy`, `test_vignette`
— stay green when run as a group (146 + 147 passed in two groupings).

### 1. Tool rail (replaces the tab bar)

`_build_tool_rail()` replaces `_build_tab_bar()`. 46px, icon buttons grouped by category
with hairline dividers. The studio rail widened 336 → 336 + `RAIL_WIDTH`. `_ToolPopout`
and the two studio-toolbar buttons are **deleted**; Background and Lens Blur are now
rail pages hosting the existing `build_background_tool` / `build_lens_blur_tool` panels.

Page indices are class constants `PAGE_ADJUST=0 … PAGE_PRESETS=7`; every bare
`currentIndex() == 1` was converted. Icons are new `kind` branches on `_mask_glyph`.

### 2. Adjustment panels

New `EditRecipe` fields and numpy-vectorised apply functions in
`cli_editor/photo_terminal/adjustments.py`; new sections on the Adjust page.

| Section | Notes |
|---|---|
| Texture | `apply_texture` already existed but had no UI and no persistence |
| Grain | `apply_grain` extended with Size/Roughness |
| Color Mixer | 8 bands × Hue/Sat/Lum, colour-chip band picker. Saturation-only recipes stay byte-identical (old sidecars + `test_adjustments_vectorized` depend on it) |
| Point Color | `apply_point_color`, sampled entries — **no eyedropper yet**, see gaps |
| Color Grading | 4 zones + a new `_ColorWheel` widget, Blending/Balance |
| Defringe | `apply_defringe`, edge-weighted so a flat purple flower is untouched |
| Color Calibration | 3×3 primary matrix, row-normalised so neutrals stay neutral |

Two real bugs the tests caught, both fixed: colour grading was shifting exposure
(it now preserves luma exactly); defringe and point colour were round-tripping through
HSV and **requantising every pixel even where the effect was zero** — both now blend in
RGB and return the input unchanged when they do nothing.

New op types `adjust.calibration`, `adjust.color_grading`, `adjust.defringe`,
`adjust.point_color` were **inserted** into `RENDERER_ORDER` (never reordered — existing
sessions would fail `validate_session`'s ascending-order check). `validate_operation_params`
gained branches for all of them plus HSL and grain, which were previously unvalidated.

### 3. Crop and geometry

`image_triage/editor_geometry.py` — a frozen `ViewTransform` is now the single
description of how source pixels reach the screen. Before this, three places
independently assumed *scale only, no offset/rotation*: `MaskOverlay._scales`,
`build_group_strength`, and `_strength_for`. A crop breaks all three at once and
**silently** — masks just land wrong. The invariant: `ViewTransform.affine()` is the
exact matrix `EditRecipe.apply()` hands to `Image.transform`, so renderer and overlays
cannot disagree. With no crop it is the identity and every old path is unchanged.

Verified: crop corners map to frame corners; `frame_to_source(source_to_frame(p)) == p`
at several angles and all four flip combinations; an axis-aligned crop is byte-identical
to an array slice; flips are exact mirrors.

While the Crop tool is armed the renderer **bypasses** the crop and the pane shows the
whole straightened frame with the box drawn and the outside dimmed. Rendering
always-cropped is self-referential — `_present_display_image` resizes the label, the
overlay tracks the label, so each drag tick would resize the overlay under the cursor
driving it, and a handle could never be pulled back outward.

`bypass_crop` is *tool* state, so `_editor_recipe_version` does not cover it. The same
`source_key` expression was rebuilt at **five** sites in `preview.py`; they now all go
through one `_editor_state_key()`. Updating four of five would have produced either a
permanently frozen pane or a bypassed frame overwriting a cropped one.

### 4. Retouch (heal / clone / red eye)

Parametric spot list on the recipe (`retouch: Optional[list]` — deliberately *not*
`field(default_factory=list)`, because `merged()` reads `field.default`, which is
`MISSING` for a factory field, and preset merging would silently change).

The old kernels could not run per-tick: `heal_spot` median-filtered the **whole frame**
per spot and `remove_red_eye` was a pure-Python per-pixel double loop — the exact class
of bug this module already had to fix once. Rewritten as patch-local numpy kernels; the
public single-shot functions are now thin wrappers so there is one implementation and
`auto_remove_dust` stops paying 80 full-frame medians.

Measured on a 12MP frame: one heal 444ms → 46ms; 30 spots 896ms → 169ms; the kernel is
now flat across radius (1.5–5.3ms for r=10…80, was 2.3ms → 222ms). A retouch cache in
`CpuEditorRenderBackend`, keyed on `(base_key, frozen_spots)` with an append-only prefix
fast path, means a slider drag never re-runs the spot stack.

### 5. Pre-existing bug found and fixed

`write_edited_copy` (`editor_copy.py`) called `render()` with neither `background=` nor
`lensblur=` — **Background and Lens Blur have been silently missing from every saved
copy**. Now threaded through `EditorCopyService`, plus `view={"bypass_crop": False}` so
an export can never bypass the crop even with the tool armed.

Also fixed: `_apply_lens_blur` / `_apply_background` did `depth.resize(image.size)` on
assets produced at *source* size — under a crop that stretches a full-frame matte across
the cropped frame and slides the subject cut-out off the subject, with no error. Both now
warp through the same affine (`_align_asset`).

Also: `reset_recipe()` would have wiped the crop and every retouch spot from a button
labelled "Adjustments reset". It now preserves `STRUCTURAL_RECIPE_KEYS`; Crop has its own
`reset_crop()`.

## Known gaps — not done

1. **The suite crash above.** Nothing ships until that is understood.
2. **Point Color has no eyedropper.** The backend and persistence work; there is no
   on-canvas sampler, so entries can only be created programmatically.
3. **`_write_session` still reloads the recipe from disk** (`self._recipe =
   recipe_from_session(...)`), so any mask-pane write could discard unsaved crop/retouch.
   Flagged in design, **not fixed**. This is the highest-value remaining correctness item.
4. **Retouch tool arming is untested end-to-end.** The overlay's mouse handlers are
   exercised by calling `_apply_drag` directly, not through real Qt events.
5. **XMP export drops crop and retouch** (`CRS_EXPORT_MAP` only maps scalars). Crop could
   map to `crs:HasCrop`/`crs:Crop*`; retouch is not worth emulating.
6. **`session.py` has no direct test module** — a pre-existing gap, more visible now that
   the schema carries more.
7. The Curve section and the Presets page report `minimumSizeHint().width()` over the
   344px column budget, but only in an *unstyled* panel — the studio QSS pins those
   buttons to `min-width: 0`. Pre-existing, untouched, probably fine at runtime, worth a
   look with the stylesheet applied.
8. **No manual validation in a real culling session.** Everything here is tests and
   headless smoke checks; per the standing rule that is not sufficient sign-off.

## Files

Added: `image_triage/editor_geometry.py`, `image_triage/ui/canvas_overlay.py`
(`CanvasOverlay` + `OverlayStack`), `image_triage/ui/crop_overlay.py`,
`image_triage/ui/retouch_overlay.py`, `tests/test_editor_tool_rail.py`,
`tests/test_editor_crop_retouch.py`.

Modified: `cli_editor/photo_terminal/adjustments.py`, `cli_editor/photo_terminal/session.py`,
`image_triage/editor_copy.py`, `image_triage/editor_render.py`, `image_triage/preview.py`,
`image_triage/ui/mask_overlay.py`, `image_triage/ui/photo_editor_panel.py`, and six test
modules updated for the new page constants and the widened `render()` signature.

The full plan this was built from is at `~/.claude/plans/warm-whistling-sun.md`.
