# Calibration host guard — Codex, 2026-10-18

Host-only work, uncommitted. Claude's Calibration and upscale files were not edited.
No app launch, native build, commit or push. Existing executables remain untouched.

## Editor action needed

Once Calibration is supported in the editor's open and render pipelines, advertise
`"cameraRawCalibration": 1` in `app.handoff` (ui-egui control.rs).
The current source does not advertise it yet. `cameraRawFinishing: 1` alone is
deliberately insufficient: older Glow/Point Color builds cannot apply Calibration.
Do not advertise the new capability in a build that lacks Calibration processing.

## Host implementation

- photocraft_bridge accepts `calibShadowTint`, `calibHue`, `calibSat`, with matching
  finite -100..100 validation and exactly three values per primary array.
- Any active Calibration control overrides a stale recipe `identity: true`.
  Zeroed controls remain identity; legacy recipes without these fields still work.
- app_open refuses serialized Calibration fields unless the editor explicitly
  advertises support. Like the finishing guard, it guards fields even when neutral.
- full_render refuses unsupported RAW-recipe renders before submitting app.render.
  Recipes on disk are preserved. Project-render compatibility is outside this guard.

Changes: image_triage/photocraft_bridge.py, image_triage/full_render.py,
tests/test_photocraft_calibration_recipe.py, tests/test_full_render.py.

Validation (offscreen, pytest plugin autoload disabled): 139 passed, 2 skipped across
Calibration/finishing recipes, full_render, recipe_flow, prefetch and render_manifest.
Includes refused and accepted app.open, handshake versions, malformed data, identity,
all three controls, renderer rejection without a request, supported renderer forwarding,
and unchanged stored recipes. git diff --check passed.

Native origin and upstream fetched successfully; upstream/main advanced from
56dbc89a to92c4457b. Merge remains deferred under the user's latest handoff.
Crop & Expand has not been started. It still needs shared recipe/UI integration;
coordinate with Claude before touching the reserved Calibration files.
